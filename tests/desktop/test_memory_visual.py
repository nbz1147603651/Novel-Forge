"""Visual regression baseline capture for UnifiedMemoryPanel.

Captures ``tests/desktop/baselines/memory.png`` so future UI polish work
can detect regressions in the memory panel surface.  Runs under
``QT_QPA_PLATFORM=offscreen`` (set in ``pyproject.toml``).

Usage:
    pytest tests/desktop/test_memory_visual.py -v
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from tests.desktop.visual_regression import (
    VISUAL_STORAGE_ROOT,
    capture_widget_screenshot,
    save_baseline,
)

pytestmark = pytest.mark.skipif(
    bool(os.environ.get("PYTEST_XDIST_WORKER")),
    reason="PySide visual baseline capture is xdist-unsafe; run this test serially.",
)


@pytest.fixture
def visual_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Deterministic QSS setup for the memory panel snapshot."""
    root = VISUAL_STORAGE_ROOT
    root.mkdir(parents=True, exist_ok=True)

    from PySide6.QtWidgets import QApplication

    from novel_forge.desktop.theme import get_stylesheet

    app = QApplication.instance()
    old_stylesheet = app.styleSheet() if isinstance(app, QApplication) else ""
    if isinstance(app, QApplication):
        app.setStyleSheet(get_stylesheet())
    try:
        yield root
    finally:
        if isinstance(app, QApplication):
            app.setStyleSheet(old_stylesheet)


class TestMemoryVisualBaseline:
    """UnifiedMemoryPanel visual regression baseline (Task 18)."""

    def test_memory_baseline_captured(
        self,
        qtbot: QtBot,
        visual_workspace: Path,
        update_baselines: bool,
    ) -> None:
        if not update_baselines:
            pytest.skip("Baseline capture updates PNGs; run with --update-baselines.")

        from PySide6.QtCore import QCoreApplication

        from novel_forge.desktop.components.memory_components import UnifiedMemoryPanel

        page = UnifiedMemoryPanel()
        qtbot.addWidget(page)
        page.resize(1200, 800)
        page.show()
        QCoreApplication.processEvents()
        QCoreApplication.processEvents()

        image = capture_widget_screenshot(page)
        baseline_path = save_baseline("memory", image)
        assert baseline_path.exists(), f"Baseline not created at {baseline_path}"
        assert baseline_path.stat().st_size > 0, "Baseline PNG is empty"
