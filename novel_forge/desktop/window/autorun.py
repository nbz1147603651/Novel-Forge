"""Mixin module: autorun methods for ``NovelForgeDesktopWindow``.

Auto-extracted in the M3.1 giant-file-split refactor. Methods live here so the
main :file:`window/__init__.py` facade can compose them via mixin inheritance.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QTimer,
)

import novel_forge.desktop.page_registrations as _page_registrations  # noqa: F401
from novel_forge.core.config import get_settings
from novel_forge.desktop.workflow_requests import (
    build_prepare_chapter_request,
    build_repair_continuity_request,
    build_repair_issues_request,
    build_resolve_chapter_checkpoint_request,
)
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import canon_watermark

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


class AutorunMixin:
    """Mixin that contributes the **autorun** method group."""

    def _current_chapter_done(self, snapshot: object) -> bool:
        chapter_number = int(getattr(snapshot, "chapter_number", 0) or 0)
        for chapter in getattr(snapshot, "chapters", []) or []:
            if int(getattr(chapter, "chapter_number", 0) or 0) == chapter_number:
                return getattr(chapter, "status", "") == "done"
        return False

    def _project_chapter_ready(self, project_id: str, chapter_number: int) -> bool:
        if not project_id or chapter_number < 1 or self._snapshot is None:
            return True
        project_dir = self._snapshot.storage_root / project_id
        layout = ProjectLayout(project_dir)
        if not layout.chapter_path(chapter_number).exists():
            return False
        # New projects persist the authoritative canon watermark in the
        # StoryKernel SQLite DB; legacy projects may still have
        # canon/canon_current.json.  Use the shared reader so background
        # book-auto does not wait forever merely because the legacy JSON file
        # is absent.
        storage = FileSystemStorage(self._snapshot.storage_root)
        from novel_forge.persistence.authoring_store import AuthoringStore
        from novel_forge.pipeline.finalization_manifest import tracked_finalization_ready

        return canon_watermark(storage, layout) >= chapter_number and tracked_finalization_ready(
            storage, layout, chapter_number,
            require_state=get_settings().narrative_state_required,
            allow_legacy=AuthoringStore(layout.root).policy() is None,
        )

    def _autorun_cooldown_seconds(self) -> int:
        try:
            return max(
                0, int(getattr(get_settings(), "long_auto_chapter_cooldown_seconds", 0) or 0)
            )
        except Exception:
            return 0

    def _reset_project_autorun_transients(self, project_id: str) -> None:
        project_id = (project_id or "").strip()
        if not project_id:
            return
        if not hasattr(self, "_autorun_chapter_numbers"):
            self._autorun_chapter_numbers = {}
        if not hasattr(self, "_autorun_writing_modes_by_project"):
            self._autorun_writing_modes_by_project = {}
        if not hasattr(self, "_autorun_cooldown_until"):
            self._autorun_cooldown_until = {}
        if not hasattr(self, "_autorun_refresh_counts"):
            self._autorun_refresh_counts = {}
        if not hasattr(self, "_autorun_last_submitted_checkpoint"):
            self._autorun_last_submitted_checkpoint = {}
        if not hasattr(self, "_autorun_prepared_chapters"):
            self._autorun_prepared_chapters = set()
        if not hasattr(self, "_autorun_resolve_attempts"):
            self._autorun_resolve_attempts = {}
        self._autorun_chapter_numbers.pop(project_id, None)
        self._autorun_writing_modes_by_project.pop(project_id, None)
        self._autorun_cooldown_until.pop(project_id, None)
        self._autorun_refresh_counts.pop(project_id, None)
        self._autorun_last_submitted_checkpoint = {
            key: value
            for key, value in self._autorun_last_submitted_checkpoint.items()
            if key[0] != project_id
        }
        self._autorun_prepared_chapters = {
            key for key in self._autorun_prepared_chapters if key[0] != project_id
        }
        # Note: _autorun_resolve_attempts is intentionally NOT reset here — the retry
        # cap is keyed by checkpoint_id and must survive autorun stop/start so a
        # chronically stuck checkpoint does not get an unlimited fresh budget.
        # (A new checkpoint always starts fresh because the count is keyed by id.)

    def _notify_autorun_start_failure(self, message: str) -> None:
        """Surface an autorun start failure on the priority status bar.

        Uses getattr so the window mixin facade stays mypy-clean: the status
        method lives on the composed window class, not on this mixin.
        """
        show = getattr(self, "show_priority_status", None)
        if callable(show):
            show(message, 8_000, getattr(self, "_STATUS_ERROR", 3))

    def _reject_project_autorun_start(self, project_id: str, message: str) -> None:
        """Return the local chapter-studio control to idle after Engine rejection."""
        self._notify_autorun_start_failure(message)
        studio = self._pages.get("chapter_studio")  # type: ignore[attr-defined]
        stop = getattr(studio, "set_autorun_project_started", None)
        if callable(stop):
            stop(project_id, False)

    def _on_project_autorun_started(self, project_id: str) -> None:
        project_id = (project_id or "").strip()
        if not project_id:
            return
        writing_mode = self._chapter_studio_writing_mode(project_id)
        self._reset_project_autorun_transients(project_id)
        chapter_number = self._get_chapter_number(project_id)
        self._set_autorun_chapter_number(project_id, chapter_number)
        self._autorun_writing_modes_by_project[project_id] = writing_mode
        manager = getattr(self, "_job_manager", None)
        snapshot = getattr(self, "_snapshot", None)
        manager_root = getattr(manager, "storage_root", None)
        snapshot_root = getattr(snapshot, "storage_root", None)
        if manager_root is not None and snapshot_root is not None:
            if Path(manager_root).resolve() != Path(snapshot_root).resolve():
                self._reject_project_autorun_start(
                    project_id,
                    "无法启动 Engine 连跑：任务管理器与工作区存储根目录不一致。",
                )
                return
        start_engine = getattr(manager, "start_chapter_autorun", None)
        if not callable(start_engine):
            self._reject_project_autorun_start(
                project_id,
                "无法启动 Engine 连跑：当前任务管理器不支持 Engine 连跑。",
            )
            return
        studio = self._pages.get("chapter_studio")  # type: ignore[attr-defined]
        page_state = (
            studio.autorun_state_for_project(project_id)
            if studio is not None and hasattr(studio, "autorun_state_for_project")
            else None
        )
        mode = "book" if getattr(page_state, "mode", "") == "book_auto" else "chapter"
        skip_done = bool(getattr(page_state, "book_auto_skip_done", True))
        record = start_engine(
            project_id=project_id,
            chapter_number=chapter_number,
            mode=mode,
            writing_mode=writing_mode,
            force=mode == "book" and not skip_done,
            skip_done=skip_done,
            mock=self._mock_enabled,  # type: ignore[attr-defined]
        )
        if record is None:
            # None 可能表示幂等命中（会话已在执行）或引擎拒绝；仅在后一种情况下报错。
            get_state = getattr(manager, "book_autorun_state", None)
            state = get_state(project_id) if callable(get_state) else None
            if state is None or getattr(state, "status", "") not in {
                "waiting_init",
                "running",
                "retry_wait",
            }:
                self._reject_project_autorun_start(
                    project_id,
                    "无法启动 Engine 连跑：Engine 未接受连跑会话，请检查项目状态后重试。",
                )

    def _on_project_autorun_stopped(self, project_id: str) -> None:
        pause = getattr(
            self._job_manager,  # type: ignore[attr-defined]
            "pause_book_autorun",
            None,
        )
        if callable(pause) and project_id:
            pause(project_id, reason="用户已暂停章节连跑")
        self._reset_project_autorun_transients(project_id)

    def _stop_project_autorun_before_cancel(self, project_id: str, reason: str) -> bool:
        project_id = (project_id or "").strip()
        studio = self._pages.get("chapter_studio")
        if not project_id or studio is None or not hasattr(studio, "autorun_state_for_project"):
            return False

        try:
            state = studio.autorun_state_for_project(project_id)
        except Exception:
            return False
        if not bool(getattr(state, "auto_started", False)) or getattr(state, "mode", "") not in {
            "auto",
            "book_auto",
        }:
            return False

        pause = getattr(
            self._job_manager,  # type: ignore[attr-defined]
            "pause_book_autorun",
            None,
        )
        if callable(pause):
            pause(project_id, reason=reason)

        self._reset_project_autorun_transients(project_id)
        current_project_id = ""
        current_project = getattr(studio, "current_project_id", None)
        if callable(current_project):
            try:
                current_project_id = str(current_project() or "").strip()
            except Exception:
                current_project_id = ""
        if current_project_id == project_id and hasattr(studio, "stop_auto_pilot"):
            try:
                studio.stop_auto_pilot(reason=reason, cancel_jobs=False)
            except TypeError:
                studio.stop_auto_pilot()
            return True
        if hasattr(studio, "set_autorun_project_started"):
            studio.set_autorun_project_started(project_id, False)
        return True

    def _autorun_cooldown_remaining_ms(self, project_id: str) -> int:
        deadline = self._autorun_cooldown_until.get(project_id, 0.0)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            self._autorun_cooldown_until.pop(project_id, None)
            return 0
        return max(1, int(remaining * 1000))

    def _autorun_repair_attempts_for(self, project_id: str, chapter_number: int) -> int:
        studio_page = self._pages.get("chapter_studio")
        state = getattr(studio_page, "_state", None)
        attempts = getattr(state, "auto_repair_attempts", {}) if state is not None else {}
        try:
            return int(attempts.get((project_id, chapter_number), 0) or 0)
        except Exception:
            return 0

    def _increment_autorun_repair_attempts(self, project_id: str, chapter_number: int) -> None:
        studio_page = self._pages.get("chapter_studio")
        state = getattr(studio_page, "_state", None)
        if state is None:
            return
        attempts = getattr(state, "auto_repair_attempts", None)
        if attempts is None:
            return
        key = (project_id, chapter_number)
        attempts[key] = int(attempts.get(key, 0) or 0) + 1

    def _chapter_studio_writing_mode(self, project_id: str | None = None) -> str:
        if project_id:
            stored = getattr(self, "_autorun_writing_modes_by_project", {}).get(project_id)
            if stored in {"whole_chapter", "scene_level"}:
                return str(stored)
        studio_page = self._pages.get("chapter_studio")
        current_mode = getattr(studio_page, "current_writing_mode", None)
        if callable(current_mode):
            try:
                raw_mode = str(current_mode() or "whole_chapter")
                return "scene_level" if raw_mode == "scene_level" else "whole_chapter"
            except Exception:
                pass
        return "whole_chapter"

    def _drive_active_autoruns(self) -> None:
        # Skip autorun processing during the post-wake recovery window to avoid
        # submitting jobs against stale workspace state.
        if self._sleep_wake_monitor.in_recovery_window:
            return
        studio_page = self._pages.get("chapter_studio")
        manager = getattr(self, "_job_manager", None)
        snapshot = getattr(self, "_snapshot", None)
        manager_root = getattr(manager, "storage_root", None)
        snapshot_root = getattr(snapshot, "storage_root", None)
        roots_match = (
            manager_root is None
            or snapshot_root is None
            or Path(manager_root).resolve() == Path(snapshot_root).resolve()
        )
        engine_ids = getattr(manager, "active_book_autorun_project_ids", None)
        engine_state = getattr(manager, "book_autorun_state", None)
        if (
            roots_match
            and studio_page is not None
            and callable(engine_ids)
            and callable(engine_state)
        ):
            local_ids = (
                set(studio_page.active_autorun_project_ids())
                if hasattr(studio_page, "active_autorun_project_ids")
                else set()
            )
            known_project_ids = (
                {project.project_id for project in snapshot.projects}
                if snapshot is not None
                else set()
            )
            persisted_ids = set(engine_ids())
            if known_project_ids:
                persisted_ids &= known_project_ids
            observed = {project_id: engine_state(project_id) for project_id in local_ids}
            # Compatibility fallback for an old in-memory page state that has
            # no Engine session yet. New sessions always take this branch.
            if persisted_ids or any(state is not None for state in observed.values()):
                candidate_ids = persisted_ids | local_ids
                active_ids: set[str] = set()
                for project_id in candidate_ids:
                    state = observed.get(project_id) or engine_state(project_id)
                    status = str(getattr(state, "status", "idle"))
                    running = status in {"waiting_init", "running", "retry_wait"}
                    if running:
                        active_ids.add(project_id)
                        chapter_number = int(getattr(state, "current_chapter", 1) or 1)
                        self._set_autorun_chapter_number(  # type: ignore[attr-defined]
                            project_id, chapter_number
                        )
                    if hasattr(studio_page, "set_autorun_project_started"):
                        studio_page.set_autorun_project_started(project_id, running)
                self._prune_autorun_tracking_dicts(active_project_ids=active_ids)
                return
        if (
            studio_page is None
            or self._snapshot is None
            or self._workspace is None
            or not hasattr(studio_page, "active_autorun_project_ids")
        ):
            # Even when studio page isn't ready, prune stale tracking data.
            self._prune_autorun_tracking_dicts(active_project_ids=set())
            return
        active_ids = set(studio_page.active_autorun_project_ids())
        self._prune_autorun_tracking_dicts(active_project_ids=active_ids)
        for project_id in active_ids:
            self._drive_project_autorun(project_id)

    def _prune_autorun_tracking_dicts(self, *, active_project_ids: set[str]) -> None:
        """Remove stale entries from autorun tracking dictionaries.

        Called on every autorun ticker cycle (~6 s) to prevent unbounded
        growth when projects are deleted or autorun is stopped.
        """
        now = time.monotonic()

        # _autorun_cooldown_until: drop expired entries
        self._autorun_cooldown_until = {
            pid: expiry
            for pid, expiry in self._autorun_cooldown_until.items()
            if expiry > now and pid in active_project_ids
        }

        # _autorun_last_submitted_checkpoint: only keep active projects
        self._autorun_last_submitted_checkpoint = {
            key: value
            for key, value in self._autorun_last_submitted_checkpoint.items()
            if key[0] in active_project_ids
        }

        # _autorun_prepared_chapters: only keep active projects
        self._autorun_prepared_chapters = {
            key for key in self._autorun_prepared_chapters if key[0] in active_project_ids
        }

        # _autorun_resolve_attempts: keep for projects still present in the snapshot
        # (not just active autorun) so the retry cap survives autorun stop/start and
        # app restarts (it is also persisted to disk, see _persist_resolve_attempts).
        if self._snapshot is not None:
            known_ids = {p.project_id for p in self._snapshot.projects}
            self._autorun_resolve_attempts = {
                key: value
                for key, value in self._autorun_resolve_attempts.items()
                if key[0] in known_ids
            }

        # _autorun_refresh_counts: only keep active projects
        self._autorun_refresh_counts = {
            pid: count
            for pid, count in self._autorun_refresh_counts.items()
            if pid in active_project_ids
        }

        # _autorun_chapter_numbers: keep all (user-set values may be needed
        # even when autorun stops); but drop projects no longer in snapshot.
        if self._snapshot is not None:
            known_ids = {p.project_id for p in self._snapshot.projects}
            self._autorun_chapter_numbers = {
                pid: num
                for pid, num in self._autorun_chapter_numbers.items()
                if pid in known_ids
            }

    def _drive_project_autorun(self, project_id: str) -> None:
        project_id = project_id.strip()
        studio_page = self._pages.get("chapter_studio")
        if (
            not project_id
            or project_id in self._autorun_driving_projects
            or studio_page is None
            or self._snapshot is None
            or self._workspace is None
            or not hasattr(studio_page, "autorun_state_for_project")
        ):
            return

        state = studio_page.autorun_state_for_project(project_id)
        if not getattr(state, "auto_started", False) or getattr(state, "mode", "") not in {
            "auto",
            "book_auto",
        }:
            self._autorun_cooldown_until.pop(project_id, None)
            return
        detail = self._snapshot.details.get(project_id)
        if detail is None or detail.mode != "long":
            if hasattr(studio_page, "set_autorun_project_started"):
                studio_page.set_autorun_project_started(project_id, False)
            return
        if self._active_write_job_for_project(project_id) is not None:
            return
        cooldown_ms = self._autorun_cooldown_remaining_ms(project_id)
        if cooldown_ms > 0:
            QTimer.singleShot(
                min(cooldown_ms, 60_000),
                lambda pid=project_id: self._drive_project_autorun(pid),
            )
            return

        self._autorun_driving_projects.add(project_id)
        try:
            # RuntimeServices may still be building on the ui_io pool (B2 async
            # init). Defer this autorun tick; the next ticker fire will retry.
            if self._workspace is None:
                return
            chapter_number = self._get_autorun_chapter_number(project_id)
            snapshot = self._workspace.get_chapter_workspace_snapshot(project_id, chapter_number)
            latest_job = self._latest_chapter_job(
                project_id=project_id,
                chapter_number=chapter_number,
            )
            from novel_forge.desktop.pages.chapter_studio.autorun import (
                AutoPilotContext,
                decide_autopilot_action,
                should_auto_submit_repair,
            )

            context = AutoPilotContext(
                mode=getattr(state, "mode", "manual"),
                auto_started=bool(getattr(state, "auto_started", False)),
                auto_pilot_pending=False,
                studio=snapshot,
                latest_job=latest_job,
                last_submitted_checkpoint_id=self._autorun_last_submitted_checkpoint.get(
                    (project_id, chapter_number)
                ),
                current_chapter_done=self._current_chapter_done(snapshot),
                book_auto_skip_done=bool(getattr(state, "book_auto_skip_done", True)),
                chapter_prepared_this_run=(project_id, chapter_number)
                in self._autorun_prepared_chapters,
                repair_attempts=self._autorun_repair_attempts_for(project_id, chapter_number),
                max_repair_attempts=get_settings().max_auto_repair_attempts,
                upstream_chapter_ready=self._project_chapter_ready(project_id, chapter_number),
                current_project_id=project_id,
            )
            decision = decide_autopilot_action(context)
            if (
                getattr(decision, "action", "none") == "prepare_chapter"
                and should_auto_submit_repair(context)
                and self._submit_project_autorun_repair(project_id, snapshot)
            ):
                return
            self._apply_project_autorun_decision(project_id, snapshot, decision, state)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("后台章节连跑推进失败 | project=%s | error=%s", project_id, exc)
        finally:
            self._autorun_driving_projects.discard(project_id)

    def _submit_project_autorun_repair(self, project_id: str, snapshot: object) -> bool:
        chapter_number = int(getattr(snapshot, "chapter_number", 0) or 0)
        if not project_id or chapter_number <= 0:
            return False
        continuity_issues = list(getattr(snapshot, "continuity_issues", None) or [])
        causal_issues = list(getattr(snapshot, "causal_issues", None) or [])
        if not continuity_issues and not causal_issues:
            return False
        self._increment_autorun_repair_attempts(project_id, chapter_number)
        if continuity_issues and causal_issues:
            request = build_repair_issues_request(
                project_id=project_id,
                chapter_number=chapter_number,
                continuity_issue_indices=list(range(len(continuity_issues))),
                causal_issue_indices=list(range(len(causal_issues))),
                repair_control_mode="ai_auto",
            )
            self._job_manager.submit_repair_issues(request, mock=self._mock_enabled)
        elif continuity_issues:
            continuity_request = build_repair_continuity_request(
                project_id=project_id,
                chapter_number=chapter_number,
                issue_indices=list(range(len(continuity_issues))),
                repair_control_mode="ai_auto",
            )
            self._job_manager.submit_repair_continuity(continuity_request, mock=self._mock_enabled)
        else:
            request = build_repair_issues_request(
                project_id=project_id,
                chapter_number=chapter_number,
                continuity_issue_indices=[],
                causal_issue_indices=list(range(len(causal_issues))),
                repair_control_mode="ai_auto",
            )
            self._job_manager.submit_repair_issues(request, mock=self._mock_enabled)
        return True

    def _apply_project_autorun_decision(
        self,
        project_id: str,
        snapshot: object,
        decision: object,
        state: object,
    ) -> None:
        action = getattr(decision, "action", "none")
        if action == "none":
            return
        if action == "refresh_context":
            count = self._autorun_refresh_counts.get(project_id, 0) + 1
            self._autorun_refresh_counts[project_id] = count
            # Earlier behavior: hard-stop after 15 retries.  In multi-project
            # 连跑 this killed background autorun whenever the workspace
            # snapshot was slow to catch up after a checkpoint resolution,
            # forcing the user to manually re-start the project.
            # New behavior: never permanently stop here — slow down and let
            # the periodic ``_autorun_ticker`` keep retrying.  An informational
            # toast is emitted once when we cross the warning threshold so the
            # user is aware something is lagging.
            #
            # Circuit breaker: after 200 consecutive refresh_context cycles
            # (~10 minutes at 3s backoff) something is fundamentally broken
            # (e.g. workspace backend unavailable).  Stop autorun to prevent
            # unbounded resource consumption and alert the user.
            _REFRESH_CIRCUIT_BREAKER = 200
            if count >= _REFRESH_CIRCUIT_BREAKER:
                self._autorun_refresh_counts[project_id] = 0
                self._autorun_cooldown_until.pop(project_id, None)
                studio_page = self._pages.get("chapter_studio")
                if studio_page is not None and hasattr(
                    studio_page, "set_autorun_project_started"
                ):
                    studio_page.set_autorun_project_started(project_id, False)
                self.show_priority_status(
                    f"⚠️ {project_id} 工作区状态同步失败超过 {_REFRESH_CIRCUIT_BREAKER} 次，"
                    "已停止连跑。请检查项目数据完整性后重新启动。",
                    10_000,
                    self._STATUS_WARNING,
                )
                _logger.error(
                    "Autorun refresh_context circuit breaker tripped | project=%s count=%d",
                    project_id,
                    count,
                )
                return
            if count == 20:
                self.show_priority_status(
                    f"{project_id} 等待状态同步较久，仍在后台重试",
                    5_000,
                    self._STATUS_INFO,
                )
            self.refresh_workspace(force=True)
            # Backoff: short delay early, longer delay once we are clearly
            # waiting for an out-of-band event (snapshot or job state).
            delay_ms = 700 if count < 10 else 1_500 if count < 30 else 3_000
            QTimer.singleShot(delay_ms, lambda pid=project_id: self._drive_project_autorun(pid))
            return
        self._autorun_refresh_counts[project_id] = 0

        chapter_number = int(getattr(snapshot, "chapter_number", 1) or 1)
        if action == "resolve_checkpoint":
            checkpoint = getattr(snapshot, "pending_checkpoint", None)
            option = getattr(decision, "option", None)
            if checkpoint is None or option is None:
                return
            # Retry cap + exponential backoff: a checkpoint that keeps re-appearing
            # means previous resolve attempts failed.  Without a guard the ticker
            # would resubmit the same resolve forever (root cause of the runaway
            # work-unit / event-ledger growth).
            self._submit_resolve_checkpoint_with_guard(
                project_id, chapter_number, checkpoint, option
            )
            return

        if action == "prepare_chapter":
            self._autorun_prepared_chapters.add((project_id, chapter_number))
            prepare_request = build_prepare_chapter_request(
                project_id=project_id,
                chapter_number=chapter_number,
                force=(
                    getattr(state, "mode", "") == "book_auto"
                    and not bool(getattr(state, "book_auto_skip_done", True))
                ),
                notes="",
                writing_mode=self._chapter_studio_writing_mode(project_id),
                repair_control_mode="ai_auto",
            )
            self._job_manager.submit_prepare_chapter(prepare_request, mock=self._mock_enabled)
            return

        if action == "advance_chapter":
            next_chapter = int(getattr(decision, "next_chapter", 0) or 0)
            if next_chapter <= 0:
                return
            target_project, target_chapter = self._clamp_chapter_studio_target(
                project_id,
                next_chapter,
            )
            self._set_autorun_chapter_number(target_project, target_chapter)
            self._set_chapter_number(target_project, target_chapter)
            if bool(getattr(state, "follow_autorun", False)):
                self._chapter_studio_project_id = target_project
                self._pages["chapter_studio"].focus_project(target_project, target_chapter)
            cooldown = (
                self._autorun_cooldown_seconds()
                if self._current_chapter_done(snapshot) and target_chapter > chapter_number
                else 0
            )
            if cooldown > 0:
                self._autorun_cooldown_until[target_project] = time.monotonic() + cooldown
            self.show_priority_status(
                f"全自动：{target_project} 推进到第 {target_chapter} 章",
                3000,
                self._STATUS_SUCCESS,
            )
            delay = max(
                int(getattr(decision, "delay_ms", 0) or 0),
                cooldown * 1000 if cooldown > 0 else 0,
            )
            QTimer.singleShot(delay, lambda pid=target_project: self._drive_project_autorun(pid))
            return

        if action in {"finish", "stop"}:
            self._autorun_cooldown_until.pop(project_id, None)
            studio_page = self._pages.get("chapter_studio")
            if studio_page is not None and hasattr(studio_page, "set_autorun_project_started"):
                studio_page.set_autorun_project_started(project_id, False)
            if action == "finish":
                self.show_priority_status(
                    f"{project_id} 章节连跑已完成",
                    4000,
                    self._STATUS_SUCCESS,
                )

    def _submit_resolve_checkpoint_with_guard(
        self,
        project_id: str,
        chapter_number: int,
        checkpoint: object,
        option: object,
    ) -> None:
        """Submit a checkpoint resolution with a retry cap and exponential backoff.

        A checkpoint that keeps re-appearing means previous resolve attempts failed
        (e.g. the finalize job errored and the checkpoint was never consumed).
        Without a guard the autorun ticker (~6 s) would resubmit the same resolve
        forever, accumulating work units and ledger events.  Here we:

        - count submissions per (project, chapter, checkpoint_id);
        - apply exponential backoff before each retry (attempt >= 2);
        - stop the project's autorun after ``autorun_max_checkpoint_resolve_attempts``
          submissions of the same checkpoint and alert the user.
        """
        checkpoint_id = str(getattr(checkpoint, "checkpoint_id", "") or "")
        option_id = str(getattr(option, "option_id", "") or "")
        if not checkpoint_id or not option_id:
            return

        self._load_persisted_resolve_attempts()
        key = (project_id, chapter_number)
        prev_id, prev_count = self._autorun_resolve_attempts.get(key, ("", 0))
        submitted_so_far = prev_count if prev_id == checkpoint_id else 0
        next_attempt = submitted_so_far + 1

        max_attempts = int(
            getattr(get_settings(), "autorun_max_checkpoint_resolve_attempts", 5) or 5
        )
        if next_attempt > max_attempts:
            # Exhausted the retry budget for this checkpoint: stop autorun and
            # surface the failure so the user can inspect reports and decide manually.
            self._autorun_resolve_attempts.pop(key, None)
            self._persist_resolve_attempts()
            self._autorun_cooldown_until.pop(project_id, None)
            studio_page = self._pages.get("chapter_studio")
            if studio_page is not None and hasattr(studio_page, "set_autorun_project_started"):
                studio_page.set_autorun_project_started(project_id, False)
            self.show_priority_status(
                f"⚠️ {project_id} 第{chapter_number}章归档重试{max_attempts}次仍未完成，"
                "已停止连跑，请人工检查",
                8_000,
                self._STATUS_WARNING,
            )
            _logger.warning(
                "Autorun resolve_checkpoint gave up | project=%s chapter=%d checkpoint=%s "
                "attempts=%d",
                project_id,
                chapter_number,
                checkpoint_id,
                max_attempts,
            )
            return

        # Record this submission (increment count) before dispatching so a deferred
        # retry is not double-counted by a concurrent ticker cycle.
        self._autorun_resolve_attempts[key] = (checkpoint_id, next_attempt)
        self._persist_resolve_attempts()

        if next_attempt > 1:
            # Exponential backoff for retries; block re-entry during the window via
            # the project cooldown so the ticker does not schedule a second submit.
            base_ms = int(
                getattr(get_settings(), "autorun_checkpoint_resolve_backoff_base_ms", 3000)
                or 3000
            )
            delay_ms = min(base_ms * (2 ** (next_attempt - 2)), 60_000)
            self._autorun_cooldown_until[project_id] = time.monotonic() + delay_ms / 1000.0 + 0.5
            _logger.info(
                "Autorun resolve_checkpoint retry #%d in %d ms | project=%s chapter=%d "
                "checkpoint=%s",
                next_attempt,
                delay_ms,
                project_id,
                chapter_number,
                checkpoint_id,
            )
            QTimer.singleShot(
                delay_ms,
                lambda pid=project_id, ch=chapter_number, cid=checkpoint_id, oid=option_id: (
                    self._submit_resolve_checkpoint_now(pid, ch, cid, oid)
                ),
            )
            return

        self._submit_resolve_checkpoint_now(project_id, chapter_number, checkpoint_id, option_id)

    def _submit_resolve_checkpoint_now(
        self,
        project_id: str,
        chapter_number: int,
        checkpoint_id: str,
        option_id: str,
    ) -> None:
        """Actually submit a checkpoint resolution (may run after a backoff delay).

        Re-reads the workspace snapshot and bails out if the checkpoint is no longer
        pending (already consumed or replaced while backing off), so a stale
        checkpoint is never submitted.
        """
        if self._workspace is None:
            return
        try:
            snapshot = self._workspace.get_chapter_workspace_snapshot(project_id, chapter_number)
        except Exception:
            snapshot = None
        pending = getattr(snapshot, "pending_checkpoint", None) if snapshot is not None else None
        if pending is None or getattr(pending, "checkpoint_id", None) != checkpoint_id:
            # Checkpoint already resolved/changed; let the next tick re-decide.
            return
        self._autorun_last_submitted_checkpoint[(project_id, chapter_number)] = checkpoint_id
        request = build_resolve_chapter_checkpoint_request(
            project_id=project_id,
            chapter_number=chapter_number,
            checkpoint_id=checkpoint_id,
            option_id=option_id,
            notes="",
            repair_control_mode="ai_auto",
        )
        self._job_manager.submit_resolve_chapter_checkpoint(
            request,
            mock=self._mock_enabled,
        )
        # Force a workspace refresh slightly after submission so the next
        # autopilot cycle (driven by job-change or the periodic ticker)
        # sees the updated pending_checkpoint state instead of looping in
        # ``refresh_context`` waiting for the old checkpoint to disappear.
        QTimer.singleShot(1_500, lambda: self.refresh_workspace(force=True))

    # ------------------------------------------------------------------
    # Resolve-attempt persistence (retry cap survives app restarts)
    # ------------------------------------------------------------------

    _AUTORUN_ATTEMPTS_FILENAME = "autorun_checkpoint_attempts.json"

    def _autorun_attempts_path(self) -> Path | None:
        """Return the persistence path for resolve attempts, or None if unavailable."""
        if self._snapshot is None:
            return None
        try:
            root = Path(str(self._snapshot.storage_root))
        except Exception:
            return None
        return root / "_global" / self._AUTORUN_ATTEMPTS_FILENAME

    def _load_persisted_resolve_attempts(self) -> None:
        """Best-effort load of persisted resolve attempts (once per session).

        Lets the retry cap survive app restarts: a chronically stuck checkpoint
        (same checkpoint_id) keeps its attempt count instead of getting a fresh
        budget on every launch.  New checkpoints always start fresh because the
        count is keyed by checkpoint_id.
        """
        if getattr(self, "_autorun_attempts_loaded", False):
            return
        self._autorun_attempts_loaded = True
        path = self._autorun_attempts_path()
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for key_str, value in (raw or {}).items():
                project_id, _, chapter_str = key_str.rpartition(":")
                if not project_id or not str(chapter_str).isdigit():
                    continue
                self._autorun_resolve_attempts[(project_id, int(chapter_str))] = (
                    str(value[0]),
                    int(value[1]),
                )
        except Exception:
            _logger.debug("Failed to load persisted autorun attempts", exc_info=True)

    def _persist_resolve_attempts(self) -> None:
        """Best-effort persist of resolve attempts so the cap survives restarts."""
        path = self._autorun_attempts_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            serializable = {
                f"{project_id}:{chapter}": [checkpoint_id, count]
                for (project_id, chapter), (checkpoint_id, count) in (
                    self._autorun_resolve_attempts.items()
                )
            }
            path.write_text(json.dumps(serializable, ensure_ascii=False), encoding="utf-8")
        except Exception:
            _logger.debug("Failed to persist autorun attempts", exc_info=True)
