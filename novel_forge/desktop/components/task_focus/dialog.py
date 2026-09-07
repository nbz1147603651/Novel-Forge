"""Sub-module of novel_forge.desktop.components.task_focus.

Auto-generated in the M3.7 split. Contains dialog.py classes.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop import pets as _pets
from novel_forge.desktop.components.dialogs import MessageBoxAction, show_message_box
from novel_forge.desktop.components.primitives import (
    ActionButton,
)
from novel_forge.desktop.components.sizing import smart_dialog_size
from novel_forge.desktop.components.stream_detail import StreamDetailWidget

# Cross-references to sibling sub-modules (originally in a single file).
from novel_forge.desktop.components.task_focus.focus_panel import TaskFocusPanel
from novel_forge.desktop.components.task_focus.model_call import TaskModelCallPanel
from novel_forge.desktop.components.task_focus.presentation import (
    _job_kind_label,
    _stream_history_label,
)
from novel_forge.desktop.components.task_focus.switcher import TaskSwitcherBar
from novel_forge.desktop.state.snapshot_cache import JobSnapshotCache
from novel_forge.desktop.task_observation import (
    ObservedStreamState,
    ObservedTaskState,
    TaskFocusScope,
    TaskObservationStore,
)

# Module-level singleton cache (process-local).
_model_call_snapshot_cache = JobSnapshotCache(max_entries=64, ttl_s=60.0)
_load_pet_atlas = _pets.load_pet_atlas
_load_pet_pixmap = _pets.load_pet_pixmap
_DEFAULT_SHOW_MESSAGE_BOX = show_message_box


def _resolve_show_message_box() -> Any:
    """Return the patched message-box hook used by split-module compatibility tests."""

    if show_message_box is not _DEFAULT_SHOW_MESSAGE_BOX:
        return show_message_box
    from novel_forge.desktop.components import task_focus as task_focus_components

    return getattr(task_focus_components, "show_message_box", show_message_box)


class TaskFocusDialog(QDialog):
    """Floating task focus dialog opened by the companion."""

    decision_selected = Signal(str, str, str, str, str)
    task_delete_requested = Signal(str)

    def __init__(self, store: TaskObservationStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("taskFocusDialog")
        self.setWindowTitle("任务观察")
        self.setModal(False)
        self.resize(*smart_dialog_size(self, 900, 700))
        self.setMinimumSize(720, 560)
        self.setMaximumWidth(1180)
        self._store = store
        self._visible_job_id = ""
        self._selected_stream_id = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        self._switcher = TaskSwitcherBar(TaskFocusScope.GLOBAL, parent=self)
        self._switcher.bind_store(store)
        self._switcher.pin_requested.connect(self._handle_pin_requested)
        self._switcher.close_requested.connect(self._confirm_delete_task)
        layout.addWidget(self._switcher)

        self._tabs = QTabWidget(parent=self)
        self._tabs.setObjectName("taskFocusDialogTabs")
        self._tabs.setDocumentMode(True)
        layout.addWidget(self._tabs, 1)

        current_tab = QWidget(parent=self._tabs)
        current_layout = QVBoxLayout(current_tab)
        current_layout.setContentsMargins(0, 0, 0, 0)
        current_layout.setSpacing(0)
        self._content_scroll = QScrollArea()
        self._content_scroll.setObjectName("taskFocusDialogScroll")
        self._content_scroll.setWidgetResizable(True)
        self._content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._content_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._content_host = QWidget()
        content_layout = QVBoxLayout(self._content_host)
        content_layout.setContentsMargins(0, 0, 2, 0)
        content_layout.setSpacing(6)
        self._content_scroll.setWidget(self._content_host)
        current_layout.addWidget(self._content_scroll, 1)

        self._panel = TaskFocusPanel(
            TaskFocusScope.GLOBAL,
            title="任务观察",
            prominent=True,
            parent=self._content_host,
        )
        self._panel.decision_selected.connect(self.decision_selected)
        self._panel.pin_invalidated.connect(self._handle_pin_invalidated)
        self._panel.state_rendered.connect(self._handle_state_rendered)
        content_layout.addWidget(self._panel)
        content_layout.addStretch(1)
        self._tabs.addTab(current_tab, "当前节点")

        # Interleaved stream detail with per-segment collapsible reasoning.
        self._stream_tab = QWidget(parent=self._tabs)
        stream_layout = QVBoxLayout(self._stream_tab)
        stream_layout.setContentsMargins(0, 2, 0, 0)
        stream_layout.setSpacing(5)
        stream_toolbar = QHBoxLayout()
        stream_toolbar.setSpacing(6)
        stream_toolbar.addWidget(QLabel("流式详情"))
        self._stream_select = QComboBox()
        self._stream_select.setObjectName("taskFocusDialogStreamSelect")
        self._stream_select.setMinimumWidth(260)
        self._stream_select.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._stream_select.currentIndexChanged.connect(self._handle_stream_selected)
        stream_toolbar.addWidget(self._stream_select, 1)
        self._collapse_all_btn = ActionButton("全部折叠思考", variant="secondary")
        self._collapse_all_btn.clicked.connect(self._collapse_all_reasoning)
        stream_toolbar.addWidget(self._collapse_all_btn)
        self._expand_all_btn = ActionButton("全部展开思考", variant="secondary")
        self._expand_all_btn.clicked.connect(self._expand_all_reasoning)
        stream_toolbar.addWidget(self._expand_all_btn)
        stream_layout.addLayout(stream_toolbar)
        self._stream_detail = StreamDetailWidget(parent=self._stream_tab)
        stream_layout.addWidget(self._stream_detail, 1)
        self._tabs.addTab(self._stream_tab, "流式详情")

        self._model_calls = TaskModelCallPanel(parent=self._tabs)
        self._tabs.addTab(self._model_calls, "调用详情")

        self._panel.bind_store(store)
        state = self._panel.current_state
        self._visible_job_id = state.job_id if state is not None else ""
        initial_stream = self._sync_stream_selector(state)
        self._sync_stream_tab(initial_stream)
        self._stream_detail.set_stream(initial_stream)
        self._model_calls.set_job(state.job if state is not None else None)
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = ActionButton("关闭", variant="secondary")
        close_btn.clicked.connect(self.close)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

    def reopen(self, store: TaskObservationStore) -> None:
        """Rebind store after a prior close shut the panel down."""
        self._store = store
        self._switcher.bind_store(store)
        self._switcher.set_selected_job_id(self._panel.pinned_job_id)
        self._panel.bind_store(store)
        state = self._panel.current_state
        self._visible_job_id = state.job_id if state is not None else ""
        current_stream = self._sync_stream_selector(state)
        self._sync_stream_tab(current_stream)
        self._stream_detail.set_stream(current_stream)
        self._model_calls.set_job(state.job if state is not None else None)
        self._scroll_content_to_top()

    def show_stream_detail(self) -> None:
        index = self._tabs.indexOf(self._stream_tab)
        if index >= 0 and self._tabs.isTabVisible(index):
            self._tabs.setCurrentWidget(self._stream_tab)
            return
        # Some providers only expose call-level telemetry.  In that case the
        # same "展开详情" action should land on useful call evidence.
        self._tabs.setCurrentWidget(self._model_calls)

    def _handle_pin_requested(self, job_id: str) -> None:
        if job_id:
            self._panel.set_pinned_job(job_id)
        else:
            self._panel.clear_pinned_job()
        self._switcher.set_selected_job_id(self._panel.pinned_job_id)
        self._scroll_content_to_top()

    def _handle_pin_invalidated(self) -> None:
        self._switcher.set_selected_job_id("")
        self._scroll_content_to_top()

    def _confirm_delete_task(self, job_id: str) -> None:
        job_key = str(job_id or "").strip()
        if not job_key:
            return
        state = self._store.state_for_job_id(job_key, TaskFocusScope.GLOBAL)
        if state is None:
            return
        project = state.job.project_id or "自动项目"
        kind = _job_kind_label(state.job.kind)
        choice = _resolve_show_message_box()(
            self,
            "删除任务观察记录",
            f"确认删除「{project} · {kind} · {state.status_label}」？",
            informative_text="这只会移除任务观察里的历史记录，不会删除项目文件。运行中的任务请先取消或等待结束。",
            icon=QMessageBox.Icon.Warning,
            actions=(
                MessageBoxAction(
                    "delete",
                    "删除",
                    QMessageBox.ButtonRole.DestructiveRole,
                    "danger",
                    True,
                ),
                MessageBoxAction(
                    "cancel",
                    "取消",
                    QMessageBox.ButtonRole.RejectRole,
                    "secondary",
                ),
            ),
            escape_key="cancel",
        )
        if choice != "delete":
            return
        if self._panel.pinned_job_id == job_key:
            self._panel.clear_pinned_job()
            self._switcher.set_selected_job_id("")
        self.task_delete_requested.emit(job_key)

    def _handle_state_rendered(self, state: object) -> None:
        job = getattr(state, "job", None)
        job_id = str(getattr(state, "job_id", "") or "")
        if job_id and self._visible_job_id and job_id != self._visible_job_id:
            self._scroll_content_to_top()
        if job_id:
            self._visible_job_id = job_id
        self._model_calls.set_job(job)
        stream = self._sync_stream_selector(state if isinstance(state, ObservedTaskState) else None)
        self._sync_stream_tab(stream)
        self._stream_detail.set_stream(stream)

    def _streams_for_state(
        self, state: ObservedTaskState | None
    ) -> tuple[ObservedStreamState, ...]:
        if state is None:
            return ()
        streams = self._store.streams_for_job_id(state.job_id, source="llm_stream")
        if not streams and state.stream is not None and state.stream.source == "llm_stream":
            streams = (state.stream,)
        return streams

    def _sync_stream_selector(self, state: ObservedTaskState | None) -> ObservedStreamState | None:
        streams = self._streams_for_state(state)
        if state is None or not streams:
            old = self._stream_select.blockSignals(True)
            self._stream_select.clear()
            self._stream_select.addItem("暂无流式步骤", "")
            self._stream_select.blockSignals(old)
            self._stream_select.setEnabled(False)
            self._stream_select.setVisible(False)
            self._selected_stream_id = ""
            return None

        latest = state.stream if state.stream in streams else streams[-1]
        latest_id = latest.stream_id if latest is not None else streams[-1].stream_id
        stream_by_id = {stream.stream_id: stream for stream in streams}
        if self._selected_stream_id and self._selected_stream_id not in stream_by_id:
            self._selected_stream_id = ""
        selected = stream_by_id.get(self._selected_stream_id, latest)

        old = self._stream_select.blockSignals(True)
        self._stream_select.clear()
        self._stream_select.addItem("自动跟随最新步骤", "")
        for ordinal, stream in reversed(tuple(enumerate(streams, start=1))):
            self._stream_select.addItem(
                _stream_history_label(
                    stream,
                    ordinal=ordinal,
                    latest_stream_id=latest_id,
                ),
                stream.stream_id,
            )
        target_data = self._selected_stream_id
        for index in range(self._stream_select.count()):
            if str(self._stream_select.itemData(index) or "") == target_data:
                self._stream_select.setCurrentIndex(index)
                break
        self._stream_select.blockSignals(old)
        self._stream_select.setEnabled(True)
        self._stream_select.setVisible(True)
        return selected

    def _handle_stream_selected(self, index: int) -> None:
        self._selected_stream_id = str(self._stream_select.itemData(index) or "").strip()
        self._stream_detail.set_stream(self._sync_stream_selector(self._panel.current_state))

    def _sync_stream_tab(self, stream: ObservedStreamState | None) -> None:
        visible = stream is not None and stream.source == "llm_stream"
        index = self._tabs.indexOf(self._stream_tab)
        if index >= 0:
            self._tabs.setTabVisible(index, visible)
        if not visible and self._tabs.currentWidget() is self._stream_tab:
            self._tabs.setCurrentIndex(0)

    def _collapse_all_reasoning(self) -> None:
        self._stream_detail.collapse_all_reasoning()

    def _expand_all_reasoning(self) -> None:
        self._stream_detail.expand_all_reasoning()

    def _scroll_content_to_top(self) -> None:
        scroll = self._content_scroll

        def _scroll() -> None:
            try:
                scroll.verticalScrollBar().setValue(0)
            except RuntimeError:
                return

        QTimer.singleShot(0, _scroll)

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        self._switcher.shutdown()
        self._panel.shutdown()
        self._model_calls.set_job(None)
        super().closeEvent(event)
