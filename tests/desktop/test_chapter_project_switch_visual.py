"""PySide6 reference capture for the active-task project switch confirmation."""

from __future__ import annotations

import os

import pytest
from pytestqt.qtbot import QtBot

from tests.desktop.visual_regression import capture_widget_screenshot, save_baseline

pytestmark = pytest.mark.skipif(
    bool(os.environ.get("PYTEST_XDIST_WORKER")),
    reason="PySide visual baseline capture is xdist-unsafe; run this test serially.",
)


class TestChapterProjectSwitchVisualBaseline:
    """Freeze the confirmation over the same active-chapter fixture."""

    def test_project_switch_dialog_baseline_captured(
        self,
        qtbot: QtBot,
        update_baselines: bool,
    ) -> None:
        if not update_baselines:
            pytest.skip("Baseline capture updates PNGs; run this test serially with --update-baselines.")

        from PySide6.QtWidgets import QApplication

        from novel_forge.desktop.pages.chapter_studio.dialogs import ProjectSwitchConfirmDialog
        from novel_forge.desktop.theme import get_stylesheet

        app = QApplication.instance()
        old_stylesheet = app.styleSheet() if isinstance(app, QApplication) else ""
        if isinstance(app, QApplication):
            app.setStyleSheet(get_stylesheet())
        try:
            dialog = ProjectSwitchConfirmDialog("测试长篇", 5, "正在生成第 5 章")
            qtbot.addWidget(dialog)
            dialog.show()
            qtbot.wait(100)
            image = capture_widget_screenshot(dialog)
            path = save_baseline("chapter_project_switch_dialog", image)
            assert path.exists()
            assert image.width() == 480
            assert image.height() == 287
        finally:
            if isinstance(app, QApplication):
                app.setStyleSheet(old_stylesheet)
