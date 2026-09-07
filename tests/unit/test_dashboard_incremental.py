"""Tests for DashboardPage.bind_workspace_sections incremental binding.

Verifies that only the render methods corresponding to changed sections
are invoked, avoiding unnecessary full-page re-renders.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from novel_forge.desktop.workspace import (
    DesktopProjectItem,
    DesktopWorkspaceMetrics,
    DesktopWorkspaceSnapshot,
)
from novel_forge.workspace.projects import WorkspaceOverview

# ── Helpers ──────────────────────────────────────────────────────────


def _make_project(project_id: str = "proj-1") -> DesktopProjectItem:
    return DesktopProjectItem(
        project_id=project_id,
        title="Test Project",
        mode="long",
        mode_label="长篇",
        status="writing",
        status_label="连载中",
        progress_label="3/10",
        progress_percent=30,
        last_updated_label="2026-01-01",
        headline="A test project",
        next_action="Write chapter 4",
        genre="Fantasy",
        tone="Dark",
        completed_chapters=3,
        total_chapters=10,
        next_chapter=4,
        has_outline=True,
        has_canon=True,
    )


def _make_snapshot(
    projects: list[DesktopProjectItem] | None = None,
    featured: DesktopProjectItem | None = None,
) -> DesktopWorkspaceSnapshot:
    if projects is None:
        projects = [_make_project()]
    if featured is None:
        featured = projects[0] if projects else None
    return DesktopWorkspaceSnapshot(
        storage_root=Path("/tmp/test_workspace"),
        default_provider="openai:gpt-4o",
        overview=WorkspaceOverview(
            storage_root="/tmp/test_workspace",
            total_projects=len(projects),
            short_projects=0,
            long_projects=len(projects),
            total_generated_chapters=3,
            providers=["openai"],
            default_provider="openai:gpt-4o",
        ),
        metrics=DesktopWorkspaceMetrics(
            total_projects=len(projects),
            total_chapters=3,
            total_words=15000,
            configured_providers=2,
        ),
        providers=[],
        projects=projects,
        featured_project=featured,
        details={},
    )


def _make_harness() -> MagicMock:
    """Create a mock dashboard with real ``bind_workspace_sections`` bound.

    DashboardPage cannot be constructed in offscreen tests (SIGSEGV),
    so we bind the unbound method to a MagicMock that has all required
    attributes.
    """
    from novel_forge.desktop.pages.standalone.dashboard_page import DashboardPage

    mock = MagicMock()
    mock._snapshot = None
    mock._all_projects = []
    mock._selected_project_id = None
    mock._workspace_fingerprint = None
    mock._pending_workspace_full = None
    mock._pending_workspace_sections = None
    mock._is_background_hidden = MagicMock(return_value=False)

    # Metric card mocks
    card_mocks = [MagicMock() for _ in range(4)]
    mock._metric_cards = card_mocks

    # Render method mocks (track call count)
    mock._render_hero = MagicMock()
    mock._render_status = MagicMock()
    mock._render_cards = MagicMock()
    mock._render_detail = MagicMock()

    # Signal mock
    mock.context_changed = MagicMock()

    # Bind the real unbound method
    mock.bind_workspace_sections = DashboardPage.bind_workspace_sections.__get__(
        mock, type(mock)
    )
    mock._adopt_workspace_state = DashboardPage._adopt_workspace_state.__get__(
        mock, type(mock)
    )

    return mock


# ── Tests: single-section binding ────────────────────────────────────


class TestBindWorkspaceSectionsSingleSection:
    """Each section triggers exactly its own render method."""

    def test_metrics_only_updates_metric_cards(self) -> None:
        harness = _make_harness()
        snapshot = _make_snapshot()

        harness.bind_workspace_sections(snapshot, frozenset({"metrics"}))

        # "metrics" updates the 4 stat cards via set_content, NOT _render_cards
        # (_render_cards renders the project card grid, which belongs to "projects")
        assert harness._render_hero.call_count == 0
        assert harness._render_status.call_count == 0
        assert harness._render_cards.call_count == 0
        assert harness._render_detail.call_count == 0

    def test_metrics_updates_card_content(self) -> None:
        harness = _make_harness()
        snapshot = _make_snapshot()

        harness.bind_workspace_sections(snapshot, frozenset({"metrics"}))

        # 4 metric cards should each receive set_content
        for card in harness._metric_cards:
            card.set_content.assert_called_once()

    def test_projects_only_renders_cards_and_detail(self) -> None:
        harness = _make_harness()
        snapshot = _make_snapshot()

        harness.bind_workspace_sections(snapshot, frozenset({"projects"}))

        assert harness._render_cards.call_count == 1
        assert harness._render_detail.call_count == 1
        assert harness._render_hero.call_count == 0
        assert harness._render_status.call_count == 0

    def test_overview_only_renders_status(self) -> None:
        harness = _make_harness()
        snapshot = _make_snapshot()

        harness.bind_workspace_sections(snapshot, frozenset({"overview"}))

        assert harness._render_status.call_count == 1
        assert harness._render_hero.call_count == 0
        assert harness._render_cards.call_count == 0
        assert harness._render_detail.call_count == 0

    def test_featured_project_only_renders_hero(self) -> None:
        harness = _make_harness()
        snapshot = _make_snapshot()

        harness.bind_workspace_sections(snapshot, frozenset({"featured_project"}))

        assert harness._render_hero.call_count == 1
        assert harness._render_status.call_count == 0
        assert harness._render_cards.call_count == 0
        assert harness._render_detail.call_count == 0


# ── Tests: multi-section binding ─────────────────────────────────────


class TestBindWorkspaceSectionsMultiSection:
    """Multiple sections trigger the union of their render methods."""

    def test_metrics_and_projects(self) -> None:
        harness = _make_harness()
        snapshot = _make_snapshot()

        harness.bind_workspace_sections(snapshot, frozenset({"metrics", "projects"}))

        assert harness._render_cards.call_count == 1
        assert harness._render_detail.call_count == 1
        assert harness._render_hero.call_count == 0
        assert harness._render_status.call_count == 0

    def test_all_sections(self) -> None:
        harness = _make_harness()
        snapshot = _make_snapshot()

        harness.bind_workspace_sections(
            snapshot,
            frozenset({"metrics", "projects", "overview", "featured_project"}),
        )

        assert harness._render_cards.call_count == 1
        assert harness._render_detail.call_count == 1
        assert harness._render_status.call_count == 1
        assert harness._render_hero.call_count == 1

    def test_overview_and_featured(self) -> None:
        harness = _make_harness()
        snapshot = _make_snapshot()

        harness.bind_workspace_sections(
            snapshot, frozenset({"overview", "featured_project"})
        )

        assert harness._render_status.call_count == 1
        assert harness._render_hero.call_count == 1
        assert harness._render_cards.call_count == 0
        assert harness._render_detail.call_count == 0


# ── Tests: empty / unknown sections ─────────────────────────────────


class TestBindWorkspaceSectionsEdgeCases:
    """Edge cases: empty set, unknown section names."""

    def test_empty_sections_no_render(self) -> None:
        harness = _make_harness()
        snapshot = _make_snapshot()

        harness.bind_workspace_sections(snapshot, frozenset())

        assert harness._render_cards.call_count == 0
        assert harness._render_detail.call_count == 0
        assert harness._render_hero.call_count == 0
        assert harness._render_status.call_count == 0

    def test_unknown_section_ignored(self) -> None:
        harness = _make_harness()
        snapshot = _make_snapshot()

        harness.bind_workspace_sections(snapshot, frozenset({"nonexistent"}))

        assert harness._render_cards.call_count == 0
        assert harness._render_hero.call_count == 0
        assert harness._render_status.call_count == 0
        assert harness._render_detail.call_count == 0


# ── Tests: state updates ─────────────────────────────────────────────


class TestBindWorkspaceSectionsStateUpdates:
    """Snapshot and project list are always updated regardless of sections."""

    def test_snapshot_always_updated(self) -> None:
        harness = _make_harness()
        snapshot = _make_snapshot()

        harness.bind_workspace_sections(snapshot, frozenset({"metrics"}))

        assert harness._snapshot is snapshot

    def test_all_projects_always_updated(self) -> None:
        harness = _make_harness()
        proj = _make_project("proj-x")
        snapshot = _make_snapshot(projects=[proj])

        harness.bind_workspace_sections(snapshot, frozenset())

        assert harness._all_projects == [proj]

    def test_selected_project_reset_when_unavailable(self) -> None:
        harness = _make_harness()
        harness._selected_project_id = "gone-project"
        snapshot = _make_snapshot()

        harness.bind_workspace_sections(snapshot, frozenset())

        # "gone-project" not in snapshot → should reset to first project
        assert harness._selected_project_id == snapshot.projects[0].project_id

    def test_selected_project_preserved_when_available(self) -> None:
        harness = _make_harness()
        proj = _make_project("keep-me")
        snapshot = _make_snapshot(projects=[proj])
        harness._selected_project_id = "keep-me"

        harness.bind_workspace_sections(snapshot, frozenset())

        assert harness._selected_project_id == "keep-me"


# ── Tests: context_changed signal ────────────────────────────────────


class TestBindWorkspaceSectionsSignal:
    """context_changed is emitted only when sections are rendered."""

    def test_signal_emitted_when_sections_rendered(self) -> None:
        harness = _make_harness()
        snapshot = _make_snapshot()

        harness.bind_workspace_sections(snapshot, frozenset({"metrics"}))

        harness.context_changed.emit.assert_called_once()

    def test_signal_not_emitted_for_empty_sections(self) -> None:
        harness = _make_harness()
        snapshot = _make_snapshot()

        harness.bind_workspace_sections(snapshot, frozenset())

        harness.context_changed.emit.assert_not_called()

    def test_signal_emitted_once_for_multiple_sections(self) -> None:
        harness = _make_harness()
        snapshot = _make_snapshot()

        harness.bind_workspace_sections(
            snapshot,
            frozenset({"metrics", "projects", "overview", "featured_project"}),
        )

        # Should emit exactly once, not once per section
        assert harness.context_changed.emit.call_count == 1
