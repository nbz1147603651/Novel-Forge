"""TTS-specific spending tracker with monthly and per-book budget enforcement.

The LLM gateway has its own :class:`~novel_forge.gateway.pricing.SpendingTracker`;
TTS synthesis cost historically bypassed it entirely (see ``assemble_audio_step``
summing ``cost_usd`` only for logging/reporting). This module provides an
independent tracker that reuses :class:`~novel_forge.core.exceptions.BudgetExceededError`
so the existing API/Desktop exception handlers (which already special-case it)
work without modification.

Design notes
------------
- **Monthly** budget is global across all projects in a process (mirrors LLM
  ``SpendingTracker`` semantics).
- **Per-book** budget is keyed by ``voice_team_hash`` (the stable fingerprint of
  a voice team; see ``workspace/execution_tts._voice_team_content_hash``). This
  isolates spend per book/voice-team rather than per project directory, which is
  the unit users reason about when budgeting an audiobook.
- ``0.0`` limit means *not enforced* — consistent with LLM tracker.
- Thread-safe via ``threading.Lock``; safe to call from asyncio workers.
"""

from __future__ import annotations

import threading
from datetime import date
from typing import TYPE_CHECKING

from novel_forge.core.exceptions import BudgetExceededError

if TYPE_CHECKING:
    pass


class TTSSpendingTracker:
    """In-process TTS spending tracker with monthly + per-book budget gates.

    Unlike the LLM ``SpendingTracker``, this tracker does **not** auto-reset the
    per-book total — a book budget is consumed until the process restarts or
    :meth:`reset_book` is called explicitly (e.g. on book finalization). This
    matches user expectations: a book budget covers the whole book, not a
    calendar window.
    """

    def __init__(
        self,
        *,
        monthly_limit: float = 0.0,
        book_limit: float = 0.0,
    ) -> None:
        self._monthly_limit = monthly_limit
        self._book_limit = book_limit
        self._lock = threading.Lock()
        self._monthly_total: float = 0.0
        self._current_month: tuple[int, int] = (date.today().year, date.today().month)
        # Per voice_team_hash accumulated spend for the book dimension.
        self._book_totals: dict[str, float] = {}

    @property
    def monthly_limit(self) -> float:
        return self._monthly_limit

    @property
    def book_limit(self) -> float:
        return self._book_limit

    def monthly_total(self) -> float:
        """Current calendar-month spend (USD), after rollover reset."""
        with self._lock:
            self._maybe_reset_month()
            return self._monthly_total

    def book_total(self, voice_team_hash: str) -> float:
        """Current per-book spend (USD) for the given voice team."""
        with self._lock:
            return self._book_totals.get(voice_team_hash, 0.0)

    def _maybe_reset_month(self) -> None:
        """Reset monthly counter on month rollover (must hold lock)."""
        month_key = (date.today().year, date.today().month)
        if month_key != self._current_month:
            self._monthly_total = 0.0
            self._current_month = month_key

    def check_budget(self, *, voice_team_hash: str = "") -> None:
        """Raise :class:`BudgetExceededError` if monthly or book budget exceeded.

        Safe to call before each synthesis attempt. When ``voice_team_hash`` is
        empty the book-dimension check is skipped (only monthly applies).
        """
        with self._lock:
            self._maybe_reset_month()
            if self._monthly_limit > 0 and self._monthly_total >= self._monthly_limit:
                raise BudgetExceededError("tts_monthly", self._monthly_total, self._monthly_limit)
            if self._book_limit > 0 and voice_team_hash:
                book_total = self._book_totals.get(voice_team_hash, 0.0)
                if book_total >= self._book_limit:
                    raise BudgetExceededError("tts_book", book_total, self._book_limit)

    def record(
        self,
        cost_usd: float,
        *,
        voice_team_hash: str = "",
    ) -> None:
        """Record a completed synthesis cost against monthly + book totals.

        Negative or zero costs are ignored (cost_usd == 0 for cached/mock takes).
        """
        if cost_usd <= 0.0:
            return
        with self._lock:
            self._maybe_reset_month()
            self._monthly_total += cost_usd
            if voice_team_hash:
                self._book_totals[voice_team_hash] = (
                    self._book_totals.get(voice_team_hash, 0.0) + cost_usd
                )

    def reset_book(self, voice_team_hash: str) -> None:
        """Clear the per-book total for a voice team (e.g. on book finalization)."""
        with self._lock:
            self._book_totals.pop(voice_team_hash, None)


def build_tts_spending_tracker(
    *,
    monthly_limit: float,
    book_limit: float,
) -> TTSSpendingTracker | None:
    """Build a tracker only when at least one limit is configured.

    Returns ``None`` when both limits are 0.0 (disabled), so callers can skip
    the per-segment check/record calls entirely.
    """
    if monthly_limit <= 0.0 and book_limit <= 0.0:
        return None
    return TTSSpendingTracker(monthly_limit=monthly_limit, book_limit=book_limit)
