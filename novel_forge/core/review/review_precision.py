"""Shared precision gate for review diagnostics before repair.

The review/repair architecture mirrors editor tooling:

``raw issue -> diagnostic anchor -> repair readiness -> repair ticket -> verification``.

This module owns the diagnostic-anchor/readiness part so book-wide audits,
chapter checks, guardrail reviews, reading-power checks, and future repair lanes
can make the same admission decision before automated text edits.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any

from novel_forge.common.severity import normalize_severity
from novel_forge.core.review.repair_harness import diagnose_review_finding
from novel_forge.core.schemas.review import (
    ReviewDiagnosticAnchor,
    ReviewFinding,
    ReviewRepairReadiness,
)
from novel_forge.core.utils.patch_utils import resolve_paragraph_locally

ParagraphLookup = Callable[[int], list[str]]

HIGH_RISK_MANUAL_ACTIONS = {"rewrite", "fulltext", "window"}


def review_precision_severity_rank(value: Any, *, default: str = "info") -> int:
    """Return the precision-gate risk band for normalized review severity."""
    severity = normalize_severity(value, default=default)
    if severity == "critical":
        return 3
    if severity == "high":
        return 2
    if severity in {"warning", "medium", "major"}:
        return 1
    return 0


def coerce_int(value: Any, default: int = 0) -> int:
    if isinstance(value, list):
        value = value[0] if value else default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def issue_to_dict(issue: Any) -> dict[str, Any]:
    if isinstance(issue, dict):
        return dict(issue)
    if hasattr(issue, "model_dump"):
        dumped = issue.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, dict) else {}
    return {}


def split_review_paragraphs(text: str) -> list[str]:
    """Split review source text into stable paragraph anchors."""
    source = str(text or "").strip()
    if not source:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", source) if part.strip()]
    if len(paragraphs) <= 1:
        lines = [line.strip() for line in source.splitlines() if line.strip()]
        if len(lines) > 1:
            return lines
    return paragraphs


def diagnostic_auto_repair_eligible(item: Any) -> bool:
    """Read the precision-gate auto-repair flag from a diagnostic-like payload."""
    payload = issue_to_dict(item)
    if not payload:
        return True
    if payload.get("auto_repair_eligible") is False:
        return False
    readiness = payload.get("repair_readiness")
    if isinstance(readiness, dict) and readiness.get("auto_repair_eligible") is False:
        return False
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        if metadata.get("auto_repair_eligible") is False:
            return False
        nested = metadata.get("repair_readiness")
        if isinstance(nested, dict) and nested.get("auto_repair_eligible") is False:
            return False
    return True


def finding_auto_repair_eligible(finding: ReviewFinding) -> bool:
    """Return whether a normalized finding may be compiled into an auto ticket."""
    metadata = dict(getattr(finding, "metadata", {}) or {})
    return diagnostic_auto_repair_eligible(
        {
            "auto_repair_eligible": metadata.get("auto_repair_eligible", True),
            "repair_readiness": metadata.get("repair_readiness"),
        }
    )


def finding_to_diagnostic_issue(finding: ReviewFinding) -> dict[str, Any]:
    """Project a normalized finding into the shared precision-gate issue shape."""
    metadata = dict(getattr(finding, "metadata", {}) or {})
    original_issue = metadata.get("original_issue")
    if not isinstance(original_issue, dict):
        original_issue = {}
    paragraph_span: list[int] = []
    if finding.paragraph_start > 0:
        paragraph_span = [
            finding.paragraph_start,
            max(finding.paragraph_start, finding.paragraph_end or finding.paragraph_start),
        ]
    location = (
        str(metadata.get("location", "") or "").strip()
        or str(original_issue.get("location", "") or "").strip()
    )
    linked_issue_refs = metadata.get("linked_issue_refs")
    if not isinstance(linked_issue_refs, list):
        linked_issue_refs = original_issue.get("linked_issue_refs")
    if not isinstance(linked_issue_refs, list):
        linked_issue_refs = []
    return {
        "issue_id": finding.finding_id,
        "finding_id": finding.finding_id,
        "primary_chapter": finding.chapter_number,
        "chapter_number": finding.chapter_number,
        "chapters_involved": [finding.chapter_number] if finding.chapter_number > 0 else [],
        "dimension": finding.dimension,
        "category": metadata.get("chapter_quality_category") or finding.dimension,
        "issue_type": finding.issue_type,
        "severity": finding.severity,
        "confidence": finding.confidence,
        "summary": finding.summary,
        "description": finding.summary,
        "evidence": finding.evidence_quote,
        "evidence_quote": finding.evidence_quote,
        "location": location,
        "paragraph_index": finding.paragraph_start,
        "paragraph_start": finding.paragraph_start,
        "paragraph_end": finding.paragraph_end,
        "paragraph_span": paragraph_span,
        "anchor_type": finding.anchor_type,
        "fix_mode": metadata.get("fix_mode") or finding.suggested_mode,
        "fix_action": metadata.get("fix_action") or finding.suggested_mode,
        "linked_issue_refs": linked_issue_refs,
    }


def stable_diagnostic_digest(parts: list[Any], *, length: int = 12) -> str:
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def diagnostic_signature(issue: dict[str, Any]) -> str:
    return stable_diagnostic_digest(
        [
            issue.get("primary_chapter"),
            issue.get("chapter_number"),
            issue.get("category"),
            issue.get("dimension"),
            issue.get("issue_type"),
            issue.get("location"),
            issue.get("paragraph_index"),
            issue.get("paragraph_span"),
            str(issue.get("evidence", "") or issue.get("evidence_quote", ""))[:240],
            str(issue.get("description", "") or issue.get("summary", ""))[:240],
        ]
    )


def normalize_diagnostic_id(issue: dict[str, Any], seen: dict[str, int]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    raw_issue_id = str(
        issue.get("issue_id") or issue.get("finding_id") or issue.get("id") or ""
    ).strip()
    if not raw_issue_id:
        raw_issue_id = f"review_issue_{diagnostic_signature(issue)}"
        reasons.append("missing_issue_id_generated")

    count = seen.get(raw_issue_id, 0)
    seen[raw_issue_id] = count + 1
    if count <= 0:
        return raw_issue_id, reasons

    unique_id = f"{raw_issue_id}__{diagnostic_signature(issue)}"
    issue["original_issue_id"] = raw_issue_id
    reasons.append("duplicate_issue_id_renamed")
    return unique_id, reasons


def resolve_primary_chapter(issue: dict[str, Any]) -> int:
    primary_chapter = coerce_int(issue.get("primary_chapter"), 0)
    if primary_chapter > 0:
        return primary_chapter
    chapter_number = coerce_int(issue.get("chapter_number"), 0)
    if chapter_number > 0:
        return chapter_number
    chapters = issue.get("chapters_involved") or []
    if isinstance(chapters, list) and chapters:
        return coerce_int(chapters[0], 0)
    return 0


def _valid_span(raw: Any, paragraph_index: int) -> list[int]:
    span: list[int] = []
    if isinstance(raw, list) and len(raw) >= 2:
        start = coerce_int(raw[0], 0)
        end = coerce_int(raw[-1], 0)
        if start > 0 and end > 0:
            span = [min(start, end), max(start, end)]
    if not span and paragraph_index > 0:
        span = [paragraph_index, paragraph_index]
    return span


def resolve_diagnostic_anchor(
    issue: dict[str, Any],
    paragraphs: list[str],
) -> ReviewDiagnosticAnchor:
    primary_chapter = resolve_primary_chapter(issue)
    paragraph_index = coerce_int(
        issue.get("paragraph_index") or issue.get("paragraph_start"),
        0,
    )
    if paragraph_index <= 0:
        match = re.search(r"第\s*(\d+)\s*段", str(issue.get("location", "") or ""))
        if match:
            paragraph_index = coerce_int(match.group(1), 0)
    paragraph_span = _valid_span(
        issue.get("paragraph_span") or [issue.get("paragraph_start"), issue.get("paragraph_end")],
        paragraph_index,
    )
    linked_refs = issue.get("linked_issue_refs")
    linked_ref_count = len(linked_refs) if isinstance(linked_refs, list) else 0

    if not paragraphs:
        if paragraph_index > 0:
            return ReviewDiagnosticAnchor(
                chapter_number=primary_chapter,
                paragraph_index=paragraph_index,
                paragraph_span=paragraph_span,
                anchor_type="reported_paragraph",
                confidence=0.45,
                linked_issue_ref_count=linked_ref_count,
            )
        return ReviewDiagnosticAnchor(
            chapter_number=primary_chapter,
            anchor_type="missing_text",
            confidence=0.0,
            linked_issue_ref_count=linked_ref_count,
        )

    evidence = str(issue.get("evidence", "") or issue.get("evidence_quote", "") or "")
    location = str(issue.get("location", "") or "")
    targets, anchor_type, confidence = resolve_paragraph_locally(
        paragraphs,
        evidence=evidence,
        location=location,
        llm_hint=paragraph_index,
    )
    if targets and anchor_type != "fallback" and confidence >= 0.5:
        start = max(1, min(targets) + 1)
        end = max(1, max(targets) + 1)
        return ReviewDiagnosticAnchor(
            chapter_number=primary_chapter,
            paragraph_index=start,
            paragraph_span=[start, end],
            anchor_type=anchor_type,
            confidence=max(0.0, min(float(confidence), 1.0)),
            linked_issue_ref_count=linked_ref_count,
        )

    para_count = len(paragraphs)
    if 1 <= paragraph_index <= para_count:
        return ReviewDiagnosticAnchor(
            chapter_number=primary_chapter,
            paragraph_index=paragraph_index,
            paragraph_span=paragraph_span or [paragraph_index, paragraph_index],
            anchor_type="llm_hint",
            confidence=0.25,
            linked_issue_ref_count=linked_ref_count,
        )

    return ReviewDiagnosticAnchor(
        chapter_number=primary_chapter,
        anchor_type=anchor_type or "fallback",
        confidence=max(0.0, min(float(confidence), 0.20)),
        linked_issue_ref_count=linked_ref_count,
    )


def classify_repair_readiness(
    issue: dict[str, Any],
    *,
    completed_chapters: set[int],
    anchor: ReviewDiagnosticAnchor,
    id_reasons: list[str],
) -> ReviewRepairReadiness:
    reasons = list(id_reasons)
    primary_chapter = anchor.chapter_number
    if primary_chapter <= 0:
        reasons.append("missing_primary_chapter")
    elif completed_chapters and primary_chapter not in completed_chapters:
        reasons.append("primary_chapter_not_completed")

    confidence = coerce_float(issue.get("confidence"), 0.0)
    severity_rank = review_precision_severity_rank(issue.get("severity"), default="info")
    fix_mode = str(issue.get("fix_mode", "") or "").strip().lower()
    fix_action = str(issue.get("fix_action", "") or "").strip().lower()

    status = "ready"
    risk = "low"
    if primary_chapter <= 0 or "primary_chapter_not_completed" in reasons:
        status = "blocked"
        risk = "blocker"
    elif anchor.paragraph_index <= 0:
        status = "verify_first"
        risk = "medium" if severity_rank >= 1 else "low"
        reasons.append("unresolved_paragraph_anchor")
    elif confidence and confidence <= 0.35:
        status = "manual_review"
        risk = "high"
        reasons.append("low_confidence")
    elif fix_mode == "manual_patch" and fix_action in HIGH_RISK_MANUAL_ACTIONS:
        status = "manual_review"
        risk = "high"
        reasons.append("manual_patch_rewrite_requires_human")
    elif (
        fix_mode == "manual_patch"
        and anchor.linked_issue_ref_count <= 0
        and anchor.confidence < 0.75
    ):
        status = "manual_review"
        risk = "medium"
        reasons.append("manual_patch_without_precise_anchor")
    elif anchor.linked_issue_ref_count <= 0 and anchor.confidence < 0.5:
        status = "verify_first"
        risk = "medium" if severity_rank >= 1 else "low"
        reasons.append("needs_stage2_location_verification")
    elif anchor.linked_issue_ref_count <= 0 and anchor.confidence < 0.75:
        status = "verify_first"
        risk = "low"
        reasons.append("no_linked_issue_ref")

    return ReviewRepairReadiness(
        status=status,
        risk=risk,
        reasons=reasons,
        auto_repair_eligible=status not in {"manual_review", "blocked"},
    )


def summarize_repair_readiness(
    prepared_issues: list[dict[str, Any]],
    *,
    duplicate_issue_ids: set[str],
    report_anomalies: list[dict[str, Any]],
) -> dict[str, Any]:
    status_counts = {"ready": 0, "verify_first": 0, "manual_review": 0, "blocked": 0}
    eligible_count = 0
    for issue in prepared_issues:
        readiness = issue.get("repair_readiness")
        if not isinstance(readiness, dict):
            continue
        status = str(readiness.get("status", "") or "ready")
        if status not in status_counts:
            status = "ready"
        status_counts[status] += 1
        if readiness.get("auto_repair_eligible") is True:
            eligible_count += 1

    blocker_anomalies = [item for item in report_anomalies if item.get("severity") == "blocker"]
    if blocker_anomalies:
        report_status = "needs_canonical_report"
    elif status_counts["blocked"]:
        report_status = "blocked"
    elif status_counts["manual_review"] and not eligible_count:
        report_status = "manual_review_only"
    elif status_counts["verify_first"]:
        report_status = "verify_first"
    else:
        report_status = "ready"

    return {
        "schema_version": 1,
        "architecture": "diagnostic_to_code_action",
        "status": report_status,
        "issue_count": len(prepared_issues),
        "auto_repair_candidate_count": eligible_count,
        "ready_count": status_counts["ready"],
        "verify_first_count": status_counts["verify_first"],
        "manual_review_count": status_counts["manual_review"],
        "blocked_count": status_counts["blocked"],
        "duplicate_issue_ids": sorted(duplicate_issue_ids),
        "report_anomalies": report_anomalies,
    }


def prepare_diagnostics_for_repair(
    issues: list[Any],
    *,
    completed_chapters: list[int] | set[int],
    paragraph_lookup: ParagraphLookup,
    report_anomalies: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Prepare raw review issues for repair across all review modules."""
    completed_set = {int(ch) for ch in completed_chapters if int(ch) > 0}
    seen_ids: dict[str, int] = {}
    duplicate_original_ids: set[str] = set()
    prepared: list[dict[str, Any]] = []
    paragraph_cache: dict[int, list[str]] = {}

    for raw_issue in issues:
        issue = issue_to_dict(raw_issue)
        if not issue:
            continue
        original_id = str(issue.get("issue_id", "") or issue.get("finding_id", "") or "").strip()
        issue_id, id_reasons = normalize_diagnostic_id(issue, seen_ids)
        if "duplicate_issue_id_renamed" in id_reasons and original_id:
            duplicate_original_ids.add(original_id)
        issue["issue_id"] = issue_id

        primary_chapter = resolve_primary_chapter(issue)
        if primary_chapter > 0:
            issue["primary_chapter"] = primary_chapter
        if primary_chapter not in paragraph_cache:
            paragraph_cache[primary_chapter] = paragraph_lookup(primary_chapter)
        anchor = resolve_diagnostic_anchor(issue, paragraph_cache.get(primary_chapter, []))
        readiness = classify_repair_readiness(
            issue,
            completed_chapters=completed_set,
            anchor=anchor,
            id_reasons=id_reasons,
        )
        if anchor.paragraph_index > 0:
            issue["paragraph_index"] = anchor.paragraph_index
            issue["paragraph_span"] = list(anchor.paragraph_span)
        issue["repair_anchor"] = anchor.model_dump(mode="json")
        issue["repair_readiness"] = readiness.model_dump(mode="json")
        issue["auto_repair_eligible"] = readiness.auto_repair_eligible
        prepared.append(issue)

    readiness_summary = summarize_repair_readiness(
        prepared,
        duplicate_issue_ids=duplicate_original_ids,
        report_anomalies=list(report_anomalies or []),
    )
    return prepared, readiness_summary


def prepare_findings_for_repair(
    findings: list[ReviewFinding],
    *,
    current_text: str = "",
    completed_chapters: list[int] | set[int] | None = None,
    paragraph_lookup: ParagraphLookup | None = None,
    report_anomalies: list[dict[str, Any]] | None = None,
) -> tuple[list[ReviewFinding], dict[str, Any]]:
    """Attach precision anchors/readiness to normalized findings before ticketing."""
    if not findings:
        return [], summarize_repair_readiness(
            [],
            duplicate_issue_ids=set(),
            report_anomalies=list(report_anomalies or []),
        )

    paragraphs = split_review_paragraphs(current_text)
    if paragraph_lookup is None:

        def _paragraph_lookup(_chapter: int) -> list[str]:
            return paragraphs

        paragraph_lookup = _paragraph_lookup

    resolved_completed = (
        set(completed_chapters)
        if completed_chapters is not None
        else {finding.chapter_number for finding in findings if finding.chapter_number > 0}
    )
    diagnostic_issues = [finding_to_diagnostic_issue(finding) for finding in findings]
    prepared_issues, readiness_summary = prepare_diagnostics_for_repair(
        diagnostic_issues,
        completed_chapters=resolved_completed,
        paragraph_lookup=paragraph_lookup,
        report_anomalies=report_anomalies,
    )

    prepared_findings: list[ReviewFinding] = []
    for finding, issue in zip(findings, prepared_issues, strict=False):
        anchor = issue.get("repair_anchor")
        readiness = issue.get("repair_readiness")
        anchor_payload = anchor if isinstance(anchor, dict) else {}
        readiness_payload = readiness if isinstance(readiness, dict) else {}
        span = anchor_payload.get("paragraph_span")
        if isinstance(span, list) and span:
            paragraph_start = coerce_int(span[0], finding.paragraph_start)
            paragraph_end = coerce_int(span[-1], paragraph_start)
        else:
            paragraph_start = coerce_int(
                anchor_payload.get("paragraph_index"),
                finding.paragraph_start,
            )
            paragraph_end = max(paragraph_start, finding.paragraph_end)
        metadata = dict(finding.metadata or {})
        original_finding_id = str(issue.get("original_issue_id", "") or "").strip()
        if original_finding_id:
            metadata["original_finding_id"] = original_finding_id
        metadata.update(
            {
                "repair_anchor": anchor_payload,
                "repair_readiness": readiness_payload,
                "auto_repair_eligible": bool(issue.get("auto_repair_eligible", True)),
            }
        )
        prepared_finding = finding.model_copy(
            update={
                "finding_id": str(issue.get("issue_id") or finding.finding_id),
                "paragraph_start": max(0, paragraph_start),
                "paragraph_end": max(0, paragraph_end),
                "anchor_type": str(anchor_payload.get("anchor_type") or finding.anchor_type),
                "metadata": metadata,
            }
        )
        source_text = current_text
        if not source_text and prepared_finding.chapter_number > 0:
            source_text = "\n\n".join(paragraph_lookup(prepared_finding.chapter_number))
        metadata["repair_harness"] = diagnose_review_finding(
            prepared_finding,
            current_text=source_text,
            auto_repair_eligible=bool(issue.get("auto_repair_eligible", True)),
        )
        prepared_findings.append(prepared_finding.model_copy(update={"metadata": metadata}))

    return prepared_findings, readiness_summary
