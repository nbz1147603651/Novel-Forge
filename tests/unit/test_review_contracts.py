"""Tests for normalized review findings and repair ticket routing."""

from __future__ import annotations

from types import SimpleNamespace

from novel_forge.core.review.review_contracts import (
    alignment_report_to_findings,
    causal_report_to_findings,
    chapter_repair_report_to_findings,
    compile_repair_tickets_from_findings,
    continuity_report_to_findings,
    reading_power_report_to_findings,
    repair_ticket_matches_remaining_issue,
    repair_ticket_to_causal_issue_payload,
    repair_ticket_to_continuity_issue_payload,
    repair_ticket_to_reading_power_issue_payload,
)
from novel_forge.core.schemas.chapter import AlignmentReport, CausalIssue, ChapterRepairReport
from novel_forge.core.schemas.continuity import ContinuityIssue
from novel_forge.core.schemas.reading_power import ReadingPowerReport
from novel_forge.core.schemas.review import RepairTicket
from novel_forge.pipeline.long.stages.quality_checks import build_guard_compliance_findings


def test_all_review_dimensions_convert_to_findings() -> None:
    findings = []
    findings.extend(
        alignment_report_to_findings(
            AlignmentReport(
                missing_main_points=["主线点缺失"],
                weak_subplot_points=["支线兑现偏弱"],
            ),
            chapter_number=2,
        )
    )
    findings.extend(
        chapter_repair_report_to_findings(
            ChapterRepairReport(
                prompt_leaks=["系统提示混入正文"],
                expression_errors=["硬禁元素：冷白灯光"],
            ),
            chapter_number=2,
        )
    )
    findings.extend(
        continuity_report_to_findings(
            SimpleNamespace(
                issues=[
                    ContinuityIssue(
                        issue_type="character_state_conflict",
                        severity="high",
                        summary="角色状态与上章不一致",
                    )
                ]
            ),
            chapter_number=2,
        )
    )
    findings.extend(
        causal_report_to_findings(
            SimpleNamespace(
                issues=[
                    CausalIssue(
                        issue_type="event_without_cause",
                        severity="high",
                        summary="关键行动缺少前因",
                    )
                ]
            ),
            chapter_number=2,
        )
    )
    findings.extend(
        reading_power_report_to_findings(
            ReadingPowerReport(
                chapter=2,
                hook_type="none",
                hook_strength="none",
                micro_payoffs=[],
            ),
            chapter_number=2,
            min_payoffs=1,
        )
    )
    findings.extend(
        build_guard_compliance_findings(
            {
                "compliance_results": [
                    {
                        "constraint": "必须回应上章护栏约束",
                        "status": "non_compliant",
                        "confidence": 0.9,
                        "evidence": "",
                    }
                ]
            },
            chapter_number=2,
        )
    )

    assert {
        "alignment",
        "chapter_quality",
        "continuity",
        "causal",
        "reading_power",
        "guard",
    } <= {finding.dimension for finding in findings}


def test_string_backed_review_findings_get_stable_issue_ids() -> None:
    report = AlignmentReport(missing_main_points=["主线点缺失"])

    first = alignment_report_to_findings(report, chapter_number=2)
    second = alignment_report_to_findings(report, chapter_number=2)

    assert len(first) == 1
    assert first[0].finding_id == second[0].finding_id
    assert first[0].metadata["original_issue"]["issue_id"].startswith("ch002-check_alignment-")


def test_repair_ticket_projects_to_domain_issue_payloads() -> None:
    continuity_finding = continuity_report_to_findings(
        SimpleNamespace(
            issues=[
                ContinuityIssue(
                    issue_type="timeline_conflict",
                    severity="critical",
                    summary="时间线冲突",
                    evidence="前一日与三日后冲突",
                    paragraph_start=3,
                    paragraph_end=3,
                )
            ]
        ),
        chapter_number=1,
    )[0]
    causal_finding = causal_report_to_findings(
        SimpleNamespace(
            issues=[
                CausalIssue(
                    issue_type="motivation_gap",
                    severity="high",
                    summary="行动动机缺失",
                    evidence="突然决定离开",
                    paragraph_start=5,
                    paragraph_end=5,
                )
            ]
        ),
        chapter_number=1,
    )[0]
    reading_finding = reading_power_report_to_findings(
        ReadingPowerReport(chapter=1, hook_type="none", hook_strength="none"),
        chapter_number=1,
        current_text_hash="abc123",
        review_mode="targeted_recheck",
        review_round=2,
    )[0]

    tickets = compile_repair_tickets_from_findings(
        [continuity_finding, causal_finding, reading_finding]
    )
    ticket_by_dimension = {ticket.dimension: ticket for ticket in tickets}

    continuity_payload = repair_ticket_to_continuity_issue_payload(
        ticket_by_dimension["continuity"]
    )
    causal_payload = repair_ticket_to_causal_issue_payload(ticket_by_dimension["causal"])
    reading_payload = repair_ticket_to_reading_power_issue_payload(
        ticket_by_dimension["reading_power"]
    )

    assert continuity_payload["issue_type"] == "timeline_conflict"
    assert continuity_payload["paragraph_start"] == 3
    assert causal_payload["issue_type"] == "motivation_gap"
    assert causal_payload["paragraph_start"] == 5
    assert reading_payload["issue_type"] == "hook_missing"
    assert reading_finding.review_mode == "targeted_recheck"
    assert reading_finding.review_round == 2
    assert reading_finding.postconditions
    assert ticket_by_dimension["reading_power"].source_text_hash == "abc123"
    assert reading_payload["postconditions"]


def test_repair_ticket_matching_treats_mismatched_ids_as_different_issues() -> None:
    ticket = RepairTicket(
        finding_ids=["issue-original"],
        issue_type="continuity_gap",
        target_summary="同一个摘要",
        repair_goal="修复同一个摘要",
    )
    remaining_issue = ContinuityIssue(
        issue_id="issue-different",
        issue_type="continuity_gap",
        severity="high",
        summary="同一个摘要",
    )

    assert repair_ticket_matches_remaining_issue(ticket, remaining_issue) is False
