"""Visual regression test for the Dashboard (案头) page.

Renders ``DashboardPage`` in offscreen mode with a synthetic 5-project
snapshot and compares the captured screenshot against the baseline PNG in
``tests/perf/visual/baselines/dashboard.png``.

Run with::

    pytest -m visual tests/perf/visual/test_dashboard_visual.py
    pytest -m visual --update-visual tests/perf/visual/test_dashboard_visual.py
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


class TestDashboardVisual:
    """Snapshot test for ``DashboardPage``."""

    def test_dashboard_matches_baseline(
        self,
        qtbot: QtBot,
        tmp_path: Path,
        dashboard_snapshot,
        update_visual: bool,
    ) -> None:
        from novel_forge.desktop.pages.standalone.dashboard_page import DashboardPage

        page = DashboardPage()
        qtbot.addWidget(page)
        # ``bind_workspace`` populates metric cards, hero, status, project
        # grid and detail panel in one synchronous pass.
        page.bind_workspace(dashboard_snapshot)

        actual_path = tmp_path / "dashboard.png"
        capture_page(page, actual_path)

        assert_matches_baseline(
            name="dashboard",
            actual_path=actual_path,
            update=update_visual,
        )

        # Sanity guard: the baseline file must exist by the time the assertion
        # above is reached (whether via pytest.skip on first run or after a
        # successful re-run).  This guards against silent capture failures.
        baseline = Path(__file__).resolve().parent / "baselines" / "dashboard.png"
        assert baseline.exists(), f"Expected baseline at {baseline}"