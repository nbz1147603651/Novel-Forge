"""Xiaomi MiMo Token Plan adapter — https://mimo.mi.com."""

from __future__ import annotations

from typing import Any

from novel_forge.gateway.adapters.openai_compat import OpenAICompatibleAdapter
from novel_forge.gateway.reasoning import normalize_thinking_mode, thinking_mode_enabled
from novel_forge.gateway.types import ModelRequest

_MIMO_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"
_MIMO_DEFAULT_MODEL = "mimo-v2.5-pro"


class MiMoAdapter(OpenAICompatibleAdapter):
    """Adapter for Xiaomi MiMo's OpenAI-compatible Chat Completions API."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = _MIMO_BASE_URL,
        default_model: str = _MIMO_DEFAULT_MODEL,
        connect_timeout_s: float = 10.0,
        read_timeout_s: float = 1200.0,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            provider="mimo",
            default_model=default_model,
            connect_timeout_s=connect_timeout_s,
            read_timeout_s=read_timeout_s,
        )

    def _thinking_extra_body_for_request(self, enabled: bool) -> dict[str, Any] | None:
        # MiMo-V2.5-Pro and MiMo-V2.5 enable deep thinking by default. Always
        # send the explicit state so the route's thinking toggle remains authoritative.
        return {"thinking": {"type": "enabled" if enabled else "disabled"}}

    def _thinking_request_kwargs(
        self,
        request: ModelRequest,
        model_id: str,
    ) -> dict[str, Any]:
        del model_id
        mode = normalize_thinking_mode(request.thinking_mode, thinking=request.thinking)
        enabled = thinking_mode_enabled(mode)
        return {"extra_body": self._thinking_extra_body_for_request(enabled)}

    def _completion_token_limit_kwargs(self, max_tokens: int) -> dict[str, int]:
        return {"max_completion_tokens": max_tokens}
