"""Tests for keeping 拟人化资料库 inside the 火候 page.

The library dashboard is launched from the 火候 page's local card. It must not
appear as a secondary item in the main side rail.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QWidget  # noqa: E402

from novel_forge.desktop.pages.settings.page import SettingsPage  # noqa: E402
from novel_forge.desktop.window import NovelForgeDesktopWindow  # noqa: E402


def _make_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_window_does_not_expose_humanize_sidebar_slots() -> None:
    """The window should not own sidebar-level humanize launch slots."""
    _make_app()
    assert not hasattr(NovelForgeDesktopWindow, "_toggle_humanize_secondary")
    assert not hasattr(NovelForgeDesktopWindow, "_open_humanize_library_dialog")


def test_humanize_secondary_row_absent_after_build() -> None:
    """After _build_side_rail, no secondary row should be inserted."""
    _make_app()
    window = NovelForgeDesktopWindow.__new__(NovelForgeDesktopWindow)
    # Skip QMainWindow __init__; build only the rail via the existing helper
    from PySide6.QtWidgets import QMainWindow

    QMainWindow.__init__(window)
    # We don't need a full init — just check _build_side_rail avoids the row.
    window._nav_buttons = {}
    rail = window._build_side_rail()
    assert not hasattr(window, "_humanize_secondary_row")
    btns = rail.findChildren(QPushButton)
    labels = [b.text() for b in btns]
    assert "拟人化资料库" not in labels
    rail.deleteLater()


def test_settings_public_humanize_launcher_builds_lazy_card(monkeypatch) -> None:  # noqa: ANN001
    """The public launcher must work before deferred settings sections are built."""
    _make_app()

    class FakeHumanizeDashboard(QWidget):
        stats_refreshed = Signal(dict)

    monkeypatch.setattr(
        "novel_forge.desktop.pages.settings.page.HumanizeLibraryDashboardPage",
        FakeHumanizeDashboard,
    )
    monkeypatch.setattr(QDialog, "show", lambda self: None)

    page = SettingsPage(eager_build=False)
    assert not hasattr(page, "_humanize_library_card")

    page.open_humanize_library_dialog()

    assert hasattr(page, "_humanize_library_card")
    page.shutdown()
