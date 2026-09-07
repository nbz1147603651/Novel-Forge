"""Pipeline tracing with step timing and model-call telemetry."""

from __future__ import annotations

import asyncio
import logging
import time
import traceback as _traceback
from contextlib import AbstractContextManager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from types import TracebackType
from typing import ClassVar

from novel_forge.obs.context import bind_log_context

_active_step_trace: ContextVar[StepTrace | None] = ContextVar(
    "novel_forge_active_step_trace",
    default=None,
)

# ---------------------------------------------------------------------------
# Watchdog thresholds — warn to console when a step hangs
# ---------------------------------------------------------------------------

#: Seconds before emitting the first "step is slow" warning.
STEP_WARN_FIRST_S: float = 120.0
#: Interval between subsequent "step still running" warnings.
STEP_WARN_INTERVAL_S: float = 60.0

_watchdog_log = logging.getLogger("novel_forge.pipeline.watchdog")


async def _watchdog_coroutine(step_name: str, started_at: float) -> None:
    """Background task: warn periodically when a step takes too long."""
    try:
        await asyncio.sleep(STEP_WARN_FIRST_S)
        elapsed = time.monotonic() - started_at
        _watchdog_log.warning(
            "step_slow | step=%s | elapsed_s=%.0f | 步骤运行时间较长，可能正在等待模型响应",
            step_name,
            elapsed,
        )
        while True:
            await asyncio.sleep(STEP_WARN_INTERVAL_S)
            elapsed = time.monotonic() - started_at
            _watchdog_log.warning(
                "step_slow | step=%s | elapsed_s=%.0f | 步骤仍在运行中",
                step_name,
                elapsed,
            )
    except asyncio.CancelledError:
        pass


@dataclass
class ModelCallTrace:
    """Telemetry for a single routed model call."""

    task: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    thinking: bool = False
    multi_turn: bool = False
    success: bool = True
    error: str = ""
    # ── Extended telemetry (backward-compatible: all have defaults) ──
    model_call_id: str = ""           # UUID v4 for idempotent event tracking
    chapter_run_id: str = ""          # links to chapter-level run
    attempt_id: str = ""              # links to a specific replan attempt
    chapter_number: int = 0
    lane: str = ""                    # "continuity" | "causal" | "reading_power" | ...
    repair_round: int = -1            # -1 = not in a repair loop
    mutation_id: str = ""             # links to a TextMutation event
    cost_source: str = "reported"     # "reported" | "estimated" | "unknown"

    def as_summary(self) -> dict[str, object]:
        return {
            "task": self.task,
            "provider": self.provider,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "latency_ms": round(self.latency_ms, 2),
            "thinking": self.thinking,
            "multi_turn": self.multi_turn,
            "success": self.success,
            "error": self.error,
        }


@dataclass
class StepTrace:
    """Record for a single pipeline step execution."""

    step_name: str
    started_at: float = 0.0
    ended_at: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tokens_used: int = 0
    cost_usd: float = 0.0
    success: bool = True
    error: str = ""
    error_traceback: str = ""
    retry_count: int = 0
    model_calls: list[ModelCallTrace] = field(default_factory=list)

    @property
    def duration_ms(self) -> float:
        return (self.ended_at - self.started_at) * 1000

    def record_model_call(self, call: ModelCallTrace) -> None:
        self.prompt_tokens += call.prompt_tokens
        self.completion_tokens += call.completion_tokens
        self.tokens_used += call.total_tokens or (call.prompt_tokens + call.completion_tokens)
        self.cost_usd += call.cost_usd
        self.model_calls.append(call)


@dataclass
class PipelineTrace:
    """Collect traces for all steps in a pipeline run."""

    steps: list[StepTrace] = field(default_factory=list)
    # ── Extended telemetry (backward-compatible: all have defaults) ──
    chapter_run_id: str = ""          # UUID v4, persists across replans
    attempt_id: str = ""              # UUID v4, new per replan attempt
    chapter_number: int = 0
    status: str = ""                  # "success" | "failed" | "cancelled" | "replanned" | "budget_blocked"

    @property
    def total_prompt_tokens(self) -> int:
        return sum(step.prompt_tokens for step in self.steps)

    @property
    def total_completion_tokens(self) -> int:
        return sum(step.completion_tokens for step in self.steps)

    @property
    def total_tokens(self) -> int:
        return sum(step.tokens_used for step in self.steps)

    @property
    def total_cost(self) -> float:
        return sum(step.cost_usd for step in self.steps)

    @property
    def total_duration_ms(self) -> float:
        return sum(step.duration_ms for step in self.steps)

    def add(self, trace: StepTrace) -> None:
        self.steps.append(trace)

    def step(self, step_name: str) -> StepTimer:
        return StepTimer(step_name, trace=self)

    def summary(self) -> dict[str, object]:
        return {
            "step_count": len(self.steps),
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost, 6),
            "total_duration_ms": round(self.total_duration_ms, 2),
            "stages": self._stage_breakdown(),
            "steps": [
                {
                    "name": step.step_name,
                    "prompt_tokens": step.prompt_tokens,
                    "completion_tokens": step.completion_tokens,
                    "tokens": step.tokens_used,
                    "cost": round(step.cost_usd, 6),
                    "duration_ms": round(step.duration_ms, 2),
                    "success": step.success,
                    "error": step.error,
                    "error_traceback": step.error_traceback,
                    "retry_count": step.retry_count,
                    "model_call_count": len(step.model_calls),
                    "model_calls": [call.as_summary() for call in step.model_calls],
                }
                for step in self.steps
            ],
        }

    # Stage prefix mapping for three-level cost ledger
    _STAGE_PREFIXES: ClassVar[dict[str, tuple[str, ...]]] = {
        "planning": ("bridge", "plan", "context_compress", "state_packet"),
        "drafting": ("draft",),
        "editing": ("edit", "chapter_repair"),
        "quality": (
            "alignment", "continuity", "causal", "pronoun",
            "repetition", "self_repetition", "dedup", "post_repair",
            "reading_power",
        ),
        "finalize": ("extract", "evaluate", "persist", "polish", "strand_weave", "time_validation"),
    }

    def _classify_step(self, step_name: str) -> str:
        lower = step_name.lower()
        for stage, prefixes in self._STAGE_PREFIXES.items():
            if any(lower.startswith(p) for p in prefixes):
                return stage
        return "other"

    def _stage_breakdown(self) -> dict[str, dict[str, float | int]]:
        """Group steps into pipeline stages for cost/token overview."""
        stages: dict[str, dict[str, float | int]] = {}
        for step in self.steps:
            stage = self._classify_step(step.step_name)
            if stage not in stages:
                stages[stage] = {"tokens": 0, "cost_usd": 0.0, "duration_ms": 0.0, "steps": 0}
            stages[stage]["tokens"] += step.tokens_used
            stages[stage]["cost_usd"] += step.cost_usd
            stages[stage]["duration_ms"] += step.duration_ms
            stages[stage]["steps"] += 1
        # Round for display
        for v in stages.values():
            v["cost_usd"] = round(float(v["cost_usd"]), 6)
            v["duration_ms"] = round(float(v["duration_ms"]), 2)
        return stages


class StepTimer:
    """Context manager for timing a step and binding model-call telemetry.

    Supports both sync (``with``) and async (``async with``) usage.
    Async mode also starts a watchdog background task that emits periodic
    WARNING logs when a step runs longer than :data:`STEP_WARN_FIRST_S` seconds.
    """

    def __init__(self, step_name: str, *, trace: PipelineTrace | None = None) -> None:
        self.trace = StepTrace(step_name=step_name)
        self._pipeline_trace = trace
        self._token: Token[StepTrace | None] | None = None
        self._log_context: AbstractContextManager[None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Sync context manager (preserves backward compatibility)
    # ------------------------------------------------------------------

    def __enter__(self) -> StepTrace:
        self.trace.started_at = time.monotonic()
        self._token = _active_step_trace.set(self.trace)
        self._log_context = bind_log_context(step=self.trace.step_name)
        self._log_context.__enter__()
        return self.trace

    def __exit__(
        self,
        exc_type: type | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.trace.ended_at = time.monotonic()
        if self._token is not None:
            _active_step_trace.reset(self._token)
        if self._log_context is not None:
            self._log_context.__exit__(exc_type, exc, tb)
            self._log_context = None
        if exc_type is not None:
            self.trace.success = False
            self.trace.error = str(exc or exc_type.__name__)
            self.trace.error_traceback = "".join(
                _traceback.format_exception(exc_type, exc, tb)
            )
        if self._pipeline_trace is not None:
            self._pipeline_trace.add(self.trace)

    # ------------------------------------------------------------------
    # Async context manager (adds watchdog warning for slow steps)
    # ------------------------------------------------------------------

    async def __aenter__(self) -> StepTrace:
        self.trace.started_at = time.monotonic()
        self._token = _active_step_trace.set(self.trace)
        self._log_context = bind_log_context(step=self.trace.step_name)
        self._log_context.__enter__()
        self._watchdog_task = asyncio.create_task(
            _watchdog_coroutine(self.trace.step_name, self.trace.started_at)
        )
        return self.trace

    async def __aexit__(
        self,
        exc_type: type | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except (asyncio.CancelledError, Exception):
                pass
            self._watchdog_task = None
        self.trace.ended_at = time.monotonic()
        if self._token is not None:
            _active_step_trace.reset(self._token)
        if self._log_context is not None:
            self._log_context.__exit__(exc_type, exc, tb)
            self._log_context = None
        if exc_type is not None:
            self.trace.success = False
            self.trace.error = str(exc or exc_type.__name__)
            self.trace.error_traceback = "".join(
                _traceback.format_exception(exc_type, exc, tb)
            )
        if self._pipeline_trace is not None:
            self._pipeline_trace.add(self.trace)


def record_model_response(
    *,
    task: str,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    cost_usd: float,
    latency_ms: float,
    thinking: bool,
    multi_turn: bool,
) -> None:
    """Attach a successful model response to the active trace step."""
    step = _active_step_trace.get()
    if step is None:
        return
    step.record_model_call(
        ModelCallTrace(
            task=task,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens or (prompt_tokens + completion_tokens),
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            thinking=thinking,
            multi_turn=multi_turn,
        )
    )


def record_model_error(
    *,
    task: str,
    provider: str,
    model: str,
    latency_ms: float,
    thinking: bool,
    multi_turn: bool,
    error: str,
) -> None:
    """Attach a failed model call to the active trace step."""
    step = _active_step_trace.get()
    if step is None:
        return
    step.record_model_call(
        ModelCallTrace(
            task=task,
            provider=provider,
            model=model,
            latency_ms=latency_ms,
            thinking=thinking,
            multi_turn=multi_turn,
            success=False,
            error=error,
        )
    )


def record_retry(task: str, *, reason: str = "") -> None:
    """Increment the retry counter on the currently active step trace.

    Call this whenever a step silently retries a sub-operation (e.g. JSON
    parse failure causing token escalation) so the retry count is visible in
    the trace summary even when no exception propagates.
    """
    step = _active_step_trace.get()
    if step is None:
        return
    step.retry_count += 1
