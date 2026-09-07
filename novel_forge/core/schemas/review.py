"""Unified review findings and repair tickets used across quality modules."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import Field

from novel_forge.core.schemas.base import VersionedSchema


class ReviewFinding(VersionedSchema):
    """A normalized review finding that can be routed into repair planning."""

    finding_id: str = Field(default="")
    chapter_number: int = Field(default=0, ge=0)
    review_mode: str = Field(
        default="full_review",
        description="full_review / targeted_recheck / regression_scan.",
    )
    review_round: int = Field(
        default=1,
        ge=1,
        description="1 for first-pass review, >1 for recheck/repair verification rounds.",
    )
    source_module: str = Field(default="")
    dimension: str = Field(default="")
    issue_type: str = Field(default="")
    severity: str = Field(default="medium")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    summary: str = Field(default="")
    evidence_quote: str = Field(default="")
    paragraph_start: int = Field(default=0, ge=0)
    paragraph_end: int = Field(default=0, ge=0)
    anchor_type: str = Field(default="inferred_scope")
    repair_goal: str = Field(default="")
    postconditions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Machine-readable acceptance checks that must hold after repair.",
    )
    must_preserve: list[str] = Field(default_factory=list)
    suggested_mode: str = Field(default="window")
    blocks_finalize: bool = Field(default=False)
    source_text_hash: str = Field(default="")
    signature: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewDiagnosticAnchor(VersionedSchema):
    """Machine-usable source anchor for an issue before repair."""

    chapter_number: int = Field(default=0, ge=0)
    paragraph_index: int = Field(default=0, ge=0)
    paragraph_span: list[int] = Field(default_factory=list)
    anchor_type: str = Field(default="inferred_scope")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    linked_issue_ref_count: int = Field(default=0, ge=0)


class ReviewRepairReadiness(VersionedSchema):
    """Repair admission verdict for one diagnostic."""

    status: str = Field(
        default="ready",
        description="ready / verify_first / manual_review / blocked.",
    )
    risk: str = Field(default="low")
    reasons: list[str] = Field(default_factory=list)
    auto_repair_eligible: bool = Field(default=True)


class RepairTicket(VersionedSchema):
    """A bounded repair task compiled from one or more findings."""

    ticket_id: str = Field(default="")
    chapter_number: int = Field(default=0, ge=0)
    review_mode: str = Field(
        default="full_review",
        description="Review mode that produced this ticket.",
    )
    finding_ids: list[str] = Field(default_factory=list)
    source_module: str = Field(default="")
    dimension: str = Field(default="")
    issue_type: str = Field(default="")
    severity: str = Field(default="medium")
    target_summary: str = Field(default="")
    repair_goal: str = Field(default="")
    repair_mode: str = Field(default="window")
    acceptance_criteria: list[str] = Field(default_factory=list)
    postconditions: list[dict[str, Any]] = Field(default_factory=list)
    must_preserve: list[str] = Field(default_factory=list)
    forbidden_changes: list[str] = Field(default_factory=list)
    source_text_hash: str = Field(default="")
    max_change_ratio: float = Field(default=0.08, ge=0.0, le=1.0)
    max_attempts: int = Field(default=2, ge=1, le=5)
    target_paragraph_start: int = Field(default=0, ge=0)
    target_paragraph_end: int = Field(default=0, ge=0)
    blocking: bool = Field(default=False)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepairVerificationResult(VersionedSchema):
    """Normalized post-repair verification for a repair ticket."""

    verification_id: str = Field(default="")
    ticket_id: str = Field(default="")
    finding_ids: list[str] = Field(default_factory=list)
    chapter_number: int = Field(default=0, ge=0)
    review_mode: str = Field(default="targeted_recheck")
    source_module: str = Field(default="")
    dimension: str = Field(default="")
    issue_type: str = Field(default="")
    status: str = Field(
        default="not_run",
        description="resolved / partial / unresolved / regressed / blocked / skipped / failed / not_run",
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    remaining_evidence: list[str] = Field(default_factory=list)
    new_findings: list[ReviewFinding] = Field(default_factory=list)
    checked_postconditions: list[dict[str, Any]] = Field(default_factory=list)
    source_text_hash: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class BeforeRepairCheckpoint(VersionedSchema):
    """Snapshot of chapter text and context before a repair operation."""

    checkpoint_id: str = Field(default_factory=lambda: uuid4().hex)
    project_id: str = Field(default="")
    chapter_number: int = Field(default=0, ge=0)
    original_text: str = Field(default="")
    text_path: str = Field(default="")
    repair_source: str = Field(default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    issue_signatures: list[str] = Field(default_factory=list)


class CausalRepairPlan(VersionedSchema):
    """Repair plan for causal chain issues."""

    plan_id: str = Field(default_factory=lambda: uuid4().hex)
    chapter_number: int = Field(default=0, ge=0)
    issues: list[Any] = Field(default_factory=list)
    target_sections: list[Any] = Field(default_factory=list)
    must_keep: list[Any] = Field(default_factory=list)
    must_change: list[Any] = Field(default_factory=list)
    expected_outcome: str = Field(default="")


class CrossDimensionIssueLink(VersionedSchema):
    """Links an issue in one repair dimension to a suspected cause in another.

    Created when post-repair checks detect that a repair in dimension A
    (e.g. causal) may have introduced a new issue in dimension B (e.g. continuity).
    Enables traceability across the multi-dimensional repair chain.
    """

    source_issue_id: str = Field(
        default="",
        description="Issue ID of the suspected cause (e.g. the causal repair target).",
    )
    target_issue_id: str = Field(
        default="",
        description="Issue ID of the newly detected issue (e.g. continuity regression).",
    )
    source_dimension: str = Field(default="")
    target_dimension: str = Field(default="")
    link_type: str = Field(
        default="suspected_regression",
        description="suspected_regression / shared_root / repair_side_effect",
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: str = Field(default="")
