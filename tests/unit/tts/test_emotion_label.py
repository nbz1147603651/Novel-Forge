"""Unit tests for LLM emotion fine-labeling (P3-1).

Covers:
- _window_batches continuous-window splitting (BGM/SFX breaks windows)
- _coerce_emotion / _coerce_sub_emotion / _coerce_intensity validation gates
- _parse_label_response batch parsing (dedup, unknown indexes, sub==main)
- phase_emotion_label integration with a mocked LLM service
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.tts.pipeline.script_phases.emotion_label import (
    _coerce_emotion,
    _coerce_intensity,
    _coerce_sub_emotion,
    _parse_label_response,
    _window_batches,
    phase_emotion_label,
)
from novel_forge.tts.schemas import DubbingScript, DubbingSegment, EmotionTag, SegmentType


def _segment(
    index: int,
    seg_type: SegmentType = SegmentType.NARRATION,
    *,
    text: str = "内容",
    emotion: EmotionTag = EmotionTag.NEUTRAL,
    intensity: float = 0.5,
    character_name: str = "",
    scene_context: str = "",
) -> DubbingSegment:
    return DubbingSegment(
        segment_index=index,
        segment_type=seg_type,
        text=text,
        spoken_text=text,
        emotion=emotion,
        emotion_intensity=intensity,
        character_name=character_name,
        scene_context=scene_context,
    )


# ─── _window_batches ─────────────────────────────────────────────────────────


class TestWindowBatches:
    def test_windows_split_at_size(self) -> None:
        segments = [_segment(i) for i in range(10)]
        batches = _window_batches(segments, window=6)
        assert [len(batch) for batch in batches] == [6, 4]

    def test_non_labelable_breaks_window(self) -> None:
        segments = [
            _segment(0),
            _segment(1, SegmentType.BGM),
            _segment(2),
            _segment(3),
        ]
        batches = _window_batches(segments, window=6)
        assert [len(batch) for batch in batches] == [1, 2]

    def test_empty_input(self) -> None:
        assert _window_batches([], window=6) == []


# ─── Validation gates ────────────────────────────────────────────────────────


class TestCoercion:
    def test_valid_emotion_passthrough(self) -> None:
        assert _coerce_emotion("happy", "随便") == EmotionTag.HAPPY
        assert _coerce_emotion("HAPPY", "随便") == EmotionTag.HAPPY

    def test_unknown_emotion_falls_back_to_keyword_inference(self) -> None:
        # "他愤怒地砸碎了杯子" infers ANGRY via keywords.
        assert _coerce_emotion("ecstatic_unknown", "他愤怒地砸碎了杯子") == EmotionTag.ANGRY

    def test_unknown_emotion_with_neutral_fallback_returns_none(self) -> None:
        assert _coerce_emotion("made_up_label", "平淡的描述文字") is None

    def test_none_returns_none(self) -> None:
        assert _coerce_emotion(None, "随便") is None

    def test_sub_emotion_unknown_dropped(self) -> None:
        assert _coerce_sub_emotion("nonsense") is None
        assert _coerce_sub_emotion(None) is None
        assert _coerce_sub_emotion("neutral") is None
        assert _coerce_sub_emotion("tender") == EmotionTag.TENDER

    def test_intensity_clamped(self) -> None:
        assert _coerce_intensity(1.7, 0.5) == 1.0
        assert _coerce_intensity(-0.3, 0.5) == 0.0
        assert _coerce_intensity(0.4, 0.5) == 0.4
        assert _coerce_intensity("oops", 0.5) == 0.5
        assert _coerce_intensity("0.8", 0.5) == 0.8


# ─── _parse_label_response ───────────────────────────────────────────────────


class TestParseLabelResponse:
    def _script(self) -> DubbingScript:
        return DubbingScript(chapter_number=1, segments=[_segment(0), _segment(1)])

    def test_parses_valid_labels(self) -> None:
        script = self._script()
        batch = [(0, script.segments[0]), (1, script.segments[1])]
        response = SimpleNamespace(
            content={
                "emotions": [
                    {"segment_index": 0, "emotion": "angry", "sub_emotion": "sad", "intensity": 0.9},
                    {"segment_index": 1, "emotion": "whisper", "intensity": 0.2},
                ]
            }
        )
        labels = _parse_label_response(response, batch)
        assert labels is not None
        assert labels[0] == {
            "emotion": EmotionTag.ANGRY,
            "sub_emotion": EmotionTag.SAD,
            "emotion_intensity": 0.9,
        }
        assert labels[1]["emotion"] == EmotionTag.WHISPER
        assert labels[1]["sub_emotion"] is None

    def test_sub_emotion_equal_to_emotion_dropped(self) -> None:
        script = self._script()
        batch = [(0, script.segments[0])]
        response = SimpleNamespace(
            content={
                "emotions": [
                    {"segment_index": 0, "emotion": "sad", "sub_emotion": "sad", "intensity": 0.6}
                ]
            }
        )
        labels = _parse_label_response(response, batch)
        assert labels[0]["sub_emotion"] is None

    def test_unknown_segment_index_skipped(self) -> None:
        script = self._script()
        batch = [(0, script.segments[0])]
        response = SimpleNamespace(
            content={"emotions": [{"segment_index": 99, "emotion": "happy", "intensity": 0.8}]}
        )
        assert _parse_label_response(response, batch) == {}

    def test_missing_content_returns_none(self) -> None:
        assert _parse_label_response(SimpleNamespace(content=None), []) is None
        assert _parse_label_response(SimpleNamespace(content={"emotions": "oops"}), []) is None


# ─── phase_emotion_label ─────────────────────────────────────────────────────


def _fake_step(settings: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        _settings=SimpleNamespace(
            tts_emotion_label_enabled=settings.get("enabled", True),
            tts_emotion_label_window=settings.get("window", 6),
        ),
        _router=object(),
        _builder=object(),
        _on_step_event=lambda *args, **kwargs: None,
    )


class TestPhaseEmotionLabel:
    @pytest.mark.asyncio
    async def test_updates_segments_and_writes_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        script = DubbingScript(
            chapter_number=1,
            segments=[_segment(i, text=f"段落{i}") for i in range(3)],
        )
        input_data = SimpleNamespace(chapter_number=1)
        ctx = SimpleNamespace(project=lambda *a, **kw: {})

        async def fake_call(*args: Any, **kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(
                content={
                    "emotions": [
                        {"segment_index": i, "emotion": "angry", "intensity": 0.85}
                        for i in range(3)
                    ]
                }
            )

        monkeypatch.setattr(
            "novel_forge.model_runtime.StructuredModelService",
            lambda **kwargs: SimpleNamespace(call_with_retry=fake_call),
        )

        result = await phase_emotion_label(script, input_data, ctx, _fake_step({}))
        assert all(
            seg.emotion == EmotionTag.ANGRY and seg.emotion_intensity == 0.85
            for seg in result.segments
        )
        stats = result.metadata["emotion_label"]
        assert stats["labeled_segments"] == 3
        assert stats["total_segments"] == 3

    @pytest.mark.asyncio
    async def test_llm_failure_keeps_original_labels(self, monkeypatch: pytest.MonkeyPatch) -> None:
        script = DubbingScript(
            chapter_number=1,
            segments=[_segment(0, emotion=EmotionTag.HAPPY, intensity=0.7)],
        )
        input_data = SimpleNamespace(chapter_number=1)
        ctx = SimpleNamespace(project=lambda *a, **kw: {})

        async def failing_call(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("provider down")

        monkeypatch.setattr(
            "novel_forge.model_runtime.StructuredModelService",
            lambda **kwargs: SimpleNamespace(call_with_retry=failing_call),
        )

        result = await phase_emotion_label(script, input_data, ctx, _fake_step({}))
        # 失败时返回原脚本（不写 metadata），情绪保持原值。
        assert result is script
        assert result.segments[0].emotion == EmotionTag.HAPPY

    @pytest.mark.asyncio
    async def test_disabled_returns_script_unchanged(self) -> None:
        script = DubbingScript(chapter_number=1, segments=[_segment(0)])
        input_data = SimpleNamespace(chapter_number=1)
        result = await phase_emotion_label(
            script, input_data, SimpleNamespace(project=lambda *a, **kw: {}), _fake_step({"enabled": False})
        )
        assert result is script

    @pytest.mark.asyncio
    async def test_unlabelable_script_returns_unchanged(self) -> None:
        script = DubbingScript(chapter_number=1, segments=[_segment(0, SegmentType.BGM)])
        input_data = SimpleNamespace(chapter_number=1)
        result = await phase_emotion_label(
            script, input_data, SimpleNamespace(project=lambda *a, **kw: {}), _fake_step({})
        )
        assert result is script
