"""ProjectIssueLedger — project-level quality issue tracking schema.

Tracks issues discovered by volume audit, book consistency, summary drift
detection, and manual annotations. Issues are persisted to
``states/issue_ledger.jsonl`` and consumed by subsequent chapter generation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from novel_forge.core.schemas.base import VersionedSchema

IssueCategory = Literal[
    "continuity",
    "character_state",
    "timeline",
    "summary_drift",
    "world_rule",
    "promise_overdue",
    "retrieval_quality",
    "other",
]

IssueSeverity = Literal["critical", "high", "medium", "low"]

IssueSource = Literal[
    "volume_audit",
    "book_consistency",
    "summary_drift",
    "quality_trend",
    "manual",
]

IssueStatus = Literal["open", "repairing", "resolved", "suppressed", "deferred"]


class ProjectIssueLedgerEntry(VersionedSchema):
    """A single quality issue tracked at the project level."""

    model_config = ConfigDict(extra="ignore")

    issue_id: str = Field(
        description="Unique issue ID, e.g. 'VA-V3-001' (volume audit) or 'BC-001' (book consistency)."
    )
    category: IssueCategory = Field(
        default="other",
        description="Issue category.",
    )
    severity: IssueSeverity = Field(
        default="medium",
        description="Issue severity level.",
    )
    source: IssueSource = Field(
        default="manual",
        description="Subsystem that discovered this issue.",
    )
    chapter_range: list[int] = Field(
        default_factory=list,
        description="Chapter numbers involved in this issue.",
    )
    status: IssueStatus = Field(
        default="open",
        description="Current resolution status.",
    )
    summary: str = Field(
        default="",
        description="Issue summary (≤ 200 chars).",
    )
    evidence: str = Field(
        default="",
        description="Evidence reference (≤ 500 chars).",
    )
    created_at: str = Field(
        default="",
        description="ISO timestamp when the issue was created.",
    )
    resolved_at: str | None = Field(
        default=None,
        description="ISO timestamp when the issue was resolved.",
    )
    resolved_chapter: int | None = Field(
        default=None,
        description="Chapter number when the issue was resolved.",
    )
    ttl_chapters: int = Field(
        default=50,
        ge=1,
        description="Auto-expire after this many chapters. If status unchanged, entry is archived.",
    )

    @property
    def is_active(self) -> bool:
        """Whether this issue is still actionable."""
        return self.status in ("open", "repairing")


class ProjectIssueLedger(VersionedSchema):
    """Snapshot of all active + archived issues (for serialization)."""

    model_config = ConfigDict(extra="ignore")

    active: list[ProjectIssueLedgerEntry] = Field(
        default_factory=list,
        description="Currently open/repairing issues.",
    )
    archived: list[ProjectIssueLedgerEntry] = Field(
        default_factory=list,
        description="Resolved/suppressed/expired issues.",
    )


__all__ = [
    "IssueCategory",
    "IssueSeverity",
    "IssueSource",
    "IssueStatus",
    "ProjectIssueLedger",
    "ProjectIssueLedgerEntry",
]
