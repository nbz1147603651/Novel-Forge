"""Mixin module: shutdown methods for ``NovelForgeDesktopWindow``.

Auto-extracted in the M3.1 giant-file-split refactor. Methods live here so the
main :file:`window/__init__.py` facade can compose them via mixin inheritance.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QTimer,
)
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMessageBox,
    QProgressBar,
    QVBoxLayout,
)

import novel_forge.desktop.page_registrations as _page_registrations  # noqa: F401
from novel_forge.desktop.constants import (
    COMPACT_HEIGHT_THRESHOLD,
    COMPACT_WIDTH_THRESHOLD,
    WINDOW_DEFAULT_MIN_HEIGHT,
    WINDOW_DEFAULT_MIN_WIDTH,
    WINDOW_DEFAULT_START_HEIGHT,
    WINDOW_DEFAULT_START_WIDTH,
    WINDOW_SCREEN_HEIGHT_RATIO,
    WINDOW_SCREEN_WIDTH_RATIO,
)
from novel_forge.desktop.registry import page_registry
from novel_forge.desktop.state.store import get_ui_store
from novel_forge.desktop.ui_perf import ui_perf_span

_logger = logging.getLogger(__name__)

_WINDOW_DEFAULT_MIN_WIDTH = WINDOW_DEFAULT_MIN_WIDTH
_WINDOW_DEFAULT_MIN_HEIGHT = WINDOW_DEFAULT_MIN_HEIGHT
_WINDOW_DEFAULT_START_WIDTH = WINDOW_DEFAULT_START_WIDTH
_WINDOW_DEFAULT_START_HEIGHT = WINDOW_DEFAULT_START_HEIGHT
_WINDOW_SCREEN_WIDTH_RATIO = WINDOW_SCREEN_WIDTH_RATIO
_WINDOW_SCREEN_HEIGHT_RATIO = WINDOW_SCREEN_HEIGHT_RATIO
_COMPACT_WIDTH_THRESHOLD = COMPACT_WIDTH_THRESHOLD
_COMPACT_HEIGHT_THRESHOLD = COMPACT_HEIGHT_THRESHOLD

if TYPE_CHECKING:
    pass


from novel_forge.desktop.window._widgets import (  # noqa: E402
    PageMeta,
)


class ShutdownMixin:
    """Mixin that contributes the **shutdown** method group."""

    def _accept_close(self, event: QCloseEvent) -> None:
        """Hide immediately, then finish bounded teardown before accepting."""

        try:
            self.hide()
        except (AttributeError, RuntimeError):
            pass
        try:
            self._pre_close_cleanup()
        except Exception:
            # A best-effort resource hook must never leave an invisible
            # application running after the user committed to exit.
            _logger.warning("Pre-close cleanup failed", exc_info=True)
        finally:
            event.accept()

    def _pre_close_cleanup(self) -> None:
        """Stop timers and animations before accepting close to prevent use-after-free crashes."""
        self._is_closing = True
        app = QApplication.instance()
        app_state_connection = getattr(self, "_application_state_connection", None)
        if app is not None and app_state_connection is not None:
            try:
                app.disconnect(app_state_connection)
            except (RuntimeError, TypeError):
                pass
            self._application_state_connection = None
        # ── Detach WindowState from the process-wide UIStore singleton ──
        # Without this, a second window created after the first closes would
        # inherit the first window's (now-deleted) WindowState, and late signal
        # emissions would hit a deleted QObject.
        window_state = getattr(self, "_window_state", None)
        if window_state is not None:
            get_ui_store().detach_window_state(window_state)
            self._window_state = None
        # ── Stop all tracked _safe_deferred timers ──
        deferred_timers: set = getattr(self, "_deferred_timers", set())
        for timer in list(deferred_timers):
            try:
                if timer.isActive():
                    timer.stop()
            except RuntimeError:
                pass
        deferred_timers.clear()
        # ── Disconnect background worker signals before thread-pool drain ──
        # Prevents late signal delivery to destroyed Qt objects after waitForDone
        # timeout.  Both workspace refresh and context refresh workers are covered.
        wr = getattr(self, "_active_workspace_refresh_worker", None)
        if wr is not None:
            try:
                wr.signals.refresh_ready.disconnect()
            except (RuntimeError, TypeError):
                pass
            try:
                wr.signals.failed.disconnect()
            except (RuntimeError, TypeError):
                pass
            self._active_workspace_refresh_worker = None
        for ctx_worker in list(getattr(self, "_active_context_refresh_workers", set())):
            try:
                ctx_worker.signals.finished.disconnect()
            except (RuntimeError, TypeError):
                pass
            try:
                ctx_worker.signals.failed.disconnect()
            except (RuntimeError, TypeError):
                pass
        self._active_context_refresh_workers.clear()
        if self._ui_session_save_timer.isActive():
            self._ui_session_save_timer.stop()
        self._save_ui_session()
        self._refresh_timer.stop()
        fs_watcher = self._fs_watcher
        if fs_watcher is not None:
            fs_watcher.deleteLater()
            self._fs_watcher = None
        self._fs_watcher_debounce_timer.stop()
        self._jobs_bind_timer.stop()
        self._density_resize_timer.stop()
        if self._skeleton_loading_timer is not None:
            self._skeleton_loading_timer.stop()
        if self._autorun_ticker is not None:
            self._autorun_ticker.stop()
        self._sleep_wake_monitor.stop()
        self._wake_recovery_timer.stop()
        self._workspace_refresh_schedule_timer.stop()
        self._page_prewarm_timer.stop()
        self._page_preload_timer.stop()
        if self._floating_task_companion is not None:
            self._floating_task_companion.shutdown()
        if self._floating_stream_window is not None:
            self._floating_stream_window.close()
            self._floating_stream_window = None
        if self._task_focus_dialog is not None:
            self._task_focus_dialog.close()
            self._task_focus_dialog = None
        if self._page_animation is not None:
            self._clear_page_animation()
        # Shut down all pages that have cleanup hooks.
        with ui_perf_span("shutdown.pages", page_count=len(self._pages)):
            for page in self._pages.values():
                if hasattr(page, "shutdown"):
                    try:
                        page.shutdown()
                    except Exception as exc:
                        _logger.warning("Page shutdown error: %s", exc)
        page_registry.clear_instances(exclude=self._pages.values())
        with ui_perf_span("shutdown.jobs"):
            self._job_manager.shutdown(wait_ms=100)
        # Phase M6: also drain global ui_io_pool + aux_pool to ensure no
        # QRunnable still owns a Qt object reference when we exit. The
        # job_pool was already drained inside JobManager.shutdown().
        # Workers were already cancelled above, so this is only a short safety
        # net for threads that haven't observed the cancel flag yet. Timed-out
        # pools are retained until idle instead of being destroyed.
        try:
            from novel_forge.desktop.thread_pools import (
                shutdown_desktop_thread_pools,
            )

            with ui_perf_span("shutdown.thread_pools"):
                shutdown_desktop_thread_pools(wait_ms=50)
        except Exception as exc:
            _logger.warning("thread_pools shutdown failed: %s", exc)
        try:
            # RuntimeServices may never have finished building if shutdown
            # races the B2 async init worker.
            if self._workspace is not None:
                with ui_perf_span("shutdown.workspace"):
                    self._workspace.shutdown()
        except Exception as exc:
            _logger.warning("Workspace shutdown error: %s", exc)
        for timer in self.findChildren(QTimer):
            if timer.isActive():
                timer.stop()

    def _collect_unsaved_page_entries(self) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        for page_id, page in self._pages.items():
            has_unsaved = getattr(page, "has_unsaved_changes", None)
            if not callable(has_unsaved):
                continue
            try:
                dirty = bool(has_unsaved())
            except Exception:
                continue
            if not dirty:
                continue
            describe = getattr(page, "unsaved_changes_description", None)
            if callable(describe):
                try:
                    detail = str(describe() or "").strip()
                except Exception:
                    detail = ""
            else:
                detail = ""
            if not detail:
                detail = f"- {self.PAGE_META.get(page_id, PageMeta(page_id, page_id, page_id, '')).label}页有未保存内容"
            save_fn = getattr(page, "save_pending_changes", None)
            entries.append(
                {
                    "page_id": page_id,
                    "page": page,
                    "detail": detail,
                    "save_fn": save_fn if callable(save_fn) else None,
                }
            )
        return entries

    def _confirm_exit_for_unsavable(self, details: list[str]) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("仍有内容无法保存")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("以下内容为临时输入，当前没有可用的“保存”动作，退出将丢失：")
        box.setInformativeText("\n".join(details))

        leave_btn = box.addButton("仍要退出", QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = box.addButton("返回编辑", QMessageBox.ButtonRole.RejectRole)
        leave_btn.setObjectName("actionButton")
        leave_btn.setProperty("variant", "danger")
        leave_btn.setProperty("compact", True)
        cancel_btn.setObjectName("actionButton")
        cancel_btn.setProperty("variant", "secondary")
        cancel_btn.setProperty("compact", True)
        for btn in (leave_btn, cancel_btn):
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        box.setDefaultButton(cancel_btn)
        box.exec()
        return box.clickedButton() == leave_btn

    def _shutdown_with_active_jobs(
        self,
        active_labels: list[str],
        cancelling_labels: list[str] | None = None,
    ) -> tuple[bool, bool]:
        """Show a graceful shutdown dialog when LLM jobs are still running or winding down.

        active_labels: jobs in RUNNING/QUEUED state.
        cancelling_labels: jobs already user-cancelled but whose threads haven't exited yet.
        Returns (proceed, force):
          proceed=True  → caller should continue with exit sequence
          force=True    → caller should use os._exit(0) (skip thread waiting)
        """
        from PySide6.QtCore import QElapsedTimer
        from PySide6.QtWidgets import QDialog, QDialogButtonBox

        cancelling_labels = cancelling_labels or []
        all_labels = active_labels + cancelling_labels

        dialog = QDialog(self)
        dialog.setMinimumWidth(420)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)

        if active_labels and not cancelling_labels:
            dialog.setWindowTitle("正在等待模型响应")
            info = QLabel(f"当前有 {len(active_labels)} 个任务正在等待模型响应：")
        elif cancelling_labels and not active_labels:
            dialog.setWindowTitle("正在等待已取消的任务退出")
            info = QLabel(f"已取消 {len(cancelling_labels)} 个任务，正在等待后台线程退出：")
        else:
            dialog.setWindowTitle("正在等待任务完成")
            info = QLabel(
                f"当前有 {len(active_labels)} 个任务等待响应，"
                f"{len(cancelling_labels)} 个已取消但仍在退出中："
            )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Task list (max 5 shown)
        shown = all_labels[:5]
        if len(all_labels) > 5:
            shown.append(f"…及其他 {len(all_labels) - 5} 个任务")
        task_label = QLabel("\n".join(f"  • {lbl}" for lbl in shown))
        task_label.setWordWrap(True)
        task_label.setObjectName("windowTaskLabel")
        layout.addWidget(task_label)

        # Progress bar (indeterminate)
        progress = QProgressBar()
        progress.setRange(0, 0)  # indeterminate / pulsing
        progress.setTextVisible(False)
        progress.setFixedHeight(6)
        layout.addWidget(progress)

        # Status label
        status = QLabel("任务正在运行，请选择操作。")
        status.setObjectName("windowStatusLabel")
        layout.addWidget(status)

        # Checkpoint hint
        hint = QLabel("已完成的阶段会自动保存进度，下次可从断点继续。")
        hint.setObjectName("windowHintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # Buttons
        btn_box = QDialogButtonBox()
        wait_btn = btn_box.addButton("等待完成", QDialogButtonBox.ButtonRole.AcceptRole)
        force_btn = btn_box.addButton("强制退出", QDialogButtonBox.ButtonRole.DestructiveRole)
        cancel_btn = btn_box.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        wait_btn.setObjectName("actionButton")
        wait_btn.setProperty("variant", "primary")
        wait_btn.setProperty("compact", True)
        force_btn.setObjectName("actionButton")
        force_btn.setProperty("variant", "danger")
        force_btn.setProperty("compact", True)
        cancel_btn.setObjectName("actionButton")
        cancel_btn.setProperty("variant", "secondary")
        cancel_btn.setProperty("compact", True)
        for btn in (wait_btn, force_btn, cancel_btn):
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        layout.addWidget(btn_box)

        # State
        user_choice: list[str] = []  # mutable container for closure

        def on_wait() -> None:
            """Wait for active jobs to finish *naturally* (no cancellation).

            The poll timer monitors job status and auto-accepts when all jobs
            complete on their own.  The force-quit button stays enabled so the
            user can still bail out if the wait takes too long.
            """
            if not user_choice:
                user_choice.append("wait")
                wait_btn.setEnabled(False)
                cancel_btn.setEnabled(False)
                status.setText("正在等待自然完成，不中断正在执行的任务…")

        def on_force() -> None:
            # Replace (not append) so this takes priority even if "wait" was clicked first.
            user_choice[:] = ["force"]
            self._job_manager.request_cancel_all()
            dialog.accept()

        def on_cancel() -> None:
            user_choice.append("cancel")
            dialog.reject()

        wait_btn.clicked.connect(on_wait)
        force_btn.clicked.connect(on_force)
        cancel_btn.clicked.connect(on_cancel)

        # NOTE: Do NOT call request_cancel_all() here — that would cancel jobs
        # before the user has made a choice.  Only the "force" button cancels.
        # The poll timer below monitors natural completion (for "wait") or
        # thread shutdown after force-cancel.

        # Poll for job completion while dialog is open — check BOTH active and
        # cancelling lists so threads that were already user-stopped are included.
        poll_timer = QTimer(dialog)
        elapsed = QElapsedTimer()
        elapsed.start()

        def poll_jobs() -> None:
            rem_active = self._job_manager.active_job_labels()
            rem_cancelling = self._job_manager.cancelling_job_labels()
            remaining = rem_active + rem_cancelling
            secs = elapsed.elapsed() // 1000
            if not remaining:
                status.setText("所有任务已结束，可以安全退出。")
                progress.setRange(0, 1)
                progress.setValue(1)
                poll_timer.stop()
                # Auto-accept after tasks finish; set choice only if not already set
                if not user_choice:
                    user_choice.append("wait")
                dialog.accept()
                return

            if user_choice and user_choice[0] == "force":
                # After force-cancel, describe as thread-wait
                status.setText(f"正在等待 {len(remaining)} 个已取消的任务退出…（已等待 {secs} 秒）")
            else:
                status.setText(f"正在等待 {len(remaining)} 个任务结束…（已等待 {secs} 秒）")

        poll_timer.timeout.connect(poll_jobs)
        poll_timer.start(500)

        dialog.exec()
        poll_timer.stop()

        choice = user_choice[0] if user_choice else "cancel"
        return choice in ("wait", "force"), choice == "force"

    def closeEvent(self, event: QCloseEvent) -> None:
        if getattr(self, "_is_closing", False):
            event.accept()
            return
        # ── Step 0: check for active or cancelling LLM jobs ──
        active_labels = self._job_manager.active_job_labels()
        cancelling_labels = self._job_manager.cancelling_job_labels()
        if active_labels or cancelling_labels:
            proceed, force = self._shutdown_with_active_jobs(active_labels, cancelling_labels)
            if not proceed:
                event.ignore()
                return
            if force:
                # User chose "强制退出": terminate the process immediately without
                # waiting for background threads.  os._exit() bypasses Python
                # finalizers and thread join — equivalent to force-killing the app.
                import os as _os

                self._save_ui_session()
                # I-11: 显式释放 sleep inhibitor + flush streams（atexit 不可靠）
                # os._exit() 会绕过 atexit handler，导致 SleepInhibitor 的弱引用
                # 释放回调不执行，caffeinate / SetThreadExecutionState / systemd-inhibit
                # 子进程不会被终止。
                try:
                    sleep_inhibitor = getattr(self._job_manager, "_sleep_inhibitor", None)
                    if sleep_inhibitor is not None:
                        sleep_inhibitor.release()
                except Exception:
                    pass
                try:
                    import sys as _sys

                    _sys.stdout.flush()
                    _sys.stderr.flush()
                except Exception:
                    pass
                _os._exit(0)

        dirty_entries = self._collect_unsaved_page_entries()
        if not dirty_entries:
            self._accept_close(event)
            return

        details = [str(item.get("detail", "") or "") for item in dirty_entries]
        savable_entries = [item for item in dirty_entries if callable(item.get("save_fn"))]
        unsavable_details = [
            str(item.get("detail", "") or "")
            for item in dirty_entries
            if not callable(item.get("save_fn"))
        ]

        # If nothing can actually be saved, avoid presenting a misleading "Save and Exit" prompt.
        if not savable_entries and unsavable_details:
            if self._confirm_exit_for_unsavable(unsavable_details):
                self._accept_close(event)
                return
            event.ignore()
            return

        box = QMessageBox(self)
        box.setWindowTitle("尚未保存")
        box.setIcon(QMessageBox.Icon.Question)
        if unsavable_details:
            box.setText("尚有未保存内容（含临时输入），退出前是否先保存可保存项？")
        else:
            box.setText("尚有未落笔的内容，退出前是否先保存？")
        box.setInformativeText("\n".join(details))

        save_btn = box.addButton("保存并退出", QMessageBox.ButtonRole.AcceptRole)
        discard_btn = box.addButton("不保存退出", QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = box.addButton("继续编辑", QMessageBox.ButtonRole.RejectRole)
        save_btn.setObjectName("actionButton")
        save_btn.setProperty("variant", "primary")
        save_btn.setProperty("compact", True)
        discard_btn.setObjectName("actionButton")
        discard_btn.setProperty("variant", "danger")
        discard_btn.setProperty("compact", True)
        cancel_btn.setObjectName("actionButton")
        cancel_btn.setProperty("variant", "secondary")
        cancel_btn.setProperty("compact", True)
        for btn in (save_btn, discard_btn, cancel_btn):
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        box.setDefaultButton(save_btn)
        box.exec()

        clicked = box.clickedButton()
        if clicked == cancel_btn:
            event.ignore()
            return
        if clicked == save_btn:
            for item in savable_entries:
                save_fn = item.get("save_fn")
                if not callable(save_fn):
                    continue
                try:
                    ok = bool(save_fn())
                except Exception:
                    _logger.warning("Save callback failed during shutdown", exc_info=True)
                    ok = False
                if not ok:
                    event.ignore()
                    return
            if unsavable_details and not self._confirm_exit_for_unsavable(unsavable_details):
                event.ignore()
                return
            self._accept_close(event)
            return
        if clicked == discard_btn:
            self._accept_close(event)
            return

        event.ignore()
