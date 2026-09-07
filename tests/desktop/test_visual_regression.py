"""Visual regression tests for Novel Forge desktop app.

Captures and compares baseline screenshots of top-level pages:
- DashboardPage
- ProjectsPage
- WorkflowPage
- ChapterStudioPage
- SettingsPage

Usage:
    # Run visual regression tests
    pytest tests/desktop/test_visual_regression.py -v

    # Update baselines (after intentional UI changes)
    pytest tests/desktop/test_visual_regression.py -v --update-baselines

    # Run with custom threshold
    pytest tests/desktop/test_visual_regression.py -v --visual-threshold=0.05
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from tests.desktop.visual_regression import (
    VISUAL_STORAGE_ROOT,
    assert_visual_match,
    capture_widget_screenshot,
)

pytestmark = pytest.mark.skipif(
    bool(os.environ.get("PYTEST_XDIST_WORKER")),
    reason="PySide visual regression screenshots are xdist-unsafe; run them serially.",
)


def _visual_profiles_config() -> object:
    from novel_forge.gateway.profiles import ModelProfile, ProfilesConfig

    profile = ModelProfile(
        profile_id="openai:gpt-4o-mini",
        display_name="OpenAI Mini",
        provider="openai",
        model_id="gpt-4o-mini",
        api_key="sk-test",
    )
    return ProfilesConfig(profiles=[profile], default_profile_id=profile.profile_id)


@pytest.fixture
def visual_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Provide deterministic workspace and profile config for visual snapshots."""
    root = VISUAL_STORAGE_ROOT
    root.mkdir(parents=True, exist_ok=True)

    def _load_profiles(_settings: object | None = None) -> object:
        return _visual_profiles_config()

    from PySide6.QtWidgets import QApplication

    import novel_forge.desktop.pages.settings.page as settings_page
    import novel_forge.desktop.pages.standalone.dashboard_page as dashboard_page
    from novel_forge.desktop import constants as desktop_constants
    from novel_forge.desktop.components.containers import CollapsibleSection
    from novel_forge.desktop.motion import Motion
    from novel_forge.desktop.pages.settings.components import _ConnectionTestWorker
    from novel_forge.desktop.theme import get_stylesheet

    monkeypatch.setattr(dashboard_page, "load_or_import_profiles", _load_profiles)
    monkeypatch.setattr(settings_page, "load_or_import_profiles", _load_profiles)
    monkeypatch.setattr(desktop_constants, "ANIMATIONS_ENABLED", False)
    monkeypatch.setattr(
        CollapsibleSection,
        "_load_expanded_from_settings",
        classmethod(
            lambda _cls, key: {
                "settings/models_expanded": False,
                "settings/routing_expanded": True,
            }.get(key)
        ),
    )
    monkeypatch.setattr(
        _ConnectionTestWorker,
        "_result_cache",
        {
            "openai:gpt-4o-mini": (
                time.monotonic(),
                True,
                "连通 12ms",
                True,
                True,
            )
        },
    )

    class _InstantSignal:
        def connect(self, callback):  # noqa: ANN001, ANN202
            callback()

    class _InstantAnimation:
        finished = _InstantSignal()

        def stop(self) -> None:
            return

    def _instant_fade(widget, **_kwargs):  # noqa: ANN001, ANN202
        effect = widget.graphicsEffect()
        if effect is not None and hasattr(effect, "setOpacity"):
            effect.setOpacity(1.0)
        return _InstantAnimation()

    def _instant_scale(widget, *, end_value: float = 1.0, **_kwargs):  # noqa: ANN001, ANN202
        if hasattr(widget, "_scale"):
            widget._scale = end_value
        widget.update()
        return _InstantAnimation()

    monkeypatch.setattr(Motion, "fade_in", staticmethod(_instant_fade))
    monkeypatch.setattr(Motion, "scale", staticmethod(_instant_scale))

    app = QApplication.instance()
    old_stylesheet = app.styleSheet() if app is not None else ""
    if app is not None:
        app.setStyleSheet(get_stylesheet())
    try:
        yield root
    finally:
        if app is not None:
            app.setStyleSheet(old_stylesheet)


def _render_page_with_workspace(page, qtbot: QtBot, storage_root: Path) -> None:
    from novel_forge.desktop.workspace import (
        DesktopProjectItem,
        DesktopWorkspaceMetrics,
        DesktopWorkspaceSnapshot,
        ProviderStatus,
    )
    from novel_forge.workspace.projects import ProjectDetail, WorkspaceOverview

    short_detail = ProjectDetail(
        project_id="test-short",
        mode="short",
        title="测试短篇",
        genre="mystery",
        tone="suspenseful",
        premise="一个关于遗物整理的短篇故事",
        preview="",
        chapters=[],
        artifact_counts={"chapters": 1, "spec": 1},
        recent_files=["chapters/short_story.md", "spec.json"],
        outline_generated_count=0,
        total_chapters=0,
    )
    long_detail = ProjectDetail(
        project_id="test-long",
        mode="long",
        title="测试长篇",
        genre="scifi",
        tone="dark",
        premise="记忆回收师的长篇故事",
        preview="",
        chapters=[],
        artifact_counts={"outline": 1, "canon": 1},
        recent_files=["outline.json", "canon/canon_current.json"],
        outline_generated_count=1,
        total_chapters=24,
    )

    snapshot = DesktopWorkspaceSnapshot(
        storage_root=storage_root,
        default_provider="openai",
        overview=WorkspaceOverview(
            storage_root=str(storage_root),
            total_projects=2,
            short_projects=1,
            long_projects=1,
            total_generated_chapters=3,
            providers=["openai"],
            default_provider="openai",
        ),
        metrics=DesktopWorkspaceMetrics(
            total_projects=2,
            total_chapters=3,
            total_words=15000,
            configured_providers=1,
        ),
        providers=[
            ProviderStatus(
                provider_id="openai",
                label="OpenAI",
                detail="Visual fixture",
                is_default=True,
                ready=True,
                configured=True,
            ),
        ],
        projects=[
            DesktopProjectItem(
                project_id="test-short",
                title="测试短篇",
                mode="short",
                mode_label="短篇",
                status="completed",
                status_label="已完稿",
                progress_label="短篇完稿",
                progress_percent=100,
                next_chapter=None,
                last_updated_label="2026-04-30",
                headline="一个关于遗物整理的短篇故事",
                next_action="查看作品",
                genre="mystery",
                tone="suspenseful",
                completed_chapters=1,
                total_chapters=None,
                has_outline=False,
                has_canon=False,
            ),
            DesktopProjectItem(
                project_id="test-long",
                title="测试长篇",
                mode="long",
                mode_label="长篇",
                status="writing",
                status_label="连载中",
                progress_label="第 4 章完成",
                progress_percent=45,
                next_chapter=5,
                last_updated_label="2026-04-29",
                headline="记忆回收师的长篇故事",
                next_action="续写第 5 章",
                genre="scifi",
                tone="dark",
                completed_chapters=4,
                total_chapters=24,
                has_outline=True,
                has_canon=True,
            ),
        ],
        featured_project=None,
        details={
            "test-short": short_detail,
            "test-long": long_detail,
        },
    )
    page.bind_workspace(snapshot)
    qtbot.wait(100)


class TestDashboardPage:
    """Visual regression tests for DashboardPage."""

    def test_dashboard_renders(
        self,
        qtbot: QtBot,
        visual_threshold: float,
        update_baselines: bool,
        visual_workspace: Path,
    ) -> None:
        from novel_forge.desktop.pages.standalone.dashboard_page import DashboardPage

        page = DashboardPage()
        qtbot.addWidget(page)
        page.resize(1200, 800)
        page.show()

        _render_page_with_workspace(page, qtbot, visual_workspace)

        image = capture_widget_screenshot(page)
        error = assert_visual_match(
            image, "dashboard", threshold=visual_threshold, update_baselines=update_baselines,
        )
        assert not error, error


class TestProjectsPage:
    """Visual regression tests for ProjectsPage."""

    def test_projects_renders(
        self,
        qtbot: QtBot,
        visual_threshold: float,
        update_baselines: bool,
        visual_workspace: Path,
    ) -> None:
        from novel_forge.desktop.pages.standalone.projects_page import ProjectsPage

        page = ProjectsPage()
        qtbot.addWidget(page)
        page.resize(1200, 800)
        page.show()

        _render_page_with_workspace(page, qtbot, visual_workspace)

        image = capture_widget_screenshot(page)
        error = assert_visual_match(
            image, "projects", threshold=visual_threshold, update_baselines=update_baselines,
        )
        assert not error, error


class TestWorkflowPage:
    """Visual regression tests for WorkflowPage."""

    def test_workflow_renders(
        self,
        qtbot: QtBot,
        visual_threshold: float,
        update_baselines: bool,
        visual_workspace: Path,
    ) -> None:
        from novel_forge.desktop.pages.workflow.page import WorkflowPage

        page = WorkflowPage()
        qtbot.addWidget(page)
        page.resize(1200, 800)
        page.show()

        _render_page_with_workspace(page, qtbot, visual_workspace)

        image = capture_widget_screenshot(page)
        error = assert_visual_match(
            image, "workflow", threshold=visual_threshold, update_baselines=update_baselines,
        )
        assert not error, error


class TestChapterStudioPage:
    """Visual regression tests for ChapterStudioPage."""

    def test_chapter_studio_renders(
        self,
        qtbot: QtBot,
        visual_threshold: float,
        update_baselines: bool,
        visual_workspace: Path,
    ) -> None:
        from novel_forge.desktop.pages.chapter_studio.page import ChapterStudioPage

        page = ChapterStudioPage()
        qtbot.addWidget(page)
        page.resize(1200, 800)
        page.show()

        _render_page_with_workspace(page, qtbot, visual_workspace)

        image = capture_widget_screenshot(page)
        error = assert_visual_match(
            image, "chapter_studio", threshold=visual_threshold, update_baselines=update_baselines,
        )
        assert not error, error


class TestSettingsPage:
    """Visual regression tests for SettingsPage."""

    def test_settings_renders(
        self,
        qtbot: QtBot,
        visual_threshold: float,
        update_baselines: bool,
        visual_workspace: Path,
    ) -> None:
        from novel_forge.desktop.pages.settings.page import SettingsPage

        page = SettingsPage()
        qtbot.addWidget(page)
        page.resize(1200, 800)
        page.show()

        _render_page_with_workspace(page, qtbot, visual_workspace)

        image = capture_widget_screenshot(page)
        error = assert_visual_match(
            image, "settings", threshold=visual_threshold, update_baselines=update_baselines,
        )
        assert not error, error
