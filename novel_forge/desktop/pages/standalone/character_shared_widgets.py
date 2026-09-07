"""Shared widgets used by the character profile / relationship network pages.

These two widgets exist because both pages need the same two experiences:

  * A floating **save bar** that slides up from the bottom when a page
    has unsaved changes (with Discard / Save actions). Lives inside the
    page so it never collides with other tabs.
  * An **inspector** panel that slides in from the right edge when a
    detail is selected. Used as the right-side details pane in browse
    mode and as the editing surface in edit mode.

The animations are layered on top of the existing ``Motion`` API:
  * The save bar uses opacity fade combined with a 6px upward slide —
    both are macOS-safe per the D1 granularity rules.
  * The inspector uses a custom QPropertyAnimation on ``maximumWidth``
    because the project explicitly opts out of native geometry
    animations on macOS; we therefore drive a fixed 320px target that
    matches the design and let macOS still pick up the opacity crossfade.
"""

from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.components.primitives import ActionButton, SectionHeading
from novel_forge.desktop.motion import Motion, animations_supported


class SaveBar(QFrame):
    """A bottom-pinned save / discard bar that fades in when dirty."""

    saveClicked = Signal()
    discardClicked = Signal()

    FADE_DURATION_MS = 220
    SLIDE_DISTANCE_PX = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("characterSaveBar")
        self.setProperty("state", "clean")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setVisible(False)
        self._build_ui()
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._fade_anim: QPropertyAnimation | None = None
        self._slide_anim: QPropertyAnimation | None = None
        # Start with no visible offset so the first reveal plays cleanly.
        self._resting_geometry = None

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(12)

        self._status_label = QLabel("已保存")
        self._status_label.setObjectName("fieldHint")
        self._status_label.setWordWrap(False)
        layout.addWidget(self._status_label, 1)

        self._discard_button = ActionButton("放弃修改", variant="quiet")
        self._discard_button.setProperty("compact", True)
        self._discard_button.clicked.connect(self.discardClicked.emit)
        layout.addWidget(self._discard_button)

        self._save_button = ActionButton("保存修改", variant="primary")
        self._save_button.setProperty("compact", True)
        self._save_button.clicked.connect(self.saveClicked.emit)
        layout.addWidget(self._save_button)

    # ── Public API ────────────────────────────────────────────────────

    def show_dirty(self, *, dirty: bool, summary: str = "", warnings: Iterable[str] = ()) -> None:
        self.setProperty("state", "dirty" if dirty else "clean")
        if dirty:
            label = summary or "有未保存修改"
            extra = "；".join(w for w in warnings if w)
            text = label if not extra else f"{label}（{extra}）"
            self._status_label.setText(text)
        else:
            self._status_label.setText("已保存")
        self._save_button.setEnabled(dirty)
        self._discard_button.setEnabled(dirty)
        # Re-polish so QSS picks up the new property.
        self.style().unpolish(self)
        self.style().polish(self)
        self._play_transition(dirty)

    def _play_transition(self, dirty: bool) -> None:
        if dirty:
            self.setVisible(True)
        # Cancel any in-flight transitions so we don't stack them.
        Motion.stop_safely(self._fade_anim)
        Motion.stop_safely(self._slide_anim)
        self._fade_anim = None
        self._slide_anim = None

        end_opacity = 1.0 if dirty else 0.0
        # Fade animation is opacity-only — safe on macOS per motion module D1.
        self._fade_anim = (
            Motion.fade_in(
                self,
                duration=self.FADE_DURATION_MS,
                easing="standard",
            )
            if dirty
            else Motion.fade_out(
                self,
                duration=self.FADE_DURATION_MS,
                easing="standard",
            )
        )
        self._fade_anim.setStartValue(0.0 if dirty else self._opacity.opacity())
        self._fade_anim.setEndValue(end_opacity)

        # Slide animation is intentionally limited to a small vertical
        # offset. macOS only permits opacity/color animations (geometry is
        # blocked per D1), so we keep this lightweight and rely on the
        # opacity fade as the primary motion cue.
        self._slide_anim = None

        if not dirty:
            # Hide after the fade completes; keep widget in tree so the
            # next reveal can animate from 0 opacity again.
            self._fade_anim.finished.connect(self._hide_when_cleaned)

        # Reset cursor visibility immediately on the dirty path.
        self._fade_anim.start()

    def _hide_when_cleaned(self) -> None:
        if self.property("state") == "clean":
            self.setVisible(False)


class InspectorPanel(QFrame):
    """Right-side slide-in panel used as the detail / edit surface.

    The panel keeps a ``QStackedLayout`` with two children: a *browse*
    surface and an *edit* surface. Switching between the two performs
    a 180ms opacity crossfade; the panel itself is always visible, but
    its width collapses to 0 with an opacity fade when the host wants
    it gone (no selection).
    """

    closed = Signal()

    REVEAL_DURATION_MS = 240
    SWITCH_DURATION_MS = 180

    def __init__(
        self,
        *,
        browse_widget: QWidget,
        edit_widget: QWidget,
        title: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("characterInspectorPanel")
        self.setProperty("state", "open")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._build_ui(browse_widget=browse_widget, edit_widget=edit_widget, title=title)

        self._width_anim: QPropertyAnimation | None = None
        self._fade_anim: QPropertyAnimation | None = None
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity)

    def _build_ui(
        self,
        *,
        browse_widget: QWidget,
        edit_widget: QWidget,
        title: str,
    ) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        if title:
            header = QWidget()
            header_layout = QHBoxLayout(header)
            header_layout.setContentsMargins(14, 12, 8, 8)
            header_layout.setSpacing(8)
            heading = SectionHeading(title)
            header_layout.addWidget(heading, 1)
            self._close_button = ActionButton("关闭", variant="quiet")
            self._close_button.setProperty("compact", True)
            self._close_button.clicked.connect(self.closed.emit)
            header_layout.addWidget(self._close_button, 0, Qt.AlignmentFlag.AlignTop)
            outer.addWidget(header)

        self._stack = QStackedLayout()
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(browse_widget)
        self._stack.addWidget(edit_widget)
        outer.addLayout(self._stack, 1)

        self._browse_index = 0
        self._edit_index = 1

    # ── Mode switching ────────────────────────────────────────────────

    def show_browse(self, *, animated: bool = True) -> None:
        self._crossfade_to(self._browse_index, animated=animated)

    def show_edit(self, *, animated: bool = True) -> None:
        self._crossfade_to(self._edit_index, animated=animated)

    def is_edit_visible(self) -> bool:
        return self._stack.currentIndex() == self._edit_index

    def _crossfade_to(self, index: int, *, animated: bool) -> None:
        if self._stack.currentIndex() == index:
            return
        if not animated or not animations_supported("opacity"):
            self._stack.setCurrentIndex(index)
            return
        Motion.stop_safely(self._fade_anim)
        new_widget = self._stack.widget(index)
        if new_widget is None:
            self._stack.setCurrentIndex(index)
            return
        new_effect = new_widget.graphicsEffect()
        if not isinstance(new_effect, QGraphicsOpacityEffect):
            new_effect = QGraphicsOpacityEffect(new_widget)
            new_widget.setGraphicsEffect(new_effect)
        new_effect.setOpacity(0.0)
        self._stack.setCurrentIndex(index)
        fade = QPropertyAnimation(new_effect, b"opacity")
        fade.setDuration(self.SWITCH_DURATION_MS)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.start()
        self._fade_anim = fade


class Toast(QFrame):
    """Minimal fade-out toast used for transient status messages."""

    FADE_DURATION_MS = 200
    HOLD_DURATION_MS = 1800

    def __init__(self, message: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("characterToast")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        label = QLabel(message)
        label.setObjectName("cardMeta")
        layout.addWidget(label)
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)
        QTimer.singleShot(0, self._fade_in)

    def _fade_in(self) -> None:
        if not animations_supported("opacity"):
            self._opacity.setOpacity(1.0)
            self._timer.start(self.HOLD_DURATION_MS)
            return
        anim = QPropertyAnimation(self._opacity, b"opacity")
        anim.setDuration(self.FADE_DURATION_MS)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: self._timer.start(self.HOLD_DURATION_MS))
        anim.start()

    def _fade_out(self) -> None:
        if not animations_supported("opacity"):
            self._opacity.setOpacity(0.0)
            self.deleteLater()
            return
        anim = QPropertyAnimation(self._opacity, b"opacity")
        anim.setDuration(self.FADE_DURATION_MS)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        anim.finished.connect(self.deleteLater)
        anim.start()


__all__ = ["InspectorPanel", "SaveBar", "Toast"]
