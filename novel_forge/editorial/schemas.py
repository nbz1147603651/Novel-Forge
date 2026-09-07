"""Schemas for the project-wide editorial contract and audit findings."""

from __future__ import annotations

import re
from collections.abc import Mapping
from hashlib import sha1
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.core.utils.type_coerce import coerce_text_list

_POLICY_TEXT_KEYS = ("policy", "rule", "description", "requirement", "text", "content")
_TITLE_NO_REUSE_TOKENS = (
    "no reuse",
    "no repeated",
    "no repeat",
    "unique",
    "禁止复用",
    "禁止重复",
    "不复用",
    "不重复",
    "不得复用",
    "不得重复",
    "不可复用",
    "不可重复",
    "每章不同",
    "标题唯一",
    "唯一",
)

_CLIMAX_TYPE_ALIASES = {
    "primary": "main",
    "main_climax": "main",
    "main climax": "main",
    "core": "main",
    "final": "main",
    "finale": "main",
    "ultimate": "main",
    "主高潮": "main",
    "主线高潮": "main",
    "核心高潮": "main",
    "终局高潮": "main",
    "secondary": "subplot",
    "side": "subplot",
    "minor": "subplot",
    "sub": "subplot",
    "sub-plot": "subplot",
    "subplot_climax": "subplot",
    "subplot climax": "subplot",
    "支线": "subplot",
    "副线": "subplot",
    "支线高潮": "subplot",
    "political": "subplot",
    "political_climax": "subplot",
    "political climax": "subplot",
    "political_intrigue": "subplot",
    "political intrigue": "subplot",
    "court_intrigue": "subplot",
    "court intrigue": "subplot",
    "power_struggle": "subplot",
    "power struggle": "subplot",
    "权谋": "subplot",
    "权谋高潮": "subplot",
    "政治": "subplot",
    "政治高潮": "subplot",
    "朝堂": "subplot",
    "朝堂高潮": "subplot",
    "emotion": "emotional",
    "emotional_climax": "emotional",
    "emotional climax": "emotional",
    "情感": "emotional",
    "情感高潮": "emotional",
    "resolution": "emotional",
    "denouement": "emotional",
    "aftermath": "emotional",
    "epilogue": "emotional",
    "尾声": "emotional",
    "余波": "emotional",
    "收束": "emotional",
    "business": "commercial",
    "market": "commercial",
    "商业": "commercial",
    "商业高潮": "commercial",
    "suspense": "mystery",
    "reveal": "mystery",
    "revelation": "mystery",
    "悬疑": "mystery",
    "谜题": "mystery",
    "揭示": "mystery",
}

_EXPLANATION_POLICY_ALIASES = {
    "never": "never_explain",
    "none": "never_explain",
    "no_explanation": "never_explain",
    "no explanation": "never_explain",
    "forbid": "never_explain",
    "forbidden": "never_explain",
    "禁止解释": "never_explain",
    "不解释": "never_explain",
    "once": "explain_once",
    "one_time": "explain_once",
    "one time": "explain_once",
    "single": "explain_once",
    "只解释一次": "explain_once",
    "on_escalation": "explain_on_escalation",
    "escalation": "explain_on_escalation",
    "when_escalated": "explain_on_escalation",
    "升级时解释": "explain_on_escalation",
    "free_explain": "free",
    "unrestricted": "free",
    "自由": "free",
}

_EXPRESSION_CHANNEL_ALIASES = {
    "somatic": "somatic_reaction",
    "somatic_reactions": "somatic_reaction",
    "body_signal": "somatic_reaction",
    "body_signals": "somatic_reaction",
    "physical_reaction": "somatic_reaction",
    "physical_reactions": "somatic_reaction",
    "身体反应": "somatic_reaction",
    "身体信号": "somatic_reaction",
    "action": "action_tag",
    "action_tags": "action_tag",
    "movement_tag": "action_tag",
    "movement_tags": "action_tag",
    "动作标签": "action_tag",
    "dialogue": "dialogue_tag",
    "dialogue_tags": "dialogue_tag",
    "speech_tag": "dialogue_tag",
    "speech_tags": "dialogue_tag",
    "对白标签": "dialogue_tag",
    "sensory": "sensory_anchor",
    "sensory_anchors": "sensory_anchor",
    "sense_anchor": "sensory_anchor",
    "sense_anchors": "sensory_anchor",
    "感官锚点": "sensory_anchor",
    "sentence": "sentence_pattern",
    "sentences": "sentence_pattern",
    "sentence_patterns": "sentence_pattern",
    "syntax_pattern": "sentence_pattern",
    "syntax_patterns": "sentence_pattern",
    "句式": "sentence_pattern",
    "句式模板": "sentence_pattern",
    "句法": "sentence_pattern",
    "other": "other",
    "其他": "other",
}

_EXPRESSION_CHANNEL_DEFAULTS = {
    "somatic_reaction": 1,
    "action_tag": 2,
    "dialogue_tag": 2,
    "sensory_anchor": 3,
}
_EXPRESSION_PROFILE_CHANNELS = {
    "somatic_reaction",
    "action_tag",
    "dialogue_tag",
    "sensory_anchor",
    "sentence_pattern",
    "other",
}
_EXPRESSION_PROFILE_MIN_CONFIDENCE = 0.55
_EXPRESSION_PROFILE_MAX_ITEMS = 8
_EXPRESSION_PROFILE_MAX_SURFACE_FORMS = 8
_EXPRESSION_PROFILE_MAX_CONTEXTS = 4
_EXPRESSION_PROFILE_MAX_AXES = 4
_CLIMAX_TYPE_VALUES = {"main", "subplot", "emotional", "commercial", "mystery"}
_EXPLANATION_POLICY_VALUES = {"never_explain", "explain_once", "explain_on_escalation", "free"}
_EDITORIAL_SEVERITY_VALUES = {"critical", "high", "medium", "low", "info"}
_EDITORIAL_PRIORITY_VALUES = {"critical", "high", "medium", "low"}
_EDITORIAL_SEVERITY_ALIASES = {
    "none": "info",
    "informational": "info",
    "minor": "low",
    "trivial": "low",
    "ok": "low",
    "pass": "low",
    "warning": "medium",
    "warn": "medium",
    "moderate": "medium",
    "issue": "medium",
    "problem": "medium",
    "error": "high",
    "invalid": "high",
    "major": "high",
    "serious": "high",
    "severe": "high",
    "blocker": "critical",
    "blocking": "critical",
    "fatal": "critical",
    "提示": "info",
    "信息": "info",
    "轻微": "low",
    "低": "low",
    "警告": "medium",
    "中": "medium",
    "错误": "high",
    "严重": "high",
    "高": "high",
    "致命": "critical",
    "阻断": "critical",
}
_REGEX_LIKE_RE = re.compile(r"(\\|\(\?|\[[^\]]*\]|\{|\}|\||\.\*|\.\+|\^|\$)")

_BASIC_CHINESE_NUMERALS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _normalize_literal_alias(value: Any, aliases: dict[str, str]) -> Any:
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    return aliases.get(normalized.lower(), aliases.get(normalized, normalized))


def _enum_key(value: Any) -> str:
    return (
        str(value or "")
        .strip()
        .strip("\"'`，。,.；;：: ")
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )


def _normalize_climax_type(value: Any) -> str:
    normalized = _normalize_literal_alias(value, _CLIMAX_TYPE_ALIASES)
    key = _enum_key(normalized)
    return key if key in _CLIMAX_TYPE_VALUES else "subplot"


def _normalize_explanation_policy(value: Any) -> str:
    normalized = _normalize_literal_alias(value, _EXPLANATION_POLICY_ALIASES)
    key = _enum_key(normalized)
    return key if key in _EXPLANATION_POLICY_VALUES else "explain_once"


def _normalize_editorial_severity(value: Any, *, allow_info: bool) -> str:
    key = _enum_key(value)
    allowed = _EDITORIAL_SEVERITY_VALUES if allow_info else _EDITORIAL_PRIORITY_VALUES
    if key in allowed:
        return key
    mapped = _EDITORIAL_SEVERITY_ALIASES.get(key)
    if mapped in allowed:
        return mapped
    if mapped == "info" and "low" in allowed:
        return "low"
    return "medium"


def _append_policy_note(existing: Any, note: Any) -> str:
    existing_text = str(existing or "").strip()
    note_text = str(note or "").strip()
    if not note_text:
        return existing_text
    if not existing_text:
        return note_text
    if note_text in existing_text:
        return existing_text
    return f"{existing_text}；{note_text}"


def _coerce_text_list(value: Any) -> list[str]:
    if isinstance(value, list | tuple):
        result: list[str] = []
        for item in value:
            if isinstance(item, list | tuple):
                text = "；".join(str(part).strip() for part in item if str(part).strip())
            elif isinstance(item, dict):
                text = "；".join(str(part).strip() for part in item.values() if str(part).strip())
            else:
                text = str(item or "").strip()
            if text:
                result.append(text)
        return result
    if isinstance(value, dict):
        return [text for item in value.values() if (text := str(item or "").strip())]
    text = str(value or "").strip()
    return [text] if text else []


def _coerce_policy_text_list(value: Any) -> list[str]:
    """Normalize LLM policy objects like ``{"policy": "..."}`` into strings."""

    return coerce_text_list(value, preferred_keys=_POLICY_TEXT_KEYS)


def _normalize_revelation_ladder_payload(value: Any) -> Any:
    """Strip explanatory drift from revelation ladder items before strict validation."""

    if not isinstance(value, list | tuple):
        return value
    text_keys = (
        "thread",
        "stage",
        "trigger",
        "allowed_disclosure",
        "required_action_consequence",
    )
    normalized: list[Any] = []
    for item in value:
        if not isinstance(item, Mapping):
            normalized.append(item)
            continue
        raw = dict(item)
        entry: dict[str, Any] = {}
        for key in text_keys:
            if key in raw:
                text_values = _coerce_text_list(raw.get(key))
                entry[key] = "；".join(text_values) if text_values else str(raw.get(key) or "")
        entry["stage_order"] = raw.get("stage_order", raw.get("order", 1))
        entry["target_chapter"] = raw.get(
            "target_chapter",
            raw.get("chapter", raw.get("chapter_number", 0)),
        )
        normalized.append(entry)
    return normalized


def _clip_profile_text(value: Any, *, limit: int = 80) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _coerce_profile_text_list(
    value: Any,
    *,
    max_items: int,
    max_chars: int = 32,
    reject_regex_like: bool = False,
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in coerce_text_list(value):
        text = _clip_profile_text(raw, limit=max_chars)
        if not text or text in seen:
            continue
        if reject_regex_like and _REGEX_LIKE_RE.search(text):
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= max_items:
            break
    return result


def _coerce_profile_confidence(value: Any, *, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int | float):
        return max(0.0, min(float(value), 1.0))
    text = str(value or "").strip()
    if not text:
        return default
    try:
        parsed = float(text.rstrip("%"))
    except ValueError:
        return default
    if parsed > 1:
        parsed = parsed / 100
    return max(0.0, min(parsed, 1.0))


def _normalize_expression_profile_channel(value: Any) -> str:
    normalized = _normalize_literal_alias(str(value or "").strip(), _EXPRESSION_CHANNEL_ALIASES)
    if normalized in _EXPRESSION_PROFILE_CHANNELS:
        return str(normalized)
    return "other"


def _normalize_profile_id(value: Any, *, label: str, surface_forms: list[str]) -> str:
    raw = str(value or "").strip().lower()
    raw = re.sub(r"[^a-z0-9_]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")
    if raw:
        return raw[:64]
    seed = label or "；".join(surface_forms[:2]) or "expression_channel"
    return f"profile_{sha1(seed.encode('utf-8')).hexdigest()[:10]}"


def _raw_profile_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list | tuple):
        return list(value)
    if isinstance(value, Mapping):
        for key in (
            "expression_channel_profiles",
            "profiles",
            "items",
            "channels",
        ):
            nested = value.get(key)
            if isinstance(nested, list | tuple):
                return list(nested)
        return list(value.values())
    return []


def normalize_expression_channel_profiles(
    value: Any,
    *,
    max_items: int = _EXPRESSION_PROFILE_MAX_ITEMS,
    min_confidence: float = _EXPRESSION_PROFILE_MIN_CONFIDENCE,
    provenance: str = "init_contract",
) -> list[dict[str, Any]]:
    """Normalize LLM-derived expression-channel profiles into bounded contract data."""

    profiles: dict[str, dict[str, Any]] = {}
    used_surface_forms: set[str] = set()
    for raw in _raw_profile_list(value):
        dump = getattr(raw, "model_dump", None)
        if callable(dump):
            raw = dump(mode="json")
        if not isinstance(raw, Mapping):
            continue
        surface_forms = _coerce_profile_text_list(
            raw.get("surface_forms")
            or raw.get("forms")
            or raw.get("phrases")
            or raw.get("expressions")
            or raw.get("patterns"),
            max_items=_EXPRESSION_PROFILE_MAX_SURFACE_FORMS,
            max_chars=32,
            reject_regex_like=True,
        )
        if not surface_forms:
            continue
        confidence = _coerce_profile_confidence(raw.get("confidence"), default=0.0)
        if confidence < min_confidence:
            continue
        replacement_axes = _coerce_profile_text_list(
            raw.get("replacement_axes")
            or raw.get("alternatives")
            or raw.get("replacement_advice")
            or raw.get("advice"),
            max_items=_EXPRESSION_PROFILE_MAX_AXES,
            max_chars=32,
        )
        if not replacement_axes:
            continue
        risk_reason = _clip_profile_text(
            raw.get("risk_reason") or raw.get("reason") or raw.get("risk"),
            limit=96,
        )
        allowed_when = _clip_profile_text(
            raw.get("allowed_when") or raw.get("allowed_context") or raw.get("exception"),
            limit=96,
        )
        if not risk_reason or not allowed_when:
            continue
        trigger_contexts = _coerce_profile_text_list(
            raw.get("trigger_contexts") or raw.get("contexts") or raw.get("triggers"),
            max_items=_EXPRESSION_PROFILE_MAX_CONTEXTS,
            max_chars=24,
        )
        evidence_quotes = _coerce_profile_text_list(
            raw.get("evidence_quotes") or raw.get("evidence") or raw.get("quotes"),
            max_items=6,
            max_chars=80,
            reject_regex_like=True,
        )
        label = _clip_profile_text(
            raw.get("label") or raw.get("name") or raw.get("channel_label") or surface_forms[0],
            limit=48,
        )
        channel = _normalize_expression_profile_channel(raw.get("channel"))
        channel_id = _normalize_profile_id(
            raw.get("channel_id") or raw.get("id"),
            label=label,
            surface_forms=surface_forms,
        )
        unique_surface_forms = [item for item in surface_forms if item not in used_surface_forms]
        if not unique_surface_forms:
            continue
        used_surface_forms.update(unique_surface_forms)
        cooldown = _coerce_budget_int(raw.get("cooldown_chapters"))
        profile = {
            "channel_id": channel_id,
            "channel": channel,
            "label": label,
            "surface_forms": unique_surface_forms,
            "trigger_contexts": trigger_contexts,
            "risk_reason": risk_reason,
            "replacement_axes": replacement_axes,
            "allowed_when": allowed_when,
            "cooldown_chapters": max(0, min(int(cooldown if cooldown is not None else 3), 12)),
            "actor_scope": _clip_profile_text(raw.get("actor_scope") or "global", limit=32),
            "confidence": confidence,
            "provenance": _clip_profile_text(raw.get("provenance") or provenance, limit=32),
            "evidence_quotes": evidence_quotes,
        }
        existing = profiles.get(channel_id)
        if existing is None:
            profiles[channel_id] = profile
        else:
            existing["surface_forms"] = [
                *existing["surface_forms"],
                *[
                    item
                    for item in unique_surface_forms
                    if item not in set(existing["surface_forms"])
                ],
            ][:_EXPRESSION_PROFILE_MAX_SURFACE_FORMS]
            existing["trigger_contexts"] = [
                *existing["trigger_contexts"],
                *[
                    item
                    for item in trigger_contexts
                    if item not in set(existing["trigger_contexts"])
                ],
            ][:_EXPRESSION_PROFILE_MAX_CONTEXTS]
            existing["replacement_axes"] = [
                *existing["replacement_axes"],
                *[
                    item
                    for item in replacement_axes
                    if item not in set(existing["replacement_axes"])
                ],
            ][:_EXPRESSION_PROFILE_MAX_AXES]
            existing["confidence"] = max(float(existing["confidence"]), confidence)
            existing["evidence_quotes"] = [
                *existing.get("evidence_quotes", []),
                *[
                    item
                    for item in evidence_quotes
                    if item not in set(existing.get("evidence_quotes", []))
                ],
            ][:6]

    return list(profiles.values())[: max(0, int(max_items or 0))]


def _coerce_budget_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value or "").strip()
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if digits:
        return int(digits)
    for numeral, number in _BASIC_CHINESE_NUMERALS.items():
        if numeral in text:
            return number
    return None


def _clamp_budget_int(value: int, *, minimum: int = 0, maximum: int = 5) -> int:
    return max(minimum, min(value, maximum))


def _integer_candidates(value: Any) -> list[int]:
    """Extract integer candidates from common LLM scalar/range shapes."""

    if isinstance(value, bool):
        return [int(value)]
    if isinstance(value, int):
        return [value]
    if isinstance(value, float):
        return [int(value)]
    if isinstance(value, list | tuple):
        result: list[int] = []
        for item in value:
            result.extend(_integer_candidates(item))
        return result
    if isinstance(value, Mapping):
        result: list[int] = []
        for key in (
            "count",
            "chapters",
            "expected_chapters",
            "expected_aftermath_chapters",
            "start",
            "end",
            "start_chapter",
            "end_chapter",
        ):
            if key in value:
                result.extend(_integer_candidates(value[key]))
        if result:
            return result
        for item in value.values():
            result.extend(_integer_candidates(item))
        return result

    text = str(value or "").strip()
    if not text:
        return []
    digits = [int(match) for match in re.findall(r"\d+", text)]
    if digits:
        return digits
    return [number for numeral, number in _BASIC_CHINESE_NUMERALS.items() if numeral in text]


def _coerce_aftermath_chapter_count(value: Any, *, chapter_number: Any = None) -> int | None:
    """Convert LLM aftermath windows/ranges into a single chapter count."""

    candidates = _integer_candidates(value)
    if not candidates:
        return None
    chapter_candidates = _integer_candidates(chapter_number)
    chapter = chapter_candidates[0] if chapter_candidates else None

    if len(candidates) >= 2:
        upper = max(candidates[0], candidates[-1])
        if chapter is not None and upper >= chapter and upper > 10:
            return max(0, min(upper - chapter, 50))
        return max(0, min(upper, 50))

    return max(0, min(candidates[0], 50))


def _expression_budget_from_text(text: Any) -> dict[str, int]:
    raw = str(text or "")
    if not raw:
        return {}
    budget: dict[str, int] = {}
    channel_markers = {
        "somatic_reaction": ("身体", "体感", "心跳", "胸口", "手指", "眼眶"),
        "action_tag": ("动作", "手势", "视线", "停顿"),
        "dialogue_tag": ("对白", "对话", "话语", "语气"),
        "sensory_anchor": ("感官", "气味", "声音", "光线", "触感"),
    }
    for channel, markers in channel_markers.items():
        marker_positions = [raw.find(marker) for marker in markers if raw.find(marker) >= 0]
        if not marker_positions:
            continue
        start = min(marker_positions)
        window = raw[start : start + 48]
        parsed = _coerce_budget_int(window)
        if parsed is not None:
            budget[channel] = parsed
    return budget


def _normalize_expression_channel_budget(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return dict(_EXPRESSION_CHANNEL_DEFAULTS)

    budget: dict[str, int] = {}
    for key, raw_value in value.items():
        raw_key = str(key or "").strip()
        normalized_key = _normalize_literal_alias(raw_key, _EXPRESSION_CHANNEL_ALIASES)
        if normalized_key in _EXPRESSION_CHANNEL_DEFAULTS:
            parsed = _coerce_budget_int(raw_value)
            if parsed is not None:
                budget[normalized_key] = parsed
            continue

        text_budget = _expression_budget_from_text(raw_value)
        if text_budget:
            budget.update(text_budget)

    merged = dict(_EXPRESSION_CHANNEL_DEFAULTS)
    merged.update(
        {
            key: max(0, min(int(value), 20))
            for key, value in budget.items()
            if key in _EXPRESSION_CHANNEL_DEFAULTS
        }
    )
    return merged


class CharacterVoiceProfile(VersionedSchema):
    """Project-level speech contract for one character."""

    character: str = Field(min_length=1, description="角色名。")
    sentence_profile: str = Field(
        min_length=1,
        description="句式长短、停顿和节奏偏好。",
    )
    explanation_bias: str = Field(
        min_length=1,
        description="解释倾向：多解释、少解释、转移话题、用事实替代情绪等。",
    )
    emotion_syntax: str = Field(
        min_length=1,
        description="情绪升高时的句法变化。",
    )
    signature_moves: list[str] = Field(
        min_length=1,
        description="可反复用于区分声纹的说话动作或句法习惯。",
    )
    taboo_patterns: list[str] = Field(
        default_factory=list,
        description="该角色不应使用的句式、修辞或口吻。",
    )
    sample_lines: list[str] = Field(
        default_factory=list,
        description="1-3 句角色声纹样例，不进入正文复用。",
    )
    subtext_bias: str = Field(
        default="",
        description="角色对白潜台词倾向：情绪升高时是回避、反问、转移话题、用事实替代情绪还是沉默；默认表达习惯之外的言外之意。",
    )
    tts_voice_hints: dict[str, Any] | None = Field(
        default=None,
        description=(
            "TTS 声音提示（对应 TTSVoiceHints schema）。"
            "包含 preferred_pitch / preferred_speed / voice_texture 等。"
            "以 dict 存储以避免循环导入。"
        ),
    )


class ClimaxMarker(VersionedSchema):
    """High-level structural climax marker owned by the editorial contract."""

    chapter_number: int = Field(ge=1)
    climax_type: Literal["main", "subplot", "emotional", "commercial", "mystery"] = "main"
    description: str = Field(min_length=1)
    expected_aftermath_chapters: int = Field(default=4, ge=0, le=50)

    @model_validator(mode="before")
    @classmethod
    def _normalize_climax_type_aliases(cls, data: Any) -> Any:
        """Normalize common LLM enum aliases before strict literal validation."""

        if not isinstance(data, dict):
            return data
        fixed = dict(data)
        if "climax_type" in fixed:
            fixed["climax_type"] = _normalize_climax_type(fixed.get("climax_type"))
        if "expected_aftermath_chapters" not in fixed:
            for alias in (
                "aftermath_chapters",
                "expected_aftermath",
                "aftermath_window",
                "chapter_window",
            ):
                if alias in fixed:
                    fixed["expected_aftermath_chapters"] = fixed.pop(alias)
                    break
        if "expected_aftermath_chapters" in fixed:
            parsed_aftermath = _coerce_aftermath_chapter_count(
                fixed.get("expected_aftermath_chapters"),
                chapter_number=fixed.get("chapter_number"),
            )
            if parsed_aftermath is not None:
                fixed["expected_aftermath_chapters"] = parsed_aftermath
        return fixed


class DenouementBudget(VersionedSchema):
    """Budget for what may happen after the main climax."""

    expected_chapters: int = Field(default=4, ge=0, le=80)
    max_confirmation_scenes: int = Field(default=2, ge=0, le=20)
    required_new_functions: list[str] = Field(
        min_length=1,
        description="高潮后章节必须承担的新功能，如余波后果、关系制度化、尾声意象。",
    )
    forbidden_repeats: list[str] = Field(
        default_factory=list,
        description="高潮后不得反复执行的确认型场景或主题句。",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_denouement_budget_payload(cls, data: Any) -> Any:
        """Fold common LLM list-as-text drift into strict list fields."""

        if not isinstance(data, dict):
            return data
        fixed = dict(data)
        for key in ("required_new_functions", "forbidden_repeats"):
            aliases = (f"{key}_description",)
            values = _coerce_policy_text_list(fixed.get(key)) if key in fixed else []
            found = key in fixed
            for alias in aliases:
                if alias not in fixed:
                    continue
                found = True
                values.extend(_coerce_policy_text_list(fixed.pop(alias)))
            if found:
                fixed[key] = list(dict.fromkeys(values))
        return fixed


class SymbolPolicy(VersionedSchema):
    """How one symbol or motif should recur without over-explaining itself."""

    symbol: str = Field(min_length=1)
    narrative_function: str = Field(min_length=1)
    explanation_policy: Literal[
        "never_explain",
        "explain_once",
        "explain_on_escalation",
        "free",
    ] = "explain_once"
    escalation_rule: str = Field(default="")
    max_explicit_explanations: int = Field(default=1, ge=0, le=10)

    @model_validator(mode="before")
    @classmethod
    def _normalize_symbol_policy_aliases(cls, data: Any) -> Any:
        """Fold recoverable LLM drift into the strict SymbolPolicy shape."""

        if not isinstance(data, dict):
            return data
        fixed = dict(data)
        forbidden_note = fixed.pop("forbidden_explanation", None)
        if forbidden_note is None:
            forbidden_note = fixed.pop("forbidden_explanations", None)
        if forbidden_note is not None:
            fixed["escalation_rule"] = _append_policy_note(
                fixed.get("escalation_rule"),
                forbidden_note,
            )

        if "explanation_policy" in fixed:
            fixed["explanation_policy"] = _normalize_explanation_policy(
                fixed.get("explanation_policy"),
            )
        if "max_explicit_explanations" in fixed:
            parsed = _coerce_budget_int(fixed.get("max_explicit_explanations"))
            if parsed is not None:
                fixed["max_explicit_explanations"] = _clamp_budget_int(
                    parsed,
                    minimum=0,
                    maximum=10,
                )
        if fixed.get("explanation_policy") == "never_explain":
            fixed["max_explicit_explanations"] = 0
        return fixed


class SceneResistanceRule(VersionedSchema):
    """Scene-level friction requirements for turning atmosphere into drama."""

    scene_type: str = Field(min_length=1)
    required_resistance: str = Field(min_length=1)
    examples: list[str] = Field(
        min_length=1,
        description="空间、流程、人群、物件或信息阻力示例。",
    )

    @model_validator(mode="before")
    @classmethod
    def _fold_known_extra_scene_fields(cls, data: Any) -> Any:
        """Fold common window annotations into the allowed scene-resistance fields."""

        if not isinstance(data, dict):
            return data
        fixed = dict(data)
        window_note = fixed.pop("scene_resistance_chapter_windows", None)
        if window_note is None:
            window_note = fixed.pop("chapter_windows", None)
        if window_note is None:
            window_note = fixed.pop("target_chapter_windows", None)
        if window_note is not None:
            fixed["required_resistance"] = _append_policy_note(
                fixed.get("required_resistance"),
                f"适用窗口：{window_note}",
            )
        if "examples" in fixed:
            fixed["examples"] = _coerce_text_list(fixed.get("examples"))
        if not fixed.get("examples"):
            scene_type = str(fixed.get("scene_type") or "场景").strip() or "场景"
            fixed["examples"] = [f"{scene_type}需要通过环境、流程或物件制造阻力"]
        return fixed


class EditorialExpressionChannelProfile(VersionedSchema):
    """Project-specific expression channel that should cool down across chapters."""

    channel_id: str = Field(min_length=1, description="Stable snake_case channel id.")
    channel: Literal[
        "somatic_reaction",
        "action_tag",
        "dialogue_tag",
        "sensory_anchor",
        "sentence_pattern",
        "other",
    ] = "other"
    label: str = Field(min_length=1, description="Human-readable channel label.")
    surface_forms: list[str] = Field(
        min_length=1,
        description="Natural-language phrases only; never regex.",
    )
    trigger_contexts: list[str] = Field(default_factory=list)
    risk_reason: str = Field(min_length=1)
    replacement_axes: list[str] = Field(min_length=1)
    allowed_when: str = Field(min_length=1)
    cooldown_chapters: int = Field(default=3, ge=0, le=12)
    actor_scope: str = "global"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    provenance: str = "init_contract"
    evidence_quotes: list[str] = Field(
        default_factory=list,
        description="Optional verified source quotes for profile refresh; not projected to prompts.",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_profile_payload(cls, data: Any) -> Any:
        """Fold common LLM drift into the strict profile shape."""

        normalized = normalize_expression_channel_profiles(
            [data],
            max_items=1,
            min_confidence=0.0,
        )
        return normalized[0] if normalized else data


class RevelationStep(VersionedSchema):
    """Disclosure ladder step for mystery, memory, symbol, or past-life threads."""

    thread: str = Field(min_length=1, description="揭示线名称，如前世线、信物线、商业黑幕。")
    stage: str = Field(
        min_length=1,
        description="该级揭示名称，由所选叙述要素决定，如感应、物证、事件、后果。",
    )
    stage_order: int = Field(default=1, ge=1)
    target_chapter: int = Field(default=0, ge=0)
    trigger: str = Field(min_length=1, description="触发该级揭示的物件、地点、行动或冲突。")
    allowed_disclosure: str = Field(min_length=1, description="本级最多允许给出的信息。")
    required_action_consequence: str = Field(
        min_length=1,
        description="揭示后必须造成的行动后果，避免只加重情绪。",
    )


class EditorialElementDirective(VersionedSchema):
    """Project-specific directive derived from a selected narrative element."""

    element_id: str = Field(min_length=1, description="叙述要素库中的 element_id。")
    element_name: str = Field(default="", description="要素显示名，便于报告阅读。")
    directive_type: str = Field(
        min_length=1,
        description="本项目中该要素承担的编辑功能，禁止写成作品专属硬编码枚举。",
    )
    target_window: str = Field(
        min_length=1,
        description="建议落点，如前段、中段、主高潮前、余波预算内。",
    )
    linked_characters: list[str] = Field(
        default_factory=list,
        description="该指令影响的角色；不涉及角色时可为空。",
    )
    requirement: str = Field(
        min_length=1,
        description="该要素在本项目中必须落实的场景/结构要求。",
    )
    success_criteria: str = Field(min_length=1, description="完成后读者应能看见的变化。")


class TitlePolicy(VersionedSchema):
    """Chapter-title reuse and naming policy."""

    max_reuse: int = Field(default=2, ge=1, le=10)
    allowed_repeated_titles: list[str] = Field(default_factory=list)
    naming_strategy: str = Field(
        default="章节标题应提示节点功能；只保留少量有意回环标题。",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_title_policy_payload(cls, data: Any) -> Any:
        """Fold recoverable title-policy drift into the strict count semantics."""

        if not isinstance(data, dict):
            return data
        fixed = dict(data)

        allow_reuse = fixed.pop("allow_reuse", None)
        allow_repeated_titles = fixed.pop("allow_repeated_titles", None)

        if "max_reuse" not in fixed:
            for alias in (
                "reuse_limit",
                "max_repeat",
                "max_repeats",
                "maximum_reuse",
                "maximum_repeats",
            ):
                if alias in fixed:
                    fixed["max_reuse"] = fixed.pop(alias)
                    break
        else:
            for alias in (
                "reuse_limit",
                "max_repeat",
                "max_repeats",
                "maximum_reuse",
                "maximum_repeats",
            ):
                fixed.pop(alias, None)

        if allow_reuse is False or allow_repeated_titles is False:
            fixed["max_reuse"] = 1

        if "max_reuse" in fixed:
            parsed = _coerce_budget_int(fixed.get("max_reuse"))
            if parsed is None:
                raw_reuse = str(fixed.get("max_reuse") or "").strip().lower()
                if any(token in raw_reuse for token in _TITLE_NO_REUSE_TOKENS):
                    fixed["max_reuse"] = 1
                else:
                    fixed["max_reuse"] = 2
            else:
                fixed["max_reuse"] = _clamp_budget_int(parsed, minimum=1, maximum=10)

        if "allowed_repeated_titles" not in fixed and allow_repeated_titles not in (None, False):
            fixed["allowed_repeated_titles"] = allow_repeated_titles
        if "allowed_repeated_titles" not in fixed:
            for alias in ("allowed_titles", "repeatable_titles", "repeated_titles"):
                if alias in fixed:
                    fixed["allowed_repeated_titles"] = fixed.pop(alias)
                    break
        else:
            for alias in ("allowed_titles", "repeatable_titles", "repeated_titles"):
                fixed.pop(alias, None)
        if "allowed_repeated_titles" in fixed:
            fixed["allowed_repeated_titles"] = _coerce_text_list(
                fixed.get("allowed_repeated_titles")
            )

        if not str(fixed.get("naming_strategy") or "").strip():
            fixed["naming_strategy"] = "章节标题应提示节点功能；只保留少量有意回环标题。"

        return fixed


class EditorialContract(VersionedSchema):
    """Single source of truth for publication-grade editorial constraints."""

    project_title: str = Field(default="")
    character_voices: list[CharacterVoiceProfile] = Field(min_length=1)
    climax_markers: list[ClimaxMarker] = Field(min_length=1)
    denouement_budget: DenouementBudget
    theme_policies: list[str] = Field(
        min_length=1,
        description="主题呈现规则：通过选择/动作/冲突呈现，限制独白和解释。",
    )
    symbol_policies: list[SymbolPolicy] = Field(min_length=1)
    scene_resistance_rules: list[SceneResistanceRule] = Field(min_length=1)
    expression_channel_budget: dict[str, int] = Field(
        default_factory=lambda: {
            "somatic_reaction": 1,
            "action_tag": 2,
            "dialogue_tag": 2,
            "sensory_anchor": 3,
        },
        description="单章/单场表达通道预算。",
    )
    expression_channel_profiles: list[EditorialExpressionChannelProfile] = Field(
        default_factory=list,
        description="项目级表达通道画像，供生成、精修和拟人化层做冷却提示。",
    )
    body_signal_budget_per_high_emotion_scene: int = Field(default=1, ge=0, le=5)
    forbidden_confirmation_phrases: list[str] = Field(default_factory=list)
    revision_priorities: list[str] = Field(default_factory=list)
    revelation_ladder: list[RevelationStep] = Field(
        default_factory=list,
        description="重要悬疑/记忆/信物揭示的分级发放计划。",
    )
    editorial_element_directives: list[EditorialElementDirective] = Field(
        default_factory=list,
        description="由叙述要素库扩展出的项目级编辑指令。",
    )
    time_bridge_policies: list[str] = Field(
        default_factory=list,
        description="大跨度时间转场的标记规则。",
    )
    title_policy: TitlePolicy = Field(default_factory=TitlePolicy)

    @model_validator(mode="before")
    @classmethod
    def _normalize_contract_payload(cls, data: Any) -> Any:
        """Normalize recoverable nested LLM drift before strict validation."""

        if not isinstance(data, dict):
            return data
        fixed = dict(data)
        if "expression_channel_budget" in fixed:
            fixed["expression_channel_budget"] = _normalize_expression_channel_budget(
                fixed.get("expression_channel_budget"),
            )
        if "expression_channel_profiles" in fixed:
            fixed["expression_channel_profiles"] = normalize_expression_channel_profiles(
                fixed.get("expression_channel_profiles"),
                max_items=_EXPRESSION_PROFILE_MAX_ITEMS,
                min_confidence=_EXPRESSION_PROFILE_MIN_CONFIDENCE,
                provenance="init_contract",
            )
        for key in (
            "theme_policies",
            "forbidden_confirmation_phrases",
            "revision_priorities",
            "time_bridge_policies",
        ):
            if key in fixed:
                fixed[key] = _coerce_policy_text_list(fixed.get(key))
        if "revelation_ladder" in fixed:
            fixed["revelation_ladder"] = _normalize_revelation_ladder_payload(
                fixed.get("revelation_ladder"),
            )

        body_signal_budget = fixed.get("body_signal_budget_per_high_emotion_scene")
        if body_signal_budget is not None:
            parsed_body_signal_budget = _coerce_budget_int(body_signal_budget)
            if parsed_body_signal_budget is not None:
                fixed["body_signal_budget_per_high_emotion_scene"] = _clamp_budget_int(
                    parsed_body_signal_budget,
                )
        if body_signal_budget is None and isinstance(data.get("expression_channel_budget"), dict):
            for key in ("per_high_emotion_scene_limit", "high_emotion_scene_limit"):
                if key in data["expression_channel_budget"]:
                    parsed = _coerce_budget_int(data["expression_channel_budget"][key])
                    if parsed is not None:
                        fixed["body_signal_budget_per_high_emotion_scene"] = _clamp_budget_int(
                            parsed,
                        )
                        break
        return fixed

    @model_validator(mode="after")
    def _require_main_climax(self) -> "EditorialContract":
        if not any(marker.climax_type == "main" for marker in self.climax_markers):
            raise ValueError("EditorialContract requires at least one main climax marker.")
        if not {voice.character for voice in self.character_voices}:
            raise ValueError("EditorialContract requires character voice profiles.")
        return self

    def main_climax(self) -> ClimaxMarker:
        """Return the first main climax marker."""

        for marker in self.climax_markers:
            if marker.climax_type == "main":
                return marker
        raise ValueError("EditorialContract has no main climax marker.")


class EditorialFinding(VersionedSchema):
    """One local or LLM editorial finding."""

    issue_type: str = Field(min_length=1)
    severity: Literal["critical", "high", "medium", "low", "info"] = "medium"
    chapter_number: int = Field(default=0, ge=0)
    summary: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)
    recommendation: str = Field(default="")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("severity", mode="before")
    @classmethod
    def _normalize_severity_aliases(cls, value: Any) -> str:
        return _normalize_editorial_severity(value, allow_info=True)


class EditorialAuditReport(VersionedSchema):
    """Whole-book or chapter-level editorial audit result."""

    summary: str = Field(default="")
    findings: list[EditorialFinding] = Field(default_factory=list)
    revision_plan: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class EditorialRevisionAction(VersionedSchema):
    """Structured revision action derived from editorial audit findings."""

    action_type: str = Field(min_length=1)
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    chapter_range: list[int] = Field(default_factory=list)
    target: str = Field(default="")
    rationale: str = Field(min_length=1)
    instruction: str = Field(min_length=1)

    @field_validator("priority", mode="before")
    @classmethod
    def _normalize_priority_aliases(cls, value: Any) -> str:
        return _normalize_editorial_severity(value, allow_info=False)
