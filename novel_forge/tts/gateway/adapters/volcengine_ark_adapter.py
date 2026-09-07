"""Volcengine Ark Agent Plan / Doubao Seed TTS 2.0 adapter.

Official Agent Plan guide: https://www.volcengine.com/docs/82379/2516286
"""

from __future__ import annotations

import base64
import json
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

_log = get_logger("tts.gateway.adapters.volcengine_ark")

_DEFAULT_ENDPOINT_URL = "https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional"
_DEFAULT_MODEL = "doubao-seed-tts-2.0"
_DEFAULT_RESOURCE_ID = "seed-tts-2.0"
_DEFAULT_SPEAKER = "zh_female_vv_uranus_bigtts"

_SYSTEM_VOICES: tuple[dict[str, Any], ...] = (
    {
        "voice_id": "zh_female_vv_uranus_bigtts",
        "name": "Vivi 2.0",
        "gender": "female",
        "tags": ["中文", "日文", "印尼语", "西班牙语", "指令遵循"],
    },
    {
        "voice_id": "zh_female_xiaohe_uranus_bigtts",
        "name": "小何 2.0",
        "gender": "female",
        "tags": ["中文", "Seed TTS 2.0", "指令遵循"],
    },
    {
        "voice_id": "zh_male_m191_uranus_bigtts",
        "name": "云舟 2.0",
        "gender": "male",
        "tags": ["中文", "Seed TTS 2.0", "指令遵循"],
    },
    {
        "voice_id": "zh_male_taocheng_uranus_bigtts",
        "name": "小天 2.0",
        "gender": "male",
        "tags": ["中文", "Seed TTS 2.0", "指令遵循"],
    },
    {
        "voice_id": "zh_female_cancan_uranus_bigtts",
        "name": "知性灿灿 2.0",
        "gender": "female",
        "tags": ["中文", "角色扮演", "指令遵循"],
    },
    {
        "voice_id": "zh_female_gufengshaoyu_uranus_bigtts",
        "name": "古风少御 2.0",
        "gender": "female",
        "tags": ["中文", "古风", "角色扮演", "指令遵循"],
    },
    {
        "voice_id": "zh_female_wenroushunv_uranus_bigtts",
        "name": "温柔淑女 2.0",
        "gender": "female",
        "tags": ["中文", "有声阅读", "指令遵循"],
    },
    {
        "voice_id": "zh_female_gaolengyujie_uranus_bigtts",
        "name": "高冷御姐 2.0",
        "gender": "female",
        "tags": ["中文", "角色扮演", "指令遵循"],
    },
    {
        "voice_id": "zh_male_ruyaqingnian_uranus_bigtts",
        "name": "儒雅青年 2.0",
        "gender": "male",
        "tags": ["中文", "有声阅读", "指令遵循"],
    },
    {
        "voice_id": "zh_male_qingcang_uranus_bigtts",
        "name": "擎苍 2.0",
        "gender": "male",
        "tags": ["中文", "角色扮演", "指令遵循"],
    },
    {
        "voice_id": "zh_male_baqiqingshu_uranus_bigtts",
        "name": "霸气青叔 2.0",
        "gender": "male",
        "tags": ["中文", "有声阅读", "指令遵循"],
    },
    {
        "voice_id": "zh_male_xuanyijieshuo_uranus_bigtts",
        "name": "悬疑解说 2.0",
        "gender": "male",
        "tags": ["中文", "悬疑", "有声阅读", "指令遵循"],
    },
    {
        "voice_id": "zh_male_shenyeboke_uranus_bigtts",
        "name": "深夜播客 2.0",
        "gender": "male",
        "tags": ["中文", "播客", "指令遵循"],
    },
    {
        "voice_id": "en_male_tim_uranus_bigtts",
        "name": "Tim",
        "gender": "male",
        "tags": ["英文", "美式英语", "指令遵循"],
    },
    {
        "voice_id": "en_female_dacey_uranus_bigtts",
        "name": "Dacey",
        "gender": "female",
        "tags": ["英文", "美式英语", "指令遵循"],
    },
)


class VolcengineArkTTSAdapter(TTSProviderAdapter):
    """Synthesize complete takes from the Agent Plan HTTP chunked JSON stream."""

    def __init__(
        self,
        api_key: str,
        *,
        resource_id: str = _DEFAULT_RESOURCE_ID,
        endpoint_url: str = _DEFAULT_ENDPOINT_URL,
        default_model: str = _DEFAULT_MODEL,
        connect_timeout_s: float = 10.0,
        read_timeout_s: float = 180.0,
    ) -> None:
        if not api_key:
            raise ValueError("Volcengine Ark Agent Plan API key is required")
        self._api_key = api_key
        self._resource_id = resource_id or _DEFAULT_RESOURCE_ID
        self._endpoint_url = endpoint_url or _DEFAULT_ENDPOINT_URL
        self._default_model = default_model or _DEFAULT_MODEL
        self._last_health_error = ""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=connect_timeout_s,
                read=read_timeout_s,
                write=30.0,
                pool=10.0,
            ),
        )

    @property
    def provider_name(self) -> str:
        return "volcengine_ark"

    @property
    def provider_type(self) -> TTSProvider:
        return TTSProvider.VOLCENGINE_ARK

    @property
    def capabilities(self) -> TTSProviderCapabilities:
        return TTSProviderCapabilities(
            provider=TTSProvider.VOLCENGINE_ARK,
            system_voice_catalog=True,
            synthesis_features={
                TTSFeature.SPEED,
                TTSFeature.PITCH,
                TTSFeature.VOLUME,
                TTSFeature.EMOTION,
                TTSFeature.INSTRUCTION_CONTROL,
            },
        )

    @property
    def last_health_error(self) -> str:
        return self._last_health_error

    async def synthesize(self, request: TTSRequest) -> TTSResponse:
        self._validate_request(request)
        output_format = self._output_format(request.output_format)
        audio_params: dict[str, Any] = {
            "format": output_format,
            "sample_rate": request.sample_rate,
            "speech_rate": self._ratio_scale(request.speed),
            "loudness_rate": self._ratio_scale(request.volume),
        }
        if output_format == "mp3":
            audio_params["bit_rate"] = max(64000, min(160000, request.bitrate))

        req_params: dict[str, Any] = {
            "text": request.text,
            "speaker": request.voice_id or _DEFAULT_SPEAKER,
            "audio_params": audio_params,
        }
        additions = self._additions(request)
        if additions:
            # The V3 wire contract expects this advanced object as a JSON string.
            req_params["additions"] = json.dumps(additions, ensure_ascii=False)
        payload = {
            "req_params": req_params,
        }
        headers = self._headers()
        start = time.monotonic()
        try:
            response = await self._client.post(
                self._endpoint_url,
                json=payload,
                headers=headers,
            )
            self._raise_for_status(response)
            events = self._decode_events(response.text)
        except (AuthenticationError, RateLimitError):
            raise
        except httpx.RequestError as exc:
            self._last_health_error = str(exc)
            raise ModelGatewayError(
                f"Volcengine TTS request failed: {exc}", is_transient=True
            ) from exc

        audio_parts: list[bytes] = []
        terminal_code = 0
        usage: dict[str, Any] = {}
        duration_ms = 0
        for event in events:
            code = self._int_value(event.get("code"))
            terminal_code = code or terminal_code
            if code not in {0, 20000000}:
                message = str(event.get("message") or "unknown provider error")
                raise ModelGatewayError(
                    f"Volcengine TTS API error {code}: {message}",
                    is_transient=code >= 55000000,
                    context={
                        "provider_error_code": str(code),
                        "status_code": 503 if code >= 55000000 else 400,
                    },
                )
            encoded = event.get("data")
            if isinstance(encoded, str) and encoded:
                try:
                    audio_parts.append(base64.b64decode(encoded, validate=True))
                except ValueError as exc:
                    raise ModelGatewayError("Volcengine TTS returned invalid base64 audio") from exc
            event_usage = event.get("usage")
            if isinstance(event_usage, dict):
                usage.update(event_usage)
            addition = event.get("addition")
            if isinstance(addition, dict):
                duration_ms = self._int_value(addition.get("duration")) or duration_ms

        audio_data = b"".join(audio_parts)
        if not audio_data:
            raise ModelGatewayError(
                f"Volcengine TTS returned no audio (terminal code {terminal_code})"
            )
        elapsed_ms = (time.monotonic() - start) * 1000
        self._last_health_error = ""
        return TTSResponse(
            audio_data=audio_data,
            duration_ms=max(0, duration_ms),
            model_id=request.model_id or self._default_model,
            voice_id=request.voice_id or _DEFAULT_SPEAKER,
            latency_ms=round(elapsed_ms, 2),
            content_type=self._content_type(output_format),
            audio_format=output_format,
            metadata={
                "resource_id": self._resource_id,
                "endpoint": self._endpoint_url,
                "log_id": response.headers.get("X-Tt-Logid", ""),
                "usage": usage,
            },
        )

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Connection": "keep-alive",
            "X-Api-Key": self._api_key,
            "X-Api-Resource-Id": self._resource_id,
            "X-Control-Require-Usage-Tokens-Return": "*",
        }
        return headers

    @staticmethod
    def _decode_events(raw: str) -> list[dict[str, Any]]:
        """Decode newline-delimited or directly concatenated JSON objects."""

        text = raw.strip()
        if not text:
            raise ModelGatewayError("Volcengine TTS returned an empty response")
        decoder = json.JSONDecoder()
        events: list[dict[str, Any]] = []
        index = 0
        while index < len(text):
            while index < len(text) and text[index].isspace():
                index += 1
            if text.startswith("data:", index):
                index += len("data:")
                while index < len(text) and text[index].isspace():
                    index += 1
            if index >= len(text):
                break
            try:
                value, index = decoder.raw_decode(text, index)
            except json.JSONDecodeError as exc:
                raise ModelGatewayError(
                    f"Volcengine TTS returned an invalid JSON stream at byte {exc.pos}"
                ) from exc
            if isinstance(value, dict):
                events.append(value)
        if not events:
            raise ModelGatewayError("Volcengine TTS response contained no JSON events")
        return events

    @staticmethod
    def _ratio_scale(value: float) -> int:
        return int(round((max(0.5, min(2.0, value)) - 1.0) * 100))

    @staticmethod
    def _output_format(requested: str) -> str:
        value = requested.strip().lower()
        # Agent Plan's chunked API officially supports mp3/ogg_opus/pcm.  WAV
        # can repeat a container header per chunk, so portable WAV/FLAC requests
        # are normalized to one valid MP3 stream.
        return value if value in {"pcm", "mp3"} else "mp3"

    @staticmethod
    def _content_type(audio_format: str) -> str:
        return {"mp3": "audio/mpeg", "wav": "audio/wav", "pcm": "audio/L16"}.get(
            audio_format, f"audio/{audio_format}"
        )

    @staticmethod
    def _additions(request: TTSRequest) -> dict[str, Any]:
        metadata = request.metadata or {}
        extension = metadata.get("platform_extension") or {}
        context_texts: list[str] = []
        if isinstance(extension, dict):
            instruction = str(extension.get("instruct") or "").strip()
            if instruction:
                context_texts.append(instruction)
        emotion = str(metadata.get("performance_emotion") or request.emotion or "").strip()
        if emotion and emotion != "neutral":
            context_texts.append(f"情绪：{emotion}")
        tone = str(metadata.get("tone_hint") or "").strip()
        if tone:
            context_texts.append(f"语气：{tone}")
        result: dict[str, Any] = {}
        if context_texts:
            # Seed TTS 2.0 currently consumes only the first list item.  Merge
            # the director's dimensions so no later emotion/tone hint is lost.
            result["context_texts"] = ["；".join(dict.fromkeys(context_texts))]
        if request.pitch:
            result["post_process"] = {"pitch": request.pitch}
        language = str(metadata.get("language_code") or "").strip().lower()
        if language and language != "auto":
            result["explicit_language"] = language
        return result

    @staticmethod
    def _int_value(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        detail = response.text[:500]
        context = {"status_code": response.status_code}
        if response.status_code == 429:
            raise RateLimitError(f"Volcengine TTS rate limited: {detail}", context=context)
        if response.status_code in {401, 403}:
            raise AuthenticationError(
                f"Volcengine TTS authentication failed: {detail}", context=context
            )
        raise ModelGatewayError(
            f"Volcengine TTS failed ({response.status_code}): {detail}",
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
            normalized = language.lower()
            language_tag = "英文"
            for prefixes, tag in (
                (("zh", "yue"), "中文"),
                (("ja",), "日文"),
                (("id",), "印尼语"),
                (("es",), "西班牙语"),
            ):
                if normalized.startswith(prefixes):
                    language_tag = tag
                    break
            voices = [item for item in voices if language_tag in item["tags"]]
        return voices[:limit]

    async def health_check(self) -> bool:
        try:
            await self.synthesize(
                TTSRequest(
                    text="你好",
                    voice_id=_DEFAULT_SPEAKER,
                    output_format="mp3",
                    sample_rate=24000,
                    provider=TTSProvider.VOLCENGINE_ARK,
                )
            )
            return True
        except Exception as exc:
            self._last_health_error = str(exc)
            return False

    async def shutdown(self) -> None:
        await self._client.aclose()
