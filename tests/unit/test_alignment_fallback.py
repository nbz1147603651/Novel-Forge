"""Tests for alignment evaluation fallback / timeout exemption.

When the alignment check times out or returns a degraded (truncated/partial)
response, the resulting AlignmentReport is marked with ``is_fallback=True`` so
downstream gates and threshold checks exempt it from hard-blocking decisions.
This mirrors the existing ``ReadingPowerReport.is_fallback`` pattern.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.core.exceptions import ConsistencyViolationError
from novel_forge.core.schemas.chapter import AlignmentReport
from novel_forge.pipeline.long import chapter_flow
from novel_forge.pipeline.quality_gate import QualityGate, QualityVerdict

# ──────────────────────────────────────────────────────────────────────────
# Schema defaults
# ──────────────────────────────────────────────────────────────────────────


class TestAlignmentReportFallbackFields:
    def test_default_fields(self):
        report = AlignmentReport()
        assert report.evaluation_status == "ok"
        assert report.is_fallback is False
        assert report.fallback_reason == ""

    def test_fallback_report_construction(self):
        report = AlignmentReport(
            alignment_score=0.0,
            evaluation_status="timeout",
            is_fallback=True,
            fallback_reason="critic_soft_timeout",
        )
        assert report.is_fallback is True
        assert report.evaluation_status == "timeout"
        assert report.fallback_reason == "critic_soft_timeout"


# ──────────────────────────────────────────────────────────────────────────
# Threshold enforcement exemption
# ──────────────────────────────────────────────────────────────────────────


class TestThresholdEnforcementExemption:
    def _make_runner(self, events):
        return SimpleNamespace(
            _config=SimpleNamespace(alignment_threshold=7.0),
            _on_step=lambda step, data: events.append((step, data)),
        )

    def test_fallback_report_does_not_raise(self):
        """A fallback AlignmentReport should NOT trigger ConsistencyViolationError."""
        events: list[tuple[str, object]] = []
        runner = self._make_runner(events)
        fallback_report = AlignmentReport(
            alignment_score=0.0,
            is_fallback=True,
            evaluation_status="timeout",
            fallback_reason="critic_soft_timeout",
            missing_main_points=["主线点缺失"],
        )

        # Should not raise despite score=0.0 and missing_main_points
        chapter_flow._enforce_alignment_threshold(
            runner=runner,
            chapter_number=1,
            alignment_score=fallback_report.alignment_score,
            repair_actions=fallback_report.repair_actions,
            alignment_report=fallback_report,
        )

        event_names = [step for step, _ in events]
        assert "alignment_fallback_exempt" in event_names

    def test_real_low_score_still_raises(self):
        """A non-fallback low score should still raise ConsistencyViolationError."""
        events: list[tuple[str, object]] = []
        runner = self._make_runner(events)
        real_report = AlignmentReport(
            alignment_score=3.0,
            is_fallback=False,
            missing_main_points=["主线点缺失"],
        )

        with pytest.raises(ConsistencyViolationError):
            chapter_flow._enforce_alignment_threshold(
                runner=runner,
                chapter_number=1,
                alignment_score=real_report.alignment_score,
                repair_actions=real_report.repair_actions,
                alignment_report=real_report,
            )

    def test_fallback_exempt_on_retry_path(self):
        """is_retry=True with fallback should also exempt (not even reach moderate_floor)."""
        events: list[tuple[str, object]] = []
        runner = self._make_runner(events)
        fallback_report = AlignmentReport(
            alignment_score=0.0,
            is_fallback=True,
            evaluation_status="degraded",
            fallback_reason="model_score_near_zero_with_misses",
        )

        chapter_flow._enforce_alignment_threshold(
            runner=runner,
            chapter_number=1,
            alignment_score=fallback_report.alignment_score,
            repair_actions=fallback_report.repair_actions,
            alignment_report=fallback_report,
            is_retry=True,
        )

        event_names = [step for step, _ in events]
        assert "alignment_fallback_exempt" in event_names


# ──────────────────────────────────────────────────────────────────────────
# Quality gate exemption
# ──────────────────────────────────────────────────────────────────────────


class TestQualityGateExemption:
    def test_fallback_alignment_passes_gate(self):
        """A fallback AlignmentReport should pass the quality gate."""
        gate = QualityGate()
        fallback_report = AlignmentReport(
            alignment_score=0.0,
            is_fallback=True,
            evaluation_status="timeout",
            fallback_reason="critic_soft_timeout",
            missing_main_points=["主线点1缺失", "主线点2缺失"],
        )
        result = gate.check_alignment(fallback_report)
        assert result.passed is True
        assert result.details.get("is_fallback") is True

    def test_real_low_score_fails_gate(self):
        """A non-fallback low score should fail the quality gate."""
        gate = QualityGate()
        real_report = AlignmentReport(
            alignment_score=3.0,
            is_fallback=False,
            missing_main_points=["主线点缺失"],
        )
        result = gate.check_alignment(real_report)
        assert not result.passed

    def test_fallback_alignment_does_not_block_overall_verdict(self):
        """Fallback alignment should not cause overall gate verdict to be FAIL."""
        gate = QualityGate()
        gate.check_eval(SimpleNamespace(overall_score=8.0))
        gate.check_alignment(
            AlignmentReport(
                alignment_score=0.0,
                is_fallback=True,
                evaluation_status="timeout",
                fallback_reason="critic_soft_timeout",
            )
        )
        report = gate.report()
        assert report.verdict != QualityVerdict.FAIL
        assert report.passed is True


# ──────────────────────────────────────────────────────────────────────────
# CriticAgent timeout propagation
# ──────────────────────────────────────────────────────────────────────────


class TestCriticTimeoutPropagation:
    def test_alignment_timed_out_property(self):
        """CritiqueReport.alignment_timed_out should detect timeout in metadata."""
        from novel_forge.memory.critic import CritiqueReport

        report = CritiqueReport(
            chapter_number=1,
            metadata={
                "execution": {
                    "checks_timed_out": ["alignment"],
                }
            },
        )
        assert report.alignment_timed_out is True

    def test_alignment_timed_out_false_when_no_timeout(self):
        from novel_forge.memory.critic import CritiqueReport

        report = CritiqueReport(
            chapter_number=1,
            metadata={"execution": {"checks_timed_out": []}},
        )
        assert report.alignment_timed_out is False

    def test_to_alignment_report_marks_timeout(self):
        """to_alignment_report should propagate timeout flag."""
        from novel_forge.memory.critic import AlignmentResult, CritiqueReport

        report = CritiqueReport(
            chapter_number=1,
            alignment_result=AlignmentResult(alignment_score=8.0),
            metadata={
                "execution": {
                    "checks_timed_out": ["alignment"],
                }
            },
        )
        alignment_report = report.to_alignment_report()
        assert alignment_report is not None
        assert alignment_report.is_fallback is True
        assert alignment_report.evaluation_status == "timeout"
        assert alignment_report.fallback_reason == "critic_soft_timeout"

    def test_to_alignment_report_marks_degraded(self):
        """to_alignment_report should propagate degraded flag from AlignmentResult."""
        from novel_forge.memory.critic import AlignmentResult, CritiqueReport

        report = CritiqueReport(
            chapter_number=1,
            alignment_result=AlignmentResult(
                alignment_score=1.8,
                is_degraded=True,
                degraded_reason="model_score_near_zero_with_misses",
            ),
            metadata={"execution": {"checks_timed_out": []}},
        )
        alignment_report = report.to_alignment_report()
        assert alignment_report is not None
        assert alignment_report.is_fallback is True
        assert alignment_report.evaluation_status == "degraded"
        assert alignment_report.fallback_reason == "model_score_near_zero_with_misses"

    def test_to_alignment_report_clean_when_no_issues(self):
        """to_alignment_report should not mark fallback when evaluation is clean."""
        from novel_forge.memory.critic import AlignmentResult, CritiqueReport

        report = CritiqueReport(
            chapter_number=1,
            alignment_result=AlignmentResult(alignment_score=8.5),
            metadata={"execution": {"checks_timed_out": []}},
        )
        alignment_report = report.to_alignment_report()
        assert alignment_report is not None
        assert alignment_report.is_fallback is False
        assert alignment_report.evaluation_status == "ok"


# ──────────────────────────────────────────────────────────────────────────
# AlignmentStep degraded-response detection
# ──────────────────────────────────────────────────────────────────────────


class TestAlignmentStepDegradedDetection:
    def test_degraded_detection_when_model_score_near_zero_with_misses(self):
        """_normalize_alignment_payload should expose _model_score for degraded check."""
        from novel_forge.pipeline.steps.alignment_step import AlignmentStep

        raw = {
            "alignment_score": 0,
            "risk_level": "high",
            "missing_main_points": ["主线点1缺失", "主线点2缺失", "主线点3缺失"],
            "weak_subplot_points": [],
            "repair_actions": [],
        }
        normalized = AlignmentStep._normalize_alignment_payload(raw)
        # The internal _model_score should be 0.0 (from the raw "alignment_score": 0)
        assert normalized["_model_score"] == 0.0

    def test_no_degraded_when_model_score_missing(self):
        """When alignment_score is absent, default=8.5, should NOT be degraded."""
        from novel_forge.pipeline.steps.alignment_step import AlignmentStep

        raw = {
            "missing_main_points": ["主线点缺失"],
        }
        normalized = AlignmentStep._normalize_alignment_payload(raw)
        # Default model_score is 8.5, not near zero
        assert normalized["_model_score"] == 8.5

    def test_no_degraded_when_model_score_realistic(self):
        """A realistic low model_score (e.g. 3.0) should NOT be degraded."""
        from novel_forge.pipeline.steps.alignment_step import AlignmentStep

        raw = {
            "alignment_score": 3.0,
            "missing_main_points": ["主线点缺失"],
        }
        normalized = AlignmentStep._normalize_alignment_payload(raw)
        assert normalized["_model_score"] == 3.0
        assert normalized["_model_score"] >= 1.0
