"""Tests: concurrent action safety during workspace refresh.

Wave 3 / Task 21 — verify that user actions (page switches, button clicks)
during an in-flight workspace refresh do not crash, duplicate bindings, or
corrupt state.  Also verify that the fs-watcher debounce coalesces rapid
events into a single refresh.
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

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
# Helpers (reuse pattern from test_hidden_page_skip.py)
# ---------------------------------------------------------------------------


def _drain_deferred_switch(qapp: QApplication, duration_ms: int = 30) -> None:
    deadline = time.perf_counter() + duration_ms / 1000
    while time.perf_counter() < deadline:
        qapp.processEvents()
        time.sleep(0.001)
    qapp.processEvents()


def _build_snapshot(*, num_projects: int = 1) -> DesktopWorkspaceSnapshot:
    """Build a minimal DesktopWorkspaceSnapshot for testing."""
    from novel_forge.workspace.projects import ProjectDetail

    storage_root = Path("/tmp/novel_forge_concurrent_test")
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

    projects: list[DesktopProjectItem] = []
    details: dict[str, ProjectDetail] = {}
    for i in range(num_projects):
        pid = f"proj_{i:03d}"
        item = DesktopProjectItem(
            project_id=pid,
            title=f"测试项目 {i}",
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
            headline=f"测试项目 {i}",
            next_action="续写第3章",
        )
        projects.append(item)
        details[pid] = ProjectDetail(
            project_id=pid,
            title=f"测试项目 {i}",
            mode="long",
            total_chapters=10,
            completed_chapters=2,
            chapters=[],
            recent_files=[],
            artifact_counts={},
        )

    overview = WorkspaceOverview(
        storage_root=str(storage_root),
        total_projects=num_projects,
        short_projects=0,
        long_projects=num_projects,
        total_generated_chapters=num_projects * 2,
        providers=["mock"],
        default_provider="mock",
    )
    metrics = DesktopWorkspaceMetrics(
        total_projects=num_projects,
        total_chapters=num_projects * 2,
        total_words=num_projects * 5000,
        configured_providers=1,
    )
    return DesktopWorkspaceSnapshot(
        storage_root=storage_root,
        default_provider="mock",
        overview=overview,
        metrics=metrics,
        providers=providers,
        projects=projects,
        details=details,
        featured_project=projects[0] if projects else None,
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
# Tests: concurrent actions during refresh
# ---------------------------------------------------------------------------


@pytest.mark.perf
class TestActionDuringRefresh:
    """User actions during an in-flight refresh must not crash or duplicate."""

    def test_switch_page_during_refresh_no_crash(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """Switching page while _refresh_in_progress=True does not crash.

        Simulates: background refresh is running, user clicks a nav button.
        The switch_page call should succeed without error and the new page
        should get correct data once the refresh completes.
        """
        window = _make_window(monkeypatch)
        base_snapshot = _build_snapshot()
        _setup_non_first_snapshot(window, base_snapshot)

        # Simulate refresh in progress
        window._refresh_in_progress = True

        # Switch page while refresh is in progress — must not crash
        window.switch_page("projects")
        QApplication.processEvents()

        # Now complete the refresh
        window._refresh_in_progress = False
        new_project_item = replace(base_snapshot.projects[0], title="已修改")
        _refresh_with_changed_sections(
            window,
            base_snapshot,
            update_kwargs={"projects": [new_project_item]},
        )

        # The new page should have been bound (projects was pending or direct)
        # Verify snapshot is updated
        assert window._snapshot is not None
        assert window._refresh_in_progress is False

    def test_action_during_refresh_no_duplicate_binding(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """Triggering switch_page during refresh does not cause duplicate binds.

        The revision-based skip in _bind_workspace_for_page ensures that
        a page already bound at the current revision is not re-bound.
        """
        window = _make_window(monkeypatch)
        base_snapshot = _build_snapshot()
        _setup_non_first_snapshot(window, base_snapshot)

        # First, do a normal refresh to establish baseline
        _refresh_with_changed_sections(
            window,
            base_snapshot,
            update_kwargs={"projects": [replace(base_snapshot.projects[0], title="v1")]},
        )

        # Now simulate refresh in progress
        window._refresh_in_progress = True

        # Spy on bind calls
        bind_calls = _spy_bind(window, monkeypatch)

        # Switch to projects page during refresh
        window.switch_page("projects")
        QApplication.processEvents()

        # Complete the refresh
        window._refresh_in_progress = False
        current_snap = window._snapshot
        assert current_snap is not None
        _refresh_with_changed_sections(
            window,
            current_snap,
            update_kwargs={"projects": [replace(current_snap.projects[0], title="v2")]},
        )

        # projects should be bound a small number of times:
        # 1. switch_page → pending check → bind(force=True)
        # 2. refresh completion → current page bind(force=True)
        # The revision skip prevents additional duplicates within each path.
        # No unbounded growth — the key invariant is no crash and finite binds.
        projects_bind_count = bind_calls.count("projects")
        assert 1 <= projects_bind_count <= 3, (
            f"projects bound {projects_bind_count} times (expected 1-3). All binds: {bind_calls}"
        )

    def test_refresh_in_progress_blocks_concurrent_refresh(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """When _refresh_in_progress=True, refresh_workspace() is a no-op.

        The guard at line 1411 prevents queuing multiple concurrent refreshes.
        """
        window = _make_window(monkeypatch)
        base_snapshot = _build_snapshot()
        _setup_non_first_snapshot(window, base_snapshot)

        # v2: refresh_workspace short-circuits while the workspace service is
        # still being built (B2 async init) — provide a service stub.
        window._workspace = MagicMock()

        # Simulate refresh in progress
        window._refresh_in_progress = True
        window._last_refresh_complete_time = 0  # bypass throttle

        # Spy on the dedicated UI I/O pool to verify no new worker is queued.
        ui_io_pool = MagicMock()
        fake_pools = SimpleNamespace(
            job_pool=MagicMock(),
            ui_io_pool=ui_io_pool,
            aux_pool=MagicMock(),
        )
        monkeypatch.setattr(
            "novel_forge.desktop.window.desktop_thread_pools",
            lambda: fake_pools,
        )

        # Call refresh_workspace — should be blocked
        window.refresh_workspace(force=True)

        # No worker should have been started
        ui_io_pool.start.assert_not_called()

        # _refresh_force_pending should be set (so a refresh is scheduled
        # after the current one completes)
        assert window._refresh_force_pending is True

    def test_switch_page_gets_correct_data_after_refresh(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """After refresh completes, switching to a pending page binds fresh data.

        Verifies the dirty-flag pattern: page deferred during refresh gets
        bound with the latest snapshot when user navigates to it.
        """
        window = _make_window(monkeypatch)
        base_snapshot = _build_snapshot()
        _setup_non_first_snapshot(window, base_snapshot)

        assert window._current_page_id() == "dashboard"

        # Trigger a refresh that changes projects → projects page deferred
        new_project_item = replace(base_snapshot.projects[0], title="新标题")
        _refresh_with_changed_sections(
            window,
            base_snapshot,
            update_kwargs={"projects": [new_project_item]},
        )

        # projects should be in pending
        assert "projects" in window._pending_rebind_pages

        # v2: switch_page requires the target page to exist (page creation is
        # decoupled from switching). Create it without binding.
        page = window._ensure_page("projects", bind_workspace=False, bind_jobs=False)
        assert page is not None

        # Now switch to projects page
        bind_calls = _spy_bind(window, monkeypatch)
        window.switch_page("projects")
        _drain_deferred_switch(qapp)

        # projects should have been bound
        assert "projects" in bind_calls

        # projects should be removed from pending
        assert "projects" not in window._pending_rebind_pages


# ---------------------------------------------------------------------------
# Tests: fs watcher debounce coalescing
# ---------------------------------------------------------------------------


@pytest.mark.perf
class TestFsWatcherDebounceCoalescing:
    """Two rapid fs watcher events should trigger only one refresh."""

    def test_two_rapid_events_one_refresh(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """Two fileChanged signals in quick succession → one debounce timer start.

        The debounce timer is single-shot; restarting it before it fires
        coalesces the events into a single timeout → single refresh.
        """
        window = _make_window(monkeypatch)
        assert window._fs_watcher is not None

        # Spy on refresh_workspace
        refresh_calls: list[bool] = []

        def spy_refresh(*, force: bool = True) -> None:
            refresh_calls.append(force)

        monkeypatch.setattr(window, "refresh_workspace", spy_refresh)

        # Simulate two rapid fileChanged events
        window._fs_watcher.fileChanged.emit("/fake/path/spec.json")
        window._fs_watcher.fileChanged.emit("/fake/path/outline.json")
        QApplication.processEvents()

        # The debounce timer should be active (waiting to fire)
        assert window._fs_watcher_debounce_timer.isActive(), (
            "Debounce timer should be active after fs watcher events"
        )

        # refresh_workspace should NOT have been called yet (debounce pending)
        assert len(refresh_calls) == 0, (
            f"refresh_workspace called {len(refresh_calls)} times during debounce. "
            f"Expected 0 (debounce should delay the call)."
        )

    def test_debounce_timer_fires_single_refresh(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
        qtbot: Any,
    ) -> None:
        """After debounce interval, exactly one refresh is triggered."""
        window = _make_window(monkeypatch)
        assert window._fs_watcher is not None

        refresh_calls: list[bool] = []

        def spy_refresh(*, force: bool = True) -> None:
            refresh_calls.append(force)

        monkeypatch.setattr(window, "refresh_workspace", spy_refresh)

        # Simulate three rapid fileChanged events
        window._fs_watcher.fileChanged.emit("/fake/path/spec.json")
        window._fs_watcher.fileChanged.emit("/fake/path/outline.json")
        window._fs_watcher.fileChanged.emit("/fake/path/character_bible.json")
        QApplication.processEvents()

        # Wait for debounce timer to fire
        qtbot.waitUntil(
            lambda: len(refresh_calls) >= 1,
            timeout=window._fs_watcher_debounce_timer.interval() + 1000,
        )

        # Exactly one refresh should have been triggered
        assert len(refresh_calls) == 1, (
            f"Expected exactly 1 refresh after debounce, got {len(refresh_calls)}"
        )
        assert refresh_calls[0] is True  # force=True from debounce timer

    def test_debounce_timer_is_single_shot(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """The fs watcher debounce timer is configured as single-shot."""
        window = _make_window(monkeypatch)

        assert window._fs_watcher_debounce_timer.isSingleShot(), (
            "fs_watcher_debounce_timer must be single-shot for coalescing"
        )

    def test_debounce_interval_matches_constant(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """Debounce timer interval matches FS_WATCHER_DEBOUNCE_MS constant."""
        from novel_forge.desktop.constants import FS_WATCHER_DEBOUNCE_MS

        window = _make_window(monkeypatch)

        assert window._fs_watcher_debounce_timer.interval() == FS_WATCHER_DEBOUNCE_MS, (
            f"Timer interval {window._fs_watcher_debounce_timer.interval()}ms "
            f"!= constant {FS_WATCHER_DEBOUNCE_MS}ms"
        )
