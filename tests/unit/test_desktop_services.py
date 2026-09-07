"""Tests for desktop persistence and file-operation services."""

from __future__ import annotations

import json

from novel_forge.desktop.config_store import DesktopSettingsStore
from novel_forge.desktop.files import ProjectFileService
from novel_forge.gateway.profiles import ModelProfile, ProfilesConfig


def test_desktop_settings_store_merge_env_preserves_comments(tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("# comment\nFOO=old\nBAR=keep\n", encoding="utf-8")

    DesktopSettingsStore.merge_env(env_path, {"FOO": "new", "BAZ": "add"})

    assert env_path.read_text(encoding="utf-8").splitlines() == [
        "# comment",
        "FOO=new",
        "BAR=keep",
        "BAZ=add",
    ]


def test_merge_env_strips_deprecated_routing_keys(tmp_path) -> None:
    """Deprecated routing keys are removed from .env even if passed in new_map."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# routing section\n"
        "NOVEL_FORGE_TASK_ROUTING='{\"draft\": \"x\"}'\n"
        "NOVEL_FORGE_TASK_FALLBACK_ROUTING='{\"draft\": [\"y\"]}'\n"
        "NOVEL_FORGE_DEEPSEEK_API_KEY=sk-old\n",
        encoding="utf-8",
    )

    DesktopSettingsStore.merge_env(
        env_path,
        {
            "NOVEL_FORGE_TASK_ROUTING": "'{\"draft\": \"z\"}'",
            "NOVEL_FORGE_DEEPSEEK_API_KEY": "sk-new",
        },
    )

    content = env_path.read_text(encoding="utf-8")
    assert "NOVEL_FORGE_TASK_ROUTING" not in content
    assert "NOVEL_FORGE_TASK_FALLBACK_ROUTING" not in content
    assert "NOVEL_FORGE_DEEPSEEK_API_KEY=sk-new" in content


def test_project_file_service_does_not_create_missing_project_before_delete(tmp_path) -> None:
    service = ProjectFileService(tmp_path)

    result = service.delete_project("missing_project")

    assert result.deleted is False
    assert result.path == tmp_path / "missing_project"
    assert not (tmp_path / "missing_project").exists()


def test_desktop_settings_store_writes_env_and_sanitized_profiles(tmp_path) -> None:
    store = DesktopSettingsStore(
        profiles_path=tmp_path / "model_profiles.json",
        env_path=tmp_path / ".env",
    )
    config = ProfilesConfig(
        profiles=[
            ModelProfile(
                profile_id="deepseek:writer",
                display_name="DeepSeek Writer",
                provider="deepseek",
                model_id="deepseek-chat",
                api_key="sk-real",
            )
        ],
        default_profile_id="deepseek:writer",
    )

    result = store.save(
        config=config,
        env_pairs={
            "NOVEL_FORGE_DEEPSEEK_API_KEY": "sk-real",
            "NOVEL_FORGE_DEFAULT_PROVIDER": "deepseek",
        },
    )

    saved_profiles = json.loads(result.profiles_path.read_text(encoding="utf-8"))
    assert "api_key" not in saved_profiles["profiles"][0]
    assert result.env_path == tmp_path / ".env"
    assert result.env_path.read_text(encoding="utf-8").splitlines() == [
        "NOVEL_FORGE_DEEPSEEK_API_KEY=sk-real",
        "NOVEL_FORGE_DEFAULT_PROVIDER=deepseek",
    ]


def test_profiles_config_sets_first_profile_as_default_when_missing() -> None:
    config = ProfilesConfig()

    config.add_profile(
        ModelProfile(
            profile_id="deepseek:writer",
            display_name="DeepSeek Writer",
            provider="deepseek",
            model_id="deepseek-chat",
            api_key="sk-real",
        )
    )

    assert config.default_profile_id == "deepseek:writer"


def test_profiles_to_env_pairs_clear_routing_and_default_when_empty() -> None:
    config = ProfilesConfig()

    env_pairs = config.to_env_pairs()

    # Routing is no longer written to .env — only API keys and default provider
    assert "NOVEL_FORGE_TASK_ROUTING" not in env_pairs
    assert "NOVEL_FORGE_TASK_FALLBACK_ROUTING" not in env_pairs
    assert env_pairs["NOVEL_FORGE_DEFAULT_PROVIDER"] == ""
