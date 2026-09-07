"""Unit tests for QualityGate.check_backstory_reveals().

Added in M4 — see docs/ai_flavor_quality.md.

Per-reveal lifecycle:
- pending:   deadline reached but no expansion yet
- satisfied: min_word_count of expansion present in chapter(s) up to and
             including the current chapter
- overdue:   deadline + 2 chapters passed and still under min_word_count
- not_yet:   current chapter < deadline (silently passes)

Score formula:
    score = 10 * (satisfied / total_when_due)   # only count reveals whose
                                                 # deadline has been reached
    passed = (overdue == 0)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from novel_forge.core.schemas.spec import BackstoryRevealSpec
from novel_forge.pipeline.quality_gate import (
    QualityGate,
)

# ---------------------------------------------------------------------------
# Minimal mocks
# ---------------------------------------------------------------------------


@dataclass
class _MockBackstoryReport:
    """Object exposing per-reveal cumulative word counts up to chapter_number.

    Shape:
    {
        topic: { "current_word_count": int, "first_seen_chapter": int | None }
    }
    """

    cumulative: dict[str, dict[str, Any]]


# ---------------------------------------------------------------------------
# Empty / no constraints
# ---------------------------------------------------------------------------


class TestNoConstraints:
    def test_empty_list_passes_immediately(self) -> None:
        gate = QualityGate()
        result = gate.check_backstory_reveals(
            backstory_reveals=[],
            chapter_number=10,
            backstory_report=None,
        )
        assert result.passed is True
        assert result.score == 10.0
        assert result.dimension == "backstory_reveal"

    def test_none_reveals_passes(self) -> None:
        gate = QualityGate()
        result = gate.check_backstory_reveals(
            backstory_reveals=None,
            chapter_number=1,
            backstory_report=None,
        )
        assert result.passed is True


# ---------------------------------------------------------------------------
# Not yet due
# ---------------------------------------------------------------------------


class TestNotYetDue:
    def test_reveal_before_deadline_is_silent(self) -> None:
        """A reveal whose deadline is chapter 10 must not appear in any output
        when called at chapter 5 — it has not yet 'come due'."""
        gate = QualityGate()
        reveals = [
            BackstoryRevealSpec(topic="x", required_first_appearance=10),
        ]
        report = _MockBackstoryReport(cumulative={})  # no expansion yet
        result = gate.check_backstory_reveals(
            backstory_reveals=reveals,
            chapter_number=5,
            backstory_report=report,
        )
        # No overdue, no failed — passes.
        assert result.passed is True
        assert "x" not in result.details.get("overdue", [])
        assert "x" not in result.details.get("pending", [])


# ---------------------------------------------------------------------------
# Pending state
# ---------------------------------------------------------------------------


class TestPending:
    def test_due_reveal_with_zero_expansion_is_pending(self) -> None:
        gate = QualityGate()
        reveals = [
            BackstoryRevealSpec(topic="x", required_first_appearance=5),
        ]
        report = _MockBackstoryReport(cumulative={"x": {"current_word_count": 0}})
        result = gate.check_backstory_reveals(
            backstory_reveals=reveals,
            chapter_number=5,  # exactly deadline
            backstory_report=report,
        )
        assert "x" in result.details["pending"]
        assert result.passed is True  # pending is not yet a failure

    def test_due_reveal_with_partial_expansion_is_pending(self) -> None:
        gate = QualityGate()
        reveals = [
            BackstoryRevealSpec(
                topic="x", required_first_appearance=5, min_word_count=300
            ),
        ]
        report = _MockBackstoryReport(cumulative={"x": {"current_word_count": 150}})
        result = gate.check_backstory_reveals(
            backstory_reveals=reveals,
            chapter_number=5,
            backstory_report=report,
        )
        assert "x" in result.details["pending"]


# ---------------------------------------------------------------------------
# Satisfied state
# ---------------------------------------------------------------------------


class TestSatisfied:
    def test_due_reveal_with_full_expansion_satisfies(self) -> None:
        gate = QualityGate()
        reveals = [
            BackstoryRevealSpec(
                topic="x", required_first_appearance=5, min_word_count=200
            ),
        ]
        report = _MockBackstoryReport(cumulative={"x": {"current_word_count": 250}})
        result = gate.check_backstory_reveals(
            backstory_reveals=reveals,
            chapter_number=5,
            backstory_report=report,
        )
        assert "x" in result.details["satisfied"]
        assert result.passed is True

    def test_exact_word_count_boundary_satisfies(self) -> None:
        """min_word_count is met exactly → satisfied."""
        gate = QualityGate()
        reveals = [
            BackstoryRevealSpec(
                topic="x", required_first_appearance=5, min_word_count=200
            ),
        ]
        report = _MockBackstoryReport(cumulative={"x": {"current_word_count": 200}})
        result = gate.check_backstory_reveals(
            backstory_reveals=reveals,
            chapter_number=5,
            backstory_report=report,
        )
        assert "x" in result.details["satisfied"]


# ---------------------------------------------------------------------------
# Overdue state — the failure mode
# ---------------------------------------------------------------------------


class TestOverdue:
    def test_reveal_past_deadline_plus_two_fails(self) -> None:
        """If we're at chapter 8+ and the reveal's deadline was 5 with no
        expansion, it's overdue — gate fails."""
        gate = QualityGate()
        reveals = [
            BackstoryRevealSpec(topic="x", required_first_appearance=5),
        ]
        report = _MockBackstoryReport(cumulative={"x": {"current_word_count": 0}})
        result = gate.check_backstory_reveals(
            backstory_reveals=reveals,
            chapter_number=8,  # 5 + 3
            backstory_report=report,
        )
        assert "x" in result.details["overdue"]
        assert result.passed is False

    def test_at_deadline_plus_two_just_becomes_overdue(self) -> None:
        """The boundary: deadline+2 is the cut-off."""
        gate = QualityGate()
        reveals = [
            BackstoryRevealSpec(topic="x", required_first_appearance=5),
        ]
        report = _MockBackstoryReport(cumulative={"x": {"current_word_count": 0}})
        # At chapter 7 (deadline 5 + 2), still pending.
        result_pending = gate.check_backstory_reveals(
            backstory_reveals=reveals,
            chapter_number=7,
            backstory_report=report,
        )
        assert "x" in result_pending.details["pending"]
        assert result_pending.passed is True

        gate.reset()
        # At chapter 8 (deadline 5 + 3), overdue.
        result_overdue = gate.check_backstory_reveals(
            backstory_reveals=reveals,
            chapter_number=8,
            backstory_report=report,
        )
        assert "x" in result_overdue.details["overdue"]
        assert result_overdue.passed is False


# ---------------------------------------------------------------------------
# Mixed scenarios
# ---------------------------------------------------------------------------


class TestMixedScenarios:
    def test_multiple_reveals_with_mixed_states(self) -> None:
        gate = QualityGate()
        reveals = [
            BackstoryRevealSpec(
                topic="satisfied_one",
                required_first_appearance=5,
                min_word_count=200,
            ),
            # Deadline 10, current chapter 11 → within grace window → pending
            BackstoryRevealSpec(
                topic="pending_one",
                required_first_appearance=10,
            ),
            # Deadline 3, current chapter 11 → past grace window → overdue
            BackstoryRevealSpec(
                topic="overdue_one",
                required_first_appearance=3,
            ),
            BackstoryRevealSpec(
                topic="future_one",
                required_first_appearance=20,
            ),
        ]
        report = _MockBackstoryReport(cumulative={
            "satisfied_one": {"current_word_count": 300},
            "pending_one": {"current_word_count": 0},
            "overdue_one": {"current_word_count": 0},
            "future_one": {"current_word_count": 0},
        })
        result = gate.check_backstory_reveals(
            backstory_reveals=reveals,
            chapter_number=11,
            backstory_report=report,
        )
        assert "satisfied_one" in result.details["satisfied"]
        assert "pending_one" in result.details["pending"]
        assert "overdue_one" in result.details["overdue"]
        assert "future_one" not in result.details["satisfied"]
        assert "future_one" not in result.details["pending"]
        assert "future_one" not in result.details["overdue"]
        # Overdue fails the gate.
        assert result.passed is False


# ---------------------------------------------------------------------------
# Details / metadata
# ---------------------------------------------------------------------------


class TestDetailsMetadata:
    def test_details_records_threshold(self) -> None:
        gate = QualityGate()
        result = gate.check_backstory_reveals(
            backstory_reveals=[],
            chapter_number=1,
            backstory_report=None,
            threshold=7.5,
        )
        assert result.threshold == 7.5

    def test_score_is_proportional_to_satisfied(self) -> None:
        gate = QualityGate()
        reveals = [
            BackstoryRevealSpec(
                topic="satisfied", required_first_appearance=5, min_word_count=100
            ),
            BackstoryRevealSpec(
                topic="overdue", required_first_appearance=3
            ),
        ]
        report = _MockBackstoryReport(cumulative={
            "satisfied": {"current_word_count": 200},
            "overdue": {"current_word_count": 0},
        })
        result = gate.check_backstory_reveals(
            backstory_reveals=reveals,
            chapter_number=8,
            backstory_report=report,
        )
        # 1/2 due reveals satisfied → score 5.0
        assert result.score == 5.0
        assert result.passed is False


# ---------------------------------------------------------------------------
# Integration with real BackstoryRevealSpec from spec.py
# ---------------------------------------------------------------------------


class TestRealSpecRoundTrip:
    def test_accepts_spec_from_storyspec(self) -> None:
        from novel_forge.core.schemas.spec import StorySpec

        spec = StorySpec(
            theme="x",
            backstory_reveals=[
                # a: deadline 3, chapter 4 → satisfied if count >= min_word_count
                BackstoryRevealSpec(topic="a", required_first_appearance=3),
                # b: deadline 5, chapter 4 → not yet due (silent)
                BackstoryRevealSpec(topic="b", required_first_appearance=5),
            ],
        )
        gate = QualityGate()
        result = gate.check_backstory_reveals(
            backstory_reveals=spec.backstory_reveals,
            chapter_number=4,
            backstory_report=_MockBackstoryReport(cumulative={
                "a": {"current_word_count": 250},
                "b": {"current_word_count": 0},
            }),
        )
        assert "a" in result.details["satisfied"]
        # b is not yet due, so it appears in NEITHER pending nor satisfied.
        assert "b" not in result.details["satisfied"]
        assert "b" not in result.details["pending"]
        assert "b" not in result.details["overdue"]