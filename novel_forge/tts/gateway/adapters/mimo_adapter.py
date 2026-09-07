"""Xiaomi MiMo V2.5 TTS adapter.

Official API: https://mimo.mi.com/docs/zh-CN/usage-guide/speech-synthesis
"""

from __future__ import annotations

import base64
import time
from typing import Any

import httpx

from novel_forge.core.exceptions import AuthenticationError, ModelGatewayError, RateLimitError
from novel_forge.obs.logger import get_logger
from novel_forge.tts.gateway.base import TTSProviderAdapter
from novel_forge.tts.schemas import (
    TTSFeature,
    TTSProvider,
    TTSProviderCapabilities,
    TTSRequest,
    TTSResponse,
)

_log = get_logger("tts.gateway.adapters.mimo")

_DEFAULT_BASE_URL = "https://api.xiaomimimo.com/v1"
_DEFAULT_MODEL = "mimo-v2.5-tts"

_SYSTEM_VOICES: tuple[dict[str, Any], ...] = (
    {"voice_id": "mimo_default", "name": "MiMo 默认", "gender": "neutral", "tags": ["自动"]},
    {"voice_id": "冰糖", "name": "冰糖", "gender": "female", "tags": ["中文"]},
    {"voice_id": "茉莉", "name": "茉莉", "gender": "female", "tags": ["中文"]},
    {"voice_id": "苏打", "name": "苏打", "gender": "male", "tags": ["中文"]},
    {"voice_id": "白桦", "name": "白桦", "gender": "male", "tags": ["中文"]},
    {"voice_id": "Mia", "name": "Mia", "gender": "female", "tags": ["英文"]},
    {"voice_id": "Chloe", "name": "Chloe", "gender": "female", "tags": ["英文"]},
    {"voice_id": "Milo", "name": "Milo", "gender": "male", "tags": ["英文"]},
    {"voice_id": "Dean", "name": "Dean", "gender": "male", "tags": ["英文"]},
)


class MiMoTTSAdapter(TTSProviderAdapter):
    """Synthesize built-in MiMo voices through the chat-completions audio API."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        default_model: str = _DEFAULT_MODEL,
        connect_timeout_s: float = 10.0,
        read_timeout_s: float = 180.0,
    ) -> None:
        if not api_key:
            raise ValueError("Xiaomi MiMo API key is required")
        self._api_key = api_key
        self._default_model = default_model or _DEFAULT_MODEL
        self._last_health_error = ""
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(
                connect=connect_timeout_s,
                read=read_timeout_s,
                write=30.0,
                pool=10.0,
            ),
        )

    @property
    def provider_name(self) -> str:
        return "mimo"

    @property
    def provider_type(self) -> TTSProvider:
        return TTSProvider.MIMO

    @property
    def capabilities(self) -> TTSProviderCapabilities:
        return TTSProviderCapabilities(
            provider=TTSProvider.MIMO,
            system_voice_catalog=True,
            synthesis_features={
                TTSFeature.EMOTION,
                TTSFeature.PARALINGUISTIC,
                TTSFeature.INSTRUCTION_CONTROL,
            },
        )

    @property
    def last_health_error(self) -> str:
        return self._last_health_error

    async def synthesize(self, request: TTSRequest) -> TTSResponse:
        """Return a WAV take; shared controls are normalized after synthesis."""

        self._validate_request(request)
        model_id = request.model_id or self._default_model
        if model_id != _DEFAULT_MODEL:
            raise ValueError(
                "MiMo formal synthesis requires mimo-v2.5-tts; "
                "voice design/clone models do not expose reusable voice IDs"
            )

        messages: list[dict[str, str]] = []
        instruction = self._style_instruction(request)
        if instruction:
            messages.append({"role": "user", "content": instruction})
        messages.append({"role": "assistant", "content": request.text})
        payload = {
            "model": model_id,
            "messages": messages,
            "audio": {"format": "wav", "voice": request.voice_id or "mimo_default"},
            "stream": False,
        }
        start = time.monotonic()
        try:
            response = await self._client.post(
                "/chat/completions",
                json=payload,
                headers={"api-key": self._api_key, "Content-Type": "application/json"},
            )
            self._raise_for_status(response)
            data = response.json()
        except (AuthenticationError, RateLimitError):
            raise
        except httpx.RequestError as exc:
            self._last_health_error = str(exc)
            raise ModelGatewayError(f"MiMo TTS request failed: {exc}", is_transient=True) from exc
        except (ValueError, TypeError) as exc:
            self._last_health_error = str(exc)
            raise ModelGatewayError(f"MiMo TTS returned invalid JSON: {exc}") from exc

        audio_b64 = self._audio_data(data)
        try:
            audio_data = base64.b64decode(audio_b64, validate=True)
        except ValueError as exc:
            raise ModelGatewayError("MiMo TTS returned invalid base64 audio") from exc
        if not audio_data:
            raise ModelGatewayError("MiMo TTS returned empty audio")
        self._last_health_error = ""
        elapsed_ms = (time.monotonic() - start) * 1000
        return TTSResponse(
            audio_data=audio_data,
            model_id=model_id,
            voice_id=request.voice_id or "mimo_default",
            latency_ms=round(elapsed_ms, 2),
            content_type="audio/wav",
            audio_format="wav",
            metadata={"requested_output_format": request.output_format},
        )

    @staticmethod
    def _audio_data(payload: Any) -> str:
        try:
            value = payload["choices"][0]["message"]["audio"]["data"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelGatewayError(
                "MiMo TTS response is missing choices[0].message.audio.data"
            ) from exc
        if not isinstance(value, str) or not value:
            raise ModelGatewayError("MiMo TTS response contains no audio data")
        return value

    @staticmethod
    def _style_instruction(request: TTSRequest) -> str:
        metadata = request.metadata or {}
        extension = metadata.get("platform_extension") or {}
        direction = metadata.get("vocal_direction") or {}
        parts: list[str] = []
        if isinstance(extension, dict) and str(extension.get("instruct") or "").strip():
            parts.append(str(extension["instruct"]).strip())
        emotion = str(metadata.get("performance_emotion") or request.emotion or "").strip()
        if emotion and emotion != "neutral":
            parts.append(f"情绪：{emotion}")
        tone = str(metadata.get("tone_hint") or "").strip()
        if tone:
            parts.append(f"语气：{tone}")
        if isinstance(direction, dict):
            for field, label in (
                ("delivery_style", "表达方式"),
                ("articulation", "吐字"),
                ("breathiness", "气声"),
                ("intimacy", "叙述距离"),
            ):
                value = direction.get(field)
                if value not in (None, "", 0, 0.0):
                    parts.append(f"{label}：{value}")
        return "；".join(dict.fromkeys(parts))

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        detail = response.text[:500]
        context = {"status_code": response.status_code}
        if response.status_code == 429:
            raise RateLimitError(f"MiMo TTS rate limited: {detail}", context=context)
        if response.status_code in {401, 403}:
            raise AuthenticationError(f"MiMo TTS authentication failed: {detail}", context=context)
        raise ModelGatewayError(
            f"MiMo TTS failed ({response.status_code}): {detail}",
            is_transient=response.status_code >= 500,
            context=context,
        )

    async def list_system_voices(
        self,
        *,
        gender: str | None = None,
        language: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        voices = [dict(item) for item in _SYSTEM_VOICES]
        if gender:
            voices = [item for item in voices if item["gender"] == gender.lower()]
        if language:
            language_key = "中文" if language.lower().startswith("zh") else "英文"
            voices = [item for item in voices if language_key in item["tags"]]
        return voices[:limit]

    async def health_check(self) -> bool:
        """Validate credentials with the smallest possible synthesis request."""

        try:
            await self.synthesize(
                TTSRequest(text="你好", voice_id="mimo_default", provider=TTSProvider.MIMO)
            )
            return True
        except Exception as exc:
            self._last_health_error = str(exc)
            return False

    async def shutdown(self) -> None:
        await self._client.aclose()
