"""Versioned, read-only projections for replaceable UI clients.

These models deliberately sit beside ``JobService`` instead of inside a UI or
transport package.  PySide can keep consuming the in-process application
contracts, while HTTP/Tauri/cloud adapters consume the same application state
through stable read models.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from novel_forge.app_service.contracts import (
    VOICE_DURABLE_JOB_KINDS,
    JobRecord,
    JobScope,
    JobState,
    JobStepEvent,
)
from novel_forge.app_service.error_diagnostics import prompt_task_from_error_payload
from novel_forge.app_service.workflow_projection import (
    project_workflow_progress,
    project_workflow_stages,
    project_workflow_step_label,
)
from novel_forge.common.runtime_identity import engine_revision_status
from novel_forge.core.task_catalog import ROUTING_GROUPS

ENGINE_CONTRACT_VERSION = "1.0"
ENGINE_API_VERSION = "v1"

EngineTaskState = Literal["queued", "running", "paused", "succeeded", "failed"]
EngineStreamState = Literal["streaming", "paused", "completed", "failed"]
EngineStreamEventKind = Literal[
    "stream_start",
    "delta",
    "restart",
    "stream_end",
    "stream_error",
    "validation",
]
EngineStreamSegmentKind = Literal["content", "reasoning", "system"]
EngineTaskModelCallStatus = Literal["running", "success", "retrying", "error"]

_TASK_LABEL_BY_KEY = {task.key: task.label for group in ROUTING_GROUPS for task in group.tasks}


def project_running_operation_detail(record: JobRecord) -> str:
    """Project the latest model/validation event into one honest live sentence."""
    candidates = [
        event
        for event in [*record.events, *record.stream_results.values()]
        if event.step
        in {
            "llm_stream_start",
            "llm_stream_delta",
            "llm_stream_end",
            "llm_stream_error",
            "llm_stream_validation",
        }
    ]
    if not candidates:
        return ""
    event = max(enumerate(candidates), key=lambda item: (item[1].at, item[0]))[1]
    payload = event.payload
    task_key = str(payload.get("task") or "").strip()
    task_label = _TASK_LABEL_BY_KEY.get(task_key, task_key or "模型任务")
    attempt = _optional_int(payload.get("attempt"))
    max_attempts = _optional_int(payload.get("max_attempts"))

    def _attempt_suffix(*, next_attempt: bool = False) -> str:
        if attempt is None or max_attempts is None:
            return ""
        value = min(attempt + 1, max_attempts) if next_attempt else attempt
        return f"（第 {value}/{max_attempts} 次）"

    if event.step in {"llm_stream_start", "llm_stream_delta"}:
        return f"正在调用模型生成{task_label}{_attempt_suffix()}"
    if event.step == "llm_stream_end":
        return f"{task_label}已返回，正在准备结构校验{_attempt_suffix()}"
    if event.step == "llm_stream_error":
        return f"{task_label}调用异常，正在执行恢复策略{_attempt_suffix()}"

    status = str(payload.get("validation_status") or "").strip()
    if status == "validating":
        return f"{task_label}已返回，正在校验结构{_attempt_suffix()}"
    if status == "repairing":
        source = str(payload.get("repair_source") or "").strip()
        repair_label = "本地修正" if source == "local" else "结构修复"
        return f"正在{repair_label}{task_label}格式{_attempt_suffix()}"
    if status == "retrying":
        if str(payload.get("finish_reason") or "").strip() == "length":
            return f"{task_label}达到输出上限，正在扩容重试{_attempt_suffix(next_attempt=True)}"
        return f"{task_label}结构未通过，正在自动重试{_attempt_suffix(next_attempt=True)}"
    if status == "validated":
        return f"{task_label}已通过结构校验，正在本地组装与审计"
    if status == "failed":
        return f"{task_label}未通过校验，自动恢复已停止"
    return ""


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


class EngineViewModel(BaseModel):
    """Base for OpenAPI-generated clients and hand-written transport adapters."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)


class EngineCapabilitiesView(EngineViewModel):
    contract_version: str = ENGINE_CONTRACT_VERSION
    api_version: str = ENGINE_API_VERSION
    transports: list[str] = Field(default_factory=lambda: ["in_process", "http"])
    features: dict[str, bool] = Field(
        default_factory=lambda: {
            "job_queries": True,
            "task_stream_snapshots": True,
            "task_stream_subscription": True,
            "engine_commands": True,
            "runtime_status": True,
            "authoring_policy_v1": True,
            "authoring_coauthor": True,
            "planning_horizon_jobs": True,
            # Repair publication is enabled only for a registered content
            # publisher.  The first publisher delegates one approved chapter
            # candidate to the existing versioned manual-revision transaction;
            # whole-book batches and working drafts remain non-publishable.
            "repair_workbench_v1": True,
            "repair_publish_v1": True,
        }
    )
    commands: dict[str, bool] = Field(
        default_factory=lambda: {
            "prepare_chapter": True,
            "resolve_chapter_checkpoint": True,
            "polish_chapter": True,
            "repair_continuity": True,
            "repair_causal": True,
            "repair_issues": True,
            "reevaluate_chapter": True,
            "reextract_relationships": True,
            "repair_motif_history": True,
            "polish_outline": True,
            "audit_book": True,
            "audit_book_editorial": True,
            "execute_global_repair_queue": True,
            "export_book": True,
            "clean_chapters": True,
            "cancel_job": True,
            "resume_job": True,
            "retry_init_repair": True,
            "save_init_manual_repair": True,
            "save_narrative_character": True,
            "retire_narrative_character": True,
            "save_narrative_relationship": True,
            "remove_narrative_relationship": True,
            "save_humanize_pattern": True,
            "set_humanize_pattern_enabled": True,
            "remove_humanize_pattern": True,
            "merge_humanize_patterns": True,
            "rebuild_memory_vectors": True,
            "clear_job_history": True,
            "acknowledge_task_errors": True,
            "reopen_task_errors": True,
            "clear_closed_task_errors": True,
            "clear_error_archive": True,
            "continue_long_init": True,
            "restart_long_init": True,
            "start_workflow": True,
            "save_chapter_revision": True,
            "save_token_dashboard_preferences": True,
            "save_settings": True,
            "delete_projects": True,
            "test_model_profile": True,
            "generate_workflow_fields": True,
            "synthesize_voice": True,
            "build_voice_team": True,
            "confirm_voice_team": True,
            "clone_character_voice": True,
            "design_character_voice": True,
            "approve_character_voice": True,
            "preview_character_voice": True,
            "update_voice_performance": True,
            "assign_catalog_voice": True,
            "generate_voice_script": True,
            "save_voice_script": True,
            "preview_voice_segment": True,
            "accept_voice_take": True,
            "reassemble_voice": True,
            "resolve_speakers": True,
            "export_audio": True,
            "export_audiobook": True,
            "ollama_management": True,
        }
    )
    modules: list["EngineModuleCapabilityView"] = Field(default_factory=list)


class EngineModuleCapabilityView(EngineViewModel):
    id: Literal["novel", "voice", "ollama"]
    read_model: bool = True
    durable_job_kinds: list[str] = Field(default_factory=list)
    in_process_only_operations: list[str] = Field(default_factory=list)


class EngineJobErrorView(EngineViewModel):
    code: str = ""
    category: str = ""
    message: str
    failed_step: str = ""
    retryable: bool = False
    recovery_actions: list[dict[str, Any]] = Field(default_factory=list)


class EngineJobView(EngineViewModel):
    id: str
    kind: str
    label: str
    project_id: str = ""
    scope: Literal["project", "system"] = "project"
    state: EngineTaskState
    current_step: str = ""
    step_label: str = ""
    progress_percent: int = 0
    detail: str = ""
    cumulative_tokens: int = 0
    cumulative_cost_usd: float = 0.0
    error: EngineJobErrorView | None = None
    decisions: list[dict[str, Any]] = Field(default_factory=list)


class EngineJobsView(EngineViewModel):
    contract_version: str = ENGINE_CONTRACT_VERSION
    jobs: list[EngineJobView] = Field(default_factory=list)


class OllamaRuntimeStatusView(EngineViewModel):
    status: Literal[
        "disabled",
        "starting",
        "healthy",
        "degraded",
        "stopped",
        "missing_binary",
        "failed",
        "external",
    ]
    version: str = ""
    detail: str = ""


class OllamaSidecarView(EngineViewModel):
    enabled: bool
    auto_start: bool
    prefer_local: bool
    binary_available: bool


class OllamaStorageView(EngineViewModel):
    scope: Literal["engine_managed", "engine_local", "external"]
    display_label: str
    path_reveal_capability: bool = False


class OllamaCapabilitiesView(EngineViewModel):
    can_ensure: bool
    can_restart: bool
    can_stop: bool
    can_pull: bool
    can_delete: bool
    can_configure_paths: bool


class OllamaConfiguredRolesView(EngineViewModel):
    generation_model: str = ""
    embedding_model: str = ""
    managed_profile_ids: list[str] = Field(default_factory=list)


class OllamaModelView(EngineViewModel):
    name: str
    size: int = Field(ge=0)
    modified_at: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    roles: list[Literal["generation", "embedding", "managed"]] = Field(default_factory=list)


class OllamaRoutingImpactView(EngineViewModel):
    profile_ids: list[str] = Field(default_factory=list)
    primary_route_ids: list[str] = Field(default_factory=list)
    fallback_route_ids: list[str] = Field(default_factory=list)
    generation_selected: bool = False
    embedding_selected: bool = False
    confirmation_token: str = ""


class OllamaOperationView(EngineViewModel):
    task_id: str
    kind: str
    state: EngineTaskState
    model: str = ""
    operation: str = ""


class OllamaManagerView(EngineViewModel):
    contract_version: str = ENGINE_CONTRACT_VERSION
    revision: str
    endpoint_scope: Literal["engine_host", "external_host"]
    ownership: Literal["engine_owned", "external", "unavailable"]
    runtime: OllamaRuntimeStatusView
    sidecar: OllamaSidecarView
    storage: OllamaStorageView
    capabilities: OllamaCapabilitiesView
    models: list[OllamaModelView] = Field(default_factory=list)
    configured_roles: OllamaConfiguredRolesView
    routing_impact_by_model: dict[str, OllamaRoutingImpactView] = Field(default_factory=dict)
    active_operations: list[OllamaOperationView] = Field(default_factory=list)


class EngineRuntimeView(EngineViewModel):
    """Credential-free local Engine identity for the desktop launch boundary."""

    contract_version: str = ENGINE_CONTRACT_VERSION
    status: Literal["ready", "restartRequired", "unsupported"]
    boot_revision: str
    current_revision: str
    instance_id: str
    managed_by: str
    started_at: str
    active_job_count: int = Field(ge=0)
    queued_job_count: int = Field(ge=0)
    can_submit_tasks: bool


class InitManualRepairLocationView(EngineViewModel):
    pointer: str = ""
    label: str
    confidence: str = "weak"
    excerpt: str = ""


class InitManualRepairIssueView(EngineViewModel):
    title: str
    summary: str
    locations: list[InitManualRepairLocationView] = Field(default_factory=list)


class InitManualRepairView(EngineViewModel):
    project_id: str
    available: bool
    artifact: str = ""
    artifact_label: str = ""
    artifact_path: str = ""
    payload: dict[str, Any] | None = None
    revision: str = ""
    summary: str = ""
    issues: list[InitManualRepairIssueView] = Field(default_factory=list)


class InitManualRepairSaveResult(EngineViewModel):
    status: Literal["saved", "conflict", "rejected"]
    message: str
    repair: InitManualRepairView | None = None


class EngineTaskStreamEventView(EngineViewModel):
    cursor: str
    stream_id: str
    sequence: int = Field(ge=1)
    kind: EngineStreamEventKind
    segment: EngineStreamSegmentKind
    at: str
    model_task_id: str = ""
    attempt: int | None = None
    text: str | None = None
    message: str | None = None
    # Engine-declared output contract (text | json | report) carried on every
    # delta event so readers pick the renderer without guessing from partial
    # text. Mirrors TaskStreamEvent.outputKind in engine-contracts.
    output_kind: str | None = None

    text_mode: Literal["delta", "snapshot"] | None = None
    text_length: int | None = None
    text_truncated: bool | None = None
    operation_id: str | None = None
    validation_status: (
        Literal["validating", "repairing", "retrying", "validated", "failed"] | None
    ) = None
    repair_source: str | None = None
    finish_reason: str | None = None
    model_id: str | None = None
    structured_output_mode: str | None = None
    max_attempts: int | None = None


class EngineTaskStreamRuntimeSummaryView(EngineViewModel):
    output_kind: str | None = None
    attempt: int | None = None
    output_characters: int | None = None
    elapsed_ms: float | None = None
    provider: str | None = None
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None


class EngineTaskDeliveryView(EngineViewModel):
    """A bounded downloadable artifact exposed by a completed delivery job."""

    filename: str
    download_url: str


class EngineTaskModelCallView(EngineViewModel):
    call_id: str
    task: str = ""
    task_label: str = "模型调用"
    provider: str | None = None
    model: str | None = None
    route: str | None = None
    status: EngineTaskModelCallStatus = "running"
    event: str = ""
    attempt: int | None = None
    max_attempts: int | None = None
    max_tokens: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: float | None = None
    cost_usd: float | None = None
    finish_reason: str | None = None
    will_retry: bool | None = None
    started_at: str | None = None
    finished_at: str | None = None


class EngineTaskStreamView(EngineViewModel):
    contract_version: str = ENGINE_CONTRACT_VERSION
    task_id: str
    title: str
    step_id: str = ""
    step_label: str = ""
    status: EngineStreamState
    progress_percent: int = 0
    job_state: EngineTaskState = "running"
    error: EngineJobErrorView | None = None
    delivery: EngineTaskDeliveryView | None = None
    summary: EngineTaskStreamRuntimeSummaryView | None = None
    calls: list[EngineTaskModelCallView] = Field(default_factory=list)
    events: list[EngineTaskStreamEventView] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False


def engine_capabilities() -> EngineCapabilitiesView:
    """Describe only the Engine API implemented today.

    Task-stream subscription uses the durable job SSE channel for wake-ups and
    the cursor-bearing Engine snapshot as the canonical event projection.
    """

    return EngineCapabilitiesView(
        modules=[
            EngineModuleCapabilityView(
                id="novel",
                durable_job_kinds=sorted(_NOVEL_JOB_KINDS),
                in_process_only_operations=["manual_revision"],
            ),
            EngineModuleCapabilityView(
                id="voice",
                durable_job_kinds=sorted(VOICE_DURABLE_JOB_KINDS),
                in_process_only_operations=[
                    "build_narrator_profile",
                    "prepare_voice_previews",
                    "preview_voice",
                    "preview_narrator",
                    "clone_voice",
                    "design_voice",
                    "approve_voice",
                    "segment_synthesis",
                    "accept_segment_take",
                    "audio_assembly",
                    "generate_sound_palette",
                    "list_voices",
                    "cleanup_audio",
                    "audio_model_management",
                ],
            ),
            EngineModuleCapabilityView(
                id="ollama",
                durable_job_kinds=[
                    "ollama_delete_model",
                    "ollama_pull_model",
                    "ollama_runtime_control",
                ],
                in_process_only_operations=[],
            ),
        ]
    )


def project_job_view(record: JobRecord) -> EngineJobView:
    """Project internal job state without leaking Desktop or workspace types."""

    state = _job_state(record)
    error = _project_error(record)
    kind = _enum_value(record.kind)
    stages = project_workflow_stages(record)
    pending = (
        record.pending_decision if record.status in {JobState.RUNNING, JobState.PAUSED} else {}
    )
    decisions = [
        {
            "id": option["id"],
            "label": option.get("label", option["id"]),
            "description": pending.get("message", ""),
            "decision_id": pending.get("decision_id", ""),
            "approval_version": pending.get("approval_version", ""),
            "requires_explicit_approval": bool(pending.get("requires_explicit_approval")),
        }
        for option in pending.get("options", [])
        if isinstance(option, dict) and option.get("id")
    ]
    return EngineJobView(
        id=record.job_id,
        kind=kind,
        label=record.label,
        project_id=record.project_id,
        scope=(record.scope.value if isinstance(record.scope, JobScope) else str(record.scope)),
        state="paused" if decisions else state,
        current_step=record.current_step,
        step_label=project_workflow_step_label(record, stages),
        progress_percent=_progress_percent(record, state=state, stages=stages),
        detail=_job_detail(record, error=error),
        cumulative_tokens=record.cumulative_tokens,
        cumulative_cost_usd=record.cumulative_cost_usd,
        error=error,
        decisions=decisions,
    )


def project_jobs_view(records: list[JobRecord]) -> EngineJobsView:
    return EngineJobsView(jobs=[project_job_view(record) for record in records])


def engine_runtime_view(
    *,
    started_at: float,
    active_job_count: int,
    queued_job_count: int,
) -> EngineRuntimeView:
    """Project runtime facts without leaking configuration or storage details."""

    revision = engine_revision_status()
    return EngineRuntimeView(
        status="restartRequired" if revision.restart_required else "ready",
        boot_revision=revision.boot_revision,
        current_revision=revision.current_revision,
        instance_id=revision.instance_id,
        managed_by=revision.managed_by,
        started_at=datetime.fromtimestamp(started_at, timezone.utc).isoformat(),
        active_job_count=active_job_count,
        queued_job_count=queued_job_count,
        can_submit_tasks=not revision.restart_required,
    )


def project_task_stream_view(
    record: JobRecord,
    *,
    after_cursor: str | None = None,
    limit: int = 240,
) -> EngineTaskStreamView:
    """Project durable observation events into a bounded, cursor-aware page."""

    projected_events: list[EngineTaskStreamEventView] = []
    sequence = 0
    latest_stream_payload: dict[str, Any] = {}
    latest_model_call_payload: dict[str, Any] = {}

    history = {
        (event.at, event.step, str(event.payload.get("stream_id") or "")): event
        for event in [*record.events, *record.stream_results.values()]
    }
    for event in sorted(history.values(), key=lambda item: item.at):
        if event.step == "model_call_update":
            latest_model_call_payload = dict(event.payload)
        if event.step not in _STREAM_STEP_KIND:
            continue
        latest_stream_payload = dict(event.payload)
        for item in _project_stream_event(record.job_id, event):
            sequence += 1
            projected_events.append(item.model_copy(update={"sequence": sequence}))

    limit = max(1, min(int(limit), 500))
    if after_cursor:
        cursor_index = next(
            (index for index, event in enumerate(projected_events) if event.cursor == after_cursor),
            None,
        )
        candidates = (
            projected_events[cursor_index + 1 :]
            if cursor_index is not None
            else projected_events[-limit:]
        )
        page = candidates[:limit]
        has_more = cursor_index is not None and len(candidates) > len(page)
    else:
        # First paint favours the newest context over an unbounded historical replay.
        page = projected_events[-limit:]
        has_more = False

    status = _stream_status(record, projected_events)
    summary = _stream_summary(
        record,
        stream_payload=latest_stream_payload,
        model_call_payload=latest_model_call_payload,
    )
    stages = project_workflow_stages(record)
    return EngineTaskStreamView(
        task_id=record.job_id,
        title=record.label,
        step_id=record.current_step,
        step_label=project_workflow_step_label(record, stages),
        status=status,
        progress_percent=_progress_percent(record, state=_job_state(record), stages=stages),
        job_state=_job_state(record),
        error=_project_error(record),
        delivery=_project_delivery(record),
        summary=summary,
        calls=_task_model_call_views(record),
        events=page,
        next_cursor=page[-1].cursor if page else after_cursor,
        has_more=has_more,
    )


def _project_delivery(record: JobRecord) -> EngineTaskDeliveryView | None:
    """Expose only Engine-owned download links, never an arbitrary job result."""

    if _job_state(record) != "succeeded":
        return None
    if _enum_value(record.kind) not in {"tts_export_audio", "tts_export_audiobook"}:
        return None
    filename = str(record.result.get("export_filename") or "").strip()
    if not filename or Path(filename).name != filename:
        return None
    return EngineTaskDeliveryView(
        filename=filename,
        download_url=(
            f"/api/v1/engine/voice/projects/{quote(record.project_id, safe='')}"
            f"/exports/{quote(filename, safe='')}"
        ),
    )


_NOVEL_JOB_KINDS = {
    "run_short",
    "init_long",
    "init_repair_retry",
    "run_chapter",
    "prepare_chapter",
    "resolve_chapter_checkpoint",
    "resolve_chapter_checkpoint_finalize",
    "repair_continuity",
    "repair_causal",
    "repair_issues",
    "reevaluate_chapter",
    "polish_chapter",
    "book_consistency",
    "book_editorial_audit",
    "global_repair_queue",
    "export_book",
    "reextract_relationships",
    "repair_motif_history",
    "rebuild_memory_vectors",
    "polish_outline",
    "sync_chapter_contracts",
    "extend_outline",
    "planning_horizon",
}

_STREAM_STEP_KIND: dict[str, EngineStreamEventKind] = {
    "llm_stream_start": "stream_start",
    "llm_stream_delta": "delta",
    "llm_stream_restart": "restart",
    "llm_stream_end": "stream_end",
    "llm_stream_error": "stream_error",
    "llm_stream_validation": "validation",
}


def _project_stream_event(job_id: str, event: JobStepEvent) -> list[EngineTaskStreamEventView]:
    payload = event.payload
    kind = _STREAM_STEP_KIND[event.step]
    stream_id = str(payload.get("stream_id") or f"{job_id}:observation").strip()
    task_id = str(payload.get("task") or "").strip()
    attempt = _optional_int(payload.get("attempt"))

    segments: list[tuple[EngineStreamSegmentKind, str | None, str | None]] = []
    if kind == "delta":
        raw_segments = payload.get("segments")
        if isinstance(raw_segments, list):
            for raw_segment in raw_segments:
                if not isinstance(raw_segment, dict):
                    continue
                segment_kind = str(raw_segment.get("kind") or "content").strip()
                normalized_kind: EngineStreamSegmentKind = (
                    "reasoning" if segment_kind == "reasoning" else "content"
                )
                text = str(raw_segment.get("text") or "")
                if text:
                    segments.append((normalized_kind, text, None))
        if not segments:
            delta = str(payload.get("delta") or "")
            if delta:
                segments.append(("content", delta, None))
            else:
                full_text = str(payload.get("text") or "")
                if full_text:
                    segments.append(("content", full_text, None))
    else:
        system_message = ""
        if kind == "restart":
            system_message = str(payload.get("message") or payload.get("error") or "").strip()
        elif kind == "stream_error":
            system_message = str(payload.get("error") or payload.get("message") or "").strip()
        elif kind == "validation":
            system_message = str(payload.get("error") or "").strip()
        # Bounded history/reconnect may lose deltas. A replacement snapshot
        # restores the response without appending it to the existing text.
        if kind in {"stream_end", "stream_error", "validation"}:
            for segment_kind, key in (("content", "text"), ("reasoning", "reasoning")):
                if isinstance(payload.get(key), str):
                    segments.append(
                        (cast(EngineStreamSegmentKind, segment_kind), payload[key], None)
                    )
        segments.append(("system", None, system_message or None))

    projected: list[EngineTaskStreamEventView] = []
    for segment_index, (segment, segment_text, event_message) in enumerate(segments):
        snapshot = segment != "system" and kind != "delta"
        length_key = "reasoning_length" if segment == "reasoning" else "text_length"
        text_length = _optional_int(payload.get(length_key)) if snapshot else None
        validation_status = payload.get("validation_status")
        if not isinstance(validation_status, str) or validation_status not in {
            "validating",
            "repairing",
            "retrying",
            "validated",
            "failed",
        }:
            validation_status = None
        projected.append(
            EngineTaskStreamEventView(
                cursor=_event_cursor(event, segment_index=segment_index),
                stream_id=stream_id,
                sequence=1,
                kind=kind,
                segment=segment,
                at=event.at,
                model_task_id=task_id,
                attempt=attempt,
                text=segment_text,
                message=event_message,
                output_kind=_optional_text(payload.get("output_kind")),
                text_mode="snapshot" if snapshot else ("delta" if kind == "delta" else None),
                text_length=text_length,
                text_truncated=(
                    bool(
                        payload.get(
                            "reasoning_truncated" if segment == "reasoning" else "text_truncated"
                        )
                    )
                    or (text_length > len(segment_text or "") if text_length is not None else False)
                    if snapshot
                    else None
                ),
                operation_id=_optional_text(payload.get("operation_id")),
                validation_status=validation_status,
                repair_source=_optional_text(payload.get("repair_source")),
                finish_reason=_optional_text(payload.get("finish_reason")),
                model_id=_optional_text(payload.get("model_id")),
                structured_output_mode=_optional_text(payload.get("structured_output_mode")),
                max_attempts=_optional_int(payload.get("max_attempts")),
            )
        )
    return projected


def _event_cursor(event: JobStepEvent, *, segment_index: int) -> str:
    payload = json.dumps(event.payload, ensure_ascii=False, sort_keys=True, default=str)
    raw = f"{event.at}|{event.step}|{segment_index}|{payload}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _job_state(record: JobRecord) -> EngineTaskState:
    value = _enum_value(record.status)
    if value in {"queued", "running", "paused", "succeeded", "failed"}:
        return cast(EngineTaskState, value)
    return "failed"


def _stream_status(
    record: JobRecord,
    events: list[EngineTaskStreamEventView],
) -> EngineStreamState:
    # A single sibling ending must not settle other concurrent streams.
    job_state = _enum_value(record.status)
    if job_state in {"failed", "paused", "succeeded"}:
        return cast(
            EngineStreamState,
            {"failed": "failed", "paused": "paused", "succeeded": "completed"}[job_state],
        )
    if events:
        latest: dict[str, EngineTaskStreamEventView] = {}
        operation_streams: dict[str, str] = {}
        for event in events:
            latest[event.stream_id] = event
            if event.operation_id:
                operation_streams[event.operation_id] = event.stream_id
        active = [
            event
            for event in latest.values()
            if not event.operation_id
            or operation_streams.get(event.operation_id) == event.stream_id
        ]
        if any(
            event.validation_status in {"validating", "repairing", "retrying"}
            or event.kind in {"stream_start", "delta", "restart"}
            for event in active
        ):
            return "streaming"
        if any(
            event.validation_status == "failed"
            or (event.kind == "stream_error" and event.validation_status != "validated")
            for event in active
        ):
            return "failed"
        return "completed"
    status = {
        "paused": "paused",
        "succeeded": "completed",
        "failed": "failed",
    }.get(_enum_value(record.status), "streaming")
    return cast(EngineStreamState, status)


def _project_error(record: JobRecord) -> EngineJobErrorView | None:
    summary = record.error_summary
    if not record.error and not summary:
        return None
    context = summary.get("context")
    context_mapping = context if isinstance(context, dict) else {}
    failed_step = (
        prompt_task_from_error_payload(summary)
        or str(context_mapping.get("failed_stage") or "").strip()
        or record.current_step
    )
    raw_actions = summary.get("recovery_actions")
    actions = (
        [dict(item) for item in raw_actions if isinstance(item, dict)]
        if isinstance(raw_actions, list)
        else []
    )
    return EngineJobErrorView(
        code=str(summary.get("error_code") or summary.get("code") or "job_failed").strip(),
        category=str(summary.get("category") or "").strip(),
        message=str(summary.get("summary") or summary.get("title") or record.error or "").strip(),
        failed_step=failed_step,
        retryable=bool(summary.get("retryable", False)),
        recovery_actions=actions,
    )


def _progress_percent(
    record: JobRecord,
    *,
    state: EngineTaskState,
    stages: list[dict[str, str]],
) -> int:
    # Use the same pure event projection as task cards and chapter timelines.
    # Explicit provider percentages are only a fallback for tasks without a
    # semantic workflow (downloads, audio synthesis, legacy snapshots, etc.).
    kind = _enum_value(record.kind)
    if kind not in VOICE_DURABLE_JOB_KINDS and (record.current_step or record.events):
        if stages:
            return project_workflow_progress(record, stages)
    if state == "queued":
        return 0
    if state == "succeeded":
        return 100
    for source in (record.current_step_payload, record.result):
        for key in ("progress_percent", "progress", "percent"):
            if key not in source:
                continue
            try:
                value = float(source[key])
            except (TypeError, ValueError):
                continue
            if 0.0 <= value <= 1.0:
                value *= 100.0
            return max(0, min(100, round(value)))
    return 0


def _job_detail(record: JobRecord, *, error: EngineJobErrorView | None) -> str:
    if error is not None:
        return error.message
    for source in (record.current_step_payload, record.result):
        for key in ("summary", "message", "detail", "status"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return record.current_step


def _stream_summary(
    record: JobRecord,
    *,
    stream_payload: dict[str, Any],
    model_call_payload: dict[str, Any],
) -> EngineTaskStreamRuntimeSummaryView | None:
    if not stream_payload and not model_call_payload and not record.cumulative_tokens:
        return None
    return EngineTaskStreamRuntimeSummaryView(
        output_kind=_optional_text(stream_payload.get("output_kind")),
        attempt=_optional_int(stream_payload.get("attempt")),
        output_characters=_optional_int(
            stream_payload.get("chars") or stream_payload.get("text_length")
        ),
        elapsed_ms=_optional_float(model_call_payload.get("latency_ms")),
        provider=_optional_text(model_call_payload.get("provider")),
        model=_optional_text(model_call_payload.get("model")),
        prompt_tokens=_optional_int(model_call_payload.get("prompt_tokens")),
        completion_tokens=_optional_int(model_call_payload.get("completion_tokens")),
        total_tokens=_optional_int(
            model_call_payload.get("total_tokens") or record.cumulative_tokens
        ),
        cost_usd=_optional_float(model_call_payload.get("cost_usd") or record.cumulative_cost_usd),
    )


def _task_model_call_views(
    record: JobRecord,
    *,
    limit: int = 120,
) -> list[EngineTaskModelCallView]:
    """Collapse live router lifecycle events into safe logical-call rows."""

    calls: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for step_event in record.events:
        if step_event.step != "model_call_update":
            continue
        payload = step_event.payload
        call_id = _optional_text(payload.get("call_id"))
        if call_id is None:
            continue
        current = calls.get(call_id)
        if current is None:
            current = {
                "call_id": call_id,
                "status": "running",
                "event": "",
                "started_at": step_event.at,
            }
            calls[call_id] = current
            order.append(call_id)

        event_name = _optional_text(payload.get("event")) or ""
        current["event"] = event_name
        current["status"] = _task_model_call_status(payload, event_name=event_name)

        for source, target in (
            ("task", "task"),
            ("provider", "provider"),
            ("model", "model"),
            ("route", "route"),
            ("finish_reason", "finish_reason"),
        ):
            value = _optional_text(payload.get(source))
            if value is not None:
                current[target] = value
        for source, target in (
            ("attempt", "attempt"),
            ("max_attempts", "max_attempts"),
            ("max_tokens", "max_tokens"),
            ("prompt_tokens", "prompt_tokens"),
            ("completion_tokens", "completion_tokens"),
            ("total_tokens", "total_tokens"),
        ):
            value = _optional_int(payload.get(source))
            if value is not None:
                current[target] = value
        for source, target in (("latency_ms", "latency_ms"), ("cost_usd", "cost_usd")):
            value = _optional_float(payload.get(source))
            if value is not None:
                current[target] = value
        if "will_retry" in payload:
            current["will_retry"] = bool(payload.get("will_retry"))
        if event_name.endswith("_start"):
            current["started_at"] = current.get("started_at") or step_event.at
        else:
            current["finished_at"] = step_event.at

    result: list[EngineTaskModelCallView] = []
    for call_id in order[-max(1, min(limit, 240)) :]:
        payload = dict(calls[call_id])
        task = str(payload.get("task") or "").strip()
        payload["task_label"] = _TASK_LABEL_BY_KEY.get(task.lower(), task or "模型调用")
        if payload.get("total_tokens") is None:
            prompt_tokens = int(payload.get("prompt_tokens") or 0)
            completion_tokens = int(payload.get("completion_tokens") or 0)
            if prompt_tokens or completion_tokens:
                payload["total_tokens"] = prompt_tokens + completion_tokens
        result.append(EngineTaskModelCallView(**payload))
    return result


def _task_model_call_status(
    payload: dict[str, Any],
    *,
    event_name: str,
) -> EngineTaskModelCallStatus:
    raw = str(payload.get("status") or "").strip().lower()
    if bool(payload.get("will_retry")):
        return "retrying"
    if raw in {"running", "success", "retrying", "error"}:
        return cast(EngineTaskModelCallStatus, raw)
    if event_name.endswith("_start"):
        return "running"
    if event_name.endswith("_done"):
        return "success"
    return "error"


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


__all__ = [
    "ENGINE_API_VERSION",
    "ENGINE_CONTRACT_VERSION",
    "EngineCapabilitiesView",
    "EngineJobErrorView",
    "EngineJobsView",
    "EngineRuntimeView",
    "EngineJobView",
    "EngineModuleCapabilityView",
    "OllamaCapabilitiesView",
    "OllamaConfiguredRolesView",
    "OllamaManagerView",
    "OllamaModelView",
    "OllamaOperationView",
    "OllamaRoutingImpactView",
    "OllamaRuntimeStatusView",
    "OllamaSidecarView",
    "OllamaStorageView",
    "EngineTaskModelCallView",
    "EngineTaskStreamEventView",
    "EngineTaskStreamRuntimeSummaryView",
    "EngineTaskStreamView",
    "EngineViewModel",
    "engine_capabilities",
    "engine_runtime_view",
    "project_job_view",
    "project_jobs_view",
    "project_task_stream_view",
]
