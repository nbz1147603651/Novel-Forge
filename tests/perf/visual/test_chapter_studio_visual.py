"""Visual regression test for the Chapter Studio (章节工作室) page.

Renders ``ChapterStudioPage`` in offscreen mode with a synthetic
1-project / 5-chapter snapshot and compares the captured screenshot
against the baseline PNG in
``tests/perf/visual/baselines/chapter_studio.png``.

Run with::

    pytest -m visual tests/perf/visual/test_chapter_studio_visual.py
    pytest -m visual --update-visual tests/perf/visual/test_chapter_studio_visual.py
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from tests.perf.visual.conftest import (
    assert_matches_baseline,
    capture_page,
)

pytestmark = pytest.mark.visual


class TestChapterStudioVisual:
    """Snapshot test for ``ChapterStudioPage`` with 1 project and 5 chapters."""

    def test_chapter_studio_matches_baseline(
        self,
        qtbot: QtBot,
        tmp_path: Path,
        chapter_studio_snapshot,
        update_visual: bool,
    ) -> None:
        from novel_forge.desktop.pages.chapter_studio.page import ChapterStudioPage

        page = ChapterStudioPage()
        qtbot.addWidget(page)
        # Bind workspace; chapter-studio requires this before its project
        # combo and context are populated.
        page.bind_workspace(chapter_studio_snapshot)

        actual_path = tmp_path / "chapter_studio.png"
        capture_page(page, actual_path)

        assert_matches_baseline(
            name="chapter_studio",
            actual_path=actual_path,
            update=update_visual,
        )

        # Sanity guard: workspace snapshot must have been stored so that the
        # rendered UI is in a meaningful state.
        assert page._workspace is chapter_studio_snapshot

        baseline = Path(__file__).resolve().parent / "baselines" / "chapter_studio.png"
        assert baseline.exists(), f"Expected baseline at {baseline}"