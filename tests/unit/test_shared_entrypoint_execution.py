"""Tests that CLI/API/Desktop delegate chapter execution through shared contracts."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from novel_forge.api.app import create_app
from novel_forge.api.deps import get_runtime_services
from novel_forge.api.routes import chapter as chapter_routes
from novel_forge.app_service import workspace_commands
from novel_forge.app_service.contracts import JobCommand, JobEvent, JobEventType, JobKind, JobRecord
from novel_forge.app_service.workspace_commands import WorkspaceCommandExecutor
from novel_forge.cli.commands import run_chapter as run_chapter_command
from novel_forge.desktop.jobs import DesktopJobManager, DesktopJobRecord, DesktopJobState
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.workspace.contracts import (
    BookConsistencyRequest,
    ExportBookRequest,
    ExtendOutlineRequest,
    ManualRevisionRequest,
    PolishChapterRequest,
    PrepareChapterRequest,
    RebuildMemoryVectorsRequest,
    ReevaluateChapterRequest,
    ReextractRelationshipsRequest,
    RepairMotifHistoryRequest,
    ResolveChapterCheckpointRequest,
    RunChapterRequest,
    SyncChapterContractsRequest,
)
from novel_forge.workspace.runtime import RuntimeServices


class _StubSubscription:
    def get(self, timeout: float | None = None) -> None:
        return None

    def close(self) -> None:
        return None


class _StubJobService:
    def __init__(self) -> None:
        self.submitted: list[JobCommand] = []
        self.cancelled: list[str] = []
        self.decisions: list[tuple[str, dict[str, Any]]] = []

    def open_subscription(self, job_id: str | None = None) -> _StubSubscription:
        return _StubSubscription()

    def submit(self, command: JobCommand) -> JobRecord:
        self.submitted.append(command)
        return JobRecord(
            job_id=command.job_id or "job-1",
            kind=command.record_kind or command.kind,
            label=command.label,
            project_id=command.project_id,
        )

    def cancel(self, job_id: str, reason: str = "") -> JobRecord:
        self.cancelled.append(job_id)
        return JobRecord(job_id=job_id, kind=JobKind.RUN_CHAPTER, label="", status="failed")

    def provide_decision(self, job_id: str, payload: dict[str, Any]) -> JobRecord:
        self.decisions.append((job_id, payload))
        return JobRecord(job_id=job_id, kind=JobKind.RUN_CHAPTER, label="")

    def shutdown(self, *, wait_s: float = 0.0, reason: str = "") -> None:
        return None


def _make_runtime(tmp_path: Path, runtime_settings: Any) -> RuntimeServices:
    return RuntimeServices(
        settings=runtime_settings,
        router=ModelRouter(adapters={"mock": MockAdapter()}, default_provider="mock"),
        builder=PromptBuilder(),
        storage=FileSystemStorage(tmp_path),
    )


def _fake_chapter_result(chapter_number: int) -> SimpleNamespace:
    return SimpleNamespace(
        meta=SimpleNamespace(chapter_number=chapter_number, word_count=1234),
        text="章节正文" * 40,
        eval_report=SimpleNamespace(overall_score=8.4),
        continuity_report=SimpleNamespace(continuity_score=7.8, issues=[]),
        causal_report=SimpleNamespace(
            causal_score=9.3,
            issues=[
                SimpleNamespace(summary="开头承接偏弱"),
                SimpleNamespace(summary="转折因果略跳"),
            ],
        ),
        bridge=SimpleNamespace(bridge_summary=f"bridge-{chapter_number}"),
        chapter_exit_state=SimpleNamespace(must_carry_forward=["下一章线索"]),
        trace_summary={"total_tokens": 321, "total_cost_usd": 0.0123},
        creative_report=None,
        warnings=["因果链校验发现 2 个问题，其中高优先级 1 个，建议人工复核。"],
    )


def test_cli_run_chapter_uses_shared_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runtime_settings: Any,
) -> None:
    runtime = _make_runtime(tmp_path, runtime_settings)
    captured: dict[str, object] = {}

    async def _fake_execute(
        runtime_obj: RuntimeServices,
        request_obj: RunChapterRequest,
        *,
        on_step_progress: object | None = None,
    ) -> SimpleNamespace:
        captured["runtime"] = runtime_obj
        captured["request"] = request_obj
        captured["has_callback"] = on_step_progress is not None
        return SimpleNamespace(project_id=request_obj.project_id, result=_fake_chapter_result(2))

    import novel_forge.workspace.execution as execution_module

    monkeypatch.setattr(run_chapter_command, "get_settings", lambda: runtime_settings)
    monkeypatch.setattr(
        run_chapter_command,
        "_create_runtime_services",
        lambda *, settings, mock: runtime,
    )
    monkeypatch.setattr(execution_module, "execute_run_chapter", _fake_execute)

    run_chapter_command.run_chapter(
        project_id="cli_demo",
        chapter=2,
        auto=False,
        end_chapter=0,
        ai_judge_apply_mode="assist",
        force=True,
        mock=False,
        verbose=False,
        config=None,
    )

    request = captured["request"]
    assert isinstance(request, RunChapterRequest)
    assert captured["runtime"] is runtime
    assert captured["has_callback"] is True
    assert request.project_id == "cli_demo"
    assert request.chapter_number == 2
    assert request.force is True


def test_api_run_chapter_route_uses_shared_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runtime_settings: Any,
) -> None:
    runtime = _make_runtime(tmp_path, runtime_settings)
    captured: dict[str, object] = {}

    async def _fake_execute(
        runtime_obj: RuntimeServices,
        request_obj: RunChapterRequest,
        *,
        on_step_progress: object | None = None,
    ) -> SimpleNamespace:
        captured["runtime"] = runtime_obj
        captured["request"] = request_obj
        return SimpleNamespace(project_id="api_result_project", result=_fake_chapter_result(3))

    monkeypatch.setattr(chapter_routes, "execute_run_chapter", _fake_execute)

    app = create_app()
    app.dependency_overrides[get_runtime_services] = lambda: runtime
    client = TestClient(app)

    response = client.post(
        "/api/v1/chapters/run",
        json={
            "project_id": "api_demo",
            "chapter_number": 3,
            "force": True,
        },
    )

    assert response.status_code == 200
    request = captured["request"]
    assert isinstance(request, RunChapterRequest)
    assert captured["runtime"] is runtime
    assert request.project_id == "api_demo"
    assert request.chapter_number == 3
    assert request.force is True
    assert response.json()["project_id"] == "api_result_project"
    assert response.json()["chapter_number"] == 3
    assert response.json()["causal_score"] == pytest.approx(9.3)
    assert response.json()["causal_issue_count"] == 2
    assert response.json()["warnings"] == [
        "因果链校验发现 2 个问题，其中高优先级 1 个，建议人工复核。"
    ]


def test_api_manual_revision_route_uses_workspace_execution_and_schedules_reevaluate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runtime_settings: Any,
) -> None:
    runtime = _make_runtime(tmp_path, runtime_settings)
    captured: dict[str, object] = {}

    async def _fake_execute(
        runtime_obj: RuntimeServices,
        request_obj: ManualRevisionRequest,
        *,
        on_step_progress: object | None = None,
    ) -> SimpleNamespace:
        captured["runtime"] = runtime_obj
        captured["request"] = request_obj
        captured["has_callback"] = on_step_progress is not None
        return SimpleNamespace(
            project_id=request_obj.project_id,
            result={
                "project_id": request_obj.project_id,
                "chapter_number": request_obj.chapter_number,
                "status": "completed",
                "scope": request_obj.scope,
                "background_reevaluate_requested": request_obj.background_reevaluate,
            },
        )

    async def _fake_background(
        runtime_obj: RuntimeServices,
        *,
        project_id: str,
        chapter_number: int,
    ) -> None:
        captured["background_runtime"] = runtime_obj
        captured["background_project_id"] = project_id
        captured["background_chapter_number"] = chapter_number

    monkeypatch.setattr(chapter_routes, "execute_manual_revision", _fake_execute)
    monkeypatch.setattr(
        chapter_routes,
        "_background_reevaluate_manual_revision",
        _fake_background,
    )

    app = create_app()
    app.dependency_overrides[get_runtime_services] = lambda: runtime
    client = TestClient(app)

    response = client.post(
        "/api/v1/chapters/manual-revision",
        json={
            "project_id": "api_demo",
            "chapter_number": 3,
            "text": "人工改稿正文",
            "scope": "from_chapter_n",
            "background_reevaluate": True,
        },
    )

    assert response.status_code == 200
    request = captured["request"]
    assert isinstance(request, ManualRevisionRequest)
    assert captured["runtime"] is runtime
    assert captured["has_callback"] is True
    assert request.project_id == "api_demo"
    assert request.chapter_number == 3
    assert request.text == "人工改稿正文"
    assert request.scope == "from_chapter_n"
    assert request.background_reevaluate is True
    assert captured["background_runtime"] is runtime
    assert captured["background_project_id"] == "api_demo"
    assert captured["background_chapter_number"] == 3
    assert response.json()["background_reevaluate_scheduled"] is True


def test_desktop_run_chapter_job_uses_shared_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runtime_settings: Any,
) -> None:
    runtime = _make_runtime(tmp_path, runtime_settings)
    runtime.settings = runtime.settings.model_copy(update={"repair_control_mode": "ai_assisted"})
    job_service = _StubJobService()
    manager = DesktopJobManager(job_service=job_service, load_persisted_history=False)
    captured_execute: dict[str, object] = {}

    async def _fake_execute(
        runtime_obj: RuntimeServices,
        request_obj: RunChapterRequest,
        *,
        on_step_progress: object | None = None,
        defer_post_archive_tts: bool = False,
    ) -> SimpleNamespace:
        captured_execute["runtime"] = runtime_obj
        captured_execute["request"] = request_obj
        captured_execute["has_callback"] = on_step_progress is not None
        captured_execute["defer_post_archive_tts"] = defer_post_archive_tts
        captured_execute["repair_control_mode"] = runtime_obj.settings.repair_control_mode
        return SimpleNamespace(project_id="desktop_result_project", result=_fake_chapter_result(4))

    monkeypatch.setattr(workspace_commands, "execute_run_chapter", _fake_execute)
    monkeypatch.setattr(manager._dependency_resolver, "is_ready", lambda *args, **kwargs: True)

    request = RunChapterRequest(
        project_id="desktop_demo",
        chapter_number=4,
        force=True,
        repair_control_mode="ai_auto",
    )
    record = manager.submit_run_chapter(request, mock=True)

    assert record.kind == "run_chapter"
    assert len(job_service.submitted) == 1
    command = job_service.submitted[0]
    assert command.kind == JobKind.RUN_CHAPTER
    assert command.project_id == "desktop_demo"
    assert command.mock is True
    assert command.metadata["command_name"] == "desktop-run-chapter"
    assert command.payload["chapter_number"] == 4

    executor = WorkspaceCommandExecutor()
    prepared = executor.prepare(command)
    payload = asyncio.run(executor.run(prepared, runtime, lambda *_args: None))

    executed_request = captured_execute["request"]
    assert isinstance(executed_request, RunChapterRequest)
    assert captured_execute["runtime"] is runtime
    assert captured_execute["has_callback"] is True
    assert captured_execute["defer_post_archive_tts"] is True
    assert captured_execute["repair_control_mode"] == "ai_auto"
    assert runtime.settings.repair_control_mode == "ai_assisted"
    assert executed_request.project_id == "desktop_demo"
    assert executed_request.chapter_number == 4
    assert executed_request.force is True
    assert payload["project_id"] == "desktop_result_project"
    assert payload["chapter_number"] == 4
    assert payload["causal_score"] == pytest.approx(9.3)
    assert payload["causal_issue_count"] == 2
    assert payload["warnings"] == ["因果链校验发现 2 个问题，其中高优先级 1 个，建议人工复核。"]
    assert payload["tokens_used"] == 321
    assert payload["total_tokens"] == 321
    assert payload["cost_usd"] == pytest.approx(0.0123)
    assert payload["total_cost_usd"] == pytest.approx(0.0123)
    assert payload["run_log_dir"]


def test_desktop_job_manager_maps_app_service_events_to_legacy_state(tmp_path: Path) -> None:
    job_service = _StubJobService()
    manager = DesktopJobManager(
        storage_root=tmp_path,
        job_service=job_service,
        load_persisted_history=False,
    )
    manager._storage_root = None  # noqa: SLF001
    completed: list[str] = []
    manager.job_completed.connect(lambda job_id: completed.append(job_id))

    record = manager.submit_run_chapter(
        RunChapterRequest(project_id="desktop_demo", chapter_number=4),
        mock=True,
    )
    manager._handle_app_service_event(  # noqa: SLF001
        JobEvent(job_id=record.job_id, type=JobEventType.JOB_STARTED)
    )
    manager._handle_app_service_event(  # noqa: SLF001
        JobEvent(
            job_id=record.job_id,
            type=JobEventType.JOB_STEP,
            step="draft",
            payload={"tokens_so_far": 42},
        )
    )
    manager._handle_app_service_event(  # noqa: SLF001
        JobEvent(
            job_id=record.job_id,
            type=JobEventType.JOB_SUCCEEDED,
            payload={"project_id": "desktop_demo", "chapter_number": 4, "ok": True},
        )
    )

    final = next(job for job in manager.jobs() if job.job_id == record.job_id)
    assert final.status == DesktopJobState.SUCCEEDED
    assert final.current_step == "completed"
    assert final.cumulative_tokens == 42
    assert final.result["ok"] is True
    assert completed == [record.job_id]


def test_desktop_job_manager_cancels_app_service_job_and_ignores_late_success(
    tmp_path: Path,
) -> None:
    job_service = _StubJobService()
    manager = DesktopJobManager(
        storage_root=tmp_path,
        job_service=job_service,
        load_persisted_history=False,
    )
    manager._storage_root = None  # noqa: SLF001
    record = manager.submit_run_chapter(
        RunChapterRequest(project_id="desktop_demo", chapter_number=4),
        mock=True,
    )
    manager._handle_app_service_event(  # noqa: SLF001
        JobEvent(job_id=record.job_id, type=JobEventType.JOB_STARTED)
    )

    manager.cancel_job(record.job_id, reason="测试取消")
    manager._handle_app_service_event(  # noqa: SLF001
        JobEvent(
            job_id=record.job_id,
            type=JobEventType.JOB_SUCCEEDED,
            payload={"project_id": "desktop_demo", "chapter_number": 4, "ok": True},
        )
    )

    final = next(job for job in manager.jobs() if job.job_id == record.job_id)
    assert job_service.cancelled == [record.job_id]
    assert final.status == DesktopJobState.FAILED
    assert final.current_step == "cancelled"
    assert final.error == "测试取消"
    assert "ok" not in final.result


def test_desktop_job_manager_forwards_app_service_decisions(tmp_path: Path) -> None:
    job_service = _StubJobService()
    manager = DesktopJobManager(
        storage_root=tmp_path,
        job_service=job_service,
        load_persisted_history=False,
    )
    manager._storage_root = None  # noqa: SLF001
    record = manager.submit_run_chapter(
        RunChapterRequest(project_id="desktop_demo", chapter_number=4),
        mock=True,
    )

    provided = manager.provide_decision(
        record.job_id,
        "decision-1",
        "continue",
        custom_text="保留当前断点",
        approval_version="candidate-verified",
    )

    assert provided is True
    assert job_service.decisions == [
        (
            record.job_id,
            {
                "decision_id": "decision-1",
                "choice": "continue",
                "custom_text": "保留当前断点",
                "approval_version": "candidate-verified",
            },
        )
    ]


def test_desktop_export_book_uses_app_service_job() -> None:
    job_service = _StubJobService()
    manager = DesktopJobManager(job_service=job_service, load_persisted_history=False)

    record = manager.submit_export_book(
        ExportBookRequest(project_id="desktop_demo", format="markdown"),
        mock=True,
    )

    assert record.kind == "export_book"
    assert len(job_service.submitted) == 1
    command = job_service.submitted[0]
    assert command.kind == JobKind.EXPORT_BOOK
    assert command.project_id == "desktop_demo"
    assert command.mock is True
    assert command.metadata["command_name"] == "desktop-export-book"
    assert command.payload["format"] == "markdown"


def test_desktop_rebuild_memory_vectors_uses_app_service_job() -> None:
    job_service = _StubJobService()
    manager = DesktopJobManager(job_service=job_service, load_persisted_history=False)

    record = manager.submit_rebuild_memory_vectors(
        RebuildMemoryVectorsRequest(project_id="desktop_demo"),
        mock=True,
    )

    assert record.kind == "rebuild_memory_vectors"
    assert len(job_service.submitted) == 1
    command = job_service.submitted[0]
    assert command.kind == JobKind.REBUILD_MEMORY_VECTORS
    assert command.project_id == "desktop_demo"
    assert command.mock is True
    assert command.metadata["command_name"] == "desktop-rebuild-memory-vectors"
    assert command.payload["include_expression"] is True


@pytest.mark.parametrize(
    ("method_name", "job_request", "expected_kind", "command_name"),
    [
        (
            "submit_prepare_chapter",
            PrepareChapterRequest(project_id="desktop_demo", chapter_number=2),
            JobKind.PREPARE_CHAPTER,
            "desktop-prepare-chapter",
        ),
        (
            "submit_reevaluate_chapter",
            ReevaluateChapterRequest(project_id="desktop_demo", chapter_number=2),
            JobKind.REEVALUATE_CHAPTER,
            "desktop-reevaluate-chapter",
        ),
        (
            "submit_polish_chapter",
            PolishChapterRequest(project_id="desktop_demo", chapter_number=2),
            JobKind.POLISH_CHAPTER,
            "desktop-polish-chapter",
        ),
        (
            "submit_book_consistency",
            BookConsistencyRequest(project_id="desktop_demo"),
            JobKind.BOOK_CONSISTENCY,
            "desktop-book-consistency",
        ),
        (
            "submit_reextract_relationships",
            ReextractRelationshipsRequest(project_id="desktop_demo"),
            JobKind.REEXTRACT_RELATIONSHIPS,
            "desktop-reextract-relationships",
        ),
        (
            "submit_repair_motif_history",
            RepairMotifHistoryRequest(project_id="desktop_demo", chapter_number=2),
            JobKind.REPAIR_MOTIF_HISTORY,
            "desktop-repair-motif-history",
        ),
        (
            "submit_sync_chapter_contracts",
            SyncChapterContractsRequest(project_id="desktop_demo"),
            JobKind.SYNC_CHAPTER_CONTRACTS,
            "desktop-sync-chapter-contracts",
        ),
        (
            "submit_extend_outline",
            ExtendOutlineRequest(project_id="desktop_demo", additional_chapters=3),
            JobKind.EXTEND_OUTLINE,
            "desktop-extend-outline",
        ),
    ],
)
def test_desktop_remaining_submit_paths_use_app_service_jobs(
    method_name: str,
    job_request: object,
    expected_kind: JobKind,
    command_name: str,
) -> None:
    job_service = _StubJobService()
    manager = DesktopJobManager(job_service=job_service, load_persisted_history=False)
    manager._storage_root = None  # noqa: SLF001

    record = getattr(manager, method_name)(job_request, mock=True)

    assert record.kind == expected_kind.value
    assert len(job_service.submitted) == 1
    command = job_service.submitted[0]
    assert command.kind == expected_kind
    assert command.record_kind == ""
    assert command.project_id == "desktop_demo"
    assert command.mock is True
    assert command.metadata["command_name"] == command_name


@pytest.mark.parametrize(
    ("option_id", "expected_command_kind", "expected_record_kind"),
    [
        ("write_now", JobKind.RESOLVE_CHAPTER_CHECKPOINT, "resolve_chapter_checkpoint"),
        ("regenerate_plan", JobKind.RESOLVE_CHAPTER_CHECKPOINT, "prepare_chapter"),
        (
            "accept_and_finalize",
            JobKind.RESOLVE_CHAPTER_CHECKPOINT_FINALIZE,
            "resolve_chapter_checkpoint_finalize",
        ),
    ],
)
def test_desktop_resolve_checkpoint_preserves_visual_record_kind(
    option_id: str,
    expected_command_kind: JobKind,
    expected_record_kind: str,
) -> None:
    job_service = _StubJobService()
    manager = DesktopJobManager(job_service=job_service, load_persisted_history=False)

    record = manager.submit_resolve_chapter_checkpoint(
        ResolveChapterCheckpointRequest(
            project_id="desktop_demo",
            chapter_number=2,
            checkpoint_id="checkpoint-1",
            option_id=option_id,
        ),
        mock=True,
    )

    assert record.kind == expected_record_kind
    command = job_service.submitted[0]
    assert command.kind == expected_command_kind
    assert command.record_kind == expected_record_kind
    assert command.metadata["command_name"] == "desktop-resolve-chapter-checkpoint"


def test_desktop_waiting_chapter_job_starts_after_dependencies_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager = DesktopJobManager()
    manager._storage_root = tmp_path  # noqa: SLF001
    with manager._lock:  # noqa: SLF001
        manager._jobs.clear()  # noqa: SLF001
        manager._waiting_jobs.clear()  # noqa: SLF001
        init_record = DesktopJobRecord(
            job_id="init-active",
            kind="init_long",
            label="长篇立项 · waiting_demo",
            project_id="waiting_demo",
            status=DesktopJobState.RUNNING,
        )
        manager._jobs[init_record.job_id] = init_record  # noqa: SLF001
    dependency_ready = False
    submitted: list[JobCommand] = []

    monkeypatch.setattr(
        manager._dependency_resolver,  # noqa: SLF001
        "is_ready",
        lambda *args, **kwargs: dependency_ready,
    )

    def _fake_submit_app_service_job(
        command: JobCommand,
        *,
        emit_submitted: bool,
    ) -> DesktopJobRecord:
        submitted.append(command)
        queued = manager._jobs[command.job_id]  # noqa: SLF001
        queued.status = DesktopJobState.RUNNING
        return queued

    monkeypatch.setattr(manager, "_submit_app_service_job", _fake_submit_app_service_job)

    record = manager.submit_run_chapter(
        RunChapterRequest(project_id="waiting_demo", chapter_number=3),
        mock=True,
    )

    assert record.status == DesktopJobState.QUEUED
    assert record.job_id in manager._waiting_jobs  # noqa: SLF001

    manager._check_waiting_jobs("waiting_demo")  # noqa: SLF001
    assert submitted == []

    dependency_ready = True
    manager._check_waiting_jobs("waiting_demo")  # noqa: SLF001
    assert submitted == []

    init_record.status = DesktopJobState.SUCCEEDED
    manager._check_waiting_jobs("waiting_demo")  # noqa: SLF001

    assert len(submitted) == 1
    assert submitted[0].job_id == record.job_id
    assert submitted[0].kind == JobKind.RUN_CHAPTER
    assert submitted[0].mock is True
    assert record.job_id not in manager._waiting_jobs  # noqa: SLF001


def test_desktop_prepare_chapter_can_start_during_active_init_after_outline_batch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager = DesktopJobManager()
    manager._storage_root = tmp_path  # noqa: SLF001
    project_id = "waiting_demo"
    with manager._lock:  # noqa: SLF001
        manager._jobs.clear()  # noqa: SLF001
        manager._waiting_jobs.clear()  # noqa: SLF001
        init_record = DesktopJobRecord(
            job_id="init-active",
            kind="init_long",
            label=f"长篇立项 · {project_id}",
            project_id=project_id,
            status=DesktopJobState.RUNNING,
        )
        manager._jobs[init_record.job_id] = init_record  # noqa: SLF001

    monkeypatch.setattr(
        manager._dependency_resolver,  # noqa: SLF001
        "is_ready",
        lambda *args, **kwargs: True,
    )
    submitted: list[JobCommand] = []

    def _fake_submit_app_service_job(
        command: JobCommand,
        *,
        emit_submitted: bool,
    ) -> DesktopJobRecord:
        submitted.append(command)
        queued = manager._jobs[command.job_id]  # noqa: SLF001
        queued.status = DesktopJobState.RUNNING
        return queued

    monkeypatch.setattr(manager, "_submit_app_service_job", _fake_submit_app_service_job)

    record = manager.submit_prepare_chapter(
        PrepareChapterRequest(project_id=project_id, chapter_number=2),
        mock=True,
    )
    second_record = manager.submit_prepare_chapter(
        PrepareChapterRequest(project_id=project_id, chapter_number=3),
        mock=True,
    )

    assert record.status == DesktopJobState.QUEUED
    assert second_record.status == DesktopJobState.QUEUED
    assert record.job_id in manager._waiting_jobs  # noqa: SLF001
    assert second_record.job_id in manager._waiting_jobs  # noqa: SLF001
    assert submitted == []

    project_dir = tmp_path / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "outline.json").write_text(
        '{"chapters": [{"chapter_number": 1}, {"chapter_number": 2}, {"chapter_number": 3}]}',
        encoding="utf-8",
    )
    init_record.current_step = "plan_outline_batch_1_3"

    manager._check_waiting_jobs(project_id)  # noqa: SLF001

    assert init_record.status == DesktopJobState.RUNNING
    assert len(submitted) == 1
    assert submitted[0].job_id == record.job_id
    assert submitted[0].kind == JobKind.PREPARE_CHAPTER
    assert submitted[0].mock is True
    assert record.job_id not in manager._waiting_jobs  # noqa: SLF001
    assert second_record.job_id in manager._waiting_jobs  # noqa: SLF001

    manager._check_waiting_jobs(project_id)  # noqa: SLF001
    assert len(submitted) == 1

    record.status = DesktopJobState.SUCCEEDED
    manager._check_waiting_jobs(project_id)  # noqa: SLF001

    assert len(submitted) == 2
    assert submitted[1].job_id == second_record.job_id
    assert submitted[1].kind == JobKind.PREPARE_CHAPTER
    assert submitted[1].mock is True
    assert second_record.job_id not in manager._waiting_jobs  # noqa: SLF001
