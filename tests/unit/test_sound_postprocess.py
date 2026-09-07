"""Tests for the deterministic audio post-processing pipeline."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from novel_forge.core.config import Settings
from novel_forge.tts.sound_generation.postprocess import (
    SoundPostprocessError,
    _build_filtergraph,
    postprocess_generated_audio,
)
from novel_forge.tts.sound_generation.schemas import SoundGenerationKind, SoundGenerationRequest


def _make_request(kind: SoundGenerationKind = SoundGenerationKind.SFX) -> SoundGenerationRequest:
    return SoundGenerationRequest(
        request_id="test-1",
        chapter_number=1,
        kind=kind,
        cue_index=0,
        cue_label="test cue",
        prompt="test prompt for post-processing",
        duration_ms=5000,
        output_format="wav",
    )


def _settings(**overrides) -> Settings:
    defaults = {
        "sound_generation_postprocess_enabled": True,
        "sound_generation_sfx_target_lufs": -18.0,
        "sound_generation_soundscape_target_lufs": -20.0,
        "sound_generation_highpass_hz": 40,
        "sound_generation_tail_fade_sfx_ms": 150,
        "sound_generation_tail_fade_soundscape_ms": 500,
    }
    defaults.update(overrides)
    return Settings(**defaults)


class TestBuildFiltergraph:
    def test_sfx_filtergraph_contains_highpass(self) -> None:
        request = _make_request(SoundGenerationKind.SFX)
        fg = _build_filtergraph(request, _settings(), duration_s=5.0)
        assert "highpass=f=40" in fg

    def test_sfx_filtergraph_contains_loudnorm(self) -> None:
        request = _make_request(SoundGenerationKind.SFX)
        fg = _build_filtergraph(request, _settings(), duration_s=5.0)
        assert "loudnorm=I=-18.0" in fg

    def test_soundscape_uses_different_loudness(self) -> None:
        request = _make_request(SoundGenerationKind.SOUNDSCAPE)
        fg = _build_filtergraph(request, _settings(), duration_s=10.0)
        assert "loudnorm=I=-20.0" in fg

    def test_highpass_disabled_when_zero(self) -> None:
        request = _make_request(SoundGenerationKind.SFX)
        fg = _build_filtergraph(request, _settings(sound_generation_highpass_hz=0), duration_s=5.0)
        assert "highpass" not in fg

    def test_fade_out_present_for_long_audio(self) -> None:
        request = _make_request(SoundGenerationKind.SFX)
        fg = _build_filtergraph(request, _settings(), duration_s=5.0)
        assert "afade=t=out" in fg

    def test_no_fade_for_very_short_audio(self) -> None:
        request = _make_request(SoundGenerationKind.SFX)
        # Duration shorter than 2x fade → no fade applied.
        fg = _build_filtergraph(request, _settings(), duration_s=0.2)
        assert "afade" not in fg


class TestPostprocessGracefulDegradation:
    async def test_disabled_returns_original(self) -> None:
        data = b"raw-audio-bytes"
        result = await postprocess_generated_audio(
            data, _make_request(), _settings(sound_generation_postprocess_enabled=False)
        )
        assert result == data

    async def test_empty_data_returns_original(self) -> None:
        result = await postprocess_generated_audio(b"", _make_request(), _settings())
        assert result == b""

    async def test_ffmpeg_unavailable_raises(self) -> None:
        data = b"raw-audio-bytes"
        with patch(
            "novel_forge.tts.sound_generation.postprocess._ffmpeg_executable",
            return_value=None,
        ):
            with pytest.raises(SoundPostprocessError):
                await postprocess_generated_audio(data, _make_request(), _settings())

    async def test_ffmpeg_failure_raises(self) -> None:
        data = b"raw-audio-bytes"
        with (
            patch(
                "novel_forge.tts.sound_generation.postprocess._ffmpeg_executable",
                return_value="/usr/bin/ffmpeg",
            ),
            patch(
                "novel_forge.tts.sound_generation.postprocess._probe_duration",
                new_callable=AsyncMock,
                return_value=5.0,
            ),
            patch("asyncio.create_subprocess_exec", side_effect=OSError("no ffmpeg")),
        ):
            with pytest.raises(SoundPostprocessError):
                await postprocess_generated_audio(data, _make_request(), _settings())

    async def test_unprobeable_duration_raises(self) -> None:
        data = b"raw-audio-bytes"
        with (
            patch(
                "novel_forge.tts.sound_generation.postprocess._ffmpeg_executable",
                return_value="/usr/bin/ffmpeg",
            ),
            patch(
                "novel_forge.tts.sound_generation.postprocess._probe_duration",
                new_callable=AsyncMock,
                return_value=0.0,
            ),
        ):
            with pytest.raises(SoundPostprocessError):
                await postprocess_generated_audio(data, _make_request(), _settings())
