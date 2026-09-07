"""Canonical book-audit payload helpers for schema-first tests."""

from __future__ import annotations

from typing import Any


def canonical_book_issue(
    issue_id: str = "issue_1",
    *,
    category: str = "timeline",
    severity: str = "warning",
    chapters_involved: list[int] | None = None,
    primary_chapter: int = 1,
    paragraph_index: int = 1,
    paragraph_span: list[int] | None = None,
    confidence: float = 0.8,
    **overrides: Any,
) -> dict[str, Any]:
    """Return a canonical BOOK_CONSISTENCY issue item."""
    span = paragraph_span if paragraph_span is not None else [paragraph_index, paragraph_index]
    payload: dict[str, Any] = {
        "issue_id": issue_id,
        "category": category,
        "severity": severity,
        "chapters_involved": chapters_involved or [primary_chapter],
        "primary_chapter": primary_chapter,
        "issue_type": category,
        "location": f"第{primary_chapter}章 P{paragraph_index}",
        "paragraph_index": paragraph_index,
        "paragraph_span": span,
        "evidence": "test evidence",
        "description": f"Issue {issue_id}",
        "suggestion": "按证据修正一致性问题。",
        "fix_mode": "repair_continuity",
        "fix_action": "rewrite",
        "confidence": confidence,
        "evidence_pairs": [],
        "verification_questions": [],
        "handoff_notes": "",
        "linked_issue_refs": [],
    }
    payload.update(overrides)
    return payload


def canonical_book_issue_from_mapping(issue: dict[str, Any]) -> dict[str, Any]:
    """Upgrade a compact semantic fixture to the current issue contract."""
    primary_chapter = int(issue.get("primary_chapter") or 1)
    paragraph_index = int(issue.get("paragraph_index") or 1)
    chapters = [int(item) for item in issue.get("chapters_involved") or [primary_chapter]]
    return canonical_book_issue(
        str(issue.get("issue_id") or "issue_1"),
        category=str(issue.get("category") or "timeline"),
        severity=str(issue.get("severity") or "warning"),
        chapters_involved=chapters,
        primary_chapter=primary_chapter,
        paragraph_index=paragraph_index,
        confidence=float(issue.get("confidence") or 0.8),
        **{
            key: value
            for key, value in issue.items()
            if key
            not in {
                "issue_id",
                "category",
                "severity",
                "chapters_involved",
                "primary_chapter",
                "paragraph_index",
                "confidence",
            }
        },
    )


def canonical_repair_plan(
    issue_id: str = "issue_1",
    *,
    chapter_number: int = 1,
    **overrides: Any,
) -> dict[str, Any]:
    """Return a canonical BOOK_CONSISTENCY repair_plan item."""
    payload: dict[str, Any] = {
        "chapter_number": chapter_number,
        "issue_ids": [issue_id],
        "priority": "normal",
        "strategy": "按验证后的定位修复。",
    }
    payload.update(overrides)
    return payload


def canonical_book_audit_response(
    *,
    issues: list[dict[str, Any]] | None = None,
    repair_plan: list[dict[str, Any]] | None = None,
    summary: str = "审计完成",
    consistency_score: float = 8.0,
) -> dict[str, Any]:
    """Return a canonical BOOK_CONSISTENCY envelope."""
    return {
        "issues": issues or [],
        "repair_plan": repair_plan or [],
        "summary": summary,
        "consistency_score": consistency_score,
    }


def canonical_repair_scope(**overrides: Any) -> dict[str, Any]:
    """Return a canonical BOOK_CONSISTENCY_VERIFY repair_scope object."""
    payload: dict[str, Any] = {
        "target": "paragraph",
        "allowed_changes": [],
        "forbidden_changes": [],
        "preserve": [],
    }
    payload.update(overrides)
    return payload


def canonical_verified_issue(
    issue_id: str = "issue_1",
    *,
    status: str = "verified",
    severity: str = "warning",
    confidence: float = 0.8,
    paragraph_index: int = 1,
    paragraph_span: list[int] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Return a canonical BOOK_CONSISTENCY_VERIFY verified_issues item."""
    span = paragraph_span if paragraph_span is not None else [paragraph_index, paragraph_index]
    payload: dict[str, Any] = {
        "issue_id": issue_id,
        "status": status,
        "description": f"Issue {issue_id}",
        "severity": severity,
        "confidence": confidence,
        "evidence": "test evidence",
        "paragraph_index": paragraph_index,
        "paragraph_span": span,
        "location": f"P{paragraph_index}",
        "anchor_type": "paragraph",
        "location_confidence": 0.9,
        "evidence_pairs": [],
        "adjudication_notes": "",
        "repair_scope": canonical_repair_scope(),
        "fix_mode": "repair_continuity",
        "fix_action": "rewrite",
        "postconditions": [],
        "rejection_reason": "",
    }
    payload.update(overrides)
    return payload
