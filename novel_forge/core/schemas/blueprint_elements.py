"""Schemas for narrative blueprint element library selection."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.core.utils.type_coerce import stringify_text_value


class BlueprintElementCard(BaseModel):
    """One selectable narrative element with prompt/UI metadata."""

    model_config = {"extra": "ignore"}

    element_id: str = Field(default="", description="Stable element identifier.")
    name: str = Field(default="", description="Display name.")
    category: str = Field(default="", description="Element category.")
    tier: Literal["required", "extension"] = "extension"
    description: str = Field(default="", description="What this element controls.")
    rationale: str = Field(default="", description="Why the element matters.")
    recommended_genres: list[str] = Field(
        default_factory=list,
        description="Genres where the element is usually helpful.",
    )
    prompt_hint: str = Field(
        default="",
        description="Injection hint used by prompt templates.",
    )
    implementation_guide: str = Field(
        default="",
        description="Concrete prose-level execution guidance for this element.",
    )
    verification_anchors: list[str] = Field(
        default_factory=list,
        description="Observable prose or structure features used to verify execution.",
    )
    verification_mode: Literal["anchor", "structural", "llm"] = Field(
        default="anchor",
        description="Preferred downstream verification strategy.",
    )
    intensity_hint: str = Field(
        default="",
        description="How strongly the element should be expressed when focused.",
    )
    requires: list[str] = Field(
        default_factory=list,
        description="Element ids that should be selected with this element.",
    )
    excludes: list[str] = Field(
        default_factory=list,
        description="Element ids that should not be selected with this element.",
    )
    complements: list[str] = Field(
        default_factory=list,
        description="Element ids that work especially well with this element.",
    )
    phase_affinity: list[str] = Field(
        default_factory=list,
        description="Preferred narrative phases for this element.",
    )
    ui_hint: str = Field(
        default="checklist",
        description="Preferred UI rendering style.",
    )
    selection_reason: str = Field(
        default="",
        description="Why selector picked this element for current story.",
    )
    library_source: str = Field(
        default="builtin",
        description="Element library source: builtin or external.",
    )
    selection_source: str = Field(
        default="",
        description="How this element entered the selected set: llm, heuristic, preset, user.",
    )
    selection_score: float = Field(
        default=0.0,
        ge=0.0,
        description="Normalized selector priority score for downstream ordering/debugging.",
    )
    user_weight: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Original UI preference weight when supplied.",
    )
    user_locked: bool = Field(
        default=False,
        description="Whether the user locked this element during selection.",
    )
    user_enabled: bool | None = Field(
        default=None,
        description="User include/exclude toggle when supplied.",
    )

    @field_validator(
        "element_id",
        "name",
        "description",
        "rationale",
        "prompt_hint",
        "implementation_guide",
        "verification_mode",
        "intensity_hint",
        "ui_hint",
        "selection_reason",
        mode="before",
    )
    @classmethod
    def _coerce_blueprint_text_fields(cls, v: Any) -> str:
        return stringify_text_value(v)


class BlueprintElementPreferenceItem(BaseModel):
    """User preference for one extension element."""

    model_config = {"extra": "ignore"}

    element_id: str = Field(default="", description="Stable extension element identifier.")
    enabled: bool | None = Field(
        default=None,
        description="Manual include/exclude toggle. None means no explicit toggle.",
    )
    locked: bool = Field(
        default=False,
        description="When true, enabled state overrides auto selector decisions.",
    )
    weight: float = Field(
        default=50.0,
        ge=0.0,
        le=100.0,
        description="UI weight slider (0-100), 50 as neutral baseline.",
    )


class BlueprintElementPreferenceConfig(BaseModel):
    """User-configurable selector preferences from workflow forms."""

    model_config = {"extra": "ignore"}

    preset_id: str = Field(default="", description="Applied genre preset identifier.")
    manual_override: bool = Field(
        default=False,
        description="If true, manual enabled toggles override auto selection.",
    )
    items: list[BlueprintElementPreferenceItem] = Field(default_factory=list)


class BlueprintElementSelection(VersionedSchema):
    """Selector output used for prompt injection and UI rendering."""

    model_config = {"extra": "ignore"}

    library_version: str = Field(default="2026.05")
    mode: Literal["short", "long"] = "long"
    selector_summary: str = Field(default="", description="Short selection summary.")
    genre_inference: list[str] = Field(
        default_factory=list,
        description="Selector inferred subgenres/tags.",
    )
    focus_constraints: list[str] = Field(
        default_factory=list,
        description="Execution constraints inferred from current project.",
    )
    required_elements: list[BlueprintElementCard] = Field(default_factory=list)
    extension_elements: list[BlueprintElementCard] = Field(default_factory=list)
    quality_config_elements: list[BlueprintElementCard] = Field(
        default_factory=list,
        description="质量评估配置型要素（不注入叙事约束提示词，由质量评估模块单独消费）。",
    )
