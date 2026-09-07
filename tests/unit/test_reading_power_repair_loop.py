"""Tests for the upgraded _execute_reading_power_repair_loop function."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.core.schemas.reading_power import MicroPayoff, MicroPayoffType, ReadingPowerReport
from novel_forge.core.schemas.reading_power_repair import _build_reading_power_issues
from novel_forge.core.schemas.review import RepairTicket
from novel_forge.core.utils.issue_ledger import IssuePrint, LedgerDiff
from novel_forge.core.utils.semantic_drift import DriftReport, DriftSignal
from novel_forge.pipeline.long.stages.reading_power_repair import (
    ReadingPowerRepairRunner,
    _execute_reading_power_repair_loop,
    _reading_power_repair_focus,
)
from novel_forge.pipeline.repair_orchestration.loop_runner import RepairLoopConfig
from novel_forge.pipeline.steps.reading_power_repair_step import ReadingPowerRepairResult


class _DummyStorage:
    def __init__(self, *, alignment_exists: bool = False) -> None:
        self.saved_text: list[tuple] = []
        self.saved_json: list[tuple] = []
        self._exists: dict[str, bool] = {}
        self._json_data: dict[str, dict] = {}
        self._alignment_exists = alignment_exists

    def exists(self, path) -> bool:
        str_path = str(path)
        if "alignment" in str_path:
            return self._alignment_exists
        return self._exists.get(str_path, False)

    def load_json(self, path) -> dict:
        str_path = str(path)
        if "alignment" in str_path and self._alignment_exists:
            return {"alignment_score": 8.5, "issues": []}
        return self._json_data.get(str_path, {})

    def save_text(self, path, text) -> None:
        self.saved_text.append((path, text))

    def save_json(self, path, payload) -> None:
        self.saved_json.append((path, payload))


class _DummyLayout:
    def reading_power_report_path(self, chapter_number: int):
        from pathlib import Path
        return Path(f"reports/rp_ch{chapter_number}.json")

    def chapter_draft_path(self, chapter_number: int, version: int):
        from pathlib import Path
        return Path(f"drafts/ch{chapter_number}/v{version}.md")

    def alignment_report_path(self, chapter_number: int):
        from pathlib import Path
        return Path(f"reports/alignment_ch{chapter_number}.json")


class _DummyRunner:
    def __init__(self, **settings_overrides) -> None:
        self._router = object()
        self._builder = object()
        defaults = {
            "long_reading_power_repair_enabled": True,
            "long_reading_power_max_repair_rounds": 3,
            "long_reading_power_score_threshold": 5.0,
            "change_budget_threshold": 0.15,
            "long_reading_power_repair_max_change_ratio": 0.35,
        }
        defaults.update(settings_overrides)
        self._settings = SimpleNamespace(**defaults)
        self._storage = _DummyStorage()
        self.events: list[tuple[str, object]] = []
        self.memory_context = None

    def _on_step(self, step: str, payload: object) -> None:
        self.events.append((step, payload))

    def has_memory_context(self) -> bool:
        return self.memory_context is not None


class _FakeReadingPowerMemory:
    def __init__(self) -> None:
        self._critique_index = {}
        self.indexed = []
        self.records = []

    async def _add_critique_entry(self, entry):
        sig = entry.signature()
        self._critique_index[sig] = entry
        self.indexed.append(entry)
        return sig

    async def search_similar_critiques(
        self,
        *,
        issue_type: str,
        summary: str,
        current_chapter: int,
        top_k: int = 5,
        min_relevance: float = 0.55,
    ) -> list[dict]:
        return [
            {
                "signature": "old-reading-power",
                "chapter_number": max(1, current_chapter - 1),
                "issue_type": issue_type,
                "severity": "high",
                "summary": "历史章节章尾钩子过弱，读者缺少继续点击的理由",
                "evidence": "章尾只停在情绪感受，没有行动压力",
                "suggested_fix": "用具体选择代价收束章尾，而不是只补说明。",
                "relevance_score": 0.86,
                "distance": 1,
                "repair_attempts": [
                    {
                        "strategy": "ending_hook",
                        "result": "success",
                        "new_issues_introduced": [],
                    }
                ],
                "failure_pattern": "",
                "lesson_learned": "章尾钩子优先落到角色必须马上处理的行动压力。",
                "metadata": {"source": "reading_power_repair"},
            }
        ]

    def record_repair_result(
        self,
        critique_signature: str,
        *,
        chapter: int,
        round_num: int,
        strategy: str,
        result: str,
        new_issues: list[str] | None = None,
        score_before: float = 0.0,
        score_after: float = 0.0,
    ) -> bool:
        if critique_signature not in self._critique_index:
            return False
        self.records.append(
            {
                "signature": critique_signature,
                "chapter": chapter,
                "round": round_num,
                "strategy": strategy,
                "result": result,
                "new_issues": new_issues or [],
                "score_before": score_before,
                "score_after": score_after,
            }
        )
        return True


def _make_report(
    chapter: int = 1,
    overall_score: float = 4.0,
    hook_type: str = "none",
    hook_strength: str = "weak",
    micro_payoffs: list | None = None,
    prev_hook_fulfilled: bool = True,
    is_fallback: bool = False,
    evaluation_status: str = "ok",
) -> ReadingPowerReport:
    return ReadingPowerReport(
        chapter=chapter,
        overall_score=overall_score,
        hook_type=hook_type,
        hook_strength=hook_strength,
        micro_payoffs=micro_payoffs or [],
        prev_hook_fulfilled=prev_hook_fulfilled,
        is_fallback=is_fallback,
        evaluation_status=evaluation_status,
    )


def _make_good_report(chapter: int = 1) -> ReadingPowerReport:
    return _make_report(
        overall_score=7.5,
        hook_type="mystery",
        hook_strength="strong",
        micro_payoffs=[
            MicroPayoff(payoff_type=MicroPayoffType.INFORMATION, description="线索揭示", strength="strong"),
            MicroPayoff(payoff_type=MicroPayoffType.RELATIONSHIP, description="关系推进", strength="medium"),
        ],
        prev_hook_fulfilled=True,
    )


def _make_bundle(style_profile=None) -> SimpleNamespace:
    return SimpleNamespace(
        layout=_DummyLayout(),
        story_bible=SimpleNamespace(genre="mystery"),
        chapter_outline=SimpleNamespace(
            pov_character="张三",
            required_characters=["张三", "李四"],
            expected_hook=None,
            expected_payoffs=[],
        ),
        style_profile=style_profile,
    )


def _make_packet() -> SimpleNamespace:
    return SimpleNamespace()


def _make_bridge() -> SimpleNamespace:
    return SimpleNamespace()


def _make_plan() -> SimpleNamespace:
    return SimpleNamespace()


SAMPLE_TEXT = "这是一段测试正文。张三走进房间，发现李四已经在那里等着了。"


def test_first_chapter_reading_power_ignores_previous_hook_failure() -> None:
    report = _make_report(
        chapter=1,
        overall_score=8.0,
        hook_type="mystery",
        hook_strength="strong",
        micro_payoffs=[
            MicroPayoff(
                payoff_type=MicroPayoffType.INFORMATION,
                description="本章线索兑现",
                strength="medium",
            )
        ],
        prev_hook_fulfilled=False,
    )

    focus = _reading_power_repair_focus(
        report=report,
        expected_hook=None,
        expected_payoffs=[],
        min_payoffs=1,
        repair_threshold=6.0,
        chapter_number=1,
    )
    issues = _build_reading_power_issues(report, min_payoffs=1, chapter_number=1)

    assert not any("上一章钩子" in item for item in focus)
    assert not any(issue.issue_type == "prev_hook_unfulfilled" for issue in issues)


# ── Test 1 ───────────────────────────────────────────────────────────────────

async def test_early_exit_on_score_improvement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Score 4.0 -> 7.0 -> loop exits after round 1."""
    runner = _DummyRunner(long_reading_power_max_repair_rounds=3)
    bundle = _make_bundle()
    packet = _make_packet()
    bridge = _make_bridge()
    plan = _make_plan()

    call_count = {"eval": 0}

    async def _fake_eval(**kwargs):
        call_count["eval"] += 1
        if call_count["eval"] == 1:
            return _make_report(overall_score=4.0, hook_type="none")
        return _make_good_report()

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.reading_power_repair.evaluate_and_record_reading_power",
        _fake_eval,
    )

    async def _fake_repair_run(_self, _input):
        return ReadingPowerRepairResult(
            revised_text=SAMPLE_TEXT + "\n修复后的钩子内容。",
            applied=True,
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.reading_power_repair_step.ReadingPowerRepairStep.run",
        _fake_repair_run,
    )

    text, report, text_hash = await _execute_reading_power_repair_loop(
        runner, bundle, packet, bridge, plan, SAMPLE_TEXT, chapter_number=1, trace=SimpleNamespace(total_tokens=0),
    )

    assert call_count["eval"] == 2
    assert report is not None
    assert float(getattr(report, "overall_score", 0)) >= 7.0
    assert text != SAMPLE_TEXT


async def test_repair_threshold_triggers_low_score_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A score below long_reading_power_repair_threshold should repair even without hook-specific issues."""
    runner = _DummyRunner(long_reading_power_repair_threshold=6.0)
    bundle = _make_bundle()
    packet = _make_packet()
    bridge = _make_bridge()
    plan = _make_plan()

    call_count = {"eval": 0, "repair": 0}
    captured_input = {}

    async def _fake_eval(**kwargs):
        call_count["eval"] += 1
        if call_count["eval"] == 1:
            return _make_report(
                overall_score=5.5,
                hook_type="mystery",
                hook_strength="strong",
                micro_payoffs=[
                    MicroPayoff(
                        payoff_type=MicroPayoffType.INFORMATION,
                        description="线索揭示",
                        strength="strong",
                    )
                ],
                prev_hook_fulfilled=True,
            )
        return _make_good_report()

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.reading_power_repair.evaluate_and_record_reading_power",
        _fake_eval,
    )

    async def _fake_repair_run(_self, _input):
        call_count["repair"] += 1
        captured_input["value"] = _input
        return ReadingPowerRepairResult(
            revised_text=SAMPLE_TEXT + "\n低分追读力修复。",
            applied=True,
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.reading_power_repair_step.ReadingPowerRepairStep.run",
        _fake_repair_run,
    )

    monkeypatch.setattr(
        "novel_forge.pipeline.repair_orchestration.loop_runner.detect_drift",
        lambda *args, **kwargs: DriftReport(signals=[]),
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.repair_orchestration.loop_runner.diff_issues",
        lambda before, after, **kwargs: LedgerDiff(),
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.dedup_pronoun.run_self_repetition_check",
        lambda runner, text: (text, None),
    )

    text, report, text_hash = await _execute_reading_power_repair_loop(
        runner, bundle, packet, bridge, plan, SAMPLE_TEXT, chapter_number=1, trace=SimpleNamespace(total_tokens=0),
    )

    assert call_count["repair"] == 1
    assert text != SAMPLE_TEXT
    assert captured_input["value"].issues[0].issue_type == "overall_score_low"


async def test_repair_loop_reuses_precomputed_quality_stage_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matching quality-stage report should skip the pre-repair LLM evaluation."""
    runner = _DummyRunner()
    bundle = _make_bundle()
    packet = _make_packet()
    bridge = _make_bridge()
    plan = _make_plan()
    precomputed_report = _make_good_report()
    text_hash = hashlib.sha256(SAMPLE_TEXT.encode("utf-8")).hexdigest()

    async def _unexpected_eval_run(_self, _input):
        raise AssertionError("ReadingPowerEvalStep.run should not be called")

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.reading_power_eval_step.ReadingPowerEvalStep.run",
        _unexpected_eval_run,
    )

    text, report, result_hash = await _execute_reading_power_repair_loop(
        runner,
        bundle,
        packet,
        bridge,
        plan,
        SAMPLE_TEXT,
        chapter_number=1,
        trace=SimpleNamespace(total_tokens=0),
        precomputed_reading_power_report=precomputed_report,
        precomputed_reading_power_text_hash=text_hash,
    )

    assert text == SAMPLE_TEXT
    assert report is precomputed_report
    assert result_hash == text_hash
    assert any(step == "reading_power_prerepair_eval" for step, _ in runner.events)
    assert any(step == "reading_power_repair_skipped" for step, _ in runner.events)


async def test_reading_power_repair_receives_plan_forbidden_elements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan hard/soft forbidden elements should reach ReadingPowerRepairInput."""
    runner = _DummyRunner()
    bundle = _make_bundle()
    packet = _make_packet()
    bridge = _make_bridge()
    plan = SimpleNamespace(
        forbidden_elements=["金丝颤动", "母题回环"],
        forbidden_elements_soft=["惨淡月光", "金丝颤动"],
        intentional_callbacks=["母题回环"],
    )
    captured_input = {}

    async def _fake_eval(**kwargs):
        return _make_report(overall_score=3.0, hook_type="none")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.reading_power_repair.evaluate_and_record_reading_power",
        _fake_eval,
    )

    async def _fake_repair_run(_self, _input):
        captured_input["value"] = _input
        return ReadingPowerRepairResult(
            revised_text=SAMPLE_TEXT,
            applied=False,
            failure_reason="stop after capture",
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.reading_power_repair_step.ReadingPowerRepairStep.run",
        _fake_repair_run,
    )

    await _execute_reading_power_repair_loop(
        runner, bundle, packet, bridge, plan, SAMPLE_TEXT, chapter_number=1, trace=SimpleNamespace(total_tokens=0),
    )

    rp_input = captured_input["value"]
    assert rp_input.forbidden_elements == ["金丝颤动"]
    assert rp_input.forbidden_elements_soft == ["惨淡月光"]
    assert rp_input.intentional_callbacks == ["母题回环"]


async def test_reading_power_repair_uses_and_records_memory_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading-power repair should read historical guidance and record repair results."""
    runner = _DummyRunner(
        long_reading_power_max_repair_rounds=1,
        long_reading_power_repair_max_change_ratio=1.0,
    )
    memory = _FakeReadingPowerMemory()
    runner.memory_context = SimpleNamespace(episodic_memory=memory)
    bundle = _make_bundle()
    packet = _make_packet()
    bridge = _make_bridge()
    plan = _make_plan()
    call_count = {"eval": 0}
    captured_input = {}

    async def _fake_eval(**kwargs):
        call_count["eval"] += 1
        if call_count["eval"] == 1:
            return _make_report(
                chapter=2,
                overall_score=3.0,
                hook_type="none",
                hook_strength="weak",
                prev_hook_fulfilled=False,
            )
        return _make_good_report(chapter=2)

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.reading_power_repair.evaluate_and_record_reading_power",
        _fake_eval,
    )

    async def _fake_repair_run(_self, _input):
        captured_input["value"] = _input
        return ReadingPowerRepairResult(
            revised_text=SAMPLE_TEXT + "\n他推开门，听见对方说期限只剩最后十分钟。",
            applied=True,
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.reading_power_repair_step.ReadingPowerRepairStep.run",
        _fake_repair_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.repair_orchestration.loop_runner.detect_drift",
        lambda *args, **kwargs: DriftReport(signals=[]),
    )

    def _fake_diff_issues(before, after, **kwargs):
        return LedgerDiff(
            resolved=[
                IssuePrint(
                    fingerprint="hook-missing",
                    issue_type="hook_missing",
                    severity="high",
                    summary="章尾缺少明确钩子",
                )
            ],
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.repair_orchestration.loop_runner.diff_issues",
        _fake_diff_issues,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.dedup_pronoun.run_self_repetition_check",
        lambda runner, text: (text, None),
    )

    text, report, text_hash = await _execute_reading_power_repair_loop(
        runner, bundle, packet, bridge, plan, SAMPLE_TEXT, chapter_number=2, trace=SimpleNamespace(total_tokens=0),
    )

    assert text != SAMPLE_TEXT
    assert report is not None
    assert text_hash
    assert memory.indexed
    assert memory.records
    assert memory.records[0]["result"] == "success"
    guidance = captured_input["value"].memory_guidance
    assert guidance["matched_issues"]
    assert guidance["recommended_strategies"]
    assert any(e[0] == "reading_power_memory_issues_indexed" for e in runner.events)
    assert any(e[0] == "reading_power_memory_guidance_added" for e in runner.events)
    assert any(e[0] == "reading_power_repair_memory_results_recorded" for e in runner.events)


async def test_reading_power_repair_accepts_repair_tickets_as_entry_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reading-power RepairTicket should trigger repair even when the report is otherwise fine."""
    runner = _DummyRunner(long_reading_power_score_threshold=5.0)
    bundle = _make_bundle()
    packet = _make_packet()
    bridge = _make_bridge()
    plan = _make_plan()
    captured_input = {}
    call_count = {"eval": 0, "repair": 0}

    async def _fake_eval(**kwargs):
        call_count["eval"] += 1
        return _make_good_report()

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.reading_power_repair.evaluate_and_record_reading_power",
        _fake_eval,
    )

    async def _fake_repair_run(_self, _input):
        call_count["repair"] += 1
        captured_input["value"] = _input
        return ReadingPowerRepairResult(
            revised_text=SAMPLE_TEXT + "\n补上一处线索兑现。",
            applied=True,
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.reading_power_repair_step.ReadingPowerRepairStep.run",
        _fake_repair_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.repair_orchestration.loop_runner.detect_drift",
        lambda *args, **kwargs: DriftReport(signals=[]),
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.repair_orchestration.loop_runner.diff_issues",
        lambda before, after, **kwargs: LedgerDiff(),
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.dedup_pronoun.run_self_repetition_check",
        lambda runner, text: (text, None),
    )

    ticket = RepairTicket(
        ticket_id="ticket_reading_payoff",
        chapter_number=1,
        source_module="reading_power_eval",
        dimension="reading_power",
        issue_type="payoff_missing",
        severity="medium",
        target_summary="章内微兑现不足",
        repair_goal="补一处与本章线索相关的微兑现。",
        repair_mode="window",
        acceptance_criteria=["修复后读者能看到至少一处明确兑现"],
    )

    text, report, text_hash = await _execute_reading_power_repair_loop(
        runner,
        bundle,
        packet,
        bridge,
        plan,
        SAMPLE_TEXT,
        chapter_number=1,
        trace=SimpleNamespace(total_tokens=0),
        repair_tickets=[ticket],
    )

    assert call_count["repair"] == 1
    assert text != SAMPLE_TEXT
    assert report is not None
    assert text_hash
    rp_input = captured_input["value"]
    assert rp_input.issues[0].issue_type == "payoff_missing"
    assert rp_input.issues[0].fix_suggestion == "补一处与本章线索相关的微兑现。"


# ── Test 2 ───────────────────────────────────────────────────────────────────

async def test_runs_max_rounds_on_no_improvement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Score stays low -> runs max_rounds times."""
    max_rounds = 2
    runner = _DummyRunner(
        long_reading_power_max_repair_rounds=max_rounds,
        long_reading_power_score_threshold=8.0,
    )
    bundle = _make_bundle()
    packet = _make_packet()
    bridge = _make_bridge()
    plan = _make_plan()

    call_count = {"eval": 0, "repair": 0}

    async def _fake_eval(**kwargs):
        call_count["eval"] += 1
        return _make_report(overall_score=4.0, hook_type="none")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.reading_power_repair.evaluate_and_record_reading_power",
        _fake_eval,
    )

    async def _fake_repair_run(_self, _input):
        call_count["repair"] += 1
        return ReadingPowerRepairResult(
            revised_text=SAMPLE_TEXT + f"\n修复尝试{call_count['repair']}",
            applied=True,
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.reading_power_repair_step.ReadingPowerRepairStep.run",
        _fake_repair_run,
    )

    def _fake_detect_drift(*args, **kwargs):
        return DriftReport(signals=[])

    monkeypatch.setattr(
        "novel_forge.pipeline.repair_orchestration.loop_runner.detect_drift",
        _fake_detect_drift,
    )

    def _fake_diff_issues(before, after, **kwargs):
        return LedgerDiff()

    monkeypatch.setattr(
        "novel_forge.pipeline.repair_orchestration.loop_runner.diff_issues",
        _fake_diff_issues,
    )

    def _fake_dedup(runner, text):
        return text, None

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.dedup_pronoun.run_self_repetition_check",
        _fake_dedup,
    )

    text, report, text_hash = await _execute_reading_power_repair_loop(
        runner, bundle, packet, bridge, plan, SAMPLE_TEXT, chapter_number=1, trace=SimpleNamespace(total_tokens=0),
    )

    assert call_count["repair"] == max_rounds


# ── Test 3 ───────────────────────────────────────────────────────────────────

async def test_drift_triggers_rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    """detect_drift returns has_drift=True -> text rolled back."""
    runner = _DummyRunner()
    bundle = _make_bundle()
    packet = _make_packet()
    bridge = _make_bridge()
    plan = _make_plan()

    eval_count = {"n": 0}

    async def _fake_eval(**kwargs):
        eval_count["n"] += 1
        if eval_count["n"] == 1:
            return _make_report(overall_score=3.0, hook_type="none")
        return _make_report(overall_score=5.0, hook_type="mystery")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.reading_power_repair.evaluate_and_record_reading_power",
        _fake_eval,
    )

    async def _fake_repair_run(_self, _input):
        return ReadingPowerRepairResult(
            revised_text=SAMPLE_TEXT + "\n修复内容",
            applied=True,
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.reading_power_repair_step.ReadingPowerRepairStep.run",
        _fake_repair_run,
    )

    def _fake_detect_drift(*args, **kwargs):
        return DriftReport(
            signals=[DriftSignal(category="pov", description="POV角色从张三变为李四", severity="high")]
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.repair_orchestration.loop_runner.detect_drift",
        _fake_detect_drift,
    )

    def _fake_dedup(runner, text):
        return text, None

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.dedup_pronoun.run_self_repetition_check",
        _fake_dedup,
    )

    text, report, text_hash = await _execute_reading_power_repair_loop(
        runner, bundle, packet, bridge, plan, SAMPLE_TEXT, chapter_number=1, trace=SimpleNamespace(total_tokens=0),
    )

    assert text == SAMPLE_TEXT
    assert ("reading_power_repair_rollback",) in [(e[0],) for e in runner.events]


# ── Test 4 ───────────────────────────────────────────────────────────────────

async def test_ledger_regression_triggers_rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    """diff_issues returns has_regression=True -> text rolled back."""
    runner = _DummyRunner()
    bundle = _make_bundle()
    packet = _make_packet()
    bridge = _make_bridge()
    plan = _make_plan()

    eval_count = {"n": 0}

    async def _fake_eval(**kwargs):
        eval_count["n"] += 1
        if eval_count["n"] == 1:
            return _make_report(overall_score=3.0, hook_type="none")
        return _make_report(overall_score=5.0, hook_type="mystery")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.reading_power_repair.evaluate_and_record_reading_power",
        _fake_eval,
    )

    async def _fake_repair_run(_self, _input):
        return ReadingPowerRepairResult(
            revised_text=SAMPLE_TEXT + "\n修复内容",
            applied=True,
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.reading_power_repair_step.ReadingPowerRepairStep.run",
        _fake_repair_run,
    )

    def _fake_detect_drift(*args, **kwargs):
        return DriftReport(signals=[])

    monkeypatch.setattr(
        "novel_forge.pipeline.repair_orchestration.loop_runner.detect_drift",
        _fake_detect_drift,
    )

    def _fake_diff_issues(before, after, **kwargs):
        new_issue = IssuePrint(
            fingerprint="abc123",
            issue_type="hook_missing",
            severity="high",
            summary="章尾缺少钩子",
        )
        return LedgerDiff(
            new_issues=[new_issue],
            resolved=[],
            downgraded=[],
            upgraded=[],
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.repair_orchestration.loop_runner.diff_issues",
        _fake_diff_issues,
    )

    def _fake_dedup(runner, text):
        return text, None

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.dedup_pronoun.run_self_repetition_check",
        _fake_dedup,
    )

    text, report, text_hash = await _execute_reading_power_repair_loop(
        runner, bundle, packet, bridge, plan, SAMPLE_TEXT, chapter_number=1, trace=SimpleNamespace(total_tokens=0),
    )

    assert text == SAMPLE_TEXT
    assert ("reading_power_repair_rollback",) in [(e[0],) for e in runner.events]


# ── Test 5 ───────────────────────────────────────────────────────────────────

async def test_intermediate_dedup_called(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_self_repetition_check is called during the repair loop."""
    runner = _DummyRunner()
    bundle = _make_bundle()
    packet = _make_packet()
    bridge = _make_bridge()
    plan = _make_plan()

    dedup_called = {"flag": False}

    async def _fake_eval(**kwargs):
        return _make_report(overall_score=3.0, hook_type="none")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.reading_power_repair.evaluate_and_record_reading_power",
        _fake_eval,
    )

    async def _fake_repair_run(_self, _input):
        return ReadingPowerRepairResult(
            revised_text=SAMPLE_TEXT + "\n修复内容",
            applied=True,
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.reading_power_repair_step.ReadingPowerRepairStep.run",
        _fake_repair_run,
    )

    def _fake_detect_drift(*args, **kwargs):
        return DriftReport(signals=[])

    monkeypatch.setattr(
        "novel_forge.pipeline.repair_orchestration.loop_runner.detect_drift",
        _fake_detect_drift,
    )

    def _fake_diff_issues(before, after, **kwargs):
        return LedgerDiff()

    monkeypatch.setattr(
        "novel_forge.pipeline.repair_orchestration.loop_runner.diff_issues",
        _fake_diff_issues,
    )

    def _fake_dedup(runner, text):
        dedup_called["flag"] = True
        return text, None

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.dedup_pronoun.run_self_repetition_check",
        _fake_dedup,
    )

    await _execute_reading_power_repair_loop(
        runner, bundle, packet, bridge, plan, SAMPLE_TEXT, chapter_number=1, trace=SimpleNamespace(total_tokens=0),
    )

    assert dedup_called["flag"] is True


# ── Test 6 ───────────────────────────────────────────────────────────────────

async def test_post_repair_checks_called(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_post_repair_checks is called after successful repair."""
    runner = _DummyRunner()
    runner._storage = _DummyStorage(alignment_exists=True)
    bundle = _make_bundle()
    packet = _make_packet()
    bridge = _make_bridge()
    plan = _make_plan()

    post_repair_called = {"flag": False}

    async def _fake_eval(**kwargs):
        return _make_report(overall_score=3.0, hook_type="none")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.reading_power_repair.evaluate_and_record_reading_power",
        _fake_eval,
    )

    async def _fake_repair_run(_self, _input):
        return ReadingPowerRepairResult(
            revised_text=SAMPLE_TEXT + "\n修复内容",
            applied=True,
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.reading_power_repair_step.ReadingPowerRepairStep.run",
        _fake_repair_run,
    )

    def _fake_detect_drift(*args, **kwargs):
        return DriftReport(signals=[])

    monkeypatch.setattr(
        "novel_forge.pipeline.repair_orchestration.loop_runner.detect_drift",
        _fake_detect_drift,
    )

    def _fake_diff_issues(before, after, **kwargs):
        return LedgerDiff()

    monkeypatch.setattr(
        "novel_forge.pipeline.repair_orchestration.loop_runner.diff_issues",
        _fake_diff_issues,
    )

    def _fake_dedup(runner, text):
        return text, None

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.dedup_pronoun.run_self_repetition_check",
        _fake_dedup,
    )

    async def _fake_post_repair_checks(*args, **kwargs):
        post_repair_called["flag"] = True
        return None, None, None

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.chapter_repair_support.run_post_repair_checks",
        _fake_post_repair_checks,
    )

    # Also patch the AlignmentReport.model_validate to ensure it doesn't fail
    from novel_forge.core.schemas.chapter import AlignmentReport
    original_validate = AlignmentReport.model_validate

    def _fake_validate(data):
        if isinstance(data, dict):
            return AlignmentReport(
                alignment_score=data.get("alignment_score", 8.5),
                risk_level=data.get("risk_level", "low"),
                conflict_level=data.get("conflict_level", "low"),
            )
        return original_validate(data)

    monkeypatch.setattr(AlignmentReport, "model_validate", _fake_validate)

    await _execute_reading_power_repair_loop(
        runner, bundle, packet, bridge, plan, SAMPLE_TEXT, chapter_number=1, trace=SimpleNamespace(total_tokens=0),
    )

    start_events = [e for e in runner.events if e[0] == "reading_power_post_repair_checks_start"]
    assert len(start_events) >= 1, f"Post-repair checks not started. Events: {[e[0] for e in runner.events]}"
    assert post_repair_called["flag"] is True


# ── Test 7 ───────────────────────────────────────────────────────────────────

async def test_repair_disabled_by_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """long_reading_power_repair_enabled=False -> skipped."""
    runner = _DummyRunner(long_reading_power_repair_enabled=False)
    bundle = _make_bundle()
    packet = _make_packet()
    bridge = _make_bridge()
    plan = _make_plan()

    text, report, text_hash = await _execute_reading_power_repair_loop(
        runner, bundle, packet, bridge, plan, SAMPLE_TEXT, chapter_number=1, trace=SimpleNamespace(total_tokens=0),
    )

    assert text == SAMPLE_TEXT
    assert report is None
    assert text_hash is None


# ── Test 8 ───────────────────────────────────────────────────────────────────

async def test_fallback_report_skips_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fallback report -> repair skipped."""
    runner = _DummyRunner()
    bundle = _make_bundle()
    packet = _make_packet()
    bridge = _make_bridge()
    plan = _make_plan()

    async def _fake_eval(**kwargs):
        return _make_report(
            overall_score=3.0,
            hook_type="none",
            is_fallback=True,
            evaluation_status="fallback",
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.reading_power_repair.evaluate_and_record_reading_power",
        _fake_eval,
    )

    text, report, text_hash = await _execute_reading_power_repair_loop(
        runner, bundle, packet, bridge, plan, SAMPLE_TEXT, chapter_number=1, trace=SimpleNamespace(total_tokens=0),
    )

    assert text == SAMPLE_TEXT
    assert report is not None
    assert getattr(report, "is_fallback", False) is True
    skip_events = [e for e in runner.events if e[0] == "reading_power_repair_skipped"]
    assert len(skip_events) >= 1


# ── Test 9 ───────────────────────────────────────────────────────────────────

async def test_model_gateway_error_graceful_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    """ModelGatewayError -> returns original text."""
    runner = _DummyRunner()
    bundle = _make_bundle()
    packet = _make_packet()
    bridge = _make_bridge()
    plan = _make_plan()

    async def _fake_eval(**kwargs):
        return _make_report(overall_score=3.0, hook_type="none")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.reading_power_repair.evaluate_and_record_reading_power",
        _fake_eval,
    )

    async def _fake_repair_run(_self, _input):
        raise ModelGatewayError("Connection refused")

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.reading_power_repair_step.ReadingPowerRepairStep.run",
        _fake_repair_run,
    )

    text, report, text_hash = await _execute_reading_power_repair_loop(
        runner, bundle, packet, bridge, plan, SAMPLE_TEXT, chapter_number=1, trace=SimpleNamespace(total_tokens=0),
    )

    assert text == SAMPLE_TEXT
    error_events = [e for e in runner.events if "gateway_error" in e[0]]
    assert len(error_events) >= 1


async def test_internal_repair_error_keeps_original_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unexpected repair exceptions should not fail the chapter or keep partial text."""
    runner = _DummyRunner()
    bundle = _make_bundle()
    packet = _make_packet()
    bridge = _make_bridge()
    plan = _make_plan()

    async def _fake_eval(**kwargs):
        return _make_report(overall_score=3.0, hook_type="none")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.reading_power_repair.evaluate_and_record_reading_power",
        _fake_eval,
    )

    async def _fake_repair_run(_self, _input):
        raise RuntimeError("repair boom")

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.reading_power_repair_step.ReadingPowerRepairStep.run",
        _fake_repair_run,
    )

    text, report, text_hash = await _execute_reading_power_repair_loop(
        runner, bundle, packet, bridge, plan, SAMPLE_TEXT, chapter_number=1, trace=SimpleNamespace(total_tokens=0),
    )

    assert text == SAMPLE_TEXT
    assert report is not None
    assert text_hash == hashlib.sha256(SAMPLE_TEXT.encode("utf-8")).hexdigest()
    assert any(e[0] == "reading_power_repair_internal_error" for e in runner.events)


async def test_recheck_error_rolls_back_unverified_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    """If post-repair RP recheck crashes, discard that round's revised text."""
    runner = _DummyRunner()
    bundle = _make_bundle()
    packet = _make_packet()
    bridge = _make_bridge()
    plan = _make_plan()
    eval_count = {"n": 0}

    async def _fake_eval(**kwargs):
        eval_count["n"] += 1
        if eval_count["n"] == 1:
            return _make_report(overall_score=3.0, hook_type="none")
        raise RuntimeError("recheck boom")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.reading_power_repair.evaluate_and_record_reading_power",
        _fake_eval,
    )

    async def _fake_repair_run(_self, _input):
        return ReadingPowerRepairResult(
            revised_text=SAMPLE_TEXT + "\n修复后的钩子内容。",
            applied=True,
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.reading_power_repair_step.ReadingPowerRepairStep.run",
        _fake_repair_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.repair_orchestration.loop_runner.detect_drift",
        lambda *args, **kwargs: DriftReport(signals=[]),
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.dedup_pronoun.run_self_repetition_check",
        lambda runner, text: (text, None),
    )

    text, report, text_hash = await _execute_reading_power_repair_loop(
        runner, bundle, packet, bridge, plan, SAMPLE_TEXT, chapter_number=1, trace=SimpleNamespace(total_tokens=0),
    )

    assert text == SAMPLE_TEXT
    assert report is not None
    assert eval_count["n"] == 2
    assert any(e[0] == "reading_power_recheck_internal_error" for e in runner.events)


# ── Test 10 ──────────────────────────────────────────────────────────────────

async def test_change_budget_exceeded_event_emitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """change_ratio > budget -> event emitted and text rolled back."""
    runner = _DummyRunner(long_reading_power_repair_max_change_ratio=0.10)
    bundle = _make_bundle()
    packet = _make_packet()
    bridge = _make_bridge()
    plan = _make_plan()

    eval_count = {"n": 0}

    async def _fake_eval(**kwargs):
        eval_count["n"] += 1
        if eval_count["n"] == 1:
            return _make_report(overall_score=3.0, hook_type="none")
        return _make_report(overall_score=5.0, hook_type="mystery")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.reading_power_repair.evaluate_and_record_reading_power",
        _fake_eval,
    )

    async def _fake_repair_run(_self, _input):
        return ReadingPowerRepairResult(
            revised_text="完全不同的内容" * 100,
            applied=True,
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.reading_power_repair_step.ReadingPowerRepairStep.run",
        _fake_repair_run,
    )

    def _fake_detect_drift(*args, **kwargs):
        return DriftReport(signals=[])

    monkeypatch.setattr(
        "novel_forge.pipeline.repair_orchestration.loop_runner.detect_drift",
        _fake_detect_drift,
    )

    def _fake_dedup(runner, text):
        return text, None

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.dedup_pronoun.run_self_repetition_check",
        _fake_dedup,
    )

    text, report, text_hash = await _execute_reading_power_repair_loop(
        runner, bundle, packet, bridge, plan, SAMPLE_TEXT, chapter_number=1, trace=SimpleNamespace(total_tokens=0),
    )

    assert text == SAMPLE_TEXT
    budget_events = [e for e in runner.events if "change_budget_exceeded" in e[0]]
    assert len(budget_events) >= 1
    payload = budget_events[0][1]
    assert "change_ratio" in payload
    assert "threshold" in payload


def test_reading_power_runner_uses_generic_issue_signature_default() -> None:
    """Reading power should not maintain a parallel issue-signature algorithm."""
    runner = ReadingPowerRepairRunner(
        runner=_DummyRunner(),
        bundle=_make_bundle(),
        packet=_make_packet(),
        bridge=_make_bridge(),
        plan=_make_plan(),
        trace=SimpleNamespace(total_tokens=0),
        config=RepairLoopConfig(),
        on_step=lambda *args: None,
        chapter_number=1,
    )

    assert "issue_signature" not in ReadingPowerRepairRunner.__dict__
    assert runner.issue_signature(
        SimpleNamespace(issue_id="rp-1", issue_type="hook_missing", summary="章尾弱")
    ) == "id:rp-1"
    assert (
        runner.issue_signature({"issue_type": "hook_missing", "summary": "章尾弱"})
        == "hook_missing:章尾弱"
    )


async def test_execute_reading_power_repair_loop_delegates_to_runner_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The active entrypoint remains a domain adapter over ReadingPowerRepairRunner.run()."""
    from novel_forge.pipeline.long.stages.reading_power_repair import (
        ReadingPowerRepairLoopResult,
    )

    runner = _DummyRunner(long_reading_power_max_repair_rounds=1)
    bundle = _make_bundle()
    packet = _make_packet()
    bridge = _make_bridge()
    plan = _make_plan()
    delegated: dict[str, object] = {}
    delegated_text = SAMPLE_TEXT + "\n由统一 runner 返回的修复文本。"

    async def _fake_eval(**kwargs):
        return _make_report(overall_score=3.0, hook_type="none")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.reading_power_repair.evaluate_and_record_reading_power",
        _fake_eval,
    )

    async def _fake_runner_run(self, current_text, initial_report):
        delegated["runner"] = self
        delegated["current_text"] = current_text
        delegated["initial_report"] = initial_report
        return ReadingPowerRepairLoopResult(
            current_text=delegated_text,
            report=_make_good_report(),
            text_hash="runner-owned-before-final-record",
            rounds_used=1,
            applied=True,
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.reading_power_repair.ReadingPowerRepairRunner.run",
        _fake_runner_run,
    )

    result = await _execute_reading_power_repair_loop(
        runner,
        bundle,
        packet,
        bridge,
        plan,
        SAMPLE_TEXT,
        chapter_number=1,
        trace=SimpleNamespace(total_tokens=0),
    )

    assert isinstance(delegated["runner"], ReadingPowerRepairRunner)
    assert delegated["current_text"] == SAMPLE_TEXT
    assert delegated["initial_report"].overall_score == 3.0
    assert result.current_text == delegated_text
    assert result.applied is True
    assert result.text_hash == hashlib.sha256(delegated_text.encode("utf-8")).hexdigest()


async def test_golden_delegation_to_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Golden fixture: verifies _execute_reading_power_repair_loop delegates to ReadingPowerRepairRunner.

    Captures end-to-end behavior: low score triggers repair, repair improves text,
    re-evaluation confirms improvement, loop exits with correct result shape.
    """
    from novel_forge.pipeline.long.stages.reading_power_repair import (
        ReadingPowerRepairLoopResult,
    )

    runner = _DummyRunner(long_reading_power_max_repair_rounds=2)
    bundle = _make_bundle()
    packet = _make_packet()
    bridge = _make_bridge()
    plan = _make_plan()

    call_count = {"eval": 0, "repair": 0}

    async def _fake_eval(**kwargs):
        call_count["eval"] += 1
        if call_count["eval"] == 1:
            return _make_report(overall_score=3.0, hook_type="none", hook_strength="weak")
        return _make_good_report()

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.reading_power_repair.evaluate_and_record_reading_power",
        _fake_eval,
    )

    async def _fake_repair_run(_self, _input):
        call_count["repair"] += 1
        return ReadingPowerRepairResult(
            revised_text=SAMPLE_TEXT + "\n章尾留下一个未解之谜。",
            applied=True,
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.reading_power_repair_step.ReadingPowerRepairStep.run",
        _fake_repair_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.repair_orchestration.loop_runner.detect_drift",
        lambda *args, **kwargs: DriftReport(signals=[]),
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.repair_orchestration.loop_runner.diff_issues",
        lambda before, after, **kwargs: LedgerDiff(),
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.dedup_pronoun.run_self_repetition_check",
        lambda runner, text: (text, None),
    )

    result = await _execute_reading_power_repair_loop(
        runner, bundle, packet, bridge, plan, SAMPLE_TEXT,
        chapter_number=1, trace=SimpleNamespace(total_tokens=0),
    )

    assert isinstance(result, ReadingPowerRepairLoopResult)
    assert result.current_text != SAMPLE_TEXT
    assert result.report is not None
    assert result.text_hash is not None
    assert call_count["repair"] >= 1
    assert call_count["eval"] >= 2
    assert result.applied is True
    repair_events = [e for e in runner.events if "reading_power" in e[0] and "repair" in e[0]]
    assert len(repair_events) >= 1
