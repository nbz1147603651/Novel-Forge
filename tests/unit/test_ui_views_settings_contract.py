"""Parity checks for the sanitized React settings projection."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_forge.api.routes.ui_views import (
    VoiceAudioModelOperationBody,
    _compose_settings_view,
    _reader_source_metadata,
    _validate_voice_audio_model_operation,
    _voice_audio_model_center_view,
)
from novel_forge.app_service.settings_environment import (
    _PARAM_ENV_MAP,
    _PARAM_SETTINGS_ATTR,
    creation_parameter_env_pairs,
    creative_temperature_env_pairs,
    get_creation_parameters,
    persist_env_pairs,
    persist_settings_environment,
    route_temperature_env_pairs,
    validate_creation_parameters,
)
from novel_forge.app_service.settings_save import (
    SettingsRouteCommand,
    synchronize_profile_commands,
)
from novel_forge.core.config import Settings
from novel_forge.core.parsing.temperature_jitter import is_temperature_jitter_protected
from novel_forge.core.task_catalog import (
    LONG_TEMPERATURE_TASKS,
    MULTI_TURN_TASK_KEYS,
    ROUTING_GROUPS,
    SHORT_TEMPERATURE_TASKS,
    routing_subgroup_task_keys,
)
from novel_forge.desktop.config_store import DesktopSettingsStore
from novel_forge.gateway.profiles import ModelProfile, ProfilesConfig, TaskRouteEntry


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_settings_view_projects_the_canonical_pyside_catalog() -> None:
    settings = _settings()
    temperature_task = SHORT_TEMPERATURE_TASKS[0]
    setattr(settings, temperature_task.setting_attr, 0.77)
    config = ProfilesConfig(
        profiles=[
            ModelProfile(
                profile_id="local:fixture",
                display_name="本地测试模型",
                provider="ollama",
                model_id="qwen2.5:7b",
            )
        ],
        default_profile_id="local:fixture",
        routes={temperature_task.key: TaskRouteEntry(profile_id="local:fixture", multi_turn=True)},
    )

    view = _compose_settings_view(settings=settings, default_provider="ollama", config=config)

    assert [group["id"] for group in view["routing_groups"]] == [
        group.name for group in ROUTING_GROUPS
    ]
    assert "配音" in {group["id"] for group in view["routing_groups"]}
    assert view["model_profiles"][0]["supports_multi_turn"] is True
    assert view["model_profiles"][0]["masked_key"] == "(未配置)"
    assert view["model_profiles"][0]["key_configured"] is True
    assert view["model_profiles"][0]["is_embedding"] is False
    assert "tongyi" in {option["id"] for option in view["model_provider_options"]}
    assert "volcengine_ark" in {option["id"] for option in view["model_provider_options"]}
    token_plan = next(
        option for option in view["model_provider_options"] if option["id"] == "tongyi_token_plan"
    )
    assert token_plan["default_base_url"] == (
        "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    )
    assert "sk-sp-" in token_plan["connection_hint"]
    assert view["chapter_runtime_policy"]["preset"] == "compat"
    assert view["chapter_runtime_policy"]["preset_options"][2]["id"] == "balanced"

    for source_group, rendered_group in zip(ROUTING_GROUPS, view["routing_groups"], strict=True):
        rendered_subgroups = {subgroup["id"]: subgroup for subgroup in rendered_group["subgroups"]}
        for source_subgroup in source_group.subgroups:
            rendered = rendered_subgroups[f"{source_group.name}/{source_subgroup.title}"]
            assert rendered["route_ids"] == list(
                routing_subgroup_task_keys(source_group, source_subgroup)
            )

    rendered_route = next(
        route
        for group in view["routing_groups"]
        for route in group["routes"]
        if route["task_key"] == temperature_task.key
    )
    assert rendered_route["temperature"] == 0.77
    assert rendered_route["supports_multi_turn"] is (temperature_task.key in MULTI_TURN_TASK_KEYS)


def test_profile_sync_preserves_existing_secret_and_removes_orphan_routes() -> None:
    retained = ModelProfile(
        profile_id="openai:kept",
        display_name="旧名称",
        provider="openai",
        model_id="gpt-4o",
        api_key="secret-kept",
        base_url="https://example.test/v1",
    )
    removed = ModelProfile(
        profile_id="openai:removed",
        display_name="待删除",
        provider="openai",
        model_id="gpt-4o-mini",
        api_key="secret-removed",
    )
    config = ProfilesConfig(
        profiles=[retained, removed],
        default_profile_id=removed.profile_id,
        routes={"kept": TaskRouteEntry(profile_id=retained.profile_id)},
        fallback_routes={
            "mixed": [
                TaskRouteEntry(profile_id=retained.profile_id),
                TaskRouteEntry(profile_id=removed.profile_id),
            ]
        },
        group_bulk_routes={"group": {"profile_id": removed.profile_id}},
    )

    rejected = synchronize_profile_commands(
        config,
        [
            {
                "id": retained.profile_id,
                "label": "更新后名称",
                "provider": retained.provider,
                "model": retained.model_id,
            }
        ],
    )

    assert rejected == []
    assert config.profiles[0].display_name == "更新后名称"
    assert config.profiles[0].api_key == "secret-kept"
    assert config.profiles[0].base_url == "https://example.test/v1"
    assert config.default_profile_id == retained.profile_id
    assert [entry.profile_id for entry in config.fallback_routes["mixed"]] == [retained.profile_id]
    assert config.group_bulk_routes == {}


def test_profile_sync_replaces_clears_and_moves_secrets_without_exposing_them() -> None:
    existing = ModelProfile(
        profile_id="openai:old",
        display_name="旧档案",
        provider="openai",
        model_id="gpt-4o",
        api_key="secret-old",
        base_url="https://old.example/v1",
    )
    config = ProfilesConfig(profiles=[existing], default_profile_id=existing.profile_id)

    rejected = synchronize_profile_commands(
        config,
        [
            {
                "id": "openai:new",
                "previous_id": "openai:old",
                "label": "新档案",
                "provider": "openai",
                "model": "gpt-5.6",
                "api_key_action": "replace",
                "api_key": "secret-new",
                "base_url": "https://new.example/v1",
            }
        ],
    )

    assert rejected == []
    assert config.profiles[0].api_key == "secret-new"
    assert config.profiles[0].base_url == "https://new.example/v1"

    rejected = synchronize_profile_commands(
        config,
        [
            {
                "id": "openai:new",
                "label": "新档案",
                "provider": "openai",
                "model": "gpt-5.6",
                "api_key_action": "clear",
            }
        ],
    )
    assert rejected == []
    assert config.profiles[0].api_key == ""


def test_profile_secret_persistence_uses_native_env_boundary(tmp_path: Path) -> None:
    profile = ModelProfile(
        profile_id="openai:gpt",
        display_name="GPT",
        provider="openai",
        model_id="gpt-5.6",
        api_key="secret-value",
    )
    config = ProfilesConfig(profiles=[profile], default_profile_id=profile.profile_id)
    profiles_path = tmp_path / "model_profiles.json"
    env_path = tmp_path / ".env"

    DesktopSettingsStore(profiles_path=profiles_path, env_path=env_path).save(
        config=config,
        env_pairs=config.to_env_pairs(),
    )

    assert "secret-value" not in profiles_path.read_text(encoding="utf-8")
    assert "NOVEL_FORGE_OPENAI_API_KEY=secret-value" in env_path.read_text(encoding="utf-8")


def test_temperature_env_pairs_keep_only_valid_unprotected_tasks() -> None:
    protected_task = next(
        task
        for task in (*SHORT_TEMPERATURE_TASKS, *LONG_TEMPERATURE_TASKS)
        if is_temperature_jitter_protected(task.task_type)
    )
    writable_task = next(
        task
        for task in (*SHORT_TEMPERATURE_TASKS, *LONG_TEMPERATURE_TASKS)
        if not is_temperature_jitter_protected(task.task_type)
    )
    creative_pairs = creative_temperature_env_pairs(
        {
            "enabled": True,
            "scope": "custom",
            "down_delta": 0.4,
            "up_delta": 0.2,
            "custom_task_keys": [writable_task.task_type.value, protected_task.task_type.value],
        }
    )

    assert creative_pairs["NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_ENABLED"] == "true"
    assert (
        protected_task.task_type.value
        not in creative_pairs["NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_CUSTOM_TASKS"]
    )
    route_pairs = route_temperature_env_pairs(
        {writable_task.key: SettingsRouteCommand(primary_profile_id="local", temperature=0.66)}
    )
    assert route_pairs == {f"NOVEL_FORGE_{writable_task.setting_attr.upper()}": "0.66"}


def test_env_pair_persistence_is_atomic_and_keeps_unrelated_lines(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("# keep\nNOVEL_FORGE_EXISTING=before\n", encoding="utf-8")

    persist_env_pairs(
        {"NOVEL_FORGE_EXISTING": "after", "NOVEL_FORGE_NEW": "value"}, env_path=env_path
    )

    assert env_path.read_text(encoding="utf-8") == (
        "# keep\nNOVEL_FORGE_EXISTING=after\nNOVEL_FORGE_NEW=value\n"
    )
    assert not list(tmp_path.glob(".env.*.tmp"))


def test_settings_environment_clears_explicit_empty_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "NOVEL_FORGE_HUMANIZE_MODEL=old:model\n"
        "NOVEL_FORGE_TEMP_DRAFT_CHAPTER=0.9\n"
        "NOVEL_FORGE_RESEARCH_API_KEY=keep-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "novel_forge.core.config.get_writable_env_path",
        lambda: env_path,
    )

    persist_settings_environment(
        creation_parameters={"humanize-model": "", "research-api-key": ""},
        creative_temperature=None,
        routes={"draft_chapter": SettingsRouteCommand(temperature=None)},
        theme_id=None,
        font_preferences=None,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "NOVEL_FORGE_HUMANIZE_MODEL" not in content
    assert "NOVEL_FORGE_TEMP_DRAFT_CHAPTER" not in content
    assert "NOVEL_FORGE_RESEARCH_API_KEY=keep-secret" in content


def test_creation_parameter_validation_uses_settings_types_and_ui_aliases() -> None:
    accepted, rejected = validate_creation_parameters(
        {
            "local-check-mode": "prescreen",
            "init-coh-confidence": "2",
            "unknown-setting": "value",
        }
    )

    assert accepted == {"local-check-mode": "true"}
    assert {item["route_id"] for item in rejected} == {
        "__setting__:init-coh-confidence",
        "__setting__:unknown-setting",
    }


def test_voice_platform_parameters_use_the_same_guarded_env_contract() -> None:
    pairs = creation_parameter_env_pairs(
        {
            "audio-quality-preset": "master",
            "audio-plugin-overrides": '{"alignment":"qwen3-forced-aligner"}',
            "audio-qwen3-asr-api-key": "new-sidecar-token",
            "tts-master-quality-gate-blocking": "true",
            "tts-audio-quality-tier": "commercial",
            "tts-subtitle-word-level": "true",
            "tts-minimax-force-cbr": "true",
            "untrusted-name": "must-not-be-written",
        }
    )

    assert pairs == {
        "NOVEL_FORGE_AUDIO_QUALITY_PRESET": "master",
        "NOVEL_FORGE_AUDIO_PLUGIN_OVERRIDES": '{"alignment":"qwen3-forced-aligner"}',
        "NOVEL_FORGE_AUDIO_QWEN3_ASR_API_KEY": "new-sidecar-token",
        "NOVEL_FORGE_TTS_MASTER_QUALITY_GATE_BLOCKING": "true",
        "NOVEL_FORGE_TTS_AUDIO_QUALITY_TIER": "commercial",
        "NOVEL_FORGE_TTS_SUBTITLE_WORD_LEVEL": "true",
        "NOVEL_FORGE_TTS_MINIMAX_FORCE_CBR": "true",
    }
    assert (
        "audio-qwen3-asr-api-key"
        not in _compose_settings_view(
            settings=_settings(), default_provider="mock", config=ProfilesConfig()
        )["creation_parameters"]
    )


def test_voice_model_center_projects_the_shared_catalog_and_rejects_cross_kind_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAudioModelCenterService:
        def __init__(self, _settings: Settings) -> None:
            pass

        async def list_models(self) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(
                    descriptor=SimpleNamespace(
                        plugin_id="qwen3_tts_formal",
                        display_name="Qwen3–TTS Formal",
                        roles=["正式合成"],
                        family="Qwen3–TTS",
                        recommended_for="正式成片",
                        estimated_download_bytes=3_400_000_000,
                        requires_license_acceptance=False,
                        license_name="",
                        license_url="",
                    ),
                    state="installed",
                    runtime_healthy=True,
                    runtime_version="2",
                    installed_revision="main",
                    installed_size_bytes=3_300_000_000,
                    local_path="/managed/qwen3",
                    project_references=["fixture"],
                    license_accepted=False,
                    self_test_passed=True,
                    detail="本地文件完整。",
                    compatible=True,
                    compatibility_reason="",
                )
            ]

        async def list_runtimes(self) -> list[dict[str, object]]:
            return [
                {
                    "descriptor": {
                        "runtime_id": "qwen3_tts",
                        "display_name": "Qwen3–TTS Sidecar",
                        "python_constraint": ">=3.12",
                        "port": 8011,
                        "version": "2",
                        "managed_process": True,
                    },
                    "state": {
                        "state": "running",
                        "version": "2",
                        "environment_path": "/managed/runtime",
                        "rollback_available": False,
                    },
                }
            ]

        async def repository_status(self, _statuses: object) -> dict[str, object]:
            return {
                "root": "/managed",
                "size_bytes": 3_300_000_000,
                "free_bytes": 50_000_000_000,
                "rollback_available": False,
            }

    from novel_forge.tts.model_center import service as model_center_service

    monkeypatch.setattr(
        model_center_service, "AudioModelCenterService", FakeAudioModelCenterService
    )
    view = asyncio.run(_voice_audio_model_center_view(_settings()))

    assert view["installed_model_count"] == 1
    assert view["models"][0]["state_label"] == "已安装"
    assert view["models"][0]["local_path"] == "/managed/qwen3"
    assert view["runtimes"][0]["status"] == "运行中"
    with pytest.raises(Exception, match="目标类型不匹配"):
        _validate_voice_audio_model_operation(
            VoiceAudioModelOperationBody(
                target_kind="runtime",
                target_id="qwen3_tts",
                operation="install",
            )
        )


def test_reader_source_metadata_uses_the_real_resolved_file(tmp_path: Path) -> None:
    document = tmp_path / "reports" / "chapter_004_eval.json"
    document.parent.mkdir()
    document.write_text('{"score": 8.2}\n', encoding="utf-8")

    metadata = _reader_source_metadata(document)

    assert metadata["source_local_path"] == str(document.resolve())
    assert metadata["source_byte_size"] == document.stat().st_size
    assert metadata["source_modified_at_label"]


def test_generated_react_settings_fixture_is_current() -> None:
    project_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            str(project_root / "tools/ui-parity/generate_react_settings_fixture.py"),
            "--check",
        ],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_param_allowlists_are_symmetric_except_write_only_keys() -> None:
    """Every echoed field must also be writable; only credential keys differ."""
    assert set(_PARAM_SETTINGS_ATTR) <= set(_PARAM_ENV_MAP)
    write_only = set(_PARAM_ENV_MAP) - set(_PARAM_SETTINGS_ATTR)
    assert write_only == {
        "audio-qwen3-asr-api-key",
        "research-api-key",
        "tts-dashscope-api-key",
        "tts-local-api-key",
        "tts-mimo-api-key",
        "tts-minimax-api-key",
        "tts-qwen3-api-key",
        "tts-tencent-secret-id",
        "tts-tencent-secret-key",
        "volcengine-ark-api-key",
    }


def test_tts_provider_catalog_settings_are_guarded_by_api_allowlists() -> None:
    """A catalog field is not UI-ready until the API can safely persist it."""
    from novel_forge.tts.platform.provider_catalog import tts_provider_catalog

    for provider in tts_provider_catalog():
        for setting in provider.settings:
            assert setting.parameter_id in _PARAM_ENV_MAP
            if not setting.is_secret:
                assert setting.parameter_id in _PARAM_SETTINGS_ATTR


def test_param_allowlists_cover_every_fire_tune_form_field() -> None:
    """Every CreationParameterSections field id must persist and echo back."""
    import re

    project_root = Path(__file__).resolve().parents[2]
    tsx = (
        project_root / "clients/nimo-desktop/src/components/CreationParameterSections.tsx"
    ).read_text(encoding="utf-8")
    field_ids = set(re.findall(r'\{ id: "([a-z0-9-]+)"', tsx))
    assert field_ids, "failed to extract form field ids"

    missing_env = sorted(field_ids - set(_PARAM_ENV_MAP))
    # Write-only credential fields are persisted but never echoed back.
    missing_attr = sorted(
        field_id
        for field_id in field_ids
        if field_id not in _PARAM_SETTINGS_ATTR and field_id != "research-api-key"
    )
    assert missing_env == [], f"fields without a writable env mapping: {missing_env}"
    assert missing_attr == [], f"fields without an echo-back attr mapping: {missing_attr}"


def test_param_allowlist_attrs_all_exist_on_settings() -> None:
    """Every attr name in the allowlists must be a real Settings field."""
    settings = _settings()
    missing = [
        field_id
        for field_id, attr_name in _PARAM_SETTINGS_ATTR.items()
        if not hasattr(settings, attr_name)
    ]
    assert missing == [], f"allowlist attrs missing on Settings: {missing}"


def test_param_allowlist_round_trip_persists_and_echoes() -> None:
    """A representative sample round-trips through env pairs and echo-back."""
    sample = {
        "long-beats-min": "5",
        "max-profiles": "16",
        "macro-guard-enabled": "true",
        "book-audit-mode": "full_text",
        "notify-decision-sound": "bell",
        "ollama-url": "http://127.0.0.1:11434",
        "tts-script-generation-top-p": "0.9",
    }
    pairs = creation_parameter_env_pairs(sample)
    assert set(pairs) == {
        "NOVEL_FORGE_LONG_PLAN_BEATS_MIN",
        "NOVEL_FORGE_LONG_PROMPT_MAX_CHARACTER_PROFILES",
        "NOVEL_FORGE_LONG_MACRO_GUARD_ENABLED",
        "NOVEL_FORGE_LONG_BOOK_AUDIT_DEFAULT_MODE",
        "NOVEL_FORGE_DESKTOP_NOTIFICATION_DECISION_SOUND",
        "NOVEL_FORGE_OLLAMA_BASE_URL",
        "NOVEL_FORGE_TTS_SCRIPT_GENERATION_TOP_P",
    }

    settings = Settings(_env_file=None, **{})
    for field_id, value in sample.items():
        attr_name = _PARAM_SETTINGS_ATTR[field_id]
        raw = "true" if value == "true" else value
        setattr(settings, attr_name, raw)
    echoed = get_creation_parameters(settings)
    for field_id in sample:
        assert echoed.get(field_id) == sample[field_id], field_id
