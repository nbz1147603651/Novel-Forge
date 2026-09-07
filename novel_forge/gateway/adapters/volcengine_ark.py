"""火山方舟 (Volcano Ark / Volcengine Ark) adapter — https://www.volcengine.com/product/ark.

Volcano Ark Agent Plan (智能体套餐) 使用专属 Base URL:
``https://ark.cn-beijing.volces.com/api/plan/v3``

官方文档明确说明："Agent Plan 专属 Base URL 中包含 /plan，请勿混用其他 Base URL"

Agent Plan 的 Model ID 使用点号格式（无日期后缀），例如 ``doubao-seed-2.0-pro``。
这与标准按量付费的连字符+日期格式（如 ``doubao-seed-2-0-pro-260215``）不同。

Thinking is enabled via ``extra_body={"thinking": {"type": "enabled"}}`` —
the Volcengine-native style, NOT the DashScope ``enable_thinking`` flag
(which Tongyi/DashScope uses).
"""

from __future__ import annotations

from typing import Any

from novel_forge.gateway.adapters.openai_compat import OpenAICompatibleAdapter
from novel_forge.gateway.types import ModelRequest, ModelResponse

# Agent Plan (智能体套餐) 专属 Base URL。
# 官方文档明确说明："Agent Plan 专属 Base URL 中包含 /plan，请勿混用其他 Base URL"
# 来源：https://www.volcengine.com/docs/82379/2375464
_VOLCENGINE_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/plan/v3"

# Agent Plan 的 Model ID 使用点号格式（无日期后缀），例如 doubao-seed-2.0-pro
# 这与标准按量付费的连字符+日期格式（如 doubao-seed-2-0-pro-260215）不同
_VOLCENGINE_ARK_DEFAULT_MODEL = "doubao-seed-2.0-pro"


class VolcengineArkAdapter(OpenAICompatibleAdapter):
    """Adapter for 火山方舟 (Volcano Ark / Volcengine Ark).

    Requires ``pip install novel-forge[openai]`` (uses openai SDK as HTTP client).

    Thinking support
    ----------------
    Volcano Ark uses its own thinking-on/off style. The provider sends
    ``extra_body={"thinking": {"type": "enabled"}}`` when ``request.thinking``
    is set. This is the Volcengine-native format (matches the Dify plugin and
    the official Ark Python SDK); it is **not** the DashScope
    ``enable_thinking`` flag used by Tongyi.

    Endpoint ID resolution
    -----------------------
    Volcengine Ark's API only accepts the canonical Model ID
    (``doubao-seed-2-0-pro-260215``, dash-separated + date stamp), not
    the friendly Model Name shown in the console
    (``doubao-seed-2.0-pro``, dot-separated, no date).  This adapter
    resolves the friendly form to the canonical form via
    :func:`novel_forge.gateway.profiles.canonical_model_id` so that
    users can paste either form into the desktop config and have it
    work.  Custom endpoint IDs of the form ``ep-xxxxxxxx-xxxxx`` pass
    through unchanged.

    Available models
    ----------------
    Doubao Seed 2.0 (Agent Plan defaults):
    - ``doubao-seed-2-0-pro-260215``     — flagship, 256K context
    - ``doubao-seed-2-0-lite-260215``    — lite (default), 256K context
    - ``doubao-seed-2-0-mini-260215``    — mini, 256K context
    - ``doubao-seed-2-0-code-preview-260215`` — code-optimized

    Doubao Seed 1.6 / 1.8:
    - ``doubao-seed-1-6-250615``
    - ``doubao-seed-1-6-flash-250715``
    - ``doubao-seed-1-6-thinking-250715``
    - ``doubao-seed-1-6-lite``
    - ``doubao-seed-1-6-vision``
    - ``doubao-seed-1-8``

    Doubao 1.5 (legacy):
    - ``doubao-1-5-pro-32k-250115``
    - ``doubao-1-5-pro-256k-250115``
    - ``doubao-1-5-lite-32k-250115``
    - ``doubao-1-5-thinking-pro-250415``
    - ``doubao-1-5-vision-pro-32k-250115``

    Doubao Pro / Lite (legacy aliases):
    - ``doubao-pro-32k`` / ``doubao-pro-128k`` / ``doubao-pro-256k``
    - ``doubao-lite-4k`` / ``doubao-lite-32k`` / ``doubao-lite-128k``

    DeepSeek on Ark:
    - ``deepseek-v3-250324`` / ``deepseek-v3-241226``
    - ``deepseek-r1-250528`` / ``deepseek-r1-250120``

    Custom endpoint IDs (the user creates the inference endpoint in the Ark
    console and passes the resulting ``ep-xxxxxxxx-xxxxx`` string as
    ``model_id``) are also supported.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = _VOLCENGINE_ARK_BASE_URL,
        default_model: str = _VOLCENGINE_ARK_DEFAULT_MODEL,
        connect_timeout_s: float = 10.0,
        read_timeout_s: float = 1200.0,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            provider="volcengine_ark",
            default_model=default_model,
            connect_timeout_s=connect_timeout_s,
            read_timeout_s=read_timeout_s,
        )

    def _get_client(self) -> Any:
        """Override parent to bypass the unconditional ``/v1`` suffix.

        The base :class:`OpenAICompatibleAdapter` appends ``/v1`` to any
        ``base_url`` that does not end in ``/v1``. Volcano Ark Agent Plan's
        base URL is ``https://ark.cn-beijing.volces.com/api/plan/v3``, which
        ends in ``/v3`` — the parent class therefore mangles it into
        ``/api/plan/v3/v1``, producing 404 on every chat-completions call.

        This override builds the AsyncOpenAI client with the base URL
        unchanged. The client is still lazy-initialized, reused across calls,
        and guarded by the same lock the parent uses, so thread-safety and
        timeout layering behave identically to other OpenAI-compatible
        adapters.
        """
        if self._client is not None:
            return self._client
        import httpx
        import openai  # noqa: F811

        with self._client_lock:
            if self._client is not None:
                return self._client
            sdk_timeout = httpx.Timeout(
                connect=self._connect_timeout_s,
                read=self._read_timeout_s,
                write=self._write_timeout_s,
                pool=10.0,
            )
            self._client = openai.AsyncOpenAI(
                api_key=self._api_key,
                # No ``/v1`` suffix: Ark Agent Plan's URL is already
                # versioned (``/api/plan/v3``); the real endpoint is
                # ``/api/plan/v3/chat/completions``, not
                # ``/api/plan/v3/v1/chat/completions``.
                base_url=self._base_url,
                timeout=sdk_timeout,
                max_retries=0,
            )
            return self._client

    def _thinking_extra_body(self) -> dict[str, Any]:
        """Volcano-Ark-native thinking payload.

        Overrides the Tongyi ``enable_thinking`` default in
        :class:`OpenAICompatibleAdapter` with the Volcengine-native
        ``{"thinking": {"type": "enabled"}}`` payload, which the Dify plugin
        and the official ``volcenginesdkarkruntime`` SDK use.
        """
        return {"thinking": {"type": "enabled"}}

    def _thinking_extra_body_for_request(self, enabled: bool) -> dict[str, Any] | None:
        return {"thinking": {"type": "enabled" if enabled else "disabled"}}

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Forward to the base adapter.

        Agent Plan 使用点号格式的 Model ID（如 ``doubao-seed-2.0-pro``），
        直接透传给 API 即可，无需规范化。
        """
        return await super().complete(request)

    async def health_check(self) -> bool:
        """Check connectivity through the normal Ark completion path."""
        return await super().health_check()
