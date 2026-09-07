"""Story beats — structured plot outline."""

from __future__ import annotations

from pydantic import BaseModel, Field

from novel_forge.core.constants import BeatType
from novel_forge.core.schemas.base import VersionedSchema


class Beat(BaseModel):
    """A single story beat / scene unit.

    Intentionally uses BaseModel (not VersionedSchema) to avoid carrying
    schema_version / created_at on every beat — saves tokens when
    serialized into prompts.
    """

    model_config = {"extra": "ignore"}  # 容忍 LLM 返回多余字段

    sequence: int = Field(ge=1, description="1-based position in the beat list.")
    beat_type: BeatType = Field(
        default=BeatType.RISING,
        description="该节拍在故事结构中的角色：opening/rising/climax/falling/resolution。",
    )
    summary: str = Field(description="What happens in this beat (1–3 sentences).")
    tension_level: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Narrative tension on a 1-10 scale.",
    )
    characters_involved: list[str] = Field(
        default_factory=list,
        description="Names of characters present in this beat.",
    )
    setting: str = Field(default="", description="Location / setting.")
    emotional_note: str = Field(
        default="",
        description="该节拍的情绪基调或内心活动提示。",
    )
    notes: str = Field(default="", description="Author / system notes.")


class StoryBeats(VersionedSchema):
    """Ordered list of beats forming the story skeleton."""

    beats: list[Beat] = Field(min_length=1, description="At least one beat required.")
    total_estimated_words: int = Field(
        default=0,
        ge=0,
        description="Rough word-count estimate for the full draft.",
    )
    structure_note: str = Field(
        default="",
        description="整体结构说明，如'三幕式'、'环形结构'等。",
    )
