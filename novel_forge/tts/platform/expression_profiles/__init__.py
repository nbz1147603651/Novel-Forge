"""Expression profile loader with mtime-based hot-reload caching.

Loads per-provider JSON expression profiles from this directory.
Falls back to ``default.json`` when a provider-specific file is absent
or fails to parse.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from novel_forge.obs.logger import get_logger

_log = get_logger("tts.platform.expression_profiles")

_PROFILES_DIR = Path(__file__).resolve().parent

# Cache: provider_id -> (mtime_ns, parsed_dict)
_cache: dict[str, tuple[int, dict[str, Any]]] = {}

# Provider alias normalization — mirrors rewrite_profiles so that the
# DashScope compatibility enum value resolves to the same Bailian profile.
_PROVIDER_ALIASES: dict[str, str] = {
    "dashscope": "bailian",
    "qwen3": "bailian",
    "cosyvoice": "bailian",
}


def _normalize_provider(provider_id: str) -> str:
    """Normalize a provider ID and resolve aliases."""
    normalized = provider_id.strip().lower().replace("-", "_")
    return _PROVIDER_ALIASES.get(normalized, normalized)


def _load_json(path: Path) -> dict[str, Any] | None:
    """Load and parse a JSON file, returning None on failure."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
        _log.warning("Expression profile %s is not a JSON object", path.name)
        return None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        _log.warning("Failed to load expression profile %s: %s", path.name, exc)
        return None


def load_profile_dict(provider_id: str) -> dict[str, Any]:
    """Load the raw profile dict for a provider, with mtime caching.

    Resolution order:
    1. ``{provider_id}.json`` in this directory
    2. ``default.json`` fallback

    Returns an empty-ish default dict if nothing loads.
    """
    normalized = _normalize_provider(provider_id)
    cache_key = normalized or "default"

    # Determine target file
    target = _PROFILES_DIR / f"{normalized}.json"
    if not target.is_file():
        target = _PROFILES_DIR / "default.json"

    # Check mtime cache
    try:
        mtime_ns = os.stat(target).st_mtime_ns
    except OSError:
        mtime_ns = 0

    cached = _cache.get(cache_key)
    if cached is not None and cached[0] == mtime_ns:
        return cached[1]

    # Load fresh
    data = _load_json(target)
    if data is None:
        # Ultimate fallback: minimal valid structure
        data = {
            "provider_id": "default",
            "profile_version": "1.0",
            "emotion_compensation": {},
            "tone_hint_rules": [],
            "vocal_direction_bridge": {"enabled": False},
            "expression_scale": {
                "extreme_short": 0.0,
                "short_base": 0.10,
                "short_intensity_factor": 0.20,
                "normal_base": 0.15,
                "normal_intensity_factor": 0.35,
            },
            "sub_emotion_modulation": {"energy_deltas": {}, "tension_deltas": {}},
        }

    _cache[cache_key] = (mtime_ns, data)
    return data


def invalidate_cache(provider_id: str | None = None) -> None:
    """Clear cached profiles. If provider_id is None, clear all."""
    if provider_id is None:
        _cache.clear()
    else:
        _cache.pop(_normalize_provider(provider_id), None)


__all__ = ["load_profile_dict", "invalidate_cache"]
