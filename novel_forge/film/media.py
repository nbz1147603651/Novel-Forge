"""Media materialization and probing for the 映界 production core.

Provider URLs expire and are not renderable inputs.  This layer downloads
generated media into the project media vault, checksums it and probes it with
ffprobe so rendering, QC and delivery all work on reproducible local files.

The layer is deliberately cross-media: film shots, comic panels and audiobook
masters all flow through the same :class:`FilmMediaVault`, keeping one
maintenance surface for the whole novel → audiobook → comic → film chain.

FFmpeg/ffprobe resolution follows the project convention: system PATH first,
then the bundled ``imageio-ffmpeg`` executable.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from novel_forge.persistence.models import ProjectLayout

from .schemas import MediaArtifact, MediaArtifactKind

Downloader = Callable[[str, Path], Awaitable[None]]
Prober = Callable[[Path], Awaitable[dict[str, Any] | None]]

_KIND_EXTENSIONS: dict[MediaArtifactKind, str] = {
    MediaArtifactKind.IMAGE: ".jpg",
    MediaArtifactKind.VIDEO: ".mp4",
    MediaArtifactKind.AUDIO: ".wav",
    MediaArtifactKind.SUBTITLE: ".srt",
}

_URL_EXTENSION_WHITELIST = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".mp4",
    ".mov",
    ".webm",
    ".wav",
    ".mp3",
    ".m4a",
    ".srt",
    ".vtt",
}


def ffmpeg_executable() -> str | None:
    """Resolve an ffmpeg binary: system PATH first, bundled imageio-ffmpeg next."""

    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    try:
        import imageio_ffmpeg  # type: ignore[import-untyped]

        return str(imageio_ffmpeg.get_ffmpeg_exe())
    except (ImportError, RuntimeError, OSError):
        return None


def ffprobe_executable() -> str | None:
    """Resolve an ffprobe binary: PATH first, then beside the ffmpeg binary."""

    on_path = shutil.which("ffprobe")
    if on_path:
        return on_path
    ffmpeg = ffmpeg_executable()
    if ffmpeg:
        sibling = Path(ffmpeg).with_name("ffprobe")
        if sibling.is_file():
            return str(sibling)
    return None


def _sanitize_subject(subject_ref: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", subject_ref).strip("-")
    return cleaned[:48] or "media"


def _extension_for(url: str, kind: MediaArtifactKind) -> str:
    candidate = Path(url.split("?", 1)[0]).suffix.lower()
    if candidate in _URL_EXTENSION_WHITELIST:
        return candidate
    return _KIND_EXTENSIONS[kind]


async def _default_download(url: str, dest: Path) -> None:
    """Download http(s) URLs with httpx; decode data: URIs; copy local files."""

    if url.startswith("data:"):
        header, _, payload = url.partition(",")
        if ";base64" in header:
            dest.write_bytes(base64.b64decode(payload))
        else:
            dest.write_bytes(payload.encode("utf-8"))
        return
    if url.startswith(("http://", "https://")):
        import httpx

        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with dest.open("wb") as handle:
                    async for chunk in response.aiter_bytes(chunk_size=1 << 16):
                        handle.write(chunk)
        return
    local = Path(url)
    if local.is_file():
        shutil.copy2(local, dest)
        return
    raise ValueError(f"Unsupported media source: {url[:120]}")


async def _default_probe(path: Path) -> dict[str, Any] | None:
    """Probe one media file with ffprobe JSON output.  Returns None when
    ffprobe is unavailable so callers can degrade to metadata-only artifacts."""

    ffprobe = ffprobe_executable()
    if ffprobe is None or not path.is_file():
        return None
    try:
        process = await asyncio.create_subprocess_exec(
            ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=60)
    except (OSError, asyncio.TimeoutError):
        return None
    if process.returncode != 0:
        return None
    try:
        payload = json.loads(stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _parse_probe(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize ffprobe JSON into flat artifact metrics."""

    fmt = payload.get("format")
    format_block: dict[str, Any] = fmt if isinstance(fmt, dict) else {}
    duration = 0.0
    try:
        duration = max(0.0, float(format_block.get("duration") or 0))
    except (TypeError, ValueError):
        duration = 0.0
    width = 0
    height = 0
    fps = 0.0
    codec = ""
    has_audio = False
    streams = payload.get("streams")
    for stream in streams if isinstance(streams, list) else []:
        if not isinstance(stream, dict):
            continue
        codec_type = str(stream.get("codec_type") or "")
        if codec_type == "video" and not codec:
            codec = str(stream.get("codec_name") or "")
            try:
                width = int(stream.get("width") or 0)
                height = int(stream.get("height") or 0)
            except (TypeError, ValueError):
                width, height = 0, 0
            rate = str(stream.get("avg_frame_rate") or "")
            if "/" in rate:
                numerator, _, denominator = rate.partition("/")
                try:
                    if float(denominator) > 0:
                        fps = round(float(numerator) / float(denominator), 3)
                except (TypeError, ValueError):
                    fps = 0.0
        elif codec_type == "audio":
            has_audio = True
    return {
        "duration_s": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "codec": codec,
        "has_audio": has_audio,
    }


class FilmMediaVault:
    """Downloads, checksums and probes generated media into ``film/media/``."""

    def __init__(
        self,
        layout: ProjectLayout,
        *,
        downloader: Downloader | None = None,
        prober: Prober | None = None,
    ) -> None:
        self.layout = layout
        self._download = downloader or _default_download
        self._probe = prober or _default_probe

    @property
    def media_dir(self) -> Path:
        return self.layout.root / "film" / "media"

    async def materialize(
        self,
        url: str,
        *,
        kind: MediaArtifactKind,
        subject_ref: str,
        provider_id: str = "",
        task_id: str = "",
    ) -> MediaArtifact:
        """Fetch ``url`` into the vault and return a probed artifact record."""

        if not url:
            raise ValueError("Cannot materialize an empty media URL")
        self.media_dir.mkdir(parents=True, exist_ok=True)
        artifact_id = uuid4().hex
        dest = (
            self.media_dir
            / f"{_sanitize_subject(subject_ref)}-{artifact_id[:10]}{_extension_for(url, kind)}"
        )
        await self._download(url, dest)
        if not dest.is_file() or dest.stat().st_size == 0:
            raise ValueError(f"Media download produced an empty file: {url[:120]}")
        checksum = hashlib.sha256(dest.read_bytes()).hexdigest()
        metrics: dict[str, Any] = {}
        if kind in {MediaArtifactKind.VIDEO, MediaArtifactKind.AUDIO}:
            payload = await self._probe(dest)
            if payload:
                metrics = _parse_probe(payload)
        return MediaArtifact(
            artifact_id=artifact_id,
            kind=kind,
            subject_ref=subject_ref,
            source_url=url if url.startswith(("http://", "https://")) else "",
            local_path=str(dest),
            checksum=checksum,
            size_bytes=dest.stat().st_size,
            width=int(metrics.get("width") or 0),
            height=int(metrics.get("height") or 0),
            duration_s=float(metrics.get("duration_s") or 0),
            fps=float(metrics.get("fps") or 0),
            codec=str(metrics.get("codec") or ""),
            has_audio=bool(metrics.get("has_audio")),
            provider_id=provider_id,
            task_id=task_id,
        )

    async def probe_path(self, path: Path) -> dict[str, Any]:
        """Probe an arbitrary local file; empty dict when ffprobe is absent."""

        payload = await self._probe(path)
        return _parse_probe(payload) if payload else {}
