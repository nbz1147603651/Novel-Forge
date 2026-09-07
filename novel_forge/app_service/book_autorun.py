"""Durable Engine-owned state machine for chapter and whole-book autorun.

The state machine is deliberately transport-neutral.  PySide and Nimo issue
start/pause/resolve commands and observe the projected session; only this
module decides which chapter job follows, which checkpoint option is safe,
how retries consume budget, and how an interrupted attempt is resumed.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from threading import RLock, Timer
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, Field

from novel_forge.app_service.contracts import (
    JobCommand,
    JobKind,
    JobRecord,
    JobState,
    utc_now_iso,
)
from novel_forge.common.runtime_identity import EngineRestartRequiredError
from novel_forge.persistence.filesystem import FileSystemStorage, atomic_write_json
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import canon_watermark, stale_chapter_cutoff

logger = logging.getLogger(__name__)


class BookAutorunStatus(StrEnum):
    WAITING_INIT = "waiting_init"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BookAutorunPhase(StrEnum):
    INITIALIZING = "initializing"
    PREPARING = "preparing"
    RESOLVING = "resolving"
    ADVANCING = "advancing"
    FINISHED = "finished"


class BookAutorunState(BaseModel):
    """Versioned project-owned recovery state for one autorun session."""

    version: int = 1
    owner: Literal["engine"] = "engine"
    session_id: str = Field(default_factory=lambda: uuid4().hex)
    launch_key: str = ""
    origin_job_id: str = ""
    project_id: str
    mode: Literal["chapter", "book"] = "book"
    status: BookAutorunStatus = BookAutorunStatus.RUNNING
    phase: BookAutorunPhase = BookAutorunPhase.PREPARING
    start_chapter: int = 1
    current_chapter: int = 1
    end_chapter: int = 1
    total_chapters: int = 1
    completed_chapters: list[int] = Field(default_factory=list)
    active_job_id: str = ""
    active_job_kind: str = ""
    resume_job_id: str = ""
    waiting_planning_job_id: str = ""
    checkpoint_id: str = ""
    checkpoint_type: str = ""
    checkpoint_payload: dict[str, Any] = Field(default_factory=dict)
    selected_option_id: str = ""
    checkpoint_attempts: dict[str, int] = Field(default_factory=dict)
    failure_attempts: dict[str, int] = Field(default_factory=dict)
    checkpoint_budget: int = 5
    failure_budget: int = 3
    total_failures: int = 0
    next_retry_at: str = ""
    wait_reason: Literal["", "engine_restart_required"] = ""
    last_error: str = ""
    last_failure_kind: str = ""
    recovery_target: Literal["", "plan", "draft", "wave", "manual", "semantic"] = ""
    writing_mode: Literal["whole_chapter", "scene_level"] = "whole_chapter"
    force: bool = False
    skip_done: bool = True
    mock: bool = False
    dispatch_sequence: int = 0
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


SubmitCommand = Callable[[JobCommand], JobRecord]
ResumeJob = Callable[[str, str, str], JobRecord]
GetJob = Callable[[str], JobRecord | None]
NotifyState = Callable[[BookAutorunState], None]
ResolveBounds = Callable[[str, int, str], tuple[int, int]]
ChapterReady = Callable[[str, int], bool]


class BookAutorunCoordinator:
    """Persist and drive one autorun state machine per project."""

    def __init__(
        self,
        *,
        storage_root: Path | None,
        submit_command: SubmitCommand,
        resume_job: ResumeJob,
        get_job: GetJob,
        notify_state: NotifyState | None = None,
        resolve_bounds: ResolveBounds | None = None,
        chapter_ready: ChapterReady | None = None,
        checkpoint_budget: int = 5,
        failure_budget: int = 3,
        checkpoint_backoff_ms: int = 3000,
        failure_backoff_ms: int = 3000,
    ) -> None:
        self._storage_root = storage_root
        self._submit_command = submit_command
        self._resume_job = resume_job
        self._get_job = get_job
        self._notify_state = notify_state or (lambda _state: None)
        self._resolve_bounds = resolve_bounds or self._default_bounds
        self._chapter_ready = chapter_ready or self._default_chapter_ready
        self._checkpoint_budget = max(1, int(checkpoint_budget))
        self._failure_budget = max(1, int(failure_budget))
        self._checkpoint_backoff_ms = max(0, int(checkpoint_backoff_ms))
        self._failure_backoff_ms = max(0, int(failure_backoff_ms))
        self._lock = RLock()
        self._states: dict[str, BookAutorunState] = {}
        self._timers: dict[str, Timer] = {}
        self._shutdown = False

    def refresh_defaults(
        self,
        *,
        checkpoint_budget: int,
        failure_budget: int,
        checkpoint_backoff_ms: int,
        failure_backoff_ms: int,
    ) -> None:
        """Apply saved defaults to future autorun sessions only."""

        with self._lock:
            self._checkpoint_budget = max(1, int(checkpoint_budget))
            self._failure_budget = max(1, int(failure_budget))
            self._checkpoint_backoff_ms = max(0, int(checkpoint_backoff_ms))
            self._failure_backoff_ms = max(0, int(failure_backoff_ms))

    def start(
        self,
        *,
        project_id: str,
        chapter_number: int,
        mode: Literal["chapter", "book"] = "book",
        launch_key: str = "",
        writing_mode: Literal["whole_chapter", "scene_level"] = "whole_chapter",
        force: bool = False,
        skip_done: bool = True,
        mock: bool = False,
    ) -> JobRecord | None:
        """Create, resume, or idempotently observe an autorun session."""

        project_id = project_id.strip()
        if not project_id:
            raise ValueError("project_id is required")
        chapter_number = max(1, int(chapter_number))
        from novel_forge.persistence.authoring_store import AuthoringStore

        policy = (
            AuthoringStore(self._storage_root / project_id).policy() if self._storage_root else None
        )
        if policy is not None and self._storage_root is not None:
            AuthoringStore(self._storage_root / project_id).require(
                "prepare", chapter_number, explicit=True
            )
        with self._lock:
            current = self._states.get(project_id) or self._load(project_id)
            if current is not None and current.status in {
                BookAutorunStatus.WAITING_INIT,
                BookAutorunStatus.RUNNING,
                BookAutorunStatus.RETRY_WAIT,
            }:
                if not launch_key or not current.launch_key or current.launch_key == launch_key:
                    if current.wait_reason == "engine_restart_required":
                        return self._drive_locked(current)
                    return self._get_job(current.active_job_id) if current.active_job_id else None
                raise RuntimeError("该项目已有另一个 Engine 连跑会话")
            if current is not None and current.status in {
                BookAutorunStatus.PAUSED,
                BookAutorunStatus.CANCELLED,
            }:
                if current.current_chapter == chapter_number and current.mode == mode:
                    if policy is not None:
                        current.end_chapter = min(current.end_chapter, policy.end_chapter)
                    current.status = BookAutorunStatus.RUNNING
                    current.last_error = ""
                    current.last_failure_kind = ""
                    current.recovery_target = ""
                    current.next_retry_at = ""
                    current.launch_key = launch_key or current.launch_key
                    current.updated_at = utc_now_iso()
                    self._store(current)
                    return self._drive_locked(current)

            end_chapter, total_chapters = self._resolve_bounds(project_id, chapter_number, mode)
            if policy is not None:
                end_chapter = min(end_chapter, policy.end_chapter)
            state = BookAutorunState(
                project_id=project_id,
                launch_key=launch_key,
                mode=mode,
                status=BookAutorunStatus.RUNNING,
                phase=BookAutorunPhase.PREPARING,
                start_chapter=chapter_number,
                current_chapter=chapter_number,
                end_chapter=max(chapter_number, end_chapter),
                total_chapters=max(chapter_number, total_chapters),
                checkpoint_budget=self._checkpoint_budget,
                failure_budget=self._failure_budget,
                writing_mode=writing_mode,
                force=force,
                skip_done=skip_done,
                mock=mock,
            )
            self._states[project_id] = state
            self._store(state)
            return self._drive_locked(state)

    def register_init(
        self,
        *,
        record: JobRecord,
        launch_key: str,
        total_chapters: int,
        writing_mode: Literal["whole_chapter", "scene_level"] = "whole_chapter",
        mock: bool = False,
    ) -> BookAutorunState:
        """Attach an autorun session to an initialization attempt before it starts."""

        project_id = record.project_id.strip()
        with self._lock:
            existing = self._states.get(project_id) or self._load(project_id)
            if (
                existing is not None
                and existing.phase == BookAutorunPhase.INITIALIZING
                and (not launch_key or not existing.launch_key or existing.launch_key == launch_key)
            ):
                existing.status = BookAutorunStatus.WAITING_INIT
                existing.origin_job_id = existing.origin_job_id or record.job_id
                existing.active_job_id = record.job_id
                existing.active_job_kind = JobKind.INIT_LONG.value
                existing.resume_job_id = ""
                existing.updated_at = utc_now_iso()
                self._store(existing)
                return existing.model_copy(deep=True)
            state = BookAutorunState(
                project_id=project_id,
                launch_key=launch_key,
                origin_job_id=record.job_id,
                mode="book",
                status=BookAutorunStatus.WAITING_INIT,
                phase=BookAutorunPhase.INITIALIZING,
                start_chapter=1,
                current_chapter=1,
                end_chapter=max(1, int(total_chapters or 1)),
                total_chapters=max(1, int(total_chapters or 1)),
                active_job_id=record.job_id,
                active_job_kind=JobKind.INIT_LONG.value,
                checkpoint_budget=self._checkpoint_budget,
                failure_budget=self._failure_budget,
                writing_mode=writing_mode,
                mock=mock,
            )
            self._states[project_id] = state
            self._store(state)
            return state.model_copy(deep=True)

    def state(self, project_id: str) -> BookAutorunState | None:
        with self._lock:
            state = self._states.get(project_id) or self._load(project_id)
            return state.model_copy(deep=True) if state is not None else None

    def active_project_ids(self) -> list[str]:
        with self._lock:
            return sorted(
                project_id
                for project_id, state in self._states.items()
                if state.status
                in {
                    BookAutorunStatus.WAITING_INIT,
                    BookAutorunStatus.RUNNING,
                    BookAutorunStatus.RETRY_WAIT,
                    BookAutorunStatus.PAUSED,
                }
            )

    def pause(self, project_id: str, *, reason: str = "用户已暂停连跑") -> str:
        """Pause orchestration and return the active job id for cancellation."""

        with self._lock:
            state = self._states.get(project_id) or self._load(project_id)
            if state is None:
                return ""
            self._cancel_timer_locked(project_id)
            active_job_id = state.active_job_id
            state.status = BookAutorunStatus.PAUSED
            state.last_error = reason
            state.waiting_planning_job_id = ""
            state.next_retry_at = ""
            if active_job_id:
                state.resume_job_id = active_job_id
            state.active_job_id = ""
            state.active_job_kind = ""
            state.updated_at = utc_now_iso()
            self._store(state)
            return active_job_id

    def reset_after_chapter_cleanup(self, project_id: str, *, from_chapter: int) -> bool:
        """Invalidate resumable autorun state that points at deleted chapter artifacts.

        Chapter cleanup is an explicit destructive boundary.  A failed or
        paused autorun must not keep an old checkpoint, retry intent, or error
        banner that could later resume against files the cleanup removed.
        Historical task/error records remain separate evidence.
        """

        cutoff = max(1, int(from_chapter))
        with self._lock:
            state = self._states.get(project_id) or self._load(project_id)
            if state is None:
                return False
            touches_session = state.current_chapter >= cutoff or any(
                chapter >= cutoff for chapter in state.completed_chapters
            )
            if not touches_session:
                return False
            self._cancel_timer_locked(project_id)
            state.status = BookAutorunStatus.CANCELLED
            state.phase = BookAutorunPhase.PREPARING
            state.current_chapter = cutoff
            state.completed_chapters = [
                chapter for chapter in state.completed_chapters if chapter < cutoff
            ]
            state.active_job_id = ""
            state.active_job_kind = ""
            state.resume_job_id = ""
            state.waiting_planning_job_id = ""
            state.checkpoint_id = ""
            state.checkpoint_type = ""
            state.checkpoint_payload = {}
            state.selected_option_id = ""
            state.checkpoint_attempts = {}
            state.failure_attempts = {}
            state.total_failures = 0
            state.next_retry_at = ""
            state.wait_reason = ""
            state.last_error = f"已从第 {cutoff} 章起清理产物；需明确重新启动"
            state.updated_at = utc_now_iso()
            self._store(state)
            return True

    def resolve_checkpoint(
        self,
        *,
        project_id: str,
        checkpoint_id: str,
        option_id: str,
        notes: str = "",
        force: bool = False,
        authoring_approval_id: str = "",
        job_id: str = "",
    ) -> JobRecord | None:
        """Resolve a paused Engine checkpoint through the same durable session."""

        with self._lock:
            state = self._states.get(project_id) or self._load(project_id)
            if state is None or state.checkpoint_id != checkpoint_id:
                return None
            return self._dispatch_checkpoint_locked(
                state,
                option_id=option_id,
                notes=notes,
                force=force,
                authoring_approval_id=authoring_approval_id,
                job_id=job_id,
            )

    def on_job_paused(self, record: JobRecord) -> None:
        payload = record.result if isinstance(record.result, dict) else {}
        with self._lock:
            state = self._state_for_job_locked(record.job_id)
            if state is None:
                return
            state.active_job_id = ""
            state.active_job_kind = ""
            state.resume_job_id = ""
            checkpoint = payload.get("checkpoint")
            if not isinstance(checkpoint, dict):
                self._fail_locked(state, "任务暂停但未返回可恢复检查点")
                return
            state.checkpoint_id = str(checkpoint.get("checkpoint_id") or "").strip()
            state.checkpoint_type = str(checkpoint.get("checkpoint_type") or "").strip()
            state.checkpoint_payload = checkpoint
            if state.checkpoint_type == "planning_wait":
                state.waiting_planning_job_id = str(checkpoint.get("waiting_task_id") or "")
                state.checkpoint_id = ""
                state.checkpoint_type = ""
                state.checkpoint_payload = {}
                state.phase = BookAutorunPhase.PREPARING
                state.status = BookAutorunStatus.RETRY_WAIT
                state.last_error = str(checkpoint.get("summary") or "等待规划任务")
                self._store(state)
                dependency = self._get_job(state.waiting_planning_job_id)
                if dependency is not None:
                    self.on_planning_finished(dependency)
                return
            state.phase = BookAutorunPhase.RESOLVING
            state.updated_at = utc_now_iso()
            self._store(state)
            option_id = self._recommended_option(checkpoint)
            if not option_id:
                state.status = BookAutorunStatus.PAUSED
                state.last_error = "检查点需要人工裁决"
                self._store(state)
                return
            prior_attempts = state.checkpoint_attempts.get(state.checkpoint_id, 0)
            if prior_attempts > 0:
                state.selected_option_id = option_id
                self._schedule_retry_locked(
                    state,
                    attempts=prior_attempts + 1,
                    checkpoint=True,
                )
                return
            self._dispatch_checkpoint_locked(state, option_id=option_id)

    def on_job_succeeded(self, record: JobRecord) -> None:
        payload = record.result if isinstance(record.result, dict) else {}
        with self._lock:
            state = self._state_for_job_locked(record.job_id)
            if state is None:
                return
            state.active_job_id = ""
            state.active_job_kind = ""
            state.resume_job_id = ""
            state.next_retry_at = ""
            state.last_error = ""
            state.last_failure_kind = ""
            state.recovery_target = ""
            if state.phase == BookAutorunPhase.INITIALIZING:
                try:
                    end_chapter, total = self._resolve_bounds(state.project_id, 1, "book")
                except Exception:
                    end_chapter, total = state.end_chapter, state.total_chapters
                # The init request is authoritative while project projections
                # may still be warming up; never shrink its declared range.
                state.end_chapter = max(state.end_chapter, 1, end_chapter)
                state.total_chapters = max(state.total_chapters, state.end_chapter, total)
                state.status = BookAutorunStatus.RUNNING
                state.phase = BookAutorunPhase.PREPARING
                state.updated_at = utc_now_iso()
                self._store(state)
                self._drive_locked(state)
                return
            if str(payload.get("status") or "") == "needs_decision":
                # Defensive: JobService normally routes this through on_job_paused.
                paused = record.model_copy(update={"status": JobState.PAUSED})
                self.on_job_paused(paused)
                return
            if not self._chapter_ready(state.project_id, state.current_chapter):
                self._retry_without_resume_locked(
                    state,
                    "章节任务返回完成，但归档/Canon 完整性验证未通过",
                )
                return
            self._complete_current_chapter_locked(state)

    def on_planning_finished(self, record: JobRecord) -> None:
        with self._lock:
            state = self._states.get(record.project_id) or self._load(record.project_id)
            if (
                state is None
                or state.waiting_planning_job_id != record.job_id
                or state.status not in {BookAutorunStatus.RETRY_WAIT, BookAutorunStatus.PAUSED}
            ):
                return
            if record.status not in {JobState.SUCCEEDED, JobState.FAILED, JobState.PAUSED}:
                return
            payload = record.result if isinstance(record.result, dict) else {}
            # Workspace commands may wrap their domain result.
            planning = payload if "status" in payload else payload.get("result", {})
            if record.status != JobState.SUCCEEDED or planning.get("status") != "published":
                state.status = BookAutorunStatus.PAUSED
                state.last_error = record.error or "规划候选待批准；未推进下一章"
                self._store(state)
                return
            state.waiting_planning_job_id = ""
            state.last_error = ""
            state.status = BookAutorunStatus.RUNNING
            self._store(state)
            self._drive_locked(state)

    def on_job_failed(self, record: JobRecord) -> None:
        with self._lock:
            state = self._state_for_job_locked(record.job_id)
            if state is None:
                return
            state.active_job_id = ""
            state.active_job_kind = ""
            state.resume_job_id = record.job_id
            error = record.error or str(record.error_summary.get("summary") or "任务失败")
            context = record.error_summary.get("context")
            error_context = context if isinstance(context, dict) else {}
            state.last_failure_kind = str(
                error_context.get("violation_kind")
                or record.error_summary.get("cause_code")
                or ""
            )
            recovery_target = str(error_context.get("replan_target") or "")
            state.recovery_target = cast(
                Literal["", "plan", "draft", "wave", "manual", "semantic"],
                recovery_target
                if recovery_target in {"", "plan", "draft", "wave", "manual", "semantic"}
                else "",
            )
            if not self._is_retryable(record.error_summary):
                self._fail_locked(state, error)
                return
            key = self._failure_key(state)
            attempts = state.failure_attempts.get(key, 0) + 1
            state.failure_attempts[key] = attempts
            state.total_failures += 1
            state.last_error = error
            if attempts > state.failure_budget:
                self._fail_locked(state, f"{error}（失败预算已耗尽）")
                return
            self._schedule_retry_locked(state, attempts=attempts, checkpoint=False)

    def on_job_cancelled(self, job_id: str, *, for_restart: bool) -> None:
        with self._lock:
            state = self._state_for_job_locked(job_id)
            if state is None:
                return
            state.active_job_id = ""
            state.active_job_kind = ""
            state.resume_job_id = job_id
            state.status = BookAutorunStatus.RETRY_WAIT if for_restart else BookAutorunStatus.PAUSED
            state.last_error = "Engine 重启后将恢复" if for_restart else "用户已暂停连跑"
            state.updated_at = utc_now_iso()
            self._store(state)

    def recover(self) -> None:
        """Reconcile persisted sessions with durable job history on startup."""

        if self._storage_root is None or not self._storage_root.exists():
            return
        try:
            project_ids = [item.name for item in self._storage_root.iterdir() if item.is_dir()]
        except OSError:
            return
        for project_id in project_ids:
            with self._lock:
                state = self._load(project_id)
                if state is not None:
                    self._recover_legacy_restart_wait_locked(state)
                if state is None or state.status not in {
                    BookAutorunStatus.WAITING_INIT,
                    BookAutorunStatus.RUNNING,
                    BookAutorunStatus.RETRY_WAIT,
                }:
                    continue
                record = self._get_job(state.active_job_id) if state.active_job_id else None
                if record is None and state.active_job_id:
                    state.resume_job_id = state.active_job_id
                    state.active_job_id = ""
                    state.active_job_kind = ""
                    state.status = BookAutorunStatus.RETRY_WAIT
                    state.last_error = "检测到未完成的进程内任务，准备从持久化意图恢复"
                    self._store(state)
                    self._schedule_retry_locked(state, attempts=1, checkpoint=False, delay_ms=0)
                    continue
                if record is None:
                    remaining_ms = self._remaining_retry_ms(state.next_retry_at)
                    if state.status == BookAutorunStatus.RETRY_WAIT and remaining_ms > 0:
                        self._schedule_retry_locked(
                            state,
                            attempts=1,
                            checkpoint=False,
                            delay_ms=remaining_ms,
                        )
                        continue
                    self._drive_locked(state)
                    continue
                if record.status == JobState.SUCCEEDED:
                    self.on_job_succeeded(record)
                elif record.status == JobState.PAUSED:
                    self.on_job_paused(record)
                elif record.status == JobState.FAILED:
                    self.on_job_failed(record)

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown = True
            for project_id in list(self._timers):
                self._cancel_timer_locked(project_id)

    def _drive_locked(self, state: BookAutorunState) -> JobRecord | None:
        if self._shutdown or state.status not in {
            BookAutorunStatus.RUNNING,
            BookAutorunStatus.RETRY_WAIT,
        }:
            return None
        if state.active_job_id:
            return self._get_job(state.active_job_id)
        if state.waiting_planning_job_id:
            dependency = self._get_job(state.waiting_planning_job_id)
            if dependency is not None:
                self.on_planning_finished(dependency)
            return None
        if self._storage_root is not None:
            from novel_forge.core.authoring import authoring_permission
            from novel_forge.persistence.authoring_store import AuthoringStore

            policy = AuthoringStore(self._storage_root / state.project_id).policy()
            if policy is not None:
                from novel_forge.workspace.authoring_control import checkpoint_action

                action = (
                    checkpoint_action(
                        state.selected_option_id
                        or self._recommended_option(state.checkpoint_payload)
                    )
                    if state.checkpoint_id
                    else "prepare"
                )
                permission = authoring_permission(policy, action, state.current_chapter)
                if not permission.allowed:
                    state.status = BookAutorunStatus.PAUSED
                    state.last_error = permission.reason
                    self._store(state)
                    return None
        if state.resume_job_id:
            return self._resume_locked(state)
        if state.checkpoint_id:
            option_id = state.selected_option_id or self._recommended_option(
                state.checkpoint_payload
            )
            if not option_id:
                state.status = BookAutorunStatus.PAUSED
                state.last_error = "检查点需要人工裁决"
                self._store(state)
                return None
            return self._dispatch_checkpoint_locked(state, option_id=option_id)
        if state.current_chapter > state.end_chapter:
            self._finish_locked(state)
            return None
        if (
            state.skip_done
            and not state.force
            and self._chapter_ready(state.project_id, state.current_chapter)
        ):
            self._complete_current_chapter_locked(state)
            return self._get_job(state.active_job_id) if state.active_job_id else None
        state.status = BookAutorunStatus.RUNNING
        state.phase = BookAutorunPhase.PREPARING
        return self._submit_new_locked(
            state,
            JobCommand(
                job_id=self._next_job_id(state, "prepare"),
                kind=JobKind.PREPARE_CHAPTER,
                project_id=state.project_id,
                payload={
                    "project_id": state.project_id,
                    "chapter_number": state.current_chapter,
                    "force": state.force,
                    "notes": "",
                    "rewrite_strategy": "auto",
                    "writing_mode": state.writing_mode,
                },
                mock=state.mock,
                metadata=self._metadata(state, "prepare"),
            ),
        )

    def _dispatch_checkpoint_locked(
        self,
        state: BookAutorunState,
        *,
        option_id: str,
        notes: str = "",
        force: bool = False,
        authoring_approval_id: str = "",
        job_id: str = "",
    ) -> JobRecord | None:
        checkpoint_id = state.checkpoint_id
        if not checkpoint_id:
            return None
        if self._storage_root is not None:
            from novel_forge.core.authoring import authoring_permission
            from novel_forge.persistence.authoring_store import AuthoringStore
            from novel_forge.workspace.authoring_control import (
                checkpoint_action,
                checkpoint_command_version,
            )

            policy = AuthoringStore(self._storage_root / state.project_id).policy()
            if policy is not None:
                permission = authoring_permission(
                    policy,
                    checkpoint_action(option_id),
                    state.current_chapter,
                    approved=AuthoringStore(self._storage_root / state.project_id).approval_matches(
                        authoring_approval_id,
                        checkpoint_action(option_id),
                        state.current_chapter,
                        checkpoint_command_version(
                            self._storage_root / state.project_id,
                            state.current_chapter,
                            option_id,
                            notes,
                        ),
                    ),
                )
                if not permission.allowed:
                    state.status = BookAutorunStatus.PAUSED
                    state.last_error = permission.reason
                    state.updated_at = utc_now_iso()
                    self._store(state)
                    return None
        attempts = state.checkpoint_attempts.get(checkpoint_id, 0) + 1
        state.checkpoint_attempts[checkpoint_id] = attempts
        if attempts > state.checkpoint_budget:
            self._fail_locked(state, "同一检查点自动裁决预算已耗尽，请人工处理")
            return None
        state.status = BookAutorunStatus.RUNNING
        state.phase = BookAutorunPhase.RESOLVING
        state.selected_option_id = option_id
        state.last_error = ""
        state.next_retry_at = ""
        finalize = option_id.endswith("_finalize") or option_id == "accept_and_finalize"
        kind = (
            JobKind.RESOLVE_CHAPTER_CHECKPOINT_FINALIZE
            if finalize
            else JobKind.RESOLVE_CHAPTER_CHECKPOINT
        )
        command = JobCommand(
            job_id=job_id or self._next_job_id(state, f"resolve:{checkpoint_id}:{attempts}"),
            kind=kind,
            record_kind=kind,
            project_id=state.project_id,
            payload={
                "project_id": state.project_id,
                "chapter_number": state.current_chapter,
                "checkpoint_id": checkpoint_id,
                "option_id": option_id,
                "notes": notes,
                "force": force or state.force,
                **(
                    {"authoring_approval_id": authoring_approval_id}
                    if authoring_approval_id
                    else {}
                ),
            },
            mock=state.mock,
            metadata=self._metadata(state, "resolve"),
        )
        return self._submit_new_locked(state, command)

    def _submit_new_locked(self, state: BookAutorunState, command: JobCommand) -> JobRecord | None:
        state.wait_reason = ""
        state.active_job_id = command.job_id
        state.active_job_kind = command.kind.value
        state.resume_job_id = ""
        state.updated_at = utc_now_iso()
        self._store(state)
        try:
            record = self._submit_command(command)
        except EngineRestartRequiredError:
            self._refund_checkpoint_dispatch(state, command)
            self._wait_for_engine_restart_locked(state)
            return None
        except Exception as exc:
            self._refund_checkpoint_dispatch(state, command)
            state.active_job_id = ""
            state.active_job_kind = ""
            state.last_error = str(exc) or type(exc).__name__
            key = self._failure_key(state, suffix="dispatch")
            attempts = state.failure_attempts.get(key, 0) + 1
            state.failure_attempts[key] = attempts
            state.total_failures += 1
            if attempts > state.failure_budget:
                self._fail_locked(state, f"{state.last_error}（提交失败预算已耗尽）")
                return None
            self._schedule_retry_locked(state, attempts=attempts, checkpoint=False)
            return None
        if record.job_id != command.job_id:
            self._refund_checkpoint_dispatch(state, command)
            state.active_job_id = ""
            state.active_job_kind = ""
            state.status = BookAutorunStatus.PAUSED
            state.last_error = "项目存在其他写任务，Engine 连跑已暂停"
            self._store(state)
            return record
        return record

    def _resume_locked(self, state: BookAutorunState) -> JobRecord | None:
        source_job_id = state.resume_job_id
        if not source_job_id:
            return None
        if self._storage_root is not None:
            from novel_forge.persistence.authoring_store import AuthoringStore

            policy = AuthoringStore(self._storage_root / state.project_id).policy()
            if policy is not None and (
                policy.stopped
                or policy.mode != "authorized_auto"
                or not policy.start_chapter <= state.current_chapter <= policy.end_chapter
            ):
                state.status = BookAutorunStatus.PAUSED
                state.last_error = "恢复需要重新核验作者授权与候选版本"
                self._store(state)
                return None
        resumed_job_id = self._next_job_id(state, f"resume:{source_job_id}")
        state.wait_reason = ""
        state.status = BookAutorunStatus.RUNNING
        state.active_job_id = resumed_job_id
        state.active_job_kind = "resume"
        state.next_retry_at = ""
        state.updated_at = utc_now_iso()
        self._store(state)
        try:
            record = self._resume_job(state.project_id, source_job_id, resumed_job_id)
        except EngineRestartRequiredError:
            self._wait_for_engine_restart_locked(state)
            return None
        except Exception as exc:
            state.active_job_id = ""
            state.active_job_kind = ""
            self._fail_locked(state, f"无法恢复持久化任务意图：{exc}")
            return None
        if state.active_job_id == resumed_job_id:
            state.resume_job_id = ""
            state.active_job_id = record.job_id
            state.active_job_kind = (
                record.kind.value if isinstance(record.kind, JobKind) else str(record.kind)
            )
            state.updated_at = utc_now_iso()
            self._store(state)
        return record

    @staticmethod
    def _refund_checkpoint_dispatch(state: BookAutorunState, command: JobCommand) -> None:
        """Only accepted resolver jobs consume the automatic decision budget."""

        checkpoint_id = str(command.payload.get("checkpoint_id") or "")
        if checkpoint_id and checkpoint_id in state.checkpoint_attempts:
            attempts = state.checkpoint_attempts[checkpoint_id] - 1
            if attempts > 0:
                state.checkpoint_attempts[checkpoint_id] = attempts
            else:
                state.checkpoint_attempts.pop(checkpoint_id)

    def _wait_for_engine_restart_locked(self, state: BookAutorunState) -> None:
        """Park durable intent until restart, without timers or failed attempts."""

        self._cancel_timer_locked(state.project_id)
        state.status = BookAutorunStatus.RETRY_WAIT
        state.wait_reason = "engine_restart_required"
        state.active_job_id = ""
        state.active_job_kind = ""
        state.next_retry_at = ""
        state.last_error = (
            "后端源码已更新，等待安全重启；进度已保存，重启后自动继续，不消耗失败预算。"
        )
        self._store(state)

    def _recover_legacy_restart_wait_locked(self, state: BookAutorunState) -> None:
        """Recover the old dispatch-only exhaustion bug, never genuine failures."""

        key = self._failure_key(state, suffix="dispatch")
        attempts = state.failure_attempts.get(key, 0)
        if (
            state.status == BookAutorunStatus.FAILED
            and state.phase == BookAutorunPhase.PREPARING
            and not state.active_job_id
            and not state.resume_job_id
            and not state.checkpoint_id
            and state.last_error
            == "本地 Engine 源码已更新，请安全重启后端后再提交新任务。（提交失败预算已耗尽）"
            and attempts == state.total_failures
            and attempts > state.failure_budget
            and len(state.failure_attempts) == 1
        ):
            state.failure_attempts.clear()
            state.total_failures = 0
            self._wait_for_engine_restart_locked(state)

    def _complete_current_chapter_locked(self, state: BookAutorunState) -> None:
        if state.current_chapter not in state.completed_chapters:
            state.completed_chapters.append(state.current_chapter)
            state.completed_chapters.sort()
        state.checkpoint_id = ""
        state.checkpoint_type = ""
        state.checkpoint_payload = {}
        state.selected_option_id = ""
        if state.mode == "chapter" or state.current_chapter >= state.end_chapter:
            self._finish_locked(state)
            return
        state.current_chapter += 1
        state.phase = BookAutorunPhase.ADVANCING
        state.status = BookAutorunStatus.RUNNING
        state.updated_at = utc_now_iso()
        self._store(state)
        self._drive_locked(state)

    def _retry_without_resume_locked(self, state: BookAutorunState, error: str) -> None:
        key = self._failure_key(state, suffix="verification")
        attempts = state.failure_attempts.get(key, 0) + 1
        state.failure_attempts[key] = attempts
        state.total_failures += 1
        state.last_error = error
        state.resume_job_id = ""
        if attempts > state.failure_budget:
            self._fail_locked(state, f"{error}（失败预算已耗尽）")
            return
        self._schedule_retry_locked(state, attempts=attempts, checkpoint=False)

    def _schedule_retry_locked(
        self,
        state: BookAutorunState,
        *,
        attempts: int,
        checkpoint: bool,
        delay_ms: int | None = None,
    ) -> None:
        base = self._checkpoint_backoff_ms if checkpoint else self._failure_backoff_ms
        calculated = min(60_000, base * (2 ** max(0, attempts - 1)))
        delay = calculated if delay_ms is None else max(0, delay_ms)
        state.status = BookAutorunStatus.RETRY_WAIT
        state.next_retry_at = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() + delay / 1000,
            tz=timezone.utc,
        ).isoformat()
        state.updated_at = utc_now_iso()
        self._store(state)
        self._cancel_timer_locked(state.project_id)
        timer = Timer(delay / 1000, self._retry_project, args=(state.project_id,))
        timer.daemon = True
        self._timers[state.project_id] = timer
        timer.start()

    def _retry_project(self, project_id: str) -> None:
        with self._lock:
            self._timers.pop(project_id, None)
            state = self._states.get(project_id)
            if state is None or state.status != BookAutorunStatus.RETRY_WAIT or self._shutdown:
                return
            state.status = BookAutorunStatus.RUNNING
            state.updated_at = utc_now_iso()
            self._store(state)
            self._drive_locked(state)

    def _finish_locked(self, state: BookAutorunState) -> None:
        state.status = BookAutorunStatus.COMPLETED
        state.phase = BookAutorunPhase.FINISHED
        state.active_job_id = ""
        state.active_job_kind = ""
        state.resume_job_id = ""
        state.checkpoint_id = ""
        state.checkpoint_type = ""
        state.checkpoint_payload = {}
        state.next_retry_at = ""
        state.last_error = ""
        state.last_failure_kind = ""
        state.recovery_target = ""
        state.wait_reason = ""
        state.updated_at = utc_now_iso()
        self._store(state)

    def _fail_locked(self, state: BookAutorunState, error: str) -> None:
        self._cancel_timer_locked(state.project_id)
        state.status = BookAutorunStatus.FAILED
        state.active_job_id = ""
        state.active_job_kind = ""
        state.next_retry_at = ""
        state.last_error = error
        state.wait_reason = ""
        state.updated_at = utc_now_iso()
        self._store(state)

    def _state_for_job_locked(self, job_id: str) -> BookAutorunState | None:
        for state in self._states.values():
            if job_id in {state.active_job_id, state.resume_job_id}:
                return state
        return None

    def _next_job_id(self, state: BookAutorunState, operation: str) -> str:
        state.dispatch_sequence += 1
        raw = f"{state.session_id}:{state.dispatch_sequence}:{state.current_chapter}:{operation}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _metadata(state: BookAutorunState, stage: str) -> dict[str, Any]:
        return {
            "workflow_type": "long_chapter",
            "run_mode": "autorun",
            "autorun_scope": state.mode,
            "autorun_session_id": state.session_id,
            "autorun_stage": stage,
            "autorun_chapter": state.current_chapter,
            "autorun_chained_from": state.origin_job_id,
        }

    @staticmethod
    def _recommended_option(checkpoint: dict[str, Any]) -> str:
        options = [item for item in list(checkpoint.get("options") or []) if isinstance(item, dict)]
        recommended = [item for item in options if bool(item.get("is_recommended"))]
        if any(
            item.get("option_id") == "pause_for_human"
            or item.get("semantic_tag") == "pause_for_human"
            for item in recommended
        ):
            return ""
        ordered = recommended + [item for item in options if item not in recommended]
        for item in ordered:
            option_id = str(item.get("option_id") or "").strip()
            semantic_tag = str(item.get("semantic_tag") or "").strip()
            if option_id and option_id != "pause_for_human" and semantic_tag != "pause_for_human":
                return option_id
        return ""

    @staticmethod
    def _is_retryable(summary: dict[str, Any]) -> bool:
        if "retryable" in summary:
            return bool(summary.get("retryable"))
        error_type = str(summary.get("exception_type") or "").casefold()
        error_code = str(summary.get("error_code") or "").casefold()
        blocked = (
            "authentication",
            "configuration",
            "budgetexceeded",
            "contentfilter",
            "input_integrity",
        )
        return not any(token in error_type or token in error_code for token in blocked)

    @staticmethod
    def _failure_key(state: BookAutorunState, *, suffix: str = "") -> str:
        base = f"{state.current_chapter}:{state.phase.value}"
        return f"{base}:{suffix}" if suffix else base

    def _path(self, project_id: str) -> Path | None:
        if self._storage_root is None:
            return None
        return ProjectLayout(self._storage_root / project_id).engine_autorun_session_path

    def _load(self, project_id: str) -> BookAutorunState | None:
        path = self._path(project_id)
        if path is None or not path.exists():
            return None
        try:
            state = BookAutorunState.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            logger.warning("Ignoring invalid autorun session %s", path, exc_info=True)
            return None
        self._states[project_id] = state
        return state

    def _store(self, state: BookAutorunState) -> None:
        state.updated_at = utc_now_iso()
        self._states[state.project_id] = state
        path = self._path(state.project_id)
        if path is not None:
            atomic_write_json(path, state.model_dump(mode="json"))
        try:
            self._notify_state(state.model_copy(deep=True))
        except Exception:
            logger.debug("Autorun state notification failed", exc_info=True)

    def _cancel_timer_locked(self, project_id: str) -> None:
        timer = self._timers.pop(project_id, None)
        if timer is not None:
            timer.cancel()

    @staticmethod
    def _remaining_retry_ms(value: str) -> int:
        if not value:
            return 0
        try:
            target = datetime.fromisoformat(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
        except ValueError:
            return 0
        return max(
            0,
            int(
                (target.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
                * 1000
            ),
        )

    def _default_bounds(self, project_id: str, chapter_number: int, mode: str) -> tuple[int, int]:
        if self._storage_root is None:
            return chapter_number, chapter_number
        from novel_forge.workspace.projects import ProjectInspector

        try:
            detail = ProjectInspector(FileSystemStorage(self._storage_root)).get_project_detail(
                project_id
            )
        except Exception:
            logger.debug(
                "Autorun bounds unavailable for %s; using current chapter",
                project_id,
                exc_info=True,
            )
            return chapter_number, chapter_number
        total = max(chapter_number, int(detail.total_chapters or chapter_number))
        return (chapter_number if mode == "chapter" else total), total

    def _default_chapter_ready(self, project_id: str, chapter_number: int) -> bool:
        if self._storage_root is None:
            return False
        storage = FileSystemStorage(self._storage_root)
        layout = ProjectLayout(self._storage_root / project_id)
        cutoff = stale_chapter_cutoff(storage, layout)
        from novel_forge.core.config import get_settings
        from novel_forge.persistence.authoring_store import AuthoringStore
        from novel_forge.pipeline.finalization_manifest import tracked_finalization_ready

        return (
            layout.chapter_path(chapter_number).exists()
            and layout.chapter_exit_state_path(chapter_number).exists()
            and layout.creative_report_path(chapter_number).exists()
            and canon_watermark(storage, layout) >= chapter_number
            and (cutoff is None or chapter_number < cutoff)
            and tracked_finalization_ready(
                storage,
                layout,
                chapter_number,
                require_state=get_settings().narrative_state_required,
                allow_legacy=AuthoringStore(layout.root).policy() is None,
            )
        )
