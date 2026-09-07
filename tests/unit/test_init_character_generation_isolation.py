"""Regression tests for isolated character generation modes."""

from __future__ import annotations

from novel_forge.core.domain.character_boundary import canonical_character_names
from novel_forge.core.schemas.bible import CharacterBible
from novel_forge.pipeline.long.services.init.init_character_bible import (
    _relationship_matrix_items_for_roster,
)
from novel_forge.pipeline.long.services.init.init_service import (
    CHARACTER_GENERATION_MODE_SPLIT,
    _character_source_metadata_matches,
    _relationship_matrix_cache_matches,
)
from novel_forge.pipeline.long.services.init.init_v2 import (
    CHARACTER_RELATIONSHIP_MATRIX_PROMPT_VERSION,
)


def test_relationship_matrix_cache_rejects_other_character_generation_mode() -> None:
    payload = {
        "prompt_version": CHARACTER_RELATIONSHIP_MATRIX_PROMPT_VERSION,
        "generation_mode": CHARACTER_GENERATION_MODE_SPLIT,
        "story_bible_hash": "story-a",
        "character_bible_hash": "chars-a",
        "relationship_matrix": [{"character_a": "甲", "character_b": "乙"}],
    }

    assert not _relationship_matrix_cache_matches(
        payload,
        expected={
            "generation_mode": "unknown_v1",
            "story_bible_hash": "story-a",
            "character_bible_hash": "chars-a",
        },
    )


def test_relationship_matrix_cache_rejects_other_character_source_hash() -> None:
    payload = {
        "prompt_version": CHARACTER_RELATIONSHIP_MATRIX_PROMPT_VERSION,
        "generation_mode": CHARACTER_GENERATION_MODE_SPLIT,
        "story_bible_hash": "story-a",
        "character_bible_hash": "chars-old",
        "relationship_matrix": [{"character_a": "甲", "character_b": "乙"}],
    }

    assert not _relationship_matrix_cache_matches(
        payload,
        expected={
            "generation_mode": CHARACTER_GENERATION_MODE_SPLIT,
            "story_bible_hash": "story-a",
            "character_bible_hash": "chars-new",
        },
    )


def test_character_source_metadata_accepts_projected_hash_but_rejects_other_mode() -> None:
    metadata = {
        "generation_mode": CHARACTER_GENERATION_MODE_SPLIT,
        "source_character_bible_hash": "source-hash",
        "projected_character_bible_hash": "projected-hash",
    }

    assert _character_source_metadata_matches(
        metadata,
        generation_mode=CHARACTER_GENERATION_MODE_SPLIT,
        current_character_bible_hash="projected-hash",
        source_character_bible_hash="source-hash",
    )
    assert not _character_source_metadata_matches(
        metadata,
        generation_mode="unknown_v1",
        current_character_bible_hash="projected-hash",
        source_character_bible_hash="source-hash",
    )
    assert not _character_source_metadata_matches(
        metadata,
        generation_mode=CHARACTER_GENERATION_MODE_SPLIT,
        current_character_bible_hash="other-hash",
        source_character_bible_hash="source-hash",
    )


def test_relationship_matrix_boundary_rejects_unknown_names_before_downstream() -> None:
    accepted, rejected = _relationship_matrix_items_for_roster(
        {
            "relationship_matrix": [
                {"character_a": "顾琛", "character_b": "林晚", "description": "同盟"},
                {"character_a": "顾珩", "character_b": "林晚", "description": "错名"},
            ]
        },
        roster=[{"name": "顾琛"}, {"name": "林晚"}],
    )

    assert accepted == [
        {"character_a": "顾琛", "character_b": "林晚", "description": "同盟"}
    ]
    assert rejected[0]["reason"] == "character_name_not_in_canonical_roster"
    assert rejected[0]["invalid_character_names"] == ["顾珩"]


def test_outline_recovery_does_not_promote_relationship_keys_to_canonical_names() -> None:
    bible = CharacterBible.model_validate(
        {
            "characters": [
                {
                    "name": "顾琛",
                    "role": "protagonist",
                    "relationships": {"顾珩": "历史污染的错名关系"},
                }
            ]
        }
    )

    assert set(canonical_character_names(bible)) == {"顾琛"}
