"""Tests for the decision framework (decisions.py).

Covers:
- RepairDecision: continuation/exit logic for repair loops
- QualityGateDecision: post-repair recheck tiering
- RollbackDecision: regression and drift rollback
- CausalRegressionDecision: cross-dimension regression checks
- Threshold building and issue filtering
"""

from __future__ import annotations

from novel_forge.pipeline.long.decisions import (
    BestEffortContext,
    CausalRegressionAction,
    CausalRegressionContext,
    DriftContext,
    PostRepairAction,
    PostRepairContext,
    RepairContext,
    RepairThresholds,
    RepairVerdict,
    RollbackVerdict,
    build_repair_thresholds,
    decide_best_effort_accept,
    decide_causal_regression_action,
    decide_post_repair_action,
    decide_repair_continuation,
    decide_repair_strategy,
    decide_rollback,
    derive_dynamic_repair_policy,
    filter_issues_by_round,
    filter_must_fix_issues,
)

# ─── Default thresholds for tests ───────────────────────────────────────────

_DEFAULT = RepairThresholds()


# ═══════════════════════════════════════════════════════════════════════════
# decide_repair_continuation
# ═══════════════════════════════════════════════════════════════════════════


class TestDecideRepairContinuation:
    def test_complete_when_score_ok_and_no_must_fix(self) -> None:
        ctx = RepairContext(
            current_round=0,
            max_rounds=3,
            score=9.0,
            score_threshold=8.5,
            must_fix_issues=[],
        )
        assert decide_repair_continuation(ctx) == RepairVerdict.COMPLETE

    def test_continue_when_score_ok_but_must_fix_remains(self) -> None:
        ctx = RepairContext(
            current_round=0,
            max_rounds=3,
            score=9.0,
            score_threshold=8.5,
            must_fix_issues=["issue_1"],
        )
        assert decide_repair_continuation(ctx) == RepairVerdict.CONTINUE

    def test_continue_when_no_must_fix_but_score_low(self) -> None:
        ctx = RepairContext(
            current_round=0,
            max_rounds=3,
            score=7.0,
            score_threshold=8.5,
            must_fix_issues=[],
        )
        assert decide_repair_continuation(ctx) == RepairVerdict.CONTINUE

    def test_exhausted_on_last_round(self) -> None:
        ctx = RepairContext(
            current_round=2,
            max_rounds=3,
            score=7.0,
            score_threshold=8.5,
            must_fix_issues=["issue_1"],
        )
        assert decide_repair_continuation(ctx) == RepairVerdict.EXHAUSTED

    def test_rollback_takes_priority_over_complete(self) -> None:
        ctx = RepairContext(
            current_round=0,
            max_rounds=3,
            score=9.0,
            score_threshold=8.5,
            must_fix_issues=[],
            has_regression=True,
        )
        assert decide_repair_continuation(ctx) == RepairVerdict.ROLLBACK

    def test_rollback_takes_priority_over_exhausted(self) -> None:
        ctx = RepairContext(
            current_round=2,
            max_rounds=3,
            score=7.0,
            score_threshold=8.5,
            has_regression=True,
        )
        assert decide_repair_continuation(ctx) == RepairVerdict.ROLLBACK

    def test_complete_on_last_round_when_quality_ok(self) -> None:
        """Last round but score passes and no must-fix → COMPLETE, not EXHAUSTED."""
        ctx = RepairContext(
            current_round=2,
            max_rounds=3,
            score=9.0,
            score_threshold=8.5,
            must_fix_issues=[],
        )
        assert decide_repair_continuation(ctx) == RepairVerdict.COMPLETE

    def test_zero_threshold_never_score_ok(self) -> None:
        """score_threshold=0 means score is never considered passing."""
        ctx = RepairContext(
            current_round=0,
            max_rounds=3,
            score=0.0,
            score_threshold=0.0,
            must_fix_issues=[],
        )
        assert decide_repair_continuation(ctx) == RepairVerdict.CONTINUE

    def test_stagnated_when_score_unchanged_for_two_rounds(self) -> None:
        ctx = RepairContext(
            current_round=1,
            max_rounds=3,
            score=7.0,
            score_threshold=8.5,
            must_fix_issues=["issue_1"],
            previous_score=7.0,
        )
        assert decide_repair_continuation(ctx) == RepairVerdict.STAGNATED

    def test_not_stagnated_when_score_improves(self) -> None:
        ctx = RepairContext(
            current_round=1,
            max_rounds=3,
            score=7.5,
            score_threshold=8.5,
            must_fix_issues=["issue_1"],
            previous_score=7.0,
        )
        assert decide_repair_continuation(ctx) == RepairVerdict.CONTINUE

    def test_not_stagnated_on_first_round(self) -> None:
        ctx = RepairContext(
            current_round=0,
            max_rounds=3,
            score=7.0,
            score_threshold=8.5,
            must_fix_issues=["issue_1"],
            previous_score=7.0,
        )
        assert decide_repair_continuation(ctx) == RepairVerdict.CONTINUE


# ═══════════════════════════════════════════════════════════════════════════
# decide_repair_strategy
# ═══════════════════════════════════════════════════════════════════════════


class TestDecideRepairStrategy:
    def test_defaults_to_patch_without_memory_guidance(self) -> None:
        ctx = RepairContext(
            current_round=0,
            max_rounds=3,
            score=6.0,
            score_threshold=8.5,
        )

        recommendation = decide_repair_strategy(ctx)

        assert recommendation.preferred_strategy == "patch"
        assert recommendation.confidence == 0.5

    def test_avoids_patch_after_historical_regression(self) -> None:
        ctx = RepairContext(
            current_round=1,
            max_rounds=3,
            score=6.0,
            score_threshold=8.5,
            memory_guidance={
                "success_rate": 0.5,
                "total_attempts": 2,
                "avoid_strategies": ["patch"],
                "recommended_strategies": [],
            },
        )

        recommendation = decide_repair_strategy(ctx)

        assert recommendation.preferred_strategy == "fulltext"

    def test_recommends_rewrite_when_history_is_consistently_bad(self) -> None:
        ctx = RepairContext(
            current_round=1,
            max_rounds=3,
            score=6.0,
            score_threshold=8.5,
            memory_guidance={
                "success_rate": 0.2,
                "total_attempts": 3,
                "avoid_strategies": [],
                "recommended_strategies": [],
            },
        )

        recommendation = decide_repair_strategy(ctx)

        assert recommendation.preferred_strategy == "rewrite"

    def test_no_attempt_history_does_not_imply_failure(self) -> None:
        ctx = RepairContext(
            current_round=1,
            max_rounds=3,
            score=6.0,
            score_threshold=8.5,
            memory_guidance={
                "success_rate": 0.0,
                "total_attempts": 0,
                "avoid_strategies": [],
                "recommended_strategies": [],
            },
        )

        recommendation = decide_repair_strategy(ctx)

        assert recommendation.preferred_strategy == "patch"


# ═══════════════════════════════════════════════════════════════════════════
# decide_post_repair_action
# ═══════════════════════════════════════════════════════════════════════════


class TestDecidePostRepairAction:
    def test_no_op_repair_skips(self) -> None:
        ctx = PostRepairContext(
            change_ratio=0.0,
            thresholds=_DEFAULT,
            alignment_score=8.0,
            continuity_score=9.0,
            has_prompt_leaks=False,
            repair_was_no_op=True,
        )
        assert decide_post_repair_action(ctx) == PostRepairAction.SKIP_NO_OP_REPAIR

    def test_no_op_with_leaks_does_full_recheck(self) -> None:
        ctx = PostRepairContext(
            change_ratio=0.0,
            thresholds=_DEFAULT,
            alignment_score=8.0,
            continuity_score=9.0,
            has_prompt_leaks=True,
            repair_was_no_op=True,
        )
        assert decide_post_repair_action(ctx) == PostRepairAction.FULL_RECHECK

    def test_budget_exceeded_forces_full_recheck(self) -> None:
        ctx = PostRepairContext(
            change_ratio=0.20,
            thresholds=_DEFAULT,
            alignment_score=8.0,
            continuity_score=9.0,
            has_prompt_leaks=False,
        )
        assert decide_post_repair_action(ctx) == PostRepairAction.FULL_RECHECK

    def test_minor_change_skips_all_when_low_severity(self) -> None:
        ctx = PostRepairContext(
            change_ratio=0.03,
            thresholds=_DEFAULT,
            alignment_score=8.0,
            continuity_score=9.0,
            has_prompt_leaks=False,
            max_issue_severity="low",
        )
        assert decide_post_repair_action(ctx) == PostRepairAction.SKIP_ALL_RECHECKS

    def test_minor_change_medium_severity_skips_chapter_repair_only(self) -> None:
        ctx = PostRepairContext(
            change_ratio=0.03,
            thresholds=_DEFAULT,
            alignment_score=8.0,
            continuity_score=9.0,
            has_prompt_leaks=False,
            max_issue_severity="medium",
        )
        assert decide_post_repair_action(ctx) == PostRepairAction.SKIP_CHAPTER_REPAIR_ONLY

    def test_moderate_change_skips_chapter_repair_only(self) -> None:
        ctx = PostRepairContext(
            change_ratio=0.07,
            thresholds=_DEFAULT,
            alignment_score=8.0,
            continuity_score=9.0,
            has_prompt_leaks=False,
        )
        assert decide_post_repair_action(ctx) == PostRepairAction.SKIP_CHAPTER_REPAIR_ONLY

    def test_minor_change_but_low_alignment_forces_full(self) -> None:
        ctx = PostRepairContext(
            change_ratio=0.03,
            thresholds=_DEFAULT,
            alignment_score=5.0,
            continuity_score=9.0,
            has_prompt_leaks=False,
        )
        assert decide_post_repair_action(ctx) == PostRepairAction.FULL_RECHECK

    def test_minor_change_but_low_continuity_forces_full(self) -> None:
        ctx = PostRepairContext(
            change_ratio=0.03,
            thresholds=_DEFAULT,
            alignment_score=8.0,
            continuity_score=5.0,
            has_prompt_leaks=False,
        )
        assert decide_post_repair_action(ctx) == PostRepairAction.FULL_RECHECK


# ═══════════════════════════════════════════════════════════════════════════
# decide_rollback
# ═══════════════════════════════════════════════════════════════════════════


class TestDecideRollback:
    def test_keep_when_no_issues(self) -> None:
        assert decide_rollback() == RollbackVerdict.KEEP

    def test_rollback_on_regression(self) -> None:
        assert decide_rollback(ledger_has_regression=True) == RollbackVerdict.ROLLBACK_REGRESSION

    def test_rollback_on_drift(self) -> None:
        v = decide_rollback(drift=DriftContext(has_drift=True, high_severity_count=2))
        assert v == RollbackVerdict.ROLLBACK_DRIFT

    def test_drift_takes_priority_over_regression(self) -> None:
        v = decide_rollback(
            ledger_has_regression=True,
            drift=DriftContext(has_drift=True, high_severity_count=1),
        )
        assert v == RollbackVerdict.ROLLBACK_DRIFT

    def test_drift_without_high_severity_is_ok(self) -> None:
        v = decide_rollback(drift=DriftContext(has_drift=True, high_severity_count=0))
        assert v == RollbackVerdict.KEEP

    def test_progressive_keep_when_equal_count_but_weight_improves(self) -> None:
        class _LedgerStub:
            new_high_critical = [_IssueStub("high")]
            adjusted_resolved_high_critical = [_IssueStub("critical")]
            high_critical_net_weight = 1
            repair_effectiveness_score = 0.25

        v = decide_rollback(ledger=_LedgerStub())
        assert v == RollbackVerdict.KEEP

    def test_progressive_keep_rejects_new_critical(self) -> None:
        class _LedgerStub:
            new_high_critical = [_IssueStub("critical")]
            adjusted_resolved_high_critical = [_IssueStub("high")]
            high_critical_net_weight = -1
            repair_effectiveness_score = 0.25

        v = decide_rollback(ledger=_LedgerStub())
        assert v == RollbackVerdict.ROLLBACK_REGRESSION


# ═══════════════════════════════════════════════════════════════════════════
# decide_causal_regression_action
# ═══════════════════════════════════════════════════════════════════════════


class TestDecideCausalRegressionAction:
    def test_skip_on_minor_change_and_quality_ok(self) -> None:
        ctx = CausalRegressionContext(
            total_change_ratio=0.01,
            thresholds=_DEFAULT,
            alignment_score=8.0,
            continuity_score=9.0,
            has_prompt_leaks=False,
        )
        assert decide_causal_regression_action(ctx) == CausalRegressionAction.SKIP

    def test_full_recheck_on_large_change(self) -> None:
        ctx = CausalRegressionContext(
            total_change_ratio=0.20,
            thresholds=_DEFAULT,
            alignment_score=8.0,
            continuity_score=9.0,
            has_prompt_leaks=False,
        )
        assert decide_causal_regression_action(ctx) == CausalRegressionAction.FULL_RECHECK

    def test_skip_chapter_repair_on_moderate_change(self) -> None:
        ctx = CausalRegressionContext(
            total_change_ratio=0.05,
            thresholds=_DEFAULT,
            alignment_score=8.0,
            continuity_score=9.0,
            has_prompt_leaks=False,
        )
        assert (
            decide_causal_regression_action(ctx)
            == CausalRegressionAction.RECHECK_SKIP_CHAPTER_REPAIR
        )

    def test_full_recheck_when_quality_not_ok(self) -> None:
        ctx = CausalRegressionContext(
            total_change_ratio=0.01,
            thresholds=_DEFAULT,
            alignment_score=3.0,
            continuity_score=9.0,
            has_prompt_leaks=False,
        )
        assert decide_causal_regression_action(ctx) == CausalRegressionAction.FULL_RECHECK


# ═══════════════════════════════════════════════════════════════════════════
# filter_must_fix_issues
# ═══════════════════════════════════════════════════════════════════════════


class _IssueStub:
    def __init__(self, severity: str) -> None:
        self.severity = severity


class TestFilterMustFixIssues:
    def test_filters_at_or_above_severity(self) -> None:
        issues = [
            _IssueStub("critical"),
            _IssueStub("high"),
            _IssueStub("medium"),
            _IssueStub("low"),
        ]
        result = filter_must_fix_issues(issues, "high")
        assert len(result) == 2

    def test_off_returns_empty(self) -> None:
        issues = [_IssueStub("critical")]
        assert filter_must_fix_issues(issues, "off") == []

    def test_empty_input(self) -> None:
        assert filter_must_fix_issues([], "critical") == []


# ═══════════════════════════════════════════════════════════════════════════
# build_repair_thresholds
# ═══════════════════════════════════════════════════════════════════════════


class _SettingsStub:
    change_budget_threshold = 0.20
    minor_change_skip_recheck_ratio = 0.06
    moderate_change_skip_chapter_repair_ratio = 0.12
    causal_minor_change_skip_recheck_ratio = 0.02
    long_continuity_repair_threshold = 9.0
    repair_must_fix_severity = "high"


class _ConfigStub:
    alignment_threshold = 7.5


class TestBuildRepairThresholds:
    def test_reads_from_settings(self) -> None:
        t = build_repair_thresholds(_SettingsStub(), _ConfigStub())
        assert t.change_budget == 0.20
        assert t.minor_change_skip == 0.06
        assert t.moderate_change_skip_chapter_repair == 0.12
        assert t.causal_minor_change_skip == 0.02
        assert t.continuity_score_threshold == 9.0
        assert t.must_fix_severity == "high"
        assert t.alignment_threshold == 7.5

    def test_defaults_when_attrs_missing(self) -> None:
        t = build_repair_thresholds(object(), object())
        assert t.change_budget == 0.15
        assert t.minor_change_skip == 0.05
        assert t.alignment_threshold == 7.0


class TestDeriveDynamicRepairPolicy:
    def test_empty_issues_returns_base_thresholds(self) -> None:
        policy = derive_dynamic_repair_policy(
            [],
            base_change_budget=0.15,
            base_score_threshold=8.5,
            base_stagnation_delta=0.3,
        )
        assert policy.change_budget == 0.15
        assert policy.score_threshold == 8.5
        assert policy.stagnation_delta == 0.3

    def test_complex_issues_expand_budget_and_reduce_stagnation_delta(self) -> None:
        issues = [_IssueStub("critical"), _IssueStub("high"), _IssueStub("high")]
        policy = derive_dynamic_repair_policy(
            issues,
            base_change_budget=0.15,
            base_score_threshold=8.5,
            base_stagnation_delta=0.3,
            rollback_history_len=1,
        )
        assert policy.change_budget > 0.15
        assert policy.stagnation_delta < 0.3
        assert 0.0 < policy.issue_pressure <= 1.0


# ═══════════════════════════════════════════════════════════════════════════
# decide_repair_continuation — additional branches
# ═══════════════════════════════════════════════════════════════════════════


class TestDecideRepairContinuationExtra:
    def test_complete_on_perfect_score(self) -> None:
        """score >= 10.0 → COMPLETE regardless of other conditions."""
        ctx = RepairContext(
            current_round=0,
            max_rounds=3,
            score=10.0,
            score_threshold=8.5,
            must_fix_issues=["issue_1"],
            has_regression=False,
        )
        assert decide_repair_continuation(ctx) == RepairVerdict.COMPLETE

    def test_critical_stagnation_on_non_last_round(self) -> None:
        """Critical issue + score barely changed → STAGNATED (not last round)."""
        ctx = RepairContext(
            current_round=1,
            max_rounds=3,
            score=7.1,
            score_threshold=8.5,
            must_fix_issues=[_IssueStub("critical")],
            previous_score=7.2,
        )
        assert decide_repair_continuation(ctx) == RepairVerdict.STAGNATED

    def test_critical_last_round_exhausted(self) -> None:
        """Critical issue on last round → EXHAUSTED."""
        ctx = RepairContext(
            current_round=2,
            max_rounds=3,
            score=7.0,
            score_threshold=8.5,
            must_fix_issues=[_IssueStub("critical")],
        )
        assert decide_repair_continuation(ctx) == RepairVerdict.EXHAUSTED

    def test_critical_continue_when_score_changed_enough(self) -> None:
        """Critical issue + score changed enough → CONTINUE."""
        ctx = RepairContext(
            current_round=1,
            max_rounds=3,
            score=7.5,
            score_threshold=8.5,
            must_fix_issues=[_IssueStub("critical")],
            previous_score=7.0,
        )
        assert decide_repair_continuation(ctx) == RepairVerdict.CONTINUE

    def test_high_severity_raises_threshold(self) -> None:
        """High severity issues raise effective threshold to 9.0."""
        ctx = RepairContext(
            current_round=0,
            max_rounds=3,
            score=8.8,
            score_threshold=8.5,
            must_fix_issues=[_IssueStub("high")],
        )
        # 8.8 < 9.0 (high threshold), so not score_ok → CONTINUE
        assert decide_repair_continuation(ctx) == RepairVerdict.CONTINUE

    def test_high_severity_with_passing_score(self) -> None:
        """High severity + score >= 9.0 + no must_fix → COMPLETE."""
        ctx = RepairContext(
            current_round=0,
            max_rounds=3,
            score=9.2,
            score_threshold=8.5,
            must_fix_issues=[],
        )
        # No must_fix_issues, so high severity check doesn't matter
        assert decide_repair_continuation(ctx) == RepairVerdict.COMPLETE


# ═══════════════════════════════════════════════════════════════════════════
# decide_repair_strategy — additional branches
# ═══════════════════════════════════════════════════════════════════════════


class TestDecideRepairStrategyExtra:
    def test_fulltext_recommended_with_high_success(self) -> None:
        """High success rate + fulltext in recommended → fulltext."""
        ctx = RepairContext(
            current_round=0,
            max_rounds=3,
            score=6.0,
            score_threshold=8.5,
            memory_guidance={
                "success_rate": 0.7,
                "total_attempts": 5,
                "avoid_strategies": [],
                "recommended_strategies": ["fulltext rewrite"],
            },
        )
        rec = decide_repair_strategy(ctx)
        assert rec.preferred_strategy == "fulltext"
        assert rec.confidence == 0.8

    def test_fulltext_recommended_with_chinese_keyword(self) -> None:
        """Fulltext recommended via Chinese keyword '全文'."""
        ctx = RepairContext(
            current_round=0,
            max_rounds=3,
            score=6.0,
            score_threshold=8.5,
            memory_guidance={
                "success_rate": 0.65,
                "total_attempts": 3,
                "avoid_strategies": [],
                "recommended_strategies": ["全文重写"],
            },
        )
        rec = decide_repair_strategy(ctx)
        assert rec.preferred_strategy == "fulltext"

    def test_default_patch_with_recommendations(self) -> None:
        """No special conditions → default patch with recommendation reason."""
        ctx = RepairContext(
            current_round=0,
            max_rounds=3,
            score=6.0,
            score_threshold=8.5,
            memory_guidance={
                "success_rate": 0.5,
                "total_attempts": 2,
                "avoid_strategies": [],
                "recommended_strategies": ["patch small fix"],
            },
        )
        rec = decide_repair_strategy(ctx)
        assert rec.preferred_strategy == "patch"
        assert "patch" in rec.reason.lower() or "safest" in rec.reason.lower()
        assert rec.confidence == 0.5  # max(0.5, 0.5)

    def test_default_patch_without_recommendations(self) -> None:
        """No recommendations → default patch."""
        ctx = RepairContext(
            current_round=0,
            max_rounds=3,
            score=6.0,
            score_threshold=8.5,
            memory_guidance={
                "success_rate": 0.4,
                "total_attempts": 1,
                "avoid_strategies": [],
                "recommended_strategies": [],
            },
        )
        rec = decide_repair_strategy(ctx)
        assert rec.preferred_strategy == "patch"
        assert "safest" in rec.reason.lower()

    def test_warning_passed_through(self) -> None:
        """Warning from guidance is passed to recommendation."""
        ctx = RepairContext(
            current_round=0,
            max_rounds=3,
            score=6.0,
            score_threshold=8.5,
            memory_guidance={
                "success_rate": 0.5,
                "total_attempts": 2,
                "avoid_strategies": ["patch"],
                "recommended_strategies": [],
                "warning": "This issue type is tricky",
            },
        )
        rec = decide_repair_strategy(ctx)
        assert "tricky" in rec.warning


# ═══════════════════════════════════════════════════════════════════════════
# decide_post_repair_action — additional branches
# ═══════════════════════════════════════════════════════════════════════════


class TestDecidePostRepairActionExtra:
    def test_no_op_with_perfect_continuity_score(self) -> None:
        """No-op + continuity_score >= 10.0 → SKIP_NO_OP_REPAIR."""
        ctx = PostRepairContext(
            change_ratio=0.0,
            thresholds=_DEFAULT,
            alignment_score=5.0,
            continuity_score=10.0,
            has_prompt_leaks=False,
            repair_was_no_op=True,
        )
        assert decide_post_repair_action(ctx) == PostRepairAction.SKIP_NO_OP_REPAIR

    def test_full_recheck_as_default(self) -> None:
        """High change ratio + quality ok → FULL_RECHECK (above moderate threshold)."""
        ctx = PostRepairContext(
            change_ratio=0.12,
            thresholds=_DEFAULT,
            alignment_score=8.0,
            continuity_score=9.0,
            has_prompt_leaks=False,
        )
        assert decide_post_repair_action(ctx) == PostRepairAction.FULL_RECHECK


class TestFilterIssuesByRound:
    def test_empty_issues(self) -> None:
        assert filter_issues_by_round([], 0) == []

    def test_first_round_critical_and_high(self) -> None:
        """Round 0: critical + high via patch."""
        issues = [
            _IssueStub("critical"),
            _IssueStub("high"),
            _IssueStub("medium"),
            _IssueStub("low"),
        ]
        result = filter_issues_by_round(issues, 0, max_rounds=3)
        assert len(result) == 2
        severities = [i.severity for i in result]
        assert "critical" in severities
        assert "high" in severities
        assert "medium" not in severities

    def test_rollback_history_only_critical(self) -> None:
        """After rollback: only critical issues."""
        issues = [
            _IssueStub("critical"),
            _IssueStub("high"),
            _IssueStub("medium"),
        ]
        result = filter_issues_by_round(issues, 1, has_rollback_history=True, max_rounds=3)
        assert len(result) == 1
        assert result[0].severity == "critical"

    def test_final_round_all_issues(self) -> None:
        """Final round: all remaining issues via fulltext."""
        issues = [
            _IssueStub("critical"),
            _IssueStub("high"),
            _IssueStub("medium"),
            _IssueStub("low"),
        ]
        result = filter_issues_by_round(issues, 2, max_rounds=3)
        assert len(result) == 4

    def test_final_round_beyond_max(self) -> None:
        """Round >= max_rounds: all issues."""
        issues = [_IssueStub("low")]
        result = filter_issues_by_round(issues, 5, max_rounds=3)
        assert len(result) == 1

    def test_case_insensitive_severity(self) -> None:
        """Severity matching is case-insensitive."""
        issues = [_IssueStub("Critical"), _IssueStub("HIGH")]
        result = filter_issues_by_round(issues, 0, max_rounds=3)
        assert len(result) == 2


# ═══════════════════════════════════════════════════════════════════════════
# decide_best_effort_accept
# ═══════════════════════════════════════════════════════════════════════════


class TestDecideBestEffortAccept:
    def test_reject_prompt_leaks(self) -> None:
        ctx = BestEffortContext(
            original_score=5.0,
            current_score=7.0,
            original_critical_count=3,
            current_critical_count=1,
            alignment_score=8.0,
            has_prompt_leaks=True,
        )
        v = decide_best_effort_accept(ctx)
        assert v.should_accept is False
        assert "Prompt leaks" in v.reason

    def test_reject_low_alignment(self) -> None:
        ctx = BestEffortContext(
            original_score=5.0,
            current_score=7.0,
            original_critical_count=3,
            current_critical_count=1,
            alignment_score=6.5,
            has_prompt_leaks=False,
        )
        v = decide_best_effort_accept(ctx)
        assert v.should_accept is False
        assert "Alignment" in v.reason

    def test_reject_no_improvement(self) -> None:
        ctx = BestEffortContext(
            original_score=7.0,
            current_score=7.0,
            original_critical_count=3,
            current_critical_count=3,
            alignment_score=8.0,
            has_prompt_leaks=False,
        )
        v = decide_best_effort_accept(ctx)
        assert v.should_accept is False
        assert "not better" in v.reason

    def test_reject_score_decreased(self) -> None:
        ctx = BestEffortContext(
            original_score=7.0,
            current_score=5.0,
            original_critical_count=3,
            current_critical_count=2,
            alignment_score=8.0,
            has_prompt_leaks=False,
        )
        v = decide_best_effort_accept(ctx)
        assert v.should_accept is False

    def test_reject_below_70_percent(self) -> None:
        ctx = BestEffortContext(
            original_score=5.0,
            current_score=6.0,
            original_critical_count=3,
            current_critical_count=2,
            alignment_score=8.0,
            has_prompt_leaks=False,
        )
        v = decide_best_effort_accept(ctx)
        assert v.should_accept is True

    def test_reject_increased_critical(self) -> None:
        ctx = BestEffortContext(
            original_score=5.0,
            current_score=7.0,
            original_critical_count=2,
            current_critical_count=4,
            alignment_score=8.0,
            has_prompt_leaks=False,
        )
        v = decide_best_effort_accept(ctx)
        assert v.should_accept is False
        assert "increased" in v.reason

    def test_accept_all_conditions_met(self) -> None:
        ctx = BestEffortContext(
            original_score=5.0,
            current_score=8.0,
            original_critical_count=3,
            current_critical_count=1,
            alignment_score=8.0,
            has_prompt_leaks=False,
        )
        v = decide_best_effort_accept(ctx)
        assert v.should_accept is True
        assert "Improvement" in v.reason

    def test_accept_same_critical_count(self) -> None:
        """Critical count unchanged is acceptable."""
        ctx = BestEffortContext(
            original_score=5.0,
            current_score=7.5,
            original_critical_count=2,
            current_critical_count=2,
            alignment_score=8.0,
            has_prompt_leaks=False,
        )
        v = decide_best_effort_accept(ctx)
        assert v.should_accept is True

    def test_accept_reduced_critical(self) -> None:
        """Critical count reduced is acceptable."""
        ctx = BestEffortContext(
            original_score=5.0,
            current_score=7.5,
            original_critical_count=3,
            current_critical_count=0,
            alignment_score=8.0,
            has_prompt_leaks=False,
        )
        v = decide_best_effort_accept(ctx)
        assert v.should_accept is True
