"""Global keyboard shortcut registry for the desktop shell.

Provides a declarative shortcut table and a helper to register keyboard
actions on the main window. Navigation uses ``QShortcut`` instances; Nimo's
global shortcut is handled at the application event level for consistent
focus handling. Shortcuts are grouped by scope:

- **navigation**: page switching (Ctrl+1~4)
- **app**: global actions (save, settings)
- **page**: per-page actions registered via ``PageRegistry``

Usage::

    from novel_forge.desktop.shortcuts import register_global_shortcuts

    register_global_shortcuts(window)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShortcutDescriptor:
    """Immutable description of a keyboard shortcut."""

    key: str
    sequence: str
    description: str
    scope: str = "app"  # "app" | "navigation" | "page"


# ── Built-in shortcut table ───────────────────────────────────────────
# Sequences follow Qt key notation (e.g. "Ctrl+1", "Ctrl+Shift+Z").

BUILTIN_SHORTCUTS: list[ShortcutDescriptor] = [
    # Navigation
    ShortcutDescriptor("nav.dashboard", "Ctrl+1", "切换到案头", scope="navigation"),
    ShortcutDescriptor("nav.projects", "Ctrl+2", "切换到卷帙", scope="navigation"),
    ShortcutDescriptor("nav.workflow", "Ctrl+3", "切换到机杼", scope="navigation"),
    ShortcutDescriptor("nav.chapter_studio", "Ctrl+4", "切换到章台", scope="navigation"),
    ShortcutDescriptor("nav.settings", "Ctrl+,", "切换到火候", scope="navigation"),
    ShortcutDescriptor("pet.toggle", "Ctrl+Alt+N", "隐藏或召回 Nimo"),
]

NIMO_SHORTCUT = "Ctrl+Alt+N"


_TEXT_EDIT_FOCUS_TYPES = (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox)


def _is_text_editing_focus(window: Any) -> bool:
    """Return whether focus is inside a text-editing control for *window*."""

    app = QApplication.instance()
    if app is None:
        return False
    focused = app.focusWidget()
    if focused is None:
        return False
    if isinstance(window, QWidget) and focused is not window and not window.isAncestorOf(focused):
        return False
    widget: QWidget | None = focused
    while widget is not None:
        if isinstance(widget, _TEXT_EDIT_FOCUS_TYPES):
            return True
        widget = widget.parentWidget()
    return False


def _navigation_handler(window: Any, page_id: str) -> Callable[[], None]:
    """Return a zero-arg callable that switches to *page_id*."""

    def _handler() -> None:
        if _is_text_editing_focus(window):
            return
        switch = getattr(window, "switch_page", None)
        if callable(switch):
            switch(page_id)

    return _handler


class _NimoShortcutFilter(QObject):
    """Handle Nimo's global shortcut independently of the focused widget."""

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self._window = window

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.KeyPress or not isinstance(event, QKeyEvent):
            return False
        required = (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
        )
        if event.key() != Qt.Key.Key_N or event.modifiers() & required != required:
            return False
        toggle = getattr(self._window, "_toggle_task_companion_visibility", None)
        if callable(toggle):
            toggle()
            return True
        return False


def _register_nimo_shortcut(window: Any) -> None:
    """Install the Nimo keyboard listener once for this window."""

    if getattr(window, "_nimo_shortcut_filter", None) is not None:
        return
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return
    shortcut_filter = _NimoShortcutFilter(window)
    app.installEventFilter(shortcut_filter)
    window._nimo_shortcut_filter = shortcut_filter


_NAV_PAGE_IDS: dict[str, str] = {
    "nav.dashboard": "dashboard",
    "nav.projects": "projects",
    "nav.workflow": "workflow",
    "nav.chapter_studio": "chapter_studio",
    "nav.settings": "settings",
}


def register_global_shortcuts(window: Any) -> list[QShortcut]:
    """Register navigation shortcuts and Nimo's global listener on *window*.

    Returns the created navigation ``QShortcut`` instances (owned by *window*).
    """
    created: list[QShortcut] = []
    for desc in BUILTIN_SHORTCUTS:
        if desc.scope == "navigation" and desc.key in _NAV_PAGE_IDS:
            page_id = _NAV_PAGE_IDS[desc.key]
            shortcut = QShortcut(QKeySequence(desc.sequence), window)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(_navigation_handler(window, page_id))
            created.append(shortcut)
        else:
            _logger.debug("Shortcut %s (%s) not yet wired", desc.key, desc.sequence)
    _register_nimo_shortcut(window)
    _logger.debug("Registered %d global shortcuts", len(created))
    return created
