"""Abstract base for TTS provider adapters.

This mirrors the LLM ProviderAdapter pattern but handles text→audio I/O
instead of text→text. Each provider implements voice cloning, synthesis,
and optional voice design operations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

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


class TTSProviderAdapter(ABC):
    """Interface that every TTS provider must implement.

    This is parallel to the LLM ProviderAdapter but handles audio output.
    Subclasses must implement:
    - provider_name: identifier
    - synthesize: text → audio
    - clone_voice: reference audio → voice_id
    - design_voice (optional): text description → new voice
    - health_check: connectivity test
    - shutdown: cleanup
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider identifier, e.g. 'minimax', 'mock'."""

    @property
    def provider_type(self) -> TTSProvider:
        """Provider enum value. Override if not MOCK."""
        return TTSProvider.MOCK

    @property
    def capabilities(self) -> TTSProviderCapabilities:
        """Return explicit provider capabilities for safe UI feature gating."""
        return TTSProviderCapabilities(provider=self.provider_type)

    async def discover_capabilities(self) -> TTSProviderCapabilities:
        """Discover runtime capabilities; static adapters return their declaration."""
        return self.capabilities

    async def prepare_clone_source(self, reference: str) -> str:
        """Convert a local reference into a provider file id when supported.

        Non-local references are treated as already uploaded provider ids.
        Providers accepting local files must override this method.
        """
        from pathlib import Path

        if Path(reference).expanduser().is_file():
            raise ValueError(f"{self.provider_name} requires a provider-uploaded reference file id")
        return reference

    @abstractmethod
    async def synthesize(self, request: TTSRequest) -> TTSResponse:
        """Synthesize speech from text.

        Args:
            request: TTS request with text, voice_id, and parameters.

        Returns:
            TTSResponse with audio data or saved path.

        Raises:
            ModelGatewayError: On provider communication failure.
            TimeoutException: On request timeout.
        """

    async def clone_voice(self, request: VoiceCloneRequest) -> VoiceCloneResponse:
        """Clone a voice from reference audio.

        Default implementation raises NotImplementedError.
        Providers that support voice cloning must override this.

        Args:
            request: Voice clone request with file_id and voice_id.

        Returns:
            VoiceCloneResponse with cloned voice_id.
        """
        raise NotImplementedError(f"{self.provider_name} does not support voice cloning")

    async def design_voice(self, request: VoiceDesignRequest) -> VoiceDesignResponse:
        """Design a new voice from text description.

        Default implementation raises NotImplementedError.
        Providers that support voice design must override this.

        Args:
            request: Voice design request with description text.

        Returns:
            VoiceDesignResponse with new voice_id.
        """
        raise NotImplementedError(f"{self.provider_name} does not support voice design")

    async def list_system_voices(
        self,
        *,
        gender: str | None = None,
        language: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List available system voices.

        Default returns empty list. Override for providers with voice catalogs.

        Args:
            gender: Filter by gender ('male', 'female', 'neutral').
            language: Filter by language code.
            limit: Maximum voices to return.

        Returns:
            List of voice dicts with 'voice_id', 'name', 'gender', 'tags'.
        """
        return []

    @property
    def last_health_error(self) -> str:
        """Last health-check error message for diagnostics."""
        return ""

    async def health_check(self) -> bool:
        """Return True if the provider is reachable."""
        return True

    async def shutdown(self) -> None:
        """Release any async resources held by this adapter."""
        return None

    async def aclose(self) -> None:
        """Alias for shutdown to support generic async cleanup flows."""
        await self.shutdown()

    # ─── Utility methods ──────────────────────────────────────────────────

    def _validate_request(self, request: TTSRequest) -> None:
        """Validate request parameters before sending.

        Subclasses can override for provider-specific validation.
        """
        if not request.text or not request.text.strip():
            raise ValueError("TTS request text cannot be empty")
        if len(request.text) > 1_000_000:
            raise ValueError(f"TTS text too long: {len(request.text)} > 1,000,000 characters")
