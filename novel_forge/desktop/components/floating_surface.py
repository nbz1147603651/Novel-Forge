"""Shared painted shell for translucent frameless floating windows."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import QFrame, QWidget

from novel_forge.desktop.theme import resolve_qcolor

# Token names used when no explicit color is supplied.
_DEFAULT_BG_TOKEN = "bg.surface"
_DEFAULT_BORDER_TOKEN = "border.default"
_DEFAULT_BORDER_ALPHA = 140


class PaintedFloatingSurface(QFrame):
    """Base class for top-level translucent floating UI.

    Qt/macOS can leave opaque rectangular corner pixels when a transparent
    frameless window lets QSS draw its background.  This base keeps the widget
    transparent at the window-system level and makes the rounded shell the only
    painted background source.

    Keep the native-window attributes constructor-only.  Toggling translucent
    background attributes during polish/style/paint events is unsafe on macOS
    after the backing window exists and can crash Qt.
    """

    def __init__(
        self,
        *,
        parent: QWidget | None = None,
        object_name: str = "",
        radius: int = 8,
        background_color: QColor | None = None,
        border_color: QColor | None = None,
        border_width: float = 1.0,
        window_flags: Qt.WindowType | None = None,
        background_token: str | None = None,
        border_token: str | None = None,
        border_alpha: int = _DEFAULT_BORDER_ALPHA,
    ) -> None:
        super().__init__(parent)
        self._surface_radius = radius
        self._surface_border_width = border_width

        # When a token name is supplied, resolve it dynamically at paint time
        # so theme switches are reflected without rebuilding the widget.
        self._bg_token: str | None = background_token or _DEFAULT_BG_TOKEN
        self._border_token: str | None = border_token or _DEFAULT_BORDER_TOKEN
        self._border_token_alpha: int = border_alpha

        if background_color is not None:
            self._surface_background = QColor(background_color)
            self._bg_token = None  # explicit color wins over token
        else:
            self._surface_background = resolve_qcolor(self._bg_token)

        if border_color is not None:
            self._surface_border = QColor(border_color)
            self._border_token = None
        else:
            self._surface_border = resolve_qcolor(
                self._border_token,
                border_alpha,
            )

        if object_name:
            self.setObjectName(object_name)

        self.setWindowFlags(
            window_flags
            or (
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.Tool
                | Qt.WindowType.WindowStaysOnTopHint
            )
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAutoFillBackground(False)

    def paintEvent(self, event: QPaintEvent) -> None:
        """Clear the transparent window and paint only the rounded shell."""
        event.accept()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self._surface_radius, self._surface_radius)
        self.paint_floating_surface(painter, rect, path)

    def paint_floating_surface(
        self,
        painter: QPainter,
        rect: QRectF,
        path: QPainterPath,
    ) -> None:
        """Paint the rounded shell; subclasses may override for accents."""
        del rect
        # Resolve tokens at paint time so theme switches are picked up live.
        bg = (
            resolve_qcolor(self._bg_token)
            if self._bg_token
            else self._surface_background
        )
        if self._border_token:
            border = resolve_qcolor(self._border_token, self._border_token_alpha)
        else:
            border = self._surface_border

        painter.fillPath(path, bg)
        if self._surface_border_width > 0:
            painter.setPen(QPen(border, self._surface_border_width))
            painter.drawPath(path)


__all__ = ["PaintedFloatingSurface"]
