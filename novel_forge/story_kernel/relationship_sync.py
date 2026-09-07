"""One-way relationship sync from CharacterBible to StoryKernel."""

from __future__ import annotations

import logging

from novel_forge.core.domain.character_relationships import iter_character_relationship_projections
from novel_forge.core.schemas.bible import CharacterBible
from novel_forge.story_kernel.entity_projection import stable_entity_id
from novel_forge.story_kernel.schemas import Relationship, StoryKernel

_log = logging.getLogger(__name__)


def _make_pair_id(name_a: str, name_b: str) -> str:
    sorted_names = sorted([name_a, name_b])
    return f"{sorted_names[0]}__{sorted_names[1]}"


def _character_entity_id(name: str) -> str:
    """Generate a stable entity ID consistent with the rest of the system."""
    return stable_entity_id("character", name)


def sync_relationships_from_bible_to_kernel(
    bible: CharacterBible,
    kernel: StoryKernel,
) -> int:
    added = 0
    entity_names = {e.name: e.entity_id for e in kernel.entities}
    existing_pairs = set()
    for rel in kernel.relationships:
        pair_key = tuple(sorted([rel.source_entity_id, rel.target_entity_id]))
        existing_pairs.add(pair_key)

    for projection in iter_character_relationship_projections(bible.characters):
        src_id = entity_names.get(projection.source_name, _character_entity_id(projection.source_name))
        tgt_id = entity_names.get(projection.target_name, _character_entity_id(projection.target_name))
        pair_key = tuple(sorted([src_id, tgt_id]))
        if pair_key in existing_pairs:
            continue

        pair_id = _make_pair_id(projection.source_name, projection.target_name)
        kernel.relationships.append(
            Relationship(
                relationship_id=f"rel_{pair_id}",
                source_entity_id=src_id,
                target_entity_id=tgt_id,
                relation_type="acquaintance",
                label=projection.description[:200],
                trust=0.5,
                tension=0.5,
                status="active",
                notes="从角色档案同步",
            )
        )
        existing_pairs.add(pair_key)
        added += 1

    if added:
        _log.info(
            "Relationship sync (bible->kernel): added %d new pairs (total now %d)",
            added,
            len(kernel.relationships),
        )

    return added
