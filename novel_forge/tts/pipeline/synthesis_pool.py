"""Synthesis pool — producer-consumer TTS synthesis with rate limiting.

Replaces the naive asyncio.gather approach with a bounded worker pool
that respects provider rate limits, supports fallback chains, and
provides structured progress reporting.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Callable

from novel_forge.obs.logger import get_logger
from novel_forge.tts.gateway.base import TTSProviderAdapter
from novel_forge.tts.schemas import (
    SynthesisResult,
    SynthesisStatus,
    TTSRequest,
)

_log = get_logger("tts.pipeline.synthesis_pool")


# ─── Rate limiter ────────────────────────────────────────────────────────────


class TokenBucketRateLimiter:
    """Token-bucket rate limiter for API call throttling.

    Ensures synthesis requests don't exceed the provider's rate limit
    by acquiring tokens before each request.
    """

    def __init__(self, rate: float, burst: int) -> None:
        """Initialize with tokens/sec rate and max burst size."""
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self._rate
                await asyncio.sleep(wait)
                self._tokens = 0.0
                self._last_refill = time.monotonic()
            else:
                self._tokens -= 1.0


# ─── Synthesis task ──────────────────────────────────────────────────────────


@dataclass
class SynthesisTask:
    """A single synthesis task submitted to the pool."""

    segment_index: int
    request: TTSRequest
    retry_limit: int = 3
    on_progress: Callable[[int, str], None] | None = None


@dataclass
class PoolConfig:
    """Configuration for the synthesis pool."""

    max_workers: int = 4
    rate_per_second: float = 2.0
    burst_size: int = 8
    retry_backoff_base: float = 0.5
    fallback_adapter: TTSProviderAdapter | None = None


# ─── Synthesis pool ──────────────────────────────────────────────────────────


class SynthesisPool:
    """Producer-consumer synthesis pool with rate limiting and fallback.

    Usage:
        pool = SynthesisPool(adapter, config=PoolConfig(max_workers=4))
        results = await pool.run(tasks)
    """

    def __init__(
        self,
        adapter: TTSProviderAdapter,
        *,
        config: PoolConfig | None = None,
    ) -> None:
        self._adapter = adapter
        self._config = config or PoolConfig()
        self._queue: asyncio.Queue[SynthesisTask] = asyncio.Queue()
        self._results: dict[int, SynthesisResult] = {}
        self._results_lock = asyncio.Lock()
        self._rate_limiter = TokenBucketRateLimiter(
            self._config.rate_per_second,
            self._config.burst_size,
        )
        self._completed = 0
        self._total = 0
        self._on_global_progress: Callable[[int, int], None] | None = None

    async def run(
        self,
        tasks: list[SynthesisTask],
        *,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[SynthesisResult]:
        """Execute all tasks through the worker pool.

        Args:
            tasks: List of synthesis tasks to execute.
            on_progress: Callback(completed_count, total_count) for
                global progress tracking.

        Returns:
            List of SynthesisResult sorted by segment_index.
        """
        self._total = len(tasks)
        self._completed = 0
        self._results.clear()
        self._on_global_progress = on_progress

        if not tasks:
            return []

        # Enqueue all tasks
        for task in tasks:
            await self._queue.put(task)

        # Launch worker pool
        n_workers = min(self._config.max_workers, len(tasks))
        workers = [asyncio.create_task(self._worker(i)) for i in range(n_workers)]

        # Wait for all tasks to complete
        await self._queue.join()

        # Signal workers to stop
        for _ in workers:
            await self._queue.put(None)  # type: ignore[arg-type]
        await asyncio.gather(*workers, return_exceptions=True)

        # Sort results by segment index
        return [self._results[i] for i in sorted(self._results.keys())]

    async def _worker(self, worker_id: int) -> None:
        """Worker coroutine: dequeue tasks and synthesize."""
        while True:
            task = await self._queue.get()
            if task is None:
                self._queue.task_done()
                break
            try:
                result = await self._process_task(task, worker_id)
                async with self._results_lock:
                    self._results[task.segment_index] = result
                    self._completed += 1

                if self._on_global_progress:
                    self._on_global_progress(self._completed, self._total)
            except Exception as exc:
                _log.error(
                    "Worker %d: unhandled error on segment %d: %s",
                    worker_id,
                    task.segment_index,
                    exc,
                )
                async with self._results_lock:
                    self._results[task.segment_index] = SynthesisResult(
                        segment_index=task.segment_index,
                        status=SynthesisStatus.FAILED,
                        error_message=str(exc),
                    )
                    self._completed += 1
            finally:
                self._queue.task_done()

    async def _process_task(
        self,
        task: SynthesisTask,
        worker_id: int,
    ) -> SynthesisResult:
        """Process a single task with retry and fallback."""
        if task.on_progress:
            task.on_progress(task.segment_index, "synthesizing")

        start = time.monotonic()
        last_error = ""

        for attempt in range(task.retry_limit + 1):
            try:
                # Rate limit
                await self._rate_limiter.acquire()

                response = await self._adapter.synthesize(task.request)

                latency = (time.monotonic() - start) * 1000
                _log.info(
                    "Worker %d: segment %d OK (attempt=%d, latency=%.0fms, dur=%dms)",
                    worker_id,
                    task.segment_index,
                    attempt + 1,
                    latency,
                    response.duration_ms,
                )

                if task.on_progress:
                    task.on_progress(task.segment_index, "completed")

                return SynthesisResult(
                    segment_index=task.segment_index,
                    audio_path="",  # Caller writes to disk
                    duration_ms=response.duration_ms,
                    status=SynthesisStatus.COMPLETED,
                    provider=task.request.provider,
                    model_id=response.model_id,
                    voice_id=response.voice_id,
                    cost_usd=response.cost_usd,
                    latency_ms=response.latency_ms,
                    retry_count=attempt,
                    audio_data=response.audio_data,
                )

            except Exception as exc:
                last_error = str(exc)
                _log.warning(
                    "Worker %d: segment %d attempt %d failed: %s",
                    worker_id,
                    task.segment_index,
                    attempt + 1,
                    exc,
                )
                if attempt < task.retry_limit:
                    backoff = self._config.retry_backoff_base * (2**attempt)
                    await asyncio.sleep(backoff)

        # Primary exhausted — try fallback adapter
        fallback = self._config.fallback_adapter
        if fallback:
            _log.info(
                "Worker %d: segment %d trying fallback adapter",
                worker_id,
                task.segment_index,
            )
            try:
                response = await fallback.synthesize(task.request)
                if task.on_progress:
                    task.on_progress(task.segment_index, "completed")
                return SynthesisResult(
                    segment_index=task.segment_index,
                    audio_path="",
                    duration_ms=response.duration_ms,
                    status=SynthesisStatus.COMPLETED,
                    provider=task.request.provider,
                    model_id=response.model_id,
                    voice_id=response.voice_id,
                    cost_usd=response.cost_usd,
                    latency_ms=response.latency_ms,
                    retry_count=task.retry_limit + 1,
                    audio_data=response.audio_data,
                    used_fallback=True,
                )
            except Exception as fb_exc:
                _log.error(
                    "Worker %d: segment %d fallback also failed: %s",
                    worker_id,
                    task.segment_index,
                    fb_exc,
                )

        if task.on_progress:
            task.on_progress(task.segment_index, "failed")

        return SynthesisResult(
            segment_index=task.segment_index,
            status=SynthesisStatus.FAILED,
            error_message=last_error,
            retry_count=task.retry_limit,
        )
