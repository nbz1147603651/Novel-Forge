"""Film platform depth-adaptation profile loader with mtime-based hot reload.

Mirrors :mod:`novel_forge.tts.platform.rewrite_profiles`: per-provider JSON
files with ``default.json`` fallback and deep-merge semantics.  Adding a new
visual platform = adding a new JSON file; no code changes required.

Profiles describe the *prompt-level* platform contract for 映界 generation:
character budgets, structure conventions, camera-vocabulary mappings,
negative-prompt policy and capability switches.  ``structure.kind`` selects
the prompt structure renderer in :mod:`novel_forge.film.platform_adapter`
(``flat`` by default; ``h3_timeline`` implements the MiniMax H3 Context-IR
timeline format).  The canonical capability catalog stays in
:mod:`novel_forge.film.providers.catalog`; profiles only carry the
text-projection rules layered on top of it.

Author: novel-forge
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from novel_forge.obs.logger import get_logger

_log = get_logger("film.platform_profiles")

_PROFILES_DIR = Path(__file__).resolve().parent

# Cache: provider_key -> (mtime_ns, merged_dict)
_cache: dict[str, tuple[int, dict[str, Any]]] = {}

# Provider alias normalization (multiple IDs → same profile file)
_PROVIDER_ALIASES: dict[str, str] = {
    "dashscope": "bailian",
    "volcengine": "volcengine_ark",
    "ark": "volcengine_ark",
    "doubao": "volcengine_ark",
    "hailuo": "minimax",
}

# Hardcoded minimal fallback when no JSON file loads at all.
_MINIMAL_DEFAULT: dict[str, Any] = {
    "provider_id": "default",
    "profile_version": "1.0",
    "display_name": "通用视觉生成平台",
    "char_budget": {"prompt_zh": 800, "negative_zh": 200},
    "structure": {
        "kind": "flat",
        "section_order": ["subject", "action", "camera", "lighting", "style"],
        "guidance": "按 主体→动作→镜头→光线→风格 顺序组织，供应商无关的自然语言描述。",
    },
    "camera_vocabulary": {},
    "negative_policy": {
        "required_terms": [],
        "forbidden_terms": [],
        "guidance": "负面提示只写画面缺陷与合规红线，不写叙事内容。",
    },
    "capability_switches": {
        "multi_shot": False,
        "native_audio": False,
        "multi_image_reference": False,
        "camera_command": False,
        "trusted_actor_asset": False,
    },
    "platform_prompt_guidance": "只使用供应商无关字段，不写入任何厂商私有标记。",
    "special_rules": [],
}


def _normalize_provider(provider_id: str) -> str:
    """Normalize a provider ID and resolve aliases."""
    normalized = str(provider_id or "").strip().lower().replace("-", "_")
    return _PROVIDER_ALIASES.get(normalized, normalized)


def _load_json(path: Path) -> dict[str, Any] | None:
    """Load and parse a JSON file, returning None on failure."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
        _log.warning("Film platform profile %s is not a JSON object", path.name)
        return None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        _log.warning("Failed to load film platform profile %s: %s", path.name, exc)
        return None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge override into base with platform-specific semantics.

    - Scalar fields: override replaces base.
    - Dict fields: per-key merge (missing keys retain base value).
    - List fields: override replaces base entirely (no append).
    """
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_platform_profile(provider_id: str) -> dict[str, Any]:
    """Load the film platform profile dict for a provider, with mtime caching.

    Resolution order:
    1. ``{normalized_provider}.json`` in this directory
    2. ``default.json`` fallback (merged as base)
    3. Hardcoded minimal structure

    Platform files only need to contain override fields; missing fields are
    deep-merged from default.json automatically.
    """
    normalized = _normalize_provider(provider_id)
    cache_key = normalized or "default"

    target = _PROFILES_DIR / f"{normalized}.json" if normalized else Path()
    if not normalized or not target.is_file():
        target = _PROFILES_DIR / "default.json"

    try:
        mtime_ns = os.stat(target).st_mtime_ns
    except OSError:
        mtime_ns = 0

    cached = _cache.get(cache_key)
    if cached is not None and cached[0] == mtime_ns:
        return cached[1]

    default_path = _PROFILES_DIR / "default.json"
    base = _load_json(default_path)
    if base is None:
        base = json.loads(json.dumps(_MINIMAL_DEFAULT))

    if target != default_path:
        override = _load_json(target)
        data = _deep_merge(base, override) if override is not None else base
    else:
        data = base

    _cache[cache_key] = (mtime_ns, data)
    return data


def invalidate_platform_profile_cache(provider_id: str | None = None) -> None:
    """Clear cached profiles. If provider_id is None, clear all."""
    if provider_id is None:
        _cache.clear()
    else:
        _cache.pop(_normalize_provider(provider_id), None)


__all__ = [
    "invalidate_platform_profile_cache",
    "load_platform_profile",
]
