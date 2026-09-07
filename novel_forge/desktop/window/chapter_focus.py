"""Mixin module: chapter_focus methods for ``NovelForgeDesktopWindow``.

Auto-extracted in the M3.1 giant-file-split refactor. Methods live here so the
main :file:`window/__init__.py` facade can compose them via mixin inheritance.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import (
    QTimer,
)

import novel_forge.desktop.page_registrations as _page_registrations  # noqa: F401

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


from novel_forge.desktop.window._runnables import (  # noqa: E402
    _ChapterContextRefreshRunnable,
)


def _desktop_thread_pools() -> Any:
    import novel_forge.desktop.window as window_facade

    return window_facade.desktop_thread_pools()


class ChapterFocusMixin:
    """Mixin that contributes the **chapter_focus** method group."""

    def _focus_workflow(self, project_id: str, chapter_number: int) -> None:
        if (
            project_id.strip()
            and self._snapshot is not None
            and self._snapshot.details.get(project_id) is not None
            and self._snapshot.details[project_id].mode == "long"
        ):
            if self._snapshot.details[project_id].init_resume_available:
                self._focus_long_init_project(project_id)
                return
            self._focus_chapter_studio(project_id, chapter_number)
            return
        self.switch_page("workflow")
        self._pages["workflow"].focus_short_create()

    def _focus_long_init_project(self, project_id: str) -> None:
        self.switch_page("workflow")
        self._pages["workflow"].focus_long_init_project(project_id)

    def _clamp_chapter_studio_target(self, project_id: str, chapter_number: int) -> tuple[str, int]:
        """Normalize chapter-studio target and clamp chapter to outline range."""
        target_project_id = project_id.strip()
        target_chapter = max(1, chapter_number)
        if (
            target_project_id
            and self._snapshot is not None
            and (detail := self._snapshot.details.get(target_project_id)) is not None
            and detail.mode == "long"
        ):
            raw_total_chapters = getattr(detail, "total_chapters", 0)
            try:
                total_chapters = (
                    int(raw_total_chapters or 0)
                    if isinstance(raw_total_chapters, (int, float, str))
                    else 0
                )
            except (TypeError, ValueError):
                total_chapters = 0
            if total_chapters > 0:
                target_chapter = min(target_chapter, total_chapters)
        return target_project_id, target_chapter

    def _focus_chapter_studio(self, project_id: str, chapter_number: int) -> None:
        clamped = self._clamp_chapter_studio_target(project_id, chapter_number)
        target_project_id, target_chapter = clamped
        self._chapter_studio_project_id = target_project_id
        self._set_chapter_number(target_project_id, target_chapter)
        # Carry the target through the post-switch deep-link path.  Indexing
        # _pages here would synchronously instantiate the cold page and defeat
        # ChapterStudioPage's staged construction.
        self.switch_page(f"chapter_studio:{target_project_id}:{target_chapter}")

    def _focus_chapter_studio_with_autorun(self, project_id: str, chapter_number: int) -> None:
        """Navigate to chapter_studio and activate book_auto mode to start chapter generation."""
        clamped = self._clamp_chapter_studio_target(project_id, chapter_number)
        target_project_id, target_chapter = clamped
        self._chapter_studio_project_id = target_project_id
        self._set_chapter_number(target_project_id, target_chapter)
        self.switch_page("chapter_studio")
        self._chapter_studio_project_id = target_project_id
        self._set_chapter_number(target_project_id, target_chapter)
        studio = self._pages["chapter_studio"]
        studio.focus_project(target_project_id, target_chapter)
        # Activate book_auto mode and start auto-pilot
        self._reset_project_autorun_transients(target_project_id)
        studio.set_mode(studio.MODE_BOOK_AUTO)
        studio.start_auto_pilot()
        self.show_priority_status("立项完成，已启动章节连跑", 5000, self._STATUS_SUCCESS)

    def _auto_advance_chapter(self, project_id: str, chapter_number: int) -> None:
        """Auto-pilot: switch to next chapter and trigger prepare."""
        studio = self._pages["chapter_studio"]
        previous_chapter = self._get_autorun_chapter_number(project_id)
        apply_cooldown = False
        if hasattr(studio, "current_project_id") and studio.current_project_id() == project_id:
            current_done = getattr(studio, "_is_current_chapter_done", None)
            if callable(current_done):
                apply_cooldown = bool(current_done()) and chapter_number > previous_chapter
        clamped = self._clamp_chapter_studio_target(project_id, chapter_number)
        self._set_autorun_chapter_number(clamped[0], clamped[1])
        self._set_chapter_number(clamped[0], clamped[1])
        if (
            studio.should_follow_autorun_for_project(clamped[0])
            if hasattr(studio, "should_follow_autorun_for_project")
            else studio.should_follow_autorun()
        ):
            self._chapter_studio_project_id = clamped[0]
            studio.focus_project(
                self._chapter_studio_project_id,
                self._get_chapter_number(self._chapter_studio_project_id),
            )
        self.show_priority_status(
            f"全自动：推进到第 {self._get_autorun_chapter_number(project_id)} 章",
            3000,
            self._STATUS_SUCCESS,
        )
        cooldown = self._autorun_cooldown_seconds() if apply_cooldown else 0
        if cooldown > 0:
            self._autorun_cooldown_until[project_id] = time.monotonic() + cooldown
        QTimer.singleShot(
            cooldown * 1000 if cooldown > 0 else 0,
            lambda pid=project_id: self._drive_project_autorun(pid),
        )

    def _on_stop_auto_pilot(self, job_id: str, reason: str) -> None:
        """Cancel the running job and show feedback in the status bar."""
        job = next((item for item in self._job_manager.jobs() if item.job_id == job_id), None)
        stopped_autorun = self._stop_project_autorun_before_cancel(
            job.project_id if job is not None else "",
            reason,
        )
        self._job_manager.cancel_job(job_id, reason=reason)
        # Determine message and priority based on whether auto-pilot was active
        studio = self._pages.get("chapter_studio")
        if stopped_autorun or (studio is not None and studio.stopped_from_auto):
            msg = "全自动已停止，当前任务已取消，可点《继续全自动》恢复"
            priority = self._STATUS_ERROR
        else:
            msg = "任务已取消"
            priority = self._STATUS_INFO
        self.show_priority_status(msg, 5000, priority)

    def _open_or_prefill_chapter_studio_from_workflow(self) -> None:
        workflow_page = self._pages["workflow"]
        project_id = workflow_page.current_project_id().strip()
        if not project_id:
            workflow_page.prefill_latest_project()
            project_id = workflow_page.current_project_id().strip()
        if project_id:
            self._focus_chapter_studio(
                project_id,
                workflow_page.current_chapter_number() or 1,
            )

    def _bind_chapter_studio_context(self, project_id: str, chapter_number: int) -> None:
        target_project = project_id.strip()
        target_chapter = chapter_number
        if target_project and target_project != self._chapter_studio_project_id:
            if target_project in self._chapter_studio_chapter_numbers:
                target_chapter = self._get_chapter_number(target_project)
            elif self._snapshot is not None and target_project in self._snapshot.details:
                detail = self._snapshot.details[target_project]
                target_chapter = detail.completed_chapters + 1 if detail.completed_chapters else 1
        clamped = self._clamp_chapter_studio_target(target_project, target_chapter)
        self._chapter_studio_project_id = clamped[0]
        self._set_chapter_number(clamped[0], clamped[1])
        self._sync_chapter_studio_page_chapter(clamped[0], clamped[1])
        self._refresh_chapter_studio_context()
        if self._ui_session_restored:
            self._schedule_ui_session_save()

    def _refresh_chapter_studio_context(self) -> None:
        # RuntimeServices may still be building on the ui_io pool (B2 async
        # init). Without a workspace we cannot read chapter snapshots.
        if self._workspace is None:
            return
        chapter_page = self._pages.get("chapter_studio")
        if chapter_page is None:
            self._chapter_context_refresh_key = None
            return
        if not self._chapter_studio_project_id or self._snapshot is None:
            self._chapter_context_refresh_key = None
            chapter_page.bind_studio(None)
            return
        project_detail = self._snapshot.details.get(self._chapter_studio_project_id)
        if project_detail is None or project_detail.mode != "long":
            self._chapter_context_refresh_key = None
            chapter_page.bind_studio(None)
            return

        target_chapter = self._get_chapter_number(self._chapter_studio_project_id)
        self._sync_chapter_studio_page_chapter(self._chapter_studio_project_id, target_chapter)
        refresh_key = (
            self._workspace_revision,
            self._chapter_studio_project_id,
            target_chapter,
        )
        if self._chapter_context_refresh_key == refresh_key:
            return
        self._chapter_context_refresh_key = refresh_key

        # Always offload ~12-file disk I/O to a worker thread so the GUI event
        # loop is never blocked (50-200 ms on slow media).  bind_studio() guards
        # against out-of-order / stale snapshots, so the async path is safe in
        # both manual and auto-pilot modes.
        #
        # Cap concurrent context refresh workers to avoid starving the thread
        # pool (auto-pilot ticker fires every 6 s and each workspace change
        # also triggers a refresh).
        _CONTEXT_REFRESH_WORKER_CAP = 3
        if len(self._active_context_refresh_workers) >= _CONTEXT_REFRESH_WORKER_CAP:
            return
        worker = _ChapterContextRefreshRunnable(
            self._workspace,
            self._chapter_studio_project_id,
            target_chapter,
            project_detail,
        )
        self._active_context_refresh_workers.add(worker)
        worker.signals.finished.connect(
            lambda snapshot, worker=worker: self._on_chapter_context_refreshed(worker, snapshot)
        )
        worker.signals.failed.connect(
            lambda project_id, chapter_number, worker=worker: (
                self._on_chapter_context_refresh_failed(
                    worker,
                    project_id,
                    chapter_number,
                )
            )
        )
        _desktop_thread_pools().ui_io_pool.start(worker)

    def _on_chapter_context_refreshed(
        self,
        worker: _ChapterContextRefreshRunnable,
        snapshot: object,
    ) -> None:
        """Called on main thread after background chapter context refresh completes."""
        if worker not in self._active_context_refresh_workers:
            return
        self._active_context_refresh_workers.discard(worker)
        project_id = getattr(worker, "_project_id", "")
        chapter_number = int(getattr(worker, "_chapter_number", 0) or 0)
        if project_id and project_id != self._chapter_studio_project_id:
            return
        if (
            project_id
            and chapter_number > 0
            and chapter_number != self._get_chapter_number(project_id)
        ):
            return
        chapter_page = self._pages.get("chapter_studio")
        if chapter_page is not None:
            chapter_page.bind_studio(snapshot)

    def _on_chapter_context_refresh_failed(
        self,
        worker: _ChapterContextRefreshRunnable,
        project_id: str = "",
        chapter_number: int = 0,
    ) -> None:
        """Called on main thread when background chapter context refresh fails."""
        if worker not in self._active_context_refresh_workers:
            return
        self._active_context_refresh_workers.discard(worker)
        if project_id and project_id != self._chapter_studio_project_id:
            return
        if (
            project_id
            and chapter_number > 0
            and chapter_number != self._get_chapter_number(project_id)
        ):
            return
        chapter_page = self._pages.get("chapter_studio")
        if chapter_page is not None:
            chapter_page.bind_studio(None)
