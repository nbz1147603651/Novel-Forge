"""Shared payload builders for workspace entrypoints and desktop jobs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from novel_forge.core.schemas.story_state import ChapterExitState
from novel_forge.core.utils.coerce import coerce_float
from novel_forge.core.utils.text_validation import display_word_count
from novel_forge.workspace.contracts import (
    ChapterSessionResult,
    PrepareChapterResponse,
)

DEFAULT_PREVIEW_CHARS = 500


def clip_preview(
    text: str,
    *,
    limit: int = DEFAULT_PREVIEW_CHARS,
    ellipsis: bool = False,
) -> str:
    """Clip preview text using one shared set of rules."""
    value = str(text or "")
    if limit <= 0 or len(value) <= limit:
        return value
    clipped = value[:limit]
    return clipped + ("…" if ellipsis else "")


def summarize_chapter_exit(exit_state: ChapterExitState | None) -> str:
    """Build the compact chapter-exit summary used by API and desktop payloads."""
    if exit_state is None:
        return ""
    items = getattr(exit_state, "must_carry_forward", []) or []
    return "；".join(str(item) for item in items[:3] if str(item).strip())


def build_run_short_result_payload(
    project_id: str,
    result: Any,
    *,
    preview_chars: int = DEFAULT_PREVIEW_CHARS,
    preview_ellipsis: bool = False,
) -> dict[str, Any]:
    """Build the shared short-story payload surfaced by API/Desktop entrypoints."""
    eval_report = getattr(result, "eval_report", None)
    final_text = str(getattr(result, "final_text", "") or "")
    creative = getattr(result, "creative_summary", None)

    payload: dict[str, Any] = {
        "project_id": project_id,
        "word_count": display_word_count(final_text),
        "overall_score": getattr(eval_report, "overall_score", 0.0) if eval_report else 0.0,
        "passed": bool(getattr(eval_report, "passed", False)) if eval_report else False,
        "preview": clip_preview(
            final_text,
            limit=preview_chars,
            ellipsis=preview_ellipsis,
        ),
        "warnings": [
            str(item) for item in (getattr(result, "warnings", []) or []) if str(item).strip()
        ],
    }

    # Enrich with creative analysis metadata when available
    if creative is not None:
        narr = getattr(creative, "narrative_analysis", None)
        thematic = getattr(creative, "thematic_analysis", None)
        chars = getattr(creative, "characters", []) or []
        payload["creative_summary"] = {
            "character_count": len(chars),
            "character_names": [getattr(c, "name", "") for c in chars[:8]],
            "structure_type": getattr(narr, "structure_type", "") if narr else "",
            "tension_curve": getattr(narr, "tension_curve", "") if narr else "",
            "core_theme": getattr(thematic, "core_theme", "") if thematic else "",
            "highlights_count": len(getattr(creative, "creative_highlights", [])),
            "beat_fulfillment_rate": _beat_fulfillment_rate(creative),
        }

    return payload


def _beat_fulfillment_rate(creative: Any) -> float:
    """Calculate fraction of beats fulfilled (0.0–1.0)."""
    items = getattr(creative, "beat_fulfillment", []) or []
    if not items:
        return 0.0
    fulfilled = sum(1 for bf in items if getattr(bf, "fulfilled", False))
    return round(fulfilled / len(items), 2)


def build_init_long_result_payload(project_id: str, result: Any) -> dict[str, Any]:
    """Build the shared long-init payload surfaced by API/Desktop entrypoints."""
    outline = getattr(result, "outline", None)
    story_bible = getattr(result, "story_bible", None)
    character_bible = getattr(result, "character_bible", None)
    characters = getattr(character_bible, "characters", []) if character_bible else []
    return {
        "project_id": project_id,
        "title": getattr(story_bible, "title", "") if story_bible else "",
        "total_chapters": getattr(outline, "total_chapters", 0) if outline else 0,
        "volume_mode": bool(getattr(outline, "volume_mode", False)) if outline else False,
        "volume_count": len(getattr(outline, "volumes", []) if outline else []),
        "characters": [getattr(item, "name", "") for item in characters][:12],
    }


def build_run_chapter_result_payload(
    project_id: str,
    result: Any,
    *,
    chapter_number: int | None = None,
    preview_chars: int = DEFAULT_PREVIEW_CHARS,
    preview_ellipsis: bool = False,
) -> dict[str, Any]:
    """Build the shared single-chapter payload surfaced by API/Desktop entrypoints."""
    meta = getattr(result, "meta", None)
    text = str(getattr(result, "text", "") or "")
    eval_report = getattr(result, "eval_report", None)
    continuity_report = getattr(result, "continuity_report", None)
    causal_report = getattr(result, "causal_report", None)
    reading_power_report = getattr(result, "reading_power_report", None)
    bridge = getattr(result, "bridge", None)
    exit_state = getattr(result, "chapter_exit_state", None)
    resolved_chapter_number = chapter_number or int(getattr(meta, "chapter_number", 0) or 0)

    def _open_issue_count(report: Any) -> int:
        issues = getattr(report, "issues", []) or []
        count = 0
        for issue in issues:
            if isinstance(issue, dict):
                status = issue.get("status", "open")
            else:
                status = getattr(issue, "status", "open")
            if str(status or "open").strip().lower() == "open":
                count += 1
        return count

    return {
        "project_id": project_id,
        "chapter_number": resolved_chapter_number,
        "word_count": display_word_count(text),
        "overall_score": getattr(eval_report, "overall_score", 0.0) if eval_report else 0.0,
        "continuity_score": (
            getattr(continuity_report, "continuity_score", 0.0) if continuity_report else 0.0
        ),
        "continuity_issue_count": (
            _open_issue_count(continuity_report) if continuity_report else 0
        ),
        "causal_score": (
            getattr(causal_report, "causal_score", 0.0) if causal_report is not None else 0.0
        ),
        "causal_issue_count": (
            len(getattr(causal_report, "issues", []) or []) if causal_report is not None else 0
        ),
        "reading_power_score": (
            float(getattr(reading_power_report, "overall_score", 0.0))
            if reading_power_report is not None
            else None
        ),
        "bridge_summary": getattr(bridge, "bridge_summary", "") if bridge else "",
        "chapter_exit_summary": summarize_chapter_exit(exit_state),
        "preview": clip_preview(
            text,
            limit=preview_chars,
            ellipsis=preview_ellipsis,
        ),
        "warnings": [
            str(item) for item in (getattr(result, "warnings", []) or []) if str(item).strip()
        ],
    }


@dataclass(frozen=True)
class UsageMetrics:
    """Canonical usage metrics surfaced by desktop job payloads."""

    tokens_used: int = 0
    cost_usd: float = 0.0

    def as_payload(self, *, include_trace_aliases: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tokens_used": self.tokens_used,
            "cost_usd": self.cost_usd,
        }
        if include_trace_aliases:
            payload["total_tokens"] = self.tokens_used
            payload["total_cost_usd"] = self.cost_usd
        return payload


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        return max(int(value), 0)
    if value in (None, ""):
        return 0
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _coerce_cost(value: Any) -> float:
    """Coerce cost value to non-negative float (bools → 0.0)."""
    if isinstance(value, bool):
        return 0.0
    return coerce_float(value, default=0.0, clamp=(0.0, float("inf")))


def _trace_summary(result: Any) -> Mapping[str, Any]:
    payload = getattr(result, "trace_summary", {}) or {}
    return payload if isinstance(payload, Mapping) else {}


def extract_trace_step_usage(
    result: Any,
    *,
    max_steps: int = 24,
) -> list[dict[str, Any]]:
    """Extract compact per-step token usage for desktop task-flow display."""
    trace_summary = _trace_summary(result)
    steps = trace_summary.get("steps")
    if not isinstance(steps, list):
        return []

    entries: list[dict[str, Any]] = []
    for raw in steps:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name", "") or "").strip()
        if not name:
            continue
        tokens = _coerce_int(raw.get("tokens"))
        prompt_tokens = _coerce_int(raw.get("prompt_tokens"))
        completion_tokens = _coerce_int(raw.get("completion_tokens"))
        if tokens <= 0:
            tokens = prompt_tokens + completion_tokens
        if tokens <= 0 and prompt_tokens <= 0 and completion_tokens <= 0:
            continue
        entries.append(
            {
                "step": name,
                "tokens": tokens,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost_usd": _coerce_cost(raw.get("cost")),
                "model_call_count": _coerce_int(raw.get("model_call_count")),
            }
        )

    if len(entries) > max_steps:
        return entries[:max_steps]
    return entries


def extract_usage_metrics(result: Any) -> UsageMetrics:
    """Read usage metrics from shared result objects with trace-summary fallbacks."""
    meta = getattr(result, "meta", None)
    trace_summary = _trace_summary(result)

    tokens_used = _coerce_int(getattr(meta, "tokens_used", None))
    if tokens_used <= 0:
        tokens_used = _coerce_int(getattr(result, "tokens_used", None))
    if tokens_used <= 0:
        tokens_used = _coerce_int(trace_summary.get("tokens_used"))
    if tokens_used <= 0:
        tokens_used = _coerce_int(trace_summary.get("total_tokens"))
    trace_tokens = _coerce_int(trace_summary.get("total_tokens"))
    if trace_tokens > 0:
        # Trace summary is the canonical aggregation of model-call usage.
        tokens_used = trace_tokens

    cost_usd = _coerce_cost(getattr(meta, "cost_usd", None))
    if cost_usd <= 0.0:
        cost_usd = _coerce_cost(getattr(result, "cost_usd", None))
    if cost_usd <= 0.0:
        cost_usd = _coerce_cost(trace_summary.get("cost_usd"))
    if cost_usd <= 0.0:
        cost_usd = _coerce_cost(trace_summary.get("total_cost_usd"))
    trace_cost = _coerce_cost(trace_summary.get("total_cost_usd"))
    if trace_cost > 0.0:
        cost_usd = trace_cost

    return UsageMetrics(tokens_used=tokens_used, cost_usd=cost_usd)


def build_usage_payload(
    result: Any,
    *,
    include_trace_aliases: bool = False,
) -> dict[str, Any]:
    """Expose consistent desktop job usage keys with optional trace aliases."""
    return extract_usage_metrics(result).as_payload(
        include_trace_aliases=include_trace_aliases,
    )


def normalize_prepare_chapter_payload(result: Any) -> dict[str, Any]:
    """Normalize prepare results through the shared contract model."""
    payload = (
        result
        if isinstance(result, PrepareChapterResponse)
        else PrepareChapterResponse.model_validate(result)
    )
    return payload.model_dump(mode="json")


def build_repair_chapter_result_payload(
    project_id: str,
    result: Any,
    *,
    chapter_number: int,
) -> dict[str, Any]:
    """Build the shared repair payload surfaced by API and Desktop entrypoints."""
    applied = getattr(result, "applied", None)
    if applied is None:
        applied = getattr(result, "continuity_applied", False) or getattr(
            result, "causal_applied", False
        )
    payload: dict[str, Any] = {
        "project_id": project_id,
        "chapter_number": chapter_number,
        "applied": applied,
        "failure_reason": getattr(result, "failure_reason", None),
        "warnings": [str(w) for w in (getattr(result, "warnings", []) or []) if str(w).strip()],
        "patches_applied": getattr(result, "patches_applied", None),
        "patches_attempted": getattr(result, "patches_attempted", None),
    }
    repair_plan = getattr(result, "repair_plan", None)
    if repair_plan is not None:
        payload["repair_plan"] = (
            repair_plan.model_dump(mode="json")
            if hasattr(repair_plan, "model_dump")
            else repair_plan
        )
    return payload


def normalize_chapter_session_payload(result: Any) -> dict[str, Any]:
    """Normalize chapter-session results through the shared contract model."""
    payload = (
        result
        if isinstance(result, ChapterSessionResult)
        else ChapterSessionResult.model_validate(result)
    )
    data = payload.model_dump(mode="json")
    metadata = data.get("metadata")
    if isinstance(metadata, Mapping):
        for key in ("causal_score", "causal_issue_count", "warnings"):
            if key in metadata and key not in data:
                data[key] = metadata[key]
    return data
