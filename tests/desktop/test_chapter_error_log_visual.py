"""PySide6 reference capture for the Chapter Studio task error log."""

from __future__ import annotations

import os

import pytest
from pytestqt.qtbot import QtBot

from tests.desktop.visual_regression import capture_widget_screenshot, save_baseline

pytestmark = pytest.mark.skipif(
    bool(os.environ.get("PYTEST_XDIST_WORKER")),
    reason="PySide visual baseline capture is xdist-unsafe; run this test serially.",
)


class TestChapterErrorLogVisualBaseline:
    """Freeze the chapter-local log using the same entry as the React fixture."""

    def test_chapter_error_log_dialog_baseline_captured(
        self,
        qtbot: QtBot,
        update_baselines: bool,
    ) -> None:
        if not update_baselines:
            pytest.skip("Baseline capture updates PNGs; run this test serially with --update-baselines.")

        from PySide6.QtWidgets import QApplication

        from novel_forge.desktop.pages.chapter_studio.jobs import TaskFlowErrorLogDialog
        from novel_forge.desktop.theme import get_stylesheet

        app = QApplication.instance()
        old_stylesheet = app.styleSheet() if isinstance(app, QApplication) else ""
        if isinstance(app, QApplication):
            app.setStyleSheet(get_stylesheet())
        try:
            dialog = TaskFlowErrorLogDialog(
                [
                    {
                        "id": "fixture-chapter-causal-error",
                        "time": "2026-07-14 14:08:31",
                        "job": "第 5 章 · 档案室",
                        "task": "VALIDATE_CAUSAL",
                        "task_label": "因果验证",
                        "attempt": "1/2",
                        "error": "因果验证等待上游计划检查点",
                        "excerpt": "章节计划尚未确认，因果校验被安全地延后；现有草稿与故事状态均未被修改。",
                        "log_file": "logs/fixture/chapter_005/format_errors/causal.json",
                        "kind": "等待确认",
                    }
                ]
            )
            qtbot.addWidget(dialog)
            dialog.show()
            qtbot.wait(100)
            image = capture_widget_screenshot(dialog)
            path = save_baseline("chapter_error_log_dialog", image)
            assert path.exists()
            assert image.width() == 760
            assert image.height() == 520
        finally:
            if isinstance(app, QApplication):
                app.setStyleSheet(old_stylesheet)
