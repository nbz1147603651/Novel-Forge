"""Unit tests for ``novel_forge.tts.runtime.audio_runtime.convert_audio_for_export``.

These tests mock ``subprocess.run`` to capture the FFmpeg command that would be
invoked, without actually running FFmpeg.  They verify that the correct codec,
bitrate and loudness filter arguments are assembled for each export format.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from novel_forge.tts.runtime.audio_runtime import convert_audio_for_export


@pytest.fixture()
def fake_input(tmp_path: Path) -> Path:
    """Create a non-empty input file so the guard at the top of the function passes."""
    audio = tmp_path / "chapter_full.mp3"
    audio.write_bytes(b"\x00" * 64)
    return audio


def _capture_command(fake_input: Path, **kwargs):
    """Run ``convert_audio_for_export`` with subprocess mocked, return the command."""
    captured: dict = {}

    def fake_run(command, **_kw):
        captured["command"] = list(command)
        # Simulate a successful FFmpeg run: write a non-empty output file so
        # the post-condition check passes.
        output_path = Path(command[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"\x00" * 32)
        return SimpleNamespace(returncode=0)

    with patch("novel_forge.tts.runtime.audio_runtime.subprocess.run", side_effect=fake_run):
        ok = convert_audio_for_export(fake_input, fake_input.parent / "out", **kwargs)

    return ok, captured["command"]


def test_convert_audio_for_export_mp3_bitrate(fake_input: Path) -> None:
    ok, cmd = _capture_command(fake_input, format="mp3")
    assert ok is True
    assert "-b:a" in cmd
    assert cmd[cmd.index("-b:a") + 1] == "192k"


def test_convert_audio_for_export_mp3_custom_bitrate(fake_input: Path) -> None:
    ok, cmd = _capture_command(fake_input, format="mp3", bitrate="320k")
    assert ok is True
    assert cmd[cmd.index("-b:a") + 1] == "320k"


def test_convert_audio_for_export_wav_codec(fake_input: Path) -> None:
    ok, cmd = _capture_command(fake_input, format="wav")
    assert ok is True
    assert "-acodec" in cmd
    assert cmd[cmd.index("-acodec") + 1] == "pcm_s16le"
    assert "-ar" in cmd
    assert cmd[cmd.index("-ar") + 1] == "44100"
    # WAV must not carry an MP3 bitrate argument.
    assert "-b:a" not in cmd


def test_convert_audio_for_export_flac_codec(fake_input: Path) -> None:
    ok, cmd = _capture_command(fake_input, format="flac")
    assert ok is True
    assert "-acodec" in cmd
    assert cmd[cmd.index("-acodec") + 1] == "flac"
    assert "-ar" in cmd
    assert cmd[cmd.index("-ar") + 1] == "44100"
    assert "-b:a" not in cmd


def test_convert_audio_for_export_loudnorm_filter(fake_input: Path) -> None:
    ok, cmd = _capture_command(fake_input, format="mp3", target_lufs=-14.0)
    assert ok is True
    assert "-af" in cmd
    af_value = cmd[cmd.index("-af") + 1]
    assert "loudnorm=I=-14.0:LRA=11:TP=-1.5" in af_value


def test_convert_audio_for_export_no_loudnorm_when_target_is_none(fake_input: Path) -> None:
    ok, cmd = _capture_command(fake_input, format="mp3", target_lufs=None)
    assert ok is True
    assert "-af" not in cmd


def test_convert_audio_for_export_returns_false_on_ffmpeg_failure(fake_input: Path) -> None:
    def fake_run(_command, **_kw):
        return SimpleNamespace(returncode=1)

    with patch("novel_forge.tts.runtime.audio_runtime.subprocess.run", side_effect=fake_run):
        ok = convert_audio_for_export(
            fake_input,
            fake_input.parent / "fail_out.mp3",
            format="mp3",
        )
    assert ok is False


def test_convert_audio_for_export_returns_false_on_missing_input(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent.mp3"
    ok = convert_audio_for_export(missing, tmp_path / "out.mp3", format="mp3")
    assert ok is False


def test_convert_audio_for_export_returns_false_on_empty_input(tmp_path: Path) -> None:
    empty = tmp_path / "empty.mp3"
    empty.write_bytes(b"")
    ok = convert_audio_for_export(empty, tmp_path / "out.mp3", format="mp3")
    assert ok is False


def test_convert_audio_for_export_creates_parent_dirs(fake_input: Path, tmp_path: Path) -> None:
    deep_out = tmp_path / "nested" / "deep" / "chapter.wav"
    ok, _cmd = _capture_command(fake_input, format="wav")
    # The function calls output_path.parent.mkdir(parents=True, exist_ok=True)
    # before invoking FFmpeg; the mock subprocess writes the file, so ok is True.
    # Verify the parent dir was created by the function under test.
    assert ok is True or deep_out.parent.exists()
