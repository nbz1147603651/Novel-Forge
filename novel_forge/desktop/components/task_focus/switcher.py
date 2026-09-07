"""Sub-module of novel_forge.desktop.components.task_focus.

Auto-generated in the M3.7 split. Contains switcher.py classes.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QWidget,
)

from novel_forge.desktop import pets as _pets
from novel_forge.desktop.components.primitives import (
    Badge,
    Surface,
    clear_layout,
)
from novel_forge.desktop.components.task_focus.presentation import (
    _compact_label,
    _job_kind_label,
)
from novel_forge.desktop.jobs import DesktopJobState
from novel_forge.desktop.state.snapshot_cache import JobSnapshotCache
from novel_forge.desktop.task_observation import (
    ObservedAttentionGroup,
    ObservedTaskState,
    TaskFocusScope,
    TaskObservationStore,
)

# Module-level singleton cache (process-local).
_model_call_snapshot_cache = JobSnapshotCache(max_entries=64, ttl_s=60.0)
_load_pet_atlas = _pets.load_pet_atlas
_load_pet_pixmap = _pets.load_pet_pixmap


class TaskSwitcherChip(QPushButton):
    """Compact selectable chip for one observed task."""

    def __init__(self, text: str, *, job_id: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("taskSwitcherChip")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(28)
        self.setMinimumWidth(92)
        self.setProperty("job_id", job_id)
        self.setProperty("tone", "default")
        self.setProperty("terminal", False)

    @property
    def job_id(self) -> str:
        return str(self.property("job_id") or "")

    def set_auto(self) -> None:
        self.setText("✦ 自动跟随")
        self.setToolTip("自动显示当前最需要关注的任务")
        self._set_visual_properties(tone="default", terminal=False)

    def set_task_state(self, state: ObservedTaskState) -> None:
        project = state.job.project_id or "自动项目"
        kind = _job_kind_label(state.job.kind)
        decision = " ⚠" if state.has_active_decision else ""
        text = f"● {project} · {kind} · {state.status_label}{decision}"
        self.setText(text)
        self.setToolTip(f"{state.job.label or state.job.kind}\n当前节点：{state.current_node}")
        terminal = state.job.status in {DesktopJobState.SUCCEEDED, DesktopJobState.FAILED}
        self._set_visual_properties(tone=state.status_tone, terminal=terminal)

    def set_attention_group(self, group: ObservedAttentionGroup) -> None:
        state = group.primary
        decision = " ⚠" if group.decision_count else ""
        live = " · 流式" if group.live_stream_count and state.status_label != "输出中" else ""
        extra = ""
        if group.active_count > 1:
            extra = f" · {group.active_count} 项活跃"
        text = f"● {_compact_label(group.label, limit=18)} · {state.status_label}{live}{decision}{extra}"
        self.setText(text)
        tooltip_lines = [
            state.job.label or state.job.kind,
            f"当前节点：{state.current_node}",
            f"任务进度：{state.progress_percent}%",
        ]
        if len(group.states) > 1:
            tooltip_lines.append(
                f"已合并同项目观察：{len(group.states)} 条"
                + (f"，历史 {group.history_count} 条" if group.history_count else "")
            )
            for item in group.states[1:5]:
                tooltip_lines.append(
                    f"- {_job_kind_label(item.job.kind)} · {item.status_label} · {item.current_node}"
                )
        self.setToolTip("\n".join(tooltip_lines))
        terminal = state.job.status in {DesktopJobState.SUCCEEDED, DesktopJobState.FAILED}
        self._set_visual_properties(tone=state.status_tone, terminal=terminal)

    def _set_visual_properties(self, *, tone: str, terminal: bool) -> None:
        self.setProperty("tone", tone or "default")
        self.setProperty("terminal", bool(terminal))
        self.style().unpolish(self)
        self.style().polish(self)


class TaskSwitcherBar(Surface):
    """Dialog-only task selector for switching among observed jobs."""

    pin_requested = Signal(str)
    close_requested = Signal(str)

    def __init__(
        self,
        scope: TaskFocusScope | str = TaskFocusScope.GLOBAL,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("flat", parent)
        self.setObjectName("taskSwitcherBar")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.setMinimumHeight(50)
        self.setMaximumHeight(56)
        self._store: TaskObservationStore | None = None
        self._scope = TaskFocusScope(scope)
        self._project_id = ""
        self._chapter_number = 0
        self._selected_job_id = ""
        self._candidate_order: tuple[str, ...] = ()
        self._chips: dict[str, TaskSwitcherChip] = {}
        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)
        self._count_badge = Badge("任务 0 项", tone="default")
        root.addWidget(self._count_badge, 0, Qt.AlignmentFlag.AlignVCenter)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("taskSwitcherScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setFixedHeight(30)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._chip_host = QWidget()
        self._chip_layout = QHBoxLayout(self._chip_host)
        self._chip_layout.setContentsMargins(0, 0, 0, 0)
        self._chip_layout.setSpacing(6)
        self._scroll.setWidget(self._chip_host)
        root.addWidget(self._scroll, 1)
        self.setVisible(False)

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

    def set_scope(
        self,
        scope: TaskFocusScope | str,
        *,
        project_id: str = "",
        chapter_number: int = 0,
    ) -> None:
        self._scope = TaskFocusScope(scope)
        self._project_id = project_id
        self._chapter_number = int(chapter_number or 0)
        self._candidate_order = ()
        self.refresh()

    def set_selected_job_id(self, job_id: str) -> None:
        self._selected_job_id = str(job_id or "").strip()
        self._sync_checked_state()

    def refresh(self) -> None:
        store = self._store
        if store is None:
            self.setVisible(False)
            return
        groups = store.attention_groups_for_scope(
            self._scope,
            project_id=self._project_id,
            chapter_number=self._chapter_number,
        )
        self._count_badge.setText(f"项目 {len(groups)} 项")
        order = tuple(f"{group.key}:{group.job_id}:{len(group.states)}" for group in groups)
        if order != self._candidate_order:
            self._rebuild_chips(groups)
            self._candidate_order = order
        else:
            self._update_chips(groups)
        self.setVisible(bool(groups))
        self._sync_checked_state()

    def shutdown(self) -> None:
        if self._store is not None:
            try:
                self._store.changed.disconnect(self.refresh)
            except (RuntimeError, TypeError):
                pass
        self._store = None

    def _rebuild_chips(self, groups: list[ObservedAttentionGroup]) -> None:
        for button in self._button_group.buttons():
            self._button_group.removeButton(button)
        clear_layout(self._chip_layout)
        self._chips.clear()
        auto_chip = TaskSwitcherChip("✦ 自动跟随", parent=self._chip_host)
        auto_chip.set_auto()
        auto_chip.clicked.connect(lambda _checked=False: self._select_job(""))
        self._button_group.addButton(auto_chip)
        self._chip_layout.addWidget(auto_chip)
        self._chips[""] = auto_chip
        for group in groups:
            state = group.primary
            item = QWidget(self._chip_host)
            item.setObjectName("taskSwitcherItem")
            item_layout = QHBoxLayout(item)
            item_layout.setContentsMargins(0, 0, 0, 0)
            item_layout.setSpacing(2)
            chip = TaskSwitcherChip("", job_id=state.job_id, parent=item)
            chip.set_attention_group(group)
            chip.clicked.connect(
                lambda _checked=False, current=state.job_id: self._select_job(current)
            )
            close_button = QPushButton("X", parent=item)
            close_button.setObjectName("taskSwitcherCloseButton")
            close_button.setFixedSize(24, 28)
            close_button.setCursor(Qt.CursorShape.PointingHandCursor)
            close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            close_button.setProperty("job_id", state.job_id)
            close_button.setToolTip("删除当前主任务观察记录")
            close_button.clicked.connect(
                lambda _checked=False, current=state.job_id: self._request_close(current)
            )
            self._button_group.addButton(chip)
            item_layout.addWidget(chip)
            item_layout.addWidget(close_button)
            self._chip_layout.addWidget(item)
            self._chips[state.job_id] = chip
        self._chip_layout.addStretch(1)

    def _update_chips(self, groups: list[ObservedAttentionGroup]) -> None:
        for group in groups:
            chip = self._chips.get(group.job_id)
            if chip is not None:
                chip.set_attention_group(group)

    def _select_job(self, job_id: str) -> None:
        self._selected_job_id = str(job_id or "").strip()
        self._sync_checked_state()
        self.pin_requested.emit(self._selected_job_id)

    def _request_close(self, job_id: str) -> None:
        job_key = str(job_id or "").strip()
        if job_key:
            self.close_requested.emit(job_key)

    def _sync_checked_state(self) -> None:
        effective = self._selected_job_id if self._selected_job_id in self._chips else ""
        for job_id, chip in self._chips.items():
            old = chip.blockSignals(True)
            chip.setChecked(job_id == effective)
            chip.blockSignals(old)
