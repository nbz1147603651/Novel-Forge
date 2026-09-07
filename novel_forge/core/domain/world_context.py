"""World-context rule helpers for StoryBible-derived writing constraints."""

from __future__ import annotations

from typing import Any

WORLD_CONTEXT_LIST_FIELDS: tuple[str, ...] = (
    "address_rules",
    "self_reference_rules",
    "etiquette_rules",
    "institution_terms",
    "material_culture",
    "anachronism_blacklist",
    "dialogue_register_rules",
)

OPTIONAL_WORLD_CONTEXT_FIELDS: tuple[str, ...] = (
    "time_convention",
    "social_hierarchy",
    *WORLD_CONTEXT_LIST_FIELDS,
)

WORLD_CONTEXT_FIELD_LABELS: dict[str, str] = {
    "time_convention": "时间表达",
    "world_rules": "世界硬规则",
    "social_hierarchy": "社会等级",
    "address_rules": "称谓规则",
    "self_reference_rules": "自称规则",
    "etiquette_rules": "礼制/行为边界",
    "institution_terms": "制度术语",
    "material_culture": "时代器物",
    "anachronism_blacklist": "时代错位禁用",
    "dialogue_register_rules": "对白语体",
}


def _get_field(source: Any, field_name: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(field_name, default)
    return getattr(source, field_name, default)


def coerce_rule_list(value: Any, *, max_items: int = 20, max_chars: int = 120) -> list[str]:
    """Return a compact list of non-empty string rules."""
    if isinstance(value, str):
        value = [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            normalized.append(text[:max_chars])
        if len(normalized) >= max_items:
            break
    return normalized


def extract_world_context(source: Any) -> dict[str, Any]:
    """Extract structured world-context rules from a StoryBible-like object."""
    if source is None:
        return {}
    social_hierarchy = str(_get_field(source, "social_hierarchy", "") or "").strip()
    context: dict[str, Any] = {}
    time_convention = str(_get_field(source, "time_convention", "") or "").strip()
    if time_convention:
        context["time_convention"] = time_convention[:300]
    if social_hierarchy:
        context["social_hierarchy"] = social_hierarchy[:500]
    for field_name in WORLD_CONTEXT_LIST_FIELDS:
        values = coerce_rule_list(_get_field(source, field_name, []))
        if values:
            context[field_name] = values
    rule_book = _get_field(source, "world_rule_book", None)
    raw_rules = getattr(rule_book, "rules", None)
    if raw_rules is None and isinstance(rule_book, dict):
        raw_rules = rule_book.get("rules", [])
    rules: list[str] = []
    for item in list(raw_rules or []):
        severity = item.get("severity") if isinstance(item, dict) else getattr(item, "severity", "")
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", "")
        if str(severity or "hard") == "hard" and str(content or "").strip():
            rules.append(str(content).strip()[:240])
    if not rules:
        rules = coerce_rule_list(_get_field(source, "rules", []), max_items=14, max_chars=240)
    if rules:
        context["world_rules"] = rules[:14]
    return context


def prune_empty_world_context_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Remove empty optional world-context fields from a StoryBible payload."""
    pruned = dict(data)
    for field_name in OPTIONAL_WORLD_CONTEXT_FIELDS:
        if pruned.get(field_name) in (None, "", [], {}):
            pruned.pop(field_name, None)
    return pruned


def dump_story_bible_for_prompt(source: Any, *, mode: str = "python") -> dict[str, Any]:
    """Dump a StoryBible-like object without empty optional world-context fields."""
    if source is None:
        return {}
    if isinstance(source, dict):
        return prune_empty_world_context_fields(source)
    if hasattr(source, "model_dump"):
        return prune_empty_world_context_fields(source.model_dump(mode=mode))
    return {}


def render_world_context_rules(source: Any, *, include_world_rules: bool = True) -> str:
    """Render world context while allowing stage cards to own rule selection.

    ``include_world_rules=False`` is used by stages that already receive the
    bounded ``world_rule_card``.  This preserves time/register guidance
    without re-injecting the complete compatibility summary into the prompt.
    """
    context = extract_world_context(source)
    if not context:
        return ""
    lines: list[str] = []
    time_convention = str(context.get("time_convention", "") or "").strip()
    if time_convention:
        lines.append(f"- {WORLD_CONTEXT_FIELD_LABELS['time_convention']}：{time_convention}")
    world_rules = coerce_rule_list(context.get("world_rules", []), max_items=14, max_chars=240)
    if include_world_rules and world_rules:
        lines.append(
            f"- {WORLD_CONTEXT_FIELD_LABELS['world_rules']}：{'；'.join(world_rules)}"
        )
    social_hierarchy = str(context.get("social_hierarchy", "") or "").strip()
    if social_hierarchy:
        lines.append(f"- {WORLD_CONTEXT_FIELD_LABELS['social_hierarchy']}：{social_hierarchy}")
    for field_name in WORLD_CONTEXT_LIST_FIELDS:
        values = coerce_rule_list(context.get(field_name, []))
        if values:
            lines.append(
                f"- {WORLD_CONTEXT_FIELD_LABELS[field_name]}：{'；'.join(values)}"
            )
    return "\n".join(lines)


def format_address_rules_for_prompt(source: Any) -> str:
    """Return address/self-reference/dialogue rules for checking and repair prompts."""
    context = extract_world_context(source)
    parts: list[str] = []
    for field_name in (
        "address_rules",
        "self_reference_rules",
        "dialogue_register_rules",
        "etiquette_rules",
    ):
        values = coerce_rule_list(context.get(field_name, []))
        if values:
            parts.append(f"{WORLD_CONTEXT_FIELD_LABELS[field_name]}：{'；'.join(values)}")
    return "；".join(parts)


__all__ = [
    "OPTIONAL_WORLD_CONTEXT_FIELDS",
    "WORLD_CONTEXT_FIELD_LABELS",
    "WORLD_CONTEXT_LIST_FIELDS",
    "coerce_rule_list",
    "dump_story_bible_for_prompt",
    "extract_world_context",
    "format_address_rules_for_prompt",
    "prune_empty_world_context_fields",
    "render_world_context_rules",
]
