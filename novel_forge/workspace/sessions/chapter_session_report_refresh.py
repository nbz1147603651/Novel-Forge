"""Report refresh helpers for chapter checkpoint sessions."""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from typing import Any, cast

from novel_forge.core.schemas.chapter import AlignmentReport
from novel_forge.core.schemas.continuity import ContinuityReport
from novel_forge.obs.tracer import PipelineTrace
from novel_forge.pipeline.long.preflight import LongProjectBundle
from novel_forge.pipeline.long.stages.report_refresh import ReviewReportService
from novel_forge.workspace.sessions.chapter_session_state import PendingChapterReviewState


async def refresh_pending_reports_for_text(
    *,
    runner: Any,
    bundle: LongProjectBundle,
    packet: Any,
    bridge: Any,
    plan: Any,
    current_text: str,
    chapter_number: int,
    trace: PipelineTrace,
    pending: PendingChapterReviewState,
    stale_reason: str,
    include_reading_power: bool = False,
    alignment_recheck: Callable[
        [Any, LongProjectBundle, Any, Any, str, int, PipelineTrace],
        Awaitable[AlignmentReport],
    ]
    | None = None,
) -> PendingChapterReviewState:
    """Refresh or reuse checkpoint pending reports through the unified service."""

    report_kinds = (
        ("alignment", "continuity", "causal", "reading_power")
        if include_reading_power
        else ("alignment", "continuity", "causal")
    )
    if alignment_recheck is not None:
        report_kinds = tuple(kind for kind in report_kinds if kind != "alignment")

    refreshed = await ReviewReportService(
        runner=runner,
        bundle=bundle,
        packet=packet,
        bridge=bridge,
        plan=plan,
        chapter_number=chapter_number,
        trace=trace,
    ).ensure_current(
        current_text=current_text,
        alignment_report=pending.alignment_report,
        continuity_report=pending.continuity_report,
        causal_report=pending.causal_report,
        chapter_repair_report=pending.chapter_repair_report,
        reading_power_report=pending.reading_power_report if include_reading_power else None,
        stale_reason=stale_reason,
        report_kinds=report_kinds,
    )
    refreshed_alignment = (
        await alignment_recheck(
            runner,
            bundle,
            packet,
            plan,
            current_text,
            chapter_number,
            trace,
        )
        if alignment_recheck is not None
        else refreshed.alignment_report
    )
    return dataclasses.replace(
        pending,
        alignment_report=cast(AlignmentReport, refreshed_alignment),
        continuity_report=cast(ContinuityReport, refreshed.continuity_report),
        causal_report=refreshed.causal_report,
        reading_power_report=(
            refreshed.reading_power_report if include_reading_power else pending.reading_power_report
        ),
        chapter_repair_report=refreshed.chapter_repair_report,
    )


__all__ = ["refresh_pending_reports_for_text"]
