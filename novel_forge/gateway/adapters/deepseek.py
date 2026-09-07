"""DeepSeek adapter — https://platform.deepseek.com."""

from __future__ import annotations

from typing import Any

from novel_forge.gateway.adapters.openai_compat import OpenAICompatibleAdapter
from novel_forge.gateway.reasoning import (
    normalize_thinking_mode,
    reasoning_effort_for_mode,
    thinking_mode_enabled,
)
from novel_forge.gateway.types import ModelRequest

# https://platform.deepseek.com/api-docs
_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
_DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"


class DeepSeekAdapter(OpenAICompatibleAdapter):
    """Adapter for DeepSeek API.

    Requires ``pip install novel-forge[openai]`` (uses openai SDK as HTTP client).

    Available models
    ----------------
    - ``deepseek-v4-flash``    — DeepSeek-V4-Flash, 1M context, 384K output
    - ``deepseek-v4-pro``      — DeepSeek-V4-Pro, 1M context, 384K output
    - ``deepseek-chat``        — DeepSeek-V3 (deprecated 2026-07-24)
    - ``deepseek-reasoner``    — DeepSeek-R1 (deprecated 2026-07-24)
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = _DEEPSEEK_BASE_URL,
        default_model: str = _DEEPSEEK_DEFAULT_MODEL,
        connect_timeout_s: float = 10.0,
        read_timeout_s: float = 1200.0,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            provider="deepseek",
            default_model=default_model,
            connect_timeout_s=connect_timeout_s,
            read_timeout_s=read_timeout_s,
        )

    def _thinking_request_kwargs(
        self,
        request: ModelRequest,
        model_id: str,
    ) -> dict[str, Any]:
        normalized_model = model_id.strip().lower()
        # Deprecated aliases select a fixed V4-Flash mode by model name.
        if normalized_model in {"deepseek-chat", "deepseek-reasoner"}:
            return {}

        mode = normalize_thinking_mode(request.thinking_mode, thinking=request.thinking)
        enabled = thinking_mode_enabled(mode)
        body: dict[str, Any] = {
            "thinking": {"type": "enabled" if enabled else "disabled"}
        }
        effort = reasoning_effort_for_mode(mode)
        if enabled:
            # DeepSeek exposes only two effective levels. Compatibility values
            # low/medium become high, and xhigh becomes max upstream.
            body["reasoning_effort"] = "max" if effort in {"xhigh", "max"} else "high"
        return {"extra_body": body}
