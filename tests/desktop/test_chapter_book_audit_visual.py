"""PySide6 reference capture for the default book-audit dialog."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from pytestqt.qtbot import QtBot

from tests.desktop.visual_regression import capture_widget_screenshot, save_baseline

pytestmark = pytest.mark.skipif(
    bool(os.environ.get("PYTEST_XDIST_WORKER")),
    reason="PySide visual baseline capture is xdist-unsafe; run this test serially.",
)


class _FixedSettings:
    """Keep user-local Qt settings from changing the frozen default view."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        return

    def value(self, _key: str, default: object = None, **_kwargs: object) -> object:
        return default


class TestChapterBookAuditVisualBaseline:
    """Freeze the simple-mode initial state at its native Qt size."""

    def test_book_audit_dialog_baseline_captured(
        self,
        qtbot: QtBot,
        update_baselines: bool,
    ) -> None:
        if not update_baselines:
            pytest.skip("Baseline capture updates PNGs; run with --update-baselines.")

        from PySide6.QtWidgets import QApplication

        from novel_forge.desktop.pages.chapter_studio import dialogs as dialog_module
        from novel_forge.desktop.theme import get_stylesheet

        app = QApplication.instance()
        old_stylesheet = app.styleSheet() if isinstance(app, QApplication) else ""
        if isinstance(app, QApplication):
            app.setStyleSheet(get_stylesheet())
        try:
            with patch.object(dialog_module, "QSettings", _FixedSettings):
                dialog = dialog_module.BookAuditDialog(
                    [1, 2, 3, 4],
                    has_prior_audit=True,
                    prior_audit_status="上次审计已完成",
                )
                qtbot.addWidget(dialog)
                dialog.show()
                qtbot.wait(100)
                image = capture_widget_screenshot(dialog)
                path = save_baseline("chapter_book_audit_dialog", image)
                assert path.exists()
                assert image.width() == 640
                assert image.height() == 533
        finally:
            if isinstance(app, QApplication):
                app.setStyleSheet(old_stylesheet)
