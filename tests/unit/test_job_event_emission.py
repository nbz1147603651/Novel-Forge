"""Tests for granular section-changed events emitted by DesktopJobManager.

Task 16: DesktopJobManager emits SECTION_CHANGED events on job completion/failure.
- Chapter jobs → ProjectChanged(project_id, "chapters")
- Init/short jobs → SectionChanged("projects")
- Book consistency → SectionChanged("details")
- Any failure → SectionChanged("jobs")
- Irrelevant jobs (export_book) → no event
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from novel_forge.desktop.jobs import (
    DesktopJobManager,
    DesktopJobRecord,
    DesktopJobState,
    _job_kind_to_section,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def manager():
    """DesktopJobManager with no history loading and mocked event bus."""
    mgr = DesktopJobManager(load_persisted_history=False)
    mgr._publish_event = MagicMock()
    mgr._publish_section_change = MagicMock()
    yield mgr
    mgr.shutdown()


def _make_record(
    job_id: str = "job1",
    kind: str = "run_chapter",
    project_id: str = "proj1",
    chapter_number: int | None = 1,
) -> DesktopJobRecord:
    rec = DesktopJobRecord(
        job_id=job_id,
        kind=kind,
        label=f"test {kind}",
        project_id=project_id,
        status=DesktopJobState.RUNNING,
    )
    if chapter_number is not None:
        rec.result = {"chapter_number": chapter_number}
    return rec


# ── _job_kind_to_section mapping ─────────────────────────────────────────────


class TestJobKindToSectionMapping:
    """Unit tests for the kind → section mapping function."""

    def test_run_chapter_maps_to_chapters(self):
        assert _job_kind_to_section("run_chapter") == "chapters"

    def test_prepare_chapter_maps_to_chapters(self):
        assert _job_kind_to_section("prepare_chapter") == "chapters"

    def test_polish_chapter_maps_to_chapters(self):
        assert _job_kind_to_section("polish_chapter") == "chapters"

    def test_repair_continuity_maps_to_chapters(self):
        assert _job_kind_to_section("repair_continuity") == "chapters"

    def test_repair_causal_maps_to_chapters(self):
        assert _job_kind_to_section("repair_causal") == "chapters"

    def test_repair_issues_maps_to_chapters(self):
        assert _job_kind_to_section("repair_issues") == "chapters"

    def test_reevaluate_chapter_maps_to_chapters(self):
        assert _job_kind_to_section("reevaluate_chapter") == "chapters"

    def test_resolve_checkpoint_maps_to_chapters(self):
        assert _job_kind_to_section("resolve_chapter_checkpoint") == "chapters"

    def test_reextract_relationships_maps_to_chapters(self):
        assert _job_kind_to_section("reextract_relationships") == "chapters"

    def test_init_long_maps_to_projects(self):
        assert _job_kind_to_section("init_long") == "projects"

    def test_run_short_maps_to_projects(self):
        assert _job_kind_to_section("run_short") == "projects"

    def test_book_consistency_maps_to_details(self):
        assert _job_kind_to_section("book_consistency") == "details"

    def test_sync_chapter_contracts_maps_to_details(self):
        assert _job_kind_to_section("sync_chapter_contracts") == "details"

    def test_tts_post_archive_maps_to_details(self):
        assert _job_kind_to_section("tts_post_archive") == "details"

    def test_export_book_returns_none(self):
        assert _job_kind_to_section("export_book") is None

    def test_repair_motif_history_returns_none(self):
        assert _job_kind_to_section("repair_motif_history") is None

    def test_unknown_kind_returns_none(self):
        assert _job_kind_to_section("nonexistent_kind") is None

    def test_empty_string_returns_none(self):
        assert _job_kind_to_section("") is None


# ── _handle_finished publishes section events ─────────────────────────────────


class TestHandleFinishedPublishesSectionEvent:
    """When a job completes successfully, the correct section event fires."""

    def test_chapter_job_publishes_chapters_section(self, manager):
        rec = _make_record(kind="run_chapter", project_id="proj1", chapter_number=3)
        with manager._lock:
            manager._jobs["job1"] = rec

        payload = {"project_id": "proj1", "chapter_number": 3}
        manager._handle_finished("job1", payload)

        manager._publish_section_change.assert_any_call("proj1", "chapters")

    def test_completed_chapter_links_independent_tts_followup(self, manager):
        rec = _make_record(kind="run_chapter", project_id="proj1", chapter_number=3)
        followup = _make_record(
            job_id="tts-child",
            kind="tts_post_archive",
            project_id="proj1",
            chapter_number=3,
        )
        followup.status = DesktopJobState.QUEUED
        manager.submit_post_archive_tts = MagicMock(return_value=followup)
        with manager._lock:
            manager._jobs["job1"] = rec

        manager._handle_finished("job1", {"project_id": "proj1", "chapter_number": 3})

        manager.submit_post_archive_tts.assert_called_once_with(
            project_id="proj1",
            chapter_number=3,
            parent_job_id="job1",
        )
        completed = next(job for job in manager.jobs() if job.job_id == "job1")
        assert completed.status == DesktopJobState.SUCCEEDED
        assert completed.result["tts_followup_job_id"] == "tts-child"
        assert completed.result["tts_followup_status"] == "queued"

    def test_tts_queue_failure_never_changes_archived_chapter_success(self, manager):
        rec = _make_record(kind="run_chapter", project_id="proj1", chapter_number=4)
        manager.submit_post_archive_tts = MagicMock(side_effect=RuntimeError("queue offline"))
        with manager._lock:
            manager._jobs["job1"] = rec

        manager._handle_finished("job1", {"project_id": "proj1", "chapter_number": 4})

        completed = next(job for job in manager.jobs() if job.job_id == "job1")
        assert completed.status == DesktopJobState.SUCCEEDED
        assert completed.result["tts_followup_status"] == "queue_failed"
        assert completed.result["tts_followup_error"] == "queue offline"

    def test_init_long_publishes_projects_section(self, manager):
        rec = _make_record(kind="init_long", project_id="proj1", chapter_number=None)
        with manager._lock:
            manager._jobs["job1"] = rec

        payload = {"project_id": "proj1"}
        manager._handle_finished("job1", payload)

        manager._publish_section_change.assert_any_call("proj1", "projects")

    def test_book_consistency_publishes_details_section(self, manager):
        rec = _make_record(kind="book_consistency", project_id="proj1", chapter_number=None)
        with manager._lock:
            manager._jobs["job1"] = rec

        payload = {"project_id": "proj1"}
        manager._handle_finished("job1", payload)

        manager._publish_section_change.assert_any_call("proj1", "details")

    def test_export_book_publishes_no_section_event(self, manager):
        rec = _make_record(kind="export_book", project_id="proj1", chapter_number=None)
        with manager._lock:
            manager._jobs["job1"] = rec

        payload = {"project_id": "proj1"}
        manager._handle_finished("job1", payload)

        # _publish_section_change should NOT have been called for this job
        for call_args in manager._publish_section_change.call_args_list:
            assert call_args != (("proj1", "exports"),), f"Unexpected section event: {call_args}"
        # More precisely: no call with any section for this project
        section_calls = [
            c
            for c in manager._publish_section_change.call_args_list
            if len(c.args) >= 2 and c.args[0] == "proj1"
        ]
        assert section_calls == [], f"Expected no section events, got {section_calls}"

    def test_polish_chapter_publishes_chapters_section(self, manager):
        rec = _make_record(kind="polish_chapter", project_id="proj2", chapter_number=5)
        with manager._lock:
            manager._jobs["job1"] = rec

        payload = {"project_id": "proj2", "chapter_number": 5}
        manager._handle_finished("job1", payload)

        manager._publish_section_change.assert_any_call("proj2", "chapters")


# ── _handle_failed publishes jobs section ─────────────────────────────────────


class TestHandleFailedPublishesJobsSection:
    """When a job fails, a 'jobs' section event fires for status badge updates."""

    def test_failed_job_publishes_jobs_section(self, manager):
        rec = _make_record(kind="run_chapter", project_id="proj1", chapter_number=1)
        with manager._lock:
            manager._jobs["job1"] = rec

        manager._handle_failed("job1", {"error": "test failure"})

        manager._publish_section_change.assert_any_call("proj1", "jobs")

    def test_failed_init_long_publishes_jobs_section(self, manager):
        rec = _make_record(kind="init_long", project_id="proj2", chapter_number=None)
        with manager._lock:
            manager._jobs["job1"] = rec

        manager._handle_failed("job1", {"error": "init failed"})

        manager._publish_section_change.assert_any_call("proj2", "jobs")

    def test_failed_job_no_project_id_still_publishes(self, manager):
        """Even without a project_id, the jobs section event fires (with empty pid)."""
        rec = _make_record(kind="run_short", project_id="", chapter_number=None)
        with manager._lock:
            manager._jobs["job1"] = rec

        manager._handle_failed("job1", {"error": "fail"})

        manager._publish_section_change.assert_any_call("", "jobs")


# ── Cancelled jobs do NOT publish section events ─────────────────────────────


class TestCancelledJobsNoSectionEvent:
    """Cancelled jobs (already FAILED when finished arrives) skip section events."""

    def test_cancelled_job_does_not_publish_section(self, manager):
        rec = _make_record(kind="run_chapter", project_id="proj1", chapter_number=1)
        rec.status = DesktopJobState.FAILED  # simulates already-cancelled
        with manager._lock:
            manager._jobs["job1"] = rec

        manager._handle_finished("job1", {"project_id": "proj1", "chapter_number": 1})

        # No section change should be published for cancelled jobs
        manager._publish_section_change.assert_not_called()


# ── needs_decision (PAUSED) jobs DO publish section events ────────────────────


class TestNeedsDecisionPublishesSectionEvent:
    """Jobs that pause for decision (needs_decision) still publish section events."""

    def test_needs_decision_chapter_job_publishes_chapters(self, manager):
        rec = _make_record(kind="run_chapter", project_id="proj1", chapter_number=2)
        with manager._lock:
            manager._jobs["job1"] = rec

        payload = {
            "project_id": "proj1",
            "status": "needs_decision",
            "chapter_number": 2,
            "checkpoint": {"checkpoint_type": "guard_checkpoint"},
        }
        manager._handle_finished("job1", payload)

        manager._publish_section_change.assert_any_call("proj1", "chapters")
