"""StoryKernel projections for prompts and lightweight context payloads."""

from __future__ import annotations

from typing import Any

from novel_forge.narrative_state.schemas import EntityRecord, EntityRegistry, stable_id
from novel_forge.story_kernel.schemas import Entity, StoryKernel

_REGISTRY_PREFIX_BY_TYPE = {
    "character": "char",
    "location": "loc",
    "item": "item",
    "organization": "org",
    "concept": "concept",
}


def entity_registry_from_kernel(kernel: StoryKernel) -> EntityRegistry:
    """Build the adjudication prompt registry from StoryKernel entities."""
    records: list[EntityRecord] = []
    for entity in kernel.entities:
        entity_type = _entity_type_value(entity)
        # Adjudication prompts currently support the five canonical entity
        # types below; new entity types must opt in through the prefix map.
        if entity_type not in _REGISTRY_PREFIX_BY_TYPE:
            continue
        records.append(
            EntityRecord(
                entity_id=entity.entity_id or stable_entity_id(entity_type, entity.name),
                name=entity.name,
                entity_type=entity_type,
                aliases=list(entity.aliases),
                source="story_kernel_projection",
                notes=entity.notes,
            )
        )
    return EntityRegistry(entities=records)


def known_character_names_from_kernel(kernel: StoryKernel) -> list[str]:
    """Return character display names currently known to the kernel."""
    return [
        entity.name
        for entity in kernel.entities
        if _entity_type_value(entity) == "character" and entity.name
    ]


def entity_lookup_from_kernel(kernel: StoryKernel) -> dict[str, Entity]:
    """Return lookup entries by id, name, and aliases."""
    lookup: dict[str, Entity] = {}
    for entity in kernel.entities:
        keys = [entity.entity_id, entity.name, *entity.aliases]
        for key in keys:
            text = str(key or "").strip()
            if text:
                lookup[text] = entity
    return lookup


def short_context_projection_from_kernel(kernel: StoryKernel) -> dict[str, Any]:
    """Build a full short-story context projection without long-form recency trimming."""
    return {
        "project_id": kernel.project_id,
        "project_mode": kernel.project_mode,
        "title": kernel.title,
        "premise": kernel.premise,
        "entities": [entity.model_dump(mode="json") for entity in kernel.entities],
        "timeline": [anchor.model_dump(mode="json") for anchor in kernel.timeline],
        "promise_ledger": [
            promise.model_dump(mode="json") for promise in kernel.promise_ledger
        ],
        "chapter_summaries": dict(kernel.chapter_summaries),
        "artifact_refs": dict(kernel.artifact_refs),
        "structured_warnings": [
            warning.model_dump(mode="json") for warning in kernel.structured_warnings
        ],
        "notes": kernel.notes,
    }


def stable_entity_id(entity_type: str, name: str) -> str:
    """Generate a deterministic EntityRecord-compatible id for an entity."""
    prefix = _REGISTRY_PREFIX_BY_TYPE.get(str(entity_type or "").strip(), "ent")
    return stable_id(prefix, name)


def dedupe_entities(
    entities: list[Entity],
    *,
    prefer_existing_attributes: bool = True,
) -> list[Entity]:
    """Dedupe entities by id first, then by entity_type + name."""
    by_id: dict[str, Entity] = {}
    by_type_name: dict[tuple[str, str], str] = {}
    ordered_ids: list[str] = []

    for entity in entities:
        entity_id = entity.entity_id
        entity_type = _entity_type_value(entity)
        type_name_key = (entity_type, entity.name)
        existing_id = by_type_name.get(type_name_key)
        target_id = existing_id or entity_id

        if target_id in by_id:
            existing = by_id[target_id]
            if prefer_existing_attributes:
                attributes = {**entity.attributes, **existing.attributes}
            else:
                attributes = {**existing.attributes, **entity.attributes}
            by_id[target_id] = existing.model_copy(
                update={
                    "aliases": list(dict.fromkeys([*existing.aliases, *entity.aliases])),
                    "attributes": attributes,
                    "notes": existing.notes or entity.notes,
                }
            )
            continue

        by_id[entity_id] = entity
        by_type_name[type_name_key] = entity_id
        ordered_ids.append(entity_id)

    return [by_id[entity_id] for entity_id in ordered_ids if entity_id in by_id]


def _entity_type_value(entity: Entity) -> str:
    return str(getattr(entity.entity_type, "value", entity.entity_type) or "").strip()


__all__ = [
    "entity_lookup_from_kernel",
    "entity_registry_from_kernel",
    "dedupe_entities",
    "known_character_names_from_kernel",
    "short_context_projection_from_kernel",
    "stable_entity_id",
]
