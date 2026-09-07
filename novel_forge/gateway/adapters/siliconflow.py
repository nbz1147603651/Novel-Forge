"""硅基流动 (SiliconFlow) adapter — https://api.siliconflow.cn."""

from __future__ import annotations

from typing import Any

from novel_forge.gateway.adapters.openai_compat import OpenAICompatibleAdapter
from novel_forge.gateway.reasoning import normalize_thinking_mode, thinking_mode_enabled
from novel_forge.gateway.types import ModelRequest

# https://api-docs.siliconflow.cn/docs/api/chat-completions-post
_SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"


class SiliconFlowAdapter(OpenAICompatibleAdapter):
    """Adapter for SiliconFlow API (硅基流动).

    Requires ``pip install novel-forge[openai]`` (uses openai SDK as HTTP client).

    Available models
    ----------------
    DeepSeek 系列:
    - ``deepseek-ai/DeepSeek-V4-Flash``  — V4 Flash, 1M context
    - ``deepseek-ai/DeepSeek-V4-Pro``    — V4 Pro, 1M context
    - ``deepseek-ai/DeepSeek-V3.2``      — V3.2
    - ``deepseek-ai/DeepSeek-V3``        — V3
    - ``deepseek-ai/DeepSeek-R1``        — R1 推理模型

    Qwen 系列:
    - ``Qwen/Qwen3-235B-A22B``           — Qwen3 旗舰
    - ``Qwen/Qwen3-32B``                 — Qwen3 32B
    - ``Qwen/Qwen3-8B``                  — Qwen3 8B

    GLM 系列:
    - ``THUDM/glm-4-9b-chat``            — GLM-4

    Llama 系列:
    - ``meta-llama/Meta-Llama-3.1-70B-Instruct``
    - ``meta-llama/Meta-Llama-3.1-8B-Instruct``
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = _SILICONFLOW_BASE_URL,
        default_model: str = "deepseek-ai/DeepSeek-V3.2",
        connect_timeout_s: float = 10.0,
        read_timeout_s: float = 1200.0,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            provider="siliconflow",
            default_model=default_model,
            connect_timeout_s=connect_timeout_s,
            read_timeout_s=read_timeout_s,
        )

    def _thinking_request_kwargs(
        self,
        request: ModelRequest,
        model_id: str,
    ) -> dict[str, Any]:
        del model_id
        mode = normalize_thinking_mode(request.thinking_mode, thinking=request.thinking)
        return {"extra_body": {"enable_thinking": thinking_mode_enabled(mode)}}
