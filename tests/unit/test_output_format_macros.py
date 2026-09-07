"""Tests for schema-first prompt output contracts."""

from __future__ import annotations

from novel_forge.common.constants import TaskType
from novel_forge.core.format_contracts import (
    OutputKind,
    render_prompt_contract_block,
    resolve_task_format_contract,
)
from novel_forge.prompts.builder import PromptBuilder


def test_json_contract_renderer_declares_required_and_allowed_keys() -> None:
    block = render_prompt_contract_block(TaskType.BLUEPRINT_ELEMENT_SELECT)

    assert "## 统一格式契约（系统注入）" in block
    assert "`required_ids`" in block
    assert "`extension_selection`" in block
    assert "顶层只允许字段" in block

    contract = resolve_task_format_contract(TaskType.BLUEPRINT_ELEMENT_SELECT)
    assert contract is not None
    assert contract.json_schema is not None
    assert contract.json_schema["additionalProperties"] is False


def test_text_contract_renderer_declares_runtime_prose_guards() -> None:
    block = render_prompt_contract_block(TaskType.DRAFT_CHAPTER)

    assert "## 统一格式契约（系统注入）" in block
    assert "禁止 JSON" in block
    assert "直接从第一句叙事正文开始" in block
    assert "scene_intent" in block


def test_builder_passes_native_json_schema_to_model_request() -> None:
    request = PromptBuilder().build(
        TaskType.BEATS,
        {
            "spec": {
                "genre": "悬疑",
                "theme": "真相",
                "tone": "冷峻",
                "length_target": 3000,
                "language": "zh",
                "characters_hint": "",
                "world_hint": "",
                "extra_instructions": "",
            }
        },
    )
    contract = resolve_task_format_contract(TaskType.BEATS)

    assert contract is not None
    assert contract.output_kind == OutputKind.JSON
    assert request.response_json_schema == contract.json_schema
    assert request.response_schema_name == contract.contract_id.replace(":", "_")
    assert request.response_schema_strict is True
