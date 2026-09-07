"""Sub-module of novel_forge.desktop.pages.document_renderer.

Auto-generated in the M3.2 split. Contains popups.py renderers.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import (
    QGuiApplication,
)
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QLabel,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.components.floating_surface import PaintedFloatingSurface
from novel_forge.desktop.theme import resolve_qcolor
from novel_forge.desktop.tokens.radius import TOOLTIP_RADIUS

JsonDict = dict[str, Any]
FeedbackMarker = tuple[float, float, float, JsonDict, JsonDict]
WeaveLine = tuple[float, float, float, JsonDict, JsonDict]
DocumentRenderer = Callable[[dict[str, Any]], QWidget]


def _configure_artifact_tabs(tabs: QTabWidget, object_name: str) -> QTabWidget:
    """Keep nested artifact tabs visually compact and scrollable."""
    tabs.setObjectName(object_name)
    tabs.setDocumentMode(True)
    tabs.setElideMode(Qt.TextElideMode.ElideNone)
    tab_bar = tabs.tabBar()
    tab_bar.setObjectName(f"{object_name}Bar")
    tab_bar.setExpanding(False)
    tab_bar.setUsesScrollButtons(True)
    tab_bar.setDrawBase(False)
    tab_bar.setElideMode(Qt.TextElideMode.ElideNone)
    tab_bar.setMinimumHeight(36)
    return tabs


class _FramelessTooltipPopup(PaintedFloatingSurface):
    """Frameless tooltip popup used when native ``QToolTip`` cannot be styled.

    Native ``QToolTip`` on macOS ignores ``border-radius`` (it renders as a
    native ``NSWindow``), so rounded tooltip corners need a frameless QWidget
    styled by QSS.

    QSS still owns the selector/theme contract, but this top-level
    translucent window paints its own shell. Letting QSS also draw the
    popup background can leave opaque rectangular corner pixels outside
    the rounded path on macOS compositors.
    """

    # Mouse position offset (right / down) — kept identical to the previous
    # ``QToolTip.showText`` delivery so the popup lands in the same place.
    SHOW_OFFSET_X = 12
    SHOW_OFFSET_Y = 16
    SCREEN_MARGIN = 8
    def __init__(
        self,
        *,
        object_name: str,
        label_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            parent=parent,
            object_name=object_name,
            radius=TOOLTIP_RADIUS,
            background_token="bg.surface",
            border_token="border.default",
            border_alpha=140,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(0)
        self._label = QLabel(self)
        self._label.setObjectName(label_name)
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        layout.addWidget(self._label)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 4)
        shadow.setColor(resolve_qcolor("shadow", 72))
        self.setGraphicsEffect(shadow)
        self._shadow = shadow

    def refresh_theme_colors(self) -> None:
        """Refresh the popup shadow; its shell resolves tokens while painting."""
        self._shadow.setColor(resolve_qcolor("shadow", 72))
        self.update()

    def set_content(self, html: str) -> None:
        """Update the inner label's HTML content and re-fit the popup size."""
        self._label.setText(html)
        self._label.adjustSize()
        self.adjustSize()

    @classmethod
    def _fit_to_available_geometry(
        cls,
        anchor: QPoint,
        popup_size: QSize,
        available: QRect,
    ) -> QPoint:
        """Return a tooltip position that remains inside ``available``."""
        margin = cls.SCREEN_MARGIN
        width = max(1, popup_size.width())
        height = max(1, popup_size.height())

        x = anchor.x() + cls.SHOW_OFFSET_X
        y = anchor.y() + cls.SHOW_OFFSET_Y

        max_x = available.left() + available.width() - margin - width
        max_y = available.top() + available.height() - margin - height
        min_x = available.left() + margin
        min_y = available.top() + margin

        if x > max_x:
            x = anchor.x() - width - cls.SHOW_OFFSET_X
        if y > max_y:
            y = anchor.y() - height - cls.SHOW_OFFSET_Y

        if max_x < min_x:
            x = min_x
        else:
            x = max(min_x, min(x, max_x))

        if max_y < min_y:
            y = min_y
        else:
            y = max(min_y, min(y, max_y))

        return QPoint(x, y)

    def _available_geometry_for(self, global_pos: QPoint) -> QRect:
        screen = QGuiApplication.screenAt(global_pos)
        if screen is None:
            window_handle = self.windowHandle()
            if window_handle is not None:
                screen = window_handle.screen()
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return QRect(global_pos.x(), global_pos.y(), 1, 1)
        available = screen.availableGeometry()

        parent = self.parentWidget()
        if parent is not None:
            owner = parent.window()
            if owner is not None and owner.isVisible():
                owner_rect = QRect(owner.mapToGlobal(QPoint(0, 0)), owner.size())
                confined = available.intersected(owner_rect)
                if not confined.isEmpty():
                    return confined

        return available

    def show_at(self, global_pos: QPoint) -> None:
        """Position the popup near ``global_pos`` and show it.

        The preferred position is right/down from the cursor.  Near window
        edges, the popup flips left/up and then clamps to the owner window
        geometry so hover text never lands outside the visible surface.
        """
        self.adjustSize()
        size = self.sizeHint().expandedTo(self.minimumSizeHint())
        if size.isValid():
            self.resize(size)
        self.move(
            self._fit_to_available_geometry(
                global_pos,
                self.size(),
                self._available_geometry_for(global_pos),
            )
        )
        self.show()
        self.raise_()


class _CharacterTooltipPopup(_FramelessTooltipPopup):
    """Frameless popup used by ``CharacterGraphWidget._show_tooltip``."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            object_name="characterTooltipPopup",
            label_name="characterTooltipLabel",
            parent=parent,
        )


class _BlueprintTooltipPopup(_FramelessTooltipPopup):
    """Frameless popup used by ``NarrativeBlueprintWidget`` hover hints."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            object_name="blueprintTooltipPopup",
            label_name="blueprintTooltipLabel",
            parent=parent,
        )
