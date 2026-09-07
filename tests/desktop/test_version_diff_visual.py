"""PySide6 reference captures for the chapter version-diff flow.

The fixture is intentionally limited to source UI rendering.  It does not
read project drafts or invoke any chapter workflow, and mirrors the Phase 1
React fixture's two visible draft versions.
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


class TestVersionDiffVisualBaseline:
    """Freeze the selector and its resulting source document renderer."""

    def test_selector_and_result_baselines_captured(
        self,
        qtbot: QtBot,
        update_baselines: bool,
    ) -> None:
        if not update_baselines:
            pytest.skip("Baseline capture updates PNGs; run with --update-baselines.")

        from PySide6.QtWidgets import QApplication

        from novel_forge.core.utils.version_diff import DraftVersionInfo, compute_diff
        from novel_forge.desktop.pages.chapter_studio.dialogs import VersionDiffDialog
        from novel_forge.desktop.pages.document_renderers import render_version_diff
        from novel_forge.desktop.theme import get_stylesheet

        before = (
            "雨声压在高架桥底，像一盘没倒回去的磁带。林逐把读卡器贴上玻璃，"
            "屏幕里跳出的时间戳比姐姐失踪的那天晚了七分钟。\n\n"
            "她没有立刻拨给周砚，只把那串数字抄进纸质本。纸页吸了潮气，边缘卷起，"
            "像有人在这座城里替她保留了一次迟到的呼吸。"
        )
        after = before.replace("她没有", "她并无")
        versions = [
            DraftVersionInfo(1, "初始草稿", "v1.md", word_count=3840),
            DraftVersionInfo(2, "第 1 轮编辑", "v2.md", word_count=4216),
        ]
        result = compute_diff(
            before,
            after,
            label_a=versions[0].label,
            label_b=versions[1].label,
            version_a=versions[0].version,
            version_b=versions[1].version,
        )
        diff_data = {
            "additions": result.total_additions,
            "deletions": result.total_deletions,
            "similarity_ratio": result.similarity_ratio,
            "unified_diff": result.unified_diff,
            "hunks": [
                {
                    "tag": "replace",
                    "a_text": "\n".join(line[1:] for line in hunk.lines if line.startswith("-")),
                    "b_text": "\n".join(line[1:] for line in hunk.lines if line.startswith("+")),
                }
                for hunk in result.hunks
            ],
        }

        app = QApplication.instance()
        old_stylesheet = app.styleSheet() if isinstance(app, QApplication) else ""
        if isinstance(app, QApplication):
            app.setStyleSheet(get_stylesheet())
        try:
            selector = VersionDiffDialog(versions, chapter_num=4)
            qtbot.addWidget(selector)
            selector.show()
            qtbot.wait(100)
            selector_image = capture_widget_screenshot(selector)
            selector_path = save_baseline("version_diff_dialog", selector_image)
            assert selector_path.exists()
            assert selector_image.width() == 480

            artifact = render_version_diff(diff_data, label_a=versions[0].label, label_b=versions[1].label)
            artifact.resize(760, 520)
            qtbot.addWidget(artifact)
            artifact.show()
            qtbot.wait(100)
            artifact_image = capture_widget_screenshot(artifact)
            artifact_path = save_baseline("version_diff_artifact", artifact_image)
            assert artifact_path.exists()
            assert artifact_image.width() == 760
            assert artifact_image.height() == 520
        finally:
            if isinstance(app, QApplication):
                app.setStyleSheet(old_stylesheet)
