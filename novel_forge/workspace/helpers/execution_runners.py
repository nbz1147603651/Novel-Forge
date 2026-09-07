"""Pipeline runners for API and desktop entrypoints."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from novel_forge.core.exceptions import StateError
from novel_forge.core.guards import assert_not_coroutine
from novel_forge.core.infra.resource_locks import (
    ResourceLockType,
    ResourceName,
    get_resource_lock_manager,
)
from novel_forge.core.project_state import ProjectOperation, ProjectState, ProjectStateMachine
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.obs.logger import get_logger
from novel_forge.persistence.filesystem import FileSystemStorage, project_file_lock_owner
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.memory_finalization import (
    finalize_chapter_memory_phase,
)
from novel_forge.workspace.async_context import sync_to_async_context
from novel_forge.workspace.contracts import (
    CreateWorkRequest,
    InitLongRequest,
    PrepareChapterRequest,
    ReextractRelationshipsRequest,
    ResolveChapterCheckpointRequest,
    RunChapterRequest,
    RunShortRequest,
    SyncChapterContractsRequest,
)
from novel_forge.workspace.execution_result import ExecutionResult, StepCallback
from novel_forge.workspace.helpers.execution_helpers import _load_chapter_source_slice_if_available
from novel_forge.workspace.helpers.execution_manifest import (
    manifest_for_project,
    record_entry_failure,
    record_entry_success,
    wrap_manifest_step_callback,
)
from novel_forge.workspace.memory_warmup import warm_start_first_chapter_memory
from novel_forge.workspace.runtime import RuntimeServices, request_runtime_overrides
from novel_forge.workspace.sessions.chapter_sessions import (
    prepare_chapter_session,
    resolve_chapter_session,
)

_log = get_logger("workspace.execution_runners")


def _project_finalized_chapter(
    runtime: RuntimeServices,
    *,
    project_id: str,
    chapter_number: int,
    on_step_progress: StepCallback = None,
) -> bool:
    """Build derived publication/cross-media views after core finalization.

    Projection failures never roll back a committed novel chapter.  The
    ArtifactManifest recovery ledger added by the finalization phase records
    the pending projection and makes this helper safe to replay.
    """

    manifest = manifest_for_project(runtime.storage, project_id)
    text_hash = ""
    try:
        from novel_forge.pipeline.finalization_manifest import (
            matching_finalization_phase,
            record_finalization_success,
        )
        from novel_forge.workspace.publication import (
            persist_chapter_publication_view,
            record_cross_media_freshness,
        )

        layout = ProjectLayout(runtime.storage.existing_project_dir(project_id))
        chapter_path = layout.chapter_path(chapter_number)
        text_hash = source_text_hash(chapter_path.read_text(encoding="utf-8"))
        if (
            manifest is not None
            and matching_finalization_phase(
                manifest,
                chapter_number=chapter_number,
                phase="publication_projection",
                text_hash=text_hash,
            )
            is not None
            and layout.chapter_publication_path(chapter_number).is_file()
        ):
            if on_step_progress is not None:
                on_step_progress(
                    "finalization_phase_reused",
                    {
                        "chapter": chapter_number,
                        "phase": "publication_projection",
                        "text_hash": text_hash,
                    },
                )
            return True
        publication = persist_chapter_publication_view(
            layout,
            project_id,
            chapter_number,
            finalized=True,
        )
        lineage = record_cross_media_freshness(
            layout,
            publication,
            reason="novel_chapter_finalized",
        )
        if manifest is not None:
            record_finalization_success(
                manifest,
                chapter_number=chapter_number,
                phase="publication_projection",
                text_hash=publication.final_text_hash,
                output_hashes={"publication": publication.final_text_hash},
                paths={
                    "publication": str(layout.chapter_publication_path(chapter_number)),
                    "cross_media": str(layout.cross_media_lineage_path),
                },
                metadata={"publication_status": publication.publication_status},
            )
        if on_step_progress is not None:
            on_step_progress(
                "chapter_publication_ready",
                {
                    "chapter": chapter_number,
                    "final_text_hash": publication.final_text_hash,
                    "publication_status": publication.publication_status,
                    "tts_status": lineage["tts"]["status"],
                    "film_status": lineage["film"]["status"],
                },
            )
        return True
    except Exception as exc:
        if manifest is not None and text_hash:
            from novel_forge.pipeline.finalization_manifest import (
                record_finalization_pending,
            )

            record_finalization_pending(
                manifest,
                chapter_number=chapter_number,
                phase="publication_projection",
                text_hash=text_hash,
                error=exc,
            )
        _log.warning(
            "cross_media_publication_projection_failed | project=%s | chapter=%d | error=%s",
            project_id,
            chapter_number,
            exc,
            exc_info=True,
        )
        if on_step_progress is not None:
            on_step_progress(
                "chapter_publication_pending",
                {
                    "chapter": chapter_number,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                },
            )
        return False


def _ensure_required_finalization_phases(
    runtime: RuntimeServices,
    *,
    project_id: str,
    chapter_number: int,
) -> None:
    """Refuse a successful workflow result until required commits are durable."""

    from novel_forge.pipeline.finalization_manifest import matching_finalization_phase

    layout = ProjectLayout(runtime.storage.existing_project_dir(project_id))
    chapter_path = layout.chapter_path(chapter_number)
    if not chapter_path.is_file():
        raise StateError(
            "chapter_finalization",
            expected="final chapter text",
            actual="missing",
        )
    text_hash = source_text_hash(chapter_path.read_text(encoding="utf-8"))
    manifest = manifest_for_project(runtime.storage, project_id)
    if manifest is None:
        raise StateError(
            "chapter_finalization",
            expected="artifact manifest",
            actual="unavailable",
        )
    required = ["final_text", "reports"]
    if bool(getattr(runtime.settings, "narrative_state_required", True)):
        required.extend(("narrative_state", "story_kernel"))
    missing = [
        phase
        for phase in required
        if matching_finalization_phase(
            manifest,
            chapter_number=chapter_number,
            phase=phase,
            text_hash=text_hash,
        )
        is None
    ]
    if missing:
        raise StateError(
            "chapter_finalization",
            expected="required phases committed",
            actual=",".join(missing),
        )


def _clear_memory_progress_callback(memory_ctx: Any) -> None:
    set_callback = getattr(memory_ctx, "set_progress_callback", None)
    if not callable(set_callback):
        return
    try:
        set_callback(None)
    except Exception as exc:
        _log.debug("Failed to clear memory progress callback: %s", exc)


@asynccontextmanager
async def _project_lock(
    runtime: Any,
    project_id: str,
    resource: ResourceName = ResourceName.STATE,
    lock_type: ResourceLockType = ResourceLockType.EXCLUSIVE,
    *,
    chapter: int | None = None,
    storage_lock_type: ResourceLockType | None = ResourceLockType.EXCLUSIVE,
) -> AsyncIterator[None]:
    storage = getattr(runtime, "storage", None)
    owner_id = f"async-task:{id(asyncio.current_task())}"

    with project_file_lock_owner(owner_id):
        async with AsyncExitStack() as stack:
            try:
                lock_manager = get_resource_lock_manager()
                await stack.enter_async_context(
                    lock_manager.lock(resource, lock_type, chapter=chapter, project_id=project_id)
                )
            except Exception:
                _log.warning(
                    "Resource lock acquisition failed (falling back to fcntl only) | "
                    "resource=%s lock_type=%s project=%s",
                    resource.value,
                    lock_type.value,
                    project_id,
                )

            if storage_lock_type == ResourceLockType.EXCLUSIVE:
                fcntl_lock = getattr(storage, "project_lock", None)
            elif storage_lock_type == ResourceLockType.SHARED:
                fcntl_lock = getattr(storage, "project_shared_lock", None)
            else:
                fcntl_lock = None
            if callable(fcntl_lock):
                sync_ctx = fcntl_lock(project_id)
                await stack.enter_async_context(sync_to_async_context(sync_ctx))

            from novel_forge.persistence.planning_revision import (
                assert_planning_publish_complete,
                recover_planning_publish,
            )

            if storage is not None:
                project_root = storage.project_dir(project_id)
                if storage_lock_type == ResourceLockType.EXCLUSIVE:
                    recover_planning_publish(project_root)
                else:
                    assert_planning_publish_complete(project_root)
            yield


@asynccontextmanager
async def _project_shared_lock(
    runtime: Any,
    project_id: str,
    resource: ResourceName = ResourceName.OUTLINE,
    lock_type: ResourceLockType = ResourceLockType.SHARED,
    *,
    chapter: int | None = None,
) -> AsyncIterator[None]:
    storage = getattr(runtime, "storage", None)
    owner_id = f"async-task:{id(asyncio.current_task())}"

    with project_file_lock_owner(owner_id):
        async with AsyncExitStack() as stack:
            try:
                lock_manager = get_resource_lock_manager()
                await stack.enter_async_context(
                    lock_manager.lock(resource, lock_type, chapter=chapter, project_id=project_id)
                )
            except Exception:
                pass

            fcntl_lock = getattr(storage, "project_shared_lock", None)
            if callable(fcntl_lock):
                sync_ctx = fcntl_lock(project_id)
                await stack.enter_async_context(sync_to_async_context(sync_ctx))

            from novel_forge.persistence.planning_revision import assert_planning_publish_complete

            if storage is not None:
                assert_planning_publish_complete(storage.project_dir(project_id))
            yield


def _extract_chapter_texts(result: Any) -> tuple[str, str]:
    chapter_text = str(getattr(result, "text", "") or "")
    creative_report = getattr(result, "creative_report", None)
    report_text = str(getattr(creative_report, "summary", "") or "")
    return chapter_text, report_text


def short_spec_input_from_request(request: RunShortRequest) -> dict[str, Any]:
    return {
        "theme": request.theme,
        "genre": request.genre,
        "tone": request.tone,
        "length_target": request.length_target,
        "title": request.title,
        "language": request.language,
        "characters_hint": request.characters_hint,
        "world_hint": request.world_hint,
        "conflict_hint": request.conflict_hint,
        "pov_hint": request.pov_hint,
        "opening_style": request.opening_style,
        "ending_style": request.ending_style,
        "extra_instructions": request.extra_instructions,
    }


def _load_project_state_machine(
    runtime: Any,
    project_id: str,
    *,
    create_if_missing: bool = True,
) -> ProjectStateMachine | None:
    storage = getattr(runtime, "storage", None)
    if storage is None:
        return None

    if create_if_missing:
        ensure_project_dir = getattr(storage, "ensure_project_dir", None)
        if not callable(ensure_project_dir):
            return None
        project_dir = ensure_project_dir(project_id)
    else:
        project_path = getattr(storage, "project_path", None)
        if callable(project_path):
            project_dir = project_path(project_id)
        else:
            root = getattr(storage, "root", None)
            if root is None:
                return None
            project_dir = root / project_id
        if not project_dir.exists():
            return None

    try:
        return ProjectStateMachine.load_from_disk(project_id, project_dir)
    except FileNotFoundError:
        if not create_if_missing:
            return None
        return ProjectStateMachine(project_id, project_dir=project_dir)


def _begin_init_lifecycle(state_machine: ProjectStateMachine | None) -> bool:
    if state_machine is None:
        return False
    if state_machine.state == ProjectState.CREATED:
        state_machine.execute(ProjectOperation.INIT)
        return True
    if state_machine.state == ProjectState.INIT_FAILED:
        state_machine.execute(ProjectOperation.RETRY)
        return True
    return state_machine.state == ProjectState.INITIALIZING


def _finalize_init_lifecycle(
    state_machine: ProjectStateMachine | None,
    *,
    success: bool,
) -> None:
    if state_machine is None or state_machine.state != ProjectState.INITIALIZING:
        return
    operation = ProjectOperation.COMPLETE_INIT if success else ProjectOperation.FAIL_INIT
    if state_machine.can_execute(operation):
        state_machine.execute(operation)


def _finalize_writing_lifecycle(state_machine: ProjectStateMachine | None) -> None:
    if state_machine is None:
        return
    if state_machine.state == ProjectState.OUTLINE_READY:
        operation = ProjectOperation.START_WRITING
    elif state_machine.state in {ProjectState.PAUSED, ProjectState.COMPLETED}:
        operation = ProjectOperation.RESUME
    else:
        return
    if state_machine.can_execute(operation):
        state_machine.execute(operation)


def _is_paused_chapter_result(result: Any) -> bool:
    status = getattr(result, "status", None)
    metadata = getattr(result, "metadata", None)
    return (
        status == "needs_decision" and isinstance(metadata, dict) and bool(metadata.get("paused"))
    )


def _is_completed_chapter_result(result: Any) -> bool:
    status = result.get("status") if isinstance(result, dict) else getattr(result, "status", None)
    return status is None or status == "completed"


def _project_is_complete(storage: Any, project_id: str) -> bool:
    if storage is None:
        return False
    try:
        layout = ProjectLayout(storage.existing_project_dir(project_id))
        if not storage.exists(layout.outline_path):
            return False
        outline_payload = storage.load_json(layout.outline_path)
    except Exception:
        return False

    total_chapters = outline_payload.get("total_chapters")
    if not isinstance(total_chapters, int) or total_chapters <= 0:
        return False

    chapter_files = (
        [
            path
            for path in layout.chapters_dir.iterdir()
            if path.is_file() and path.suffix == ".md" and path.stem.startswith("chapter_")
        ]
        if layout.chapters_dir.is_dir()
        else []
    )
    return len(chapter_files) >= total_chapters


def _finalize_project_lifecycle_after_chapter(
    state_machine: ProjectStateMachine | None,
    *,
    storage: Any,
    project_id: str,
    result: Any,
) -> None:
    if state_machine is None or result is None:
        return
    if _is_paused_chapter_result(result):
        if state_machine.can_execute(ProjectOperation.PAUSE):
            state_machine.execute(ProjectOperation.PAUSE)
        return
    if not _is_completed_chapter_result(result):
        return
    if _project_is_complete(storage, project_id) and state_machine.can_execute(
        ProjectOperation.COMPLETE
    ):
        state_machine.execute(ProjectOperation.COMPLETE)


async def execute_run_short(
    runtime: RuntimeServices,
    request: RunShortRequest,
    *,
    on_step_progress: StepCallback = None,
) -> ExecutionResult[Any]:
    project_id = request.project_id.strip() or runtime.create_project_id("short")
    storage = getattr(runtime, "storage", None)
    progress = wrap_manifest_step_callback(storage, project_id, "run_short", on_step_progress)
    try:
        async with _project_lock(runtime, project_id, ResourceName.STATE):
            runner = runtime.short_runner(
                max_edit_rounds=request.max_edit_rounds,
                writing_mode=request.writing_mode,
                on_step_progress=progress,
            )
            result = await runner.run(
                short_spec_input_from_request(request),
                project_id=project_id,
                segmented_mode=request.segmented_mode,
                segment_target_words=request.segment_target_words,
                segment_max_count=request.segment_max_count,
                blueprint_element_preferences=request.blueprint_element_preferences,
                research_enabled=request.research_enabled,
                research_provider=request.research_provider,
                research_query_hint=request.research_query_hint,
            )
    except Exception as exc:
        record_entry_failure(storage, project_id, "run_short", exc)
        raise
    record_entry_success(storage, project_id, "run_short", result=result)
    return ExecutionResult(project_id=project_id, result=result)


async def execute_init_long(
    runtime: RuntimeServices,
    request: InitLongRequest,
    *,
    on_step_progress: StepCallback = None,
) -> ExecutionResult[Any]:
    is_config_stale = getattr(runtime, "is_config_stale", None)
    if callable(is_config_stale) and is_config_stale():
        reload_runtime_dependencies = getattr(runtime, "reload_runtime_dependencies", None)
        if callable(reload_runtime_dependencies):
            reload_runtime_dependencies()
    project_id = request.project_id.strip() or runtime.create_project_id("long")
    storage = getattr(runtime, "storage", None)
    from novel_forge.persistence.foundation_guard import require_versioned_foundation_write

    project_root = storage.project_path(project_id) if storage is not None else None
    if isinstance(project_root, Path):
        require_versioned_foundation_write(project_root)
    from novel_forge.pipeline.long.services.init.init_v2 import _get_template_version_label

    snapshot = getattr(runtime, "config_snapshot", None)
    lineage_metadata = {
        "template_version": _get_template_version_label(),
        "config_fingerprint": getattr(snapshot, "effective_settings_version", ""),
        "model_fingerprint": getattr(snapshot, "profiles_version", ""),
        "runtime_config_version": getattr(snapshot, "config_version", ""),
        "runtime_config_stale": False,
    }
    progress = wrap_manifest_step_callback(storage, project_id, "init_long", on_step_progress)
    async with _project_lock(
        runtime,
        project_id,
        ResourceName.STATE,
        lock_type=ResourceLockType.EXCLUSIVE,
        storage_lock_type=ResourceLockType.SHARED,
    ):
        state_machine = _load_project_state_machine(runtime, project_id)
        track_init_lifecycle = _begin_init_lifecycle(state_machine)
        runner = runtime.chapter_runner(
            on_step_progress=progress,
            warn_missing_memory_context=False,
        )
        memory_context = None
        get_memory_context = getattr(runtime, "get_memory_context", None)
        if callable(get_memory_context):
            memory_context = await get_memory_context(project_id=project_id, storage=storage)
        if memory_context is not None:
            runner._memory_context = memory_context
        try:
            if request.regenerate_outline:
                from novel_forge.pipeline.long.services.init.init_outline_recovery import (
                    prepare_outline_regeneration,
                )

                layout = ProjectLayout(runtime.storage.ensure_project_dir(project_id))
                regeneration = prepare_outline_regeneration(
                    runtime.storage,
                    layout,
                    apply=True,
                )
                if progress is not None:
                    progress("plan_outline_regeneration_prepared", regeneration)
            init_kwargs = {
                "project_id": project_id,
                "genre": request.genre,
                "tone": request.tone,
                "title": request.title,
                "language": request.language,
                "characters_hint": request.characters_hint,
                "world_hint": request.world_hint,
                "conflict_hint": request.conflict_hint,
                "pov_hint": request.pov_hint,
                "opening_style": request.opening_style,
                "ending_style": request.ending_style,
                "extra_instructions": request.extra_instructions,
                "total_chapters": request.total_chapters,
                "words_per_chapter": request.words_per_chapter,
                "volume_mode": request.volume_mode,
                "chapters_per_volume": request.chapters_per_volume,
                "blueprint_element_preferences": request.blueprint_element_preferences,
                "research_enabled": request.research_enabled,
                "research_provider": request.research_provider,
                "research_query_hint": request.research_query_hint,
            }
            if request.copilot_gates:
                init_kwargs["copilot_gates"] = request.copilot_gates
            if request.polish_hint.strip():
                init_kwargs["polish_hint"] = request.polish_hint
            result = await runner.init_long(
                request.premise,
                creative_exploration=request.creative_exploration,
                planning_commitment=request.planning_commitment,
                **init_kwargs,
            )
        except Exception as exc:
            if track_init_lifecycle:
                _finalize_init_lifecycle(state_machine, success=False)
            record_entry_failure(
                storage,
                project_id,
                "init_long",
                exc,
                metadata=lineage_metadata,
            )
            raise
        if track_init_lifecycle:
            _finalize_init_lifecycle(state_machine, success=True)
    record_entry_success(
        storage,
        project_id,
        "init_long",
        result=result,
        metadata=lineage_metadata,
    )
    return ExecutionResult(project_id=project_id, result=result)


async def execute_run_chapter(
    runtime: RuntimeServices,
    request: RunChapterRequest,
    *,
    on_step_progress: StepCallback = None,
    defer_post_archive_tts: bool = False,
) -> ExecutionResult[Any]:
    from novel_forge.workspace.authoring_control import authoring_operation

    with (
        request_runtime_overrides(runtime, request),
        authoring_operation(runtime, request, "archive"),
    ):
        storage = getattr(runtime, "storage", None)
        progress = wrap_manifest_step_callback(
            storage, request.project_id, "run_chapter", on_step_progress
        )
        try:
            from novel_forge.workspace.execution_planning_horizon import ensure_chapter_planning

            await ensure_chapter_planning(
                runtime,
                project_id=request.project_id,
                chapter_number=request.chapter_number,
                on_step_progress=progress,
            )
            async with _project_lock(
                runtime,
                request.project_id,
                ResourceName.STATE,
                lock_type=ResourceLockType.EXCLUSIVE,
                chapter=request.chapter_number,
            ):
                state_machine = _load_project_state_machine(
                    runtime,
                    request.project_id,
                    create_if_missing=False,
                )
                result = await _execute_run_chapter_locked(
                    runtime,
                    request,
                    storage=storage,
                    state_machine=state_machine,
                    on_step_progress=progress,
                )
            if _is_completed_chapter_result(result):
                from novel_forge.workspace.execution_post_archive import complete_chapter_archive

                await complete_chapter_archive(
                    runtime,
                    project_id=request.project_id,
                    chapter_number=request.chapter_number,
                    on_step_progress=progress,
                    defer_post_archive_tts=defer_post_archive_tts,
                )
        except Exception as exc:
            record_entry_failure(
                storage,
                request.project_id,
                "run_chapter",
                exc,
                chapter_number=request.chapter_number,
            )
            raise
        record_entry_success(
            storage,
            request.project_id,
            "run_chapter",
            result=result,
            chapter_number=request.chapter_number,
        )
        return ExecutionResult(project_id=request.project_id, result=result)


async def _execute_run_chapter_locked(
    runtime: RuntimeServices,
    request: RunChapterRequest,
    *,
    storage: Any,
    state_machine: ProjectStateMachine | None,
    on_step_progress: StepCallback = None,
) -> Any:
    from novel_forge.workspace.planning_horizon import assert_chapter_within_hard_window

    assert_chapter_within_hard_window(
        storage,
        project_id=request.project_id,
        chapter_number=request.chapter_number,
    )
    memory_ctx: Any = None
    memory_lease_acquired = False
    acquire_memory_context = getattr(runtime, "acquire_memory_context", None)
    get_memory_context = getattr(runtime, "get_memory_context", None)
    if callable(acquire_memory_context):
        memory_ctx = await acquire_memory_context(
            project_id=request.project_id,
            storage=storage,
        )
        memory_lease_acquired = memory_ctx is not None
        assert_not_coroutine(memory_ctx, "acquire_memory_context at execution_runners.py:183")
    elif callable(get_memory_context):
        memory_ctx = await get_memory_context(
            project_id=request.project_id,
            storage=storage,
        )
        assert_not_coroutine(memory_ctx, "get_memory_context at execution_runners.py:183")

    await warm_start_first_chapter_memory(
        memory_ctx,
        project_id=request.project_id,
        chapter_number=request.chapter_number,
    )

    if memory_ctx is not None and on_step_progress is not None:

        def memory_progress_callback(stage: str, data: dict[str, Any]) -> None:
            payload: dict[str, Any] = {
                "chapter": data.get("chapter", request.chapter_number),
                **{k: v for k, v in data.items() if k != "chapter"},
            }
            if stage in {
                "indexing_complete",
                "summary_generated",
                "motifs_extracted",
                "motifs_completed",
            }:
                try:
                    payload["memory_status"] = memory_ctx.get_memory_status_for_ui()
                except Exception as exc:
                    _log.debug("Failed to get memory status for UI: %s", exc)
            on_step_progress(f"memory_{stage}", payload)

        memory_ctx.set_progress_callback(memory_progress_callback)
        _log.debug("Memory progress callback registered | project=%s", request.project_id)

    try:
        runner = runtime.chapter_runner(
            writing_mode=request.writing_mode,
            on_step_progress=on_step_progress,
            memory_context=memory_ctx,
        )
        result = await runner.run_chapter(
            request.project_id,
            request.chapter_number,
            force_regenerate=request.force,
            chapter_instruction=request.notes,
        )
        if _is_completed_chapter_result(result):
            _ensure_required_finalization_phases(runtime, project_id=request.project_id,
                                                 chapter_number=request.chapter_number)
        _finalize_writing_lifecycle(state_machine)
        _finalize_project_lifecycle_after_chapter(
            state_machine,
            storage=storage,
            project_id=request.project_id,
            result=result,
        )

        if memory_ctx is not None and result is not None:
            chapter_text, report_text = _extract_chapter_texts(result)
            if chapter_text:
                layout = ProjectLayout(storage.existing_project_dir(request.project_id))
                memory_result = await finalize_chapter_memory_phase(
                    storage=storage,
                    layout=layout,
                    memory_context=memory_ctx,
                    chapter_number=request.chapter_number,
                    chapter_text=chapter_text,
                    creative_report_text=report_text,
                    chapter_result=result,
                    on_step=on_step_progress,
                )
                if memory_result.status == "pending":
                    _log.warning(
                        "记忆阶段待补偿 | project=%s | chapter=%d | error=%s",
                        request.project_id,
                        request.chapter_number,
                        memory_result.error,
                    )
    finally:
        if memory_ctx is not None:
            _clear_memory_progress_callback(memory_ctx)
        if memory_lease_acquired:
            release_lease = getattr(runtime, "release_memory_context_lease", None)
            if callable(release_lease):
                release_lease(request.project_id)
    return result


async def execute_prepare_chapter(
    runtime: RuntimeServices,
    request: PrepareChapterRequest,
    *,
    on_step_progress: StepCallback = None,
) -> ExecutionResult[Any]:
    from novel_forge.workspace.authoring_control import authoring_operation

    with (
        request_runtime_overrides(runtime, request),
        authoring_operation(runtime, request, "prepare"),
    ):
        storage = getattr(runtime, "storage", None)
        progress = wrap_manifest_step_callback(
            storage,
            request.project_id,
            "prepare_chapter",
            on_step_progress,
        )
        try:
            from novel_forge.workspace.execution_planning_horizon import ensure_chapter_planning

            await ensure_chapter_planning(
                runtime,
                project_id=request.project_id,
                chapter_number=request.chapter_number,
                on_step_progress=progress,
            )
            async with _project_lock(
                runtime,
                request.project_id,
                ResourceName.STATE,
                lock_type=ResourceLockType.EXCLUSIVE,
                chapter=request.chapter_number,
                storage_lock_type=ResourceLockType.SHARED,
            ):
                result = await prepare_chapter_session(
                    runtime,
                    request,
                    on_step_progress=progress,
                )
        except Exception as exc:
            record_entry_failure(
                storage,
                request.project_id,
                "prepare_chapter",
                exc,
                chapter_number=request.chapter_number,
            )
            raise
        record_entry_success(
            storage,
            request.project_id,
            "prepare_chapter",
            result=result,
            chapter_number=request.chapter_number,
        )
        return ExecutionResult(project_id=request.project_id, result=result)


async def execute_resolve_chapter_checkpoint(
    runtime: RuntimeServices,
    request: ResolveChapterCheckpointRequest,
    *,
    on_step_progress: StepCallback = None,
    defer_post_archive_tts: bool = False,
) -> ExecutionResult[Any]:
    from novel_forge.workspace.authoring_control import authoring_operation, checkpoint_action

    with (
        request_runtime_overrides(runtime, request),
        authoring_operation(runtime, request, checkpoint_action(request.option_id)),
    ):
        storage = getattr(runtime, "storage", None)
        progress = wrap_manifest_step_callback(
            storage,
            request.project_id,
            "resolve_chapter_checkpoint",
            on_step_progress,
        )
        try:
            async with _project_lock(
                runtime,
                request.project_id,
                ResourceName.STATE,
                lock_type=ResourceLockType.EXCLUSIVE,
                chapter=request.chapter_number,
            ):
                state_machine = _load_project_state_machine(
                    runtime,
                    request.project_id,
                    create_if_missing=False,
                )
                result = await resolve_chapter_session(
                    runtime,
                    request,
                    on_step_progress=progress,
                )
                if _is_completed_chapter_result(result):
                    _ensure_required_finalization_phases(
                        runtime,
                        project_id=request.project_id,
                        chapter_number=request.chapter_number,
                    )
                _finalize_writing_lifecycle(state_machine)
                _finalize_project_lifecycle_after_chapter(
                    state_machine,
                    storage=storage,
                    project_id=request.project_id,
                    result=result,
                )
            if _is_completed_chapter_result(result):
                from novel_forge.workspace.execution_post_archive import complete_chapter_archive

                await complete_chapter_archive(
                    runtime,
                    project_id=request.project_id,
                    chapter_number=request.chapter_number,
                    on_step_progress=progress,
                    defer_post_archive_tts=defer_post_archive_tts,
                )
        except Exception as exc:
            record_entry_failure(
                storage,
                request.project_id,
                "resolve_chapter_checkpoint",
                exc,
                chapter_number=request.chapter_number,
            )
            raise
        record_entry_success(
            storage,
            request.project_id,
            "resolve_chapter_checkpoint",
            result=result,
            chapter_number=request.chapter_number,
        )
        return ExecutionResult(project_id=request.project_id, result=result)


def create_project_workspace(
    storage: FileSystemStorage,
    runtime: RuntimeServices,
    request: CreateWorkRequest,
) -> str:
    project_id = runtime.create_project_id(request.mode)
    storage.ensure_project_dir(project_id)
    return project_id


async def execute_reextract_relationships(
    runtime: RuntimeServices,
    request: "ReextractRelationshipsRequest",
    *,
    on_step_progress: StepCallback = None,
) -> ExecutionResult[dict[str, Any]]:
    from novel_forge.persistence.foundation_guard import require_versioned_maintenance_write

    require_versioned_maintenance_write(
        runtime.storage.existing_project_dir(request.project_id), "重新抽取并覆盖关系"
    )
    from novel_forge.core.domain.guardrails import is_system_artifact_name
    from novel_forge.obs.tracer import PipelineTrace
    from novel_forge.persistence.models import ProjectLayout
    from novel_forge.pipeline.steps.extract_step import ExtractCanonDeltaStep, ExtractInput
    from novel_forge.story_kernel.state_tracker import StateTracker
    from novel_forge.story_kernel.store import StoryKernelStore

    async with _project_lock(
        runtime,
        request.project_id,
        ResourceName.CANON,
        lock_type=ResourceLockType.EXCLUSIVE,
    ):
        storage = runtime.storage
        project_dir = storage.project_dir(request.project_id)
        layout = ProjectLayout(project_dir)
        canon_store = StoryKernelStore(layout.story_kernel_db_path)

        try:
            canon_state = await canon_store.load_kernel(request.project_id)
        except ValueError:
            return ExecutionResult(
                project_id=request.project_id,
                result={"error": "Canon not initialized", "chapters_processed": 0},
            )

        if request.chapter_number > 0:
            chapters = [request.chapter_number]
        else:
            chapters = list(range(1, canon_state.current_chapter + 1))

        chapters = [ch for ch in chapters if layout.chapter_path(ch).exists()]

        if not chapters:
            return ExecutionResult(
                project_id=request.project_id,
                result={"error": "No completed chapters found", "chapters_processed": 0},
            )

        if on_step_progress:
            on_step_progress(
                "reextract_start",
                {
                    "chapters": chapters,
                    "total": len(chapters),
                },
            )

        trace = PipelineTrace()
        extract_step = ExtractCanonDeltaStep(
            runtime.router,
            runtime.builder,
            settings=runtime.settings,
            trace=trace,
        )

        total_relationships = 0
        processed = 0
        known_characters = list(canon_state.get_all_characters().keys())

        for ch_num in chapters:
            try:
                chapter_path = layout.chapter_path(ch_num)
                chapter_text = chapter_path.read_text(encoding="utf-8")

                if len(chapter_text.strip()) < 500:
                    _log.debug(
                        "Skipping chapter %d — too short (%d chars)", ch_num, len(chapter_text)
                    )
                    continue

                outline_summary = ""
                outline_path = layout.chapter_plan_path(ch_num)
                if outline_path.exists():
                    try:
                        import json

                        plan_data = json.loads(outline_path.read_text(encoding="utf-8"))
                        outline_summary = (
                            plan_data.get("title", "")
                            + " - "
                            + plan_data.get("goal", plan_data.get("summary", ""))
                        )
                    except (OSError, json.JSONDecodeError):
                        _log.debug(
                            "Failed to load outline for re-extract chapter %d",
                            ch_num,
                            exc_info=True,
                        )

                if on_step_progress:
                    on_step_progress(
                        "reextract_chapter",
                        {
                            "chapter": ch_num,
                            "progress": processed + 1,
                            "total": len(chapters),
                        },
                    )

                chapter_source_slice = _load_chapter_source_slice_if_available(
                    runtime.storage,
                    layout,
                    project_id=request.project_id,
                    chapter_number=ch_num,
                )
                outcome = await extract_step.run(
                    ExtractInput(
                        chapter_number=ch_num,
                        chapter_text=chapter_text,
                        known_characters=known_characters,
                        chapter_outline_summary=outline_summary,
                        chapter_source_slice=chapter_source_slice,
                    )
                )

                for relationship_delta in outcome.relationship_deltas:
                    if any(
                        is_system_artifact_name(name)
                        for name in relationship_delta.relationship.characters
                    ):
                        continue
                    canon_state.relationships[relationship_delta.pair_id] = (
                        relationship_delta.relationship
                    )
                    total_relationships += 1

                for plot_thread_delta in outcome.plot_thread_deltas:
                    canon_state.plot_threads[plot_thread_delta.thread_id] = plot_thread_delta.thread

                for character_delta in outcome.character_state_deltas:
                    if is_system_artifact_name(character_delta.name):
                        continue
                    existing = canon_state.get_character_by_name(character_delta.name)
                    merged_char = StateTracker._merge_character_state(
                        existing, character_delta.to_state
                    )
                    canon_state.set_character(character_delta.name, merged_char)

                if outcome.chapter_exit_state is not None:
                    outcome.chapter_exit_state.character_end_states = {
                        name: cs
                        for name, cs in outcome.chapter_exit_state.character_end_states.items()
                        if not is_system_artifact_name(name)
                    }
                    canon_state.chapter_exit_states[ch_num] = outcome.chapter_exit_state

                if outcome.creative_report is not None:
                    storage.save_json(
                        layout.creative_report_path(ch_num),
                        outcome.creative_report.model_dump(mode="json"),
                    )

                processed += 1
                _log.info(
                    "Re-extracted chapter %d | relationships=%d | threads=%d",
                    ch_num,
                    len(outcome.relationship_deltas),
                    len(outcome.plot_thread_deltas),
                )

            except Exception as exc:
                _log.error("Failed to re-extract chapter %d: %s", ch_num, exc, exc_info=True)
                if on_step_progress:
                    on_step_progress(
                        "reextract_error",
                        {
                            "chapter": ch_num,
                            "error": str(exc),
                        },
                    )

        await canon_store.save_kernel(canon_state)

        if on_step_progress:
            on_step_progress(
                "reextract_done",
                {
                    "chapters_processed": processed,
                    "total_relationships": total_relationships,
                },
            )

        return ExecutionResult(
            project_id=request.project_id,
            result={
                "chapters_processed": processed,
                "total_relationships": total_relationships,
            },
        )


async def execute_sync_chapter_contracts(
    runtime: "RuntimeServices",
    request: "SyncChapterContractsRequest",
    *,
    on_step_progress: "StepCallback" = None,
    strict: bool = False,
) -> "ExecutionResult[dict[str, Any]]":
    """Local regeneration of chapter contracts after an outline edit.

    Workflow:

    1. Acquire the project-level exclusive lock.
    2. Load ``outline.json``, ``chapter_contracts.json``,
       ``narrative_contract.json`` (best-effort, may be missing).
    3. Resolve affected chapters: explicit list from the request, or
       auto-detect via outline fingerprint.
    4. Compute downstream cascade via ``compute_cascade`` (bounded depth).
    5. LLM re-extract contracts for the focus set via
       ``service_ctx.call_with_retry(TaskType.PLAN_CHAPTER_CONTRACTS, ...)``.
    6. Merge reextracted contracts into the existing payload via
       ``merge_reextracted_contracts`` (preserves untouched chapters).
    7. Ensure full coverage via ``ensure_chapter_contract_coverage`` as a
       defensive fallback (local backfill, no LLM).
    8. Rebuild the plot milestone index (only writes when ``source_hash``
       changes).
    9. Mark downstream artifacts stale (chapter plan/bridge, contract
       coherence/completion reports, review progress files).
    10. **Never** touch ``chapters/chapter_*.md``, ``progression_ledger``,
        ``character_bible``, ``narrative_state/``, ``story_kernel.db``,
        or ``memory/``.

    The function emits structured ``on_step_progress`` events so the
    Desktop JobManager can drive the widget step-flow indicator.
    """
    from novel_forge.core.constants import TaskType
    from novel_forge.core.schemas.outline import StoryOutline
    from novel_forge.persistence.models import ProjectLayout
    from novel_forge.pipeline.long.services.chapter_contract_sync import (
        analyze_outline_change_scope,
        collect_downstream_stale_paths,
        compute_cascade,
        compute_outline_chapter_fingerprints,
        compute_outline_chapter_snapshots,
        compute_outline_fingerprint,
        mark_artifact_stale,
        merge_reextracted_contracts,
        restore_nonfocus_contract_items,
    )
    from novel_forge.pipeline.long.services.init_repair.policies.chapter_contracts import (
        _chapter_contract_repair_outline_payload,
        ensure_chapter_contract_coverage,
    )
    from novel_forge.pipeline.long.services.plot_milestones import (
        build_plot_milestone_index,
    )
    from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens

    started_at = __import__("time").monotonic()

    from novel_forge.persistence.authoring_store import AuthoringStore

    root = runtime.storage.project_path(request.project_id)
    from novel_forge.persistence.foundation_guard import is_isolated_planning_candidate

    if (
        isinstance(root, Path)
        and AuthoringStore(root).policy() is not None
        and not is_isolated_planning_candidate(root)
    ):
        from novel_forge.workspace.execution_outline_polish import propose_contract_sync

        return await propose_contract_sync(runtime, request, on_step_progress=on_step_progress)

    async with _project_lock(
        runtime,
        request.project_id,
        ResourceName.CANON,
        lock_type=ResourceLockType.EXCLUSIVE,
    ):
        storage = runtime.storage
        project_dir = storage.project_dir(request.project_id)
        layout = ProjectLayout(project_dir)

        # 1. Load inputs.
        outline_data = _safe_load_json(storage, layout.outline_path)
        if not outline_data:
            return ExecutionResult(
                project_id=request.project_id,
                result={
                    "project_id": request.project_id,
                    "status": "failed",
                    "error": "outline.json not found or invalid",
                    "refreshed": 0,
                    "milestones_rebuilt": 0,
                    "stale_marked": 0,
                },
            )
        try:
            outline = StoryOutline.model_validate(outline_data)
        except Exception as exc:  # noqa: BLE001
            return ExecutionResult(
                project_id=request.project_id,
                result={
                    "project_id": request.project_id,
                    "status": "failed",
                    "error": f"invalid outline: {exc}",
                    "refreshed": 0,
                    "milestones_rebuilt": 0,
                    "stale_marked": 0,
                },
            )

        chapter_contracts = _safe_load_json(
            storage,
            layout.plans_dir / "chapter_contracts.json",
        ) or {"chapter_contracts": []}
        narrative_contract = _safe_load_json(storage, layout.narrative_contract_path) or {}

        # 2. Resolve affected chapters.
        explicit = {int(n) for n in request.affected_chapter_numbers if int(n or 0) > 0}
        cached_chapter_fps = _read_cached_chapter_fingerprints(storage, layout)
        scope_analysis = analyze_outline_change_scope(outline, cached_chapter_fps)
        cached_fp = request.cached_outline_fingerprint or _read_cached_fingerprint(storage, layout)
        legacy_fingerprint_matches = (
            not cached_chapter_fps
            and bool(cached_fp)
            and compute_outline_fingerprint(outline) == cached_fp
        )
        if explicit:
            affected_set = explicit
        elif legacy_fingerprint_matches:
            affected_set = set()
        else:
            if bool(scope_analysis.get("requires_manual_scope")):
                duration = __import__("time").monotonic() - started_at
                result = {
                    "project_id": request.project_id,
                    "status": "requires_manual_scope",
                    "requires_manual_scope": True,
                    "manual_scope_reason": str(scope_analysis.get("reason") or ""),
                    "affected": [],
                    "cascade": [],
                    "focus": [],
                    "refreshed": 0,
                    "milestones_rebuilt": 0,
                    "stale_marked": 0,
                    "duration_s": round(duration, 2),
                    "scope_analysis": {
                        "added": scope_analysis.get("added") or [],
                        "deleted": scope_analysis.get("deleted") or [],
                        "changed": scope_analysis.get("changed") or [],
                    },
                }
                if on_step_progress:
                    on_step_progress("sync_scope_required", result)
                    on_step_progress("sync_done", {"summary": result})
                return ExecutionResult(project_id=request.project_id, result=result)
            affected_set = {
                int(n) for n in (scope_analysis.get("affected") or []) if int(n or 0) > 0
            }

        # 3. Compute cascade.
        cascade_set: set[int] = set()
        if request.cascade_downstream and affected_set:
            cascade_set = compute_cascade(
                affected_set,
                chapter_contracts,
                max_depth=request.max_cascade_depth,
            )
        focus_set = affected_set | cascade_set
        focus_list = sorted(focus_set)

        if on_step_progress:
            on_step_progress(
                "sync_start",
                {
                    "affected": sorted(affected_set),
                    "cascade": sorted(cascade_set),
                    "focus": focus_list,
                    "total_chapters": len(focus_set),
                },
            )

        if not focus_list:
            duration = __import__("time").monotonic() - started_at
            if on_step_progress:
                on_step_progress(
                    "sync_done",
                    {
                        "summary": {
                            "refreshed": 0,
                            "milestones_rebuilt": 0,
                            "stale_marked": 0,
                            "project_id": request.project_id,
                            "status": "noop",
                            "duration_s": round(duration, 2),
                        },
                        "reason": "no_focus_chapters",
                    },
                )
            return ExecutionResult(
                project_id=request.project_id,
                result={
                    "project_id": request.project_id,
                    "status": "noop",
                    "refreshed": 0,
                    "milestones_rebuilt": 0,
                    "stale_marked": 0,
                    "affected": [],
                    "cascade": [],
                    "duration_s": round(duration, 2),
                },
            )

        # 4. LLM re-extract.
        service_ctx = getattr(runtime, "service_ctx", None)
        if service_ctx is None and all(
            hasattr(runtime, name) for name in ("router", "builder", "settings")
        ):
            from types import SimpleNamespace

            from novel_forge.pipeline.long.services.generation.llm_service import LLMService

            service = LLMService(
                router=runtime.router,
                builder=runtime.builder,
                settings=runtime.settings,
                on_step=on_step_progress or (lambda _step, _payload: None),
            )
            service_ctx = SimpleNamespace(
                router=runtime.router,
                settings=runtime.settings,
                call_with_retry=service.call_with_retry,
            )
        if service_ctx is None:
            return ExecutionResult(
                project_id=request.project_id,
                result={
                    "project_id": request.project_id,
                    "status": "failed",
                    "error": "runtime.service_ctx not available",
                    "refreshed": 0,
                    "milestones_rebuilt": 0,
                    "stale_marked": 0,
                },
            )

        chapters_for_llm = [
            ch
            for ch in sorted(outline.chapters, key=lambda c: int(c.chapter_number))
            if int(ch.chapter_number) in focus_set
        ]
        if not chapters_for_llm:
            return ExecutionResult(
                project_id=request.project_id,
                result={
                    "project_id": request.project_id,
                    "status": "failed",
                    "error": "focus chapters not present in outline",
                    "focus": focus_list,
                    "refreshed": 0,
                },
            )

        try:
            if on_step_progress:
                on_step_progress(
                    "sync_chapter_loading",
                    {"focus": focus_list, "count": len(chapters_for_llm)},
                )
            payload = _chapter_contract_repair_outline_payload(
                outline,
                chapters_for_llm,
                settings=service_ctx.settings,
            )
            max_tokens = calculate_route_aware_max_tokens(
                service_ctx.router,
                TaskType.PLAN_CHAPTER_CONTRACTS,
                max(2400, len(chapters_for_llm) * 850),
                prompt_overhead=3200,
                min_tokens=4096,
            )
            if on_step_progress:
                on_step_progress(
                    "sync_chapter_llm",
                    {"chapters": focus_list, "attempt": 1},
                )
            reextracted = await service_ctx.call_with_retry(
                TaskType.PLAN_CHAPTER_CONTRACTS,
                {
                    "narrative_contract": narrative_contract,
                    "outline": payload,
                },
                max_tokens=max_tokens,
                temperature=getattr(service_ctx.settings, "temp_plan_chapter_contracts", 0.25),
                required_keys=("chapter_contracts",),
                max_retries=3,
            )
        except Exception as exc:  # noqa: BLE001
            _log.error(
                "execute_sync_chapter_contracts: LLM call failed: %s",
                exc,
                exc_info=True,
            )
            if on_step_progress:
                on_step_progress(
                    "sync_llm_error",
                    {"error": str(exc), "focus": focus_list},
                )
            return ExecutionResult(
                project_id=request.project_id,
                result={
                    "project_id": request.project_id,
                    "status": "failed",
                    "error": f"llm_call_failed: {exc}",
                    "focus": focus_list,
                    "refreshed": 0,
                },
            )

        if strict:
            returned_numbers = {
                int(item.get("chapter_number", 0) or 0)
                for item in reextracted.get("chapter_contracts", [])
                if isinstance(item, dict)
            }
            if not focus_set.issubset(returned_numbers):
                raise ValueError("Candidate synchronization omitted chapter contracts")

        # 5. Merge.
        merged = merge_reextracted_contracts(
            chapter_contracts,
            reextracted,
            focus_set,
        )

        # 6. Coverage fallback.
        normalized, coverage = ensure_chapter_contract_coverage(
            merged,
            outline,
            backfill_missing=not strict,
            accept_local_fallback=not strict,
            settings=service_ctx.settings,
        )
        if strict and (
            not coverage.get("complete")
            or focus_set.intersection(coverage.get("backfilled_chapters", []))
        ):
            raise ValueError("Candidate synchronization requires verified contract coverage")
        normalized = restore_nonfocus_contract_items(
            normalized,
            chapter_contracts,
            focus_set,
        )

        # Normalize cognitive_subjects against entity catalog — filters corrupted LLM tokens.
        from novel_forge.pipeline.long.services.context.source_artifacts import (
            build_init_entity_catalog,
        )
        from novel_forge.pipeline.long.services.init.init_service import (
            _normalize_chapter_contracts_cognitive_subjects,
        )

        _entity_graph_data = _safe_load_json(
            storage,
            layout.narrative_state_dir / "entity_graph.json",
        )
        _character_bible_data = _safe_load_json(
            storage,
            layout.characters_path,
        )
        _entity_catalog = (
            build_init_entity_catalog(
                _entity_graph_data,
                _character_bible_data,
                chapter_contracts=normalized,
            )
            if _entity_graph_data or _character_bible_data
            else None
        )
        _normalize_chapter_contracts_cognitive_subjects(
            normalized,
            entity_catalog=_entity_catalog,
        )

        # Persist chapter_contracts.json.
        if on_step_progress:
            on_step_progress(
                "sync_chapter_saving",
                {"focus": focus_list, "path": "plans/chapter_contracts.json"},
            )
        contracts_path = layout.plans_dir / "chapter_contracts.json"
        try:
            previous_contracts_hash = (
                hashlib.sha256(contracts_path.read_bytes()).hexdigest()
                if contracts_path.exists()
                else ""
            )
        except OSError:
            previous_contracts_hash = ""
        normalized["sync_metadata"] = {
            **normalized.get("sync_metadata", {}),
            "session_id": request.sync_session_id or _generate_sync_session_id(),
            "synced_at": _utc_now_iso(),
            "outline_fingerprint": compute_outline_fingerprint(outline),
            "outline_chapter_fingerprints": compute_outline_chapter_fingerprints(outline),
            "outline_chapter_snapshots": compute_outline_chapter_snapshots(outline),
            "affected_chapter_numbers": sorted(affected_set),
            "cascade_chapter_numbers": sorted(cascade_set),
            "focus_chapter_numbers": focus_list,
            "prose_untouched": bool(request.prose_untouched),
        }
        storage.save_json(contracts_path, normalized)
        try:
            from novel_forge.persistence.project_staleness import (
                RevisionScope,
                UpstreamArtifactKind,
                record_upstream_artifact_revision,
            )

            record_upstream_artifact_revision(
                storage,
                layout,
                artifact_kind=UpstreamArtifactKind.CHAPTER_CONTRACTS,
                previous_hash=previous_contracts_hash,
                scope=RevisionScope.FORWARD_ONLY,
                from_chapter=min(focus_list),
                reason="chapter_contracts_sync",
            )
        except Exception as exc:  # noqa: BLE001 - stale marker should not abort sync
            if strict:
                raise
            _log.warning(
                "execute_sync_chapter_contracts: revision record failed: %s",
                exc,
                exc_info=True,
            )
        try:
            from novel_forge.pipeline.long.services.context.source_artifacts import (
                persist_init_source_artifacts,
            )

            persist_init_source_artifacts(
                storage=storage,
                layout=layout,
                project_id=request.project_id,
                spec=_safe_load_json(storage, layout.spec_path) or {},
                story_bible=_safe_load_json(storage, layout.bible_path) or {},
                character_bible=_safe_load_json(storage, layout.characters_path) or {},
                character_system=(
                    _safe_load_json(
                        storage,
                        layout.states_dir / "init_v2" / "character_system.json",
                    )
                    or {}
                ),
                entity_graph=(
                    _safe_load_json(storage, layout.narrative_state_dir / "entity_graph.json") or {}
                ),
                style_profile=_safe_load_json(storage, layout.style_profile_path),
                creative_packet=(
                    _safe_load_json(
                        storage,
                        layout.plans_dir / "creative_director_packet.json",
                    )
                    or {}
                ),
                blueprint=_safe_load_json(storage, layout.blueprint_path) or {},
                outline=outline,
                narrative_contract=narrative_contract,
                chapter_contracts=normalized,
                readiness_report=(
                    _safe_load_json(storage, layout.reports_dir / "init_readiness.json") or {}
                ),
            )
            if on_step_progress:
                on_step_progress("sync_source_artifacts_refreshed", {})
        except Exception as exc:  # noqa: BLE001 - source refresh should not abort sync
            if strict:
                raise
            _log.warning(
                "execute_sync_chapter_contracts: source artifact refresh failed: %s",
                exc,
                exc_info=True,
            )

        refreshed_count = len(
            [
                n
                for n in focus_list
                if n
                in {
                    int(item.get("chapter_number", 0) or 0)
                    for item in (reextracted.get("chapter_contracts", []) or [])
                    if isinstance(item, dict)
                }
            ]
        )

        # 7. Rebuild plot_milestone_index.
        milestones_rebuilt = 0
        old_hash = _read_milestone_source_hash(storage, layout)
        if request.rebuild_milestones:
            try:
                if on_step_progress:
                    on_step_progress(
                        "sync_milestone_rebuilding",
                        {"old_hash": old_hash},
                    )
                index = build_plot_milestone_index(
                    outline=outline,
                    chapter_contracts=normalized,
                    narrative_contract=narrative_contract,
                    project_id=request.project_id,
                )
                if index.source_hash != old_hash:
                    storage.save_json(
                        layout.plot_milestone_index_path,
                        index.model_dump(mode="json"),
                    )
                    milestones_rebuilt = len(index.milestones)
            except Exception as exc:  # noqa: BLE001
                if strict:
                    raise
                _log.warning(
                    "execute_sync_chapter_contracts: milestone rebuild failed: %s",
                    exc,
                    exc_info=True,
                )

        # 8. Mark stale.
        stale_marked = 0
        if request.mark_stale:
            stale_paths = collect_downstream_stale_paths(
                layout,
                affected_set,
                cascade_set,
            )
            reason = (
                f"chapter_contracts_sync: focus={focus_list} "
                f"session={normalized['sync_metadata']['session_id']}"
            )
            if on_step_progress:
                on_step_progress(
                    "sync_marking_stale",
                    {"count": len(stale_paths)},
                )
            for path in stale_paths:
                if mark_artifact_stale(storage, path, reason):
                    stale_marked += 1

        duration = __import__("time").monotonic() - started_at
        summary = {
            "project_id": request.project_id,
            "status": "completed",
            "refreshed": refreshed_count,
            "milestones_rebuilt": milestones_rebuilt,
            "stale_marked": stale_marked,
            "affected": sorted(affected_set),
            "cascade": sorted(cascade_set),
            "focus": focus_list,
            "session_id": normalized["sync_metadata"]["session_id"],
            "outline_fingerprint": normalized["sync_metadata"]["outline_fingerprint"],
            "duration_s": round(duration, 2),
        }
        if on_step_progress:
            on_step_progress("sync_done", {"summary": summary})

        return ExecutionResult(
            project_id=request.project_id,
            result=summary,
        )


def _safe_load_json(storage: Any, path: Any) -> dict[str, Any] | None:
    """Best-effort JSON loader that returns None on any failure."""
    from pathlib import Path

    path = Path(path)
    if not path.exists():
        return None
    try:
        data = storage.load_json(path)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _read_cached_fingerprint(storage: Any, layout: Any) -> str:
    """Read the most recent outline fingerprint from chapter_contracts sync_metadata."""
    data = _safe_load_json(storage, layout.plans_dir / "chapter_contracts.json")
    if not data:
        return ""
    metadata = data.get("sync_metadata")
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("outline_fingerprint") or "")


def _read_cached_chapter_fingerprints(storage: Any, layout: Any) -> dict[str, str]:
    """Read per-chapter outline fingerprints from chapter_contracts sync_metadata."""
    data = _safe_load_json(storage, layout.plans_dir / "chapter_contracts.json")
    if not data:
        return {}
    metadata = data.get("sync_metadata")
    if not isinstance(metadata, dict):
        return {}
    raw = metadata.get("outline_chapter_fingerprints")
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in raw.items()
        if str(key).strip() and str(value).strip()
    }


def _read_milestone_source_hash(storage: Any, layout: Any) -> str:
    data = _safe_load_json(storage, layout.plot_milestone_index_path)
    if not data:
        return ""
    return str(data.get("source_hash") or "")


def _generate_sync_session_id() -> str:
    import uuid

    return f"sync-{uuid.uuid4().hex[:12]}"


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()
