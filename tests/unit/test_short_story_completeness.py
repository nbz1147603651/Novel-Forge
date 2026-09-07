"""Tests for short-story completeness safeguards."""

from __future__ import annotations

from novel_forge.core.schemas.beats import StoryBeats
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.pipeline.short_runner import ShortStoryRunner
from novel_forge.pipeline.steps.beats_step import BeatsStep


def test_beats_normalization_enforces_complete_arc() -> None:
    payload = BeatsStep._normalize_beats_payload(
        {
            "beats": [
                {"sequence": 1, "summary": "人物在旧站台醒来。", "tension_level": 2},
                {"sequence": 2, "summary": "他发现一封改变命运的信。", "tension_level": 7},
                {"sequence": 3, "summary": "他决定推门进入黑暗。", "tension_level": 5},
            ]
        }
    )

    beats = StoryBeats.model_validate(payload)

    assert beats.beats[0].beat_type.value == "opening"
    assert beats.beats[1].beat_type.value == "climax"
    assert beats.beats[-1].beat_type.value == "resolution"


def test_short_story_completeness_check_flags_to_be_continued_ending(
    router,
    builder,
    tmp_storage,
    runtime_settings,
) -> None:
    runner = ShortStoryRunner(router, builder, tmp_storage, settings=runtime_settings, max_edit_rounds=1)
    spec = StorySpec(
        title="旧信",
        genre="mystery",
        theme="真相与代价",
        tone="克制",
        length_target=2000,
        language="zh",
    )
    execution_plan = {
        "completion_contract": {
            "core_question": "主角是否拆开旧信并接受真相",
            "resolution_target": "主角在雨夜里做出选择并完成情绪落点",
            "ending_strategy": "余韵式收束",
            "final_emotional_landing": "释然",
        }
    }

    report = runner._check_short_story_completeness(
        "她终于摸到门把手，黑暗里传来新的脚步声。待续",
        spec,
        execution_plan,
    )

    assert report["passed"] is False
    assert any("待续" in issue or "完整" in issue for issue in report["issues"])
