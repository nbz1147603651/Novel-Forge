"""Primitive-level UI components: basic building blocks for the desktop UI."""

from __future__ import annotations

import sys

from PySide6.QtCore import Property, QAbstractAnimation, QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QWidget,
)

from novel_forge.desktop.constants import animations_supported
from novel_forge.desktop.theme import resolve_qcolor

_CUSTOM_SCALE_PAINT_SUPPORTED = sys.platform != "darwin"


def clear_layout(layout: QLayout) -> None:
    """Delete all child widgets/layouts from a layout.

    Widgets can be removed while handling their own ``clicked`` signal.  Hide
    them before clearing the parent: on macOS, an already-visible native child
    can otherwise briefly become a top-level window during ``setParent(None)``.
    """
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        widget = item.widget()
        child_layout = item.layout()
        if widget is not None:
            # ``setParent(None)`` promotes a widget to a native top-level
            # window.  Keep it hidden for the short interval before
            # ``deleteLater`` is delivered, especially when this runs from a
            # button click handler.
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
            continue
        if child_layout is not None:
            clear_layout(child_layout)


class Surface(QFrame):
    """Tone-aware container used throughout the desktop UI."""

    # Tones that require a true QGraphicsDropShadowEffect for visual elevation.
    # card/panel/flat/rail rely on QSS border + background (Task 8 D9 unification).
    _SHADOW_TONES: frozenset[str] = frozenset({"hero", "elevated", "inset"})

    # Declarative fast-path for page transition safety checks.
    _declares_graphics_effect: bool = False

    def __init__(self, tone: str = "panel", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("surface")
        self.setProperty("tone", tone)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._shadow_effect: QGraphicsDropShadowEffect | None = None
        if tone in self._SHADOW_TONES:
            self._declares_graphics_effect = True
            shadow = QGraphicsDropShadowEffect(self)
            # Keep blur small: large blur forces Qt to allocate a wide off-screen
            # pixmap buffer per-surface, causing scroll jank when many are visible.
            is_hero = tone == "hero"
            shadow.setBlurRadius(8 if is_hero else 4)
            shadow.setOffset(0, 4 if is_hero else 2)
            shadow.setColor(resolve_qcolor("shadow", 28))
            self.setGraphicsEffect(shadow)
            self._shadow_effect = shadow

    def refresh_theme_colors(self) -> None:
        """Refresh the token-derived shadow after an application theme switch."""
        if self._shadow_effect is not None:
            self._shadow_effect.setColor(resolve_qcolor("shadow", 28))
            self.update()


class Badge(QLabel):
    """Tone-aware capsule badge."""

    def __init__(
        self,
        text: str = "",
        *,
        tone: str = "default",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setObjectName("badge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)


class ActionButton(QPushButton):
    """Primary / secondary / quiet action button."""

    HOVER_SCALE: float = 1.02
    # Deliberately NOT Motion.DURATIONS['hover'] (100ms) — Task 9 spec is 120ms.
    HOVER_DURATION_MS: int = 120
    # Hover overlay alpha target (subtle brightness shift).
    _HOVER_OVERLAY_ALPHA: int = 18

    def __init__(
        self,
        text: str,
        *,
        variant: str = "primary",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setObjectName("actionButton")
        self.setProperty("variant", variant)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName(text)
        self._scale = 1.0
        self._hover_alpha: float = 0.0
        self._anim = QPropertyAnimation(self, b"scale")
        self._anim.setDuration(150)
        self._hover_anim: QPropertyAnimation | None = None
        self._hover_alpha_anim: QPropertyAnimation | None = None

    def _get_scale(self) -> float:
        return self._scale

    def _set_scale(self, value: float) -> None:
        self._scale = value
        self.update()

    scale = Property(float, _get_scale, _set_scale)

    def _get_hover_alpha(self) -> float:
        return self._hover_alpha

    def _set_hover_alpha(self, value: float) -> None:
        self._hover_alpha = value
        self.update()

    hoverAlpha = Property(float, _get_hover_alpha, _set_hover_alpha)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self.isEnabled() or not animations_supported():
            super().mousePressEvent(event)
            return
        self._anim.stop()
        self._anim.setStartValue(self._scale)
        self._anim.setEndValue(0.95)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if not self.isEnabled() or not animations_supported():
            super().mouseReleaseEvent(event)
            return
        self._anim.stop()
        self._anim.setStartValue(self._scale)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._anim.start()
        super().mouseReleaseEvent(event)

    def enterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        # Hover/scale is platform-safe on macOS (D1): only geometry animations
        # are blocked. Do NOT gate on ``animations_supported()`` — disabled
        # state alone is the correct guard (lets QSS opacity own the visual).
        if self.isEnabled():
            self._start_hover_animation(self.HOVER_SCALE)
            self._start_hover_alpha_animation(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.isEnabled():
            self._start_hover_animation(1.0)
            self._start_hover_alpha_animation(0.0)
        super().leaveEvent(event)

    def _start_hover_animation(self, target: float) -> None:
        # Stops the previous hover anim first so rapid enter/leave cycles
        # don't stack animations on the same ``b"scale"`` property (D11).
        from novel_forge.desktop.motion import Motion

        Motion.stop_safely(self._hover_anim)
        self._hover_anim = None

        anim = Motion.scale(
            self,
            start_value=self._scale,
            end_value=target,
            duration=self.HOVER_DURATION_MS,
            easing="standard",
        )
        self._hover_anim = anim

        def _clear_hover_anim() -> None:
            if self._hover_anim is anim:
                self._hover_anim = None

        anim.finished.connect(_clear_hover_anim)

    def _start_hover_alpha_animation(self, target: float) -> None:
        """Animate the hover overlay alpha for smooth enter/leave transitions."""
        from novel_forge.desktop.motion import Motion

        Motion.stop_safely(self._hover_alpha_anim)
        self._hover_alpha_anim = None

        anim = QPropertyAnimation(self, b"hoverAlpha")
        anim.setDuration(self.HOVER_DURATION_MS)
        anim.setStartValue(self._hover_alpha)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        Motion._apply_safety(self, anim)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        self._hover_alpha_anim = anim

        def _clear() -> None:
            if self._hover_alpha_anim is anim:
                self._hover_alpha_anim = None

        anim.finished.connect(_clear)

    def paintEvent(self, event: QPaintEvent) -> None:
        if self._scale == 1.0 or not _CUSTOM_SCALE_PAINT_SUPPORTED:
            super().paintEvent(event)
            # Draw hover overlay on top
            if self._hover_alpha > 0.01:
                p = QPainter(self)
                try:
                    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(
                        QColor(255, 255, 255, int(self._hover_alpha * self._HOVER_OVERLAY_ALPHA))
                    )
                    p.drawRoundedRect(self.rect(), 6, 6)
                finally:
                    p.end()
            return

        option = QStyleOptionButton()
        self.initStyleOption(option)
        painter = QPainter(self)
        if not painter.isActive():
            super().paintEvent(event)
            return
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            center = self.rect().center()
            painter.translate(center)
            painter.scale(self._scale, self._scale)
            painter.translate(-center.x(), -center.y())
            self.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, self)
            # Draw hover overlay
            if self._hover_alpha > 0.01:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(
                    QColor(255, 255, 255, int(self._hover_alpha * self._HOVER_OVERLAY_ALPHA))
                )
                painter.drawRoundedRect(self.rect(), 6, 6)
        finally:
            painter.end()


class FilterChip(QPushButton):
    """Checkable pill used by list filters.

    Task 18 polish: a brief 150ms ``Motion.scale`` pop fires whenever the
    checked state changes — provides perceptual feedback that the
    background QSS state change actually happened (QSS itself has no
    transition support for ``background``).

    Phase 5.2: hover overlay alpha gradient for smooth enter/leave.
    """

    ACTIVE_DURATION_MS: int = 150
    ACTIVE_POP_SCALE: float = 1.05
    HOVER_DURATION_MS: int = 120
    _HOVER_OVERLAY_ALPHA: int = 18

    def __init__(
        self,
        text: str,
        *,
        active: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setObjectName("filterChip")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(text)
        self._previous_checked: bool = active
        self._active_anim: QPropertyAnimation | None = None
        self._hover_alpha: float = 0.0
        self._hover_alpha_anim: QPropertyAnimation | None = None
        self._scale: float = 1.0
        self.setChecked(active)

    def _get_scale(self) -> float:
        return self._scale

    def _set_scale(self, value: float) -> None:
        self._scale = value
        self.update()

    scale = Property(float, _get_scale, _set_scale)

    def _get_hover_alpha(self) -> float:
        return self._hover_alpha

    def _set_hover_alpha(self, value: float) -> None:
        self._hover_alpha = value
        self.update()

    hoverAlpha = Property(float, _get_hover_alpha, _set_hover_alpha)

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        toggled = checked != self._previous_checked
        super().setChecked(checked)
        self._previous_checked = checked
        if toggled and self.isVisible():
            self._animate_active_change()

    def enterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.isEnabled():
            self._start_hover_alpha_animation(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.isEnabled():
            self._start_hover_alpha_animation(0.0)
        super().leaveEvent(event)

    def _animate_active_change(self) -> None:
        """Run a 150ms scale pop on state flip (D1-safe on macOS, D11 reentrancy)."""
        from novel_forge.desktop.motion import Motion

        Motion.stop_safely(self._active_anim)
        self._active_anim = None
        end = self.ACTIVE_POP_SCALE if self.isChecked() else 1.0
        anim = Motion.scale(
            self,
            start_value=self._scale,
            end_value=end,
            duration=self.ACTIVE_DURATION_MS,
            easing="emphasized",
        )
        self._active_anim = anim

        def _clear_active_anim() -> None:
            if self._active_anim is anim:
                self._active_anim = None

        anim.finished.connect(_clear_active_anim)

    def _start_hover_alpha_animation(self, target: float) -> None:
        """Animate the hover overlay alpha for smooth enter/leave transitions."""
        from novel_forge.desktop.motion import Motion

        Motion.stop_safely(self._hover_alpha_anim)
        self._hover_alpha_anim = None

        anim = QPropertyAnimation(self, b"hoverAlpha")
        anim.setDuration(self.HOVER_DURATION_MS)
        anim.setStartValue(self._hover_alpha)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        Motion._apply_safety(self, anim)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        self._hover_alpha_anim = anim

        def _clear() -> None:
            if self._hover_alpha_anim is anim:
                self._hover_alpha_anim = None

        anim.finished.connect(_clear)

    def paintEvent(self, event: QPaintEvent) -> None:
        if self._scale == 1.0 or not _CUSTOM_SCALE_PAINT_SUPPORTED:
            super().paintEvent(event)
            # Draw hover overlay on top
            if self._hover_alpha > 0.01:
                p = QPainter(self)
                try:
                    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(
                        QColor(255, 255, 255, int(self._hover_alpha * self._HOVER_OVERLAY_ALPHA))
                    )
                    p.drawRoundedRect(self.rect(), 6, 6)
                finally:
                    p.end()
            return
        option = QStyleOptionButton()
        self.initStyleOption(option)
        painter = QPainter(self)
        if not painter.isActive():
            super().paintEvent(event)
            return
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            center = self.rect().center()
            painter.translate(center)
            painter.scale(self._scale, self._scale)
            painter.translate(-center.x(), -center.y())
            self.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, self)
            # Draw hover overlay
            if self._hover_alpha > 0.01:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(
                    QColor(255, 255, 255, int(self._hover_alpha * self._HOVER_OVERLAY_ALPHA))
                )
                painter.drawRoundedRect(self.rect(), 6, 6)
        finally:
            painter.end()


class SectionHeading(QWidget):
    """Title/subtitle pair for page sections."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        *,
        parent: QWidget | None = None,
    ) -> None:
        from PySide6.QtWidgets import QVBoxLayout

        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("sectionTitle")
        layout.addWidget(self.title_label)

        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("sectionSubtitle")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setVisible(bool(subtitle))
        layout.addWidget(self.subtitle_label)

    def set_content(self, title: str, subtitle: str = "") -> None:
        self.title_label.setText(title)
        self.subtitle_label.setText(subtitle)
        self.subtitle_label.setVisible(bool(subtitle))
