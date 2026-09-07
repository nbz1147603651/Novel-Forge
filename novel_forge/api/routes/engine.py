"""Versioned Engine views and command bridge for replaceable UI clients."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import Field

from novel_forge.api.deps import (
    get_engine_query_service,
    get_job_service,
    get_runtime_services,
    get_storage,
    reload_runtime_dependencies,
)
from novel_forge.api.routes.authoring import router as authoring_router
from novel_forge.app_service.ai_generate import (
    AI_CREATIVE_NOTE_FIELD,
    AI_POLISH_SUGGESTIONS_FIELD,
    generate_config,
    polish_config,
)
from novel_forge.app_service.character_artifacts import (
    RELATIONSHIP_TYPE_OPTIONS,
    CharacterArtifactConflictError,
    CharacterArtifactMutationError,
    remove_narrative_relationship,
    retire_narrative_character,
    save_narrative_character,
    save_narrative_relationship,
)
from novel_forge.app_service.contracts import JobCommand, JobKind, JobRecord
from novel_forge.app_service.engine_novel import EngineNovelStudioView
from novel_forge.app_service.engine_queries import (
    ChapterOutOfRangeError,
    EngineQueryError,
    EngineQueryService,
    InvalidChapterNumberError,
    ProjectNotLongError,
    ProjectOutlineMissingError,
)
from novel_forge.app_service.engine_views import (
    EngineCapabilitiesView,
    EngineJobsView,
    EngineRuntimeView,
    EngineTaskStreamView,
    EngineViewModel,
    InitManualRepairIssueView,
    InitManualRepairLocationView,
    InitManualRepairSaveResult,
    InitManualRepairView,
    OllamaManagerView,
    engine_capabilities,
    engine_runtime_view,
    project_jobs_view,
    project_task_stream_view,
)
from novel_forge.app_service.engine_voice import (
    EngineVoiceCatalogOptionView,
    EngineVoiceCatalogView,
    EngineVoiceStudioView,
)
from novel_forge.app_service.humanize_library import (
    HumanizeLibraryConflictError,
    HumanizeLibraryMutationError,
    HumanizePatternInput,
    humanize_pattern_view,
    merge_humanize_patterns,
    remove_humanize_pattern,
    save_humanize_pattern,
    set_humanize_pattern_enabled,
)
from novel_forge.app_service.init_manual_repair import (
    ManualInitRepairConflictError,
    ManualInitRepairError,
    ManualInitRepairSnapshot,
    load_manual_init_repair,
    save_manual_init_repair,
)
from novel_forge.app_service.job_service import JobService
from novel_forge.app_service.model_profile_probe import (
    ModelProfileProbeRequest,
    ModelProfileProbeRequestError,
    probe_model_profile_request,
)
from novel_forge.app_service.narrative_blueprint_subplots import (
    arc_to_subplot,
    blueprint_total_chapters,
    is_event_driven_arc,
    normalize_subplot_payloads,
)
from novel_forge.app_service.ollama_management import (
    OllamaConfigurationError,
    OllamaEngineCommandService,
)
from novel_forge.app_service.preset_manager import sanitize_preset_payload
from novel_forge.app_service.project_files import ProjectFileService
from novel_forge.app_service.selection_revision import (
    MAX_SELECTION_REVISION_CHARS,
    SelectionRevisionError,
    SelectionRevisionInput,
    generate_selection_revision_candidate,
)
from novel_forge.app_service.settings_save import (
    SettingsRouteCommand,
    SettingsSaveCommand,
    save_settings_command,
)
from novel_forge.app_service.task_flow_error_log import TaskFlowErrorLog
from novel_forge.app_service.token_analytics import (
    load_token_dashboard_prefs,
    save_token_dashboard_prefs,
)
from novel_forge.app_service.workflow_requests import build_init_long_request, build_short_request
from novel_forge.common.constants import TaskType
from novel_forge.core.config import get_settings
from novel_forge.core.parsing.parse_utils import safe_parse_json
from novel_forge.core.schemas.bible import CharacterBible
from novel_forge.obs.logger import get_logger
from novel_forge.persistence.authoring_store import AuthoringDeniedError
from novel_forge.persistence.filesystem import (
    FileSystemStorage,
    atomic_write_json,
    normalize_project_id,
)
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import (
    ChapterCleanupConvergenceError,
    RevisionScope,
    UpstreamArtifactKind,
    invalidate_chapter_tts_artifacts,
    record_upstream_artifact_revision,
    regenerate_from_chapter_async,
)
from novel_forge.pipeline.long.services.init.init_cache import reset_project_for_reinit
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens
from novel_forge.tts.runtime.cleanup import (
    ALL_CATEGORIES,
    execute_cleanup_tts_files,
    execute_reset_project_tts_artifacts,
)
from novel_forge.tts.schemas import ChapterAudioResult, DubbingScript, DubbingSegment
from novel_forge.tts.script_integrity import compute_dubbing_script_hash, refresh_segment_uid
from novel_forge.tts.services.delivery import TTSDeliveryNotReadyError, require_delivery_ready
from novel_forge.tts.services.studio_service import VoiceStudioProjectService
from novel_forge.tts.services.voice_preview import (
    VoicePreviewPlan,
    build_preview_plan,
    confirm_preview,
    generate_candidate_previews,
)
from novel_forge.tts.sound_generation.service import SoundGenerationService
from novel_forge.workspace.contracts import (
    ManualRevisionRequest,
    RebuildMemoryVectorsRequest,
    ReevaluateChapterRequest,
    ReextractRelationshipsRequest,
    RepairCausalRequest,
    RepairContinuityRequest,
    RepairIssuesRequest,
    RepairMotifHistoryRequest,
    RunChapterRequest,
)
from novel_forge.workspace.execution import (
    execute_accept_segment_take,
    execute_manual_revision,
    execute_reassemble_chapter_audio,
    execute_reevaluate_chapter,
    execute_resolve_dubbing_speakers,
    execute_synthesize_segment,
)
from novel_forge.workspace.runtime import RuntimeServices
from novel_forge.workspace.tts_ops.execution import (
    execute_analyze_dubbing_style_reference,
    execute_approve_character_voice,
    execute_assign_catalog_voice,
    execute_build_narrator_profile,
    execute_clone_character_voice,
    execute_confirm_voice_team,
    execute_design_character_voice,
    execute_list_tts_voices,
    execute_preview_character_voice,
    execute_save_dubbing_script,
    execute_update_voice_performance,
)

router = APIRouter()
router.include_router(authoring_router)
JobServiceDep = Annotated[JobService, Depends(get_job_service)]
EngineQueryServiceDep = Annotated[EngineQueryService, Depends(get_engine_query_service)]
StorageDep = Annotated[FileSystemStorage, Depends(get_storage)]
RuntimeDep = Annotated[RuntimeServices, Depends(get_runtime_services)]
_log = get_logger("api.engine")


def _init_manual_repair_view(snapshot: ManualInitRepairSnapshot) -> InitManualRepairView:
    """Map the Qt-free manual-repair service state to the public Engine DTO."""

    return InitManualRepairView(
        project_id=snapshot.project_id,
        available=snapshot.available,
        artifact=snapshot.artifact,
        artifact_label=snapshot.artifact_label,
        artifact_path=snapshot.artifact_path,
        payload=snapshot.payload,
        revision=snapshot.revision,
        summary=snapshot.summary,
        issues=[
            InitManualRepairIssueView(
                title=issue.title,
                summary=issue.summary,
                locations=[
                    InitManualRepairLocationView(
                        pointer=location.pointer,
                        label=location.label,
                        confidence=location.confidence,
                        excerpt=location.excerpt,
                    )
                    for location in issue.locations
                ],
            )
            for issue in snapshot.issues
        ],
    )


@router.get(
    "/capabilities",
    response_model=EngineCapabilitiesView,
    response_model_exclude_none=True,
)
async def get_engine_capabilities() -> EngineCapabilitiesView:
    """Expose implemented transports and features for progressive UI adoption."""

    return engine_capabilities()


@router.get("/runtime", response_model=EngineRuntimeView, response_model_exclude_none=True)
async def get_engine_runtime(
    request: Request,
    service: JobServiceDep,
) -> EngineRuntimeView:
    """Expose safe process identity before a client submits work to this Engine."""

    active_job_count, queued_job_count = service.runtime_activity_counts()
    return engine_runtime_view(
        started_at=float(request.app.state.start_time),
        active_job_count=active_job_count,
        queued_job_count=queued_job_count,
    )


@router.get("/jobs", response_model=EngineJobsView, response_model_exclude_none=True)
async def list_engine_jobs(
    service: JobServiceDep,
    project_id: str | None = None,
) -> EngineJobsView:
    """Return a transport-neutral snapshot of application jobs."""

    return project_jobs_view(service.list(project_id=project_id))


@router.get("/ollama", response_model=OllamaManagerView, response_model_exclude_none=True)
async def get_engine_ollama(
    service: JobServiceDep,
) -> OllamaManagerView:
    """Return Engine-host Ollama state; never proxy a client-local endpoint."""

    return await asyncio.to_thread(OllamaEngineCommandService(service).view, get_settings())


class _ErrorArchiveSummaryView(EngineViewModel):
    entry_count: int = Field(ge=0)
    project_count: int = Field(ge=0)
    latest_time: str = ""


@router.get("/error-archive", response_model=_ErrorArchiveSummaryView)
async def get_engine_error_archive(storage: StorageDep) -> _ErrorArchiveSummaryView:
    """Read compact task diagnostics through the Engine, never the UI filesystem."""

    summary = TaskFlowErrorLog(storage.root).summary()
    return _ErrorArchiveSummaryView(
        entry_count=int(summary.get("entry_count") or 0),
        project_count=int(summary.get("project_count") or 0),
        latest_time=str(summary.get("latest_time") or ""),
    )


@router.get(
    "/projects/{project_id}/init-manual-repair",
    response_model=InitManualRepairView,
    response_model_exclude_none=True,
)
async def get_init_manual_repair(
    project_id: str,
    storage: StorageDep,
    artifact: str | None = None,
) -> InitManualRepairView:
    """Read the Engine-owned editor state for a blocked long initialization."""

    try:
        return _init_manual_repair_view(
            load_manual_init_repair(storage, project_id, artifact=artifact)
        )
    except ManualInitRepairError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/novel/projects/{project_id}/studio",
    response_model=EngineNovelStudioView,
    response_model_exclude_none=True,
)
async def get_engine_novel_studio(
    project_id: str,
    service: EngineQueryServiceDep,
    chapter_number: int | None = None,
) -> EngineNovelStudioView:
    """Return the novel module read model used by replaceable UI clients."""

    try:
        return service.get_novel_studio(project_id, chapter_number=chapter_number)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except ProjectNotLongError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProjectOutlineMissingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ChapterOutOfRangeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidChapterNumberError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EngineQueryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/voice/projects/{project_id}/studio",
    response_model=EngineVoiceStudioView,
    response_model_exclude_none=True,
)
async def get_engine_voice_studio(
    project_id: str,
    service: EngineQueryServiceDep,
    chapter_number: int | None = None,
) -> EngineVoiceStudioView:
    """Return the voice module read model without exposing credentials or file paths."""

    try:
        return service.get_voice_studio(project_id, chapter_number=chapter_number)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/jobs/{job_id}/task-stream",
    response_model=EngineTaskStreamView,
    response_model_exclude_none=True,
)
async def get_engine_task_stream(
    job_id: str,
    service: JobServiceDep,
    after_cursor: str | None = None,
    limit: int = 240,
) -> EngineTaskStreamView:
    """Return a bounded task-stream page; clients resume with ``after_cursor``."""

    record = service.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    return project_task_stream_view(record, after_cursor=after_cursor, limit=limit)


# ── Commands (write-side, called by EngineCommandClient) ─────────────────────
# The React frontend's LegacyLocalEngineClient posts commands to
# /api/v1/engine/commands/*.  These thin handlers delegate to JobService.


class EngineCommandResponse(EngineViewModel):
    """Unified response envelope for all Engine write commands."""

    status: Literal["accepted", "rejected", "already_running"]
    message: str
    task_id: str | None = None
    take_id: str | None = None
    audio_url: str | None = None
    download_url: str | None = None
    data: dict[str, Any] | None = None
    error_code: str | None = None


class _StrictEngineCommandModel(EngineViewModel):
    model_config = {"extra": "forbid"}


class _TestModelProfileBody(_StrictEngineCommandModel):
    kind: Literal["test_model_profile"] = "test_model_profile"
    id: str = Field(min_length=1, max_length=255)
    previous_id: str | None = Field(default=None, max_length=255)
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=255)
    api_key_action: Literal["preserve", "replace", "clear"] = "preserve"
    api_key: str | None = Field(default=None, max_length=10_000)
    base_url: str = Field(default="", max_length=2_048)


class _TestModelProfileResponse(EngineViewModel):
    ok: bool
    detail: str
    latency_ms: int | None = None
    supports_thinking: bool
    supports_multi_turn: bool


class _SettingsRouteBody(_StrictEngineCommandModel):
    primary_profile_id: str = Field(default="", max_length=255)
    fallback_routes: list[dict[str, Any]] = Field(default_factory=list, max_length=3)
    thinking_enabled: bool = False
    multi_turn_enabled: bool = False
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class _SaveSettingsBody(_StrictEngineCommandModel):
    kind: Literal["save_settings"] = "save_settings"
    default_profile_id: str | None = Field(default=None, max_length=255)
    profiles: list[dict[str, Any]] | None = Field(default=None, max_length=128)
    routes: dict[str, _SettingsRouteBody] | None = Field(default=None, max_length=256)
    creative_temperature: dict[str, Any] | None = None
    theme_id: str | None = Field(default=None, max_length=160)
    font_preferences: dict[str, Any] | None = None
    creation_parameters: dict[str, str] | None = Field(default=None, max_length=512)
    chapter_runtime_policy: dict[str, Any] | None = None


class _SaveSettingsResponse(EngineViewModel):
    status: Literal["saved", "partial", "validation_error"]
    persistence: Literal["persisted", "accepted_only"]
    message: str
    accepted_route_ids: list[str] = Field(default_factory=list)
    rejected_routes: list[dict[str, str]] = Field(default_factory=list)
    saved_at_label: str
    runtime_reload_status: Literal["reloaded", "current", "unavailable", "failed"]


class _DeleteProjectsBody(_StrictEngineCommandModel):
    kind: Literal["delete_projects"] = "delete_projects"
    project_ids: list[str] = Field(min_length=1, max_length=100)


class _DeleteProjectFailureView(EngineViewModel):
    project_id: str
    reason: Literal[
        "active_job",
        "delete_failed",
        "invalid_project_id",
        "project_not_found",
    ]
    message: str


class _DeleteProjectsResponse(EngineViewModel):
    status: Literal["deleted", "partial", "rejected"]
    message: str
    deleted_project_ids: list[str] = Field(default_factory=list)
    failures: list[_DeleteProjectFailureView] = Field(default_factory=list)


class _OllamaRevisionBody(_StrictEngineCommandModel):
    expected_revision: str = Field(min_length=8, max_length=128)
    idempotency_key: str = Field(min_length=8, max_length=160)


class _ConfigureOllamaRuntimeBody(_OllamaRevisionBody):
    kind: Literal["configure_ollama_runtime"] = "configure_ollama_runtime"
    base_url: str | None = Field(default=None, max_length=1024)
    enabled: bool | None = None
    auto_start: bool | None = None
    prefer_local: bool | None = None
    binary_path: str | None = Field(default=None, max_length=4096)
    models_dir: str | None = Field(default=None, max_length=4096)


class _SetOllamaModelRolesBody(_OllamaRevisionBody):
    kind: Literal["set_ollama_model_roles"] = "set_ollama_model_roles"
    model: str = Field(min_length=1, max_length=160)
    managed: bool
    generation: bool
    embedding: bool


class _OllamaRuntimeCommandBody(_OllamaRevisionBody):
    kind: Literal["ensure_ollama_runtime", "restart_ollama_runtime", "stop_ollama_runtime"]


class _PullOllamaModelBody(_OllamaRevisionBody):
    kind: Literal["pull_ollama_model"] = "pull_ollama_model"
    model: str = Field(min_length=1, max_length=160)


class _DeleteOllamaModelBody(_OllamaRevisionBody):
    kind: Literal["delete_ollama_model"] = "delete_ollama_model"
    model: str = Field(min_length=1, max_length=160)
    confirmation_token: str = Field(min_length=20, max_length=256)
    cascade_configuration: bool = False


class _NarrativeSubplotEventBody(_StrictEngineCommandModel):
    chapter_number: int = Field(ge=1)
    event: str = ""
    weave_notes: str = ""
    depends_on: list[str] = Field(default_factory=list)


class _NarrativeSubplotWeaveLinkBody(_StrictEngineCommandModel):
    source_type: str = ""
    source_ref: str = ""
    target_subplot: str = "主线"
    trigger_chapter: int = Field(default=0, ge=0)
    link_type: str = ""
    description: str = ""


class _NarrativeSubplotBody(_StrictEngineCommandModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    involved_chapters: list[int] = Field(default_factory=list)
    chapter_events: list[_NarrativeSubplotEventBody] = Field(default_factory=list)
    weave_links: list[_NarrativeSubplotWeaveLinkBody] = Field(default_factory=list)
    priority: Literal["primary", "normal", "background"] = "normal"
    resolution_chapter: int = Field(default=0, ge=0)
    resolution_target: str = ""
    resolution_type: Literal["", "resolve", "reveal", "ascend", "merge"] = ""


class _SaveNarrativeSubplotsBody(_StrictEngineCommandModel):
    kind: Literal["save_narrative_subplots"] = "save_narrative_subplots"
    project_id: str = Field(min_length=1, max_length=255)
    subplots: list[_NarrativeSubplotBody] = Field(default_factory=list)
    expected_revision: str | None = None


class _GenerateNarrativeSubplotsBody(_StrictEngineCommandModel):
    kind: Literal["generate_narrative_subplots"] = "generate_narrative_subplots"
    project_id: str = Field(min_length=1, max_length=255)
    user_hint: str = Field(default="", max_length=4000)
    count: int = Field(default=2, ge=1, le=6)


class _ConvertNarrativeArcsToSubplotsBody(_StrictEngineCommandModel):
    kind: Literal["convert_narrative_arcs_to_subplots"] = "convert_narrative_arcs_to_subplots"
    project_id: str = Field(min_length=1, max_length=255)
    arc_ids: list[str] = Field(min_length=1, max_length=24)
    expected_revision: str | None = None


class _NarrativeSubplotMutationResponse(EngineViewModel):
    status: Literal["saved", "candidate", "conflict", "rejected"]
    proposal_id: str | None = None
    message: str
    blueprint_revision: str | None = None


class _GenerateNarrativeSubplotsResponse(EngineViewModel):
    status: Literal["generated", "rejected"]
    message: str
    candidates: list[_NarrativeSubplotBody] = Field(default_factory=list)


class _SaveNarrativeCharacterBody(_StrictEngineCommandModel):
    kind: Literal["save_narrative_character"] = "save_narrative_character"
    project_id: str = Field(min_length=1, max_length=255)
    character_id: str | None = Field(default=None, max_length=255)
    profile: dict[str, Any] = Field(default_factory=dict)
    expected_revision: str = Field(min_length=1, max_length=128)


class _RetireNarrativeCharacterBody(_StrictEngineCommandModel):
    kind: Literal["retire_narrative_character"] = "retire_narrative_character"
    project_id: str = Field(min_length=1, max_length=255)
    character_id: str = Field(min_length=1, max_length=255)
    expected_revision: str = Field(min_length=1, max_length=128)


class _SaveNarrativeRelationshipBody(_StrictEngineCommandModel):
    kind: Literal["save_narrative_relationship"] = "save_narrative_relationship"
    project_id: str = Field(min_length=1, max_length=255)
    source_character_id: str = Field(min_length=1, max_length=255)
    target_character_id: str = Field(min_length=1, max_length=255)
    relation_type: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=8000)
    expected_revision: str = Field(min_length=1, max_length=128)


class _RemoveNarrativeRelationshipBody(_StrictEngineCommandModel):
    kind: Literal["remove_narrative_relationship"] = "remove_narrative_relationship"
    project_id: str = Field(min_length=1, max_length=255)
    source_character_id: str = Field(min_length=1, max_length=255)
    target_character_id: str = Field(min_length=1, max_length=255)
    expected_revision: str = Field(min_length=1, max_length=128)


class _NarrativeCharacterMutationResponse(EngineViewModel):
    proposal_id: str | None = None
    status: Literal["saved", "candidate", "conflict", "rejected"]
    message: str
    character_revision: str | None = None
    invalidated_chapters: list[int] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class _HumanizePatternInputBody(_StrictEngineCommandModel):
    pattern_id: str | None = Field(default=None, max_length=255)
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(default="", max_length=200)
    severity: str = Field(default="medium", max_length=32)
    keywords: list[str] = Field(default_factory=list, max_length=100)
    notes: str = Field(default="", max_length=8000)
    example_phrase: str = Field(default="", max_length=4000)
    source: Literal["user", "imported"] = "user"


class _SaveHumanizePatternBody(_StrictEngineCommandModel):
    kind: Literal["save_humanize_pattern"] = "save_humanize_pattern"
    project_id: str = Field(min_length=1, max_length=255)
    pattern_id: str | None = Field(default=None, max_length=255)
    pattern: _HumanizePatternInputBody
    expected_revision: str = Field(min_length=1, max_length=128)


class _SetHumanizePatternEnabledBody(_StrictEngineCommandModel):
    kind: Literal["set_humanize_pattern_enabled"] = "set_humanize_pattern_enabled"
    project_id: str = Field(min_length=1, max_length=255)
    pattern_id: str = Field(min_length=1, max_length=255)
    enabled: bool
    expected_revision: str = Field(min_length=1, max_length=128)


class _RemoveHumanizePatternBody(_StrictEngineCommandModel):
    kind: Literal["remove_humanize_pattern"] = "remove_humanize_pattern"
    project_id: str = Field(min_length=1, max_length=255)
    pattern_id: str = Field(min_length=1, max_length=255)
    expected_revision: str = Field(min_length=1, max_length=128)


class _MergeHumanizePatternsBody(_StrictEngineCommandModel):
    kind: Literal["merge_humanize_patterns"] = "merge_humanize_patterns"
    project_id: str = Field(min_length=1, max_length=255)
    source_pattern_id: str = Field(min_length=1, max_length=255)
    target_pattern_id: str = Field(min_length=1, max_length=255)
    expected_revision: str = Field(min_length=1, max_length=128)


class _HumanizePatternViewResponse(EngineViewModel):
    id: str
    name: str
    source_label: str
    category: str
    severity: str
    hit_count: int
    last_chapter_label: str
    enabled: bool
    keywords: list[str] = Field(default_factory=list)
    notes: str = ""
    example_phrase: str = ""


class _HumanizeLibraryMutationResponse(EngineViewModel):
    status: Literal["saved", "conflict", "rejected"]
    message: str
    humanize_library_revision: str | None = None
    pattern: _HumanizePatternViewResponse | None = None


class _PrepareChapterBody(EngineViewModel):
    kind: str = "prepare_chapter"
    project_id: str
    chapter_number: int
    notes: str | None = None
    force: bool = False
    writing_mode: Literal["whole_chapter", "scene_level"] = "whole_chapter"
    rewrite_strategy: Literal["auto", "sequential", "compatible", "reconstruct", "surgical"] = (
        "auto"
    )


class _CancelChapterBody(EngineViewModel):
    kind: str = "cancel_chapter"
    project_id: str
    chapter_number: int


class _CancelJobBody(_StrictEngineCommandModel):
    """Optional audit context for a durable job cancellation."""

    reason: str = Field(default="用户已取消", min_length=1, max_length=500)


class _ResolveChapterCheckpointBody(_StrictEngineCommandModel):
    kind: str = "resolve_chapter_checkpoint"
    project_id: str
    chapter_number: int = Field(ge=1)
    checkpoint_id: str = Field(min_length=1)
    option_id: str = Field(min_length=1)
    notes: str = ""
    force: bool = False
    authoring_approval_id: str = ""


class _PolishChapterBody(_StrictEngineCommandModel):
    kind: str = "polish_chapter"
    project_id: str
    chapter_number: int = Field(ge=1)
    notes: str = ""


class _RepairContinuityBody(_StrictEngineCommandModel):
    kind: Literal["repair_continuity"] = "repair_continuity"
    project_id: str
    chapter_number: int = Field(ge=1)
    issue_indices: list[int] = Field(default_factory=list)
    issue_signatures: list[str] = Field(default_factory=list)
    synthetic_issues: list[dict[str, Any]] = Field(default_factory=list)
    repair_control_mode: Literal["manual", "ai_assisted", "ai_auto"] | None = None


class _RepairCausalBody(_StrictEngineCommandModel):
    kind: Literal["repair_causal"] = "repair_causal"
    project_id: str
    chapter_number: int = Field(ge=1)
    issue_indices: list[int] = Field(default_factory=list)
    issue_signatures: list[str] = Field(default_factory=list)
    synthetic_issues: list[dict[str, Any]] = Field(default_factory=list)
    allow_exhausted_retry: bool = False
    repair_control_mode: Literal["manual", "ai_assisted", "ai_auto"] | None = None


class _RepairIssuesBody(_StrictEngineCommandModel):
    kind: Literal["repair_issues"] = "repair_issues"
    project_id: str
    chapter_number: int = Field(ge=1)
    continuity_issue_indices: list[int] = Field(default_factory=list)
    causal_issue_indices: list[int] = Field(default_factory=list)
    continuity_issue_signatures: list[str] = Field(default_factory=list)
    causal_issue_signatures: list[str] = Field(default_factory=list)
    continuity_synthetic_issues: list[dict[str, Any]] = Field(default_factory=list)
    causal_synthetic_issues: list[dict[str, Any]] = Field(default_factory=list)
    allow_exhausted_retry: bool = False
    repair_control_mode: Literal["manual", "ai_assisted", "ai_auto"] | None = None


class _ReevaluateChapterBody(_StrictEngineCommandModel):
    kind: Literal["reevaluate_chapter"] = "reevaluate_chapter"
    project_id: str
    chapter_number: int = Field(ge=1)


class _ReextractRelationshipsBody(_StrictEngineCommandModel):
    kind: Literal["reextract_relationships"] = "reextract_relationships"
    project_id: str
    chapter_number: int = Field(default=0, ge=0)


class _RepairMotifHistoryBody(_StrictEngineCommandModel):
    kind: Literal["repair_motif_history"] = "repair_motif_history"
    project_id: str
    chapter_number: int = Field(ge=1)
    force_re_extract: bool = False
    start_chapter: int = Field(default=1, ge=1)
    end_chapter: int | None = Field(default=None, ge=1)


class _PolishOutlineBody(_StrictEngineCommandModel):
    kind: str = "polish_outline"
    project_id: str
    user_hint: str = ""
    selected_suggestions: list[str] = Field(default_factory=list)
    focus_fields: list[str] = Field(default_factory=list)
    chapter_range: str = ""
    analysis_only: bool = False
    sync_contracts: bool = True


class _SyncChapterContractsBody(_StrictEngineCommandModel):
    kind: str = "sync_chapter_contracts"
    project_id: str
    affected_chapter_numbers: list[int] = Field(default_factory=list)
    cascade_downstream: bool = True
    rebuild_milestones: bool = True
    mark_stale: bool = True
    prose_untouched: Literal[True] = True
    max_cascade_depth: int = Field(default=3, ge=1, le=10)


class _ExtendOutlineBody(_StrictEngineCommandModel):
    kind: str = "extend_outline"
    project_id: str
    additional_chapters: int | None = Field(default=None, ge=1)
    target_total: int | None = Field(default=None, ge=1, le=10000)
    decommission_old_ending: bool = True
    sync_contracts: bool = True
    reason: str = "extend_outline"


class _AuditBookBody(_StrictEngineCommandModel):
    kind: str = "audit_book"
    project_id: str
    chapter_range: list[int] = Field(default_factory=list)
    analysis_mode: Literal["auto", "summary", "full_text"] = "auto"
    two_phase_enabled: bool = True
    two_phase_max_target_chapters: int = Field(default=24, ge=1, le=500)
    location_strictness: Literal["strict", "balanced", "loose"] = "balanced"
    max_tokens: int | None = Field(default=None, ge=512, le=65536)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    audit_max_chapters_per_batch: int = Field(default=12, ge=1, le=500)
    audit_max_issues_per_chunk: int = Field(default=12, ge=1, le=50)
    audit_issue_pool_max_items: int = Field(default=160, ge=0, le=1000)
    two_phase_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    chapter_max_chars: int | None = Field(default=None, ge=1000, le=100000)
    parallel_chunks: bool = True
    parallel_dimensions: bool = True
    prompt_hint: str = ""


def _default_ready_repair_statuses() -> list[Literal["ready"]]:
    return ["ready"]


class _ExecuteGlobalRepairQueueBody(_StrictEngineCommandModel):
    kind: str = "execute_global_repair_queue"
    project_id: str
    run_id: str = ""
    statuses: list[Literal["ready"]] = Field(default_factory=_default_ready_repair_statuses)
    max_items: int = Field(default=20, ge=1, le=500)
    verify_before_apply: bool = True
    rollback_on_failure: bool = True
    concurrency: int = Field(default=1, ge=1, le=8)


class _AuditBookEditorialBody(_StrictEngineCommandModel):
    kind: str = "audit_book_editorial"
    project_id: str
    chapter_range: list[int] = Field(default_factory=list)
    prompt_hint: str = ""
    max_tokens: int = Field(default=8192, ge=512, le=65536)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    batch_size: int = Field(default=12, ge=1, le=100)
    batch_timeout_s: float = Field(default=0.0, ge=0.0)


class _ExportBookBody(_StrictEngineCommandModel):
    kind: str = "export_book"
    project_id: str
    format: Literal["markdown", "txt", "epub"] = "markdown"
    chapter_range: list[int] = Field(default_factory=list)
    book_title: str = ""


class _CleanChaptersBody(_StrictEngineCommandModel):
    kind: str = "clean_chapters"
    project_id: str
    from_chapter: int = Field(ge=1)


class _SaveChapterRevisionBody(_StrictEngineCommandModel):
    kind: str = "save_chapter_revision"
    project_id: str
    chapter_number: int = Field(ge=1)
    text: str = Field(min_length=1)
    expected_revision: str | None = None
    scope: Literal["none", "next", "volume", "downstream"] = "downstream"
    background_reevaluate: bool = False


class _SaveChapterRevisionResponse(EngineViewModel):
    status: Literal["applied", "noop", "conflict", "rejected"]
    message: str
    revision: str | None = None
    latest_text: str | None = None
    background_reevaluate_scheduled: bool | None = None


class _GenerateChapterRevisionCandidateBody(_StrictEngineCommandModel):
    kind: str = "generate_chapter_revision_candidate"
    project_id: str
    chapter_number: int = Field(ge=1)
    chapter_title: str = Field(default="", max_length=500)
    selected_text: str = Field(min_length=1, max_length=MAX_SELECTION_REVISION_CHARS)
    before_context: str = Field(default="", max_length=900)
    after_context: str = Field(default="", max_length=900)
    instruction: str = Field(default="", max_length=4_000)


class _GenerateChapterRevisionCandidateResponse(EngineViewModel):
    status: Literal["generated", "rejected"]
    message: str
    replacement: str | None = None


class _TokenDashboardPreferencesBody(_StrictEngineCommandModel):
    kind: str = "save_token_dashboard_preferences"
    project_id: str
    currency: Literal["USD", "CNY", "EUR"] = "USD"
    exchange_rates: dict[str, float] = Field(default_factory=dict)
    step_waterfall_filter: Literal["all", "init", "chapter", "repair"] = "all"
    price_per_million: float | None = Field(default=None, ge=0)
    price_unit: Literal["million", "thousand"] | None = None
    model_price_per_million: dict[str, float] | None = None
    model_price_unit: dict[str, Literal["million", "thousand"]] | None = None
    expected_revision: str | None = None


class _TokenDashboardPreferencesView(EngineViewModel):
    currency: Literal["USD", "CNY", "EUR"]
    exchange_rates: dict[str, float]
    step_waterfall_filter: Literal["all", "init", "chapter", "repair"]
    price_per_million: float
    price_unit: Literal["million", "thousand"]
    model_price_per_million: dict[str, float]
    model_price_unit: dict[str, Literal["million", "thousand"]]
    revision: str


class _SaveTokenDashboardPreferencesResponse(EngineViewModel):
    status: Literal["saved", "conflict", "rejected"]
    message: str
    revision: str | None = None
    preferences: _TokenDashboardPreferencesView | None = None


class _ResumeJobBody(_StrictEngineCommandModel):
    kind: str = "resume_job"
    task_id: str


class _RetryInitRepairBody(_StrictEngineCommandModel):
    kind: Literal["retry_init_repair"] = "retry_init_repair"
    project_id: str = Field(min_length=1, max_length=255)
    reset_repair_history: bool = False


class _SaveInitManualRepairBody(_StrictEngineCommandModel):
    kind: Literal["save_init_manual_repair"] = "save_init_manual_repair"
    project_id: str = Field(min_length=1, max_length=255)
    artifact: Literal["blueprint", "outline", "chapter_contracts"]
    payload: dict[str, Any]
    expected_revision: str = Field(min_length=1, max_length=128)


class _RebuildMemoryVectorsBody(_StrictEngineCommandModel):
    kind: Literal["rebuild_memory_vectors"] = "rebuild_memory_vectors"
    project_id: str = Field(min_length=1, max_length=255)
    include_expression: bool = True
    from_chapter: int | None = Field(default=None, ge=1)
    to_chapter: int | None = Field(default=None, ge=1)


class _ClearJobHistoryBody(_StrictEngineCommandModel):
    kind: Literal["clear_job_history"] = "clear_job_history"
    task_ids: list[str] | None = None


class _ClearJobHistoryResponse(EngineViewModel):
    status: Literal["cleared", "rejected"]
    message: str
    cleared_task_ids: list[str] = Field(default_factory=list)


class _TaskErrorResolutionBody(_StrictEngineCommandModel):
    error_entry_ids: list[str] = Field(min_length=1, max_length=1000)


class _AcknowledgeTaskErrorsBody(_TaskErrorResolutionBody):
    kind: Literal["acknowledge_task_errors"] = "acknowledge_task_errors"


class _ReopenTaskErrorsBody(_TaskErrorResolutionBody):
    kind: Literal["reopen_task_errors"] = "reopen_task_errors"


class _TaskErrorResolutionResponse(EngineViewModel):
    status: Literal["acknowledged", "reopened"]
    message: str
    updated_error_entry_ids: list[str] = Field(default_factory=list)


class _ClearClosedTaskErrorsBody(_TaskErrorResolutionBody):
    kind: Literal["clear_closed_task_errors"] = "clear_closed_task_errors"


class _ClearClosedTaskErrorsResponse(EngineViewModel):
    status: Literal["cleared"] = "cleared"
    message: str
    cleared_task_ids: list[str] = Field(default_factory=list)
    cleared_error_entry_ids: list[str] = Field(default_factory=list)
    retained_pending_error_entry_ids: list[str] = Field(default_factory=list)


class _ClearErrorArchiveBody(_StrictEngineCommandModel):
    kind: Literal["clear_error_archive"] = "clear_error_archive"
    project_id: str | None = Field(default=None, min_length=1, max_length=255)


class _ClearErrorArchiveResponse(EngineViewModel):
    status: Literal["cleared"] = "cleared"
    message: str
    removed_project_count: int = Field(ge=0)


class _RestartLongInitBody(_StrictEngineCommandModel):
    kind: Literal["restart_long_init"] = "restart_long_init"
    project_id: str = Field(min_length=1, max_length=255)


class _RestartLongInitResponse(EngineViewModel):
    status: Literal["reset", "rejected"]
    message: str
    cleared_task_ids: list[str] = Field(default_factory=list)
    removed_artifact_count: int = Field(default=0, ge=0)


class _WorkflowBlueprintPreferenceItem(_StrictEngineCommandModel):
    element_id: str
    enabled: bool | None = None
    locked: bool = False
    weight: int = Field(default=50, ge=0, le=100)


class _WorkflowBlueprintPreferences(_StrictEngineCommandModel):
    preset_id: str = ""
    manual_override: bool = False
    items: list[_WorkflowBlueprintPreferenceItem] = Field(default_factory=list)


class _ShortWorkflowPayload(_StrictEngineCommandModel):
    project_id: str = ""
    theme: str
    genre: str = ""
    tone: str = "neutral"
    length_target: int = Field(default=3200, ge=500, le=50000)
    max_edit_rounds: int = Field(default=2, ge=0, le=10)
    segment_trigger_words: int = Field(default=6000, ge=1500, le=50000)
    writing_mode: Literal["auto", "whole_chapter", "scene_level"] = "auto"
    title: str = ""
    language: str = "zh"
    characters_hint: str = ""
    world_hint: str = ""
    conflict_hint: str = ""
    pov_hint: str = ""
    opening_style: str = ""
    ending_style: str = ""
    extra_instructions: str = ""
    research_enabled: bool = False
    research_provider: str = "auto"
    research_query_hint: str = ""
    blueprint_element_preferences: _WorkflowBlueprintPreferences = Field(
        default_factory=_WorkflowBlueprintPreferences
    )


class _LongInitWorkflowPayload(_StrictEngineCommandModel):
    project_id: str = ""
    premise: str
    genre: str = ""
    tone: str = "neutral"
    total_chapters: int = Field(default=24, ge=1, le=10000)
    words_per_chapter: int = Field(default=4500, ge=500, le=20000)
    volume_mode: Literal["auto", "on", "off"] = "auto"
    chapters_per_volume: int = Field(default=0, ge=0, le=500)
    title: str = ""
    language: str = "zh"
    characters_hint: str = ""
    world_hint: str = ""
    conflict_hint: str = ""
    pov_hint: str = ""
    opening_style: str = ""
    ending_style: str = ""
    extra_instructions: str = ""
    polish_hint: str = ""
    research_enabled: bool = False
    research_provider: str = "auto"
    research_query_hint: str = ""
    regenerate_outline: bool = False
    blueprint_element_preferences: _WorkflowBlueprintPreferences = Field(
        default_factory=_WorkflowBlueprintPreferences
    )
    copilot_gates: list[str] = Field(default_factory=list)
    creative_exploration: Literal["adaptive", "single"] = "adaptive"
    planning_commitment: Literal["progressive", "full"] = "full"


class _ContinueLongInitBody(_StrictEngineCommandModel):
    kind: Literal["continue_long_init"] = "continue_long_init"
    project_id: str = Field(min_length=1, max_length=255)
    run_mode: Literal["create", "copilot", "autorun"] = "create"
    fallback_payload: _LongInitWorkflowPayload | None = None


class _LongChapterWorkflowPayload(_StrictEngineCommandModel):
    project_id: str
    chapter_number: int = Field(ge=1)
    force: bool = False
    writing_mode: Literal["whole_chapter", "scene_level"] = "whole_chapter"
    autorun_scope: Literal["chapter", "book"] = "book"
    skip_done: bool = True


class _StartWorkflowBody(_StrictEngineCommandModel):
    kind: str = "start_workflow"
    project_id: str
    workflow_type: Literal["short", "long_init", "long_chapter"]
    run_mode: Literal["create", "copilot", "autorun"] = "create"
    idempotency_key: str = Field(min_length=1, max_length=128)
    payload: _ShortWorkflowPayload | _LongInitWorkflowPayload | _LongChapterWorkflowPayload


class WorkflowDraftView(EngineViewModel):
    mode: Literal["short", "long"]
    payload: dict[str, Any] | None = None
    revision: str = ""
    saved_at_label: str = ""


class WorkflowPresetRecordView(EngineViewModel):
    name: str
    payload: dict[str, Any]
    revision: str
    updated_at_label: str


class WorkflowPresetListView(EngineViewModel):
    presets: list[WorkflowPresetRecordView] = Field(default_factory=list)


class WorkflowAiHistoryEntryView(EngineViewModel):
    id: str
    timestamp: str
    operation: str
    data: dict[str, Any]
    hint: str | None = None
    selected_suggestions: list[str] = Field(default_factory=list)
    focus_fields: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] | None = None


class WorkflowAiHistoryListView(EngineViewModel):
    entries: list[WorkflowAiHistoryEntryView] = Field(default_factory=list)


class _WorkflowPersistenceBody(EngineViewModel):
    payload: dict[str, Any]
    expected_revision: str | None = None


class _WorkflowAiHistoryBody(EngineViewModel):
    operation: str = Field(min_length=1, max_length=80)
    data: dict[str, Any] = Field(default_factory=dict)
    hint: str = ""
    selected_suggestions: list[str] = Field(default_factory=list)
    focus_fields: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] | None = None


class WorkflowPersistenceResponse(EngineViewModel):
    status: Literal["saved", "deleted", "conflict", "not_found", "rejected"]
    message: str
    revision: str | None = None
    saved_at_label: str | None = None


class _GenerateWorkflowFieldsBody(_StrictEngineCommandModel):
    kind: Literal["generate_workflow_fields"] = "generate_workflow_fields"
    mode: Literal["short", "long"]
    operation: Literal["generate", "polish"]
    current_payload: dict[str, Any] = Field(default_factory=dict)
    user_hint: str = ""
    generation_mode: Literal["replace", "fill_blanks", "variant"] = "replace"
    creative_profile: dict[str, Any] = Field(default_factory=dict)
    hard_constraints: dict[str, Any] = Field(default_factory=dict)
    selected_suggestions: list[str] = Field(default_factory=list)
    focus_fields: list[str] = Field(default_factory=list)


class _GenerateWorkflowFieldsResponse(EngineViewModel):
    status: Literal["generated", "rejected"]
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
    suggestions: list[str] = Field(default_factory=list)
    creative_note: dict[str, Any] | None = None


class _SynthesizeVoiceBody(EngineViewModel):
    kind: str = "synthesize_voice"
    project_id: str
    chapter_number: int
    segment_ids: list[str] | None = None
    provider: str = ""


class _BuildVoiceTeamBody(_StrictEngineCommandModel):
    kind: str = "build_voice_team"
    project_id: str
    provider: str = ""
    rebuild_character_ids: list[str] = Field(default_factory=list)


class _RebuildNarratorVoiceBody(_StrictEngineCommandModel):
    kind: str = "rebuild_narrator_voice"
    project_id: str
    provider: str = ""


class _ConfirmVoiceTeamBody(_StrictEngineCommandModel):
    kind: str = "confirm_voice_team"
    project_id: str


class _CloneCharacterVoiceBody(_StrictEngineCommandModel):
    kind: str = "clone_character_voice"
    project_id: str
    character_id: str = Field(min_length=1)
    reference_audio: str = Field(min_length=1)
    reference_transcript: str = ""
    authorized: bool
    provider: str = ""


class _DesignCharacterVoiceBody(_StrictEngineCommandModel):
    kind: str = "design_character_voice"
    project_id: str
    character_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    provider: str = ""


class _ApproveCharacterVoiceBody(_StrictEngineCommandModel):
    kind: str = "approve_character_voice"
    project_id: str
    character_id: str = Field(min_length=1)


class _PreviewCharacterVoiceBody(_StrictEngineCommandModel):
    kind: str = "preview_character_voice"
    project_id: str
    character_id: str = Field(min_length=1)
    sample_text: str = ""
    provider: str = ""


class _BuildVoicePreviewPlanBody(_StrictEngineCommandModel):
    kind: str = "build_voice_preview_plan"
    project_id: str
    characters: list[dict[str, Any]] = Field(default_factory=list)
    provider: str = ""
    sample_text: str = ""
    candidate_count: int = Field(default=3, ge=1, le=6)
    language: str = "zh"


class _GenerateVoicePreviewsBody(_StrictEngineCommandModel):
    kind: str = "generate_voice_previews"
    project_id: str
    plan: dict[str, Any]
    provider: str = ""


class _ConfirmVoicePreviewBody(_StrictEngineCommandModel):
    kind: str = "confirm_voice_preview"
    project_id: str
    character_id: str = Field(min_length=1)
    voice_id: str = Field(min_length=1)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    volume: float = Field(default=1.0, ge=0.0, le=2.0)
    provider: str = ""
    model_id: str = ""
    sample_text: str = ""
    sample_path: str = ""


class _UpdateVoicePerformanceBody(_StrictEngineCommandModel):
    kind: str = "update_voice_performance"
    project_id: str
    character_id: str = Field(min_length=1)
    speed_offset: float = Field(ge=-0.5, le=0.5)
    pitch_offset: int = Field(ge=-12, le=12)
    volume_offset: float = Field(ge=-0.5, le=0.5)


class _AssignCatalogVoiceBody(_StrictEngineCommandModel):
    kind: str = "assign_catalog_voice"
    project_id: str
    character_id: str = Field(min_length=1)
    voice_id: str = Field(min_length=1)
    provider: str = ""


class _GenerateVoiceScriptBody(_StrictEngineCommandModel):
    kind: str = "generate_voice_script"
    project_id: str
    chapter_number: int = Field(ge=1)
    provider: str = ""
    reference_style_strength: float = Field(default=0.65, ge=0.0, le=1.0)


class _AnalyzeVoiceScriptStyleBody(_StrictEngineCommandModel):
    kind: str = "analyze_voice_script_style"
    project_id: str
    source_name: str = Field(default="参考配音脚本", max_length=200)
    reference_script_text: str = Field(min_length=1, max_length=200_000)


class _VoiceScriptEditBody(EngineViewModel):
    segment_index: int = Field(ge=0)
    content: str = Field(min_length=1)
    speaker_id: str = ""
    speaker_label: str = ""
    segment_type: Literal["narration", "dialogue", "inner_thought"] | None = None
    emotion_label: str = ""
    emotion_intensity: float | None = Field(default=None, ge=0.0, le=1.0)
    tone_hint: str = ""
    speed_override: float | None = Field(default=None, ge=0.5, le=2.0)
    volume_override: float | None = Field(default=None, ge=0.0, le=2.0)
    pitch_override: int | None = Field(default=None, ge=-12, le=12)
    stress_words: list[str] = Field(default_factory=list)
    narrator_distance: str = ""
    pronunciation_overrides: list[str] = Field(default_factory=list)
    language_code: str = "auto"


class _SaveVoiceScriptBody(_StrictEngineCommandModel):
    kind: str = "save_voice_script"
    project_id: str
    chapter_number: int = Field(ge=1)
    edits: list[_VoiceScriptEditBody] = Field(min_length=1)


class _VoiceGuidanceEditBody(EngineViewModel):
    segment_index: int = Field(ge=0)
    segment_override: dict[str, Any]


class _SaveVoiceGuidanceBody(_StrictEngineCommandModel):
    kind: Literal["save_voice_guidance"] = "save_voice_guidance"
    project_id: str
    chapter_number: int = Field(ge=1)
    edits: list[_VoiceGuidanceEditBody] = Field(min_length=1)


class _PreviewVoiceSegmentBody(_StrictEngineCommandModel):
    kind: str = "preview_voice_segment"
    project_id: str
    chapter_number: int = Field(ge=1)
    segment_index: int = Field(ge=0)
    provider: str = ""
    segment_override: dict[str, Any] | None = None


class _AcceptVoiceTakeBody(_StrictEngineCommandModel):
    kind: str = "accept_voice_take"
    project_id: str
    chapter_number: int = Field(ge=1)
    take_id: str = Field(min_length=1)


class _ReassembleVoiceBody(_StrictEngineCommandModel):
    kind: str = "reassemble_voice"
    project_id: str
    chapter_number: int = Field(ge=1)
    fast: bool = False


class _FullVoicePipelineBody(_StrictEngineCommandModel):
    kind: str = "full_voice_pipeline"
    project_id: str
    chapter_number: int = Field(ge=1)
    automation_mode: Literal["manual", "assisted", "autonomous"] = "assisted"
    provider: str = ""


class _RejectVoiceTakeBody(_StrictEngineCommandModel):
    kind: str = "reject_voice_take"
    project_id: str
    chapter_number: int = Field(ge=1)
    take_id: str = Field(min_length=1)


class _ClearVoiceArtifactsBody(_StrictEngineCommandModel):
    kind: str = "clear_voice_artifacts"
    project_id: str
    chapter_number: int | None = Field(default=None, ge=1)
    chapter_numbers: list[int] = Field(default_factory=list)
    categories: list[
        Literal[
            "stale_previews",
            "orphan_candidates",
            "completed_checkpoints",
            "orphan_sound_assets",
            "orphan_chapter_reports",
        ]
    ] = Field(default_factory=list)
    scope: Literal[
        "script_chapter",
        "chapter",
        "chapters",
        "redundant_takes",
        "stale_files",
        "project_reset",
    ]


class _GenerateSoundPaletteBody(_StrictEngineCommandModel):
    kind: str = "generate_sound_palette"
    project_id: str


class _UpdateSoundAssetBody(_StrictEngineCommandModel):
    kind: str = "update_sound_asset"
    project_id: str
    asset_id: str = Field(min_length=1)
    action: Literal["set_status", "set_tags", "set_commercial_rights", "publish"]
    status: Literal["approved", "pending", "rejected"] | None = None
    tags: list[str] = Field(default_factory=list)
    commercial_use_status: Literal["cleared", "review_required", "restricted"] | None = None
    license_note: str = Field(default="", max_length=1000)


class _EncodedSoundFileBody(_StrictEngineCommandModel):
    name: str = Field(min_length=1, max_length=255)
    base64: str = Field(min_length=1)


class _ImportSoundAssetsBody(_StrictEngineCommandModel):
    kind: str = "import_sound_assets"
    project_id: str
    asset_kind: Literal["bgm", "soundscape", "sfx"]
    tags: list[str] = Field(default_factory=list)
    files: list[_EncodedSoundFileBody] = Field(min_length=1, max_length=20)


class _SpeakerResolutionBody(EngineViewModel):
    segment_index: int = Field(ge=0)
    character_id: str = ""
    segment_type: str | None = None


class _ResolveSpeakersBody(EngineViewModel):
    kind: str = "resolve_speakers"
    project_id: str
    chapter_number: int = Field(ge=1)
    resolutions: list[_SpeakerResolutionBody] = Field(min_length=1)


class _ExportAudioBody(EngineViewModel):
    kind: str = "export_audio"
    project_id: str
    scope: str = "chapter"
    chapter_number: int | None = None
    format: str = "mp3"
    include_subtitles: bool = False
    target_lufs: float | None = None


class _ExportAudiobookBody(EngineViewModel):
    kind: str = "export_audiobook"
    project_id: str
    chapter_numbers: list[int] = Field(default_factory=list)
    require_delivery_ready: bool = True


def _job_kind_value(record: Any) -> str:
    kind = getattr(record, "kind", "")
    return str(getattr(kind, "value", kind))


def _same_project_write_conflict_message(record: Any, *, requested_kind: str = "") -> str:
    """Explain why a project-scoped writer cannot start beside its predecessor."""

    kind = _job_kind_value(record)
    if kind == "init_long":
        return (
            "该项目正在立项初始化；初始化会写入世界观、大纲和章节契约。"
            "请等待立项完成后再进入章台，其他项目仍可并行处理。"
        )
    if requested_kind == "init_long" and kind in {"prepare_chapter", "run_chapter"}:
        return (
            "该项目正在生成章节；重新立项会重建世界观、大纲和章节契约，"
            "不能与章台并行。请等待当前章节结束后再立项，其他项目仍可并行。"
        )
    if kind in {"prepare_chapter", "run_chapter"}:
        return "该项目已有章节写入任务正在执行；同项目章节必须按顺序生成，以保持故事状态连续。"
    return "该项目已有写入任务正在执行；请等待其完成后再修改同一项目的故事状态。"


@router.post("/commands/prepare-chapter", response_model=EngineCommandResponse)
async def engine_prepare_chapter(
    body: _PrepareChapterBody, service: JobServiceDep
) -> EngineCommandResponse:
    from novel_forge.app_service.contracts import JobCommand

    command = JobCommand(
        kind="prepare_chapter",
        project_id=body.project_id,
        payload={
            "project_id": body.project_id,
            "chapter_number": body.chapter_number,
            "notes": body.notes or "",
            "force": body.force,
            "writing_mode": body.writing_mode,
            "rewrite_strategy": body.rewrite_strategy,
        },
    )
    active_job_ids = {
        record.job_id
        for record in service.list(project_id=body.project_id)
        if record.status.value in {"running", "queued", "paused"}
    }
    record = service.submit(command)
    if record.job_id in active_job_ids:
        return EngineCommandResponse(
            status="already_running",
            message=_same_project_write_conflict_message(record, requested_kind="prepare_chapter"),
        )
    return EngineCommandResponse(
        status="accepted",
        message=f"章节 {body.chapter_number} 准备任务已提交",
        task_id=record.job_id,
    )


@router.post(
    "/commands/test-model-profile",
    response_model=_TestModelProfileResponse,
)
async def engine_test_model_profile(
    body: _TestModelProfileBody,
) -> _TestModelProfileResponse:
    """Probe a saved or in-session profile through the versioned Engine boundary."""

    try:
        result = await probe_model_profile_request(
            ModelProfileProbeRequest(
                profile_id=body.id,
                previous_profile_id=body.previous_id,
                provider=body.provider,
                model=body.model,
                api_key_action=body.api_key_action,
                api_key=body.api_key,
                base_url=body.base_url,
            )
        )
    except ModelProfileProbeRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _TestModelProfileResponse(
        ok=result.ok,
        detail=result.detail,
        latency_ms=result.latency_ms,
        supports_thinking=result.supports_thinking,
        supports_multi_turn=result.supports_multi_turn,
    )


@router.post(
    "/commands/save-settings",
    response_model=_SaveSettingsResponse,
)
async def engine_save_settings(
    body: _SaveSettingsBody,
) -> _SaveSettingsResponse:
    """Persist routing and runtime settings through the versioned Engine API."""

    result = await save_settings_command(
        SettingsSaveCommand(
            default_profile_id=body.default_profile_id,
            profiles=body.profiles,
            routes=(
                {
                    task_key: SettingsRouteCommand(**route.model_dump())
                    for task_key, route in body.routes.items()
                }
                if body.routes is not None
                else None
            ),
            creative_temperature=body.creative_temperature,
            theme_id=body.theme_id,
            font_preferences=body.font_preferences,
            creation_parameters=body.creation_parameters,
            chapter_runtime_policy=body.chapter_runtime_policy,
        ),
        reload_runtime=reload_runtime_dependencies,
    )
    return _SaveSettingsResponse(
        status=result.status,
        persistence=result.persistence,
        message=result.message,
        accepted_route_ids=result.accepted_route_ids,
        rejected_routes=result.rejected_routes,
        saved_at_label=result.saved_at_label,
        runtime_reload_status=result.runtime_reload_status,
    )


@router.post(
    "/commands/delete-projects",
    response_model=_DeleteProjectsResponse,
)
async def engine_delete_projects(
    body: _DeleteProjectsBody,
    service: JobServiceDep,
    storage: StorageDep,
    runtime: RuntimeDep,
) -> _DeleteProjectsResponse:
    """Permanently delete selected project directories after active-job checks."""

    requested_project_ids = list(dict.fromkeys(body.project_ids))
    active_project_ids = {
        record.project_id
        for record in service.list()
        if str(getattr(record.status, "value", record.status)).lower() in {"queued", "running"}
    }
    file_service = ProjectFileService(storage.root)
    deleted_project_ids: list[str] = []
    failures: list[_DeleteProjectFailureView] = []

    for submitted_project_id in requested_project_ids:
        try:
            project_id = normalize_project_id(submitted_project_id)
        except ValueError:
            failures.append(
                _DeleteProjectFailureView(
                    project_id=submitted_project_id,
                    reason="invalid_project_id",
                    message="项目 ID 不合法，已拒绝删除。",
                )
            )
            continue

        if project_id in active_project_ids:
            failures.append(
                _DeleteProjectFailureView(
                    project_id=project_id,
                    reason="active_job",
                    message="项目有正在运行或排队的任务，请先结束任务。",
                )
            )
            continue

        runtime.release_memory_context(project_id)
        try:
            result = await asyncio.to_thread(file_service.delete_project, project_id)
        except (OSError, NotADirectoryError, ValueError) as exc:
            failures.append(
                _DeleteProjectFailureView(
                    project_id=project_id,
                    reason="delete_failed",
                    message=f"项目目录删除失败：{exc}",
                )
            )
            continue

        if not result.deleted:
            failures.append(
                _DeleteProjectFailureView(
                    project_id=project_id,
                    reason="project_not_found",
                    message="项目目录不存在，可能已被删除。",
                )
            )
            continue

        deleted_project_ids.append(project_id)
        inactive_job_ids = {
            record.job_id
            for record in service.list(project_id=project_id)
            if str(getattr(record.status, "value", record.status)).lower()
            not in {"queued", "running"}
        }
        if inactive_job_ids:
            service.clear_inactive_history(inactive_job_ids)

    status: Literal["deleted", "partial", "rejected"] = (
        "deleted"
        if deleted_project_ids and not failures
        else "partial"
        if deleted_project_ids
        else "rejected"
    )
    message = (
        f"已永久删除 {len(deleted_project_ids)} 个项目目录。"
        if not failures
        else (f"已删除 {len(deleted_project_ids)} 个项目，{len(failures)} 个项目未删除。")
    )
    return _DeleteProjectsResponse(
        status=status,
        message=message,
        deleted_project_ids=deleted_project_ids,
        failures=failures,
    )


def _is_loopback_engine_request(request: Request) -> bool:
    client = request.client
    return client is not None and client.host in {"127.0.0.1", "::1", "localhost"}


def _ollama_job_response(record: Any) -> EngineCommandResponse:
    """Translate the shared command result into the generic API envelope."""

    already_running = record.status.value in {"queued", "running"} and bool(record.current_step)
    return EngineCommandResponse(
        status="already_running" if already_running else "accepted",
        message="Ollama 管理任务已提交。",
        task_id=record.job_id,
    )


@router.post("/commands/configure-ollama-runtime", response_model=OllamaManagerView)
async def engine_configure_ollama_runtime(
    body: _ConfigureOllamaRuntimeBody,
    request: Request,
    service: JobServiceDep,
) -> OllamaManagerView:
    """Persist runtime controls while retaining Engine ownership of sidecars."""

    current_settings = get_settings()
    commands = OllamaEngineCommandService(service)
    try:
        await asyncio.to_thread(
            commands.configure_runtime,
            current_settings,
            expected_revision=body.expected_revision,
            base_url=body.base_url,
            enabled=body.enabled,
            auto_start=body.auto_start,
            prefer_local=body.prefer_local,
            binary_path=body.binary_path,
            models_dir=body.models_dir,
            allow_path_configuration=_is_loopback_engine_request(request),
            allow_endpoint_configuration=_is_loopback_engine_request(request),
        )
    except OllamaConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await asyncio.to_thread(commands.view, get_settings())


@router.post("/commands/set-ollama-model-roles", response_model=OllamaManagerView)
async def engine_set_ollama_model_roles(
    body: _SetOllamaModelRolesBody,
    service: JobServiceDep,
) -> OllamaManagerView:
    """Apply Engine-owned model-role and routing configuration."""

    current_settings = get_settings()
    commands = OllamaEngineCommandService(service)
    try:
        await asyncio.to_thread(
            commands.set_model_roles,
            current_settings,
            expected_revision=body.expected_revision,
            model=body.model,
            managed=body.managed,
            generation=body.generation,
            embedding=body.embedding,
        )
    except OllamaConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await asyncio.to_thread(commands.view, get_settings())


@router.post("/commands/ensure-ollama-runtime", response_model=EngineCommandResponse)
@router.post("/commands/restart-ollama-runtime", response_model=EngineCommandResponse)
@router.post("/commands/stop-ollama-runtime", response_model=EngineCommandResponse)
async def engine_control_ollama_runtime(
    body: _OllamaRuntimeCommandBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    """Run lifecycle operations through durable Engine system jobs."""

    commands = OllamaEngineCommandService(service)
    operation_by_kind = {
        "ensure_ollama_runtime": "ensure",
        "restart_ollama_runtime": "restart",
        "stop_ollama_runtime": "stop",
    }
    operation = operation_by_kind[body.kind]
    try:
        record = await asyncio.to_thread(
            commands.submit_runtime,
            get_settings(),
            expected_revision=body.expected_revision,
            idempotency_key=body.idempotency_key,
            operation=operation,
        )
    except OllamaConfigurationError as exc:
        return EngineCommandResponse(status="rejected", message=str(exc))
    return _ollama_job_response(record)


@router.post("/commands/pull-ollama-model", response_model=EngineCommandResponse)
async def engine_pull_ollama_model(
    body: _PullOllamaModelBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    """Queue a recoverable model pull; the browser never opens Ollama directly."""

    commands = OllamaEngineCommandService(service)
    try:
        record = await asyncio.to_thread(
            commands.submit_pull,
            get_settings(),
            expected_revision=body.expected_revision,
            idempotency_key=body.idempotency_key,
            model=body.model,
        )
    except OllamaConfigurationError as exc:
        return EngineCommandResponse(status="rejected", message=str(exc))
    return _ollama_job_response(record)


@router.post("/commands/delete-ollama-model", response_model=EngineCommandResponse)
async def engine_delete_ollama_model(
    body: _DeleteOllamaModelBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    """Commit configuration detachment before idempotent native model deletion."""

    current_settings = get_settings()
    commands = OllamaEngineCommandService(service)
    try:
        record = await asyncio.to_thread(
            commands.submit_delete,
            current_settings,
            expected_revision=body.expected_revision,
            model=body.model,
            idempotency_key=body.idempotency_key,
            confirmation_token=body.confirmation_token,
            cascade_configuration=body.cascade_configuration,
        )
    except OllamaConfigurationError as exc:
        return EngineCommandResponse(status="rejected", message=str(exc))
    return _ollama_job_response(record).model_copy(
        update={"data": {"configurationCheckpoint": "config_detached"}}
    )


@router.post(
    "/commands/resolve-chapter-checkpoint",
    response_model=EngineCommandResponse,
)
async def engine_resolve_chapter_checkpoint(
    body: _ResolveChapterCheckpointBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    from novel_forge.app_service.contracts import JobCommand

    resolve_autorun = getattr(service, "resolve_book_autorun_checkpoint", None)
    autorun_record = (
        resolve_autorun(
            project_id=body.project_id,
            checkpoint_id=body.checkpoint_id,
            option_id=body.option_id,
            notes=body.notes,
            force=body.force,
            **(
                {"authoring_approval_id": body.authoring_approval_id}
                if body.authoring_approval_id
                else {}
            ),
        )
        if callable(resolve_autorun)
        else None
    )
    if autorun_record is not None:
        return EngineCommandResponse(
            status="accepted",
            message="检查点裁决已交由 Engine 连跑状态机继续。",
            task_id=autorun_record.job_id,
        )
    record = service.submit(
        JobCommand(
            kind="resolve_chapter_checkpoint",
            project_id=body.project_id,
            payload=body.model_dump(mode="json", exclude={"kind"}),
        )
    )
    return EngineCommandResponse(
        status="accepted",
        message="检查点裁决已提交，章节任务将从持久化状态继续。",
        task_id=record.job_id,
    )


@router.post("/commands/polish-chapter", response_model=EngineCommandResponse)
async def engine_polish_chapter(
    body: _PolishChapterBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    from novel_forge.app_service.contracts import JobCommand

    record = service.submit(
        JobCommand(
            kind="polish_chapter",
            project_id=body.project_id,
            payload=body.model_dump(mode="json", exclude={"kind"}),
        )
    )
    return EngineCommandResponse(
        status="accepted",
        message=f"第 {body.chapter_number} 章润色任务已提交。",
        task_id=record.job_id,
    )


def _submit_chapter_maintenance(
    service: JobService,
    *,
    kind: JobKind,
    project_id: str,
    payload: dict[str, Any],
    label: str,
) -> EngineCommandResponse:
    """Submit a durable maintenance job shared by both desktop clients."""

    from novel_forge.app_service.contracts import JobCommand

    record = service.submit(
        JobCommand(
            kind=kind,
            project_id=project_id,
            payload=payload,
            label=label,
            metadata={"workflow_type": "chapter_maintenance"},
        )
    )
    return EngineCommandResponse(
        status="accepted", message=f"{label}已提交。", task_id=record.job_id
    )


@router.post("/commands/repair-continuity", response_model=EngineCommandResponse)
async def engine_repair_continuity(
    body: _RepairContinuityBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    request = RepairContinuityRequest.model_validate(body.model_dump(mode="json", exclude={"kind"}))
    return _submit_chapter_maintenance(
        service,
        kind=JobKind.REPAIR_CONTINUITY,
        project_id=request.project_id,
        payload=request.model_dump(mode="json"),
        label=f"连续性修复 · 第 {request.chapter_number} 章",
    )


@router.post("/commands/repair-causal", response_model=EngineCommandResponse)
async def engine_repair_causal(
    body: _RepairCausalBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    request = RepairCausalRequest.model_validate(body.model_dump(mode="json", exclude={"kind"}))
    return _submit_chapter_maintenance(
        service,
        kind=JobKind.REPAIR_CAUSAL,
        project_id=request.project_id,
        payload=request.model_dump(mode="json"),
        label=f"因果链修复 · 第 {request.chapter_number} 章",
    )


@router.post("/commands/repair-issues", response_model=EngineCommandResponse)
async def engine_repair_issues(
    body: _RepairIssuesBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    request = RepairIssuesRequest.model_validate(body.model_dump(mode="json", exclude={"kind"}))
    return _submit_chapter_maintenance(
        service,
        kind=JobKind.REPAIR_ISSUES,
        project_id=request.project_id,
        payload=request.model_dump(mode="json"),
        label=f"综合问题修复 · 第 {request.chapter_number} 章",
    )


@router.post("/commands/reevaluate-chapter", response_model=EngineCommandResponse)
async def engine_reevaluate_chapter(
    body: _ReevaluateChapterBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    request = ReevaluateChapterRequest.model_validate(
        body.model_dump(mode="json", exclude={"kind"})
    )
    return _submit_chapter_maintenance(
        service,
        kind=JobKind.REEVALUATE_CHAPTER,
        project_id=request.project_id,
        payload=request.model_dump(mode="json"),
        label=f"章节重评估 · 第 {request.chapter_number} 章",
    )


@router.post("/commands/reextract-relationships", response_model=EngineCommandResponse)
async def engine_reextract_relationships(
    body: _ReextractRelationshipsBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    request = ReextractRelationshipsRequest.model_validate(
        body.model_dump(mode="json", exclude={"kind"})
    )
    scope = "全部已完成章节" if request.chapter_number == 0 else f"第 {request.chapter_number} 章"
    return _submit_chapter_maintenance(
        service,
        kind=JobKind.REEXTRACT_RELATIONSHIPS,
        project_id=request.project_id,
        payload=request.model_dump(mode="json"),
        label=f"关系重提取 · {scope}",
    )


@router.post("/commands/repair-motif-history", response_model=EngineCommandResponse)
async def engine_repair_motif_history(
    body: _RepairMotifHistoryBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    request = RepairMotifHistoryRequest.model_validate(
        body.model_dump(mode="json", exclude={"kind"})
    )
    return _submit_chapter_maintenance(
        service,
        kind=JobKind.REPAIR_MOTIF_HISTORY,
        project_id=request.project_id,
        payload=request.model_dump(mode="json"),
        label=f"母题历史修复 · 第 {request.chapter_number} 章",
    )


@router.post("/commands/polish-outline", response_model=EngineCommandResponse)
async def engine_polish_outline(
    body: _PolishOutlineBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    """Submit the single durable outline-polish write path for Web clients."""

    from novel_forge.app_service.contracts import JobCommand

    record = service.submit(
        JobCommand(
            kind="polish_outline",
            project_id=body.project_id,
            payload=body.model_dump(mode="json", exclude={"kind"}),
        )
    )
    action = "分析任务" if body.analysis_only else "润色任务"
    return EngineCommandResponse(
        status="accepted",
        message=f"大纲{action}已提交；完成后将刷新受影响章节的契约。",
        task_id=record.job_id,
    )


def _load_blueprint_for_subplot_write(
    storage: FileSystemStorage, project_id: str
) -> tuple[ProjectLayout, dict[str, Any], str]:
    """Load the only artifact a subplot command may mutate plus its revision."""

    layout = ProjectLayout(storage.project_path(project_id))
    if not storage.exists(layout.blueprint_path):
        raise FileNotFoundError("当前项目尚未生成叙事蓝图")
    blueprint = storage.load_json(layout.blueprint_path)
    if not isinstance(blueprint, dict):
        raise ValueError("叙事蓝图不是有效的 JSON 对象")
    revision = hashlib.sha256(layout.blueprint_path.read_bytes()).hexdigest()
    return layout, blueprint, revision


def _persist_subplot_plan(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    blueprint: dict[str, Any],
    *,
    previous_revision: str,
    reason: str,
) -> str | _NarrativeSubplotMutationResponse:
    """Atomically write a complete plan and register whole-book invalidation."""

    from novel_forge.persistence.foundation_guard import require_versioned_foundation_write

    if (layout.root / "authoring_policy.json").is_file():
        from novel_forge.app_service.authoring_foundation import propose_foundation_payload

        with storage.project_lock(layout.root.name):
            if hashlib.sha256(layout.blueprint_path.read_bytes()).hexdigest() != previous_revision:
                raise ValueError("蓝图已在其他窗口更新，请刷新后再提案")
            proposal = propose_foundation_payload(storage, layout.root, "blueprint", blueprint)
        return _NarrativeSubplotMutationResponse(
            status="candidate",
            proposal_id=proposal.id,
            blueprint_revision=previous_revision,
            message="支线修改已保存为蓝图专项提案；正式蓝图未改变，请在共创中核对并批准。",
        )

    require_versioned_foundation_write(layout.root)
    atomic_write_json(layout.blueprint_path, blueprint)
    revision = hashlib.sha256(layout.blueprint_path.read_bytes()).hexdigest()
    try:
        record_upstream_artifact_revision(
            storage,
            layout,
            artifact_kind=UpstreamArtifactKind.NARRATIVE_BLUEPRINT,
            previous_hash=previous_revision,
            scope=RevisionScope.WHOLE_BOOK,
            reason=reason,
        )
    except Exception as exc:  # noqa: BLE001 - artifact write has already succeeded
        _log.warning("narrative_subplot_staleness_record_failed | error=%s", exc)
    return revision


def _subplot_payloads_for_response(
    subplots: list[dict[str, Any]],
) -> list[_NarrativeSubplotBody]:
    return [_NarrativeSubplotBody.model_validate(subplot) for subplot in subplots]


def _selected_arc_indices(arc_ids: list[str], arc_count: int) -> list[int]:
    indices: list[int] = []
    for arc_id in arc_ids:
        prefix, separator, raw_index = arc_id.rpartition("-")
        if prefix != "arc" or not separator or not raw_index.isdigit():
            raise ValueError(f"无效的角色弧光标识：{arc_id}")
        index = int(raw_index)
        if index < 0 or index >= arc_count:
            raise ValueError(f"角色弧光不存在：{arc_id}")
        if index not in indices:
            indices.append(index)
    return indices


@router.post(
    "/commands/save-narrative-subplots",
    response_model=_NarrativeSubplotMutationResponse,
    response_model_exclude_none=True,
)
async def engine_save_narrative_subplots(
    body: _SaveNarrativeSubplotsBody,
    storage: StorageDep,
) -> _NarrativeSubplotMutationResponse:
    """Durably replace only ``subplot_plan`` with optimistic concurrency."""

    try:
        layout, blueprint, current_revision = _load_blueprint_for_subplot_write(
            storage, body.project_id
        )
    except (FileNotFoundError, ValueError) as exc:
        return _NarrativeSubplotMutationResponse(status="rejected", message=str(exc))
    if body.expected_revision and body.expected_revision != current_revision:
        return _NarrativeSubplotMutationResponse(
            status="conflict",
            message="叙事蓝图已在其他窗口更新；请刷新后再保存。",
            blueprint_revision=current_revision,
        )

    try:
        subplot_plan = normalize_subplot_payloads(
            [subplot.model_dump(mode="json") for subplot in body.subplots],
            total_chapters=blueprint_total_chapters(blueprint),
        )
    except ValueError as exc:
        return _NarrativeSubplotMutationResponse(status="rejected", message=str(exc))

    blueprint["subplot_plan"] = subplot_plan
    try:
        revision = _persist_subplot_plan(
            storage,
            layout,
            blueprint,
            previous_revision=current_revision,
            reason="engine_narrative_subplot_save",
        )
    except (OSError, ValueError) as exc:
        return _NarrativeSubplotMutationResponse(
            status="rejected", message=f"无法保存叙事蓝图：{exc}"
        )
    if isinstance(revision, _NarrativeSubplotMutationResponse):
        return revision
    return _NarrativeSubplotMutationResponse(
        status="saved",
        message=f"已保存 {len(subplot_plan)} 条支线，并标记受影响章节需要重新校准。",
        blueprint_revision=revision,
    )


@router.post(
    "/commands/convert-narrative-arcs-to-subplots",
    response_model=_NarrativeSubplotMutationResponse,
    response_model_exclude_none=True,
)
async def engine_convert_narrative_arcs_to_subplots(
    body: _ConvertNarrativeArcsToSubplotsBody,
    storage: StorageDep,
) -> _NarrativeSubplotMutationResponse:
    """Convert selected event-driven arcs and preserve all source arc records."""

    try:
        layout, blueprint, current_revision = _load_blueprint_for_subplot_write(
            storage, body.project_id
        )
    except (FileNotFoundError, ValueError) as exc:
        return _NarrativeSubplotMutationResponse(status="rejected", message=str(exc))
    if body.expected_revision and body.expected_revision != current_revision:
        return _NarrativeSubplotMutationResponse(
            status="conflict",
            message="叙事蓝图已在其他窗口更新；请刷新后再转换。",
            blueprint_revision=current_revision,
        )

    arcs = [arc for arc in blueprint.get("character_arcs", []) if isinstance(arc, dict)]
    try:
        selected = [arcs[index] for index in _selected_arc_indices(body.arc_ids, len(arcs))]
    except ValueError as exc:
        return _NarrativeSubplotMutationResponse(status="rejected", message=str(exc))
    total_chapters = blueprint_total_chapters(blueprint)
    unsupported = [
        arc.get("character", "未知角色")
        for arc in selected
        if not is_event_driven_arc(arc, total_chapters)
    ]
    if unsupported:
        return _NarrativeSubplotMutationResponse(
            status="rejected",
            message=f"以下弧光不是可转换的事件驱动线：{'、'.join(str(name) for name in unsupported)}。",
        )

    current = [item for item in blueprint.get("subplot_plan", []) if isinstance(item, dict)]
    existing_names = {str(item.get("name") or "") for item in current}
    for arc in selected:
        converted = arc_to_subplot(arc)
        base_name = str(converted["name"])
        candidate_name = base_name
        suffix = 2
        while candidate_name in existing_names:
            candidate_name = f"{base_name}_{suffix}"
            suffix += 1
        converted["name"] = candidate_name
        existing_names.add(candidate_name)
        current.append(converted)
    blueprint["subplot_plan"] = current

    try:
        revision = _persist_subplot_plan(
            storage,
            layout,
            blueprint,
            previous_revision=current_revision,
            reason="engine_narrative_arc_to_subplot",
        )
    except (OSError, ValueError) as exc:
        return _NarrativeSubplotMutationResponse(
            status="rejected", message=f"无法保存叙事蓝图：{exc}"
        )
    if isinstance(revision, _NarrativeSubplotMutationResponse):
        return revision
    return _NarrativeSubplotMutationResponse(
        status="saved",
        message=f"已将 {len(selected)} 条角色弧光转换为支线；原始弧光仍被保留。",
        blueprint_revision=revision,
    )


@router.post(
    "/commands/generate-narrative-subplots",
    response_model=_GenerateNarrativeSubplotsResponse,
)
async def engine_generate_narrative_subplots(
    body: _GenerateNarrativeSubplotsBody,
    storage: StorageDep,
    runtime: RuntimeDep,
) -> _GenerateNarrativeSubplotsResponse:
    """Generate reviewable subplot proposals; the follow-up save remains explicit."""

    try:
        _, blueprint, _ = _load_blueprint_for_subplot_write(storage, body.project_id)
    except (FileNotFoundError, ValueError) as exc:
        return _GenerateNarrativeSubplotsResponse(status="rejected", message=str(exc))
    current = [item for item in blueprint.get("subplot_plan", []) if isinstance(item, dict)]
    try:
        request = runtime.builder.build(
            TaskType.POLISH_SUBPLOT,
            {
                "all_subplots": current,
                "blueprint": blueprint,
                "generation_mode": True,
                "requested_count": body.count,
                "selected_names": [],
                "subplots": [],
                "user_hint": body.user_hint,
            },
            max_tokens=calculate_route_aware_max_tokens(
                runtime.router,
                TaskType.POLISH_SUBPLOT,
                max(3600, body.count * 1400),
                prompt_overhead=3000,
                min_tokens=4096,
            ),
            temperature=0.7,
        )
        response = await runtime.router.route(request)
        payload = safe_parse_json(response.content)
        candidates_raw = payload.get("subplots") if isinstance(payload, dict) else None
        if not isinstance(candidates_raw, list):
            raise ValueError("模型没有返回 subplots 数组")
        candidates = normalize_subplot_payloads(
            candidates_raw[: body.count], total_chapters=blueprint_total_chapters(blueprint)
        )
    except Exception as exc:  # noqa: BLE001 - converted to a safe, actionable UI response
        _log.warning(
            "narrative_subplot_generation_failed | project=%s | error=%s", body.project_id, exc
        )
        return _GenerateNarrativeSubplotsResponse(
            status="rejected", message=f"AI 支线生成失败：{exc}"
        )
    return _GenerateNarrativeSubplotsResponse(
        status="generated",
        message=f"已生成 {len(candidates)} 条支线候选，请审阅后保存。",
        candidates=_subplot_payloads_for_response(candidates),
    )


def _narrative_character_mutation_response(
    *,
    message: str,
    result: Any,
) -> _NarrativeCharacterMutationResponse:
    write_result = result.write_result
    if getattr(result, "proposal_id", ""):
        return _NarrativeCharacterMutationResponse(
            status="candidate",
            proposal_id=result.proposal_id,
            character_revision=result.revision,
            message="人物/关系修改已保存为专项提案；正式设定未改变，请在共创中核对并批准。",
        )
    return _NarrativeCharacterMutationResponse(
        status="saved",
        message=message,
        character_revision=result.revision,
        invalidated_chapters=list(write_result.invalidated_chapters),
        warnings=list(write_result.warnings),
    )


def _canonical_narrative_relationship_type(value: str) -> str:
    """Accept the UI's localized relation label while persisting canonical data."""

    raw = str(value or "").split("·", 1)[0].strip()
    labels = {label: key for key, label in RELATIONSHIP_TYPE_OPTIONS}
    return labels.get(raw, raw)


@router.post(
    "/commands/save-narrative-character",
    response_model=_NarrativeCharacterMutationResponse,
    response_model_exclude_none=True,
)
async def engine_save_narrative_character(
    body: _SaveNarrativeCharacterBody,
    storage: StorageDep,
) -> _NarrativeCharacterMutationResponse:
    """Create or update a character through the shared Engine artifact service."""

    try:
        result = save_narrative_character(
            storage,
            body.project_id,
            character_id=body.character_id,
            profile_patch=body.profile,
            expected_revision=body.expected_revision,
        )
    except CharacterArtifactConflictError as exc:
        return _NarrativeCharacterMutationResponse(status="conflict", message=str(exc))
    except (CharacterArtifactMutationError, OSError, ValueError) as exc:
        return _NarrativeCharacterMutationResponse(status="rejected", message=str(exc))
    operation = "更新" if body.character_id else "新增"
    return _narrative_character_mutation_response(
        message=(f"已{operation}角色；实体图谱、StoryKernel 与受影响章节状态已同步。"),
        result=result,
    )


@router.post(
    "/commands/retire-narrative-character",
    response_model=_NarrativeCharacterMutationResponse,
    response_model_exclude_none=True,
)
async def engine_retire_narrative_character(
    body: _RetireNarrativeCharacterBody,
    storage: StorageDep,
) -> _NarrativeCharacterMutationResponse:
    """Retire a character through the same write boundary used by PySide."""

    try:
        result = retire_narrative_character(
            storage,
            body.project_id,
            character_id=body.character_id,
            expected_revision=body.expected_revision,
        )
    except CharacterArtifactConflictError as exc:
        return _NarrativeCharacterMutationResponse(status="conflict", message=str(exc))
    except (CharacterArtifactMutationError, OSError, ValueError) as exc:
        return _NarrativeCharacterMutationResponse(status="rejected", message=str(exc))
    return _narrative_character_mutation_response(
        message="已标记角色退场；实体图谱、StoryKernel 与受影响章节状态已同步。",
        result=result,
    )


@router.post(
    "/commands/save-narrative-relationship",
    response_model=_NarrativeCharacterMutationResponse,
    response_model_exclude_none=True,
)
async def engine_save_narrative_relationship(
    body: _SaveNarrativeRelationshipBody,
    storage: StorageDep,
) -> _NarrativeCharacterMutationResponse:
    """Persist a typed relation and refresh all derived character artifacts."""

    try:
        result = save_narrative_relationship(
            storage,
            body.project_id,
            source_character_id=body.source_character_id,
            target_character_id=body.target_character_id,
            relation_type=_canonical_narrative_relationship_type(body.relation_type),
            description=body.description,
            expected_revision=body.expected_revision,
        )
    except CharacterArtifactConflictError as exc:
        return _NarrativeCharacterMutationResponse(status="conflict", message=str(exc))
    except (CharacterArtifactMutationError, OSError, ValueError) as exc:
        return _NarrativeCharacterMutationResponse(status="rejected", message=str(exc))
    return _narrative_character_mutation_response(
        message="已保存角色关系；实体图谱、StoryKernel 与受影响章节状态已同步。",
        result=result,
    )


@router.post(
    "/commands/remove-narrative-relationship",
    response_model=_NarrativeCharacterMutationResponse,
    response_model_exclude_none=True,
)
async def engine_remove_narrative_relationship(
    body: _RemoveNarrativeRelationshipBody,
    storage: StorageDep,
) -> _NarrativeCharacterMutationResponse:
    """Remove a typed relation through the shared Engine artifact service."""

    try:
        result = remove_narrative_relationship(
            storage,
            body.project_id,
            source_character_id=body.source_character_id,
            target_character_id=body.target_character_id,
            expected_revision=body.expected_revision,
        )
    except CharacterArtifactConflictError as exc:
        return _NarrativeCharacterMutationResponse(status="conflict", message=str(exc))
    except (CharacterArtifactMutationError, OSError, ValueError) as exc:
        return _NarrativeCharacterMutationResponse(status="rejected", message=str(exc))
    return _narrative_character_mutation_response(
        message="已移除角色关系；实体图谱、StoryKernel 与受影响章节状态已同步。",
        result=result,
    )


def _humanize_library_mutation_response(
    *,
    message: str,
    result: Any,
) -> _HumanizeLibraryMutationResponse:
    return _HumanizeLibraryMutationResponse(
        status="saved",
        message=message,
        humanize_library_revision=result.revision,
        pattern=(
            _HumanizePatternViewResponse.model_validate(humanize_pattern_view(result.entry))
            if result.entry is not None
            else None
        ),
    )


@router.post(
    "/commands/save-humanize-pattern",
    response_model=_HumanizeLibraryMutationResponse,
    response_model_exclude_none=True,
)
async def engine_save_humanize_pattern(
    body: _SaveHumanizePatternBody,
) -> _HumanizeLibraryMutationResponse:
    """Create or update a global HumanizeLibrary entry through the Engine."""

    try:
        result = save_humanize_pattern(
            HumanizePatternInput(
                pattern_id=body.pattern.pattern_id,
                name=body.pattern.name,
                category=body.pattern.category,
                severity=body.pattern.severity,
                keywords=tuple(body.pattern.keywords),
                notes=body.pattern.notes,
                example_phrase=body.pattern.example_phrase,
                source=body.pattern.source,
            ),
            pattern_id=body.pattern_id,
            expected_revision=body.expected_revision,
        )
    except HumanizeLibraryConflictError as exc:
        return _HumanizeLibraryMutationResponse(status="conflict", message=str(exc))
    except HumanizeLibraryMutationError as exc:
        return _HumanizeLibraryMutationResponse(status="rejected", message=str(exc))
    return _humanize_library_mutation_response(
        message="已保存拟人化模式；其他客户端刷新后将读取同一份库。",
        result=result,
    )


@router.post(
    "/commands/set-humanize-pattern-enabled",
    response_model=_HumanizeLibraryMutationResponse,
    response_model_exclude_none=True,
)
async def engine_set_humanize_pattern_enabled(
    body: _SetHumanizePatternEnabledBody,
) -> _HumanizeLibraryMutationResponse:
    """Toggle a library pattern without exposing the SQLite store to clients."""

    try:
        result = set_humanize_pattern_enabled(
            body.pattern_id,
            body.enabled,
            expected_revision=body.expected_revision,
        )
    except HumanizeLibraryConflictError as exc:
        return _HumanizeLibraryMutationResponse(status="conflict", message=str(exc))
    except HumanizeLibraryMutationError as exc:
        return _HumanizeLibraryMutationResponse(status="rejected", message=str(exc))
    state = "启用" if body.enabled else "停用"
    return _humanize_library_mutation_response(
        message=f"已{state}拟人化模式；刷新后可在所有客户端看到变更。",
        result=result,
    )


@router.post(
    "/commands/remove-humanize-pattern",
    response_model=_HumanizeLibraryMutationResponse,
    response_model_exclude_none=True,
)
async def engine_remove_humanize_pattern(
    body: _RemoveHumanizePatternBody,
) -> _HumanizeLibraryMutationResponse:
    """Delete a non-builtin pattern under the Engine's revision guard."""

    try:
        result = remove_humanize_pattern(
            body.pattern_id,
            expected_revision=body.expected_revision,
        )
    except HumanizeLibraryConflictError as exc:
        return _HumanizeLibraryMutationResponse(status="conflict", message=str(exc))
    except HumanizeLibraryMutationError as exc:
        return _HumanizeLibraryMutationResponse(status="rejected", message=str(exc))
    return _humanize_library_mutation_response(
        message="已删除拟人化模式；刷新后会载入当前库。",
        result=result,
    )


@router.post(
    "/commands/merge-humanize-patterns",
    response_model=_HumanizeLibraryMutationResponse,
    response_model_exclude_none=True,
)
async def engine_merge_humanize_patterns(
    body: _MergeHumanizePatternsBody,
) -> _HumanizeLibraryMutationResponse:
    """Merge a user/imported pattern into a target entry atomically."""

    try:
        result = merge_humanize_patterns(
            body.source_pattern_id,
            body.target_pattern_id,
            expected_revision=body.expected_revision,
        )
    except HumanizeLibraryConflictError as exc:
        return _HumanizeLibraryMutationResponse(status="conflict", message=str(exc))
    except HumanizeLibraryMutationError as exc:
        return _HumanizeLibraryMutationResponse(status="rejected", message=str(exc))
    return _humanize_library_mutation_response(
        message="已合并拟人化模式并保留目标条目的命中历史。",
        result=result,
    )


@router.post("/commands/sync-chapter-contracts", response_model=EngineCommandResponse)
async def engine_sync_chapter_contracts(
    body: _SyncChapterContractsBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    """Submit the durable outline-to-contract synchronization path."""

    from novel_forge.app_service.contracts import JobCommand

    record = service.submit(
        JobCommand(
            kind="sync_chapter_contracts",
            project_id=body.project_id,
            payload=body.model_dump(mode="json", exclude={"kind"}),
        )
    )
    return EngineCommandResponse(
        status="accepted",
        message="章节契约同步任务已提交；正文不会被修改。",
        task_id=record.job_id,
    )


@router.post("/commands/extend-outline", response_model=EngineCommandResponse)
async def engine_extend_outline(
    body: _ExtendOutlineBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    """Submit the durable outline-extension path used by the reader."""

    from novel_forge.app_service.contracts import JobCommand

    if (body.additional_chapters is None) == (body.target_total is None):
        raise HTTPException(
            status_code=422,
            detail="Provide exactly one of additional_chapters or target_total",
        )
    record = service.submit(
        JobCommand(
            kind="extend_outline",
            project_id=body.project_id,
            payload=body.model_dump(mode="json", exclude={"kind"}),
        )
    )
    return EngineCommandResponse(
        status="accepted",
        message="延长全书任务已提交；完成后将同步新增章节契约。",
        task_id=record.job_id,
    )


@router.post("/commands/audit-book", response_model=EngineCommandResponse)
async def engine_audit_book(
    body: _AuditBookBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    from novel_forge.app_service.contracts import JobCommand

    record = service.submit(
        JobCommand(
            kind="book_consistency",
            project_id=body.project_id,
            payload=body.model_dump(mode="json", exclude={"kind"}),
        )
    )
    return EngineCommandResponse(
        status="accepted",
        message="全书一致性审计已提交。",
        task_id=record.job_id,
    )


@router.post("/commands/execute-global-repair-queue", response_model=EngineCommandResponse)
async def engine_execute_global_repair_queue(
    body: _ExecuteGlobalRepairQueueBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    """Submit the guarded repair queue produced by a completed whole-book audit."""

    from novel_forge.app_service.contracts import JobCommand

    record = service.submit(
        JobCommand(
            kind="global_repair_queue",
            project_id=body.project_id,
            payload=body.model_dump(mode="json", exclude={"kind"}),
        )
    )
    return EngineCommandResponse(
        status="accepted",
        message="全书审计修复队列已提交；执行前会复验正文版本并在失败时回滚。",
        task_id=record.job_id,
    )


@router.post("/commands/audit-book-editorial", response_model=EngineCommandResponse)
async def engine_audit_book_editorial(
    body: _AuditBookEditorialBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    """Submit a durable, publication-level whole-book editorial audit."""

    from novel_forge.app_service.contracts import JobCommand

    record = service.submit(
        JobCommand(
            kind="book_editorial_audit",
            project_id=body.project_id,
            payload=body.model_dump(mode="json", exclude={"kind"}),
        )
    )
    return EngineCommandResponse(
        status="accepted",
        message="全书出版编辑审查已提交；完成后将生成可追溯的修订队列。",
        task_id=record.job_id,
    )


@router.post("/commands/export-book", response_model=EngineCommandResponse)
async def engine_export_book(
    body: _ExportBookBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    from novel_forge.app_service.contracts import JobCommand

    record = service.submit(
        JobCommand(
            kind="export_book",
            project_id=body.project_id,
            payload=body.model_dump(mode="json", exclude={"kind"}),
        )
    )
    return EngineCommandResponse(
        status="accepted",
        message="书稿导出任务已提交。",
        task_id=record.job_id,
    )


@router.post("/commands/clean-chapters", response_model=EngineCommandResponse)
async def engine_clean_chapters(
    body: _CleanChaptersBody,
    service: JobServiceDep,
    runtime: RuntimeDep,
    storage: StorageDep,
) -> EngineCommandResponse:
    records = service.list(project_id=body.project_id)
    execution_activity = getattr(service, "authoring_activity", None)
    activity_state, activity_job_id = (
        execution_activity(body.project_id)
        if callable(execution_activity)
        else ("idle", "")
    )
    blocking_records = [
        record
        for record in records
        if str(getattr(record.status, "value", record.status)) in {"queued", "running"}
    ]
    if activity_state in {"running", "stopping"} or blocking_records:
        blocking_record = next(
            (record for record in records if record.job_id == activity_job_id),
            blocking_records[0] if blocking_records else None,
        )
        task_label = f"「{blocking_record.label}」" if blocking_record is not None else "后台任务"
        state_label = "正在安全停止" if activity_state == "stopping" else "仍在运行或排队"
        return EngineCommandResponse(
            status="rejected",
            message=f"项目{task_label}{state_label}；请等待完全停止后再清理章节。",
        )

    autorun_getter = getattr(service, "book_autorun_state", None)
    autorun = autorun_getter(body.project_id) if callable(autorun_getter) else None
    autorun_status = str(
        getattr(getattr(autorun, "status", ""), "value", getattr(autorun, "status", ""))
    )
    if autorun_status in {"waiting_init", "running", "retry_wait"}:
        return EngineCommandResponse(
            status="rejected",
            message="章节连跑仍在运行或等待重试；请先停止连跑，等待工作线程退出后再清理。",
        )

    layout = ProjectLayout(storage.existing_project_dir(body.project_id))
    invalidated: list[int] = []
    cleanup_failure: ChapterCleanupConvergenceError | None = None
    try:
        invalidated = await regenerate_from_chapter_async(
            storage,
            layout,
            from_chapter=body.from_chapter,
        )
    except AuthoringDeniedError as exc:
        return EngineCommandResponse(status="rejected", message=str(exc))
    except ChapterCleanupConvergenceError as exc:
        cleanup_failure = exc
        invalidated = list(exc.invalidated_chapters)
        _log.exception(
            "Chapter cleanup did not converge | project=%s cutoff=%s stage=%s",
            body.project_id,
            body.from_chapter,
            exc.failed_stage,
        )
    cleanable_chapter_kinds = {
        "run_chapter",
        "prepare_chapter",
        "resolve_chapter_checkpoint",
        "resolve_chapter_checkpoint_finalize",
        "polish_chapter",
        "repair_continuity",
        "repair_causal",
        "repair_issues",
        "reevaluate_chapter",
    }
    inactive_job_ids = {
        record.job_id
        for record in records
        if str(getattr(record.status, "value", record.status)) in {"paused", "failed", "succeeded"}
        and str(getattr(record.kind, "value", record.kind)) in cleanable_chapter_kinds
        and (
            (chapter_number := _record_chapter_number(record)) is not None
            and chapter_number >= body.from_chapter
        )
    }
    followup_warnings: list[tuple[str, str]] = []
    memory_context = runtime.memory_contexts.pop(body.project_id, None)
    if memory_context is not None:
        try:
            memory_context.invalidate_chapter_memory(body.from_chapter)
            memory_context.save_to_disk()
        except Exception:
            _log.exception(
                "Chapter cleanup committed but memory cache refresh failed | project=%s cutoff=%s",
                body.project_id,
                body.from_chapter,
            )
            followup_warnings.append(("memory_cache", "记忆缓存刷新失败"))
    cleared_ids: list[str] = []
    if inactive_job_ids:
        try:
            cleared_ids = service.clear_inactive_history(inactive_job_ids)
        except Exception:
            _log.exception(
                "Chapter cleanup committed but task history reset failed | project=%s cutoff=%s",
                body.project_id,
                body.from_chapter,
            )
            followup_warnings.append(("task_history", "任务流记录重置失败"))
    reset_autorun = getattr(service, "reset_book_autorun_after_cleanup", None)
    autorun_reset = False
    if callable(reset_autorun):
        try:
            autorun_reset = bool(
                reset_autorun(body.project_id, from_chapter=body.from_chapter)
            )
        except Exception:
            _log.exception(
                "Chapter cleanup committed but autorun reset failed | project=%s cutoff=%s",
                body.project_id,
                body.from_chapter,
            )
            followup_warnings.append(("autorun", "续跑指针重置失败"))

    if invalidated:
        chapter_list = "、".join(str(number) for number in sorted(invalidated))
        message = f"已从第 {body.from_chapter} 章起清理失效产物：{chapter_list}。"
    elif cleared_ids or autorun_reset:
        message = (
            f"第 {body.from_chapter} 章起已无可删除文件；"
            "已清除旧任务流与续跑指针，可从该章重新开始。"
        )
    else:
        message = (
            f"第 {body.from_chapter} 章起未发现可清理产物；"
            "Canon、任务流与续跑状态已处于干净起点。"
        )
    failed_stages = [stage for stage, _message in followup_warnings]
    failure_messages = [warning for _stage, warning in followup_warnings]
    if cleanup_failure is not None:
        failed_stages.insert(0, cleanup_failure.failed_stage)
        failure_messages.insert(0, str(cleanup_failure))

    response_data = {
        "cleanupMayHaveChangedData": True,
        "cleanupComplete": not failed_stages,
        "refreshRequired": True,
        "invalidatedChapters": sorted(invalidated),
        "failedStages": failed_stages,
    }
    if failed_stages:
        return EngineCommandResponse(
            status="rejected",
            message=(
                f"⚠️ 章节清理未完成：{message} "
                f"{'；'.join(failure_messages)}。"
                "系统未确认安全重跑条件；页面将刷新实际状态，"
                "请再次执行清理以收敛，暂勿启动连跑。"
            ),
            data=response_data,
        )
    return EngineCommandResponse(
        status="accepted",
        message=f"清理完成并已核验可安全重跑。{message}",
        data=response_data,
    )


def _record_chapter_number(record: JobRecord) -> int | None:
    """Read the chapter owned by a durable task without trusting one field only."""

    for payload in (record.current_step_payload, record.result):
        for key in ("chapter_number", "chapter"):
            value = payload.get(key)
            if value is None:
                continue
            try:
                chapter_number = int(value)
            except (TypeError, ValueError):
                continue
            if chapter_number > 0:
                return chapter_number
    label_match = re.search(r"第\s*(\d+)\s*章", record.label)
    return int(label_match.group(1)) if label_match else None


@router.post("/commands/cancel-chapter", response_model=EngineCommandResponse)
async def engine_cancel_chapter(
    body: _CancelChapterBody, service: JobServiceDep
) -> EngineCommandResponse:
    autorun_getter = getattr(service, "book_autorun_state", None)
    autorun = autorun_getter(body.project_id) if callable(autorun_getter) else None
    if (
        autorun is not None
        and autorun.current_chapter == body.chapter_number
        and autorun.status.value
        in {
            "waiting_init",
            "running",
            "retry_wait",
            "paused",
        }
    ):
        pause_autorun = getattr(service, "pause_book_autorun", None)
        active_job_id = (
            pause_autorun(body.project_id, reason="用户已暂停章节连跑")
            if callable(pause_autorun)
            else ""
        )
        if active_job_id:
            try:
                service.cancel(active_job_id, reason="用户已暂停章节连跑")
            except KeyError:
                pass
        return EngineCommandResponse(
            status="accepted",
            task_id=active_job_id or None,
            message=f"章节 {body.chapter_number} 连跑已暂停，可从持久化状态恢复",
        )
    records = service.list(project_id=body.project_id)
    for r in records:
        kind_val = r.kind.value if hasattr(r.kind, "value") else str(r.kind)
        if kind_val in {
            "prepare_chapter",
            "run_chapter",
            "resolve_chapter_checkpoint",
            "polish_chapter",
        } and r.status.value in {"running", "queued", "paused"}:
            # Match by chapter_number to avoid cancelling a different chapter's job.
            record_chapter = _record_chapter_number(r)
            if record_chapter is None:
                continue
            if record_chapter is not None and int(record_chapter) != body.chapter_number:
                continue
            service.cancel(r.job_id, reason="用户已取消")
            return EngineCommandResponse(
                status="accepted",
                task_id=r.job_id,
                message=f"章节 {body.chapter_number} 任务已取消",
            )
    return EngineCommandResponse(
        status="rejected",
        message="未找到运行中的章节任务",
    )


@router.post("/jobs/{job_id}/cancel", response_model=EngineCommandResponse)
async def engine_cancel_job(
    job_id: str,
    service: JobServiceDep,
    body: _CancelJobBody | None = None,
) -> EngineCommandResponse:
    """Cancel any job by ID. Usable by chapter studio, workflow, and voice studio."""
    reason = body.reason.strip() if body is not None else "用户已取消"
    reason = reason or "用户已取消"
    record = service.get(job_id)
    if record is None:
        return EngineCommandResponse(status="rejected", message="任务不存在")
    if record.status.value not in {"running", "queued", "paused"}:
        return EngineCommandResponse(
            status="rejected",
            message=f"任务已结束（{record.status.value}），无法取消",
        )
    autorun_getter = getattr(service, "book_autorun_state", None)
    autorun = autorun_getter(record.project_id) if callable(autorun_getter) else None
    if autorun is not None and autorun.active_job_id == job_id:
        pause_autorun = getattr(service, "pause_book_autorun", None)
        if callable(pause_autorun):
            pause_autorun(record.project_id, reason="用户已暂停章节连跑")
    service.cancel(job_id, reason=reason)
    return EngineCommandResponse(status="accepted", message="任务已取消")


@router.post("/commands/resume-job", response_model=EngineCommandResponse)
async def engine_resume_job(
    body: _ResumeJobBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    try:
        record = service.resume(body.task_id)
    except KeyError:
        return EngineCommandResponse(
            status="rejected",
            message="该任务没有可恢复的持久化执行意图。",
        )
    return EngineCommandResponse(
        status="accepted",
        task_id=record.job_id,
        message="引擎已确认恢复任务。",
    )


@router.post("/commands/retry-init-repair", response_model=EngineCommandResponse)
async def engine_retry_init_repair(
    body: _RetryInitRepairBody,
    service: JobServiceDep,
    storage: StorageDep,
) -> EngineCommandResponse:
    """Re-run init repair from durable original parameters and artifacts."""

    from novel_forge.app_service.contracts import JobCommand, JobKind

    project_id = body.project_id.strip()
    layout = ProjectLayout(storage.project_path(project_id))
    if not layout.init_request_meta_path.is_file():
        return EngineCommandResponse(
            status="rejected",
            message="缺少可验证的原始立项参数，不能安全执行 AI 修复。",
        )
    if any(
        record.status.value in {"running", "queued"}
        for record in service.list(project_id=project_id)
    ):
        return EngineCommandResponse(
            status="already_running",
            message="该项目已有任务正在执行。",
        )
    record = service.submit(
        JobCommand(
            kind=JobKind.INIT_REPAIR_RETRY,
            project_id=project_id,
            payload={
                "project_id": project_id,
                "reset_repair_history": body.reset_repair_history,
            },
            label=f"长篇立项修复 · {project_id}",
            metadata={"workflow_type": "long_init_repair"},
        )
    )
    return EngineCommandResponse(
        status="accepted",
        task_id=record.job_id,
        message="已复用验证通过的立项产物，开始 AI 修复与一致性复审。",
    )


@router.post(
    "/commands/save-init-manual-repair",
    response_model=InitManualRepairSaveResult,
    response_model_exclude_none=True,
)
async def engine_save_init_manual_repair(
    body: _SaveInitManualRepairBody,
    storage: StorageDep,
) -> InitManualRepairSaveResult:
    """Persist one validated manual repair before the durable retry command."""

    try:
        snapshot = save_manual_init_repair(
            storage,
            body.project_id,
            artifact=body.artifact,
            payload=body.payload,
            expected_revision=body.expected_revision,
        )
    except ManualInitRepairConflictError as exc:
        latest = load_manual_init_repair(storage, body.project_id, artifact=body.artifact)
        return InitManualRepairSaveResult(
            status="conflict",
            message=str(exc),
            repair=_init_manual_repair_view(latest),
        )
    except ManualInitRepairError as exc:
        return InitManualRepairSaveResult(status="rejected", message=str(exc))
    except (OSError, ValueError) as exc:
        return InitManualRepairSaveResult(status="rejected", message=f"无法保存修复：{exc}")
    return InitManualRepairSaveResult(
        status="saved",
        message="人工修复已验证并持久化；现在可以继续初始化复审。",
        repair=_init_manual_repair_view(snapshot),
    )


@router.post("/commands/rebuild-memory-vectors", response_model=EngineCommandResponse)
async def engine_rebuild_memory_vectors(
    body: _RebuildMemoryVectorsBody,
    service: JobServiceDep,
    storage: StorageDep,
) -> EngineCommandResponse:
    """Submit vector rebuilding through the shared durable job service."""

    from novel_forge.app_service.contracts import JobCommand, JobKind

    project_id = body.project_id.strip()
    layout = ProjectLayout(storage.project_path(project_id))
    if not layout.root.is_dir():
        return EngineCommandResponse(status="rejected", message="项目不存在，无法重建向量索引。")
    if body.from_chapter is not None and body.to_chapter is not None:
        if body.from_chapter > body.to_chapter:
            return EngineCommandResponse(status="rejected", message="起始章节不能晚于结束章节。")
    if any(
        str(getattr(record.kind, "value", record.kind)) == JobKind.REBUILD_MEMORY_VECTORS.value
        and record.status.value in {"running", "queued", "paused"}
        for record in service.list(project_id=project_id)
    ):
        return EngineCommandResponse(
            status="already_running", message="该项目已有向量重建任务正在执行。"
        )
    request = RebuildMemoryVectorsRequest(
        project_id=project_id,
        include_expression=body.include_expression,
        from_chapter=body.from_chapter,
        to_chapter=body.to_chapter,
    )
    record = service.submit(
        JobCommand(
            kind=JobKind.REBUILD_MEMORY_VECTORS,
            project_id=project_id,
            payload=request.model_dump(mode="json"),
            label=f"重建向量索引 · {project_id}",
            metadata={"workflow_type": "memory_maintenance"},
        )
    )
    return EngineCommandResponse(
        status="accepted",
        task_id=record.job_id,
        message="向量索引重建任务已提交；可在任务流中观察、取消或恢复。",
    )


@router.post(
    "/commands/clear-job-history",
    response_model=_ClearJobHistoryResponse,
)
async def engine_clear_job_history(
    body: _ClearJobHistoryBody,
    service: JobServiceDep,
) -> _ClearJobHistoryResponse:
    cleared = service.clear_inactive_history(None if body.task_ids is None else set(body.task_ids))
    return _ClearJobHistoryResponse(
        status="cleared",
        message=f"已清理 {len(cleared)} 条终态任务记录；完整运行日志仍保留。",
        cleared_task_ids=cleared,
    )


@router.post(
    "/commands/acknowledge-task-errors",
    response_model=_TaskErrorResolutionResponse,
)
async def engine_acknowledge_task_errors(
    body: _AcknowledgeTaskErrorsBody,
    service: JobServiceDep,
) -> _TaskErrorResolutionResponse:
    """Record author review without claiming an upstream retry was repaired."""

    updated = service.acknowledge_error_log_entries(set(body.error_entry_ids))
    message = (
        f"已确认 {len(updated)} 条错误已完成核查；这不会自动修复模型、配置或重新运行任务。"
        if updated
        else "未确认任何条目；所选诊断可能已被清理、自动恢复，或来自过期页面。请刷新后重试。"
    )
    return _TaskErrorResolutionResponse(
        status="acknowledged",
        message=message,
        updated_error_entry_ids=updated,
    )


@router.post(
    "/commands/reopen-task-errors",
    response_model=_TaskErrorResolutionResponse,
)
async def engine_reopen_task_errors(
    body: _ReopenTaskErrorsBody,
    service: JobServiceDep,
) -> _TaskErrorResolutionResponse:
    """Return explicitly acknowledged diagnostics to the pending queue."""

    updated = service.reopen_error_log_entries(set(body.error_entry_ids))
    return _TaskErrorResolutionResponse(
        status="reopened",
        message=f"已重新打开 {len(updated)} 条错误，后续仍会保留完整运行日志供核查。",
        updated_error_entry_ids=updated,
    )


@router.post(
    "/commands/clear-closed-task-errors",
    response_model=_ClearClosedTaskErrorsResponse,
)
async def engine_clear_closed_task_errors(
    body: _ClearClosedTaskErrorsBody,
    service: JobServiceDep,
) -> _ClearClosedTaskErrorsResponse:
    """Prune only Engine-verified closed error indexes and terminal task cards."""

    result = service.clear_closed_error_log_entries(set(body.error_entry_ids))
    protected_suffix = (
        f"；{len(result.retained_pending_error_entry_ids)} 条仍待确认，未作改动"
        if result.retained_pending_error_entry_ids
        else ""
    )
    return _ClearClosedTaskErrorsResponse(
        message=(
            f"已清理 {len(result.cleared_error_entry_ids)} 条已闭环诊断索引、"
            f"{len(result.cleared_task_ids)} 条关联终态任务卡{protected_suffix}；"
            "完整运行日志仍保留。"
        ),
        cleared_task_ids=result.cleared_task_ids,
        cleared_error_entry_ids=result.cleared_error_entry_ids,
        retained_pending_error_entry_ids=result.retained_pending_error_entry_ids,
    )


@router.post(
    "/commands/clear-error-archive",
    response_model=_ClearErrorArchiveResponse,
)
async def engine_clear_error_archive(
    body: _ClearErrorArchiveBody,
    storage: StorageDep,
) -> _ClearErrorArchiveResponse:
    """Remove Engine-owned compact diagnostics without touching full run logs."""

    project_id = body.project_id.strip() if body.project_id else ""
    removed = TaskFlowErrorLog(storage.root).clear(project_id=project_id)
    scope = f"项目「{project_id}」" if project_id else "全部项目"
    return _ClearErrorArchiveResponse(
        message=f"已清空{scope}的 {removed} 份任务错误档案；完整运行日志仍保留。",
        removed_project_count=removed,
    )


@router.post(
    "/commands/restart-long-init",
    response_model=_RestartLongInitResponse,
)
async def engine_restart_long_init(
    body: _RestartLongInitBody,
    service: JobServiceDep,
    storage: StorageDep,
) -> _RestartLongInitResponse:
    """Reset one idle long project to a clean initialization boundary.

    A fresh init must never delete artifacts while a worker may still write to
    them.  Completed chapter Markdown deliberately survives the reset; every
    other project artifact is recreated from the next init workflow.
    """

    project_id = body.project_id.strip()
    if not project_id:
        return _RestartLongInitResponse(
            status="rejected",
            message="项目 ID 不能为空，无法重新立项。",
        )
    active_states = {"running", "queued"}

    def has_active_work() -> bool:
        return any(
            record.status.value in active_states for record in service.list(project_id=project_id)
        )

    if has_active_work():
        return _RestartLongInitResponse(
            status="rejected",
            message="该项目仍有正在执行或排队的任务，暂不能重新立项。",
        )

    try:
        project_dir = storage.existing_project_dir(project_id)
    except FileNotFoundError:
        return _RestartLongInitResponse(
            status="rejected",
            message="项目目录不存在，无法重新立项。",
        )

    try:
        with storage.project_lock(project_id):
            # Recheck after the process-level project lock is acquired so a
            # just-started local worker is still protected.
            if has_active_work():
                return _RestartLongInitResponse(
                    status="rejected",
                    message="该项目刚开始执行任务，暂不能重新立项。",
                )
            removed = reset_project_for_reinit(
                ProjectLayout(project_dir),
                preserve_chapters=True,
                preserve_logs=False,
            )
    except OSError:
        return _RestartLongInitResponse(
            status="rejected",
            message="清理旧立项产物失败；项目内容未完成重置。请检查本地存储后重试。",
        )

    inactive_task_ids = {
        record.job_id
        for record in service.list(project_id=project_id)
        if record.status.value in {"succeeded", "failed", "paused"}
    }
    cleared = service.clear_inactive_history(inactive_task_ids)
    return _RestartLongInitResponse(
        status="reset",
        message=(
            f"已清理 {len(removed)} 项旧立项产物与 {len(cleared)} 条任务记录；"
            "现在可创建新的长篇项目。"
        ),
        cleared_task_ids=cleared,
        removed_artifact_count=len(removed),
    )


async def _reevaluate_saved_chapter(
    runtime: RuntimeServices,
    *,
    project_id: str,
    chapter_number: int,
) -> None:
    try:
        await execute_reevaluate_chapter(
            runtime,
            ReevaluateChapterRequest(
                project_id=project_id,
                chapter_number=chapter_number,
            ),
        )
    except Exception:
        # The durable manual-revision status remains the recovery source when
        # background reevaluation fails. The next reader refresh still exposes
        # the chapter as requiring reevaluation.
        return


@router.post(
    "/commands/generate-chapter-revision-candidate",
    response_model=_GenerateChapterRevisionCandidateResponse,
    response_model_exclude_none=True,
)
async def engine_generate_chapter_revision_candidate(
    body: _GenerateChapterRevisionCandidateBody,
    runtime: RuntimeDep,
    storage: StorageDep,
) -> _GenerateChapterRevisionCandidateResponse:
    """Generate one unsaved selection rewrite through the shared Engine service."""

    layout = ProjectLayout(storage.existing_project_dir(body.project_id))
    if not layout.chapter_path(body.chapter_number).exists():
        return _GenerateChapterRevisionCandidateResponse(
            status="rejected",
            message=f"第 {body.chapter_number} 章尚无终稿，无法生成精修候选。",
        )
    try:
        replacement = await generate_selection_revision_candidate(
            runtime.router,
            project_dir=layout.root,
            request=SelectionRevisionInput(
                project_id=body.project_id,
                chapter_number=body.chapter_number,
                chapter_title=body.chapter_title,
                selected_text=body.selected_text,
                before_context=body.before_context,
                after_context=body.after_context,
                instruction=body.instruction,
            ),
            temperature=float(getattr(runtime.settings, "temp_polish_chapter", 0.35)),
        )
    except SelectionRevisionError as exc:
        return _GenerateChapterRevisionCandidateResponse(status="rejected", message=str(exc))
    return _GenerateChapterRevisionCandidateResponse(
        status="generated",
        message="Engine 已生成选段精修候选；请审阅后再纳入正文。",
        replacement=replacement,
    )


@router.post(
    "/commands/save-chapter-revision",
    response_model=_SaveChapterRevisionResponse,
    response_model_exclude_none=True,
)
async def engine_save_chapter_revision(
    body: _SaveChapterRevisionBody,
    background_tasks: BackgroundTasks,
    runtime: RuntimeDep,
    storage: StorageDep,
) -> _SaveChapterRevisionResponse:
    """Apply a final-text revision through the same workspace service as PySide."""
    layout = ProjectLayout(storage.existing_project_dir(body.project_id))
    chapter_path = layout.chapter_path(body.chapter_number)
    if not chapter_path.exists():
        return _SaveChapterRevisionResponse(
            status="rejected",
            message=f"第 {body.chapter_number} 章尚无终稿，无法保存。",
        )
    current_revision = _file_revision(chapter_path)
    if body.expected_revision is not None and body.expected_revision != current_revision:
        return _SaveChapterRevisionResponse(
            status="conflict",
            message="终稿已被其他窗口更新，请审阅最新版本后重试。",
            revision=current_revision,
            latest_text=chapter_path.read_text(encoding="utf-8"),
        )
    try:
        execution = await execute_manual_revision(
            runtime,
            ManualRevisionRequest(
                project_id=body.project_id,
                chapter_number=body.chapter_number,
                text=body.text,
                scope=body.scope,
                background_reevaluate=body.background_reevaluate,
                reason="react_manual_revision",
            ),
        )
    except (OSError, ValueError, TTSDeliveryNotReadyError) as exc:
        return _SaveChapterRevisionResponse(status="rejected", message=str(exc))

    status = str(execution.result.get("status") or "applied")
    if body.background_reevaluate and status == "applied":
        background_tasks.add_task(
            _reevaluate_saved_chapter,
            runtime,
            project_id=body.project_id,
            chapter_number=body.chapter_number,
        )
    return _SaveChapterRevisionResponse(
        status="noop" if status == "noop" else "applied",
        message=(
            "终稿内容未变化。"
            if status == "noop"
            else "终稿已写入项目，并已记录版本历史与下游陈旧状态。"
        ),
        revision=_file_revision(chapter_path),
        background_reevaluate_scheduled=body.background_reevaluate and status == "applied",
    )


def _token_preferences_view(
    project_dir: Path,
    prefs: dict[str, Any],
) -> _TokenDashboardPreferencesView:
    path = project_dir / "states" / "token_dashboard_preferences.json"
    currency = str(prefs.get("currency") or "USD").upper()
    if currency not in {"USD", "CNY", "EUR"}:
        currency = "USD"
    rates = prefs.get("exchange_rates")
    exchange_rates = (
        {str(key): float(value) for key, value in rates.items()}
        if isinstance(rates, dict)
        else {"USD": 1.0}
    )
    step_filter = str(prefs.get("step_waterfall_filter") or "all")
    if step_filter not in {"all", "init", "chapter", "repair"}:
        step_filter = "all"
    price_per_million = float(prefs.get("price_per_million") or 0.0)
    price_unit = str(prefs.get("price_unit") or "million")
    if price_unit not in {"million", "thousand"}:
        price_unit = "million"
    raw_model_prices = prefs.get("model_price_per_million")
    model_prices = (
        {str(key): max(float(value), 0.0) for key, value in raw_model_prices.items()}
        if isinstance(raw_model_prices, dict)
        else {}
    )
    raw_model_units = prefs.get("model_price_unit")
    model_units = (
        {
            str(key): str(value)
            for key, value in raw_model_units.items()
            if str(value) in {"million", "thousand"}
        }
        if isinstance(raw_model_units, dict)
        else {}
    )
    return _TokenDashboardPreferencesView(
        currency=currency,
        exchange_rates=exchange_rates,
        step_waterfall_filter=step_filter,
        price_per_million=price_per_million,
        price_unit=price_unit,
        model_price_per_million=model_prices,
        model_price_unit=model_units,
        revision=_file_revision(path),
    )


@router.get(
    "/projects/{project_id}/token-dashboard-preferences",
    response_model=_TokenDashboardPreferencesView,
)
async def get_token_dashboard_preferences(
    project_id: str,
    storage: StorageDep,
) -> _TokenDashboardPreferencesView:
    project_dir = storage.existing_project_dir(project_id)
    return _token_preferences_view(
        project_dir,
        load_token_dashboard_prefs(project_dir),
    )


@router.post(
    "/commands/save-token-dashboard-preferences",
    response_model=_SaveTokenDashboardPreferencesResponse,
    response_model_exclude_none=True,
)
async def engine_save_token_dashboard_preferences(
    body: _TokenDashboardPreferencesBody,
    storage: StorageDep,
) -> _SaveTokenDashboardPreferencesResponse:
    project_dir = storage.existing_project_dir(body.project_id)
    path = project_dir / "states" / "token_dashboard_preferences.json"
    current_revision = _file_revision(path)
    if body.expected_revision is not None and body.expected_revision != current_revision:
        current = _token_preferences_view(
            project_dir,
            load_token_dashboard_prefs(project_dir),
        )
        return _SaveTokenDashboardPreferencesResponse(
            status="conflict",
            message="Token 追踪设置已被其他窗口更新。",
            revision=current.revision,
            preferences=current,
        )

    prefs = load_token_dashboard_prefs(project_dir)
    prefs["currency"] = body.currency
    rates = dict(prefs.get("exchange_rates") or {})
    for key, value in body.exchange_rates.items():
        if value > 0:
            rates[key.upper()] = value
    rates["CNY"] = 1.0
    prefs["exchange_rates"] = rates
    prefs["step_waterfall_filter"] = body.step_waterfall_filter
    if body.price_per_million is not None:
        prefs["price_per_million"] = body.price_per_million
    if body.price_unit is not None:
        prefs["price_unit"] = body.price_unit
    if body.model_price_per_million is not None:
        prefs["model_price_per_million"] = {
            key: value for key, value in body.model_price_per_million.items() if value >= 0
        }
    if body.model_price_unit is not None:
        prefs["model_price_unit"] = dict(body.model_price_unit)
    save_token_dashboard_prefs(project_dir, prefs)
    if not path.exists():
        return _SaveTokenDashboardPreferencesResponse(
            status="rejected",
            message="Token 追踪设置写入失败。",
        )
    saved = _token_preferences_view(project_dir, load_token_dashboard_prefs(project_dir))
    return _SaveTokenDashboardPreferencesResponse(
        status="saved",
        message="Token 追踪偏好已持久化。",
        revision=saved.revision,
        preferences=saved,
    )


_WORKFLOW_KIND_MAP: dict[str, str] = {
    "short": "run_short",
    "long_init": "init_long",
    "long_chapter": "run_chapter",
}


def _workflow_payload_dict(body: _StartWorkflowBody) -> dict[str, Any]:
    payload_project_id = str(getattr(body.payload, "project_id", "") or "").strip()
    command_project_id = body.project_id.strip()
    if payload_project_id and command_project_id and payload_project_id != command_project_id:
        raise HTTPException(
            status_code=409,
            detail="命令 projectId 与 payload.projectId 不一致",
        )
    project_id = payload_project_id or command_project_id

    if body.workflow_type == "short":
        if not isinstance(body.payload, _ShortWorkflowPayload):
            raise HTTPException(status_code=422, detail="短篇工作流 payload 类型不匹配")
        raw = body.payload.model_dump()
        request = build_short_request(**raw)
    elif body.workflow_type == "long_init":
        if not isinstance(body.payload, _LongInitWorkflowPayload):
            raise HTTPException(status_code=422, detail="长篇立项 payload 类型不匹配")
        raw = body.payload.model_dump()
        if body.run_mode == "copilot" and not raw["copilot_gates"]:
            raw["copilot_gates"] = ("concept", "spec", "blueprint", "outline")
        request = build_init_long_request(**raw)
    else:
        if not isinstance(body.payload, _LongChapterWorkflowPayload):
            raise HTTPException(status_code=422, detail="长篇章节 payload 类型不匹配")
        request = RunChapterRequest.model_validate(body.payload.model_dump())

    normalized = request.model_dump(mode="json")
    normalized["project_id"] = project_id
    if body.workflow_type == "short" and isinstance(body.payload, _ShortWorkflowPayload):
        # The domain request derives segmented_mode from this UI threshold.
        # Preserve the original value in the durable job intent so previews,
        # retries and contract round-trip tests do not silently lose it.
        normalized["segment_trigger_words"] = body.payload.segment_trigger_words
    return normalized


@router.post("/commands/continue-long-init", response_model=EngineCommandResponse)
async def engine_continue_long_init(
    body: _ContinueLongInitBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    """Continue long initialization from durable intent and validated artifacts."""

    from novel_forge.app_service.contracts import JobCommand

    project_id = body.project_id.strip()
    active_ids = {
        record.job_id
        for record in service.list(project_id=project_id)
        if record.status.value in {"running", "queued"}
    }

    fallback_command: JobCommand | None = None
    fallback_payload: dict[str, Any] | None = None
    if body.fallback_payload is not None:
        normalized_fallback = body.fallback_payload.model_copy(update={"project_id": project_id})
        fallback_payload = _workflow_payload_dict(
            _StartWorkflowBody(
                project_id=project_id,
                workflow_type="long_init",
                run_mode=body.run_mode,
                idempotency_key=f"continue-{project_id}"[:128],
                payload=normalized_fallback,
            )
        )
        fallback_command = JobCommand(
            kind="init_long",
            project_id=project_id,
            payload=fallback_payload,
            metadata={"workflow_type": "long_init"},
        )

    copilot_gates = (
        list(fallback_payload.get("copilot_gates") or [])
        if fallback_payload is not None
        else (["concept", "spec", "blueprint", "outline"] if body.run_mode == "copilot" else [])
    )
    try:
        record = service.resume_project(
            project_id,
            "init_long",
            metadata_updates={
                "workflow_type": "long_init",
                "run_mode": body.run_mode,
                "autorun_after_init": body.run_mode == "autorun",
            },
            payload_updates={"copilot_gates": copilot_gates},
            fallback_command=fallback_command,
        )
    except KeyError:
        return EngineCommandResponse(
            status="rejected",
            message="未找到可恢复的立项意图；请确认项目后选择重新立项。",
        )

    already_running = record.job_id in active_ids
    return EngineCommandResponse(
        status="already_running" if already_running else "accepted",
        message=(
            "该项目已有立项任务正在执行"
            if already_running
            else "已按原始立项参数恢复，将从首个未完成节点继续"
        ),
        task_id=record.job_id,
    )


@router.post("/commands/start-workflow", response_model=EngineCommandResponse)
async def engine_start_workflow(
    body: _StartWorkflowBody,
    service: JobServiceDep,
    runtime: RuntimeDep,
) -> EngineCommandResponse:
    from novel_forge.app_service.contracts import JobCommand

    kind = _WORKFLOW_KIND_MAP[body.workflow_type]
    payload = _workflow_payload_dict(body)
    if not payload["project_id"]:
        if body.workflow_type == "long_chapter":
            raise HTTPException(status_code=422, detail="章节工作流必须关联已有项目。")
        # Project creation used to happen inside the worker.  That left the
        # queued/running job unbound until it finished, so its completed
        # steps could not resolve their artifacts.  Allocate the identifier
        # before creating the durable command; the command, job projection,
        # and workspace execution now own the same directory from the start.
        project_mode = "short" if body.workflow_type == "short" else "long"
        payload["project_id"] = runtime.create_project_id(project_mode)
    start_autorun = getattr(service, "start_book_autorun", None)
    if (
        body.workflow_type == "long_chapter"
        and body.run_mode == "autorun"
        and callable(start_autorun)
    ):
        existing = service.book_autorun_state(payload["project_id"])
        was_active = existing is not None and existing.status.value in {
            "waiting_init",
            "running",
            "retry_wait",
        }
        record = start_autorun(
            project_id=payload["project_id"],
            chapter_number=int(payload["chapter_number"]),
            mode=str(payload.get("autorun_scope") or "book"),
            launch_key=body.idempotency_key,
            writing_mode=str(payload.get("writing_mode") or "whole_chapter"),
            force=bool(payload.get("force")),
            skip_done=bool(payload.get("skip_done", True)),
        )
        state = service.book_autorun_state(payload["project_id"])
        return EngineCommandResponse(
            status="already_running" if was_active else "accepted",
            message=("Engine 连跑会话已在执行" if was_active else "Engine 连跑状态机已启动"),
            task_id=(
                record.job_id if record is not None else state.active_job_id if state else None
            ),
        )
    existing_ids = {
        record.job_id
        for record in service.list(project_id=payload["project_id"])
        if record.status.value in {"running", "queued", "paused"}
    }
    command = JobCommand(
        kind=kind,
        project_id=payload["project_id"],
        payload=payload,
        metadata={
            "idempotency_key": body.idempotency_key,
            "workflow_type": body.workflow_type,
            "run_mode": body.run_mode,
            "autorun_after_init": body.workflow_type == "long_init" and body.run_mode == "autorun",
        },
    )
    record = service.submit(command)
    already_running = record.job_id in existing_ids
    return EngineCommandResponse(
        status="already_running" if already_running else "accepted",
        message=_same_project_write_conflict_message(record, requested_kind=kind)
        if already_running
        else "工作流任务已提交",
        task_id=record.job_id,
    )


@router.post(
    "/commands/generate-workflow-fields",
    response_model=_GenerateWorkflowFieldsResponse,
    response_model_exclude_none=True,
)
async def engine_generate_workflow_fields(
    body: _GenerateWorkflowFieldsBody,
    runtime: RuntimeDep,
) -> _GenerateWorkflowFieldsResponse:
    """Generate a reversible workflow-field candidate for UI diff preview."""

    if body.operation == "generate":
        generated = await generate_config(
            body.mode,
            body.user_hint,
            current_config=body.current_payload,
            generation_mode=body.generation_mode,
            creative_profile=body.creative_profile,
            hard_constraints=body.hard_constraints,
            runtime=runtime,
        )
        operation_label = "生成"
    else:
        if not body.current_payload:
            return _GenerateWorkflowFieldsResponse(
                status="rejected",
                message="当前创作配置为空，无法执行定向润色。",
            )
        generated = await polish_config(
            body.mode,
            body.current_payload,
            body.user_hint,
            selected_suggestions=body.selected_suggestions,
            focus_fields=body.focus_fields,
            runtime=runtime,
        )
        operation_label = "润色"

    payload = dict(generated)
    raw_suggestions = payload.pop(AI_POLISH_SUGGESTIONS_FIELD, [])
    raw_creative_note = payload.pop(AI_CREATIVE_NOTE_FIELD, None)
    suggestions = (
        [str(item) for item in raw_suggestions if str(item).strip()]
        if isinstance(raw_suggestions, list)
        else []
    )
    creative_note = raw_creative_note if isinstance(raw_creative_note, dict) else None
    return _GenerateWorkflowFieldsResponse(
        status="generated",
        message=f"AI {operation_label}已完成，请审阅字段差异后选择应用。",
        payload=payload,
        suggestions=suggestions,
        creative_note=creative_note,
    )


def _workflow_mode(value: str) -> Literal["short", "long"]:
    if value not in {"short", "long"}:
        raise HTTPException(status_code=422, detail=f"不支持的表单模式: {value}")
    return value


def _workflow_draft_path(storage: FileSystemStorage, mode: str) -> Path:
    return storage.root / ".presets" / ".draft" / f"_autosave_{_workflow_mode(mode)}.json"


def _safe_preset_name(name: str) -> str:
    clean = "".join(char if char.isalnum() or char in "-_" else "_" for char in name.strip())
    if not clean:
        raise HTTPException(status_code=422, detail="预设名称不能为空")
    return clean


def _workflow_preset_path(storage: FileSystemStorage, mode: str, name: str) -> Path:
    return storage.root / ".presets" / _workflow_mode(mode) / f"{_safe_preset_name(name)}.json"


def _workflow_history_dir(storage: FileSystemStorage, mode: str, name: str) -> Path:
    return storage.root / ".presets" / _workflow_mode(mode) / ".history" / _safe_preset_name(name)


def _file_revision(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _file_time_label(path: Path) -> str:
    if not path.exists():
        return ""
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _assert_expected_revision(path: Path, expected_revision: str | None) -> None:
    if expected_revision is None:
        return
    current_revision = _file_revision(path)
    if expected_revision != current_revision:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "持久化内容已被其他窗口更新",
                "currentRevision": current_revision,
            },
        )


@router.get("/workflow/drafts/{mode}", response_model=WorkflowDraftView)
async def get_workflow_draft(mode: str, storage: StorageDep) -> WorkflowDraftView:
    normalized_mode = _workflow_mode(mode)
    path = _workflow_draft_path(storage, normalized_mode)
    if not path.exists():
        return WorkflowDraftView(mode=normalized_mode)
    return WorkflowDraftView(
        mode=normalized_mode,
        payload=storage.load_json(path),
        revision=_file_revision(path),
        saved_at_label=_file_time_label(path),
    )


@router.put("/workflow/drafts/{mode}", response_model=WorkflowPersistenceResponse)
async def save_workflow_draft(
    mode: str,
    body: _WorkflowPersistenceBody,
    storage: StorageDep,
) -> WorkflowPersistenceResponse:
    normalized_mode = _workflow_mode(mode)
    path = _workflow_draft_path(storage, normalized_mode)
    _assert_expected_revision(path, body.expected_revision)
    storage.save_json(path, sanitize_preset_payload(normalized_mode, body.payload))
    return WorkflowPersistenceResponse(
        status="saved",
        message="表单草稿已持久化",
        revision=_file_revision(path),
        saved_at_label=_file_time_label(path),
    )


@router.get("/workflow/presets/{mode}", response_model=WorkflowPresetListView)
async def list_workflow_presets(
    mode: str,
    storage: StorageDep,
) -> WorkflowPresetListView:
    normalized_mode = _workflow_mode(mode)
    directory = storage.root / ".presets" / normalized_mode
    records: list[WorkflowPresetRecordView] = []
    if directory.exists():
        for path in sorted(directory.glob("*.json"), key=lambda item: item.stem.casefold()):
            records.append(
                WorkflowPresetRecordView(
                    name=path.stem,
                    payload=storage.load_json(path),
                    revision=_file_revision(path),
                    updated_at_label=_file_time_label(path),
                )
            )
    return WorkflowPresetListView(presets=records)


@router.put("/workflow/presets/{mode}/{name}", response_model=WorkflowPersistenceResponse)
async def save_workflow_preset(
    mode: str,
    name: str,
    body: _WorkflowPersistenceBody,
    storage: StorageDep,
) -> WorkflowPersistenceResponse:
    normalized_mode = _workflow_mode(mode)
    path = _workflow_preset_path(storage, normalized_mode, name)
    _assert_expected_revision(path, body.expected_revision)
    if path.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = path.with_name(f"{path.name}.bak.{timestamp}")
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
    storage.save_json(path, sanitize_preset_payload(normalized_mode, body.payload))
    return WorkflowPersistenceResponse(
        status="saved",
        message=f"预设「{name}」已保存",
        revision=_file_revision(path),
        saved_at_label=_file_time_label(path),
    )


@router.delete("/workflow/presets/{mode}/{name}", response_model=WorkflowPersistenceResponse)
async def delete_workflow_preset(
    mode: str,
    name: str,
    storage: StorageDep,
    expected_revision: str | None = None,
) -> WorkflowPersistenceResponse:
    path = _workflow_preset_path(storage, mode, name)
    if not path.exists():
        return WorkflowPersistenceResponse(status="not_found", message="预设不存在")
    _assert_expected_revision(path, expected_revision)
    path.unlink()
    return WorkflowPersistenceResponse(status="deleted", message=f"预设「{name}」已删除")


@router.get(
    "/workflow/presets/{mode}/{name}/history",
    response_model=WorkflowAiHistoryListView,
)
async def list_workflow_ai_history(
    mode: str,
    name: str,
    storage: StorageDep,
) -> WorkflowAiHistoryListView:
    normalized_mode = _workflow_mode(mode)
    directory = _workflow_history_dir(storage, normalized_mode, name)
    entries: list[WorkflowAiHistoryEntryView] = []
    if not directory.exists():
        return WorkflowAiHistoryListView(entries=entries)
    for path in sorted(directory.glob("*.json"), reverse=True):
        try:
            raw = storage.load_json(path)
        except (OSError, ValueError):
            continue
        operation = raw.get("operation")
        timestamp = raw.get("timestamp")
        data = raw.get("data")
        if (
            not isinstance(operation, str)
            or not isinstance(timestamp, str)
            or not isinstance(data, dict)
        ):
            continue
        entries.append(
            WorkflowAiHistoryEntryView(
                id=path.name,
                timestamp=timestamp,
                operation=operation,
                data=data,
                hint=raw.get("hint") if isinstance(raw.get("hint"), str) else None,
                selected_suggestions=[
                    str(item)
                    for item in raw.get("selected_suggestions", [])
                    if isinstance(item, str)
                ]
                if isinstance(raw.get("selected_suggestions"), list)
                else [],
                focus_fields=[
                    str(item) for item in raw.get("focus_fields", []) if isinstance(item, str)
                ]
                if isinstance(raw.get("focus_fields"), list)
                else [],
                metadata=raw.get("metadata") if isinstance(raw.get("metadata"), dict) else None,
            )
        )
    return WorkflowAiHistoryListView(entries=entries)


@router.put(
    "/workflow/presets/{mode}/{name}/history",
    response_model=WorkflowPersistenceResponse,
)
async def save_workflow_ai_history(
    mode: str,
    name: str,
    body: _WorkflowAiHistoryBody,
    storage: StorageDep,
) -> WorkflowPersistenceResponse:
    normalized_mode = _workflow_mode(mode)
    directory = _workflow_history_dir(storage, normalized_mode, name)
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = directory / f"{timestamp}_{body.operation}.json"
    storage.save_json(
        path,
        {
            "timestamp": timestamp,
            "operation": body.operation,
            "data": sanitize_preset_payload(normalized_mode, body.data),
            **({"hint": body.hint} if body.hint else {}),
            **(
                {"selected_suggestions": body.selected_suggestions}
                if body.selected_suggestions
                else {}
            ),
            **({"focus_fields": body.focus_fields} if body.focus_fields else {}),
            **({"metadata": body.metadata} if body.metadata else {}),
        },
    )
    history_paths = sorted(directory.glob("*.json"), reverse=True)
    for stale in history_paths[20:]:
        stale.unlink(missing_ok=True)
    return WorkflowPersistenceResponse(
        status="saved",
        message="创作札记已持久化",
        revision=_file_revision(path),
        saved_at_label=_file_time_label(path),
    )


@router.delete(
    "/workflow/presets/{mode}/{name}/history",
    response_model=WorkflowPersistenceResponse,
)
async def clear_workflow_ai_history(
    mode: str,
    name: str,
    storage: StorageDep,
) -> WorkflowPersistenceResponse:
    directory = _workflow_history_dir(storage, mode, name)
    removed = 0
    if directory.exists():
        for path in directory.glob("*.json"):
            path.unlink(missing_ok=True)
            removed += 1
    return WorkflowPersistenceResponse(
        status="deleted",
        message="创作札记已清空" if removed else "暂无可清空的创作札记",
    )


@router.post("/commands/synthesize-voice", response_model=EngineCommandResponse)
async def engine_synthesize_voice(
    body: _SynthesizeVoiceBody, service: JobServiceDep
) -> EngineCommandResponse:
    from novel_forge.app_service.contracts import JobCommand

    command = JobCommand(
        kind="tts_synthesize",
        project_id=body.project_id,
        payload={
            "project_id": body.project_id,
            "chapter_number": body.chapter_number,
            "segment_ids": body.segment_ids or [],
            "provider": body.provider,
        },
    )
    record = service.submit(command)
    return EngineCommandResponse(
        status="accepted",
        message="语音合成任务已提交",
        task_id=record.job_id,
    )


@router.post("/commands/build-voice-team", response_model=EngineCommandResponse)
async def engine_build_voice_team(
    body: _BuildVoiceTeamBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    from novel_forge.app_service.contracts import JobCommand

    record = service.submit(
        JobCommand(
            kind="tts_build_voice_team",
            project_id=body.project_id,
            payload=body.model_dump(mode="json", exclude={"kind"}),
        )
    )
    return EngineCommandResponse(
        status="accepted",
        message="配音团队构建任务已提交。",
        task_id=record.job_id,
    )


@router.post("/commands/rebuild-narrator-voice", response_model=EngineCommandResponse)
async def engine_rebuild_narrator_voice(
    body: _RebuildNarratorVoiceBody,
    runtime: RuntimeDep,
    storage: StorageDep,
) -> EngineCommandResponse:
    execution = await execute_build_narrator_profile(
        project_id=body.project_id,
        provider=body.provider,
        settings=runtime.settings,
        layout=ProjectLayout(storage.existing_project_dir(body.project_id)),
    )
    error = execution.result.get("error")
    if error:
        return EngineCommandResponse(status="rejected", message=str(error))
    return EngineCommandResponse(
        status="accepted",
        message="旁白音色已按当前作品档案重新生成，请试听确认。",
    )


@router.post("/commands/confirm-voice-team", response_model=EngineCommandResponse)
async def engine_confirm_voice_team(
    body: _ConfirmVoiceTeamBody,
    runtime: RuntimeDep,
    storage: StorageDep,
) -> EngineCommandResponse:
    execution = await execute_confirm_voice_team(
        project_id=body.project_id,
        settings=runtime.settings,
        layout=ProjectLayout(storage.existing_project_dir(body.project_id)),
    )
    error = execution.result.get("error")
    if error:
        return EngineCommandResponse(status="rejected", message=str(error))
    return EngineCommandResponse(
        status="accepted",
        message="配音团队已完成项目级确认，可进入自动配音与后期流程。",
    )


@router.post("/commands/clone-character-voice", response_model=EngineCommandResponse)
async def engine_clone_character_voice(
    body: _CloneCharacterVoiceBody,
    runtime: RuntimeDep,
    storage: StorageDep,
) -> EngineCommandResponse:
    execution = await execute_clone_character_voice(
        project_id=body.project_id,
        character_id=body.character_id,
        reference_audio=body.reference_audio,
        reference_transcript=body.reference_transcript,
        authorized=body.authorized,
        provider=body.provider,
        settings=runtime.settings,
        layout=ProjectLayout(storage.existing_project_dir(body.project_id)),
    )
    error = execution.result.get("error")
    if error:
        return EngineCommandResponse(status="rejected", message=str(error))
    return EngineCommandResponse(
        status="accepted",
        message="角色音色已完成克隆并写入项目，等待试听批准。",
    )


@router.post("/commands/design-character-voice", response_model=EngineCommandResponse)
async def engine_design_character_voice(
    body: _DesignCharacterVoiceBody,
    runtime: RuntimeDep,
    storage: StorageDep,
) -> EngineCommandResponse:
    execution = await execute_design_character_voice(
        project_id=body.project_id,
        character_id=body.character_id,
        description=body.description,
        provider=body.provider,
        settings=runtime.settings,
        layout=ProjectLayout(storage.existing_project_dir(body.project_id)),
    )
    error = execution.result.get("error")
    if error:
        return EngineCommandResponse(status="rejected", message=str(error))
    return EngineCommandResponse(
        status="accepted",
        message="角色候选音色已生成并持久化，等待试听批准。",
    )


@router.post("/commands/approve-character-voice", response_model=EngineCommandResponse)
async def engine_approve_character_voice(
    body: _ApproveCharacterVoiceBody,
    runtime: RuntimeDep,
    storage: StorageDep,
) -> EngineCommandResponse:
    execution = await execute_approve_character_voice(
        project_id=body.project_id,
        character_id=body.character_id,
        settings=runtime.settings,
        layout=ProjectLayout(storage.existing_project_dir(body.project_id)),
    )
    error = execution.result.get("error")
    if error:
        return EngineCommandResponse(status="rejected", message=str(error))
    return EngineCommandResponse(status="accepted", message="角色音色已批准并写入配音团队。")


@router.post(
    "/commands/preview-character-voice",
    response_model=EngineCommandResponse,
    response_model_exclude_none=True,
)
async def engine_preview_character_voice(
    body: _PreviewCharacterVoiceBody,
    runtime: RuntimeDep,
    storage: StorageDep,
) -> EngineCommandResponse:
    project_dir = storage.existing_project_dir(body.project_id).resolve()
    try:
        execution = await execute_preview_character_voice(
            project_id=body.project_id,
            character_id=body.character_id,
            sample_text=body.sample_text,
            provider=body.provider,
            settings=runtime.settings,
            layout=ProjectLayout(project_dir),
        )
    except TimeoutError as exc:
        # The interactive audition waits on the project TTS lock for a bounded
        # window; when a background auto-dub / script job holds the lock longer,
        # surface a structured rejection instead of a raw 500 so the UI can
        # explain the retry instead of blaming the engine or TTS runtime.
        return EngineCommandResponse(
            status="rejected",
            message=str(exc),
            error_code="tts_preview_lock_busy",
        )
    error = execution.result.get("error")
    if error:
        return EngineCommandResponse(status="rejected", message=str(error))
    audio_path = Path(str(execution.result.get("audio_path") or "")).resolve()
    tts_dir = (project_dir / "tts").resolve()
    if tts_dir not in audio_path.parents or not audio_path.is_file():
        return EngineCommandResponse(status="rejected", message="试听音频产物不存在。")
    relative_path = quote(audio_path.relative_to(tts_dir).as_posix(), safe="/")
    return EngineCommandResponse(
        status="accepted",
        message="角色音色试听已生成。",
        audio_url=f"/api/v1/engine/voice/projects/{body.project_id}/audio/{relative_path}",
    )


@router.post(
    "/commands/build-voice-preview-plan",
    response_model=EngineCommandResponse,
    response_model_exclude_none=True,
)
async def engine_build_voice_preview_plan(
    body: _BuildVoicePreviewPlanBody,
    runtime: RuntimeDep,
    storage: StorageDep,
) -> EngineCommandResponse:
    """Build multi-candidate A/B preview plans (shared service with the
    PySide6 voice studio — deterministic filtering, no synthesis yet)."""
    try:
        layout = ProjectLayout(storage.existing_project_dir(body.project_id))
        plans = await build_preview_plan(
            layout=layout,
            settings=runtime.settings,
            characters=body.characters,
            provider=body.provider,
            sample_text=body.sample_text,
            candidate_count=body.candidate_count,
            language=body.language,
        )
    except Exception as exc:
        return EngineCommandResponse(
            status="rejected",
            message=str(exc).strip() or type(exc).__name__,
        )
    return EngineCommandResponse(
        status="accepted",
        message=f"已为 {len(plans)} 个角色生成音色候选计划。",
        data={"plans": [plan.model_dump(mode="json") for plan in plans]},
    )


@router.post(
    "/commands/generate-voice-previews",
    response_model=EngineCommandResponse,
    response_model_exclude_none=True,
)
async def engine_generate_voice_previews(
    body: _GenerateVoicePreviewsBody,
    runtime: RuntimeDep,
    storage: StorageDep,
) -> EngineCommandResponse:
    """Synthesize audition clips for every candidate of one preview plan.

    The shared ``generate_candidate_previews`` service fills ``sample_path``;
    this endpoint additionally maps each path to a playable ``audio_url``
    (same static voice-audio route the PySide6 flow already serves).
    """
    try:
        layout = ProjectLayout(storage.existing_project_dir(body.project_id))
        generated = await generate_candidate_previews(
            layout=layout,
            settings=runtime.settings,
            plan=VoicePreviewPlan.model_validate(body.plan),
            provider=body.provider,
        )
    except Exception as exc:
        return EngineCommandResponse(
            status="rejected",
            message=str(exc).strip() or type(exc).__name__,
        )
    project_dir = storage.existing_project_dir(body.project_id).resolve()
    tts_dir = (project_dir / "tts").resolve()
    plan_data = generated.model_dump(mode="json")
    for candidate in plan_data.get("candidates", []):
        path = str(candidate.get("sample_path") or "")
        candidate["audio_url"] = ""
        if not path:
            continue
        try:
            audio_path = Path(path).resolve()
            relative_path = quote(audio_path.relative_to(tts_dir).as_posix(), safe="/")
            candidate["audio_url"] = (
                f"/api/v1/engine/voice/projects/{body.project_id}/audio/{relative_path}"
            )
        except ValueError:
            candidate["audio_url"] = ""
    succeeded = sum(1 for c in plan_data["candidates"] if not c.get("error"))
    return EngineCommandResponse(
        status="accepted",
        message=f"候选试听已生成（{succeeded}/{len(plan_data['candidates'])} 成功）。",
        data={"plan": plan_data},
    )


@router.post(
    "/commands/confirm-voice-preview",
    response_model=EngineCommandResponse,
    response_model_exclude_none=True,
)
async def engine_confirm_voice_preview(
    body: _ConfirmVoicePreviewBody,
    runtime: RuntimeDep,
    storage: StorageDep,
) -> EngineCommandResponse:
    """Persist the author-chosen candidate voice into the voice team
    (shared ``confirm_preview`` service; invalidates team confirmation)."""
    try:
        team = confirm_preview(
            layout=ProjectLayout(storage.existing_project_dir(body.project_id)),
            settings=runtime.settings,
            character_id=body.character_id,
            voice_id=body.voice_id,
            speed=body.speed,
            volume=body.volume,
            provider=body.provider,
            model_id=body.model_id,
            sample_text=body.sample_text,
            sample_path=body.sample_path,
        )
    except Exception as exc:
        return EngineCommandResponse(
            status="rejected",
            message=str(exc).strip() or type(exc).__name__,
        )
    return EngineCommandResponse(
        status="accepted",
        message=f"已确认角色音色并写入配音团队（{body.character_id}）。",
        data={"team": team},
    )


@router.post("/commands/update-voice-performance", response_model=EngineCommandResponse)
async def engine_update_voice_performance(
    body: _UpdateVoicePerformanceBody,
    storage: StorageDep,
) -> EngineCommandResponse:
    execution = await execute_update_voice_performance(
        project_id=body.project_id,
        character_id=body.character_id,
        speed_offset=body.speed_offset,
        pitch_offset=body.pitch_offset,
        volume_offset=body.volume_offset,
        layout=ProjectLayout(storage.existing_project_dir(body.project_id)),
    )
    error = execution.result.get("error")
    if error:
        return EngineCommandResponse(status="rejected", message=str(error))
    return EngineCommandResponse(
        status="accepted",
        message=(
            f"表达参数已写入项目：语速 {1 + body.speed_offset:.2f}× · "
            f"音调 {body.pitch_offset:+d} st · 音量 {1 + body.volume_offset:.2f}×。"
        ),
    )


@router.post("/commands/assign-catalog-voice", response_model=EngineCommandResponse)
async def engine_assign_catalog_voice(
    body: _AssignCatalogVoiceBody,
    runtime: RuntimeDep,
    storage: StorageDep,
) -> EngineCommandResponse:
    execution = await execute_assign_catalog_voice(
        project_id=body.project_id,
        character_id=body.character_id,
        voice_id=body.voice_id,
        provider=body.provider,
        settings=runtime.settings,
        layout=ProjectLayout(storage.existing_project_dir(body.project_id)),
    )
    error = execution.result.get("error")
    if error:
        return EngineCommandResponse(status="rejected", message=str(error))
    voice_label = str(execution.result.get("voice_label") or body.voice_id)
    return EngineCommandResponse(
        status="accepted",
        message=f"音色已写入项目：{voice_label}。试听缓存已失效，请重新试听并确认团队。",
    )


@router.get(
    "/voice/projects/{project_id}/catalog",
    response_model=EngineVoiceCatalogView,
    response_model_exclude_none=True,
)
async def engine_voice_catalog(
    project_id: str,
    query: EngineQueryServiceDep,
    runtime: RuntimeDep,
) -> EngineVoiceCatalogView:
    """Return the current provider catalog through the Engine read boundary."""

    studio = query.get_voice_studio(project_id)
    try:
        execution = await execute_list_tts_voices(
            project_id=project_id,
            settings=runtime.settings,
            provider=studio.provider_label,
            limit=200,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raw_voices = execution.result.get("voices")
    options: list[EngineVoiceCatalogOptionView] = []
    for raw in raw_voices if isinstance(raw_voices, list) else []:
        if not isinstance(raw, dict):
            continue
        voice_id = str(raw.get("voice_id") or "").strip()
        if not voice_id:
            continue
        tags = raw.get("tags")
        tag_label = (
            "、".join(str(item).strip() for item in tags if str(item).strip())
            if isinstance(tags, list)
            else ""
        )
        description = " · ".join(
            value
            for value in (
                str(raw.get("voice_description") or "").strip(),
                str(raw.get("gender") or "").strip(),
                str(raw.get("age_hint") or "").strip(),
                str(raw.get("personality") or "").strip(),
                tag_label,
            )
            if value
        )
        options.append(
            EngineVoiceCatalogOptionView(
                id=voice_id,
                label=str(raw.get("name") or voice_id).strip(),
                description=description[:360],
            )
        )
    return EngineVoiceCatalogView(
        project_id=project_id,
        provider_label=studio.provider_label,
        options=options,
    )


@router.get(
    "/voice/projects/{project_id}/audio/{relative_path:path}",
    response_class=FileResponse,
)
async def engine_voice_project_audio(
    project_id: str,
    relative_path: str,
    storage: StorageDep,
) -> FileResponse:
    project_dir = storage.existing_project_dir(project_id).resolve()
    tts_dir = (project_dir / "tts").resolve()
    audio_path = (tts_dir / relative_path).resolve()
    if tts_dir not in audio_path.parents or not audio_path.is_file():
        raise HTTPException(status_code=404, detail="试听音频不存在")
    return FileResponse(audio_path)


@router.get(
    "/voice/projects/{project_id}/chapters/{chapter_number}/audio",
    response_class=FileResponse,
)
async def engine_voice_chapter_audio(
    project_id: str,
    chapter_number: int,
    storage: StorageDep,
) -> FileResponse:
    project_dir = storage.existing_project_dir(project_id).resolve()
    layout = ProjectLayout(project_dir)
    result_path = layout.tts_audio_result_path(chapter_number)
    try:
        result = require_delivery_ready(
            ChapterAudioResult.model_validate_json(result_path.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="章节主音轨尚未达到交付条件") from exc
    audio_path = Path(result.assembled_audio_path).resolve()
    if project_dir not in audio_path.parents or not audio_path.is_file():
        raise HTTPException(status_code=404, detail="章节主音轨不存在")
    return FileResponse(audio_path)


@router.get(
    "/voice/projects/{project_id}/chapters/{chapter_number}/stems/{stem_name}",
    response_class=FileResponse,
)
async def engine_voice_chapter_stem(
    project_id: str,
    chapter_number: int,
    stem_name: Literal["voice", "bed", "sfx"],
    storage: StorageDep,
) -> FileResponse:
    project_dir = storage.existing_project_dir(project_id).resolve()
    result_path = ProjectLayout(project_dir).tts_audio_result_path(chapter_number)
    try:
        result = ChapterAudioResult.model_validate_json(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="章节分轨尚未生成") from exc
    report = result.mix_render_report
    raw_stem_path = report.stem_paths.get(stem_name, "") if report else ""
    if not raw_stem_path:
        raise HTTPException(status_code=404, detail="章节分轨不存在")
    stem_path = Path(raw_stem_path).resolve()
    if project_dir not in stem_path.parents or not stem_path.is_file():
        raise HTTPException(status_code=404, detail="章节分轨不存在")
    return FileResponse(stem_path, filename=f"chapter_{chapter_number:03d}_{stem_name}_stem.wav")


@router.get(
    "/voice/projects/{project_id}/sound-assets/{asset_id}/audio",
    response_class=FileResponse,
)
async def engine_voice_sound_asset_audio(
    project_id: str,
    asset_id: str,
    storage: StorageDep,
) -> FileResponse:
    service = VoiceStudioProjectService(
        ProjectLayout(storage.existing_project_dir(project_id)),
        project_id=project_id,
    )
    asset = next((item for item in service.all_sound_assets() if item.asset_id == asset_id), None)
    if asset is None:
        raise HTTPException(status_code=404, detail="声音资产不存在")
    audio_path = service.sound_asset_path(asset).resolve()
    if not audio_path.is_file():
        raise HTTPException(status_code=404, detail="声音资产文件不存在")
    return FileResponse(audio_path)


@router.get(
    "/voice/projects/{project_id}/exports/{filename}",
    response_class=FileResponse,
)
async def engine_voice_export_download(
    project_id: str,
    filename: str,
    storage: StorageDep,
) -> FileResponse:
    export_dir = (
        ProjectLayout(storage.existing_project_dir(project_id)).root / "exports" / "voice"
    ).resolve()
    path = (export_dir / filename).resolve()
    if export_dir not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="导出文件不存在")
    return FileResponse(path, filename=path.name)


@router.post("/commands/generate-voice-script", response_model=EngineCommandResponse)
async def engine_generate_voice_script(
    body: _GenerateVoiceScriptBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    from novel_forge.app_service.contracts import JobCommand

    record = service.submit(
        JobCommand(
            kind="tts_generate_script",
            project_id=body.project_id,
            payload=body.model_dump(mode="json", exclude={"kind"}),
        )
    )
    return EngineCommandResponse(
        status="accepted",
        message=f"第 {body.chapter_number} 章配音稿生成任务已提交。",
        task_id=record.job_id,
    )


@router.post("/commands/analyze-voice-script-style", response_model=EngineCommandResponse)
async def engine_analyze_voice_script_style(
    body: _AnalyzeVoiceScriptStyleBody,
    runtime: RuntimeDep,
) -> EngineCommandResponse:
    layout = ProjectLayout(runtime.storage.project_dir(body.project_id))
    try:
        execution = await execute_analyze_dubbing_style_reference(
            project_id=body.project_id,
            reference_script_text=body.reference_script_text,
            reference_script_name=body.source_name,
            settings=runtime.settings,
            layout=layout,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return EngineCommandResponse(
        status="accepted",
        message=str(execution.result.get("message") or "参考配音风格画像已建立。"),
        data=execution.result,
    )


@router.post("/commands/save-voice-script", response_model=EngineCommandResponse)
async def engine_save_voice_script(
    body: _SaveVoiceScriptBody,
    storage: StorageDep,
) -> EngineCommandResponse:
    execution = await execute_save_dubbing_script(
        project_id=body.project_id,
        chapter_number=body.chapter_number,
        edits=[edit.model_dump(mode="json", exclude_none=True) for edit in body.edits],
        layout=ProjectLayout(storage.existing_project_dir(body.project_id)),
    )
    error = execution.result.get("error")
    if error:
        return EngineCommandResponse(status="rejected", message=str(error))
    changed_count = len(execution.result.get("changed_segment_indices") or [])
    return EngineCommandResponse(
        status="accepted",
        message=f"已保存 {changed_count} 个片段，并使旧音频与字幕进入待重建状态。",
    )


@router.post("/commands/save-voice-guidance", response_model=EngineCommandResponse)
async def engine_save_voice_guidance(
    body: _SaveVoiceGuidanceBody,
    storage: StorageDep,
) -> EngineCommandResponse:
    """Persist Voice Room performance guidance without mutating the source script."""
    layout = ProjectLayout(storage.existing_project_dir(body.project_id))
    script_path = layout.tts_dubbing_script_path(body.chapter_number)
    if not script_path.is_file():
        return EngineCommandResponse(
            status="rejected", message="配音脚本不存在，无法保存试听指导。"
        )
    try:
        script = DubbingScript.model_validate_json(script_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return EngineCommandResponse(status="rejected", message=f"配音脚本读取失败：{exc}")
    if script.chapter_number != body.chapter_number:
        return EngineCommandResponse(status="rejected", message="配音脚本章节与请求不一致。")

    source_by_index = {segment.segment_index: segment for segment in script.segments}
    normalized_segments: list[DubbingSegment] = []
    for edit in body.edits:
        source = source_by_index.get(edit.segment_index)
        if source is None:
            return EngineCommandResponse(
                status="rejected",
                message=f"配音脚本中不存在第 {edit.segment_index + 1} 段。",
            )
        try:
            candidate = DubbingSegment.model_validate(edit.segment_override)
        except ValueError as exc:
            return EngineCommandResponse(status="rejected", message=f"试听指导无法读取：{exc}")
        if candidate.segment_index != source.segment_index:
            return EngineCommandResponse(status="rejected", message="试听指导与当前片段不匹配。")
        # Text, speaker identity, type and lineage stay source-authoritative
        # until a take is explicitly accepted. Only performance fields survive.
        normalized_segments.append(
            refresh_segment_uid(
                candidate.model_copy(
                    update={
                        "text": source.text,
                        "character_id": source.character_id,
                        "character_name": source.character_name,
                        "segment_type": source.segment_type,
                        "source_paragraph": source.source_paragraph,
                    }
                )
            )
        )
    try:
        VoiceStudioProjectService(layout, project_id=body.project_id).save_take_drafts(
            body.chapter_number,
            script_hash=script.script_hash or compute_dubbing_script_hash(script),
            segments=normalized_segments,
        )
    except ValueError as exc:
        return EngineCommandResponse(status="rejected", message=str(exc))
    return EngineCommandResponse(
        status="accepted",
        message=(
            f"已保存 {len(normalized_segments)} 段待审试听指导；已有候选试听已舍弃，正式脚本未变。"
        ),
    )


@router.post(
    "/commands/preview-voice-segment",
    response_model=EngineCommandResponse,
    response_model_exclude_none=True,
)
async def engine_preview_voice_segment(
    body: _PreviewVoiceSegmentBody,
    runtime: RuntimeDep,
    storage: StorageDep,
) -> EngineCommandResponse:
    execution = await execute_synthesize_segment(
        project_id=body.project_id,
        chapter_number=body.chapter_number,
        segment_index=body.segment_index,
        settings=runtime.settings,
        layout=ProjectLayout(storage.existing_project_dir(body.project_id)),
        provider=body.provider,
        segment_override=body.segment_override,
    )
    error = execution.result.get("error")
    if error:
        return EngineCommandResponse(status="rejected", message=str(error))
    take_id = str(execution.result.get("take_id") or "")
    return EngineCommandResponse(
        status="accepted",
        message="候选音轨已生成，等待采纳或丢弃。",
        take_id=take_id,
        audio_url=(
            f"/api/v1/engine/voice/projects/{body.project_id}/chapters/"
            f"{body.chapter_number}/takes/{take_id}/audio"
        ),
    )


@router.get(
    "/voice/projects/{project_id}/chapters/{chapter_number}/takes/{take_id}/audio",
    response_class=FileResponse,
)
async def engine_voice_take_audio(
    project_id: str,
    chapter_number: int,
    take_id: str,
    storage: StorageDep,
) -> FileResponse:
    project_dir = storage.existing_project_dir(project_id).resolve()
    service = VoiceStudioProjectService(
        ProjectLayout(project_dir),
        project_id=project_id,
    )
    take = next(
        (
            item
            for item in service.load_take_manifest(chapter_number).takes
            if item.take_id == take_id
        ),
        None,
    )
    if take is None:
        raise HTTPException(status_code=404, detail="试听版本不存在")
    audio_path = Path(take.segment_result.audio_path).resolve()
    if project_dir not in audio_path.parents or not audio_path.is_file():
        raise HTTPException(status_code=404, detail="试听音频不存在")
    return FileResponse(audio_path)


@router.post("/commands/accept-voice-take", response_model=EngineCommandResponse)
async def engine_accept_voice_take(
    body: _AcceptVoiceTakeBody,
    runtime: RuntimeDep,
    storage: StorageDep,
) -> EngineCommandResponse:
    execution = await execute_accept_segment_take(
        project_id=body.project_id,
        chapter_number=body.chapter_number,
        take_id=body.take_id,
        settings=runtime.settings,
        layout=ProjectLayout(storage.existing_project_dir(body.project_id)),
    )
    error = execution.result.get("error")
    if error:
        return EngineCommandResponse(status="rejected", message=str(error))
    return EngineCommandResponse(
        status="accepted",
        message="候选音轨已采纳并写入项目产物；章节装配已标记为待刷新。",
        take_id=body.take_id,
    )


@router.post("/commands/reject-voice-take", response_model=EngineCommandResponse)
async def engine_reject_voice_take(
    body: _RejectVoiceTakeBody,
    storage: StorageDep,
) -> EngineCommandResponse:
    service = VoiceStudioProjectService(
        ProjectLayout(storage.existing_project_dir(body.project_id)),
        project_id=body.project_id,
    )
    if not service.reject_candidate_take(body.chapter_number, body.take_id):
        return EngineCommandResponse(
            status="rejected",
            message="试听版本不存在或已经处理；正式音轨未改变。",
        )
    return EngineCommandResponse(
        status="accepted",
        message="已舍弃试听；正式脚本与正式音轨未改变。",
        take_id=body.take_id,
    )


@router.post("/commands/reassemble-voice", response_model=EngineCommandResponse)
async def engine_reassemble_voice(
    body: _ReassembleVoiceBody,
    runtime: RuntimeDep,
    storage: StorageDep,
) -> EngineCommandResponse:
    execution = await execute_reassemble_chapter_audio(
        project_id=body.project_id,
        chapter_number=body.chapter_number,
        settings=runtime.settings,
        layout=ProjectLayout(storage.existing_project_dir(body.project_id)),
        fast=body.fast,
    )
    error = execution.result.get("error")
    if error:
        return EngineCommandResponse(status="rejected", message=str(error))
    return EngineCommandResponse(
        status="accepted",
        message="章节音频已重新装配，字幕与混音清单已刷新。",
    )


@router.post("/commands/full-voice-pipeline", response_model=EngineCommandResponse)
async def engine_full_voice_pipeline(
    body: _FullVoicePipelineBody,
    service: JobServiceDep,
    storage: StorageDep,
) -> EngineCommandResponse:
    from novel_forge.app_service.contracts import JobCommand

    layout = ProjectLayout(storage.existing_project_dir(body.project_id))
    chapter_path = layout.chapter_path(body.chapter_number)
    if not chapter_path.is_file():
        return EngineCommandResponse(
            status="rejected",
            message=f"第 {body.chapter_number} 章尚未定稿，无法启动完整配音流程。",
        )
    chapter_text = chapter_path.read_text(encoding="utf-8").strip()
    if not chapter_text:
        return EngineCommandResponse(
            status="rejected",
            message=f"第 {body.chapter_number} 章正文为空，无法启动完整配音流程。",
        )
    characters: list[dict[str, Any]] = []
    if layout.characters_path.is_file():
        try:
            bible = CharacterBible.model_validate_json(
                layout.characters_path.read_text(encoding="utf-8")
            )
            characters = [item.model_dump(mode="json") for item in bible.characters]
        except (OSError, ValueError):
            characters = []
    context = VoiceStudioProjectService(
        layout,
        project_id=body.project_id,
    ).story_sound_context()
    record = service.submit(
        JobCommand(
            kind="tts_full_pipeline",
            project_id=body.project_id,
            payload={
                "project_id": body.project_id,
                "chapter_number": body.chapter_number,
                "chapter_text": chapter_text,
                "characters": characters,
                "provider": body.provider,
                "automation_mode": body.automation_mode,
                "genre": str(context.get("genre") or ""),
                "tone": str(context.get("tone") or ""),
            },
        )
    )
    return EngineCommandResponse(
        status="accepted",
        message=f"第 {body.chapter_number} 章完整配音流程已提交。",
        task_id=record.job_id,
    )


@router.post("/commands/clear-voice-artifacts", response_model=EngineCommandResponse)
async def engine_clear_voice_artifacts(
    body: _ClearVoiceArtifactsBody,
    storage: StorageDep,
) -> EngineCommandResponse:
    layout = ProjectLayout(storage.existing_project_dir(body.project_id))
    service = VoiceStudioProjectService(layout, project_id=body.project_id)
    if body.scope == "project_reset":
        report = execute_reset_project_tts_artifacts(layout)
        return EngineCommandResponse(
            status="accepted",
            message=(
                f"已清空 {report.removed_count} 个可再生配音文件，"
                f"保留配音团队、平台路由与声音资源库。"
            ),
        )
    if body.scope == "chapters":
        chapter_numbers = sorted({number for number in body.chapter_numbers if number >= 1})
        if not chapter_numbers:
            return EngineCommandResponse(status="rejected", message="请选择需要批量清理的章节。")
        removed_count = 0
        for chapter_number in chapter_numbers:
            removed_count += len(
                invalidate_chapter_tts_artifacts(
                    layout,
                    chapter_number,
                    include_script=True,
                )
            )
        chapter_label = "、".join(str(number) for number in chapter_numbers)
        return EngineCommandResponse(
            status="accepted",
            message=f"已清理第 {chapter_label} 章共 {removed_count} 组配音产物。",
        )
    if body.scope == "stale_files":
        categories = set(body.categories)
        if not categories:
            return EngineCommandResponse(status="rejected", message="请选择需要清理的文件类别。")
        report = execute_cleanup_tts_files(
            layout,
            categories=categories.intersection(ALL_CATEGORIES),
        )
        reclaimed_mib = report.reclaimed_bytes / (1024 * 1024)
        return EngineCommandResponse(
            status="accepted",
            message=(
                f"已清理 {report.removed_count} 个过期配音文件，释放 {reclaimed_mib:.1f} MiB。"
            ),
        )
    if body.chapter_number is None:
        return EngineCommandResponse(status="rejected", message="请选择需要处理的章节。")
    if body.scope == "redundant_takes":
        report = service.cleanup_redundant_takes(body.chapter_number)
        return EngineCommandResponse(
            status="accepted",
            message=f"已清理 {report.removed_files} 个冗余试听文件。",
        )
    if body.scope == "script_chapter":
        path = layout.tts_dubbing_script_path(body.chapter_number)
        removed = path.is_file()
        path.unlink(missing_ok=True)
        return EngineCommandResponse(
            status="accepted",
            message=(
                f"已清理第 {body.chapter_number} 章源配音脚本。"
                if removed
                else f"第 {body.chapter_number} 章没有可清理的源配音脚本。"
            ),
        )
    removed = invalidate_chapter_tts_artifacts(
        layout,
        body.chapter_number,
        include_script=True,
    )
    return EngineCommandResponse(
        status="accepted",
        message=f"已清理第 {body.chapter_number} 章 {len(removed)} 组配音产物。",
    )


@router.post("/commands/generate-sound-palette", response_model=EngineCommandResponse)
async def engine_generate_sound_palette(
    body: _GenerateSoundPaletteBody,
    runtime: RuntimeDep,
    storage: StorageDep,
) -> EngineCommandResponse:
    layout = ProjectLayout(storage.existing_project_dir(body.project_id))
    service = VoiceStudioProjectService(layout, project_id=body.project_id)
    try:
        assets = await SoundGenerationService(
            settings=runtime.settings,
            layout=layout,
        ).generate_project_palette(service.story_sound_context())
    except Exception as exc:
        return EngineCommandResponse(status="rejected", message=f"作品声音方案生成失败：{exc}")
    pending = sum(item.approval_status == "pending" for item in assets)
    return EngineCommandResponse(
        status="accepted",
        message=f"已生成 {len(assets)} 项作品声音候选，其中 {pending} 项待试听。",
    )


@router.post("/commands/update-sound-asset", response_model=EngineCommandResponse)
async def engine_update_sound_asset(
    body: _UpdateSoundAssetBody,
    storage: StorageDep,
) -> EngineCommandResponse:
    service = VoiceStudioProjectService(
        ProjectLayout(storage.existing_project_dir(body.project_id)),
        project_id=body.project_id,
    )
    try:
        if body.action == "publish":
            service.publish_sound_asset(body.asset_id)
            message = "声音资产已发布到应用共享资源库。"
        elif body.action == "set_tags":
            service.update_sound_asset_tags(body.asset_id, body.tags)
            message = "声音资产标签已保存。"
        elif body.action == "set_commercial_rights":
            if body.commercial_use_status is None:
                return EngineCommandResponse(status="rejected", message="请选择商用授权结论。")
            service.set_sound_asset_commercial_rights(
                body.asset_id,
                status=body.commercial_use_status,
                license_note=body.license_note,
            )
            message = "声音资产的商用授权结论已记录；重新装配可刷新交付门禁。"
        else:
            if body.status is None:
                return EngineCommandResponse(status="rejected", message="请选择资产状态。")
            service.set_sound_asset_status(body.asset_id, body.status)
            message = "声音资产状态已更新；重新装配可应用最新选择。"
    except (KeyError, ValueError, FileNotFoundError) as exc:
        return EngineCommandResponse(status="rejected", message=str(exc))
    return EngineCommandResponse(status="accepted", message=message)


@router.post("/commands/import-sound-assets", response_model=EngineCommandResponse)
async def engine_import_sound_assets(
    body: _ImportSoundAssetsBody,
    runtime: RuntimeDep,
    storage: StorageDep,
) -> EngineCommandResponse:
    layout = ProjectLayout(storage.existing_project_dir(body.project_id))
    service = VoiceStudioProjectService(
        layout,
        project_id=body.project_id,
        settings=runtime.settings,
    )
    total_bytes = 0
    with tempfile.TemporaryDirectory(prefix="nf_sound_import_") as tmp_dir:
        paths: list[Path] = []
        for ordinal, item in enumerate(body.files):
            try:
                payload = base64.b64decode(item.base64, validate=True)
            except (ValueError, binascii.Error):
                return EngineCommandResponse(
                    status="rejected",
                    message=f"文件 {item.name} 的上传内容无效。",
                )
            total_bytes += len(payload)
            if total_bytes > 128 * 1024 * 1024:
                return EngineCommandResponse(
                    status="rejected",
                    message="单次导入总大小不能超过 128 MiB。",
                )
            suffix = Path(item.name).suffix.lower()
            safe_name = re.sub(r"[^\w.-]+", "_", Path(item.name).stem).strip("_.")
            path = Path(tmp_dir) / f"{ordinal:02d}_{safe_name or 'sound'}{suffix}"
            path.write_bytes(payload)
            paths.append(path)
        result = service.import_sound_assets(
            paths,
            kind=body.asset_kind,
            tags=body.tags,
        )
    if not result.count:
        return EngineCommandResponse(status="rejected", message="未导入可用的音频文件。")
    return EngineCommandResponse(
        status="accepted",
        message=f"已导入 {result.count} 个声音资产；重新装配即可参与匹配。",
    )


@router.post("/commands/resolve-speakers", response_model=EngineCommandResponse)
async def engine_resolve_speakers(
    body: _ResolveSpeakersBody,
    storage: StorageDep,
) -> EngineCommandResponse:
    """Persist human speaker decisions before synthesis can resume."""
    execution = await execute_resolve_dubbing_speakers(
        project_id=body.project_id,
        chapter_number=body.chapter_number,
        resolutions=[item.model_dump(mode="json") for item in body.resolutions],
        layout=ProjectLayout(storage.project_path(body.project_id)),
    )
    error = execution.result.get("error")
    if error:
        return EngineCommandResponse(status="rejected", message=str(error))
    resolved_count = len(execution.result.get("resolved_segment_indices", []))
    remaining_count = len(execution.result.get("remaining_unresolved_segment_indices", []))
    return EngineCommandResponse(
        status="accepted",
        message=(
            f"已确认 {resolved_count} 段说话人"
            + (f"，仍有 {remaining_count} 段待复核" if remaining_count else "，本章复核已完成")
        ),
    )


@router.post("/commands/export-audio", response_model=EngineCommandResponse)
async def engine_export_audio(
    body: _ExportAudioBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    if body.scope == "book" and body.format != "zip":
        return EngineCommandResponse(status="rejected", message="全书导出仅支持 ZIP 格式。")
    if body.scope == "chapter" and body.chapter_number is None:
        return EngineCommandResponse(status="rejected", message="请选择需要导出的章节。")
    if body.scope == "chapter" and body.format not in {"mp3", "wav", "flac", "srt"}:
        return EngineCommandResponse(
            status="rejected", message=f"不支持的章节交付格式：{body.format}"
        )
    if body.scope not in {"chapter", "book"}:
        return EngineCommandResponse(status="rejected", message=f"不支持的导出范围：{body.scope}")
    record = service.submit(
        JobCommand(
            kind=JobKind.TTS_EXPORT_AUDIO,
            project_id=body.project_id,
            payload=body.model_dump(mode="json", exclude={"kind"}),
        )
    )
    return EngineCommandResponse(
        status="accepted",
        message="音频导出任务已提交；完成后可下载交付文件。",
        task_id=record.job_id,
    )


@router.post("/commands/export-audiobook", response_model=EngineCommandResponse)
async def engine_export_audiobook(
    body: _ExportAudiobookBody,
    service: JobServiceDep,
) -> EngineCommandResponse:
    """Submit finished-audiobook delivery to the shared durable job service."""

    record = service.submit(
        JobCommand(
            kind=JobKind.TTS_EXPORT_AUDIOBOOK,
            project_id=body.project_id,
            payload=body.model_dump(mode="json", exclude={"kind"}),
        )
    )
    return EngineCommandResponse(
        status="accepted",
        message="有声书包导出任务已提交；完成后可下载交付包。",
        task_id=record.job_id,
    )
