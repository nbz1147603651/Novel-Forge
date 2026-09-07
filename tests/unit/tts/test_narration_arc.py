"""Unit tests for narration arc (P3-5).

Covers:
- build_narration_arc four-section structure and climax adaptation
- distance_at ratio lookup
- apply_narration_arc script-level behavior (distance writing, override
  respect, metadata, disabled switch, no-narration no-op)
"""

from __future__ import annotations

from novel_forge.tts.pipeline.narration_arc import (
    NarrationArcSegment,
    apply_narration_arc,
    build_narration_arc,
    distance_at,
)
from novel_forge.tts.schemas import DubbingScript, DubbingSegment, EmotionTag, SegmentType


def _segment(
    index: int,
    seg_type: SegmentType = SegmentType.NARRATION,
    *,
    intensity: float = 0.5,
    narrator_distance: str = "",
) -> DubbingSegment:
    return DubbingSegment(
        segment_index=index,
        segment_type=seg_type,
        text="夜色如墨。",
        spoken_text="夜色如墨。",
        emotion=EmotionTag.NEUTRAL,
        emotion_intensity=intensity,
        narrator_distance=narrator_distance,
    )


def _script(count: int = 20, **kwargs: object) -> DubbingScript:
    return DubbingScript(
        chapter_number=1,
        segments=[_segment(i, **kwargs) for i in range(count)],
    )


# ─── build_narration_arc ─────────────────────────────────────────────────────


class TestBuildNarrationArc:
    def test_four_sections_with_expected_order(self) -> None:
        arc = build_narration_arc(_script().segments)
        assert len(arc) == 4
        assert [section.distance for section in arc] == [
            "close",
            "medium",
            "medium",  # 中性强度 → 高潮保持 medium
            "distant",
        ]
        assert arc[0].start_ratio == 0.0
        assert arc[-1].end_ratio == 1.0
        # 相邻区间无缝覆盖。
        for prev, next_sec in zip(arc, arc[1:], strict=False):
            assert prev.end_ratio == next_sec.start_ratio

    def test_high_intensity_climax_becomes_close(self) -> None:
        segments = _script().segments
        # 高潮区（0.55-0.85 比例）内的段拉高情绪强度。
        for i, seg in enumerate(segments):
            if 0.55 <= i / len(segments) < 0.85:
                segments[i] = seg.model_copy(update={"emotion_intensity": 0.9})
        arc = build_narration_arc(segments)
        assert arc[2].distance == "close"

    def test_empty_segments_returns_default_arc(self) -> None:
        arc = build_narration_arc([])
        assert len(arc) == 4
        assert arc[2].distance == "medium"


# ─── distance_at ─────────────────────────────────────────────────────────────


class TestDistanceAt:
    def _arc(self) -> list[NarrationArcSegment]:
        return build_narration_arc(_script().segments)

    def test_ratio_lookup(self) -> None:
        arc = self._arc()
        assert distance_at(arc, 0.05) == "close"
        assert distance_at(arc, 0.3) == "medium"
        assert distance_at(arc, 0.7) == "medium"
        assert distance_at(arc, 0.95) == "distant"

    def test_clamped_ratios(self) -> None:
        arc = self._arc()
        assert distance_at(arc, -1.0) == "close"
        assert distance_at(arc, 2.0) == "distant"

    def test_empty_arc_returns_medium(self) -> None:
        assert distance_at([], 0.5) == "medium"


# ─── apply_narration_arc ─────────────────────────────────────────────────────


class TestApplyNarrationArc:
    def test_writes_distances_and_metadata(self) -> None:
        script = _script()
        result = apply_narration_arc(script)
        distances = {seg.narrator_distance for seg in result.segments}
        assert distances <= {"close", "medium", "distant"}
        assert len(distances) >= 2
        stats = result.metadata["narration_arc"]
        assert stats["updated_narration_segments"] == len(result.segments)
        assert len(stats["sections"]) == 4

    def test_existing_distance_never_overridden(self) -> None:
        segments = [_segment(0, narrator_distance="distant")]
        script = DubbingScript(chapter_number=1, segments=segments)
        result = apply_narration_arc(script)
        assert result.segments[0].narrator_distance == "distant"

    def test_disabled_returns_script_unchanged(self) -> None:
        script = _script()
        assert apply_narration_arc(script, enabled=False) is script

    def test_no_narration_segments_no_op(self) -> None:
        script = DubbingScript(
            chapter_number=1,
            segments=[
                _segment(0, SegmentType.DIALOGUE, intensity=0.7),
                _segment(1, SegmentType.BGM),
            ],
        )
        assert apply_narration_arc(script) is script

    def test_high_intensity_climax_produces_close_segment(self) -> None:
        segments = _script().segments
        for i, seg in enumerate(segments):
            if 0.55 <= i / len(segments) < 0.85:
                segments[i] = seg.model_copy(update={"emotion_intensity": 0.95})
        script = DubbingScript(chapter_number=1, segments=segments)
        result = apply_narration_arc(script)
        assert any(
            seg.narrator_distance == "close"
            for seg in result.segments
            if 0.55 <= seg.segment_index / len(result.segments) < 0.85
        )
