"""Sub-module of novel_forge.desktop.components.task_focus.

Auto-generated in the M3.7 split. Contains model_call.py classes.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop import pets as _pets
from novel_forge.desktop.components.primitives import (
    ActionButton,
    Badge,
    SectionHeading,
    Surface,
    clear_layout,
)
from novel_forge.desktop.components.task_focus.presentation import (
    _MODEL_BAR_LABEL_WIDTH,
    _compact_label,
    _constrain_label_width,
    _fmt_cost,
    _fmt_count,
    _fmt_latency,
)
from novel_forge.desktop.model_call_observation import (
    ModelCallActivity,
    ModelCallRecord,
    ModelCallSnapshot,
    latest_model_call_activity,
    load_model_call_snapshot,
)
from novel_forge.desktop.state.snapshot_cache import JobSnapshotCache

# Module-level singleton cache (process-local).
_model_call_snapshot_cache = JobSnapshotCache(max_entries=64, ttl_s=60.0)
_load_pet_atlas = _pets.load_pet_atlas
_load_pet_pixmap = _pets.load_pet_pixmap


def _call_status_label(record: ModelCallRecord) -> str:
    labels = {
        "success": "成功",
        "timeout": "超时",
        "incomplete": "未完成",
        "error": "错误",
        "empty_response": "模型返回空响应",
        "length_truncated": "输出达到长度上限",
        "unknown": "状态未知",
    }
    label = labels.get(record.status, record.status or "状态未知")
    if record.will_retry:
        label += "（已自动重试）"
    return label


def _activity_text(activity: ModelCallActivity | None) -> str:
    if activity is None or activity.status not in {"running", "retrying", "error"}:
        return ""
    target = activity.task or "模型调用"
    route = " / ".join(part for part in (activity.provider, activity.model) if part)
    target = f"{target} · {route}" if route else target
    if activity.status == "running":
        suffix = (
            f" · 输出上限 {_fmt_count(activity.max_tokens)} Token"
            if activity.max_tokens
            else ""
        )
        return f"正在等待模型响应：{target}{suffix}"

    attempt = f" · 第 {activity.attempt} 次返回" if activity.attempt else ""
    if activity.status == "error":
        if activity.event == "api_call_empty_response":
            return f"模型调用失败：{target}{attempt}为空响应，自动重试已用尽。"
        if activity.event == "api_call_length_truncated":
            return f"模型调用失败：{target}{attempt}达到长度上限，未获得完整结果。"
        return f"模型调用异常：{target}"
    if activity.event == "api_call_empty_response":
        return f"自动重试中：{target}{attempt}为空响应，正在请求下一次结果。"
    if activity.event == "api_call_length_truncated":
        limits = ""
        if activity.max_tokens and activity.next_max_tokens:
            limits = (
                f"，输出上限已从 {_fmt_count(activity.max_tokens)} "
                f"提升至 {_fmt_count(activity.next_max_tokens)} Token"
            )
        return f"自动重试中：{target}{attempt}达到长度上限{limits}。"
    return f"模型调用异常：{target}"


class TaskModelCallPanel(Surface):
    """Dialog-only model-call summary with lightweight charts."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("panel", parent)
        self.setObjectName("taskModelCallPanel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._snapshot: ModelCallSnapshot | None = None
        self._records: tuple[ModelCallRecord, ...] = ()
        self._expanded = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(9)

        header = QHBoxLayout()
        header.setSpacing(10)
        self._heading = SectionHeading("调用明细", "模型调用、Token、耗时与输入输出预览。")
        header.addWidget(self._heading, 1)
        self._count_badge = Badge("无记录", tone="default")
        header.addWidget(self._count_badge, 0, Qt.AlignmentFlag.AlignTop)
        self._token_badge = Badge("Token 0", tone="default")
        header.addWidget(self._token_badge, 0, Qt.AlignmentFlag.AlignTop)
        self._toggle = ActionButton("展开", variant="secondary")
        self._toggle.setCheckable(True)
        self._toggle.setProperty("compact", True)
        self._toggle.clicked.connect(self._toggle_expanded)
        header.addWidget(self._toggle, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self._hint = QLabel("选择任务后显示模型调用概况。")
        self._hint.setObjectName("cardHint")
        _constrain_label_width(self._hint)
        self._hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self._hint)
        self._activity_hint = QLabel()
        self._activity_hint.setObjectName("cardHint")
        self._activity_hint.setWordWrap(True)
        self._activity_hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._activity_hint.setVisible(False)
        root.addWidget(self._activity_hint)

        self._content_scroll = QScrollArea()
        self._content_scroll.setObjectName("taskModelCallContentScroll")
        self._content_scroll.setWidgetResizable(True)
        self._content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._content_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._content_scroll.setMinimumHeight(260)
        self._content = QWidget()
        self._content.setObjectName("taskModelCallContent")
        content = QVBoxLayout(self._content)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(10)

        self._token_chart = QVBoxLayout()
        self._token_chart.setSpacing(6)
        content.addLayout(self._token_chart)
        self._provider_chart = QVBoxLayout()
        self._provider_chart.setSpacing(6)
        content.addLayout(self._provider_chart)
        self._task_chart = QVBoxLayout()
        self._task_chart.setSpacing(6)
        content.addLayout(self._task_chart)

        select_row = QHBoxLayout()
        select_row.setSpacing(8)
        select_label = QLabel("单次调用")
        select_label.setObjectName("cardTitle")
        select_label.setProperty("compact", True)
        select_row.addWidget(select_label)
        self._call_select = QComboBox()
        self._call_select.setObjectName("taskModelCallSelect")
        self._call_select.currentIndexChanged.connect(self._render_selected_call)
        select_row.addWidget(self._call_select, 1)
        content.addLayout(select_row)

        previews = QHBoxLayout()
        previews.setSpacing(8)
        self._request_preview = QTextBrowser()
        self._request_preview.setObjectName("taskModelCallPreview")
        self._request_preview.setReadOnly(True)
        self._request_preview.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self._request_preview.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._request_preview.setMinimumWidth(0)
        self._request_preview.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Expanding,
        )
        self._request_preview.setMinimumHeight(170)
        self._request_preview.setMaximumHeight(260)
        self._request_preview.setAccessibleName("模型调用请求与参数")
        self._response_preview = QTextBrowser()
        self._response_preview.setObjectName("taskModelCallPreview")
        self._response_preview.setReadOnly(True)
        self._response_preview.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self._response_preview.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._response_preview.setMinimumWidth(0)
        self._response_preview.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Expanding,
        )
        self._response_preview.setMinimumHeight(170)
        self._response_preview.setMaximumHeight(260)
        self._response_preview.setAccessibleName("模型调用响应或异常")
        request_column = QVBoxLayout()
        request_column.setSpacing(5)
        request_label = QLabel("请求与参数")
        request_label.setObjectName("cardTitle")
        request_label.setProperty("compact", True)
        request_column.addWidget(request_label)
        request_column.addWidget(self._request_preview, 1)
        response_column = QVBoxLayout()
        response_column.setSpacing(5)
        response_label = QLabel("响应或异常")
        response_label.setObjectName("cardTitle")
        response_label.setProperty("compact", True)
        response_column.addWidget(response_label)
        response_column.addWidget(self._response_preview, 1)
        previews.addLayout(request_column, 1)
        previews.addLayout(response_column, 1)
        content.addLayout(previews, 1)

        self._content_scroll.setWidget(self._content)
        root.addWidget(self._content_scroll, 1)
        self._content_scroll.setVisible(False)
        self._sync_height_limit()

    def set_job(self, job: object | None) -> None:
        if job is None:
            self._snapshot = None
            self._records = ()
            self._render_empty("选择任务后显示模型调用概况。")
            return
        if not hasattr(job, "result"):
            self._render_empty("当前对象没有任务结果。")
            return
        run_id = str(getattr(job, "job_id", None) or "unknown")
        _result = getattr(job, "result", None)
        if not isinstance(_result, dict):
            _result = {}
        _run_log_dir = _result.get("run_log_dir") or _result.get("run_dir") or ""
        _run_log_dir_path = Path(str(_run_log_dir)) if _run_log_dir else Path(".")
        model_calls_dir = _run_log_dir_path / "model_calls"

        def _load_snapshot():
            return load_model_call_snapshot(job)  # type: ignore[arg-type]

        snapshot = _model_call_snapshot_cache.get_or_load(run_id, model_calls_dir, _load_snapshot)
        snapshot = dataclasses.replace(snapshot, activity=latest_model_call_activity(job))
        self._snapshot = snapshot
        self._records = snapshot.records
        self._render_snapshot(snapshot)

    def _toggle_expanded(self) -> None:
        self._expanded = self._toggle.isChecked()
        self._content_scroll.setVisible(self._expanded and bool(self._records))
        self._toggle.setText("收起" if self._expanded else "展开")
        self._sync_height_limit()

    def _render_empty(self, message: str) -> None:
        self._count_badge.setText("无记录")
        self._count_badge.set_tone("default")
        self._token_badge.setText("Token 0")
        self._token_badge.set_tone("default")
        self._hint.setText(message)
        self._activity_hint.clear()
        self._activity_hint.setVisible(False)
        self._records = ()
        self._content_scroll.setVisible(False)
        self._toggle.setChecked(False)
        self._toggle.setText("展开")
        self._sync_height_limit()
        self._clear_charts()

    def _render_snapshot(self, snapshot: ModelCallSnapshot) -> None:
        if not snapshot.records:
            self._render_empty(snapshot.missing_reason or "暂无模型调用记录。")
            self._render_activity(snapshot.activity)
            return
        self._count_badge.setText(f"{snapshot.total_calls} 次调用")
        if snapshot.terminal_failed_count:
            self._count_badge.set_tone("danger")
        elif snapshot.retry_count:
            self._count_badge.set_tone("warning")
        else:
            self._count_badge.set_tone("success")
        self._token_badge.setText(f"Token {_fmt_count(snapshot.total_tokens)}")
        self._token_badge.set_tone("warning" if snapshot.total_tokens else "default")
        parts = [
            f"成功 {snapshot.success_count}",
            f"自动重试 {snapshot.retry_count}" if snapshot.retry_count else "",
            f"异常 {snapshot.terminal_failed_count}" if snapshot.terminal_failed_count else "",
            f"费用 {_fmt_cost(snapshot.total_cost_usd)}" if snapshot.total_cost_usd else "",
            f"日志：{snapshot.run_dir}" if snapshot.run_dir is not None else "",
        ]
        if snapshot.truncated:
            parts.append("调用较多，仅显示最近记录")
        self._hint.setText("  ·  ".join(part for part in parts if part))
        self._render_activity(snapshot.activity)
        self._render_charts(snapshot)
        self._render_call_select(snapshot.records)
        self._content_scroll.setVisible(self._expanded)
        self._sync_height_limit()

    def _sync_height_limit(self) -> None:
        expanded = self._expanded and bool(self._records)
        collapsed_height = 158 if self._activity_hint.isVisible() else 128
        self.setMaximumHeight(16777215 if expanded else collapsed_height)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred if expanded else QSizePolicy.Policy.Maximum,
        )
        self._content_scroll.setMaximumHeight(16777215 if expanded else 0)
        self.updateGeometry()

    def _render_charts(self, snapshot: ModelCallSnapshot) -> None:
        self._clear_charts()
        self._add_bar_row(
            self._token_chart,
            "Prompt Token",
            snapshot.prompt_tokens,
            max(1, snapshot.total_tokens),
            _fmt_count(snapshot.prompt_tokens),
        )
        self._add_bar_row(
            self._token_chart,
            "Completion Token",
            snapshot.completion_tokens,
            max(1, snapshot.total_tokens),
            _fmt_count(snapshot.completion_tokens),
        )
        provider_rows = snapshot.provider_token_totals()
        if provider_rows:
            self._add_chart_title(self._provider_chart, "Provider / Model Token 分布")
            max_provider = max(value for _label, value in provider_rows) or 1
            for label, value in provider_rows:
                self._add_bar_row(
                    self._provider_chart, label, value, max_provider, _fmt_count(value)
                )
        task_rows = snapshot.task_latency_totals()
        if task_rows:
            self._add_chart_title(self._task_chart, "Task 耗时分布")
            max_task = max(value for _label, value in task_rows) or 1
            for task_label, task_value in task_rows:
                self._add_bar_row(
                    self._task_chart,
                    task_label,
                    task_value,
                    max_task,
                    _fmt_latency(task_value),
                )

    def _render_call_select(self, records: tuple[ModelCallRecord, ...]) -> None:
        old = self._call_select.blockSignals(True)
        self._call_select.clear()
        for index, record in enumerate(records, start=1):
            label = (
                f"{index:03d} · {record.task or 'unknown'} · "
                f"{record.provider or 'provider'} / {record.model or 'model'} · "
                f"{_call_status_label(record)}"
            )
            self._call_select.addItem(label)
        self._call_select.blockSignals(old)
        self._render_selected_call(0)

    def _render_selected_call(self, index: int) -> None:
        if index < 0 or index >= len(self._records):
            self._request_preview.setPlainText("无输入预览。")
            self._response_preview.setPlainText("无输出预览。")
            return
        record = self._records[index]
        request_text = record.request_preview or "无输入预览。"
        response_text = record.error_preview if record.error_preview else record.response_preview
        response_text = response_text or "无输出预览。"
        meta_lines = [
            f"状态：{_call_status_label(record)}",
            f"任务：{record.task}",
            f"Provider / Model：{record.provider} / {record.model}",
            f"路由：{record.route}",
            f"记录时间：{record.recorded_at or '未记录'}",
            f"耗时：{_fmt_latency(record.latency_ms)}",
            (
                "Token："
                f"输入 {_fmt_count(record.prompt_tokens)} / "
                f"输出 {_fmt_count(record.completion_tokens)} / "
                f"总计 {_fmt_count(record.total_tokens)}"
            ),
            f"费用：{_fmt_cost(record.cost_usd)}",
            f"输出上限：{_fmt_count(record.max_tokens)} Token",
            f"Temperature：{record.temperature}",
        ]
        if record.attempt and (record.attempt > 1 or record.status != "success"):
            attempt_text = f"第 {record.attempt} 次"
            if record.max_attempts:
                attempt_text += f" / 最多 {record.max_attempts} 次"
            meta_lines.append(f"尝试：{attempt_text}")
        if record.next_max_tokens:
            meta_lines.append(f"下次输出上限：{_fmt_count(record.next_max_tokens)} Token")
        if record.finish_reason:
            meta_lines.append(f"完成原因：{record.finish_reason}")
        meta_lines.append(f"日志文件：{record.path}")
        meta = "\n".join(meta_lines) + "\n\n"
        self._request_preview.setPlainText(meta + request_text)
        self._response_preview.setPlainText(response_text)

    def _render_activity(self, activity: ModelCallActivity | None) -> None:
        text = _activity_text(activity)
        self._activity_hint.setText(text)
        self._activity_hint.setVisible(bool(text))
        self._sync_height_limit()

    def _add_chart_title(self, layout: QVBoxLayout, text: str) -> None:
        label = QLabel(text)
        label.setObjectName("cardMeta")
        layout.addWidget(label)

    def _add_bar_row(
        self,
        layout: QVBoxLayout,
        label_text: str,
        value: float,
        maximum: float,
        value_text: str,
    ) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)
        compact_label = _compact_label(label_text)
        label = QLabel(compact_label)
        label.setObjectName("cardMeta")
        label.setFixedWidth(_MODEL_BAR_LABEL_WIDTH)
        label.setWordWrap(False)
        if compact_label != label_text:
            label.setToolTip(label_text)
        row.addWidget(label)
        bar = QProgressBar()
        bar.setObjectName("taskModelCallBar")
        bar.setRange(0, 1000)
        bar.setMinimumWidth(120)
        ratio = 0 if maximum <= 0 else min(1.0, max(0.0, float(value) / float(maximum)))
        bar.setValue(round(ratio * 1000))
        bar.setFormat(value_text)
        row.addWidget(bar, 1)
        layout.addLayout(row)

    def _clear_charts(self) -> None:
        clear_layout(self._token_chart)
        clear_layout(self._provider_chart)
        clear_layout(self._task_chart)
