from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTextBrowser

from novel_forge.core.utils.text_revision_diff import build_text_revision_diff
from novel_forge.desktop.pages.document_renderer_story_artifacts import render_version_diff


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def test_build_text_revision_diff_returns_renderer_ready_payload() -> None:
    payload = build_text_revision_diff(
        "第一行\n旧句子",
        "第一行\n新句子",
        source="humanize_layer",
        chapter_number=3,
        status="accepted",
        patches_applied=1,
    )

    assert payload["artifact_type"] == "text_revision_diff"
    assert payload["source"] == "humanize_layer"
    assert payload["chapter_number"] == 3
    assert payload["patches_applied"] == 1
    assert payload["additions"] >= 1
    assert payload["deletions"] >= 1
    assert payload["hunks"]


def test_render_version_diff_shows_accepted_status(qapp: QApplication) -> None:
    payload = build_text_revision_diff(
        "第一行\n旧句子",
        "第一行\n新句子",
        source="humanize_layer",
        chapter_number=3,
        status="accepted",
        patches_applied=1,
    )

    widget = render_version_diff(payload)

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "已采纳" in text
    assert "补丁：1" in text
    assert "变更率" in text

    widget.deleteLater()
    qapp.processEvents()


def test_render_version_diff_shows_change_ratio_rejection(qapp: QApplication) -> None:
    payload = build_text_revision_diff(
        "第一行\n旧句子",
        "完全不同的候选稿",
        source="humanize_layer",
        chapter_number=3,
        status="rejected",
        reason="change_ratio_exceeded",
        patches_applied=2,
        metadata={"change_ratio_cap": 0.05},
    )

    widget = render_version_diff(payload)

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "已回滚" in text
    assert "变更率超过上限" in text
    assert "补丁：2" in text
    assert "上限：5.0%" in text

    widget.deleteLater()
    qapp.processEvents()


def test_render_version_diff_shows_semantic_drift_summary(qapp: QApplication) -> None:
    payload = build_text_revision_diff(
        "林远推开门。林远看见灯。林远停住。",
        "他推开门。他看见灯。他停住。",
        source="humanize_layer",
        chapter_number=3,
        status="rejected",
        reason="semantic_drift",
        patches_applied=3,
        metadata={
            "change_ratio_cap": 0.10,
            "drift": {
                "has_drift": True,
                "signal_count": 1,
                "high_severity": 1,
                "signals": [
                    {
                        "category": "pov",
                        "severity": "high",
                        "description": "POV角色从文中完全消失",
                    }
                ],
            },
        },
    )

    widget = render_version_diff(payload)

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "已回滚" in text
    assert "漂移保护触发" in text
    assert "漂移信号：1" in text
    assert "高风险：1" in text
    assert "POV角色从文中完全消失" in text

    widget.deleteLater()
    qapp.processEvents()
