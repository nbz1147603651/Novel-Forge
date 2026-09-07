"""OpenAI-compatible adapter — works with any provider that implements
the OpenAI Chat Completions API (DeepSeek, Tongyi/DashScope, Kimi/Moonshot, etc.)."""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.core.exceptions import (
    AuthenticationError,
    ContentFilterError,
    ContextLengthError,
    ModelGatewayError,
    RateLimitError,
)
from novel_forge.gateway.adapters._think_splitter import (
    KIND_REASONING,
    ThinkStreamSplitter,
)
from novel_forge.gateway.base import ProviderAdapter
from novel_forge.gateway.client_lifecycle import close_async_client
from novel_forge.gateway.pricing import estimate_cost
from novel_forge.gateway.reasoning import normalize_thinking_mode, thinking_mode_enabled
from novel_forge.gateway.structured_output import (
    StructuredOutputDialect,
    StructuredOutputRequestPlan,
    build_structured_output_request_plan,
    is_structured_output_unsupported_error,
    remember_structured_output_unsupported,
)
from novel_forge.gateway.types import Message, ModelRequest, ModelResponse, StreamChunk

# ── Thinking-content helpers ──────────────────────────────────────────────────
# Tongyi (DashScope) thinking models embed chain-of-thought inside the content
# field as one or more <think>…</think> blocks.  DeepSeek-R1 puts it in the
# separate `reasoning_content` field, so no stripping is needed for DeepSeek.
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


def _extract_thinking(content: str) -> tuple[str, str]:
    """Split ``content`` into (clean_answer, thinking_text).

    Removes all ``<think>…</think>`` blocks from *content* and returns the
    concatenated inner text as *thinking_text*.  If no tags are present the
    original string is returned unchanged with an empty thinking_text.
    """
    matches = _THINK_RE.findall(content)
    if not matches:
        return content, ""
    thinking_text = "\n\n".join(m.strip() for m in matches)
    clean = _THINK_RE.sub("", content).strip()
    return clean, thinking_text


def _convert_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert typed :class:`Message` objects to plain ``dict`` objects for API calls.

    Validates that each message has the required ``role`` and ``content``
    fields and filters out entries that are missing either.

    Args:
        messages: List of :class:`Message` typed dicts.

    Returns:
        List of plain dicts guaranteed to contain ``role`` and ``content``.

    Example:
        >>> _convert_messages([{"role": "user", "content": "hi"}])
        [{"role": "user", "content": "hi"}]
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role is None or content is None:
            continue
        entry: dict[str, Any] = {"role": role, "content": content}
        name = msg.get("name")
        if name is not None:
            entry["name"] = name
        tool_calls = msg.get("tool_calls")
        if tool_calls is not None:
            entry["tool_calls"] = tool_calls
        result.append(entry)
    return result


def _structured_output_plan(
    request: ModelRequest,
    provider: str,
    model_id: str | None = None,
) -> StructuredOutputRequestPlan:
    """Return the unified provider request plan for structured output."""

    return build_structured_output_request_plan(
        request,
        provider=provider,
        model_id=model_id,
        dialect=StructuredOutputDialect.OPENAI_RESPONSE_FORMAT,
    )


def _parse_stream_chunk(chunk: bytes) -> str:
    """Parse a raw SSE chunk from an OpenAI-compatible stream.

    Extracts the ``delta.content`` text from each choice in the chunk.
    Ignores heartbeat lines, ``[DONE]`` markers, and malformed JSON.

    Args:
        chunk: Raw bytes from the SSE stream.

    Returns:
        Concatenated content text from all choices in the chunk.

    Example:
        >>> data = b'data: {"choices":[{"delta":{"content":"hi"}}]}\\n\\n'
        >>> _parse_stream_chunk(data)
        "hi"
    """
    text = chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
    content_parts: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data: "):
            data = line[6:]
            if data == "[DONE]":
                continue
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue
            for choice in payload.get("choices", []):
                delta = choice.get("delta", {})
                content = delta.get("content")
                if content:
                    content_parts.append(content)
    return "".join(content_parts)


def _map_error(status_code: int, body: dict[str, Any]) -> Exception:
    """Map an HTTP error response to the appropriate Novel Forge exception.

    * 401 / 403 → authentication error (non-transient).
    * 429 → rate-limit / quota exceeded (transient).
    * 502 / 503 / 504 → server error (transient).
    * All others → generic :class:`ModelGatewayError` (non-transient).

    Args:
        status_code: HTTP status code returned by the provider.
        body: Parsed JSON error body (or empty dict).

    Returns:
        A :class:`ModelGatewayError` with ``is_transient`` set
        appropriately.

    Example:
        >>> exc = _map_error(429, {"error": {"message": "Rate limited"}})
        >>> exc.is_transient_error
        True
    """
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        message = body["error"].get("message", "")
    elif isinstance(body, dict):
        message = str(body.get("error", ""))
    else:
        message = ""

    if not message:
        message = f"Provider returned HTTP {status_code}"

    if status_code == 429:
        return RateLimitError(message)
    if status_code in (401, 403):
        return AuthenticationError(message)
    if status_code == 413:
        return ContextLengthError(message)
    if status_code == 422:
        return ContentFilterError(message)
    is_transient = status_code in (502, 503, 504)
    return ModelGatewayError(message, is_transient=is_transient)


def _normalize_params(temperature: float | None, max_tokens: int | None) -> dict[str, Any]:
    """Normalize generation parameters for OpenAI-compatible API requests.

    ``None`` values are omitted from the result so callers can safely
    splat the dict into an SDK method.  ``temperature`` is clamped to
    ``[0.0, 2.0]`` and ``max_tokens`` is forced to at least ``1``.

    Args:
        temperature: Sampling temperature (0.0–2.0), or ``None`` to omit.
        max_tokens: Maximum tokens to generate, or ``None`` to omit.

    Returns:
        Dict of normalized parameters ready for the API call.

    Example:
        >>> _normalize_params(temperature=-0.5, max_tokens=0)
        {"temperature": 0.0, "max_tokens": 1}
    """
    params: dict[str, Any] = {}
    if temperature is not None:
        params["temperature"] = max(0.0, min(2.0, float(temperature)))
    if max_tokens is not None:
        params["max_tokens"] = max(1, int(max_tokens))
    return params


class OpenAICompatibleAdapter(ProviderAdapter):
    """Generic adapter for any OpenAI-compatible API endpoint.

    Parameters
    ----------
    api_key : str
        Provider API key.
    base_url : str
        Base URL for the API, e.g. ``https://api.deepseek.com``.
    provider : str
        Human-readable name such as ``"deepseek"``.
    default_model : str
        Model ID to use when the request does not specify one. Some providers
        may leave this empty so callers must provide an explicit model_id.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        provider: str,
        default_model: str,
        *,
        connect_timeout_s: float = 10.0,
        read_timeout_s: float = 1200.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._provider = provider
        self._default_model = default_model
        self._last_health_error = ""
        self._client: Any | None = None
        self._client_lock = threading.Lock()
        self._connect_timeout_s = max(0.0, connect_timeout_s)
        self._read_timeout_s = max(0.0, read_timeout_s)
        self._write_timeout_s = max(30.0, min(self._read_timeout_s, 120.0))

    def _get_client(self) -> Any:
        """Lazily create and reuse a single AsyncOpenAI client instance."""
        if self._client is not None:
            return self._client
        import httpx
        import openai  # noqa: F811

        with self._client_lock:
            if self._client is not None:
                return self._client
            # Layered timeout: connect (TCP handshake) separate from read (response body).
            # The read timeout here is intentionally higher than router's
            # asyncio.wait_for() timeout so the router remains the single source
            # of truth for per-request cancellation.
            sdk_timeout = httpx.Timeout(
                connect=self._connect_timeout_s,
                read=self._read_timeout_s,
                write=self._write_timeout_s,
                pool=10.0,
            )
            self._client = openai.AsyncOpenAI(
                api_key=self._api_key,
                base_url=f"{self._base_url}/v1"
                if not self._base_url.endswith("/v1")
                else self._base_url,
                timeout=sdk_timeout,
                # Disable SDK-level retries so the outer asyncio.wait_for() in
                # router.py remains the single source of retry/timeout truth.
                # With max_retries>0 the SDK can internally retry for up to
                # timeout×(max_retries+1) seconds, preventing cancellation.
                max_retries=0,
            )
            return self._client

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def default_model(self) -> str | None:
        return self._default_model

    @property
    def connect_timeout_s(self) -> float:
        """Configured transport connection timeout for router diagnostics."""
        return self._connect_timeout_s

    @property
    def last_health_error(self) -> str:
        return self._last_health_error

    def _thinking_extra_body(self) -> dict[str, Any] | None:
        """Return the ``extra_body`` payload to send when ``request.thinking`` is on.

        Subclasses can override this to use a provider-native thinking flag.
        The default mirrors the historical Tongyi (DashScope) behavior —
        ``{"enable_thinking": True}`` — but is gated so it only fires for the
        Tongyi provider and skips the Coding Plan endpoint, which does not
        support thinking.
        """
        if self._provider != "tongyi":
            return None
        if "coding.dashscope" in self._base_url:
            return None
        return {"enable_thinking": True}

    def _thinking_extra_body_for_request(self, enabled: bool) -> dict[str, Any] | None:
        """Return provider-specific thinking controls for one request.

        Most providers only need an extra payload when thinking is enabled.
        Providers whose models default to thinking may override this hook to
        explicitly transmit the disabled state as well.
        """

        if self._provider == "tongyi" and "coding.dashscope" not in self._base_url:
            # Some Qwen3 models default to thinking.  Sending both states keeps
            # the route selection authoritative instead of relying on a model default.
            return {"enable_thinking": enabled}
        return self._thinking_extra_body() if enabled else None

    def _thinking_request_kwargs(
        self,
        request: ModelRequest,
        model_id: str,
    ) -> dict[str, Any]:
        """Translate one normalized route selection into provider kwargs."""

        del model_id
        mode = normalize_thinking_mode(request.thinking_mode, thinking=request.thinking)
        extra_body = self._thinking_extra_body_for_request(thinking_mode_enabled(mode))
        return {"extra_body": extra_body} if extra_body is not None else {}

    @staticmethod
    def _merge_request_kwargs(
        target: dict[str, Any],
        additions: dict[str, Any],
    ) -> None:
        """Merge provider controls without losing structured-output extra_body."""

        for key, value in additions.items():
            if key == "extra_body" and isinstance(value, dict):
                current = target.get(key)
                merged = dict(current) if isinstance(current, dict) else {}
                merged.update(value)
                target[key] = merged
            elif key == "output_config" and isinstance(value, dict):
                current = target.get(key)
                merged = dict(current) if isinstance(current, dict) else {}
                merged.update(value)
                target[key] = merged
            else:
                target[key] = value

    def _completion_token_limit_kwargs(self, max_tokens: int) -> dict[str, int]:
        """Translate the application output budget into provider request fields."""

        return {"max_tokens": max_tokens}

    async def shutdown(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await close_async_client(client)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        model_id = (request.model_id or self._default_model or "").strip()
        if not model_id:
            raise ModelGatewayError(
                f"No model_id configured for provider '{self._provider}'. "
                "Set a model in model_profiles.json, task routing, or the request."
            )

        try:
            import openai  # noqa: F401
        except ImportError as exc:
            raise ModelGatewayError(
                "openai package not installed. "
                "Run: pip install novel-forge[openai]  "
                "(the openai SDK is used as HTTP client for all OpenAI-compatible providers)"
            ) from exc

        client = self._get_client()

        # ── Provider-specific extra parameters ───────────────────────────────
        # Tongyi (DashScope) compatible endpoint: pass enable_thinking via extra_body
        # when the task is configured with :thinking flag.
        # DeepSeek: reasoning is always in a separate field; no extra param needed.
        extra_kwargs = self._thinking_request_kwargs(request, model_id)
        structured_plan = _structured_output_plan(request, self._provider, model_id)
        self._merge_request_kwargs(extra_kwargs, dict(structured_plan.kwargs))

        start = time.monotonic()
        # ── Streaming call ───────────────────────────────────────────────────
        # Using stream=True ensures this coroutine yields to the asyncio event
        # loop on every chunk, so asyncio.wait_for() in the router can cancel
        # reliably.  Non-streaming calls block until the full response arrives;
        # for slow reasoning models (DeepSeek-R1, Tongyi thinking) that can
        # exceed any outer timeout before CancelledError is delivered.
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        finish_reason: str = "stop"
        p_tok = c_tok = total_tok = 0

        stream_call_kwargs: dict[str, Any] = {
            "model": model_id,
            "messages": request.messages,
            "temperature": request.temperature,
            "stream": True,
            # Request usage counts in the terminal chunk (OpenAI standard;
            # ignored gracefully by providers that don't support it).
            "stream_options": {"include_usage": True},
            # top_p is only sent when explicitly narrowed below the 1.0
            # default so unchanged-default payloads stay byte-identical for
            # providers that reject unknown sampling params.
            **({"top_p": request.top_p} if request.top_p < 1.0 else {}),
            **self._completion_token_limit_kwargs(request.max_tokens),
            **extra_kwargs,
        }
        stream: Any | None = None
        try:
            try:
                stream = await client.chat.completions.create(**stream_call_kwargs)
            except Exception as exc:
                if structured_plan.has_native_controls(
                    stream_call_kwargs
                ) and is_structured_output_unsupported_error(exc):
                    remember_structured_output_unsupported(self._provider, model_id)
                    structured_plan = structured_plan.with_runtime_fallback()
                    fallback_kwargs = structured_plan.without_native_controls(stream_call_kwargs)
                    stream = await client.chat.completions.create(**fallback_kwargs)
                else:
                    raise
            async for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    # DeepSeek-R1 streams reasoning in a separate
                    # `reasoning_content` field; accumulate it so the final
                    # ModelResponse.thinking_content is populated.  The
                    # `content` field is the clean final answer.
                    if delta.content:
                        content_parts.append(delta.content)
                    reasoning_piece = getattr(delta, "reasoning_content", None)
                    if reasoning_piece:
                        reasoning_parts.append(reasoning_piece)
                    if chunk.choices[0].finish_reason:
                        finish_reason = chunk.choices[0].finish_reason
                if chunk.usage:
                    p_tok = chunk.usage.prompt_tokens or 0
                    c_tok = chunk.usage.completion_tokens or 0
                    total_tok = chunk.usage.total_tokens or 0
        finally:
            await close_async_client(stream)

        elapsed = (time.monotonic() - start) * 1000
        content = "".join(content_parts)
        reasoning_text = "".join(reasoning_parts)

        # ── Extract thinking content ──────────────────────────────────────────
        # Tongyi / others: thinking may be embedded as <think>…</think> blocks.
        # DeepSeek-R1: reasoning_content is a separate delta field (not in
        # content), so the accumulated content is already clean.
        if "<think>" in content:
            content, embedded_thinking = _extract_thinking(content)
            if embedded_thinking:
                reasoning_text = (
                    reasoning_text + "\n\n" + embedded_thinking
                    if reasoning_text
                    else embedded_thinking
                )

        return ModelResponse(
            content=content,
            finish_reason=finish_reason,
            thinking_content=reasoning_text,
            model_id=model_id,
            prompt_tokens=p_tok,
            completion_tokens=c_tok,
            total_tokens=total_tok,
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
        model_id = (request.model_id or self._default_model or "").strip()
        if not model_id:
            raise ModelGatewayError(
                f"No model_id configured for provider '{self._provider}'. "
                "Set a model in model_profiles.json, task routing, or the request."
            )

        try:
            import openai  # noqa: F401
        except ImportError as exc:
            raise ModelGatewayError(
                "openai package not installed. "
                "Run: pip install novel-forge[openai]  "
                "(the openai SDK is used as HTTP client for all OpenAI-compatible providers)"
            ) from exc

        client = self._get_client()
        extra_kwargs = self._thinking_request_kwargs(request, model_id)
        structured_plan = _structured_output_plan(request, self._provider, model_id)
        self._merge_request_kwargs(extra_kwargs, dict(structured_plan.kwargs))

        start = time.monotonic()
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        finish_reason = "stop"
        p_tok = c_tok = total_tok = 0
        # Stateful splitter for <think>…</think> blocks that may straddle
        # chunk boundaries (Tongyi/DashScope thinking models).  DeepSeek-R1
        # uses a separate reasoning_content field and never enters <think>.
        think_splitter = ThinkStreamSplitter()
        stream_call_kwargs: dict[str, Any] = {
            "model": model_id,
            "messages": request.messages,
            "temperature": request.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
            # See the complete() path above: top_p is opt-in below the 1.0
            # default to keep default payloads byte-identical.
            **({"top_p": request.top_p} if request.top_p < 1.0 else {}),
            **self._completion_token_limit_kwargs(request.max_tokens),
            **extra_kwargs,
        }
        stream: Any | None = None
        try:
            try:
                stream = await client.chat.completions.create(**stream_call_kwargs)
            except Exception as exc:
                if structured_plan.has_native_controls(
                    stream_call_kwargs
                ) and is_structured_output_unsupported_error(exc):
                    remember_structured_output_unsupported(self._provider, model_id)
                    structured_plan = structured_plan.with_runtime_fallback()
                    fallback_kwargs = structured_plan.without_native_controls(stream_call_kwargs)
                    stream = await client.chat.completions.create(**fallback_kwargs)
                else:
                    raise

            async for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    # DeepSeek-R1 streams reasoning in a separate
                    # `reasoning_content` field; emit it as a reasoning chunk
                    # and keep content clean.
                    reasoning_piece = getattr(delta, "reasoning_content", None)
                    if reasoning_piece:
                        reasoning_parts.append(reasoning_piece)
                        yield StreamChunk(reasoning=reasoning_piece)
                    # Tongyi/others may embed <think>…</think> inside content;
                    # split it in real time so content stays clean.
                    if delta.content:
                        for kind, text in think_splitter.feed(delta.content):
                            if kind == KIND_REASONING:
                                reasoning_parts.append(text)
                                yield StreamChunk(reasoning=text)
                            else:
                                content_parts.append(text)
                                yield StreamChunk(content=text)
                    if chunk.choices[0].finish_reason:
                        finish_reason = chunk.choices[0].finish_reason
                if chunk.usage:
                    p_tok = chunk.usage.prompt_tokens or 0
                    c_tok = chunk.usage.completion_tokens or 0
                    total_tok = chunk.usage.total_tokens or 0
        finally:
            await close_async_client(stream)

        # Flush any partial <think> tag buffered at end of stream.
        for kind, text in think_splitter.finish():
            if kind == KIND_REASONING:
                reasoning_parts.append(text)
                yield StreamChunk(reasoning=text)
            else:
                content_parts.append(text)
                yield StreamChunk(content=text)

        elapsed = (time.monotonic() - start) * 1000
        content = "".join(content_parts)
        reasoning_text = "".join(reasoning_parts)
        response = ModelResponse(
            content=content,
            finish_reason=finish_reason,
            thinking_content=reasoning_text,
            model_id=model_id,
            prompt_tokens=p_tok,
            completion_tokens=c_tok,
            total_tokens=total_tok,
            latency_ms=round(elapsed, 2),
            cost_usd=estimate_cost(model_id, p_tok, c_tok),
            **structured_plan.response_fields(),
        )
        self._last_stream_response = response
        if on_final is not None:
            on_final(response)

    async def health_check(self) -> bool:
        """Check connectivity through the same path as normal completions.

        Reuses the existing client (or lazily creates one) without destroying
        it afterwards, so subsequent requests benefit from connection pooling.
        """
        try:
            if not (self._default_model or "").strip():
                self._last_health_error = (
                    f"No default model configured for provider '{self._provider}'."
                )
                return False

            await self.complete(
                ModelRequest(
                    task_type=TaskType.DRAFT,
                    messages=[{"role": "user", "content": "ping"}],
                    model_id=self._default_model,
                    max_tokens=8,
                    temperature=0,
                )
            )
            self._last_health_error = ""
            return True
        except Exception as exc:
            self._last_health_error = str(exc)
            return False
