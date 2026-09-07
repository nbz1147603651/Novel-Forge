"""Unit tests for TTS emotion expression improvements (plan v2).

Covers:
- test_emotion_intensity_propagation: emotion_intensity flows from scene_emotion_map
  through _infer_emotion into DubbingSegment.
- test_minimax_stress_words_inline: build_minimax_emotion_text inserts micro-pauses
  around stress_words.
- test_character_trajectory_overrides_scene: character_emotion_trajectories takes
  priority over scene_emotion_map in _infer_emotion.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from novel_forge.tts.pipeline.ssml_builder import build_minimax_emotion_text
from novel_forge.tts.schemas import (
    DubbingSegment,
    EmotionTag,
    ParalinguisticTag,
    SegmentType,
)

# ─── Improvement 1: emotion_intensity propagation ────────────────────────────


class TestEmotionIntensityPropagation:
    """Verify emotion_intensity field on DubbingSegment and its consumption."""

    def test_emotion_intensity_default(self) -> None:
        """DubbingSegment defaults emotion_intensity to 0.5."""
        segment = DubbingSegment(
            segment_index=0,
            segment_type=SegmentType.NARRATION,
            text="测试文本",
        )
        assert segment.emotion_intensity == 0.5

    def test_emotion_intensity_custom_value(self) -> None:
        """DubbingSegment accepts custom emotion_intensity."""
        segment = DubbingSegment(
            segment_index=0,
            segment_type=SegmentType.DIALOGUE,
            text="我绝不会放弃！",
            emotion=EmotionTag.DETERMINED,
            emotion_intensity=0.9,
        )
        assert segment.emotion_intensity == 0.9

    def test_emotion_intensity_bounds(self) -> None:
        """emotion_intensity must be in [0.0, 1.0]."""
        with pytest.raises(ValidationError):
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="测试",
                emotion_intensity=1.5,
            )

    def test_infer_emotion_returns_intensity_from_scene_map(self) -> None:
        """_infer_emotion extracts emotion_intensity from scene_emotion_map."""
        from novel_forge.tts.pipeline.generate_script_step import GenerateDubbingScriptStep

        step = GenerateDubbingScriptStep.__new__(GenerateDubbingScriptStep)
        step._tts_metadata = {
            "scene_emotion_map": [
                {
                    "scene_id": "scene_1",
                    "dominant_emotion": "angry",
                    "emotion_intensity": 0.7,
                }
            ],
            "character_emotion_trajectories": {},
        }

        emotion, intensity = step._infer_emotion(
            "他怒吼道", 0, 4, scene_id="scene_1"
        )
        assert emotion == EmotionTag.ANGRY
        assert intensity == 0.7

    def test_infer_emotion_fallback_returns_default_intensity(self) -> None:
        """_infer_emotion keyword fallback returns intensity 0.5."""
        from novel_forge.tts.pipeline.generate_script_step import GenerateDubbingScriptStep

        step = GenerateDubbingScriptStep.__new__(GenerateDubbingScriptStep)
        step._tts_metadata = None

        emotion, intensity = step._infer_emotion(
            "他开心地笑了", 0, 6, scene_id=None
        )
        assert intensity == 0.5


# ─── Improvement 3: MiniMax stress_words inline ──────────────────────────────


class TestMinimaxStressWordsInline:
    """Verify build_minimax_emotion_text inserts micro-pauses for stress_words."""

    def test_stress_words_insert_micro_pauses(self) -> None:
        """stress_words get <#0.05#> micro-pauses around them."""
        segment = DubbingSegment(
            segment_index=0,
            segment_type=SegmentType.DIALOGUE,
            text="我绝不会放弃今晚的计划",
            stress_words=["绝不", "今晚"],
        )
        result = build_minimax_emotion_text(segment, model_id="speech-2.8-hd")
        assert "<#0.05#>绝不<#0.05#>" in result
        assert "<#0.05#>今晚<#0.05#>" in result

    def test_stress_words_only_first_occurrence(self) -> None:
        """Only the first occurrence of each stress word is marked."""
        segment = DubbingSegment(
            segment_index=0,
            segment_type=SegmentType.DIALOGUE,
            text="绝不退缩，绝不动摇",
            stress_words=["绝不"],
        )
        result = build_minimax_emotion_text(segment, model_id="speech-2.8-hd")
        assert result.count("<#0.05#>绝不<#0.05#>") == 1

    def test_stress_words_with_paralinguistic_tags(self) -> None:
        """stress_words work alongside paralinguistic_tags."""
        segment = DubbingSegment(
            segment_index=0,
            segment_type=SegmentType.DIALOGUE,
            text="他微笑着说了再见",
            stress_words=["再见"],
            paralinguistic_tags=[
                ParalinguisticTag(tag_type="chuckle", position=0.0),
            ],
        )
        result = build_minimax_emotion_text(segment, model_id="speech-2.8-hd")
        assert "(chuckle)" in result
        assert "<#0.05#>再见<#0.05#>" in result

    def test_no_stress_words_no_change(self) -> None:
        """Without stress_words, text is unchanged (no paralinguistic_tags)."""
        segment = DubbingSegment(
            segment_index=0,
            segment_type=SegmentType.NARRATION,
            text="平静的叙述",
        )
        result = build_minimax_emotion_text(segment, model_id="speech-2.8-hd")
        assert result == "平静的叙述"

    def test_stress_word_not_in_text(self) -> None:
        """stress_word not present in text is silently skipped."""
        segment = DubbingSegment(
            segment_index=0,
            segment_type=SegmentType.DIALOGUE,
            text="你好世界",
            stress_words=["不存在"],
        )
        result = build_minimax_emotion_text(segment, model_id="speech-2.8-hd")
        assert result == "你好世界"


# ─── Improvement 4: character_emotion_trajectories override ──────────────────


class TestCharacterTrajectoryOverridesScene:
    """Verify character trajectory takes priority over scene_emotion_map."""

    def test_character_trajectory_overrides_scene(self) -> None:
        """Character trajectory emotion wins over scene-level emotion."""
        from novel_forge.tts.pipeline.generate_script_step import GenerateDubbingScriptStep

        step = GenerateDubbingScriptStep.__new__(GenerateDubbingScriptStep)
        step._tts_metadata = {
            "scene_emotion_map": [
                {
                    "scene_id": "scene_1",
                    "dominant_emotion": "calm",
                    "emotion_intensity": 0.5,
                }
            ],
            "character_emotion_trajectories": {
                "林晓": [
                    {
                        "scene_id": "scene_1",
                        "emotion": "angry",
                        "intensity": 0.8,
                        "beat": "愤怒爆发",
                    }
                ]
            },
        }

        emotion, intensity = step._infer_emotion(
            "林晓怒吼", 0, 4, scene_id="scene_1", character_name="林晓"
        )
        # Character trajectory should override scene-level calm
        assert emotion == EmotionTag.ANGRY
        assert intensity == 0.8

    def test_scene_emotion_used_when_no_character_trajectory(self) -> None:
        """Falls back to scene_emotion_map when character has no trajectory."""
        from novel_forge.tts.pipeline.generate_script_step import GenerateDubbingScriptStep

        step = GenerateDubbingScriptStep.__new__(GenerateDubbingScriptStep)
        step._tts_metadata = {
            "scene_emotion_map": [
                {
                    "scene_id": "scene_1",
                    "dominant_emotion": "sad",
                    "emotion_intensity": 0.6,
                }
            ],
            "character_emotion_trajectories": {
                "其他角色": [
                    {"scene_id": "scene_1", "emotion": "happy", "intensity": 0.9}
                ]
            },
        }

        emotion, intensity = step._infer_emotion(
            "她低声说", 0, 4, scene_id="scene_1", character_name="林晓"
        )
        # No trajectory for 林晓, falls back to scene-level sad
        assert emotion == EmotionTag.SAD
        assert intensity == 0.6

    def test_keyword_fallback_when_no_metadata(self) -> None:
        """Falls back to keyword inference when no metadata matches."""
        from novel_forge.tts.pipeline.generate_script_step import GenerateDubbingScriptStep

        step = GenerateDubbingScriptStep.__new__(GenerateDubbingScriptStep)
        step._tts_metadata = {
            "scene_emotion_map": [],
            "character_emotion_trajectories": {},
        }

        emotion, intensity = step._infer_emotion(
            "他恐惧地后退", 0, 6, scene_id="scene_99", character_name="张三"
        )
        # No scene match, falls back to keyword inference
        assert intensity == 0.5
