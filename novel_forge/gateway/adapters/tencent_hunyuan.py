"""Tencent Hunyuan adapter."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from typing import Any, cast

from novel_forge.gateway.adapters.openai_compat import (
    OpenAICompatibleAdapter,
    _extract_thinking,
)
from novel_forge.gateway.pricing import estimate_cost
from novel_forge.gateway.reasoning import (
    normalize_thinking_mode,
    reasoning_effort_for_mode,
    thinking_mode_enabled,
)
from novel_forge.gateway.structured_output import (
    StructuredOutputDialect,
    build_structured_output_request_plan,
    is_structured_output_unsupported_error,
    remember_structured_output_unsupported,
)
from novel_forge.gateway.types import Message, ModelRequest, ModelResponse, StreamChunk

_HUNYUAN_BASE_URL = "https://api.hunyuan.cloud.tencent.com/v1"
_TENCENT_MODEL_ALIASES = {
    "hunyuan-2.0-think": "hy3",
    "hunyuan-2.0-thinking": "hy3",
    "hunyuan-2.0-thinking-20251109": "hy3",
    "hunyuan-2.0-instruct": "hy3",
    "hunyuan-2.0-instruct-20251111": "hy3",
    "hunyuan-t1": "hy3",
    "hunyuan-t1-latest": "hy3",
    "hunyuan-turbos": "hy3",
    "hunyuan-turbos-latest": "hy3",
    "hunyuan-large-role": "hunyuan-large-role-latest",
}
_RESULT_ONLY_MODEL_FALLBACKS: dict[str, str] = {}


def normalize_tencent_model_id(model_id: str) -> str:
    value = str(model_id or "").strip()
    return _TENCENT_MODEL_ALIASES.get(value.lower(), value) if value else ""


def optimize_tencent_model_id(model_id: str, *, thinking: bool) -> str:
    normalized = normalize_tencent_model_id(model_id)
    return normalized if thinking else _RESULT_ONLY_MODEL_FALLBACKS.get(normalized, normalized)


class TencentHunyuanAdapter(OpenAICompatibleAdapter):
    def __init__(
        self,
        api_key: str,
        default_model: str = "hy3",
        *,
        connect_timeout_s: float = 10.0,
        read_timeout_s: float = 1200.0,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=_HUNYUAN_BASE_URL,
            provider="tencent",
            default_model=default_model,
            connect_timeout_s=connect_timeout_s,
            read_timeout_s=read_timeout_s,
        )

    def _thinking_extra_body(self) -> dict[str, Any] | None:
        return {"thinking": {"type": "enabled"}}

    def _thinking_request_kwargs(
        self,
        request: ModelRequest,
        model_id: str,
    ) -> dict[str, Any]:
        del model_id
        mode = normalize_thinking_mode(request.thinking_mode, thinking=request.thinking)
        enabled = thinking_mode_enabled(mode)
        body: dict[str, Any] = {
            "thinking": {"type": "enabled" if enabled else "disabled"}
        }
        effort = reasoning_effort_for_mode(mode)
        if enabled and effort in {"low", "medium", "high"}:
            body["reasoning_effort"] = effort
        return {"extra_body": body}

    def _stream_request_for_provider(self, request: ModelRequest) -> ModelRequest:
        model_id = optimize_tencent_model_id(
            request.model_id or self._default_model,
            thinking=bool(request.thinking),
        )
        messages = list(request.messages)
        if model_id == "hunyuan-a13b" and not request.thinking:
            messages = [cast(Message, dict(msg)) for msg in messages]
            for msg in messages:
                if msg.get("role") == "user" and msg.get("content"):
                    msg["content"] = "/no_think " + str(msg["content"])
                    break
        return request.model_copy(update={"model_id": model_id, "messages": messages})

    async def stream(
        self,
        request: ModelRequest,
        *,
        on_final: Callable[[ModelResponse], None] | None = None,
    ) -> AsyncIterator[StreamChunk | str]:
        async for chunk in super().stream(
            self._stream_request_for_provider(request),
            on_final=on_final,
        ):
            yield chunk

    async def complete(self, request: ModelRequest) -> ModelResponse:
        model_id = optimize_tencent_model_id(
            request.model_id or self._default_model, thinking=bool(request.thinking)
        )
        extra_kwargs = self._thinking_request_kwargs(request, model_id)
        structured_plan = build_structured_output_request_plan(
            request,
            provider=self._provider,
            model_id=model_id,
            dialect=StructuredOutputDialect.OPENAI_RESPONSE_FORMAT,
        )
        self._merge_request_kwargs(extra_kwargs, dict(structured_plan.kwargs))
        messages = list(request.messages)
        if model_id == "hunyuan-a13b" and not request.thinking:
            for msg in messages:
                if msg.get("role") == "user" and msg.get("content"):
                    msg["content"] = "/no_think " + msg["content"]
                    break
        client = self._get_client()
        start = time.monotonic()
        call_kwargs: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
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
        if "<think>" in content:
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
