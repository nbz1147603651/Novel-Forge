"""通义千问 (Tongyi Qwen) adapter — https://dashscope.aliyuncs.com."""

from __future__ import annotations

from novel_forge.gateway.adapters.openai_compat import OpenAICompatibleAdapter

# 阿里云 DashScope 提供 OpenAI 兼容端点
# https://help.aliyun.com/zh/model-studio/developer-reference/compatibility-of-openai-with-dashscope
_TONGYI_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class TongyiAdapter(OpenAICompatibleAdapter):
    """Adapter for 通义千问 (Alibaba Tongyi Qwen) via DashScope.

    Requires ``pip install novel-forge[openai]`` (uses openai SDK as HTTP client).

    Available models
    ----------------
    - ``qwen-max``           — 旗舰级，适合复杂创作任务
    - ``qwen-plus``          — 性价比高，适合多数任务
    - ``qwen-turbo``         — 速度快、成本低
    - ``qwen-long``          — 支持超长上下文（100 万 token）
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = _TONGYI_BASE_URL,
        default_model: str = "",
        connect_timeout_s: float = 10.0,
        read_timeout_s: float = 1200.0,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            provider="tongyi",
            default_model=default_model,
            connect_timeout_s=connect_timeout_s,
            read_timeout_s=read_timeout_s,
        )
