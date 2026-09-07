"""FastAPI dependency injection."""

from __future__ import annotations

from functools import lru_cache

from novel_forge.app_service.engine_queries import EngineQueryService
from novel_forge.app_service.job_service import JobService
from novel_forge.core.config import Settings, get_settings, reset_settings
from novel_forge.gateway.factory import ModelRouterBuilder
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.factory import create_storage_backend
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.workspace.projects import ProjectInspector
from novel_forge.workspace.runtime import (
    RuntimeServices,
    capture_runtime_config_snapshot,
    create_runtime_services,
)


@lru_cache
def _get_cached_settings_cached() -> Settings:
    return get_settings()


@lru_cache
def _get_storage_cached() -> FileSystemStorage:
    settings = _get_cached_settings_cached()
    return create_storage_backend(settings)


@lru_cache
def _get_router_cached() -> ModelRouter:
    settings = _get_cached_settings_cached()
    builder = ModelRouterBuilder(settings)
    return builder.build(mock=False)


@lru_cache
def _get_builder_cached() -> PromptBuilder:
    return PromptBuilder()


@lru_cache
def _get_runtime_services_cached() -> RuntimeServices:
    return create_runtime_services(_get_cached_settings_cached())


@lru_cache
def _get_project_inspector_cached() -> ProjectInspector:
    return ProjectInspector(_get_storage_cached())


@lru_cache
def _get_job_service_cached() -> JobService:
    from novel_forge.control_plane.factory import create_shadow_recorder

    settings = _get_cached_settings_cached()
    shadow = create_shadow_recorder(settings)
    return JobService(
        storage_root=_get_storage_cached().root,
        shadow_recorder=shadow,
    )


@lru_cache
def _get_engine_query_service_cached() -> EngineQueryService:
    return EngineQueryService(
        storage=_get_storage_cached(),
        settings=_get_cached_settings_cached(),
        jobs=_get_job_service_cached(),
    )


def _peek_cached_settings() -> Settings | None:
    if _get_cached_settings_cached.cache_info().currsize == 0:
        return None
    return _get_cached_settings_cached()


def _peek_cached_runtime_services() -> RuntimeServices | None:
    if _get_runtime_services_cached.cache_info().currsize == 0:
        return None
    return _get_runtime_services_cached()


def _peek_cached_job_service() -> JobService | None:
    if _get_job_service_cached.cache_info().currsize == 0:
        return None
    return _get_job_service_cached()


def _cached_api_dependencies_are_stale() -> bool:
    observed_snapshot = capture_runtime_config_snapshot(get_settings())

    cached_settings = _peek_cached_settings()
    if cached_settings is not None:
        cached_settings_snapshot = capture_runtime_config_snapshot(cached_settings)
        if cached_settings_snapshot.config_version != observed_snapshot.config_version:
            return True

    cached_runtime = _peek_cached_runtime_services()
    if cached_runtime is not None:
        if cached_runtime.config_snapshot.config_version != observed_snapshot.config_version:
            return True

    return False


def _ensure_fresh_api_dependencies() -> None:
    if _cached_api_dependencies_are_stale():
        reload_runtime_dependencies()


def get_cached_settings() -> Settings:
    _ensure_fresh_api_dependencies()
    return _get_cached_settings_cached()


def get_storage() -> FileSystemStorage:
    _ensure_fresh_api_dependencies()
    return _get_storage_cached()


def get_router() -> ModelRouter:
    _ensure_fresh_api_dependencies()
    return _get_router_cached()


def get_builder() -> PromptBuilder:
    _ensure_fresh_api_dependencies()
    return _get_builder_cached()


def get_runtime_services() -> RuntimeServices:
    _ensure_fresh_api_dependencies()
    return _get_runtime_services_cached()


def get_project_inspector() -> ProjectInspector:
    _ensure_fresh_api_dependencies()
    return _get_project_inspector_cached()


def get_job_service() -> JobService:
    _ensure_fresh_api_dependencies()
    return _get_job_service_cached()


def get_engine_query_service() -> EngineQueryService:
    _ensure_fresh_api_dependencies()
    return _get_engine_query_service_cached()


def get_runtime_services_cached() -> RuntimeServices:
    return _get_runtime_services_cached()


def get_project_inspector_cached() -> ProjectInspector:
    return _get_project_inspector_cached()


def shutdown_job_service(*, wait_s: float = 2.0) -> None:
    service = _peek_cached_job_service()
    if service is None:
        return
    service.shutdown(wait_s=wait_s)


def reload_runtime_dependencies() -> None:
    """Refresh config-backed dependencies without interrupting in-flight jobs."""

    cached_job_service = _peek_cached_job_service()
    previous_storage_root = (
        cached_job_service.storage_root if cached_job_service is not None else None
    )
    _get_engine_query_service_cached.cache_clear()
    _get_project_inspector_cached.cache_clear()
    _get_runtime_services_cached.cache_clear()
    _get_builder_cached.cache_clear()
    _get_router_cached.cache_clear()
    _get_storage_cached.cache_clear()
    _get_cached_settings_cached.cache_clear()
    reset_settings()
    refreshed_settings = get_settings()

    if cached_job_service is None:
        return
    refreshed_storage_root = refreshed_settings.storage_root.expanduser().resolve()
    if previous_storage_root != refreshed_storage_root:
        cached_job_service.shutdown()
        _get_job_service_cached.cache_clear()
        return
    try:
        cached_job_service.refresh_runtime_settings(refreshed_settings)
    except Exception:
        cached_job_service.shutdown()
        _get_job_service_cached.cache_clear()


get_cached_settings.cache_clear = _get_cached_settings_cached.cache_clear  # type: ignore[attr-defined]
get_storage.cache_clear = _get_storage_cached.cache_clear  # type: ignore[attr-defined]
get_router.cache_clear = _get_router_cached.cache_clear  # type: ignore[attr-defined]
get_builder.cache_clear = _get_builder_cached.cache_clear  # type: ignore[attr-defined]
get_runtime_services.cache_clear = _get_runtime_services_cached.cache_clear  # type: ignore[attr-defined]
get_project_inspector.cache_clear = _get_project_inspector_cached.cache_clear  # type: ignore[attr-defined]
get_job_service.cache_clear = _get_job_service_cached.cache_clear  # type: ignore[attr-defined]
get_engine_query_service.cache_clear = (  # type: ignore[attr-defined]
    _get_engine_query_service_cached.cache_clear
)
