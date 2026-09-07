"""Mixin module: dispatch methods for ``NovelForgeDesktopWindow``.

Auto-extracted in the M3.1 giant-file-split refactor. Methods live here so the
main :file:`window/__init__.py` facade can compose them via mixin inheritance.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import novel_forge.desktop.page_registrations as _page_registrations  # noqa: F401
from novel_forge.desktop.jobs import (
    DesktopJobRecord,
    DesktopJobState,
)
from novel_forge.desktop.strings import UIStrings
from novel_forge.desktop.widgets import (
    show_warning_message,
)
from novel_forge.workspace.contracts import (
    InitLongRequest,
    RebuildMemoryVectorsRequest,
)

_logger = logging.getLogger(__name__)

_WINDOW_DEFAULT_MIN_WIDTH = 1180
_WINDOW_DEFAULT_MIN_HEIGHT = 760
_WINDOW_DEFAULT_START_WIDTH = 1440
_WINDOW_DEFAULT_START_HEIGHT = 900
_WINDOW_SCREEN_WIDTH_RATIO = 0.92
_WINDOW_SCREEN_HEIGHT_RATIO = 0.90
_COMPACT_WIDTH_THRESHOLD = 1360
_COMPACT_HEIGHT_THRESHOLD = 820

if TYPE_CHECKING:
    pass


class DispatchMixin:
    """Mixin that contributes the **dispatch** method group."""

    def _dispatch_job(self, request: object) -> None:
        """Type-check *request* and call the matching job-manager method."""
        expected_kind_by_submitter = {
            "submit_run_chapter": "run_chapter",
            "submit_prepare_chapter": "prepare_chapter",
            "submit_polish_chapter": "polish_chapter",
            "submit_sync_chapter_contracts": "sync_chapter_contracts",
            "submit_extend_outline": "extend_outline",
        }
        for req_type, method_name, status_msg in self._JOB_DISPATCH:
            if isinstance(request, req_type):
                record = getattr(self._job_manager, method_name)(request, mock=self._mock_enabled)
                # Some submitters may return an already-active conflicting job
                # instead of creating a new one; in that path job_submitted is
                # not emitted, so bind explicitly to keep the studio from
                # looking idle while auto-run is waiting on an existing
                # chapter writer.
                self._bind_jobs()
                expected_kind = expected_kind_by_submitter.get(method_name)
                if (
                    expected_kind
                    and isinstance(record, DesktopJobRecord)
                    and record.kind != expected_kind
                    and record.status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
                ):
                    self.show_priority_status(
                        f"等待已有写任务完成：{record.label}",
                        5000,
                        self._STATUS_WARNING,
                    )
                    return
                self.show_priority_status(status_msg, 3000, self._STATUS_INFO)
                return

    def _submit_rebuild_memory_vectors(self, project_id: str) -> None:
        project_id = str(project_id or "").strip()
        if not project_id:
            return
        self._dispatch_job(
            RebuildMemoryVectorsRequest(
                project_id=project_id,
                include_expression=True,
            )
        )

    def _dispatch_init_long_autorun(self, request: object) -> None:
        """Dispatch init_long and track job id for book_auto activation after completion."""
        if not isinstance(request, InitLongRequest):
            self._dispatch_job(request)
            return

        record = self._job_manager.submit_init_long(request, mock=self._mock_enabled)
        self._pending_autorun_job_ids.add(record.job_id)
        self.show_priority_status(UIStrings.JOB_SUBMIT_INIT_LONG, 3000, self._STATUS_INFO)

    def _cancel_init_job(self, job_id: str) -> None:
        self._job_manager.cancel_job(job_id, reason=UIStrings.JOB_CANCEL_INIT)
        self.show_priority_status(UIStrings.JOB_CANCEL_INIT_MSG, 5000, self._STATUS_INFO)

    def _clear_chapter_task_flow(self, project_id: str, job_ids: object) -> None:
        ids = []
        if isinstance(job_ids, list):
            ids = [str(item).strip() for item in job_ids if str(item).strip()]
        if not project_id or not ids:
            self.show_priority_status(UIStrings.JOB_NO_CLEANUP, 3_500, self._STATUS_INFO)
            return
        removed = self._job_manager.clear_jobs(ids, project_id=project_id)
        if removed > 0:
            self.show_priority_status(
                UIStrings.JOB_CLEANED_UP.format(count=removed), 4_000, self._STATUS_INFO
            )
        else:
            self.show_priority_status(UIStrings.JOB_NO_CLEANUP, 3_500, self._STATUS_INFO)

    def _clear_chapter_range_task_flow(self, project_id: str, from_chapter: int) -> None:
        """Called when stale chapters are deleted — also purge their task-flow history."""
        removed = self._job_manager.clear_jobs_for_chapter_range(project_id, from_chapter)
        if removed > 0:
            self.show_priority_status(
                UIStrings.JOB_CLEANED_UP_CHAPTER.format(chapter=from_chapter, count=removed),
                4_000,
                self._STATUS_INFO,
            )

    def _clear_workflow_task_flow(self, job_ids: object) -> None:
        ids = []
        if isinstance(job_ids, list):
            ids = [str(item).strip() for item in job_ids if str(item).strip()]
        if not ids:
            self.show_priority_status(UIStrings.JOB_NO_CLEANUP, 3_500, self._STATUS_INFO)
            return
        removed = self._job_manager.clear_jobs(ids)
        if removed > 0:
            self.show_priority_status(
                UIStrings.JOB_CLEANED_UP.format(count=removed), 4_000, self._STATUS_INFO
            )
        else:
            self.show_priority_status(UIStrings.JOB_NO_CLEANUP, 3_500, self._STATUS_INFO)

    def _mark_task_flow_error_logs_resolved(self, resolved_by_job: object) -> None:
        changed = self._job_manager.mark_task_flow_errors_resolved(resolved_by_job)
        if changed > 0:
            self.show_priority_status("错误日志处理状态已保存。", 3_500, self._STATUS_INFO)

    def _retry_init_repair(
        self,
        job_id: str,
        project_id: str,
        reset_repair_history: bool,
    ) -> None:
        project_value = str(project_id or "").strip()
        if not project_value:
            show_warning_message(self, "无法提交修复", "缺少项目 ID。")
            return
        try:
            record = self._job_manager.submit_init_repair_retry(
                project_value,
                reset_repair_history=bool(reset_repair_history),
                mock=self._mock_enabled,
            )
        except Exception as exc:  # noqa: BLE001 - show actionable desktop error
            show_warning_message(self, "无法提交修复", str(exc))
            return
        self._bind_jobs()
        if (
            record.kind == "init_long"
            and record.status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
            and record.job_id != job_id
        ):
            action = "AI修复" if reset_repair_history else "人工修复复审"
            self.show_priority_status(
                f"{action}任务已提交：{record.label}", 4_000, self._STATUS_INFO
            )
            return
        self.show_priority_status(
            f"等待已有写任务完成：{record.label}",
            5_000,
            self._STATUS_WARNING,
        )

    def _clear_restarted_init_task_flow(self, project_id: str) -> None:
        project_value = str(project_id or "").strip()
        if not project_value:
            return
        purged_job_ids = self._job_manager.clear_restarted_init_jobs(project_value)
        if purged_job_ids:
            self._pending_autorun_job_ids.difference_update(purged_job_ids)
        self.show_priority_status(
            f"项目「{project_value}」已清除旧立项数据，可重新提交立项。",
            5_000,
            self._STATUS_SUCCESS,
        )

    def _handle_job_completed(self, job_id: str) -> None:
        self._job_lock.lock()
        try:
            # For repair jobs, also force a chapter context refresh to ensure scores update
            job = next((item for item in self._job_manager.jobs() if item.job_id == job_id), None)
            if job is not None and job.kind in {
                "repair_continuity",
                "repair_causal",
                "repair_issues",
            }:
                self._refresh_chapter_studio_context()
            job = next((item for item in self._job_manager.jobs() if item.job_id == job_id), None)
            if job is None:
                return
            self._notification_sounds.play_for_job(job)
            if job.kind == "sync_chapter_contracts":
                projects_page = self._pages.get("projects")
                if projects_page is not None:
                    if hasattr(projects_page, "notify_outline_sync_finished"):
                        projects_page.notify_outline_sync_finished(
                            project_id=job.project_id,
                            status=job.status.value,
                            result=job.result,
                            error=job.error,
                        )
                    elif hasattr(projects_page, "set_outline_sync_enabled"):
                        projects_page.set_outline_sync_enabled(True)
                if job.status == DesktopJobState.SUCCEEDED:
                    self.show_priority_status(
                        UIStrings.JOB_COMPLETED.format(label=job.label),
                        4000,
                        self._STATUS_SUCCESS,
                    )
                elif job.status == DesktopJobState.PAUSED:
                    self.show_priority_status(f"等待决策：{job.label}", 4000, self._STATUS_WARNING)
                elif job.error != "用户已取消":
                    self.show_priority_status(f"任务失败：{job.label}", 5000, self._STATUS_ERROR)
                return
            if job.kind == "extend_outline":
                projects_page = self._pages.get("projects")
                if projects_page is not None and hasattr(
                    projects_page, "notify_outline_extend_finished"
                ):
                    projects_page.notify_outline_extend_finished(
                        project_id=job.project_id,
                        status=job.status.value,
                        result=job.result,
                        error=job.error,
                    )
                if job.status == DesktopJobState.SUCCEEDED:
                    if job.project_id:
                        self._schedule_workspace_refresh(
                            "details",
                            project_id=job.project_id,
                            reason="extend_outline_completed",
                        )
                    result_status = str((job.result or {}).get("status") or "").lower()
                    if result_status == "partial":
                        self.show_priority_status(
                            "大纲已延长，章节契约同步需重试", 5000, self._STATUS_WARNING
                        )
                    else:
                        self.show_priority_status(
                            UIStrings.JOB_COMPLETED.format(label=job.label),
                            4000,
                            self._STATUS_SUCCESS,
                        )
                elif job.status == DesktopJobState.PAUSED:
                    self.show_priority_status(f"等待决策：{job.label}", 4000, self._STATUS_WARNING)
                elif job.error != "用户已取消":
                    self.show_priority_status(f"任务失败：{job.label}", 5000, self._STATUS_ERROR)
                return
            # Check if this is an init_long that should trigger book_auto.
            # Track by job_id so auto-generated project_id (when request.project_id is empty)
            # still triggers correctly.
            _was_pending_autorun_job = job.job_id in self._pending_autorun_job_ids
            if _was_pending_autorun_job and job.kind == "init_long":
                self._pending_autorun_job_ids.discard(job.job_id)
            _should_trigger_autorun = (
                job.kind == "init_long"
                and job.status == DesktopJobState.SUCCEEDED
                and bool(job.project_id)
                and _was_pending_autorun_job
            )
            if (
                job.project_id
                and self._snapshot is not None
                and job.project_id in self._snapshot.details
            ):
                detail = self._snapshot.details[job.project_id]
                next_chapter = detail.completed_chapters + 1 if detail.mode == "long" else 1
                if detail.mode == "long":
                    chapter_number = int(
                        (job.result or {}).get("chapter_number", next_chapter) or next_chapter
                    )
                    result_status = str((job.result or {}).get("status", "") or "").lower()
                    completed_write = (
                        job.status == DesktopJobState.SUCCEEDED
                        and job.kind
                        in {
                            "resolve_chapter_checkpoint_finalize",
                            "run_chapter",
                        }
                        and result_status in {"completed", "done"}
                    )
                    if completed_write:
                        next_chapter = max(next_chapter, chapter_number + 1)
                        total_chapters = int(getattr(detail, "total_chapters", 0) or 0)
                        if total_chapters > 0:
                            next_chapter = min(next_chapter, total_chapters)
                    workflow_page = self._pages.get("workflow")
                    if workflow_page is not None:
                        workflow_page.focus_project(job.project_id, next_chapter)
                    # Navigate to chapter_studio ONLY on success.
                    # For failed jobs (e.g., init_long), stay on workflow page so user can retry.
                    if job.status == DesktopJobState.SUCCEEDED:
                        if _should_trigger_autorun:
                            # init_long succeeded with autorun flag — navigate to chapter_studio with book_auto
                            self._focus_chapter_studio_with_autorun(job.project_id, chapter_number)
                        elif (
                            job.kind
                            in {
                                "resolve_chapter_checkpoint",
                                "resolve_chapter_checkpoint_finalize",
                                "run_chapter",
                            }
                            and next_chapter > chapter_number
                        ):
                            # If a chapter job succeeded (archive accepted), auto-advance to next chapter.
                            # Exception: when auto-pilot is running, the chapter advance is managed by
                            # _try_auto_action (which first waits for continuity repair to complete).
                            # Jumping ahead here would race with the auto-repair and submit concurrent jobs.
                            _studio_page = self._pages.get("chapter_studio")
                            _auto_running = (
                                _studio_page is not None and _studio_page.needs_job_binding()
                            )
                            if not _auto_running:
                                self._focus_chapter_studio(job.project_id, next_chapter)
                            elif _studio_page is not None and (
                                _studio_page.should_follow_autorun_for_project(job.project_id)
                                if hasattr(_studio_page, "should_follow_autorun_for_project")
                                else _studio_page.should_follow_autorun()
                            ):
                                # Auto-pilot active + follow_autorun enabled → navigate UI to current chapter
                                self._focus_chapter_studio(job.project_id, chapter_number)
                            # else: auto-pilot active, follow_autorun=False → skip UI navigation
                        else:
                            # Other successful jobs (including init_long without autorun) navigate to chapter_studio.
                            self._focus_chapter_studio(job.project_id, chapter_number)
                else:
                    workflow_page = self._pages.get("workflow")
                    if workflow_page is not None:
                        workflow_page.focus_project(job.project_id, next_chapter)
            if job.status == DesktopJobState.SUCCEEDED:
                if job.kind == "export_book":
                    self._notify_export_complete(job)
                    return
                if job.kind == "book_consistency":
                    self._notify_book_consistency_complete(job)
                    return
                self.show_priority_status(
                    UIStrings.JOB_COMPLETED.format(label=job.label), 4000, self._STATUS_SUCCESS
                )
                # NOTE: Do NOT clear _last_submitted_checkpoint_id here.
                # The guard persists across bind_studio() calls to prevent
                # double-submission of the SAME checkpoint across rebind/reload cycles.
                # The guard is ONLY cleared when the user explicitly dismisses a checkpoint.
                return
            if job.status == DesktopJobState.PAUSED:
                self.show_priority_status(f"等待决策：{job.label}", 4000, self._STATUS_WARNING)
                return
            # Cancelled jobs that completed in the background trigger a silent
            # studio refresh (job_completed was emitted by _handle_finished after
            # the asyncio task finished naturally).  Do not show "任务失败" in the
            # status bar for those — the user already saw the cancel feedback.
            if job.error == "用户已取消":
                return
            self.show_priority_status(f"任务失败：{job.label}", 5000, self._STATUS_ERROR)
        finally:
            self._job_lock.unlock()
            self._safe_deferred(0, self._drive_active_autoruns)
