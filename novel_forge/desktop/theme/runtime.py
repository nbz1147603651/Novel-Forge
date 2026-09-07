"""Runtime helpers for applying desktop themes."""

from __future__ import annotations

import logging
from typing import Any

from PySide6 import QtGui
from PySide6.QtWidgets import QApplication, QWidget

from . import get_stylesheet
from .palettes import list_desktop_themes, normalize_theme_id

logger = logging.getLogger(__name__)


def warm_theme_stylesheet_cache() -> None:
    """Pre-resolve all registered theme stylesheets for responsive switching."""

    for theme in list_desktop_themes():
        get_stylesheet(theme_id=theme.theme_id)


def current_application_theme(app: QApplication | None = None) -> str | None:
    """Return the theme id last applied through this runtime helper."""

    target_app = app or QApplication.instance()
    if not isinstance(target_app, QApplication):
        return None
    theme_id = target_app.property("_novel_forge_desktop_theme")
    return str(theme_id) if theme_id else None


def apply_desktop_theme(
    theme_id: str | None,
    *,
    app: QApplication | None = None,
    force: bool = False,
    root: QWidget | None = None,
) -> str:
    """Apply a registered desktop theme to the current QApplication.

    The stylesheet itself is cached by ``get_stylesheet()``.  This helper keeps
    switching cheap by avoiding redundant ``setStyleSheet()`` calls and by
    batching the root widget repaint into one update pass.
    """

    resolved_theme_id = normalize_theme_id(theme_id)
    target_app = app or QApplication.instance()
    if not isinstance(target_app, QApplication):
        return resolved_theme_id

    if not force and current_application_theme(target_app) == resolved_theme_id:
        return resolved_theme_id

    stylesheet = get_stylesheet(theme_id=resolved_theme_id)
    updates_were_enabled = root.updatesEnabled() if root is not None else True
    if root is not None:
        root.setUpdatesEnabled(False)
    try:
        if root is not None:
            root.setProperty("desktopTheme", resolved_theme_id)
        target_app.setProperty("_novel_forge_desktop_theme", resolved_theme_id)
        # Typography previews always start from the original themed QSS, so
        # toggling from Arial/Kai back to Songti cannot accumulate rewrites.
        target_app.setProperty("_novel_forge_theme_stylesheet", stylesheet)
        target_app.setStyleSheet(stylesheet)
        _refresh_theme_aware_widgets(target_app)
        window = _window_from_app(target_app)
        _refresh_brand_assets(target_app, window, resolved_theme_id)
    finally:
        if root is not None:
            root.setUpdatesEnabled(updates_were_enabled)
            # ``root.update`` must not outlive the widget. A static singleShot
            # keeps a bound Python callback after Qt has deleted the receiver,
            # which turns an otherwise harmless theme switch into a teardown
            # crash. Style application is complete here, so a direct queued
            # paint request is both sufficient and lifecycle-safe.
            root.update()
    return resolved_theme_id


def _refresh_theme_aware_widgets(app: QApplication) -> None:
    """Refresh paint/cache state that cannot be expressed in QSS.

    Widgets with custom ``QPainter`` output or cached pixmaps can expose a
    zero-argument ``refresh_theme_colors`` method. Theme changes are rare, so
    iterating the Qt widget registry here keeps the protocol simple without
    adding an observer to every paint path.
    """
    for widget in app.allWidgets():
        refresh = getattr(widget, "refresh_theme_colors", None)
        if not callable(refresh):
            continue
        try:
            refresh()
        except RuntimeError:
            # A deferred-delete widget can remain in Qt's registry briefly.
            continue
        except Exception as exc:  # pragma: no cover - defensive third-party widget hook
            logger.debug("Failed to refresh themed widget %r: %s", widget, exc)


def _window_from_app(app: QApplication) -> QWidget | None:
    window = app.property("_novel_forge_window")
    return window if isinstance(window, QWidget) else None


def _refresh_brand_assets(
    app: QApplication,
    window: QWidget | None,
    theme_id: str,
) -> None:
    try:
        from novel_forge.desktop.brand_assets import apply_app_icon

        apply_app_icon(app, QtGui, window=window, theme_id=theme_id)
    except Exception as exc:  # pragma: no cover - defensive around platform icon APIs
        logger.debug("Failed to refresh themed app icon: %s", exc)

    if window is None:
        return
    refresh_brand_logo = getattr(window, "_refresh_brand_logo", None)
    if callable(refresh_brand_logo):
        try:
            refresh_brand_logo(theme_id)
        except Exception as exc:  # pragma: no cover - defensive around shell logo refresh
            logger.debug("Failed to refresh themed brand logo: %s", exc)


def root_for_widget(widget: Any) -> QWidget | None:
    """Return the desktop shell root widget for a page/widget when available."""

    owner = widget.window() if hasattr(widget, "window") else None
    root = getattr(owner, "_workspace_root", None)
    return root if isinstance(root, QWidget) else None
