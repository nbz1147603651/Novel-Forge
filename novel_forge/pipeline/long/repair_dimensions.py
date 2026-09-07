"""Declarative coordination policies for the chapter repair dimension chain.

The review phase executes repair dimensions as an ordered chain
(continuity → causal → reading_power).  The per-dimension *run* logic lives in
``repair_orchestration/domains/`` (``run_*_repair_v2``); this module owns the
shared *coordination* vocabulary between those runs:

- total-rounds-cap gating (skip / compress events),
- post-run outcome absorption (text provenance, exhaustion, best-effort
  warnings, rounds accounting, cumulative change ratio).

Per-dimension differences are declared as data on :class:`RepairDimensionPolicy`
instead of being hand-coded inline in ``chapter_flow_review.py``.  Adding a new
dimension to the chain should only require a new policy constant — not another
copy of the scaffolding.

Event contracts (consumed by Desktop / logs) are preserved exactly:
``repair_dimension_skipped_total_cap`` and ``repair_dimension_rounds_capped``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from novel_forge.pipeline.long.chapter_flow_orchestrate import _record_text_change
from novel_forge.pipeline.long.stages.reading_power_repair import _text_change_ratio

if TYPE_CHECKING:
    from novel_forge.pipeline.long.chapter_flow_orchestrate import _ReviewPhaseState


@dataclass(frozen=True)
class RepairDimensionPolicy:
    """Coordination policy for one repair dimension in the chapter chain.

    Attributes:
        name: Dimension identifier used in telemetry events
            (``skipped_dimension`` / ``dimension`` payload keys).
        text_stage: Stage label recorded in ``state.text_change_history``.
        best_effort_label: Chinese prefix of the best-effort review warning
            (``f"{best_effort_label}已尽力接受：{reason}"``).
        compress_to_remaining: When True and the global rounds cap is not yet
            reached, clamp this dimension's rounds to the remaining budget and
            emit ``repair_dimension_rounds_capped``.  When False, dimensions
            keep their configured rounds until the cap is fully reached.
        propagate_exhausted: When True, a result with ``repair_exhausted``
            sets ``state.repair_exhausted`` (used by downstream cross-dimension
            coordination to reduce/skip later dimensions).
    """

    name: str
    text_stage: str
    best_effort_label: str
    compress_to_remaining: bool = False
    propagate_exhausted: bool = False


# The chapter repair chain, in execution order.  Policies are data; the
# sequencing and checkpoint saves remain in chapter_flow_review for now.
CONTINUITY_DIMENSION = RepairDimensionPolicy(
    name="continuity",
    text_stage="continuity_repair",
    best_effort_label="连续性修复",
    propagate_exhausted=True,
)
CAUSAL_DIMENSION = RepairDimensionPolicy(
    name="causal",
    text_stage="causal_repair",
    best_effort_label="因果链修复",
    compress_to_remaining=True,
)
READING_POWER_DIMENSION = RepairDimensionPolicy(
    name="reading_power",
    text_stage="reading_power_repair",
    best_effort_label="追读力修复",
)


def remaining_rounds(total_rounds_used: int, total_rounds_cap: int) -> int | None:
    """Rounds left under the global cap, or None when the cap is disabled."""
    if total_rounds_cap <= 0:
        return None
    return max(0, total_rounds_cap - total_rounds_used)


def emit_dimension_cap_skip(
    *,
    policy: RepairDimensionPolicy,
    total_rounds_used: int,
    total_rounds_cap: int,
    chapter_number: int,
    on_step: Any,
) -> None:
    """Emit the standard event when a dimension is skipped by the total cap."""
    on_step(
        "repair_dimension_skipped_total_cap",
        {
            "chapter": chapter_number,
            "total_rounds_used": total_rounds_used,
            "cap": total_rounds_cap,
            "skipped_dimension": policy.name,
        },
    )


def gate_dimension_rounds(
    *,
    policy: RepairDimensionPolicy,
    max_rounds: int,
    total_rounds_used: int,
    total_rounds_cap: int,
    chapter_number: int,
    on_step: Any,
) -> int:
    """Apply the global rounds cap to a dimension's configured max rounds.

    Returns the (possibly reduced) rounds the dimension may use.  Emits
    ``repair_dimension_skipped_total_cap`` when the cap is already reached and
    ``repair_dimension_rounds_capped`` when the policy compresses to the
    remaining budget.
    """
    if total_rounds_cap > 0 and total_rounds_used >= total_rounds_cap:
        emit_dimension_cap_skip(
            policy=policy,
            total_rounds_used=total_rounds_used,
            total_rounds_cap=total_rounds_cap,
            chapter_number=chapter_number,
            on_step=on_step,
        )
        return 0
    if policy.compress_to_remaining and total_rounds_cap > 0:
        remaining = remaining_rounds(total_rounds_used, total_rounds_cap)
        assert remaining is not None
        if max_rounds > remaining:
            on_step(
                "repair_dimension_rounds_capped",
                {
                    "chapter": chapter_number,
                    "dimension": policy.name,
                    "total_rounds_used": total_rounds_used,
                    "cap": total_rounds_cap,
                    "max_rounds": remaining,
                },
            )
            return remaining
    return max_rounds


def absorb_dimension_result(
    *,
    policy: RepairDimensionPolicy,
    state: _ReviewPhaseState,
    result: Any,
) -> None:
    """Absorb a repair-dimension loop result into the review phase state.

    Handles the accounting shared by every dimension: current-text handoff with
    provenance, exhaustion propagation (per policy), best-effort warning,
    global rounds accounting, and cumulative change ratio.  Dimension-specific
    report fields (``state.continuity_repair``, ``state.causal_report``, ...)
    remain the caller's responsibility.
    """
    before_text = state.current_text
    state.current_text = result.current_text
    if state.current_text != before_text:
        _record_text_change(
            state,
            stage=policy.text_stage,
            before_text=before_text,
            after_text=state.current_text,
            applied=True,
        )
    if policy.propagate_exhausted and getattr(result, "repair_exhausted", False):
        state.repair_exhausted = True
    if getattr(result, "best_effort_accepted", False):
        state.review_warnings.append(
            f"{policy.best_effort_label}已尽力接受：{result.best_effort_reason}"
        )
    state.total_repair_rounds_used += result.rounds_used
    state.cumulative_change_ratio = _text_change_ratio(state.baseline_text, state.current_text)


__all__ = [
    "CAUSAL_DIMENSION",
    "CONTINUITY_DIMENSION",
    "READING_POWER_DIMENSION",
    "RepairDimensionPolicy",
    "absorb_dimension_result",
    "emit_dimension_cap_skip",
    "gate_dimension_rounds",
    "remaining_rounds",
]
