"""Shared response builders for chapter studio sessions."""

from __future__ import annotations

from typing import Any

from novel_forge.core.schemas.chapter import PlotGuardDecision
from novel_forge.workspace.contracts import (
    ChapterSessionResult,
    DecisionCheckpoint,
    PrepareChapterResponse,
)
from novel_forge.workspace.result_payloads import clip_preview


def _guard_decision_label(guard_decision: PlotGuardDecision | None) -> str:
    return guard_decision.decision if guard_decision is not None else "none"


def build_prepare_response(
    *,
    project_id: str,
    chapter_number: int,
    checkpoint: DecisionCheckpoint,
) -> PrepareChapterResponse:
    return PrepareChapterResponse(
        project_id=project_id,
        chapter_number=chapter_number,
        status="needs_decision",
        checkpoint=checkpoint,
    )


def build_session_checkpoint_result(
    *,
    project_id: str,
    chapter_number: int,
    checkpoint: DecisionCheckpoint,
    applied_option_id: str,
    word_count: int = 0,
    overall_score: float | None = None,
    continuity_score: float | None = None,
    continuity_issue_count: int = 0,
    bridge_summary: str = "",
    chapter_exit_summary: str = "",
    preview_text: str = "",
    guard_decision: PlotGuardDecision | None = None,
    metadata: dict[str, Any] | None = None,
) -> ChapterSessionResult:
    payload = {"guard_decision": _guard_decision_label(guard_decision)}
    if metadata:
        payload.update(metadata)
    return ChapterSessionResult(
        project_id=project_id,
        chapter_number=chapter_number,
        status="needs_decision",
        checkpoint=checkpoint,
        word_count=word_count,
        overall_score=overall_score,
        continuity_score=continuity_score,
        continuity_issue_count=continuity_issue_count,
        bridge_summary=bridge_summary,
        chapter_exit_summary=chapter_exit_summary,
        preview=clip_preview(preview_text),
        applied_option_id=applied_option_id,
        metadata=payload,
    )


def build_session_result_from_prepare(
    *,
    response: PrepareChapterResponse,
    applied_option_id: str,
) -> ChapterSessionResult:
    checkpoint = response.checkpoint
    if checkpoint is None:
        raise ValueError("prepare response must include checkpoint")
    return build_session_checkpoint_result(
        project_id=response.project_id,
        chapter_number=response.chapter_number,
        checkpoint=checkpoint,
        applied_option_id=applied_option_id,
    )


def build_paused_session_result(
    *,
    project_id: str,
    chapter_number: int,
    checkpoint: DecisionCheckpoint,
    applied_option_id: str,
    guard_decision: PlotGuardDecision | None = None,
) -> ChapterSessionResult:
    return build_session_checkpoint_result(
        project_id=project_id,
        chapter_number=chapter_number,
        checkpoint=checkpoint,
        applied_option_id=applied_option_id,
        guard_decision=guard_decision,
        metadata={"paused": True},
    )


def build_completed_session_result(
    *,
    project_id: str,
    chapter_number: int,
    word_count: int,
    overall_score: float,
    continuity_score: float,
    continuity_issue_count: int,
    bridge_summary: str,
    chapter_exit_summary: str,
    preview_text: str,
    applied_option_id: str,
    guard_decision: PlotGuardDecision | None = None,
    metadata: dict[str, Any] | None = None,
    reading_power_score: float | None = None,
    reading_power_summary: str = "",
) -> ChapterSessionResult:
    payload = {"guard_decision": _guard_decision_label(guard_decision)}
    if metadata:
        payload.update(metadata)
    return ChapterSessionResult(
        project_id=project_id,
        chapter_number=chapter_number,
        status="completed",
        word_count=word_count,
        overall_score=overall_score,
        continuity_score=continuity_score,
        continuity_issue_count=continuity_issue_count,
        reading_power_score=reading_power_score,
        reading_power_summary=reading_power_summary,
        bridge_summary=bridge_summary,
        chapter_exit_summary=chapter_exit_summary,
        preview=clip_preview(preview_text),
        applied_option_id=applied_option_id,
        metadata=payload,
    )
