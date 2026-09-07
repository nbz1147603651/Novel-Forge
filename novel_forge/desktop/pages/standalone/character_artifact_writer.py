"""Compatibility forwarding for the former PySide character artifact writer.

The durable artifact workflow belongs to the Engine-facing application service.
PySide keeps this import path while it completes its transition to command-only
control; it owns presentation and local edit buffers, not persistence rules.
"""

from __future__ import annotations

from novel_forge.app_service.character_artifacts import (
    RELATIONSHIP_TYPE_OPTIONS,
    RELATIONSHIP_TYPES,
    CharacterArtifactWriteResult,
    add_character,
    clean_character_name,
    load_character_bible,
    remove_relationship_edge,
    retire_character,
    update_character_profile,
    write_character_bible,
    write_relationship_edge,
)

__all__ = [
    "RELATIONSHIP_TYPES",
    "RELATIONSHIP_TYPE_OPTIONS",
    "CharacterArtifactWriteResult",
    "add_character",
    "clean_character_name",
    "load_character_bible",
    "remove_relationship_edge",
    "retire_character",
    "update_character_profile",
    "write_character_bible",
    "write_relationship_edge",
]
