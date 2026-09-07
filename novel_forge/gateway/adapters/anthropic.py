"""Anthropic provider adapter."""

from __future__ import annotations

import importlib
import threading
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from novel_forge.gateway.adapters.openai_compat import (
    _convert_messages,
    _map_error,
)
from novel_forge.gateway.base import ProviderAdapter
from novel_forge.gateway.client_lifecycle import close_async_client
from novel_forge.gateway.pricing import estimate_cost
from novel_forge.gateway.reasoning import (
    normalize_thinking_mode,
    reasoning_effort_for_mode,
    thinking_mode_enabled,
)
from novel_forge.gateway.structured_output import (
    StructuredOutputDialect,
    StructuredOutputRequestPlan,
    build_structured_output_request_plan,
    is_structured_output_unsupported_error,
    remember_structured_output_unsupported,
)
from novel_forge.gateway.types import ModelRequest, ModelResponse, StreamChunk


def _anthropic_structured_output_plan(
    request: ModelRequest,
    model_id: str,
) -> StructuredOutputRequestPlan:
    return build_structured_output_request_plan(
        request,
        provider="anthropic",
        model_id=model_id,
        dialect=StructuredOutputDialect.ANTHROPIC_OUTPUT_CONFIG,
    )


def _anthropic_error_body(exc: BaseException) -> dict[str, Any]:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        return body
    message = getattr(exc, "message", None) or str(exc)
    return {"error": {"message": str(message)}}


def _anthropic_reasoning_kwargs(request: ModelRequest, model_id: str) -> dict[str, Any]:
    """Build version-aware Anthropic thinking and effort controls."""

    model_key = model_id.strip().lower()
    mode = normalize_thinking_mode(request.thinking_mode, thinking=request.thinking)
    enabled = thinking_mode_enabled(mode)
    effort = reasoning_effort_for_mode(mode) or "high"

    if "fable-5" in model_key or "mythos-5" in model_key:
        # These families use always-on adaptive thinking; effort is the only control.
        return {"output_config": {"effort": effort}}
    if any(version in model_key for version in ("-5", "4-6", "4-7", "4-8")):
        kwargs: dict[str, Any] = {
            "thinking": {"type": "adaptive" if enabled else "disabled"}
        }
        if enabled:
            kwargs["output_config"] = {"effort": effort}
        return kwargs
    if enabled and any(version in model_key for version in ("4-5", "4-2025")):
        budget = max(1024, min(8192, max(1024, request.max_tokens // 2)))
        if budget >= request.max_tokens:
            budget = max(1, request.max_tokens - 1)
        return {"thinking": {"type": "enabled", "budget_tokens": budget}}
    return {}


def _merge_anthropic_kwargs(target: dict[str, Any], additions: dict[str, Any]) -> None:
    for key, value in additions.items():
        if key == "output_config" and isinstance(value, dict):
            current = target.get(key)
            merged = dict(current) if isinstance(current, dict) else {}
            merged.update(value)
            target[key] = merged
        else:
            target[key] = value


class AnthropicAdapter(ProviderAdapter):
    def __init__(
        self,
        api_key: str,
        *,
        default_model: str = "claude-sonnet-4-6",
        connect_timeout_s: float = 30.0,
        read_timeout_s: float = 1200.0,
    ) -> None:
        self._api_key = api_key
        self._default_model = default_model
        self._connect_timeout_s = max(0.0, connect_timeout_s)
        self._read_timeout_s = max(0.0, read_timeout_s)
        self._write_timeout_s = max(30.0, min(self._read_timeout_s, 120.0))
        self._last_health_error = ""
        self._client: Any | None = None
        self._client_lock = threading.Lock()

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        anthropic = importlib.import_module("anthropic")
        httpx = importlib.import_module("httpx")
        with self._client_lock:
            if self._client is not None:
                return self._client
            timeout = httpx.Timeout(
                connect=self._connect_timeout_s,
                read=self._read_timeout_s,
                write=self._write_timeout_s,
                pool=10.0,
            )
            self._client = anthropic.AsyncAnthropic(
                api_key=self._api_key,
                timeout=timeout,
                max_retries=0,
            )
            return self._client

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def default_model(self) -> str | None:
        return self._default_model

    @property
    def connect_timeout_s(self) -> float:
        return self._connect_timeout_s

    @property
    def last_health_error(self) -> str:
        return self._last_health_error

    async def shutdown(self) -> None:
        if self._client is not None:
            await close_async_client(self._client)
            self._client = None

    async def complete(self, request: ModelRequest) -> ModelResponse:
        client = self._get_client()
        model_id = request.model_id or self._default_model
        messages = _convert_messages(request.messages)
        system_msg = next((m["content"] for m in messages if m["role"] == "system"), "")
        user_messages = [
            {"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"
        ]
        system_blocks = [
            {"type": "text", "text": system_msg, "cache_control": {"type": "ephemeral"}}
        ]
        structured_plan = _anthropic_structured_output_plan(request, model_id)
        extra_kwargs = dict(structured_plan.kwargs)
        _merge_anthropic_kwargs(extra_kwargs, _anthropic_reasoning_kwargs(request, model_id))

        async def _create(system_value: Any, kwargs: dict[str, Any]) -> Any:
            nonlocal structured_plan

            call_kwargs = {
                "model": model_id,
                "max_tokens": request.max_tokens,
                "system": system_value,
                "messages": user_messages,
                **kwargs,
            }
            try:
                return await client.messages.create(**call_kwargs)
            except Exception as exc:
                if structured_plan.has_native_controls(
                    call_kwargs
                ) and is_structured_output_unsupported_error(exc):
                    remember_structured_output_unsupported("anthropic", model_id)
                    structured_plan = structured_plan.with_runtime_fallback()
                    fallback_kwargs = structured_plan.without_native_controls(call_kwargs)
                    return await client.messages.create(**fallback_kwargs)
                raise

        start = time.monotonic()
        try:
            response = await _create(system_blocks, extra_kwargs)
        except Exception as _cache_exc:
            exc_msg = str(_cache_exc).lower()
            if "cache" in exc_msg or "system" in exc_msg:
                response = await _create(system_msg, extra_kwargs)
            else:
                raise _map_error(
                    getattr(_cache_exc, "status_code", 500),
                    _anthropic_error_body(_cache_exc),
                ) from _cache_exc
        elapsed = (time.monotonic() - start) * 1000
        # Claude extended thinking: thinking text arrives as a content block
        # with type="thinking"; the final answer is in type="text" blocks.
        # Blocks without an explicit "type" but carrying ".text" are accepted
        # as text because some SDK response objects expose that compact shape.
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        for block in response.content or []:
            block_type = getattr(block, "type", None) or (
                block.get("type") if isinstance(block, dict) else None
            )
            if block_type == "thinking":
                thinking_text = getattr(block, "thinking", None) or (
                    block.get("thinking") if isinstance(block, dict) else None
                )
                if thinking_text:
                    thinking_parts.append(thinking_text)
            else:
                # Covers explicit "text" blocks and compact .text blocks.
                text_text = getattr(block, "text", None) or (
                    block.get("text") if isinstance(block, dict) else None
                )
                if text_text:
                    text_parts.append(text_text)
        content = "".join(text_parts)
        thinking_content = "\n\n".join(thinking_parts)
        p_tok = response.usage.input_tokens
        c_tok = response.usage.output_tokens
        raw_stop = getattr(response, "stop_reason", None) or "end_turn"
        finish = "length" if raw_stop == "max_tokens" else "stop"
        return ModelResponse(
            content=content,
            finish_reason=finish,
            thinking_content=thinking_content,
            model_id=model_id,
            prompt_tokens=p_tok,
            completion_tokens=c_tok,
            total_tokens=p_tok + c_tok,
            latency_ms=round(elapsed, 2),
            cost_usd=estimate_cost(model_id, p_tok, c_tok),
            **structured_plan.response_fields(),
        )

    async def stream(
        self,
        request: ModelRequest,
        *,
        on_final: Callable[[ModelResponse], None] | None = None,
    ) -> AsyncIterator[StreamChunk | str]:
        """Yield content/reasoning deltas using Anthropic's native streaming API.

        Uses ``client.messages.stream(...)`` which emits ``text_delta`` and
        ``thinking_delta`` events as the model generates tokens.  Extended
        thinking deltas are yielded as :class:`StreamChunk` with the
        ``reasoning`` field; text deltas use the ``content`` field.  Token
        usage is reported in the terminal ``message_stop`` /
        ``message_delta`` event.
        """
        client = self._get_client()
        model_id = request.model_id or self._default_model
        messages = _convert_messages(request.messages)
        system_msg = next((m["content"] for m in messages if m["role"] == "system"), "")
        user_messages = [
            {"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"
        ]
        system_blocks = [
            {"type": "text", "text": system_msg, "cache_control": {"type": "ephemeral"}}
        ]
        structured_plan = _anthropic_structured_output_plan(request, model_id)
        extra_kwargs = dict(structured_plan.kwargs)
        _merge_anthropic_kwargs(extra_kwargs, _anthropic_reasoning_kwargs(request, model_id))

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        p_tok = 0
        c_tok = 0
        finish_reason = "stop"
        start = time.monotonic()

        def _reset_stream_state(reason: str) -> StreamChunk | None:
            nonlocal p_tok, c_tok, finish_reason
            had_output = bool(content_parts or reasoning_parts)
            content_parts.clear()
            reasoning_parts.clear()
            p_tok = 0
            c_tok = 0
            finish_reason = "stop"
            if had_output:
                return StreamChunk(reset=True, reset_reason=reason)
            return None

        def _without_structured_output(call_kwargs: dict[str, Any]) -> dict[str, Any]:
            return structured_plan.without_native_controls(call_kwargs)

        async def _consume_stream(
            call_kwargs: dict[str, Any],
        ) -> AsyncIterator[StreamChunk]:
            nonlocal p_tok, c_tok, finish_reason
            async with client.messages.stream(**call_kwargs) as stream_ctx:
                async for event in stream_ctx:
                    etype = getattr(event, "type", "")
                    if etype == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        if delta is None:
                            continue
                        delta_type = getattr(delta, "type", None)
                        # Claude extended thinking: reasoning deltas arrive
                        # as thinking_delta events with a .thinking field.
                        if delta_type == "thinking_delta":
                            thinking_text = getattr(delta, "thinking", None)
                            if thinking_text:
                                reasoning_parts.append(thinking_text)
                                yield StreamChunk(reasoning=thinking_text)
                            continue
                        text_delta = getattr(delta, "text", None)
                        if text_delta:
                            content_parts.append(text_delta)
                            yield StreamChunk(content=text_delta)
                    elif etype == "message_delta":
                        usage = getattr(event, "usage", None)
                        if usage is not None:
                            c_tok = int(getattr(usage, "output_tokens", c_tok) or c_tok)
                        raw_delta = getattr(event, "delta", None)
                        stop_reason = getattr(raw_delta, "stop_reason", None)
                        if stop_reason == "max_tokens":
                            finish_reason = "length"
                final_msg = await stream_ctx.get_final_message()
                usage = getattr(final_msg, "usage", None)
                if usage is not None:
                    p_tok = int(getattr(usage, "input_tokens", 0) or 0)
                    c_tok = int(getattr(usage, "output_tokens", c_tok) or c_tok)

        call_kwargs: dict[str, Any] = {
            "model": model_id,
            "max_tokens": request.max_tokens,
            "system": system_blocks,
            "messages": user_messages,
            **extra_kwargs,
        }

        try:
            async for chunk in _consume_stream(call_kwargs):
                yield chunk
        except Exception as exc:
            if structured_plan.has_native_controls(
                call_kwargs
            ) and is_structured_output_unsupported_error(exc):
                remember_structured_output_unsupported("anthropic", model_id)
                structured_plan = structured_plan.with_runtime_fallback()
                reset = _reset_stream_state("结构化流式模式不可用，正在改用兼容模式。")
                if reset is not None:
                    yield reset
                async for chunk in _consume_stream(_without_structured_output(call_kwargs)):
                    yield chunk
            elif "cache" in str(exc).lower() or "system" in str(exc).lower():
                fallback_kwargs = dict(call_kwargs)
                fallback_kwargs["system"] = system_msg
                reset = _reset_stream_state("提示缓存流式请求失败，正在改用普通系统提示。")
                if reset is not None:
                    yield reset
                try:
                    async for chunk in _consume_stream(fallback_kwargs):
                        yield chunk
                except Exception as fallback_exc:
                    if structured_plan.has_native_controls(
                        fallback_kwargs
                    ) and is_structured_output_unsupported_error(fallback_exc):
                        remember_structured_output_unsupported("anthropic", model_id)
                        structured_plan = structured_plan.with_runtime_fallback()
                        reset = _reset_stream_state(
                            "结构化流式模式不可用，正在改用兼容模式。"
                        )
                        if reset is not None:
                            yield reset
                        async for chunk in _consume_stream(
                            _without_structured_output(fallback_kwargs)
                        ):
                            yield chunk
                    else:
                        raise _map_error(
                            getattr(fallback_exc, "status_code", 500),
                            _anthropic_error_body(fallback_exc),
                        ) from fallback_exc
            else:
                raise _map_error(
                    getattr(exc, "status_code", 500),
                    _anthropic_error_body(exc),
                ) from exc

        elapsed = (time.monotonic() - start) * 1000
        response = ModelResponse(
            content="".join(content_parts),
            finish_reason=finish_reason,
            thinking_content="".join(reasoning_parts),
            model_id=model_id,
            prompt_tokens=p_tok,
            completion_tokens=c_tok,
            total_tokens=p_tok + c_tok,
            latency_ms=round(elapsed, 2),
            cost_usd=estimate_cost(model_id, p_tok, c_tok),
            **structured_plan.response_fields(),
        )
        self._last_stream_response = response
        if on_final is not None:
            on_final(response)

    async def health_check(self) -> bool:
        try:
            client = self._get_client()
            await client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=10,
                messages=[{"role": "user", "content": "ping"}],
            )
            self._last_health_error = ""
            return True
        except Exception as exc:
            self._last_health_error = str(exc)
            return False
