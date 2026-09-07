"""Infrastructure smoke tests for desktop UI testing."""

from __future__ import annotations


def test_qtbot_available(qtbot) -> None:
    """QtBot fixture is available."""
    assert qtbot is not None


def test_surface_creation(desktop_app) -> None:
    """Surface widget can be created."""
    from novel_forge.desktop.components import Surface

    s = Surface()
    assert s is not None
    s.deleteLater()
