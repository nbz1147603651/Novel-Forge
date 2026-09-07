"""Helpers for reading style profile config from dicts or schema objects."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any


def get_profile_section(profile: Any, section_name: str) -> Any:
    """Return one section from a style profile stored as dict or object."""
    if profile is None:
        return None
    if isinstance(profile, dict):
        return profile.get(section_name)
    return getattr(profile, section_name, None)


def get_section_value(section: Any, field_name: str, default: Any = None) -> Any:
    """Return a field from a nested config section stored as dict or object."""
    if section is None:
        return default
    if isinstance(section, dict):
        return section.get(field_name, default)
    return getattr(section, field_name, default)


def section_to_dict(section: Any) -> dict[str, Any] | None:
    """Convert a nested config section to a JSON-safe dict when possible."""
    if section is None:
        return None
    if isinstance(section, dict):
        return dict(section)
    if hasattr(section, "model_dump"):
        dumped = section.model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else None
    if is_dataclass(section):
        dumped = asdict(section)  # type: ignore[arg-type]
        return dumped if isinstance(dumped, dict) else None
    return None


def merge_style_profile_overrides(raw: Any) -> dict[str, Any] | None:
    """Return a shallowly merged style profile payload with user overrides applied.

    ``style_profile.json`` is persisted as plain JSON, and repair paths already
    allow an ``overrides`` section that replaces top-level keys or shallowly
    merges nested dict sections. Keeping that merge here gives long chapter
    generation the same effective profile as manual repair/polish flows.
    """
    if not isinstance(raw, dict):
        return None
    merged: dict[str, Any] = dict(raw)
    overrides = raw.get("overrides")
    if not isinstance(overrides, dict) or not overrides:
        return merged
    for key, value in overrides.items():
        if not isinstance(key, str) or not key:
            continue
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = {**current, **value}
        else:
            merged[key] = value
    return merged


def build_reading_power_window_config_from_settings(settings: Any) -> Any:
    """Build a ReadingPowerWindowConfig from current settings.

    The config lives inside ``style_profile.json`` so the chapter pipeline can
    initialize the moving-window subsystem without re-reading global settings.
    """
    from novel_forge.core.schemas.reading_power_window_config import (
        ReadingPowerWindowConfig,
    )

    default_weights = {
        "hook_strength": 0.25,
        "payoff_density": 0.20,
        "suspense_timing": 0.20,
        "hook_alternation": 0.15,
        "tension_match": 0.20,
    }
    raw_weights = getattr(settings, "reading_power_hook_strength_weights", default_weights)
    hook_strength_weights = raw_weights if isinstance(raw_weights, dict) else default_weights

    return ReadingPowerWindowConfig(
        window_size=getattr(settings, "reading_power_window_size", 5),
        window_left_offset=getattr(settings, "reading_power_window_left_offset", 0),
        window_right_offset=getattr(settings, "reading_power_window_right_offset", 0),
        suspense_delay_threshold=getattr(settings, "reading_power_suspense_delay_threshold", 3),
        force_resolve_threshold=getattr(settings, "reading_power_force_resolve_threshold", 5),
        hook_alternation_threshold=getattr(settings, "reading_power_hook_alternation_threshold", 2),
        max_consecutive_same_hook=getattr(settings, "reading_power_max_consecutive_same_hook", 3),
        tension_deviation_tolerance=getattr(
            settings,
            "reading_power_tension_deviation_tolerance",
            1.5,
        ),
        tension_recovery_factor=getattr(settings, "reading_power_tension_recovery_factor", 0.3),
        hook_strength_weights=hook_strength_weights,
        payoff_cap=getattr(settings, "reading_power_payoff_cap", 3),
        suspense_timing_weight=getattr(settings, "reading_power_suspense_timing_weight", 0.20),
        enabled=bool(getattr(settings, "reading_power_enabled", True)),
        enable_force_resolve=bool(getattr(settings, "reading_power_enable_force_resolve", True)),
        enable_hook_alternation_check=bool(
            getattr(settings, "reading_power_enable_hook_alternation_check", True)
        ),
        enable_tension_recovery=bool(
            getattr(settings, "reading_power_enable_tension_recovery", True)
        ),
        critical_score_threshold=getattr(settings, "reading_power_critical_score_threshold", 3.0),
        warning_score_threshold=getattr(settings, "reading_power_warning_score_threshold", 5.0),
    )


def get_reading_power_eval_config(
    style_profile: Any,
) -> tuple[int, dict[str, Any] | None]:
    """Extract reading-power eval parameters from the project style profile."""
    min_payoffs = 1
    micro_payoff_config = get_profile_section(style_profile, "micro_payoff_config")
    raw_min_payoffs = get_section_value(micro_payoff_config, "min_per_chapter", min_payoffs)
    try:
        min_payoffs = max(0, int(raw_min_payoffs))
    except (TypeError, ValueError):
        min_payoffs = 1

    hook_score_config = section_to_dict(get_profile_section(style_profile, "hook_score_config"))
    return min_payoffs, hook_score_config


def coerce_reading_power_window_config(style_profile: Any) -> Any | None:
    """Load reading-power window config from dict/object style profile data."""
    rp_window_cfg = get_profile_section(style_profile, "reading_power_window_config")
    if rp_window_cfg is None:
        return None

    from novel_forge.core.schemas.reading_power_window_config import (
        ReadingPowerWindowConfig,
    )

    if isinstance(rp_window_cfg, ReadingPowerWindowConfig):
        return rp_window_cfg
    if isinstance(rp_window_cfg, dict):
        try:
            return ReadingPowerWindowConfig.model_validate(rp_window_cfg)
        except Exception:
            return None
    return rp_window_cfg
