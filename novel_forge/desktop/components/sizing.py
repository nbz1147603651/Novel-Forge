"""Responsive sizing helpers for desktop dialogs and floating windows."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QWidget


def smart_dialog_size(
    parent: QWidget | None,
    desired_width: int = 1040,
    desired_height: int = 680,
    *,
    max_screen_fraction: float = 0.85,
) -> tuple[int, int]:
    """Return desired dialog size clamped to the relevant screen.

    Prefer the screen that owns *parent* so dialogs opened on a secondary
    monitor are sized against that monitor rather than the primary display.
    """
    app = QApplication.instance()
    if app is None:
        return desired_width, desired_height

    screen = None
    if parent is not None:
        try:
            window = parent.windowHandle()
            if window is not None:
                screen = window.screen()
        except RuntimeError:
            screen = None
        if screen is None:
            try:
                screen = parent.screen()
            except RuntimeError:
                screen = None
        if screen is None:
            try:
                screen = app.screenAt(parent.mapToGlobal(parent.rect().center()))
            except RuntimeError:
                screen = None

    if screen is None:
        screen = app.primaryScreen()
    if screen is None:
        return desired_width, desired_height

    fraction = max(0.1, min(1.0, float(max_screen_fraction)))
    available = screen.availableGeometry()
    max_w = max(1, int(available.width() * fraction))
    max_h = max(1, int(available.height() * fraction))
    return min(desired_width, max_w), min(desired_height, max_h)
