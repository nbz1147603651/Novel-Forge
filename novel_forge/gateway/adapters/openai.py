"""OpenAI provider adapter (stub — requires openai SDK)."""
from __future__ import annotations

from typing import Any

from novel_forge.gateway.adapters.openai_compat import OpenAICompatibleAdapter
from novel_forge.gateway.reasoning import normalize_thinking_mode, reasoning_effort_for_mode
from novel_forge.gateway.types import ModelRequest

_OPENAI_BASE_URL = "https://api.openai.com"
_OPENAI_DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIAdapter(OpenAICompatibleAdapter):
    """Adapter for OpenAI API. Requires ``pip install novel-forge[openai]``."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "",
        default_model: str = _OPENAI_DEFAULT_MODEL,
        connect_timeout_s: float = 10.0,
        read_timeout_s: float = 1200.0,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url.strip() or _OPENAI_BASE_URL,
            provider="openai",
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
        if not (model_key.startswith("gpt-5") or model_key.startswith(("o1", "o3", "o4"))):
            return {}
        mode = normalize_thinking_mode(request.thinking_mode, thinking=request.thinking)
        effort = reasoning_effort_for_mode(mode)
        return {"extra_body": {"reasoning_effort": effort or "none"}}
