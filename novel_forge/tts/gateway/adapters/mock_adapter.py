"""Mock TTS adapter for testing and development.

Returns synthetic audio data without calling external APIs.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from novel_forge.obs.logger import get_logger
from novel_forge.tts.gateway.base import TTSProviderAdapter
from novel_forge.tts.schemas import (
    TTSFeature,
    TTSProvider,
    TTSProviderCapabilities,
    TTSRequest,
    TTSResponse,
    VoiceCloneRequest,
    VoiceCloneResponse,
    VoiceCloneStatus,
    VoiceDesignRequest,
    VoiceDesignResponse,
)

_log = get_logger("tts.gateway.adapters.mock")

# Minimal valid MP3 frame (MPEG1 Layer3, 128kbps, 44100Hz, joint stereo)
# Frame header: 0xFFFB9004 + zero-padded side/main data to fill 417 bytes
_MOCK_MP3_FRAME_SIZE = 417  # MPEG1 L3 128kbps 44100Hz frame size
_mock_mp3_frame = bytearray(_MOCK_MP3_FRAME_SIZE)
_mock_mp3_frame[0] = 0xFF  # Sync byte 1
_mock_mp3_frame[1] = 0xFB  # Sync byte 2 + MPEG1 + Layer3 + no CRC
_mock_mp3_frame[2] = 0x90  # 128kbps, 44100Hz, no padding
_mock_mp3_frame[3] = 0x04  # Joint stereo
_MOCK_MP3_FRAME: bytes = bytes(_mock_mp3_frame)


class MockTTSAdapter(TTSProviderAdapter):
    """Mock TTS adapter for testing.

    Returns synthetic audio data and simulates API behavior
    without making actual network calls.
    """

    def __init__(
        self,
        *,
        latency_ms: float = 100.0,
        fail_rate: float = 0.0,
    ) -> None:
        self._latency_ms = latency_ms
        self._fail_rate = fail_rate
        self._cloned_voices: dict[str, dict[str, Any]] = {}
        self._designed_voices: dict[str, dict[str, Any]] = {}

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def provider_type(self) -> TTSProvider:
        return TTSProvider.MOCK

    @property
    def capabilities(self) -> TTSProviderCapabilities:
        return TTSProviderCapabilities(
            provider=TTSProvider.MOCK,
            voice_clone=True,
            voice_design=True,
            system_voice_catalog=True,
            local_reference_audio=True,
            synthesis_features={
                TTSFeature.SPEED,
                TTSFeature.VOLUME,
                TTSFeature.PITCH,
                TTSFeature.EMOTION,
            },
        )

    async def prepare_clone_source(self, reference: str) -> str:
        return reference

    async def synthesize(self, request: TTSRequest) -> TTSResponse:
        """Return mock audio data after simulated latency."""
        self._validate_request(request)

        # Simulate network latency
        if self._latency_ms > 0:
            await asyncio.sleep(self._latency_ms / 1000.0)

        # Simulate random failures
        if self._fail_rate > 0:
            import random

            if random.random() < self._fail_rate:
                raise RuntimeError("Mock TTS: simulated failure")

        # Generate mock audio (silence proportional to text length)
        text_len = len(request.text)
        # Approximate: 100ms per 10 Chinese characters
        duration_ms = max(500, int(text_len * 100))

        # Generate mock MP3 data
        audio_data = self._generate_mock_audio(text_len)

        return TTSResponse(
            audio_data=audio_data,
            duration_ms=duration_ms,
            model_id=request.model_id or "mock-tts-v1",
            voice_id=request.voice_id or "mock-voice",
            cost_usd=0.0,  # Mock is free
            latency_ms=self._latency_ms,
            content_type=f"audio/{request.output_format}",
        )

    async def clone_voice(self, request: VoiceCloneRequest) -> VoiceCloneResponse:
        """Simulate voice cloning."""
        if self._latency_ms > 0:
            await asyncio.sleep(self._latency_ms / 1000.0)

        expires_at = datetime.now(timezone.utc) + timedelta(days=7)

        # Store cloned voice
        self._cloned_voices[request.voice_id] = {
            "file_id": request.file_id,
            "clone_prompt": request.clone_prompt,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        _log.info("Mock voice clone: %s from file %s", request.voice_id, request.file_id)

        return VoiceCloneResponse(
            voice_id=request.voice_id,
            provider=TTSProvider.MOCK,
            status=VoiceCloneStatus.READY,
            expires_at=expires_at,
            message="Mock voice cloned successfully",
        )

    async def design_voice(self, request: VoiceDesignRequest) -> VoiceDesignResponse:
        """Simulate voice design."""
        if self._latency_ms > 0:
            await asyncio.sleep(self._latency_ms / 1000.0)

        # Generate a deterministic voice_id from description
        voice_id = f"mock-designed-{hashlib.md5(request.description.encode()).hexdigest()[:8]}"
        expires_at = datetime.now(timezone.utc) + timedelta(days=7)

        # Store designed voice
        self._designed_voices[voice_id] = {
            "description": request.description,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        _log.info("Mock voice design: %s from '%s'", voice_id, request.description[:50])

        return VoiceDesignResponse(
            voice_id=voice_id,
            provider=TTSProvider.MOCK,
            preview_audio_data=self._generate_mock_audio(100),
            status=VoiceCloneStatus.READY,
            expires_at=expires_at,
            message="Mock voice designed successfully",
        )

    async def list_system_voices(
        self,
        *,
        gender: str | None = None,
        language: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return mock system voices."""
        voices: list[dict[str, Any]] = [
            {"voice_id": "mock-male-1", "name": "Mock男声1", "gender": "male", "tags": ["青年"]},
            {"voice_id": "mock-male-2", "name": "Mock男声2", "gender": "male", "tags": ["中年"]},
            {
                "voice_id": "mock-female-1",
                "name": "Mock女声1",
                "gender": "female",
                "tags": ["少女"],
            },
            {
                "voice_id": "mock-female-2",
                "name": "Mock女声2",
                "gender": "female",
                "tags": ["知性"],
            },
            {
                "voice_id": "mock-neutral-1",
                "name": "Mock中性声",
                "gender": "neutral",
                "tags": ["旁白"],
            },
        ]

        if gender:
            voices = [v for v in voices if v.get("gender") == gender.lower()]

        return voices[:limit]

    async def health_check(self) -> bool:
        """Mock always healthy."""
        return True

    async def shutdown(self) -> None:
        """No resources to clean."""
        self._cloned_voices.clear()
        self._designed_voices.clear()

    # ─── Helpers ──────────────────────────────────────────────────────────

    def _generate_mock_audio(self, text_len: int) -> bytes:
        """Generate mock audio bytes.

        Returns valid MP3 frame data (silence) for testing.
        Duration is proportional to text length.
        """
        # Generate ~100ms of audio per 10 characters
        # At 44100Hz/128kbps, each frame is ~26ms, so ~4 frames per 100ms
        num_frames = max(4, text_len // 10 * 4)
        return _MOCK_MP3_FRAME * num_frames

    def get_cloned_voices(self) -> dict[str, dict[str, Any]]:
        """Return all cloned voices (for testing)."""
        return self._cloned_voices.copy()

    def get_designed_voices(self) -> dict[str, dict[str, Any]]:
        """Return all designed voices (for testing)."""
        return self._designed_voices.copy()
