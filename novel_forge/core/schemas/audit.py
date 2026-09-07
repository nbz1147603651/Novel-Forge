"""Unified audit issue schemas.

This module defines the canonical issue contract shared by continuity, causal,
reading-power, guard, chapter-quality, alignment, and future audit lanes.
Domain-specific reports may keep their own rich schemas, but repair planning,
ledgers, quality gates, and UI surfaces should consume this normalized shape.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from novel_forge.core.schemas.base import VersionedSchema

AuditSeverity = Literal["critical", "high", "medium", "low"]
AuditStatus = Literal[
    "open",
    "repairing",
    "resolved",
    "suppressed",
    "artifact_fixed",
    "deferred",
    "closed",
    "fixed",
    "ignored",
]
RepairSurface = Literal[
    "chapter_text",
    "bridge_artifact",
    "chapter_plan",
    "state_packet",
    "local_rule",
    "replan",
    "manual",
]
AuditDimension = Literal[
    "continuity",
    "causal",
    "reading_power",
    "chapter_quality",
    "guard",
    "alignment",
    "state_adjudication",
    "macro_guard",
    "quality",
    "unknown",
]
AuditVerdict = Literal["accept", "needs_repair", "reject", "ambiguous", "defer"]
AuditTargetFormat = Literal[
    "json_artifact",
    "prose_text",
    "markdown_document",
    "prompt_response",
    "multi_chapter_set",
    "tts_script",
    "manual_only",
]
AuditTargetRole = Literal["repair", "reference"]
AuditRepairOperation = Literal[
    "replace",
    "field_replace",
    "window_rewrite",
    "json_patch",
    "schema_patch",
    "split_targets",
    "manual_review",
]


class AuditPostcondition(VersionedSchema):
    """Machine-readable acceptance condition for one audit issue."""

    validator_id: str = Field(default="")
    description: str = Field(default="")
    evidence_hint: str = Field(default="")
    required: bool = Field(default=True)


class AuditIssue(VersionedSchema):
    """Canonical issue shape for review, repair, ledger, gate, and UI layers."""

    issue_id: str = Field(default="")
    dimension: str = Field(default="unknown")
    source_module: str = Field(default="")
    source: str = Field(default="")
    issue_type: str = Field(default="")
    severity: AuditSeverity = Field(default="medium")
    status: AuditStatus = Field(default="open")
    repair_surface: RepairSurface = Field(default="chapter_text")
    blocking: bool = Field(default=False)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    summary: str = Field(default="")
    evidence: str = Field(default="")
    evidence_quote: str = Field(default="")
    location: str = Field(default="")
    location_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    anchor_type: str = Field(default="")
    paragraph_start: int = Field(default=0, ge=0)
    paragraph_end: int = Field(default=0, ge=0)

    fix_suggestion: str = Field(default="")
    fix_mode: str = Field(default="")
    fix_actions: list[str] = Field(default_factory=list)
    postconditions: list[AuditPostcondition] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_ledger_payload(self) -> dict[str, Any]:
        """Return a compact stable shape for issue-ledger diffing."""
        return {
            "issue_id": self.issue_id,
            "issue_type": self.issue_type,
            "severity": self.severity,
            "summary": self.summary,
            "evidence": self.evidence or self.evidence_quote,
            "location": self.location,
            "location_confidence": self.location_confidence,
            "status": self.status,
            "repair_surface": self.repair_surface,
            "dimension": self.dimension,
        }


class AuditReportProjection(VersionedSchema):
    """Normalized view over any domain-specific audit report."""

    dimension: str = Field(default="unknown")
    score: float = Field(default=0.0, ge=0.0, le=10.0)
    threshold: float = Field(default=0.0, ge=0.0, le=10.0)
    score_attr: str = Field(default="")
    source_report_type: str = Field(default="")
    issues: list[AuditIssue] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditLocator(VersionedSchema):
    """Format-specific location metadata for an audit target.

    The common contract keeps target roles stable while allowing each text or
    artifact format to expose the location primitives that are actually safe for
    that format.  Resolvers must only use fields that make sense for the
    declared ``target_format``.
    """

    model_config = ConfigDict(extra="forbid")

    target_format: AuditTargetFormat
    role: AuditTargetRole = "repair"
    surface: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    # json_artifact / prompt_response
    artifact: str = ""
    json_pointer: str = ""
    field: str = ""
    field_path: str = ""
    stable_node_id: str = ""
    segment_uid: str = ""
    segment_index: int | None = Field(default=None, ge=0)
    container_hash: str = ""
    chapter_number: int | None = Field(default=None, ge=0)
    chapter_range: list[int] = Field(default_factory=list)
    claim_id: str = ""
    expected_type: str = ""
    task_type: str = ""
    contract_path: str = ""
    missing_key: str = ""
    schema_error: str = ""
    comparator_id: str = ""
    expected_raw: Any = None
    actual_raw: Any = None
    expected_normalized: Any = None
    actual_normalized: Any = None
    source_version: str = ""
    target_version: str = ""

    # prose_text / markdown_document
    scene_id: str = ""
    paragraph_start: int = Field(default=0, ge=0)
    paragraph_end: int = Field(default=0, ge=0)
    quote: str = ""
    context_before: str = ""
    context_after: str = ""
    text_hash: str = ""
    char_start: int = Field(
        default=0,
        ge=0,
        description="Zero-based inclusive character offset inside the resolved text leaf.",
    )
    char_end: int = Field(
        default=0,
        ge=0,
        description="Zero-based exclusive character offset inside the resolved text leaf.",
    )
    heading_path: list[str] = Field(default_factory=list)
    section_index: int | None = Field(default=None, ge=0)

    # multi_chapter_set / manual_only
    issue_thread_id: str = ""
    chapter_set: list[int] = Field(default_factory=list)
    evidence_pairs: list[dict[str, Any]] = Field(default_factory=list)
    manual_review_reason: str = ""

    @model_validator(mode="after")
    def validate_format_locator(self) -> "AuditLocator":
        if self.char_end and self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        if self.target_format == "json_artifact":
            if not self.artifact:
                raise ValueError("json_artifact locator requires artifact")
            if not (
                self.json_pointer
                or self.field
                or self.field_path
                or self.claim_id
                or self.stable_node_id
            ):
                raise ValueError(
                    "json_artifact locator requires json_pointer, field, field_path, "
                    "claim_id, or stable_node_id"
                )
        if self.target_format == "tts_script":
            if not self.segment_uid or not self.field_path:
                raise ValueError("tts_script locator requires segment_uid and field_path")
        elif self.target_format == "prose_text":
            has_window = self.paragraph_start > 0 or self.paragraph_end > 0 or bool(self.scene_id)
            if not (has_window or self.quote):
                raise ValueError("prose_text locator requires a window, scene_id, or quote")
        elif self.target_format == "markdown_document":
            if not (self.heading_path or self.paragraph_start > 0 or self.quote):
                raise ValueError("markdown_document locator requires heading_path, window, or quote")
        elif self.target_format == "prompt_response":
            if not (self.contract_path or self.json_pointer or self.missing_key or self.schema_error):
                raise ValueError("prompt_response locator requires contract/schema location")
        elif self.target_format == "multi_chapter_set":
            if not (self.chapter_set or self.evidence_pairs or self.issue_thread_id):
                raise ValueError("multi_chapter_set locator requires chapter_set, evidence_pairs, or issue_thread_id")
        elif self.target_format == "manual_only" and not self.manual_review_reason:
            raise ValueError("manual_only locator requires manual_review_reason")
        return self


class AuditEvidence(VersionedSchema):
    """One evidence item supporting an audit issue."""

    model_config = ConfigDict(extra="forbid")

    quote: str = ""
    source: str = ""
    locator: AuditLocator | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class AuditRepairIntent(VersionedSchema):
    """Machine-readable repair intent shared by audit and repair layers."""

    model_config = ConfigDict(extra="forbid")

    operation: AuditRepairOperation = "replace"
    target_policy: str = "single_target"
    rationale: str = ""
    preserve: list[str] = Field(default_factory=list)
    allowed_strategies: list[str] = Field(default_factory=list)


class AuditIssueV2(VersionedSchema):
    """Canonical v2 issue shape used by audit reports and repair target resolution."""

    model_config = ConfigDict(extra="ignore")

    issue_id: str
    dimension: str = "unknown"
    issue_type: str = ""
    severity: AuditSeverity = "medium"
    blocking: bool = False
    status: AuditStatus = "open"
    summary: str
    description: str
    evidence: list[AuditEvidence]
    repair_targets: list[AuditLocator]
    reference_targets: list[AuditLocator]
    repair_intent: AuditRepairIntent
    postconditions: list[AuditPostcondition] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_target_roles(self) -> "AuditIssueV2":
        for target in self.repair_targets:
            if target.role != "repair":
                raise ValueError("repair_targets may only contain role='repair' locators")
        for target in self.reference_targets:
            if target.role != "reference":
                raise ValueError("reference_targets may only contain role='reference' locators")
        if self.severity in {"high", "critical"} and not self.repair_targets:
            raise ValueError("high/critical issues require at least one repair_target")
        if self.severity in {"high", "critical"} and not self.description.strip():
            raise ValueError("high/critical issues require description")
        if self.severity in {"high", "critical"} and not self.evidence:
            raise ValueError("high/critical issues require evidence")
        return self


class ResolvedRepairTarget(VersionedSchema):
    """Code-resolved target passed to repair prompts and patch appliers."""

    model_config = ConfigDict(extra="forbid")

    target_id: str
    target_format: AuditTargetFormat
    surface: str
    locator: AuditLocator
    path: str = ""
    window: dict[str, Any] = Field(default_factory=dict)
    current_value: Any = None
    current_hash: str = ""
    issue_ids: list[str]
    allowed_operation: AuditRepairOperation = "replace"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    resolution_status: Literal["resolved", "manual_required", "ambiguous", "unresolved"] = "resolved"
    reason: str = ""

    def to_prompt_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        cleaned = _strip_internal_schema_metadata(payload)
        return dict(cleaned) if isinstance(cleaned, dict) else {}


def _strip_internal_schema_metadata(value: Any) -> Any:
    """Keep volatile version/timestamp fields out of nested repair prompts."""

    if isinstance(value, dict):
        return {
            key: _strip_internal_schema_metadata(item)
            for key, item in value.items()
            if key not in {"schema_version", "created_at"}
        }
    if isinstance(value, list):
        return [_strip_internal_schema_metadata(item) for item in value]
    return value


class AuditReportV2(VersionedSchema):
    """Unified v2 audit report consumed by gates, UI, and repair planning."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["audit_v2"] = "audit_v2"
    dimension: str = "unknown"
    verdict: AuditVerdict = "accept"
    score: float = Field(default=0.0, ge=0.0, le=10.0)
    issues: list[AuditIssueV2] = Field(default_factory=list)
    summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
