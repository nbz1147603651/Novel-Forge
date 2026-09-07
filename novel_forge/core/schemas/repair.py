"""Persistent contracts for candidate-first text repair.

These models describe non-canon repair evidence.  They deliberately carry
content identities and publication receipts, but never grant authority or
write project artifacts by themselves.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from novel_forge.core.schemas.audit import AuditIssueV2, AuditSeverity, ResolvedRepairTarget
from novel_forge.core.schemas.base import VersionedSchema

RepairCaseStatus = Literal[
    "open",
    "located",
    "candidate_ready",
    "needs_verification",
    "verified",
    "awaiting_approval",
    "published",
    "resolved",
    "manual_required",
    "stale",
    "rejected",
    "deferred",
    "failed",
]
RepairAuthority = Literal[
    "automatic_derived",
    "automatic_working_candidate",
    "proposal_required",
    "manual_only",
]
RepairCandidateOrigin = Literal[
    "deterministic",
    "model",
    "human_edit",
    "legacy_shadow",
]
RepairTransactionStatus = Literal[
    "not_started",
    "prepared",
    "committed",
    "reconciled",
    "failed",
]
RepairCaseEventType = Literal[
    "case_created",
    "targets_resolved",
    "candidate_built",
    "candidate_edited",
    "verification_requested",
    "verification_completed",
    "approval_requested",
    "decision_recorded",
    "semantic_decision_recorded",
    "manual_required",
    "case_stale",
    "publication_prepared",
    "publication_committed",
    "recovery_reconciled",
    "case_failed",
]


class RepairArtifactSnapshot(VersionedSchema):
    """Immutable source identity supplied to one repair plugin invocation."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    content_type: str
    artifact_id: str
    source_version: str = ""
    source_hash: str
    payload: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepairPatchRecord(VersionedSchema):
    """Report-safe description of one exact patch in a candidate."""

    model_config = ConfigDict(extra="forbid")

    target_id: str
    expected_hash: str
    replacement_hash: str
    operation: str = "replace"
    field_path: str = ""
    rationale: str = ""


class RepairCandidate(VersionedSchema):
    """One isolated candidate version; payload content lives in the blob store."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    version: int = Field(ge=1)
    base_hash: str
    candidate_hash: str
    blob_hash: str
    origin: RepairCandidateOrigin
    patch_count: int = Field(default=0, ge=0)
    change_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    patches: list[RepairPatchRecord] = Field(default_factory=list)
    protected_items: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_patch_count(self) -> "RepairCandidate":
        if self.patch_count != len(self.patches):
            raise ValueError("patch_count must match patches")
        if self.candidate_hash != self.base_hash and not self.patches:
            raise ValueError("changed repair candidate requires exact patch evidence")
        return self


class RepairValidatorResult(VersionedSchema):
    """One original or invariant validator result."""

    model_config = ConfigDict(extra="forbid")

    validator_id: str
    passed: bool
    required: bool = True
    details: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class RepairVerificationBundle(VersionedSchema):
    """Verifier evidence bound to one exact candidate version and hash."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    candidate_version: int = Field(ge=1)
    candidate_hash: str
    passed: bool
    resolved_issue_ids: list[str] = Field(default_factory=list)
    residual_issue_ids: list[str] = Field(default_factory=list)
    regression_issue_ids: list[str] = Field(default_factory=list)
    validators: list[RepairValidatorResult] = Field(default_factory=list)
    details: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_required_validators(self) -> "RepairVerificationBundle":
        required_failed = any(not item.passed for item in self.validators if item.required)
        if self.passed and not self.validators:
            raise ValueError("passed verification requires the original validator evidence")
        if self.passed and not self.resolved_issue_ids:
            raise ValueError("passed verification must close at least one original issue")
        if self.passed and (
            required_failed or self.residual_issue_ids or self.regression_issue_ids
        ):
            raise ValueError(
                "passed verification cannot retain required failures, residuals, or regressions"
            )
        return self


class RepairPublishReceipt(VersionedSchema):
    """Recoverable evidence for one guarded publication attempt."""

    model_config = ConfigDict(extra="forbid")

    receipt_id: str
    case_id: str
    candidate_version: int = Field(ge=1)
    authority: RepairAuthority
    target: str
    before_hash: str
    after_hash: str
    input_version: str = ""
    policy_version: int | None = Field(default=None, ge=1)
    approval_id: str = ""
    proposal_id: str = ""
    transaction_status: RepairTransactionStatus = "not_started"
    committed: bool = False
    recovered: bool = False
    message: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_commit_status(self) -> "RepairPublishReceipt":
        if self.committed and self.transaction_status not in {"committed", "reconciled"}:
            raise ValueError("committed receipt requires committed or reconciled status")
        if self.recovered and self.transaction_status != "reconciled":
            raise ValueError("recovered receipt requires reconciled status")
        return self


class RepairCase(VersionedSchema):
    """Durable projection of one diagnose-locate-repair lifecycle."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    project_id: str
    content_type: str
    source: str
    artifact_id: str
    source_version: str = ""
    source_hash: str
    authority: RepairAuthority
    input_version: str = ""
    policy_version: int | None = Field(default=None, ge=1)
    status: RepairCaseStatus = "open"
    version: int = Field(default=1, ge=1)
    event_seq: int = Field(default=0, ge=0)
    title: str = ""
    chapter_numbers: list[int] = Field(default_factory=list)
    issues: list[AuditIssueV2] = Field(default_factory=list)
    targets: list[ResolvedRepairTarget] = Field(default_factory=list)
    candidates: list[RepairCandidate] = Field(default_factory=list)
    verification: RepairVerificationBundle | None = None
    receipt: RepairPublishReceipt | None = None
    proposal_id: str = ""
    failure_reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def latest_candidate(self) -> RepairCandidate | None:
        return self.candidates[-1] if self.candidates else None


class RepairCaseEvent(VersionedSchema):
    """Append-only event from which a :class:`RepairCase` is projected."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    seq: int = Field(ge=1)
    case_id: str
    case_version: int = Field(ge=1)
    event_type: RepairCaseEventType
    actor: str = "system"
    data: dict[str, Any] = Field(default_factory=dict)


class RepairWorkbenchCapabilities(VersionedSchema):
    """Effective backend capabilities for one repair workbench projection."""

    model_config = ConfigDict(extra="forbid")

    annotate: bool = True
    prepare: bool = False
    edit: bool = False
    verify: bool = False
    request_approval: bool = False
    reject_or_defer: bool = True
    publish: bool = False
    recover: bool = False
    reason: str = "候选准备与发布尚未接入当前内容插件"


class RepairSourceView(VersionedSchema):
    """Server-selected, path-free source that can be annotated in NIMO."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    content_type: str = "chapter_text"
    artifact_id: str
    chapter_number: int = Field(ge=1)
    source_version: str
    source_hash: str
    content: str
    state: Literal["working_candidate", "official"]


class RepairManualAnnotationRequest(VersionedSchema):
    """One author-selected exact text span; never accepts a filesystem path."""

    model_config = ConfigDict(extra="forbid")

    chapter_number: int = Field(ge=1)
    artifact_id: str
    source_hash: str
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    selected_text: str = Field(min_length=1, max_length=20_000)
    summary: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=2_000)
    severity: AuditSeverity = "medium"

    @model_validator(mode="after")
    def validate_selection(self) -> "RepairManualAnnotationRequest":
        if self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        return self


class RepairCandidateEditRequest(VersionedSchema):
    """Exact replacement for the selected repair target, not an arbitrary file write."""

    model_config = ConfigDict(extra="forbid")

    case_version: int = Field(ge=1)
    candidate_version: int = Field(ge=0)
    replacement_text: str = Field(max_length=100_000)


class RepairCaseDecisionRequest(VersionedSchema):
    """Non-publishing author decision available even while rollout is closed."""

    model_config = ConfigDict(extra="forbid")

    case_version: int = Field(ge=1)
    decision: Literal[
        "reject",
        "defer",
        "accept_compatible",
        "authoritative_claims",
    ]
    reason: str = Field(default="", max_length=1_000)
    source_hash: str = Field(default="", max_length=128)
    claim_ids: list[str] = Field(default_factory=list, max_length=64)
    authoritative_claim_ids: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_semantic_decision_identity(self) -> "RepairCaseDecisionRequest":
        semantic = self.decision in {"accept_compatible", "authoritative_claims"}
        if semantic and (not self.source_hash or not self.claim_ids):
            raise ValueError("semantic decision requires exact source_hash and claim_ids")
        if self.decision == "authoritative_claims" and not self.authoritative_claim_ids:
            raise ValueError("authoritative decision requires authoritative_claim_ids")
        if self.decision == "accept_compatible" and self.authoritative_claim_ids:
            raise ValueError("compatible decision cannot select authoritative claims")
        return self


class RepairVerificationRequest(VersionedSchema):
    """CAS identity for re-running the original verifier."""

    model_config = ConfigDict(extra="forbid")

    case_version: int = Field(ge=1)
    candidate_version: int = Field(ge=1)


class RepairCaseJobRequest(VersionedSchema):
    """Path-free durable work request for candidate evidence only."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=255)
    case_id: str = Field(min_length=1, max_length=255)
    operation: Literal["prepare", "verify", "shadow_compare"]
    case_version: int = Field(ge=1)
    candidate_version: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_operation_version(self) -> "RepairCaseJobRequest":
        if self.operation == "verify" and self.candidate_version < 1:
            raise ValueError("verify repair job requires candidate_version")
        if self.operation == "prepare" and self.candidate_version != 0:
            raise ValueError("prepare repair job must not claim an existing candidate version")
        return self


class RepairPublishRequest(RepairVerificationRequest):
    """Publication request bound to the displayed authority projection."""

    authority_version: int = Field(ge=0)


class RepairApprovalRequest(RepairVerificationRequest):
    """Create one existing versioned authoring proposal for a verified candidate."""


class RepairRecoveryRequest(VersionedSchema):
    """Reconcile one prepared/committed receipt without replaying content."""

    model_config = ConfigDict(extra="forbid")

    case_version: int = Field(ge=1)
    receipt_id: str = Field(min_length=1, max_length=255)


class RepairCaseDetailView(VersionedSchema):
    """Workbench detail with evidence blobs projected as read-only values."""

    model_config = ConfigDict(extra="forbid")

    case: RepairCase
    events: list[RepairCaseEvent] = Field(default_factory=list)
    source_payload: Any = None
    candidate_payload: Any = None
    capabilities: RepairWorkbenchCapabilities = Field(default_factory=RepairWorkbenchCapabilities)


__all__ = [
    "RepairArtifactSnapshot",
    "RepairApprovalRequest",
    "RepairAuthority",
    "RepairCandidate",
    "RepairCandidateOrigin",
    "RepairCase",
    "RepairCaseEvent",
    "RepairCaseEventType",
    "RepairCaseJobRequest",
    "RepairCaseDecisionRequest",
    "RepairCaseDetailView",
    "RepairCaseStatus",
    "RepairPatchRecord",
    "RepairCandidateEditRequest",
    "RepairManualAnnotationRequest",
    "RepairPublishRequest",
    "RepairPublishReceipt",
    "RepairRecoveryRequest",
    "RepairTransactionStatus",
    "RepairValidatorResult",
    "RepairVerificationBundle",
    "RepairVerificationRequest",
    "RepairSourceView",
    "RepairWorkbenchCapabilities",
]
