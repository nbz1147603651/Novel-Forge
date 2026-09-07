"""Workspace adapter for unified chapter review report refresh."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from novel_forge.core.schemas.chapter import AlignmentReport, CausalValidationReport
from novel_forge.core.schemas.continuity import (
    ChapterBridge,
    ChapterPlan,
    ChapterStatePacket,
    ContinuityReport,
)
from novel_forge.core.schemas.reading_power import ReadingPowerReport
from novel_forge.pipeline.long.stages.report_refresh import (
    RefreshedReviewReports,
    ReviewReportService,
)
from novel_forge.workspace.execution_result import StepCallback


async def refresh_workspace_review_reports(
    *,
    runtime: Any,
    layout: Any,
    project_id: str,
    chapter_number: int,
    current_text: str,
    io_context: Any,
    trace: Any,
    chapter_source_slice: Any | None = None,
    style_profile: Any | None = None,
    genre: str = "",
    stale_reason: str,
    report_kinds: tuple[str, ...] = (
        "alignment",
        "continuity",
        "causal",
        "reading_power",
    ),
    causal_recheck_mode: bool = True,
    causal_strict_review: bool = False,
    missing_message_template: str = (
        "缺少审查报告刷新所需工件，已跳过审查报告刷新。（缺失：{missing}）"
    ),
    warning_sink: list[str] | None = None,
    on_step_progress: StepCallback = None,
    emit_compat_events: bool = True,
) -> RefreshedReviewReports | None:
    """Refresh or reuse expensive review reports from workspace command paths.

    This keeps CLI/API/Desktop command wrappers from duplicating the pipeline
    runner/bundle adapter required by :class:`ReviewReportService`.
    """

    packet_path = layout.chapter_state_packet_path(chapter_number)
    bridge_path = layout.chapter_bridge_path(chapter_number)
    plan_path = layout.chapter_plan_path(chapter_number)
    missing = [
        str(path)
        for path in (packet_path, bridge_path, plan_path)
        if not runtime.storage.exists(path)
    ]
    if missing:
        message = missing_message_template.format(missing=", ".join(missing))
        if warning_sink is not None:
            warning_sink.append(message)
        if on_step_progress:
            on_step_progress(
                "quality_reports_refresh_warning",
                {
                    "project_id": project_id,
                    "chapter_number": chapter_number,
                    "message": message,
                },
            )
        return None

    packet = io_context.load_model(packet_path, ChapterStatePacket)
    bridge = io_context.load_model(bridge_path, ChapterBridge)
    plan = io_context.load_model(plan_path, ChapterPlan)
    alignment_report = (
        io_context.load_optional_model(
            layout.alignment_report_path(chapter_number),
            AlignmentReport,
        )
        or AlignmentReport()
    )
    continuity_report = (
        io_context.load_optional_model(
            layout.continuity_report_path(chapter_number),
            ContinuityReport,
        )
        or ContinuityReport()
    )
    causal_report = (
        io_context.load_optional_model(
            layout.chapter_causal_report_path(chapter_number),
            CausalValidationReport,
        )
        or CausalValidationReport()
    )
    reading_power_report = (
        (
            io_context.load_optional_model(
                layout.reading_power_report_path(chapter_number),
                ReadingPowerReport,
            )
            or ReadingPowerReport(chapter=chapter_number)
        )
        if "reading_power" in report_kinds
        else None
    )

    def _relay_step(step_name: str, payload: Any) -> None:
        if on_step_progress:
            on_step_progress(step_name, payload)

    service_runner = SimpleNamespace(
        _storage=runtime.storage,
        _router=runtime.router,
        _builder=runtime.builder,
        _settings=runtime.settings,
        _on_step=_relay_step,
        on_step=_relay_step,
    )
    service_bundle = SimpleNamespace(
        layout=layout,
        project_id=project_id,
        chapter_outline=packet.chapter_outline,
        chapter_source_slice=chapter_source_slice,
        style_profile=style_profile,
        story_bible=SimpleNamespace(genre=genre),
        blueprint=None,
    )
    if on_step_progress:
        on_step_progress(
            "quality_reports_refresh_start",
            {"chapter_number": chapter_number, "source": stale_reason},
        )

    refreshed = await ReviewReportService(
        runner=service_runner,
        bundle=service_bundle,
        packet=packet,
        bridge=bridge,
        plan=plan,
        chapter_number=chapter_number,
        trace=trace,
    ).ensure_current(
        current_text=current_text,
        alignment_report=alignment_report,
        continuity_report=continuity_report,
        causal_report=causal_report,
        reading_power_report=reading_power_report,
        stale_reason=stale_reason,
        causal_recheck_mode=causal_recheck_mode,
        causal_strict_review=causal_strict_review,
        report_kinds=report_kinds,
    )

    if emit_compat_events and on_step_progress:
        on_step_progress("continuity_eval_after_repair", refreshed.continuity_report)
        if refreshed.causal_report is not None:
            on_step_progress("causal_eval_after_repair", refreshed.causal_report)
        if refreshed.reading_power_report is not None:
            rp_payload = (
                refreshed.reading_power_report.model_dump(mode="json")
                if hasattr(refreshed.reading_power_report, "model_dump")
                else dict(refreshed.reading_power_report)
            )
            on_step_progress("reading_power_eval", rp_payload)

    return refreshed


__all__ = ["refresh_workspace_review_reports"]
