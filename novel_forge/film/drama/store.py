"""Persistence for the short-drama workbench state.

The drama state lives next to the film studio state (``film/drama_state.json``)
but is an independent document: short drama shares the project shell with the
film workbench while owning its own production artifacts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from novel_forge.film.schemas import RunPlanNode, TimelineMarker
from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.persistence.models import ProjectLayout

from .schemas import DramaSeriesPlan, EpisodeOutline, EpisodeScreenplay


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class DramaProjectState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    project_id: str
    title: str = ""
    language: str = "zh"
    series_plan: DramaSeriesPlan | None = None
    outlines: list[EpisodeOutline] = Field(default_factory=list)
    screenplays: list[EpisodeScreenplay] = Field(default_factory=list)
    run_plan: list[RunPlanNode] = Field(default_factory=list)
    timeline_markers: list[TimelineMarker] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now_iso)

    def outline_for(self, episode_number: int) -> EpisodeOutline | None:
        return next((o for o in self.outlines if o.episode_number == episode_number), None)

    def screenplay_for(self, episode_number: int) -> EpisodeScreenplay | None:
        return next(
            (s for s in self.screenplays if s.episode_number == episode_number), None
        )


class DramaProjectStore:
    def __init__(self, layout: ProjectLayout) -> None:
        self.layout = layout

    @property
    def drama_dir(self) -> Path:
        return self.layout.root / "film" / "drama"

    @property
    def state_path(self) -> Path:
        return self.drama_dir / "drama_state.json"

    @property
    def export_dir(self) -> Path:
        return self.drama_dir / "export"

    def load(self) -> DramaProjectState | None:
        if not self.state_path.exists():
            return None
        try:
            return DramaProjectState.model_validate_json(
                self.state_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return None

    def save(self, state: DramaProjectState) -> DramaProjectState:
        updated = state.model_copy(update={"updated_at": utc_now_iso()})
        atomic_write_json(self.state_path, updated.model_dump(mode="json"))
        return updated


__all__ = ["DramaProjectState", "DramaProjectStore", "utc_now_iso"]
