"""Volume-level audit and bridge schemas."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from novel_forge.core.schemas.base import VersionedSchema


class VolumeMilestoneStatus(VersionedSchema):
    """Status of a planned milestone at volume end."""

    milestone: str = Field(default="")
    status: str = Field(default="partial", description="done/partial/missed")
    evidence: str = Field(default="")


class VolumeAuditReport(VersionedSchema):
    """Volume-end summary, consistency audit, and carry-over plan."""

    volume_number: int = Field(ge=1)
    volume_title: str = Field(default="")
    chapter_range: str = Field(default="", description="e.g. 1-20")
    volume_summary: str = Field(default="")
    milestone_status: list[VolumeMilestoneStatus] = Field(default_factory=list)
    consistency_score: float = Field(default=0.0, ge=0.0, le=10.0)
    consistency_issues: list[dict[str, Any]] = Field(default_factory=list)
    carry_over_characters: list[str] = Field(default_factory=list)
    retire_characters: list[str] = Field(default_factory=list)
    carry_over_items: list[str] = Field(default_factory=list)
    retire_items: list[str] = Field(default_factory=list)
    carry_over_world_fact_keys: list[str] = Field(default_factory=list)
    retire_world_fact_keys: list[str] = Field(default_factory=list)
    carry_over_foreshadowing_ids: list[str] = Field(default_factory=list)
    resolved_foreshadowing_ids: list[str] = Field(default_factory=list)
    next_volume_focus: str = Field(default="")
    token_optimization_notes: list[str] = Field(default_factory=list)
