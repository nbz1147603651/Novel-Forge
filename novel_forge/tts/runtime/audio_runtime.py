"""Portable FFmpeg helpers for deterministic TTS controls and mixing."""

from __future__ import annotations

import math
import os
import subprocess
import tempfile
import warnings
from pathlib import Path

from novel_forge.tts.runtime.performance_policy import (
    is_short_spoken_utterance as _is_short_spoken_utterance,
)
from novel_forge.tts.runtime.performance_policy import (
    resolve_character_performance,
)
from novel_forge.tts.schemas import TTSProvider, TTSRequest, VoiceCastEntry

_NATIVE_CONTROLS: dict[TTSProvider, frozenset[str]] = {
    TTSProvider.MINIMAX: frozenset({"speed", "pitch", "volume"}),
    TTSProvider.BAILIAN: frozenset({"speed", "pitch", "volume"}),
    TTSProvider.DASHSCOPE: frozenset({"speed", "pitch", "volume"}),
    # Tencent TextToVoice exposes speed, but its volume scale cannot attenuate
    # below the provider default and it has no pitch parameter.  Those two are
    # therefore normalized locally so the shared UI always means the same thing.
    TTSProvider.TENCENT: frozenset({"speed"}),
    TTSProvider.VOLCENGINE_ARK: frozenset({"speed", "pitch", "volume"}),
    # MiMo consumes style/speed guidance as natural-language instructions;
    # exact portable speed, pitch and gain semantics remain local.
    TTSProvider.MIMO: frozenset(),
    TTSProvider.LOCAL: frozenset({"speed"}),
    TTSProvider.OPENVOICE: frozenset({"speed"}),
    TTSProvider.COSYVOICE: frozenset(),
    # Mock audio is intentionally synthetic and should not require FFmpeg.
    TTSProvider.MOCK: frozenset({"speed", "pitch", "volume"}),
}


def is_short_spoken_utterance(text: str) -> bool:
    """Compatibility export for the canonical short-utterance classifier."""

    return _is_short_spoken_utterance(text)


def character_performance_offsets(
    entry: VoiceCastEntry,
    provider: TTSProvider,
    *,
    short_utterance: bool = False,
) -> tuple[float, int, float]:
    """Compatibility wrapper around the canonical character-performance policy."""

    representative_text = (
        "短句" if short_utterance else "这是一句用于解析角色表达参数的完整示例文本。"
    )
    resolved = resolve_character_performance(
        entry,
        provider,
        text=representative_text,
    )
    return resolved.speed_offset, resolved.pitch_offset, resolved.vol_offset


def ffmpeg_executable() -> str:
    """Return the bundled FFmpeg executable supplied by ``imageio-ffmpeg``."""

    import imageio_ffmpeg  # type: ignore[import-untyped]

    return str(imageio_ffmpeg.get_ffmpeg_exe())


def configure_pydub() -> bool:
    """Point pydub at the bundled FFmpeg binary.

    Returns ``False`` only when an optional import is unavailable.  Production
    installs include both dependencies through ``pyproject.toml``.
    """

    try:
        executable = ffmpeg_executable()
        binary_dir = str(Path(executable).parent)
        path_items = os.environ.get("PATH", "").split(os.pathsep)
        if binary_dir not in path_items:
            os.environ["PATH"] = os.pathsep.join([binary_dir, *path_items])
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"pydub\..*")
            warnings.filterwarnings(
                "ignore",
                message="Couldn't find ffmpeg or avconv.*",
                category=RuntimeWarning,
            )
            from pydub import AudioSegment  # type: ignore[import-untyped]

        AudioSegment.converter = executable
    except (ImportError, RuntimeError, OSError):
        return False
    return True


def native_controls(provider: TTSProvider) -> frozenset[str]:
    """Return controls implemented by a provider before local normalization."""

    return _NATIVE_CONTROLS.get(provider, frozenset())


def needs_portable_controls(request: TTSRequest) -> frozenset[str]:
    """Return changed controls that the selected provider does not implement."""

    supported = native_controls(request.provider)
    required: set[str] = set()
    if abs(request.speed - 1.0) > 1e-6 and "speed" not in supported:
        required.add("speed")
    if request.pitch and "pitch" not in supported:
        required.add("pitch")
    if abs(request.volume - 1.0) > 1e-6 and "volume" not in supported:
        required.add("volume")
    return frozenset(required)


def apply_portable_controls(
    audio_data: bytes,
    *,
    request: TTSRequest,
    audio_format: str,
) -> bytes:
    """Apply missing speed, pitch, and volume controls with bundled FFmpeg.

    Provider-native controls remain untouched.  Pitch shifting uses an
    ``asetrate``/``aresample`` pair followed by tempo compensation, so changing
    pitch does not accidentally change the spoken duration.
    """

    required = needs_portable_controls(request)
    if not required:
        return audio_data
    if not audio_data:
        raise ValueError("Cannot post-process empty TTS audio")

    fmt = str(audio_format or request.output_format or "mp3").strip().lower()
    if fmt not in {"mp3", "wav", "flac", "pcm"}:
        fmt = "mp3"

    filters: list[str] = []
    if "pitch" in required:
        factor = math.pow(2.0, request.pitch / 12.0)
        filters.extend(
            [
                f"asetrate={request.sample_rate}*{factor:.8f}",
                f"aresample={request.sample_rate}",
                f"atempo={1.0 / factor:.8f}",
            ]
        )
    if "speed" in required:
        filters.append(f"atempo={request.speed:.8f}")
    if "volume" in required:
        filters.append(f"volume={request.volume:.8f}")

    suffix = ".pcm" if fmt == "pcm" else f".{fmt}"
    with tempfile.TemporaryDirectory(prefix="novel-forge-tts-controls-") as temp_dir:
        root = Path(temp_dir)
        input_path = root / f"input{suffix}"
        output_path = root / f"output{suffix}"
        input_path.write_bytes(audio_data)

        command = [ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-y"]
        if fmt == "pcm":
            command.extend(["-f", "s16le", "-ar", str(request.sample_rate), "-ac", "1"])
        command.extend(["-i", str(input_path), "-vn", "-af", ",".join(filters)])
        if fmt == "pcm":
            command.extend(["-f", "s16le", "-ar", str(request.sample_rate), "-ac", "1"])
        command.append(str(output_path))

        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=120,
        )
        if completed.returncode != 0 or not output_path.is_file():
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"FFmpeg TTS parameter processing failed: {detail or 'no output'}")
        processed = output_path.read_bytes()
        if not processed:
            raise RuntimeError("FFmpeg TTS parameter processing returned empty audio")
        return processed


def normalize_audio_file(input_path: Path, output_path: Path) -> bool:
    """Create a delivery master at audiobook-oriented loudness.

    The one-pass EBU R128 filter targets -16 LUFS with a -1.5 dB true-peak
    ceiling.  The source is kept when FFmpeg cannot create a valid master.
    """

    if not input_path.is_file() or input_path.stat().st_size <= 0:
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-af",
        "loudnorm=I=-16:LRA=11:TP=-1.5",
        "-ar",
        "48000",
        "-b:a",
        "192k",
        str(output_path),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        check=False,
        timeout=300,
    )
    return completed.returncode == 0 and output_path.is_file() and output_path.stat().st_size > 0


def convert_audio_for_export(
    input_path: Path,
    output_path: Path,
    *,
    format: str = "mp3",
    target_lufs: float | None = None,
    bitrate: str = "192k",
) -> bool:
    """Transcode an assembled chapter master to the requested export format.

    Supports ``mp3``, ``wav`` and ``flac`` output.  When *target_lufs* is not
    ``None``, an EBU R128 loudness normalisation filter is inserted so the
    exported file lands at the requested integrated loudness (e.g. -14 for
    audiobook platforms that differ from the internal -16 LUFS master).

    The function always uses the bundled FFmpeg binary so no system codec
    assumptions are made.

    Returns ``True`` when the output file was written successfully.
    """
    if not input_path.is_file() or input_path.stat().st_size <= 0:
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)

    audio_filters: list[str] = []
    if target_lufs is not None:
        audio_filters.append(f"loudnorm=I={target_lufs}:LRA=11:TP=-1.5")

    command = [
        ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-vn",
    ]
    if audio_filters:
        command.extend(["-af", ",".join(audio_filters)])

    fmt = format.lower()
    if fmt == "mp3":
        command.extend(["-b:a", bitrate])
    elif fmt == "wav":
        command.extend(["-acodec", "pcm_s16le", "-ar", "44100"])
    elif fmt == "flac":
        command.extend(["-acodec", "flac", "-ar", "44100"])
    # Unknown formats fall through to FFmpeg's auto-detection by extension.

    command.append(str(output_path))

    completed = subprocess.run(
        command,
        capture_output=True,
        check=False,
        timeout=600,
    )
    return completed.returncode == 0 and output_path.is_file() and output_path.stat().st_size > 0


def remux_for_qt_playback(input_path: Path, output_path: Path) -> bool:
    """Rewrite an audio file so Qt's FFmpeg backend can probe it reliably.

    Some TTS providers (notably MiniMax) prepend a large ID3v2.4 AIGC-watermark
    tag ahead of the first audio frame.  Qt's ``QMediaPlayer`` probes media
    with a tiny ``analyzeduration`` and, when the tag pushes the first frame
    past that probe window, FFmpeg reports ``0 channels / unspecified frame
    size`` and Qt surfaces ``InvalidMedia`` -- so an audition that looks
    perfectly valid (``file`` reads it as MPEG ADTS) plays no sound.

    This remux strips container metadata and rewrites a clean stream copy, with
    a generous input probe so the codec parameters always resolve.  The audio
    samples are copied (``-c:a copy``) when possible; the output container
    keeps the same extension.  Returns ``True`` when a usable file is produced.
    """

    if not input_path.is_file() or input_path.stat().st_size <= 0:
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        # Read with a probe large enough to see past any leading ID3 tag.
        "-probesize",
        "10M",
        "-analyzeduration",
        "10M",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-map_metadata",
        "-1",  # drop ID3/AIGC tags that bloat the header Qt must probe
        "-c:a",
        "copy",
        str(output_path),
    ]
    completed = subprocess.run(command, capture_output=True, check=False, timeout=120)
    if completed.returncode != 0 or not output_path.is_file() or output_path.stat().st_size <= 0:
        # Fall back to a full re-encode if a stream copy could not be demuxed
        # (e.g. exotic codec).  Re-encoding to mp3 is universally Qt-friendly.
        fallback = output_path.with_suffix(".mp3")
        command = [
            ffmpeg_executable(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-probesize",
            "10M",
            "-analyzeduration",
            "10M",
            "-y",
            "-i",
            str(input_path),
            "-vn",
            "-map_metadata",
            "-1",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(fallback),
        ]
        completed = subprocess.run(command, capture_output=True, check=False, timeout=120)
        if fallback != output_path and fallback.is_file() and fallback.stat().st_size > 0:
            try:
                fallback.replace(output_path)
            except OSError:
                return False
    return output_path.is_file() and output_path.stat().st_size > 0
