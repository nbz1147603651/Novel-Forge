"""Unit tests for the ``SectionBindablePage`` Protocol and incremental binding.

Task 9 of the desktop UI performance plan introduces:
- A ``SectionBindablePage`` Protocol with ``bind_workspace_sections`` method
- Dispatcher logic in ``_bind_workspace_for_page`` that prefers the
  section-scoped API when available, falling back to ``bind_workspace``

These tests verify:
1. The Protocol class exists with the expected method signature
2. The dispatcher calls ``bind_workspace_sections`` when available
3. Backward compat: pages with only ``bind_workspace`` still work
4. Pages with neither method are skipped gracefully
"""

from __future__ import annotations

import inspect
from typing import Any, Protocol
from unittest.mock import MagicMock

from novel_forge.desktop.window import NovelForgeDesktopWindow

# ---------------------------------------------------------------------------
# Protocol existence & shape
# ---------------------------------------------------------------------------


class TestSectionBindableProtocol:
    """``SectionBindablePage`` Protocol must exist with correct signature."""

    def test_protocol_class_exists(self) -> None:
        """Window module exports a SectionBindablePage Protocol."""
        from novel_forge.desktop.window import SectionBindablePage

        assert SectionBindablePage is not None

    def test_protocol_is_protocol(self) -> None:
        """SectionBindablePage is a typing.Protocol."""
        from novel_forge.desktop.window import SectionBindablePage

        # runtime_checkable or not, it should be a Protocol subclass
        assert issubclass(type(SectionBindablePage), type(Protocol)) or hasattr(
            SectionBindablePage, "__protocol_attrs__"
        ) or hasattr(SectionBindablePage, "_is_protocol")

    def test_protocol_has_bind_workspace_sections(self) -> None:
        """Protocol declares bind_workspace_sections method."""
        from novel_forge.desktop.window import SectionBindablePage

        assert hasattr(SectionBindablePage, "bind_workspace_sections")

    def test_protocol_method_signature(self) -> None:
        """bind_workspace_sections takes (snapshot, sections: frozenset[str])."""
        from novel_forge.desktop.window import SectionBindablePage

        sig = inspect.signature(SectionBindablePage.bind_workspace_sections)
        params = list(sig.parameters.keys())
        # self, snapshot, sections
        assert "snapshot" in params or len(params) >= 3, (
            f"Expected (self, snapshot, sections), got {params}"
        )
        assert "sections" in params, (
            f"Expected 'sections' parameter, got {params}"
        )


# ---------------------------------------------------------------------------
# Dispatcher behaviour
# ---------------------------------------------------------------------------


def _make_dispatcher_harness() -> tuple[Any, Any]:
    """Build a minimal harness to test ``_bind_workspace_for_page``.

    Returns ``(harness, mock_snapshot)`` where *harness* has the attributes
    the dispatcher reads and the real unbound method bound to it.
    """
    mock_snapshot = MagicMock(name="snapshot")
    harness = MagicMock(name="harness")
    harness._snapshot = mock_snapshot
    harness._pages: dict[str, Any] = {}
    harness._page_workspace_revision: dict[str, int] = {}
    harness._workspace_revision = 1
    # Bind the real method
    harness._bind_workspace_for_page = NovelForgeDesktopWindow._bind_workspace_for_page.__get__(
        harness, type(harness)
    )
    return harness, mock_snapshot


class TestBindWorkspaceForPageDispatcher:
    """``_bind_workspace_for_page`` must prefer section-scoped binding."""

    def test_calls_bind_workspace_sections_when_available(self) -> None:
        """Page with bind_workspace_sections gets called with sections."""
        harness, snapshot = _make_dispatcher_harness()
        page = MagicMock()
        page.bind_workspace_sections = MagicMock()
        page.bind_workspace = MagicMock()
        harness._pages = {"dashboard": page}
        harness._PAGE_SECTION_MAP = NovelForgeDesktopWindow._PAGE_SECTION_MAP

        harness._bind_workspace_for_page("dashboard")

        page.bind_workspace_sections.assert_called_once()
        call_args = page.bind_workspace_sections.call_args
        # First arg: snapshot, second arg: frozenset of sections
        assert call_args[0][0] is snapshot
        assert isinstance(call_args[0][1], frozenset)
        # bind_workspace should NOT be called
        page.bind_workspace.assert_not_called()

    def test_falls_back_to_bind_workspace(self) -> None:
        """Page with only bind_workspace still works (backward compat)."""
        harness, snapshot = _make_dispatcher_harness()
        page = MagicMock(spec=[])  # no methods
        page.bind_workspace = MagicMock()
        harness._pages = {"projects": page}
        harness._PAGE_SECTION_MAP = NovelForgeDesktopWindow._PAGE_SECTION_MAP

        harness._bind_workspace_for_page("projects")

        page.bind_workspace.assert_called_once_with(snapshot)

    def test_skips_page_without_either_method(self) -> None:
        """Page with neither method is skipped gracefully."""
        harness, _snapshot = _make_dispatcher_harness()
        page = MagicMock(spec=[])  # no bind methods at all
        harness._pages = {"mystery": page}
        harness._PAGE_SECTION_MAP = NovelForgeDesktopWindow._PAGE_SECTION_MAP

        # Should not raise
        harness._bind_workspace_for_page("mystery")

    def test_skips_missing_page(self) -> None:
        """Non-existent page_id is a no-op."""
        harness, _snapshot = _make_dispatcher_harness()
        harness._pages = {}
        harness._PAGE_SECTION_MAP = NovelForgeDesktopWindow._PAGE_SECTION_MAP

        # Should not raise
        harness._bind_workspace_for_page("nonexistent")

    def test_skips_when_snapshot_is_none(self) -> None:
        """No binding happens when snapshot is None."""
        harness, _snapshot = _make_dispatcher_harness()
        harness._snapshot = None
        page = MagicMock()
        page.bind_workspace_sections = MagicMock()
        harness._pages = {"dashboard": page}
        harness._PAGE_SECTION_MAP = NovelForgeDesktopWindow._PAGE_SECTION_MAP

        harness._bind_workspace_for_page("dashboard")

        page.bind_workspace_sections.assert_not_called()

    def test_revision_skip_still_works(self) -> None:
        """Revision-based skip is preserved for section-bindable pages."""
        harness, snapshot = _make_dispatcher_harness()
        page = MagicMock()
        page.bind_workspace_sections = MagicMock()
        harness._pages = {"dashboard": page}
        harness._page_workspace_revision = {"dashboard": 1}
        harness._workspace_revision = 1  # same → skip
        harness._PAGE_SECTION_MAP = NovelForgeDesktopWindow._PAGE_SECTION_MAP

        harness._bind_workspace_for_page("dashboard", force=False)

        page.bind_workspace_sections.assert_not_called()

    def test_force_overrides_revision(self) -> None:
        """force=True bypasses revision check."""
        harness, snapshot = _make_dispatcher_harness()
        page = MagicMock()
        page.bind_workspace_sections = MagicMock()
        harness._pages = {"dashboard": page}
        harness._page_workspace_revision = {"dashboard": 1}
        harness._workspace_revision = 1  # same, but force=True
        harness._PAGE_SECTION_MAP = NovelForgeDesktopWindow._PAGE_SECTION_MAP

        harness._bind_workspace_for_page("dashboard", force=True)

        page.bind_workspace_sections.assert_called_once()

    def test_sections_match_page_section_map(self) -> None:
        """Sections passed to bind_workspace_sections match _PAGE_SECTION_MAP."""
        harness, snapshot = _make_dispatcher_harness()
        page = MagicMock()
        page.bind_workspace_sections = MagicMock()
        harness._pages = {"settings": page}
        harness._PAGE_SECTION_MAP = NovelForgeDesktopWindow._PAGE_SECTION_MAP

        harness._bind_workspace_for_page("settings")

        call_args = page.bind_workspace_sections.call_args
        sections_passed = call_args[0][1]
        expected = NovelForgeDesktopWindow._PAGE_SECTION_MAP["settings"]
        assert sections_passed == expected

    def test_page_not_in_section_map_gets_empty_frozenset(self) -> None:
        """Page not in _PAGE_SECTION_MAP gets an empty frozenset."""
        harness, snapshot = _make_dispatcher_harness()
        page = MagicMock()
        page.bind_workspace_sections = MagicMock()
        harness._pages = {"custom_page": page}
        harness._PAGE_SECTION_MAP = {}  # no entries

        harness._bind_workspace_for_page("custom_page")

        call_args = page.bind_workspace_sections.call_args
        sections_passed = call_args[0][1]
        assert sections_passed == frozenset()


# ---------------------------------------------------------------------------
# ObservablePageState.batch_update sanity
# ---------------------------------------------------------------------------


class TestObservableBatchUpdate:
    """``ObservablePageState.batch_update`` must exist and work."""

    def test_batch_update_exists(self) -> None:
        """ObservablePageState has batch_update method."""
        from novel_forge.desktop.state.observable import ObservablePageState

        assert hasattr(ObservablePageState, "batch_update")
        assert callable(ObservablePageState.batch_update)

    def test_batch_update_emits_single_signal(self) -> None:
        """batch_update emits exactly one 'changed' signal with 'batch' field."""
        from novel_forge.desktop.state.observable import ObservablePageState

        state = ObservablePageState()
        state.title = ""
        state.count = 0

        signals: list[tuple[str, Any]] = []
        state.changed.connect(lambda f, v: signals.append((f, v)))

        state.batch_update(title="Hello", count=42)

        assert len(signals) == 1
        field_name, updated_dict = signals[0]
        assert field_name == "batch"
        assert updated_dict == {"title": "Hello", "count": 42}
        assert state.title == "Hello"
        assert state.count == 42
