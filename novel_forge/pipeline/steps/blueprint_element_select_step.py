"""Thin re-export for backward compatibility. See blueprint_element_select/ for implementation."""

from __future__ import annotations

from novel_forge.pipeline.steps.blueprint_element_select.core import (
    BlueprintElementSelectInput,
    BlueprintElementSelectStep,
)
from novel_forge.pipeline.steps.blueprint_element_select.elements import (
    ELEMENT_LIBRARY_VERSION,
)
from novel_forge.pipeline.steps.blueprint_element_select.selection import (
    _normalize_selection,
    get_blueprint_element_cards,
    get_blueprint_genre_presets,
    get_related_extension_ids_for_genre,
    has_manual_selector_preferences,
    recommend_preset_for_genre,
)

__all__ = [
    "BlueprintElementSelectInput",
    "BlueprintElementSelectStep",
    "ELEMENT_LIBRARY_VERSION",
    "_normalize_selection",
    "get_blueprint_element_cards",
    "get_blueprint_genre_presets",
    "get_related_extension_ids_for_genre",
    "has_manual_selector_preferences",
    "recommend_preset_for_genre",
]
