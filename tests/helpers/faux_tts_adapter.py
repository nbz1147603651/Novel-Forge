"""Scriptable multi-turn mock TTS adapter for deterministic test sequences.

``FauxTTSAdapter`` extends :class:`MockTTSAdapter` and replaces its random
``fail_rate`` knob with deterministic, per-channel scripted response sequences.
Each of ``synthesize`` / ``clone_voice`` / ``design_voice`` consumes an ordered
list of :class:`FauxTTSStep` responses, one per call, supporting canned
success responses, error injection, and optional delay. All received requests
are recorded for assertions.

This is the TTS counterpart of :mod:`tests.helpers.faux_adapter` and replaces
the closures/``AsyncMock(side_effect=...)`` patterns tests currently bolt onto
``MockTTSAdapter`` when they need deterministic error sequences (e.g. "fail on
attempt 1, succeed on attempt 2" for retry/failover coverage).

Channels that receive no scripted steps fall back to the inherited
``MockTTSAdapter`` happy-path behaviour, so a test can script only the channel
it cares about and leave the rest on the default success path.

Example::

    adapter = FauxTTSAdapter(
        synthesize_steps=[
            FauxTTSStep(error=RateLimitError("429")),
            FauxTTSStep(),  # success: inherited mock audio
        ],
    )
    # first synthesize() raises RateLimitError; second returns mock audio
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from novel_forge.tts.gateway.adapters.mock_adapter import MockTTSAdapter
from novel_forge.tts.schemas import (
    TTSProvider,
    TTSRequest,
    TTSResponse,
    VoiceCloneRequest,
    VoiceCloneResponse,
    VoiceDesignRequest,
    VoiceDesignResponse,
)


@dataclass
class FauxTTSStep:
    """One scripted response in a FauxTTSAdapter channel sequence.

    Exactly one of ``response`` / ``error`` should be set. If both are set the
    error takes precedence. When both are ``None`` the call succeeds via the
    inherited ``MockTTSAdapter`` happy path (so a bare ``FauxTTSStep()`` means
    "succeed with default mock audio"). ``delay`` simulates latency.
    """

    response: Any | None = None
    error: Exception | None = None
    delay: float = 0.0


class _ChannelSequence:
    """Consumes a per-channel list of :class:`FauxTTSStep` in call order."""

    def __init__(self, steps: list[FauxTTSStep] | None) -> None:
        self._steps: list[FauxTTSStep] = list(steps) if steps else []
        self._index = 0
        self.calls: list[Any] = []
        self.fallback_after_exhausted = False

    @property
    def scripted(self) -> bool:
        return bool(self._steps)

    def next_step(self, request: Any) -> FauxTTSStep | None:
        """Return the next scripted step, or ``None`` when unscripted/fallback.

        - When the channel was never given a script, returns ``None`` so the
          caller falls back to the inherited happy path.
        - While scripted steps remain, returns the next one.
        - When the scripted sequence is exhausted: returns ``None`` if
          :attr:`fallback_after_exhausted` is set (so later calls reuse the
          inherited happy path), otherwise raises ``RuntimeError`` (so tests
          catch under-mocked multi-call flows).
        """
        self.calls.append(request)
        if not self._steps:
            return None
        if self._index >= len(self._steps):
            if self.fallback_after_exhausted:
                return None
            raise RuntimeError(
                f"FauxTTSAdapter channel exhausted at call {self._index} "
                f"(scripted {len(self._steps)} step(s))"
            )
        step = self._steps[self._index]
        self._index += 1
        return step

    async def maybe_delay(self, step: FauxTTSStep | None) -> None:
        if step is not None and step.delay:
            await asyncio.sleep(step.delay)


class FauxTTSAdapter(MockTTSAdapter):
    """Scriptable multi-turn TTS mock with deterministic per-channel sequences.

    Parameters
    ----------
    synthesize_steps / clone_steps / design_steps:
        Ordered :class:`FauxTTSStep` lists consumed one-per-call by the
        matching method. Omit a channel to leave it on the inherited
        ``MockTTSAdapter`` happy path.
    latency_ms:
        Inherited simulated latency applied to unscripted (fallback) calls.
    name:
        Override for :attr:`provider_name` (default ``"faux-tts"``).
    fallback_after_exhausted:
        When ``True``, calls past the end of a scripted sequence fall back to
        the inherited ``MockTTSAdapter`` happy path instead of raising
        ``RuntimeError``. Use this for "fail N times, then succeed forever"
        patterns (e.g. one rate-limit then recovery). Default ``False`` keeps
        the strict "exhaustion is a bug" behaviour.

    Attributes
    ----------
    synthesize_calls / clone_calls / design_calls:
        Recorded request objects per channel, in arrival order.
    """

    def __init__(
        self,
        *,
        synthesize_steps: list[FauxTTSStep] | None = None,
        clone_steps: list[FauxTTSStep] | None = None,
        design_steps: list[FauxTTSStep] | None = None,
        latency_ms: float = 100.0,
        name: str = "faux-tts",
        fallback_after_exhausted: bool = False,
    ) -> None:
        # fail_rate is forced to 0: deterministic sequences replace randomness.
        super().__init__(latency_ms=latency_ms, fail_rate=0.0)
        self._synthesize_seq = _ChannelSequence(synthesize_steps)
        self._clone_seq = _ChannelSequence(clone_steps)
        self._design_seq = _ChannelSequence(design_steps)
        for seq in (self._synthesize_seq, self._clone_seq, self._design_seq):
            seq.fallback_after_exhausted = fallback_after_exhausted
        self._name = name

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def provider_type(self) -> TTSProvider:
        return TTSProvider.MOCK

    @property
    def synthesize_calls(self) -> list[TTSRequest]:
        return list(self._synthesize_seq.calls)

    @property
    def clone_calls(self) -> list[VoiceCloneRequest]:
        return list(self._clone_seq.calls)

    @property
    def design_calls(self) -> list[VoiceDesignRequest]:
        return list(self._design_seq.calls)

    async def synthesize(self, request: TTSRequest) -> TTSResponse:
        step = self._synthesize_seq.next_step(request)
        await self._synthesize_seq.maybe_delay(step)
        if step is not None:
            if step.error is not None:
                raise step.error
            if step.response is not None:
                return step.response  # type: ignore[no-any-return]
        # No script, or a bare FauxTTSStep(): fall back to inherited happy path.
        return await super().synthesize(request)

    async def clone_voice(self, request: VoiceCloneRequest) -> VoiceCloneResponse:
        step = self._clone_seq.next_step(request)
        await self._clone_seq.maybe_delay(step)
        if step is not None:
            if step.error is not None:
                raise step.error
            if step.response is not None:
                return step.response  # type: ignore[no-any-return]
        return await super().clone_voice(request)

    async def design_voice(self, request: VoiceDesignRequest) -> VoiceDesignResponse:
        step = self._design_seq.next_step(request)
        await self._design_seq.maybe_delay(step)
        if step is not None:
            if step.error is not None:
                raise step.error
            if step.response is not None:
                return step.response  # type: ignore[no-any-return]
        return await super().design_voice(request)
