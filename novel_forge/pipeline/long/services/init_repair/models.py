"""Typed models for initialization repair flows."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import Enum
from typing import Any

from novel_forge.core.schemas.audit import AuditIssueV2
from novel_forge.core.schemas.repair import RepairCandidate, RepairVerificationBundle


class InitArtifact(str, Enum):
    """Initialization artifact categories handled by repair policies."""

    BLUEPRINT = "blueprint"
    STORY_BIBLE = "story_bible"
    CHARACTER_BIBLE = "character_bible"
    OUTLINE = "outline"
    CHAPTER_CONTRACTS = "chapter_contracts"
    SCENE_PLAN = "scene_plan"
    ENTITY_REGISTRY = "entity_registry"


class InitRepairStage(str, Enum):
    """Repair lifecycle stages shared by artifact policies."""

    NORMALIZE = "normalize"
    VALIDATE = "validate"
    LLM_REPAIR = "llm_repair"
    LOCAL_FALLBACK = "local_fallback"
    ACCEPT = "accept"
    BLOCK = "block"


class InitRepairIssueKind(str, Enum):
    """Shared issue categories for init artifact repair decisions."""

    FORMAT_ALIAS = "format_alias"
    SCHEMA_SHAPE = "schema_shape"
    RANGE_OR_COVERAGE = "range_or_coverage"
    MISSING_EXECUTION_GRAPH = "missing_execution_graph"
    SEMANTIC_CONFLICT = "semantic_conflict"
    CROSS_ARTIFACT_DRIFT = "cross_artifact_drift"
    IRRECOVERABLE = "irrecoverable"


@dataclass(frozen=True)
class InitRepairIssue:
    """Typed validation issue used by repair policies."""

    kind: InitRepairIssueKind
    message: str
    field: str = ""
    severity: str = "error"
    metadata: dict[str, Any] = dataclass_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "message": self.message,
            "field": self.field,
            "severity": self.severity,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class InitRepairReport:
    """Policy-neutral validation report."""

    errors: list[str] = dataclass_field(default_factory=list)
    warnings: list[str] = dataclass_field(default_factory=list)
    suggestions: list[str] = dataclass_field(default_factory=list)
    issues: list[InitRepairIssue] = dataclass_field(default_factory=list)
    raw: Any | None = None

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "suggestions": list(self.suggestions),
            "issues": [issue.to_dict() for issue in self.issues],
            "is_valid": self.is_valid,
        }


@dataclass(frozen=True)
class InitRepairAttempt:
    """One stage transition in a repair run."""

    stage: InitRepairStage
    status: str
    errors: list[str] = dataclass_field(default_factory=list)
    warnings: list[str] = dataclass_field(default_factory=list)
    metadata: dict[str, Any] = dataclass_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "status": self.status,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class InitRepairContext:
    """Runtime context shared by init repair policies."""

    service_ctx: Any
    outline_ctx: dict[str, Any]
    total_chapters: int
    narrative_complexity: str = "standard"
    outline_thinking: bool = False
    artifacts: dict[str, Any] = dataclass_field(default_factory=dict)


@dataclass(frozen=True)
class InitRepairOutcome:
    """Final result from an init repair run."""

    payload: Any
    report: InitRepairReport
    attempts: list[InitRepairAttempt] = dataclass_field(default_factory=list)
    audit_issues: list[AuditIssueV2] = dataclass_field(default_factory=list)
    candidates: list[RepairCandidate] = dataclass_field(default_factory=list)
    verifications: list[RepairVerificationBundle] = dataclass_field(default_factory=list)

    @property
    def repaired(self) -> bool:
        return any(
            attempt.stage in {InitRepairStage.LLM_REPAIR, InitRepairStage.LOCAL_FALLBACK}
            and attempt.status == "applied"
            for attempt in self.attempts
        )

    def attempts_as_dicts(self) -> list[dict[str, Any]]:
        return [attempt.to_dict() for attempt in self.attempts]

    @property
    def latest_candidate(self) -> RepairCandidate | None:
        return self.candidates[-1] if self.candidates else None

    @property
    def latest_verification(self) -> RepairVerificationBundle | None:
        return self.verifications[-1] if self.verifications else None
