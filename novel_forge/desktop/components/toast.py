"""Toast notification system — floating, auto-dismissing messages.

Warm classical aesthetic matching the 砚台 theme.

Visual styling is declared in ``novel_forge.desktop.theme.toast`` for the
selector/token contract. The translucent top-level shell is painted through
``PaintedFloatingSurface`` so QSS never contributes rectangular background
pixels outside the rounded corners.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QTimer,
)
from PySide6.QtGui import QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.components.floating_surface import PaintedFloatingSurface
from novel_forge.desktop.constants import animations_supported
from novel_forge.desktop.motion import animations_supported as motion_animations_supported
from novel_forge.desktop.theme import resolve_qcolor

# ── Variant accent tokens ───────────────────────────────────────────────────
# Each variant maps to a design-token name.  The actual QColor is resolved
# dynamically at call time via ``resolve_qcolor`` so theme switches are
# picked up automatically — no manual RGB sync required.
_VARIANT_TOKENS: dict[str, str] = {
    "success": "status.success.warm",
    "error": "status.danger.alt",
    "warning": "accent.primary",
    "info": "text.secondary",
}


_VARIANT_ICONS = {
    "success": "✓",
    "error": "✕",
    "warning": "!",
    "info": "i",
}

# ── Layout constants ────────────────────────────────────────────────────────
TOAST_WIDTH = 300
TOAST_PADDING = 12
TOAST_BORDER_RADIUS = 8
TOAST_STACK_SPACING = 8
TOAST_MARGIN_RIGHT = 16
TOAST_MARGIN_TOP = 16
FADE_IN_DURATION = 200   # ms
FADE_OUT_DURATION = 200  # ms
AUTO_DISMISS_DURATION = 4000  # ms
SLIDE_IN_DURATION = 300  # ms
SLIDE_IN_OFFSET = 40     # px; final_x - SLIDE_IN_OFFSET = start_x (slide from right)


class Toast(PaintedFloatingSurface):
    """A single floating notification widget."""

    def __init__(
        self,
        message: str,
        variant: str = "info",
        duration: int = AUTO_DISMISS_DURATION,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            parent=parent,
            object_name="toast",
            radius=TOAST_BORDER_RADIUS,
            background_token="bg.surface",
            border_token=_VARIANT_TOKENS.get(variant, _VARIANT_TOKENS["info"]),
            border_alpha=40,
        )
        self._variant = variant if variant in _VARIANT_TOKENS else "info"
        self._duration = duration
        self._dismissed = False

        # objectName + dynamic property let ``theme/toast.py`` style the
        # widget via global QSS — no inline setStyleSheet() required. The
        # top-level shell itself is painted by ``PaintedFloatingSurface``.
        self.setProperty("variant", self._variant)

        # Opacity effect for fade animations
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)

        # Build layout
        self._setup_ui(message)

        # Fixed width, auto height
        self.setFixedWidth(TOAST_WIDTH)
        self.adjustSize()

    def paint_floating_surface(
        self,
        painter: QPainter,
        rect: QRectF,
        path: QPainterPath,
    ) -> None:
        """Paint the toast shell plus the variant accent rail."""
        del rect
        painter.fillPath(path, resolve_qcolor("bg.surface", 245))
        painter.save()
        painter.setClipPath(path)
        rail_color = resolve_qcolor(_VARIANT_TOKENS.get(self._variant, _VARIANT_TOKENS["info"]), 180)
        painter.fillRect(
            self.rect().left(),
            self.rect().top(),
            3,
            self.rect().height(),
            rail_color,
        )
        painter.restore()
        border_color = resolve_qcolor(_VARIANT_TOKENS.get(self._variant, _VARIANT_TOKENS["info"]), 40)
        painter.setPen(QPen(border_color, 1.0))
        painter.drawPath(path)

    def _setup_ui(self, message: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(TOAST_PADDING, TOAST_PADDING, TOAST_PADDING, TOAST_PADDING)
        layout.setSpacing(6)

        # Background, border and accent rail are painted in ``paintEvent`` as
        # a top-level-window fallback for the QSS contract in ``theme/toast.py``.
        # Label color still comes from the global QSS rule.

        # Icon + message label
        icon = _VARIANT_ICONS.get(self._variant, "i")
        text = f"{icon}  {message}"

        self.label = QLabel(text, self)
        self.label.setObjectName("toastLabel")
        self.label.setWordWrap(True)
        self.label.setFont(QFont("Songti SC", 13, QFont.Weight.Normal))
        layout.addWidget(self.label)

    def fade_in(self, on_finished: Callable[[], None] | None = None) -> None:
        """Animate opacity 0 → 1, optionally sliding in from the right.

        Slide-in is a geometry animation (D1) and is therefore skipped on
        macOS where it would cause compositor trails on the translucent
        frameless window.  The fade is the universal baseline.
        """
        if not animations_supported():
            self._opacity_effect.setOpacity(1.0)
            if on_finished:
                QTimer.singleShot(0, on_finished)
            return

        # Slide-in: only when geometry animations are safe (non-macOS).
        # The widget is moved off-screen to the right first so the
        # animation lands at the final layout position computed by
        # ``ToastManager._position_toast``.
        if motion_animations_supported("geometry"):
            end_pos = self.pos()
            start_pos = QPoint(end_pos.x() + SLIDE_IN_OFFSET, end_pos.y())
            self.move(start_pos)
            self._slide_anim = QPropertyAnimation(self, b"pos")
            self._slide_anim.setDuration(SLIDE_IN_DURATION)
            self._slide_anim.setStartValue(start_pos)
            self._slide_anim.setEndValue(end_pos)
            self._slide_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._slide_anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

        self._anim = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._anim.setDuration(FADE_IN_DURATION)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        if on_finished:
            self._anim.finished.connect(on_finished)
        self._anim.start()

    def fade_out(self, on_finished: Callable[[], None] | None = None) -> None:
        """Animate opacity 1 → 0."""
        if not animations_supported():
            self._opacity_effect.setOpacity(0.0)
            if on_finished:
                QTimer.singleShot(0, on_finished)
            return
        self._anim = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._anim.setDuration(FADE_OUT_DURATION)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.setEasingCurve(QEasingCurve.Type.InCubic)
        if on_finished:
            self._anim.finished.connect(on_finished)
        self._anim.start()

    def dismiss(self) -> None:
        """Start fade-out and close."""
        if self._dismissed:
            return
        self._dismissed = True
        self.fade_out(on_finished=lambda: None if self.close() else None)


class ToastManager:
    """Singleton manager that handles toast stacking and lifecycle."""

    _instance: ToastManager | None = None

    def __init__(self) -> None:
        self._toasts: list[Toast] = []
        self._parent: QWidget | None = None

    @classmethod
    def instance(cls) -> ToastManager:
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (useful for testing)."""
        if cls._instance is not None:
            for toast in cls._instance._toasts:
                toast.close()
            cls._instance._toasts.clear()
            cls._instance = None

    def set_parent(self, parent: QWidget) -> None:
        """Set the reference widget for positioning."""
        self._parent = parent

    def show(
        self,
        message: str,
        variant: str = "info",
        duration: int = AUTO_DISMISS_DURATION,
    ) -> Toast:
        """Create and display a new toast notification."""
        self.cleanup()
        toast = Toast(message, variant, duration, self._parent)

        # Position: top-right of parent with stacking
        self._position_toast(toast)

        # Add to stack
        self._toasts.append(toast)

        # Show and animate
        toast.show()
        toast.fade_in(on_finished=lambda: self._schedule_dismiss(toast))

        return toast

    def _position_toast(self, toast: Toast) -> None:
        """Position toast in top-right corner with vertical stacking."""
        if self._parent is None:
            # Fallback: screen center-right
            from PySide6.QtWidgets import QApplication
            screen = QApplication.primaryScreen().geometry()
            x = screen.right() - TOAST_WIDTH - TOAST_MARGIN_RIGHT
            y = TOAST_MARGIN_TOP
        else:
            parent_geo = self._parent.geometry()
            x = parent_geo.right() - TOAST_WIDTH - TOAST_MARGIN_RIGHT
            y = TOAST_MARGIN_TOP

        # Stack below existing toasts
        offset = 0
        for existing in self._toasts:
            if existing.isVisible() and not existing._dismissed:
                offset += existing.height() + TOAST_STACK_SPACING

        toast.move(x, y + offset)

    def _schedule_dismiss(self, toast: Toast) -> None:
        """Schedule auto-dismiss after duration."""
        QTimer.singleShot(toast._duration, toast.dismiss)

    def dismiss_all(self) -> None:
        """Dismiss all active toasts."""
        for toast in list(self._toasts):
            toast.dismiss()
        self._toasts.clear()

    def cleanup(self) -> None:
        """Remove closed toasts from the stack."""
        self._toasts = [t for t in self._toasts if t.isVisible()]


# ── Global convenience function ─────────────────────────────────────────────

def show_toast(
    message: str,
    variant: str = "info",
    duration: int = AUTO_DISMISS_DURATION,
    parent: QWidget | None = None,
) -> Toast:
    """Display a toast notification.

    Args:
        message: Text to display.
        variant: One of 'success', 'error', 'warning', 'info'.
        duration: Auto-dismiss duration in milliseconds.
        parent: Parent widget for positioning (optional).

    Returns:
        The created Toast instance.
    """
    manager = ToastManager.instance()
    if parent is not None:
        manager.set_parent(parent)
    return manager.show(message, variant, duration)
