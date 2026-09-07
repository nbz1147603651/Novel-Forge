"""Motion animation library for Novel Forge Desktop.

Provides a unified API for creating QPropertyAnimation instances with
consistent durations, easings, and platform-aware safety guards.

Platform Granularity (D1):
    macOS (Darwin) has compositor issues with geometry animations on nested
    translucent widgets. This module provides granular animation support:
    - opacity/color animations: SAFE on macOS
    - geometry animations (height, width, pos): BLOCKED on macOS

Effect Limitation (D10):
    QGraphicsDropShadowEffect and QGraphicsOpacityEffect are MUTUALLY
    EXCLUSIVE on a single widget (Qt limitation). For widgets that need
    both shadow and fade animation, use QWidget.windowOpacity instead of
    QGraphicsOpacityEffect. This is the ONLY safe fade approach for
    shadowed widgets.

Widget Destruction Safety (D11):
    All factory methods perform three safety checks:
    1. Verify widget is not None before creating animation
    2. Connect widget.destroyed signal to anim.stop() to prevent crashes
    3. Cache animation on widget via setProperty("_motion_anim", anim) and a
       Python-side ``_motion_anims`` list to prevent garbage collection
       during overlapping animation lifecycles
"""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING, Any, Literal, cast

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QTimer,
)

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

from novel_forge.desktop import constants

# Type alias for animation kind
AnimationKind = Literal["opacity", "color", "geometry", "any"]


def animations_supported(kind: AnimationKind = "any") -> bool:
    """Check if animations of a specific kind are supported on this platform.

    Granular macOS guard (D1):
    - opacity: Safe on all platforms including macOS
    - color: Safe on all platforms including macOS
    - geometry: Blocked on macOS (compositor trails with translucent widgets)
    - any: Conservative - returns False if ANY kind is blocked

    Args:
        kind: The type of animation to check support for.
            'opacity' - opacity/fade animations
            'color' - color transition animations
            'geometry' - size/position animations
            'any' - conservative check (False if any kind blocked)

    Returns:
        True if the animation kind is supported on this platform.
    """
    if not constants.ANIMATIONS_ENABLED:
        return False

    if sys.platform == "darwin":
        # macOS: opacity and color are safe, geometry is blocked
        if kind in ("opacity", "color"):
            return True
        # 'geometry' and 'any' are blocked on macOS
        return False

    # Non-macOS: all animations supported
    return True


class MotionManager:
    """Global motion configuration and reduced-motion detection.

    Manages system-wide animation preferences and provides cross-platform
    detection for 'prefers-reduced-motion' accessibility settings.

    Class Attributes:
        _reduced: When True, all Motion factory methods return instant
            animations (duration=1ms) regardless of configured duration.
    """

    _reduced: bool = False

    @staticmethod
    def prefers_reduced_motion() -> bool:
        """Detect if the user prefers reduced motion (cross-platform).

        Checks platform-specific accessibility settings:
        - macOS: defaults read com.apple.universalaccess reduceMotion
        - Windows: SPI_GETCLIENTAREAANIMATION via ctypes
        - Linux: gsettings get org.gnome.desktop.interface enable-animations

        Returns:
            True if reduced motion is preferred, False otherwise.
        """
        if sys.platform == "darwin":
            return MotionManager._check_macos_reduced_motion()
        elif sys.platform == "win32":
            return MotionManager._check_windows_reduced_motion()
        elif sys.platform.startswith("linux"):
            return MotionManager._check_linux_reduced_motion()
        return False

    @staticmethod
    def _check_macos_reduced_motion() -> bool:
        """Check macOS reduceMotion setting."""
        try:
            result = subprocess.run(
                ["defaults", "read", "com.apple.universalaccess", "reduceMotion"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            return result.stdout.strip() == "1"
        except (subprocess.SubprocessError, OSError):
            return False

    @staticmethod
    def _check_windows_reduced_motion() -> bool:
        """Check Windows client area animation setting."""
        try:
            import ctypes

            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            # SPI_GETCLIENTAREAANIMATION = 0x1042
            result = user32.SystemParametersInfoW(0x1042, 0, None, 0)
            return bool(result == 0)  # 0 means animations disabled
        except (OSError, AttributeError):
            return False

    @staticmethod
    def _check_linux_reduced_motion() -> bool:
        """Check Linux GNOME animations setting."""
        try:
            result = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "enable-animations"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            return result.stdout.strip() == "false"
        except (subprocess.SubprocessError, OSError):
            return False


class Motion:
    """Factory for creating QPropertyAnimation instances with consistent settings.

    All factory methods return QPropertyAnimation objects configured with:
    - Named durations from DURATIONS catalog
    - Named easings from EASINGS catalog
    - Widget destruction safety (D11)
    - Reduced-motion override (duration=1 when MotionManager._reduced=True)
    - Disabled override (duration=1 when ANIMATIONS_ENABLED=False)

    Class Attributes:
        DURATIONS: Named duration presets in milliseconds.
        EASINGS: Named easing curve presets.
    """

    # Duration catalog (9 named durations)
    DURATIONS: dict[str, int] = {
        "hover": 100,  # Quick hover feedback
        "focus": 150,  # Focus ring transition
        "toggle": 200,  # Toggle/switch animation
        "tooltip": 150,  # Tooltip fade
        "modal": 250,  # Modal/dialog entrance
        "panel-slide": 300,  # Panel slide transitions
        "page": 250,  # Page transitions
        "notification": 200,  # Toast/notification
        "pulse": 1200,  # Slow pulse/loading indicator
    }

    # Easing catalog (5 standard easings)
    EASINGS: dict[str, QEasingCurve.Type] = {
        "standard": QEasingCurve.Type.OutCubic,  # Default easing
        "emphasized": QEasingCurve.Type.OutBack,  # Overshoot entrance
        "accelerate": QEasingCurve.Type.InCubic,  # Exiting/departing
        "decelerate": QEasingCurve.Type.OutQuart,  # Gentle arrival
        "bounce": QEasingCurve.Type.OutBounce,  # Playful bounce
    }

    @staticmethod
    def _resolve_duration(duration: int | str) -> int:
        """Resolve duration from name or integer.

        Args:
            duration: Either an integer milliseconds or a name from DURATIONS.

        Returns:
            Duration in milliseconds.
        """
        if isinstance(duration, str):
            return Motion.DURATIONS.get(duration, Motion.DURATIONS["modal"])
        return duration

    @staticmethod
    def _resolve_easing(easing: QEasingCurve.Type | str) -> QEasingCurve.Type:
        """Resolve easing from name or QEasingCurve.Type.

        Args:
            easing: Either a QEasingCurve.Type or a name from EASINGS.

        Returns:
            QEasingCurve.Type value.
        """
        if isinstance(easing, str):
            return Motion.EASINGS.get(easing, Motion.EASINGS["standard"])
        return easing

    @staticmethod
    def _effective_duration(duration: int) -> int:
        """Apply reduced-motion and disabled overrides to duration.

        Returns duration=1 (instant) when:
        - MotionManager._reduced is True (accessibility)
        - ANIMATIONS_ENABLED is False (global disable)

        Args:
            duration: Requested duration in milliseconds.

        Returns:
            Effective duration (may be 1 for instant).
        """
        if MotionManager._reduced or not constants.ANIMATIONS_ENABLED:
            return 1
        return duration

    @staticmethod
    def stop_safely(anim: QAbstractAnimation | None) -> None:
        """Stop an animation whose Qt C++ object may already be deleted."""
        if anim is None:
            return
        try:
            anim.stop()
        except RuntimeError:
            return

    @staticmethod
    def _apply_safety(widget: QWidget, anim: QPropertyAnimation) -> None:
        """Apply widget destruction safety (D11).

        Safety checks:
        1. Widget is not None (caller must verify before calling)
        2. Connect widget.destroyed to anim.stop()
        3. Cache anim on widget to prevent garbage collection. ``_motion_anim``
           remains the latest animation for legacy callers; ``_motion_anims``
           keeps overlapping fade+scale animations alive.

        Args:
            widget: The target widget (must not be None).
            anim: The animation to protect.
        """
        # Safety 2: Stop animation if widget is destroyed
        widget.destroyed.connect(anim.stop)

        cached = getattr(widget, "_motion_anims", None)
        if not isinstance(cached, list):
            cached = []
        cached.append(anim)
        cast(Any, widget)._motion_anims = cached

        def _drop_finished_animation() -> None:
            try:
                active = getattr(widget, "_motion_anims", None)
                if not isinstance(active, list):
                    return
                if any(item is anim for item in active):
                    cast(Any, widget)._motion_anims = [item for item in active if item is not anim]
            except RuntimeError:
                return

        anim.finished.connect(_drop_finished_animation)
        # Safety 3: Cache latest animation on widget for compatibility.
        widget.setProperty("_motion_anim", anim)

    @staticmethod
    def fade_in(
        widget: QWidget,
        *,
        duration: int | str = "modal",
        easing: QEasingCurve.Type | str = "standard",
        delete_when_stopped: bool = True,
    ) -> QPropertyAnimation:
        """Create a fade-in animation (opacity 0 → 1).

        Uses QGraphicsOpacityEffect on the widget. For widgets with
        QGraphicsDropShadowEffect, use windowOpacity instead (D10).

        Args:
            widget: Target widget (must not be None).
            duration: Duration in ms or name from DURATIONS.
            easing: Easing curve type or name from EASINGS.
            delete_when_stopped: Whether Qt should delete the C++ object when
                the animation stops. Long-lived owners that store the returned
                wrapper should set this to False and clear their reference on
                ``finished``.

        Returns:
            Configured QPropertyAnimation (not started).

        Raises:
            ValueError: If widget is None.
        """
        # Safety 1: Verify widget is not None
        if widget is None:
            raise ValueError("fade_in: widget must not be None")

        from PySide6.QtWidgets import QGraphicsOpacityEffect

        # Get or create opacity effect
        effect = widget.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)

        resolved_duration = Motion._resolve_duration(duration)
        resolved_easing = Motion._resolve_easing(easing)
        effective_duration = Motion._effective_duration(resolved_duration)

        anim = QPropertyAnimation(effect, b"opacity")
        anim.setDuration(effective_duration)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(resolved_easing)
        Motion._apply_safety(widget, anim)
        deletion_policy = (
            QAbstractAnimation.DeletionPolicy.DeleteWhenStopped
            if delete_when_stopped
            else QAbstractAnimation.DeletionPolicy.KeepWhenStopped
        )
        anim.start(deletion_policy)
        return anim

    @staticmethod
    def fade_out(
        widget: QWidget,
        *,
        duration: int | str = "modal",
        easing: QEasingCurve.Type | str = "accelerate",
        delete_when_stopped: bool = True,
    ) -> QPropertyAnimation:
        """Create a fade-out animation (opacity 1 → 0).

        Uses QGraphicsOpacityEffect on the widget. For widgets with
        QGraphicsDropShadowEffect, use windowOpacity instead (D10).

        Args:
            widget: Target widget (must not be None).
            duration: Duration in ms or name from DURATIONS.
            easing: Easing curve type or name from EASINGS.
            delete_when_stopped: Whether Qt should delete the C++ object when
                the animation stops. Long-lived owners that store the returned
                wrapper should set this to False and clear their reference on
                ``finished``.

        Returns:
            Configured QPropertyAnimation (not started).

        Raises:
            ValueError: If widget is None.
        """
        if widget is None:
            raise ValueError("fade_out: widget must not be None")

        from PySide6.QtWidgets import QGraphicsOpacityEffect

        effect = widget.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)

        resolved_duration = Motion._resolve_duration(duration)
        resolved_easing = Motion._resolve_easing(easing)
        effective_duration = Motion._effective_duration(resolved_duration)

        anim = QPropertyAnimation(effect, b"opacity")
        anim.setDuration(effective_duration)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(resolved_easing)
        Motion._apply_safety(widget, anim)
        deletion_policy = (
            QAbstractAnimation.DeletionPolicy.DeleteWhenStopped
            if delete_when_stopped
            else QAbstractAnimation.DeletionPolicy.KeepWhenStopped
        )
        anim.start(deletion_policy)
        return anim

    @staticmethod
    def slide_in(
        widget: QWidget,
        *,
        prop: bytes = b"pos",
        start_value: object = None,
        end_value: object = None,
        duration: int | str = "panel-slide",
        easing: QEasingCurve.Type | str = "decelerate",
    ) -> QPropertyAnimation:
        """Create a slide-in animation.

        Note: Geometry animations are BLOCKED on macOS (D1).
        Use animations_supported('geometry') to check before calling.

        Args:
            widget: Target widget (must not be None).
            prop: Property name to animate (default: b"pos").
            start_value: Starting value for the property.
            end_value: Ending value for the property.
            duration: Duration in ms or name from DURATIONS.
            easing: Easing curve type or name from EASINGS.

        Returns:
            Configured QPropertyAnimation (not started).

        Raises:
            ValueError: If widget is None.
        """
        if widget is None:
            raise ValueError("slide_in: widget must not be None")

        resolved_duration = Motion._resolve_duration(duration)
        resolved_easing = Motion._resolve_easing(easing)
        effective_duration = Motion._effective_duration(resolved_duration)

        anim = QPropertyAnimation(widget, prop)
        anim.setDuration(effective_duration)
        if start_value is not None:
            anim.setStartValue(start_value)
        if end_value is not None:
            anim.setEndValue(end_value)
        anim.setEasingCurve(resolved_easing)
        Motion._apply_safety(widget, anim)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        return anim

    @staticmethod
    def slide_in_safe(
        widget: QWidget,
        *,
        start_pos: QPoint,
        end_pos: QPoint,
        duration: int | str = "panel-slide",
        easing: QEasingCurve.Type | str = "decelerate",
    ) -> QTimer | QPropertyAnimation:
        """Platform-safe slide-in: QTimer on macOS, QPropertyAnimation elsewhere.

        On macOS, ``QPropertyAnimation(geometry)`` is blocked (D1) because
        the AppKit compositor produces visual artifacts on nested translucent
        widgets.  This method falls back to a ``QTimer``-driven discrete
        position animation using ``widget.move()`` with OutCubic easing
        applied manually.

        Args:
            widget: Target widget (must not be None).
            start_pos: Starting position.
            end_pos: Ending position.
            duration: Duration in ms or name from DURATIONS.
            easing: Easing curve type or name from EASINGS.

        Returns:
            The animation object (QTimer on macOS, QPropertyAnimation elsewhere).

        Raises:
            ValueError: If widget is None.
        """
        if widget is None:
            raise ValueError("slide_in_safe: widget must not be None")

        resolved_duration = Motion._resolve_duration(duration)
        effective_duration = Motion._effective_duration(resolved_duration)

        # Non-macOS: use the standard QPropertyAnimation path
        if sys.platform != "darwin" or not constants.ANIMATIONS_ENABLED:
            if not constants.ANIMATIONS_ENABLED:
                widget.move(end_pos)
                # Return a dummy stopped timer for consistent return type
                t = QTimer(widget)
                t.setSingleShot(True)
                return t
            resolved_easing = Motion._resolve_easing(easing)
            anim = QPropertyAnimation(widget, b"pos")
            anim.setDuration(effective_duration)
            anim.setStartValue(start_pos)
            anim.setEndValue(end_pos)
            anim.setEasingCurve(resolved_easing)
            Motion._apply_safety(widget, anim)
            anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
            return anim

        # macOS: QTimer-driven discrete position animation
        widget.move(start_pos)
        total_steps = max(1, effective_duration // 16)  # ~60fps
        dx = end_pos.x() - start_pos.x()
        dy = end_pos.y() - start_pos.y()
        state: dict[str, int] = {"step": 0}

        timer = QTimer(widget)
        timer.setInterval(16)

        def _tick() -> None:
            state["step"] += 1
            step = state["step"]
            if step >= total_steps:
                widget.move(end_pos)
                timer.stop()
                return
            # Normalized progress [0, 1]
            t = step / total_steps
            # Apply easing (OutCubic by default)
            eased = 1.0 - (1.0 - t) ** 3
            x = start_pos.x() + int(dx * eased)
            y = start_pos.y() + int(dy * eased)
            try:
                widget.move(x, y)
            except RuntimeError:
                timer.stop()

        timer.timeout.connect(_tick)
        # Safety: stop timer if widget is destroyed
        widget.destroyed.connect(timer.stop)
        # Cache timer on widget to prevent GC
        widget.setProperty("_motion_slide_timer", timer)
        timer.start()
        return timer

    @staticmethod
    def slide_out(
        widget: QWidget,
        *,
        prop: bytes = b"pos",
        start_value: object = None,
        end_value: object = None,
        duration: int | str = "panel-slide",
        easing: QEasingCurve.Type | str = "accelerate",
    ) -> QPropertyAnimation:
        """Create a slide-out animation.

        Note: Geometry animations are BLOCKED on macOS (D1).
        Use animations_supported('geometry') to check before calling.

        Args:
            widget: Target widget (must not be None).
            prop: Property name to animate (default: b"pos").
            start_value: Starting value for the property.
            end_value: Ending value for the property.
            duration: Duration in ms or name from DURATIONS.
            easing: Easing curve type or name from EASINGS.

        Returns:
            Configured QPropertyAnimation (not started).

        Raises:
            ValueError: If widget is None.
        """
        if widget is None:
            raise ValueError("slide_out: widget must not be None")

        resolved_duration = Motion._resolve_duration(duration)
        resolved_easing = Motion._resolve_easing(easing)
        effective_duration = Motion._effective_duration(resolved_duration)

        anim = QPropertyAnimation(widget, prop)
        anim.setDuration(effective_duration)
        if start_value is not None:
            anim.setStartValue(start_value)
        if end_value is not None:
            anim.setEndValue(end_value)
        anim.setEasingCurve(resolved_easing)
        Motion._apply_safety(widget, anim)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        return anim

    @staticmethod
    def scale(
        widget: QWidget,
        *,
        start_value: float = 1.0,
        end_value: float = 1.0,
        duration: int | str = "toggle",
        easing: QEasingCurve.Type | str = "emphasized",
    ) -> QPropertyAnimation:
        """Create a scale animation using a custom 'scale' property.

        The widget must have a 'scale' property defined (see ActionButton).

        Args:
            widget: Target widget (must not be None).
            start_value: Starting scale factor.
            end_value: Ending scale factor.
            duration: Duration in ms or name from DURATIONS.
            easing: Easing curve type or name from EASINGS.

        Returns:
            Configured QPropertyAnimation (not started).

        Raises:
            ValueError: If widget is None.
        """
        if widget is None:
            raise ValueError("scale: widget must not be None")

        resolved_duration = Motion._resolve_duration(duration)
        resolved_easing = Motion._resolve_easing(easing)
        effective_duration = Motion._effective_duration(resolved_duration)

        anim = QPropertyAnimation(widget, b"scale")
        anim.setDuration(effective_duration)
        anim.setStartValue(start_value)
        anim.setEndValue(end_value)
        anim.setEasingCurve(resolved_easing)
        Motion._apply_safety(widget, anim)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        return anim

    @staticmethod
    def pulse(
        widget: QWidget,
        *,
        prop: bytes = b"opacity",
        start_value: float = 0.4,
        end_value: float = 1.0,
        duration: int | str = "pulse",
        easing: QEasingCurve.Type | str = "standard",
        loop_count: int = -1,
    ) -> QPropertyAnimation:
        """Create a pulsing animation (infinite loop by default).

        Args:
            widget: Target widget (must not be None).
            prop: Property name to animate (default: b"opacity").
            start_value: Minimum value during pulse.
            end_value: Maximum value during pulse.
            duration: Duration in ms or name from DURATIONS.
            easing: Easing curve type or name from EASINGS.
            loop_count: Number of loops (-1 for infinite).

        Returns:
            Configured QPropertyAnimation (not started).

        Raises:
            ValueError: If widget is None.
        """
        if widget is None:
            raise ValueError("pulse: widget must not be None")

        resolved_duration = Motion._resolve_duration(duration)
        resolved_easing = Motion._resolve_easing(easing)
        effective_duration = Motion._effective_duration(resolved_duration)

        anim = QPropertyAnimation(widget, prop)
        anim.setDuration(effective_duration)
        anim.setStartValue(start_value)
        anim.setEndValue(end_value)
        anim.setEasingCurve(resolved_easing)
        anim.setLoopCount(loop_count)
        Motion._apply_safety(widget, anim)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        return anim

    @staticmethod
    def collapse_height(
        widget: QWidget,
        *,
        start_height: int,
        end_height: int,
        duration: int | str = "toggle",
        easing: QEasingCurve.Type | str = "standard",
    ) -> QPropertyAnimation:
        """Create a height collapse/expand animation.

        Note: Geometry animations are BLOCKED on macOS (D1).
        Use animations_supported('geometry') to check before calling.

        Args:
            widget: Target widget (must not be None).
            start_height: Starting height in pixels.
            end_height: Ending height in pixels.
            duration: Duration in ms or name from DURATIONS.
            easing: Easing curve type or name from EASINGS.

        Returns:
            Configured QPropertyAnimation (not started).

        Raises:
            ValueError: If widget is None.
        """
        if widget is None:
            raise ValueError("collapse_height: widget must not be None")

        resolved_duration = Motion._resolve_duration(duration)
        resolved_easing = Motion._resolve_easing(easing)
        effective_duration = Motion._effective_duration(resolved_duration)

        anim = QPropertyAnimation(widget, b"minimumHeight")
        anim.setDuration(effective_duration)
        anim.setStartValue(start_height)
        anim.setEndValue(end_height)
        anim.setEasingCurve(resolved_easing)
        Motion._apply_safety(widget, anim)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        return anim
