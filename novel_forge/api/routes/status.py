"""Routes for checking project / system status."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from novel_forge.api.deps import (
    get_project_inspector_cached,
    get_runtime_services_cached,
    reload_runtime_dependencies,
)
from novel_forge.core.config import get_settings
from novel_forge.core.local_model_resources import get_local_model_resource_broker
from novel_forge.workspace.projects import ProjectInspector
from novel_forge.workspace.runtime import RuntimeServices, capture_runtime_config_snapshot

router = APIRouter()
RuntimeDep = Annotated[RuntimeServices, Depends(get_runtime_services_cached)]
InspectorDep = Annotated[ProjectInspector, Depends(get_project_inspector_cached)]

_API_CACHE_MODE = "singleton_auto_refresh"


def _runtime_payload(runtime: RuntimeServices) -> dict[str, object]:
    payload = runtime.config_snapshot.to_payload()
    payload["default_provider"] = runtime.router.default_provider
    payload["adapter_count"] = len(runtime.router.adapters)
    return payload


@router.get("/health")
async def health_check(
    runtime: RuntimeDep,
) -> dict[str, object]:
    """System health check."""
    observed_config = capture_runtime_config_snapshot(get_settings())
    return {
        "status": "ok",
        "adapters": list(runtime.router.adapters.keys()),
        "api_cache_mode": _API_CACHE_MODE,
        "active_runtime": _runtime_payload(runtime),
        "observed_config": observed_config.to_payload(),
        "local_model_resources": get_local_model_resource_broker().snapshot().to_payload(),
        "reload_required": runtime.config_snapshot.config_version != observed_config.config_version,
    }


@router.post("/reload")
async def reload_runtime() -> dict[str, object]:
    """Rebuild cached API dependencies from current settings and profile files."""
    previous_runtime = get_runtime_services_cached()
    reload_runtime_dependencies()
    runtime = get_runtime_services_cached()
    observed_config = capture_runtime_config_snapshot(get_settings())
    return {
        "status": "reloaded",
        "adapters": list(runtime.router.adapters.keys()),
        "api_cache_mode": _API_CACHE_MODE,
        "previous_runtime": _runtime_payload(previous_runtime),
        "active_runtime": _runtime_payload(runtime),
        "observed_config": observed_config.to_payload(),
        "changed": previous_runtime.config_snapshot.config_version != runtime.config_snapshot.config_version,
        "reload_required": runtime.config_snapshot.config_version != observed_config.config_version,
    }


@router.get("/project/{project_id}")
async def project_status(
    project_id: str,
    inspector: InspectorDep,
) -> dict[str, object]:
    """Get the status of a project."""
    try:
        detail = inspector.get_project_detail(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    return {
        "project_id": detail.project_id,
        "mode": detail.mode,
        "title": detail.title,
        "has_bible": detail.mode == "long",
        "has_outline": detail.has_outline,
        "has_canon": detail.has_canon,
        "chapter_count": detail.completed_chapters,
        "chapters": [f"chapter_{item.chapter_number:03d}" for item in detail.chapters],
        "updated_at": detail.updated_at,
        "completion_ratio": detail.completion_ratio,
        "project_state": detail.project_state,
        "project_state_label": detail.project_state_label,
        "project_state_updated_at": detail.project_state_updated_at,
        "project_state_source": detail.project_state_source,
        "allowed_operations": detail.allowed_operations,
    }
