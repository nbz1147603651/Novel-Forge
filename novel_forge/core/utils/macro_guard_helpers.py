"""MacroGuard helper utilities for trigger logic and data loading."""

from __future__ import annotations

import json
from typing import Any

from novel_forge.common.constants import severity_at_least
from novel_forge.core.config import Settings
from novel_forge.persistence.models import ProjectLayout


def should_trigger_macro_guard(
    chapter_number: int,
    layout: ProjectLayout,
    storage: Any,
    settings: Settings,
) -> bool:
    """Determine whether macro guard audit should run for this chapter.

    Trigger conditions:
    1. Every ``settings.long_macro_guard_interval`` chapters.
    2. Current chapter is a milestone chapter (key turning point).
    3. Last 3 consecutive chapters have medium+ deviation.
    4. Not within cooldown period after last adjustment.
    """
    if not settings.long_macro_guard_enabled:
        return False

    # Condition 4: cooldown check
    cooldown_marker = layout.states_dir / "macro_guard_cooldown.json"
    if storage.exists(cooldown_marker):
        try:
            cooldown_data = storage.load_json(cooldown_marker)
            last_adjustment_chapter = int(cooldown_data.get("last_adjustment_chapter", 0))
            cooldown_chapters = int(
                cooldown_data.get("cooldown_chapters", settings.long_macro_guard_cooldown_chapters)
            )
            if chapter_number - last_adjustment_chapter <= cooldown_chapters:
                return False
        except Exception:
            pass

    # Condition 1: interval trigger
    if chapter_number % settings.long_macro_guard_interval == 0:
        return True

    # Condition 2: milestone chapter
    milestones = load_outline_milestones(layout, storage)
    if chapter_number in milestones:
        return True

    # Condition 3: consecutive medium+ deviations
    recent_entries = load_recent_audit_entries(storage, layout, n=3)
    if len(recent_entries) >= 3:
        if all(
            severity_at_least(entry.get("risk_level", "low"), "medium")
            for entry in recent_entries
        ):
            return True

    return False


def load_recent_audit_entries(
    storage: Any,
    layout: ProjectLayout,
    n: int,
) -> list[dict[str, Any]]:
    """Read the last *n* records from ``chapter_audit_log.jsonl``.

    Returns records in append order (oldest → newest) for the selected window.
    """
    audit_log_path = layout.root / "states" / "chapter_audit_log.jsonl"
    if not storage.exists(audit_log_path):
        return []

    try:
        text = storage.load_text(audit_log_path)
    except Exception:
        return []

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    records: list[dict[str, Any]] = []
    for line in lines:
        try:
            record = json.loads(line)
            if isinstance(record, dict):
                records.append(record)
        except json.JSONDecodeError:
            continue

    # Return newest *n* entries
    return records[-n:] if len(records) > n else records


def load_outline_milestones(
    layout: ProjectLayout,
    storage: Any,
) -> list[int]:
    """Extract milestone chapter numbers from the project outline.

    Looks for ``key_turning_points`` on the outline object (NarrativeBlueprint)
    and falls back to an empty list if the field is absent.
    """
    outline_path = layout.outline_path
    if not storage.exists(outline_path):
        return []

    try:
        outline_data = storage.load_json(outline_path)
    except Exception:
        return []

    # Handle both StoryOutline dict and NarrativeBlueprint dict
    turning_points = outline_data.get("key_turning_points", [])
    if turning_points:
        return [
            int(tp["chapter_number"])
            for tp in turning_points
            if isinstance(tp, dict) and tp.get("chapter_number")
        ]

    # Fallback: check if StoryOutline has explicit milestone markers on chapters
    chapters = outline_data.get("chapters", [])
    milestones: list[int] = []
    for ch in chapters:
        if isinstance(ch, dict) and ch.get("is_milestone"):
            milestones.append(int(ch["chapter_number"]))

    return milestones


def load_macro_guard_adjustment_state(
    storage: Any,
    layout: ProjectLayout,
) -> dict[str, int]:
    """Load persisted macro-guard adjustment bookkeeping."""
    state_path = layout.states_dir / "macro_guard_adjustment_state.json"
    if not storage.exists(state_path):
        return {"applied_adjustments": 0, "last_adjustment_chapter": 0}
    try:
        payload = storage.load_json(state_path)
    except Exception:
        return {"applied_adjustments": 0, "last_adjustment_chapter": 0}
    return {
        "applied_adjustments": int(payload.get("applied_adjustments", 0) or 0),
        "last_adjustment_chapter": int(payload.get("last_adjustment_chapter", 0) or 0),
    }


def record_macro_guard_adjustment(
    storage: Any,
    layout: ProjectLayout,
    chapter_number: int,
    settings: Settings,
) -> dict[str, int]:
    """Record one applied outline adjustment and refresh macro-guard cooldown."""
    state_path = layout.states_dir / "macro_guard_adjustment_state.json"
    state = load_macro_guard_adjustment_state(storage, layout)
    updated = {
        "applied_adjustments": int(state.get("applied_adjustments", 0) or 0) + 1,
        "last_adjustment_chapter": int(chapter_number),
    }
    storage.save_json(state_path, updated)

    cooldown_marker = layout.states_dir / "macro_guard_cooldown.json"
    storage.save_json(
        cooldown_marker,
        {
            "last_adjustment_chapter": int(chapter_number),
            "cooldown_chapters": int(
                getattr(settings, "long_macro_guard_cooldown_chapters", 5) or 0
            ),
        },
    )
    return updated
