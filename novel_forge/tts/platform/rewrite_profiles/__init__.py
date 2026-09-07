"""Spoken-text rewrite profile loader with mtime-based hot-reload caching.

Mirrors the expression_profiles pattern: per-provider JSON files with
default.json fallback.  Adding a new platform = adding a new JSON file.

Author: novel-forge
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from novel_forge.obs.logger import get_logger

_log = get_logger("tts.platform.rewrite_profiles")

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
    "sentence_limits": {"narration": 30, "dialogue": 20, "inner_thought": 25},
    "pause_strategy": "在自然意群处断句，单句不超过句长限制；破折号插入语拆为独立短句。",
    "punctuation_guidance": "确保每句有明确标点收束；省略号表达语意未尽时保留。",
    "interjection_policy": "不手动写入任何平台标记；文本结构应允许合成层在情绪转折处注入副语言标签。",
    "rhythm_guidance": "按呼吸和语义分组断句，不为凑长度破坏自然意群。",
    "special_rules": [],
    "polyphone_strategy": "",
    "emotion_vocabulary": {},
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
        _log.warning("Rewrite profile %s is not a JSON object", path.name)
        return None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        _log.warning("Failed to load rewrite profile %s: %s", path.name, exc)
        return None


def load_rewrite_profile(provider_id: str) -> dict[str, Any]:
    """Load the rewrite profile dict for a provider, with mtime caching.

    Resolution order:
    1. ``{normalized_provider}.json`` in this directory
    2. ``default.json`` fallback (merged as base)
    3. Hardcoded minimal structure

    Platform files only need to contain override fields; missing fields
    are deep-merged from default.json automatically.

    Returns a complete profile dict ready for template injection.
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


def invalidate_rewrite_cache(provider_id: str | None = None) -> None:
    """Clear cached profiles. If provider_id is None, clear all."""
    if provider_id is None:
        _cache.clear()
    else:
        _cache.pop(_normalize_provider(provider_id), None)


__all__ = ["invalidate_rewrite_cache", "load_rewrite_profile"]
