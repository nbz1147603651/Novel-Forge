"""Sub-module of novel_forge.desktop.components.task_focus.

Auto-generated in the M3.7 split. Contains companion.py classes.
"""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import (
    Property,
    QAbstractAnimation,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QContextMenuEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from novel_forge.common.token_usage import format_token_count
from novel_forge.desktop.jobs import DesktopJobState
from novel_forge.desktop.pets import (
    load_pet_atlas as _load_pet_atlas,
)
from novel_forge.desktop.pets import (
    load_pet_definition,
)
from novel_forge.desktop.pets import (
    load_pet_pixmap as _load_pet_pixmap,
)
from novel_forge.desktop.state.snapshot_cache import JobSnapshotCache
from novel_forge.desktop.task_flow import TaskFlowOutcome, task_flow_outcome
from novel_forge.desktop.task_observation import (
    ObservedTaskState,
    TaskFocusScope,
    TaskObservationStore,
)
from novel_forge.desktop.theme import resolve_qcolor

# Module-level singleton cache (process-local).
_model_call_snapshot_cache = JobSnapshotCache(max_entries=64, ttl_s=60.0)

_STREAM_DISPLAY_LIMIT = 50_000
_IDLE_PET_ANIMATION_CYCLE: tuple[tuple[str, int], ...] = (
    ("idle", 24),
    ("waving", 4),
    ("idle", 18),
    ("jumping", 5),
)
_IDLE_PET_ANIMATION_TICKS = sum(duration for _, duration in _IDLE_PET_ANIMATION_CYCLE)

# Non-looping states that should play through exactly once then return to idle.
_NON_LOOPING_STATES: frozenset[str] = frozenset({"waving", "jumping"})

# Keep the sprite surface fully transparent in every state.  Status remains
# visible through the state label and task bubbles; painting a filled glow
# behind the sprite reads as a card/background once the pet is scaled down.
_STATUS_GLOW_TOKENS: dict[str, tuple[str, int] | None] = {
    "idle": None,
    "running": None,
    "decision": None,
    "paused": None,
    "waiting": None,
    "failed": None,
    "review": None,
}


def _status_glow_color(state_name: str) -> QColor:
    """Resolve the glow QColor for a companion state at call time."""
    entry = _STATUS_GLOW_TOKENS.get(state_name)
    if entry is None:
        return resolve_qcolor("text.muted", 0)
    token, alpha = entry
    return resolve_qcolor(token, alpha)


def _task_bubble_status(state: ObservedTaskState) -> str:
    """Return the small, presentation-only status used by a task bubble."""

    outcome = task_flow_outcome(state.job)
    if outcome == TaskFlowOutcome.CANCELLED:
        return "cancelled"
    if state.has_active_decision:
        return "decision"
    if state.job.status == DesktopJobState.SUCCEEDED:
        return "success"
    if state.job.status == DesktopJobState.FAILED:
        return "failed"
    if state.job.status == DesktopJobState.PAUSED:
        return "paused"
    if state.job.status == DesktopJobState.QUEUED:
        return "queued"
    return "running"


def _task_bubble_detail(state: ObservedTaskState, status: str) -> str:
    if status == "success":
        detail = state.events[-1] if state.events else "任务已完成"
    elif status == "failed":
        detail = str(state.job.error or state.current_node or "任务执行失败").strip()
    elif status == "cancelled":
        detail = "任务已取消"
    elif status == "decision":
        detail = "等待你的确认"
    else:
        detail = state.current_node or state.status_label
    detail = " ".join(str(detail or "").split())
    if status in {"running", "paused", "decision"} and state.progress_percent > 0:
        detail = f"{detail} · {state.progress_percent}%"
    return detail[:72] + ("…" if len(detail) > 72 else "")


class _TaskBubbleStatusIcon(QWidget):
    """Paint a crisp task-state glyph without depending on emoji fonts."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._status = "running"
        self._angle = 0
        self.setFixedSize(24, 24)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_status(self, status: str) -> None:
        self._status = status
        self.update()

    def advance(self) -> None:
        if self._status == "running":
            self._angle = (self._angle + 32) % 360
            self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802
        event.accept()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(3.0, 3.0, self.width() - 6.0, self.height() - 6.0)
        status = self._status

        if status == "running":
            painter.setPen(QPen(resolve_qcolor("border.default", 85), 2.0))
            painter.drawArc(rect, 0, 360 * 16)
            painter.setPen(QPen(resolve_qcolor("accent.primary"), 2.4))
            painter.drawArc(rect, -self._angle * 16, -250 * 16)
        elif status == "success":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(resolve_qcolor("status.success.job")))
            painter.drawEllipse(rect)
            check = QPainterPath()
            check.moveTo(7.0, 12.0)
            check.lineTo(10.3, 15.2)
            check.lineTo(17.2, 8.2)
            painter.setPen(QPen(resolve_qcolor("bg.surface"), 2.0))
            painter.drawPath(check)
        elif status == "failed":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(resolve_qcolor("status.danger.alt")))
            painter.drawEllipse(rect)
            painter.setPen(QPen(resolve_qcolor("bg.surface"), 2.0))
            painter.drawLine(8, 8, 16, 16)
            painter.drawLine(16, 8, 8, 16)
        elif status == "decision":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(resolve_qcolor("status.warning")))
            painter.drawEllipse(rect)
            painter.setPen(QPen(resolve_qcolor("bg.surface"), 2.0))
            painter.drawLine(12, 7, 12, 13)
            painter.drawPoint(12, 17)
        else:
            token = "text.muted" if status in {"paused", "cancelled"} else "border.default"
            painter.setPen(QPen(resolve_qcolor(token), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect)
            if status == "paused":
                painter.drawLine(9, 8, 9, 16)
                painter.drawLine(15, 8, 15, 16)
            elif status == "cancelled":
                painter.drawLine(8, 8, 16, 16)
            else:
                painter.drawLine(8, 12, 16, 12)
        painter.end()


class _TaskProgressBubbleCard(QFrame):
    """One compact task card shown next to the pet."""

    activated = Signal()

    _TONE_BY_STATUS = {
        "success": "success",
        "failed": "danger",
        "decision": "warning",
        "paused": "muted",
        "cancelled": "muted",
        "queued": "muted",
        "running": "active",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("taskPetBubbleCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("tone", "active")

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 11, 12, 10)
        row.setSpacing(10)
        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(3)

        self._title = QLabel("", self)
        self._title.setObjectName("taskPetBubbleTitle")
        self._title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._detail = QLabel("", self)
        self._detail.setObjectName("taskPetBubbleDetail")
        self._detail.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._progress = QProgressBar(self)
        self._progress.setObjectName("taskPetBubbleProgress")
        self._progress.setRange(0, 100)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(3)
        text_column.addWidget(self._title)
        text_column.addWidget(self._detail)
        text_column.addWidget(self._progress)

        self._status_icon = _TaskBubbleStatusIcon(self)
        row.addLayout(text_column, 1)
        row.addWidget(self._status_icon, 0, Qt.AlignmentFlag.AlignTop)

    def set_compact(self, compact: bool) -> None:
        self.setFixedHeight(74 if compact else 82)
        for label in (self._title, self._detail):
            label.setProperty("compact", compact)
            label.style().unpolish(label)
            label.style().polish(label)

    def set_state(self, state: ObservedTaskState) -> None:
        status = _task_bubble_status(state)
        tone = self._TONE_BY_STATUS[status]
        self.setProperty("tone", tone)
        self._progress.setProperty("tone", tone)
        for widget in (self, self._progress):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        title = str(state.job.label or state.current_node or "后台任务").strip()
        self._title.setText(title[:42] + ("…" if len(title) > 42 else ""))
        self._detail.setText(_task_bubble_detail(state, status))
        progress = 100 if status == "success" else max(0, min(100, state.progress_percent))
        self._progress.setValue(progress)
        self._status_icon.set_status(status)
        self.setToolTip("\n".join(part for part in (title, self._detail.text()) if part))

    def advance_spinner(self) -> None:
        self._status_icon.advance()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _TaskProgressBubblePanel(QWidget):
    """Transparent sibling overlay containing the two latest task bubbles."""

    activated = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if parent is None:
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.Tool
                | Qt.WindowType.WindowDoesNotAcceptFocus
            )
        self.setObjectName("taskPetBubblePanel")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAutoFillBackground(False)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._cards = tuple(_TaskProgressBubbleCard(self) for _ in range(2))
        for card in self._cards:
            card.activated.connect(self.activated.emit)
            self._layout.addWidget(card)
            card.hide()
        self.hide()

    def update_states(self, states: list[ObservedTaskState], *, compact: bool) -> None:
        self.setFixedWidth(332 if compact else 392)
        for index, card in enumerate(self._cards):
            if index >= len(states):
                card.hide()
                continue
            card.set_compact(compact)
            card.set_state(states[index])
            card.show()
        visible_count = min(len(states), len(self._cards))
        card_height = 74 if compact else 82
        self.setFixedHeight(visible_count * card_height + max(0, visible_count - 1) * 8)

    def advance_spinners(self) -> None:
        for card in self._cards:
            if card.isVisible():
                card.advance_spinner()


class _StatusGlowLabel(QLabel):
    """QLabel subclass that paints a soft pulsing glow ring behind the pixmap.

    The glow colour is set via ``set_glow_color`` and its intensity is driven
    by the ``glow_opacity`` Qt property (0.0–1.0) so a QPropertyAnimation can
    smoothly pulse it.  This is D1-safe on macOS — only opacity changes, no
    geometry animations.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._glow_color: QColor = resolve_qcolor("text.muted", 0)
        self._glow_opacity: float = 0.0

    def _get_glow_opacity(self) -> float:
        return self._glow_opacity

    def _set_glow_opacity(self, value: float) -> None:
        self._glow_opacity = max(0.0, min(1.0, value))
        self.update()

    def set_glow_color(self, color: QColor) -> None:
        self._glow_color = QColor(color)
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802
        if self._glow_opacity > 0.01 and not self._glow_color.alpha() == 0:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            glow = QColor(self._glow_color)
            glow.setAlpha(int(glow.alpha() * self._glow_opacity))
            pen = QPen(Qt.PenStyle.NoPen)
            painter.setPen(pen)
            painter.setBrush(QBrush(glow))
            rect = self.contentsRect()
            # Draw a soft elliptical glow behind the sprite, slightly larger
            margin = max(2, min(rect.width(), rect.height()) // 10)
            glow_rect = rect.adjusted(-margin, -margin, margin, margin)
            painter.drawEllipse(glow_rect)
            painter.end()
        super().paintEvent(event)

    glow_opacity = Property(float, _get_glow_opacity, _set_glow_opacity)


class FloatingTaskCompanion(QWidget):
    """Small floating launcher for active task observation.

    Animation improvements over the baseline:

    * **Cross-fade on state switch** — a 180 ms opacity fade (D1-safe)
      smoothly transitions the sprite when the pet changes state.
    * **Non-looping animations** — ``waving`` and ``jumping`` now play
      through exactly once and auto-return to ``idle`` instead of
      repeating the last frame.
    * **Transparent sprite surface** — status is communicated by text and
      task bubbles without painting a filled backdrop behind the pet.
    * **Improved fallback animation** — when no atlas is available the
      sprite uses a smooth sinusoidal breathing/bobbing offset instead
      of a crude 4-step staircase.
    """

    activated = Signal()
    hide_requested = Signal()
    position_changed = Signal(QPoint)
    reset_position_requested = Signal()

    _CROSSFADE_DURATION_MS: int = 180
    _GLOW_PULSE_DURATION_MS: int = 1800

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("floatingTaskCompanion")
        # The companion is an overlay launcher, not a themed Surface/card.
        # Keeping it as a plain QWidget prevents the global Surface QSS from
        # creating an opaque rounded panel in the macOS backing store.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAutoFillBackground(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pet = load_pet_definition()
        self._atlas_pixmap = _load_pet_atlas(self._pet)
        self._sprite_pixmap = _load_pet_pixmap(self._pet)
        self._pet_state = "idle"
        self._compact = True
        self._store: TaskObservationStore | None = None
        self._user_visible = True
        self._pulse = 0
        self._has_active_attention = False
        self._idle_animation_tick = 0
        self._drag_press_global_pos: QPoint | None = None
        self._drag_press_widget_pos: QPoint | None = None
        self._dragging = False
        # Non-looping animation tracking
        self._non_loop_return_state: str = "idle"
        self._non_loop_active: bool = False

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(10, 8, 10, 8)
        self._layout.setSpacing(0)

        self._bubble_expanded = True
        self._bubble_panel = _TaskProgressBubblePanel(parent)
        self._bubble_panel.activated.connect(self.activated.emit)
        self._bubble_toggle = QToolButton(self)
        self._bubble_toggle.setObjectName("taskPetBubbleToggle")
        self._bubble_toggle.setArrowType(Qt.ArrowType.UpArrow)
        self._bubble_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bubble_toggle.clicked.connect(self._toggle_task_bubbles)
        self._bubble_toggle.hide()

        # Use the glow-capable label for the sprite.
        self._sprite = _StatusGlowLabel(parent=self)
        self._sprite.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sprite.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # The disclosure control participates in layout instead of floating
        # over the sprite.  This keeps the pet's head and animation frames
        # unobstructed at every size.
        self._pet_row = QHBoxLayout()
        self._pet_row.setContentsMargins(0, 0, 0, 0)
        self._pet_row.setSpacing(4)
        self._pet_row.addStretch(1)
        self._pet_row.addWidget(self._sprite, 0, Qt.AlignmentFlag.AlignCenter)
        self._pet_row.addWidget(self._bubble_toggle, 0, Qt.AlignmentFlag.AlignTop)
        self._pet_row.addStretch(1)
        self._layout.addLayout(self._pet_row)

        # Opacity effect for cross-fade transitions (D1-safe: opacity only)
        self._sprite_opacity = QGraphicsOpacityEffect(self._sprite)
        self._sprite_opacity.setOpacity(1.0)
        self._sprite.setGraphicsEffect(self._sprite_opacity)
        self._fade_anim: QPropertyAnimation | None = None

        # Glow pulse animation (D1-safe: drives glow_opacity, not geometry)
        self._glow_anim: QPropertyAnimation | None = None
        self._setup_glow_animation()

        self._title = QLabel(self._pet.display_name)
        self._title.setObjectName("cardTitle")
        self._title.setProperty("compact", True)
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(self._title)
        self._meta = QLabel(self._pet.state("idle").label)
        self._meta.setObjectName("cardMeta")
        self._meta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(self._meta)
        self._usage = QLabel("")
        self._usage.setObjectName("taskPetUsage")
        self._usage.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._usage.setVisible(False)
        self._layout.addWidget(self._usage)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setToolTip(self._pet.description or self._pet.display_name)
        self.set_compact_mode(False)
        self.setVisible(False)

    # ── Glow animation setup ───────────────────────────────────────

    def _setup_glow_animation(self) -> None:
        """Create a looping glow-opacity pulse (D1-safe on macOS)."""
        self._glow_anim = QPropertyAnimation(self._sprite, b"glow_opacity", self)
        self._glow_anim.setDuration(self._GLOW_PULSE_DURATION_MS)
        self._glow_anim.setStartValue(0.3)
        self._glow_anim.setKeyValueAt(0.5, 1.0)
        self._glow_anim.setEndValue(0.3)
        self._glow_anim.setLoopCount(-1)  # infinite
        self._glow_anim.setEasingCurve(QEasingCurve.Type.SineCurve)

    def _update_glow_for_state(self, state_name: str) -> None:
        """Update glow colour and start/stop the pulse animation."""
        color = _status_glow_color(state_name)
        self._sprite.set_glow_color(color)
        if color.alpha() == 0:
            # No glow for idle — stop pulse
            if (
                self._glow_anim is not None
                and self._glow_anim.state() == QAbstractAnimation.State.Running
            ):
                self._glow_anim.stop()
            self._sprite._set_glow_opacity(0.0)
        else:
            if (
                self._glow_anim is not None
                and self._glow_anim.state() != QAbstractAnimation.State.Running
            ):
                self._glow_anim.start()

    def refresh_theme_colors(self) -> None:
        """Resolve the active glow again after a desktop-theme change."""
        self._update_glow_for_state(self._pet_state)

    # ── Cross-fade helper ──────────────────────────────────────────

    def _crossfade_sprite(self) -> None:
        """Trigger a quick opacity fade-out → fade-in on state change.

        D1-safe on macOS: only opacity is animated, no geometry.
        """
        if (
            self._fade_anim is not None
            and self._fade_anim.state() == QAbstractAnimation.State.Running
        ):
            self._fade_anim.stop()

        anim = QPropertyAnimation(self._sprite_opacity, b"opacity")
        anim.setDuration(self._CROSSFADE_DURATION_MS)
        anim.setStartValue(0.4)
        anim.setKeyValueAt(0.35, 0.4)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda anim=anim: self._clear_fade_animation(anim))
        self._fade_anim = anim
        anim.start()

    def _clear_fade_animation(self, anim: QPropertyAnimation | None) -> None:
        if anim is not None and self._fade_anim is anim:
            self._fade_anim = None

    def set_compact_mode(self, compact: bool) -> None:
        if self._compact == compact and self.width() > 0:
            return
        self._compact = compact
        width, height = self._pet.compact_size if compact else self._pet.normal_size
        if compact:
            self._layout.setContentsMargins(4, 4, 4, 4)
            self._pet_row.setSpacing(2)
            self._bubble_toggle.setFixedSize(22, 22)
        else:
            self._layout.setContentsMargins(8, 6, 8, 6)
            self._pet_row.setSpacing(4)
            self._bubble_toggle.setFixedSize(26, 26)
        self.setFixedSize(width, height)
        sprite_box = self._pet.compact_sprite_size if compact else self._pet.normal_sprite_size
        self._sprite.setFixedSize(sprite_box, sprite_box)
        self._title.setVisible(not compact)
        self._usage.setProperty("compact", compact)
        self._meta.setProperty("compact", compact)
        self.style().unpolish(self._meta)
        self.style().polish(self._meta)
        self.style().unpolish(self._usage)
        self.style().polish(self._usage)
        self._position_bubble_panel()
        self._render_pet_frame()

    def set_user_visible(self, visible: bool) -> None:
        """Apply the persisted user preference without breaking store binding."""

        self._user_visible = bool(visible)
        self.refresh()

    def bind_store(self, store: TaskObservationStore | None) -> None:
        if self._store is store:
            self.refresh()
            return
        if self._store is not None:
            try:
                self._store.changed.disconnect(self.refresh)
            except (RuntimeError, TypeError):
                pass
        self._store = store
        if store is not None:
            store.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        store = self._store
        visible = store is not None and self._user_visible
        self.setVisible(visible)
        if not visible:
            self._timer.stop()
            self._has_active_attention = False
            self._non_loop_active = False
            self._update_glow_for_state("idle")
            self._bubble_panel.hide()
            self._bubble_toggle.hide()
            return
        assert store is not None
        has_attention = store.has_active_attention()
        count = store.active_attention_count() if has_attention else 0
        focus = store.focus_for_scope(TaskFocusScope.GLOBAL) if has_attention else None
        bubble_states = store.candidates_for_scope(TaskFocusScope.GLOBAL)[:2]
        bubble_states.sort(
            key=lambda state: (
                _task_bubble_status(state)
                in {
                    "running",
                    "queued",
                    "paused",
                    "decision",
                }
            )
        )
        self._bubble_panel.update_states(bubble_states, compact=self._compact)
        self._bubble_toggle.setVisible(bool(bubble_states))
        self._update_bubble_visibility(bool(bubble_states))
        if has_attention != self._has_active_attention:
            self._idle_animation_tick = 0
        self._has_active_attention = has_attention

        state_name = self._idle_pet_state()
        if focus is not None:
            if focus.has_active_decision:
                state_name = "decision"
            elif focus.job.status == DesktopJobState.PAUSED:
                state_name = "paused"
            elif focus.job.status == DesktopJobState.QUEUED:
                state_name = "waiting"
            elif focus.job.status == DesktopJobState.FAILED:
                state_name = "failed"
            else:
                state_name = "running"
        self._set_pet_state(state_name)
        state_label = self._pet.state(state_name).label
        if not has_attention:
            meta = self._pet.state("idle").label
        elif state_name == "decision":
            meta = state_label
        elif count > 1:
            meta = f"{count} 项"
        else:
            meta = state_label
        self._meta.setText(meta)
        usage = store.active_usage()
        if usage.total_tokens:
            if self._compact:
                usage_text = f"Token {format_token_count(usage.total_tokens, compact=True)}"
            elif usage.prompt_tokens or usage.completion_tokens:
                usage_text = (
                    f"入 {format_token_count(usage.prompt_tokens, compact=True)} · "
                    f"出 {format_token_count(usage.completion_tokens, compact=True)}"
                )
            else:
                usage_text = f"Token {format_token_count(usage.total_tokens, compact=True)}"
            self._usage.setText(usage_text)
            self._usage.setVisible(True)
        else:
            self._usage.clear()
            self._usage.setVisible(False)
        if focus is not None:
            title = focus.job.label or focus.current_node or self._pet.display_name
            self._title.setText(title[:22] + ("…" if len(title) > 22 else ""))
            tooltip_parts = [title, focus.current_node, f"Token {usage.total_tokens:,}"]
            if usage.cost_usd:
                tooltip_parts.append(f"实际费用 ${usage.cost_usd:.4f}")
            self.setToolTip("\n".join(part for part in tooltip_parts if part))
        else:
            self._title.setText(self._pet.display_name)
            self.setToolTip(self._pet.description or self._pet.display_name)
        if not self._timer.isActive():
            self._timer.start()
        self._render_pet_frame()
        self._position_bubble_panel()
        self.raise_()

    def _toggle_task_bubbles(self) -> None:
        self._bubble_expanded = not self._bubble_expanded
        self.refresh()

    def _update_bubble_visibility(self, has_states: bool) -> None:
        show_bubbles = self._user_visible and self._bubble_expanded and has_states
        self._bubble_panel.setVisible(show_bubbles)
        self._bubble_toggle.setArrowType(
            Qt.ArrowType.UpArrow if self._bubble_expanded else Qt.ArrowType.DownArrow
        )
        action_label = "收起任务气泡" if self._bubble_expanded else "展开任务气泡"
        self._bubble_toggle.setToolTip(action_label)
        self._bubble_toggle.setAccessibleName(action_label)
        if show_bubbles:
            self._bubble_panel.raise_()

    def _position_bubble_panel(self) -> None:
        panel = self._bubble_panel
        if panel.width() <= 0 or panel.height() <= 0:
            return
        parent = self.parentWidget()
        if parent is None:
            panel.move(self.x() + self.width() - panel.width(), self.y() - panel.height() - 8)
            return

        margin = 8
        max_x = max(margin, parent.width() - panel.width() - margin)
        max_y = max(margin, parent.height() - panel.height() - margin)
        x = min(max(self.x() + self.width() - panel.width(), margin), max_x)
        y = self.y() - panel.height() - 8
        if y < margin:
            left_x = self.x() - panel.width() - 10
            if left_x >= margin:
                x = left_x
                y = self.y() + self.height() - panel.height()
            else:
                y = self.y() + self.height() + 8
        panel.move(x, min(max(y, margin), max_y))
        if panel.isVisible():
            panel.raise_()
            self.raise_()

    def moveEvent(self, event: Any) -> None:  # noqa: N802
        super().moveEvent(event)
        self._position_bubble_panel()

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_bubble_panel()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_press_global_pos = event.globalPosition().toPoint()
            self._drag_press_widget_pos = self.pos()
            self._dragging = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            event.buttons() & Qt.MouseButton.LeftButton
            and self._drag_press_global_pos is not None
            and self._drag_press_widget_pos is not None
        ):
            delta = event.globalPosition().toPoint() - self._drag_press_global_pos
            if not self._dragging:
                threshold = QApplication.startDragDistance()
                if delta.manhattanLength() < threshold:
                    event.accept()
                    return
                self._dragging = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            new_pos = self._drag_press_widget_pos + delta
            self.move(new_pos)
            self.position_changed.emit(new_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._drag_press_global_pos is not None:
            was_dragging = self._dragging
            self._drag_press_global_pos = None
            self._drag_press_widget_pos = None
            self._dragging = False
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            if was_dragging:
                self.position_changed.emit(self.pos())
            else:
                self.activated.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_press_global_pos = None
            self._drag_press_widget_pos = None
            self._dragging = False
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.reset_position_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802
        menu = QMenu(self)
        reset_action = menu.addAction("回到右下角")
        menu.addSeparator()
        hide_action = menu.addAction("隐藏 Nimo")
        hide_action.setToolTip("可在设置 · 界面外观中重新显示，也可按 Ctrl+Alt+N 召回")
        selected = menu.exec(event.globalPos())
        if selected is reset_action:
            self.reset_position_requested.emit()
            event.accept()
            return
        if selected is hide_action:
            self.set_user_visible(False)
            self.hide_requested.emit()
            event.accept()
            return
        super().contextMenuEvent(event)

    def _tick(self) -> None:
        self._bubble_panel.advance_spinners()
        if not self._has_active_attention:
            self._idle_animation_tick = (self._idle_animation_tick + 1) % max(
                1,
                _IDLE_PET_ANIMATION_TICKS,
            )
            idle_state = self._idle_pet_state()
            if idle_state != self._pet_state:
                self._set_pet_state(idle_state)
                self._meta.setText(self._pet.state("idle").label)
                return
        else:
            # Non-looping animation: auto-return to idle after play-through
            if self._non_loop_active and self._is_non_looping_state(self._pet_state):
                frame_count = self._frame_count_for_current_state()
                if self._pulse + 1 >= frame_count:
                    self._non_loop_active = False
                    self._set_pet_state(self._non_loop_return_state)
                    state_label = self._pet.state(self._non_loop_return_state).label
                    self._meta.setText(state_label)
                    return
        frame_count = self._frame_count_for_current_state()
        self._pulse = (self._pulse + 1) % frame_count
        self._render_pet_frame()

    def _idle_pet_state(self) -> str:
        tick = self._idle_animation_tick % max(1, _IDLE_PET_ANIMATION_TICKS)
        elapsed = 0
        for state_name, duration in _IDLE_PET_ANIMATION_CYCLE:
            elapsed += duration
            if tick < elapsed:
                return state_name
        return "idle"

    def _set_pet_state(self, state_name: str) -> None:
        if self._pet_state == state_name and self._timer.interval() > 0:
            return
        prev_state = self._pet_state
        self._pet_state = state_name
        self._pulse = 0
        # Track non-looping animations for auto-return
        if self._is_non_looping_state(state_name) and not self._is_non_looping_state(prev_state):
            self._non_loop_active = True
            self._non_loop_return_state = (
                prev_state if not self._is_non_looping_state(prev_state) else "idle"
            )
        elif not self._is_non_looping_state(state_name):
            self._non_loop_active = False
        self._timer.setInterval(self._pet.state(state_name).interval_ms)
        # Cross-fade on state change (D1-safe: opacity only)
        if prev_state != state_name:
            self._crossfade_sprite()
        # Update glow colour for the new state
        self._update_glow_for_state(state_name)
        self._render_pet_frame()

    def _is_non_looping_state(self, state_name: str) -> bool:
        return state_name in _NON_LOOPING_STATES or not self._pet.state(state_name).loop

    def _frame_count_for_current_state(self) -> int:
        state = self._pet.state(self._pet_state)
        if not self._atlas_pixmap.isNull():
            return max(1, state.frame_count)
        return max(12, state.frame_count)

    def _render_pet_frame(self) -> None:
        frame = self._current_pet_frame()
        if frame.isNull():
            self._sprite.setText(self._pet.display_name[:2])
            return
        base = self._pet.compact_sprite_size if self._compact else self._pet.normal_sprite_size
        if not self._atlas_pixmap.isNull():
            size = max(36, base)
        else:
            size = max(36, base + self._fallback_animation_delta())
        pixmap = frame.scaled(
            QSize(size, size),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._sprite.setPixmap(pixmap)

    def _current_pet_frame(self) -> QPixmap:
        if not self._atlas_pixmap.isNull():
            rect = self._pet.frame_rect(self._pet_state, self._pulse)
            if rect is not None:
                x, y, width, height = rect
                frame = self._atlas_pixmap.copy(x, y, width, height)
                if not frame.isNull():
                    return frame
        return self._sprite_pixmap

    def _fallback_animation_delta(self) -> int:
        """Smooth sinusoidal breathing/bobbing for fallback sprite.

        Replaces the crude 4-step staircase with a continuous sine wave
        for a more natural feel.
        """
        animation = self._pet.state(self._pet_state).animation
        cycle = self._frame_count_for_current_state()
        phase = (self._pulse % cycle) / cycle * 2.0 * math.pi
        if animation == "alert":
            # Fast oscillation for attention states
            return int(round(3.0 * abs(math.sin(phase * 2))))
        if animation == "bob":
            # Moderate bob for running states
            return int(round(2.5 * abs(math.sin(phase))))
        if animation == "breathe":
            # Gentle breathing for idle/waiting
            return int(round(1.5 * abs(math.sin(phase * 0.5))))
        return 0

    def shutdown(self) -> None:
        self._timer.stop()
        self._bubble_panel.hide()
        self._bubble_panel.close()
        if self._glow_anim is not None:
            self._glow_anim.stop()
            self._glow_anim = None
        if self._fade_anim is not None:
            self._fade_anim.stop()
            self._fade_anim = None
        if self._store is not None:
            try:
                self._store.changed.disconnect(self.refresh)
            except (RuntimeError, TypeError):
                pass
        self._store = None
