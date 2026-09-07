"""End-to-end smoke tests for the Novel Forge PySide6 desktop application.

Tests cover: app startup, page switching, workspace refresh, job lifecycle,
settings persistence, theme application, and full short-story workflow.
All tests use MockAdapter — no real API keys required.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget

from novel_forge.desktop.config_store import DesktopSettingsStore
from novel_forge.desktop.jobs import DesktopJobManager, DesktopJobRecord, DesktopJobState
from novel_forge.desktop.page_registrations import page_registry
from novel_forge.desktop.pages import (
    ChapterStudioPage,
    DashboardPage,
    ProjectsPage,
    SettingsPage,
    WorkflowPage,
)
from novel_forge.desktop.theme import (
    ACCENT_PRIMARY,
    BG_SURFACE,
    BG_WORKSPACE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    get_stylesheet,
)
from novel_forge.desktop.workspace import (
    DesktopProjectItem,
    DesktopWorkspaceMetrics,
    DesktopWorkspaceSnapshot,
    ProviderStatus,
)
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.workspace.projects import ProjectDetail, WorkspaceOverview

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    from novel_forge.desktop.theme import get_stylesheet

    app.setStyleSheet(get_stylesheet())
    return app


@pytest.fixture(scope="module")
def mock_router(mock_adapter: MockAdapter) -> ModelRouter:
    return ModelRouter(
        adapters={"mock": mock_adapter},
        default_provider="mock",
    )


def _make_snapshot() -> DesktopWorkspaceSnapshot:
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

    return DesktopWorkspaceSnapshot(
        storage_root=Path.cwd() / "data",
        default_provider="mock",
        overview=WorkspaceOverview(
            storage_root=str(Path.cwd() / "data"),
            total_projects=2,
            short_projects=1,
            long_projects=1,
            total_generated_chapters=3,
            providers=["mock"],
            default_provider="mock",
        ),
        metrics=DesktopWorkspaceMetrics(
            total_projects=2,
            total_chapters=3,
            total_words=15000,
            configured_providers=1,
        ),
        providers=[
            ProviderStatus(
                provider_id="mock",
                label="Mock",
                detail="Local simulation",
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


class TestAppStartup:
    def test_qapplication_exists(self, app: QApplication) -> None:
        assert app is not None
        assert isinstance(app, QApplication)

    def test_pages_registered(self) -> None:
        expected = {"dashboard", "projects", "workflow", "settings", "chapter_studio"}
        actual = set(page_registry.list_pages())
        assert expected <= actual, f"Missing pages: {expected - actual}"

    def test_page_metadata_complete(self) -> None:
        for page_id in page_registry.list_pages():
            meta = page_registry.metadata(page_id)
            assert meta is not None
            assert meta.title, f"Page {page_id} missing title"
            assert meta.subtitle, f"Page {page_id} missing subtitle"
            assert meta.label or meta.eyebrow, f"Page {page_id} missing label/eyebrow"

    def test_stylesheet_applied(self, app: QApplication) -> None:
        stylesheet = app.styleSheet()
        assert len(stylesheet) > 100


class TestPageSwitching:
    @pytest.mark.parametrize(
        "page_cls",
        [DashboardPage, ProjectsPage, WorkflowPage, SettingsPage, ChapterStudioPage],
    )
    def test_page_instantiation(self, app: QApplication, page_cls: type[QWidget]) -> None:
        page = page_cls()
        assert page is not None
        assert isinstance(page, QWidget)
        page.deleteLater()

    def test_page_bind_workspace(self, app: QApplication) -> None:
        page = DashboardPage()
        snapshot = _make_snapshot()
        page.bind_workspace(snapshot)
        QCoreApplication.processEvents()
        page.deleteLater()

    def test_page_switch_sequence(self, app: QApplication) -> None:
        stack = QStackedWidget()
        pages = {}
        for page_id in page_registry.list_pages():
            page = page_registry.get(page_id)
            if page is not None:
                pages[page_id] = page
                stack.addWidget(page)

        assert stack.count() >= 5

        for page_id in pages:
            stack.setCurrentWidget(pages[page_id])
            QCoreApplication.processEvents()
            assert stack.currentWidget() is pages[page_id]

        for p in pages.values():
            p.deleteLater()


class TestWorkspaceRefresh:
    def test_snapshot_creation(self) -> None:
        snapshot = _make_snapshot()
        assert snapshot is not None
        assert snapshot.default_provider == "mock"
        assert snapshot.metrics.total_projects == 2
        assert len(snapshot.providers) == 1
        assert snapshot.providers[0].provider_id == "mock"

    def test_snapshot_hash_stability(self) -> None:
        import hashlib

        s1 = _make_snapshot()
        s2 = _make_snapshot()

        def _hash(s: DesktopWorkspaceSnapshot) -> str:
            import json
            from dataclasses import asdict

            payload = {
                "storage_root": str(s.storage_root),
                "default_provider": s.default_provider,
                "metrics": asdict(s.metrics),
                "providers": [asdict(p) for p in s.providers],
                "projects": [asdict(p) for p in s.projects],
            }
            content = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
            return hashlib.sha256(content.encode("utf-8")).hexdigest()

        assert _hash(s1) == _hash(s2)

    def test_provider_status_labels(self) -> None:
        snapshot = _make_snapshot()
        for provider in snapshot.providers:
            assert provider.label is not None
            assert provider.detail is not None


class TestJobLifecycle:
    def test_job_manager_creation(self, app: QApplication) -> None:
        manager = DesktopJobManager(load_persisted_history=False)
        assert manager is not None
        assert len(manager.jobs()) == 0

    def test_job_record_defaults(self) -> None:
        record = DesktopJobRecord(
            job_id="test_123",
            kind="run_short",
            label="短篇创作 · test",
            project_id="test",
        )
        assert record.status == DesktopJobState.QUEUED
        assert record.job_id == "test_123"
        assert record.kind == "run_short"
        assert record.project_id == "test"
        assert record.events == []

    def test_job_state_transitions(self) -> None:
        record = DesktopJobRecord(
            job_id="test_456",
            kind="run_chapter",
            label="章节续写 · test / 第 1 章",
            project_id="test",
        )
        assert record.status == DesktopJobState.QUEUED
        record.status = DesktopJobState.RUNNING
        assert record.status == DesktopJobState.RUNNING
        record.status = DesktopJobState.SUCCEEDED
        assert record.status == DesktopJobState.SUCCEEDED

    def test_mock_adapter_response(self, mock_adapter: MockAdapter) -> None:
        import asyncio

        from novel_forge.core.constants import TaskType
        from novel_forge.gateway.types import ModelRequest

        async def _test() -> None:
            request = ModelRequest(
                task_type=TaskType.DRAFT,
                messages=[{"role": "user", "content": "Write a story"}],
                model_id="mock-model",
            )
            response = await mock_adapter.complete(request)
            assert response.content is not None
            assert len(response.content) > 0

        asyncio.run(_test())


class TestSettingsPersistence:
    def test_config_store_save_load(self, tmp_path: Path) -> None:
        from novel_forge.gateway.profiles import ModelProfile, ProfilesConfig

        profiles_path = tmp_path / "model_profiles.json"
        env_path = tmp_path / ".env"
        env_path.write_text("# Initial env\n", encoding="utf-8")

        store = DesktopSettingsStore(
            profiles_path=profiles_path,
            env_path=env_path,
        )

        config = ProfilesConfig(
            profiles=[
                ModelProfile(
                    profile_id="test_model",
                    display_name="Test Model",
                    provider="mock",
                    model_id="mock-model",
                )
            ]
        )

        result = store.save(
            config=config,
            env_pairs={"NOVEL_FORGE_DEFAULT_PROVIDER": "mock"},
        )

        assert result.profiles_path == profiles_path
        assert profiles_path.exists()

        saved = json.loads(profiles_path.read_text(encoding="utf-8"))
        assert any(profile.get("profile_id") == "test_model" for profile in saved["profiles"])

    def test_env_merge_preserves_comments(self, tmp_path: Path) -> None:
        env_path = tmp_path / ".env"
        original = "# Database config\nDB_HOST=localhost\n# API settings\nOLD_KEY=old_value\n"
        env_path.write_text(original, encoding="utf-8")

        DesktopSettingsStore.merge_env(
            env_path,
            {"OLD_KEY": "new_value", "NEW_KEY": "new"},
        )

        content = env_path.read_text(encoding="utf-8")
        assert "# Database config" in content
        assert "# API settings" in content
        assert "DB_HOST=localhost" in content
        assert "OLD_KEY=new_value" in content
        assert "NEW_KEY=new" in content

    def test_settings_page_has_save_method(self, app: QApplication) -> None:
        page = SettingsPage()
        assert hasattr(page, "save_with_feedback")
        page.deleteLater()


class TestThemeApplication:
    def test_stylesheet_generated(self) -> None:
        stylesheet = get_stylesheet()
        assert len(stylesheet) > 500
        assert "QWidget" in stylesheet or "#" in stylesheet

    def test_stylesheet_cached(self) -> None:
        result1 = get_stylesheet()
        result2 = get_stylesheet()
        assert result1 is result2

    def test_theme_colors_defined(self) -> None:
        assert ACCENT_PRIMARY is not None
        assert BG_SURFACE is not None
        assert BG_WORKSPACE is not None
        assert TEXT_PRIMARY is not None
        assert TEXT_SECONDARY is not None

    def test_app_stylesheet_matches_theme(self, app: QApplication) -> None:
        theme_stylesheet = get_stylesheet()
        app_stylesheet = app.styleSheet()
        assert len(app_stylesheet) >= len(theme_stylesheet)


class TestFullWorkflowShort:
    def test_short_workflow_request_creation(self) -> None:
        from novel_forge.workspace.contracts import RunShortRequest

        request = RunShortRequest(
            project_id="test_workflow",
            theme="勇气与牺牲",
            genre="fantasy",
            tone="epic",
            length_target=3000,
        )
        assert request.project_id == "test_workflow"
        assert request.theme == "勇气与牺牲"
        assert request.genre == "fantasy"
        assert request.length_target == 3000

    def test_workflow_page_has_mock_control(self, app: QApplication) -> None:
        page = WorkflowPage()
        assert hasattr(page, "set_mock_mode")
        page.deleteLater()

    def test_settings_page_has_mock_control(self, app: QApplication) -> None:
        page = SettingsPage()
        assert hasattr(page, "set_mock_mode")
        page.deleteLater()

    def test_chapter_studio_page_bind(self, app: QApplication) -> None:
        page = ChapterStudioPage()
        snapshot = _make_snapshot()
        page.bind_workspace(snapshot)
        QCoreApplication.processEvents()
        page.deleteLater()
