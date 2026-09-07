from __future__ import annotations

from pathlib import Path

import pytest

from novel_forge.core.utils.chapter_enumeration import (
    chapter_range,
    completed_chapter_numbers,
)
from novel_forge.persistence.models import ProjectLayout


@pytest.fixture()
def layout(tmp_path: Path) -> ProjectLayout:
    """Create a ProjectLayout with a chapters directory."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    return ProjectLayout(project_dir)


def _create_chapter_files(layout: ProjectLayout, numbers: list[int]) -> None:
    """Helper to create chapter_NNN.md files."""
    layout.chapters_dir.mkdir(parents=True, exist_ok=True)
    for num in numbers:
        (layout.chapters_dir / f"chapter_{num:03d}.md").write_text(f"# Chapter {num}")


class TestCompletedChapterNumbers:
    def test_empty_directory_returns_empty_list(self, layout: ProjectLayout) -> None:
        layout.chapters_dir.mkdir(parents=True, exist_ok=True)
        assert completed_chapter_numbers(layout) == []

    def test_single_chapter(self, layout: ProjectLayout) -> None:
        _create_chapter_files(layout, [1])
        assert completed_chapter_numbers(layout) == [1]

    def test_multiple_chapters_sorted(self, layout: ProjectLayout) -> None:
        _create_chapter_files(layout, [3, 1, 5, 2, 4])
        assert completed_chapter_numbers(layout) == [1, 2, 3, 4, 5]

    def test_non_contiguous_chapters(self, layout: ProjectLayout) -> None:
        _create_chapter_files(layout, [1, 3, 7, 12])
        assert completed_chapter_numbers(layout) == [1, 3, 7, 12]

    def test_ignores_non_chapter_files(self, layout: ProjectLayout) -> None:
        _create_chapter_files(layout, [1, 2])
        # Add files that should be ignored
        (layout.chapters_dir / "chapter_notes.txt").write_text("notes")
        (layout.chapters_dir / "readme.md").write_text("readme")
        (layout.chapters_dir / "chapter_abc.md").write_text("bad name")
        assert completed_chapter_numbers(layout) == [1, 2]

    def test_missing_chapters_dir_returns_empty(self, layout: ProjectLayout) -> None:
        # chapters_dir doesn't exist yet
        assert completed_chapter_numbers(layout) == []

    def test_large_chapter_numbers(self, layout: ProjectLayout) -> None:
        _create_chapter_files(layout, [100, 200, 999])
        assert completed_chapter_numbers(layout) == [100, 200, 999]


class TestChapterRange:
    def test_basic_range(self) -> None:
        result = chapter_range(1, 5)
        assert list(result) == [1, 2, 3, 4, 5]

    def test_single_chapter_range(self) -> None:
        result = chapter_range(3, 3)
        assert list(result) == [3]

    def test_range_is_inclusive(self) -> None:
        result = chapter_range(10, 15)
        assert 10 in result
        assert 15 in result
        assert list(result) == [10, 11, 12, 13, 14, 15]

    def test_returns_range_type(self) -> None:
        result = chapter_range(1, 10)
        assert isinstance(result, range)
