"""Smoke tests for 6 key desktop pages.

Each test verifies that the page constructs, shows, and closes without error
in offscreen mode.  This validates that pages handle the absence of runtime
services (degraded mode).
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

# ── Page smoke tests ─────────────────────────────────────────


def test_dashboard_page_smoke(desktop_app: QApplication) -> None:
    """DashboardPage constructs, shows, and closes without error."""
    from novel_forge.desktop.pages.standalone.dashboard_page import DashboardPage

    page = DashboardPage()
    try:
        page.show()
        desktop_app.processEvents()
    finally:
        page.close()
        page.deleteLater()
        desktop_app.processEvents()


def test_projects_page_smoke(desktop_app: QApplication) -> None:
    """ProjectsPage constructs, shows, and closes without error."""
    from novel_forge.desktop.pages.standalone.projects_page import ProjectsPage

    page = ProjectsPage()
    try:
        page.show()
        desktop_app.processEvents()
    finally:
        page.close()
        page.deleteLater()
        desktop_app.processEvents()


def test_workflow_page_smoke(desktop_app: QApplication) -> None:
    """WorkflowPage constructs, shows, and closes without error."""
    from novel_forge.desktop.pages.workflow.page import WorkflowPage

    page = WorkflowPage()
    try:
        page.show()
        desktop_app.processEvents()
    finally:
        page.close()
        page.deleteLater()
        desktop_app.processEvents()


def test_settings_page_smoke(desktop_app: QApplication) -> None:
    """SettingsPage constructs, shows, and closes without error."""
    from novel_forge.desktop.pages.settings.page import SettingsPage

    page = SettingsPage()
    try:
        page.show()
        desktop_app.processEvents()
    finally:
        page.close()
        page.deleteLater()
        desktop_app.processEvents()


def test_chapter_studio_page_smoke(desktop_app: QApplication) -> None:
    """ChapterStudioPage constructs, shows, and closes without error."""
    from novel_forge.desktop.pages.chapter_studio.page import ChapterStudioPage

    page = ChapterStudioPage()
    try:
        page.show()
        desktop_app.processEvents()
    finally:
        page.close()
        page.deleteLater()
        desktop_app.processEvents()


def test_chapter_studio_selector_state_roundtrip(desktop_app: QApplication) -> None:
    """Writing/decision selectors remain per-project across a UI-session restore."""
    from novel_forge.desktop.pages.chapter_studio.page import ChapterStudioPage

    source = ChapterStudioPage()
    restored = ChapterStudioPage()
    try:
        source._activate_project_context("long_demo")
        source.set_mode(source.MODE_BOOK_AUTO)
        source._writing_mode_selector.set_mode("scene_level")
        source._follow_autorun_cb.setChecked(True)

        restored.restore_ui_state(source.export_ui_state())
        restored._activate_project_context("long_demo")

        assert restored._mode == restored.MODE_BOOK_AUTO
        assert restored.current_writing_mode() == "scene_level"
        assert restored._follow_autorun_cb.isChecked() is True
        assert restored._auto_started is False
    finally:
        source.shutdown()
        restored.shutdown()
        source.deleteLater()
        restored.deleteLater()
        desktop_app.processEvents()


def test_other_primary_pages_restore_safe_session_choices(desktop_app: QApplication) -> None:
    """Primary pages retain restart-safe locations without replaying work."""
    from novel_forge.core.config import Settings
    from novel_forge.desktop.pages.settings.page import SettingsPage
    from novel_forge.desktop.pages.standalone.dashboard_page import DashboardPage
    from novel_forge.desktop.pages.standalone.projects_page import ProjectsPage
    from novel_forge.desktop.pages.voice_studio.page import VoiceStudioPage
    from novel_forge.desktop.pages.workflow.page import WorkflowPage

    dashboard = DashboardPage()
    projects = ProjectsPage()
    workflow = WorkflowPage()
    settings = SettingsPage()
    voice = VoiceStudioPage(settings=Settings(_env_file=None))
    try:
        dashboard.restore_ui_state(
            {"selected_project_id": "demo", "filter": "long", "search": "旧城"}
        )
        assert dashboard.export_ui_state()["selected_project_id"] == "demo"
        assert dashboard.export_ui_state()["filter"] == "long"
        assert dashboard.export_ui_state()["search"] == "旧城"

        projects.restore_ui_state(
            {
                "project_id": "demo",
                "outer_tab": "章节",
                "inner_tabs": {"章节": "报告"},
                "chapter_prose": 4,
                "chapter_report": 5,
            }
        )
        project_state = projects.export_ui_state()
        assert project_state["project_id"] == "demo"
        assert project_state["chapter_prose"] == 4
        assert project_state["chapter_report"] == 5

        workflow.restore_ui_state(
            {
                "mode": "long",
                "long": {
                    "mode": "chapter",
                    "chapter_project_id": "demo",
                    "chapter_number": 6,
                },
            }
        )
        workflow_state = workflow.export_ui_state()
        assert workflow_state["mode"] == "long"
        assert workflow_state["long"]["chapter_project_id"] == "demo"
        assert workflow_state["long"]["chapter_number"] == 6

        settings.restore_ui_state({"scroll_value": 200})
        voice.restore_ui_state({"project_id": "demo", "tab_index": 4, "chapter_number": 3})
        assert voice._custom_tab_bar.currentIndex() == 4  # noqa: SLF001 - UI contract
        assert voice.export_ui_state()["chapter_number"] == 3
    finally:
        for page in (dashboard, projects, workflow, settings, voice):
            shutdown = getattr(page, "shutdown", None)
            if callable(shutdown):
                shutdown()
            page.deleteLater()
        desktop_app.processEvents()


def test_memory_panel_smoke(desktop_app: QApplication) -> None:
    """UnifiedMemoryPanel (memory panel widget) constructs without error."""
    from novel_forge.desktop.components.memory_components import UnifiedMemoryPanel

    panel = UnifiedMemoryPanel()
    try:
        panel.show()
        desktop_app.processEvents()
    finally:
        panel.close()
        panel.deleteLater()
        desktop_app.processEvents()


# ── Edge case: construct without runtime services ────────────


def test_all_pages_construct_degraded(desktop_app: QApplication) -> None:
    """All 6 pages must construct without error when no runtime services are configured.

    This exercises the degraded-mode code path where UI store, workspace
    snapshot, and model profiles may be unavailable.
    """
    from novel_forge.desktop.components.memory_components import UnifiedMemoryPanel
    from novel_forge.desktop.pages.chapter_studio.page import ChapterStudioPage
    from novel_forge.desktop.pages.settings.page import SettingsPage
    from novel_forge.desktop.pages.standalone.dashboard_page import DashboardPage
    from novel_forge.desktop.pages.standalone.projects_page import ProjectsPage
    from novel_forge.desktop.pages.workflow.page import WorkflowPage

    pages: list[type] = [
        DashboardPage,
        ProjectsPage,
        WorkflowPage,
        SettingsPage,
        ChapterStudioPage,
        UnifiedMemoryPanel,
    ]

    for Page in pages:
        try:
            page = Page()
            assert page is not None
            name = getattr(Page, "__name__", str(Page))
            print(f"  {name}: OK")
        except Exception as exc:
            name = getattr(Page, "__name__", str(Page))
            pytest.fail(f"{name} failed to construct: {type(exc).__name__}: {exc}")
        finally:
            if page is not None:
                page.deleteLater()
                desktop_app.processEvents()
