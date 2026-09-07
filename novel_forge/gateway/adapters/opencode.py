"""OpenCode Go adapter - https://opencode.ai/zen/go/v1.

OpenCode Go 是 OpenCode 团队提供的开源模型聚合订阅服务
(首月 $5,之后每月 $10),通过 opencode.ai/auth 获取 API key。
OpenAI 兼容端点,聚合 21 个开源编码模型(Grok / GLM / Kimi / MiMo /
MiniMax / Qwen / DeepSeek 系列)。详见:
https://opencode.ai/docs/zh-tw/go/
"""

from __future__ import annotations

from novel_forge.gateway.adapters.openai_compat import OpenAICompatibleAdapter

# models.dev 核验(2026-07-18):@ai-sdk/openai-compatible 协议
_OPENCODE_BASE_URL = "https://opencode.ai/zen/go/v1"


class OpenCodeAdapter(OpenAICompatibleAdapter):
    """Adapter for OpenCode Go (https://opencode.ai/docs/zh-tw/go/).

    Requires ``pip install novel-forge[openai]`` (uses openai SDK as HTTP client).

    思考模式透传:不在请求里塞 ``extra_body``,由 OpenCode Go 后端按各
    上游模型自身默认行为处理。原因:OpenCode Go 是聚合网关,21 个模型
    来自不同上游(Grok / GLM / Kimi / DeepSeek / MiMo / MiniMax / Qwen),
    各家思考控制参数名不一(enable_thinking / thinking / reasoning_effort
    等),网关未必统一转发;统一塞参反而可能对部分模型被忽略或报错。
    若未来确认 Go 网关支持统一的思考参数,可在子类里重写
    ``_thinking_request_kwargs`` 按模型族分别处理。

    Available models
    ----------------
    - ``deepseek-v4-flash``  - DeepSeek V4 Flash, 1M context (默认)
    - ``deepseek-v4-pro``    - DeepSeek V4 Pro, 1M context
    - ``glm-5.2``            - GLM-5.2, 1M context
    - ``glm-5.1``            - GLM-5.1, 202K context
    - ``glm-5``              - GLM-5, 202K context
    - ``grok-4.5``           - Grok 4.5, 500K context
    - ``kimi-k3``            - Kimi K3, 1M context
    - ``kimi-k2.7-code``     - Kimi K2.7 Code, 262K context
    - ``kimi-k2.6``          - Kimi K2.6, 262K context
    - ``kimi-k2.5``          - Kimi K2.5, 262K context
    - ``mimo-v2.5-pro``      - MiMo V2.5 Pro, 1M context
    - ``mimo-v2.5``          - MiMo V2.5, 1M context
    - ``mimo-v2-pro``        - MiMo V2 Pro, 1M context
    - ``mimo-v2-omni``       - MiMo V2 Omni, 262K context
    - ``minimax-m3``         - MiniMax M3, 1M context
    - ``minimax-m2.7``       - MiniMax M2.7, 204K context
    - ``minimax-m2.5``       - MiniMax M2.5, 204K context
    - ``qwen3.7-max``        - Qwen3.7 Max, 1M context
    - ``qwen3.7-plus``       - Qwen3.7 Plus, 1M context
    - ``qwen3.6-plus``       - Qwen3.6 Plus, 1M context
    - ``qwen3.5-plus``       - Qwen3.5 Plus, 262K context
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = _OPENCODE_BASE_URL,
        default_model: str = "deepseek-v4-flash",
        connect_timeout_s: float = 10.0,
        read_timeout_s: float = 1200.0,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            provider="opencode",
            default_model=default_model,
            connect_timeout_s=connect_timeout_s,
            read_timeout_s=read_timeout_s,
        )
