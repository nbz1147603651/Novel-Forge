"""Auto-pilot logic for ChapterStudioPage.

Contains the ChapterStudioAutoMixin for:
- Auto-pilot action dispatching and timeout checking
- Auto-resolve, auto-advance, auto-prepare
- Auto-repair submission
- Stale timer validation
- Mode switching and retry scheduling
"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QSignalBlocker, QTimer

from novel_forge.desktop.constants import CONTEXT_REQUEST_DEBOUNCE_MS
from novel_forge.desktop.jobs import DesktopJobState
from novel_forge.desktop.workflow_requests import (
    build_repair_continuity_request,
    build_repair_issues_request,
)
from novel_forge.workspace.contracts import DecisionOption

from .contract import ChapterStudioMixinBase

_logger = logging.getLogger(__name__)


class ChapterStudioAutoMixin(ChapterStudioMixinBase):
    """Mixin providing auto-pilot decision execution and timeout management."""

    def _try_auto_action(self) -> None:
        if getattr(self, "_engine_owned_autorun", False):
            # JobService owns checkpoint selection, retry budgets and
            # cross-chapter advancement.  The page only renders its state.
            return
        from .autorun import decide_autopilot_action

        if self._auto_pilot_pending:
            return

        if self._state.user_chapter_nav:
            self._state.user_chapter_nav = False
            self._user_nav_ctx_pending = True
            return

        if self._studio is not None and self._studio.project_id != self.current_project_id():
            return

        decision = decide_autopilot_action(self._build_autopilot_context())
        if getattr(self, "_user_nav_ctx_pending", False):
            self._user_nav_ctx_pending = False
        if decision.action == "none":
            return
        if decision.action == "refresh_context":
            self._auto_refresh_count += 1
            if (
                self._auto_refresh_count == self.MAX_AUTO_REFRESH_ATTEMPTS + 1
                or self._auto_refresh_count % 20 == 0
            ):
                window = self.window()
                if hasattr(window, "show_priority_status"):
                    window.show_priority_status(
                        "章节连跑正在等待工作区同步，已保持后台重试。",
                        6_000,
                        2,  # _STATUS_WARNING
                    )
            try:
                self.workspace_refresh_requested.emit()
            except RuntimeError:
                _logger.debug("Auto-pilot workspace refresh signal failed", exc_info=True)
            self._context_request_timer.stop()
            delay_ms = CONTEXT_REQUEST_DEBOUNCE_MS
            if self._auto_refresh_count > self.MAX_AUTO_REFRESH_ATTEMPTS:
                overdue = self._auto_refresh_count - self.MAX_AUTO_REFRESH_ATTEMPTS
                delay_ms = min(5_000, CONTEXT_REQUEST_DEBOUNCE_MS * (2 ** min(overdue, 4)))
            self._context_request_timer.start(delay_ms)
            return
        self._auto_refresh_count = 0
        self._auto_last_progress_at = time.monotonic()
        if decision.action == "stop":
            self._stop_auto_pilot(reason="决策引擎停止")
            latest = self._latest_relevant_job()
            window = self.window()
            if latest is not None and latest.status == DesktopJobState.FAILED:
                if hasattr(window, "show_priority_status"):
                    window.show_priority_status(
                        "任务执行失败，全自动已停止。请检查错误信息后重试。",
                        10_000,
                        3,  # _STATUS_ERROR
                    )
            elif (
                self._studio is not None
                and self._studio.pending_checkpoint is not None
                and any(
                    opt.option_id == "pause_for_human" and opt.is_recommended
                    for opt in self._studio.pending_checkpoint.options
                )
            ):
                if hasattr(window, "show_priority_status"):
                    window.show_priority_status(
                        "归档硬门阻断且无法自动修复，自动运行已暂停。请查看报告后手动处理。",
                        10_000,
                        2,  # _STATUS_WARNING
                    )
            return
        if decision.action == "finish":
            self._auto_watchdog.stop()
            mode = self._mode
            project_id = self.current_project_id()
            self._auto_started = False
            self._stopped_from_auto = False
            self._auto_pilot_pending = False
            self._render_action_panel()
            window = self.window()
            if hasattr(window, "show_priority_status"):
                if mode == self.MODE_BOOK_AUTO and project_id:
                    message = f"{project_id} 章节连跑已完成"
                elif mode == self.MODE_BOOK_AUTO:
                    message = "章节连跑已完成"
                else:
                    message = "本章自动已完成"
                window.show_priority_status(
                    message,
                    4000,
                    getattr(window, "_STATUS_SUCCESS", 1),
                )
            return

        self._auto_pilot_pending = True
        self._auto_gen += 1
        _gen = self._auto_gen

        if decision.action == "resolve_checkpoint" and decision.option is not None:
            _cp = self._studio.pending_checkpoint if self._studio else None
            if _cp is not None and _cp.prompt and "重新规划" in _cp.prompt:
                window = self.window()
                if hasattr(window, "show_priority_status"):
                    window.show_priority_status(
                        f"第 {self.current_chapter_number()} 章：{_cp.prompt.splitlines()[0]}",
                        15_000,
                        2,  # _STATUS_WARNING
                    )
            QTimer.singleShot(
                decision.delay_ms,
                lambda opt=decision.option, gen=_gen: self._auto_resolve_checkpoint(opt, gen),
            )
            return
        if decision.action == "advance_chapter" and decision.next_chapter is not None:
            QTimer.singleShot(
                decision.delay_ms,
                lambda ch=decision.next_chapter, gen=_gen: self._auto_advance(ch, gen),
            )
            return
        if decision.action == "prepare_chapter":
            QTimer.singleShot(decision.delay_ms, lambda gen=_gen: self._auto_prepare(gen))

    def _check_auto_pilot_timeout(self) -> None:
        """Watchdog callback: stop auto-pilot if no progress for AUTO_PILOT_TIMEOUT_SECS."""
        if getattr(self, "_engine_owned_autorun", False):
            # Durable Engine retry/timeout budgets are authoritative.
            return
        if not self._auto_pilot or not self._auto_started:
            self._auto_watchdog.stop()
            return
        # PAUSED 状态（如等待 checkpoint 解析、外部 I/O）同样视为"仍在推进"，
        # 避免长时间 PAUSED 导致看门狗误判超时停止连跑。
        if any(
            j.status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED, DesktopJobState.PAUSED}
            for j in self._jobs
        ):
            self._auto_last_progress_at = time.monotonic()
            return
        if self._studio is None:
            _logger.debug(
                "Auto-pilot continuing in background, waiting for project context to load"
            )
        elapsed = time.monotonic() - self._auto_last_progress_at
        if elapsed >= self.AUTO_PILOT_TIMEOUT_SECS:
            self._auto_watchdog.stop()
            minutes = int(self.AUTO_PILOT_TIMEOUT_SECS // 60)
            self._stop_auto_pilot(reason=f"全自动超过 {minutes} 分钟未推进，已自动停止")
            window = self.window()
            if hasattr(window, "show_priority_status"):
                window.show_priority_status(
                    f"全自动已超过 {minutes} 分钟未推进，已自动停止。可点《继续全自动》恢复。",
                    10_000,
                    3,  # _STATUS_ERROR
                )

    def _is_stale_auto_timer(self, gen: int) -> bool:
        if gen == self._auto_gen:
            self._auto_pilot_pending = False
        return gen != self._auto_gen or not self._auto_pilot

    def _auto_resolve_checkpoint(self, option: DecisionOption, gen: int = 0) -> None:
        if self._is_stale_auto_timer(gen):
            return
        self._handle_checkpoint_option(option)

    def _auto_advance(self, next_chapter: int, gen: int = 0) -> None:
        if self._is_stale_auto_timer(gen):
            return
        self._auto_repair_pending = False
        self._auto_last_progress_at = time.monotonic()
        project_id = self.current_project_id()
        prev_ch = self.current_chapter_number()
        self._auto_repair_attempts.pop((project_id, prev_ch), None)

        self._state.reset_chapter_context()

        if self._studio:
            if self._studio.project_id != project_id:
                return
            key = (self._studio.project_id, prev_ch)
            self._notes_expanded_by_chapter.pop(key, None)

        self.auto_advance_requested.emit(project_id, next_chapter)

    def _auto_prepare(self, gen: int = 0) -> None:
        if self._is_stale_auto_timer(gen):
            return
        self._auto_chapter_prepared = True
        if self._mode == self.MODE_BOOK_AUTO and not self._book_auto_skip_done:
            self._submit_prepare(force=True)
        else:
            self._submit_prepare()

    def _stop_auto_pilot(self, reason: str = "用户已取消", cancel_jobs: bool = True) -> None:
        # A failed-job render may request a stop while state_changed is connected
        # synchronously to another render.  Make the transition atomic and reject
        # nested stop requests so an intermediate auto_started=True snapshot can
        # never recurse through render -> stop -> state_changed -> render.
        if getattr(self, "_auto_stop_in_progress", False):
            return

        self._auto_stop_in_progress = True
        try:
            stopped_mode = self._mode
            current_project = self.current_project_id()
            active_job_ids = [
                job.job_id
                for job in self._jobs
                if job.status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
            ]

            self._auto_watchdog.stop()
            with QSignalBlocker(self._state):
                self._auto_started = False
                self._stopped_mode = stopped_mode
                self._auto_pilot_pending = False
                self._auto_repair_pending = False
                self._auto_chapter_prepared = False
                self._auto_refresh_count = 0
                self._auto_last_progress_at = time.monotonic()
                self._auto_gen += 1
                # 多项目并行：仅清除当前项目的修复尝试计数，避免影响其他项目的后台连跑。
                if current_project:
                    stale_keys = [
                        key for key in self._auto_repair_attempts if key[0] == current_project
                    ]
                    for key in stale_keys:
                        self._auto_repair_attempts.pop(key, None)
                else:
                    self._auto_repair_attempts.clear()
                self._stopped_from_auto = True
                self._set_mode(self.MODE_MANUAL)

            if cancel_jobs:
                for job_id in active_job_ids:
                    self.cancel_job_requested.emit(job_id, reason)
            self.auto_pilot_stopped.emit(current_project)
            self._render_action_panel()
        finally:
            self._auto_stop_in_progress = False

    def _resume_auto_pilot(self) -> None:
        self._stopped_from_auto = False
        self._auto_started = True
        self._auto_last_progress_at = time.monotonic()
        self._auto_refresh_count = 0
        self._auto_gen += 1
        self._auto_watchdog.start()
        self._set_mode(self._stopped_mode)
        self._render_action_panel()
        if getattr(self, "_engine_owned_autorun", False):
            self.auto_pilot_started.emit(self.current_project_id())
        self._try_auto_action()

    def _should_follow_autorun(self) -> bool:
        """Check if UI should follow auto-pilot chapter jumps.

        Returns True only if user has enabled "跟随连跑" mode.
        When False, chapter logic advances but UI does not jump.
        """
        return self._state.follow_autorun

    def _queue_auto_submit_repair(self) -> None:
        self._auto_repair_pending = True
        _gen = self._auto_gen
        QTimer.singleShot(0, lambda gen=_gen: self._auto_submit_repair(gen))

    def _auto_submit_repair(self, gen: int = 0) -> None:
        if gen != self._auto_gen:
            return
        self._auto_repair_pending = False
        if not self._auto_pilot or not self._auto_started:
            return
        if self._studio is None:
            return
        _active = self._latest_relevant_job()
        if _active is not None and _active.status in {
            DesktopJobState.RUNNING,
            DesktopJobState.QUEUED,
        }:
            return
        continuity_issues = self._studio.continuity_issues or []
        causal_issues = self._studio.causal_issues or []
        if not continuity_issues and not causal_issues:
            return
        _ch_key = (self._studio.project_id, self._studio.chapter_number)
        self._auto_repair_attempts[_ch_key] = self._auto_repair_attempts.get(_ch_key, 0) + 1
        try:
            if continuity_issues and causal_issues:
                issues_request = build_repair_issues_request(
                    project_id=self._studio.project_id,
                    chapter_number=self._studio.chapter_number,
                    continuity_issue_indices=list(range(len(continuity_issues))),
                    causal_issue_indices=list(range(len(causal_issues))),
                )
                self.repair_issues_requested.emit(issues_request)
            elif continuity_issues:
                continuity_request = build_repair_continuity_request(
                    project_id=self._studio.project_id,
                    chapter_number=self._studio.chapter_number,
                    issue_indices=list(range(len(continuity_issues))),
                )
                self.repair_continuity_requested.emit(continuity_request)
            else:
                causal_request = build_repair_issues_request(
                    project_id=self._studio.project_id,
                    chapter_number=self._studio.chapter_number,
                    continuity_issue_indices=[],
                    causal_issue_indices=list(range(len(causal_issues))),
                )
                self.repair_issues_requested.emit(causal_request)
        except Exception:
            self._stop_auto_pilot(reason="构建修复请求失败")
            return

    def _auto_mode_label(self) -> str:
        from .autorun import auto_mode_label

        mode = self._stopped_mode if not self._auto_pilot else self._mode
        return auto_mode_label(mode)

    @property
    def _auto_pilot(self) -> bool:
        return self._mode in {self.MODE_AUTO, self.MODE_BOOK_AUTO}

    def _start_auto_pilot(self) -> None:
        from .dialogs import SkipStrategyDialog

        if self._mode == self.MODE_BOOK_AUTO and self._studio is not None:
            done_count = sum(1 for ch in self._studio.chapters if ch.status == "done")
            if done_count > 0:
                last_done_chapter = max(
                    (ch.chapter_number for ch in self._studio.chapters if ch.status == "done"),
                    default=0,
                )
                current_ch = self._studio.chapter_number
                if current_ch <= last_done_chapter:
                    dlg = SkipStrategyDialog(done_count, parent=self.window())
                    dlg.exec()
                    result = dlg.get_result()
                    if result == SkipStrategyDialog.CANCEL:
                        return
                    if self._studio is None:
                        return
                    self._book_auto_skip_done = result == SkipStrategyDialog.SKIP

                    if result == SkipStrategyDialog.REGENERATE and dlg.should_delete_old_chapters():
                        self._delete_completed_chapter_files()
        self._auto_started = True
        self._auto_chapter_prepared = False
        self._auto_last_progress_at = time.monotonic()
        self._auto_refresh_count = 0
        self._auto_watchdog.start()
        self._render_action_panel()
        self.auto_pilot_started.emit(self.current_project_id())
        self._try_auto_action()

    def _schedule_retry_after(self, delay_secs: int) -> None:
        self._state.scheduled_retry_at = time.monotonic() + delay_secs
        self._retry_countdown_timer.start()
        self._render_action_panel()

    def _cancel_scheduled_retry(self) -> None:
        self._state.scheduled_retry_at = 0.0
        self._retry_countdown_timer.stop()
        self._render_action_panel()

    def _on_retry_countdown_tick(self) -> None:
        remaining = self._state.scheduled_retry_at - time.monotonic()
        if remaining <= 0:
            self._retry_countdown_timer.stop()
            self._state.scheduled_retry_at = 0.0
            self._do_scheduled_retry()
        else:
            self._render_action_panel()

    def _do_scheduled_retry(self) -> None:
        studio = self._studio
        latest_job = self._latest_relevant_job()
        if latest_job is None or latest_job.status != DesktopJobState.FAILED:
            return
        checkpoint = studio.pending_checkpoint if studio else None
        if studio and studio.has_review_progress and checkpoint is not None:
            self._on_resume_from_progress(checkpoint)
        elif checkpoint is not None:
            recommended = next((opt for opt in checkpoint.options if opt.is_recommended), None)
            if recommended:
                self._handle_checkpoint_option(recommended)
            elif checkpoint.options:
                self._handle_checkpoint_option(checkpoint.options[0])
        else:
            self._submit_prepare()

    def _on_mode_changed(self, button_id: int, checked: bool) -> None:
        if not checked:
            return
        _index_to_mode = {v: k for k, v in self._MODE_INDEX.items()}
        old_mode = self._mode
        self._mode = _index_to_mode.get(button_id, self.MODE_MANUAL)

        self._auto_pilot_pending = False
        self._auto_repair_pending = False
        self._auto_chapter_prepared = False
        self._auto_gen += 1

        if old_mode in {self.MODE_AUTO, self.MODE_BOOK_AUTO} and self._mode not in {
            self.MODE_AUTO,
            self.MODE_BOOK_AUTO,
        }:
            self._auto_repair_attempts.clear()
        elif self._mode in {self.MODE_MANUAL, self.MODE_SUGGEST}:
            self._auto_repair_attempts.clear()

        if not self._auto_pilot:
            self._auto_started = False
            self._stopped_from_auto = False
        else:
            self._stopped_from_auto = False

        if old_mode in {self.MODE_AUTO, self.MODE_BOOK_AUTO} and self._mode not in {
            self.MODE_AUTO,
            self.MODE_BOOK_AUTO,
        }:
            self._auto_refresh_count = 0
            self._auto_watchdog.stop()
            self.auto_pilot_stopped.emit(self.current_project_id())

        self._render_action_panel()
        if self._studio is not None:
            self._render_continuity_checklist()
            self._render_causal_checklist()
        self._notify_ui_state_changed()

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        if not self._auto_pilot:
            self._auto_started = False
        self._mode_selector.blockSignals(True)
        self._mode_selector.set_mode(self._MODE_INDEX.get(mode, 0), animate=False)
        self._mode_selector.blockSignals(False)
        self._notify_ui_state_changed()
