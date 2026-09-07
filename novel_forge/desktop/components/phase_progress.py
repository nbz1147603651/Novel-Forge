"""Compact task-flow progress bar for chapter generation jobs."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget

from novel_forge.desktop.progress import resolve_step_key, ui_steps_for_kind
from novel_forge.desktop.task_flow import task_flow_is_cancelled
from novel_forge.pipeline.progress import is_non_progress_step_event

_PHASE_LABELS = ("规划", "生成", "审查", "润色", "人性化", "收尾")
_CHAPTER_JOB_KINDS = {
    "run_chapter",
    "prepare_chapter",
    "polish_chapter",
    "resolve_chapter_checkpoint",
    "resolve_chapter_checkpoint_finalize",
}
class _ClickableSegmentLabel(QLabel):
    clicked = Signal()

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._click_enabled = False

    def set_click_enabled(self, enabled: bool) -> None:
        self._click_enabled = bool(enabled)
        if self._click_enabled:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip(f"{self.text()} · 点击查看产出")
        else:
            self.unsetCursor()
            self.setToolTip(self.text())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if (
            self._click_enabled
            and self.property("state") == "complete"
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


def _chapter_phase_index(kind: str, step: str) -> int:
    job_kind = str(kind or "").strip()
    raw_step = str(step or "").strip().lower()
    if job_kind not in _CHAPTER_JOB_KINDS:
        return -1
    if not raw_step:
        return 0
    if any(token in raw_step for token in ("humanize",)):
        return 4
    if any(token in raw_step for token in ("polish", "word_count", "restructure")):
        return 3
    if any(token in raw_step for token in ("persist", "canon", "memory", "finalize", "compact")):
        return 5
    if any(
        token in raw_step
        for token in (
            "quality",
            "check",
            "alignment",
            "continuity",
            "causal",
            "dedup",
            "pronoun",
            "reading_power",
            "evaluate",
            "extract",
            "guard",
            "repair",
        )
    ):
        return 2
    if any(token in raw_step for token in ("draft", "wave", "generate")):
        return 1
    if any(token in raw_step for token in ("plan", "state", "bridge", "context", "prepare")):
        return 0
    return 0


class PhaseProgressBar(QFrame):
    """Task-flow progress strip matching workflow job cards."""

    phase_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("phaseProgressBar")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 2, 0, 2)
        self._layout.setSpacing(4)
        self._segments: list[QLabel] = []
        self._step_keys: list[str] = []
        # Keep a per-job in-session high-water mark.  Reconstructing it from
        # every historical event can falsely highlight a later step after a
        # checkpoint resumes an earlier step; retaining only what this widget
        # has already rendered keeps progress monotonic without losing the
        # current semantic position on first render.
        self._high_water_by_job: dict[str, int] = {}
        self._configure_segments(
            [(label, label, False) for label in _PHASE_LABELS],
        )
        # Styles migrated to global QSS fragment (part_12_phase_progress).
        self.setVisible(False)

    def set_job(self, job: object) -> None:
        """Render progress from a full DesktopJobRecord-like object."""

        kind = str(getattr(job, "kind", "") or "")
        if kind not in _CHAPTER_JOB_KINDS:
            self.setVisible(False)
            return
        steps = ui_steps_for_kind(kind)
        if not steps:
            self.set_job_state(
                kind,
                str(getattr(job, "current_step", "") or ""),
                str(getattr(job, "status", "") or ""),
            )
            return
        self._configure_segments(
            [
                (
                    str(getattr(step, "label", "") or getattr(step, "key", "") or ""),
                    str(getattr(step, "key", "") or ""),
                    bool(getattr(step, "is_prefix", False)),
                )
                for step in steps
            ]
        )
        status = str(getattr(job, "status", "") or "").lower()
        terminal_success = status.endswith("succeeded")
        terminal_failure = status.endswith("failed")
        terminal_cancelled = task_flow_is_cancelled(job)
        events = list(getattr(job, "events", []) or [])
        current_step = self._stable_current_step(
            str(getattr(job, "current_step", "") or ""), events
        )
        current_index = self._index_for_step(kind, current_step)
        if current_index is None:
            # A just-started task may not have a current_step yet.  Use the
            # newest meaningful event as a first-render fallback rather than
            # scanning the full history for a stale future phase.
            for event in reversed(events):
                raw_step = str(getattr(event, "step", "") or "")
                if is_non_progress_step_event(raw_step):
                    continue
                current_index = self._index_for_step(kind, raw_step)
                if current_index is not None:
                    break

        job_id = str(getattr(job, "job_id", "") or "")
        previous_high_water = self._high_water_by_job.get(job_id)
        high_water = current_index
        if previous_high_water is not None:
            high_water = max(previous_high_water, high_water or 0)
        elif high_water is None:
            # Never leave an active task with an all-grey progress strip.
            high_water = 0
        if job_id and high_water is not None:
            self._high_water_by_job[job_id] = high_water
        if terminal_success:
            self._apply_states(len(self._segments) - 1, terminal_success=True)
        elif terminal_cancelled:
            self._apply_states(high_water, cancelled=True)
        elif terminal_failure:
            self._apply_states(high_water, failed=True)
        else:
            self._apply_states(high_water)
        self.setVisible(True)

    def set_job_state(self, kind: str, current_step: str, status: str = "") -> None:
        job_kind = str(kind or "").strip()
        if job_kind not in _CHAPTER_JOB_KINDS:
            self.setVisible(False)
            return
        steps = ui_steps_for_kind(job_kind)
        if steps:
            self._configure_segments(
                [
                    (
                        str(getattr(step, "label", "") or getattr(step, "key", "") or ""),
                        str(getattr(step, "key", "") or ""),
                        bool(getattr(step, "is_prefix", False)),
                    )
                    for step in steps
                ]
            )
            index = self._index_for_step(job_kind, current_step)
            if index is None:
                index = 0 if current_step else None
        else:
            self._configure_segments(
                [(label, label, False) for label in _PHASE_LABELS],
            )
            phase_index = _chapter_phase_index(job_kind, current_step)
            index = phase_index if phase_index >= 0 else None
        if index is None:
            index = 0
        terminal_success = str(status or "").lower().endswith("succeeded")
        if terminal_success:
            index = len(self._segments) - 1
        self._apply_states(index, terminal_success=terminal_success)
        self.setVisible(True)

    def _configure_segments(self, steps: list[tuple[str, str, bool]]) -> None:
        signature = [(label, key, is_prefix) for label, key, is_prefix in steps]
        current = [
            (segment.text(), key, bool(segment.property("is_prefix")))
            for segment, key in zip(self._segments, self._step_keys, strict=False)
        ]
        if current == signature:
            return
        while self._segments:
            segment = self._segments.pop()
            self._layout.removeWidget(segment)
            segment.deleteLater()
        self._step_keys = []
        for index, (label, key, is_prefix) in enumerate(steps):
            segment = _ClickableSegmentLabel(label)
            segment.setObjectName("phaseProgressSegment")
            segment.setAlignment(Qt.AlignmentFlag.AlignCenter)
            segment.setProperty("state", "pending")
            segment.setProperty("is_prefix", is_prefix)
            segment.setToolTip(label)
            segment.setMinimumWidth(0)
            segment.setMinimumHeight(24)
            segment.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            segment.clicked.connect(lambda current=index: self.phase_clicked.emit(current))
            self._layout.addWidget(segment)
            self._segments.append(segment)
            self._step_keys.append(key)

    def _apply_states(
        self,
        index: int | None,
        *,
        terminal_success: bool = False,
        failed: bool = False,
        cancelled: bool = False,
    ) -> None:
        for pos, segment in enumerate(self._segments):
            if terminal_success or (index is not None and pos < index):
                state = "complete"
            elif cancelled and pos == index:
                state = "cancelled"
            elif failed and pos == index:
                state = "current"
            elif pos == index:
                state = "current"
            else:
                state = "pending"
            segment.setProperty("state", state)
            if isinstance(segment, _ClickableSegmentLabel):
                segment.set_click_enabled(state == "complete")
            segment.style().unpolish(segment)
            segment.style().polish(segment)

    def step_key_at(self, index: int) -> str:
        if 0 <= index < len(self._step_keys):
            return self._step_keys[index]
        return ""

    def step_label_at(self, index: int) -> str:
        if 0 <= index < len(self._segments):
            return self._segments[index].text()
        return ""

    def _stable_current_step(self, current_step: str, events: list[Any]) -> str:
        if current_step and not is_non_progress_step_event(current_step):
            return current_step
        for event in reversed(events):
            step = str(getattr(event, "step", "") or "")
            if step and not is_non_progress_step_event(step):
                return step
        return ""

    def _index_for_step(self, kind: str, raw_step: str) -> int | None:
        step_key = self._visible_key_for_step(kind, raw_step)
        if not step_key:
            return None
        for index, segment_key in enumerate(self._step_keys):
            is_prefix = bool(self._segments[index].property("is_prefix"))
            if step_key == segment_key or (is_prefix and step_key.startswith(segment_key)):
                return index
        return None

    def _visible_key_for_step(self, kind: str, raw_step: str) -> str:
        step = str(raw_step or "").strip()
        if not step:
            return ""
        resolved = resolve_step_key(kind, step)
        if self._key_is_visible(resolved):
            return resolved
        fallback = self._fallback_visible_key(kind, step)
        if fallback and self._key_is_visible(fallback):
            return fallback
        return ""

    def _key_is_visible(self, key: str) -> bool:
        if not key:
            return False
        for index, step_key in enumerate(self._step_keys):
            is_prefix = bool(self._segments[index].property("is_prefix"))
            if key == step_key or (is_prefix and key.startswith(step_key)):
                return True
        return False

    def _fallback_visible_key(self, kind: str, raw_step: str) -> str:
        step = str(raw_step or "").lower()
        if kind == "resolve_chapter_checkpoint_finalize":
            if any(
                token in step
                for token in (
                    "state_adjudication",
                    "extract",
                    "canon",
                    "creative_report",
                    "relationship_delta",
                    "plot_thread",
                    "expression_observation",
                    "knowledge_delta",
                )
            ):
                return "polish_reextract_canon"
            if any(token in step for token in ("persist", "archive", "chapter_saved")):
                return "persist"
            if any(token in step for token in ("evaluate", "quality", "eval")):
                return "evaluate"
            if any(token in step for token in ("volume", "audit")):
                return "volume_audit"
            if any(token in step for token in ("memory", "motif", "summary")):
                return "memory_updated"
            if "repair" in step:
                return "post_guard_repair"
            if "guard" in step or "checkpoint" in step:
                return "guard_checkpoint"
        if kind == "resolve_chapter_checkpoint":
            if any(token in step for token in ("plan", "checkpoint")):
                return "plan_checkpoint"
            if any(token in step for token in ("draft", "wave", "generate")):
                return "draft"
            if any(token in step for token in ("continuity",)):
                return "continuity_repair"
            if any(token in step for token in ("alignment_repair",)):
                return "alignment_repair"
            if any(
                token in step
                for token in ("dedup", "pronoun", "causal", "reading_power", "humanize")
            ):
                return "post_alignment"
            if any(token in step for token in ("quality", "check", "alignment", "guard")):
                return "pre_alignment"
        return ""
