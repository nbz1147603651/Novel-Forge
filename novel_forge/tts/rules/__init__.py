"""Externalized TTS rule loading with built-in fallback.

Author: novel-forge
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novel_forge.obs.logger import get_logger

_log = get_logger("tts.rules")

_RULES_DIR = Path(__file__).parent


@dataclass(frozen=True)
class EventSoundRule:
    """A single event-to-sound-effect mapping rule."""

    effect_name: str
    pattern: re.Pattern[str]
    description: str
    duration_ms: int
    volume: float
    priority: int = 95


def load_event_sound_rules() -> tuple[EventSoundRule, ...]:
    """Load event sound rules from JSON, falling back to empty on failure.

    Author: novel-forge
    """
    path = _RULES_DIR / "event_sound_rules.json"
    try:
        raw: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
        rules = []
        for item in raw:
            rules.append(
                EventSoundRule(
                    effect_name=str(item["effect_name"]),
                    pattern=re.compile(str(item["pattern"])),
                    description=str(item.get("description", "")),
                    duration_ms=int(item.get("duration_ms", 1000)),
                    volume=float(item.get("volume", 0.4)),
                    priority=int(item.get("priority", 95)),
                )
            )
        return tuple(rules)
    except Exception as exc:
        _log.warning("Failed to load event_sound_rules.json, using empty rules: %s", exc)
        return ()


def load_environment_keywords() -> dict[str, list[str]]:
    """Load environment keyword mappings from JSON.

    Author: novel-forge
    """
    path = _RULES_DIR / "environment_keywords.json"
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): [str(v) for v in vs] for k, vs in data.get("environment_keywords", {}).items()}
    except Exception as exc:
        _log.warning("Failed to load environment_keywords.json: %s", exc)
        return {}


def load_emotion_to_mood_tags() -> dict[str, list[str]]:
    """Load emotion-to-mood-tag mappings from JSON.

    Author: novel-forge
    """
    path = _RULES_DIR / "environment_keywords.json"
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): [str(v) for v in vs] for k, vs in data.get("emotion_to_mood_tags", {}).items()}
    except Exception as exc:
        _log.warning("Failed to load emotion_to_mood_tags: %s", exc)
        return {}


def load_emotion_to_narrative_role() -> dict[str, str]:
    """Load emotion-to-narrative-role mappings from JSON.

    Author: novel-forge
    """
    path = _RULES_DIR / "environment_keywords.json"
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.get("emotion_to_narrative_role", {}).items()}
    except Exception as exc:
        _log.warning("Failed to load emotion_to_narrative_role: %s", exc)
        return {}


@dataclass(frozen=True)
class VocalDirectionPreset:
    """Per-emotion vocal direction 6D parameters."""

    energy: float
    articulation: float
    breathiness: float
    tension: float
    intimacy: float
    delivery_style: str


@dataclass(frozen=True)
class NarratorDistancePreset:
    """Per-distance narrator vocal overrides."""

    intimacy: float
    breathiness: float
    delivery_style: str


@dataclass(frozen=True)
class VocalDirectionConfig:
    """Complete vocal direction configuration loaded from JSON."""

    emotion_presets: dict[str, VocalDirectionPreset]
    narrator_distance_presets: dict[str, NarratorDistancePreset]
    intensity_scale_multiplier: float
    intensity_max_delta: float


_vocal_direction_config_cache: VocalDirectionConfig | None = None


def load_vocal_direction_config() -> VocalDirectionConfig:
    """Load vocal direction presets from JSON with mtime-based caching.

    Author: novel-forge
    """
    global _vocal_direction_config_cache  # noqa: PLW0603
    if _vocal_direction_config_cache is not None:
        return _vocal_direction_config_cache

    path = _RULES_DIR / "vocal_direction_presets.json"
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _log.warning("Failed to load vocal_direction_presets.json: %s", exc)
        # Fallback: neutral-only preset
        neutral = VocalDirectionPreset(
            energy=0.5, articulation=0.6, breathiness=0.2,
            tension=0.3, intimacy=0.5, delivery_style="natural",
        )
        _vocal_direction_config_cache = VocalDirectionConfig(
            emotion_presets={"neutral": neutral},
            narrator_distance_presets={},
            intensity_scale_multiplier=0.4,
            intensity_max_delta=0.15,
        )
        return _vocal_direction_config_cache

    emotion_presets: dict[str, VocalDirectionPreset] = {}
    for key, val in data.get("emotion_presets", {}).items():
        if isinstance(val, dict):
            emotion_presets[str(key)] = VocalDirectionPreset(
                energy=float(val.get("energy", 0.5)),
                articulation=float(val.get("articulation", 0.6)),
                breathiness=float(val.get("breathiness", 0.2)),
                tension=float(val.get("tension", 0.3)),
                intimacy=float(val.get("intimacy", 0.5)),
                delivery_style=str(val.get("delivery_style", "natural")),
            )

    narrator_presets: dict[str, NarratorDistancePreset] = {}
    for key, val in data.get("narrator_distance_presets", {}).items():
        if isinstance(val, dict):
            narrator_presets[str(key)] = NarratorDistancePreset(
                intimacy=float(val.get("intimacy", 0.5)),
                breathiness=float(val.get("breathiness", 0.2)),
                delivery_style=str(val.get("delivery_style", "narrative")),
            )

    intensity_cfg = data.get("intensity_scaling", {})
    _vocal_direction_config_cache = VocalDirectionConfig(
        emotion_presets=emotion_presets,
        narrator_distance_presets=narrator_presets,
        intensity_scale_multiplier=float(intensity_cfg.get("scale_multiplier", 0.4)),
        intensity_max_delta=float(intensity_cfg.get("max_delta", 0.15)),
    )
    return _vocal_direction_config_cache
