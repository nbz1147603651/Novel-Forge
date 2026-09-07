"""Enumerations for StoryKernel field types."""

from __future__ import annotations

from enum import Enum


class EntityType(str, Enum):
    """Canonical entity types tracked in the unified field pool."""

    CHARACTER = "character"
    LOCATION = "location"
    ITEM = "item"
    ORGANIZATION = "organization"
    CONCEPT = "concept"


class RelationType(str, Enum):
    """Canonical relationship types between entities."""

    FAMILY = "family"
    ROMANTIC = "romantic"
    MENTOR_STUDENT = "mentor_student"
    BUSINESS = "business"
    ALLY = "ally"
    ENEMY = "enemy"
    RIVAL = "rival"
    SUBORDINATE = "subordinate"
    FRIEND = "friend"
    ACQUAINTANCE = "acquaintance"


class LedgerVisibility(str, Enum):
    """Visibility levels for ledger entries."""

    PUBLIC = "public"
    PRIVATE = "private"
    SECRET = "secret"


__all__ = [
    "EntityType",
    "LedgerVisibility",
    "RelationType",
]
