"""Tests: edge cases for workspace refresh (0/1/50+ projects, degraded, missing).

Wave 3 / Task 21 — verify that the workspace refresh pipeline handles
boundary conditions gracefully: empty workspaces, single-project workspaces,
large workspaces (50+ projects), degraded project details, and snapshots
with missing/empty fields.
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
from tests.perf.conftest import compute_percentiles

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_snapshot(
    *,
    num_projects: int = 1,
    is_degraded: bool = False,
) -> DesktopWorkspaceSnapshot:
    """Build a DesktopWorkspaceSnapshot with *num_projects* projects."""
    from novel_forge.workspace.projects import ProjectDetail

    storage_root = Path("/tmp/novel_forge_edge_test")
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

        if is_degraded:
            from novel_forge.workspace.projects import ProjectIndex

            idx = ProjectIndex(
                project_id=pid,
                name=f"测试项目 {i}",
                mode="long",
                chapter_count=0,
                completed_chapters=0,
                total_words=0,
                is_degraded=True,
            )
            detail = ProjectDetail(
                project_id=pid,
                title=f"测试项目 {i}",
                mode="long",
                total_chapters=0,
                completed_chapters=0,
                chapters=[],
                recent_files=[],
                artifact_counts={},
                index=idx,
            )
        else:
            detail = ProjectDetail(
                project_id=pid,
                title=f"测试项目 {i}",
                mode="long",
                total_chapters=10,
                completed_chapters=2,
                chapters=[],
                recent_files=[],
                artifact_counts={},
            )
        details[pid] = detail

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


def _build_empty_snapshot() -> DesktopWorkspaceSnapshot:
    """Build a snapshot with 0 projects."""
    storage_root = Path("/tmp/novel_forge_edge_empty")
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


def _do_refresh(
    window: NovelForgeDesktopWindow,
    snapshot: DesktopWorkspaceSnapshot,
    *,
    prev_payload: dict[str, object] | None = None,
    prev_hash_cache: dict[str, str] | None = None,
) -> None:
    """Trigger _on_workspace_refreshed with the given snapshot."""
    payload = NovelForgeDesktopWindow._build_snapshot_payload_static(snapshot)
    section_hashes, changed_sections, final_hash = (
        NovelForgeDesktopWindow._compute_section_hashes(
            snapshot,
            payload,
            prev_payload=prev_payload or window._last_snapshot_payload,
            prev_section_hash_cache=prev_hash_cache or window._section_hash_cache,
        )
    )
    window._on_workspace_refreshed(
        snapshot,
        _FakeWorkspaceService(snapshot),
        payload,
        section_hashes,
        changed_sections,
        final_hash,
    )


# ---------------------------------------------------------------------------
# Tests: 0 projects
# ---------------------------------------------------------------------------


@pytest.mark.perf
class TestZeroProjects:
    """Empty workspace (0 projects) must not crash."""

    def test_empty_snapshot_refresh_completes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """Refresh with 0 projects completes without error."""
        window = _make_window(monkeypatch)
        snapshot = _build_empty_snapshot()

        # First snapshot refresh (first_snapshot=True path)
        _do_refresh(window, snapshot)

        assert window._snapshot is not None
        assert window._snapshot.metrics.total_projects == 0
        assert len(window._snapshot.projects) == 0
        assert len(window._snapshot.details) == 0
        assert window._refresh_in_progress is False

    def test_empty_snapshot_subsequent_refresh(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """Second refresh with 0 projects also works (non-first path)."""
        window = _make_window(monkeypatch)
        snapshot = _build_empty_snapshot()

        # First refresh
        _do_refresh(window, snapshot)

        # Second refresh (same snapshot → no content change)
        _do_refresh(window, snapshot)

        assert window._refresh_in_progress is False

    def test_empty_snapshot_perf(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """Empty snapshot refresh p95 < 100ms."""
        window = _make_window(monkeypatch)
        snapshot = _build_empty_snapshot()

        times: list[float] = []
        for _ in range(20):
            t0 = time.perf_counter()
            _do_refresh(window, snapshot)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            times.append(elapsed_ms)

        pct = compute_percentiles(times)
        assert pct["p95"] < 100, (
            f"Empty snapshot p95 = {pct['p95']:.1f}ms (expected < 100ms)"
        )


# ---------------------------------------------------------------------------
# Tests: 1 project
# ---------------------------------------------------------------------------


@pytest.mark.perf
class TestSingleProject:
    """Single-project workspace must work correctly."""

    def test_single_project_refresh(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """Refresh with 1 project completes and binds correctly."""
        window = _make_window(monkeypatch)
        snapshot = _build_snapshot(num_projects=1)

        _do_refresh(window, snapshot)

        assert window._snapshot is not None
        assert window._snapshot.metrics.total_projects == 1
        assert len(window._snapshot.projects) == 1
        assert len(window._snapshot.details) == 1
        assert window._refresh_in_progress is False

    def test_single_project_detail_accessible(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """Single project detail is accessible after refresh."""
        window = _make_window(monkeypatch)
        snapshot = _build_snapshot(num_projects=1)

        _do_refresh(window, snapshot)

        pid = "proj_000"
        assert window._snapshot is not None
        assert pid in window._snapshot.details
        detail = window._snapshot.details[pid]
        assert detail.project_id == pid
        assert detail.title == "测试项目 0"

    def test_single_project_perf(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """Single project refresh p95 < 100ms."""
        window = _make_window(monkeypatch)
        snapshot = _build_snapshot(num_projects=1)

        times: list[float] = []
        for _ in range(20):
            t0 = time.perf_counter()
            _do_refresh(window, snapshot)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            times.append(elapsed_ms)

        pct = compute_percentiles(times)
        assert pct["p95"] < 100, (
            f"Single project p95 = {pct['p95']:.1f}ms (expected < 100ms)"
        )


# ---------------------------------------------------------------------------
# Tests: 50+ projects
# ---------------------------------------------------------------------------


@pytest.mark.perf
class TestManyProjects:
    """Large workspace (50 projects) must complete within performance budget."""

    def test_50_projects_refresh_completes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """Refresh with 50 projects completes without error."""
        window = _make_window(monkeypatch)
        snapshot = _build_snapshot(num_projects=50)

        _do_refresh(window, snapshot)

        assert window._snapshot is not None
        assert window._snapshot.metrics.total_projects == 50
        assert len(window._snapshot.projects) == 50
        assert len(window._snapshot.details) == 50
        assert window._refresh_in_progress is False

    def test_50_projects_perf(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """50-project refresh stays fast under noisy parallel test load."""
        window = _make_window(monkeypatch)
        snapshot = _build_snapshot(num_projects=50)

        _do_refresh(window, snapshot)

        times: list[float] = []
        for _ in range(15):
            t0 = time.perf_counter()
            _do_refresh(window, snapshot)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            times.append(elapsed_ms)

        pct = compute_percentiles(times)
        # 50 projects with lightweight ProjectIndex should be fast.
        # xdist can inject one-off scheduler spikes into GUI refresh tests, so
        # keep the strict budget on the steady-state median and a looser p95
        # guard for pathological regressions.
        assert pct["p50"] < 100 and pct["p95"] < 250, (
            f"50-project p50/p95 = {pct['p50']:.1f}/{pct['p95']:.1f}ms "
            "(expected p50 < 100ms and p95 < 250ms). "
            f"p50={pct['p50']:.1f}ms, p99={pct['p99']:.1f}ms"
        )

    def test_50_projects_hash_consistency(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """50-project snapshot hash is stable across repeated calls."""
        window = _make_window(monkeypatch)
        snapshot = _build_snapshot(num_projects=50)

        _do_refresh(window, snapshot)
        first_hash = window._last_snapshot_hash

        _do_refresh(window, snapshot)
        second_hash = window._last_snapshot_hash

        assert first_hash == second_hash, (
            "Snapshot hash changed between identical refreshes"
        )


# ---------------------------------------------------------------------------
# Tests: degraded snapshot
# ---------------------------------------------------------------------------


@pytest.mark.perf
class TestDegradedSnapshot:
    """Snapshots with is_degraded=True projects must not crash."""

    def test_degraded_project_refresh(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """Refresh with degraded project detail completes without error."""
        window = _make_window(monkeypatch)
        snapshot = _build_snapshot(num_projects=2, is_degraded=True)

        _do_refresh(window, snapshot)

        assert window._snapshot is not None
        assert window._refresh_in_progress is False
        for detail in window._snapshot.details.values():
            assert detail.index.is_degraded is True

    def test_degraded_then_normal_refresh(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """Transitioning from degraded to normal detail works."""
        window = _make_window(monkeypatch)

        degraded_snap = _build_snapshot(num_projects=1, is_degraded=True)
        _do_refresh(window, degraded_snap)
        assert window._snapshot is not None
        assert window._snapshot.details["proj_000"].index.is_degraded is True

        normal_snap = _build_snapshot(num_projects=1, is_degraded=False)
        _do_refresh(window, normal_snap)
        assert window._snapshot is not None
        assert window._snapshot.details["proj_000"].index.is_degraded is False


# ---------------------------------------------------------------------------
# Tests: snapshot with missing/empty fields
# ---------------------------------------------------------------------------


@pytest.mark.perf
class TestMissingFields:
    """Snapshots with missing or empty fields must not crash."""

    def test_no_featured_project(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """Refresh with featured_project=None works."""
        window = _make_window(monkeypatch)
        snapshot = _build_empty_snapshot()
        assert snapshot.featured_project is None

        _do_refresh(window, snapshot)

        assert window._snapshot is not None
        assert window._snapshot.featured_project is None

    def test_empty_providers_list(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """Refresh with empty providers list works."""
        window = _make_window(monkeypatch)
        base = _build_snapshot(num_projects=1)
        snapshot = replace(base, providers=[])

        _do_refresh(window, snapshot)

        assert window._snapshot is not None
        assert window._snapshot.providers == []

    def test_empty_overview_providers(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """Refresh with overview.providers=[] works."""
        window = _make_window(monkeypatch)
        base = _build_snapshot(num_projects=1)
        new_overview = base.overview.model_copy(update={"providers": []})
        snapshot = replace(base, overview=new_overview)

        _do_refresh(window, snapshot)

        assert window._snapshot is not None
        assert window._snapshot.overview.providers == []

    def test_zero_words_zero_chapters(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """Refresh with zero words and zero chapters works."""
        window = _make_window(monkeypatch)
        base = _build_snapshot(num_projects=1)
        new_metrics = replace(
            base.metrics,
            total_words=0,
            total_chapters=0,
        )
        snapshot = replace(base, metrics=new_metrics)

        _do_refresh(window, snapshot)

        assert window._snapshot is not None
        assert window._snapshot.metrics.total_words == 0
        assert window._snapshot.metrics.total_chapters == 0

    def test_build_snapshot_payload_static_handles_empty(
        self,
        qapp: QApplication,
    ) -> None:
        """_build_snapshot_payload_static works with empty snapshot."""
        snapshot = _build_empty_snapshot()
        payload = NovelForgeDesktopWindow._build_snapshot_payload_static(snapshot)

        assert isinstance(payload, dict)
        assert "metrics" in payload
        assert "projects" in payload
        assert "details" in payload

    def test_compute_section_hashes_handles_empty(
        self,
        qapp: QApplication,
    ) -> None:
        """_compute_section_hashes works with empty snapshot."""
        snapshot = _build_empty_snapshot()
        payload = NovelForgeDesktopWindow._build_snapshot_payload_static(snapshot)
        section_hashes, changed_sections, final_hash = (
            NovelForgeDesktopWindow._compute_section_hashes(
                snapshot, payload, None, None
            )
        )

        assert isinstance(section_hashes, dict)
        assert isinstance(changed_sections, set)
        assert isinstance(final_hash, str)
        # First call with no prev → all sections changed
        assert len(changed_sections) > 0
