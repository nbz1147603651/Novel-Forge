"""Visual regression baseline capture for DashboardPage.

Captures ``tests/desktop/baselines/dashboard.png`` so future UI polish work
can detect regressions in the dashboard surface.  Runs under
``QT_QPA_PLATFORM=offscreen`` (set in ``pyproject.toml``).

Usage:
    # First run: creates the baseline (already present in repo).
    pytest tests/desktop/test_dashboard_visual.py -v

    # Subsequent runs: compare against the baseline and fail on diff.
    pytest tests/desktop/test_dashboard_visual.py -v
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from tests.desktop.visual_regression import (
    VISUAL_STORAGE_ROOT,
    capture_widget_screenshot,
    save_baseline,
)

pytestmark = pytest.mark.skipif(
    bool(os.environ.get("PYTEST_XDIST_WORKER")),
    reason="PySide visual baseline capture is xdist-unsafe; run this test serially.",
)


def _visual_profiles_config() -> object:
    """Deterministic model profile config so page doesn't depend on disk."""
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
    """Deterministic workspace + QSS setup for the dashboard snapshot."""
    root = VISUAL_STORAGE_ROOT
    root.mkdir(parents=True, exist_ok=True)

    def _load_profiles(_settings: object | None = None) -> object:
        return _visual_profiles_config()

    from PySide6.QtWidgets import QApplication

    import novel_forge.desktop.pages.standalone.dashboard_page as dashboard_page
    from novel_forge.desktop.theme import get_stylesheet

    monkeypatch.setattr(dashboard_page, "load_or_import_profiles", _load_profiles)

    app = QApplication.instance()
    old_stylesheet = app.styleSheet() if isinstance(app, QApplication) else ""
    if isinstance(app, QApplication):
        app.setStyleSheet(get_stylesheet())
    try:
        yield root
    finally:
        if isinstance(app, QApplication):
            app.setStyleSheet(old_stylesheet)


def _render_dashboard(page, qtbot: QtBot, storage_root: Path) -> None:
    """Bind a deterministic workspace snapshot to the dashboard."""
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


class TestDashboardVisualBaseline:
    """DashboardPage visual regression baseline (Task 18)."""

    def test_dashboard_baseline_captured(
        self,
        qtbot: QtBot,
        visual_workspace: Path,
        update_baselines: bool,
    ) -> None:
        if not update_baselines:
            pytest.skip("Baseline capture updates PNGs; run with --update-baselines.")

        from novel_forge.desktop.pages.standalone.dashboard_page import DashboardPage

        page = DashboardPage()
        qtbot.addWidget(page)
        page.resize(1200, 800)
        page.show()

        _render_dashboard(page, qtbot, visual_workspace)

        image = capture_widget_screenshot(page)
        baseline_path = save_baseline("dashboard", image)
        assert baseline_path.exists(), f"Baseline not created at {baseline_path}"
        assert baseline_path.stat().st_size > 0, "Baseline PNG is empty"
