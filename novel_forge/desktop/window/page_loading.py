"""Mixin module: page_loading methods for ``NovelForgeDesktopWindow``.

Auto-extracted in the M3.1 giant-file-split refactor. Methods live here so the
main :file:`window/__init__.py` facade can compose them via mixin inheritance.
"""

from __future__ import annotations

import importlib
import logging

from PySide6.QtCore import (
    QRunnable,
    QSize,
)
from PySide6.QtWidgets import (
    QWidget,
)

import novel_forge.desktop.page_registrations as _page_registrations  # noqa: F401
from novel_forge.desktop.constants import (
    COMPACT_HEIGHT_THRESHOLD,
    COMPACT_WIDTH_THRESHOLD,
    WINDOW_DEFAULT_MIN_HEIGHT,
    WINDOW_DEFAULT_MIN_WIDTH,
    WINDOW_DEFAULT_START_HEIGHT,
    WINDOW_DEFAULT_START_WIDTH,
    WINDOW_SCREEN_HEIGHT_RATIO,
    WINDOW_SCREEN_WIDTH_RATIO,
)
from novel_forge.desktop.thread_pools import desktop_thread_pools
from novel_forge.desktop.ui_perf import ui_perf_span
from novel_forge.desktop.window._navigation import ensure_page as _navigation_ensure_page

_logger = logging.getLogger(__name__)

_WINDOW_DEFAULT_MIN_WIDTH = WINDOW_DEFAULT_MIN_WIDTH
_WINDOW_DEFAULT_MIN_HEIGHT = WINDOW_DEFAULT_MIN_HEIGHT
_WINDOW_DEFAULT_START_WIDTH = WINDOW_DEFAULT_START_WIDTH
_WINDOW_DEFAULT_START_HEIGHT = WINDOW_DEFAULT_START_HEIGHT
_WINDOW_SCREEN_WIDTH_RATIO = WINDOW_SCREEN_WIDTH_RATIO
_WINDOW_SCREEN_HEIGHT_RATIO = WINDOW_SCREEN_HEIGHT_RATIO
_COMPACT_WIDTH_THRESHOLD = COMPACT_WIDTH_THRESHOLD
_COMPACT_HEIGHT_THRESHOLD = COMPACT_HEIGHT_THRESHOLD


class _ModulePreloadRunnable(QRunnable):
    """Import a page's Python module away from the GUI event loop.

    The page itself must still be constructed on the GUI thread because it
    creates Qt widgets.  Importing the module is pure Python setup, however,
    and can safely happen in the auxiliary pool so the first navigation does
    not pay the import cost synchronously.
    """

    def __init__(self, module_name: str) -> None:
        super().__init__()
        self._module_name = module_name
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            importlib.import_module(self._module_name)
        except Exception:
            _logger.debug(
                "Deferred page module preload failed: %s", self._module_name, exc_info=True
            )


class PageLoadingMixin:
    """Mixin that contributes the **page_loading** method group."""

    def _ensure_page(
        self,
        page_id: str,
        *,
        bind_workspace: bool = True,
        bind_jobs: bool = True,
        perf_label: str = "switch_page.ensure_page",
    ) -> QWidget | None:
        """Create and return a page only when it is actually needed."""
        return _navigation_ensure_page(
            self,
            page_id,
            bind_workspace=bind_workspace,
            bind_jobs=bind_jobs,
            perf_label=perf_label,
        )

    def _schedule_idle_page_prewarm(self) -> None:
        """Pre-create heavy pages after the dashboard has painted and gone idle."""
        if not self._window_callbacks_allowed():
            return
        self._schedule_idle_page_module_preload()
        if self._page_prewarm_done or self._page_prewarm_timer.isActive():
            return
        if self.isMinimized() or self._active_page_id != "dashboard":
            return
        self._page_prewarm_queue = [
            page_id
            for page_id in self._PREWARM_PAGE_IDS
            if page_id in self._pages and self._pages.get(page_id) is None
        ]
        if not self._page_prewarm_queue:
            self._page_prewarm_done = True
            return
        self._page_prewarm_timer.start(self._PREWARM_INITIAL_DELAY_MS)

    def _schedule_idle_page_module_preload(self) -> None:
        """Queue pure-Python page imports without blocking the first frame."""
        if not self._window_callbacks_allowed():
            return
        if self.isMinimized() or self._active_page_id != "dashboard":
            return
        if self._page_preload_done or self._page_preload_timer.isActive():
            return
        if not self._page_preload_queue:
            self._page_preload_done = True
            return
        self._page_preload_timer.start(self._PAGE_PRELOAD_INITIAL_DELAY_MS)

    def _preload_next_page_module(self) -> None:
        if self._page_preload_done:
            return
        if self.isMinimized() or self._active_page_id != "dashboard":
            return
        if not self._page_preload_queue:
            self._page_preload_done = True
            return

        module_name = self._page_preload_queue.pop(0)
        desktop_thread_pools().aux_pool.start(_ModulePreloadRunnable(module_name))
        if self._page_preload_queue:
            self._page_preload_timer.start(self._PAGE_PRELOAD_STEP_DELAY_MS)
        else:
            self._page_preload_done = True

    def _prewarm_next_page(self) -> None:
        if self._page_prewarm_done:
            return
        if self.isMinimized() or self._active_page_id != "dashboard":
            return
        if not self._page_prewarm_queue:
            self._page_prewarm_done = True
            return

        page_id = self._page_prewarm_queue.pop(0)
        self._prewarm_page(page_id)
        if self._page_prewarm_queue:
            self._page_prewarm_timer.start(self._PREWARM_STEP_DELAY_MS)
        else:
            self._page_prewarm_done = True

    def _prewarm_page(self, page_id: str) -> None:
        """Instantiate a page without binding workspace or job data."""
        with ui_perf_span("page_prewarm", page_id=page_id):
            # Prewarming should create only the page's lazy shell.  Without
            # this lifecycle marker, ensure_page() treats prewarm like a test
            # helper and forces every deferred section to build synchronously.
            previous_deferred_state = getattr(self, "_deferred_page_creation_active", False)
            self._deferred_page_creation_active = True
            try:
                page = self._ensure_page(
                    page_id,
                    bind_workspace=False,
                    bind_jobs=False,
                    perf_label="page_prewarm.ensure_page",
                )
            finally:
                self._deferred_page_creation_active = previous_deferred_state
            if page is not None:
                self._warm_page_render(page_id, page)

    # Pages too heavy for prewarm grab(); layout-only polish is sufficient.
    _SKIP_GRAB_PAGE_IDS: frozenset[str] = frozenset({"chapter_studio"})

    def _warm_page_render(self, page_id: str, page: QWidget) -> None:
        """Force one offscreen polish/layout/render pass for a prewarmed page.

        Heavy pages (``chapter_studio``) skip the expensive ``grab()`` call
        and the ``findChildren`` deep-polish; they only get a layout
        activation.  The actual grab is deferred to the first real switch.
        """
        with ui_perf_span("page_prewarm.render", page_id=page_id):
            try:
                prewarm_visuals = getattr(page, "prewarm_visuals", None)
                if callable(prewarm_visuals):
                    prewarm_visuals()

                target_size = self._stack.size() if hasattr(self, "_stack") else QSize()
                if not target_size.isValid() or target_size.isEmpty():
                    target_size = page.sizeHint()
                if target_size.isValid() and not target_size.isEmpty():
                    page.resize(target_size)

                page.ensurePolished()
                layout = page.layout()
                if layout is not None:
                    layout.activate()

                if page_id not in self._SKIP_GRAB_PAGE_IDS:
                    # Light pages: polish direct children and grab once.
                    if layout is not None:
                        for i in range(layout.count()):
                            item = layout.itemAt(i)
                            if item is not None:
                                w = item.widget()
                                if w is not None:
                                    w.ensurePolished()
                    if page.width() > 0 and page.height() > 0:
                        page.grab()
            except RuntimeError:
                return
