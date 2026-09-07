"""Shared helpers for chapter-text Repair Orchestration v2 handlers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from novel_forge.pipeline.repair_orchestration.domains._shared import (
    restore_text_path,
    snapshot_text_path,
)

snapshot_path = snapshot_text_path
restore_path = restore_text_path


def snapshot_layout_paths(
    bundle: Any,
    chapter_number: int,
    path_names: tuple[str, ...],
) -> list[dict[str, Any]]:
    layout = getattr(bundle, "layout", None)
    if layout is None or chapter_number <= 0:
        return []
    paths: list[Path] = []
    for name in path_names:
        path_fn = getattr(layout, name, None)
        if callable(path_fn):
            paths.append(path_fn(chapter_number))
    return [snapshot_path(path) for path in paths]


def restore_snapshot_paths(snapshot_payload: dict[str, Any]) -> None:
    for item in snapshot_payload.get("paths") or []:
        if isinstance(item, dict):
            restore_path(item)
