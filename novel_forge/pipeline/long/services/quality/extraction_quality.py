"""Post-extraction quality validation for creative report and deltas.

This module is called after ``ExtractCanonDeltaStep._normalize_extracted_payload``
to detect hollow creative reports and clean placeholder relationship/thread deltas
before they pollute downstream memory/state.

Design constraints:
- Does NOT modify ``CreativeReport`` Pydantic schema (preserves historical compatibility).
- Does NOT expand ``format_contracts.py`` into nested key enforcement.
- Quiet chapters (genuinely no new content) are NOT false-flagged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger(__name__)

_CORE_CARRY_FIELDS = (
    "structured_summary",
    "must_carry_forward",
    "suggestions_for_next_chapter",
    "bridge_hints",
)

_LONG_CHAPTER_THRESHOLD = 2000

_RELATIONSHIP_NON_CHANGE_MARKERS = (
    "未直接出现",
    "没有直接出现",
    "无直接互动",
    "未发生互动",
    "没有互动",
    "未出现互动",
    "未提及互动",
    "没有提及互动",
    "无明确变化",
    "没有明确变化",
    "未发生变化",
    "关系无变化",
    "未体现关系变化",
    "没有新的关系变化",
    "无新增关系变化",
)

_CHARACTER_STATE_DEFAULT_STABILITY = 0.5


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractionQualityReport:
    """Quality assessment of a creative report extraction payload."""

    is_hollow: bool = False
    """All core carry-forward fields in creative_report are empty."""

    placeholder_relationship_deltas: list[int] = field(default_factory=list)
    """Indices (in ``relationship_deltas``) of placeholder entries."""

    placeholder_plot_thread_deltas: list[int] = field(default_factory=list)
    """Indices (in ``plot_thread_deltas``) of placeholder entries."""

    placeholder_character_state_deltas: list[int] = field(default_factory=list)
    """Indices (in ``character_state_deltas``) of placeholder entries."""

    retry_recommended: bool = False
    """Whether a single fragment retry is likely to produce better output."""

    severity: str = "pass"
    """'pass' | 'warn' | 'hard_fail'."""

    details: str = ""
    """Human-readable summary of findings."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_creative_report_payload(
    payload: dict[str, Any],
    chapter_text: str,
    chapter_number: int,
) -> ExtractionQualityReport:
    """Validate quality of the normalized extraction payload.

    Parameters
    ----------
    payload:
        The normalized extraction dict (pre-Pydantic validation).
    chapter_text:
        Original chapter text, used for length-based heuristics.
    chapter_number:
        Chapter number for logging context.

    Returns
    -------
    ExtractionQualityReport with quality assessment.
    """
    creative_report = payload.get("creative_report") or {}
    if not isinstance(creative_report, dict):
        creative_report = {}

    # Check if ALL core carry-forward fields are empty
    all_empty = _all_core_fields_empty(creative_report)
    chapter_length = len(chapter_text or "")
    is_long = chapter_length > _LONG_CHAPTER_THRESHOLD

    # Detect placeholder deltas
    placeholder_rel = _find_placeholder_indices(
        payload.get("relationship_deltas") or [],
        is_placeholder_relationship_delta,
    )
    placeholder_pt = _find_placeholder_indices(
        payload.get("plot_thread_deltas") or [],
        is_placeholder_plot_thread_delta,
    )
    placeholder_cs = _find_placeholder_indices(
        payload.get("character_state_deltas") or [],
        is_placeholder_character_state_delta,
    )

    # Determine severity and retry
    if all_empty and is_long:
        retry_recommended = True
        severity = "warn"
        details = (
            f"ch{chapter_number}: creative_report core fields all empty "
            f"(chapter length {chapter_length} chars)"
        )
    elif all_empty:
        # Short chapter — may be legitimately quiet
        severity = "warn"
        retry_recommended = False
        details = (
            f"ch{chapter_number}: creative_report core fields all empty "
            f"(short chapter {chapter_length} chars — may be quiet chapter)"
        )
    else:
        severity = "pass"
        retry_recommended = False
        details = f"ch{chapter_number}: creative_report has substantive content"

    # Upgrade to hard_fail if: long chapter + all core empty + ALL deltas are placeholder
    if (
        all_empty
        and is_long
        and _has_any_deltas(payload)
        and not placeholder_rel
        and not placeholder_pt
    ):
        # There are deltas but they're all valid — creative_report being empty is suspicious
        pass  # keep as warn, retry may help

    if all_empty and is_long and not _has_any_deltas(payload):
        severity = "hard_fail"
        details += "; no valid deltas present"

    # Upgrade severity when character_state_deltas are all placeholder
    raw_cs = payload.get("character_state_deltas") or []
    all_cs_placeholder = (
        isinstance(raw_cs, list)
        and len(raw_cs) > 0
        and len(placeholder_cs) == len(raw_cs)
    )
    if all_cs_placeholder and is_long:
        if severity == "pass":
            severity = "warn"
        retry_recommended = True
        details += f"; all {len(raw_cs)} character_state_deltas are placeholder"

    return ExtractionQualityReport(
        is_hollow=all_empty,
        placeholder_relationship_deltas=placeholder_rel,
        placeholder_plot_thread_deltas=placeholder_pt,
        placeholder_character_state_deltas=placeholder_cs,
        retry_recommended=retry_recommended,
        severity=severity,
        details=details,
    )


def is_placeholder_relationship_delta(delta: dict[str, Any]) -> bool:
    """Return True if the relationship delta appears to be a placeholder.

    A placeholder has:
    - Empty/missing ``change_summary`` or a non-change placeholder summary
    - Default ``trust=0.5`` and ``tension=0.5`` in the nested relationship
    """
    if not isinstance(delta, dict):
        return False

    change_summary = str(delta.get("change_summary", "") or "").strip()
    if _looks_like_relationship_non_change(change_summary):
        return True

    relationship = delta.get("relationship")
    if not isinstance(relationship, dict):
        return False  # Malformed — not a placeholder, just broken

    public_status = str(relationship.get("public_status", "") or "").strip()
    last_shift_event = str(relationship.get("last_shift_event", "") or "").strip()
    if (
        _looks_like_relationship_non_change(public_status)
        or _looks_like_relationship_non_change(last_shift_event)
    ):
        return True

    if change_summary:
        return False  # Has meaningful change description

    trust = _safe_float(relationship.get("trust"), default=None)
    tension = _safe_float(relationship.get("tension"), default=None)

    return trust == 0.5 and tension == 0.5


def is_placeholder_character_state_delta(delta: dict[str, Any]) -> bool:
    """Return True if the character state delta appears to be a placeholder.

    A placeholder has:
    - Empty/missing ``change_summary``
    - All ``to_state`` fields at defaults (stability=0.5, empty strings, empty lists)
    - ``last_seen_chapter`` == 0
    """
    if not isinstance(delta, dict):
        return False

    change_summary = str(delta.get("change_summary", "") or "").strip()
    if change_summary:
        return False  # Has meaningful change description

    to_state = delta.get("to_state")
    if not isinstance(to_state, dict):
        return False  # Malformed — not a placeholder, just broken

    # Check if all meaningful fields are at defaults
    last_seen = _safe_int(to_state.get("last_seen_chapter"), default=0)
    if last_seen > 0:
        return False  # Has been seen in a chapter — not placeholder

    emotional = to_state.get("emotional") or {}
    stability = _safe_float(emotional.get("stability"), default=None)
    primary_emotion = str(emotional.get("primary_emotion", "") or "").strip()
    desire = str(emotional.get("desire", "") or "").strip()
    fear = str(emotional.get("fear", "") or "").strip()

    physical = to_state.get("physical") or {}
    location = str(physical.get("location", "") or "").strip()
    injuries = physical.get("injuries") or []
    inventory = physical.get("inventory") or []

    motivation = to_state.get("motivation") or {}
    short_term_goal = str(motivation.get("short_term_goal", "") or "").strip()
    current_drive = str(motivation.get("current_drive", "") or "").strip()

    knowledge = to_state.get("knowledge_state") or {}
    known_facts = knowledge.get("known_facts") or []
    suspicions = knowledge.get("suspicions") or []

    voice = str(to_state.get("voice", "") or "").strip()

    # All fields at defaults → placeholder
    return (
        stability == _CHARACTER_STATE_DEFAULT_STABILITY
        and not primary_emotion
        and not desire
        and not fear
        and not location
        and not injuries
        and not inventory
        and not short_term_goal
        and not current_drive
        and not known_facts
        and not suspicions
        and not voice
    )


def is_placeholder_plot_thread_delta(delta: dict[str, Any]) -> bool:
    """Return True if the plot thread delta appears to be a placeholder.

    A placeholder has:
    - Empty or missing ``change_summary``
    - Default ``thread.status='active'`` with no meaningful updates
    """
    if not isinstance(delta, dict):
        return False

    change_summary = str(delta.get("change_summary", "") or "").strip()
    if change_summary:
        return False  # Has meaningful change description

    thread = delta.get("thread")
    if not isinstance(thread, dict):
        return False  # Malformed

    # If status is default 'active' and other thread fields are defaults, it's placeholder
    status = str(thread.get("status", "active") or "active").strip()
    summary = str(thread.get("summary", "") or "").strip()
    last_touched = _safe_int(thread.get("last_touched_chapter"), default=0)

    return status == "active" and not summary and last_touched == 0


def clean_placeholder_deltas(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the payload with placeholder deltas removed.

    Removes:
    - Relationship deltas where ``change_summary`` is empty AND trust/tension are defaults.
    - Plot thread deltas where ``change_summary`` is empty AND thread state is default.
    - Character state deltas where ``change_summary`` is empty AND all to_state fields are defaults.
    """
    cleaned = dict(payload)

    raw_rel = cleaned.get("relationship_deltas")
    if isinstance(raw_rel, list):
        cleaned["relationship_deltas"] = [
            d for d in raw_rel if not is_placeholder_relationship_delta(d)
        ]

    raw_pt = cleaned.get("plot_thread_deltas")
    if isinstance(raw_pt, list):
        cleaned["plot_thread_deltas"] = [
            d for d in raw_pt if not is_placeholder_plot_thread_delta(d)
        ]

    raw_cs = cleaned.get("character_state_deltas")
    if isinstance(raw_cs, list):
        cleaned["character_state_deltas"] = [
            d for d in raw_cs if not is_placeholder_character_state_delta(d)
        ]

    return cleaned


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _all_core_fields_empty(creative_report: dict[str, Any]) -> bool:
    """Check if ALL core carry-forward fields are empty."""
    for field_name in _CORE_CARRY_FIELDS:
        value = creative_report.get(field_name)
        if isinstance(value, str) and value.strip():
            return False
        if isinstance(value, list) and value:
            return False
    return True


def _find_placeholder_indices(
    items: list[Any],
    checker: Any,
) -> list[int]:
    """Return indices of items that pass the placeholder checker."""
    if not isinstance(items, list):
        return []
    return [i for i, item in enumerate(items) if checker(item)]


def _has_any_deltas(payload: dict[str, Any]) -> bool:
    """Return True if the payload has any non-empty delta arrays."""
    for key in ("relationship_deltas", "plot_thread_deltas", "character_state_deltas"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            return True
    return False


def _looks_like_relationship_non_change(text: str) -> bool:
    """Return True when text explicitly says no direct relationship change occurred."""
    compact = "".join(str(text or "").split())
    if not compact:
        return False
    return any(marker in compact for marker in _RELATIONSHIP_NON_CHANGE_MARKERS)


def _safe_float(value: Any, default: float | None = 0.0) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
