"""Tests: hidden page skip-binding with dirty flag + bind on switch_page.

Wave 2 / Task 8 — only the visible page is bound during workspace refresh;
hidden pages are added to ``_pending_rebind_pages`` and bound lazily when
the user navigates to them via ``switch_page``.

Auto-pilot special case: chapter_studio is bound immediately (not deferred)
when auto-pilot is running, because it drives automated job dispatch.
"""

from __future__ import annotations

import time
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

# ---------------------------------------------------------------------------
# Helpers (reuse pattern from test_chapter_studio_section_binding.py)
# ---------------------------------------------------------------------------


def _drain_deferred_switch(qapp: QApplication, duration_ms: int = 30) -> None:
    deadline = time.perf_counter() + duration_ms / 1000
    while time.perf_counter() < deadline:
        qapp.processEvents()
        time.sleep(0.001)
    qapp.processEvents()


def _build_snapshot(*, with_project: bool = True) -> DesktopWorkspaceSnapshot:
    """Build a minimal DesktopWorkspaceSnapshot for testing."""
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

    def get_chapter_workspace_snapshot(self, *args: object, **kwargs: object) -> None:
        return None

    def delete_project(self, *args: object, **kwargs: object) -> bool:
        return True


def _make_window(
    monkeypatch: pytest.MonkeyPatch,
) -> NovelForgeDesktopWindow:
    """Create a DesktopWindow with session restore disabled and timer stopped."""
    monkeypatch.setattr(
        NovelForgeDesktopWindow,
        "_load_ui_session",
        lambda self: None,
    )
    window = NovelForgeDesktopWindow()
    window._refresh_timer.stop()
    return window


def _setup_non_first_snapshot(
    window: NovelForgeDesktopWindow,
    base_snapshot: DesktopWorkspaceSnapshot,
) -> None:
    """Configure window state so the next refresh is NOT first_snapshot."""
    window._last_snapshot_payload = window._build_snapshot_payload(base_snapshot)
    window._section_hash_cache = {}
    window._snapshot = base_snapshot
    window._workspace_revision = 1


def _spy_bind(
    window: NovelForgeDesktopWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    """Install a spy on _bind_workspace_for_page; returns call log."""
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
    return bind_calls


def _refresh_with_changed_sections(
    window: NovelForgeDesktopWindow,
    base_snapshot: DesktopWorkspaceSnapshot,
    *,
    update_kwargs: dict[str, object] | None = None,
) -> None:
    """Build a new snapshot with given changes and trigger _on_workspace_refreshed."""
    if update_kwargs:
        new_snapshot = replace(base_snapshot, **update_kwargs)
    else:
        new_snapshot = base_snapshot

    payload = NovelForgeDesktopWindow._build_snapshot_payload_static(new_snapshot)
    section_hashes, changed_sections, final_hash = NovelForgeDesktopWindow._compute_section_hashes(
        new_snapshot,
        payload,
        prev_payload=window._last_snapshot_payload,
        prev_section_hash_cache=window._section_hash_cache,
    )
    window._on_workspace_refreshed(
        new_snapshot,
        _FakeWorkspaceService(new_snapshot),
        payload,
        section_hashes,
        changed_sections,
        final_hash,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHiddenPageSkipBinding:
    """Hidden pages should NOT be bound during workspace refresh."""

    def test_hidden_page_not_bound_on_refresh(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """When dashboard is visible and projects change, projects page NOT bound.

        Instead, 'projects' should appear in _pending_rebind_pages.
        """
        window = _make_window(monkeypatch)
        base_snapshot = _build_snapshot()
        _setup_non_first_snapshot(window, base_snapshot)

        # Dashboard is the default visible page
        assert window._current_page_id() == "dashboard"

        bind_calls = _spy_bind(window, monkeypatch)

        # Change projects section
        new_project_item = replace(base_snapshot.projects[0], title="已修改")
        _refresh_with_changed_sections(
            window,
            base_snapshot,
            update_kwargs={"projects": [new_project_item]},
        )

        # projects page should NOT be directly bound (it's hidden)
        assert "projects" not in bind_calls, (
            f"Hidden 'projects' page should NOT be bound directly. Bound pages: {bind_calls}"
        )

        # projects should be in the pending rebind set
        assert "projects" in window._pending_rebind_pages, (
            f"'projects' should be in _pending_rebind_pages. "
            f"Pending: {window._pending_rebind_pages}"
        )

    def test_visible_page_still_bound(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """The visible page (dashboard) IS bound even when other sections change."""
        window = _make_window(monkeypatch)
        base_snapshot = _build_snapshot()
        _setup_non_first_snapshot(window, base_snapshot)

        assert window._current_page_id() == "dashboard"

        bind_calls = _spy_bind(window, monkeypatch)

        # Change projects section (dashboard subscribes to 'projects' via
        # _PAGE_SECTION_MAP: dashboard → {projects, metrics, overview, ...})
        new_project_item = replace(base_snapshot.projects[0], title="已修改")
        _refresh_with_changed_sections(
            window,
            base_snapshot,
            update_kwargs={"projects": [new_project_item]},
        )

        # dashboard IS the visible page and subscribes to 'projects' → bound
        assert "dashboard" in bind_calls, (
            f"Visible 'dashboard' should be bound. Bound pages: {bind_calls}"
        )


class TestSwitchPageBinding:
    """switch_page should bind pending pages before showing them."""

    def test_switch_page_binds_pending(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """Switching to a pending page binds it and clears from pending."""
        window = _make_window(monkeypatch)
        base_snapshot = _build_snapshot()
        _setup_non_first_snapshot(window, base_snapshot)

        # Simulate a refresh that defers 'projects' to pending
        new_project_item = replace(base_snapshot.projects[0], title="已修改")
        _refresh_with_changed_sections(
            window,
            base_snapshot,
            update_kwargs={"projects": [new_project_item]},
        )

        assert "projects" in window._pending_rebind_pages
        assert window._pending_rebind_sections.get("projects") == {"projects"}

        # Now spy on bind calls for the switch
        bind_calls = _spy_bind(window, monkeypatch)

        # v2: switch_page requires the target page to exist (page creation is
        # decoupled from switching). Create it without binding.
        page = window._ensure_page("projects", bind_workspace=False, bind_jobs=False)
        assert page is not None

        # Switch to projects page
        window.switch_page("projects")
        assert "projects" not in bind_calls
        _drain_deferred_switch(qapp)

        # projects should have been bound during switch
        assert "projects" in bind_calls, (
            f"'projects' should be bound on switch. Bound: {bind_calls}"
        )

        # projects should be removed from pending
        assert "projects" not in window._pending_rebind_pages, (
            f"'projects' should be cleared from pending after switch. "
            f"Pending: {window._pending_rebind_pages}"
        )

    def test_switch_page_no_pending_no_extra_bind(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """Switching to the already-active page with no pending work is a no-op."""
        window = _make_window(monkeypatch)

        # Ensure pending set is empty
        assert len(window._pending_rebind_pages) == 0

        bind_calls = _spy_bind(window, monkeypatch)

        # Switch to dashboard (not in pending)
        window.switch_page("dashboard")

        assert bind_calls == [], f"active dashboard switch should not bind. Bound: {bind_calls}"


class TestAutoPilotSpecialCase:
    """chapter_studio binds immediately when auto-pilot is running."""

    def test_chapter_studio_bound_immediately_during_auto_pilot(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """When auto-pilot is active, chapter_studio is bound even if hidden."""
        window = _make_window(monkeypatch)
        base_snapshot = _build_snapshot()
        _setup_non_first_snapshot(window, base_snapshot)

        # Simulate auto-pilot running
        window._autorun_driving_projects.add("test_novel")

        assert window._current_page_id() == "dashboard"

        bind_calls = _spy_bind(window, monkeypatch)

        # Change details section (chapter_studio subscribes to details)
        _old = base_snapshot.details["test_novel"]
        _new_idx = _old.index.model_copy(update={"completed_chapters": 5})
        new_detail = _old.model_copy(update={"completed_chapters": 5, "index": _new_idx})
        _refresh_with_changed_sections(
            window,
            base_snapshot,
            update_kwargs={"details": {"test_novel": new_detail}},
        )

        # chapter_studio should be bound immediately (auto-pilot override)
        assert "chapter_studio" in bind_calls, (
            f"chapter_studio should be bound immediately during auto-pilot. Bound: {bind_calls}"
        )

        # chapter_studio should NOT be in pending (it was bound directly)
        assert "chapter_studio" not in window._pending_rebind_pages, (
            f"chapter_studio should NOT be pending when auto-pilot is active. "
            f"Pending: {window._pending_rebind_pages}"
        )

    def test_chapter_studio_deferred_without_auto_pilot(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """Without auto-pilot, chapter_studio is deferred when hidden."""
        window = _make_window(monkeypatch)
        base_snapshot = _build_snapshot()
        _setup_non_first_snapshot(window, base_snapshot)

        # Ensure auto-pilot is NOT running
        assert len(window._autorun_driving_projects) == 0
        assert window._current_page_id() == "dashboard"

        bind_calls = _spy_bind(window, monkeypatch)

        # Change details section
        _old = base_snapshot.details["test_novel"]
        _new_idx = _old.index.model_copy(update={"completed_chapters": 5})
        new_detail = _old.model_copy(update={"completed_chapters": 5, "index": _new_idx})
        _refresh_with_changed_sections(
            window,
            base_snapshot,
            update_kwargs={"details": {"test_novel": new_detail}},
        )

        # chapter_studio should NOT be bound (hidden, no auto-pilot)
        assert "chapter_studio" not in bind_calls, (
            f"chapter_studio should NOT be bound without auto-pilot. Bound: {bind_calls}"
        )

        # chapter_studio should be in pending
        assert "chapter_studio" in window._pending_rebind_pages, (
            f"chapter_studio should be pending. Pending: {window._pending_rebind_pages}"
        )


class TestPendingCleanup:
    """Pending sections stay precise until the hidden page is shown."""

    def test_pending_preserved_on_unrelated_refresh(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """Unrelated refreshes must not drop a hidden page's pending section."""
        window = _make_window(monkeypatch)
        base_snapshot = _build_snapshot()
        _setup_non_first_snapshot(window, base_snapshot)

        # Step 1: Change projects → 'projects' page goes to pending
        new_project_item = replace(base_snapshot.projects[0], title="已修改")
        _refresh_with_changed_sections(
            window,
            base_snapshot,
            update_kwargs={"projects": [new_project_item]},
        )
        assert "projects" in window._pending_rebind_pages

        # Step 2: Change only metrics → projects NOT affected
        # Need to update the base to the current snapshot state
        current_snapshot = window._snapshot
        assert current_snapshot is not None
        new_metrics = replace(current_snapshot.metrics, total_words=99999)
        _refresh_with_changed_sections(
            window,
            current_snapshot,
            update_kwargs={"metrics": new_metrics},
        )

        # 'projects' should remain pending so it can replay the earlier projects change.
        assert "projects" in window._pending_rebind_pages, (
            f"'projects' should remain pending after metrics-only refresh. "
            f"Pending: {window._pending_rebind_pages}"
        )
        assert window._pending_rebind_sections.get("projects") == {"projects"}

        bind_calls = _spy_bind(window, monkeypatch)
        # v2: switch_page requires the target page to exist (page creation is
        # decoupled from switching). Create it without binding.
        page = window._ensure_page("projects", bind_workspace=False, bind_jobs=False)
        assert page is not None
        window.switch_page("projects")
        _drain_deferred_switch(qapp)

        assert "projects" in bind_calls, f"'projects' should bind once shown. Bound: {bind_calls}"
        assert "projects" not in window._pending_rebind_pages, (
            f"'projects' should clear pending after deferred bind. "
            f"Pending: {window._pending_rebind_pages}"
        )


class TestPendingRebindAttributeExists:
    """Source-level guard: _pending_rebind_pages attribute exists in __init__."""

    def test_init_has_pending_rebind_pages(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """DesktopWindow.__init__ sets _pending_rebind_pages as an empty set."""
        window = _make_window(monkeypatch)
        assert hasattr(window, "_pending_rebind_pages")
        assert isinstance(window._pending_rebind_pages, set)
        assert hasattr(window, "_pending_rebind_sections")
        assert isinstance(window._pending_rebind_sections, dict)
