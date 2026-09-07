"""Native adapter for FunAudioLLM CosyVoice's official FastAPI runtime."""

from __future__ import annotations

import io
import time
import wave
from pathlib import Path
from typing import Any

import httpx

from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.tts.assets.reference_voice_store import ReferenceVoiceStore
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


class CosyVoiceTTSAdapter(TTSProviderAdapter):
    """Call the upstream `/inference_*` endpoints without an OpenAI shim."""

    _MODES = {"sft", "zero_shot", "cross_lingual", "instruct2"}

    def __init__(
        self,
        *,
        base_url: str,
        default_model: str,
        mode: str,
        voice_store_dir: str,
        sample_rate: int = 22050,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._mode = mode if mode in self._MODES else "instruct2"
        self._sample_rate = sample_rate
        self._store = ReferenceVoiceStore(voice_store_dir)
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=httpx.Timeout(180.0))
        self._last_health_error = ""

    @property
    def provider_name(self) -> str:
        return "cosyvoice"

    @property
    def provider_type(self) -> TTSProvider:
        return TTSProvider.COSYVOICE

    @property
    def capabilities(self) -> TTSProviderCapabilities:
        return TTSProviderCapabilities(
            provider=TTSProvider.COSYVOICE,
            voice_clone=True,
            voice_design=False,
            system_voice_catalog=True,
            local_reference_audio=True,
            synthesis_features={TTSFeature.INSTRUCTION_CONTROL},
        )

    @property
    def last_health_error(self) -> str:
        return self._last_health_error

    async def prepare_clone_source(self, reference: str) -> str:
        if not Path(reference).expanduser().is_file():
            raise ValueError("CosyVoice requires a local reference audio file")
        return reference

    async def clone_voice(self, request: VoiceCloneRequest) -> VoiceCloneResponse:
        try:
            self._store.save_reference(
                request.voice_id,
                request.file_id,
                mode=self._mode,
                # The existing canonical brief is appropriate as an instruct2
                # style instruction; zero-shot users should replace it with a
                # verbatim reference transcript in the profile file.
                instruction=request.clone_prompt,
                reference_text=request.clone_prompt,
                model=self._default_model,
            )
            return VoiceCloneResponse(
                voice_id=request.voice_id,
                provider=self.provider_type,
                status=VoiceCloneStatus.READY,
                message="CosyVoice reference voice registered",
            )
        except Exception as exc:
            return VoiceCloneResponse(
                voice_id=request.voice_id,
                provider=self.provider_type,
                status=VoiceCloneStatus.FAILED,
                message=f"CosyVoice reference registration failed: {exc}",
            )

    async def synthesize(self, request: TTSRequest) -> TTSResponse:
        self._validate_request(request)
        start = time.monotonic()
        profile = self._store.load(request.voice_id)
        try:
            if profile is None:
                response = await self._client.post(
                    "/inference_sft", data={"tts_text": request.text, "spk_id": request.voice_id}
                )
            else:
                response = await self._synthesize_reference(request, profile)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            self._last_health_error = str(exc)
            raise ModelGatewayError(f"CosyVoice synthesis failed: {exc}") from exc
        pcm = response.content
        if not pcm:
            raise ModelGatewayError("CosyVoice returned empty PCM audio")
        return TTSResponse(
            audio_data=self._pcm_to_wav(pcm),
            model_id=request.model_id or self._default_model,
            voice_id=request.voice_id,
            latency_ms=round((time.monotonic() - start) * 1000, 2),
            content_type="audio/wav",
            audio_format="wav",
        )

    async def _synthesize_reference(
        self, request: TTSRequest, profile: dict[str, Any]
    ) -> httpx.Response:
        reference = Path(str(profile.get("reference_audio_path") or ""))
        if not reference.is_file():
            raise ModelGatewayError("CosyVoice reference audio is missing; clone the voice again")
        mode = str(profile.get("mode") or self._mode)
        with reference.open("rb") as handle:
            files = {"prompt_wav": (reference.name, handle, "application/octet-stream")}
            if mode == "cross_lingual":
                return await self._client.post(
                    "/inference_cross_lingual", data={"tts_text": request.text}, files=files
                )
            if mode == "zero_shot":
                text = str(profile.get("reference_text") or "").strip()
                if not text:
                    raise ModelGatewayError(
                        "CosyVoice zero_shot requires the reference audio transcript"
                    )
                return await self._client.post(
                    "/inference_zero_shot",
                    data={"tts_text": request.text, "prompt_text": text},
                    files=files,
                )
            instruction = str(profile.get("instruction") or "请以自然、贴合角色的方式表达。")
            tone_hint = str(request.metadata.get("tone_hint") or "").strip()
            emotion = str(request.emotion or "neutral").strip()
            instruction = (
                f"{instruction} 本句情绪：{emotion}；"
                f"语速约 {request.speed:.2f} 倍，音量约 {request.volume:.2f} 倍，"
                f"音高偏移 {request.pitch:+d} 半音。"
            )
            if tone_hint:
                instruction += f"语气：{tone_hint}。"
            return await self._client.post(
                "/inference_instruct2",
                data={"tts_text": request.text, "instruct_text": instruction},
                files=files,
            )

    async def list_system_voices(self, **_: Any) -> list[dict[str, Any]]:
        return [
            {
                "voice_id": "中文女",
                "name": "中文女（CosyVoice SFT）",
                "gender": "female",
                "tags": ["sft"],
            },
            {
                "voice_id": "中文男",
                "name": "中文男（CosyVoice SFT）",
                "gender": "male",
                "tags": ["sft"],
            },
        ]

    async def health_check(self) -> bool:
        try:
            response = await self._client.get("/docs")
            self._last_health_error = ""
            return response.status_code < 500
        except httpx.HTTPError as exc:
            self._last_health_error = str(exc)
            return False

    async def shutdown(self) -> None:
        await self._client.aclose()

    def _pcm_to_wav(self, pcm: bytes) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(self._sample_rate)
            handle.writeframes(pcm)
        return buffer.getvalue()
