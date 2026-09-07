"""Mixin module: task_focus_dialog methods for ``NovelForgeDesktopWindow``.

Auto-extracted in the M3.1 giant-file-split refactor. Methods live here so the
main :file:`window/__init__.py` facade can compose them via mixin inheritance.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import novel_forge.desktop.page_registrations as _page_registrations  # noqa: F401
from novel_forge.desktop.components.task_focus import (
    FloatingStreamWindow,
    TaskFocusDialog,
)
from novel_forge.desktop.task_observation import TaskFocusScope

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


class TaskFocusDialogMixin:
    """Mixin that contributes the **task_focus_dialog** method group."""

    def _open_task_focus_dialog(self, *, focus_stream_detail: bool = False) -> None:
        dialog = self._task_focus_dialog
        if dialog is None:
            dialog = TaskFocusDialog(self._task_observation_store, self)
            dialog.decision_selected.connect(self._provide_task_decision)
            dialog.task_delete_requested.connect(self._clear_observed_task)
            self._task_focus_dialog = dialog
        else:
            dialog.reopen(self._task_observation_store)
        if focus_stream_detail:
            dialog.show_stream_detail()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _toggle_pet_stream_window(self) -> None:
        """Toggle the floating stream detail window (Level 3 view).

        Lazily creates the :class:`FloatingStreamWindow` on first open,
        then shows/hides it on subsequent clicks.  The window is updated
        with the current best-focus task state each time it's shown.
        """
        window = self._floating_stream_window
        if window is not None and window.isVisible():
            window.hide()
            return
        if window is None:
            window = FloatingStreamWindow(self)
            window.bind_store(self._task_observation_store)
            window.closed.connect(self._on_stream_window_closed)
            self._floating_stream_window = window
            # Connect store changes to live-update the window when visible.
            self._task_observation_store.changed.connect(self._refresh_stream_window_if_visible)
        # Update with current state.
        state = self._task_observation_store.focus_for_scope(TaskFocusScope.GLOBAL)
        window.update_state(state)
        window.position_near_anchor(self._floating_task_companion)
        window.show()
        window.raise_()
        window.activateWindow()

    def _on_stream_window_closed(self) -> None:
        """Handle FloatingStreamWindow close — just hide, don't destroy."""
        pass

    def _refresh_stream_window_if_visible(self) -> None:
        """Update the floating stream window if it's currently visible."""
        window = self._floating_stream_window
        if window is not None and window.isVisible():
            state = self._task_observation_store.focus_for_scope(TaskFocusScope.GLOBAL)
            window.update_state(state)
