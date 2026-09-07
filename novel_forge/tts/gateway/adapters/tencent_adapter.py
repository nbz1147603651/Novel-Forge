"""Tencent Cloud TTS adapter — 腾讯云语音合成.

API Reference: https://cloud.tencent.com/document/product/1073
- REST API: tts.tencentcloudapi.com (TextToVoice Action)
- Auth: HMAC-SHA256 Signature V3 (SecretId + SecretKey)
- Supports: SSML, 20+ languages, premium voices
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
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
)

_log = get_logger("tts.gateway.adapters.tencent")

_TENCENT_TTS_HOST = "tts.tencentcloudapi.com"
_TENCENT_TTS_ENDPOINT = "https://tts.tencentcloudapi.com"
_TENCENT_EMOTION_MAP = {
    "neutral": "neutral",
    "happy": "happy",
    "sad": "sad",
    "angry": "angry",
    "fearful": "fear",
    "surprised": "amaze",
    "disgusted": "disgusted",
    "tender": "peaceful",
    "mocking": "aojiao",
    "whisper": "peaceful",
    "nostalgic": "story",
    "anxious": "fear",
    "contempt": "disgusted",
    "determined": "exciting",
    "playful": "sajiao",
}
_TENCENT_EMOTIONS = {
    "neutral",
    "sad",
    "happy",
    "angry",
    "fear",
    "news",
    "story",
    "radio",
    "poetry",
    "call",
    "sajiao",
    "disgusted",
    "amaze",
    "peaceful",
    "exciting",
    "aojiao",
    "jieshuo",
}


class TencentTTSAdapter(TTSProviderAdapter):
    """Tencent Cloud TTS provider adapter.

    Supports:
    - TextToVoice REST API (sync synthesis)
    - 20+ languages: Mandarin, Cantonese, English, etc.
    - Premium and standard voice types
    - SSML support
    - Native speed control; shared pitch/volume controls are normalized locally
    """

    def __init__(
        self,
        secret_id: str,
        secret_key: str,
        *,
        project_id: int = 0,
        app_id: str = "",
        default_voice_type: int = 0,
        primary_language: int = 1,
        emotion_intensity: int = 100,
        segment_rate: int = 0,
        enable_subtitle_default: bool = False,
        connect_timeout_s: float = 10.0,
        read_timeout_s: float = 60.0,
    ) -> None:
        if not secret_id or not secret_key:
            raise ValueError("Tencent Cloud SecretId and SecretKey are required")

        self._secret_id = secret_id
        self._secret_key = secret_key
        # ``app_id`` remains a compatibility alias for older callers.  The
        # current TextToVoice contract names this field ProjectId.
        self._project_id = max(0, int(project_id or app_id or 0))
        self._default_voice_type = default_voice_type
        self._primary_language = max(1, min(2, int(primary_language)))
        self._emotion_intensity = max(50, min(200, int(emotion_intensity)))
        self._segment_rate = max(0, min(2, int(segment_rate)))
        self._enable_subtitle_default = bool(enable_subtitle_default)
        self._last_health_error = ""

        self._client = httpx.AsyncClient(
            base_url=_TENCENT_TTS_ENDPOINT,
            timeout=httpx.Timeout(
                connect=connect_timeout_s,
                read=read_timeout_s,
                write=30.0,
                pool=10.0,
            ),
        )

    @property
    def provider_name(self) -> str:
        return "tencent"

    @property
    def provider_type(self) -> TTSProvider:
        return TTSProvider.TENCENT

    @property
    def capabilities(self) -> TTSProviderCapabilities:
        return TTSProviderCapabilities(
            provider=TTSProvider.TENCENT,
            system_voice_catalog=True,
            synthesis_features={
                TTSFeature.SPEED,
                TTSFeature.EMOTION,
                TTSFeature.NATIVE_SUBTITLES,
                TTSFeature.SSML,
            },
        )

    @property
    def last_health_error(self) -> str:
        return self._last_health_error

    # ─── Signature V3 ─────────────────────────────────────────────────────

    def _sign_v3(self, payload: dict[str, Any]) -> dict[str, str]:
        """Generate Tencent Cloud API V3 signature headers."""
        timestamp = int(time.time())
        date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")

        # Step 1: Canonical request
        http_request_method = "POST"
        canonical_uri = "/"
        canonical_query_string = ""
        payload_json = json.dumps(payload)
        canonical_headers = (
            f"content-type:application/json; charset=utf-8\n"
            f"host:{_TENCENT_TTS_HOST}\n"
            f"x-tc-action:texttovoice\n"
            f"x-tc-timestamp:{timestamp}\n"
            f"x-tc-version:2019-08-23\n"
        )
        signed_headers = "content-type;host;x-tc-action;x-tc-timestamp;x-tc-version"
        hashed_payload = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        canonical_request = (
            f"{http_request_method}\n{canonical_uri}\n{canonical_query_string}\n"
            f"{canonical_headers}\n{signed_headers}\n{hashed_payload}"
        )

        # Step 2: String to sign
        algorithm = "TC3-HMAC-SHA256"
        credential_scope = f"{date}/tts/tc3_request"
        hashed_canonical = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
        string_to_sign = f"{algorithm}\n{timestamp}\n{credential_scope}\n{hashed_canonical}"

        # Step 3: Signing key
        def _hmac_sha256(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

        secret_date = _hmac_sha256(("TC3" + self._secret_key).encode("utf-8"), date)
        secret_service = _hmac_sha256(secret_date, "tts")
        secret_signing = _hmac_sha256(secret_service, "tc3_request")
        signature = hmac.new(
            secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        # Step 4: Authorization header
        authorization = (
            f"{algorithm} Credential={self._secret_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

        return {
            "Authorization": authorization,
            "Content-Type": "application/json; charset=utf-8",
            "Host": _TENCENT_TTS_HOST,
            "X-TC-Action": "TextToVoice",
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": "2019-08-23",
        }

    # ─── Voice Synthesis ──────────────────────────────────────────────────

    async def synthesize(self, request: TTSRequest) -> TTSResponse:
        """Synthesize speech using Tencent Cloud TTS REST API."""
        self._validate_request(request)

        voice_id = request.voice_id or str(self._default_voice_type)
        fast_voice_type = ""
        try:
            voice_type = int(voice_id)
        except ValueError:
            # Tencent one-shot cloned voices use a string FastVoiceType and a
            # fixed VoiceType sentinel rather than a numeric system voice id.
            voice_type = 200000000
            fast_voice_type = voice_id

        # Tencent TextToVoice uses a non-linear Speed scale: -2=0.6x,
        # -1=0.8x, 0=1.0x, 1=1.2x, 2=1.5x, 6=2.5x.  Keep one decimal
        # place so small UI changes remain audible instead of rounding to zero.
        speed = self._map_speed(request.speed)

        # Build payload
        codec = "mp3" if request.output_format == "mp3" else "wav"
        payload: dict[str, Any] = {
            "Text": request.text,
            "SessionId": f"novel-forge-{int(time.time() * 1000)}",
            "VoiceType": voice_type,
            "Speed": speed,
            # Provider value 0 means normal volume (not silence). Exact shared
            # volume and pitch semantics are applied by the portable FFmpeg
            # stage after synthesis.
            "Volume": 0,
            "Codec": codec,
            "SampleRate": self._map_sample_rate(request.sample_rate),
            "ProjectId": self._project_id,
            "PrimaryLanguage": self._primary_language,
            "SegmentRate": self._segment_rate,
            "EnableSubtitle": self._enable_subtitle_default,
        }
        if fast_voice_type:
            payload["FastVoiceType"] = fast_voice_type
        emotion_category = _TENCENT_EMOTION_MAP.get(request.emotion.strip().lower(), "")
        extension = request.metadata.get("platform_extension") or {}
        if isinstance(extension, dict):
            payload["PrimaryLanguage"] = max(
                1, min(2, int(extension.get("primary_language", payload["PrimaryLanguage"])))
            )
            payload["SegmentRate"] = max(
                0, min(2, int(extension.get("segment_rate", payload["SegmentRate"])))
            )
            if "enable_subtitle" in extension:
                payload["EnableSubtitle"] = bool(extension["enable_subtitle"])
            emotion_override = str(extension.get("emotion_category") or "").strip().lower()
            if emotion_override in _TENCENT_EMOTIONS:
                emotion_category = emotion_override
        if emotion_category:
            payload["EmotionCategory"] = emotion_category
            requested_intensity = (
                extension.get("emotion_intensity", self._emotion_intensity)
                if isinstance(extension, dict)
                else self._emotion_intensity
            )
            payload["EmotionIntensity"] = max(50, min(200, int(requested_intensity)))

        # Sign and send
        headers = self._sign_v3(payload)
        start = time.monotonic()

        try:
            response = await self._client.post("/", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            self._last_health_error = str(exc)
            _log.error("Tencent TTS error: %s", exc.response.text)
            raise ModelGatewayError(f"Tencent TTS failed: {exc}") from exc
        except httpx.RequestError as exc:
            self._last_health_error = str(exc)
            raise ModelGatewayError(f"Tencent TTS request error: {exc}") from exc

        elapsed_ms = (time.monotonic() - start) * 1000

        # Check for API error in response
        if "Response" in data and "Error" in data["Response"]:
            error = data["Response"]["Error"]
            raise ModelGatewayError(
                f"Tencent TTS API error: {error.get('Code')} - {error.get('Message')}"
            )

        # Parse audio data (base64 encoded)
        audio_data = b""
        response_payload = data.get("Response", {}) if isinstance(data, dict) else {}
        if isinstance(response_payload, dict) and response_payload.get("Audio"):
            audio_data = base64.b64decode(response_payload["Audio"])
        subtitles = (
            response_payload.get("Subtitles", []) if isinstance(response_payload, dict) else []
        )

        return TTSResponse(
            audio_data=audio_data,
            duration_ms=0,
            model_id="tencent-text-to-voice",
            voice_id=voice_id,
            cost_usd=len(request.text) * 0.00012,  # ~0.012元/千字
            latency_ms=round(elapsed_ms, 2),
            content_type=f"audio/{codec}",
            audio_format=codec,
            metadata={
                "request_id": (
                    str(response_payload.get("RequestId") or "")
                    if isinstance(response_payload, dict)
                    else ""
                ),
                "native_subtitles": subtitles if isinstance(subtitles, list) else [],
                "native_timing_granularity": "word",
                "fast_voice_type": bool(fast_voice_type),
            },
        )

    @staticmethod
    def _map_speed(multiplier: float) -> float:
        points = ((0.6, -2.0), (0.8, -1.0), (1.0, 0.0), (1.2, 1.0), (1.5, 2.0), (2.5, 6.0))
        value = max(points[0][0], min(points[-1][0], multiplier))
        for (left_x, left_y), (right_x, right_y) in zip(points, points[1:], strict=False):
            if value <= right_x:
                ratio = (value - left_x) / (right_x - left_x)
                return round(left_y + ratio * (right_y - left_y), 1)
        return points[-1][1]

    @staticmethod
    def _map_sample_rate(sample_rate: int) -> int:
        """Clamp the portable sample-rate request to Tencent's legal values."""

        return min((8000, 16000, 24000), key=lambda value: abs(value - sample_rate))

    # ─── Voice Cloning ────────────────────────────────────────────────────

    async def clone_voice(self, request: VoiceCloneRequest) -> VoiceCloneResponse:
        """Tencent Cloud does not support voice cloning via REST API."""
        return VoiceCloneResponse(
            voice_id=request.voice_id,
            provider=TTSProvider.TENCENT,
            status=VoiceCloneStatus.FAILED,
            message="Tencent Cloud TTS does not support voice cloning via REST API",
        )

    # ─── System Voices ────────────────────────────────────────────────────

    async def list_system_voices(
        self,
        *,
        gender: str | None = None,
        language: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List available Tencent Cloud TTS voices."""
        voices: list[dict[str, Any]] = [
            {"voice_id": "0", "name": "智钻", "gender": "female", "tags": ["女声", "精品"]},
            {"voice_id": "1001", "name": "智瑜", "gender": "female", "tags": ["女声", "温柔"]},
            {"voice_id": "1002", "name": "智聆", "gender": "female", "tags": ["女声", "新闻"]},
            {"voice_id": "1003", "name": "智美", "gender": "female", "tags": ["女声", "甜美"]},
            {"voice_id": "1004", "name": "智华", "gender": "male", "tags": ["男声", "沉稳"]},
            {"voice_id": "1005", "name": "智新", "gender": "male", "tags": ["男声", "青年"]},
            {"voice_id": "1006", "name": "智玲", "gender": "female", "tags": ["女声", "客服"]},
            {"voice_id": "1007", "name": "智琪", "gender": "female", "tags": ["女声", "活泼"]},
            {"voice_id": "1008", "name": "智芸", "gender": "female", "tags": ["女声", "知性"]},
            {"voice_id": "1009", "name": "智甜", "gender": "female", "tags": ["女声", "萝莉"]},
            {"voice_id": "1010", "name": "智丹", "gender": "female", "tags": ["女声", "成熟"]},
            {"voice_id": "1011", "name": "智辉", "gender": "male", "tags": ["男声", "浑厚"]},
            {"voice_id": "1012", "name": "智宁", "gender": "male", "tags": ["男声", "中年"]},
            {"voice_id": "1013", "name": "智萌", "gender": "neutral", "tags": ["童声", "可爱"]},
        ]

        if gender:
            voices = [v for v in voices if v.get("gender") == gender.lower()]

        return voices[:limit]

    # ─── Health Check ─────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """Check Tencent Cloud TTS API connectivity."""
        try:
            # Minimal synthesis check
            payload = {
                "Text": "测试",
                "SessionId": "health-check",
                "VoiceType": 0,
            }
            headers = self._sign_v3(payload)
            response = await self._client.post("/", json=payload, headers=headers)
            self._last_health_error = ""
            return response.status_code < 500
        except Exception as exc:
            self._last_health_error = str(exc)
            return False

    async def shutdown(self) -> None:
        """Close HTTP client."""
        await self._client.aclose()
