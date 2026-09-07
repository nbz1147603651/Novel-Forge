"""Shared fixtures and helpers for unit tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from novel_forge.gateway.types import ModelRequest, ModelResponse


class _MockRouter:
    """Unified mock router for pipeline step tests.

    Supports both text-content and JSON-payload modes, call capture,
    finish_reason override, and the resolve_model_id_for_task method
    required by llm_service's preflight token estimation.
    """

    def __init__(
        self,
        content: str = "",
        *,
        json_payload: dict | None = None,
        finish_reason: str = "stop",
        capture_calls: bool = True,
        model_id: str = "mock-test",
        prompt_tokens: int = 100,
        completion_tokens: int = 80,
    ) -> None:
        if json_payload is not None:
            self._content = json.dumps(json_payload, ensure_ascii=False)
            self._json_payload = json_payload
        else:
            self._content = content
            self._json_payload = None
        self._finish_reason = finish_reason
        self._model_id = model_id
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens
        self.calls: list[ModelRequest] | None = [] if capture_calls else None

    def resolve_model_id_for_task(
        self,
        task_type: Any,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> str | None:
        return model_id or self._model_id

    async def route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
    ) -> ModelResponse:
        if self.calls is not None:
            self.calls.append(request)
        return ModelResponse(
            content=self._content,
            model_id=self._model_id,
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            total_tokens=self._prompt_tokens + self._completion_tokens,
            cost_usd=0.0,
            finish_reason=self._finish_reason,
        )

    async def stream_route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
        on_delta: Callable[[str], None] | None = None,
        on_chunk: Callable[..., None] | None = None,
    ) -> ModelResponse:
        if on_delta is not None and self._content:
            on_delta(self._content)
        if on_chunk is not None and self._content:
            on_chunk(self._content)
        return await self.route(request, provider=provider)
