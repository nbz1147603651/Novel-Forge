"""Unit tests for segment quality gate including loudness/dead-air advisory checks."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from novel_forge.tts.pipeline.segment_quality import (
    evaluate_synthesized_segment,
    normalize_speakable_text,
    trustworthy_duration_ms,
)
from novel_forge.tts.runtime.audio_runtime import ffmpeg_executable
from novel_forge.tts.schemas import (
    ProviderTakeEvidence,
    TTSProvider,
    TTSRequest,
    TTSResponse,
)


def _make_response() -> TTSResponse:
    return TTSResponse(
        audio_data=b"\x00" * 1024,
        duration_ms=1000,
        model_id="speech-2.8-hd",
        voice_id="v1",
        take_evidence=ProviderTakeEvidence(
            trace_id="t1",
            provider_status="2",
            invisible_character_ratio=0.0,
        ),
    )


def _make_request() -> TTSRequest:
    return TTSRequest(
        text="测试对白内容",
        voice_id="v1",
        model_id="speech-2.8-hd",
        provider=TTSProvider.MOCK,
    )


def _generate_tone(path: Path, *, duration_s: float = 1.0, freq: int = 440, volume: float = 0.3) -> None:
    """Generate a sine-tone wav via FFmpeg for loudness tests."""
    cmd = [
        ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={freq}:duration={duration_s}",
        "-af",
        f"volume={volume}",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(path),
    ]
    subprocess.run(cmd, capture_output=True, check=True, timeout=30)


def _generate_silence(path: Path, *, duration_s: float = 2.0) -> None:
    """Generate a pure-silence wav via FFmpeg."""
    cmd = [
        ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=mono:sample_rate=16000",
        "-t",
        str(duration_s),
        str(path),
    ]
    subprocess.run(cmd, capture_output=True, check=True, timeout=30)


def test_segment_quality_hard_gate_rejects_tiny_payload() -> None:
    decision = evaluate_synthesized_segment(
        _make_request(),
        _make_response(),
        duration_ms=1000,
        audio_size_bytes=10,  # < 128
        minimum_duration_ms=100,
        maximum_characters_per_second=20.0,
    )
    assert not decision.passed


def test_segment_quality_passes_without_loudness_check_when_path_none() -> None:
    decision = evaluate_synthesized_segment(
        _make_request(),
        _make_response(),
        duration_ms=1000,
        audio_size_bytes=2048,
        minimum_duration_ms=100,
        maximum_characters_per_second=20.0,
        audio_path=None,
        min_rms_db=-30.0,
    )
    assert decision.passed
    assert decision.warnings == ()


def test_segment_quality_warns_on_silence_when_threshold_tight(tmp_path: Path) -> None:
    audio = tmp_path / "silent.wav"
    _generate_silence(audio, duration_s=2.0)
    decision = evaluate_synthesized_segment(
        _make_request(),
        _make_response(),
        duration_ms=2000,
        audio_size_bytes=audio.stat().st_size,
        minimum_duration_ms=100,
        maximum_characters_per_second=20.0,
        audio_path=audio,
        max_silence_ratio=0.3,
    )
    assert decision.passed  # advisory, not a hard gate
    assert any("静音比例" in w for w in decision.warnings)


def test_segment_quality_warns_on_low_rms_when_threshold_tight(tmp_path: Path) -> None:
    audio = tmp_path / "quiet.wav"
    _generate_tone(audio, volume=0.001)  # very quiet
    decision = evaluate_synthesized_segment(
        _make_request(),
        _make_response(),
        duration_ms=1000,
        audio_size_bytes=audio.stat().st_size,
        minimum_duration_ms=100,
        maximum_characters_per_second=20.0,
        audio_path=audio,
        min_rms_db=-20.0,  # tight; quiet tone should trip it
    )
    assert decision.passed
    assert any("RMS" in w for w in decision.warnings)


def test_segment_quality_no_warning_when_thresholds_disabled(tmp_path: Path) -> None:
    """When thresholds are at disabled sentinels, no loudness warning fires."""
    audio = tmp_path / "tone.wav"
    _generate_tone(audio, volume=0.3)
    decision = evaluate_synthesized_segment(
        _make_request(),
        _make_response(),
        duration_ms=1000,
        audio_size_bytes=audio.stat().st_size,
        minimum_duration_ms=100,
        maximum_characters_per_second=20.0,
        audio_path=audio,
        min_rms_db=-100.0,  # disabled
        max_peak_db=0.0,  # disabled
        max_silence_ratio=1.0,  # disabled
    )
    assert decision.passed
    assert decision.warnings == ()


def test_segment_quality_handles_missing_audio_path_gracefully(tmp_path: Path) -> None:
    """A non-existent path must not raise - measurement returns None and is skipped."""
    missing = tmp_path / "does_not_exist.wav"
    decision = evaluate_synthesized_segment(
        _make_request(),
        _make_response(),
        duration_ms=1000,
        audio_size_bytes=2048,
        minimum_duration_ms=100,
        maximum_characters_per_second=20.0,
        audio_path=missing,
        min_rms_db=-30.0,
        max_silence_ratio=0.3,
    )
    assert decision.passed
    assert decision.warnings == ()


# ── trustworthy_duration_ms ──────────────────────────────────────────────────


def test_trusty_duration_returns_none_for_zero() -> None:
    """A zero or negative reported value is never trustworthy."""
    assert trustworthy_duration_ms(0, "一段中文测试文本") is None
    assert trustworthy_duration_ms(-5, "一段中文测试文本") is None


def test_trusty_duration_rejects_tiny_value() -> None:
    """A 72ms value for a 72-char text is almost certainly a character-count leak.

    This reproduces the MiniMax ``audio_length`` bug where the field returned
    the character count (72) instead of milliseconds, causing the quality gate
    to reject a perfectly valid 18-second take three times in a row.
    """
    long_text = "一个年轻女人拎着一只半旧的行李箱站在门口，雨水顺着她的发梢滴落。"
    # 72 chars normalized; 72ms implies 1000 cps - absurd.
    assert trustworthy_duration_ms(72, long_text) is None


def test_trusty_duration_rejects_absurd_cps() -> None:
    """Even a moderate-length text with an absurd cps must be rejected."""
    # "快救我啊这里！" → 6 normalized chars at 100ms => 60 cps, above the 50 cps ceiling.
    assert trustworthy_duration_ms(100, "快救我啊这里！") is None


def test_trusty_duration_accepts_reasonable_value() -> None:
    """A normal provider-reported duration passes through unchanged."""
    long_text = "一个年轻女人拎着一只半旧的行李箱站在门口，雨水顺着她的发梢滴落。"
    # 72 chars at 3600ms => 20 cps, within normal speech range.
    assert trustworthy_duration_ms(3600, long_text) == 3600


def test_trusty_duration_skips_cps_check_for_short_text() -> None:
    """Short texts (<4 normalized chars) only need to clear the hard floor."""
    # 3 normalized chars; cps check is skipped, 150ms > 50ms floor => trusted.
    assert trustworthy_duration_ms(150, "救我！") == 150


def test_trusty_duration_respects_custom_floor() -> None:
    """Callers can tighten the hard floor for stricter providers."""
    # 80ms < 100ms floor → rejected
    assert trustworthy_duration_ms(80, "测试一下", hard_floor_ms=100) is None
    # 120ms >= 100ms floor, cps for "测试一下"(4 chars)/120ms = 33.3 < 50 → accepted
    assert trustworthy_duration_ms(120, "测试一下", hard_floor_ms=100) == 120


def test_trusty_duration_returns_none_for_punctuation_only_text() -> None:
    """Punctuation-only text (e.g. '————') must not trust provider duration."""
    assert trustworthy_duration_ms(72, "————") is None
    assert trustworthy_duration_ms(72, "……") is None
    assert trustworthy_duration_ms(72, "---") is None
    assert trustworthy_duration_ms(72, "——————") is None
    assert trustworthy_duration_ms(72, "。。。") is None
    assert trustworthy_duration_ms(72, "— —") is None


def test_normalize_speakable_text_public_api() -> None:
    """normalize_speakable_text is importable and works as expected."""
    assert normalize_speakable_text("————") == ""
    assert normalize_speakable_text("你好世界") == "你好世界"
    assert normalize_speakable_text("test——123") == "test123"
    assert normalize_speakable_text("") == ""
    assert normalize_speakable_text("……") == ""
    assert normalize_speakable_text("———") == ""


# ── provider status gate (P0 regression: DashScope "stop" = success) ────────────


def _evaluate_with_provider_status(status: str) -> object:
    response = TTSResponse(
        audio_data=b"\x00" * 1024,
        duration_ms=1000,
        model_id="qwen-audio-3.0-tts-plus",
        voice_id="v1",
        take_evidence=ProviderTakeEvidence(
            trace_id="t1",
            provider_status=status,
            invisible_character_ratio=0.0,
        ),
    )
    return evaluate_synthesized_segment(
        _make_request(),
        response,
        duration_ms=1000,
        audio_size_bytes=2048,
        minimum_duration_ms=100,
        maximum_characters_per_second=20.0,
    )


@pytest.mark.parametrize(
    "status",
    [
        "",                  # absent / normalized streaming intermediate
        "2",                 # MiniMax HTTP success
        "success",           # MiniMax HTTP success (text form)
        "completed",         # normalized DashScope success
        "stop",              # raw DashScope finish_reason (defensive layer)
        "streaming_complete",  # MiniMax streaming path completion
    ],
)
def test_provider_success_statuses_pass_gate(status: str) -> None:
    """All known platform success statuses must be accepted.

    Regression: DashScope reports finish_reason="stop" on success; rejecting
    it caused a 100% synthesis failure rate on the Bailian platform.
    """
    decision = _evaluate_with_provider_status(status)
    assert decision.passed
    assert decision.failure_category == ""


@pytest.mark.parametrize("status", ["length", "truncated", "error", "failed", "3"])
def test_provider_failure_statuses_rejected(status: str) -> None:
    """Non-success statuses are rejected and categorized as provider_status."""
    decision = _evaluate_with_provider_status(status)
    assert not decision.passed
    assert decision.failure_category == "provider_status"
    assert any("供应商报告非完成状态" in w for w in decision.warnings)


def test_acoustic_failure_category_is_acoustic() -> None:
    """Duration/cps failures must be categorized as acoustic (speed-fixable)."""
    decision = evaluate_synthesized_segment(
        _make_request(),
        _make_response(),
        duration_ms=50,  # below minimum_duration_ms
        audio_size_bytes=2048,
        minimum_duration_ms=100,
        maximum_characters_per_second=20.0,
    )
    assert not decision.passed
    assert decision.failure_category == "acoustic"


def test_integrity_failure_category_is_integrity() -> None:
    """Tiny/corrupt payloads must be categorized as integrity."""
    decision = evaluate_synthesized_segment(
        _make_request(),
        _make_response(),
        duration_ms=1000,
        audio_size_bytes=10,  # < 128-byte floor
        minimum_duration_ms=100,
        maximum_characters_per_second=20.0,
    )
    assert not decision.passed
    assert decision.failure_category == "integrity"
