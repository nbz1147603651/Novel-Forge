"""Tests for the dashboard ProjectCard quick action bar (Task 6).

Verifies that:
1. ``DashboardPage`` exposes three new signals for blueprint / graph / profile
   navigation.
2. ``ProjectCard`` carries a quick action bar with five ``QToolButton`` entries
   (阅卷 / 续写 / 蓝图 / 图谱 / 档案) wired via ``set_action_callbacks``.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFrame, QToolButton  # noqa: E402

from novel_forge.desktop.pages.standalone.dashboard_page import (  # noqa: E402
    DashboardPage,
    ProjectCard,
)
from novel_forge.desktop.workspace import DesktopProjectItem  # noqa: E402


def _make_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _make_project(project_id: str = "p1") -> DesktopProjectItem:
    return DesktopProjectItem(
        project_id=project_id,
        title="测试卷",
        mode="long",
        mode_label="长篇",
        status="writing",
        status_label="连载中",
        progress_label="30%",
        progress_percent=30,
        last_updated_label="今日",
        headline="测试卷摘要",
        next_action="续写",
        genre="mystery",
        tone="suspenseful",
        completed_chapters=3,
        total_chapters=10,
        next_chapter=4,
        has_outline=True,
        has_canon=True,
    )


def test_dashboard_has_quick_action_signals() -> None:
    _make_app()
    page = DashboardPage()
    # Project/page quick-nav signals must exist.
    assert hasattr(page, "open_blueprint_requested")
    assert hasattr(page, "open_graph_requested")
    assert hasattr(page, "open_profile_requested")
    assert hasattr(page, "open_book_consistency_requested")


def test_project_card_has_quick_action_buttons() -> None:
    _make_app()
    card = ProjectCard(_make_project())
    buttons = card.findChildren(QToolButton)
    tooltips = {b.toolTip() for b in buttons}
    # 至少应包含阅卷 / 续写 / 蓝图 / 图谱 / 档案 中的 5 个
    expected = {"阅卷", "续写", "蓝图", "图谱", "档案"}
    found = expected & tooltips
    assert len(found) >= 5, f"missing quick actions: {expected - found}"


def test_project_card_hides_buttons_without_callbacks() -> None:
    """不传 callback 时按钮应隐藏。"""
    _make_app()
    card = ProjectCard(_make_project())
    # 默认不注入任何 callback
    action_bar_buttons = [
        b
        for b in card.findChildren(QToolButton)
        if b.property("action") in {"view", "compose", "blueprint", "graph", "profile"}
    ]
    # 五个按钮都在 DOM 里，但都不可见
    assert len(action_bar_buttons) == 5
    for btn in action_bar_buttons:
        # ``isHidden`` reflects ``setVisible(False)`` without requiring the
        # parent widget to actually be shown.
        assert btn.isHidden() is True


def test_project_card_buttons_emit_with_callbacks() -> None:
    """注入 callback 后按钮可见，点击触发 callback。"""
    _make_app()
    card = ProjectCard(_make_project("alpha"))
    captured: list[tuple[str, str]] = []

    def make_cb(action: str):
        def cb(pid: str) -> None:
            captured.append((action, pid))
        return cb

    card.set_action_callbacks(
        view=make_cb("view"),
        compose=make_cb("compose"),
        blueprint=make_cb("blueprint"),
        graph=make_cb("graph"),
        profile=make_cb("profile"),
    )

    # 五个按钮都应可见（isHidden=False 即表示已 setVisible(True)）
    visible = [
        b
        for b in card.findChildren(QToolButton)
        if b.property("action") in {"view", "compose", "blueprint", "graph", "profile"}
    ]
    assert len(visible) == 5
    for btn in visible:
        assert btn.isHidden() is False

    # 点击 "blueprint" 按钮应调用对应 callback 并把 project_id 传出去
    blueprint_btn = next(b for b in visible if b.property("action") == "blueprint")
    blueprint_btn.click()
    assert ("blueprint", "alpha") in captured


def test_dashboard_does_not_render_global_quick_nav_row() -> None:
    _make_app()
    page = DashboardPage()
    assert page.findChild(QToolButton, "quickNavBtn") is None
    assert page.findChild(QFrame, "quickNavRow") is None

    tooltips = {b.toolTip() for b in page.findChildren(QToolButton)}
    expected = {"叙事蓝图", "角色图谱", "角色档案", "章台", "全书审修"}
    assert expected.isdisjoint(tooltips)
