"""Chapter-related helper functions for CLI commands.

Note: Plot Guard related functions have been moved to plot_guard.py.
This module now contains only general chapter helper functions.
"""

from __future__ import annotations

import json

from novel_forge.core.schemas.outline import StoryOutline
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout


def _detect_next_chapter(layout: ProjectLayout) -> int:
    """Infer the next chapter number from canon/chapter files."""
    latest_from_chapters = 0
    if layout.chapters_dir.exists():
        for chapter_file in layout.chapters_dir.glob("chapter_*.md"):
            try:
                chapter_num = int(chapter_file.stem.split("_")[1])
            except (IndexError, ValueError):
                continue
            latest_from_chapters = max(latest_from_chapters, chapter_num)

    latest_from_canon = 0
    canon_current_path = layout.canon_dir / "canon_current.json"
    if canon_current_path.exists():
        try:
            canon_data = json.loads(canon_current_path.read_text(encoding="utf-8"))
            latest_from_canon = int(canon_data.get("current_chapter", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            latest_from_canon = 0

    return max(0, latest_from_chapters, latest_from_canon) + 1


def _detect_latest_creative_report_chapter(layout: ProjectLayout) -> int | None:
    """Find the latest chapter number that has a creative report."""
    latest: int | None = None
    if not layout.reports_dir.exists():
        return None
    for report_file in layout.reports_dir.glob("chapter_*_creative.json"):
        parts = report_file.stem.split("_")
        if len(parts) < 3:
            continue
        try:
            chapter_num = int(parts[1])
        except ValueError:
            continue
        if latest is None or chapter_num > latest:
            latest = chapter_num
    return latest


def _load_outline_total_chapters(layout: ProjectLayout, storage: FileSystemStorage) -> int:
    """Load outline.json and return total chapters."""
    outline_path = layout.outline_path
    if not outline_path.exists():
        raise FileNotFoundError(f"outline.json not found at {outline_path}")
    outline_data = storage.load_json(outline_path)
    outline = StoryOutline.model_validate(outline_data)
    return outline.total_chapters
