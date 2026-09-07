"""Tests for short blueprint normalization safeguards."""

from __future__ import annotations

from novel_forge.core.schemas.spec import StorySpec
from novel_forge.pipeline.steps.short_blueprint_step import _parse_blueprint


def test_parse_blueprint_backfills_minimum_story_structure() -> None:
    spec = StorySpec(
        title="雾中的来信",
        genre="mystery",
        theme="真相与代价",
        tone="冷峻",
        length_target=2600,
        language="zh",
        conflict_hint="主角必须在一夜之间确认来信真假",
        ending_style="余韵式收束",
    )

    blueprint = _parse_blueprint({}, spec=spec)

    assert blueprint.synopsis == "真相与代价"
    assert 3 <= len(blueprint.narrative_phases) <= 5
    assert blueprint.narrative_phases[0].position_start == 0
    assert blueprint.narrative_phases[-1].position_end == 100
    assert blueprint.turning_points
    assert blueprint.anchor_elements.central_event
    assert blueprint.ending_strategy == "余韵式收束"


def test_parse_blueprint_derives_anchor_from_phase_data() -> None:
    spec = StorySpec(
        title="旧港夜雨",
        genre="thriller",
        theme="信任的试炼",
        tone="压迫",
        length_target=3200,
        language="zh",
    )

    blueprint = _parse_blueprint(
        {
            "synopsis": "沈青在旧港追查失踪案，并在风暴来临前决定是否相信线人。",
            "narrative_phases": [
                {
                    "phase_name": "引入",
                    "position_start": 0,
                    "position_end": 35,
                    "location": "旧港码头",
                    "time_setting": "暴雨将至的傍晚",
                    "characters_present": ["沈青", "线人阿策"],
                    "key_event": "沈青收到一条真假难辨的消息",
                },
                {
                    "phase_name": "高潮",
                    "position_start": 35,
                    "position_end": 80,
                    "location": "废弃仓库",
                    "time_setting": "夜里九点后",
                    "characters_present": ["沈青", "阿策"],
                    "key_event": "两人在仓库内正面对质",
                },
            ],
            "character_arcs": [
                {"character": "沈青", "arc_summary": "", "key_moment": ""},
            ],
        },
        spec=spec,
    )

    assert blueprint.anchor_elements.primary_locations[:2] == ["旧港码头", "废弃仓库"]
    assert [item.name for item in blueprint.anchor_elements.core_characters][:2] == ["沈青", "线人阿策"]
    assert blueprint.anchor_elements.time_frame == "暴雨将至的傍晚至夜里九点后"
    assert blueprint.character_arcs[0].arc_summary
