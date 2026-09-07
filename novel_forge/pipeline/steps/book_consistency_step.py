"""BookConsistencyStep — whole-book consistency audit across chapters."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import re
from dataclasses import dataclass, field
from dataclasses import replace as _dataclass_replace
from pathlib import Path
from typing import Any, Callable

from novel_forge.common.global_audit_dimensions import (
    GLOBAL_AUDIT_DIMENSION_SPECS,
    DimensionAuditResult,
    GlobalAuditDimensionSpec,
)
from novel_forge.core.audit_metrics import AuditQualityMetrics
from novel_forge.core.constants import TaskType
from novel_forge.core.domain.shared_anchor import build_shared_evidence_anchor
from novel_forge.core.exceptions import ContextLengthError
from novel_forge.core.utils.coerce import coerce_float
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.steps.base import PipelineStep
from novel_forge.prompts.context_types import validate_book_consistency_context

_log = get_logger("pipeline.book_consistency")

_ALLOWED_FIX_MODES = {"repair_continuity", "repair_causal", "manual_patch"}
_ALLOWED_FIX_ACTIONS = {"replace", "insert", "delete", "rewrite"}
_SEVERITY_MAP: dict[str, str] = {
    "critical": "critical",
    "high": "critical",
    "warning": "warning",
    "major": "warning",
    "medium": "warning",
    "info": "info",
    "minor": "info",
    "low": "info",
}
_SEVERITY_WEIGHT: dict[str, int] = {"critical": 3, "warning": 2, "info": 1}
_AUDIT_DIMENSIONS: list[tuple[str, TaskType, str]] = [
    (spec.name, spec.task_type, spec.prompt_hint) for spec in GLOBAL_AUDIT_DIMENSION_SPECS
]
_PARA_RE = re.compile(r"(?:第\s*|[Pp]\s*|\[P\s*)(\d+)\s*(?:段|\])")
_DEFAULT_PROMPT_CHAR_BUDGET = 48_000
_MIN_PROMPT_CHAR_BUDGET = 12_000
_MAX_PROMPT_CHAR_BUDGET = 500_000
_MIN_SINGLE_CHAPTER_CHARS = 1_500
_CONTEXT_LENGTH_ERROR_MARKERS = (
    "context window",
    "context_length",
    "maximum context",
    "max context",
    "prompt is too long",
    "too many tokens",
    "context length",
    "context_length_exceeded",
)

CURRENT_CHECKPOINT_SCHEMA_VERSION = 1


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp_float(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _clamp_int(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _is_context_length_error(exc: Exception) -> bool:
    if isinstance(exc, ContextLengthError):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _CONTEXT_LENGTH_ERROR_MARKERS)


def _trim_text_middle(text: str, max_chars: int) -> str:
    """Keep both ends of a long chapter excerpt under a hard char budget."""
    text = str(text or "")
    if len(text) <= max_chars:
        return text
    max_chars = max(200, max_chars)
    head = max_chars // 2
    tail = max_chars - head
    return (
        text[:head].rstrip()
        + "\n[P-TRUNCATED] ……（本章中段因上下文预算被省略）……\n"
        + text[-tail:].lstrip()
    )


def _derive_audit_summary(issues: list[dict[str, Any]]) -> str:
    if not issues:
        return "未发现明确的全书一致性问题。"

    counts: dict[str, int] = {"critical": 0, "warning": 0, "info": 0}
    for issue in issues:
        severity = str(issue.get("severity", "info") or "info").strip().lower()
        normalized = _SEVERITY_MAP.get(severity, "info")
        counts[normalized] = counts.get(normalized, 0) + 1

    parts = [f"共发现 {len(issues)} 个一致性问题"]
    if counts.get("critical"):
        parts.append(f"{counts['critical']} 个严重")
    if counts.get("warning"):
        parts.append(f"{counts['warning']} 个警告")
    if counts.get("info"):
        parts.append(f"{counts['info']} 个提示")

    focus = ""
    first_issue = next((issue for issue in issues if isinstance(issue, dict)), None)
    if first_issue:
        description = str(
            first_issue.get("description")
            or first_issue.get("summary")
            or first_issue.get("issue_type")
            or ""
        ).strip()
        if description:
            focus = f"首要问题：{description[:120]}"

    return "；".join([*parts, focus] if focus else parts)


def _derive_consistency_score(issues: list[dict[str, Any]]) -> float:
    if not issues:
        return 9.5

    critical_count = 0
    warning_count = 0
    info_count = 0

    for issue in issues:
        severity = str(issue.get("severity", "info") or "info").strip().lower()
        normalized = _SEVERITY_MAP.get(severity, "info")
        if normalized == "critical":
            critical_count += 1
        elif normalized == "warning":
            warning_count += 1
        else:
            info_count += 1

    if critical_count >= 3:
        return 5.0

    if critical_count >= 1:
        score = 8.0 - 1.0 * critical_count
        return _clamp_float(score, 0.0, 10.0)

    score = 9.0 - 0.2 * min(warning_count, 10) - 0.05 * min(info_count, 20)
    return _clamp_float(score, 0.0, 10.0)


def _compute_quality_metrics_from_issues(
    issues: list[dict[str, Any]],
    audit_depth: str = "quick",
) -> AuditQualityMetrics:
    """Compute quality metrics from audit issues.

    Computes per-dimension scores for both legacy UI categories and the global
    audit dimensions based on issue distribution.
    """
    # Legacy UI-facing dimensions.  The global audit dimensions still flow
    # through ``_global_dimension_for_issue`` for downstream tasks; here we
    # expose only the six legacy keys the UI / metric layer contract on.
    dimension_names = {
        "naming",
        "timeline",
        "worldbuilding",
        "character_state",
        "plot_thread",
        "narrative_drift",
    }
    if not issues:
        return AuditQualityMetrics(
            coverage_ratio=1.0,
            estimated_miss_rate=0.1,
            dimension_scores={name: 10.0 for name in dimension_names},
            audit_depth=audit_depth,
            total_issues_found=0,
            critical_issues_found=0,
        )

    critical_count = 0
    total_count = len(issues)
    dimension_scores: dict[str, float] = {d: 10.0 for d in dimension_names}
    dim_critical: dict[str, int] = {d: 0 for d in dimension_names}
    dim_warning: dict[str, int] = {d: 0 for d in dimension_names}

    for issue in issues:
        severity = str(issue.get("severity", "info") or "info").strip().lower()
        normalized = _SEVERITY_MAP.get(severity, "info")
        if normalized == "critical":
            critical_count += 1
        category = str(issue.get("category", "") or "").strip().lower()
        if category in dimension_names:
            if normalized == "critical":
                dim_critical[category] += 1
            elif normalized == "warning":
                dim_warning[category] += 1

    for dim in dimension_names:
        c = dim_critical[dim]
        w = dim_warning[dim]
        if c >= 2:
            dimension_scores[dim] = 5.0
        elif c == 1:
            dimension_scores[dim] = 7.5
        else:
            score = 10.0 - 0.3 * min(w, 10)
            dimension_scores[dim] = max(0.0, score)

    miss_rate = min(0.5, 0.1 + 0.1 * critical_count + 0.01 * total_count)
    coverage = max(0.5, 1.0 - miss_rate)

    return AuditQualityMetrics(
        coverage_ratio=coverage,
        estimated_miss_rate=miss_rate,
        dimension_scores=dimension_scores,
        audit_depth=audit_depth,
        total_issues_found=total_count,
        critical_issues_found=critical_count,
    )


def _severity_weight(value: Any) -> int:
    normalized = _SEVERITY_MAP.get(str(value or "info").strip().lower(), "info")
    return _SEVERITY_WEIGHT.get(normalized, 1)


def _limit_issue_dicts(issues: list[dict[str, Any]], max_items: int) -> list[dict[str, Any]]:
    """Keep the highest-value audit issues under an output cap."""
    if max_items <= 0 or len(issues) <= max_items:
        return issues

    indexed = list(enumerate(issues))

    def key(item: tuple[int, dict[str, Any]]) -> tuple[int, int, int, float, int]:
        idx, issue = item
        paragraph_index = _coerce_int(issue.get("paragraph_index"), default=0)
        confidence = coerce_float(issue.get("confidence"), default=0.0)
        chapters_involved = issue.get("chapters_involved", [])
        cross_chapter_count = len(chapters_involved) if isinstance(chapters_involved, list) else 0
        return (
            _severity_weight(issue.get("severity")),
            cross_chapter_count,
            1 if paragraph_index > 0 else 0,
            confidence,
            -idx,
        )

    return [issue for _, issue in sorted(indexed, key=key, reverse=True)[:max_items]]


def _global_dimension_for_issue(issue: dict[str, Any]) -> str:
    explicit = str(issue.get("dimension", "") or "").strip().lower()
    if explicit:
        return explicit
    category = str(issue.get("category", "") or "").strip().lower()
    issue_type = str(issue.get("issue_type", "") or "").strip().lower()
    haystack = f"{category} {issue_type} {issue.get('description', '')}".lower()
    aliases = {
        "timeline": "timeline_arc",
        "time": "timeline_arc",
        "character_state": "character_arc",
        "character": "character_arc",
        "plot_thread": "plot_thread_liveness",
        "thread": "plot_thread_liveness",
        "promise": "promise_payoff",
        "payoff": "promise_payoff",
        "motif": "motif_distribution",
        "worldbuilding": "world_rule_integrity",
        "world_rule": "world_rule_integrity",
        "narrative_drift": "tension_curve",
        "rhythm": "tension_curve",
    }
    for marker, dimension in aliases.items():
        if marker in haystack:
            return dimension
    return category or "plot_thread_liveness"


def _source_hash_for_paragraphs(paragraphs: list[str]) -> str:
    text = "\n\n".join(str(item or "") for item in paragraphs)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _slice_id_for_issue(issue: dict[str, Any], audit_slices: list[dict[str, Any]]) -> str:
    explicit = str(issue.get("slice_id", "") or "").strip()
    if explicit:
        return explicit
    chapters = {
        _coerce_int(ch, 0)
        for ch in (issue.get("chapters_involved") or [])
        if _coerce_int(ch, 0) > 0
    }
    primary = _coerce_int(issue.get("primary_chapter"), 0)
    if primary > 0:
        chapters.add(primary)
    best_id = "whole_book"
    best_overlap = 0
    for audit_slice in audit_slices:
        slice_chapters = {
            _coerce_int(ch, 0)
            for ch in (audit_slice.get("chapters") or [])
            if _coerce_int(ch, 0) > 0
        }
        overlap = len(chapters & slice_chapters)
        if overlap > best_overlap:
            best_overlap = overlap
            best_id = str(audit_slice.get("slice_id") or best_id)
    return best_id


def _build_locator_candidates_for_issue(
    issue: dict[str, Any],
    paragraphs_by_chapter: dict[int, list[str]],
) -> list[dict[str, Any]]:
    primary = _coerce_int(issue.get("primary_chapter"), 0)
    paragraphs = paragraphs_by_chapter.get(primary, []) if primary > 0 else []
    if not paragraphs:
        return []
    span = issue.get("paragraph_span") or []
    paragraph_index = _coerce_int(issue.get("paragraph_index"), 0)
    if isinstance(span, list) and len(span) >= 2:
        start = _coerce_int(span[0], 0)
        end = _coerce_int(span[-1], start)
    else:
        start = paragraph_index
        end = paragraph_index
    if start <= 0:
        return []
    start = max(1, min(start, len(paragraphs)))
    end = max(start, min(end, len(paragraphs)))
    text = "\n\n".join(paragraphs[start - 1 : end])
    evidence = str(issue.get("evidence", "") or "").strip()
    method = "exact" if evidence and evidence in text else "llm_locator"
    confidence = 0.95 if method == "exact" else 0.72
    payload = {
        "chapter_number": primary,
        "paragraph_span": [start, end],
        "text": text[:2000],
        "source_hash": _source_hash_for_paragraphs(paragraphs),
        "locator_method": method,
        "confidence": confidence,
    }
    payload["candidate_id"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return [payload]


def _build_global_findings(
    issue_dicts: list[dict[str, Any]],
    *,
    audit_slices: list[dict[str, Any]],
    paragraphs_by_chapter: dict[int, list[str]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, issue in enumerate(issue_dicts, start=1):
        primary = _coerce_int(issue.get("primary_chapter"), 0)
        if primary <= 0:
            chapters = issue.get("chapters_involved") or []
            if isinstance(chapters, list) and chapters:
                primary = _coerce_int(chapters[0], 0)
        issue_id = str(issue.get("issue_id", "") or "").strip()
        if not issue_id:
            issue_id = f"global_issue_{idx:04d}"
        finding_id = issue_id if issue_id.startswith("global_") else f"global_{issue_id}"
        if finding_id in seen:
            finding_id = f"{finding_id}_{idx}"
        seen.add(finding_id)
        locator_candidates = [
            item for item in (issue.get("locator_candidates") or []) if isinstance(item, dict)
        ] or _build_locator_candidates_for_issue(issue, paragraphs_by_chapter)
        repair_readiness = issue.get("repair_readiness")
        if not isinstance(repair_readiness, dict):
            best = locator_candidates[0] if locator_candidates else {}
            confirmed_locator = str(best.get("locator_method") or "") in {"exact", "fuzzy"}
            if confirmed_locator and float(best.get("confidence", 0.0) or 0.0) >= 0.7:
                repair_readiness = {
                    "status": "ready",
                    "risk": "medium",
                    "reasons": ["locator_candidate_confirmed"],
                    "auto_repair_eligible": True,
                }
            else:
                repair_readiness = {
                    "status": "verify_first",
                    "risk": "medium",
                    "reasons": ["missing_precision_anchor"],
                    "auto_repair_eligible": False,
                }
        finding = {
            "finding_id": finding_id,
            "slice_id": _slice_id_for_issue(issue, audit_slices),
            "dimension": _global_dimension_for_issue(issue),
            "severity": issue.get("severity", "info"),
            "chapters_involved": issue.get("chapters_involved")
            or ([primary] if primary > 0 else []),
            "primary_chapter": primary,
            "evidence_pairs": [
                item for item in (issue.get("evidence_pairs") or []) if isinstance(item, dict)
            ],
            "locator_candidates": locator_candidates,
            "repair_readiness": repair_readiness,
            "source_issue_id": issue.get("issue_id") or "",
            "description": issue.get("description") or issue.get("summary") or "",
        }
        findings.append(finding)
    return findings


def _build_coverage_metrics(
    *,
    audit_slices: list[dict[str, Any]],
    chapters_audited: list[int],
    issue_dicts: list[dict[str, Any]],
    global_findings: list[dict[str, Any]],
    paragraph_indexed_chapters: int,
) -> dict[str, Any]:
    dimension_hits = {name: 0 for name, _, _ in _AUDIT_DIMENSIONS}
    for finding in global_findings:
        dim = str(finding.get("dimension") or "")
        if dim in dimension_hits:
            dimension_hits[dim] += 1
    slice_chapters = {
        _coerce_int(ch, 0)
        for audit_slice in audit_slices
        for ch in (audit_slice.get("chapters") or [])
        if _coerce_int(ch, 0) > 0
    }
    audited_set = {int(ch) for ch in chapters_audited if int(ch) > 0}
    coverage_ratio = len(slice_chapters & audited_set) / len(audited_set) if audited_set else 0.0
    return {
        "architecture": "global_audit_v1",
        "slice_count": len(audit_slices),
        "chapters_audited": len(audited_set),
        "slice_chapter_coverage_ratio": round(coverage_ratio, 4),
        "paragraph_indexed_chapters": paragraph_indexed_chapters,
        "issue_count": len(issue_dicts),
        "global_finding_count": len(global_findings),
        "dimension_hits": dimension_hits,
    }


def _slice_chapters_for_context(audit_slice: dict[str, Any]) -> set[int]:
    chapters = {
        _coerce_int(ch, 0) for ch in (audit_slice.get("chapters") or []) if _coerce_int(ch, 0) > 0
    }
    for ch in audit_slice.get("boundary_chapters") or []:
        num = _coerce_int(ch, 0)
        if num > 0:
            chapters.add(num)
    return chapters


def _fallback_whole_book_slice(base_ctx: dict[str, Any]) -> dict[str, Any]:
    chapters = [
        _coerce_int(item.get("chapter_number"), 0)
        for item in base_ctx.get("chapter_summaries", [])
        if isinstance(item, dict) and _coerce_int(item.get("chapter_number"), 0) > 0
    ]
    return {
        "slice_id": "whole_book",
        "slice_kind": "whole_book",
        "chapters": chapters,
        "boundary_chapters": [],
        "focus_dimensions": [spec.name for spec in GLOBAL_AUDIT_DIMENSION_SPECS],
        "dimension_role": "shared_whole_book",
        "source_refs": [{"source": "dimension_scheduler_fallback"}],
        "status": "pending",
    }


class DimensionContextBuilder:
    """Build dimension-specific contexts from the shared book-audit context."""

    _ALWAYS_KEYS = {
        "analysis_mode",
        "location_strictness",
        "max_issues_per_chunk",
        "shared_evidence_anchor",
    }
    _TEMPLATE_DEFAULTS: dict[str, Any] = {
        "chapter_summaries": [],
        "chapter_texts": [],
        "chapter_issue_pool": [],
        "canon_characters": {},
        "canon_relationships": [],
        "character_bible_names": [],
        "character_profiles_compact": [],
        "world_rules": [],
        "world_setting": "",
        "world_hint": "",
        "story_theme": "",
        "conflict_hint": "",
        "arc_summary": "",
        "outline_summary": "",
        "analysis_mode": "summary",
        "location_strictness": "balanced",
        "prompt_hint": "",
        "audit_chunk_notice": "",
        "audit_slices": [],
        "semantic_evidence_context": [],
        "memory_enhancement_context": "",
        "shared_evidence_anchor": {},
        "active_audit_dimension": "",
        "dimension_focus": {},
        "dimension_ledger_context": [],
    }

    def build(
        self,
        *,
        spec: GlobalAuditDimensionSpec,
        base_ctx: dict[str, Any],
        audit_slice: dict[str, Any],
        chapter_texts: list[dict[str, Any]],
        dimension_ledger_context: list[dict[str, Any]],
        audit_chunk_notice: str = "",
    ) -> dict[str, Any]:
        wanted = set(spec.required_context_keys) | self._ALWAYS_KEYS
        ctx: dict[str, Any] = dict(self._TEMPLATE_DEFAULTS)
        ctx.update(
            {
                key: base_ctx[key]
                for key in wanted
                if key in base_ctx and key not in {"chapter_summaries", "chapter_texts"}
            }
        )
        slice_chapters = _slice_chapters_for_context(audit_slice)
        summaries = [
            item
            for item in base_ctx.get("chapter_summaries", [])
            if isinstance(item, dict)
            and (not slice_chapters or _coerce_int(item.get("chapter_number"), 0) in slice_chapters)
        ]
        if "chapter_summaries" in wanted:
            ctx["chapter_summaries"] = summaries
        else:
            ctx["chapter_summaries"] = [
                {"chapter_number": item.get("chapter_number"), "summary": item.get("summary", "")}
                for item in summaries
                if isinstance(item, dict)
            ]
        ctx["chapter_texts"] = chapter_texts if "chapter_texts" in wanted else []
        ctx["audit_slices"] = [audit_slice]
        ctx["semantic_evidence_context"] = self._dimension_semantic_context(
            spec,
            base_ctx.get("semantic_evidence_context", []),
            slice_chapters,
        )
        ctx["dimension_ledger_context"] = dimension_ledger_context[-12:]
        ctx["active_audit_dimension"] = spec.name
        ctx["dimension_focus"] = {
            "name": spec.name,
            "slice_id": audit_slice.get("slice_id") or "whole_book",
            "slice_kind": audit_slice.get("slice_kind") or "",
            "output_focus": list(spec.output_focus),
            "dependencies": list(spec.dependencies),
            "semantic_queries": self._semantic_queries(spec, slice_chapters),
            "out_of_scope": [
                item.name for item in GLOBAL_AUDIT_DIMENSION_SPECS if item.name != spec.name
            ],
        }
        ctx["dimension_audit_bundle"] = {
            "schema": "DimensionAuditBundle",
            "dimension": spec.name,
            "focus_chapters": sorted(slice_chapters),
            "evidence_slices": [audit_slice],
            "ledger_refs": dimension_ledger_context[-12:],
            "out_of_scope": ctx["dimension_focus"]["out_of_scope"],
            "max_issue_budget": ctx.get("max_issues_per_chunk", 12),
        }
        base_hint = str(base_ctx.get("prompt_hint", "") or "").strip()
        hint_parts = [
            base_hint,
            spec.prompt_hint,
            f"当前维度={spec.name}；当前切片={audit_slice.get('slice_id') or 'whole_book'}。",
            "dimension_audit_bundle 是本轮唯一审计工单；out_of_scope 维度只用于避免越界，不得补审。",
            "只输出本维度真实、可验证、可交接的问题；不要补审其他维度。",
        ]
        ctx["prompt_hint"] = "\n".join(part for part in hint_parts if part)
        ctx["audit_chunk_notice"] = audit_chunk_notice
        ctx["__task_type"] = spec.task_type
        ctx["__audit_dimension"] = spec.name
        ctx["__disable_dimension_parallel"] = True
        return ctx

    @staticmethod
    def _semantic_queries(spec: GlobalAuditDimensionSpec, chapters: set[int]) -> list[str]:
        if not chapters:
            chapters = {0}
        queries: list[str] = []
        for chapter in sorted(chapters):
            for template in spec.semantic_query_templates:
                queries.append(template.format(chapter=chapter))
        return queries

    @staticmethod
    def _dimension_semantic_context(
        spec: GlobalAuditDimensionSpec,
        raw_items: Any,
        chapters: set[int],
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_items, list):
            return []
        selected: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            dim = str(item.get("dimension") or item.get("category") or "").strip().lower()
            chapter = _coerce_int(item.get("chapter_number") or item.get("primary_chapter"), 0)
            if dim and dim != spec.name:
                continue
            if chapters and chapter > 0 and chapter not in chapters:
                continue
            enriched = dict(item)
            enriched.setdefault("dimension", spec.name)
            selected.append(enriched)
        return selected


class CrossDimensionAdjudicator:
    """Local pre-adjudication before optional LLM cross-dimension judgement."""

    def adjudicate(
        self,
        dimension_results: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        findings: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        by_fingerprint: dict[tuple[int, str], list[dict[str, Any]]] = {}
        for result in dimension_results:
            if not isinstance(result, dict):
                continue
            dimension = str(result.get("dimension") or "").strip()
            for finding in result.get("findings") or []:
                if not isinstance(finding, dict):
                    continue
                item = dict(finding)
                item.setdefault("dimension", dimension)
                item.setdefault("slice_id", result.get("slice_id") or "whole_book")
                findings.append(item)
                description = str(item.get("description") or item.get("summary") or "").strip()
                evidence = str(item.get("evidence") or "").strip()
                fingerprint = (
                    _coerce_int(item.get("primary_chapter"), 0),
                    hashlib.md5((description[:120] + evidence[:120]).encode()).hexdigest()[:12],
                )
                by_fingerprint.setdefault(fingerprint, []).append(item)

        for group in by_fingerprint.values():
            dimensions = sorted(
                {
                    str(item.get("dimension") or "").strip()
                    for item in group
                    if item.get("dimension")
                }
            )
            severities = sorted(
                {str(item.get("severity") or "").strip() for item in group if item.get("severity")}
            )
            if len(group) <= 1 or len(dimensions) <= 1:
                continue
            conflict = {
                "conflict_id": "cross_dim_"
                + hashlib.sha256(
                    json.dumps(group, ensure_ascii=False, sort_keys=True, default=str).encode()
                ).hexdigest()[:12],
                "status": "needs_llm_judgement",
                "dimensions": dimensions,
                "severity_values": severities,
                "issue_ids": [
                    str(item.get("issue_id") or item.get("finding_id") or "")
                    for item in group
                    if str(item.get("issue_id") or item.get("finding_id") or "")
                ],
                "reason": "same_or_overlapping_claim_reported_by_multiple_dimensions",
            }
            conflicts.append(conflict)
            for item in group:
                item["cross_dimension_status"] = "needs_judgement"
                item["repair_readiness"] = {
                    "status": "verify_first",
                    "risk": "medium",
                    "reasons": ["cross_dimension_judgement_required"],
                    "auto_repair_eligible": False,
                }
        return findings, conflicts


@dataclass
class BookConsistencyInput:
    """Input for whole-book consistency audit."""

    chapter_summaries: list[dict[str, Any]]
    canon_state_snapshot: dict[str, Any]
    character_bible: dict[str, Any]
    outline: dict[str, Any]
    chapter_texts: list[dict[str, Any]] = field(default_factory=list)
    chapter_issue_pool: list[dict[str, Any]] = field(default_factory=list)
    analysis_mode: str = "summary"
    max_chapters_per_batch: int = 12
    max_issues_per_chunk: int = 12
    issue_pool_max_items: int = 160
    prompt_hint: str = ""
    location_strictness: str = "balanced"
    max_tokens: int = 4096
    temperature: float = 0.3
    # Richer context for deeper consistency detection
    world_rules: list[str] = field(default_factory=list)
    """World rules / hard constraints from story_bible (magic system, social rules, etc.)"""
    world_setting: str = ""
    """World setting description: era + geography + magic_or_tech combined."""
    character_profiles_compact: list[dict[str, Any]] = field(default_factory=list)
    """Compact character profiles: name, gender, role, identity snippet."""
    arc_summary: str = ""
    """Volume/arc structure summary from outline."""
    story_theme: str = ""
    """Core narrative theme / premise from spec."""
    conflict_hint: str = ""
    """External + internal conflict definitions from spec."""
    world_hint: str = ""
    """Extended world description from spec (supplements world_setting)."""
    memory_context_factory: Any | None = None
    """Optional factory callable returning AuditContext for memory enhancement."""
    memory_enhancement_context: str = ""
    """Memory-enhanced context from AuditCoordinator for cross-chapter awareness."""
    chapter_number: int = 0
    """Current chapter number being audited (0 for whole-book audit)."""
    audit_checkpoint_path: Path | None = None
    """Optional resumable full-text audit checkpoint path."""
    resume_audit_checkpoint: bool = False
    """Reuse matching completed full-text audit chunks from checkpoint."""
    on_chunk_progress: Callable[[int, int], None] | None = None
    """Optional callback(current_chunk, total_chunks) emitted after each chunk is processed."""
    parallel_chunks: bool | None = None
    """Override settings.long_book_audit_parallel_chunks for this request."""
    parallel_dimensions: bool | None = None
    """Override settings.long_book_audit_parallel_dimensions for this request."""
    parallel_dimension_limit: int | None = None
    """Override settings.book_audit_max_parallel for this request."""
    kernel_context: dict[str, Any] | None = None
    """StoryKernel field slices from ContextComposer (merged into prompt context)."""
    audit_slices: list[dict[str, Any]] = field(default_factory=list)
    """Global audit slices planned from volumes, blueprint phases, and ledgers."""
    semantic_evidence_context: list[dict[str, Any]] = field(default_factory=list)
    """Optional semantic recall snippets used for targeted evidence location."""
    seeded_dimension_results: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    """Previously verified read-only dimension/slice results with matching signatures."""


@dataclass
class ConsistencyIssue:
    """One consistency issue found during audit."""

    category: str  # naming, timeline, worldbuilding, character_state, narrative_drift
    severity: str  # critical, warning, info
    chapters_involved: list[int]
    description: str
    issue_id: str = ""
    dimension: str = ""
    suggestion: str = ""
    issue_type: str = ""
    primary_chapter: int = 0
    location: str = ""
    paragraph_index: int = 0
    paragraph_span: list[int] = field(default_factory=list)
    evidence: str = ""
    fix_mode: str = "repair_continuity"
    fix_action: str = "rewrite"
    confidence: float = 0.0
    verification_status: str = ""  # "confirmed", "suspected", "unlikely", "verified", "rejected"
    linked_issue_refs: list[dict[str, Any]] = field(default_factory=list)
    evidence_pairs: list[dict[str, Any]] = field(default_factory=list)
    verification_questions: list[str] = field(default_factory=list)
    handoff_notes: str = ""

    def model_dump(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "dimension": self.dimension,
            "category": self.category,
            "severity": self.severity,
            "chapters_involved": self.chapters_involved,
            "description": self.description,
            "suggestion": self.suggestion,
            "issue_type": self.issue_type,
            "primary_chapter": self.primary_chapter,
            "location": self.location,
            "paragraph_index": self.paragraph_index,
            "paragraph_span": self.paragraph_span,
            "evidence": self.evidence,
            "fix_mode": self.fix_mode,
            "fix_action": self.fix_action,
            "confidence": self.confidence,
            "verification_status": self.verification_status,
            "linked_issue_refs": self.linked_issue_refs,
            "evidence_pairs": self.evidence_pairs,
            "verification_questions": self.verification_questions,
            "handoff_notes": self.handoff_notes,
        }


@dataclass
class BookConsistencyResult:
    """Result of whole-book consistency audit."""

    issues: list[ConsistencyIssue] = field(default_factory=list)
    summary: str = ""
    consistency_score: float = 0.0
    analysis_mode: str = "summary"
    chapters_audited: list[int] = field(default_factory=list)
    truncated_chapters: list[int] = field(default_factory=list)
    repair_plan: list[dict[str, Any]] = field(default_factory=list)
    auto_repair: dict[str, Any] | None = None
    empty_result_reason: str | None = None
    quality_metrics: AuditQualityMetrics | None = None
    field_sources: dict[str, str] = field(default_factory=dict)
    slices: list[dict[str, Any]] = field(default_factory=list)
    dimension_results: list[dict[str, Any]] = field(default_factory=list)
    cross_dimension_conflicts: list[dict[str, Any]] = field(default_factory=list)
    global_findings: list[dict[str, Any]] = field(default_factory=list)
    repair_queue_summary: dict[str, Any] = field(default_factory=dict)
    revision_queue: list[dict[str, Any]] = field(default_factory=list)
    coverage_metrics: dict[str, Any] = field(default_factory=dict)
    input_manifest: dict[str, Any] = field(default_factory=dict)
    incremental_reuse: dict[str, Any] = field(default_factory=dict)

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        result = {
            "issues": [_dump_consistency_issue(item) for item in self.issues],
            "summary": self.summary,
            "consistency_score": self.consistency_score,
            "analysis_mode": self.analysis_mode,
            "chapters_audited": self.chapters_audited,
            "truncated_chapters": self.truncated_chapters,
            "repair_plan": self.repair_plan,
            "auto_repair": self.auto_repair,
            "empty_result_reason": self.empty_result_reason,
            "quality_metrics": self.quality_metrics.model_dump(**kwargs)
            if self.quality_metrics is not None
            else None,
            "field_sources": self.field_sources,
            "slices": self.slices,
            "dimension_results": self.dimension_results,
            "cross_dimension_conflicts": self.cross_dimension_conflicts,
            "global_findings": self.global_findings,
            "repair_queue_summary": self.repair_queue_summary,
            "revision_queue": self.revision_queue,
            "coverage_metrics": self.coverage_metrics,
            "input_manifest": self.input_manifest,
            "incremental_reuse": self.incremental_reuse,
        }
        return result


def _dump_consistency_issue(item: Any) -> dict[str, Any]:
    """Serialize a consistency issue from typed or merged dict form."""
    if isinstance(item, dict):
        return dict(item)
    if hasattr(item, "model_dump"):
        dumped = item.model_dump()
        return dumped if isinstance(dumped, dict) else {}
    if dataclasses.is_dataclass(item) and not isinstance(item, type):
        dumped = dataclasses.asdict(item)
        return dumped if isinstance(dumped, dict) else {}
    return {}


class BookConsistencyStep(PipelineStep[BookConsistencyInput, BookConsistencyResult]):
    """Audit whole-book consistency using LLM analysis."""

    @property
    def step_name(self) -> str:
        return "book_consistency"

    def _prompt_char_budget(self) -> int:
        raw_budget = int(
            getattr(
                self.settings, "long_book_audit_prompt_char_budget", _DEFAULT_PROMPT_CHAR_BUDGET
            )
            or _DEFAULT_PROMPT_CHAR_BUDGET
        )
        return _clamp_int(raw_budget, _MIN_PROMPT_CHAR_BUDGET, _MAX_PROMPT_CHAR_BUDGET)

    def _rendered_prompt_len(self, ctx: dict[str, Any]) -> int:
        try:
            return len(self._builder.render(TaskType.BOOK_CONSISTENCY, ctx))
        except Exception:
            # Rendering should not fail here, but this is only a sizing heuristic.
            return len(str(ctx))

    @staticmethod
    def _compact_chapter_summaries(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compacted: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            key_events_raw = item.get("key_events") or []
            key_events = (
                [str(event) for event in key_events_raw] if isinstance(key_events_raw, list) else []
            )
            compacted.append(
                {
                    "chapter_number": item.get("chapter_number", 0),
                    "summary": str(item.get("summary", "") or ""),
                    "key_events": key_events,
                }
            )
        return compacted

    @staticmethod
    def _compact_issue_pool(
        items: list[dict[str, Any]],
        *,
        max_items: int = 160,
    ) -> list[dict[str, Any]]:
        compacted: list[dict[str, Any]] = []

        indexed_items = [(idx, item) for idx, item in enumerate(items) if isinstance(item, dict)]

        def key(indexed: tuple[int, dict[str, Any]]) -> tuple[int, int, int]:
            idx, item = indexed
            lane = str(item.get("lane", "") or "").strip().lower()
            return (
                _severity_weight(item.get("severity")),
                1 if lane in {"continuity", "causal"} else 0,
                -idx,
            )

        limit = max(0, min(int(max_items or 0), 1000))
        for _, item in sorted(indexed_items, key=key, reverse=True)[:limit]:
            if not isinstance(item, dict):
                continue
            compacted.append(
                {
                    "chapter_number": item.get("chapter_number", 0),
                    "lane": str(item.get("lane", "") or ""),
                    "index": item.get("index", 0),
                    "issue_type": str(item.get("issue_type", "") or ""),
                    "severity": str(item.get("severity", "") or ""),
                    "location": str(item.get("location", "") or ""),
                    "summary": str(item.get("summary", "") or ""),
                }
            )
        return compacted

    def _build_prompt_context(
        self,
        input_data: BookConsistencyInput,
        *,
        characters: Any,
        relationships: Any,
        bible_names: list[str],
        character_profiles_compact: list[dict[str, Any]],
        outline_summary: str,
        chapter_texts_for_template: list[dict[str, Any]],
        audit_chunk_notice: str = "",
    ) -> dict[str, Any]:
        chapter_summaries = self._compact_chapter_summaries(input_data.chapter_summaries)
        chapter_issue_pool = self._compact_issue_pool(
            input_data.chapter_issue_pool,
            max_items=input_data.issue_pool_max_items,
        )
        # Merge kernel_context as base layer; explicit fields take precedence.
        ctx: dict[str, Any] = {}
        if input_data.kernel_context is not None:
            ctx.update(input_data.kernel_context)
        ctx.update(
            {
                "chapter_summaries": chapter_summaries,
                "chapter_texts": chapter_texts_for_template,
                "chapter_issue_pool": chapter_issue_pool,
                "max_issues_per_chunk": _clamp_int(
                    int(input_data.max_issues_per_chunk or 12), 1, 50
                ),
                "canon_characters": characters,
                "canon_relationships": relationships,
                "character_bible_names": bible_names,
                "character_profiles_compact": character_profiles_compact,
                "world_rules": input_data.world_rules,
                "world_setting": input_data.world_setting,
                "world_hint": input_data.world_hint,
                "story_theme": input_data.story_theme,
                "conflict_hint": input_data.conflict_hint,
                "arc_summary": input_data.arc_summary or outline_summary,
                "outline_summary": outline_summary,
                "analysis_mode": input_data.analysis_mode,
                "location_strictness": input_data.location_strictness,
                "prompt_hint": input_data.prompt_hint,
                "audit_chunk_notice": audit_chunk_notice,
                "audit_slices": input_data.audit_slices,
                "semantic_evidence_context": input_data.semantic_evidence_context,
                "memory_enhancement_context": input_data.memory_enhancement_context,
                "active_audit_dimension": "",
                "dimension_focus": {},
                "dimension_ledger_context": [],
                "shared_evidence_anchor": build_shared_evidence_anchor(
                    "book_consistency.audit_context",
                    {
                        "chapter_summaries": chapter_summaries,
                        "chapter_issue_pool": chapter_issue_pool,
                        "canon_characters": characters,
                        "canon_relationships": relationships,
                        "character_bible_names": bible_names,
                        "world_rules": input_data.world_rules,
                        "story_theme": input_data.story_theme,
                        "outline_summary": outline_summary,
                        "analysis_mode": input_data.analysis_mode,
                        "audit_slices": input_data.audit_slices,
                    },
                    max_string_chars=900,
                ),
                "__parallel_dimensions": bool(
                    (
                        input_data.parallel_dimensions
                        if input_data.parallel_dimensions is not None
                        else getattr(self.settings, "long_book_audit_parallel_dimensions", True)
                    )
                    and input_data.audit_slices
                ),
                "__parallel_dimension_limit": (
                    input_data.parallel_dimension_limit
                    if input_data.parallel_dimension_limit is not None
                    else getattr(self.settings, "long_book_audit_parallel_dimension_limit", 3)
                ),
            }
        )
        validate_book_consistency_context(
            ctx,
            task_type=TaskType.BOOK_CONSISTENCY,
            source="BookConsistencyStep._build_prompt_context",
        )
        return ctx

    def _chapter_text_chunks(
        self,
        base_ctx: dict[str, Any],
        chapter_texts_for_template: list[dict[str, Any]],
        max_chapters_per_batch: int,
    ) -> list[list[dict[str, Any]]]:
        if not chapter_texts_for_template:
            return [[]]

        budget = self._prompt_char_budget()
        chapter_limit = max(1, min(int(max_chapters_per_batch or 1), 500))
        chunks: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []

        for chapter_text in chapter_texts_for_template:
            candidate = [*current, chapter_text]
            candidate_ctx = dict(base_ctx, chapter_texts=candidate)
            if current and (
                len(candidate) > chapter_limit or self._rendered_prompt_len(candidate_ctx) > budget
            ):
                chunks.append(current)
                current = []
                candidate = [chapter_text]

            single_ctx = dict(base_ctx, chapter_texts=[chapter_text])
            if self._rendered_prompt_len(single_ctx) > budget:
                # Split the long chapter into sequential sub-chunks instead of
                # binary truncation, so no content is lost.
                sub_chunks = self._split_long_chapter(base_ctx, chapter_text, budget)
                chunks.extend(sub_chunks)
                continue

            current = candidate

        if current:
            chunks.append(current)
        return chunks or [[]]

    def _summary_context_chunks(
        self,
        context: dict[str, Any],
        max_chapters_per_batch: int,
    ) -> list[dict[str, Any]]:
        """Batch complete chapter summaries without silently dropping chapters."""
        summaries = [
            item for item in context.get("chapter_summaries", []) if isinstance(item, dict)
        ]
        if not summaries:
            return [context]
        budget = self._prompt_char_budget()
        chapter_limit = max(1, min(int(max_chapters_per_batch or 1), 500))
        chunks: list[dict[str, Any]] = []
        current: list[dict[str, Any]] = []
        for summary in summaries:
            candidate = [*current, summary]
            candidate_context = dict(context, chapter_summaries=candidate)
            if current and (
                len(candidate) > chapter_limit
                or self._rendered_prompt_len(candidate_context) > budget
            ):
                chunks.append(dict(context, chapter_summaries=current))
                current = [summary]
            else:
                current = candidate
        if current:
            chunks.append(dict(context, chapter_summaries=current))
        total = len(chunks)
        if total > 1:
            for index, chunk in enumerate(chunks, start=1):
                chunk["audit_chunk_notice"] = (
                    f"当前审计切片的完整章节摘要被拆分为 {total} 个上下文块；"
                    f"当前块 {index}/{total}。各块结果将在切片层合并。"
                )
        return chunks

    def _split_long_chapter(
        self,
        base_ctx: dict[str, Any],
        chapter_text: dict[str, Any],
        budget: int,
    ) -> list[list[dict[str, Any]]]:
        """Split a single chapter that exceeds the prompt budget into sequential sub-chunks.

        Each sub-chunk is sized to fit within the budget when combined with the
        base context (canon, summaries, etc.).  No content is lost — every
        character of the original chapter text appears in exactly one sub-chunk.
        """
        source_text = str(chapter_text.get("numbered_text", "") or "")
        if not source_text:
            return [[chapter_text]]

        # Calculate the overhead: prompt length without any chapter text.
        overhead = self._rendered_prompt_len(dict(base_ctx, chapter_texts=[]))
        # Reserve 2,500 chars for the audit_chunk_notice and template framing.
        max_part_chars = budget - overhead - 2500
        max_part_chars = max(_MIN_SINGLE_CHAPTER_CHARS, max_part_chars)

        # Determine how many parts we need.  Do not cap the part count: every
        # source character must remain inside a provider-safe chunk.
        total_len = len(source_text)
        num_parts = max(1, (total_len + max_part_chars - 1) // max_part_chars)
        part_size = (total_len + num_parts - 1) // num_parts

        sub_chunks: list[list[dict[str, Any]]] = []
        for part_idx in range(num_parts):
            start = part_idx * part_size
            end = min(start + part_size, total_len)
            if start >= total_len:
                break

            part_text = source_text[start:end]
            trimmed = dict(chapter_text)
            trimmed["numbered_text"] = part_text
            trimmed["truncated"] = True
            trimmed["prompt_truncated"] = True
            trimmed["part_number"] = part_idx + 1
            trimmed["total_parts"] = num_parts
            trimmed["source_chars"] = total_len

            sub_chunks.append([trimmed])

        return sub_chunks

    async def _call_audit_context(
        self,
        ctx: dict[str, Any],
        *,
        max_tokens: int,
        temperature: float,
        depth: int = 0,
    ) -> list[dict[str, Any]]:
        _use_dim_parallel = bool(
            ctx.get(
                "__parallel_dimensions",
                getattr(self.settings, "long_book_audit_parallel_dimensions", True),
            )
        ) and not bool(ctx.get("__disable_dimension_parallel", False))
        if _use_dim_parallel:
            import asyncio as _asyncio

            raw_dim_limit = ctx.get(
                "__parallel_dimension_limit",
                getattr(self.settings, "long_book_audit_parallel_dimension_limit", 3),
            )
            try:
                dim_limit = int(raw_dim_limit or 3)
            except (TypeError, ValueError):
                dim_limit = 3
            dim_limit = max(1, min(dim_limit, len(_AUDIT_DIMENSIONS)))
            _dim_sem = _asyncio.Semaphore(dim_limit)

            async def _call_one_dimension(
                _dim_name: str,
                dim_task_type: TaskType,
                dim_hint: str,
            ) -> list[dict[str, Any]]:
                async with _dim_sem:
                    dim_ctx = dict(
                        ctx,
                        prompt_hint=((ctx.get("prompt_hint", "") or "") + "\n\n" + dim_hint),
                        active_audit_dimension=_dim_name,
                        dimension_focus={"name": _dim_name},
                        dimension_ledger_context=[],
                        __task_type=dim_task_type,
                        __audit_dimension=_dim_name,
                    )
                    dim_ctx["__disable_dimension_parallel"] = True
                    return await self._call_audit_context(
                        dim_ctx,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        depth=depth,
                    )

            tasks = [
                _call_one_dimension(name, task_type, hint)
                for name, task_type, hint in _AUDIT_DIMENSIONS
            ]
            all_results = await _asyncio.gather(*tasks)
            merged = []
            for results in all_results:
                merged.extend(results)
            return merged

        try:
            task_type = ctx.get("__task_type", TaskType.BOOK_CONSISTENCY)
            if not isinstance(task_type, TaskType):
                task_type = TaskType.BOOK_CONSISTENCY
            parsed = await self._call_with_retry(
                task_type,
                ctx,
                max_tokens=max(1024, max_tokens),
                temperature=temperature,
            )
            return [parsed] if isinstance(parsed, dict) else []
        except Exception as exc:
            if not _is_context_length_error(exc) or depth >= 8:
                raise

            chapter_texts = [
                item for item in ctx.get("chapter_texts", []) if isinstance(item, dict)
            ]
            if len(chapter_texts) > 1:
                mid = max(1, len(chapter_texts) // 2)
                overlap_size = min(2, len(chapter_texts) // 4)

                left_chapters = list(chapter_texts[:mid])
                right_chapters = list(chapter_texts[mid:])

                left_boundary = left_chapters[-overlap_size:]
                right_boundary = right_chapters[:overlap_size]
                left_chapters.extend(right_boundary)
                right_chapters[:0] = left_boundary

                left_ctx = dict(ctx, chapter_texts=left_chapters)
                right_ctx = dict(ctx, chapter_texts=right_chapters)
                left = await self._call_audit_context(
                    left_ctx,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    depth=depth + 1,
                )
                right = await self._call_audit_context(
                    right_ctx,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    depth=depth + 1,
                )
                return [*left, *right]

            if len(chapter_texts) == 1:
                chapter_text = dict(chapter_texts[0])
                source_text = str(chapter_text.get("numbered_text", "") or "")
                if len(source_text) > _MIN_SINGLE_CHAPTER_CHARS:
                    midpoint = max(1, len(source_text) // 2)
                    left_chapter = dict(chapter_text)
                    right_chapter = dict(chapter_text)
                    left_chapter.update(
                        {
                            "numbered_text": source_text[:midpoint],
                            "part_number": 1,
                            "total_parts": 2,
                            "source_chars": len(source_text),
                            "prompt_partitioned": True,
                        }
                    )
                    right_chapter.update(
                        {
                            "numbered_text": source_text[midpoint:],
                            "part_number": 2,
                            "total_parts": 2,
                            "source_chars": len(source_text),
                            "prompt_partitioned": True,
                        }
                    )
                    left = await self._call_audit_context(
                        dict(ctx, chapter_texts=[left_chapter]),
                        max_tokens=max_tokens,
                        temperature=temperature,
                        depth=depth + 1,
                    )
                    right = await self._call_audit_context(
                        dict(ctx, chapter_texts=[right_chapter]),
                        max_tokens=max_tokens,
                        temperature=temperature,
                        depth=depth + 1,
                    )
                    return [*left, *right]

            # The non-text context itself is too large.  A summary fallback
            # would silently turn a full-text audit into an incomplete one.
            raise

    @staticmethod
    def _normalize_audit_payload(
        parsed: dict[str, Any],
        *,
        max_issues: int = 0,
    ) -> dict[str, Any]:
        """Fill derivable audit fields lost by permissive JSON repair."""
        issues_raw = parsed.get("issues", [])
        issues = (
            [item for item in issues_raw if isinstance(item, dict)]
            if isinstance(issues_raw, list)
            else []
        )
        if max_issues > 0:
            issues = _limit_issue_dicts(issues, max_issues)

        normalized = dict(parsed)
        normalized["issues"] = issues
        if not isinstance(normalized.get("repair_plan"), list):
            normalized["repair_plan"] = []
        elif max_issues > 0:
            allowed_issue_ids = {
                str(issue.get("issue_id", "") or "").strip()
                for issue in issues
                if str(issue.get("issue_id", "") or "").strip()
            }
            filtered_plan: list[dict[str, Any]] = []
            for plan in normalized["repair_plan"]:
                if not isinstance(plan, dict):
                    continue
                issue_ids = plan.get("issue_ids", [])
                if allowed_issue_ids and isinstance(issue_ids, list):
                    kept_ids = [
                        str(issue_id)
                        for issue_id in issue_ids
                        if str(issue_id).strip() in allowed_issue_ids
                    ]
                    if not kept_ids:
                        continue
                    plan = dict(plan, issue_ids=kept_ids)
                filtered_plan.append(plan)
            normalized["repair_plan"] = filtered_plan[:max_issues]

        # Track what LLM originally provided for empty result diagnosis
        normalized["_llm_had_summary"] = bool(str(normalized.get("summary", "") or "").strip())
        normalized["_llm_had_score"] = (
            coerce_float(normalized.get("consistency_score"), default=-1.0) >= 0
        )

        summary = str(normalized.get("summary", "") or "").strip()
        if not summary:
            normalized["summary"] = _derive_audit_summary(issues)

        score = coerce_float(normalized.get("consistency_score"), default=-1.0)
        if score < 0:
            normalized["consistency_score"] = _derive_consistency_score(issues)

        return normalized

    @staticmethod
    def _checkpoint_signature(
        input_data: BookConsistencyInput,
        chapter_texts_for_template: list[dict[str, Any]],
    ) -> str:
        chapters: list[dict[str, Any]] = []
        for item in chapter_texts_for_template:
            text = str(item.get("numbered_text", "") or "")
            chapters.append(
                {
                    "chapter_number": int(item.get("chapter_number", 0) or 0),
                    "source_chars": int(item.get("source_chars", 0) or 0),
                    "truncated": bool(item.get("truncated", False)),
                    "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                }
            )
        payload = {
            "schema_version": CURRENT_CHECKPOINT_SCHEMA_VERSION,
            "analysis_mode": input_data.analysis_mode,
            "max_chapters_per_batch": int(input_data.max_chapters_per_batch or 0),
            "max_issues_per_chunk": int(getattr(input_data, "max_issues_per_chunk", 0) or 0),
            "issue_pool_max_items": int(getattr(input_data, "issue_pool_max_items", 0) or 0),
            "location_strictness": input_data.location_strictness,
            "prompt_hint": input_data.prompt_hint,
            "world_rules": getattr(input_data, "world_rules", []),
            "world_setting": getattr(input_data, "world_setting", ""),
            "character_profiles_compact": getattr(
                input_data,
                "character_profiles_compact",
                [],
            ),
            "arc_summary": getattr(input_data, "arc_summary", ""),
            "story_theme": getattr(input_data, "story_theme", ""),
            "conflict_hint": getattr(input_data, "conflict_hint", ""),
            "world_hint": getattr(input_data, "world_hint", ""),
            "memory_enhancement_context": getattr(
                input_data,
                "memory_enhancement_context",
                "",
            ),
            "parallel_chunks": bool(getattr(input_data, "parallel_chunks", False)),
            "parallel_dimensions": bool(getattr(input_data, "parallel_dimensions", False)),
            "audit_slices": getattr(input_data, "audit_slices", []),
            "seeded_dimension_results_hash": hashlib.sha256(
                json.dumps(
                    getattr(input_data, "seeded_dimension_results", {}),
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest(),
            "chapter_texts": chapters,
            "chapter_summaries": [
                {
                    "chapter_number": item.get("chapter_number"),
                    "summary": item.get("summary", ""),
                }
                for item in input_data.chapter_summaries
                if isinstance(item, dict)
            ],
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _load_audit_checkpoint(path: Path, signature: str) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        if str(payload.get("signature", "") or "") != signature:
            return {}

        schema_version = payload.get("schema_version")
        if schema_version is None:
            _log.warning("Legacy checkpoint without schema_version, assuming v1")
        elif schema_version < CURRENT_CHECKPOINT_SCHEMA_VERSION:
            _log.warning(
                "Checkpoint schema version %d < current %d, loading with compatibility mode",
                schema_version,
                CURRENT_CHECKPOINT_SCHEMA_VERSION,
            )
        elif schema_version > CURRENT_CHECKPOINT_SCHEMA_VERSION:
            _log.warning(
                "Checkpoint from newer version, may have incompatible fields",
            )

        return payload

    @staticmethod
    def _save_audit_checkpoint(path: Path, payload: dict[str, Any]) -> None:
        from novel_forge.persistence.filesystem import atomic_write_json

        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, payload)

    def _dimension_parallel_enabled(self, input_data: BookConsistencyInput) -> bool:
        enabled = bool(
            input_data.parallel_dimensions
            if input_data.parallel_dimensions is not None
            else getattr(self.settings, "long_book_audit_parallel_dimensions", True)
        )
        return enabled and bool(input_data.audit_slices)

    def _audit_max_parallel(self, input_data: BookConsistencyInput) -> int:
        raw = (
            input_data.parallel_dimension_limit
            if input_data.parallel_dimension_limit is not None
            else getattr(
                self.settings,
                "book_audit_max_parallel",
                getattr(self.settings, "long_book_audit_parallel_dimension_limit", 3),
            )
        )
        try:
            max_parallel = int(raw or 3)
        except (TypeError, ValueError):
            max_parallel = 3
        return max(1, min(max_parallel, len(GLOBAL_AUDIT_DIMENSION_SPECS), 8))

    @staticmethod
    def _dimension_slices(
        spec: GlobalAuditDimensionSpec,
        base_ctx: dict[str, Any],
    ) -> list[dict[str, Any]]:
        slices = [item for item in base_ctx.get("audit_slices", []) if isinstance(item, dict)]
        if not slices:
            return [_fallback_whole_book_slice(base_ctx)]
        focused = [
            item
            for item in slices
            if spec.name in {str(dim) for dim in item.get("focus_dimensions") or []}
            and str(item.get("slice_kind") or "") in set(spec.slice_kinds)
            and str(item.get("slice_kind") or "") != "whole_book"
        ]
        if focused:
            return focused
        whole_book = next(
            (item for item in slices if str(item.get("slice_kind") or "") == "whole_book"),
            None,
        )
        return [whole_book or _fallback_whole_book_slice(base_ctx)]

    @staticmethod
    def _chapter_texts_for_slice(
        chapter_texts_for_template: list[dict[str, Any]],
        audit_slice: dict[str, Any],
    ) -> list[dict[str, Any]]:
        chapters = _slice_chapters_for_context(audit_slice)
        if not chapters:
            return chapter_texts_for_template
        return [
            item
            for item in chapter_texts_for_template
            if _coerce_int(item.get("chapter_number"), 0) in chapters
        ]

    @staticmethod
    def _dimension_result_from_payload(
        *,
        spec: GlobalAuditDimensionSpec,
        audit_slice: dict[str, Any],
        parsed_items: list[dict[str, Any]],
        chapters_considered: list[int],
    ) -> dict[str, Any]:
        findings: list[dict[str, Any]] = []
        claims: list[dict[str, Any]] = []
        summaries: list[str] = []
        for parsed in parsed_items:
            if not isinstance(parsed, dict):
                continue
            summary = str(parsed.get("summary", "") or "").strip()
            if summary:
                summaries.append(summary)
            raw_claims = parsed.get("claims")
            if isinstance(raw_claims, list):
                claims.extend(item for item in raw_claims if isinstance(item, dict))
            for issue in parsed.get("issues", []) or []:
                if not isinstance(issue, dict):
                    continue
                item = dict(issue)
                item.setdefault("dimension", spec.name)
                item.setdefault("category", spec.name)
                item.setdefault("slice_id", audit_slice.get("slice_id") or "whole_book")
                findings.append(item)
                claims.append(
                    {
                        "dimension": spec.name,
                        "slice_id": audit_slice.get("slice_id") or "whole_book",
                        "issue_id": item.get("issue_id") or "",
                        "primary_chapter": item.get("primary_chapter") or 0,
                        "claim": item.get("description") or item.get("summary") or "",
                        "evidence": item.get("evidence") or "",
                    }
                )
        result = DimensionAuditResult(
            dimension=spec.name,
            slice_id=str(audit_slice.get("slice_id") or "whole_book"),
            dimension_summary="\n".join(summaries) or _derive_audit_summary(findings),
            claims=claims,
            findings=findings,
            handoff_notes=(
                f"{spec.name} 审计了 {len(chapters_considered)} 个章节/边界章节，"
                f"产出 {len(findings)} 条候选 finding。"
            ),
            coverage={
                "slice_kind": audit_slice.get("slice_kind") or "",
                "chapters_considered": chapters_considered,
                "finding_count": len(findings),
            },
        )
        return result.model_dump()

    @staticmethod
    def _merge_parsed_items(parsed_items: list[dict[str, Any]]) -> dict[str, Any]:
        if len(parsed_items) == 1:
            return parsed_items[0]
        issues: list[dict[str, Any]] = []
        repair_plan: list[dict[str, Any]] = []
        summaries: list[str] = []
        seen_summaries: set[str] = set()
        scores: list[float] = []
        dimension_results: list[dict[str, Any]] = []
        cross_dimension_conflicts: list[dict[str, Any]] = []
        seen_issue_keys: set[tuple[str, str, int, int, str]] = set()
        seen_fuzzy_issue_keys: set[tuple[str, int, str]] = set()
        for parsed in parsed_items:
            if not isinstance(parsed, dict):
                continue
            for item in parsed.get("dimension_results") or []:
                if isinstance(item, dict):
                    dimension_results.append(item)
            for item in parsed.get("cross_dimension_conflicts") or []:
                if isinstance(item, dict):
                    cross_dimension_conflicts.append(item)
            for issue in parsed.get("issues", []) or []:
                if not isinstance(issue, dict):
                    continue
                description = str(issue.get("description", "") or "").strip()
                desc_md5 = hashlib.md5(description.encode()).hexdigest()[:16]
                key = (
                    str(issue.get("issue_id", "") or "").strip(),
                    str(issue.get("category", "") or issue.get("dimension", "") or "").strip(),
                    _coerce_int(issue.get("primary_chapter"), default=0),
                    _coerce_int(issue.get("paragraph_index"), default=0),
                    desc_md5,
                )
                if key in seen_issue_keys:
                    continue
                fuzzy_key = (
                    str(issue.get("category", "") or issue.get("dimension", "") or "").strip(),
                    _coerce_int(issue.get("primary_chapter"), default=0),
                    description[:120],
                )
                if description and fuzzy_key in seen_fuzzy_issue_keys:
                    continue
                seen_issue_keys.add(key)
                if description:
                    seen_fuzzy_issue_keys.add(fuzzy_key)
                issues.append(issue)
            raw_plan = parsed.get("repair_plan", [])
            if isinstance(raw_plan, list):
                repair_plan.extend(item for item in raw_plan if isinstance(item, dict))
            summary = str(parsed.get("summary", "") or "").strip()
            if summary and summary not in seen_summaries:
                seen_summaries.add(summary)
                summaries.append(summary)
            score = coerce_float(parsed.get("consistency_score"), default=-1.0)
            if score >= 0:
                scores.append(score)

        return {
            "issues": issues,
            "repair_plan": repair_plan,
            "summary": (
                _derive_audit_summary(issues)
                if issues
                else ("\n".join(summaries) or _derive_audit_summary(issues))
            ),
            "consistency_score": min(scores) if scores else _derive_consistency_score(issues),
            "dimension_results": dimension_results,
            "cross_dimension_conflicts": cross_dimension_conflicts,
        }

    async def _call_cross_dimension_adjudication(
        self,
        *,
        base_ctx: dict[str, Any],
        dimension_results: list[dict[str, Any]],
        local_conflicts: list[dict[str, Any]],
        llm_sem: asyncio.Semaphore,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        if not local_conflicts:
            return {}
        compact_results = []
        for result in dimension_results:
            if not isinstance(result, dict):
                continue
            compact_results.append(
                {
                    "dimension": result.get("dimension"),
                    "slice_id": result.get("slice_id"),
                    "summary": result.get("dimension_summary"),
                    "claims": result.get("claims") or [],
                    "finding_count": len(result.get("findings") or []),
                }
            )
        ctx = {
            key: base_ctx.get(key)
            for key in (
                "chapter_summaries",
                "shared_evidence_anchor",
                "analysis_mode",
                "location_strictness",
                "max_issues_per_chunk",
            )
            if key in base_ctx
        }
        ctx.update(
            {
                "chapter_texts": [],
                "audit_slices": [],
                "semantic_evidence_context": [
                    {
                        "purpose": "cross_dimension_adjudication",
                        "local_conflicts": local_conflicts,
                        "dimension_results": compact_results,
                    }
                ],
                "prompt_hint": (
                    "你现在只做跨维裁决：判断不同审计维度给出的候选 finding 是否互相矛盾、"
                    "是否只是视角差异、哪些必须降级为 verify_first。不要新增正文修复建议；"
                    "如有真实冲突，只输出 cross_dimension_validation 类 issues。"
                ),
                "__task_type": TaskType.BOOK_CONSISTENCY,
                "__audit_dimension": "cross_dimension_adjudication",
                "__disable_dimension_parallel": True,
            }
        )
        async with llm_sem:
            parsed_items = await self._call_audit_context(
                ctx,
                max_tokens=max(1024, min(max_tokens, 2048)),
                temperature=min(temperature, 0.2),
            )
        normalized = [
            self._normalize_audit_payload(item, max_issues=8)
            for item in parsed_items
            if isinstance(item, dict)
        ]
        return self._merge_parsed_items(normalized)

    async def _call_dimension_aware_audit(
        self,
        input_data: BookConsistencyInput,
        base_ctx: dict[str, Any],
        chapter_texts_for_template: list[dict[str, Any]],
        *,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        context_builder = DimensionContextBuilder()
        adjudicator = CrossDimensionAdjudicator()
        llm_sem = asyncio.Semaphore(self._audit_max_parallel(input_data))
        completed: dict[str, list[dict[str, Any]]] = {}
        dimension_results: list[dict[str, Any]] = []
        seeded: dict[str, list[dict[str, Any]]] = {
            str(dimension): [dict(item) for item in results if isinstance(item, dict)]
            for dimension, results in input_data.seeded_dimension_results.items()
            if isinstance(results, list)
        }
        current_slice_ids_by_dimension = {
            spec.name: {
                str(item.get("slice_id") or "whole_book")
                for item in self._dimension_slices(spec, base_ctx)
            }
            for spec in GLOBAL_AUDIT_DIMENSION_SPECS
        }
        for spec in GLOBAL_AUDIT_DIMENSION_SPECS:
            cached_by_slice = {
                str(item.get("slice_id") or "whole_book"): item
                for item in seeded.get(spec.name, [])
            }
            required_slice_ids = current_slice_ids_by_dimension[spec.name]
            if required_slice_ids and required_slice_ids.issubset(cached_by_slice):
                cached_results = [
                    cached_by_slice[slice_id] for slice_id in sorted(required_slice_ids)
                ]
                completed[spec.name] = cached_results
                dimension_results.extend(cached_results)
        checkpoint_path = input_data.audit_checkpoint_path
        signature = self._checkpoint_signature(input_data, chapter_texts_for_template)
        checkpoint_payload: dict[str, Any] = {}
        preserved_checkpoint_fields: dict[str, Any] = {}
        if checkpoint_path is not None and checkpoint_path.exists():
            try:
                raw_checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw_checkpoint = {}
            if isinstance(raw_checkpoint, dict):
                preserved_checkpoint_fields = {
                    key: raw_checkpoint[key]
                    for key in (
                        "resume_signature",
                        "two_phase_summary_result",
                        "two_phase_summary_signature",
                        "two_phase_summary_meta",
                        "final_result",
                        "final_result_signature",
                        "final_result_saved_at",
                    )
                    if key in raw_checkpoint
                }
        if checkpoint_path is not None and input_data.resume_audit_checkpoint:
            checkpoint_payload = self._load_audit_checkpoint(checkpoint_path, signature)
            completed_dimensions = checkpoint_payload.get("completed_dimensions", [])
            if isinstance(completed_dimensions, list):
                for item in completed_dimensions:
                    if not isinstance(item, dict):
                        continue
                    dimension = str(item.get("dimension") or "")
                    results = [
                        result for result in item.get("results", []) if isinstance(result, dict)
                    ]
                    if dimension and results:
                        completed[dimension] = results
                        dimension_results.extend(results)

        def _save_dimension_checkpoint(status: str, **extra: Any) -> None:
            if checkpoint_path is None:
                return
            payload = {
                "schema_version": CURRENT_CHECKPOINT_SCHEMA_VERSION,
                "status": status,
                "signature": signature,
                "analysis_mode": input_data.analysis_mode,
                "max_chapters_per_batch": int(input_data.max_chapters_per_batch or 0),
                "chunks_total": len(GLOBAL_AUDIT_DIMENSION_SPECS),
                "completed_chunks": [],
                "completed_dimensions": [
                    {"dimension": dimension, "results": results}
                    for dimension, results in sorted(completed.items())
                ],
                "chapter_summaries": [
                    {
                        "chapter_number": item.get("chapter_number"),
                        "summary": item.get("summary", ""),
                    }
                    for item in input_data.chapter_summaries
                    if isinstance(item, dict)
                ],
            }
            payload.update(preserved_checkpoint_fields)
            payload.update(extra)
            self._save_audit_checkpoint(checkpoint_path, payload)

        _save_dimension_checkpoint("running")

        async def _call_one_context(ctx: dict[str, Any]) -> list[dict[str, Any]]:
            async with llm_sem:
                items = await self._call_audit_context(
                    ctx,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            return [
                self._normalize_audit_payload(item, max_issues=input_data.max_issues_per_chunk)
                for item in items
                if isinstance(item, dict)
            ]

        async def _run_dimension(spec: GlobalAuditDimensionSpec) -> list[dict[str, Any]]:
            ledger: list[dict[str, Any]] = [
                {
                    "dimension": dep,
                    "results": completed.get(dep, [])[-4:],
                }
                for dep in spec.dependencies
                if dep in completed
            ]
            current_slice_ids = current_slice_ids_by_dimension[spec.name]
            cached_by_slice = {
                str(item.get("slice_id") or "whole_book"): item
                for item in seeded.get(spec.name, [])
                if str(item.get("slice_id") or "whole_book") in current_slice_ids
            }
            results: list[dict[str, Any]] = [
                cached_by_slice[slice_id] for slice_id in sorted(cached_by_slice)
            ]
            for audit_slice in self._dimension_slices(spec, base_ctx):
                slice_id = str(audit_slice.get("slice_id") or "whole_book")
                if slice_id in cached_by_slice:
                    continue
                slice_texts = self._chapter_texts_for_slice(
                    chapter_texts_for_template,
                    audit_slice,
                )
                parsed_for_slice: list[dict[str, Any]] = []
                if input_data.analysis_mode == "full_text" and slice_texts:
                    sizing_ctx = context_builder.build(
                        spec=spec,
                        base_ctx=base_ctx,
                        audit_slice=audit_slice,
                        chapter_texts=[],
                        dimension_ledger_context=ledger,
                    )
                    chunks = self._chapter_text_chunks(
                        sizing_ctx,
                        slice_texts,
                        input_data.max_chapters_per_batch,
                    )
                    for idx, chunk in enumerate(chunks, start=1):
                        notice = (
                            f"维度 {spec.name} 的切片 {audit_slice.get('slice_id') or 'whole_book'} "
                            f"被拆分为 {len(chunks)} 个上下文块；当前块 {idx}/{len(chunks)}。"
                        )
                        ctx = context_builder.build(
                            spec=spec,
                            base_ctx=base_ctx,
                            audit_slice=audit_slice,
                            chapter_texts=chunk,
                            dimension_ledger_context=ledger,
                            audit_chunk_notice=notice if len(chunks) > 1 else "",
                        )
                        parsed_for_slice.extend(await _call_one_context(ctx))
                else:
                    ctx = context_builder.build(
                        spec=spec,
                        base_ctx=base_ctx,
                        audit_slice=audit_slice,
                        chapter_texts=[],
                        dimension_ledger_context=ledger,
                    )
                    for summary_ctx in self._summary_context_chunks(
                        ctx,
                        input_data.max_chapters_per_batch,
                    ):
                        parsed_for_slice.extend(await _call_one_context(summary_ctx))
                chapters_considered = sorted(_slice_chapters_for_context(audit_slice))
                result = self._dimension_result_from_payload(
                    spec=spec,
                    audit_slice=audit_slice,
                    parsed_items=parsed_for_slice,
                    chapters_considered=chapters_considered,
                )
                results.append(result)
                ledger.append(
                    {
                        "dimension": spec.name,
                        "slice_id": result["slice_id"],
                        "summary": result["dimension_summary"],
                        "claim_count": len(result.get("claims") or []),
                        "finding_count": len(result.get("findings") or []),
                    }
                )
            return results

        pending = {
            spec.name: spec for spec in GLOBAL_AUDIT_DIMENSION_SPECS if spec.name not in completed
        }
        while pending:
            runnable = [
                spec
                for spec in pending.values()
                if all(dep in completed for dep in spec.dependencies)
            ]
            if not runnable:
                unresolved = ", ".join(sorted(pending))
                raise RuntimeError(f"Global audit dimension dependency cycle: {unresolved}")
            try:
                batch_results = await asyncio.gather(*[_run_dimension(spec) for spec in runnable])
            except Exception:
                _save_dimension_checkpoint(
                    "failed",
                    failed_dimensions=[spec.name for spec in runnable],
                )
                raise
            for spec, results in zip(runnable, batch_results, strict=True):
                completed[spec.name] = results
                dimension_results.extend(results)
                pending.pop(spec.name, None)
            _save_dimension_checkpoint("running")

        adjudicated_findings, local_conflicts = adjudicator.adjudicate(dimension_results)
        llm_adjudication = await self._call_cross_dimension_adjudication(
            base_ctx=base_ctx,
            dimension_results=dimension_results,
            local_conflicts=local_conflicts,
            llm_sem=llm_sem,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if llm_adjudication:
            for conflict in local_conflicts:
                conflict["status"] = "llm_reviewed"
                conflict["llm_summary"] = str(llm_adjudication.get("summary") or "")[:500]
                conflict["llm_issue_count"] = len(llm_adjudication.get("issues") or [])
        parsed = self._merge_parsed_items(
            [
                {
                    "issues": result.get("findings") or [],
                    "repair_plan": [],
                    "summary": result.get("dimension_summary") or "",
                    "consistency_score": _derive_consistency_score(result.get("findings") or []),
                    "dimension_results": [result],
                }
                for result in dimension_results
            ]
        )
        if adjudicated_findings:
            parsed["issues"] = adjudicated_findings
        parsed["dimension_results"] = dimension_results
        parsed["cross_dimension_conflicts"] = local_conflicts
        if llm_adjudication:
            parsed["cross_dimension_adjudication"] = llm_adjudication
        parsed["_llm_had_summary"] = bool(parsed.get("summary"))
        parsed["_llm_had_score"] = True
        _save_dimension_checkpoint("completed")
        return parsed

    async def _call_audit(
        self,
        input_data: BookConsistencyInput,
        base_ctx: dict[str, Any],
        chapter_texts_for_template: list[dict[str, Any]],
    ) -> dict[str, Any]:
        requested_cap = max(1024, int(input_data.max_tokens or 4096))
        max_tokens = self._dynamic_max_tokens(
            TaskType.BOOK_CONSISTENCY,
            max(2500, len(chapter_texts_for_template) * 500),
            prompt_overhead=4000,
            min_tokens=1024,
            max_cap=requested_cap,
        )
        temperature = _clamp_float(float(input_data.temperature), 0.0, 2.0)

        if self._dimension_parallel_enabled(input_data):
            return await self._call_dimension_aware_audit(
                input_data,
                base_ctx,
                chapter_texts_for_template,
                max_tokens=max_tokens,
                temperature=temperature,
            )

        if input_data.analysis_mode != "full_text" or not chapter_texts_for_template:
            parsed_items = []
            for summary_ctx in self._summary_context_chunks(
                base_ctx,
                input_data.max_chapters_per_batch,
            ):
                parsed_items.extend(
                    await self._call_audit_context(
                        summary_ctx,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                )
        else:
            chunks = self._chapter_text_chunks(
                base_ctx,
                chapter_texts_for_template,
                input_data.max_chapters_per_batch,
            )
            parsed_items = []
            checkpoint_path = input_data.audit_checkpoint_path
            signature = self._checkpoint_signature(input_data, chapter_texts_for_template)
            checkpoint_payload: dict[str, Any] = {}
            preserved_checkpoint_fields: dict[str, Any] = {}
            if checkpoint_path is not None and checkpoint_path.exists():
                try:
                    raw_checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    raw_checkpoint = {}
                if isinstance(raw_checkpoint, dict):
                    preserved_checkpoint_fields = {
                        key: raw_checkpoint[key]
                        for key in (
                            "resume_signature",
                            "two_phase_summary_result",
                            "two_phase_summary_signature",
                            "two_phase_summary_meta",
                            "final_result",
                            "final_result_signature",
                            "final_result_saved_at",
                        )
                        if key in raw_checkpoint
                    }
            completed_by_index: dict[int, list[dict[str, Any]]] = {}
            if checkpoint_path is not None and input_data.resume_audit_checkpoint:
                checkpoint_payload = self._load_audit_checkpoint(checkpoint_path, signature)
                for item in checkpoint_payload.get("completed_chunks", []) or []:
                    if not isinstance(item, dict):
                        continue
                    index = _coerce_int(item.get("index"), default=0)
                    parsed_list = item.get("parsed_items", [])
                    if index > 0 and isinstance(parsed_list, list):
                        completed_by_index[index] = [
                            parsed for parsed in parsed_list if isinstance(parsed, dict)
                        ]

            if checkpoint_path is not None:
                checkpoint_payload = {
                    "schema_version": 1,
                    "status": "running",
                    "signature": signature,
                    "analysis_mode": input_data.analysis_mode,
                    "max_chapters_per_batch": int(input_data.max_chapters_per_batch or 0),
                    "chunks_total": len(chunks),
                    "completed_chunks": [
                        {"index": done_idx, "parsed_items": items}
                        for done_idx, items in sorted(completed_by_index.items())
                    ],
                    "chapter_summaries": [
                        {
                            "chapter_number": item.get("chapter_number"),
                            "summary": item.get("summary", ""),
                        }
                        for item in input_data.chapter_summaries
                        if isinstance(item, dict)
                    ],
                }
                checkpoint_payload.update(preserved_checkpoint_fields)
                self._save_audit_checkpoint(checkpoint_path, checkpoint_payload)

            if input_data.on_chunk_progress is not None:
                input_data.on_chunk_progress(0, len(chunks))

            pending = []
            _use_parallel = bool(
                input_data.parallel_chunks
                if input_data.parallel_chunks is not None
                else getattr(self.settings, "long_book_audit_parallel_chunks", False)
            )
            for idx, chunk in enumerate(chunks, start=1):
                if idx in completed_by_index:
                    parsed_items.extend(completed_by_index[idx])
                    if input_data.on_chunk_progress is not None:
                        input_data.on_chunk_progress(idx, len(chunks))
                    continue
                chapter_numbers = [
                    int(item.get("chapter_number", 0) or 0)
                    for item in chunk
                    if int(item.get("chapter_number", 0) or 0) > 0
                ]
                part_info = ""
                for item in chunk:
                    total_parts = int(item.get("total_parts", 0) or 0)
                    part_number = int(item.get("part_number", 0) or 0)
                    if total_parts > 1 and part_number > 0:
                        ch_num = item.get("chapter_number", 0)
                        part_info = (
                            f"第 {ch_num} 章被拆分为 {total_parts} 个子块；"
                            f"当前为第 {part_number}/{total_parts} 部分。"
                        )
                        break

                notice = (
                    f"本次全文审计被自动拆分为 {len(chunks)} 个上下文块；"
                    f"当前块 {idx}/{len(chunks)} 只包含第 {chapter_numbers} 章全文。"
                    "若块内仍超限会进一步递归拆分，相邻块会共享边界章节以检测跨块问题。"
                    "章节摘要仍覆盖全部已完成章节。"
                )
                if part_info:
                    notice = f"本次全文审计被自动拆分为 {len(chunks)} 个上下文块；当前块 {idx}/{len(chunks)}。{part_info}章节摘要仍覆盖全部已完成章节。"
                pending.append((idx, chunk, chapter_numbers, part_info, notice))

            if pending:
                if _use_parallel:
                    import asyncio as _asyncio

                    _max_parallel = min(5, len(pending))
                    _sem = _asyncio.Semaphore(_max_parallel)

                    async def _run_one(
                        chunk_idx: int,
                        chunk_data: list[dict[str, Any]],
                        ch_nums: list[int],
                        chunk_notice: str,
                    ) -> tuple[int, list[dict[str, Any]], list[int], Exception | None]:
                        async with _sem:
                            chunk_ctx = dict(
                                base_ctx,
                                chapter_texts=chunk_data,
                                audit_chunk_notice=chunk_notice if len(chunks) > 1 else "",
                            )
                            try:
                                items = await self._call_audit_context(
                                    chunk_ctx,
                                    max_tokens=max_tokens,
                                    temperature=temperature,
                                )
                            except Exception as exc:
                                return (chunk_idx, [], ch_nums, exc)
                            normalized = [
                                self._normalize_audit_payload(
                                    item, max_issues=input_data.max_issues_per_chunk
                                )
                                for item in items
                            ]
                            return (chunk_idx, normalized, ch_nums, None)

                    tasks = [
                        _run_one(idx, chunk_data, ch_nums, notice)
                        for idx, chunk_data, ch_nums, _, notice in pending
                    ]
                    results = await _asyncio.gather(*tasks)

                    first_failure: tuple[int, list[int], Exception] | None = None
                    for idx, chunk_items, ch_nums, exc in sorted(
                        results,
                        key=lambda item: item[0],
                    ):
                        if exc is not None:
                            if first_failure is None:
                                first_failure = (idx, ch_nums, exc)
                            continue
                        parsed_items.extend(chunk_items)
                        if checkpoint_path is not None:
                            completed_by_index[idx] = chunk_items
                            checkpoint_payload["status"] = "running"
                            checkpoint_payload["completed_chunks"] = [
                                {"index": done_idx, "parsed_items": items}
                                for done_idx, items in sorted(completed_by_index.items())
                            ]
                            self._save_audit_checkpoint(checkpoint_path, checkpoint_payload)
                        if input_data.on_chunk_progress is not None:
                            input_data.on_chunk_progress(idx, len(chunks))
                    if first_failure is not None:
                        failed_idx, failed_chapters, exc = first_failure
                        if checkpoint_path is not None:
                            checkpoint_payload["status"] = "failed"
                            checkpoint_payload["failed_chunk"] = failed_idx
                            checkpoint_payload["failed_chapters"] = failed_chapters
                            checkpoint_payload["completed_chunks"] = [
                                {"index": done_idx, "parsed_items": items}
                                for done_idx, items in sorted(completed_by_index.items())
                            ]
                            self._save_audit_checkpoint(checkpoint_path, checkpoint_payload)
                        raise exc
                else:
                    for idx, chunk_data, ch_nums, _, notice in pending:
                        current_task = asyncio.current_task()
                        if current_task is not None and current_task.cancelling():
                            raise asyncio.CancelledError
                        chunk_ctx = dict(
                            base_ctx,
                            chapter_texts=chunk_data,
                            audit_chunk_notice=notice if len(chunks) > 1 else "",
                        )
                        try:
                            items = await self._call_audit_context(
                                chunk_ctx,
                                max_tokens=max_tokens,
                                temperature=temperature,
                            )
                        except Exception:
                            if checkpoint_path is not None:
                                checkpoint_payload["status"] = "failed"
                                checkpoint_payload["failed_chunk"] = idx
                                checkpoint_payload["failed_chapters"] = ch_nums
                                self._save_audit_checkpoint(checkpoint_path, checkpoint_payload)
                            raise
                        chunk_items = [
                            self._normalize_audit_payload(
                                item, max_issues=input_data.max_issues_per_chunk
                            )
                            for item in items
                        ]
                        parsed_items.extend(chunk_items)
                        if checkpoint_path is not None:
                            completed_by_index[idx] = chunk_items
                            checkpoint_payload["status"] = "running"
                            checkpoint_payload["completed_chunks"] = [
                                {"index": done_idx, "parsed_items": items}
                                for done_idx, items in sorted(completed_by_index.items())
                            ]
                            self._save_audit_checkpoint(checkpoint_path, checkpoint_payload)
                        if input_data.on_chunk_progress is not None:
                            input_data.on_chunk_progress(idx, len(chunks))

            if checkpoint_path is not None:
                checkpoint_payload["status"] = "completed"
                checkpoint_payload["completed_chunks"] = [
                    {"index": done_idx, "parsed_items": items}
                    for done_idx, items in sorted(completed_by_index.items())
                ]
                self._save_audit_checkpoint(checkpoint_path, checkpoint_payload)

        parsed_items = [
            self._normalize_audit_payload(
                item,
                max_issues=input_data.max_issues_per_chunk,
            )
            for item in parsed_items
        ]

        return self._merge_parsed_items(parsed_items)

    async def _execute(self, input_data: BookConsistencyInput) -> BookConsistencyResult:
        canon = input_data.canon_state_snapshot
        characters = canon.get("characters", {})
        relationships = canon.get("relationships", [])

        bible_names: list[str] = []
        bible_chars = input_data.character_bible.get("characters", [])
        if isinstance(bible_chars, list):
            bible_names = [
                c.get("name", "") for c in bible_chars if isinstance(c, dict) and c.get("name")
            ]
        elif isinstance(bible_chars, dict):
            bible_names = [str(k) for k in bible_chars.keys()]

        outline_summary = ""
        if input_data.outline:
            outline_summary = str(input_data.outline.get("premise", "") or "")

        # Prefer pre-processed compact profiles; fall back to names-only.
        character_profiles_compact = input_data.character_profiles_compact or [
            {"name": n} for n in bible_names if n
        ]

        chapter_texts = [item for item in input_data.chapter_texts if isinstance(item, dict)]
        # Strip 'paragraphs' from the template context to avoid sending raw
        # paragraph lists to the LLM (they are only used for local matching).
        chapter_texts_for_template = [
            {k: v for k, v in ct.items() if k != "paragraphs"} for ct in chapter_texts
        ]

        # ── Memory enhancement: prepare cross-chapter context ──────────
        if getattr(self.settings, "long_book_audit_memory_enhancement_enabled", False):
            memory_ctx = None
            memory_context_factory = getattr(input_data, "memory_context_factory", None)
            if callable(memory_context_factory):
                try:
                    memory_ctx = memory_context_factory()
                except Exception as exc:
                    _log.warning("book_consistency: memory_context_factory failed: %s", exc)
            if memory_ctx is not None:
                try:
                    from novel_forge.memory.audit_coordinator import AuditCoordinator

                    coordinator = AuditCoordinator(memory_ctx)
                    audit_ctx = await coordinator.prepare_audit_context(
                        chapter_number=input_data.chapter_number,
                        chapter_text="",
                    )
                    input_data = dataclasses.replace(
                        input_data,
                        memory_enhancement_context=audit_ctx.get_summary_for_prompt(),
                    )
                except Exception as exc:
                    _log.warning("book_consistency: AuditCoordinator failed: %s", exc)

        base_ctx = self._build_prompt_context(
            input_data,
            characters=characters,
            relationships=relationships,
            bible_names=bible_names,
            character_profiles_compact=character_profiles_compact,
            outline_summary=outline_summary,
            chapter_texts_for_template=[],
        )
        empty_result_reason: str | None = None
        try:
            parsed = await self._call_audit(input_data, base_ctx, chapter_texts_for_template)
        except Exception as exc:
            exc_str = str(exc).lower()
            if any(kw in exc_str for kw in ("cannot", "unable", "refuse", "拒绝", "无法", "不能")):
                empty_result_reason = "llm_refusal"
            else:
                empty_result_reason = "parse_failure"
            raise

        # Build paragraph-count lookup so we can clamp LLM-reported indices.
        para_count_by_chapter: dict[int, int] = {
            int(ct["chapter_number"]): int(ct.get("paragraph_count", 0) or 0)
            for ct in chapter_texts
            if isinstance(ct, dict) and int(ct.get("chapter_number", 0) or 0) > 0
        }
        # Build paragraph-text lookup for local evidence matching.
        paragraphs_by_chapter: dict[int, list[str]] = {
            int(ct["chapter_number"]): ct.get("paragraphs") or []
            for ct in chapter_texts
            if isinstance(ct, dict)
            and int(ct.get("chapter_number", 0) or 0) > 0
            and isinstance(ct.get("paragraphs"), list)
        }

        issues: list[ConsistencyIssue] = []
        for item in parsed.get("issues", []):
            if not isinstance(item, dict):
                continue
            dimension = str(item.get("dimension", "") or "").strip().lower()
            category = str(item.get("category") or dimension or "unknown").strip()
            severity = _SEVERITY_MAP.get(
                str(item.get("severity", "info") or "info").strip().lower(),
                "info",
            )
            raw_chapters = item.get("chapters_involved", [])
            chapters_involved = sorted(
                {
                    int(ch)
                    for ch in raw_chapters
                    if isinstance(ch, (int, str)) and str(ch).strip().isdigit()
                }
            )
            if not chapters_involved:
                primary = _coerce_int(item.get("primary_chapter"), default=0)
                if primary > 0:
                    chapters_involved = [primary]

            location = str(item.get("location", "") or "").strip()
            paragraph_index = _coerce_int(item.get("paragraph_index"), default=0)
            if paragraph_index <= 0 and location:
                match = _PARA_RE.search(location)
                if match:
                    paragraph_index = _coerce_int(match.group(1), default=0)

            paragraph_span_raw = item.get("paragraph_span", [])
            paragraph_span: list[int] = []
            if isinstance(paragraph_span_raw, list) and len(paragraph_span_raw) >= 2:
                left = _coerce_int(paragraph_span_raw[0], default=0)
                right = _coerce_int(paragraph_span_raw[1], default=0)
                if left > 0 and right > 0:
                    paragraph_span = [min(left, right), max(left, right)]
            if not paragraph_span and paragraph_index > 0:
                paragraph_span = [paragraph_index, paragraph_index]

            fix_mode = str(item.get("fix_mode", "") or "").strip().lower()
            if fix_mode not in _ALLOWED_FIX_MODES:
                if category in {"timeline", "character_state"}:
                    fix_mode = "repair_causal"
                elif category in {"naming", "worldbuilding", "narrative_drift"}:
                    fix_mode = "repair_continuity"
                else:
                    fix_mode = "manual_patch"
            fix_action = str(item.get("fix_action", "rewrite") or "rewrite").strip().lower()
            if fix_action not in _ALLOWED_FIX_ACTIONS:
                fix_action = "rewrite"

            primary_chapter = _coerce_int(item.get("primary_chapter"), default=0)
            if primary_chapter <= 0 and chapters_involved:
                primary_chapter = chapters_involved[0]

            # ── Local paragraph resolution ──────────────────────────────
            # Trust local text-matching over the LLM's claimed paragraph_index.
            # The LLM hint is used only as a tie-breaker when multiple matches.
            evidence = str(item.get("evidence", "") or "").strip()
            ch_paras = paragraphs_by_chapter.get(primary_chapter, []) if primary_chapter > 0 else []
            if ch_paras:
                from novel_forge.core.utils.patch_utils import resolve_paragraph_locally

                local_targets, _anchor, _conf = resolve_paragraph_locally(
                    ch_paras,
                    evidence=evidence,
                    location=location,
                    llm_hint=paragraph_index,
                )
                if _anchor != "fallback" and local_targets:
                    # resolve_paragraph_locally returns 0-based; book audit uses 1-based
                    paragraph_index = local_targets[0] + 1
                    if len(local_targets) >= 2:
                        paragraph_span = [local_targets[0] + 1, local_targets[-1] + 1]
                    else:
                        paragraph_span = [paragraph_index, paragraph_index]

            # Clamp paragraph_index / paragraph_span against the actual paragraph
            # count for this chapter so hallucinated out-of-range indices don't
            # propagate to the repair stage.
            ch_para_count = (
                para_count_by_chapter.get(primary_chapter, 0) if primary_chapter > 0 else 0
            )
            if ch_para_count > 0:
                # Clamp span first so a valid span survives even when index is OOB
                if paragraph_span:
                    paragraph_span = [
                        max(1, min(paragraph_span[0], ch_para_count)),
                        max(1, min(paragraph_span[1], ch_para_count)),
                    ]
                if paragraph_index > ch_para_count:
                    # Fall back to span start if available, otherwise reset
                    paragraph_index = paragraph_span[0] if paragraph_span else 0

            issues.append(
                ConsistencyIssue(
                    issue_id=str(item.get("issue_id", "") or "").strip(),
                    dimension=dimension or _global_dimension_for_issue(item),
                    category=category,
                    severity=severity,
                    chapters_involved=chapters_involved,
                    description=str(item.get("description", "") or "").strip(),
                    suggestion=str(item.get("suggestion", "") or "").strip(),
                    issue_type=str(item.get("issue_type", "") or "").strip(),
                    primary_chapter=primary_chapter,
                    location=location,
                    paragraph_index=max(paragraph_index, 0),
                    paragraph_span=paragraph_span,
                    evidence=str(item.get("evidence", "") or "").strip(),
                    fix_mode=fix_mode,
                    fix_action=fix_action,
                    confidence=_clamp_float(
                        coerce_float(item.get("confidence"), default=0.0), 0.0, 1.0
                    ),
                    linked_issue_refs=[
                        {
                            "chapter_number": _coerce_int(ref.get("chapter_number"), default=0),
                            "lane": str(ref.get("lane", "") or "").strip().lower(),
                            "index": _coerce_int(ref.get("index"), default=-1),
                        }
                        for ref in (item.get("linked_issue_refs") or [])
                        if isinstance(ref, dict)
                        and _coerce_int(ref.get("chapter_number"), default=0) > 0
                        and str(ref.get("lane", "") or "").strip().lower()
                        in {"continuity", "causal"}
                        and _coerce_int(ref.get("index"), default=-1) >= 0
                    ],
                    evidence_pairs=[
                        pair
                        for pair in (item.get("evidence_pairs") or [])
                        if isinstance(pair, dict)
                    ],
                    verification_questions=[
                        str(question or "").strip()
                        for question in (item.get("verification_questions") or [])
                        if str(question or "").strip()
                    ],
                    handoff_notes=str(item.get("handoff_notes", "") or "").strip(),
                )
            )

        # Three-tier confidence classification for all issues.
        # confirmed (>0.7): high confidence, always retained
        # suspected (0.35-0.7): moderate confidence, retained with lower priority
        # unlikely (≤0.35): low confidence, dropped as hallucination filter
        classified: list[ConsistencyIssue] = []
        for iss in issues:
            if iss.confidence <= 0.0:
                iss = _dataclass_replace(iss, verification_status="confirmed")
            elif iss.confidence > 0.7:
                iss = _dataclass_replace(iss, verification_status="confirmed")
            elif iss.confidence > 0.35:
                iss = _dataclass_replace(iss, verification_status="suspected")
            else:
                iss = _dataclass_replace(iss, verification_status="unlikely")
            classified.append(iss)

        issues = [iss for iss in classified if iss.verification_status != "unlikely"]

        # Determine empty_result_reason when issues list is empty and no exception occurred
        if not issues and empty_result_reason is None:
            # Use preserved LLM original value flags to determine reason
            llm_had_summary = parsed.get("_llm_had_summary", False)
            llm_had_score = parsed.get("_llm_had_score", False)
            if llm_had_summary or llm_had_score:
                empty_result_reason = "genuinely_no_issues"
            else:
                empty_result_reason = "parse_failure"

        chapters_audited = [
            int(item.get("chapter_number", 0) or 0)
            for item in input_data.chapter_summaries
            if isinstance(item, dict) and int(item.get("chapter_number", 0) or 0) > 0
        ]
        truncated_chapters = [
            int(item.get("chapter_number", 0) or 0)
            for item in chapter_texts
            if bool(item.get("truncated", False)) and int(item.get("chapter_number", 0) or 0) > 0
        ]
        repair_plan_raw = parsed.get("repair_plan", [])
        repair_plan = (
            [item for item in repair_plan_raw if isinstance(item, dict)]
            if isinstance(repair_plan_raw, list)
            else []
        )

        audit_depth = "full" if input_data.analysis_mode == "full_text" else "quick"
        issue_dicts = [iss.model_dump() for iss in issues]
        quality_metrics = _compute_quality_metrics_from_issues(issue_dicts, audit_depth)
        llm_had_summary = bool(parsed.get("_llm_had_summary", False))
        llm_had_score = bool(parsed.get("_llm_had_score", False))
        audit_slices = [dict(item) for item in input_data.audit_slices if isinstance(item, dict)]
        dimension_results = [
            dict(item) for item in (parsed.get("dimension_results") or []) if isinstance(item, dict)
        ]
        cross_dimension_conflicts = [
            dict(item)
            for item in (parsed.get("cross_dimension_conflicts") or [])
            if isinstance(item, dict)
        ]
        global_findings = _build_global_findings(
            issue_dicts,
            audit_slices=audit_slices,
            paragraphs_by_chapter=paragraphs_by_chapter,
        )
        conflict_issue_ids = {
            str(issue_id)
            for conflict in cross_dimension_conflicts
            for issue_id in (conflict.get("issue_ids") or [])
            if str(issue_id)
        }
        if conflict_issue_ids:
            for finding in global_findings:
                source_issue_id = str(finding.get("source_issue_id") or "")
                finding_id = str(finding.get("finding_id") or "")
                if (
                    source_issue_id not in conflict_issue_ids
                    and finding_id not in conflict_issue_ids
                ):
                    continue
                finding["repair_readiness"] = {
                    "status": "verify_first",
                    "risk": "medium",
                    "reasons": ["cross_dimension_judgement_required"],
                    "auto_repair_eligible": False,
                }
        repair_status_counts = {"ready": 0, "verify_first": 0, "manual_review": 0, "blocked": 0}
        for finding in global_findings:
            readiness = finding.get("repair_readiness")
            status = (
                str(readiness.get("status") or "manual_review")
                if isinstance(readiness, dict)
                else "manual_review"
            )
            if status not in repair_status_counts:
                status = "manual_review"
            repair_status_counts[status] += 1
        repair_queue_summary = {
            "architecture": "global_repair_queue_v1",
            "status": "planned",
            "finding_count": len(global_findings),
            "ready_count": repair_status_counts["ready"],
            "verify_first_count": repair_status_counts["verify_first"],
            "manual_review_count": repair_status_counts["manual_review"],
            "blocked_count": repair_status_counts["blocked"],
        }
        coverage_metrics = _build_coverage_metrics(
            audit_slices=audit_slices,
            chapters_audited=chapters_audited,
            issue_dicts=issue_dicts,
            global_findings=global_findings,
            paragraph_indexed_chapters=len(paragraphs_by_chapter),
        )

        return BookConsistencyResult(
            issues=issues,
            summary=str(parsed.get("summary", "") or "").strip(),
            consistency_score=_clamp_float(
                coerce_float(parsed.get("consistency_score"), default=0.0), 0.0, 10.0
            ),
            analysis_mode=str(input_data.analysis_mode or "summary"),
            chapters_audited=chapters_audited,
            truncated_chapters=truncated_chapters,
            repair_plan=repair_plan,
            empty_result_reason=empty_result_reason,
            quality_metrics=quality_metrics,
            field_sources={
                "summary": "llm" if llm_had_summary else "derived",
                "consistency_score": "llm" if llm_had_score else "derived",
            },
            slices=audit_slices,
            dimension_results=dimension_results,
            cross_dimension_conflicts=cross_dimension_conflicts,
            global_findings=global_findings,
            repair_queue_summary=repair_queue_summary,
            revision_queue=[],
            coverage_metrics=coverage_metrics,
            input_manifest={},
            incremental_reuse={},
        )
