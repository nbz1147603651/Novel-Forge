"""Backward-compat shim for novel_forge.desktop.pages.settings.parameters.

The original 3355-line settings_page_parameters.py was decomposed into a
subpackage in the M3.6 refactor. This shim keeps existing imports of the
form 'from novel_forge.desktop.pages.settings_page_parameters import X'
working.
"""

from __future__ import annotations

from novel_forge.desktop.pages.settings.parameters import (  # noqa: F401
    _add_init_coherence_controls,
    _add_init_protocol_controls,
    _append_compact_button,
    _build_debug_section,
    _build_desktop_notification_params,
    _build_long_context_params,
    _build_long_creation_params,
    _build_long_initialization_params,
    _build_long_temp_params,
    _build_memory_params,
    _build_ollama_params,
    _build_quality_audit_params,
    _build_reading_power_params,
    _build_research_params,
    _build_short_params,
    _build_storage_params,
    _build_temperature_jitter_params,
    _build_theme_params,
    _build_tts_params,
    _make_model_combo_setting,
    _make_settings_subsection,
    collect_temperature_jitter_custom_tasks,
    research_preset_payload,
)

__all__ = [
    "_add_init_coherence_controls",
    "_add_init_protocol_controls",
    "_append_compact_button",
    "_build_debug_section",
    "_build_desktop_notification_params",
    "_build_long_context_params",
    "_build_long_creation_params",
    "_build_long_initialization_params",
    "_build_long_temp_params",
    "_build_memory_params",
    "_build_ollama_params",
    "_build_quality_audit_params",
    "_build_reading_power_params",
    "_build_research_params",
    "_build_short_params",
    "_build_storage_params",
    "_build_temperature_jitter_params",
    "_build_theme_params",
    "_build_tts_params",
    "_make_model_combo_setting",
    "_make_settings_subsection",
    "collect_temperature_jitter_custom_tasks",
    "research_preset_payload",
]
