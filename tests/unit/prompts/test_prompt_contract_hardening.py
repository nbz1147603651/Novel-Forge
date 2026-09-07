"""Tests for prompt-contract baseline and effective schema hardening."""

from __future__ import annotations

from novel_forge.common.constants import TaskType
from novel_forge.core.format_contracts import (
    OutputKind,
    effective_contract_json_schema,
    get_task_format_contract,
    resolve_task_format_contract,
)
from novel_forge.core.schemas.short_blueprint import ShortBlueprint, ShortNarrativePhase
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.prompts.registry import PromptRegistry
from scripts.baseline_prompts import BASELINE, compute_current, drift_lines
from scripts.prompt_snapshot_cases import snapshot_case_by_id


def test_prompt_baseline_matches_current_catalog() -> None:
    current = compute_current()

    assert current == BASELINE
    assert drift_lines(current) == []


def test_relationship_prompt_renders_unicode_and_prioritizes_canonical_roster() -> None:
    rendered = PromptRegistry().render(
        TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX,
        premise="顾琛调查旧案。",
        story_bible={"premise": "顾琛调查旧案。"},
        character_generation_mode="split_v2",
        relationship_generation_phase="repair",
        character_roster=[{"name": "顾琛"}, {"name": "林晚"}],
        target_character_roster=[{"name": "顾琛"}],
        character_profiles=[],
        character_arcs=[],
        relationship_seed_matrix=[],
        relationship_candidate_evidence=[],
        character_profile_normalization={},
        previous_relationship_matrix=[],
        relationship_retry_reasons=[],
        shared_evidence_anchor={"canonical_character": "顾琛"},
    )

    assert "顾琛" in rendered
    assert "\\u987e\\u741b" not in rendered
    assert rendered.index("固定角色清单（最高优先级") < rendered.index("共享证据锚点")


def test_generate_config_build_uses_effective_response_schema_model() -> None:
    context = snapshot_case_by_id("generate_config_long").context
    contract = resolve_task_format_contract(TaskType.GENERATE_CONFIG, context)

    assert contract is not None
    assert contract.output_kind == OutputKind.JSON
    assert contract.response_schema_model is not None
    schema = effective_contract_json_schema(TaskType.GENERATE_CONFIG, contract, context)
    assert schema is not None
    assert tuple(schema["properties"]) == contract.allowed_top_level_keys
    assert tuple(schema["required"]) == contract.required_top_level_keys
    assert schema["additionalProperties"] is False

    request = PromptBuilder().build(TaskType.GENERATE_CONFIG, context)
    assert request.response_json_schema == schema


def test_polish_config_partial_schema_preserves_allowed_key_filter() -> None:
    context = snapshot_case_by_id("polish_config_partial").context
    contract = resolve_task_format_contract(TaskType.POLISH_CONFIG, context)

    assert contract is not None
    assert contract.response_schema_model is not None
    schema = effective_contract_json_schema(TaskType.POLISH_CONFIG, contract, context)
    assert schema is not None
    assert tuple(schema["properties"]) == contract.allowed_top_level_keys
    assert schema["required"] == []
    assert "length_target" not in schema["properties"]
    assert schema["additionalProperties"] is False


def test_beats_contract_declares_nested_tension_level_type() -> None:
    contract = resolve_task_format_contract(TaskType.BEATS)

    assert contract is not None
    assert contract.json_schema is not None
    beat_ref = contract.json_schema["properties"]["beats"]["items"]["$ref"]
    beat_schema = contract.json_schema["$defs"][beat_ref.rsplit("/", 1)[-1]]
    tension_schema = beat_schema["properties"]["tension_level"]

    assert tension_schema["type"] == "integer"
    assert tension_schema["minimum"] == 1
    assert tension_schema["maximum"] == 10


def test_beats_prompt_distinguishes_phase_labels_from_numeric_scores() -> None:
    prompt = PromptRegistry().render(
        TaskType.BEATS,
        spec=StorySpec(theme="测试", genre="悬疑", length_target=3000, language="zh"),
        blueprint=ShortBlueprint(
            synopsis="测试梗概",
            narrative_phases=[
                ShortNarrativePhase(phase_name="引入", tension_level="低"),
            ],
        ),
    )

    assert "必须是 1-10 范围内的整数" in prompt
    assert "不能原样填入 beat" in prompt
    assert "张力=低" in prompt


def test_generate_and_polish_config_prompts_block_motif_intent_lists() -> None:
    builder = PromptBuilder()
    generate_prompt = builder.build(
        TaskType.GENERATE_CONFIG,
        snapshot_case_by_id("generate_config_long").context,
    ).messages[1]["content"]
    polish_prompt = builder.build(
        TaskType.POLISH_CONFIG,
        snapshot_case_by_id("polish_config_partial").context,
    ).messages[1]["content"]

    for prompt in (generate_prompt, polish_prompt):
        assert "意象/母题输入边界（生成与润色均必须遵守）" in prompt
        assert "不得主动新增“核心意象”“象征物”“母题”" in prompt
        assert (
            "`extra_instructions`、`polish_hint`、`opening_style`、`ending_style` 不能承载全书母题任务"
            in prompt
        )
        assert "不得改写成“全书反复呼应/持续植入/每章强化”的母题指令" in prompt
        assert (
            "`extra_instructions` 只写用户明确要求的硬约束或边界，不得写成意象/符号/母题清单"
            in prompt
        )


def test_plan_outline_dynamic_context_changes_required_schema_keys() -> None:
    full_context = snapshot_case_by_id("plan_outline_full").context
    fragment_context = snapshot_case_by_id("plan_outline_fragment").context

    full_contract = resolve_task_format_contract(TaskType.PLAN_OUTLINE, full_context)
    fragment_contract = resolve_task_format_contract(TaskType.PLAN_OUTLINE, fragment_context)

    assert full_contract is not None
    assert fragment_contract is not None
    assert "synopsis" in full_contract.required_top_level_keys
    assert fragment_contract.required_top_level_keys == ("subplot_plan",)
    assert full_contract.json_schema is not None
    assert fragment_contract.json_schema is not None
    assert set(fragment_contract.json_schema["properties"]) == {"subplot_plan"}


def test_strict_static_schemas_are_not_forced_to_response_schema_model() -> None:
    humanize = get_task_format_contract(TaskType.HUMANIZE_SCAN)
    book_consistency = get_task_format_contract(TaskType.BOOK_CONSISTENCY)

    assert humanize is not None
    assert humanize.response_schema_model is None
    assert book_consistency is not None
    assert book_consistency.response_schema_model is None
