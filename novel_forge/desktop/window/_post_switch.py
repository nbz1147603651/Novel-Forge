"""Post-switch sequencer for deferred page-switch work.

Replaces the previous pattern of 3 independent ``QTimer.singleShot`` calls
with a single deterministic sequence: workspace_bind → 16 ms → jobs_bind →
activate.  Each step is guarded by a generation check so a newer switch
cancels the previous sequence.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget

from novel_forge.desktop.ui_perf import ui_perf_span

if TYPE_CHECKING:
    from novel_forge.desktop.window import NovelForgeDesktopWindow

_logger = logging.getLogger(__name__)


class _PostSwitchSequencer:
    """Deterministic sequencer for post-page-switch work.

    Steps execute in order via a single QTimer:
      0. workspace bind  (after a 16 ms first-paint gap)
      1. jobs bind + focus deep-link  (16 ms gap)
      2. page activation  (0 ms)

    A new switch creates a new sequencer, which replaces and cancels the
    previous one via :meth:`cancel`.
    """

    def __init__(
        self,
        owner: "NovelForgeDesktopWindow",
        page_id: str,
        generation: int,
        focus_tab: str | None,
    ) -> None:
        self._owner = owner
        self._page_id = page_id
        self._generation = generation
        self._focus_tab = focus_tab
        self._cancelled = False
        self._timer = QTimer(owner)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        self._step = 0

    def start(self) -> None:
        """Begin after the newly selected page has had one paint turn."""
        self._timer.start(16)

    def cancel(self) -> None:
        """Cancel any pending steps."""
        self._cancelled = True
        self._dispose()

    # ── internal ──────────────────────────────────────────────────────

    def _still_current(self) -> bool:
        if self._cancelled:
            return False
        return self._owner._post_switch_current(self._page_id, self._generation)

    def _on_timeout(self) -> None:
        if not self._still_current():
            self._dispose(clear_owner=True)
            return
        try:
            if self._step == 0:
                self._do_workspace_bind()
            elif self._step == 1:
                self._do_jobs_bind_and_focus()
            elif self._step == 2:
                self._do_activate()
                self._dispose(clear_owner=True)
                return  # terminal step, no reschedule
        except Exception:
            step_names = {0: "workspace bind", 1: "jobs bind", 2: "activate"}
            step_label = step_names.get(self._step, f"step {self._step}")
            _logger.exception("Post-switch %s failed for %s", step_label, self._page_id)
            # Surface the failure to the user instead of silently terminating.
            try:
                from novel_forge.desktop.components.toast import show_toast

                show_toast(
                    f"页面切换后处理失败（{step_label}），部分数据可能未加载",
                    variant="warning",
                    duration=5000,
                )
            except Exception:
                pass  # Don't let toast failure mask the original error
            self._dispose(clear_owner=True)
            return
        self._step += 1
        delay = 16 if self._step == 1 else 0
        self._timer.start(delay)

    def _dispose(self, *, clear_owner: bool = False) -> None:
        try:
            self._timer.stop()
            self._timer.timeout.disconnect(self._on_timeout)
            self._timer.deleteLater()
        except (RuntimeError, TypeError):
            pass
        if clear_owner and getattr(self._owner, "_post_switch_sequencer", None) is self:
            self._owner._post_switch_sequencer = None

    def _do_workspace_bind(self) -> None:
        with ui_perf_span("switch_page.workspace_bind", page_id=self._page_id):
            self._owner._bind_pending_or_stale_page(self._page_id)

    def _do_jobs_bind_and_focus(self) -> None:
        with ui_perf_span("switch_page.jobs_bind", page_id=self._page_id):
            self._owner._bind_jobs_for_page(self._page_id, self._owner._latest_jobs)
        if self._focus_tab:
            with ui_perf_span("switch_page.deep_link", page_id=self._page_id):
                self._owner._apply_switch_focus_tab(self._page_id, self._focus_tab)
        with ui_perf_span("switch_page.top_actions", page_id=self._page_id):
            self._owner._refresh_top_actions_for(self._page_id)

    def _do_activate(self) -> None:
        page = self._owner._pages.get(self._page_id)
        if page is not None:
            self._owner._schedule_page_activation(self._page_id, cast(QWidget, page))
