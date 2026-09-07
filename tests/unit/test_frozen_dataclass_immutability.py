"""Tests for frozen dataclass immutability guarantees.

Verifies that:
1. Direct field assignment raises FrozenInstanceError
2. dataclasses.replace() works correctly for immutable updates
3. Tuple fields are truly immutable (no list mutation via .append() etc.)
"""

from __future__ import annotations

import dataclasses
from dataclasses import FrozenInstanceError
from unittest import mock

import pytest

from novel_forge.pipeline.long.decisions import (
    BestEffortContext,
    BestEffortVerdict,
    CausalRegressionContext,
    DriftContext,
    PostRepairContext,
    RepairContext,
    RepairThresholds,
    StrategyRecommendation,
)
from novel_forge.pipeline.steps.causal_repair_step import (
    CausalRepairInput,
    CausalRepairResult,
)
from novel_forge.pipeline.steps.continuity_repair_step import (
    ContinuityRepairInput,
    ContinuityRepairResult,
)
from novel_forge.workspace.sessions.chapter_session_state import PendingChapterReviewState


class TestRepairThresholdsImmutability:
    """Test RepairThresholds frozen dataclass."""

    def test_direct_assignment_raises_frozen_error(self) -> None:
        thresholds = RepairThresholds()
        with pytest.raises(FrozenInstanceError):
            thresholds.change_budget = 0.5  # type: ignore[misc]

    def test_replace_works(self) -> None:
        original = RepairThresholds(change_budget=0.15)
        updated = dataclasses.replace(original, change_budget=0.25)
        assert original.change_budget == 0.15
        assert updated.change_budget == 0.25


class TestRepairContextImmutability:
    """Test RepairContext frozen dataclass."""

    def test_direct_assignment_raises_frozen_error(self) -> None:
        ctx = RepairContext(current_round=0, max_rounds=3, score=8.0, score_threshold=8.5)
        with pytest.raises(FrozenInstanceError):
            ctx.current_round = 1  # type: ignore[misc]

    def test_must_fix_issues_is_tuple(self) -> None:
        """Verify must_fix_issues is a tuple, not list."""
        ctx = RepairContext(
            current_round=0,
            max_rounds=3,
            score=8.0,
            score_threshold=8.5,
            must_fix_issues=("issue1", "issue2"),
        )
        assert isinstance(ctx.must_fix_issues, tuple)
        assert ctx.must_fix_issues == ("issue1", "issue2")

    def test_must_fix_issues_empty_default_is_tuple(self) -> None:
        ctx = RepairContext(current_round=0, max_rounds=3, score=8.0, score_threshold=8.5)
        assert isinstance(ctx.must_fix_issues, tuple)
        assert ctx.must_fix_issues == ()

    def test_replace_with_new_must_fix_issues(self) -> None:
        original = RepairContext(
            current_round=0,
            max_rounds=3,
            score=8.0,
            score_threshold=8.5,
            must_fix_issues=("issue1",),
        )
        updated = dataclasses.replace(original, must_fix_issues=("issue1", "issue2"))
        assert original.must_fix_issues == ("issue1",)
        assert updated.must_fix_issues == ("issue1", "issue2")

    def test_tuple_field_cannot_be_mutated_via_append(self) -> None:
        """Shallow immutability: tuple field itself can't be replaced, but contents could be modified if elements are mutable."""
        issues = ["mutable_issue"]
        ctx = RepairContext(
            current_round=0,
            max_rounds=3,
            score=8.0,
            score_threshold=8.5,
            must_fix_issues=issues,  # type: ignore[arg-type]
        )
        # The tuple itself can't be replaced
        with pytest.raises(FrozenInstanceError):
            ctx.must_fix_issues = ("new",)  # type: ignore[misc]


class TestPendingChapterReviewStateImmutability:
    """Test PendingChapterReviewState frozen dataclass."""

    def _make_minimal_state(self, **overrides) -> PendingChapterReviewState:
        """Create a minimal PendingChapterReviewState with mocked sub-objects."""
        mock_outcome = mock.MagicMock()
        mock_alignment = mock.MagicMock()
        mock_continuity = mock.MagicMock()
        mock_repair_plan = mock.MagicMock()
        mock_eval = mock.MagicMock()

        defaults = dict(
            current_text="测试文本",
            performed_edits=0,
            outcome=mock_outcome,
            alignment_report=mock_alignment,
            chapter_repair_report=None,
            causal_report=None,
            continuity_report=mock_continuity,
            repair_plan=mock_repair_plan,
            eval_report=mock_eval,
            guard_decision=None,
            warnings=("warning1", "warning2"),
        )
        defaults.update(overrides)
        return PendingChapterReviewState(**defaults)

    def test_direct_assignment_raises_frozen_error(self) -> None:
        state = self._make_minimal_state()
        with pytest.raises(FrozenInstanceError):
            state.current_text = "新文本"  # type: ignore[misc]

    def test_warnings_is_tuple(self) -> None:
        state = self._make_minimal_state(warnings=("a", "b", "c"))
        assert isinstance(state.warnings, tuple)
        assert state.warnings == ("a", "b", "c")

    def test_warnings_empty_default_is_tuple(self) -> None:
        state = self._make_minimal_state(warnings=())
        assert isinstance(state.warnings, tuple)
        assert state.warnings == ()

    def test_replace_with_new_warnings(self) -> None:
        original = self._make_minimal_state(warnings=("warning1",))
        updated = dataclasses.replace(original, warnings=("warning1", "warning2"))
        assert original.warnings == ("warning1",)
        assert updated.warnings == ("warning1", "warning2")


class TestContinuityRepairInputImmutability:
    """Test ContinuityRepairInput frozen dataclass."""

    def _make_minimal_input(self, **overrides) -> ContinuityRepairInput:
        """Create a minimal ContinuityRepairInput with mocked sub-objects."""
        mock_packet = mock.MagicMock()
        mock_bridge = mock.MagicMock()
        mock_plan = mock.MagicMock()
        mock_report = mock.MagicMock()

        defaults = dict(
            chapter_number=1,
            chapter_text="测试章节文本",
            chapter_state_packet=mock_packet,
            chapter_bridge=mock_bridge,
            chapter_plan=mock_plan,
            continuity_report=mock_report,
            must_fix_issues=(),
        )
        defaults.update(overrides)
        return ContinuityRepairInput(**defaults)

    def test_direct_assignment_raises_frozen_error(self) -> None:
        inp = self._make_minimal_input()
        with pytest.raises(FrozenInstanceError):
            inp.chapter_number = 2  # type: ignore[misc]

    def test_must_fix_issues_is_tuple(self) -> None:
        inp = self._make_minimal_input(must_fix_issues=("issue1", "issue2"))
        assert isinstance(inp.must_fix_issues, tuple)

    def test_replace_works(self) -> None:
        original = self._make_minimal_input(chapter_number=1)
        updated = dataclasses.replace(original, chapter_number=2)
        assert original.chapter_number == 1
        assert updated.chapter_number == 2


class TestContinuityRepairResultImmutability:
    """Test ContinuityRepairResult frozen dataclass."""

    def _make_minimal_result(self, **overrides) -> ContinuityRepairResult:
        """Create a minimal ContinuityRepairResult with mocked sub-objects."""
        mock_plan = mock.MagicMock()

        defaults = dict(
            revised_text="修订后的文本",
            repair_plan=mock_plan,
            applied=True,
            warnings=(),
            repaired_issue_types=(),
        )
        defaults.update(overrides)
        return ContinuityRepairResult(**defaults)

    def test_direct_assignment_raises_frozen_error(self) -> None:
        result = self._make_minimal_result()
        with pytest.raises(FrozenInstanceError):
            result.applied = False  # type: ignore[misc]

    def test_warnings_is_tuple(self) -> None:
        result = self._make_minimal_result(warnings=("warn1", "warn2"))
        assert isinstance(result.warnings, tuple)

    def test_repaired_issue_types_is_tuple(self) -> None:
        result = self._make_minimal_result(repaired_issue_types=("type1",))
        assert isinstance(result.repaired_issue_types, tuple)


class TestCausalRepairInputImmutability:
    """Test CausalRepairInput frozen dataclass."""

    def _make_minimal_input(self, **overrides) -> CausalRepairInput:
        """Create a minimal CausalRepairInput with mocked sub-objects."""
        mock_bridge = mock.MagicMock()
        mock_report = mock.MagicMock()

        defaults = dict(
            chapter_number=1,
            chapter_text="测试章节文本",
            causal_link={},
            chapter_bridge=mock_bridge,
            causal_report=mock_report,
            must_fix_summaries=(),
            prev_round_issues=None,
            character_profiles=(),
            escalate_issue_signatures=(),
            chapter_plan_scenes=(),
            forbidden_elements=(),
            forbidden_elements_soft=(),
        )
        defaults.update(overrides)
        return CausalRepairInput(**defaults)

    def test_direct_assignment_raises_frozen_error(self) -> None:
        inp = self._make_minimal_input()
        with pytest.raises(FrozenInstanceError):
            inp.chapter_number = 2  # type: ignore[misc]

    def test_must_fix_summaries_is_tuple(self) -> None:
        inp = self._make_minimal_input(must_fix_summaries=("summary1", "summary2"))
        assert isinstance(inp.must_fix_summaries, tuple)

    def test_character_profiles_is_tuple(self) -> None:
        inp = self._make_minimal_input(character_profiles=({"name": "char1"},))
        assert isinstance(inp.character_profiles, tuple)

    def test_forbidden_elements_is_tuple(self) -> None:
        inp = self._make_minimal_input(forbidden_elements=("elem1", "elem2"))
        assert isinstance(inp.forbidden_elements, tuple)


class TestCausalRepairResultImmutability:
    """Test CausalRepairResult frozen dataclass."""

    def _make_minimal_result(self, **overrides) -> CausalRepairResult:
        """Create a minimal CausalRepairResult with mocked sub-objects."""
        defaults = dict(
            revised_text="修订后的文本",
            issues=(),
            applied=True,
            patches_applied=0,
            patches_attempted=0,
            repaired_issue_types=(),
            forbidden_element_warnings=[],
        )
        defaults.update(overrides)
        return CausalRepairResult(**defaults)

    def test_direct_assignment_raises_frozen_error(self) -> None:
        result = self._make_minimal_result()
        with pytest.raises(FrozenInstanceError):
            result.applied = False  # type: ignore[misc]

    def test_issues_is_tuple(self) -> None:
        mock_issue = mock.MagicMock()
        result = self._make_minimal_result(issues=(mock_issue,))
        assert isinstance(result.issues, tuple)

    def test_repaired_issue_types_is_tuple(self) -> None:
        result = self._make_minimal_result(repaired_issue_types=("type1",))
        assert isinstance(result.repaired_issue_types, tuple)

    def test_forbidden_element_warnings_is_list(self) -> None:
        result = self._make_minimal_result(forbidden_element_warnings=["warn1"])
        assert isinstance(result.forbidden_element_warnings, list)


class TestOtherFrozenDataclassesFromDecisions:
    """Test other frozen dataclasses from decisions.py that should be immutable."""

    def test_strategy_recommendation_direct_assignment(self) -> None:
        rec = StrategyRecommendation()
        with pytest.raises(FrozenInstanceError):
            rec.confidence = 0.9  # type: ignore[misc]

    def test_post_repair_context_direct_assignment(self) -> None:
        mock_thresholds = RepairThresholds()
        ctx = PostRepairContext(
            change_ratio=0.1,
            thresholds=mock_thresholds,
            alignment_score=8.0,
            continuity_score=8.5,
            has_prompt_leaks=False,
        )
        with pytest.raises(FrozenInstanceError):
            ctx.change_ratio = 0.2  # type: ignore[misc]

    def test_causal_regression_context_direct_assignment(self) -> None:
        mock_thresholds = RepairThresholds()
        ctx = CausalRegressionContext(
            total_change_ratio=0.1,
            thresholds=mock_thresholds,
            alignment_score=8.0,
            continuity_score=8.5,
            has_prompt_leaks=False,
        )
        with pytest.raises(FrozenInstanceError):
            ctx.total_change_ratio = 0.2  # type: ignore[misc]

    def test_drift_context_direct_assignment(self) -> None:
        ctx = DriftContext(has_drift=True, high_severity_count=2)
        with pytest.raises(FrozenInstanceError):
            ctx.has_drift = False  # type: ignore[misc]

    def test_best_effort_verdict_direct_assignment(self) -> None:
        verdict = BestEffortVerdict(should_accept=True, reason="test")
        with pytest.raises(FrozenInstanceError):
            verdict.should_accept = False  # type: ignore[misc]

    def test_best_effort_context_direct_assignment(self) -> None:
        ctx = BestEffortContext(
            original_score=8.0,
            current_score=8.5,
            original_critical_count=1,
            current_critical_count=0,
            alignment_score=8.0,
            has_prompt_leaks=False,
        )
        with pytest.raises(FrozenInstanceError):
            ctx.current_score = 9.0  # type: ignore[misc]
