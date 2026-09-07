"""Tests for UnifiedSettingsLoader multi-source configuration loading."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from novel_forge.core.infra.settings_loader import (
    UnifiedSettings,
    UnifiedSettingsLoader,
    _coerce_string,
    _parse_env_file,
    _strip_prefix_and_coerce,
)

_ENV_EXAMPLE_ALIGNED_SETTINGS = {
    "LONG_PROMPT_MAX_CHARACTER_PROFILES": "long_prompt_max_character_profiles",
    "CANON_CONTEXT_MAX_CHARACTERS": "canon_context_max_characters",
    "NARRATIVE_STATE_CANDIDATE_MAX_COUNT": "narrative_state_candidate_max_count",
    "NARRATIVE_STATE_PENDING_TAIL_ITEMS": "narrative_state_pending_tail_items",
    "TEMP_PLAN_CHAPTER": "temp_plan_chapter",
    "TEMP_DRAFT_CHAPTER": "temp_draft_chapter",
    "TEMP_WAVE_CHAPTER": "temp_wave_chapter",
}


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_coerce_string_bool() -> None:
    assert _coerce_string("true") is True
    assert _coerce_string("false") is False
    assert _coerce_string("TRUE") is True
    assert _coerce_string("FALSE") is False


def test_coerce_string_int() -> None:
    assert _coerce_string("42") == 42
    assert _coerce_string("0") == 0
    assert _coerce_string("-3") == -3


def test_coerce_string_float() -> None:
    assert _coerce_string("3.14") == 3.14
    assert _coerce_string("0.5") == 0.5


def test_coerce_string_json() -> None:
    assert _coerce_string('{"a": 1}') == {"a": 1}
    assert _coerce_string("[1, 2]") == [1, 2]


def test_merged_settings_kwargs_reserializes_legacy_json_string_fields() -> None:
    from novel_forge.core.config import _merged_to_settings_kwargs

    result = _merged_to_settings_kwargs(
        {
            "task_routing": {"draft_chapter": "openai:gpt-4o-mini"},
            "task_fallback_routing": {"draft_chapter": ["deepseek:deepseek-chat"]},
            "research_mcp_args_json": ["minimax-coding-plan-mcp"],
            "research_mcp_env_json": {"MINIMAX_API_HOST": "https://api.minimax.chat"},
            "research_mcp_tool_arguments_json": {"query": "{query}"},
            "audio_plugin_overrides": {"asr": "sherpa-sensevoice-int8"},
            "audio_language_overrides": {"ja": "whisperx"},
        }
    )

    assert result["task_routing"] == '{"draft_chapter": "openai:gpt-4o-mini"}'
    assert result["task_fallback_routing"] == '{"draft_chapter": ["deepseek:deepseek-chat"]}'
    assert result["research_mcp_args_json"] == '["minimax-coding-plan-mcp"]'
    assert result["research_mcp_env_json"] == '{"MINIMAX_API_HOST": "https://api.minimax.chat"}'
    assert result["research_mcp_tool_arguments_json"] == '{"query": "{query}"}'
    assert result["audio_plugin_overrides"] == '{"asr": "sherpa-sensevoice-int8"}'
    assert result["audio_language_overrides"] == '{"ja": "whisperx"}'


def test_merged_settings_kwargs_restores_numeric_string_fields() -> None:
    from novel_forge.core.config import _merged_to_settings_kwargs

    result = _merged_to_settings_kwargs(
        {
            "tts_tencent_voice_type": 0,
            "api_call_timeout_s": 900,
        }
    )

    assert result["tts_tencent_voice_type"] == "0"
    assert result["api_call_timeout_s"] == 900


def test_coerce_string_plain() -> None:
    assert _coerce_string("hello") == "hello"
    assert _coerce_string("") == ""


def test_strip_prefix_and_coerce() -> None:
    pairs = {
        "NOVEL_FORGE_LOG_LEVEL": "DEBUG",
        "NOVEL_FORGE_TIMEOUT": "30",
        "NOVEL_FORGE_ENABLED": "true",
        "OTHER_KEY": "ignored",
    }
    result = _strip_prefix_and_coerce(pairs, "NOVEL_FORGE_")
    assert result == {
        "log_level": "DEBUG",
        "timeout": 30,
        "enabled": True,
    }


def test_strip_prefix_and_coerce_accepts_dotenv_quotes() -> None:
    pairs = {
        "NOVEL_FORGE_TASK_ROUTING": '\'{"draft_chapter":"deepseek:deepseek-chat"}\'',
        "NOVEL_FORGE_PROVIDER": '"tongyi"',
    }
    result = _strip_prefix_and_coerce(pairs, "NOVEL_FORGE_")

    assert result == {
        "task_routing": {"draft_chapter": "deepseek:deepseek-chat"},
        "provider": "tongyi",
    }


def test_parse_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nNOVEL_FORGE_KEY=val\n\nNOVEL_FORGE_NUM=42\n",
        encoding="utf-8",
    )
    result = _parse_env_file(env_file)
    assert result == {"NOVEL_FORGE_KEY": "val", "NOVEL_FORGE_NUM": "42"}


def test_load_defaults_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("NOVEL_FORGE_LOG_LEVEL", raising=False)
    loader = UnifiedSettingsLoader(dotenv_path=tmp_path / "nonexistent.env")
    assert loader.load("log_level") == "INFO"
    assert loader.load("api_connect_timeout_s") == 30.0
    assert loader.load("api_call_timeout_s") == 900.0


def test_packaged_desktop_loads_the_same_dotenv_path_it_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A frozen desktop must reload the runtime .env after saving settings."""
    import novel_forge.core.config as config_module

    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(config_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config_module, "get_runtime_config_dir", lambda: runtime_dir)
    monkeypatch.delenv("NOVEL_FORGE_OPENAI_API_KEY", raising=False)
    runtime_dir.mkdir()
    (runtime_dir / ".env").write_text(
        "NOVEL_FORGE_OPENAI_API_KEY=runtime-saved-key\n",
        encoding="utf-8",
    )

    dotenv = UnifiedSettingsLoader._auto_dotenv_path()
    loader = UnifiedSettingsLoader(model_profiles_path=tmp_path / "profiles.json")

    assert dotenv == runtime_dir / ".env"
    assert loader.load("openai_api_key") == "runtime-saved-key"


def test_loader_chapter_temperature_defaults_match_settings(tmp_path: Path) -> None:
    from novel_forge.core.config import Settings

    loader = UnifiedSettingsLoader(dotenv_path=tmp_path / "nonexistent.env")
    settings = Settings(_env_file=None)

    assert loader.load("temp_plan_chapter") == settings.temp_plan_chapter
    assert loader.load("temp_draft_chapter") == settings.temp_draft_chapter
    assert loader.load("temp_wave_chapter") == settings.temp_wave_chapter


def test_env_example_defaults_match_settings_for_selected_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novel_forge.core.config import Settings

    for env_suffix in _ENV_EXAMPLE_ALIGNED_SETTINGS:
        monkeypatch.delenv(f"NOVEL_FORGE_{env_suffix}", raising=False)

    env_example = Path(__file__).resolve().parents[2] / ".env.example"
    values = _strip_prefix_and_coerce(_parse_env_file(env_example), "NOVEL_FORGE_")
    settings = Settings(_env_file=None)

    for env_suffix, attr in _ENV_EXAMPLE_ALIGNED_SETTINGS.items():
        assert values[env_suffix.lower()] == getattr(settings, attr)


def test_load_cli_args_override_defaults() -> None:
    loader = UnifiedSettingsLoader(cli_args={"log_level": "DEBUG"})
    assert loader.load("log_level") == "DEBUG"


def test_load_env_vars_override_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOVEL_FORGE_LOG_LEVEL", "WARNING")
    loader = UnifiedSettingsLoader()
    assert loader.load("log_level") == "WARNING"


def test_load_cli_args_override_env_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOVEL_FORGE_LOG_LEVEL", "WARNING")
    loader = UnifiedSettingsLoader(cli_args={"log_level": "DEBUG"})
    assert loader.load("log_level") == "DEBUG"


def test_load_missing_key_raises() -> None:
    loader = UnifiedSettingsLoader(defaults={})
    with pytest.raises(KeyError):
        loader.load("nonexistent_key")


def test_load_all_merges_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVEL_FORGE_LOG_LEVEL", "WARNING")
    loader = UnifiedSettingsLoader(
        cli_args={"log_level": "DEBUG"},
        defaults={"log_level": "INFO", "extra": "fallback"},
    )
    merged = loader.load_all()
    assert merged["log_level"] == "DEBUG"
    assert merged["extra"] == "fallback"


def test_dotenv_source_loading(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "NOVEL_FORGE_LOG_LEVEL=ERROR\nNOVEL_FORGE_API_CALL_TIMEOUT_S=60\n",
        encoding="utf-8",
    )
    loader = UnifiedSettingsLoader(dotenv_path=dotenv)
    assert loader.load("log_level") == "ERROR"
    assert loader.load("api_call_timeout_s") == 60


def test_cli_json_source_loading(tmp_path: Path) -> None:
    cli_json = tmp_path / "novel_forge.cli.json"
    _write_json(
        cli_json,
        {
            "defaults": {"log_level": "CRITICAL", "edit_rounds": 5},
            "run_short": {"theme": "test"},
        },
    )
    loader = UnifiedSettingsLoader(
        cli_json_path=cli_json,
        defaults={"log_level": "INFO", "edit_rounds": 2},
    )
    assert loader.load("log_level") == "CRITICAL"
    assert loader.load("edit_rounds") == 5


def test_cli_json_ignores_non_defaults_sections(tmp_path: Path) -> None:
    cli_json = tmp_path / "novel_forge.cli.json"
    _write_json(cli_json, {"run_short": {"theme": "test"}})
    loader = UnifiedSettingsLoader(cli_json_path=cli_json)
    with pytest.raises(KeyError):
        loader.load("theme")


def test_model_profiles_source_loading(tmp_path: Path) -> None:
    profiles = tmp_path / "model_profiles.json"
    _write_json(
        profiles,
        {
            "profiles": [
                {
                    "profile_id": "tongyi:qwen-max",
                    "display_name": "Qwen Max",
                    "provider": "tongyi",
                    "model_id": "qwen-max",
                }
            ],
            "routes": {
                "draft_chapter": {
                    "profile_id": "tongyi:qwen-max",
                    "provider": "tongyi",
                    "model_id": "qwen-max",
                    "thinking": True,
                    "multi_turn": False,
                }
            },
            "fallback_routes": {
                "draft_chapter": [
                    {
                        "profile_id": "deepseek:deepseek-chat",
                        "provider": "deepseek",
                        "model_id": "deepseek-chat",
                        "thinking": False,
                        "multi_turn": True,
                    }
                ]
            },
            "default_profile_id": "tongyi:qwen-max",
        },
    )
    loader = UnifiedSettingsLoader(
        model_profiles_path=profiles,
        defaults={"default_provider": ""},
    )
    assert loader.load("default_provider") == "tongyi"
    assert loader.load("task_routing") == {"draft_chapter": "tongyi:qwen-max,thinking"}
    assert loader.load("task_fallback_routing") == {
        "draft_chapter": ["deepseek:deepseek-chat,multi_turn"]
    }


def test_model_profiles_missing_file_returns_empty() -> None:
    loader = UnifiedSettingsLoader(model_profiles_path=Path("/nonexistent/profiles.json"))
    assert loader._load_model_profiles() == {}


def test_validate_returns_unified_settings() -> None:
    loader = UnifiedSettingsLoader(cli_args={"log_level": "DEBUG"})
    settings = loader.validate()
    assert isinstance(settings, UnifiedSettings)
    assert settings.log_level == "DEBUG"


def test_validate_coerces_storage_root_to_path() -> None:
    loader = UnifiedSettingsLoader(cli_args={"storage_root": "/tmp/data"})
    settings = loader.validate()
    assert isinstance(settings.storage_root, Path)
    assert str(settings.storage_root) == "/tmp/data"


def test_validate_extra_keys_allowed() -> None:
    loader = UnifiedSettingsLoader(
        cli_args={"custom_key": "custom_value"},
    )
    settings = loader.validate()
    assert settings.custom_key == "custom_value"


def test_validate_model_tier_enum() -> None:
    loader = UnifiedSettingsLoader(cli_args={"default_model_tier": "premium"})
    settings = loader.validate()
    assert settings.default_model_tier.value == "premium"


def test_reload_clears_cache(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("NOVEL_FORGE_LOG_LEVEL=ERROR\n", encoding="utf-8")
    loader = UnifiedSettingsLoader(dotenv_path=dotenv)
    assert loader.load("log_level") == "ERROR"

    dotenv.write_text("NOVEL_FORGE_LOG_LEVEL=WARNING\n", encoding="utf-8")
    loader.reload()
    assert loader.load("log_level") == "WARNING"


def test_has_changed_detects_modification(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("NOVEL_FORGE_LOG_LEVEL=ERROR\n", encoding="utf-8")
    loader = UnifiedSettingsLoader(dotenv_path=dotenv)
    assert not loader.has_changed()

    time.sleep(0.05)
    os.utime(dotenv, None)
    assert loader.has_changed()


def test_fingerprint_changes_when_file_source_changes(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("NOVEL_FORGE_LOG_LEVEL=ERROR\n", encoding="utf-8")
    loader = UnifiedSettingsLoader(dotenv_path=dotenv)
    first = loader.fingerprint()

    time.sleep(0.05)
    dotenv.write_text("NOVEL_FORGE_LOG_LEVEL=WARNING\n", encoding="utf-8")

    assert loader.fingerprint() != first


def test_fingerprint_changes_when_env_vars_change(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOVEL_FORGE_LOG_LEVEL", raising=False)
    loader = UnifiedSettingsLoader()
    first = loader.fingerprint()

    monkeypatch.setenv("NOVEL_FORGE_LOG_LEVEL", "WARNING")

    assert loader.fingerprint() != first


def test_has_changed_no_false_positive_for_unchanged(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("NOVEL_FORGE_LOG_LEVEL=ERROR\n", encoding="utf-8")
    loader = UnifiedSettingsLoader(dotenv_path=dotenv)
    assert not loader.has_changed()


def test_empty_cli_args_does_not_break(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("NOVEL_FORGE_LOG_LEVEL", raising=False)
    loader = UnifiedSettingsLoader(cli_args={}, dotenv_path=tmp_path / "nonexistent.env")
    assert loader.load("log_level") == "INFO"


def test_custom_defaults_replace_built_ins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("NOVEL_FORGE_LOG_LEVEL", raising=False)
    loader = UnifiedSettingsLoader(
        defaults={"log_level": "TRACE"},
        dotenv_path=tmp_path / "nonexistent.env",
    )
    assert loader.load("log_level") == "TRACE"


def test_dotenv_file_not_found_graceful() -> None:
    loader = UnifiedSettingsLoader(dotenv_path=Path("/nonexistent/.env"))
    assert loader.load("log_level") == "INFO"


def test_cli_json_malformed_graceful(tmp_path: Path) -> None:
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("not json", encoding="utf-8")
    loader = UnifiedSettingsLoader(cli_json_path=bad_json)
    assert loader._load_cli_json() == {}


def test_model_profiles_malformed_graceful(tmp_path: Path) -> None:
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("not json", encoding="utf-8")
    loader = UnifiedSettingsLoader(model_profiles_path=bad_json)
    assert loader._load_model_profiles() == {}


def test_load_source_unknown_raises() -> None:
    loader = UnifiedSettingsLoader()
    with pytest.raises(ValueError, match="Unknown source"):
        loader._load_source("unknown_source")
