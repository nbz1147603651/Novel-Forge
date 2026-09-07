"""Utilities for chapter-contract knowledge operations.

Knowledge operations are contract-level promises about who learns, suspects,
misbelieves, or hides a fact in the current chapter.  StoryKernel's
``knowledge_ledger`` is the source of truth; these helpers only normalize the
contract promise so writing and adjudication stages can consume it consistently.
"""

from __future__ import annotations

import re
from typing import Any

_KNOWLEDGE_TYPE_ALIASES = {
    "known": "known",
    "know": "known",
    "learn": "known",
    "learned": "known",
    "reveal": "known",
    "revealed": "known",
    "confirm": "known",
    "confirmed": "known",
    "discover": "known",
    "discovered": "known",
    "知道": "known",
    "得知": "known",
    "获知": "known",
    "确认": "known",
    "发现": "known",
    "揭示": "known",
    "suspect": "suspected",
    "suspected": "suspected",
    "doubt": "suspected",
    "怀疑": "suspected",
    "猜测": "suspected",
    "误解": "misbelief",
    "误认": "misbelief",
    "misbelief": "misbelief",
    "false_belief": "misbelief",
    "secret": "secret_kept",
    "secret_kept": "secret_kept",
    "hide": "secret_kept",
    "hidden": "secret_kept",
    "隐瞒": "secret_kept",
    "保密": "secret_kept",
}

_CHARACTER_KEYS = (
    "character",
    "knower",
    "actor",
    "holder",
    "who",
    "person",
    "角色",
    "人物",
)
_ENTITY_ID_KEYS = ("entity_id", "character_id", "knower_id", "actor_id", "holder_id")
_FACT_KEYS = (
    "fact",
    "knowledge",
    "knowledge_name",
    "subject",
    "target",
    "description",
    "summary",
    "value",
    "信息",
    "事实",
)
_EVIDENCE_KEYS = (
    "evidence",
    "quote",
    "evidence_quote",
    "evidence_hint",
    "trigger",
    "text",
)


def normalize_knowledge_type(value: Any) -> str:
    """Return the StoryKernel knowledge type for a loose contract operation."""

    text = str(value or "").strip().lower()
    return _KNOWLEDGE_TYPE_ALIASES.get(text, "known")


def build_entity_lookup(entities: Any) -> dict[str, dict[str, str]]:
    """Build name/id lookup maps from StoryKernel entity slices."""

    id_to_name: dict[str, str] = {}
    name_to_id: dict[str, str] = {}
    if not isinstance(entities, (list, tuple)):
        return {"id_to_name": id_to_name, "name_to_id": name_to_id}
    for raw in entities:
        data = _as_mapping(raw)
        entity_id = _clean(data.get("entity_id"))
        name = _clean(data.get("name"))
        if entity_id and name:
            id_to_name[entity_id] = name
            name_to_id.setdefault(name, entity_id)
        aliases = data.get("aliases")
        if isinstance(aliases, list) and entity_id:
            for alias in aliases:
                alias_text = _clean(alias)
                if alias_text:
                    name_to_id.setdefault(alias_text, entity_id)
    return {"id_to_name": id_to_name, "name_to_id": name_to_id}


def normalize_knowledge_ops(
    raw_ops: Any,
    *,
    entity_lookup: dict[str, dict[str, str]] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Normalize loose ``knowledge_ops`` payloads into compact operation cards."""

    if not isinstance(raw_ops, (list, tuple)):
        return []
    normalized: list[dict[str, Any]] = []
    for raw in list(raw_ops)[: max(0, limit)]:
        op = normalize_knowledge_op(raw, entity_lookup=entity_lookup)
        if op:
            normalized.append(op)
    return normalized


def normalize_knowledge_op(
    raw: Any,
    *,
    entity_lookup: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Normalize one knowledge op without making semantic judgement."""

    if isinstance(raw, dict):
        data = dict(raw)
        operation = _clean(data.get("type") or data.get("operation") or data.get("op"))
        character = _first_text(data, _CHARACTER_KEYS)
        entity_id = _first_text(data, _ENTITY_ID_KEYS)
        fact = _first_text(data, _FACT_KEYS)
        evidence = _first_text(data, _EVIDENCE_KEYS)
    elif isinstance(raw, (list, tuple)):
        items = [_clean(item) for item in raw if _clean(item)]
        operation = items[0] if items else ""
        fact = items[1] if len(items) > 1 else (items[0] if items else "")
        character = items[2] if len(items) > 2 else ""
        entity_id = ""
        evidence = items[3] if len(items) > 3 else ""
    else:
        text = _clean(raw)
        if not text:
            return {}
        operation = ""
        character = ""
        entity_id = ""
        fact = text
        evidence = ""

    lookup = entity_lookup or {"id_to_name": {}, "name_to_id": {}}
    if entity_id and not character:
        character = lookup.get("id_to_name", {}).get(entity_id, "")
    if character and not entity_id:
        entity_id = lookup.get("name_to_id", {}).get(character, "")

    knowledge_type = normalize_knowledge_type(operation)
    if not fact and evidence:
        fact = evidence
    fact = _clean(fact, limit=180)
    evidence = _clean(evidence, limit=120)
    if not fact and not evidence:
        return {}

    return _drop_empty(
        {
            "operation": operation or knowledge_type,
            "knowledge_type": knowledge_type,
            "character": character,
            "entity_id": entity_id,
            "fact": fact,
            "description": fact,
            "evidence": evidence,
            "target_text": knowledge_op_target_text(
                {
                    "character": character,
                    "knowledge_type": knowledge_type,
                    "fact": fact,
                }
            ),
        }
    )


def knowledge_op_target_text(op: Any) -> str:
    """Render a normalized operation as an audit target string."""

    data = _as_mapping(op)
    character = _clean(data.get("character") or data.get("knower"))
    fact = _clean(data.get("fact") or data.get("description") or data.get("target"))
    knowledge_type = normalize_knowledge_type(data.get("knowledge_type") or data.get("type"))
    label = {
        "known": "获知",
        "suspected": "怀疑",
        "misbelief": "误信",
        "secret_kept": "隐瞒",
    }.get(knowledge_type, "获知")
    if character and fact:
        return f"{character}{label}：{fact}"
    if fact:
        return f"{label}：{fact}"
    return ""


def knowledge_op_evidence_candidates(op: Any) -> list[str]:
    """Return likely literal phrases that can prove a knowledge op in chapter text."""

    data = _as_mapping(op)
    candidates: list[str] = []
    for key in (*_EVIDENCE_KEYS, "fact", "description", "target", "subject", "value"):
        value = data.get(key)
        if isinstance(value, list):
            for item in value:
                _append_candidate(candidates, item)
        else:
            _append_candidate(candidates, value)
    target = knowledge_op_target_text(data)
    _append_candidate(candidates, target)
    return _dedupe(candidates)


def knowledge_op_entity_ids(op: Any) -> list[str]:
    """Return the preferred knower entity id/name carried by a normalized op."""

    data = _as_mapping(op)
    entity_id = _clean(data.get("entity_id"))
    if entity_id:
        return [entity_id]
    values = [_clean(data.get("character")), _clean(data.get("knower"))]
    return _dedupe(item for item in values if item)


def _append_candidate(result: list[str], value: Any) -> None:
    text = _clean(value)
    if not text:
        return
    result.append(text)
    for quoted in re.findall(r"[「『“\"]([^」』”\"]{1,80})[」』”\"]", text):
        if quoted.strip():
            result.append(quoted.strip())


def _first_text(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        text = _clean(data.get(key))
        if text:
            return text
    return ""


def _clean(value: Any, *, limit: int = 120) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip("，。、；： \n") + "…"


def _as_mapping(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, dict) else {}
    return dict(value) if isinstance(value, dict) else {}


def _drop_empty(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value not in (None, "", [], {})}


def _dedupe(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


__all__ = [
    "build_entity_lookup",
    "knowledge_op_entity_ids",
    "knowledge_op_evidence_candidates",
    "knowledge_op_target_text",
    "normalize_knowledge_op",
    "normalize_knowledge_ops",
    "normalize_knowledge_type",
]
