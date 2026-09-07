"""Unified repair metadata for malformed model responses."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.core.format_contracts import (
    ContractMode,
    FormatSchemaIssue,
    OutputKind,
    TaskFormatContract,
    get_task_format_contract,
    resolve_task_format_contract,
)


class FormatRepairBlock(str, Enum):
    """Coarse response-format families used for retry guidance."""

    INITIALIZATION = "initialization_json"
    PLANNING = "planning_json"
    CHAPTER_CONTRACT = "chapter_contract_json"
    CHAPTER_CHECK = "chapter_check_json"
    MEMORY = "memory_json"
    CREATIVE_TOOL = "creative_tool_json"
    GENERIC_JSON = "generic_json"
    TEXT = "text"


_BLOCK_LABELS: dict[FormatRepairBlock, str] = {
    FormatRepairBlock.INITIALIZATION: "初始化/立项 JSON",
    FormatRepairBlock.PLANNING: "规划/大纲 JSON",
    FormatRepairBlock.CHAPTER_CONTRACT: "章节契约 JSON",
    FormatRepairBlock.CHAPTER_CHECK: "章节检查 JSON",
    FormatRepairBlock.MEMORY: "记忆/摘要 JSON",
    FormatRepairBlock.CREATIVE_TOOL: "创意工具 JSON",
    FormatRepairBlock.GENERIC_JSON: "通用 JSON",
    FormatRepairBlock.TEXT: "纯文本",
}

_INITIALIZATION_TASKS = frozenset(
    {
        TaskType.SPEC_ENRICH,
        TaskType.INIT_STORY_BIBLE,
        TaskType.INIT_STORY_CORE_PREMISE,
        TaskType.INIT_STORY_WORLD_RULES,
        TaskType.INIT_STORY_CONTINUITY_RULES,
        TaskType.INIT_STORY_THEMES_AND_SYMBOLS,
        TaskType.INIT_CHARACTER_BIBLE,
        TaskType.INIT_CHARACTER_ROSTER,
        TaskType.INIT_CHARACTER_PROFILE_BATCH,
        TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX,
        TaskType.INIT_CHARACTER_ARC_PLAN,
        TaskType.PROFILE_STYLE,
        TaskType.PROFILE_STRUCTURE,
        TaskType.INIT_ENTITY_REGISTRY,
        TaskType.INIT_NARRATIVE_CONTRACT,
    }
)

_PLANNING_TASKS = frozenset(
    {
        TaskType.BLUEPRINT_ELEMENT_SELECT,
        TaskType.PLAN_OUTLINE,
        TaskType.PLAN_OUTLINE_BATCH,
        TaskType.PLAN_OUTLINE_CONTINUE,
        TaskType.DERIVE_INIT_COHERENCE_PROFILE,
        TaskType.REFINE_INIT_COHERENCE_PROFILE,
        TaskType.INIT_COHERENCE_ONTOLOGY,
        TaskType.INIT_COHERENCE_EXTRACTION_GUIDE,
        TaskType.INIT_COHERENCE_CONFLICT_RULES,
        TaskType.INIT_COHERENCE_PAYOFF_RULES,
        TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
        TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS,
        TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES,
        TaskType.ADJUDICATE_BLUEPRINT_COHERENCE,
        TaskType.ADJUDICATE_OUTLINE_INHERITANCE,
        TaskType.REPAIR_INIT_ARTIFACT_PATCH,
        TaskType.REFINE_INIT_ARTIFACTS_FROM_SYNOPSIS,
        TaskType.PLAN_CHAPTER,
        TaskType.PLAN_CHAPTER_SCENES,
        TaskType.VALIDATE_SCENE_PLAN,
        TaskType.BRIDGE_CHAPTER,
        TaskType.ADJUST_OUTLINE,
        TaskType.POLISH_OUTLINE,
        TaskType.POLISH_SUBPLOT,
    }
)

_CHAPTER_CONTRACT_TASKS = frozenset(
    {
        TaskType.PLAN_CHAPTER_CONTRACTS,
        TaskType.ADJUDICATE_CONTRACT_COHERENCE,
        TaskType.ADJUDICATE_CONTRACT_COMPLETION,
        TaskType.ADJUDICATE_FACT_CONFLICT,
        TaskType.ADJUDICATE_STATE_DELTA,
        TaskType.ADJUDICATE_FINAL_STATE,
    }
)

_CHAPTER_CHECK_TASKS = frozenset(
    {
        TaskType.CHECK_ALIGNMENT,
        TaskType.CHECK_CHAPTER,
        TaskType.CHECK_CONTINUITY,
        TaskType.VALIDATE_CAUSAL,
        TaskType.EXTRACT_CANON,
        TaskType.EXTRACT_CHAPTER_SUMMARY_EXIT,
        TaskType.EXTRACT_CANON_DELTA,
        TaskType.EXTRACT_CREATIVE_REPORT,
        TaskType.EXTRACT_CHARACTER_STATE_DELTAS,
        TaskType.EXTRACT_RELATIONSHIP_DELTAS,
        TaskType.EXTRACT_PLOT_THREAD_DELTAS,
        TaskType.GUARD_CONSTRAINT_CHECK,
        TaskType.MACRO_GUARD_AUDIT,
        TaskType.BOOK_CONSISTENCY,
        TaskType.BOOK_CONSISTENCY_NAMING,
        TaskType.BOOK_CONSISTENCY_TIMELINE,
        TaskType.BOOK_CONSISTENCY_WORLD_RULE,
        TaskType.BOOK_CONSISTENCY_CHARACTER_STATE,
        TaskType.BOOK_CONSISTENCY_PLOT_THREAD,
        TaskType.BOOK_CONSISTENCY_NARRATIVE_DRIFT,
        TaskType.BOOK_CONSISTENCY_VERIFY,
        TaskType.CHECK_EDITORIAL,
        TaskType.BOOK_EDITORIAL_AUDIT,
        TaskType.BOOK_EDITORIAL_STRUCTURE_AUDIT,
        TaskType.BOOK_EDITORIAL_VOICE_AUDIT,
        TaskType.BOOK_EDITORIAL_LANGUAGE_AUDIT,
        TaskType.BOOK_EDITORIAL_THEME_SYMBOL_AUDIT,
        TaskType.BOOK_EDITORIAL_ELEMENT_AUDIT,
        TaskType.EVALUATE,
        TaskType.EVALUATE_READING_POWER,
        TaskType.PATCH_CHAPTER,
        TaskType.REPAIR_SEMANTIC_VERIFY,
    }
)

_MEMORY_TASKS = frozenset(
    {
        TaskType.CONTEXT_COMPRESS,
        TaskType.ADAPTIVE_COMPRESS,
        TaskType.VERIFY_COMPRESSION,
        TaskType.SUMMARIZE_CHAPTER,
        TaskType.SUMMARIZE_VOLUME,
        TaskType.SUMMARIZE_ARC,
        TaskType.SUMMARIZE_SCENE,
        TaskType.EXTRACT_MOTIFS,
        TaskType.CRITIC_CONTINUITY,
        TaskType.CRITIC_CHARACTER,
        TaskType.CRITIC_CAUSAL,
        TaskType.CRITIC_STRENGTHS,
    }
)

_CREATIVE_TOOL_TASKS = frozenset(
    {
        TaskType.GENERATE_CONFIG,
        TaskType.POLISH_CONFIG,
        TaskType.ENRICH_CHARACTER,
        TaskType.ADJUDICATE_CHARACTER_INTRODUCTION,
        TaskType.INTRODUCE_CHARACTER,
    }
)


@dataclass(frozen=True)
class FormatRetryDirective:
    """Retry guidance and telemetry for one malformed response."""

    task_type: TaskType
    block: FormatRepairBlock
    attempt: int
    max_attempts: int
    error_type: str
    error: str
    required_keys: tuple[str, ...]
    instruction: str
    raw_excerpt: str
    contract_id: str = ""
    contract_mode: str = ""
    schema_issues: tuple[FormatSchemaIssue, ...] = ()


def format_repair_block_for_task(task_type: TaskType) -> FormatRepairBlock:
    """Return the response-format family for *task_type*."""
    contract = get_task_format_contract(task_type)
    if contract is not None and contract.output_kind == OutputKind.TEXT:
        return FormatRepairBlock.TEXT
    if task_type in _INITIALIZATION_TASKS:
        return FormatRepairBlock.INITIALIZATION
    if task_type in _CHAPTER_CONTRACT_TASKS:
        return FormatRepairBlock.CHAPTER_CONTRACT
    if task_type in _PLANNING_TASKS:
        return FormatRepairBlock.PLANNING
    if task_type in _CHAPTER_CHECK_TASKS:
        return FormatRepairBlock.CHAPTER_CHECK
    if task_type in _MEMORY_TASKS:
        return FormatRepairBlock.MEMORY
    if task_type in _CREATIVE_TOOL_TASKS:
        return FormatRepairBlock.CREATIVE_TOOL
    return FormatRepairBlock.GENERIC_JSON


def task_expects_json(task_type: TaskType) -> bool:
    """Return True when *task_type* is contractually parsed as JSON."""
    contract = get_task_format_contract(task_type)
    return bool(contract is not None and contract.output_kind == OutputKind.JSON)


def _clean_error_text(error: BaseException) -> str:
    return str(error).strip().strip("'\"")


def _schema_issue_from_schema_message(message: str) -> FormatSchemaIssue:
    path = "$"
    detail = message
    if ":" in message:
        raw_path, detail = message.split(":", 1)
        path = raw_path.strip() or "$"
        detail = detail.strip()
    if "unexpected property `" in detail:
        key = detail.split("unexpected property `", 1)[-1].split("`", 1)[0]
        return FormatSchemaIssue(
            path=f"{path}.{key}" if path != "$" else f"$.{key}",
            issue_type="extra_key",
            expected="allowed property",
            actual=key,
            message=message,
        )
    if "missing required property `" in detail:
        key = detail.split("missing required property `", 1)[-1].split("`", 1)[0]
        return FormatSchemaIssue(
            path=f"{path}.{key}" if path != "$" else f"$.{key}",
            issue_type="missing_key",
            expected="required property",
            actual="missing",
            message=message,
        )
    if detail.startswith("expected one of "):
        return FormatSchemaIssue(
            path=path,
            issue_type="enum_mismatch",
            expected=detail.split(", got ", 1)[0].removeprefix("expected "),
            actual=detail.split(", got ", 1)[-1] if ", got " in detail else "",
            message=message,
        )
    if detail.startswith("expected ") and ", got " in detail:
        expected, actual = detail.removeprefix("expected ").split(", got ", 1)
        return FormatSchemaIssue(
            path=path,
            issue_type="type_mismatch",
            expected=expected,
            actual=actual,
            message=message,
        )
    return FormatSchemaIssue(path=path, issue_type="schema_violation", message=message)


def _schema_issues_from_error(
    error: BaseException,
    *,
    required_keys: tuple[str, ...],
) -> tuple[FormatSchemaIssue, ...]:
    text = _clean_error_text(error)
    issues: list[FormatSchemaIssue] = []
    if text.startswith("Missing required response key(s):"):
        keys = text.split(":", 1)[-1].split(",")
        for key in keys:
            clean = key.strip()
            if not clean:
                continue
            issues.append(
                FormatSchemaIssue(
                    path=f"$.{clean}",
                    issue_type="missing_key",
                    expected="required top-level key",
                    actual="missing",
                    message=f"Missing required response key: {clean}",
                )
            )
    elif text.startswith("Missing required response key:"):
        clean = text.split(":", 1)[-1].strip()
        if clean:
            issues.append(
                FormatSchemaIssue(
                    path=f"$.{clean}",
                    issue_type="missing_key",
                    expected="required top-level key",
                    actual="missing",
                    message=f"Missing required response key: {clean}",
                )
            )
    elif text.startswith("Unexpected response key(s):"):
        keys = text.split(":", 1)[-1].split(",")
        for key in keys:
            clean = key.strip()
            if not clean:
                continue
            issues.append(
                FormatSchemaIssue(
                    path=f"$.{clean}",
                    issue_type="extra_key",
                    expected="allowed top-level key",
                    actual=clean,
                    message=f"Unexpected response key: {clean}",
                )
            )
    elif text.startswith("JSON schema validation failed:"):
        details = text.split(":", 1)[-1].split(";")
        for detail in details:
            clean = detail.strip()
            if clean and not clean.startswith("... +"):
                issues.append(_schema_issue_from_schema_message(clean))
    elif isinstance(error, json.JSONDecodeError):
        issues.append(
            FormatSchemaIssue(
                path="$",
                issue_type="parse_error",
                expected="single valid JSON object",
                actual=f"line {error.lineno} column {error.colno}",
                message=text,
            )
        )

    if issues:
        return tuple(issues)
    return tuple(
        FormatSchemaIssue(
            path=f"$.{key}",
            issue_type="missing_key",
            expected="required top-level key",
            actual="unknown",
            message=f"Required key may be missing: {key}",
        )
        for key in required_keys
    )


def _format_schema_issue_lines(issues: tuple[FormatSchemaIssue, ...]) -> str:
    if not issues:
        return ""
    lines = ["## 结构化错误定位"]
    for issue in issues[:12]:
        detail_parts = [
            f"path=`{issue.path}`",
            f"type=`{issue.issue_type}`",
        ]
        if issue.expected:
            detail_parts.append(f"expected={issue.expected}")
        if issue.actual:
            detail_parts.append(f"actual={issue.actual}")
        if issue.message:
            detail_parts.append(f"message={issue.message}")
        lines.append("- " + "；".join(detail_parts))
    if len(issues) > 12:
        lines.append(f"- ... +{len(issues) - 12} more")
    return "\n".join(lines) + "\n"


def _excerpt(value: Any, *, limit: int = 1200) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) <= limit:
        return text
    head = max(300, int(limit * 0.58))
    tail = max(220, limit - head - 12)
    return f"{text[:head]}\n...\n{text[-tail:]}"


def _required_key_text(
    required_keys: tuple[str, ...],
    contract_mode: ContractMode | None,
) -> str:
    if not required_keys:
        if contract_mode == ContractMode.PARTIAL_OBJECT:
            return "本任务是局部对象；没有全量必填键，只修复本轮新增或修改字段。"
        if contract_mode == ContractMode.PATCH_PLAN:
            return "本任务是补丁计划；按契约输出可应用的修改计划字段。"
        if contract_mode == ContractMode.FRAGMENT_OBJECT:
            return "本任务是片段对象；只输出当前子任务负责的结构片段。"
        return "按原任务 schema 输出所有必要字段。"
    return "顶层必须包含：" + "、".join(f"`{key}`" for key in required_keys) + "。"


def _allowed_key_text(contract: TaskFormatContract | None) -> str:
    if contract is None or not contract.allowed_top_level_keys:
        return ""
    keys = "、".join(f"`{key}`" for key in contract.allowed_top_level_keys)
    return f"- 允许顶层字段：{keys}\n"


def _contract_mode_rules(contract_mode: ContractMode | None) -> list[str]:
    if contract_mode == ContractMode.PARTIAL_OBJECT:
        return [
            "本任务是局部对象：只输出本轮新增或修改的允许字段；不要补全、回显或伪造未修改字段。",
            "如果原始输出只包含部分字段且字段合法，应保留局部语义，不要扩展成完整配置。",
        ]
    if contract_mode == ContractMode.PATCH_PLAN:
        return [
            "本任务是补丁计划：只修复补丁计划 JSON，不要重写完整上游对象或正文。",
            "补丁条目必须保持可应用、可定位；不要把修改说明展开成完整成品。",
        ]
    if contract_mode == ContractMode.FRAGMENT_OBJECT:
        return [
            "本任务是片段对象：只修复当前子任务负责的结构片段，不要补全整份上游 artifact。",
            "片段中的数组和对象要闭合完整，但不要额外生成其他任务负责的顶层字段。",
        ]
    if contract_mode == ContractMode.FULL_OBJECT:
        return ["本任务是完整对象：必须保留契约声明的全部顶层字段。"]
    return []


def _block_specific_rules(block: FormatRepairBlock) -> list[str]:
    common = [
        "只输出一个完整 JSON 对象，首字符必须是 `{`，末字符必须是 `}`。",
        "不要使用 Markdown 代码块、解释文字、注释、Python dict、单引号或省略号。",
        "所有字符串必须使用英文双引号；数组元素和对象字段之间必须使用英文逗号。",
        "字符串内容中的台词、术语或标题不要使用裸英文双引号；应改为中文引号「」或转义为 `\\\"`。",
        '禁止混入 Python 风格分隔片段，例如 `", \'角色名":`、`\', \'角色名":`；对象字段必须修正为 `", "角色名":`。',
        '所有字段名必须完整写成 `"key": value`；禁止 `key": value`、`"key: value`、`key: value` 这类半引号/裸字段名。',
        '数组中的对象必须直接写成 `{...}`；禁止写成 `"{...}` 或 `},"{"key"...`。',
        "禁止把结构字段写进文本字段；若看到 `.field: value` 或 `field=value` 这类尾注，必须还原成对应 JSON key。",
        "如果内容过长，压缩字段内文字，不要截断 JSON 结构，不要省略闭合括号。",
    ]
    if block == FormatRepairBlock.CHAPTER_CONTRACT:
        return [
            *common,
            "`chapter_contracts` 必须是数组；每章一个对象，重复内容要合并精简。",
            "大段条目宁可缩写为短句，也不要连续输出相邻字符串而漏掉逗号。",
        ]
    if block == FormatRepairBlock.PLANNING:
        return [
            *common,
            "章节、大纲、场景数组必须保持数组结构；每个条目独立成对象或字符串。",
            '`subplot_plan[].chapter_events[].depends_on` 必须始终是数组；单个依赖也写成 ["支线名:章节号"]，没有依赖写 []。',
        ]
    if block == FormatRepairBlock.INITIALIZATION:
        return [
            *common,
            "世界观、角色、实体、契约字段保持稳定命名；不要把列表写成自然语言段落。",
        ]
    if block == FormatRepairBlock.MEMORY:
        return [
            *common,
            "摘要和记忆条目可以压缩，但 `items`/`summary`/`motifs` 等字段类型不能漂移。",
        ]
    return common


def _task_specific_rules(task_type: TaskType) -> list[str]:
    if task_type == TaskType.PLAN_OUTLINE:
        return [
            "PLAN_OUTLINE 只输出全书级蓝图字段；禁止输出 `chapter_hooks`，逐章 `expected_hook`/`expected_payoffs` 属于 PLAN_OUTLINE_BATCH/CONTINUE。",
            "如果原始输出因长度截断，优先压缩长描述并保留必填顶层字段，不要保留半截字符串或半截数组。",
        ]
    if task_type in {TaskType.PLAN_OUTLINE_BATCH, TaskType.PLAN_OUTLINE_CONTINUE}:
        return [
            "`chapters[]` 每个对象都必须包含 `chapter_number` 与 `title`；不能用 `goal`、`beats_summary` 或 `main_plot_points` 代替标题字段。",
            "`title` 是章节标题字段，即使标题很短也必须显式输出；不要只输出章节号、目标和节拍。",
            "如果 `beats_summary` 被误拆成单独数组对象，应把它移回同一个 `chapter_number` 对象内部，不要新增或丢弃章节。",
        ]
    if task_type in {
        TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
        TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS,
    }:
        return [
            "claims 抽取任务只输出最终 JSON；禁止输出分析过程、推理过程、输入复述或对 hash/路径的解释。",
            "`chunk_id`、hash、`schema_version`、`created_at`、`source_path` 是定位元数据，不能作为 claim、角色、线索或正文证据。",
            "claim 文本直接使用中文字符，禁止输出长串 `\\uXXXX` 转义；若原始输出循环复述并截断，改为 `{\"claims\":[],\"summary\":\"模型输出重复截断，未抽取可用 claims\"}`。",
            "同一个 claim 对象内每个键名只能出现一次；若上一轮重复输出 `evidence`，必须保留原有非空证据并删除重复空键，不得用空字符串覆盖。",
            "没有可抽取事实时输出 `{\"claims\":[],\"summary\":\"本分块无可抽取事实\"}`，不要用自然语言说明原因。",
        ]
    if task_type == TaskType.INIT_ENTITY_REGISTRY:
        return [
            "`entities` 必须是数组；每个实体对象都必须包含非空 `entity_id` 与 `name`。",
            "`entity_id` 使用稳定英文/拼音 ID；不能把实体名当成对象键，也不能省略 ID 字段。",
        ]
    if task_type == TaskType.EXTRACT_RELATIONSHIP_DELTAS:
        return [
            "`characters` 字段必须放在每个 delta 的 `relationship` 对象内部，即 `relationship_deltas[].relationship.characters`；绝对不能放在 delta 顶层。",
            "正确结构：{\"pair_id\":\"A__B\",\"change_summary\":\"...\",\"relationship\":{\"characters\":[\"A\",\"B\"],\"public_status\":\"...\",\"trust\":0.5,\"tension\":0.5}}。",
            "如果原始输出中 `characters` 在 delta 顶层而 `relationship` 内没有，必须把它移入 `relationship` 对象内部。",
            "`relationship.characters` 必须是恰好两个角色名的数组；每个名称必须来自已知角色列表或正文明确出现的名字。",
        ]
    return []


def build_format_retry_directive(
    *,
    task_type: TaskType,
    attempt: int,
    max_attempts: int,
    error: BaseException,
    required_keys: tuple[str, ...],
    raw_content: str,
    raw_excerpt_limit: int = 6000,
    context: dict[str, Any] | None = None,
    contract_mode_override: ContractMode | None = None,
    schema_issues: tuple[FormatSchemaIssue, ...] = (),
) -> FormatRetryDirective:
    """Build retry instructions and diagnostics for a malformed response."""
    block = format_repair_block_for_task(task_type)
    contract = resolve_task_format_contract(task_type, context)
    contract_mode = contract_mode_override or (contract.effective_contract_mode if contract else None)
    contract_mode_text = contract_mode.value if contract_mode else ""
    error_type = type(error).__name__
    error_text = _clean_error_text(error)
    resolved_schema_issues = schema_issues or _schema_issues_from_error(
        error,
        required_keys=required_keys,
    )
    schema_issue_text = _format_schema_issue_lines(resolved_schema_issues)
    rules = "\n".join(
        f"{index}. {rule}"
        for index, rule in enumerate(
            [
                *_block_specific_rules(block),
                *_contract_mode_rules(contract_mode),
                *_task_specific_rules(task_type),
            ],
            1,
        )
    )
    required_text = _required_key_text(required_keys, contract_mode)
    allowed_text = (
        _allowed_key_text(contract)
        if contract is not None and contract_mode == contract.effective_contract_mode
        else ""
    )
    raw_excerpt = _excerpt(raw_content, limit=max(1200, int(raw_excerpt_limit or 6000)))
    instruction = (
        "上一次模型输出没有通过系统格式解析，请重新生成同一任务的最终答案。\n\n"
        f"## 失败信息\n"
        f"- 任务：`{task_type.value}`（{_BLOCK_LABELS[block]}）\n"
        f"- 契约模式：`{contract_mode_text or 'unknown'}`\n"
        f"- 第 {attempt}/{max_attempts} 次尝试失败\n"
        f"- 错误：{error_type}: {error_text}\n"
        f"- {required_text}\n"
        f"{allowed_text}\n"
        f"{schema_issue_text}"
        "## 必须修正\n"
        f"{rules}\n\n"
        "## 上一次错误输出原文（超过字符预算时保留头尾；仅供定位格式错误，不要复述）\n"
        f"{raw_excerpt}"
    )
    return FormatRetryDirective(
        task_type=task_type,
        block=block,
        attempt=attempt,
        max_attempts=max_attempts,
        error_type=error_type,
        error=error_text,
        required_keys=required_keys,
        instruction=instruction,
        raw_excerpt=raw_excerpt,
        contract_id=contract.contract_id if contract else "",
        contract_mode=contract_mode_text,
        schema_issues=resolved_schema_issues,
    )


def build_format_error_event(
    directive: FormatRetryDirective,
    *,
    raw_content: str,
    finish_reason: str | None,
    model_id: str | None,
    max_tokens: int,
    next_max_tokens: int | None = None,
    retry_temperature: float | None = None,
) -> dict[str, Any]:
    """Return a log/UI payload for one format error."""
    schema_issues = [issue.as_dict() for issue in directive.schema_issues]
    missing_keys = [
        issue.path.removeprefix("$.")
        for issue in directive.schema_issues
        if issue.issue_type == "missing_key" and issue.expected == "required top-level key"
    ]
    if not missing_keys:
        missing_keys = list(directive.required_keys)
    extra_keys = [
        issue.path.removeprefix("$.")
        for issue in directive.schema_issues
        if issue.issue_type == "extra_key" and issue.expected == "allowed top-level key"
    ]
    schema_errors = [issue.message for issue in directive.schema_issues if issue.message]
    if not schema_errors:
        schema_errors = [directive.error]
    payload: dict[str, Any] = {
        "task": directive.task_type.value,
        "format_block": directive.block.value,
        "contract_id": directive.contract_id,
        "contract_mode": directive.contract_mode,
        "attempt": directive.attempt,
        "max_attempts": directive.max_attempts,
        "max_retries": max(0, directive.max_attempts - 1),
        "error_type": directive.error_type,
        "error": directive.error,
        "required_keys": list(directive.required_keys),
        "missing_keys": missing_keys,
        "extra_keys": extra_keys,
        "schema_errors": schema_errors,
        "schema_issues": schema_issues,
        "raw_excerpt": directive.raw_excerpt,
        "raw_content": raw_content,
        "finish_reason": finish_reason or "",
        "model_id": model_id or "",
        "max_tokens": max_tokens,
    }
    if next_max_tokens is not None:
        payload["next_max_tokens"] = next_max_tokens
    if retry_temperature is not None:
        payload["retry_temperature"] = retry_temperature
    return payload


def compact_event_for_json(value: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe compact copy for previews or tests."""
    payload = json.loads(json.dumps(value, ensure_ascii=False, default=str))
    return dict(payload) if isinstance(payload, dict) else {}


__all__ = [
    "FormatRepairBlock",
    "FormatRetryDirective",
    "build_format_error_event",
    "build_format_retry_directive",
    "compact_event_for_json",
    "format_repair_block_for_task",
    "task_expects_json",
]
