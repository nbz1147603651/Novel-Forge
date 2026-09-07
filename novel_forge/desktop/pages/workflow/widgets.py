"""Reusable widgets for the workflow page.

This module provides composable widgets used in WorkflowPage:
- HeroPanel: Welcome banner with title and action buttons
- ActiveProjectsPanel: List of in-progress projects for quick resume
- JobsPanel: Job feed display with empty state handling
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.components.dialogs import show_warning_message
from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState
from novel_forge.desktop.pages.standalone.init_manual_repair_dialog import (
    InitManualRepairDialog,
    init_manual_repair_available,
)
from novel_forge.desktop.pages.workflow.components import JobCard
from novel_forge.desktop.pages.workflow.jobs import (
    _CARD_FIXED_HEIGHT,
    _llm_task_display_name,
    show_stop_confirm_dialog,
)
from novel_forge.desktop.task_flow import task_flow_is_cancelled, task_flow_is_clearable
from novel_forge.desktop.task_flow_errors import (
    task_flow_error_guidance_lines,
    task_flow_error_recovery_text,
)
from novel_forge.desktop.widgets import (
    ActionButton,
    Badge,
    SectionHeading,
    Surface,
    clear_layout,
)
from novel_forge.desktop.workspace import DesktopProjectItem, DesktopWorkspaceSnapshot

__all__ = [
    "HeroPanel",
    "ActiveProjectsPanel",
    "JobsPanel",
]

_MAX_RENDERED_TASKS = 24
_TASK_FLOW_MAX_HEIGHT = 640
_TASK_FLOW_MIN_VISIBLE_CARDS = 1.0
_ERROR_EVENT_STEPS = frozenset(
    {
        "format_repair_strategy_miss",
        "format_retry",
        "format_retry_exhausted",
        "tts_auto_trigger_failed",
        "tts_auto_trigger_partial",
    }
)
_FAILED_STEP_SUFFIXES = ("_failed", "_error", "_timeout")


def _truncate_error_text(text: str, limit: int = 360) -> str:
    value = " ".join(str(text or "").split()).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _error_entry_id(*parts: object) -> str:
    raw = "\u241f".join(str(part or "") for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _task_step_resolves_format_retry(step: str, task: str) -> bool:
    if not step or not task:
        return False
    if step == task:
        return True
    if not step.startswith(f"{task}_"):
        return False
    return not step.endswith(_FAILED_STEP_SUFFIXES)


def _format_retry_auto_resolved(
    events: list[Any],
    *,
    event_index: int,
    task: str,
) -> bool:
    if not task:
        return False
    for later in events[event_index + 1 :]:
        step = str(getattr(later, "step", "") or "")
        payload = getattr(later, "payload", {})
        payload = payload if isinstance(payload, dict) else {}
        if step == "format_repaired" and str(payload.get("task") or "") == task:
            return True
        if step == "format_validation_success" and str(payload.get("task") or "") == task:
            return True
        if _task_step_resolves_format_retry(step, task):
            return True
    return False


def _task_flow_error_entries(jobs: list[DesktopJobRecord]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for job in jobs:
        events = list(job.events)
        for event_index, event in enumerate(events):
            if event.step not in _ERROR_EVENT_STEPS:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            task = str(payload.get("task") or "")
            attempt = payload.get("attempt")
            max_attempts = payload.get("max_attempts")
            if isinstance(attempt, int) and isinstance(max_attempts, int) and max_attempts > 0:
                attempt_text = f"{attempt}/{max_attempts}"
            else:
                attempt_text = ""
            auto_resolved = event.step == "format_retry" and _format_retry_auto_resolved(
                events, event_index=event_index, task=task
            )
            entries.append(
                {
                    "id": _error_entry_id(
                        job.job_id,
                        event.at,
                        event.step,
                        task,
                        attempt_text,
                        payload.get("error"),
                        payload.get("log_file"),
                    ),
                    "job_id": str(job.job_id or ""),
                    "time": str(event.at or ""),
                    "job": str(job.label or job.kind),
                    "task": task,
                    "task_label": _llm_task_display_name(payload.get("task")),
                    "attempt": attempt_text,
                    "error": _truncate_error_text(str(payload.get("error") or "")),
                    "excerpt": _truncate_error_text(str(payload.get("raw_excerpt") or ""), 520),
                    "log_file": str(payload.get("log_file") or ""),
                    "kind": "格式已修复" if event.step == "format_repaired" else "格式错误",
                    "auto_resolved": "true" if auto_resolved else "",
                }
            )
        if (
            job.status == DesktopJobState.FAILED
            and not task_flow_is_cancelled(job)
            and (job.error or job.error_summary)
        ):
            summary = job.error_summary if isinstance(job.error_summary, dict) else {}
            error = (
                str(summary.get("summary") or "").strip()
                or str(summary.get("detail") or "").strip()
                or str(job.error or "").strip()
            )
            if error:
                entries.append(
                    {
                        "id": _error_entry_id(
                            job.job_id,
                            "job_failed",
                            job.current_step,
                            error,
                        ),
                        "job_id": str(job.job_id or ""),
                        "time": str(job.updated_at or ""),
                        "job": str(job.label or job.kind),
                        "task": str(job.current_step or ""),
                        "task_label": str(job.current_step or ""),
                        "attempt": "",
                        "error": _truncate_error_text(error),
                        "excerpt": _truncate_error_text(
                            task_flow_error_recovery_text(summary),
                            520,
                        ),
                        "log_file": "",
                        "kind": "任务失败",
                    }
                )
    return entries


def _format_task_flow_error_entries(
    entries: list[dict[str, str]],
    resolved_entry_ids: set[str] | None = None,
) -> str:
    resolved_entry_ids = resolved_entry_ids or set()
    if not entries:
        return "暂无错误记录。"
    blocks: list[str] = []
    for index, entry in enumerate(entries, start=1):
        if entry.get("auto_resolved") == "true":
            state = "已自动恢复"
        elif entry.get("id", "") in resolved_entry_ids:
            state = "已标记修复"
        else:
            state = "待确认"
        header_parts = [
            f"[{index}] {entry.get('kind', '错误')}",
            f"状态：{state}",
            entry.get("time", ""),
            entry.get("job", ""),
        ]
        task = entry.get("task", "")
        if task:
            task_label = entry.get("task_label", "") or task
            if task_label != task:
                header_parts.append(f"任务：{task_label}（{task}）")
            else:
                header_parts.append(f"任务：{task}")
        attempt = entry.get("attempt", "")
        if attempt:
            header_parts.append(f"尝试：{attempt}")
        lines = ["  ·  ".join(part for part in header_parts if part)]
        error = entry.get("error", "")
        if error:
            lines.append(f"错误：{error}")
        excerpt = entry.get("excerpt", "")
        if excerpt:
            lines.append(f"片段：{excerpt}")
        lines.extend(task_flow_error_guidance_lines(entry))
        log_file = entry.get("log_file", "")
        if log_file:
            lines.append(f"日志：{log_file}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


class TaskFlowErrorLogDialog(QDialog):
    """Read-only task-flow error log viewer."""

    resolved_requested = Signal()

    def __init__(
        self,
        entries: list[dict[str, str]],
        resolved_entry_ids: set[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("errorLogDialog")
        self._entries = list(entries)
        self._resolved_entry_ids = set(resolved_entry_ids or set())
        self.setWindowTitle("任务流错误日志")
        self.setModal(True)
        self.setMinimumSize(760, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        self._title = QLabel()
        self._title.setObjectName("dlgTitle")
        layout.addWidget(self._title)

        self._hint = QLabel()
        self._hint.setObjectName("dlgHint")
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)

        self._text = QTextEdit()
        self._text.setObjectName("taskFlowErrorLogText")
        self._text.setReadOnly(True)
        layout.addWidget(self._text, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        self._mark_resolved_btn = ActionButton("标记已修复", variant="primary")
        self._mark_resolved_btn.setToolTip("保留日志内容，但将当前条目标记为已修复/已处理。")
        self._mark_resolved_btn.clicked.connect(self._on_mark_resolved)
        row.addWidget(self._mark_resolved_btn)
        close_btn = ActionButton("关闭", variant="secondary")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        layout.addLayout(row)
        self._refresh()

    def _unresolved_count(self) -> int:
        return sum(
            1
            for entry in self._entries
            if entry.get("auto_resolved") != "true"
            and entry.get("id", "") not in self._resolved_entry_ids
        )

    def _refresh(self) -> None:
        unresolved_count = self._unresolved_count()
        title_parts = [f"任务流错误日志 · {len(self._entries)} 条"]
        if self._entries:
            title_parts.append(f"待确认 {unresolved_count}" if unresolved_count else "已处理")
        self._title.setText(" · ".join(title_parts))
        self._hint.setText(
            "这里汇总模型格式重试与最终失败摘要；完整原文会单独落在运行日志的 format_errors 目录。"
            " 标记已修复只更新处理状态，不删除日志。"
        )
        self._text.setPlainText(
            _format_task_flow_error_entries(self._entries, self._resolved_entry_ids)
        )
        self._mark_resolved_btn.setEnabled(unresolved_count > 0)
        self._mark_resolved_btn.setText("标记已修复" if unresolved_count else "已标记修复")

    def _on_mark_resolved(self) -> None:
        for entry in self._entries:
            entry_id = entry.get("id", "")
            if entry_id:
                self._resolved_entry_ids.add(entry_id)
        self.resolved_requested.emit()
        self._refresh()


def _is_active_project(project: DesktopProjectItem) -> bool:
    return project.status not in {"completed", "archived"}


def _project_entry_label(project: DesktopProjectItem) -> str:
    allowed_operations = set(project.allowed_operations or ())
    if "retry" in allowed_operations:
        return "重试立项 →"
    if "resume" in allowed_operations:
        return "恢复 →"
    if project.init_resume_available:
        return "继续立项 →"
    if "start_writing" in allowed_operations:
        return "开始写作 →"
    if project.status == "writing":
        return "继续写作 →"
    return "下一步 →"


class HeroPanel(QWidget):
    """Welcome banner with title, description and action buttons."""

    view_project_clicked = Signal()
    goto_studio_clicked = Signal()

    _FADE_IN_DURATION_MS = 200

    def __init__(self) -> None:
        super().__init__()
        self._build_ui()

    def fade_in(self) -> None:
        """200ms opacity fade-in (D1-safe)."""
        try:
            from novel_forge.desktop.motion import Motion

            Motion.fade_in(self, duration=self._FADE_IN_DURATION_MS)
        except Exception:  # noqa: BLE001 — animation is purely cosmetic
            return

    def _build_ui(self) -> None:
        self.setMaximumHeight(118)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(22, 12, 22, 12)
        layout.setSpacing(18)

        copy_col = QVBoxLayout()
        copy_col.setContentsMargins(0, 0, 0, 0)
        copy_col.setSpacing(6)

        eyebrow = QLabel("机杼 · 创作工场")
        eyebrow.setObjectName("eyebrowLabel")
        copy_col.addWidget(eyebrow)

        title = QLabel("先选路线，再开写。")
        title.setObjectName("heroTitle")
        title.setProperty("density", "compact")
        title.setWordWrap(True)
        copy_col.addWidget(title)

        body = QLabel(
            "短篇：一句引子到成稿、润色与评估；长篇：先立项，再去章台逐章续写、查报告、交由 AI 决策。"
        )
        body.setObjectName("heroBody")
        body.setProperty("density", "compact")
        body.setWordWrap(True)
        copy_col.addWidget(body)
        layout.addLayout(copy_col, 1)

        action_col = QVBoxLayout()
        action_col.setContentsMargins(0, 2, 0, 0)
        action_col.setSpacing(8)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(10)

        self._view_project_button = ActionButton("阅卷", variant="secondary")
        self._view_project_button.clicked.connect(self._emit_view_project)
        self._view_project_button.setEnabled(False)
        action_row.addWidget(self._view_project_button)

        self._goto_studio_button = ActionButton("去章台 →", variant="secondary")
        self._goto_studio_button.clicked.connect(self._emit_goto_studio)
        self._goto_studio_button.setEnabled(False)
        action_row.addWidget(self._goto_studio_button)
        action_col.addLayout(action_row)
        action_col.addStretch()
        layout.addLayout(action_col)

    def _emit_view_project(self) -> None:
        self.view_project_clicked.emit()

    def _emit_goto_studio(self) -> None:
        self.goto_studio_clicked.emit()

    def set_buttons_enabled(self, enabled: bool) -> None:
        """Enable or disable action buttons."""
        self._view_project_button.setEnabled(enabled)
        self._goto_studio_button.setEnabled(enabled)


class ActiveProjectsPanel(QWidget):
    """Panel showing in-progress projects for quick resume access."""

    project_resume_requested = Signal(str, str, int)  # project_id, mode, next_chapter

    def __init__(self) -> None:
        super().__init__()
        self._render_signature: tuple[tuple[object, ...], ...] = ()
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(10)

        layout.addWidget(
            SectionHeading(
                "在制项目", "未完成的项目列于此，点击可快速进入对应表单。完成项目展示于卷帙。"
            )
        )

        self._projects_list = QVBoxLayout()
        self._projects_list.setSpacing(6)
        layout.addLayout(self._projects_list)

        self._empty_label = QLabel("暂无进行中项目——可下方选择模式新建。")
        self._empty_label.setObjectName("fieldHint")
        layout.addWidget(self._empty_label)

    def render_snapshot(self, snapshot: DesktopWorkspaceSnapshot) -> None:
        """Refresh the panel from the latest snapshot."""
        active = [p for p in snapshot.projects if _is_active_project(p)]
        signature = tuple(
            (
                p.project_id,
                p.title,
                p.mode,
                p.status,
                p.status_label,
                p.progress_label,
                p.next_chapter,
                p.completed_chapters,
                p.allowed_operations,
            )
            for p in active[:8]
        )
        if signature == self._render_signature:
            return
        self._render_signature = signature

        clear_layout(self._projects_list)
        self._empty_label.setVisible(not active)
        self.setVisible(bool(active))

        for project in active[:8]:
            row = self._build_project_row(project)
            self._projects_list.addWidget(row)

    def render(self, *args: Any, **kwargs: Any) -> None:
        """Compatibility shim for older callers; QWidget.render still delegates to Qt."""
        if len(args) == 1 and not kwargs and isinstance(args[0], DesktopWorkspaceSnapshot):
            self.render_snapshot(args[0])
            return
        super().render(*args, **kwargs)

    def _build_project_row(self, project: DesktopProjectItem) -> QWidget:
        row = Surface("inset")
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(10)

        # Project name
        name_label = QLabel(project.title or project.project_id)
        name_label.setObjectName("cardMeta")
        name_label.setMinimumWidth(0)
        name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(name_label, 1)

        # Mode badge
        mode_badge = Badge(project.mode_label, tone="default")
        layout.addWidget(mode_badge)

        # Status badge
        status_tone = {"writing": "warning", "planning": "default"}.get(project.status, "default")
        status_badge = Badge(project.status_label, tone=status_tone)
        layout.addWidget(status_badge)

        # Progress label
        progress_label = QLabel(project.progress_label)
        progress_label.setObjectName("fieldHint")
        progress_label.setMinimumWidth(0)
        progress_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(progress_label, 1)

        # Resume button
        resume_btn = ActionButton(_project_entry_label(project), variant="secondary")
        resume_btn.setProperty("compact", True)
        resume_btn.style().unpolish(resume_btn)
        resume_btn.style().polish(resume_btn)
        resume_btn.setMinimumWidth(max(116, resume_btn.sizeHint().width()))
        resume_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        next_ch = project.next_chapter or 1
        resume_btn.clicked.connect(
            lambda checked=False, pid=project.project_id, mode=project.mode, nch=next_ch: (
                self.project_resume_requested.emit(pid, mode, nch)
            )
        )
        layout.addWidget(resume_btn)

        return row


class JobsPanel(QWidget):
    """Panel displaying job feed with empty state handling."""

    clear_task_flow_requested = Signal(object)  # job_ids
    task_flow_error_logs_resolved = Signal(object)  # {job_id: [entry_id, ...]}
    stop_job_requested = Signal(str)  # job_id
    checkpoint_resume_requested = Signal(str, str, int)  # job_id, project_id, chapter_number
    resume_job_requested = Signal(str, str, int)  # job_id, project_id, chapter_number
    init_repair_retry_requested = Signal(str, str, bool)  # job_id, project_id, reset history

    def __init__(self) -> None:
        super().__init__()
        self._storage_root: Path | None = None
        self._job_cards: dict[str, JobCard] = {}
        self._empty_state: QWidget | None = None
        self._preferred_width = 0
        self._task_flow_clearable_job_ids: list[str] = []
        self._task_flow_error_entries: list[dict[str, str]] = []
        self._resolved_task_flow_error_ids: set[str] = set()
        self._latest_render_jobs: list[DesktopJobRecord] = []
        self._render_signature: tuple[object, ...] | None = None
        self._build_ui()

    def set_preferred_width(self, width: int) -> None:
        """Set the layout-requested width while preserving shrinkability."""
        self._preferred_width = max(0, int(width))
        self.updateGeometry()

    def sizeHint(self) -> QSize:  # type: ignore[override]
        hint = super().sizeHint()
        return QSize(max(hint.width(), self._preferred_width), hint.height())

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._schedule_job_card_width_sync()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._schedule_job_card_width_sync()

    def _schedule_job_card_width_sync(self) -> None:
        self._sync_job_card_widths()
        QTimer.singleShot(0, self._sync_job_card_widths)

    def _build_ui(self) -> None:
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(14)

        heading_row = QHBoxLayout()
        heading_row.setSpacing(10)
        heading_row.addWidget(
            SectionHeading(
                "任务流",
                "后台任务的实时进度、执行步骤、Token 摘要与失败原因。",
            ),
            1,
        )
        self._error_logs_btn = ActionButton("错误日志 0", variant="secondary")
        self._error_logs_btn.setProperty("errorState", "clean")
        self._error_logs_btn.setToolTip("查看任务流中的格式错误、重试次数与失败摘要。")
        self._error_logs_btn.clicked.connect(self._on_view_error_logs)
        heading_row.addWidget(self._error_logs_btn, 0, Qt.AlignmentFlag.AlignTop)
        self._clear_task_flow_btn = ActionButton("🧹 清理", variant="secondary")
        self._clear_task_flow_btn.setToolTip(
            "清空机杼任务流里的历史步骤（已完成/失败/待决策）。\n运行中任务不会被中断。"
        )
        self._clear_task_flow_btn.clicked.connect(self._on_clear_task_flow)
        self._clear_task_flow_btn.setEnabled(False)
        heading_row.addWidget(self._clear_task_flow_btn, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(heading_row)
        self._jobs_scroll = QScrollArea()
        self._jobs_scroll.setWidgetResizable(True)
        self._jobs_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._jobs_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._jobs_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._jobs_scroll.setMaximumHeight(_TASK_FLOW_MAX_HEIGHT)
        self._jobs_scroll.setMinimumWidth(0)
        self._jobs_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._jobs_scroll.setObjectName("workflowTaskFlowScroll")

        self._jobs_content = QWidget()
        self._jobs_content.setObjectName("workflowTaskFlowContent")
        self._jobs_content.setMinimumWidth(0)
        self._jobs_content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._jobs_layout = QVBoxLayout(self._jobs_content)
        self._jobs_layout.setSpacing(10)
        self._jobs_layout.setContentsMargins(12, 12, 12, 12)
        self._jobs_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._jobs_scroll.setWidget(self._jobs_content)
        layout.addWidget(self._jobs_scroll)
        self._update_scroll_min_height()

    def render_jobs(
        self,
        jobs: list[DesktopJobRecord],
        storage_root: Path | None = None,
        active_project_ids: set[str] | None = None,
    ) -> None:
        """Refresh job list with optional storage root for file links."""
        self._storage_root = storage_root
        top_jobs = jobs[:_MAX_RENDERED_TASKS]
        blocked_project_ids = {
            str(project_id or "").strip()
            for project_id in (
                active_project_ids
                if active_project_ids is not None
                else {
                    job.project_id
                    for job in top_jobs
                    if job.status in {DesktopJobState.QUEUED, DesktopJobState.RUNNING}
                }
            )
        }
        blocked_project_ids.discard("")

        # Avoid traversing the card list, reordering layouts, and refreshing
        # button state when the job manager only delivered a non-visual event
        # (for example a streaming delta or token accounting update).  The
        # signature intentionally uses JobCard's visual contract so the panel
        # and individual cards share the same invalidation boundary.
        render_signature = (
            str(storage_root) if storage_root is not None else "",
            tuple(
                (
                    JobCard._signature_for(job),
                    tuple(
                        sorted(
                            str(item) for item in getattr(job, "resolved_error_entry_ids", set())
                        )
                    ),
                )
                for job in top_jobs
            ),
            tuple(sorted(blocked_project_ids)),
        )
        if render_signature == self._render_signature:
            return
        self._render_signature = render_signature
        self._latest_render_jobs = list(top_jobs)
        self._task_flow_clearable_job_ids = [
            job.job_id for job in top_jobs if task_flow_is_clearable(job)
        ]
        self._task_flow_error_entries = _task_flow_error_entries(top_jobs)
        current_error_ids = {
            entry.get("id", "") for entry in self._task_flow_error_entries if entry.get("id", "")
        }
        persisted_error_ids = {
            str(entry_id)
            for job in top_jobs
            for entry_id in getattr(job, "resolved_error_entry_ids", set())
            if str(entry_id or "").strip() in current_error_ids
        }
        self._resolved_task_flow_error_ids.update(persisted_error_ids)
        self._resolved_task_flow_error_ids.intersection_update(current_error_ids)
        self._update_clear_task_flow_btn_state()
        self._update_error_logs_btn_state()
        if not top_jobs:
            self._clear_job_cards()
            if self._empty_state is None:
                self._empty_state = self._build_empty_state()
                self._jobs_layout.addWidget(self._empty_state)
            self._schedule_job_card_width_sync()
            self._update_scroll_min_height()
            return

        if self._empty_state is not None:
            self._jobs_layout.removeWidget(self._empty_state)
            self._empty_state.deleteLater()
            self._empty_state = None

        visible_ids = {job.job_id for job in top_jobs}
        for job_id in tuple(self._job_cards.keys()):
            if job_id in visible_ids:
                continue
            old_card = self._job_cards.pop(job_id)
            self._jobs_layout.removeWidget(old_card)
            old_card.deleteLater()

        for index, job in enumerate(top_jobs):
            card = self._job_cards.get(job.job_id)
            if card is None:
                card = JobCard(
                    job,
                    storage_root=storage_root,
                    enable_init_repair_actions=True,
                    init_repair_blocked_project_ids=blocked_project_ids,
                )
                card.stop_requested.connect(self._on_stop_requested)
                card.resume_requested.connect(self._on_resume_job)
                card.checkpoint_resume_requested.connect(self._on_checkpoint_resume_job)
                card.init_retry_requested.connect(self._on_init_retry_job)
                card.init_manual_repair_requested.connect(self._on_init_manual_repair_job)
                self._job_cards[job.job_id] = card
            else:
                card.set_init_repair_blocked_project_ids(blocked_project_ids)
                card.set_storage_root(storage_root)
                card.set_job(job)
            self._jobs_layout.insertWidget(index, card)
        self._schedule_job_card_width_sync()
        self._update_scroll_min_height()

    def render(self, *args: Any, **kwargs: Any) -> None:
        """Compatibility shim for older callers; QWidget.render still delegates to Qt."""
        if args and isinstance(args[0], list):
            storage_root = kwargs.get("storage_root")
            active_project_ids = kwargs.get("active_project_ids")
            if len(args) > 1:
                storage_root = args[1]
            if storage_root is None or isinstance(storage_root, Path):
                self.render_jobs(args[0], storage_root, active_project_ids)
                return
        super().render(*args, **kwargs)

    def _clear_job_cards(self) -> None:
        for card in self._job_cards.values():
            self._jobs_layout.removeWidget(card)
            card.deleteLater()
        self._job_cards.clear()

    def _build_empty_state(self) -> QWidget:
        empty = Surface("inset")
        empty_layout = QVBoxLayout(empty)
        empty_layout.setContentsMargins(24, 20, 24, 20)

        message = QLabel("机杼尚静，无任务在运。置卷后此处自现进度。")
        message.setObjectName("emptyMessage")
        message.setWordWrap(True)
        empty_layout.addWidget(message)
        return empty

    def _update_clear_task_flow_btn_state(self) -> None:
        has_clearable = len(self._task_flow_clearable_job_ids) > 0
        self._clear_task_flow_btn.setEnabled(has_clearable)

    def _update_error_logs_btn_state(self) -> None:
        count = len(self._task_flow_error_entries)
        unresolved_count = self._unresolved_task_flow_error_count()
        if count and unresolved_count == 0:
            text = f"错误日志 {count} · 已处理"
        elif count and unresolved_count != count:
            text = f"错误日志 {count} · 待处理 {unresolved_count}"
        else:
            text = f"错误日志 {count}"
        self._error_logs_btn.setText(text)
        self._error_logs_btn.setProperty("errorState", "error" if unresolved_count else "clean")
        self._error_logs_btn.setToolTip(
            (
                f"查看任务流错误日志（共 {count} 条，待确认 {unresolved_count} 条）。"
                if unresolved_count
                else f"查看任务流错误日志（共 {count} 条，均已标记处理）。"
            )
            if count
            else "当前任务流没有格式或运行错误。"
        )
        self._error_logs_btn.style().unpolish(self._error_logs_btn)
        self._error_logs_btn.style().polish(self._error_logs_btn)

    def _unresolved_task_flow_error_count(self) -> int:
        return sum(
            1
            for entry in self._task_flow_error_entries
            if entry.get("auto_resolved") != "true"
            and entry.get("id", "") not in self._resolved_task_flow_error_ids
        )

    def _mark_current_error_logs_resolved(self) -> None:
        resolved_by_job: dict[str, list[str]] = {}
        for entry in self._task_flow_error_entries:
            if entry.get("auto_resolved") == "true":
                continue
            entry_id = entry.get("id", "")
            if entry_id:
                self._resolved_task_flow_error_ids.add(entry_id)
                job_id = entry.get("job_id", "")
                if job_id:
                    resolved_by_job.setdefault(job_id, []).append(entry_id)
        if resolved_by_job:
            self.task_flow_error_logs_resolved.emit(resolved_by_job)
        self._update_error_logs_btn_state()

    def _on_clear_task_flow(self) -> None:
        terminal_job_ids = list(self._task_flow_clearable_job_ids)
        if not terminal_job_ids:
            terminal_job_ids = [
                job.job_id
                for job in self._latest_render_jobs
                if task_flow_is_clearable(job)
            ]
        if not terminal_job_ids:
            self._update_clear_task_flow_btn_state()
            return
        self.clear_task_flow_requested.emit(terminal_job_ids)

    def _on_view_error_logs(self) -> None:
        dialog = TaskFlowErrorLogDialog(
            list(self._task_flow_error_entries),
            set(self._resolved_task_flow_error_ids),
            self,
        )
        dialog.resolved_requested.connect(self._mark_current_error_logs_resolved)
        dialog.exec()

    def _on_stop_requested(self, job_id: str) -> None:
        card = self._job_cards.get(job_id)
        if card is None or card._job is None:
            return
        job = card._job
        if show_stop_confirm_dialog(self, job.label, job.current_step or ""):
            self.stop_job_requested.emit(job_id)

    def _on_resume_job(self, job_id: str, project_id: str, chapter_number: int) -> None:
        self.resume_job_requested.emit(job_id, project_id, chapter_number)

    def _on_checkpoint_resume_job(self, job_id: str, project_id: str, chapter_number: int) -> None:
        self.checkpoint_resume_requested.emit(job_id, project_id, chapter_number)

    def _on_init_retry_job(self, job_id: str, project_id: str) -> None:
        self.init_repair_retry_requested.emit(job_id, project_id, True)

    def _on_init_manual_repair_job(self, job_id: str, project_id: str) -> None:
        if self._storage_root is None or not project_id:
            show_warning_message(self, "无法人工修复", "项目目录尚不可用。")
            return
        project_dir = self._storage_root / project_id
        if not init_manual_repair_available(project_dir):
            show_warning_message(
                self,
                "无法人工修复",
                "没有找到 init_readiness.json 或可编辑的关联产物，请先查看运行日志。",
            )
            return
        dialog = InitManualRepairDialog(project_dir, self)
        dialog.exec()
        if dialog.saved_changes:
            self.init_repair_retry_requested.emit(job_id, project_id, False)

    def _update_scroll_min_height(self) -> None:
        """Fit the task-flow viewport to content, capped for long histories."""
        margins = self._jobs_layout.contentsMargins()
        spacing = self._jobs_layout.spacing()
        if self._job_cards:
            row_heights = [
                max(_CARD_FIXED_HEIGHT, card.sizeHint().height())
                for card in self._job_cards.values()
            ]
            content_height = margins.top() + margins.bottom() + sum(row_heights)
            content_height += spacing * max(0, len(row_heights) - 1)
        elif self._empty_state is not None:
            content_height = (
                margins.top()
                + margins.bottom()
                + max(_CARD_FIXED_HEIGHT, self._empty_state.sizeHint().height())
            )
        else:
            content_height = margins.top() + margins.bottom() + _CARD_FIXED_HEIGHT
        min_height = int(
            margins.top() + margins.bottom() + _CARD_FIXED_HEIGHT * _TASK_FLOW_MIN_VISIBLE_CARDS
        )
        target_height = min(
            _TASK_FLOW_MAX_HEIGHT,
            max(min_height, int(content_height)),
        )
        self._jobs_scroll.setMinimumHeight(target_height)
        self._jobs_scroll.setMaximumHeight(target_height)
        self._jobs_scroll.setFixedHeight(target_height)
        self.updateGeometry()

    def _sync_job_card_widths(self) -> None:
        try:
            viewport = self._jobs_scroll.viewport()
        except RuntimeError:
            # A queued resize callback may outlive page teardown.
            return
        available_width = viewport.width() if viewport is not None else self._jobs_scroll.width()
        if available_width <= 0:
            return
        self._jobs_content.setMaximumWidth(available_width)
        self._jobs_content.setMinimumWidth(0)
        margins = self._jobs_layout.contentsMargins()
        card_width = max(0, available_width - margins.left() - margins.right())
        for card in self._job_cards.values():
            card.setMaximumWidth(card_width)
            card.updateGeometry()
        if self._empty_state is not None:
            self._empty_state.setMaximumWidth(card_width)
            self._empty_state.updateGeometry()
        self._jobs_layout.activate()
        self._jobs_content.updateGeometry()
