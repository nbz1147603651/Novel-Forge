"""Workspace adapters for applying the shared precision gate to repair issues."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

from novel_forge.core.review.review_contracts import (
    build_ticket_verification_results,
    compile_repair_tickets_from_findings,
    normalize_issue_to_finding,
    source_text_hash,
)
from novel_forge.core.review.review_precision import (
    diagnostic_auto_repair_eligible,
    issue_to_dict,
    prepare_diagnostics_for_repair,
    prepare_findings_for_repair,
    split_review_paragraphs,
)
from novel_forge.core.schemas.review import RepairTicket, ReviewFinding

IssueT = TypeVar("IssueT")


@dataclass(frozen=True)
class SchemaRepairAdmission:
    """Precision-gated repair contracts for schema-native repair issues."""

    issues: list[Any]
    review_findings: list[ReviewFinding]
    repair_tickets: list[RepairTicket]
    repair_readiness: dict[str, Any]


def filter_precision_eligible_synthetic_issues(items: Any) -> list[dict[str, Any]]:
    """Drop synthetic repair issues blocked by the shared precision gate."""
    eligible: list[dict[str, Any]] = []
    for item in list(items or []):
        if not diagnostic_auto_repair_eligible(item):
            continue
        payload = issue_to_dict(item)
        if payload:
            eligible.append(payload)
    return eligible


def _issue_payload_for_precision(issue: Any, *, chapter_number: int) -> dict[str, Any]:
    payload = issue_to_dict(issue)
    if not payload:
        return {}
    payload.setdefault("primary_chapter", chapter_number)
    payload.setdefault("chapter_number", chapter_number)
    payload.setdefault("chapters_involved", [chapter_number])
    if not payload.get("evidence"):
        payload["evidence"] = payload.get("evidence_quote", "")
    if not payload.get("evidence_quote"):
        payload["evidence_quote"] = payload.get("evidence", "")
    if not payload.get("paragraph_index"):
        payload["paragraph_index"] = payload.get("paragraph_start", 0)
    if not payload.get("paragraph_span"):
        start = int(payload.get("paragraph_start") or 0)
        end = int(payload.get("paragraph_end") or start or 0)
        if start > 0:
            payload["paragraph_span"] = [start, max(start, end)]
    if not payload.get("fix_action"):
        actions = payload.get("fix_actions")
        if isinstance(actions, list) and actions:
            payload["fix_action"] = str(actions[0] or "")
        else:
            payload["fix_action"] = payload.get("fix_mode", "")
    return payload


def _update_issue_anchor(issue: IssueT, prepared: dict[str, Any]) -> IssueT:
    if not hasattr(issue, "model_copy"):
        return issue
    anchor = prepared.get("repair_anchor")
    if not isinstance(anchor, dict):
        return issue

    update: dict[str, Any] = {}
    span = anchor.get("paragraph_span")
    if isinstance(span, list) and span:
        start = int(span[0] or 0)
        end = int(span[-1] or start or 0)
        if start > 0:
            update["paragraph_start"] = start
            update["paragraph_end"] = max(start, end)

    anchor_type = str(anchor.get("anchor_type", "") or "").strip()
    if anchor_type:
        update["anchor_type"] = anchor_type

    try:
        confidence = float(anchor.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence > float(getattr(issue, "location_confidence", 0.0) or 0.0):
        update["location_confidence"] = max(0.0, min(1.0, confidence))

    if update:
        return issue.model_copy(update=update)
    return issue


def prepare_schema_issues_for_repair(
    issues: list[IssueT],
    *,
    chapter_number: int,
    current_text: str,
) -> tuple[list[IssueT], dict[str, Any]]:
    """Apply precision readiness to schema issue objects without changing their identity."""
    if not issues:
        return [], {
            "schema_version": 1,
            "architecture": "diagnostic_to_code_action",
            "status": "ready",
            "issue_count": 0,
            "auto_repair_candidate_count": 0,
            "ready_count": 0,
            "verify_first_count": 0,
            "manual_review_count": 0,
            "blocked_count": 0,
            "duplicate_issue_ids": [],
            "report_anomalies": [],
        }

    paragraphs = split_review_paragraphs(current_text)
    payloads = [
        payload
        for issue in issues
        if (payload := _issue_payload_for_precision(issue, chapter_number=chapter_number))
    ]
    prepared, readiness = prepare_diagnostics_for_repair(
        payloads,
        completed_chapters=[chapter_number],
        paragraph_lookup=lambda _chapter: paragraphs,
    )

    eligible: list[IssueT] = []
    for issue, prepared_issue in zip(issues, prepared, strict=False):
        if prepared_issue.get("auto_repair_eligible") is False:
            continue
        eligible.append(_update_issue_anchor(issue, prepared_issue))
    return eligible, readiness


def prepare_schema_repair_admission(
    issues: list[IssueT],
    *,
    chapter_number: int,
    current_text: str,
    source_module: str,
    dimension: str,
) -> SchemaRepairAdmission:
    """Build first-class findings/tickets while preserving native repair issue objects."""
    prepared_issues, readiness = prepare_schema_issues_for_repair(
        issues,
        chapter_number=chapter_number,
        current_text=current_text,
    )
    text_hash = source_text_hash(current_text)
    findings = [
        normalize_issue_to_finding(
            issue,
            source_module=source_module,
            chapter_number=chapter_number,
            dimension=dimension,
            current_text_hash=text_hash,
        )
        for issue in prepared_issues
    ]
    findings, readiness_from_findings = prepare_findings_for_repair(
        findings,
        current_text=current_text,
        completed_chapters=[chapter_number],
    )
    tickets = compile_repair_tickets_from_findings(
        findings,
        require_auto_repair_eligible=True,
    )
    return SchemaRepairAdmission(
        issues=list(prepared_issues),
        review_findings=findings,
        repair_tickets=tickets,
        repair_readiness=readiness_from_findings or readiness,
    )


__all__ = [
    "SchemaRepairAdmission",
    "build_ticket_verification_results",
    "filter_precision_eligible_synthetic_issues",
    "prepare_schema_issues_for_repair",
    "prepare_schema_repair_admission",
]
