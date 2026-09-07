"""Sub-module of novel_forge.desktop.components.task_focus.

Auto-generated in the M3.7 split. Contains stream_window.py classes.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop import pets as _pets
from novel_forge.desktop.components.primitives import (
    ActionButton,
    Surface,
)
from novel_forge.desktop.components.sizing import smart_dialog_size
from novel_forge.desktop.components.stream_detail import StreamDetailWidget

# Cross-references to sibling sub-modules (originally in a single file).
from novel_forge.desktop.components.task_focus.model_call import TaskModelCallPanel
from novel_forge.desktop.components.task_focus.presentation import _stream_history_label
from novel_forge.desktop.state.snapshot_cache import JobSnapshotCache
from novel_forge.desktop.task_observation import (
    ObservedStreamState,
    ObservedTaskState,
    TaskObservationStore,
)

# Module-level singleton cache (process-local).
_model_call_snapshot_cache = JobSnapshotCache(max_entries=64, ttl_s=60.0)
_load_pet_atlas = _pets.load_pet_atlas
_load_pet_pixmap = _pets.load_pet_pixmap


class FloatingStreamWindow(Surface):
    """Frameless, always-on-top floating window with detailed stream view.

    Level 3 of the information density hierarchy: the most detailed view,
    opened by clicking the pet companion.  Contains a tabbed interface with
    the interleaved :class:`StreamDetailWidget` (with per-segment folding)
    and the model call details panel.  Draggable via the header bar.
    """

    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("elevated", parent)
        self.setObjectName("floatingStreamWindow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self._store: TaskObservationStore | None = None
        self._current_state: ObservedTaskState | None = None
        self._selected_stream_id = ""
        self._drag_press_global_pos: QPoint | None = None
        self._drag_press_widget_pos: QPoint | None = None
        self._dragging = False
        self._build_ui()
        self.resize(*smart_dialog_size(self, 560, 540))
        self.setMinimumSize(420, 340)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        # Draggable header bar.
        header = QFrame()
        header.setObjectName("floatingStreamHeader")
        header.setFixedHeight(36)
        header.setCursor(Qt.CursorShape.OpenHandCursor)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 4, 20, 4)
        header_layout.setSpacing(8)
        self._header_title = QLabel("流式详情")
        self._header_title.setObjectName("cardTitle")
        self._header_title.setProperty("compact", True)
        header_layout.addWidget(self._header_title)
        header_layout.addStretch()
        self._header_status = QLabel("")
        self._header_status.setObjectName("cardMeta")
        header_layout.addWidget(self._header_status)
        close_btn = ActionButton("×", variant="secondary")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self._close)
        header_layout.addWidget(close_btn)
        header.mousePressEvent = self._header_mouse_press  # type: ignore[method-assign]
        header.mouseMoveEvent = self._header_mouse_move  # type: ignore[method-assign]
        header.mouseReleaseEvent = self._header_mouse_release  # type: ignore[method-assign]
        layout.addWidget(header)

        # Tabbed content.
        self._tabs = QTabWidget()
        self._tabs.setObjectName("floatingStreamTabs")
        self._tabs.setDocumentMode(True)
        self._tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # Tab 1: Stream detail with per-segment folding.
        self._stream_tab = QWidget()
        stream_layout = QVBoxLayout(self._stream_tab)
        stream_layout.setContentsMargins(0, 4, 0, 0)
        stream_layout.setSpacing(4)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        history_label = QLabel("查看步骤")
        history_label.setObjectName("cardMeta")
        toolbar.addWidget(history_label)
        self._stream_select = QComboBox()
        self._stream_select.setObjectName("floatingStreamSelect")
        self._stream_select.setMinimumWidth(220)
        self._stream_select.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._stream_select.currentIndexChanged.connect(self._handle_stream_selected)
        toolbar.addWidget(self._stream_select, 1)
        collapse_btn = ActionButton("全部折叠思考", variant="secondary")
        collapse_btn.clicked.connect(self._collapse_all)
        toolbar.addWidget(collapse_btn)
        expand_btn = ActionButton("全部展开思考", variant="secondary")
        expand_btn.clicked.connect(self._expand_all)
        toolbar.addWidget(expand_btn)
        toolbar.addStretch()
        stream_layout.addLayout(toolbar)
        self._stream_detail = StreamDetailWidget(parent=self._stream_tab)
        stream_layout.addWidget(self._stream_detail, 1)
        self._tabs.addTab(self._stream_tab, "流式详情")
        # Tab 2: Model call details.
        self._model_calls = TaskModelCallPanel(parent=self._tabs)
        self._tabs.addTab(self._model_calls, "调用详情")
        # Tab 3: Event timeline.
        self._event_timeline = QTextBrowser()
        self._event_timeline.setOpenExternalLinks(False)
        self._event_timeline.setReadOnly(True)
        self._event_timeline.setFrameShape(QFrame.Shape.NoFrame)
        self._tabs.addTab(self._event_timeline, "原始事件")
        layout.addWidget(self._tabs, 1)
        self._sync_stream_tab(None)

    def bind_store(self, store: TaskObservationStore) -> None:
        self._store = store

    def update_state(self, state: ObservedTaskState | None) -> None:
        """Refresh the window with the latest observed task state."""
        self._current_state = state
        if state is None:
            self._header_status.setText("")
            self._sync_stream_tab(None)
            self._sync_stream_selector(None, ())
            self._stream_detail.set_stream(None)
            self._model_calls.set_job(None)
            self._event_timeline.clear()
            return
        self._header_status.setText(state.status_label)
        streams = self._stream_history_for_state(state)
        selected_stream = self._sync_stream_selector(state, streams)
        self._sync_stream_tab(selected_stream)
        self._stream_detail.set_stream(selected_stream)
        self._model_calls.set_job(state.job)
        events_text = "\n".join(f"· {e}" for e in state.events) or "暂无关键事件。"
        diagnostics_text = "\n".join(f"· {d}" for d in state.diagnostics) or "暂无诊断。"
        self._event_timeline.setPlainText(
            f"── 关键事件 ──\n{events_text}\n\n── 诊断 ──\n{diagnostics_text}"
        )

    def _stream_history_for_state(
        self, state: ObservedTaskState | None
    ) -> tuple[ObservedStreamState, ...]:
        if state is None:
            return ()
        streams: tuple[ObservedStreamState, ...] = ()
        if self._store is not None:
            streams = self._store.streams_for_job_id(state.job_id, source="llm_stream")
        if not streams and state.stream is not None and state.stream.source == "llm_stream":
            streams = (state.stream,)
        return streams

    def _sync_stream_selector(
        self,
        state: ObservedTaskState | None,
        streams: tuple[ObservedStreamState, ...],
    ) -> ObservedStreamState | None:
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
        self.update_state(self._current_state)

    def _sync_stream_tab(self, stream: ObservedStreamState | None) -> None:
        visible = stream is not None and stream.source == "llm_stream"
        index = self._tabs.indexOf(self._stream_tab)
        if index >= 0:
            self._tabs.setTabVisible(index, visible)
        if not visible and self._tabs.currentWidget() is self._stream_tab:
            model_index = self._tabs.indexOf(self._model_calls)
            if model_index >= 0:
                self._tabs.setCurrentIndex(model_index)

    def position_near_anchor(self, anchor: QWidget | None = None) -> None:
        """Place the floating window near the pet, using global screen coordinates."""

        self.ensurePolished()

        reference = anchor if anchor is not None else self.parentWidget()
        screen = None
        if reference is not None:
            reference_bottom_right = reference.mapToGlobal(
                QPoint(reference.width(), reference.height())
            )
            reference_top_left = reference.mapToGlobal(QPoint(0, 0))
            reference_center = reference.mapToGlobal(reference.rect().center())
        else:
            screen = QApplication.primaryScreen()
            available = screen.availableGeometry() if screen is not None else None
            if available is None:
                self.move(80, 80)
                return
            reference_center = available.center()
            reference_bottom_right = reference_center
            reference_top_left = reference_center

        screen = screen or QApplication.screenAt(reference_center) or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        margin = 12
        target_width = min(
            max(self.minimumWidth(), 560),
            max(self.minimumWidth(), available.width() - margin * 2),
        )
        target_height = min(
            max(self.minimumHeight(), 540),
            max(self.minimumHeight(), available.height() - margin * 2),
        )
        if self.size() != QSize(target_width, target_height):
            self.resize(target_width, target_height)

        if reference is not None:
            x = reference_bottom_right.x() - self.width()
            y = reference_top_left.y() - self.height() - 10
            if y < available.y() + margin:
                y = reference_bottom_right.y() + 10
        else:
            x = available.center().x() - self.width() // 2
            y = available.center().y() - self.height() // 2

        min_x = available.x() + margin
        min_y = available.y() + margin
        max_x = available.x() + available.width() - self.width() - margin
        max_y = available.y() + available.height() - self.height() - margin
        self.move(
            max(min_x, min(x, max_x)),
            max(min_y, min(y, max_y)),
        )

    def _collapse_all(self) -> None:
        self._stream_detail.collapse_all_reasoning()

    def _expand_all(self) -> None:
        self._stream_detail.expand_all_reasoning()

    def _close(self) -> None:
        self.hide()
        self.closed.emit()

    # ── Drag handlers ──────────────────────────────────────────────────

    def _header_mouse_press(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_press_global_pos = event.globalPosition().toPoint()
            self._drag_press_widget_pos = self.pos()
            self._dragging = False
            event.accept()
            return

    def _header_mouse_move(self, event: QMouseEvent) -> None:
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
            new_pos = self._drag_press_widget_pos + delta
            self.move(new_pos)
            event.accept()
            return

    def _header_mouse_release(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_press_global_pos = None
            self._drag_press_widget_pos = None
            self._dragging = False
            event.accept()
            return

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        self.closed.emit()
        super().closeEvent(event)
