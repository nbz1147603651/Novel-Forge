"""Tests for portable TTS parameter processing."""

from __future__ import annotations

import io
import math
import struct
import wave
from pathlib import Path

from novel_forge.tts.runtime.audio_runtime import (
    apply_portable_controls,
    needs_portable_controls,
    remux_for_qt_playback,
)
from novel_forge.tts.schemas import TTSProvider, TTSRequest


def _tone_wav(*, duration_ms: int = 400, frequency: float = 440.0, rate: int = 16_000) -> bytes:
    frame_count = int(rate * duration_ms / 1000)
    frames = bytearray()
    for index in range(frame_count):
        sample = int(12_000 * math.sin(2 * math.pi * frequency * index / rate))
        frames.extend(struct.pack("<h", sample))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return buffer.getvalue()


def _wav_metrics(data: bytes) -> tuple[int, int]:
    with wave.open(io.BytesIO(data), "rb") as handle:
        duration_ms = int(handle.getnframes() * 1000 / handle.getframerate())
        samples = struct.unpack(f"<{handle.getnframes()}h", handle.readframes(handle.getnframes()))
    return duration_ms, max(abs(value) for value in samples)


def test_minimax_uses_native_controls_without_postprocessing() -> None:
    request = TTSRequest(
        text="测试",
        provider=TTSProvider.MINIMAX,
        speed=1.2,
        pitch=2,
        volume=0.8,
    )

    assert needs_portable_controls(request) == frozenset()


def test_local_pitch_and_volume_are_applied_by_portable_ffmpeg() -> None:
    source = _tone_wav()
    request = TTSRequest(
        text="测试",
        provider=TTSProvider.LOCAL,
        speed=1.0,
        pitch=3,
        volume=0.5,
        output_format="wav",
        sample_rate=16_000,
    )

    assert needs_portable_controls(request) == frozenset({"pitch", "volume"})
    processed = apply_portable_controls(source, request=request, audio_format="wav")
    source_duration, source_peak = _wav_metrics(source)
    processed_duration, processed_peak = _wav_metrics(processed)

    assert abs(processed_duration - source_duration) <= 35
    assert processed_peak < source_peak * 0.7


def test_cosyvoice_speed_is_applied_when_endpoint_has_no_numeric_control() -> None:
    source = _tone_wav(duration_ms=500)
    request = TTSRequest(
        text="测试",
        provider=TTSProvider.COSYVOICE,
        speed=2.0,
        output_format="wav",
        sample_rate=16_000,
    )

    processed = apply_portable_controls(source, request=request, audio_format="wav")
    duration_ms, _ = _wav_metrics(processed)

    assert 220 <= duration_ms <= 280


def test_remux_for_qt_playback_produces_decodable_file(tmp_path: Path) -> None:
    """The audition remux must yield a non-empty file Qt's FFmpeg can probe.

    MiniMax prepends a large AIGC-watermark ID3 tag that Qt's tiny default
    probe cannot see past (it reports ``InvalidMedia`` and the audition is
    silent).  ``remux_for_qt_playback`` rewrites a clean stream copy with a
    generous probe and stripped metadata so the cached preview is always
    playable.  This guards against regressions that would reintroduce the
    "已播放已保存的试听 but no sound" symptom.
    """
    src = tmp_path / "source.wav"
    src.write_bytes(_tone_wav(duration_ms=600))
    dst = tmp_path / "preview.wav"

    assert remux_for_qt_playback(src, dst) is True
    assert dst.is_file()
    assert dst.stat().st_size > 0
    # The output must still be a valid WAV (decodable header intact).
    with wave.open(str(dst), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getframerate() == 16_000


def test_remux_for_qt_playback_missing_source_returns_false(tmp_path: Path) -> None:
    dst = tmp_path / "preview.mp3"
    assert remux_for_qt_playback(tmp_path / "absent.mp3", dst) is False
    assert not dst.exists()
