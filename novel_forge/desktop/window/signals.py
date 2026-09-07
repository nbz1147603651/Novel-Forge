"""Mixin module: signals methods for ``NovelForgeDesktopWindow``.

Auto-extracted in the M3.1 giant-file-split refactor. Methods live here so the
main :file:`window/__init__.py` facade can compose them via mixin inheritance.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import novel_forge.desktop.page_registrations as _page_registrations  # noqa: F401
from novel_forge.desktop.window._navigation import (
    connect_page_signals as _navigation_connect_page_signals,
)

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


class SignalsMixin:
    """Mixin that contributes the **signals** method group."""

    def _connect_signals(self) -> None:
        if not self._global_signals_connected:
            self._job_manager.jobs_changed.connect(self._schedule_bind_jobs)
            self._job_manager.job_completed.connect(self._handle_job_completed)
            self._job_manager.job_submitted.connect(self._bind_jobs)
            self._job_manager.token_update.connect(self._update_cost_label)
            self._job_manager.section_changed.connect(self._handle_job_section_changed)
            self._job_manager.decision_required.connect(self._handle_task_decision_required)
            self._global_signals_connected = True
        self._page_signal_connections_ready = True
        for page_id, page in self._pages.loaded_items():
            self._connect_page_signals(page_id, page)

    def _connect_page_signals(self, page_id: str, page: Any) -> None:
        _navigation_connect_page_signals(self, page_id, page)
