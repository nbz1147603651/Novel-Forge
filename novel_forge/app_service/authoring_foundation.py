"""Versioned foundation proposals, using existing domain projections and publication.

No model calls, shadow canon or arbitrary file commands. JSON is prepared in an
isolated PlanningRevision; the live SQLite mirror is merged, never replaced by
a stale snapshot. A durable barrier protects a committed but unfinished merge.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel

from novel_forge.core.authoring import AuthoringProposalRequest, AuthoringProposalView
from novel_forge.core.schemas import CharacterBible, StoryBible, StorySpec
from novel_forge.core.schemas.init_v2 import CharacterSystem
from novel_forge.core.schemas.outline import NarrativeBlueprint
from novel_forge.persistence.authoring_proposals import ProposalStore
from novel_forge.persistence.authoring_store import (
    AuthoringDeniedError,
    AuthoringStore,
    story_input_version,
)
from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.persistence.foundation_guard import SYNC_PATH, foundation_candidate
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.planning_revision import PlanningRevision, planning_publication_receipt
from novel_forge.persistence.project_staleness import (
    RevisionScope,
    UpstreamArtifactKind,
    record_upstream_artifact_revision,
)
from novel_forge.workspace.authoring_proposals import create_proposal, create_proposal_under_lock
from novel_forge.workspace.helpers.execution_runners import _project_lock

_ARTIFACTS: dict[str, tuple[str, type[BaseModel], UpstreamArtifactKind]] = {
    "spec": ("spec.json", StorySpec, UpstreamArtifactKind.SPEC),
    "world": ("story_bible.json", StoryBible, UpstreamArtifactKind.STORY_BIBLE),
    "characters": ("character_bible.json", CharacterBible, UpstreamArtifactKind.CHARACTER_BIBLE),
    "blueprint": (
        "plans/narrative_blueprint.json",
        NarrativeBlueprint,
        UpstreamArtifactKind.NARRATIVE_BLUEPRINT,
    ),
}


async def create_authoring_proposal(
    runtime: Any, project_id: str, request: AuthoringProposalRequest, *, proposal_id: str = ""
) -> AuthoringProposalView:
    if request.command != "revise_foundation":
        return await create_proposal(runtime, project_id, request, proposal_id=proposal_id)
    root = runtime.storage.existing_project_dir(project_id)
    AuthoringStore(root).require_enabled()
    if proposal_id and ProposalStore(root).path(proposal_id).is_file():
        return AuthoringProposalView.model_validate(ProposalStore(root).read(proposal_id)["view"])
    async with _project_lock(runtime, project_id):
        policy = AuthoringStore(root).policy()
        if policy is None:
            raise AuthoringDeniedError("请先选择作者授权；不会自动升级旧项目")
        if not policy.start_chapter <= request.chapter_number <= policy.end_chapter:
            raise AuthoringDeniedError("提案超出本次章段")
        version = story_input_version(root)
        if request.expected_input_version and request.expected_input_version != version:
            raise AuthoringDeniedError("设定输入已变化，请重新核对")
        from novel_forge.persistence.foundation_guard import assert_foundation_sync_complete

        assert_foundation_sync_complete(root)
        revision = PlanningRevision(root, project_id, purpose="foundation")
        _prepare(revision, request)
    # No project lock is held while entering the common proposal facade.
    return await create_proposal(
        runtime,
        project_id,
        request.model_copy(
            update={"revision_id": revision.revision_id, "expected_input_version": version}
        ),
        proposal_id=proposal_id,
    )


def _prepare(revision: PlanningRevision, request: AuthoringProposalRequest) -> None:
    from novel_forge.app_service.character_artifacts import (
        _load_relationship_matrix,
        _refresh_source_artifacts,
        write_character_bible,
    )
    from novel_forge.pipeline.long.services.init.init_v2 import build_character_system

    name, model, _ = _ARTIFACTS[request.foundation_artifact]
    original = json.loads(revision.before[name])
    supplied = json.loads(request.candidate)
    if not isinstance(supplied, dict):
        raise ValueError("设定候选必须是完整的领域对象")
    payload = model.model_validate(supplied).model_dump(mode="json")
    normalized_original = model.model_validate(original).model_dump(mode="json")
    if request.foundation_artifact in {"spec", "world", "blueprint"}:
        for key in ("length_target", "total_chapters", "total_words", "project_id"):
            if payload.get(key) != normalized_original.get(key):
                raise ValueError("总目标与项目身份不得通过设定修订改变；请使用延长全书专项提案")
    if normalized_original == payload:
        raise ValueError("候选未改变设定")
    layout = ProjectLayout(revision.project)
    with foundation_candidate(revision.project):
        if request.foundation_artifact == "characters":
            write_character_bible(revision.project, payload)
        else:
            atomic_write_json(revision.project / name, payload)
            if request.foundation_artifact == "spec":
                _record_author_inputs(revision, normalized_original, payload)
            bible = CharacterBible.model_validate(
                revision.storage.load_json(layout.characters_path)
            )
            system = build_character_system(
                bible, relationship_matrix=_load_relationship_matrix(revision.project)
            )
            graph_path = layout.narrative_state_dir / "entity_graph.json"
            graph = revision.storage.load_json(graph_path) if graph_path.exists() else {}
            warnings: list[str] = []
            _refresh_source_artifacts(
                storage=revision.storage,
                layout=layout,
                character_bible=bible,
                character_system=system,
                entity_graph=graph,
                warnings=warnings,
            )
            if warnings:
                raise ValueError("设定候选来源同步失败：" + "；".join(warnings))
    _finish_preparation(revision, request.foundation_artifact)


def _finish_preparation(revision: PlanningRevision, artifact: str) -> None:
    name, _, kind = _ARTIFACTS[artifact]
    layout = ProjectLayout(revision.project)
    finalized = sorted(int(Path(path).stem.split("_")[-1]) for path in revision.chapter_hashes)
    scope = RevisionScope.WHOLE_BOOK
    if artifact == "characters":
        from novel_forge.app_service.character_artifacts import _character_revision_scope

        system_path = "states/init_v2/character_system.json"
        previous_system = revision.before.get(system_path)
        scope = _character_revision_scope(
            CharacterBible.model_validate_json(revision.before[name]),
            CharacterBible.model_validate(revision.storage.load_json(layout.characters_path)),
            previous_character_system=CharacterSystem.model_validate_json(previous_system)
            if previous_system
            else None,
            current_character_system=CharacterSystem.model_validate(
                revision.storage.load_json(revision.project / system_path)
            ),
        )
    # Voice/profile additions retain the existing forward-only semantics. The
    # isolated candidate has no canon DB, so never derive its watermark as zero.
    start = max(finalized, default=0) + 1 if scope == RevisionScope.FORWARD_ONLY else None
    # Record, don't delete old reports/canon/chapters: existing staleness gates
    # require revalidation. The immutable revision preserves all previous JSON.
    record_upstream_artifact_revision(
        revision.storage,
        layout,
        artifact_kind=kind,
        previous_hash=hashlib.sha256(revision.before[name].encode()).hexdigest(),
        scope=scope,
        from_chapter=start,
        reason=f"authoring_foundation:{revision.revision_id}",
        apply_invalidation=False,
        finalized_numbers=finalized,
    )
    atomic_write_json(
        layout.root / SYNC_PATH,
        {
            "revision_id": revision.revision_id,
            "artifact": artifact,
            "status": "pending",
        },
    )
    outline = revision.storage.load_json(layout.outline_path)
    proof = revision.mark_validated(list(range(start or 1, int(outline["total_chapters"]) + 1)))
    proof["foundation_artifact"] = artifact
    proof["revision_scope"] = scope.value
    atomic_write_json(revision.directory / "validated.json", proof)


def propose_character_mutation(
    storage: Any, root: Path, write: Callable[[Path], Any]
) -> tuple[AuthoringProposalView, Any]:
    """Old editors keep their typed patch; only the isolated target changes.

    Caller holds the project lock. No locks or approval are inferred from a UI
    save click; this creates an unapproved proposal, even in authorized_auto.
    """
    AuthoringStore(root).require_enabled()
    policy = AuthoringStore(root).policy()
    if policy is None:
        raise ValueError("未配置作者授权")
    from novel_forge.persistence.foundation_guard import assert_foundation_sync_complete

    assert_foundation_sync_complete(root)
    version = story_input_version(root)
    revision = PlanningRevision(root, root.name, purpose="foundation")
    with foundation_candidate(revision.project):
        result = write(revision.project)
    _finish_preparation(revision, "characters")
    view = create_proposal_under_lock(
        SimpleNamespace(storage=storage),
        root.name,
        AuthoringProposalRequest(
            command="revise_foundation",
            chapter_number=policy.start_chapter,
            title="人物与关系专项修改",
            foundation_artifact="characters",
            revision_id=revision.revision_id,
            expected_input_version=version,
            evidence=["来自作者在人物/关系编辑器中的修改"],
        ),
    )
    return view, result


def propose_foundation_payload(
    storage: Any, root: Path, artifact: Any, payload: dict[str, Any]
) -> AuthoringProposalView:
    """Typed legacy editor bridge. Caller already holds the project lock and CAS."""
    AuthoringStore(root).require_enabled()
    policy = AuthoringStore(root).policy()
    if policy is None:
        raise AuthoringDeniedError("未配置作者授权")
    from novel_forge.persistence.foundation_guard import assert_foundation_sync_complete

    assert_foundation_sync_complete(root)
    version = story_input_version(root)
    request = AuthoringProposalRequest(
        command="revise_foundation",
        chapter_number=policy.start_chapter,
        foundation_artifact=artifact,
        candidate=json.dumps(payload, ensure_ascii=False),
        expected_input_version=version,
        title="叙事蓝图专项修改",
        evidence=["来自作者在支线编辑器中的修改"],
    )
    revision = PlanningRevision(root, root.name, purpose="foundation")
    _prepare(revision, request)
    return create_proposal_under_lock(
        SimpleNamespace(storage=storage),
        root.name,
        request.model_copy(update={"revision_id": revision.revision_id}),
    )


def _record_author_inputs(
    revision: PlanningRevision, before: dict[str, Any], after: dict[str, Any]
) -> None:
    # Original init request remains evidence. Only explicitly approved *changed*
    # fields override it; unchanged model-enriched fields remain accepted facts.
    from novel_forge.core.user_intent import _SPEC_FIELD_MAP

    path = revision.project / "states/init_request_meta.json"
    meta = revision.storage.load_json(path) if path.exists() else {}
    overrides = dict(meta.get("author_input_overrides") or {})
    for intent, field in _SPEC_FIELD_MAP.items():
        if before.get(field) != after.get(field):
            overrides[intent] = {
                "value": after.get(field, ""),
                "source": "author_approved_proposal",
                "scope": "book",
                "revision_id": revision.revision_id,
            }
    meta["author_input_overrides"] = overrides
    atomic_write_json(path, meta)


def complete_foundation_commit(runtime: Any, project_id: str, revision: PlanningRevision) -> None:
    """Finish an already legal commit under project + authority locks, even after pause.

    Only the same published bytes may be mirrored. Later author edits are never
    overwritten by recovery, and a completed receipt never replays the merge.
    """
    receipt = planning_publication_receipt(revision.root, revision.revision_id)
    if receipt is None:
        raise ValueError("设定尚未正式提交")
    done = revision.directory / "foundation_completed.json"
    if done.is_file():
        marker_path = revision.root / SYNC_PATH
        marker = runtime.storage.load_json(marker_path)
        if (
            marker.get("revision_id") == revision.revision_id
            and marker.get("status") != "completed"
        ):
            atomic_write_json(marker_path, {**marker, "status": "completed"})
        return
    layout = ProjectLayout(revision.root)
    marker = runtime.storage.load_json(layout.root / SYNC_PATH)
    if marker.get("revision_id") != revision.revision_id:
        raise ValueError("设定恢复标记不匹配，未覆盖新修改")
    for name, text in revision.changes().items():
        path = revision.root / name
        if (path.read_text(encoding="utf-8") if path.exists() else None) != text:
            raise ValueError(f"已提交设定的输入已变化，未重放旧投影：{name}")
    if marker["artifact"] == "characters":
        from novel_forge.app_service.character_artifacts import (
            _sync_character_artifacts_to_story_kernel,
        )

        _sync_character_artifacts_to_story_kernel(
            layout=layout,
            character_bible=CharacterBible.model_validate(
                runtime.storage.load_json(layout.characters_path)
            ),
            character_system=CharacterSystem.model_validate(
                runtime.storage.load_json(layout.states_dir / "init_v2/character_system.json")
            ),
        )
    elif marker["artifact"] == "world":
        from novel_forge.app_service.character_artifacts import _run_async_to_completion

        _run_async_to_completion(lambda: _sync_world(runtime, layout, revision))
    atomic_write_json(done, {"revision_id": revision.revision_id, "status": "completed"})
    # Written last: if interrupted here, recovery can prove completion without
    # doing the SQLite merge twice. The caller clears the barrier from the receipt.
    atomic_write_json(layout.root / SYNC_PATH, {**marker, "status": "completed"})


async def _sync_world(runtime: Any, layout: ProjectLayout, revision: PlanningRevision) -> None:
    from novel_forge.app_service.character_artifacts import _load_story_kernel_for_layout
    from novel_forge.story_kernel.init_adapter import InitAdapter
    from novel_forge.story_kernel.schemas import StoryKernel, WorldRule
    from novel_forge.story_kernel.store import StoryKernelStore

    before = InitAdapter.map_story_bible_to_kernel(
        StoryBible.model_validate_json(revision.before["story_bible.json"])
    )
    after = InitAdapter.map_story_bible_to_kernel(
        StoryBible.model_validate(runtime.storage.load_json(layout.bible_path))
    )
    store = StoryKernelStore(layout.story_kernel_db_path)
    try:
        await store.init_db()
        kernel = await _load_story_kernel_for_layout(store, layout, StoryKernel)
        managed_ids = {r["rule_id"] for r in before["world_rules"]} | {
            r["rule_id"] for r in after["world_rules"]
        }
        kernel.world_rules = [r for r in kernel.world_rules if r.rule_id not in managed_ids] + [
            WorldRule.model_validate(r) for r in after["world_rules"]
        ]
        kernel.title, kernel.premise = after["title"], after["premise"]
        await store.save_kernel(kernel)
    finally:
        await store.close()
