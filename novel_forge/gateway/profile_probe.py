"""Provider connectivity probe shared by non-Qt desktop surfaces."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import time
from dataclasses import dataclass
from typing import Any, Protocol, cast

from novel_forge.gateway.embedding_config import is_embedding_model
from novel_forge.gateway.profiles import (
    TONGYI_CODING_PLAN_BASE_URL,
    TONGYI_TOKEN_PLAN_BASE_URL,
    ModelProfile,
    get_model_capabilities,
)


class _HealthCheckAdapter(Protocol):
    last_health_error: str

    async def health_check(self) -> bool: ...


_ADAPTERS: dict[str, tuple[str, str]] = {
    "tongyi": ("novel_forge.gateway.adapters.tongyi", "TongyiAdapter"),
    "tongyi_coding": ("novel_forge.gateway.adapters.tongyi", "TongyiAdapter"),
    "tongyi_token_plan": ("novel_forge.gateway.adapters.tongyi", "TongyiAdapter"),
    "deepseek": ("novel_forge.gateway.adapters.deepseek", "DeepSeekAdapter"),
    "openai": ("novel_forge.gateway.adapters.openai", "OpenAIAdapter"),
    "anthropic": ("novel_forge.gateway.adapters.anthropic", "AnthropicAdapter"),
    "kimi": ("novel_forge.gateway.adapters.kimi", "KimiAdapter"),
    "mimo": ("novel_forge.gateway.adapters.mimo", "MiMoAdapter"),
    "tencent": ("novel_forge.gateway.adapters.tencent_hunyuan", "TencentHunyuanAdapter"),
    "ollama": ("novel_forge.gateway.adapters.ollama", "OllamaAdapter"),
    "minimax": ("novel_forge.gateway.adapters.minimax", "MiniMaxAdapter"),
    "siliconflow": ("novel_forge.gateway.adapters.siliconflow", "SiliconFlowAdapter"),
    "volcengine_ark": (
        "novel_forge.gateway.adapters.volcengine_ark",
        "VolcengineArkAdapter",
    ),
    "opencode": ("novel_forge.gateway.adapters.opencode", "OpenCodeAdapter"),
}


@dataclass(frozen=True)
class ModelProfileProbeResult:
    ok: bool
    detail: str
    latency_ms: int | None
    supports_thinking: bool
    supports_multi_turn: bool


async def probe_model_profile(
    profile: ModelProfile,
    *,
    timeout_s: float = 18.0,
) -> ModelProfileProbeResult:
    """Run the same provider/embedding health check expected by desktop settings."""

    supports_thinking, supports_multi_turn = get_model_capabilities(
        profile.provider,
        profile.model_id,
    )
    if not profile.is_key_configured:
        return ModelProfileProbeResult(
            ok=False,
            detail="未配置 API Key",
            latency_ms=None,
            supports_thinking=False,
            supports_multi_turn=False,
        )

    started = time.monotonic()
    try:
        if is_embedding_model(profile.provider, profile.model_id):
            await asyncio.wait_for(_probe_embedding(profile), timeout=timeout_s)
            latency_ms = int((time.monotonic() - started) * 1000)
            return ModelProfileProbeResult(
                ok=True,
                detail=f"嵌入连通 {latency_ms}ms",
                latency_ms=latency_ms,
                supports_thinking=False,
                supports_multi_turn=False,
            )

        adapter = _make_adapter(profile)
        try:
            ok = await asyncio.wait_for(adapter.health_check(), timeout=timeout_s)
            detail = adapter.last_health_error or "连通性检测失败"
        finally:
            await _shutdown_adapter(adapter)
        latency_ms = int((time.monotonic() - started) * 1000)
        if not ok:
            return ModelProfileProbeResult(
                ok=False,
                detail=_friendly_error(detail, timeout_s=timeout_s),
                latency_ms=latency_ms,
                supports_thinking=False,
                supports_multi_turn=False,
            )
        return ModelProfileProbeResult(
            ok=True,
            detail=f"连通 {latency_ms}ms",
            latency_ms=latency_ms,
            supports_thinking=supports_thinking,
            supports_multi_turn=supports_multi_turn,
        )
    except TimeoutError:
        detail = f"连接超时：供应商未在 {int(timeout_s)}s 内返回"
    except Exception as exc:  # noqa: BLE001 - provider SDKs expose heterogeneous errors
        detail = _friendly_error(str(exc).strip() or type(exc).__name__, timeout_s=timeout_s)
    return ModelProfileProbeResult(
        ok=False,
        detail=detail,
        latency_ms=int((time.monotonic() - started) * 1000),
        supports_thinking=False,
        supports_multi_turn=False,
    )


async def _probe_embedding(profile: ModelProfile) -> None:
    from novel_forge.gateway.embedding import EmbeddingService

    service = EmbeddingService(
        provider=profile.provider,
        api_key=profile.api_key or None,
        model=profile.model_id,
        base_url=profile.base_url or None,
    )
    try:
        result = await service.generate_embedding("ping")
        if not isinstance(result.embedding, list) or not result.embedding:
            raise RuntimeError("嵌入返回为空向量")
    finally:
        await service.aclose()


async def _shutdown_adapter(adapter: object) -> None:
    close_hook = getattr(adapter, "shutdown", None)
    if not callable(close_hook):
        close_hook = getattr(adapter, "aclose", None)
    if not callable(close_hook):
        return
    result = close_hook()
    if inspect.isawaitable(result):
        await result


def _make_adapter(profile: ModelProfile) -> _HealthCheckAdapter:
    if profile.provider == "custom":
        if not profile.base_url.strip():
            raise ValueError("自定义供应商需要填写接口地址")
        from novel_forge.gateway.adapters.openai_compat import OpenAICompatibleAdapter

        return cast(
            _HealthCheckAdapter,
            OpenAICompatibleAdapter(
                profile.api_key,
                profile.base_url,
                "custom",
                profile.model_id,
            ),
        )
    entry = _ADAPTERS.get(profile.provider)
    if entry is None:
        raise ValueError(f"未知供应商: {profile.provider}")
    module_path, class_name = entry
    adapter_class = getattr(importlib.import_module(module_path), class_name)
    kwargs: dict[str, Any] = {}
    tongyi_plan_default_urls = {
        "tongyi_coding": TONGYI_CODING_PLAN_BASE_URL,
        "tongyi_token_plan": TONGYI_TOKEN_PLAN_BASE_URL,
    }
    if profile.provider in tongyi_plan_default_urls:
        kwargs["base_url"] = profile.base_url or tongyi_plan_default_urls[profile.provider]
    elif profile.base_url:
        kwargs["base_url"] = profile.base_url
    if profile.model_id:
        kwargs["default_model"] = profile.model_id
    if profile.provider == "ollama":
        return cast(_HealthCheckAdapter, adapter_class(**kwargs))
    return cast(_HealthCheckAdapter, adapter_class(profile.api_key, **kwargs))


def _friendly_error(detail: str, *, timeout_s: float) -> str:
    message = detail.strip() or "连通性检测失败"
    lowered = message.lower()
    if "model not found" in lowered or "model_not_found" in lowered:
        message = "模型不存在或未开通：请检查模型 ID 与账号权限"
    elif "401" in lowered or "unauthorized" in lowered or "invalid api key" in lowered:
        message = "鉴权失败：请检查 API Key"
    elif "timeout" in lowered or "timed out" in lowered:
        message = f"连接超时：供应商未在 {int(timeout_s)}s 内返回"
    elif "connection refused" in lowered or "cannot connect" in lowered:
        message = "无法连接服务：请检查接口地址与网络"
    return message if len(message) <= 200 else f"{message[:197]}..."
