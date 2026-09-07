from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from novel_forge.core.schemas.continuity import ContinuityIssue, ContinuityReport
from novel_forge.core.schemas.review import RepairTicket
from novel_forge.pipeline.long.chapter_flow_finalize import (
    normalize_continuity_report_repair_state,
)
from novel_forge.pipeline.long.execution_models import FlowContextAdapter
from novel_forge.pipeline.long.stages.quality_checks import (
    _remember_opening_guard_pending_issues,
    merge_opening_guard_pending_issues,
)


def test_opening_guard_pending_issues_merge_into_continuity_report() -> None:
    runner = SimpleNamespace()
    issue = ContinuityIssue(
        issue_type="opening_state_mismatch",
        severity="high",
        summary="开场状态与上一章结尾不一致",
    )
    _remember_opening_guard_pending_issues(
        runner,
        [issue],
        reason="opening_guard_repair_not_applied",
    )
    events: list[tuple[str, Any]] = []
    report = ContinuityReport(continuity_score=8.8, summary="初检通过", issues=[])

    merged = merge_opening_guard_pending_issues(
        runner,
        report,
        chapter_number=3,
        on_step=lambda step, payload: events.append((step, payload)),
    )

    assert len(merged.issues) == 1
    assert merged.issues[0].blocking is True
    assert merged.issues[0].source == "local"
    assert merged.continuity_score == 6.0
    assert "开场门禁遗留" in merged.summary
    assert report.issues == []
    assert report.continuity_score == 8.8
    assert report.summary == "初检通过"
    assert events[0][0] == "opening_guard_issues_merged"
    assert not hasattr(runner, "_novel_forge_opening_guard_pending_issues")


def test_opening_guard_pending_state_survives_flow_context_adapter() -> None:
    runner = FlowContextAdapter(SimpleNamespace())
    issue = ContinuityIssue(
        issue_type="opening_state_mismatch",
        severity="high",
        summary="开场状态与上一章结尾不一致",
    )
    _remember_opening_guard_pending_issues(
        runner,
        [issue],
        reason="opening_guard_repair_not_applied",
    )

    merged = merge_opening_guard_pending_issues(
        runner,
        ContinuityReport(continuity_score=8.8, summary="初检通过", issues=[]),
        chapter_number=3,
        on_step=lambda _step, _payload: None,
    )

    assert len(merged.issues) == 1
    assert not hasattr(runner, "_novel_forge_opening_guard_pending_issues")


def test_continuity_report_normalization_dedupes_remaining_issues_and_tickets() -> None:
    remaining_issue = ContinuityIssue(
        issue_id="keep-issue",
        issue_type="state_mismatch",
        severity="high",
        summary="人物状态未承接",
    )
    duplicate_issue = remaining_issue.model_copy()
    resolved_ticket = RepairTicket(
        ticket_id="ticket-resolved",
        finding_ids=["resolved-issue"],
        dimension="continuity",
        issue_type="state_mismatch",
        target_summary="已修复的问题",
        repair_goal="不应继续出现在剩余报告中",
    )
    remaining_ticket = RepairTicket(
        ticket_id="ticket-keep",
        finding_ids=["keep-issue"],
        dimension="continuity",
        issue_type="state_mismatch",
        target_summary="人物状态未承接",
        repair_goal="补足状态承接",
    )
    duplicate_ticket = remaining_ticket.model_copy(update={"ticket_id": "ticket-duplicate"})
    report = ContinuityReport(
        continuity_score=7.2,
        issues=[remaining_issue, duplicate_issue],
        repair_tickets=[resolved_ticket, remaining_ticket, duplicate_ticket],
    )

    normalized = normalize_continuity_report_repair_state(report)

    assert [issue.issue_id for issue in normalized.issues] == ["keep-issue"]
    assert [ticket.ticket_id for ticket in normalized.repair_tickets] == ["ticket-keep"]
    assert len(report.issues) == 2
    assert len(report.repair_tickets) == 3


def test_continuity_report_normalization_drops_tickets_when_no_issues_remain() -> None:
    ticket = RepairTicket(
        ticket_id="ticket-resolved",
        finding_ids=["resolved-issue"],
        dimension="continuity",
        issue_type="state_mismatch",
        target_summary="已修复的问题",
        repair_goal="不应继续出现在剩余报告中",
    )
    report = ContinuityReport(continuity_score=9.8, issues=[], repair_tickets=[ticket])

    normalized = normalize_continuity_report_repair_state(report)

    assert normalized.issues == []
    assert normalized.repair_tickets == []


def test_opening_guard_pending_fallback_copies_non_pydantic_report() -> None:
    runner = SimpleNamespace()
    issue = ContinuityIssue(
        issue_type="opening_state_mismatch",
        severity="high",
        summary="开场状态与上一章结尾不一致",
    )
    _remember_opening_guard_pending_issues(
        runner,
        [issue],
        reason="opening_guard_repair_not_applied",
    )
    report = SimpleNamespace(issues=[], continuity_score=8.8, summary="初检通过")

    merged = merge_opening_guard_pending_issues(
        runner,
        report,
        chapter_number=3,
        on_step=lambda _step, _payload: None,
    )

    assert merged is not report
    assert len(merged.issues) == 1
    assert merged.continuity_score == 6.0
    assert "开场门禁遗留" in merged.summary
    assert report.issues == []
    assert report.continuity_score == 8.8
    assert report.summary == "初检通过"
