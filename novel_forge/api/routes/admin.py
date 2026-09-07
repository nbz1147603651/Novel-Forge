"""Admin management routes — config reload, diagnostics, error archive."""

from __future__ import annotations

from fastapi import APIRouter

from novel_forge.api.deps import reload_runtime_dependencies
from novel_forge.core.config import get_settings

router = APIRouter()


@router.post("/reload", summary="刷新运行时配置")
async def reload_config() -> dict[str, str]:
    """Clear all cached singletons so subsequent requests rebuild from latest config.

    This should be called after modifying .env or model_profiles.json.
    """
    reload_runtime_dependencies()
    return {"status": "ok", "message": "运行时配置已刷新，下次请求将使用最新配置。"}


@router.get("/error-archive/summary", summary="任务错误档案摘要")
async def error_archive_summary() -> dict[str, object]:
    """Return task-flow error archive summary across all projects."""
    from novel_forge.app_service.task_flow_error_log import TaskFlowErrorLog

    storage_root = get_settings().storage_root
    return TaskFlowErrorLog(storage_root).summary()


@router.post("/error-archive/clear", summary="清空任务错误档案")
async def error_archive_clear() -> dict[str, object]:
    """Clear all task-flow error archives."""
    from novel_forge.app_service.task_flow_error_log import TaskFlowErrorLog

    storage_root = get_settings().storage_root
    removed = TaskFlowErrorLog(storage_root).clear()
    return {"status": "ok", "removed_projects": removed, "message": f"已清空 {removed} 个项目的错误档案。"}
