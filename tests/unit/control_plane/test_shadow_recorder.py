"""Tests for ShadowRecorder - control-plane dual-write.

Verifies that job lifecycle events are correctly shadow-written to the
control plane, and that all methods swallow exceptions (never break
the main pipeline).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from novel_forge.app_service.contracts import JobCommand, JobKind, JobRecord
from novel_forge.control_plane.enums import (
    AssuranceLevel,
    Priority,
    RunAttemptState,
    WorkUnitState,
)
from novel_forge.control_plane.shadow import (
    ShadowRecorder,
    _compute_idempotency_key,
    _is_write_kind,
    _priority_for_kind,
)
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
def recorder(store: ControlPlaneStore) -> ShadowRecorder:
    return ShadowRecorder(store)


@pytest.fixture
def disabled_recorder() -> ShadowRecorder:
    return ShadowRecorder(None)


def _make_command(kind: JobKind = JobKind.RUN_CHAPTER, project_id: str = "proj1") -> JobCommand:
    return JobCommand(
        kind=kind,
        payload={"chapter_number": 12, "project_id": project_id},
        label="Test Job",
        project_id=project_id,
    )


def _make_record(
    job_id: str = "job1", kind: JobKind = JobKind.RUN_CHAPTER, project_id: str = "proj1"
) -> JobRecord:
    return JobRecord(job_id=job_id, kind=kind, label="Test Job", project_id=project_id)


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


def test_is_write_kind() -> None:
    assert _is_write_kind("run_chapter")
    assert _is_write_kind("init_long")
    assert _is_write_kind("repair_continuity")
    assert _is_write_kind("polish_outline")
    assert _is_write_kind("tts_synthesize")
    assert _is_write_kind("tts_full_pipeline")
    assert not _is_write_kind("export_book")
    assert not _is_write_kind("unknown_kind")


def test_priority_for_kind() -> None:
    assert _priority_for_kind("resolve_chapter_checkpoint") == Priority.P0
    assert _priority_for_kind("resolve_chapter_checkpoint_finalize") == Priority.P0
    assert _priority_for_kind("run_chapter") == Priority.P1
    assert _priority_for_kind("init_long") == Priority.P1
    assert _priority_for_kind("polish_chapter") == Priority.P1
    assert _priority_for_kind("tts_synthesize") == Priority.P1
    assert _priority_for_kind("reevaluate_chapter") == Priority.P2
    assert _priority_for_kind("book_consistency") == Priority.P2
    assert _priority_for_kind("polish_outline") == Priority.P2
    # Unknown kind defaults to P1
    assert _priority_for_kind("unknown") == Priority.P1


def test_compute_idempotency_key_stable() -> None:
    payload = {"chapter": 12, "project": "p1"}
    key1 = _compute_idempotency_key("run_chapter", "p1", payload)
    key2 = _compute_idempotency_key("run_chapter", "p1", payload)
    assert key1 == key2
    assert key1.startswith("run_chapter:p1:")


def test_compute_idempotency_key_differs_on_payload() -> None:
    key1 = _compute_idempotency_key("run_chapter", "p1", {"chapter": 12})
    key2 = _compute_idempotency_key("run_chapter", "p1", {"chapter": 13})
    assert key1 != key2


def test_compute_idempotency_key_differs_on_project() -> None:
    key1 = _compute_idempotency_key("run_chapter", "p1", {"chapter": 12})
    key2 = _compute_idempotency_key("run_chapter", "p2", {"chapter": 12})
    assert key1 != key2


# ---------------------------------------------------------------------------
# Disabled recorder (store=None)
# ---------------------------------------------------------------------------


def test_disabled_recorder_no_op(disabled_recorder: ShadowRecorder) -> None:
    """All methods should be no-ops when store is None."""
    assert not disabled_recorder.enabled
    # These should not raise
    disabled_recorder.on_submit("job1", _make_command(), _make_record())
    disabled_recorder.on_started("job1")
    disabled_recorder.on_step("job1", "draft")
    disabled_recorder.on_succeeded("job1")
    disabled_recorder.on_failed("job1", "gateway", {"error": "test"})
    disabled_recorder.on_paused("job1")
    disabled_recorder.on_cancelled("job1", "user cancelled")
    disabled_recorder.mark_degraded("job1", "provider down")


# ---------------------------------------------------------------------------
# Lifecycle shadow-write tests
# ---------------------------------------------------------------------------


def test_on_submit_creates_queued_work_unit_without_attempt(
    recorder: ShadowRecorder, store: ControlPlaneStore
) -> None:
    command = _make_command()
    record = _make_record()
    recorder.on_submit("job1", command, record)

    wu = store.sync.get_work_unit("job1")
    assert wu is not None
    assert wu.kind == "run_chapter"
    assert wu.project_id == "proj1"
    assert wu.state == WorkUnitState.QUEUED
    assert wu.priority == Priority.P1
    assert wu.idempotency_key.startswith("run_chapter:proj1:")

    attempts = store.sync.list_run_attempts("job1")
    assert attempts == []


def test_on_started_creates_first_actual_attempt(
    recorder: ShadowRecorder, store: ControlPlaneStore
) -> None:
    recorder.on_submit("job1", _make_command(), _make_record())
    recorder.on_started("job1")

    attempts = store.sync.list_run_attempts("job1")
    assert len(attempts) == 1
    assert attempts[0].id == "job1_a1"
    assert attempts[0].attempt_number == 1
    assert attempts[0].state == RunAttemptState.RUNNING


def test_retry_wait_resumption_creates_next_attempt(
    recorder: ShadowRecorder, store: ControlPlaneStore
) -> None:
    recorder.on_submit("job1", _make_command(), _make_record())
    recorder.on_started("job1")
    recorder.mark_degraded("job1", "provider outage")

    recorder.on_submit("job1", _make_command(), _make_record())
    recorder.on_started("job1")

    attempts = store.sync.list_run_attempts("job1")
    assert [attempt.attempt_number for attempt in attempts] == [1, 2]
    assert attempts[-1].id == "job1_a2"


def test_on_submit_skips_non_write_kinds(
    recorder: ShadowRecorder, store: ControlPlaneStore
) -> None:
    command = JobCommand(kind=JobKind.EXPORT_BOOK, project_id="proj1", label="Export")
    record = JobRecord(job_id="job2", kind=JobKind.EXPORT_BOOK, label="Export", project_id="proj1")
    recorder.on_submit("job2", command, record)

    # No WorkUnit should be created for non-write kinds
    assert store.sync.get_work_unit("job2") is None


def test_on_started_updates_state_to_running(
    recorder: ShadowRecorder, store: ControlPlaneStore
) -> None:
    recorder.on_submit("job1", _make_command(), _make_record())
    recorder.on_started("job1")

    wu = store.sync.get_work_unit("job1")
    assert wu is not None
    assert wu.state == WorkUnitState.RUNNING
    assert wu.heartbeat_at != ""


def test_on_step_updates_heartbeat(recorder: ShadowRecorder, store: ControlPlaneStore) -> None:
    recorder.on_submit("job1", _make_command(), _make_record())
    recorder.on_started("job1")

    hb_before = store.sync.get_work_unit("job1").heartbeat_at
    # Small delay to ensure timestamp differs
    import time

    time.sleep(0.01)
    recorder.on_step("job1", "draft_progress")

    attempts = store.sync.list_run_attempts("job1")
    assert attempts[0].heartbeat_at >= hb_before


def test_on_succeeded_marks_committed(recorder: ShadowRecorder, store: ControlPlaneStore) -> None:
    recorder.on_submit("job1", _make_command(), _make_record())
    recorder.on_started("job1")
    recorder.on_succeeded("job1")

    wu = store.sync.get_work_unit("job1")
    assert wu is not None
    assert wu.state == WorkUnitState.COMMITTED

    attempts = store.sync.list_run_attempts("job1")
    assert attempts[0].state == RunAttemptState.COMMITTED
    assert attempts[0].ended_at != ""


def test_on_failed_marks_failed(recorder: ShadowRecorder, store: ControlPlaneStore) -> None:
    recorder.on_submit("job1", _make_command(), _make_record())
    recorder.on_started("job1")
    recorder.on_failed("job1", "gateway", {"error": "rate_limit", "provider": "openai"})

    wu = store.sync.get_work_unit("job1")
    assert wu is not None
    assert wu.state == WorkUnitState.FAILED

    attempts = store.sync.list_run_attempts("job1")
    assert attempts[0].state == RunAttemptState.FAILED
    assert attempts[0].error_kind == "gateway"
    assert attempts[0].error_summary == {"error": "rate_limit", "provider": "openai"}


def test_on_paused_marks_waiting_human(recorder: ShadowRecorder, store: ControlPlaneStore) -> None:
    recorder.on_submit("job1", _make_command(), _make_record())
    recorder.on_started("job1")
    recorder.on_paused("job1")

    wu = store.sync.get_work_unit("job1")
    assert wu is not None
    assert wu.state == WorkUnitState.WAITING_HUMAN

    attempts = store.sync.list_run_attempts("job1")
    assert attempts[0].state == RunAttemptState.WAITING_HUMAN


def test_on_cancelled_marks_cancelled(recorder: ShadowRecorder, store: ControlPlaneStore) -> None:
    recorder.on_submit("job1", _make_command(), _make_record())
    recorder.on_started("job1")
    recorder.on_cancelled("job1", "user cancelled")

    wu = store.sync.get_work_unit("job1")
    assert wu is not None
    assert wu.state == WorkUnitState.CANCELLED

    attempts = store.sync.list_run_attempts("job1")
    assert attempts[0].state == RunAttemptState.CANCELLED


def test_mark_degraded_sets_assurance(recorder: ShadowRecorder, store: ControlPlaneStore) -> None:
    recorder.on_submit("job1", _make_command(), _make_record())
    recorder.on_started("job1")
    recorder.mark_degraded("job1", "all providers unavailable")

    wu = store.sync.get_work_unit("job1")
    assert wu is not None
    assert wu.assurance == AssuranceLevel.DEGRADED
    assert wu.state == WorkUnitState.RETRY_WAIT


# ---------------------------------------------------------------------------
# Exception swallowing
# ---------------------------------------------------------------------------


def test_methods_swallow_exceptions() -> None:
    """ShadowRecorder methods must never raise, even if the store fails."""
    broken_store = MagicMock()
    broken_store.sync.create_work_unit.side_effect = RuntimeError("DB down")
    broken_store.sync.update_work_unit_state.side_effect = RuntimeError("DB down")
    broken_store.sync.update_run_attempt.side_effect = RuntimeError("DB down")
    broken_store.sync.create_run_attempt.side_effect = RuntimeError("DB down")

    recorder = ShadowRecorder(broken_store)

    # None of these should raise
    recorder.on_submit("job1", _make_command(), _make_record())
    recorder.on_started("job1")
    recorder.on_step("job1", "draft")
    recorder.on_succeeded("job1")
    recorder.on_failed("job1", "gateway", {})
    recorder.on_paused("job1")
    recorder.on_cancelled("job1", "reason")
    recorder.mark_degraded("job1", "reason")


# ---------------------------------------------------------------------------
# Full lifecycle integration
# ---------------------------------------------------------------------------


def test_full_lifecycle_succeeded(recorder: ShadowRecorder, store: ControlPlaneStore) -> None:
    """Full submit -> started -> step -> succeeded lifecycle."""
    recorder.on_submit("job1", _make_command(), _make_record())
    recorder.on_started("job1")
    recorder.on_step("job1", "planning")
    recorder.on_step("job1", "draft")
    recorder.on_step("job1", "wave")
    recorder.on_step("job1", "finalize")
    recorder.on_succeeded("job1")

    wu = store.sync.get_work_unit("job1")
    assert wu.state == WorkUnitState.COMMITTED

    active = store.sync.list_active_work_units()
    assert len(active) == 0  # committed is not active


def test_full_lifecycle_failed(recorder: ShadowRecorder, store: ControlPlaneStore) -> None:
    """Full submit -> started -> step -> failed lifecycle."""
    recorder.on_submit("job1", _make_command(), _make_record())
    recorder.on_started("job1")
    recorder.on_step("job1", "draft")
    recorder.on_failed("job1", "timeout", {"detail": "model took too long"})

    wu = store.sync.get_work_unit("job1")
    assert wu.state == WorkUnitState.FAILED

    active = store.sync.list_active_work_units()
    assert len(active) == 0  # failed is not active


def test_full_lifecycle_paused_then_resumed(
    recorder: ShadowRecorder, store: ControlPlaneStore
) -> None:
    """submit -> started -> paused -> (user resolves) -> succeeded."""
    recorder.on_submit("job1", _make_command(), _make_record())
    recorder.on_started("job1")
    recorder.on_step("job1", "draft")
    recorder.on_paused("job1")

    # While paused, the work unit is waiting_human (still active)
    active = store.sync.list_active_work_units()
    assert len(active) == 1
    assert active[0].state == WorkUnitState.WAITING_HUMAN

    # User resolves the checkpoint - a new attempt would be created
    # (in the full system). For shadow-write, we just mark success.
    recorder.on_succeeded("job1")

    wu = store.sync.get_work_unit("job1")
    assert wu.state == WorkUnitState.COMMITTED
    active = store.sync.list_active_work_units()
    assert len(active) == 0
