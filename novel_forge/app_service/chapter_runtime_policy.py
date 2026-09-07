"""Shared chapter quality/research policy presets for every desktop client."""

from __future__ import annotations

from typing import Any, Literal

ChapterRuntimePreset = Literal["compat", "safe", "balanced", "enhanced", "custom"]

_POLICY_FIELDS = (
    "intent_guard_mode",
    "fact_refresh_enabled",
    "inspiration_enabled",
    "inspiration_cooldown",
    "short_adaptive_revision_enabled",
    "long_single_final_verify_enabled",
)

CHAPTER_RUNTIME_POLICY_PRESETS: dict[str, dict[str, Any]] = {
    "compat": {
        "label": "兼容当前",
        "description": "保持现有默认调用量；记录意图冲突但不新增章节联网或自适应修订。",
        "intent_guard_mode": "warn",
        "fact_refresh_enabled": False,
        "inspiration_enabled": False,
        "inspiration_cooldown": 3,
        "short_adaptive_revision_enabled": False,
        "long_single_final_verify_enabled": False,
    },
    "safe": {
        "label": "稳健",
        "description": "阻断意图冲突，只补必须事实，并启用有界修订与最终验证。",
        "intent_guard_mode": "block",
        "fact_refresh_enabled": True,
        "inspiration_enabled": False,
        "inspiration_cooldown": 3,
        "short_adaptive_revision_enabled": True,
        "long_single_final_verify_enabled": True,
    },
    "balanced": {
        "label": "均衡",
        "description": "在稳健门禁上加入三章冷却的低频灵感，兼顾质量、成本与多样性。",
        "intent_guard_mode": "block",
        "fact_refresh_enabled": True,
        "inspiration_enabled": True,
        "inspiration_cooldown": 3,
        "short_adaptive_revision_enabled": True,
        "long_single_final_verify_enabled": True,
    },
    "enhanced": {
        "label": "增强多样性",
        "description": "允许更高频的抽象灵感刷新；仍受用户意图、证据权限和单章查询上限约束。",
        "intent_guard_mode": "block",
        "fact_refresh_enabled": True,
        "inspiration_enabled": True,
        "inspiration_cooldown": 1,
        "short_adaptive_revision_enabled": True,
        "long_single_final_verify_enabled": True,
    },
}

_SETTING_ATTRS = {
    "intent_guard_mode": "chapter_intent_guard_mode",
    "fact_refresh_enabled": "chapter_research_refresh_enabled",
    "inspiration_enabled": "chapter_research_inspiration_enabled",
    "inspiration_cooldown": "chapter_research_inspiration_cooldown",
    "short_adaptive_revision_enabled": "short_adaptive_revision_enabled",
    "long_single_final_verify_enabled": "long_single_final_verify_enabled",
}

_CREATION_PARAMETER_IDS = {
    "intent_guard_mode": "chapter-intent-guard-mode",
    "fact_refresh_enabled": "chapter-research-refresh-enabled",
    "inspiration_enabled": "chapter-research-inspiration-enabled",
    "inspiration_cooldown": "chapter-research-inspiration-cooldown",
    "short_adaptive_revision_enabled": "short-adaptive-revision-enabled",
    "long_single_final_verify_enabled": "long-single-final-verify-enabled",
}


def _bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if str(value).strip().casefold() in {"true", "1"}:
        return True
    if str(value).strip().casefold() in {"false", "0"}:
        return False
    raise ValueError(f"{field} must be boolean")


def normalize_chapter_runtime_policy(value: dict[str, Any]) -> dict[str, Any]:
    """Validate one policy draft and apply named presets atomically."""

    preset = str(value.get("preset") or "custom").strip().casefold()
    if preset not in {*CHAPTER_RUNTIME_POLICY_PRESETS, "custom"}:
        raise ValueError("unknown chapter runtime policy preset")
    source = CHAPTER_RUNTIME_POLICY_PRESETS[preset] if preset != "custom" else value
    intent_mode = str(source.get("intent_guard_mode") or "warn").strip().casefold()
    if intent_mode not in {"off", "warn", "block"}:
        raise ValueError("intent_guard_mode must be off, warn, or block")
    try:
        cooldown = int(source.get("inspiration_cooldown", 3))
    except (TypeError, ValueError) as exc:
        raise ValueError("inspiration_cooldown must be an integer") from exc
    if not 1 <= cooldown <= 20:
        raise ValueError("inspiration_cooldown must be between 1 and 20")
    normalized = {
        "preset": preset,
        "intent_guard_mode": intent_mode,
        "fact_refresh_enabled": _bool(
            source.get("fact_refresh_enabled", False), field="fact_refresh_enabled"
        ),
        "inspiration_enabled": _bool(
            source.get("inspiration_enabled", False), field="inspiration_enabled"
        ),
        "inspiration_cooldown": cooldown,
        "short_adaptive_revision_enabled": _bool(
            source.get("short_adaptive_revision_enabled", False),
            field="short_adaptive_revision_enabled",
        ),
        "long_single_final_verify_enabled": _bool(
            source.get("long_single_final_verify_enabled", False),
            field="long_single_final_verify_enabled",
        ),
    }
    if preset == "custom":
        normalized["preset"] = detect_chapter_runtime_preset(normalized)
    return normalized


def detect_chapter_runtime_preset(value: dict[str, Any]) -> ChapterRuntimePreset:
    for preset_id, definition in CHAPTER_RUNTIME_POLICY_PRESETS.items():
        if all(value.get(field) == definition.get(field) for field in _POLICY_FIELDS):
            return preset_id  # type: ignore[return-value]
    return "custom"


def project_chapter_runtime_policy(settings: Any) -> dict[str, Any]:
    current = {
        field: getattr(settings, attr, CHAPTER_RUNTIME_POLICY_PRESETS["compat"][field])
        for field, attr in _SETTING_ATTRS.items()
    }
    normalized = normalize_chapter_runtime_policy({"preset": "custom", **current})
    return {
        **normalized,
        "preset_options": [
            {
                "id": preset_id,
                "label": str(definition["label"]),
                "description": str(definition["description"]),
                "values": {field: definition[field] for field in _POLICY_FIELDS},
            }
            for preset_id, definition in CHAPTER_RUNTIME_POLICY_PRESETS.items()
        ],
    }


def chapter_runtime_policy_creation_parameters(value: dict[str, Any]) -> dict[str, str]:
    normalized = normalize_chapter_runtime_policy(value)
    result: dict[str, str] = {}
    for field, parameter_id in _CREATION_PARAMETER_IDS.items():
        item = normalized[field]
        result[parameter_id] = str(item).lower() if isinstance(item, bool) else str(item)
    return result


__all__ = [
    "CHAPTER_RUNTIME_POLICY_PRESETS",
    "chapter_runtime_policy_creation_parameters",
    "detect_chapter_runtime_preset",
    "normalize_chapter_runtime_policy",
    "project_chapter_runtime_policy",
]
