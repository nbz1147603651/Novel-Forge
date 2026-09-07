"""V2 initialization schemas.

These schemas describe the canonical internal artifacts for long-project
initialization.  The public chapter-generation inputs remain compact projected
files, while V2 keeps richer structure for audit, resume, and UI inspection.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator

from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.core.utils.type_coerce import stringify_text_value
from novel_forge.narrative_state.schemas import EntityRecord

CharacterRole = Literal[
    "protagonist",
    "antagonist",
    "deuteragonist",
    "supporting",
    "minor",
]
AuditSeverity = Literal["info", "warning", "error"]
InitBlockStatus = Literal["succeeded", "failed"]


class CharacterRosterEntry(VersionedSchema):
    """Stable roster entry used before richer character profiles are assembled."""

    model_config = ConfigDict(extra="ignore")

    character_id: str
    name: str
    role: str = "supporting"
    time_layer: str = "default"
    priority: int = Field(default=50, ge=0, le=100)
    source: str = "character_bible"

    @field_validator("character_id", "name", "role", "time_layer", "source", mode="before")
    @classmethod
    def _clean_text(cls, value: Any) -> str:
        return str(value or "").strip()


class RelationshipEdge(VersionedSchema):
    """Typed character-to-character relationship edge."""

    model_config = ConfigDict(extra="ignore")

    source_id: str
    target_id: str
    source_name: str
    target_name: str
    relation_type: str = "relationship"
    description: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: str = "character_bible"

    @field_validator("description", mode="before")
    @classmethod
    def _stringify_description(cls, value: Any) -> str:
        return stringify_text_value(value)


class EntityLink(VersionedSchema):
    """Typed non-social entity reference link."""

    model_config = ConfigDict(extra="ignore")

    source_id: str
    target_id: str
    source_name: str = ""
    target_name: str = ""
    link_type: str = "related_to"
    time_layer: str = ""
    description: str = ""
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    source: str = "init_v2"

    @field_validator("description", mode="before")
    @classmethod
    def _stringify_description(cls, value: Any) -> str:
        return stringify_text_value(value)


class CharacterAuditIssue(VersionedSchema):
    """One deterministic character-system audit finding."""

    model_config = ConfigDict(extra="ignore")

    severity: AuditSeverity = "warning"
    code: str
    message: str
    character_name: str = ""
    target_name: str = ""
    suggested_action: str = ""


class CharacterSystem(VersionedSchema):
    """Internal character system with clean relationship and identity layers."""

    model_config = ConfigDict(extra="ignore")

    roster: list[CharacterRosterEntry] = Field(default_factory=list)
    profiles: list[dict[str, Any]] = Field(default_factory=list)
    relationship_edges: list[RelationshipEdge] = Field(default_factory=list)
    identity_links: list[EntityLink] = Field(default_factory=list)
    audit: list[CharacterAuditIssue] = Field(default_factory=list)

    def name_to_id(self) -> dict[str, str]:
        return {item.name: item.character_id for item in self.roster if item.name}


class EntityGraph(VersionedSchema):
    """Entity registry plus typed links for aliases, identity, items and concepts."""

    model_config = ConfigDict(extra="ignore")

    entities: list[EntityRecord] = Field(default_factory=list)
    entity_links: list[EntityLink] = Field(default_factory=list)
    audit: list[CharacterAuditIssue] = Field(default_factory=list)

    def by_id(self) -> dict[str, EntityRecord]:
        return {item.entity_id: item for item in self.entities if item.entity_id}


class CreativeDirectorPacket(VersionedSchema):
    """Compact creative guidance derived from init artifacts."""

    model_config = ConfigDict(extra="ignore")

    emotional_engine: list[str] = Field(default_factory=list)
    thematic_promises: list[str] = Field(default_factory=list)
    signature_motifs: list[str] = Field(default_factory=list)
    relationship_tensions: list[str] = Field(default_factory=list)
    anti_cliche_rules: list[str] = Field(default_factory=list)
    scene_potential: list[str] = Field(default_factory=list)
    notes: str = ""

    @field_validator("notes", mode="before")
    @classmethod
    def _stringify_notes(cls, value: Any) -> str:
        return stringify_text_value(value)


class CreativeDirectionCandidate(VersionedSchema):
    """One bounded concept direction that must explicitly preserve user intent."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    packet: CreativeDirectorPacket
    preserved_intent_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    hard_constraint_risks: list[str] = Field(default_factory=list)
    intent_conflicts: list[str] = Field(default_factory=list)


class CreativeDirectionCandidateBatch(VersionedSchema):
    """Response envelope for one concept-candidate generation call."""

    model_config = ConfigDict(extra="forbid")

    candidates: list[CreativeDirectionCandidate] = Field(min_length=1, max_length=2)


class CreativeDirectionDecision(VersionedSchema):
    """Independent low-temperature selection result for a candidate batch."""

    model_config = ConfigDict(extra="forbid")

    selected_candidate_id: str
    diversity_score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    need_third_candidate: bool = False
    selection_reason: str


class BlueprintFragments(VersionedSchema):
    """Fragment cache used to assemble the final NarrativeBlueprint."""

    model_config = ConfigDict(extra="ignore")

    synopsis: str = ""
    volume_mode: bool = False
    volumes: list[dict[str, Any]] = Field(default_factory=list)
    narrative_phases: list[dict[str, Any]] = Field(default_factory=list)
    key_turning_points: list[dict[str, Any]] = Field(default_factory=list)
    character_arcs: list[dict[str, Any]] = Field(default_factory=list)
    subplot_plan: list[dict[str, Any]] = Field(default_factory=list)
    suspense_schedule: list[dict[str, Any]] = Field(default_factory=list)
    ending_strategy: str = ""
    emotional_arcs: list[dict[str, Any]] = Field(default_factory=list)
    causal_chains: list[dict[str, Any]] = Field(default_factory=list)
    subplot_collisions: list[dict[str, Any]] = Field(default_factory=list)
    subversion_points: list[dict[str, Any]] = Field(default_factory=list)
    assembly_mode: str = "fragmented_plan_outline"

    @field_validator("synopsis", "ending_strategy", mode="before")
    @classmethod
    def _stringify_text(cls, value: Any) -> str:
        return stringify_text_value(value)


class InitV2BlockRecord(VersionedSchema):
    """Persisted cache record for one V2 init block."""

    model_config = ConfigDict(extra="ignore")

    block_key: str
    request_fingerprint: str
    template_version: str = ""  # NEW: stores template version at save time for drift detection; "" for legacy records
    upstream_hashes: dict[str, str] = Field(default_factory=dict)
    status: InitBlockStatus
    payload: dict[str, Any] = Field(default_factory=dict)
    retry_count: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Resolve forward references for cross-module type annotations.
# ---------------------------------------------------------------------------
EntityGraph.model_rebuild()
