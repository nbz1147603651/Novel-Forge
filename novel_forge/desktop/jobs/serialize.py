"""Sub-module of novel_forge.desktop.jobs.

Auto-generated in the M3.5 split. Contains serialize.py symbols.
"""

from __future__ import annotations

import logging
from typing import Any

from novel_forge.core.infra.event_bus import (
    EventBus,
)
from novel_forge.workspace.result_payloads import (
    build_init_long_result_payload,
    build_repair_chapter_result_payload,
    build_run_chapter_result_payload,
    build_run_short_result_payload,
    build_usage_payload,
    extract_trace_step_usage,
    normalize_chapter_session_payload,
    normalize_prepare_chapter_payload,
)

logger = logging.getLogger(__name__)

_event_bus: EventBus | None = None


def _extract_trace_summary(result: Any) -> dict[str, Any]:
    """Read canonical trace summary from both ExecutionResult and raw result objects."""
    direct = getattr(result, "trace_summary", None)
    if isinstance(direct, dict) and direct:
        return direct

    nested = getattr(result, "result", None)
    if isinstance(nested, dict):
        nested_trace = nested.get("trace_summary")
        if isinstance(nested_trace, dict) and nested_trace:
            return nested_trace
    else:
        nested_trace = getattr(nested, "trace_summary", None)
        if isinstance(nested_trace, dict) and nested_trace:
            return nested_trace

    if isinstance(direct, dict):
        return direct
    return {}


def _compact_memory_status_payload(data: dict[str, Any]) -> dict[str, Any]:
    def _compact_list(
        items: Any, *, max_items: int, keep_keys: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            return []
        compacted: list[dict[str, Any]] = []
        for item in items[:max_items]:
            if not isinstance(item, dict):
                continue
            compacted.append({key: item[key] for key in keep_keys if key in item})
        return compacted

    def _compact_text_list(items: Any, *, max_items: int) -> list[str]:
        if not isinstance(items, list):
            return []
        compacted: list[str] = []
        for item in items[:max_items]:
            text = str(item).strip()
            if text:
                compacted.append(text)
        return compacted

    def _compact_chapter_motifs(raw: Any) -> dict[str, list[str]]:
        if not isinstance(raw, dict):
            return {}
        compacted: dict[str, list[str]] = {}
        for chapter_key, motif_ids in raw.items():
            try:
                chapter_no = int(chapter_key)
            except (TypeError, ValueError):
                continue
            if not isinstance(motif_ids, (list, tuple, set)):
                continue
            ids = [str(item).strip() for item in motif_ids if str(item).strip()]
            if ids:
                compacted[str(chapter_no)] = ids
        return compacted

    return {
        "chapter": int(data.get("chapter", 0) or 0),
        "indexed_chapters": int(data.get("indexed_chapters", 0) or 0),
        "last_indexed_chapter": int(data.get("last_indexed_chapter", 0) or 0),
        "cached_summaries": int(data.get("cached_summaries", 0) or 0),
        "summary_hash_bound": int(data.get("summary_hash_bound", 0) or 0),
        "chapter_hash_tracked": int(data.get("chapter_hash_tracked", 0) or 0),
        "summary_stats": data.get("summary_stats", {}),
        "save_success": bool(data.get("save_success", False)),
        "memory_module_status": data.get("memory_module_status", {}),
        "outline_stats": data.get("outline_stats", {}),
        "motifs": _compact_list(
            data.get("motifs", []),
            max_items=500,
            keep_keys=(
                "motif_id",
                "category",
                "description",
                "name",
                "occurrence_count",
                "first_chapter",
                "last_chapter",
            ),
        ),
        "motif_suggestions": _compact_list(
            data.get("motif_suggestions", []),
            max_items=8,
            keep_keys=("motif_id", "suggestion", "priority"),
        ),
        "repetition_warnings": _compact_list(
            data.get("repetition_warnings", []),
            max_items=8,
            keep_keys=("motif_id", "warning_type", "message", "severity"),
        ),
        "unresolved_questions": _compact_text_list(
            data.get("unresolved_questions", []),
            max_items=12,
        ),
        "chapter_motifs": _compact_chapter_motifs(data.get("chapter_motifs", {})),
    }


def _compact_memory_stage_payload(data: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chapter": int(data.get("chapter", 0) or 0),
        "success": bool(data.get("success", True)),
        "count": int(data.get("count", 0) or 0),
        "length": int(data.get("length", 0) or 0),
        "last_indexed": int(data.get("last_indexed", 0) or 0),
        "completed": int(data.get("completed", 0) or 0),
        "pending": int(data.get("pending", 0) or 0),
    }
    if "error" in data:
        payload["error"] = str(data.get("error") or "")[:180]
    if isinstance(data.get("memory_status"), dict):
        payload["memory_status"] = _compact_memory_status_payload(data["memory_status"])
    return payload


def _compact_stage_memory_context_payload(data: dict[str, Any]) -> dict[str, Any]:
    def _compact_layers(raw: Any) -> list[str]:
        if not isinstance(raw, list):
            return []
        layers: list[str] = []
        for item in raw[:4]:
            text = str(item or "").strip()
            if text:
                layers.append(text)
        return layers

    def _compact_counts(raw: Any) -> dict[str, int]:
        if not isinstance(raw, dict):
            return {}
        keys = (
            "relevant_history",
            "previous_chapter_events",
            "outline_context_fields",
            "motif_suggestions",
            "forbidden_repetition",
            "active_motif_ids",
            "motif_active_entries",
            "prompt_context_fields",
        )
        result: dict[str, int] = {}
        for key in keys:
            if key in raw:
                result[key] = int(raw.get(key, 0) or 0)
        return result

    def _compact_flags(raw: Any) -> dict[str, bool]:
        if not isinstance(raw, dict):
            return {}
        keys = (
            "has_relevant_history",
            "has_previous_chapter_events",
            "has_critique_context",
            "has_outline_context",
            "has_motif_continuity",
            "has_motif_suggestions",
            "has_prompt_summary",
            "has_forbidden_repetition",
            "has_layered_context",
        )
        return {key: bool(raw.get(key, False)) for key in keys if key in raw}

    def _compact_sources(raw: Any) -> dict[str, str]:
        if not isinstance(raw, dict):
            return {}
        keys = (
            "prompt_context",
            "layered_context",
            "relevant_history",
            "previous_chapter_events",
            "outline_context",
            "motif_continuity",
            "motif_suggestions",
        )
        result: dict[str, str] = {}
        for key in keys:
            value = str(raw.get(key, "") or "").strip()
            if value:
                result[key] = value
        return result

    payload = {
        "stage": str(data.get("stage", "") or "").strip(),
        "chapter_number": int(data.get("chapter_number", 0) or 0),
        "requested_layers": _compact_layers(data.get("requested_layers")),
        "resolved_layers": _compact_layers(data.get("resolved_layers")),
        "missing_layers": _compact_layers(data.get("missing_layers")),
        "counts": _compact_counts(data.get("counts")),
        "flags": _compact_flags(data.get("flags")),
        "sources": _compact_sources(data.get("sources")),
        "history_reused": bool(data.get("history_reused", False)),
        "memory_context_available": bool(data.get("memory_context_available", False)),
    }
    if "duration_ms" in data:
        payload["duration_ms"] = float(data.get("duration_ms", 0.0) or 0.0)
    return payload


def _compact_payload(step: str, data: Any) -> dict[str, Any]:
    if data is None:
        return {}
    if hasattr(data, "model_dump"):
        return _compact_payload(step, data.model_dump(mode="json"))
    if isinstance(data, dict):
        if step == "run_log_started":
            keys: tuple[str, ...] = ("project_id", "command", "run_id", "run_log_dir")
            return {key: data[key] for key in keys if key in data}
        if step == "model_call_update":
            keys = (
                "event",
                "status",
                "run_log_dir",
                "call_id",
                "task",
                "provider",
                "model",
                "route",
                "max_tokens",
                "temperature",
                "latency_ms",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "cost_usd",
                "timeout_s",
                "text_length",
                "response_preview",
                "error_preview",
            )
            return {key: data[key] for key in keys if key in data}
        if step.startswith("llm_stream_"):
            keys = (
                "stream_id",
                "task",
                "chapter",
                "chapter_number",
                "attempt",
                "stream_kind",
                "output_kind",
                "text",
                "text_length",
                "reasoning",
                "reasoning_length",
                "segments",
                "provider",
                "model",
                "model_id",
                "error",
                "message",
                "finish_reason",
            )
            reduced = {key: data[key] for key in keys if key in data}
            if "delta" in data:
                reduced["delta"] = str(data.get("delta") or "")
            return reduced
        if step.startswith("human_decision_"):
            keys = (
                "decision_id",
                "kind",
                "project_id",
                "chapter_number",
                "title",
                "message",
                "options",
                "default_option",
                "timeout_seconds",
                "risk",
                "cost_hint",
                "choice",
                "custom_text",
                "timed_out",
            )
            return {key: data[key] for key in keys if key in data}
        if step == "repair_strategy_diagnosis":
            keys = (
                "chapter",
                "dimension",
                "round",
                "accepted",
                "preferred_strategy",
                "confidence",
                "confidence_floor",
                "reason",
                "diagnostic_summary",
                "root_causes",
                "risk_flags",
            )
            return {key: data[key] for key in keys if key in data}
        if step == "repair_repeated_issue_guard":
            keys = (
                "chapter",
                "dimension",
                "round",
                "issue_signature",
                "attempts",
                "threshold",
                "action",
                "preferred_strategy",
                "reason",
            )
            return {key: data[key] for key in keys if key in data}
        if step == "repair_round_focus":
            keys = (
                "chapter",
                "dimension",
                "round",
                "max_issues_per_round",
                "selected_count",
                "skipped_count",
                "skipped_issue_signatures",
            )
            return {key: data[key] for key in keys if key in data}
        if step in {
            "memory_planning_context",
            "memory_draft_context",
            "memory_finalize_context",
        }:
            return _compact_stage_memory_context_payload(data)
        if step == "memory_updated":
            return _compact_memory_status_payload(data)
        if step.startswith("memory_"):
            return _compact_memory_stage_payload(data)
        if step == "word_count_archive_gate":

            def _compact_assessment(raw: Any) -> dict[str, Any]:
                if not isinstance(raw, dict):
                    return {}
                result: dict[str, Any] = {}
                for key in ("actual", "target", "band", "action", "deviation_pct"):
                    if key in raw:
                        result[key] = raw[key]
                return result

            payload = {
                "chapter": int(data.get("chapter", 0) or 0),
                "changed": bool(data.get("changed", False)),
                "accepted": bool(data.get("accepted", False)),
                "mode": str(data.get("mode", "") or ""),
                "reason": str(data.get("reason", "") or ""),
                "before": _compact_assessment(data.get("before")),
                "after": _compact_assessment(data.get("after")),
            }
            return {key: value for key, value in payload.items() if value not in ("", {}, None)}
    if isinstance(data, dict):
        keys = (
            "project_id",
            "chapter",
            "chapter_number",
            "violation_count",
            "issue_pool_size",
            "issue_pool_enabled",
            "phase",
            "flagged_count",
            "target_count",
            "max_target_chapters",
            "artifact",
            "batch",
            "batch_index",
            "batch_total",
            "batches_done",
            "batch_start",
            "batch_end",
            "chapters_done",
            "chapters_total",
            "safe_chapters_done",
            "safe_saved_chapter",
            "safe_next_chapter",
            "current_batch_status",
            "total",
            "targeted",
            "processed",
            "applied",
            "failed",
            "chapters_attempted",
            "chapters_processed",
            "chapters_skipped",
            "chapters_missing",
            "chapters_empty",
            "chapters_failed",
            "motifs_before",
            "motifs_after",
            "motifs_touched",
            "occurrences_before",
            "occurrences_after",
            "occurrences_seen",
            "created_motifs",
            "extraction_cache_merged",
            "motifs_extracted",
            "matched_by_ref",
            "matched_by_fuzzy",
            "panel_expanded",
            "order",
            "attempt",
            "max_attempts",
            "max_retries",
            "next_max_tokens",
            "retry_temperature",
            "repair_concurrency",
            "round",
            "score",
            "overall_score",
            "alignment_score",
            "continuity_score",
            "passed",
            "claims",
            "active_claims",
            "extracted_claims",
            "claim_count",
            "active_claim_count",
            "invalidated_claim_count",
            "candidates",
            "degraded_memory",
            "verdict",
            "issues",
            "issue_count",
            "high_or_critical",
            "max_parallel",
            "cached",
            "source",
            "origin",
            "status",
            "task",
            "format_block",
            "error_type",
            "repaired",
            "repair_action",
            "finish_reason",
            "model_id",
            "max_tokens",
            "old_max_tokens",
            "new_max_tokens",
            "model_limit",
            "backoff_seconds",
            "stage",
            "text_changed",
            "message",
            "reason",
            "path",
            "log_file",
            "error",
        )
        reduced = {key: data[key] for key in keys if key in data}
        raw_excerpt = str(data.get("raw_excerpt") or "").strip()
        if raw_excerpt:
            reduced["raw_excerpt"] = raw_excerpt[:420] + ("…" if len(raw_excerpt) > 420 else "")
        required_keys = data.get("required_keys")
        if isinstance(required_keys, list):
            reduced["required_keys"] = [str(item) for item in required_keys[:12]]
        violations = data.get("violations")
        if isinstance(violations, list):
            compact_violations = []
            for item in violations[:5]:
                text = str(item or "").strip()
                if text:
                    compact_violations.append(text[:240] + ("…" if len(text) > 240 else ""))
            if compact_violations:
                reduced["violations"] = compact_violations
        if reduced:
            return reduced
        return {"keys": sorted(str(key) for key in data)[:8]}
    if isinstance(data, list):
        return {"items": len(data)}
    text = str(data).strip()
    if not text:
        return {}
    return {"preview": text[:160] + ("…" if len(text) > 160 else "")}


def _serialize_short_result(project_id: str, result: Any) -> dict[str, Any]:
    payload = build_run_short_result_payload(
        project_id,
        result,
        preview_chars=280,
        preview_ellipsis=True,
    )
    payload.update(build_usage_payload(result, include_trace_aliases=True))
    step_usage = extract_trace_step_usage(result)
    if step_usage:
        payload["token_steps"] = step_usage
    return payload


def _serialize_init_result(project_id: str, result: Any) -> dict[str, Any]:
    payload = build_init_long_result_payload(project_id, result)
    payload.update(build_usage_payload(result, include_trace_aliases=True))
    return payload


def _serialize_chapter_result(project_id: str, chapter_number: int, result: Any) -> dict[str, Any]:
    payload = build_run_chapter_result_payload(
        project_id,
        result,
        chapter_number=chapter_number,
        preview_chars=280,
        preview_ellipsis=True,
    )
    payload.update(build_usage_payload(result, include_trace_aliases=True))
    step_usage = extract_trace_step_usage(result)
    if step_usage:
        payload["token_steps"] = step_usage
    return payload


def _serialize_prepare_result(result: Any) -> dict[str, Any]:
    return normalize_prepare_chapter_payload(result)


def _serialize_session_result(result: Any) -> dict[str, Any]:
    return normalize_chapter_session_payload(result)


def _serialize_repair_result(result: Any, project_id: str, chapter_number: int) -> dict[str, Any]:
    return build_repair_chapter_result_payload(
        project_id,
        result,
        chapter_number=chapter_number,
    )


def _serialize_polish_result(result: Any, project_id: str, chapter_number: int) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "chapter_number": chapter_number,
        "original_word_count": getattr(result, "original_word_count", 0),
        "polished_word_count": getattr(result, "polished_word_count", 0),
        "warnings": list(getattr(result, "warnings", []) or []),
    }


def _serialize_consistency_result(result: Any, project_id: str) -> dict[str, Any]:
    auto_repair = getattr(result, "auto_repair", None)
    repair_report_path = (
        auto_repair.get("repair_report_path") if isinstance(auto_repair, dict) else None
    )
    return {
        "project_id": project_id,
        "issue_count": len(getattr(result, "issues", [])),
        "consistency_score": getattr(result, "consistency_score", 0.0),
        "summary": getattr(result, "summary", ""),
        "analysis_mode": getattr(result, "analysis_mode", "summary"),
        "chapters_audited": list(getattr(result, "chapters_audited", []) or []),
        "truncated_chapters": list(getattr(result, "truncated_chapters", []) or []),
        "auto_repair": auto_repair,
        "repair_report_path": repair_report_path,
    }
