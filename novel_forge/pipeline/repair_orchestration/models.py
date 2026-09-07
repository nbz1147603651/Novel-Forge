"""Shared models for Repair Orchestration v2."""

from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class RepairControlMode(str, Enum):
    """User-facing control modes for automatic repair execution."""

    MANUAL = "manual"
    AI_ASSISTED = "ai_assisted"
    AI_AUTO = "ai_auto"


class RepairDomain(str, Enum):
    """Repair domains handled by the v2 orchestration layer."""

    CONTINUITY = "continuity"
    CAUSAL = "causal"
    READING_POWER = "reading_power"
    KNOWLEDGE_BOUNDARY = "knowledge_boundary"
    PROMPT_LEAK = "prompt_leak"
    GUARDRAIL = "guardrail"
    INIT_ARTIFACT = "init_artifact"
    RUNTIME_CONTRACT = "runtime_contract"
    BOOK_CONSISTENCY = "book_consistency"
    FORMAT_RESPONSE = "format_response"
    SHORT_STORY = "short_story"
    STATE_ADJUDICATION = "state_adjudication"


class RepairSurface(str, Enum):
    """Mutable surface targeted by a repair attempt."""

    CHAPTER_TEXT = "chapter_text"
    CHAPTER_WINDOW = "chapter_window"
    BRIDGE_ARTIFACT = "bridge_artifact"
    CHAPTER_CONTRACTS = "chapter_contracts"
    BLUEPRINT = "blueprint"
    OUTLINE = "outline"
    SCENE_PLAN = "scene_plan"
    RESPONSE_JSON = "response_json"
    BOOK_CHAPTER_SET = "book_chapter_set"
    SHORT_TEXT = "short_text"


class RepairStrategy(str, Enum):
    """Execution strategy selected for a repair target."""

    LOCAL_PATCH = "local_patch"
    LLM_PATCH = "llm_patch"
    WINDOW_REWRITE = "window_rewrite"
    FULLTEXT_REWRITE = "fulltext_rewrite"
    JSON_PATCH = "json_patch"
    FORMAT_REPAIR = "format_repair"
    LOCAL_FALLBACK = "local_fallback"
    VERIFY_ONLY = "verify_only"


class RepairAttemptStatus(str, Enum):
    """Lifecycle status for one target attempt."""

    PLANNED = "planned"
    PENDING_APPROVAL = "pending_approval"
    APPLIED = "applied"
    VERIFIED = "verified"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    SKIPPED = "skipped"


class RepairTarget(BaseModel):
    """One concrete issue or artifact location to repair."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    domain: RepairDomain
    surface: RepairSurface
    chapter_number: int | None = Field(default=None, ge=0)
    issue_ref: str = ""
    severity: str = "medium"
    summary: str = ""
    evidence: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    allowed_strategies: list[RepairStrategy] = Field(default_factory=list)

    @property
    def signature(self) -> str:
        parts = [
            self.domain.value,
            self.surface.value,
            str(self.chapter_number or ""),
            self.issue_ref,
            self.summary,
        ]
        return ":".join(part for part in parts if part)


class RepairMission(BaseModel):
    """A set of repair targets executed under one policy and trace."""

    project_id: str
    targets: list[RepairTarget] = Field(default_factory=list)
    control_mode: RepairControlMode = RepairControlMode.AI_ASSISTED
    policy: dict[str, Any] = Field(default_factory=dict)
    source_context: dict[str, Any] = Field(default_factory=dict)
    max_rounds: int = Field(default=3, ge=0)
    trace_id: str = Field(default_factory=lambda: uuid4().hex)


class RepairPlanCandidate(BaseModel):
    """Plan returned by a domain handler before execution."""

    target_id: str
    strategy: RepairStrategy
    summary: str = ""
    rationale: str = ""
    preview: str = ""
    estimated_change_ratio: float = Field(default=0.0, ge=0.0)
    crosses_artifact_boundary: bool = False
    verification_required: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepairSnapshot(BaseModel):
    """Rollback anchor captured before an execution attempt."""

    snapshot_id: str = Field(default_factory=lambda: uuid4().hex)
    target_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepairExecutionResult(BaseModel):
    """Raw execution result from a handler."""

    applied: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)
    changed_artifacts: list[str] = Field(default_factory=list)
    change_ratio: float = Field(default=0.0, ge=0.0)
    warnings: list[str] = Field(default_factory=list)
    failure_reason: str = ""


class RepairVerificationResult(BaseModel):
    """Verification result after applying a repair candidate."""

    verified: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    residual_issues: list[dict[str, Any]] = Field(default_factory=list)


class RepairArtifactChange(BaseModel):
    """Committed artifact-level change."""

    artifact: str
    change_type: str = "update"
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepairAttempt(BaseModel):
    """One planned or executed attempt for a target."""

    target_id: str
    round_number: int = Field(default=1, ge=1)
    domain: RepairDomain
    surface: RepairSurface
    strategy: RepairStrategy
    status: RepairAttemptStatus
    snapshot_id: str = ""
    decision: str = ""
    decision_reason: str = ""
    change_ratio: float = Field(default=0.0, ge=0.0)
    verification: RepairVerificationResult | None = None
    rollback_reason: str = ""
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepairOutcome(BaseModel):
    """Standard result returned by RepairOrchestrator."""

    project_id: str
    trace_id: str
    status: str = "completed"
    applied: bool = False
    targets_resolved: list[str] = Field(default_factory=list)
    targets_remaining: list[str] = Field(default_factory=list)
    attempts: list[RepairAttempt] = Field(default_factory=list)
    artifacts_changed: list[RepairArtifactChange] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    needs_human_review: bool = False
