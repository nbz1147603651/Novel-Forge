"""Contract-mode aware format-repair guidance tests."""

from __future__ import annotations

from novel_forge.core.constants import TaskType
from novel_forge.core.format_contracts import ContractMode
from novel_forge.core.parsing.format_repair import (
    build_format_error_event,
    build_format_retry_directive,
)
from novel_forge.core.response_repair.orchestrator import (
    FormatRepairContext,
    build_llm_format_repair_prompt,
)


def test_format_retry_directive_uses_partial_contract_language() -> None:
    directive = build_format_retry_directive(
        task_type=TaskType.POLISH_CONFIG,
        attempt=1,
        max_attempts=2,
        error=ValueError("missing full config"),
        required_keys=(),
        raw_content='{"patch": [{"field": "world_hint"}]}',
        context={"mode": "long"},
    )

    assert directive.contract_mode == "partial_object"
    assert "契约模式：`partial_object`" in directive.instruction
    assert "本任务是局部对象" in directive.instruction
    assert "不要补全、回显或伪造未修改字段" in directive.instruction

    event = build_format_error_event(
        directive,
        raw_content='{"patch": [{"field": "world_hint"}]}',
        finish_reason=None,
        model_id="mock-model",
        max_tokens=1024,
    )
    assert event["contract_mode"] == "partial_object"


def test_format_retry_directive_honors_fragment_override() -> None:
    directive = build_format_retry_directive(
        task_type=TaskType.PLAN_OUTLINE,
        attempt=1,
        max_attempts=2,
        error=ValueError("missing fragment comma"),
        required_keys=("subplot_plan",),
        raw_content='{"subplot_plan": [',
        contract_mode_override=ContractMode.FRAGMENT_OBJECT,
    )

    assert directive.contract_mode == "fragment_object"
    assert "契约模式：`fragment_object`" in directive.instruction
    assert "顶层必须包含：`subplot_plan`" in directive.instruction
    assert "必须保留契约声明的全部顶层字段" not in directive.instruction


def test_format_retry_directive_includes_schema_issues_in_prompt_and_event() -> None:
    directive = build_format_retry_directive(
        task_type=TaskType.PLAN_OUTLINE,
        attempt=1,
        max_attempts=2,
        error=KeyError("Missing required response key(s): emotional_arcs"),
        required_keys=("character_arcs", "emotional_arcs"),
        raw_content='{"character_arcs":[]}',
        contract_mode_override=ContractMode.FRAGMENT_OBJECT,
    )

    assert directive.schema_issues
    assert directive.schema_issues[0].path == "$.emotional_arcs"
    assert "结构化错误定位" in directive.instruction
    assert "path=`$.emotional_arcs`" in directive.instruction

    event = build_format_error_event(
        directive,
        raw_content='{"character_arcs":[]}',
        finish_reason="stop",
        model_id="mock-model",
        max_tokens=1024,
    )

    assert event["schema_issues"][0]["path"] == "$.emotional_arcs"
    assert event["missing_keys"] == ["emotional_arcs"]


def test_format_retry_directive_warns_claim_extraction_against_analysis_preamble() -> None:
    directive = build_format_retry_directive(
        task_type=TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
        attempt=1,
        max_attempts=3,
        error=ValueError("Expecting value: line 1 column 1"),
        required_keys=("claims",),
        raw_content="然后，我需要从这些数据中提取符合要求的claims。",
    )

    assert "claims 抽取任务只输出最终 JSON" in directive.instruction
    assert "禁止输出分析过程" in directive.instruction
    assert "`chunk_id`、hash" in directive.instruction


def test_llm_format_repair_prompt_uses_patch_plan_language() -> None:
    prompt = build_llm_format_repair_prompt(
        FormatRepairContext(
            task_type=TaskType.PATCH_CHAPTER,
            raw_content='{"patches": [{"original": "A", "replacement": "B"}',
            error=ValueError("truncated JSON"),
            required_keys=("patches",),
        )
    )

    assert "契约模式：`patch_plan`" in prompt
    assert "只修复补丁计划结构" in prompt
    assert "不要重写完整上游对象或正文" in prompt


def test_llm_format_repair_prompt_honors_explicit_fragment_override() -> None:
    prompt = build_llm_format_repair_prompt(
        FormatRepairContext(
            task_type=TaskType.PLAN_OUTLINE,
            raw_content='{"subplot_plan": [',
            error=ValueError("truncated JSON"),
            required_keys=("subplot_plan",),
            include_contract_required_keys=False,
            contract_mode_override=ContractMode.FRAGMENT_OBJECT,
        )
    )

    assert "契约模式：`fragment_object`" in prompt
    assert "顶层必须包含：`subplot_plan`" in prompt
    assert "`synopsis`" not in prompt
    assert "`volumes`" not in prompt
