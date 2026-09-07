from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from novel_forge.app_service.contracts import (
    JobCommand,
    JobEvent,
    JobEventType,
    JobKind,
    JobRecord,
    JobScope,
    JobState,
    JobStepEvent,
)
from novel_forge.app_service.event_stream import JobEventStream
from novel_forge.app_service.job_service import _WRITE_JOB_KINDS, JobService, _error_payload
from novel_forge.app_service.workspace_commands import LoggedCommandError, PreparedCommand
from novel_forge.core.exceptions import (
    AuthenticationError,
    ConsistencyViolationError,
    ModelGatewayError,
    RecoveryTarget,
)
from novel_forge.desktop.jobs import (
    DesktopJobEvent,
    DesktopJobRecord,
    DesktopJobState,
    app_job_record_to_desktop_record,
    desktop_job_record_to_app_record,
)
from novel_forge.desktop.progress import init_long_resume_anchor_step


class StubRuntime:
    async def shutdown(self) -> None:
        return None


class FastExecutor:
    def prepare(self, command: JobCommand) -> PreparedCommand:
        return PreparedCommand(
            kind=command.kind,
            request=command.payload,
            label=command.label or "stub job",
            project_id=command.project_id or str(command.payload.get("project_id") or "demo"),
            command_name="stub",
            metadata={},
        )

    async def run(
        self,
        prepared: PreparedCommand,
        runtime: Any,
        on_step: Any,
    ) -> dict[str, Any]:
        on_step("draft", {"tokens_so_far": 12})
        return {"project_id": prepared.project_id, "ok": True}


def test_approved_checkpoint_cannot_start_before_durable_intent(tmp_path, monkeypatch):
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=FastExecutor(),
        runtime_factory=lambda _: StubRuntime(),
    )
    monkeypatch.setattr(service, "_persist_control_plane_intent", lambda *args: "")
    try:
        with pytest.raises(RuntimeError, match="未启动模型请求"):
            service.submit(
                JobCommand(
                    job_id="approved",
                    kind="resolve_chapter_checkpoint",
                    project_id="book",
                    payload={"project_id": "book", "authoring_approval_id": "exact-approval"},
                )
            )
        assert not service._workers and not service._pending_jobs
        assert service.get("approved").status == JobState.FAILED
    finally:
        service.shutdown(wait_s=0)


class SlowExecutor(FastExecutor):
    async def run(
        self,
        prepared: PreparedCommand,
        runtime: Any,
        on_step: Any,
    ) -> dict[str, Any]:
        on_step("waiting", {})
        await asyncio.sleep(30)
        return {"project_id": prepared.project_id, "ok": True}


class FailsOnceExecutor(FastExecutor):
    def __init__(self) -> None:
        self.calls = 0

    async def run(
        self,
        prepared: PreparedCommand,
        runtime: Any,
        on_step: Any,
    ) -> dict[str, Any]:
        self.calls += 1
        on_step("init_story_bible", {"attempt": self.calls})
        if self.calls == 1:
            raise RuntimeError("simulated init interruption")
        return {"project_id": prepared.project_id, "ok": True}


class BlockingExecutor(FastExecutor):
    def __init__(self) -> None:
        self.release: dict[str, threading.Event] = {}

    async def run(
        self,
        prepared: PreparedCommand,
        runtime: Any,
        on_step: Any,
    ) -> dict[str, Any]:
        event = self.release.setdefault(prepared.project_id, threading.Event())
        on_step("blocked", {})
        while not event.is_set():
            await asyncio.sleep(0.01)
        return {"project_id": prepared.project_id, "ok": True}


class KindBlockingExecutor(FastExecutor):
    """Block by job kind + project so separate lanes can share one project."""

    def __init__(self) -> None:
        self.release: dict[str, threading.Event] = {}
        self.started: list[str] = []

    @staticmethod
    def key(kind: JobKind | str, project_id: str) -> str:
        kind_value = kind.value if isinstance(kind, JobKind) else str(kind)
        return f"{kind_value}:{project_id}"

    async def run(
        self,
        prepared: PreparedCommand,
        runtime: Any,
        on_step: Any,
    ) -> dict[str, Any]:
        key = self.key(prepared.kind, prepared.project_id)
        event = self.release.setdefault(key, threading.Event())
        self.started.append(key)
        on_step("blocked", {})
        while not event.is_set():
            await asyncio.sleep(0.01)
        return {"project_id": prepared.project_id, "ok": True}


def _runtime_factory(_mock: bool) -> StubRuntime:
    return StubRuntime()


def test_input_integrity_error_payload_is_actionable_for_desktop() -> None:
    error = ConsistencyViolationError(
        [
            "planning_input 缺少必填输入 chapter_outline.pov_character",
            "planning_input 缺少必填输入 chapter_outline.goal",
        ],
        violation_kind="input_contract",
        failed_stage="planning_input",
        replan_target=RecoveryTarget.MANUAL,
        block_kind="input_integrity",
    )

    payload = _error_payload(error)

    assert payload["title"] == "章节输入不完整"
    assert "chapter_outline.pov_character" in payload["summary"]
    assert payload["error_code"] == "consistency_violation"
    assert payload["context"]["failed_stage"] == "planning_input"
    assert payload["recovery_actions"]


def test_upstream_source_conflict_error_payload_forbids_generic_retry() -> None:
    error = ConsistencyViolationError(
        ["停职期限冲突：大纲=[3, 5]日"],
        violation_kind="upstream_source_conflict",
        failed_stage="upstream_source_preflight",
        replan_target=RecoveryTarget.MANUAL,
    )

    payload = _error_payload(error)

    assert payload["title"] == "上游来源存在语义冲突"
    assert payload["retryable"] is False
    assert payload["cause_code"] == "upstream_source_conflict"
    assert payload["context"]["replan_target"] == "manual"
    assert payload["recovery_actions"] == [
        {
            "kind": "review_semantic_repairs",
            "label": "在修复工作台生成候选，经作者批准后再准备本章。",
        }
    ]


def _wait_for_status(service: JobService, job_id: str, status: JobState) -> None:
    deadline = time.time() + 5
    while time.time() < deadline:
        record = service.get(job_id)
        if record is not None and record.status == status:
            return
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not reach {status}")


def _wait_for_release_key(executor: BlockingExecutor, project_id: str) -> None:
    deadline = time.time() + 5
    while time.time() < deadline:
        if project_id in executor.release:
            return
        time.sleep(0.02)
    raise AssertionError(f"executor did not start project {project_id}")


def _wait_for_kind_release_key(
    executor: KindBlockingExecutor,
    kind: JobKind,
    project_id: str,
) -> str:
    key = executor.key(kind, project_id)
    deadline = time.time() + 5
    while time.time() < deadline:
        if key in executor.release:
            return key
        time.sleep(0.02)
    raise AssertionError(f"executor did not start {key}")


def test_job_service_submit_records_steps_and_terminal_event(tmp_path: Path) -> None:
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=FastExecutor(),
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    )
    subscription = service.open_subscription()
    record = service.submit(
        JobCommand(
            kind=JobKind.RUN_CHAPTER,
            project_id="demo",
            payload={"project_id": "demo", "chapter_number": 1},
        )
    )

    _wait_for_status(service, record.job_id, JobState.SUCCEEDED)
    final = service.get(record.job_id)
    assert final is not None
    assert final.current_step == "completed"
    assert final.result["ok"] is True
    assert final.cumulative_tokens == 12

    seen: list[str] = []
    deadline = time.time() + 2
    while time.time() < deadline and JobEventType.JOB_SUCCEEDED.value not in seen:
        event = subscription.get(timeout=0.1)
        if event is not None:
            seen.append(event.type.value if hasattr(event.type, "value") else str(event.type))
    subscription.close()
    assert JobEventType.JOB_STARTED.value in seen
    assert JobEventType.JOB_STEP.value in seen
    assert JobEventType.JOB_SUCCEEDED.value in seen


def test_job_service_persists_author_facing_project_label(tmp_path: Path) -> None:
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=FastExecutor(),
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    )

    record = service.submit(
        JobCommand(
            kind=JobKind.INIT_LONG,
            project_id="long_be882a54",
            payload={"project_id": "long_be882a54", "title": "药证"},
        )
    )

    assert record.project_id == "long_be882a54"
    assert record.project_label == "药证"
    assert record.to_history_payload()["project_label"] == "药证"


def test_system_job_history_is_durable_without_creating_a_project(tmp_path: Path) -> None:
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=FastExecutor(),
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    )
    record = service.submit(
        JobCommand(
            kind=JobKind.OLLAMA_PULL_MODEL,
            scope=JobScope.SYSTEM,
            payload={"model": "qwen3:8b"},
        )
    )

    _wait_for_status(service, record.job_id, JobState.SUCCEEDED)
    history_path = tmp_path / ".engine" / "states" / "task_flow_history.json"
    intent_path = (
        tmp_path / ".engine" / "states" / "control_plane_intents" / f"{record.job_id}.json"
    )
    assert history_path.exists()
    assert intent_path.exists()
    assert not (tmp_path / "demo").exists()

    restored = JobService(
        storage_root=tmp_path,
        load_persisted_history=True,
        executor=FastExecutor(),
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    ).get(record.job_id)
    assert restored is not None
    assert restored.scope == JobScope.SYSTEM
    assert restored.project_id == ""


def test_system_pull_restart_recovery_commits_when_model_already_exists(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """A crash does not redownload a model that Ollama already finished pulling."""

    class ExistingModelControl:
        def model_exists(self, model: str) -> bool:
            return model == "qwen3:8b"

    from novel_forge.app_service import ollama_control

    monkeypatch.setattr(
        ollama_control, "get_ollama_control_service", lambda _settings: ExistingModelControl()
    )
    writer = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=FastExecutor(),
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    )
    command = JobCommand(
        job_id="interrupted-ollama-pull",
        kind=JobKind.OLLAMA_PULL_MODEL,
        scope=JobScope.SYSTEM,
        payload={"model": "qwen3:8b"},
    )
    interrupted = JobRecord(
        job_id=command.job_id,
        kind=command.kind,
        label="下载模型",
        scope=JobScope.SYSTEM,
        status=JobState.RUNNING,
    )
    writer._persist_control_plane_intent(interrupted, command)

    recovered = JobService(
        storage_root=tmp_path,
        load_persisted_history=True,
        executor=FastExecutor(),
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    ).get(command.job_id)

    assert recovered is not None
    assert recovered.status == JobState.SUCCEEDED
    assert recovered.result["status"] == "already_present_after_restart"
    assert recovered.result["checkpoint"] == "committed"


def test_system_delete_records_durable_saga_checkpoints(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class DeleteControl:
        def delete_model(self, model: str) -> None:
            assert model == "qwen3:8b"

    class OllamaRuntime(StubRuntime):
        settings = object()

    from novel_forge.app_service import workspace_commands

    monkeypatch.setattr(
        workspace_commands,
        "get_ollama_control_service",
        lambda _settings: DeleteControl(),
    )
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        runtime_factory=lambda _mock: OllamaRuntime(),  # type: ignore[arg-type]
    )
    record = service.submit(
        JobCommand(
            kind=JobKind.OLLAMA_DELETE_MODEL,
            scope=JobScope.SYSTEM,
            payload={"model": "qwen3:8b"},
            metadata={"configuration_checkpoint": "config_detached"},
        )
    )

    _wait_for_status(service, record.job_id, JobState.SUCCEEDED)
    completed = service.get(record.job_id)
    assert completed is not None
    checkpoints = [
        event.payload.get("checkpoint")
        for event in completed.events
        if event.step == "ollama_delete"
    ]
    assert checkpoints == ["config_detached", "model_delete_started", "model_deleted", "committed"]
    assert completed.result["checkpoint"] == "committed"


def test_system_pull_cancellation_reaches_the_engine_download_stream(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class PullControl:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.cancelled = threading.Event()

        def model_exists(self, _model: str) -> bool:
            return False

        def pull_model(self, _model: str, *, on_progress, is_cancelled) -> None:  # type: ignore[no-untyped-def]
            from novel_forge.app_service.ollama_control import OllamaOperationCancelled

            self.started.set()
            on_progress(
                type(
                    "Progress", (), {"status": "下载中", "percent": 10, "completed": 1, "total": 10}
                )()
            )
            while not is_cancelled():
                time.sleep(0.01)
            self.cancelled.set()
            raise OllamaOperationCancelled("cancelled")

    class OllamaRuntime(StubRuntime):
        settings = object()

    from novel_forge.app_service import workspace_commands

    control = PullControl()
    monkeypatch.setattr(workspace_commands, "get_ollama_control_service", lambda _settings: control)
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        runtime_factory=lambda _mock: OllamaRuntime(),  # type: ignore[arg-type]
    )
    record = service.submit(
        JobCommand(
            kind=JobKind.OLLAMA_PULL_MODEL,
            scope=JobScope.SYSTEM,
            payload={"model": "qwen3:8b"},
        )
    )
    assert control.started.wait(2)

    cancelled = service.cancel(record.job_id, reason="test cancel")
    assert cancelled.status == JobState.FAILED
    assert cancelled.current_step == "cancelled"
    assert control.cancelled.wait(2)


def test_project_resume_replays_durable_intent_after_history_is_cleared(tmp_path: Path) -> None:
    executor = FailsOnceExecutor()
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=executor,
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    )
    original = service.submit(
        JobCommand(
            kind=JobKind.INIT_LONG,
            project_id="resume-demo",
            payload={"project_id": "resume-demo", "premise": "不能丢失的原始前提"},
            metadata={"run_mode": "create"},
        )
    )
    _wait_for_status(service, original.job_id, JobState.FAILED)

    intent_path = (
        tmp_path / "resume-demo" / "states" / "control_plane_intents" / f"{original.job_id}.json"
    )
    assert intent_path.exists()
    assert service.clear_inactive_history({original.job_id}) == [original.job_id]

    resumed = service.resume_project(
        "resume-demo",
        JobKind.INIT_LONG,
        metadata_updates={"run_mode": "autorun", "autorun_after_init": True},
    )
    assert resumed.job_id != original.job_id
    _wait_for_status(service, resumed.job_id, JobState.SUCCEEDED)
    assert executor.calls == 2

    resumed_intent = json.loads(
        (
            tmp_path / "resume-demo" / "states" / "control_plane_intents" / f"{resumed.job_id}.json"
        ).read_text(encoding="utf-8")
    )
    assert resumed_intent["payload"]["premise"] == "不能丢失的原始前提"
    assert resumed_intent["metadata"]["resumed_from_job_id"] == original.job_id
    assert resumed_intent["metadata"]["autorun_after_init"] is True


def test_repair_case_evidence_job_persists_and_resumes_without_becoming_write_job(
    tmp_path: Path,
) -> None:
    executor = FailsOnceExecutor()
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=executor,
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    )
    original = service.submit(
        JobCommand(
            kind=JobKind.REPAIR_CASE,
            project_id="repair-demo",
            payload={
                "project_id": "repair-demo",
                "case_id": "case-1",
                "operation": "verify",
                "case_version": 3,
                "candidate_version": 1,
            },
        )
    )
    _wait_for_status(service, original.job_id, JobState.FAILED)

    intent_path = (
        tmp_path
        / "repair-demo"
        / "states"
        / "control_plane_intents"
        / f"{original.job_id}.json"
    )
    assert intent_path.exists()
    assert "repair_case" not in _WRITE_JOB_KINDS

    resumed = service.resume_project("repair-demo", JobKind.REPAIR_CASE)
    _wait_for_status(service, resumed.job_id, JobState.SUCCEEDED)
    assert resumed.job_id != original.job_id
    assert executor.calls == 2
    payload = json.loads(
        (
            tmp_path
            / "repair-demo"
            / "states"
            / "control_plane_intents"
            / f"{resumed.job_id}.json"
        ).read_text(encoding="utf-8")
    )
    assert payload["payload"]["case_id"] == "case-1"
    assert payload["payload"]["operation"] == "verify"


def test_failed_job_resume_replays_its_own_durable_intent(tmp_path: Path) -> None:
    executor = FailsOnceExecutor()
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=executor,
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    )
    original = service.submit(
        JobCommand(
            kind=JobKind.INIT_LONG,
            project_id="task-resume-demo",
            payload={"project_id": "task-resume-demo", "premise": "原任务参数"},
        )
    )
    _wait_for_status(service, original.job_id, JobState.FAILED)

    resumed = service.resume(original.job_id)

    assert resumed.job_id != original.job_id
    _wait_for_status(service, resumed.job_id, JobState.SUCCEEDED)
    assert executor.calls == 2


def test_clear_inactive_history_preserves_active_jobs_and_removes_paused_records(
    tmp_path: Path,
) -> None:
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=FastExecutor(),
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    )
    records = [
        JobRecord(
            job_id="done",
            kind=JobKind.RUN_SHORT,
            label="done",
            project_id="demo",
            status=JobState.SUCCEEDED,
        ),
        JobRecord(
            job_id="failed",
            kind=JobKind.INIT_LONG,
            label="failed",
            project_id="demo",
            status=JobState.FAILED,
        ),
        JobRecord(
            job_id="paused",
            kind=JobKind.RUN_CHAPTER,
            label="paused",
            project_id="demo",
            status=JobState.PAUSED,
        ),
        JobRecord(
            job_id="running",
            kind=JobKind.RUN_CHAPTER,
            label="running",
            project_id="demo",
            status=JobState.RUNNING,
        ),
    ]
    with service._lock:
        service._jobs.update({record.job_id: record for record in records})

    cleared = service.clear_inactive_history({"done", "failed", "paused", "running"})

    assert set(cleared) == {"done", "failed", "paused"}
    assert service.get("done") is None
    assert service.get("failed") is None
    assert service.get("paused") is None
    assert service.get("running") is not None


def test_task_flow_error_log_survives_history_cleanup(tmp_path: Path) -> None:
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=FastExecutor(),
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    )
    record = JobRecord(
        job_id="failed-init",
        kind=JobKind.INIT_LONG,
        label="长篇立项 · 眠咒",
        project_id="sleep-spell",
        status=JobState.FAILED,
        current_step="init_story_bible",
        error="RuntimeError: provider unavailable",
        error_summary={
            "summary": "模型服务不可用",
            "detail": "RuntimeError: provider unavailable",
            "log_path": "/tmp/sleep-spell/logs/run-1",
        },
        events=[
            JobStepEvent(
                step="run_log_started",
                payload={"run_log_dir": "/tmp/sleep-spell/logs/run-1"},
            )
        ],
    )
    with service._lock:
        service._jobs[record.job_id] = record

    service._persist_terminal_job(record.job_id)
    assert service.clear_inactive_history({record.job_id}) == [record.job_id]

    entries = service.list_error_log(project_id="sleep-spell")
    assert len(entries) == 1
    assert entries[0]["task"] == "init_story_bible"
    assert entries[0]["error"] == "模型服务不可用"
    assert entries[0]["log_path"] == "/tmp/sleep-spell/logs/run-1"


def test_task_flow_error_log_summary_and_clear_keep_run_logs(tmp_path: Path) -> None:
    from novel_forge.app_service.task_flow_error_log import TaskFlowErrorLog

    run_log = tmp_path / "sleep-spell" / "logs" / "run-1" / "events.jsonl"
    run_log.parent.mkdir(parents=True)
    run_log.write_text('{"event":"failed"}\n', encoding="utf-8")
    record = JobRecord(
        job_id="failed-init",
        kind=JobKind.INIT_LONG,
        label="长篇立项 · 眠咒",
        project_id="sleep-spell",
        status=JobState.FAILED,
        current_step="init_story_bible",
        error="provider unavailable",
    )
    archive = TaskFlowErrorLog(tmp_path)

    assert archive.archive_job(record) == 1
    assert archive.summary() == {
        "schema_version": 3,
        "entry_count": 1,
        "project_count": 1,
        "latest_time": record.updated_at,
    }
    assert archive.clear() == 1
    assert archive.summary()["entry_count"] == 0
    assert run_log.is_file()


def test_pending_task_flow_error_cannot_be_pruned_until_acknowledged(tmp_path: Path) -> None:
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=FastExecutor(),
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    )
    run_log = tmp_path / "sleep-spell" / "logs" / "run-1" / "events.jsonl"
    run_log.parent.mkdir(parents=True)
    run_log.write_text('{"event":"failed"}\n', encoding="utf-8")
    record = JobRecord(
        job_id="failed-init",
        kind=JobKind.INIT_LONG,
        label="长篇立项 · 眠咒",
        project_id="sleep-spell",
        status=JobState.FAILED,
        current_step="init_story_bible",
        error="RuntimeError: provider unavailable",
        error_summary={
            "summary": "模型服务不可用",
            "detail": "RuntimeError: provider unavailable",
            "log_path": str(run_log.parent),
        },
    )
    with service._lock:
        service._jobs[record.job_id] = record
    service._persist_terminal_job(record.job_id)
    entry_id = service.list_error_log(project_id="sleep-spell")[0]["id"]
    records_dir = tmp_path / "sleep-spell" / "states" / "task_flow_errors" / "records"
    canonical_record = records_dir / f"{entry_id}.json"
    legacy_record = records_dir / f"{entry_id}_init_story_bible.json"
    canonical_record.rename(legacy_record)

    pending_cleanup = service.clear_closed_error_log_entries({entry_id})

    assert pending_cleanup.cleared_error_entry_ids == []
    assert pending_cleanup.cleared_task_ids == []
    assert pending_cleanup.retained_pending_error_entry_ids == [entry_id]
    assert service.list_error_log(project_id="sleep-spell")[0].get("acknowledged_at", "") == ""

    assert service.acknowledge_error_log_entries({entry_id}) == [entry_id]
    assert service.list_error_log(project_id="sleep-spell")[0]["acknowledged_at"]
    assert not canonical_record.exists()
    assert legacy_record.exists()

    assert service.reopen_error_log_entries({entry_id}) == [entry_id]
    reopened_cleanup = service.clear_closed_error_log_entries({entry_id})
    assert reopened_cleanup.cleared_error_entry_ids == []
    assert reopened_cleanup.retained_pending_error_entry_ids == [entry_id]

    assert service.acknowledge_error_log_entries({entry_id}) == [entry_id]

    cleanup = service.clear_closed_error_log_entries({entry_id})

    assert cleanup.cleared_task_ids == [record.job_id]
    assert cleanup.cleared_error_entry_ids == [entry_id]
    assert cleanup.retained_pending_error_entry_ids == []
    assert service.list_error_log(project_id="sleep-spell") == []
    assert run_log.read_text(encoding="utf-8") == '{"event":"failed"}\n'


def test_logged_command_error_keeps_structured_run_log_location() -> None:
    payload = _error_payload(
        LoggedCommandError(ValueError("provider unavailable"), run_log_dir="/tmp/logs/run-1")
    )

    assert payload["summary"] == "ValueError: provider unavailable"
    assert payload["log_path"] == "/tmp/logs/run-1"


def test_logged_errors_preserve_transient_retry_policy_and_original_type() -> None:
    for cause, retryable in (
        (ModelGatewayError("temporary outage", is_transient=True), True),
        (TimeoutError("timeout"), True),
        (AuthenticationError("invalid credentials"), False),
        (ModelGatewayError("permanent", is_transient=False), False),
        (ModelGatewayError("do not retry", is_transient=True, context={"retryable": False}), False),
    ):
        payload = _error_payload(LoggedCommandError(cause, run_log_dir="/tmp/logs/retry"))
        assert payload["retryable"] is retryable
        assert payload["exception_type"] == type(cause).__name__
        assert payload["log_path"] == "/tmp/logs/retry"


def test_job_service_preserves_semantic_step_after_diagnostic_window_slides(
    tmp_path: Path,
) -> None:
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=FastExecutor(),
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    )
    record = JobRecord(
        job_id="job-outline-window",
        kind=JobKind.INIT_LONG,
        label="长篇立项 · demo",
        project_id="demo",
        status=JobState.RUNNING,
    )
    with service._lock:
        service._jobs[record.job_id] = record

    service._handle_step(
        record.job_id,
        "extract_init_coherence_claims",
        {"stage": "outline_inheritance", "artifact": "outline"},
    )
    for index in range(200):
        service._handle_step(
            record.job_id,
            "llm_stream_delta_summary",
            {"task": "adjudicate_entity_references", "index": index},
        )

    current = service.get(record.job_id)
    assert current is not None
    assert current.current_step == "extract_init_coherence_claims"
    assert current.current_step_payload == {
        "stage": "outline_inheritance",
        "artifact": "outline",
    }
    assert len(current.events) == 160
    assert any(event.step == "extract_init_coherence_claims" for event in current.events)


def test_job_service_backfills_legacy_auto_project_from_run_log(tmp_path: Path) -> None:
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=FastExecutor(),
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    )
    record = JobRecord(
        job_id="job-legacy-auto-project",
        kind=JobKind.INIT_LONG,
        label="长篇立项 · 自动项目",
        project_id="",
        status=JobState.RUNNING,
        result={"run_log_dir": str(tmp_path / "long_legacy_1234" / "logs" / "run-1")},
    )
    with service._lock:
        service._jobs[record.job_id] = record

    current = service.get(record.job_id)

    assert current is not None
    assert current.project_id == "long_legacy_1234"
    assert [item.job_id for item in service.list(project_id="long_legacy_1234")] == [record.job_id]


def test_job_service_binds_auto_project_from_run_log_started_event(tmp_path: Path) -> None:
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=FastExecutor(),
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    )
    record = JobRecord(
        job_id="job-new-auto-project",
        kind=JobKind.INIT_LONG,
        label="长篇立项 · 自动项目",
        project_id="",
        status=JobState.RUNNING,
    )
    with service._lock:
        service._jobs[record.job_id] = record

    service._handle_step(
        record.job_id,
        "run_log_started",
        {
            "project_id": "long_new_5678",
            "run_log_dir": str(tmp_path / "long_new_5678" / "logs" / "run-1"),
        },
    )

    current = service.get(record.job_id)

    assert current is not None
    assert current.project_id == "long_new_5678"


def test_validation_snapshots_survive_history_compaction_and_serialization(tmp_path: Path) -> None:
    from novel_forge.app_service.engine_views import project_task_stream_view
    from novel_forge.app_service.job_service import _compact_payload

    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=FastExecutor(),
        runtime_factory=_runtime_factory,
    )
    record = JobRecord(
        job_id="validation-history", kind=JobKind.INIT_LONG, label="初始化", status=JobState.RUNNING
    )
    service._jobs[record.job_id] = record
    service._handle_step(
        record.job_id,
        "llm_stream_error",
        {
            "stream_id": "s",
            "error": "原始格式错误",
            "text": "{",
        },
    )
    content = json.dumps({"summary": "正文" * 4000}, ensure_ascii=False)
    service._handle_step(
        record.job_id,
        "llm_stream_validation",
        _compact_payload(
            "llm_stream_validation",
            {
                "stream_id": "s",
                "validation_status": "validated",
                "text": content,
                "text_length": len(content),
                "operation_id": "op",
                "repair_source": "local",
            },
        ),
    )
    for i in range(200):
        service._handle_step(record.job_id, "llm_stream_delta_summary", {"index": i})
    current = service.get(record.job_id)
    assert current is not None
    assert not any(event.step == "llm_stream_validation" for event in current.events)
    restored = JobRecord.from_history_payload(current.to_history_payload())
    assert restored is not None
    saved = restored.stream_results["s"].payload
    assert saved["text_length"] == len(content)
    assert len(saved["text"]) < len(content)
    assert "error" not in saved
    view = project_task_stream_view(restored)
    assert view.events[0].validation_status == "validated"
    assert view.events[0].text_truncated is True
    assert view.events[-1].message is None


def test_stream_snapshot_clipping_flags_cover_equal_length_ellipsis_boundary() -> None:
    from novel_forge.app_service.engine_views import project_task_stream_view
    from novel_forge.app_service.job_service import _MAX_COMPACT_STRING, _compact_payload

    text = "x" * (_MAX_COMPACT_STRING + 1)
    payload = _compact_payload(
        "llm_stream_end",
        {
            "stream_id": "s",
            "text": text,
            "text_length": len(text),
        },
    )
    assert len(payload["text"]) == len(text)
    assert payload["text_truncated"] is True
    record = JobRecord(
        job_id="clip-edge",
        kind=JobKind.INIT_LONG,
        label="边界",
        events=[JobStepEvent(at="2026-08-27T10:00:00Z", step="llm_stream_end", payload=payload)],
    )
    assert project_task_stream_view(record).events[0].text_truncated is True


def test_stream_result_cache_is_bounded(tmp_path: Path) -> None:
    from novel_forge.app_service.job_service import _MAX_STREAM_RESULTS

    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=FastExecutor(),
        runtime_factory=_runtime_factory,
    )
    record = JobRecord(
        job_id="bounded-results", kind=JobKind.INIT_LONG, label="初始化", status=JobState.RUNNING
    )
    service._jobs[record.job_id] = record
    service._handle_step(record.job_id, "llm_stream_start", {"stream_id": "slow-sibling"})
    for i in range(_MAX_STREAM_RESULTS + 2):
        service._handle_step(
            record.job_id,
            "llm_stream_validation",
            {
                "stream_id": str(i),
                "validation_status": "validated",
                "text": "{}",
            },
        )
    current = service.get(record.job_id)
    assert current is not None
    assert len(current.stream_results) == _MAX_STREAM_RESULTS
    assert "0" not in current.stream_results
    assert str(_MAX_STREAM_RESULTS + 1) in current.stream_results
    assert "slow-sibling" in current.stream_results
    from novel_forge.app_service.engine_views import project_task_stream_view

    assert project_task_stream_view(current).status == "streaming"


def test_job_service_keeps_resume_anchor_after_diagnostic_window_slides(
    tmp_path: Path,
) -> None:
    """A resumed job must not lose its validated display floor to stream noise."""

    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=FastExecutor(),
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    )
    record = JobRecord(
        job_id="job-resume-window",
        kind=JobKind.INIT_LONG,
        label="长篇立项 · demo",
        project_id="demo",
        status=JobState.RUNNING,
    )
    with service._lock:
        service._jobs[record.job_id] = record

    service._handle_step(
        record.job_id,
        "init_resume_anchor",
        {"step": "plan_outline", "artifact": "outline"},
    )
    service._handle_step(record.job_id, "spec_resumed", {})
    service._handle_step(record.job_id, "init_story_bible_resumed", {})
    for index in range(200):
        service._handle_step(
            record.job_id,
            "llm_stream_delta_summary",
            {"task": "plan_outline", "index": index},
        )

    current = service.get(record.job_id)
    assert current is not None
    assert len(current.events) == 160
    assert any(event.step == "init_resume_anchor" for event in current.events)
    assert init_long_resume_anchor_step(app_job_record_to_desktop_record(current)) == "plan_outline"


def test_api_stream_events_do_not_replace_active_workflow_step(tmp_path: Path) -> None:
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=FastExecutor(),
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    )
    record = JobRecord(
        job_id="job-api-stream-observation",
        kind=JobKind.INIT_LONG,
        label="长篇立项 · demo",
        project_id="demo",
        status=JobState.RUNNING,
        current_step="profile_style",
    )
    with service._lock:
        service._jobs[record.job_id] = record

    service._handle_step(record.job_id, "api_stream_start", {"task": "plan_outline"})

    assert record.current_step == "profile_style"


def test_desktop_job_history_serialization_keeps_resume_anchor() -> None:
    from novel_forge.desktop.jobs.records import _job_from_payload, _job_to_payload

    record = DesktopJobRecord(
        job_id="job-resume-history",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        events=[
            DesktopJobEvent(
                at="2026-08-02T12:00:00+00:00",
                step="init_resume_anchor",
                payload={"step": "plan_outline", "artifact": "outline"},
            ),
            *[
                DesktopJobEvent(
                    at=f"2026-08-02T12:{index // 60:02d}:{index % 60:02d}+00:00",
                    step="format_validation_success",
                    payload={"task": "plan_outline", "index": index},
                )
                for index in range(200)
            ],
        ],
    )

    restored = _job_from_payload(_job_to_payload(record))

    assert restored is not None
    assert len(restored.events) == 160
    assert init_long_resume_anchor_step(restored) == "plan_outline"


def test_event_stream_bounded_subscription_drops_oldest_when_consumer_lags() -> None:
    stream = JobEventStream()
    subscription = stream.subscribe(max_queue_size=2)

    for idx in range(4):
        stream.publish(
            JobEvent(
                job_id="j1",
                type=JobEventType.JOB_STEP,
                step=f"step_{idx}",
            )
        )

    first = subscription.get(timeout=0)
    second = subscription.get(timeout=0)
    empty = subscription.get(timeout=0)
    subscription.close()

    assert first is not None
    assert second is not None
    assert first.step == "step_2"
    assert second.step == "step_3"
    assert empty is None


def test_event_stream_bounded_subscription_close_never_blocks_when_full() -> None:
    stream = JobEventStream()
    subscription = stream.subscribe(max_queue_size=1)
    stream.publish(JobEvent(job_id="j1", type=JobEventType.JOB_STEP, step="queued"))

    subscription.close()

    assert subscription.get(timeout=0) is None


def test_job_service_uses_record_kind_for_job_record(tmp_path: Path) -> None:
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=FastExecutor(),
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    )

    record = service.submit(
        JobCommand(
            kind=JobKind.RESOLVE_CHAPTER_CHECKPOINT,
            record_kind=JobKind.PREPARE_CHAPTER,
            project_id="demo",
            payload={"project_id": "demo", "chapter_number": 1},
        )
    )

    assert record.kind == JobKind.PREPARE_CHAPTER


def test_job_service_rejects_duplicate_project_writer_by_returning_active_job(
    tmp_path: Path,
) -> None:
    executor = BlockingExecutor()
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=executor,
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    )
    first = service.submit(
        JobCommand(
            kind=JobKind.RUN_CHAPTER,
            project_id="demo",
            payload={"project_id": "demo", "chapter_number": 1},
        )
    )
    _wait_for_status(service, first.job_id, JobState.RUNNING)
    _wait_for_release_key(executor, "demo")

    second = service.submit(
        JobCommand(
            kind=JobKind.REPAIR_ISSUES,
            project_id="demo",
            payload={"project_id": "demo", "chapter_number": 1},
        )
    )

    assert second.job_id == first.job_id
    executor.release["demo"].set()
    _wait_for_status(service, first.job_id, JobState.SUCCEEDED)


def test_job_service_limits_concurrency_and_starts_queued_jobs(tmp_path: Path) -> None:
    executor = BlockingExecutor()
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=executor,
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
        max_concurrent_jobs=1,
    )
    first = service.submit(
        JobCommand(
            kind=JobKind.RUN_CHAPTER,
            project_id="demo-a",
            payload={"project_id": "demo-a", "chapter_number": 1},
        )
    )
    second = service.submit(
        JobCommand(
            kind=JobKind.RUN_CHAPTER,
            project_id="demo-b",
            payload={"project_id": "demo-b", "chapter_number": 1},
        )
    )
    _wait_for_status(service, first.job_id, JobState.RUNNING)
    _wait_for_release_key(executor, "demo-a")
    assert service.get(second.job_id).status == JobState.QUEUED  # type: ignore[union-attr]

    executor.release["demo-a"].set()
    _wait_for_status(service, first.job_id, JobState.SUCCEEDED)
    _wait_for_status(service, second.job_id, JobState.RUNNING)
    _wait_for_release_key(executor, "demo-b")
    executor.release["demo-b"].set()
    _wait_for_status(service, second.job_id, JobState.SUCCEEDED)


def test_tts_lane_runs_beside_novel_job_for_same_project(tmp_path: Path) -> None:
    executor = KindBlockingExecutor()
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=executor,
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
        max_concurrent_jobs=2,
        max_concurrent_tts_jobs=1,
    )
    tts = service.submit(
        JobCommand(
            kind=JobKind.TTS_POST_ARCHIVE,
            project_id="demo",
            payload={"project_id": "demo", "chapter_number": 1},
        )
    )
    _wait_for_status(service, tts.job_id, JobState.RUNNING)
    tts_key = _wait_for_kind_release_key(executor, JobKind.TTS_POST_ARCHIVE, "demo")

    novel = service.submit(
        JobCommand(
            kind=JobKind.RUN_CHAPTER,
            project_id="demo",
            payload={"project_id": "demo", "chapter_number": 2},
        )
    )
    _wait_for_status(service, novel.job_id, JobState.RUNNING)
    novel_key = _wait_for_kind_release_key(executor, JobKind.RUN_CHAPTER, "demo")

    executor.release[novel_key].set()
    executor.release[tts_key].set()
    _wait_for_status(service, novel.job_id, JobState.SUCCEEDED)
    _wait_for_status(service, tts.job_id, JobState.SUCCEEDED)


def test_tts_lane_queues_extra_audio_without_blocking_novel_priority(tmp_path: Path) -> None:
    executor = KindBlockingExecutor()
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=executor,
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
        max_concurrent_jobs=2,
        max_concurrent_tts_jobs=1,
    )
    first_tts = service.submit(
        JobCommand(
            kind=JobKind.TTS_POST_ARCHIVE,
            project_id="audio-a",
            payload={"project_id": "audio-a", "chapter_number": 1},
        )
    )
    first_key = _wait_for_kind_release_key(
        executor,
        JobKind.TTS_POST_ARCHIVE,
        "audio-a",
    )
    second_tts = service.submit(
        JobCommand(
            kind=JobKind.TTS_POST_ARCHIVE,
            project_id="audio-b",
            payload={"project_id": "audio-b", "chapter_number": 1},
        )
    )
    assert service.get(second_tts.job_id).status == JobState.QUEUED  # type: ignore[union-attr]

    novel = service.submit(
        JobCommand(
            kind=JobKind.RUN_CHAPTER,
            project_id="novel",
            payload={"project_id": "novel", "chapter_number": 1},
        )
    )
    _wait_for_status(service, novel.job_id, JobState.RUNNING)
    novel_key = _wait_for_kind_release_key(executor, JobKind.RUN_CHAPTER, "novel")
    assert service.get(second_tts.job_id).status == JobState.QUEUED  # type: ignore[union-attr]

    executor.release[novel_key].set()
    _wait_for_status(service, novel.job_id, JobState.SUCCEEDED)
    executor.release[first_key].set()
    _wait_for_status(service, first_tts.job_id, JobState.SUCCEEDED)
    _wait_for_status(service, second_tts.job_id, JobState.RUNNING)
    second_key = _wait_for_kind_release_key(executor, JobKind.TTS_POST_ARCHIVE, "audio-b")
    executor.release[second_key].set()
    _wait_for_status(service, second_tts.job_id, JobState.SUCCEEDED)


def test_tts_lane_reserves_one_global_worker_for_novel_jobs(tmp_path: Path) -> None:
    executor = KindBlockingExecutor()
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=executor,
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
        max_concurrent_jobs=3,
        max_concurrent_tts_jobs=3,
    )
    audio_jobs = [
        service.submit(
            JobCommand(
                kind=JobKind.TTS_POST_ARCHIVE,
                project_id=f"audio-{index}",
                payload={"project_id": f"audio-{index}", "chapter_number": 1},
            )
        )
        for index in range(3)
    ]
    first_key = _wait_for_kind_release_key(executor, JobKind.TTS_POST_ARCHIVE, "audio-0")
    second_key = _wait_for_kind_release_key(executor, JobKind.TTS_POST_ARCHIVE, "audio-1")
    assert service.get(audio_jobs[2].job_id).status == JobState.QUEUED  # type: ignore[union-attr]

    novel = service.submit(
        JobCommand(
            kind=JobKind.RUN_CHAPTER,
            project_id="novel",
            payload={"project_id": "novel", "chapter_number": 1},
        )
    )
    _wait_for_status(service, novel.job_id, JobState.RUNNING)
    novel_key = _wait_for_kind_release_key(executor, JobKind.RUN_CHAPTER, "novel")

    executor.release[novel_key].set()
    executor.release[first_key].set()
    executor.release[second_key].set()
    _wait_for_status(service, novel.job_id, JobState.SUCCEEDED)
    _wait_for_status(service, audio_jobs[0].job_id, JobState.SUCCEEDED)
    _wait_for_status(service, audio_jobs[1].job_id, JobState.SUCCEEDED)
    _wait_for_status(service, audio_jobs[2].job_id, JobState.RUNNING)
    third_key = _wait_for_kind_release_key(executor, JobKind.TTS_POST_ARCHIVE, "audio-2")
    executor.release[third_key].set()
    _wait_for_status(service, audio_jobs[2].job_id, JobState.SUCCEEDED)


def test_tts_lane_deduplicates_same_chapter_followup(tmp_path: Path) -> None:
    executor = KindBlockingExecutor()
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=executor,
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
        max_concurrent_jobs=2,
        max_concurrent_tts_jobs=1,
    )
    command = JobCommand(
        kind=JobKind.TTS_POST_ARCHIVE,
        project_id="demo",
        payload={"project_id": "demo", "chapter_number": 3},
    )

    first = service.submit(command)
    key = _wait_for_kind_release_key(executor, JobKind.TTS_POST_ARCHIVE, "demo")
    duplicate = service.submit(command)

    assert duplicate.job_id == first.job_id
    executor.release[key].set()
    _wait_for_status(service, first.job_id, JobState.SUCCEEDED)


def test_job_service_replays_snapshot_steps_and_terminal_event(tmp_path: Path) -> None:
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=FastExecutor(),
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    )
    record = service.submit(
        JobCommand(
            kind=JobKind.RUN_CHAPTER,
            project_id="demo",
            payload={"project_id": "demo", "chapter_number": 1},
        )
    )
    _wait_for_status(service, record.job_id, JobState.SUCCEEDED)

    replayed = service.replay_events(record.job_id)
    types = [event.type for event in replayed]

    assert JobEventType.JOB_SNAPSHOT in types
    assert JobEventType.JOB_STEP in types
    assert types[-1] == JobEventType.JOB_SUCCEEDED


def test_job_service_emits_section_changed_for_terminal_writer(tmp_path: Path) -> None:
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=FastExecutor(),
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    )
    subscription = service.open_subscription()
    record = service.submit(
        JobCommand(
            kind=JobKind.RUN_CHAPTER,
            project_id="demo",
            payload={"project_id": "demo", "chapter_number": 1},
        )
    )
    _wait_for_status(service, record.job_id, JobState.SUCCEEDED)

    section_payloads: list[dict[str, Any]] = []
    deadline = time.time() + 2
    while time.time() < deadline and not section_payloads:
        event = subscription.get(timeout=0.1)
        if event is not None and event.type == JobEventType.SECTION_CHANGED:
            section_payloads.append(event.payload)
    subscription.close()

    assert section_payloads == [{"project_id": "demo", "section": "chapters"}]


def test_job_service_cancel_marks_failed_without_late_success(tmp_path: Path) -> None:
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=SlowExecutor(),
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    )
    record = service.submit(
        JobCommand(
            kind=JobKind.RUN_CHAPTER,
            project_id="demo",
            payload={"project_id": "demo", "chapter_number": 1},
        )
    )
    _wait_for_status(service, record.job_id, JobState.RUNNING)

    cancelled = service.cancel(record.job_id, reason="stop")

    assert cancelled.status == JobState.FAILED
    assert cancelled.current_step == "cancelled"
    assert cancelled.error == "stop"


def test_job_service_shutdown_cancels_running_and_queued_jobs(tmp_path: Path) -> None:
    executor = BlockingExecutor()
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=executor,
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
        max_concurrent_jobs=1,
    )
    first = service.submit(
        JobCommand(
            kind=JobKind.RUN_CHAPTER,
            project_id="demo-a",
            payload={"project_id": "demo-a", "chapter_number": 1},
        )
    )
    second = service.submit(
        JobCommand(
            kind=JobKind.RUN_CHAPTER,
            project_id="demo-b",
            payload={"project_id": "demo-b", "chapter_number": 1},
        )
    )
    _wait_for_status(service, first.job_id, JobState.RUNNING)
    _wait_for_release_key(executor, "demo-a")

    service.shutdown(wait_s=0.1, reason="closing")
    if "demo-a" in executor.release:
        executor.release["demo-a"].set()

    assert service.get(first.job_id).status == JobState.FAILED  # type: ignore[union-attr]
    assert service.get(second.job_id).status == JobState.FAILED  # type: ignore[union-attr]


def test_job_service_loads_legacy_history_payload(tmp_path: Path) -> None:
    history_path = tmp_path / "demo" / "states" / "task_flow_history.json"
    history_path.parent.mkdir(parents=True)
    history_path.write_text(
        json.dumps(
            [
                {
                    "job_id": "job-old",
                    "kind": "run_chapter",
                    "label": "old",
                    "project_id": "demo",
                    "status": "succeeded",
                    "created_at": "2026-06-01T00:00:00+00:00",
                    "updated_at": "2026-06-01T00:00:00+00:00",
                    "events": [{"at": "x", "step": "draft", "payload": {}}],
                }
            ]
        ),
        encoding="utf-8",
    )

    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=True,
        executor=FastExecutor(),
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    )

    jobs = service.list(project_id="demo")
    assert [job.job_id for job in jobs] == ["job-old"]
    assert jobs[0].status == JobState.SUCCEEDED


def test_desktop_job_record_round_trips_through_app_contract() -> None:
    desktop = DesktopJobRecord(
        job_id="job-1",
        kind="run_chapter",
        label="章节续写",
        project_id="demo",
        status=DesktopJobState.RUNNING,
        current_step="draft",
        current_step_payload={"chapter": 3, "stage": "generate"},
        events=[DesktopJobEvent(at="now", step="draft", payload={"x": 1})],
        cumulative_tokens=8,
        cumulative_cost_usd=0.1,
    )

    app_record = desktop_job_record_to_app_record(desktop)
    restored = app_job_record_to_desktop_record(app_record)

    assert app_record.status == JobState.RUNNING
    assert restored.status == DesktopJobState.RUNNING
    assert restored.current_step_payload == {"chapter": 3, "stage": "generate"}
    assert restored.events[0].payload == {"x": 1}
    assert restored.cumulative_tokens == 8
