"""Tests for FastAPI dependency singleton behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_forge.api import deps
from novel_forge.core.config import Settings, reset_settings
from novel_forge.core.infra.settings_loader import UnifiedSettingsLoader
from novel_forge.workspace.runtime import create_runtime_services


@pytest.fixture(autouse=True)
def clear_dep_caches() -> None:
    deps.get_builder.cache_clear()
    deps.get_job_service.cache_clear()
    deps.get_project_inspector.cache_clear()
    deps.get_router.cache_clear()
    deps.get_runtime_services.cache_clear()
    deps.get_storage.cache_clear()
    deps.get_cached_settings.cache_clear()
    yield
    deps.get_builder.cache_clear()
    deps.get_job_service.cache_clear()
    deps.get_project_inspector.cache_clear()
    deps.get_router.cache_clear()
    deps.get_runtime_services.cache_clear()
    deps.get_storage.cache_clear()
    deps.get_cached_settings.cache_clear()


def test_api_dependencies_are_singletons(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(tmp_path))

    storage_a = deps.get_storage()
    storage_b = deps.get_storage()
    router_a = deps.get_router()
    router_b = deps.get_router()
    builder_a = deps.get_builder()
    builder_b = deps.get_builder()
    runtime_a = deps.get_runtime_services()
    runtime_b = deps.get_runtime_services()
    inspector_a = deps.get_project_inspector()
    inspector_b = deps.get_project_inspector()

    assert storage_a is storage_b
    assert router_a is router_b
    assert builder_a is builder_b
    assert runtime_a is runtime_b
    assert inspector_a is inspector_b
    assert storage_a.root == tmp_path


def test_api_router_has_available_default_provider() -> None:
    router = deps.get_router()

    assert router.default_provider in router.adapters


def test_reload_runtime_dependencies_picks_up_latest_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(first_root))
    first_storage = deps.get_storage()
    assert first_storage.root == first_root

    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(second_root))
    deps.reload_runtime_dependencies()
    second_storage = deps.get_storage()

    assert second_storage.root == second_root
    assert second_storage is not first_storage


def test_reload_runtime_dependencies_preserves_cached_job_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(tmp_path))
    service = deps.get_job_service()
    calls: list[Settings] = []

    def fake_refresh(settings: Settings) -> None:
        calls.append(settings)

    monkeypatch.setattr(service, "refresh_runtime_settings", fake_refresh)

    deps.reload_runtime_dependencies()

    assert len(calls) == 1
    assert deps.get_job_service() is service


def test_reload_runtime_dependencies_replaces_job_service_when_storage_root_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(first_root))
    service = deps.get_job_service()
    calls: list[float] = []

    def fake_shutdown(*, wait_s: float = 2.0, reason: str = "服务正在关闭") -> None:
        calls.append(wait_s)

    monkeypatch.setattr(service, "shutdown", fake_shutdown)
    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(second_root))

    deps.reload_runtime_dependencies()

    assert calls == [2.0]
    assert deps.get_job_service() is not service
    assert deps.get_job_service().storage_root == second_root


def test_runtime_services_snapshot_version_changes_after_reload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "snapshot_first"
    second_root = tmp_path / "snapshot_second"
    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(first_root))
    first_runtime = deps.get_runtime_services()

    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(second_root))
    deps.reload_runtime_dependencies()
    second_runtime = deps.get_runtime_services()

    assert first_runtime.config_snapshot.storage_root == str(first_root.resolve())
    assert second_runtime.config_snapshot.storage_root == str(second_root.resolve())
    assert (
        first_runtime.config_snapshot.config_version
        != second_runtime.config_snapshot.config_version
    )


def test_runtime_and_storage_auto_refresh_when_env_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "auto_first"
    second_root = tmp_path / "auto_second"
    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(first_root))

    first_runtime = deps.get_runtime_services()
    first_storage = deps.get_storage()
    first_inspector = deps.get_project_inspector()

    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(second_root))

    second_runtime = deps.get_runtime_services()
    second_storage = deps.get_storage()
    second_inspector = deps.get_project_inspector()

    assert second_runtime is not first_runtime
    assert second_storage is not first_storage
    assert second_inspector is not first_inspector
    assert second_runtime.config_snapshot.storage_root == str(second_root.resolve())
    assert second_storage.root == second_root


def test_runtime_and_storage_auto_refresh_when_dotenv_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dotenv = tmp_path / ".env"
    profiles = tmp_path / "model_profiles.json"
    first_root = tmp_path / "dotenv_first"
    second_root = tmp_path / "dotenv_second"
    dotenv.write_text(f"NOVEL_FORGE_STORAGE_ROOT={first_root}\n", encoding="utf-8")
    monkeypatch.setattr(
        UnifiedSettingsLoader,
        "_auto_dotenv_path",
        staticmethod(lambda: dotenv),
    )
    monkeypatch.setattr(
        UnifiedSettingsLoader,
        "_auto_profiles_path",
        staticmethod(lambda: profiles),
    )
    monkeypatch.delenv("NOVEL_FORGE_STORAGE_ROOT", raising=False)
    reset_settings()

    first_storage = deps.get_storage()
    assert first_storage.root == first_root

    dotenv.write_text(f"NOVEL_FORGE_STORAGE_ROOT={second_root}\n", encoding="utf-8")

    second_storage = deps.get_storage()
    assert second_storage.root == second_root
    assert second_storage is not first_storage


def test_runtime_services_detects_stale_env_without_api_facade(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "runtime_first"
    second_root = tmp_path / "runtime_second"
    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(first_root))
    reset_settings()
    runtime = create_runtime_services()

    assert runtime.is_config_stale() is False

    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(second_root))

    assert runtime.is_config_stale() is True


def test_create_runtime_services_mock_forces_mock_embeddings_without_mutating_settings(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        storage_root=tmp_path,
        memory_use_mock_embeddings=False,
    )

    runtime = create_runtime_services(settings, mock=True)

    assert settings.memory_use_mock_embeddings is False
    assert runtime.settings.memory_use_mock_embeddings is True
    assert settings.memory_vector_store_backend == "zvec"
    assert runtime.settings.memory_vector_store_backend == "in_memory"
    assert runtime.router.default_provider == "mock"


def test_create_runtime_services_mock_keeps_global_config_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("NOVEL_FORGE_MEMORY_USE_MOCK_EMBEDDINGS", "false")
    reset_settings()
    try:
        runtime = create_runtime_services(mock=True)

        assert runtime.settings.memory_use_mock_embeddings is True
        assert runtime.settings.memory_vector_store_backend == "in_memory"
        assert runtime.is_config_stale() is False
    finally:
        reset_settings()


def test_runtime_reload_preserves_mock_mode_after_global_settings_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(tmp_path / "first"))
    reset_settings()
    runtime = create_runtime_services(mock=True)
    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(tmp_path / "second"))
    try:
        assert runtime.is_config_stale()
        runtime.reload_runtime_dependencies()
        assert runtime.router.default_provider == "mock"
        assert set(runtime.router.adapters) == {"mock"}
        assert runtime.settings.memory_use_mock_embeddings
        assert runtime.storage.root == tmp_path / "second"
        assert not runtime.is_config_stale()
    finally:
        reset_settings()


def test_explicit_runtime_settings_are_not_replaced_by_global_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = Settings(_env_file=None, storage_root=tmp_path / "isolated")
    runtime = create_runtime_services(settings, mock=True)
    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(tmp_path / "unrelated"))
    assert not runtime.is_config_stale()
    settings.storage_root = tmp_path / "updated-isolated"
    assert runtime.is_config_stale()
    runtime.reload_runtime_dependencies()
    assert runtime.storage.root == tmp_path / "updated-isolated"
    assert runtime.router.default_provider == "mock"
