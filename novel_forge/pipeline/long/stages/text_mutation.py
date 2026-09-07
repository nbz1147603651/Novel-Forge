"""Text mutation contract for impact-aware report refresh decisions.

Defines ``TextMutationKind``, ``ReviewDimension``, ``TextMutation``,
and the central ``MUTATION_IMPACT`` mapping table.

Callers construct a :class:`TextMutation` with a ``kind`` enum value.
The ``affected_dimensions`` property is derived from the central mapping
— callers **cannot** under-report impact by specifying dimensions directly.

Conservative rules:
- ``UNKNOWN`` kind -> ``ALL_DIMENSIONS`` (same as current behaviour).
- ``mutation is None`` -> keep current ``stale_reason`` refresh path.
- Multiple mutations -> union of ``affected_dimensions``.
- Text / context hash remain the ultimate correctness anchor.
- ``TextMutation`` can only *narrow* refresh scope; it never bypasses
  ``FAIL_CLOSED`` at the archive gate.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

# ── Review dimensions ────────────────────────────────────────────────────────


class ReviewDimension(str, enum.Enum):
    """Quality-report dimensions that can be independently refreshed."""

    CONTINUITY = "continuity"
    CAUSAL = "causal"
    ALIGNMENT = "alignment"
    READING_POWER = "reading_power"
    CHAPTER_REPAIR = "chapter_repair"
    GUARD_COMPLIANCE = "guard_compliance"


ALL_DIMENSIONS: frozenset[ReviewDimension] = frozenset(ReviewDimension)

# ── Mutation kinds ───────────────────────────────────────────────────────────


class TextMutationKind(str, enum.Enum):
    """Exhaustive catalogue of text-mutation sources.

    Each kind maps to a fixed ``affected_dimensions`` set via
    :data:`MUTATION_IMPACT`.  Adding a new source requires extending both
    this enum and the mapping table.
    """

    PRONOUN_MECHANICAL_FIX = "pronoun_mechanical_fix"
    PRONOUN_REFERENT_REWRITE = "pronoun_referent_rewrite"
    EXACT_DUPLICATE_REMOVAL = "exact_duplicate_removal"
    SEMANTIC_DEDUP_REWRITE = "semantic_dedup_rewrite"
    CONTINUITY_REPAIR_PATCH = "continuity_repair_patch"
    CONTINUITY_REPAIR_FULLTEXT = "continuity_repair_fulltext"
    CAUSAL_REPAIR = "causal_repair"
    READING_POWER_REPAIR = "reading_power_repair"
    OPENING_GUARD_PATCH = "opening_guard_patch"
    KNOWLEDGE_BOUNDARY_REPAIR = "knowledge_boundary_repair"
    WORLD_RULE_REPAIR = "world_rule_repair"
    CARRY_FORWARD_REPAIR = "carry_forward_repair"
    CONTRACT_EXECUTION_REPAIR = "contract_execution_repair"
    HUMANIZE_LAYER = "humanize_layer"
    POST_REPAIR_POLISH = "post_repair_polish"
    WORD_COUNT_POLISH = "word_count_polish"
    WAVE = "wave"
    UNKNOWN = "unknown"


# ── Central impact mapping ───────────────────────────────────────────────────

MUTATION_IMPACT: dict[TextMutationKind, frozenset[ReviewDimension]] = {
    TextMutationKind.PRONOUN_MECHANICAL_FIX: frozenset(),
    TextMutationKind.PRONOUN_REFERENT_REWRITE: frozenset(
        {ReviewDimension.CONTINUITY}
    ),
    TextMutationKind.EXACT_DUPLICATE_REMOVAL: frozenset(),
    TextMutationKind.SEMANTIC_DEDUP_REWRITE: frozenset(
        {ReviewDimension.CONTINUITY}
    ),
    TextMutationKind.CONTINUITY_REPAIR_PATCH: frozenset(
        {ReviewDimension.CONTINUITY}
    ),
    TextMutationKind.CONTINUITY_REPAIR_FULLTEXT: frozenset(
        {ReviewDimension.CONTINUITY, ReviewDimension.ALIGNMENT, ReviewDimension.CAUSAL}
    ),
    TextMutationKind.CAUSAL_REPAIR: frozenset(
        {ReviewDimension.CAUSAL, ReviewDimension.CONTINUITY}
    ),
    TextMutationKind.READING_POWER_REPAIR: frozenset(
        {
            ReviewDimension.READING_POWER,
            ReviewDimension.ALIGNMENT,
            ReviewDimension.CAUSAL,
        }
    ),
    TextMutationKind.OPENING_GUARD_PATCH: frozenset(
        {ReviewDimension.CONTINUITY, ReviewDimension.ALIGNMENT}
    ),
    TextMutationKind.KNOWLEDGE_BOUNDARY_REPAIR: frozenset(
        {ReviewDimension.CONTINUITY, ReviewDimension.CAUSAL}
    ),
    TextMutationKind.WORLD_RULE_REPAIR: frozenset(
        {ReviewDimension.CONTINUITY, ReviewDimension.CAUSAL}
    ),
    TextMutationKind.CARRY_FORWARD_REPAIR: frozenset(
        {ReviewDimension.CONTINUITY}
    ),
    TextMutationKind.CONTRACT_EXECUTION_REPAIR: frozenset(
        {ReviewDimension.CONTINUITY, ReviewDimension.CHAPTER_REPAIR}
    ),
    TextMutationKind.HUMANIZE_LAYER: ALL_DIMENSIONS,
    TextMutationKind.POST_REPAIR_POLISH: ALL_DIMENSIONS,
    TextMutationKind.WORD_COUNT_POLISH: ALL_DIMENSIONS,
    TextMutationKind.WAVE: ALL_DIMENSIONS,
    # Conservative default: unknown mutations invalidate everything.
    TextMutationKind.UNKNOWN: ALL_DIMENSIONS,
}

# ── TextMutation value object ────────────────────────────────────────────────


@dataclass(frozen=True)
class TextMutation:
    """A single text-change event with centrally-derived impact scope.

    Callers specify ``kind``; ``affected_dimensions`` is a derived property.
    """

    kind: TextMutationKind
    ticket_ids: tuple[str, ...] = field(default_factory=tuple)
    issue_ids: tuple[str, ...] = field(default_factory=tuple)

    @property
    def affected_dimensions(self) -> frozenset[ReviewDimension]:
        return MUTATION_IMPACT[self.kind]


# ── Multi-mutation merge helper ──────────────────────────────────────────────


def merge_mutations(mutations: list[TextMutation]) -> frozenset[ReviewDimension]:
    """Union of ``affected_dimensions`` across multiple mutations."""
    dims: set[ReviewDimension] = set()
    for m in mutations:
        dims |= m.affected_dimensions
    return frozenset(dims)


def mutation_report_kinds(
    mutations: list[TextMutation],
    *,
    allow_narrowing: bool = True,
) -> tuple[str, ...]:
    """Derive ``report_kinds`` for :func:`ReviewReportService.ensure_current`.

    When *allow_narrowing* is ``False`` (e.g. at the archive gate), returns
    all dimension values regardless of mutation scope.
    """
    if not allow_narrowing:
        return tuple(d.value for d in ALL_DIMENSIONS)
    dims = merge_mutations(mutations)
    return tuple(d.value for d in dims)
