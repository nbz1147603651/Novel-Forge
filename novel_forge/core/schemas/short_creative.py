"""Short story creative analysis — characters, narrative, themes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.core.utils.type_coerce import stringify_text_value


class CharacterRelationship(BaseModel):
    model_config = {"extra": "ignore"}

    target: str = ""
    type: str = ""
    note: str = ""


class CharacterAnalysis(BaseModel):
    model_config = {"extra": "ignore"}

    name: str = ""
    role: str = ""
    arc_summary: str = ""
    key_traits: list[str] = Field(default_factory=list)
    relationships: list[CharacterRelationship] = Field(default_factory=list)

    @field_validator("arc_summary", mode="before")
    @classmethod
    def _coerce_character_arc_summary(cls, v: Any) -> str:
        return stringify_text_value(v)

    @field_validator("key_traits", mode="before")
    @classmethod
    def _coerce_character_key_traits(cls, v: Any) -> list[str]:
        if isinstance(v, list):
            return [stringify_text_value(item) for item in v]
        if isinstance(v, str):
            return [v]
        return []


class TurningPoint(BaseModel):
    model_config = {"extra": "ignore"}

    description: str = ""
    effectiveness: str = ""


class NarrativeAnalysis(BaseModel):
    model_config = {"extra": "ignore"}

    pacing_assessment: str = ""
    tension_curve: str = ""
    tension_curve_note: str = ""
    structure_type: str = ""
    turning_points: list[TurningPoint] = Field(default_factory=list)
    opening_hook: str = ""
    ending_impact: str = ""

    @field_validator(
        "pacing_assessment",
        "tension_curve",
        "tension_curve_note",
        "structure_type",
        "opening_hook",
        "ending_impact",
        mode="before",
    )
    @classmethod
    def _coerce_narrative_text_fields(cls, v: Any) -> str:
        return stringify_text_value(v)


class ThematicAnalysis(BaseModel):
    model_config = {"extra": "ignore"}

    core_theme: str = ""
    theme_delivery: str = ""
    symbolic_elements: list[str] = Field(default_factory=list)
    emotional_resonance: str = ""

    @field_validator("core_theme", "theme_delivery", "emotional_resonance", mode="before")
    @classmethod
    def _coerce_thematic_text_fields(cls, v: Any) -> str:
        return stringify_text_value(v)


class BeatFulfillment(BaseModel):
    model_config = {"extra": "ignore"}

    sequence: int = 0
    fulfilled: bool = False
    note: str = ""


class ShortCreativeSummary(VersionedSchema):
    """Creative analysis result for a short story."""

    characters: list[CharacterAnalysis] = Field(default_factory=list)
    narrative_analysis: NarrativeAnalysis = Field(default_factory=NarrativeAnalysis)
    thematic_analysis: ThematicAnalysis = Field(default_factory=ThematicAnalysis)
    creative_highlights: list[str] = Field(default_factory=list)
    improvement_suggestions: list[str] = Field(default_factory=list)
    beat_fulfillment: list[BeatFulfillment] = Field(default_factory=list)
