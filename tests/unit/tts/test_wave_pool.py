"""P4: MiniMaxWavePool — wave-based synthesis scheduling tests.

Covers the reference Phase-4 generation model invariants:
- wave size limits in-flight concurrency
- waves are synchronous (next wave starts after the current one completes)
- failures are isolated and never retried inline
- failed items are retried in unified post-wave rounds
- retry rounds are capped and separated by a settle delay
- application-level failure results can opt into retry via ``is_failure``
- MINIMAX synthesis goes through the pool inside SynthesizeAudioStep
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import novel_forge.tts.pipeline.synthesize_audio_step as synthesize_audio_module
from novel_forge.core.config import Settings
from novel_forge.core.exceptions import AuthenticationError, RateLimitError
from novel_forge.tts.pipeline.minimax_wave_pool import (
    MAX_RETRY_ROUNDS,
    WAVE_SIZE,
    MiniMaxWavePool,
    WaveOutcome,
)
from novel_forge.tts.pipeline.synthesize_audio_step import (
    SynthesizeAudioInput,
    SynthesizeAudioStep,
)
from novel_forge.tts.schemas import (
    DubbingScript,
    DubbingSegment,
    SegmentType,
    SynthesisResult,
    SynthesisStatus,
    TTSProvider,
    VoiceTeamContract,
)
from tests.helpers.faux_tts_adapter import FauxTTSAdapter, FauxTTSStep


async def _ok(item: int) -> str:
    await asyncio.sleep(0.005)
    return f"ok-{item}"


async def _boom(item: int) -> str:
    await asyncio.sleep(0.005)
    if item == 0:  # deterministic failure slot
        raise RuntimeError(f"fail-{item}")
    return f"ok-{item}"


class TestMiniMaxWavePool:
    """Unit tests for the wave pool scheduler."""

    async def test_wave_size_limits_inflight_concurrency(self) -> None:
        active = 0
        peak = 0

        async def call(item: int) -> str:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return f"ok-{item}"

        pool = MiniMaxWavePool(wave_size=3, max_retry_rounds=0, retry_delay_s=0)
        outcomes = await pool.run(range(10), call)

        assert len(outcomes) == 10
        assert all(o.error is None for o in outcomes)
        assert peak <= 3

    async def test_waves_are_synchronous(self) -> None:
        # Record when each item's call starts; no two waves may overlap.
        starts: dict[int, float] = {}

        async def call(item: int) -> str:
            starts[item] = time.monotonic()
            await asyncio.sleep(0.02)
            return f"ok-{item}"

        pool = MiniMaxWavePool(wave_size=3, max_retry_rounds=0, retry_delay_s=0)
        await pool.run(range(6), call)

        # Wave 0: items 0-2, wave 1: items 3-5.  The last call of wave 0 must
        # finish before the first call of wave 1 starts (strictly sequential).
        wave0_end = max(starts[i] for i in range(3)) + 0.02
        wave1_start = min(starts[i] for i in range(3, 6))
        assert wave1_start >= wave0_end - 0.001

    async def test_failure_isolated_without_inline_retry(self) -> None:
        calls: dict[int, int] = {}

        async def call(item: int) -> str:
            calls[item] = calls.get(item, 0) + 1
            await asyncio.sleep(0.005)
            if item == 5:
                raise RuntimeError("boom")
            return f"ok-{item}"

        pool = MiniMaxWavePool(wave_size=3, max_retry_rounds=0, retry_delay_s=0)
        outcomes = await pool.run(range(6), call)

        # All items called exactly once (no inline retry on failure).
        assert calls == {i: 1 for i in range(6)}
        assert outcomes[5].error is not None
        assert outcomes[5].attempts == 1
        assert outcomes[0].result == "ok-0"
        assert outcomes[0].error is None

    async def test_failed_items_retried_after_all_waves(self) -> None:
        calls: dict[int, int] = {}
        # Item 2 fails on its first attempt, succeeds afterwards.
        first_attempt: dict[int, int] = {}

        async def call(item: int) -> str:
            calls[item] = calls.get(item, 0) + 1
            first_attempt[item] = first_attempt.get(item, 0) + 1
            await asyncio.sleep(0.005)
            if item == 2 and first_attempt[item] == 1:
                raise RuntimeError("transient")
            return f"ok-{item}"

        pool = MiniMaxWavePool(wave_size=3, max_retry_rounds=2, retry_delay_s=0)
        outcomes = await pool.run(range(6), call)

        # Successes ran once; the failed item ran a second time in the
        # post-wave retry round and recovered.
        assert calls[0] == 1
        assert calls[2] == 2
        assert outcomes[2].error is None
        assert outcomes[2].result == "ok-2"
        assert outcomes[2].attempts == 2

    async def test_retry_rounds_are_capped(self) -> None:
        pool = MiniMaxWavePool(wave_size=3, max_retry_rounds=2, retry_delay_s=0)
        outcomes = await pool.run(range(1), _boom)

        assert outcomes[0].error is not None
        # 1 initial wave attempt + 2 retry rounds.
        assert outcomes[0].attempts == 3
        assert len(outcomes[0].failures) == 3

    async def test_zero_retry_rounds_disables_retry(self) -> None:
        pool = MiniMaxWavePool(wave_size=3, max_retry_rounds=0, retry_delay_s=0)
        outcomes = await pool.run(range(1), _boom)

        assert outcomes[0].error is not None
        assert outcomes[0].attempts == 1

    async def test_retry_delay_separates_retry_rounds(self) -> None:
        calls: dict[int, int] = {}

        async def call(item: int) -> str:
            calls[item] = calls.get(item, 0) + 1
            if item == 0 and calls[item] == 1:
                raise RuntimeError("transient")
            return "ok"

        pool = MiniMaxWavePool(wave_size=3, max_retry_rounds=1, retry_delay_s=0.05)
        start = time.monotonic()
        outcomes = await pool.run(range(1), call)
        elapsed = time.monotonic() - start

        assert outcomes[0].error is None
        # The retry round slept at least the configured delay.
        assert elapsed >= 0.05

    async def test_adaptive_delay_only_receives_currently_failed_outcomes(self) -> None:
        observed_pending_counts: list[int] = []
        attempts: dict[int, int] = {}

        async def call(item: int) -> str:
            attempts[item] = attempts.get(item, 0) + 1
            if item == 0 and attempts[item] == 1:
                raise RuntimeError("transient")
            return "ok"

        pool = MiniMaxWavePool(
            wave_size=2,
            max_retry_rounds=1,
            retry_delay_s=0,
            retry_delay_fn=lambda _round, pending: (
                observed_pending_counts.append(len(pending)) or 0
            ),
        )
        outcomes = await pool.run([0, 1], call)

        assert observed_pending_counts == [1]
        assert all(outcome.error is None for outcome in outcomes)

    async def test_is_failure_predicate_opts_into_retry(self) -> None:
        @dataclass
        class AppResult:
            ok: bool

        calls: list[int] = []

        async def call(item: int) -> AppResult:
            calls.append(item)
            if item == 1:
                return AppResult(ok=False)  # application-level failure
            return AppResult(ok=True)

        pool = MiniMaxWavePool(wave_size=3, max_retry_rounds=1, retry_delay_s=0)
        outcomes = await pool.run(
            [0, 1],
            call,
            is_failure=lambda r: not r.ok,
        )

        assert calls == [0, 1, 1]  # item 1 retried once after the wave
        assert outcomes[1].error is not None
        assert outcomes[1].result == AppResult(ok=False)
        assert outcomes[1].attempts == 2

    async def test_successful_items_are_not_retried(self) -> None:
        calls: dict[int, int] = {}

        async def call(item: int) -> str:
            calls[item] = calls.get(item, 0) + 1
            if item == 0:
                raise RuntimeError("always-fails")
            return "ok"

        pool = MiniMaxWavePool(wave_size=3, max_retry_rounds=3, retry_delay_s=0)
        outcomes = await pool.run(range(4), call)

        assert calls[0] == 4  # 1 + 3 retry rounds, still failing
        assert calls[1] == 1
        assert calls[2] == 1
        assert calls[3] == 1
        assert outcomes[0].error is not None
        assert all(o.error is None for o in outcomes[1:])

    async def test_wave_done_callback_reports_progress(self) -> None:
        events: list[tuple[int, int]] = []

        def on_wave_done(round_no: int, remaining: int) -> None:
            events.append((round_no, remaining))

        pool = MiniMaxWavePool(wave_size=3, max_retry_rounds=0, retry_delay_s=0)
        await pool.run(range(7), _ok, on_wave_done=on_wave_done)

        # 7 items in waves of 3 → 3 waves, remaining 4/1/0.
        assert events == [(0, 4), (0, 1), (0, 0)]

    async def test_wave_outcome_keeps_failures_in_order(self) -> None:
        async def call(item: int) -> str:
            raise RuntimeError(f"fail-{item}")

        pool = MiniMaxWavePool(wave_size=3, max_retry_rounds=1, retry_delay_s=0)
        outcomes = await pool.run([0], call)

        assert [str(e) for e in outcomes[0].failures] == ["fail-0", "fail-0"]
        assert isinstance(outcomes[0], WaveOutcome)

    def test_invalid_parameters_rejected(self) -> None:
        with pytest.raises(ValueError):
            MiniMaxWavePool(wave_size=0)
        with pytest.raises(ValueError):
            MiniMaxWavePool(max_retry_rounds=-1)
        with pytest.raises(ValueError):
            MiniMaxWavePool(retry_delay_s=-1.0)

    def test_defaults_match_reference_model(self) -> None:
        pool = MiniMaxWavePool()
        assert pool.wave_size == WAVE_SIZE == 3
        assert pool._max_retry_rounds == MAX_RETRY_ROUNDS == 2


class TestWavePoolSynthesisIntegration:
    """MINIMAX synthesis must run through the wave pool in the pipeline."""

    async def test_minimax_failed_segment_retried_by_pool(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # First synthesize() call raises a retryable rate-limit error; every
        # later call falls back to the happy path (valid mock audio).
        adapter = FauxTTSAdapter(
            synthesize_steps=[FauxTTSStep(error=RateLimitError("429"))],
            latency_ms=0,
            fallback_after_exhausted=True,
        )

        class Registry:
            def get_adapter(self, provider: TTSProvider) -> FauxTTSAdapter:
                assert provider == TTSProvider.MINIMAX
                return adapter

        class NoopPacer:
            async def acquire(self) -> None:
                return None

            async def defer(self, cooldown_s: float) -> None:
                return None

        monkeypatch.setattr(
            synthesize_audio_module,
            "SynthesisRequestPacer",
            lambda _rpm: NoopPacer(),
        )
        settings = Settings(
            tts_wave_pool_enabled=True,
            tts_wave_pool_size=3,
            tts_wave_pool_retry_rounds=2,
            tts_wave_retry_delay_seconds=0.0,
        )
        step = SynthesizeAudioStep(Registry(), settings=settings)
        script = DubbingScript(
            chapter_number=1,
            segments=[
                DubbingSegment(
                    segment_index=0,
                    segment_type=SegmentType.NARRATION,
                    text="测试旁白",
                )
            ],
        )
        results = await step.run(
            SynthesizeAudioInput(
                chapter_number=1,
                script=script,
                voice_team=VoiceTeamContract(entries=[]),
                provider=TTSProvider.MINIMAX,
                output_dir=tmp_path,
                retry_limit=0,
            )
        )

        # Wave 0 failed → post-wave retry round succeeded.
        assert len(adapter.synthesize_calls) == 2
        assert results[0].status == SynthesisStatus.COMPLETED

    async def test_minimax_wave_pool_disables_segment_inline_retry_and_records_wave_retry(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A MiniMax retry must happen only after every initial wave finishes."""

        class Registry:
            def get_adapter(self, _provider: TTSProvider) -> FauxTTSAdapter:
                return FauxTTSAdapter(latency_ms=0)

        settings = Settings(
            tts_wave_pool_enabled=True,
            tts_wave_pool_size=3,
            tts_wave_pool_retry_rounds=1,
            tts_wave_retry_delay_seconds=0.0,
        )

        class RecordingPacer:
            def __init__(self) -> None:
                self.batches: list[int] = []

            async def acquire(self) -> None:
                return None

            async def acquire_batch(self, count: int) -> None:
                self.batches.append(count)

            async def defer(self, _cooldown_s: float) -> None:
                return None

        pacer = RecordingPacer()
        monkeypatch.setattr(
            synthesize_audio_module,
            "SynthesisRequestPacer",
            lambda _rpm: pacer,
        )
        step = SynthesizeAudioStep(Registry(), settings=settings)
        seen_retry_limits: list[tuple[int, int]] = []
        seen_initial_pacing: list[bool] = []
        attempts: dict[int, int] = {}

        async def fake_synthesize_with_retry(
            *,
            segment_idx: int,
            retry_limit: int,
            initial_request_paced: bool,
            **_: object,
        ) -> SynthesisResult:
            seen_retry_limits.append((segment_idx, retry_limit))
            seen_initial_pacing.append(initial_request_paced)
            attempts[segment_idx] = attempts.get(segment_idx, 0) + 1
            if segment_idx == 0 and attempts[segment_idx] == 1:
                return SynthesisResult(
                    segment_index=segment_idx,
                    status=SynthesisStatus.FAILED,
                    retryable=True,
                )
            return SynthesisResult(
                segment_index=segment_idx,
                status=SynthesisStatus.COMPLETED,
            )

        step._synthesize_with_retry = fake_synthesize_with_retry  # type: ignore[method-assign]
        script = DubbingScript(
            chapter_number=1,
            segments=[
                DubbingSegment(
                    segment_index=index,
                    segment_type=SegmentType.NARRATION,
                    text=f"第{index}段旁白。",
                )
                for index in range(4)
            ],
        )

        results = await step.run(
            SynthesizeAudioInput(
                chapter_number=1,
                script=script,
                voice_team=VoiceTeamContract(entries=[]),
                provider=TTSProvider.MINIMAX,
                output_dir=tmp_path,
                # A generic provider would receive this budget inline. MiniMax
                # must instead receive the single-attempt budget of zero.
                retry_limit=3,
                persist_progress=False,
            )
        )

        # First round consumes 0–2 then 3. Only then is item 0 retried.
        assert [index for index, _limit in seen_retry_limits] == [0, 1, 2, 3, 0]
        assert {limit for _index, limit in seen_retry_limits} == {0}
        assert seen_initial_pacing == [True, True, True, True, True]
        assert pacer.batches == [3, 1, 1]
        assert results[0].status == SynthesisStatus.COMPLETED
        assert results[0].retry_count == 1

    async def test_non_retryable_failure_not_retried_by_pool(
        self,
        tmp_path: Path,
    ) -> None:
        # Authentication failures are classified non-retryable: the pool must
        # keep the original decision and never schedule retry rounds.
        adapter = FauxTTSAdapter(latency_ms=0)
        adapter.synthesize = AsyncMock(side_effect=AuthenticationError("invalid api key"))

        class Registry:
            def get_adapter(self, _provider: TTSProvider) -> FauxTTSAdapter:
                return adapter

        step = SynthesizeAudioStep(Registry(), settings=Settings())
        script = DubbingScript(
            chapter_number=1,
            segments=[
                DubbingSegment(
                    segment_index=0,
                    segment_type=SegmentType.NARRATION,
                    text="测试旁白",
                )
            ],
        )
        results = await step.run(
            SynthesizeAudioInput(
                chapter_number=1,
                script=script,
                voice_team=VoiceTeamContract(entries=[]),
                provider=TTSProvider.MINIMAX,
                output_dir=tmp_path,
                retry_limit=0,
            )
        )

        # Authentication is classified non-retryable: the pool keeps the
        # original decision — exactly one call, no retry rounds.
        assert adapter.synthesize.await_count == 1
        assert results[0].status == SynthesisStatus.FAILED


class TestWavePoolAdaptiveBackoff:
    """P2-2: adaptive settle delays via ``retry_delay_fn``."""

    async def test_retry_delay_fn_receives_round_and_outcomes(self, monkeypatch) -> None:
        calls: list[tuple[int, int]] = []

        def delay_fn(round_no: int, outcomes) -> float:
            calls.append((round_no, len(outcomes)))
            return 0.0

        pool = MiniMaxWavePool(
            wave_size=2,
            max_retry_rounds=1,
            retry_delay_s=9.9,  # would be used without the injected fn
            retry_delay_fn=delay_fn,
        )
        outcomes = await pool.run([0, 1, 2], _boom)

        # Only the failed item retries; the adaptive callback receives exactly
        # that pending set rather than successful calls from the prior round.
        assert calls == [(1, 1)]
        assert outcomes[0].error is not None
        assert outcomes[1].error is None
        assert outcomes[2].error is None

    async def test_fixed_delay_still_used_without_fn(self, monkeypatch) -> None:
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        pool = MiniMaxWavePool(
            wave_size=2,
            max_retry_rounds=1,
            retry_delay_s=0.5,
        )
        await pool.run([0, 1], _boom)

        # The fixed 0.5s settle delay was used before the retry round (the
        # 0.005 sleeps come from the call helper itself).
        assert 0.5 in sleeps

    async def test_adaptive_fn_suppresses_fixed_delay(self, monkeypatch) -> None:
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        def delay_fn(round_no: int, outcomes) -> float:
            return 0.0

        pool = MiniMaxWavePool(
            wave_size=2,
            max_retry_rounds=1,
            retry_delay_s=9.9,
            retry_delay_fn=delay_fn,
        )
        await pool.run([0, 1], _boom)

        # The adaptive fn returned 0.0, suppressing the fixed 9.9s delay.
        assert 9.9 not in sleeps
        assert 0.5 not in sleeps
