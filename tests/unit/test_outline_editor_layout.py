"""Layout regressions for the interactive outline editor."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QSizePolicy  # noqa: E402

from novel_forge.desktop.pages.standalone.outline_editor import (  # noqa: E402
    InteractiveOutlineWidget,
    _polish_focus_fields_for_text,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def test_outline_mode_tabs_center_label_content(
    qapp: QApplication,
    tmp_path,
) -> None:
    outline_data = {
        "total_chapters": 1,
        "synopsis": "测试大纲",
        "chapters": [
            {
                "chapter_number": 1,
                "title": "起章",
                "goal": "验证布局",
                "beats_summary": ["打开卷帙"],
            }
        ],
    }
    widget = InteractiveOutlineWidget(outline_data, tmp_path / "outline.json")
    qapp.processEvents()

    try:
        for button in (widget._read_btn, widget._polish_btn):
            style = button.styleSheet()
            assert button.property("outlineModeTab") == "true"
            assert "padding: 3px 9px;" in style
            assert "background: rgba(237, 226, 211, 0.46);" in style
            assert "border-bottom: none;" in style
            assert "text-align: center;" in style
            assert button.minimumHeight() == 26
            assert button.maximumHeight() == 26
            assert button.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Fixed
        assert widget._toolbar.minimumHeight() == 40
        assert widget._toolbar.maximumHeight() == 40
        assert widget._hint_input.parentWidget() is widget._polish_controls
    finally:
        widget.deleteLater()
        qapp.processEvents()


def test_configured_outline_editor_preserves_pending_draft_without_direct_publish(tmp_path):
    from novel_forge.core.authoring import AuthoringPolicy
    from novel_forge.persistence.authoring_store import AuthoringStore
    from novel_forge.persistence.filesystem import atomic_write_json

    path = tmp_path / "outline.json"
    atomic_write_json(path, {"author": "original"})
    original = path.read_bytes()
    AuthoringStore(tmp_path).set_policy(AuthoringPolicy(), expected_version=0)
    status = []
    widget = SimpleNamespace(
        _outline_path=path,
        _status_label=SimpleNamespace(setText=status.append),
        _pending_outline_data={"candidate": "保留作者的待确认方案"},
    )
    assert InteractiveOutlineWidget._save_outline(widget) is False
    assert widget._pending_outline_data
    assert path.read_bytes() == original
    assert "未写正式大纲" in status[-1]


def test_outline_polish_requires_selected_chapter_before_direct_execute(
    qapp: QApplication,
    tmp_path,
) -> None:
    outline_data = {
        "total_chapters": 1,
        "synopsis": "测试大纲",
        "chapters": [
            {
                "chapter_number": 1,
                "title": "起章",
                "goal": "验证直接润色",
                "beats_summary": ["打开卷帙"],
            }
        ],
    }
    widget = InteractiveOutlineWidget(outline_data, tmp_path / "outline.json")
    qapp.processEvents()

    try:
        widget._hint_input.setText("重命名第 1 章")
        widget._set_mode("polish")
        qapp.processEvents()

        assert not widget._left_panel.isVisible()
        assert not widget._exec_btn.isEnabled()
        assert "请先在阅读页勾选" in widget._exec_btn.toolTip()

        widget._set_mode("read")
        item = widget._chapter_list.item(0)
        item.setCheckState(Qt.CheckState.Checked)
        widget._set_mode("polish")
        qapp.processEvents()

        assert not widget._left_panel.isVisible()
        assert widget._exec_btn.isEnabled()
        assert "直接按润色方向执行" in widget._exec_btn.toolTip()
    finally:
        widget.deleteLater()
        qapp.processEvents()


def test_outline_chapter_track_selection_is_themed_and_row_toggleable(
    qapp: QApplication,
    tmp_path,
) -> None:
    outline_data = {
        "total_chapters": 1,
        "synopsis": "测试大纲",
        "chapters": [
            {
                "chapter_number": 1,
                "title": "起章",
                "goal": "验证章节轨道选择",
                "beats_summary": ["打开卷帙"],
            }
        ],
    }
    widget = InteractiveOutlineWidget(outline_data, tmp_path / "outline.json")
    qapp.processEvents()

    try:
        assert widget._chapter_list.objectName() == "outlineChapterList"
        style = widget._chapter_list.styleSheet()
        assert "QListWidget#outlineChapterList::indicator" in style
        assert "background: #fffaf3;" in style
        assert "background: #b65634;" in style

        item = widget._chapter_list.item(0)
        assert item.checkState() == Qt.CheckState.Unchecked
        widget._toggle_chapter_item_check_state(item)
        assert item.checkState() == Qt.CheckState.Checked
        widget._toggle_chapter_item_check_state(item)
        assert item.checkState() == Qt.CheckState.Unchecked
    finally:
        widget.deleteLater()
        qapp.processEvents()


def test_outline_polish_mode_restored_from_session(
    qapp: QApplication,
    tmp_path,
) -> None:
    outline_path = tmp_path / "outline.json"
    outline_data = {
        "total_chapters": 1,
        "synopsis": "测试大纲",
        "chapters": [
            {
                "chapter_number": 1,
                "title": "起章",
                "goal": "验证模式恢复",
                "beats_summary": ["打开卷帙"],
            }
        ],
    }
    outline_path.write_text(json.dumps(outline_data, ensure_ascii=False), encoding="utf-8")

    first = InteractiveOutlineWidget(dict(outline_data), outline_path)
    qapp.processEvents()
    try:
        first._set_mode("polish")
        qapp.processEvents()
    finally:
        first.deleteLater()
        qapp.processEvents()

    second = InteractiveOutlineWidget(dict(outline_data), outline_path)
    qapp.processEvents()
    try:
        assert second._mode == "polish"
        assert not second._polish_panel.isHidden()
        assert second._read_browser.isHidden()
    finally:
        second.deleteLater()
        qapp.processEvents()


def test_outline_polish_done_waits_for_apply_before_mutating_outline(
    qapp: QApplication,
    tmp_path,
) -> None:
    outline_path = tmp_path / "outline.json"
    outline_data = {
        "total_chapters": 1,
        "synopsis": "测试大纲",
        "chapters": [
            {
                "chapter_number": 1,
                "title": "旧章",
                "goal": "旧目标",
                "beats_summary": ["旧节奏"],
            }
        ],
    }
    outline_path.write_text(json.dumps(outline_data, ensure_ascii=False), encoding="utf-8")

    class AdjustedOutline:
        chapters = [
            SimpleNamespace(
                chapter_number=1,
                title="新章",
                goal="新目标",
                beats_summary=["新节奏"],
                main_plot_points=[],
                subplot_points=[],
                subplot_focus="",
                element_focus=[],
                pov_character="",
                setting="",
                expected_hook="",
                expected_payoffs=[],
                involved_characters=[],
                notes="",
            )
        ]

        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {
                **outline_data,
                "chapters": [
                    {
                        "chapter_number": 1,
                        "title": "新章",
                        "goal": "新目标",
                        "beats_summary": ["新节奏"],
                    }
                ],
            }

    widget = InteractiveOutlineWidget(dict(outline_data), outline_path)
    qapp.processEvents()

    try:
        item = widget._chapter_list.item(0)
        item.setCheckState(Qt.CheckState.Checked)
        widget._set_mode("polish")
        widget._original_chapters = {1: dict(outline_data["chapters"][0])}
        result = SimpleNamespace(adjusted_outline=AdjustedOutline(), changed_chapters=[1])

        widget._on_polish_done(result)
        qapp.processEvents()

        assert widget._mode == "polish"
        assert widget._outline_data["chapters"][0]["title"] == "旧章"
        assert widget.has_unsaved_changes() is True
        assert widget._save_btn.isEnabled()
        assert widget._discard_btn.isEnabled()
        saved_before_apply = json.loads(outline_path.read_text(encoding="utf-8"))
        assert saved_before_apply["chapters"][0]["title"] == "旧章"

        assert widget._save_outline()
        saved_after_apply = json.loads(outline_path.read_text(encoding="utf-8"))
        assert saved_after_apply["chapters"][0]["title"] == "新章"
        assert widget._outline_data["chapters"][0]["title"] == "新章"
        assert widget._mode == "polish"
        assert not widget._polish_panel.isHidden()
        assert widget._read_browser.isHidden()
        assert widget.has_unsaved_changes() is False
        assert not widget._save_btn.isEnabled()
        assert not widget._discard_btn.isEnabled()
    finally:
        widget.deleteLater()
        qapp.processEvents()


def test_outline_sync_result_is_visible_in_polish_panel(
    qapp: QApplication,
    tmp_path,
) -> None:
    outline_data = {
        "total_chapters": 1,
        "synopsis": "测试大纲",
        "chapters": [
            {
                "chapter_number": 1,
                "title": "起章",
                "goal": "验证同步反馈",
                "beats_summary": ["打开卷帙"],
            }
        ],
    }
    widget = InteractiveOutlineWidget(outline_data, tmp_path / "outline.json")
    qapp.processEvents()

    try:
        widget._set_mode("polish")
        widget._sync_btn.setText("同步中...")
        widget._sync_btn.setEnabled(False)
        widget._on_sync_job_finished(
            status="succeeded",
            result={
                "status": "completed",
                "affected": [1],
                "cascade": [2],
                "focus": [1, 2],
                "refreshed": 2,
                "stale_marked": 3,
                "milestones_rebuilt": 5,
                "duration_s": 1.2,
            },
        )

        assert widget._sync_btn.isEnabled()
        assert widget._sync_btn.text() == "同步契约"
        assert "同步契约完成" in widget._status_label.text()
        assert widget._preview_state_label.text() == "同步成功"
        assert "刷新契约：2 章" in widget._diff_preview.toPlainText()
        assert "软过期产物：3 个" in widget._diff_preview.toPlainText()
    finally:
        widget.deleteLater()
        qapp.processEvents()


def test_outline_polish_focus_fields_detect_title_requests() -> None:
    assert _polish_focus_fields_for_text("请调整这几章的标题") == ["title"]


def test_outline_polish_focus_fields_uses_core_fields_by_default() -> None:
    assert _polish_focus_fields_for_text("加强冲突层次") == [
        "goal",
        "beats_summary",
        "main_plot_points",
    ]
