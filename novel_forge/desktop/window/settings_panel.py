"""Mixin module: settings_panel methods for ``NovelForgeDesktopWindow``.

Auto-extracted in the M3.1 giant-file-split refactor. Methods live here so the
main :file:`window/__init__.py` facade can compose them via mixin inheritance.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QApplication

import novel_forge.desktop.page_registrations as _page_registrations  # noqa: F401
from novel_forge.core.config import get_settings, reset_settings
from novel_forge.desktop.theme.runtime import apply_desktop_theme

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


class SettingsPanelMixin:
    """Mixin that contributes the **settings_panel** method group."""

    def _handle_mock_toggled(self, checked: bool) -> None:
        self._mock_enabled = checked
        self._workspace_needs_reload = True
        self.refresh_workspace(force=True)

    def _on_settings_saved(self) -> None:
        reset_settings()
        app = QApplication.instance()
        if isinstance(app, QApplication):
            root = getattr(self, "_workspace_root", None)
            apply_desktop_theme(getattr(get_settings(), "desktop_theme", None), app=app, root=root)
        companion = getattr(self, "_floating_task_companion", None)
        if companion is not None:
            companion.set_user_visible(bool(get_settings().desktop_pet_visible))
        self._notification_sounds.reload_settings()
        self._workspace_needs_reload = True
        self._safe_deferred(0, lambda: self.refresh_workspace(force=True))
