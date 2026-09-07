"""Uniform, dependency-isolated client for ASR/alignment audio sidecars."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

import httpx

_log = logging.getLogger(__name__)


class AudioSidecarClient:
    """Thin protocol client; provider frameworks stay outside the main process."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        timeout_s: float = 300.0,
        connect_timeout_s: float = 2.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(
                timeout_s,
                connect=min(timeout_s, connect_timeout_s),
                pool=min(timeout_s, connect_timeout_s),
            ),
            transport=transport,
        )

    async def health(self) -> dict[str, Any]:
        response = await self._client.get("/health")
        response.raise_for_status()
        return dict(response.json())

    async def capabilities(self) -> dict[str, Any]:
        response = await self._client.get("/capabilities")
        response.raise_for_status()
        return dict(response.json())

    async def self_test(self, *, model_id: str = "") -> dict[str, Any]:
        response = await self._client.post("/self-test", json={"model_id": model_id})
        response.raise_for_status()
        return dict(response.json())

    async def version(self) -> dict[str, Any]:
        response = await self._client.get("/version")
        response.raise_for_status()
        return dict(response.json())

    async def list_models(self) -> dict[str, Any]:
        response = await self._client.get("/models")
        response.raise_for_status()
        return dict(response.json())

    async def install_model(
        self,
        *,
        plugin_id: str,
        model_id: str,
        revision: str = "main",
        local_path: str = "",
        accept_license: bool = False,
    ) -> dict[str, Any]:
        response = await self._client.post(
            "/models/install",
            json={
                "plugin_id": plugin_id,
                "model_id": model_id,
                "revision": revision,
                "local_path": local_path,
                "accept_license": accept_license,
            },
        )
        response.raise_for_status()
        return dict(response.json())

    async def delete_model(self, *, plugin_id: str, model_id: str) -> dict[str, Any]:
        response = await self._client.post(
            "/models/delete",
            json={"plugin_id": plugin_id, "model_id": model_id},
        )
        response.raise_for_status()
        return dict(response.json())

    async def transcribe(
        self,
        audio_path: str | Path,
        *,
        language: str = "auto",
        expected_text: str = "",
    ) -> dict[str, Any]:
        wav_path, cleanup = self._ensure_wav(Path(audio_path))
        try:
            with wav_path.open("rb") as handle:
                response = await self._client.post(
                    "/transcribe",
                    data={"language": language, "expected_text": expected_text},
                    files={"audio": (wav_path.name, handle, "application/octet-stream")},
                )
        finally:
            if cleanup is not None:
                cleanup.unlink(missing_ok=True)
        response.raise_for_status()
        return dict(response.json())

    async def align(
        self,
        audio_path: str | Path,
        *,
        text: str,
        language: str,
    ) -> dict[str, Any]:
        wav_path, cleanup = self._ensure_wav(Path(audio_path))
        try:
            with wav_path.open("rb") as handle:
                response = await self._client.post(
                    "/align",
                    data={"language": language, "text": text},
                    files={"audio": (wav_path.name, handle, "application/octet-stream")},
                )
        finally:
            if cleanup is not None:
                cleanup.unlink(missing_ok=True)
        response.raise_for_status()
        return dict(response.json())

    @staticmethod
    def _ensure_wav(path: Path) -> tuple[Path, Path | None]:
        """Return a 16-bit PCM WAV path for sidecar upload.

        Sherpa's ``wave`` module only reads WAV; WhisperX's ``load_audio``
        shells out to ``ffmpeg`` which may be absent in the sidecar's isolated
        environment.  By pre-transcoding in the main process (which bundles
        ``imageio-ffmpeg`` via :func:`configure_pydub`), both backends receive
        a universally readable WAV.

        Returns ``(wav_path, cleanup_path)``.  ``cleanup_path`` is ``None``
        when no temporary file was created (caller skips cleanup); otherwise
        it is the temp path to ``unlink`` after upload.
        """
        if path.suffix.lower() in {".wav", ".wave"}:
            return path, None
        try:
            from novel_forge.tts.runtime.audio_runtime import configure_pydub

            if not configure_pydub():
                return path, None
            from pydub import AudioSegment  # type: ignore[import-untyped]

            audio = AudioSegment.from_file(str(path))
            audio = audio.set_channels(1).set_frame_rate(16000).set_sample_width(2)
            tmp = Path(tempfile.mktemp(suffix=".wav", prefix="sidecar_upload_"))
            audio.export(str(tmp), format="wav")
            return tmp, tmp
        except Exception as exc:  # noqa: BLE001 -- fall back to original file
            _log.debug("WAV pre-transcode failed for %s: %s", path, exc)
            return path, None

    async def diagnostics(self) -> dict[str, Any]:
        response = await self._client.get("/diagnostics")
        response.raise_for_status()
        return dict(response.json())

    async def aclose(self) -> None:
        await self._client.aclose()
