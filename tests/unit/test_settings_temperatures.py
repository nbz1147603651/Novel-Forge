"""Tests for temperature settings loaded from environment."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from novel_forge.core.config import Settings
from novel_forge.core.task_catalog import (
    LONG_TEMPERATURE_TASKS,
    ROUTING_GROUPS,
    SHORT_TEMPERATURE_TASKS,
)


def test_temperature_defaults_are_in_valid_range(monkeypatch: pytest.MonkeyPatch) -> None:
    for task in (*SHORT_TEMPERATURE_TASKS, *LONG_TEMPERATURE_TASKS):
        monkeypatch.delenv(f"NOVEL_FORGE_{task.setting_attr.upper()}", raising=False)
    for key in (
        "NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_ENABLED",
        "NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_UP_DELTA",
        "NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_DOWN_DELTA",
        "NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_SCOPE",
        "NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_CUSTOM_TASKS",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = Settings(_env_file=None)
    values = [
        settings.temp_spec_enrich,
        settings.temp_beats,
        settings.temp_draft,
        settings.temp_edit,
        settings.temp_evaluate,
        settings.temp_init_story_bible,
        settings.temp_init_character_bible,
        settings.temp_init_entity_registry,
        settings.temp_init_narrative_contract,
        settings.temp_plan_chapter_contracts,
        settings.temp_adjudicate_contract_coherence,
        settings.temp_plan_outline,
        settings.temp_plan_chapter,
        settings.temp_draft_chapter,
        settings.temp_wave_chapter,
        settings.temp_edit_chapter,
        settings.temp_extract_canon,
        settings.temp_check_alignment,
        settings.temp_check_chapter,
        settings.temp_repair_reading_power,
        settings.temp_validate_causal,
        settings.temp_volume_audit,
        settings.temp_enrich_character,
        settings.temp_adjudicate_character_introduction,
        settings.temp_generate_config,
        settings.temp_polish_config,
        settings.temp_adjust_outline,
        settings.temp_profile_structure,
        settings.creative_temperature_jitter_up_delta,
        settings.creative_temperature_jitter_down_delta,
    ]
    assert all(0.0 <= temp <= 2.0 for temp in values)
    assert settings.creative_temperature_jitter_enabled is False
    assert settings.creative_temperature_jitter_scope == "recommended"
    assert settings.creative_temperature_jitter_custom_tasks == ""


def test_repair_must_fix_severity_default_is_critical() -> None:
    settings = Settings(_env_file=None)
    assert settings.repair_must_fix_severity == "critical"


def test_repair_control_mode_default_and_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOVEL_FORGE_REPAIR_CONTROL_MODE", raising=False)
    settings = Settings(_env_file=None)
    assert settings.repair_control_mode == "ai_assisted"

    monkeypatch.setenv("NOVEL_FORGE_REPAIR_CONTROL_MODE", "manual")
    assert Settings(_env_file=None).repair_control_mode == "manual"

    monkeypatch.setenv("NOVEL_FORGE_REPAIR_CONTROL_MODE", "ai_auto")
    assert Settings(_env_file=None).repair_control_mode == "ai_auto"


def test_init_outline_density_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.init_outline_beats_min == 4
    assert settings.init_outline_beats_max == 8
    assert settings.init_outline_main_plot_points_min == 2
    assert settings.init_outline_main_plot_points_max == 4
    assert settings.init_outline_subplot_points_max == 3
    assert settings.init_outline_element_focus_max == 3
    assert settings.init_outline_expected_payoffs_min == 1
    assert settings.init_outline_expected_payoffs_max == 3


def test_temperature_task_catalog_keys_exist_on_settings() -> None:
    settings = Settings(_env_file=None)

    for task in (*SHORT_TEMPERATURE_TASKS, *LONG_TEMPERATURE_TASKS):
        assert hasattr(settings, task.setting_attr), task.setting_attr


def test_routed_temperature_tasks_are_cataloged() -> None:
    routed_keys = {task.key for group in ROUTING_GROUPS for task in group.tasks}
    temperature_keys = {task.key for task in (*SHORT_TEMPERATURE_TASKS, *LONG_TEMPERATURE_TASKS)}
    # TTS tasks use routing but don't need temperature controls
    separate_controls = {
        "element_progress_arbiter",
        "tts_generate_dubbing_script",
        "tts_adjudicate_script_segments",
        "tts_review_dubbing_script",
        "tts_adjudicate_voice_match",
        "tts_build_narrator_profile",
        "tts_sound_design",
    }

    assert routed_keys - temperature_keys == separate_controls


def test_temperature_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVEL_FORGE_TEMP_DRAFT_CHAPTER", "0.91")
    monkeypatch.setenv("NOVEL_FORGE_TEMP_WAVE_CHAPTER", "0.73")
    monkeypatch.setenv("NOVEL_FORGE_TEMP_CHECK_ALIGNMENT", "0.15")
    monkeypatch.setenv("NOVEL_FORGE_TEMP_CHECK_CHAPTER", "0.22")
    monkeypatch.setenv("NOVEL_FORGE_TEMP_REPAIR_READING_POWER", "0.33")
    monkeypatch.setenv("NOVEL_FORGE_TEMP_VALIDATE_CAUSAL", "0.24")
    monkeypatch.setenv("NOVEL_FORGE_TEMP_INIT_KNOWLEDGE_BOUNDARIES", "0.31")
    monkeypatch.setenv("NOVEL_FORGE_TEMP_REPAIR_KNOWLEDGE_BOUNDARY", "0.21")
    monkeypatch.setenv("NOVEL_FORGE_TEMP_EXTRACT_KNOWLEDGE_DELTAS", "0.16")
    monkeypatch.setenv("NOVEL_FORGE_TEMP_GENERATE_CONFIG", "0.92")
    monkeypatch.setenv("NOVEL_FORGE_TEMP_POLISH_CONFIG", "0.64")
    monkeypatch.setenv("NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_ENABLED", "true")
    monkeypatch.setenv("NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_UP_DELTA", "0.08")
    monkeypatch.setenv("NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_DOWN_DELTA", "0.28")
    monkeypatch.setenv("NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_SCOPE", "chapter_core")
    monkeypatch.setenv(
        "NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_CUSTOM_TASKS",
        "draft_chapter,edit_chapter",
    )
    settings = Settings()
    assert settings.temp_draft_chapter == pytest.approx(0.91)
    assert settings.temp_wave_chapter == pytest.approx(0.73)
    assert settings.temp_check_alignment == pytest.approx(0.15)
    assert settings.temp_check_chapter == pytest.approx(0.22)
    assert settings.temp_repair_reading_power == pytest.approx(0.33)
    assert settings.temp_validate_causal == pytest.approx(0.24)
    assert settings.temp_init_knowledge_boundaries == pytest.approx(0.31)
    assert settings.temp_repair_knowledge_boundary == pytest.approx(0.21)
    assert settings.temp_extract_knowledge_deltas == pytest.approx(0.16)
    assert settings.temp_generate_config == pytest.approx(0.92)
    assert settings.temp_polish_config == pytest.approx(0.64)
    assert settings.creative_temperature_jitter_enabled is True
    assert settings.creative_temperature_jitter_up_delta == pytest.approx(0.08)
    assert settings.creative_temperature_jitter_down_delta == pytest.approx(0.28)
    assert settings.creative_temperature_jitter_scope == "chapter_core"
    assert settings.creative_temperature_jitter_custom_tasks == "draft_chapter,edit_chapter"


def test_temperature_env_out_of_range_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVEL_FORGE_TEMP_DRAFT", "2.5")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.delenv("NOVEL_FORGE_TEMP_DRAFT", raising=False)
