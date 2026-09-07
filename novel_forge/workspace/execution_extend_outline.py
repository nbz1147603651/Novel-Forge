"""Workspace execution for extending an existing long-form outline."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novel_forge.core.infra.resource_locks import ResourceLockType, ResourceName
from novel_forge.core.schemas.bible import CharacterBible, StoryBible
from novel_forge.core.schemas.outline import NarrativeBlueprint, StoryOutline
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.persistence.authoring_store import AuthoringStore
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.planning_revision import PlanningRevision
from novel_forge.persistence.project_staleness import (
    RevisionScope,
    UpstreamArtifactKind,
    record_upstream_artifact_revision,
)
from novel_forge.pipeline.long.services.blueprint.blueprint_payloads import (
    pre_normalize_blueprint_payload,
)
from novel_forge.pipeline.long.services.chapter_position import PREVIOUS_FINAL_MARKER
from novel_forge.pipeline.long.services.context.source_artifacts import (
    build_and_persist_chapter_source_slice,
)
from novel_forge.pipeline.long.services.init.init_cache import _build_base_ctx
from novel_forge.pipeline.long.services.init.init_context import build_init_context
from novel_forge.pipeline.long.services.init.init_outline_batch import (
    _batched_generate_outline,
    _outline_session_matches_reveal_guard,
    outline_reveal_guard_input_hashes,
)
from novel_forge.workspace.authoring_control import planning_authority
from novel_forge.workspace.contracts import ExtendOutlineRequest
from novel_forge.workspace.execution_outline_polish import (
    _CandidateRuntime,
    propose_planning_revision,
)
from novel_forge.workspace.execution_result import ExecutionResult, StepCallback
from novel_forge.workspace.helpers.execution_runners import (
    _project_lock,
    execute_sync_chapter_contracts,
)
from novel_forge.workspace.planning_range_service import (
    sync_validate_refresh_planning_range,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


async def execute_extend_outline(
    runtime: Any,
    request: ExtendOutlineRequest,
    *,
    on_step_progress: StepCallback = None,
) -> ExecutionResult[dict[str, Any]]:
    project_id = request.project_id.strip()
    if not project_id:
        raise ValueError("project_id is required")
    root = runtime.storage.existing_project_dir(project_id)
    if AuthoringStore(root).policy() is None:
        return await _execute_extend_outline(runtime, request, on_step_progress=on_step_progress)
    if not request.sync_contracts:
        raise ValueError("共创的补齐或延长候选必须完成严格契约同步")
    async with _project_lock(runtime, project_id):
        outline = StoryOutline.model_validate(runtime.storage.load_json(root / "outline.json"))
        total = outline.total_chapters
        target = (
            request.target_total
            if request.target_total is not None
            else _target_total(request, total)
        )
        if target < total:
            raise ValueError("延长或补齐不得缩减全书目标")
        hard = int(outline.hard_through_chapter or total)
        affected = list(range(hard + 1, target + 1))
        if target > total and request.decommission_old_ending:
            affected = sorted({total, *affected})
        revision = (
            PlanningRevision(root, project_id, purpose="extension") if target > total else None
        )
    with planning_authority(runtime, project_id, affected or [total]):
        if revision is None:
            from novel_forge.workspace.execution_planning_horizon import advance_planning_horizon

            advance = await advance_planning_horizon(
                runtime,
                project_id=project_id,
                target_chapter=target,
                explicit=True,
                on_step_progress=on_step_progress,
            )
            proposal_id = ""
            if advance is not None and not advance.published:
                prepared = PlanningRevision.load(root, project_id, advance.revision_id)
                proposal_id = await propose_planning_revision(runtime, project_id, prepared, target)
            return ExecutionResult(
                project_id=project_id,
                result={
                    "project_id": project_id,
                    "operation": "complete_planning",
                    "status": "candidate" if proposal_id else "completed",
                    "previous_total": total,
                    "target_total": total,
                    "added_chapters": 0,
                    "proposal_id": proposal_id,
                    "message": "补齐候选待作者批准" if proposal_id else "当前目标规划已齐备",
                },
            )

        def candidate_progress(step: str, payload: Any) -> None:
            if step != "extend_outline_done" and on_step_progress is not None:
                on_step_progress(step, payload)

        result = await _execute_extend_outline(
            _CandidateRuntime(runtime, revision.storage),
            request,
            on_step_progress=candidate_progress,
        )
        if (
            result.result["status"] != "completed"
            or result.result["source_slices_refreshed"] != target - hard
        ):
            raise RuntimeError("延长候选未通过完整契约和来源同步；原规划未改变")
        revision.mark_validated(affected)
        proposal_id = await propose_planning_revision(runtime, project_id, revision, target)
        return ExecutionResult(
            project_id=project_id,
            result={
                **result.result,
                "status": "candidate",
                "proposal_id": proposal_id,
                "revision_id": revision.revision_id,
                "message": "延长全书候选待专项批准；正式目标未改变",
            },
        )


async def _execute_extend_outline(
    runtime: Any, request: ExtendOutlineRequest, *, on_step_progress: StepCallback = None
) -> ExecutionResult[dict[str, Any]]:
    """Append chapters to the end of an existing long-form project."""

    project_id = request.project_id.strip()
    if not project_id:
        raise ValueError("project_id is required")

    if request.target_total is not None:
        async with _project_lock(runtime, project_id, ResourceName.CANON):
            current_layout = ProjectLayout(runtime.storage.existing_project_dir(project_id))
            current = StoryOutline.model_validate(
                runtime.storage.load_json(current_layout.outline_path)
            )
        if request.target_total == current.total_chapters:
            if not request.sync_contracts:
                raise ValueError("补齐全书规划必须同步章节契约。")
            from novel_forge.workspace.execution_planning_horizon import advance_planning_horizon

            advance = await advance_planning_horizon(
                runtime,
                project_id=project_id,
                target_chapter=current.total_chapters,
                explicit=True,
                on_step_progress=on_step_progress,
            )
            result: dict[str, Any] = {
                "project_id": project_id,
                "status": "candidate" if advance and not advance.published else "completed",
                "operation": "complete_planning",
                "previous_total": current.total_chapters,
                "target_total": current.total_chapters,
                "added_chapters": 0,
                "new_chapters": list(advance.generated_chapters) if advance else [],
                "affected_chapters": list(advance.affected_chapters) if advance else [],
            }
            if on_step_progress is not None:
                on_step_progress("extend_outline_done", result)
            return ExecutionResult(project_id=project_id, result=result)

    warnings: list[str] = []
    contracts_result: dict[str, Any] | None = None
    source_slices_refreshed = 0

    async with _project_lock(
        runtime,
        project_id,
        ResourceName.CANON,
        lock_type=ResourceLockType.EXCLUSIVE,
    ):
        storage = runtime.storage
        layout = ProjectLayout(storage.existing_project_dir(project_id))
        _ensure_required_artifacts(storage, layout)

        spec = StorySpec.model_validate(storage.load_json(layout.spec_path))
        story_bible = StoryBible.model_validate(storage.load_json(layout.bible_path))
        character_bible = CharacterBible.model_validate(storage.load_json(layout.characters_path))
        style_profile = storage.load_json(layout.style_profile_path)
        outline = StoryOutline.model_validate(storage.load_json(layout.outline_path))
        blueprint_raw = storage.load_json(layout.blueprint_path)
        _chapter_contracts = storage.load_json(layout.plans_dir / "chapter_contracts.json")
        _narrative_contract = storage.load_json(layout.narrative_contract_path)
        editorial_contract = storage.load_json(layout.editorial_contract_path)

        previous_total = int(outline.total_chapters)
        previous_hard = int(outline.hard_through_chapter or previous_total)
        target_total = _target_total(request, previous_total)
        added_chapters = target_total - previous_total
        new_chapters = list(range(previous_total + 1, target_total + 1))
        planning_chapters = list(range(previous_hard + 1, target_total + 1))
        old_final_chapter = previous_total
        words_per_chapter = max(500, round(int(spec.length_target or 0) / previous_total))
        target_length = target_total * words_per_chapter
        if target_length > 1_000_000:
            warnings.append(
                "spec.length_target capped at 1000000 by StorySpec schema; "
                f"computed target was {target_length}"
            )
            target_length = 1_000_000

        if on_step_progress:
            on_step_progress(
                "extend_outline_start",
                {
                    "project_id": project_id,
                    "previous_total": previous_total,
                    "target_total": target_total,
                    "new_chapters": new_chapters,
                },
            )

        updated_spec = spec.model_copy(update={"length_target": target_length})
        updated_story_bible = _append_story_bible_note(
            story_bible,
            previous_total=previous_total,
            target_total=target_total,
            reason=request.reason,
        )
        prepared_outline = _prepare_existing_outline(
            outline,
            old_final_chapter=old_final_chapter,
            target_total=target_total,
            decommission_old_ending=request.decommission_old_ending,
        )
        normalized_blueprint_raw = pre_normalize_blueprint_payload(
            blueprint_raw,
            total_chapters=target_total,
        )
        blueprint = NarrativeBlueprint.model_validate(normalized_blueprint_raw)

        previous_outline_hash = _file_hash(layout.outline_path)
        previous_blueprint_hash = _file_hash(layout.blueprint_path)

        _ensure_reveal_guard_session(storage, layout, editorial_contract)

        runner = runtime.chapter_runner(
            on_step_progress=on_step_progress,
            warn_missing_memory_context=False,
        )
        ctx = replace(
            build_init_context(runner, project_id),
            storage=storage,
            layout=layout,
            memory_context=None,
        )
        use_volume_mode = bool(blueprint.volume_mode or prepared_outline.volume_mode)
        effective_chapters_per_volume = _effective_chapters_per_volume(
            prepared_outline,
            getattr(ctx, "config", None),
        )
        expected_total_words = target_total * words_per_chapter
        outline_ctx = {
            key: value
            for key, value in _build_base_ctx(
                updated_spec,
                expected_total_words,
                premise=updated_spec.theme,
            ).items()
            if key != "extra_instructions"
        }
        outline_ctx.update(
            {
                "spec": updated_spec,
                "story_bible": updated_story_bible,
                "character_bible": character_bible.model_dump(mode="json"),
                "total_chapters": target_total,
                "words_per_chapter": words_per_chapter,
                "use_volume_mode": use_volume_mode,
                "blueprint_element_selection": (
                    blueprint.element_selection.model_dump(mode="json")
                    if blueprint.element_selection is not None
                    else {}
                ),
                "style_profile": style_profile,
                "character_system": {},
                "relationship_overview": "",
                "entity_graph": None,
                "creative_director_packet": None,
            }
        )

        backup_dir = _backup_extend_artifacts(layout, storage)
        extended_outline = await _batched_generate_outline(
            ctx,
            existing_outline=prepared_outline,
            outline_ctx=outline_ctx,
            blueprint=blueprint,
            total_chapters=target_total,
            words_per_chapter=words_per_chapter,
            use_volume_mode=use_volume_mode,
            effective_chapters_per_volume=effective_chapters_per_volume,
            character_bible=character_bible,
            entity_registry=_load_optional_json(
                storage,
                layout.narrative_state_dir / "entity_registry.json",
            ),
            editorial_contract=editorial_contract,
            target_start_chapter=previous_hard + 1,
            target_end_chapter=target_total,
            design_through_chapter=target_total,
            preserve_committed_chapters=True,
        )
        extended_outline = extended_outline.model_copy(
            update={
                "total_chapters": target_total,
                "hard_through_chapter": target_total,
                "planned_through_chapter": target_total,
            }
        )

        generated_numbers = {int(ch.chapter_number) for ch in extended_outline.chapters}
        if not set(range(1, target_total + 1)).issubset(generated_numbers):
            raise RuntimeError("outline extension did not produce all requested new chapters")

        extended_outline = _preserve_original_chapters(
            extended_outline,
            prepared_outline.model_copy(
                update={
                    "chapters": [
                        ch for ch in prepared_outline.chapters if ch.chapter_number <= previous_hard
                    ],
                }
            ),
        )

        storage.save_json(layout.spec_path, updated_spec.model_dump(mode="json"))
        storage.save_json(layout.bible_path, updated_story_bible.model_dump(mode="json"))
        storage.save_json(layout.blueprint_path, blueprint.model_dump(mode="json"))
        storage.save_json(layout.outline_path, extended_outline.model_dump(mode="json"))

        record_upstream_artifact_revision(
            storage,
            layout,
            artifact_kind=UpstreamArtifactKind.OUTLINE,
            previous_hash=previous_outline_hash,
            scope=RevisionScope.FORWARD_ONLY,
            from_chapter=min(planning_chapters[0], old_final_chapter)
            if request.decommission_old_ending
            else planning_chapters[0],
            reason=request.reason,
        )
        current_blueprint_hash = _file_hash(layout.blueprint_path)
        if current_blueprint_hash != previous_blueprint_hash:
            record_upstream_artifact_revision(
                storage,
                layout,
                artifact_kind=UpstreamArtifactKind.NARRATIVE_BLUEPRINT,
                previous_hash=previous_blueprint_hash,
                scope=RevisionScope.FORWARD_ONLY,
                from_chapter=old_final_chapter
                if request.decommission_old_ending
                else old_final_chapter + 1,
                reason=f"{request.reason}:blueprint_normalized",
            )

        if request.decommission_old_ending:
            _delete_chapter_source_slice(layout, old_final_chapter)

    if request.sync_contracts:
        affected = list(planning_chapters)
        if request.decommission_old_ending:
            affected = sorted({old_final_chapter, *affected})
        range_finalization = await sync_validate_refresh_planning_range(
            runtime,
            project_id=project_id,
            chapter_numbers=affected,
            source_slice_chapter_numbers=planning_chapters,
            cascade_downstream=True,
            require_completed=False,
            sync_executor=execute_sync_chapter_contracts,
            slice_refresher=_refresh_new_source_slices,
            on_step_progress=on_step_progress,
        )
        contracts_result = range_finalization.contracts_result
        source_slices_refreshed = range_finalization.source_slices_refreshed
        warnings.extend(range_finalization.warnings)
    else:
        warnings.append("chapter contract sync skipped; new chapter source slices not generated")

    status = "completed"
    if request.sync_contracts and (contracts_result or {}).get("status") != "completed":
        status = "partial"
    elif not request.sync_contracts:
        status = "outline_extended"

    result = {
        "project_id": project_id,
        "status": status,
        "previous_total": previous_total,
        "target_total": target_total,
        "added_chapters": added_chapters,
        "old_final_chapter": old_final_chapter,
        "new_chapters": new_chapters,
        "contracts_result": contracts_result,
        "source_slices_refreshed": source_slices_refreshed,
        "backup_dir": str(backup_dir) if backup_dir else None,
        "warnings": warnings,
    }
    if on_step_progress:
        on_step_progress("extend_outline_done", result)
    return ExecutionResult(project_id=project_id, result=result)


def _ensure_required_artifacts(storage: Any, layout: ProjectLayout) -> None:
    required = {
        "spec.json": layout.spec_path,
        "story_bible.json": layout.bible_path,
        "character_bible.json": layout.characters_path,
        "style_profile.json": layout.style_profile_path,
        "outline.json": layout.outline_path,
        "plans/narrative_blueprint.json": layout.blueprint_path,
        "plans/chapter_contracts.json": layout.plans_dir / "chapter_contracts.json",
        "plans/narrative_contract.json": layout.narrative_contract_path,
        "plans/editorial_contract.json": layout.editorial_contract_path,
    }
    missing = [name for name, path in required.items() if not storage.exists(path)]
    if missing:
        raise FileNotFoundError("Project is missing required artifacts: " + ", ".join(missing))


def _target_total(request: ExtendOutlineRequest, previous_total: int) -> int:
    if request.target_total is not None:
        target = int(request.target_total)
        if target <= previous_total:
            raise ValueError("target_total must be greater than current outline.total_chapters")
        return target
    target = previous_total + int(request.additional_chapters or 0)
    if target > 10000:
        raise ValueError("target_total must be <= 10000")
    return target


def _append_story_bible_note(
    story_bible: StoryBible,
    *,
    previous_total: int,
    target_total: int,
    reason: str,
) -> StoryBible:
    marker = (
        f"[extend_outline] previous_total={previous_total}, target_total={target_total}, "
        f"reason={reason}, at={_utc_now_iso()}"
    )
    notes = str(story_bible.notes or "").strip()
    if f"previous_total={previous_total}, target_total={target_total}" in notes:
        return story_bible
    combined = f"{notes}\n{marker}".strip() if notes else marker
    return story_bible.model_copy(update={"notes": combined})


def _prepare_existing_outline(
    outline: StoryOutline,
    *,
    old_final_chapter: int,
    target_total: int,
    decommission_old_ending: bool,
) -> StoryOutline:
    chapters = []
    for chapter in outline.chapters:
        if int(chapter.chapter_number) != old_final_chapter or not decommission_old_ending:
            chapters.append(chapter)
            continue
        notes = str(chapter.notes or "").strip()
        marker = (
            f"{PREVIOUS_FINAL_MARKER} previous_total={old_final_chapter}, "
            f"target_total={target_total}, at={_utc_now_iso()}; "
            "已延长：此章不再作为全书终章，应转为过渡/再开启章。"
        )
        if PREVIOUS_FINAL_MARKER not in notes:
            notes = f"{notes}\n{marker}".strip() if notes else marker
        chapters.append(chapter.model_copy(update={"notes": notes}))
    return outline.model_copy(update={"total_chapters": target_total, "chapters": chapters})


def _effective_chapters_per_volume(outline: StoryOutline, config: Any | None) -> int:
    lengths = [
        int(volume.end_chapter) - int(volume.start_chapter) + 1
        for volume in outline.volumes
        if int(volume.end_chapter) >= int(volume.start_chapter)
    ]
    if lengths:
        return max(1, round(sum(lengths) / len(lengths)))
    return max(1, int(getattr(config, "default_chapters_per_volume", 20) or 20))


def _load_optional_json(storage: Any, path: Path) -> dict[str, Any] | None:
    if not storage.exists(path):
        return None
    try:
        data = storage.load_json(path)
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def _delete_chapter_source_slice(layout: ProjectLayout, chapter_number: int) -> None:
    path = layout.chapter_source_slice_path(chapter_number)
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _refresh_new_source_slices(
    storage: Any,
    layout: ProjectLayout,
    *,
    project_id: str,
    chapter_numbers: list[int],
) -> tuple[int, list[str]]:
    refreshed = 0
    warnings: list[str] = []
    for number in chapter_numbers:
        try:
            build_and_persist_chapter_source_slice(
                storage=storage,
                layout=layout,
                project_id=project_id,
                chapter_number=number,
            )
            refreshed += 1
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"source slice refresh failed for chapter {number}: {exc}")
    return refreshed, warnings


def _ensure_reveal_guard_session(
    storage: Any,
    layout: ProjectLayout,
    editorial_contract: Any,
) -> None:
    """Abort extension if reveal-guard session would discard the existing outline.

    ``_batched_generate_outline`` silently sets ``existing_outline = None`` when
    the editorial revelation-ladder fingerprints no longer match the persisted
    outline session.  For an extension that would regenerate the whole book
    outline from scratch and overwrite the on-disk artifact, destroying the
    mapping to already-written prose.  Detect the mismatch up-front and abort
    before any artifact is touched.
    """
    reveal_hashes = outline_reveal_guard_input_hashes(editorial_contract)
    if not reveal_hashes or not storage.exists(layout.outline_session_path):
        # Successful initialization intentionally removes this temporary file.
        # The batch caller preserves the authoritative committed outline.
        return
    if _outline_session_matches_reveal_guard(storage, layout, reveal_hashes):
        return
    raise RuntimeError(
        "extend_outline aborted: editorial reveal-guard session mismatch. "
        "outline_session.json was generated against a different revelation_ladder; "
        "continuing would silently discard the existing outline. "
        "Re-run the init editorial step or explicitly clear "
        "states/outline_session.json before extending."
    )


def _preserve_original_chapters(
    extended_outline: StoryOutline,
    reference_outline: StoryOutline,
) -> StoryOutline:
    """Keep original chapters untouched by outline-batch normalization.

    ``_batched_generate_outline`` re-runs ``_normalize_outline_character_fields``
    over every accumulated (pre-existing) chapter using a design matrix rebuilt
    for the new total chapter count.  That can quietly rewrite cast_plan /
    emotional_plan / involved_character_ids of already-written chapters and
    desync the outline from persisted ``chapter_contracts.json``.  Restore the
    reference (prepared) chapters verbatim so only newly generated chapters
    reflect batch normalization.
    """
    reference_by_number = {int(ch.chapter_number): ch for ch in reference_outline.chapters}
    if not reference_by_number:
        return extended_outline
    preserved: list[Any] = []
    changed = False
    for ch in extended_outline.chapters:
        number = int(ch.chapter_number)
        reference = reference_by_number.get(number)
        if reference is not None and reference != ch:
            preserved.append(reference)
            changed = True
        else:
            preserved.append(ch)
    if not changed:
        return extended_outline
    return extended_outline.model_copy(update={"chapters": preserved})


def _backup_extend_artifacts(layout: ProjectLayout, storage: Any) -> Path | None:
    """Snapshot the four artifacts that extension overwrites, for undo."""
    stamp = _utc_now_iso().replace(":", "").replace("-", "")
    backup_dir = layout.root / "backups" / f"extend_outline_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    sources = {
        "spec.json": layout.spec_path,
        "story_bible.json": layout.bible_path,
        "outline.json": layout.outline_path,
        "narrative_blueprint.json": layout.blueprint_path,
    }
    backed = False
    for name, src in sources.items():
        try:
            if storage.exists(src):
                shutil.copy2(src, backup_dir / name)
                backed = True
        except OSError:
            pass
    return backup_dir if backed else None


__all__ = ["execute_extend_outline"]
