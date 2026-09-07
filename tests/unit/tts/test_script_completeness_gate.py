"""Unit tests for the script completeness gate.

Covers:
- Gate passes when all thresholds are met
- Gate rejects when spoken_text coverage is below threshold
- Gate rejects when emotion differentiation is below threshold
- Gate rejects when voice assignment is incomplete
- Gate rejects when LLM rewrite has hard failures
- Gate disabled via settings
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.tts.pipeline.script_completeness_gate import validate_script_completeness
from novel_forge.tts.schemas import (
    DubbingScript,
    DubbingSegment,
    EmotionTag,
    SegmentType,
    VoiceCastEntry,
    VoiceTeamContract,
)


def _make_settings(
    *,
    gate_enabled: bool = True,
    spoken_coverage: float = 0.6,
    emotion_diff: float = 0.15,
) -> SimpleNamespace:
    return SimpleNamespace(
        tts_script_gate_enabled=gate_enabled,
        tts_script_gate_spoken_text_coverage=spoken_coverage,
        tts_script_gate_emotion_differentiation=emotion_diff,
    )


def _make_voice_team() -> VoiceTeamContract:
    return VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="char_a",
                character_name="角色A",
                voice_id="voice_001",
            ),
        ],
        narrator_voice_id="narrator_001",
    )


def _make_segment(
    index: int,
    *,
    seg_type: SegmentType = SegmentType.NARRATION,
    spoken_text: str = "",
    emotion: EmotionTag = EmotionTag.NEUTRAL,
    intensity: float = 0.5,
    character_id: str = "",
) -> DubbingSegment:
    return DubbingSegment(
        segment_index=index,
        segment_type=seg_type,
        text=f"测试文本段落{index}",
        spoken_text=spoken_text,
        emotion=emotion,
        emotion_intensity=intensity,
        character_id=character_id,
    )


class TestCompletenessGatePass:
    """Tests where the gate should pass."""

    def test_all_thresholds_met(self) -> None:
        segments = [
            _make_segment(0, spoken_text="口语文本0", emotion=EmotionTag.HAPPY, intensity=0.7),
            _make_segment(1, spoken_text="口语文本1", emotion=EmotionTag.SAD, intensity=0.8),
            _make_segment(
                2,
                seg_type=SegmentType.DIALOGUE,
                spoken_text="对话文本",
                emotion=EmotionTag.ANGRY,
                intensity=0.9,
                character_id="char_a",
            ),
        ]
        script = DubbingScript(chapter_number=1, segments=segments)
        report = validate_script_completeness(script, _make_voice_team(), _make_settings())
        assert report.passed is True
        assert report.failures == []

    def test_gate_disabled_always_passes(self) -> None:
        segments = [_make_segment(0)]  # No spoken_text, neutral emotion
        script = DubbingScript(chapter_number=1, segments=segments)
        report = validate_script_completeness(
            script, _make_voice_team(), _make_settings(gate_enabled=False)
        )
        assert report.passed is True

    def test_empty_script_passes(self) -> None:
        script = DubbingScript(chapter_number=1, segments=[])
        report = validate_script_completeness(script, _make_voice_team(), _make_settings())
        assert report.passed is True


class TestCompletenessGateSpokenTextCoverage:
    """Tests for spoken_text coverage threshold."""

    def test_below_threshold_fails(self) -> None:
        # 5 segments, only 2 have spoken_text → 40% < 60%
        segments = [
            _make_segment(0, spoken_text="有口语"),
            _make_segment(1, spoken_text="有口语"),
            _make_segment(2, spoken_text=""),
            _make_segment(3, spoken_text=""),
            _make_segment(4, spoken_text=""),
        ]
        script = DubbingScript(chapter_number=1, segments=segments)
        report = validate_script_completeness(script, _make_voice_team(), _make_settings())
        assert report.passed is False
        assert any("spoken_text" in f for f in report.failures)
        assert report.spoken_text_coverage == pytest.approx(0.4, abs=0.01)

    def test_at_threshold_passes(self) -> None:
        # 5 segments, 3 have spoken_text → 60% == 60%
        segments = [
            _make_segment(0, spoken_text="有口语", emotion=EmotionTag.HAPPY, intensity=0.7),
            _make_segment(1, spoken_text="有口语", emotion=EmotionTag.SAD, intensity=0.8),
            _make_segment(2, spoken_text="有口语", emotion=EmotionTag.ANGRY, intensity=0.9),
            _make_segment(3),
            _make_segment(4),
        ]
        script = DubbingScript(chapter_number=1, segments=segments)
        report = validate_script_completeness(script, _make_voice_team(), _make_settings())
        assert report.spoken_text_coverage == pytest.approx(0.6, abs=0.01)


class TestCompletenessGateEmotionDifferentiation:
    """Tests for emotion differentiation threshold."""

    def test_short_neutral_passage_is_not_forced_to_fake_emotion(self) -> None:
        segments = [
            _make_segment(0, spoken_text="平静开场"),
            _make_segment(1, spoken_text="平静收束"),
        ]
        script = DubbingScript(chapter_number=1, segments=segments)

        report = validate_script_completeness(script, _make_voice_team(), _make_settings())

        assert report.passed is True
        assert report.emotion_differentiation == 0.0

    def test_non_spoken_cues_do_not_dilute_emotion_ratio(self) -> None:
        segments = [
            _make_segment(0, spoken_text="有情绪", emotion=EmotionTag.HAPPY, intensity=0.8),
            _make_segment(1, spoken_text="平静"),
            _make_segment(2, spoken_text="平静"),
            _make_segment(3, seg_type=SegmentType.SILENCE),
            _make_segment(4, seg_type=SegmentType.SFX),
        ]
        script = DubbingScript(chapter_number=1, segments=segments)

        report = validate_script_completeness(script, _make_voice_team(), _make_settings())

        assert report.passed is True
        assert report.emotion_differentiation == pytest.approx(1 / 3, abs=0.01)

    def test_all_neutral_fails(self) -> None:
        segments = [
            _make_segment(i, spoken_text=f"口语{i}") for i in range(10)
        ]
        script = DubbingScript(chapter_number=1, segments=segments)
        report = validate_script_completeness(script, _make_voice_team(), _make_settings())
        assert report.emotion_differentiation == 0.0
        assert any("情绪分化" in f for f in report.failures)

    def test_sufficient_differentiation_passes(self) -> None:
        # 2/10 non-neutral = 20% > 15%
        segments = [
            _make_segment(0, spoken_text="口语", emotion=EmotionTag.HAPPY, intensity=0.8),
            _make_segment(1, spoken_text="口语", emotion=EmotionTag.SAD, intensity=0.7),
        ] + [_make_segment(i + 2, spoken_text="口语") for i in range(8)]
        script = DubbingScript(chapter_number=1, segments=segments)
        report = validate_script_completeness(script, _make_voice_team(), _make_settings())
        assert report.emotion_differentiation == pytest.approx(0.2, abs=0.01)


class TestCompletenessGateVoiceAssignment:
    """Tests for voice assignment coverage."""

    def test_dialogue_without_character_fails(self) -> None:
        segments = [
            _make_segment(0, spoken_text="口语", emotion=EmotionTag.HAPPY, intensity=0.8),
            _make_segment(
                1,
                seg_type=SegmentType.DIALOGUE,
                spoken_text="对话",
                emotion=EmotionTag.ANGRY,
                intensity=0.9,
                character_id="",  # Missing!
            ),
        ]
        script = DubbingScript(chapter_number=1, segments=segments)
        report = validate_script_completeness(script, _make_voice_team(), _make_settings())
        assert report.passed is False
        assert any("角色分配" in f for f in report.failures)


class TestCompletenessGateLLMFailure:
    """Tests for LLM rewrite hard failure check."""

    def test_llm_call_failed_in_metadata_fails(self) -> None:
        segments = [
            _make_segment(0, spoken_text="口语", emotion=EmotionTag.HAPPY, intensity=0.8),
        ]
        script = DubbingScript(
            chapter_number=1,
            segments=segments,
            metadata={
                "spoken_text_rewrite": {
                    "rejection_reasons": {"llm_call_failed": 5},
                }
            },
        )
        report = validate_script_completeness(script, _make_voice_team(), _make_settings())
        assert report.passed is False
        assert any("LLM" in f for f in report.failures)
