"""StoryKernel ORM models — unified field pool for narrative state.

Defines 10 SQLAlchemy 2.0 tables that replace the fragmented canon + narrative_state
storage with a single relational source of truth.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)
from sqlalchemy.types import JSON

# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Shared declarative base for all StoryKernel ORM models."""

    pass


# ---------------------------------------------------------------------------
# 1. WorldRule — immutable world-building axioms
# ---------------------------------------------------------------------------


class WorldRule(Base):
    """Established world rules (magic systems, physics, social norms)."""

    __tablename__ = "world_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_text: Mapped[str] = mapped_column(Text, nullable=False)
    rule_type: Mapped[str] = mapped_column(String(64), nullable=False, default="general")
    created_chapter: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    def __repr__(self) -> str:
        return f"<WorldRule id={self.id} type={self.rule_type!r}>"


# ---------------------------------------------------------------------------
# 2. Entity — canonical entity registry (characters, locations, items, etc.)
# ---------------------------------------------------------------------------


class Entity(Base):
    """A canonical entity known to the story: character, location, item, etc."""

    __tablename__ = "entities"
    __table_args__ = (
        Index("ix_entities_source", "source"),
        Index("ix_entities_name", "name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    entity_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="unknown",
    )
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    source: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Reverse relationships
    timeline_anchors: Mapped[list[TimelineAnchor]] = relationship(
        "TimelineAnchor",
        back_populates="entity",
        cascade="all, delete-orphan",
    )
    knowledge_entries: Mapped[list[KnowledgeLedger]] = relationship(
        "KnowledgeLedger",
        back_populates="entity",
        cascade="all, delete-orphan",
    )
    access_entries: Mapped[list[AccessLedger]] = relationship(
        "AccessLedger",
        back_populates="entity",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Entity id={self.id} name={self.name!r} type={self.entity_type!r}>"


# ---------------------------------------------------------------------------
# 3. Relationship — directional relationship between two entities
# ---------------------------------------------------------------------------


class Relationship(Base):
    """A directional relationship between two entities."""

    __tablename__ = "relationships"
    __table_args__ = (
        Index("ix_relationships_source_id", "source_id"),
        Index("ix_relationships_target_id", "target_id"),
        Index("ix_relationships_source", "source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("entities.id"),
        nullable=False,
    )
    target_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("entities.id"),
        nullable=False,
    )
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False, default="generic")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    source: Mapped[str] = mapped_column(String(256), nullable=False, default="")

    def __repr__(self) -> str:
        return (
            f"<Relationship id={self.id} "
            f"{self.source_id}->{self.target_id} type={self.relation_type!r}>"
        )


# ---------------------------------------------------------------------------
# 4. TimelineAnchor — event anchored to an entity and in-story time
# ---------------------------------------------------------------------------


class TimelineAnchor(Base):
    """An event anchored to a specific entity and in-story time reference."""

    __tablename__ = "timeline_anchors"
    __table_args__ = (
        Index("ix_timeline_anchors_entity_id", "entity_id"),
        Index("ix_timeline_anchors_source", "source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("entities.id"),
        nullable=True,
    )
    event_text: Mapped[str] = mapped_column(Text, nullable=False)
    anchor_date: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    anchor_type: Mapped[str] = mapped_column(String(64), nullable=False, default="event")
    source: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    entity: Mapped[Entity | None] = relationship("Entity", back_populates="timeline_anchors")

    def __repr__(self) -> str:
        return f"<TimelineAnchor id={self.id} entity_id={self.entity_id}>"


# ---------------------------------------------------------------------------
# 5. ObjectLedger — tracking physical objects / props
# ---------------------------------------------------------------------------


class ObjectLedger(Base):
    """Tracks physical objects: origin, ownership, visibility."""

    __tablename__ = "object_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    object_name: Mapped[str] = mapped_column(String(256), nullable=False)
    origin: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    current_owner: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    visibility: Mapped[str] = mapped_column(String(64), nullable=False, default="public")
    chapter_acquired: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    def __repr__(self) -> str:
        return f"<ObjectLedger id={self.id} name={self.object_name!r}>"


# ---------------------------------------------------------------------------
# 6. KnowledgeLedger — what an entity knows (facts, secrets)
# ---------------------------------------------------------------------------


class KnowledgeLedger(Base):
    """Tracks facts known by an entity, including secrets."""

    __tablename__ = "knowledge_ledger"
    __table_args__ = (
        Index("ix_knowledge_ledger_entity_id", "entity_id"),
        Index("ix_knowledge_ledger_source", "source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("entities.id"),
        nullable=False,
    )
    fact_text: Mapped[str] = mapped_column(Text, nullable=False)
    learned_chapter: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    entity: Mapped[Entity] = relationship("Entity", back_populates="knowledge_entries")

    def __repr__(self) -> str:
        return f"<KnowledgeLedger id={self.id} entity_id={self.entity_id} secret={self.is_secret}>"


# ---------------------------------------------------------------------------
# 7. AccessLedger — location access grants
# ---------------------------------------------------------------------------


class AccessLedger(Base):
    """Tracks which entity has access to which location and why."""

    __tablename__ = "access_ledger"
    __table_args__ = (
        Index("ix_access_ledger_entity_id", "entity_id"),
        Index("ix_access_ledger_source", "source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("entities.id"),
        nullable=False,
    )
    location: Mapped[str] = mapped_column(String(256), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    chapter_granted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    entity: Mapped[Entity] = relationship("Entity", back_populates="access_entries")

    def __repr__(self) -> str:
        return f"<AccessLedger id={self.id} entity_id={self.entity_id} location={self.location!r}>"


# ---------------------------------------------------------------------------
# 8. PromiseLedger — planted / fulfilled foreshadowing promises
# ---------------------------------------------------------------------------


class PromiseLedger(Base):
    """Tracks narrative promises: foreshadowing planted and when fulfilled."""

    __tablename__ = "promise_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    promise_text: Mapped[str] = mapped_column(Text, nullable=False)
    planted_chapter: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expected_window: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    fulfilled_chapter: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    source: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    def __repr__(self) -> str:
        return f"<PromiseLedger id={self.id} fulfilled={self.fulfilled_chapter}>"


# ---------------------------------------------------------------------------
# 9. MotifProtocol — recurring motif rules and trigger patterns
# ---------------------------------------------------------------------------


class MotifProtocol(Base):
    """Defines a recurring motif: trigger pattern and callback rules."""

    __tablename__ = "motif_protocols"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    motif_name: Mapped[str] = mapped_column(String(256), nullable=False)
    trigger_pattern: Mapped[str] = mapped_column(Text, nullable=False)
    callback_rules: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    source: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    def __repr__(self) -> str:
        return f"<MotifProtocol id={self.id} name={self.motif_name!r}>"


# ---------------------------------------------------------------------------
# 10. BusinessDependency — dependency between two business entities
# ---------------------------------------------------------------------------


class BusinessDependency(Base):
    """Tracks a dependency relationship between two named entities."""

    __tablename__ = "business_dependencies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_a: Mapped[str] = mapped_column(String(256), nullable=False)
    entity_b: Mapped[str] = mapped_column(String(256), nullable=False)
    dependency_type: Mapped[str] = mapped_column(String(64), nullable=False, default="generic")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    def __repr__(self) -> str:
        return (
            f"<BusinessDependency id={self.id} "
            f"{self.entity_a!r}->{self.entity_b!r} type={self.dependency_type!r}>"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "AccessLedger",
    "Base",
    "BusinessDependency",
    "Entity",
    "KnowledgeLedger",
    "MotifProtocol",
    "ObjectLedger",
    "PromiseLedger",
    "Relationship",
    "TimelineAnchor",
    "WorldRule",
]
