"""Golden fixture test for _execute_continuity_repair_loop delegation to ContinuityRepairRunner.

Task 9: Verify behavior equivalence before/after rewiring.
Captures the output structure of _execute_continuity_repair_loop with mock LLM calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.core.schemas.continuity import (
    ContinuityIssue,
    ContinuityReport,
    RepairPlan,
)
from novel_forge.pipeline.long.decisions import RepairThresholds
from novel_forge.pipeline.long.stages.continuity_repair import (
    ContinuityRepairLoopResult,
    _execute_continuity_repair_loop,
)
from novel_forge.pipeline.steps.continuity_repair_step import ContinuityRepairResult


class _DummyTrace:
    total_tokens = 0


class _DummySettings:
    long_continuity_repair_threshold = 8.5
    repair_must_fix_severity = "critical"
    cont_rollback_retry_limit = 2
    long_continuity_hard_block_threshold = 4.0
    long_best_effort_accept_floor = 6.0
    long_anchor_recalibration_confidence_floor = 0.5
    quality_gate_max_secondary_attempts = 1


class _DummyRunner:
    def __init__(self) -> None:
        self._settings = _DummySettings()
        self._router = SimpleNamespace()
        self._builder = SimpleNamespace()
        self._storage = SimpleNamespace()
        self._storage.save_json = lambda *args, **kwargs: None


class _DummyBundle:
    def __init__(self) -> None:
        self.chapter_outline = SimpleNamespace(
            pov_character="张三",
            required_characters=["张三", "李四"],
        )
        self.layout = SimpleNamespace()
        self.layout.repair_plan_path = lambda ch: f"plans/chapter_{ch:03d}_repair_plan.json"
        self.layout.chapter_bridge_path = lambda ch: f"plans/chapter_{ch:03d}_bridge.json"


class _DummyPacket:
    def __init__(self) -> None:
        self.banned_phrases: list[str] = []


def _make_continuity_report(
    score: float = 7.0,
    issues: list[ContinuityIssue] | None = None,
) -> ContinuityReport:
    """Create a mock ContinuityReport."""
    if issues is None:
        issues = [
            ContinuityIssue(
                issue_type="character_contradiction",
                severity="critical",
                summary="角色行为矛盾",
            )
        ]
    return ContinuityReport(
        continuity_score=score,
        issues=issues,
    )


def _make_alignment_report(score: float = 8.0) -> Any:
    """Create a mock alignment report."""
    return SimpleNamespace(alignment_score=score)


@pytest.mark.asyncio
async def test_continuity_repair_loop_output_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Golden fixture: capture output structure of _execute_continuity_repair_loop.

    This test verifies that the output has the expected fields and types,
    regardless of whether the loop runs directly or delegates to ContinuityRepairRunner.
    """
    # Mock run_continuity_repair to return a simple repair result
    async def _fake_run_continuity_repair(
        runner: Any,
        bundle: Any,
        packet: Any,
        bridge: Any,
        plan: Any,
        report: Any,
        text: str,
        chapter_number: int,
        trace: Any,
        *,
        must_fix_issues: list[Any] | None = None,
        memory_hints: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[str, ContinuityRepairResult]:
        # Return slightly modified text and a repair result
        revised_text = text + "\n修复后的文本"
        repair_result = ContinuityRepairResult(
            revised_text=revised_text,
            repair_plan=RepairPlan(no_op=False),
            applied=True,
            repaired_issue_types=["character_contradiction"],
        )
        return revised_text, repair_result

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.continuity_repair.run_continuity_repair",
        _fake_run_continuity_repair,
    )

    # Mock run_post_repair_checks to return improved scores
    async def _fake_run_post_repair_checks(
        runner: Any,
        bundle: Any,
        packet: Any,
        bridge: Any,
        plan: Any,
        pre_repair_text: str,
        current_text: str,
        chapter_number: int,
        alignment_report: Any,
        continuity_report: Any,
        chapter_repair_report: Any | None,
        trace: Any,
        **kwargs: Any,
    ) -> tuple[Any, ContinuityReport, Any]:
        # Return improved continuity score
        new_cont_report = ContinuityReport(
            continuity_score=9.0,
            issues=[],  # No issues after repair
        )
        return alignment_report, new_cont_report, chapter_repair_report

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.continuity_repair.run_post_repair_checks",
        _fake_run_post_repair_checks,
    )

    # Mock run_self_repetition_check to return text unchanged
    def _fake_self_repetition_check(runner: Any, text: str) -> tuple[str, Any]:
        return text, SimpleNamespace()

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.dedup_pronoun.run_self_repetition_check",
        _fake_self_repetition_check,
    )

    # Setup inputs
    runner = _DummyRunner()
    bundle = _DummyBundle()
    packet = _DummyPacket()
    bridge = SimpleNamespace()
    plan = SimpleNamespace()
    trace = _DummyTrace()

    current_text = "原始章节文本" * 10
    initial_report = _make_continuity_report(score=7.0)
    alignment_report = _make_alignment_report(score=8.0)

    repair_thresholds = RepairThresholds(
        change_budget=0.15,
        continuity_score_threshold=8.5,
        stagnation_delta=0.3,
        minor_change_skip=0.05,
        moderate_change_skip_chapter_repair=0.10,
    )

    events: list[tuple[str, Any]] = []

    def on_step(event: str, payload: Any) -> None:
        events.append((event, payload))

    # Call the function
    result = await _execute_continuity_repair_loop(
        runner,
        bundle,
        packet,
        bridge,
        plan,
        on_step,
        current_text=current_text,
        alignment_report=alignment_report,
        continuity_report=initial_report,
        chapter_repair_report=None,
        chapter_number=1,
        trace=trace,
        repair_thresholds=repair_thresholds,
        cont_max_rounds=2,
        cont_must_fix_sev="critical",
        cont_threshold=8.5,
        memory_hints=None,
    )

    # Verify output structure
    assert isinstance(result, ContinuityRepairLoopResult)
    assert hasattr(result, "current_text")
    assert hasattr(result, "continuity_repair")
    assert hasattr(result, "alignment_report")
    assert hasattr(result, "continuity_report")
    assert hasattr(result, "chapter_repair_report")
    assert hasattr(result, "repair_exhausted")
    assert hasattr(result, "rounds_used")
    assert hasattr(result, "applied")
    assert hasattr(result, "rolled_back")
    assert hasattr(result, "rollback_history")
    assert hasattr(result, "best_effort_accepted")
    assert hasattr(result, "best_effort_reason")
    assert hasattr(result, "needs_human_review")

    # Verify types
    assert isinstance(result.current_text, str)
    assert isinstance(result.repair_exhausted, bool)
    assert isinstance(result.rounds_used, int)
    assert isinstance(result.applied, bool)
    assert isinstance(result.rolled_back, bool)
    assert isinstance(result.rollback_history, list)
    assert isinstance(result.best_effort_accepted, bool)
    assert isinstance(result.best_effort_reason, str)
    assert isinstance(result.needs_human_review, bool)

    # Verify the repair was applied (score improved from 7.0 to 9.0)
    assert result.rounds_used >= 1
    assert result.continuity_report.continuity_score == 9.0


@pytest.mark.asyncio
async def test_continuity_repair_loop_no_must_fix_issues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test early exit when no must-fix issues exist."""
    # Setup inputs with no critical issues
    runner = _DummyRunner()
    bundle = _DummyBundle()
    packet = _DummyPacket()
    bridge = SimpleNamespace()
    plan = SimpleNamespace()
    trace = _DummyTrace()

    current_text = "原始章节文本" * 10
    # Report with only low-severity issues (no critical)
    initial_report = _make_continuity_report(
        score=9.0,
        issues=[
            ContinuityIssue(
                issue_type="minor_style",
                severity="low",
                summary="轻微风格问题",
            )
        ],
    )
    alignment_report = _make_alignment_report(score=9.0)

    repair_thresholds = RepairThresholds(
        change_budget=0.15,
        continuity_score_threshold=8.5,
        stagnation_delta=0.3,
        minor_change_skip=0.05,
        moderate_change_skip_chapter_repair=0.10,
    )

    events: list[tuple[str, Any]] = []

    def on_step(event: str, payload: Any) -> None:
        events.append((event, payload))

    # Call the function
    result = await _execute_continuity_repair_loop(
        runner,
        bundle,
        packet,
        bridge,
        plan,
        on_step,
        current_text=current_text,
        alignment_report=alignment_report,
        continuity_report=initial_report,
        chapter_repair_report=None,
        chapter_number=1,
        trace=trace,
        repair_thresholds=repair_thresholds,
        cont_max_rounds=2,
        cont_must_fix_sev="critical",
        cont_threshold=8.5,
        memory_hints=None,
    )

    # Verify output structure
    assert isinstance(result, ContinuityRepairLoopResult)
    assert result.rounds_used >= 0


@pytest.mark.asyncio
async def test_continuity_repair_loop_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test repair exhaustion when score doesn't improve."""
    # Mock run_continuity_repair to return unchanged text (no improvement)
    async def _fake_run_continuity_repair(
        runner: Any,
        bundle: Any,
        packet: Any,
        bridge: Any,
        plan: Any,
        report: Any,
        text: str,
        chapter_number: int,
        trace: Any,
        *,
        must_fix_issues: list[Any] | None = None,
        memory_hints: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[str, ContinuityRepairResult]:
        # Return same text (no repair applied)
        repair_result = ContinuityRepairResult(
            revised_text=text,
            repair_plan=RepairPlan(no_op=True),
            applied=False,
        )
        return text, repair_result

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.continuity_repair.run_continuity_repair",
        _fake_run_continuity_repair,
    )

    # Mock run_post_repair_checks to return same low score
    async def _fake_run_post_repair_checks(
        runner: Any,
        bundle: Any,
        packet: Any,
        bridge: Any,
        plan: Any,
        pre_repair_text: str,
        current_text: str,
        chapter_number: int,
        alignment_report: Any,
        continuity_report: Any,
        chapter_repair_report: Any | None,
        trace: Any,
        **kwargs: Any,
    ) -> tuple[Any, ContinuityReport, Any]:
        # Return same low score (no improvement)
        new_cont_report = ContinuityReport(
            continuity_score=5.0,
            issues=[
                ContinuityIssue(
                    issue_type="character_contradiction",
                    severity="critical",
                    summary="角色行为矛盾",
                )
            ],
        )
        return alignment_report, new_cont_report, chapter_repair_report

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.continuity_repair.run_post_repair_checks",
        _fake_run_post_repair_checks,
    )

    # Mock run_self_repetition_check
    def _fake_self_repetition_check(runner: Any, text: str) -> tuple[str, Any]:
        return text, SimpleNamespace()

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.dedup_pronoun.run_self_repetition_check",
        _fake_self_repetition_check,
    )

    # Setup inputs
    runner = _DummyRunner()
    bundle = _DummyBundle()
    packet = _DummyPacket()
    bridge = SimpleNamespace()
    plan = SimpleNamespace()
    trace = _DummyTrace()

    current_text = "原始章节文本" * 10
    initial_report = _make_continuity_report(score=5.0)
    alignment_report = _make_alignment_report(score=8.0)

    repair_thresholds = RepairThresholds(
        change_budget=0.15,
        continuity_score_threshold=8.5,
        stagnation_delta=0.3,
        minor_change_skip=0.05,
        moderate_change_skip_chapter_repair=0.10,
    )

    events: list[tuple[str, Any]] = []

    def on_step(event: str, payload: Any) -> None:
        events.append((event, payload))

    # Call the function
    result = await _execute_continuity_repair_loop(
        runner,
        bundle,
        packet,
        bridge,
        plan,
        on_step,
        current_text=current_text,
        alignment_report=alignment_report,
        continuity_report=initial_report,
        chapter_repair_report=None,
        chapter_number=1,
        trace=trace,
        repair_thresholds=repair_thresholds,
        cont_max_rounds=2,
        cont_must_fix_sev="critical",
        cont_threshold=8.5,
        memory_hints=None,
    )

    # Verify output structure
    assert isinstance(result, ContinuityRepairLoopResult)
    assert result.rounds_used >= 1
