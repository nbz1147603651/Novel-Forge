"""Native in-process adapter for MyShell OpenVoice V2 + MeloTTS."""

from __future__ import annotations

import asyncio
import gc
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

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


class OpenVoiceTTSAdapter(TTSProviderAdapter):
    """Persist tone-color embeddings and synthesize through the official SDK.

    OpenVoice has no official HTTP synthesis API.  Keeping this adapter
    in-process is deliberate: it consumes the official ``ToneColorConverter``
    and ``MeloTTS`` APIs directly while importing heavyweight dependencies only
    when the user actually selects this provider.
    """

    def __init__(
        self,
        *,
        default_model: str,
        checkpoint_dir: str,
        voice_store_dir: str,
        device: str = "auto",
        language: str = "ZH",
    ) -> None:
        self._default_model = default_model
        self._checkpoint_dir = Path(checkpoint_dir).expanduser()
        self._store = ReferenceVoiceStore(voice_store_dir)
        self._device = device
        self._language = language.upper()
        self._runtime_lock = threading.Lock()
        self._runtime: dict[str, Any] | None = None
        self._last_health_error = ""

    @property
    def provider_name(self) -> str:
        return "openvoice"

    @property
    def provider_type(self) -> TTSProvider:
        return TTSProvider.OPENVOICE

    @property
    def capabilities(self) -> TTSProviderCapabilities:
        return TTSProviderCapabilities(
            provider=TTSProvider.OPENVOICE,
            voice_clone=True,
            # OpenVoice clones a legal reference timbre; it must not advertise
            # text-only voice design as a capability it does not provide.
            voice_design=False,
            system_voice_catalog=True,
            local_reference_audio=True,
            synthesis_features={TTSFeature.SPEED},
        )

    @property
    def last_health_error(self) -> str:
        return self._last_health_error

    async def prepare_clone_source(self, reference: str) -> str:
        if not Path(reference).expanduser().is_file():
            raise ValueError("OpenVoice requires a local reference audio file")
        return reference

    async def clone_voice(self, request: VoiceCloneRequest) -> VoiceCloneResponse:
        try:
            await asyncio.to_thread(self._clone_sync, request)
            return VoiceCloneResponse(
                voice_id=request.voice_id,
                provider=self.provider_type,
                status=VoiceCloneStatus.READY,
                message="OpenVoice tone-color embedding created",
            )
        except Exception as exc:
            self._last_health_error = str(exc)
            return VoiceCloneResponse(
                voice_id=request.voice_id,
                provider=self.provider_type,
                status=VoiceCloneStatus.FAILED,
                message=f"OpenVoice clone failed: {exc}",
            )

    def _clone_sync(self, request: VoiceCloneRequest) -> None:
        profile = self._store.save_reference(
            request.voice_id,
            request.file_id,
            language=self._language,
            model=self._default_model,
            instruction=request.clone_prompt,
        )
        runtime = self._load_runtime()
        embedding, _ = runtime["extractor"].get_se(
            profile["reference_audio_path"],
            runtime["converter"],
            target_dir=str(self._store.asset_path(request.voice_id, "").parent),
            vad=True,
        )
        embedding_path = self._store.asset_path(request.voice_id, ".pt")
        embedding_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = embedding_path.with_suffix(".tmp")
        runtime["torch"].save(embedding, tmp)
        os.replace(tmp, embedding_path)
        self._store.update(request.voice_id, embedding_path=str(embedding_path))

    async def synthesize(self, request: TTSRequest) -> TTSResponse:
        self._validate_request(request)
        start = time.monotonic()
        try:
            audio = await asyncio.to_thread(self._synthesize_sync, request)
        except Exception as exc:
            self._last_health_error = str(exc)
            raise ModelGatewayError(f"OpenVoice synthesis failed: {exc}") from exc
        return TTSResponse(
            audio_data=audio,
            model_id=request.model_id or self._default_model,
            voice_id=request.voice_id,
            latency_ms=round((time.monotonic() - start) * 1000, 2),
            content_type="audio/wav",
            audio_format="wav",
        )

    def _synthesize_sync(self, request: TTSRequest) -> bytes:
        profile = self._store.load(request.voice_id)
        runtime = self._load_runtime()
        model = self._get_melo_model(runtime)
        speaker_name, speaker_id = next(iter(model.hps.data.spk2id.items()))
        if profile is None:
            # The catalog's base voice remains useful for narration/preview
            # before an authorized character reference is uploaded.
            with tempfile.TemporaryDirectory(prefix="novel-forge-openvoice-") as temp_dir:
                output = Path(temp_dir) / "base.wav"
                model.tts_to_file(request.text, speaker_id, str(output), speed=request.speed)
                return output.read_bytes()
        if not Path(str(profile.get("embedding_path") or "")).is_file():
            raise ValueError("OpenVoice voice embedding is missing; clone the reference again")
        source_key = speaker_name.lower().replace("_", "-")
        source_path = self._checkpoint_dir / "base_speakers" / "ses" / f"{source_key}.pth"
        if not source_path.is_file():
            raise FileNotFoundError(f"OpenVoice base speaker embedding missing: {source_path}")
        with tempfile.TemporaryDirectory(prefix="novel-forge-openvoice-") as temp_dir:
            base_audio = Path(temp_dir) / "base.wav"
            output = Path(temp_dir) / "output.wav"
            model.tts_to_file(request.text, speaker_id, str(base_audio), speed=request.speed)
            source_embedding = runtime["torch"].load(source_path, map_location=runtime["device"])
            target_embedding = runtime["torch"].load(
                profile["embedding_path"], map_location=runtime["device"]
            )
            runtime["converter"].convert(
                audio_src_path=str(base_audio),
                src_se=source_embedding,
                tgt_se=target_embedding,
                output_path=str(output),
                message="@NovelForge",
            )
            return output.read_bytes()

    def _load_runtime(self) -> dict[str, Any]:
        with self._runtime_lock:
            if self._runtime is not None:
                return self._runtime
            try:
                import torch  # type: ignore[import-not-found]
                from melo.api import TTS  # type: ignore[import-not-found]
                from openvoice import se_extractor  # type: ignore[import-not-found]
                from openvoice.api import ToneColorConverter  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError(
                    "OpenVoice V2 runtime is not installed. Install OpenVoice and MeloTTS in the app environment."
                ) from exc
            device = (
                "cuda" if self._device == "auto" and torch.cuda.is_available() else self._device
            )
            converter_dir = self._checkpoint_dir / "converter"
            converter = ToneColorConverter(str(converter_dir / "config.json"), device=device)
            converter.load_ckpt(str(converter_dir / "checkpoint.pth"))
            self._runtime = {
                "torch": torch,
                "TTS": TTS,
                "extractor": se_extractor,
                "converter": converter,
                "device": device,
                "models": {},
            }
            return self._runtime

    def _get_melo_model(self, runtime: dict[str, Any]) -> Any:
        models: dict[str, Any] = runtime["models"]
        if self._language not in models:
            models[self._language] = runtime["TTS"](
                language=self._language, device=runtime["device"]
            )
        return models[self._language]

    async def list_system_voices(self, **_: Any) -> list[dict[str, Any]]:
        return [
            {
                "voice_id": "openvoice-base",
                "name": "OpenVoice 基础声线",
                "gender": "neutral",
                "tags": ["base", self._language],
            }
        ]

    async def health_check(self) -> bool:
        try:
            await asyncio.to_thread(self._load_runtime)
            self._last_health_error = ""
            return True
        except Exception as exc:
            self._last_health_error = str(exc)
            return False

    async def shutdown(self) -> None:
        """Drop in-process Torch models when the owning desktop worker ends."""
        await asyncio.to_thread(self._release_runtime)

    def _release_runtime(self) -> None:
        with self._runtime_lock:
            runtime = self._runtime
            self._runtime = None
        if not runtime:
            return
        models = runtime.get("models")
        if isinstance(models, dict):
            models.clear()
        torch = runtime.get("torch")
        runtime.clear()
        gc.collect()
        try:
            if torch is not None and torch.cuda.is_available():
                torch.cuda.empty_cache()
        except (AttributeError, RuntimeError):
            pass
        try:
            empty_mps_cache = getattr(getattr(torch, "mps", None), "empty_cache", None)
            if callable(empty_mps_cache):
                empty_mps_cache()
        except RuntimeError:
            pass
