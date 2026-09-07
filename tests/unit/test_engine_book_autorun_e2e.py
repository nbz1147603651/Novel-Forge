from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

from novel_forge.app_service.book_autorun import (
    BookAutorunCoordinator,
    BookAutorunState,
    BookAutorunStatus,
)
from novel_forge.app_service.contracts import JobCommand, JobKind, JobRecord, JobState
from novel_forge.app_service.engine_novel import project_book_autorun_view
from novel_forge.app_service.job_service import JobService
from novel_forge.app_service.workspace_commands import PreparedCommand
from novel_forge.common.runtime_identity import EngineRestartRequiredError
from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.persistence.models import ProjectLayout


class _Runtime:
    async def shutdown(self) -> None:
        return None


class _AutorunExecutor:
    def __init__(
        self,
        storage_root: Path,
        *,
        fail_first_prepare: bool = False,
        block_first_prepare: bool = False,
        block_first_resolve: bool = False,
    ) -> None:
        self.storage_root = storage_root
        self.fail_first_prepare = fail_first_prepare
        self.block_first_prepare = block_first_prepare
        self.block_first_resolve = block_first_resolve
        self.commands: list[tuple[str, dict[str, Any]]] = []
        self.prepare_attempts = 0
        self.resolve_attempts = 0
        self.blocked = threading.Event()

    def prepare(self, command: JobCommand) -> PreparedCommand:
        return PreparedCommand(
            kind=command.kind,
            request=dict(command.payload),
            label=command.label or command.kind.value,
            project_id=command.project_id,
            command_name="autorun-e2e",
            metadata=dict(command.metadata),
        )

    async def run(
        self,
        prepared: PreparedCommand,
        runtime: Any,
        on_step: Any,
    ) -> dict[str, Any]:
        kind = prepared.kind.value
        payload = dict(prepared.request)
        self.commands.append((kind, payload))
        on_step("working", {"chapter_number": payload.get("chapter_number")})
        if kind == JobKind.INIT_LONG.value:
            return {"project_id": prepared.project_id, "status": "completed"}
        if kind == JobKind.PREPARE_CHAPTER.value:
            self.prepare_attempts += 1
            if self.fail_first_prepare and self.prepare_attempts == 1:
                raise RuntimeError("temporary gateway timeout")
            if self.block_first_prepare and self.prepare_attempts == 1:
                self.blocked.set()
                await asyncio.sleep(60)
            chapter = int(payload["chapter_number"])
            return self._checkpoint_payload(prepared.project_id, chapter, "plan_checkpoint")
        if kind in {
            JobKind.RESOLVE_CHAPTER_CHECKPOINT.value,
            JobKind.RESOLVE_CHAPTER_CHECKPOINT_FINALIZE.value,
        }:
            self.resolve_attempts += 1
            if self.block_first_resolve and self.resolve_attempts == 1:
                self.blocked.set()
                await asyncio.sleep(60)
            chapter = int(payload["chapter_number"])
            option_id = str(payload["option_id"])
            if option_id == "write_now":
                return self._checkpoint_payload(
                    prepared.project_id,
                    chapter,
                    "guard_checkpoint",
                )
            self._archive(prepared.project_id, chapter)
            return {
                "project_id": prepared.project_id,
                "chapter_number": chapter,
                "status": "completed",
                "applied_option_id": option_id,
            }
        raise AssertionError(f"unexpected job kind: {kind}")

    @staticmethod
    def _checkpoint_payload(project_id: str, chapter: int, checkpoint_type: str) -> dict[str, Any]:
        is_plan = checkpoint_type == "plan_checkpoint"
        option_id = "write_now" if is_plan else "accept_and_finalize"
        return {
            "project_id": project_id,
            "chapter_number": chapter,
            "status": "needs_decision",
            "checkpoint": {
                "checkpoint_id": f"{checkpoint_type}-{chapter}",
                "checkpoint_type": checkpoint_type,
                "summary": f"chapter {chapter}",
                "prompt": "continue",
                "options": [
                    {
                        "option_id": option_id,
                        "label": option_id,
                        "is_recommended": True,
                    }
                ],
            },
        }

    def _archive(self, project_id: str, chapter: int) -> None:
        layout = ProjectLayout(self.storage_root / project_id)
        layout.chapter_path(chapter).parent.mkdir(parents=True, exist_ok=True)
        layout.chapter_path(chapter).write_text(f"# chapter {chapter}\n", encoding="utf-8")
        atomic_write_json(layout.chapter_exit_state_path(chapter), {"chapter_number": chapter})
        atomic_write_json(layout.creative_report_path(chapter), {"chapter_number": chapter})
        atomic_write_json(layout.canon_dir / "canon_current.json", {"current_chapter": chapter})


def _service(root: Path, executor: _AutorunExecutor, *, load_history: bool = False) -> JobService:
    service = JobService(
        storage_root=root,
        load_persisted_history=load_history,
        executor=executor,
        runtime_factory=lambda _mock: _Runtime(),  # type: ignore[arg-type]
    )
    service._book_autorun._failure_backoff_ms = 0
    service._book_autorun._checkpoint_backoff_ms = 0
    return service


def _start_from_init(service: JobService, project_id: str, total_chapters: int) -> None:
    service.submit(
        JobCommand(
            kind=JobKind.INIT_LONG,
            project_id=project_id,
            payload={"project_id": project_id, "premise": "p", "total_chapters": total_chapters},
            metadata={
                "run_mode": "autorun",
                "autorun_after_init": True,
                "idempotency_key": f"init:{project_id}",
            },
        )
    )


def _wait_until(predicate: Any, *, timeout: float = 8.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition was not met within timeout")


def test_autorun_cleanup_reset_discards_stale_resume_and_checkpoint(tmp_path: Path) -> None:
    coordinator = BookAutorunCoordinator(
        storage_root=tmp_path,
        submit_command=lambda _command: None,  # type: ignore[arg-type]
        resume_job=lambda *_args: None,  # type: ignore[arg-type]
        get_job=lambda _job_id: None,
    )
    state = BookAutorunState(
        project_id="cleanup",
        status=BookAutorunStatus.FAILED,
        phase="resolving",
        start_chapter=1,
        current_chapter=3,
        end_chapter=8,
        total_chapters=8,
        completed_chapters=[1, 2],
        resume_job_id="old-failed-job",
        waiting_planning_job_id="old-planning-job",
        checkpoint_id="old-checkpoint",
        checkpoint_type="plan_checkpoint",
        checkpoint_payload={"checkpoint_id": "old-checkpoint"},
        selected_option_id="write_now",
        checkpoint_attempts={"old-checkpoint": 2},
        failure_attempts={"3:preparing": 3},
        total_failures=3,
        last_error="old validation error",
    )
    coordinator._store(state)

    assert coordinator.reset_after_chapter_cleanup("cleanup", from_chapter=2)
    reset = coordinator.state("cleanup")

    assert reset is not None
    assert reset.status == BookAutorunStatus.CANCELLED
    assert reset.current_chapter == 2
    assert reset.completed_chapters == [1]
    assert reset.phase.value == "preparing"
    assert not reset.resume_job_id
    assert not reset.waiting_planning_job_id
    assert not reset.checkpoint_id
    assert reset.checkpoint_payload == {}
    assert reset.checkpoint_attempts == {}
    assert reset.failure_attempts == {}
    assert reset.total_failures == 0
    assert "第 2 章" in reset.last_error
    coordinator.shutdown()


def test_autorun_parks_for_engine_restart_without_spending_failure_budget(tmp_path: Path) -> None:
    submitted: list[JobCommand] = []

    def reject(command: JobCommand) -> JobRecord:
        submitted.append(command)
        raise EngineRestartRequiredError(boot_revision="old", current_revision="new")

    coordinator = BookAutorunCoordinator(
        storage_root=tmp_path,
        submit_command=reject,
        resume_job=lambda *_args: None,  # type: ignore[arg-type]
        get_job=lambda _job_id: None,
        failure_backoff_ms=0,
    )
    coordinator.start(project_id="restart-wait", chapter_number=1)
    state = coordinator.state("restart-wait")
    assert state is not None
    assert state.status == BookAutorunStatus.RETRY_WAIT
    assert state.wait_reason == "engine_restart_required"
    view = project_book_autorun_view(state).model_dump(by_alias=True)
    assert view["waitReason"] == "engine_restart_required"
    assert view["status"] == "retry_wait"
    assert state.total_failures == 0
    assert state.failure_attempts == {}
    assert not state.active_job_id and not state.next_retry_at
    assert coordinator._timers == {}
    assert len(submitted) == 1
    coordinator.shutdown()

    executor = _AutorunExecutor(tmp_path)
    recovered = _service(tmp_path, executor, load_history=True)
    try:
        _wait_until(
            lambda: (
                recovered.book_autorun_state("restart-wait").status  # type: ignore[union-attr]
                == BookAutorunStatus.COMPLETED
            )
        )
        final = recovered.book_autorun_state("restart-wait")
        assert final is not None and final.completed_chapters == [1]
        assert final.wait_reason == ""
        assert final.total_failures == 0
    finally:
        recovered.shutdown(wait_s=1)


def test_autorun_restart_rejection_preserves_checkpoint_and_decision_budget(tmp_path: Path) -> None:
    def reject(_command: JobCommand) -> JobRecord:
        raise EngineRestartRequiredError(boot_revision="old", current_revision="new")

    coordinator = BookAutorunCoordinator(
        storage_root=tmp_path,
        submit_command=reject,
        resume_job=lambda *_args: None,  # type: ignore[arg-type]
        get_job=lambda _job_id: None,
    )
    state = BookAutorunState(
        project_id="checkpoint-wait",
        checkpoint_id="plan_checkpoint-1",
        checkpoint_type="plan_checkpoint",
        checkpoint_payload=_AutorunExecutor._checkpoint_payload(
            "checkpoint-wait", 1, "plan_checkpoint"
        )["checkpoint"],
    )
    coordinator._store(state)
    coordinator.resolve_checkpoint(
        project_id=state.project_id, checkpoint_id=state.checkpoint_id, option_id="write_now"
    )
    assert state.wait_reason == "engine_restart_required"
    assert state.checkpoint_id == "plan_checkpoint-1"
    assert state.checkpoint_attempts == {}
    assert state.total_failures == 0
    coordinator.shutdown()


def test_autorun_restart_rejection_preserves_resume_intent(tmp_path: Path) -> None:
    def reject_resume(*_args: str) -> JobRecord:
        raise EngineRestartRequiredError(boot_revision="old", current_revision="new")

    coordinator = BookAutorunCoordinator(
        storage_root=tmp_path,
        submit_command=lambda _command: None,  # type: ignore[arg-type]
        resume_job=reject_resume,
        get_job=lambda _job_id: None,
    )
    state = BookAutorunState(project_id="resume-wait", resume_job_id="durable-source")
    coordinator._store(state)
    coordinator._drive_locked(state)
    assert state.status == BookAutorunStatus.RETRY_WAIT
    assert state.wait_reason == "engine_restart_required"
    assert state.resume_job_id == "durable-source"
    assert not state.active_job_id
    assert state.total_failures == 0
    coordinator.shutdown()


def test_autorun_recovers_legacy_restart_exhaustion_but_not_real_failures(tmp_path: Path) -> None:
    for project_id, message in (
        (
            "legacy-restart",
            "本地 Engine 源码已更新，请安全重启后端后再提交新任务。（提交失败预算已耗尽）",
        ),
        ("real-failure", "模型服务超时（提交失败预算已耗尽）"),
    ):
        state = BookAutorunState(
            project_id=project_id,
            status=BookAutorunStatus.FAILED,
            total_failures=4,
            failure_attempts={"1:preparing:dispatch": 4},
            last_error=message,
        )
        atomic_write_json(
            ProjectLayout(tmp_path / project_id).engine_autorun_session_path,
            state.model_dump(mode="json"),
        )
    executor = _AutorunExecutor(tmp_path)
    service = _service(tmp_path, executor, load_history=True)
    try:
        _wait_until(
            lambda: (
                service.book_autorun_state("legacy-restart").status  # type: ignore[union-attr]
                == BookAutorunStatus.COMPLETED
            )
        )
        assert service.book_autorun_state("legacy-restart").total_failures == 0  # type: ignore[union-attr]
        failed = service.book_autorun_state("real-failure")
        assert failed is not None and failed.status == BookAutorunStatus.FAILED
        assert failed.total_failures == 4
    finally:
        service.shutdown(wait_s=1)


def test_engine_autorun_runs_two_chapters_and_stops_at_terminal(tmp_path: Path) -> None:
    executor = _AutorunExecutor(tmp_path)
    service = _service(tmp_path, executor)
    _start_from_init(service, "two-chapters", 2)

    _wait_until(
        lambda: (
            (state := service.book_autorun_state("two-chapters")) is not None
            and state.status == BookAutorunStatus.COMPLETED
        )
    )

    state = service.book_autorun_state("two-chapters")
    assert state is not None
    assert state.completed_chapters == [1, 2]
    assert state.current_chapter == 2
    prepared_chapters = [
        int(payload["chapter_number"])
        for kind, payload in executor.commands
        if kind == JobKind.PREPARE_CHAPTER.value
    ]
    assert prepared_chapters == [1, 2]
    assert all(payload.get("chapter_number") != 3 for _, payload in executor.commands)
    service.shutdown(wait_s=1)


def test_engine_autorun_terminal_chapter_never_dispatches_next(tmp_path: Path) -> None:
    executor = _AutorunExecutor(tmp_path)
    service = _service(tmp_path, executor)
    _start_from_init(service, "terminal", 1)

    _wait_until(
        lambda: (
            (state := service.book_autorun_state("terminal")) is not None
            and state.status == BookAutorunStatus.COMPLETED
        )
    )
    assert [
        payload["chapter_number"]
        for kind, payload in executor.commands
        if kind == JobKind.PREPARE_CHAPTER.value
    ] == [1]
    service.shutdown(wait_s=1)


def test_engine_autorun_recovers_checkpoint_attempt_after_restart(tmp_path: Path) -> None:
    first_executor = _AutorunExecutor(tmp_path, block_first_resolve=True)
    first = _service(tmp_path, first_executor)
    _start_from_init(first, "restart", 1)
    assert first_executor.blocked.wait(5)
    first.shutdown(wait_s=1)

    persisted = ProjectLayout(tmp_path / "restart").engine_autorun_session_path
    assert persisted.exists()
    second_executor = _AutorunExecutor(tmp_path)
    second = _service(tmp_path, second_executor, load_history=True)
    _wait_until(
        lambda: (
            (state := second.book_autorun_state("restart")) is not None
            and state.status == BookAutorunStatus.COMPLETED
        )
    )
    assert any(payload.get("option_id") == "write_now" for _, payload in second_executor.commands)
    second.shutdown(wait_s=1)


def test_engine_autorun_retries_transient_failure_with_persisted_budget(tmp_path: Path) -> None:
    executor = _AutorunExecutor(tmp_path, fail_first_prepare=True)
    service = _service(tmp_path, executor)
    _start_from_init(service, "retry", 1)

    _wait_until(
        lambda: (
            (state := service.book_autorun_state("retry")) is not None
            and state.status == BookAutorunStatus.COMPLETED
        )
    )
    state = service.book_autorun_state("retry")
    assert state is not None
    assert state.total_failures == 1
    assert executor.prepare_attempts == 2
    assert any(attempts == 1 for attempts in state.failure_attempts.values())
    service.shutdown(wait_s=1)


def test_engine_autorun_cancel_then_resume_is_idempotent(tmp_path: Path) -> None:
    executor = _AutorunExecutor(tmp_path, block_first_prepare=True)
    service = _service(tmp_path, executor)
    _start_from_init(service, "cancel-resume", 1)
    assert executor.blocked.wait(5)

    before = service.book_autorun_state("cancel-resume")
    assert before is not None and before.active_job_id
    duplicate = service.start_book_autorun(
        project_id="cancel-resume",
        chapter_number=1,
        mode="book",
        launch_key="init:cancel-resume",
    )
    assert duplicate is not None and duplicate.job_id == before.active_job_id

    active_job_id = service.pause_book_autorun("cancel-resume", reason="test cancel")
    cancelled = service.cancel(active_job_id, reason="test cancel")
    assert cancelled.status == JobState.FAILED
    paused = service.book_autorun_state("cancel-resume")
    assert paused is not None and paused.status == BookAutorunStatus.PAUSED

    service.start_book_autorun(
        project_id="cancel-resume",
        chapter_number=1,
        mode="book",
        launch_key="init:cancel-resume",
    )
    _wait_until(
        lambda: (
            (state := service.book_autorun_state("cancel-resume")) is not None
            and state.status == BookAutorunStatus.COMPLETED
        )
    )
    assert executor.prepare_attempts == 2
    service.shutdown(wait_s=1)


def test_autorun_projects_manual_ownership_for_upstream_source_failure(tmp_path: Path) -> None:
    coordinator = BookAutorunCoordinator(
        storage_root=tmp_path,
        submit_command=lambda _command: None,  # type: ignore[arg-type]
        resume_job=lambda *_args: None,  # type: ignore[arg-type]
        get_job=lambda _job_id: None,
    )
    coordinator._states["source-conflict"] = BookAutorunState(
        project_id="source-conflict",
        active_job_id="job-source-conflict",
    )
    coordinator.on_job_failed(
        JobRecord(
            job_id="job-source-conflict",
            kind=JobKind.PREPARE_CHAPTER,
            label="prepare",
            project_id="source-conflict",
            status=JobState.FAILED,
            error="上游大纲存在冲突",
            error_summary={
                "retryable": False,
                "cause_code": "upstream_source_conflict",
                "context": {
                    "violation_kind": "upstream_source_conflict",
                    "replan_target": "manual",
                },
            },
        )
    )

    state = coordinator.state("source-conflict")
    assert state is not None and state.status == BookAutorunStatus.FAILED
    view = project_book_autorun_view(state).model_dump(by_alias=True)
    assert view["lastFailureKind"] == "upstream_source_conflict"
    assert view["recoveryTarget"] == "manual"
    coordinator.shutdown()


def test_autorun_projects_legacy_truncated_source_conflict_as_manual() -> None:
    state = BookAutorunState(
        project_id="legacy-source-conflict",
        status=BookAutorunStatus.FAILED,
        last_error=(
            "ConsistencyViolationError: 审查上游的「停职」期限冲突："
            "大纲=[3, 5]日，Plan=[3, 5]日。 Context: {'violations': ['truncated"
        ),
    )

    view = project_book_autorun_view(state).model_dump(by_alias=True)

    assert view["lastFailureKind"] == "upstream_source_conflict"
    assert view["recoveryTarget"] == "manual"
