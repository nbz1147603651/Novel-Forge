"""Tests for task format contracts shared by prompts and parser pipelines."""

from __future__ import annotations

import json
import re

from novel_forge.common.constants import TaskType
from novel_forge.core.format_contracts import (
    _TASK_FORMAT_CONTRACTS,
    ContractMode,
    OutputKind,
    TextOutputContractError,
    collect_json_output_contract_issues,
    get_task_format_contract,
    merge_required_keys_for_task,
    render_prompt_contract_block,
    resolve_task_format_contract,
    validate_json_output_contract,
    validate_text_output_contract,
)
from novel_forge.core.parsing.response_schemas import get_response_schema
from novel_forge.gateway.adapters.mock import (
    _MOCK_BLUEPRINT_ELEMENT_SELECT,
    _MOCK_CHAPTER_PLAN,
    _MOCK_CHECK_CHAPTER,
)
from novel_forge.prompts.builder import PromptBuilder


def test_merge_required_keys_uses_strict_contract_defaults() -> None:
    keys = merge_required_keys_for_task(TaskType.INIT_STORY_BIBLE)
    assert keys == ("story_bible",)


def test_merge_required_keys_deduplicates_explicit_and_contract() -> None:
    keys = merge_required_keys_for_task(
        TaskType.PLAN_OUTLINE,
        ("synopsis", "volumes"),
    )
    assert keys == (
        "synopsis",
        "volumes",
        "volume_mode",
        "narrative_phases",
        "key_turning_points",
        "character_arcs",
        "subplot_plan",
        "suspense_schedule",
        "ending_strategy",
        "emotional_arcs",
        "causal_chains",
        "subplot_collisions",
        "subversion_points",
        "chapter_rhythm_curve",
    )


def test_mock_chapter_plan_satisfies_plan_contract() -> None:
    validate_json_output_contract(
        TaskType.PLAN_CHAPTER,
        json.loads(_MOCK_CHAPTER_PLAN),
    )


def test_mock_blueprint_element_select_satisfies_contract() -> None:
    validate_json_output_contract(
        TaskType.BLUEPRINT_ELEMENT_SELECT,
        json.loads(_MOCK_BLUEPRINT_ELEMENT_SELECT),
    )


def test_mock_check_chapter_satisfies_contract() -> None:
    validate_json_output_contract(
        TaskType.CHECK_CHAPTER,
        json.loads(_MOCK_CHECK_CHAPTER),
    )


def test_plan_outline_character_arcs_fragment_uses_strong_pydantic_envelope() -> None:
    context = {
        "blueprint_fragment_request": {
            "block_key": "character_arcs",
            "required_keys": ["character_arcs", "emotional_arcs"],
        }
    }

    contract = resolve_task_format_contract(TaskType.PLAN_OUTLINE, context)

    assert contract is not None
    assert contract.effective_contract_mode == ContractMode.FRAGMENT_OBJECT
    assert contract.response_schema_model is not None
    assert contract.response_schema_model.__name__ == "PlanOutlineCharacterArcsFragment"
    assert contract.schema_source == "pydantic_model"
    assert contract.schema_strength == "strong"
    assert contract.json_schema is not None
    assert contract.json_schema["additionalProperties"] is False
    assert set(contract.json_schema["properties"]) == {"character_arcs", "emotional_arcs"}


def test_collect_json_output_contract_issues_is_machine_readable() -> None:
    context = {
        "blueprint_fragment_request": {
            "block_key": "ending",
            "required_keys": ["ending_strategy"],
        }
    }

    issues = collect_json_output_contract_issues(
        TaskType.PLAN_OUTLINE,
        {"ending_strategy": {"summary": "bad"}, "extra": True},
        context=context,
        explicit_required_keys=("ending_strategy",),
        include_contract_required_keys=False,
        validate_allowed_keys=True,
        validate_json_schema=True,
    )

    issue_types = {(issue.path, issue.issue_type) for issue in issues}
    assert ("$.extra", "extra_key") in issue_types
    assert ("$.ending_strategy", "type_mismatch") in issue_types

    missing_issues = collect_json_output_contract_issues(
        TaskType.PLAN_OUTLINE,
        {},
        context=context,
        explicit_required_keys=("ending_strategy",),
        include_contract_required_keys=False,
        validate_allowed_keys=True,
        validate_json_schema=True,
    )
    missing_keys = [issue for issue in missing_issues if issue.path == "$.ending_strategy"]
    assert len(missing_keys) == 1
    assert missing_keys[0].expected == "required top-level key"


def test_render_prompt_contract_block_for_json_task() -> None:
    block = render_prompt_contract_block(TaskType.BEATS)
    assert "统一格式契约（系统注入）" in block
    assert "contract_mode: `full_object`" in block
    assert "`beats`" in block
    assert "json.loads" in block
    assert "顶层必须包含字段" in block


def test_render_prompt_contract_block_uses_required_language_for_all_keyed_tasks() -> None:
    block = render_prompt_contract_block(TaskType.CHECK_ALIGNMENT)
    assert "顶层必须包含字段" in block
    assert "顶层建议包含字段" not in block


def test_contract_completion_adjudication_contract_owns_full_shape() -> None:
    contract = get_task_format_contract(TaskType.ADJUDICATE_CONTRACT_COMPLETION)

    assert contract is not None
    assert contract.output_kind == OutputKind.JSON
    assert contract.effective_contract_mode == ContractMode.FULL_OBJECT
    assert contract.enforce_required_keys is True
    assert contract.required_top_level_keys == (
        "verdict",
        "severity",
        "rationale",
        "repair_or_replan_decision",
        "missing_required_progressions",
        "missing_knowledge_ops",
        "forbidden_progression_hits",
        "future_leak_hits",
        "evidence_quotes",
        "should_block_archive",
        "contract_completion_score",
    )
    assert contract.allowed_top_level_keys == (
        "verdict",
        "severity",
        "rationale",
        "repair_or_replan_decision",
        "missing_required_progressions",
        "missing_knowledge_ops",
        "forbidden_progression_hits",
        "future_leak_hits",
        "cognitive_constraint_hits",
        "unexpected_progressions",
        "unaccepted_knowledge_ops",
        "evidence_quotes",
        "should_block_archive",
        "contract_completion_score",
    )
    assert contract.json_schema is not None
    assert contract.json_schema["additionalProperties"] is False
    assert contract.json_schema["properties"]["repair_or_replan_decision"]["enum"] == [
        "continue",
        "repair",
        "replan",
        "repair_or_replan",
    ]
    assert contract.json_schema["properties"]["should_block_archive"]["type"] == "boolean"
    assert contract.json_schema["properties"]["contract_completion_score"]["type"] == "number"


def test_critical_chapter_tasks_require_native_json() -> None:
    for task_type in (
        TaskType.EXTRACT_CANDIDATE_STATE_DELTAS,
        TaskType.ADJUDICATE_STATE_DELTA,
        TaskType.ADJUDICATE_CONTRACT_COMPLETION,
        TaskType.ADJUDICATE_FINAL_STATE,
        TaskType.HUMANIZE_SCAN,
    ):
        contract = get_task_format_contract(task_type)
        assert contract is not None
        assert contract.require_native_structured_output is True


def test_entity_reference_adjudication_allows_prompt_only_routes_with_local_validation() -> None:
    contract = get_task_format_contract(TaskType.ADJUDICATE_ENTITY_REFERENCES)

    assert contract is not None
    assert contract.require_native_structured_output is False
    assert contract.json_schema is not None


def test_contract_completion_adjudication_prompt_and_runtime_schema_are_strict() -> None:
    payload = {
        "verdict": "accept",
        "severity": "low",
        "rationale": "本章完成当前契约且没有越界推进。",
        "repair_or_replan_decision": "continue",
        "missing_required_progressions": [],
        "missing_knowledge_ops": [],
        "forbidden_progression_hits": [],
        "future_leak_hits": [],
        "evidence_quotes": [],
        "should_block_archive": False,
        "contract_completion_score": 10.0,
    }
    validate_json_output_contract(
        TaskType.ADJUDICATE_CONTRACT_COMPLETION,
        payload,
        validate_json_schema=True,
    )

    block = render_prompt_contract_block(TaskType.ADJUDICATE_CONTRACT_COMPLETION)
    assert "`repair_or_replan_decision`" in block
    assert '"repair_or_replan_decision"' in block
    assert '"repair_or_replan"' in block
    assert '"should_block_archive"' in block
    assert '"contract_completion_score"' in block

    missing_payload = dict(payload)
    missing_payload.pop("future_leak_hits")
    try:
        validate_json_output_contract(TaskType.ADJUDICATE_CONTRACT_COMPLETION, missing_payload)
    except KeyError as exc:
        assert "future_leak_hits" in str(exc)
    else:
        raise AssertionError("expected missing contract-completion key to be rejected")

    extra_payload = dict(payload)
    extra_payload["debug_notes"] = "should not be accepted"
    try:
        validate_json_output_contract(TaskType.ADJUDICATE_CONTRACT_COMPLETION, extra_payload)
    except KeyError as exc:
        assert "Unexpected response key(s): debug_notes" in str(exc)
    else:
        raise AssertionError("expected extra contract-completion key to be rejected")

    invalid_payload = dict(payload)
    invalid_payload["should_block_archive"] = "false"
    try:
        validate_json_output_contract(
            TaskType.ADJUDICATE_CONTRACT_COMPLETION,
            invalid_payload,
            validate_json_schema=True,
        )
    except ValueError as exc:
        assert "should_block_archive" in str(exc)
        assert "expected boolean" in str(exc)
    else:
        raise AssertionError("expected contract-completion type violation")


def test_contract_completion_adjudication_full_prompt_has_single_builder_contract() -> None:
    rendered = PromptBuilder().render(
        TaskType.ADJUDICATE_CONTRACT_COMPLETION,
        {
            "contract_item": "{}",
            "current_state": "{}",
            "evidence_window": "正文证据窗口",
            "local_prescreen": "",
        },
    )

    assert rendered.count("## 统一格式契约（系统注入）") == 1
    assert "## 输出格式" not in rendered
    assert "## 可选 verdict" not in rendered
    assert "顶层必须包含字段" in rendered
    assert "`contract_completion_score`" in rendered
    assert '"contract_completion_score"' in rendered


def test_no_required_key_contract_remains_non_strict() -> None:
    offenders = [
        task_type.value
        for task_type, contract in _TASK_FORMAT_CONTRACTS.items()
        if contract.required_top_level_keys and not contract.enforce_required_keys
    ]
    assert offenders == []


def test_render_prompt_contract_block_injects_text_guard() -> None:
    block = render_prompt_contract_block(TaskType.DRAFT_CHAPTER)
    assert "统一格式契约（系统注入）" in block
    assert "contract_mode: `text_only`" in block
    assert "禁止 JSON" in block
    assert "scene_intent" in block
    assert "opening_bridge" in block


def test_contract_registry_covers_text_and_json_kinds() -> None:
    beats_contract = get_task_format_contract(TaskType.BEATS)
    draft_contract = get_task_format_contract(TaskType.DRAFT)

    assert beats_contract is not None
    assert beats_contract.output_kind == OutputKind.JSON

    assert draft_contract is not None
    assert draft_contract.output_kind == OutputKind.TEXT


def test_validate_text_output_contract_accepts_plain_prose() -> None:
    text = "她推开门，风从走廊尽头压了进来。"
    assert validate_text_output_contract(TaskType.DRAFT_CHAPTER, text, text) == text


def test_validate_text_output_contract_strips_leading_chapter_heading() -> None:
    raw = "# 第一章 峰会初逢\n\n她推开门，风从走廊尽头压了进来。"
    expected = "她推开门，风从走廊尽头压了进来。"

    assert validate_text_output_contract(TaskType.DRAFT_CHAPTER, raw, raw) == expected


def test_validate_text_output_contract_rejects_mid_body_markdown_heading() -> None:
    raw = "她推开门，风从走廊尽头压了进来。\n\n## 场景二\n\n脚步声停在门外。"

    try:
        validate_text_output_contract(TaskType.DRAFT_CHAPTER, raw, raw)
    except TextOutputContractError as exc:
        assert "Markdown heading" in str(exc)
    else:
        raise AssertionError("expected TextOutputContractError")


def test_validate_text_output_contract_unwraps_markdown_fence() -> None:
    raw = "```text\n她推开门，风从走廊尽头压了进来。\n```"
    try:
        validate_text_output_contract(TaskType.DRAFT_CHAPTER, raw, raw)
    except TextOutputContractError as exc:
        assert "code fence" in str(exc)
    else:
        raise AssertionError("expected TextOutputContractError")


def test_validate_text_output_contract_rejects_unextracted_json() -> None:
    raw = '{"status": "ok", "notes": "不是正文"}'
    try:
        validate_text_output_contract(TaskType.DRAFT_CHAPTER, raw, raw)
    except TextOutputContractError as exc:
        assert "structured payload" in str(exc) or "JSON/list" in str(exc)
    else:
        raise AssertionError("expected TextOutputContractError")


def test_validate_text_output_contract_ignores_json_tasks() -> None:
    raw = '{"beats": []}'
    assert validate_text_output_contract(TaskType.BEATS, raw, raw) == raw


def _assert_text_contract_rejects(raw: str, expected: str) -> None:
    try:
        validate_text_output_contract(TaskType.DRAFT_CHAPTER, raw, raw)
    except TextOutputContractError as exc:
        assert expected in str(exc)
    else:
        raise AssertionError("expected TextOutputContractError")


def test_validate_text_output_contract_rejects_planning_tokens_and_meta_prefixes() -> None:
    cases = (
        ("scene_intent：主角必须进入旧楼。\n她推开门，雨声压低。", "scene_intent"),
        ("required_outcome：确认线索。\n她摸到信封边缘的蜡印。", "required_outcome"),
        ("opening_contract：承接上章追逐。\n她没有回头。", "opening_contract"),
        ("opening_bridge：承接上章追逐。\n她没有回头。", "opening_bridge"),
        ("自检：已完成修复。\n她推开门，风从走廊尽头压了进来。", "meta"),
        ("以下是修订后的正文：\n她推开门，风从走廊尽头压了进来。", "meta"),
    )

    for raw, expected in cases:
        _assert_text_contract_rejects(raw, expected)


def test_validate_text_output_contract_rejects_list_protocol_output() -> None:
    raw = "1. 强化开场承接\n2. 修复结尾钩子\n3. 保持人物动机"

    _assert_text_contract_rejects(raw, "instruction/list protocol")


def test_validate_text_output_contract_rejects_markdown_heading_after_stripped_lead() -> None:
    raw = "# 第一章 峰会初逢\n\n她推开门，风从走廊尽头压了进来。\n\n## 场景二\n\n脚步声停在门外。"

    _assert_text_contract_rejects(raw, "Markdown heading")


def test_patch_chapter_contract_matches_patch_json_output() -> None:
    contract = get_task_format_contract(TaskType.PATCH_CHAPTER)
    assert contract is not None
    assert contract.output_kind == OutputKind.JSON
    assert contract.required_top_level_keys == ("patches",)
    assert contract.enforce_required_keys is True
    assert contract.effective_contract_mode == ContractMode.PATCH_PLAN


def test_fragment_contract_prompt_block_explains_fragment_scope() -> None:
    contract = get_task_format_contract(TaskType.INIT_STORY_WORLD_RULES)
    assert contract is not None
    assert contract.effective_contract_mode == ContractMode.FRAGMENT_OBJECT
    block = render_prompt_contract_block(TaskType.INIT_STORY_WORLD_RULES)
    assert "contract_mode: `fragment_object`" in block
    assert "只输出当前子任务负责的结构片段" in block


def test_prompt_schema_omits_runtime_metadata_for_plan_outline_fragments() -> None:
    block = render_prompt_contract_block(
        TaskType.PLAN_OUTLINE,
        {
            "blueprint_fragment_request": {
                "block_key": "subplots",
                "required_keys": ["subplot_plan"],
            },
        },
    )

    assert "schema_version" not in block
    assert "created_at" not in block


def test_plan_outline_contract_uses_canonical_blueprint_schema() -> None:
    contract = resolve_task_format_contract(TaskType.PLAN_OUTLINE)

    assert contract is not None
    assert contract.schema_model == "narrative_blueprint"
    assert contract.allowed_top_level_keys == contract.required_top_level_keys
    assert contract.json_schema is not None
    assert set(contract.json_schema["properties"]) == set(contract.required_top_level_keys)

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
                        "心理阶段": "试探",
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
    }

    try:
        validate_json_output_contract(TaskType.PLAN_OUTLINE, payload)
    except ValueError as exc:
        assert "$.character_arcs[0].milestones[0]: unexpected property `心理阶段`" in str(exc)
    else:
        raise AssertionError("expected nested NarrativeBlueprint schema violation")


def test_prompt_contract_block_warns_against_field_leakage_into_text() -> None:
    block = render_prompt_contract_block(TaskType.PLAN_OUTLINE)

    assert "禁止把结构字段泄漏到文本字段中" in block
    assert ".field: value" in block


def test_prompt_contract_block_marks_schema_metadata_as_non_output_keys() -> None:
    block = render_prompt_contract_block(TaskType.INIT_CHARACTER_PROFILE_BATCH)

    assert "JSON Schema 仅供理解校验规则，不是输出样例" in block
    assert "`type`" in block
    assert "`additionalProperties`" in block
    assert "绝不能作为最终输出 JSON 的 key" in block


def test_prompt_schema_strips_llm_parrot_keywords() -> None:
    """Regression: the 魂玉 init_character_profile_batch failure had the LLM
    echo ``type`` / ``required`` / ``additionalProperties`` / ``minItems`` as
    top-level JSON output keys, sourced from a nested schema. The
    prompt-facing schema must strip these four keywords on schema-metadata
    nodes (those with ``properties`` / ``items`` / ``oneOf`` etc.) so the
    LLM has no material to parrot. Leaf primitive nodes keep ``type`` so
    the LLM still sees field type guidance."""
    from novel_forge.core.format_contracts import _schema_for_prompt_display

    schema = {
        "type": "object",
        "required": ["character_profiles"],
        "properties": {
            "character_profiles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {"name": {"type": "string"}},
                    "additionalProperties": False,
                },
                "minItems": 1,
            }
        },
        "additionalProperties": False,
        "schema_version": "1.0",
        "created_at": "2026-01-01",
    }
    cleaned = _schema_for_prompt_display(schema, protected_top_level_keys={"character_profiles"})

    assert "type" not in cleaned
    assert "required" not in cleaned
    assert "additionalProperties" not in cleaned
    assert "schema_version" not in cleaned
    assert "created_at" not in cleaned
    assert "character_profiles" in cleaned["properties"]

    array_node = cleaned["properties"]["character_profiles"]
    assert "type" not in array_node
    assert "minItems" not in array_node

    items_node = array_node["items"]
    assert "type" not in items_node
    assert "required" not in items_node
    assert "additionalProperties" not in items_node

    leaf = items_node["properties"]["name"]
    assert leaf == {"type": "string"}, (
        f"leaf primitive must keep 'type' so the LLM sees field-type guidance; got {leaf!r}"
    )


def test_prompt_schema_preserves_field_named_like_schema_keyword() -> None:
    """Regression: when a user-defined field happens to be named ``type``
    or ``required`` (i.e. a key inside ``properties``), it must be
    preserved — those are field names, not schema keywords."""
    from novel_forge.core.format_contracts import _schema_for_prompt_display

    schema = {
        "type": "object",
        "required": ["required", "type"],
        "properties": {
            "required": {"type": "array"},
            "type": {"type": "string"},
        },
    }
    cleaned = _schema_for_prompt_display(schema, protected_top_level_keys={"required", "type"})
    props = cleaned["properties"]
    assert set(props.keys()) == {"required", "type"}
    assert props["required"] == {"type": "array"}
    assert props["type"] == {"type": "string"}


def test_prompt_schema_recurses_into_oneof_anyof_branches() -> None:
    """Combinator branches must also have parrot keywords stripped."""
    from novel_forge.core.format_contracts import _schema_for_prompt_display

    schema = {
        "type": "object",
        "properties": {
            "value": {
                "oneOf": [
                    {"type": "string", "minLength": 1},
                    {
                        "type": "object",
                        "required": ["x"],
                        "properties": {"x": {"type": "integer"}},
                    },
                ]
            }
        },
    }
    cleaned = _schema_for_prompt_display(schema)
    branches = cleaned["properties"]["value"]["oneOf"]
    assert branches[0] == {"type": "string", "minLength": 1}
    assert "type" not in branches[1]
    assert "required" not in branches[1]
    assert branches[1]["properties"]["x"] == {"type": "integer"}


def test_book_consistency_contract_matches_downstream_summary_payload() -> None:
    contract = get_task_format_contract(TaskType.BOOK_CONSISTENCY)
    assert contract is not None
    assert contract.output_kind == OutputKind.JSON
    assert contract.required_top_level_keys == (
        "issues",
        "repair_plan",
        "summary",
        "consistency_score",
    )
    assert contract.enforce_required_keys is True
    assert contract.json_schema is not None
    assert "issues" in contract.json_schema["properties"]
    issue_schema = contract.json_schema["properties"]["issues"]["items"]
    assert "evidence_pairs" in issue_schema["properties"]
    assert "verification_questions" in issue_schema["properties"]


def test_book_consistency_verify_contract_documents_nested_verified_issues() -> None:
    contract = get_task_format_contract(TaskType.BOOK_CONSISTENCY_VERIFY)
    assert contract is not None
    assert contract.required_top_level_keys == ("verified_issues",)
    assert contract.json_schema is not None
    issue_schema = contract.json_schema["properties"]["verified_issues"]["items"]
    assert "repair_scope" in issue_schema["properties"]
    assert "postconditions" in issue_schema["properties"]


def test_knowledge_boundary_audit_contract_documents_adjudication_fields() -> None:
    contract = get_task_format_contract(TaskType.KNOWLEDGE_BOUNDARY_AUDIT)
    assert contract is not None
    assert contract.output_kind == OutputKind.JSON
    assert contract.required_top_level_keys == ("verdict", "issues")
    assert contract.enforce_required_keys is True
    assert contract.json_schema is not None
    issue_schema = contract.json_schema["properties"]["issues"]["items"]
    assert "decision" in issue_schema["properties"]
    assert "entry_id" in issue_schema["properties"]
    assert "candidate_id" not in issue_schema["properties"]
    assert "repair_goal" in issue_schema["properties"]


def test_knowledge_boundary_audit_contract_requires_entry_id_not_candidate_id() -> None:
    valid_payload = {
        "verdict": "issues_found",
        "issues": [
            {
                "decision": "leak",
                "issue_type": "knowledge_leak",
                "severity": "high",
                "confidence": 0.9,
                "evidence_quote": "她知道账册在门后",
                "entry_id": "k1",
                "repair_goal": "移除越界确认。",
                "paragraph_start": 1,
                "paragraph_end": 1,
                "reason": "缺少当前章获知证据。",
            }
        ],
    }
    validate_json_output_contract(TaskType.KNOWLEDGE_BOUNDARY_AUDIT, valid_payload)

    invalid_payload = json.loads(json.dumps(valid_payload))
    invalid_payload["issues"][0]["candidate_id"] = invalid_payload["issues"][0].pop("entry_id")
    try:
        validate_json_output_contract(TaskType.KNOWLEDGE_BOUNDARY_AUDIT, invalid_payload)
    except ValueError as exc:
        assert "$.issues[0]: missing required property `entry_id`" in str(exc)
    else:
        raise AssertionError("Expected knowledge audit contract to reject candidate_id-only issue")


def test_editorial_audit_contract_documents_nested_findings() -> None:
    for task_type in (TaskType.CHECK_EDITORIAL, TaskType.BOOK_EDITORIAL_AUDIT):
        contract = get_task_format_contract(task_type)
        assert contract is not None
        assert contract.required_top_level_keys == (
            "summary",
            "findings",
            "revision_plan",
            "metrics",
        )
        assert contract.json_schema is not None
        finding_schema = contract.json_schema["properties"]["findings"]["items"]
        assert "issue_type" in finding_schema["properties"]
        assert "metadata" in finding_schema["properties"]


def test_editorial_character_voices_contract_uses_dynamic_name_enum() -> None:
    context = {"voice_character_whitelist": ["林晚", "周正阳"]}
    contract = resolve_task_format_contract(TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES, context)

    assert contract is not None
    assert contract.allowed_top_level_keys == ("character_voices",)
    assert contract.json_schema is not None
    item_schema = contract.json_schema["properties"]["character_voices"]["items"]
    assert item_schema["properties"]["character"]["enum"] == ["林晚", "周正阳"]


def test_editorial_character_voices_contract_rejects_wrong_character_name() -> None:
    context = {"voice_character_whitelist": ["林晚", "周正阳"]}
    payload = {
        "character_voices": [
            {
                "character": "林晚儿",
                "sentence_profile": "短句，先观察再补一句。",
                "explanation_bias": "用数据报告绕开情绪。",
                "emotion_syntax": "情绪升高时句子更短。",
                "signature_moves": ["推眼镜"],
                "taboo_patterns": ["长篇自白"],
                "sample_lines": ["这组数据不对。"],
            }
        ]
    }

    try:
        validate_json_output_contract(
            TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES,
            payload,
            context=context,
        )
    except ValueError as exc:
        assert "$.character_voices[0].character: expected one of [林晚, 周正阳]" in str(exc)
    else:
        raise AssertionError("Expected dynamic character-name enum to reject wrong name")


def test_relationship_repair_contract_enforces_canonical_roster_names() -> None:
    context = {
        "relationship_generation_phase": "repair",
        "character_roster": [{"name": "顾琛"}, {"name": "林晚"}],
    }
    payload = {
        "relationship_matrix": [
            {
                "character_a": "顾珩",
                "character_b": "林晚",
                "relation_type": "alliance",
                "description": "共同调查旧案。",
                "confidence": 0.8,
            }
        ]
    }

    try:
        validate_json_output_contract(
            TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX,
            payload,
            context=context,
        )
    except ValueError as exc:
        assert "$.relationship_matrix[0].character_a: expected one of [顾琛, 林晚]" in str(exc)
    else:
        raise AssertionError("Expected repair relationship endpoint to reject drifted name")


def test_chapter_delta_contracts_reject_unknown_character_names() -> None:
    context = {"known_characters": ["顾琛", "林晚"]}
    invalid_payloads = {
        TaskType.EXTRACT_CHARACTER_STATE_DELTAS: {
            "character_state_deltas": [{"name": "顾珩", "to_state": {}}]
        },
        TaskType.EXTRACT_RELATIONSHIP_DELTAS: {
            "relationship_deltas": [{"relationship": {"characters": ["顾珩", "林晚"]}}]
        },
        TaskType.EXTRACT_CANON: {
            "canon_delta": {},
            "creative_report": {},
            "chapter_exit_state": {},
            "character_state_deltas": [{"name": "顾珩"}],
            "relationship_deltas": [],
            "plot_thread_deltas": [],
            "structured_summary": "",
        },
    }

    for task_type, payload in invalid_payloads.items():
        try:
            validate_json_output_contract(task_type, payload, context=context)
        except ValueError as exc:
            assert "expected one of [顾琛, 林晚]" in str(exc)
        else:
            raise AssertionError(f"Expected {task_type.value} to reject unknown character")


def test_polish_subplot_contract_matches_json_object_output() -> None:
    contract = get_task_format_contract(TaskType.POLISH_SUBPLOT)
    assert contract is not None
    assert contract.output_kind == OutputKind.JSON
    assert contract.required_top_level_keys == ("subplots",)
    assert contract.enforce_required_keys is True


def test_plan_chapter_contract_includes_chapter_type() -> None:
    contract = get_task_format_contract(TaskType.PLAN_CHAPTER)
    assert contract is not None
    assert "chapter_type" in contract.required_top_level_keys
    for key in (
        "world_rule_applications",
        "required_literals",
        "emotional_arc",
        "relationship_evolution",
        "forbidden_elements",
        "forbidden_elements_soft",
        "forbidden_elements_quota",
        "intentional_callbacks",
        "foreshadowing_plan",
        "key_revelations",
    ):
        assert key in contract.required_top_level_keys
    assert contract.enforce_required_keys is True


def test_plan_chapter_contract_includes_cross_scene_intent() -> None:
    """PLAN_CHAPTER must require cross_scene_intent for WAVE to consume.

    Regression: ``cross_scene_intent`` carries ``cross_scene_references`` and
    ``pacing_curve`` which the WAVE pass reads from ``runtime.chapter_plan``.
    The format contract and ``plan_chapter.j2`` template both must list it
    as a required top-level field; otherwise LLM outputs omit it and
    ``wave.apply_wave`` falls back to empty constraints, silently disabling
    cross-scene reference enforcement.
    """
    contract = get_task_format_contract(TaskType.PLAN_CHAPTER)
    assert contract is not None
    assert "cross_scene_intent" in contract.required_top_level_keys
    assert contract.enforce_required_keys is True


def test_plan_chapter_contract_publishes_nested_world_rule_fields() -> None:
    contract = resolve_task_format_contract(TaskType.PLAN_CHAPTER)
    assert contract is not None
    assert contract.json_schema is not None
    schema = contract.json_schema
    item_schema = schema["properties"]["world_rule_applications"]["items"]
    ref_name = item_schema["$ref"].rsplit("/", 1)[-1]
    properties = schema["$defs"][ref_name]["properties"]

    assert {
        "rule_id",
        "scene_id",
        "applicability",
        "usage",
        "expected_evidence",
        "forbidden_boundary",
        "not_applicable_reason",
    }.issubset(properties)
    assert "reason" not in properties


def test_scene_plan_contract_publishes_mandatory_downstream_handoffs() -> None:
    contract = resolve_task_format_contract(TaskType.PLAN_CHAPTER_SCENES)
    assert contract is not None
    assert contract.json_schema is not None
    schema = contract.json_schema
    payload_ref = schema["properties"]["scene_plan"]["$ref"].rsplit("/", 1)[-1]
    payload_schema = schema["$defs"][payload_ref]

    assert {
        "scene_intents",
        "world_rule_applications",
        "required_literals",
        "cross_scene_intent",
    }.issubset(payload_schema["required"])
    assert payload_schema["properties"]["world_rule_applications"]["items"]["$ref"]
    assert payload_schema["properties"]["cross_scene_intent"]["$ref"]


def test_plan_chapter_template_declares_all_required_top_level_keys() -> None:
    """The plan_chapter.j2 template must list every required top-level key.

    Guards against contract/template drift: if a new field is added to
    ``TaskFormatContract.required_top_level_keys`` without updating the
    template's "只输出以下 N 个英文 snake_case 顶层字段" section, the LLM
    is told to omit the field and the contract validator rejects the
    output, causing a retry loop.
    """
    from pathlib import Path

    contract = get_task_format_contract(TaskType.PLAN_CHAPTER)
    assert contract is not None

    canonical_template = Path("novel_forge/prompts/packs/zh/templates/planning/plan_chapter.j2")
    legacy_template = Path("novel_forge/prompts/prompts/planning/plan_chapter.j2")

    for template_path in (canonical_template, legacy_template):
        source = template_path.read_text(encoding="utf-8")
        section = source.split("## 顶层字段要求", 1)[1].split("## 章节卡", 1)[0]
        declared_count = re.search(r"只输出以下\s+(\d+)\s+个英文 `snake_case` 顶层字段", section)
        assert declared_count is not None, f"{template_path} is missing the top-level count line."
        declared_keys = tuple(re.findall(r"^- `([^`]+)`", section, flags=re.MULTILINE))

        assert int(declared_count.group(1)) == len(contract.required_top_level_keys)
        assert declared_keys == contract.required_top_level_keys, (
            f"{template_path} top-level field list drifted from "
            "TaskFormatContract.required_top_level_keys."
        )


def test_bridge_chapter_contract_matches_downstream_bridge_consumers() -> None:
    contract = get_task_format_contract(TaskType.BRIDGE_CHAPTER)
    assert contract is not None
    assert contract.required_top_level_keys == (
        "opening_time",
        "opening_location",
        "opening_pov",
        "transition_mode",
        "emotional_carryover",
        "action_handoff",
        "causal_link",
        "pending_questions",
        "forbidden_repetition",
        "opening_acceptance_criteria",
    )
    assert contract.enforce_required_keys is True


def test_plan_outline_contract_matches_full_blueprint_prompt_fields() -> None:
    contract = get_task_format_contract(TaskType.PLAN_OUTLINE)
    assert contract is not None
    assert contract.required_top_level_keys == (
        "synopsis",
        "volume_mode",
        "volumes",
        "narrative_phases",
        "key_turning_points",
        "character_arcs",
        "subplot_plan",
        "suspense_schedule",
        "ending_strategy",
        "emotional_arcs",
        "causal_chains",
        "subplot_collisions",
        "subversion_points",
        "chapter_rhythm_curve",
    )
    assert contract.enforce_required_keys is True


def test_init_narrative_contract_matches_prompt_fields() -> None:
    contract = get_task_format_contract(TaskType.INIT_NARRATIVE_CONTRACT)
    assert contract is not None
    assert contract.required_top_level_keys == (
        "world_rules",
        "character_arcs",
        "plot_threads",
        "promise_plan",
        "notes",
    )
    assert contract.enforce_required_keys is True


def test_check_chapter_contract_matches_quality_pipeline_payload() -> None:
    contract = get_task_format_contract(TaskType.CHECK_CHAPTER)
    assert contract is not None
    assert contract.required_top_level_keys == (
        "risk_level",
        "summary",
        "prompt_leaks",
        "factual_errors",
        "continuity_errors",
        "expression_errors",
        "repair_actions",
        "forbidden_element_findings",
    )
    assert contract.enforce_required_keys is True


def test_short_blueprint_contract_matches_blueprint_payload() -> None:
    contract = get_task_format_contract(TaskType.SHORT_BLUEPRINT)
    assert contract is not None
    assert contract.required_top_level_keys == (
        "synopsis",
        "anchor_elements",
        "narrative_phases",
        "turning_points",
        "character_arcs",
        "emotional_arc",
        "ending_strategy",
    )
    assert contract.enforce_required_keys is True
    assert get_response_schema(TaskType.SHORT_BLUEPRINT) is not None


def test_short_creative_summary_contract_matches_report_payload() -> None:
    contract = get_task_format_contract(TaskType.SHORT_CREATIVE_SUMMARY)
    assert contract is not None
    assert contract.required_top_level_keys == (
        "characters",
        "narrative_analysis",
        "thematic_analysis",
        "creative_highlights",
        "improvement_suggestions",
        "beat_fulfillment",
    )
    assert contract.enforce_required_keys is True
    assert get_response_schema(TaskType.SHORT_CREATIVE_SUMMARY) is not None


def test_evaluate_contract_matches_eval_report_payload() -> None:
    contract = get_task_format_contract(TaskType.EVALUATE)
    assert contract is not None
    assert contract.required_top_level_keys == (
        "scores",
        "overall_score",
        "passed",
        "threshold",
        "summary",
        "repair_suggestions",
    )
    assert contract.enforce_required_keys is True


def test_profile_style_contract_requires_source_elements() -> None:
    contract = get_task_format_contract(TaskType.PROFILE_STYLE)
    assert contract is not None
    assert contract.required_top_level_keys == (
        "modules",
        "source_elements",
        "summary",
        "global_style",
    )
    assert contract.allowed_top_level_keys == contract.required_top_level_keys
    assert contract.enforce_required_keys is True
    assert contract.effective_contract_mode == ContractMode.FRAGMENT_OBJECT


def test_profile_structure_contract_restricts_top_level_keys() -> None:
    contract = get_task_format_contract(TaskType.PROFILE_STRUCTURE)
    assert contract is not None
    assert contract.required_top_level_keys == (
        "hook_config",
        "strand_config",
        "micro_payoff_config",
        "cool_point_config",
    )
    assert contract.allowed_top_level_keys == contract.required_top_level_keys
    assert contract.enforce_required_keys is True
    assert contract.effective_contract_mode == ContractMode.FRAGMENT_OBJECT


def test_init_coherence_profile_contract_matches_prompt_fields() -> None:
    expected = (
        "genre_tags",
        "narrative_modes",
        "project_ontology",
        "conflict_lens",
        "extraction_guidance",
        "summary",
    )
    for task_type in (
        TaskType.DERIVE_INIT_COHERENCE_PROFILE,
        TaskType.REFINE_INIT_COHERENCE_PROFILE,
    ):
        contract = get_task_format_contract(task_type)
        assert contract is not None
        assert contract.required_top_level_keys == expected
        assert contract.allowed_top_level_keys == expected
        assert contract.enforce_required_keys is True


def test_editorial_structure_contract_rejects_revelation_ladder_extra_keys() -> None:
    payload = {
        "climax_markers": [
            {
                "chapter_number": 40,
                "climax_type": "main",
                "description": "主线摊牌。",
                "expected_aftermath_chapters": 3,
            }
        ],
        "denouement_budget": {
            "expected_chapters": 3,
            "max_confirmation_scenes": 1,
            "required_new_functions": ["余波"],
            "forbidden_repeats": ["重复摊牌"],
        },
        "revelation_ladder": [
            {
                "thread": "信物线",
                "stage": "物证",
                "stage_order": 1,
                "target_chapter": 20,
                "trigger": "怀表刻字",
                "allowed_disclosure": "只确认怀表不是偶然出现。",
                "required_action_consequence": "主角主动查证来源。",
                "rationale": "不允许输出解释字段。",
            }
        ],
        "time_bridge_policies": ["跨月转场必须明确标注。"],
        "title_policy": {
            "max_reuse": 1,
            "allowed_repeated_titles": [],
            "naming_strategy": "标题唯一。",
        },
    }

    try:
        validate_json_output_contract(TaskType.DERIVE_EDITORIAL_STRUCTURE, payload)
    except ValueError as exc:
        assert "unexpected property `rationale`" in str(exc)
    else:
        raise AssertionError("expected revelation_ladder extra key to be rejected")


def test_editorial_structure_contract_rejects_denouement_budget_extra_keys() -> None:
    payload = {
        "climax_markers": [],
        "denouement_budget": {
            "expected_chapters": 1,
            "max_confirmation_scenes": 1,
            "required_new_functions": ["余波后果"],
            "forbidden_repeats": ["重复圆满确认"],
            "forbidden_repeats_description": "不允许的解释键。",
        },
        "revelation_ladder": [],
        "time_bridge_policies": [],
        "title_policy": {
            "max_reuse": 1,
            "allowed_repeated_titles": [],
            "naming_strategy": "标题唯一。",
        },
    }

    try:
        validate_json_output_contract(TaskType.DERIVE_EDITORIAL_STRUCTURE, payload)
    except ValueError as exc:
        assert "unexpected property `forbidden_repeats_description`" in str(exc)
    else:
        raise AssertionError("expected denouement_budget extra key to be rejected")


def test_editorial_style_contract_rejects_symbol_policy_explanation_budget_overflow() -> None:
    payload = {
        "theme_policies": [],
        "symbol_policies": [
            {
                "symbol": "残缺意象",
                "narrative_function": "身份错位与自我接纳。",
                "explanation_policy": "free",
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

    try:
        validate_json_output_contract(TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS, payload)
    except ValueError as exc:
        assert "max_explicit_explanations" in str(exc)
        assert "expected <= 10" in str(exc)
    else:
        raise AssertionError("expected symbol policy explanation budget overflow to be rejected")


def test_init_coherence_ontology_contract_requires_narrative_modes() -> None:
    contract = get_task_format_contract(TaskType.INIT_COHERENCE_ONTOLOGY)
    assert contract is not None
    assert contract.required_top_level_keys == (
        "genre_tags",
        "narrative_modes",
        "project_ontology",
    )
    assert contract.allowed_top_level_keys == contract.required_top_level_keys
    assert contract.enforce_required_keys is True
    assert contract.effective_contract_mode == ContractMode.FRAGMENT_OBJECT


def test_runtime_transform_tasks_use_fragment_or_patch_modes() -> None:
    fragment_tasks = (
        TaskType.PROFILE_STRUCTURE,
        TaskType.PLAN_CHAPTER_SCENES,
        TaskType.POLISH_SUBPLOT,
        TaskType.CONTEXT_COMPRESS,
        TaskType.ADAPTIVE_COMPRESS,
        TaskType.EXTRACT_MOTIFS,
        TaskType.ENRICH_CHARACTER,
    )
    for task_type in fragment_tasks:
        contract = get_task_format_contract(task_type)
        assert contract is not None
        assert contract.effective_contract_mode == ContractMode.FRAGMENT_OBJECT

    contract = get_task_format_contract(TaskType.ADJUST_OUTLINE)
    assert contract is not None
    assert contract.effective_contract_mode == ContractMode.PATCH_PLAN


def test_spec_enrich_contract_does_not_require_legacy_writing_style() -> None:
    contract = get_task_format_contract(TaskType.SPEC_ENRICH)
    assert contract is not None
    assert "writing_style" not in contract.required_top_level_keys
    assert "theme" in contract.required_top_level_keys
    assert contract.enforce_required_keys is True
    assert contract.allowed_top_level_keys == contract.required_top_level_keys


def _generate_config_render_context(mode: str) -> dict[str, object]:
    if mode == "short":
        output_fields_text = (
            "创作配置字段：theme、genre、tone、length_target、max_edit_rounds、writing_mode、"
            "title、characters_hint、world_hint、conflict_hint、pov_hint、opening_style、"
            "ending_style、extra_instructions、project_id、language\n"
            "桌面元数据字段：polish_suggestions、creative_note"
        )
        mode_label = "短篇小说"
    else:
        output_fields_text = (
            "创作配置字段：premise、genre、tone、total_chapters、words_per_chapter、"
            "volume_mode、chapters_per_volume、max_edit_rounds、title、characters_hint、"
            "world_hint、conflict_hint、pov_hint、opening_style、ending_style、"
            "extra_instructions、polish_hint、project_id、language\n"
            "桌面元数据字段：polish_suggestions、creative_note"
        )
        mode_label = "长篇小说"

    return {
        "mode": mode,
        "mode_label": mode_label,
        "operation": "generate",
        "user_hint": "雪港、旧案、死亡预告",
        "output_fields_text": output_fields_text,
    }


def test_generate_config_prompts_inject_mode_scoped_contracts() -> None:
    builder = PromptBuilder()
    expectations = {
        "short": ("`theme`", "`length_target`"),
        "long": ("`premise`", "`total_chapters`", "`words_per_chapter`"),
    }

    for mode, required_markers in expectations.items():
        request = builder.build(TaskType.GENERATE_CONFIG, _generate_config_render_context(mode))
        rendered = request.messages[-1]["content"]
        contract_block = rendered.split("## 统一格式契约（系统注入）", 1)[1]

        assert rendered.count("## 统一格式契约（系统注入）") == 1
        assert "contract_mode: `full_object`" in contract_block
        assert "顶层必须包含字段" in contract_block
        assert "JSON Schema（顶层约束，运行时会按此验证）" in contract_block
        assert request.response_json_schema is not None
        for marker in required_markers:
            assert marker in contract_block


def test_generate_config_prompt_does_not_show_invalid_json_range_examples() -> None:
    rendered = PromptBuilder().render(
        TaskType.GENERATE_CONFIG,
        _generate_config_render_context("long"),
    )

    assert "```json" not in rendered
    assert '"total_chapters": 16-40' not in rendered
    assert '"words_per_chapter": 3000-20000' not in rendered
    assert '"length_target": 3000-20000' not in rendered
    assert "数值字段必须是具体整数，不要使用 `1-3`、`3000-20000`、`16-40` 这类范围表达" in rendered


def test_polish_config_prompt_keeps_partial_contract_without_required_keys() -> None:
    rendered = PromptBuilder().render(
        TaskType.POLISH_CONFIG,
        {
            "mode": "long",
            "mode_label": "长篇小说",
            "operation": "polish",
            "allow_partial_config_output": True,
            "editable_config_fields": ["title", "world_hint"],
            "focus_fields": ["title", "world_hint"],
            "current_config_json": '{"title": "雪港", "world_hint": "终年暴雪的海港。"}',
            "output_fields_text": "创作配置字段：title、world_hint\n桌面元数据字段：polish_suggestions、creative_note",
        },
    )
    contract_block = rendered.split("## 统一格式契约（系统注入）", 1)[1]

    assert rendered.count("## 统一格式契约（系统注入）") == 1
    assert "contract_mode: `partial_object`" in contract_block
    assert "顶层必须包含字段" not in contract_block
    assert (
        "顶层只允许字段：`title`、`world_hint`、`polish_suggestions`、`creative_note`"
        in contract_block
    )
    assert '"title"' in contract_block
    assert '"world_hint"' in contract_block
    assert '"total_chapters"' not in contract_block


def test_dynamic_generate_config_contract_uses_long_mode_fields() -> None:
    contract = resolve_task_format_contract(TaskType.GENERATE_CONFIG, {"mode": "long"})
    assert contract is not None
    assert "total_chapters" in contract.required_top_level_keys
    assert "words_per_chapter" in contract.required_top_level_keys
    assert "chapters_per_volume" in contract.required_top_level_keys
    assert "length_target" not in contract.required_top_level_keys
    assert contract.allowed_top_level_keys == contract.required_top_level_keys
    assert contract.json_schema is not None
    assert tuple(contract.json_schema["properties"]) == contract.allowed_top_level_keys
    assert tuple(contract.json_schema["required"]) == contract.required_top_level_keys
    assert contract.json_schema["properties"]["total_chapters"]["type"] == "integer"
    assert contract.json_schema["properties"]["creative_note"]["type"] == "object"


def test_dynamic_generate_config_schema_is_mode_scoped() -> None:
    short_contract = resolve_task_format_contract(TaskType.GENERATE_CONFIG, {"mode": "short"})
    long_contract = resolve_task_format_contract(TaskType.GENERATE_CONFIG, {"mode": "long"})
    assert short_contract is not None
    assert long_contract is not None
    assert short_contract.json_schema is not None
    assert long_contract.json_schema is not None

    short_properties = set(short_contract.json_schema["properties"])
    long_properties = set(long_contract.json_schema["properties"])
    assert short_properties == set(short_contract.allowed_top_level_keys)
    assert long_properties == set(long_contract.allowed_top_level_keys)
    assert {"premise", "total_chapters", "words_per_chapter", "chapters_per_volume"}.isdisjoint(
        short_properties
    )
    assert {"theme", "length_target"}.isdisjoint(long_properties)
    assert {"polish_suggestions", "creative_note"} <= short_properties
    assert {"polish_suggestions", "creative_note"} <= long_properties


def test_static_polish_config_contract_is_partial_by_default() -> None:
    contract = get_task_format_contract(TaskType.POLISH_CONFIG)
    assert contract is not None
    assert contract.required_top_level_keys == ()
    assert contract.enforce_required_keys is False
    assert contract.strict_mode is False
    assert "premise" in contract.allowed_top_level_keys
    assert "theme" in contract.allowed_top_level_keys
    assert contract.json_schema is not None
    assert tuple(contract.json_schema["properties"]) == contract.allowed_top_level_keys
    assert contract.json_schema["required"] == []
    assert contract.effective_contract_mode == ContractMode.PARTIAL_OBJECT


def test_dynamic_polish_config_defaults_to_partial_without_context_flag() -> None:
    contract = resolve_task_format_contract(TaskType.POLISH_CONFIG, {"mode": "long"})
    assert contract is not None
    assert contract.required_top_level_keys == ()
    assert contract.enforce_required_keys is False
    assert "premise" in contract.allowed_top_level_keys
    assert "total_chapters" in contract.allowed_top_level_keys
    assert "length_target" not in contract.allowed_top_level_keys
    assert contract.json_schema is not None
    assert set(contract.json_schema["properties"]) == set(contract.allowed_top_level_keys)
    assert "length_target" not in contract.json_schema["properties"]
    assert contract.strict_mode is False
    assert contract.effective_contract_mode == ContractMode.PARTIAL_OBJECT


def test_dynamic_polish_config_partial_contract_allows_patch_payload() -> None:
    contract = resolve_task_format_contract(
        TaskType.POLISH_CONFIG,
        {"mode": "long", "allow_partial_config_output": True},
    )
    assert contract is not None
    assert contract.required_top_level_keys == ()
    assert contract.enforce_required_keys is False
    assert "premise" in contract.allowed_top_level_keys
    assert "polish_suggestions" in contract.allowed_top_level_keys
    assert contract.strict_mode is False
    assert contract.effective_contract_mode == ContractMode.PARTIAL_OBJECT


def test_dynamic_polish_config_partial_contract_uses_editable_fields() -> None:
    contract = resolve_task_format_contract(
        TaskType.POLISH_CONFIG,
        {
            "mode": "long",
            "allow_partial_config_output": True,
            "editable_config_fields": ["title", "world_hint"],
        },
    )
    assert contract is not None
    assert contract.required_top_level_keys == ()
    assert contract.enforce_required_keys is False
    assert contract.allowed_top_level_keys == (
        "title",
        "world_hint",
        "polish_suggestions",
        "creative_note",
    )
    assert contract.json_schema is not None
    assert tuple(contract.json_schema["properties"]) == contract.allowed_top_level_keys
    assert contract.json_schema["required"] == []
    assert contract.effective_contract_mode == ContractMode.PARTIAL_OBJECT


def test_validate_json_output_contract_rejects_extra_allowed_key() -> None:
    contract = resolve_task_format_contract(TaskType.SPEC_ENRICH)
    assert contract is not None
    payload = {key: "" for key in contract.required_top_level_keys}
    payload["unexpected"] = ""

    try:
        validate_json_output_contract(TaskType.SPEC_ENRICH, payload)
    except KeyError as exc:
        assert "Unexpected response key" in str(exc)
    else:
        raise AssertionError("expected KeyError")


def test_validate_json_output_contract_rejects_nested_schema_type_drift() -> None:
    payload = {
        "issues": "not an array",
        "repair_plan": [],
        "summary": "ok",
        "consistency_score": 8.0,
    }

    try:
        validate_json_output_contract(TaskType.BOOK_CONSISTENCY, payload)
    except ValueError as exc:
        assert "$.issues: expected array" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_validate_json_output_contract_rejects_nested_missing_required_key() -> None:
    payload = {
        "issues": [
            {
                "category": "naming",
                "severity": "high",
                "chapters_involved": [1],
                "primary_chapter": 1,
                "issue_type": "name_drift",
                "location": "第1章",
                "paragraph_index": 1,
                "paragraph_span": [1],
                "evidence": "证据",
                "description": "问题",
                "suggestion": "建议",
                "fix_mode": "patch",
                "fix_action": "统一命名",
                "confidence": 0.9,
                "evidence_pairs": [],
                "verification_questions": [],
                "handoff_notes": "",
                "linked_issue_refs": [],
            }
        ],
        "repair_plan": [],
        "summary": "ok",
        "consistency_score": 8.0,
    }

    try:
        validate_json_output_contract(TaskType.BOOK_CONSISTENCY, payload)
    except ValueError as exc:
        assert "$.issues[0]: missing required property `issue_id`" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_guard_constraint_check_contract_matches_consumer_payload() -> None:
    contract = get_task_format_contract(TaskType.GUARD_CONSTRAINT_CHECK)
    assert contract is not None
    assert contract.required_top_level_keys == ("status", "confidence", "evidence", "notes")
    assert contract.enforce_required_keys is True


def test_introduce_character_contract_matches_character_profile_output() -> None:
    contract = get_task_format_contract(TaskType.INTRODUCE_CHARACTER)
    assert contract is not None
    assert contract.required_top_level_keys == (
        "name",
        "role",
        "gender",
        "social_status",
        "abilities",
        "appearance",
        "personality",
        "backstory",
        "arc",
        "relationships",
        "voice",
        "notes",
    )
    assert contract.enforce_required_keys is True


def test_introduce_character_contract_has_json_schema_with_proper_property_types() -> None:
    contract = get_task_format_contract(TaskType.INTRODUCE_CHARACTER)
    assert contract is not None
    assert contract.json_schema is not None

    schema = contract.json_schema
    assert schema["type"] == "object"
    assert set(schema["required"]) == {
        "name",
        "role",
        "gender",
        "social_status",
        "abilities",
        "appearance",
        "personality",
        "backstory",
        "arc",
        "relationships",
        "voice",
        "notes",
    }

    props = schema["properties"]
    for field in (
        "name",
        "role",
        "gender",
        "social_status",
        "abilities",
        "appearance",
        "personality",
        "backstory",
        "arc",
        "voice",
        "notes",
    ):
        assert props[field]["type"] == "string", f"{field} should be string type"

    assert props["relationships"]["type"] == "object"
    assert props["relationships"]["additionalProperties"] == {"type": "string"}
    assert schema["additionalProperties"] is False


def test_introduce_character_contract_enforce_required_keys_validates_all_12_keys() -> None:
    """Verify enforce_required_keys=True works with all 12 required keys."""
    valid_payload = {
        "name": "张三",
        "role": "主角",
        "gender": "男",
        "social_status": "普通市民",
        "abilities": "无特殊能力",
        "appearance": "中等身材",
        "personality": "沉稳",
        "backstory": "普通人",
        "arc": "成长",
        "relationships": {"李四": "朋友"},
        "voice": "沉稳低沉的嗓音",
        "notes": "",
    }
    # Should not raise
    validate_json_output_contract(TaskType.INTRODUCE_CHARACTER, valid_payload)


def test_introduce_character_contract_rejects_missing_required_key() -> None:
    payload = {
        "name": "张三",
        "role": "主角",
        "gender": "男",
        "social_status": "普通市民",
        "abilities": "无特殊能力",
        "appearance": "中等身材",
        "personality": "沉稳",
        "backstory": "普通人",
        "arc": "成长",
        "relationships": {"李四": "朋友"},
        "voice": "沉稳低沉的嗓音",
        # missing "notes"
    }
    try:
        validate_json_output_contract(TaskType.INTRODUCE_CHARACTER, payload)
    except KeyError as exc:
        assert "Missing required response key" in str(exc)
    else:
        raise AssertionError("expected KeyError for missing 'notes'")


def test_introduce_character_contract_rejects_missing_gender() -> None:
    payload = {
        "name": "张三",
        "role": "主角",
        "social_status": "普通市民",
        "abilities": "无特殊能力",
        "appearance": "中等身材",
        "personality": "沉稳",
        "backstory": "普通人",
        "arc": "成长",
        "relationships": {"李四": "朋友"},
        "voice": "沉稳低沉的嗓音",
        "notes": "",
    }
    try:
        validate_json_output_contract(TaskType.INTRODUCE_CHARACTER, payload)
    except KeyError as exc:
        assert "Missing required response key(s): gender" in str(exc)
    else:
        raise AssertionError("expected KeyError for missing 'gender'")


def test_introduce_character_contract_rejects_type_drift() -> None:
    payload = {
        "name": "张三",
        "role": "主角",
        "gender": "男",
        "social_status": "普通市民",
        "abilities": "无特殊能力",
        "appearance": "中等身材",
        "personality": "沉稳",
        "backstory": "普通人",
        "arc": "成长",
        "relationships": "not an object",
        "voice": "沉稳低沉的嗓音",
        "notes": "",
    }
    try:
        validate_json_output_contract(TaskType.INTRODUCE_CHARACTER, payload)
    except ValueError as exc:
        assert "$.relationships: expected object" in str(exc)
    else:
        raise AssertionError("expected ValueError for relationships type drift")


def test_adjudicate_character_introduction_contract_is_strict() -> None:
    contract = get_task_format_contract(TaskType.ADJUDICATE_CHARACTER_INTRODUCTION)
    assert contract is not None
    assert contract.output_kind == OutputKind.JSON
    assert contract.required_top_level_keys == ("decisions", "summary")
    assert contract.enforce_required_keys is True


def test_element_progress_arbiter_contract_is_strict() -> None:
    contract = get_task_format_contract(TaskType.ELEMENT_PROGRESS_ARBITER)
    assert contract is not None
    assert contract.required_top_level_keys == ("status", "confidence", "reason")
    assert contract.enforce_required_keys is True


def test_macro_guard_contract_matches_response_schema() -> None:
    contract = get_task_format_contract(TaskType.MACRO_GUARD_AUDIT)
    assert contract is not None
    assert contract.required_top_level_keys == (
        "dimensions",
        "recommended_action",
        "drift_score",
        "findings",
        "adjustment_plan",
        "confidence",
        "reasoning",
    )
    assert contract.schema_model == "macro_guard_audit"
    assert contract.json_schema is not None
    assert contract.enforce_required_keys is True


def test_volume_audit_contract_requires_bridge_fields() -> None:
    contract = get_task_format_contract(TaskType.VOLUME_AUDIT)
    assert contract is not None
    assert contract.required_top_level_keys == (
        "volume_number",
        "volume_summary",
        "milestone_status",
        "carry_over_characters",
        "carry_over_items",
        "carry_over_world_fact_keys",
        "carry_over_foreshadowing_ids",
        "next_volume_focus",
    )
    assert contract.enforce_required_keys is True


def test_book_consistency_contract_requires_downstream_payload_fields() -> None:
    contract = get_task_format_contract(TaskType.BOOK_CONSISTENCY)
    assert contract is not None
    assert contract.required_top_level_keys == (
        "issues",
        "repair_plan",
        "summary",
        "consistency_score",
    )
    assert contract.enforce_required_keys is True


def test_init_claim_extraction_contract_requires_claims_and_coverage() -> None:
    contract = get_task_format_contract(TaskType.EXTRACT_INIT_COHERENCE_CLAIMS)
    assert contract is not None
    assert contract.required_top_level_keys == (
        "claims",
        "coverage_status",
        "unprocessed_source_refs",
    )
    assert "summary" not in contract.required_top_level_keys
    assert contract.enforce_required_keys is True


def test_state_adjudication_contract_rejects_non_enum_severity() -> None:
    try:
        validate_json_output_contract(
            TaskType.ADJUDICATE_STATE_DELTA,
            {
                "candidate_id": "c1",
                "verdict": "reject",
                "severity": "error",
                "rationale": "候选缺少证据。",
            },
        )
    except ValueError as exc:
        assert "$.severity" in str(exc)
    else:
        raise AssertionError("Expected enum validation to reject severity='error'")


def test_init_coherence_llm_contracts_match_response_schemas() -> None:
    for task_type in (
        TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
        TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS,
        TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES,
        TaskType.REPAIR_INIT_ARTIFACT_PATCH,
        TaskType.REFINE_INIT_ARTIFACTS_FROM_SYNOPSIS,
    ):
        contract = get_task_format_contract(task_type)
        schema = get_response_schema(task_type)
        assert contract is not None
        assert schema is not None
        assert set(contract.required_top_level_keys).issubset(schema.model_fields)


def test_extract_canon_contract_matches_prompt_required_top_level_keys() -> None:
    contract = get_task_format_contract(TaskType.EXTRACT_CANON)
    assert contract is not None
    assert contract.required_top_level_keys == (
        "canon_delta",
        "creative_report",
        "chapter_exit_state",
        "character_state_deltas",
        "relationship_deltas",
        "plot_thread_deltas",
        "structured_summary",
    )
    assert contract.enforce_required_keys is True


def test_creative_note_field_rationales_rejects_nested_objects() -> None:
    """field_rationales must be a flat string→string map, not arbitrary deep nesting."""
    from novel_forge.core.format_contracts import _CREATIVE_NOTE_SCHEMA

    # Valid: flat string→string map
    valid_payload = {
        "core_pitch": "test",
        "design_intent": "test",
        "preserved_constraints": [],
        "field_rationales": {"title": "Chosen for clarity", "tone": "Dark to match premise"},
        "risks": [],
        "next_moves": [],
        "anti_drift_check": "ok",
    }
    _validate_json_against_schema(valid_payload, _CREATIVE_NOTE_SCHEMA)

    # Invalid: nested object value
    invalid_payload = {
        "core_pitch": "test",
        "design_intent": "test",
        "preserved_constraints": [],
        "field_rationales": {"title": {"nested": "bad"}},
        "risks": [],
        "next_moves": [],
        "anti_drift_check": "ok",
    }
    try:
        _validate_json_against_schema(invalid_payload, _CREATIVE_NOTE_SCHEMA)
    except ValueError as exc:
        assert "string" in str(exc).lower() or "type" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError for nested object in field_rationales")


def _validate_json_against_schema(payload: dict, schema: dict) -> None:
    """Minimal JSON Schema validator for object type checks."""
    _check_schema(payload, schema, "$")


def _check_schema(value: object, schema: dict, path: str) -> None:
    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path}: expected object, got {type(value).__name__}")
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(f"{path}: missing required property `{key}`")
        props = schema.get("properties", {})
        for k, v in value.items():
            if k in props:
                _check_schema(v, props[k], f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                raise ValueError(f"{path}: unexpected property `{k}`")
            elif "additionalProperties" in schema and isinstance(
                schema["additionalProperties"], dict
            ):
                _check_schema(v, schema["additionalProperties"], f"{path}.{k}")
    elif schema_type == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path}: expected array, got {type(value).__name__}")
        items_schema = schema.get("items")
        if items_schema:
            for i, item in enumerate(value):
                _check_schema(item, items_schema, f"{path}[{i}]")
    elif schema_type == "string":
        if not isinstance(value, str):
            raise ValueError(f"{path}: expected string, got {type(value).__name__}")
    elif schema_type == "integer":
        if not isinstance(value, int):
            raise ValueError(f"{path}: expected integer, got {type(value).__name__}")
    elif schema_type == "number":
        if not isinstance(value, (int, float)):
            raise ValueError(f"{path}: expected number, got {type(value).__name__}")
    elif schema_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{path}: expected boolean, got {type(value).__name__}")


def test_check_continuity_contract_no_vestigial_mode() -> None:
    """mode was a vestigial field — verify it is no longer required."""
    contract = get_task_format_contract(TaskType.CHECK_CONTINUITY)
    assert contract is not None
    assert "mode" not in contract.required_top_level_keys
    assert "continuity_score" in contract.required_top_level_keys
    assert "issues" in contract.required_top_level_keys


def test_enrich_character_contract_has_json_schema_with_proper_types() -> None:
    contract = get_task_format_contract(TaskType.ENRICH_CHARACTER)
    assert contract is not None
    assert contract.output_kind == OutputKind.JSON
    assert contract.required_top_level_keys == ("appearance", "personality", "backstory", "arc")
    assert contract.enforce_required_keys is True
    assert contract.json_schema is not None

    schema = contract.json_schema
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"appearance", "personality", "backstory", "arc"}
    assert schema["additionalProperties"] is False

    props = schema["properties"]
    for field in (
        "name",
        "appearance",
        "personality",
        "backstory",
        "arc",
        "notes",
        "gender",
        "social_status",
        "abilities",
    ):
        assert props[field] == {"type": "string"}, f"{field} should be string type"

    assert props["relationships"]["type"] == "object"
    assert props["relationships"]["additionalProperties"] == {"type": "string"}


def test_enrich_character_schema_validates_correct_payload() -> None:
    payload = {
        "name": "张三",
        "appearance": "身材高大，面容冷峻",
        "personality": "沉默寡言，内心火热",
        "backstory": "出身书香门第，后因变故流落江湖",
        "arc": "从孤僻到学会信任他人",
        "relationships": {"李四": "挚友", "王五": "宿敌"},
        "gender": "男",
        "social_status": "镖局少东家",
        "abilities": "擅长追踪",
        "notes": "注意代词使用",
    }
    validate_json_output_contract(TaskType.ENRICH_CHARACTER, payload)


def test_enrich_character_schema_rejects_type_drift() -> None:
    payload = {
        "appearance": {"hair": "black", "eyes": "brown"},
        "personality": "introverted",
        "backstory": "orphaned young",
        "arc": "growth arc",
    }
    try:
        validate_json_output_contract(TaskType.ENRICH_CHARACTER, payload)
    except ValueError as exc:
        assert "$.appearance: expected string" in str(exc)
    else:
        raise AssertionError("expected ValueError for type drift")


def test_editorial_finding_metadata_accepts_string_values() -> None:
    """metadata field accepts arbitrary string-keyed string values."""
    payload = {
        "summary": "test",
        "findings": [
            {
                "issue_type": "pacing",
                "severity": "medium",
                "chapter_number": 1,
                "summary": "slow start",
                "evidence": ["paragraph 1"],
                "recommendation": "tighten opening",
                "confidence": 0.8,
                "metadata": {"pov": "third", "tense": "past", "location": "chapter_1"},
            }
        ],
        "revision_plan": ["fix pacing"],
        "metrics": {"editorial_score": 7.5},
    }
    validate_json_output_contract(TaskType.CHECK_EDITORIAL, payload)


def test_editorial_finding_metadata_rejects_nested_objects() -> None:
    """metadata field rejects deeply nested objects as values."""
    payload = {
        "summary": "test",
        "findings": [
            {
                "issue_type": "pacing",
                "severity": "medium",
                "chapter_number": 1,
                "summary": "slow start",
                "evidence": ["paragraph 1"],
                "recommendation": "tighten opening",
                "confidence": 0.8,
                "metadata": {"nested": {"deep": {"key": "value"}}},
            }
        ],
        "revision_plan": ["fix pacing"],
        "metrics": {"editorial_score": 7.5},
    }
    try:
        validate_json_output_contract(TaskType.CHECK_EDITORIAL, payload)
    except ValueError as exc:
        assert "$.findings[0].metadata.nested: expected string" in str(exc)
    else:
        raise AssertionError("expected ValueError for nested object in metadata")


def test_macro_guard_adjusted_chapter_goals_schema_has_typed_items() -> None:
    """adjusted_chapter_goals items must have typed properties, not _GENERIC_OBJECT_SCHEMA."""
    from novel_forge.core.format_contracts import (
        _MACRO_GUARD_ADJUSTED_CHAPTER_GOAL_SCHEMA,
        _MACRO_GUARD_ADJUSTMENT_PLAN_SCHEMA,
    )

    # Verify the item schema has explicit properties
    props = _MACRO_GUARD_ADJUSTED_CHAPTER_GOAL_SCHEMA["properties"]
    assert props["chapter_number"] == {"type": "integer"}
    assert props["goal"] == {"type": "string"}
    assert props["notes"] == {"type": "string"}
    assert set(_MACRO_GUARD_ADJUSTED_CHAPTER_GOAL_SCHEMA["required"]) == {"chapter_number", "goal"}
    assert _MACRO_GUARD_ADJUSTED_CHAPTER_GOAL_SCHEMA["additionalProperties"] is False

    # Verify the adjustment plan schema uses the typed item schema
    items_schema = _MACRO_GUARD_ADJUSTMENT_PLAN_SCHEMA["properties"]["adjusted_chapter_goals"][
        "items"
    ]
    assert items_schema is _MACRO_GUARD_ADJUSTED_CHAPTER_GOAL_SCHEMA


def test_macro_guard_adjusted_chapter_goals_validates_correct_payload() -> None:
    """Valid adjusted_chapter_goals items pass schema validation."""
    from novel_forge.core.format_contracts import _MACRO_GUARD_ADJUSTMENT_PLAN_SCHEMA

    payload = {
        "window_size": 3,
        "strategy": "accelerate_plot",
        "target_outline_v": "v2",
        "adjusted_chapter_goals": [
            {"chapter_number": 13, "goal": "Introduce the antagonist"},
            {"chapter_number": 14, "goal": "Resolve subplot B", "notes": "Keep it brief"},
        ],
        "reasoning": "Plot pacing needs adjustment",
    }
    _validate_json_against_schema(payload, _MACRO_GUARD_ADJUSTMENT_PLAN_SCHEMA)


def test_macro_guard_adjusted_chapter_goals_rejects_missing_required_field() -> None:
    """adjusted_chapter_goals items missing 'goal' are rejected."""
    from novel_forge.core.format_contracts import _MACRO_GUARD_ADJUSTMENT_PLAN_SCHEMA

    payload = {
        "window_size": 3,
        "strategy": "accelerate_plot",
        "target_outline_v": "v2",
        "adjusted_chapter_goals": [{"chapter_number": 13}],
        "reasoning": "test",
    }
    try:
        _validate_json_against_schema(payload, _MACRO_GUARD_ADJUSTMENT_PLAN_SCHEMA)
    except ValueError as exc:
        assert "missing required property `goal`" in str(exc)
    else:
        raise AssertionError("expected ValueError for missing goal field")


def test_macro_guard_adjusted_chapter_goals_rejects_type_drift() -> None:
    """adjusted_chapter_goals items with wrong types are rejected."""
    from novel_forge.core.format_contracts import _MACRO_GUARD_ADJUSTMENT_PLAN_SCHEMA

    payload = {
        "window_size": 3,
        "strategy": "accelerate_plot",
        "target_outline_v": "v2",
        "adjusted_chapter_goals": [{"chapter_number": "thirteen", "goal": "test"}],
        "reasoning": "test",
    }
    try:
        _validate_json_against_schema(payload, _MACRO_GUARD_ADJUSTMENT_PLAN_SCHEMA)
    except ValueError as exc:
        assert "expected integer" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError for chapter_number type drift")


def test_macro_guard_adjusted_chapter_goals_rejects_extra_properties() -> None:
    """adjusted_chapter_goals items with unexpected fields are rejected."""
    from novel_forge.core.format_contracts import _MACRO_GUARD_ADJUSTMENT_PLAN_SCHEMA

    payload = {
        "window_size": 3,
        "strategy": "accelerate_plot",
        "target_outline_v": "v2",
        "adjusted_chapter_goals": [
            {"chapter_number": 13, "goal": "test", "unexpected_field": "bad"}
        ],
        "reasoning": "test",
    }
    try:
        _validate_json_against_schema(payload, _MACRO_GUARD_ADJUSTMENT_PLAN_SCHEMA)
    except ValueError as exc:
        assert "unexpected property" in str(exc)
    else:
        raise AssertionError(
            "expected ValueError for extra property in adjusted_chapter_goals item"
        )
