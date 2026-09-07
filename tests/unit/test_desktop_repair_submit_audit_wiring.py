"""Desktop repair submit paths must preserve audit update wiring through JobService."""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from novel_forge.app_service import workspace_commands
from novel_forge.app_service.contracts import JobCommand, JobKind, JobRecord
from novel_forge.app_service.workspace_commands import WorkspaceCommandExecutor
from novel_forge.desktop.jobs import DesktopJobManager
from novel_forge.workspace.contracts import (
    RepairCausalRequest,
    RepairContinuityRequest,
    RepairIssuesRequest,
)
from novel_forge.workspace.execution_result import ExecutionResult


class _StubSubscription:
    def get(self, timeout: float | None = None) -> None:
        return None

    def close(self) -> None:
        return None


class _StubJobService:
    def __init__(self) -> None:
        self.submitted: list[JobCommand] = []

    def open_subscription(self, job_id: str | None = None) -> _StubSubscription:
        return _StubSubscription()

    def submit(self, command: JobCommand) -> JobRecord:
        self.submitted.append(command)
        return JobRecord(
            job_id=command.job_id or "job-1",
            kind=command.kind,
            label=command.label,
            project_id=command.project_id,
        )

    def shutdown(self, *, wait_s: float = 0.0, reason: str = "") -> None:
        return None


@pytest.fixture()
def job_service() -> _StubJobService:
    return _StubJobService()


@pytest.fixture()
def manager(job_service: _StubJobService):
    mgr = DesktopJobManager(job_service=job_service, load_persisted_history=False)
    mgr._publish_event = MagicMock()
    yield mgr
    mgr.shutdown()


@pytest.fixture()
def runtime_stub():
    return SimpleNamespace(
        settings=SimpleNamespace(
            repair_control_mode="ai_auto",
            long_repair_max_change_ratio=1.0,
        )
    )


def _drive_repair_command(
    command: JobCommand,
    *,
    monkeypatch: pytest.MonkeyPatch,
    runtime_stub: Any,
) -> tuple[dict[str, Any], list[tuple[str, Any]], dict[str, Any]]:
    received: dict[str, Any] = {}

    async def _stub_execute_repair(
        runtime,
        mission,
        *,
        on_step_progress=None,
        on_audit_update=None,
        **kw,
    ):
        received["runtime"] = runtime
        received["mission"] = mission
        received["on_step_progress"] = on_step_progress
        received["on_audit_update"] = on_audit_update
        received["extra_kwargs"] = kw
        if on_audit_update is not None:
            chapter_number = mission.targets[0].chapter_number if mission.targets else -1
            on_audit_update(
                getattr(mission, "project_id", "?"),
                int(chapter_number or -1),
                {"score": 0.9, "critique": {"issues": []}},
            )
        return ExecutionResult(
            project_id=mission.project_id,
            result=SimpleNamespace(applied=True, warnings=[]),
        )

    async def _fake_execute_with_project_logger(
        *,
        runtime,
        project_id,
        command,
        metadata,
        on_step,
        execute,
    ):
        return await execute(on_step), "logs/test-run"

    monkeypatch.setattr(workspace_commands, "execute_repair", _stub_execute_repair)
    monkeypatch.setattr(
        workspace_commands,
        "execute_with_project_logger",
        _fake_execute_with_project_logger,
    )

    on_step_calls: list[tuple[str, Any]] = []
    executor = WorkspaceCommandExecutor()
    prepared = executor.prepare(command)
    payload = asyncio.run(
        executor.run(
            prepared,
            runtime_stub,
            lambda step, data: on_step_calls.append((step, data)),
        )
    )
    return received, on_step_calls, payload


def test_submit_repair_continuity_uses_job_service_and_preserves_audit_update(
    manager: DesktopJobManager,
    job_service: _StubJobService,
    monkeypatch: pytest.MonkeyPatch,
    runtime_stub,
) -> None:
    request = RepairContinuityRequest(
        project_id="proj-1",
        chapter_number=5,
        issue_indices=[0, 1],
    )
    manager.submit_repair_continuity(request, mock=True)

    command = job_service.submitted[0]
    assert command.kind == JobKind.REPAIR_CONTINUITY
    assert command.metadata["command_name"] == "desktop-repair-continuity"
    assert command.payload["issue_indices"] == [0, 1]

    received, on_step_calls, payload = _drive_repair_command(
        command,
        monkeypatch=monkeypatch,
        runtime_stub=runtime_stub,
    )

    assert received["on_audit_update"] is not None
    assert callable(received["on_audit_update"])
    assert received["mission"].targets[0].domain.value == "continuity"
    relayed = [call for call in on_step_calls if call[0] == "audit_result_update"]
    assert len(relayed) == 1
    assert relayed[0][1] == {
        "chapter_number": 5,
        "audit_result": {"score": 0.9, "critique": {"issues": []}},
    }
    assert payload["project_id"] == "proj-1"
    assert payload["chapter_number"] == 5
    assert payload["run_log_dir"] == "logs/test-run"


def test_submit_repair_causal_uses_job_service_and_preserves_audit_update(
    manager: DesktopJobManager,
    job_service: _StubJobService,
    monkeypatch: pytest.MonkeyPatch,
    runtime_stub,
) -> None:
    request = RepairCausalRequest(
        project_id="proj-1",
        chapter_number=7,
        issue_indices=[],
    )
    manager.submit_repair_causal(request, mock=True)

    command = job_service.submitted[0]
    assert command.kind == JobKind.REPAIR_CAUSAL
    assert command.metadata["command_name"] == "desktop-repair-causal"

    received, on_step_calls, _payload = _drive_repair_command(
        command,
        monkeypatch=monkeypatch,
        runtime_stub=runtime_stub,
    )

    assert received["on_audit_update"] is not None
    assert callable(received["on_audit_update"])
    assert received["mission"].targets[0].domain.value == "causal"
    relayed = [call for call in on_step_calls if call[0] == "audit_result_update"]
    assert len(relayed) == 1
    assert relayed[0][1]["chapter_number"] == 7
    assert "audit_result" in relayed[0][1]


def test_submit_repair_issues_uses_job_service_and_preserves_audit_update(
    manager: DesktopJobManager,
    job_service: _StubJobService,
    monkeypatch: pytest.MonkeyPatch,
    runtime_stub,
) -> None:
    request = RepairIssuesRequest(
        project_id="proj-1",
        chapter_number=9,
        continuity_issue_indices=[0],
        causal_issue_indices=[1, 2],
    )
    manager.submit_repair_issues(request, mock=True)

    command = job_service.submitted[0]
    assert command.kind == JobKind.REPAIR_ISSUES
    assert command.metadata["command_name"] == "desktop-repair-issues"
    assert command.payload["continuity_issue_indices"] == [0]
    assert command.payload["causal_issue_indices"] == [1, 2]

    received, on_step_calls, _payload = _drive_repair_command(
        command,
        monkeypatch=monkeypatch,
        runtime_stub=runtime_stub,
    )

    assert received["on_audit_update"] is not None
    assert callable(received["on_audit_update"])
    assert [target.domain.value for target in received["mission"].targets] == [
        "continuity",
        "causal",
        "causal",
    ]
    assert [target.payload.get("issue_indices") for target in received["mission"].targets] == [
        [0],
        [1],
        [2],
    ]
    relayed = [call for call in on_step_calls if call[0] == "audit_result_update"]
    assert len(relayed) == 1
    assert relayed[0][1]["chapter_number"] == 9


def test_execute_repair_signature_accepts_on_audit_update() -> None:
    from novel_forge.workspace.repair_ops.execution_repair_v2 import execute_repair

    sig = inspect.signature(execute_repair)
    assert "on_audit_update" in sig.parameters
