"""Wave-based synthesis pool for MiniMax TTS.

Mirrors the reference Phase-4 generation model
(``Reference/audiobook/phases/04-generation.md`` + ``build_generation_plan.py``):

- at most ``wave_size`` concurrent calls per wave (reference: 3)
- the next wave starts only after the current wave fully completes — no
  sleep between successful waves
- a failed call is recorded, never retried inline (inline retry risks
  using wrong parameters from LLM context)
- after all waves, failures are retried in unified rounds (also wave
  based) with an optional settle delay between rounds (reference: sleep
  20-30s before the retry round)

The pool is provider-agnostic: any ``call`` coroutine may be pooled, and an
optional ``is_failure`` predicate lets callers treat application-level
failure results (e.g. a FAILED synthesis result) as retryable.

Author: novel-forge
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Generic, TypeVar

_log = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")

# Reference constants: MAX_CALLS_PER_WAVE = 3, retry sleep 20-30s.
WAVE_SIZE = 3
MAX_RETRY_ROUNDS = 2
RETRY_DELAY_S = 20.0


class WaveCallFailedError(RuntimeError):
    """Marker error: the pooled call returned an application-level failure.

    Raised internally when ``is_failure`` rejects a result; the original
    result stays available on the :class:`WaveOutcome` for callers.
    """


@dataclass
class WaveOutcome(Generic[T, R]):
    """Final result of one pooled item across all waves and retry rounds."""

    item: T
    result: R | None = None
    """The latest successful result; ``None`` when the item never succeeded."""
    error: Exception | None = None
    """The final error; ``None`` when the item eventually succeeded."""
    attempts: int = 0
    """Total call attempts across all rounds (retry rounds included)."""
    failures: list[Exception] = field(default_factory=list)
    """Every failure observed for this item, in chronological order."""


class MiniMaxWavePool(Generic[T, R]):
    """Wave-based scheduler for remote TTS generation calls.

    Parameters mirror the reference model and are intentionally configurable
    so tests can shrink the settle delay to near zero.
    """

    def __init__(
        self,
        *,
        wave_size: int = WAVE_SIZE,
        max_retry_rounds: int = MAX_RETRY_ROUNDS,
        retry_delay_s: float = RETRY_DELAY_S,
        retry_delay_fn: Callable[[int, list[WaveOutcome[T, R]]], float] | None = None,
    ) -> None:
        if wave_size < 1:
            raise ValueError("wave_size must be at least 1")
        if max_retry_rounds < 0:
            raise ValueError("max_retry_rounds must be >= 0")
        if retry_delay_s < 0:
            raise ValueError("retry_delay_s must be >= 0")
        self._wave_size = wave_size
        self._max_retry_rounds = max_retry_rounds
        self._retry_delay_s = retry_delay_s
        self._retry_delay_fn = retry_delay_fn

    @property
    def wave_size(self) -> int:
        return self._wave_size

    def _retry_delay_for_round(self, round_no: int, outcomes: list[WaveOutcome[T, R]]) -> float:
        """Resolve the settle delay for one retry round.

        Uses the injected adaptive function when provided (e.g. exponential
        backoff keyed on rate-limit evidence); otherwise keeps the configured
        fixed delay so existing behavior and tests stay unchanged.
        """
        if self._retry_delay_fn is not None:
            try:
                return max(0.0, float(self._retry_delay_fn(round_no, outcomes)))
            except (TypeError, ValueError):
                pass
        return self._retry_delay_s

    async def run(
        self,
        items: Sequence[T],
        call: Callable[[T], Awaitable[R]],
        *,
        is_failure: Callable[[R], bool] | None = None,
        on_wave_done: Callable[[int, int], None] | None = None,
        before_wave: Callable[[int, int], Awaitable[None] | None] | None = None,
    ) -> list[WaveOutcome[T, R]]:
        """Execute ``call`` for every item in strict waves.

        - Wave 0 covers all items, ``wave_size`` calls at a time.
        - Failed items are recorded and never retried inline.
        - After wave 0 (and after each retry round), failed items are
          retried in fresh wave rounds, up to ``max_retry_rounds``, with an
          optional settle delay before each retry round.

        ``on_wave_done(round_no, remaining)`` is invoked after every wave
        completes; ``remaining`` is the number of items still pending.
        ``before_wave(round_no, item_count)`` runs immediately before a wave
        starts and may await a provider rate-budget reservation.
        """
        outcomes: list[WaveOutcome[T, R]] = [WaveOutcome(item=item) for item in items]
        pending: list[tuple[int, T]] = list(enumerate(items))
        round_no = 0
        while pending and round_no <= self._max_retry_rounds:
            if round_no > 0:
                _log.info(
                    "Wave pool retry round %d: %d failed items pending",
                    round_no,
                    len(pending),
                )
                # The adaptive callback must only see calls that remain
                # pending.  Passing successful outcomes from an earlier wave
                # makes a recovered rate-limit error unnecessarily slow down
                # unrelated retries in later rounds.
                delay = self._retry_delay_for_round(
                    round_no,
                    [outcome for outcome in outcomes if outcome.error is not None],
                )
                if delay > 0:
                    await asyncio.sleep(delay)

            for wave_start in range(0, len(pending), self._wave_size):
                wave = pending[wave_start : wave_start + self._wave_size]
                if before_wave is not None:
                    before_result = before_wave(round_no, len(wave))
                    if before_result is not None:
                        await before_result
                wave_results = await asyncio.gather(
                    *(call(item) for _, item in wave),
                    return_exceptions=True,
                )
                for (index, _item), raw in zip(wave, wave_results, strict=True):
                    outcome = outcomes[index]
                    outcome.attempts += 1
                    if isinstance(raw, BaseException):
                        if not isinstance(raw, Exception):
                            # KeyboardInterrupt/SystemExit cannot be stored in
                            # the outcome (typed as Exception); wrap them so
                            # the failure is recorded, not treated as success.
                            wrapped = RuntimeError(f"call raised {type(raw).__name__}: {raw!r}")
                            outcome.failures.append(wrapped)
                            outcome.error = wrapped
                        else:
                            outcome.failures.append(raw)
                            outcome.error = raw
                        outcome.result = None
                    elif is_failure is not None and is_failure(raw):
                        outcome.failures.append(
                            WaveCallFailedError(f"call returned failure: {raw!r}")
                        )
                        outcome.error = outcome.failures[-1]
                        outcome.result = raw
                    else:
                        outcome.result = raw
                        outcome.error = None
                if on_wave_done is not None:
                    # Items not yet started in this round after this wave.
                    remaining_in_round = len(pending) - (wave_start + len(wave))
                    on_wave_done(round_no, remaining_in_round)

            pending = [
                (index, item) for index, item in pending if outcomes[index].error is not None
            ]
            round_no += 1

        failed = [outcome for outcome in outcomes if outcome.error is not None]
        if failed:
            _log.warning(
                "Wave pool finished with %d/%d items failed after %d retry rounds",
                len(failed),
                len(outcomes),
                round_no - 1,
            )
        return outcomes
