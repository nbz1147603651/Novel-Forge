"""Regression tests for prompt/local format-contract synchronization."""

from __future__ import annotations

import json

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.format_contracts import (
    ContractMode,
    collect_json_output_contract_issues,
    render_prompt_contract_block,
    resolve_task_format_contract,
    should_validate_contract_schema,
    validate_json_output_contract,
)
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.pipeline.long.services.generation import llm_helpers as llm_h


def _blueprint_fragment_context(block_key: str, required_keys: list[str]) -> dict[str, object]:
    return {
        "blueprint_fragment_request": {
            "block_key": block_key,
            "required_keys": required_keys,
        }
    }


def test_plan_outline_ending_fragment_contract_requires_string_strategy() -> None:
    context = _blueprint_fragment_context("ending", ["ending_strategy"])

    validate_json_output_contract(
        TaskType.PLAN_OUTLINE,
        {"ending_strategy": "终局回收主线、情感线与悬念。"},
        context=context,
        explicit_required_keys=("ending_strategy",),
        include_contract_required_keys=False,
        validate_json_schema=True,
    )

    with pytest.raises(ValueError, match="ending_strategy.*expected string"):
        validate_json_output_contract(
            TaskType.PLAN_OUTLINE,
            {"ending_strategy": {"summary": "终局回收主线。"}},
            context=context,
            explicit_required_keys=("ending_strategy",),
            include_contract_required_keys=False,
            validate_json_schema=True,
        )


def test_plan_outline_phase_fragment_rejects_legacy_nested_field_names() -> None:
    context = _blueprint_fragment_context("phases", ["narrative_phases"])

    with pytest.raises(ValueError, match="unexpected property `main_locations`"):
        validate_json_output_contract(
            TaskType.PLAN_OUTLINE,
            {
                "narrative_phases": [
                    {
                        "phase_name": "入局",
                        "chapter_start": 1,
                        "chapter_end": 10,
                        "phase_description": "旧字段不应进入新输出。",
                        "main_locations": ["上海国际会议中心"],
                    }
                ]
            },
            context=context,
            explicit_required_keys=("narrative_phases",),
            include_contract_required_keys=False,
            validate_json_schema=True,
        )


def test_blueprint_fragment_prompt_schema_uses_canonical_field_shape() -> None:
    context = _blueprint_fragment_context("ending", ["ending_strategy"])
    contract = resolve_task_format_contract(TaskType.PLAN_OUTLINE, context)
    assert contract is not None
    assert contract.json_schema is not None
    assert contract.json_schema["properties"]["ending_strategy"]["type"] == "string"

    block = render_prompt_contract_block(TaskType.PLAN_OUTLINE, context)
    assert '"ending_strategy"' in block
    assert '"type": "string"' in block
    assert "character_fates" not in block


def test_response_schema_backfills_typed_prompt_contract_for_registered_task() -> None:
    contract = resolve_task_format_contract(TaskType.CHECK_ALIGNMENT)
    assert contract is not None
    assert contract.json_schema is not None
    assert contract.json_schema["properties"]["alignment_score"]["type"] == "number"
    assert contract.json_schema["properties"]["summary"]["type"] == "string"


@pytest.mark.parametrize(
    "task_type",
    [TaskType.PLAN_CHAPTER, TaskType.PLAN_CHAPTER_SCENES],
)
def test_plan_contract_keeps_source_guidance_out_of_model_output_schema(
    task_type: TaskType,
) -> None:
    contract = resolve_task_format_contract(task_type)
    assert contract is not None
    assert contract.json_schema is not None
    schema = contract.json_schema
    payload_schema = schema
    if task_type == TaskType.PLAN_CHAPTER_SCENES:
        payload_ref = schema["properties"]["scene_plan"]["$ref"].rsplit("/", 1)[-1]
        payload_schema = schema["$defs"][payload_ref]

    assert "guidance_requirements" not in payload_schema["properties"]
    cross_ref = payload_schema["properties"]["cross_scene_intent"]["$ref"].rsplit("/", 1)[-1]
    cross_schema = schema["$defs"][cross_ref]
    item_ref = cross_schema["properties"]["cross_scene_references"]["items"]["$ref"].rsplit("/", 1)[
        -1
    ]
    requirement_ref = schema["$defs"][item_ref]["properties"]["requirement"]["$ref"].rsplit("/", 1)[
        -1
    ]
    requirement_schema = schema["$defs"][requirement_ref]

    assert set(requirement_schema["properties"]) == {
        "source",
        "scope",
        "satisfaction",
        "status",
        "evidence",
        "source_text_hash",
    }
    assert "requirement_id" not in requirement_schema["properties"]
    assert "text" not in requirement_schema["properties"]


async def test_plan_json_retry_accepts_known_guidance_inheritance_drift_without_retry() -> None:
    payload = {
        "scene_intents": [],
        "world_rule_applications": [],
        "opening_contract": "承接前章。",
        "closing_contract": "保留后续入口。",
        "required_state_transitions": [],
        "required_literals": [],
        "chapter_type": "transition",
        "emotional_arc": "从试探到警觉。",
        "relationship_evolution": [],
        "forbidden_elements": [],
        "forbidden_elements_soft": [],
        "forbidden_elements_quota": [],
        "intentional_callbacks": [],
        "foreshadowing_plan": [],
        "key_revelations": [],
        "guidance_requirements": [
            {
                "requirement_id": "chapter_contract.required_progressions:0",
                "text": "以动作完成承接。",
            }
        ],
        "cross_scene_intent": {
            "cross_scene_references": [
                {
                    "from_scene": "scene_01",
                    "to_scene": "scene_02",
                    "ref_type": "callback",
                    "description": "用门禁声承接上一场的密码。",
                    "requirement": {
                        "source": "chapter_contract.required_progressions:0",
                        "scope": "chapter:1",
                        "satisfaction": "narrative",
                        "status": "pending",
                        "evidence": "",
                        "source_text_hash": "",
                        "requirement_id": "chapter_contract.required_progressions:0",
                        "text": "以动作完成承接。",
                    },
                }
            ],
            "pacing_curve": [],
        },
    }

    class _Router:
        def __init__(self) -> None:
            self.calls = 0

        async def route(self, _request: ModelRequest) -> ModelResponse:
            self.calls += 1
            return ModelResponse(
                content=json.dumps(payload, ensure_ascii=False),
                model_id="mock-model",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                latency_ms=1.0,
                cost_usd=0.0,
            )

    router = _Router()
    request = ModelRequest(
        task_type=TaskType.PLAN_CHAPTER,
        messages=[{"role": "user", "content": "plan"}],
        max_tokens=256,
        temperature=0.3,
    )

    parsed = await llm_h.route_json_object_with_retry(
        router,
        request,
        task_type=TaskType.PLAN_CHAPTER,
    )

    assert router.calls == 1
    assert "guidance_requirements" not in parsed
    requirement = parsed["cross_scene_intent"]["cross_scene_references"][0]["requirement"]
    assert requirement == {
        "source": "chapter_contract.required_progressions:0",
        "scope": "chapter:1",
        "satisfaction": "narrative",
        "status": "pending",
        "evidence": "",
        "source_text_hash": "",
    }


def test_creative_direction_prompt_contract_publishes_nested_packet_shape() -> None:
    contract = resolve_task_format_contract(TaskType.INIT_CREATIVE_DIRECTION_CANDIDATES)

    assert contract is not None
    assert contract.json_schema is not None
    candidate_items = contract.json_schema["properties"]["candidates"]["items"]
    candidate_ref = candidate_items["$ref"].rsplit("/", 1)[-1]
    candidate_schema = contract.json_schema["$defs"][candidate_ref]
    packet_ref = candidate_schema["properties"]["packet"]["$ref"].rsplit("/", 1)[-1]
    packet_schema = contract.json_schema["$defs"][packet_ref]

    assert candidate_schema["additionalProperties"] is False
    assert set(candidate_schema["required"]) == {"candidate_id", "packet"}
    assert tuple(packet_schema["properties"]) == (
        "emotional_engine",
        "thematic_promises",
        "signature_motifs",
        "relationship_tensions",
        "anti_cliche_rules",
        "scene_potential",
        "notes",
    )
    assert '"packet"' in render_prompt_contract_block(TaskType.INIT_CREATIVE_DIRECTION_CANDIDATES)


@pytest.mark.parametrize(
    "task_type",
    (
        TaskType.INIT_STORY_BIBLE,
        TaskType.INIT_ENTITY_REGISTRY,
        TaskType.INIT_NARRATIVE_CONTRACT,
        TaskType.BRIDGE_CHAPTER,
        TaskType.CHECK_CHAPTER,
        TaskType.CHECK_ALIGNMENT,
        TaskType.CHECK_CONTINUITY,
        TaskType.VALIDATE_CAUSAL,
        TaskType.EVALUATE_READING_POWER,
        TaskType.EXTRACT_CANON,
        TaskType.EXTRACT_CHAPTER_SUMMARY_EXIT,
        TaskType.EXTRACT_CANON_DELTA,
        TaskType.EXTRACT_CREATIVE_REPORT,
        TaskType.EXTRACT_CHARACTER_STATE_DELTAS,
        TaskType.EXTRACT_RELATIONSHIP_DELTAS,
        TaskType.EXTRACT_PLOT_THREAD_DELTAS,
        TaskType.EXTRACT_EXPRESSION_OBSERVATIONS,
    ),
)
def test_narrative_handoff_contracts_close_the_response_envelope(task_type: TaskType) -> None:
    """Prompt, provider schema, and local parser share one closed handoff shape."""
    contract = resolve_task_format_contract(task_type)

    assert contract is not None
    assert contract.json_schema is not None
    assert contract.allowed_top_level_keys
    assert tuple(contract.json_schema["properties"]) == contract.allowed_top_level_keys
    assert contract.json_schema["additionalProperties"] is False
    assert "顶层只允许字段" in render_prompt_contract_block(task_type)

    issues = collect_json_output_contract_issues(
        task_type,
        {"undeclared_debug_field": True},
        validate_allowed_keys=True,
        validate_json_schema=True,
    )
    assert any(
        issue.path == "$.undeclared_debug_field" and issue.issue_type == "extra_key"
        for issue in issues
    )


def test_bridge_contract_keeps_model_authored_fields_but_blocks_local_provenance() -> None:
    contract = resolve_task_format_contract(TaskType.BRIDGE_CHAPTER)

    assert contract is not None
    assert {"bridge_summary", "sensory_anchors", "relationship_beat"}.issubset(
        contract.allowed_top_level_keys
    )
    assert "from_chapter" not in contract.allowed_top_level_keys
    assert "to_chapter" not in contract.allowed_top_level_keys


def test_bridge_contract_closes_nested_causal_and_relationship_handoffs() -> None:
    contract = resolve_task_format_contract(TaskType.BRIDGE_CHAPTER)

    assert contract is not None
    assert contract.json_schema is not None
    definitions = contract.json_schema["$defs"]
    causal_schema = definitions["_BridgeCausalLinkResponse"]
    relationship_schema = definitions["_BridgeRelationshipBeatResponse"]

    assert causal_schema["additionalProperties"] is False
    assert tuple(causal_schema["properties"]) == (
        "previous_event",
        "causal_mechanism",
        "unresolved_question",
        "open_threads",
    )
    assert relationship_schema["additionalProperties"] is False
    assert tuple(relationship_schema["properties"]) == (
        "current_trust_level",
        "unspoken_tension",
        "power_dynamic",
    )


def _canonical_outline_batch_payload() -> dict[str, object]:
    return {
        "chapters": [
            {
                "chapter_number": 19,
                "title": "暗流涌动",
                "goal": "商业间谍潜伏期。",
                "beats_summary": ["服务器日志异常。"],
                "main_plot_points": ["算法原型出现异常访问记录。"],
                "subplot_points": ["林素素秘密收集罪证。"],
                "subplot_focus": "证据线",
                "element_focus": ["business_market_feedback"],
                "pov_character_id": "char_lu_yunzheng",
                "pov_character_name": "陆云峥",
                "pov_character": "陆云峥",
                "pov_switch": False,
                "setting": "智云科技办公室深夜",
                "expected_word_count": 4500,
                "involved_character_ids": ["char_lu_yunzheng", "char_cheng_yanqiu"],
                "required_character_ids": ["char_lu_yunzheng"],
                "support_character_ids": ["char_cheng_yanqiu"],
                "involved_character_names": ["陆云峥", "程砚秋"],
                "involved_characters": ["陆云峥", "程砚秋"],
                "cast_plan": {
                    "pov_entity_id": "char_lu_yunzheng",
                    "required_character_ids": ["char_lu_yunzheng"],
                    "support_character_ids": ["char_cheng_yanqiu"],
                    "mention_only_entity_ids": [],
                    "forbidden_active_character_ids": [],
                },
                "emotional_plan": {
                    "subject_entity_id": "char_lu_yunzheng",
                    "entry_state": "警觉",
                    "pressure_source": "内鬼访问记录",
                    "relationship_choice": "暂不公开怀疑",
                    "turning_emotion": "确认风险",
                    "exit_aftertaste": "危险逼近",
                    "expression_channels": ["动作", "短句"],
                },
                "scene_design_goals": [
                    "用服务器访问记录推进商业间谍线。",
                    "让陆云峥在公开质疑与暗中取证之间作出选择。",
                ],
                "notes": "承接第18章身份危机。",
                "expected_hook": {
                    "hook_type": "mystery",
                    "hook_strength": "strong",
                    "hook_description": "内鬼访问记录时间点过于精准。",
                },
                "expected_payoffs": [
                    {
                        "payoff_type": "clue",
                        "description": "锁定商业间谍线索。",
                    }
                ],
            }
        ]
    }


def test_plan_outline_batch_contract_uses_canonical_chapter_shape() -> None:
    contract = resolve_task_format_contract(TaskType.PLAN_OUTLINE_CONTINUE)
    assert contract is not None
    assert contract.contract_id == "planning:plan_outline_batch"
    assert contract.json_schema is not None
    assert contract.allowed_top_level_keys == ("chapters",)

    chapter_schema = contract.json_schema["properties"]["chapters"]["items"]
    assert chapter_schema["additionalProperties"] is False
    assert "setting" in chapter_schema["required"]
    assert "expected_word_count" in chapter_schema["required"]
    assert "main_scenes" not in chapter_schema["properties"]
    assert "target_word_count" not in chapter_schema["properties"]

    block = render_prompt_contract_block(TaskType.PLAN_OUTLINE_CONTINUE)
    assert '"setting"' in block
    assert '"expected_word_count"' in block


def test_plan_outline_batch_contract_accepts_canonical_chapter_payload() -> None:
    validate_json_output_contract(
        TaskType.PLAN_OUTLINE_BATCH,
        _canonical_outline_batch_payload(),
        validate_json_schema=True,
    )


def test_plan_outline_batch_contract_rejects_legacy_alias_fields() -> None:
    payload = _canonical_outline_batch_payload()
    chapter = payload["chapters"][0]  # type: ignore[index]
    assert isinstance(chapter, dict)
    chapter["main_scenes"] = ["智云科技办公室深夜"]
    chapter["target_word_count"] = 4500

    with pytest.raises(ValueError, match="unexpected property `main_scenes`"):
        validate_json_output_contract(
            TaskType.PLAN_OUTLINE_CONTINUE,
            payload,
            validate_json_schema=True,
        )


def test_schema_validation_decision_keeps_strict_fragment_contracts_enabled() -> None:
    outline_batch = resolve_task_format_contract(TaskType.PLAN_OUTLINE_CONTINUE)
    assert should_validate_contract_schema(
        outline_batch,
        include_contract_required_keys=False,
    )

    partial = resolve_task_format_contract(
        TaskType.POLISH_CONFIG,
        {"mode": "long", "allow_partial_config_output": True},
    )
    assert partial is not None
    assert partial.effective_contract_mode == ContractMode.PARTIAL_OBJECT
    assert not should_validate_contract_schema(
        partial,
        include_contract_required_keys=False,
    )


def test_polish_outline_title_repair_contract_only_allows_title_patch() -> None:
    context = {"polish_mode": "title_repair", "focus_fields": ["title"]}
    contract = resolve_task_format_contract(TaskType.POLISH_OUTLINE, context)

    assert contract is not None
    assert contract.schema_model == "polish_outline_title_repair"

    validate_json_output_contract(
        TaskType.POLISH_OUTLINE,
        {
            "adjusted_chapters": [{"chapter_number": 46, "title": "古店争钥"}],
            "polish_suggestions": [],
        },
        context=context,
        validate_json_schema=True,
    )

    with pytest.raises(ValueError, match="unexpected property `goal`"):
        validate_json_output_contract(
            TaskType.POLISH_OUTLINE,
            {
                "adjusted_chapters": [
                    {"chapter_number": 46, "title": "古店争钥", "goal": "不应改剧情"}
                ],
                "polish_suggestions": [],
            },
            context=context,
            validate_json_schema=True,
        )


def test_polish_outline_title_repair_contract_rejects_summary_title() -> None:
    context = {"polish_mode": "title_repair", "focus_fields": ["title"]}

    with pytest.raises(ValueError, match="expected length <="):
        validate_json_output_contract(
            TaskType.POLISH_OUTLINE,
            {
                "adjusted_chapters": [
                    {"chapter_number": 46, "title": "沈念卿五人赶到古董店与陈伯庸争夺入口"}
                ],
                "polish_suggestions": [],
            },
            context=context,
            validate_json_schema=True,
        )


def test_reading_power_eval_contract_owns_extended_output_shape() -> None:
    contract = resolve_task_format_contract(TaskType.EVALUATE_READING_POWER)

    assert contract is not None
    assert contract.schema_model == "reading_power_eval"
    assert contract.json_schema is not None
    assert contract.contract_id == "checking:reading_power_eval"
    assert "information_pacing" in contract.json_schema["properties"]
    assert "character_drive" in contract.required_top_level_keys

    block = render_prompt_contract_block(TaskType.EVALUATE_READING_POWER)
    assert "JSON Schema" in block
    assert '"information_pacing"' in block
    assert '"character_drive"' in block

    payload = {
        "hook_type": "mystery",
        "hook_strength": "strong",
        "hook_description": "门外响起同一段暗号。",
        "prev_hook_fulfilled": True,
        "micro_payoffs": [
            {
                "type": "clue",
                "description": "主角确认暗号来自旧案。",
                "strength": "medium",
            }
        ],
        "is_transition": False,
        "next_chapter_reason": "暗号背后的人即将现身。",
        "information_pacing": "balanced",
        "main_plot_depth": "moderate",
        "tension_match": "matched",
        "character_drive": "moderate",
    }
    validate_json_output_contract(
        TaskType.EVALUATE_READING_POWER,
        payload,
        validate_json_schema=True,
    )

    payload["hook_type"] = "suspense"
    with pytest.raises(ValueError, match="hook_type.*expected one of"):
        validate_json_output_contract(
            TaskType.EVALUATE_READING_POWER,
            payload,
            validate_json_schema=True,
        )


def test_generate_config_short_contract_rejects_long_only_schema_fields() -> None:
    payload = {
        "characters_hint": "女主是县城返乡创业者。",
        "conflict_hint": "家族债务与新事业冲突。",
        "ending_style": "开放但完成情感回收。",
        "extra_instructions": "保持现实质感。",
        "genre": "现实",
        "language": "zh",
        "length_target": 8000,
        "max_edit_rounds": 1,
        "opening_style": "以危机开场。",
        "pov_hint": "第三人称限知。",
        "project_id": "demo",
        "theme": "返乡之后重建自我。",
        "title": "春水回潮",
        "tone": "温暖克制",
        "world_hint": "江南小城。",
        "writing_mode": "short",
        "polish_suggestions": ["强化债务压力"],
        "creative_note": {
            "core_pitch": "返乡创业与自我修复并行。",
            "design_intent": "用生活困境承托人物选择。",
            "preserved_constraints": ["短篇"],
            "field_rationales": {"theme": "保持核心卖点"},
            "risks": ["冲突过散"],
            "next_moves": ["压缩支线"],
            "anti_drift_check": "所有事件服务返乡主题。",
        },
        "premise": "长篇字段不应出现在短篇契约中。",
    }

    with pytest.raises(KeyError, match="Unexpected response key\\(s\\): premise"):
        validate_json_output_contract(
            TaskType.GENERATE_CONFIG,
            payload,
            context={"mode": "short"},
            validate_json_schema=True,
        )


async def test_json_retry_normalizes_legacy_outline_aliases_before_contract_validation() -> None:
    payload = _canonical_outline_batch_payload()
    chapter = payload["chapters"][0]  # type: ignore[index]
    assert isinstance(chapter, dict)
    chapter["main_scenes"] = ["智云科技办公室深夜"]
    chapter["target_word_count"] = 4500

    class _AliasRouter:
        def __init__(self) -> None:
            self.calls = 0

        async def route(self, request: ModelRequest) -> ModelResponse:
            self.calls += 1
            return ModelResponse(
                content=json.dumps(payload, ensure_ascii=False),
                model_id="mock-model",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                latency_ms=1.0,
                cost_usd=0.0,
            )

    router = _AliasRouter()
    request = ModelRequest(
        task_type=TaskType.PLAN_OUTLINE_CONTINUE,
        messages=[{"role": "user", "content": "generate"}],
        max_tokens=256,
        temperature=0.3,
    )

    parsed = await llm_h.route_json_object_with_retry(
        router,
        request,
        task_type=TaskType.PLAN_OUTLINE_CONTINUE,
        include_contract_required_keys=False,
    )

    parsed_chapter = parsed["chapters"][0]
    assert "main_scenes" not in parsed_chapter
    assert "target_word_count" not in parsed_chapter
    assert parsed_chapter["setting"] == "智云科技办公室深夜"
    assert parsed_chapter["expected_word_count"] == 4500
    assert router.calls == 1
