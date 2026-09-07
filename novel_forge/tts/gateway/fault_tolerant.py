"""Adapter decorator applying one failure policy to every provider operation."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.tts.gateway.base import TTSProviderAdapter
from novel_forge.tts.gateway.failures import TTSProviderFailure, TTSProviderFailurePolicy
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


class FailureManagedTTSAdapter(TTSProviderAdapter):
    """Normalize clone, design, catalog and synthesis failures consistently."""

    def __init__(
        self,
        adapter: TTSProviderAdapter,
        policy: TTSProviderFailurePolicy,
    ) -> None:
        self._adapter = adapter
        self.failure_policy = policy

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
        return await self.failure_policy.execute(self._adapter.discover_capabilities)

    async def prepare_clone_source(self, reference: str) -> str:
        return await self.failure_policy.execute(
            lambda: self._adapter.prepare_clone_source(reference)
        )

    async def synthesize(self, request: TTSRequest) -> TTSResponse:
        async def _operation() -> TTSResponse:
            response = await self._adapter.synthesize(request)
            if not response.audio_data:
                raise ModelGatewayError(
                    "TTS provider returned empty audio data",
                    is_transient=True,
                )
            return response

        return await self.failure_policy.execute(_operation)

    async def synthesize_streaming(
        self,
        request: TTSRequest,
        *,
        ws_url: str = "",
        on_chunk: Callable[[bytes], Awaitable[None] | None] | None = None,
    ) -> TTSResponse:
        """Streaming synthesis with the same failure policy as ``synthesize``."""
        streaming_method = getattr(self._adapter, "synthesize_streaming", None)
        if not callable(streaming_method):
            raise ModelGatewayError(
                f"{self._adapter.provider_name} does not support streaming synthesis"
            )

        async def _operation() -> TTSResponse:
            response = await streaming_method(request, ws_url=ws_url, on_chunk=on_chunk)
            if not response.audio_data:
                raise ModelGatewayError(
                    "TTS provider returned empty audio data",
                    is_transient=True,
                )
            return response

        return await self.failure_policy.execute(_operation)

    async def clone_voice(self, request: VoiceCloneRequest) -> VoiceCloneResponse:
        return await self.failure_policy.execute(lambda: self._adapter.clone_voice(request))

    async def design_voice(self, request: VoiceDesignRequest) -> VoiceDesignResponse:
        return await self.failure_policy.execute(lambda: self._adapter.design_voice(request))

    async def list_system_voices(
        self,
        *,
        gender: str | None = None,
        language: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return await self.failure_policy.execute(
            lambda: self._adapter.list_system_voices(
                gender=gender,
                language=language,
                limit=limit,
            )
        )

    async def health_check(self) -> bool:
        async def _operation() -> bool:
            healthy = await self._adapter.health_check()
            if not healthy:
                raise ModelGatewayError(
                    self._adapter.last_health_error or "TTS provider health check failed",
                    is_transient=True,
                )
            return True

        try:
            return await self.failure_policy.execute(_operation)
        except TTSProviderFailure:
            return False

    async def shutdown(self) -> None:
        await self._adapter.shutdown()
