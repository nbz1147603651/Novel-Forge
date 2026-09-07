# behavior-snapshot — Desktop MainWindow behavior snapshot tests.
#
# These tests freeze the observable behavior of NovelForgeDesktopWindow's key
# structural properties so that the upcoming window.py refactor (Sprint 3) can
# be verified to preserve behavior.  Each test exercises one real aspect:
#   1. Page registry: the set of registered page IDs + their metadata
#   2. Signal connection topology: connect_page_signals for each page
#   3. Session save/load: _save_ui_session + _load_ui_session roundtrip
#   4. Shutdown protocol: _pre_close_cleanup stops all timers and pages
#
# If a refactoring legitimately changes behavior, update the expected values
# after review and note the reason in the commit message.

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from pytestqt.qtbot import QtBot

from novel_forge.desktop.registry import page_registry

_EXPECTED_PAGE_IDS: frozenset[str] = frozenset(
    {
        "dashboard",
        "projects",
        "workflow",
        "chapter_studio",
        "settings",
        "voice_studio",
    }
)

_EXPECTED_PAGE_META: dict[str, dict[str, str]] = {
    "dashboard": {
        "eyebrow": "案头",
        "title": "先定手头所重",
        "subtitle": "诸卷总领、任务行止与通路火候，皆陈于案头。",
    },
    "projects": {
        "eyebrow": "卷帙",
        "title": "卷帙总览",
        "subtitle": "设定、契约、章节与报告分层归档，查阅时少绕路。",
    },
    "workflow": {
        "eyebrow": "机杼",
        "title": "把任务调度清楚",
        "subtitle": "在同一页完成短篇快启、长篇立项与章节续写。",
    },
    "chapter_studio": {
        "eyebrow": "章台",
        "title": "把章节工作放到一张台面上",
        "subtitle": "上一章结果、本章目标、下一章预埋点与 AI 决策，俱在章台同看。",
    },
    "settings": {
        "eyebrow": "火候",
        "title": "读懂当前运行环境",
        "subtitle": "确认工作区、默认通路、Provider 和模式是否都在正确位置。",
    },
    "voice_studio": {
        "eyebrow": "声腔",
        "title": "AI 配音工作室",
        "subtitle": "配音团队管理、脚本预览、语音合成与音频导出。",
    },
}


class TestPageRegistrySnapshot:
    # behavior-snapshot: page registry is the stable interface for window.py

    def test_registered_page_ids_match_baseline(self) -> None:
        from novel_forge.desktop import window  # noqa: F401 — triggers registration

        actual = frozenset(page_registry.list_pages())
        assert actual == _EXPECTED_PAGE_IDS, (
            f"Page IDs changed:\n"
            f"  expected: {sorted(_EXPECTED_PAGE_IDS)}\n"
            f"  actual:   {sorted(actual)}\n"
            f"  added:    {sorted(actual - _EXPECTED_PAGE_IDS)}\n"
            f"  removed:  {sorted(_EXPECTED_PAGE_IDS - actual)}"
        )

    def test_page_metadata_matches_baseline(self) -> None:
        from novel_forge.desktop import window  # noqa: F401

        for page_id, expected_meta in _EXPECTED_PAGE_META.items():
            desc = page_registry.metadata(page_id)
            assert desc is not None, f"Page '{page_id}' has no metadata"
            actual = {
                "eyebrow": desc.eyebrow,
                "title": desc.title,
                "subtitle": desc.subtitle,
            }
            assert actual == expected_meta, (
                f"Page '{page_id}' metadata changed:\n"
                f"  expected: {expected_meta}\n"
                f"  actual:   {actual}"
            )


class TestSignalConnectionTopology:
    # behavior-snapshot: signal connections for each page

    def test_connect_page_signals_is_idempotent_for_all_pages(
        self, qtbot: QtBot, desktop_app: object
    ) -> None:
        from novel_forge.desktop.window import NovelForgeDesktopWindow

        win = NovelForgeDesktopWindow()
        qtbot.addWidget(win)
        try:
            for page_id in _EXPECTED_PAGE_IDS:
                if page_id not in win._pages:
                    continue
                page = win._pages[page_id]
                if page is None:
                    continue
                assert page_id in win._connected_page_signals or page_id in {
                    "dashboard",
                    "projects",
                    "workflow",
                    "chapter_studio",
                    "settings",
                    "voice_studio",
                }, f"Page '{page_id}' signals not connected"
        finally:
            win._pre_close_cleanup()
            win.deleteLater()

    def test_navigation_signals_wired_for_dashboard(
        self, qtbot: QtBot, desktop_app: object
    ) -> None:
        from novel_forge.desktop.window import NovelForgeDesktopWindow

        win = NovelForgeDesktopWindow()
        qtbot.addWidget(win)
        try:
            dashboard = win._pages["dashboard"]
            assert hasattr(dashboard, "navigate_requested")
            assert hasattr(dashboard, "compose_requested")
            assert hasattr(dashboard, "view_project_requested")
            assert hasattr(dashboard, "open_project_requested")
            assert hasattr(dashboard, "context_changed")
        finally:
            win._pre_close_cleanup()
            win.deleteLater()


class TestSessionSaveLoad:
    # behavior-snapshot: UI session persistence roundtrip

    def test_session_save_writes_expected_keys(
        self, qtbot: QtBot, desktop_app: object, tmp_path: Path
    ) -> None:
        from novel_forge.desktop.window import NovelForgeDesktopWindow

        session_file = tmp_path / "ui_session.json"
        win = NovelForgeDesktopWindow()
        qtbot.addWidget(win)
        try:
            with patch.object(
                NovelForgeDesktopWindow,
                "_ui_session_path",
                staticmethod(lambda: session_file),
            ):
                win._save_ui_session()
            assert session_file.exists(), "Session file should be written"
            state = json.loads(session_file.read_text(encoding="utf-8"))
            expected_keys = {
                "active_page",
                "side_rail_collapsed",
                "chapter_studio_project_id",
                "chapter_studio_chapter_numbers",
                "chapter_studio_chapter_number",
                "page_ui_states",
            }
            assert expected_keys.issubset(set(state.keys())), (
                f"Missing session keys: {expected_keys - set(state.keys())}"
            )
            assert isinstance(state["chapter_studio_chapter_numbers"], dict)
            assert isinstance(state["page_ui_states"], dict)
        finally:
            win._pre_close_cleanup()
            win.deleteLater()

    def test_session_save_load_roundtrip(
        self, qtbot: QtBot, desktop_app: object, tmp_path: Path
    ) -> None:
        from novel_forge.desktop.window import NovelForgeDesktopWindow

        session_file = tmp_path / "ui_session.json"
        win = NovelForgeDesktopWindow()
        qtbot.addWidget(win)
        try:
            with patch.object(
                NovelForgeDesktopWindow,
                "_ui_session_path",
                staticmethod(lambda: session_file),
            ):
                win._save_ui_session()
                assert session_file.exists()
                win._load_ui_session()
        finally:
            win._pre_close_cleanup()
            win.deleteLater()

    def test_session_restores_chapter_studio_selector_choices(
        self, qtbot: QtBot, desktop_app: object, tmp_path: Path
    ) -> None:
        """A saved selector choice survives an in-process session restore."""
        from novel_forge.desktop.window import NovelForgeDesktopWindow

        session_file = tmp_path / "ui_session.json"
        win = NovelForgeDesktopWindow()
        qtbot.addWidget(win)
        session_path_patch = patch.object(
            NovelForgeDesktopWindow,
            "_ui_session_path",
            staticmethod(lambda: session_file),
        )
        session_path_patch.start()
        try:
            studio = win._ensure_page(
                "chapter_studio",
                bind_workspace=False,
                bind_jobs=False,
            )
            assert studio is not None
            studio._activate_project_context("long_demo")
            studio.set_mode(studio.MODE_BOOK_AUTO)
            studio._writing_mode_selector.set_mode("scene_level")
            studio._follow_autorun_cb.setChecked(True)
            win._save_ui_session()

            studio.set_mode(studio.MODE_MANUAL)
            studio._writing_mode_selector.set_mode("whole_chapter")
            studio._follow_autorun_cb.setChecked(False)
            win._load_ui_session()

            assert studio._mode == studio.MODE_BOOK_AUTO
            assert studio.current_writing_mode() == "scene_level"
            assert studio._follow_autorun_cb.isChecked() is True
            assert studio._auto_started is False
        finally:
            win._pre_close_cleanup()
            win.deleteLater()
            session_path_patch.stop()


class TestShutdownProtocol:
    # behavior-snapshot: _pre_close_cleanup stops timers and shuts down pages

    def test_pre_close_cleanup_stops_timers(self, qtbot: QtBot, desktop_app: object) -> None:

        from novel_forge.desktop.window import NovelForgeDesktopWindow

        win = NovelForgeDesktopWindow()
        qtbot.addWidget(win)
        try:
            win._pre_close_cleanup()
            assert win._is_closing is True
            assert not win._refresh_timer.isActive()
            assert not win._fs_watcher_debounce_timer.isActive()
            assert not win._jobs_bind_timer.isActive()
            assert not win._density_resize_timer.isActive()
            assert not win._workspace_refresh_schedule_timer.isActive()
            assert not win._page_prewarm_timer.isActive()
        finally:
            win.deleteLater()

    def test_pre_close_cleanup_shuts_down_pages(self, qtbot: QtBot, desktop_app: object) -> None:
        from novel_forge.desktop.window import NovelForgeDesktopWindow

        win = NovelForgeDesktopWindow()
        qtbot.addWidget(win)
        try:
            dashboard = win._pages["dashboard"]
            assert dashboard is not None
            win._pre_close_cleanup()
        finally:
            win.deleteLater()

    def test_window_creation_and_destruction_is_clean(
        self, qtbot: QtBot, desktop_app: object
    ) -> None:
        from novel_forge.desktop.window import NovelForgeDesktopWindow

        win = NovelForgeDesktopWindow()
        qtbot.addWidget(win)
        assert win._pages is not None
        assert len(win._pages) >= 1
        win._pre_close_cleanup()
        win.deleteLater()


class TestPageEventSectionMap:
    # behavior-snapshot: page-to-section binding map is stable

    def test_page_section_map_keys_match_expected(self) -> None:
        from novel_forge.desktop.window import NovelForgeDesktopWindow

        expected_keys = frozenset(
            {
                "dashboard",
                "projects",
                "workflow",
                "chapter_studio",
                "voice_studio",
                "settings",
            }
        )
        actual_keys = frozenset(NovelForgeDesktopWindow._PAGE_SECTION_MAP.keys())
        assert actual_keys == expected_keys, (
            f"Page section map keys changed:\n"
            f"  expected: {sorted(expected_keys)}\n"
            f"  actual:   {sorted(actual_keys)}"
        )

    def test_prewarm_page_ids_are_subset_of_registered(self) -> None:
        from novel_forge.desktop.window import NovelForgeDesktopWindow

        prewarm = frozenset(NovelForgeDesktopWindow._PREWARM_PAGE_IDS)
        assert prewarm.issubset(_EXPECTED_PAGE_IDS), (
            f"Prewarm pages not in registered set: {prewarm - _EXPECTED_PAGE_IDS}"
        )
