"""Request-scoped chapter artifact I/O context.

Extracted from workspace/chapter_run_io.py so that pipeline can
import this type without a reverse dependency on workspace.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from novel_forge.pipeline.long.artifact_cache import (
    ArtifactLoadContext,
    ChapterArtifactBundleLoader,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(frozen=True)
class ChapterRunIOContext:
    """Unified request-scoped artifact loader for one chapter operation.

    The context intentionally caches structured artifacts only. Chapter prose is
    passed explicitly by callers so semantic edits cannot be hidden by a stale
    text cache.
    """

    storage: Any
    layout: Any
    project_id: str
    chapter_number: int
    source: str = "chapter_run"
    artifact_loader: ChapterArtifactBundleLoader | None = None

    def __post_init__(self) -> None:
        if self.artifact_loader is not None:
            return
        object.__setattr__(
            self,
            "artifact_loader",
            ChapterArtifactBundleLoader(
                self.storage,
                context=ArtifactLoadContext(
                    source=self.source,
                    project_id=self.project_id,
                    chapter_number=self.chapter_number,
                ),
            ),
        )

    def load_json(self, path: Path) -> dict[str, Any]:
        return self._loader.load_json(path)

    def load_optional_json(self, path: Path) -> dict[str, Any] | None:
        return self._loader.load_optional_json(path)

    def load_model(self, path: Path, model: type[ModelT]) -> ModelT:
        return self._loader.load_model(path, model)

    def load_optional_model(self, path: Path, model: type[ModelT]) -> ModelT | None:
        return self._loader.load_optional_model(path, model)

    def stats(self) -> dict[str, int]:
        return self._loader.stats()

    @property
    def _loader(self) -> ChapterArtifactBundleLoader:
        loader = self.artifact_loader
        if loader is None:
            raise RuntimeError("ChapterRunIOContext artifact_loader was not initialized")
        return loader


__all__ = ["ChapterRunIOContext"]
