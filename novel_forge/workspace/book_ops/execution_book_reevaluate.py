"""Re-run review artifacts for an existing chapter text.

This refreshes report files against the current chapter body without editing
the prose itself. When a chapter is paused at ``guard_checkpoint``, the helper
also resyncs the pending checkpoint/session snapshot so the UI and later
finalization use the same text/report version.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

from novel_forge.common.utils import normalize_gender_value
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.steps.evaluate_step import EvaluateStep
from novel_forge.workspace.book_ops.execution_book_common import _log
from novel_forge.workspace.chapter_run_io import ChapterRunIOContext
from novel_forge.workspace.contracts import ReevaluateChapterRequest
from novel_forge.workspace.execution_result import ExecutionResult, StepCallback
from novel_forge.workspace.helpers.execution_helpers import _load_chapter_source_slice_if_available
from novel_forge.workspace.helpers.execution_io import _ChapterDataCache
from novel_forge.workspace.helpers.execution_runners import _project_lock
from novel_forge.workspace.helpers.execution_state import _source_text_hash
from novel_forge.workspace.runtime import RuntimeServices
from novel_forge.workspace.sessions.chapter_session_handlers import _open_issue_count


async def execute_reevaluate_chapter(
    runtime: RuntimeServices,
    request: ReevaluateChapterRequest,
    *,
    on_step_progress: StepCallback = None,
) -> ExecutionResult[dict[str, Any]]:
    """Re-run review artifacts for an existing chapter text.

    This operation never edits chapter text and never enters any repair path.
    It refreshes reports on disk so UI warnings can be resolved safely, and
    resyncs guard-checkpoint snapshots when they still reference an older text
    version.
    """
    from novel_forge.workspace.authoring_control import authoring_operation

    with authoring_operation(runtime, request, "repair"):
        return await _execute_reevaluate_chapter(
            runtime, request, on_step_progress=on_step_progress
        )


async def _execute_reevaluate_chapter(
    runtime: RuntimeServices,
    request: ReevaluateChapterRequest,
    *,
    on_step_progress: StepCallback = None,
) -> ExecutionResult[dict[str, Any]]:
    from novel_forge.obs.tracer import PipelineTrace

    result: dict[str, Any] = {
        "chapter_number": request.chapter_number,
        "overall_score": None,
        "alignment_score": None,
        "continuity_score": None,
        "continuity_issue_count": 0,
        "causal_score": None,
        "causal_issue_count": 0,
        "reading_power_score": None,
        "reading_power_is_fallback": None,
        "warnings": [],
    }

    async with _project_lock(runtime, request.project_id):
        layout = ProjectLayout(runtime.storage.existing_project_dir(request.project_id))
        chapter_num = request.chapter_number
        io_context = ChapterRunIOContext(
            storage=runtime.storage,
            layout=layout,
            project_id=request.project_id,
            chapter_number=chapter_num,
            source="reevaluate_chapter",
        )
        cache = _ChapterDataCache(runtime.storage, layout, artifact_loader=io_context)
        chapter_source_slice = _load_chapter_source_slice_if_available(
            runtime.storage,
            layout,
            project_id=request.project_id,
            chapter_number=chapter_num,
        )
        chapter_path = layout.chapter_path(chapter_num)
        review_draft_path = layout.chapter_review_draft_path(chapter_num)
        if chapter_path.exists():
            chapter_text = chapter_path.read_text(encoding="utf-8")
        elif review_draft_path.exists():
            chapter_text = review_draft_path.read_text(encoding="utf-8")
        else:
            raise ValueError(f"第 {chapter_num} 章尚无可用正文，无法重新评估。")

        text_hash = _source_text_hash(chapter_text)
        if on_step_progress:
            on_step_progress("reevaluate_start", {"chapter_number": chapter_num})

        spec_raw = io_context.load_json(layout.spec_path) if layout.spec_path.exists() else {}
        tone = str(spec_raw.get("tone", "") or "")
        genre = str(spec_raw.get("genre", "") or "")
        eval_ctx: dict[str, Any] = {}
        if tone:
            eval_ctx["tone"] = tone
        if genre:
            eval_ctx["genre"] = genre
        # 注入风格参数，使评估器能根据风格类型动态调整评分标准
        eval_ctx["style"] = None
        eval_ctx["style_profile"] = cache.get_style_profile()

        # ── Concurrent re-evaluation ──────────────────────────────────────
        # Quality scoring remains separate; the expensive review reports go
        # through ReviewReportService so freshness/reuse rules stay centralized.

        async def _reeval_quality() -> None:
            """1) Quality re-evaluation."""
            try:
                evaluate_step = EvaluateStep(
                    runtime.router,
                    runtime.builder,
                    settings=runtime.settings,
                    trace=PipelineTrace(),
                    extra_context=eval_ctx,
                )
                eval_report = await evaluate_step.run(chapter_text)
                eval_payload = eval_report.model_dump(mode="json")
                eval_payload["source_text_hash"] = text_hash
                runtime.storage.save_json(layout.eval_report_path(chapter_num), eval_payload)
                result["overall_score"] = float(getattr(eval_report, "overall_score", 0.0))
                if on_step_progress:
                    on_step_progress("evaluate", eval_payload)
            except Exception as exc:
                warning = "章节重评估：质量评估失败，已保留旧版评分报告。"
                result["warnings"].append(warning)
                if on_step_progress:
                    on_step_progress(
                        "evaluate_warning",
                        {
                            "project_id": request.project_id,
                            "chapter_number": chapter_num,
                            "message": warning,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        },
                    )

        async def _reeval_review_reports() -> None:
            """Refresh/reuse alignment, continuity, causal, and reading-power reports."""
            try:
                from novel_forge.workspace.review_report_refresh import (
                    refresh_workspace_review_reports,
                )

                refreshed = await refresh_workspace_review_reports(
                    runtime=runtime,
                    layout=layout,
                    project_id=request.project_id,
                    chapter_number=chapter_num,
                    current_text=chapter_text,
                    io_context=io_context,
                    trace=PipelineTrace(),
                    chapter_source_slice=chapter_source_slice,
                    style_profile=cache.get_style_profile(),
                    genre=genre,
                    stale_reason="reevaluate_chapter",
                    report_kinds=(
                        "alignment",
                        "continuity",
                        "causal",
                        "reading_power",
                    ),
                    causal_recheck_mode=True,
                    missing_message_template=(
                        "章节重评估：缺少审查报告刷新所需工件，已跳过对齐/连贯性/"
                        "因果/追读力重评估。（缺失：{missing}）"
                    ),
                    warning_sink=result["warnings"],
                    on_step_progress=on_step_progress,
                )
                if refreshed is None:
                    return

                result["alignment_score"] = float(
                    getattr(refreshed.alignment_report, "alignment_score", 0.0)
                )
                result["continuity_score"] = float(
                    getattr(refreshed.continuity_report, "continuity_score", 0.0)
                )
                result["continuity_issue_count"] = _open_issue_count(
                    getattr(refreshed.continuity_report, "issues", []) or []
                )
                if refreshed.causal_report is not None:
                    result["causal_score"] = float(
                        getattr(refreshed.causal_report, "causal_score", 0.0)
                    )
                    result["causal_issue_count"] = len(
                        getattr(refreshed.causal_report, "issues", []) or []
                    )
                if refreshed.reading_power_report is not None:
                    result["reading_power_score"] = float(
                        getattr(refreshed.reading_power_report, "overall_score", 0.0)
                    )
                    result["reading_power_is_fallback"] = bool(
                        getattr(refreshed.reading_power_report, "is_fallback", False)
                    )
            except Exception as exc:
                warning = "章节重评估：审查报告刷新失败，已保留旧版审查报告。"
                result["warnings"].append(warning)
                _log.warning(
                    "reevaluate_review_reports_failed | chapter=%d | error=%s",
                    chapter_num,
                    exc,
                    exc_info=True,
                )
                if on_step_progress:
                    on_step_progress(
                        "quality_reports_refresh_warning",
                        {
                            "project_id": request.project_id,
                            "chapter_number": chapter_num,
                            "message": warning,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        },
                    )

        _reeval_results = await asyncio.gather(
            _reeval_quality(),
            _reeval_review_reports(),
            return_exceptions=True,
        )
        for _ridx, _rname in enumerate(["quality", "review_reports"]):
            _rval = _reeval_results[_ridx]
            if isinstance(_rval, BaseException):
                _log.warning(
                    "reevaluate_task_failed | chapter=%d | task=%s | error=%s",
                    chapter_num,
                    _rname,
                    _rval,
                )

        # 5) Refresh guard-checkpoint snapshot ────────────────────────────
        # If the user manually edited the chapter and then clicked "重新评估",
        # the checkpoint/session files must also be updated; otherwise later
        # finalization may still read stale pending text/outcome and overwrite
        # the user's edits.
        checkpoint_path = layout.chapter_checkpoint_path(chapter_num)
        session_path = layout.chapter_session_path(chapter_num)
        if runtime.storage.exists(checkpoint_path) and runtime.storage.exists(session_path):
            try:
                from novel_forge.core.schemas.chapter import (
                    AlignmentReport,
                    CausalValidationReport,
                    ChapterOutcome,
                )
                from novel_forge.core.schemas.continuity import (
                    ChapterBridge,
                    ChapterPlan,
                    ChapterStatePacket,
                    ContinuityReport,
                )
                from novel_forge.core.schemas.eval_schema import EvalReport
                from novel_forge.pipeline.long.stages.finalize_report import extract_and_validate
                from novel_forge.pipeline.steps.extract_step import (
                    ExtractCanonDeltaStep,
                    ExtractInput,
                )
                from novel_forge.story_kernel.rules import StoryKernelConsistencyRules
                from novel_forge.story_kernel.store import StoryKernelStore
                from novel_forge.workspace.contracts import DecisionCheckpoint
                from novel_forge.workspace.sessions.chapter_session_state import (
                    GuardCheckpointSessionState,
                    build_guard_checkpoint,
                    deserialize_pending_result,
                    save_guard_session_state,
                )

                _checkpoint = io_context.load_model(checkpoint_path, DecisionCheckpoint)
                _session = io_context.load_model(session_path, GuardCheckpointSessionState)
                if (
                    _checkpoint.checkpoint_type == "guard_checkpoint"
                    and _session.stage == "guard_checkpoint"
                ):
                    pending = deserialize_pending_result(_session.pending_result)
                    alignment_report = pending.alignment_report
                    if runtime.storage.exists(layout.alignment_report_path(chapter_num)):
                        alignment_report = io_context.load_model(
                            layout.alignment_report_path(chapter_num),
                            AlignmentReport,
                        )
                    eval_report = pending.eval_report
                    if runtime.storage.exists(layout.eval_report_path(chapter_num)):
                        eval_report = io_context.load_model(
                            layout.eval_report_path(chapter_num), EvalReport
                        )
                    continuity_report = pending.continuity_report
                    if runtime.storage.exists(layout.continuity_report_path(chapter_num)):
                        continuity_report = io_context.load_model(
                            layout.continuity_report_path(chapter_num),
                            ContinuityReport,
                        )
                    causal_report = pending.causal_report
                    if runtime.storage.exists(layout.chapter_causal_report_path(chapter_num)):
                        causal_report = io_context.load_model(
                            layout.chapter_causal_report_path(chapter_num),
                            CausalValidationReport,
                        )

                    refreshed_outcome = pending.outcome
                    stale_pending_text = (
                        _source_text_hash(pending.current_text) != text_hash
                        or pending.current_text != chapter_text
                    )
                    _packet = io_context.load_model(
                        layout.chapter_state_packet_path(chapter_num),
                        ChapterStatePacket,
                    )
                    _bridge = io_context.load_model(
                        layout.chapter_bridge_path(chapter_num),
                        ChapterBridge,
                    )
                    _plan = io_context.load_model(
                        layout.chapter_plan_path(chapter_num),
                        ChapterPlan,
                    )
                    _sk_store = StoryKernelStore(layout.story_kernel_db_path)
                    try:
                        _loaded_canon = await _sk_store.load_kernel(request.project_id)
                    except ValueError:
                        _loaded_canon = None
                    _bundle_stub = SimpleNamespace(
                        layout=layout,
                        chapter_outline=_packet.chapter_outline,
                        canon_state=_loaded_canon,
                    )

                    if _bundle_stub.canon_state is not None:
                        _extract_runner = SimpleNamespace(
                            _storage=runtime.storage,
                            _router=runtime.router,
                            _builder=runtime.builder,
                            _settings=runtime.settings,
                            _rules=StoryKernelConsistencyRules(),
                            _on_step=lambda *_args, **_kwargs: None,
                        )
                        try:
                            refreshed_outcome = await extract_and_validate(
                                _extract_runner,
                                _bundle_stub,
                                _packet,
                                _bridge,
                                _plan,
                                chapter_text,
                                chapter_num,
                                PipelineTrace(),
                                continuity_report,
                                repair_exhausted=True,
                            )
                        except Exception as exc:
                            _log.warning(
                                "reevaluate_guard_extract_failed | chapter=%d | error=%s",
                                chapter_num,
                                exc,
                            )
                            _extract_step = ExtractCanonDeltaStep(
                                runtime.router,
                                runtime.builder,
                                settings=runtime.settings,
                                trace=PipelineTrace(),
                            )
                            refreshed_outcome = ChapterOutcome.model_validate(
                                (
                                    await _extract_step.run(
                                        ExtractInput(
                                            chapter_number=chapter_num,
                                            chapter_text=chapter_text,
                                            known_characters=_packet.known_characters,
                                            chapter_outline_summary=(
                                                f"{_packet.chapter_outline.title} - "
                                                f"{_packet.chapter_outline.goal}"
                                            ),
                                            authoritative_character_genders={
                                                str(
                                                    profile.get("name", "")
                                                ).strip(): normalize_gender_value(
                                                    profile.get("gender", "")
                                                )
                                                for profile in _packet.character_profiles
                                                if isinstance(profile, dict)
                                                and str(profile.get("name", "")).strip()
                                                and normalize_gender_value(
                                                    profile.get("gender", "")
                                                )
                                            }
                                            or None,
                                            chapter_source_slice=chapter_source_slice,
                                        )
                                    )
                                ).model_dump(mode="json")
                            )
                            result["warnings"].append(
                                "章节重评估：剧情状态已按当前正文重提取，但一致性校验未完成，归档前建议人工复核。"
                            )
                    elif stale_pending_text:
                        result["warnings"].append(
                            "章节重评估：已刷新审核报告，但缺少 canon 状态，无法同步剧情状态快照。"
                        )

                    pending = replace(
                        pending,
                        current_text=chapter_text,
                        outcome=refreshed_outcome,
                        alignment_report=alignment_report,
                        causal_report=causal_report,
                        continuity_report=continuity_report,
                        eval_report=eval_report,
                    )
                    if refreshed_outcome.creative_report is not None:
                        runtime.storage.save_json(
                            layout.creative_report_path(chapter_num),
                            refreshed_outcome.creative_report.model_dump(mode="json"),
                        )
                    if refreshed_outcome.chapter_exit_state is not None:
                        runtime.storage.save_json(
                            layout.chapter_exit_state_path(chapter_num),
                            refreshed_outcome.chapter_exit_state.model_dump(mode="json"),
                        )
                    _bundle_ref = cast(Any, SimpleNamespace(layout=layout))
                    _checkpoint_refreshed = build_guard_checkpoint(
                        _bundle_ref,
                        chapter_num,
                        current_text=chapter_text,
                        alignment_report=alignment_report,
                        continuity_report=continuity_report,
                        eval_report=eval_report,
                        causal_report=causal_report,
                        guard_decision=pending.guard_decision,
                        reading_power_report=pending.reading_power_report,
                        warnings=tuple(pending.warnings or ()),
                        repair_tickets=tuple(pending.repair_tickets or ()),
                    ).model_copy(update={"checkpoint_id": _checkpoint.checkpoint_id})
                    runtime.storage.save_json(
                        checkpoint_path,
                        _checkpoint_refreshed.model_dump(mode="json"),
                    )
                    save_guard_session_state(
                        storage=runtime.storage,
                        bundle=_bundle_ref,
                        chapter_number=chapter_num,
                        checkpoint_id=_checkpoint.checkpoint_id,
                        project_id=_session.project_id,
                        canon_watermark=int(_session.canon_watermark or 0),
                        notes=_session.notes,
                        rewrite_strategy=getattr(_session, "rewrite_strategy", "auto"),
                        replan_history=list(getattr(_session, "replan_history", []) or []),
                        pending_state=pending,
                    )

                    # ── Sync guard_report hash to prevent "outdated text" warnings ──
                    # The guard_report is created once during guard_checkpoint and never
                    # updated during re-evaluation. Sync its source_text_hash to match
                    # the current text to prevent UI warnings.
                    try:
                        from novel_forge.workspace.helpers.execution_state import (
                            _stamp_source_text_hash,
                        )

                        guard_report_path = layout.guard_report_path(chapter_num)
                        if runtime.storage.exists(guard_report_path):
                            _stamp_source_text_hash(
                                storage=runtime.storage,
                                path=guard_report_path,
                                text_hash=text_hash,
                            )
                            _log.debug(
                                "Synced guard_report hash after re-evaluation | chapter=%d",
                                chapter_num,
                            )
                    except Exception as _guard_sync_exc:
                        _log.warning(
                            "Failed to sync guard_report hash after re-evaluation: %s",
                            _guard_sync_exc,
                        )
            except Exception as exc:
                _log.warning(
                    "reevaluate_checkpoint_resync_failed | chapter=%d | error=%s",
                    chapter_num,
                    exc,
                )
                result["warnings"].append(
                    "章节重评估：报告已刷新，但检查点摘要未完全同步，建议刷新界面后再归档。"
                )

        if on_step_progress:
            on_step_progress("reevaluate_chapter", dict(result))

    return ExecutionResult(project_id=request.project_id, result=result)
