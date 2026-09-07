"""Long-form pipeline orchestration package."""

from __future__ import annotations

from typing import Any

__all__ = ["run_long_chapter"]


def __getattr__(name: str) -> Any:
    if name == "run_long_chapter":
        from novel_forge.pipeline.long.loop import run_long_chapter

        return run_long_chapter
    raise AttributeError(name)
