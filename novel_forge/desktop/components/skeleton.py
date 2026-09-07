"""Skeleton loading components for the Novel Forge desktop UI.

Provides pulsing placeholder widgets that indicate loading states
without using GIFs or external resources.

Components:
- SkeletonPulse: Base widget providing opacity-pulse (QPropertyAnimation)
  AND shimmer (QVariantAnimation driving a moving QLinearGradient)
- SkeletonLine: Single-line text placeholder with pulse + shimmer
- SkeletonCard: Card-shaped container with multiple skeleton lines

All colors are derived from theme.py constants (BG_SURFACE, TEXT_SECONDARY).
Animation uses QPropertyAnimation following the ActionButton pattern.

D1 platform safety:
- The shimmer animation only animates a color/offset Property → safe on macOS.
- Geometry animations are NOT used here.
"""

from __future__ import annotations

import random

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    QVariantAnimation,
)
from PySide6.QtGui import QHideEvent, QLinearGradient, QPainter, QPaintEvent, QShowEvent
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from novel_forge.desktop.constants import animations_supported
from novel_forge.desktop.theme import resolve_qcolor
from novel_forge.desktop.tokens.radius import SKELETON_RADIUS


# Shimmer is a color animation (safe on macOS per D1); use motion's
# granular check rather than constants.animations_supported (which is
# conservative for ALL animation kinds on macOS). Lazy import avoids a
# motion.py → constants → skeleton.py load-order cycle.
def _shimmer_animations_supported() -> bool:
    from novel_forge.desktop.motion import animations_supported as _motion_supported

    return _motion_supported("color")


# ── SkeletonPulse ──────────────────────────────────────────────────


class SkeletonPulse(QWidget):
    """Base widget providing opacity-pulse + shimmer animations.

    Two independent animations drive two visual effects:
    1. Opacity pulse (existing): QPropertyAnimation cycles `_opacity` between
       `min_opacity` and `max_opacity` over `duration_ms` (default 1200ms),
       infinite loop, InOutSine easing.
    2. Shimmer (new in Task 11): QVariantAnimation cycles `shimmer_offset`
       between 0.0 and 1.0 over `shimmer_duration_ms` (default 1500ms),
       infinite loop, Linear easing. The offset drives a moving
       QLinearGradient highlight band painted in paintEvent.

    Both animations are platform-safe on macOS (D1: opacity + color only).

    Subclasses should override paintEvent() and call ``super().paintEvent(event)``
    first to render the shimmer base, then paint their own content (rounded
    rectangle overlay, border, etc.) on top.
    """

    DEFAULT_SHIMMER_DURATION_MS: int = 1500
    _SHIMMER_BAND_HALF_WIDTH: float = 0.2  # half-width of highlight band (0..1)
    _SHIMMER_TRAVEL_RANGE: float = 1.4  # how far the band travels across sweep

    def __init__(
        self,
        *,
        min_opacity: float = 0.3,
        max_opacity: float = 0.7,
        duration_ms: int = 1200,
        shimmer_duration_ms: int = DEFAULT_SHIMMER_DURATION_MS,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._opacity = min_opacity
        self._min_opacity = min_opacity
        self._max_opacity = max_opacity
        self._shimmer_offset: float = 0.0

        # Existing opacity-pulse animation
        self._pulse_anim = QPropertyAnimation(self, b"opacity")
        self._pulse_anim.setDuration(duration_ms)
        self._pulse_anim.setStartValue(min_opacity)
        self._pulse_anim.setEndValue(max_opacity)
        self._pulse_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._pulse_anim.setLoopCount(-1)

        # Shimmer animation — drives the `shimmer_offset` Property.
        # QVariantAnimation lets us animate a float value directly without
        # needing a QProperty subclass.
        self._shimmer_anim = QVariantAnimation(self)
        self._shimmer_anim.setDuration(shimmer_duration_ms)
        self._shimmer_anim.setStartValue(0.0)
        self._shimmer_anim.setEndValue(1.0)
        self._shimmer_anim.setEasingCurve(QEasingCurve.Type.Linear)
        self._shimmer_anim.setLoopCount(-1)

    # ── opacity Property ──────────────────────────────────────────

    def _get_opacity(self) -> float:
        return self._opacity

    def _set_opacity(self, value: float) -> None:
        self._opacity = value
        self.update()

    opacity = Property(float, _get_opacity, _set_opacity)

    # ── shimmer_offset Property ───────────────────────────────────

    def _get_shimmer_offset(self) -> float:
        return self._shimmer_offset

    def _set_shimmer_offset(self, value: float) -> None:
        self._shimmer_offset = float(value)
        self.update()

    shimmer_offset = Property(float, _get_shimmer_offset, _set_shimmer_offset)

    # ── lifecycle ─────────────────────────────────────────────────

    def start_pulse(self) -> None:
        """Start the opacity-pulse animation only (legacy API).

        Uses the conservative ``animations_supported`` check from constants —
        same behavior as the pre-shimmer implementation, so existing callers
        on macOS see no change.
        """
        if not animations_supported():
            self._opacity = self._max_opacity
            self.update()
            return
        if self._pulse_anim.state() != QVariantAnimation.State.Running:
            self._pulse_anim.start()

    def start(self) -> None:
        """Start BOTH opacity-pulse and shimmer animations.

        Canonical "start animation" entrypoint used by QA scenarios and
        external callers. Shimmer uses the granular color-animation check
        so it runs on macOS (D1: opacity/color are safe).
        """
        self.start_pulse()
        if self._shimmer_anim.state() != QVariantAnimation.State.Running:
            if _shimmer_animations_supported() or self.isVisible():
                self._shimmer_anim.start()

    def stop_pulse(self) -> None:
        """Stop both opacity-pulse and shimmer animations."""
        self._pulse_anim.stop()
        self._opacity = self._min_opacity
        self._shimmer_anim.stop()
        self._shimmer_offset = 0.0
        self.update()

    def stop(self) -> None:
        """Stop both animations (canonical stop entrypoint)."""
        self.stop_pulse()

    def hideEvent(self, event: QHideEvent) -> None:
        """Pause the shimmer when the widget is hidden (edge-case safety).

        The widget cannot paint while hidden, so the shimmer animation is
        pure CPU waste. We pause (rather than stop) so the offset is preserved
        if the widget is shown again.
        """
        if self._shimmer_anim.state() == QVariantAnimation.State.Running:
            self._shimmer_anim.pause()
        super().hideEvent(event)

    def showEvent(self, event: QShowEvent) -> None:
        """Resume shimmer when the widget becomes visible again."""
        super().showEvent(event)
        if (
            self._shimmer_anim.state() == QVariantAnimation.State.Paused
            and _shimmer_animations_supported()
        ):
            self._shimmer_anim.resume()

    # ── fade_in / fade_out via Motion library ─────────────────────

    def fade_in(
        self,
        *,
        duration: int | str = "modal",
        easing: QEasingCurve.Type | str = "standard",
    ) -> QPropertyAnimation:
        """Fade the skeleton in (opacity 0 → 1) using the Motion library.

        Uses QGraphicsOpacityEffect — safe on macOS (D1: opacity only).
        """
        # Lazy import to avoid a hard dependency cycle in module load order
        from novel_forge.desktop.motion import Motion

        return Motion.fade_in(self, duration=duration, easing=easing)

    def fade_out(
        self,
        *,
        duration: int | str = "modal",
        easing: QEasingCurve.Type | str = "accelerate",
    ) -> QPropertyAnimation:
        """Fade the skeleton out (opacity 1 → 0) using the Motion library.

        Uses QGraphicsOpacityEffect — safe on macOS (D1: opacity only).
        """
        from novel_forge.desktop.motion import Motion

        return Motion.fade_out(self, duration=duration, easing=easing)

    # ── paintEvent (shimmer base) ─────────────────────────────────

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint the shimmer base gradient.

        Subclasses should call ``super().paintEvent(event)`` first, then
        paint their own content (rounded rectangle, border, etc.) on top.
        The shimmer is drawn over the full widget rect as a horizontal
        gradient whose highlight band slides left-to-right based on
        ``shimmer_offset``.
        """
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = self.rect().adjusted(0, 0, -1, -1)
            if rect.width() <= 0 or rect.height() <= 0:
                return
            painter.fillRect(rect, self._build_shimmer_gradient(rect.width()))
        finally:
            painter.end()

    def _build_shimmer_gradient(self, width: int) -> QLinearGradient:
        """Build the horizontal shimmer gradient.

        Gradient stops are resolved from design tokens at paint time so
        theme switches are picked up automatically:

        - base:   ``bg.memory.tag``   (warm tan)
        - highlight: ``bg.surface`` at 50 % alpha
        """
        gradient = QLinearGradient(0, 0, max(1, width), 0)
        base_color = resolve_qcolor("bg.memory.tag")
        highlight_color = resolve_qcolor("bg.surface", int(255 * 0.5))

        band_center = (
            -self._SHIMMER_BAND_HALF_WIDTH + self._shimmer_offset * self._SHIMMER_TRAVEL_RANGE
        )
        # band_center range: [-0.2, 1.2]

        # Always anchor base color at the ends
        gradient.setColorAt(0.0, base_color)
        gradient.setColorAt(1.0, base_color)

        # Insert highlight band only when it intersects [0, 1]
        if 0.0 < band_center < 1.0:
            left = max(0.0, band_center - self._SHIMMER_BAND_HALF_WIDTH)
            right = min(1.0, band_center + self._SHIMMER_BAND_HALF_WIDTH)

            # QLinearGradient requires non-decreasing stop positions;
            # insert edges around the peak in order
            if 0.0 < left < 1.0:
                gradient.setColorAt(left, base_color)
            gradient.setColorAt(band_center, highlight_color)
            if 0.0 < right < 1.0:
                gradient.setColorAt(right, base_color)

        return gradient


# ── SkeletonLine ───────────────────────────────────────────────────


class SkeletonLine(SkeletonPulse):
    """Single-line text placeholder with rounded rectangle, pulse, and shimmer.

    Draws a rounded rectangle that pulses (opacity) and shimmers (moving
    gradient highlight) over the surface, simulating a loading text line.

    Usage:
        line = SkeletonLine(width=200, height=16)
        line.start()
        # ... when data arrives ...
        line.stop()
        line.hide()
    """

    _BORDER_RADIUS = SKELETON_RADIUS

    def __init__(
        self,
        *,
        width: int = 200,
        height: int = 16,
        min_opacity: float = 0.3,
        max_opacity: float = 0.7,
        duration_ms: int = 1200,
        shimmer_duration_ms: int = SkeletonPulse.DEFAULT_SHIMMER_DURATION_MS,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            min_opacity=min_opacity,
            max_opacity=max_opacity,
            duration_ms=duration_ms,
            shimmer_duration_ms=shimmer_duration_ms,
            parent=parent,
        )
        self.setFixedSize(width, height)

    def paintEvent(self, event: QPaintEvent) -> None:
        # Base: shimmer gradient
        super().paintEvent(event)

        # Overlay: pulse-tinted rounded rectangle
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            rect = self.rect().adjusted(0, 0, -1, -1)
            radius = min(self._BORDER_RADIUS, rect.height() // 2)

            # Subtle text.secondary tint based on current opacity
            overlay = resolve_qcolor("text.secondary", int(255 * self._opacity * 0.15))
            painter.setBrush(overlay)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, radius, radius)
        finally:
            painter.end()


# ── SkeletonCard ───────────────────────────────────────────────────


class SkeletonCard(SkeletonPulse):
    """Card-shaped container with a subtle border, pulse, and shimmer.

    Used as a placeholder for card-based UI elements during loading.
    Can contain child widgets (e.g., SkeletonLine instances).

    Usage:
        card = SkeletonCard()
        layout = QVBoxLayout(card)
        layout.addWidget(SkeletonLine(width=180))
        layout.addWidget(SkeletonLine(width=140))
        card.start()
    """

    _BORDER_RADIUS = SKELETON_RADIUS

    def __init__(
        self,
        *,
        min_opacity: float = 0.2,
        max_opacity: float = 0.5,
        duration_ms: int = 1400,
        shimmer_duration_ms: int = SkeletonPulse.DEFAULT_SHIMMER_DURATION_MS,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            min_opacity=min_opacity,
            max_opacity=max_opacity,
            duration_ms=duration_ms,
            shimmer_duration_ms=shimmer_duration_ms,
            parent=parent,
        )
        self.setMinimumHeight(80)

    def paintEvent(self, event: QPaintEvent) -> None:
        # Base: shimmer gradient
        super().paintEvent(event)

        # Overlay: pulse-opacity border
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            rect = self.rect().adjusted(0, 0, -1, -1)
            radius = self._BORDER_RADIUS

            border_alpha = int(255 * self._opacity * 0.2)
            border_color = resolve_qcolor("text.secondary", border_alpha)
            painter.setPen(border_color)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, radius, radius)
        finally:
            painter.end()


# ── Reusable loading state ─────────────────────────────────────────


class LoadingState(QWidget):
    """A compact, theme-aware loading state for deferred desktop content.

    The component deliberately uses the existing shimmer primitives instead
    of GIFs or a busy ``QMovie``.  That keeps it inexpensive, works in the
    offscreen test platform, and automatically follows the active theme.

    Use it as a page or panel placeholder while I/O or staged UI construction
    is in progress.  ``card_count`` lets card grids reserve a stable footprint
    and avoid a second layout jump when their real content is swapped in.
    """

    def __init__(
        self,
        message: str = "正在加载…",
        detail: str = "",
        *,
        card_count: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("loadingState")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(112)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        layout.addStretch(1)

        self._message = QLabel(message, self)
        self._message.setObjectName("loadingStateMessage")
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._message)

        self._detail = QLabel(detail, self)
        self._detail.setObjectName("loadingStateDetail")
        self._detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail.setWordWrap(True)
        self._detail.setVisible(bool(detail))
        layout.addWidget(self._detail)

        self._skeletons: list[SkeletonPulse] = []
        for index in range(max(0, card_count)):
            card = SkeletonCard(parent=self)
            card.setMinimumHeight(72)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            card_layout.setSpacing(8)
            first = SkeletonLine(width=220, height=12, parent=card)
            second = SkeletonLine(width=150, height=10, parent=card)
            card_layout.addWidget(first)
            card_layout.addWidget(second)
            layout.addWidget(card)
            self._skeletons.extend((card, first, second))
            # Avoid a perfectly synchronized sweep across a card grid.
            if index:
                card._set_shimmer_offset(index / max(card_count, 1))

        layout.addStretch(1)

    def set_loading_text(self, message: str, detail: str = "") -> None:
        """Update the loading copy without rebuilding the placeholder."""
        self._message.setText(message)
        self._detail.setText(detail)
        self._detail.setVisible(bool(detail))

    def start(self) -> None:
        """Start the lightweight shimmer animation for all placeholders."""
        for skeleton in self._skeletons:
            skeleton.start()

    def stop(self) -> None:
        """Stop animations before the state is removed or hidden."""
        for skeleton in self._skeletons:
            skeleton.stop()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.start()

    def hideEvent(self, event: QHideEvent) -> None:
        self.stop()
        super().hideEvent(event)


# ── Factory helpers ────────────────────────────────────────────────


def create_skeleton_lines(
    count: int = 4,
    *,
    min_width: int = 80,
    max_width: int = 280,
    height: int = 14,
    spacing: int = 10,
    parent: QWidget | None = None,
) -> list[SkeletonLine]:
    """Create a set of skeleton lines with varied widths.

    Returns a list of SkeletonLine widgets ready to be added to a layout.
    Each line has a random width between min_width and max_width.
    The last line is typically shorter (simulating a partial line).

    Usage:
        container = QWidget()
        layout = QVBoxLayout(container)
        lines = create_skeleton_lines(count=5, parent=container)
        for line in lines:
            layout.addWidget(line)
        for line in lines:
            line.start()
    """
    lines: list[SkeletonLine] = []
    for i in range(count):
        # Last line is shorter for natural text feel
        if i == count - 1:
            w = random.randint(min_width, int(max_width * 0.6))
        else:
            w = random.randint(min_width, max_width)
        line = SkeletonLine(width=w, height=height, parent=parent)
        lines.append(line)
    return lines


def create_skeleton_card_with_lines(
    line_count: int = 4,
    *,
    parent: QWidget | None = None,
) -> SkeletonCard:
    """Create a SkeletonCard pre-populated with skeleton lines.

    Returns a ready-to-use skeleton card widget. Call start() on the card
    and each line to begin animation.

    Usage:
        card = create_skeleton_card_with_lines(line_count=5)
        card.start()
        for child in card.findChildren(SkeletonLine):
            child.start()
        layout.addWidget(card)
    """
    card = SkeletonCard(parent=parent)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(10)

    lines = create_skeleton_lines(
        count=line_count,
        min_width=100,
        max_width=260,
        height=14,
        spacing=10,
        parent=card,
    )
    for line in lines:
        layout.addWidget(line)

    layout.addStretch()
    return card
