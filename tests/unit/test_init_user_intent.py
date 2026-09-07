from __future__ import annotations

from novel_forge.core.schemas.spec import StorySpec
from novel_forge.core.user_intent import (
    build_story_spec_user_intent_card,
    project_user_intent_guard_constraints,
    with_chapter_instruction,
)
from novel_forge.pipeline.long.services.init.init_user_intent import (
    build_user_intent_card,
    candidate_intent_conflicts,
    enforce_explicit_input_on_story_spec,
)


def _payload() -> dict[str, object]:
    return {
        "init_input": {
            "premise": "  记忆回收师发现自己的过去被改写  ",
            "pov_hint": "单 POV",
            "ending_style": "HE 圆满结局",
            "world_hint": "记忆不能无代价复制",
            "extra_instructions": "",
        },
        "generation_options": {
            "blueprint_element_preferences": {
                "items": [
                    {"element_id": "hidden_identity", "enabled": True, "locked": True},
                    {"element_id": "time_travel", "enabled": False, "locked": True},
                    {"element_id": "comic_relief", "enabled": True, "locked": False},
                ]
            }
        },
    }


def test_user_intent_card_projects_only_nonempty_input_and_locked_choices() -> None:
    card = build_user_intent_card(_payload())

    explicit = {item["field"]: item["value"] for item in card["explicit_intents"]}
    assert explicit["premise"] == "记忆回收师发现自己的过去被改写"
    assert "extra_instructions" not in explicit
    assert card["locked_blueprint_elements"] == [
        {
            "intent_id": "locked_element:hidden_identity",
            "element_id": "hidden_identity",
            "decision": "include",
        },
        {
            "intent_id": "locked_element:time_travel",
            "element_id": "time_travel",
            "decision": "exclude",
        },
    ]
    assert card["authority"] == "highest"


def test_candidate_rejects_he_single_pov_and_locked_exclude_conflicts() -> None:
    card = build_user_intent_card(_payload())
    conflicts = candidate_intent_conflicts(
        user_intent=card,
        preserved_intent_ids=list(card["immutable_intent_ids"]),
        declared_conflicts=[],
        scene_potential=["以多 POV 切换推进，最终走向悲剧结局", "time_travel"],
    )

    assert any("HE" in item for item in conflicts)
    assert any("单 POV" in item for item in conflicts)
    assert any("locked exclude" in item for item in conflicts)


def test_candidate_must_acknowledge_every_immutable_intent() -> None:
    card = build_user_intent_card(_payload())
    conflicts = candidate_intent_conflicts(
        user_intent=card,
        preserved_intent_ids=[],
        declared_conflicts=[],
        scene_potential=[],
    )

    assert len(conflicts) == len(card["immutable_intent_ids"])


def test_spec_enrichment_cannot_rewrite_nonempty_user_input() -> None:
    generated = StorySpec(
        title="AI 改写的书名",
        genre="tragedy",
        theme="AI 改写的前提",
        tone="dark",
        length_target=1000,
        pov_hint="多 POV",
        ending_style="BE",
    )

    protected, changed = enforce_explicit_input_on_story_spec(
        generated,
        _payload(),
        expected_total_words=150000,
    )

    assert protected.theme == "记忆回收师发现自己的过去被改写"
    assert protected.pov_hint == "单 POV"
    assert protected.ending_style == "HE 圆满结局"
    assert protected.length_target == 150000
    assert {"theme", "pov_hint", "ending_style", "length_target"} <= set(changed)


def test_story_spec_card_and_chapter_instruction_keep_original_authority() -> None:
    card = build_story_spec_user_intent_card(
        {
            "theme": "失忆侦探追查自己的旧案",
            "pov_hint": "单 POV",
            "ending_style": "HE",
            "extra_instructions": "不要解释所有谜团",
        }
    )

    projected = with_chapter_instruction(
        card,
        "本章结尾只写雨声，不揭晓凶手。",
        chapter_number=3,
    )

    assert projected["chapter_instruction"]["chapter_number"] == 3
    assert projected["immutable_intent_ids"][0] == "user:chapter_instruction:3"
    assert projected["explicit_intents"][0]["field"] == "chapter_instruction"
    assert card.get("chapter_instruction") is None


def test_intent_guard_constraints_prohibit_contradiction_not_omission() -> None:
    card = build_story_spec_user_intent_card(
        {
            "theme": "一次不能被撤销的选择",
            "ending_style": "HE，两人共同生活",
            "pov_hint": "单 POV",
        },
        blueprint_element_preferences={
            "items": [
                {"element_id": "amnesia", "enabled": False, "locked": True},
                {"element_id": "reconciliation", "enabled": True, "locked": True},
            ]
        },
    )

    constraints = project_user_intent_guard_constraints(card)

    assert len(constraints) == 1
    assert set(constraints[0]["intent_ids"]) == set(card["immutable_intent_ids"])
    assert "未涉及" in constraints[0]["constraint"]
    assert "不得引入" in constraints[0]["constraint"]
    assert "可以不展开" in constraints[0]["constraint"]
