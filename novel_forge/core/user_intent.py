"""Shared in-memory user-intent projections for generation pipelines.

The request/project metadata remains the source of truth.  These helpers only
build bounded governance cards for prompts and runtime validation; they never
create a second persisted intent store.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

_INTENT_FIELDS: tuple[str, ...] = (
    "premise",
    "genre",
    "tone",
    "title",
    "language",
    "characters_hint",
    "world_hint",
    "conflict_hint",
    "pov_hint",
    "opening_style",
    "ending_style",
    "extra_instructions",
)

_SPEC_FIELD_MAP: dict[str, str] = {
    "premise": "theme",
    "genre": "genre",
    "tone": "tone",
    "title": "title",
    "language": "language",
    "characters_hint": "characters_hint",
    "world_hint": "world_hint",
    "conflict_hint": "conflict_hint",
    "pov_hint": "pov_hint",
    "opening_style": "opening_style",
    "ending_style": "ending_style",
    "extra_instructions": "extra_instructions",
}

USER_INTENT_AUTHORITY_ORDER: tuple[str, ...] = (
    "current_user_instruction",
    "user_explicit_input",
    "user_locked_elements_or_human_edits",
    "accepted_project_facts",
    "verified_external_facts",
    "ai_creative_direction",
    "style_and_inspiration",
)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _locked_blueprint_elements(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, Mapping):
        return []
    result: list[dict[str, str]] = []
    for item in raw.get("items") or ():
        if not isinstance(item, Mapping) or not bool(item.get("locked")):
            continue
        element_id = _clean_text(item.get("element_id") or item.get("elementId") or item.get("id"))
        if not element_id:
            continue
        enabled = bool(item.get("enabled"))
        result.append(
            {
                "intent_id": f"locked_element:{element_id}",
                "element_id": element_id,
                "decision": "include" if enabled else "exclude",
            }
        )
    return result


def _card_from_explicit_source(
    source: Mapping[str, Any],
    *,
    field_map: Mapping[str, str],
    blueprint_element_preferences: Any = None,
) -> dict[str, Any]:
    explicit: list[dict[str, str]] = []
    for intent_field in _INTENT_FIELDS:
        source_field = field_map.get(intent_field, intent_field)
        value = _clean_text(source.get(source_field))
        if not value:
            continue
        explicit.append(
            {
                "intent_id": f"user:{intent_field}",
                "field": intent_field,
                "value": value,
            }
        )
    locked = _locked_blueprint_elements(blueprint_element_preferences)
    immutable_ids = [item["intent_id"] for item in explicit]
    immutable_ids.extend(item["intent_id"] for item in locked)
    return {
        "schema": "user_intent_card_v1",
        "authority": "highest",
        "authority_order": list(USER_INTENT_AUTHORITY_ORDER),
        "explicit_intents": explicit,
        "locked_blueprint_elements": locked,
        "immutable_intent_ids": immutable_ids,
        "allowed_ai_actions": ["fill_blank", "detail_path", "propose_implementation"],
        "forbidden_ai_actions": [
            "rewrite_user_intent",
            "repair_by_changing_user_intent",
            "silently_override_human_edit",
        ],
    }


def build_user_intent_card(request_payload: Mapping[str, Any]) -> dict[str, Any]:
    """Project a long-init request snapshot into the stable v1 card."""

    init_input = request_payload.get("init_input")
    generation_options = request_payload.get("generation_options")
    source = init_input if isinstance(init_input, Mapping) else {}
    options = generation_options if isinstance(generation_options, Mapping) else {}
    return _card_from_explicit_source(
        source,
        field_map={field: field for field in _INTENT_FIELDS},
        blueprint_element_preferences=options.get("blueprint_element_preferences"),
    )


def build_story_spec_user_intent_card(
    spec_payload: Mapping[str, Any],
    *,
    blueprint_element_preferences: Any = None,
) -> dict[str, Any]:
    """Project a raw short/long StorySpec-shaped payload without AI enrichment."""

    return _card_from_explicit_source(
        spec_payload,
        field_map=_SPEC_FIELD_MAP,
        blueprint_element_preferences=blueprint_element_preferences,
    )


def build_persisted_user_intent_card(
    metadata: Mapping[str, Any],
    accepted_spec: Mapping[str, Any],
) -> dict[str, Any]:
    """Never promote an enriched/legacy Spec to explicit author instructions."""
    request = metadata.get("request")
    card = build_user_intent_card(request if isinstance(request, Mapping) else {})
    card["source"] = (
        "init_request_snapshot" if isinstance(request, Mapping) else "legacy_accepted_facts"
    )
    card["scope"] = "book"
    card["accepted_project_facts"] = deepcopy(dict(accepted_spec))
    overrides = metadata.get("author_input_overrides")
    if isinstance(overrides, Mapping):
        for field, entry in overrides.items():
            if (
                field not in _INTENT_FIELDS
                or not isinstance(entry, Mapping)
                or entry.get("source") != "author_approved_proposal"
                or entry.get("scope") != "book"
                or not entry.get("revision_id")
            ):
                continue
            intent_id = f"user:{field}"
            card["explicit_intents"] = [
                i for i in card["explicit_intents"] if i["intent_id"] != intent_id
            ]
            card["immutable_intent_ids"] = [
                i for i in card["immutable_intent_ids"] if i != intent_id
            ]
            if _clean_text(entry.get("value")):
                card["explicit_intents"].append(
                    {
                        "intent_id": intent_id,
                        "field": field,
                        "value": _clean_text(entry["value"]),
                        "source": "author_approved_proposal",
                        "scope": "book",
                        "revision_id": entry["revision_id"],
                    }
                )
                card["immutable_intent_ids"].append(intent_id)
        card["author_input_overrides"] = deepcopy(dict(overrides))
    return card


def with_chapter_instruction(
    user_intent: Mapping[str, Any],
    instruction: str,
    *,
    chapter_number: int,
) -> dict[str, Any]:
    """Return a run-local card with the current human instruction at top authority."""

    card = deepcopy(dict(user_intent))
    value = _clean_text(instruction)
    if not value:
        return card
    intent_id = f"user:chapter_instruction:{max(1, int(chapter_number))}"
    explicit = [
        item
        for item in list(card.get("explicit_intents") or [])
        if isinstance(item, Mapping) and item.get("intent_id") != intent_id
    ]
    explicit.insert(
        0,
        {
            "intent_id": intent_id,
            "field": "chapter_instruction",
            "value": value,
        },
    )
    immutable = [
        str(item) for item in list(card.get("immutable_intent_ids") or []) if str(item) != intent_id
    ]
    immutable.insert(0, intent_id)
    card["explicit_intents"] = explicit
    card["immutable_intent_ids"] = immutable
    card["chapter_instruction"] = {
        "chapter_number": max(1, int(chapter_number)),
        "intent_id": intent_id,
        "value": value,
    }
    return card


def project_user_intent_guard_constraints(
    user_intent: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Project immutable intent into chapter-safe guard constraints.

    The constraints prohibit contradiction, not omission.  This distinction is
    important for book-level intentions such as the ending: an intermediate
    chapter need not realize the ending, but it must not silently replace it.
    All immutable items are deliberately grouped into one guard input so that
    enabling the chapter intent guard adds at most one model check rather than
    one call per user field.
    """

    rules: list[str] = []
    intent_ids: list[str] = []
    fields: list[str] = []
    for item in user_intent.get("explicit_intents") or ():
        if not isinstance(item, Mapping):
            continue
        intent_id = _clean_text(item.get("intent_id"))
        field = _clean_text(item.get("field"))
        value = _clean_text(item.get("value"))
        if not intent_id or not field or not value:
            continue
        intent_ids.append(intent_id)
        fields.append(field)
        rules.append(
            f"- [{intent_id}/{field}] {value}。本章未涉及时视为遵守；"
            "只有正文明确取消、反转或改写该意图时才判定违约。"
        )
    for item in user_intent.get("locked_blueprint_elements") or ():
        if not isinstance(item, Mapping):
            continue
        intent_id = _clean_text(item.get("intent_id"))
        element_id = _clean_text(item.get("element_id"))
        decision = _clean_text(item.get("decision"))
        if not intent_id or not element_id or decision not in {"include", "exclude"}:
            continue
        intent_ids.append(intent_id)
        fields.append("locked_blueprint_element")
        if decision == "exclude":
            rule = f"用户锁定排除要素 {element_id}：本章不得引入或恢复该要素。"
        else:
            rule = (
                f"用户锁定保留要素 {element_id}：本章可以不展开，"
                "但不得明确取消、否定或改成互斥要素。"
            )
        rules.append(f"- [{intent_id}/locked_blueprint_element] {rule}")
    if not rules:
        return []
    return [
        {
            "intent_id": "user_intent_card_v1",
            "intent_ids": list(dict.fromkeys(intent_ids)),
            "field": "immutable_user_intent",
            "fields": list(dict.fromkeys(fields)),
            "constraint": (
                "请逐项检查本章是否明确取消、反转或改写下列用户不可改写意图。"
                "未涉及某项不等于违约；只有正文存在明确冲突才判定不合规：\n" + "\n".join(rules)
            ),
        }
    ]


def enforce_explicit_input_on_story_spec(
    spec: Any,
    request_payload: Mapping[str, Any],
    *,
    expected_total_words: int,
) -> tuple[Any, tuple[str, ...]]:
    """Overlay non-empty request fields after Spec enrichment; AI fills blanks only."""

    init_input = request_payload.get("init_input")
    source = init_input if isinstance(init_input, Mapping) else {}
    updates: dict[str, Any] = {}
    changed: list[str] = []
    for request_field, spec_field in _SPEC_FIELD_MAP.items():
        value = _clean_text(source.get(request_field))
        if not value:
            continue
        updates[spec_field] = value
        if _clean_text(getattr(spec, spec_field, "")) != value:
            changed.append(spec_field)
    updates["length_target"] = max(1, int(expected_total_words))
    if int(getattr(spec, "length_target", 0) or 0) != updates["length_target"]:
        changed.append("length_target")
    return spec.model_copy(update=updates), tuple(dict.fromkeys(changed))


def candidate_intent_conflicts(
    *,
    user_intent: Mapping[str, Any],
    preserved_intent_ids: list[str],
    declared_conflicts: list[str],
    scene_potential: list[str],
    candidate_notes: str = "",
) -> list[str]:
    """Return deterministic conflict reasons without inferring creative meaning locally."""

    conflicts = [_clean_text(item) for item in declared_conflicts if _clean_text(item)]
    preserved = {_clean_text(item) for item in preserved_intent_ids if _clean_text(item)}
    required = {
        _clean_text(item)
        for item in user_intent.get("immutable_intent_ids") or ()
        if _clean_text(item)
    }
    missing = sorted(required - preserved)
    conflicts.extend(f"未确认保留用户意图：{intent_id}" for intent_id in missing)

    selected_text = " ".join(
        [*(_clean_text(item) for item in scene_potential), _clean_text(candidate_notes)]
    )
    normalized_candidate = selected_text.lower()
    explicit_by_field = {
        _clean_text(item.get("field")): _clean_text(item.get("value"))
        for item in user_intent.get("explicit_intents") or ()
        if isinstance(item, Mapping)
    }
    ending = explicit_by_field.get("ending_style", "").lower()
    happy_ending_requested = any(
        marker in ending for marker in ("he", "happy ending", "圆满", "幸福", "大团圆")
    )
    tragic_candidate = any(
        marker in normalized_candidate
        for marker in ("be结局", "bad ending", "悲剧结局", "全灭", "死亡结局")
    )
    if happy_ending_requested and tragic_candidate:
        conflicts.append("候选结局与用户指定的 HE/圆满结局冲突")

    pov_hint = explicit_by_field.get("pov_hint", "").lower()
    single_pov_requested = any(
        marker in pov_hint for marker in ("单pov", "单 pov", "单一视角", "single pov")
    )
    multiple_pov_candidate = any(
        marker in normalized_candidate
        for marker in ("多pov", "多 pov", "多视角", "multiple pov", "pov切换")
    )
    if single_pov_requested and multiple_pov_candidate:
        conflicts.append("候选视角策略与用户指定的单 POV 冲突")
    for item in user_intent.get("locked_blueprint_elements") or ():
        if not isinstance(item, Mapping) or item.get("decision") != "exclude":
            continue
        element_id = _clean_text(item.get("element_id"))
        if element_id and element_id in selected_text:
            conflicts.append(f"候选重新引入 locked exclude 要素：{element_id}")
    return list(dict.fromkeys(conflicts))


__all__ = (
    "USER_INTENT_AUTHORITY_ORDER",
    "build_story_spec_user_intent_card",
    "build_user_intent_card",
    "candidate_intent_conflicts",
    "enforce_explicit_input_on_story_spec",
    "project_user_intent_guard_constraints",
    "with_chapter_instruction",
)
