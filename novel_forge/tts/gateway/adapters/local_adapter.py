"""Local TTS adapter — OpenAI-compatible API for local models.

Supports local deployment models:
- CosyVoice local API
- Fish Speech
- GPT-SoVITS
- Any OpenAI /v1/audio/speech compatible server
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

import httpx

from novel_forge.core.exceptions import ModelGatewayError
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

_log = get_logger("tts.gateway.adapters.local")

_DEFAULT_BASE_URL = "http://localhost:8000/v1"


class LocalTTSAdapter(TTSProviderAdapter):
    """Local TTS provider adapter using OpenAI-compatible API.

    Supports any local TTS server that implements:
    - POST /v1/audio/speech (synthesis)
    - GET /v1/models (voice listing)

    Compatible with: CosyVoice local, Fish Speech, GPT-SoVITS, etc.
    """

    def __init__(
        self,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        api_key: str = "",
        default_model: str = "default",
        connect_timeout_s: float = 5.0,
        read_timeout_s: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_model = default_model
        self._last_health_error = ""
        self._capabilities_cache: TTSProviderCapabilities | None = None

        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(
                connect=connect_timeout_s,
                read=read_timeout_s,
                write=30.0,
                pool=10.0,
            ),
            headers=headers,
        )

    @property
    def provider_name(self) -> str:
        return "local"

    @property
    def provider_type(self) -> TTSProvider:
        return TTSProvider.LOCAL

    @property
    def capabilities(self) -> TTSProviderCapabilities:
        return self._capabilities_cache or TTSProviderCapabilities(
            provider=TTSProvider.LOCAL,
            system_voice_catalog=True,
            synthesis_features={TTSFeature.SPEED},
        )

    async def discover_capabilities(self) -> TTSProviderCapabilities:
        """Negotiate optional local features without binding to one engine."""
        if self._capabilities_cache is not None:
            return self._capabilities_cache
        try:
            response = await self._client.get("/capabilities")
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                self._capabilities_cache = TTSProviderCapabilities(
                    provider=TTSProvider.LOCAL,
                    synthesis=bool(payload.get("synthesis", True)),
                    voice_clone=bool(payload.get("voice_clone", False)),
                    voice_design=bool(payload.get("voice_design", False)),
                    system_voice_catalog=bool(payload.get("system_voice_catalog", True)),
                    local_reference_audio=bool(payload.get("local_reference_audio", False)),
                    synthesis_features={
                        TTSFeature(str(value))
                        for value in payload.get("synthesis_features", [])
                        if str(value) in {item.value for item in TTSFeature}
                    },
                )
                return self._capabilities_cache
        except Exception:
            pass
        self._capabilities_cache = TTSProviderCapabilities(
            provider=TTSProvider.LOCAL,
            system_voice_catalog=True,
        )
        return self._capabilities_cache

    async def prepare_clone_source(self, reference: str) -> str:
        return reference

    @property
    def last_health_error(self) -> str:
        return self._last_health_error

    # ─── Voice Synthesis ──────────────────────────────────────────────────

    async def synthesize(self, request: TTSRequest) -> TTSResponse:
        """Synthesize speech using OpenAI-compatible /v1/audio/speech endpoint."""
        self._validate_request(request)

        model_id = request.model_id or self._default_model
        voice_id = request.voice_id or "default"

        payload: dict[str, Any] = {
            "model": model_id,
            "input": request.text,
            "voice": voice_id,
            "speed": round(request.speed, 2),
            "response_format": request.output_format,
        }

        start = time.monotonic()

        try:
            response = await self._client.post("/audio/speech", json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self._last_health_error = str(exc)
            _log.error("Local TTS error: %s", exc.response.text)
            raise ModelGatewayError(f"Local TTS synthesis failed: {exc}") from exc
        except httpx.RequestError as exc:
            self._last_health_error = str(exc)
            raise ModelGatewayError(f"Local TTS request error: {exc}") from exc

        elapsed_ms = (time.monotonic() - start) * 1000
        audio_data = response.content

        return TTSResponse(
            audio_data=audio_data,
            duration_ms=0,
            model_id=model_id,
            voice_id=voice_id,
            cost_usd=0.0,  # Local = free
            latency_ms=round(elapsed_ms, 2),
            content_type=f"audio/{request.output_format}",
        )

    # ─── Voice Cloning ────────────────────────────────────────────────────

    async def clone_voice(self, request: VoiceCloneRequest) -> VoiceCloneResponse:
        """Use the optional local ``/voices/clone`` extension endpoint."""
        capabilities = await self.discover_capabilities()
        if not capabilities.voice_clone:
            return VoiceCloneResponse(
                voice_id=request.voice_id,
                provider=TTSProvider.LOCAL,
                status=VoiceCloneStatus.FAILED,
                message="Local TTS server does not advertise voice cloning",
            )
        path = Path(request.file_id).expanduser()
        try:
            if path.is_file():
                with path.open("rb") as handle:
                    response = await self._client.post(
                        "/voices/clone",
                        data={"voice_id": request.voice_id, "clone_prompt": request.clone_prompt},
                        files={"file": (path.name, handle, "application/octet-stream")},
                    )
            else:
                response = await self._client.post(
                    "/voices/clone",
                    json={
                        "voice_id": request.voice_id,
                        "reference_audio": request.file_id,
                        "clone_prompt": request.clone_prompt,
                    },
                )
            response.raise_for_status()
            payload = response.json()
            voice_id = str(payload.get("voice_id") or request.voice_id)
            return VoiceCloneResponse(
                voice_id=voice_id,
                provider=TTSProvider.LOCAL,
                status=VoiceCloneStatus.READY,
                message=str(payload.get("message") or "Local voice cloned"),
            )
        except Exception as exc:
            return VoiceCloneResponse(
                voice_id=request.voice_id,
                provider=TTSProvider.LOCAL,
                status=VoiceCloneStatus.FAILED,
                message=f"Local voice clone failed: {exc}",
            )

    async def design_voice(self, request: VoiceDesignRequest) -> VoiceDesignResponse:
        """Use the optional local ``/voices/design`` extension endpoint."""
        capabilities = await self.discover_capabilities()
        if not capabilities.voice_design:
            return VoiceDesignResponse(
                provider=TTSProvider.LOCAL,
                status=VoiceCloneStatus.FAILED,
                message="Local TTS server does not advertise voice design",
            )
        try:
            response = await self._client.post(
                "/voices/design",
                json={
                    "description": request.description,
                    "preview_text": request.preview_text,
                },
            )
            response.raise_for_status()
            payload = response.json()
            preview = payload.get("preview_audio", "")
            preview_audio = base64.b64decode(preview) if preview else b""
            return VoiceDesignResponse(
                voice_id=str(payload.get("voice_id") or ""),
                provider=TTSProvider.LOCAL,
                preview_audio_data=preview_audio,
                preview_audio_format=str(payload.get("preview_audio_format") or "wav"),
                status=VoiceCloneStatus.READY,
                message=str(payload.get("message") or "Local voice designed"),
            )
        except Exception as exc:
            return VoiceDesignResponse(
                provider=TTSProvider.LOCAL,
                status=VoiceCloneStatus.FAILED,
                message=f"Local voice design failed: {exc}",
            )

    # ─── System Voices ────────────────────────────────────────────────────

    async def list_system_voices(
        self,
        *,
        gender: str | None = None,
        language: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List local voices via ``/voices``, falling back to model ids."""
        try:
            response = await self._client.get("/voices")
            response.raise_for_status()
            data = response.json()
            raw_voices = data.get("data", data.get("voices", []))
        except Exception:
            try:
                response = await self._client.get("/models")
                response.raise_for_status()
                data = response.json()
                raw_voices = data.get("data", [])
            except Exception as exc:
                _log.warning("Failed to list local voices: %s", exc)
                return []

        voices: list[dict[str, Any]] = []
        for item in raw_voices if isinstance(raw_voices, list) else []:
            voice_id = str(item.get("voice_id") or item.get("id") or "")
            if not voice_id:
                continue
            voices.append(
                {
                    "voice_id": voice_id,
                    "name": str(item.get("name") or voice_id),
                    "gender": str(item.get("gender") or "neutral"),
                    "tags": list(item.get("tags") or ["local"]),
                }
            )

        if gender:
            voices = [v for v in voices if v.get("gender") == gender.lower()]

        return voices[:limit]

    # ─── Health Check ─────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """Check local TTS server connectivity."""
        try:
            response = await self._client.get("/models")
            self._last_health_error = ""
            return response.status_code < 500
        except Exception as exc:
            self._last_health_error = str(exc)
            return False

    async def shutdown(self) -> None:
        """Close HTTP client."""
        await self._client.aclose()
