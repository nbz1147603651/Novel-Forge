"""Diagnostics report helpers for stage-aware memory collection."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from novel_forge.core.utils.string import clean_str
from novel_forge.obs.logger import get_logger

_log = get_logger("pipeline.long.stage_memory")

_STAGE_ORDER = {
    "planning": 0,
    "draft": 1,
    "finalize": 2,
}


def _ordered_stage_names(stages: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(
        (
            stage_name
            for stage_name, payload in stages.items()
            if stage_name in _STAGE_ORDER and isinstance(payload, dict)
        ),
        key=lambda item: _STAGE_ORDER[item],
    )


def _copy_memory_diagnostics(diagnostics: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(diagnostics) if isinstance(diagnostics, dict) else {}


def build_memory_diagnostics_report(
    chapter_number: int,
    stages: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build the merged per-chapter memory diagnostics report."""
    ordered_stages = _ordered_stage_names(stages)
    counts = {
        "relevant_history": 0,
        "previous_chapter_events": 0,
        "motif_suggestions": 0,
        "forbidden_repetition": 0,
    }
    resolved_layers_by_stage: dict[str, list[str]] = {}
    missing_layers_by_stage: dict[str, list[str]] = {}
    history_reused_stages: list[str] = []
    context_available_stages: list[str] = []

    for stage_name in ordered_stages:
        payload = stages[stage_name]
        stage_counts = payload.get("counts", {})
        if isinstance(stage_counts, dict):
            for key in counts:
                counts[key] += int(stage_counts.get(key, 0) or 0)
        resolved_layers_by_stage[stage_name] = [
            str(item).strip()
            for item in list(payload.get("resolved_layers", []) or [])
            if str(item).strip()
        ]
        missing_layers_by_stage[stage_name] = [
            str(item).strip()
            for item in list(payload.get("missing_layers", []) or [])
            if str(item).strip()
        ]
        if payload.get("history_reused"):
            history_reused_stages.append(stage_name)
        if payload.get("memory_context_available"):
            context_available_stages.append(stage_name)

    return {
        "report_type": "chapter_memory_diagnostics",
        "chapter_number": chapter_number,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "available_stages": ordered_stages,
            "latest_stage": ordered_stages[-1] if ordered_stages else "",
            "resolved_layers_by_stage": resolved_layers_by_stage,
            "missing_layers_by_stage": missing_layers_by_stage,
            "history_reused_stages": history_reused_stages,
            "context_available_stages": context_available_stages,
            "counts": counts,
        },
        "stages": {
            stage_name: _copy_memory_diagnostics(stages[stage_name])
            for stage_name in ordered_stages
        },
    }


def persist_stage_memory_diagnostics_report(
    storage: Any,
    layout: Any,
    chapter_number: int,
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    """Merge and persist stage memory diagnostics for a chapter."""
    stage_name = clean_str(diagnostics.get("stage", "")).lower()
    if stage_name not in _STAGE_ORDER:
        return {}

    path = getattr(layout, "chapter_memory_diagnostics_path", None)
    if not callable(path):
        return {}
    report_path = path(chapter_number)

    stages: dict[str, dict[str, Any]] = {}
    if stage_name != "planning" and callable(getattr(storage, "exists", None)):
        try:
            if storage.exists(report_path):
                existing = storage.load_json(report_path)
                raw_stages = existing.get("stages", {}) if isinstance(existing, dict) else {}
                if isinstance(raw_stages, dict):
                    for name, payload in raw_stages.items():
                        if name in _STAGE_ORDER and isinstance(payload, dict):
                            stages[name] = _copy_memory_diagnostics(payload)
        except Exception as exc:
            _log.warning(
                "stage_memory_report_load_failed | chapter=%d | stage=%s | error=%s",
                chapter_number,
                stage_name,
                exc,
            )

    stages[stage_name] = _copy_memory_diagnostics(diagnostics)
    report = build_memory_diagnostics_report(chapter_number, stages)
    try:
        storage.save_json(report_path, report)
    except Exception as exc:
        _log.warning(
            "stage_memory_report_save_failed | chapter=%d | stage=%s | error=%s",
            chapter_number,
            stage_name,
            exc,
        )
        return {}
    return report
