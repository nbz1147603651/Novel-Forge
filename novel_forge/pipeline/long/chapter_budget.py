"""Chapter-level soft budget controller (Phase 6).

Implements a tiered cost-priority queue for the review/repair pipeline:

- **Tier 1** (hard-reserved): hard-gate checks + critical/high repair —
  never skipped regardless of budget pressure.
- **Tier 2** (benefit-sorted): medium repair, sorted by historical
  ``cost_per_resolved_issue``.
- **Tier 3** (degradable): low repair, non-essential polish, duplicate
  evaluation — may be skipped when soft budget is exhausted.

Hard limits (daily/monthly ``SpendingTracker``) remain the final gate.
When the hard budget cannot cover Tier 1, the pipeline pauses with
``budget_blocked`` status instead of silently lowering quality gates.

This module is purely deterministic and has no LLM calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

_logger = logging.getLogger(__name__)


class BudgetTier(str, Enum):
    """Cost-priority tiers for chapter repair steps."""

    TIER1_HARD_RESERVED = "tier1_hard_reserved"
    TIER2_BENEFIT_SORTED = "tier2_benefit_sorted"
    TIER3_DEGRADABLE = "tier3_degradable"


class BudgetDecision(str, Enum):
    """Outcome of a budget check for one repair step."""

    ALLOW = "allow"
    SKIP_BUDGET_EXHAUSTED = "skip_budget_exhausted"
    BLOCK_BUDGET_BLOCKED = "block_budget_blocked"


@dataclass
class ChapterBudgetState:
    """Mutable running total for one chapter's soft budget."""

    spent_usd: float = 0.0
    soft_budget_usd: float = 0.0  # 0 = unlimited
    tier1_spent: float = 0.0
    tier2_spent: float = 0.0
    tier3_spent: float = 0.0
    blocked: bool = False
    blocked_task_type: str = ""
    estimated_required_usd: float | None = None

    @property
    def remaining_usd(self) -> float:
        if self.soft_budget_usd <= 0:
            return float("inf")
        return max(0.0, self.soft_budget_usd - self.spent_usd)

    @property
    def pressure(self) -> float:
        """0.0 = no pressure, 1.0 = budget fully consumed."""
        if self.soft_budget_usd <= 0:
            return 0.0
        return min(1.0, self.spent_usd / self.soft_budget_usd)


@dataclass(frozen=True)
class BudgetCheckResult:
    """Result of checking whether a step can proceed."""

    decision: BudgetDecision
    tier: BudgetTier
    reason: str = ""
    estimated_cost_usd: float = 0.0


def classify_tier(
    *,
    task_type: str,
    severity: str,
    is_hard_gate: bool = False,
    is_polish: bool = False,
) -> BudgetTier:
    """Classify a repair step into a budget tier."""
    if is_hard_gate or severity in ("critical", "high"):
        return BudgetTier.TIER1_HARD_RESERVED
    # Polish and low-severity are degradable regardless of other factors.
    if is_polish or severity == "low":
        return BudgetTier.TIER3_DEGRADABLE
    if severity == "medium":
        return BudgetTier.TIER2_BENEFIT_SORTED
    # Default: medium tier
    return BudgetTier.TIER2_BENEFIT_SORTED


def check_budget(
    state: ChapterBudgetState,
    *,
    tier: BudgetTier,
    estimated_cost_usd: float = 0.0,
    unknown_cost_reserve_usd: float = 0.05,
    cost_source: str = "reported",
) -> BudgetCheckResult:
    """Decide whether a step can proceed given the current budget state.

    Rules:
    - Tier 1 always allowed (hard-reserved).
    - Tier 2 allowed if remaining > 0 or budget is unlimited.
    - Tier 3 allowed only if pressure < 0.8 (20% headroom).
    - Unknown cost: use reserve, never allow economic routing.
    """
    # Unknown cost handling: use conservative reserve
    effective_cost = estimated_cost_usd
    if cost_source == "unknown":
        effective_cost = unknown_cost_reserve_usd

    # Tier 1: never blocked by soft budget
    if tier == BudgetTier.TIER1_HARD_RESERVED:
        return BudgetCheckResult(
            decision=BudgetDecision.ALLOW,
            tier=tier,
            reason="tier1_hard_reserved",
            estimated_cost_usd=effective_cost,
        )

    # Unlimited budget: always allow
    if state.soft_budget_usd <= 0:
        return BudgetCheckResult(
            decision=BudgetDecision.ALLOW,
            tier=tier,
            reason="unlimited_budget",
            estimated_cost_usd=effective_cost,
        )

    remaining = state.remaining_usd

    # Tier 3: only allow if < 80% consumed
    if tier == BudgetTier.TIER3_DEGRADABLE:
        if state.pressure >= 0.8:
            return BudgetCheckResult(
                decision=BudgetDecision.SKIP_BUDGET_EXHAUSTED,
                tier=tier,
                reason=f"pressure_{state.pressure:.1f}_skip_tier3",
                estimated_cost_usd=effective_cost,
            )

    # Tier 2: allow if remaining > 0
    if remaining <= 0:
        return BudgetCheckResult(
            decision=BudgetDecision.SKIP_BUDGET_EXHAUSTED,
            tier=tier,
            reason="budget_exhausted",
            estimated_cost_usd=effective_cost,
        )

    return BudgetCheckResult(
        decision=BudgetDecision.ALLOW,
        tier=tier,
        reason="within_budget",
        estimated_cost_usd=effective_cost,
    )


def record_spend(
    state: ChapterBudgetState,
    *,
    tier: BudgetTier,
    cost_usd: float,
    task_type: str = "",
) -> None:
    """Record actual spend against the budget."""
    state.spent_usd += cost_usd
    if tier == BudgetTier.TIER1_HARD_RESERVED:
        state.tier1_spent += cost_usd
    elif tier == BudgetTier.TIER2_BENEFIT_SORTED:
        state.tier2_spent += cost_usd
    else:
        state.tier3_spent += cost_usd


def check_hard_budget_block(
    state: ChapterBudgetState,
    *,
    hard_limit_usd: float,
    task_type: str,
    estimated_cost_usd: float,
) -> BudgetCheckResult:
    """Check if the hard (daily/monthly) budget can cover this step.

    Returns BLOCK_BUDGET_BLOCKED if the hard limit would be exceeded
    and this is a Tier 1 step (which cannot be skipped).
    """
    if hard_limit_usd <= 0:
        return BudgetCheckResult(
            decision=BudgetDecision.ALLOW,
            tier=BudgetTier.TIER1_HARD_RESERVED,
            reason="no_hard_limit",
        )

    projected = state.spent_usd + estimated_cost_usd
    if projected > hard_limit_usd:
        state.blocked = True
        state.blocked_task_type = task_type
        state.estimated_required_usd = estimated_cost_usd
        return BudgetCheckResult(
            decision=BudgetDecision.BLOCK_BUDGET_BLOCKED,
            tier=BudgetTier.TIER1_HARD_RESERVED,
            reason=f"hard_limit_{hard_limit_usd:.2f}_projected_{projected:.2f}",
            estimated_cost_usd=estimated_cost_usd,
        )

    return BudgetCheckResult(
        decision=BudgetDecision.ALLOW,
        tier=BudgetTier.TIER1_HARD_RESERVED,
        reason="within_hard_limit",
    )
