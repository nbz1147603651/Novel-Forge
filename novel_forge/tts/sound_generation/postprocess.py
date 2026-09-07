"""Deterministic post-processing pipeline for generated sound assets.

Applies FFmpeg-based audio conditioning *before* assets are written to the
project library.  The pipeline is fail-closed: if FFmpeg is unavailable or any
step fails, :class:`SoundPostprocessError` is raised so the caller marks the
asset un-approvable instead of silently storing unprocessed audio.

Processing chain (single FFmpeg pass):
1. High-pass filter — remove sub-sonic rumble below a configurable threshold.
2. Tail silence trimming — remove trailing silence beyond a minimum decay.
3. Standardized fade-out — kind-dependent tail fade for natural cutoff.
4. Loudness normalization — EBU R128 single-pass loudnorm to target LUFS.
5. True-peak limiter — cap at -3 dBTP to prevent downstream clipping.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

from novel_forge.core.config import Settings
from novel_forge.tts.runtime.audio_runtime import ffmpeg_executable
from novel_forge.tts.sound_generation.schemas import SoundGenerationKind, SoundGenerationRequest

logger = logging.getLogger(__name__)

# Kind-specific fade-out durations in milliseconds.
_DEFAULT_FADE_MS: dict[SoundGenerationKind, int] = {
    SoundGenerationKind.SFX: 150,
    SoundGenerationKind.SOUNDSCAPE: 500,
    SoundGenerationKind.BGM: 800,
}

# Kind-specific target loudness in LUFS.
_DEFAULT_TARGET_LUFS: dict[SoundGenerationKind, float] = {
    SoundGenerationKind.SFX: -18.0,
    SoundGenerationKind.SOUNDSCAPE: -20.0,
    SoundGenerationKind.BGM: -16.0,
}

_TRUE_PEAK_LIMIT_DBTP = -3.0


class SoundPostprocessError(RuntimeError):
    """Raised when audio conditioning cannot be performed.

    Callers treat this as a failed asset (never as an opportunity to store the
    unprocessed audio), keeping the fail-closed contract intact.
    """


def _ffmpeg_executable() -> str | None:
    """Resolve the FFmpeg binary: bundled first, system PATH as fallback."""
    try:
        return ffmpeg_executable()
    except (ImportError, RuntimeError, OSError):
        pass
    return shutil.which("ffmpeg")


def _ffprobe_executable() -> str | None:
    """Resolve an ffprobe binary: PATH first, then the bundled ffmpeg directory."""
    on_path = shutil.which("ffprobe")
    if on_path:
        return on_path
    ffmpeg = _ffmpeg_executable()
    if ffmpeg:
        sibling = Path(ffmpeg).with_name("ffprobe")
        if sibling.is_file():
            return str(sibling)
    return None


def _build_filtergraph(
    request: SoundGenerationRequest,
    settings: Settings,
    duration_s: float,
) -> str:
    """Assemble the FFmpeg audio filtergraph string for one asset."""
    highpass_hz = settings.sound_generation_highpass_hz
    fade_ms = _resolve_fade_ms(request.kind, settings)
    target_lufs = _resolve_target_lufs(request.kind, settings)

    filters: list[str] = []

    # 1. High-pass filter to remove sub-sonic rumble.
    if highpass_hz > 0:
        filters.append(f"highpass=f={highpass_hz}")

    # 2. Trailing silence removal (silenceremove with reverse trick).
    #    Remove silence from the end: stop threshold -50dB, keep at least 200ms.
    filters.append(
        "areverse,silenceremove=stop_periods=1:stop_duration=0.2:stop_threshold=-50dB,areverse"
    )

    # 3. Fade-out at the tail.
    fade_s = fade_ms / 1000.0
    if duration_s > fade_s * 2:
        fade_start = max(0.0, duration_s - fade_s)
        filters.append(f"afade=t=out:st={fade_start:.3f}:d={fade_s:.3f}")

    # 4. Loudness normalization (single-pass loudnorm).
    filters.append(
        f"loudnorm=I={target_lufs:.1f}:TP={_TRUE_PEAK_LIMIT_DBTP:.1f}:LRA=11:linear=true"
    )

    return ",".join(filters)


def _resolve_fade_ms(kind: SoundGenerationKind, settings: Settings) -> int:
    """Return the configured fade-out duration for a given kind."""
    if kind == SoundGenerationKind.SFX:
        return settings.sound_generation_tail_fade_sfx_ms
    if kind == SoundGenerationKind.SOUNDSCAPE:
        return settings.sound_generation_tail_fade_soundscape_ms
    return _DEFAULT_FADE_MS.get(kind, 800)


def _resolve_target_lufs(kind: SoundGenerationKind, settings: Settings) -> float:
    """Return the configured target loudness for a given kind."""
    if kind == SoundGenerationKind.SFX:
        return settings.sound_generation_sfx_target_lufs
    if kind == SoundGenerationKind.SOUNDSCAPE:
        return settings.sound_generation_soundscape_target_lufs
    return _DEFAULT_TARGET_LUFS.get(kind, -16.0)


async def postprocess_generated_audio(
    audio_data: bytes,
    request: SoundGenerationRequest,
    settings: Settings,
) -> bytes:
    """Apply deterministic post-processing to generated audio bytes.

    Returns the processed audio.  Raises :class:`SoundPostprocessError` when
    the toolchain is unavailable or the FFmpeg pass fails, so unprocessed
    audio can never silently enter the project library.

    No-op (returns the input unchanged) only when post-processing is disabled
    via settings or the input is empty.
    """
    if not settings.sound_generation_postprocess_enabled:
        return audio_data
    if not audio_data:
        return audio_data
    ffmpeg = _ffmpeg_executable()
    if not ffmpeg:
        logger.error("FFmpeg not found; post-processing fails closed.")
        raise SoundPostprocessError("ffmpeg unavailable; cannot post-process generated audio")

    suffix = f".{request.output_format}"
    try:
        with tempfile.TemporaryDirectory(prefix="novel-forge-postprocess-") as tmp_dir:
            input_path = Path(tmp_dir) / f"input{suffix}"
            output_path = Path(tmp_dir) / f"output{suffix}"
            input_path.write_bytes(audio_data)

            # Probe duration first for fade-out calculation.
            duration_s = await _probe_duration(input_path)
            if duration_s <= 0:
                logger.error("Post-process: could not probe duration; failing closed.")
                raise SoundPostprocessError(
                    "could not probe generated audio duration for post-processing"
                )

            filtergraph = _build_filtergraph(request, settings, duration_s)
            command = [
                ffmpeg,
                "-y",
                "-i",
                str(input_path),
                "-af",
                filtergraph,
                "-ar",
                "48000",
                str(output_path),
            ]

            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=60.0)

            if process.returncode != 0:
                detail = stderr.decode("utf-8", errors="replace")[-500:]
                logger.error(
                    "Post-process FFmpeg failed (rc=%d): %s; failing closed.",
                    process.returncode,
                    detail,
                )
                raise SoundPostprocessError(
                    f"ffmpeg post-processing failed (rc={process.returncode})"
                )

            if not output_path.is_file() or output_path.stat().st_size == 0:
                logger.error("Post-process produced empty output; failing closed.")
                raise SoundPostprocessError("ffmpeg post-processing produced empty output")

            processed = output_path.read_bytes()
            logger.debug(
                "Post-process complete: %d -> %d bytes (%s)",
                len(audio_data),
                len(processed),
                request.kind.value,
            )
            return processed

    except TimeoutError as exc:
        logger.error("Post-process FFmpeg timed out; failing closed.")
        raise SoundPostprocessError("ffmpeg post-processing timed out") from exc
    except OSError as exc:
        logger.error("Post-process I/O error: %s; failing closed.", exc)
        raise SoundPostprocessError(f"ffmpeg post-processing I/O error: {exc}") from exc


async def _probe_duration(path: Path) -> float:
    """Probe audio duration in seconds using ffprobe."""
    ffprobe = _ffprobe_executable()
    if ffprobe is None:
        return 0.0
    try:
        process = await asyncio.create_subprocess_exec(
            ffprobe,
            "-v",
            "quiet",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10.0)
        if process.returncode != 0:
            return 0.0
        return float(stdout.decode("utf-8", errors="replace").strip())
    except (ValueError, TimeoutError, OSError):
        return 0.0
