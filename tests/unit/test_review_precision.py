from __future__ import annotations

from novel_forge.core.review.review_contracts import compile_repair_tickets_from_findings
from novel_forge.core.review.review_precision import (
    prepare_diagnostics_for_repair,
    prepare_findings_for_repair,
)
from novel_forge.core.schemas.chapter import CausalIssue, CausalValidationReport
from novel_forge.core.schemas.continuity import ContinuityReport
from novel_forge.core.schemas.review import ReviewFinding
from novel_forge.workspace.repair_ops.execution_repair_precision import (
    build_ticket_verification_results,
    prepare_schema_issues_for_repair,
    prepare_schema_repair_admission,
)


def test_prepare_diagnostics_for_repair_is_shared_editor_style_gate() -> None:
    paragraphs = {
        1: [
            "第一段没有问题。",
            "第二段保留了旧称谓。",
        ]
    }

    prepared, readiness = prepare_diagnostics_for_repair(
        [
            {
                "issue_id": "dup",
                "primary_chapter": 1,
                "chapters_involved": [1],
                "severity": "warning",
                "category": "naming",
                "description": "称谓不一致。",
                "evidence": "旧称谓",
                "fix_mode": "repair_continuity",
                "fix_action": "replace",
                "confidence": 0.9,
            },
            {
                "issue_id": "dup",
                "primary_chapter": 1,
                "chapters_involved": [1],
                "severity": "critical",
                "category": "narrative_drift",
                "description": "需要人工大改。",
                "evidence": "第二段保留了旧称谓",
                "fix_mode": "manual_patch",
                "fix_action": "rewrite",
                "confidence": 0.9,
            },
        ],
        completed_chapters=[1],
        paragraph_lookup=lambda chapter: paragraphs.get(chapter, []),
    )

    assert prepared[0]["paragraph_index"] == 2
    assert prepared[0]["repair_anchor"]["anchor_type"] == "evidence_exact"
    assert prepared[0]["repair_readiness"]["status"] == "ready"
    assert prepared[1]["original_issue_id"] == "dup"
    assert prepared[1]["repair_readiness"]["status"] == "manual_review"
    assert readiness["architecture"] == "diagnostic_to_code_action"
    assert readiness["duplicate_issue_ids"] == ["dup"]
    assert readiness["auto_repair_candidate_count"] == 1


def test_prepare_diagnostics_for_repair_propagates_report_blockers() -> None:
    prepared, readiness = prepare_diagnostics_for_repair(
        [
            {
                "issue_id": "ok",
                "primary_chapter": 1,
                "chapters_involved": [1],
                "severity": "warning",
                "description": "有定位。",
                "paragraph_index": 1,
                "confidence": 0.8,
            }
        ],
        completed_chapters=[1],
        paragraph_lookup=lambda _chapter: ["有定位。"],
        report_anomalies=[
            {
                "code": "issue_count_truncated",
                "severity": "blocker",
                "message": "报告被截断。",
            }
        ],
    )

    assert prepared[0]["auto_repair_eligible"] is True
    assert readiness["status"] == "needs_canonical_report"
    assert readiness["report_anomalies"][0]["code"] == "issue_count_truncated"


def test_prepare_findings_for_repair_controls_ticket_compilation() -> None:
    findings = [
        ReviewFinding(
            finding_id="ready_finding",
            chapter_number=3,
            source_module="reading_power_eval",
            dimension="reading_power",
            issue_type="weak_hook",
            severity="medium",
            confidence=0.9,
            summary="章尾钩子弱。",
            evidence_quote="她只觉得一切都结束了。",
            paragraph_start=0,
            repair_goal="补强章尾钩子",
        ),
        ReviewFinding(
            finding_id="manual_finding",
            chapter_number=3,
            source_module="book_consistency",
            dimension="narrative_drift",
            issue_type="global_rewrite_needed",
            severity="critical",
            confidence=0.9,
            summary="需要人工重写多处。",
            evidence_quote="她只觉得一切都结束了。",
            repair_goal="人工重写",
            metadata={"fix_mode": "manual_patch", "fix_action": "rewrite"},
        ),
    ]

    prepared, readiness = prepare_findings_for_repair(
        findings,
        current_text="第一段。\n\n她只觉得一切都结束了。",
        completed_chapters=[3],
    )
    tickets = compile_repair_tickets_from_findings(
        prepared,
        require_auto_repair_eligible=True,
    )

    assert prepared[0].paragraph_start == 2
    assert prepared[0].metadata["repair_anchor"]["anchor_type"] == "evidence_exact"
    assert prepared[0].metadata["repair_readiness"]["status"] == "ready"
    assert prepared[0].metadata["repair_harness"]["architecture"] == (
        "diagnose_locate_patch_verify"
    )
    assert prepared[0].metadata["repair_harness"]["status"] == "ready"
    assert prepared[1].metadata["repair_readiness"]["status"] == "manual_review"
    assert prepared[1].metadata["repair_harness"]["status"] == "manual_required"
    assert readiness["auto_repair_candidate_count"] == 1
    assert [ticket.finding_ids for ticket in tickets] == [["ready_finding"]]


def test_prepare_schema_issues_for_repair_keeps_issue_identity_and_filters_risk() -> None:
    issues = [
        CausalIssue(
            issue_type="event_without_cause",
            severity="high",
            summary="角色突然离开缺少触发。",
            evidence="她突然决定离开。",
            fix_mode="window",
        ),
        CausalIssue(
            issue_type="event_without_cause",
            severity="critical",
            summary="需要人工大范围重写。",
            evidence="她突然决定离开。",
            fix_mode="manual_patch",
            fix_actions=["rewrite"],
        ),
    ]

    prepared, readiness = prepare_schema_issues_for_repair(
        issues,
        chapter_number=5,
        current_text="第一段。\n\n她突然决定离开。",
    )

    assert len(prepared) == 1
    assert prepared[0].issue_id == ""
    assert prepared[0].paragraph_start == 2
    assert prepared[0].anchor_type == "evidence_exact"
    assert readiness["auto_repair_candidate_count"] == 1
    assert readiness["manual_review_count"] == 1


def test_repair_reports_have_first_class_readiness_and_verification_fields() -> None:
    causal = CausalValidationReport()
    continuity = ContinuityReport()

    assert causal.repair_readiness == {}
    assert causal.verification_results == []
    assert causal.review_findings == []
    assert causal.repair_tickets == []
    assert continuity.repair_readiness == {}
    assert continuity.verification_results == []
    assert continuity.review_findings == []
    assert continuity.repair_tickets == []


def test_prepare_schema_repair_admission_builds_findings_and_tickets() -> None:
    admission = prepare_schema_repair_admission(
        [
            CausalIssue(
                issue_type="event_without_cause",
                severity="high",
                summary="角色突然离开缺少触发。",
                evidence="她突然决定离开。",
                fix_mode="window",
            )
        ],
        chapter_number=5,
        current_text="第一段。\n\n她突然决定离开。",
        source_module="validate_causal",
        dimension="causal",
    )

    assert len(admission.issues) == 1
    assert len(admission.review_findings) == 1
    assert len(admission.repair_tickets) == 1
    assert admission.review_findings[0].paragraph_start == 2
    assert admission.repair_tickets[0].target_paragraph_start == 2
    assert admission.repair_readiness["auto_repair_candidate_count"] == 1


def test_build_ticket_verification_results_maps_recheck_issues_to_tickets() -> None:
    admission = prepare_schema_repair_admission(
        [
            CausalIssue(
                issue_type="event_without_cause",
                severity="high",
                summary="角色突然离开缺少触发。",
                evidence="她突然决定离开。",
                fix_mode="window",
            )
        ],
        chapter_number=5,
        current_text="第一段。\n\n她突然决定离开。",
        source_module="validate_causal",
        dimension="causal",
    )

    unresolved = build_ticket_verification_results(
        admission.repair_tickets,
        remaining_issues=[
            CausalIssue(
                issue_type="event_without_cause",
                summary="角色突然离开缺少触发。",
                evidence="她突然决定离开。",
            )
        ],
        current_text="第一段。\n\n她突然决定离开。",
        applied=True,
    )
    resolved = build_ticket_verification_results(
        admission.repair_tickets,
        remaining_issues=[],
        current_text="第一段。\n\n她改为先找到触发原因。",
        applied=True,
    )

    assert unresolved[0].status == "unresolved"
    assert unresolved[0].ticket_id == admission.repair_tickets[0].ticket_id
    assert resolved[0].status == "resolved"
