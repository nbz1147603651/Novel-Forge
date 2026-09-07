"""Preset management for desktop workflow forms.

Handles saving, loading, listing and deleting form presets (short-form and long-form),
as well as importing presets from novel_forge.cli.json, AI polish history tracking,
polish suggestions persistence, and preset version backup.
"""

from __future__ import annotations

import copy
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novel_forge.core.config import get_settings
from novel_forge.core.domain.story_defaults import DEFAULT_GENRE, DEFAULT_TONE
from novel_forge.persistence.filesystem import atomic_write_text

# Exposed as a module-level variable so tests can monkeypatch it.
_PRESETS_DIR: Path | None = None

# Max history entries to retain per preset
_MAX_HISTORY_ENTRIES = 30


def _presets_dir() -> Path:
    if _PRESETS_DIR is not None:
        return _PRESETS_DIR
    return Path(get_settings().storage_root) / ".presets"


SHORT_TEMPLATE: dict[str, Any] = {
    "theme": "",
    "genre": DEFAULT_GENRE,
    "tone": DEFAULT_TONE,
    "length_target": 3200,
    "max_edit_rounds": 2,
    "segment_trigger_words": 5500,
    "writing_mode": "auto",
    "title": "",
    "language": "zh",
    "characters_hint": "",
    "world_hint": "",
    "conflict_hint": "",
    "pov_hint": "",
    "opening_style": "",
    "ending_style": "",
    "extra_instructions": "",
    "project_id": "",
    "research_enabled": False,
    "research_provider": "auto",
    "research_query_hint": "",
    "blueprint_element_preferences": {
        "preset_id": "",
        "manual_override": False,
        "items": [],
    },
}

LONG_TEMPLATE: dict[str, Any] = {
    "premise": "",
    "genre": DEFAULT_GENRE,
    "tone": DEFAULT_TONE,
    "total_chapters": 24,
    "words_per_chapter": 4500,
    "volume_mode": "auto",
    "chapters_per_volume": 0,
    "title": "",
    "language": "zh",
    "characters_hint": "",
    "world_hint": "",
    "conflict_hint": "",
    "pov_hint": "",
    "opening_style": "",
    "ending_style": "",
    "extra_instructions": "",
    "polish_hint": "",
    "project_id": "",
    "research_enabled": False,
    "research_provider": "auto",
    "research_query_hint": "",
    "blueprint_element_preferences": {
        "preset_id": "",
        "manual_override": False,
        "items": [],
    },
}

_MODE_TEMPLATES: dict[str, dict[str, Any]] = {
    "short": SHORT_TEMPLATE,
    "long": LONG_TEMPLATE,
}


def preset_template(mode: str) -> dict[str, Any]:
    """Return a copy of the built-in JSON template for a preset mode."""
    template = _MODE_TEMPLATES.get(mode)
    if template is None:
        raise ValueError(f"Unsupported preset mode: {mode}")
    return copy.deepcopy(template)


def sanitize_preset_payload(mode: str, data: dict[str, Any]) -> dict[str, Any]:
    """Keep only fields belonging to the selected mode and backfill defaults."""
    template = preset_template(mode)
    for key in template:
        if key in data:
            template[key] = data[key]
    if mode in {"short", "long"}:
        template["research_enabled"] = _coerce_bool(template["research_enabled"])
        template["research_provider"] = (
            str(template["research_provider"] or "auto").strip().lower() or "auto"
        )
        template["research_query_hint"] = str(template["research_query_hint"] or "")
    return template


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"0", "false", "no", "off", ""}:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True
    return bool(value)


def _ensure_dir(mode: str) -> Path:
    d = _presets_dir() / mode
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def _history_dir_for(mode: str, preset_name: str) -> Path:
    return _presets_dir() / mode / ".history" / _safe_name(preset_name)


def _draft_path_for(mode: str, draft_key: str) -> Path:
    return _presets_dir() / mode / ".drafts" / f"{_safe_name(draft_key)}.json"


def backup_preset_before_save(mode: str, name: str) -> Path | None:
    """Copy the current preset to a timestamped backup before overwriting."""
    d = _ensure_dir(mode)
    path = d / f"{_safe_name(name)}.json"
    if not path.exists():
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = d / f"{_safe_name(name)}.json.bak.{ts}"
    shutil.copy2(str(path), str(backup))
    return backup


def save_preset(mode: str, name: str, data: dict[str, Any]) -> Path:
    """Save a preset under ``data/.presets/<mode>/<name>.json``."""
    d = _ensure_dir(mode)
    path = d / f"{_safe_name(name)}.json"
    payload = sanitize_preset_payload(mode, data)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return path


def save_ai_input_draft(mode: str, draft_key: str, data: dict[str, Any]) -> Path:
    """Persist an in-progress AI prompt/polish dialog draft."""
    path = _draft_path_for(mode, draft_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"),
        "data": data,
    }
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return path


def load_ai_input_draft(mode: str, draft_key: str) -> dict[str, Any]:
    """Load a saved AI prompt/polish dialog draft, returning an empty dict if absent."""
    path = _draft_path_for(mode, draft_key)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    data = raw.get("data", {}) if isinstance(raw, dict) else {}
    return data if isinstance(data, dict) else {}


def delete_ai_input_draft(mode: str, draft_key: str) -> bool:
    """Delete a saved AI prompt/polish dialog draft."""
    path = _draft_path_for(mode, draft_key)
    if path.exists():
        path.unlink()
        return True
    return False


def save_polish_history(
    mode: str,
    preset_name: str,
    *,
    operation: str,
    data: dict[str, Any],
    hint: str = "",
    suggestions: list[str] | None = None,
    focus_fields: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """Save a snapshot of AI generate/polish operation to per-preset history."""
    history = _history_dir_for(mode, preset_name)
    history.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = history / f"{ts}_{operation}.json"

    entry: dict[str, Any] = {
        "timestamp": ts,
        "operation": operation,
        "data": sanitize_preset_payload(mode, data),
    }
    if hint:
        entry["hint"] = hint
    if suggestions:
        entry["selected_suggestions"] = suggestions
    if focus_fields:
        entry["focus_fields"] = focus_fields
    if metadata:
        entry["metadata"] = metadata

    atomic_write_text(path, json.dumps(entry, ensure_ascii=False, indent=2))

    _prune_history(history)
    return path


def load_polish_history(mode: str, preset_name: str) -> list[dict[str, Any]]:
    """Load all history entries for a preset, newest first."""
    history = _history_dir_for(mode, preset_name)
    if not history.exists():
        return []
    entries: list[dict[str, Any]] = []
    for p in sorted(history.glob("*.json"), reverse=True):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                raw["_file"] = p.name
                entries.append(raw)
        except (json.JSONDecodeError, OSError):
            continue
    return entries


def delete_polish_history(mode: str, preset_name: str, filename: str) -> bool:
    """Delete a single history entry file."""
    path = _history_dir_for(mode, preset_name) / filename
    if path.exists():
        path.unlink()
        return True
    return False


def save_polish_suggestions(mode: str, preset_name: str, suggestions: list[str]) -> None:
    """Persist polish suggestions for a preset."""
    if not suggestions:
        return
    history = _history_dir_for(mode, preset_name)
    history.mkdir(parents=True, exist_ok=True)
    path = history / "_suggestions.json"
    atomic_write_text(
        path,
        json.dumps({"suggestions": suggestions}, ensure_ascii=False, indent=2),
    )


def load_polish_suggestions(mode: str, preset_name: str) -> list[str]:
    """Restore persisted polish suggestions for a preset."""
    path = _history_dir_for(mode, preset_name) / "_suggestions.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        suggestions = raw.get("suggestions", []) if isinstance(raw, dict) else []
        return [str(s) for s in suggestions if s]
    except (json.JSONDecodeError, OSError):
        return []


def clear_history_for_preset(mode: str, preset_name: str) -> int:
    """Remove all history and suggestions for a preset. Returns count of deleted files."""
    history = _history_dir_for(mode, preset_name)
    if not history.exists():
        return 0
    count = 0
    for p in history.iterdir():
        if p.is_file():
            p.unlink()
            count += 1
    return count


def _prune_history(history_dir: Path) -> None:
    """Keep only the latest _MAX_HISTORY_ENTRIES snapshots."""
    files = sorted(history_dir.glob("[0-9]*_*.json"), reverse=True)
    for stale in files[_MAX_HISTORY_ENTRIES:]:
        stale.unlink()


def list_presets(mode: str) -> list[str]:
    """Return sorted list of preset names for a mode."""
    d = _presets_dir() / mode
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.json"))


def load_preset(mode: str, name: str) -> dict[str, Any]:
    """Load a preset by name, raises FileNotFoundError if missing."""
    d = _presets_dir() / mode
    path = d / f"{_safe_name(name)}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return sanitize_preset_payload(mode, raw)


def delete_preset(mode: str, name: str) -> bool:
    """Delete a preset. Returns True if deleted."""
    d = _presets_dir() / mode
    path = d / f"{_safe_name(name)}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def export_preset_json(path: str | Path, mode: str, data: dict[str, Any] | None = None) -> Path:
    """Export current preset payload or a built-in template as plain JSON."""
    export_path = Path(path)
    payload = preset_template(mode) if data is None else sanitize_preset_payload(mode, data)
    atomic_write_text(export_path, json.dumps(payload, ensure_ascii=False, indent=2))
    return export_path


def import_from_cli_json(
    cli_json_path: str | Path,
    *,
    preferred_mode: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Parse a CLI config, exported preset, or mode-wrapped preset JSON.

    ``preferred_mode`` resolves a plain payload that has no mode discriminator.
    Exported preset files always contain one, so an accidental short/long
    cross-import is still rejected instead of silently filling defaults.
    """
    path = Path(cli_json_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("preset JSON root must be an object")
    if preferred_mode is not None and preferred_mode not in _MODE_TEMPLATES:
        raise ValueError(f"Unsupported preset mode: {preferred_mode}")

    result: dict[str, dict[str, Any]] = {}
    if isinstance(raw.get("run_short"), dict):
        result["short"] = _extract_short(raw["run_short"])
    if isinstance(raw.get("init_long"), dict):
        result["long"] = _extract_long(raw["init_long"])
    if isinstance(raw.get("short"), dict):
        result["short"] = sanitize_preset_payload("short", raw["short"])
    if isinstance(raw.get("long"), dict):
        result["long"] = sanitize_preset_payload("long", raw["long"])
    if result:
        return result

    inferred_mode = _infer_plain_preset_mode(raw)
    if inferred_mode is None:
        inferred_mode = preferred_mode
    if inferred_mode is None:
        raise ValueError("cannot determine whether this is a short or long preset")
    if preferred_mode is not None and inferred_mode != preferred_mode:
        return {inferred_mode: sanitize_preset_payload(inferred_mode, raw)}
    result[inferred_mode] = sanitize_preset_payload(inferred_mode, raw)
    return result


def _infer_plain_preset_mode(data: dict[str, Any]) -> str | None:
    short_markers = {"theme", "length_target", "max_edit_rounds", "writing_mode"}
    long_markers = {
        "premise",
        "total_chapters",
        "words_per_chapter",
        "volume_mode",
    }
    looks_short = bool(short_markers.intersection(data))
    looks_long = bool(long_markers.intersection(data))
    if looks_short and looks_long:
        raise ValueError("preset JSON mixes short and long mode fields")
    if looks_short:
        return "short"
    if looks_long:
        return "long"
    return None


def _extract_short(block: dict[str, Any]) -> dict[str, Any]:
    return sanitize_preset_payload("short", {
        "theme": block.get("theme", ""),
        "genre": block.get("genre", DEFAULT_GENRE),
        "tone": block.get("tone", DEFAULT_TONE),
        "length_target": block.get("length", 3200),
        "max_edit_rounds": block.get("edit_rounds", 2),
        "segment_trigger_words": 5500,
        "writing_mode": block.get("writing_mode", "auto"),
        "title": block.get("title", ""),
        "language": block.get("language", "zh"),
        "characters_hint": block.get("characters_hint", ""),
        "world_hint": block.get("world_hint", ""),
        "conflict_hint": block.get("conflict_hint", ""),
        "pov_hint": block.get("pov_hint", ""),
        "opening_style": block.get("opening_style", ""),
        "ending_style": block.get("ending_style", ""),
        "extra_instructions": block.get("extra_instructions", ""),
        "project_id": block.get("project_id", ""),
        "research_enabled": block.get("research_enabled", False),
        "research_provider": block.get("research_provider", "auto"),
        "research_query_hint": block.get("research_query_hint", ""),
        "blueprint_element_preferences": {
            "preset_id": "",
            "manual_override": False,
            "items": [],
        },
    })


def _extract_long(block: dict[str, Any]) -> dict[str, Any]:
    return sanitize_preset_payload("long", {
        "premise": block.get("premise", ""),
        "genre": block.get("genre", DEFAULT_GENRE),
        "tone": block.get("tone", DEFAULT_TONE),
        "total_chapters": block.get("total_chapters", 24),
        "words_per_chapter": block.get("words_per_chapter", 4500),
        "volume_mode": block.get("volume_mode", "auto"),
        "chapters_per_volume": block.get("chapters_per_volume", 0),
        "title": block.get("title", ""),
        "language": block.get("language", "zh"),
        "characters_hint": block.get("characters_hint", ""),
        "world_hint": block.get("world_hint", ""),
        "conflict_hint": block.get("conflict_hint", ""),
        "pov_hint": block.get("pov_hint", ""),
        "opening_style": block.get("opening_style", ""),
        "ending_style": block.get("ending_style", ""),
        "extra_instructions": block.get("extra_instructions", ""),
        "polish_hint": block.get("polish_hint", ""),
        "project_id": block.get("project_id", ""),
        "research_enabled": block.get("research_enabled", False),
        "research_provider": block.get("research_provider", "auto"),
        "research_query_hint": block.get("research_query_hint", ""),
        "blueprint_element_preferences": {
            "preset_id": "",
            "manual_override": False,
            "items": [],
        },
    })


# ── Short-form field list (for collect/fill) ──────────────────────
SHORT_FIELDS = [
    "theme", "genre", "tone", "length_target", "max_edit_rounds",
    "segment_trigger_words", "writing_mode",
    "title", "language", "characters_hint", "world_hint", "conflict_hint",
    "pov_hint", "opening_style", "ending_style", "extra_instructions", "project_id",
    "research_enabled",
    "research_provider",
    "research_query_hint",
    "blueprint_element_preferences",
]

LONG_FIELDS = [
    "premise", "genre", "tone", "total_chapters", "words_per_chapter",
    "volume_mode", "chapters_per_volume", "title", "language",
    "characters_hint", "world_hint", "conflict_hint",
    "pov_hint", "opening_style", "ending_style", "extra_instructions", "polish_hint", "project_id",
    "research_enabled", "research_provider", "research_query_hint",
    "blueprint_element_preferences",
]
