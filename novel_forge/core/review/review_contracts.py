"""Adapters for normalized review findings, repair tickets, and verification."""

from __future__ import annotations

import dataclasses
import hashlib
import re
from typing import Any

from novel_forge.common import severity as _severity
from novel_forge.core.review.audit_taxonomy import classify_review_issue
from novel_forge.core.schemas.review import (
    RepairTicket,
    RepairVerificationResult,
    ReviewFinding,
)
from novel_forge.core.utils.audit_issue import normalize_audit_issue
from novel_forge.core.utils.coerce import coerce_float as _coerce_float
from novel_forge.core.utils.field_extractor import field as _field
from novel_forge.core.utils.text_hash import source_text_hash as _source_text_hash

# Compatibility export: callers keep one public review helper while the hash
# implementation remains canonical in core.utils.text_hash.
source_text_hash = _source_text_hash

SEVERITY_RANK = _severity.SEVERITY_RANK
normalize_severity = _severity.normalize_severity
severity_at_least = _severity.severity_at_least


def _issue_to_dict(issue: Any) -> dict[str, Any]:
    if isinstance(issue, dict):
        return dict(issue)
    if hasattr(issue, "model_dump"):
        dumped = issue.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, dict) else {}
    if dataclasses.is_dataclass(issue) and not isinstance(issue, type):
        dumped = dataclasses.asdict(issue)
        return dict(dumped) if isinstance(dumped, dict) else {}
    return {}


def _coerce_int(value: Any, default: int = 0) -> int:
    if isinstance(value, list):
        value = value[0] if value else default
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def normalize_review_mode(value: Any, *, recheck: bool = False) -> str:
    """Normalize review mode names used by reports, findings, and tickets."""
    raw = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "full": "full_review",
        "first": "full_review",
        "first_pass": "full_review",
        "initial": "full_review",
        "recheck": "targeted_recheck",
        "delta": "targeted_recheck",
        "post_repair": "targeted_recheck",
        "strict": "targeted_recheck",
        "regression": "regression_scan",
    }
    normalized = aliases.get(raw, raw)
    if normalized in {"full_review", "targeted_recheck", "regression_scan"}:
        return normalized
    return "targeted_recheck" if recheck else "full_review"


def review_dimension_for_issue(issue: Any) -> str:
    """Infer the review dimension used for routing repair."""
    fix_mode = str(_field(issue, "fix_mode", "") or "").strip().lower()
    repair_source = ""
    if fix_mode.startswith("repair_"):
        repair_source = fix_mode
    return classify_review_issue(
        issue_type=_field(issue, "issue_type", "") or _field(issue, "category", ""),
        source_module=_field(issue, "source_module", "") or repair_source,
        dimension=_field(issue, "dimension", ""),
        summary=_field(issue, "description", "") or _field(issue, "summary", ""),
        evidence=_field(issue, "evidence", "") or _field(issue, "evidence_quote", ""),
        metadata=_issue_to_dict(issue),
    ).primary_dimension


def _chapter_number_for_issue(issue: Any, fallback: int = 0) -> int:
    primary = _coerce_int(_field(issue, "primary_chapter", 0), 0)
    if primary > 0:
        return primary
    explicit = _coerce_int(_field(issue, "chapter_number", 0), 0)
    if explicit > 0:
        return explicit
    involved = _field(issue, "chapters_involved", [])
    if isinstance(involved, list) and involved:
        first = _coerce_int(involved[0], 0)
        if first > 0:
            return first
    return fallback


def _paragraph_span_for_issue(issue: Any) -> tuple[int, int]:
    span = _field(issue, "paragraph_span", [])
    if isinstance(span, list) and span:
        start = _coerce_int(span[0], 0)
        end = _coerce_int(span[-1], start)
        return start, max(start, end)
    start = _coerce_int(_field(issue, "paragraph_start", 0), 0)
    end = _coerce_int(_field(issue, "paragraph_end", 0), start)
    if start <= 0:
        start = _coerce_int(_field(issue, "paragraph_index", 0), 0)
        end = start
    return start, max(start, end)


def _stable_suffix(parts: list[Any]) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def normalize_issue_to_finding(
    issue: Any,
    *,
    source_module: str,
    chapter_number: int = 0,
    dimension: str = "",
    current_text_hash: str = "",
    review_mode: str = "full_review",
    review_round: int = 1,
    metadata: dict[str, Any] | None = None,
) -> ReviewFinding:
    """Convert any audit issue shape into the shared finding contract."""
    raw_issue = _issue_to_dict(issue)
    chapter = _chapter_number_for_issue(issue, fallback=chapter_number)
    resolved_dimension = dimension or review_dimension_for_issue(issue)
    audit_issue = normalize_audit_issue(
        issue,
        dimension=resolved_dimension,
        namespace=source_module or resolved_dimension or "review",
        chapter_number=chapter,
        repair_surface_default=str(_field(issue, "repair_surface", "") or "chapter_text"),
        source_module=source_module,
    )
    issue_id = audit_issue.issue_id
    issue_type = audit_issue.issue_type
    summary = audit_issue.summary
    evidence = audit_issue.evidence or audit_issue.evidence_quote
    location = audit_issue.location
    para_start, para_end = _paragraph_span_for_issue(issue)
    signature = _stable_suffix(
        [source_module, chapter, resolved_dimension, issue_type, summary, location, para_start]
    )
    finding_id = f"{source_module}_ch{chapter}_{issue_id or signature}"
    severity = normalize_severity(audit_issue.severity)
    confidence = _coerce_float(audit_issue.confidence, 0.0)
    if confidence <= 0.0:
        confidence = 0.75 if severity in {"critical", "high", "warning", "major"} else 0.55
    original = audit_issue.model_dump(
        mode="json",
        exclude={"schema_version", "created_at"},
    )
    postconditions_raw = _field(issue, "postconditions", [])
    postconditions = (
        [dict(item) for item in postconditions_raw if isinstance(item, dict)]
        if isinstance(postconditions_raw, list)
        else []
    )
    resolved_review_mode = normalize_review_mode(
        _field(issue, "review_mode", "") or (metadata or {}).get("review_mode", "") or review_mode,
        recheck=review_round > 1,
    )
    raw_fix_action = str(_field(issue, "fix_action", "") or "").strip()
    if not raw_fix_action:
        raw_fix_actions = list(getattr(audit_issue, "fix_actions", []) or [])
        raw_fix_action = str(raw_fix_actions[0]).strip() if raw_fix_actions else ""
    finding_metadata = {
        "original_issue": original,
        "location": location,
        "fix_mode": audit_issue.fix_mode,
        "fix_action": raw_fix_action or audit_issue.fix_mode,
        "review_mode": resolved_review_mode,
        "anchor_type": audit_issue.anchor_type,
        "location_confidence": audit_issue.location_confidence,
    }
    for extra_key in (
        "evidence_pairs",
        "verification_questions",
        "handoff_notes",
        "adjudication_notes",
        "repair_scope",
        "repair_boundary",
        "allowed_changes",
        "forbidden_changes",
        "must_preserve",
    ):
        extra_value = raw_issue.get(extra_key)
        if extra_value not in (None, "", [], {}):
            finding_metadata[extra_key] = extra_value
    finding_metadata.update(metadata or {})
    suggestion = audit_issue.fix_suggestion
    repair_goal = suggestion.strip() or f"修复该审核问题：{summary or issue_type}"
    raw_mode = audit_issue.fix_mode
    raw_mode = raw_mode.strip().lower()
    suggested_mode = (
        raw_mode if raw_mode in {"replace", "insert", "window", "fulltext"} else "window"
    )
    return ReviewFinding(
        finding_id=finding_id,
        chapter_number=chapter,
        review_mode=resolved_review_mode,
        review_round=max(1, _coerce_int(_field(issue, "review_round", review_round), review_round)),
        source_module=source_module,
        dimension=resolved_dimension,
        issue_type=issue_type,
        severity=severity,
        confidence=max(0.0, min(1.0, confidence)),
        summary=summary or issue_type,
        evidence_quote=evidence,
        paragraph_start=para_start,
        paragraph_end=para_end,
        anchor_type="explicit_para"
        if para_start > 0
        else ("evidence_match" if evidence else "inferred_scope"),
        repair_goal=repair_goal,
        postconditions=postconditions,
        must_preserve=[
            "保持本章已确认的主线结果、角色状态和关键因果关系",
            "不得制造新的跨章节时间线、称谓或世界观冲突",
        ],
        suggested_mode=suggested_mode,
        blocks_finalize=severity in {"critical", "high"},
        source_text_hash=current_text_hash,
        signature=signature,
        metadata=finding_metadata,
    )


def compile_repair_ticket_from_finding(finding: ReviewFinding) -> RepairTicket:
    """Compile one normalized finding into a bounded repair ticket."""
    evidence = str(finding.evidence_quote or "").strip()
    metadata = dict(finding.metadata or {})
    repair_scope = (
        metadata.get("repair_scope") if isinstance(metadata.get("repair_scope"), dict) else {}
    )

    def _text_items(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item or "").strip() for item in value if str(item or "").strip()]
        text = str(value or "").strip()
        return [text] if text else []

    acceptance_criteria = [
        "修复后该问题不应在同一证据位置继续成立",
        "保持本章已确认的主线结果、角色状态和关键因果关系",
        "不得引入新的跨章节时间线、称谓、角色状态或世界观冲突",
    ]
    if evidence:
        acceptance_criteria.insert(1, f"优先围绕原证据位置处理：{evidence[:80]}")
    for postcondition in list(finding.postconditions or []):
        if not isinstance(postcondition, dict):
            continue
        description = str(postcondition.get("description", "") or "").strip()
        if description and description not in acceptance_criteria:
            acceptance_criteria.append(description)
    for note_key, label in (
        ("adjudication_notes", "验证裁决"),
        ("handoff_notes", "审计交接"),
        ("repair_boundary", "修复边界"),
    ):
        note = str(metadata.get(note_key, "") or "").strip()
        if note:
            acceptance_criteria.append(f"{label}：{note[:180]}")
    allowed_changes = [
        *_text_items(metadata.get("allowed_changes")),
        *_text_items(repair_scope.get("allowed_changes")),
    ]
    for allowed in allowed_changes:
        acceptance_criteria.append(f"允许改动：{allowed[:140]}")
    evidence_pairs = metadata.get("evidence_pairs")
    if isinstance(evidence_pairs, list) and evidence_pairs:
        acceptance_criteria.append("修复前必须保留并核对 metadata.evidence_pairs 中的跨章证据对。")
    severity = normalize_severity(finding.severity)
    max_change_ratio = 0.12 if severity in {"critical", "high"} else 0.08
    must_preserve = [
        *list(finding.must_preserve or []),
        *_text_items(metadata.get("must_preserve")),
        *_text_items(repair_scope.get("must_preserve")),
    ]
    forbidden_changes = [
        "不得为了解决局部问题重写无关剧情段落",
        "不得删除后续章节仍依赖的伏笔、道具、人物关系或状态变化",
        *_text_items(metadata.get("forbidden_changes")),
        *_text_items(repair_scope.get("forbidden_changes")),
    ]
    return RepairTicket(
        ticket_id=f"ticket_{finding.finding_id}",
        chapter_number=finding.chapter_number,
        review_mode=finding.review_mode,
        finding_ids=[finding.finding_id],
        source_module=finding.source_module,
        dimension=finding.dimension,
        issue_type=finding.issue_type,
        severity=severity,
        target_summary=finding.summary,
        repair_goal=finding.repair_goal or finding.summary,
        repair_mode=finding.suggested_mode or "window",
        acceptance_criteria=acceptance_criteria,
        postconditions=list(finding.postconditions or []),
        must_preserve=must_preserve,
        forbidden_changes=forbidden_changes,
        source_text_hash=finding.source_text_hash,
        max_change_ratio=max_change_ratio,
        max_attempts=2,
        target_paragraph_start=finding.paragraph_start,
        target_paragraph_end=finding.paragraph_end,
        blocking=bool(finding.blocks_finalize),
        metadata={
            "source_finding_signature": finding.signature,
            "source_text_hash": finding.source_text_hash,
            "review_mode": finding.review_mode,
            "evidence_quote": finding.evidence_quote,
            "anchor_type": finding.anchor_type,
            "postconditions": list(finding.postconditions or []),
            **metadata,
        },
    )


def compile_repair_tickets_from_findings(
    findings: list[ReviewFinding],
    *,
    require_auto_repair_eligible: bool = False,
) -> list[RepairTicket]:
    """Compile a list of findings into repair tickets."""
    if require_auto_repair_eligible:
        from novel_forge.core.review.review_precision import finding_auto_repair_eligible

        findings = [finding for finding in findings if finding_auto_repair_eligible(finding)]
    return [compile_repair_ticket_from_finding(finding) for finding in findings]


def _make_issue(
    *,
    issue_type: str,
    severity: str,
    summary: str,
    evidence: str = "",
    fix_suggestion: str = "",
    location: str = "",
) -> dict[str, Any]:
    return {
        "issue_type": issue_type,
        "severity": severity,
        "summary": summary,
        "evidence": evidence,
        "fix_suggestion": fix_suggestion,
        "location": location,
    }


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = str(text or "").lower()
    return any(marker.lower() in lowered for marker in markers)


_CHAPTER_REPAIR_FRAGMENT_RE = re.compile(
    r"^\s*(?:type|issue|repair|evidence(?:_\d+)?|location|summary|类型|问题|修复|证据|位置|摘要)\s*[:：]",
    re.IGNORECASE,
)


def _coalesce_chapter_repair_entries(values: Any) -> list[str]:
    """Group flat CHECK_CHAPTER label fragments into issue-sized entries."""

    entries = [str(item or "").strip() for item in list(values or []) if str(item or "").strip()]
    if not any(_CHAPTER_REPAIR_FRAGMENT_RE.match(entry) for entry in entries):
        return entries
    result: list[str] = []
    current: list[str] = []
    for entry in entries:
        labeled = bool(_CHAPTER_REPAIR_FRAGMENT_RE.match(entry))
        starts_new = bool(re.match(r"^\s*(?:type|类型)\s*[:：]", entry, re.IGNORECASE))
        if starts_new and current:
            result.append("；".join(current))
            current = [entry]
            continue
        if labeled:
            current.append(entry)
            continue
        if current:
            result.append("；".join(current))
            current = []
        result.append(entry)
    if current:
        result.append("；".join(current))
    return result


def _chapter_issue_payload(
    value: Any,
    *,
    default_issue_type: str,
    default_severity: str,
    default_fix_suggestion: str,
) -> dict[str, Any] | None:
    """Project a typed CHECK_CHAPTER issue into the review finding contract.

    The chapter checker persists atomic issue objects.  This adapter preserves
    the checker-provided severity, evidence and paragraph anchors instead of
    replacing them with an unconditional ``high`` severity during ticket
    compilation.
    """

    if hasattr(value, "model_dump"):
        raw = value.model_dump(mode="json", exclude={"schema_version", "created_at"})
    elif isinstance(value, dict):
        raw = dict(value)
    else:
        text = str(value or "").strip()
        if not text:
            return None
        raw = {"summary": text, "evidence": text}
    summary = str(raw.get("summary") or raw.get("description") or raw.get("issue") or "").strip()
    if not summary:
        return None
    severity = str(raw.get("severity") or default_severity).strip().lower()
    if severity not in {"critical", "high", "medium", "low"}:
        severity = default_severity
    def _paragraph_index(field_name: str) -> int:
        try:
            return max(0, int(raw.get(field_name) or 0))
        except (TypeError, ValueError):
            return 0

    return _make_issue(
        issue_type=str(raw.get("issue_type") or raw.get("type") or default_issue_type).strip(),
        severity=severity,
        summary=summary,
        evidence=str(raw.get("evidence") or raw.get("quote") or summary).strip(),
        fix_suggestion=str(
            raw.get("fix_suggestion")
            or raw.get("repair_suggestion")
            or raw.get("suggestion")
            or default_fix_suggestion
        ).strip(),
        location=str(raw.get("location") or raw.get("paragraph_hint") or "").strip(),
    ) | {
        "paragraph_start": _paragraph_index("paragraph_start"),
        "paragraph_end": _paragraph_index("paragraph_end"),
    }


def _finding_from_issue(
    issue: dict[str, Any],
    *,
    source_module: str,
    dimension: str,
    chapter_number: int,
    current_text_hash: str,
    review_mode: str = "full_review",
    review_round: int = 1,
    metadata: dict[str, Any] | None = None,
) -> ReviewFinding:
    return normalize_issue_to_finding(
        issue,
        source_module=source_module,
        chapter_number=chapter_number,
        dimension=dimension,
        current_text_hash=current_text_hash,
        review_mode=review_mode,
        review_round=review_round,
        metadata=metadata,
    )


def chapter_repair_report_to_findings(
    report: Any | None,
    *,
    chapter_number: int,
    current_text_hash: str = "",
    review_mode: str = "",
    review_round: int = 1,
) -> list[ReviewFinding]:
    """Convert CHECK_CHAPTER output into chapter-quality findings."""
    if report is None:
        return []
    findings: list[ReviewFinding] = []
    resolved_review_mode = normalize_review_mode(
        review_mode or getattr(report, "review_mode", ""),
        recheck=review_round > 1,
    )

    def _append(issue: dict[str, Any], *, category: str) -> None:
        findings.append(
            _finding_from_issue(
                issue,
                source_module="check_chapter",
                dimension="chapter_quality",
                chapter_number=chapter_number,
                current_text_hash=current_text_hash,
                review_mode=resolved_review_mode,
                review_round=review_round,
                metadata={
                    "chapter_quality_category": category,
                    "review_mode": resolved_review_mode,
                },
            )
        )

    for leak in list(getattr(report, "prompt_leaks", []) or []):
        text = str(leak or "").strip()
        if text:
            _append(
                _make_issue(
                    issue_type="prompt_leak",
                    severity="critical",
                    summary=f"正文混入提示词或规划层标记：{text[:80]}",
                    evidence=text,
                    fix_suggestion="删除提示词/规划层元语言，改为角色当下动作、感官或对话。",
                ),
                category="prompt_leak",
            )

    for item in list(getattr(report, "factual_errors", []) or []):
        issue = _chapter_issue_payload(
            item,
            default_issue_type="factual_error",
            default_severity="medium",
            default_fix_suggestion="修正章内事实、时间或状态错误，并保持既有剧情结果不变。",
        )
        if issue is not None:
            _append(issue, category="factual_error")

    for item in list(getattr(report, "continuity_errors", []) or []):
        issue = _chapter_issue_payload(
            item,
            default_issue_type="chapter_continuity_error",
            default_severity="medium",
            default_fix_suggestion="修正文内前后不一致处，不扩大到跨章重写。",
        )
        if issue is not None:
            _append(issue, category="chapter_continuity_error")

    for item in list(getattr(report, "expression_errors", []) or []):
        issue = _chapter_issue_payload(
            item,
            default_issue_type="expression_clarity",
            default_severity="medium",
            default_fix_suggestion="用局部改写修复章内表达问题，避免改动已通过的主线结果。",
        )
        if issue is not None:
            _append(issue, category="expression_error")
    return findings


def eval_report_to_findings(
    report: Any | None,
    *,
    chapter_number: int,
    current_text_hash: str = "",
    priorities: tuple[str, ...] = ("critical", "high"),
    review_mode: str = "full_review",
    review_round: int = 1,
) -> list[ReviewFinding]:
    """Convert high-priority eval repair suggestions into normalized findings."""

    if report is None:
        return []
    priority_set = {str(item or "").strip().lower() for item in priorities}
    findings: list[ReviewFinding] = []
    for idx, suggestion in enumerate(list(getattr(report, "repair_suggestions", []) or []), 1):
        priority = str(_field(suggestion, "priority", "medium") or "medium").strip().lower()
        if priority not in priority_set:
            continue
        issue = str(_field(suggestion, "issue", "") or "").strip()
        fix = str(_field(suggestion, "suggestion", "") or "").strip()
        location = str(_field(suggestion, "location", "") or "").strip()
        dimension = str(_field(suggestion, "dimension", "") or "").strip()
        if not issue and not fix:
            continue
        severity = "critical" if priority == "critical" else "high"
        findings.append(
            _finding_from_issue(
                _make_issue(
                    issue_type="eval_repair_suggestion",
                    severity=severity,
                    summary=issue or fix,
                    evidence=location,
                    fix_suggestion=fix or "按评估建议补强该处质量问题。",
                    location=location,
                ),
                source_module="evaluate_chapter",
                dimension="chapter_quality",
                chapter_number=chapter_number,
                current_text_hash=current_text_hash,
                review_mode=review_mode,
                review_round=review_round,
                metadata={
                    "eval_dimension": dimension,
                    "eval_priority": priority,
                    "eval_suggestion_index": idx,
                    "review_mode": normalize_review_mode(review_mode),
                },
            )
        )
    return findings


def alignment_report_to_findings(
    report: Any | None,
    *,
    chapter_number: int,
    current_text_hash: str = "",
    review_mode: str = "",
    review_round: int = 1,
) -> list[ReviewFinding]:
    """Convert alignment report gaps into review findings."""
    if report is None:
        return []
    findings: list[ReviewFinding] = []
    resolved_review_mode = normalize_review_mode(
        review_mode or getattr(report, "review_mode", ""),
        recheck=review_round > 1,
    )
    repair_actions = list(getattr(report, "repair_actions", []) or [])
    for item in list(getattr(report, "missing_main_points", []) or []):
        text = str(item or "").strip()
        if text:
            findings.append(
                _finding_from_issue(
                    _make_issue(
                        issue_type="outline_main_point_missing",
                        severity="high",
                        summary=text,
                        evidence=text,
                        fix_suggestion="补足章节目标或主线点，使正文重新贴合大纲。",
                    ),
                    source_module="check_alignment",
                    dimension="alignment",
                    chapter_number=chapter_number,
                    current_text_hash=current_text_hash,
                    review_mode=resolved_review_mode,
                    review_round=review_round,
                )
            )
    for item in list(getattr(report, "weak_subplot_points", []) or []):
        text = str(item or "").strip()
        if text:
            findings.append(
                _finding_from_issue(
                    _make_issue(
                        issue_type="outline_subplot_weak",
                        severity="medium",
                        summary=text,
                        evidence=text,
                        fix_suggestion=repair_actions[0] if repair_actions else "补强支线承接。",
                    ),
                    source_module="check_alignment",
                    dimension="alignment",
                    chapter_number=chapter_number,
                    current_text_hash=current_text_hash,
                    review_mode=resolved_review_mode,
                    review_round=review_round,
                )
            )
    return findings


def continuity_report_to_findings(
    report: Any | None,
    *,
    chapter_number: int,
    current_text_hash: str = "",
    review_mode: str = "",
    review_round: int = 1,
) -> list[ReviewFinding]:
    """Convert continuity issues into normalized findings."""
    resolved_review_mode = normalize_review_mode(
        review_mode or getattr(report, "review_mode", "") or getattr(report, "pipeline_stage", ""),
        recheck=review_round > 1,
    )
    return [
        normalize_issue_to_finding(
            issue,
            source_module="check_continuity",
            chapter_number=chapter_number,
            dimension="continuity",
            current_text_hash=current_text_hash,
            review_mode=resolved_review_mode,
            review_round=review_round,
        )
        for issue in list(getattr(report, "issues", []) or [])
    ]


def causal_report_to_findings(
    report: Any | None,
    *,
    chapter_number: int,
    current_text_hash: str = "",
    review_mode: str = "",
    review_round: int = 1,
) -> list[ReviewFinding]:
    """Convert causal issues into normalized findings."""
    resolved_review_mode = normalize_review_mode(
        review_mode or getattr(report, "review_mode", "") or getattr(report, "pipeline_stage", ""),
        recheck=review_round > 1,
    )
    return [
        normalize_issue_to_finding(
            issue,
            source_module="causal_validation",
            chapter_number=chapter_number,
            dimension="causal",
            current_text_hash=current_text_hash,
            review_mode=resolved_review_mode,
            review_round=review_round,
        )
        for issue in list(getattr(report, "issues", []) or [])
    ]


def reading_power_report_to_findings(
    report: Any | None,
    *,
    chapter_number: int,
    current_text_hash: str = "",
    min_payoffs: int = 1,
    review_mode: str = "",
    review_round: int = 1,
) -> list[ReviewFinding]:
    """Convert reading-power deficiencies into normalized findings."""
    if report is None:
        return []
    from novel_forge.core.schemas.reading_power_repair import _build_reading_power_issues

    resolved_review_mode = normalize_review_mode(
        review_mode or getattr(report, "review_mode", ""),
        recheck=review_round > 1,
    )
    return [
        normalize_issue_to_finding(
            issue,
            source_module="reading_power_eval",
            chapter_number=chapter_number,
            dimension="reading_power",
            current_text_hash=current_text_hash,
            review_mode=resolved_review_mode,
            review_round=review_round,
        )
        for issue in _build_reading_power_issues(
            report,
            min_payoffs=min_payoffs,
            chapter_number=chapter_number,
        )
    ]


def _ticket_location(ticket: RepairTicket) -> str:
    start = int(ticket.target_paragraph_start or 0)
    end = int(ticket.target_paragraph_end or start or 0)
    if start > 0 and end > start:
        return f"第{start}-{end}段"
    if start > 0:
        return f"第{start}段"
    original = ticket.metadata.get("original_issue", {})
    if isinstance(original, dict):
        return str(original.get("location", "") or "")
    return ""


def _ticket_evidence(ticket: RepairTicket) -> str:
    evidence = str(ticket.metadata.get("evidence_quote", "") or "").strip()
    if evidence:
        return evidence
    original = ticket.metadata.get("original_issue", {})
    if isinstance(original, dict):
        return str(original.get("evidence", "") or original.get("evidence_quote", "") or "")
    return ""


def _ticket_issue_id(ticket: RepairTicket) -> str:
    original = ticket.metadata.get("original_issue", {})
    if isinstance(original, dict):
        issue_id = str(original.get("issue_id", "") or original.get("id", "") or "").strip()
        if issue_id:
            return issue_id
    if ticket.finding_ids:
        return str(ticket.finding_ids[0] or "").strip()
    return str(ticket.ticket_id or "").removeprefix("ticket_").strip()


def repair_ticket_to_continuity_issue_payload(ticket: RepairTicket) -> dict[str, Any]:
    """Project a repair ticket into the continuity repair issue shape."""
    evidence = _ticket_evidence(ticket)
    start = int(ticket.target_paragraph_start or 0)
    end = int(ticket.target_paragraph_end or start or 0)
    fix_actions = [ticket.repair_goal, *list(ticket.acceptance_criteria or [])]
    repair_scope = (
        ticket.metadata.get("repair_scope")
        if isinstance(ticket.metadata.get("repair_scope"), dict)
        else {}
    )
    rewrite_scope = str(
        repair_scope.get("rewrite_scope")
        or repair_scope.get("scope")
        or ("paragraph" if start > 0 or evidence else "chapter")
    )
    return {
        "issue_id": _ticket_issue_id(ticket),
        "issue_type": ticket.issue_type or "book_consistency_continuity",
        "severity": ticket.severity or "medium",
        "summary": ticket.target_summary or ticket.repair_goal,
        "evidence": evidence,
        "location": _ticket_location(ticket),
        "location_confidence": 0.85 if start > 0 else (0.65 if evidence else 0.0),
        "anchor_type": "explicit_para"
        if start > 0
        else ("evidence_match" if evidence else "inferred_scope"),
        "paragraph_start": start,
        "paragraph_end": end,
        "evidence_quote": evidence,
        "fix_mode": ticket.repair_mode or "window",
        "affected_characters": [],
        "rewrite_scope": rewrite_scope,
        "fix_actions": [str(item) for item in fix_actions if str(item or "").strip()],
        "postconditions": list(getattr(ticket, "postconditions", []) or []),
        "review_mode": normalize_review_mode(getattr(ticket, "review_mode", "")),
        "repair_scope": repair_scope,
        "evidence_pairs": list(ticket.metadata.get("evidence_pairs") or []),
        "adjudication_notes": str(ticket.metadata.get("adjudication_notes", "") or ""),
        "handoff_notes": str(ticket.metadata.get("handoff_notes", "") or ""),
        "repair_boundary": str(ticket.metadata.get("repair_boundary", "") or ""),
        "must_preserve": list(ticket.must_preserve or []),
        "forbidden_changes": list(ticket.forbidden_changes or []),
    }


def repair_ticket_to_causal_issue_payload(ticket: RepairTicket) -> dict[str, Any]:
    """Project a repair ticket into the causal repair issue shape."""
    evidence = _ticket_evidence(ticket)
    start = int(ticket.target_paragraph_start or 0)
    end = int(ticket.target_paragraph_end or start or 0)
    fix_actions = [ticket.repair_goal, *list(ticket.acceptance_criteria or [])]
    repair_scope = (
        ticket.metadata.get("repair_scope")
        if isinstance(ticket.metadata.get("repair_scope"), dict)
        else {}
    )
    rewrite_scope = str(
        repair_scope.get("rewrite_scope")
        or repair_scope.get("scope")
        or ("paragraph" if start > 0 or evidence else "chapter")
    )
    return {
        "issue_id": _ticket_issue_id(ticket),
        "issue_type": ticket.issue_type or "event_without_cause",
        "severity": ticket.severity or "medium",
        "location": _ticket_location(ticket),
        "location_confidence": 0.85 if start > 0 else (0.65 if evidence else 0.0),
        "anchor_type": "explicit_para"
        if start > 0
        else ("evidence_match" if evidence else "inferred_scope"),
        "paragraph_start": start,
        "paragraph_end": end,
        "summary": ticket.target_summary or ticket.repair_goal,
        "evidence": evidence,
        "evidence_quote": evidence,
        "fix_suggestion": ticket.repair_goal,
        "fix_mode": ticket.repair_mode or "window",
        "affected_characters": [],
        "rewrite_scope": rewrite_scope,
        "fix_actions": [str(item) for item in fix_actions if str(item or "").strip()],
        "postconditions": list(getattr(ticket, "postconditions", []) or []),
        "review_mode": normalize_review_mode(getattr(ticket, "review_mode", "")),
        "repair_scope": repair_scope,
        "evidence_pairs": list(ticket.metadata.get("evidence_pairs") or []),
        "adjudication_notes": str(ticket.metadata.get("adjudication_notes", "") or ""),
        "handoff_notes": str(ticket.metadata.get("handoff_notes", "") or ""),
        "repair_boundary": str(ticket.metadata.get("repair_boundary", "") or ""),
        "must_preserve": list(ticket.must_preserve or []),
        "forbidden_changes": list(ticket.forbidden_changes or []),
    }


def repair_ticket_to_reading_power_issue_payload(ticket: RepairTicket) -> dict[str, Any]:
    """Project a repair ticket into the reading-power repair issue shape."""
    evidence = _ticket_evidence(ticket)
    return {
        "issue_id": _ticket_issue_id(ticket),
        "issue_type": ticket.issue_type or "reading_power_issue",
        "severity": ticket.severity or "medium",
        "summary": ticket.target_summary or ticket.repair_goal,
        "evidence": evidence,
        "fix_suggestion": ticket.repair_goal,
        "location": _ticket_location(ticket) or "全文",
        "postconditions": list(getattr(ticket, "postconditions", []) or []),
        "review_mode": normalize_review_mode(getattr(ticket, "review_mode", "")),
    }


def build_repair_verification_result(
    ticket: RepairTicket,
    *,
    status: str,
    confidence: float = 0.0,
    remaining_evidence: list[str] | None = None,
    source_text_hash: str = "",
    metadata: dict[str, Any] | None = None,
) -> RepairVerificationResult:
    """Build a normalized verification result for a repair ticket."""
    status_normalized = str(status or "not_run").strip().lower() or "not_run"
    verification_id = _stable_suffix(
        [ticket.ticket_id, ticket.chapter_number, ticket.dimension, status_normalized]
    )
    return RepairVerificationResult(
        verification_id=f"verify_{verification_id}",
        ticket_id=ticket.ticket_id,
        finding_ids=list(ticket.finding_ids or []),
        chapter_number=ticket.chapter_number,
        review_mode=normalize_review_mode(
            getattr(ticket, "review_mode", ""),
            recheck=True,
        ),
        source_module=ticket.source_module,
        dimension=ticket.dimension,
        issue_type=ticket.issue_type,
        status=status_normalized,
        confidence=max(0.0, min(1.0, float(confidence or 0.0))),
        remaining_evidence=list(remaining_evidence or []),
        checked_postconditions=list(getattr(ticket, "postconditions", []) or []),
        source_text_hash=source_text_hash,
        metadata=dict(metadata or {}),
    )


def _compact_verification_text(value: Any) -> str:
    return "".join(str(value or "").split()).lower()


def repair_ticket_matches_remaining_issue(ticket: RepairTicket, issue: Any) -> bool:
    """Return whether a post-repair issue appears to be the same ticket target.

    Prefers issue_id-based matching when the ticket carries finding_ids or
    the issue carries issue_id.  Falls back to text-similarity matching.
    """
    # ID-based matching is authoritative when both sides carry IDs.
    ticket_finding_ids = {
        str(fid).strip() for fid in (ticket.finding_ids or []) if str(fid).strip()
    }
    issue_id = str(
        getattr(issue, "issue_id", "")
        or getattr(issue, "finding_id", "")
        or ""
    ).strip()
    if ticket_finding_ids and issue_id:
        return issue_id in ticket_finding_ids

    # Legacy ticket IDs sometimes embed the source issue ID.
    ticket_id = str(ticket.ticket_id or "").strip()
    if ticket_id and issue_id and len(issue_id) >= 8 and issue_id in ticket_id:
        return True

    # Text-similarity fallback for older issue shapes that lack IDs.
    ticket_type = str(ticket.issue_type or "").strip().lower()
    issue_type = str(getattr(issue, "issue_type", "") or "").strip().lower()
    if ticket_type and issue_type and ticket_type != issue_type:
        return False

    target_summary = _compact_verification_text(ticket.target_summary or ticket.repair_goal)
    issue_summary = _compact_verification_text(getattr(issue, "summary", ""))
    if target_summary and issue_summary:
        return target_summary in issue_summary or issue_summary in target_summary

    evidence = _compact_verification_text((ticket.metadata or {}).get("evidence_quote", ""))
    issue_evidence = _compact_verification_text(
        getattr(issue, "evidence_quote", "") or getattr(issue, "evidence", "")
    )
    return bool(
        evidence
        and issue_evidence
        and (evidence in issue_evidence or issue_evidence in evidence)
    )


def build_ticket_verification_results(
    tickets: list[RepairTicket],
    *,
    remaining_issues: list[Any],
    current_text: str,
    applied: bool,
    metadata: dict[str, Any] | None = None,
) -> list[RepairVerificationResult]:
    """Map post-repair diagnostics back to normalized ticket verification results."""
    text_hash = source_text_hash(current_text)
    results: list[RepairVerificationResult] = []
    for ticket in tickets:
        matched = [
            issue
            for issue in remaining_issues
            if repair_ticket_matches_remaining_issue(ticket, issue)
        ]
        if not applied:
            status = "unresolved"
            confidence = 0.35
        elif matched:
            status = "unresolved"
            confidence = 0.75
        else:
            status = "resolved"
            confidence = 0.8
        remaining_evidence = [
            str(getattr(issue, "evidence_quote", "") or getattr(issue, "evidence", "") or "")[
                :120
            ]
            for issue in matched
            if str(
                getattr(issue, "evidence_quote", "") or getattr(issue, "evidence", "") or ""
            ).strip()
        ]
        results.append(
            build_repair_verification_result(
                ticket,
                status=status,
                confidence=confidence,
                remaining_evidence=remaining_evidence,
                source_text_hash=text_hash,
                metadata={
                    "verification_source": "targeted_recheck",
                    "remaining_match_count": len(matched),
                    **dict(metadata or {}),
                },
            )
        )
    return results
