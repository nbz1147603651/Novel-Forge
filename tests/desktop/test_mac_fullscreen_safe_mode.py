"""Tests for macOS fullscreen safe mode (I-1)."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_darwin(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")


def test_is_native_fullscreen_true_when_isFullScreen(mock_darwin):
    from novel_forge.desktop.window import navigation as nav

    win = MagicMock()
    win.isFullScreen.return_value = True
    win.windowHandle.return_value = None
    assert nav.NavigationMixin._is_native_fullscreen_active(win) is True


def test_is_native_fullscreen_true_when_QWindow_FullScreen(mock_darwin):
    from PySide6.QtGui import QWindow

    from novel_forge.desktop.window import navigation as nav

    win = MagicMock()
    win.isFullScreen.return_value = False
    handle = MagicMock()
    handle.visibility.return_value = QWindow.Visibility.FullScreen
    win.windowHandle.return_value = handle
    assert nav.NavigationMixin._is_native_fullscreen_active(win) is True


def test_is_native_fullscreen_false_when_maximized_only(mock_darwin):
    from PySide6.QtGui import QWindow

    from novel_forge.desktop.window import navigation as nav

    win = MagicMock()
    win.isFullScreen.return_value = False
    handle = MagicMock()
    handle.visibility.return_value = QWindow.Visibility.Maximized
    win.windowHandle.return_value = handle
    assert nav.NavigationMixin._is_native_fullscreen_active(win) is False


def test_is_native_fullscreen_false_on_non_darwin(monkeypatch):
    from novel_forge.desktop.window import navigation as nav

    # unique: tests non-darwin path, do not use mock_darwin fixture
    # Force the navigation module's view of sys.platform to be non-darwin
    # so the early-return short-circuit is exercised on macOS CI as well.
    monkeypatch.setattr(nav.sys, "platform", "linux")

    win = MagicMock()
    win.isFullScreen.return_value = True
    assert nav.NavigationMixin._is_native_fullscreen_active(win) is False


def test_enter_safe_mode_sets_flag_and_preserves_shadow(mock_darwin, qtbot):
    """Safe mode must preserve QGraphicsDropShadowEffect."""
    from PySide6.QtWidgets import QGraphicsDropShadowEffect, QLabel

    from novel_forge.desktop.window import navigation as nav

    win = MagicMock(spec=nav.NavigationMixin)
    win._is_native_fullscreen_active.return_value = True
    win._previous_widget = None
    win._stack = MagicMock()
    win._stack.currentWidget.return_value = None
    win._top_eyebrow = None
    win._top_title = None
    win._top_subtitle = None

    label = QLabel("x")
    qtbot.addWidget(label)
    shadow = QGraphicsDropShadowEffect(label)
    label.setGraphicsEffect(shadow)
    win._top_title = label

    nav.NavigationMixin._enter_mac_fullscreen_safe_mode(win)

    assert win._mac_fullscreen_safe_mode_active is True
    assert getattr(win, "_mac_fullscreen_at_switch_start_time", 0) > 0
    assert label.graphicsEffect() is shadow  # 保留


def test_enter_safe_mode_clears_opacity_effect(mock_darwin, qtbot):
    """QGraphicsOpacityEffect should be cleared."""
    from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel

    from novel_forge.desktop.window import navigation as nav

    win = MagicMock(spec=nav.NavigationMixin)
    win._is_native_fullscreen_active.return_value = True
    win._previous_widget = None
    win._stack = MagicMock()
    win._stack.currentWidget.return_value = None
    win._top_eyebrow = None
    win._top_title = None
    win._top_subtitle = None

    label = QLabel("x")
    qtbot.addWidget(label)
    opacity = QGraphicsOpacityEffect(label)
    label.setGraphicsEffect(opacity)
    win._top_title = label

    nav.NavigationMixin._enter_mac_fullscreen_safe_mode(win)

    assert label.graphicsEffect() is None


def test_enter_safe_mode_noop_on_non_fullscreen(mock_darwin):
    from novel_forge.desktop.window import navigation as nav

    win = MagicMock(spec=nav.NavigationMixin)
    win._is_native_fullscreen_active.return_value = False

    result = nav.NavigationMixin._enter_mac_fullscreen_safe_mode(win)
    assert result is False
    assert getattr(win, "_mac_fullscreen_safe_mode_active", False) is False


def test_enter_safe_mode_noop_on_non_darwin(monkeypatch):
    from novel_forge.desktop.window import navigation as nav

    monkeypatch.setattr(sys, "platform", "linux")
    win = MagicMock()
    win._is_native_fullscreen_active.return_value = True  # ignored on non-darwin

    result = nav.NavigationMixin._enter_mac_fullscreen_safe_mode(win)
    assert result is False


def test_exit_safe_mode_restores_fullscreen_when_dropped(mock_darwin):
    import time as _time

    from novel_forge.desktop.window import navigation as nav

    win = MagicMock()
    win._mac_fullscreen_safe_mode_active = True
    win._mac_fullscreen_at_switch_start_time = _time.monotonic() - 1.0
    win._post_switch_current.return_value = True
    win._is_native_fullscreen_active.return_value = False
    win.isVisible.return_value = True

    nav.NavigationMixin._exit_mac_fullscreen_safe_mode(win, "dashboard", 1)

    win.showFullScreen.assert_called_once()
    assert win._mac_fullscreen_safe_mode_active is False


def test_exit_safe_mode_noop_when_still_fullscreen(mock_darwin):
    import time as _time

    from novel_forge.desktop.window import navigation as nav

    win = MagicMock()
    win._mac_fullscreen_safe_mode_active = True
    win._mac_fullscreen_at_switch_start_time = _time.monotonic() - 1.0
    win._post_switch_current.return_value = True
    win._is_native_fullscreen_active.return_value = True

    nav.NavigationMixin._exit_mac_fullscreen_safe_mode(win, "dashboard", 1)

    win.showFullScreen.assert_not_called()
    assert win._mac_fullscreen_safe_mode_active is False


def test_exit_safe_mode_noop_after_5s_timeout(mock_darwin):
    import time as _time

    from novel_forge.desktop.window import navigation as nav

    win = MagicMock()
    win._mac_fullscreen_safe_mode_active = True
    win._mac_fullscreen_at_switch_start_time = _time.monotonic() - 6.0
    win._post_switch_current.return_value = True
    win._is_native_fullscreen_active.return_value = False

    nav.NavigationMixin._exit_mac_fullscreen_safe_mode(win, "dashboard", 1)

    win.showFullScreen.assert_not_called()
    assert win._mac_fullscreen_safe_mode_active is False


def test_exit_safe_mode_noop_when_not_post_switch_current(mock_darwin):
    import time as _time

    from novel_forge.desktop.window import navigation as nav

    win = MagicMock()
    win._mac_fullscreen_safe_mode_active = True
    win._mac_fullscreen_at_switch_start_time = _time.monotonic() - 1.0
    win._post_switch_current.return_value = False

    nav.NavigationMixin._exit_mac_fullscreen_safe_mode(win, "dashboard", 1)

    win.showFullScreen.assert_not_called()


def test_exit_safe_mode_noop_when_flag_false(mock_darwin):
    from novel_forge.desktop.window import navigation as nav

    win = MagicMock()
    win._mac_fullscreen_safe_mode_active = False

    nav.NavigationMixin._exit_mac_fullscreen_safe_mode(win, "dashboard", 1)

    win.showFullScreen.assert_not_called()
    win._is_native_fullscreen_active.assert_not_called()


def test_animate_current_page_skips_when_safe_mode_active(mock_darwin):
    from novel_forge.desktop.window import navigation as nav

    win = MagicMock()
    win._mac_fullscreen_safe_mode_active = True
    win._active_page_id = "dashboard"

    with patch("novel_forge.desktop.motion.Motion.fade_in") as mock_fade_in:
        nav.NavigationMixin._animate_current_page(win)
        mock_fade_in.assert_not_called()

    assert win._mac_fullscreen_safe_mode_active is True


def test_safe_mode_fallback_runs_after_animation_skip(mock_darwin):
    import time as _time

    from novel_forge.desktop.window import navigation as nav

    win = MagicMock()
    win._mac_fullscreen_safe_mode_active = True
    win._mac_fullscreen_at_switch_start_time = _time.monotonic() - 1.0
    win._active_page_id = "dashboard"
    win._post_switch_current.return_value = True
    win._is_native_fullscreen_active.return_value = False
    win.isVisible.return_value = True

    with patch("novel_forge.desktop.motion.Motion.fade_in") as mock_fade_in:
        nav.NavigationMixin._animate_current_page(win)
        mock_fade_in.assert_not_called()

    nav.NavigationMixin._exit_mac_fullscreen_safe_mode(win, "dashboard", 1)

    win.showFullScreen.assert_called_once()
    assert win._mac_fullscreen_safe_mode_active is False


def test_animate_top_bar_title_skips_when_safe_mode_active(mock_darwin):
    """When _mac_fullscreen_safe_mode_active is True, _animate_top_bar_title
    returns early without Motion.fade_in (preserves safe mode).
    """
    from novel_forge.desktop.window import navigation as nav

    win = MagicMock(spec=nav.NavigationMixin)
    win._mac_fullscreen_safe_mode_active = True
    # Top bar labels are None in mocked spec
    win._top_eyebrow = None
    win._top_title = None
    win._top_subtitle = None
    # _top_bar_animations might be a real list (the method assigns to it)
    win._top_bar_animations = []

    with patch("novel_forge.desktop.motion.Motion.fade_in") as mock_fade_in:
        nav.NavigationMixin._animate_top_bar_title(win)
        mock_fade_in.assert_not_called()

    assert win._top_bar_animations == []
