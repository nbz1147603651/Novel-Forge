"""BookConsistencyVerifyResult — typed output for BookConsistencyVerifyStep."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _StrictBookConsistencyVerifyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidencePair(_StrictBookConsistencyVerifyModel):
    chapter_number: int
    evidence: str = ""
    claim: str = ""


class RepairScope(_StrictBookConsistencyVerifyModel):
    target: str = ""
    allowed_changes: list[str] = Field(default_factory=list)
    forbidden_changes: list[str] = Field(default_factory=list)
    preserve: list[str] = Field(default_factory=list)


class Postcondition(_StrictBookConsistencyVerifyModel):
    check: str = ""
    expected: str = ""


class VerifiedIssue(_StrictBookConsistencyVerifyModel):
    issue_id: str = ""
    status: str = ""
    description: str = ""
    severity: str = ""
    confidence: float = 0.0
    evidence: str = ""
    paragraph_index: int = 0
    paragraph_span: list[int] = Field(default_factory=list)
    location: str = ""
    anchor_type: str = ""
    location_confidence: float = 0.0
    evidence_pairs: list[EvidencePair] = Field(default_factory=list)
    adjudication_notes: str = ""
    repair_scope: RepairScope | None = None
    fix_mode: str = ""
    fix_action: str = ""
    postconditions: list[Postcondition] = Field(default_factory=list)
    rejection_reason: str = ""


class BookConsistencyVerifyResult(_StrictBookConsistencyVerifyModel):
    """Structured output from BookConsistencyVerifyStep.

    Replaces the previous dict[str, Any] return type with a typed model
    that mirrors the BOOK_CONSISTENCY_VERIFY format contract.
    """

    verified_issues: list[VerifiedIssue] = Field(default_factory=list)
