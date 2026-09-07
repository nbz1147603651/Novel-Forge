"""MiniMax TTS adapter — voice cloning and speech synthesis.

API Reference: https://platform.minimaxi.com/docs/api-reference/speech-t2a-http
- Voice Clone: POST /v1/voice_clone
- TTS (sync): POST /v1/t2a_v2
- TTS (async): POST /v1/t2a_async_v2
- TTS (WebSocket streaming): wss://api.minimaxi.com/ws/v1/t2a_v2
- Voice Design: POST /v1/voice_design
- File Upload: POST /v1/files/upload

Models: speech-2.8-hd, speech-2.8-turbo, speech-2.6-hd, speech-2.6-turbo,
        speech-02-hd, speech-02-turbo, speech-01-hd, speech-01-turbo
Voice features: 300+ system voices, 9 native emotions (model-dependent),
19 T2A interjection tags (speech-2.8 only), pronunciation control,
language boost, voice effects, cross-language clone, WebSocket streaming
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any, NoReturn

import httpx

from novel_forge.core.exceptions import ModelGatewayError, RateLimitError
from novel_forge.obs.logger import get_logger
from novel_forge.tts.gateway.base import TTSProviderAdapter
from novel_forge.tts.platform.minimax_contract import (
    MINIMAX_NON_STREAMING_AUDIO_FORMATS,
    MINIMAX_STREAMING_AUDIO_FORMATS,
    MINIMAX_T2A_CONTINUOUS_SOUND_MODELS,
    MINIMAX_T2A_INTERJECTIONS,
    MINIMAX_TTS_API_BASE_URL,
    MINIMAX_TTS_WS_URL,
    minimax_emotion_allowed_for_model,
    minimax_estimated_t2a_cost_usd,
    minimax_language_boost,
    minimax_model_id,
    minimax_native_emotion,
    minimax_supports_interjections,
    minimax_valid_interjections,
)
from novel_forge.tts.schemas import (
    ProviderTakeEvidence,
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

_log = get_logger("tts.gateway.adapters.minimax")

_MINIMAX_TTS_BASE_URL = MINIMAX_TTS_API_BASE_URL
_MINIMAX_DEFAULT_MODEL = "speech-2.8-hd"
_MINIMAX_DEFAULT_VOICE = "Chinese (Mandarin)_Reliable_Executive"
_MINIMAX_SYNC_TEXT_LIMIT = 10_000
_NATIVE_SUBTITLE_KEYS: tuple[str, ...] = (
    "subtitle",
    "subtitles",
    "subtitle_data",
    "subtitle_info",
)

# Backward-compatible public name.  The authoritative set lives in the shared
# platform contract used by both script rendering and the HTTP adapter.
MINIMAX_EMOTION_TAGS = list(MINIMAX_T2A_INTERJECTIONS)


class MiniMaxTTSAdapter(TTSProviderAdapter):
    """MiniMax TTS provider adapter.

    Supports:
    - Voice cloning from reference audio
    - Voice design from text description
    - Synchronous TTS (≤10,000 chars)
    - Async TTS for long text (≤1,000,000 chars)
    - WebSocket streaming TTS (low-latency chunked audio)
    - 300+ system voices
    - 9 native emotions (model-dependent: fluent/whisper only on 2.6 series)
    - 19 T2A interjection tags (speech-2.8 series only)
    - continuous_sound for long-text prosody (speech-2.8 only)
    """

    def __init__(
        self,
        api_key: str,
        *,
        group_id: str = "",
        base_url: str = _MINIMAX_TTS_BASE_URL,
        default_model: str = _MINIMAX_DEFAULT_MODEL,
        connect_timeout_s: float = 10.0,
        read_timeout_s: float = 300.0,
        async_poll_interval_s: float = 1.0,
        async_timeout_s: float = 3600.0,
        aigc_watermark_default: bool = False,
        continuous_sound_default: bool = False,
        force_cbr_default: bool = True,
        english_normalization_default: bool = False,
    ) -> None:
        if not api_key:
            raise ValueError("MiniMax TTS API key is required")

        self._api_key = api_key
        self._group_id = group_id
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._last_health_error = ""
        self._async_poll_interval_s = max(0.0, async_poll_interval_s)
        self._async_timeout_s = max(1.0, async_timeout_s)
        # AIGC 水印默认关闭：MiniMax 开启水印时会在音频尾部注入可听方波（短试听末尾
        # 的“滴滴”声）。如需合规水印，可在 settings.tts_aigc_watermark_default 或
        # 单次请求 platform_extension.aigc_watermark=True 显式开启。
        self._aigc_watermark_default = bool(aigc_watermark_default)
        # continuous_sound 默认开启（仅对 speech-2.8 系列生效）：开启后模型不切分
        # 文本，长文本韵律更自然。可在 settings.tts_minimax_continuous_sound_default
        # 或单次请求 platform_extension.continuous_sound=False 显式关闭。
        self._continuous_sound_default = bool(continuous_sound_default)
        self._force_cbr_default = bool(force_cbr_default)
        self._english_normalization_default = bool(english_normalization_default)

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(
                connect=connect_timeout_s,
                read=read_timeout_s,
                write=30.0,
                pool=10.0,
            ),
            headers=self._build_headers(),
        )

    def _build_headers(self) -> dict[str, str]:
        """Build authorization headers."""
        return {
            "Authorization": f"Bearer {self._api_key}",
        }

    @property
    def provider_name(self) -> str:
        return "minimax"

    @property
    def provider_type(self) -> TTSProvider:
        return TTSProvider.MINIMAX

    @property
    def capabilities(self) -> TTSProviderCapabilities:
        return TTSProviderCapabilities(
            provider=TTSProvider.MINIMAX,
            voice_clone=True,
            voice_design=True,
            system_voice_catalog=True,
            local_reference_audio=True,
            synthesis_features={
                TTSFeature.SPEED,
                TTSFeature.VOLUME,
                TTSFeature.PITCH,
                TTSFeature.EMOTION,
                TTSFeature.PARALINGUISTIC,
                TTSFeature.PRONUNCIATION,
                TTSFeature.LANGUAGE_BOOST,
                TTSFeature.VOICE_EFFECTS,
            },
        )

    async def prepare_clone_source(self, reference: str) -> str:
        """Upload a local voice sample and return MiniMax's file id."""
        from pathlib import Path

        path = Path(reference).expanduser()
        if not path.is_file():
            return reference
        if path.suffix.lower() not in {".mp3", ".m4a", ".wav"}:
            raise ValueError("MiniMax voice clone accepts mp3, m4a or wav reference audio")
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("MiniMax voice clone reference audio must not exceed 20 MB")
        form: dict[str, str] = {"purpose": "voice_clone"}
        with path.open("rb") as handle:
            response = await self._client.post(
                "/files/upload",
                data=form,
                files={"file": (path.name, handle, "application/octet-stream")},
            )
        response.raise_for_status()
        payload = response.json()
        self._ensure_api_success(payload, "file upload")
        file_payload = payload.get("file", {}) if isinstance(payload, dict) else {}
        file_id = (
            (file_payload.get("file_id") if isinstance(file_payload, dict) else "")
            or (payload.get("file_id") if isinstance(payload, dict) else "")
            or (payload.get("id") if isinstance(payload, dict) else "")
        )
        if not file_id:
            raise RuntimeError("MiniMax file upload response did not contain file_id")
        return str(file_id)

    @property
    def last_health_error(self) -> str:
        return self._last_health_error

    # ─── Voice Synthesis ──────────────────────────────────────────────────

    async def synthesize(self, request: TTSRequest) -> TTSResponse:
        """Synthesize speech using MiniMax TTS API.

        Uses t2a_v2 for text ≤10,000 chars and transparently completes the
        asynchronous task/download flow for longer text.
        """
        self._validate_request(request)

        # The HTTP endpoint has a hard 10,000-character boundary.  Long input
        # must complete the full async submit -> poll -> file download cycle so
        # callers still receive the same TTSResponse contract.
        model_id = request.model_id or self._default_model
        self._validate_minimax_t2a_request(request)
        text = self._inject_emotion_tags(request.text, request.emotion_tags, model_id=model_id)
        if len(text) > _MINIMAX_SYNC_TEXT_LIMIT:
            return await self._synthesize_async_and_download(request, text=text)

        voice_id = request.voice_id or _MINIMAX_DEFAULT_VOICE
        voice_setting = self._build_voice_setting(request, model_id=model_id, voice_id=voice_id)

        # Build audio_setting
        audio_setting: dict[str, Any] = {
            "format": request.output_format.lower(),
            "sample_rate": request.sample_rate,
            "bitrate": request.bitrate,
            "channel": request.channel,
            "force_cbr": self._force_cbr_default,
        }

        # Build request payload
        payload: dict[str, Any] = {
            "model": model_id,
            "text": text,
            "stream": False,
            "voice_setting": voice_setting,
            "audio_setting": audio_setting,
            "language_boost": self._language_boost(request.language_boost),
            "subtitle_enable": False,
            "subtitle_type": "sentence",
            "output_format": "hex",
            "english_normalization": self._english_normalization_default,
        }
        if request.pronunciation_overrides:
            payload["pronunciation_dict"] = {"tone": request.pronunciation_overrides}
        voice_effect = request.voice_effect.model_dump(exclude_defaults=True)
        if voice_effect:
            payload["voice_modify"] = voice_effect
        extension = request.metadata.get("platform_extension") or {}
        if isinstance(extension, dict):
            if "subtitle_enable" in extension:
                payload["subtitle_enable"] = bool(extension["subtitle_enable"])
            subtitle_type = str(extension.get("subtitle_type") or payload["subtitle_type"])
            if subtitle_type in {"sentence", "word"}:
                payload["subtitle_type"] = subtitle_type
            if "force_cbr" in extension:
                audio_setting["force_cbr"] = bool(extension["force_cbr"])
            if "english_normalization" in extension:
                payload["english_normalization"] = bool(extension["english_normalization"])
            timbre_weights = extension.get("timbre_weights")
            if isinstance(timbre_weights, list) and timbre_weights:
                payload["timbre_weights"] = timbre_weights
        # AIGC 水印默认跟随 adapter 的 aigc_watermark_default（默认 False，避免短试听
        # 末尾出现可听方波“滴”声）。用户可通过 platform_extension.aigc_watermark=True
        # 为单次请求显式开启合规水印；extension 不存在或未指定时走默认值。
        aigc_override = extension.get("aigc_watermark") if isinstance(extension, dict) else None
        payload["aigc_watermark"] = bool(
            aigc_override if aigc_override is not None else self._aigc_watermark_default
        )
        # continuous_sound: speech-2.8 系列独有的长文本连续推理参数，开启后模型不切分文本，
        # 韵律更自然但延迟稍高。仅对 speech-2.8-hd / speech-2.8-turbo 生效；
        # extension 未指定时回退 adapter 默认值（settings.tts_minimax_continuous_sound_default）。
        continuous_sound = (
            extension.get("continuous_sound") if isinstance(extension, dict) else None
        )
        if continuous_sound is None:
            continuous_sound = self._continuous_sound_default
        if minimax_model_id(model_id) in MINIMAX_T2A_CONTINUOUS_SOUND_MODELS:
            payload["continuous_sound"] = bool(continuous_sound)

        start = time.monotonic()
        try:
            response = await self._client.post("/t2a_v2", json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            self._last_health_error = str(exc)
            _log.error("MiniMax TTS error: %s", exc.response.text)
            self._raise_http_error(exc, "speech synthesis")
        except httpx.RequestError as exc:
            self._last_health_error = str(exc)
            raise ModelGatewayError(f"MiniMax TTS request error: {exc}") from exc

        elapsed_ms = (time.monotonic() - start) * 1000

        # Parse response
        self._ensure_api_success(data, "speech synthesis")
        data_payload = data.get("data") or {}
        provider_status = data_payload.get("status") if isinstance(data_payload, dict) else None
        if provider_status not in (None, 2, "2"):
            raise ModelGatewayError(f"MiniMax synthesis returned status {provider_status}")
        audio_hex = str(data_payload.get("audio") or "") if isinstance(data_payload, dict) else ""
        try:
            audio_data = bytes.fromhex(audio_hex) if audio_hex else b""
        except ValueError as exc:
            raise ModelGatewayError("MiniMax returned invalid hex audio") from exc
        if not audio_data:
            raise ModelGatewayError("MiniMax returned empty audio")

        extra_info = data.get("extra_info", {})
        if not isinstance(extra_info, dict):
            extra_info = {}
        duration_ms = extra_info.get("audio_length", 0)
        usage_characters = int(extra_info.get("usage_characters", 0) or 0)
        actual_format = str(extra_info.get("audio_format") or request.output_format).lower()

        native_subtitles = self._native_subtitle_payload(data, data_payload, extra_info)
        response_metadata: dict[str, Any] = {}
        if native_subtitles is not None:
            # Preserve provider timing evidence verbatim for diagnostics and
            # later parser upgrades.  ASR/forced alignment remains the
            # canonical quality-verification source until a documented MiniMax
            # response schema can be normalised without guessing time units.
            response_metadata["native_subtitles"] = native_subtitles

        return TTSResponse(
            audio_data=audio_data,
            duration_ms=duration_ms,
            model_id=model_id,
            voice_id=voice_id,
            cost_usd=minimax_estimated_t2a_cost_usd(model_id, usage_characters),
            latency_ms=round(elapsed_ms, 2),
            content_type=f"audio/{actual_format}",
            audio_format=actual_format,
            metadata=response_metadata,
            take_evidence=ProviderTakeEvidence(
                trace_id=str(data.get("trace_id") or ""),
                provider_status=str(provider_status or ""),
                reported_duration_ms=int(duration_ms or 0),
                sample_rate=int(extra_info.get("audio_sample_rate") or request.sample_rate),
                audio_size_bytes=int(extra_info.get("audio_size") or len(audio_data)),
                text_units=int(extra_info.get("word_count") or usage_characters),
                invisible_character_ratio=float(extra_info.get("invisible_character_ratio") or 0.0),
                native_timing_requested=bool(payload["subtitle_enable"]),
                native_timing_granularity=str(payload["subtitle_type"]),
                provider_extension={"usage_characters": usage_characters},
            ),
        )

    @staticmethod
    def _native_subtitle_payload(
        response: dict[str, Any],
        data_payload: dict[str, Any],
        extra_info: dict[str, Any],
    ) -> dict[str, Any] | list[Any] | str | None:
        """Extract a returned native subtitle payload without inventing a schema."""

        for source in (data_payload, extra_info, response):
            for key in _NATIVE_SUBTITLE_KEYS:
                value = source.get(key)
                if isinstance(value, (dict, list, str)):
                    return value
        return None

    async def _synthesize_async_long(self, request: TTSRequest) -> str:
        """Submit long text TTS job and return task_id.

        Kept as a low-level operation for task-oriented callers.  Normal
        ``synthesize`` calls use the complete submit/poll/download path.
        """
        self._validate_request(request)
        self._validate_minimax_t2a_request(request)
        model_id = request.model_id or self._default_model
        result = await self._submit_async_long(
            request,
            text=self._inject_emotion_tags(request.text, request.emotion_tags, model_id=model_id),
        )
        return str(result["task_id"])

    # ─── WebSocket Streaming Synthesis ────────────────────────────────────

    async def synthesize_streaming(
        self,
        request: TTSRequest,
        *,
        ws_url: str = "",
        on_chunk: Callable[[bytes], Awaitable[None] | None] | None = None,
    ) -> TTSResponse:
        """Synthesize speech via WebSocket streaming for low time-to-first-audio.

        Connects to MiniMax's ``wss://…/ws/v1/t2a_v2`` endpoint, sends the
        text in a single ``task_continue`` frame, collects hex audio chunks
        until ``is_final``, and returns the assembled audio.

        Args:
            request: Standard TTS request.
            ws_url: Override WebSocket URL (defaults to China-domestic endpoint).
            on_chunk: Optional async/sync callback invoked with each decoded
                audio chunk for real-time playback.

        Returns:
            TTSResponse with the complete audio data.
        """
        import json as _json
        import ssl as _ssl

        try:
            import websockets
        except ImportError as exc:
            raise ModelGatewayError(
                "WebSocket streaming requires the 'websockets' package. "
                "Install it with: pip install websockets"
            ) from exc

        self._validate_request(request)
        model_id = request.model_id or self._default_model
        text = self._inject_emotion_tags(request.text, request.emotion_tags, model_id=model_id)
        voice_id = request.voice_id or _MINIMAX_DEFAULT_VOICE
        voice_setting = self._build_voice_setting(request, model_id=model_id, voice_id=voice_id)

        # Validate streaming format
        output_format = request.output_format.strip().lower()
        if output_format not in MINIMAX_STREAMING_AUDIO_FORMATS:
            allowed = ", ".join(sorted(MINIMAX_STREAMING_AUDIO_FORMATS))
            raise ValueError(
                f"MiniMax WebSocket T2A output_format must be one of {allowed}; "
                f"got {request.output_format!r}"
            )

        audio_setting: dict[str, Any] = {
            "sample_rate": request.sample_rate,
            "bitrate": request.bitrate,
            "format": output_format,
            "channel": request.channel,
            "force_cbr": self._force_cbr_default,
        }

        url = ws_url or MINIMAX_TTS_WS_URL
        headers = {"Authorization": f"Bearer {self._api_key}"}
        ssl_context = _ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = _ssl.CERT_NONE

        start = time.monotonic()
        audio_data = bytearray()

        try:
            async with websockets.connect(url, additional_headers=headers, ssl=ssl_context) as ws:
                # Wait for connected_success
                connected_msg = _json.loads(await ws.recv())
                if connected_msg.get("event") != "connected_success":
                    raise ModelGatewayError(f"MiniMax WebSocket connection failed: {connected_msg}")

                # Send task_start
                task_start_msg: dict[str, Any] = {
                    "event": "task_start",
                    "model": model_id,
                    "voice_setting": voice_setting,
                    "audio_setting": audio_setting,
                    "english_normalization": self._english_normalization_default,
                }
                if request.language_boost:
                    task_start_msg["language_boost"] = self._language_boost(request.language_boost)
                if request.pronunciation_overrides:
                    task_start_msg["pronunciation_dict"] = {"tone": request.pronunciation_overrides}
                voice_effect = request.voice_effect.model_dump(exclude_defaults=True)
                if voice_effect:
                    task_start_msg["voice_modify"] = voice_effect
                # continuous_sound for speech-2.8（extension 未指定时回退 adapter 默认值）
                extension = request.metadata.get("platform_extension") or {}
                if isinstance(extension, dict):
                    if "force_cbr" in extension:
                        audio_setting["force_cbr"] = bool(extension["force_cbr"])
                    if "english_normalization" in extension:
                        task_start_msg["english_normalization"] = bool(
                            extension["english_normalization"]
                        )
                    cs = extension.get("continuous_sound")
                    if cs is None:
                        cs = self._continuous_sound_default
                    if minimax_model_id(model_id) in MINIMAX_T2A_CONTINUOUS_SOUND_MODELS:
                        task_start_msg["continuous_sound"] = bool(cs)

                await ws.send(_json.dumps(task_start_msg))
                start_resp = _json.loads(await ws.recv())
                if start_resp.get("event") != "task_started":
                    raise ModelGatewayError(f"MiniMax WebSocket task_start failed: {start_resp}")

                # Send text via task_continue
                await ws.send(_json.dumps({"event": "task_continue", "text": text}))

                # Collect audio chunks until is_final
                while True:
                    raw = await ws.recv()
                    msg = _json.loads(raw)
                    data_obj = msg.get("data") or {}
                    audio_hex = data_obj.get("audio") or ""
                    if audio_hex:
                        chunk = bytes.fromhex(audio_hex)
                        audio_data.extend(chunk)
                        if on_chunk is not None:
                            result = on_chunk(chunk)
                            if isinstance(result, Awaitable):
                                await result
                    if msg.get("is_final"):
                        break

                # Send task_finish
                await ws.send(_json.dumps({"event": "task_finish"}))

        except ModelGatewayError:
            raise
        except Exception as exc:
            self._last_health_error = str(exc)
            raise ModelGatewayError(f"MiniMax WebSocket TTS error: {exc}") from exc

        elapsed_ms = (time.monotonic() - start) * 1000
        if not audio_data:
            raise ModelGatewayError("MiniMax WebSocket TTS returned empty audio")

        return TTSResponse(
            audio_data=bytes(audio_data),
            model_id=model_id,
            voice_id=voice_id,
            cost_usd=minimax_estimated_t2a_cost_usd(model_id, len(text)),
            latency_ms=round(elapsed_ms, 2),
            content_type=f"audio/{output_format}",
            audio_format=output_format,
            metadata={"streaming": True, "ws_url": url},
            take_evidence=ProviderTakeEvidence(
                trace_id="",
                provider_status="streaming_complete",
                sample_rate=request.sample_rate,
                audio_size_bytes=len(audio_data),
                text_units=len(text),
                provider_extension={"streaming": True},
            ),
        )

    async def _submit_async_long(
        self,
        request: TTSRequest,
        *,
        text: str,
    ) -> dict[str, Any]:
        """Submit one asynchronous long-text task and retain billing metadata."""

        model_id = request.model_id or self._default_model
        voice_id = request.voice_id or _MINIMAX_DEFAULT_VOICE
        voice_setting = self._build_voice_setting(request, model_id=model_id, voice_id=voice_id)
        payload: dict[str, Any] = {
            "model": model_id,
            "text": text,
            "voice_setting": voice_setting,
            "audio_setting": {
                "format": request.output_format.lower(),
                "audio_sample_rate": request.sample_rate,
                "bitrate": request.bitrate,
                "channel": request.channel,
                "force_cbr": self._force_cbr_default,
            },
            "language_boost": self._language_boost(request.language_boost),
            "english_normalization": self._english_normalization_default,
        }
        extension = request.metadata.get("platform_extension") or {}
        if isinstance(extension, dict):
            if "force_cbr" in extension:
                payload["audio_setting"]["force_cbr"] = bool(extension["force_cbr"])
            if "english_normalization" in extension:
                payload["english_normalization"] = bool(extension["english_normalization"])
        if request.pronunciation_overrides:
            payload["pronunciation_dict"] = {"tone": request.pronunciation_overrides}
        voice_effect = request.voice_effect.model_dump(exclude_defaults=True)
        if voice_effect:
            payload["voice_modify"] = voice_effect
        try:
            response = await self._client.post("/t2a_async_v2", json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            self._last_health_error = str(exc)
            self._raise_http_error(exc, "async speech submission")
        except httpx.RequestError as exc:
            self._last_health_error = str(exc)
            raise ModelGatewayError(f"MiniMax async speech request error: {exc}") from exc
        self._ensure_api_success(data, "async speech submission")

        task_id_raw = data.get("task_id", "")
        task_id: str = str(task_id_raw) if task_id_raw else ""
        if not task_id:
            raise ModelGatewayError("MiniMax async TTS did not return task_id")

        return {
            "task_id": task_id,
            "usage_characters": int(data.get("usage_characters", 0) or 0),
            "trace_id": str(data.get("trace_id") or task_id),
        }

    async def _synthesize_async_and_download(
        self,
        request: TTSRequest,
        *,
        text: str,
    ) -> TTSResponse:
        """Finish MiniMax's asynchronous delivery cycle and return local bytes.

        On ``expired`` (MiniMax's temporary download URL lapsed before the task
        was polled) we resubmit once with the same voice/audio setting and a
        5-minute fast-fail deadline, so a single lapsed task doesn't doom a
        long chapter. Other failures propagate immediately.
        """
        started = time.monotonic()
        submission = await self._submit_async_long(request, text=text)
        task_id = str(submission["task_id"])
        try:
            result = await self._wait_for_async_result(task_id)
        except ModelGatewayError as exc:
            if "expired" not in str(exc).lower():
                raise
            # Resubmit once: the original task's temp URL expired, but the
            # text/voice/setting are still valid. Cap the retry at 5 minutes so
            # a persistently-expiring task fails fast rather than looping.
            _log.warning(
                "MiniMax async TTS task %s expired; resubmitting once with 5min cap",
                task_id,
            )
            submission = await self._submit_async_long(request, text=text)
            task_id = str(submission["task_id"])
            saved_timeout = self._async_timeout_s
            self._async_timeout_s = 300.0
            try:
                result = await self._wait_for_async_result(task_id)
            finally:
                self._async_timeout_s = saved_timeout
        file_id = str(result.get("file_id") or "")
        if not file_id:
            raise ModelGatewayError(f"MiniMax async TTS task {task_id} succeeded without a file_id")
        audio_data = await self._download_file_content(file_id)
        elapsed_ms = (time.monotonic() - started) * 1000
        usage_characters = int(submission.get("usage_characters", 0) or len(text))
        model_id = request.model_id or self._default_model
        voice_id = request.voice_id or _MINIMAX_DEFAULT_VOICE
        actual_format = request.output_format.lower()

        return TTSResponse(
            audio_data=audio_data,
            model_id=model_id,
            voice_id=voice_id,
            cost_usd=minimax_estimated_t2a_cost_usd(model_id, usage_characters),
            latency_ms=round(elapsed_ms, 2),
            content_type=f"audio/{actual_format}",
            audio_format=actual_format,
            metadata={"async_task_id": task_id, "async_file_id": file_id},
            take_evidence=ProviderTakeEvidence(
                trace_id=str(submission.get("trace_id") or task_id),
                provider_status=str(result.get("status") or "Success"),
                sample_rate=request.sample_rate,
                audio_size_bytes=len(audio_data),
                text_units=usage_characters,
                provider_extension={
                    "async": True,
                    "task_id": task_id,
                    "file_id": file_id,
                    "usage_characters": usage_characters,
                },
            ),
        )

    async def _wait_for_async_result(self, task_id: str) -> dict[str, Any]:
        """Poll below the provider's 10 QPS limit until a terminal state.

        Uses adaptive backoff: polls every ``async_poll_interval_s`` for the
        first ~10 polls, then 5s, then 15s after 5 minutes elapsed. This
        respects MiniMax's QPS budget on long tasks (which may run for tens of
        minutes) while keeping short-task latency tight.
        """
        deadline = time.monotonic() + self._async_timeout_s
        poll_count = 0
        while True:
            result = await self.get_task_result(task_id)
            status = str(result.get("status") or "").strip().lower()
            if status == "success":
                return result
            if status in {"failed", "expired"}:
                detail = str(result.get("error") or result.get("base_resp") or status)
                raise ModelGatewayError(
                    f"MiniMax async TTS task {task_id} ended as {status}: {detail}"
                )
            if status != "processing":
                raise ModelGatewayError(
                    f"MiniMax async TTS task {task_id} returned unknown status {status!r}"
                )
            if time.monotonic() >= deadline:
                raise ModelGatewayError(
                    f"MiniMax async TTS task {task_id} timed out after {self._async_timeout_s:.0f}s"
                )
            # Adaptive poll interval: tight at first, relax as the task drags on.
            elapsed = time.monotonic() - (deadline - self._async_timeout_s)
            poll_count += 1
            if poll_count <= 10 and elapsed < 60:
                interval = self._async_poll_interval_s
            elif elapsed < 300:
                interval = 5.0
            else:
                interval = 15.0
            await asyncio.sleep(interval)

    async def _download_file_content(self, file_id: str) -> bytes:
        """Download generated audio before MiniMax's temporary URL expires."""
        try:
            response = await self._client.get(
                "/files/retrieve_content",
                params={"file_id": file_id},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self._last_health_error = str(exc)
            self._raise_http_error(exc, "async audio download")
        except httpx.RequestError as exc:
            self._last_health_error = str(exc)
            raise ModelGatewayError(f"MiniMax async audio download failed: {exc}") from exc
        audio_data = response.content
        if not audio_data:
            raise ModelGatewayError(f"MiniMax file {file_id} returned empty audio")
        return audio_data

    async def get_task_result(self, task_id: str) -> dict[str, Any]:
        """Poll async TTS task status.

        Returns task status and file_id when completed.
        """
        try:
            response = await self._client.get(
                "/query/t2a_async_query_v2",
                params={"task_id": task_id},
            )
            response.raise_for_status()
            result: dict[str, Any] = response.json()
        except httpx.HTTPStatusError as exc:
            self._last_health_error = str(exc)
            self._raise_http_error(exc, "async speech query")
        except httpx.RequestError as exc:
            self._last_health_error = str(exc)
            raise ModelGatewayError(f"MiniMax async speech query failed: {exc}") from exc
        self._ensure_api_success(result, "async speech query")
        return result

    # ─── Voice Cloning ────────────────────────────────────────────────────

    async def clone_voice(self, request: VoiceCloneRequest) -> VoiceCloneResponse:
        """Clone a voice from reference audio.

        Args:
            request: VoiceCloneRequest with file_id and voice_id.

        Returns:
            VoiceCloneResponse with status and expiry info.
        """
        voice_id = self._normalize_clone_voice_id(request.voice_id)
        # A clone is tied to its intended T2A model.  The workspace already
        # resolves this model before cloning; preserving it in both the
        # provider request and response prevents a later default-model change
        # from silently synthesizing the character with a different voice
        # contract.
        model_id = request.model_id or self._default_model
        payload: dict[str, Any] = {
            "voice_id": voice_id,
            "file_id": int(request.file_id) if request.file_id.isdigit() else request.file_id,
            "model": model_id,
        }

        try:
            response = await self._client.post("/voice_clone", json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            _log.error("MiniMax voice clone error: %s", exc.response.text)
            return VoiceCloneResponse(
                voice_id=voice_id,
                model_id=model_id,
                provider=TTSProvider.MINIMAX,
                status=VoiceCloneStatus.FAILED,
                message=f"Clone failed: {exc.response.text}",
            )
        except httpx.RequestError as exc:
            return VoiceCloneResponse(
                voice_id=voice_id,
                model_id=model_id,
                provider=TTSProvider.MINIMAX,
                status=VoiceCloneStatus.FAILED,
                message=f"Clone request failed: {exc}",
            )

        try:
            self._ensure_api_success(data, "voice clone")
        except ModelGatewayError as exc:
            return VoiceCloneResponse(
                voice_id=voice_id,
                model_id=model_id,
                provider=TTSProvider.MINIMAX,
                status=VoiceCloneStatus.FAILED,
                message=str(exc),
            )

        # MiniMax deletes an unactivated clone if it is not used by a formal
        # T2A request within 168 hours.  This is an activation deadline, not a
        # recurring expiry for an already activated voice.
        activation_deadline = datetime.now(timezone.utc) + timedelta(days=7)

        return VoiceCloneResponse(
            voice_id=voice_id,
            model_id=model_id,
            provider=TTSProvider.MINIMAX,
            status=VoiceCloneStatus.READY,
            activation_deadline=activation_deadline,
            message="Voice cloned successfully",
        )

    # ─── Voice Design ─────────────────────────────────────────────────────

    async def design_voice(self, request: VoiceDesignRequest) -> VoiceDesignResponse:
        """Design a new voice from text description.

        Requires enterprise account. Returns a preview audio.
        """
        payload: dict[str, Any] = {
            "prompt": request.description,
            "preview_text": request.preview_text,
        }

        try:
            response = await self._client.post("/voice_design", json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            _log.error("MiniMax voice design error: %s", exc.response.text)
            return VoiceDesignResponse(
                provider=TTSProvider.MINIMAX,
                status=VoiceCloneStatus.FAILED,
                message=f"Design failed: {exc.response.text}",
            )
        except httpx.RequestError as exc:
            return VoiceDesignResponse(
                provider=TTSProvider.MINIMAX,
                status=VoiceCloneStatus.FAILED,
                message=f"Design request failed: {exc}",
            )

        try:
            self._ensure_api_success(data, "voice design")
        except ModelGatewayError as exc:
            return VoiceDesignResponse(
                provider=TTSProvider.MINIMAX,
                status=VoiceCloneStatus.FAILED,
                message=str(exc),
            )

        trial_audio = str(data.get("trial_audio") or "")
        try:
            preview_audio = bytes.fromhex(trial_audio) if trial_audio else b""
        except ValueError:
            preview_audio = b""

        voice_id = str(data.get("voice_id") or "")
        if not voice_id:
            return VoiceDesignResponse(
                provider=TTSProvider.MINIMAX,
                status=VoiceCloneStatus.FAILED,
                message="MiniMax voice design returned no voice_id",
            )

        activation_deadline = datetime.now(timezone.utc) + timedelta(days=7)

        return VoiceDesignResponse(
            voice_id=voice_id,
            provider=TTSProvider.MINIMAX,
            preview_audio_data=preview_audio,
            preview_audio_format="mp3",
            status=VoiceCloneStatus.READY,
            activation_deadline=activation_deadline,
            message="Voice designed successfully",
        )

    # ─── System Voices ────────────────────────────────────────────────────

    async def list_system_voices(
        self,
        *,
        gender: str | None = None,
        language: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query the account's current MiniMax system voice catalog."""
        try:
            response = await self._client.post("/get_voice", json={"voice_type": "system"})
            response.raise_for_status()
            data = response.json()
            self._ensure_api_success(data, "voice catalog")
        except Exception as exc:
            _log.warning("MiniMax voice catalog request failed: %s", exc)
            return []

        voices: list[dict[str, Any]] = []
        for item in data.get("system_voice", []) or []:
            if not isinstance(item, dict) or not item.get("voice_id"):
                continue
            descriptions = item.get("description") or []
            tags = [str(value) for value in descriptions] if isinstance(descriptions, list) else []
            voice_name = str(item.get("voice_name") or item["voice_id"])
            joined = " ".join(tags).lower()
            name_lower = voice_name.lower()
            combined_text = f"{name_lower} {joined}"
            inferred_gender = "neutral"
            if any(
                token in combined_text
                for token in (
                    "女性",
                    "女声",
                    "female",
                    "girl",
                    "woman",
                    "lady",
                    "queen",
                    "sister",
                    "auntie",
                )
            ):
                inferred_gender = "female"
            elif any(
                token in combined_text
                for token in ("男性", "男声", "male", "boy", "man", "gentleman")
            ):
                inferred_gender = "male"
            # Also infer gender from voice_id prefix patterns
            if inferred_gender == "neutral":
                vid = str(item["voice_id"]).lower()
                if vid.startswith("female") or "_female_" in vid:
                    inferred_gender = "female"
                elif vid.startswith("male") or "_male_" in vid:
                    inferred_gender = "male"
            inferred_age = _infer_minimax_age_hint(voice_name, joined, str(item["voice_id"]))
            inferred_personality = _infer_minimax_personality(voice_name, joined)
            inferred_language = _infer_minimax_voice_language(voice_name, joined)
            voice_description = " ".join(tags) if tags else voice_name
            voices.append(
                {
                    "voice_id": str(item["voice_id"]),
                    "name": voice_name,
                    "gender": inferred_gender,
                    "age_hint": inferred_age,
                    "personality": inferred_personality,
                    "voice_description": voice_description,
                    "tags": tags,
                    "language": inferred_language,
                }
            )

        # Filter by gender
        if gender:
            voices = [v for v in voices if v.get("gender") == gender.lower()]

        # Filter by language — exclude voices whose inferred language conflicts
        # with the target project language (e.g. Korean voices for a Chinese novel).
        if language:
            voices = [
                v
                for v in voices
                if _voice_matches_language_filter(
                    str(v.get("name") or ""),
                    str(v.get("voice_description") or ""),
                    language,
                )
            ]

        return voices[:limit]

    # ─── Health Check ─────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """Check MiniMax API connectivity."""
        try:
            response = await self._client.post("/get_voice", json={"voice_type": "system"})
            if response.status_code < 500:
                self._ensure_api_success(response.json(), "health check")
            self._last_health_error = ""
            return response.status_code < 500
        except Exception as exc:
            self._last_health_error = str(exc)
            return False

    async def shutdown(self) -> None:
        """Close HTTP client."""
        await self._client.aclose()

    # ─── Helpers ──────────────────────────────────────────────────────────

    def _inject_emotion_tags(self, text: str, tags: list[str], *, model_id: str) -> str:
        """Inject MiniMax emotion tags into text.

        Tags are T2A syntax for Speech 2.8 only.  On every other selected
        model we preserve the source sentence rather than sending punctuation
        that might be pronounced aloud.
        """
        if not minimax_supports_interjections(model_id):
            return text
        valid_tags = minimax_valid_interjections(tags)
        if not valid_tags:
            return text

        # Legacy callers may still supply unordered tags.  New dubbing scripts
        # place actions inside ``text`` at director-selected positions, so do
        # not inject duplicates already present in the synthesis text.
        missing = [tag for tag in valid_tags if tag not in text]
        if not missing:
            return text
        return f"{' '.join(missing)} {text}"

    @staticmethod
    def _validate_minimax_t2a_request(request: TTSRequest) -> None:
        """Reject formats that the current HTTP T2A contract does not accept."""

        output_format = request.output_format.strip().lower()
        if output_format not in MINIMAX_NON_STREAMING_AUDIO_FORMATS:
            allowed = ", ".join(sorted(MINIMAX_NON_STREAMING_AUDIO_FORMATS))
            raise ValueError(
                f"MiniMax HTTP T2A output_format must be one of {allowed}; got {request.output_format!r}"
            )

    @staticmethod
    def _language_boost(value: str) -> str:
        """Normalize generic language annotations to MiniMax's published enum."""

        return minimax_language_boost(value)

    @staticmethod
    def _build_voice_setting(
        request: TTSRequest,
        *,
        model_id: str,
        voice_id: str,
    ) -> dict[str, Any]:
        """Build a model-safe voice setting without changing cast identity."""

        voice_setting: dict[str, Any] = {
            "voice_id": voice_id,
            "speed": round(request.speed, 2),
            "vol": round(request.volume, 2),
            "pitch": request.pitch,
        }
        emotion = minimax_native_emotion(request.emotion)
        # All currently supported T2A model families expose nine native
        # emotions, but fluent/whisper are restricted to speech-2.6 series.
        # Story-only labels are projected before they reach the vendor so
        # values such as ``nostalgic`` cannot leak into the request contract.
        native_emotion_allowed = bool(request.metadata.get("native_emotion_allowed"))
        if emotion and (native_emotion_allowed or not request.metadata.get("voice_identity_lock")):
            # Enforce model-specific emotion restrictions (e.g. whisper only on 2.6)
            if minimax_emotion_allowed_for_model(emotion, model_id):
                voice_setting["emotion"] = emotion
        return voice_setting

    @staticmethod
    def _normalize_clone_voice_id(value: str) -> str:
        """Return an ID that satisfies MiniMax's ASCII and length contract."""
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{6,254}[A-Za-z0-9]", value):
            return value
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
        return f"nf-{digest}"

    @staticmethod
    def _raise_http_error(exc: httpx.HTTPStatusError, operation: str) -> NoReturn:
        status_code = exc.response.status_code
        detail = exc.response.text or exc.response.reason_phrase
        if status_code == 429:
            raise RateLimitError(
                f"MiniMax {operation} HTTP 429: {detail}",
                provider="minimax",
                provider_code=status_code,
            ) from exc
        raise ModelGatewayError(
            f"MiniMax {operation} HTTP {status_code}: {detail}",
            is_transient=status_code >= 500,
            provider="minimax",
            provider_code=status_code,
        ) from exc

    @staticmethod
    def _ensure_api_success(payload: Any, operation: str) -> None:
        if not isinstance(payload, dict):
            raise ModelGatewayError(f"MiniMax {operation} returned a non-object response")
        base_resp = payload.get("base_resp") or {}
        status_code = int(base_resp.get("status_code", 0) or 0)
        if status_code != 0:
            message = str(base_resp.get("status_msg") or "unknown error")
            if status_code == 1002 or "rate limit" in message.lower():
                raise RateLimitError(
                    f"MiniMax {operation} failed ({status_code}): {message}",
                    provider="minimax",
                    provider_code=status_code,
                )
            raise ModelGatewayError(f"MiniMax {operation} failed ({status_code}): {message}")


# ─── Language inference from MiniMax voice names ─────────────────────────────

# MiniMax system voice names follow the pattern "Language_..." or
# "Language (Variant)_...", e.g. "Chinese (Mandarin)_Reliable_Executive",
# "Korean_Confident_Boy", "English_Gentle_Lady".
_LANGUAGE_PREFIX_MAP: list[tuple[str, tuple[str, ...]]] = [
    ("zh", ("chinese", "中文", "普通话", "mandarin")),
    ("yue", ("cantonese", "粤语", "yue")),
    ("en", ("english", "英语", "英文")),
    ("ja", ("japanese", "日语", "日文")),
    ("ko", ("korean", "韩语", "韩文", "한국")),
    ("vi", ("vietnamese", "越南语", "越南")),
    ("th", ("thai", "泰语", "泰文")),
    ("id", ("indonesian", "印尼语", "印尼")),
    ("es", ("spanish", "西班牙语")),
    ("fr", ("french", "法语")),
    ("de", ("german", "德语")),
    ("ru", ("russian", "俄语")),
    ("ar", ("arabic", "阿拉伯语")),
    ("pt", ("portuguese", "葡萄牙语")),
    ("tr", ("turkish", "土耳其语")),
    ("hi", ("hindi", "印地语")),
    ("ms", ("malay", "马来语")),
    ("fil", ("filipino", "菲律宾语")),
]

# Mapping from generic language codes to MiniMax voice-name prefixes for filtering.
_LANGUAGE_FILTER_TOKENS: dict[str, tuple[str, ...]] = {
    "zh": ("chinese", "中文"),
    "zh-cn": ("chinese", "中文"),
    "zh-hans": ("chinese", "中文"),
    "yue": ("cantonese", "粤语"),
    "zh-yue": ("cantonese", "粤语"),
    "en": ("english",),
    "ja": ("japanese",),
    "ko": ("korean",),
    "vi": ("vietnamese",),
    "th": ("thai",),
    "id": ("indonesian",),
    "es": ("spanish",),
    "fr": ("french",),
    "de": ("german",),
    "ru": ("russian",),
    "ar": ("arabic",),
    "pt": ("portuguese",),
    "tr": ("turkish",),
    "hi": ("hindi",),
    "ms": ("malay",),
    "fil": ("filipino",),
}


def _infer_minimax_voice_language(voice_name: str, description_text: str) -> str:
    """Infer the language code from a MiniMax system voice name/description.

    Returns a generic language code (e.g. 'zh', 'ko', 'en') or '' if unknown.
    """
    combined = f"{voice_name} {description_text}".lower()
    for lang_code, tokens in _LANGUAGE_PREFIX_MAP:
        if any(token in combined for token in tokens):
            return lang_code
    return ""


def _voice_matches_language_filter(voice_name: str, description_text: str, language: str) -> bool:
    """Return whether a voice passes the target language filter.

    A voice passes if:
    - No language filter is specified (language is empty/None)
    - The voice's inferred language matches the filter
    - The voice's language cannot be inferred (give benefit of the doubt)
    """
    if not language:
        return True
    normalized = language.strip().lower().replace("_", "-")
    filter_tokens = _LANGUAGE_FILTER_TOKENS.get(normalized)
    if filter_tokens is None:
        # Unknown language code — do not filter
        return True
    inferred = _infer_minimax_voice_language(voice_name, description_text)
    if not inferred:
        # Cannot determine voice language — allow through
        return True
    # Check if the inferred language matches the filter
    # Normalize both to base language code for comparison
    base_filter = normalized.split("-")[0]
    base_inferred = inferred.split("-")[0]
    # Special case: 'yue' is a variant of Chinese
    if base_filter == "zh" and inferred in ("zh", "yue"):
        return True
    if base_filter == "yue" and inferred == "yue":
        return True
    return base_inferred == base_filter


# ─── Catalog metadata inference helpers ─────────────────────────────────────

_AGE_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("child", ("child", "boy", "girl", "男童", "女童", "聪明男", "可爱男", "萌萌女", "卡通")),
    (
        "young",
        (
            "青年",
            "young",
            "youth",
            "少女",
            "青涩",
            "大学",
            "学弟",
            "学长",
            "学妹",
            "学姐",
            "teen",
            "student",
        ),
    ),
    (
        "middle",
        (
            "中年",
            "middle",
            "成熟",
            "高管",
            "御姐",
            "阅历",
            "boss",
            "manager",
            "anchor",
            "executive",
        ),
    ),
    ("senior", ("老年", "elder", "senior", "奶奶", "大爷", "大婶", "花甲", "gentle_butler")),
]

_PERSONALITY_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("沉稳克制", ("沉稳", "可靠", "reliable", "steady", "calm", "serious", "composed")),
    ("温柔亲和", ("温柔", "温暖", "gentle", "warm", "kind", "sweet", "soft", "caring")),
    (
        "活泼明亮",
        ("活泼", "开朗", "lively", "cheerful", "bright", "energetic", "playful", "optimistic"),
    ),
    ("低沉厚实", ("低沉", "浑厚", "deep", "resonant", "rich", "mature")),
    ("冷峻疏离", ("冷", "cold", "distant", "aloof", "lengdan", "冷淡")),
    (
        "威严有压迫感",
        ("霸道", "威严", "dominant", "bossy", "authoritative", "commanding", "strict"),
    ),
    ("机敏狡黠", ("俏皮", "witty", "sly", "playful", "qiaopi")),
    ("清脆轻盈", ("清脆", "crisp", "light", "tianmei", "甜美")),
    (
        "稚嫩天真",
        ("稚嫩", "天真", "innocent", "childlike", "cute", "lovely", "聪明", "可爱", "萌萌"),
    ),
]


def _infer_minimax_age_hint(voice_name: str, description_text: str, voice_id: str = "") -> str:
    """Infer an age bucket from MiniMax voice_name, description text, and voice_id.

    Returns one of: 'child', 'young', 'middle', 'senior', or '' if unknown.
    When name/description yield no match, falls back to voice_id prefix patterns
    so system voices always carry a usable age hint for matching.
    """
    combined = f"{voice_name} {description_text}".lower()
    for bucket, tokens in _AGE_PATTERNS:
        if any(token in combined for token in tokens):
            return bucket
    # Fallback: infer from voice_id prefix patterns for system voices
    vid = str(voice_id or "").lower()
    if vid.startswith("male-qn-") or "_boy" in vid:
        return "young"
    if vid.startswith("female-") or "_girl" in vid:
        return "young"
    if "executive" in vid or "news" in vid or "anchor" in vid or "boss" in vid:
        return "middle"
    if "child" in vid or "kid" in vid:
        return "child"
    if "elder" in vid or "senior" in vid or "butler" in vid:
        return "senior"
    return ""


def _infer_minimax_personality(voice_name: str, description_text: str) -> str:
    """Infer personality keywords from MiniMax voice_name and description text.

    Returns a comma-separated string of matched personality labels, or '' if none.
    """
    combined = f"{voice_name} {description_text}".lower()
    matched: list[str] = []
    for label, tokens in _PERSONALITY_PATTERNS:
        if any(token in combined for token in tokens):
            matched.append(label)
    return "，".join(matched[:3]) if matched else ""
