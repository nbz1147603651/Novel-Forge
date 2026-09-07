"""Regression tests for long-stage causal repair wrapper behavior."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_forge.core.schemas.chapter import CausalIssue, CausalValidationReport
from novel_forge.pipeline.long.decisions import RepairThresholds
from novel_forge.pipeline.long.stages.causal_repair import (
    _execute_causal_repair_loop,
    causal_repair_edit,
)
from novel_forge.pipeline.steps.causal_repair_step import CausalRepairResult


class _DummyTrace:
    total_tokens = 0


class _DummyStorage:
    def exists(self, _path: Path) -> bool:
        return False

    def load_json(self, _path: Path) -> dict:
        return {}

    def save_text(self, _path: Path, _text: str) -> None:
        return None


class _DummyLayout:
    def chapter_plan_path(self, chapter_number: int) -> Path:
        return Path(f"plans/chapter_{chapter_number:03d}_plan.json")

    def chapter_draft_path(self, chapter_number: int, version: int) -> Path:
        return Path(f"drafts/chapter_{chapter_number:03d}/v{version}_edited.md")


class _DummyRunner:
    def __init__(self) -> None:
        self._router = object()
        self._builder = object()
        self._settings = SimpleNamespace(temp_repair_causal=0.35)
        self._storage = _DummyStorage()
        self.memory_context = None
        self.steps: list[tuple[str, object]] = []

    def _on_step(self, step: str, payload: object) -> None:
        self.steps.append((step, payload))

    def has_memory_context(self) -> bool:
        return self.memory_context is not None


class _FakeCausalMemory:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.records: list[dict[str, object]] = []
        self._critique_index = {
            "sig-causal": SimpleNamespace(
                chapter_number=2,
                issue_type="causal_break",
                summary="事件缺少触发原因",
            )
        }

    async def search_similar_critiques(self, **kwargs):
        self.calls.append(kwargs)
        return [
            {
                "chapter_number": 1,
                "issue_type": "causal_break",
                "summary": "情报来源没有交代",
                "relevance_score": 0.9,
                "repair_attempts": [{"strategy": "window", "result": "success"}],
                "lesson_learned": "先补信息渠道，再让角色行动",
                "failure_pattern": "",
                "suggested_fix": "补充消息传递链",
            }
        ]

    def record_repair_result(self, signature: str, **kwargs) -> bool:
        self.records.append({"signature": signature, **kwargs})
        return True


@pytest.mark.asyncio
async def test_causal_repair_wrapper_passes_must_fix_summaries(monkeypatch) -> None:
    runner = _DummyRunner()
    bundle = SimpleNamespace(
        layout=_DummyLayout(),
        style_profile=None,
    )
    packet = SimpleNamespace(previous_chapter_ending="", character_profiles=[])
    bridge = SimpleNamespace(causal_link={})

    issue = CausalIssue(
        issue_type="event_without_cause",
        summary="黑衣人如何得知位置未交代",
        evidence="第27段黑衣人突然包围",
        fix_suggestion="补充信息传递链路",
        location="第27段",
        severity="medium",
    )
    causal_report = CausalValidationReport(causal_score=6.0, issues=[issue])
    captured: dict[str, object] = {}

    async def _fake_run_causal_repair(_step, payload):
        captured["payload"] = payload
        return CausalRepairResult(
            revised_text=payload.chapter_text,
            issues=[],
            applied=False,
            failure_reason="noop",
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.long.repair_causal.run_causal_repair",
        _fake_run_causal_repair,
    )

    text = await causal_repair_edit(
        runner,
        bundle=bundle,
        packet=packet,
        bridge=bridge,
        current_text="原正文",
        chapter_number=6,
        causal_report=causal_report,
        trace=_DummyTrace(),
        must_fix_summaries=[issue.summary],
    )

    assert text == "原正文"
    payload = captured["payload"]
    assert payload.must_fix_summaries == [issue.summary]


@pytest.mark.asyncio
async def test_causal_repair_wrapper_passes_memory_guidance(monkeypatch) -> None:
    runner = _DummyRunner()
    bundle = SimpleNamespace(layout=_DummyLayout(), style_profile=None)
    packet = SimpleNamespace(previous_chapter_ending="", character_profiles=[])
    bridge = SimpleNamespace(causal_link={})
    causal_report = CausalValidationReport(causal_score=6.0, issues=[])
    guidance = {
        "matched_issues": [{"chapter": 3, "summary": "情报来源缺失"}],
        "recommended_strategies": ["window"],
    }
    captured: dict[str, object] = {}

    async def _fake_run_causal_repair(_step, payload):
        captured["payload"] = payload
        return CausalRepairResult(
            revised_text=payload.chapter_text,
            issues=[],
            applied=False,
            failure_reason="noop",
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.long.repair_causal.run_causal_repair",
        _fake_run_causal_repair,
    )

    await causal_repair_edit(
        runner,
        bundle=bundle,
        packet=packet,
        bridge=bridge,
        current_text="原正文",
        chapter_number=6,
        causal_report=causal_report,
        trace=_DummyTrace(),
        memory_guidance=guidance,
    )

    payload = captured["payload"]
    assert payload.memory_guidance is guidance


@pytest.mark.asyncio
async def test_causal_repair_loop_skips_initial_validation_internal_error(monkeypatch) -> None:
    runner = _DummyRunner()
    bundle = SimpleNamespace(
        layout=_DummyLayout(),
        chapter_outline=SimpleNamespace(pov_character="林远", required_characters=[]),
    )
    packet = SimpleNamespace(previous_chapter_ending="", character_profiles=[])
    bridge = SimpleNamespace(causal_link={})

    async def _raise_validation_error(*_args, **_kwargs):
        raise RuntimeError("validation boom")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.causal_repair.run_causal_validation",
        _raise_validation_error,
    )

    result = await _execute_causal_repair_loop(
        runner,
        bundle,
        packet,
        bridge,
        SimpleNamespace(),
        runner._on_step,
        current_text="原正文",
        alignment_report=SimpleNamespace(alignment_score=8.0),
        continuity_report=SimpleNamespace(continuity_score=8.0, issues=[]),
        chapter_repair_report=None,
        chapter_number=2,
        trace=_DummyTrace(),
        repair_thresholds=RepairThresholds(change_budget=1.0),
        prev_chapter_ending="上一章",
        max_causal_rounds=1,
    )

    assert result.current_text == "原正文"
    assert result.causal_report is None
    assert result.rounds_used == 0
    assert any("validation boom" in warning for warning in result.causal_warnings)
    assert any(step == "causal_validation_internal_error" for step, _ in runner.steps)


@pytest.mark.asyncio
async def test_causal_repair_loop_keeps_text_on_internal_repair_error(monkeypatch) -> None:
    runner = _DummyRunner()
    bundle = SimpleNamespace(
        layout=_DummyLayout(),
        chapter_outline=SimpleNamespace(pov_character="林远", required_characters=[]),
    )
    packet = SimpleNamespace(previous_chapter_ending="", character_profiles=[])
    bridge = SimpleNamespace(causal_link={})
    issue = CausalIssue(
        issue_type="event_without_cause",
        severity="critical",
        location="第2段",
        summary="事件缺少触发原因",
        evidence="A突然发生",
        fix_suggestion="补充前置触发",
    )
    causal_report = CausalValidationReport(
        causal_score=4.0,
        summary="需修复",
        causal_link_verified=False,
        issues=[issue],
    )

    async def _raise_repair_error(*_args, **_kwargs):
        raise RuntimeError("repair boom")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.causal_repair.causal_repair_edit",
        _raise_repair_error,
    )

    result = await _execute_causal_repair_loop(
        runner,
        bundle,
        packet,
        bridge,
        SimpleNamespace(),
        runner._on_step,
        current_text="原正文",
        alignment_report=SimpleNamespace(alignment_score=8.0),
        continuity_report=SimpleNamespace(continuity_score=8.0, issues=[]),
        chapter_repair_report=None,
        chapter_number=2,
        trace=_DummyTrace(),
        repair_thresholds=RepairThresholds(change_budget=1.0),
        prev_chapter_ending="上一章",
        max_causal_rounds=1,
        initial_causal_report=causal_report,
    )

    assert result.current_text == "原正文"
    assert result.causal_report is causal_report
    assert result.repair_exhausted is True
    assert any("repair boom" in warning for warning in result.causal_warnings)
    assert any(step == "causal_repair_internal_error" for step, _ in runner.steps)


@pytest.mark.asyncio
async def test_causal_repair_loop_uses_and_records_memory_guidance(monkeypatch) -> None:
    runner = _DummyRunner()
    memory = _FakeCausalMemory()
    runner.memory_context = SimpleNamespace(episodic_memory=memory)
    bundle = SimpleNamespace(
        layout=_DummyLayout(),
        chapter_outline=SimpleNamespace(pov_character="林远", required_characters=[]),
    )
    packet = SimpleNamespace(previous_chapter_ending="", character_profiles=[])
    bridge = SimpleNamespace(causal_link={})
    issue = CausalIssue(
        issue_type="event_without_cause",
        severity="critical",
        location="第2段",
        summary="事件缺少触发原因",
        evidence="A突然发生",
        fix_suggestion="补充前置触发",
    )
    causal_report = CausalValidationReport(
        causal_score=4.0,
        summary="需修复",
        causal_link_verified=False,
        issues=[issue],
    )
    captured: dict[str, object] = {}

    async def _fake_repair(*_args, **kwargs):
        captured["memory_guidance"] = kwargs.get("memory_guidance")
        return "原正文。"

    async def _fake_recheck(*_args, **_kwargs):
        return CausalValidationReport(causal_score=8.4, summary="已修复", issues=[])

    async def _fake_post_checks(
        _runner,
        _bundle,
        _packet,
        _bridge,
        _plan,
        _before,
        _after,
        _chapter_number,
        alignment_report,
        continuity_report,
        chapter_repair_report,
        _trace,
        **_kwargs,
    ):
        return alignment_report, continuity_report, chapter_repair_report

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.causal_repair.causal_repair_edit",
        _fake_repair,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.causal_repair.run_causal_validation",
        _fake_recheck,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.dedup_pronoun.run_self_repetition_check",
        lambda _runner, text: (text, {}),
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.causal_repair.detect_drift",
        lambda *_args, **_kwargs: SimpleNamespace(has_drift=False, high_severity_count=0),
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.chapter_repair_support.run_post_repair_checks",
        _fake_post_checks,
    )

    result = await _execute_causal_repair_loop(
        runner,
        bundle,
        packet,
        bridge,
        SimpleNamespace(),
        runner._on_step,
        current_text="原正文",
        alignment_report=SimpleNamespace(alignment_score=8.0),
        continuity_report=SimpleNamespace(continuity_score=8.0, issues=[]),
        chapter_repair_report=None,
        chapter_number=2,
        trace=_DummyTrace(),
        repair_thresholds=RepairThresholds(change_budget=1.0),
        prev_chapter_ending="上一章",
        max_causal_rounds=1,
        initial_causal_report=causal_report,
    )

    assert result.current_text == "原正文。"
    assert captured["memory_guidance"]["matched_issues"][0]["chapter"] == 1
    assert memory.calls[0]["issue_type"] == "event_without_cause"
    assert memory.records[0]["signature"] == "sig-causal"
    assert memory.records[0]["result"] == "success"
    assert any(step == "causal_memory_guidance_added" for step, _ in runner.steps)
    assert any(step == "causal_repair_memory_results_recorded" for step, _ in runner.steps)


@pytest.mark.asyncio
async def test_causal_repair_loop_rolls_back_when_recheck_fails(monkeypatch) -> None:
    runner = _DummyRunner()
    bundle = SimpleNamespace(
        layout=_DummyLayout(),
        chapter_outline=SimpleNamespace(pov_character="林远", required_characters=[]),
    )
    packet = SimpleNamespace(previous_chapter_ending="", character_profiles=[])
    bridge = SimpleNamespace(causal_link={})
    issue = CausalIssue(
        issue_type="event_without_cause",
        severity="critical",
        location="第2段",
        summary="事件缺少触发原因",
        evidence="A突然发生",
        fix_suggestion="补充前置触发",
    )
    causal_report = CausalValidationReport(
        causal_score=4.0,
        summary="需修复",
        causal_link_verified=False,
        issues=[issue],
    )

    async def _fake_repair(*_args, **_kwargs):
        return "原正文"

    async def _raise_recheck_error(*_args, **_kwargs):
        raise RuntimeError("recheck boom")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.causal_repair.causal_repair_edit",
        _fake_repair,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.causal_repair.run_causal_validation",
        _raise_recheck_error,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.dedup_pronoun.run_self_repetition_check",
        lambda _runner, text: (text, {}),
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.causal_repair.detect_drift",
        lambda *_args, **_kwargs: SimpleNamespace(has_drift=False, high_severity_count=0),
    )

    result = await _execute_causal_repair_loop(
        runner,
        bundle,
        packet,
        bridge,
        SimpleNamespace(),
        runner._on_step,
        current_text="原正文",
        alignment_report=SimpleNamespace(alignment_score=8.0),
        continuity_report=SimpleNamespace(continuity_score=8.0, issues=[]),
        chapter_repair_report=None,
        chapter_number=2,
        trace=_DummyTrace(),
        repair_thresholds=RepairThresholds(change_budget=1.0),
        prev_chapter_ending="上一章",
        max_causal_rounds=1,
        initial_causal_report=causal_report,
    )

    assert result.current_text == "原正文"
    assert result.causal_report is causal_report
    assert result.repair_exhausted is True
    assert any("recheck boom" in warning for warning in result.causal_warnings)
    assert any(step == "causal_recheck_internal_error" for step, _ in runner.steps)
