"""Desktop job parallelism rules across projects."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from PySide6.QtTest import QSignalSpy

from novel_forge.app_service.contracts import JobCommand, JobKind
from novel_forge.desktop.jobs import (
    DesktopJobManager,
    DesktopJobRecord,
    DesktopJobState,
    WaitingJob,
)


def _waiting_command(record: DesktopJobRecord) -> JobCommand:
    return JobCommand(
        job_id=record.job_id,
        kind=JobKind.RUN_CHAPTER,
        payload={"project_id": record.project_id, "chapter_number": record.result["chapter_number"]},
        label=record.label,
        project_id=record.project_id,
        mock=True,
        metadata={"command_name": "test-waiting-run-chapter"},
    )


def test_book_consistency_is_same_project_write_conflict_only() -> None:
    manager = DesktopJobManager(load_persisted_history=False)
    audit = DesktopJobRecord(
        job_id="audit-a",
        kind="book_consistency",
        label="全书一致性审计 · 项目A",
        project_id="项目A",
        status=DesktopJobState.RUNNING,
    )
    with manager._lock:  # noqa: SLF001 - focused regression on manager internals
        manager._jobs[audit.job_id] = audit  # noqa: SLF001

    same_project_chapter = DesktopJobRecord(
        job_id="chapter-a",
        kind="run_chapter",
        label="章节续写 · 项目A / 第 1 章",
        project_id="项目A",
        result={"chapter_number": 1},
    )
    other_project_chapter = DesktopJobRecord(
        job_id="chapter-b",
        kind="run_chapter",
        label="章节续写 · 项目B / 第 1 章",
        project_id="项目B",
        result={"chapter_number": 1},
    )

    assert manager._find_active_write_conflict(same_project_chapter) is audit  # noqa: SLF001
    assert manager._find_active_write_conflict(other_project_chapter) is None  # noqa: SLF001


def test_chapter_write_does_not_block_other_project_book_consistency() -> None:
    manager = DesktopJobManager(load_persisted_history=False)
    chapter = DesktopJobRecord(
        job_id="chapter-a",
        kind="run_chapter",
        label="章节续写 · 项目A / 第 1 章",
        project_id="项目A",
        status=DesktopJobState.RUNNING,
        result={"chapter_number": 1},
    )
    with manager._lock:  # noqa: SLF001 - focused regression on manager internals
        manager._jobs[chapter.job_id] = chapter  # noqa: SLF001

    same_project_audit = DesktopJobRecord(
        job_id="audit-a",
        kind="book_consistency",
        label="全书一致性审计 · 项目A",
        project_id="项目A",
    )
    other_project_audit = DesktopJobRecord(
        job_id="audit-b",
        kind="book_consistency",
        label="全书一致性审计 · 项目B",
        project_id="项目B",
    )

    assert manager._find_active_write_conflict(same_project_audit) is chapter  # noqa: SLF001
    assert manager._find_active_write_conflict(other_project_audit) is None  # noqa: SLF001


# -- dirty-flag coalescing tests (Task 6) --


def _make_manager_with_running_job(
    qapp: object,  # noqa: ARG001 - fixture ensures QApplication exists
    job_id: str = "j1",
) -> DesktopJobManager:
    manager = DesktopJobManager(load_persisted_history=False)
    record = DesktopJobRecord(
        job_id=job_id,
        kind="run_chapter",
        label="test",
        project_id="proj",
        status=DesktopJobState.RUNNING,
    )
    with manager._lock:  # noqa: SLF001
        manager._jobs[job_id] = record  # noqa: SLF001
    return manager


def test_high_frequency_steps_coalesced(qapp: object) -> None:
    """Five step events in <10ms should be coalesced, not emitted one-for-one."""
    manager = _make_manager_with_running_job(qapp)
    spy = QSignalSpy(manager.jobs_changed)

    for i in range(5):
        manager._handle_step("j1", f"step_{i}", {})  # noqa: SLF001

    # Wait 200ms: the 120ms single-shot QTimer fires once, flushing all 5.
    from PySide6.QtTest import QTest

    QTest.qWait(200)

    assert 1 <= spy.count() < 5, f"Expected coalesced emits, got {spy.count()}"


def test_normal_interval_steps_not_over_coalesced(qapp: object) -> None:
    """Three step events spaced 200ms apart should each produce an emit."""
    manager = _make_manager_with_running_job(qapp)
    spy = QSignalSpy(manager.jobs_changed)

    from PySide6.QtTest import QTest

    for i in range(3):
        manager._handle_step("j1", f"step_{i}", {})  # noqa: SLF001
        # 200ms > 120ms coalesce window → timer fires between steps.
        QTest.qWait(200)

    assert spy.count() >= 3, f"Expected >=3 emits, got {spy.count()}"


def test_job_completed_still_emits_after_coalescing(qapp: object) -> None:
    """job_completed is unaffected by the jobs_changed coalescing."""
    manager = _make_manager_with_running_job(qapp)
    spy = QSignalSpy(manager.job_completed)

    manager._handle_finished("j1", {"project_id": "proj", "status": "done"})  # noqa: SLF001

    from PySide6.QtTest import QTest

    QTest.qWait(10)

    assert spy.count() >= 1, f"Expected >=1 job_completed emit, got {spy.count()}"


# -- waiting job poller tests (Task 7) --


def test_poller_promotes_waiting_job_when_ready(qapp: object) -> None:
    """Poll timer should promote a waiting job whose dependencies are satisfied."""
    manager = DesktopJobManager(load_persisted_history=False)
    record = DesktopJobRecord(
        job_id="wait-1",
        kind="run_chapter",
        label="章节续写 · 项目A / 第 1 章",
        project_id="项目A",
        status=DesktopJobState.QUEUED,
        result={"chapter_number": 1},
    )
    with manager._lock:  # noqa: SLF001
        manager._jobs[record.job_id] = record  # noqa: SLF001
        manager._waiting_jobs[record.job_id] = WaitingJob(  # noqa: SLF001
            record=record,
            app_command=_waiting_command(record),
        )

    with (
        patch.object(manager._dependency_resolver, "is_ready", return_value=True),
        patch.object(manager, "_submit_app_service_job", return_value=record) as mock_submit,
    ):
        manager._poll_waiting_jobs()  # noqa: SLF001

    mock_submit.assert_called_once()
    submitted_command = mock_submit.call_args.args[0]
    assert submitted_command.job_id == record.job_id
    assert mock_submit.call_args.kwargs == {"emit_submitted": False}
    with manager._lock:  # noqa: SLF001
        assert record.job_id not in manager._waiting_jobs  # noqa: SLF001


def test_poller_skips_waiting_job_when_not_ready(qapp: object) -> None:
    """Poll timer should NOT promote a waiting job whose dependencies are not satisfied."""
    manager = DesktopJobManager(load_persisted_history=False)
    record = DesktopJobRecord(
        job_id="wait-2",
        kind="run_chapter",
        label="章节续写 · 项目B / 第 2 章",
        project_id="项目B",
        status=DesktopJobState.QUEUED,
        result={"chapter_number": 2},
    )
    with manager._lock:  # noqa: SLF001
        manager._jobs[record.job_id] = record  # noqa: SLF001
        manager._waiting_jobs[record.job_id] = WaitingJob(  # noqa: SLF001
            record=record,
            app_command=_waiting_command(record),
        )

    with (
        patch.object(manager._dependency_resolver, "is_ready", return_value=False),
        patch.object(manager, "_submit_app_service_job", return_value=record) as mock_submit,
    ):
        manager._poll_waiting_jobs()  # noqa: SLF001

    mock_submit.assert_not_called()
    with manager._lock:  # noqa: SLF001
        assert record.job_id in manager._waiting_jobs  # noqa: SLF001


def test_publish_event_no_asyncio_run_on_subscriber_path(qapp: object) -> None:
    """Event subscribers are no-ops; _publish_event does not trigger _check_waiting_jobs."""
    manager = DesktopJobManager(load_persisted_history=False)

    with (
        patch.object(manager, "_check_waiting_jobs") as mock_check,
        patch.object(manager, "_check_waiting_jobs_for_project") as mock_check_proj,
    ):
        from novel_forge.core.infra.event_bus import ProjectEvent

        asyncio.run(manager._on_init_started(ProjectEvent(project_id="proj", event_type="init_started")))
        asyncio.run(manager._on_init_completed(ProjectEvent(project_id="proj", event_type="init_completed")))
        asyncio.run(manager._on_outline_updated(ProjectEvent(project_id="proj", event_type="outline_updated")))
        asyncio.run(manager._on_chapter_completed(ProjectEvent(project_id="proj", event_type="chapter_completed")))

    mock_check.assert_not_called()
    mock_check_proj.assert_not_called()
