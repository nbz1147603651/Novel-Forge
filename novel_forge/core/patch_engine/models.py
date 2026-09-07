"""Data models for PatchExecutorV2."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class FailureCode(str, enum.Enum):
    """Reason a single patch could not be applied."""

    NOT_FOUND = "not_found"
    AMBIGUOUS_MATCH = "ambiguous_match"
    STALE_SOURCE_HASH = "stale_source_hash"
    OUT_OF_WINDOW = "out_of_window"
    OVERLAP_CONFLICT = "overlap_conflict"
    GUARD_REJECTED = "guard_rejected"


class MatchPolicy(str, enum.Enum):
    """How many occurrences are acceptable for a patch original."""

    UNIQUE_REQUIRED = "unique_required"
    ALLOW_MULTI = "allow_multi"


@dataclass(frozen=True)
class PatchOperation:
    """A single patch to apply to chapter text.

    Fields mirror the LLM output format enriched with paragraph anchors from
    ``build_issue_windows``.
    """

    patch_id: str = ""
    issue_id: str = ""
    original: str = ""
    replacement: str = ""
    para_start: int | None = None
    para_end: int | None = None
    editable_para_start: int | None = None
    editable_para_end: int | None = None
    replace_all: bool = False
    match_policy: MatchPolicy = MatchPolicy.UNIQUE_REQUIRED
    source_text_hash: str = ""


@dataclass(frozen=True)
class PatchFailure:
    """Diagnostic record for a single failed patch."""

    patch_id: str
    failure_code: FailureCode
    detail: str = ""


@dataclass
class PatchApplyResult:
    """Batch result from PatchExecutorV2.apply_batch().

    Designed to be a drop-in enrichment over the old ``(revised_text, applied)``
    tuple while providing full observability.
    """

    revised_text: str
    applied_count: int = 0
    attempted_count: int = 0
    failed_items: list[PatchFailure] = field(default_factory=list)
    fallback: bool = False

    # ── Diagnostics ──
    unique_match_count: int = 0
    normalized_match_count: int = 0
    ambiguous_match_count: int = 0
    stale_reject_count: int = 0

    @property
    def has_failures(self) -> bool:
        return bool(self.failed_items)

    @property
    def success_rate(self) -> float:
        if self.attempted_count == 0:
            return 0.0
        return self.applied_count / self.attempted_count

    def failure_codes(self) -> set[FailureCode]:
        return {f.failure_code for f in self.failed_items}
