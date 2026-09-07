"""Tests: chapter_studio section-based binding respects _PAGE_SECTION_MAP.

Verifies that chapter_studio is only rebound when its relevant sections
(projects, details) change — not unconditionally via the hardcoded
override at what was window.py:1414-1415.

After the fix, chapter_studio relies on _PAGE_SECTION_MAP like every
other page. The dirty-flag pattern (Wave 2 / Task 8) handles stale
context for non-visible pages.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from novel_forge.desktop.window import NovelForgeDesktopWindow
from novel_forge.desktop.workspace import (
    DesktopProjectItem,
    DesktopWorkspaceMetrics,
    DesktopWorkspaceSnapshot,
    ProviderStatus,
    WorkspaceOverview,
)


def _build_snapshot(*, with_project: bool) -> DesktopWorkspaceSnapshot:
    """Build a minimal DesktopWorkspaceSnapshot for testing.

    Uses the real types: projects=list[DesktopProjectItem] (frozen dataclass)
    and details=dict[str, ProjectDetail] (Pydantic model).
    """
    from novel_forge.workspace.projects import ProjectDetail

    storage_root = Path("/tmp/novel_forge_test")
    providers = [
        ProviderStatus(
            provider_id="mock",
            label="Mock",
            ready=True,
            configured=True,
            is_default=True,
            detail="当前已载入",
        ),
    ]

    if not with_project:
        overview = WorkspaceOverview(
            storage_root=str(storage_root),
            total_projects=0,
            short_projects=0,
            long_projects=0,
            total_generated_chapters=0,
            providers=["mock"],
            default_provider="mock",
        )
        metrics = DesktopWorkspaceMetrics(
            total_projects=0,
            total_chapters=0,
            total_words=0,
            configured_providers=1,
        )
        return DesktopWorkspaceSnapshot(
            storage_root=storage_root,
            default_provider="mock",
            overview=overview,
            metrics=metrics,
            providers=providers,
            projects=[],
            details={},
            featured_project=None,
        )

    # DesktopProjectItem is a frozen dataclass used for snapshot.projects
    project_item = DesktopProjectItem(
            project_id="test_novel",
            title="测试小说",
            mode="long",
            mode_label="长篇",
        genre="",
        tone="",
        completed_chapters=2,
        total_chapters=10,
        next_chapter=3,
        has_outline=True,
        has_canon=False,
        init_resume_available=False,
        init_resume_step_label="",
        project_state="active",
        project_state_label="",
        allowed_operations=(),
        status="active",
        status_label="进行中",
        progress_label="2/10",
        progress_percent=20,
        last_updated_label="刚刚",
        headline="测试小说 - 第3章待续写",
        next_action="续写第3章",
    )

    # ProjectDetail is a Pydantic model used for snapshot.details
    project_detail = ProjectDetail(
        project_id="test_novel",
        title="测试小说",
        mode="long",
        total_chapters=10,
        completed_chapters=2,
        chapters=[],
        recent_files=[],
        artifact_counts={},
    )

    overview = WorkspaceOverview(
        storage_root=str(storage_root),
        total_projects=1,
        short_projects=0,
        long_projects=1,
        total_generated_chapters=2,
        providers=["mock"],
        default_provider="mock",
    )
    metrics = DesktopWorkspaceMetrics(
        total_projects=1,
        total_chapters=2,
        total_words=5000,
        configured_providers=1,
    )
    return DesktopWorkspaceSnapshot(
        storage_root=storage_root,
        default_provider="mock",
        overview=overview,
        metrics=metrics,
        providers=providers,
        projects=[project_item],
        details={"test_novel": project_detail},
        featured_project=project_item,
    )


class _FakeWorkspaceService:
    """Minimal service stub for _on_workspace_refreshed."""

    def __init__(self, snapshot: DesktopWorkspaceSnapshot) -> None:
        self._snapshot = snapshot

    def build_snapshot(self) -> DesktopWorkspaceSnapshot:
        return self._snapshot

    def get_chapter_workspace_snapshot(
        self, *args: object, **kwargs: object
    ) -> None:
        return None

    def delete_project(self, *args: object, **kwargs: object) -> bool:
        return True


class TestChapterStudioSectionBinding:
    """Tests for chapter_studio section-based binding."""

    # ── RED-phase test: fails while the hardcoded override exists ─────────

    def test_chapter_studio_not_bound_when_only_metrics_change(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """chapter_studio.bind_workspace NOT called when only metrics change.

        This test FAILS (RED) while the hardcoded override at window.py:1414-1415
        exists, because it binds chapter_studio unconditionally regardless of
        _pages_needing_bind. After removing the override, it PASSES (GREEN).
        """
        monkeypatch.setattr(
            NovelForgeDesktopWindow,
            "_load_ui_session",
            lambda self: None,
        )

        window = NovelForgeDesktopWindow()
        window._refresh_timer.stop()

        # Set up initial state so the next refresh is NOT first_snapshot
        base_snapshot = _build_snapshot(with_project=True)
        window._last_snapshot_payload = window._build_snapshot_payload(base_snapshot)
        window._section_hash_cache = {}
        window._snapshot = base_snapshot
        window._workspace_revision = 1

        # Spy on _bind_workspace_for_page
        bind_calls: list[str] = []
        original_bind = window._bind_workspace_for_page

        def spy_bind(
            page_id: str,
            *,
            force: bool = False,
            sections: frozenset[str] | None = None,
        ) -> None:
            bind_calls.append(page_id)
            original_bind(page_id, force=force, sections=sections)

        monkeypatch.setattr(window, "_bind_workspace_for_page", spy_bind)

        # Build snapshot with only metrics changed (frozen dataclass → replace)
        new_metrics = replace(
            base_snapshot.metrics,
            total_words=9999,
            total_chapters=42,
        )
        new_snapshot = replace(base_snapshot, metrics=new_metrics)

        payload = NovelForgeDesktopWindow._build_snapshot_payload_static(new_snapshot)
        section_hashes, changed_sections, final_hash = (
            NovelForgeDesktopWindow._compute_section_hashes(
                new_snapshot,
                payload,
                prev_payload=window._last_snapshot_payload,
                prev_section_hash_cache=window._section_hash_cache,
            )
        )
        window._on_workspace_refreshed(
            new_snapshot,
            _FakeWorkspaceService(new_snapshot),
            payload,
            section_hashes,
            changed_sections,
            final_hash,
        )

        # chapter_studio should NOT be bound — it doesn't subscribe to "metrics"
        assert "chapter_studio" not in bind_calls, (
            f"chapter_studio was incorrectly bound when only metrics changed. "
            f"Bound pages: {bind_calls}"
        )

    # ── Positive tests: chapter_studio IS bound for relevant sections ─────

    def test_chapter_studio_bound_when_projects_change(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """chapter_studio IS bound when projects section changes and it is visible."""
        monkeypatch.setattr(
            NovelForgeDesktopWindow,
            "_load_ui_session",
            lambda self: None,
        )

        window = NovelForgeDesktopWindow()
        window._refresh_timer.stop()

        base_snapshot = _build_snapshot(with_project=True)
        window._last_snapshot_payload = window._build_snapshot_payload(base_snapshot)
        window._section_hash_cache = {}
        window._snapshot = base_snapshot
        window._workspace_revision = 1

        # Navigate to chapter_studio so it is the visible page.
        # With the dirty-flag pattern (Wave 2 / Task 8), only the visible page
        # is bound immediately; hidden pages are deferred to _pending_rebind_pages.
        window.switch_page("chapter_studio")

        bind_calls: list[str] = []
        original_bind = window._bind_workspace_for_page

        def spy_bind(
            page_id: str,
            *,
            force: bool = False,
            sections: frozenset[str] | None = None,
        ) -> None:
            bind_calls.append(page_id)
            original_bind(page_id, force=force, sections=sections)

        monkeypatch.setattr(window, "_bind_workspace_for_page", spy_bind)

        # Only change projects list — keep details identical
        new_project_item = replace(
            base_snapshot.projects[0],
            title="已修改",
        )
        new_snapshot = replace(base_snapshot, projects=[new_project_item])

        payload = NovelForgeDesktopWindow._build_snapshot_payload_static(new_snapshot)
        section_hashes, changed_sections, final_hash = (
            NovelForgeDesktopWindow._compute_section_hashes(
                new_snapshot,
                payload,
                prev_payload=window._last_snapshot_payload,
                prev_section_hash_cache=window._section_hash_cache,
            )
        )
        window._on_workspace_refreshed(
            new_snapshot,
            _FakeWorkspaceService(new_snapshot),
            payload,
            section_hashes,
            changed_sections,
            final_hash,
        )

        assert "chapter_studio" in bind_calls, (
            f"chapter_studio should be bound when projects change. "
            f"Bound pages: {bind_calls}"
        )

    def test_chapter_studio_bound_when_details_change(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """chapter_studio IS bound when details section changes and it is visible."""
        monkeypatch.setattr(
            NovelForgeDesktopWindow,
            "_load_ui_session",
            lambda self: None,
        )

        window = NovelForgeDesktopWindow()
        window._refresh_timer.stop()

        base_snapshot = _build_snapshot(with_project=True)
        window._last_snapshot_payload = window._build_snapshot_payload(base_snapshot)
        window._section_hash_cache = {}
        window._snapshot = base_snapshot
        window._workspace_revision = 1

        # Navigate to chapter_studio so it is the visible page (see test above).
        window.switch_page("chapter_studio")

        bind_calls: list[str] = []
        original_bind = window._bind_workspace_for_page

        def spy_bind(
            page_id: str,
            *,
            force: bool = False,
            sections: frozenset[str] | None = None,
        ) -> None:
            bind_calls.append(page_id)
            original_bind(page_id, force=force, sections=sections)

        monkeypatch.setattr(window, "_bind_workspace_for_page", spy_bind)

        # Only change details — keep projects identical.
        # ProjectDetail is a Pydantic model so we use model_copy.
        _old = base_snapshot.details["test_novel"]
        _new_idx = _old.index.model_copy(update={"completed_chapters": 5})
        new_detail = _old.model_copy(
            update={"completed_chapters": 5, "index": _new_idx}
        )
        new_snapshot = replace(base_snapshot, details={"test_novel": new_detail})

        payload = NovelForgeDesktopWindow._build_snapshot_payload_static(new_snapshot)
        section_hashes, changed_sections, final_hash = (
            NovelForgeDesktopWindow._compute_section_hashes(
                new_snapshot,
                payload,
                prev_payload=window._last_snapshot_payload,
                prev_section_hash_cache=window._section_hash_cache,
            )
        )
        window._on_workspace_refreshed(
            new_snapshot,
            _FakeWorkspaceService(new_snapshot),
            payload,
            section_hashes,
            changed_sections,
            final_hash,
        )

        assert "chapter_studio" in bind_calls, (
            f"chapter_studio should be bound when details change. "
            f"Bound pages: {bind_calls}"
        )

    def test_pages_needing_bind_excludes_chapter_studio_for_metrics(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """_pages_needing_bind unit-style: metrics-only does not include chapter_studio."""
        monkeypatch.setattr(
            NovelForgeDesktopWindow,
            "_load_ui_session",
            lambda self: None,
        )

        window = NovelForgeDesktopWindow()
        window._refresh_timer.stop()

        pages = window._pages_needing_bind({"metrics"}, "dashboard", initial=False)
        assert "chapter_studio" not in pages, (
            f"chapter_studio should not need bind on metrics-only change: {pages}"
        )

    def test_pages_needing_bind_includes_chapter_studio_for_projects(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """_pages_needing_bind unit-style: projects change includes chapter_studio."""
        monkeypatch.setattr(
            NovelForgeDesktopWindow,
            "_load_ui_session",
            lambda self: None,
        )

        window = NovelForgeDesktopWindow()
        window._refresh_timer.stop()

        pages = window._pages_needing_bind({"projects"}, "dashboard", initial=False)
        assert "chapter_studio" in pages, (
            f"chapter_studio should need bind on projects change: {pages}"
        )

    def test_pages_needing_bind_includes_chapter_studio_for_details(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """_pages_needing_bind unit-style: details change includes chapter_studio."""
        monkeypatch.setattr(
            NovelForgeDesktopWindow,
            "_load_ui_session",
            lambda self: None,
        )

        window = NovelForgeDesktopWindow()
        window._refresh_timer.stop()

        pages = window._pages_needing_bind({"details"}, "dashboard", initial=False)
        assert "chapter_studio" in pages, (
            f"chapter_studio should need bind on details change: {pages}"
        )
