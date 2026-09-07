"""Job rendering and button state management for ChapterStudioPage."""

from __future__ import annotations

import enum
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QTextEdit, QVBoxLayout, QWidget

from novel_forge.desktop.components.skeleton import create_skeleton_card_with_lines
from novel_forge.desktop.constants import (
    MAX_CHAPTER_JOBS_DISPLAY,
)
from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState
from novel_forge.desktop.pages.workflow.jobs import show_stop_confirm_dialog
from novel_forge.desktop.progress import (
    compute_job_progress,
    display_step_name_for_job,
    resolve_step_key,
)
from novel_forge.desktop.task_flow import (
    BOOK_TASK_KINDS,
    CHAPTER_EXECUTION_TASK_KINDS,
    CHAPTER_TASK_FLOW_KINDS,
    CHAPTER_TASK_KINDS,
    TaskFlowScope,
    task_flow_is_cancelled,
    task_flow_is_clearable,
    task_flow_matches_scope,
    task_flow_needs_attention,
)
from novel_forge.desktop.task_flow import (
    task_flow_job_chapter_number as shared_task_flow_job_chapter_number,
)
from novel_forge.desktop.task_flow_errors import (
    annotate_task_flow_error_entry,
    task_flow_error_guidance_lines,
    task_flow_error_recovery_text,
    task_flow_job_chapter_number,
    task_flow_job_is_inactive,
)
from novel_forge.desktop.widgets import ActionButton

from .contract import ChapterStudioMixinBase


class AuditState(enum.Enum):
    """Granular state for the book-level consistency audit button."""

    IDLE = "idle"
    AUDITING = "auditing"
    VERIFYING = "verifying"
    REPAIRING = "repairing"
    COMPLETE = "complete"
    FAILED = "failed"


_BOOK_JOB_RECENT_WINDOW_SECS = 300


def _recent_cutoff() -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=_BOOK_JOB_RECENT_WINDOW_SECS)
    return cutoff.isoformat()


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
        if _task_step_resolves_format_retry(step, task):
            return True
    return False


def _task_flow_error_entries(
    jobs: list[DesktopJobRecord],
    *,
    storage_root: object = None,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for job in jobs:
        events = list(job.events)
        chapter_number = task_flow_job_chapter_number(job)
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
                annotate_task_flow_error_entry(
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
                        "project_id": str(job.project_id or ""),
                        "job_id": str(job.job_id or ""),
                        "job_kind": str(job.kind or ""),
                        "job_label": str(job.label or ""),
                        "chapter_number": chapter_number,
                        "status": str(getattr(job.status, "value", job.status) or ""),
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
                    },
                    storage_root=storage_root,
                )
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
                    annotate_task_flow_error_entry(
                        {
                            "id": _error_entry_id(
                                job.job_id,
                                "job_failed",
                                job.current_step,
                                error,
                            ),
                            "project_id": str(job.project_id or ""),
                            "job_id": str(job.job_id or ""),
                            "job_kind": str(job.kind or ""),
                            "job_label": str(job.label or ""),
                            "chapter_number": chapter_number,
                            "status": str(getattr(job.status, "value", job.status) or ""),
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
                        },
                        storage_root=storage_root,
                    )
                )
    return entries


def _llm_task_display_name(task: str | None) -> str:
    if not task:
        return ""
    display_map = {
        "DRAFT_CHAPTER": "章节草稿",
        "EDIT_CHAPTER": "章节编辑",
        "PLAN_CHAPTER": "章节规划",
        "BRIDGE_CHAPTER": "章节桥接",
        "CHECK_CHAPTER": "章节检查",
        "CHECK_CONTINUITY": "连贯性检查",
        "REPAIR_CONTINUITY": "连贯性修复",
        "VALIDATE_CAUSAL": "因果验证",
        "REPAIR_CAUSAL": "因果修复",
        "EVALUATE_READING_POWER": "阅读力评估",
        "REPAIR_READING_POWER": "阅读力修复",
        "POLISH_CHAPTER": "章节精修",
        "HUMANIZE_SCAN": "AI 去痕扫描",
        "EXTRACT_CANON": "典据提取",
        "CHECK_ALIGNMENT": "对齐检查",
        "GUARD_CONSTRAINT_CHECK": "约束检查",
        "MACRO_GUARD_AUDIT": "宏观护栏",
        "REPAIR_GUARDRAIL": "护栏修复",
        "PLOT_GUARD_JUDGE": "剧情护栏",
        "REPAIR_SEMANTIC_VERIFY": "语义验证修复",
    }
    return display_map.get(task, task)


def _format_task_flow_error_entries(
    entries: list[dict[str, str]],
    resolved_entry_ids: set[str] | None = None,
) -> str:
    resolved_entry_ids = resolved_entry_ids or set()
    if not entries:
        return "暂无错误记录。"
    blocks: list[str] = []
    for index, entry in enumerate(entries, start=1):
        if entry.get("actionability") == "historical":
            state = "历史失效"
        elif entry.get("auto_resolved") == "true":
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
        inactive_reason = entry.get("inactive_reason", "")
        if inactive_reason:
            lines.append(f"说明：{inactive_reason}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


class TaskFlowErrorLogDialog(QDialog):
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


_CHAPTER_JOB_KINDS = CHAPTER_TASK_KINDS
_CHAPTER_VISIBLE_JOB_KINDS = CHAPTER_TASK_FLOW_KINDS
_BOOK_LEVEL_JOB_KINDS = BOOK_TASK_KINDS
_FINGERPRINT_IGNORED_TAIL_STEPS = frozenset(
    {
        "llm_stream_delta",
        "model_call_update",
        "budget_status",
        "preflight_token_estimate",
        "prompt_pressure",
    }
)
_CHAPTER_EXECUTION_FLOW_KINDS = CHAPTER_EXECUTION_TASK_KINDS
_TASK_FLOW_MAX_HEIGHT = 560
_TASK_FLOW_FALLBACK_CARD_HEIGHT = 160
_TASK_FLOW_MIN_VISIBLE_CARDS = 2.5


def _job_chapter_number(job: DesktopJobRecord) -> int | None:
    return shared_task_flow_job_chapter_number(job)


def _job_flow_key(job: DesktopJobRecord, *, fallback_chapter: int) -> tuple[int, str]:
    chapter = _job_chapter_number(job) or fallback_chapter
    flow = "chapter_execution" if job.kind in _CHAPTER_EXECUTION_FLOW_KINDS else job.kind
    return chapter, flow


def _classify_jobs_by_status(
    jobs: list[DesktopJobRecord],
    *,
    current_chapter: int,
) -> tuple[list[DesktopJobRecord], list[DesktopJobRecord]]:
    active_states = {
        DesktopJobState.QUEUED,
        DesktopJobState.RUNNING,
        DesktopJobState.PAUSED,
    }
    active: list[DesktopJobRecord] = []
    historical: list[DesktopJobRecord] = []
    for job in jobs:
        if job.status in active_states:
            active.append(job)
        else:
            historical.append(job)
    return active, historical


def _chapter_job_needs_attention(job: DesktopJobRecord) -> bool:
    return task_flow_needs_attention(job)


def _relevant_chapter_jobs(
    jobs: list[DesktopJobRecord],
    *,
    project_id: str,
    current_chapter: int,
) -> list[DesktopJobRecord]:
    relevant: list[DesktopJobRecord] = []
    for job in jobs:
        if job.kind not in _CHAPTER_VISIBLE_JOB_KINDS:
            continue
        if task_flow_matches_scope(
            job,
            TaskFlowScope.CHAPTER,
            project_id=project_id,
            chapter_number=current_chapter,
        ):
            relevant.append(job)
    return relevant


class ChapterStudioJobsMixin(ChapterStudioMixinBase):
    """Mixin providing job rendering, task flow management, and button state updates."""

    def _active_project_jobs(
        self,
        jobs: list[DesktopJobRecord] | None = None,
    ) -> list[DesktopJobRecord]:
        """Return active chapter jobs for the visible project, including other chapters.

        Chapter auto-run may keep advancing in the background when "跟随连跑" is
        off.  The chapter panel is still scoped to the selected chapter, but the
        user needs to see that the same project is busy elsewhere instead of a
        misleading "当前章节暂无任务" state.
        """
        project_id = self.current_project_id()
        if not project_id:
            return []
        source_jobs = jobs
        if source_jobs is None:
            source_jobs = getattr(self, "_all_jobs", None) or []
        active_states = {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
        return [
            job
            for job in source_jobs
            if job.kind in _CHAPTER_JOB_KINDS
            and job.project_id == project_id
            and job.status in active_states
        ]

    def _latest_active_project_job(self) -> DesktopJobRecord | None:
        """Return the newest active chapter job for the visible project."""
        active_jobs = self._active_project_jobs()
        return active_jobs[0] if active_jobs else None

    def _render_jobs_panel(
        self, all_jobs: list[DesktopJobRecord], *, loading: bool = False
    ) -> None:
        from novel_forge.desktop.pages.workflow.jobs import JobCard

        storage_root = self._workspace.storage_root if self._workspace else None
        project_id = self.current_project_id()
        chapter_number = self.current_chapter_number()
        chapter_jobs = list(self._jobs) if self._jobs else []
        if all_jobs:
            known_ids = {job.job_id for job in chapter_jobs}
            chapter_jobs.extend(
                job
                for job in _relevant_chapter_jobs(
                    all_jobs,
                    project_id=project_id,
                    current_chapter=chapter_number,
                )
                if job.job_id not in known_ids
            )
        if storage_root is not None:
            chapter_jobs = [
                job
                for job in chapter_jobs
                if not task_flow_job_is_inactive(job, storage_root=storage_root)
            ]
        if self._studio is not None and self._studio.pending_checkpoint is None:
            chapter_jobs = [job for job in chapter_jobs if job.status != DesktopJobState.PAUSED]

        active_jobs, historical_jobs = _classify_jobs_by_status(
            chapter_jobs,
            current_chapter=self.current_chapter_number(),
        )

        book_jobs = [
            j
            for j in self._book_level_jobs
            if j.status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
            or (
                j.status in {DesktopJobState.SUCCEEDED, DesktopJobState.FAILED}
                and j.updated_at
                and j.updated_at > _recent_cutoff()
            )
        ]

        display_jobs = book_jobs + active_jobs + historical_jobs
        display_jobs = display_jobs[:MAX_CHAPTER_JOBS_DISPLAY]
        self._task_flow_clearable_job_ids = [
            job.job_id for job in display_jobs if task_flow_is_clearable(job)
        ]
        self._latest_render_jobs = list(display_jobs)
        self._task_flow_error_entries = _task_flow_error_entries(
            display_jobs,
            storage_root=storage_root,
        )
        current_error_ids = {
            entry.get("id", "") for entry in self._task_flow_error_entries if entry.get("id", "")
        }
        persisted_error_ids = {
            str(entry_id)
            for job in display_jobs
            for entry_id in getattr(job, "resolved_error_entry_ids", set())
            if str(entry_id or "").strip() in current_error_ids
        }
        self._resolved_task_flow_error_ids.update(persisted_error_ids)
        self._resolved_task_flow_error_ids.intersection_update(current_error_ids)
        self._update_clear_task_flow_btn_state()
        self._update_error_logs_btn_state()

        # Hide skeleton when we have real data or are done loading
        if display_jobs or not loading:
            skeleton_widget = self._jobs_skeleton_widget
            if skeleton_widget is not None:
                self._jobs_layout.removeWidget(skeleton_widget)
                skeleton_widget.deleteLater()
                self._jobs_skeleton_widget = None
            # Cancel skeleton timeout if active
            self._jobs_skeleton_timeout_active = False

        if display_jobs:
            if self._jobs_empty_state is not None:
                self._jobs_empty_state.hide()
                self._jobs_layout.removeWidget(self._jobs_empty_state)
                self._jobs_empty_state.deleteLater()
                self._jobs_empty_state = None

            visible_ids = {job.job_id for job in display_jobs}
            for job_id in tuple(self._chapter_job_cards.keys()):
                if job_id in visible_ids:
                    continue
                card = self._chapter_job_cards.pop(job_id)
                self._jobs_layout.removeWidget(card)
                card.deleteLater()

            if not historical_jobs:
                self._remove_history_separator()

            historical_start_index = len(book_jobs) + len(active_jobs)
            current_index = 0

            for index, job in enumerate(display_jobs):
                is_historical = index >= historical_start_index
                card = self._chapter_job_cards.get(job.job_id)
                if card is None:
                    card = JobCard(job, storage_root=storage_root, historical=is_historical)
                    card.stop_requested.connect(self._on_stop_job_requested)
                    card.resume_requested.connect(self._on_resume_job_requested)
                    card.checkpoint_resume_requested.connect(self._on_checkpoint_resume_requested)
                    self._chapter_job_cards[job.job_id] = card
                else:
                    if hasattr(card, "set_storage_root"):
                        card.set_storage_root(storage_root)
                    if hasattr(card, "set_historical"):
                        card.set_historical(is_historical)
                    if hasattr(card, "set_job"):
                        card.set_job(job)

                if index == historical_start_index and historical_jobs:
                    self._render_history_separator(current_index)
                    current_index += 1

                self._jobs_layout.insertWidget(current_index, card)
                current_index += 1

            self._update_jobs_scroll_min_height()
            return

        # No display jobs — show skeleton if loading, otherwise empty state
        if loading:
            if self._jobs_empty_state is not None:
                self._jobs_empty_state.hide()
            if getattr(self, "_jobs_skeleton_widget", None) is None:
                skeleton = create_skeleton_card_with_lines(line_count=3)
                skeleton.start_pulse()
                for child in skeleton.findChildren(QWidget):
                    if hasattr(child, "start_pulse"):
                        child.start_pulse()
                self._jobs_skeleton_widget = skeleton
                self._jobs_layout.addWidget(skeleton)
                self._jobs_skeleton_timeout_active = True
                QTimer.singleShot(3000, self._hide_jobs_skeleton_on_timeout)
            else:
                # Skeleton already exists — reset timeout flag to prevent stale timeout
                # from removing skeleton if jobs arrive and hide it normally first
                self._jobs_skeleton_timeout_active = False
            return

        # Truly empty — show empty state, hide skeleton
        if self._jobs_empty_state is None:
            self._jobs_empty_state = QLabel("当前章节暂无相关任务运行中。")
            self._jobs_empty_state.setObjectName("fieldHint")
            self._jobs_layout.addWidget(self._jobs_empty_state)
        else:
            self._jobs_empty_state.show()

        visible_ids = {job.job_id for job in display_jobs}
        for job_id in tuple(self._chapter_job_cards.keys()):
            if job_id in visible_ids:
                continue
            card = self._chapter_job_cards.pop(job_id)
            self._jobs_layout.removeWidget(card)
            card.deleteLater()
        self._remove_history_separator()

        for index, job in enumerate(display_jobs):
            card = self._chapter_job_cards.get(job.job_id)
            if card is None:
                card = JobCard(job, storage_root=storage_root)
                self._chapter_job_cards[job.job_id] = card
            else:
                if hasattr(card, "set_storage_root"):
                    card.set_storage_root(storage_root)
                if hasattr(card, "set_job"):
                    card.set_job(job)
            self._jobs_layout.insertWidget(index, card)
        self._update_jobs_scroll_min_height()

    def _hide_jobs_skeleton_on_timeout(self) -> None:
        if not getattr(self, "_jobs_skeleton_timeout_active", False):
            return
        skeleton_widget = self._jobs_skeleton_widget
        if skeleton_widget is None:
            return
        self._jobs_layout.removeWidget(skeleton_widget)
        skeleton_widget.deleteLater()
        self._jobs_skeleton_widget = None
        self._jobs_skeleton_timeout_active = False
        if self._jobs_empty_state is None:
            self._jobs_empty_state = QLabel("当前章节暂无相关任务运行中。")
            self._jobs_empty_state.setObjectName("fieldHint")
            self._jobs_layout.addWidget(self._jobs_empty_state)
        else:
            self._jobs_empty_state.show()

    def _update_jobs_scroll_min_height(self) -> None:
        row_height = _TASK_FLOW_FALLBACK_CARD_HEIGHT
        if self._chapter_job_cards:
            sample = next(iter(self._chapter_job_cards.values()))
            row_height = max(
                row_height,
                sample.minimumSizeHint().height(),
                sample.sizeHint().height(),
            )
        elif self._jobs_empty_state is not None:
            row_height = max(row_height, self._jobs_empty_state.sizeHint().height())
        visible_cards = _TASK_FLOW_MIN_VISIBLE_CARDS
        margins = self._jobs_layout.contentsMargins()
        spacing = self._jobs_layout.spacing()
        min_h = int(
            margins.top()
            + margins.bottom()
            + row_height * visible_cards
            + spacing * max(0.0, visible_cards - 1.0)
        )
        self._jobs_scroll.setMinimumHeight(min(min_h, _TASK_FLOW_MAX_HEIGHT))

    def _clear_chapter_job_cards(self) -> None:
        for card in self._chapter_job_cards.values():
            self._jobs_layout.removeWidget(card)
            card.deleteLater()
        self._chapter_job_cards.clear()
        if self._history_separator_widget is not None:
            self._remove_history_separator()

    def _render_history_separator(self, index: int | None = None) -> None:
        from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

        if self._has_history_separator():
            if index is not None and self._history_separator_widget is not None:
                self._jobs_layout.removeWidget(self._history_separator_widget)
                self._jobs_layout.insertWidget(index, self._history_separator_widget)
            return

        separator_container = QWidget()
        separator_container.setObjectName("historySeparatorContainer")
        sep_layout = QHBoxLayout(separator_container)
        sep_layout.setContentsMargins(0, 8, 0, 8)
        sep_layout.setSpacing(8)

        line_left = QFrame()
        line_left.setObjectName("railSep")
        line_left.setFixedHeight(1)
        sep_layout.addWidget(line_left, 1)

        label = QLabel("历史任务")
        label.setObjectName("cardMeta")
        label.setObjectName("memoryHint")
        sep_layout.addWidget(label)

        line_right = QFrame()
        line_right.setObjectName("railSep")
        line_right.setFixedHeight(1)
        sep_layout.addWidget(line_right, 1)

        self._history_separator_widget = separator_container
        if index is None:
            self._jobs_layout.addWidget(separator_container)
        else:
            self._jobs_layout.insertWidget(index, separator_container)

    def _has_history_separator(self) -> bool:
        return getattr(self, "_history_separator_widget", None) is not None

    def _remove_history_separator(self) -> None:
        separator = getattr(self, "_history_separator_widget", None)
        if separator is None:
            return
        self._jobs_layout.removeWidget(separator)
        separator.deleteLater()
        self._history_separator_widget = None

    def _update_clear_task_flow_btn_state(self) -> None:
        if not hasattr(self, "_clear_task_flow_btn"):
            return
        has_pending_checkpoint = (
            self._studio is not None and self._studio.pending_checkpoint is not None
        )
        has_clearable = any(
            task_flow_is_clearable(job, include_paused=has_pending_checkpoint) for job in self._jobs
        ) or bool(getattr(self, "_task_flow_clearable_job_ids", None))
        self._clear_task_flow_btn.setEnabled(has_clearable)

    def _on_clear_task_flow(self) -> None:
        project_id = self.current_project_id()
        if not project_id:
            return
        has_pending_checkpoint = (
            self._studio is not None and self._studio.pending_checkpoint is not None
        )
        terminal_job_ids = [
            job.job_id
            for job in self._jobs
            if task_flow_is_clearable(job, include_paused=has_pending_checkpoint)
        ]
        if not terminal_job_ids:
            terminal_job_ids = list(getattr(self, "_task_flow_clearable_job_ids", None) or [])
        if not terminal_job_ids:
            window = self.window()
            if hasattr(window, "show_priority_status"):
                window.show_priority_status(
                    "当前任务流没有可清理的历史步骤。",
                    3_500,
                    0,  # _STATUS_INFO
                )
            self._update_clear_task_flow_btn_state()
            return
        self.clear_task_flow_requested.emit(project_id, terminal_job_ids)

    def _update_error_logs_btn_state(self) -> None:
        if not hasattr(self, "_error_logs_btn"):
            return
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
        self._update_error_logs_btn_state()

    def _on_view_error_logs(self) -> None:
        dialog = TaskFlowErrorLogDialog(
            list(self._task_flow_error_entries),
            set(self._resolved_task_flow_error_ids),
            self,
        )
        dialog.resolved_requested.connect(self._mark_current_error_logs_resolved)
        dialog.exec()

    # ── JobCard button handlers ──────────────────────────────────────

    def _on_stop_job_requested(self, job_id: str) -> None:
        """Handle stop button click from a JobCard in the task flow."""
        card = self._chapter_job_cards.get(job_id)
        if card is None or card._job is None:
            return
        job = card._job
        if show_stop_confirm_dialog(self, job.label, job.current_step or ""):
            self.cancel_job_requested.emit(job_id, "用户已取消")

    def _on_resume_job_requested(self, job_id: str, project_id: str, chapter_number: int) -> None:
        """Handle resume button click for a PAUSED job card."""
        # User is already on chapter studio; just refresh to show checkpoint UI
        self.workspace_refresh_requested.emit()

    def _on_checkpoint_resume_requested(
        self, job_id: str, project_id: str, chapter_number: int
    ) -> None:
        """Handle checkpoint resume button click for a FAILED job card."""
        # User is already on chapter studio; refresh to pick up checkpoint state
        self.workspace_refresh_requested.emit()

    @staticmethod
    def _build_jobs_fingerprint(
        jobs: list[DesktopJobRecord],
        project_id: str,
        chapter_number: int,
    ) -> tuple[object, ...]:
        relevant: list[tuple[object, ...]] = []
        for job in jobs:
            if job.project_id != project_id:
                continue
            result = job.result if isinstance(job.result, dict) else {}
            target_chapter = int(result.get("chapter_number", chapter_number) or chapter_number)
            visible_events = [
                event for event in job.events if event.step not in _FINGERPRINT_IGNORED_TAIL_STEPS
            ]
            tail_step = visible_events[-1].step if visible_events else ""
            relevant.append(
                (
                    job.job_id,
                    job.kind,
                    job.status,
                    target_chapter,
                    job.current_step,
                    len(visible_events),
                    tail_step,
                    job.error,
                )
            )
        return (project_id, chapter_number, tuple(relevant))

    @staticmethod
    def _select_latest_memory_event(
        jobs: list[DesktopJobRecord],
        *,
        project_id: str,
        fallback_chapter: int,
    ) -> tuple[str, str, dict[str, Any]] | None:
        best: tuple[str, str, dict[str, Any]] | None = None
        for job in jobs:
            if job.project_id != project_id:
                continue
            for event in job.events[-10:]:
                event_type = ""
                payload: dict[str, Any] | None = None
                if event.step == "memory_invalidated":
                    event_type = "invalidated"
                    payload = event.payload if isinstance(event.payload, dict) else {}
                elif event.step == "memory_updated":
                    event_type = "updated"
                    payload = event.payload if isinstance(event.payload, dict) else {}
                elif event.step in ("concurrent_tasks_done", "memory_concurrent_tasks_done"):
                    # P0-5 (round 2): surface silent memory subtask failures
                    # to the UI. The actual event arriving at the desktop
                    # is ``memory_concurrent_tasks_done`` (workspace
                    # callback wraps the stage with the ``memory_`` prefix
                    # in execution_runners.py:410-426 and
                    # chapter_session_handlers.py:1598-1614). The unprefixed
                    # name is also accepted for defense-in-depth in case
                    # a future caller forgets the prefix.
                    # finalize_chapter_memory emits this with
                    # tasks_failed=["episodic", ...] when subtasks raise but
                    # the chapter itself is not failed. Previously the UI
                    # never saw these — 弈局谋心 chapter 1 lost its episodic
                    # memory silently with a green "completed" indicator.
                    if isinstance(event.payload, dict):
                        tasks_failed = event.payload.get("tasks_failed", []) or []
                        if tasks_failed:
                            event_type = "tasks_failed"
                            payload = {
                                "chapter": event.payload.get("chapter", fallback_chapter),
                                "tasks_failed": list(tasks_failed),
                                "tasks_ok": list(event.payload.get("tasks_ok", []) or []),
                            }
                elif event.step.startswith("memory_"):
                    stage_status = (
                        event.payload.get("memory_status")
                        if isinstance(event.payload, dict)
                        else None
                    )
                    if isinstance(stage_status, dict):
                        merged_status = dict(stage_status)
                        if isinstance(event.payload, dict):
                            merged_status.setdefault(
                                "chapter", event.payload.get("chapter", fallback_chapter)
                            )
                        event_type = "updated"
                        payload = merged_status

                if not event_type or payload is None:
                    continue
                event_at = event.at or job.updated_at or job.created_at or ""
                if best is None or event_at >= best[1]:
                    best = (event_type, event_at, payload)
        if best is None:
            return None
        return best[0], best[1], best[2]

    def _handle_memory_tasks_failed(self, project_id: str, payload: dict[str, Any]) -> None:
        """P0-5 (round 2): separate handler for memory subtask failures.

        Critical: this is NOT folded into ``_handle_memory_updated``.
        That method builds a status dict from payload fields it knows
        about (``indexed_chapters``, ``motifs``, …) and would silently
        drop ``tasks_failed``. We must keep the failure signal visible
        in the UI store so a red badge / warning can be rendered.
        """
        if project_id != self.current_project_id():
            return

        from novel_forge.desktop.state.store import get_ui_store

        store = get_ui_store()
        tasks_failed = list(payload.get("tasks_failed", []) or [])
        if not tasks_failed:
            return
        existing = store.memory_status(project_id) or {}
        store.set_memory_status(
            project_id,
            {
                **existing,
                "tasks_failed": tasks_failed,
                "tasks_ok": list(payload.get("tasks_ok", []) or []),
                "tasks_failed_chapter": int(payload.get("chapter", 0) or 0),
                "last_indexed_chapter": int(
                    payload.get("chapter", existing.get("last_indexed_chapter", 0) or 0)
                ),
            },
        )

    def _handle_memory_invalidated(self, project_id: str, payload: dict[str, Any]) -> None:
        if project_id != self.current_project_id():
            return

        from novel_forge.desktop.state.store import get_ui_store

        store = get_ui_store()
        existing = store.memory_status(project_id)
        if not existing or not existing.get("motifs"):
            return

        from_chapter = payload.get("from_chapter", 0) if isinstance(payload, dict) else 0
        if from_chapter <= 0:
            return

        kept = [m for m in existing["motifs"] if 0 < m.get("first_chapter", 0) < from_chapter]
        last_indexed = int(existing.get("last_indexed_chapter", 0) or 0)
        if last_indexed >= from_chapter:
            last_indexed = max(0, from_chapter - 1)
        indexed_chapters = int(existing.get("indexed_chapters", 0) or 0)
        if indexed_chapters > last_indexed:
            indexed_chapters = last_indexed
        store.set_memory_status(
            project_id,
            {
                **existing,
                "motifs": kept,
                "motif_suggestions": [],
                "repetition_warnings": [],
                "last_indexed_chapter": last_indexed,
                "indexed_chapters": indexed_chapters,
            },
        )

    def _handle_memory_updated(self, project_id: str, payload: dict[str, Any]) -> None:
        if project_id != self.current_project_id():
            return

        from novel_forge.desktop.state.store import get_ui_store

        store = get_ui_store()
        new_motifs = payload.get("motifs", [])

        if not new_motifs:
            existing = store.memory_status(project_id)
            if existing:
                incoming_last = payload.get("last_indexed_chapter", -1)
                existing_last = existing.get("last_indexed_chapter", 0)
                incoming_chapter = payload.get("chapter", 0)
                is_reindex_same_chapter = (
                    incoming_last == existing_last
                    and incoming_chapter > 0
                    and incoming_chapter == incoming_last
                )
                if (
                    incoming_last < 0 or incoming_last >= existing_last
                ) and not is_reindex_same_chapter:
                    new_motifs = existing.get("motifs", [])

        status = {
            "indexed_chapters": payload.get("indexed_chapters", 0),
            "motifs": new_motifs,
            "motif_suggestions": payload.get("motif_suggestions", []),
            "repetition_warnings": payload.get("repetition_warnings", []),
            "unresolved_questions": payload.get("unresolved_questions", []),
            "memory_module_status": payload.get("memory_module_status", {}),
            "cached_summaries": payload.get("cached_summaries", 0),
            "summary_hash_bound": payload.get("summary_hash_bound", 0),
            "chapter_hash_tracked": payload.get("chapter_hash_tracked", 0),
            "summary_stats": payload.get("summary_stats", {}),
            "last_indexed_chapter": payload.get("last_indexed_chapter", 0),
            "save_success": payload.get("save_success", False),
            "chapter": payload.get("chapter", 0),
            "outline_stats": payload.get("outline_stats", {}),
        }
        store.set_memory_status(project_id, status)

    def _update_repair_btn_state(self) -> None:
        repair_btn = self._memory_presenter.get_repair_button() if self._memory_presenter else None
        if repair_btn is None:
            return

        _status_priority = {
            DesktopJobState.RUNNING: 0,
            DesktopJobState.QUEUED: 1,
            DesktopJobState.PAUSED: 2,
            DesktopJobState.FAILED: 3,
            DesktopJobState.SUCCEEDED: 4,
        }
        repair_jobs = [
            j
            for j in self._jobs
            if j.kind
            in {"repair_issues", "repair_continuity", "repair_causal", "reevaluate_chapter"}
        ]
        repair_job = (
            min(repair_jobs, key=lambda j: _status_priority.get(j.status, 99))
            if repair_jobs
            else None
        )
        if repair_job is None:
            repair_btn.setText("修复选中")
            repair_btn.setEnabled(True)
        elif repair_job.status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED}:
            repair_btn.setText("评估中…" if repair_job.kind == "reevaluate_chapter" else "修复中…")
            repair_btn.setEnabled(False)
        elif repair_job.status == DesktopJobState.PAUSED:
            repair_btn.setText("修复选中")
            repair_btn.setEnabled(True)
        elif repair_job.status == DesktopJobState.FAILED:
            repair_btn.setText("修复选中")
            repair_btn.setEnabled(True)
        elif repair_job.status == DesktopJobState.SUCCEEDED:
            repair_btn.setText("修复选中")
            repair_btn.setEnabled(True)

    def _update_reevaluate_btn_state(self) -> None:
        reevaluate_btn = (
            self._memory_presenter.get_reevaluate_button() if self._memory_presenter else None
        )
        if reevaluate_btn is None:
            return

        _status_priority = {
            DesktopJobState.RUNNING: 0,
            DesktopJobState.QUEUED: 1,
            DesktopJobState.PAUSED: 2,
            DesktopJobState.FAILED: 3,
            DesktopJobState.SUCCEEDED: 4,
        }
        related_jobs = [
            j
            for j in self._jobs
            if j.kind
            in {"reevaluate_chapter", "repair_issues", "repair_continuity", "repair_causal"}
        ]
        active_job = (
            min(related_jobs, key=lambda j: _status_priority.get(j.status, 99))
            if related_jobs
            else None
        )
        if active_job is None:
            reevaluate_btn.setText("重新评估")
            reevaluate_btn.setEnabled(True)
        elif active_job.status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED}:
            reevaluate_btn.setText(
                "评估中…" if active_job.kind == "reevaluate_chapter" else "修复中…"
            )
            reevaluate_btn.setEnabled(False)
        else:
            reevaluate_btn.setText("重新评估")
            reevaluate_btn.setEnabled(True)

    def _update_reextract_btn_state(self) -> None:
        extract_btn = (
            self._memory_panel_widget.get_relations_extract_button()
            if hasattr(self, "_memory_panel_widget")
            else None
        )
        if extract_btn is None:
            return
        reextract_job = next(
            (j for j in self._jobs if j.kind == "reextract_relationships"),
            None,
        )
        if reextract_job is None:
            extract_btn.setText("重新提取关系")
            extract_btn.setEnabled(True)
            self._memory_panel_widget.update_relations_extract_hint("")
        elif reextract_job.status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED}:
            extract_btn.setText("提取中…")
            extract_btn.setEnabled(False)
            scope = ""
            if reextract_job.label:
                import re as _re

                m = _re.search(r"\((.+?)\)", reextract_job.label)
                if m:
                    scope = m.group(1)
            hint = f"正在重新提取关系（{scope}）…" if scope else "正在重新提取关系…"
            self._memory_panel_widget.update_relations_extract_hint(hint)
        elif reextract_job.status == DesktopJobState.SUCCEEDED:
            result = reextract_job.result or {}
            processed = result.get("chapters_processed", 0)
            total_rels = result.get("total_relationships", 0)
            extract_btn.setText("重新提取关系")
            extract_btn.setEnabled(True)
            self._memory_panel_widget.update_relations_extract_hint(
                f"已完成：处理 {processed} 章，提取 {total_rels} 组关系"
            )
        elif reextract_job.status == DesktopJobState.FAILED:
            extract_btn.setText("重新提取关系")
            extract_btn.setEnabled(True)
            self._memory_panel_widget.update_relations_extract_hint(
                "提取失败，请重试", is_warning=True
            )

    def _update_motif_repair_btn_state(self) -> None:
        repair_btn = (
            self._memory_panel_widget.get_motif_repair_button()
            if hasattr(self, "_memory_panel_widget")
            else None
        )
        if repair_btn is None:
            return

        motif_job = next((j for j in self._jobs if j.kind == "repair_motif_history"), None)
        if motif_job is None:
            repair_btn.setText("修补母题历史")
            repair_btn.setEnabled(True)
            self._memory_panel_widget.update_motif_repair_hint("")
            return

        if motif_job.status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED}:
            repair_btn.setText("修补中…")
            repair_btn.setEnabled(False)
            self._memory_panel_widget.update_motif_repair_hint("正在修补母题历史…")
            return

        repair_btn.setText("修补母题历史")
        repair_btn.setEnabled(True)
        if motif_job.status == DesktopJobState.SUCCEEDED:
            result = motif_job.result or {}
            layer1 = result.get("layer1", result)
            touched = int(layer1.get("motifs_touched", 0) or 0)
            seen = int(layer1.get("occurrences_seen", 0) or 0)
            created = int(layer1.get("created_motifs", 0) or 0)
            merged = int(layer1.get("extraction_cache_merged", 0) or 0)
            parts = [
                f"回填母题 {touched} 个，统计片段 {seen} 条，新增母题 {created} 个，补并缓存章 {merged} 章"
            ]

            layer2 = result.get("layer2", {})
            if layer2 and not layer2.get("skipped"):
                l2_processed = int(layer2.get("chapters_processed", 0) or 0)
                l2_skipped = int(layer2.get("chapters_skipped", 0) or 0)
                l2_extracted = int(layer2.get("motifs_extracted", 0) or 0)
                l2_empty = int(layer2.get("chapters_empty", 0) or 0)
                l2_missing = int(layer2.get("chapters_missing", 0) or 0)
                l2_failed = int(layer2.get("chapters_failed", 0) or 0)
                detail = f"重新提取 {l2_processed} 章（跳过 {l2_skipped} 章）"
                extra_bits = [f"新增母题片段 {l2_extracted} 条"]
                if l2_empty:
                    extra_bits.append(f"空结果 {l2_empty} 章")
                if l2_missing:
                    extra_bits.append(f"缺文件 {l2_missing} 章")
                if l2_failed:
                    extra_bits.append(f"失败 {l2_failed} 章")
                parts.append(detail + "，" + "，".join(extra_bits))

            self._memory_panel_widget.update_motif_repair_hint(
                "修补完成：" + "；".join(parts) + "。"
            )

            # Refresh memory panel to show newly extracted motifs.
            # The repair job updates disk data but does NOT emit memory_updated
            # events, so the panel would otherwise show stale data until the
            # user navigates to another chapter.
            render_panel = getattr(self, "_render_memory_panel", None)
            if callable(render_panel):
                render_panel()
        elif motif_job.status == DesktopJobState.FAILED:
            self._memory_panel_widget.update_motif_repair_hint(
                "修补失败，请重试。",
                is_warning=True,
            )
        else:
            self._memory_panel_widget.update_motif_repair_hint("")

    def _compute_audit_state(self) -> tuple[AuditState, int]:
        """Derive AuditState and progress percentage from book_consistency job events.

        Returns (AuditState, progress_0_to_100).
        """
        audit_jobs = [j for j in self._book_level_jobs if j.kind == "book_consistency"]
        if not audit_jobs:
            return AuditState.IDLE, 0

        # Use the most recent audit job.
        job = max(audit_jobs, key=lambda j: j.updated_at or "")
        if job.status == DesktopJobState.SUCCEEDED:
            return AuditState.COMPLETE, 100
        if job.status == DesktopJobState.FAILED:
            return AuditState.FAILED, 0
        if job.status not in {DesktopJobState.RUNNING, DesktopJobState.QUEUED}:
            return AuditState.IDLE, 0

        # Determine phase from current_step.
        step = job.current_step or ""
        resolved_step = resolve_step_key("book_consistency", step)
        progress = compute_job_progress(job)

        # Map step to audit phase.
        if resolved_step.startswith("book_consistency_repair_"):
            return AuditState.REPAIRING, progress
        # book_consistency_start, book_consistency, book_consistency_report_written
        # all fall under AUDITING.
        return AuditState.AUDITING, progress

    def _latest_book_consistency_job(self) -> DesktopJobRecord | None:
        audit_jobs = [j for j in self._book_level_jobs if j.kind == "book_consistency"]
        if not audit_jobs:
            return None
        return max(audit_jobs, key=lambda j: j.updated_at or "")

    def _audit_running_tooltip(
        self,
        job: DesktopJobRecord | None,
        *,
        progress: int,
        fallback: str,
    ) -> str:
        if job is None:
            return fallback
        detail = display_step_name_for_job(job)
        if not detail:
            return fallback
        return f"{detail}\n当前进度：{progress}%"

    def _update_book_level_btn_state(self) -> None:
        book_active = any(
            j.status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
            for j in self._book_level_jobs
        )
        audit_state, audit_progress = self._compute_audit_state()
        export_running = any(
            j.kind == "export_book"
            and j.status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
            for j in self._book_level_jobs
        )
        if hasattr(self, "_consistency_tool_btn"):
            audit_job = self._latest_book_consistency_job()
            self._consistency_tool_btn.setEnabled(
                not book_active or audit_state == AuditState.FAILED
            )
            if audit_state == AuditState.IDLE:
                self._consistency_tool_btn.setText("📋 全书审计")
                self._consistency_tool_btn.setToolTip(
                    "检查全书一致性：命名、时间线、世界观、角色状态等，可选择审计范围"
                )
            elif audit_state == AuditState.AUDITING:
                self._consistency_tool_btn.setText(f"📋 审计 {audit_progress}%")
                self._consistency_tool_btn.setToolTip(
                    self._audit_running_tooltip(
                        audit_job,
                        progress=audit_progress,
                        fallback="全书审计正在运行。",
                    )
                )
            elif audit_state == AuditState.VERIFYING:
                self._consistency_tool_btn.setText(f"📋 验证 {audit_progress}%")
                self._consistency_tool_btn.setToolTip(
                    self._audit_running_tooltip(
                        audit_job,
                        progress=audit_progress,
                        fallback="正在验证全书审计命中的问题。",
                    )
                )
            elif audit_state == AuditState.REPAIRING:
                self._consistency_tool_btn.setText(f"📋 修复 {audit_progress}%")
                self._consistency_tool_btn.setToolTip(
                    self._audit_running_tooltip(
                        audit_job,
                        progress=audit_progress,
                        fallback="正在按全书审计结果执行定向修复。",
                    )
                )
            elif audit_state == AuditState.COMPLETE:
                self._consistency_tool_btn.setText("📋 再次审计")
                self._consistency_tool_btn.setToolTip(
                    "上次审计已完成。点击可重新审计，或在设置窗口中选择续修上次结果。"
                )
            elif audit_state == AuditState.FAILED:
                self._consistency_tool_btn.setText("📋 审计失败")
                self._consistency_tool_btn.setToolTip("上次全书审计失败。点击可重新发起审计。")
        if hasattr(self, "_export_tool_btn"):
            self._export_tool_btn.setEnabled(not book_active)
            if export_running:
                self._export_tool_btn.setText("📤 导出中…")
            else:
                self._export_tool_btn.setText("📤 导出")
