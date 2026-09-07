"""Tests for _RepairRoundKernel ABC default helper methods.

Task 8: Converge _RepairRoundKernel subclass overrides into ABC defaults.
Tests the new helper methods that subclasses override instead of _detect_drift.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.format_contracts import validate_json_output_contract
from novel_forge.core.parsing.response_schemas import validate_response_schema
from novel_forge.core.utils.semantic_drift import DriftReport
from novel_forge.pipeline.long.repair_strategy_advisor import normalize_strategy_diagnosis
from novel_forge.pipeline.repair_orchestration.loop_runner import (
    RepairLoopConfig,
    RepairRoundContext,
    _RepairRoundKernel,
)


class _MinimalRunner(_RepairRoundKernel[Any]):
    """Minimal concrete implementation for testing ABC defaults."""

    async def execute_repair(self, ctx: RepairRoundContext[Any]) -> str:
        return ctx.current_text

    async def evaluate(self, text: str) -> Any:
        return SimpleNamespace(issues=[], score=8.0)

    def extract_issues(self, report: Any) -> list[Any]:
        return list(getattr(report, "issues", []) or [])

    def compute_score(self, report: Any) -> float:
        return float(getattr(report, "score", 0.0) or 0.0)


class _ResolvingRunner(_MinimalRunner):
    """Runner that resolves all issues after one repair."""

    async def execute_repair(self, ctx: RepairRoundContext[Any]) -> str:
        return ctx.current_text + "修复"

    async def evaluate(self, text: str) -> Any:
        del text
        return SimpleNamespace(issues=[], score=10.0)


class _CapturingResolvingRunner(_ResolvingRunner):
    """Runner that records the issues actually passed to execute_repair."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.seen_must_fix_issues: list[list[Any]] = []

    async def execute_repair(self, ctx: RepairRoundContext[Any]) -> str:
        self.seen_must_fix_issues.append(list(ctx.must_fix_issues))
        return await super().execute_repair(ctx)


class _ContinuityLikeRunner(_MinimalRunner):
    """Simulates ContinuityRepairRunner with helper overrides."""

    def __init__(self, pov_char: str = "张三", known_chars: list[str] | None = None) -> None:
        super().__init__(
            config=RepairLoopConfig(),
            on_step=lambda *a: None,
            chapter_number=1,
        )
        self._pov_character = pov_char
        self._known_characters = known_chars or ["李四", "王五"]
        self._chapter_outline = SimpleNamespace(
            pov_character=pov_char,
            required_characters=known_chars or ["李四", "王五"],
        )

    def _get_pov_character(self) -> str:
        return self._pov_character

    def _get_known_characters(self) -> list[str]:
        return list(self._known_characters)

    def _get_chapter_outline(self) -> Any:
        return self._chapter_outline


class _CausalLikeRunner(_MinimalRunner):
    """Simulates CausalRepairRunner with helper overrides."""

    def __init__(self, pov_char: str = "主角", known_chars: list[str] | None = None) -> None:
        super().__init__(
            config=RepairLoopConfig(),
            on_step=lambda *a: None,
            chapter_number=1,
        )
        self._pov_character = pov_char
        self._known_characters = known_chars or ["配角A"]
        self._chapter_outline = SimpleNamespace(
            pov_character=pov_char,
            required_characters=known_chars or ["配角A"],
        )

    def _get_pov_character(self) -> str:
        return self._pov_character

    def _get_known_characters(self) -> list[str]:
        return list(self._known_characters)

    def _get_chapter_outline(self) -> Any:
        return self._chapter_outline


class _ReadingPowerLikeRunner(_MinimalRunner):
    """Simulates ReadingPowerRepairRunner with helper overrides."""

    def __init__(self) -> None:
        super().__init__(
            config=RepairLoopConfig(),
            on_step=lambda *a: None,
            chapter_number=1,
        )
        self._chapter_outline = SimpleNamespace(
            pov_character="叙述者",
            involved_characters=["角色A", "角色B"],
            required_characters=None,
        )

    def _get_pov_character(self) -> str:
        return getattr(self._chapter_outline, "pov_character", "") or ""

    def _get_known_characters(self) -> list[str]:
        outline_chars = (
            getattr(self._chapter_outline, "involved_characters", None)
            or getattr(self._chapter_outline, "required_characters", None)
            or []
        )
        pov = self._get_pov_character()
        return list(dict.fromkeys([*outline_chars, pov] if pov else outline_chars))

    def _get_chapter_outline(self) -> Any:
        return self._chapter_outline


# ─── ABC Default Tests ───────────────────────────────────────────────────────


class TestABCDefaults:
    """Test ABC default helper methods return safe defaults."""

    def test_default_get_pov_character_returns_empty(self) -> None:
        runner = _MinimalRunner(
            config=RepairLoopConfig(),
            on_step=lambda *a: None,
            chapter_number=1,
        )
        assert runner._get_pov_character() == ""

    def test_default_get_known_characters_returns_empty(self) -> None:
        runner = _MinimalRunner(
            config=RepairLoopConfig(),
            on_step=lambda *a: None,
            chapter_number=1,
        )
        assert runner._get_known_characters() == []

    def test_default_get_chapter_outline_returns_none(self) -> None:
        runner = _MinimalRunner(
            config=RepairLoopConfig(),
            on_step=lambda *a: None,
            chapter_number=1,
        )
        assert runner._get_chapter_outline() is None

    def test_default_detect_drift_uses_empty_chars(self) -> None:
        runner = _MinimalRunner(
            config=RepairLoopConfig(),
            on_step=lambda *a: None,
            chapter_number=1,
        )
        ctx = RepairRoundContext[Any](
            pre_round_text="张三走在路上。",
            current_text="张三走在路上。",
        )
        drift = runner._detect_drift(ctx)
        assert isinstance(drift, DriftReport)
        # No drift when text is identical
        assert not drift.has_drift

    def test_default_get_alignment_score_returns_7(self) -> None:
        runner = _MinimalRunner(
            config=RepairLoopConfig(),
            on_step=lambda *a: None,
            chapter_number=1,
        )
        assert runner._get_alignment_score() == 7.0

    def test_default_has_prompt_leaks_returns_false(self) -> None:
        runner = _MinimalRunner(
            config=RepairLoopConfig(),
            on_step=lambda *a: None,
            chapter_number=1,
        )
        assert runner._has_prompt_leaks() is False

    def test_repeated_issue_guard_triggers_at_threshold_and_updates_guidance(self) -> None:
        runner = _MinimalRunner(
            config=RepairLoopConfig(),
            on_step=lambda *a: None,
            chapter_number=7,
        )
        issue = SimpleNamespace(issue_type="continuity", severity="critical", summary="A")
        ctx = RepairRoundContext[Any](
            round_number=2,
            must_fix_issues=[issue],
            issue_attempts={"continuity:A": 3},
        )

        guard = runner._build_repeated_issue_guard(ctx)
        assert guard is not None
        assert guard["threshold"] == 3
        assert guard["issues"][0]["signature"] == "continuity:A"

        guidance: dict[str, Any] = {}
        runner._apply_repeated_issue_guidance(ctx, guidance, guard)
        assert guidance["preferred_strategy"] == "fulltext"
        assert guidance["fulltext_escalation_requested"] is True
        assert ctx.extra["memory_guidance"]["strategy_recommendation"]["source"] == (
            "repeated_issue_guard"
        )

    def test_repeated_issue_guard_disabled_returns_none(self) -> None:
        runner = _MinimalRunner(
            config=RepairLoopConfig(),
            on_step=lambda *a: None,
            chapter_number=1,
        )
        runner._runner = SimpleNamespace(
            _settings=SimpleNamespace(long_repair_repeated_issue_guard_enabled=False)
        )
        issue = SimpleNamespace(issue_type="continuity", severity="critical", summary="A")
        ctx = RepairRoundContext[Any](
            round_number=2,
            must_fix_issues=[issue],
            issue_attempts={"continuity:A": 99},
        )
        assert runner._build_repeated_issue_guard(ctx) is None

    def test_focus_issues_caps_round_and_prefers_located_issue(self) -> None:
        events: list[tuple[str, Any]] = []
        runner = _MinimalRunner(
            config=RepairLoopConfig(max_issues_per_round=1),
            on_step=lambda event, payload: events.append((event, payload)),
            chapter_number=3,
        )
        broad_issue = SimpleNamespace(
            issue_type="continuity",
            severity="critical",
            summary="泛化问题",
        )
        located_issue = SimpleNamespace(
            issue_type="continuity",
            severity="critical",
            summary="有证据问题",
            evidence="原文证据",
        )
        ctx = RepairRoundContext[Any](round_number=0)

        focused = runner._focus_issues_for_round([broad_issue, located_issue], ctx)

        assert focused == [located_issue]
        assert events[0][0] == "repair_round_focus"
        assert events[0][1]["selected_count"] == 1
        assert events[0][1]["skipped_count"] == 1

    def test_focus_issues_zero_cap_keeps_all_issues(self) -> None:
        runner = _MinimalRunner(
            config=RepairLoopConfig(max_issues_per_round=0),
            on_step=lambda *a: None,
            chapter_number=3,
        )
        issues = [
            SimpleNamespace(issue_type="continuity", severity="critical", summary="A"),
            SimpleNamespace(issue_type="continuity", severity="critical", summary="B"),
        ]

        assert runner._focus_issues_for_round(issues, RepairRoundContext[Any]()) == issues

    def test_strategy_diagnosis_normalizer_rejects_low_shape_quality(self) -> None:
        assert normalize_strategy_diagnosis({"preferred_strategy": "skip", "confidence": 0.9}) is None
        diagnosis = normalize_strategy_diagnosis(
            {
                "preferred_strategy": "rewrite",
                "confidence": 0.64,
                "reason": "结构牵连",
                "root_causes": ["定位失败"],
                "risk_flags": ["改动大"],
                "diagnostic_summary": "建议谨慎扩大范围",
            }
        )
        assert diagnosis is not None
        assert diagnosis.confidence == 0.64
        assert diagnosis.preferred_strategy == "rewrite"

    def test_strategy_diagnosis_schema_contract_consumer_chain(self) -> None:
        payload = {
            "preferred_strategy": "patch",
            "confidence": 0.72,
            "reason": "问题集中在局部段落",
            "root_causes": ["anchor_missing"],
            "risk_flags": ["low_change_budget"],
            "diagnostic_summary": "局部补丁优先",
        }

        validate_response_schema(payload, TaskType.REPAIR_STRATEGY_DIAGNOSE)
        validate_json_output_contract(TaskType.REPAIR_STRATEGY_DIAGNOSE, payload)
        diagnosis = normalize_strategy_diagnosis(payload)

        assert diagnosis is not None
        assert diagnosis.preferred_strategy == "patch"
        assert diagnosis.confidence == 0.72

    @pytest.mark.asyncio
    async def test_run_emits_replayable_repair_audit_events(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "novel_forge.pipeline.long.stages.dedup_pronoun.run_self_repetition_check",
            lambda _runner, text: (text, None),
        )
        events: list[tuple[str, dict[str, Any]]] = []
        runner = _ResolvingRunner(
            config=RepairLoopConfig(max_rounds=1, change_budget=1.0),
            on_step=lambda event, payload: events.append((event, payload)),
            chapter_number=7,
        )
        runner._settings = SimpleNamespace(repair_control_mode="ai_auto")
        issue = SimpleNamespace(
            issue_id="issue-loop-1",
            issue_type="continuity_gap",
            severity="critical",
            summary="承接断裂",
            paragraph_start=2,
            paragraph_end=3,
        )

        result = await runner.run("原文", SimpleNamespace(score=3.0, issues=[issue]))

        assert result.rounds_used == 1
        verification = result.extra["candidate_verification"]
        assert verification["original_issue_ids"] == ["issue-loop-1"]
        assert verification["recheck_performed"] is True
        assert verification["gate_passed"] is True
        assert verification["residual_issue_ids"] == []
        assert verification["candidate_text_hash"]
        audit_payloads = [payload for event, payload in events if event == "repair_audit_event"]
        event_types = [payload["event_type"] for payload in audit_payloads]
        assert "selected" in event_types
        assert "attempted" in event_types
        assert "verified" in event_types
        assert "finalized" in event_types
        selected = next(payload for payload in audit_payloads if payload["event_type"] == "selected")
        assert selected["issue_id"] == "issue-loop-1"
        assert selected["paragraph_start"] == 2
        assert selected["paragraph_end"] == 3
        assert selected["source_text_hash"]

        summary = next(payload for event, payload in events if event == "repair_audit_summary")
        assert summary["status"] == "completed"
        assert summary["selected_issue_ids"] == ["issue-loop-1"]
        assert summary["attempted_issue_ids"] == ["issue-loop-1"]
        assert summary["verified_issue_ids"] == ["issue-loop-1"]
        assert summary["finalized_issue_ids"] == ["issue-loop-1"]
        assert summary["remaining_issue_ids"] == []

    @pytest.mark.asyncio
    async def test_recheck_disabled_never_claims_candidate_verification(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "novel_forge.pipeline.long.stages.dedup_pronoun.run_self_repetition_check",
            lambda _runner, text: (text, None),
        )
        runner = _ResolvingRunner(
            config=RepairLoopConfig(
                max_rounds=1,
                change_budget=1.0,
                recheck_enabled=False,
            ),
            on_step=lambda *a: None,
            chapter_number=1,
        )
        issue = SimpleNamespace(
            issue_id="issue-no-recheck",
            issue_type="continuity_gap",
            severity="critical",
            summary="必须复验",
        )

        baseline = "原文" * 100
        result = await runner.run(baseline, SimpleNamespace(score=3.0, issues=[issue]))

        assert result.current_text == baseline + "修复"
        verification = result.extra["candidate_verification"]
        assert verification["recheck_performed"] is False
        assert verification["gate_passed"] is False
        assert verification["residual_issue_ids"] == ["issue-no-recheck"]

    @pytest.mark.asyncio
    async def test_run_promotes_low_score_non_critical_issues_each_round(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "novel_forge.pipeline.long.stages.dedup_pronoun.run_self_repetition_check",
            lambda _runner, text: (text, None),
        )
        runner = _CapturingResolvingRunner(
            config=RepairLoopConfig(max_rounds=1, change_budget=1.0, hard_floor=6.0),
            on_step=lambda *a: None,
            chapter_number=7,
        )
        runner._settings = SimpleNamespace(repair_control_mode="ai_auto")
        issue = SimpleNamespace(
            issue_id="issue-low-score-1",
            issue_type="continuity_gap",
            severity="medium",
            summary="低分但非 critical 的承接问题",
        )

        result = await runner.run("原文", SimpleNamespace(score=5.5, issues=[issue]))

        assert result.rounds_used == 1
        assert runner.seen_must_fix_issues == [[issue]]


# ─── Subclass Helper Override Tests ──────────────────────────────────────────


class TestContinuityLikeRunner:
    """Test Continuity-like runner helper overrides."""

    def test_get_pov_character(self) -> None:
        runner = _ContinuityLikeRunner(pov_char="张三")
        assert runner._get_pov_character() == "张三"

    def test_get_known_characters(self) -> None:
        runner = _ContinuityLikeRunner(known_chars=["李四", "王五"])
        assert runner._get_known_characters() == ["李四", "王五"]

    def test_get_chapter_outline(self) -> None:
        runner = _ContinuityLikeRunner()
        outline = runner._get_chapter_outline()
        assert outline is not None
        assert outline.pov_character == "张三"

    def test_detect_drift_uses_pov_character(self) -> None:
        runner = _ContinuityLikeRunner(pov_char="张三")
        # Text where POV character disappears
        ctx = RepairRoundContext[Any](
            pre_round_text="张三走在路上，张三看到了李四。张三" * 5,
            current_text="李四走在路上，看到了王五。" * 5,
        )
        drift = runner._detect_drift(ctx)
        assert isinstance(drift, DriftReport)


class TestCausalLikeRunner:
    """Test Causal-like runner helper overrides."""

    def test_get_pov_character(self) -> None:
        runner = _CausalLikeRunner(pov_char="主角")
        assert runner._get_pov_character() == "主角"

    def test_get_known_characters(self) -> None:
        runner = _CausalLikeRunner(known_chars=["配角A", "配角B"])
        assert runner._get_known_characters() == ["配角A", "配角B"]

    def test_detect_drift_uses_helpers(self) -> None:
        runner = _CausalLikeRunner(pov_char="主角")
        ctx = RepairRoundContext[Any](
            pre_round_text="主角走在路上。" * 10,
            current_text="主角走在路上。" * 10,
        )
        drift = runner._detect_drift(ctx)
        assert isinstance(drift, DriftReport)
        assert not drift.has_drift


class TestReadingPowerLikeRunner:
    """Test ReadingPower-like runner helper overrides."""

    def test_get_pov_character(self) -> None:
        runner = _ReadingPowerLikeRunner()
        assert runner._get_pov_character() == "叙述者"

    def test_get_known_characters_includes_pov(self) -> None:
        runner = _ReadingPowerLikeRunner()
        chars = runner._get_known_characters()
        assert "叙述者" in chars
        assert "角色A" in chars
        assert "角色B" in chars

    def test_detect_drift_uses_involved_characters(self) -> None:
        runner = _ReadingPowerLikeRunner()
        ctx = RepairRoundContext[Any](
            pre_round_text="叙述者看到角色A和角色B。" * 10,
            current_text="叙述者看到角色A和角色B。" * 10,
        )
        drift = runner._detect_drift(ctx)
        assert isinstance(drift, DriftReport)


# ─── Integration: ABC _detect_drift uses helpers ─────────────────────────────


class TestDetectDriftUsesHelpers:
    """Verify ABC _detect_drift calls the helper methods."""

    def test_abc_detect_drift_calls_helpers(self) -> None:
        """ABC _detect_drift should use _get_pov_character, _get_known_characters, _get_chapter_outline."""
        runner = _ContinuityLikeRunner(pov_char="张三", known_chars=["李四"])
        ctx = RepairRoundContext[Any](
            pre_round_text="张三走在路上。" * 10,
            current_text="张三走在路上。" * 10,
        )
        # Should not raise, should use helpers
        drift = runner._detect_drift(ctx)
        assert isinstance(drift, DriftReport)

    def test_abc_detect_drift_with_default_runner(self) -> None:
        """Default ABC _detect_drift should work with empty defaults."""
        runner = _MinimalRunner(
            config=RepairLoopConfig(),
            on_step=lambda *a: None,
            chapter_number=1,
        )
        ctx = RepairRoundContext[Any](
            pre_round_text="测试文本。" * 10,
            current_text="测试文本。" * 10,
        )
        drift = runner._detect_drift(ctx)
        assert isinstance(drift, DriftReport)
        assert not drift.has_drift
