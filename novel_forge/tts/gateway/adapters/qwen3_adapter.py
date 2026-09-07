"""Native contract adapter for the bundled Qwen3-TTS local sidecar.

The sidecar deliberately owns Qwen's CUDA/PyTorch runtime.  This adapter owns
only the stable HTTP contract and maps Novel Forge's segment direction into
Qwen's natural-language instruction control.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

import httpx

from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.tts.gateway.base import TTSProviderAdapter
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


class Qwen3TTSAdapter(TTSProviderAdapter):
    """Call the Qwen3-TTS sidecar with official model-role routing.

    ``formal_model`` and ``preview_model`` are CustomVoice models.  The
    sidecar switches a designed or cloned stable voice ID to ``clone_model``
    automatically, because official VoiceDesign → Base prompt reuse is the
    workflow that preserves a character's identity over a full novel.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        formal_model: str,
        preview_model: str,
        design_model: str,
        clone_model: str,
        connect_timeout_s: float = 5.0,
        read_timeout_s: float = 300.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._formal_model = formal_model
        self._preview_model = preview_model
        self._design_model = design_model
        self._clone_model = clone_model
        self._last_health_error = ""
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=httpx.Timeout(
                connect=connect_timeout_s,
                read=read_timeout_s,
                write=60.0,
                pool=10.0,
            ),
        )

    @property
    def provider_name(self) -> str:
        return "qwen3"

    @property
    def provider_type(self) -> TTSProvider:
        return TTSProvider.QWEN3

    @property
    def capabilities(self) -> TTSProviderCapabilities:
        return TTSProviderCapabilities(
            provider=TTSProvider.QWEN3,
            voice_clone=True,
            voice_design=True,
            system_voice_catalog=True,
            local_reference_audio=True,
            synthesis_features={
                TTSFeature.SPEED,
                TTSFeature.EMOTION,
                TTSFeature.INSTRUCTION_CONTROL,
            },
        )

    @property
    def last_health_error(self) -> str:
        return self._last_health_error

    async def prepare_clone_source(self, reference: str) -> str:
        if not Path(reference).expanduser().is_file():
            raise ValueError("Qwen3-TTS Base requires a local authorized reference audio file")
        return reference

    async def synthesize(self, request: TTSRequest) -> TTSResponse:
        self._validate_request(request)
        model_id = request.model_id or self._formal_model
        extension = request.metadata.get("platform_extension") or {}
        if not isinstance(extension, dict):
            extension = {}
        payload = {
            "model": model_id,
            "input": request.text,
            "voice": request.voice_id or "Vivian",
            "speed": round(request.speed, 2),
            "response_format": request.output_format,
            "language": self._language(request.language_boost),
            "instruct": self._instruction(request),
            "emotion": request.emotion,
            "tone_hint": str(request.metadata.get("tone_hint") or ""),
        }
        for field in ("temperature", "top_p", "max_new_tokens"):
            if extension.get(field) is not None:
                payload[field] = extension[field]
        started = time.monotonic()
        try:
            response = await self._client.post("/audio/speech", json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self._last_health_error = self._response_error_detail(exc.response)[:500]
            raise ModelGatewayError(
                f"Qwen3-TTS 合成失败：{self._last_health_error}",
                is_transient=exc.response.status_code >= 500,
                context={
                    "provider": self.provider_name,
                    "status_code": exc.response.status_code,
                    "error_type": type(exc).__name__,
                },
            ) from exc
        except httpx.RequestError as exc:
            self._last_health_error = self._request_error_detail(exc)
            raise ModelGatewayError(
                self._last_health_error,
                is_transient=True,
                context={
                    "provider": self.provider_name,
                    "endpoint": f"{self._base_url}/audio/speech",
                    "error_type": type(exc).__name__,
                },
            ) from exc
        if not response.content:
            raise ModelGatewayError("Qwen3-TTS returned empty audio")
        audio_format = response.headers.get("X-Novel-Forge-Audio-Format", "wav").lower()
        if audio_format == "wav" and not response.content.startswith(b"RIFF"):
            raise ModelGatewayError("Qwen3-TTS returned an invalid WAV payload")
        return TTSResponse(
            audio_data=response.content,
            model_id=response.headers.get("X-Novel-Forge-Model", model_id),
            voice_id=response.headers.get("X-Novel-Forge-Voice", request.voice_id),
            latency_ms=round((time.monotonic() - started) * 1000, 2),
            content_type=response.headers.get("content-type", "audio/wav"),
            audio_format=audio_format if audio_format in {"wav", "mp3", "flac"} else "wav",
            take_evidence=ProviderTakeEvidence(
                provider_status="completed",
                audio_size_bytes=len(response.content),
                provider_extension={
                    "runtime_model": response.headers.get("X-Novel-Forge-Model", model_id),
                    "runtime_voice": response.headers.get("X-Novel-Forge-Voice", request.voice_id),
                    "language": payload["language"],
                    "instruction_controlled": bool(payload["instruct"]),
                },
            ),
        )

    async def clone_voice(self, request: VoiceCloneRequest) -> VoiceCloneResponse:
        if not request.authorized:
            return VoiceCloneResponse(
                voice_id=request.voice_id,
                provider=self.provider_type,
                status=VoiceCloneStatus.FAILED,
                message="Qwen3-TTS requires explicit authorization from the reference speaker",
            )
        reference = Path(request.file_id).expanduser()
        if not reference.is_file():
            return VoiceCloneResponse(
                voice_id=request.voice_id,
                provider=self.provider_type,
                status=VoiceCloneStatus.FAILED,
                message="Qwen3-TTS requires a local reference audio file",
            )
        try:
            with reference.open("rb") as handle:
                response = await self._client.post(
                    "/voices/clone",
                    data={
                        "voice_id": request.voice_id,
                        "reference_transcript": request.reference_transcript,
                        "x_vector_only_mode": str(not bool(request.reference_transcript)).lower(),
                        "authorized": "true",
                        "clone_model": self._clone_model,
                    },
                    files={"file": (reference.name, handle, "application/octet-stream")},
                )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return VoiceCloneResponse(
                voice_id=request.voice_id,
                provider=self.provider_type,
                status=VoiceCloneStatus.FAILED,
                message=f"Qwen3-TTS voice clone failed: {exc}",
            )
        return VoiceCloneResponse(
            voice_id=str(payload.get("voice_id") or request.voice_id),
            provider=self.provider_type,
            status=VoiceCloneStatus.READY,
            message=str(payload.get("message") or "Qwen3-TTS voice clone ready"),
        )

    async def design_voice(self, request: VoiceDesignRequest) -> VoiceDesignResponse:
        try:
            response = await self._client.post(
                "/voices/design",
                json={
                    "model": self._design_model,
                    "clone_model": self._clone_model,
                    "description": request.description,
                    "preview_text": request.preview_text,
                    "language": self._language(request.language),
                },
            )
            response.raise_for_status()
            payload = response.json()
            encoded_audio = str(payload.get("preview_audio") or "")
            preview = base64.b64decode(encoded_audio) if encoded_audio else b""
        except (httpx.HTTPError, ValueError) as exc:
            return VoiceDesignResponse(
                provider=self.provider_type,
                status=VoiceCloneStatus.FAILED,
                message=f"Qwen3-TTS voice design failed: {exc}",
            )
        return VoiceDesignResponse(
            voice_id=str(payload.get("voice_id") or ""),
            provider=self.provider_type,
            preview_audio_data=preview,
            preview_audio_format=str(payload.get("preview_audio_format") or "wav"),
            status=VoiceCloneStatus.READY,
            message=str(payload.get("message") or "Qwen3-TTS designed voice ready"),
        )

    async def list_system_voices(
        self,
        *,
        gender: str | None = None,
        language: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        try:
            response = await self._client.get("/voices")
            response.raise_for_status()
            payload = response.json()
            voices = payload.get("voices", payload.get("data", []))
            if not isinstance(voices, list):
                return []
        except (httpx.HTTPError, ValueError):
            return []
        result = [item for item in voices if isinstance(item, dict) and item.get("voice_id")]
        if gender:
            result = [item for item in result if item.get("gender") == gender.lower()]
        return result[:limit]

    async def health_check(self) -> bool:
        try:
            response = await self._client.get("/health")
            response.raise_for_status()
            payload = response.json()
            healthy = bool(payload.get("model_ready", False))
            self._last_health_error = (
                "" if healthy else str(payload.get("detail") or "model not ready")
            )
            return healthy
        except (httpx.HTTPError, ValueError) as exc:
            self._last_health_error = str(exc)
            return False

    async def shutdown(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _response_error_detail(response: httpx.Response) -> str:
        """Extract FastAPI's useful detail instead of exposing an opaque JSON blob."""
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            payload = None
        if isinstance(payload, dict) and payload.get("detail"):
            detail = payload["detail"]
            if isinstance(detail, str):
                return detail.strip()
            return json.dumps(detail, ensure_ascii=False)
        raw = response.text.strip()
        return raw or f"HTTP {response.status_code}"

    def _request_error_detail(self, exc: httpx.RequestError) -> str:
        """Turn often-empty httpx transport errors into actionable local guidance."""
        endpoint = f"{self._base_url}/audio/speech"
        if isinstance(exc, httpx.ConnectError):
            return (
                f"无法连接 Qwen3-TTS 本地服务（{endpoint}）。"
                "请在“平台设置 → 音频模型中心”安装并启动 Qwen3-TTS 运行时后重试。"
            )
        if isinstance(exc, httpx.ConnectTimeout):
            return (
                f"连接 Qwen3-TTS 本地服务超时（{endpoint}）。"
                "请检查运行时是否正在启动，以及 8011 端口是否被其他程序占用。"
            )
        if isinstance(exc, httpx.ReadTimeout):
            return (
                "Qwen3-TTS 长时间未返回结果。模型可能仍在加载，或正被自动组建、"
                "正式合成等本地音频任务占用；请等待当前任务结束后重试。"
            )
        if isinstance(exc, httpx.PoolTimeout):
            return "Qwen3-TTS 本地请求队列已满，请等待当前音频任务结束后重试。"
        detail = str(exc).strip() or type(exc).__name__
        return f"Qwen3-TTS 本地请求失败：{detail}（{endpoint}）"

    @staticmethod
    def _language(value: str) -> str:
        normalized = value.strip().lower()
        if normalized in {"chinese", "zh", "zh-cn"}:
            return "Chinese"
        if normalized in {"chinese,yue", "cantonese", "yue"}:
            return "Cantonese"
        if normalized in {"english", "en"}:
            return "English"
        aliases = {
            "ja": "Japanese",
            "ko": "Korean",
            "fr": "French",
            "de": "German",
            "es": "Spanish",
            "pt": "Portuguese",
            "ru": "Russian",
        }
        return aliases.get(normalized, value if value and normalized != "auto" else "Auto")

    @staticmethod
    def _instruction(request: TTSRequest) -> str:
        """Keep direction natural-language based, matching Qwen's official API."""
        parts: list[str] = []
        if request.metadata.get("voice_identity_lock"):
            parts.append("始终保持指定说话人的年龄感、音高中心和音色，情绪不得改变声线身份")
        if request.emotion and request.emotion != "neutral":
            parts.append(f"以{request.emotion}的情绪表达")
        tone_hint = str(request.metadata.get("tone_hint") or "").strip()
        if tone_hint:
            parts.append(f"语气为{tone_hint}")
        sub_emotion = str(request.metadata.get("sub_emotion") or "").strip()
        if sub_emotion:
            parts.append(f"情绪渐变到{sub_emotion}")
        direction = request.metadata.get("vocal_direction") or {}
        if isinstance(direction, dict):
            style = str(direction.get("delivery_style") or "natural")
            style_label = {
                "intimate": "贴近耳语般的亲密表达",
                "narrative": "有声书叙事风格",
                "conversational": "自然交谈风格",
                "dramatic": "戏剧化但不过度的表达",
                "broadcast": "清晰稳定的播音表达",
            }.get(style, "")
            if style_label:
                parts.append(style_label)
            energy = float(direction.get("energy", 0.5))
            articulation = float(direction.get("articulation", 0.6))
            breathiness = float(direction.get("breathiness", 0.2))
            resonance = float(direction.get("resonance", 0.0))
            tension = float(direction.get("tension", 0.3))
            intimacy = float(direction.get("intimacy", 0.5))
            if energy >= 0.7:
                parts.append("能量充沛")
            elif energy <= 0.3:
                parts.append("收敛能量")
            if articulation >= 0.78:
                parts.append("吐字清晰利落")
            elif articulation <= 0.35:
                parts.append("吐字松弛自然")
            if breathiness >= 0.55:
                parts.append("保留明显气声")
            if resonance >= 0.35:
                parts.append("声线偏明亮")
            elif resonance <= -0.35:
                parts.append("声线偏暗沉")
            if tension >= 0.65:
                parts.append("保持紧绷感")
            if intimacy >= 0.72:
                parts.append("拉近与听众的距离")
            intent = str(direction.get("intent") or "").strip()
            if intent:
                parts.append(f"表达意图：{intent}")
        stress_words = request.metadata.get("stress_words") or []
        if isinstance(stress_words, list) and stress_words:
            parts.append(f"强调{'、'.join(str(item) for item in stress_words)}")
        if request.pronunciation_overrides:
            parts.append(f"按读音提示：{'；'.join(request.pronunciation_overrides)}")
        context = request.metadata.get("performance_context") or {}
        if isinstance(context, dict) and any(context.values()):
            previous_text = str(context.get("previous_text") or "").strip()[:60]
            previous_speaker = str(context.get("previous_speaker") or "").strip()[:20]
            next_text = str(context.get("next_text") or "").strip()[:60]
            next_speaker = str(context.get("next_speaker") or "").strip()[:20]
            parts.append("承接相邻句的呼吸和节奏，保持自然对话连续性，不要像独立播报句")
            if previous_text:
                parts.append(
                    f"上一句由{previous_speaker or '旁白'}说：{previous_text}，只承接节奏不要复述"
                )
            if next_text:
                parts.append(f"下一句由{next_speaker or '旁白'}说：{next_text}，自然留出衔接")
        extension = request.metadata.get("platform_extension") or {}
        if isinstance(extension, dict):
            explicit = str(extension.get("instruct") or "").strip()
            if explicit:
                parts.append(explicit)
        if abs(request.speed - 1.0) >= 0.05:
            parts.append(f"语速约为正常的{request.speed:.2f}倍")
        return "；".join(parts)
