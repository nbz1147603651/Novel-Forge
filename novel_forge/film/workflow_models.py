"""Versioned contracts for the editable film-production workflow graph.

The original film ``run_plan`` is a stage projection.  These models keep the
editable graph, one execution and one node attempt separate so layout edits do
not rewrite runtime state and provider updates do not move the canvas.
"""

from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .schemas import FilmStage, utc_now_iso


class FilmGraphRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_CONFIRMATION = "waiting_confirmation"
    WAITING_HUMAN = "waiting_human"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class FilmGraphIssueSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class FilmGraphRunScope(str, Enum):
    SELECTED = "selected"
    TO_NODE = "to_node"
    DOWNSTREAM = "downstream"
    ALL = "all"


class FilmGraphPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = 0
    y: float = 0


class FilmGraphViewport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = 0
    y: float = 0
    zoom: float = Field(default=0.82, ge=0.1, le=4)


class FilmNodePort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    port_id: str = Field(min_length=1)
    label: str
    artifact_type: str = Field(min_length=1)
    required: bool = True
    multiple: bool = False


class FilmPromptSource(str, Enum):
    NOVEL = "novel"
    VOICE = "voice"
    H3_GUIDE = "h3_guide"
    WORKFLOW = "workflow"
    USER = "user"


class FilmPromptSection(BaseModel):
    """One editable semantic layer of a node prompt.

    Sections are persisted instead of one opaque prompt string so the UI can
    preserve source lineage, optimize only one concern and render the exact
    provider payload deterministically on the backend.
    """

    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    guidance: str = ""
    content: str = ""
    required: bool = True
    editable: bool = True
    sources: list[FilmPromptSource] = Field(default_factory=list)


class FilmNodePrompt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    template_id: str = ""
    template_version: str = ""
    purpose: str = ""
    sections: list[FilmPromptSection] = Field(default_factory=list)
    user_notes: str = ""
    updated_at: str = Field(default_factory=utc_now_iso)


class FilmPromptOptimizationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: FilmNodePrompt
    rendered_prompt: str
    changes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    character_count: int = Field(default=0, ge=0)


class FilmGraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    type_id: str = Field(min_length=1)
    type_version: str = "1.0"
    label: str
    stage: FilmStage
    position: FilmGraphPosition = Field(default_factory=FilmGraphPosition)
    group_id: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    prompt: FilmNodePrompt = Field(default_factory=FilmNodePrompt)
    input_ports: list[FilmNodePort] = Field(default_factory=list)
    output_ports: list[FilmNodePort] = Field(default_factory=list)
    provider_id: str = ""
    model_id: str = ""
    bypassed: bool = False
    disabled: bool = False
    human_checkpoint: bool = False
    estimated_cost_usd: float = Field(default=0, ge=0)


class FilmGraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edge_id: str = Field(min_length=1)
    source_node_id: str = Field(min_length=1)
    source_port_id: str = Field(min_length=1)
    target_node_id: str = Field(min_length=1)
    target_port_id: str = Field(min_length=1)


class FilmGraphGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(min_length=1)
    label: str
    color: str = ""
    collapsed: bool = False


class FilmGraphDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "2.0"
    graph_id: str = Field(default_factory=lambda: uuid4().hex)
    project_id: str
    revision: int = Field(default=1, ge=1)
    nodes: list[FilmGraphNode] = Field(default_factory=list)
    edges: list[FilmGraphEdge] = Field(default_factory=list)
    groups: list[FilmGraphGroup] = Field(default_factory=list)
    viewport: FilmGraphViewport = Field(default_factory=FilmGraphViewport)
    source_signatures: dict[str, str] = Field(default_factory=dict)
    updated_at: str = Field(default_factory=utc_now_iso)


class FilmGraphValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_id: str = Field(default_factory=lambda: uuid4().hex)
    code: str
    message: str
    severity: FilmGraphIssueSeverity = FilmGraphIssueSeverity.ERROR
    node_id: str = ""
    edge_id: str = ""


class FilmNodeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type_id: str
    version: str = "1.0"
    label: str
    description: str = ""
    category: str
    stage: FilmStage
    input_ports: list[FilmNodePort] = Field(default_factory=list)
    output_ports: list[FilmNodePort] = Field(default_factory=list)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    default_config: dict[str, Any] = Field(default_factory=dict)
    prompt_template: FilmNodePrompt = Field(default_factory=FilmNodePrompt)
    permissions: list[str] = Field(default_factory=list)
    provider_capabilities: list[str] = Field(default_factory=list)
    cacheable: bool = True
    allows_bypass: bool = True
    paid: bool = False
    human_checkpoint: bool = False
    estimated_cost_usd: float = Field(default=0, ge=0)
    estimated_duration_s: int = Field(default=0, ge=0)


class FilmNodeAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    node_id: str
    attempt: int = Field(default=1, ge=1)
    status: FilmGraphRunStatus = FilmGraphRunStatus.QUEUED
    input_signature: str = ""
    provider_task_id: str = ""
    artifact_uris: list[str] = Field(default_factory=list)
    cost_usd: float = Field(default=0, ge=0)
    error_code: str = ""
    error_message: str = ""
    started_at: str = ""
    finished_at: str = ""


class FilmRunEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph_revision: int
    scope: FilmGraphRunScope
    target_node_ids: list[str] = Field(default_factory=list)
    execution_node_ids: list[str] = Field(default_factory=list)
    cached_node_ids: list[str] = Field(default_factory=list)
    estimated_cost_usd: float = Field(default=0, ge=0)
    estimated_duration_s: int = Field(default=0, ge=0)
    missing_inputs: list[str] = Field(default_factory=list)
    validation_issues: list[FilmGraphValidationIssue] = Field(default_factory=list)
    requires_confirmation: bool = False


class FilmGraphRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(default_factory=lambda: uuid4().hex)
    project_id: str
    graph_id: str
    graph_revision: int
    scope: FilmGraphRunScope
    target_node_ids: list[str] = Field(default_factory=list)
    high_priority: bool = False
    status: FilmGraphRunStatus = FilmGraphRunStatus.QUEUED
    confirmed_cost: bool = False
    estimate: FilmRunEstimate
    attempts: list[FilmNodeAttempt] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class FilmGraphEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    sequence: int = Field(ge=1)
    kind: str
    node_id: str = ""
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)


class FilmGraphView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition: FilmGraphDefinition
    validation_issues: list[FilmGraphValidationIssue] = Field(default_factory=list)
    latest_run: FilmGraphRun | None = None
