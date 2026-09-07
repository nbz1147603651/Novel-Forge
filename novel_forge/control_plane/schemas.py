"""Pydantic DTOs for control-plane records.

These are the serialization boundary between the async store and callers
(JobService, Desktop, CLI, pipeline stages). All datetime fields are
ISO-8601 UTC strings to match the existing ``utc_now_iso`` convention.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from novel_forge.control_plane.enums import (
    ArtifactKind,
    AssuranceLevel,
    EventSeverity,
    Priority,
    ResourceKind,
    RunAttemptState,
    StageState,
    WorkUnitState,
)


def utc_now_iso() -> str:
    """Current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid4().hex


class WorkUnitDTO(BaseModel):
    """User-intent work unit."""

    id: str = Field(default_factory=_new_id)
    kind: str
    project_id: str = ""
    priority: Priority = Priority.P1
    intent_payload_path: str = ""
    state: WorkUnitState = WorkUnitState.QUEUED
    assurance: AssuranceLevel = AssuranceLevel.NORMAL
    idempotency_key: str = ""
    label: str = ""
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    heartbeat_at: str = ""


class RunAttemptDTO(BaseModel):
    """One actual execution attempt of a WorkUnit."""

    id: str = Field(default_factory=_new_id)
    work_unit_id: str
    attempt_number: int = 1
    runtime_config_version: str = ""
    state: RunAttemptState = RunAttemptState.RUNNING
    error_kind: str = ""
    error_summary: dict[str, Any] = Field(default_factory=dict)
    started_at: str = Field(default_factory=utc_now_iso)
    ended_at: str = ""
    heartbeat_at: str = ""


class StageExecutionDTO(BaseModel):
    """Per-stage execution record."""

    id: str = Field(default_factory=_new_id)
    run_attempt_id: str
    stage_name: str
    task_type: str = ""
    input_artifact_hashes: dict[str, str] = Field(default_factory=dict)
    idempotency_key: str = ""
    retry_budget: int = 2
    retries_used: int = 0
    output_artifact_hash: str = ""
    committed_version: int = 0
    state: StageState = StageState.RUNNING
    heartbeat_at: str = ""
    started_at: str = Field(default_factory=utc_now_iso)
    ended_at: str = ""


class EventLedgerEntryDTO(BaseModel):
    """Append-only immutable event."""

    id: str = Field(default_factory=_new_id)
    run_attempt_id: str | None = None
    stage_execution_id: str | None = None
    event_type: str
    severity: EventSeverity = EventSeverity.INFO
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)


class ArtifactManifestDTO(BaseModel):
    """Immutable content-addressed artifact metadata."""

    id: str = Field(default_factory=_new_id)
    sha256: str
    artifact_kind: ArtifactKind
    project_id: str = ""
    scope: str = "chapter"
    content_path: str = ""
    content_size: int = 0
    mime_type: str = ""
    source_artifact_hashes: dict[str, str] = Field(default_factory=dict)
    parent_artifact_id: str = ""
    parent_artifact_refs: list[str] = Field(default_factory=list)
    source_text_hash: str = ""
    input_signature: str = "legacy_unknown"
    schema_version: int = Field(default=1, ge=1)
    workflow_version: str = "legacy_unknown"
    config_fingerprint: str = "legacy_unknown"
    model_fingerprint: str = "legacy_unknown"
    quality_status: Literal["actual", "degraded", "fallback", "blocked", "legacy_unknown"] = (
        "actual"
    )
    degradation_reason: str = ""
    derivation_status: Literal["fresh", "stale", "conflict", "blocked", "legacy_unknown"] = "fresh"
    output_version: int = Field(default=1, ge=0)
    prompt_hash: str = ""
    template_version: str = ""
    task_type: str = ""
    route: str = ""
    model_params: dict[str, Any] = Field(default_factory=dict)
    response_hash: str = ""
    validation_result: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)
    created_by_stage_execution_id: str | None = None


class ResourceVersionDTO(BaseModel):
    """A committed version of a project resource."""

    id: str = Field(default_factory=_new_id)
    work_unit_id: str
    resource_kind: ResourceKind
    project_id: str = ""
    resource_ref: str = ""
    version_number: int = 1
    committed_artifact_id: str | None = None
    committed_at: str = Field(default_factory=utc_now_iso)
