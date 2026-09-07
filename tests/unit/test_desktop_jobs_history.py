"""Tests for project-bound desktop task-flow history persistence."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from novel_forge.app_service.contracts import JobCommand, JobKind
from novel_forge.app_service.workspace_commands import (
    _init_long_request_from_meta as _app_init_long_request_from_meta,
)
from novel_forge.desktop import jobs as desktop_jobs
from novel_forge.desktop import task_flow_errors
from novel_forge.desktop.jobs import (
    DesktopJobEvent,
    DesktopJobManager,
    DesktopJobRecord,
    DesktopJobState,
)
from novel_forge.desktop.jobs import manager as desktop_jobs_manager
from novel_forge.desktop.pages import chapter_studio_jobs
from novel_forge.persistence.models import ProjectLayout


def _history_path(root: Path, project_id: str) -> Path:
    return root / project_id / "states" / "task_flow_history.json"


def _error_archive_path(root: Path, project_id: str) -> Path:
    return root / project_id / "states" / "task_flow_errors"


def _write_init_request_meta(layout: ProjectLayout) -> None:
    layout.states_dir.mkdir(parents=True, exist_ok=True)
    layout.init_request_meta_path.write_text(
        json.dumps(
            {
                "request": {
                    "init_input": {
                        "premise": "魂玉归位。",
                        "genre": "玄幻",
                        "tone": "冷峻",
                        "title": "魂玉",
                        "language": "zh",
                        "characters_hint": "沈珩",
                        "world_hint": "玉魂宗",
                        "conflict_hint": "传承与背叛",
                        "pov_hint": "第三人称限知",
                        "opening_style": "悬念开场",
                        "ending_style": "余韵式收束",
                        "extra_instructions": "保持克制。",
                    },
                    "generation_options": {
                        "total_chapters": 78,
                        "words_per_chapter": 3200,
                        "volume_mode_setting": "auto",
                        "chapters_per_volume_setting": 12,
                        "blueprint_element_preferences": {"locked": ["mirror"]},
                        "polish_hint": "强化传承线。",
                        "research_enabled": True,
                        "research_provider": "searxng",
                        "research_query_hint": "玉石信仰 宗族传承",
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_task_flow_history_persists_and_reloads_by_project(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        desktop_jobs_manager,
        "get_settings",
        lambda: SimpleNamespace(storage_root=tmp_path, long_waiting_job_poll_interval_ms=3000),
    )

    manager = DesktopJobManager()
    record = DesktopJobRecord(
        job_id="job-1",
        kind="resolve_chapter_checkpoint",
        label="归档决策 · demo / 第 7 章",
        project_id="demo",
        status=DesktopJobState.SUCCEEDED,
        current_step="completed",
        current_step_payload={"stage": "finalize"},
        result={"chapter_number": 7},
        resolved_error_entry_ids={"error-entry-1"},
    )
    record.events = [
        DesktopJobEvent(
            at="2026-04-08T09:12:00+00:00",
            step="memory_updated",
            payload={"chapter": 7, "last_indexed_chapter": 7},
        ),
    ]

    with manager._lock:
        manager._jobs[record.job_id] = record
    manager._persist_terminal_job(record.job_id)

    path = _history_path(tmp_path, "demo")
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload and payload[0]["job_id"] == "job-1"
    assert payload[0]["project_id"] == "demo"
    assert payload[0]["resolved_error_entry_ids"] == ["error-entry-1"]

    reloaded = DesktopJobManager()
    reloaded.wait_for_history()
    loaded_jobs = reloaded.jobs()
    restored = next((job for job in loaded_jobs if job.job_id == "job-1"), None)
    assert restored is not None
    assert restored.project_id == "demo"
    assert restored.status == DesktopJobState.SUCCEEDED
    assert restored.current_step_payload == {"stage": "finalize"}
    assert restored.resolved_error_entry_ids == {"error-entry-1"}


def test_init_repair_retry_resets_failed_readiness_state(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        desktop_jobs_manager,
        "get_settings",
        lambda: SimpleNamespace(storage_root=tmp_path, long_waiting_job_poll_interval_ms=3000),
    )
    layout = ProjectLayout(tmp_path / "魂玉")
    layout.reports_dir.mkdir(parents=True, exist_ok=True)
    _write_init_request_meta(layout)
    (layout.reports_dir / "init_readiness.json").write_text("{}", encoding="utf-8")
    (layout.reports_dir / "outline_inheritance.json").write_text("{}", encoding="utf-8")
    (layout.reports_dir / "init_artifact_repair.json").write_text(
        json.dumps(
            {
                "repairs": [
                    {"artifact": "outline", "round": 2},
                    {"artifact": "blueprint", "round": 1},
                ]
            }
        ),
        encoding="utf-8",
    )

    manager = DesktopJobManager()
    request = manager._init_long_request_from_meta("魂玉")
    manager._reset_init_readiness_retry_state("魂玉", reset_repair_history=True)

    assert request.project_id == "魂玉"
    assert request.premise == "魂玉归位。"
    assert request.total_chapters == 78
    assert request.words_per_chapter == 3200
    assert request.research_enabled is True
    assert request.research_provider == "searxng"
    assert request.research_query_hint == "玉石信仰 宗族传承"
    assert request.blueprint_element_preferences == {"locked": ["mirror"]}
    assert not (layout.reports_dir / "init_readiness.json").exists()
    assert not (layout.reports_dir / "outline_inheritance.json").exists()
    repair_payload = json.loads(
        (layout.reports_dir / "init_artifact_repair.json").read_text(encoding="utf-8")
    )
    assert repair_payload["repairs"] == [{"artifact": "blueprint", "round": 1}]


def test_app_service_init_retry_request_preserves_research_options(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "魂玉")
    _write_init_request_meta(layout)

    request = _app_init_long_request_from_meta(layout, "魂玉")

    assert request.research_enabled is True
    assert request.research_provider == "searxng"
    assert request.research_query_hint == "玉石信仰 宗族传承"


def test_init_repair_retry_resets_blueprint_history_for_blueprint_block(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        desktop_jobs_manager,
        "get_settings",
        lambda: SimpleNamespace(storage_root=tmp_path, long_waiting_job_poll_interval_ms=3000),
    )
    layout = ProjectLayout(tmp_path / "魂玉")
    layout.reports_dir.mkdir(parents=True, exist_ok=True)
    _write_init_request_meta(layout)
    (layout.reports_dir / "init_readiness.json").write_text(
        json.dumps(
            {
                "allowed": False,
                "remaining_issues": [
                    {
                        "stage": "blueprint_coherence",
                        "repair_scope": [
                            {
                                "artifact": "blueprint",
                                "chapters": [6, 9, 14],
                                "fields": ["character_arcs"],
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (layout.reports_dir / "outline_inheritance.json").write_text("{}", encoding="utf-8")
    (layout.reports_dir / "init_artifact_repair.json").write_text(
        json.dumps(
            {
                "repairs": [
                    {"artifact": "outline", "round": 2},
                    {"artifact": "blueprint", "round": 1},
                ]
            }
        ),
        encoding="utf-8",
    )

    manager = DesktopJobManager()
    manager._reset_init_readiness_retry_state("魂玉", reset_repair_history=True)

    assert not (layout.reports_dir / "init_readiness.json").exists()
    repair_payload = json.loads(
        (layout.reports_dir / "init_artifact_repair.json").read_text(encoding="utf-8")
    )
    assert repair_payload["repairs"] == [{"artifact": "outline", "round": 2}]


def test_init_repair_retry_preserves_source_artifacts_resume_evidence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        desktop_jobs_manager,
        "get_settings",
        lambda: SimpleNamespace(storage_root=tmp_path, long_waiting_job_poll_interval_ms=3000),
    )
    layout = ProjectLayout(tmp_path / "魂玉")
    layout.reports_dir.mkdir(parents=True, exist_ok=True)
    _write_init_request_meta(layout)
    readiness = {
        "allowed": False,
        "summary": "source_artifacts 阻断。",
        "stages": {
            "contract_coherence": {"blocked": False},
            "claim_contract_coverage": {"blocked": False},
            "source_artifacts": {"blocked": True},
        },
        "remaining_issues": [{"stage": "source_artifacts"}],
    }
    (layout.reports_dir / "init_readiness.json").write_text(
        json.dumps(readiness, ensure_ascii=False),
        encoding="utf-8",
    )
    (layout.reports_dir / "outline_inheritance.json").write_text("{}", encoding="utf-8")
    (layout.reports_dir / "init_artifact_repair.json").write_text(
        json.dumps(
            {
                "repairs": [
                    {"artifact": "source_artifacts", "round": 1},
                    {"artifact": "chapter_contracts", "round": 1},
                    {"artifact": "blueprint", "round": 1},
                ]
            }
        ),
        encoding="utf-8",
    )

    manager = DesktopJobManager()
    manager._reset_init_readiness_retry_state("魂玉", reset_repair_history=True)

    assert (layout.reports_dir / "init_readiness.json").exists()
    assert (layout.reports_dir / "outline_inheritance.json").exists()
    repair_payload = json.loads(
        (layout.reports_dir / "init_artifact_repair.json").read_text(encoding="utf-8")
    )
    assert repair_payload["repairs"] == [{"artifact": "blueprint", "round": 1}]


def test_task_flow_history_skips_generated_test_projects(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        desktop_jobs_manager,
        "get_settings",
        lambda: SimpleNamespace(storage_root=tmp_path, long_waiting_job_poll_interval_ms=3000),
    )

    DesktopJobManager._write_history_records(
        _history_path(tmp_path, "test-project"),
        [
            DesktopJobRecord(
                job_id="job-test",
                kind="init_long",
                label="长篇立项 · test-project",
                project_id="test-project",
                status=DesktopJobState.SUCCEEDED,
            )
        ],
    )
    DesktopJobManager._write_history_records(
        _history_path(tmp_path, "reader_project"),
        [
            DesktopJobRecord(
                job_id="job-reader",
                kind="init_long",
                label="长篇立项 · reader_project",
                project_id="reader_project",
                status=DesktopJobState.SUCCEEDED,
            )
        ],
    )

    reloaded = DesktopJobManager()
    reloaded.wait_for_history()

    assert {job.job_id for job in reloaded.jobs()} == {"job-reader"}


def test_task_flow_history_skips_orphaned_checkpoint_finalize_failures(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        desktop_jobs_manager,
        "get_settings",
        lambda: SimpleNamespace(storage_root=tmp_path, long_waiting_job_poll_interval_ms=3000),
    )

    DesktopJobManager._write_history_records(
        _history_path(tmp_path, "demo"),
        [
            DesktopJobRecord(
                job_id="job-orphaned-finalize",
                kind="resolve_chapter_checkpoint_finalize",
                label="归档前修复（阶段 2/2） · demo / 第 5 章",
                project_id="demo",
                status=DesktopJobState.FAILED,
                current_step="contract_execution_audit",
                error="章节契约执行审计要求阻断归档",
            )
        ],
    )

    reloaded = DesktopJobManager()
    reloaded.wait_for_history()

    assert "job-orphaned-finalize" not in {job.job_id for job in reloaded.jobs()}


def test_task_flow_history_keeps_failed_checkpoint_job_with_live_session(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        desktop_jobs_manager,
        "get_settings",
        lambda: SimpleNamespace(storage_root=tmp_path, long_waiting_job_poll_interval_ms=3000),
    )
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    layout.chapter_checkpoint_path(5).write_text("{}", encoding="utf-8")
    layout.chapter_session_path(5).write_text("{}", encoding="utf-8")

    DesktopJobManager._write_history_records(
        _history_path(tmp_path, "demo"),
        [
            DesktopJobRecord(
                job_id="job-live-finalize",
                kind="resolve_chapter_checkpoint_finalize",
                label="归档前修复（阶段 2/2） · demo / 第 5 章",
                project_id="demo",
                status=DesktopJobState.FAILED,
                current_step="contract_execution_audit",
                error="章节契约执行审计要求阻断归档",
            )
        ],
    )

    reloaded = DesktopJobManager()
    reloaded.wait_for_history()

    assert "job-live-finalize" in {job.job_id for job in reloaded.jobs()}


def test_chapter_studio_error_entries_auto_resolve_orphaned_checkpoint_failure(
    tmp_path: Path,
) -> None:
    failed = DesktopJobRecord(
        job_id="job-orphaned-finalize",
        kind="resolve_chapter_checkpoint_finalize",
        label="归档前修复（阶段 2/2） · demo / 第 5 章",
        project_id="demo",
        status=DesktopJobState.FAILED,
        current_step="contract_execution_audit",
        error="章节契约执行审计要求阻断归档",
    )

    entries = chapter_studio_jobs._task_flow_error_entries([failed], storage_root=tmp_path)

    assert entries
    assert entries[0]["chapter_number"] == 5
    assert entries[0]["actionability"] == "historical"
    assert entries[0]["auto_resolved"] == "true"
    assert "不再影响当前任务流" in entries[0]["inactive_reason"]


def test_structured_recovery_actions_reach_chapter_studio_error_entry() -> None:
    failed = DesktopJobRecord(
        job_id="job-input-integrity",
        kind="run_chapter",
        label="写作章节 · demo / 第 3 章",
        project_id="demo",
        status=DesktopJobState.FAILED,
        current_step="planning_input",
        error="章节输入不完整",
        error_summary={
            "summary": "planning_input 缺少 chapter_outline.pov_character",
            "detail": "缺少必填字段。",
            "recovery_actions": [
                "检查并修复章节大纲的 POV。",
                "重新生成当前章节大纲。",
            ],
        },
    )

    entries = chapter_studio_jobs._task_flow_error_entries([failed])

    assert entries
    assert "建议操作" in entries[0]["excerpt"]
    assert "重新生成当前章节大纲" in entries[0]["excerpt"]


def test_mark_task_flow_errors_resolved_persists_review_state(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        desktop_jobs_manager,
        "get_settings",
        lambda: SimpleNamespace(storage_root=tmp_path, long_waiting_job_poll_interval_ms=3000),
    )

    manager = DesktopJobManager()
    failed = DesktopJobRecord(
        job_id="job-failed",
        kind="init_long",
        label="长篇立项 · demo",
        project_id="demo",
        status=DesktopJobState.FAILED,
        current_step="init_readiness",
        error="blueprint_coherence needs_repair",
    )
    with manager._lock:
        manager._jobs[failed.job_id] = failed
    manager._persist_terminal_job(failed.job_id)

    changed = manager.mark_task_flow_errors_resolved({"job-failed": ["entry-a", "entry-b"]})

    assert changed == 2
    payload = json.loads(_history_path(tmp_path, "demo").read_text(encoding="utf-8"))
    assert payload[0]["resolved_error_entry_ids"] == ["entry-a", "entry-b"]


def test_clear_jobs_removes_terminal_entries_and_syncs_history(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        desktop_jobs_manager,
        "get_settings",
        lambda: SimpleNamespace(storage_root=tmp_path, long_waiting_job_poll_interval_ms=3000),
    )

    manager = DesktopJobManager()
    done = DesktopJobRecord(
        job_id="job-done",
        kind="resolve_chapter_checkpoint",
        label="归档决策 · demo / 第 3 章",
        project_id="demo",
        status=DesktopJobState.SUCCEEDED,
        result={"chapter_number": 3},
    )
    failed = DesktopJobRecord(
        job_id="job-failed",
        kind="prepare_chapter",
        label="章节方案 · demo / 第 3 章",
        project_id="demo",
        status=DesktopJobState.FAILED,
        result={"chapter_number": 3},
    )
    running = DesktopJobRecord(
        job_id="job-running",
        kind="run_chapter",
        label="章节续写 · demo / 第 3 章",
        project_id="demo",
        status=DesktopJobState.RUNNING,
        result={"chapter_number": 3},
    )

    with manager._lock:
        manager._jobs[done.job_id] = done
        manager._jobs[failed.job_id] = failed
        manager._jobs[running.job_id] = running

    manager._persist_terminal_job(done.job_id)
    manager._persist_terminal_job(failed.job_id)

    removed = manager.clear_jobs(
        ["job-done", "job-failed", "job-running", "missing"],
        project_id="demo",
    )
    assert removed == 2

    remaining_ids = {job.job_id for job in manager.jobs()}
    assert "job-done" not in remaining_ids
    assert "job-failed" not in remaining_ids
    assert "job-running" in remaining_ids

    path = _history_path(tmp_path, "demo")
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert all(item.get("job_id") not in {"job-done", "job-failed"} for item in payload)


def test_restarted_init_cleanup_discards_active_and_terminal_init_jobs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        desktop_jobs_manager,
        "get_settings",
        lambda: SimpleNamespace(storage_root=tmp_path, long_waiting_job_poll_interval_ms=3000),
    )

    class FakeWorker:
        def __init__(self) -> None:
            self.cancel_requested = False
            self._cleanup_notify = None

        def request_cancel(self) -> None:
            self.cancel_requested = True

    manager = DesktopJobManager(load_persisted_history=False)
    running_init = DesktopJobRecord(
        job_id="init-running",
        kind="init_long",
        label="长篇立项 · demo",
        project_id="demo",
        status=DesktopJobState.RUNNING,
        current_step="plan_blueprint",
    )
    queued_init = DesktopJobRecord(
        job_id="init-queued",
        kind="init_long",
        label="长篇立项 · demo",
        project_id="demo",
        status=DesktopJobState.QUEUED,
    )
    failed_init = DesktopJobRecord(
        job_id="init-failed",
        kind="init_long",
        label="长篇立项 · demo",
        project_id="demo",
        status=DesktopJobState.FAILED,
        current_step="plan_outline",
        error="旧错误",
    )
    short_done = DesktopJobRecord(
        job_id="short-done",
        kind="run_short",
        label="短篇创作 · demo",
        project_id="demo",
        status=DesktopJobState.SUCCEEDED,
    )
    chapter_running = DesktopJobRecord(
        job_id="chapter-running",
        kind="run_chapter",
        label="章节续写 · demo / 第 1 章",
        project_id="demo",
        status=DesktopJobState.RUNNING,
        result={"chapter_number": 1},
    )
    other_init = DesktopJobRecord(
        job_id="other-init",
        kind="init_long",
        label="长篇立项 · other",
        project_id="other",
        status=DesktopJobState.RUNNING,
    )
    running_worker = FakeWorker()

    with manager._lock:
        for record in (
            running_init,
            queued_init,
            failed_init,
            short_done,
            chapter_running,
            other_init,
        ):
            manager._jobs[record.job_id] = record
        manager._workers[running_init.job_id] = running_worker  # type: ignore[assignment]
        manager._waiting_jobs[queued_init.job_id] = desktop_jobs.WaitingJob(
            record=queued_init,
            app_command=JobCommand(
                job_id=queued_init.job_id,
                kind=JobKind.INIT_LONG,
                payload={"project_id": "demo"},
                label=queued_init.label,
                project_id="demo",
                metadata={"command_name": "test-queued-init"},
            ),
        )
    DesktopJobManager._write_history_records(
        _history_path(tmp_path, "demo"), [failed_init, short_done]
    )

    purged = manager.clear_restarted_init_jobs("demo")

    assert set(purged) == {"init-running", "init-queued", "init-failed"}
    assert running_worker.cancel_requested is True
    assert callable(running_worker._cleanup_notify)
    running_worker._cleanup_notify()
    assert manager.cancelling_job_labels() == []
    assert queued_init.job_id not in manager._waiting_jobs
    remaining_ids = {job.job_id for job in manager.jobs()}
    assert "short-done" in remaining_ids
    assert "chapter-running" in remaining_ids
    assert "other-init" in remaining_ids
    assert remaining_ids.isdisjoint({"init-running", "init-queued", "init-failed"})
    payload = json.loads(_history_path(tmp_path, "demo").read_text(encoding="utf-8"))
    assert [item["job_id"] for item in payload] == ["short-done"]
    assert not _error_archive_path(tmp_path, "demo").exists()


def test_clear_jobs_preserves_error_archive_after_removing_ui_history(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        desktop_jobs_manager,
        "get_settings",
        lambda: SimpleNamespace(storage_root=tmp_path, long_waiting_job_poll_interval_ms=3000),
    )

    manager = DesktopJobManager()
    done = DesktopJobRecord(
        job_id="job-done",
        kind="init_long",
        label="长篇立项 · demo",
        project_id="demo",
        status=DesktopJobState.SUCCEEDED,
        current_step="plan_chapter_contracts",
    )
    done.events = [
        DesktopJobEvent(
            at="2026-05-25T15:48:37+00:00",
            step="format_retry",
            payload={
                "task": "plan_chapter_contracts",
                "attempt": 1,
                "max_attempts": 3,
                "error": "Local JSON repair lost structural content",
                "raw_excerpt": '{"chapter_contracts": [{"chapter_number": 34}',
                "log_file": "data/demo/logs/run/format_errors/010.json",
            },
        )
    ]

    with manager._lock:
        manager._jobs[done.job_id] = done

    manager._persist_terminal_job(done.job_id)
    archive_path = _error_archive_path(tmp_path, "demo")
    assert archive_path.exists()

    removed = manager.clear_jobs(["job-done"], project_id="demo")

    assert removed == 1
    assert not _history_path(tmp_path, "demo").exists()
    assert archive_path.exists()
    index = json.loads((archive_path / "index.json").read_text(encoding="utf-8"))
    assert index["entries"][0]["task"] == "plan_chapter_contracts"
    assert index["entries"][0]["kind"] == "格式错误"
    records = list((archive_path / "records").glob("*.json"))
    assert len(records) == 1
    stream = (archive_path / "errors.jsonl").read_text(encoding="utf-8")
    assert "plan_chapter_contracts" in stream


def test_task_flow_error_archive_summary_and_clear(
    tmp_path: Path,
) -> None:
    archive_path = _error_archive_path(tmp_path, "demo")
    archive = task_flow_errors.TaskFlowErrorArchive(tmp_path)
    archive.append_entries(
        "demo",
        [
            {
                "id": "entry-a",
                "time": "2026-05-25T15:48:37+00:00",
                "project_id": "demo",
                "kind": "格式错误",
                "task": "plan_chapter_contracts",
                "error": "Local JSON repair lost structural content",
            }
        ],
    )

    summary = task_flow_errors.task_flow_error_archive_summary(tmp_path)

    assert summary["entry_count"] == 1
    assert summary["project_count"] == 1
    assert summary["latest_time"] == "2026-05-25T15:48:37+00:00"

    removed = task_flow_errors.clear_task_flow_error_archive(tmp_path)

    assert removed == 1
    assert not archive_path.exists()


def test_task_flow_error_archive_marks_orphaned_checkpoint_failure_historical(
    tmp_path: Path,
) -> None:
    archive_path = _error_archive_path(tmp_path, "demo")
    archive = task_flow_errors.TaskFlowErrorArchive(tmp_path)
    archive.append_entries(
        "demo",
        [
            {
                "id": "entry-orphaned-finalize",
                "time": "2026-06-01T17:52:10+00:00",
                "project_id": "demo",
                "job_kind": "resolve_chapter_checkpoint_finalize",
                "job_label": "归档前修复（阶段 2/2） · demo / 第 5 章",
                "status": "failed",
                "kind": "任务失败",
                "task": "contract_execution_audit",
                "error": "章节契约执行审计要求阻断归档",
            }
        ],
    )

    index = json.loads((archive_path / "index.json").read_text(encoding="utf-8"))

    assert index["entries"][0]["chapter_number"] == 5
    assert index["entries"][0]["actionability"] == "historical"
    assert "不再影响当前任务流" in index["entries"][0]["inactive_reason"]


def test_startup_does_not_block_init(monkeypatch, tmp_path: Path) -> None:
    """DesktopJobManager.__init__ returns immediately without reading history."""
    import time

    monkeypatch.setattr(
        desktop_jobs_manager,
        "get_settings",
        lambda: SimpleNamespace(storage_root=tmp_path, long_waiting_job_poll_interval_ms=3000),
    )

    for i in range(50):
        DesktopJobManager._write_history_records(
            _history_path(tmp_path, f"project-{i:03d}"),
            [
                DesktopJobRecord(
                    job_id=f"job-{i}",
                    kind="init_long",
                    label=f"长篇立项 · project-{i:03d}",
                    project_id=f"project-{i:03d}",
                    status=DesktopJobState.SUCCEEDED,
                )
            ],
        )

    start = time.monotonic()
    manager = DesktopJobManager()
    elapsed = time.monotonic() - start

    assert elapsed < 0.5, f"__init__ took {elapsed:.3f}s — history not async?"
    manager.wait_for_history(5000)


def test_corrupted_history_does_not_crash_main_thread(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """A corrupted history file is silently skipped — no exception on main thread."""
    monkeypatch.setattr(
        desktop_jobs_manager,
        "get_settings",
        lambda: SimpleNamespace(storage_root=tmp_path, long_waiting_job_poll_interval_ms=3000),
    )

    history_dir = tmp_path / "demo" / "states"
    history_dir.mkdir(parents=True, exist_ok=True)
    (history_dir / "task_flow_history.json").write_text(
        "{{{{not valid json",
        encoding="utf-8",
    )

    manager = DesktopJobManager()
    manager.wait_for_history(5000)

    assert manager.jobs() == []
