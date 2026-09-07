"""Unit coverage for the Engine-owned settings persistence application service."""

from __future__ import annotations

from novel_forge.app_service.settings_save import (
    SettingsRouteCommand,
    SettingsSaveCommand,
    save_settings_command,
)
from novel_forge.core.task_catalog import ROUTING_GROUPS
from novel_forge.gateway.profiles import ModelProfile, ProfilesConfig, TaskRouteEntry


async def test_save_settings_command_persists_routes_and_passes_only_typed_values(
    monkeypatch,
) -> None:
    from novel_forge.app_service import settings_save

    profile = ModelProfile(
        profile_id="ollama:local",
        display_name="本地模型",
        provider="ollama",
        model_id="qwen2.5:7b",
        api_key="configured",
    )
    config = ProfilesConfig(profiles=[profile], default_profile_id=profile.profile_id)
    task_key = ROUTING_GROUPS[0].tasks[0].key
    saved: dict[str, object] = {}
    persisted_environment: dict[str, object] = {}

    class FakeStore:
        def save(self, **kwargs) -> None:
            saved.update(kwargs)

    def persist_environment(
        creation_parameters,
        creative_temperature,
        routes,
        theme_id,
        font_preferences,
    ) -> None:
        persisted_environment.update(
            {
                "creation_parameters": creation_parameters,
                "creative_temperature": creative_temperature,
                "routes": routes,
                "theme_id": theme_id,
                "font_preferences": font_preferences,
            }
        )

    monkeypatch.setattr(settings_save, "load_or_import_profiles", lambda: config)
    monkeypatch.setattr(settings_save, "SettingsStore", FakeStore)
    monkeypatch.setattr(settings_save, "persist_settings_environment", persist_environment)

    result = await save_settings_command(
        SettingsSaveCommand(
            default_profile_id=profile.profile_id,
            routes={
                task_key: SettingsRouteCommand(
                    primary_profile_id=profile.profile_id,
                    thinking_enabled=True,
                    multi_turn_enabled=True,
                    temperature=0.7,
                )
            },
            creation_parameters={"outline-batch": "4"},
            theme_id="ink",
        ),
    )

    assert result.status == "saved"
    assert result.persistence == "persisted"
    assert result.accepted_route_ids == [task_key]
    assert saved["config"] is config
    assert persisted_environment["creation_parameters"] == {"outline-batch": "4"}
    assert persisted_environment["theme_id"] == "ink"
    assert persisted_environment["routes"] == {
        task_key: SettingsRouteCommand(
            primary_profile_id=profile.profile_id,
            thinking_enabled=True,
            multi_turn_enabled=True,
            temperature=0.7,
        )
    }


async def test_save_settings_command_expands_chapter_policy_without_silent_enable(
    monkeypatch,
) -> None:
    from novel_forge.app_service import settings_save

    profile = ModelProfile(
        profile_id="ollama:local",
        display_name="本地模型",
        provider="ollama",
        model_id="qwen2.5:7b",
        api_key="configured",
    )
    config = ProfilesConfig(profiles=[profile], default_profile_id=profile.profile_id)
    persisted_environment: dict[str, object] = {}

    class FakeStore:
        def save(self, **_kwargs) -> None:
            return None

    monkeypatch.setattr(settings_save, "load_or_import_profiles", lambda: config)
    monkeypatch.setattr(settings_save, "SettingsStore", FakeStore)
    monkeypatch.setattr(
        settings_save,
        "persist_settings_environment",
        lambda **kwargs: persisted_environment.update(kwargs),
    )

    result = await save_settings_command(
        SettingsSaveCommand(chapter_runtime_policy={"preset": "safe"})
    )

    assert result.status == "saved"
    assert persisted_environment["creation_parameters"] == {
        "chapter-intent-guard-mode": "block",
        "chapter-research-refresh-enabled": "true",
        "chapter-research-inspiration-enabled": "false",
        "chapter-research-inspiration-cooldown": "3",
        "short-adaptive-revision-enabled": "true",
        "long-single-final-verify-enabled": "true",
    }


async def test_save_settings_command_clears_explicit_route_to_inherit_default(
    monkeypatch,
) -> None:
    from novel_forge.app_service import settings_save

    default_profile = ModelProfile(
        profile_id="minimax:m3",
        display_name="MiniMax M3",
        provider="minimax",
        model_id="MiniMax-M3",
        api_key="configured",
    )
    stale_profile = ModelProfile(
        profile_id="mimo:v25",
        display_name="MiMo V2.5",
        provider="mimo",
        model_id="mimo-v2.5",
        api_key="configured",
    )
    task_key = ROUTING_GROUPS[0].tasks[0].key
    config = ProfilesConfig(
        profiles=[default_profile, stale_profile],
        default_profile_id=default_profile.profile_id,
        routes={task_key: settings_save.TaskRouteEntry(profile_id=stale_profile.profile_id)},
        fallback_routes={task_key: [settings_save.TaskRouteEntry(profile_id=default_profile.profile_id)]},
    )

    class FakeStore:
        def save(self, **_kwargs) -> None:
            return None

    monkeypatch.setattr(settings_save, "load_or_import_profiles", lambda: config)
    monkeypatch.setattr(settings_save, "SettingsStore", FakeStore)
    monkeypatch.setattr(settings_save, "persist_settings_environment", lambda **_kwargs: None)

    result = await save_settings_command(
        SettingsSaveCommand(routes={task_key: SettingsRouteCommand()})
    )

    assert result.status == "saved"
    assert result.accepted_route_ids == [task_key]
    assert task_key not in config.routes
    assert task_key not in config.fallback_routes


async def test_save_settings_command_reloads_host_runtime_after_profile_persistence(
    monkeypatch,
) -> None:
    from novel_forge.app_service import settings_save

    profile = ModelProfile(
        profile_id="minimax:m3",
        display_name="MiniMax M3",
        provider="minimax",
        model_id="MiniMax-M3",
        api_key="configured",
    )
    config = ProfilesConfig(profiles=[profile], default_profile_id=profile.profile_id)
    reloads: list[bool] = []

    class FakeStore:
        def save(self, **_kwargs) -> None:
            return None

    monkeypatch.setattr(settings_save, "load_or_import_profiles", lambda: config)
    monkeypatch.setattr(settings_save, "SettingsStore", FakeStore)
    monkeypatch.setattr(settings_save, "persist_settings_environment", lambda **_kwargs: None)

    result = await save_settings_command(
        SettingsSaveCommand(default_profile_id=profile.profile_id),
        reload_runtime=lambda: reloads.append(True),
    )

    assert reloads == [True]
    assert result.runtime_reload_status == "reloaded"


async def test_save_settings_command_rejects_invalid_runtime_values_before_persistence(
    monkeypatch,
) -> None:
    from novel_forge.app_service import settings_save

    profile = ModelProfile(
        profile_id="minimax:m3",
        display_name="MiniMax M3",
        provider="minimax",
        model_id="MiniMax-M3",
        api_key="configured",
    )
    config = ProfilesConfig(profiles=[profile], default_profile_id=profile.profile_id)
    persisted_environment: dict[str, object] = {}

    class FakeStore:
        def save(self, **_kwargs) -> None:
            return None

    monkeypatch.setattr(settings_save, "load_or_import_profiles", lambda: config)
    monkeypatch.setattr(settings_save, "SettingsStore", FakeStore)
    monkeypatch.setattr(
        settings_save,
        "persist_settings_environment",
        lambda **kwargs: persisted_environment.update(kwargs),
    )

    result = await save_settings_command(
        SettingsSaveCommand(
            creation_parameters={
                "local-check-mode": "prescreen",
                "init-coh-confidence": "2",
            }
        )
    )

    assert result.status == "partial"
    assert persisted_environment["creation_parameters"] == {"local-check-mode": "true"}
    assert result.rejected_routes == [
        {
            "route_id": "__setting__:init-coh-confidence",
            "reason": "value is outside the Settings contract",
        }
    ]


async def test_save_settings_command_rejects_embedding_generation_routes(
    monkeypatch,
) -> None:
    from novel_forge.app_service import settings_save

    primary = ModelProfile(
        profile_id="minimax:m3",
        display_name="MiniMax M3",
        provider="minimax",
        model_id="MiniMax-M3",
        api_key="configured",
    )
    embedding = ModelProfile(
        profile_id="tongyi:embedding",
        display_name="Tongyi Embedding",
        provider="tongyi",
        model_id="text-embedding-v4",
        api_key="configured",
    )
    fallback = ModelProfile(
        profile_id="tongyi:flash",
        display_name="Tongyi Flash",
        provider="tongyi",
        model_id="deepseek-v4-flash",
        api_key="configured",
    )
    task_key = ROUTING_GROUPS[0].tasks[0].key
    config = ProfilesConfig(profiles=[primary, embedding, fallback], default_profile_id=primary.profile_id)

    class FakeStore:
        def save(self, **_kwargs) -> None:
            return None

    monkeypatch.setattr(settings_save, "load_or_import_profiles", lambda: config)
    monkeypatch.setattr(settings_save, "SettingsStore", FakeStore)
    monkeypatch.setattr(settings_save, "persist_settings_environment", lambda **_kwargs: None)

    result = await save_settings_command(
        SettingsSaveCommand(
            default_profile_id=embedding.profile_id,
            routes={
                task_key: SettingsRouteCommand(
                    primary_profile_id=primary.profile_id,
                    fallback_routes=[
                        {"profile_id": embedding.profile_id},
                        {"profile_id": fallback.profile_id},
                    ],
                )
            },
        )
    )

    assert result.status == "partial"
    assert config.default_profile_id == primary.profile_id
    assert config.routes[task_key].profile_id == primary.profile_id
    assert [entry.profile_id for entry in config.fallback_routes[task_key]] == [fallback.profile_id]
    assert {entry["route_id"] for entry in result.rejected_routes} == {"__default__", task_key}


def test_profile_sync_repairs_legacy_embedding_generation_routes() -> None:
    from novel_forge.app_service.settings_save import synchronize_profile_commands

    embedding = ModelProfile(
        profile_id="tongyi:embedding",
        display_name="Tongyi Embedding",
        provider="tongyi",
        model_id="text-embedding-v4",
        api_key="configured",
    )
    text = ModelProfile(
        profile_id="minimax:m3",
        display_name="MiniMax M3",
        provider="minimax",
        model_id="MiniMax-M3",
        api_key="configured",
    )
    task_key = ROUTING_GROUPS[0].tasks[0].key
    config = ProfilesConfig(
        profiles=[embedding, text],
        default_profile_id=embedding.profile_id,
        routes={task_key: TaskRouteEntry(profile_id=embedding.profile_id)},
        fallback_routes={task_key: [TaskRouteEntry(profile_id=text.profile_id)]},
        group_bulk_routes={
            "短篇流程 / 构思与节拍": {
                "profile_id": text.profile_id,
                "fallback_routes": [{"profile_id": embedding.profile_id}],
            }
        },
    )

    rejected = synchronize_profile_commands(
        config,
        [
            {
                "id": embedding.profile_id,
                "label": embedding.display_name,
                "provider": embedding.provider,
                "model": embedding.model_id,
            },
            {
                "id": text.profile_id,
                "label": text.display_name,
                "provider": text.provider,
                "model": text.model_id,
            },
        ],
    )

    assert config.default_profile_id == text.profile_id
    assert task_key not in config.routes
    assert task_key not in config.fallback_routes
    assert config.group_bulk_routes["短篇流程 / 构思与节拍"]["fallback_routes"] == []
    assert {entry["route_id"] for entry in rejected} == {task_key}
