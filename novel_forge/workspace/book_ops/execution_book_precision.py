"""Precision-gating helpers for whole-book audit repair.

This module treats book-audit issues like editor diagnostics: each issue must
carry a stable id, a chapter/range anchor, an action classification, and a
repair-readiness verdict before it is allowed to drive automated edits.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from novel_forge.common.severity import normalize_severity
from novel_forge.core.review.review_precision import (
    coerce_float as _coerce_float,
)
from novel_forge.core.review.review_precision import (
    coerce_int as _coerce_int,
)
from novel_forge.core.review.review_precision import (
    issue_to_dict as _issue_to_dict,
)
from novel_forge.core.review.review_precision import (
    prepare_diagnostics_for_repair,
)
from novel_forge.core.review.review_precision import (
    review_precision_severity_rank as _severity_rank,
)
from novel_forge.workspace.helpers.execution_helpers import _build_numbered_paragraph_text


def _chapter_path(layout: Any, chapter_number: int) -> Path | None:
    if chapter_number <= 0:
        return None
    if hasattr(layout, "chapter_path"):
        try:
            return Path(layout.chapter_path(chapter_number))
        except Exception:  # noqa: BLE001 - layout may be a lightweight test double
            return None
    chapters_dir = getattr(layout, "chapters_dir", None)
    if chapters_dir is None:
        root = getattr(layout, "root", None)
        if root is not None:
            chapters_dir = Path(root) / "chapters"
    if chapters_dir is None:
        return None
    return Path(chapters_dir) / f"chapter_{chapter_number:03d}.md"


def _chapter_paragraphs(layout: Any, chapter_number: int) -> list[str]:
    path = _chapter_path(layout, chapter_number)
    if path is None or not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    _numbered, _count, paragraphs = _build_numbered_paragraph_text(text)
    return paragraphs


def _extract_chapter_numbers_from_summaries(payload: dict[str, Any]) -> list[int]:
    chapters: list[int] = []
    for item in payload.get("chapter_summaries") or []:
        if not isinstance(item, dict):
            continue
        number = _coerce_int(item.get("chapter_number"), 0)
        if number > 0 and number not in chapters:
            chapters.append(number)
    return sorted(chapters)


def recover_completed_audit_payload_from_checkpoint(
    layout: Any,
    existing_payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Rebuild a full audit payload from a completed chunk checkpoint.

    Old desktop failures could leave a compact/recovered JSON report while the
    raw full-text chunk checkpoint still contained every parsed issue.  This
    function restores a canonical repair input from that checkpoint.
    """
    states_dir = getattr(layout, "states_dir", None)
    if states_dir is None:
        root = getattr(layout, "root", None)
        if root is None:
            return None
        states_dir = Path(root) / "states"
    checkpoint_path = Path(states_dir) / "book_consistency_audit_checkpoint.json"
    if not checkpoint_path.exists():
        return None
    try:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(checkpoint, dict):
        return None

    final_result = checkpoint.get("final_result")
    if isinstance(final_result, dict) and isinstance(final_result.get("issues"), list):
        restored = dict(existing_payload or {})
        restored.update(final_result)
        request_payload = restored.get("request")
        if not isinstance(request_payload, dict):
            request_payload = {}
        request_payload["canonical_rebuilt_from_checkpoint"] = True
        restored["request"] = request_payload
        restored["checkpoint_recovery"] = {
            "source": str(checkpoint_path),
            "mode": "final_result",
            "raw_issue_count": len(final_result.get("issues") or []),
        }
        return restored

    completed_chunks = checkpoint.get("completed_chunks")
    if not isinstance(completed_chunks, list) or not completed_chunks:
        return None

    issues: list[dict[str, Any]] = []
    repair_plan: list[dict[str, Any]] = []
    summaries: list[str] = []
    scores: list[float] = []
    for chunk in completed_chunks:
        if not isinstance(chunk, dict):
            continue
        for parsed in chunk.get("parsed_items") or []:
            if not isinstance(parsed, dict):
                continue
            for issue in parsed.get("issues") or []:
                issue_dict = _issue_to_dict(issue)
                if issue_dict:
                    issues.append(issue_dict)
            for plan_item in parsed.get("repair_plan") or []:
                if isinstance(plan_item, dict):
                    repair_plan.append(dict(plan_item))
            summary = str(parsed.get("summary", "") or "").strip()
            if summary:
                summaries.append(summary)
            score = _coerce_float(parsed.get("consistency_score"), 0.0)
            if score > 0:
                scores.append(score)

    if not issues:
        return None

    restored = dict(existing_payload or {})
    restored["issues"] = issues
    restored["repair_plan"] = repair_plan
    restored["chapters_audited"] = _extract_chapter_numbers_from_summaries(checkpoint)
    restored["analysis_mode"] = str(restored.get("analysis_mode", "") or "full_text")
    restored["summary"] = (
        str(restored.get("summary", "") or "").strip()
        or "；".join(summaries[:6])
        or f"从审计 checkpoint 恢复 {len(issues)} 个全书一致性问题。"
    )
    if scores and not restored.get("consistency_score"):
        restored["consistency_score"] = round(min(scores), 2)
    request_payload = restored.get("request")
    if not isinstance(request_payload, dict):
        request_payload = {}
    request_payload["canonical_rebuilt_from_checkpoint"] = True
    restored["request"] = request_payload
    restored["checkpoint_recovery"] = {
        "source": str(checkpoint_path),
        "mode": "completed_chunks",
        "chunks_total": _coerce_int(checkpoint.get("chunks_total"), len(completed_chunks)),
        "completed_chunks": len(completed_chunks),
        "raw_issue_count": len(issues),
        "repair_plan_items": len(repair_plan),
    }
    return restored


def _report_anomalies(
    report_payload: dict[str, Any] | None,
    completed_chapters: list[int],
    issue_count: int,
) -> list[dict[str, Any]]:
    if not isinstance(report_payload, dict):
        return []

    anomalies: list[dict[str, Any]] = []
    request_payload = report_payload.get("request")
    if not isinstance(request_payload, dict):
        request_payload = {}
    checkpoint_recovery = report_payload.get("checkpoint_recovery")
    rebuilt_from_checkpoint = bool(request_payload.get("canonical_rebuilt_from_checkpoint")) and (
        isinstance(checkpoint_recovery, dict)
    )

    if (report_payload.get("recovered_from") or request_payload.get("recovered_from_failed_run")) and (
        not rebuilt_from_checkpoint
    ):
        anomalies.append(
            {
                "code": "recovered_compact_report",
                "severity": "blocker",
                "message": "报告来自失败日志恢复，可能经过桌面进度压缩。",
            }
        )

    audited = [
        _coerce_int(ch, 0)
        for ch in (report_payload.get("chapters_audited") or [])
        if _coerce_int(ch, 0) > 0
    ]
    if audited and completed_chapters:
        missing = sorted(set(completed_chapters) - set(audited))
        if missing:
            anomalies.append(
                {
                    "code": "chapters_audited_incomplete",
                    "severity": "blocker",
                    "missing_chapters": missing[:40],
                    "missing_count": len(missing),
                    "message": "报告覆盖章节少于当前已完成章节。",
                }
            )

    metrics = report_payload.get("quality_metrics")
    if isinstance(metrics, dict):
        total_found = _coerce_int(metrics.get("total_issues_found"), 0)
        if total_found > issue_count and issue_count < int(total_found * 0.8):
            anomalies.append(
                {
                    "code": "issue_count_truncated",
                    "severity": "blocker",
                    "reported_total": total_found,
                    "materialized_issues": issue_count,
                    "message": "质量指标中的问题总数明显高于报告实际 issues 数。",
                }
            )

    return anomalies


def prepare_report_issues_for_precision_repair(
    report_issues: list[Any],
    *,
    completed_chapters: list[int],
    layout: Any,
    report_payload: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Normalize book-audit diagnostics before they drive whole-book repair."""
    return prepare_diagnostics_for_repair(
        report_issues,
        completed_chapters=completed_chapters,
        paragraph_lookup=lambda chapter_number: _chapter_paragraphs(layout, chapter_number),
        report_anomalies=_report_anomalies(
            report_payload,
            completed_chapters,
            len([item for item in report_issues if _issue_to_dict(item)]),
        ),
    )


def summarize_auto_repair_admission(
    prepared_issues: list[Any],
    *,
    min_severity: str = "warning",
    limit: int = 40,
) -> dict[str, Any]:
    """Summarize repair admission without making semantic judgments.

    The LLM/verification stages decide whether an issue is real.  This helper
    only normalizes the already-produced diagnostic/readiness fields so repair
    runners and UI can agree on what is eligible for automatic edits.
    """
    min_rank = _severity_rank(min_severity, default="warning")
    eligible_chapters: set[int] = set()
    ineligible_chapters: set[int] = set()
    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    eligible_issue_count = 0
    ineligible_issue_count = 0
    below_threshold_count = 0
    missing_readiness_count = 0
    ineligible_issues: list[dict[str, Any]] = []

    for raw_issue in prepared_issues:
        issue = _issue_to_dict(raw_issue)
        if not issue:
            continue
        severity = normalize_severity(issue.get("severity"), default="info")
        if _severity_rank(severity, default="info") < min_rank:
            below_threshold_count += 1
            continue
        chapter = _coerce_int(issue.get("primary_chapter") or issue.get("chapter_number"), 0)
        readiness = issue.get("repair_readiness")
        if not isinstance(readiness, dict):
            readiness = {}
            missing_readiness_count += 1
        status = str(readiness.get("status") or "ready").strip() or "ready"
        status_counts[status] += 1
        reasons = readiness.get("reasons")
        if not isinstance(reasons, list):
            reasons = []
        for reason in reasons:
            text = str(reason or "").strip()
            if text:
                reason_counts[text] += 1
        eligible = issue.get("auto_repair_eligible") is not False
        if eligible:
            eligible_issue_count += 1
            if chapter > 0:
                eligible_chapters.add(chapter)
            continue
        ineligible_issue_count += 1
        if chapter > 0:
            ineligible_chapters.add(chapter)
        if len(ineligible_issues) < limit:
            ineligible_issues.append(
                {
                    "issue_id": str(issue.get("issue_id") or issue.get("id") or ""),
                    "primary_chapter": chapter,
                    "severity": severity,
                    "category": str(issue.get("category") or issue.get("issue_type") or ""),
                    "status": status,
                    "reasons": [str(item) for item in reasons[:6]],
                    "description": str(
                        issue.get("description") or issue.get("summary") or ""
                    )[:160],
                }
            )

    return {
        "schema_version": 1,
        "source": "repair_readiness",
        "min_severity": str(min_severity or "warning"),
        "eligible_issue_count": eligible_issue_count,
        "ineligible_issue_count": ineligible_issue_count,
        "below_threshold_count": below_threshold_count,
        "missing_readiness_count": missing_readiness_count,
        "eligible_chapter_count": len(eligible_chapters),
        "eligible_chapters": sorted(eligible_chapters),
        "ineligible_chapter_count": len(ineligible_chapters),
        "ineligible_chapters": sorted(ineligible_chapters),
        "by_status": dict(sorted(status_counts.items())),
        "by_reason": dict(reason_counts.most_common(12)),
        "ineligible_issues": ineligible_issues,
    }
