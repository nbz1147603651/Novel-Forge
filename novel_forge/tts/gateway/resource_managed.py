"""Resource admission wrapper for heavyweight local TTS adapters."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from novel_forge.core.local_model_resources import (
    LocalMemoryClass,
    LocalResourcePriority,
    LocalResourceRequest,
    configure_local_model_resources,
)
from novel_forge.tts.gateway.base import TTSProviderAdapter
from novel_forge.tts.schemas import (
    TTSProvider,
    TTSProviderCapabilities,
    TTSRequest,
    TTSResponse,
    VoiceCloneRequest,
    VoiceCloneResponse,
    VoiceDesignRequest,
    VoiceDesignResponse,
)

_LOCAL_TTS_PROVIDERS = {
    TTSProvider.LOCAL,
    TTSProvider.QWEN3,
    TTSProvider.COSYVOICE,
    TTSProvider.OPENVOICE,
}


def is_local_tts_provider(provider: TTSProvider) -> bool:
    return provider in _LOCAL_TTS_PROVIDERS


def _memory_class(model_id: str, provider: TTSProvider) -> LocalMemoryClass:
    normalized = model_id.lower()
    if "1.7b" in normalized or "voice-design" in normalized or "stable" in normalized:
        return LocalMemoryClass.HIGH
    if "0.6b" in normalized:
        return LocalMemoryClass.MEDIUM
    if provider in {TTSProvider.LOCAL, TTSProvider.COSYVOICE, TTSProvider.OPENVOICE}:
        return LocalMemoryClass.MEDIUM
    return LocalMemoryClass.HIGH


def _priority(metadata: dict[str, Any]) -> LocalResourcePriority:
    raw = str(metadata.get("resource_priority") or "foreground").strip().lower()
    return {
        "interactive": LocalResourcePriority.INTERACTIVE,
        "foreground": LocalResourcePriority.FOREGROUND,
        "background": LocalResourcePriority.BACKGROUND,
        "maintenance": LocalResourcePriority.MAINTENANCE,
    }.get(raw, LocalResourcePriority.FOREGROUND)


class ResourceManagedTTSAdapter(TTSProviderAdapter):
    """Delegate transport details while coordinating local accelerator use."""

    def __init__(self, adapter: TTSProviderAdapter, settings: object) -> None:
        self._adapter = adapter
        self._broker = configure_local_model_resources(settings)
        self._timeout_s = float(
            getattr(settings, "local_model_resource_wait_timeout_s", 900.0) or 900.0
        )

    @property
    def provider_name(self) -> str:
        return self._adapter.provider_name

    @property
    def provider_type(self) -> TTSProvider:
        return self._adapter.provider_type

    @property
    def capabilities(self) -> TTSProviderCapabilities:
        return self._adapter.capabilities

    @property
    def last_health_error(self) -> str:
        return self._adapter.last_health_error

    async def discover_capabilities(self) -> TTSProviderCapabilities:
        return await self._adapter.discover_capabilities()

    async def prepare_clone_source(self, reference: str) -> str:
        return await self._adapter.prepare_clone_source(reference)

    async def synthesize(self, request: TTSRequest) -> TTSResponse:
        resource_request = LocalResourceRequest(
            workload="tts_synthesis",
            label=str(request.metadata.get("resource_label") or f"{self.provider_name} 语音合成"),
            memory_class=_memory_class(request.model_id, self.provider_type),
            accelerator=True,
            cpu_heavy=True,
            priority=_priority(request.metadata),
            timeout_s=self._timeout_s,
        )
        async with self._broker.lease(resource_request):
            return await self._adapter.synthesize(request)

    async def synthesize_streaming(
        self,
        request: TTSRequest,
        *,
        ws_url: str = "",
        on_chunk: Callable[[bytes], Awaitable[None] | None] | None = None,
    ) -> TTSResponse:
        """Streaming synthesis under the same local resource admission policy."""
        streaming_method = getattr(self._adapter, "synthesize_streaming", None)
        if not callable(streaming_method):
            return await self._adapter.synthesize(request)
        resource_request = LocalResourceRequest(
            workload="tts_synthesis",
            label=str(request.metadata.get("resource_label") or f"{self.provider_name} 语音合成"),
            memory_class=_memory_class(request.model_id, self.provider_type),
            accelerator=True,
            cpu_heavy=True,
            priority=_priority(request.metadata),
            timeout_s=self._timeout_s,
        )
        async with self._broker.lease(resource_request):
            return await streaming_method(request, ws_url=ws_url, on_chunk=on_chunk)

    async def clone_voice(self, request: VoiceCloneRequest) -> VoiceCloneResponse:
        resource_request = LocalResourceRequest(
            workload="voice_clone",
            label=f"{self.provider_name} 音色克隆",
            memory_class=LocalMemoryClass.HIGH,
            accelerator=True,
            cpu_heavy=True,
            priority=LocalResourcePriority.FOREGROUND,
            timeout_s=self._timeout_s,
        )
        async with self._broker.lease(resource_request):
            return await self._adapter.clone_voice(request)

    async def design_voice(self, request: VoiceDesignRequest) -> VoiceDesignResponse:
        resource_request = LocalResourceRequest(
            workload="voice_design",
            label=f"{self.provider_name} AI 音色设计",
            memory_class=LocalMemoryClass.HIGH,
            accelerator=True,
            cpu_heavy=True,
            priority=LocalResourcePriority.FOREGROUND,
            timeout_s=self._timeout_s,
        )
        async with self._broker.lease(resource_request):
            return await self._adapter.design_voice(request)

    async def list_system_voices(
        self,
        *,
        gender: str | None = None,
        language: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return await self._adapter.list_system_voices(
            gender=gender,
            language=language,
            limit=limit,
        )

    async def health_check(self) -> bool:
        return await self._adapter.health_check()

    async def shutdown(self) -> None:
        await self._adapter.shutdown()

    @property
    def wrapped_adapter(self) -> TTSProviderAdapter:
        """Expose the transport for diagnostics without bypassing normal calls."""
        return self._adapter

    @property
    def failure_policy(self) -> Any:
        """Expose an inner provider policy so callers do not wrap it twice."""
        return getattr(self._adapter, "failure_policy", None)


__all__ = ["ResourceManagedTTSAdapter", "is_local_tts_provider"]
