from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from novel_forge.app_service.autorun_chain import (
    build_autorun_followup_command,
    next_autorun_chapter_number,
)
from novel_forge.app_service.contracts import (
    JobCommand,
    JobKind,
    JobRecord,
    JobState,
)
from novel_forge.app_service.job_service import JobService
from novel_forge.app_service.workspace_commands import PreparedCommand


class StubRuntime:
    async def shutdown(self) -> None:
        return None


class RecordingExecutor:
    """Executor stub that records every prepared command it runs."""

    def __init__(self) -> None:
        self.prepared: list[PreparedCommand] = []

    def prepare(self, command: JobCommand) -> PreparedCommand:
        return PreparedCommand(
            kind=command.kind,
            request=command.payload,
            label=command.label or "stub job",
            project_id=command.project_id or str(command.payload.get("project_id") or "demo"),
            command_name="stub",
            metadata=dict(command.metadata),
        )

    async def run(
        self,
        prepared: PreparedCommand,
        runtime: Any,
        on_step: Any,
    ) -> dict[str, Any]:
        self.prepared.append(prepared)
        on_step("working", {})
        return {"project_id": prepared.project_id, "ok": True}


def _runtime_factory(_mock: bool) -> StubRuntime:
    return StubRuntime()


def _make_service(tmp_path: Path, executor: RecordingExecutor) -> JobService:
    return JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=executor,
        runtime_factory=_runtime_factory,  # type: ignore[arg-type]
    )


def _wait_until(predicate: Any, *, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition was not met within timeout")


def test_autorun_init_success_chains_prepare_chapter(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    service = _make_service(tmp_path, executor)
    record = service.submit(
        JobCommand(
            kind=JobKind.INIT_LONG,
            project_id="chain-demo",
            payload={"project_id": "chain-demo", "premise": "p"},
            metadata={"run_mode": "autorun", "autorun_after_init": True},
        )
    )

    _wait_until(lambda: service.get(record.job_id) is not None
                and service.get(record.job_id).status == JobState.SUCCEEDED)

    def _has_chained_prepare() -> bool:
        return any(
            str(item.kind) == JobKind.PREPARE_CHAPTER.value
            or getattr(item.kind, "value", item.kind) == JobKind.PREPARE_CHAPTER.value
            for item in executor.prepared
        )

    _wait_until(_has_chained_prepare)
    chained = [
        item
        for item in executor.prepared
        if getattr(item.kind, "value", item.kind) == JobKind.PREPARE_CHAPTER.value
    ]
    assert len(chained) == 1
    payload = chained[0].request
    assert payload["project_id"] == "chain-demo"
    assert payload["chapter_number"] == 1
    assert payload["rewrite_strategy"] == "auto"
    metadata = chained[0].metadata
    assert metadata.get("autorun_chained_from") == record.job_id
    assert metadata.get("run_mode") == "autorun"

    chained_jobs = [
        item
        for item in service.list(project_id="chain-demo")
        if str(item.kind) == JobKind.PREPARE_CHAPTER.value
        or getattr(item.kind, "value", item.kind) == JobKind.PREPARE_CHAPTER.value
    ]
    assert len(chained_jobs) == 1
    service.shutdown(wait_s=1.0)


def test_init_without_autorun_flag_does_not_chain(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    service = _make_service(tmp_path, executor)
    record = service.submit(
        JobCommand(
            kind=JobKind.INIT_LONG,
            project_id="plain-demo",
            payload={"project_id": "plain-demo", "premise": "p"},
            metadata={"run_mode": "create"},
        )
    )

    _wait_until(lambda: service.get(record.job_id) is not None
                and service.get(record.job_id).status == JobState.SUCCEEDED)
    time.sleep(0.15)
    assert all(
        getattr(item.kind, "value", item.kind) == JobKind.INIT_LONG.value
        for item in executor.prepared
    )
    service.shutdown(wait_s=1.0)


def test_failed_autorun_init_does_not_chain(tmp_path: Path) -> None:
    class FailingExecutor(RecordingExecutor):
        async def run(
            self,
            prepared: PreparedCommand,
            runtime: Any,
            on_step: Any,
        ) -> dict[str, Any]:
            self.prepared.append(prepared)
            raise RuntimeError("simulated init failure")

    executor = FailingExecutor()
    service = _make_service(tmp_path, executor)
    record = service.submit(
        JobCommand(
            kind=JobKind.INIT_LONG,
            project_id="fail-demo",
            payload={"project_id": "fail-demo", "premise": "p"},
            metadata={"autorun_after_init": True},
        )
    )

    _wait_until(lambda: service.get(record.job_id) is not None
                and service.get(record.job_id).status == JobState.FAILED)
    time.sleep(0.15)
    assert service._autorun_pending_ids == set()
    assert len(executor.prepared) == 1
    service.shutdown(wait_s=1.0)


def test_build_autorun_followup_command_rejects_non_init_records() -> None:
    record = JobRecord(kind=JobKind.RUN_CHAPTER, label="x", project_id="demo")
    assert build_autorun_followup_command(record, None) is None


def test_next_autorun_chapter_number_falls_back_to_one(tmp_path: Path) -> None:
    assert next_autorun_chapter_number(None, "demo") == 1
    assert next_autorun_chapter_number(tmp_path, "") == 1
    # Unknown project: inspection fails and the helper degrades to chapter 1.
    assert next_autorun_chapter_number(tmp_path, "missing-project") == 1
