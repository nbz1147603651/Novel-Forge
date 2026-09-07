"""Visual regression test for the Projects (卷帙) page.

Renders ``ProjectsPage`` in offscreen mode with a synthetic 3-project
snapshot (no project selected, so the viewer shows the empty-hint state)
and compares the captured screenshot against the baseline PNG in
``tests/perf/visual/baselines/projects.png``.

Run with::

    pytest -m visual tests/perf/visual/test_projects_visual.py
    pytest -m visual --update-visual tests/perf/visual/test_projects_visual.py
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


class TestProjectsVisual:
    """Snapshot test for ``ProjectsPage`` with 3 projects in the combo."""

    def test_projects_matches_baseline(
        self,
        qtbot: QtBot,
        tmp_path: Path,
        projects_snapshot,
        update_visual: bool,
    ) -> None:
        from novel_forge.desktop.pages.standalone.projects_page import ProjectsPage

        page = ProjectsPage()
        qtbot.addWidget(page)
        # Don't load any project — the viewer should show the empty hint
        # while the combo is populated with 3 entries.
        page.bind_workspace(projects_snapshot)

        actual_path = tmp_path / "projects.png"
        capture_page(page, actual_path)

        assert_matches_baseline(
            name="projects",
            actual_path=actual_path,
            update=update_visual,
        )

        # Combo box must be populated so the rendered UI matches the snapshot
        # we're trying to capture deterministically.
        assert page._project_combo.count() == 3

        baseline = Path(__file__).resolve().parent / "baselines" / "projects.png"
        assert baseline.exists(), f"Expected baseline at {baseline}"