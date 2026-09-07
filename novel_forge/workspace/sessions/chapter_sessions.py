"""Interactive chapter-studio sessions built on top of the long pipeline."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from novel_forge.core.exceptions import ChapterSessionStaleError
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import (
    canon_watermark,
    is_checkpoint_session_fresh_for_current_chain,
    scoped_stale_chapters,
)
from novel_forge.pipeline.long.preflight import (
    LongProjectBundle,
    close_long_project,
    prepare_long_project,
)
from novel_forge.workspace.contracts import (
    ChapterSessionResult,
    PrepareChapterRequest,
    PrepareChapterResponse,
    ResolveChapterCheckpointRequest,
)
from novel_forge.workspace.runtime import RuntimeServices
from novel_forge.workspace.sessions.chapter_session_handlers import (
    prepare_plan_checkpoint,
    resolve_guard_checkpoint,
    resolve_plan_checkpoint,
)
from novel_forge.workspace.sessions.chapter_session_state import (
    GuardCheckpointSessionState,
    PlanCheckpointSessionState,
    load_checkpoint,
    load_session_state,
)


def _bundle_canon_watermark(runtime: RuntimeServices, bundle: Any) -> int:
    canon_state = getattr(bundle, "canon_state", None)
    raw_value = getattr(canon_state, "current_chapter", None)
    try:
        if raw_value is not None:
            return max(0, int(raw_value))
    except (TypeError, ValueError):
        pass
    layout = getattr(bundle, "layout", None)
    if isinstance(layout, ProjectLayout):
        return canon_watermark(runtime.storage, layout)
    return 0


def _assert_session_matches_current_chain(
    runtime: RuntimeServices,
    request: ResolveChapterCheckpointRequest,
    *,
    bundle: Any,
    session_state: Any,
) -> None:
    if session_state.project_id != request.project_id:
        raise ChapterSessionStaleError(
            message="章节工作台 session 项目不匹配，请重新展开本章。",
            kind="project_mismatch",
            context={
                "session_project_id": str(session_state.project_id or ""),
                "request_project_id": str(request.project_id or ""),
            },
        )
    if session_state.chapter_number != request.chapter_number:
        raise ChapterSessionStaleError(
            message="章节工作台 session 章节号不匹配，请重新展开本章。",
            kind="chapter_mismatch",
            context={
                "session_chapter_number": int(getattr(session_state, "chapter_number", 0) or 0),
                "request_chapter_number": int(request.chapter_number or 0),
            },
        )

    session_watermark = session_state.canon_watermark
    current_watermark = _bundle_canon_watermark(runtime, bundle)
    if session_watermark is None:
        raise ChapterSessionStaleError(
            message="章节工作台 checkpoint 缺少上游水位，请重新展开本章。",
            kind="watermark_missing",
        )
    try:
        session_watermark_int = max(0, int(session_watermark))
    except (TypeError, ValueError):
        raise ChapterSessionStaleError(
            message="章节工作台 checkpoint 上游水位无效，请重新展开本章。",
            kind="watermark_invalid",
            context={"session_watermark": str(session_watermark)},
        ) from None
    if session_watermark_int != current_watermark:
        raise ChapterSessionStaleError(
            message=(
                "章节工作台 checkpoint 已失效："
                f"创建时上游水位为第 {session_watermark_int} 章，"
                f"当前上游水位为第 {current_watermark} 章。请重新展开本章。"
            ),
            kind="canon_watermark_drift",
            context={
                "session_watermark": session_watermark_int,
                "current_watermark": current_watermark,
            },
        )


def _guard_redirect_option_id(
    checkpoint: Any,
    *,
    repair_control_mode: Any,
) -> str:
    """Choose the current guard action after a plan→guard checkpoint race.

    A stale ``write_now`` request means the background write already reached
    the archive checkpoint.  Manual sessions should remain paused so the user
    can inspect that new decision, while AI-auto sessions should continue with
    the guard checkpoint's actual recommendation.  Older behavior always
    redirected to ``pause_for_human`` and also dropped ``repair_control_mode``,
    which turned a normal checkpoint race into a deterministic autorun stop.
    """
    options = list(getattr(checkpoint, "options", None) or [])
    option_by_id = {
        str(getattr(option, "option_id", "") or ""): option
        for option in options
        if str(getattr(option, "option_id", "") or "")
    }
    mode_value = (
        str(getattr(repair_control_mode, "value", repair_control_mode) or "").strip().lower()
    )
    recommended = next(
        (
            str(getattr(option, "option_id", "") or "")
            for option in options
            if bool(getattr(option, "is_recommended", False))
        ),
        "",
    )

    if mode_value == "ai_auto":
        if recommended and recommended != "pause_for_human":
            return recommended
        metadata = getattr(checkpoint, "metadata", None) or {}
        if (
            isinstance(metadata, dict)
            and metadata.get("archive_quality_proceed_with_repair")
            and not metadata.get("archive_quality_auto_repair_exhausted")
            and "apply_repairs_and_finalize" in option_by_id
        ):
            return "apply_repairs_and_finalize"

    if "pause_for_human" in option_by_id:
        return "pause_for_human"
    if recommended:
        return recommended
    if options:
        return str(getattr(options[0], "option_id", "") or "")
    # Legacy guard checkpoints could be persisted without their options list.
    # Preserve the old recovery behavior instead of turning those resumable
    # sessions into a new checkpoint-structure error.
    return "pause_for_human"


async def prepare_chapter_session(
    runtime: RuntimeServices,
    request: PrepareChapterRequest,
    *,
    on_step_progress: Callable[[str, Any], None] | None = None,
) -> PrepareChapterResponse:
    """Prepare a chapter to the planning checkpoint."""
    return await prepare_plan_checkpoint(
        runtime,
        request,
        on_step_progress=on_step_progress,
    )


async def resolve_chapter_session(
    runtime: RuntimeServices,
    request: ResolveChapterCheckpointRequest,
    *,
    on_step_progress: Callable[[str, Any], None] | None = None,
) -> ChapterSessionResult:
    """Resolve a chapter checkpoint and continue or finalize the chapter."""
    layout = ProjectLayout(runtime.storage.existing_project_dir(request.project_id))
    stale_chapters = scoped_stale_chapters(runtime.storage, layout)
    stale_cutoff = min(stale_chapters) if stale_chapters else None
    if stale_cutoff is not None:
        if request.chapter_number > stale_cutoff:
            raise ChapterSessionStaleError(
                message=(
                    f"第 {request.chapter_number} 章基于已失效的上游结果，"
                    f"请先重新生成第 {stale_cutoff} 章后再继续。"
                ),
                kind="stale_upstream",
                context={
                    "stale_cutoff": int(stale_cutoff),
                    "chapter_number": int(request.chapter_number),
                },
            )
        if (
            request.chapter_number in stale_chapters
            and not is_checkpoint_session_fresh_for_current_chain(
                runtime.storage,
                layout,
                request.chapter_number,
            )
        ):
            raise ChapterSessionStaleError(
                message=(
                    f"第 {request.chapter_number} 章基于已失效的上游结果，"
                    f"请先重新生成第 {stale_cutoff} 章后再继续。"
                ),
                kind="stale_upstream",
                context={
                    "stale_cutoff": int(stale_cutoff),
                    "chapter_number": int(request.chapter_number),
                },
            )

    bundle = await prepare_long_project(
        storage=runtime.storage,
        project_id=request.project_id,
        chapter_number=request.chapter_number,
        force_regenerate=request.force,
    )
    try:
        return await _resolve_prepared_chapter_session(
            runtime, request, bundle, on_step_progress=on_step_progress
        )
    finally:
        await close_long_project(bundle)


async def _resolve_prepared_chapter_session(
    runtime: RuntimeServices,
    request: ResolveChapterCheckpointRequest,
    bundle: LongProjectBundle,
    *,
    on_step_progress: Callable[[str, Any], None] | None = None,
) -> ChapterSessionResult:
    checkpoint = load_checkpoint(bundle, request.chapter_number)
    session_state = load_session_state(bundle, request.chapter_number)
    _assert_session_matches_current_chain(
        runtime,
        request,
        bundle=bundle,
        session_state=session_state,
    )
    if checkpoint.checkpoint_id != request.checkpoint_id:
        # The background asyncio task cannot be interrupted. When the user
        # cancels during quality checks, the background continues running and
        # may advance the checkpoint from plan_checkpoint → guard_checkpoint.
        # If that happened, redirect to the guard decision instead of failing
        # with a confusing "checkpoint 已变化" error.
        if (
            checkpoint.checkpoint_type == "guard_checkpoint"
            and isinstance(session_state, GuardCheckpointSessionState)
            and request.option_id in {"write_now", "edit_plan_and_write"}
        ):
            redirect_option_id = _guard_redirect_option_id(
                checkpoint,
                repair_control_mode=request.repair_control_mode,
            )
            return await resolve_guard_checkpoint(
                runtime,
                ResolveChapterCheckpointRequest(
                    project_id=request.project_id,
                    chapter_number=request.chapter_number,
                    checkpoint_id=checkpoint.checkpoint_id,
                    option_id=redirect_option_id,
                    notes=request.notes,
                    force=request.force,
                    repair_control_mode=request.repair_control_mode,
                ),
                bundle=bundle,
                session_state=session_state,
                checkpoint=checkpoint,
                on_step_progress=on_step_progress,
            )
        # Soft-redirect: guard → guard with the same option still available.
        # When a prior resolve (e.g. apply_repairs_and_finalize) created a new
        # guard checkpoint (archive quality retry), the desktop autopilot may
        # submit with the stale checkpoint_id before the workspace snapshot
        # refreshes.  Instead of failing with a confusing error, transparently
        # redirect to the current checkpoint so the option is re-evaluated
        # against the latest reports and retry budget.
        if (
            checkpoint.checkpoint_type == "guard_checkpoint"
            and isinstance(session_state, GuardCheckpointSessionState)
            and request.option_id in {option.option_id for option in checkpoint.options}
        ):
            return await resolve_guard_checkpoint(
                runtime,
                ResolveChapterCheckpointRequest(
                    project_id=request.project_id,
                    chapter_number=request.chapter_number,
                    checkpoint_id=checkpoint.checkpoint_id,
                    option_id=request.option_id,
                    notes=request.notes,
                    force=request.force,
                    repair_control_mode=request.repair_control_mode,
                ),
                bundle=bundle,
                session_state=session_state,
                checkpoint=checkpoint,
                on_step_progress=on_step_progress,
            )
        raise ChapterSessionStaleError(
            message="章节工作台 checkpoint 已变化，请刷新后重试。",
            kind="checkpoint_id_drift",
            context={
                "request_checkpoint_id": str(request.checkpoint_id or ""),
                "current_checkpoint_id": str(checkpoint.checkpoint_id or ""),
                "current_checkpoint_type": str(checkpoint.checkpoint_type or ""),
            },
        )

    if checkpoint.checkpoint_type == "plan_checkpoint":
        if not isinstance(session_state, PlanCheckpointSessionState):
            raise ChapterSessionStaleError(
                message="章节工作台 session 状态与当前 checkpoint 不一致，请刷新后重试。",
                kind="session_type_mismatch",
                context={
                    "checkpoint_type": "plan_checkpoint",
                    "session_state_type": type(session_state).__name__,
                },
            )
        return await resolve_plan_checkpoint(
            runtime,
            request,
            bundle=bundle,
            session_state=session_state,
            on_step_progress=on_step_progress,
        )
    if checkpoint.checkpoint_type == "guard_checkpoint":
        if not isinstance(session_state, GuardCheckpointSessionState):
            raise ChapterSessionStaleError(
                message="章节工作台 session 状态与当前 checkpoint 不一致，请刷新后重试。",
                kind="session_type_mismatch",
                context={
                    "checkpoint_type": "guard_checkpoint",
                    "session_state_type": type(session_state).__name__,
                },
            )
        return await resolve_guard_checkpoint(
            runtime,
            request,
            bundle=bundle,
            session_state=session_state,
            checkpoint=checkpoint,
            on_step_progress=on_step_progress,
        )
    raise ChapterSessionStaleError(
        message=f"未知 checkpoint 类型：{checkpoint.checkpoint_type}",
        kind="unknown_checkpoint_type",
        context={"checkpoint_type": str(checkpoint.checkpoint_type or "")},
    )
