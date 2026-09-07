"""Scripted mock adapter for deterministic integration testing.

Extends the existing MockAdapter with a scripted response queue, enabling
precise control over "what the LLM returns on call N" for deterministic
testing of repair loops, quality gates, and TTS pipelines.

Design:
- Backward compatible: when the queue is empty, falls back to MockAdapter's
  default plausible data generation.
- Thread-safe: response queue is protected by a lock.
- Supports: text responses, JSON data, errors, delays, token usage.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


# ── MockResponse ──────────────────────────────────────────────────────────────


@dataclass
class MockResponse:
    """A single scripted response from the mock adapter.

    Attributes:
        text: Raw text content of the response.
        json_data: If provided, serialized as JSON string for text content.
        error: If provided, the adapter raises this exception instead.
        delay_ms: Artificial delay before returning (simulates latency).
        token_usage: Simulated token counts (input, output).
        finish_reason: Simulated finish reason ("stop", "length", etc.).
        model_id: Simulated model identifier.
    """

    text: str = ""
    json_data: dict[str, Any] | None = None
    error: Exception | None = None
    delay_ms: int = 0
    token_usage: tuple[int, int] = (100, 50)
    finish_reason: str = "stop"
    model_id: str = "scripted-mock"

    @property
    def content(self) -> str:
        """Effective text content."""
        if self.json_data is not None:
            import json
            return json.dumps(self.json_data, ensure_ascii=False)
        return self.text


# ── ScriptedMockAdapter ───────────────────────────────────────────────────────


class ScriptedMockAdapter:
    """Mock adapter with a scripted response queue for deterministic tests.

    Usage::

        adapter = ScriptedMockAdapter()
        adapter.set_responses([
            MockResponse(json_data={"score": 5.0, "issues": [...]}),
            MockResponse(json_data={"score": 7.5, "issues": []}),
        ])

        # First call returns score=5.0, second returns score=7.5
        # Third call (queue empty) falls back to default mock behavior
    """

    def __init__(self, *, fallback_to_default: bool = True) -> None:
        self._queue: deque[MockResponse] = deque()
        self._lock = Lock()
        self._fallback_to_default = fallback_to_default
        self._call_count = 0
        self._call_log: list[dict[str, Any]] = []

    # ── Queue Management ──────────────────────────────────────────────────

    def set_responses(self, responses: list[MockResponse]) -> None:
        """Replace the response queue with a new list."""
        with self._lock:
            self._queue = deque(responses)

    def append_responses(self, responses: list[MockResponse]) -> None:
        """Append responses to the end of the queue."""
        with self._lock:
            self._queue.extend(responses)

    @property
    def pending_count(self) -> int:
        """Number of responses remaining in the queue."""
        with self._lock:
            return len(self._queue)

    @property
    def call_count(self) -> int:
        """Total number of calls made to this adapter."""
        return self._call_count

    @property
    def call_log(self) -> list[dict[str, Any]]:
        """Log of all calls made (for assertion in tests)."""
        return list(self._call_log)

    # ── Adapter Interface ─────────────────────────────────────────────────

    async def complete(self, request: Any = None, **kwargs: Any) -> Any:
        """Return the next scripted response or fall back to default."""
        self._call_count += 1
        call_info = {
            "call_number": self._call_count,
            "request": str(request)[:200] if request else "",
            "kwargs_keys": list(kwargs.keys()),
        }
        self._call_log.append(call_info)

        response = self._next_response()
        if response is None:
            if self._fallback_to_default:
                return self._default_response(request)
            raise RuntimeError(
                f"ScriptedMockAdapter: no more scripted responses "
                f"(call #{self._call_count})"
            )

        if response.delay_ms > 0:
            await asyncio.sleep(response.delay_ms / 1000.0)

        if response.error is not None:
            raise response.error

        return _MockAdapterResponse(
            content=response.content,
            finish_reason=response.finish_reason,
            model_id=response.model_id,
            input_tokens=response.token_usage[0],
            output_tokens=response.token_usage[1],
        )

    # Alias for compatibility with different adapter interfaces
    async def generate(self, request: Any = None, **kwargs: Any) -> Any:
        return await self.complete(request, **kwargs)

    async def stream(self, request: Any = None, **kwargs: Any) -> Any:
        """Stream interface — yields the full response as a single chunk."""
        result = await self.complete(request, **kwargs)
        yield result

    # ── Internal ──────────────────────────────────────────────────────────

    def _next_response(self) -> MockResponse | None:
        with self._lock:
            if self._queue:
                return self._queue.popleft()
        return None

    def _default_response(self, request: Any) -> Any:
        """Generate a plausible default response (matches MockAdapter behavior)."""
        return _MockAdapterResponse(
            content='{"result": "mock_default", "score": 7.0}',
            finish_reason="stop",
            model_id="scripted-mock-default",
            input_tokens=100,
            output_tokens=50,
        )


# ── Response Object ───────────────────────────────────────────────────────────


@dataclass
class _MockAdapterResponse:
    """Minimal response object matching the gateway adapter interface."""

    content: str = ""
    finish_reason: str = "stop"
    model_id: str = "scripted-mock"
    input_tokens: int = 100
    output_tokens: int = 50
    completion_tokens: int = 0

    def __post_init__(self) -> None:
        if self.completion_tokens == 0:
            self.completion_tokens = self.output_tokens
