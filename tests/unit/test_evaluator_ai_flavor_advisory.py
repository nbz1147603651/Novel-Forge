"""Unit tests for M5 — ai_flavor advisory block on EvalReport.

Design (see docs/ai_flavor_quality.md, plan §2.2.4):
- The advisory block is **derived deterministically** from a HumanizeReport
  (or list of pattern hits) at evaluator time.
- It is **non-blocking**: it never modifies EvalReport.scores, overall_score,
  or passed. It only fills a new `ai_flavor_advisory` field.
- The block is consumed by downstream calibration tooling
  (``scripts/calibrate_ai_flavor_distribution.py``) to decide whether to
  promote the dimension to a hard gate in rubric v4+.

This test suite covers:
1. Helper: ``DraftEvaluator._build_ai_flavor_advisory``
2. Schema: ``EvalReport.ai_flavor_advisory`` field exists, serializes round-trip
3. Behavior: passing the humanize report stamps the advisory; absence leaves it empty
4. Format: hits are grouped by pattern_id; severity counts; evidence quotes truncated
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.eval_schema import EvalReport, EvalScore
from novel_forge.core.schemas.humanize import HumanizePatternHit, HumanizeReport
from novel_forge.eval.evaluator import DraftEvaluator
from novel_forge.gateway.types import ModelRequest, ModelResponse

# ---------------------------------------------------------------------------
# Mock fixtures
# ---------------------------------------------------------------------------


@dataclass
class _MockRouter:
    """Minimal stand-in for ModelRouter; not actually called in advisory path."""


@dataclass
class _MockBuilder:
    pass


@dataclass
class _MockSettings:
    temp_evaluate: float = 0.0


def _make_evaluator() -> DraftEvaluator:
    """Construct a DraftEvaluator that we never actually call."""
    return DraftEvaluator(
        router=_MockRouter(),  # type: ignore[arg-type]
        builder=_MockBuilder(),  # type: ignore[arg-type]
        settings=_MockSettings(),  # type: ignore[arg-type]
    )


class _RecordingBuilder:
    def __init__(self) -> None:
        self.contexts: list[dict[str, Any]] = []

    def build(
        self,
        task_type: TaskType,
        context: dict[str, Any],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        thinking: bool = False,
        multi_turn: bool = False,
        prior_messages: list[dict[str, str]] | None = None,
    ) -> ModelRequest:
        self.contexts.append(dict(context))
        messages = [{"role": "user", "content": str(context.get("draft_text", ""))}]
        if prior_messages:
            messages = prior_messages + messages
        return ModelRequest(
            task_type=task_type,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            thinking=thinking,
            multi_turn=multi_turn,
        )


class _SequencedRouter:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)

    async def route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
    ) -> ModelResponse:
        return self._responses.pop(0)


def _valid_eval_response() -> str:
    return (
        '{"scores":['
        '{"dimension":"consistency","score":8.0,"comment":"结构和信息承接稳定"},'
        '{"dimension":"style","score":8.0,"comment":"语言风格稳定且有细节"}'
        '],"overall_score":8.0,"passed":true,"threshold":6.0,'
        '"summary":"整体质量稳定。","repair_suggestions":[]}'
    )


def _hit(
    pattern_id: str,
    severity: str,
    confidence: float = 0.9,
    evidence: str = "一些证据文本",
) -> HumanizePatternHit:
    return HumanizePatternHit(
        pattern_id=pattern_id,
        pattern_name=pattern_id,
        category="test",
        severity=severity,
        evidence_quote=evidence,
        confidence=confidence,
        actionable=True,
        source="llm",
    )


def _make_report(hits: list[HumanizePatternHit]) -> HumanizeReport:
    return HumanizeReport(
        source_text_hash="abc",
        chapter_number=1,
        total_hits=len(hits),
        pattern_hits=hits,
        hits_by_category={},
        critical_hits=sum(1 for h in hits if h.severity == "critical"),
    )


# ---------------------------------------------------------------------------
# Helper behavior
# ---------------------------------------------------------------------------


class TestBuildAIFlavorAdvisory:
    def test_empty_report_produces_empty_advisory(self) -> None:
        evaluator = _make_evaluator()
        report = _make_report([])
        advisory = evaluator._build_ai_flavor_advisory(report)
        # Empty input → empty advisory, but advisory itself is still a dict.
        assert advisory["hit_count"] == 0
        assert advisory["critical_count"] == 0
        assert advisory["by_pattern_id"] == {}
        assert advisory["by_severity"] == {}
        assert advisory["deterministic_score"] == 10.0  # no penalty
        assert advisory["evidence"] == []

    def test_aggregates_by_pattern_id(self) -> None:
        evaluator = _make_evaluator()
        hits = [
            _hit("weak_verb_stacking", "high"),
            _hit("weak_verb_stacking", "medium"),
            _hit("tautology_marker", "high"),
            _hit("binary_judgment_closing", "high"),
        ]
        report = _make_report(hits)
        advisory = evaluator._build_ai_flavor_advisory(report)
        assert advisory["by_pattern_id"]["weak_verb_stacking"] == 2
        assert advisory["by_pattern_id"]["tautology_marker"] == 1
        assert advisory["by_pattern_id"]["binary_judgment_closing"] == 1

    def test_aggregates_by_severity(self) -> None:
        evaluator = _make_evaluator()
        hits = [
            _hit("a", "critical"),
            _hit("b", "high"),
            _hit("c", "high"),
            _hit("d", "medium"),
        ]
        report = _make_report(hits)
        advisory = evaluator._build_ai_flavor_advisory(report)
        assert advisory["by_severity"]["critical"] == 1
        assert advisory["by_severity"]["high"] == 2
        assert advisory["by_severity"]["medium"] == 1
        assert advisory["by_severity"]["low"] == 0

    def test_deterministic_score_formula(self) -> None:
        """Deterministic score mirrors QualityGate._AI_FLAVOR_SEVERITY_WEIGHTS."""
        evaluator = _make_evaluator()
        hits = [
            _hit("a", "critical", confidence=1.0),  # -4
            _hit("b", "high", confidence=1.0),      # -2
            _hit("c", "medium", confidence=1.0),     # -1
            _hit("d", "low", confidence=1.0),        # -0.5
        ]
        report = _make_report(hits)
        advisory = evaluator._build_ai_flavor_advisory(report)
        # penalty = 4 + 2 + 1 + 0.5 = 7.5 → score = 2.5; but critical caps at 5.0
        # since 2.5 < 5.0, no capping needed
        assert advisory["deterministic_score"] == pytest.approx(2.5)

    def test_critical_caps_score_at_5(self) -> None:
        evaluator = _make_evaluator()
        # Two low hits would normally give 9.0, but a critical caps at 5.0
        hits = [
            _hit("a", "critical", confidence=0.5),
            _hit("b", "low", confidence=0.5),
            _hit("c", "low", confidence=0.5),
        ]
        report = _make_report(hits)
        advisory = evaluator._build_ai_flavor_advisory(report)
        assert advisory["deterministic_score"] == 5.0
        assert advisory["critical_count"] == 1

    def test_evidence_quotes_truncated(self) -> None:
        evaluator = _make_evaluator()
        long_evidence = "一" * 200
        hits = [_hit("a", "high", evidence=long_evidence)]
        report = _make_report(hits)
        advisory = evaluator._build_ai_flavor_advisory(report)
        assert len(advisory["evidence"]) == 1
        evidence_quote = advisory["evidence"][0]["evidence_quote"]
        # Should be truncated to a reasonable length.
        assert len(evidence_quote) <= 80

    def test_accepts_dict_list_for_testability(self) -> None:
        """The helper accepts plain dicts (e.g. from HumanizeScanStep output)."""
        evaluator = _make_evaluator()
        hits_dict = [
            {"pattern_id": "weak_verb_stacking", "severity": "high", "confidence": 0.9,
             "evidence_quote": "X"},
            {"pattern_id": "tautology_marker", "severity": "high", "confidence": 0.9,
             "evidence_quote": "Y"},
        ]
        advisory = evaluator._build_ai_flavor_advisory(hits_dict)
        assert advisory["hit_count"] == 2

    def test_handles_none(self) -> None:
        evaluator = _make_evaluator()
        advisory = evaluator._build_ai_flavor_advisory(None)
        assert advisory["hit_count"] == 0

    def test_confidence_is_clamped_to_quality_gate_range(self) -> None:
        evaluator = _make_evaluator()
        advisory = evaluator._build_ai_flavor_advisory([
            {"pattern_id": "over", "severity": "high", "confidence": 2.0},
            {"pattern_id": "under", "severity": "medium", "confidence": -1.0},
            {"pattern_id": "invalid", "severity": "low", "confidence": "bad"},
        ])
        # Mirrors QualityGate.check_ai_flavor: confidence is clamped to 0..1,
        # invalid values use the default 0.8.
        assert advisory["deterministic_score"] == pytest.approx(7.6)


# ---------------------------------------------------------------------------
# EvalReport schema integration
# ---------------------------------------------------------------------------


class TestEvalReportAIFlavorAdvisoryField:
    def test_field_exists(self) -> None:
        """EvalReport must accept ai_flavor_advisory without schema error."""
        report = EvalReport(
            scores=[
                EvalScore(dimension="consistency", score=8.0),
                EvalScore(dimension="style", score=7.0),
            ],
            overall_score=7.5,
            passed=True,
            threshold=6.0,
            ai_flavor_advisory={
                "hit_count": 2,
                "deterministic_score": 8.0,
                "by_pattern_id": {"weak_verb_stacking": 2},
                "by_severity": {"high": 2},
                "evidence": [],
            },
        )
        assert report.ai_flavor_advisory["hit_count"] == 2
        assert report.ai_flavor_advisory["deterministic_score"] == 8.0

    def test_default_field_value(self) -> None:
        """Field default = empty advisory dict for backward compat."""
        report = EvalReport(scores=[EvalScore(dimension="consistency", score=8.0)])
        assert report.ai_flavor_advisory == {} or report.ai_flavor_advisory.get("hit_count") == 0

    def test_advisory_does_not_change_overall_score(self) -> None:
        """The advisory MUST NOT affect overall_score or passed — it's diagnostic only."""
        report = EvalReport(
            scores=[
                EvalScore(dimension="consistency", score=8.0),
                EvalScore(dimension="style", score=8.0),
            ],
            ai_flavor_advisory={
                "hit_count": 10,
                "deterministic_score": 1.0,  # terrible ai flavor
                "by_pattern_id": {},
                "by_severity": {},
                "evidence": [],
            },
        )
        report.compute_overall()
        # overall is still 8.0 — ai_flavor advisory is non-blocking.
        assert report.overall_score == 8.0
        assert report.passed is True


# ---------------------------------------------------------------------------
# Integration with stamp_report_metadata
# ---------------------------------------------------------------------------


class TestAIFlavorAdvisoryOnEvalReport:
    def test_rubric_version_bump(self) -> None:
        """M5 advisory block should be reflected in the rubric_version stamp."""
        from novel_forge.eval.evaluator import DraftEvaluator
        version = DraftEvaluator.RUBRIC_VERSION
        # Confirm a v3 marker exists.
        assert "v3" in version, f"Expected v3 in RUBRIC_VERSION, got {version}"

    def test_advisory_stamped_into_score_diagnostics(self) -> None:
        """The advisory hit_count should land in score_diagnostics for log/output."""
        evaluator = _make_evaluator()
        # Build a real EvalReport (not a HumanizeReport).
        eval_report = EvalReport(
            scores=[EvalScore(dimension="style", score=8.0)],
            ai_flavor_advisory={"hit_count": 1, "deterministic_score": 8.0},
        )
        stamped = evaluator._stamp_report_metadata(eval_report)
        # rubric_version stamped with v3 marker (M5)
        assert "v3" in stamped.rubric_version
        # Advisory field remains on the report.
        assert stamped.ai_flavor_advisory["hit_count"] == 1


class TestAIFlavorAdvisoryEvaluateIntegration:
    @pytest.mark.asyncio
    async def test_evaluate_stamps_advisory_from_humanize_report(self) -> None:
        builder = _RecordingBuilder()
        evaluator = DraftEvaluator(
            _SequencedRouter([ModelResponse(content=_valid_eval_response(), model_id="mock")]),  # type: ignore[arg-type]
            builder,  # type: ignore[arg-type]
            settings=Settings(_env_file=None),
        )
        humanize_report = _make_report([
            _hit("weak_verb_stacking", "high", confidence=1.0),
        ])

        report = await evaluator.evaluate("测试正文", humanize_report=humanize_report)

        assert report.overall_score == 8.0
        assert report.passed is True
        assert report.ai_flavor_advisory["hit_count"] == 1
        assert report.ai_flavor_advisory["deterministic_score"] == 8.0
        assert report.score_diagnostics["ai_flavor_advisory"]["hit_count"] == 1
        assert "humanize_report" not in builder.contexts[0]

    @pytest.mark.asyncio
    async def test_evaluate_stamps_empty_advisory_when_empty_hits_are_provided(self) -> None:
        evaluator = DraftEvaluator(
            _SequencedRouter([ModelResponse(content=_valid_eval_response(), model_id="mock")]),  # type: ignore[arg-type]
            _RecordingBuilder(),  # type: ignore[arg-type]
            settings=Settings(_env_file=None),
        )

        report = await evaluator.evaluate("测试正文", ai_flavor_hits=[])

        assert report.ai_flavor_advisory["hit_count"] == 0
        assert report.ai_flavor_advisory["deterministic_score"] == 10.0
        assert report.overall_score == 8.0

    @pytest.mark.asyncio
    async def test_evaluate_leaves_advisory_empty_without_source(self) -> None:
        evaluator = DraftEvaluator(
            _SequencedRouter([ModelResponse(content=_valid_eval_response(), model_id="mock")]),  # type: ignore[arg-type]
            _RecordingBuilder(),  # type: ignore[arg-type]
            settings=Settings(_env_file=None),
        )

        report = await evaluator.evaluate("测试正文")

        assert report.ai_flavor_advisory == {}

    @pytest.mark.asyncio
    async def test_default_report_keeps_advisory_after_parse_failure(self) -> None:
        evaluator = DraftEvaluator(
            _SequencedRouter([
                ModelResponse(content="not json", model_id="mock"),
                ModelResponse(content="still not json", model_id="mock"),
            ]),  # type: ignore[arg-type]
            _RecordingBuilder(),  # type: ignore[arg-type]
            settings=Settings(_env_file=None),
        )

        report = await evaluator.evaluate(
            "测试正文",
            ai_flavor_hits=[
                {"pattern_id": "tautology_marker", "severity": "high", "confidence": 1.0},
            ],
        )

        assert report.score_confidence == "fallback"
        assert report.ai_flavor_advisory["hit_count"] == 1
        assert report.score_diagnostics["ai_flavor_advisory"]["hit_count"] == 1
