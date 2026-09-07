"""Workspace entrypoint for applying manual final-chapter revisions."""

from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novel_forge.persistence.filesystem import (
    FileSystemStorage,
    atomic_write_json,
    atomic_write_text,
)
from novel_forge.persistence.final_revision_journal import (
    preserve_revision_dependencies,
    revision_for_proposal,
    revision_meta_path,
)
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import (
    InvalidationScope,
    RevisionScope,
    _coerce_scope,
    invalidate_downstream_generated_artifacts,
    resolve_manual_invalidation_range,
)
from novel_forge.workspace.contracts import ManualRevisionRequest
from novel_forge.workspace.execution_result import ExecutionResult, StepCallback
from novel_forge.workspace.helpers.execution_runners import _project_lock
from novel_forge.workspace.helpers.execution_state import _source_text_hash
from novel_forge.workspace.publication import (
    persist_chapter_publication_view,
    record_cross_media_freshness,
)
from novel_forge.workspace.runtime import RuntimeServices


def _revision_scope_to_invalidation(scope: str) -> InvalidationScope:
    raw = str(scope or "").strip().lower()
    if raw == RevisionScope.LOCAL.value:
        return InvalidationScope.NONE
    if raw == RevisionScope.VOLUME.value:
        return InvalidationScope.VOLUME
    if raw in {
        RevisionScope.FORWARD_ONLY.value,
        RevisionScope.FROM_CHAPTER_N.value,
        RevisionScope.WHOLE_BOOK.value,
    }:
        return InvalidationScope.DOWNSTREAM
    return _coerce_scope(raw)


def _write_revision_version(
    *,
    layout: ProjectLayout,
    project_id: str,
    chapter_number: int,
    previous_text: str,
    current_text: str,
    reason: str,
    scope: str = "forward_only",
    input_version: str = "",
) -> dict[str, Any]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    chapter_label = f"chapter_{chapter_number:03d}"
    version_dir = layout.states_dir / "final_revision_versions" / chapter_label
    version_dir.mkdir(parents=True, exist_ok=True)
    before_path = version_dir / f"{timestamp}_before.md"
    after_path = version_dir / f"{timestamp}_after.md"
    meta_path = version_dir / f"{timestamp}.json"
    previous_hash = _source_text_hash(previous_text)
    current_hash = _source_text_hash(current_text)
    atomic_write_text(before_path, previous_text)
    atomic_write_text(after_path, current_text)
    meta = {
        "schema_version": "1.0",
        "timestamp": timestamp,
        "project_id": project_id,
        "chapter_number": chapter_number,
        "chapter_path": str(layout.chapter_path(chapter_number)),
        "previous_hash": previous_hash,
        "current_hash": current_hash,
        "word_count": len(current_text),
        "reason": reason,
        "scope": scope,
        "input_version": input_version,
        "transaction_status": "prepared",
    }
    atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
    return {
        "timestamp": timestamp,
        "meta_path": meta_path,
        "previous_hash": previous_hash,
        "current_hash": current_hash,
        "word_count": len(current_text),
        "scope": scope,
        "reason": reason,
    }


def _finish_revision_commit(
    runtime: Any,
    layout: ProjectLayout,
    version: dict[str, Any],
    *,
    project_id: str,
    chapter_number: int,
) -> dict[str, Any]:
    """Complete only dependent state for an already-written exact prose hash."""
    if (
        _source_text_hash(layout.chapter_path(chapter_number).read_text(encoding="utf-8"))
        != version["current_hash"]
    ):
        raise ValueError("正文已变化，不能用旧修订恢复记录覆盖新版本")
    # Invalidation was durably recorded *before* changing prose. Repeating it
    # during recovery could delete work produced by later chapters.
    status_payload = version["final_revision_status"]
    publication = persist_chapter_publication_view(layout, project_id, chapter_number)
    lineage = record_cross_media_freshness(layout, publication, reason="manual_chapter_revision")
    result = {
        "project_id": project_id,
        "chapter_number": chapter_number,
        "status": "applied",
        "current_hash": version["current_hash"],
        "version_meta_path": str(version["meta_path"]),
        "final_revision_status": status_payload,
        "chapter_publication": publication.model_dump(mode="json", exclude={"text"}),
        "cross_media_lineage": lineage,
    }
    path = Path(version["meta_path"])
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta.update(transaction_status="applied", application_result=result)
    atomic_write_json(path, meta)
    return result


async def recover_committed_revision(
    runtime: Any,
    project_id: str,
    chapter_number: int,
    proposal_id: str,
) -> dict[str, Any] | None:
    """Reconcile a legal commit receipt, never replay its prose write."""
    async with _project_lock(runtime, project_id):
        layout = ProjectLayout(runtime.storage.existing_project_dir(project_id))
        version = revision_for_proposal(layout, chapter_number, proposal_id)
        if not version:
            return None
        if version.get("transaction_status") == "applied":
            return dict(version["application_result"])
        path = layout.chapter_path(chapter_number)
        if (
            not path.is_file()
            or _source_text_hash(path.read_text(encoding="utf-8")) != version["current_hash"]
        ):
            return None  # No proof of commit; ordinary CAS/approval checks apply.
        if not version.get("final_revision_status"):
            raise ValueError("修订提交证据不完整，请检查修订日志；未覆盖正文")
        version["meta_path"] = revision_meta_path(layout, chapter_number, version["timestamp"])
        return _finish_revision_commit(
            runtime, layout, version, project_id=project_id, chapter_number=chapter_number
        )


def _mark_manual_revision_requires_refresh(
    *,
    storage: FileSystemStorage,
    layout: ProjectLayout,
    project_id: str,
    chapter_number: int,
    version: dict[str, Any],
    scope: InvalidationScope,
    reason: str,
) -> dict[str, Any]:
    range_start, range_end, affected = resolve_manual_invalidation_range(
        scope=scope,
        chapter_number=chapter_number,
        layout=layout,
    )
    invalidated: list[int] = []
    if scope is not InvalidationScope.NONE and affected:
        invalidated = invalidate_downstream_generated_artifacts(
            storage,
            layout,
            completed_chapter=chapter_number,
            delete_chapter_files=False,
            max_chapter=range_end,
        )

    status_dir = layout.states_dir / "final_revision_status"
    status_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "timestamp": version["timestamp"],
        "revision_id": version["timestamp"],
        "project_id": project_id,
        "chapter_number": chapter_number,
        "previous_hash": version["previous_hash"],
        "current_hash": version["current_hash"],
        "word_count": version["word_count"],
        "requires_reevaluation": True,
        "requires_state_reextract": True,
        "requires_canon_reextract": True,
        "requires_humanize": True,
        "requires_final_verification": True,
        "publication_status": "blocked_pending_finalize",
        "scope": scope.value,
        "range_start": range_start,
        "range_end": range_end,
        "affected_chapters": list(affected),
        "reason": reason,
        "version_meta_path": str(version["meta_path"]),
        "invalidated_downstream_chapters": invalidated,
    }
    status_path = status_dir / f"chapter_{chapter_number:03d}.json"
    atomic_write_text(status_path, json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


async def execute_manual_revision(
    runtime: RuntimeServices,
    request: ManualRevisionRequest,
    *,
    on_step_progress: StepCallback = None,
) -> ExecutionResult[dict[str, Any]]:
    """Apply a manual final-chapter revision and mark dependent artifacts stale."""
    async with _project_lock(runtime, request.project_id):
        layout = ProjectLayout(runtime.storage.existing_project_dir(request.project_id))
        chapter_path = layout.chapter_path(request.chapter_number)
        if not chapter_path.exists():
            raise ValueError(f"第 {request.chapter_number} 章尚无终稿，无法应用手动修订。")
        previous_text = chapter_path.read_text(encoding="utf-8")
        current_text = request.text
        from novel_forge.persistence.authoring_store import (
            AuthoringDeniedError,
            AuthoringStore,
            content_version,
            story_input_version,
        )

        authority = AuthoringStore(layout.root)

        def check_authority() -> None:
            if (
                request.expected_input_version
                and request.expected_input_version != story_input_version(layout.root)
            ):
                raise AuthoringDeniedError("正文或故事输入已更新，未覆盖作者新修订")
            authority.require(
                "revise",
                request.chapter_number,
                explicit=True,
                approval_id=request.authoring_approval_id,
                candidate_version=content_version(
                    {
                        "text": request.text,
                        "chapter": request.chapter_number,
                        "scope": request.scope,
                    }
                ),
                major_change=True,
            )

        check_authority()
        if previous_text == current_text:
            result = {
                "project_id": request.project_id,
                "chapter_number": request.chapter_number,
                "status": "noop",
                "reason": "text_unchanged",
            }
            if on_step_progress:
                on_step_progress("manual_revision_noop", result)
            return ExecutionResult(project_id=request.project_id, result=result)

        if on_step_progress:
            on_step_progress(
                "manual_revision_start",
                {"chapter_number": request.chapter_number, "scope": request.scope},
            )

        version = _write_revision_version(
            layout=layout,
            project_id=request.project_id,
            chapter_number=request.chapter_number,
            previous_text=previous_text,
            current_text=current_text,
            reason=request.reason,
            scope=request.scope,
            input_version=story_input_version(layout.root),
        )
        scope = _revision_scope_to_invalidation(request.scope)
        _, _, affected = resolve_manual_invalidation_range(
            scope=scope, chapter_number=request.chapter_number, layout=layout
        )
        preserve_revision_dependencies(layout, version, {request.chapter_number, *affected})
        # Stop waits only for this short, synchronous legal commit, not the
        # potentially larger evidence copy above. Never await under this lock.
        with authority.lock() if authority.policy() is not None else nullcontext():
            check_authority()
            status_payload = _mark_manual_revision_requires_refresh(
                storage=runtime.storage,
                layout=layout,
                project_id=request.project_id,
                chapter_number=request.chapter_number,
                version=version,
                scope=scope,
                reason=request.reason,
            )
            meta = json.loads(Path(version["meta_path"]).read_text(encoding="utf-8"))
            meta["final_revision_status"] = status_payload
            atomic_write_json(Path(version["meta_path"]), meta)
            version["final_revision_status"] = status_payload
            atomic_write_text(chapter_path, current_text)
            result = _finish_revision_commit(
                runtime,
                layout,
                version,
                project_id=request.project_id,
                chapter_number=request.chapter_number,
            )
        try:
            from novel_forge.core.utils.edit_tracker import save_snapshot

            save_snapshot(chapter_path, layout.states_dir, request.chapter_number)
        except Exception:
            pass
        result["background_reevaluate_requested"] = bool(request.background_reevaluate)
        if on_step_progress:
            on_step_progress("manual_revision_done", result)
        return ExecutionResult(project_id=request.project_id, result=result)
