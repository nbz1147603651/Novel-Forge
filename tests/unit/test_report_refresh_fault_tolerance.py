"""Tests for fault tolerance in report refresh (asyncio.gather exception isolation)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from novel_forge.core.schemas.chapter import AlignmentReport, CausalValidationReport
from novel_forge.core.schemas.continuity import ContinuityReport
from novel_forge.pipeline.long.stages.report_refresh import (
    ReportRefreshFailurePolicy,
    _refresh_quality_reports_after_semantic_text_change_impl,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runner(events: list[tuple[str, Any]] | None = None) -> Any:
    """Build a minimal runner stub."""
    if events is None:
        events = []

    storage = MagicMock()
    storage.exists = MagicMock(return_value=False)
    storage.load_json = MagicMock(return_value={})
    storage.save_json = MagicMock()

    def _on_step(name: str, data: Any = None) -> None:
        events.append((name, data))

    return SimpleNamespace(
        _router=MagicMock(),
        _builder=MagicMock(),
        _settings=SimpleNamespace(
            temp_check_continuity=0.0,
            temp_post_repair_review=0.0,
            long_post_repair_review_independent_prompt="",
        ),
        _storage=storage,
        _on_step=_on_step,
    )


def _make_bundle() -> Any:
    layout = MagicMock()
    layout.alignment_report_path = MagicMock(return_value=None)
    layout.continuity_report_path = MagicMock(return_value=None)
    layout.causal_report_path = MagicMock(return_value=None)
    layout.reading_power_report_path = MagicMock(return_value=None)
    layout.root = None
    return SimpleNamespace(
        layout=layout,
        chapter_outline=None,
        chapter_source_slice=None,
        project_id="test",
    )


def _patch_planner_force_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the planner to always refresh."""
    from novel_forge.pipeline.long.stages.report_freshness import ReportFreshnessDecision

    def _fake_decide(dimension: str, **kwargs: Any) -> ReportFreshnessDecision:
        return ReportFreshnessDecision(
            dimension=dimension,
            action="refresh",
            reason="forced",
            report=None,
            source="forced",
            source_text_hash="hash",
            context_hash="ctx",
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.ReportRefreshPlanner",
        MagicMock(return_value=MagicMock(decide=_fake_decide)),
    )


def _patch_kernel_and_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch kernel composer and canon state hash to return stubs."""

    async def _fake_kernel(*args: Any, **kwargs: Any) -> None:
        return None

    async def _fake_hash(*args: Any, **kwargs: Any) -> str:
        return "h"

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.load_story_kernel_composer",
        _fake_kernel,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.load_canon_state_hash_for_bundle",
        _fake_hash,
    )


async def _run_refresh(
    runner: Any,
    bundle: Any,
    *,
    old_alignment: AlignmentReport | None = None,
    old_continuity: ContinuityReport | None = None,
    old_causal: CausalValidationReport | None = None,
    failure_policy: ReportRefreshFailurePolicy = ReportRefreshFailurePolicy.BEST_EFFORT,
) -> Any:
    """Convenience wrapper to call the impl function."""
    return await _refresh_quality_reports_after_semantic_text_change_impl(
        runner=runner,
        bundle=bundle,
        packet=MagicMock(),
        bridge=MagicMock(),
        plan=MagicMock(),
        current_text="chapter text",
        chapter_number=1,
        trace=SimpleNamespace(total_tokens=0),
        alignment_report=old_alignment or AlignmentReport(alignment_score=6.0),
        continuity_report=old_continuity or ContinuityReport(continuity_score=7.0, issues=[]),
        causal_report=old_causal or CausalValidationReport(causal_score=5.0, issues=[]),
        failure_policy=failure_policy,
        report_kinds=frozenset({"alignment", "continuity", "causal"}),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_continuity_refresh_failure_falls_back_to_old_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When continuity LLM call raises, the old report is returned."""
    events: list[tuple[str, Any]] = []
    runner = _make_runner(events)
    bundle = _make_bundle()
    old_continuity = ContinuityReport(continuity_score=7.5, issues=[])
    old_alignment = AlignmentReport(alignment_score=8.0)

    _patch_planner_force_refresh(monkeypatch)
    _patch_kernel_and_hash(monkeypatch)

    # Patch recheck_alignment to succeed
    async def _fake_recheck(*args: Any, **kwargs: Any) -> AlignmentReport:
        return AlignmentReport(alignment_score=9.0)

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.recheck_alignment",
        _fake_recheck,
    )

    # Patch ContinuityEvalStep to raise on construction (or on run)
    class _FailingStep:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def run(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("LLM returned malformed JSON")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.ContinuityEvalStep",
        _FailingStep,
    )

    # Also need to patch ContinuityEvalInput so construction doesn't fail
    # before reaching the step.run() call
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.ContinuityEvalInput",
        MagicMock(),
    )

    async def _fake_causal(*args: Any, **kwargs: Any) -> CausalValidationReport:
        return CausalValidationReport(causal_score=10.0, issues=[])

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.run_causal_validation",
        _fake_causal,
    )

    result = await _run_refresh(
        runner,
        bundle,
        old_alignment=old_alignment,
        old_continuity=old_continuity,
    )

    # Continuity should fall back to old report
    assert result.continuity_report is old_continuity
    # Alignment should have been refreshed
    assert result.alignment_report.alignment_score == 9.0
    # Verify fallback event was emitted
    fallback_events = [e for e in events if e[0] == "continuity_refresh_failed_fallback"]
    assert len(fallback_events) == 1
    assert "malformed JSON" in fallback_events[0][1]["error"]


@pytest.mark.asyncio
async def test_fail_closed_policy_propagates_refresh_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, Any]] = []
    runner = _make_runner(events)
    bundle = _make_bundle()
    _patch_planner_force_refresh(monkeypatch)
    _patch_kernel_and_hash(monkeypatch)

    async def _ok_alignment(*args: Any, **kwargs: Any) -> AlignmentReport:
        return AlignmentReport(alignment_score=9.0)

    class _FailingStep:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def run(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("continuity refresh unavailable")

    async def _ok_causal(*args: Any, **kwargs: Any) -> CausalValidationReport:
        return CausalValidationReport(causal_score=9.0, issues=[])

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.recheck_alignment",
        _ok_alignment,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.ContinuityEvalStep",
        _FailingStep,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.ContinuityEvalInput",
        MagicMock(),
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.run_causal_validation",
        _ok_causal,
    )

    with pytest.raises(RuntimeError, match="continuity refresh unavailable"):
        await _run_refresh(
            runner,
            bundle,
            failure_policy=ReportRefreshFailurePolicy.FAIL_CLOSED,
        )

    blocking = [event for event in events if event[0] == "continuity_refresh_failed_blocking"]
    assert blocking
    assert blocking[0][1]["action"] == "block_archive"


@pytest.mark.asyncio
async def test_alignment_refresh_failure_falls_back_to_old_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When alignment LLM call raises, the old report is returned."""
    events: list[tuple[str, Any]] = []
    runner = _make_runner(events)
    bundle = _make_bundle()
    old_alignment = AlignmentReport(alignment_score=6.0)

    _patch_planner_force_refresh(monkeypatch)
    _patch_kernel_and_hash(monkeypatch)

    async def _failing_alignment(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("JSONDecodeError: property name missing")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.recheck_alignment",
        _failing_alignment,
    )

    class _OkStep:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def run(self, *args: Any, **kwargs: Any) -> ContinuityReport:
            return ContinuityReport(continuity_score=9.0, issues=[])

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.ContinuityEvalStep",
        _OkStep,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.ContinuityEvalInput",
        MagicMock(),
    )

    async def _fake_causal(*args: Any, **kwargs: Any) -> CausalValidationReport:
        return CausalValidationReport(causal_score=10.0, issues=[])

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.run_causal_validation",
        _fake_causal,
    )

    result = await _run_refresh(runner, bundle, old_alignment=old_alignment)

    assert result.alignment_report is old_alignment
    assert result.continuity_report.continuity_score == 9.0
    fallback_events = [e for e in events if e[0] == "alignment_refresh_failed_fallback"]
    assert len(fallback_events) == 1


@pytest.mark.asyncio
async def test_causal_refresh_failure_falls_back_to_old_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When causal LLM call raises, the old report is returned."""
    events: list[tuple[str, Any]] = []
    runner = _make_runner(events)
    bundle = _make_bundle()
    old_causal = CausalValidationReport(causal_score=5.0, issues=[])

    _patch_planner_force_refresh(monkeypatch)
    _patch_kernel_and_hash(monkeypatch)

    async def _fake_alignment(*args: Any, **kwargs: Any) -> AlignmentReport:
        return AlignmentReport(alignment_score=9.0)

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.recheck_alignment",
        _fake_alignment,
    )

    class _OkStep:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def run(self, *args: Any, **kwargs: Any) -> ContinuityReport:
            return ContinuityReport(continuity_score=9.0, issues=[])

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.ContinuityEvalStep",
        _OkStep,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.ContinuityEvalInput",
        MagicMock(),
    )

    async def _failing_causal(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Model timeout")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.run_causal_validation",
        _failing_causal,
    )

    result = await _run_refresh(runner, bundle, old_causal=old_causal)

    assert result.causal_report is old_causal
    fallback_events = [e for e in events if e[0] == "causal_refresh_failed_fallback"]
    assert len(fallback_events) == 1


@pytest.mark.asyncio
async def test_all_dimensions_fail_simultaneously(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When all three dimensions fail, all fall back to old reports."""
    events: list[tuple[str, Any]] = []
    runner = _make_runner(events)
    bundle = _make_bundle()

    old_alignment = AlignmentReport(alignment_score=6.0)
    old_continuity = ContinuityReport(continuity_score=7.0, issues=[])
    old_causal = CausalValidationReport(causal_score=5.0, issues=[])

    _patch_planner_force_refresh(monkeypatch)
    _patch_kernel_and_hash(monkeypatch)

    async def _fail_alignment(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("alignment boom")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.recheck_alignment",
        _fail_alignment,
    )

    class _FailStep:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def run(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("continuity boom")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.ContinuityEvalStep",
        _FailStep,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.ContinuityEvalInput",
        MagicMock(),
    )

    async def _fail_causal(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("causal boom")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.run_causal_validation",
        _fail_causal,
    )

    result = await _run_refresh(
        runner,
        bundle,
        old_alignment=old_alignment,
        old_continuity=old_continuity,
        old_causal=old_causal,
    )

    # All should fall back to old reports, gather must not crash
    assert result.alignment_report is old_alignment
    assert result.continuity_report is old_continuity
    assert result.causal_report is old_causal

    # Verify all three fallback events emitted
    event_names = {e[0] for e in events}
    assert "alignment_refresh_failed_fallback" in event_names
    assert "continuity_refresh_failed_fallback" in event_names
    assert "causal_refresh_failed_fallback" in event_names


@pytest.mark.asyncio
async def test_all_dimensions_succeed_normally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normal success path is unaffected by the fault-tolerance wrapping."""
    events: list[tuple[str, Any]] = []
    runner = _make_runner(events)
    bundle = _make_bundle()

    _patch_planner_force_refresh(monkeypatch)
    _patch_kernel_and_hash(monkeypatch)

    new_alignment = AlignmentReport(alignment_score=9.5)

    async def _ok_alignment(*args: Any, **kwargs: Any) -> AlignmentReport:
        return new_alignment

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.recheck_alignment",
        _ok_alignment,
    )

    new_continuity = ContinuityReport(continuity_score=9.0, issues=[])

    class _OkStep:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def run(self, *args: Any, **kwargs: Any) -> ContinuityReport:
            return new_continuity

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.ContinuityEvalStep",
        _OkStep,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.ContinuityEvalInput",
        MagicMock(),
    )

    new_causal = CausalValidationReport(causal_score=10.0, issues=[])

    async def _ok_causal(*args: Any, **kwargs: Any) -> CausalValidationReport:
        return new_causal

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.report_refresh.run_causal_validation",
        _ok_causal,
    )

    result = await _run_refresh(runner, bundle)

    assert result.alignment_report.alignment_score == new_alignment.alignment_score
    assert result.continuity_report.continuity_score == new_continuity.continuity_score
    assert result.causal_report.causal_score == new_causal.causal_score
    assert result.alignment_report.source_text_hash == result.current_text_hash
    assert result.continuity_report.source_text_hash == result.current_text_hash
    assert result.causal_report.source_text_hash == result.current_text_hash

    # No fallback events should be emitted
    fallback_events = [e for e in events if "fallback" in e[0] or "exception" in e[0]]
    assert len(fallback_events) == 0
