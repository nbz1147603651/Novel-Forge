"""Tests: chapter_studio ``bind_workspace_sections`` incremental binding.

Verifies that ``ChapterStudioCoordMixin.bind_workspace_sections`` only
updates the parts of the UI that correspond to the changed sections:

- ``"projects"`` → update the project combo box only
- ``"details"`` → update context for currently selected project only
- Both → full update (combo + context)
- Empty → no-op

The existing ``bind_workspace`` method remains as a full-refresh fallback.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from novel_forge.desktop.pages.chapter_studio.coord import ChapterStudioCoordMixin
from novel_forge.desktop.workspace import (
    DesktopProjectItem,
    DesktopWorkspaceMetrics,
    DesktopWorkspaceSnapshot,
    ProviderStatus,
    WorkspaceOverview,
)

# ── Helpers ────────────────────────────────────────────────────────────────


def _build_snapshot(
    *, project_ids: list[str] | None = None
) -> DesktopWorkspaceSnapshot:
    """Build a minimal DesktopWorkspaceSnapshot with long-mode projects."""
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

    projects: list[DesktopProjectItem] = []
    details: dict[str, object] = {}
    for pid in (project_ids if project_ids is not None else ["novel_a"]):
        item = DesktopProjectItem(
            project_id=pid,
            title=pid,
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
            headline=f"{pid} - 第3章待续写",
            next_action="续写第3章",
        )
        projects.append(item)

    overview = WorkspaceOverview(
        storage_root=str(storage_root),
        total_projects=len(projects),
        short_projects=0,
        long_projects=len(projects),
        total_generated_chapters=2 * len(projects),
        providers=["mock"],
        default_provider="mock",
    )
    metrics = DesktopWorkspaceMetrics(
        total_projects=len(projects),
        total_chapters=2 * len(projects),
        total_words=5000 * len(projects),
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


class _Harness(ChapterStudioCoordMixin):
    """Minimal harness that provides the attributes bind_workspace_sections needs.

    ChapterStudioCoordMixin is a mixin — we can't instantiate it alone.
    This harness provides mock Qt widgets and spy methods so we can test
    the real ``bind_workspace_sections`` without constructing the full page.
    """

    def __init__(self) -> None:
        # Mock Qt widgets
        self._project_combo = MagicMock()
        self._chapter_spin = MagicMock()
        self._project_status_dot = MagicMock()

        # Internal state
        self._workspace: DesktopWorkspaceSnapshot | None = None
        self._previous_project: str | None = None

        # Spy trackers
        self.activate_calls: list[str] = []
        self.request_context_calls: int = 0
        self.bind_studio_calls: list[object] = []
        self.status_dot_calls: int = 0

    # ── Spy overrides ──────────────────────────────────────────────────

    def _activate_project_context(self, project_id: str) -> None:
        self.activate_calls.append(project_id)

    def _request_context(self) -> None:
        self.request_context_calls += 1

    def bind_studio(self, snapshot: object) -> None:
        self.bind_studio_calls.append(snapshot)

    def _update_status_dot(self) -> None:
        self.status_dot_calls += 1

    def _update_active_projects(self) -> None:
        if not hasattr(self, "_active_projects"):
            self._active_projects: set[str] = set()

    def current_project_id(self) -> str:
        return self._project_combo.currentText().strip()

    def current_chapter_number(self) -> int:
        return self._chapter_spin.value()


# ── Tests ──────────────────────────────────────────────────────────────────


class TestBindWorkspaceSections:
    """Tests for ChapterStudioCoordMixin.bind_workspace_sections."""

    def test_method_exists(self) -> None:
        """bind_workspace_sections is defined on the mixin."""
        assert hasattr(ChapterStudioCoordMixin, "bind_workspace_sections")

    def test_projects_section_updates_combo_only(self) -> None:
        """'projects' section: combo box updated, context NOT refreshed."""
        h = _Harness()
        snap = _build_snapshot(project_ids=["novel_a", "novel_b"])

        # Set current text so combo has a selection
        h._project_combo.currentText.return_value = "novel_a"

        h.bind_workspace_sections(snap, frozenset({"projects"}))

        # Combo should be cleared and repopulated
        h._project_combo.blockSignals.assert_called()
        h._project_combo.clear.assert_called_once()
        # 2 projects added
        assert h._project_combo.addItem.call_count == 2

        # Context should NOT be refreshed
        assert h.activate_calls == [], (
            "_activate_project_context should not be called for 'projects' section"
        )
        assert h.request_context_calls == 0, (
            "_request_context should not be called for 'projects' section"
        )
        assert h.bind_studio_calls == [], (
            "bind_studio should not be called for 'projects' section"
        )

    def test_details_section_updates_context_only(self) -> None:
        """'details' section: context refreshed, combo NOT updated."""
        h = _Harness()
        snap = _build_snapshot(project_ids=["novel_a"])

        # Set current text so combo has a selection
        h._project_combo.currentText.return_value = "novel_a"

        h.bind_workspace_sections(snap, frozenset({"details"}))

        # Combo should NOT be touched
        h._project_combo.clear.assert_not_called()
        h._project_combo.blockSignals.assert_not_called()

        # Context SHOULD be refreshed for the currently selected project
        assert "novel_a" in h.activate_calls, (
            f"_activate_project_context should be called for selected project. "
            f"Calls: {h.activate_calls}"
        )
        assert h.request_context_calls >= 1, (
            "_request_context should be called for 'details' section"
        )

    def test_both_sections_updates_everything(self) -> None:
        """Both sections: combo + context both updated."""
        h = _Harness()
        snap = _build_snapshot(project_ids=["novel_a", "novel_b"])

        h._project_combo.currentText.return_value = "novel_a"

        h.bind_workspace_sections(snap, frozenset({"projects", "details"}))

        # Combo updated
        h._project_combo.clear.assert_called_once()
        assert h._project_combo.addItem.call_count == 2

        # Context updated
        assert len(h.activate_calls) >= 1
        assert h.request_context_calls >= 1

    def test_empty_sections_is_noop(self) -> None:
        """Empty sections: nothing happens."""
        h = _Harness()
        snap = _build_snapshot(project_ids=["novel_a"])

        h._project_combo.currentText.return_value = "novel_a"

        h.bind_workspace_sections(snap, frozenset())

        # Nothing should be called
        h._project_combo.clear.assert_not_called()
        assert h.activate_calls == []
        assert h.request_context_calls == 0
        assert h.bind_studio_calls == []

    def test_stores_snapshot(self) -> None:
        """bind_workspace_sections always stores the snapshot."""
        h = _Harness()
        snap = _build_snapshot(project_ids=["novel_a"])

        h._project_combo.currentText.return_value = ""

        h.bind_workspace_sections(snap, frozenset({"projects"}))

        assert h._workspace is snap

    def test_fallback_bind_workspace_still_works(self) -> None:
        """Existing bind_workspace remains as full-refresh fallback."""
        h = _Harness()
        snap = _build_snapshot(project_ids=["novel_a"])

        h._project_combo.currentText.return_value = "novel_a"

        # bind_workspace should still work (full refresh)
        h.bind_workspace(snap)

        # Combo updated
        h._project_combo.clear.assert_called_once()
        # Context activated
        assert len(h.activate_calls) >= 1

    def test_projects_section_handles_no_long_projects(self) -> None:
        """'projects' section with no long projects clears combo."""
        h = _Harness()
        # Build snapshot with only short projects
        snap = _build_snapshot(project_ids=[])

        h._project_combo.currentText.return_value = "old_project"

        h.bind_workspace_sections(snap, frozenset({"projects"}))

        # Combo should be cleared
        h._project_combo.clear.assert_called_once()
        # No projects added
        assert h._project_combo.addItem.call_count == 0

    def test_details_section_no_selected_project(self) -> None:
        """'details' section with no selected project: no context request."""
        h = _Harness()
        snap = _build_snapshot(project_ids=["novel_a"])

        # No project selected
        h._project_combo.currentText.return_value = ""

        h.bind_workspace_sections(snap, frozenset({"details"}))

        # Should not request context when no project is selected
        assert h.request_context_calls == 0
