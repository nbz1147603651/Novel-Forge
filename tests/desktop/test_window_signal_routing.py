"""Tests for window-level signal routing of dashboard quick actions (Task 8).

The dashboard exposes three signals when a user clicks the project card's
快捷动作 (阅卷 / 续写 / 蓝图 / 图谱 / 档案) — but the focus_xxx helpers
on ProjectsPage must exist and be callable so the window can route the
blueprint / graph / profile signals into the appropriate tab.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QTabWidget, QWidget  # noqa: E402

from novel_forge.desktop.pages.standalone.dashboard_page import DashboardPage  # noqa: E402
from novel_forge.desktop.pages.standalone.projects_page import ProjectsPage  # noqa: E402


def _make_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_dashboard_blueprint_signal_emits() -> None:
    _make_app()
    page = DashboardPage()
    mock = MagicMock()
    page.open_blueprint_requested.connect(mock)
    page.open_blueprint_requested.emit("test_pid")
    mock.assert_called_once_with("test_pid")


def test_dashboard_graph_signal_emits() -> None:
    _make_app()
    page = DashboardPage()
    mock = MagicMock()
    page.open_graph_requested.connect(mock)
    page.open_graph_requested.emit("pid_g")
    mock.assert_called_once_with("pid_g")


def test_dashboard_profile_signal_emits() -> None:
    _make_app()
    page = DashboardPage()
    mock = MagicMock()
    page.open_profile_requested.connect(mock)
    page.open_profile_requested.emit("pid_p")
    mock.assert_called_once_with("pid_p")


def test_dashboard_book_consistency_signal_emits() -> None:
    _make_app()
    page = DashboardPage()
    mock = MagicMock()
    page.open_book_consistency_requested.connect(mock)
    page.open_book_consistency_requested.emit("pid_b")
    mock.assert_called_once_with("pid_b")


def test_projects_page_has_focus_methods() -> None:
    """ProjectsPage must expose the three focus helpers used by the router."""
    _make_app()
    page = ProjectsPage()
    assert hasattr(page, "focus_narrative_blueprint_tab")
    assert hasattr(page, "focus_relationship_tracking_tab")
    assert hasattr(page, "focus_character_bible_tab")
    assert hasattr(page, "focus_book_consistency_tab")
    assert callable(page.focus_narrative_blueprint_tab)
    assert callable(page.focus_relationship_tracking_tab)
    assert callable(page.focus_character_bible_tab)
    assert callable(page.focus_book_consistency_tab)


def test_focus_outer_tab_by_text_no_crash_without_tabs() -> None:
    """Helper must be a no-op when outer tabs aren't built yet."""
    _make_app()
    page = ProjectsPage()
    # _outer_tabs is None until a project is loaded — must not raise
    assert page._outer_tabs is None
    page.focus_narrative_blueprint_tab()
    page.focus_relationship_tracking_tab()
    page.focus_character_bible_tab()
    page.focus_book_consistency_tab()


def test_focus_outer_tab_by_text_selects_correct_outer_tab() -> None:
    """With a stub QTabWidget the helper must set the matching tab active."""
    _make_app()
    page = ProjectsPage()
    outer = QTabWidget()
    outer.addTab(QTabWidget(), "叙事蓝图")
    outer.addTab(QTabWidget(), "追踪")
    outer.addTab(QTabWidget(), "基础设定")
    page._outer_tabs = outer

    # Select the 叙事蓝图 outer tab
    page.focus_narrative_blueprint_tab()
    assert outer.currentIndex() == 0

    # Select the 追踪 outer tab
    page.focus_relationship_tracking_tab()
    assert outer.currentIndex() == 1

    # Select the 基础设定 outer tab
    page.focus_character_bible_tab()
    assert outer.currentIndex() == 2


def test_focus_relationship_tracking_tab_drills_into_inner() -> None:
    """The helper must drill into the inner tab widget to find 关系追踪."""
    _make_app()
    page = ProjectsPage()
    outer = QTabWidget()
    inner = QTabWidget()
    inner.addTab(QTabWidget(), "关系追踪")
    inner.addTab(QTabWidget(), "Token 追踪")
    outer.addTab(inner, "追踪")
    outer.addTab(QTabWidget(), "叙事蓝图")
    page._outer_tabs = outer

    page.focus_relationship_tracking_tab()
    assert outer.currentIndex() == 0
    assert inner.currentIndex() == 0


def test_focus_character_bible_tab_drills_into_group_inner() -> None:
    """The helper must drill into the inner group tab widget for 角色与实体."""
    _make_app()
    page = ProjectsPage()
    outer = QTabWidget()
    group_inner = QTabWidget()
    group_inner.addTab(QTabWidget(), "故事规格")
    group_inner.addTab(QTabWidget(), "世界观")
    group_inner.addTab(QTabWidget(), "角色与实体")
    outer.addTab(group_inner, "基础设定")
    outer.addTab(QTabWidget(), "叙事蓝图")
    page._outer_tabs = outer

    page.focus_character_bible_tab()
    assert outer.currentIndex() == 0
    assert group_inner.currentIndex() == 2


def test_focus_book_consistency_tab_drills_into_governance_inner() -> None:
    """The helper must drill into 治理 → 全书审修."""
    _make_app()
    page = ProjectsPage()
    outer = QTabWidget()
    governance_inner = QTabWidget()
    governance_inner.addTab(QTabWidget(), "初始化准入")
    governance_inner.addTab(QTabWidget(), "全书审修")
    outer.addTab(QTabWidget(), "叙事蓝图")
    outer.addTab(governance_inner, "治理")
    page._outer_tabs = outer

    page.focus_book_consistency_tab()
    assert outer.currentIndex() == 1
    assert governance_inner.currentIndex() == 1


def test_entering_outer_tab_builds_default_nested_lazy_tab() -> None:
    """A lazy child at index 0 must load when its outer tab becomes active."""
    _make_app()
    page = ProjectsPage()
    outer = QTabWidget()
    outer.addTab(QWidget(), "基础设定")
    tracking = QTabWidget()
    build_calls: list[str] = []

    def _build_relationships() -> QWidget:
        build_calls.append("relationships")
        return QLabel("关系追踪已加载")

    page._register_lazy_tab(tracking, "关系追踪", _build_relationships)
    outer.addTab(tracking, "追踪")
    page._outer_tabs = outer
    outer.currentChanged.connect(page._on_outer_tab_changed)

    assert tracking.currentIndex() == 0
    assert build_calls == []

    outer.setCurrentIndex(1)

    assert build_calls == ["relationships"]
    assert isinstance(tracking.currentWidget(), QLabel)
    assert tracking.currentWidget().text() == "关系追踪已加载"


def test_window_has_open_routing_slots() -> None:
    """The window class must expose the three router slot methods."""
    from novel_forge.desktop.window import NovelForgeDesktopWindow

    assert hasattr(NovelForgeDesktopWindow, "_on_open_blueprint")
    assert hasattr(NovelForgeDesktopWindow, "_on_open_graph")
    assert hasattr(NovelForgeDesktopWindow, "_on_open_profile")
    assert hasattr(NovelForgeDesktopWindow, "_on_open_book_consistency")
    assert callable(NovelForgeDesktopWindow._on_open_blueprint)
    assert callable(NovelForgeDesktopWindow._on_open_graph)
    assert callable(NovelForgeDesktopWindow._on_open_profile)
    assert callable(NovelForgeDesktopWindow._on_open_book_consistency)
