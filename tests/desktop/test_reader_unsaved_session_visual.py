"""PySide6 reference capture for source-shaped reader unsaved-change prompts."""

from __future__ import annotations

import os

import pytest
from pytestqt.qtbot import QtBot

from tests.desktop.visual_regression import capture_widget_screenshot, save_baseline

pytestmark = pytest.mark.skipif(
    bool(os.environ.get("PYTEST_XDIST_WORKER")),
    reason="PySide visual baseline capture is xdist-unsafe; run this test serially.",
)


class TestReaderUnsavedSessionVisualBaseline:
    """Freeze the shared source message box used by reader close guards."""

    def test_final_revision_unsaved_dialog_baseline_captured(
        self,
        qtbot: QtBot,
        update_baselines: bool,
    ) -> None:
        if not update_baselines:
            pytest.skip("Baseline capture updates PNGs; run this test serially with --update-baselines.")

        from PySide6.QtWidgets import QApplication, QMessageBox

        from novel_forge.desktop.components.dialogs import (
            MessageBoxAction,
            _build_message_box_dialog,
        )
        from novel_forge.desktop.theme import get_stylesheet

        app = QApplication.instance()
        old_stylesheet = app.styleSheet() if isinstance(app, QApplication) else ""
        if isinstance(app, QApplication):
            app.setStyleSheet(get_stylesheet())
        try:
            dialog = _build_message_box_dialog(
                None,
                "终稿修订尚未保存",
                "当前终稿有未保存修改。",
                informative_text="可以先保存定稿，也可以放弃本次修订。",
                icon=QMessageBox.Icon.Question,
                actions=(
                    MessageBoxAction("save", "保存定稿", QMessageBox.ButtonRole.AcceptRole, "primary", True),
                    MessageBoxAction("discard", "放弃修改", QMessageBox.ButtonRole.DestructiveRole, "danger"),
                    MessageBoxAction("cancel", "继续修订", QMessageBox.ButtonRole.RejectRole, "secondary"),
                ),
                escape_key="cancel",
            )
            qtbot.addWidget(dialog)
            dialog.show()
            qtbot.wait(100)
            image = capture_widget_screenshot(dialog)
            path = save_baseline("reader_unsaved_final_revision_dialog", image)
            assert path.exists()
        finally:
            if isinstance(app, QApplication):
                app.setStyleSheet(old_stylesheet)
