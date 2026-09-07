"""Unit tests for the TTS spending tracker (monthly + per-book budget)."""

from __future__ import annotations

import pytest

from novel_forge.core.exceptions import BudgetExceededError
from novel_forge.tts.runtime.budget import TTSSpendingTracker, build_tts_spending_tracker


def test_tracker_returns_none_when_both_limits_disabled() -> None:
    assert build_tts_spending_tracker(monthly_limit=0.0, book_limit=0.0) is None


def test_tracker_builds_when_either_limit_set() -> None:
    assert build_tts_spending_tracker(monthly_limit=5.0, book_limit=0.0) is not None
    assert build_tts_spending_tracker(monthly_limit=0.0, book_limit=5.0) is not None


def test_check_budget_no_raise_when_under_limit() -> None:
    tracker = TTSSpendingTracker(monthly_limit=10.0, book_limit=5.0)
    tracker.record(2.0, voice_team_hash="team-a")
    # Should not raise: 2.0 < 5.0 (book), 2.0 < 10.0 (monthly)
    tracker.check_budget(voice_team_hash="team-a")


def test_check_budget_raises_when_book_limit_exceeded() -> None:
    tracker = TTSSpendingTracker(monthly_limit=100.0, book_limit=5.0)
    tracker.record(3.0, voice_team_hash="team-a")
    tracker.record(3.0, voice_team_hash="team-a")  # book total now 6.0 > 5.0
    with pytest.raises(BudgetExceededError) as exc_info:
        tracker.check_budget(voice_team_hash="team-a")
    assert exc_info.value.period == "tts_book"
    assert exc_info.value.limit == 5.0


def test_check_budget_raises_when_monthly_limit_exceeded() -> None:
    tracker = TTSSpendingTracker(monthly_limit=10.0, book_limit=100.0)
    tracker.record(6.0, voice_team_hash="team-a")
    tracker.record(6.0, voice_team_hash="team-b")  # monthly total 12.0 > 10.0
    with pytest.raises(BudgetExceededError) as exc_info:
        tracker.check_budget(voice_team_hash="team-b")
    assert exc_info.value.period == "tts_monthly"
    assert exc_info.value.limit == 10.0


def test_book_totals_isolated_per_voice_team_hash() -> None:
    tracker = TTSSpendingTracker(monthly_limit=1000.0, book_limit=5.0)
    tracker.record(4.0, voice_team_hash="team-a")
    tracker.record(4.0, voice_team_hash="team-b")
    # team-a at 4.0 (under 5.0), team-b at 4.0 (under 5.0) - neither trips
    tracker.check_budget(voice_team_hash="team-a")
    tracker.check_budget(voice_team_hash="team-b")
    assert tracker.book_total("team-a") == 4.0
    assert tracker.book_total("team-b") == 4.0


def test_monthly_check_skips_book_dimension_when_hash_empty() -> None:
    tracker = TTSSpendingTracker(monthly_limit=100.0, book_limit=1.0)
    tracker.record(0.5, voice_team_hash="team-a")  # book at 0.5, under 1.0
    # Empty hash: book dimension skipped, only monthly checked
    tracker.check_budget(voice_team_hash="")


def test_zero_cost_recorded_is_noop() -> None:
    tracker = TTSSpendingTracker(monthly_limit=10.0, book_limit=5.0)
    tracker.record(0.0, voice_team_hash="team-a")  # cached/mock take
    assert tracker.monthly_total() == 0.0
    assert tracker.book_total("team-a") == 0.0


def test_reset_book_clears_per_book_total() -> None:
    tracker = TTSSpendingTracker(monthly_limit=100.0, book_limit=5.0)
    tracker.record(4.0, voice_team_hash="team-a")
    tracker.reset_book("team-a")
    assert tracker.book_total("team-a") == 0.0
    # Monthly total is NOT reset by reset_book (calendar-window scope)
    assert tracker.monthly_total() == 4.0


def test_book_budget_blocks_further_synthesis_after_trip() -> None:
    """Simulate the synthesis gate: after the limit trips, subsequent checks raise."""
    tracker = TTSSpendingTracker(monthly_limit=1000.0, book_limit=5.0)
    # First take: 3.0, under limit - allowed
    tracker.check_budget(voice_team_hash="team-a")
    tracker.record(3.0, voice_team_hash="team-a")
    # Second take: 3.0, total 6.0 > 5.0 - allowed at check time (5.0 was the
    # pre-record gate), records to 6.0
    tracker.check_budget(voice_team_hash="team-a")
    tracker.record(3.0, voice_team_hash="team-a")
    # Third attempt: now book total 6.0 >= 5.0, gate trips
    with pytest.raises(BudgetExceededError):
        tracker.check_budget(voice_team_hash="team-a")
