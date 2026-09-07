from __future__ import annotations

from novel_forge.pipeline.short._shared import (
    project_short_blueprint_prompt_card,
    project_short_execution_prompt_card,
)


def test_short_prompt_cards_keep_authority_and_drop_stage_irrelevant_payload() -> None:
    blueprint = {
        "synopsis": "主角在急诊室做出选择。",
        "emotional_arc": "犹疑到承担",
        "ending_strategy": "HE 且完整收束",
        "anchor_elements": {
            "time_frame": "一夜",
            "primary_locations": ["急诊室", "走廊", "门口", "天台", "多余地点"],
            "core_characters": [
                {"name": f"角色{index}", "role": "决策者"} for index in range(10)
            ],
            "central_event": "签署决定",
        },
        "turning_points": [
            {"description": f"转折{index}", "position_percent": index * 10}
            for index in range(8)
        ],
        "narrative_phases": [{"large": "payload"}],
        "character_arcs": [{"large": "payload"}],
        "element_selection": {
            "extension_elements": [
                {"name": f"要素{index}", "prompt_hint": "抽象实现"} for index in range(8)
            ]
        },
    }
    execution_plan = {
        "opening_contract": "从雨声开场",
        "ending_contract": "决定已执行",
        "completion_contract": {"resolution_target": "承担后果"},
        "anchor_guardrails": {"locations": ["急诊室"]},
        "beat_execution_plan": [
            {
                "sequence": 1,
                "word_budget": 500,
                "phase_name": "选择",
                "phase_goal": "做出决定",
                "private_debug": "must not leak",
            }
        ],
        "private_debug": "must not leak",
    }

    draft_blueprint = project_short_blueprint_prompt_card(blueprint, stage="draft")
    edit_blueprint = project_short_blueprint_prompt_card(blueprint, stage="edit")
    draft_plan = project_short_execution_prompt_card(execution_plan, stage="draft")
    eval_plan = project_short_execution_prompt_card(execution_plan, stage="evaluate")

    assert draft_blueprint["ending_strategy"].startswith("HE")
    assert len(draft_blueprint["anchor_elements"]["primary_locations"]) == 4
    assert len(draft_blueprint["anchor_elements"]["core_characters"]) == 8
    assert len(draft_blueprint["turning_points"]) == 5
    assert len(draft_blueprint["element_selection"]["extension_elements"]) == 6
    assert "narrative_phases" not in draft_blueprint
    assert "turning_points" not in edit_blueprint
    assert "private_debug" not in str(draft_plan)
    assert "beat_execution_plan" in draft_plan
    assert "beat_execution_plan" not in eval_plan
    assert eval_plan["ending_contract"] == "决定已执行"
