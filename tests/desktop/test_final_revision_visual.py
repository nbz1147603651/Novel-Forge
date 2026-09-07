"""PySide6 reference capture for the final-draft save-review dialog.

This is a Phase 1 visual-source fixture only: it does not alter the writing
pipeline or revision persistence. The React/Tauri reader uses the same fixed
chapter text and target word count in its Chromium regression case.
"""

from __future__ import annotations

import os

import pytest
from pytestqt.qtbot import QtBot

from tests.desktop.visual_regression import capture_widget_screenshot, save_baseline

pytestmark = pytest.mark.skipif(
    bool(os.environ.get("PYTEST_XDIST_WORKER")),
    reason="PySide visual baseline capture is xdist-unsafe; run this test serially.",
)


class TestFinalRevisionVisualBaseline:
    """Freeze the source 840×560 save-preview state for cross-client QA."""

    def test_save_preview_baseline_captured(
        self,
        qtbot: QtBot,
        update_baselines: bool,
    ) -> None:
        if not update_baselines:
            pytest.skip("Baseline capture updates PNGs; run with --update-baselines.")

        from PySide6.QtWidgets import QApplication

        from novel_forge.desktop.pages.standalone.final_revision import (
            _build_revision_guard_report,
            _revision_unified_diff,
            _RevisionSavePreviewDialog,
        )
        from novel_forge.desktop.theme import get_stylesheet

        before = (
            "雨声压在高架桥底，像一盘没倒回去的磁带。林逐把读卡器贴上玻璃，"
            "屏幕里跳出的时间戳比姐姐失踪的那天晚了七分钟。\n\n"
            "她没有立刻拨给周砚，只把那串数字抄进纸质本。纸页吸了潮气，边缘卷起，"
            "像有人在这座城里替她保留了一次迟到的呼吸。"
        )
        after = before.replace("她没有", "她并无")
        report = _build_revision_guard_report(
            after,
            previous_text=before,
            expected_word_count=4216,
        )

        app = QApplication.instance()
        old_stylesheet = app.styleSheet() if isinstance(app, QApplication) else ""
        if isinstance(app, QApplication):
            app.setStyleSheet(get_stylesheet())
        try:
            dialog = _RevisionSavePreviewDialog(
                report=report,
                diff_text=_revision_unified_diff(before, after),
            )
            qtbot.addWidget(dialog)
            dialog.resize(840, 560)
            dialog.show()
            qtbot.wait(100)

            image = capture_widget_screenshot(dialog)
            baseline_path = save_baseline("final_revision_save_preview", image)
            assert baseline_path.exists()
            assert image.width() == 840
            assert image.height() == 560
        finally:
            if isinstance(app, QApplication):
                app.setStyleSheet(old_stylesheet)
