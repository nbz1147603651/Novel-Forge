"""Tests for chapter-level soft budget controller."""

from __future__ import annotations

from novel_forge.pipeline.long.chapter_budget import (
    BudgetDecision,
    BudgetTier,
    ChapterBudgetState,
    check_budget,
    check_hard_budget_block,
    classify_tier,
    record_spend,
)


class TestClassifyTier:
    def test_critical_is_tier1(self):
        assert classify_tier(task_type="t", severity="critical") == BudgetTier.TIER1_HARD_RESERVED

    def test_high_is_tier1(self):
        assert classify_tier(task_type="t", severity="high") == BudgetTier.TIER1_HARD_RESERVED

    def test_hard_gate_is_tier1(self):
        assert classify_tier(task_type="t", severity="low", is_hard_gate=True) == BudgetTier.TIER1_HARD_RESERVED

    def test_medium_is_tier2(self):
        assert classify_tier(task_type="t", severity="medium") == BudgetTier.TIER2_BENEFIT_SORTED

    def test_low_is_tier3(self):
        assert classify_tier(task_type="t", severity="low") == BudgetTier.TIER3_DEGRADABLE

    def test_polish_is_tier3(self):
        assert classify_tier(task_type="t", severity="medium", is_polish=True) == BudgetTier.TIER3_DEGRADABLE

    def test_unknown_defaults_to_tier2(self):
        assert classify_tier(task_type="t", severity="") == BudgetTier.TIER2_BENEFIT_SORTED


class TestCheckBudget:
    def test_tier1_always_allowed(self):
        state = ChapterBudgetState(spent_usd=100.0, soft_budget_usd=50.0)
        result = check_budget(state, tier=BudgetTier.TIER1_HARD_RESERVED, estimated_cost_usd=10.0)
        assert result.decision == BudgetDecision.ALLOW
        assert result.reason == "tier1_hard_reserved"

    def test_unlimited_budget_always_allows(self):
        state = ChapterBudgetState(spent_usd=100.0, soft_budget_usd=0.0)
        result = check_budget(state, tier=BudgetTier.TIER2_BENEFIT_SORTED, estimated_cost_usd=10.0)
        assert result.decision == BudgetDecision.ALLOW
        assert result.reason == "unlimited_budget"

    def test_tier3_skipped_at_high_pressure(self):
        state = ChapterBudgetState(spent_usd=45.0, soft_budget_usd=50.0)  # 90% pressure
        result = check_budget(state, tier=BudgetTier.TIER3_DEGRADABLE, estimated_cost_usd=1.0)
        assert result.decision == BudgetDecision.SKIP_BUDGET_EXHAUSTED
        assert "skip_tier3" in result.reason

    def test_tier3_allowed_below_threshold(self):
        state = ChapterBudgetState(spent_usd=30.0, soft_budget_usd=50.0)  # 60% pressure
        result = check_budget(state, tier=BudgetTier.TIER3_DEGRADABLE, estimated_cost_usd=1.0)
        assert result.decision == BudgetDecision.ALLOW

    def test_tier2_skipped_when_exhausted(self):
        state = ChapterBudgetState(spent_usd=50.0, soft_budget_usd=50.0)  # 100%
        result = check_budget(state, tier=BudgetTier.TIER2_BENEFIT_SORTED, estimated_cost_usd=1.0)
        assert result.decision == BudgetDecision.SKIP_BUDGET_EXHAUSTED

    def test_tier2_allowed_within_budget(self):
        state = ChapterBudgetState(spent_usd=20.0, soft_budget_usd=50.0)
        result = check_budget(state, tier=BudgetTier.TIER2_BENEFIT_SORTED, estimated_cost_usd=5.0)
        assert result.decision == BudgetDecision.ALLOW

    def test_unknown_cost_uses_reserve(self):
        state = ChapterBudgetState(spent_usd=10.0, soft_budget_usd=50.0)
        result = check_budget(
            state,
            tier=BudgetTier.TIER2_BENEFIT_SORTED,
            estimated_cost_usd=0.0,
            cost_source="unknown",
            unknown_cost_reserve_usd=0.05,
        )
        assert result.decision == BudgetDecision.ALLOW
        assert result.estimated_cost_usd == 0.05


class TestRecordSpend:
    def test_records_per_tier(self):
        state = ChapterBudgetState(soft_budget_usd=100.0)
        record_spend(state, tier=BudgetTier.TIER1_HARD_RESERVED, cost_usd=5.0)
        record_spend(state, tier=BudgetTier.TIER2_BENEFIT_SORTED, cost_usd=3.0)
        record_spend(state, tier=BudgetTier.TIER3_DEGRADABLE, cost_usd=1.0)
        assert state.spent_usd == 9.0
        assert state.tier1_spent == 5.0
        assert state.tier2_spent == 3.0
        assert state.tier3_spent == 1.0

    def test_remaining_decreases(self):
        state = ChapterBudgetState(spent_usd=0.0, soft_budget_usd=50.0)
        assert state.remaining_usd == 50.0
        record_spend(state, tier=BudgetTier.TIER2_BENEFIT_SORTED, cost_usd=20.0)
        assert state.remaining_usd == 30.0

    def test_pressure_increases(self):
        state = ChapterBudgetState(spent_usd=0.0, soft_budget_usd=100.0)
        assert state.pressure == 0.0
        record_spend(state, tier=BudgetTier.TIER1_HARD_RESERVED, cost_usd=50.0)
        assert state.pressure == 0.5


class TestHardBudgetBlock:
    def test_no_hard_limit_allows(self):
        state = ChapterBudgetState(spent_usd=100.0)
        result = check_hard_budget_block(
            state, hard_limit_usd=0.0, task_type="t", estimated_cost_usd=10.0,
        )
        assert result.decision == BudgetDecision.ALLOW

    def test_within_hard_limit_allows(self):
        state = ChapterBudgetState(spent_usd=5.0)
        result = check_hard_budget_block(
            state, hard_limit_usd=20.0, task_type="t", estimated_cost_usd=10.0,
        )
        assert result.decision == BudgetDecision.ALLOW

    def test_exceeds_hard_limit_blocks(self):
        state = ChapterBudgetState(spent_usd=15.0)
        result = check_hard_budget_block(
            state, hard_limit_usd=20.0, task_type="REPAIR_CAUSAL", estimated_cost_usd=10.0,
        )
        assert result.decision == BudgetDecision.BLOCK_BUDGET_BLOCKED
        assert state.blocked is True
        assert state.blocked_task_type == "REPAIR_CAUSAL"
        assert state.estimated_required_usd == 10.0
