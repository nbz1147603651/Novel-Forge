"""Tests for audit-to-verification-to-repair handoff fields."""

from __future__ import annotations

from novel_forge.core.review.review_contracts import (
    compile_repair_ticket_from_finding,
    normalize_issue_to_finding,
    repair_ticket_to_causal_issue_payload,
    repair_ticket_to_continuity_issue_payload,
)


def test_verified_handoff_fields_flow_into_repair_ticket_and_payloads() -> None:
    issue = {
        "issue_id": "ch6_timeline_01",
        "category": "timeline",
        "issue_type": "time_order_conflict",
        "severity": "warning",
        "primary_chapter": 6,
        "paragraph_index": 4,
        "paragraph_span": [4, 5],
        "evidence": "第一次去古董店",
        "description": "第6章的首次到店说法需要与第5章对照。",
        "fix_mode": "repair_causal",
        "fix_action": "replace",
        "evidence_pairs": [
            {"chapter_number": 5, "evidence": "昨夜已在古董店", "claim": "已到店"},
            {"chapter_number": 6, "evidence": "第一次去古董店", "claim": "首次到店"},
        ],
        "verification_questions": ["第6章该句是否为回忆或转述？"],
        "adjudication_notes": "仅调整时间指代，不改两章事件结果。",
        "repair_scope": {
            "paragraph_span": [4, 5],
            "rewrite_scope": "paragraph",
            "allowed_changes": ["改时间副词和过渡句"],
            "forbidden_changes": ["不得改变金镯获得顺序"],
            "must_preserve": ["保留第6章到店动作"],
        },
        "postconditions": [
            {"description": "修复后第6章不再声称这是现实时间线第一次到店"}
        ],
    }

    finding = normalize_issue_to_finding(
        issue,
        source_module="book_consistency",
        chapter_number=6,
        current_text_hash="hash",
    )
    ticket = compile_repair_ticket_from_finding(finding)
    continuity_payload = repair_ticket_to_continuity_issue_payload(ticket)
    causal_payload = repair_ticket_to_causal_issue_payload(ticket)

    assert ticket.target_paragraph_start == 4
    assert ticket.target_paragraph_end == 5
    assert ticket.metadata["evidence_pairs"][0]["chapter_number"] == 5
    assert any("验证裁决" in item for item in ticket.acceptance_criteria)
    assert any("允许改动" in item for item in ticket.acceptance_criteria)
    assert "不得改变金镯获得顺序" in ticket.forbidden_changes
    assert "保留第6章到店动作" in ticket.must_preserve
    assert continuity_payload["repair_scope"]["rewrite_scope"] == "paragraph"
    assert continuity_payload["evidence_pairs"][1]["chapter_number"] == 6
    assert continuity_payload["forbidden_changes"] == ticket.forbidden_changes
    assert causal_payload["rewrite_scope"] == "paragraph"
    assert causal_payload["adjudication_notes"] == "仅调整时间指代，不改两章事件结果。"
