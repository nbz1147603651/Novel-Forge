"""Post-repair local verification: check whether original issues still exist."""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novel_forge.core.exceptions import StorageError, ValidationError
from novel_forge.core.review.review_contracts import (
    build_repair_verification_result,
    compile_repair_tickets_from_findings,
    normalize_issue_to_finding,
    repair_ticket_to_causal_issue_payload,
    repair_ticket_to_continuity_issue_payload,
    review_dimension_for_issue,
    source_text_hash,
)
from novel_forge.core.schemas.review import RepairTicket, ReviewFinding
from novel_forge.persistence.filesystem import atomic_write_text
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.repair_orchestration.mission_factory import issues_repair_mission
from novel_forge.workspace.book_ops.execution_book_common import _log
from novel_forge.workspace.book_ops.execution_book_precision import summarize_auto_repair_admission
from novel_forge.workspace.book_ops.execution_book_reevaluate import execute_reevaluate_chapter
from novel_forge.workspace.book_ops.execution_book_repair_checkpoint import (
    _compute_repair_checksum as _compute_repair_checksum,
)
from novel_forge.workspace.book_ops.execution_book_repair_checkpoint import (
    _load_repair_checkpoint as _load_repair_checkpoint,
)
from novel_forge.workspace.book_ops.execution_book_repair_checkpoint import (
    _repair_checkpoint_signature as _repair_checkpoint_signature,
)
from novel_forge.workspace.book_ops.execution_book_repair_checkpoint import (
    _save_repair_checkpoint as _save_repair_checkpoint,
)
from novel_forge.workspace.book_repair_metrics import summarize_book_repair_details
from novel_forge.workspace.contracts import (
    BookConsistencyRequest,
    ReevaluateChapterRequest,
    RepairIssuesRequest,
)
from novel_forge.workspace.execution_result import StepCallback
from novel_forge.workspace.helpers.execution_helpers import (
    _book_issue_to_lane,
    _collect_linked_issue_indices_for_lane,
    _group_book_issues_by_chapter,
    _post_repair_evidence_check,
    _report_severity_rank,
    _scan_cross_chapter_entity_impact,
    _select_issue_indices_by_paragraph,
    _select_issue_indices_for_lane,
)
from novel_forge.workspace.propagation_validator import PropagationValidator
from novel_forge.workspace.regression_detector import RegressionDetector
from novel_forge.workspace.repair_ops.execution_repair_v2 import execute_repair
from novel_forge.workspace.runtime import RuntimeServices


def _select_panel_first_expansion_indices(
    report_issues: list[Any],
    existing_indices: set[int],
    *,
    max_extra: int = 3,
) -> list[int]:
    threshold = _report_severity_rank("high")
    candidates = sorted(
        [
            (idx, issue)
            for idx, issue in enumerate(report_issues)
            if idx not in existing_indices
            and _report_severity_rank(getattr(issue, "severity", None)) >= threshold
        ],
        key=lambda item: -_report_severity_rank(getattr(item[1], "severity", None)),
    )
    return [idx for idx, _issue in candidates[:max_extra]]


def _aggregate_propagation_by_type(issues: list[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for issue in issues:
        issue_type = str(issue.get("issue_type", "unknown"))
        result[issue_type] = result.get(issue_type, 0) + 1
    return result


def _aggregate_propagation_by_severity(issues: list[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for issue in issues:
        severity = str(issue.get("severity", "unknown"))
        result[severity] = result.get(severity, 0) + 1
    return result


_BOOK_ISSUE_CATEGORY_LABELS: dict[str, str] = {
    "naming": "名称",
    "timeline": "时间线",
    "worldbuilding": "世界观",
    "character_state": "角色状态",
    "narrative_drift": "叙事漂移",
}

_BOOK_ISSUE_SEVERITY_LABELS: dict[str, str] = {
    "critical": "严重",
    "high": "严重",
    "warning": "警告",
    "major": "重要",
    "medium": "重要",
}


def _summarize_book_issue_focus(chapter_issues: list[dict[str, Any]]) -> str:
    """Return a compact UI-safe issue summary for the current repair chapter."""
    category_counts: dict[str, int] = {}
    severity_counts: dict[str, int] = {}
    for issue in chapter_issues:
        if not isinstance(issue, dict):
            continue
        category = str(issue.get("category") or issue.get("issue_type") or "问题").strip()
        if category:
            category_counts[category] = category_counts.get(category, 0) + 1
        severity = str(issue.get("severity") or "").strip().lower()
        if severity in _BOOK_ISSUE_SEVERITY_LABELS:
            label = _BOOK_ISSUE_SEVERITY_LABELS[severity]
            severity_counts[label] = severity_counts.get(label, 0) + 1

    category_parts: list[str] = []
    sorted_cats = sorted(category_counts.items(), key=lambda i: (-i[1], i[0]))
    for category, count in sorted_cats[:2]:
        label = _BOOK_ISSUE_CATEGORY_LABELS.get(category, category)
        category_parts.append(f"{label}{count}项")

    severity_parts: list[str] = []
    for label in ("严重", "警告", "重要"):
        count = severity_counts.get(label, 0)
        if count:
            severity_parts.append(f"{label}{count}")

    category_text = "、".join(category_parts or [f"问题{len(chapter_issues)}项"])
    if severity_parts:
        category_text += " / " + "、".join(severity_parts)
    return category_text[:48]


def _build_book_repair_contracts(
    chapter_issues: list[dict[str, Any]],
    *,
    chapter_number: int,
    current_text: str = "",
) -> tuple[list[ReviewFinding], list[RepairTicket]]:
    """Normalize book-audit issues into shared findings and repair tickets."""
    text_hash = source_text_hash(current_text) if current_text else ""
    findings: list[ReviewFinding] = []
    for index, issue in enumerate(chapter_issues):
        if not isinstance(issue, dict):
            continue
        dimension = review_dimension_for_issue(issue)
        finding = normalize_issue_to_finding(
            issue,
            source_module="book_consistency_audit",
            chapter_number=chapter_number,
            dimension=dimension,
            current_text_hash=text_hash,
            metadata={
                "book_issue_index": index,
                "repair_lane": _book_issue_to_lane(issue),
            },
        )
        findings.append(finding)
    return findings, compile_repair_tickets_from_findings(findings)


def _book_issue_matches_selected_indices(
    *,
    issue: dict[str, Any],
    chapter_number: int,
    lane: str,
    selected_indices: set[int],
    report_issues: list[Any],
) -> bool:
    """Return whether a single book issue already maps to selected report issues."""
    if not selected_indices or not report_issues:
        return False
    ref_indices, _ = _collect_linked_issue_indices_for_lane(
        chapter_issues=[issue],
        chapter_number=chapter_number,
        lane=lane,
        max_issue_count=len(report_issues),
    )
    fuzzy_indices = _select_issue_indices_for_lane(
        book_issues=[issue],
        report_issues=report_issues,
        lane=lane,
    )
    para_indices = _select_issue_indices_by_paragraph(
        book_issues=[issue],
        report_issues=report_issues,
        lane=lane,
        threshold=2,
    )
    matched = set(ref_indices) | set(fuzzy_indices) | set(para_indices)
    return bool(matched & selected_indices)


def _unmatched_book_repair_tickets(
    *,
    chapter_issues: list[dict[str, Any]],
    tickets: list[RepairTicket],
    chapter_number: int,
    cont_indices: set[int],
    causal_indices: set[int],
    cont_report_issues: list[Any],
    causal_report_issues: list[Any],
) -> list[RepairTicket]:
    """Find repair tickets not covered by linked, fuzzy, or paragraph matching."""
    ticket_by_issue_index: dict[int, RepairTicket] = {}
    for ticket in tickets:
        index = ticket.metadata.get("book_issue_index")
        if isinstance(index, int):
            ticket_by_issue_index[index] = ticket

    unmatched: list[RepairTicket] = []
    for index, issue in enumerate(chapter_issues):
        if not isinstance(issue, dict):
            continue
        matched_ticket = ticket_by_issue_index.get(index)
        if matched_ticket is None:
            continue
        lane = _book_issue_to_lane(issue)
        if lane == "causal":
            matched = _book_issue_matches_selected_indices(
                issue=issue,
                chapter_number=chapter_number,
                lane="causal",
                selected_indices=causal_indices,
                report_issues=causal_report_issues,
            )
        else:
            matched = _book_issue_matches_selected_indices(
                issue=issue,
                chapter_number=chapter_number,
                lane="continuity",
                selected_indices=cont_indices,
                report_issues=cont_report_issues,
            )
        if not matched:
            unmatched.append(matched_ticket)
    return unmatched


def _ticket_verifications_for_detail(
    *,
    tickets: list[RepairTicket],
    detail: dict[str, Any],
    current_text: str = "",
) -> list[dict[str, Any]]:
    """Build normalized verification payloads for one repaired chapter detail."""
    if not tickets:
        return []
    text_hash = source_text_hash(current_text) if current_text else ""
    normalized_text = re.sub(r"\s+", "", current_text or "")
    status = str(detail.get("status", "") or "").strip().lower()
    if status == "skipped":
        result_status = "skipped"
        confidence = 0.2
    elif status == "failed":
        result_status = "failed"
        confidence = 0.0
    elif status == "blocked":
        result_status = "blocked"
        confidence = 0.9
    elif not bool(detail.get("applied", False)):
        result_status = "unresolved"
        confidence = 0.4
    else:
        post_check = detail.get("post_repair_check") or {}
        remaining = int(post_check.get("issues_remaining", 0) or 0)
        closed = int(post_check.get("issues_closed", 0) or 0)
        if remaining <= 0 and closed > 0:
            result_status = "resolved"
            confidence = 0.85
        elif closed > 0:
            result_status = "partial"
            confidence = 0.65
        else:
            result_status = "unresolved"
            confidence = 0.45
    remaining_details = (detail.get("post_repair_check") or {}).get("remaining_details") or []
    remaining_evidence = [
        str(item.get("evidence_snippet", "") or "")
        for item in remaining_details
        if isinstance(item, dict) and str(item.get("evidence_snippet", "") or "").strip()
    ]
    if detail.get("regression_count") or detail.get("critical_regression_count"):
        result_status = "regressed"
        confidence = 0.8
    results: list[dict[str, Any]] = []
    for ticket in tickets:
        ticket_status = result_status
        ticket_confidence = confidence
        ticket_remaining = list(remaining_evidence)
        if bool(detail.get("applied", False)) and result_status in {
            "resolved",
            "partial",
            "unresolved",
        }:
            original_issue = ticket.metadata.get("original_issue", {})
            ticket_evidence = str(ticket.metadata.get("evidence_quote", "") or "").strip()
            if not ticket_evidence and isinstance(original_issue, dict):
                ticket_evidence = str(
                    original_issue.get("evidence", "")
                    or original_issue.get("evidence_quote", "")
                    or ""
                ).strip()
            if len(ticket_evidence) >= 2:
                normalized_evidence = re.sub(r"\s+", "", ticket_evidence)
                if normalized_evidence and normalized_evidence not in normalized_text:
                    ticket_status = "resolved"
                    ticket_confidence = max(ticket_confidence, 0.8)
                    ticket_remaining = []
                else:
                    ticket_status = "unresolved"
                    ticket_confidence = max(ticket_confidence, 0.55)
                    ticket_remaining = [ticket_evidence[:80]]
        results.append(
            build_repair_verification_result(
                ticket,
                status=ticket_status,
                confidence=ticket_confidence,
                remaining_evidence=ticket_remaining,
                source_text_hash=text_hash,
                metadata={
                    "chapter_status": status,
                    "applied": bool(detail.get("applied", False)),
                    "match_mode": str(detail.get("match_mode", "") or ""),
                },
            ).model_dump(mode="json")
        )
    return results


_REPAIR_GUARD_SCHEMA_MARKERS: tuple[str, ...] = (
    "```json",
    "```",
    "repair_plan",
    "issue_id",
    "paragraph_index",
    "book_consistency",
    "continuity_issue_indices",
    "causal_issue_indices",
    "matched_issue_count",
    "manual_review_reason",
    "post_repair_check",
    '"issues"',
    '"issue_type"',
    '"severity"',
)

_REPAIR_GUARD_MARKDOWN_MARKERS: tuple[str, ...] = (
    "**",
    "\n###",
    "\n##",
    "\n- ",
    "\n* ",
)


def _book_repair_guard_enabled(
    *,
    request: BookConsistencyRequest,
    runtime: RuntimeServices,
) -> bool:
    """Return whether the post-write contamination guard should run."""
    fields_set: set[str] = getattr(request, "model_fields_set", set())
    if "repair_guard_enabled" in fields_set:
        return bool(getattr(request, "repair_guard_enabled", True))
    return bool(getattr(runtime.settings, "long_book_audit_repair_guard_enabled", True))


def _book_repair_guard_limits(
    *,
    request: BookConsistencyRequest,
    runtime: RuntimeServices,
) -> tuple[float, int]:
    fields_set: set[str] = getattr(request, "model_fields_set", set())
    raw_ratio = (
        getattr(request, "repair_guard_max_delta_ratio", None)
        if "repair_guard_max_delta_ratio" in fields_set
        else getattr(runtime.settings, "long_book_audit_repair_guard_max_delta_ratio", 0.20)
    )
    ratio = float(raw_ratio if raw_ratio is not None else 0.20)
    raw_added = (
        getattr(request, "repair_guard_max_added_chars", None)
        if "repair_guard_max_added_chars" in fields_set
        else getattr(runtime.settings, "long_book_audit_repair_guard_max_added_chars", 1200)
    )
    added = int(raw_added if raw_added is not None else 1200)
    return max(0.01, min(ratio, 1.0)), max(100, min(added, 10000))


def _read_repair_guard_chapter_text(
    layout: ProjectLayout,
    chapter_num: int,
) -> tuple[Path | None, str]:
    chapter_path = layout.chapter_path(chapter_num)
    review_path = layout.chapter_review_draft_path(chapter_num)
    if chapter_path.exists():
        return chapter_path, chapter_path.read_text(encoding="utf-8")
    if review_path.exists():
        return review_path, review_path.read_text(encoding="utf-8")
    return None, ""


def _repair_guard_normalize_unit(value: str) -> str:
    return re.sub(r"\s+", "", value).strip()


def _repair_guard_paragraphs(text: str) -> list[str]:
    paragraphs: list[str] = []
    for raw in re.split(r"\n\s*\n+", text):
        unit = _repair_guard_normalize_unit(raw)
        if len(unit) >= 40 and not unit.startswith("#"):
            paragraphs.append(unit)
    return paragraphs


def _repair_guard_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for raw in re.findall(r"[^。！？!?]+[。！？!?]", text):
        unit = _repair_guard_normalize_unit(raw)
        if len(unit) >= 18:
            sentences.append(unit)
    return sentences


def _repair_guard_top_repeat(
    old_units: list[str],
    new_units: list[str],
    *,
    min_count: int,
    min_growth: int,
) -> dict[str, Any] | None:
    old_counts = Counter(old_units)
    new_counts = Counter(new_units)
    candidates: list[tuple[str, int, int]] = []
    for unit, count in new_counts.items():
        growth = count - old_counts.get(unit, 0)
        if count >= min_count and growth >= min_growth:
            candidates.append((unit, count, growth))
    if not candidates:
        return None
    unit, count, growth = sorted(candidates, key=lambda item: (-item[1], -item[2], item[0]))[0]
    return {
        "sample": unit[:120],
        "count": count,
        "growth": growth,
    }


def _evaluate_book_repair_text_guard(
    original_text: str,
    repaired_text: str,
    *,
    request: BookConsistencyRequest,
    runtime: RuntimeServices,
) -> dict[str, Any]:
    """Detect obvious contamination in a just-written whole-book repair result."""
    max_delta_ratio, max_added_chars = _book_repair_guard_limits(
        request=request,
        runtime=runtime,
    )
    old_len = len(original_text)
    new_len = len(repaired_text)
    delta = new_len - old_len
    ratio = abs(delta) / max(old_len, 1)
    reasons: list[dict[str, Any]] = []

    schema_hits = [
        marker
        for marker in _REPAIR_GUARD_SCHEMA_MARKERS
        if marker in repaired_text and marker not in original_text
    ]
    if schema_hits:
        reasons.append(
            {
                "code": "schema_or_prompt_leak",
                "message": "检测到疑似提示词、JSON 或修复结构字段混入正文",
                "markers": schema_hits[:8],
            }
        )

    markdown_hits = [
        marker.strip()
        for marker in _REPAIR_GUARD_MARKDOWN_MARKERS
        if marker in repaired_text and marker not in original_text
    ]
    if markdown_hits:
        reasons.append(
            {
                "code": "markdown_intrusion",
                "message": "检测到 Markdown 标记被写入正文",
                "markers": markdown_hits[:8],
            }
        )

    paragraph_repeat = _repair_guard_top_repeat(
        _repair_guard_paragraphs(original_text),
        _repair_guard_paragraphs(repaired_text),
        min_count=3,
        min_growth=2,
    )
    if paragraph_repeat is not None:
        reasons.append(
            {
                "code": "paragraph_repetition_regression",
                "message": "检测到段落级异常重复增加",
                **paragraph_repeat,
            }
        )

    sentence_repeat = _repair_guard_top_repeat(
        _repair_guard_sentences(original_text),
        _repair_guard_sentences(repaired_text),
        min_count=4,
        min_growth=2,
    )
    if sentence_repeat is not None:
        reasons.append(
            {
                "code": "sentence_repetition_regression",
                "message": "检测到句子级异常重复增加",
                **sentence_repeat,
            }
        )

    if delta > max_added_chars:
        reasons.append(
            {
                "code": "added_chars_over_budget",
                "message": "单章新增字符数超过全书自动修复安全预算",
                "delta_chars": delta,
                "limit": max_added_chars,
            }
        )
    if abs(delta) >= 240 and ratio > max_delta_ratio:
        reasons.append(
            {
                "code": "delta_ratio_over_budget",
                "message": "单章改动比例超过全书自动修复安全预算",
                "delta_ratio": round(ratio, 4),
                "limit": max_delta_ratio,
            }
        )

    return {
        "enabled": True,
        "ok": not reasons,
        "delta_chars": delta,
        "delta_ratio": round(ratio, 4),
        "max_delta_ratio": max_delta_ratio,
        "max_added_chars": max_added_chars,
        "reason_codes": [str(item.get("code", "")) for item in reasons],
        "reasons": reasons,
    }


def _repair_guard_reason_text(guard: dict[str, Any]) -> str:
    reasons = guard.get("reasons")
    if not isinstance(reasons, list) or not reasons:
        return "修复结果未通过正文污染防护"
    messages = [
        str(item.get("message", "") or item.get("code", "") or "").strip()
        for item in reasons
        if isinstance(item, dict)
    ]
    return "；".join(msg for msg in messages if msg) or "修复结果未通过正文污染防护"


def _assert_auto_repair_outside_project_lock(project_id: str) -> None:
    """Fail fast when auto-repair is invoked while this task holds the project lock."""
    from novel_forge.core.infra.resource_locks import ResourceName, get_resource_lock_manager

    lock_mgr = get_resource_lock_manager()
    if hasattr(lock_mgr, "current_task_holds") and lock_mgr.current_task_holds(
        ResourceName.CANON,
        project_id=project_id,
    ):
        raise RuntimeError(
            "_run_book_consistency_auto_repair must be called outside the project CANON lock"
        )


async def _run_book_consistency_auto_repair(
    *,
    runtime: RuntimeServices,
    request: BookConsistencyRequest,
    report_issues: list[dict[str, Any]],
    chapter_issue_pool: list[dict[str, Any]] | None = None,
    on_step_progress: StepCallback = None,
    checkpoint_path: Path | None = None,
) -> dict[str, Any]:
    """Run auto-repair for book consistency audit issues.

    IMPORTANT: Must be called **outside** the project lock.  Internal helpers
    (v2 ``execute_repair`` and ``execute_reevaluate_chapter``) acquire their
    own project locks; holding an outer lock would cause a deadlock.
    """
    from novel_forge.core.schemas.chapter import CausalValidationReport
    from novel_forge.core.schemas.continuity import ContinuityReport

    _assert_auto_repair_outside_project_lock(request.project_id)
    layout = ProjectLayout(runtime.storage.existing_project_dir(request.project_id))
    from novel_forge.persistence.foundation_guard import require_versioned_maintenance_write

    require_versioned_maintenance_write(layout.root, "直接执行全书自动修复")
    _min_severity = str(getattr(request, "repair_min_severity", "warning") or "warning")
    _max_chapters = int(getattr(request, "repair_max_chapters", 20) or 20)
    admission_summary = summarize_auto_repair_admission(
        report_issues,
        min_severity=_min_severity,
    )

    # Compute full set of issue chapters (before max_chapters truncation) for tracking
    _all_issue_chapters_ranked = _group_book_issues_by_chapter(
        report_issues,
        min_severity=_min_severity,
        max_chapters=0,  # no limit — get all
    )
    chapter_groups = (
        _all_issue_chapters_ranked[:_max_chapters]
        if _max_chapters > 0
        else _all_issue_chapters_ranked
    )
    targeted_chapter_nums = {ch for ch, _ in chapter_groups}
    excluded_chapters = sorted(
        ch for ch, _ in _all_issue_chapters_ranked if ch not in targeted_chapter_nums
    )
    all_chapter_nums = sorted(
        {ch for ch, _ in _all_issue_chapters_ranked}
        | set(excluded_chapters)
        | targeted_chapter_nums
    )

    repair_concurrency = int(
        getattr(
            request,
            "repair_concurrency",
            int(getattr(runtime.settings, "long_book_audit_repair_concurrency", 1) or 1),
        )
        or 1
    )
    repair_concurrency = max(1, min(repair_concurrency, 8))
    total_chapters = len(chapter_groups)
    checkpoint_signature = _repair_checkpoint_signature(
        chapter_groups,
        min_severity=_min_severity,
        max_chapters=_max_chapters,
    )

    completed_chapters: set[int] = set()
    applied_chapters: set[int] = set()
    cp = _load_repair_checkpoint(checkpoint_path, expected_signature=checkpoint_signature)
    if cp is not None:
        prev = [int(x) for x in cp.get("completed_chapters", []) if isinstance(x, (int, str))]
        completed_chapters = set(prev)
        applied_prev = [int(x) for x in cp.get("applied_chapters", []) if isinstance(x, (int, str))]
        applied_chapters = set(applied_prev)
        pending_chapter_nums = (ch for ch, _ in chapter_groups if ch not in completed_chapters)
        resume_from = min(pending_chapter_nums, default=None)
        if resume_from is not None:
            _log.info(
                "book_consistency_repair: resuming from chapter %d "
                "(%d/%d already completed, %d applied)",
                resume_from,
                len(completed_chapters),
                total_chapters,
                len(applied_chapters),
            )
        else:
            _log.info("book_consistency_repair: all chapters already repaired, skipping")

    if on_step_progress:
        on_step_progress(
            "book_consistency_repair_start",
            {
                "status": "queued",
                "targeted": total_chapters,
                "repair_concurrency": repair_concurrency,
                "eligible_issues": int(admission_summary.get("eligible_issue_count", 0) or 0),
                "ineligible_issues": int(
                    admission_summary.get("ineligible_issue_count", 0) or 0
                ),
            },
        )

    details_by_order: dict[int, dict[str, Any]] = {}
    progress_lock = asyncio.Lock()
    progress_state = {
        "processed": 0,
        "applied": 0,
        "matched_by_ref": 0,
        "matched_by_fuzzy": 0,
        "matched_by_para": 0,
        "matched_by_ticket": 0,
        "panel_expanded": 0,
    }

    # ── Serial repair execution ─────────────────────────────────────────
    # All repairs are serialized by project-level CANON EXCLUSIVE lock
    # (held inside _process_chapter via execution_repair_common._project_lock).
    # This ensures canon state remains consistent when multiple chapters
    # share characters/timelines. The _is_canon_modifying helper below is
    # kept as documentation of which issue types would require serialization
    # if we ever supported true concurrent text-only repairs.

    _CANON_MODIFYING_CATEGORIES: frozenset[str] = frozenset(
        {
            "character_state",
            "timeline",
        }
    )
    _CANON_MODIFYING_FIX_MODES: frozenset[str] = frozenset(
        {
            "repair_causal",
        }
    )

    def _is_canon_modifying(issues: list[dict[str, Any]]) -> bool:
        """Check if any issue in the list may modify canon state.

        NOTE: This function is kept for documentation purposes. All repairs
        are now serialized regardless of canon-modifying status.
        """
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            category = str(issue.get("category", "") or "").strip().lower()
            fix_mode = str(issue.get("fix_mode", "") or "").strip().lower()
            issue_type = str(issue.get("issue_type", "") or "").strip().lower()
            if category in _CANON_MODIFYING_CATEGORIES:
                return True
            if fix_mode in _CANON_MODIFYING_FIX_MODES:
                return True
            from novel_forge.workspace.helpers.execution_helpers import _STATE_INHERIT_ISSUE_TYPES

            if issue_type in _STATE_INHERIT_ISSUE_TYPES:
                return True
        return False

    async def _process_chapter(
        order: int,
        chapter_num: int,
        chapter_issues: list[dict[str, Any]],
    ) -> tuple[int, dict[str, Any]]:
        issue_focus = _summarize_book_issue_focus(chapter_issues)
        if on_step_progress:
            on_step_progress(
                "book_consistency_repair_progress",
                {
                    "status": "matching",
                    "chapter_number": chapter_num,
                    "issue_count": len(chapter_issues),
                    "issue_focus": issue_focus,
                    "order": order + 1,  # 1-based queue position for display
                    "processed": progress_state["processed"],  # completed so far
                    "total": total_chapters,
                },
            )

        cont_report = None
        causal_report = None
        cont_path = layout.continuity_report_path(chapter_num)
        causal_path = layout.chapter_causal_report_path(chapter_num)

        if runtime.storage.exists(cont_path):
            try:
                cont_report = ContinuityReport.model_validate(runtime.storage.load_json(cont_path))
            except (OSError, StorageError, ValidationError):
                cont_report = None
        if runtime.storage.exists(causal_path):
            try:
                causal_report = CausalValidationReport.model_validate(
                    runtime.storage.load_json(causal_path)
                )
            except (OSError, StorageError, ValidationError):
                causal_report = None

        if cont_report is None or causal_report is None:
            try:
                await execute_reevaluate_chapter(
                    runtime,
                    ReevaluateChapterRequest(
                        project_id=request.project_id, chapter_number=chapter_num
                    ),
                    on_step_progress=None,
                )
                if runtime.storage.exists(cont_path):
                    cont_report = ContinuityReport.model_validate(
                        runtime.storage.load_json(cont_path)
                    )
                if runtime.storage.exists(causal_path):
                    causal_report = CausalValidationReport.model_validate(
                        runtime.storage.load_json(causal_path)
                    )
            except Exception as exc:  # noqa: BLE001
                _log.debug("Failed to load causal report for ch%d: %s", chapter_num, exc)

        cont_report_issues = list(getattr(cont_report, "issues", []) or [])
        causal_report_issues = list(getattr(causal_report, "issues", []) or [])

        cont_ref_indices, cont_linked_issues = _collect_linked_issue_indices_for_lane(
            chapter_issues=chapter_issues,
            chapter_number=chapter_num,
            lane="continuity",
            max_issue_count=len(cont_report_issues),
        )
        causal_ref_indices, causal_linked_issues = _collect_linked_issue_indices_for_lane(
            chapter_issues=chapter_issues,
            chapter_number=chapter_num,
            lane="causal",
            max_issue_count=len(causal_report_issues),
        )

        cont_indices_set = set(cont_ref_indices)
        causal_indices_set = set(causal_ref_indices)

        cont_fuzzy_candidates = _select_issue_indices_for_lane(
            book_issues=chapter_issues,
            report_issues=cont_report_issues,
            lane="continuity",
        )
        causal_fuzzy_candidates = _select_issue_indices_for_lane(
            book_issues=chapter_issues,
            report_issues=causal_report_issues,
            lane="causal",
        )

        fuzzy_added = 0
        for idx in cont_fuzzy_candidates:
            if idx not in cont_indices_set:
                cont_indices_set.add(idx)
                fuzzy_added += 1
        for idx in causal_fuzzy_candidates:
            if idx not in causal_indices_set:
                causal_indices_set.add(idx)
                fuzzy_added += 1

        cont_para_candidates = _select_issue_indices_by_paragraph(
            book_issues=chapter_issues,
            report_issues=cont_report_issues,
            lane="continuity",
            threshold=2,
        )
        causal_para_candidates = _select_issue_indices_by_paragraph(
            book_issues=chapter_issues,
            report_issues=causal_report_issues,
            lane="causal",
            threshold=2,
        )

        para_added = 0
        for idx in cont_para_candidates:
            if idx not in cont_indices_set:
                cont_indices_set.add(idx)
                para_added += 1
        for idx in causal_para_candidates:
            if idx not in causal_indices_set:
                causal_indices_set.add(idx)
                para_added += 1

        ref_matched = len(cont_ref_indices) + len(causal_ref_indices)

        # ── Panel-first expansion ─────────────────────────────────────
        # Opportunistically include a small number of critical/high panel
        # issues not already matched by book-audit, so we make progress
        # on outstanding single-chapter problems while in the chapter.
        # Capped at 3 per lane to avoid scope creep — uncapped expansion
        # caused unrelated issues to be repaired and sometimes worsened.
        _enable_panel_first = bool(
            getattr(
                request,
                "panel_first_expansion",
                bool(getattr(runtime.settings, "long_book_audit_panel_first_expansion", True)),
            )
        )
        _PANEL_MAX_EXTRA = 3
        panel_cont_added = 0
        if _enable_panel_first:
            cont_extra_indices = _select_panel_first_expansion_indices(
                cont_report_issues,
                cont_indices_set,
                max_extra=_PANEL_MAX_EXTRA,
            )
            for idx in cont_extra_indices:
                cont_indices_set.add(idx)
                panel_cont_added += 1
        panel_causal_added = 0
        if _enable_panel_first:
            causal_extra_indices = _select_panel_first_expansion_indices(
                causal_report_issues,
                causal_indices_set,
                max_extra=_PANEL_MAX_EXTRA,
            )
            for idx in causal_extra_indices:
                causal_indices_set.add(idx)
                panel_causal_added += 1
        panel_added = panel_cont_added + panel_causal_added
        # ──────────────────────────────────────────────────────────────

        cont_indices = sorted(cont_indices_set)
        causal_indices = sorted(causal_indices_set)

        guard_enabled = _book_repair_guard_enabled(request=request, runtime=runtime)
        original_text_path: Path | None = None
        original_text = ""
        guard_read_error = ""
        try:
            original_text_path, original_text = _read_repair_guard_chapter_text(
                layout,
                chapter_num,
            )
        except OSError as exc:
            guard_read_error = str(exc)

        review_findings, repair_tickets = _build_book_repair_contracts(
            chapter_issues,
            chapter_number=chapter_num,
            current_text=original_text,
        )
        unmatched_tickets = _unmatched_book_repair_tickets(
            chapter_issues=chapter_issues,
            tickets=repair_tickets,
            chapter_number=chapter_num,
            cont_indices=set(cont_indices),
            causal_indices=set(causal_indices),
            cont_report_issues=cont_report_issues,
            causal_report_issues=causal_report_issues,
        )
        cont_synthetic_issues: list[dict[str, Any]] = []
        causal_synthetic_issues: list[dict[str, Any]] = []
        for ticket in unmatched_tickets:
            lane = str(ticket.metadata.get("repair_lane") or ticket.dimension or "").lower()
            if lane == "causal":
                causal_synthetic_issues.append(repair_ticket_to_causal_issue_payload(ticket))
            else:
                cont_synthetic_issues.append(repair_ticket_to_continuity_issue_payload(ticket))
        ticket_matched = len(cont_synthetic_issues) + len(causal_synthetic_issues)

        if not cont_indices and not causal_indices and not ticket_matched:
            detail = {
                "chapter_number": chapter_num,
                "status": "skipped",
                "reason": "未匹配到可执行修复索引",
                "continuity_issue_indices": [],
                "causal_issue_indices": [],
                "continuity_synthetic_issue_count": 0,
                "causal_synthetic_issue_count": 0,
                "matched_issue_count": len(chapter_issues),
                "match_mode": "none",
                "matched_by_ref": ref_matched,
                "matched_by_fuzzy": fuzzy_added,
                "matched_by_para": para_added,
                "matched_by_ticket": 0,
                "panel_expanded": panel_added,
                "linked_issue_count": cont_linked_issues + causal_linked_issues,
                "review_findings": [finding.model_dump(mode="json") for finding in review_findings],
                "repair_tickets": [ticket.model_dump(mode="json") for ticket in repair_tickets],
            }
            detail["verification_results"] = _ticket_verifications_for_detail(
                tickets=repair_tickets,
                detail=detail,
                current_text=original_text,
            )
            async with progress_lock:
                progress_state["processed"] += 1
                progress_state["matched_by_ref"] += ref_matched
                progress_state["matched_by_fuzzy"] += fuzzy_added
                progress_state["matched_by_para"] += para_added
                progress_state["matched_by_ticket"] += ticket_matched
                progress_state["panel_expanded"] += panel_added
                completed_chapters.add(chapter_num)
                _save_repair_checkpoint(
                    checkpoint_path,
                    {
                        "schema_version": 1,
                        "signature": checkpoint_signature,
                        "completed_chapters": sorted(completed_chapters),
                        "applied_chapters": sorted(applied_chapters),
                        "total_chapters": total_chapters,
                        "status": "running",
                        "checksum": _compute_repair_checksum(sorted(completed_chapters)),
                    },
                )
                if on_step_progress:
                    on_step_progress(
                        "book_consistency_repair_progress",
                        {
                            "status": "skipped",
                            "chapter_number": chapter_num,
                            "issue_count": len(chapter_issues),
                            "issue_focus": issue_focus,
                            "processed": progress_state["processed"],
                            "applied": progress_state["applied"],
                            "total": total_chapters,
                            "matched_by_ref": progress_state["matched_by_ref"],
                            "matched_by_fuzzy": progress_state["matched_by_fuzzy"],
                            "matched_by_ticket": progress_state["matched_by_ticket"],
                            "panel_expanded": progress_state["panel_expanded"],
                        },
                    )
            return order, detail

        if guard_enabled and (guard_read_error or original_text_path is None):
            guard_reason = (
                f"修复前正文读取失败，已阻断自动写入: {guard_read_error}"
                if guard_read_error
                else "未找到修复前正文，已阻断自动写入"
            )
            detail = {
                "chapter_number": chapter_num,
                "status": "blocked",
                "reason": guard_reason,
                "applied": False,
                "continuity_issue_indices": cont_indices,
                "causal_issue_indices": causal_indices,
                "continuity_synthetic_issue_count": len(cont_synthetic_issues),
                "causal_synthetic_issue_count": len(causal_synthetic_issues),
                "matched_issue_count": len(chapter_issues),
                "match_mode": "guard_preflight",
                "matched_by_ref": ref_matched,
                "matched_by_fuzzy": fuzzy_added,
                "matched_by_para": para_added,
                "matched_by_ticket": ticket_matched,
                "panel_expanded": panel_added,
                "linked_issue_count": cont_linked_issues + causal_linked_issues,
                "guard_blocked": True,
                "needs_manual_review": True,
                "manual_review_reason": guard_reason,
                "review_findings": [finding.model_dump(mode="json") for finding in review_findings],
                "repair_tickets": [ticket.model_dump(mode="json") for ticket in repair_tickets],
                "_source_issues": chapter_issues,
                "repair_guard": {
                    "enabled": True,
                    "ok": False,
                    "blocked_before_write": True,
                    "reason": guard_reason,
                    "reasons": [
                        {
                            "code": "pre_repair_text_unavailable",
                            "message": guard_reason,
                        }
                    ],
                    "reason_codes": ["pre_repair_text_unavailable"],
                },
            }
            detail["verification_results"] = _ticket_verifications_for_detail(
                tickets=repair_tickets,
                detail=detail,
                current_text=original_text,
            )
            async with progress_lock:
                progress_state["processed"] += 1
                progress_state["matched_by_ref"] += ref_matched
                progress_state["matched_by_fuzzy"] += fuzzy_added
                progress_state["matched_by_para"] += para_added
                progress_state["matched_by_ticket"] += ticket_matched
                progress_state["panel_expanded"] += panel_added
                completed_chapters.add(chapter_num)
                _save_repair_checkpoint(
                    checkpoint_path,
                    {
                        "schema_version": 1,
                        "signature": checkpoint_signature,
                        "completed_chapters": sorted(completed_chapters),
                        "applied_chapters": sorted(applied_chapters),
                        "total_chapters": total_chapters,
                        "status": "running",
                        "checksum": _compute_repair_checksum(sorted(completed_chapters)),
                    },
                )
                if on_step_progress:
                    on_step_progress(
                        "book_consistency_repair_progress",
                        {
                            "status": "blocked",
                            "chapter_number": chapter_num,
                            "issue_count": len(chapter_issues),
                            "issue_focus": issue_focus,
                            "processed": progress_state["processed"],
                            "applied": progress_state["applied"],
                            "total": total_chapters,
                            "matched_by_ref": progress_state["matched_by_ref"],
                            "matched_by_fuzzy": progress_state["matched_by_fuzzy"],
                            "matched_by_para": progress_state["matched_by_para"],
                            "matched_by_ticket": progress_state["matched_by_ticket"],
                            "panel_expanded": progress_state["panel_expanded"],
                        },
                    )
            return order, detail

        repair_req = RepairIssuesRequest(
            project_id=request.project_id,
            chapter_number=chapter_num,
            continuity_issue_indices=cont_indices,
            causal_issue_indices=causal_indices,
            continuity_synthetic_issues=cont_synthetic_issues,
            causal_synthetic_issues=causal_synthetic_issues,
            allow_exhausted_retry=bool(getattr(request, "allow_exhausted_retry", False)),
        )

        try:
            repair_mission = issues_repair_mission(
                repair_req,
                settings=runtime.settings,
                include_empty_causal_target=True,
            )
            repair_execution = await execute_repair(
                runtime,
                repair_mission,
                on_step_progress=None,
            )
            repair_result = repair_execution.result
            applied = bool(getattr(repair_result, "applied", False))
            continuity_applied = bool(getattr(repair_result, "continuity_applied", False))
            causal_applied = bool(getattr(repair_result, "causal_applied", False))
            for attempt in getattr(repair_result, "attempts", []) or []:
                domain = getattr(getattr(attempt, "domain", ""), "value", getattr(attempt, "domain", ""))
                status = getattr(getattr(attempt, "status", ""), "value", getattr(attempt, "status", ""))
                if status in {"applied", "verified"}:
                    if domain == "continuity":
                        continuity_applied = True
                    elif domain == "causal":
                        causal_applied = True
            repair_failure_reasons: list[str] = []
            if not applied:
                outcome_failure = str(getattr(repair_result, "failure_reason", "") or "").strip()
                if outcome_failure:
                    repair_failure_reasons.append(outcome_failure)
                repair_failure_reasons.extend(
                    str(w).strip()
                    for w in (getattr(repair_result, "warnings", []) or [])
                    if str(w).strip()
                )
                continuity_failure = str(
                    getattr(
                        getattr(repair_result, "continuity_result", None),
                        "failure_reason",
                        "",
                    )
                    or ""
                ).strip()
                causal_failure = str(
                    getattr(
                        getattr(repair_result, "causal_result", None),
                        "failure_reason",
                        "",
                    )
                    or ""
                ).strip()
                if continuity_failure:
                    repair_failure_reasons.append(f"连贯性修复未写入：{continuity_failure}")
                if causal_failure:
                    repair_failure_reasons.append(f"因果修复未写入：{causal_failure}")
                if not repair_failure_reasons:
                    repair_failure_reasons.append("修复器执行完成但未生成可写入正文改动")
            _mode_parts: list[str] = []
            if panel_added > 0:
                _mode_parts.append("panel")
            if ref_matched > 0:
                _mode_parts.append("ref")
            if fuzzy_added > 0:
                _mode_parts.append("fuzzy")
            if para_added > 0:
                _mode_parts.append("para")
            if ticket_matched > 0:
                _mode_parts.append("ticket")
            detail = {
                "chapter_number": chapter_num,
                "status": "applied" if applied else "no_change",
                "reason": "" if applied else "；".join(repair_failure_reasons),
                "applied": applied,
                "continuity_issue_indices": cont_indices,
                "causal_issue_indices": causal_indices,
                "continuity_synthetic_issue_count": len(cont_synthetic_issues),
                "causal_synthetic_issue_count": len(causal_synthetic_issues),
                "matched_issue_count": len(chapter_issues),
                "continuity_applied": continuity_applied,
                "causal_applied": causal_applied,
                "match_mode": "+".join(_mode_parts) if _mode_parts else "none",
                "matched_by_ref": ref_matched,
                "matched_by_fuzzy": fuzzy_added,
                "matched_by_para": para_added,
                "matched_by_ticket": ticket_matched,
                "panel_expanded": panel_added,
                "linked_issue_count": cont_linked_issues + causal_linked_issues,
                "synthetic_ticket_ids": [str(ticket.ticket_id) for ticket in unmatched_tickets],
                "review_findings": [finding.model_dump(mode="json") for finding in review_findings],
                "repair_tickets": [ticket.model_dump(mode="json") for ticket in repair_tickets],
                "_source_issues": chapter_issues,
                # Store texts for propagation validation (internal, stripped before return)
                "_propagation_original_text": original_text or "",
                "_propagation_repaired_text": "",  # filled after repair is applied
            }

            # ── Post-write contamination guard ───────────────────────
            if applied and guard_enabled:
                if guard_read_error:
                    detail["repair_guard"] = {
                        "enabled": True,
                        "ok": True,
                        "skipped": True,
                        "reason": f"修复前正文读取失败，已跳过防污染对比: {guard_read_error}",
                    }
                elif original_text_path is None:
                    detail["repair_guard"] = {
                        "enabled": True,
                        "ok": True,
                        "skipped": True,
                        "reason": "未找到修复前正文，已跳过防污染对比",
                    }
                else:
                    try:
                        _, repaired_text = _read_repair_guard_chapter_text(
                            layout,
                            chapter_num,
                        )
                        detail["_propagation_repaired_text"] = repaired_text
                        guard = _evaluate_book_repair_text_guard(
                            original_text,
                            repaired_text,
                            request=request,
                            runtime=runtime,
                        )
                        detail["repair_guard"] = guard
                        if not bool(guard.get("ok", False)):
                            reason_codes = set(guard.get("reason_codes", []))
                            is_schema_leak = "schema_or_prompt_leak" in reason_codes
                            is_markdown_intrusion = "markdown_intrusion" in reason_codes
                            if is_schema_leak or is_markdown_intrusion:
                                try:
                                    atomic_write_text(original_text_path, original_text)
                                    guard["rolled_back"] = True
                                    detail["status"] = "blocked"
                                    detail["reason"] = _repair_guard_reason_text(guard)
                                except OSError as exc:
                                    guard["rolled_back"] = False
                                    guard["rollback_error"] = str(exc)
                                    detail["status"] = "failed"
                                    detail["reason"] = (
                                        _repair_guard_reason_text(guard)
                                        + f"；回滚失败: {type(exc).__name__}"
                                    )
                                detail["applied"] = False
                                detail["guard_blocked"] = True
                                detail["needs_manual_review"] = True
                                detail["manual_review_reason"] = detail["reason"]
                                detail["raw_continuity_applied"] = detail["continuity_applied"]
                                detail["raw_causal_applied"] = detail["causal_applied"]
                                detail["continuity_applied"] = False
                                detail["causal_applied"] = False
                                applied = False
                            else:
                                guard["rolled_back"] = False
                                guard["kept_with_warning"] = True
                                guard["warning"] = "仅超预算但无提示词泄漏，保留修复结果待人工复核"
                                detail["status"] = "applied"
                                detail["reason"] = _repair_guard_reason_text(guard)
                                detail["applied"] = True
                                detail["guard_blocked"] = False
                                detail["guard_budget_exceeded"] = True
                                detail["needs_manual_review"] = True
                                detail["manual_review_reason"] = (
                                    "修复超预算但无泄漏，需人工确认: " + str(detail["reason"])
                                )
                                detail["raw_continuity_applied"] = detail["continuity_applied"]
                                detail["raw_causal_applied"] = detail["causal_applied"]
                    except (OSError, TypeError, ValueError) as exc:
                        _log.debug(
                            "Post-write repair guard failed for chapter %d",
                            chapter_num,
                            exc_info=True,
                        )
                        rollback_error = ""
                        rolled_back = False
                        try:
                            atomic_write_text(original_text_path, original_text)
                            rolled_back = True
                        except OSError as rollback_exc:
                            rollback_error = str(rollback_exc)
                        repair_guard_detail: dict[str, Any] = {
                            "enabled": True,
                            "ok": False,
                            "reason": f"防污染对比执行异常，已阻断自动写入: {type(exc).__name__}",
                            "rolled_back": rolled_back,
                            "rollback_error": rollback_error,
                            "reason_codes": ["post_repair_guard_failed"],
                            "reasons": [
                                {
                                    "code": "post_repair_guard_failed",
                                    "message": "防污染对比执行异常，已阻断自动写入",
                                }
                            ],
                        }
                        detail["repair_guard"] = repair_guard_detail
                        detail["status"] = "blocked" if rolled_back else "failed"
                        detail["reason"] = _repair_guard_reason_text(repair_guard_detail)
                        detail["applied"] = False
                        detail["guard_blocked"] = True
                        detail["needs_manual_review"] = True
                        detail["manual_review_reason"] = detail["reason"]
                        detail["raw_continuity_applied"] = detail["continuity_applied"]
                        detail["raw_causal_applied"] = detail["causal_applied"]
                        detail["continuity_applied"] = False
                        detail["causal_applied"] = False
                        applied = False

            # ── Post-repair local evidence check ──────────────────────
            _new_text = ""
            if applied:
                try:
                    _ch_path = layout.chapter_path(chapter_num)
                    _rv_path = layout.chapter_review_draft_path(chapter_num)
                    if _ch_path.exists():
                        _new_text = _ch_path.read_text(encoding="utf-8")
                    elif _rv_path.exists():
                        _new_text = _rv_path.read_text(encoding="utf-8")
                    if _new_text:
                        detail["_propagation_repaired_text"] = _new_text
                        _check = _post_repair_evidence_check(_new_text, chapter_issues)
                        detail["post_repair_check"] = _check
                        if _check["issues_remaining"] > 0 and _check["issues_closed"] == 0:
                            detail["needs_manual_review"] = True
                            detail["manual_review_reason"] = "修复后所有原问题证据仍存在于文本中"
                except (OSError, TypeError, ValueError):
                    _log.debug(
                        "Post-repair evidence check failed for chapter %d",
                        chapter_num,
                        exc_info=True,
                    )  # non-critical; skip check on read failure

            # ── Regression detection ──────────────────────────────────
            if applied:
                try:
                    from novel_forge.story_kernel.store import StoryKernelStore

                    # Load canon state for entity/state validation
                    canon_state_dict: dict[str, Any] | None = None
                    try:
                        canon_store = StoryKernelStore(layout.story_kernel_db_path)
                        canon_state_obj = await canon_store.load_kernel(request.project_id)
                        canon_state_dict = (
                            canon_state_obj.model_dump(mode="json")
                            if hasattr(canon_state_obj, "model_dump")
                            else {}
                        )
                    except (OSError, StorageError, ValidationError):
                        _log.debug(
                            "Regression detector: canon state load failed for chapter %d",
                            chapter_num,
                            exc_info=True,
                        )

                    # Build all_chapter_texts for cross-chapter narrative checks
                    all_chapter_texts: dict[int, str] = {}
                    for ch_num in all_chapter_nums:
                        ch_path = layout.chapter_path(ch_num)
                        rv_path = layout.chapter_review_draft_path(ch_num)
                        ch_text = ""
                        if ch_path.exists():
                            ch_text = ch_path.read_text(encoding="utf-8")
                        elif rv_path.exists():
                            ch_text = rv_path.read_text(encoding="utf-8")
                        if ch_text:
                            all_chapter_texts[ch_num] = ch_text

                    # Run regression detection
                    detector = RegressionDetector()
                    repaired_for_regression = _new_text or str(
                        detail.get("_propagation_repaired_text", "") or ""
                    )
                    regressions = await detector.detect_regressions(
                        original_text=original_text if original_text else "",
                        repaired_text=repaired_for_regression,
                        chapter_issues=chapter_issues,
                        all_chapter_texts=all_chapter_texts,
                        canon_state=canon_state_dict,
                        chapter_number=chapter_num,
                    )

                    if regressions:
                        detail["regressions"] = [r.model_dump() for r in regressions]
                        detail["regression_count"] = len(regressions)
                        detail["regression_adjudication"] = "candidate_only"
                        detail["regression_note"] = (
                            "本地仅提供格式化候选信号；是否构成真实回归交由 LLM/人工复核。"
                        )

                        critical_regressions = [r for r in regressions if r.severity == "critical"]
                        if critical_regressions:
                            detail["critical_regression_count"] = len(critical_regressions)
                            detail["regression_review_hint"] = (
                                f"本地候选包含 {len(critical_regressions)} 个严重级别信号，"
                                "需由 LLM 复核后再决定是否回滚。"
                            )

                except (OSError, TypeError, ValueError):
                    _log.debug(
                        "Regression detection failed for chapter %d",
                        chapter_num,
                        exc_info=True,
                    )  # non-critical; skip on failure
            verification_text = str(
                detail.get("_propagation_repaired_text", "") or original_text or ""
            )
            detail["verification_results"] = _ticket_verifications_for_detail(
                tickets=repair_tickets,
                detail=detail,
                current_text=verification_text,
            )
        except Exception as exc:
            detail = {
                "chapter_number": chapter_num,
                "status": "failed",
                "reason": str(exc),
                "continuity_issue_indices": cont_indices,
                "causal_issue_indices": causal_indices,
                "continuity_synthetic_issue_count": len(cont_synthetic_issues),
                "causal_synthetic_issue_count": len(causal_synthetic_issues),
                "matched_issue_count": len(chapter_issues),
                "match_mode": "failed",
                "matched_by_ref": ref_matched,
                "matched_by_fuzzy": fuzzy_added,
                "matched_by_para": para_added,
                "matched_by_ticket": ticket_matched,
                "panel_expanded": panel_added,
                "linked_issue_count": cont_linked_issues + causal_linked_issues,
                "needs_manual_review": True,
                "manual_review_reason": f"修复过程异常: {type(exc).__name__}",
                "review_findings": [finding.model_dump(mode="json") for finding in review_findings],
                "repair_tickets": [ticket.model_dump(mode="json") for ticket in repair_tickets],
                "_source_issues": chapter_issues,
            }
            detail["verification_results"] = _ticket_verifications_for_detail(
                tickets=repair_tickets,
                detail=detail,
                current_text=original_text,
            )
            applied = False

        async with progress_lock:
            progress_state["processed"] += 1
            progress_state["matched_by_ref"] += ref_matched
            progress_state["matched_by_fuzzy"] += fuzzy_added
            progress_state["matched_by_para"] += para_added
            progress_state["matched_by_ticket"] += ticket_matched
            progress_state["panel_expanded"] += panel_added
            if detail.get("applied"):
                progress_state["applied"] += 1
                applied_chapters.add(chapter_num)
            completed_chapters.add(chapter_num)
            _save_repair_checkpoint(
                checkpoint_path,
                {
                    "schema_version": 1,
                    "signature": checkpoint_signature,
                    "completed_chapters": sorted(completed_chapters),
                    "applied_chapters": sorted(applied_chapters),
                    "total_chapters": total_chapters,
                    "status": "running",
                    "checksum": _compute_repair_checksum(sorted(completed_chapters)),
                },
            )
            if on_step_progress:
                on_step_progress(
                    "book_consistency_repair_progress",
                    {
                        "status": str(detail.get("status", "")),
                        "chapter_number": chapter_num,
                        "issue_count": len(chapter_issues),
                        "issue_focus": issue_focus,
                        "processed": progress_state["processed"],
                        "applied": progress_state["applied"],
                        "total": total_chapters,
                        "matched_by_ref": progress_state["matched_by_ref"],
                        "matched_by_fuzzy": progress_state["matched_by_fuzzy"],
                        "matched_by_para": progress_state["matched_by_para"],
                        "matched_by_ticket": progress_state["matched_by_ticket"],
                        "panel_expanded": progress_state["panel_expanded"],
                    },
                )
        return order, detail

    if not chapter_groups:
        details: list[dict[str, Any]] = []
    else:
        if repair_concurrency > 1 and len(chapter_groups) > 1:
            _repair_sem = asyncio.Semaphore(repair_concurrency)

            pending = [
                (order, ch, issues)
                for order, (ch, issues) in enumerate(chapter_groups)
                if ch not in completed_chapters
            ]

            async def _run_guarded(
                order: int, ch: int, issues: list[dict[str, Any]]
            ) -> tuple[int, dict[str, Any]]:
                async with _repair_sem:
                    return await _process_chapter(order, ch, issues)

            tasks = [_run_guarded(order, ch, issues) for order, ch, issues in pending]
            for coro in asyncio.as_completed(tasks):
                order, detail = await coro
                details_by_order[order] = detail
        else:
            for order, (chapter_num, chapter_issues) in enumerate(chapter_groups):
                if chapter_num in completed_chapters:
                    continue
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    raise asyncio.CancelledError
                order_result, detail = await _process_chapter(
                    order,
                    chapter_num,
                    chapter_issues,
                )
                details_by_order[order_result] = detail

        details = [details_by_order[idx] for idx in sorted(details_by_order)]

    repair_metrics = summarize_book_repair_details(
        details,
        targeted_chapters=len(chapter_groups),
    )
    applied_count = int(repair_metrics["applied_chapters"])
    failed_count = int(repair_metrics["failed_chapters"])
    blocked_count = int(repair_metrics["blocked_chapters"])
    pool_size = len(chapter_issue_pool or [])

    if on_step_progress:
        on_step_progress(
            "book_consistency_repair_progress",
            {
                "status": "done",
                "processed": int(repair_metrics["processed_chapters"]),
                "applied": applied_count,
                "applied_writes": int(repair_metrics["applied_write_count"]),
                "failed": failed_count,
                "blocked": blocked_count,
                "total": total_chapters,
                "matched_by_ref": int(progress_state["matched_by_ref"]),
                "matched_by_fuzzy": int(progress_state["matched_by_fuzzy"]),
                "matched_by_para": int(progress_state["matched_by_para"]),
                "matched_by_ticket": int(progress_state["matched_by_ticket"]),
                "panel_expanded": int(progress_state["panel_expanded"]),
            },
        )

    # ── Post-repair aggregation ─────────────────────────────────────────
    # Collect needs_manual_review and post-repair stats
    needs_manual_review_chs = list(repair_metrics["needs_manual_review"])
    blocked_chapter_nums = list(repair_metrics["blocked_chapter_numbers"])

    total_issues_checked = 0
    total_issues_closed = 0
    total_issues_remaining = 0
    for d in details:
        prc = d.get("post_repair_check")
        if isinstance(prc, dict):
            total_issues_checked += int(prc.get("issues_checked", 0))
            total_issues_closed += int(prc.get("issues_closed", 0))
            total_issues_remaining += int(prc.get("issues_remaining", 0))

    all_verification_results: list[dict[str, Any]] = []
    verification_by_status: dict[str, int] = {}
    for d in details:
        verifications = d.get("verification_results") or []
        if not isinstance(verifications, list):
            continue
        for item in verifications:
            if not isinstance(item, dict):
                continue
            all_verification_results.append(item)
            status = str(item.get("status", "unknown") or "unknown")
            verification_by_status[status] = verification_by_status.get(status, 0) + 1

    # Regression aggregation
    total_regressions = 0
    regression_by_type: dict[str, int] = {}
    regression_by_severity: dict[str, int] = {}
    chapters_with_regressions: list[int] = []
    chapters_with_critical_regressions: list[int] = []
    all_regression_issues: list[dict[str, Any]] = []

    for d in details:
        regs = d.get("regressions")
        if isinstance(regs, list) and regs:
            ch_num = int(d.get("chapter_number", 0))
            chapters_with_regressions.append(ch_num)
            total_regressions += len(regs)

            if d.get("critical_regression_count"):
                chapters_with_critical_regressions.append(ch_num)

            for reg in regs:
                reg_type = str(reg.get("issue_type", "unknown"))
                reg_severity = str(reg.get("severity", "unknown"))
                regression_by_type[reg_type] = regression_by_type.get(reg_type, 0) + 1
                regression_by_severity[reg_severity] = (
                    regression_by_severity.get(reg_severity, 0) + 1
                )
                all_regression_issues.append(reg)

    # Cross-chapter entity impact scan
    all_chapter_nums = sorted(
        {ch for ch, _ in _all_issue_chapters_ranked}
        | set(excluded_chapters)
        | targeted_chapter_nums
    )
    cross_chapter_warnings = _scan_cross_chapter_entity_impact(
        storage=runtime.storage,
        layout=layout,
        repaired_chapters=details,
        all_chapter_numbers=all_chapter_nums,
    )

    # ── Propagation validation ────────────────────────────────────────
    propagation_validator = PropagationValidator()
    all_propagation_issues: list[dict[str, Any]] = []
    propagation_by_chapter: dict[int, list[dict[str, Any]]] = {}

    # Build all_chapter_texts map for propagation validation
    all_chapter_texts_map: dict[int, str] = {}
    for ch_num in all_chapter_nums:
        ch_path = layout.chapter_path(ch_num)
        rv_path = layout.chapter_review_draft_path(ch_num)
        ch_text = ""
        if ch_path.exists():
            ch_text = ch_path.read_text(encoding="utf-8")
        elif rv_path.exists():
            ch_text = rv_path.read_text(encoding="utf-8")
        if ch_text:
            all_chapter_texts_map[ch_num] = ch_text

    # Validate propagation for each repaired chapter
    repaired_chapter_details = [
        d
        for d in details
        if isinstance(d, dict)
        and d.get("status") in ("applied", "blocked")
        and d.get("_propagation_original_text")
    ]

    for detail in repaired_chapter_details:
        ch_num = int(detail.get("chapter_number", 0))
        original_text = detail.get("_propagation_original_text", "")
        repaired_text = detail.get("_propagation_repaired_text", "")

        if not repaired_text:
            continue

        subsequent_chapters = [ch for ch in all_chapter_nums if ch > ch_num]
        if not subsequent_chapters:
            continue

        try:
            issues = await propagation_validator.validate_propagation(
                repaired_chapter=ch_num,
                original_text=original_text,
                repaired_text=repaired_text,
                subsequent_chapters=subsequent_chapters,
                all_chapter_texts=all_chapter_texts_map,
            )
            if issues:
                issue_dicts = [iss.model_dump() for iss in issues]
                propagation_by_chapter[ch_num] = issue_dicts
                all_propagation_issues.extend(issue_dicts)
        except Exception:
            _log.debug(
                "Propagation validation failed for chapter %d",
                ch_num,
                exc_info=True,
            )

    # Enhance cross-chapter warnings with propagation data
    if all_propagation_issues:
        propagation_counts_by_affected: dict[int, int] = {}
        for issue in all_propagation_issues:
            for affected in issue.get("affected_chapters", []):
                try:
                    affected_ch = int(affected)
                except (TypeError, ValueError):
                    continue
                propagation_counts_by_affected[affected_ch] = (
                    propagation_counts_by_affected.get(affected_ch, 0) + 1
                )

        for warning in cross_chapter_warnings:
            warning_chapter = warning.get("chapter_number")
            if warning_chapter in propagation_counts_by_affected:
                warning["propagation_issue_count"] = propagation_counts_by_affected[warning_chapter]
                warning["has_propagation_issue"] = True

    if on_step_progress:
        on_step_progress(
            "book_consistency_repair_review",
            {
                "status": "done",
                "issues_checked": total_issues_checked,
                "issues_closed": total_issues_closed,
                "issues_remaining": total_issues_remaining,
                "manual_review_chapters": len(needs_manual_review_chs),
                "cross_chapter_warnings": len(cross_chapter_warnings),
            },
        )

    # Strip internal-only fields from details before returning
    for d in details:
        d.pop("_source_issues", None)
        d.pop("_propagation_original_text", None)
        d.pop("_propagation_repaired_text", None)

    _save_repair_checkpoint(
        checkpoint_path,
        {
            "schema_version": 1,
            "signature": checkpoint_signature,
            "completed_chapters": sorted(completed_chapters),
            "applied_chapters": sorted(applied_chapters),
            "total_chapters": total_chapters,
            "status": "completed",
            "checksum": _compute_repair_checksum(sorted(completed_chapters)),
        },
    )

    return {
        "enabled": True,
        "min_severity": _min_severity,
        "max_chapters": _max_chapters,
        "repair_concurrency": repair_concurrency,
        "issue_pool_enabled": bool(getattr(request, "use_issue_panel_pool", True)),
        "issue_pool_size": pool_size,
        "admission_summary": admission_summary,
        "targeted_chapters": len(chapter_groups),
        "processed_chapters": int(repair_metrics["processed_chapters"]),
        "processed_unique_chapter_count": int(repair_metrics["processed_unique_chapter_count"]),
        "processed_chapter_numbers": repair_metrics["processed_chapter_numbers"],
        "applied_chapters": applied_count,
        "applied_unique_chapter_count": int(repair_metrics["applied_unique_chapter_count"]),
        "applied_write_count": int(repair_metrics["applied_write_count"]),
        "applied_chapter_numbers": repair_metrics["applied_chapter_numbers"],
        "failed_chapters": failed_count,
        "failed_unique_chapter_count": int(repair_metrics["failed_unique_chapter_count"]),
        "failed_chapter_numbers": repair_metrics["failed_chapter_numbers"],
        "blocked_chapters": blocked_count,
        "blocked_unique_chapter_count": int(repair_metrics["blocked_unique_chapter_count"]),
        "no_change_unique_chapter_count": int(repair_metrics["no_change_unique_chapter_count"]),
        "no_change_chapter_numbers": repair_metrics["no_change_chapter_numbers"],
        "skipped_unique_chapter_count": int(repair_metrics["skipped_unique_chapter_count"]),
        "skipped_chapter_numbers": repair_metrics["skipped_chapter_numbers"],
        "matched_by_ref": int(progress_state["matched_by_ref"]),
        "matched_by_fuzzy": int(progress_state["matched_by_fuzzy"]),
        "matched_by_para": int(progress_state["matched_by_para"]),
        "matched_by_ticket": int(progress_state["matched_by_ticket"]),
        "panel_expanded": int(progress_state["panel_expanded"]),
        "details": details,
        # Chapters with qualifying issues that were NOT processed this run (max_chapters cap)
        "excluded_chapters": excluded_chapters,
        # Post-repair verification
        "post_repair_summary": {
            "issues_checked": total_issues_checked,
            "issues_closed": total_issues_closed,
            "issues_remaining": total_issues_remaining,
        },
        "verification_summary": {
            "total": len(all_verification_results),
            "by_status": verification_by_status,
        },
        "verification_results": all_verification_results,
        "needs_manual_review": needs_manual_review_chs,
        "blocked_chapter_numbers": blocked_chapter_nums,
        "cross_chapter_warnings": cross_chapter_warnings,
        # Regression detection
        "regression_summary": {
            "total_regressions": total_regressions,
            "by_type": regression_by_type,
            "by_severity": regression_by_severity,
            "chapters_with_regressions": chapters_with_regressions,
            "chapters_with_critical_regressions": chapters_with_critical_regressions,
            "adjudication_status": "candidate_only",
            "requires_llm_adjudication": total_regressions > 0,
        },
        "regression_issues": all_regression_issues,
        # Propagation validation
        "propagation_summary": {
            "total_issues": len(all_propagation_issues),
            "by_type": _aggregate_propagation_by_type(all_propagation_issues),
            "by_severity": _aggregate_propagation_by_severity(all_propagation_issues),
            "chapters_with_issues": sorted(propagation_by_chapter.keys()),
            "adjudication_status": "candidate_only",
            "requires_llm_adjudication": bool(all_propagation_issues),
        },
        "propagation_issues": all_propagation_issues,
        "propagation_by_chapter": propagation_by_chapter,
    }


def _build_book_consistency_repair_report_payload(
    *,
    request: BookConsistencyRequest,
    audit_result: Any,
    auto_repair: dict[str, Any],
) -> dict[str, Any]:
    issue_count = len(getattr(audit_result, "issues", []) or [])
    analysis_mode = str(getattr(audit_result, "analysis_mode", "summary") or "summary")
    chapters_audited = list(getattr(audit_result, "chapters_audited", []) or [])
    truncated_chapters = list(getattr(audit_result, "truncated_chapters", []) or [])
    targeted = int(auto_repair.get("targeted_chapters", 0) or 0)
    details = [item for item in auto_repair.get("details", []) or [] if isinstance(item, dict)]
    repair_metrics = summarize_book_repair_details(details, targeted_chapters=targeted)
    processed = int(auto_repair.get("processed_chapters", repair_metrics["processed_chapters"]) or 0)
    applied = int(auto_repair.get("applied_chapters", repair_metrics["applied_chapters"]) or 0)
    failed = int(auto_repair.get("failed_chapters", repair_metrics["failed_chapters"]) or 0)
    blocked = int(auto_repair.get("blocked_chapters", repair_metrics["blocked_chapters"]) or 0)
    no_change = int(
        auto_repair.get(
            "no_change_unique_chapter_count",
            repair_metrics["no_change_unique_chapter_count"],
        )
        or 0
    )
    skipped = int(
        auto_repair.get(
            "skipped_unique_chapter_count",
            repair_metrics["skipped_unique_chapter_count"],
        )
        or 0
    )
    matched_by_ref = int(auto_repair.get("matched_by_ref", 0) or 0)
    matched_by_fuzzy = int(auto_repair.get("matched_by_fuzzy", 0) or 0)
    matched_by_para = int(auto_repair.get("matched_by_para", 0) or 0)
    matched_by_ticket = int(auto_repair.get("matched_by_ticket", 0) or 0)
    panel_expanded = int(auto_repair.get("panel_expanded", 0) or 0)
    issue_pool_size = int(auto_repair.get("issue_pool_size", 0) or 0)
    admission_summary = (
        auto_repair.get("admission_summary")
        if isinstance(auto_repair.get("admission_summary"), dict)
        else {}
    )

    summary_line = (
        f"全书审计共识别 {issue_count} 个问题；"
        f"定向修复目标 {targeted} 章，已处理 {processed} 章，"
        f"发生正文改动 {applied} 章"
    )
    if no_change > 0:
        summary_line += f"，执行后无改动 {no_change} 章"
    if skipped > 0:
        summary_line += f"，未匹配可执行修复 {skipped} 章"
    summary_line += f"，失败 {failed} 章。"
    if blocked > 0:
        summary_line += f"  防污染闸门已阻止并回滚 {blocked} 章疑似污染写入。"
    if panel_expanded > 0:
        summary_line += f"  问题面板优先扩展修复 {panel_expanded} 个问题。"
    if admission_summary:
        eligible_issues = int(admission_summary.get("eligible_issue_count", 0) or 0)
        ineligible_issues = int(admission_summary.get("ineligible_issue_count", 0) or 0)
        if eligible_issues or ineligible_issues:
            summary_line += (
                f"  修复准入：{eligible_issues} 条进入自动修复，"
                f"{ineligible_issues} 条降级为人工/计划处理。"
            )

    post_repair = auto_repair.get("post_repair_summary") or {}
    pr_checked = int(post_repair.get("issues_checked", 0) or 0)
    pr_closed = int(post_repair.get("issues_closed", 0) or 0)
    pr_remaining = int(post_repair.get("issues_remaining", 0) or 0)
    if pr_checked > 0:
        summary_line += f"  本地证据复核（仅已写入章节）：{pr_closed}/{pr_checked} 项证据已消除"
        if pr_remaining > 0:
            summary_line += f"，{pr_remaining} 项仍存在"
        summary_line += "。"

    verification_summary = auto_repair.get("verification_summary") or {}
    if isinstance(verification_summary, dict):
        by_status = verification_summary.get("by_status") or {}
        if isinstance(by_status, dict):
            unresolved = int(by_status.get("unresolved", 0) or 0)
            regressed = int(by_status.get("regressed", 0) or 0)
            partial = int(by_status.get("partial", 0) or 0)
            resolved = int(by_status.get("resolved", 0) or 0)
            status_bits = []
            if resolved:
                status_bits.append(f"已解决 {resolved}")
            if partial:
                status_bits.append(f"部分解决 {partial}")
            if unresolved:
                status_bits.append(f"未解决 {unresolved}")
            if regressed:
                status_bits.append(f"疑似回归 {regressed}")
            if status_bits:
                summary_line += f"  票据验证：{'，'.join(status_bits)}。"

    needs_manual = list(auto_repair.get("needs_manual_review", []) or [])
    if needs_manual:
        summary_line += f"  ⚠ 需人工复核：第 {'、'.join(str(c) for c in needs_manual)} 章。"

    regression_summary = auto_repair.get("regression_summary") or {}
    total_regs = int(regression_summary.get("total_regressions", 0) or 0)
    if total_regs > 0:
        reg_by_sev = regression_summary.get("by_severity", {})
        crit_count = int(reg_by_sev.get("critical", 0) or 0)
        warn_count = int(reg_by_sev.get("warning", 0) or 0)
        summary_line += f"  回归候选：发现 {total_regs} 个需 LLM 复核的本地信号"
        if crit_count > 0:
            summary_line += f"（严重 {crit_count}）"
        if warn_count > 0:
            summary_line += f"（警告 {warn_count}）"
        summary_line += "。"

    propagation_summary = auto_repair.get("propagation_summary") or {}
    total_prop = int(propagation_summary.get("total_issues", 0) or 0)
    if total_prop > 0:
        prop_by_sev = propagation_summary.get("by_severity", {})
        prop_crit = int(prop_by_sev.get("critical", 0) or 0)
        prop_warn = int(prop_by_sev.get("warning", 0) or 0)
        summary_line += f"  传播候选：发现 {total_prop} 个需 LLM 复核的本地信号"
        if prop_crit > 0:
            summary_line += f"（严重 {prop_crit}）"
        if prop_warn > 0:
            summary_line += f"（警告 {prop_warn}）"
        summary_line += "。"

    post_targeted = auto_repair.get("post_repair_targeted_audit") or {}
    post_targeted_issue_count = 0
    if isinstance(post_targeted, dict):
        post_targeted_issue_count = int(post_targeted.get("issue_count", 0) or 0)
        if post_targeted.get("status") == "completed":
            summary_line += (
                f"  修复后二次小审计：复查 {len(post_targeted.get('target_chapters', []) or [])} 章，"
                f"命中 {post_targeted_issue_count} 个剩余/新增问题。"
            )
        elif post_targeted.get("status") == "failed":
            summary_line += "  修复后二次小审计失败，需查看诊断信息。"

    acceptance_status = "needs_attention"
    if failed == 0 and blocked == 0 and post_targeted_issue_count == 0 and pr_remaining == 0:
        acceptance_status = (
            "ready_to_export" if issue_count == 0 or applied >= 0 else "pass_with_notes"
        )
    elif failed == 0 and blocked == 0:
        acceptance_status = "review_recommended"

    return {
        "report_type": "book_consistency_repair_report",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_id": request.project_id,
        "summary": summary_line,
        "analysis": {
            "analysis_mode": analysis_mode,
            "consistency_score": float(getattr(audit_result, "consistency_score", 0.0) or 0.0),
            "issue_count": issue_count,
            "chapters_audited": chapters_audited,
            "truncated_chapters": truncated_chapters,
        },
        "task_flow": {
            "status": "completed",
            "repair_mode": str(getattr(request, "repair_mode", "off") or "off"),
            "repair_concurrency": int(auto_repair.get("repair_concurrency", 1) or 1),
            "repair_min_severity": str(auto_repair.get("min_severity", "warning") or "warning"),
            "targeted_chapters": targeted,
            "processed_chapters": processed,
            "applied_chapters": applied,
            "failed_chapters": failed,
            "blocked_chapters": blocked,
        },
        "matching": {
            "use_issue_panel_pool": bool(auto_repair.get("issue_pool_enabled", False)),
            "issue_pool_size": issue_pool_size,
            "matched_by_ref": matched_by_ref,
            "matched_by_fuzzy": matched_by_fuzzy,
            "matched_by_para": matched_by_para,
            "matched_by_ticket": matched_by_ticket,
            "panel_expanded": panel_expanded,
        },
        "admission_summary": admission_summary,
        "verify": auto_repair.get("verify") or {},
        "post_repair_summary": post_repair,
        "verification_summary": verification_summary if isinstance(verification_summary, dict) else {},
        "verification_results": list(auto_repair.get("verification_results", []) or []),
        "regression_summary": regression_summary,
        "regression_issues": list(auto_repair.get("regression_issues", []) or []),
        "propagation_summary": propagation_summary,
        "propagation_issues": list(auto_repair.get("propagation_issues", []) or []),
        "propagation_by_chapter": auto_repair.get("propagation_by_chapter") or {},
        "post_repair_targeted_audit": post_targeted if isinstance(post_targeted, dict) else {},
        "acceptance": {
            "status": acceptance_status,
            "suggest_export": acceptance_status in {"ready_to_export", "pass_with_notes"},
            "post_repair_targeted_issue_count": post_targeted_issue_count,
            "remaining_after_repair": pr_remaining,
            "failed_chapters": failed,
            "blocked_chapters": blocked,
        },
        "needs_manual_review": needs_manual,
        "blocked_chapter_numbers": list(auto_repair.get("blocked_chapter_numbers", []) or []),
        "cross_chapter_warnings": list(auto_repair.get("cross_chapter_warnings", []) or []),
        "chapters": details,
    }
