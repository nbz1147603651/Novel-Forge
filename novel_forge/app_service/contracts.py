"""Serializable application-service contracts for UI-facing jobs."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobKind(str, Enum):
    RUN_SHORT = "run_short"
    INIT_LONG = "init_long"
    INIT_REPAIR_RETRY = "init_repair_retry"
    RUN_CHAPTER = "run_chapter"
    PREPARE_CHAPTER = "prepare_chapter"
    RESOLVE_CHAPTER_CHECKPOINT = "resolve_chapter_checkpoint"
    RESOLVE_CHAPTER_CHECKPOINT_FINALIZE = "resolve_chapter_checkpoint_finalize"
    REPAIR_CONTINUITY = "repair_continuity"
    REPAIR_CAUSAL = "repair_causal"
    REPAIR_ISSUES = "repair_issues"
    REEVALUATE_CHAPTER = "reevaluate_chapter"
    POLISH_CHAPTER = "polish_chapter"
    BOOK_CONSISTENCY = "book_consistency"
    BOOK_EDITORIAL_AUDIT = "book_editorial_audit"
    GLOBAL_REPAIR_QUEUE = "global_repair_queue"
    EXPORT_BOOK = "export_book"
    REEXTRACT_RELATIONSHIPS = "reextract_relationships"
    REPAIR_MOTIF_HISTORY = "repair_motif_history"
    REBUILD_MEMORY_VECTORS = "rebuild_memory_vectors"
    POLISH_OUTLINE = "polish_outline"
    SYNC_CHAPTER_CONTRACTS = "sync_chapter_contracts"
    EXTEND_OUTLINE = "extend_outline"
    PLANNING_HORIZON = "planning_horizon"
    AUTHORING_CHAT = "authoring_chat"
    REPAIR_CASE = "repair_case"
    SEMANTIC_CONSISTENCY = "semantic_consistency"
    TTS_BUILD_VOICE_TEAM = "tts_build_voice_team"
    TTS_GENERATE_SCRIPT = "tts_generate_script"
    TTS_SYNTHESIZE = "tts_synthesize"
    TTS_FULL_PIPELINE = "tts_full_pipeline"
    TTS_POST_ARCHIVE = "tts_post_archive"
    TTS_EXPORT_AUDIO = "tts_export_audio"
    TTS_EXPORT_AUDIOBOOK = "tts_export_audiobook"
    OLLAMA_RUNTIME_CONTROL = "ollama_runtime_control"
    OLLAMA_PULL_MODEL = "ollama_pull_model"
    OLLAMA_DELETE_MODEL = "ollama_delete_model"


class JobScope(str, Enum):
    """Ownership boundary for durable UI-facing jobs."""

    PROJECT = "project"
    SYSTEM = "system"


VOICE_DURABLE_JOB_KINDS = frozenset(
    {
        JobKind.TTS_BUILD_VOICE_TEAM.value,
        JobKind.TTS_GENERATE_SCRIPT.value,
        JobKind.TTS_SYNTHESIZE.value,
        JobKind.TTS_FULL_PIPELINE.value,
        JobKind.TTS_EXPORT_AUDIO.value,
        JobKind.TTS_EXPORT_AUDIOBOOK.value,
    }
)


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class JobEventType(str, Enum):
    JOB_SNAPSHOT = "job_snapshot"
    JOB_STARTED = "job_started"
    JOB_STEP = "job_step"
    DECISION_REQUIRED = "decision_required"
    TOKEN_UPDATE = "token_update"
    SECTION_CHANGED = "section_changed"
    JOB_SUCCEEDED = "job_succeeded"
    JOB_FAILED = "job_failed"
    JOB_CANCELLED = "job_cancelled"
    JOB_PAUSED = "job_paused"


class JobStepEvent(BaseModel):
    at: str = Field(default_factory=utc_now_iso)
    step: str
    payload: dict[str, Any] = Field(default_factory=dict)


class JobRecord(BaseModel):
    job_id: str = Field(default_factory=lambda: uuid4().hex)
    kind: JobKind | str
    label: str
    project_id: str = ""
    # Human-facing project name.  The opaque ``project_id`` remains the
    # stable storage/command key and must never be repurposed for display.
    project_label: str = ""
    scope: JobScope = JobScope.PROJECT
    status: JobState = JobState.QUEUED
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    current_step: str = ""
    current_step_payload: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    error_summary: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    events: list[JobStepEvent] = Field(default_factory=list)
    # Bounded per-stream snapshots survive diagnostic-event compaction and
    # reconnects. Workflow milestones remain in their separate events history.
    stream_results: dict[str, JobStepEvent] = Field(default_factory=dict)
    resolved_error_entry_ids: set[str] = Field(default_factory=set)
    cumulative_tokens: int = 0
    cumulative_cost_usd: float = 0.0
    pending_decision: dict[str, Any] = Field(default_factory=dict)

    def to_history_payload(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["status"] = self.status.value if isinstance(self.status, JobState) else self.status
        data["kind"] = self.kind.value if isinstance(self.kind, JobKind) else self.kind
        data["resolved_error_entry_ids"] = sorted(self.resolved_error_entry_ids)
        return data

    @classmethod
    def from_history_payload(cls, data: dict[str, Any]) -> "JobRecord | None":
        try:
            status_raw = str(data.get("status", "") or "").strip().lower()
            status = (
                JobState(status_raw)
                if status_raw in JobState._value2member_map_
                else JobState.SUCCEEDED
            )
            payload = dict(data)
            payload["status"] = status
            return cls.model_validate(payload)
        except Exception:
            return None


class JobEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    job_id: str
    type: JobEventType | str
    at: str = Field(default_factory=utc_now_iso)
    step: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class JobCommand(BaseModel):
    job_id: str = ""
    kind: JobKind
    record_kind: JobKind | str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    label: str = ""
    project_id: str = ""
    scope: JobScope = JobScope.PROJECT
    mock: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class DecisionRequest(BaseModel):
    decision_id: str
    choice: str
    custom_text: str = ""
    approval_version: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
