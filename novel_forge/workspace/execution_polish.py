"""Polish chapter execution helpers."""

from __future__ import annotations

from typing import Any

from novel_forge.core.schemas.outline import StoryOutline
from novel_forge.core.utils.text_revision_diff import build_text_revision_diff
from novel_forge.obs.logger import get_logger
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import (
    RevisionScope,
    invalidate_chapter_tts_artifacts,
    record_text_revision,
)
from novel_forge.pipeline.long.services.chapter_position import build_chapter_position
from novel_forge.workspace.chapter_run_io import ChapterRunIOContext
from novel_forge.workspace.contracts import PolishChapterRequest
from novel_forge.workspace.execution_result import ExecutionResult, StepCallback
from novel_forge.workspace.helpers.execution_helpers import _load_chapter_source_slice_if_available
from novel_forge.workspace.helpers.execution_io import _ChapterDataCache
from novel_forge.workspace.helpers.execution_runners import _project_lock
from novel_forge.workspace.helpers.execution_state import _source_text_hash
from novel_forge.workspace.runtime import RuntimeServices

_log = get_logger("workspace.execution_polish")


async def execute_polish_chapter(
    runtime: RuntimeServices,
    request: PolishChapterRequest,
    *,
    on_step_progress: StepCallback = None,
) -> ExecutionResult[Any]:
    """Polish a completed chapter for literary quality."""
    from novel_forge.workspace.authoring_control import authoring_operation

    with authoring_operation(runtime, request, "repair"):
        return await _execute_polish_chapter(runtime, request, on_step_progress=on_step_progress)


async def _execute_polish_chapter(
    runtime: RuntimeServices,
    request: PolishChapterRequest,
    *,
    on_step_progress: StepCallback = None,
) -> ExecutionResult[Any]:
    from novel_forge.obs.tracer import PipelineTrace
    from novel_forge.pipeline.steps.polish_step import PolishInput, PolishStep

    async with _project_lock(runtime, request.project_id):
        layout = ProjectLayout(runtime.storage.existing_project_dir(request.project_id))
        chapter_num = request.chapter_number
        from novel_forge.persistence.authoring_store import AuthoringStore, story_input_version

        configured = AuthoringStore(layout.root).policy() is not None
        input_version = story_input_version(layout.root) if configured else ""
        io_context = ChapterRunIOContext(
            storage=runtime.storage,
            layout=layout,
            project_id=request.project_id,
            chapter_number=chapter_num,
            source="polish_chapter",
        )
        cache = _ChapterDataCache(runtime.storage, layout, artifact_loader=io_context)
        chapter_source_slice = _load_chapter_source_slice_if_available(
            runtime.storage,
            layout,
            project_id=request.project_id,
            chapter_number=chapter_num,
        )

        # Load chapter text
        chapter_path = layout.chapter_path(chapter_num)
        review_draft_path = layout.chapter_review_draft_path(chapter_num)
        if chapter_path.exists():
            chapter_text = chapter_path.read_text(encoding="utf-8")
            save_path = chapter_path
        elif review_draft_path.exists():
            if configured:
                raise ValueError("未归档正文请在当前检查点修改方案或继续有限修复；不能绕开成稿验收")
            chapter_text = review_draft_path.read_text(encoding="utf-8")
            save_path = review_draft_path
        else:
            raise ValueError(f"第 {chapter_num} 章尚无可用正文，无法执行精修润色。")

        spec_raw = io_context.load_json(layout.spec_path) if layout.spec_path.exists() else {}
        tone = spec_raw.get("tone", "")
        genre = spec_raw.get("genre", "")

        outline_raw = (
            io_context.load_json(layout.outline_path) if layout.outline_path.exists() else {}
        )
        chapters_list = outline_raw.get("chapters", [])
        try:
            outline_for_position: Any = StoryOutline.model_validate(outline_raw)
        except Exception:
            outline_for_position = outline_raw
        chapter_position = (
            build_chapter_position(outline_for_position, chapter_num) if outline_raw else {}
        )
        chapter_info: dict[str, Any] = next(
            (c for c in chapters_list if c.get("chapter_number") == chapter_num),
            {},
        )
        chapter_title = chapter_info.get("title", f"第 {chapter_num} 章")
        pov_character = chapter_info.get("pov_character", "")

        eval_summary = ""
        eval_path = layout.eval_report_path(chapter_num)
        if eval_path.exists():
            eval_raw = io_context.load_json(eval_path)
            eval_summary = eval_raw.get("summary", "")

        trace = PipelineTrace()
        step = PolishStep(
            runtime.router,
            runtime.builder,
            settings=runtime.settings,
            trace=trace,
            on_step=on_step_progress,
        )

        if on_step_progress:
            on_step_progress("polish_start", {"chapter_number": chapter_num})

        result = await step.run(
            PolishInput(
                chapter_number=chapter_num,
                chapter_title=chapter_title,
                chapter_text=chapter_text,
                tone=tone,
                genre=genre,
                pov_character=pov_character,
                eval_summary=eval_summary,
                continuity_notes=request.notes,
                style_profile=cache.get_style_profile(),
                chapter_source_slice=chapter_source_slice,
                total_chapters=int(chapter_position.get("total_chapters") or 0),
                is_last_chapter=bool(chapter_position.get("is_last_chapter")),
                chapter_position=chapter_position,
            )
        )

        if configured:
            from novel_forge.core.authoring import AuthoringProposalRequest
            from novel_forge.workspace.authoring_proposals import create_proposal_under_lock

            proposal = create_proposal_under_lock(
                runtime,
                request.project_id,
                AuthoringProposalRequest(
                    command="revise_chapter",
                    chapter_number=chapter_num,
                    candidate=result.polished_text,
                    title="正文精修候选",
                    expected_input_version=input_version,
                    evidence=[request.notes or "作者主动启动的精修；文学建议不代表事实重验通过"],
                ),
            )
            payload = {
                "status": "candidate",
                "proposal_id": proposal.id,
                "chapter_number": chapter_num,
                "message": "精修候选待作者批准；正文、正史与既有报告未改变",
            }
            if on_step_progress:
                on_step_progress("polish_candidate", payload)
            return ExecutionResult(project_id=request.project_id, result=payload)

        runtime.storage.save_text(save_path, result.polished_text)
        if result.polished_text != chapter_text:
            invalidate_chapter_tts_artifacts(layout, chapter_num)
            try:
                record_text_revision(
                    layout,
                    chapter_number=chapter_num,
                    source="polish_chapter",
                    previous_hash=_source_text_hash(chapter_text),
                    current_hash=_source_text_hash(result.polished_text),
                    scope=RevisionScope.LOCAL,
                    reason=str(request.notes or "polish_chapter"),
                )
            except Exception as exc:
                _log.warning("Failed to record polish text revision: %s", exc)

        polish_diff = build_text_revision_diff(
            chapter_text,
            result.polished_text,
            source="polish_chapter",
            chapter_number=chapter_num,
            label_before="润色前",
            label_after="润色后",
            status="accepted",
            reason=str(request.notes or ""),
        )
        polish_diff_path = (
            layout.reports_dir / "revisions" / f"chapter_{chapter_num:03d}_polish_chapter.json"
        )
        try:
            runtime.storage.save_json(polish_diff_path, polish_diff)
        except Exception as exc:
            _log.warning("Failed to save polish revision diff: %s", exc)
        else:
            if on_step_progress:
                on_step_progress(
                    "polish_revision_diff",
                    {
                        "chapter_number": chapter_num,
                        "path": str(polish_diff_path),
                        "status": polish_diff.get("status", ""),
                    },
                )

        try:
            from novel_forge.core.utils.edit_tracker import save_snapshot

            save_snapshot(save_path, layout.states_dir, chapter_num)
        except Exception as exc:
            _log.warning("Failed to save edit snapshot: %s", exc)

        if on_step_progress:
            on_step_progress("polish", result)

        # ── Quality re-score ──────────────────────────────────────────────────
        try:
            from novel_forge.pipeline.steps.evaluate_step import EvaluateStep

            eval_ctx: dict[str, Any] = {"tone": tone, "genre": genre}
            if request.notes:
                eval_ctx["rewrite_notes"] = request.notes
            # 注入风格参数，使评估器能根据风格类型动态调整评分标准
            eval_ctx["style"] = None
            eval_ctx["style_profile"] = cache.get_style_profile()
            re_eval_step = EvaluateStep(
                runtime.router,
                runtime.builder,
                settings=runtime.settings,
                trace=PipelineTrace(),
                extra_context=eval_ctx,
            )
            re_eval_report = await re_eval_step.run(result.polished_text)
            _eval_payload = re_eval_report.model_dump(mode="json")
            _eval_payload["source_text_hash"] = _source_text_hash(result.polished_text)
            runtime.storage.save_json(
                layout.eval_report_path(chapter_num),
                _eval_payload,
            )
            if on_step_progress:
                on_step_progress("evaluate", re_eval_report.model_dump(mode="json"))
        except Exception as exc:
            from novel_forge.workspace.helpers.execution_helpers import _emit_noncritical_warning

            result = _emit_noncritical_warning(
                result=result,
                on_step_progress=on_step_progress,
                warning_step="evaluate_warning",
                warning_payload={
                    "project_id": request.project_id,
                    "chapter_number": chapter_num,
                    "message": "润色正文已保存，但润色后的质量重评分失败，当前评估报告可能仍是旧版本。",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                log_event="polish_rescore_failed",
                error=exc,
            )

        # ── Review report refresh after polish ───────────────────────────────
        # Polish may rewrite prose, so expensive review reports must go through
        # the same freshness/reuse service used by checkpoints and re-evaluation.
        try:
            from novel_forge.workspace.review_report_refresh import (
                refresh_workspace_review_reports,
            )

            await refresh_workspace_review_reports(
                runtime=runtime,
                layout=layout,
                project_id=request.project_id,
                chapter_number=chapter_num,
                current_text=result.polished_text,
                io_context=io_context,
                trace=PipelineTrace(),
                chapter_source_slice=chapter_source_slice,
                style_profile=cache.get_style_profile(),
                genre=genre,
                stale_reason="polish_chapter",
                report_kinds=("alignment", "continuity", "causal"),
                causal_recheck_mode=True,
                missing_message_template=(
                    "润色正文已保存，但缺少审查报告刷新所需工件，"
                    "已跳过对齐/连贯性/因果重审核。（缺失：{missing}）"
                ),
                on_step_progress=on_step_progress,
            )
        except Exception as exc:
            from novel_forge.workspace.helpers.execution_helpers import _emit_noncritical_warning

            result = _emit_noncritical_warning(
                result=result,
                on_step_progress=on_step_progress,
                warning_step="quality_reports_refresh_warning",
                warning_payload={
                    "project_id": request.project_id,
                    "chapter_number": chapter_num,
                    "message": "润色正文已保存，但润色后的审查报告刷新失败，当前审查报告可能仍是旧版本。",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                log_event="polish_review_reports_refresh_failed",
                error=exc,
            )

        from novel_forge.workspace.helpers.execution_helpers import _run_revised_text_postprocess

        await _run_revised_text_postprocess(
            runtime,
            project_id=request.project_id,
            layout=layout,
            chapter_number=chapter_num,
            source="polish_chapter",
            original_text=chapter_text,
            revised_text=result.polished_text,
            downstream_always_safe=True,
            on_step_progress=on_step_progress,
        )

    return ExecutionResult(project_id=request.project_id, result=result)
