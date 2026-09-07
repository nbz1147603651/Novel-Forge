"""Unit tests for onomatopoeia tag injection (P3-2).

Covers:
- _onomatopoeia_tag rules (HAPPY/SAD/WHISPER, intensity thresholds)
- speech-2.8 model gating (other models never receive tags)
- build_rule_pause_markers tag placement and idempotency
- inject_pause_markers metadata stats
"""

from __future__ import annotations

from novel_forge.tts.pipeline.pause_marker_injection import (
    _onomatopoeia_tag,
    build_rule_pause_markers,
    inject_pause_markers,
)
from novel_forge.tts.schemas import DubbingScript, DubbingSegment, EmotionTag, SegmentType


def _segment(
    index: int,
    text: str = "他高兴地笑了起来。",
    *,
    emotion: EmotionTag = EmotionTag.NEUTRAL,
    intensity: float = 0.5,
    seg_type: SegmentType = SegmentType.NARRATION,
    character_name: str = "",
) -> DubbingSegment:
    return DubbingSegment(
        segment_index=index,
        segment_type=seg_type,
        text=text,
        spoken_text=text,
        emotion=emotion,
        emotion_intensity=intensity,
        character_name=character_name,
    )


def _script(segments: list[DubbingSegment]) -> DubbingScript:
    return DubbingScript(chapter_number=1, segments=segments)


# ─── _onomatopoeia_tag ───────────────────────────────────────────────────────


class TestOnomatopoeiaTag:
    def test_happy_high_intensity_gets_laughs(self) -> None:
        seg = _segment(0, emotion=EmotionTag.HAPPY, intensity=0.8)
        assert (
            _onomatopoeia_tag(seg, model_id="speech-2.8-hd", enabled=True, min_intensity=0.6)
            == "(laughs)"
        )

    def test_happy_below_threshold_no_tag(self) -> None:
        seg = _segment(0, emotion=EmotionTag.HAPPY, intensity=0.4)
        assert _onomatopoeia_tag(seg, model_id="speech-2.8-hd", enabled=True, min_intensity=0.6) == ""

    def test_sad_gets_sighs(self) -> None:
        seg = _segment(0, emotion=EmotionTag.SAD, intensity=0.8)
        assert (
            _onomatopoeia_tag(seg, model_id="speech-2.8-turbo", enabled=True, min_intensity=0.6)
            == "(sighs)"
        )

    def test_whisper_unconditional(self) -> None:
        seg = _segment(0, emotion=EmotionTag.WHISPER, intensity=0.1)
        assert (
            _onomatopoeia_tag(seg, model_id="speech-2.8-hd", enabled=True, min_intensity=0.6)
            == "(whisper)"
        )

    def test_other_models_never_get_tags(self) -> None:
        seg = _segment(0, emotion=EmotionTag.HAPPY, intensity=0.9)
        assert _onomatopoeia_tag(seg, model_id="speech-2.5-hd", enabled=True, min_intensity=0.6) == ""
        assert _onomatopoeia_tag(seg, model_id="", enabled=True, min_intensity=0.6) == ""
        assert _onomatopoeia_tag(seg, model_id="speech-2.8-hd", enabled=False, min_intensity=0.6) == ""

    def test_neutral_emotion_no_tag(self) -> None:
        seg = _segment(0, emotion=EmotionTag.NEUTRAL, intensity=0.9)
        assert _onomatopoeia_tag(seg, model_id="speech-2.8-hd", enabled=True, min_intensity=0.6) == ""


# ─── build_rule_pause_markers ────────────────────────────────────────────────


class TestBuildRulePauseMarkers:
    def test_tag_prepended_before_text(self) -> None:
        seg = _segment(0, "太棒了！", emotion=EmotionTag.HAPPY, intensity=0.9)
        result = build_rule_pause_markers(
            seg,
            model_id="speech-2.8-hd",
            onomatopoeia_enabled=True,
            onomatopoeia_min_intensity=0.6,
        )
        assert result.startswith("(laughs) ")
        assert "太棒了！" in result

    def test_tag_after_leading_pause_marker(self) -> None:
        prev = _segment(
            0, "我走了。", seg_type=SegmentType.DIALOGUE, character_name="甲"
        )
        seg = _segment(1, "太好了！", emotion=EmotionTag.HAPPY, intensity=0.9)
        result = build_rule_pause_markers(
            seg,
            prev,
            model_id="speech-2.8-hd",
            onomatopoeia_enabled=True,
            onomatopoeia_min_intensity=0.6,
        )
        # 对话转旁白停顿标记在最前，拟声标签紧随其后。
        assert result.startswith("<#0.8#>")
        assert "(laughs) " in result

    def test_idempotent_on_rerun(self) -> None:
        seg = _segment(0, "太好了！", emotion=EmotionTag.HAPPY, intensity=0.9)
        once = build_rule_pause_markers(
            seg,
            model_id="speech-2.8-hd",
            onomatopoeia_enabled=True,
            onomatopoeia_min_intensity=0.6,
        )
        twice_seg = seg.model_copy(update={"spoken_text": once})
        twice = build_rule_pause_markers(
            twice_seg,
            model_id="speech-2.8-hd",
            onomatopoeia_enabled=True,
            onomatopoeia_min_intensity=0.6,
        )
        assert twice == once
        assert twice.count("(laughs)") == 1

    def test_non_speech28_model_only_pause_markers(self) -> None:
        seg = _segment(0, "太好了！", emotion=EmotionTag.HAPPY, intensity=0.9)
        result = build_rule_pause_markers(
            seg,
            model_id="speech-2.5-hd",
            onomatopoeia_enabled=True,
            onomatopoeia_min_intensity=0.6,
        )
        assert "(laughs)" not in result
        assert "<#0.4#>" in result


# ─── inject_pause_markers ────────────────────────────────────────────────────


class TestInjectPauseMarkers:
    def test_metadata_reports_tag_count(self) -> None:
        segments = [
            _segment(0, emotion=EmotionTag.HAPPY, intensity=0.9),
            _segment(1, "平静的叙述。", emotion=EmotionTag.NEUTRAL, intensity=0.5),
            _segment(2, "不要走……", emotion=EmotionTag.SAD, intensity=0.8),
        ]
        script = _script(segments)
        result = inject_pause_markers(
            script,
            platform="minimax",
            model_id="speech-2.8-hd",
            onomatopoeia_enabled=True,
            onomatopoeia_min_intensity=0.6,
        )
        stats = result.metadata["pause_marker_injection"]
        assert stats["onomatopoeia_segments"] == 2  # (laughs) + (sighs)
        assert stats["model_id"] == "speech-2.8-hd"
        assert result.segments[0].spoken_text.startswith("(laughs)")

    def test_onomatopoeia_disabled_keeps_pause_markers(self) -> None:
        segments = [_segment(0, emotion=EmotionTag.HAPPY, intensity=0.9)]
        script = _script(segments)
        result = inject_pause_markers(
            script,
            platform="minimax",
            model_id="speech-2.8-hd",
            onomatopoeia_enabled=False,
        )
        assert "(laughs)" not in result.segments[0].spoken_text
        assert "<#" in result.segments[0].spoken_text
