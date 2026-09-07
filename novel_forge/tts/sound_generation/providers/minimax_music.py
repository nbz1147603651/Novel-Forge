"""MiniMax Music API adapter for instrumental background music.

API Reference: https://platform.minimaxi.com/docs/api-reference/music-generation

Supported models (2026-07):
- music-3.0 (recommended): latest generation, superior quality
- music-2.6: previous generation
- music-cover: cover generation from reference audio
- music-3.0-free / music-2.6-free / music-cover-free: free-tier variants (RPM 3)

Endpoint: POST /v1/music_generation
Auth: Bearer token
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import tempfile
from pathlib import Path
from typing import Any

import httpx

from novel_forge.tts.platform.minimax_contract import (
    MINIMAX_MUSIC_ALL_MODELS,
    MINIMAX_MUSIC_AUDIO_FORMATS,
    MINIMAX_MUSIC_SAMPLE_RATES,
)
from novel_forge.tts.runtime.audio_runtime import ffmpeg_executable
from novel_forge.tts.sound_generation.providers.base import (
    SoundGenerationError,
    SoundGenerationProvider,
)
from novel_forge.tts.sound_generation.schemas import (
    GeneratedSoundAsset,
    SoundGenerationKind,
    SoundGenerationRequest,
)


class MiniMaxMusicProvider(SoundGenerationProvider):
    """Generate long instrumental music or ambience through MiniMax Music.

    Supports music-3.0 (recommended), music-2.6, and their free-tier variants.
    Full audio_setting (sample_rate, bitrate, format) and output_format (url/hex)
    are passed to the API per official documentation.
    """

    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str,
        default_model: str,
        timeout_s: float = 180.0,
    ) -> None:
        if not api_key:
            raise ValueError("MiniMax Music API key is required")
        self._api_key = api_key
        self._endpoint = endpoint
        self._default_model = default_model
        self._timeout_s = timeout_s

    @property
    def provider_id(self) -> str:
        return "minimax_music"

    async def generate(self, request: SoundGenerationRequest) -> GeneratedSoundAsset:
        if request.kind not in {SoundGenerationKind.BGM, SoundGenerationKind.SOUNDSCAPE}:
            raise SoundGenerationError("MiniMax Music only accepts BGM or persistent soundscapes")

        model_id = request.model_id or self._default_model
        # Validate model against official catalog
        if model_id not in MINIMAX_MUSIC_ALL_MODELS:
            raise SoundGenerationError(
                f"MiniMax Music model '{model_id}' is not in the official catalog: "
                f"{sorted(MINIMAX_MUSIC_ALL_MODELS)}"
            )

        # Build full audio_setting per official API spec
        output_format = request.output_format.lower()
        if output_format not in MINIMAX_MUSIC_AUDIO_FORMATS:
            output_format = "mp3"  # safe fallback
        sample_rate = 44100  # default high quality
        if hasattr(request, "sample_rate") and request.sample_rate in MINIMAX_MUSIC_SAMPLE_RATES:
            sample_rate = request.sample_rate
        bitrate = 256000  # default high quality
        if hasattr(request, "bitrate") and request.bitrate:
            bitrate = request.bitrate

        payload: dict[str, Any] = {
            "model": model_id,
            "prompt": request.prompt,
            # Both roles must be free of vocals.  Soundscape prompts are
            # authored as stable ambience beds, while BGM prompts are music.
            "is_instrumental": True,
            "audio_setting": {
                "sample_rate": sample_rate,
                "bitrate": bitrate,
                "format": output_format,
            },
            # Request URL output for efficient transfer; fallback to hex handled below
            "output_format": "url",
        }
        if request.seed is not None:
            payload["seed"] = request.seed

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(
                    self._endpoint,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                response.raise_for_status()
                response_payload = response.json()
                self._ensure_success(response_payload)
                audio_data = await self._extract_audio(client, response_payload)
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise SoundGenerationError(f"MiniMax Music HTTP error: {detail}") from exc
        except httpx.RequestError as exc:
            raise SoundGenerationError(f"MiniMax Music request failed: {exc}") from exc

        if not audio_data:
            raise SoundGenerationError("MiniMax Music returned empty audio")
        extra_info = response_payload.get("extra_info") or {}
        duration_ms = int(extra_info.get("music_duration") or 0)
        original_duration_ms = duration_ms
        if duration_ms > request.duration_ms + 250:
            audio_data = await self._trim_audio(
                audio_data,
                audio_format=request.output_format,
                duration_ms=request.duration_ms,
            )
            duration_ms = request.duration_ms
        return GeneratedSoundAsset(
            provider=self.provider_id,
            model_id=model_id,
            audio_format=request.output_format,
            duration_ms=duration_ms,
            audio_data=audio_data,
            metadata={
                "extra_info": extra_info,
                "original_duration_ms": original_duration_ms,
                "trimmed_to_request": original_duration_ms > duration_ms,
            },
        )

    @staticmethod
    async def _trim_audio(
        audio_data: bytes,
        *,
        audio_format: str,
        duration_ms: int,
    ) -> bytes:
        """Trim MiniMax's full-song response to the cue duration requested by the mix plan."""

        codec = {
            "wav": "pcm_s16le",
            "mp3": "libmp3lame",
            "flac": "flac",
        }.get(audio_format, "copy")
        with tempfile.TemporaryDirectory(prefix="novel-forge-minimax-music-") as temp_dir:
            root = Path(temp_dir)
            source = root / f"source.{audio_format}"
            target = root / f"trimmed.{audio_format}"
            source.write_bytes(audio_data)
            process = await asyncio.create_subprocess_exec(
                ffmpeg_executable(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-t",
                f"{max(0.5, duration_ms / 1000):.3f}",
                "-map_metadata",
                "-1",
                "-c:a",
                codec,
                str(target),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()
            if process.returncode != 0 or not target.is_file() or not target.stat().st_size:
                detail = stderr.decode("utf-8", errors="replace")[-800:]
                raise SoundGenerationError(f"MiniMax Music trim failed: {detail}")
            return target.read_bytes()

    @staticmethod
    def _ensure_success(payload: dict[str, Any]) -> None:
        base_resp = payload.get("base_resp") or {}
        code = base_resp.get("status_code") if isinstance(base_resp, dict) else None
        if code not in (None, 0):
            detail = base_resp.get("status_msg") or "unknown API error"
            raise SoundGenerationError(f"MiniMax Music rejected the request: {detail}")

    @staticmethod
    async def _extract_audio(client: httpx.AsyncClient, payload: dict[str, Any]) -> bytes:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if not isinstance(data, dict):
            raise SoundGenerationError("MiniMax Music response had no data object")
        raw = data.get("audio") or data.get("audio_url") or data.get("url") or ""
        if not isinstance(raw, str) or not raw:
            raise SoundGenerationError("MiniMax Music response did not contain audio")
        if raw.startswith(("https://", "http://")):
            response = await client.get(raw)
            response.raise_for_status()
            return response.content
        try:
            return bytes.fromhex(raw)
        except ValueError:
            try:
                return base64.b64decode(raw, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise SoundGenerationError(
                    "MiniMax Music returned unsupported audio encoding"
                ) from exc
