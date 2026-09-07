"""Tests for desktop global shortcut registration."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget

from novel_forge.desktop.shortcuts import (
    BUILTIN_SHORTCUTS,
    NIMO_SHORTCUT,
    _is_text_editing_focus,
    register_global_shortcuts,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _ShortcutWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.switched_pages: list[str] = []
        self.pet_toggle_count = 0

    def switch_page(self, page_id: str) -> None:
        self.switched_pages.append(page_id)

    def _toggle_task_companion_visibility(self) -> None:
        self.pet_toggle_count += 1


def test_register_global_shortcuts_wires_navigation(qapp: QApplication) -> None:
    window = _ShortcutWindow()
    shortcuts = register_global_shortcuts(window)

    assert len(shortcuts) == len(BUILTIN_SHORTCUTS) - 1
    assert all(
        shortcut.context() == Qt.ShortcutContext.ApplicationShortcut
        for shortcut in shortcuts
    )

    for shortcut in shortcuts:
        shortcut.activated.emit()

    assert window.switched_pages == [
        "dashboard",
        "projects",
        "workflow",
        "chapter_studio",
        "settings",
    ]


def test_nimo_shortcut_toggles_the_pet(qapp: QApplication) -> None:
    window = _ShortcutWindow()
    window.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    window.show()
    window.activateWindow()
    window.setFocus()
    register_global_shortcuts(window)
    descriptor = next(item for item in BUILTIN_SHORTCUTS if item.key == "pet.toggle")

    assert NIMO_SHORTCUT == "Ctrl+Alt+N"
    assert descriptor.sequence == NIMO_SHORTCUT

    QTest.keyClick(
        window,
        Qt.Key.Key_N,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier,
    )
    qapp.processEvents()

    assert window.pet_toggle_count == 1


def test_navigation_shortcuts_ignore_text_edit_focus(qapp: QApplication) -> None:
    window = _ShortcutWindow()
    editor = QLineEdit(window)
    window.show()
    editor.setFocus()
    qapp.processEvents()

    assert _is_text_editing_focus(window) is True

    shortcuts = register_global_shortcuts(window)
    shortcuts[0].activated.emit()

    assert window.switched_pages == []
