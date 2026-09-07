"""Re-export element definitions and genre presets for selection module."""

from __future__ import annotations

from .element_defs import (
    _ELEMENT_LIBRARY,
    _EXTENSION_LIBRARY_IDS,
    _LIBRARY_BY_ID,
    _QUALITY_CONFIG_IDS,
    _REQUIRED_CORE_IDS,
    ELEMENT_LIBRARY_VERSION,
    _ElementDefinition,
    external_element_library_path,
)
from .presets import (
    _GENRE_HEURISTIC_MAP,
    _GENRE_PRESET_BY_ID,
    _GENRE_PRESET_HINT_MAP,
    _GENRE_PRESETS,
    _GenrePresetDefinition,
)


def get_element_library_version() -> str:
    return ELEMENT_LIBRARY_VERSION


def get_extension_library_ids() -> tuple[str, ...]:
    return _EXTENSION_LIBRARY_IDS


def get_quality_config_ids() -> frozenset[str]:
    return _QUALITY_CONFIG_IDS


def get_required_core_ids() -> tuple[str, ...]:
    return _REQUIRED_CORE_IDS


def get_library_by_id() -> dict[str, _ElementDefinition]:
    return _LIBRARY_BY_ID


def get_element_library() -> tuple[_ElementDefinition, ...]:
    return _ELEMENT_LIBRARY


def get_genre_heuristic_map() -> tuple[tuple[tuple[str, ...], tuple[str, ...], float], ...]:
    return _GENRE_HEURISTIC_MAP


def get_genre_preset_hint_map() -> tuple[tuple[tuple[str, ...], str], ...]:
    return _GENRE_PRESET_HINT_MAP


def get_genre_presets() -> tuple[_GenrePresetDefinition, ...]:
    return _GENRE_PRESETS


def get_genre_preset_by_id() -> dict[str, _GenrePresetDefinition]:
    return _GENRE_PRESET_BY_ID


__all__ = [
    "ELEMENT_LIBRARY_VERSION",
    "_ELEMENT_LIBRARY",
    "_LIBRARY_BY_ID",
    "_EXTENSION_LIBRARY_IDS",
    "_QUALITY_CONFIG_IDS",
    "_REQUIRED_CORE_IDS",
    "_ElementDefinition",
    "_GENRE_HEURISTIC_MAP",
    "_GENRE_PRESET_BY_ID",
    "_GENRE_PRESET_HINT_MAP",
    "_GENRE_PRESETS",
    "_GenrePresetDefinition",
    "get_element_library_version",
    "get_extension_library_ids",
    "get_quality_config_ids",
    "get_required_core_ids",
    "get_library_by_id",
    "get_element_library",
    "external_element_library_path",
    "get_genre_heuristic_map",
    "get_genre_preset_hint_map",
    "get_genre_presets",
    "get_genre_preset_by_id",
]
