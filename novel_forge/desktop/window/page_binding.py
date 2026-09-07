"""Mixin module: page_binding methods for ``NovelForgeDesktopWindow``.

Auto-extracted in the M3.1 giant-file-split refactor. Methods live here so the
main :file:`window/__init__.py` facade can compose them via mixin inheritance.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import shiboken6
from PySide6.QtCore import (
    QObject,
)
from PySide6.QtWidgets import (
    QPushButton,
    QWidget,
)

import novel_forge.desktop.page_registrations as _page_registrations  # noqa: F401
from novel_forge.desktop.registry import page_registry
from novel_forge.desktop.strings import UIStrings
from novel_forge.desktop.ui_perf import ui_perf_span

_logger = logging.getLogger(__name__)

_WINDOW_DEFAULT_MIN_WIDTH = 1180
_WINDOW_DEFAULT_MIN_HEIGHT = 760
_WINDOW_DEFAULT_START_WIDTH = 1440
_WINDOW_DEFAULT_START_HEIGHT = 900
_WINDOW_SCREEN_WIDTH_RATIO = 0.92
_WINDOW_SCREEN_HEIGHT_RATIO = 0.90
_COMPACT_WIDTH_THRESHOLD = 1360
_COMPACT_HEIGHT_THRESHOLD = 820

if TYPE_CHECKING:
    pass


class PageBindingMixin:
    """Mixin that contributes the **page_binding** method group."""

    @staticmethod
    def _changed_sections_overlap(changed_sections: set[str], relevant: frozenset[str]) -> bool:
        for cs in changed_sections:
            base = cs.split("/")[0] if "/" in cs else cs
            if base in relevant:
                return True
        return False

    @staticmethod
    def _relevant_changed_sections(
        changed_sections: set[str],
        relevant: frozenset[str],
    ) -> set[str]:
        result: set[str] = set()
        for section in changed_sections:
            base = section.split("/", 1)[0] if "/" in section else section
            if base in relevant:
                result.add(base)
        return result

    def _mark_page_sections_pending(self, page_id: str, sections: set[str]) -> None:
        relevant = self._PAGE_SECTION_MAP.get(page_id, frozenset())
        pending = self._relevant_changed_sections(sections, relevant)
        if not pending:
            return
        self._pending_rebind_pages.add(page_id)
        self._pending_rebind_sections.setdefault(page_id, set()).update(pending)

    def _clear_page_pending_sections(self, page_id: str) -> None:
        self._pending_rebind_pages.discard(page_id)
        self._pending_rebind_sections.pop(page_id, None)

    def _bind_pending_or_stale_page(self, page_id: str) -> None:
        if self._snapshot is None:
            return
        page = self._pages.get(page_id)
        if page is None:
            return
        page_revision = self._page_workspace_revision.get(page_id, -1)
        pending_sections = set(self._pending_rebind_sections.get(page_id, set()))
        stale = page_revision != self._workspace_revision
        if not pending_sections and not stale:
            return

        if page_revision < 0 or not pending_sections:
            sections: frozenset[str] | None = self._PAGE_SECTION_MAP.get(page_id, frozenset())
        else:
            sections = frozenset(pending_sections)
        self._bind_workspace_for_page(page_id, force=True, sections=sections)
        self._clear_page_pending_sections(page_id)

    def _pages_needing_bind(
        self,
        changed_sections: set[str],
        current_page_id: str,
        *,
        initial: bool = False,
    ) -> list[str]:
        if not changed_sections:
            if initial:
                return [current_page_id]
            return []

        if initial:
            return [current_page_id]

        pages_to_bind: list[str] = []
        _overlap = self._changed_sections_overlap
        for page_id, relevant_sections in self._PAGE_SECTION_MAP.items():
            if page_id not in self._pages:
                continue
            if _overlap(changed_sections, relevant_sections):
                pages_to_bind.append(page_id)

        if current_page_id not in pages_to_bind:
            pages_to_bind.append(current_page_id)

        return pages_to_bind

    def _bind_workspace_for_page(
        self,
        page_id: str,
        *,
        force: bool = False,
        sections: frozenset[str] | None = None,
    ) -> None:
        """Bind latest workspace snapshot to one page when needed."""
        with ui_perf_span("bind_workspace_for_page", page_id=page_id, force=force):
            if self._snapshot is None:
                return
            page = self._pages.get(page_id)
            if page is None:
                return
            if isinstance(page, QObject) and not shiboken6.isValid(page):
                return
            if not force and self._page_workspace_revision.get(page_id) == self._workspace_revision:
                return
            try:
                if hasattr(page, "bind_workspace_sections"):
                    relevant_sections = sections or self._PAGE_SECTION_MAP.get(page_id, frozenset())
                    page.bind_workspace_sections(self._snapshot, relevant_sections)
                elif hasattr(page, "bind_workspace"):
                    page.bind_workspace(self._snapshot)
                else:
                    return
            except RuntimeError as exc:
                if "already deleted" in str(exc):
                    return
                raise
            self._page_workspace_revision[page_id] = self._workspace_revision

    def _apply_page_meta(self, page_id: str) -> None:
        meta = self.PAGE_META[page_id]
        self._top_eyebrow.setText(meta.eyebrow)
        self._top_title.setText(meta.title)
        self._top_subtitle.setText(meta.subtitle)

        actions = self._page_actions_for(page_id)
        self._bind_top_button(self._primary_button, actions[0] if len(actions) > 0 else None)
        self._bind_top_button(self._secondary_button, actions[1] if len(actions) > 1 else None)
        self._bind_top_button(self._tertiary_button, actions[2] if len(actions) > 2 else None)

        # Pages that expose a top-bar widget (e.g. Voice Studio's project
        # selector + metrics) mount it in place of the generic meta string.
        page = self._pages.get(page_id)
        top_bar = None
        if page is not None and hasattr(page, "get_top_bar_widget"):
            top_bar = page.get_top_bar_widget()

        if top_bar is not None:
            self._top_meta.setVisible(False)
            self._mount_page_top_bar(page_id, page, top_bar)
        else:
            self._unmount_page_top_bar()
            if self._snapshot is None:
                self._top_meta.setText(UIStrings.TOP_BAR_META_UNLOADED)
                self._top_meta.setVisible(True)
                return
            metrics = self._snapshot.metrics
            self._top_meta.setText(
                f"项目 {metrics.total_projects} · 已归档 {metrics.total_chapters} 章 · "
                f"字数 {metrics.total_words:,}"
            )
            self._top_meta.setVisible(True)

    def _mount_page_top_bar(self, page_id: str, page: Any, selector: QWidget) -> None:
        """Mount a page's top-bar widget into the shared header.

        Any page that exposes ``get_top_bar_widget()`` can contribute a
        compact context toolbar (project selector, metrics, etc.) in place
        of the generic meta string.
        """
        # Populate with available projects
        projects: list[tuple[str, str]] = []
        if self._snapshot is not None:
            for item in self._snapshot.projects:
                projects.append((item.project_id, item.title or item.project_id))
        # Pass storage root to page for auto-loading first project
        if hasattr(page, "set_storage_root") and self._snapshot is not None:
            page.set_storage_root(self._snapshot.storage_root)
        if hasattr(page, "populate_project_selector"):
            page.populate_project_selector(projects)
        # Top layout order is: title block, page context, global controls.
        # Mount at index 1 so page information uses the otherwise empty center
        # area instead of being appended after the right-side controls.
        controls = self._top_layout
        if controls.indexOf(selector) < 0:
            controls.insertWidget(1, selector)
        selector.setVisible(True)

    def _unmount_page_top_bar(self) -> None:
        """Hide any page's top-bar widget that is currently mounted."""
        # Check all pages for a mounted top-bar widget.
        for page in self._pages.values():
            if page is None or not hasattr(page, "get_top_bar_widget"):
                continue
            selector = page.get_top_bar_widget()
            if selector is not None:
                selector.setVisible(False)

    def _refresh_top_actions_for(self, page_id: str) -> None:
        try:
            current_widget = self._stack.currentWidget()
        except RuntimeError:
            return
        if current_widget is self._pages.get(page_id):
            self._apply_page_meta(page_id)

    def _page_actions_for(self, page_id: str) -> list[tuple[str, Callable[[], None]]]:
        # Delegate to registry action factories first.  Each page registers
        # an action_factory in page_registrations.py that receives the page
        # instance and returns (label, callback) tuples.  This removes the
        # hardcoded if-page_id chain from the shell.
        registry_actions = page_registry.get_actions(page_id)
        if registry_actions:
            return registry_actions
        return []

    def _bind_top_button(
        self,
        button: QPushButton,
        action: tuple[str, Callable[[], None]] | None,
    ) -> None:
        previous_handler = self._top_button_handlers.pop(button, None)
        if previous_handler is not None:
            try:
                button.clicked.disconnect(previous_handler)
            except (RuntimeError, TypeError):
                pass
        if action is None:
            button.hide()
            button.setText("")
            return
        label, handler = action
        button.setText(label)
        button.show()
        button.clicked.connect(handler)
        self._top_button_handlers[button] = handler
