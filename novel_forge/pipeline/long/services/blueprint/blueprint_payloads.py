"""Blueprint payload normalization and structural fallback helpers.

This module owns recoverable shape drift for NarrativeBlueprint payloads at
initialization boundaries. It intentionally separates syntax/shape repair from
init cache and orchestration code so future blueprint schema changes have a
single place to evolve.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from novel_forge.core.response_repair import (
    coerce_dependency_ref_list,
    parse_json_object_string,
)
from novel_forge.pipeline.long.services.blueprint.blueprint_chapter_refs import (
    repair_explicit_chapter_refs,
)

_BLUEPRINT_SOURCE_TYPE_ALIASES = {
    "main_plot": "main_plot",
    "mainplot": "main_plot",
    "main": "main_plot",
    "mainline": "main_plot",
    "main_line": "main_plot",
    "主线": "main_plot",
    "主线事件": "main_plot",
    "主线剧情": "main_plot",
    "主线情节": "main_plot",
    "主情节": "main_plot",
    "主剧情": "main_plot",
    "subplot": "subplot",
    "sub_plot": "subplot",
    "side_plot": "subplot",
    "sideplot": "subplot",
    "branch": "subplot",
    "支线": "subplot",
    "副线": "subplot",
    "支线事件": "subplot",
    "支线剧情": "subplot",
    "支线情节": "subplot",
    "turning_point": "turning_point",
    "turningpoint": "turning_point",
    "turn": "turning_point",
    "关键转折": "turning_point",
    "转折点": "turning_point",
    "剧情转折": "turning_point",
    "主线转折": "turning_point",
}

_BLUEPRINT_BASE_MODEL_FIELDS = {"schema_version", "created_at"}
_BLUEPRINT_SUBPLOT_FIELDS = {
    "name",
    "description",
    "involved_chapters",
    "chapter_events",
    "weave_links",
    "priority",
    "resolution_chapter",
    "resolution_target",
    "resolution_type",
    *_BLUEPRINT_BASE_MODEL_FIELDS,
}
_BLUEPRINT_CHAPTER_EVENT_FIELDS = {
    "chapter_number",
    "event",
    "weave_notes",
    "depends_on",
    *_BLUEPRINT_BASE_MODEL_FIELDS,
}
_BLUEPRINT_CHARACTER_ARC_FIELDS = {
    "character",
    "arc_summary",
    "milestones",
    *_BLUEPRINT_BASE_MODEL_FIELDS,
}
_BLUEPRINT_NARRATIVE_PHASE_FIELDS = {
    "phase_name",
    "chapter_start",
    "chapter_end",
    "description",
    "key_events",
    "tension_level",
    "time_context",
    "primary_locations",
    "key_characters",
    *_BLUEPRINT_BASE_MODEL_FIELDS,
}
_BLUEPRINT_NARRATIVE_PHASE_ALIASES = {
    "phase_description": "description",
    "phase_goal": "description",
    "main_locations": "primary_locations",
    "locations": "primary_locations",
}
_BLUEPRINT_NARRATIVE_PHASE_LIST_FIELDS = {
    "key_events",
    "primary_locations",
    "key_characters",
}
_BLUEPRINT_ARC_MILESTONE_FIELDS = {
    "chapter_start",
    "chapter_end",
    "description",
    *_BLUEPRINT_BASE_MODEL_FIELDS,
}
_BLUEPRINT_WEAVE_LINK_FIELDS = {
    "source_type",
    "source_ref",
    "target_subplot",
    "trigger_chapter",
    "link_type",
    "description",
    *_BLUEPRINT_BASE_MODEL_FIELDS,
}
_BLUEPRINT_EVENT_WEAVE_HINT_FIELDS = {
    "source_type",
    "source_ref",
    "target_subplot",
    "trigger_chapter",
    "link_type",
}
_BLUEPRINT_VOLUME_TEXT_REF_FIELDS = (
    "arc_goal",
    "milestone_targets",
    "main_conflicts",
    "climax_hint",
    "resolution_hint",
    "notes",
)
_BLUEPRINT_PHASE_TEXT_REF_FIELDS = (
    "phase_name",
    "description",
    "key_events",
    "tension_level",
    "time_context",
)
_BLUEPRINT_GLOBAL_TEXT_REF_FIELDS = ("synopsis", "ending_strategy")
_BLUEPRINT_MILESTONE_CHAPTER_FIELDS = {"chapter_start", "chapter_end"}
_BLUEPRINT_LEAKED_MILESTONE_FIELD_RE = re.compile(
    r"(?:^|[；;\s])\.?\s*(chapter_start|chapter_end)\s*[:：]\s*(\d{1,4})\s*$",
    re.IGNORECASE,
)


def _compact_blueprint_json_text(value: Any, *, limit: int = 1200) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except TypeError:
        text = str(value)
    text = " ".join(text.split()).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _coerce_blueprint_text_field(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_coerce_blueprint_text_field(item) for item in value]
        return "；".join(part for part in parts if part)
    if isinstance(value, dict):
        ordered_keys: tuple[tuple[str, str], ...] = (
            ("summary", ""),
            ("description", ""),
            ("strategy", ""),
            ("resolution", ""),
            ("payoff", ""),
            ("final_scene", "终局画面"),
            ("ending_image", "终局画面"),
            ("theme_resolution", "主题收束"),
            ("relationship_resolution", "关系收束"),
            ("subplot_resolution", "支线收束"),
        )
        parts: list[str] = []
        seen_keys: set[str] = set()
        for key, label in ordered_keys:
            if key not in value:
                continue
            seen_keys.add(key)
            text = _coerce_blueprint_text_field(value.get(key))
            if not text:
                continue
            parts.append(f"{label}：{text}" if label else text)
        for key, raw in value.items():
            if key in seen_keys or raw in (None, "", [], {}):
                continue
            text = _coerce_blueprint_text_field(raw)
            if text:
                parts.append(f"{key}：{text}")
        if parts:
            return "；".join(dict.fromkeys(parts))
        return _compact_blueprint_json_text(value)
    return str(value).strip()


_BLUEPRINT_LINK_TYPE_ALIASES = {
    "trigger_start": "trigger_start",
    "triggerstart": "trigger_start",
    "start": "trigger_start",
    "启动": "trigger_start",
    "触发启动": "trigger_start",
    "触发开始": "trigger_start",
    "开始触发": "trigger_start",
    "主线触发支线": "trigger_start",
    "触发支线启动": "trigger_start",
    "trigger_turn": "trigger_turn",
    "triggerturn": "trigger_turn",
    "turn": "trigger_turn",
    "触发转折": "trigger_turn",
    "推动转折": "trigger_turn",
    "转折触发": "trigger_turn",
    "constrain": "constrain",
    "constraint": "constrain",
    "约束": "constrain",
    "约束走向": "constrain",
    "限制": "constrain",
    "enable": "enable",
    "使能": "enable",
    "提供条件": "enable",
    "赋能": "enable",
    "助推": "enable",
    "conflict": "conflict",
    "冲突": "conflict",
    "制造冲突": "conflict",
    "引发冲突": "conflict",
    "feed_main": "feed_main",
    "feedmain": "feed_main",
    "feedback": "feed_main",
    "反哺主线": "feed_main",
    "回馈主线": "feed_main",
    "支线反哺主线": "feed_main",
    "推动主线": "feed_main",
    "reveal_key": "reveal_key",
    "revealkey": "reveal_key",
    "reveal": "reveal_key",
    "揭示关键": "reveal_key",
    "揭露关键": "reveal_key",
    "揭示关键信息": "reveal_key",
    "揭露关键信息": "reveal_key",
    "揭开真相": "reveal_key",
    "create_tension": "create_tension",
    "createtension": "create_tension",
    "tension": "create_tension",
    "制造张力": "create_tension",
    "增加张力": "create_tension",
    "制造紧张": "create_tension",
    "theme_echo": "theme_echo",
    "themeecho": "theme_echo",
    "echo": "theme_echo",
    "主题呼应": "theme_echo",
    "主题回响": "theme_echo",
    "主题映照": "theme_echo",
}

_BLUEPRINT_PRIORITY_ALIASES = {
    "primary": "primary",
    "major": "primary",
    "main": "primary",
    "主要": "primary",
    "主线级": "primary",
    "准主线": "primary",
    "核心": "primary",
    "normal": "normal",
    "medium": "normal",
    "regular": "normal",
    "普通": "normal",
    "常规": "normal",
    "一般": "normal",
    "background": "background",
    "minor": "background",
    "low": "background",
    "背景": "background",
    "背景线": "background",
    "点缀": "background",
}

_BLUEPRINT_RESOLUTION_TYPE_ALIASES = {
    "resolve": "resolve",
    "resolved": "resolve",
    "resolution": "resolve",
    "解决": "resolve",
    "收束": "resolve",
    "闭合": "resolve",
    "reveal": "reveal",
    "揭示": "reveal",
    "揭露": "reveal",
    "真相揭示": "reveal",
    "ascend": "ascend",
    "升华": "ascend",
    "价值升华": "ascend",
    "主题升华": "ascend",
    "merge": "merge",
    "并入": "merge",
    "合流": "merge",
    "并入主线": "merge",
    "回归主线": "merge",
}

_BLUEPRINT_SUSPENSE_TYPE_ALIASES = {
    "mystery": "mystery",
    "谜团": "mystery",
    "悬疑": "mystery",
    "秘密": "mystery",
    "疑问": "mystery",
    "crisis": "crisis",
    "危机": "crisis",
    "险情": "crisis",
    "危险": "crisis",
    "emotion": "emotion",
    "情感": "emotion",
    "感情": "emotion",
    "关系": "emotion",
    "choice": "choice",
    "选择": "choice",
    "抉择": "choice",
    "两难": "choice",
    "desire": "desire",
    "欲望": "desire",
    "愿望": "desire",
    "渴望": "desire",
}

_BLUEPRINT_URGENCY_ALIASES = {
    "critical": "critical",
    "urgent": "critical",
    "致命": "critical",
    "危急": "critical",
    "紧急": "critical",
    "关键": "critical",
    "high": "high",
    "important": "high",
    "高": "high",
    "较高": "high",
    "重要": "high",
    "normal": "normal",
    "medium": "normal",
    "普通": "normal",
    "常规": "normal",
    "一般": "normal",
    "low": "low",
    "低": "low",
    "较低": "low",
    "背景": "low",
}

_BLUEPRINT_STRAND_AFFINITY_KEY_ALIASES = {
    "quest": "quest",
    "探索": "quest",
    "调查": "quest",
    "求索": "quest",
    "追寻": "quest",
    "fire": "fire",
    "冲突": "fire",
    "危机": "fire",
    "行动": "fire",
    "火线": "fire",
    "constellation": "constellation",
    "关系": "constellation",
    "群像": "constellation",
    "命运": "constellation",
    "星群": "constellation",
}

_BLUEPRINT_LOCAL_SUBPLOT_MIN = {"simple": 1, "standard": 2, "complex": 3, "epic": 4}
_BLUEPRINT_VALID_SOURCE_TYPES = {"main_plot", "subplot", "turning_point"}
_BLUEPRINT_VALID_LINK_TYPES = {
    "trigger_start",
    "trigger_turn",
    "feed_main",
    "reveal_key",
    "constrain",
    "enable",
    "conflict",
    "create_tension",
    "theme_echo",
}
_BLUEPRINT_VALID_SUBPLOT_PRIORITIES = {"primary", "normal", "background"}
_BLUEPRINT_VALID_RESOLUTION_TYPES = {"resolve", "reveal", "ascend", "merge"}
_BLUEPRINT_VALID_SUSPENSE_TYPES = {"mystery", "crisis", "emotion", "choice", "desire"}
_BLUEPRINT_VALID_URGENCY_LEVELS = {"critical", "high", "normal", "low"}
_BLUEPRINT_LOCAL_SUBPLOT_TEMPLATES = (
    {
        "name": "主线真相追索线",
        "description": "围绕核心谜团与目标推进，提供可持续调查、发现与反转节点。",
        "resolution_type": "reveal",
        "feedback_type": "reveal_key",
    },
    {
        "name": "人物关系压力线",
        "description": "让关键人物关系在互信、误解、代价与选择中持续变化。",
        "resolution_type": "merge",
        "feedback_type": "feed_main",
    },
    {
        "name": "危机反噬线",
        "description": "让主线行动引发新的风险和代价，持续抬高局势压力。",
        "resolution_type": "resolve",
        "feedback_type": "feed_main",
    },
    {
        "name": "主题代价回收线",
        "description": "承载主题承诺与价值选择，在终局阶段完成回响和升华。",
        "resolution_type": "ascend",
        "feedback_type": "reveal_key",
    },
)


def _clamp_int(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _blueprint_enum_key(value: str) -> str:
    text = str(value or "").strip().strip("`'\"“”‘’")
    text = text.replace("：", ":").replace("－", "-").replace("—", "-")
    text = re.sub(r"\s+", "_", text.lower())
    return text.replace("-", "_")


def _canonicalize_blueprint_enum(
    value: Any,
    aliases: dict[str, str],
) -> tuple[Any, bool]:
    if not isinstance(value, str):
        return value, False
    raw = value.strip()
    if not raw:
        return value, False
    key = _blueprint_enum_key(raw)
    for candidate in (raw, raw.lower(), key, key.replace("_", "")):
        canonical = aliases.get(candidate)
        if canonical is not None:
            return canonical, canonical != value
    return value, False


def _canonicalize_blueprint_source_type(value: Any) -> tuple[Any, bool]:
    canonical, changed = _canonicalize_blueprint_enum(value, _BLUEPRINT_SOURCE_TYPE_ALIASES)
    if changed or canonical != value:
        return canonical, changed
    if not isinstance(value, str):
        return value, False

    raw = value.strip()
    key = _blueprint_enum_key(raw)
    compact = key.replace("_", "")
    if raw.startswith("主线") or compact.startswith(("mainplot", "mainline")):
        return "main_plot", value != "main_plot"
    if raw.startswith(("支线", "副线")) or compact.startswith(("subplot", "sideplot")):
        return "subplot", value != "subplot"
    if "转折" in raw or compact.startswith("turningpoint"):
        return "turning_point", value != "turning_point"
    return value, False


def _canonicalize_blueprint_link_type(value: Any) -> tuple[Any, bool]:
    canonical, changed = _canonicalize_blueprint_enum(value, _BLUEPRINT_LINK_TYPE_ALIASES)
    if changed or canonical != value:
        return canonical, changed
    if not isinstance(value, str):
        return value, False

    raw = value.strip()
    key = _blueprint_enum_key(raw)
    compact = key.replace("_", "")
    if any(marker in raw for marker in ("反哺", "回馈主线", "推动主线")):
        return "feed_main", value != "feed_main"
    if any(marker in raw for marker in ("揭示", "揭露", "揭开", "关键信息", "关键真相")):
        return "reveal_key", value != "reveal_key"
    if "触发" in raw and "转折" in raw:
        return "trigger_turn", value != "trigger_turn"
    if any(marker in raw for marker in ("触发", "启动", "开始")) or compact.startswith(
        "triggerstart"
    ):
        return "trigger_start", value != "trigger_start"
    if any(marker in raw for marker in ("张力", "紧张")):
        return "create_tension", value != "create_tension"
    if "冲突" in raw:
        return "conflict", value != "conflict"
    if any(marker in raw for marker in ("约束", "限制")):
        return "constrain", value != "constrain"
    if any(marker in raw for marker in ("条件", "赋能", "使能", "助推")):
        return "enable", value != "enable"
    if any(marker in raw for marker in ("主题", "呼应", "回响")):
        return "theme_echo", value != "theme_echo"
    return value, False


def _canonicalize_blueprint_main_target(value: Any) -> tuple[Any, bool]:
    if not isinstance(value, str):
        return value, False
    raw = value.strip()
    if not raw:
        return value, False
    key = _blueprint_enum_key(raw)
    if raw in {"主线", "主线事件", "主线剧情", "主情节"} or key in {
        "main",
        "main_plot",
        "mainplot",
        "mainline",
        "main_line",
    }:
        return "主线", value != "主线"
    return value, False


def _blueprint_reference_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[\s`'\"“”‘’《》〈〉「」『』（）()【】\[\]{}·•._\-]+", "", text)
    return text


def _collect_blueprint_subplot_names(payload: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for item in payload.get("subplot_plan") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _canonicalize_blueprint_subplot_reference(
    value: Any,
    *,
    subplot_names: list[str],
) -> tuple[Any, bool]:
    main_target, main_changed = _canonicalize_blueprint_main_target(value)
    if main_target == "主线":
        return main_target, main_changed
    if not isinstance(value, str):
        return value, False

    raw = value.strip()
    if not raw:
        return value, False
    if raw in subplot_names:
        return raw, raw != value

    reference_key = _blueprint_reference_key(raw)
    if reference_key:
        for name in subplot_names:
            subplot_key = _blueprint_reference_key(name)
            if reference_key == subplot_key:
                return name, name != value
        for name in subplot_names:
            subplot_key = _blueprint_reference_key(name)
            if len(reference_key) >= 3 and (
                reference_key in subplot_key or subplot_key in reference_key
            ):
                return name, name != value

    return "主线", True


def _coerce_blueprint_chapter_value(value: Any, *, allow_zero: bool = False) -> tuple[Any, bool]:
    if isinstance(value, bool) or value is None:
        return value, False
    if isinstance(value, int):
        return value, False
    if isinstance(value, float) and value.is_integer():
        parsed = int(value)
        return parsed, parsed != value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return value, False
        match = re.search(r"\d+", raw)
        if match:
            parsed = int(match.group(0))
            if parsed == 0 and not allow_zero:
                parsed = 1
            return parsed, True
    return value, False


def _coerce_blueprint_chapter_int(value: Any) -> int | None:
    parsed, _ = _coerce_blueprint_chapter_value(value)
    if isinstance(parsed, bool) or parsed is None:
        return None
    if isinstance(parsed, int):
        return parsed
    return None


def _blueprint_milestone_chapter_field_name(key: Any) -> str | None:
    text = str(key or "").strip().strip("`'\"“”‘’")
    text = re.sub(r"^[\s.。；;:：,，]+", "", text).strip().lower()
    if text in _BLUEPRINT_MILESTONE_CHAPTER_FIELDS:
        return text
    return None


def _should_apply_leaked_milestone_chapter(
    normalized: dict[str, Any],
    field_name: str,
    leaked_value: int,
) -> bool:
    current = _coerce_blueprint_chapter_int(normalized.get(field_name))
    if current is None:
        return True
    if field_name == "chapter_end":
        chapter_start = _coerce_blueprint_chapter_int(normalized.get("chapter_start"))
        return chapter_start is not None and current < chapter_start <= leaked_value
    if field_name == "chapter_start":
        chapter_end = _coerce_blueprint_chapter_int(normalized.get("chapter_end"))
        return chapter_end is not None and leaked_value <= chapter_end < current
    return False


def _extract_leaked_milestone_chapter_fields(
    description: Any,
) -> tuple[Any, dict[str, int], bool]:
    if not isinstance(description, str):
        return description, {}, False

    text = description.strip()
    fields: dict[str, int] = {}
    changed = False
    while text:
        match = _BLUEPRINT_LEAKED_MILESTONE_FIELD_RE.search(text)
        if match is None:
            break
        fields[match.group(1).lower()] = int(match.group(2))
        text = text[: match.start()].rstrip("；; \t\r\n")
        changed = True

    return text, fields, changed


def _next_blueprint_milestone_start(
    milestones: list[dict[str, Any]],
    index: int,
) -> int | None:
    for item in milestones[index + 1 :]:
        chapter_start = _coerce_blueprint_chapter_int(item.get("chapter_start"))
        if chapter_start is not None:
            return chapter_start
    return None


def _repair_blueprint_arc_milestone_ranges(
    milestones: list[dict[str, Any]],
    *,
    total_chapters: int | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    changed = False
    repaired: list[dict[str, Any]] = []

    for index, milestone in enumerate(milestones):
        normalized = dict(milestone)
        chapter_start = _coerce_blueprint_chapter_int(normalized.get("chapter_start"))
        chapter_end = _coerce_blueprint_chapter_int(normalized.get("chapter_end"))

        if chapter_start is not None and normalized.get("chapter_start") != chapter_start:
            normalized["chapter_start"] = chapter_start
            changed = True
        if chapter_end is not None and normalized.get("chapter_end") != chapter_end:
            normalized["chapter_end"] = chapter_end
            changed = True

        if chapter_start is not None and (chapter_end is None or chapter_end < chapter_start):
            next_start = _next_blueprint_milestone_start(milestones, index)
            if next_start is not None and next_start > chapter_start:
                repaired_end = next_start - 1
            elif total_chapters is not None and total_chapters >= chapter_start:
                repaired_end = total_chapters
            else:
                repaired_end = chapter_start
            if chapter_end != repaired_end:
                normalized["chapter_end"] = repaired_end
                changed = True

        repaired.append(normalized)

    return repaired, changed


def _normalize_blueprint_chapter_list(value: Any) -> tuple[Any, bool]:
    if not isinstance(value, list):
        return value, False
    changed = False
    normalized: list[Any] = []
    for item in value:
        chapter, item_changed = _coerce_blueprint_chapter_value(item)
        normalized.append(chapter)
        changed = changed or item_changed
    return (normalized, True) if changed else (value, False)


def _coerce_blueprint_text_list(value: Any) -> tuple[Any, bool]:
    if value is None:
        return [], True
    if isinstance(value, str):
        parsed = parse_json_object_string(value)
        if isinstance(parsed, list):
            value = parsed
        else:
            text = value.strip()
            return ([text] if text else []), True
    if not isinstance(value, list):
        text = _coerce_blueprint_text_field(value)
        return ([text] if text else []), True

    changed = False
    items: list[str] = []
    for item in value:
        text = _coerce_blueprint_text_field(item)
        if text:
            items.append(text)
        if not isinstance(item, str) or item != text:
            changed = True
    return (items, True) if changed else (value, False)


def _canonicalize_blueprint_resolution_target(value: Any) -> tuple[Any, bool]:
    if not isinstance(value, str):
        return value, False
    raw = value.strip()
    if not raw:
        return value, False
    key = _blueprint_enum_key(raw)
    if key in {"theme_echo", "themeecho", "主题呼应", "主题回响", "主题升华"}:
        return "theme_echo", value != "theme_echo"

    for prefix in ("character_fate:", "character_fate：", "角色命运:", "角色命运："):
        if raw.startswith(prefix):
            name = raw[len(prefix) :].strip()
            if name:
                target = f"character_fate:{name}"
                return target, target != value

    if raw.startswith(("主线转折", "关键转折", "main_turning_point")):
        match = re.search(r"\d+", raw)
        if match:
            target = f"main_turning_point:{int(match.group(0))}"
            return target, target != value
    return value, False


def _normalize_blueprint_weave_link(item: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    normalized = dict(item)
    changed = False

    source_type, field_changed = _canonicalize_blueprint_source_type(normalized.get("source_type"))
    if field_changed:
        normalized["source_type"] = source_type
        changed = True

    link_type, field_changed = _canonicalize_blueprint_link_type(normalized.get("link_type"))
    if field_changed:
        normalized["link_type"] = link_type
        changed = True

    target_subplot, field_changed = _canonicalize_blueprint_main_target(
        normalized.get("target_subplot")
    )
    if field_changed:
        normalized["target_subplot"] = target_subplot
        changed = True

    trigger_chapter, field_changed = _coerce_blueprint_chapter_value(
        normalized.get("trigger_chapter"), allow_zero=True
    )
    if field_changed:
        normalized["trigger_chapter"] = trigger_chapter
        changed = True

    for key in list(normalized):
        if key not in _BLUEPRINT_WEAVE_LINK_FIELDS:
            normalized.pop(key, None)
            changed = True

    return normalized, changed


def _normalize_blueprint_weave_links(value: Any) -> tuple[Any, bool]:
    if value is None:
        return [], True
    if isinstance(value, dict):
        normalized, _ = _normalize_blueprint_weave_link(value)
        return [normalized], True
    if isinstance(value, str):
        parsed = parse_json_object_string(value)
        if isinstance(parsed, list):
            value = parsed
        elif isinstance(parsed, dict):
            normalized, _ = _normalize_blueprint_weave_link(parsed)
            return [normalized], True
        else:
            return [], True
    if not isinstance(value, list):
        return value, False

    changed = False
    links: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            normalized, item_changed = _normalize_blueprint_weave_link(item)
            links.append(normalized)
            changed = changed or item_changed
            continue
        if isinstance(item, str):
            parsed = parse_json_object_string(item)
            if parsed is not None:
                normalized, _ = _normalize_blueprint_weave_link(parsed)
                links.append(normalized)
                changed = True
                continue
        changed = True
    if not changed:
        return value, False
    return links, True


def _blueprint_event_weave_link_candidate(
    raw_event: dict[str, Any],
    normalized_event: dict[str, Any],
    *,
    subplot_name: str,
) -> dict[str, Any] | None:
    if not any(key in raw_event for key in _BLUEPRINT_EVENT_WEAVE_HINT_FIELDS):
        return None

    link: dict[str, Any] = {
        key: raw_event[key] for key in _BLUEPRINT_WEAVE_LINK_FIELDS if key in raw_event
    }
    if "trigger_chapter" not in link and "chapter_number" in normalized_event:
        link["trigger_chapter"] = normalized_event.get("chapter_number")
    if "target_subplot" not in link and subplot_name:
        link["target_subplot"] = subplot_name

    event_text = str(normalized_event.get("event") or raw_event.get("description") or "").strip()
    if "source_ref" not in link and event_text:
        link["source_ref"] = event_text
    if "description" not in link:
        for key in ("description", "weave_notes", "event"):
            text = str(raw_event.get(key) or normalized_event.get(key) or "").strip()
            if text:
                link["description"] = text
                break

    normalized_link, _ = _normalize_blueprint_weave_link(link)
    meaningful = any(
        str(normalized_link.get(key) or "").strip()
        for key in ("source_type", "source_ref", "target_subplot", "link_type", "description")
    )
    return normalized_link if meaningful else None


def _merge_blueprint_weave_links(
    existing: list[dict[str, Any]],
    additions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int, str]] = set()
    for link in [*existing, *additions]:
        if not isinstance(link, dict):
            continue
        key = (
            str(link.get("source_type") or ""),
            str(link.get("source_ref") or ""),
            str(link.get("target_subplot") or ""),
            _safe_blueprint_int(link.get("trigger_chapter"), 0),
            str(link.get("link_type") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(link)
    return merged


def _normalize_blueprint_chapter_events(
    value: Any,
    *,
    subplot_name: str = "",
) -> tuple[Any, list[dict[str, Any]], bool]:
    if value is None:
        return [], [], True
    if isinstance(value, dict):
        value = [value]
        changed = True
    else:
        changed = False
    if isinstance(value, str):
        parsed = parse_json_object_string(value)
        if isinstance(parsed, list):
            value = parsed
            changed = True
        elif isinstance(parsed, dict):
            value = [parsed]
            changed = True
        else:
            return [], [], True
    if not isinstance(value, list):
        return value, [], False

    events: list[dict[str, Any]] = []
    extracted_links: list[dict[str, Any]] = []
    for item in value:
        event = item
        if isinstance(event, str):
            parsed = parse_json_object_string(event)
            if parsed is None:
                changed = True
                continue
            event = parsed
            changed = True
        if not isinstance(event, dict):
            changed = True
            continue

        normalized = dict(event)
        if "chapter_number" not in normalized and "trigger_chapter" in normalized:
            normalized["chapter_number"] = normalized.get("trigger_chapter")
            changed = True
        if "event" not in normalized:
            for alias in ("event_description", "summary", "beat", "plot_event", "description"):
                text = str(normalized.get(alias) or "").strip()
                if text:
                    normalized["event"] = text
                    changed = True
                    break

        if "chapter_number" in normalized:
            chapter_number, chapter_changed = _coerce_blueprint_chapter_value(
                normalized.get("chapter_number")
            )
            if chapter_changed:
                normalized["chapter_number"] = chapter_number
                changed = True

        link = _blueprint_event_weave_link_candidate(
            event,
            normalized,
            subplot_name=subplot_name,
        )
        if link is not None:
            extracted_links.append(link)
            changed = True

        if "depends_on" in normalized:
            deps = coerce_dependency_ref_list(normalized.get("depends_on"))
            if deps != normalized.get("depends_on"):
                normalized["depends_on"] = deps
                changed = True
        elif "dependsOn" in normalized:
            normalized["depends_on"] = coerce_dependency_ref_list(normalized.pop("dependsOn"))
            changed = True

        if "weave_notes" not in normalized:
            for alias in ("weave_note", "notes", "interaction_notes"):
                if alias in normalized:
                    normalized["weave_notes"] = str(normalized.pop(alias) or "").strip()
                    changed = True
                    break
        for key in list(normalized):
            if key not in _BLUEPRINT_CHAPTER_EVENT_FIELDS:
                normalized.pop(key, None)
                changed = True
        events.append(normalized)

    if not changed:
        return value, [], False
    return events, extracted_links, True


def _normalize_blueprint_subplot_plan(value: Any) -> tuple[Any, bool]:
    if value is None:
        return [], True
    if not isinstance(value, list):
        return value, False

    changed = False
    subplots: list[dict[str, Any]] = []
    for item in value:
        subplot = item
        if isinstance(subplot, str):
            parsed = parse_json_object_string(subplot)
            if parsed is None:
                changed = True
                continue
            subplot = parsed
            changed = True
        if not isinstance(subplot, dict):
            changed = True
            continue

        normalized = dict(subplot)
        if "name" not in normalized:
            for alias in ("subplot_name", "title"):
                if alias in normalized:
                    normalized["name"] = str(normalized.pop(alias) or "").strip()
                    changed = True
                    break
        if "involved_chapters" in normalized:
            involved_chapters, chapters_changed = _normalize_blueprint_chapter_list(
                normalized.get("involved_chapters")
            )
            if chapters_changed:
                normalized["involved_chapters"] = involved_chapters
                changed = True

        if "priority" in normalized:
            priority, priority_changed = _canonicalize_blueprint_enum(
                normalized.get("priority"), _BLUEPRINT_PRIORITY_ALIASES
            )
            if priority_changed:
                normalized["priority"] = priority
                changed = True

        if "resolution_chapter" in normalized:
            resolution_chapter, chapter_changed = _coerce_blueprint_chapter_value(
                normalized.get("resolution_chapter"), allow_zero=True
            )
            if chapter_changed:
                normalized["resolution_chapter"] = resolution_chapter
                changed = True

        if "resolution_target" in normalized:
            resolution_target, target_changed = _canonicalize_blueprint_resolution_target(
                normalized.get("resolution_target")
            )
            if target_changed:
                normalized["resolution_target"] = resolution_target
                changed = True

        if "resolution_type" in normalized:
            resolution_type, type_changed = _canonicalize_blueprint_enum(
                normalized.get("resolution_type"), _BLUEPRINT_RESOLUTION_TYPE_ALIASES
            )
            if type_changed:
                normalized["resolution_type"] = resolution_type
                changed = True

        extracted_weave_links: list[dict[str, Any]] = []
        subplot_name = str(normalized.get("name") or "").strip()
        if "chapter_events" in normalized:
            chapter_events, extracted_weave_links, events_changed = (
                _normalize_blueprint_chapter_events(
                    normalized.get("chapter_events"),
                    subplot_name=subplot_name,
                )
            )
            if events_changed:
                normalized["chapter_events"] = chapter_events
                changed = True

        if "weave_links" in normalized:
            weave_links, links_changed = _normalize_blueprint_weave_links(
                normalized.get("weave_links")
            )
            if links_changed:
                normalized["weave_links"] = weave_links
                changed = True
        elif extracted_weave_links:
            weave_links = []
            normalized["weave_links"] = weave_links
            changed = True
        else:
            weave_links = None

        if extracted_weave_links:
            normalized["weave_links"] = _merge_blueprint_weave_links(
                weave_links if isinstance(weave_links, list) else [],
                extracted_weave_links,
            )
            changed = True

        for key in list(normalized):
            if key not in _BLUEPRINT_SUBPLOT_FIELDS:
                normalized.pop(key, None)
                changed = True

        subplots.append(normalized)

    if not changed:
        return value, False
    return subplots, True


_BLUEPRINT_EMOTIONAL_ARC_FIELDS = {
    "arc_name",
    "emotion_type",
    "peak_chapters",
    "valley_chapters",
    "description",
    "related_characters",
    "related_subplots",
}

_BLUEPRINT_CAUSAL_CHAIN_FIELDS = {
    "chain_name",
    "trigger_chapter",
    "trigger_action",
    "intermediate_chapters",
    "payoff_chapter",
    "payoff_event",
    "escalation",
    "involved_subplots",
}

_BLUEPRINT_SUBPLOT_COLLISION_FIELDS = {
    "collision_chapter",
    "involved_subplots",
    "collision_type",
    "outcome",
    "ripple_effects",
    "setup_chapters",
}

_BLUEPRINT_SUBVERSION_POINT_FIELDS = {
    "chapter",
    "expected_outcome",
    "actual_outcome",
    "setup_chapters",
    "justification",
    "related_subplots",
}


def _normalize_blueprint_emotional_arcs(value: Any) -> tuple[Any, bool]:
    if value is None:
        return [], True
    if not isinstance(value, list):
        return value, False

    changed = False
    items: list[dict[str, Any]] = []
    for item in value:
        arc = item
        if isinstance(arc, str):
            parsed = parse_json_object_string(arc)
            if parsed is None:
                changed = True
                continue
            arc = parsed
            changed = True
        if not isinstance(arc, dict):
            changed = True
            continue

        normalized = dict(arc)
        if "arc_name" not in normalized:
            for alias in ("name", "emotional_arc_name", "arc"):
                if alias in normalized:
                    normalized["arc_name"] = str(normalized.pop(alias) or "").strip()
                    changed = True
                    break

        if "peak_chapters" in normalized:
            chapters, chapters_changed = _normalize_blueprint_chapter_list(
                normalized.get("peak_chapters")
            )
            if chapters_changed:
                normalized["peak_chapters"] = chapters
                changed = True

        if "valley_chapters" in normalized:
            chapters, chapters_changed = _normalize_blueprint_chapter_list(
                normalized.get("valley_chapters")
            )
            if chapters_changed:
                normalized["valley_chapters"] = chapters
                changed = True

        for list_field in ("related_characters", "related_subplots"):
            if list_field in normalized:
                coerced, list_changed = _coerce_blueprint_text_list(normalized.get(list_field))
                if list_changed:
                    normalized[list_field] = coerced
                    changed = True

        for key in list(normalized):
            if key not in _BLUEPRINT_EMOTIONAL_ARC_FIELDS:
                normalized.pop(key, None)
                changed = True

        items.append(normalized)

    if not changed:
        return value, False
    return items, True


def _normalize_blueprint_causal_chains(value: Any) -> tuple[Any, bool]:
    if value is None:
        return [], True
    if not isinstance(value, list):
        return value, False

    changed = False
    items: list[dict[str, Any]] = []
    for item in value:
        chain = item
        if isinstance(chain, str):
            parsed = parse_json_object_string(chain)
            if parsed is None:
                changed = True
                continue
            chain = parsed
            changed = True
        if not isinstance(chain, dict):
            changed = True
            continue

        normalized = dict(chain)
        if "chain_name" not in normalized:
            for alias in ("name", "causal_chain_name", "causal_name"):
                if alias in normalized:
                    normalized["chain_name"] = str(normalized.pop(alias) or "").strip()
                    changed = True
                    break

        for int_field in ("trigger_chapter", "payoff_chapter"):
            if int_field in normalized:
                coerced = _safe_blueprint_int(normalized.get(int_field))
                if coerced != normalized.get(int_field):
                    normalized[int_field] = coerced
                    changed = True

        if "intermediate_chapters" in normalized:
            chapters, chapters_changed = _normalize_blueprint_chapter_list(
                normalized.get("intermediate_chapters")
            )
            if chapters_changed:
                normalized["intermediate_chapters"] = chapters
                changed = True

        if "involved_subplots" in normalized:
            coerced, list_changed = _coerce_blueprint_text_list(normalized.get("involved_subplots"))
            if list_changed:
                normalized["involved_subplots"] = coerced
                changed = True

        for key in list(normalized):
            if key not in _BLUEPRINT_CAUSAL_CHAIN_FIELDS:
                normalized.pop(key, None)
                changed = True

        items.append(normalized)

    if not changed:
        return value, False
    return items, True


def _normalize_blueprint_subplot_collisions(value: Any) -> tuple[Any, bool]:
    if value is None:
        return [], True
    if not isinstance(value, list):
        return value, False

    changed = False
    items: list[dict[str, Any]] = []
    for item in value:
        collision = item
        if isinstance(collision, str):
            parsed = parse_json_object_string(collision)
            if parsed is None:
                changed = True
                continue
            collision = parsed
            changed = True
        if not isinstance(collision, dict):
            changed = True
            continue

        normalized = dict(collision)
        if "collision_chapter" not in normalized:
            for alias in ("chapter", "chapter_number", "collision_at"):
                if alias in normalized:
                    normalized["collision_chapter"] = _safe_blueprint_int(normalized.pop(alias))
                    changed = True
                    break

        if "collision_chapter" in normalized:
            coerced = _safe_blueprint_int(normalized.get("collision_chapter"))
            if coerced != normalized.get("collision_chapter"):
                normalized["collision_chapter"] = coerced
                changed = True

        for list_field in ("involved_subplots", "ripple_effects"):
            if list_field in normalized:
                coerced, list_changed = _coerce_blueprint_text_list(normalized.get(list_field))
                if list_changed:
                    normalized[list_field] = coerced
                    changed = True

        if "setup_chapters" in normalized:
            chapters, chapters_changed = _normalize_blueprint_chapter_list(
                normalized.get("setup_chapters")
            )
            if chapters_changed:
                normalized["setup_chapters"] = chapters
                changed = True

        for key in list(normalized):
            if key not in _BLUEPRINT_SUBPLOT_COLLISION_FIELDS:
                normalized.pop(key, None)
                changed = True

        items.append(normalized)

    if not changed:
        return value, False
    return items, True


def _normalize_blueprint_subversion_points(value: Any) -> tuple[Any, bool]:
    if value is None:
        return [], True
    if not isinstance(value, list):
        return value, False

    changed = False
    items: list[dict[str, Any]] = []
    for item in value:
        point = item
        if isinstance(point, str):
            parsed = parse_json_object_string(point)
            if parsed is None:
                changed = True
                continue
            point = parsed
            changed = True
        if not isinstance(point, dict):
            changed = True
            continue

        normalized = dict(point)
        if "chapter" not in normalized:
            for alias in ("chapter_number", "subversion_chapter", "point_chapter"):
                if alias in normalized:
                    normalized["chapter"] = _safe_blueprint_int(normalized.pop(alias))
                    changed = True
                    break

        if "chapter" in normalized:
            coerced = _safe_blueprint_int(normalized.get("chapter"))
            if coerced != normalized.get("chapter"):
                normalized["chapter"] = coerced
                changed = True

        if "setup_chapters" in normalized:
            chapters, chapters_changed = _normalize_blueprint_chapter_list(
                normalized.get("setup_chapters")
            )
            if chapters_changed:
                normalized["setup_chapters"] = chapters
                changed = True

        if "related_subplots" in normalized:
            coerced, list_changed = _coerce_blueprint_text_list(normalized.get("related_subplots"))
            if list_changed:
                normalized["related_subplots"] = coerced
                changed = True

        for key in list(normalized):
            if key not in _BLUEPRINT_SUBVERSION_POINT_FIELDS:
                normalized.pop(key, None)
                changed = True

        items.append(normalized)

    if not changed:
        return value, False
    return items, True


def _normalize_blueprint_arc_milestones(
    value: Any,
    *,
    total_chapters: int | None = None,
) -> tuple[Any, bool]:
    if value is None:
        return [], True
    if isinstance(value, dict):
        value = [value]
        changed = True
    else:
        changed = False
    if isinstance(value, str):
        parsed = parse_json_object_string(value)
        if isinstance(parsed, list):
            value = parsed
            changed = True
        elif isinstance(parsed, dict):
            value = [parsed]
            changed = True
        else:
            return [{"chapter_start": 1, "chapter_end": 1, "description": value}], True
    if not isinstance(value, list):
        return value, False

    milestones: list[dict[str, Any]] = []
    for item in value:
        milestone = item
        if isinstance(milestone, str):
            parsed = parse_json_object_string(milestone)
            if isinstance(parsed, dict):
                milestone = parsed
            else:
                milestones.append(
                    {"chapter_start": 1, "chapter_end": 1, "description": str(milestone)}
                )
                changed = True
                continue
            changed = True
        if not isinstance(milestone, dict):
            changed = True
            continue

        normalized = dict(milestone)
        if "chapter_start" not in normalized:
            for alias in ("start_chapter", "from_chapter", "chapter"):
                if alias in normalized:
                    normalized["chapter_start"] = normalized.pop(alias)
                    changed = True
                    break
        if "chapter_end" not in normalized:
            for alias in ("end_chapter", "to_chapter"):
                if alias in normalized:
                    normalized["chapter_end"] = normalized.pop(alias)
                    changed = True
                    break
        if "description" not in normalized:
            for alias in ("summary", "beat", "change", "arc_change"):
                text = str(normalized.get(alias) or "").strip()
                if text:
                    normalized["description"] = text
                    changed = True
                    break

        for key in list(normalized):
            field_name = _blueprint_milestone_chapter_field_name(key)
            if field_name is None or key == field_name:
                continue
            raw = normalized.pop(key, None)
            leaked_value = _coerce_blueprint_chapter_int(raw)
            if leaked_value is not None and _should_apply_leaked_milestone_chapter(
                normalized,
                field_name,
                leaked_value,
            ):
                normalized[field_name] = leaked_value
            changed = True

        description, leaked_fields, description_changed = _extract_leaked_milestone_chapter_fields(
            normalized.get("description")
        )
        if description_changed:
            normalized["description"] = description
            changed = True
        for field_name, leaked_value in leaked_fields.items():
            if _should_apply_leaked_milestone_chapter(normalized, field_name, leaked_value):
                normalized[field_name] = leaked_value
                changed = True

        extra_notes: list[str] = []
        for key in list(normalized):
            if key in _BLUEPRINT_ARC_MILESTONE_FIELDS:
                continue
            raw = normalized.pop(key, None)
            text = str(raw or "").strip()
            if text:
                extra_notes.append(f"{key}: {text}")
            changed = True
        if extra_notes:
            base = str(normalized.get("description") or "").strip()
            normalized["description"] = "；".join([part for part in [base, *extra_notes] if part])
            changed = True
        milestones.append(normalized)

    milestones, ranges_changed = _repair_blueprint_arc_milestone_ranges(
        milestones,
        total_chapters=total_chapters,
    )
    changed = changed or ranges_changed

    if not changed:
        return value, False
    return milestones, True


def _normalize_blueprint_narrative_phases(value: Any) -> tuple[Any, bool]:
    if value is None:
        return [], True
    if not isinstance(value, list):
        return value, False

    changed = False
    phases: list[dict[str, Any]] = []
    for item in value:
        phase = item
        if isinstance(phase, str):
            parsed = parse_json_object_string(phase)
            if parsed is None:
                changed = True
                continue
            phase = parsed
            changed = True
        if not isinstance(phase, dict):
            changed = True
            continue

        normalized = dict(phase)
        for alias, canonical in _BLUEPRINT_NARRATIVE_PHASE_ALIASES.items():
            if alias not in normalized:
                continue
            raw = normalized.pop(alias)
            if canonical not in normalized:
                normalized[canonical] = raw
            elif raw not in (None, "", [], {}):
                existing = _coerce_blueprint_text_field(normalized.get(canonical))
                extra = _coerce_blueprint_text_field(raw)
                normalized[canonical] = "；".join(part for part in (existing, extra) if part)
            changed = True

        for key in list(_BLUEPRINT_NARRATIVE_PHASE_LIST_FIELDS):
            if key not in normalized:
                continue
            coerced, list_changed = _coerce_blueprint_text_list(normalized.get(key))
            if list_changed:
                normalized[key] = coerced
                changed = True

        extra_notes: list[str] = []
        for key in list(normalized):
            if key in _BLUEPRINT_NARRATIVE_PHASE_FIELDS:
                continue
            raw = normalized.pop(key, None)
            text = _coerce_blueprint_text_field(raw)
            if text:
                extra_notes.append(text)
            changed = True
        if extra_notes:
            base = str(normalized.get("description") or "").strip()
            normalized["description"] = "；".join(part for part in [base, *extra_notes] if part)
            changed = True
        phases.append(normalized)

    if not changed:
        return value, False
    return phases, True


def _normalize_blueprint_character_arcs(
    value: Any,
    *,
    total_chapters: int | None = None,
) -> tuple[Any, bool]:
    if value is None:
        return [], True
    if not isinstance(value, list):
        return value, False

    changed = False
    arcs: list[dict[str, Any]] = []
    for item in value:
        arc = item
        if isinstance(arc, str):
            parsed = parse_json_object_string(arc)
            if parsed is None:
                changed = True
                continue
            arc = parsed
            changed = True
        if not isinstance(arc, dict):
            changed = True
            continue

        normalized = dict(arc)
        if "character" not in normalized:
            for alias in ("character_name", "name", "role_name"):
                if alias in normalized:
                    normalized["character"] = str(normalized.pop(alias) or "").strip()
                    changed = True
                    break
        if "arc_summary" not in normalized:
            for alias in ("summary", "arc", "description"):
                text = str(normalized.get(alias) or "").strip()
                if text:
                    normalized["arc_summary"] = text
                    changed = True
                    break

        if "milestones" in normalized:
            milestones, milestones_changed = _normalize_blueprint_arc_milestones(
                normalized.get("milestones"),
                total_chapters=total_chapters,
            )
            if milestones_changed:
                normalized["milestones"] = milestones
                changed = True

        for key in list(normalized):
            if key not in _BLUEPRINT_CHARACTER_ARC_FIELDS:
                normalized.pop(key, None)
                changed = True
        arcs.append(normalized)

    if not changed:
        return value, False
    return arcs, True


def _normalize_blueprint_strand_affinity(value: Any) -> tuple[Any, bool]:
    if not isinstance(value, dict):
        return value, False

    changed = False
    normalized: dict[str, Any] = {}
    for key, weight in value.items():
        canonical_key, key_changed = _canonicalize_blueprint_enum(
            str(key), _BLUEPRINT_STRAND_AFFINITY_KEY_ALIASES
        )
        if key_changed:
            changed = True
        if canonical_key in normalized:
            try:
                normalized[canonical_key] = float(normalized[canonical_key]) + float(weight)
            except (TypeError, ValueError):
                normalized[canonical_key] = weight
            changed = True
        else:
            normalized[canonical_key] = weight

    if not changed:
        return value, False
    return normalized, True


def _normalize_blueprint_suspense_schedule(value: Any) -> tuple[Any, bool]:
    if value is None:
        return [], True
    if not isinstance(value, list):
        return value, False

    changed = False
    items: list[dict[str, Any]] = []
    for item in value:
        suspense = item
        if isinstance(suspense, str):
            parsed = parse_json_object_string(suspense)
            if parsed is None:
                changed = True
                continue
            suspense = parsed
            changed = True
        if not isinstance(suspense, dict):
            changed = True
            continue

        normalized = dict(suspense)
        if "suspense_type" in normalized:
            suspense_type, type_changed = _canonicalize_blueprint_enum(
                normalized.get("suspense_type"), _BLUEPRINT_SUSPENSE_TYPE_ALIASES
            )
            if type_changed:
                normalized["suspense_type"] = suspense_type
                changed = True

        if "urgency_level" in normalized:
            urgency, urgency_changed = _canonicalize_blueprint_enum(
                normalized.get("urgency_level"), _BLUEPRINT_URGENCY_ALIASES
            )
            if urgency_changed:
                normalized["urgency_level"] = urgency
                changed = True

        for chapter_key in ("introduce_chapter", "resolve_chapter"):
            if chapter_key in normalized:
                chapter_value, chapter_changed = _coerce_blueprint_chapter_value(
                    normalized.get(chapter_key), allow_zero=chapter_key == "resolve_chapter"
                )
                if chapter_changed:
                    normalized[chapter_key] = chapter_value
                    changed = True

        if "related_subplot" in normalized:
            related_subplot, target_changed = _canonicalize_blueprint_main_target(
                normalized.get("related_subplot")
            )
            if target_changed:
                normalized["related_subplot"] = related_subplot
                changed = True

        if "strand_affinity" in normalized:
            affinity, affinity_changed = _normalize_blueprint_strand_affinity(
                normalized.get("strand_affinity")
            )
            if affinity_changed:
                normalized["strand_affinity"] = affinity
                changed = True

        items.append(normalized)

    if not changed:
        return value, False
    return items, True


def _normalize_blueprint_suspense_references(payload: dict[str, Any]) -> tuple[Any, bool]:
    schedule = payload.get("suspense_schedule")
    if not isinstance(schedule, list):
        return schedule, False

    subplot_names = _collect_blueprint_subplot_names(payload)
    changed = False
    normalized_schedule: list[Any] = []
    for item in schedule:
        if not isinstance(item, dict):
            normalized_schedule.append(item)
            continue
        related_subplot, item_changed = _canonicalize_blueprint_subplot_reference(
            item.get("related_subplot"),
            subplot_names=subplot_names,
        )
        if not item_changed:
            normalized_schedule.append(item)
            continue
        normalized = dict(item)
        normalized["related_subplot"] = related_subplot
        normalized_schedule.append(normalized)
        changed = True

    if not changed:
        return schedule, False
    return normalized_schedule, True


def _pre_normalize_blueprint_payload(
    payload: Any,
    *,
    total_chapters: int,
) -> Any:
    """Pre-normalize blueprint payload to fix common LLM field name mismatches.

    Handles volumes with non-standard field names and common nested shape
    drift inside ``subplot_plan`` before ``NarrativeBlueprint`` validates.
    """
    if not isinstance(payload, dict):
        return payload

    result: dict[str, Any] | None = None

    for field_name in _BLUEPRINT_GLOBAL_TEXT_REF_FIELDS:
        if field_name not in payload:
            continue
        raw_text = payload.get(field_name)
        if raw_text is None or isinstance(raw_text, str):
            continue
        if result is None:
            result = dict(payload)
        result[field_name] = _coerce_blueprint_text_field(raw_text)

    volumes = payload.get("volumes")
    if isinstance(volumes, list) and volumes:
        needs_norm = any(
            isinstance(v, dict)
            and (
                "volume_number" not in v
                or "start_chapter" not in v
                or "end_chapter" not in v
                or "volume_name" in v
                or "name" in v
                or "chapter_start" in v
                or "chapter_end" in v
            )
            for v in volumes
        )

        if needs_norm:
            n = len(volumes)
            size = max(1, total_chapters // n)

            new_volumes: list[Any] = []
            for i, v in enumerate(volumes):
                if not isinstance(v, dict):
                    new_volumes.append(v)
                    continue
                vol: dict[str, Any] = dict(v)

                # Map volume_name / name → title (keep title if already present)
                if "title" not in vol:
                    for alt in ("volume_name", "name"):
                        if alt in vol:
                            vol["title"] = str(vol.pop(alt) or "")
                            break

                # Remove leftover alias keys to satisfy VolumeOutline extra="forbid"
                for alt in ("volume_name", "name"):
                    vol.pop(alt, None)

                # Normalise chapter range aliases
                if "start_chapter" not in vol and "chapter_start" in vol:
                    vol["start_chapter"] = vol.pop("chapter_start")
                if "end_chapter" not in vol and "chapter_end" in vol:
                    vol["end_chapter"] = vol.pop("chapter_end")

                # Auto-fill required fields that are still missing
                if "volume_number" not in vol:
                    vol["volume_number"] = i + 1
                if "start_chapter" not in vol:
                    vol["start_chapter"] = i * size + 1
                if "end_chapter" not in vol:
                    vol["end_chapter"] = min((i + 1) * size, total_chapters)

                new_volumes.append(vol)

            if result is None:
                result = dict(payload)
            result["volumes"] = new_volumes

    if "narrative_phases" in payload:
        narrative_phases, phases_changed = _normalize_blueprint_narrative_phases(
            payload.get("narrative_phases")
        )
        if phases_changed:
            if result is None:
                result = dict(payload)
            result["narrative_phases"] = narrative_phases

    if "subplot_plan" in payload:
        subplot_plan, subplot_changed = _normalize_blueprint_subplot_plan(
            payload.get("subplot_plan")
        )
        if subplot_changed:
            if result is None:
                result = dict(payload)
            result["subplot_plan"] = subplot_plan

    if "character_arcs" in payload:
        character_arcs, character_arcs_changed = _normalize_blueprint_character_arcs(
            payload.get("character_arcs"),
            total_chapters=total_chapters,
        )
        if character_arcs_changed:
            if result is None:
                result = dict(payload)
            result["character_arcs"] = character_arcs

    if "suspense_schedule" in payload:
        suspense_schedule, suspense_changed = _normalize_blueprint_suspense_schedule(
            payload.get("suspense_schedule")
        )
        if suspense_changed:
            if result is None:
                result = dict(payload)
            result["suspense_schedule"] = suspense_schedule

    current_payload = result if result is not None else payload
    if "suspense_schedule" in current_payload:
        suspense_schedule, suspense_reference_changed = _normalize_blueprint_suspense_references(
            current_payload
        )
        if suspense_reference_changed:
            if result is None:
                result = dict(payload)
            result["suspense_schedule"] = suspense_schedule

    if "emotional_arcs" in payload:
        emotional_arcs, emotional_arcs_changed = _normalize_blueprint_emotional_arcs(
            payload.get("emotional_arcs")
        )
        if emotional_arcs_changed:
            if result is None:
                result = dict(payload)
            result["emotional_arcs"] = emotional_arcs

    if "causal_chains" in payload:
        causal_chains, causal_chains_changed = _normalize_blueprint_causal_chains(
            payload.get("causal_chains")
        )
        if causal_chains_changed:
            if result is None:
                result = dict(payload)
            result["causal_chains"] = causal_chains

    if "subplot_collisions" in payload:
        subplot_collisions, subplot_collisions_changed = _normalize_blueprint_subplot_collisions(
            payload.get("subplot_collisions")
        )
        if subplot_collisions_changed:
            if result is None:
                result = dict(payload)
            result["subplot_collisions"] = subplot_collisions

    if "subversion_points" in payload:
        subversion_points, subversion_points_changed = _normalize_blueprint_subversion_points(
            payload.get("subversion_points")
        )
        if subversion_points_changed:
            if result is None:
                result = dict(payload)
            result["subversion_points"] = subversion_points

    return result if result is not None else payload


def _safe_blueprint_int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError):
        if isinstance(value, str):
            match = re.search(r"\d+", value)
            if match:
                return int(match.group(0))
        return default


def _effective_local_blueprint_total_chapters(payload: dict[str, Any], total_chapters: int) -> int:
    if total_chapters > 0:
        return int(total_chapters)
    candidates = [int(total_chapters or 0)]
    for phase in payload.get("narrative_phases", []) or []:
        if isinstance(phase, dict):
            candidates.append(_safe_blueprint_int(phase.get("chapter_end")))
    for volume in payload.get("volumes", []) or []:
        if isinstance(volume, dict):
            candidates.append(_safe_blueprint_int(volume.get("end_chapter")))
    for point in payload.get("key_turning_points", []) or []:
        if isinstance(point, dict):
            candidates.append(_safe_blueprint_int(point.get("chapter_number")))
    for subplot in payload.get("subplot_plan", []) or []:
        if not isinstance(subplot, dict):
            continue
        candidates.append(_safe_blueprint_int(subplot.get("resolution_chapter")))
        for chapter in subplot.get("involved_chapters", []) or []:
            candidates.append(_safe_blueprint_int(chapter))
    for suspense in payload.get("suspense_schedule", []) or []:
        if isinstance(suspense, dict):
            candidates.append(_safe_blueprint_int(suspense.get("introduce_chapter")))
            candidates.append(_safe_blueprint_int(suspense.get("resolve_chapter")))
    return max(candidates)


def _local_blueprint_subplot_min(narrative_complexity: str) -> int:
    return _BLUEPRINT_LOCAL_SUBPLOT_MIN.get(str(narrative_complexity or "standard"), 2)


def _synthesize_local_blueprint_phases(total_chapters: int) -> list[dict[str, Any]]:
    if total_chapters <= 1:
        ranges = [(1, 1)]
    elif total_chapters == 2:
        ranges = [(1, 1), (2, 2)]
    else:
        first_end = max(1, total_chapters // 3)
        second_end = max(first_end + 1, (total_chapters * 2) // 3)
        ranges = [(1, first_end), (first_end + 1, second_end), (second_end + 1, total_chapters)]
    names = ("开局立势", "中段升级", "终局收束")
    descriptions = (
        "建立核心目标、冲突入口与主要人物关系。",
        "推进调查、关系变化与风险升级，让主线和支线互相牵引。",
        "集中回收悬念、支线与主题承诺，完成终局选择。",
    )
    phases: list[dict[str, Any]] = []
    for idx, (start, end) in enumerate(ranges):
        if start > end:
            continue
        phases.append(
            {
                "phase_name": names[min(idx, len(names) - 1)],
                "chapter_start": start,
                "chapter_end": end,
                "description": descriptions[min(idx, len(descriptions) - 1)],
                "key_events": [],
                "tension_level": "渐升" if idx < len(ranges) - 1 else "高压收束",
            }
        )
    return phases


def _synthesize_local_blueprint_volumes(total_chapters: int) -> list[dict[str, Any]]:
    return [
        {
            "volume_number": 1,
            "title": "正篇",
            "start_chapter": 1,
            "end_chapter": max(1, total_chapters),
            "arc_goal": "覆盖全书主线推进、关系变化与终局收束。",
            "milestone_targets": [],
            "main_conflicts": [],
            "climax_hint": "",
            "resolution_hint": "",
            "notes": "",
        }
    ]


def _repair_local_blueprint_volumes(
    value: Any, *, total_chapters: int
) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(value, list) or not any(isinstance(item, dict) for item in value):
        return _synthesize_local_blueprint_volumes(total_chapters), True

    original_items = [dict(item) for item in value if isinstance(item, dict)]
    original_items.sort(key=lambda item: _safe_blueprint_int(item.get("start_chapter"), 1))
    raw_items = [dict(item) for item in original_items]
    repaired: list[dict[str, Any]] = []
    previous_end = 0
    total_items = len(raw_items)
    for idx, item in enumerate(raw_items):
        start = previous_end + 1
        if start > total_chapters:
            break
        remaining_items = max(0, total_items - idx - 1)
        max_end = max(start, total_chapters - remaining_items)
        if idx == total_items - 1:
            end = total_chapters
        else:
            end = _clamp_int(
                _safe_blueprint_int(item.get("end_chapter"), start),
                start,
                max_end,
            )
        item["volume_number"] = idx + 1
        item["start_chapter"] = start
        item["end_chapter"] = end
        if not str(item.get("title") or "").strip():
            item["title"] = f"第{idx + 1}卷"
        if not str(item.get("arc_goal") or "").strip():
            item["arc_goal"] = "承接全书主线并推进本卷核心冲突。"
        repaired.append(item)
        previous_end = end

    if not repaired:
        return _synthesize_local_blueprint_volumes(total_chapters), True
    if repaired[-1].get("end_chapter") != total_chapters:
        repaired[-1]["end_chapter"] = total_chapters
    return repaired, repaired != original_items


def _repair_local_blueprint_phases(
    value: Any, *, total_chapters: int
) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(value, list) or not any(isinstance(item, dict) for item in value):
        return _synthesize_local_blueprint_phases(total_chapters), True

    original_items = [dict(item) for item in value if isinstance(item, dict)]
    original_items.sort(key=lambda item: _safe_blueprint_int(item.get("chapter_start"), 1))
    raw_items = [dict(item) for item in original_items]
    repaired: list[dict[str, Any]] = []
    previous_end = 0
    total_items = len(raw_items)
    for idx, item in enumerate(raw_items):
        start = previous_end + 1
        if start > total_chapters:
            break
        remaining_items = max(0, total_items - idx - 1)
        max_end = max(start, total_chapters - remaining_items)
        if idx == total_items - 1:
            end = total_chapters
        else:
            end = _clamp_int(
                _safe_blueprint_int(item.get("chapter_end"), start),
                start,
                max_end,
            )
        item["chapter_start"] = start
        item["chapter_end"] = end
        if not str(item.get("phase_name") or "").strip():
            item["phase_name"] = f"阶段{idx + 1}"
        if not str(item.get("description") or "").strip():
            item["description"] = "承接上一阶段目标并推进主线、支线与人物变化。"
        repaired.append(item)
        previous_end = end

    if not repaired:
        return _synthesize_local_blueprint_phases(total_chapters), True
    if repaired[-1].get("chapter_end") != total_chapters:
        repaired[-1]["chapter_end"] = total_chapters
    return repaired, repaired != original_items


def _repair_local_blueprint_text_fields(
    item: dict[str, Any],
    fields: tuple[str, ...],
    *,
    start: int,
    end: int,
    total_chapters: int,
    replacement: str | Callable[[int, int, int], str],
) -> bool:
    changed = False
    for field in fields:
        if field not in item:
            continue
        repaired, field_changed = repair_explicit_chapter_refs(
            item.get(field),
            start=start,
            end=end,
            total_chapters=total_chapters,
            replacement=replacement,
        )
        if field_changed:
            item[field] = repaired
            changed = True
    return changed


def _relative_blueprint_ref_label(prefix: str) -> Callable[[int, int, int], str]:
    def label(chapter: int, start: int, end: int) -> str:
        if chapter < start:
            return f"{prefix}前段"
        if chapter > end:
            return f"{prefix}后段"
        return f"{prefix}范围内"

    return label


def _repair_local_blueprint_textual_chapter_refs(
    payload: dict[str, Any], *, total_chapters: int
) -> bool:
    changed = False

    if _repair_local_blueprint_text_fields(
        payload,
        _BLUEPRINT_GLOBAL_TEXT_REF_FIELDS,
        start=1,
        end=total_chapters,
        total_chapters=total_chapters,
        replacement=_relative_blueprint_ref_label("全书"),
    ):
        changed = True

    for volume in payload.get("volumes", []) or []:
        if not isinstance(volume, dict):
            continue
        start = _safe_blueprint_int(volume.get("start_chapter"), 1)
        end = _safe_blueprint_int(volume.get("end_chapter"), start)
        if _repair_local_blueprint_text_fields(
            volume,
            _BLUEPRINT_VOLUME_TEXT_REF_FIELDS,
            start=start,
            end=end,
            total_chapters=total_chapters,
            replacement=_relative_blueprint_ref_label("本卷"),
        ):
            changed = True

    for phase in payload.get("narrative_phases", []) or []:
        if not isinstance(phase, dict):
            continue
        start = _safe_blueprint_int(phase.get("chapter_start"), 1)
        end = _safe_blueprint_int(phase.get("chapter_end"), start)
        if _repair_local_blueprint_text_fields(
            phase,
            _BLUEPRINT_PHASE_TEXT_REF_FIELDS,
            start=start,
            end=end,
            total_chapters=total_chapters,
            replacement=_relative_blueprint_ref_label("本阶段"),
        ):
            changed = True

    for arc in payload.get("character_arcs", []) or []:
        if not isinstance(arc, dict):
            continue
        for milestone in arc.get("milestones", []) or []:
            if not isinstance(milestone, dict):
                continue
            start = _safe_blueprint_int(milestone.get("chapter_start"), 1)
            end = _safe_blueprint_int(milestone.get("chapter_end"), start)
            if _repair_local_blueprint_text_fields(
                milestone,
                ("description",),
                start=start,
                end=end,
                total_chapters=total_chapters,
                replacement="该里程碑阶段",
            ):
                changed = True

    return changed


def _local_blueprint_chapter_anchors(total_chapters: int, subplot_index: int) -> list[int]:
    if total_chapters <= 1:
        return [1]
    candidates = [
        1 + subplot_index,
        max(1, round(total_chapters * 0.5) + subplot_index),
        total_chapters - subplot_index,
        1,
        max(1, round(total_chapters * 0.5)),
        total_chapters,
    ]
    anchors: list[int] = []
    target_count = min(3, total_chapters)
    for candidate in candidates:
        chapter = _clamp_int(candidate, 1, total_chapters)
        if chapter not in anchors:
            anchors.append(chapter)
        if len(anchors) >= target_count:
            break
    return sorted(anchors)


def _repair_local_blueprint_turning_points(payload: dict[str, Any], *, total_chapters: int) -> bool:
    points = payload.get("key_turning_points")
    if isinstance(points, list) and points:
        changed = False
        repaired: list[dict[str, Any]] = []
        for point in points:
            if not isinstance(point, dict):
                changed = True
                continue
            item = dict(point)
            chapter = _safe_blueprint_int(item.get("chapter_number"), 1)
            clamped = _clamp_int(chapter, 1, total_chapters)
            if clamped != chapter:
                item["chapter_number"] = clamped
                changed = True
            if not str(item.get("description") or "").strip():
                item["description"] = "关键局势发生转向。"
                changed = True
            if "location" not in item:
                item["location"] = ""
                changed = True
            if "characters_involved" not in item:
                item["characters_involved"] = []
                changed = True
            repaired.append(item)
        if repaired:
            payload["key_turning_points"] = repaired
            return changed or repaired != points

    anchors = _local_blueprint_chapter_anchors(total_chapters, 0)
    descriptions = ("核心线索显现", "危机升级并改变行动方向", "真相与代价集中收束")
    payload["key_turning_points"] = [
        {
            "chapter_number": chapter,
            "description": descriptions[min(idx, len(descriptions) - 1)],
            "location": "",
            "characters_involved": [],
        }
        for idx, chapter in enumerate(anchors)
    ]
    return True


def _local_blueprint_resolution_target(payload: dict[str, Any], subplot_index: int) -> str:
    turning_points = [
        item for item in payload.get("key_turning_points", []) or [] if isinstance(item, dict)
    ]
    if turning_points:
        return f"main_turning_point:{min(subplot_index + 1, len(turning_points))}"
    return "main_turning_point:1"


def _local_blueprint_clean_chapters(value: Any, *, total_chapters: int) -> list[int]:
    if not isinstance(value, list):
        return []
    chapters: list[int] = []
    for item in value:
        chapter = _safe_blueprint_int(item)
        if 1 <= chapter <= total_chapters and chapter not in chapters:
            chapters.append(chapter)
    return sorted(chapters)


def _local_blueprint_valid_weave_links(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    links: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        source_type = str(item.get("source_type") or "").strip()
        link_type = str(item.get("link_type") or "").strip()
        if source_type not in _BLUEPRINT_VALID_SOURCE_TYPES:
            continue
        if link_type not in _BLUEPRINT_VALID_LINK_TYPES:
            continue
        links.append(dict(item))
    return links


def _repair_local_blueprint_subplot(
    value: Any,
    *,
    payload: dict[str, Any],
    subplot_index: int,
    total_chapters: int,
) -> dict[str, Any]:
    template = _BLUEPRINT_LOCAL_SUBPLOT_TEMPLATES[
        subplot_index % len(_BLUEPRINT_LOCAL_SUBPLOT_TEMPLATES)
    ]
    subplot = dict(value) if isinstance(value, dict) else {}
    name = str(subplot.get("name") or template["name"]).strip()
    if not name:
        name = str(template["name"])
    anchors = _local_blueprint_chapter_anchors(total_chapters, subplot_index)
    involved = _local_blueprint_clean_chapters(
        subplot.get("involved_chapters"), total_chapters=total_chapters
    )
    if len(involved) < min(3, total_chapters):
        involved = sorted({*involved, *anchors})
    involved = involved[: max(1, min(3, len(involved)))]

    existing_events: list[dict[str, Any]] = []
    seen_event_chapters: set[int] = set()
    for event in subplot.get("chapter_events", []) or []:
        if not isinstance(event, dict):
            continue
        chapter = _safe_blueprint_int(event.get("chapter_number"))
        if chapter not in involved or chapter in seen_event_chapters:
            continue
        seen_event_chapters.add(chapter)
        existing_events.append(dict(event))

    min_events = min(3, max(1, len(involved)))
    event_templates = ("触发支线并建立问题", "推进支线并制造反作用", "支线收束并反哺主线")
    for chapter in involved:
        if len(existing_events) >= min_events:
            break
        if chapter in seen_event_chapters:
            continue
        seen_event_chapters.add(chapter)
        existing_events.append(
            {
                "chapter_number": chapter,
                "event": f"{name}：{event_templates[min(len(existing_events), 2)]}",
                "weave_notes": "与主线目标形成因果牵引。",
                "depends_on": [],
            }
        )
    existing_events.sort(key=lambda item: _safe_blueprint_int(item.get("chapter_number")))

    links = _local_blueprint_valid_weave_links(subplot.get("weave_links"))
    for link in links:
        trigger_chapter = _safe_blueprint_int(link.get("trigger_chapter"), 0)
        if trigger_chapter < 0 or trigger_chapter > total_chapters:
            link["trigger_chapter"] = involved[-1] if involved else 0
        elif link.get("trigger_chapter") != trigger_chapter:
            link["trigger_chapter"] = trigger_chapter
    has_trigger_start = any(
        link.get("source_type") == "main_plot" and link.get("link_type") == "trigger_start"
        for link in links
    )
    has_feedback = any(link.get("link_type") in {"feed_main", "reveal_key"} for link in links)
    if not has_trigger_start:
        links.append(
            {
                "source_type": "main_plot",
                "source_ref": "主线目标推进",
                "target_subplot": name,
                "trigger_chapter": involved[0],
                "link_type": "trigger_start",
                "description": f"主线事件触发「{name}」进入执行。",
            }
        )
    if not has_feedback:
        feedback_type = str(template["feedback_type"])
        links.append(
            {
                "source_type": "subplot",
                "source_ref": name,
                "target_subplot": "主线",
                "trigger_chapter": involved[-1],
                "link_type": feedback_type,
                "description": f"「{name}」的阶段结果反哺主线推进。",
            }
        )

    priority = str(subplot.get("priority") or "normal").strip()
    if priority not in _BLUEPRINT_VALID_SUBPLOT_PRIORITIES:
        priority = "normal"
    resolution_chapter = _safe_blueprint_int(subplot.get("resolution_chapter"))
    if resolution_chapter < 1 or resolution_chapter > total_chapters:
        resolution_chapter = involved[-1]
    resolution_type = str(subplot.get("resolution_type") or template["resolution_type"]).strip()
    if resolution_type not in _BLUEPRINT_VALID_RESOLUTION_TYPES:
        resolution_type = str(template["resolution_type"])

    resolution_target = str(subplot.get("resolution_target") or "").strip()
    if not resolution_target or (
        priority in {"primary", "normal"} and resolution_target == "theme_echo"
    ):
        resolution_target = _local_blueprint_resolution_target(payload, subplot_index)

    return {
        **subplot,
        "name": name,
        "description": str(subplot.get("description") or template["description"]).strip(),
        "involved_chapters": involved,
        "chapter_events": existing_events,
        "weave_links": links,
        "priority": priority,
        "resolution_chapter": resolution_chapter,
        "resolution_target": resolution_target,
        "resolution_type": resolution_type,
    }


def _repair_local_blueprint_subplots(
    payload: dict[str, Any],
    *,
    total_chapters: int,
    narrative_complexity: str,
) -> bool:
    min_subplots = _local_blueprint_subplot_min(narrative_complexity)
    raw_subplots = payload.get("subplot_plan")
    subplots = [item for item in raw_subplots or [] if isinstance(item, dict)]
    while len(subplots) < min_subplots:
        subplots.append({})
    repaired = [
        _repair_local_blueprint_subplot(
            item,
            payload=payload,
            subplot_index=idx,
            total_chapters=total_chapters,
        )
        for idx, item in enumerate(subplots)
    ]
    payload["subplot_plan"] = repaired
    return repaired != raw_subplots


def _repair_local_blueprint_suspense_schedule(
    payload: dict[str, Any], *, total_chapters: int
) -> bool:
    subplots = [item for item in payload.get("subplot_plan", []) or [] if isinstance(item, dict)]
    raw_schedule = payload.get("suspense_schedule")
    schedule = [dict(item) for item in raw_schedule or [] if isinstance(item, dict)]
    if not schedule:
        source_subplots = subplots[: max(1, min(3, len(subplots)))] or [{}]
        schedule = []
        for idx, subplot in enumerate(source_subplots):
            involved = _local_blueprint_clean_chapters(
                subplot.get("involved_chapters"), total_chapters=total_chapters
            ) or _local_blueprint_chapter_anchors(total_chapters, idx)
            name = str(subplot.get("name") or "主线").strip()
            schedule.append(
                {
                    "suspense_id": f"local_s_{idx + 1:03d}",
                    "suspense_type": ("mystery", "emotion", "crisis")[idx % 3],
                    "introduce_chapter": involved[0],
                    "resolve_chapter": involved[-1],
                    "description": f"{name}的关键疑问如何改变主线走向",
                    "urgency_level": "normal" if idx else "high",
                    "related_subplot": name if name != "主线" else "主线",
                    "strand_affinity": {"quest": 0.4, "fire": 0.2, "constellation": 0.4},
                }
            )
    seen_ids: set[str] = set()
    for idx, item in enumerate(schedule):
        subplot = subplots[min(idx, len(subplots) - 1)] if subplots else {}
        involved = _local_blueprint_clean_chapters(
            subplot.get("involved_chapters"), total_chapters=total_chapters
        ) or _local_blueprint_chapter_anchors(total_chapters, idx)
        suspense_id = str(item.get("suspense_id") or "").strip() or f"local_s_{idx + 1:03d}"
        if suspense_id in seen_ids:
            suspense_id = f"{suspense_id}_{idx + 1}"
        seen_ids.add(suspense_id)
        item["suspense_id"] = suspense_id
        if item.get("suspense_type") not in _BLUEPRINT_VALID_SUSPENSE_TYPES:
            item["suspense_type"] = "mystery"
        if item.get("urgency_level") not in _BLUEPRINT_VALID_URGENCY_LEVELS:
            item["urgency_level"] = "normal"
        introduce = _safe_blueprint_int(item.get("introduce_chapter"))
        if introduce < 1 or introduce > total_chapters:
            introduce = involved[0]
        resolve = _safe_blueprint_int(item.get("resolve_chapter"))
        if resolve < introduce or resolve > total_chapters:
            resolve = involved[-1]
        item["introduce_chapter"] = introduce
        item["resolve_chapter"] = resolve
        if not str(item.get("description") or "").strip():
            item["description"] = "关键疑问需要在后续章节得到回应。"
        if not str(item.get("related_subplot") or "").strip():
            item["related_subplot"] = str(subplot.get("name") or "主线")
        affinity = item.get("strand_affinity")
        if not _local_blueprint_affinity_has_signal(affinity):
            item["strand_affinity"] = {"quest": 0.4, "fire": 0.2, "constellation": 0.4}
    payload["suspense_schedule"] = schedule
    return schedule != raw_schedule


def _local_blueprint_affinity_has_signal(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for raw in value.values():
        try:
            if float(raw or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _apply_local_blueprint_structural_fallback(
    payload: Any,
    *,
    total_chapters: int,
    narrative_complexity: str = "standard",
    validation_errors: list[str] | None = None,
) -> dict[str, Any] | None:
    """Build a minimal valid blueprint when LLM repair omits structural sections."""
    if not isinstance(payload, dict):
        return None

    normalized = _pre_normalize_blueprint_payload(payload, total_chapters=total_chapters)
    if not isinstance(normalized, dict):
        return None
    effective_total = _effective_local_blueprint_total_chapters(normalized, total_chapters)
    if effective_total <= 0:
        return None

    data = dict(normalized)
    changed = False
    if not str(data.get("synopsis") or "").strip():
        data["synopsis"] = "主角围绕核心目标推进调查、关系选择与危机应对，并在终局完成收束。"
        changed = True

    if data.get("volume_mode") or data.get("volumes"):
        volumes, volumes_changed = _repair_local_blueprint_volumes(
            data.get("volumes"), total_chapters=effective_total
        )
        if volumes_changed:
            data["volumes"] = volumes
            changed = True

    phases, phases_changed = _repair_local_blueprint_phases(
        data.get("narrative_phases"), total_chapters=effective_total
    )
    if phases_changed:
        data["narrative_phases"] = phases
        changed = True

    if _repair_local_blueprint_textual_chapter_refs(data, total_chapters=effective_total):
        changed = True

    if _repair_local_blueprint_turning_points(data, total_chapters=effective_total):
        changed = True

    if _repair_local_blueprint_subplots(
        data,
        total_chapters=effective_total,
        narrative_complexity=narrative_complexity,
    ):
        changed = True

    if _repair_local_blueprint_suspense_schedule(data, total_chapters=effective_total):
        changed = True

    if not changed and not validation_errors:
        return None
    repaired = _pre_normalize_blueprint_payload(data, total_chapters=effective_total)
    return repaired if isinstance(repaired, dict) else data


def pre_normalize_blueprint_payload(
    payload: Any,
    *,
    total_chapters: int,
) -> Any:
    """Normalize common LLM blueprint field drift before schema validation."""
    return _pre_normalize_blueprint_payload(payload, total_chapters=total_chapters)


def apply_local_blueprint_structural_fallback(
    payload: Any,
    *,
    total_chapters: int,
    narrative_complexity: str = "standard",
    validation_errors: list[str] | None = None,
) -> dict[str, Any] | None:
    """Create minimal executable blueprint sections when LLM repair omits them."""
    return _apply_local_blueprint_structural_fallback(
        payload,
        total_chapters=total_chapters,
        narrative_complexity=narrative_complexity,
        validation_errors=validation_errors,
    )


__all__ = [
    "apply_local_blueprint_structural_fallback",
    "pre_normalize_blueprint_payload",
]
