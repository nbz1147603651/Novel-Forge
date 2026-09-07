"""MiniMax adapter — https://platform.minimaxi.com / https://platform.minimax.io."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from novel_forge.gateway.adapters.openai_compat import (
    OpenAICompatibleAdapter,
    _extract_thinking,
)
from novel_forge.gateway.pricing import estimate_cost
from novel_forge.gateway.reasoning import normalize_thinking_mode, thinking_mode_enabled
from novel_forge.gateway.structured_output import (
    StructuredOutputDialect,
    build_structured_output_request_plan,
    is_structured_output_unsupported_error,
    remember_structured_output_unsupported,
)
from novel_forge.gateway.types import ModelRequest, ModelResponse, StreamChunk

_MINIMAX_CN_BASE_URL = "https://api.minimaxi.com/v1"
_MINIMAX_INTL_BASE_URL = "https://api.minimax.io/v1"
_MINIMAX_DEFAULT_MODEL = "MiniMax-M2.7-highspeed"
_MINIMAX_TEMP_MIN = 0.01
_MINIMAX_TEMP_MAX = 1.0


class MiniMaxAdapter(OpenAICompatibleAdapter):
    """Adapter for MiniMax API."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = _MINIMAX_CN_BASE_URL,
        default_model: str = _MINIMAX_DEFAULT_MODEL,
        connect_timeout_s: float = 10.0,
        read_timeout_s: float = 1200.0,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            provider="minimax",
            default_model=default_model,
            connect_timeout_s=connect_timeout_s,
            read_timeout_s=read_timeout_s,
        )

    @staticmethod
    def _clamp_temperature(temp: float) -> float:
        """MiniMax requires temperature in (0.0, 1.0]."""
        return max(_MINIMAX_TEMP_MIN, min(_MINIMAX_TEMP_MAX, temp))

    def _thinking_extra_body(self) -> dict[str, Any] | None:
        return {"reasoning_split": True}

    def _thinking_request_kwargs(
        self,
        request: ModelRequest,
        model_id: str,
    ) -> dict[str, Any]:
        mode = normalize_thinking_mode(request.thinking_mode, thinking=request.thinking)
        model_key = model_id.strip().lower()
        body: dict[str, Any] = {}
        if model_key.startswith("minimax-m3"):
            body["thinking"] = {
                "type": "adaptive" if thinking_mode_enabled(mode) else "disabled"
            }
            if thinking_mode_enabled(mode):
                body["reasoning_split"] = True
        elif model_key.startswith("minimax-m2"):
            # M2.x always reasons; reasoning_split changes only the response shape.
            body["reasoning_split"] = True
        return {"extra_body": body} if body else {}

    def _completion_token_limit_kwargs(self, max_tokens: int) -> dict[str, int]:
        """Use MiniMax's current field; ``max_tokens`` is deprecated upstream."""

        return {"max_completion_tokens": max_tokens}

    async def stream(
        self,
        request: ModelRequest,
        *,
        on_final: Callable[[ModelResponse], None] | None = None,
    ) -> AsyncIterator[StreamChunk | str]:
        stream_request = request.model_copy(
            update={"temperature": self._clamp_temperature(request.temperature)}
        )
        async for chunk in super().stream(stream_request, on_final=on_final):
            yield chunk

    async def complete(self, request: ModelRequest) -> ModelResponse:
        client = self._get_client()
        model_id = request.model_id or self._default_model
        temperature = self._clamp_temperature(request.temperature)

        extra_kwargs = self._thinking_request_kwargs(request, model_id)
        structured_plan = build_structured_output_request_plan(
            request,
            provider=self._provider,
            model_id=model_id,
            dialect=StructuredOutputDialect.OPENAI_RESPONSE_FORMAT,
        )
        self._merge_request_kwargs(extra_kwargs, dict(structured_plan.kwargs))

        start = time.monotonic()
        call_kwargs: dict[str, Any] = {
            "model": model_id,
            "messages": request.messages,
            "temperature": temperature,
            **self._completion_token_limit_kwargs(request.max_tokens),
            **extra_kwargs,
        }
        try:
            response = await client.chat.completions.create(**call_kwargs)
        except Exception as exc:
            if structured_plan.has_native_controls(
                call_kwargs
            ) and is_structured_output_unsupported_error(exc):
                remember_structured_output_unsupported(self._provider, model_id)
                structured_plan = structured_plan.with_runtime_fallback()
                fallback_kwargs = structured_plan.without_native_controls(call_kwargs)
                response = await client.chat.completions.create(**fallback_kwargs)
            else:
                raise
        elapsed = (time.monotonic() - start) * 1000

        choice = response.choices[0]
        content = choice.message.content or ""
        thinking_text = str(getattr(choice.message, "reasoning_content", None) or "")
        reasoning_details = getattr(choice.message, "reasoning_details", None)
        if not thinking_text and isinstance(reasoning_details, list):
            details: list[str] = []
            for detail in reasoning_details:
                text = (
                    detail.get("text")
                    if isinstance(detail, dict)
                    else getattr(detail, "text", None)
                )
                if text:
                    details.append(str(text))
            thinking_text = "".join(details)
        if not reasoning_details and "<think>" in content:
            content, thinking_text = _extract_thinking(content)

        usage = response.usage
        p_tok = usage.prompt_tokens if usage else 0
        c_tok = usage.completion_tokens if usage else 0
        return ModelResponse(
            content=content,
            finish_reason=getattr(choice, "finish_reason", None) or "stop",
            thinking_content=thinking_text,
            model_id=model_id,
            prompt_tokens=p_tok,
            completion_tokens=c_tok,
            total_tokens=usage.total_tokens if usage else 0,
            latency_ms=round(elapsed, 2),
            cost_usd=estimate_cost(model_id, p_tok, c_tok),
            **structured_plan.response_fields(),
        )
