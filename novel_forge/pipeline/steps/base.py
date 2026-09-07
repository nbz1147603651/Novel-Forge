"""Pipeline step base classes.

``TracedStep`` is the thin base for any pipeline step: it owns settings, the
optional ``PipelineTrace``/``StepTimer`` machinery, an ``on_step`` event sink,
and the ``run()`` wrapper. It carries no LLM dependencies and is the right
parent for steps that never call a model (e.g. TTS synthesis / audio assembly /
ASR alignment).

``PipelineStep`` extends ``TracedStep`` with the LLM-oriented collaborators
(``ModelRouter`` / ``PromptBuilder``) and the ``_call_with_retry`` /
``_dynamic_max_tokens`` helpers that resolve format contracts and token
budgets against the router. LLM-calling steps inherit it unchanged.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Generic, TypeVar

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.core.format_contracts import ContractMode, resolve_task_format_contract
from novel_forge.gateway.router import ModelRouter
from novel_forge.obs.logger import get_logger, log_step
from novel_forge.obs.tracer import PipelineTrace, StepTimer
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens
from novel_forge.prompts.builder import PromptBuilder

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")

StepEventCallback = Callable[[str, dict[str, Any]], None]

# Events that warrant an info-level log line in addition to forwarding.
_LOGGED_STEP_EVENTS: frozenset[str] = frozenset({"token_escalation", "retry_transient_error"})


def forward_step_event(
    callback: StepEventCallback | None,
    logger: Any,
    event: str,
    data: dict[str, Any],
) -> None:
    """Deliver a step event to an optional callback without breaking the caller.

    Shared implementation used both by ``TracedStep._emit`` (instance method,
    bound to ``self._on_step_cb``) and by module-level helpers that operate on
    a bare callback (e.g. ``workspace.execution_tts._emit_tts_progress``).
    Non-critical: any callback failure is swallowed and logged at debug level.
    """
    if event in _LOGGED_STEP_EVENTS:
        logger.info("%s | task=%s | %s", event, data.get("task", "?"), data)
    if callback is None:
        return
    try:
        callback(event, data)
    except Exception:
        logger.debug("on_step callback failed", exc_info=True)


class TracedStep(ABC, Generic[InputT, OutputT]):
    """Thin base for any pipeline step.

    Owns the cross-cutting concerns shared by every step regardless of whether
    it talks to an LLM: ``Settings``, an optional ``PipelineTrace``, an
    ``on_step`` event sink, ``log_step``/``StepTimer`` wrapping in ``run()``,
    and the unified ``_emit`` event forwarder. No router/builder/retry here.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        trace: PipelineTrace | None = None,
        on_step: StepEventCallback | None = None,
        event_bus: Any | None = None,
        project_id: str | None = None,
        hook_chain: Any | None = None,
    ) -> None:
        self._settings = settings
        self._trace = trace or PipelineTrace()
        self._logger = get_logger(self.step_name)
        # Optional sink for step events (llm_stream_*, token_escalation, ...).
        # When wired to the runner's on_step, streaming events reach the desktop
        # instead of being silently dropped here.
        self._on_step_cb: StepEventCallback | None = on_step
        # Optional EventBus for publishing structured pipeline events (Phase 6).
        self._event_bus = event_bus
        self._event_bus_project_id = project_id
        # Optional HookChain for before/after interceptors (Pi-inspired).
        self._hook_chain = hook_chain

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    @abstractmethod
    def step_name(self) -> str:
        """Human-readable step identifier."""

    @abstractmethod
    async def _execute(self, input_data: InputT) -> OutputT:
        """Core logic - subclasses implement this."""

    async def run(self, input_data: InputT) -> OutputT:
        """Run the step with tracing, watchdog logging, hooks, and error capture.

        Hook execution order (Pi-inspired):
        before_hooks → _execute() (with retry/tracing) → after_hooks

        When no hook_chain is attached, behavior is identical to unhooked
        execution (zero overhead fast-path).
        """
        # ── Before hooks ──────────────────────────────────────────────────
        effective_input = input_data
        if self._hook_chain is not None and not self._hook_chain.is_empty:
            effective_input = await self._hook_chain.run_before(self.step_name, input_data)
            if effective_input is None:
                # A before-hook signaled to skip execution
                log_step(self._logger, self.step_name, status="skipped_by_hook")
                return None  # type: ignore[return-value]

        log_step(self._logger, self.step_name, status="start")
        await self._publish_bus_event("step_started", {"step_name": self.step_name})
        async with StepTimer(self.step_name, trace=self._trace):
            try:
                result = await self._execute(effective_input)
                log_step(self._logger, self.step_name, status="done")
                await self._publish_bus_event("step_completed", {"step_name": self.step_name})
            except Exception as exc:
                log_step(self._logger, self.step_name, status="error")
                await self._publish_bus_event("step_failed", {"step_name": self.step_name, "error": str(exc)})
                raise

        # ── After hooks ───────────────────────────────────────────────────
        if self._hook_chain is not None and not self._hook_chain.is_empty:
            result = await self._hook_chain.run_after(self.step_name, effective_input, result)

        return result

    async def _publish_bus_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Publish an event to the EventBus if one is attached."""
        if self._event_bus is None:
            return
        try:
            from novel_forge.core.infra.event_bus import ProjectEvent

            await self._event_bus.publish(
                ProjectEvent(
                    project_id=self._event_bus_project_id,
                    event_type=event_type,
                    data=data,
                )
            )
        except Exception:  # noqa: BLE001
            pass  # EventBus failures must never break the pipeline

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        """Forward a step event to the optional ``on_step`` callback.

        Single canonical implementation of the event-forwarding logic that
        was previously triplicated (``PipelineStep._on_step_event``,
        ``BuildVoiceTeamStep._emit``, ``workspace.execution_tts._emit_tts_progress``).
        Logs ``token_escalation`` / ``retry_transient_error`` at info level
        and swallows non-critical callback failures.
        """
        forward_step_event(self._on_step_cb, self._logger, event, data)


class PipelineStep(TracedStep[InputT, OutputT]):
    """LLM-oriented pipeline step.

    Extends :class:`TracedStep` with the model-routing collaborators
    (``ModelRouter`` / ``PromptBuilder``) and the ``_call_with_retry`` /
    ``_dynamic_max_tokens`` helpers. Steps that call an LLM inherit this;
    steps that only drive TTS adapters / local audio / sidecars should
    inherit :class:`TracedStep` directly to avoid carrying dead LLM state.
    """

    def __init__(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
        *,
        settings: Settings,
        trace: PipelineTrace | None = None,
        on_step: StepEventCallback | None = None,
    ) -> None:
        super().__init__(settings=settings, trace=trace, on_step=on_step)
        self._router = router
        self._builder = builder

    def _on_step_event(self, event: str, data: dict[str, Any]) -> None:
        """Back-compat alias for :meth:`_emit`.

        Kept so the ~24 existing ``self._on_step_event(...)`` call sites in
        LLM-step subclasses keep working; new code should call ``_emit``.
        """
        self._emit(event, data)

    def _dynamic_max_tokens(
        self,
        task_type: TaskType,
        target_output_chars: int,
        *,
        prompt_overhead: int = 2000,
        safety_margin: float = 0.85,
        min_tokens: int = 4096,
        max_cap: int | None = None,
    ) -> int:
        """Return a route-aware output budget for this step's current model."""
        return calculate_route_aware_max_tokens(
            self._router,
            task_type,
            target_output_chars,
            prompt_overhead=prompt_overhead,
            safety_margin=safety_margin,
            min_tokens=min_tokens,
            max_cap=max_cap,
        )

    async def _call_with_retry(
        self,
        task_type: TaskType,
        context: dict[str, Any],
        *,
        max_tokens: int,
        temperature: float,
        top_p: float | None = None,
        required_keys: tuple[str, ...] = (),
        max_retries: int = 2,
        prior_messages: list[dict[str, str]] | None = None,
        thinking: bool = False,
        multi_turn: bool = False,
        provider: str | None = None,
        model_id: str | None = None,
        validate_text_output: bool = True,
        on_step: StepEventCallback | None = None,
    ) -> dict[str, Any] | str:
        """Delegate to LLMService for unified retry + escalation logic (L1/L3).

        ``on_step`` (when provided) overrides the step's default event sink so
        callers can route streaming events to an arbitrary callback without
        mutating step state.
        """
        from novel_forge.pipeline.long.services.generation.llm_service import (
            LLMService,  # noqa: PLC0415
        )

        service = LLMService(
            router=self._router,
            builder=self._builder,
            on_step=on_step or self._on_step_event,
            settings=self._settings,
        )
        contract = resolve_task_format_contract(task_type, context)
        streams_text_output = (
            contract is not None
            and contract.effective_contract_mode == ContractMode.TEXT_ONLY
        )
        if (
            bool(getattr(self._settings, "long_streaming_text_enabled", True))
            and streams_text_output
        ):
            return await service.call_text_stream_with_retry(
                task_type,
                context,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                max_retries=max_retries,
                prior_messages=prior_messages,
                thinking=thinking,
                multi_turn=multi_turn,
                provider=provider,
                model_id=model_id,
                validate_text_output=validate_text_output,
            )
        return await service.call_with_retry(
            task_type,
            context,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            required_keys=required_keys,
            max_retries=max_retries,
            prior_messages=prior_messages,
            thinking=thinking,
            multi_turn=multi_turn,
            provider=provider,
            model_id=model_id,
        )
