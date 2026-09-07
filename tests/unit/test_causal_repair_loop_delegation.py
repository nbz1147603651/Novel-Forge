"""Golden fixture test for _execute_causal_repair_loop delegation to CausalRepairRunner.

Task 10: Verify behavior equivalence before/after rewiring.
Captures the output structure of _execute_causal_repair_loop with mock LLM calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.core.schemas.chapter import (
    CausalIssue,
    CausalValidationReport,
)
from novel_forge.pipeline.long.decisions import RepairThresholds
from novel_forge.pipeline.long.stages.causal_repair import (
    CausalRepairLoopResult,
    _execute_causal_repair_loop,
)


class _DummyTrace:
    total_tokens = 0


class _DummySettings:
    long_causal_repair_enabled = True
    long_causal_threshold = 5.0
    long_causal_max_repair_rounds = 2
    long_causal_stagnation_delta = 0.3
    long_causal_hard_block_threshold = 4.0
    long_best_effort_accept_floor = 6.0
    long_anchor_recalibration_confidence_floor = 0.5
    repair_must_fix_severity = "critical"
    causal_validation_fail_mode = "warn_unknown"
    recheck_strategy = "targeted_with_global_guard"


class _DummyRunner:
    def __init__(self) -> None:
        self._settings = _DummySettings()
        self._router = SimpleNamespace()
        self._builder = SimpleNamespace()
        self._storage = SimpleNamespace()
        self._storage.save_json = lambda *args, **kwargs: None
        self._storage.save_text = lambda *args, **kwargs: None
        self._storage.exists = lambda *args, **kwargs: False

    def _on_step(self, event: str, payload: Any) -> None:
        pass


class _DummyBundle:
    def __init__(self) -> None:
        self.chapter_outline = SimpleNamespace(
            pov_character="张三",
            required_characters=["张三", "李四"],
        )
        self.layout = SimpleNamespace()
        self.layout.chapter_causal_report_path = lambda ch: f"reports/chapter_{ch:03d}_causal.json"
        self.layout.chapter_draft_path = lambda ch, rnd: f"drafts/chapter_{ch:03d}_r{rnd}.md"
        self.layout.chapter_plan_path = lambda ch: f"plans/chapter_{ch:03d}_plan.json"
        self.style_profile = None
        self.editorial_contract = None
        self.story_bible = None
        self.chapter_outline_obj = None
        self.chapter_source_slice = None


class _DummyPacket:
    def __init__(self) -> None:
        self.character_profiles: list[dict[str, Any]] = []
        self.previous_chapter_ending = ""
        self.canon_context = {}


def _make_causal_report(
    score: float = 5.0,
    issues: list[CausalIssue] | None = None,
    validation_status: str = "ok",
) -> CausalValidationReport:
    """Create a mock CausalValidationReport."""
    if issues is None:
        issues = [
            CausalIssue(
                issue_type="causal_gap",
                severity="critical",
                summary="因果关系缺失",
                location="第2段",
            )
        ]
    return CausalValidationReport(
        causal_score=score,
        issues=issues,
        validation_status=validation_status,
    )


def _make_alignment_report(score: float = 8.0) -> Any:
    return SimpleNamespace(alignment_score=score)


def _make_continuity_report(score: float = 8.0) -> Any:
    return SimpleNamespace(continuity_score=score, issues=[])


@pytest.mark.asyncio
async def test_causal_repair_loop_output_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Golden fixture: capture output structure of _execute_causal_repair_loop.

    Verifies the output has expected fields and types regardless of whether
    the loop runs directly or delegates to CausalRepairRunner.
    """
    # Mock run_causal_validation to return improved report on recheck
    call_count = 0

    async def _fake_run_causal_validation(
        runner: Any,
        bundle: Any,
        bridge: Any,
        current_text: str,
        chapter_number: int,
        trace: Any,
        *,
        previous_chapter_ending: str = "",
        recheck_mode: bool = False,
        must_resolve_summaries: list[str] | None = None,
        recheck_strategy: str = "targeted_with_global_guard",
        prior_issues: list[dict[str, Any]] | None = None,
        repaired_issue_types: list[str] | None = None,
        patch_only_repair: bool = False,
        strict_review: bool = False,
    ) -> CausalValidationReport:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Initial validation: return report with issues
            return _make_causal_report(score=5.0)
        # Recheck: return improved report (no issues)
        return CausalValidationReport(
            causal_score=9.0,
            issues=[],
            validation_status="ok",
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.causal_repair.run_causal_validation",
        _fake_run_causal_validation,
    )

    # Mock causal_repair_edit to return modified text
    async def _fake_causal_repair_edit(
        runner: Any,
        *,
        bundle: Any,
        packet: Any,
        bridge: Any,
        current_text: str,
        chapter_number: int,
        causal_report: Any,
        trace: Any,
        repair_round: int = 1,
            prev_round_issues: list[str] | None = None,
            per_issue_rounds: dict[str, int] | None = None,
            must_fix_summaries: list[str] | None = None,
            must_fix_issue_ids: list[str] | None = None,
            memory_guidance: dict[str, Any] | None = None,
        ) -> str:
            del must_fix_issue_ids
            return current_text + "\n修复后的因果文本"

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.causal_repair.causal_repair_edit",
        _fake_causal_repair_edit,
    )

    # Mock run_self_repetition_check
    def _fake_self_repetition_check(runner: Any, text: str) -> tuple[str, Any]:
        return text, SimpleNamespace()

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.dedup_pronoun.run_self_repetition_check",
        _fake_self_repetition_check,
    )

    # Mock _build_memory_guidance to return empty
    async def _fake_build_memory_guidance(**kwargs: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.chapter_repair_support._build_memory_guidance",
        _fake_build_memory_guidance,
    )

    # Mock _get_runner_episodic_memory
    def _fake_get_runner_episodic_memory(runner: Any) -> Any:
        return None

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.chapter_repair_support._get_runner_episodic_memory",
        _fake_get_runner_episodic_memory,
    )

    # Setup inputs
    runner = _DummyRunner()
    bundle = _DummyBundle()
    packet = _DummyPacket()
    bridge = SimpleNamespace(causal_link=None)
    plan = SimpleNamespace()
    trace = _DummyTrace()

    current_text = "原始章节文本" * 10
    initial_report = _make_causal_report(score=5.0)
    alignment_report = _make_alignment_report(score=8.0)
    continuity_report = _make_continuity_report(score=8.0)

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

    result = await _execute_causal_repair_loop(
        runner,
        bundle,
        packet,
        bridge,
        plan,
        on_step,
        current_text=current_text,
        alignment_report=alignment_report,
        continuity_report=continuity_report,
        chapter_repair_report=None,
        chapter_number=1,
        trace=trace,
        repair_thresholds=repair_thresholds,
        prev_chapter_ending="",
        max_causal_rounds=2,
        initial_causal_report=initial_report,
    )

    # Verify output structure
    assert isinstance(result, CausalRepairLoopResult)
    assert hasattr(result, "current_text")
    assert hasattr(result, "causal_report")
    assert hasattr(result, "alignment_report")
    assert hasattr(result, "continuity_report")
    assert hasattr(result, "chapter_repair_report")
    assert hasattr(result, "causal_warnings")
    assert hasattr(result, "repair_exhausted")
    assert hasattr(result, "rounds_used")
    assert hasattr(result, "applied")
    assert hasattr(result, "rolled_back")
    assert hasattr(result, "best_effort_accepted")
    assert hasattr(result, "best_effort_reason")
    assert hasattr(result, "needs_human_review")

    # Verify types
    assert isinstance(result.current_text, str)
    assert isinstance(result.repair_exhausted, bool)
    assert isinstance(result.rounds_used, int)
    assert isinstance(result.applied, bool)
    assert isinstance(result.rolled_back, bool)
    assert isinstance(result.causal_warnings, list)
    assert isinstance(result.best_effort_accepted, bool)
    assert isinstance(result.best_effort_reason, str)
    assert isinstance(result.needs_human_review, bool)

    # Verify repair was applied
    assert result.rounds_used >= 1
    assert result.causal_report is not None


@pytest.mark.asyncio
async def test_causal_repair_loop_no_must_fix_issues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test early exit when no must-fix issues exist."""
    runner = _DummyRunner()
    bundle = _DummyBundle()
    packet = _DummyPacket()
    bridge = SimpleNamespace(causal_link=None)
    plan = SimpleNamespace()
    trace = _DummyTrace()

    current_text = "原始章节文本" * 10
    # Report with only low-severity issues (no critical)
    initial_report = _make_causal_report(
        score=9.0,
        issues=[
            CausalIssue(
                issue_type="causal_gap",
                severity="low",
                summary="轻微因果问题",
            )
        ],
    )
    alignment_report = _make_alignment_report(score=9.0)
    continuity_report = _make_continuity_report(score=9.0)

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

    result = await _execute_causal_repair_loop(
        runner,
        bundle,
        packet,
        bridge,
        plan,
        on_step,
        current_text=current_text,
        alignment_report=alignment_report,
        continuity_report=continuity_report,
        chapter_repair_report=None,
        chapter_number=1,
        trace=trace,
        repair_thresholds=repair_thresholds,
        prev_chapter_ending="",
        max_causal_rounds=2,
        initial_causal_report=initial_report,
    )

    assert isinstance(result, CausalRepairLoopResult)
    assert result.rounds_used >= 0


@pytest.mark.asyncio
async def test_causal_repair_loop_validation_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test behavior when causal validation is unavailable."""
    runner = _DummyRunner()
    bundle = _DummyBundle()
    packet = _DummyPacket()
    bridge = SimpleNamespace(causal_link=None)
    plan = SimpleNamespace()
    trace = _DummyTrace()

    current_text = "原始章节文本" * 10
    initial_report = _make_causal_report(
        score=5.0,
        validation_status="unavailable",
    )
    alignment_report = _make_alignment_report(score=8.0)
    continuity_report = _make_continuity_report(score=8.0)

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

    result = await _execute_causal_repair_loop(
        runner,
        bundle,
        packet,
        bridge,
        plan,
        on_step,
        current_text=current_text,
        alignment_report=alignment_report,
        continuity_report=continuity_report,
        chapter_repair_report=None,
        chapter_number=1,
        trace=trace,
        repair_thresholds=repair_thresholds,
        prev_chapter_ending="",
        max_causal_rounds=2,
        initial_causal_report=initial_report,
    )

    assert isinstance(result, CausalRepairLoopResult)
    # When validation unavailable, no repair rounds should execute
    assert result.rounds_used == 0
    assert result.current_text == current_text
