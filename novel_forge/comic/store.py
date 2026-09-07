"""Persistence for the comic workbench state.

The comic state lives in ``comic/comic_state.json`` under the project root:
comic is an independent downstream medium and never writes back into the
film studio or novel canon.
"""

from __future__ import annotations

from pathlib import Path

from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.persistence.models import ProjectLayout

from .schemas import ComicProjectState, utc_now_iso


class ComicProjectStore:
    def __init__(self, layout: ProjectLayout) -> None:
        self.layout = layout

    @property
    def comic_dir(self) -> Path:
        return self.layout.root / "comic"

    @property
    def state_path(self) -> Path:
        return self.comic_dir / "comic_state.json"

    @property
    def export_dir(self) -> Path:
        return self.comic_dir / "export"

    def load(self) -> ComicProjectState | None:
        if not self.state_path.exists():
            return None
        try:
            return ComicProjectState.model_validate_json(
                self.state_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return None

    def save(self, state: ComicProjectState) -> ComicProjectState:
        updated = state.model_copy(update={"updated_at": utc_now_iso()})
        atomic_write_json(self.state_path, updated.model_dump(mode="json"))
        return updated


__all__ = ["ComicProjectStore"]
