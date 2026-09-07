"""Platform-specific voice matching profile loader with mtime-based hot-reload caching.

Mirrors the rewrite_profiles pattern: per-provider JSON files with
default.json fallback.  Adding a new platform = adding a new JSON file.

Author: novel-forge
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from novel_forge.obs.logger import get_logger

_log = get_logger("tts.platform.matching_profiles")

_PROFILES_DIR = Path(__file__).resolve().parent

# Cache: provider_key -> (mtime_ns, merged_dict)
_cache: dict[str, tuple[int, dict[str, Any]]] = {}

# Provider alias normalization (multiple IDs → same profile file)
_PROVIDER_ALIASES: dict[str, str] = {
    "dashscope": "bailian",
    "qwen3": "bailian",
    "cosyvoice": "bailian",
}

# Hardcoded minimal fallback when no JSON file loads at all.
_MINIMAL_DEFAULT: dict[str, Any] = {
    "provider_id": "default",
    "profile_version": "1.0",
    "display_name": "通用TTS平台",
    "scoring_weights": {
        "base": 0.15,
        "language": 0.05,
        "gender": 0.30,
        "age": 0.20,
        "expressive": 0.25,
        "role": 0.10,
        "semantic_blend": 0.35,
    },
    "expressive_group_overrides": {},
    "adjudication_guidance": {
        "platform_context": "",
        "voice_metadata_hint": "候选音色可能包含 personality、voice_description、tags 等元数据。",
        "selection_criteria": "综合比较角色年龄感、声线质地、吐字节奏、身份地位与团队辨识度。",
        "caution_notes": [],
    },
}


def _normalize_provider(provider_id: str) -> str:
    """Normalize a provider ID and resolve aliases."""
    normalized = provider_id.strip().lower().replace("-", "_")
    return _PROVIDER_ALIASES.get(normalized, normalized)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge override into base with platform-specific semantics.

    - Scalar fields: override replaces base.
    - Dict fields: per-key merge (missing keys retain base value).
    - List fields: override replaces base entirely (no append).
    """
    merged = dict(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_json(path: Path) -> dict[str, Any] | None:
    """Load and parse a JSON file, returning None on failure."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
        _log.warning("Matching profile %s is not a JSON object", path.name)
        return None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        _log.warning("Failed to load matching profile %s: %s", path.name, exc)
        return None


def load_matching_profile(provider_id: str) -> dict[str, Any]:
    """Load the matching profile dict for a provider, with mtime caching.

    Resolution order:
    1. ``{normalized_provider}.json`` in this directory
    2. ``default.json`` fallback (merged as base)
    3. Hardcoded minimal structure

    Platform files only need to contain override fields; missing fields
    are deep-merged from default.json automatically.

    Returns a complete profile dict ready for matching/adjudication injection.
    """
    normalized = _normalize_provider(provider_id)
    cache_key = normalized or "default"

    # Determine target file
    target = _PROFILES_DIR / f"{normalized}.json" if normalized else Path()
    if not normalized or not target.is_file():
        target = _PROFILES_DIR / "default.json"

    # Check mtime cache
    try:
        mtime_ns = os.stat(target).st_mtime_ns
    except OSError:
        mtime_ns = 0

    cached = _cache.get(cache_key)
    if cached is not None and cached[0] == mtime_ns:
        return cached[1]

    # Load default base
    default_path = _PROFILES_DIR / "default.json"
    base = _load_json(default_path)
    if base is None:
        base = dict(_MINIMAL_DEFAULT)

    # Load platform override (if different from default)
    if target != default_path:
        override = _load_json(target)
        if override is not None:
            data = _deep_merge(base, override)
        else:
            data = base
    else:
        data = base

    _cache[cache_key] = (mtime_ns, data)
    return data


def invalidate_matching_cache(provider_id: str | None = None) -> None:
    """Clear cached profiles. If provider_id is None, clear all."""
    if provider_id is None:
        _cache.clear()
    else:
        _cache.pop(_normalize_provider(provider_id), None)


__all__ = ["invalidate_matching_cache", "load_matching_profile"]
