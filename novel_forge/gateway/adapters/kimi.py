"""Kimi (Moonshot AI) adapter — https://platform.moonshot.cn."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from novel_forge.gateway.adapters.openai_compat import OpenAICompatibleAdapter
from novel_forge.gateway.reasoning import normalize_thinking_mode, thinking_mode_enabled
from novel_forge.gateway.types import ModelRequest, ModelResponse, StreamChunk

# https://platform.kimi.ai/docs/models
_KIMI_BASE_URL = "https://api.moonshot.cn"
_KIMI_DEFAULT_MODEL = "kimi-k2.6"


class KimiAdapter(OpenAICompatibleAdapter):
    """Adapter for Kimi (Moonshot AI).

    Requires ``pip install novel-forge[openai]`` (uses openai SDK as HTTP client).

    Available models
    ----------------
    - ``kimi-k2.7-code-highspeed`` — 当前高速代码/智能体模型
    - ``kimi-k2.6``                — 当前通用模型（默认）
    - ``kimi-k2.5``                — 在役通用模型
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = _KIMI_BASE_URL,
        default_model: str = _KIMI_DEFAULT_MODEL,
        connect_timeout_s: float = 10.0,
        read_timeout_s: float = 1200.0,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            provider="kimi",
            default_model=default_model,
            connect_timeout_s=connect_timeout_s,
            read_timeout_s=read_timeout_s,
        )

    def _thinking_request_kwargs(
        self,
        request: ModelRequest,
        model_id: str,
    ) -> dict[str, Any]:
        model_key = model_id.strip().lower()
        if "thinking" in model_key:
            return {}
        if not model_key.startswith("kimi-k2."):
            return {}
        mode = normalize_thinking_mode(request.thinking_mode, thinking=request.thinking)
        return {
            "extra_body": {
                "thinking": {
                    "type": "enabled" if thinking_mode_enabled(mode) else "disabled"
                }
            }
        }

    def _request_with_supported_temperature(self, request: ModelRequest) -> ModelRequest:
        model_key = str(request.model_id or self._default_model or "").strip().lower()
        if not model_key.startswith("kimi-k2."):
            return request
        mode = normalize_thinking_mode(request.thinking_mode, thinking=request.thinking)
        temperature = 1.0 if thinking_mode_enabled(mode) else 0.6
        return request.model_copy(update={"temperature": temperature})

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return await super().complete(self._request_with_supported_temperature(request))

    async def stream(
        self,
        request: ModelRequest,
        *,
        on_final: Callable[[ModelResponse], None] | None = None,
    ) -> AsyncIterator[StreamChunk | str]:
        async for chunk in super().stream(
            self._request_with_supported_temperature(request),
            on_final=on_final,
        ):
            yield chunk
