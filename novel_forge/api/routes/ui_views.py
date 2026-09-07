"""UI composition read models and compatibility commands for replaceable clients.

These endpoints compose existing domain services into view models that
map 1:1 to the TypeScript ``EngineClient`` contract. Settings writes and their
allowlisted parameter catalog are owned by the versioned Engine boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from novel_forge.api.deps import (
    get_cached_settings,
    get_job_service,
    get_project_inspector,
    get_router,
    get_runtime_services,
    get_storage,
)
from novel_forge.api.step_artifact_resolver import (
    build_empty_artifact_hint,
    build_step_artifact_candidates,
    format_candidate_summary,
    resolve_step_artifacts,
)
from novel_forge.api.step_artifact_resolver import (
    infer_artifact_format as _infer_artifact_format,
)
from novel_forge.app_service.humanize_library import (
    humanize_pattern_view,
    load_humanize_library,
)
from novel_forge.app_service.job_service import JobService
from novel_forge.app_service.settings_environment import get_creation_parameters
from novel_forge.app_service.workflow_projection import (
    WorkflowProjectionContext,
    project_workflow_project_label,
    project_workflow_run,
)
from novel_forge.core.config import Settings
from novel_forge.core.domain.character_identity import clean_character_name
from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.workspace.projects import ProjectInspector
from novel_forge.workspace.runtime import RuntimeServices

router = APIRouter()

InspectorDep = Annotated[ProjectInspector, Depends(get_project_inspector)]
JobServiceDep = Annotated[JobService, Depends(get_job_service)]
RouterDep = Annotated[ModelRouter, Depends(get_router)]
SettingsDep = Annotated[Settings, Depends(get_cached_settings)]
StorageDep = Annotated[FileSystemStorage, Depends(get_storage)]
RuntimeDep = Annotated[RuntimeServices, Depends(get_runtime_services)]


def _planning_task_view(root: Path) -> dict[str, Any] | None:
    from novel_forge.workspace.planning_jobs import planning_job_view

    try:
        state = planning_job_view(root)
        return (
            {
                key: state.get(key, "")
                for key in ("job_id", "status", "error", "chapter_number", "target_chapter")
            }
            if state
            else None
        )
    except (OSError, ValueError):
        return {"status": "failed", "error": "规划任务记录需要检查；未重新生成正文"}


# ── Workspace ─────────────────────────────────────────────────────────────────


@router.get("/workspace")
async def get_workspace_view(
    inspector: InspectorDep,
    router_dep: RouterDep,
) -> dict[str, Any]:
    """Compose the workspace overview for the React dashboard."""
    projects = inspector.list_projects()
    total_words = 0
    init_resume: dict[str, bool] = {}
    for p in projects:
        try:
            detail = inspector.get_project_detail(p.project_id)
            total_words += sum(ch.word_count for ch in detail.chapters)
            init_resume[p.project_id] = detail.init_resume_available
        except Exception:
            init_resume[p.project_id] = False
    providers = sorted(
        {
            getattr(adapter, "provider_name", key.split(":", 1)[0])
            for key, adapter in router_dep.adapters.items()
        }
    )
    return {
        "metrics": {
            "total_projects": len(projects),
            "total_chapters": sum(p.completed_chapters for p in projects),
            "total_words": total_words,
            "configured_providers": len(providers),
        },
        "default_provider": router_dep.default_provider,
        "projects": [
            {
                "id": p.project_id,
                "title": p.title,
                "mode": p.mode,
                "status": p.project_state
                or ("completed" if p.completion_ratio >= 1.0 else "writing"),
                "status_label": p.project_state_label
                or ("已完稿" if p.completion_ratio >= 1.0 else "创作中"),
                "progress_label": f"{int(p.completion_ratio * 100)}%",
                "progress_percent": int(p.completion_ratio * 100),
                "next_action": _next_action(p),
                "updated_label": (p.updated_at or "")[:10],
                "headline": p.premise or p.preview or "",
                "genre": p.genre,
                "tone": p.tone,
                "completed_chapters": p.completed_chapters,
                "total_chapters": p.total_chapters,
                "init_resume_available": init_resume.get(p.project_id, False),
            }
            for p in projects
        ],
    }


# ── Test / empty-run artifact maintenance ────────────────────────────────────


class TestProjectCleanupBody(BaseModel):
    """Explicitly selected directories from the cleanup preview dialog."""

    project_ids: list[str] = Field(min_length=1, max_length=200)


def _cleanup_candidate_payload(candidate: Any) -> dict[str, Any]:
    return {
        "project_id": candidate.project_id,
        "reason": candidate.reason,
        "file_count": candidate.file_count,
        "size_bytes": candidate.size_bytes,
    }


@router.get("/maintenance/test-projects")
async def preview_test_project_cleanup(
    inspector: InspectorDep,
) -> dict[str, Any]:
    """Preview disposable test projects and empty generated run traces."""

    candidates = inspector.list_test_project_cleanup_candidates()
    return {
        "candidates": [_cleanup_candidate_payload(candidate) for candidate in candidates],
        "message": (
            "未发现可清理的测试或空白执行残留。"
            if not candidates
            else f"发现 {len(candidates)} 个可清理的测试或空白执行残留。"
        ),
    }


@router.post("/maintenance/test-projects/cleanup")
async def cleanup_test_projects(
    body: TestProjectCleanupBody,
    inspector: InspectorDep,
    service: JobServiceDep,
) -> dict[str, Any]:
    """Delete only reviewed cleanup candidates, never projects with active jobs."""

    jobs = service.list()
    active_project_ids = {
        record.project_id
        for record in jobs
        if str(getattr(record.state, "value", record.state)).lower() in {"queued", "running"}
    }
    result = inspector.cleanup_test_project_dirs(
        body.project_ids,
        protected_project_ids=active_project_ids,
    )
    if result.removed_project_ids:
        inactive_job_ids = {
            record.job_id
            for record in jobs
            if record.project_id in result.removed_project_ids
            and str(getattr(record.state, "value", record.state)).lower()
            not in {"queued", "running"}
        }
        if inactive_job_ids:
            service.clear_inactive_history(inactive_job_ids)
    candidates = inspector.list_test_project_cleanup_candidates()
    return {
        "removed_project_ids": list(result.removed_project_ids),
        "skipped_project_ids": list(result.skipped_project_ids),
        "candidates": [_cleanup_candidate_payload(candidate) for candidate in candidates],
        "message": (
            f"已删除 {len(result.removed_project_ids)} 个测试或空白执行残留。"
            if result.removed_project_ids
            else "没有目录被删除；它们可能正在执行，或已不再符合清理条件。"
        ),
    }


# ── Settings ──────────────────────────────────────────────────────────────────


@router.get("/settings")
async def get_settings_view(
    settings: SettingsDep,
    router_dep: RouterDep,
) -> dict[str, Any]:
    """Return credential-free settings for the React 火候 page."""
    from novel_forge.gateway.profiles import load_or_import_profiles

    config = load_or_import_profiles()
    return _compose_settings_view(
        settings=settings,
        default_provider=router_dep.default_provider,
        config=config,
    )


def _compose_settings_view(
    *,
    settings: Settings,
    default_provider: str,
    config: Any,
) -> dict[str, Any]:
    """Project the same route catalog and capabilities rendered by PySide."""

    from novel_forge.app_service.chapter_runtime_policy import (
        project_chapter_runtime_policy,
    )
    from novel_forge.core.parsing.temperature_jitter import (
        is_temperature_jitter_protected,
        parse_temperature_task_keys,
    )
    from novel_forge.core.task_catalog import (
        LONG_TEMPERATURE_TASKS,
        MULTI_TURN_TASK_KEYS,
        ROUTING_GROUPS,
        SHORT_TEMPERATURE_TASKS,
        routing_subgroup_task_keys,
    )
    from novel_forge.gateway.embedding_config import is_embedding_model
    from novel_forge.gateway.profiles import KNOWN_PROVIDERS, get_model_capabilities

    temperature_tasks = {
        task.key: task for task in (*SHORT_TEMPERATURE_TASKS, *LONG_TEMPERATURE_TASKS)
    }
    profiles = []
    for profile in config.profiles:
        supports_thinking, supports_multi_turn = get_model_capabilities(
            profile.provider,
            profile.model_id,
        )
        profiles.append(
            {
                "id": profile.profile_id,
                "label": profile.display_name,
                "provider": profile.provider,
                "model": profile.model_id,
                "tier_label": _tier_label(profile),
                "status_label": "已配置" if profile.is_key_configured else "未配置 Key",
                "supports_thinking": supports_thinking,
                "supports_multi_turn": supports_multi_turn,
                "masked_key": profile.masked_key,
                "key_configured": profile.is_key_configured,
                "base_url": profile.base_url,
                "is_embedding": is_embedding_model(profile.provider, profile.model_id),
            }
        )

    routing_groups: list[dict[str, Any]] = []
    for group in ROUTING_GROUPS:
        subgroups = [
            {
                "id": f"{group.name}/{subgroup.title}",
                "label": subgroup.title,
                "description": subgroup.description,
                "route_ids": list(routing_subgroup_task_keys(group, subgroup)),
            }
            for subgroup in group.subgroups
        ]
        routes = []
        for task in group.tasks:
            current_route = config.routes.get(task.key)
            temperature_task = temperature_tasks.get(task.key)
            temperature = (
                float(getattr(settings, temperature_task.setting_attr))
                if temperature_task is not None
                else None
            )
            routes.append(
                {
                    "id": task.key,
                    "task_key": task.key,
                    "label": task.label,
                    "hint": task.hint,
                    "primary_profile_id": (
                        current_route.profile_id
                        if current_route is not None
                        else config.default_profile_id
                    ),
                    "fallback_routes": [
                        {
                            "profile_id": fallback.profile_id,
                            "thinking_enabled": fallback.thinking,
                            "multi_turn_enabled": fallback.multi_turn,
                        }
                        for fallback in config.fallback_routes.get(task.key, [])
                    ],
                    "thinking_enabled": current_route.thinking
                    if current_route is not None
                    else False,
                    "multi_turn_enabled": (
                        current_route.multi_turn if current_route is not None else False
                    ),
                    "temperature": temperature,
                    "temperature_kind": (
                        "fixed"
                        if temperature_task is not None
                        and is_temperature_jitter_protected(task.task_type)
                        else "base"
                        if temperature_task is not None
                        else None
                    ),
                    "supports_multi_turn": task.key in MULTI_TURN_TASK_KEYS,
                    "temperature_jitter_protected": is_temperature_jitter_protected(task.task_type),
                }
            )
        routing_groups.append(
            {
                "id": group.name,
                "label": group.name,
                "description": group.description,
                "icon": group.icon,
                "subgroups": subgroups,
                "routes": routes,
            }
        )

    is_mock = getattr(settings, "mock_mode", False) or default_provider == "mock"
    return {
        "active_theme_id": getattr(settings, "desktop_theme", "narrative_ember"),
        "font_preferences": {
            "ui_family": getattr(settings, "desktop_ui_font_family", "source_sans"),
            "reading_family": getattr(settings, "desktop_reading_font_family", "source_serif"),
            "scale": getattr(settings, "desktop_font_scale", 1.0),
        },
        "default_profile_id": config.default_profile_id,
        "default_provider": default_provider,
        "storage_root_label": str(getattr(settings, "storage_root", "~")),
        "mock_mode": is_mock,
        "model_profiles": profiles,
        "model_provider_options": [
            {
                "id": provider_id,
                "label": str(value.get("label", provider_id)),
                "models": list(value.get("models", [])),
                "api_key_required": provider_id != "ollama",
                "default_base_url": str(value.get("default_base_url", "")),
                "connection_hint": str(value.get("connection_hint", "")),
            }
            for provider_id, value in KNOWN_PROVIDERS.items()
        ]
        + [
            {
                "id": "custom",
                "label": "自定义 OpenAI 兼容",
                "models": [],
                "api_key_required": True,
            }
        ],
        "routing_groups": routing_groups,
        "creative_temperature": {
            "enabled": settings.creative_temperature_jitter_enabled,
            "scope": settings.creative_temperature_jitter_scope,
            "down_delta": settings.creative_temperature_jitter_down_delta,
            "up_delta": settings.creative_temperature_jitter_up_delta,
            "custom_task_keys": [
                task.value
                for task in parse_temperature_task_keys(
                    settings.creative_temperature_jitter_custom_tasks
                )
            ],
        },
        "creation_parameters": get_creation_parameters(settings),
        "chapter_runtime_policy": project_chapter_runtime_policy(settings),
    }


# ── Voice Platform / model center ───────────────────────────────────────────


_MODEL_STATE_LABELS = {
    "external": "Sidecar 管理",
    "incomplete": "不完整",
    "installed": "已安装",
    "not_installed": "未安装",
}
_RUNTIME_STATE_LABELS = {
    "failed": "异常",
    "incompatible": "不兼容",
    "installed": "已安装",
    "not_installed": "未安装",
    "running": "运行中",
    "stopped": "已停止",
}
_MODEL_CENTER_TERMINAL_STATES = {"cancelled", "completed", "failed"}
_MODEL_CENTER_MODEL_OPERATIONS = {"accept_license", "delete", "install", "self_test"}
_MODEL_CENTER_RUNTIME_OPERATIONS = {
    "install_runtime",
    "reconcile_runtime",
    "rollback_runtime",
    "start_runtime",
    "stop_runtime",
    "upgrade_runtime",
}


class VoiceAudioModelOperationBody(BaseModel):
    """A guarded request for a catalogued model-center operation.

    ``token`` is deliberately request-scoped: it is passed to Hugging Face for
    this one download and is never persisted or included in a read model.
    """

    target_kind: Literal["model", "runtime"]
    target_id: str = Field(min_length=1, max_length=160)
    operation: str = Field(min_length=1, max_length=80)
    token: str = Field(default="", max_length=8192)
    accept_license: bool = False
    force: bool = False


@dataclass
class _VoiceAudioModelOperation:
    operation_id: str
    target_kind: Literal["model", "runtime"]
    target_id: str
    operation: str
    status: Literal["cancelled", "completed", "failed", "queued", "running"] = "queued"
    message: str = "等待模型中心队列…"
    percent: int = -1
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    cancel_requested: threading.Event = field(default_factory=threading.Event, repr=False)


class _VoiceAudioModelOperationRegistry:
    """Small in-process queue for cancellable model/runtime lifecycle work.

    The PySide page uses the same ``AudioModelCenterService`` but drives it
    through worker objects.  The local Engine has no durable JobKind for
    application-scoped model files, so this queue supplies the equivalent
    status/progress boundary without pretending those downloads belong to a
    project.  It keeps the PySide limit of two concurrent downloads.
    """

    _MAX_CONCURRENT_OPERATIONS = 2
    _MAX_RETAINED_OPERATIONS = 128

    def __init__(self) -> None:
        self._operations: dict[str, _VoiceAudioModelOperation] = {}
        self._semaphores: dict[int, asyncio.Semaphore] = {}

    def _semaphore(self) -> asyncio.Semaphore:
        loop_key = id(asyncio.get_running_loop())
        semaphore = self._semaphores.get(loop_key)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self._MAX_CONCURRENT_OPERATIONS)
            self._semaphores = {loop_key: semaphore}
        return semaphore

    def start(
        self,
        body: VoiceAudioModelOperationBody,
        *,
        settings: Settings,
    ) -> _VoiceAudioModelOperation:
        _validate_voice_audio_model_operation(body)
        for existing in self._operations.values():
            if (
                existing.target_kind == body.target_kind
                and existing.target_id == body.target_id
                and existing.operation == body.operation
                and existing.status not in _MODEL_CENTER_TERMINAL_STATES
            ):
                return existing
        self._trim_terminal_operations()
        record = _VoiceAudioModelOperation(
            operation_id=uuid4().hex,
            target_kind=body.target_kind,
            target_id=body.target_id,
            operation=body.operation,
        )
        self._operations[record.operation_id] = record
        record.task = asyncio.create_task(self._run(record, body=body, settings=settings))
        return record

    def get(self, operation_id: str) -> _VoiceAudioModelOperation | None:
        return self._operations.get(operation_id)

    def cancel(self, operation_id: str) -> _VoiceAudioModelOperation | None:
        record = self._operations.get(operation_id)
        if record is None or record.status in _MODEL_CENTER_TERMINAL_STATES:
            return record
        record.cancel_requested.set()
        record.message = "正在取消…"
        return record

    async def _run(
        self,
        record: _VoiceAudioModelOperation,
        *,
        body: VoiceAudioModelOperationBody,
        settings: Settings,
    ) -> None:
        from novel_forge.tts.model_center.manager import AudioModelDownloadCancelled
        from novel_forge.tts.model_center.service import AudioModelCenterService

        async with self._semaphore():
            if record.cancel_requested.is_set():
                record.status = "cancelled"
                record.message = "下载已取消。"
                return
            record.status = "running"
            record.message = "正在准备任务…"
            record.percent = 0

            def report(message: str, percent: int) -> None:
                if record.status in _MODEL_CENTER_TERMINAL_STATES:
                    return
                record.message = message.strip() or "正在执行模型中心任务…"
                record.percent = max(-1, min(100, int(percent)))

            try:
                service = AudioModelCenterService(settings)
                if body.target_kind == "model":
                    if body.operation == "install":
                        await service.install(
                            body.target_id,
                            token=body.token,
                            accept_license=body.accept_license,
                            progress=report,
                            should_cancel=record.cancel_requested.is_set,
                        )
                        message = "模型已下载、校验并登记。"
                    elif body.operation == "accept_license":
                        await service.accept_license(body.target_id)
                        message = "已记录模型许可确认。"
                    elif body.operation == "delete":
                        await service.delete(body.target_id, force=body.force)
                        message = "模型已删除。"
                    else:  # self_test is validated above.
                        result = await service.self_test(body.target_id)
                        message = "自检通过。" if result.get("ok") else "自检未通过。"
                else:
                    result = await service.runtime_operation(
                        body.target_id,
                        body.operation,
                        progress=report,
                    )
                    state = str(result.get("state") or "")
                    message = (
                        f"运行时操作完成：{_RUNTIME_STATE_LABELS.get(state, state or '已完成')}。"
                    )
                if record.cancel_requested.is_set() and body.operation == "install":
                    record.status = "cancelled"
                    record.message = "下载已取消。"
                    return
                record.status = "completed"
                record.message = message
                record.percent = 100
            except AudioModelDownloadCancelled as exc:
                record.status = "cancelled"
                record.message = str(exc).strip() or "下载已取消。"
                record.percent = -1
            except Exception as exc:
                record.status = "failed"
                record.message = str(exc).strip() or type(exc).__name__
                record.percent = -1

    def _trim_terminal_operations(self) -> None:
        if len(self._operations) < self._MAX_RETAINED_OPERATIONS:
            return
        terminal = [
            operation_id
            for operation_id, item in self._operations.items()
            if item.status in _MODEL_CENTER_TERMINAL_STATES
        ]
        for operation_id in terminal[: len(self._operations) - self._MAX_RETAINED_OPERATIONS + 1]:
            self._operations.pop(operation_id, None)


_VOICE_AUDIO_MODEL_OPERATIONS = _VoiceAudioModelOperationRegistry()


def _validate_voice_audio_model_operation(body: VoiceAudioModelOperationBody) -> None:
    allowed = (
        _MODEL_CENTER_MODEL_OPERATIONS
        if body.target_kind == "model"
        else _MODEL_CENTER_RUNTIME_OPERATIONS
    )
    if body.operation not in allowed:
        raise HTTPException(status_code=422, detail="模型中心操作与目标类型不匹配。")


def _voice_audio_model_operation_view(
    record: _VoiceAudioModelOperation,
) -> dict[str, str | int]:
    return {
        "id": record.operation_id,
        "target_kind": record.target_kind,
        "target_id": record.target_id,
        "operation": record.operation,
        "status": record.status,
        "message": record.message,
        "percent": record.percent,
    }


def _voice_audio_model_view(status: Any) -> dict[str, Any]:
    descriptor = status.descriptor
    state = str(getattr(status.state, "value", status.state))
    runtime_healthy = status.runtime_healthy
    return {
        "id": descriptor.plugin_id,
        "name": descriptor.display_name,
        "roles": list(descriptor.roles),
        "state": state,
        "state_label": _MODEL_STATE_LABELS.get(state, state),
        "family": descriptor.family,
        "recommended_for": descriptor.recommended_for,
        "detail": status.detail,
        "compatible": status.compatible,
        "compatibility_reason": status.compatibility_reason,
        "installed_size_bytes": status.installed_size_bytes,
        "estimated_download_bytes": descriptor.estimated_download_bytes,
        "runtime_status": (
            "healthy"
            if runtime_healthy is True
            else "unavailable"
            if runtime_healthy is False
            else "unchecked"
        ),
        "runtime_version": status.runtime_version,
        "installed_revision": status.installed_revision,
        "local_path": status.local_path,
        "project_references": list(status.project_references),
        "requires_license_acceptance": descriptor.requires_license_acceptance,
        "license_name": descriptor.license_name,
        "license_url": descriptor.license_url,
        "license_accepted": status.license_accepted,
        "self_test_passed": status.self_test_passed,
    }


def _voice_audio_runtime_view(payload: dict[str, Any]) -> dict[str, Any]:
    descriptor = payload.get("descriptor") or {}
    state = payload.get("state") or {}
    state_id = str(state.get("state") or "not_installed")
    target_version = str(descriptor.get("version") or "")
    version = str(state.get("version") or "")
    version_detail = (
        f"当前版本 {version or target_version}"
        if state.get("environment_path")
        else f"目标版本 {target_version}"
    )
    return {
        "id": str(descriptor.get("runtime_id") or ""),
        "name": str(descriptor.get("display_name") or "本地运行时"),
        "detail": (
            f"Python {descriptor.get('python_constraint') or '—'} · "
            f"端口 {descriptor.get('port') or '—'} · {version_detail}"
        ),
        "state": state_id,
        "status": _RUNTIME_STATE_LABELS.get(state_id, state_id),
        "version": version,
        "target_version": target_version,
        "managed_process": bool(descriptor.get("managed_process")),
        "rollback_available": bool(state.get("rollback_available")),
        "last_error": str(state.get("last_error") or ""),
    }


async def _voice_audio_model_center_view(settings: Settings) -> dict[str, Any]:
    from novel_forge.tts.model_center.service import AudioModelCenterService

    service = AudioModelCenterService(settings)
    statuses = await service.list_models()
    runtimes = await service.list_runtimes()
    repository = await service.repository_status(statuses)
    models = [_voice_audio_model_view(status) for status in statuses]
    return {
        "installed_model_count": sum(item["state"] == "installed" for item in models),
        "model_count": len(models),
        "repository": {
            "root": str(repository.get("root") or ""),
            "size_bytes": int(repository.get("size_bytes") or 0),
            "free_bytes": int(repository.get("free_bytes") or 0),
            "rollback_available": bool(repository.get("rollback_available")),
        },
        "models": models,
        "runtimes": [_voice_audio_runtime_view(payload) for payload in runtimes],
    }


@router.get("/voice-platform/model-center")
async def get_voice_audio_model_center(settings: SettingsDep) -> dict[str, Any]:
    """Read the same application-scoped model catalog used by PySide."""

    return await _voice_audio_model_center_view(settings)


@router.get("/voice-platform/runtimes")
async def get_voice_platform_runtimes(settings: SettingsDep) -> list[dict[str, Any]]:
    """Compatibility projection for a standalone runtime check button."""

    center = await _voice_audio_model_center_view(settings)
    return list(center["runtimes"])


@router.post("/voice-platform/model-center/operations")
async def start_voice_audio_model_operation(
    body: VoiceAudioModelOperationBody,
    settings: SettingsDep,
) -> dict[str, str | int]:
    """Start a cancellable, application-scoped model or runtime operation."""

    record = _VOICE_AUDIO_MODEL_OPERATIONS.start(body, settings=settings)
    return _voice_audio_model_operation_view(record)


@router.get("/voice-platform/model-center/operations/{operation_id}")
async def get_voice_audio_model_operation(operation_id: str) -> dict[str, str | int]:
    record = _VOICE_AUDIO_MODEL_OPERATIONS.get(operation_id)
    if record is None:
        raise HTTPException(status_code=404, detail="未找到模型中心任务。")
    return _voice_audio_model_operation_view(record)


@router.post("/voice-platform/model-center/operations/{operation_id}/cancel")
async def cancel_voice_audio_model_operation(operation_id: str) -> dict[str, str | int]:
    record = _VOICE_AUDIO_MODEL_OPERATIONS.cancel(operation_id)
    if record is None:
        raise HTTPException(status_code=404, detail="未找到模型中心任务。")
    return _voice_audio_model_operation_view(record)


# ── Workflow ──────────────────────────────────────────────────────────────────


def _workflow_chapter_number(record: Any) -> int:
    """Recover a chapter context using the same sources as the PySide card."""

    payloads: list[object] = [
        getattr(record, "current_step_payload", {}),
        getattr(record, "result", {}),
    ]
    payloads.extend(
        getattr(event, "payload", {})
        for event in reversed(list(getattr(record, "events", []) or []))
    )
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key in ("chapter_number", "chapter"):
            try:
                chapter = int(payload.get(key, 0) or 0)
            except (TypeError, ValueError):
                continue
            if chapter > 0:
                return chapter

    match = re.search(r"第\s*(\d+)\s*章", str(getattr(record, "label", "") or ""))
    return int(match.group(1)) if match is not None else 0


def _workflow_error_log_view(entry: dict[str, Any]) -> dict[str, Any]:
    """Project one persisted diagnostic into the public TypeScript contract."""

    def text(key: str, *, limit: int = 1600) -> str:
        value = " ".join(str(entry.get(key) or "").split()).strip()
        return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"

    auto_resolved = entry.get("auto_resolved", False)
    return {
        "id": text("id", limit=200),
        "time_label": text("time", limit=80),
        "job_label": text("job_label", limit=240),
        "task_id": text("job_id", limit=200),
        "task_label": text("task", limit=240),
        "attempt_label": text("attempt", limit=80),
        "error_message": text("error", limit=600),
        "excerpt": text("excerpt", limit=1600),
        "log_path": text("log_path", limit=2000),
        "kind_label": text("kind", limit=120) or "任务失败",
        "auto_resolved": auto_resolved is True or str(auto_resolved).casefold() == "true",
        "acknowledged_at": text("acknowledged_at", limit=80),
        "cause_code": text("cause_code", limit=120),
        "auto_repair_state": text("auto_repair_state", limit=80) or "unknown",
        "auto_repair_explanation": text("auto_repair_explanation", limit=600),
        "recommended_action": text("recommended_action", limit=400),
        "recovery_action_kinds": [
            str(item) for item in (entry.get("recovery_action_kinds") or []) if str(item).strip()
        ][:12],
    }


def _workflow_run_view(
    record: Any,
    *,
    projection_context: WorkflowProjectionContext | None = None,
    storage_root: Path | None = None,
) -> dict[str, Any]:
    """Project one task record without leaking internal engine event names."""
    projection = project_workflow_run(
        record,
        storage_root=storage_root,
        context=projection_context,
    )
    raw_kind = getattr(record, "kind", "")
    kind = str(getattr(raw_kind, "value", raw_kind) or "")
    project_label = project_workflow_project_label(
        record,
        storage_root=storage_root,
        context=projection_context,
    )
    return {
        "id": record.job_id,
        # The task card is an author surface: its headline names the work,
        # while project_id stays available solely for commands and artifacts.
        "title": project_label or record.label,
        "project_id": record.project_id,
        "project_label": project_label,
        "elapsed_label": "",
        "progress_percent": projection["progress_percent"],
        "state_label": str(getattr(record.status, "value", record.status)),
        # The visible stage is the public task label.  Details remain available
        # in the task stream; this keeps cache/retry diagnostics out of every
        # progress surface and makes the label localizable by the web client.
        "current_stage_label": projection["current_stage_label"],
        # Engine-formatted Chinese step label with per-step detail (batch x/y,
        # verdict, etc.); surfaces fall back to it when the stage sequence
        # does not carry the active milestone.
        "step_label": projection["step_label"],
        "validation_label": "",
        "activity_label": projection["activity_label"],
        "stages": projection["stages"],
        "kind": kind,
        "init_repair_available": any(
            action.get("kind") == "retry_init_repair" and action.get("enabled") is True
            for action in projection["recovery_actions"]
        ),
        "has_checkpoint": projection["checkpoint"]["exists"],
        "chapter_number": _workflow_chapter_number(record) or None,
        **{
            key: projection[key]
            for key in (
                "workflow_projection_version",
                "workflow_version",
                "stage_id",
                "actual_state",
                "input_signature",
                "output_version",
                "parent_artifact_versions",
                "quality_status",
                "degradation_reason",
                "derivation_status",
                "retryable",
                "recovery_actions",
                "checkpoint",
                "stale_dependencies",
                "cumulative_tokens",
                "cumulative_cost_usd",
                "run_insights",
                "efficiency",
            )
        },
    }


@router.get("/workflow")
async def get_workflow_view(
    job_service: JobServiceDep,
) -> dict[str, Any]:
    """Compose the workflow page read model."""
    records = job_service.list()
    error_entries = job_service.list_error_log()
    error_log = [_workflow_error_log_view(entry) for entry in error_entries]
    storage_root = getattr(job_service, "storage_root", None)
    projection_context = WorkflowProjectionContext(storage_root=storage_root)
    runs = [
        _workflow_run_view(record, projection_context=projection_context) for record in records[:20]
    ]
    return {
        "error_count": sum(
            1 for entry in error_log if not entry["auto_resolved"] and not entry["acknowledged_at"]
        ),
        "error_log": error_log,
        "runs": runs,
        "focus": {
            "kind_label": "空闲",
            "status_label": "无活动任务",
            "title": "当前无聚焦任务",
            "summary": "启动工作流或准备章节后，任务焦点将显示在此处。",
            "fragment": "",
        },
    }


# ── Step Artifacts ─────────────────────────────────────────────────────────────

# Artifact resolution lives in ``novel_forge.api.step_artifact_resolver``,
# which mirrors the PySide6 ``novel_forge.desktop.pages.workflow.artifacts``
# mapping, draft-version auto-enumeration, init-story-bible fragment
# fallback, short-story edit/segment wildcards, volume-audit pattern
# matching, and profile_style failure diagnostics.  ``_infer_artifact_format``
# is re-exported from there to keep route signatures stable.

# Character budget for inlined artifact content (kept in sync with the
# ``... (内容已截断)`` truncation marker consumed by the React viewer).
_ARTIFACT_CONTENT_LIMIT = 500_000


def _format_artifact_size(size_bytes: int) -> str:
    """Render a byte count as a short human-readable label."""
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def _load_artifact_content(file_path: Path, format_: str) -> str:
    """Read artifact text with a bounded budget; never decode binaries.

    ``binary`` assets (audio, archives, office documents) are not safe to
    inline, so the resolver's format sentinel short-circuits to a size
    placeholder instead of attempting a UTF-8 decode.
    """
    if format_ == "binary":
        try:
            size = file_path.stat().st_size
        except OSError:
            size = 0
        return f"[二进制文件 · {_format_artifact_size(size)} · 暂不支持在线预览]"
    try:
        with file_path.open(encoding="utf-8") as handle:
            content = handle.read(_ARTIFACT_CONTENT_LIMIT + 1)
    except (OSError, UnicodeDecodeError):
        return f"[无法读取文件: {file_path.name}]"
    if len(content) > _ARTIFACT_CONTENT_LIMIT:
        return content[:_ARTIFACT_CONTENT_LIMIT] + "\n\n... (内容已截断)"
    return content


def _artifact_word_count(file_path: Path, artifact_path: str, format_: str) -> int | None:
    """Return the source count for a chapter-prose artifact, when available.

    Keep this deliberately narrower than "all Markdown": task artifacts also
    include plans and reports, for which a prose count would be misleading.
    """
    if format_ != "markdown" or not re.fullmatch(
        r"(?:chapters/chapter_\d+|drafts/chapter_\d+/v[^/]+)\.md", artifact_path
    ):
        return None
    try:
        return count_chapter_words(file_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return None


@router.get("/projects/{project_id}/step-artifacts")
async def get_step_artifacts(
    project_id: str,
    kind: str,
    step_key: str,
    storage: StorageDep,
    chapter_number: int = 0,
) -> dict[str, Any]:
    """Return artifact files produced by a pipeline step.

    Resolves every recognized file (including PySide6-style wildcard
    collections such as ``book_consistency_audit_*.json`` or
    ``drafts/chapter_NNN/v*.md``) through the shared step-artifact resolver
    so the React/Tauri viewer mirrors the PySide6 dialog.
    """
    project_dir = storage.project_path(project_id)
    if not project_dir.is_dir():
        raise HTTPException(status_code=404, detail="项目目录不存在")

    entries = resolve_step_artifacts(kind, step_key, project_dir, chapter_number)

    artifacts: list[dict[str, Any]] = []
    for label, file_path in entries:
        artifact_path = file_path.relative_to(project_dir).as_posix()
        format_ = _infer_artifact_format(artifact_path)
        artifact: dict[str, Any] = {
            "label": label,
            "path": artifact_path,
            "content": _load_artifact_content(file_path, format_),
            "format": format_,
        }
        word_count = _artifact_word_count(file_path, artifact_path, format_)
        if word_count is not None:
            artifact["word_count"] = word_count
        artifacts.append(artifact)

    candidates: list[tuple[str, Path]] = []
    candidate_paths: list[dict[str, str]] = []
    if not artifacts:
        candidates = build_step_artifact_candidates(kind, step_key, project_dir, chapter_number)
        candidate_paths = [
            {"label": label, "path": path.relative_to(project_dir).as_posix()}
            for label, path in candidates
        ]

    empty_hint = ""
    if not artifacts:
        diagnostic = build_empty_artifact_hint(kind, step_key, project_dir)
        candidate_block = format_candidate_summary(candidates, project_dir)
        blocks = [block for block in (diagnostic, candidate_block) if block]
        if candidate_block:
            blocks[-1] = f"已检查候选产物：\n{candidate_block}"
        empty_hint = (
            "\n".join(blocks) if blocks else ("该步骤的产物文件尚未生成，任务完成后将自动出现。")
        )

    return {
        "project_id": project_id,
        "kind": kind,
        "step_key": step_key,
        "artifacts": artifacts,
        "candidate_paths": candidate_paths,
        "empty_hint": empty_hint,
    }


# ── Project Reader ────────────────────────────────────────────────────────────


# Keep this catalog in lockstep with ``ProjectsPage`` in the PySide client.  The
# API owns the same hierarchy so a React client does not fall back to a reduced
# three-tab reader merely because an artifact is not generated yet.
_LONG_READER_GROUPS: tuple[tuple[str, str, tuple[tuple[str, str, str], ...]], ...] = (
    (
        "foundation",
        "基础设定",
        (
            ("spec", "故事规格", "spec.json"),
            ("world", "世界观", "story_bible.json"),
            ("characters", "角色与实体", "character_bible.json"),
            ("elements", "要素与风格", "plans/blueprint_elements_selection.json"),
        ),
    ),
    (
        "research",
        "资料检索",
        (
            ("web_research", "资料检索报告", "reports/init_web_research.json"),
            ("research_dossier", "资料分析报告", "reports/init_research_dossier.json"),
            ("outline_research", "大纲资料校准", "reports/outline_research_grounding.json"),
        ),
    ),
)

_LONG_READER_DIRECT_TABS: tuple[tuple[str, str, str], ...] = (
    ("blueprint", "叙事蓝图", "plans/narrative_blueprint.json"),
    ("chapter_design", "章节设计矩阵", "plans/chapter_design_matrix.json"),
    ("outline", "章节大纲", "outline.json"),
)

_CHAPTER_READER_REPORTS: tuple[tuple[str, str], ...] = (
    ("质量评估", "reports/chapter_{chapter:03d}_eval.json"),
    ("创作总结", "reports/chapter_{chapter:03d}_creative.json"),
    ("对齐报告", "reports/chapter_{chapter:03d}_alignment.json"),
    ("连贯性", "reports/chapter_{chapter:03d}_continuity.json"),
    ("因果链", "reports/chapter_{chapter:03d}_causal.json"),
    ("追读力", "reports/chapter_{chapter:03d}_reading_power.json"),
    ("知识边界", "reports/chapter_{chapter:03d}_knowledge_boundary_verification.json"),
    ("护栏", "reports/chapter_{chapter:03d}_guard.json"),
    ("质量门禁", "reports/chapter_{chapter:03d}_quality_gate.json"),
    ("控制", "reports/chapter_{chapter:03d}_stage_visibility.json"),
    ("表达", "reports/chapter_{chapter:03d}_expression_repetition.json"),
    ("拟人化", "reports/chapter_{chapter:03d}_humanize.json"),
    ("拟人化对比", "reports/revisions/chapter_{chapter:03d}_humanize_layer.json"),
    ("润色对比", "reports/revisions/chapter_{chapter:03d}_polish_chapter.json"),
)

_GOVERNANCE_READER_DOCUMENTS: tuple[tuple[str, str], ...] = (
    ("一致性审计", "reports/book_consistency_audit.json"),
    ("出版编辑审查", "reports/book_editorial_audit.json"),
    ("修复报告", "reports/book_consistency_repair_report.json"),
    ("初始化准入", "reports/init_readiness.json"),
    ("编辑契约准入", "reports/init_editorial_readiness.json"),
    ("初始化修复", "reports/init_artifact_repair.json"),
    ("创意精炼", "reports/init_creative_refinement.json"),
    ("一致性画像", "reports/init_coherence_profile.json"),
    ("一致性 Claims", "memory/init_coherence_claims.jsonl"),
    ("一致性 Claims 账本", "memory/init_coherence_claim_ledger.json"),
    ("一致性索引", "memory/init_coherence_index.json"),
    ("冲突候选", "reports/init_conflict_candidates.json"),
    ("冲突裁决", "reports/init_conflict_adjudication.json"),
    ("一致性 Claims 契约覆盖", "reports/init_claim_contract_coverage.json"),
)


@router.get("/projects/{project_id}/reader")
async def get_project_reader_view(
    project_id: str,
    inspector: InspectorDep,
    storage: StorageDep,
) -> dict[str, Any]:
    """Compose the complete source-reader catalog from stored artifacts."""
    try:
        detail = inspector.get_project_detail(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc

    from novel_forge.persistence.models import ProjectLayout

    layout = ProjectLayout(storage.project_path(project_id))
    tabs = (
        _long_project_reader_tabs(storage, layout, detail)
        if detail.mode == "long"
        else _short_project_reader_tabs(storage, layout)
    )

    return {
        "project_id": project_id,
        "project_title": detail.title,
        "mode_label": "长篇项目" if detail.mode == "long" else "短篇项目",
        "updated_label": (detail.updated_at or "")[:10],
        "tabs": tabs,
    }


def _chapter_draft_artifacts(
    storage: FileSystemStorage, layout: Any, chapter_number: int
) -> list[dict[str, Any]]:
    """Build version artifacts for a chapter's drafts (drafts/chapter_NNN/v*.md).

    Mirrors PySide6 `_fill_chapter_text`, which lists the final text plus every
    draft snapshot as selectable version tabs. Labels reuse the source's
    `draft_stem_display_label` wording (DRAFT 原稿 / 初稿成章 / 第N轮润色 …).
    """
    from novel_forge.core.utils.version_diff import draft_stem_display_label

    draft_dir = layout.chapter_draft_dir(chapter_number)
    artifacts: list[dict[str, Any]] = []
    if not draft_dir.is_dir():
        return artifacts
    for draft_path in sorted(draft_dir.glob("v*.md")):
        stem = draft_path.stem
        version_label = draft_stem_display_label(stem)
        relative_path = f"drafts/chapter_{chapter_number:03d}/{draft_path.name}"
        word_count = _reader_prose_word_count(storage, draft_path)
        artifacts.append(
            _reader_file_artifact(
                storage,
                layout,
                f"chapter-{chapter_number}-draft-{stem}",
                version_label,
                relative_path,
                caption=(
                    f"{version_label} · {word_count:,} 字" if word_count > 0 else version_label
                ),
                facts=(
                    [{"label": "实际字数", "value": f"{word_count:,}"}] if word_count > 0 else []
                ),
            )
        )
    return artifacts


def _reader_chapter_catalog(
    storage: FileSystemStorage,
    layout: Any,
    detail: Any,
) -> list[tuple[int, str]]:
    """Return the PySide reader's chapter rail, independent of final prose.

    ``ProjectsPage._populate_chapter_list`` uses the outline first and falls
    back to archived chapter summaries only when no outline is available.  The
    React reader must keep that same separation: a chapter can exist in the
    rail while its readable versions come only from ``drafts/``.
    """

    outline_payload: Any = {}
    if layout.outline_path.is_file():
        try:
            outline_payload = storage.load_json(layout.outline_path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            outline_payload = {}
    raw_chapters = outline_payload.get("chapters", []) if isinstance(outline_payload, dict) else []
    catalog: list[tuple[int, str]] = []
    seen: set[int] = set()
    if isinstance(raw_chapters, list):
        for raw in raw_chapters:
            if not isinstance(raw, dict):
                continue
            try:
                chapter_number = int(raw.get("chapter_number", 0) or 0)
            except (TypeError, ValueError):
                continue
            if chapter_number < 1 or chapter_number in seen:
                continue
            title = str(raw.get("title") or f"第{chapter_number}章").strip()
            catalog.append((chapter_number, title))
            seen.add(chapter_number)
    if catalog:
        return catalog
    return [
        (chapter.chapter_number, chapter.title or f"第{chapter.chapter_number}章")
        for chapter in detail.chapters
    ]


def _reader_has_usable_final(path: Path, storage: FileSystemStorage) -> bool:
    """Mirror PySide's rule that a tiny/empty final file is not a prose tab."""

    if not path.is_file():
        return False
    try:
        return len(storage.load_text(path).strip()) > 100
    except (OSError, UnicodeError):
        return False


def _reader_prose_word_count(storage: FileSystemStorage, path: Path) -> int:
    """Count stored prose, never the possibly stale project-summary value."""
    try:
        return count_chapter_words(storage.load_text(path)) if path.is_file() else 0
    except (OSError, UnicodeError):
        return 0


def _reader_latest_draft_word_count(
    storage: FileSystemStorage, layout: Any, chapter_number: int
) -> int:
    draft_dir = layout.chapter_draft_dir(chapter_number)
    try:
        paths = sorted(draft_dir.glob("v*.md"))
    except OSError:
        return 0
    if not paths:
        return 0
    try:
        latest_path = max(paths, key=lambda path: (path.stat().st_mtime_ns, path.name))
    except OSError:
        return 0
    return _reader_prose_word_count(storage, latest_path)


def _long_project_reader_tabs(
    storage: FileSystemStorage,
    layout: Any,
    detail: Any,
) -> list[dict[str, Any]]:
    from novel_forge.app_service.token_analytics import (
        collect_project_token_analytics,
    )

    tabs: list[dict[str, Any]] = []
    for tab_id, label, documents in _LONG_READER_GROUPS:
        tabs.append(
            {
                "id": tab_id,
                "label": label,
                "artifacts": [
                    _reader_file_artifact(
                        storage, layout, artifact_id, artifact_label, relative_path
                    )
                    for artifact_id, artifact_label, relative_path in documents
                ],
            }
        )
    for tab_id, label, relative_path in _LONG_READER_DIRECT_TABS:
        tabs.append(
            {
                "id": tab_id,
                "label": label,
                "artifacts": [_reader_file_artifact(storage, layout, tab_id, label, relative_path)],
            }
        )

    chapter_artifacts: list[dict[str, Any]] = []
    chapter_entries: list[dict[str, Any]] = []
    for chapter_number, chapter_title in _reader_chapter_catalog(storage, layout, detail):
        chapter_path = layout.chapter_path(chapter_number)
        has_final = _reader_has_usable_final(chapter_path, storage)
        word_count = _reader_prose_word_count(storage, chapter_path) if has_final else 0
        if has_final:
            chapter_artifacts.append(
                _reader_file_artifact(
                    storage,
                    layout,
                    f"chapter-{chapter_number}",
                    f"第 {chapter_number} 章 · {chapter_title}",
                    f"chapters/chapter_{chapter_number:03d}.md",
                    caption=f"{word_count} 字" if word_count > 0 else "终稿",
                    facts=(
                        [{"label": "实际字数", "value": f"{word_count:,}"}]
                        if word_count > 0
                        else []
                    ),
                )
            )
        # Draft versions (PySide6 `_fill_chapter_text` shows 终稿 + drafts as
        # version tabs). Scan drafts/chapter_{n:03d}/v*.md.
        draft_artifacts = _chapter_draft_artifacts(storage, layout, chapter_number)
        chapter_artifacts.extend(draft_artifacts)
        current_word_count = word_count or _reader_latest_draft_word_count(
            storage, layout, chapter_number
        )
        chapter_entries.append(
            {
                "number": chapter_number,
                "title": chapter_title,
                "state": "final" if has_final else ("draft" if draft_artifacts else "pending"),
                **({"word_count": current_word_count} if current_word_count > 0 else {}),
            }
        )
        for report_label, template in _CHAPTER_READER_REPORTS:
            relative_path = template.format(chapter=chapter_number)
            report_path = layout.root / relative_path
            if not storage.exists(report_path):
                continue
            chapter_artifacts.append(
                _reader_file_artifact(
                    storage,
                    layout,
                    f"chapter-{chapter_number}-report-{_reader_slug(report_label)}",
                    f"第 {chapter_number} 章 · {report_label}",
                    relative_path,
                    caption=report_label,
                )
            )
    tabs.append(
        {
            "id": "chapters",
            "label": "章节",
            "chapters": chapter_entries,
            "artifacts": chapter_artifacts,
        }
    )

    governance_artifacts = [
        _reader_file_artifact(
            storage, layout, f"governance-{_reader_slug(label)}", label, relative_path
        )
        for label, relative_path in _GOVERNANCE_READER_DOCUMENTS
        if storage.exists(layout.root / relative_path)
    ]
    tabs.append({"id": "governance", "label": "治理", "artifacts": governance_artifacts})
    # TokenAnalyticsTab has always been a computed PySide view over the
    # project's run logs rather than a single source file.  Expose the same
    # aggregate as a virtual reader artifact so React does not mistake an
    # existing tracking feature for a missing document.
    token_analytics = collect_project_token_analytics(layout.root)
    total_tokens = int(token_analytics.get("total_tokens") or 0)
    call_count = int(token_analytics.get("total_call_count") or 0)
    step_count = int(token_analytics.get("step_count") or 0)
    model_count = len(token_analytics.get("models") or [])
    token_summary = (
        f"累计记录 {total_tokens:,} Token，来自 {call_count} 次模型调用。"
        if total_tokens > 0
        else "暂无可统计的运行日志；完成一次任务后将展示 Token 明细。"
    )
    tabs.append(
        {
            "id": "tracking",
            "label": "追踪",
            "artifacts": [
                {
                    "id": "token-analytics",
                    "label": "Token 追踪",
                    "caption": "本项目调用、步骤与模型成本摘要",
                    "source_label": "logs/（运行日志聚合）",
                    "paragraphs": [token_summary],
                    "facts": [
                        {"label": "累计用量", "value": f"{total_tokens:,} tokens"},
                        {"label": "模型调用", "value": str(call_count)},
                        {"label": "步骤记录", "value": str(step_count)},
                        {"label": "参与模型", "value": str(model_count)},
                    ],
                    "content": json.dumps(token_analytics, ensure_ascii=False),
                    "format": "json",
                }
            ],
        }
    )
    return tabs


def _short_project_reader_tabs(storage: FileSystemStorage, layout: Any) -> list[dict[str, Any]]:
    documents = (
        ("prose", "正文", "chapters/short_story.md"),
        ("spec", "故事规格", "spec.json"),
        ("elements", "要素选择", "plans/blueprint_elements_selection.json"),
        ("beats", "节拍结构", "beats.json"),
        ("evaluation", "评估报告", "reports/eval_report.json"),
    )
    return [
        {
            "id": artifact_id,
            "label": label,
            "artifacts": [
                _reader_file_artifact(storage, layout, artifact_id, label, relative_path)
            ],
        }
        for artifact_id, label, relative_path in documents
    ]


def _reader_file_artifact(
    storage: FileSystemStorage,
    layout: Any,
    artifact_id: str,
    label: str,
    relative_path: str,
    *,
    caption: str | None = None,
    facts: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    path = layout.root / relative_path
    if not storage.exists(path):
        return {
            "id": artifact_id,
            "label": label,
            "caption": caption or f"{label}尚未生成",
            "source_label": relative_path,
            "paragraphs": ["完成对应流程后将自动生成。"],
            "facts": facts or [],
        }

    text = _reader_document_text(storage, path)
    paragraphs = _reader_document_paragraphs(text)
    artifact: dict[str, Any] = {
        "id": artifact_id,
        "label": label,
        "caption": caption or paragraphs[0][:120],
        "source_label": relative_path,
        **_reader_source_metadata(path),
        "paragraphs": paragraphs,
        "facts": facts or _reader_document_facts(text),
    }
    # A report's existence/score is not evidence that it verified the current
    # prose. Short-story artifacts have no numbered chapter and stay separate.
    chapter_match = re.search(r"chapter_(\d+)", relative_path)
    if chapter_match and relative_path.startswith("reports/"):
        from novel_forge.core.utils.text_hash import source_text_hash
        from novel_forge.persistence.project_staleness import revision_stale_chapters

        chapter_number = int(chapter_match.group(1))
        prose_path = layout.chapter_path(chapter_number)
        current_hash = (
            source_text_hash(prose_path.read_text(encoding="utf-8")) if prose_path.is_file() else ""
        )
        report_hash = str(text.get("source_text_hash") or "") if isinstance(text, dict) else ""
        status_path = (
            layout.states_dir / "final_revision_status" / f"chapter_{chapter_number:03d}.json"
        )
        revision = _reader_document_text(storage, status_path) if status_path.exists() else {}
        pending = (
            isinstance(revision, dict)
            and revision.get("current_hash") == current_hash
            and revision.get("requires_reevaluation")
        )
        stale = bool(
            pending
            or chapter_number in revision_stale_chapters(storage, layout)
            or (report_hash and report_hash != current_hash)
        )
        artifact["freshness"] = (
            "stale"
            if stale
            else "current"
            if report_hash and report_hash == current_hash
            else "unverified"
        )
        artifact["freshness_message"] = (
            "正文或上游已变化：此报告仅供历史参考，须重验"
            if stale
            else "已核对报告正文版本"
            if report_hash
            else "报告未记录正文哈希，不能证明已验证当前版本"
        )
        artifact["facts"] = [
            {"label": "版本校验", "value": artifact["freshness_message"]},
            *artifact["facts"],
        ]
    full_content = _reader_document_content(storage, path)
    if full_content is not None:
        content, content_format = full_content
        artifact["content"] = content
        artifact["format"] = content_format
    return artifact


# Documents larger than this only ship the compact excerpt so the reader
# payload stays bounded; the raw file remains reachable via source_local_path.
_READER_CONTENT_MAX_BYTES = 2 * 1024 * 1024


def _reader_document_content(storage: FileSystemStorage, path: Path) -> tuple[str, str] | None:
    """Return the full raw source text plus its RenderDocumentFormat.

    ``.json`` is re-serialized with stable indentation so the client JSON tree
    renders a canonical structure; ``.md`` stays markdown; anything else is
    plain text. Oversized or unreadable sources return ``None`` so callers
    omit ``content`` and consumers fall back to the excerpt.
    """
    try:
        if path.stat().st_size > _READER_CONTENT_MAX_BYTES:
            return None
        if path.suffix == ".json":
            data = storage.load_json(path)
            return json.dumps(data, ensure_ascii=False, indent=2), "json"
        if path.suffix == ".md":
            return path.read_text(encoding="utf-8"), "markdown"
        return path.read_text(encoding="utf-8"), "plain_text"
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _reader_document_text(storage: FileSystemStorage, path: Path) -> Any:
    try:
        if path.suffix == ".json":
            return storage.load_json(path)
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError):
        return ""


def _reader_document_paragraphs(value: Any) -> list[str]:
    if isinstance(value, str):
        paragraphs = [paragraph.strip() for paragraph in value.split("\n\n") if paragraph.strip()]
    elif isinstance(value, dict):
        paragraphs = [
            f"{key}：{_reader_display_value(item)}"
            for key, item in value.items()
            if _reader_display_value(item)
        ]
    elif isinstance(value, list):
        paragraphs = [_reader_display_value(item) for item in value if _reader_display_value(item)]
    else:
        paragraphs = []
    return paragraphs[:32] or ["文档内容为空。"]


def _reader_document_facts(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, dict):
        return []
    return [
        {"label": str(key), "value": _reader_display_value(item)[:180]}
        for key, item in list(value.items())[:12]
        if _reader_display_value(item)
    ]


def _reader_display_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))[:1200]


def _reader_slug(value: str) -> str:
    return "-".join(f"{ord(char):x}" for char in value)


def _reader_source_metadata(path: Path) -> dict[str, str | int]:
    """Return true local-file metadata for a compact reader artifact.

    The reader payload stays deliberately excerpt-sized, while the rich dialog
    receives a canonical engine-resolved path so Tauri can reveal the exact
    source file instead of guessing from a display label.
    """

    try:
        stat = path.resolve().stat()
    except OSError:
        return {}
    return {
        "source_local_path": str(path.resolve()),
        "source_byte_size": stat.st_size,
        "source_modified_at_label": datetime.fromtimestamp(stat.st_mtime).strftime(
            "%Y-%m-%d %H:%M"
        ),
        "source_revision": hashlib.sha256(path.read_bytes()).hexdigest()[:16],
    }


# ── Narrative Tools ───────────────────────────────────────────────────────────

_SUBPLOT_LANE_TONES: tuple[str, ...] = ("jade", "blue", "red", "violet")
_FEEDBACK_LINK_TYPES: set[str] = {"feed_main", "reveal_key", "theme_echo"}
_KNOWN_WEAVE_LINK_TYPES: set[str] = {
    "feed_main",
    "reveal_key",
    "theme_echo",
    "trigger_start",
    "trigger_turn",
}


def _as_positive_int(value: Any) -> int:
    """Coerce a value to a non-negative int (0 when not a positive number)."""
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _infer_blueprint_total_chapters(blueprint: dict[str, Any]) -> int:
    """Infer the narrative horizon from declared and chapter-bearing fields."""
    # ``NarrativeBlueprint`` normally derives its horizon from the companion
    # outline, but historical projects and imported blueprints may retain a
    # declared total without complete phase/node arrays.  Honour that value so
    # their timeline never collapses to a misleading ``共 0 章`` canvas.
    max_chapter = max(
        _as_positive_int(blueprint.get("total_chapters")),
        _as_positive_int(blueprint.get("chapters_total")),
    )
    for volume in blueprint.get("volumes", []) or []:
        if isinstance(volume, dict):
            max_chapter = max(max_chapter, _as_positive_int(volume.get("chapter_end")))
    for phase in blueprint.get("narrative_phases", []) or []:
        if isinstance(phase, dict):
            max_chapter = max(max_chapter, _as_positive_int(phase.get("chapter_end")))
    for tp in blueprint.get("key_turning_points", []) or []:
        if isinstance(tp, dict):
            max_chapter = max(max_chapter, _as_positive_int(tp.get("chapter_number")))
    for arc in blueprint.get("character_arcs", []) or []:
        if not isinstance(arc, dict):
            continue
        for milestone in arc.get("milestones", []) or []:
            if isinstance(milestone, dict):
                max_chapter = max(max_chapter, _as_positive_int(milestone.get("chapter_end")))
    for subplot in blueprint.get("subplot_plan", []) or []:
        if not isinstance(subplot, dict):
            continue
        max_chapter = max(max_chapter, _as_positive_int(subplot.get("resolution_chapter")))
        for chapter in subplot.get("involved_chapters", []) or []:
            max_chapter = max(max_chapter, _as_positive_int(chapter))
        for event in subplot.get("chapter_events", []) or []:
            if isinstance(event, dict):
                max_chapter = max(max_chapter, _as_positive_int(event.get("chapter_number")))
        for link in subplot.get("weave_links", []) or []:
            if isinstance(link, dict):
                max_chapter = max(max_chapter, _as_positive_int(link.get("trigger_chapter")))
    return max_chapter


def _outline_text_items(value: Any) -> list[str]:
    """Keep outline prose as prose, never Python container representations."""
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [text for item in value for text in _outline_text_items(item)]
    if isinstance(value, dict):
        for key in ("description", "text", "event", "summary"):
            if isinstance(value.get(key), str) and value[key].strip():
                return [value[key].strip()]
    return []


def _outline_reading_fields(item: dict[str, Any]) -> dict[str, Any]:
    facts = []
    for label, value in (
        ("视角", item.get("pov_character_name") or item.get("pov_character")),
        ("场景", item.get("setting")),
        ("时间", item.get("time_anchor")),
        ("时间跨度", item.get("time_span")),
        ("参与角色", item.get("involved_character_names") or item.get("involved_characters")),
    ):
        text = "、".join(_outline_text_items(value))
        if text:
            facts.append({"label": label, "value": text})
    return {
        "goal": "\n\n".join(_outline_text_items(item.get("goal"))),
        "beats_summary": _outline_text_items(item.get("beats_summary")),
        "main_plot_points": _outline_text_items(item.get("main_plot_points")),
        "subplot_points": _outline_text_items(item.get("subplot_points")),
        "scene_design_goals": _outline_text_items(item.get("scene_design_goals")),
        "notes": "\n\n".join(_outline_text_items(item.get("notes"))),
        "facts": facts,
    }


def _outline_chapter_sources(outline: dict[str, Any]) -> list[tuple[int, str, str]]:
    """Return stable chapter coordinates and display copy from an outline."""
    by_chapter: dict[int, tuple[str, str]] = {}
    for item in outline.get("chapters", []) or []:
        if not isinstance(item, dict):
            continue
        chapter = _as_positive_int(item.get("chapter_number"))
        if chapter < 1:
            continue
        title = str(item.get("title") or item.get("chapter_title") or "").strip()
        summary = "\n\n".join(
            _outline_text_items(
                item.get("beats_summary")
                or item.get("summary")
                or item.get("chapter_summary")
                or item.get("description")
                or title
                or f"第{chapter}章章节节点"
            )
        )
        by_chapter[chapter] = (title, summary)
    return [(chapter, title, summary) for chapter, (title, summary) in sorted(by_chapter.items())]


def _infer_outline_total_chapters(outline: dict[str, Any]) -> int:
    """Use the outline declaration and its materialized chapter coordinates."""
    chapters = _outline_chapter_sources(outline)
    return max(
        [
            _as_positive_int(outline.get("total_chapters")),
            *(chapter for chapter, _, _ in chapters),
        ]
    )


def _phase_tone(index: int, total: int) -> str:
    """Map a phase's ordinal position to the timeline tone contract."""
    if total <= 0:
        return "rising"
    if index == 0:
        return "opening"
    if index == total - 1:
        return "resolution"
    if total > 2 and index == total - 2:
        return "climax"
    return "rising"


def _build_narrative_visualization(
    storage: FileSystemStorage, layout: Any, detail: Any
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Compose the timeline payload + subplot list from narrative_blueprint.json.

    Mirrors the PySide6 ``NarrativeBlueprintWidget`` data sources (phases,
    turning points, subplot lanes and weave links) so the React timeline
    renders the same narrative structure.
    """
    blueprint = _load_optional_json(storage, layout.blueprint_path) or {}
    outline = _load_optional_json(storage, layout.outline_path) or {}
    outline_chapters = _outline_chapter_sources(outline)
    total_chapters = max(
        _infer_blueprint_total_chapters(blueprint),
        _infer_outline_total_chapters(outline),
        _as_positive_int(detail.total_chapters),
        _as_positive_int(detail.completed_chapters),
    )

    phase_sources = [
        phase for phase in blueprint.get("narrative_phases", []) or [] if isinstance(phase, dict)
    ]
    phases = [
        {
            "id": f"phase-{index}",
            "label": str(phase.get("phase_name") or f"阶段{index + 1}"),
            "chapter_start": _as_positive_int(phase.get("chapter_start")) or 1,
            "chapter_end": _as_positive_int(phase.get("chapter_end")) or total_chapters,
            "tension_label": str(phase.get("tension_level") or ""),
            "summary": str(phase.get("description") or ""),
            "tone": _phase_tone(index, len(phase_sources)),
        }
        for index, phase in enumerate(phase_sources)
    ]

    milestones: list[dict[str, Any]] = []
    for index, tp in enumerate(
        tp for tp in blueprint.get("key_turning_points", []) or [] if isinstance(tp, dict)
    ):
        chapter = _as_positive_int(tp.get("chapter_number"))
        if chapter < 1:
            continue
        description = str(tp.get("description") or tp.get("label") or "主线转折").strip()
        milestones.append(
            {
                "id": f"milestone-{index}",
                "chapter": chapter,
                "label": description[:12] or "主线转折",
                "description": description or "主线转折",
            }
        )

    # A partially initialized or legacy project can have a valid chapter
    # outline but no authored turning points yet.  The outline remains the
    # canonical fallback: render its concrete chapter nodes instead of an
    # empty timeline frame, while keeping authored blueprint nodes authoritative
    # whenever they exist.
    if not milestones:
        milestones.extend(
            {
                "id": f"outline-milestone-{chapter}",
                "chapter": chapter,
                "label": (title or f"第{chapter}章")[:12],
                "description": summary,
            }
            for chapter, title, summary in outline_chapters
        )

    subplot_lanes: list[dict[str, Any]] = []
    weave_links: list[dict[str, Any]] = []
    subplots: list[dict[str, Any]] = []
    for index, subplot in enumerate(
        sp for sp in blueprint.get("subplot_plan", []) or [] if isinstance(sp, dict)
    ):
        lane_id = f"subplot-{index}"
        name = str(subplot.get("name") or f"支线{index + 1}")
        events: list[dict[str, Any]] = []
        for event in subplot.get("chapter_events", []) or []:
            if not isinstance(event, dict):
                continue
            chapter = _as_positive_int(event.get("chapter_number"))
            if chapter < 1:
                continue
            description = str(event.get("description") or event.get("event") or "").strip()
            events.append(
                {
                    "id": f"{lane_id}-ev-{chapter}",
                    "chapter": chapter,
                    "label": description[:12] or f"第{chapter}章",
                    "description": description or f"第{chapter}章支线节点",
                    "emphasis": "normal",
                }
            )
        subplot_lanes.append(
            {
                "id": lane_id,
                "label": name,
                "tone": _SUBPLOT_LANE_TONES[index % len(_SUBPLOT_LANE_TONES)],
                "events": events,
            }
        )
        for link_index, link in enumerate(subplot.get("weave_links", []) or []):
            if not isinstance(link, dict):
                continue
            chapter = _as_positive_int(link.get("trigger_chapter"))
            link_type = str(link.get("link_type") or "").strip()
            target = str(link.get("target_subplot") or "").strip()
            is_feedback = link_type in _FEEDBACK_LINK_TYPES or target == "主线"
            if chapter < 1 or not is_feedback:
                continue
            weave_links.append(
                {
                    "id": f"{lane_id}-weave-{link_index}",
                    "source_lane_id": lane_id,
                    "target": "mainline",
                    "chapter": chapter,
                    "type": link_type if link_type in _KNOWN_WEAVE_LINK_TYPES else "feed_main",
                    "label": str(link.get("description") or "反哺主线")[:12],
                    "description": str(link.get("description") or ""),
                }
            )
        involved = sorted(
            chapter
            for chapter in (
                _as_positive_int(item) for item in subplot.get("involved_chapters", []) or []
            )
            if chapter > 0
        )
        chapters_label = (
            f"第{involved[0]}-{involved[-1]}章"
            if len(involved) > 1
            else (f"第{involved[0]}章" if involved else "未配置")
        )
        subplots.append(
            {
                "id": lane_id,
                "title": name,
                "priority_label": str(subplot.get("priority") or "normal"),
                "chapters_label": chapters_label,
                "description": str(subplot.get("description") or ""),
                "resolution": str(
                    subplot.get("resolution_type") or subplot.get("resolution_target") or ""
                ),
                # The compact fields above serve the overview card.  The
                # workbench receives this lossless projection so title/range
                # edits cannot discard existing events or weave links.
                "plan": {
                    "name": name,
                    "description": str(subplot.get("description") or ""),
                    "involved_chapters": involved,
                    "chapter_events": [
                        {
                            "chapter_number": _as_positive_int(event.get("chapter_number")),
                            "event": str(event.get("event") or ""),
                            "weave_notes": str(event.get("weave_notes") or ""),
                            "depends_on": [
                                str(dep) for dep in event.get("depends_on", []) if str(dep).strip()
                            ]
                            if isinstance(event.get("depends_on"), list)
                            else [],
                        }
                        for event in subplot.get("chapter_events", []) or []
                        if isinstance(event, dict)
                        and _as_positive_int(event.get("chapter_number")) > 0
                    ],
                    "weave_links": [
                        {
                            "source_type": str(link.get("source_type") or ""),
                            "source_ref": str(link.get("source_ref") or ""),
                            "target_subplot": str(link.get("target_subplot") or "主线"),
                            "trigger_chapter": _as_positive_int(link.get("trigger_chapter")),
                            "link_type": str(link.get("link_type") or ""),
                            "description": str(link.get("description") or ""),
                        }
                        for link in subplot.get("weave_links", []) or []
                        if isinstance(link, dict)
                    ],
                    "priority": str(subplot.get("priority") or "normal"),
                    "resolution_chapter": _as_positive_int(subplot.get("resolution_chapter")),
                    "resolution_target": str(subplot.get("resolution_target") or ""),
                    "resolution_type": str(subplot.get("resolution_type") or ""),
                },
            }
        )

    character_arcs: list[dict[str, Any]] = []
    for arc_index, arc in enumerate(
        a for a in blueprint.get("character_arcs", []) or [] if isinstance(a, dict)
    ):
        arc_milestones: list[dict[str, Any]] = []
        for ms in arc.get("milestones", []) or []:
            if not isinstance(ms, dict):
                continue
            arc_milestones.append(
                {
                    "chapter_start": _as_positive_int(ms.get("chapter_start")),
                    "chapter_end": _as_positive_int(ms.get("chapter_end")),
                    "description": str(ms.get("description") or ""),
                }
            )
        character_arcs.append(
            {
                "id": f"arc-{arc_index}",
                "character": str(arc.get("character") or f"角色{arc_index + 1}"),
                "arc_summary": str(arc.get("arc_summary") or ""),
                "milestones": arc_milestones,
            }
        )

    visualization = {
        "total_chapters": total_chapters,
        "phases": phases,
        "milestones": milestones,
        "subplot_lanes": subplot_lanes,
        "weave_links": weave_links,
        "character_arcs": character_arcs,
    }
    return visualization, subplots


def _narrative_character_role_label(value: Any) -> str:
    return {
        "protagonist": "主角",
        "deuteragonist": "重要配角",
        "antagonist": "对手",
        "supporting": "配角",
        "minor": "次要角色",
    }.get(str(value or "").strip().lower(), str(value or "配角"))


def _narrative_character_status_label(value: Any) -> str:
    return {
        "active": "活跃",
        "dormant": "缺席",
        "retired": "退场",
    }.get(str(value or "").strip().lower(), str(value or "活跃"))


def _narrative_character_timeline_label(value: Any) -> str:
    return {
        "default": "当前线",
        "modern": "当前线",
        "past": "历史线",
        "cross_temporal": "跨时空",
        "memory_only": "回忆线",
    }.get(str(value or "").strip().lower(), str(value or "当前线"))


def _narrative_character_summary(character: dict[str, Any]) -> str:
    for field_name in ("summary", "description", "personality", "social_status", "backstory"):
        value = " ".join(str(character.get(field_name) or "").split())
        if value:
            return value
    return "暂无角色摘要。"


def _relationship_type_labels(matrix_payload: Any) -> dict[frozenset[str], str]:
    if not isinstance(matrix_payload, dict):
        return {}
    labels = {
        "relationship": "一般关系",
        "romantic_tension": "情感张力",
        "family": "亲属",
        "mentor_student": "师徒",
        "professional": "职场/组织",
        "alliance": "同盟",
        "rivalry": "竞争",
        "antagonism": "对抗",
        "community": "社群",
        "identity_link": "身份映射",
    }
    result: dict[frozenset[str], str] = {}
    for item in matrix_payload.get("relationship_matrix", []):
        if not isinstance(item, dict):
            continue
        source = clean_character_name(
            item.get("character_a") or item.get("source_name") or item.get("source")
        )
        target = clean_character_name(
            item.get("character_b") or item.get("target_name") or item.get("target")
        )
        if not source or not target:
            continue
        relation_type = str(item.get("relation_type") or "relationship").strip()
        result[frozenset((source, target))] = labels.get(relation_type, relation_type)
    return result


def _build_character_relationships(
    chars_data: Any,
    relationship_matrix: Any = None,
) -> list[dict[str, Any]]:
    """Build deduplicated relationship edges from each character's relationships.

    Mirrors PySide6 ``CharacterGraphWidget`` edge construction: an A→B and B→A
    pair collapses to a single undirected edge keyed by the character ids.
    """
    if not chars_data:
        return []
    characters = chars_data.get("characters", []) or []
    name_to_id: dict[str, str] = {}
    for ch in characters:
        if not isinstance(ch, dict):
            continue
        name = clean_character_name(ch.get("name"))
        if name:
            name_to_id[name] = str(ch.get("character_id") or ch.get("id") or name)

    relationships: list[dict[str, Any]] = []
    seen: set[frozenset[str]] = set()
    relation_labels = _relationship_type_labels(relationship_matrix)
    for ch in characters:
        if not isinstance(ch, dict):
            continue
        source_name = clean_character_name(ch.get("name"))
        source_id = name_to_id.get(source_name)
        if not source_id:
            continue
        rels = ch.get("relationships", {})
        if not isinstance(rels, dict):
            continue
        for target_name, label in rels.items():
            target_name = clean_character_name(target_name)
            target_id = name_to_id.get(target_name)
            if not target_id or target_id == source_id:
                continue
            pair = frozenset((source_id, target_id))
            if pair in seen:
                continue
            seen.add(pair)
            ordered = tuple(sorted((source_id, target_id)))
            relationships.append(
                {
                    "id": f"rel-{ordered[0]}~{ordered[1]}",
                    "from_character_id": source_id,
                    "to_character_id": target_id,
                    "type_label": relation_labels.get(
                        frozenset((source_name, target_name)),
                        "一般关系",
                    ),
                    "evidence": str(label or ""),
                }
            )
    return relationships


@router.get("/projects/{project_id}/narrative-tools")
async def get_narrative_tools_view(
    project_id: str,
    inspector: InspectorDep,
    storage: StorageDep,
) -> dict[str, Any]:
    """Compose narrative workbench data from stored artifacts."""
    try:
        detail = inspector.get_project_detail(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc

    from novel_forge.persistence.models import ProjectLayout

    layout = ProjectLayout(storage.project_path(project_id))

    # Characters
    chars_data = _load_optional_json(storage, layout.characters_path)
    characters: list[dict[str, Any]] = []
    character_details: list[dict[str, Any]] = []
    if chars_data:
        for ch in chars_data.get("characters", []):
            if not isinstance(ch, dict):
                continue
            character_id = str(ch.get("character_id") or ch.get("id") or ch.get("name") or "")
            role = _narrative_character_role_label(ch.get("role"))
            status = _narrative_character_status_label(ch.get("status"))
            characters.append(
                {
                    "id": character_id,
                    "name": ch.get("name", ""),
                    "role": role,
                    "status_label": status,
                    "summary": _narrative_character_summary(ch),
                    "arc": ch.get("arc", ""),
                }
            )
            character_details.append(
                {
                    "character_id": character_id,
                    "timeline_label": _narrative_character_timeline_label(ch.get("time_layer")),
                    "age_label": str(ch.get("age", "")),
                    "gender_label": ch.get("gender", ""),
                    "occupation": ch.get("social_status", ch.get("occupation", "")),
                    "personality": ch.get("personality", ""),
                    "backstory": ch.get("backstory", ""),
                    "abilities": ch.get("abilities", ""),
                    "appearance": ch.get("appearance", ""),
                    "arc": ch.get("arc", ""),
                    "voice": ch.get("voice", ""),
                    "notes": ch.get("notes", ""),
                }
            )

    # Outline for visualization
    outline_data = _load_optional_json(storage, layout.outline_path) or {}
    total_chapters = _as_positive_int(outline_data.get("total_chapters"))
    hard_through = _as_positive_int(outline_data.get("hard_through_chapter")) or total_chapters
    planned_through = (
        _as_positive_int(outline_data.get("planned_through_chapter")) or total_chapters
    )
    completed_chapter_nums = {ch.chapter_number for ch in detail.chapters}
    outline_reading_fields = {
        _as_positive_int(item.get("chapter_number")): _outline_reading_fields(item)
        for item in outline_data.get("chapters", []) or []
        if isinstance(item, dict)
    }
    outline_nodes = [
        {
            "id": f"outline-{chapter}",
            "chapter_number": chapter,
            "chapter_label": f"第 {chapter} 章",
            "title": title,
            "summary": summary,
            "state_label": "已完成"
            if chapter in completed_chapter_nums
            else ("预规划" if chapter > hard_through else "待写"),
            **outline_reading_fields.get(chapter, {}),
        }
        for chapter, title, summary in _outline_chapter_sources(outline_data)
    ]

    visualization, subplots = _build_narrative_visualization(storage, layout, detail)
    try:
        humanize_library = load_humanize_library()
        humanize_patterns = [humanize_pattern_view(entry) for entry in humanize_library.entries]
        humanize_library_revision = humanize_library.revision
    except Exception:
        # Narrative editing remains usable if an optional global library is
        # unavailable; mutation commands will return the concrete failure.
        humanize_patterns = []
        humanize_library_revision = ""
    relationships = _build_character_relationships(
        chars_data,
        _load_optional_json(
            storage,
            layout.states_dir / "init_v2" / "character_relationship_matrix.json",
        ),
    )

    from novel_forge.story_kernel.relationship_tracker import (
        build_relationship_overview_sync,
    )

    try:
        relationship_overview = build_relationship_overview_sync(
            storage.project_path(project_id)
        ).to_dict()
    except Exception:
        relationship_overview = {
            "total_relationships": 0,
            "high_tension_pairs": [],
            "recent_shifts": [],
            "timelines": [],
        }

    return {
        "characters": characters,
        "character_details": character_details,
        "relationships": relationships,
        "visualization": visualization,
        "relationship_overview": relationship_overview,
        "outline": outline_nodes,
        "planning": {
            "total_chapters": total_chapters,
            "hard_through_chapter": hard_through,
            "planned_through_chapter": planned_through,
            "archived_chapters": len(completed_chapter_nums),
            "task": _planning_task_view(layout.root),
        },
        "subplots": subplots,
        "humanize_patterns": humanize_patterns,
        "revision_candidates": [],
        "blueprint_revision": hashlib.sha256(layout.blueprint_path.read_bytes()).hexdigest()
        if storage.exists(layout.blueprint_path)
        else "",
        "character_revision": hashlib.sha256(layout.characters_path.read_bytes()).hexdigest()
        if storage.exists(layout.characters_path)
        else "",
        "humanize_library_revision": humanize_library_revision,
    }


@router.get("/projects/{project_id}/relationship-overview")
async def get_relationship_overview_view(
    project_id: str,
    inspector: InspectorDep,
    storage: StorageDep,
) -> dict[str, Any]:
    """Relationship evolution overview (PySide6 `_build_relationship_tab` source).

    Returns per-pair trust/tension timelines across chapters so the reader can
    render the relationship evolution dashboard rather than a static graph.
    """
    try:
        inspector.get_project_detail(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc

    from novel_forge.story_kernel.relationship_tracker import (
        build_relationship_overview_sync,
    )

    project_path = storage.project_path(project_id)
    try:
        overview = build_relationship_overview_sync(project_path)
    except Exception:
        return {
            "total_relationships": 0,
            "high_tension_pairs": [],
            "recent_shifts": [],
            "timelines": [],
        }
    return overview.to_dict()


# ── Commands ──────────────────────────────────────────────────────────────────


class PrepareChapterBody(BaseModel):
    kind: str = "prepare_chapter"
    project_id: str
    chapter_number: int
    notes: str | None = None


class CancelChapterBody(BaseModel):
    kind: str = "cancel_chapter"
    project_id: str
    chapter_number: int


class StartWorkflowBody(BaseModel):
    kind: str = "start_workflow"
    project_id: str
    workflow_type: str
    run_mode: str = "create"
    idempotency_key: str
    payload: dict[str, Any]


class SynthesizeVoiceBody(BaseModel):
    kind: str = "synthesize_voice"
    project_id: str
    segment_ids: list[str] | None = None


class ExportAudioBody(BaseModel):
    kind: str = "export_audio"
    project_id: str
    scope: str = "chapter"
    chapter_number: int | None = None
    format: str = "mp3"
    include_subtitles: bool = False
    target_lufs: float | None = None


@router.post("/commands/prepare-chapter")
async def prepare_chapter(
    body: PrepareChapterBody,
    job_service: JobServiceDep,
) -> dict[str, Any]:
    """Submit a chapter preparation job."""
    from novel_forge.app_service.contracts import JobCommand

    command = JobCommand(
        kind="prepare_chapter",
        project_id=body.project_id,
        payload={
            "chapter_number": body.chapter_number,
            "notes": body.notes or "",
        },
    )
    record = job_service.submit(command)
    return {
        "status": "accepted",
        "message": f"章节 {body.chapter_number} 准备任务已提交",
        "task_id": record.job_id,
    }


@router.post("/commands/cancel-chapter")
async def cancel_chapter(
    body: CancelChapterBody,
    job_service: JobServiceDep,
) -> dict[str, Any]:
    """Cancel a running chapter job."""
    # Find the active job for this chapter
    records = job_service.list(project_id=body.project_id)
    for record in records:
        if str(
            record.kind.value if hasattr(record.kind, "value") else record.kind
        ) == "prepare_chapter" and record.status.value in {"running", "queued"}:
            job_service.cancel(record.job_id, reason="用户已取消")
            return {
                "status": "accepted",
                "message": f"章节 {body.chapter_number} 任务已取消",
            }
    return {
        "status": "rejected",
        "message": "未找到运行中的章节任务",
    }


@router.post("/commands/start-workflow")
async def start_workflow(
    body: StartWorkflowBody,
    job_service: JobServiceDep,
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Compatibility route delegated to the canonical Engine command handler."""
    from novel_forge.api.routes.engine import _StartWorkflowBody, engine_start_workflow

    engine_body = _StartWorkflowBody.model_validate(
        body.model_dump(),
    )
    response = await engine_start_workflow(engine_body, job_service, runtime)
    return response.model_dump(mode="json", by_alias=True, exclude_none=True)


@router.post("/commands/synthesize-voice")
async def synthesize_voice(
    body: SynthesizeVoiceBody,
    job_service: JobServiceDep,
) -> dict[str, Any]:
    """Submit a TTS synthesis job."""
    from novel_forge.app_service.contracts import JobCommand

    command = JobCommand(
        kind="tts_synthesize",
        project_id=body.project_id,
        payload={"segment_ids": body.segment_ids or []},
    )
    record = job_service.submit(command)
    return {
        "status": "accepted",
        "message": "语音合成任务已提交",
        "task_id": record.job_id,
    }


@router.post("/commands/export-audio")
async def export_audio(
    body: ExportAudioBody,
    job_service: JobServiceDep,
) -> dict[str, Any]:
    """Submit an audio export job."""
    from novel_forge.app_service.contracts import JobCommand

    command = JobCommand(
        kind="export_book" if body.scope == "book" else "export_chapter_audio",
        project_id=body.project_id,
        payload={
            "scope": body.scope,
            "chapter_number": body.chapter_number,
            "format": body.format,
            "include_subtitles": body.include_subtitles,
            "target_lufs": body.target_lufs,
        },
    )
    record = job_service.submit(command)
    return {
        "status": "accepted",
        "message": "音频导出任务已提交",
        "task_id": record.job_id,
    }


# ── SSE Stream ────────────────────────────────────────────────────────────────


@router.get("/jobs/{job_id}/stream")
async def job_stream_sse(
    job_id: str,
    job_service: JobServiceDep,
) -> StreamingResponse:
    """Server-Sent Events stream for a job's lifecycle.

    Emits events in SSE format:
    - event: snapshot / step / status / done
    - data: JSON payload
    """
    record = job_service.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")

    async def event_generator():
        import json

        # Send initial snapshot
        yield f"event: snapshot\ndata: {json.dumps({'job_id': job_id, 'status': record.status.value})}\n\n"

        # Subscribe to events
        subscription = job_service.open_subscription(job_id, max_queue_size=100)
        try:
            while True:
                try:
                    event = await asyncio.to_thread(subscription.next_event, timeout=30.0)
                    if event is None:
                        # Heartbeat
                        yield ": heartbeat\n\n"
                        continue

                    event_type = (
                        event.type.value if hasattr(event.type, "value") else str(event.type)
                    )
                    payload = {
                        "job_id": job_id,
                        "event_type": event_type,
                        "step": event.step,
                        "payload": event.payload,
                    }
                    yield f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"

                    # Terminal events
                    if event_type in {"job_succeeded", "job_failed", "job_cancelled"}:
                        yield f"event: done\ndata: {json.dumps({'job_id': job_id})}\n\n"
                        break
                except Exception:
                    # Connection closed or timeout
                    break
        finally:
            subscription.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _load_optional_json(storage: FileSystemStorage, path: Any) -> dict[str, Any] | None:
    if not storage.exists(path):
        return None
    payload = storage.load_json(path)
    if not isinstance(payload, dict):
        return None
    return payload


def _next_action(p: Any) -> str:
    if p.mode == "short":
        return "查看作品" if p.completion_ratio >= 1.0 else "前往机杼页生成短篇"
    if p.project_state == "completed":
        return "回看章节与质量报告"
    if p.completed_chapters == 0:
        return "开始第 1 章"
    return f"继续推进第 {p.completed_chapters + 1} 章"


def _tier_label(profile: Any) -> str:
    model_id = (profile.model_id or "").lower()
    if any(k in model_id for k in ("max", "pro", "premium", "o1", "o3")):
        return "高级"
    if any(k in model_id for k in ("mini", "lite", "turbo", "flash")):
        return "经济"
    return "标准"
