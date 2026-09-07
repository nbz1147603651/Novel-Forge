"""Format-repair orchestration for structured model responses."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.core.format_contracts import (
    ContractMode,
    OutputKind,
    resolve_task_format_contract,
)
from novel_forge.core.parsing.format_repair import format_repair_block_for_task
from novel_forge.core.response_repair.json_blocks import repair_missing_colon_delimiters
from novel_forge.core.utils.json import safe_parse_json, strip_markdown_fences


class RepairSource(str, Enum):
    """Where a successful parsed response came from."""

    STRICT = "strict_json"
    LOCAL = "local_repair"
    LLM = "llm_repair"


class RepairRisk(str, Enum):
    """How trustworthy a repaired candidate appears before task validation."""

    NONE = "none"
    SAFE = "safe"
    LOSSY = "lossy"
    UNSAFE = "unsafe"


@dataclass(frozen=True)
class FormatRepairContext:
    """Inputs needed by the generic format-repair layer."""

    task_type: TaskType
    raw_content: str
    error: BaseException | None
    required_keys: tuple[str, ...] = ()
    attempt: int = 1
    max_attempts: int = 1
    finish_reason: str | None = None
    model_id: str | None = None
    max_tokens: int | None = None
    raw_char_limit: int = 60000
    contract_context: dict[str, Any] | None = None
    include_contract_required_keys: bool = True
    contract_mode_override: ContractMode | None = None


@dataclass(frozen=True)
class RepairCandidate:
    """One parsed candidate produced by a repair strategy."""

    data: dict[str, Any]
    source: RepairSource
    strategy: str
    risk: RepairRisk
    repaired_text: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FormatRepairResult:
    """Result returned by :func:`repair_json_object_response`."""

    success: bool
    source: RepairSource | None = None
    data: dict[str, Any] | None = None
    repaired_text: str | None = None
    repair_action: str = ""
    strategy: str = ""
    risk: RepairRisk = RepairRisk.NONE
    error: BaseException | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LocalRepairStrategy:
    """A pluggable local repair strategy."""

    name: str
    parse: Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class TaskSpecificRepairStrategy:
    """A task-scoped local repair strategy with explicit risk declaration."""

    name: str
    supported_task_types: frozenset[TaskType]
    risk: RepairRisk
    build: Callable[[FormatRepairContext], RepairCandidate | None]
    preconditions: tuple[str, ...] = ()


RepairValidator = Callable[[dict[str, Any]], None]
LocalRepairAcceptor = Callable[[RepairCandidate], bool]
RawRequiredKeyLossClassifier = Callable[[RepairCandidate, tuple[str, ...]], BaseException | None]
LLMRepairCallable = Callable[[str], Awaitable[str]]

_CHAPTER_NUMBER_RE = re.compile(r'"chapter_number"\s*:\s*(\d+)')
_CHAPTER_COLLECTION_KEYS = ("chapters", "chapter_contracts", "adjusted_chapters")
_CHAPTER_COLLECTION_REPAIR_TASKS = frozenset(
    {
        TaskType.PLAN_CHAPTER_CONTRACTS,
        TaskType.PLAN_OUTLINE_BATCH,
        TaskType.PLAN_OUTLINE_CONTINUE,
    }
)
_OUTLINE_BATCH_TASKS = frozenset(
    {
        TaskType.PLAN_OUTLINE_BATCH,
        TaskType.PLAN_OUTLINE_CONTINUE,
    }
)
_PLAN_OUTLINE_TOP_LEVEL_KEYS = frozenset(
    {
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
    }
)
_PLAN_OUTLINE_ARRAY_TOP_LEVEL_KEYS = frozenset(
    {
        "volumes",
        "narrative_phases",
        "key_turning_points",
        "character_arcs",
        "subplot_plan",
        "suspense_schedule",
        "emotional_arcs",
        "causal_chains",
        "subplot_collisions",
        "subversion_points",
        "chapter_rhythm_curve",
    }
)
_INIT_CLAIMS_TASKS = frozenset(
    {
        TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
        TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS,
    }
)
_JSON_STRING_TOKEN = r'"(?:\\.|[^"\\])*"'
_OUTLINE_SCALAR_BEATS_SUMMARY_RE = re.compile(
    r'("beats_summary"\s*:\s*)'
    rf"({_JSON_STRING_TOKEN})"
    rf"((?:\s*,\s*{_JSON_STRING_TOKEN})*)"
    r'(\s*,\s*"main_plot_points"\s*:)',
    re.DOTALL,
)
_INIT_CLAIM_DUPLICATE_KEY_STRATEGY = "init_claim_duplicate_key_preserve_non_empty"
_DUPLICATE_VALUE_EXCERPT_LIMIT = 120
_INIT_CLAIM_DUPLICATE_NESTED_MAP_KEYS = frozenset({"character_knowledge_coverage"})


@dataclass(frozen=True)
class _JSONPairObject:
    """JSON object preserving duplicate keys from object_pairs_hook."""

    pairs: tuple[tuple[str, Any], ...]


def _strict_json_object(text: str) -> dict[str, Any]:
    parsed = json.loads(strip_markdown_fences(text), strict=False)
    if not isinstance(parsed, dict):
        raise TypeError("Model response JSON must be an object")
    return parsed


def _pair_json_object(text: str) -> _JSONPairObject:
    parsed = json.loads(
        strip_markdown_fences(text),
        strict=False,
        object_pairs_hook=lambda pairs: _JSONPairObject(tuple(pairs)),
    )
    if not isinstance(parsed, _JSONPairObject):
        raise TypeError("Model response JSON must be an object")
    return parsed


def _safe_parse_json_object(text: str) -> dict[str, Any]:
    parsed = safe_parse_json(text)
    if not isinstance(parsed, dict):
        raise TypeError("Model response JSON must be an object")
    return parsed


def _json_path(parent: str, key: str) -> str:
    if not parent:
        return key
    return f"{parent}.{key}"


def _is_empty_duplicate_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, _JSONPairObject):
        return not value.pairs
    if isinstance(value, (list, dict)):
        return not value
    return False


def _duplicate_value_excerpt(value: Any) -> str:
    text = json.dumps(_plain_json_value(value), ensure_ascii=False, default=str)
    if len(text) <= _DUPLICATE_VALUE_EXCERPT_LIMIT:
        return text
    return f"{text[:_DUPLICATE_VALUE_EXCERPT_LIMIT]}..."


def _plain_json_value(value: Any) -> Any:
    if isinstance(value, _JSONPairObject):
        result: dict[str, Any] = {}
        for key, item in value.pairs:
            result[key] = _plain_json_value(item)
        return result
    if isinstance(value, list):
        return [_plain_json_value(item) for item in value]
    return value


def _convert_json_value_rejecting_duplicates(value: Any, path: str) -> Any:
    if isinstance(value, _JSONPairObject):
        result: dict[str, Any] = {}
        for key, item in value.pairs:
            child_path = _json_path(path, key)
            if key in result:
                raise ValueError(
                    f"Duplicate key outside supported init claim object at {child_path}"
                )
            result[key] = _convert_json_value_rejecting_duplicates(item, child_path)
        return result
    if isinstance(value, list):
        return [
            _convert_json_value_rejecting_duplicates(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    return value


def _missing_colon_then_safe_parse(text: str) -> dict[str, Any]:
    """Apply missing-colon repair first, then run the full safe_parse_json pipeline."""
    repaired = repair_missing_colon_delimiters(strip_markdown_fences(text))
    parsed = safe_parse_json(repaired)
    if not isinstance(parsed, dict):
        raise TypeError("Model response JSON must be an object")
    return parsed


LOCAL_JSON_REPAIR_STRATEGIES: tuple[LocalRepairStrategy, ...] = (
    LocalRepairStrategy("missing_colon_delimiter", _missing_colon_then_safe_parse),
    LocalRepairStrategy("safe_parse_json", _safe_parse_json_object),
)


def _chapter_numbers_from_raw(raw_content: str) -> set[int]:
    return {int(match.group(1)) for match in _CHAPTER_NUMBER_RE.finditer(raw_content or "")}


def _chapter_numbers_from_data(data: dict[str, Any]) -> set[int]:
    numbers: set[int] = set()
    for key in _CHAPTER_COLLECTION_KEYS:
        items = data.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            value = item.get("chapter_number")
            if value is None:
                continue
            try:
                numbers.add(int(value))
            except (TypeError, ValueError):
                continue
    return numbers


def _chapter_numbers_from_contract_context(context: FormatRepairContext) -> set[int]:
    if context.task_type != TaskType.PLAN_CHAPTER_CONTRACTS:
        return set()
    contract_context = (
        context.contract_context if isinstance(context.contract_context, dict) else {}
    )
    outline = contract_context.get("outline")
    if not isinstance(outline, dict):
        return set()
    batch = outline.get("contract_batch")
    if isinstance(batch, dict):
        raw_numbers = batch.get("chapter_numbers")
        if isinstance(raw_numbers, list):
            numbers: set[int] = set()
            for item in raw_numbers:
                try:
                    numbers.add(int(item))
                except (TypeError, ValueError):
                    continue
            if numbers:
                return numbers
    chapters = outline.get("chapters")
    if not isinstance(chapters, list):
        return set()
    numbers = set()
    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue
        value = chapter.get("chapter_number")
        if value is None:
            continue
        try:
            numbers.add(int(value))
        except (TypeError, ValueError):
            continue
    return numbers


def _can_backfill_partial_chapter_contract_repair(
    context: FormatRepairContext,
    *,
    raw_numbers: set[int],
    parsed_numbers: set[int],
) -> bool:
    if context.task_type != TaskType.PLAN_CHAPTER_CONTRACTS or context.finish_reason == "length":
        return False
    if not raw_numbers or not parsed_numbers:
        return False
    expected_numbers = _chapter_numbers_from_contract_context(context)
    if not expected_numbers:
        return False
    return raw_numbers.issubset(expected_numbers) and parsed_numbers.issubset(expected_numbers)


def _chapter_contract_repair_comparison_numbers(
    context: FormatRepairContext,
    *,
    raw_numbers: set[int],
    parsed_numbers: set[int],
) -> tuple[set[int], set[int]]:
    """Compare only the requested chapter-contract batch when that scope is known."""

    expected_numbers = _chapter_numbers_from_contract_context(context)
    if context.task_type != TaskType.PLAN_CHAPTER_CONTRACTS or not expected_numbers:
        return raw_numbers, parsed_numbers
    scoped_raw_numbers = raw_numbers & expected_numbers
    scoped_parsed_numbers = parsed_numbers & expected_numbers
    if scoped_raw_numbers or scoped_parsed_numbers:
        return scoped_raw_numbers, scoped_parsed_numbers
    return raw_numbers, parsed_numbers


def _classify_local_repair_risk(
    *,
    context: FormatRepairContext,
    data: dict[str, Any],
) -> RepairRisk:
    if context.task_type in _CHAPTER_COLLECTION_REPAIR_TASKS:
        raw_numbers = _chapter_numbers_from_raw(context.raw_content)
        parsed_numbers = _chapter_numbers_from_data(data)
        compare_raw_numbers, compare_parsed_numbers = _chapter_contract_repair_comparison_numbers(
            context,
            raw_numbers=raw_numbers,
            parsed_numbers=parsed_numbers,
        )
        if compare_raw_numbers and not compare_raw_numbers.issubset(compare_parsed_numbers):
            if _can_backfill_partial_chapter_contract_repair(
                context,
                raw_numbers=compare_raw_numbers,
                parsed_numbers=compare_parsed_numbers,
            ):
                return RepairRisk.LOSSY
            return RepairRisk.UNSAFE
    if context.finish_reason == "length":
        return RepairRisk.LOSSY
    return RepairRisk.SAFE


def _format_required_keys(context: FormatRepairContext) -> str:
    contract = resolve_task_format_contract(context.task_type, context.contract_context)
    keys: list[str] = []
    contract_keys = (
        contract.required_top_level_keys
        if contract is not None and context.include_contract_required_keys
        else ()
    )
    for key in (*context.required_keys, *contract_keys):
        clean = str(key or "").strip()
        if clean and clean not in keys:
            keys.append(clean)
    if not keys:
        contract_mode = context.contract_mode_override or (
            contract.effective_contract_mode if contract else None
        )
        if contract_mode == ContractMode.PARTIAL_OBJECT:
            return "本任务是局部对象；无全量必填键，只需保留本轮新增或修改字段。"
        if contract_mode == ContractMode.PATCH_PLAN:
            return "本任务是补丁计划；按原输出修复可应用的修改计划字段。"
        if contract_mode == ContractMode.FRAGMENT_OBJECT:
            return "本任务是片段对象；只需修复当前子任务负责的结构片段。"
        return "无额外顶层必填键；仍需保持原任务约定的 JSON 结构。"
    return "顶层必须包含：" + "、".join(f"`{key}`" for key in keys) + "。"


def _contract_mode_repair_rule(context: FormatRepairContext) -> str:
    contract = resolve_task_format_contract(context.task_type, context.contract_context)
    contract_mode = context.contract_mode_override or (
        contract.effective_contract_mode if contract else None
    )
    if contract_mode is None:
        return ""
    if contract_mode == ContractMode.PARTIAL_OBJECT:
        return (
            f"- 契约模式：`{contract_mode.value}`。只修复局部字段 JSON；"
            "不要补全、回显或发明未修改字段。\n"
        )
    if contract_mode == ContractMode.PATCH_PLAN:
        return (
            f"- 契约模式：`{contract_mode.value}`。只修复补丁计划结构；"
            "不要重写完整上游对象或正文。\n"
        )
    if contract_mode == ContractMode.FRAGMENT_OBJECT:
        return f"- 契约模式：`{contract_mode.value}`。只修复当前片段；不要补全整份上游 artifact。\n"
    if contract_mode == ContractMode.FULL_OBJECT:
        return f"- 契约模式：`{contract_mode.value}`。必须保留完整对象的全部契约字段。\n"
    return f"- 契约模式：`{contract_mode.value}`。\n"


def _task_specific_llm_rules(context: FormatRepairContext) -> str:
    if context.task_type == TaskType.PLAN_OUTLINE:
        return (
            "9. PLAN_OUTLINE 只保留全书级蓝图字段；删除 `chapter_hooks`，"
            "逐章 `expected_hook`/`expected_payoffs` 不属于本任务。\n"
            "10. 如果原始输出因长度截断，压缩长描述并闭合现有结构；"
            "不要保留半截字符串、半截对象或半截数组。\n"
            "11. 如果角色弧光、支线或悬念字段被写成 description/notes 尾注，"
            "必须还原为对应 JSON key，禁止保留 `.field: value` 或 `field=value` 文本尾注。\n"
            "12. 如果片段要求同时输出 `character_arcs` 与 `emotional_arcs`，"
            "二者必须是同一个根对象下的两个顶层字段；"
            "`character_arcs` 数组必须先用 `]` 闭合，再写 `emotional_arcs`，"
            "禁止把 `emotional_arcs` 写成 `character_arcs[]` 内部字段或数组元素。\n"
        )
    if context.task_type in {TaskType.PLAN_OUTLINE_BATCH, TaskType.PLAN_OUTLINE_CONTINUE}:
        return (
            "9. PLAN_OUTLINE_BATCH/CONTINUE 的 `chapters[]` 每个对象都必须包含"
            " `chapter_number` 与 `title`；不能用 `goal`、`beats_summary` 或"
            " `main_plot_points` 代替标题字段。\n"
            "10. 如果 `beats_summary` 被误拆成相邻的单独对象，应把它移回同一个"
            " `chapter_number` 对象内部，不要新增章节、丢弃章节或改写剧情。\n"
        )
    if context.task_type in _INIT_CLAIMS_TASKS:
        return (
            "9. claims 抽取任务必须输出短句中文 claims，禁止把中文转成大段 `\\uXXXX` 转义串。\n"
            "10. 如果原始输出已经循环复述并因长度截断，请输出"
            ' `{"claims":[],"summary":"模型输出重复截断，未抽取可用 claims"}`，'
            "不要尝试保留半截 claim。\n"
            "11. claims 抽取任务新增认知化字段是关键约束："
            " `cognitive_subjects`, `cognitive_object`, `cognitive_level`, `action_level`, `reader_awareness`,"
            " `character_knowledge_coverage`, `cognitive_chapter`, `public_reveal_chapter`, `foreshadow_chapters`"
            " 必须完整存在。\n"
            "12. 不得为认知字段补强语义或伪造章节；只能修 JSON 结构和非法枚举。"
            "若缺失/为空且无法可靠修复，"
            "请仅保留原内容并只修 JSON 结构，让上游 schema 校验继续触发重试。\n"
            "13. 同一个 claim 对象内每个键名只能出现一次；如果上一轮重复输出 `evidence`，"
            "必须保留原有非空证据并删除重复空键，不得用空字符串覆盖。\n"
        )
    return ""


def _raw_excerpt(context: FormatRepairContext) -> str:
    text = str(context.raw_content or "").replace("\r\n", "\n").replace("\r", "\n")
    limit = max(1000, int(context.raw_char_limit or 60000))
    if len(text) <= limit:
        return text
    head = max(600, int(limit * 0.62))
    tail = max(300, limit - head - 20)
    return f"{text[:head]}\n\n...（中间内容已截断，仅用于格式修复）...\n\n{text[-tail:]}"


def build_llm_format_repair_prompt(context: FormatRepairContext) -> str:
    """Build a dedicated prompt for the LLM format-repair module."""
    contract = resolve_task_format_contract(context.task_type, context.contract_context)
    output_kind = contract.output_kind.value if contract else OutputKind.JSON.value
    block = format_repair_block_for_task(context.task_type)
    error = context.error
    error_type = type(error).__name__ if error is not None else "UnknownFormatError"
    error_text = str(error or "解析或结构校验失败")
    max_tokens_note = f"{context.max_tokens}" if context.max_tokens is not None else "未知"
    return (
        "你是 Novel Forge 的专用格式修复模块，只修复机器可解析格式，不创作新内容。\n\n"
        "## 修复目标\n"
        f"- 任务：`{context.task_type.value}`\n"
        f"- 格式族：`{block.value}`\n"
        f"- 输出类型：`{output_kind}`\n"
        f"- 当前尝试：{context.attempt}/{context.max_attempts}\n"
        f"- 原模型：{context.model_id or '未知'}\n"
        f"- 原 max_tokens：{max_tokens_note}\n"
        f"- finish_reason：{context.finish_reason or '未知'}\n"
        f"- 错误：{error_type}: {error_text}\n"
        f"{_contract_mode_repair_rule(context)}"
        f"- {_format_required_keys(context)}\n\n"
        "## 硬规则\n"
        "1. 只输出一个完整 JSON 对象，首字符必须是 `{`，末字符必须是 `}`。\n"
        "2. 不要输出 Markdown 代码块、解释文字、注释、道歉或分析过程。\n"
        "3. 不得新增剧情事实、不得改写语义、不得删除可恢复字段；只修复括号、逗号、引号、字段归属和顶层结构。\n"
        "4. 如果某个字段被错误写到 `]}` 之后，但语义上属于最后一个数组对象，请把它移回该对象内部。\n"
        "5. 如果字段名轻微漂移但可从上下文判断为契约字段，请改回正确字段名；无法判断时保留原字段。\n"
        '6. 字段名必须完整写成 `"key": value`；如果发现 `key": value`、`"key: value` 或 `key: value`，只补引号和冒号，不改语义。\n'
        '7. 如果发现 Python 风格伪分隔片段，如 `\', \'角色名":` 或 `", \'角色名":`，必须改成标准 JSON 的 `", "角色名":`。\n'
        '8. 如果对象成员被写成裸字符串（如 `"未向某人透露"}` 或 `,"仍待确认",`），必须保留该字符串并补成 `"未向某人透露": true`，不要原样留下无冒号成员。\n'
        "9. 数组和对象必须使用标准 JSON：英文双引号、英文逗号、无尾随逗号。\n"
        '10. 字符串内容中的台词、术语或标题不要保留裸英文双引号；应改为中文引号「」或转义为 `\\"`。\n\n'
        f"{_task_specific_llm_rules(context)}"
        "## 原始错误输出\n"
        "```text\n"
        f"{_raw_excerpt(context)}\n"
        "```"
    )


def _validate_candidate(
    candidate: RepairCandidate,
    validator: RepairValidator,
) -> FormatRepairResult:
    try:
        validator(candidate.data)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        diagnostics = dict(candidate.diagnostics)
        schema_issues = getattr(validator, "last_schema_issues", ())
        if schema_issues:
            diagnostics["schema_issues"] = [
                issue.as_dict() if hasattr(issue, "as_dict") else dict(issue)
                for issue in schema_issues
            ]
        return FormatRepairResult(
            success=False,
            source=candidate.source,
            strategy=candidate.strategy,
            risk=candidate.risk,
            error=exc,
            diagnostics=diagnostics,
        )
    return FormatRepairResult(
        success=True,
        source=candidate.source,
        data=candidate.data,
        repaired_text=candidate.repaired_text,
        repair_action=(
            "llm_format_repair" if candidate.source == RepairSource.LLM else "local_parse_repair"
        ),
        strategy=candidate.strategy,
        risk=candidate.risk,
        diagnostics=dict(candidate.diagnostics),
    )


def _max_consecutive_unit_repeats(text: str, *, max_unit_size: int = 18) -> int:
    compact = re.sub(r"[\s，,；;。.!！?？、：:「」“”\"'（）()《》\\[\\]{}]+", "", text)
    if len(compact) < 24:
        return 1
    best = 1
    max_unit = min(max_unit_size, max(1, len(compact) // 2))
    for unit_size in range(2, max_unit + 1):
        index = 0
        while index + unit_size * 2 <= len(compact):
            unit = compact[index : index + unit_size]
            if not unit.strip():
                index += 1
                continue
            repeats = 1
            cursor = index + unit_size
            while compact[cursor : cursor + unit_size] == unit:
                repeats += 1
                cursor += unit_size
            best = max(best, repeats)
            index = cursor if repeats > 1 else index + 1
    return best


def _max_consecutive_unicode_escape_token_repeats(
    text: str,
    *,
    max_unit_size: int = 8,
) -> int:
    tokens = re.findall(r"\\u[0-9a-fA-F]{4}", text)
    if len(tokens) < 8:
        return 1
    best = 1
    max_unit = min(max_unit_size, max(1, len(tokens) // 2))
    for unit_size in range(1, max_unit + 1):
        index = 0
        while index + unit_size * 2 <= len(tokens):
            unit = tokens[index : index + unit_size]
            repeats = 1
            cursor = index + unit_size
            while tokens[cursor : cursor + unit_size] == unit:
                repeats += 1
                cursor += unit_size
            best = max(best, repeats)
            index = cursor if repeats > 1 else index + 1
    return best


def _looks_like_degenerate_claim_output(context: FormatRepairContext) -> bool:
    if context.task_type not in _INIT_CLAIMS_TASKS:
        return False
    raw = strip_markdown_fences(context.raw_content or "")
    if '"claims"' not in raw and "'claims'" not in raw:
        return False
    if len(raw) < 500 or context.finish_reason != "length":
        return False
    return (
        _max_consecutive_unit_repeats(raw) >= 24
        or _max_consecutive_unicode_escape_token_repeats(raw) >= 12
    )


def _resolve_duplicate_claim_key(
    *,
    path: str,
    values: list[Any],
) -> tuple[Any, str]:
    plain_values = [_plain_json_value(value) for value in values]
    first_value = plain_values[0]
    if all(value == first_value for value in plain_values[1:]):
        return first_value, "deduped_identical"

    non_empty = [
        plain_values[index]
        for index, value in enumerate(values)
        if not _is_empty_duplicate_value(value)
    ]
    if len(non_empty) == 1:
        return non_empty[0], "preserved_non_empty"
    if not non_empty:
        return first_value, "deduped_identical"
    raise ValueError(f"Duplicate key conflict at {path}")


def _merge_init_claim_object(
    claim_obj: _JSONPairObject,
    *,
    claim_index: int,
    duplicate_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    grouped: dict[str, list[Any]] = {}
    order: list[str] = []
    claim_path = f"claims[{claim_index}]"
    for key, value in claim_obj.pairs:
        if key not in grouped:
            order.append(key)
            grouped[key] = []
        grouped[key].append(value)

    merged: dict[str, Any] = {}
    pending_duplicate_actions: list[dict[str, Any]] = []
    for key in order:
        key_path = _json_path(claim_path, key)
        values = grouped[key]
        if len(values) == 1:
            merged[key] = _convert_init_claim_field_value(
                key,
                values[0],
                key_path,
                duplicate_actions=pending_duplicate_actions,
            )
            continue

        converted_values = [
            _convert_init_claim_field_value(
                key,
                value,
                key_path,
                duplicate_actions=pending_duplicate_actions,
            )
            for value in values
        ]
        kept_value, resolution_action = _resolve_duplicate_claim_key(
            path=key_path,
            values=converted_values,
        )
        merged[key] = kept_value
        pending_duplicate_actions.append(
            {
                "path": key_path,
                "action": resolution_action,
                "kept_value_excerpt": _duplicate_value_excerpt(kept_value),
                "discarded_value_excerpts": [
                    _duplicate_value_excerpt(value)
                    for value in converted_values
                    if _plain_json_value(value) != kept_value
                ],
            }
        )

    claim_id = str(merged.get("claim_id") or "").strip()
    for duplicate_action in pending_duplicate_actions:
        if claim_id:
            duplicate_action["claim_id"] = claim_id
        duplicate_actions.append(duplicate_action)
    return merged


def _convert_init_claim_field_value(
    key: str,
    value: Any,
    path: str,
    *,
    duplicate_actions: list[dict[str, Any]],
) -> Any:
    if key in _INIT_CLAIM_DUPLICATE_NESTED_MAP_KEYS and isinstance(value, _JSONPairObject):
        return _merge_duplicate_key_mapping(
            value,
            path=path,
            duplicate_actions=duplicate_actions,
        )
    return _convert_json_value_rejecting_duplicates(value, path)


def _merge_duplicate_key_mapping(
    mapping: _JSONPairObject,
    *,
    path: str,
    duplicate_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    grouped: dict[str, list[Any]] = {}
    order: list[str] = []
    for key, value in mapping.pairs:
        if key not in grouped:
            order.append(key)
            grouped[key] = []
        grouped[key].append(value)

    merged: dict[str, Any] = {}
    for key in order:
        key_path = _json_path(path, key)
        values = grouped[key]
        if len(values) == 1:
            merged[key] = _convert_json_value_rejecting_duplicates(values[0], key_path)
            continue
        converted_values = [
            _convert_json_value_rejecting_duplicates(value, key_path) for value in values
        ]
        kept_value, action = _resolve_duplicate_claim_key(
            path=key_path,
            values=converted_values,
        )
        merged[key] = kept_value
        duplicate_actions.append(
            {
                "path": key_path,
                "action": action,
                "kept_value_excerpt": _duplicate_value_excerpt(kept_value),
                "discarded_value_excerpts": [
                    _duplicate_value_excerpt(value)
                    for value in converted_values
                    if _plain_json_value(value) != kept_value
                ],
            }
        )
    return merged


def _candidate_init_claim_duplicate_key_repair(
    context: FormatRepairContext,
) -> RepairCandidate | None:
    if context.task_type not in _INIT_CLAIMS_TASKS:
        return None
    raw = strip_markdown_fences(context.raw_content or "")
    if '"claims"' not in raw and "'claims'" not in raw:
        return None

    root = _pair_json_object(raw)
    data: dict[str, Any] = {}
    duplicate_actions: list[dict[str, Any]] = []
    for key, value in root.pairs:
        if key in data:
            raise ValueError(f"Duplicate key outside supported init claim object at {key}")
        if key != "claims":
            data[key] = _convert_json_value_rejecting_duplicates(value, key)
            continue

        if not isinstance(value, list):
            data[key] = _convert_json_value_rejecting_duplicates(value, key)
            continue
        claims: list[Any] = []
        for index, item in enumerate(value):
            item_path = f"claims[{index}]"
            if isinstance(item, _JSONPairObject):
                claims.append(
                    _merge_init_claim_object(
                        item,
                        claim_index=index,
                        duplicate_actions=duplicate_actions,
                    )
                )
                continue
            claims.append(_convert_json_value_rejecting_duplicates(item, item_path))
        data[key] = claims

    if not duplicate_actions:
        return None

    claim_ids: list[str] = []
    for action in duplicate_actions:
        claim_id = str(action.get("claim_id") or "").strip()
        if claim_id and claim_id not in claim_ids:
            claim_ids.append(claim_id)
    diagnostics: dict[str, Any] = {
        "duplicate_key_locations": [action["path"] for action in duplicate_actions],
        "duplicate_key_actions": duplicate_actions,
    }
    if claim_ids:
        diagnostics["duplicate_key_claim_ids"] = claim_ids
    return RepairCandidate(
        data=data,
        source=RepairSource.LOCAL,
        strategy=_INIT_CLAIM_DUPLICATE_KEY_STRATEGY,
        risk=RepairRisk.SAFE,
        repaired_text=json.dumps(data, ensure_ascii=False),
        diagnostics=diagnostics,
    )


def _candidate_state_delta_salvage(context: FormatRepairContext) -> RepairCandidate | None:
    """Recover complete candidate objects when one later object breaks the array.

    Candidate extraction is evidence-only and followed by independent adjudication,
    so preserving the complete candidates is better than blocking archive because a
    later optional candidate nested ``evidence`` in the wrong object.
    """
    if context.task_type != TaskType.EXTRACT_CANDIDATE_STATE_DELTAS:
        return None
    raw = strip_markdown_fences(context.raw_content or "")
    match = re.search(r'"candidates"\s*:\s*\[', raw)
    if match is None:
        return None
    body_start = match.end()
    body_end = raw.rfind("]")
    if body_end <= body_start:
        body_end = len(raw)
    body = raw[body_start:body_end]
    parts = re.split(r'\},\s*(?=\{"candidate_id"\s*:)', body)
    candidates: list[dict[str, Any]] = []
    for part in parts:
        candidate_text = part.strip().rstrip(",")
        if not candidate_text:
            continue
        start = candidate_text.find('{"candidate_id"')
        if start > 0:
            candidate_text = candidate_text[start:]
        if not candidate_text.startswith("{"):
            continue
        if not candidate_text.endswith("}"):
            candidate_text = f"{candidate_text}}}"
        try:
            parsed = json.loads(candidate_text, strict=False)
        except json.JSONDecodeError:
            try:
                parsed = safe_parse_json(candidate_text)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        if not isinstance(parsed, dict):
            continue
        if "delta_type" not in parsed or "summary" not in parsed or "evidence" not in parsed:
            continue
        evidence_items: list[dict[str, Any]] = []
        for item in list(parsed.get("evidence") or []):
            if not isinstance(item, dict):
                continue
            quote = str(item.get("quote") or "").strip()
            if not quote:
                continue
            evidence_item: dict[str, Any] = {"quote": quote}
            paragraph_index = item.get("paragraph_index")
            if isinstance(paragraph_index, int):
                evidence_item["paragraph_index"] = paragraph_index
            evidence_items.append(evidence_item)
        if not evidence_items:
            continue
        parsed["evidence"] = evidence_items
        proposed_delta = parsed.get("proposed_delta")
        if isinstance(proposed_delta, dict):
            proposed_delta = dict(proposed_delta)
            proposed_delta.pop("evidence", None)
            parsed["proposed_delta"] = proposed_delta
        candidates.append(parsed)
    if not candidates:
        return None
    return RepairCandidate(
        data={"candidates": candidates},
        source=RepairSource.LOCAL,
        strategy="candidate_state_delta_salvage",
        risk=RepairRisk.LOSSY,
    )


def _candidate_outline_batch_scalar_beats_repair(
    context: FormatRepairContext,
) -> RepairCandidate | None:
    """Recover outline batches where ``beats_summary`` was emitted as adjacent strings."""
    if context.task_type not in _OUTLINE_BATCH_TASKS:
        return None
    raw = strip_markdown_fences(context.raw_content or "")
    if '"beats_summary"' not in raw or '"main_plot_points"' not in raw:
        return None

    changed = False

    def _wrap_scalar_beats(match: re.Match[str]) -> str:
        nonlocal changed
        changed = True
        return f"{match.group(1)}[{match.group(2)}{match.group(3)}]{match.group(4)}"

    repaired = _OUTLINE_SCALAR_BEATS_SUMMARY_RE.sub(_wrap_scalar_beats, raw)
    if not changed or repaired == raw:
        return None

    try:
        data = json.loads(repaired, strict=False)
    except json.JSONDecodeError:
        try:
            data = safe_parse_json(repaired)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
    if not isinstance(data, dict):
        return None
    risk = _classify_local_repair_risk(context=context, data=data)
    if risk == RepairRisk.UNSAFE:
        return None
    return RepairCandidate(
        data=data,
        source=RepairSource.LOCAL,
        strategy="outline_batch_scalar_beats_summary",
        risk=risk,
        repaired_text=repaired,
    )


def _plan_outline_fragment_required_keys(context: FormatRepairContext) -> tuple[str, ...]:
    keys: list[str] = []
    for key in context.required_keys:
        clean = str(key or "").strip()
        if clean and clean not in keys:
            keys.append(clean)

    contract_context = (
        context.contract_context if isinstance(context.contract_context, dict) else {}
    )
    request = contract_context.get("blueprint_fragment_request")
    if isinstance(request, dict):
        for key in request.get("required_keys") or ():
            clean = str(key or "").strip()
            if clean and clean not in keys:
                keys.append(clean)
    return tuple(keys)


def _peek_json_key_name(text: str, pos: int) -> str | None:
    length = len(text)
    j = pos
    while j < length and text[j] in " \t\r\n":
        j += 1
    if j >= length or text[j] != '"':
        return None

    j += 1
    key_start = j
    escape_next = False
    while j < length:
        ch = text[j]
        if escape_next:
            escape_next = False
            j += 1
            continue
        if ch == "\\":
            escape_next = True
            j += 1
            continue
        if ch == '"':
            break
        j += 1
    if j >= length:
        return None

    key = text[key_start:j]
    j += 1
    while j < length and text[j] in " \t\r\n":
        j += 1
    if j < length and text[j] == ":":
        return key
    return None


def _insert_missing_plan_outline_array_closer_once(
    text: str,
    *,
    array_key: str,
    sibling_keys: set[str],
) -> str:
    pattern = re.compile(rf'"{re.escape(array_key)}"\s*:\s*\[')
    for match in pattern.finditer(text):
        opener = text.rfind("[", match.start(), match.end())
        if opener < 0:
            continue

        stack: list[str] = ["["]
        in_string = False
        escape_next = False
        index = opener + 1
        length = len(text)

        while index < length:
            ch = text[index]

            if escape_next:
                escape_next = False
                index += 1
                continue
            if ch == "\\":
                escape_next = True
                index += 1
                continue
            if ch == '"':
                in_string = not in_string
                index += 1
                continue
            if in_string:
                index += 1
                continue

            if ch in "{[":
                stack.append(ch)
                index += 1
                continue
            if ch in "}]":
                if stack:
                    top = stack[-1]
                    if (top == "{" and ch == "}") or (top == "[" and ch == "]"):
                        stack.pop()
                if not stack:
                    break
                index += 1
                continue
            if ch in {",", "，"} and stack == ["["]:
                sibling_key = _peek_json_key_name(text, index + 1)
                if sibling_key in sibling_keys:
                    return f"{text[:index]}]{text[index:]}"

            index += 1
    return text


def _repair_plan_outline_missing_array_closer_before_sibling(
    text: str,
    *,
    required_keys: tuple[str, ...],
) -> str:
    required = {key for key in required_keys if key in _PLAN_OUTLINE_TOP_LEVEL_KEYS}
    sibling_pool = required or set(_PLAN_OUTLINE_TOP_LEVEL_KEYS)
    repaired = text
    for _ in range(4):
        next_repaired = repaired
        for array_key in _PLAN_OUTLINE_ARRAY_TOP_LEVEL_KEYS & sibling_pool:
            sibling_keys = set(sibling_pool)
            sibling_keys.discard(array_key)
            if not sibling_keys:
                continue
            next_repaired = _insert_missing_plan_outline_array_closer_once(
                next_repaired,
                array_key=array_key,
                sibling_keys=sibling_keys,
            )
            if next_repaired != repaired:
                break
        if next_repaired == repaired:
            return repaired
        repaired = next_repaired
        try:
            json.loads(repaired, strict=False)
            return repaired
        except json.JSONDecodeError:
            continue
    return repaired


def _candidate_plan_outline_fragment_array_sibling_repair(
    context: FormatRepairContext,
) -> RepairCandidate | None:
    if context.task_type != TaskType.PLAN_OUTLINE:
        return None

    required_keys = _plan_outline_fragment_required_keys(context)
    if len(required_keys) < 2 or not any(
        key in _PLAN_OUTLINE_ARRAY_TOP_LEVEL_KEYS for key in required_keys
    ):
        return None

    raw = strip_markdown_fences(context.raw_content or "")
    if not raw or not any(f'"{key}"' in raw for key in required_keys):
        return None

    repaired = _repair_plan_outline_missing_array_closer_before_sibling(
        raw,
        required_keys=required_keys,
    )
    if repaired == raw:
        return None

    try:
        data = json.loads(repaired, strict=False)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return RepairCandidate(
        data=data,
        source=RepairSource.LOCAL,
        strategy="plan_outline_fragment_array_sibling_repair",
        risk=RepairRisk.SAFE,
        repaired_text=repaired,
        diagnostics={"required_keys": list(required_keys)},
    )


def _candidate_degenerate_claims_empty_fallback(
    context: FormatRepairContext,
) -> RepairCandidate | None:
    if not _looks_like_degenerate_claim_output(context):
        return None
    return RepairCandidate(
        data={
            "claims": [],
            "coverage_status": "uncertain",
            "unprocessed_source_refs": ["model_output_truncated"],
            "summary": "模型 claims 输出重复并截断，按无可用 claims 处理。",
        },
        source=RepairSource.LOCAL,
        strategy="degenerate_claims_empty_fallback",
        risk=RepairRisk.SAFE,
    )


_TASK_SPECIFIC_REPAIR_STRATEGIES: tuple[TaskSpecificRepairStrategy, ...] = (
    TaskSpecificRepairStrategy(
        name=_INIT_CLAIM_DUPLICATE_KEY_STRATEGY,
        supported_task_types=_INIT_CLAIMS_TASKS,
        risk=RepairRisk.SAFE,
        build=_candidate_init_claim_duplicate_key_repair,
        preconditions=("duplicate key inside claims object",),
    ),
    TaskSpecificRepairStrategy(
        name="plan_outline_fragment_array_sibling_repair",
        supported_task_types=frozenset({TaskType.PLAN_OUTLINE}),
        risk=RepairRisk.SAFE,
        build=_candidate_plan_outline_fragment_array_sibling_repair,
        preconditions=("fragment mode", "sibling required key leaked inside prior array"),
    ),
    TaskSpecificRepairStrategy(
        name="candidate_state_delta_salvage",
        supported_task_types=frozenset({TaskType.EXTRACT_CANDIDATE_STATE_DELTAS}),
        risk=RepairRisk.LOSSY,
        build=_candidate_state_delta_salvage,
        preconditions=(
            "candidate extraction only",
            "complete candidate objects remain recoverable",
        ),
    ),
    TaskSpecificRepairStrategy(
        name="outline_batch_scalar_beats_summary",
        supported_task_types=_OUTLINE_BATCH_TASKS,
        risk=RepairRisk.LOSSY,
        build=_candidate_outline_batch_scalar_beats_repair,
        preconditions=("beats_summary adjacent scalar strings",),
    ),
    TaskSpecificRepairStrategy(
        name="degenerate_claims_empty_fallback",
        supported_task_types=_INIT_CLAIMS_TASKS,
        risk=RepairRisk.SAFE,
        build=_candidate_degenerate_claims_empty_fallback,
        preconditions=("finish_reason=length", "repeated claim loop"),
    ),
)


def _task_specific_local_fallback(
    context: FormatRepairContext,
) -> RepairCandidate | None:
    for strategy in _TASK_SPECIFIC_REPAIR_STRATEGIES:
        if context.task_type not in strategy.supported_task_types:
            continue
        try:
            candidate = strategy.build(context)
        except (json.JSONDecodeError, TypeError):
            candidate = None
        if candidate is None:
            continue
        diagnostics = dict(candidate.diagnostics)
        diagnostics.setdefault("strategy_declared_risk", strategy.risk.value)
        if strategy.preconditions:
            diagnostics.setdefault("strategy_preconditions", list(strategy.preconditions))
        return RepairCandidate(
            data=candidate.data,
            source=candidate.source,
            strategy=candidate.strategy,
            risk=candidate.risk,
            repaired_text=candidate.repaired_text,
            diagnostics=diagnostics,
        )
    return None


def _raw_required_keys_missing_from_candidate(
    context: FormatRepairContext,
    data: dict[str, Any],
) -> list[str]:
    contract = resolve_task_format_contract(context.task_type, context.contract_context)
    keys: list[str] = []
    for key in context.required_keys:
        clean = str(key or "").strip()
        if clean and clean not in keys:
            keys.append(clean)
    if (
        contract is not None
        and context.include_contract_required_keys
        and contract.output_kind == OutputKind.JSON
    ):
        for key in contract.required_top_level_keys:
            clean = str(key or "").strip()
            if clean and clean not in keys:
                keys.append(clean)

    raw = context.raw_content or ""
    missing: list[str] = []
    for key in keys:
        if key in data:
            continue
        if f'"{key}"' in raw or f"'{key}'" in raw:
            missing.append(key)
    return missing


def _generic_lossy_repair_allowed(context: FormatRepairContext) -> bool:
    if context.finish_reason == "length":
        return True
    # A transport can fail after one or more complete chapter-contract rows
    # have already streamed. Risk classification only marks this candidate
    # LOSSY when the requested batch scope is known and the missing rows can be
    # deterministically backfilled from the contract scaffold. The caller's
    # policy still validates the parsed rows and explicitly accepts the repair
    # below; no other task receives this exception.
    return (
        context.finish_reason == "stream_error_partial"
        and context.task_type == TaskType.PLAN_CHAPTER_CONTRACTS
        and bool(_chapter_numbers_from_contract_context(context))
    )


async def repair_json_object_response(
    context: FormatRepairContext,
    *,
    validator: RepairValidator,
    accept_local_repair: LocalRepairAcceptor | None = None,
    classify_raw_required_key_loss: RawRequiredKeyLossClassifier | None = None,
    llm_repair: LLMRepairCallable | None = None,
) -> FormatRepairResult:
    """Parse and repair a JSON-object model response.

    The orchestration order is intentionally conservative:
    strict JSON parse → local deterministic strategies → optional dedicated LLM repair.
    The caller supplies task validation so this module stays independent of
    pipeline-specific normalizers and schemas.
    """
    diagnostics: dict[str, Any] = {}
    strict_error: BaseException | None = None
    task_specific_error: BaseException | None = None

    try:
        strict_data = _strict_json_object(context.raw_content)
    except (json.JSONDecodeError, TypeError) as exc:
        strict_error = exc
        diagnostics["strict_error"] = f"{type(exc).__name__}: {exc}"
    else:
        strict_result = _validate_candidate(
            RepairCandidate(
                data=strict_data,
                source=RepairSource.STRICT,
                strategy="json.loads",
                risk=RepairRisk.NONE,
            ),
            validator,
        )
        if strict_result.success:
            try:
                duplicate_candidate = _candidate_init_claim_duplicate_key_repair(context)
            except ValueError as exc:
                task_specific_error = exc
                strict_error = exc
                diagnostics["task_specific_local_fallback_error"] = f"{type(exc).__name__}: {exc}"
            except (json.JSONDecodeError, TypeError):
                return strict_result
            else:
                if duplicate_candidate is None:
                    return strict_result
                duplicate_result = _validate_candidate(duplicate_candidate, validator)
                if duplicate_result.success:
                    merged_diagnostics = dict(diagnostics)
                    merged_diagnostics.update(duplicate_result.diagnostics)
                    return FormatRepairResult(
                        success=True,
                        source=duplicate_result.source,
                        data=duplicate_result.data,
                        repaired_text=duplicate_result.repaired_text,
                        repair_action=duplicate_result.repair_action,
                        strategy=duplicate_result.strategy,
                        risk=duplicate_result.risk,
                        diagnostics=merged_diagnostics,
                    )
                strict_error = duplicate_result.error
                diagnostics[f"local_{duplicate_candidate.strategy}_validation_error"] = (
                    f"{type(duplicate_result.error).__name__}: {duplicate_result.error}"
                )
        if strict_result.success:
            if task_specific_error is None and strict_error is None:
                return strict_result
        else:
            strict_error = strict_result.error
            diagnostics["strict_validation_error"] = (
                f"{type(strict_result.error).__name__}: {strict_result.error}"
            )

    last_error: BaseException | None = task_specific_error or strict_error or context.error
    fallback_candidate: RepairCandidate | None = None
    if task_specific_error is None:
        try:
            fallback_candidate = _task_specific_local_fallback(context)
        except ValueError as exc:
            task_specific_error = exc
            last_error = exc
            diagnostics["task_specific_local_fallback_error"] = f"{type(exc).__name__}: {exc}"
        except (json.JSONDecodeError, TypeError):
            fallback_candidate = None
    if fallback_candidate is not None:
        fallback_result = _validate_candidate(fallback_candidate, validator)
        if fallback_result.success:
            merged_diagnostics = dict(diagnostics)
            merged_diagnostics.update(fallback_result.diagnostics)
            return FormatRepairResult(
                success=True,
                source=fallback_result.source,
                data=fallback_result.data,
                repaired_text=fallback_result.repaired_text,
                repair_action=fallback_result.repair_action,
                strategy=fallback_result.strategy,
                risk=fallback_result.risk,
                diagnostics=merged_diagnostics,
            )
        last_error = fallback_result.error
        diagnostics[f"local_{fallback_candidate.strategy}_validation_error"] = (
            f"{type(fallback_result.error).__name__}: {fallback_result.error}"
        )

    local_strategies = () if task_specific_error is not None else LOCAL_JSON_REPAIR_STRATEGIES
    for strategy in local_strategies:
        try:
            local_data = strategy.parse(context.raw_content)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            last_error = exc
            diagnostics[f"local_{strategy.name}_error"] = f"{type(exc).__name__}: {exc}"
            continue

        candidate = RepairCandidate(
            data=local_data,
            source=RepairSource.LOCAL,
            strategy=strategy.name,
            risk=_classify_local_repair_risk(context=context, data=local_data),
        )
        missing_raw_keys = _raw_required_keys_missing_from_candidate(context, local_data)
        if missing_raw_keys:
            diagnostics[f"local_{strategy.name}_rejected"] = "raw_required_key_loss:" + ",".join(
                missing_raw_keys
            )
            classified_error = None
            if classify_raw_required_key_loss is not None:
                classified_error = classify_raw_required_key_loss(
                    candidate,
                    tuple(missing_raw_keys),
                )
            last_error = classified_error or ValueError(
                "Local JSON repair dropped required key(s) present in raw output: "
                + ", ".join(missing_raw_keys)
            )
            continue
        if candidate.risk == RepairRisk.LOSSY and not _generic_lossy_repair_allowed(context):
            diagnostics[f"local_{strategy.name}_rejected"] = "lossy_repair_not_allowed"
            last_error = ValueError("Lossy local JSON repair is not allowed for this finish_reason")
            continue
        local_result = _validate_candidate(candidate, validator)
        if not local_result.success:
            last_error = local_result.error
            diagnostics[f"local_{strategy.name}_validation_error"] = (
                f"{type(local_result.error).__name__}: {local_result.error}"
            )
            continue

        if candidate.risk == RepairRisk.UNSAFE:
            diagnostics[f"local_{strategy.name}_rejected"] = "unsafe_structural_loss"
            last_error = ValueError("Local JSON repair lost structural content")
            continue
        if accept_local_repair is not None and not accept_local_repair(candidate):
            diagnostics[f"local_{strategy.name}_rejected"] = "caller_policy"
            last_error = ValueError("Local JSON repair rejected by caller policy")
            continue
        merged_diagnostics = dict(diagnostics)
        merged_diagnostics.update(local_result.diagnostics)
        return FormatRepairResult(
            success=True,
            source=local_result.source,
            data=local_result.data,
            repaired_text=local_result.repaired_text,
            repair_action=local_result.repair_action,
            strategy=local_result.strategy,
            risk=local_result.risk,
            diagnostics=merged_diagnostics,
        )

    diagnostics["local_repair_strategy_miss"] = True

    if llm_repair is not None:
        prompt = build_llm_format_repair_prompt(
            FormatRepairContext(
                task_type=context.task_type,
                raw_content=context.raw_content,
                error=last_error or context.error,
                required_keys=context.required_keys,
                attempt=context.attempt,
                max_attempts=context.max_attempts,
                finish_reason=context.finish_reason,
                model_id=context.model_id,
                max_tokens=context.max_tokens,
                raw_char_limit=context.raw_char_limit,
                contract_context=context.contract_context,
                include_contract_required_keys=context.include_contract_required_keys,
                contract_mode_override=context.contract_mode_override,
            )
        )
        try:
            repaired_text = await llm_repair(prompt)
            llm_data = _strict_json_object(repaired_text)
        except Exception as exc:
            last_error = (
                exc
                if isinstance(exc, (json.JSONDecodeError, KeyError, TypeError, ValueError))
                else ValueError(f"LLM format repair failed: {type(exc).__name__}: {exc}")
            )
            diagnostics["llm_repair_error"] = f"{type(exc).__name__}: {exc}"
        else:
            llm_candidate = RepairCandidate(
                data=llm_data,
                source=RepairSource.LLM,
                strategy="dedicated_llm",
                risk=RepairRisk.SAFE,
                repaired_text=repaired_text,
            )
            llm_result = _validate_candidate(llm_candidate, validator)
            if llm_result.success:
                merged_diagnostics = dict(diagnostics)
                merged_diagnostics.update(llm_result.diagnostics)
                return FormatRepairResult(
                    success=True,
                    source=llm_result.source,
                    data=llm_result.data,
                    repaired_text=llm_result.repaired_text,
                    repair_action=llm_result.repair_action,
                    strategy=llm_result.strategy,
                    risk=llm_result.risk,
                    diagnostics=merged_diagnostics,
                )
            last_error = llm_result.error
            diagnostics["llm_repair_validation_error"] = (
                f"{type(llm_result.error).__name__}: {llm_result.error}"
            )

    return FormatRepairResult(
        success=False,
        error=last_error or context.error or ValueError("JSON response repair failed"),
        diagnostics=diagnostics,
    )
