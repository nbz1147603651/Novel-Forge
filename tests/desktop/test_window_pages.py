"""Tests for page switching in NovelForgeDesktopWindow.

Covers: switch_page, navigation buttons, page meta binding,
deep-link targets, and invalid page handling.
"""

from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot


def test_main_window_alias_matches_desktop_window_class() -> None:
    from novel_forge.desktop.window import MainWindow, NovelForgeDesktopWindow

    assert MainWindow is NovelForgeDesktopWindow


def test_window_snapshot_hash_static_methods_delegate_to_helper_module() -> None:
    from pathlib import Path
    from types import SimpleNamespace

    from novel_forge.desktop.window import NovelForgeDesktopWindow
    from novel_forge.desktop.workspace_snapshot_hash import (
        build_snapshot_payload_static,
        compute_section_hashes,
    )

    snapshot = SimpleNamespace(
        storage_root=Path("/tmp/novel-forge"),
        default_provider="mock",
        overview={"projects": 1},
        metrics={"tokens": 42},
        providers={"mock": {"enabled": True}},
        projects=[{"project_id": "demo"}],
        featured_project={"project_id": "demo"},
        details={"demo": SimpleNamespace(index={"title": "测试项目", "chapter": 1})},
    )
    payload = build_snapshot_payload_static(snapshot)

    assert NovelForgeDesktopWindow._build_snapshot_payload_static(snapshot) == payload
    assert NovelForgeDesktopWindow._compute_section_hashes(
        snapshot, payload
    ) == compute_section_hashes(
        snapshot,
        payload,
    )


@pytest.fixture
def window(qtbot: QtBot):
    """Create a NovelForgeDesktopWindow with the real page registrations."""
    from novel_forge.desktop.window import NovelForgeDesktopWindow

    win = NovelForgeDesktopWindow()
    qtbot.addWidget(win)
    return win


# ── switch_page ──────────────────────────────────────────────────────────


class TestSwitchPage:
    """Tests for the switch_page() method."""

    def test_switch_to_valid_page(self, window, qtbot: QtBot) -> None:
        """switch_page() changes the visible page."""
        window.switch_page("projects")
        assert window._current_page_id() == "projects"

    def test_switch_to_invalid_page_is_noop(self, window) -> None:
        """switch_page() ignores unknown page_id."""
        before = window._current_page_id()
        window.switch_page("nonexistent_page")
        assert window._current_page_id() == before

    def test_switch_updates_nav_buttons(self, window) -> None:
        """switch_page() checks the correct navigation button."""
        window.switch_page("workflow")
        assert window._nav_buttons["workflow"].isChecked()
        assert not window._nav_buttons["dashboard"].isChecked()

    def test_switch_to_dashboard(self, window) -> None:
        """Switch back to dashboard from another page."""
        window.switch_page("settings")
        window.switch_page("dashboard")
        assert window._current_page_id() == "dashboard"
        assert window._nav_buttons["dashboard"].isChecked()


# ── page meta ─────────────────────────────────────────────────────────────


class TestPageMeta:
    """Tests for page metadata display after switching."""

    def test_page_meta_updated_on_switch(self, window) -> None:
        """switch_page() updates top bar eyebrow/title/subtitle."""
        window.switch_page("settings")
        assert window._top_eyebrow.text() == "火候"
        assert window._top_title.text() == "读懂当前运行环境"
        assert "工作区" in window._top_subtitle.text()

    def test_page_meta_for_chapter_studio(self, window) -> None:
        """Page meta reflects chapter studio labels."""
        window.switch_page("chapter_studio")
        assert window._top_eyebrow.text() == "章台"
        assert "章节" in window._top_title.text()

    def test_voice_studio_context_uses_shared_header_center(self, window, qtbot: QtBot) -> None:
        """Voice metrics and platform switch replace the empty header center."""
        from PySide6.QtWidgets import QApplication

        window.switch_page("voice_studio")
        # Voice Studio is in _COLD_INSTANT_PAGE_IDS, so page creation is
        # deferred via QTimer.singleShot(16ms). Process events to let the
        # timer fire.
        for _ in range(50):  # 50 * 100ms = 5s max
            QApplication.processEvents()
            if window._pages.get("voice_studio") is not None:
                break
            import time
            time.sleep(0.1)

        # Wait for the top bar widget to be mounted
        for _ in range(30):  # 30 * 100ms = 3s max
            QApplication.processEvents()
            page = window._pages.get("voice_studio")
            if page is not None:
                top_bar = page.get_top_bar_widget()
                if top_bar is not None and window._top_layout.indexOf(top_bar) == 1:
                    break
            import time
            time.sleep(0.1)

        page = window._pages.get("voice_studio")
        assert page is not None, "voice_studio page was not created"
        context = page.get_top_bar_widget()

        assert context is not None, "get_top_bar_widget() returned None"
        assert window._top_layout.indexOf(context) == 1, f"top_bar not at index 1, got {window._top_layout.indexOf(context)}"
        assert not context.isHidden()
        assert window._top_meta.isHidden()


# ── deep-link targets ─────────────────────────────────────────────────────


class TestDeepLinkTargets:
    """Tests for compound page_id targets (e.g. 'projects:relationships')."""

    def test_compound_target_strips_tab(self, window) -> None:
        """switch_page('projects:relationships') switches to projects page."""
        window.switch_page("projects:relationships")
        assert window._current_page_id() == "projects"

    def test_compound_target_invalid_page_is_noop(self, window) -> None:
        """switch_page('unknown:tab') does nothing."""
        before = window._current_page_id()
        window.switch_page("unknown:tab")
        assert window._current_page_id() == before


# ── page stack ────────────────────────────────────────────────────────────


class TestPageStack:
    """Tests for the QStackedWidget page stack."""

    def test_all_registered_pages_in_stack(self, window) -> None:
        """All registered pages are added to the QStackedWidget."""
        stack_count = window._stack.count()
        registered_count = len(window._pages)
        assert stack_count == registered_count

    def test_pages_dict_matches_registry(self, window) -> None:
        """window._pages keys match the 5 built-in pages."""
        expected = {
            "dashboard",
            "projects",
            "workflow",
            "settings",
            "chapter_studio",
            "voice_studio",
        }
        assert set(window._pages.keys()) == expected

    def test_prewarm_settings_does_not_activate(self, window, monkeypatch) -> None:
        """Visual prewarm must not start settings-page business activation."""
        from novel_forge.desktop.pages.settings.page import SettingsPage

        calls: list[str] = []
        monkeypatch.setattr(SettingsPage, "activate", lambda _self: calls.append("activate"))

        window._prewarm_page("settings")

        assert calls == []
        assert window._pages.get("settings") is not None
        assert window._current_page_id() == "dashboard"


# ── current_page_id ───────────────────────────────────────────────────────


class TestCurrentPageId:
    """Tests for _current_page_id() helper."""

    def test_initial_page_is_dashboard(self, window) -> None:
        """Default page after construction is dashboard."""
        assert window._current_page_id() == "dashboard"

    def test_current_page_id_after_switch(self, window) -> None:
        """_current_page_id() reflects the last switch."""
        window.switch_page("workflow")
        assert window._current_page_id() == "workflow"
