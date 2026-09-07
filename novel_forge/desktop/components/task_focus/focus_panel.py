"""Sub-module of novel_forge.desktop.components.task_focus.

Auto-generated in the M3.7 split. Contains focus_panel.py classes.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop import pets as _pets
from novel_forge.desktop.components.phase_progress import PhaseProgressBar
from novel_forge.desktop.components.primitives import (
    ActionButton,
    Badge,
    SectionHeading,
    Surface,
    clear_layout,
)
from novel_forge.desktop.components.stream_behavior import StreamFollowController
from novel_forge.desktop.components.stream_rendering import (
    detect_stream_render_kind,
    stream_document_html,
    stream_html_from_segments,
    stream_html_from_text,
)
from novel_forge.desktop.components.task_focus.presentation import (
    _constrain_label_width,
    _elapsed_text,
    _fmt_cost,
    _stream_notice,
)
from novel_forge.desktop.jobs import DesktopJobState
from novel_forge.desktop.state.snapshot_cache import JobSnapshotCache
from novel_forge.desktop.task_observation import (
    ObservedDecisionState,
    ObservedStreamState,
    ObservedTaskState,
    TaskFocusScope,
    TaskObservationStore,
)

# Module-level singleton cache (process-local).
_model_call_snapshot_cache = JobSnapshotCache(max_entries=64, ttl_s=60.0)
_load_pet_atlas = _pets.load_pet_atlas
_load_pet_pixmap = _pets.load_pet_pixmap


class TaskFocusPanel(Surface):
    """Shared current-task panel used by dashboard/workflow/chapter pages."""

    decision_selected = Signal(str, str, str, str, str)  # + exact approval_version
    artifact_requested = Signal(
        str, str, str, str, int
    )  # project_id, kind, step_key, label, chapter
    pin_invalidated = Signal()
    state_rendered = Signal(object)
    expand_requested = Signal()

    def __init__(
        self,
        scope: TaskFocusScope | str = TaskFocusScope.GLOBAL,
        *,
        title: str = "当前关注",
        prominent: bool = False,
        compact: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("panel", parent)
        self.setObjectName("taskFocusPanel")
        self.setMinimumWidth(0)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred if prominent else QSizePolicy.Policy.Maximum,
        )
        self._store: TaskObservationStore | None = None
        self._scope = TaskFocusScope(scope)
        self._prominent = prominent
        self._compact = compact
        self._project_id = ""
        self._chapter_number = 0
        self._waiting_dots = 0
        self._waiting_message = "等待当前节点输出"
        self._last_job_id = ""
        self._pinned_job_id = ""
        self._pending_state: ObservedTaskState | None = None
        self._current_state: ObservedTaskState | None = None
        self._last_rendered_sig: tuple[object, ...] | None = None
        self._rendered_stream_id = ""
        self._build_ui(title)
        self._waiting_timer = QTimer(self)
        self._waiting_timer.setInterval(450)
        self._waiting_timer.timeout.connect(self._tick_waiting)
        self._render_throttle_timer = QTimer(self)
        self._render_throttle_timer.setInterval(80)
        self._render_throttle_timer.setSingleShot(True)
        self._render_throttle_timer.timeout.connect(self._flush_render)
        self.setVisible(False)

    def _build_ui(self, title: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(
            16 if self._prominent else 18,
            14 if self._prominent else 16,
            16 if self._prominent else 18,
            14 if self._prominent else 16,
        )
        root.setSpacing(8 if self._prominent else 10)

        header = QHBoxLayout()
        header.setSpacing(10)
        self._heading = SectionHeading(title, "当前节点输出、诊断与需要确认的动作。")
        header.addWidget(self._heading, 1)
        self._reason_badge = Badge("待命", tone="default")
        header.addWidget(self._reason_badge, 0, Qt.AlignmentFlag.AlignTop)
        self._status_badge = Badge("待命", tone="default")
        header.addWidget(self._status_badge, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self._title_label = QLabel("")
        self._title_label.setObjectName("cardTitle")
        _constrain_label_width(self._title_label)
        self._title_label.setVisible(not self._compact)
        root.addWidget(self._title_label)

        self._meta_host = QWidget()
        meta_row = QHBoxLayout(self._meta_host)
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(10)
        self._meta_label = QLabel("")
        self._meta_label.setObjectName("cardMeta")
        _constrain_label_width(self._meta_label)
        meta_row.addWidget(self._meta_label, 1)
        self._node_label = QLabel("")
        self._node_label.setObjectName("progressDetail")
        _constrain_label_width(self._node_label)
        meta_row.addWidget(self._node_label, 1)
        self._meta_host.setVisible(not self._compact)
        root.addWidget(self._meta_host)

        self._phase_progress = PhaseProgressBar()
        self._phase_progress.phase_clicked.connect(self._on_phase_clicked)
        self._phase_progress.setVisible(False)
        root.addWidget(self._phase_progress)

        self._decision_frame = Surface("inset")
        self._decision_frame.setObjectName("taskFocusDecision")
        decision_layout = QVBoxLayout(self._decision_frame)
        decision_layout.setContentsMargins(14, 12, 14, 12)
        decision_layout.setSpacing(8)
        self._decision_title = QLabel("")
        self._decision_title.setObjectName("cardTitle")
        _constrain_label_width(self._decision_title)
        decision_layout.addWidget(self._decision_title)
        self._decision_message = QLabel("")
        self._decision_message.setObjectName("cardBody")
        _constrain_label_width(self._decision_message)
        decision_layout.addWidget(self._decision_message)
        self._decision_hint = QLabel("")
        self._decision_hint.setObjectName("cardHint")
        _constrain_label_width(self._decision_hint)
        decision_layout.addWidget(self._decision_hint)
        self._decision_custom_text = QTextEdit()
        self._decision_custom_text.setObjectName("taskFocusDecisionCustomText")
        self._decision_custom_text.setPlaceholderText(
            "选择“应用编辑 JSON”前，在这里粘贴完整 JSON。"
        )
        self._decision_custom_text.setMinimumHeight(120)
        self._decision_custom_text.setMaximumHeight(260)
        self._decision_custom_text.setVisible(False)
        decision_layout.addWidget(self._decision_custom_text)
        self._decision_buttons_host = QWidget()
        self._decision_buttons = QHBoxLayout(self._decision_buttons_host)
        self._decision_buttons.setContentsMargins(0, 0, 0, 0)
        self._decision_buttons.setSpacing(8)
        decision_layout.addWidget(self._decision_buttons_host)
        root.addWidget(self._decision_frame)

        stream_header = QHBoxLayout()
        stream_header.setSpacing(8)
        self._stream_title = QLabel("当前节点正文")
        self._stream_title.setObjectName("cardTitle")
        self._stream_title.setProperty("compact", True)
        stream_header.addWidget(self._stream_title)
        self._render_mode_badge = Badge("文本", tone="default")
        self._render_mode_badge.setToolTip("当前输出的解析与渲染模式")
        stream_header.addWidget(self._render_mode_badge)
        self._stream_notice = QLabel("")
        self._stream_notice.setObjectName("cardHint")
        _constrain_label_width(self._stream_notice)
        stream_header.addWidget(self._stream_notice, 1)
        root.addLayout(stream_header)

        self._waiting_label = QLabel("")
        self._waiting_label.setObjectName("fieldHint")
        self._waiting_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._waiting_label)

        self._stream_browser = QTextBrowser()
        self._stream_browser.setObjectName("taskFocusStream")
        self._stream_browser.setOpenExternalLinks(False)
        self._stream_browser.setReadOnly(True)
        self._stream_browser.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self._stream_browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._stream_browser.setMinimumWidth(0)
        self._stream_browser.setMinimumHeight(220 if self._prominent else 150)
        self._stream_browser.setMaximumHeight(420 if self._prominent else 260)
        self._stream_browser.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Expanding if self._prominent else QSizePolicy.Policy.Preferred,
        )
        self._stream_browser.setFrameShape(QFrame.Shape.NoFrame)
        self._stream_browser.document().setDocumentMargin(0)
        from novel_forge.desktop.pages.document_renderer.incremental import (
            IncrementalDocumentRenderer,
        )

        self._stream_renderer = IncrementalDocumentRenderer(self._stream_browser)
        self._stream_follow = StreamFollowController(self._stream_browser)

        # Compact mode: mini ticker + "思考中" badge + expand button,
        # replacing the full stream browser for the embedded focus panels.
        self._compact_preview = QWidget()
        self._compact_preview.setObjectName("compactStreamPreview")
        self._compact_preview.setMinimumWidth(0)
        self._compact_preview.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        compact_layout = QHBoxLayout(self._compact_preview)
        compact_layout.setContentsMargins(0, 4, 0, 4)
        compact_layout.setSpacing(8)
        self._thinking_badge = Badge("思考中", tone="warning")
        self._thinking_badge.setVisible(False)
        compact_layout.addWidget(self._thinking_badge)
        self._ticker_label = QLabel("")
        self._ticker_label.setObjectName("taskFocusTicker")
        _constrain_label_width(self._ticker_label)
        compact_layout.addWidget(self._ticker_label, 1)
        self._expand_btn = ActionButton("展开详情", variant="secondary")
        self._expand_btn.clicked.connect(self.expand_requested.emit)
        compact_layout.addWidget(self._expand_btn)
        self._compact_preview.setVisible(self._compact)
        root.addWidget(self._compact_preview)

        self._stream_browser.setVisible(not self._compact)
        root.addWidget(self._stream_browser, 1 if self._prominent else 0)

        self._diagnostics_toggle = ActionButton("诊断摘要", variant="secondary")
        self._diagnostics_toggle.setProperty("compact", True)
        self._diagnostics_toggle.setCheckable(True)
        self._diagnostics_toggle.clicked.connect(self._toggle_diagnostics)
        self._diagnostics_toggle.setVisible(not self._compact)
        root.addWidget(self._diagnostics_toggle, 0, Qt.AlignmentFlag.AlignLeft)

        self._diagnostics_text = QLabel("")
        self._diagnostics_text.setObjectName("cardHint")
        _constrain_label_width(self._diagnostics_text)
        self._diagnostics_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._diagnostics_text.setVisible(False)
        root.addWidget(self._diagnostics_text)

        self._events_toggle = ActionButton("关键事件", variant="secondary")
        self._events_toggle.setProperty("compact", True)
        self._events_toggle.setCheckable(True)
        self._events_toggle.clicked.connect(self._toggle_events)
        self._events_toggle.setVisible(not self._compact)
        root.addWidget(self._events_toggle, 0, Qt.AlignmentFlag.AlignLeft)

        self._events_text = QLabel("")
        self._events_text.setObjectName("cardMeta")
        _constrain_label_width(self._events_text)
        self._events_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._events_text.setVisible(False)
        root.addWidget(self._events_text)

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
        self.refresh()

    @property
    def pinned_job_id(self) -> str:
        return self._pinned_job_id

    @property
    def current_state(self) -> ObservedTaskState | None:
        return self._current_state

    def set_pinned_job(self, job_id: str) -> None:
        pinned = str(job_id or "").strip()
        if self._pinned_job_id == pinned:
            self.refresh()
            return
        self._pinned_job_id = pinned
        self._pending_state = None
        self._last_rendered_sig = None
        self._render_throttle_timer.stop()
        self.refresh()

    def clear_pinned_job(self) -> None:
        if not self._pinned_job_id:
            self.refresh()
            return
        self._pinned_job_id = ""
        self._pending_state = None
        self._last_rendered_sig = None
        self._render_throttle_timer.stop()
        self.refresh()

    def refresh(self) -> None:
        store = self._store
        if store is None:
            self.setVisible(False)
            self._waiting_timer.stop()
            self._render_throttle_timer.stop()
            self._pending_state = None
            self._current_state = None
            self._last_rendered_sig = None
            return
        invalidated_pin = False
        if self._pinned_job_id:
            state = store.state_for_job_id(
                self._pinned_job_id,
                self._scope,
                project_id=self._project_id,
                chapter_number=self._chapter_number,
            )
            if state is None:
                self._pinned_job_id = ""
                invalidated_pin = True
                state = store.focus_for_scope(
                    self._scope,
                    project_id=self._project_id,
                    chapter_number=self._chapter_number,
                )
        else:
            state = store.focus_for_scope(
                self._scope,
                project_id=self._project_id,
                chapter_number=self._chapter_number,
            )
        if state is None:
            self.setVisible(False)
            self._waiting_timer.stop()
            self._render_throttle_timer.stop()
            self._pending_state = None
            self._current_state = None
            self._last_rendered_sig = None
            if invalidated_pin:
                self.pin_invalidated.emit()
            return
        state_sig = self._state_signature(state)
        if self._compact and state.stream is None and not state.has_active_decision:
            # The normal task card already owns progress, history, and stop
            # controls.  A compact panel is reserved for actual output or a
            # decision, so a waiting task is not rendered as a second task
            # flow on the same page.
            self.setVisible(False)
            self._waiting_timer.stop()
            self._render_throttle_timer.stop()
            self._pending_state = None
            self._current_state = state
            self._last_rendered_sig = state_sig
            return
        self.setVisible(True)
        if state_sig == self._last_rendered_sig:
            if invalidated_pin:
                self.pin_invalidated.emit()
            return
        # Task switch renders immediately so the panel reflects the new focus;
        # same-task updates (e.g. streaming deltas) are coalesced via a timer.
        if state.job_id != self._last_job_id:
            self._pending_state = None
            self._render_throttle_timer.stop()
            self._render_state(state)
            self._last_rendered_sig = state_sig
            if invalidated_pin:
                self.pin_invalidated.emit()
            return
        self._pending_state = state
        if not self._render_throttle_timer.isActive():
            self._render_throttle_timer.start()
        if invalidated_pin:
            self.pin_invalidated.emit()

    def _flush_render(self) -> None:
        state = self._pending_state
        self._pending_state = None
        if state is not None:
            state_sig = self._state_signature(state)
            if state_sig == self._last_rendered_sig:
                return
            self._render_state(state)
            self._last_rendered_sig = state_sig

    def _state_signature(self, state: ObservedTaskState) -> tuple[object, ...]:
        stream = state.stream
        decision = state.decision
        return (
            state.job_id,
            stream.stream_id if stream is not None else "",
            stream.source if stream is not None else "",
            stream.status if stream is not None else "",
            stream.text_length if stream is not None else 0,
            stream.reasoning_length if stream is not None else 0,
            len(stream.segments) if stream is not None else 0,
            stream.segments[-1].kind if stream is not None and stream.segments else "",
            len(stream.segments[-1].text) if stream is not None and stream.segments else 0,
            stream.truncated if stream is not None else False,
            stream.updated_at if stream is not None else "",
            stream.error if stream is not None else "",
            state.status_label,
            decision.status if decision is not None else "",
            decision.decision_id if decision is not None else "",
            state.current_node,
            state.usage.prompt_tokens,
            state.usage.completion_tokens,
            state.usage.total_tokens,
            state.usage.cost_usd,
            state.progress_percent,
            state.diagnostics,
            state.events,
        )

    def _render_state(self, state: ObservedTaskState) -> None:
        self._current_state = state
        if state.job_id != self._last_job_id:
            self._last_job_id = state.job_id
            self._diagnostics_toggle.setChecked(False)
            self._events_toggle.setChecked(False)
            self._diagnostics_text.setVisible(False)
            self._events_text.setVisible(False)

        self._reason_badge.setText(state.focus_reason)
        self._reason_badge.set_tone("warning" if state.has_active_decision else "default")
        self._status_badge.setText(state.status_label)
        self._status_badge.set_tone(state.status_tone)
        self._title_label.setText(state.job.label or state.job.kind)
        self._node_label.setText(f"当前节点：{state.current_node}")

        elapsed = _elapsed_text(state.job.created_at)
        tokens = f"{state.usage.total_tokens:,} tokens" if state.usage.total_tokens else ""
        cost = _fmt_cost(state.usage.cost_usd)
        meta_parts = [state.job.project_id or "自动项目"]
        if elapsed:
            meta_parts.append(f"已运行 {elapsed}")
        meta_parts.append(f"进度 {state.progress_percent}%")
        if tokens:
            meta_parts.append(tokens)
        if cost:
            meta_parts.append(cost)
        self._meta_label.setText("  ·  ".join(meta_parts))
        if self._compact:
            self._phase_progress.setVisible(False)
        else:
            self._phase_progress.set_job(state.job)

        self._render_decision(state)
        self._render_stream(state.stream, state.job.status)
        self._render_collapsible(state)
        self.state_rendered.emit(state)

    def _render_decision(self, state: ObservedTaskState) -> None:
        decision = state.decision
        pending = decision is not None and decision.status == "pending"
        self._decision_frame.setVisible(pending)
        if not pending or decision is None:
            self._decision_custom_text.setVisible(False)
            self._clear_decision_buttons()
            return
        self._decision_title.setText(decision.title or "需要确认")
        self._decision_message.setText(decision.message or "当前任务请求确认后继续。")
        hint_parts = []
        if decision.risk:
            hint_parts.append(f"风险：{decision.risk}")
        if decision.timeout_seconds:
            hint_parts.append(f"超时：{decision.timeout_seconds} 秒")
        if decision.default_option:
            hint_parts.append(f"默认：{decision.default_option}")
        if decision.cost_hint:
            hint_parts.append(decision.cost_hint)
        self._decision_hint.setText("  ·  ".join(hint_parts))

        self._clear_decision_buttons()
        options = decision.options or (
            {
                "id": decision.default_option or "continue_without_escalation",
                "label": decision.default_option or "保守继续",
                "description": "",
            },
        )
        accepts_custom_text = any(option.get("id") == "apply_edits" for option in options)
        self._decision_custom_text.setVisible(accepts_custom_text)
        if self._decision_custom_text.property("decision_id") != decision.decision_id:
            self._decision_custom_text.setProperty("decision_id", decision.decision_id)
            self._decision_custom_text.clear()
        for option in options:
            label = option.get("label") or option.get("id") or "选择"
            choice = option.get("id") or label
            variant = "primary" if choice == decision.default_option else "secondary"
            button = ActionButton(label, variant=variant)
            button.setProperty("compact", True)
            description = option.get("description", "")
            if description:
                button.setToolTip(description)
            button.clicked.connect(
                lambda _checked=False, current=choice: self._submit_decision(decision, current)
            )
            self._decision_buttons.addWidget(button)
        self._decision_buttons.addStretch(1)

    def _clear_decision_buttons(self) -> None:
        while self._decision_buttons.count():
            item = self._decision_buttons.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
                continue
            if child_layout is not None:
                clear_layout(child_layout)

    def _render_stream(
        self, stream: ObservedStreamState | None, job_status: DesktopJobState
    ) -> None:
        self._stream_title.setText(
            "正在生成结果"
            if stream is not None and stream.source == "model_call"
            else "当前节点正文"
        )
        self._stream_notice.setText(_stream_notice(stream))
        render_kind = detect_stream_render_kind(stream.text if stream is not None else "")
        self._render_mode_badge.setText(render_kind.label)
        self._render_mode_badge.set_tone(
            "warning" if render_kind.value == "json_partial" else "default"
        )
        # Compact mode: update mini ticker + thinking badge, skip full browser.
        if self._compact:
            self._render_compact(stream, job_status)
            return
        stream_id = stream.stream_id if stream is not None else ""
        if stream_id != self._rendered_stream_id:
            self._rendered_stream_id = stream_id
            self._stream_follow.reset_to_latest()
        if stream is not None and stream.segments:
            body_html = stream_html_from_segments(
                stream.segments,
                truncated=stream.truncated,
            )
        else:
            text = stream.text if stream is not None else ""
            body_html = stream_html_from_text(text)
        # ``IncrementalDocumentRenderer`` replaces the browser document.  Use
        # the shared anchor protocol so reviewing earlier output never turns
        # into a proportional jump as the streamed document grows.
        stream_anchor = self._stream_follow.capture_content_anchor()
        self._stream_renderer.update_content(stream_document_html(body_html))
        self._stream_follow.restore_content_anchor(stream_anchor)
        has_text = stream is not None and bool(stream.text.strip())
        waiting = not has_text and job_status in {
            DesktopJobState.QUEUED,
            DesktopJobState.RUNNING,
            DesktopJobState.PAUSED,
        }
        if waiting and stream is not None and stream.source == "model_call":
            self._waiting_message = "正在生成，完成后自动展示"
        elif waiting and stream is not None and stream.source == "llm_stream":
            self._waiting_message = "已连接流式输出，等待首个片段"
        else:
            self._waiting_message = "等待当前节点输出"
        self._waiting_label.setVisible(waiting)
        if waiting and not self._waiting_timer.isActive():
            self._waiting_timer.start()
        elif not waiting:
            self._waiting_timer.stop()
            self._waiting_label.setText("")

    def _render_compact(
        self, stream: ObservedStreamState | None, job_status: DesktopJobState
    ) -> None:
        """Render the compact mini-ticker view (single-line preview)."""
        text = stream.text if stream is not None else ""
        # Show the last ~100 characters as a preview ticker.  Only the tail
        # window is normalized: a full ``join(text.split())`` over a 50 KB
        # accumulator is O(n) on every 80 ms render tick, while the ticker
        # only ever displays the newest output anyway.
        _TICKER_TAIL_WINDOW = 2000
        ticker_source = " ".join(text[-_TICKER_TAIL_WINDOW:].split()).strip()
        ticker_text = ticker_source
        if len(ticker_text) > 100:
            ticker_text = "…" + ticker_text[-100:]
        if not ticker_text and job_status in {
            DesktopJobState.QUEUED,
            DesktopJobState.RUNNING,
            DesktopJobState.PAUSED,
        }:
            ticker_text = (
                "正在生成，完成后自动展示…"
                if stream is not None and stream.source == "model_call"
                else "等待输出…"
            )
        self._ticker_label.setText(ticker_text)
        self._ticker_label.setToolTip(ticker_source[-1000:] if ticker_source else ticker_text)
        # Show "思考中" badge when reasoning is present or streaming.
        has_reasoning = stream is not None and bool(stream.reasoning_text.strip())
        is_streaming = stream is not None and stream.status == "streaming"
        self._thinking_badge.setVisible(has_reasoning and is_streaming)
        # Waiting animation for compact mode.
        waiting = not text.strip() and job_status in {
            DesktopJobState.QUEUED,
            DesktopJobState.RUNNING,
            DesktopJobState.PAUSED,
        }
        if waiting and not self._waiting_timer.isActive():
            self._waiting_timer.start()
        elif not waiting:
            self._waiting_timer.stop()

    def _render_collapsible(self, state: ObservedTaskState) -> None:
        diagnostics = "\n".join(f"- {item}" for item in state.diagnostics) or "暂无结构化诊断。"
        events = "\n".join(f"- {item}" for item in state.events) or "暂无关键事件。"
        self._diagnostics_text.setText(diagnostics)
        self._events_text.setText(events)
        self._diagnostics_toggle.setText(
            "收起诊断摘要" if self._diagnostics_toggle.isChecked() else "诊断摘要"
        )
        self._events_toggle.setText(
            "收起关键事件" if self._events_toggle.isChecked() else "关键事件"
        )

    def _submit_decision(self, decision: ObservedDecisionState, choice: str) -> None:
        custom_text = (
            self._decision_custom_text.toPlainText().strip()
            if choice == "apply_edits" and self._decision_custom_text.isVisible()
            else ""
        )
        self.decision_selected.emit(
            decision.job_id, decision.decision_id, choice, custom_text, decision.approval_version
        )

    def _on_phase_clicked(self, index: int) -> None:
        state = self._current_state
        job = state.job if state is not None else None
        if job is None:
            return
        project_id = str(getattr(job, "project_id", "") or self._project_id or "").strip()
        job_kind = str(getattr(job, "kind", "") or "").strip()
        step_key = self._phase_progress.step_key_at(index)
        if not project_id or not job_kind or not step_key:
            return
        step_label = self._phase_progress.step_label_at(index) or step_key
        chapter_number = self._extract_chapter_number_from_job(job) or self._chapter_number
        self.artifact_requested.emit(
            project_id,
            job_kind,
            step_key,
            step_label,
            int(chapter_number or 0),
        )

    @staticmethod
    def _extract_chapter_number_from_job(job: object) -> int:
        result = getattr(job, "result", None)
        if isinstance(result, dict):
            for key in ("chapter_number", "chapter", "current_chapter"):
                value = result.get(key)
                if isinstance(value, int) and value > 0:
                    return value
                if isinstance(value, str) and value.strip().isdigit():
                    return int(value.strip())
        label = str(getattr(job, "label", "") or "")
        match = re.search(r"第\s*(\d+)\s*章", label)
        if match:
            return int(match.group(1))
        return 0

    def _toggle_diagnostics(self) -> None:
        self._diagnostics_text.setVisible(self._diagnostics_toggle.isChecked())
        self._diagnostics_toggle.setText(
            "收起诊断摘要" if self._diagnostics_toggle.isChecked() else "诊断摘要"
        )

    def _toggle_events(self) -> None:
        self._events_text.setVisible(self._events_toggle.isChecked())
        self._events_toggle.setText(
            "收起关键事件" if self._events_toggle.isChecked() else "关键事件"
        )

    def _tick_waiting(self) -> None:
        self._waiting_dots = (self._waiting_dots + 1) % 4
        self._waiting_label.setText(self._waiting_message + "." * self._waiting_dots)

    def shutdown(self) -> None:
        self._waiting_timer.stop()
        self._render_throttle_timer.stop()
        self._pending_state = None
        if self._store is not None:
            try:
                self._store.changed.disconnect(self.refresh)
            except (RuntimeError, TypeError):
                pass
        self._store = None
