"""Tests for continuity repair score-gate behavior in quality stage."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_forge.core.schemas.continuity import (
    ChapterBridge,
    ChapterStatePacket,
    ContinuityIssue,
    ContinuityReport,
    RepairPlan,
)
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.core.schemas.story_state import ChapterExitState
from novel_forge.pipeline.long.decisions import RepairThresholds
from novel_forge.pipeline.long.stages.continuity_repair import (
    _execute_continuity_repair_loop,
    run_continuity_repair,
)
from novel_forge.pipeline.steps.continuity_repair_step import ContinuityRepairResult


class _DummyStorage:
    def __init__(self) -> None:
        self.saved: list[tuple[Path, dict]] = []

    def save_json(self, path: Path, payload: dict) -> None:
        self.saved.append((path, payload))


class _DummyLayout:
    def repair_plan_path(self, chapter_number: int) -> Path:
        return Path(f"plans/chapter_{chapter_number:03d}_repair_plan.json")

    def chapter_bridge_path(self, chapter_number: int) -> Path:
        return Path(f"plans/chapter_{chapter_number:03d}_bridge.json")


class _DummyRunner:
    def __init__(self, *, threshold: float = 9.0, must_fix: str = "high") -> None:
        self._router = object()
        self._builder = object()
        self._settings = SimpleNamespace(
            long_continuity_repair_threshold=threshold,
            repair_must_fix_severity=must_fix,
        )
        self._storage = _DummyStorage()
        self.events: list[tuple[str, object]] = []

    def _on_step(self, step: str, payload: object) -> None:
        self.events.append((step, payload))


@pytest.mark.asyncio
async def test_continuity_repair_gate_does_not_skip_hard_structural_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _DummyRunner(threshold=9.0, must_fix="critical")
    bundle = SimpleNamespace(layout=_DummyLayout(), chapter_outline=SimpleNamespace())
    packet = SimpleNamespace()
    bridge = SimpleNamespace()
    plan = SimpleNamespace()

    called = {"repair": False}

    async def _fake_run_repair(_step, payload):
        called["repair"] = True
        return ContinuityRepairResult(
            revised_text=payload.chapter_text + "\n补丁",
            repair_plan=RepairPlan(no_op=False),
            applied=True,
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.long.repair.run_continuity_repair",
        _fake_run_repair,
    )

    report = ContinuityReport(
        continuity_score=9.8,
        issues=[
            ContinuityIssue(
                issue_type="prompt_leak",
                severity="high",
                summary="正文混入规划层术语。",
            )
        ],
    )

    revised_text, result = await run_continuity_repair(
        runner,
        bundle,
        packet,
        bridge,
        plan,
        report,
        "原正文",
        chapter_number=12,
        trace=object(),
    )

    assert called["repair"] is True
    assert result.applied is True
    assert revised_text.endswith("补丁")


@pytest.mark.asyncio
async def test_continuity_repair_gate_skips_when_score_high_and_no_hard_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _DummyRunner(threshold=9.0, must_fix="high")
    bundle = SimpleNamespace(layout=_DummyLayout(), chapter_outline=SimpleNamespace())
    packet = SimpleNamespace()
    bridge = SimpleNamespace()
    plan = SimpleNamespace()

    called = {"repair": False}

    async def _fake_run_repair(_step, payload):
        called["repair"] = True
        return ContinuityRepairResult(
            revised_text=payload.chapter_text,
            repair_plan=RepairPlan(no_op=False),
            applied=False,
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.long.repair.run_continuity_repair",
        _fake_run_repair,
    )

    report = ContinuityReport(
        continuity_score=9.6,
        issues=[
            ContinuityIssue(
                issue_type="opening_gap",
                severity="low",
                summary="开场感官描写可再强化。",
            )
        ],
    )

    revised_text, result = await run_continuity_repair(
        runner,
        bundle,
        packet,
        bridge,
        plan,
        report,
        "原正文",
        chapter_number=13,
        trace=object(),
    )

    assert called["repair"] is False
    assert result.applied is False
    assert revised_text == "原正文"


@pytest.mark.asyncio
async def test_continuity_repair_gate_does_not_skip_medium_opening_boundary_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _DummyRunner(threshold=9.0, must_fix="high")
    bundle = SimpleNamespace(layout=_DummyLayout(), chapter_outline=SimpleNamespace())
    packet = SimpleNamespace()
    bridge = SimpleNamespace()
    plan = SimpleNamespace()

    called = {"repair": False}

    async def _fake_run_repair(_step, payload):
        called["repair"] = True
        return ContinuityRepairResult(
            revised_text=payload.chapter_text + "\n补丁",
            repair_plan=RepairPlan(no_op=False),
            applied=True,
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.long.repair.run_continuity_repair",
        _fake_run_repair,
    )

    report = ContinuityReport(
        continuity_score=9.6,
        issues=[
            ContinuityIssue(
                issue_type="opening_gap",
                severity="medium",
                summary="开场动作接力不自然。",
            )
        ],
    )

    revised_text, result = await run_continuity_repair(
        runner,
        bundle,
        packet,
        bridge,
        plan,
        report,
        "原正文",
        chapter_number=13,
        trace=object(),
    )

    assert called["repair"] is True
    assert result.applied is True
    assert revised_text.endswith("补丁")


@pytest.mark.asyncio
async def test_continuity_repair_attaches_revised_bridge_from_artifact_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _DummyRunner(threshold=0.0)
    outline = ChapterOutline(
        chapter_number=2,
        title="怀表",
        goal="切换到陆云峥行动线",
        pov_character="陆云峥",
        setting="办公室",
        expected_word_count=2500,
    )
    bundle = SimpleNamespace(layout=_DummyLayout(), chapter_outline=outline)
    packet = ChapterStatePacket(
        chapter_number=2,
        chapter_outline=outline,
        canon_context={},
        previous_exit_state=ChapterExitState(
            chapter_number=1,
            time_marker="深夜",
            pov="沈念卿",
            open_questions=["陆云峥是否已经收到讯号"],
        ),
    )
    bridge = ChapterBridge(
        from_chapter=0,
        to_chapter=2,
        opening_pov="沈念卿",
        opening_location="沈念卿公寓",
        transition_mode="action_handoff",
        bridge_summary="怀表成为切换到陆云峥行动线的触发物。",
    )

    async def _fake_run_repair(_step, payload):
        return ContinuityRepairResult(
            revised_text=payload.chapter_text,
            repair_plan=RepairPlan(no_op=True),
            applied=False,
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.long.repair.run_continuity_repair",
        _fake_run_repair,
    )

    report = ContinuityReport(
        continuity_score=6.0,
        issues=[
            ContinuityIssue(
                issue_type="bridge_contract_not_followed",
                severity="high",
                summary="bridge.opening_pov 与大纲不一致，opening_location 仍指向旧地点。",
                repair_surface="bridge_artifact",
            )
        ],
    )

    revised_text, result = await run_continuity_repair(
        runner,
        bundle,
        packet,
        bridge,
        SimpleNamespace(),
        report,
        "原正文",
        chapter_number=2,
        trace=object(),
    )

    assert revised_text == "原正文"
    assert result.revised_bridge is not None
    assert result.revised_bridge.from_chapter == 1
    assert result.revised_bridge.opening_pov == "陆云峥"
    assert result.revised_bridge.opening_location == "办公室"


@pytest.mark.asyncio
async def test_continuity_repair_loop_keeps_text_on_internal_repair_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _DummyRunner(threshold=0.0)
    outline = ChapterOutline(
        chapter_number=2,
        title="怀表",
        goal="切换到陆云峥行动线",
        pov_character="陆云峥",
        setting="办公室",
        expected_word_count=2500,
    )
    bundle = SimpleNamespace(layout=_DummyLayout(), chapter_outline=outline)
    packet = ChapterStatePacket(
        chapter_number=2,
        chapter_outline=outline,
        canon_context={},
    )

    async def _raise_internal_error(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.continuity_repair.run_continuity_repair",
        _raise_internal_error,
    )

    report = ContinuityReport(
        continuity_score=6.0,
        issues=[
            ContinuityIssue(
                issue_type="opening_gap",
                severity="critical",
                summary="开场缺少承接。",
            )
        ],
    )

    result = await _execute_continuity_repair_loop(
        runner,
        bundle,
        packet,
        ChapterBridge(to_chapter=2),
        SimpleNamespace(),
        runner._on_step,
        current_text="原正文",
        alignment_report=SimpleNamespace(alignment_score=8.0),
        continuity_report=report,
        chapter_repair_report=None,
        chapter_number=2,
        trace=SimpleNamespace(total_tokens=0),
        repair_thresholds=RepairThresholds(),
        cont_max_rounds=1,
        cont_must_fix_sev="critical",
        cont_threshold=8.5,
    )

    assert result.current_text == "原正文"
    assert result.repair_exhausted is True
    assert result.continuity_repair.applied is False
    assert result.continuity_repair.failure_reason == "RuntimeError: boom"
    assert any(step == "continuity_repair_internal_error" for step, _ in runner.events)


@pytest.mark.asyncio
async def test_continuity_repair_loop_rolls_back_when_recheck_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _DummyRunner(threshold=0.0)
    outline = ChapterOutline(
        chapter_number=2,
        title="怀表",
        goal="切换到陆云峥行动线",
        pov_character="陆云峥",
        setting="办公室",
        expected_word_count=2500,
    )
    bundle = SimpleNamespace(layout=_DummyLayout(), chapter_outline=outline)
    packet = ChapterStatePacket(
        chapter_number=2,
        chapter_outline=outline,
        canon_context={},
    )

    async def _fake_run_repair(*_args, **_kwargs):
        return "原正文。", ContinuityRepairResult(
            revised_text="原正文。",
            repair_plan=RepairPlan(no_op=False),
            applied=True,
        )

    async def _raise_recheck_error(*_args, **_kwargs):
        raise RuntimeError("recheck boom")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.continuity_repair.run_continuity_repair",
        _fake_run_repair,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.continuity_repair.run_post_repair_checks",
        _raise_recheck_error,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.continuity_repair.detect_drift",
        lambda *_args, **_kwargs: SimpleNamespace(has_drift=False),
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.dedup_pronoun.run_self_repetition_check",
        lambda _runner, text: (text, {}),
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.steps.continuity_repair_step.prescreen_revised_text",
        lambda *_args, **_kwargs: (True, ""),
    )

    report = ContinuityReport(
        continuity_score=6.0,
        issues=[
            ContinuityIssue(
                issue_type="opening_gap",
                severity="critical",
                summary="开场缺少承接。",
            )
        ],
    )
    alignment_report = SimpleNamespace(alignment_score=8.0)
    chapter_repair_report = SimpleNamespace()

    result = await _execute_continuity_repair_loop(
        runner,
        bundle,
        packet,
        ChapterBridge(to_chapter=2),
        SimpleNamespace(),
        runner._on_step,
        current_text="原正文",
        alignment_report=alignment_report,
        continuity_report=report,
        chapter_repair_report=chapter_repair_report,
        chapter_number=2,
        trace=SimpleNamespace(total_tokens=0),
        repair_thresholds=RepairThresholds(change_budget=1.0),
        cont_max_rounds=1,
        cont_must_fix_sev="critical",
        cont_threshold=8.5,
    )

    assert result.current_text == "原正文"
    assert result.alignment_report is alignment_report
    assert result.continuity_report is report
    assert result.chapter_repair_report is chapter_repair_report
    assert result.repair_exhausted is True
    assert result.continuity_repair.applied is False
    assert result.continuity_repair.failure_reason == "RuntimeError: recheck boom"
    assert any(step == "continuity_recheck_internal_error" for step, _ in runner.events)
