"""Regression tests for outline batch response normalization."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.format_contracts import validate_json_output_contract
from novel_forge.pipeline.long.services.init.init_outline_batch import _outline_title_repair_numbers
from novel_forge.pipeline.long.services.task_output_adapters import apply_task_output_adapter
from novel_forge.pipeline.long.services.task_semantic_contracts import (
    TaskSemanticContractError,
    validate_task_semantic_contract,
)


def _chapter_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chapter_number": 22,
        "title": "信物归来",
        "goal": "古董店重逢关键时刻。沈念卿触发信物共鸣。",
        "beats_summary": ["沈念卿走进古董店，金镯触发记忆碎片。"],
        "main_plot_points": ["沈鹤卿亲手将金镯归还给沈念卿。"],
        "subplot_points": [],
        "subplot_focus": "",
        "element_focus": ["foreshadowing_thread"],
        "pov_character_id": "char_shen_nianqing",
        "pov_character_name": "沈念卿",
        "pov_character": "沈念卿",
        "pov_switch": False,
        "setting": "老城厢古董店",
        "expected_word_count": 4500,
        "involved_character_ids": ["char_shen_nianqing", "char_shen_heqing"],
        "required_character_ids": ["char_shen_nianqing"],
        "support_character_ids": ["char_shen_heqing"],
        "involved_character_names": ["沈念卿", "沈鹤卿"],
        "involved_characters": ["沈念卿", "沈鹤卿"],
        "cast_plan": {
            "pov_entity_id": "char_shen_nianqing",
            "required_character_ids": ["char_shen_nianqing"],
            "support_character_ids": ["char_shen_heqing"],
            "mention_only_entity_ids": [],
            "forbidden_active_character_ids": [],
        },
        "emotional_plan": {
            "subject_entity_id": "char_shen_nianqing",
            "entry_state": "沈念卿带着信物疑问进入古董店。",
            "pressure_source": "沈鹤卿归还金镯迫使她面对旧案线索。",
            "relationship_choice": "沈念卿必须决定是否相信沈鹤卿的解释。",
            "turning_emotion": "从戒备转为带条件的试探。",
            "exit_aftertaste": "留下信件即将揭晓的紧张余味。",
            "expression_channels": ["action", "object_detail"],
        },
        "scene_design_goals": ["让金镯归还成为关系选择。", "用信件钩住下一章。"],
        "notes": "承接前批信物线。",
        "expected_hook": {
            "hook_type": "mystery",
            "hook_strength": "strong",
            "hook_description": "铁盒子里的信件即将揭晓。",
        },
        "expected_payoffs": [{"payoff_type": "clue", "description": "金镯碎片线索兑现。"}],
    }
    payload.update(overrides)
    return payload


def test_plan_outline_continue_adapter_marks_missing_title_for_polish_repair() -> None:
    chapter = _chapter_payload(title="")
    payload = {"chapters": [chapter]}

    result = apply_task_output_adapter(payload, TaskType.PLAN_OUTLINE_CONTINUE)

    assert result.changed is True
    assert result.adapter == "plan_outline_batch_shape_normalizer"
    assert payload["chapters"][0]["title"] == "标题待补"
    validate_json_output_contract(
        TaskType.PLAN_OUTLINE_CONTINUE,
        payload,
        validate_json_schema=True,
    )


def test_plan_outline_continue_adapter_merges_stray_beats_fragment() -> None:
    full_chapter = _chapter_payload()
    beats = full_chapter.pop("beats_summary")
    payload = {"chapters": [{"beats_summary": beats}, full_chapter]}

    result = apply_task_output_adapter(payload, TaskType.PLAN_OUTLINE_CONTINUE)

    assert result.changed is True
    assert len(payload["chapters"]) == 1
    assert payload["chapters"][0]["beats_summary"] == beats
    assert payload["chapters"][0]["chapter_number"] == 22
    validate_json_output_contract(
        TaskType.PLAN_OUTLINE_CONTINUE,
        payload,
        validate_json_schema=True,
    )


def test_plan_outline_batch_adapter_leaves_valid_payload_unchanged() -> None:
    chapter = _chapter_payload()
    payload = {"chapters": [deepcopy(chapter)]}

    result = apply_task_output_adapter(payload, TaskType.PLAN_OUTLINE_BATCH)

    assert result.changed is False
    assert payload == {"chapters": [chapter]}
    validate_task_semantic_contract(payload, TaskType.PLAN_OUTLINE_BATCH)


def test_outline_adapter_does_not_invent_missing_narrative_fields() -> None:
    payload = {
        "chapters": [
            {
                "chapter_number": 22,
                "title": "信物归来",
                "beats_summary": ["沈念卿走进古董店。"],
                "main_plot_points": ["金镯触发旧案线索。"],
                "pov_character": "沈念卿",
            }
        ]
    }

    apply_task_output_adapter(payload, TaskType.PLAN_OUTLINE_BATCH)

    chapter = payload["chapters"][0]
    assert chapter["pov_character_name"] == "沈念卿"
    for key in (
        "goal",
        "pov_character_id",
        "setting",
        "expected_word_count",
        "emotional_plan",
        "scene_design_goals",
        "expected_hook",
        "expected_payoffs",
    ):
        assert key not in chapter
    with pytest.raises(ValueError, match=r"missing required property `goal`"):
        validate_json_output_contract(
            TaskType.PLAN_OUTLINE_BATCH,
            payload,
            validate_json_schema=True,
        )


def test_outline_semantic_contract_rejects_empty_llm_decisions() -> None:
    chapter = _chapter_payload(
        goal="",
        scene_design_goals=["只有一项"],
        expected_payoffs=[],
    )
    chapter["emotional_plan"]["turning_emotion"] = ""
    payload = {"chapters": [chapter]}

    with pytest.raises(TaskSemanticContractError) as exc_info:
        validate_task_semantic_contract(payload, TaskType.PLAN_OUTLINE_BATCH)

    paths = {issue.path for issue in exc_info.value.issues}
    assert "$.chapters[0].goal" in paths
    assert "$.chapters[0].scene_design_goals" in paths
    assert "$.chapters[0].emotional_plan.turning_emotion" in paths
    assert "$.chapters[0].expected_payoffs" in paths


def test_plan_outline_continue_adapter_removes_extra_chapter_type_field() -> None:
    chapter = _chapter_payload(type="scene")
    payload = {"chapters": [chapter]}

    result = apply_task_output_adapter(payload, TaskType.PLAN_OUTLINE_CONTINUE)

    assert result.changed is True
    assert "type" not in payload["chapters"][0]
    validate_json_output_contract(
        TaskType.PLAN_OUTLINE_CONTINUE,
        payload,
        validate_json_schema=True,
    )


def test_outline_title_repair_numbers_detect_summary_like_titles() -> None:
    bad = _chapter_payload(
        chapter_number=46,
        title="沈念卿五人赶到古董店与陈伯庸争夺入口",
        goal="沈念卿五人赶到古董店与陈伯庸争夺入口，找到藏信地点。",
    )
    good = _chapter_payload(chapter_number=47, title="古店争钥")

    assert _outline_title_repair_numbers([bad, good], batch_start=46, batch_end=47) == [46]


def test_plan_outline_adapter_enforces_full_blueprint_boundary() -> None:
    payload = {
        "synopsis": "主角追查旧案并完成关系选择。",
        "volume_mode": False,
        "volumes": [],
        "narrative_phases": [],
        "key_turning_points": [],
        "character_arcs": [
            {
                "character": "沈知微",
                "arc_summary": "从设防到信任。",
                "milestones": [
                    {
                        "chapter_start": 1,
                        "chapter_end": 3,
                        "description": "初步信任。",
                    }
                ],
            }
        ],
        "subplot_plan": [],
        "suspense_schedule": [],
        "ending_strategy": "公开真相并完成选择。",
        "emotional_arcs": [],
        "causal_chains": [],
        "subplot_collisions": [],
        "subversion_points": [],
        "chapter_rhythm_curve": [],
        "chapter_hooks": [{"chapter_number": 1, "hook": "误入逐章字段"}],
        "element_selection": {"required_elements": []},
    }

    result = apply_task_output_adapter(
        payload,
        TaskType.PLAN_OUTLINE,
        context={"total_chapters": 12},
    )

    assert result.changed is True
    assert "chapter_hooks" not in payload
    assert "element_selection" not in payload
    validate_json_output_contract(
        TaskType.PLAN_OUTLINE,
        payload,
        validate_json_schema=True,
    )


def test_character_bible_adapter_flattens_profile_text_fields_before_schema_validation() -> None:
    payload = {
        "character_bible": {
            "characters": [
                {
                    "name": "赵启明",
                    "role": "supporting",
                    "abilities": {
                        "professional": "商业谈判与票号经营",
                        "symbolic": "民族资本觉醒",
                    },
                    "appearance": {"general": "民国商人装束", "detail": "袖口有墨痕"},
                    "personality": {"traits": ["圆滑", "谨慎"], "fear": "家业崩塌"},
                    "relationships": {"沈念卿": {"public": "旧识", "tension": "利益试探"}},
                }
            ]
        }
    }

    result = apply_task_output_adapter(payload, TaskType.INIT_CHARACTER_BIBLE)

    assert result.changed is True
    profile = payload["character_bible"]["characters"][0]
    assert profile["abilities"] == "professional: 商业谈判与票号经营；symbolic: 民族资本觉醒"
    assert profile["appearance"] == "general: 民国商人装束；detail: 袖口有墨痕"
    assert profile["personality"] == "traits: 圆滑；谨慎；fear: 家业崩塌"
    assert profile["relationships"]["沈念卿"] == "public: 旧识；tension: 利益试探"
    validate_json_output_contract(
        TaskType.INIT_CHARACTER_BIBLE,
        payload,
        validate_json_schema=True,
    )


def test_character_profile_batch_contract_rejects_unadapted_nested_text_field() -> None:
    payload = {
        "character_profiles": [
            {
                "name": "赵启明",
                "abilities": {"professional": "商业谈判"},
                "relationships": {"沈念卿": {"public": "旧识"}},
            }
        ]
    }

    try:
        validate_json_output_contract(
            TaskType.INIT_CHARACTER_PROFILE_BATCH,
            payload,
            validate_json_schema=True,
        )
    except ValueError as exc:
        assert "$.character_profiles[0].abilities: expected string, got object" in str(exc)
        assert "$.character_profiles[0].relationships.沈念卿: expected string, got object" in str(
            exc
        )
    else:
        raise AssertionError("expected nested profile text field to violate JSON Schema")


def test_character_profile_batch_adapter_coerces_null_scalar_fields() -> None:
    payload = {
        "character_profiles": [
            {
                "name": "赵启明",
                "role": "supporting",
                "age": None,
                "gender": "男",
                "status": "active",
                "time_layer": "modern",
                "social_status": "票号经理",
                "abilities": "商业谈判",
                "appearance": "长衫与怀表",
                "personality": "谨慎",
                "backstory": "因旧案卷入主线。",
                "arc": "从旁观到表态。",
                "relationships": {},
                "notes": None,
            }
        ]
    }

    result = apply_task_output_adapter(payload, TaskType.INIT_CHARACTER_PROFILE_BATCH)

    assert result.changed is True
    profile = payload["character_profiles"][0]
    assert profile["age"] == ""
    assert profile["notes"] == ""
    validate_json_output_contract(
        TaskType.INIT_CHARACTER_PROFILE_BATCH,
        payload,
        validate_json_schema=True,
    )


def test_character_profile_batch_adapter_strips_schema_leak_and_null_placeholders() -> None:
    payload = {
        "character_profiles": [
            {
                "name": "玄昱",
                "role": "protagonist",
                "age": "22",
                "gender": "男",
                "status": "active",
                "time_layer": "default",
                "social_status": "魏国储君",
                "abilities": "骑射剑术皆通。",
                "appearance": "眉骨高峻。",
                "personality": "克制多疑，恐惧失控。",
                "backstory": "童年创痛卷入宫廷主线。",
                "arc": "从压抑到自我接纳。",
                "relationships": {},
                "voice": "言简意赅。",
                "notes": "铁锈屑为关键锚点。",
            },
            None,
        ],
        "minItems": 1,
        "additionalProperties": False,
        "type": "object",
        "required": ["character_profiles"],
    }

    result = apply_task_output_adapter(payload, TaskType.INIT_CHARACTER_PROFILE_BATCH)

    assert result.changed is True
    assert payload["character_profiles"][0]["name"] == "玄昱"
    assert len(payload["character_profiles"]) == 1
    for key in ("minItems", "additionalProperties", "type", "required"):
        assert key not in payload
    validate_json_output_contract(
        TaskType.INIT_CHARACTER_PROFILE_BATCH,
        payload,
        validate_json_schema=True,
    )


def test_editorial_structure_adapter_does_not_invent_missing_title_policy() -> None:
    payload = {
        "climax_markers": [],
        "denouement_budget": {
            "expected_chapters": 1,
            "max_confirmation_scenes": 1,
            "required_new_functions": ["余波后果"],
            "forbidden_repeats": [],
        },
        "revelation_ladder": [],
        "time_bridge_policies": [
            {"policy": "跨月转场必须在章节开头标注时间。"},
            {"rule": "民国闪回使用器物触发，不按线性日历硬推。"},
        ],
        "title_policy": {},
    }

    result = apply_task_output_adapter(payload, TaskType.DERIVE_EDITORIAL_STRUCTURE)

    assert result.changed is True
    assert payload["time_bridge_policies"] == [
        "跨月转场必须在章节开头标注时间。",
        "民国闪回使用器物触发，不按线性日历硬推。",
    ]
    assert payload["title_policy"] == {}
    with pytest.raises(ValueError, match=r"title_policy.*missing required property"):
        validate_json_output_contract(
            TaskType.DERIVE_EDITORIAL_STRUCTURE,
            payload,
            validate_json_schema=True,
        )


def test_editorial_structure_adapter_normalizes_title_policy_before_schema_validation() -> None:
    payload = {
        "climax_markers": [],
        "denouement_budget": {
            "expected_chapters": 1,
            "max_confirmation_scenes": 1,
            "required_new_functions": ["余波后果"],
            "forbidden_repeats": [],
        },
        "revelation_ladder": [],
        "time_bridge_policies": [],
        "title_policy": {
            "max_reuse": 0,
            "allowed_titles": [{"title": "残玉归心"}, "月下残局"],
            "naming_strategy": "每章标题唯一，只有首尾回环可复用。",
        },
    }

    result = apply_task_output_adapter(payload, TaskType.DERIVE_EDITORIAL_STRUCTURE)

    assert result.changed is True
    assert payload["title_policy"] == {
        "max_reuse": 1,
        "allowed_repeated_titles": ["残玉归心", "月下残局"],
        "naming_strategy": "每章标题唯一，只有首尾回环可复用。",
    }
    validate_json_output_contract(
        TaskType.DERIVE_EDITORIAL_STRUCTURE,
        payload,
        validate_json_schema=True,
    )


def test_editorial_structure_adapter_strips_revelation_ladder_explanatory_drift() -> None:
    payload = {
        "climax_markers": [],
        "denouement_budget": {
            "expected_chapters": 1,
            "max_confirmation_scenes": 1,
            "required_new_functions": ["余波后果"],
            "forbidden_repeats": [],
        },
        "revelation_ladder": [
            {
                "thread": {"name": "旧案真相"},
                "stage": "第一幕",
                "order": "1",
                "chapter": "3",
                "trigger": {"text": "沈清浅发现账册暗记。"},
                "allowed_disclosure": {"description": "只显露账册来自吴宫。"},
                "required_action_consequence": "玄菘改变府兵部署。",
                "rationale": "第一幕只允许局部揭示。",
                "evidence_based": "蓝图写明旧案要分阶段释放。",
            }
        ],
        "time_bridge_policies": [],
        "title_policy": {
            "max_reuse": 2,
            "allowed_repeated_titles": [],
            "naming_strategy": "节点功能优先，仅允许有意回环。",
        },
    }

    result = apply_task_output_adapter(payload, TaskType.DERIVE_EDITORIAL_STRUCTURE)

    assert result.changed is True
    ladder_step = payload["revelation_ladder"][0]
    assert ladder_step == {
        "thread": "旧案真相",
        "stage": "第一幕",
        "stage_order": 1,
        "target_chapter": 3,
        "trigger": "沈清浅发现账册暗记。",
        "allowed_disclosure": "只显露账册来自吴宫。",
        "required_action_consequence": "玄菘改变府兵部署。",
    }
    assert "rationale" not in ladder_step
    assert "evidence_based" not in ladder_step
    validate_json_output_contract(
        TaskType.DERIVE_EDITORIAL_STRUCTURE,
        payload,
        validate_json_schema=True,
    )


def test_editorial_structure_adapter_preserves_unrecoverable_ladder_shape_for_retry() -> None:
    payload = {
        "climax_markers": [],
        "denouement_budget": {},
        "revelation_ladder": "第一幕只允许局部揭示。",
        "time_bridge_policies": [],
        "title_policy": {
            "max_reuse": 2,
            "allowed_repeated_titles": [],
            "naming_strategy": "节点功能优先，仅允许有意回环。",
        },
    }

    result = apply_task_output_adapter(payload, TaskType.DERIVE_EDITORIAL_STRUCTURE)

    assert result.changed is False
    assert payload["revelation_ladder"] == "第一幕只允许局部揭示。"
    with pytest.raises(ValueError, match=r"revelation_ladder.*expected array"):
        validate_json_output_contract(
            TaskType.DERIVE_EDITORIAL_STRUCTURE,
            payload,
            validate_json_schema=True,
        )


def test_editorial_structure_adapter_migrates_denouement_description_alias() -> None:
    payload = {
        "climax_markers": [],
        "denouement_budget": {
            "expected_chapters": 1,
            "max_confirmation_scenes": 1,
            "required_new_functions": ["余波后果"],
            "forbidden_repeats": ["重复圆满确认"],
            "forbidden_repeats_description": "结尾一章只能完成一项收束，不得新开反派动作。",
        },
        "revelation_ladder": [],
        "time_bridge_policies": [],
        "title_policy": {
            "max_reuse": 1,
            "allowed_repeated_titles": [],
            "naming_strategy": "标题唯一。",
        },
    }

    result = apply_task_output_adapter(payload, TaskType.DERIVE_EDITORIAL_STRUCTURE)

    assert result.changed is True
    assert payload["denouement_budget"] == {
        "expected_chapters": 1,
        "max_confirmation_scenes": 1,
        "required_new_functions": ["余波后果"],
        "forbidden_repeats": [
            "重复圆满确认",
            "结尾一章只能完成一项收束，不得新开反派动作。",
        ],
    }
    validate_json_output_contract(
        TaskType.DERIVE_EDITORIAL_STRUCTURE,
        payload,
        validate_json_schema=True,
    )


def test_editorial_structure_adapter_preserves_unknown_denouement_key_for_retry() -> None:
    payload = {
        "climax_markers": [],
        "denouement_budget": {
            "expected_chapters": 1,
            "max_confirmation_scenes": 1,
            "required_new_functions": ["余波后果"],
            "forbidden_repeats": [],
            "new_semantic_rule": "未定义的语义不应被静默删除。",
        },
        "revelation_ladder": [],
        "time_bridge_policies": [],
        "title_policy": {
            "max_reuse": 1,
            "allowed_repeated_titles": [],
            "naming_strategy": "标题唯一。",
        },
    }

    result = apply_task_output_adapter(payload, TaskType.DERIVE_EDITORIAL_STRUCTURE)

    assert result.changed is False
    assert payload["denouement_budget"]["new_semantic_rule"] == ("未定义的语义不应被静默删除。")
    with pytest.raises(ValueError, match=r"unexpected property `new_semantic_rule`"):
        validate_json_output_contract(
            TaskType.DERIVE_EDITORIAL_STRUCTURE,
            payload,
            validate_json_schema=True,
        )


def test_editorial_style_adapter_preserves_unknown_policy_for_llm_retry() -> None:
    payload = {
        "theme_policies": [],
        "symbol_policies": [
            {
                "symbol": "残缺意象",
                "narrative_function": "身份错位与自我接纳。",
                "explanation_policy": "symbolic_guidance",
                "escalation_rule": "终局只允许行动承接。",
                "max_explicit_explanations": 99,
            }
        ],
        "scene_resistance_rules": [],
        "expression_channel_budget": {},
        "expression_channel_profiles": [],
        "body_signal_budget_per_high_emotion_scene": 1,
        "forbidden_confirmation_phrases": [],
        "revision_priorities": [],
    }

    result = apply_task_output_adapter(payload, TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS)

    assert result.changed is True
    assert payload["symbol_policies"][0]["explanation_policy"] == "symbolic_guidance"
    assert payload["symbol_policies"][0]["max_explicit_explanations"] == 10
    with pytest.raises(ValueError, match=r"explanation_policy.*expected one of"):
        validate_json_output_contract(
            TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS,
            payload,
            validate_json_schema=True,
        )
