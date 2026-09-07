"""Workflow UI components: pipeline step indicator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NamedTuple

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QResizeEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class _ElidedStepLabel(QLabel):
    """Single-line centered step label that keeps the full text in a tooltip."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setWordWrap(False)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setText(text)

    def setText(self, text: str) -> None:
        self._full_text = str(text or "")
        self.setToolTip(self._full_text)
        self.updateGeometry()
        self._apply_elide()

    def sizeHint(self) -> QSize:
        metrics = QFontMetrics(self.font())
        width = min(metrics.horizontalAdvance(self._full_text), self.maximumWidth())
        return QSize(max(0, width), metrics.height())

    def minimumSizeHint(self) -> QSize:
        return QSize(0, QFontMetrics(self.font()).height())

    def resizeEvent(self, event: QResizeEvent) -> None:
        self._apply_elide()
        super().resizeEvent(event)

    def _apply_elide(self) -> None:
        width = self.contentsRect().width()
        if width <= 0:
            QLabel.setText(self, self._full_text)
            return
        metrics = QFontMetrics(self.font())
        elided = metrics.elidedText(self._full_text, Qt.TextElideMode.ElideRight, width)
        QLabel.setText(self, elided)


class PipelineStep(NamedTuple):
    """A single pipeline milestone used by StepIndicatorRow."""

    key: str  # internal step key (or prefix to match, e.g. "edit_")
    label: str  # user-facing Chinese display name
    is_prefix: bool = False  # if True, any step starting with `key` counts


StepDotState = Literal["pending", "active", "done", "failed", "skipped"]


@dataclass(frozen=True)
class StepIndicatorState:
    """Fully resolved render state for a step indicator row."""

    dot_states: tuple[StepDotState, ...]


class StepIndicatorRow(QWidget):
    """Horizontal step-by-step progress indicator for pipeline jobs.

    • Shows N labelled milestones connected by thin horizontal lines.
    • Each dot transitions: ``pending`` → ``active`` → ``done`` / ``failed``.
    • Supports parallel steps: when two milestones run concurrently via
      ``asyncio.gather``, both dots light up simultaneously.
    • Call :meth:`update_step` whenever a new step key is received from
      the job callbacks.
    • Call :meth:`mark_done` when the job succeeds, and :meth:`mark_failed`
      when it errors.
    • Emits :attr:`step_clicked` ``(int)`` when a completed (``done``) dot is
      clicked — the int is the step index.

    Usage::

        steps = [
            PipelineStep("spec", "规格确认"),
            PipelineStep("beats", "节拍生成"),
            PipelineStep("draft", "初稿完成"),
            PipelineStep("edit_", "编辑修订", is_prefix=True),
            PipelineStep("evaluate", "质量评估"),
        ]
        parallels = [("beats", "draft")]   # these two run concurrently
        row = StepIndicatorRow(steps, parallel_pairs=parallels)
        row.update_step(current_step_key)
        row.mark_done()
    """

    step_clicked = Signal(int)

    def __init__(
        self,
        steps: list[PipelineStep],
        *,
        parallel_pairs: list[tuple[str, str]] | None = None,
        clickable_indices: set[int] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._steps = steps
        self._running_indices: set[int] = set()
        self._final_state: str = "running"
        self._visited: set[int] = set()
        self._clickable_indices: set[int] | None = (
            {index for index in clickable_indices if 0 <= index < len(steps)}
            if clickable_indices is not None
            else None
        )
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        key_to_idx: dict[str, int] = {}
        for i, step in enumerate(steps):
            key_to_idx[step.key] = i
        self._parallel_groups: list[frozenset[int]] = []
        if parallel_pairs:
            grouped_indices: list[set[int]] = []
            for key_a, key_b in parallel_pairs:
                idx_a = key_to_idx.get(key_a)
                idx_b = key_to_idx.get(key_b)
                if idx_a is not None and idx_b is not None:
                    pair = {idx_a, idx_b}
                    merged: set[int] = set(pair)
                    remaining: list[set[int]] = []
                    for group in grouped_indices:
                        if group & pair:
                            merged.update(group)
                        else:
                            remaining.append(group)
                    remaining.append(merged)
                    grouped_indices = remaining
            self._parallel_groups = [frozenset(group) for group in grouped_indices]

        self._dot_labels: list[QLabel] = []
        self._step_labels: list[QLabel] = []
        step_count = max(1, len(steps))
        compact = step_count >= 9
        dot_size = 12 if compact else 14
        label_width = 96 if compact else 88
        column_stretch = 2 if compact else 0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(0)

        for i, step in enumerate(steps):
            dot = QLabel()
            dot.setObjectName("stepDot")
            dot.setProperty("state", "pending")
            dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            dot.setFixedSize(dot_size, dot_size)
            dot.installEventFilter(self)
            self._dot_labels.append(dot)

            col = QVBoxLayout()
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(1)
            col.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            col.addWidget(dot, 0, Qt.AlignmentFlag.AlignHCenter)

            text = _ElidedStepLabel(step.label)
            text.setObjectName("stepLabel")
            text.setAlignment(Qt.AlignmentFlag.AlignCenter)
            text.setMinimumWidth(0)
            text.setMaximumWidth(label_width)
            text.setMinimumHeight(QFontMetrics(text.font()).height())
            self._step_labels.append(text)
            if compact:
                col.addWidget(text)
            else:
                col.addWidget(text, 0, Qt.AlignmentFlag.AlignHCenter)

            col_widget = QWidget()
            col_widget.setLayout(col)
            col_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            if compact:
                col_widget.setMaximumWidth(label_width)
            layout.addWidget(col_widget, column_stretch)

            if i < len(steps) - 1:
                line = QLabel()
                line.setObjectName("stepConnector")
                line.setFixedHeight(2)
                line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                # Wrap line in a widget to vertically center it with the dot
                line_wrapper = QWidget()
                line_wrapper_layout = QVBoxLayout(line_wrapper)
                line_wrapper_layout.setContentsMargins(0, 0, 0, 0)
                line_wrapper_layout.setSpacing(0)
                line_wrapper_layout.addStretch(1)
                line_wrapper_layout.addWidget(line)
                line_wrapper_layout.addStretch(1)
                layout.addWidget(line_wrapper, 1)

    # ── Public API ────────────────────────────────────────────────────────

    def update_step(self, step_key: str) -> None:
        """Advance the indicator to the step matching *step_key*.

        Parallel-aware: if *step_key* belongs to a parallel group, all
        siblings in that group are also marked as running.  Earlier running
        steps that are NOT in the same parallel group are promoted to
        "done" (removed from the running set) since a later sequential
        step implies they have finished.  If a retry/resume moves back to an
        earlier sequential step, later visited dots are cleared so stale
        events from a prior pass do not appear completed.
        """
        matched = self._match_index(step_key)
        if matched < 0:
            return
        self._visited.add(matched)

        # Find the parallel group this step belongs to (if any)
        parallel_siblings: frozenset[int] = frozenset()
        for group in self._parallel_groups:
            if matched in group:
                parallel_siblings = group
                break

        active_span_end = max(parallel_siblings) if parallel_siblings else matched
        self._visited = {i for i in self._visited if i <= active_span_end}
        self._running_indices = {i for i in self._running_indices if i <= active_span_end}

        if parallel_siblings:
            # Parallel step: add all siblings to running and mark them as
            # visited so they become done once the next sequential step begins.
            self._visited.update(parallel_siblings)
            self._running_indices = {i for i in self._running_indices if i in parallel_siblings}
            self._running_indices.update(parallel_siblings)
        else:
            self._running_indices = {matched}
        self._repaint_dots()

    def mark_done(self) -> None:
        """Mark all steps as completed (job succeeded)."""
        self._final_state = "done"
        self._running_indices.clear()
        self._repaint_dots(force_all_done=True)

    def mark_completed_at(self, step_key: str = "") -> None:
        """Mark the flow as closed at *step_key* without completing later steps."""
        matched = self._match_index(step_key) if step_key else -1
        if matched >= 0:
            self._visited = {i for i in self._visited if i <= matched}
            self._visited.add(matched)
        self._final_state = "done"
        self._running_indices.clear()
        self._repaint_dots()

    def mark_reached_at(self, step_key: str = "") -> None:
        """Mark all milestones through *step_key* as reached while keeping it active."""
        matched = self._match_index(step_key) if step_key else -1
        if matched < 0:
            return

        parallel_siblings: frozenset[int] = frozenset()
        for group in self._parallel_groups:
            if matched in group:
                parallel_siblings = group
                break

        active_span_end = max(parallel_siblings) if parallel_siblings else matched
        self._visited = {i for i in self._visited if i <= active_span_end}
        self._visited.update(range(active_span_end + 1))
        self._running_indices = set(parallel_siblings) if parallel_siblings else {matched}
        self._final_state = "running"
        self._repaint_dots()

    def mark_failed(self, step_key: str = "") -> None:
        """Mark the current active step as failed.

        Clears all visited steps after the failed step so that later steps
        do not incorrectly appear as completed (green).
        """
        self._final_state = "failed"
        if step_key:
            matched = self._match_index(step_key)
            if matched >= 0:
                self._running_indices = {matched}
                # Remove all visited steps after (and including) the failed step
                # so they render as pending, not done.
                self._visited = {i for i in self._visited if i < matched}
        self._repaint_dots()

    def apply_state(self, state: StepIndicatorState) -> None:
        """Render already-resolved dot states without replaying raw events."""
        self._running_indices.clear()
        self._visited.clear()
        self._final_state = "running"
        states = state.dot_states
        for index, dot in enumerate(self._dot_labels):
            raw_state = states[index] if index < len(states) else "pending"
            dot_state: StepDotState = (
                raw_state
                if raw_state in {"pending", "active", "done", "failed", "skipped"}
                else "pending"
            )
            self._apply_dot_state(index, dot, dot_state)

    def reset(self) -> None:
        """Reset all steps to pending state."""
        self._running_indices.clear()
        self._final_state = "running"
        self._visited.clear()
        self._repaint_dots()

    def step_key(self, index: int) -> str:
        if 0 <= index < len(self._steps):
            return self._steps[index].key
        return ""

    def step_label(self, index: int) -> str:
        if 0 <= index < len(self._steps):
            return self._steps[index].label
        return ""

    def set_clickable_indices(self, clickable_indices: set[int] | None) -> None:
        """Restrict which completed dots can open artifacts.

        ``None`` preserves the legacy behavior: every completed dot is
        clickable.  Passing a set makes completed dots without concrete
        artifacts render as reached but inert.
        """
        self._clickable_indices = (
            {index for index in clickable_indices if 0 <= index < len(self._steps)}
            if clickable_indices is not None
            else None
        )
        for index, dot in enumerate(self._dot_labels):
            state = str(dot.property("state") or "pending")
            self._apply_dot_state(index, dot, state)  # type: ignore[arg-type]

    # ── Internal helpers ──────────────────────────────────────────────────

    def _match_index(self, step_key: str) -> int:
        for i, step in enumerate(self._steps):
            if step.is_prefix:
                if step_key.startswith(step.key):
                    return i
            else:
                if step_key == step.key:
                    return i
        return -1

    def eventFilter(self, obj: QWidget, event: QEvent) -> bool:  # type: ignore[override]
        if event.type() == QEvent.Type.MouseButtonRelease:
            if obj in self._dot_labels:
                idx = self._dot_labels.index(obj)
                if obj.property("state") == "done" and self._dot_is_clickable(idx):
                    self.step_clicked.emit(idx)
                    return True
        return super().eventFilter(obj, event)

    def _repaint_dots(self, *, force_all_done: bool = False) -> None:
        for i, dot in enumerate(self._dot_labels):
            state: StepDotState
            if force_all_done and i in self._visited:
                state = "done"
            elif force_all_done and i not in self._visited:
                state = "skipped"
            elif i in self._running_indices:
                if self._final_state == "failed":
                    state = "failed"
                elif self._final_state == "done":
                    state = "done"
                else:
                    state = "active"
            elif i in self._visited:
                state = "done"
            else:
                state = "pending"
            self._apply_dot_state(i, dot, state)

    def _dot_is_clickable(self, index: int) -> bool:
        return self._clickable_indices is None or index in self._clickable_indices

    def _apply_dot_state(self, index: int, dot: QLabel, state: StepDotState) -> None:
        dot.setProperty("state", state)
        if 0 <= index < len(self._step_labels):
            step_label = self._step_labels[index]
            step_label.setProperty("state", state)
            step_label.style().unpolish(step_label)
            step_label.style().polish(step_label)
        if state == "done":
            if self._dot_is_clickable(index):
                dot.setCursor(Qt.CursorShape.PointingHandCursor)
                dot.setToolTip("点击查看该步骤产出文件")
            else:
                dot.setCursor(Qt.CursorShape.ArrowCursor)
                dot.setToolTip("该步骤暂无已落盘产物")
        elif state == "skipped":
            dot.setCursor(Qt.CursorShape.ArrowCursor)
            dot.setToolTip("该步骤已跳过，未生成可查看产物")
        elif state == "failed":
            dot.setCursor(Qt.CursorShape.ArrowCursor)
            dot.setToolTip("该步骤执行失败，未生成可查看产物")
        else:
            dot.setCursor(Qt.CursorShape.ArrowCursor)
            dot.setToolTip("")
        dot.style().unpolish(dot)
        dot.style().polish(dot)
