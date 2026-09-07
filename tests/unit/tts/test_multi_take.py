"""Unit tests for multi-take selection (P3-3).

Covers:
- take_count policy (intensity threshold, high-energy dialogue, max_takes)
- score_take duration plausibility + emotion-energy consistency
- select_best_take tie-breaking and empty-input guard
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_forge.tts.pipeline.multi_take import (
    TakeEvidence,
    score_take,
    select_best_take,
    take_count,
)
from novel_forge.tts.schemas import DubbingSegment, EmotionTag, SegmentType


def _segment(
    *,
    emotion: EmotionTag = EmotionTag.NEUTRAL,
    intensity: float = 0.5,
    seg_type: SegmentType = SegmentType.NARRATION,
    text: str = "他推开门走了进去，屋里一片安静。",
) -> DubbingSegment:
    return DubbingSegment(
        segment_index=0,
        segment_type=seg_type,
        text=text,
        spoken_text=text,
        emotion=emotion,
        emotion_intensity=intensity,
    )


# ─── take_count ──────────────────────────────────────────────────────────────


class TestTakeCount:
    def test_disabled_returns_single(self) -> None:
        seg = _segment(emotion=EmotionTag.ANGRY, intensity=0.9)
        assert take_count(seg, enabled=False, min_intensity=0.7, max_takes=3) == 1

    def test_low_intensity_returns_single(self) -> None:
        seg = _segment(emotion=EmotionTag.HAPPY, intensity=0.4)
        assert take_count(seg, enabled=True, min_intensity=0.7, max_takes=3) == 1

    def test_intensity_above_threshold_returns_two(self) -> None:
        seg = _segment(emotion=EmotionTag.ANGRY, intensity=0.75)
        assert take_count(seg, enabled=True, min_intensity=0.7, max_takes=3) == 2

    def test_very_high_intensity_returns_three(self) -> None:
        seg = _segment(emotion=EmotionTag.ANGRY, intensity=0.9)
        assert take_count(seg, enabled=True, min_intensity=0.7, max_takes=3) == 3

    def test_max_takes_two_never_returns_three(self) -> None:
        seg = _segment(emotion=EmotionTag.ANGRY, intensity=0.95)
        assert take_count(seg, enabled=True, min_intensity=0.7, max_takes=2) == 2

    def test_high_energy_dialogue_below_threshold_returns_two(self) -> None:
        seg = _segment(emotion=EmotionTag.HAPPY, intensity=0.6, seg_type=SegmentType.DIALOGUE)
        assert take_count(seg, enabled=True, min_intensity=0.7, max_takes=3) == 2

    def test_high_energy_dialogue_too_quiet_returns_single(self) -> None:
        seg = _segment(emotion=EmotionTag.HAPPY, intensity=0.3, seg_type=SegmentType.DIALOGUE)
        assert take_count(seg, enabled=True, min_intensity=0.7, max_takes=3) == 1

    def test_neutral_narration_returns_single(self) -> None:
        seg = _segment(emotion=EmotionTag.NEUTRAL, intensity=0.8)
        assert take_count(seg, enabled=True, min_intensity=0.7, max_takes=3) == 1


# ─── score_take ──────────────────────────────────────────────────────────────


class TestScoreTake:
    def _segment(self, emotion: EmotionTag = EmotionTag.NEUTRAL) -> DubbingSegment:
        return _segment(emotion=emotion, intensity=0.8)

    def test_ideal_duration_and_energy_scores_high(self) -> None:
        seg = self._segment(emotion=EmotionTag.ANGRY)
        # ~14 chars × 230ms = ~3220ms; 3200ms is near ideal; loud RMS matches angry.
        evidence = TakeEvidence(
            audio_path=Path("/tmp/x.wav"),
            duration_ms=3200,
            rms_dbfs=-20.0,
        )
        score = score_take(seg, evidence, speed=1.0)
        assert 0.9 <= score <= 1.0

    def test_truncated_take_scores_lower(self) -> None:
        seg = self._segment()
        evidence = TakeEvidence(
            audio_path=Path("/tmp/x.wav"),
            duration_ms=600,
            rms_dbfs=None,
        )
        assert score_take(seg, evidence, speed=1.0) < 0.5

    def test_high_energy_emotion_with_quiet_audio_penalized(self) -> None:
        seg = self._segment(emotion=EmotionTag.ANGRY)
        evidence = TakeEvidence(
            audio_path=Path("/tmp/x.wav"),
            duration_ms=3200,
            rms_dbfs=-40.0,
        )
        quiet_score = score_take(seg, evidence, speed=1.0)

        loud_evidence = TakeEvidence(
            audio_path=Path("/tmp/x.wav"),
            duration_ms=3200,
            rms_dbfs=-15.0,
        )
        loud_score = score_take(seg, loud_evidence, speed=1.0)
        assert loud_score > quiet_score

    def test_quality_warnings_penalize(self) -> None:
        seg = self._segment()
        clean = TakeEvidence(
            audio_path=Path("/tmp/x.wav"),
            duration_ms=3200,
            quality_warnings=(),
        )
        warn = TakeEvidence(
            audio_path=Path("/tmp/x.wav"),
            duration_ms=3200,
            quality_warnings=("静音比例偏高", "供应商报告不可见字符"),
        )
        assert score_take(seg, warn, speed=1.0) < score_take(seg, clean, speed=1.0)

    def test_no_audio_evidence_degrades_gracefully(self) -> None:
        seg = self._segment()
        evidence = TakeEvidence(
            audio_path=Path("/tmp/x.wav"),
            duration_ms=3200,
            rms_dbfs=None,
        )
        assert 0.0 <= score_take(seg, evidence, speed=1.0) <= 1.0


# ─── select_best_take ────────────────────────────────────────────────────────


class TestSelectBestTake:
    def test_picks_highest_score(self) -> None:
        seg = _segment()
        takes = [
            (seg, TakeEvidence(Path("/tmp/a.wav"), duration_ms=800)),
            (seg, TakeEvidence(Path("/tmp/b.wav"), duration_ms=3200)),
            (seg, TakeEvidence(Path("/tmp/c.wav"), duration_ms=20000)),
        ]
        _best_segment, best = select_best_take(takes)
        assert best.audio_path == Path("/tmp/b.wav")

    def test_tie_keeps_first(self) -> None:
        seg = _segment()
        takes = [
            (seg, TakeEvidence(Path("/tmp/a.wav"), duration_ms=3200)),
            (seg, TakeEvidence(Path("/tmp/b.wav"), duration_ms=3200)),
        ]
        _best_segment, best = select_best_take(takes)
        assert best.audio_path == Path("/tmp/a.wav")

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            select_best_take([])
