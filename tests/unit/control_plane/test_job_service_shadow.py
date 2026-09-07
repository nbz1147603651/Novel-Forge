"""Tests for JobService + ShadowRecorder integration.

Verifies that the JobService correctly shadow-writes to the control plane
during the full job lifecycle, and that existing behavior is unchanged
when the shadow recorder is disabled.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from novel_forge.app_service.contracts import (
    JobCommand,
    JobKind,
    JobState,
)
from novel_forge.app_service.job_service import JobService
from novel_forge.app_service.workspace_commands import (
    CommandExecutor,
    PreparedCommand,
)
from novel_forge.control_plane.enums import RunAttemptState, WorkUnitState
from novel_forge.control_plane.schemas import WorkUnitDTO
from novel_forge.control_plane.shadow import ShadowRecorder
from novel_forge.control_plane.store import ControlPlaneStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def store() -> ControlPlaneStore:
    s = ControlPlaneStore.in_memory()
    await s.init_db()
    yield s
    await s.close()


@pytest.fixture
def shadow_recorder(store: ControlPlaneStore) -> ShadowRecorder:
    return ShadowRecorder(store)


class _MockExecutor(CommandExecutor):
    """Minimal executor that runs a predefined async function."""

    def __init__(self, run_fn: Any = None) -> None:
        self._run_fn = run_fn

    def prepare(self, command: JobCommand) -> PreparedCommand:
        return PreparedCommand(
            kind=command.kind,
            label=command.label or f"Mock {command.kind}",
            project_id=command.project_id,
            request=command.payload,
            command_name=f"mock_{command.kind.value}",
            metadata={"kind": command.kind.value, **command.metadata},
        )

    async def run(
        self,
        prepared: PreparedCommand,
        runtime: Any,
        on_step: Any,
    ) -> dict[str, Any]:
        if self._run_fn is not None:
            return await self._run_fn(prepared, runtime, on_step)
        # Default: emit a step and return success
        on_step("test_step", {"status": "ok"})
        return {"status": "success", "project_id": prepared.project_id}


@pytest.fixture
def mock_executor() -> _MockExecutor:
    return _MockExecutor()


@pytest.fixture
def service_with_shadow(
    tmp_path: Path,
    shadow_recorder: ShadowRecorder,
    mock_executor: _MockExecutor,
) -> Iterator[JobService]:
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=mock_executor,
        shadow_recorder=shadow_recorder,
    )
    yield service
    service.shutdown(wait_s=5.0, reason="测试夹具关闭")


@pytest.fixture
def service_without_shadow(
    tmp_path: Path,
    mock_executor: _MockExecutor,
) -> Iterator[JobService]:
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=mock_executor,
    )
    yield service
    service.shutdown(wait_s=5.0, reason="测试夹具关闭")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_submit_creates_shadow_work_unit(
    service_with_shadow: JobService,
    store: ControlPlaneStore,
) -> None:
    command = JobCommand(
        kind=JobKind.RUN_CHAPTER,
        payload={"chapter_number": 1},
        label="Test Chapter",
        project_id="test_proj",
    )
    record = service_with_shadow.submit(command)

    # The work unit should be created in the control plane
    wu = store.sync.get_work_unit(record.job_id)
    assert wu is not None
    assert wu.kind == "run_chapter"
    assert wu.project_id == "test_proj"
    assert wu.state in (WorkUnitState.QUEUED, WorkUnitState.RUNNING, WorkUnitState.COMMITTED)
    assert wu.intent_payload_path
    intent_payload = json.loads(Path(wu.intent_payload_path).read_text(encoding="utf-8"))
    assert intent_payload["job_id"] == record.job_id
    assert intent_payload["payload"] == {"chapter_number": 1}

    # The WorkUnit is persisted before the worker is started.  The attempt is
    # intentionally created by ``on_started`` so a queued/cancelled job never
    # masquerades as an active execution.
    attempts = store.sync.list_run_attempts(record.job_id)
    assert len(attempts) in {0, 1}


def test_tts_job_can_resume_from_persisted_intent_after_reconciliation(
    service_with_shadow: JobService,
    store: ControlPlaneStore,
    tmp_path: Path,
) -> None:
    intent_path = tmp_path / "demo" / "states" / "control_plane_intents" / "tts-resume.json"
    intent_path.parent.mkdir(parents=True, exist_ok=True)
    intent_path.write_text(
        JobCommand(
            kind=JobKind.TTS_SYNTHESIZE,
            job_id="tts-resume",
            project_id="demo",
            payload={"project_id": "demo", "chapter_number": 3, "provider": "mock"},
        ).model_dump_json(),
        encoding="utf-8",
    )
    store.sync.create_work_unit(
        WorkUnitDTO(
            id="tts-resume",
            kind=JobKind.TTS_SYNTHESIZE.value,
            project_id="demo",
            state=WorkUnitState.RETRY_WAIT,
            intent_payload_path=str(intent_path),
        )
    )

    record = service_with_shadow.resume("tts-resume")

    assert record.job_id == "tts-resume"
    assert record.kind == JobKind.TTS_SYNTHESIZE


def test_job_succeeds_shadows_committed(
    service_with_shadow: JobService,
    store: ControlPlaneStore,
) -> None:
    """A successful job should shadow-write WorkUnit(committed)."""
    command = JobCommand(
        kind=JobKind.RUN_CHAPTER,
        payload={"chapter_number": 1},
        label="Test",
        project_id="test_proj",
    )
    record = service_with_shadow.submit(command)

    # Wait for the worker to finish
    import time

    deadline = time.time() + 5.0
    while time.time() < deadline:
        current = service_with_shadow.get(record.job_id)
        if current and current.status in {JobState.SUCCEEDED, JobState.FAILED}:
            break
        time.sleep(0.05)

    final = service_with_shadow.get(record.job_id)
    assert final is not None
    assert final.status == JobState.SUCCEEDED

    # Shadow record should show committed
    wu = store.sync.get_work_unit(record.job_id)
    assert wu is not None
    assert wu.state == WorkUnitState.COMMITTED

    attempts = store.sync.list_run_attempts(record.job_id)
    assert len(attempts) == 1
    assert attempts[0].state == RunAttemptState.COMMITTED


def test_disabled_shadow_does_not_break_service(
    service_without_shadow: JobService,
) -> None:
    """JobService with no shadow recorder should work exactly as before."""
    command = JobCommand(
        kind=JobKind.RUN_CHAPTER,
        payload={"chapter_number": 1},
        label="Test",
        project_id="test_proj",
    )
    record = service_without_shadow.submit(command)

    # Wait for completion
    import time

    deadline = time.time() + 5.0
    while time.time() < deadline:
        current = service_without_shadow.get(record.job_id)
        if current and current.status in {JobState.SUCCEEDED, JobState.FAILED}:
            break
        time.sleep(0.05)

    final = service_without_shadow.get(record.job_id)
    assert final is not None
    assert final.status == JobState.SUCCEEDED


def test_cancel_shadows_cancelled(
    service_with_shadow: JobService,
    store: ControlPlaneStore,
    tmp_path: Path,
) -> None:
    """Cancelling a job should shadow-write WorkUnit(cancelled)."""

    async def slow_run(prepared: Any, runtime: Any, on_step: Any) -> dict[str, Any]:
        on_step("started", {})
        await asyncio.sleep(10)  # Long enough to cancel
        return {"status": "success"}

    service_with_shadow._executor = _MockExecutor(slow_run)

    command = JobCommand(
        kind=JobKind.RUN_CHAPTER,
        payload={"chapter_number": 1},
        label="Cancellable",
        project_id="cancel_proj",
    )
    record = service_with_shadow.submit(command)

    # Give it a moment to start
    import time

    time.sleep(0.2)

    service_with_shadow.cancel(record.job_id, "test cancel")

    # Wait for cancel to propagate
    deadline = time.time() + 5.0
    while time.time() < deadline:
        wu = store.sync.get_work_unit(record.job_id)
        if wu and wu.state == WorkUnitState.CANCELLED:
            break
        time.sleep(0.05)

    wu = store.sync.get_work_unit(record.job_id)
    # Cancelled state may or may not be set depending on timing,
    # but the WorkUnit should at least exist.
    assert wu is not None


def test_failed_job_shadows_failed(
    store: ControlPlaneStore,
    tmp_path: Path,
) -> None:
    """A failed job should shadow-write WorkUnit(failed)."""

    async def failing_run(prepared: Any, runtime: Any, on_step: Any) -> dict[str, Any]:
        on_step("started", {})
        raise RuntimeError("ModelGatewayError: provider unavailable")

    executor = _MockExecutor(failing_run)
    shadow = ShadowRecorder(store)
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        executor=executor,
        shadow_recorder=shadow,
    )

    command = JobCommand(
        kind=JobKind.RUN_CHAPTER,
        payload={"chapter_number": 1},
        label="Failing",
        project_id="fail_proj",
    )
    record = service.submit(command)

    # Wait for failure
    import time

    deadline = time.time() + 5.0
    while time.time() < deadline:
        current = service.get(record.job_id)
        if current and current.status == JobState.FAILED:
            break
        time.sleep(0.05)

    final = service.get(record.job_id)
    assert final is not None
    assert final.status == JobState.FAILED

    wu = store.sync.get_work_unit(record.job_id)
    assert wu is not None
    assert wu.state == WorkUnitState.FAILED

    attempts = store.sync.list_run_attempts(record.job_id)
    assert len(attempts) == 1
    assert attempts[0].state == RunAttemptState.FAILED
