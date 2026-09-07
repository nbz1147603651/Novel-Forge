"""Small structured-output model service for non-pipeline domains.

The long-form pipeline keeps its richer semantic repair layer.  This service
only owns prompt construction, provider routing, generic format validation,
and bounded retries so TTS and Memory do not depend on long-form internals.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable

from novel_forge.common.constants import TaskType
from novel_forge.core.constants import PipelineConstants
from novel_forge.core.format_contracts import (
    merge_required_keys_for_task,
    resolve_task_format_contract,
    should_validate_contract_schema,
    validate_json_output_contract,
)
from novel_forge.core.parsing.format_repair import task_expects_json
from novel_forge.core.parsing.parse_utils import safe_parse_json
from novel_forge.core.parsing.response_schemas import validate_response_schema
from novel_forge.gateway.profiles import get_model_max_output_tokens
from novel_forge.gateway.retry_policy import (
    compute_transient_retry_backoff,
    escalate_retry_tokens,
    is_transient_llm_error,
)
from novel_forge.model_runtime.streaming import route_with_observed_stream

if TYPE_CHECKING:
    from novel_forge.gateway.router import ModelRouter
    from novel_forge.prompts.builder import PromptBuilder

StepCallback = Callable[[str, dict[str, Any]], None]


def _ensure_required_keys(data: dict[str, Any], required_keys: tuple[str, ...]) -> None:
    keys_by_lower = {str(key).lower(): str(key) for key in data}
    for required in required_keys:
        actual = keys_by_lower.get(required.lower())
        if actual is None:
            raise KeyError(f"Missing required response key: {required}")
        if actual != required:
            data[required] = data.pop(actual)


class StructuredModelService:
    """Invoke schema-bound model tasks without importing novel pipeline code."""

    def __init__(
        self,
        *,
        router: ModelRouter,
        builder: PromptBuilder,
        on_step: StepCallback | None = None,
        settings: Any = None,
    ) -> None:
        self._router = router
        self._builder = builder
        self._on_step = on_step or (lambda _event, _payload: None)
        self._settings = settings

    def _observe_json_stream(self, task_type: TaskType) -> bool:
        if not bool(
            getattr(self._settings, "long_streaming_json_observation_enabled", True)
        ):
            return False
        excluded = {
            item.strip().lower()
            for item in str(
                getattr(self._settings, "long_streaming_json_excluded_tasks", "") or ""
            ).split(",")
            if item.strip()
        }
        return task_type.value.lower() not in excluded and callable(
            getattr(self._router, "stream_route", None)
        )

    async def call_with_retry(
        self,
        task_type: TaskType,
        context: dict[str, Any],
        *,
        max_tokens: int = PipelineConstants.INITIAL_MAX_TOKENS,
        temperature: float = 0.7,
        top_p: float | None = None,
        required_keys: tuple[str, ...] = (),
        max_retries: int = 2,
        prior_messages: list[dict[str, str]] | None = None,
        thinking: bool = False,
        multi_turn: bool = False,
        provider: str | None = None,
        model_id: str | None = None,
        include_contract_required_keys: bool = True,
    ) -> dict[str, Any]:
        """Build, route, parse, and validate one structured task."""

        if not task_expects_json(task_type):
            raise TypeError(
                f"StructuredModelService only accepts JSON tasks: {task_type.value}"
            )
        configured_attempts = int(
            getattr(self._settings, "llm_format_retry_attempts", max_retries) or max_retries
        )
        max_attempts = max(1, max(max_retries, configured_attempts))
        current_tokens = max(1, int(max_tokens))
        current_temperature = float(temperature)
        contract = resolve_task_format_contract(task_type, context)
        validate_schema = should_validate_contract_schema(
            contract,
            include_contract_required_keys=include_contract_required_keys,
        )
        effective_required = (
            merge_required_keys_for_task(task_type, required_keys, context=context)
            if include_contract_required_keys
            else required_keys
        )
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            response: Any = None
            request = self._builder.build(
                task_type,
                context,
                max_tokens=current_tokens,
                temperature=current_temperature,
                top_p=top_p,
                prior_messages=prior_messages,
                thinking=thinking,
                multi_turn=multi_turn,
            )
            if model_id:
                request = request.model_copy(update={"model_id": model_id})
            try:
                if self._observe_json_stream(task_type):
                    response = await route_with_observed_stream(
                        self._router,
                        request,
                        on_step=self._on_step,
                        task_type=task_type,
                        chapter=context.get("chapter_number", context.get("chapter", "")),
                        attempt=attempt,
                        max_attempts=max_attempts,
                        stream_kind="json_observation",
                        output_kind="json",
                        provider=provider,
                    )
                elif provider:
                    response = await self._router.route(request, provider=provider)
                else:
                    response = await self._router.route(request)

                if str(getattr(response, "finish_reason", "") or "").lower() == "length":
                    raise ValueError(f"Model output truncated for task {task_type.value}")
                parsed = safe_parse_json(str(getattr(response, "content", "") or ""))
                if not isinstance(parsed, dict):
                    raise TypeError("Model response JSON must be an object")
                if include_contract_required_keys:
                    validate_response_schema(parsed, task_type)
                validate_json_output_contract(
                    task_type,
                    parsed,
                    context=context,
                    explicit_required_keys=effective_required,
                    include_contract_required_keys=False,
                    validate_allowed_keys=validate_schema,
                    validate_json_schema=validate_schema,
                )
                _ensure_required_keys(parsed, effective_required)
                return parsed
            except Exception as exc:
                last_error = exc
                if attempt >= max_attempts:
                    raise
                finish_reason = str(getattr(response, "finish_reason", "") or "")
                if finish_reason == "length":
                    resolved_model = str(
                        getattr(response, "model_id", "") or model_id or ""
                    )
                    model_limit = (
                        get_model_max_output_tokens(resolved_model) if resolved_model else None
                    )
                    current_tokens = escalate_retry_tokens(
                        current_tokens,
                        task_type.value,
                        model_max_tokens=model_limit,
                    )
                current_temperature = 0.0
                backoff = (
                    compute_transient_retry_backoff(exc, attempt)
                    if is_transient_llm_error(exc)
                    else 0.0
                )
                self._on_step(
                    "structured_model_retry",
                    {
                        "task": task_type.value,
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "backoff_seconds": backoff,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                await asyncio.sleep(backoff)

        assert last_error is not None
        raise last_error


__all__ = ["StructuredModelService"]
