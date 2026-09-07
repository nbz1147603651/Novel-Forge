"""Tests for the generated audio quality gate."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from novel_forge.core.config import Settings
from novel_forge.tts.sound_generation.quality_gate import (
    evaluate_generated_audio,
)
from novel_forge.tts.sound_generation.schemas import SoundGenerationKind, SoundGenerationRequest


def _make_request(kind: SoundGenerationKind = SoundGenerationKind.SFX) -> SoundGenerationRequest:
    return SoundGenerationRequest(
        request_id="test-qg-1",
        chapter_number=1,
        kind=kind,
        cue_index=0,
        cue_label="quality gate test",
        prompt="test prompt for quality gate evaluation",
        duration_ms=5000,
        output_format="wav",
    )


def _settings(**overrides) -> Settings:
    defaults = {
        "sound_generation_quality_gate_enabled": True,
        "sound_generation_sfx_target_lufs": -18.0,
        "sound_generation_soundscape_target_lufs": -20.0,
    }
    defaults.update(overrides)
    return Settings(**defaults)


class TestQualityGateDisabled:
    async def test_disabled_always_passes(self) -> None:
        result = await evaluate_generated_audio(
            b"audio", _make_request(), _settings(sound_generation_quality_gate_enabled=False)
        )
        assert result.passed is True


class TestQualityGateEmptyData:
    async def test_empty_data_fails(self) -> None:
        result = await evaluate_generated_audio(b"", _make_request(), _settings())
        assert result.passed is False
        assert "empty" in result.blocking_reasons[0].lower()


class TestQualityGateProbeResults:
    async def test_fails_when_probe_unavailable(self) -> None:
        """If ffprobe is not found, quality gate fails closed."""
        with patch(
            "novel_forge.tts.sound_generation.quality_gate._ffprobe_executable",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await evaluate_generated_audio(b"audio-data", _make_request(), _settings())
        assert result.passed is False
        assert any("ffprobe" in r.lower() for r in result.blocking_reasons)

    async def test_fails_when_audio_undecodable(self) -> None:
        """If the file cannot be decoded, quality gate fails closed."""
        with (
            patch(
                "novel_forge.tts.sound_generation.quality_gate._ffprobe_executable",
                new_callable=AsyncMock,
                return_value="/usr/bin/ffprobe",
            ),
            patch(
                "novel_forge.tts.sound_generation.quality_gate._probe_audio",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = await evaluate_generated_audio(b"audio-data", _make_request(), _settings())
        assert result.passed is False
        assert any("decod" in r.lower() for r in result.blocking_reasons)

    async def test_duration_deviation_blocks(self) -> None:
        """Duration > 20% deviation should block."""
        probe_result = {
            "duration_s": 10.0,  # Requested 5s, actual 10s → 100% deviation.
            "integrated_loudness_lufs": -18.0,
            "true_peak_dbtp": -3.0,
        }
        with (
            patch("shutil.which", return_value="/usr/bin/ffprobe"),
            patch(
                "novel_forge.tts.sound_generation.quality_gate._probe_audio",
                new_callable=AsyncMock,
                return_value=probe_result,
            ),
        ):
            result = await evaluate_generated_audio(b"audio-data", _make_request(), _settings())
        assert result.passed is False
        assert any("duration" in r.lower() for r in result.blocking_reasons)

    async def test_loudness_out_of_range_blocks(self) -> None:
        """Loudness far from target should block."""
        probe_result = {
            "duration_s": 5.0,
            "integrated_loudness_lufs": -5.0,  # Way too loud vs -18 target.
            "true_peak_dbtp": -3.0,
        }
        with (
            patch("shutil.which", return_value="/usr/bin/ffprobe"),
            patch(
                "novel_forge.tts.sound_generation.quality_gate._probe_audio",
                new_callable=AsyncMock,
                return_value=probe_result,
            ),
        ):
            result = await evaluate_generated_audio(b"audio-data", _make_request(), _settings())
        assert result.passed is False
        assert any("loudness" in r.lower() for r in result.blocking_reasons)

    async def test_true_peak_clipping_blocks(self) -> None:
        """True peak above -1 dBTP should block."""
        probe_result = {
            "duration_s": 5.0,
            "integrated_loudness_lufs": -18.0,
            "true_peak_dbtp": 0.5,  # Clipping!
        }
        with (
            patch("shutil.which", return_value="/usr/bin/ffprobe"),
            patch(
                "novel_forge.tts.sound_generation.quality_gate._probe_audio",
                new_callable=AsyncMock,
                return_value=probe_result,
            ),
        ):
            result = await evaluate_generated_audio(b"audio-data", _make_request(), _settings())
        assert result.passed is False
        assert any("peak" in r.lower() for r in result.blocking_reasons)

    async def test_all_checks_pass(self) -> None:
        """Good audio passes all checks."""
        probe_result = {
            "duration_s": 5.1,  # Within 20% of 5s.
            "integrated_loudness_lufs": -17.5,  # Within ±6 of -18.
            "true_peak_dbtp": -3.5,  # Below -1 dBTP.
        }
        with (
            patch("shutil.which", return_value="/usr/bin/ffprobe"),
            patch(
                "novel_forge.tts.sound_generation.quality_gate._probe_audio",
                new_callable=AsyncMock,
                return_value=probe_result,
            ),
        ):
            result = await evaluate_generated_audio(b"audio-data", _make_request(), _settings())
        assert result.passed is True
        assert len(result.blocking_reasons) == 0
