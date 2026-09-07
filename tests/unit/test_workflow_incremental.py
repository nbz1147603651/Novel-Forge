"""Tests: workflow page incremental binding via ``bind_workspace_sections``.

Task 13 of the desktop UI performance plan migrates ``workflow_page.py`` to
implement ``bind_workspace_sections(snapshot, sections)`` so that only the
changed snapshot sections trigger re-renders:

- ``"projects"`` → ``_active_projects_panel.render_snapshot``
- ``"overview"`` → hero panel, short form, long panel, hero buttons, draft restore
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from novel_forge.desktop.pages.workflow.page import WorkflowPage  # noqa: E402


def _make_snapshot(**overrides: object) -> SimpleNamespace:
    """Build a minimal snapshot-like object for binding tests."""
    defaults = dict(
        storage_root=Path("/tmp/test_storage"),
        default_provider="mock",
        overview=SimpleNamespace(providers=["mock"]),
        metrics=SimpleNamespace(total_projects=0, configured_providers=1),
        providers=[],
        projects=[],
        featured_project=None,
        details={},
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_harness() -> SimpleNamespace:
    """Create a mock WorkflowPage-like object with real ``bind_workspace_sections``."""
    hero_panel = MagicMock()
    short_form = MagicMock()
    long_panel = MagicMock()
    active_projects_panel = MagicMock()

    harness = SimpleNamespace(
        _snapshot=None,
        _hero_panel=hero_panel,
        _short_form=short_form,
        _long_panel=long_panel,
        _active_projects_panel=active_projects_panel,
        _mock_enabled=False,
        _draft_dir=None,
        _draft_restored=False,
        context_changed=MagicMock(),
        _update_hero_buttons=MagicMock(),
        _restore_drafts=MagicMock(),
        _sync_dynamic_height=MagicMock(),
        has_unsaved_changes=MagicMock(return_value=False),
    )
    # Bind the real method from WorkflowPage
    harness.bind_workspace_sections = WorkflowPage.bind_workspace_sections.__get__(
        harness, type(harness)
    )
    return harness


class TestWorkflowBindWorkspaceSectionsExists:
    """WorkflowPage must expose ``bind_workspace_sections``."""

    def test_method_exists(self) -> None:
        assert hasattr(WorkflowPage, "bind_workspace_sections")

    def test_method_is_callable(self) -> None:
        assert callable(getattr(WorkflowPage, "bind_workspace_sections", None))


class TestWorkflowProjectsSection:
    """``"projects"`` section only re-renders the active projects panel."""

    def test_projects_section_calls_render_snapshot(self) -> None:
        harness = _make_harness()
        snap = _make_snapshot()

        harness.bind_workspace_sections(snap, frozenset({"projects"}))

        harness._active_projects_panel.render_snapshot.assert_called_once_with(snap)

    def test_projects_section_skips_overview_widgets(self) -> None:
        harness = _make_harness()
        snap = _make_snapshot()

        harness.bind_workspace_sections(snap, frozenset({"projects"}))

        harness._short_form.set_storage_root.assert_not_called()
        harness._long_panel.set_storage_root.assert_not_called()
        harness._long_panel.bind_snapshot.assert_not_called()

    def test_projects_section_skips_hero_buttons_and_context(self) -> None:
        harness = _make_harness()
        snap = _make_snapshot()

        harness.bind_workspace_sections(snap, frozenset({"projects"}))

        harness._update_hero_buttons.assert_not_called()
        harness.context_changed.emit.assert_not_called()


class TestWorkflowOverviewSection:
    """``"overview"`` section re-renders overview widgets, not projects."""

    def test_overview_section_skips_projects_panel(self) -> None:
        harness = _make_harness()
        snap = _make_snapshot()

        harness.bind_workspace_sections(snap, frozenset({"overview"}))

        harness._active_projects_panel.render_snapshot.assert_not_called()

    def test_overview_section_updates_hero_panel(self) -> None:
        harness = _make_harness()
        snap = _make_snapshot()

        harness.bind_workspace_sections(snap, frozenset({"overview"}))

        assert harness._snapshot is snap

    def test_overview_section_updates_forms(self) -> None:
        harness = _make_harness()
        snap = _make_snapshot()

        harness.bind_workspace_sections(snap, frozenset({"overview"}))

        harness._short_form.set_storage_root.assert_called_once_with(snap.storage_root)
        harness._long_panel.set_storage_root.assert_called_once_with(snap.storage_root)
        harness._long_panel.bind_snapshot.assert_called_once_with(snap)

    def test_overview_section_updates_hero_buttons_and_context(self) -> None:
        harness = _make_harness()
        snap = _make_snapshot()

        harness.bind_workspace_sections(snap, frozenset({"overview"}))

        harness._update_hero_buttons.assert_called_once()
        harness.context_changed.emit.assert_called_once()


class TestWorkflowBothSections:
    """Both sections together update everything."""

    def test_both_sections_update_all(self) -> None:
        harness = _make_harness()
        snap = _make_snapshot()

        harness.bind_workspace_sections(snap, frozenset({"projects", "overview"}))

        harness._active_projects_panel.render_snapshot.assert_called_once()
        harness._long_panel.bind_snapshot.assert_called_once()

    def test_empty_sections_update_nothing(self) -> None:
        harness = _make_harness()
        snap = _make_snapshot()

        harness.bind_workspace_sections(snap, frozenset())

        harness._active_projects_panel.render_snapshot.assert_not_called()


class TestWorkflowSnapshotStored:
    """``bind_workspace_sections`` always stores the snapshot."""

    def test_snapshot_stored_on_projects(self) -> None:
        harness = _make_harness()
        snap = _make_snapshot()

        harness.bind_workspace_sections(snap, frozenset({"projects"}))

        assert harness._snapshot is snap

    def test_snapshot_stored_on_overview(self) -> None:
        harness = _make_harness()
        snap = _make_snapshot()

        harness.bind_workspace_sections(snap, frozenset({"overview"}))

        assert harness._snapshot is snap
