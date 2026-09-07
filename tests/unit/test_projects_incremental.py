"""Tests for incremental binding on the projects page (Task 11).

Verifies that ``bind_workspace_sections`` only rebuilds the viewer when the
currently displayed project's details actually changed, skipping redundant
rebuilds when a *different* project's details were updated.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from novel_forge.desktop.pages.standalone.projects_page import ProjectsPage
from novel_forge.desktop.workspace import (
    DesktopProjectItem,
    DesktopWorkspaceMetrics,
    DesktopWorkspaceSnapshot,
)
from novel_forge.workspace.projects import ChapterSummary, ProjectDetail


def _make_project_item(project_id: str, title: str) -> DesktopProjectItem:
    return DesktopProjectItem(
        project_id=project_id,
        title=title,
        mode="long",
        mode_label="长篇",
        status="active",
        status_label="进行中",
        progress_label="",
        progress_percent=0,
        last_updated_label="刚刚",
        headline="",
        next_action="",
        genre="scifi",
        tone="dark",
        completed_chapters=0,
        total_chapters=None,
        next_chapter=None,
        has_outline=False,
        has_canon=False,
    )


def _make_detail(
    project_id: str,
    *,
    updated_at: str = "2026-06-01T00:00:00+00:00",
    chapters: list[ChapterSummary] | None = None,
) -> ProjectDetail:
    return ProjectDetail(
        project_id=project_id,
        mode="long",
        title=f"Project {project_id}",
        updated_at=updated_at,
        chapters=chapters or [],
    )


def _make_snapshot(
    tmp_path: Path,
    details: dict[str, ProjectDetail],
) -> DesktopWorkspaceSnapshot:
    projects = [_make_project_item(pid, d.title) for pid, d in details.items()]
    return DesktopWorkspaceSnapshot(
        storage_root=tmp_path,
        default_provider="mock",
        overview=None,  # type: ignore[arg-type]
        metrics=DesktopWorkspaceMetrics(
            total_projects=len(projects),
            total_chapters=0,
            total_words=0,
            configured_providers=0,
        ),
        providers=[],
        projects=projects,
        featured_project=projects[0] if projects else None,
        details=details,
    )


@pytest.fixture()
def page(qapp):  # noqa: ANN001, ANN201
    """Construct a ProjectsPage in offscreen mode."""
    widget = ProjectsPage()
    try:
        yield widget
    finally:
        widget.shutdown()
        widget.close()
        widget.deleteLater()


@pytest.fixture()
def two_project_snapshot(tmp_path: Path) -> DesktopWorkspaceSnapshot:
    """Snapshot with two projects: 'proj_a' and 'proj_b'."""
    return _make_snapshot(
        tmp_path,
        {
            "proj_a": _make_detail("proj_a"),
            "proj_b": _make_detail("proj_b"),
        },
    )


class TestBindWorkspaceSectionsExists:
    """``bind_workspace_sections`` must exist on ProjectsPage."""

    def test_method_exists(self, page: ProjectsPage) -> None:
        assert hasattr(page, "bind_workspace_sections")
        assert callable(page.bind_workspace_sections)


class TestProjectsSectionUpdatesComboOnly:
    """When only 'projects' section changed, combo box updates but viewer does NOT rebuild."""

    def test_projects_section_updates_combo_not_viewer(
        self,
        page: ProjectsPage,
        two_project_snapshot: DesktopWorkspaceSnapshot,
    ) -> None:
        # Initial full bind
        page.bind_workspace(two_project_snapshot)
        page.load_project("proj_a")

        with patch.object(page, "_rebuild_viewer") as mock_rebuild:
            page.bind_workspace_sections(two_project_snapshot, frozenset({"projects"}))

        # Viewer should NOT have been rebuilt — only the combo was refreshed
        mock_rebuild.assert_not_called()
        # Combo should still have 2 items
        assert page._project_combo.count() == 2


class TestDetailsSectionSkipsUnrelatedProject:
    """When 'details' changed for a project NOT currently displayed, viewer is NOT rebuilt."""

    def test_details_for_different_project_skips_rebuild(
        self,
        page: ProjectsPage,
        two_project_snapshot: DesktopWorkspaceSnapshot,
    ) -> None:
        # Set up: display proj_a
        page.bind_workspace(two_project_snapshot)
        page.load_project("proj_a")

        # Build a new snapshot where only proj_b's details changed
        new_detail_b = _make_detail("proj_b", updated_at="2026-06-02T00:00:00+00:00")
        new_snapshot = _make_snapshot(
            two_project_snapshot.storage_root,
            {
                "proj_a": _make_detail("proj_a"),  # unchanged
                "proj_b": new_detail_b,  # changed
            },
        )

        with patch.object(page, "_rebuild_if_changed") as mock_rebuild:
            page.bind_workspace_sections(new_snapshot, frozenset({"details"}))

        # proj_a is displayed, but only proj_b changed → no rebuild
        mock_rebuild.assert_not_called()

    def test_details_for_displayed_project_triggers_rebuild(
        self,
        page: ProjectsPage,
        two_project_snapshot: DesktopWorkspaceSnapshot,
    ) -> None:
        # Set up: display proj_a
        page.bind_workspace(two_project_snapshot)
        page.load_project("proj_a")

        # Build a new snapshot where proj_a's details changed
        new_detail_a = _make_detail("proj_a", updated_at="2026-06-02T00:00:00+00:00")
        new_snapshot = _make_snapshot(
            two_project_snapshot.storage_root,
            {
                "proj_a": new_detail_a,  # changed
                "proj_b": _make_detail("proj_b"),  # unchanged
            },
        )

        with patch.object(page, "_rebuild_if_changed") as mock_rebuild:
            page.bind_workspace_sections(new_snapshot, frozenset({"details"}))

        # proj_a is displayed AND its details changed → rebuild
        mock_rebuild.assert_called_once()

    def test_details_section_file_change_forces_rebuild(
        self,
        page: ProjectsPage,
        two_project_snapshot: DesktopWorkspaceSnapshot,
    ) -> None:
        project_dir = two_project_snapshot.storage_root / "proj_a"
        project_dir.mkdir(parents=True)
        spec = project_dir / "spec.json"
        spec.write_text('{"title": "old", "genre": "test"}', encoding="utf-8")

        page.bind_workspace(two_project_snapshot)
        page.load_project("proj_a")

        spec.write_text(
            '{"title": "new title", "genre": "test", "theme": "changed"}', encoding="utf-8"
        )
        new_mtime = time.time() + 100
        os.utime(spec, (new_mtime, new_mtime))

        with patch.object(page, "_rebuild_if_changed") as mock_rebuild:
            page.bind_workspace_sections(two_project_snapshot, frozenset({"details"}))

        mock_rebuild.assert_called_once_with(force=True)

    def test_full_bind_file_change_forces_rebuild(
        self,
        page: ProjectsPage,
        two_project_snapshot: DesktopWorkspaceSnapshot,
    ) -> None:
        project_dir = two_project_snapshot.storage_root / "proj_a"
        project_dir.mkdir(parents=True)
        spec = project_dir / "spec.json"
        spec.write_text('{"title": "old", "genre": "test"}', encoding="utf-8")

        page.bind_workspace(two_project_snapshot)
        page.load_project("proj_a")

        spec.write_text('{"title": "changed from full bind", "genre": "test"}', encoding="utf-8")
        new_mtime = time.time() + 100
        os.utime(spec, (new_mtime, new_mtime))

        with patch.object(page, "_rebuild_if_changed") as mock_rebuild:
            page.bind_workspace(two_project_snapshot)

        mock_rebuild.assert_called_once_with(force=True)

    def test_force_rebuild_bypasses_unchanged_detail_fingerprint(
        self,
        page: ProjectsPage,
        two_project_snapshot: DesktopWorkspaceSnapshot,
    ) -> None:
        page.bind_workspace(two_project_snapshot)
        page.load_project("proj_a")

        with patch.object(page, "_rebuild_viewer") as mock_rebuild:
            page._rebuild_if_changed(force=True)

        mock_rebuild.assert_called_once()

    def test_displayed_project_change_does_not_rebuild_during_character_edit(
        self,
        page: ProjectsPage,
        two_project_snapshot: DesktopWorkspaceSnapshot,
    ) -> None:
        class ActiveCharacterEditor:
            def has_unsaved_changes(self) -> bool:
                return False

            def has_active_edit_session(self) -> bool:
                return True

        page.bind_workspace(two_project_snapshot)
        page.load_project("proj_a")
        page._character_widgets = [ActiveCharacterEditor()]  # type: ignore[list-item]

        new_snapshot = _make_snapshot(
            two_project_snapshot.storage_root,
            {
                "proj_a": _make_detail("proj_a", updated_at="2026-06-02T00:00:00+00:00"),
                "proj_b": _make_detail("proj_b"),
            },
        )

        with patch.object(page, "_rebuild_viewer") as mock_rebuild:
            page.bind_workspace_sections(new_snapshot, frozenset({"details"}))

        mock_rebuild.assert_not_called()


class TestBothSectionsUpdate:
    """When both 'projects' and 'details' changed, combo updates and viewer rebuilds if needed."""

    def test_both_sections_with_displayed_project_changed(
        self,
        page: ProjectsPage,
        two_project_snapshot: DesktopWorkspaceSnapshot,
    ) -> None:
        page.bind_workspace(two_project_snapshot)
        page.load_project("proj_a")

        new_detail_a = _make_detail("proj_a", updated_at="2026-06-03T00:00:00+00:00")
        new_snapshot = _make_snapshot(
            two_project_snapshot.storage_root,
            {
                "proj_a": new_detail_a,
                "proj_b": _make_detail("proj_b"),
            },
        )

        with patch.object(page, "_rebuild_if_changed") as mock_rebuild:
            page.bind_workspace_sections(
                new_snapshot, frozenset({"projects", "details"})
            )

        mock_rebuild.assert_called_once()
        assert page._project_combo.count() == 2


class TestEmptySectionsNoOp:
    """Empty sections frozenset is a no-op."""

    def test_empty_sections_does_nothing(
        self,
        page: ProjectsPage,
        two_project_snapshot: DesktopWorkspaceSnapshot,
    ) -> None:
        page.bind_workspace(two_project_snapshot)
        page.load_project("proj_a")

        with (
            patch.object(page, "_rebuild_viewer") as mock_viewer,
            patch.object(page, "_rebuild_if_changed") as mock_rebuild,
        ):
            page.bind_workspace_sections(two_project_snapshot, frozenset())

        mock_viewer.assert_not_called()
        mock_rebuild.assert_not_called()


class TestNoDisplayedProject:
    """When no project is currently displayed, details section is a no-op."""

    def test_details_without_displayed_project(
        self,
        page: ProjectsPage,
        two_project_snapshot: DesktopWorkspaceSnapshot,
    ) -> None:
        page.bind_workspace(two_project_snapshot)
        # Don't load any project — _current_project_id is None

        with patch.object(page, "_rebuild_if_changed") as mock_rebuild:
            page.bind_workspace_sections(two_project_snapshot, frozenset({"details"}))

        mock_rebuild.assert_not_called()
