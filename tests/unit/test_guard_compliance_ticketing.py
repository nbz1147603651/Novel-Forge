from __future__ import annotations

from novel_forge.core.schemas.review import RepairTicket
from novel_forge.pipeline.long.stages.quality_checks import attach_guard_repair_metadata
from novel_forge.workspace.sessions.chapter_session_handlers import _guard_ticket_is_actionable


def _repair_ticket(issue_type: str, metadata: dict) -> RepairTicket:
    return RepairTicket(
        ticket_id=f"ticket_{issue_type}",
        chapter_number=8,
        finding_ids=["finding_1"],
        source_module="guard_constraint_compliance",
        dimension="guard_compliance",
        issue_type=issue_type,
        severity="medium",
        target_summary="AI护栏约束需要复核",
        repair_goal="复核护栏约束",
        repair_mode="window",
        acceptance_criteria=["修复后需明确回应约束"],
        must_preserve=["保持主线推进"],
        forbidden_changes=["不得改写已通过质量门的主要情节结果"],
        max_change_ratio=0.08,
        max_attempts=2,
        blocking=False,
        metadata=metadata,
    )


def test_attach_guard_repair_metadata_builds_ranked_findings_and_tickets() -> None:
    report = {
        "constraints": ["必须回应上一章的悬念", "不能让主角动机漂移", "保持章节节奏"],
        "compliance_results": [
            {
                "constraint": "必须回应上一章的悬念",
                "status": "non_compliant",
                "confidence": 0.91,
                "evidence": "章尾仍停留在旧信息，没有回应悬念。",
                "notes": "建议在后半章补上明确回应。",
            },
            {
                "constraint": "不能让主角动机漂移",
                "status": "weak",
                "confidence": 0.72,
                "evidence": "",
                "notes": "动机表达偏弱。",
            },
            {
                "constraint": "保持章节节奏",
                "status": "compliant",
                "confidence": 0.88,
                "evidence": "",
                "notes": "",
            },
        ],
        "overall_compliance_rate": 0.33,
        "summary": "共 3 条约束，1 条已遵守 (33%)",
    }

    findings, tickets = attach_guard_repair_metadata(
        report,
        chapter_number=12,
        current_text="第一段。\n\n章尾仍停留在旧信息，没有回应悬念。\n\n第二段回应不足。",
        max_tickets=2,
    )

    assert len(findings) == 2
    assert len(tickets) == 2

    first_finding = findings[0]
    assert first_finding.finding_id.startswith("guard_constraint_ch012-guard-")
    assert first_finding.issue_type == "guard_constraint_missing"
    assert first_finding.severity == "critical"
    assert first_finding.blocks_finalize is True
    assert first_finding.metadata["constraint"] == "必须回应上一章的悬念"
    assert first_finding.metadata["original_issue"]["issue_id"].startswith("ch012-guard-")
    assert report["repair_readiness"]["auto_repair_candidate_count"] == 2
    assert len(report["review_findings"]) == 2
    assert len(report["repair_tickets"]) == 2

    first_ticket = tickets[0]
    assert first_ticket.finding_ids == [first_finding.finding_id]
    assert first_ticket.metadata["original_issue"]["issue_id"].startswith("ch012-guard-")
    assert first_ticket.repair_mode == "replace"
    assert first_ticket.blocking is True
    assert first_ticket.acceptance_criteria[0] == "修复后需明确回应约束：必须回应上一章的悬念"

    second_ticket = tickets[1]
    assert second_ticket.severity == "medium"
    assert second_ticket.repair_mode == "window"


def test_attach_guard_repair_metadata_accepts_near_exact_evidence() -> None:
    findings, tickets = attach_guard_repair_metadata(
        {
            "constraints": ["必须回应上一章的悬念"],
            "compliance_results": [
                {
                    "constraint": "必须回应上一章的悬念",
                    "status": "non_compliant",
                    "confidence": 0.91,
                    "evidence": "章尾停留在旧信息，没有回应悬念。",
                    "notes": "这是概括，不是原文。",
                }
            ],
            "overall_compliance_rate": 0.0,
        },
        chapter_number=12,
        current_text="章尾仍停留在旧信息，没有回应悬念。",
    )

    assert len(findings) == 1
    assert findings[0].issue_type == "guard_constraint_missing"
    assert findings[0].metadata["repairable"] is True
    assert findings[0].metadata["evidence_exact"] is False
    assert findings[0].metadata["evidence_match_mode"] == "fuzzy"
    assert len(tickets) == 1


def test_attach_guard_repair_metadata_rejects_summary_evidence() -> None:
    findings, tickets = attach_guard_repair_metadata(
        {
            "constraints": ["必须回应上一章的悬念"],
            "compliance_results": [
                {
                    "constraint": "必须回应上一章的悬念",
                    "status": "non_compliant",
                    "confidence": 0.91,
                    "evidence": "这一段没有处理悬念，只是在重复旧信息。",
                    "notes": "这是概括，不是原文。",
                }
            ],
            "overall_compliance_rate": 0.0,
        },
        chapter_number=12,
        current_text="章尾仍停留在旧信息，没有回应悬念。",
    )

    assert len(findings) == 1
    assert findings[0].issue_type == "guard_constraint_unverified"
    assert findings[0].metadata["repairable"] is False
    assert findings[0].metadata["evidence_match_mode"] == "none"
    assert tickets == []


def test_attach_guard_repair_metadata_rejects_subject_mismatch_evidence() -> None:
    exact_but_unrelated = "沈念卿低头看着金镯，那种相似感让她短暂失神。"
    findings, tickets = attach_guard_repair_metadata(
        {
            "constraints": ["周芷若与陈伯庸的相似性仅通过程砚秋口述暗示，不做直接历史影像对比"],
            "compliance_results": [
                {
                    "constraint": "周芷若与陈伯庸的相似性仅通过程砚秋口述暗示，不做直接历史影像对比",
                    "status": "partial",
                    "confidence": 0.86,
                    "evidence": exact_but_unrelated,
                    "notes": "证据涉及相似感，但没有命中约束主体。",
                    "constraint_subjects": ["周芷若", "陈伯庸", "程砚秋"],
                }
            ],
            "overall_compliance_rate": 0.0,
        },
        chapter_number=4,
        current_text=exact_but_unrelated,
    )

    assert len(findings) == 1
    assert findings[0].issue_type == "guard_constraint_unverified"
    assert "显式主体" in findings[0].metadata["notes"]
    assert tickets == []


def test_attach_guard_repair_metadata_requires_evidence_for_prohibition_violation() -> None:
    findings, tickets = attach_guard_repair_metadata(
        {
            "constraints": ["不得直接历史影像对比"],
            "compliance_results": [
                {
                    "constraint": "不得直接历史影像对比",
                    "status": "non_compliant",
                    "confidence": 0.88,
                    "evidence": "",
                    "notes": "疑似违反禁令。",
                }
            ],
            "overall_compliance_rate": 0.0,
        },
        chapter_number=4,
        current_text="正文没有直接给出违规片段。",
    )

    assert len(findings) == 1
    assert findings[0].issue_type == "guard_constraint_unverified"
    assert "禁令类护栏" in findings[0].metadata["notes"]
    assert tickets == []


def test_attach_guard_repair_metadata_skips_compliant_results() -> None:
    findings, tickets = attach_guard_repair_metadata(
        {
            "constraints": ["保持场景收束"],
            "compliance_results": [
                {
                    "constraint": "保持场景收束",
                    "status": "compliant",
                    "confidence": 0.95,
                    "evidence": "已自然收束。",
                    "notes": "",
                }
            ],
            "overall_compliance_rate": 1.0,
            "summary": "共 1 条约束，1 条已遵守 (100%)",
        },
        chapter_number=3,
        current_text="",
    )

    assert findings == []
    assert tickets == []


def test_attach_guard_repair_metadata_does_not_ticket_unverified_or_check_error() -> None:
    findings, tickets = attach_guard_repair_metadata(
        {
            "constraints": ["必须回应上一章的悬念", "不能让主角动机漂移"],
            "compliance_results": [
                {
                    "constraint": "必须回应上一章的悬念",
                    "status": "check_error",
                    "confidence": 0.0,
                    "evidence": "",
                    "notes": "模型空响应",
                    "check_error": True,
                    "repairable": False,
                },
                {
                    "constraint": "不能让主角动机漂移",
                    "status": "unknown",
                    "confidence": 0.2,
                    "evidence": "",
                    "notes": "缺少证据，无法判断",
                },
            ],
            "overall_compliance_rate": None,
            "checked_count": 0,
            "check_failed_count": 1,
            "unverified_count": 1,
            "summary": "共 2 条约束，0 条已遵守；合规率未计算；1 条无法确认；1 条检查失败",
        },
        chapter_number=8,
        current_text="正文片段",
    )

    assert len(findings) == 1
    assert findings[0].issue_type == "guard_constraint_unverified"
    assert findings[0].metadata["repairable"] is False
    assert tickets == []


def test_guard_ticket_action_filter_skips_stale_unverified_ticket() -> None:
    stale_ticket = _repair_ticket(
        "guard_constraint_unverified",
        {"status": "unknown", "check_error": True, "repairable": False},
    )
    real_ticket = _repair_ticket(
        "guard_constraint_missing",
        {"status": "non_compliant", "repairable": True},
    )

    assert _guard_ticket_is_actionable(stale_ticket) is False
    assert _guard_ticket_is_actionable(real_ticket) is True


def test_guard_ticket_action_filter_requires_exact_evidence_flag() -> None:
    stale_evidence_ticket = _repair_ticket(
        "guard_constraint_missing",
        {
            "status": "non_compliant",
            "repairable": True,
            "evidence_quote": "这是一段旧票证据",
        },
    )
    exact_evidence_ticket = _repair_ticket(
        "guard_constraint_missing",
        {
            "status": "non_compliant",
            "repairable": True,
            "evidence_quote": "这是一段精确证据",
            "evidence_exact": True,
        },
    )

    assert _guard_ticket_is_actionable(stale_evidence_ticket) is False
    assert _guard_ticket_is_actionable(exact_evidence_ticket) is True


def test_guard_ticket_action_filter_honors_precision_gate() -> None:
    blocked_ticket = _repair_ticket(
        "guard_constraint_missing",
        {
            "status": "non_compliant",
            "repairable": True,
            "repair_readiness": {
                "status": "manual_review",
                "auto_repair_eligible": False,
            },
        },
    )

    assert _guard_ticket_is_actionable(blocked_ticket) is False


def test_guard_ticket_action_filter_does_not_consume_knowledge_boundary_ticket() -> None:
    ticket = RepairTicket(
        ticket_id="ticket_kb",
        chapter_number=8,
        finding_ids=["kb_finding"],
        source_module="knowledge_boundary_audit",
        dimension="knowledge_boundary",
        issue_type="knowledge_leak",
        severity="high",
        target_summary="知识边界泄漏",
        repair_goal="移除越界认知",
        repair_mode="window",
        metadata={"status": "non_compliant", "repairable": True},
    )

    assert _guard_ticket_is_actionable(ticket) is False
