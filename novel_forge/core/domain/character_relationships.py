"""Projection helpers for character-to-character relationships."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from novel_forge.core.domain.character_identity import clean_character_name, stable_character_id


@dataclass(frozen=True)
class CharacterRelationshipProjection:
    """A normalized character relationship projected from character profiles."""

    source_id: str
    target_id: str
    source_name: str
    target_name: str
    description: str
    source: str = "character_bible.relationships"

    @property
    def pair_key(self) -> tuple[str, str]:
        return tuple(sorted((self.source_name, self.target_name)))


def _profile_get(profile: Any, key: str, default: Any = None) -> Any:
    if isinstance(profile, dict):
        return profile.get(key, default)
    return getattr(profile, key, default)


def character_profile_identity_map(profiles: list[Any]) -> dict[str, str]:
    """Return canonical character name -> stable id for profile-like objects."""
    mapping: dict[str, str] = {}
    for profile in profiles:
        name = clean_character_name(_profile_get(profile, "name"))
        if not name:
            continue
        character_id = clean_character_name(_profile_get(profile, "character_id"))
        mapping[name] = character_id or stable_character_id(name)
    return mapping


def iter_character_relationship_projections(
    profiles: list[Any],
) -> list[CharacterRelationshipProjection]:
    """Project profile relationships to character-only relationship records."""
    name_to_id = character_profile_identity_map(profiles)
    projections: list[CharacterRelationshipProjection] = []
    seen: set[tuple[str, str, str]] = set()

    for profile in profiles:
        source_name = clean_character_name(_profile_get(profile, "name"))
        source_id = name_to_id.get(source_name)
        if not source_name or not source_id:
            continue
        relationships = _profile_get(profile, "relationships", {})
        if not isinstance(relationships, dict):
            continue
        for raw_target, raw_description in relationships.items():
            target_name = clean_character_name(raw_target)
            target_id = name_to_id.get(target_name)
            description = str(raw_description or "").strip()
            if not target_name or not target_id or target_name == source_name or not description:
                continue
            pair = tuple(sorted((source_name, target_name)))
            key = (pair[0], pair[1], description)
            if key in seen:
                continue
            seen.add(key)
            projections.append(
                CharacterRelationshipProjection(
                    source_id=source_id,
                    target_id=target_id,
                    source_name=source_name,
                    target_name=target_name,
                    description=description,
                )
            )
    return projections
