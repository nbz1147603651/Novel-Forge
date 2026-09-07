"""Unit tests for QualityGate.check_ai_flavor().

The check accepts any object exposing ``pattern_hits`` (HumanizeReport),
a list of HumanizePatternHit, or a dict with the same shape. It returns a
QualityCheckResult using the severity-weighted formula:

    score = 10 - sum(severity_weight * confidence)
    if any critical: score = min(score, 5.0)

Critical hits ALWAYS fail the gate. High hits drop score by 2 per hit.
Medium by 1. Low by 0.5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from novel_forge.core.schemas.humanize import (
    HumanizePatternHit,
    HumanizeReport,
)
from novel_forge.pipeline.quality_gate import (
    QualityCheckResult,
    QualityGate,
    QualityVerdict,
)

# ---------------------------------------------------------------------------
# Mock fixtures — keep tests independent of the full HumanizeReport schema
# ---------------------------------------------------------------------------


@dataclass
class _MockHit:
    pattern_id: str
    severity: str
    confidence: float = 0.9


@dataclass
class _MockHumanizeReport:
    """Object exposing pattern_hits like HumanizeReport."""

    pattern_hits: list[Any] = field(default_factory=list)
    total_hits: int = 0
    critical_hits: int = 0


# ---------------------------------------------------------------------------
# Basic score formula
# ---------------------------------------------------------------------------


class TestAIFlavorScoring:
    def test_empty_report_scores_ten_and_passes(self) -> None:
        gate = QualityGate()
        report = _MockHumanizeReport(pattern_hits=[], total_hits=0)
        result = gate.check_ai_flavor(report)
        assert result.dimension == "ai_flavor"
        assert result.score == 10.0
        assert result.passed is True

    def test_single_high_hit_drops_score_by_two(self) -> None:
        gate = QualityGate()
        report = _MockHumanizeReport(
            pattern_hits=[_MockHit("weak_verb_stacking", "high", confidence=1.0)],
            total_hits=1,
        )
        result = gate.check_ai_flavor(report)
        assert result.score == pytest.approx(8.0)
        assert result.threshold == 8.0
        assert result.passed is True  # exactly at threshold

    def test_two_high_hits_fail(self) -> None:
        gate = QualityGate()
        report = _MockHumanizeReport(
            pattern_hits=[
                _MockHit("weak_verb_stacking", "high", 1.0),
                _MockHit("tautology_marker", "high", 1.0),
            ],
            total_hits=2,
        )
        result = gate.check_ai_flavor(report)
        assert result.score == pytest.approx(6.0)
        assert result.passed is False

    def test_medium_hit_drops_by_one(self) -> None:
        gate = QualityGate()
        report = _MockHumanizeReport(
            pattern_hits=[_MockHit("pronoun_disappearance_run", "medium", 1.0)],
            total_hits=1,
        )
        result = gate.check_ai_flavor(report)
        assert result.score == pytest.approx(9.0)
        assert result.passed is True

    def test_low_hit_drops_by_half(self) -> None:
        gate = QualityGate()
        report = _MockHumanizeReport(
            pattern_hits=[_MockHit("precise_timestamp_overuse", "low", 1.0)],
            total_hits=1,
        )
        result = gate.check_ai_flavor(report)
        assert result.score == pytest.approx(9.5)
        assert result.passed is True

    def test_confidence_scales_penalty(self) -> None:
        gate = QualityGate()
        # Half confidence halves the penalty.
        report = _MockHumanizeReport(
            pattern_hits=[_MockHit("weak_verb_stacking", "high", 0.5)],
            total_hits=1,
        )
        result = gate.check_ai_flavor(report)
        assert result.score == pytest.approx(9.0)

    def test_invalid_confidence_uses_default(self) -> None:
        gate = QualityGate()
        report = {"pattern_hits": [
            {"pattern_id": "weak_verb_stacking", "severity": "high", "confidence": "bad"}
        ]}
        result = gate.check_ai_flavor(report)
        assert result.score == pytest.approx(8.4)
        assert result.passed is True

    def test_confidence_is_clamped_to_unit_range(self) -> None:
        gate = QualityGate()
        report = {"pattern_hits": [
            {"pattern_id": "weak_verb_stacking", "severity": "high", "confidence": 2.0}
        ]}
        result = gate.check_ai_flavor(report)
        assert result.score == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# Critical cap
# ---------------------------------------------------------------------------


class TestAIFlavorCriticalCap:
    def test_any_critical_hit_caps_score_at_five(self) -> None:
        gate = QualityGate()
        # Two low hits would normally yield score 9.0 (10 - 2*0.5);
        # but a single critical hit caps the score at 5.0 regardless.
        report = _MockHumanizeReport(
            pattern_hits=[
                _MockHit("collaborative_artifact", "critical", 1.0),
                _MockHit("precise_timestamp_overuse", "low", 1.0),
                _MockHit("precise_timestamp_overuse", "low", 1.0),
            ],
            total_hits=3,
        )
        result = gate.check_ai_flavor(report)
        assert result.score == 5.0
        assert result.passed is False

    def test_critical_hit_alone_caps_at_five(self) -> None:
        gate = QualityGate()
        report = _MockHumanizeReport(
            pattern_hits=[_MockHit("collaborative_artifact", "critical", 0.5)],
            total_hits=1,
        )
        result = gate.check_ai_flavor(report)
        assert result.score == 5.0
        assert result.passed is False


# ---------------------------------------------------------------------------
# Threshold override
# ---------------------------------------------------------------------------


class TestAIFlavorThreshold:
    def test_custom_threshold(self) -> None:
        gate = QualityGate()
        # 3 medium hits → score 7.0. With default 8.0 threshold it fails,
        # but with threshold=6.0 it passes.
        report = _MockHumanizeReport(
            pattern_hits=[_MockHit("p" + str(i), "medium", 1.0) for i in range(3)],
            total_hits=3,
        )
        result_strict = gate.check_ai_flavor(report, threshold=8.0)
        assert result_strict.passed is False

        gate.reset()
        result_loose = gate.check_ai_flavor(report, threshold=6.0)
        assert result_loose.passed is True


# ---------------------------------------------------------------------------
# Acceptance shape — works with real HumanizeReport
# ---------------------------------------------------------------------------


class TestAIFlavorAcceptsRealHumanizeReport:
    def test_real_humanize_report_with_hits(self) -> None:
        gate = QualityGate()
        real_hits = [
            HumanizePatternHit(
                pattern_id="weak_verb_stacking",
                pattern_name="弱动词堆叠",
                category="叙事轻重",
                severity="high",
                evidence_quote="她感到一种难以言喻的感觉",
                confidence=0.85,
            ),
            HumanizePatternHit(
                pattern_id="tautology_marker",
                pattern_name="抽象虚指三连",
                category="模板句式",
                severity="high",
                evidence_quote="某种...某种...某种",
                confidence=0.92,
            ),
        ]
        real_report = HumanizeReport(
            source_text_hash="abc123",
            chapter_number=21,
            total_hits=2,
            pattern_hits=real_hits,
            hits_by_category={"叙事轻重": 1, "模板句式": 1},
            critical_hits=0,
        )
        result = gate.check_ai_flavor(real_report)
        assert isinstance(result, QualityCheckResult)
        assert result.dimension == "ai_flavor"
        # 2 * high (severity_weight 2.0) * 0.85 + 2.0 * 0.92 = 1.7 + 1.84 = 3.54
        # score = 10 - 3.54 = 6.46
        assert result.score == pytest.approx(6.46, abs=0.01)
        assert result.passed is False  # below threshold 8.0


# ---------------------------------------------------------------------------
# Details / metadata — exposed for downstream repair / UI display
# ---------------------------------------------------------------------------


class TestAIFlavorDetails:
    def test_details_contains_hit_count(self) -> None:
        gate = QualityGate()
        report = _MockHumanizeReport(
            pattern_hits=[
                _MockHit("weak_verb_stacking", "high", 0.9),
                _MockHit("tautology_marker", "high", 0.9),
            ],
            total_hits=2,
        )
        result = gate.check_ai_flavor(report)
        assert result.details["hit_count"] == 2

    def test_details_contains_pattern_breakdown(self) -> None:
        gate = QualityGate()
        report = _MockHumanizeReport(
            pattern_hits=[
                _MockHit("weak_verb_stacking", "high", 0.9),
                _MockHit("tautology_marker", "high", 0.9),
                _MockHit("weak_verb_stacking", "medium", 0.7),
            ],
            total_hits=3,
        )
        result = gate.check_ai_flavor(report)
        breakdown = result.details["by_pattern_id"]
        assert breakdown["weak_verb_stacking"] == 2
        assert breakdown["tautology_marker"] == 1

    def test_details_records_has_critical(self) -> None:
        gate = QualityGate()
        report = _MockHumanizeReport(
            pattern_hits=[_MockHit("x", "critical", 1.0)],
            total_hits=1,
        )
        result = gate.check_ai_flavor(report)
        assert result.details["has_critical"] is True
        assert result.details["hard_fail"] is True

    def test_details_records_no_critical(self) -> None:
        gate = QualityGate()
        report = _MockHumanizeReport(
            pattern_hits=[_MockHit("x", "high", 1.0)],
            total_hits=1,
        )
        result = gate.check_ai_flavor(report)
        assert result.details["has_critical"] is False
        assert result.details["hard_fail"] is False


# ---------------------------------------------------------------------------
# Result is recorded into the gate's check history
# ---------------------------------------------------------------------------


class TestAIFlavorRecordsToGate:
    def test_result_appended_to_checks_list(self) -> None:
        gate = QualityGate()
        report = _MockHumanizeReport(pattern_hits=[], total_hits=0)
        result = gate.check_ai_flavor(report)
        assert len(gate._checks) == 1
        assert gate._checks[0] is result

    def test_multiple_calls_accumulate(self) -> None:
        gate = QualityGate()
        report = _MockHumanizeReport(pattern_hits=[], total_hits=0)
        gate.check_ai_flavor(report)
        gate.check_ai_flavor(report)
        assert len(gate._checks) == 2

    def test_critical_ai_flavor_makes_aggregate_report_fail(self) -> None:
        gate = QualityGate()
        report = _MockHumanizeReport(
            pattern_hits=[_MockHit("collaborative_artifact", "critical", 1.0)],
            total_hits=1,
        )
        gate.check_ai_flavor(report)

        aggregate = gate.report()

        assert aggregate.verdict == QualityVerdict.FAIL


# ---------------------------------------------------------------------------
# Defaults match the rubric version bump target
# ---------------------------------------------------------------------------


class TestAIFlavorDefaultThreshold:
    def test_default_threshold_is_eight(self) -> None:
        """Plan §2.2.4 sets the rubric threshold at 8.0; QualityGate must match."""
        gate = QualityGate()
        report = _MockHumanizeReport(
            pattern_hits=[_MockHit("weak_verb_stacking", "high", 1.0)],
            total_hits=1,
        )
        result = gate.check_ai_flavor(report)
        assert result.threshold == 8.0
