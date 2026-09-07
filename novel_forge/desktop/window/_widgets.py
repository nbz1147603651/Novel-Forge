"""Standalone widget and dataclass helpers for the desktop main window.

Extracted from ``window.py`` to reduce its size. Contains:
- PageMeta: frozen dataclass for page display metadata
- SectionBindablePage: Protocol for section-scoped workspace binding
- NavigationButton: sidebar navigation button

These types have no dependency on NovelForgeDesktopWindow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QPushButton, QSizePolicy, QWidget

if TYPE_CHECKING:
    from novel_forge.desktop.workspace import DesktopWorkspaceSnapshot


@dataclass(frozen=True)
class PageMeta:
    label: str
    eyebrow: str
    title: str
    subtitle: str


class SectionBindablePage(Protocol):
    """Protocol for pages that support section-scoped workspace binding.

    Pages implementing this method receive only the snapshot sections they
    care about (as declared in ``_PAGE_SECTION_MAP``), enabling incremental
    updates instead of full rebinds.

    Pages that do *not* implement this method fall back to ``bind_workspace``
    in the dispatcher (``_bind_workspace_for_page``).
    """

    def bind_workspace_sections(
        self, snapshot: "DesktopWorkspaceSnapshot", sections: frozenset[str]
    ) -> None:
        """Bind only the changed *sections* of *snapshot* to this page."""
        ...


class NavigationButton(QPushButton):
    """Sidebar navigation button.

    The ``active`` dynamic property is set to ``"true"`` / ``"false"`` by
    ``switch_page``.  The button deliberately avoids scale animations so the
    side navigation does not visually grow during resize or hover transitions.

    Provides adaptive sizing: ``sizeHint()`` returns a reasonable default
    based on the label text, and ``minimumSizeHint()`` ensures the button
    never shrinks below a usable width. When the available width is less
    than the text requires, the label is elided via ``elideMode``.
    """

    # Minimum usable width for a 2-char label with decorative dots at 20pt
    _NAV_BUTTON_MIN_WIDTH: int = 110
    _NAV_BUTTON_MIN_HEIGHT: int = 40

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("navButton")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("active", "false")
        # Allow the button to shrink but not below a minimum usable width
        self.setMinimumWidth(self._NAV_BUTTON_MIN_WIDTH)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_active(self, active: bool) -> None:
        """Update the ``active`` dynamic property and refresh the style."""
        value = "true" if active else "false"
        if self.property("active") == value:
            return
        self.setProperty("active", value)
        self.style().unpolish(self)
        self.style().polish(self)

    def sizeHint(self) -> QSize:
        """Return a size hint based on the label text metrics."""
        fm = QFontMetrics(self.font())
        text_width = fm.horizontalAdvance(self.text())
        # Add horizontal padding (14px each side from QSS) + some margin
        width = max(self._NAV_BUTTON_MIN_WIDTH, text_width + 36)
        height = max(self._NAV_BUTTON_MIN_HEIGHT, fm.height() + 20)
        return QSize(width, height)

    def minimumSizeHint(self) -> QSize:
        """Return the minimum size the button can shrink to."""
        return QSize(self._NAV_BUTTON_MIN_WIDTH, self._NAV_BUTTON_MIN_HEIGHT)

    def _update_elide_mode(self) -> None:
        """Enable text elision when the button is narrower than its text.

        .. note::
           This method must NOT be called from ``resizeEvent`` because
           ``setElideMode`` can trigger layout invalidation → resize →
           recursive layout loop on some Qt platforms (notably offscreen).
           Call it explicitly after the layout has settled (e.g. via
           ``QTimer.singleShot(0, ...)``).
        """
        fm = QFontMetrics(self.font())
        text_width = fm.horizontalAdvance(self.text())
        available = self.width() - 8  # account for padding
        target = (
            Qt.TextElideMode.ElideRight
            if available > 0 and text_width > available
            else Qt.TextElideMode.ElideNone
        )
        if self.elideMode() != target:
            self.setElideMode(target)


class CachedGradientWidget(QWidget):
    """QWidget that paints a cached linear gradient background.

    Avoids per-repaint ``qlineargradient`` evaluation by rendering the
    gradient into a ``QPixmap`` once and scaling it on resize.
    """

    def __init__(
        self,
        stops: list[tuple[float, str]],
        angle: float = 0.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._gradient_stops = stops
        self._gradient_angle = angle
        self._gradient_pixmap: QPixmap | None = None

    def invalidate_cache(self) -> None:
        self._gradient_pixmap = None
        self.update()

    def paintEvent(self, event: object) -> None:  # noqa: ARG002
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        if self._gradient_pixmap is None or self._gradient_pixmap.size() != QSize(w, h):
            pix = QPixmap(w, h)
            pix.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pix)
            grad = self._build_gradient(w, h)
            painter.fillRect(self.rect(), grad)
            painter.end()
            self._gradient_pixmap = pix
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._gradient_pixmap)

    def _build_gradient(self, w: int, h: int) -> QLinearGradient:
        import math

        angle_rad = math.radians(self._gradient_angle)
        cx, cy = w / 2, h / 2
        half_len = abs(w * math.sin(angle_rad)) + abs(h * math.cos(angle_rad))
        half_len /= 2
        dx = math.sin(angle_rad) * half_len
        dy = -math.cos(angle_rad) * half_len
        grad = QLinearGradient(cx - dx, cy - dy, cx + dx, cy + dy)
        for pos, color_str in self._gradient_stops:
            grad.setColorAt(pos, QColor(color_str))
        return grad


class CachedGradientFrame(QFrame):
    """QFrame that paints a cached linear gradient background.

    Identical to :class:`CachedGradientWidget` but inherits ``QFrame``
    for use where a frame border or shadow is needed.
    """

    def __init__(
        self,
        stops: list[tuple[float, str]],
        angle: float = 0.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._gradient_stops = stops
        self._gradient_angle = angle
        self._gradient_pixmap: QPixmap | None = None

    def invalidate_cache(self) -> None:
        self._gradient_pixmap = None
        self.update()

    def paintEvent(self, event: object) -> None:  # noqa: ARG002
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        if self._gradient_pixmap is None or self._gradient_pixmap.size() != QSize(w, h):
            pix = QPixmap(w, h)
            pix.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pix)
            grad = self._build_gradient(w, h)
            painter.fillRect(self.rect(), grad)
            painter.end()
            self._gradient_pixmap = pix
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._gradient_pixmap)

    def _build_gradient(self, w: int, h: int) -> QLinearGradient:
        import math

        angle_rad = math.radians(self._gradient_angle)
        cx, cy = w / 2, h / 2
        half_len = abs(w * math.sin(angle_rad)) + abs(h * math.cos(angle_rad))
        half_len /= 2
        dx = math.sin(angle_rad) * half_len
        dy = -math.cos(angle_rad) * half_len
        grad = QLinearGradient(cx - dx, cy - dy, cx + dx, cy + dy)
        for pos, color_str in self._gradient_stops:
            grad.setColorAt(pos, QColor(color_str))
        return grad
