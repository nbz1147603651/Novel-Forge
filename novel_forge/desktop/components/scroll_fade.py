"""Centralized opaque edge fades for vertically scrollable desktop widgets."""

from __future__ import annotations

from typing import Any, TypeVar, cast

import shiboken6
from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QWidget

from novel_forge.desktop.theme import resolve_qcolor
from novel_forge.desktop.tokens.radius import DIALOG_RADIUS

DEFAULT_SCROLL_FADE_HEIGHT = 80
MIN_SCROLL_FADE_HEIGHT = 24
SCROLL_FADE_DISABLED_PROPERTY = "scrollFadeDisabled"

_QObjectT = TypeVar("_QObjectT", bound=QObject)


class ScrollEdgeFade(QWidget):
    """Opaque-to-transparent mask at the edge where scrolled content disappears.

    The overlay lives inside the scroll area's viewport, so it never covers the
    native scrollbar.  A cached pixmap keeps scrolling cheap even for large
    pages, tables, text editors, and nested scroll areas.
    """

    def __init__(
        self,
        scroll_area: QAbstractScrollArea,
        *,
        fade_height: int = DEFAULT_SCROLL_FADE_HEIGHT,
        bg_color: QColor | None = None,
        corner_radius: float = float(DIALOG_RADIUS),
    ) -> None:
        viewport = scroll_area.viewport()
        scroll_bar = scroll_area.verticalScrollBar()
        super().__init__(viewport)
        # The scroll area owns this overlay through its viewport and also keeps
        # a Python attribute pointing at it.  Do not retain the scroll-area
        # wrapper here: that creates a Python cycle which can survive until
        # QApplication's atexit cleanup.  The scrollbar wrapper is safe to keep
        # because it has no Python back-reference to this overlay.
        self._scroll_bar = scroll_bar
        self.fade_height = max(MIN_SCROLL_FADE_HEIGHT, fade_height)
        self._bg_token = "fade.bg" if bg_color is None else None
        self.bg_color = QColor(bg_color) if bg_color is not None else resolve_qcolor("fade.bg")
        self._corner_radius = max(0.0, corner_radius)
        self._enabled = True

        self.setObjectName("scrollEdgeFade")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StaticContents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._cached_pixmap: QPixmap | None = None
        self._cached_pixmap_size: QSize | None = None

        viewport.installEventFilter(self)
        scroll_bar.valueChanged.connect(self._sync_visibility)
        scroll_bar.rangeChanged.connect(self._sync_visibility)
        self.sync_geometry()
        self._sync_visibility()

    @staticmethod
    def _live(obj: _QObjectT | None) -> _QObjectT | None:
        if obj is None or not shiboken6.isValid(obj):
            return None
        return obj

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        viewport = self._live(self.parentWidget())
        try:
            event_type = event.type()
        except RuntimeError:
            return False
        if watched is viewport and event_type in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.LayoutRequest,
        }:
            try:
                self.sync_geometry()
                self._sync_visibility()
            except RuntimeError:
                return False
        return False

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._invalidate_pixmap_cache()

    def _effective_height(self) -> int:
        viewport = self._live(self.parentWidget())
        if viewport is None:
            return self.fade_height
        viewport_height = viewport.height()
        if viewport_height <= 0:
            return self.fade_height
        # Compact editors and lists should retain the same treatment without
        # losing most of their usable viewport to an 80 px overlay.
        return min(self.fade_height, max(MIN_SCROLL_FADE_HEIGHT, viewport_height // 3))

    def sync_geometry(self) -> None:
        viewport = self._live(self.parentWidget())
        if viewport is None:
            return
        height = self._effective_height()
        self.setGeometry(0, 0, viewport.width(), height)
        self.raise_()

    def _sync_visibility(self, *_args: object) -> None:
        scroll_bar = self._live(self._scroll_bar)
        if scroll_bar is None:
            self.hide()
            return
        visible = (
            self._enabled
            and scroll_bar.maximum() > scroll_bar.minimum()
            and (scroll_bar.value() > scroll_bar.minimum())
        )
        self.setVisible(visible)
        if visible:
            self.raise_()
            self.update()

    def set_fade_height(self, height: int) -> None:
        normalized = max(MIN_SCROLL_FADE_HEIGHT, height)
        if normalized == self.fade_height:
            return
        self.fade_height = normalized
        self._invalidate_pixmap_cache()
        self.sync_geometry()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self._sync_visibility()

    def set_color(self, color: QColor) -> None:
        self._bg_token = None
        self.bg_color = QColor(color)
        self._invalidate_pixmap_cache()
        self.update()

    def _invalidate_pixmap_cache(self) -> None:
        self._cached_pixmap = None
        self._cached_pixmap_size = None

    def refresh_theme_colors(self) -> None:
        if self._bg_token is None:
            return
        color = resolve_qcolor(self._bg_token)
        if color == self.bg_color:
            return
        self.bg_color = color
        self._invalidate_pixmap_cache()
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        if not self.isVisible():
            return

        current_size = self.size()
        cache_valid = (
            self._cached_pixmap is not None
            and self._cached_pixmap_size is not None
            and self._cached_pixmap_size == current_size
        )
        if not cache_valid:
            self._cached_pixmap = QPixmap(current_size)
            self._cached_pixmap.fill(QColor(0, 0, 0, 0))
            self._render_fade_to_pixmap(self._cached_pixmap)
            self._cached_pixmap_size = current_size

        painter = QPainter(self)
        assert self._cached_pixmap is not None
        painter.drawPixmap(0, 0, self._cached_pixmap)
        painter.end()

    def _render_fade_to_pixmap(self, pixmap: QPixmap) -> None:
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if self._corner_radius > 0:
            clip_path = QPainterPath()
            clip_path.addRoundedRect(
                0.0,
                0.0,
                float(pixmap.width()),
                float(pixmap.height()),
                self._corner_radius,
                self._corner_radius,
            )
            painter.setClipPath(clip_path)

        r, g, b = self.bg_color.red(), self.bg_color.green(), self.bg_color.blue()
        gradient = QLinearGradient(0, 0, 0, max(1, pixmap.height()))
        # Keep the disappearance edge fully opaque before easing into the
        # content. This avoids the legible-text overlap visible in the old mask.
        gradient.setColorAt(0.0, QColor(r, g, b, 255))
        gradient.setColorAt(0.38, QColor(r, g, b, 255))
        gradient.setColorAt(0.68, QColor(r, g, b, 224))
        gradient.setColorAt(0.86, QColor(r, g, b, 112))
        gradient.setColorAt(1.0, QColor(r, g, b, 0))
        painter.fillRect(pixmap.rect(), gradient)
        painter.end()


def ensure_scroll_edge_fade(
    scroll_area: QAbstractScrollArea,
    *,
    fade_height: int = DEFAULT_SCROLL_FADE_HEIGHT,
    bg_color: QColor | None = None,
    corner_radius: float = float(DIALOG_RADIUS),
) -> ScrollEdgeFade | None:
    """Attach the shared fade once, unless the widget explicitly opts out."""
    app = QApplication.instance()
    if app is None or QApplication.closingDown() or not shiboken6.isValid(scroll_area):
        return None
    if scroll_area.property(SCROLL_FADE_DISABLED_PROPERTY) is True:
        return None
    existing = getattr(scroll_area, "_novel_forge_scroll_edge_fade", None)
    if isinstance(existing, ScrollEdgeFade) and shiboken6.isValid(existing):
        existing.set_fade_height(fade_height)
        return existing
    if bool(getattr(scroll_area, "_novel_forge_scroll_edge_fade_installing", False)):
        return None
    cast(Any, scroll_area)._novel_forge_scroll_edge_fade_installing = True
    try:
        overlay = ScrollEdgeFade(
            scroll_area,
            fade_height=fade_height,
            bg_color=bg_color,
            corner_radius=corner_radius,
        )
        cast(Any, scroll_area)._novel_forge_scroll_edge_fade = overlay
    finally:
        cast(Any, scroll_area)._novel_forge_scroll_edge_fade_installing = False
    return overlay


def refresh_scroll_edge_fades(root: QWidget | None = None) -> None:
    """Install fades only after a widget subtree has finished construction.

    This deliberately does not use a QApplication event filter.  Intercepting
    ``Polish`` while a QAbstractScrollArea is still being constructed and then
    calling ``verticalScrollBar()`` re-enters PySide's wrapper registry.  On
    macOS that can leave a dangling QObject event and crash later in native Qt
    code.  Callers invoke this function at stable page/window lifecycle points.
    """
    app = QApplication.instance()
    if not isinstance(app, QApplication) or QApplication.closingDown():
        return
    if root is None:
        candidates = [
            widget for widget in app.allWidgets() if isinstance(widget, QAbstractScrollArea)
        ]
    else:
        candidates = []
        if isinstance(root, QAbstractScrollArea):
            candidates.append(root)
        candidates.extend(root.findChildren(QAbstractScrollArea))
    for scroll_area in candidates:
        if shiboken6.isValid(scroll_area):
            ensure_scroll_edge_fade(scroll_area)


def install_scroll_fade_manager(app: QApplication | None = None) -> None:
    """Compatibility entry point for a one-shot, lifecycle-safe refresh."""
    target = app or QApplication.instance()
    if not isinstance(target, QApplication):
        return None
    refresh_scroll_edge_fades()
