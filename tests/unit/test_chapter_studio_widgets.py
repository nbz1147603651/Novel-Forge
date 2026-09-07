"""Tests for chapter studio widget rendering helpers."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QPainter, QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QApplication, QListView, QPushButton, QStyleOptionViewItem

from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState
from novel_forge.desktop.pages.chapter_studio.widgets import (
    ChapterRailDelegate,
    ChapterRailPanel,
    _status_icon,
    resolve_chapter_display_state,
)


def _job(
    job_id: str,
    project_id: str,
    status: DesktopJobState,
    chapter_number: int,
) -> DesktopJobRecord:
    return DesktopJobRecord(
        job_id=job_id,
        kind="run_chapter",
        label=f"章节续写 · {project_id} / 第 {chapter_number} 章",
        project_id=project_id,
        status=status,
        result={"chapter_number": chapter_number},
    )


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def test_chapter_rail_delegate_size_hint_has_fallback_width(qapp: QApplication) -> None:
    model = QStandardItemModel()
    model.appendRow(QStandardItem("第 1 章 开场 · 已完成"))
    index = model.index(0, 0)
    delegate = ChapterRailDelegate()

    option = QStyleOptionViewItem()
    size = delegate.sizeHint(option, index)

    assert size.width() >= 220
    assert size.height() == 48


def test_chapter_rail_delegate_size_hint_uses_viewport_width(qapp: QApplication) -> None:
    model = QStandardItemModel()
    model.appendRow(QStandardItem("第 61 章 很长很长的章节标题 · 已完成"))
    index = model.index(0, 0)
    view = QListView()
    view.setModel(model)
    view.setFixedSize(180, 120)
    view.show()
    qapp.processEvents()
    delegate = ChapterRailDelegate()

    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 220, 48)
    option.widget = view
    size = delegate.sizeHint(option, index)

    assert 0 < size.width() <= 180
    assert size.height() == 48

    view.deleteLater()
    qapp.processEvents()


def test_chapter_rail_delegate_paint_does_not_raise(qapp: QApplication) -> None:
    model = QStandardItemModel()
    model.appendRow(QStandardItem("第 1 章 开场 · 已完成"))
    index = model.index(0, 0)
    delegate = ChapterRailDelegate()

    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 240, 48)
    pixmap = QPixmap(240, 48)
    painter = QPainter(pixmap)

    try:
        delegate.paint(painter, option, index)
    finally:
        painter.end()


def test_chapter_rail_jobs_are_scoped_to_current_project(qapp: QApplication) -> None:
    panel = ChapterRailPanel()

    panel.bind_jobs(
        [
            _job("a-running", "project-a", DesktopJobState.RUNNING, 2),
            _job("b-running", "project-b", DesktopJobState.RUNNING, 61),
        ],
        project_id="project-b",
    )

    assert panel._chapter_job_status == {61: DesktopJobState.RUNNING}

    panel.deleteLater()
    qapp.processEvents()


def test_chapter_rail_keeps_highest_priority_status(qapp: QApplication) -> None:
    panel = ChapterRailPanel()

    panel.bind_jobs(
        [
            _job("running", "project-a", DesktopJobState.RUNNING, 2),
            _job("done", "project-a", DesktopJobState.SUCCEEDED, 2),
        ],
        project_id="project-a",
    )

    assert panel._chapter_job_status == {2: DesktopJobState.RUNNING}

    panel.deleteLater()
    qapp.processEvents()


def test_done_chapter_status_overrides_stale_failed_job() -> None:
    assert _status_icon(DesktopJobState.FAILED, "done") == ("✓", "#5f8b5a")
    assert _status_icon(DesktopJobState.PAUSED, "done") == ("✓", "#5f8b5a")
    assert _status_icon(DesktopJobState.RUNNING, "done") == ("⟳", "#b65634")
    state = resolve_chapter_display_state(DesktopJobState.FAILED, "done")
    assert state.variant == "default"
    assert state.ignored_job_status == DesktopJobState.FAILED


def _chapters(count: int) -> list[dict[str, object]]:
    return [
        {
            "chapter_number": chapter_number,
            "title": f"章标题 {chapter_number}",
            "status": "done",
            "status_label": "已完成",
        }
        for chapter_number in range(1, count + 1)
    ]


def test_chapter_rail_uses_virtual_list_for_large_projects(qapp: QApplication) -> None:
    panel = ChapterRailPanel()

    panel.render_chapters(_chapters(92), current_chapter=6)

    assert panel._use_virtual is True
    assert panel._scroll.isHidden()
    assert panel._list_view is not None
    assert not panel._list_view.isHidden()
    assert panel._list_model is not None
    assert panel._list_model.rowCount() == 92
    active_index = panel._list_model.index(5, 0)
    assert bool(active_index.data(Qt.ItemDataRole.CheckStateRole)) is True
    assert active_index.data(Qt.ItemDataRole.ToolTipRole) == "第 6 章 章标题 6 · 已完成"

    panel.deleteLater()
    qapp.processEvents()


def test_chapter_rail_switches_between_button_and_virtual_modes(qapp: QApplication) -> None:
    panel = ChapterRailPanel()

    panel.render_chapters(_chapters(92), current_chapter=6)
    assert panel._use_virtual is True
    assert panel._list_view is not None
    assert not panel._list_view.isHidden()

    panel.render_chapters(_chapters(12), current_chapter=3)
    assert panel._use_virtual is False
    assert panel._list_view.isHidden()
    assert not panel._scroll.isHidden()
    assert len(panel.findChildren(QPushButton, "chapterRailBtn")) == 12

    panel.render_chapters(_chapters(92), current_chapter=90)
    assert panel._use_virtual is True
    assert panel._scroll.isHidden()
    assert not panel._list_view.isHidden()
    assert panel._list_model is not None
    assert panel._list_model.rowCount() == 92
    active_index = panel._list_model.index(89, 0)
    assert bool(active_index.data(Qt.ItemDataRole.CheckStateRole)) is True

    panel.deleteLater()
    qapp.processEvents()


def test_chapter_rail_buttons_have_readable_wide_limit(qapp: QApplication) -> None:
    panel = ChapterRailPanel()

    panel.render_chapters(_chapters(12), current_chapter=10)

    buttons = panel.findChildren(QPushButton, "chapterRailBtn")
    assert buttons
    assert min(button.maximumWidth() for button in buttons) >= 260

    panel.deleteLater()
    qapp.processEvents()
