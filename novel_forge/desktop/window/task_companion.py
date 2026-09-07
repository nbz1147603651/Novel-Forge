"""Mixin module: task_companion methods for ``NovelForgeDesktopWindow``.

Auto-extracted in the M3.1 giant-file-split refactor. Methods live here so the
main :file:`window/__init__.py` facade can compose them via mixin inheritance.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QPoint,
)

import novel_forge.desktop.page_registrations as _page_registrations  # noqa: F401
from novel_forge.core.config import get_settings, get_writable_env_path, reset_settings
from novel_forge.desktop.components.task_focus import (
    FloatingTaskCompanion,
)
from novel_forge.desktop.config_store import DesktopSettingsStore

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


class TaskCompanionMixin:
    """Mixin that contributes the **task_companion** method group."""

    def _build_task_companion(self) -> None:
        root = self._workspace_root
        if root is None:
            return
        companion = FloatingTaskCompanion(root)
        companion.bind_store(self._task_observation_store)
        companion.set_user_visible(bool(get_settings().desktop_pet_visible))
        companion.activated.connect(self._toggle_pet_stream_window)
        companion.hide_requested.connect(self._hide_task_companion)
        companion.position_changed.connect(self._handle_task_companion_moved)
        companion.reset_position_requested.connect(self._reset_task_companion_position)
        self._floating_task_companion = companion
        self._position_task_companion()

    def _hide_task_companion(self) -> None:
        """Hide Nimo and persist the context-menu choice."""

        self._set_task_companion_visible(False)

    def _toggle_task_companion_visibility(self) -> None:
        """Toggle Nimo from its global keyboard shortcut."""

        self._set_task_companion_visible(not bool(get_settings().desktop_pet_visible))

    def _set_task_companion_visible(self, visible: bool) -> None:
        """Apply and persist Nimo visibility for the companion and next launch."""

        companion = self._floating_task_companion
        if companion is not None:
            companion.set_user_visible(visible)
        try:
            DesktopSettingsStore.merge_env(
                get_writable_env_path(),
                {"NOVEL_FORGE_DESKTOP_PET_VISIBLE": str(visible).lower()},
            )
            reset_settings()
        except OSError:
            _logger.warning("Could not persist desktop pet visibility", exc_info=True)

    def _position_task_companion(self) -> None:
        companion = self._floating_task_companion
        root = self._workspace_root
        if companion is None or root is None:
            return
        compact = (
            self._active_page_id == "chapter_studio"
            or self._layout_density == "compact"
            or root.width() < 560
            or root.height() < 420
        )
        companion.set_compact_mode(compact)
        margin = 12 if compact else 22
        if self._task_companion_position_ratio is not None:
            companion.move(self._task_companion_pos_from_ratio())
            companion.raise_()
            return
        x = max(4, root.width() - companion.width() - margin)
        y = max(4, root.height() - companion.height() - margin)
        companion.move(x, y)
        companion.raise_()

    def _task_companion_bounds(self) -> tuple[int, int, int, int]:
        companion = self._floating_task_companion
        root = self._workspace_root
        if companion is None or root is None:
            return (4, 4, 4, 4)
        min_x = 4
        min_y = 4
        max_x = max(min_x, root.width() - companion.width() - min_x)
        max_y = max(min_y, root.height() - companion.height() - min_y)
        return min_x, min_y, max_x, max_y

    def _clamp_task_companion_pos(self, pos: QPoint) -> QPoint:
        min_x, min_y, max_x, max_y = self._task_companion_bounds()
        return QPoint(min(max(pos.x(), min_x), max_x), min(max(pos.y(), min_y), max_y))

    def _task_companion_ratio_from_pos(self, pos: QPoint) -> tuple[float, float]:
        min_x, min_y, max_x, max_y = self._task_companion_bounds()
        width_span = max(1, max_x - min_x)
        height_span = max(1, max_y - min_y)
        clamped = self._clamp_task_companion_pos(pos)
        return (
            min(1.0, max(0.0, (clamped.x() - min_x) / width_span)),
            min(1.0, max(0.0, (clamped.y() - min_y) / height_span)),
        )

    def _task_companion_pos_from_ratio(self) -> QPoint:
        ratio = self._task_companion_position_ratio or (1.0, 1.0)
        min_x, min_y, max_x, max_y = self._task_companion_bounds()
        x = min_x + round((max_x - min_x) * min(1.0, max(0.0, ratio[0])))
        y = min_y + round((max_y - min_y) * min(1.0, max(0.0, ratio[1])))
        return self._clamp_task_companion_pos(QPoint(x, y))

    def _handle_task_companion_moved(self, pos: QPoint) -> None:
        companion = self._floating_task_companion
        if companion is None:
            return
        clamped = self._clamp_task_companion_pos(pos)
        self._task_companion_position_ratio = self._task_companion_ratio_from_pos(clamped)
        if companion.pos() != clamped:
            companion.move(clamped)
        companion.raise_()

    def _reset_task_companion_position(self) -> None:
        self._task_companion_position_ratio = None
        self._position_task_companion()
