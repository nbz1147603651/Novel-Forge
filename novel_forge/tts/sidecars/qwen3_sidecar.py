"""Isolated local HTTP runtime for the official Qwen3-TTS package.

Keep this process in a dedicated Python/CUDA environment.  The main Novel
Forge process communicates with it through a deliberately small, versioned
HTTP contract and never imports Qwen, Torch, or FlashAttention itself.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import gc
import hashlib
import io
import json
import os
import sys
import tempfile
import time
import wave
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from novel_forge.core.config import get_application_assets_dir
from novel_forge.persistence.filesystem import atomic_write_bytes
from novel_forge.tts.assets.reference_voice_store import ReferenceVoiceStore

DEFAULT_FORMAL_MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
DEFAULT_DESIGN_MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
DEFAULT_CLONE_MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
_MAX_REFERENCE_BYTES = 32 * 1024 * 1024
_ALLOWED_REFERENCE_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg"}


def _default_voice_store_dir() -> str:
    return str(get_application_assets_dir() / "tts-voices" / "qwen3")


_CUSTOM_VOICES: tuple[dict[str, Any], ...] = (
    {"voice_id": "Vivian", "name": "Vivian", "gender": "female", "tags": ["青年", "中文", "明亮"]},
    {"voice_id": "Serena", "name": "Serena", "gender": "female", "tags": ["青年", "中文", "温柔"]},
    {
        "voice_id": "Uncle_Fu",
        "name": "Uncle Fu",
        "gender": "male",
        "tags": ["成熟", "中文", "低沉"],
    },
    {"voice_id": "Dylan", "name": "Dylan", "gender": "male", "tags": ["青年", "北京方言"]},
    {"voice_id": "Eric", "name": "Eric", "gender": "male", "tags": ["青年", "四川方言"]},
    {"voice_id": "Ryan", "name": "Ryan", "gender": "male", "tags": ["英文", "节奏感"]},
    {"voice_id": "Aiden", "name": "Aiden", "gender": "male", "tags": ["英文", "明朗"]},
    {"voice_id": "Ono_Anna", "name": "Ono Anna", "gender": "female", "tags": ["日文", "俏皮"]},
    {"voice_id": "Sohee", "name": "Sohee", "gender": "female", "tags": ["韩文", "温暖"]},
)


@dataclass(frozen=True)
class Qwen3SidecarConfig:
    """Runtime parameters that remain outside the Novel Forge desktop process."""

    device: str = "auto"
    dtype: str = "auto"
    use_flash_attention: bool = False
    allow_remote_models: bool = False
    voice_store_dir: str = field(default_factory=_default_voice_store_dir)
    model_registry_path: str = ""
    formal_model: str = DEFAULT_FORMAL_MODEL
    design_model: str = DEFAULT_DESIGN_MODEL
    clone_model: str = DEFAULT_CLONE_MODEL
    api_key: str = ""
    max_resident_models: int = 1
    idle_unload_s: float = 300.0


def resolve_qwen_device(
    device: str,
    dtype: str,
    *,
    cuda_available: bool,
    mps_available: bool,
) -> tuple[str, str]:
    """Resolve portable defaults without loading a model or allocating an accelerator."""

    resolved_device = device.strip().lower() or "auto"
    if resolved_device == "auto":
        if cuda_available:
            resolved_device = "cuda:0"
        elif mps_available:
            resolved_device = "mps"
        else:
            resolved_device = "cpu"
    resolved_dtype = dtype.strip().lower() or "auto"
    if resolved_dtype == "auto":
        resolved_dtype = "bfloat16" if resolved_device.startswith("cuda") else "float32"
    return resolved_device, resolved_dtype


class SpeechRequest(BaseModel):
    """OpenAI Speech-compatible request with Qwen-specific optional direction."""

    model: str = Field(default=DEFAULT_FORMAL_MODEL)
    input: str = Field(min_length=1, max_length=12_000)
    voice: str = Field(default="Vivian")
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    response_format: str = Field(default="wav")
    language: str = Field(default="Auto")
    instruct: str = Field(default="", max_length=1200)
    emotion: str = Field(default="neutral", max_length=80)
    tone_hint: str = Field(default="", max_length=300)
    temperature: float | None = Field(default=None, gt=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    max_new_tokens: int | None = Field(default=None, ge=64, le=8192)


class VoiceDesignRequest(BaseModel):
    """Design a reusable role voice by VoiceDesign then Base prompt caching."""

    model: str = Field(default=DEFAULT_DESIGN_MODEL)
    clone_model: str = Field(default=DEFAULT_CLONE_MODEL)
    description: str = Field(min_length=1, max_length=2400)
    preview_text: str = Field(min_length=1, max_length=1000)
    language: str = Field(default="Auto", max_length=40)


class ModelInstallRequest(BaseModel):
    plugin_id: str = ""
    model_id: str = Field(min_length=1, max_length=300)
    revision: str = "main"
    local_path: str = Field(min_length=1)
    accept_license: bool = False


class ModelDeleteRequest(BaseModel):
    plugin_id: str = ""
    model_id: str = Field(min_length=1, max_length=300)


class SelfTestRequest(BaseModel):
    model_id: str = ""


class Qwen3Runtime:
    """Lazy Qwen model manager plus durable, recreatable clone profiles."""

    def __init__(self, config: Qwen3SidecarConfig) -> None:
        self._config = config
        self._models: dict[str, Any] = {}
        self._clone_prompts: dict[str, Any] = {}
        self._store = ReferenceVoiceStore(config.voice_store_dir)
        self._model_registry_path = (
            Path(config.model_registry_path).expanduser()
            if config.model_registry_path
            else Path(config.voice_store_dir).expanduser().parent / "models.json"
        )
        self._registered_models = self._load_model_registry()
        self._lock = asyncio.Lock()
        self._last_activity_at = time.monotonic()

    async def synthesize(self, request: SpeechRequest) -> tuple[bytes, str, str]:
        async with self._lock:
            self._touch_activity()
            try:
                return await asyncio.to_thread(self._synthesize_sync, request)
            finally:
                self._touch_activity()

    async def clone_voice(
        self,
        *,
        voice_id: str,
        reference_path: Path,
        reference_transcript: str,
        x_vector_only_mode: bool,
        clone_model: str = "",
    ) -> None:
        async with self._lock:
            self._touch_activity()
            try:
                await asyncio.to_thread(
                    self._clone_voice_sync,
                    voice_id,
                    reference_path,
                    reference_transcript,
                    x_vector_only_mode,
                    clone_model,
                )
            finally:
                self._touch_activity()

    async def design_voice(self, request: VoiceDesignRequest) -> tuple[str, bytes]:
        async with self._lock:
            self._touch_activity()
            try:
                return await asyncio.to_thread(self._design_voice_sync, request)
            finally:
                self._touch_activity()

    async def warmup(self) -> None:
        async with self._lock:
            self._touch_activity()
            try:
                await asyncio.to_thread(self._warmup_sync)
            finally:
                self._touch_activity()

    async def release_models(self) -> int:
        """Release resident model and prompt tensors under the inference lock."""
        async with self._lock:
            return await asyncio.to_thread(self._release_models_sync)

    async def release_if_idle(self) -> int:
        """Release accelerator memory after the configured idle window."""
        idle_unload_s = max(0.0, float(self._config.idle_unload_s))
        if idle_unload_s <= 0 or time.monotonic() - self._last_activity_at < idle_unload_s:
            return 0
        async with self._lock:
            if time.monotonic() - self._last_activity_at < idle_unload_s:
                return 0
            return await asyncio.to_thread(self._release_models_sync)

    def _load_model(self, model_id: str) -> Any:
        registered_path = self._registered_models.get(model_id, "")
        if registered_path:
            registered = Path(registered_path)
            if not registered.is_dir():
                self._registered_models.pop(model_id, None)
                self._save_model_registry()
                raise RuntimeError(f"模型登记路径已失效，请在模型中心重新安装：{model_id}")
            resolved = str(registered)
        elif self._config.allow_remote_models:
            resolved = model_id
        else:
            raise RuntimeError(f"模型尚未通过模型中心安装：{model_id}")
        if resolved in self._models:
            model = self._models.pop(resolved)
            self._models[resolved] = model
            return model
        self._evict_for_new_model()
        try:
            import torch  # type: ignore[import-not-found]
            from qwen_tts import Qwen3TTSModel  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "qwen-tts is not installed in the sidecar environment; run `pip install -U qwen-tts`"
            ) from exc
        device_name, dtype_name = resolve_qwen_device(
            self._config.device,
            self._config.dtype,
            cuda_available=bool(torch.cuda.is_available()),
            mps_available=bool(
                getattr(getattr(torch, "backends", None), "mps", None)
                and torch.backends.mps.is_available()
            ),
        )
        dtype = {
            "float16": torch.float16,
            "bfloat16": torch.bfloat16,
            "float32": torch.float32,
        }.get(dtype_name)
        if dtype is None:
            raise ValueError("dtype must be float16, bfloat16, or float32")
        kwargs: dict[str, Any] = {"device_map": device_name, "dtype": dtype}
        if self._config.use_flash_attention:
            kwargs["attn_implementation"] = "flash_attention_2"
        model = Qwen3TTSModel.from_pretrained(resolved, **kwargs)
        self._models[resolved] = model
        return model

    def list_models(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for model_id, local_path in sorted(self._registered_models.items()):
            path = Path(local_path)
            result.append(
                {
                    "model_id": model_id,
                    "local_path": local_path,
                    "size_bytes": self._directory_size(path),
                    "installed": path.is_dir() and (path / "config.json").is_file(),
                }
            )
        return result

    def register_model(self, model_id: str, local_path: str) -> None:
        path = Path(local_path).expanduser().resolve()
        if not path.is_dir() or not (path / "config.json").is_file():
            raise ValueError("注册的 Qwen3-TTS 本地目录不完整。")
        self._registered_models[model_id] = str(path)
        self._save_model_registry()

    def unregister_model(self, model_id: str) -> None:
        resolved = self._registered_models.pop(model_id, "")
        if resolved:
            removed = self._models.pop(resolved, None)
            if removed is not None:
                self._clone_prompts.clear()
                self._release_accelerator_cache()
        self._save_model_registry()

    def health(self) -> dict[str, Any]:
        try:
            import qwen_tts  # noqa: F401
            import torch
        except ImportError as exc:
            raise RuntimeError(f"Qwen3-TTS 运行时依赖不完整：{exc}") from exc
        device, dtype = resolve_qwen_device(
            self._config.device,
            self._config.dtype,
            cuda_available=bool(torch.cuda.is_available()),
            mps_available=bool(
                getattr(getattr(torch, "backends", None), "mps", None)
                and torch.backends.mps.is_available()
            ),
        )
        formal_path = self._registered_models.get(self._config.formal_model, "")
        return {
            "status": "ok",
            "healthy": True,
            "model_ready": bool(formal_path and Path(formal_path).is_dir()),
            "checked_model": self._config.formal_model,
            "device": device,
            "dtype": dtype,
            "resident_models": len(self._models),
            "max_resident_models": max(1, int(self._config.max_resident_models)),
            "idle_unload_s": max(0.0, float(self._config.idle_unload_s)),
        }

    def _touch_activity(self) -> None:
        self._last_activity_at = time.monotonic()

    def _evict_for_new_model(self) -> None:
        limit = max(1, int(self._config.max_resident_models))
        evicted = False
        while len(self._models) >= limit:
            oldest = next(iter(self._models))
            self._models.pop(oldest, None)
            evicted = True
        if evicted:
            # Clone prompts may contain tensors owned by an evicted Base model.
            self._clone_prompts.clear()
            self._release_accelerator_cache()

    def _release_models_sync(self) -> int:
        released = len(self._models)
        had_resources = bool(self._models or self._clone_prompts)
        self._models.clear()
        self._clone_prompts.clear()
        if had_resources:
            self._release_accelerator_cache()
        return released

    @staticmethod
    def _release_accelerator_cache() -> None:
        gc.collect()
        torch = sys.modules.get("torch")
        if torch is None:
            return
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except (AttributeError, RuntimeError):
            pass
        try:
            mps = getattr(getattr(torch, "mps", None), "empty_cache", None)
            if callable(mps):
                mps()
        except RuntimeError:
            pass

    def _load_model_registry(self) -> dict[str, str]:
        try:
            payload = json.loads(self._model_registry_path.read_text(encoding="utf-8"))
            return {
                str(key): str(value)
                for key, value in payload.items()
                if isinstance(key, str) and isinstance(value, str)
            }
        except (OSError, ValueError, AttributeError):
            return {}

    def _save_model_registry(self) -> None:
        self._model_registry_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(
            self._model_registry_path,
            json.dumps(self._registered_models, ensure_ascii=False, indent=2).encode("utf-8"),
        )

    @staticmethod
    def _directory_size(path: Path) -> int:
        try:
            return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
        except OSError:
            return 0

    def _synthesize_sync(self, request: SpeechRequest) -> tuple[bytes, str, str]:
        profile = self._store.load(request.voice)
        if profile and profile.get("mode") == "clone":
            model_id = str(profile.get("clone_model") or self._config.clone_model)
            model = self._load_model(model_id)
            prompt = self._clone_prompt(request.voice, model, profile)
            wavs, sample_rate = model.generate_voice_clone(
                text=request.input,
                language=self._language(request.language),
                voice_clone_prompt=prompt,
            )
            return self._wav_bytes(wavs[0], sample_rate), model_id, request.voice

        model_id = request.model or self._config.formal_model
        model = self._load_model(model_id)
        speaker = (
            request.voice
            if request.voice in {item["voice_id"] for item in _CUSTOM_VOICES}
            else "Vivian"
        )
        instruct = request.instruct or self._fallback_instruction(request)
        generation_kwargs = {
            key: value
            for key, value in {
                "temperature": request.temperature,
                "top_p": request.top_p,
                "max_new_tokens": request.max_new_tokens,
            }.items()
            if value is not None
        }
        wavs, sample_rate = model.generate_custom_voice(
            text=request.input,
            language=self._language(request.language),
            speaker=speaker,
            instruct=instruct,
            **generation_kwargs,
        )
        return self._wav_bytes(wavs[0], sample_rate), model_id, speaker

    def _clone_voice_sync(
        self,
        voice_id: str,
        reference_path: Path,
        reference_transcript: str,
        x_vector_only_mode: bool,
        clone_model: str,
    ) -> None:
        clone_model_id = clone_model or self._config.clone_model
        model = self._load_model(clone_model_id)
        prompt = model.create_voice_clone_prompt(
            ref_audio=str(reference_path),
            ref_text=reference_transcript or None,
            x_vector_only_mode=x_vector_only_mode,
        )
        self._store.save_reference(
            voice_id,
            str(reference_path),
            mode="clone",
            reference_text=reference_transcript,
            x_vector_only_mode=x_vector_only_mode,
            clone_model=clone_model_id,
        )
        self._clone_prompts[voice_id] = prompt

    def _design_voice_sync(self, request: VoiceDesignRequest) -> tuple[str, bytes]:
        voice_id = self._design_voice_id(request)
        design_model = self._load_model(request.model or self._config.design_model)
        wavs, sample_rate = design_model.generate_voice_design(
            text=request.preview_text,
            language=self._language(request.language),
            instruct=request.description,
        )
        preview_audio = self._wav_bytes(wavs[0], sample_rate)
        del design_model
        if max(1, int(self._config.max_resident_models)) == 1:
            # VoiceDesign and Base are separate 1.7B models.  On the light
            # budget, free the former before loading the latter.
            self._release_models_sync()
        reference_path = self._store.asset_path(voice_id, ".wav")
        atomic_write_bytes(reference_path, preview_audio)
        clone_model_id = request.clone_model or self._config.clone_model
        clone_model = self._load_model(clone_model_id)
        prompt = clone_model.create_voice_clone_prompt(
            ref_audio=str(reference_path),
            ref_text=request.preview_text,
            x_vector_only_mode=False,
        )
        self._store.update(
            voice_id,
            mode="clone",
            reference_audio_path=str(reference_path),
            reference_text=request.preview_text,
            x_vector_only_mode=False,
            clone_model=clone_model_id,
            design_model=request.model or self._config.design_model,
            design_description=request.description,
        )
        self._clone_prompts[voice_id] = prompt
        return voice_id, preview_audio

    def _clone_prompt(self, voice_id: str, model: Any, profile: dict[str, Any]) -> Any:
        prompt = self._clone_prompts.get(voice_id)
        if prompt is not None:
            return prompt
        reference = str(profile.get("reference_audio_path") or "")
        if not Path(reference).is_file():
            raise RuntimeError("The local reference audio for this Qwen3-TTS voice is missing")
        prompt = model.create_voice_clone_prompt(
            ref_audio=reference,
            ref_text=str(profile.get("reference_text") or "") or None,
            x_vector_only_mode=bool(profile.get("x_vector_only_mode", False)),
        )
        self._clone_prompts[voice_id] = prompt
        return prompt

    def _warmup_sync(self) -> None:
        model = self._load_model(self._config.formal_model)
        model.generate_custom_voice(
            text="健康检查。",
            language="Chinese",
            speaker="Vivian",
            instruct="自然、清晰地说。",
        )

    @staticmethod
    def _language(value: str) -> str:
        return (
            value
            if value
            in {
                "Chinese",
                "English",
                "Japanese",
                "Korean",
                "German",
                "French",
                "Russian",
                "Portuguese",
                "Spanish",
                "Italian",
            }
            else "Auto"
        )

    @staticmethod
    def _fallback_instruction(request: SpeechRequest) -> str:
        parts = [f"以{request.emotion}的情绪表达"] if request.emotion != "neutral" else []
        if request.tone_hint:
            parts.append(f"语气为{request.tone_hint}")
        if abs(request.speed - 1.0) >= 0.05:
            parts.append(f"语速约为正常的{request.speed:.2f}倍")
        return "；".join(parts)

    @staticmethod
    def _design_voice_id(request: VoiceDesignRequest) -> str:
        raw = f"{request.model}|{request.clone_model}|{request.description}|{request.preview_text}"
        return f"qwen3-design-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"

    @staticmethod
    def _wav_bytes(samples: Any, sample_rate: int) -> bytes:
        audio = np.asarray(samples, dtype=np.float32)
        if audio.ndim > 1:
            audio = np.mean(audio, axis=-1)
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(pcm)
        return buffer.getvalue()


def create_app(config: Qwen3SidecarConfig | None = None) -> FastAPI:
    """Create a sidecar app; importing it does not load Qwen or GPU dependencies."""
    runtime = Qwen3Runtime(config or Qwen3SidecarConfig())

    async def idle_reaper() -> None:
        interval = min(30.0, max(5.0, runtime._config.idle_unload_s / 2))  # noqa: SLF001
        while True:
            await asyncio.sleep(interval)
            await runtime.release_if_idle()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        reaper: asyncio.Task[None] | None = None
        if runtime._config.idle_unload_s > 0:  # noqa: SLF001
            reaper = asyncio.create_task(idle_reaper())
        try:
            yield
        finally:
            if reaper is not None:
                reaper.cancel()
                with suppress(asyncio.CancelledError):
                    await reaper
            await runtime.release_models()

    app = FastAPI(title="Novel Forge Qwen3-TTS sidecar", version="1.0", lifespan=lifespan)

    async def require_token(authorization: str = Header(default="")) -> None:
        expected = runtime._config.api_key  # noqa: SLF001 - closure-level auth policy
        if not expected:
            return
        if authorization != f"Bearer {expected}":
            raise HTTPException(status_code=401, detail="Invalid sidecar bearer token")

    @app.get("/v1/capabilities", dependencies=[Depends(require_token)])
    async def capabilities() -> dict[str, Any]:
        return {
            "provider": "qwen3",
            "synthesis": True,
            "voice_clone": True,
            "voice_design": True,
            "system_voice_catalog": True,
            "local_reference_audio": True,
            "synthesis_features": ["speed", "emotion", "instruction_control"],
        }

    @app.get("/v1/voices", dependencies=[Depends(require_token)])
    async def voices() -> dict[str, Any]:
        return {"voices": list(_CUSTOM_VOICES)}

    @app.get("/v1/health", dependencies=[Depends(require_token)])
    async def health() -> dict[str, Any]:
        try:
            return await asyncio.to_thread(runtime.health)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/v1/version", dependencies=[Depends(require_token)])
    async def version() -> dict[str, Any]:
        return {
            "runtime": "qwen3-tts",
            "version": os.getenv("NOVEL_FORGE_SIDECAR_RUNTIME_VERSION", "2"),
            "protocol_version": 1,
        }

    @app.get("/v1/models", dependencies=[Depends(require_token)])
    async def models() -> dict[str, Any]:
        return {"models": runtime.list_models()}

    @app.post("/v1/runtime/release", dependencies=[Depends(require_token)])
    async def release_runtime_models() -> dict[str, Any]:
        return {"ok": True, "released_models": await runtime.release_models()}

    @app.post("/v1/models/install", dependencies=[Depends(require_token)])
    async def install_model(request: ModelInstallRequest) -> dict[str, Any]:
        try:
            runtime.register_model(request.model_id, request.local_path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True, "model_id": request.model_id, "local_path": request.local_path}

    @app.post("/v1/models/delete", dependencies=[Depends(require_token)])
    async def delete_model(request: ModelDeleteRequest) -> dict[str, Any]:
        runtime.unregister_model(request.model_id)
        return {"ok": True, "model_id": request.model_id}

    @app.post("/v1/self-test", dependencies=[Depends(require_token)])
    async def self_test(request: SelfTestRequest) -> dict[str, Any]:
        model_id = request.model_id or runtime._config.formal_model  # noqa: SLF001
        try:
            speech_request = SpeechRequest(
                model=model_id,
                input="健康检查。",
                voice="Vivian",
                language="Chinese",
                instruct="自然、清晰地说。",
            )
            audio, _, _ = await runtime.synthesize(speech_request)
        except Exception as exc:
            return {"ok": False, "model_id": model_id, "detail": str(exc)}
        return {"ok": bool(audio), "model_id": model_id, "audio_bytes": len(audio)}

    @app.post("/v1/audio/speech", dependencies=[Depends(require_token)])
    async def speech(request: SpeechRequest) -> Response:
        try:
            audio, model_id, voice_id = await runtime.synthesize(request)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return Response(
            content=audio,
            media_type="audio/wav",
            headers={
                "X-Novel-Forge-Audio-Format": "wav",
                "X-Novel-Forge-Model": model_id,
                "X-Novel-Forge-Voice": voice_id,
            },
        )

    @app.post("/v1/voices/clone", dependencies=[Depends(require_token)])
    async def clone_voice(
        file: UploadFile = File(...),  # noqa: B008 - FastAPI dependency declaration
        voice_id: str = Form(..., min_length=1, max_length=160),
        reference_transcript: str = Form(default="", max_length=4000),
        x_vector_only_mode: bool = Form(default=False),
        authorized: bool = Form(default=False),
        clone_model: str = Form(default="", max_length=300),
    ) -> dict[str, Any]:
        if not authorized:
            raise HTTPException(
                status_code=403, detail="Reference-speaker authorization is required"
            )
        suffix = Path(file.filename or "reference.wav").suffix.lower()
        if suffix not in _ALLOWED_REFERENCE_SUFFIXES:
            raise HTTPException(status_code=422, detail="Unsupported reference audio format")
        raw = await file.read(_MAX_REFERENCE_BYTES + 1)
        if not raw or len(raw) > _MAX_REFERENCE_BYTES:
            raise HTTPException(
                status_code=422, detail="Reference audio must be between 1 byte and 32 MB"
            )
        if not reference_transcript.strip() and not x_vector_only_mode:
            raise HTTPException(
                status_code=422,
                detail="A reference transcript is required unless x_vector_only_mode is explicitly enabled",
            )
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(raw)
        try:
            await runtime.clone_voice(
                voice_id=voice_id,
                reference_path=temp_path,
                reference_transcript=reference_transcript.strip(),
                x_vector_only_mode=x_vector_only_mode,
                clone_model=clone_model,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        finally:
            temp_path.unlink(missing_ok=True)
        quality_note = (
            "已建立逐字转写克隆提示"
            if reference_transcript.strip()
            else "仅使用声纹向量，质量可能下降"
        )
        return {"voice_id": voice_id, "message": f"Qwen3-TTS clone ready: {quality_note}"}

    @app.post("/v1/voices/design", dependencies=[Depends(require_token)])
    async def design_voice(request: VoiceDesignRequest) -> dict[str, Any]:
        try:
            voice_id, preview = await runtime.design_voice(request)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {
            "voice_id": voice_id,
            "preview_audio": base64.b64encode(preview).decode("ascii"),
            "preview_audio_format": "wav",
            "message": "Qwen3-TTS VoiceDesign reference created and cached as a reusable Base prompt",
        }

    return app


def main() -> None:
    """Run the sidecar after installing ``qwen-tts`` in an isolated environment."""
    parser = argparse.ArgumentParser(description="Run the Novel Forge Qwen3-TTS local sidecar")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8011, type=int)
    parser.add_argument("--device", default=os.getenv("QWEN3_TTS_DEVICE", "auto"))
    parser.add_argument("--dtype", default=os.getenv("QWEN3_TTS_DTYPE", "auto"))
    parser.add_argument(
        "--voice-store", default=os.getenv("QWEN3_TTS_VOICE_STORE", _default_voice_store_dir())
    )
    parser.add_argument("--model-registry", default=os.getenv("QWEN3_TTS_MODEL_REGISTRY", ""))
    parser.add_argument("--api-key", default=os.getenv("QWEN3_TTS_API_KEY", ""))
    parser.add_argument(
        "--formal-model",
        default=os.getenv("QWEN3_TTS_FORMAL_MODEL", DEFAULT_FORMAL_MODEL),
    )
    parser.add_argument(
        "--design-model",
        default=os.getenv("QWEN3_TTS_DESIGN_MODEL", DEFAULT_DESIGN_MODEL),
    )
    parser.add_argument(
        "--clone-model",
        default=os.getenv("QWEN3_TTS_CLONE_MODEL", DEFAULT_CLONE_MODEL),
    )
    parser.add_argument(
        "--max-resident-models",
        type=int,
        default=int(os.getenv("QWEN3_TTS_MAX_RESIDENT_MODELS", "1")),
    )
    parser.add_argument(
        "--idle-unload-s",
        type=float,
        default=float(os.getenv("QWEN3_TTS_IDLE_UNLOAD_S", "300")),
    )
    parser.add_argument("--flash-attention", action="store_true")
    args = parser.parse_args()
    config = Qwen3SidecarConfig(
        device=args.device,
        dtype=args.dtype,
        use_flash_attention=args.flash_attention,
        allow_remote_models=os.getenv("QWEN3_TTS_ALLOW_REMOTE_MODELS", "").strip().lower()
        in {"1", "true", "yes", "on"},
        voice_store_dir=args.voice_store,
        model_registry_path=args.model_registry,
        formal_model=args.formal_model,
        design_model=args.design_model,
        clone_model=args.clone_model,
        api_key=args.api_key,
        max_resident_models=max(1, args.max_resident_models),
        idle_unload_s=max(0.0, args.idle_unload_s),
    )
    import uvicorn

    uvicorn.run(create_app(config), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
