"""Centralized timer lifecycle on page show/hide events.

Many desktop pages create periodic timers (animation spinners, watchdogs,
status rotation) and throttle timers (high-frequency signal coalescing).
Without central management, pages that are hidden but not destroyed keep
these timers running, burning CPU on invisible UI updates and - on macOS -
starving the Qt event loop enough to cause audio stutter.

Usage::

    class MyPage(PageVisibilityMixin, QWidget):
        def __init__(self):
            super().__init__()
            self._spinner_timer = QTimer(self)
            self._spinner_timer.setInterval(600)
            self._spinner_timer.timeout.connect(self._tick_spinner)
            # Register for automatic lifecycle management
            self._register_periodic_timer(self._spinner_timer)
            self._start_visibility_periodic_timer(self._spinner_timer)

            self._throttle = QTimer(self)
            self._throttle.setSingleShot(True)
            self._register_throttle_timer(self._throttle)

Subclasses do NOT need to override ``showEvent``/``hideEvent`` unless they
have additional work beyond timer management; the mixin calls
``super().showEvent()`` / ``super().hideEvent()`` so the rest of the MRO
chain is preserved.

Design notes:
- Periodic timers are stopped on hide and restarted on show (only if they
  were active before hide, so a page does not accidentally start a timer
  that was intentionally paused).
- Code paths that may start or stop a periodic timer while the page is hidden
  use ``_start_visibility_periodic_timer`` /
  ``_stop_visibility_periodic_timer`` so resume intent remains accurate.
- Throttle timers (single-shot coalescers) are only stopped on hide; they
  are NOT restarted on show because a pending throttle makes no sense once
  the page is invisible. They will naturally restart when the next signal
  arrives after the page becomes visible again. High-frequency handlers use
  ``_start_visibility_throttle_timer`` so hidden signals cannot restart them.
- ``shutdown()`` callers should still stop timers explicitly; this mixin
  only covers the show/hide lifecycle, not teardown.
"""

from __future__ import annotations

from typing import Any, cast

from PySide6.QtCore import QTimer
from PySide6.QtGui import QHideEvent, QShowEvent

__all__ = ("PageVisibilityMixin",)


class PageVisibilityMixin:
    """Mixin that centralizes QTimer lifecycle on widget show/hide.

    Subclasses register timers via :meth:`_register_periodic_timer` and
    :meth:`_register_throttle_timer` during ``__init__``.  The mixin then
    ensures periodic timers are paused while the page is hidden and
    resumed when it becomes visible again.

    The mixin is cooperative: it calls ``super().showEvent()`` and
    ``super().hideEvent()`` so it can be composed with any other mixin or
    ``QWidget`` in the MRO without swallowing their event handling.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Initialize the registries before super().__init__ so that any
        # signal connections made during base construction that might
        # fire early find the lists ready.
        self._visibility_periodic_timers: list[QTimer] = []
        self._visibility_throttle_timers: list[QTimer] = []
        # Track which periodic timers were active before hide so show can
        # restart only those that were running.  Throttle timers are
        # single-shot and are not restarted on show.
        self._visibility_periodic_active_before_hide: set[int] = set()
        super().__init__(*args, **kwargs)
        # A newly constructed stacked-page child normally starts hidden and
        # may not receive a hideEvent before background signals arrive.
        self._visibility_page_hidden = not cast(Any, self).isVisible()

    # ─── Registration ────────────────────────────────────────────────────

    def _register_periodic_timer(self, timer: QTimer) -> QTimer:
        """Register a periodic timer for automatic pause/resume on hide/show.

        Periodic timers (those without ``setSingleShot(True)``) are stopped
        when the page is hidden and restarted when it becomes visible again,
        but only if they were active before being hidden.
        """
        if not any(item is timer for item in self._visibility_periodic_timers):
            self._visibility_periodic_timers.append(timer)
        if self._visibility_page_hidden and timer.isActive():
            self._visibility_periodic_active_before_hide.add(id(timer))
            timer.stop()
        return timer

    def _register_throttle_timer(self, timer: QTimer) -> QTimer:
        """Register a single-shot throttle timer for automatic stop on hide.

        Throttle timers are stopped when the page is hidden but are NOT
        restarted on show - a pending throttle for an invisible page is
        meaningless.  The next signal after the page becomes visible will
        naturally restart the throttle.
        """
        if not any(item is timer for item in self._visibility_throttle_timers):
            self._visibility_throttle_timers.append(timer)
        if self._visibility_page_hidden and timer.isActive():
            timer.stop()
        return timer

    def _start_visibility_periodic_timer(
        self,
        timer: QTimer,
        interval_ms: int | None = None,
    ) -> None:
        """Start a registered periodic timer, or defer it until the page is shown."""
        if self._visibility_page_hidden:
            self._visibility_periodic_active_before_hide.add(id(timer))
            return
        self._visibility_periodic_active_before_hide.discard(id(timer))
        if interval_ms is None:
            timer.start()
        else:
            timer.start(interval_ms)

    def _stop_visibility_periodic_timer(self, timer: QTimer) -> None:
        """Stop a registered periodic timer and cancel any deferred resume."""
        self._visibility_periodic_active_before_hide.discard(id(timer))
        timer.stop()

    def _start_visibility_throttle_timer(self, timer: QTimer) -> None:
        """Start a UI coalescer only while its owning page is visible."""
        if not self._visibility_page_hidden and not timer.isActive():
            timer.start()

    # ─── Qt event overrides ──────────────────────────────────────────────

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt override
        """Restart periodic timers that were active before the page was hidden."""
        super().showEvent(event)  # type: ignore[misc]
        self._visibility_page_hidden = False
        for timer in self._visibility_periodic_timers:
            # Use id() as a stable identity key since QTimer is not hashable
            # by default and we only need to remember "was it running".
            if id(timer) in self._visibility_periodic_active_before_hide:
                if not timer.isActive():
                    timer.start()
        self._visibility_periodic_active_before_hide.clear()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802 - Qt override
        """Stop all periodic and throttle timers while the page is invisible."""
        self._visibility_page_hidden = True
        for timer in self._visibility_periodic_timers:
            if timer.isActive():
                self._visibility_periodic_active_before_hide.add(id(timer))
                timer.stop()
        for timer in self._visibility_throttle_timers:
            if timer.isActive():
                timer.stop()
        super().hideEvent(event)  # type: ignore[misc]
