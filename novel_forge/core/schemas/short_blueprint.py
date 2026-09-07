"""Short story narrative blueprint — lightweight structural plan."""

from __future__ import annotations

from pydantic import BaseModel, Field

from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.core.schemas.blueprint_elements import BlueprintElementSelection


class AnchorCharacter(BaseModel):
    """A pinned character with name and role."""
    model_config = {"extra": "ignore"}

    name: str = ""
    role: str = Field(default="", description="在故事中的定位，如主角/反派/配角")


class StoryAnchor(BaseModel):
    """全篇级叙事锚定要素 — 时间线、核心场景、主要人物、核心事件线。"""
    model_config = {"extra": "ignore"}

    time_frame: str = Field(default="", description="故事时间范围，如'一个雨夜'/'春节前一周'")
    primary_locations: list[str] = Field(default_factory=list, description="核心场景列表")
    core_characters: list[AnchorCharacter] = Field(default_factory=list)
    central_event: str = Field(default="", description="故事围绕的核心事件/行动")


class ShortNarrativePhase(BaseModel):
    model_config = {"extra": "ignore"}

    phase_name: str = ""
    position_start: int = Field(default=0, ge=0, le=100)
    position_end: int = Field(default=100, ge=0, le=100)
    description: str = ""
    tension_level: str = ""
    emotional_focus: str = ""
    # 锚定要素 — 防止各阶段时地人物事件漂移
    time_setting: str = Field(default="", description="本阶段的时间点/时段")
    location: str = Field(default="", description="本阶段的场景/地点")
    characters_present: list[str] = Field(default_factory=list, description="本阶段出场角色")
    key_event: str = Field(default="", description="本阶段的核心事件")


class ShortTurningPoint(BaseModel):
    model_config = {"extra": "ignore"}

    position_percent: int = Field(default=50, ge=0, le=100)
    description: str = ""
    impact: str = ""


class ShortCharacterArc(BaseModel):
    model_config = {"extra": "ignore"}

    character: str = ""
    arc_summary: str = ""
    key_moment: str = ""


class ShortBlueprint(VersionedSchema):
    """Lightweight narrative blueprint for short stories."""

    model_config = {"extra": "ignore"}

    synopsis: str = Field(default="", description="2-3句全篇概要")
    anchor_elements: StoryAnchor = Field(default_factory=StoryAnchor, description="全篇叙事锚定要素")
    narrative_phases: list[ShortNarrativePhase] = Field(default_factory=list)
    turning_points: list[ShortTurningPoint] = Field(default_factory=list)
    character_arcs: list[ShortCharacterArc] = Field(default_factory=list)
    emotional_arc: str = Field(default="", description="情感走势描述")
    ending_strategy: str = Field(default="", description="收束策略")
    element_selection: BlueprintElementSelection | None = Field(
        default=None,
        description="叙事要素选择器输出。",
    )


# ---------------------------------------------------------------------------
# Resolve forward references for cross-module type annotations.
# ---------------------------------------------------------------------------
ShortBlueprint.model_rebuild()
