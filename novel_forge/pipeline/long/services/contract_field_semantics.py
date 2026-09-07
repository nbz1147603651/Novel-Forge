"""Deterministic field semantics for chapter-contract artifacts."""

from __future__ import annotations

from enum import Enum
from typing import Sequence


class ChapterContractFieldSemantic(str, Enum):
    """Meaning of a chapter-contract field for source artifact validation."""

    ENTITY_REF = "entity_ref"
    ENTITY_REF_MAP = "entity_ref_map"
    FACT_TEXT = "fact_text"
    FREE_TEXT = "free_text"
    NEW_ENTITY_CANDIDATE = "new_entity_candidate"
    STRUCTURAL = "structural"
    UNKNOWN = "unknown"


KNOWLEDGE_OP_FACT_KEYS = frozenset(
    {
        "subject",
        "target",
        "fact",
        "knowledge",
        "knowledge_name",
        "description",
        "summary",
        "value",
        "object",
    }
)

NEW_ENTITY_CANDIDATE_FIELDS = frozenset(
    {
        "new_character_candidates",
        "new_entity_candidates",
        "new_group_candidates",
        "new_collective_candidates",
        "new_organization_candidates",
        "new_location_candidates",
        "new_item_candidates",
        "new_concept_candidates",
    }
)

_WRAPPER_KEYS = frozenset({"chapter_contracts", "by_chapter", "payload"})
_ENTITY_REF_MAP_KEYS = frozenset({"character_knowledge_coverage"})

_TOP_LEVEL_ENTITY_REF_KEYS = frozenset(
    {
        "pov_character",
        "pov_character_id",
        "pov_entity_id",
        "involved_characters",
        "involved_character_names",
        "involved_character_ids",
        "required_character_ids",
        "support_character_ids",
        "canonical_entity_ids",
    }
)

_CAST_PLAN_ENTITY_REF_KEYS = frozenset(
    {
        "pov_entity_id",
        "pov_character_id",
        "required_character_ids",
        "support_character_ids",
        "mention_only_entity_ids",
        "forbidden_active_character_ids",
    }
)

_COGNITIVE_CONSTRAINT_ENTITY_REF_KEYS = frozenset({"cognitive_subjects"})
_EMOTIONAL_PLAN_ENTITY_REF_KEYS = frozenset(
    {"subject_entity_id", "pov_entity_id", "pov_character_id", "character_id", "entity_id"}
)

_KNOWLEDGE_OP_ENTITY_REF_KEYS = frozenset(
    {
        "character",
        "characters",
        "character_name",
        "character_names",
        "character_id",
        "character_ids",
        "entity_id",
        "entity_ids",
        "knower",
        "knowers",
        "actor",
        "actors",
        "holder",
        "holders",
        "owner",
        "owners",
    }
)

_ITEM_OP_ENTITY_REF_KEYS = frozenset(
    {
        "subject",
        "subjects",
        "object",
        "objects",
        "item",
        "items",
        "item_id",
        "item_ids",
        "entity",
        "entities",
        "entity_id",
        "entity_ids",
        "owner",
        "owners",
        "holder",
        "holders",
        "character",
        "characters",
        "character_id",
        "character_ids",
    }
)

_RELATIONSHIP_OP_ENTITY_REF_KEYS = frozenset(
    {
        "subject",
        "subjects",
        "object",
        "objects",
        "target",
        "targets",
        "from",
        "to",
        "character",
        "characters",
        "character_id",
        "character_ids",
        "entity_id",
        "entity_ids",
    }
)

_PROMISE_OP_ENTITY_REF_KEYS = frozenset(
    {
        "character",
        "characters",
        "character_id",
        "character_ids",
        "entity_id",
        "entity_ids",
        "owner",
        "owners",
        "holder",
        "holders",
        "promisor",
        "promisee",
    }
)

_ENTITY_KEYS_BY_CONTEXT: dict[str, frozenset[str]] = {
    "cast_plan": _CAST_PLAN_ENTITY_REF_KEYS,
    "cognitive_constraints": _COGNITIVE_CONSTRAINT_ENTITY_REF_KEYS,
    "emotional_plan": _EMOTIONAL_PLAN_ENTITY_REF_KEYS,
    "knowledge_ops": _KNOWLEDGE_OP_ENTITY_REF_KEYS,
    "item_ops": _ITEM_OP_ENTITY_REF_KEYS,
    "relationship_ops": _RELATIONSHIP_OP_ENTITY_REF_KEYS,
    "promise_ops": _PROMISE_OP_ENTITY_REF_KEYS,
}

_FREE_TEXT_KEYS = frozenset(
    {
        "description",
        "summary",
        "title",
        "entry_state",
        "pressure_source",
        "relationship_choice",
        "turning_emotion",
        "exit_aftertaste",
        "claim_text",
        "cognitive_object",
        "required_events",
        "allowed_changes",
        "forbidden_changes",
        "required_progressions",
        "allowed_progressions",
        "forbidden_progressions",
        "completion_criteria",
        "future_leak_risks",
        "scene_design_goals",
        "exit_state_targets",
    }
)

_STRUCTURAL_KEYS = frozenset(
    {
        "chapter_number",
        "chapter",
        "number",
        "source",
        "type",
        "claim_id",
        "cognitive_level",
        "action_level",
        "reader_awareness",
        "cognitive_chapter",
        "public_reveal_chapter",
        "foreshadow_chapters",
    }
)


def chapter_contract_field_semantic(path: Sequence[str]) -> ChapterContractFieldSemantic:
    """Return the configured semantic class for a chapter-contract field path."""

    parts = _normalize_path(path)
    if not parts:
        return ChapterContractFieldSemantic.UNKNOWN

    key = parts[-1]
    context = _nearest_context(parts[:-1])
    if key in NEW_ENTITY_CANDIDATE_FIELDS or context in NEW_ENTITY_CANDIDATE_FIELDS:
        return ChapterContractFieldSemantic.NEW_ENTITY_CANDIDATE
    if key in _ENTITY_REF_MAP_KEYS:
        return ChapterContractFieldSemantic.ENTITY_REF_MAP
    if context in _ENTITY_REF_MAP_KEYS:
        return ChapterContractFieldSemantic.ENTITY_REF
    if context == "knowledge_ops" and key in KNOWLEDGE_OP_FACT_KEYS:
        return ChapterContractFieldSemantic.FACT_TEXT
    if context and key in _ENTITY_KEYS_BY_CONTEXT.get(context, frozenset()):
        return ChapterContractFieldSemantic.ENTITY_REF
    if key in _TOP_LEVEL_ENTITY_REF_KEYS or _is_explicit_entity_ref_key(key):
        return ChapterContractFieldSemantic.ENTITY_REF
    if key in _FREE_TEXT_KEYS:
        return ChapterContractFieldSemantic.FREE_TEXT
    if key in _STRUCTURAL_KEYS:
        return ChapterContractFieldSemantic.STRUCTURAL
    return ChapterContractFieldSemantic.UNKNOWN


def is_chapter_contract_entity_ref_path(path: Sequence[str]) -> bool:
    """True when the field value is an entity reference and must be registered."""

    return chapter_contract_field_semantic(path) == ChapterContractFieldSemantic.ENTITY_REF


def is_chapter_contract_entity_id_path(path: Sequence[str]) -> bool:
    """True when the field stores entity ids rather than canonical names."""

    parts = _normalize_path(path)
    if not parts or not is_chapter_contract_entity_ref_path(path):
        return False
    return _is_explicit_entity_id_key(parts[-1])


def is_chapter_contract_entity_ref_map_path(path: Sequence[str]) -> bool:
    """True when the field is a mapping keyed by entity names."""

    return chapter_contract_field_semantic(path) == ChapterContractFieldSemantic.ENTITY_REF_MAP


def is_chapter_contract_fact_text_path(path: Sequence[str]) -> bool:
    """True when the field value is a narrative fact/proposition, not an entity ref."""

    return chapter_contract_field_semantic(path) == ChapterContractFieldSemantic.FACT_TEXT


def _normalize_path(path: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    for raw_part in path:
        part = str(raw_part or "").strip().lower()
        if not part or part in _WRAPPER_KEYS or part.isdigit():
            continue
        result.append(part)
    return tuple(result)


def _nearest_context(parts: Sequence[str]) -> str:
    for part in reversed(parts):
        if (
            part in _ENTITY_KEYS_BY_CONTEXT
            or part in _ENTITY_REF_MAP_KEYS
            or part in NEW_ENTITY_CANDIDATE_FIELDS
        ):
            return part
    return ""


def _is_explicit_entity_ref_key(key: str) -> bool:
    return _is_explicit_entity_id_key(key)


def _is_explicit_entity_id_key(key: str) -> bool:
    return key in {
        "entity_id",
        "entity_ids",
        "character_id",
        "character_ids",
        "item_id",
        "item_ids",
    } or key.endswith(("_entity_id", "_entity_ids", "_character_id", "_character_ids"))


__all__ = [
    "ChapterContractFieldSemantic",
    "KNOWLEDGE_OP_FACT_KEYS",
    "NEW_ENTITY_CANDIDATE_FIELDS",
    "chapter_contract_field_semantic",
    "is_chapter_contract_entity_id_path",
    "is_chapter_contract_entity_ref_map_path",
    "is_chapter_contract_entity_ref_path",
    "is_chapter_contract_fact_text_path",
]
