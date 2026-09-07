"""AI-powered creative configuration generation via LLM.

Calls the model gateway (same infrastructure as other pipeline steps)
to generate complete short-form or long-form creative configurations.
The user provides an optional brief hint; the LLM expands it into a
full JSON configuration with theme/premise, characters, world-building,
conflict, opening/ending style, etc.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novel_forge.app_service.preset_manager import preset_template
from novel_forge.core.constants import TaskType
from novel_forge.core.format_contracts import validate_json_output_contract
from novel_forge.core.parsing.parse_utils import safe_parse_json
from novel_forge.core.parsing.response_schemas import validate_response_schema
from novel_forge.gateway.router import ModelRouter
from novel_forge.obs.logger import get_logger
from novel_forge.persistence.polish_history import PolishHistoryRecorder
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.prompts.context_types import validate_generate_config_context
from novel_forge.prompts.packs import prompt_locale_for_language
from novel_forge.workspace.runtime import RuntimeServices, create_runtime_services

_log = get_logger("app_service.ai_generate")

_MAX_PARSE_RETRIES = 2
_CONFIG_TASK_TYPES = {TaskType.GENERATE_CONFIG, TaskType.POLISH_CONFIG}


async def _shutdown_owned_runtime(
    runtime: RuntimeServices,
    *,
    owned: bool,
    source: str,
) -> None:
    if not owned:
        return
    try:
        await runtime.shutdown()
    except Exception as exc:
        _log.debug("%s_runtime_shutdown_failed | error=%s", source, exc)


def _is_partial_config_context(context: dict[str, Any]) -> bool:
    return bool(context.get("allow_partial_config_output"))


def _patch_items_to_dict(items: Any) -> dict[str, Any]:
    if not isinstance(items, list):
        return {}
    converted: dict[str, Any] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        if len(item) == 1:
            key, value = next(iter(item.items()))
            converted[str(key)] = value
            continue
        field_name = str(
            item.get("field") or item.get("key") or item.get("name") or item.get("field_name") or ""
        ).strip()
        if not field_name:
            continue
        for value_key in ("value", "new_value", "content", "text", "after"):
            if value_key in item:
                converted[field_name] = item[value_key]
                break
    return converted


def _partial_config_allowed_keys(context: dict[str, Any]) -> set[str]:
    mode = str(context.get("mode") or "short").strip().lower()
    editable = _normalize_focus_fields(context.get("editable_config_fields") or [], mode)
    if editable:
        return set(editable) | _AI_METADATA_OUTPUT_FIELDS
    return _field_names_for_mode(mode) | _AI_METADATA_OUTPUT_FIELDS


def _coerce_config_response_payload(raw: Any, context: dict[str, Any]) -> Any:
    """Accept common patch-style payloads and drop fields outside the edit scope."""
    if not _is_partial_config_context(context):
        return raw

    if isinstance(raw, list):
        raw = _patch_items_to_dict(raw)
    if not isinstance(raw, dict):
        return raw

    data = dict(raw)
    for envelope_key in ("config", "result", "patch", "changes", "updates", "fields"):
        nested = data.get(envelope_key)
        if isinstance(nested, dict):
            merged = {
                key: value for key, value in data.items() if key in _AI_METADATA_OUTPUT_FIELDS
            }
            merged.update(nested)
            data = merged
            break
        nested_patch = _patch_items_to_dict(nested)
        if nested_patch:
            merged = {
                key: value for key, value in data.items() if key in _AI_METADATA_OUTPUT_FIELDS
            }
            merged.update(nested_patch)
            data = merged
            break

    allowed = _partial_config_allowed_keys(context)
    return {key: value for key, value in data.items() if key in allowed}


async def _call_and_parse(
    router: ModelRouter,
    request: Any,
    *,
    task_type: TaskType,
    context: dict[str, Any],
    label: str = "",
) -> dict[str, Any]:
    """Route *request*, parse JSON, and validate the task contract."""
    last_err: Exception | None = None
    for attempt in range(1, _MAX_PARSE_RETRIES + 1):
        response = await router.route(request)
        try:
            raw = safe_parse_json(response.content)
            if task_type in _CONFIG_TASK_TYPES:
                raw = _coerce_config_response_payload(raw, context)
            if not isinstance(raw, dict):
                raise ValueError(f"LLM 返回的不是 JSON 对象: {type(raw).__name__}")
            if not (task_type in _CONFIG_TASK_TYPES and _is_partial_config_context(context)):
                validate_response_schema(raw, task_type)
            validate_json_output_contract(task_type, raw, context=context)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            last_err = exc
            _log.warning(
                "%s: JSON contract error (attempt %d/%d): %s",
                label,
                attempt,
                _MAX_PARSE_RETRIES,
                exc,
            )
            continue
        return raw
    raise ValueError(f"{label}: JSON 解析失败（{_MAX_PARSE_RETRIES} 次重试后仍无效）") from last_err


AI_POLISH_SUGGESTIONS_FIELD = "_ai_polish_suggestions"
AI_CREATIVE_NOTE_FIELD = "_ai_creative_note"


@dataclass(frozen=True)
class AiChoice:
    """One configurable AI-control option shared by UI and prompt rendering."""

    value: str
    label: str
    prompt: str
    description: str = ""


GENERATION_MODE_CHOICES: tuple[AiChoice, ...] = (
    AiChoice(
        "replace",
        "重写新方案",
        "完全从零构思新题材、新人物、新世界，不参考当前表单内容；只尊重用户提示与显式参数。",
        "丢弃当前所有配置，基于提示词从头生成全新方案",
    ),
    AiChoice(
        "fill_blanks",
        "只补空白",
        "已有内容是边界与基底，只补全空白或明显不足的字段，不改动已填内容。",
        "保留所有已填字段，仅补充空白项",
    ),
    AiChoice(
        "variant",
        "生成变体",
        "保留当前核心设定、题材、情绪方向和硬约束，换一个更有张力的表达角度；不会更换题材或推翻已有世界观。",
        "在当前设定基础上换表达角度，保留题材与核心设定",
    ),
)

CREATIVE_AXIS_CHOICES: dict[str, tuple[AiChoice, ...]] = {
    "style": (
        AiChoice("balanced", "均衡完整", "整体完整、可执行、不过度偏科。"),
        AiChoice("high_concept", "高概念强钩子", "优先制造一句话能抓人的陌生化概念。"),
        AiChoice("commercial", "商业连载感", "优先追读感、关系推进和章节钩子。"),
        AiChoice("literary", "文学质感", "优先时代质地、人物内在和表达余韵。"),
        AiChoice("emotional", "强情感张力", "优先亲密关系、误判、欲望和情绪压强。"),
    ),
    "novelty": (
        AiChoice("steady", "稳妥清晰", "新意服务清晰度，不追求过度陌生化。"),
        AiChoice("fresh", "新鲜独特", "在可读基础上加入明确独特点。"),
        AiChoice("bold", "大胆陌生化", "允许更少见的职业、机制或叙事切口，但仍要可写。"),
    ),
    "conflict": (
        AiChoice("gentle", "舒展推进", "冲突逐步升温，避免一上来压强过满。"),
        AiChoice("layered", "多层压力", "外部事件、人物关系和内心矛盾同步推进。"),
        AiChoice("high_pressure", "高压强钩子", "开局就给出强问题、强选择或强误判。"),
    ),
    "emotion": (
        AiChoice("restrained", "克制留白", "情绪不直白宣泄，靠场景和行动显影。"),
        AiChoice("textured", "细腻有层次", "情感推进有递进、反复与微妙变化。"),
        AiChoice("intense", "浓烈高张力", "人物欲望与关系拉扯更鲜明。"),
    ),
}

FIELD_LABELS: dict[str, str] = {
    "theme": "故事主题",
    "premise": "故事前提",
    "genre": "题材",
    "tone": "基调",
    "title": "标题",
    "characters_hint": "人物提示",
    "world_hint": "世界观提示",
    "conflict_hint": "冲突提示",
    "pov_hint": "叙事视角",
    "opening_style": "开篇方式",
    "ending_style": "结尾方式",
    "extra_instructions": "额外创作指令",
    "polish_hint": "大纲润色提示",
    "language": "语言",
    "length_target": "目标字数",
    "total_chapters": "总章节数",
    "words_per_chapter": "每章字数",
    "max_edit_rounds": "编辑轮次",
    "project_id": "项目ID",
}

_POLISH_FIELD_PRIORITY = (
    "theme",
    "premise",
    "title",
    "characters_hint",
    "world_hint",
    "conflict_hint",
    "pov_hint",
    "opening_style",
    "ending_style",
    "extra_instructions",
    "polish_hint",
)

# ── Genre / tone enum look-up for fuzzy matching ─────────────────

_GENRE_MAP: dict[str, str] = {
    "文学": "literary",
    "奇幻": "fantasy",
    "科幻": "scifi",
    "悬疑": "mystery",
    "言情": "romance",
    "惊悚": "thriller",
    "恐怖": "horror",
    "历史": "historical",
    "其他": "other",
    "literary": "literary",
    "fantasy": "fantasy",
    "scifi": "scifi",
    "mystery": "mystery",
    "romance": "romance",
    "thriller": "thriller",
    "horror": "horror",
    "historical": "historical",
    "other": "other",
}

_TONE_MAP: dict[str, str] = {
    "中性": "neutral",
    "温暖": "warm",
    "温柔": "gentle",
    "阴郁": "dark",
    "悬疑": "suspenseful",
    "幽默": "humorous",
    "庄重": "solemn",
    "抒情": "lyrical",
    "neutral": "neutral",
    "warm": "warm",
    "gentle": "gentle",
    "dark": "dark",
    "suspenseful": "suspenseful",
    "humorous": "humorous",
    "solemn": "solemn",
    "lyrical": "lyrical",
}

_PASSTHROUGH_FIELDS = {
    "short": {
        "segment_trigger_words",
        "blueprint_element_preferences",
        "research_enabled",
        "research_provider",
        "research_query_hint",
    },
    "long": {
        "blueprint_element_preferences",
        "research_enabled",
        "research_provider",
        "research_query_hint",
    },
}

_AI_METADATA_OUTPUT_FIELDS = {"polish_suggestions", "creative_note"}


def _choice_map(choices: tuple[AiChoice, ...]) -> dict[str, AiChoice]:
    return {choice.value: choice for choice in choices}


def generation_mode_options() -> list[tuple[str, str, str]]:
    """Return generation-mode options for UI controls."""
    return [(choice.label, choice.value, choice.prompt) for choice in GENERATION_MODE_CHOICES]


def generation_mode_description(value: str) -> str:
    """Return the human-readable UI description for a generation mode."""
    return (
        _choice_map(GENERATION_MODE_CHOICES).get(value, AiChoice(value, value, "", "")).description
    )


def creative_axis_options(axis: str) -> list[tuple[str, str, str]]:
    """Return creative-control options for one axis."""
    return [
        (choice.label, choice.value, choice.prompt)
        for choice in CREATIVE_AXIS_CHOICES.get(axis, ())
    ]


def get_ai_field_label(field_name: str, mode: str | None = None) -> str:
    """Return a human label for a config field."""
    if field_name == "characters_hint" and mode == "long":
        return "主角群提示"
    if field_name == "characters_hint" and mode == "short":
        return "人物提示"
    if field_name == "world_hint" and mode == "long":
        return "世界观 / 时代背景"
    if field_name == "world_hint" and mode == "short":
        return "世界观 / 场景"
    if field_name == "conflict_hint" and mode == "long":
        return "主冲突提示"
    if field_name == "conflict_hint" and mode == "short":
        return "核心冲突"
    if field_name == "theme" and mode == "short":
        return "故事主题"
    if field_name == "premise" and mode == "long":
        return "故事前提"
    return FIELD_LABELS.get(field_name, field_name)


def _field_names_for_mode(mode: str) -> set[str]:
    """Derive accepted desktop config fields from the preset template."""
    fields = set(preset_template(mode))
    fields.difference_update(_PASSTHROUGH_FIELDS.get(mode, set()))
    fields.difference_update({"segment_trigger_words", "blueprint_element_preferences"})
    return fields


def polishable_fields_for_mode(mode: str | None = None) -> list[str]:
    """Return creative text fields that AI polish may target."""
    allowed = (
        _field_names_for_mode(mode)
        if mode in {"short", "long"}
        else _field_names_for_mode("short") | _field_names_for_mode("long")
    )
    return [field for field in _POLISH_FIELD_PRIORITY if field in allowed]


def _choice_label(axis: str, value: str) -> str:
    choices = (
        GENERATION_MODE_CHOICES
        if axis == "generation_mode"
        else CREATIVE_AXIS_CHOICES.get(axis, ())
    )
    return _choice_map(choices).get(value, AiChoice(value, value, value)).label


def _choice_prompt(axis: str, value: str) -> str:
    choices = (
        GENERATION_MODE_CHOICES
        if axis == "generation_mode"
        else CREATIVE_AXIS_CHOICES.get(axis, ())
    )
    return _choice_map(choices).get(value, AiChoice(value, value, value)).prompt


def _truncate_anchor_text(value: Any, *, max_chars: int | None = 220) -> str:
    text = _structured_to_text(value) if isinstance(value, (list, dict)) else str(value)
    text = " ".join(text.split())
    if max_chars is None:
        return text
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "…"
    return text


def _anchor_constraints_text(
    base_config: dict[str, Any],
    mode: str,
    *,
    exclude_fields: list[str] | tuple[str, ...] | set[str] = (),
    max_chars: int | None = 220,
) -> str:
    """Render non-empty current fields as anti-drift anchors for the model."""
    excluded = set(exclude_fields)
    anchor_keys = [
        key
        for key in (
            "title",
            "genre",
            "tone",
            "theme",
            "premise",
            "characters_hint",
            "world_hint",
            "conflict_hint",
            "pov_hint",
            "opening_style",
            "ending_style",
            "extra_instructions",
            "total_chapters",
            "words_per_chapter",
            "length_target",
        )
        if key not in excluded and key in base_config and not _is_blank_value(base_config.get(key))
    ]
    lines: list[str] = []
    for key in anchor_keys:
        lines.append(
            f"- {get_ai_field_label(key, mode)}（{key}）："
            f"{_truncate_anchor_text(base_config[key], max_chars=max_chars)}"
        )
    return "\n".join(lines)


def _render_constraint_lines(constraints: dict[str, Any], mode: str) -> str:
    """Render explicit user-selected generation constraints for prompt context."""
    lines: list[str] = []
    for key, value in constraints.items():
        if _is_blank_value(value):
            continue
        lines.append(f"- {get_ai_field_label(key, mode)}（{key}）：{value}")
    return "\n".join(lines)


def _output_fields_text(
    mode: str, editable_fields: list[str] | tuple[str, ...] | None = None
) -> str:
    selected = _normalize_focus_fields(list(editable_fields or []), mode)
    config_fields = selected if selected else sorted(_field_names_for_mode(mode))
    prefix = "本轮可修改创作配置字段：" if selected else "创作配置字段："
    return "\n".join(
        [
            prefix + "、".join(config_fields),
            "桌面元数据字段：" + "、".join(sorted(_AI_METADATA_OUTPUT_FIELDS)),
            "除以上字段外不要输出额外顶层 key。",
        ]
    )


def _structured_to_text(value: Any) -> str:
    """Convert structured LLM output (list/dict) to readable plain text."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                if "name" in item:
                    name = item["name"]
                    rest = [str(v) for k, v in item.items() if k != "name" and v]
                    parts.append(f"{name}：{'，'.join(rest)}")
                else:
                    parts.append("；".join(f"{str(v)}" for v in item.values() if v))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    if isinstance(value, dict):
        return "；".join(f"{str(v)}" for v in value.values() if v)
    return str(value)


def _normalize_enum(value: str, enum_map: dict[str, str]) -> str:
    """Try to match a possibly free-text LLM value to a known enum key."""
    v = value.strip()
    # Direct match
    if v in enum_map:
        return enum_map[v]
    # Substring match: find the first enum key contained in the value
    for label, code in enum_map.items():
        if label in v:
            return code
    return v


def _normalize_polish_suggestions(value: Any) -> list[str]:
    """Normalize suggestion list from model/UI inputs."""
    if value is None:
        return []

    raw_items: list[str] = []
    if isinstance(value, str):
        text = value
        for separator in ("；", ";", "。", "，", ",", "、"):
            text = text.replace(separator, "\n")
        raw_items.extend(line.strip() for line in text.splitlines())
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                raw_items.append(item)
            elif isinstance(item, dict):
                picked = (
                    item.get("text")
                    or item.get("label")
                    or item.get("direction")
                    or item.get("hint")
                    or ""
                )
                raw_items.append(str(picked))
            elif item is not None:
                raw_items.append(str(item))
    elif isinstance(value, dict):
        for item in value.values():
            if item is not None:
                raw_items.append(str(item))
    else:
        raw_items.append(str(value))

    normalized: list[str] = []
    for item in raw_items:
        clean = " ".join(str(item).split()).strip("；;，,。 ")
        if not clean:
            continue
        if clean not in normalized:
            normalized.append(clean)
        if len(normalized) >= 8:
            break
    return normalized


def _extract_polish_suggestions(raw: dict[str, Any]) -> list[str]:
    """Extract suggestion candidates from raw model response."""
    for key in ("polish_suggestions", "polish_options", "refine_suggestions"):
        if key in raw:
            return _normalize_polish_suggestions(raw.get(key))
    return []


def _note_text(value: Any, *, max_chars: int = 260) -> str:
    """Normalize a creative-note fragment into compact UI/history text."""
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        text = _structured_to_text(value)
    else:
        text = str(value)
    text = " ".join(text.split()).strip("；;，,。 ")
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "…"
    return text


def _normalize_note_list(value: Any, *, limit: int = 6) -> list[str]:
    """Normalize list-like creative-note sections."""
    if value is None:
        return []
    raw_items: list[Any] = []
    if isinstance(value, str):
        text = value
        for separator in ("；", ";", "。", "\n"):
            text = text.replace(separator, "\n")
        raw_items.extend(part.strip() for part in text.splitlines())
    elif isinstance(value, list):
        raw_items.extend(value)
    elif isinstance(value, dict):
        raw_items.extend(value.values())
    else:
        raw_items.append(value)

    normalized: list[str] = []
    for item in raw_items:
        clean = _note_text(item, max_chars=180)
        if clean and clean not in normalized:
            normalized.append(clean)
        if len(normalized) >= limit:
            break
    return normalized


def _normalize_field_rationales(value: Any) -> dict[str, str]:
    """Normalize per-field rationale notes from model responses."""
    rationales: dict[str, str] = {}
    if value is None:
        return rationales
    if isinstance(value, dict):
        items = value.items()
    elif isinstance(value, list):
        collected: list[tuple[Any, Any]] = []
        for item in value:
            if isinstance(item, dict):
                field = item.get("field") or item.get("key") or item.get("name") or item.get("字段")
                reason = (
                    item.get("reason")
                    or item.get("rationale")
                    or item.get("text")
                    or item.get("说明")
                    or item.get("理由")
                )
                if field and reason:
                    collected.append((field, reason))
        items = collected
    else:
        return rationales

    for key, item_value in items:
        field_name = str(key).strip()
        if not field_name:
            continue
        clean = _note_text(item_value, max_chars=180)
        if clean:
            rationales[field_name] = clean
        if len(rationales) >= 12:
            break
    return rationales


def _extract_creative_note(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract structured creative rationale from the raw model response."""
    source: Any = None
    for key in (
        "creative_note",
        "_creative_note",
        "design_note",
        "creative_rationale",
        "creation_note",
    ):
        if key in raw:
            source = raw.get(key)
            break
    if source is None:
        return {}

    if isinstance(source, str):
        text = _note_text(source, max_chars=480)
        return {"design_intent": text} if text else {}
    if not isinstance(source, dict):
        text = _note_text(source, max_chars=480)
        return {"design_intent": text} if text else {}

    aliases = {
        "core_pitch": ("core_pitch", "pitch", "hook", "核心卖点", "核心钩子"),
        "design_intent": ("design_intent", "intent", "rationale", "设计意图", "创作意图"),
        "preserved_constraints": (
            "preserved_constraints",
            "constraints",
            "保留约束",
            "信息守恒",
        ),
        "field_rationales": (
            "field_rationales",
            "field_notes",
            "rationales",
            "字段理由",
            "字段说明",
        ),
        "risks": ("risks", "watchouts", "风险", "注意事项"),
        "next_moves": ("next_moves", "next_steps", "followups", "下一步", "后续方向"),
        "anti_drift_check": (
            "anti_drift_check",
            "divergence_check",
            "consistency_check",
            "防发散检查",
            "边界检查",
        ),
    }

    note: dict[str, Any] = {}
    for target, keys in aliases.items():
        matched = next((key for key in keys if key in source), None)
        if matched is None:
            continue
        value = source.get(matched)
        if target == "field_rationales":
            rationales = _normalize_field_rationales(value)
            if rationales:
                note[target] = rationales
        elif target in {"preserved_constraints", "risks", "next_moves", "anti_drift_check"}:
            items = _normalize_note_list(value)
            if items:
                note[target] = items
        else:
            text = _note_text(value)
            if text:
                note[target] = text
    return note


def _normalize_focus_fields(value: Any, mode: str | None = None) -> list[str]:
    """Normalize focus-field selector values for polish config."""
    if value is None:
        return []

    raw_items: list[str] = []
    if isinstance(value, str):
        raw_items = [
            part.strip() for part in value.replace("；", ",").replace("，", ",").split(",")
        ]
    elif isinstance(value, list):
        raw_items = [str(item).strip() for item in value]
    elif isinstance(value, dict):
        for key, enabled in value.items():
            if enabled:
                raw_items.append(str(key).strip())
    else:
        raw_items = [str(value).strip()]

    allowed_fields = set(polishable_fields_for_mode(mode))
    normalized: list[str] = []
    for field_name in raw_items:
        if not field_name:
            continue
        if field_name not in allowed_fields:
            continue
        if field_name in normalized:
            continue
        normalized.append(field_name)
    return normalized


def _validate_and_clean(
    data: dict[str, Any],
    mode: str,
    *,
    auto_project_id: bool = True,
) -> dict[str, Any]:
    """Normalise LLM output: keep only expected keys, fix types."""
    allowed = _field_names_for_mode(mode)
    cleaned: dict[str, Any] = {}
    for key in allowed:
        if key not in data:
            continue
        value = data[key]
        # Force integer types for numeric fields
        if key in (
            "length_target",
            "max_edit_rounds",
            "total_chapters",
            "words_per_chapter",
            "chapters_per_volume",
        ):
            try:
                cleaned[key] = int(value)
            except (TypeError, ValueError):
                pass
        elif key == "genre":
            cleaned[key] = _normalize_enum(str(value), _GENRE_MAP)
        elif key == "tone":
            cleaned[key] = _normalize_enum(str(value), _TONE_MAP)
        else:
            # Convert structured data (list/dict) to readable text
            if isinstance(value, (list, dict)):
                cleaned[key] = _structured_to_text(value)
            else:
                cleaned[key] = str(value) if value is not None else ""
    if auto_project_id:
        # Default project_id to title if available, otherwise empty
        title = cleaned.get("title", "").strip()
        cleaned["project_id"] = title if title else ""
    return cleaned


def _extract_passthrough_fields(data: dict[str, Any], mode: str) -> dict[str, Any]:
    """Return non-LLM desktop fields that polish must carry forward unchanged."""
    preserved: dict[str, Any] = {}
    for key in _PASSTHROUGH_FIELDS.get(mode, set()):
        if key in data:
            preserved[key] = copy.deepcopy(data[key])
    return preserved


def _prompt_language_context(config: dict[str, Any]) -> dict[str, str]:
    """Build prompt-pack context from a workflow form's output language.

    Configuration co-creation occurs before a project exists, so it cannot
    inherit a language from project state. The workflow form is the source of
    truth here. Explicitly supplying both values keeps the generate/polish
    helpers on the same prompt-pack path as the main writing pipeline.
    """

    output_language = str(config.get("language") or "zh").strip() or "zh"
    return {
        "output_language": output_language,
        "prompt_locale": prompt_locale_for_language(output_language),
    }


def _normalize_generation_mode(value: str | None) -> str:
    mode = str(value or "").strip()
    return mode if mode in _choice_map(GENERATION_MODE_CHOICES) else "replace"


def _normalize_creative_profile(value: dict[str, Any] | None) -> dict[str, str]:
    """Normalize creative-control options from the desktop generation dialog."""
    raw = value or {}
    profile = {
        "style": str(raw.get("style") or "balanced").strip(),
        "novelty": str(raw.get("novelty") or "fresh").strip(),
        "conflict": str(raw.get("conflict") or "layered").strip(),
        "emotion": str(raw.get("emotion") or "textured").strip(),
        "custom_brief": " ".join(str(raw.get("custom_brief") or "").split()).strip(),
    }
    if profile["style"] not in _choice_map(CREATIVE_AXIS_CHOICES["style"]):
        profile["style"] = "balanced"
    for key in ("novelty", "conflict", "emotion"):
        if profile[key] not in _choice_map(CREATIVE_AXIS_CHOICES[key]):
            profile[key] = {
                "novelty": "fresh",
                "conflict": "layered",
                "emotion": "textured",
            }[key]
    return profile


def _normalize_generation_constraints(value: dict[str, Any] | None, mode: str) -> dict[str, Any]:
    """Normalize hard controls from the AI generation dialog."""
    raw = value or {}
    allowed = _field_names_for_mode(mode)
    constraints: dict[str, Any] = {}
    for key in ("genre", "tone", "length_target", "total_chapters", "words_per_chapter"):
        if key not in allowed or key not in raw:
            continue
        val = raw.get(key)
        if _is_blank_value(val):
            continue
        if key in {"length_target", "total_chapters", "words_per_chapter"}:
            try:
                intval = int(val)
            except (TypeError, ValueError):
                continue
            if intval > 0:
                constraints[key] = intval
        else:
            text = " ".join(str(val).split()).strip()
            if text:
                constraints[key] = text
    return constraints


def _creative_profile_text(profile: dict[str, str]) -> str:
    lines = [
        f"- 创作取向：{_choice_label('style', profile['style'])}；{_choice_prompt('style', profile['style'])}",
        f"- 新奇度：{_choice_label('novelty', profile['novelty'])}；{_choice_prompt('novelty', profile['novelty'])}",
        f"- 冲突密度：{_choice_label('conflict', profile['conflict'])}；{_choice_prompt('conflict', profile['conflict'])}",
        f"- 情感浓度：{_choice_label('emotion', profile['emotion'])}；{_choice_prompt('emotion', profile['emotion'])}",
    ]
    custom_brief = profile.get("custom_brief", "")
    if custom_brief:
        lines.append(f"- 用户自由创意侧重点：{custom_brief}")
    return "\n".join(lines)


def _story_synopsis_impact_text(
    mode: str,
    *,
    operation: str,
    generation_mode: str | None = None,
    creative_profile: dict[str, str] | None = None,
    hard_constraints: dict[str, Any] | None = None,
    selected_suggestions: list[str] | None = None,
    focus_fields: list[str] | None = None,
) -> str:
    """Describe how UI controls must influence the main synopsis field."""
    main_field = "theme" if mode == "short" else "premise"
    main_label = get_ai_field_label(main_field, mode)
    lines = [
        f"- 主要梗概字段是「{main_label}（{main_field}）」；以下控制项不是为了影响而影响，不能机械塞入字段名或数字；但凡会改变故事类型、叙事尺度、冲突结构或情绪气质的选择，都应自然约束该字段。",
    ]
    if operation == "generate":
        if generation_mode:
            lines.append(
                f"- 生成方式：{_choice_label('generation_mode', generation_mode)}；{_choice_prompt('generation_mode', generation_mode)}"
            )
        if creative_profile:
            lines.append(
                "- 创意取向/新奇度/冲突密度/情感浓度应共同塑造梗概的卖点、压力结构和人物欲望；保持故事自然可写，不做生硬堆砌。"
            )
            custom_brief = creative_profile.get("custom_brief", "")
            if custom_brief:
                lines.append(f"- 自由创意侧重点必须体现在梗概中：{custom_brief}")
        if hard_constraints:
            lines.append(
                "- 题材、基调、章节/字数等硬参数应约束梗概的故事尺度、类型承诺和叙事气质；数字本身不必生硬写入梗概，除非它是故事设定的一部分。"
            )
    else:
        if selected_suggestions:
            lines.append(
                "- 已勾选润色灵感应转化为相关字段中的具体补强点；只有确实改变核心设定、人物欲望或冲突结构时，才同步改写梗概："
                + "；".join(selected_suggestions)
            )
        if focus_fields:
            focus_labels = [
                f"{get_ai_field_label(field, mode)}（{field}）" for field in focus_fields
            ]
            lines.append(
                "- 重点字段会参与梗概联动；若润色内容实质影响人物、世界观、冲突、开篇、结尾、额外指令，"
                f"才同步校准「{main_label}（{main_field}）」以保持同一故事方向："
                + "、".join(focus_labels)
            )
    return "\n".join(lines)


def _polish_focus_coverage_text(mode: str, focus_fields: list[str]) -> str:
    """Render the hard coverage contract for selected AI polish focus fields."""
    normalized = _normalize_focus_fields(focus_fields, mode)
    if not normalized:
        return ""
    labels = [f"{get_ai_field_label(field, mode)}（{field}）" for field in normalized]
    return "\n".join(
        [
            f"本轮共选择 {len(normalized)} 个重点润色字段，必须全部产生可感知改写："
            + "、".join(labels),
            "不要只改其中 4-5 个字段；每个重点字段都要在保留事实与硬约束的前提下提升表达精度、冲突张力或可执行细节。",
            "输出 JSON 可只包含本轮修改字段；creative_note.field_rationales 如输出，必须逐一说明每个重点字段的修改理由。",
        ]
    )


def _is_blank_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    if isinstance(value, (int, float)):
        return value == 0
    return False


def _polish_value_changed(before: Any, after: Any) -> bool:
    if _is_blank_value(after):
        return False
    before_text = (
        _structured_to_text(before).strip()
        if isinstance(before, (list, dict))
        else str(before or "").strip()
    )
    after_text = (
        _structured_to_text(after).strip()
        if isinstance(after, (list, dict))
        else str(after or "").strip()
    )
    return bool(after_text and after_text != before_text)


def _focus_fields_missing_changes(
    base_config: dict[str, Any],
    polished: dict[str, Any],
    focus_fields: list[str],
) -> list[str]:
    return [
        field
        for field in focus_fields
        if field in base_config
        and not _polish_value_changed(base_config.get(field), polished.get(field))
    ]


def _merge_generated_config(
    base_config: dict[str, Any],
    generated: dict[str, Any],
    *,
    generation_mode: str,
) -> dict[str, Any]:
    if generation_mode == "fill_blanks":
        merged = dict(base_config)
        for key, value in generated.items():
            if key not in merged or _is_blank_value(merged.get(key)):
                merged[key] = value
        return merged
    if generation_mode == "variant":
        merged = dict(base_config)
        for key, value in generated.items():
            if isinstance(value, str):
                if value.strip():
                    merged[key] = value
            elif value is not None:
                merged[key] = value
        return merged
    return dict(generated)


async def generate_config(
    mode: str,
    user_hint: str = "",
    *,
    current_config: dict[str, Any] | None = None,
    generation_mode: str = "replace",
    creative_profile: dict[str, Any] | None = None,
    hard_constraints: dict[str, Any] | None = None,
    runtime: RuntimeServices | None = None,
    mock: bool = False,
) -> dict[str, Any]:
    """Call the LLM to generate a creative configuration.

    Args:
        mode: ``"short"`` or ``"long"``.
        user_hint: Optional free-text description from the user (can be empty).
        runtime: Pre-built runtime services. Created automatically if *None*.
        mock: If *True* and *runtime* is None, create mock runtime (for testing).

    Returns:
        A dict of form field values ready to fill into the UI.
    """
    owned_runtime = runtime is None
    if runtime is None:
        runtime = create_runtime_services(mock=mock)
    assert runtime is not None

    router: ModelRouter = runtime.router
    builder: PromptBuilder = runtime.builder

    current_payload = dict(current_config or {})
    constraints = _normalize_generation_constraints(hard_constraints, mode)
    effective_payload = {**current_payload, **constraints}
    base_config = _validate_and_clean(effective_payload, mode, auto_project_id=False)
    passthrough_fields = _extract_passthrough_fields(current_payload, mode)
    generation_mode = _normalize_generation_mode(generation_mode)
    creative_profile_norm = _normalize_creative_profile(creative_profile)
    mode_label = "短篇小说" if mode == "short" else "长篇小说"
    # replace 模式不传旧配置给 LLM，让其真正从零构思；variant/fill_blanks 需要参考
    _pass_current_to_llm = generation_mode != "replace"
    context = {
        **_prompt_language_context(current_payload),
        "mode": mode,
        "mode_label": mode_label,
        "operation": "generate",
        "generation_mode": generation_mode,
        "generation_mode_label": _choice_label("generation_mode", generation_mode),
        "generation_mode_prompt": _choice_prompt("generation_mode", generation_mode),
        "creative_profile": creative_profile_norm,
        "creative_profile_text": _creative_profile_text(creative_profile_norm),
        "allow_partial_config_output": generation_mode in {"fill_blanks", "variant"},
        "hard_constraints": constraints,
        "hard_constraints_text": _render_constraint_lines(constraints, mode),
        "story_synopsis_impact_text": _story_synopsis_impact_text(
            mode,
            operation="generate",
            generation_mode=generation_mode,
            creative_profile=creative_profile_norm,
            hard_constraints=constraints,
        ),
        "output_fields_text": _output_fields_text(mode),
        "anchor_constraints_text": _anchor_constraints_text(base_config, mode)
        if _pass_current_to_llm
        else "",
        "user_hint": user_hint.strip(),
        "current_config_json": json.dumps(base_config, ensure_ascii=False, indent=2)
        if base_config and _pass_current_to_llm
        else "",
    }

    settings = getattr(runtime, "settings", None)
    raw_temperature = getattr(settings, "temp_generate_config", 0.9)
    temperature = float(raw_temperature if raw_temperature is not None else 0.9)
    validate_generate_config_context(
        context,
        task_type=TaskType.GENERATE_CONFIG,
        source="app_service.ai_generate.generate_config",
    )
    request = builder.build(
        TaskType.GENERATE_CONFIG,
        context,
        max_tokens=calculate_route_aware_max_tokens(
            router,
            TaskType.GENERATE_CONFIG,
            2600,
            prompt_overhead=1800,
            min_tokens=2048,
        ),
        temperature=temperature,
    )

    _log.info(
        "generate_config: mode=%s generation_mode=%s hint=%r",
        mode,
        generation_mode,
        user_hint[:80],
    )
    try:
        raw = await _call_and_parse(
            router,
            request,
            task_type=TaskType.GENERATE_CONFIG,
            context=context,
            label="generate_config",
        )
        generated = _validate_and_clean(raw, mode, auto_project_id=True)
        result = _merge_generated_config(
            {**base_config, **passthrough_fields},
            generated,
            generation_mode=generation_mode,
        )
        result.update(constraints)
        for key, value in passthrough_fields.items():
            result.setdefault(key, value)
        suggestions = _extract_polish_suggestions(raw)
        if suggestions:
            result[AI_POLISH_SUGGESTIONS_FIELD] = suggestions
        creative_note = _extract_creative_note(raw)
        if creative_note:
            result[AI_CREATIVE_NOTE_FIELD] = creative_note
        _log.info("generate_config: got %d fields", len(result))
        return result
    finally:
        await _shutdown_owned_runtime(
            runtime,
            owned=owned_runtime,
            source="generate_config",
        )


async def polish_config(
    mode: str,
    current_config: dict[str, Any],
    user_hint: str = "",
    *,
    selected_suggestions: list[str] | None = None,
    focus_fields: list[str] | None = None,
    runtime: RuntimeServices | None = None,
    mock: bool = False,
) -> dict[str, Any]:
    """Refine an existing config based on user direction + optional suggestion tags."""
    owned_runtime = runtime is None
    if runtime is None:
        runtime = create_runtime_services(mock=mock)
    assert runtime is not None
    try:
        return await _polish_config_body(
            runtime,
            current_config=current_config,
            mode=mode,
            user_hint=user_hint,
            selected_suggestions=selected_suggestions,
            focus_fields=focus_fields,
        )
    finally:
        await _shutdown_owned_runtime(
            runtime,
            owned=owned_runtime,
            source="polish_config",
        )


async def _polish_config_body(
    runtime: RuntimeServices,
    *,
    current_config: dict[str, Any],
    mode: str,
    user_hint: str,
    selected_suggestions: list[str] | None,
    focus_fields: list[str] | None,
) -> dict[str, Any]:
    """Refine an existing config based on user direction + optional suggestion tags.

    The caller owns ``runtime`` and is responsible for shutting it down; this
    body never creates a fresh runtime and never awaits ``runtime.shutdown()``.
    """
    router: ModelRouter = runtime.router
    builder: PromptBuilder = runtime.builder

    current_payload = dict(current_config or {})
    base_config = _validate_and_clean(current_payload, mode, auto_project_id=False)
    if not base_config:
        raise ValueError("当前配置为空，无法执行润色。")
    passthrough_fields = _extract_passthrough_fields(current_payload, mode)

    chosen_suggestions = _normalize_polish_suggestions(selected_suggestions or [])
    chosen_focus_fields = _normalize_focus_fields(focus_fields or [], mode)
    editable_fields = chosen_focus_fields or polishable_fields_for_mode(mode)
    editable_config = {key: base_config.get(key) for key in editable_fields if key in base_config}
    mode_label = "短篇小说" if mode == "short" else "长篇小说"
    context = {
        **_prompt_language_context(current_payload),
        "mode": mode,
        "mode_label": mode_label,
        "operation": "polish",
        "user_hint": user_hint.strip(),
        "selected_suggestions": chosen_suggestions,
        "focus_fields": chosen_focus_fields,
        "editable_config_fields": editable_fields,
        "allow_partial_config_output": True,
        "story_synopsis_impact_text": _story_synopsis_impact_text(
            mode,
            operation="polish",
            selected_suggestions=chosen_suggestions,
            focus_fields=chosen_focus_fields,
        ),
        "polish_focus_coverage_text": _polish_focus_coverage_text(
            mode,
            chosen_focus_fields,
        ),
        "output_fields_text": _output_fields_text(mode, editable_fields),
        "anchor_constraints_text": _anchor_constraints_text(
            base_config,
            mode,
            exclude_fields=editable_fields,
            max_chars=None,
        ),
        "current_config_json": json.dumps(editable_config, ensure_ascii=False, indent=2),
    }

    settings = getattr(runtime, "settings", None)
    raw_temperature = getattr(settings, "temp_polish_config", 0.75)
    temperature = float(raw_temperature if raw_temperature is not None else 0.75)
    validate_generate_config_context(
        context,
        task_type=TaskType.POLISH_CONFIG,
        source="app_service.ai_generate.polish_config",
    )
    request = builder.build(
        TaskType.POLISH_CONFIG,
        context,
        max_tokens=calculate_route_aware_max_tokens(
            router,
            TaskType.POLISH_CONFIG,
            max(2600, len(context["current_config_json"]) // 2),
            prompt_overhead=2200,
            min_tokens=2048,
        ),
        temperature=temperature,
    )

    _log.info(
        "polish_config: mode=%s hint=%r selected=%d focus=%d",
        mode,
        user_hint[:80],
        len(chosen_suggestions),
        len(chosen_focus_fields),
    )
    raw = await _call_and_parse(
        router,
        request,
        task_type=TaskType.POLISH_CONFIG,
        context=context,
        label="polish_config",
    )
    polished = _validate_and_clean(raw, mode, auto_project_id=False)
    missing_focus_fields = _focus_fields_missing_changes(
        base_config,
        polished,
        chosen_focus_fields,
    )
    if missing_focus_fields:
        retry_context = dict(context)
        retry_context["focus_fields"] = missing_focus_fields
        retry_context["editable_config_fields"] = missing_focus_fields
        retry_context["allow_partial_config_output"] = True
        retry_context["output_fields_text"] = _output_fields_text(mode, missing_focus_fields)
        retry_context["anchor_constraints_text"] = _anchor_constraints_text(
            base_config,
            mode,
            exclude_fields=missing_focus_fields,
            max_chars=None,
        )
        retry_context["current_config_json"] = json.dumps(
            {
                field: base_config.get(field)
                for field in missing_focus_fields
                if field in base_config
            },
            ensure_ascii=False,
            indent=2,
        )
        retry_context["polish_focus_coverage_text"] = _polish_focus_coverage_text(
            mode,
            missing_focus_fields,
        )
        retry_context["story_synopsis_impact_text"] = _story_synopsis_impact_text(
            mode,
            operation="polish",
            selected_suggestions=chosen_suggestions,
            focus_fields=missing_focus_fields,
        )
        retry_context["user_hint"] = "\n\n".join(
            part
            for part in (
                user_hint.strip(),
                "上一轮润色没有让以下重点字段产生可感知变化；本轮只补足这些遗漏字段，"
                "必须逐项改写并保留原事实：" + "、".join(missing_focus_fields),
            )
            if part
        )
        validate_generate_config_context(
            retry_context,
            task_type=TaskType.POLISH_CONFIG,
            source="app_service.ai_generate.polish_config.retry",
        )
        retry_request = builder.build(
            TaskType.POLISH_CONFIG,
            retry_context,
            max_tokens=calculate_route_aware_max_tokens(
                router,
                TaskType.POLISH_CONFIG,
                max(2200, len(retry_context["current_config_json"]) // 3),
                prompt_overhead=2200,
                min_tokens=2048,
            ),
            temperature=temperature,
        )
        _log.info(
            "polish_config: retrying uncovered focus fields=%s",
            ",".join(missing_focus_fields),
        )
        try:
            retry_raw = await _call_and_parse(
                router,
                retry_request,
                task_type=TaskType.POLISH_CONFIG,
                context=retry_context,
                label="polish_config_focus_retry",
            )
        except Exception as exc:
            _log.warning(
                "polish_config_focus_retry_failed_nonfatal | fields=%s | error=%s",
                ",".join(missing_focus_fields),
                exc,
            )
        else:
            retry_polished = _validate_and_clean(retry_raw, mode, auto_project_id=False)
            for field in missing_focus_fields:
                if _polish_value_changed(base_config.get(field), retry_polished.get(field)):
                    polished[field] = retry_polished[field]

    merged = {**base_config, **passthrough_fields}
    for key, value in polished.items():
        if isinstance(value, str):
            if value.strip():
                merged[key] = value
        elif value is not None:
            merged[key] = value

    suggestions = _extract_polish_suggestions(raw)
    if suggestions:
        merged[AI_POLISH_SUGGESTIONS_FIELD] = suggestions
    elif chosen_suggestions:
        merged[AI_POLISH_SUGGESTIONS_FIELD] = chosen_suggestions

    creative_note = _extract_creative_note(raw)
    if creative_note:
        merged[AI_CREATIVE_NOTE_FIELD] = creative_note

    _warn_polish_field_length_overflow(merged)

    _log.info("polish_config: got %d fields", len(merged))

    try:
        settings = runtime.settings
        project_id = merged.get("project_id", "") or merged.get("title", "default")
        project_dir = Path(settings.storage_root) / project_id
        recorder = PolishHistoryRecorder(project_dir)
        history_after = {k: v for k, v in merged.items() if not str(k).startswith("_")}
        changed = [
            k for k in history_after if k not in base_config or history_after[k] != base_config[k]
        ]
        recorder.record_spec_polish(
            user_hint=user_hint,
            selected_suggestions=chosen_suggestions,
            focus_fields=chosen_focus_fields,
            before_spec=base_config,
            after_spec=history_after,
            changed_keys=changed,
            ai_suggestions=_extract_polish_suggestions(raw) or chosen_suggestions,
            creative_note=creative_note,
        )
    except Exception as exc:
        _log.warning("spec_polish_history_persist_failed | error=%s", exc)

    return merged


_POLISH_FIELD_LENGTH_LIMITS: dict[str, int] = {
    "extra_instructions": 500,
    "polish_hint": 200,
    "opening_style": 300,
    "ending_style": 300,
    "pov_hint": 300,
}


def _warn_polish_field_length_overflow(merged: dict[str, Any]) -> None:
    """Log a warning when polished config fields exceed the anti-bloat limits.

    Non-blocking: only emits a structured warning so the LLM self-discipline
    (enforced via the prompt) remains the primary control. This is the
    safety net for catching persistent overflow across multiple polish rounds.
    """
    for field, limit in _POLISH_FIELD_LENGTH_LIMITS.items():
        value = merged.get(field)
        if not isinstance(value, str):
            continue
        length = len(value)
        if length > limit:
            _log.warning(
                "polish_config_field_overflow | field=%s | length=%d | limit=%d | "
                "overshoot=%d | value_preview=%r",
                field,
                length,
                limit,
                length - limit,
                value[:120],
            )


def get_default_polish_suggestions(mode: str) -> list[str]:
    """Return field-driven fallback suggestions when no model suggestions exist yet."""
    return [
        f"强化{get_ai_field_label(field, mode)}的具体性、张力和可执行性"
        for field in polishable_fields_for_mode(mode)
    ][:8]
