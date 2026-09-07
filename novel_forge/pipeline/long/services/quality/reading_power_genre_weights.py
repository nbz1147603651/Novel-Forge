"""Genre-adaptive weight derivation for Reading Power scoring.

Derives scoring weights based on:
1. StyleProfile.hook_score_config (if custom weights configured)
2. Genre inference from BlueprintElementSelection
3. Default fallback weights by genre

Priority: StyleProfile config > Genre derivation > Default weights
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from novel_forge.core.schemas.blueprint_elements import BlueprintElementSelection
    from novel_forge.core.schemas.style_profile import HookScoreConfig


# Default weights by genre (sum to 1.0)
_GENRE_DEFAULT_WEIGHTS: dict[str, dict[str, float]] = {
    "古代言情": {
        "hook_strength": 0.20,
        "payoff_density": 0.25,
        "information_pacing": 0.20,
        "main_plot_depth": 0.15,
        "tension_match": 0.20,
    },
    "宫斗": {
        "hook_strength": 0.20,
        "payoff_density": 0.25,
        "information_pacing": 0.25,
        "main_plot_depth": 0.15,
        "tension_match": 0.15,
    },
    "悬疑": {
        "hook_strength": 0.20,
        "payoff_density": 0.15,
        "information_pacing": 0.25,
        "main_plot_depth": 0.20,
        "tension_match": 0.20,
    },
    "推理": {
        "hook_strength": 0.20,
        "payoff_density": 0.15,
        "information_pacing": 0.25,
        "main_plot_depth": 0.20,
        "tension_match": 0.20,
    },
    "玄幻": {
        "hook_strength": 0.25,
        "payoff_density": 0.25,
        "information_pacing": 0.15,
        "main_plot_depth": 0.20,
        "tension_match": 0.15,
    },
    "修仙": {
        "hook_strength": 0.25,
        "payoff_density": 0.25,
        "information_pacing": 0.15,
        "main_plot_depth": 0.20,
        "tension_match": 0.15,
    },
    "都市": {
        "hook_strength": 0.20,
        "payoff_density": 0.20,
        "information_pacing": 0.20,
        "main_plot_depth": 0.20,
        "tension_match": 0.20,
    },
    "职场": {
        "hook_strength": 0.20,
        "payoff_density": 0.20,
        "information_pacing": 0.20,
        "main_plot_depth": 0.20,
        "tension_match": 0.20,
    },
    "科幻": {
        "hook_strength": 0.20,
        "payoff_density": 0.20,
        "information_pacing": 0.25,
        "main_plot_depth": 0.20,
        "tension_match": 0.15,
    },
    "武侠": {
        "hook_strength": 0.25,
        "payoff_density": 0.20,
        "information_pacing": 0.15,
        "main_plot_depth": 0.25,
        "tension_match": 0.15,
    },
    # Default fallback
    "default": {
        "hook_strength": 0.25,
        "payoff_density": 0.20,
        "information_pacing": 0.20,
        "main_plot_depth": 0.15,
        "tension_match": 0.20,
    },
}

# Weight adjustments by genre inference tags
_GENRE_INFERENCE_ADJUSTMENTS: dict[str, dict[str, float]] = {
    # Increase information pacing for mystery-focused subgenres
    "悬疑推理": {"information_pacing": 0.05, "main_plot_depth": 0.05},
    "探案": {"information_pacing": 0.05, "main_plot_depth": 0.05},
    # Increase hook strength for fast-paced subgenres
    "快节奏": {"hook_strength": 0.05, "tension_match": 0.05},
    "热血": {"hook_strength": 0.05, "tension_match": 0.05},
    # Increase payoff density for relationship-focused subgenres
    "感情线": {"payoff_density": 0.05, "information_pacing": 0.05},
    "言情": {"payoff_density": 0.05, "information_pacing": 0.05},
    # Increase main plot depth for epic subgenres
    "宏大叙事": {"main_plot_depth": 0.05, "hook_strength": 0.05},
    "史诗": {"main_plot_depth": 0.05, "hook_strength": 0.05},
}


def derive_genre_weights(
    genre: str,
    element_selection: "BlueprintElementSelection | None" = None,
    hook_score_config: "HookScoreConfig | None" = None,
) -> dict[str, float]:
    """Derive genre-adaptive scoring weights.

    Priority:
    1. If hook_score_config has custom weights, use them directly
    2. Otherwise derive from genre + element_selection.genre_inference
    3. Fallback to default weights

    Args:
        genre: Main genre string (e.g., "古代言情", "悬疑")
        element_selection: Blueprint element selection (for genre_inference)
        hook_score_config: StyleProfile hook score config

    Returns:
        Dict with keys: hook_strength, payoff_density, information_pacing,
        main_plot_depth, tension_match (values sum to 1.0)
    """
    # Priority 1: StyleProfile config takes precedence
    if hook_score_config is not None:
        custom_weights = _extract_custom_weights(hook_score_config)
        if custom_weights is not None:
            return custom_weights

    # Priority 2: Genre-based derivation
    weights = _default_weights_for_genre(genre)

    # Secondary adjustment: based on genre_inference tags
    if element_selection is not None and element_selection.genre_inference:
        weights = _adjust_by_inference(weights, element_selection.genre_inference)

    return weights


def _extract_custom_weights(
    hook_score_config: "HookScoreConfig",
) -> dict[str, float] | None:
    """Extract custom weights from HookScoreConfig if available.

    Currently HookScoreConfig only has basic hook scoring parameters.
    If extended in the future with multi-dimensional weights, this function
    will extract them. For now, returns None to fall back to genre derivation.

    Returns:
        Custom weights dict or None if not configured.
    """
    # TODO: When HookScoreConfig is extended with custom_weights field,
    # extract and return them here.
    # Example:
    # if hasattr(hook_score_config, 'custom_weights') and hook_score_config.custom_weights:
    #     return hook_score_config.custom_weights
    return None


def _default_weights_for_genre(genre: str) -> dict[str, float]:
    """Get default weights for a given genre."""
    # Try exact match first
    if genre in _GENRE_DEFAULT_WEIGHTS:
        return dict(_GENRE_DEFAULT_WEIGHTS[genre])

    # Try partial match (e.g., "古代言情，宫斗" matches "古代言情")
    for known_genre, weights in _GENRE_DEFAULT_WEIGHTS.items():
        if known_genre in genre or genre in known_genre:
            return dict(weights)

    # Fallback to default
    return dict(_GENRE_DEFAULT_WEIGHTS["default"])


def _adjust_by_inference(
    base_weights: dict[str, float],
    genre_inference: list[str],
) -> dict[str, float]:
    """Adjust weights based on genre inference tags.

    Args:
        base_weights: Base weights from genre derivation
        genre_inference: List of genre inference tags

    Returns:
        Adjusted weights (normalized to sum to 1.0)
    """
    adjusted = dict(base_weights)

    for tag in genre_inference:
        if tag in _GENRE_INFERENCE_ADJUSTMENTS:
            adjustments = _GENRE_INFERENCE_ADJUSTMENTS[tag]
            for key, delta in adjustments.items():
                if key in adjusted:
                    adjusted[key] += delta

    # Normalize to ensure sum = 1.0
    total = sum(adjusted.values())
    if total > 0:
        adjusted = {k: v / total for k, v in adjusted.items()}

    return adjusted


def get_payoff_type_weights(preferred_types: list[str] | None = None) -> dict[str, float]:
    """Get micro-payoff type weights.

    Args:
        preferred_types: List of preferred payoff types from StyleProfile

    Returns:
        Dict mapping payoff type to weight multiplier
    """
    base_weights = {
        "information": 1.0,
        "relationship": 1.0,
        "ability": 1.0,
        "resource": 0.8,
        "recognition": 0.8,
        "emotion": 1.0,
        "clue": 1.2,  # Mystery/suspense preference
    }

    if not preferred_types:
        return base_weights

    preferred = set(preferred_types)
    adjusted = {}
    for payoff_type, base_weight in base_weights.items():
        if payoff_type in preferred:
            adjusted[payoff_type] = base_weight * 1.3  # Boost preferred types
        else:
            adjusted[payoff_type] = base_weight * 0.7  # Reduce non-preferred types

    return adjusted
