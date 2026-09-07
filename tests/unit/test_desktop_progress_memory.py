"""Tests for workflow memory-stage status extraction."""

from __future__ import annotations

from novel_forge.desktop.jobs import DesktopJobEvent, DesktopJobRecord
from novel_forge.desktop.progress import memory_stage_status_for_job


def _make_job(*events: tuple[str, dict]) -> DesktopJobRecord:
    job = DesktopJobRecord(job_id="job-1", kind="run_chapter", label="章节续写")
    job.events = [
        DesktopJobEvent(
            at=f"2026-03-30T10:00:{idx:02d}+00:00",
            step=step,
            payload=payload,
        )
        for idx, (step, payload) in enumerate(events)
    ]
    return job


def test_memory_stage_status_marks_three_stages_done() -> None:
    job = _make_job(
        ("memory_indexing_started", {"chapter": 7}),
        ("memory_indexing_complete", {"chapter": 7, "success": True}),
        ("memory_summary_generated", {"chapter": 7, "success": True}),
        ("memory_motifs_completed", {"chapter": 7, "success": True}),
    )

    status = memory_stage_status_for_job(job)

    assert status == {
        "indexing": "done",
        "summary": "done",
        "motif": "done",
    }


def test_memory_stage_status_marks_running_and_failed_paths() -> None:
    job = _make_job(
        ("memory_indexing_started", {"chapter": 4}),
        ("memory_summary_scheduled", {"chapter": 4, "success": True}),
        ("memory_motifs_extracted", {"chapter": 4, "success": True}),
        ("memory_motifs_completed", {"chapter": 4, "success": False}),
    )

    status = memory_stage_status_for_job(job)

    assert status == {
        "indexing": "running",
        "summary": "running",
        "motif": "failed",
    }


def test_memory_stage_status_returns_none_for_non_chapter_jobs() -> None:
    job = DesktopJobRecord(job_id="job-2", kind="run_short", label="短篇")
    job.events = [DesktopJobEvent(at="2026-03-30T10:00:00+00:00", step="memory_updated", payload={})]
    assert memory_stage_status_for_job(job) is None


def test_memory_updated_marks_summary_and_motif_done_when_stage_events_are_absent() -> None:
    job = _make_job(
        (
            "memory_updated",
            {
                "chapter": 9,
                "last_indexed_chapter": 9,
                "memory_module_status": {
                    "summary_enabled": True,
                    "motif_enabled": True,
                },
            },
        ),
    )

    status = memory_stage_status_for_job(job)

    assert status == {
        "indexing": "done",
        "summary": "done",
        "motif": "done",
    }


def test_memory_concurrent_events_mark_running_and_done_stages() -> None:
    job = _make_job(
        ("memory_indexing_started", {"chapter": 12}),
        (
            "memory_concurrent_tasks_started",
            {"chapter": 12, "tasks": ["episodic", "summary", "motifs"]},
        ),
        (
            "memory_concurrent_tasks_done",
            {
                "chapter": 12,
                "tasks_ok": ["episodic", "summary", "motifs"],
                "tasks_failed": [],
            },
        ),
    )

    status = memory_stage_status_for_job(job)

    assert status == {
        "indexing": "done",
        "summary": "done",
        "motif": "done",
    }


def test_memory_concurrent_events_expose_failed_subtask() -> None:
    job = _make_job(
        (
            "memory_concurrent_tasks_started",
            {"chapter": 12, "tasks": ["episodic", "summary", "motifs"]},
        ),
        (
            "memory_concurrent_tasks_done",
            {
                "chapter": 12,
                "tasks_ok": ["episodic", "summary"],
                "tasks_failed": ["motifs"],
            },
        ),
    )

    status = memory_stage_status_for_job(job)

    assert status == {
        "indexing": "done",
        "summary": "done",
        "motif": "failed",
    }


def test_stage_memory_context_events_do_not_trigger_memory_progress_row() -> None:
    job = _make_job(
        (
            "memory_planning_context",
            {
                "requested_layers": ["L0_identity", "L1_core_memory", "L2_on_demand"],
                "resolved_layers": ["L0_identity", "L1_core_memory", "L2_on_demand"],
            },
        ),
    )

    assert memory_stage_status_for_job(job) is None
