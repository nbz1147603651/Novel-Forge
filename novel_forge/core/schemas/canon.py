"""Canon (正史) pipeline artifact schemas for Long Mode."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.core.schemas.story_state import (
    CharacterStateDelta,
    PlotThreadDelta,
    RelationshipStateDelta,
)
from novel_forge.core.utils.type_coerce import stringify_text_value

__all__ = [
    "CreativeReport",
    "NewCharacterDetail",
    "PlotDeviation",
]


class NewCharacterDetail(VersionedSchema):
    """Detailed new-character metadata for creative reports."""

    name: str
    canonical_name: str = Field(default="")
    first_appearance_chapter: int = Field(ge=1)
    role_in_story: str = Field(default="supporting")
    importance: str = Field(default="minor")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    description: str = Field(default="")
    relationship_to_existing: dict[str, str] = Field(default_factory=dict)
    should_add_to_bible: bool = Field(default=False)
    matched_existing_name: str = Field(default="")
    evidence: list[str] = Field(default_factory=list)

    @field_validator("description", mode="before")
    @classmethod
    def _coerce_new_char_description(cls, v: Any) -> str:
        return stringify_text_value(v)

    @field_validator("relationship_to_existing", mode="before")
    @classmethod
    def _coerce_new_char_relationships(cls, v: Any) -> dict[str, str]:
        if isinstance(v, dict):
            return {k: stringify_text_value(val) for k, val in v.items()}
        return {}


class PlotDeviation(VersionedSchema):
    """Difference between intended outline and actual chapter direction."""

    outline_plan: str = Field(description="Planned outline direction.")
    actual_plot: str = Field(description="Actual chapter direction.")
    deviation_level: str = Field(default="minor")
    reason: str = Field(default="")
    impact_on_future: str = Field(default="")

    @field_validator("actual_plot", mode="before")
    @classmethod
    def _coerce_actual_plot(cls, v: Any) -> str:
        return stringify_text_value(v)

    @field_validator("reason", mode="before")
    @classmethod
    def _coerce_reason(cls, v: Any) -> str:
        return stringify_text_value(v)


class CreativeReport(VersionedSchema):
    """Author-facing structured report that does not directly mutate canon."""

    new_characters: list[NewCharacterDetail] = Field(default_factory=list)
    new_locations: list[str] = Field(default_factory=list)
    new_key_items: list[str] = Field(default_factory=list)
    plot_deviations: list[PlotDeviation] = Field(default_factory=list)
    suggestions_for_next_chapter: str = Field(default="")
    creative_highlights: list[str] = Field(default_factory=list)
    structured_summary: str = Field(default="")
    must_carry_forward: list[str] = Field(default_factory=list)
    bridge_hints: list[str] = Field(default_factory=list)
    character_state_deltas: list[CharacterStateDelta] = Field(default_factory=list)
    relationship_deltas: list[RelationshipStateDelta] = Field(default_factory=list)
    plot_thread_updates: list[PlotThreadDelta] = Field(default_factory=list)
    # Additional fields for episodic memory indexing
    key_revelations: list[str] = Field(default_factory=list)
    key_moments: list[str] = Field(default_factory=list)

    @field_validator("structured_summary", mode="before")
    @classmethod
    def _coerce_creative_summary(cls, v: Any) -> str:
        return stringify_text_value(v)


# ---------------------------------------------------------------------------
# Resolve forward references for cross-module type annotations.
# ---------------------------------------------------------------------------
CreativeReport.model_rebuild()
