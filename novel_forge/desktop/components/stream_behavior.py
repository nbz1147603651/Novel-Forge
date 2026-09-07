"""Shared scroll-follow behavior for live, vertically scrolling output.

Live output should stay attached to its newest line only while the reader is
already reading there.  The controller intentionally treats wheel, keyboard,
and scrollbar actions as an explicit request to inspect another point, and
resumes following only after the reader returns to the bottom.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QAbstractScrollArea


class StreamFollowController(QObject):
    """Keep a live-output viewport at its newest line unless the user scrolls.

    ``notify_content_changed`` is safe to call after every render.  It moves
    to the latest line immediately and once more on the next event-loop turn,
    which covers layouts whose scrollbar range grows after a label/document
    recalculates its height.
    """

    _BOTTOM_TOLERANCE_PX = 2

    def __init__(self, scroll_area: QAbstractScrollArea) -> None:
        super().__init__(scroll_area)
        self._scroll_area = scroll_area
        self._scrollbar = scroll_area.verticalScrollBar()
        self._following_latest = True
        self._position_check_queued = False
        self._scroll_flush_queued = False
        self._programmatic_scroll = False

        self._scrollbar.sliderPressed.connect(self._pause_for_user_review)
        self._scrollbar.sliderMoved.connect(self._pause_for_user_review)
        self._scrollbar.sliderReleased.connect(self._queue_user_position_check)
        self._scrollbar.actionTriggered.connect(self._queue_user_position_check)
        self._scrollbar.valueChanged.connect(self._handle_value_changed)

    @property
    def following_latest(self) -> bool:
        """Whether incoming output is currently allowed to move the viewport."""

        return self._following_latest

    def reset_to_latest(self) -> None:
        """Use when switching to another stream or clearing the current one."""

        self._following_latest = True

    def capture_content_anchor(self) -> tuple[bool, int]:
        """Capture the reader's intent before a renderer replaces its document.

        Some views use ``setHtml`` (or clear-and-insert) for each stream
        refresh.  Those operations reset the scroll range, so callers need to
        capture this state *before* the replacement and restore it afterwards.
        A reader reviewing earlier output retains their absolute scroll offset;
        a reader already at the newest output remains attached to the bottom.
        """

        try:
            following_latest = self._following_latest and self._is_at_latest()
            self._following_latest = following_latest
            return following_latest, self._scrollbar.value()
        except RuntimeError:
            # A queued refresh may race with widget teardown.  Treat the
            # deleted viewport as paused rather than trying to move it.
            return False, 0

    def restore_content_anchor(self, anchor: tuple[bool, int]) -> None:
        """Restore an anchor captured by :meth:`capture_content_anchor`."""

        following_latest, previous_value = anchor
        try:
            self._following_latest = following_latest
            self._programmatic_scroll = True
            try:
                target = (
                    self._scrollbar.maximum()
                    if following_latest
                    else min(previous_value, self._scrollbar.maximum())
                )
                self._scrollbar.setValue(target)
            finally:
                self._programmatic_scroll = False
        except RuntimeError:
            return
        if following_latest:
            self._queue_scroll_flush()

    def notify_content_changed(self) -> None:
        """Follow the newest rendered output when the reader has not opted out."""

        if not self._following_latest:
            return
        self._scroll_to_latest()
        self._queue_scroll_flush()

    def _queue_scroll_flush(self) -> None:
        """Schedule one post-layout bottom alignment for an active follow."""

        if self._scroll_flush_queued:
            return
        self._scroll_flush_queued = True
        QTimer.singleShot(0, self._flush_scroll_to_latest)

    def _pause_for_user_review(self, *_args: Any) -> None:
        # Pressing/dragging the scrollbar is an explicit navigation action.
        # A release at the bottom will immediately re-enable following.
        self._following_latest = False

    def _queue_user_position_check(self, *_args: Any) -> None:
        # ``actionTriggered`` is emitted for track clicks, wheel scrolling,
        # and keyboard navigation before the scrollbar value is updated.
        # Pause immediately so a same-turn stream update cannot steal focus.
        self._following_latest = False
        if self._position_check_queued:
            return
        self._position_check_queued = True
        QTimer.singleShot(0, self._sync_following_from_position)

    def _handle_value_changed(self, _value: int) -> None:
        """Keep the follow state in sync with every scrollbar position change.

        Not every user navigation path emits the slider signals above (for
        example, some platform-specific wheel/track interactions).  The
        value itself is the reliable source of truth: leaving the bottom
        pauses follow mode, and returning to the bottom resumes it.
        """

        if self._programmatic_scroll:
            return
        try:
            self._following_latest = self._is_at_latest()
        except RuntimeError:
            return

    def _sync_following_from_position(self) -> None:
        self._position_check_queued = False
        try:
            self._following_latest = self._is_at_latest()
        except RuntimeError:
            # The parent viewport can be deleted while a queued callback is
            # pending during dialog teardown.
            return

    def _flush_scroll_to_latest(self) -> None:
        self._scroll_flush_queued = False
        if not self._following_latest:
            return
        try:
            self._scroll_to_latest()
        except RuntimeError:
            return

    def _scroll_to_latest(self) -> None:
        self._programmatic_scroll = True
        try:
            self._scrollbar.setValue(self._scrollbar.maximum())
        finally:
            self._programmatic_scroll = False

    def _is_at_latest(self) -> bool:
        return self._scrollbar.value() >= max(
            0,
            self._scrollbar.maximum() - self._BOTTOM_TOLERANCE_PX,
        )
