"""Checkpoint decision dialog for chapter studio.

Provides a movable floating panel that appears near the right edge of the
chapter workspace when a checkpoint decision is needed in manual/suggest modes.
It can also run as a top-level tool window so users can freely drag it instead
of being boxed into the chapter center column.

Shared helpers ``build_checkpoint_summary_text`` and
``build_checkpoint_dialog_content`` are used by both the Presenter (for inline
summary) and the Dialog (for bubble content), avoiding text divergence.
"""

from __future__ import annotations

import ast
import re
from typing import Any

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.components.primitives import Badge, Surface
from novel_forge.workspace.contracts import DecisionCheckpoint, DecisionOption

# ── Replan-reason extraction (migrated from chapter_studio_action_panel.py) ──

_REPLAN_REASON_PATTERNS = (
    re.compile(r"未通过(?:质量校验|一致性校验)[（(](.+?)[）)]"),
    re.compile(r"未通过(?:质量校验|一致性校验)[:：]\s*([^\n]+)"),
)


def _extract_checkpoint_failure_reason(checkpoint: DecisionCheckpoint) -> str:
    """Extract human-readable failure reason from a replan checkpoint prompt."""
    prompt = str(checkpoint.prompt or "").strip()
    if not prompt:
        return ""
    for pattern in _REPLAN_REASON_PATTERNS:
        match = pattern.search(prompt)
        if match:
            return str(match.group(1)).strip().strip("。")
    return ""


def _strip_replan_notice(prompt: str) -> str:
    """Remove the leading auto-replan notice line from a checkpoint prompt."""
    text = str(prompt or "").strip()
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    first = lines[0]
    if "未通过" in first and ("重试" in first or "重新规划" in first or "重新生成" in first):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _checkpoint_payload_text(item: Any) -> str:
    value = item
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                parsed = ast.literal_eval(stripped)
            except (SyntaxError, ValueError):
                return stripped
            if isinstance(parsed, dict):
                value = parsed
            else:
                return stripped
        else:
            return stripped
    if isinstance(value, dict):
        for key in ("text", "summary", "description", "content", "title"):
            text = str(value.get(key) or "").strip()
            if text:
                return text
        return ""
    return str(value or "").strip()


def _sanitize_summary_text(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    matches = list(re.finditer(r"\{[^{}]*\}", raw))
    if not matches:
        return raw
    parsed: list[str] = []
    for match in matches:
        try:
            payload = ast.literal_eval(match.group(0))
        except (SyntaxError, ValueError):
            continue
        if isinstance(payload, dict):
            item_text = _checkpoint_payload_text(payload)
            if item_text:
                parsed.append(item_text)
    if not parsed:
        return raw
    prefix = raw[: matches[0].start()].strip(" \n;；")
    bullets = "\n".join(f"• {item}" for item in parsed[:6])
    if len(parsed) > 6:
        bullets += f"\n另 {len(parsed) - 6} 条已折叠。"
    return f"{prefix}\n{bullets}".strip() if prefix else bullets


# ── Shared content helpers ───────────────────────────────────────────────────


def build_checkpoint_summary_text(
    checkpoint: DecisionCheckpoint,
    studio_warnings: list[str] | None = None,
) -> str:
    """Build a human-readable summary for a checkpoint.

    Encapsulates the summary/prompt/replan-reason/warnings concatenation logic
    so that both the Presenter (inline display) and the Dialog (bubble content)
    produce identical text.
    """
    reason_text = _extract_checkpoint_failure_reason(checkpoint)
    summary_text = checkpoint.summary or checkpoint.prompt
    prompt_without_notice = _strip_replan_notice(checkpoint.prompt or "")
    if prompt_without_notice and checkpoint.summary and prompt_without_notice != checkpoint.summary:
        summary_text = f"{checkpoint.summary}\n\n{prompt_without_notice}"
    elif checkpoint.prompt and checkpoint.summary and checkpoint.prompt != checkpoint.summary:
        summary_text = f"{checkpoint.summary}\n\n{checkpoint.prompt}"
    if reason_text:
        summary_text = f"⚠️ 上一轮未通过原因：{reason_text}\n\n{summary_text}"
    warnings = [str(item).strip() for item in (studio_warnings or []) if str(item).strip()]
    if warnings:
        summary_text = f"{summary_text}\n\n提醒：{warnings[0]}"
    return _sanitize_summary_text(summary_text)


def build_checkpoint_dialog_content(
    checkpoint: DecisionCheckpoint,
    summary_text: str,
) -> dict[str, Any]:
    """Build structured content dict for the checkpoint dialog bubble panel.

    Returns a dict with keys: title, badge_tone, body_lines, options, has_artifacts.
    """
    has_replan = bool(_extract_checkpoint_failure_reason(checkpoint))
    title = "方案需确认" if checkpoint.checkpoint_type == "plan_checkpoint" else "守卫检查"
    badge_tone = "warning" if has_replan else "default"
    body_lines = [line for line in _sanitize_summary_text(summary_text).split("\n") if line.strip()]
    return {
        "title": title,
        "badge_tone": badge_tone,
        "body_lines": body_lines,
        "options": list(checkpoint.options),
        "has_artifacts": bool(checkpoint.related_artifacts),
    }


# ── Option card ──────────────────────────────────────────────────────────────


class _OptionCard(Surface):
    """Clickable option card that emits ``option_clicked`` with the DecisionOption."""

    option_clicked = Signal(object)

    def __init__(
        self,
        option: DecisionOption,
        *,
        is_recommended: bool,
        is_single: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("panel", parent)
        self._option = option
        self.setObjectName("checkpointOptionCard")
        self.setProperty("recommended", "true" if is_recommended else "false")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)
        self.setMinimumHeight(0)

        # Header row: badge + label
        header = QHBoxLayout()
        header.setSpacing(5)
        if is_recommended:
            badge = Badge("推荐", tone="warning")
            badge.setStyleSheet("font-size: 10px; padding: 1px 6px;")
            header.addWidget(badge)
        label = QLabel(option.label)
        label.setObjectName("cardTitle")
        label.setWordWrap(True)
        header.addWidget(label, 1)
        btn = QPushButton("确认" if is_recommended else "选择")
        btn.setObjectName("checkpointOptionMiniButton")
        btn.setProperty("recommended", "true" if is_recommended else "false")
        btn.setAccessibleName(option.label)
        btn.setToolTip(option.label)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedSize(46, 22)
        btn.clicked.connect(lambda: self.option_clicked.emit(self._option))
        header.addWidget(btn, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        # Description
        if option.description:
            desc = QLabel(option.description)
            desc.setObjectName("cardBody")
            desc.setWordWrap(True)
            layout.addWidget(desc)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        # Clicking anywhere on the card triggers the option
        self.option_clicked.emit(self._option)
        super().mousePressEvent(event)


# ── Bubble panel ─────────────────────────────────────────────────────────────


class _BubblePanel(QWidget):
    """Scrollable panel containing AI message bubble + option cards + notes."""

    option_selected = Signal(object, str)  # (DecisionOption, notes_text)

    def __init__(
        self,
        content: dict[str, Any],
        *,
        initial_notes: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        # A checkpoint dialog can be a top-level ``Qt.Tool`` window on macOS.
        # Do not rely on the transparent scroll viewport to reveal the panel
        # background in that configuration: native viewport composition can
        # instead reveal the chapter page underneath it.
        self.setObjectName("checkpointDialogContent")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # AI message bubble
        bubble = QFrame()
        bubble.setObjectName("checkpointDialogSummary")
        bubble.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(12, 9, 12, 9)
        bubble_layout.setSpacing(4)

        for line in content.get("body_lines", []):
            line_label = QLabel(line)
            line_label.setWordWrap(True)
            bubble_layout.addWidget(line_label)

        layout.addWidget(bubble)

        # Option cards
        options: list[DecisionOption] = content.get("options", [])
        is_single = len(options) == 1
        for option in options:
            card = _OptionCard(
                option,
                is_recommended=option.is_recommended,
                is_single=is_single,
            )
            card.option_clicked.connect(self._on_option_clicked)
            layout.addWidget(card)

        # Notes area
        self._notes = QTextEdit()
        self._notes.setObjectName("checkpointDialogNotes")
        self._notes.setPlaceholderText("对 AI 的补充说明…")
        if initial_notes:
            self._notes.setPlainText(initial_notes)
        self._notes.setMinimumHeight(38)
        self._notes.setMaximumHeight(58)
        layout.addWidget(self._notes)

    def _on_option_clicked(self, option: DecisionOption) -> None:
        notes_text = self._notes.toPlainText().strip()
        self.option_selected.emit(option, notes_text)

    def notes_text(self) -> str:
        """Return current notes text without forcing callers to reach into QTextEdit."""
        return self._notes.toPlainText()


# ── Main dialog ──────────────────────────────────────────────────────────────


class CheckpointDialog(QWidget):
    """Non-modal movable floating panel for checkpoint decisions.

    Signals:
        option_selected(DecisionOption, str): Emitted when user selects an option.
        dismissed(): Emitted when user manually closes the dialog (Escape/close button).
    """

    _MARGIN = 18
    _MIN_WIDTH = 620
    _MIN_HEIGHT = 460
    _MAX_WIDTH = 860

    option_selected = Signal(object, str)
    dismissed = Signal()

    def __init__(
        self,
        parent: QWidget,
        checkpoint: DecisionCheckpoint,
        summary_text: str,
        initial_notes: str = "",
        *,
        free_floating: bool = False,
    ) -> None:
        if free_floating:
            super().__init__(
                parent,
                Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint,
            )
        else:
            super().__init__(parent)
        self._checkpoint = checkpoint
        self._summary_text = summary_text
        self._initial_notes = initial_notes
        self._free_floating = free_floating
        self._content = build_checkpoint_dialog_content(checkpoint, summary_text)
        self._drag_handle: QWidget | None = None
        self._drag_widgets: list[QWidget] = []
        self._drag_start_global: QPoint | None = None
        self._drag_start_pos: QPoint | None = None
        self._user_positioned = False
        self._syncing_geometry = False

        # Install event filter on parent to track resize
        parent.installEventFilter(self)
        self._parent_filter_installed = True

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        if self._free_floating:
            self.setWindowModality(Qt.WindowModality.NonModal)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(self._MIN_WIDTH, self._MIN_HEIGHT)

        self._panel: Surface | None = None
        self._bubble_panel: _BubblePanel | None = None
        self._size_grip: QSizeGrip | None = None
        self._opacity_effect: QGraphicsOpacityEffect | None = None
        # Applying QGraphicsOpacityEffect to a frameless top-level Qt.Tool
        # makes macOS compose QScrollArea's native viewport separately.  The
        # viewport then shows the underlying chapter workbench instead of this
        # panel.  Embedded dialogs can still use the short fade safely.
        if not self._free_floating:
            self._opacity_effect = QGraphicsOpacityEffect(self)
            self._opacity_effect.setOpacity(0.0)
            self.setGraphicsEffect(self._opacity_effect)

        self._build_ui()
        self._update_parent_constraints()

    def _build_ui(self) -> None:
        """Build the floating panel with compact chrome and scrollable content."""
        # Panel (using "panel" tone, not "elevated", to avoid shadow+opacity conflict)
        self._panel = Surface("panel", self)
        self._panel.setObjectName("checkpointDialogPanel")
        self._panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._panel.setMinimumWidth(self._MIN_WIDTH)

        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(12, 8, 12, 8)
        panel_layout.setSpacing(6)

        # Header row
        header_widget = QWidget(self._panel)
        header_widget.setObjectName("checkpointDialogHeader")
        header_widget.setCursor(Qt.CursorShape.OpenHandCursor)
        header = QHBoxLayout(header_widget)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        badge = Badge(self._content["title"], tone=self._content["badge_tone"])
        header.addWidget(badge)
        header.addStretch(1)
        close_btn = QPushButton("×")
        close_btn.setObjectName("checkpointDialogClose")
        close_btn.setAccessibleName("关闭")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(lambda: self.dismiss_animated(emit_dismissed=True))
        header.addWidget(close_btn)
        panel_layout.addWidget(header_widget)
        self._drag_handle = header_widget
        self._drag_widgets = [header_widget, badge]
        for widget in self._drag_widgets:
            widget.installEventFilter(self)

        # Scroll area with bubble panel
        scroll = QScrollArea()
        scroll.setObjectName("checkpointDialogScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._bubble_panel = _BubblePanel(self._content, initial_notes=self._initial_notes)
        self._bubble_panel.option_selected.connect(self._on_bubble_option_selected)
        scroll.setWidget(self._bubble_panel)
        panel_layout.addWidget(scroll, 1)

        self._size_grip = QSizeGrip(self)
        self._size_grip.setFixedSize(18, 18)
        self._size_grip.raise_()

    def _on_bubble_option_selected(self, option: DecisionOption, notes: str) -> None:
        self.option_selected.emit(option, notes)

    # ── Public API ───────────────────────────────────────────────────────────

    @property
    def checkpoint_id(self) -> str:
        """Read-only checkpoint ID."""
        return self._checkpoint.checkpoint_id

    @property
    def summary_text(self) -> str:
        """Read-only current summary text."""
        return self._summary_text

    def is_for_checkpoint(self, checkpoint: DecisionCheckpoint) -> bool:
        """Check if this dialog is for the given checkpoint."""
        return self._checkpoint.checkpoint_id == checkpoint.checkpoint_id

    def update_checkpoint(self, checkpoint: DecisionCheckpoint, summary_text: str) -> None:
        """Hot-update the dialog with a new checkpoint and summary.

        Coord is responsible for passing a fully-built summary_text (with
        warnings already included).
        """
        self._checkpoint = checkpoint
        self._summary_text = summary_text
        self._content = build_checkpoint_dialog_content(checkpoint, summary_text)
        current_notes = self.current_notes()
        # Rebuild bubble panel content
        if self._bubble_panel is not None:
            self._bubble_panel.setParent(None)
            self._bubble_panel.deleteLater()
        self._bubble_panel = _BubblePanel(self._content, initial_notes=current_notes)
        self._bubble_panel.option_selected.connect(self._on_bubble_option_selected)
        # Replace in scroll area
        if self._panel is not None:
            scroll = self._panel.findChild(QScrollArea)
            if scroll is not None:
                scroll.setWidget(self._bubble_panel)
            # Update header badge
            badge = self._panel.findChild(Badge)
            if badge is not None:
                badge.setText(self._content["title"])
                badge.set_tone(self._content["badge_tone"])

    def current_notes(self) -> str:
        """Return current dialog notes text."""
        if self._bubble_panel is None:
            return ""
        return self._bubble_panel.notes_text()

    def show_animated(self) -> None:
        """Show the dialog with fade-in animation."""
        self._is_dismissing = False
        self._update_parent_constraints()
        self._position_panel()
        self.show()
        self.raise_()
        if self._free_floating:
            self.activateWindow()
            self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
            return
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        # Fade in
        assert self._opacity_effect is not None
        anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        anim.setDuration(200)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._fade_anim = anim

    def dismiss_animated(self, *, emit_dismissed: bool = True) -> None:
        """Fade out and hide the dialog.

        Args:
            emit_dismissed: If True, emit the ``dismissed`` signal after fade-out.
                Set to False when closing due to option selection, bind_studio
                cleanup, or program shutdown to avoid triggering fallback render.
        """
        if getattr(self, "_is_dismissing", False):
            if emit_dismissed:
                self._emit_dismissed_on_finish = True
            return
        self._is_dismissing = True
        self._emit_dismissed_on_finish = emit_dismissed
        if self._free_floating:
            self._finish_dismissal()
            return
        assert self._opacity_effect is not None
        anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        anim.setDuration(150)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.InCubic)

        anim.finished.connect(self._finish_dismissal)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._fade_anim = anim

    def _finish_dismissal(self) -> None:
        """Release a dismissed dialog after either the fade or direct close."""
        self.hide()
        self._cleanup_parent_event_filter()
        if getattr(self, "_emit_dismissed_on_finish", True):
            self.dismissed.emit()
        self.deleteLater()

    # ── Event handling ───────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.dismiss_animated(emit_dismissed=True)
            event.accept()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.raise_()
        super().mousePressEvent(event)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if self._panel is not None:
            self._panel.setGeometry(self.rect())
        if self._size_grip is not None:
            self._size_grip.move(
                max(0, self.width() - self._size_grip.width() - 5),
                max(0, self.height() - self._size_grip.height() - 5),
            )
            self._size_grip.raise_()
        if self.isVisible() and not self._syncing_geometry:
            self._user_positioned = True
            self.move(self._clamped_position(self.pos()))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Track parent resize and title-bar drag gestures."""
        parent = self.parent()
        if watched is parent and parent is not None and event.type() == QEvent.Type.Resize:
            self._update_parent_constraints()
            if self._user_positioned:
                self.move(self._clamped_position(self.pos()))
            else:
                self._position_panel()
            return super().eventFilter(watched, event)
        if isinstance(watched, QWidget) and watched in self._drag_widgets:
            if isinstance(event, QMouseEvent):
                if (
                    event.type() == QEvent.Type.MouseButtonPress
                    and event.button() == Qt.MouseButton.LeftButton
                ):
                    self._start_drag(event)
                    return True
                if event.type() == QEvent.Type.MouseMove and self._drag_start_global is not None:
                    self._move_drag(event)
                    return True
                if event.type() == QEvent.Type.MouseButtonRelease:
                    self._end_drag(event)
                    return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event: Any) -> None:
        self._cleanup_parent_event_filter()
        super().closeEvent(event)

    def _position_panel(self) -> None:
        """Place the dialog as a compact floating window near the right edge."""
        parent = self.parentWidget()
        if parent is None:
            return
        self._update_parent_constraints()
        margin = 18
        available_width = max(420, parent.width() - margin * 2)
        available_height = max(420, parent.height() - margin * 2)
        max_width = min(self._MAX_WIDTH, available_width)
        min_width = min(self._MIN_WIDTH, max_width)
        dialog_width = max(min_width, min(max_width, int(available_width * 0.9)))
        preferred_height = max(self._MIN_HEIGHT, self.sizeHint().height() + 18)
        height_cap = max(self._MIN_HEIGHT, int(available_height * 0.86))
        dialog_height = min(
            available_height,
            min(preferred_height, height_cap),
        )
        if self._free_floating:
            origin = parent.mapToGlobal(QPoint(0, 0))
            x = origin.x() + max(margin, parent.width() - dialog_width - margin)
            y = origin.y() + margin
        else:
            x = max(margin, parent.width() - dialog_width - margin)
            y = margin
        self._set_dialog_geometry(x, y, dialog_width, dialog_height)

    def _set_dialog_geometry(self, x: int, y: int, width: int, height: int) -> None:
        self._syncing_geometry = True
        try:
            self.setGeometry(x, y, width, height)
        finally:
            self._syncing_geometry = False
        if self._panel is not None:
            self._panel.setGeometry(self.rect())

    def _update_parent_constraints(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        max_width = max(self._MIN_WIDTH, parent.width() - self._MARGIN * 2)
        max_height = max(self._MIN_HEIGHT, parent.height() - self._MARGIN * 2)
        self.setMaximumSize(max_width, max_height)

    def _clamped_position(self, pos: QPoint) -> QPoint:
        if self._free_floating:
            return pos
        parent = self.parentWidget()
        if parent is None:
            return pos
        margin = self._MARGIN
        max_x = max(0, parent.width() - self.width() - margin)
        max_y = max(0, parent.height() - self.height() - margin)
        min_x = min(margin, max_x)
        min_y = min(margin, max_y)
        return QPoint(
            min(max(pos.x(), min_x), max_x),
            min(max(pos.y(), min_y), max_y),
        )

    def _start_drag(self, event: QMouseEvent) -> None:
        self.raise_()
        self._user_positioned = True
        self._drag_start_global = event.globalPosition().toPoint()
        self._drag_start_pos = self.pos()
        if self._drag_handle is not None:
            self._drag_handle.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept()

    def _move_drag(self, event: QMouseEvent) -> None:
        if self._drag_start_global is None or self._drag_start_pos is None:
            return
        delta = event.globalPosition().toPoint() - self._drag_start_global
        self.move(self._clamped_position(self._drag_start_pos + delta))
        event.accept()

    def _end_drag(self, event: QMouseEvent) -> None:
        self._drag_start_global = None
        self._drag_start_pos = None
        if self._drag_handle is not None:
            self._drag_handle.setCursor(Qt.CursorShape.OpenHandCursor)
        event.accept()

    def _cleanup_parent_event_filter(self) -> None:
        if not getattr(self, "_parent_filter_installed", False):
            return
        parent = self.parent()
        if parent is not None:
            try:
                parent.removeEventFilter(self)
            except RuntimeError:
                pass
        for widget in self._drag_widgets:
            try:
                widget.removeEventFilter(self)
            except RuntimeError:
                pass
        self._parent_filter_installed = False
