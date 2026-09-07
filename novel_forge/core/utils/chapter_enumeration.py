from __future__ import annotations

import re

from novel_forge.persistence.models import ProjectLayout

_CHAPTER_FILE_RE = re.compile(r"^chapter_(\d+)\.md$")


def completed_chapter_numbers(layout: ProjectLayout) -> list[int]:
    """Return sorted list of chapter numbers found in ``layout.chapters_dir``.

    Scans for files matching ``chapter_NNN.md`` and extracts the numeric part.
    Returns an empty list if the directory does not exist or contains no matches.
    """
    chapters_dir = layout.chapters_dir
    if not chapters_dir.is_dir():
        return []

    numbers: list[int] = []
    for path in chapters_dir.glob("chapter_*.md"):
        match = _CHAPTER_FILE_RE.match(path.name)
        if match:
            numbers.append(int(match.group(1)))

    return sorted(numbers)


def chapter_range(start: int, end: int) -> range:
    """Return an inclusive range of chapter numbers from *start* to *end*."""
    return range(start, end + 1)
