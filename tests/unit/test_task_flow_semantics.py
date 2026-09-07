from __future__ import annotations

from novel_forge.desktop.jobs import DesktopJobEvent, DesktopJobRecord, DesktopJobState
from novel_forge.desktop.task_flow import (
    TaskFlowOutcome,
    TaskFlowScope,
    task_flow_failure_text,
    task_flow_job_chapter_number,
    task_flow_matches_scope,
    task_flow_outcome,
    task_flow_status_spec,
)
from novel_forge.desktop.task_flow_errors import entries_from_job_record
from novel_forge.desktop.task_observation import TaskObservationStore


def _chapter_job(
    *,
    chapter: int,
    status: DesktopJobState = DesktopJobState.RUNNING,
) -> DesktopJobRecord:
    return DesktopJobRecord(
        job_id=f"chapter-{chapter}-{status.value}",
        kind="run_chapter",
        label=f"生成第 {chapter} 章",
        project_id="demo",
        status=status,
        current_step="draft",
        result={"chapter_number": chapter},
    )


def test_shared_scope_policy_matches_workflow_and_chapter_surfaces() -> None:
    init_job = DesktopJobRecord(
        job_id="init",
        kind="init_long",
        label="长篇立项",
        project_id="demo",
        status=DesktopJobState.RUNNING,
    )
    current = _chapter_job(chapter=3)
    background = _chapter_job(chapter=4)
    old_history = _chapter_job(chapter=4, status=DesktopJobState.SUCCEEDED)
    book_job = DesktopJobRecord(
        job_id="book-audit",
        kind="book_consistency",
        label="全书审修",
        project_id="demo",
        status=DesktopJobState.RUNNING,
    )
    audio_job = DesktopJobRecord(
        job_id="audio-3",
        kind="tts_post_archive",
        label="自动配音 · demo / 第 3 章",
        project_id="demo",
        status=DesktopJobState.RUNNING,
        current_step="tts_script_llm_call",
        result={"chapter_number": 3},
    )

    assert task_flow_matches_scope(init_job, TaskFlowScope.WORKFLOW)
    assert not task_flow_matches_scope(init_job, TaskFlowScope.CHAPTER, project_id="demo")
    assert task_flow_matches_scope(
        current, TaskFlowScope.CHAPTER, project_id="demo", chapter_number=3
    )
    assert task_flow_matches_scope(
        background, TaskFlowScope.CHAPTER, project_id="demo", chapter_number=3
    )
    assert not task_flow_matches_scope(
        old_history, TaskFlowScope.CHAPTER, project_id="demo", chapter_number=3
    )
    assert task_flow_matches_scope(
        book_job, TaskFlowScope.CHAPTER, project_id="demo", chapter_number=3
    )
    assert task_flow_matches_scope(audio_job, TaskFlowScope.WORKFLOW)
    assert task_flow_matches_scope(
        audio_job, TaskFlowScope.CHAPTER, project_id="demo", chapter_number=3
    )


def test_cancellation_is_terminal_but_not_an_error() -> None:
    cancelled = DesktopJobRecord(
        job_id="cancelled",
        kind="init_long",
        label="长篇立项 · demo",
        project_id="demo",
        status=DesktopJobState.FAILED,
        current_step="cancelled",
        error="用户已取消",
        events=[
            DesktopJobEvent(
                at="2026-07-13T12:00:00+00:00",
                step="plan_outline",
                payload={"chapter_number": 7},
            )
        ],
    )

    status = task_flow_status_spec(cancelled)
    assert task_flow_outcome(cancelled) == TaskFlowOutcome.CANCELLED
    assert status.label == "已取消"
    assert status.tone == "muted"
    assert status.is_terminal is True
    assert status.is_error is False
    assert task_flow_failure_text(cancelled) == ("", "")
    assert entries_from_job_record(cancelled) == []
    assert task_flow_job_chapter_number(cancelled) == 7


def test_task_focus_uses_shared_cancelled_status_and_card_progress() -> None:
    cancelled = DesktopJobRecord(
        job_id="cancelled-focus",
        kind="run_chapter",
        label="方案执行 · demo / 第 3 章",
        project_id="demo",
        status=DesktopJobState.FAILED,
        current_step="cancelled",
        error="用户已取消",
        events=[
            DesktopJobEvent(
                at="2026-07-13T12:00:00+00:00",
                step="draft",
                payload={"chapter_number": 3},
            ),
            DesktopJobEvent(
                at="2026-07-13T12:00:01+00:00",
                step="llm_stream_start",
                payload={
                    "stream_id": "stream-cancelled",
                    "task": "draft_chapter",
                    "chapter_number": 3,
                },
            ),
        ],
    )
    store = TaskObservationStore()

    store.ingest_jobs([cancelled])
    state = store.focus_for_scope(TaskFlowScope.GLOBAL)

    assert state is not None
    assert state.status_label == "已取消"
    assert state.status_tone == "muted"
    assert state.focus_reason == "最近取消"
    assert state.progress_percent > 0
    assert "已取消" in state.current_node
    assert state.stream is not None
    assert state.stream.status == "restarted"
    assert state.stream.discarded is True
