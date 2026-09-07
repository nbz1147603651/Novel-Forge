"""Tests for idempotent submit and WorkUnitResumer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_forge.app_service.contracts import JobCommand, JobKind
from novel_forge.control_plane.enums import (
    WorkUnitState,
)
from novel_forge.control_plane.resume import (
    IdempotencyCheckResult,
    WorkUnitResumer,
    check_idempotent_submit,
)
from novel_forge.control_plane.schemas import WorkUnitDTO
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
def resumer(store: ControlPlaneStore) -> WorkUnitResumer:
    return WorkUnitResumer(store)


# ---------------------------------------------------------------------------
# IdempotencyCheckResult
# ---------------------------------------------------------------------------


def test_idempotency_result_should_skip_when_committed() -> None:
    r = IdempotencyCheckResult(
        is_duplicate=True,
        existing_work_unit_id="wu1",
        state=WorkUnitState.COMMITTED,
    )
    assert r.should_skip_submit


def test_idempotency_result_should_not_skip_when_running() -> None:
    r = IdempotencyCheckResult(
        is_duplicate=True,
        existing_work_unit_id="wu1",
        state=WorkUnitState.RUNNING,
    )
    assert not r.should_skip_submit


def test_idempotency_result_should_not_skip_when_not_duplicate() -> None:
    r = IdempotencyCheckResult(is_duplicate=False)
    assert not r.should_skip_submit


# ---------------------------------------------------------------------------
# check_idempotent_submit
# ---------------------------------------------------------------------------


def test_check_idempotent_no_existing(store: ControlPlaneStore) -> None:
    """No existing work unit -> not a duplicate."""
    result = check_idempotent_submit(store, "nonexistent_key")
    assert not result.is_duplicate
    assert not result.should_skip_submit


def test_check_idempotent_empty_key(store: ControlPlaneStore) -> None:
    """Empty key -> not a duplicate (no check performed)."""
    result = check_idempotent_submit(store, "")
    assert not result.is_duplicate


def test_check_idempotent_committed(store: ControlPlaneStore) -> None:
    """A committed work unit -> should skip submit."""
    wu = WorkUnitDTO(
        id="wu_committed",
        kind="run_chapter",
        project_id="proj1",
        state=WorkUnitState.COMMITTED,
        idempotency_key="committed_key",
    )
    store.sync.create_work_unit(wu)

    result = check_idempotent_submit(store, "committed_key")
    assert result.is_duplicate
    assert result.existing_work_unit_id == "wu_committed"
    assert result.state == WorkUnitState.COMMITTED
    assert result.should_skip_submit


def test_check_idempotent_running(store: ControlPlaneStore) -> None:
    """A running work unit -> duplicate but should NOT skip (return existing)."""
    wu = WorkUnitDTO(
        id="wu_running",
        kind="run_chapter",
        project_id="proj1",
        state=WorkUnitState.RUNNING,
        idempotency_key="running_key",
    )
    store.sync.create_work_unit(wu)

    result = check_idempotent_submit(store, "running_key")
    assert result.is_duplicate
    assert result.state == WorkUnitState.RUNNING
    assert not result.should_skip_submit


def test_check_idempotent_failed(store: ControlPlaneStore) -> None:
    """A failed work unit -> duplicate but should NOT skip (allow retry)."""
    wu = WorkUnitDTO(
        id="wu_failed",
        kind="run_chapter",
        project_id="proj1",
        state=WorkUnitState.FAILED,
        idempotency_key="failed_key",
    )
    store.sync.create_work_unit(wu)

    result = check_idempotent_submit(store, "failed_key")
    assert result.is_duplicate
    assert result.state == WorkUnitState.FAILED
    assert not result.should_skip_submit


def test_check_idempotent_retry_wait(store: ControlPlaneStore) -> None:
    """A retry_wait work unit -> duplicate but should NOT skip (allow retry)."""
    wu = WorkUnitDTO(
        id="wu_retry",
        kind="run_chapter",
        project_id="proj1",
        state=WorkUnitState.RETRY_WAIT,
        idempotency_key="retry_key",
    )
    store.sync.create_work_unit(wu)

    result = check_idempotent_submit(store, "retry_key")
    assert result.is_duplicate
    assert not result.should_skip_submit


# ---------------------------------------------------------------------------
# WorkUnitResumer
# ---------------------------------------------------------------------------


def test_resumer_list_resumable_empty(store: ControlPlaneStore, resumer: WorkUnitResumer) -> None:
    """No retry_wait work units -> empty list."""
    assert resumer.list_resumable_work_units() == []


def test_resumer_list_resumable(store: ControlPlaneStore, resumer: WorkUnitResumer) -> None:
    """Only retry_wait work units should be listed."""
    wu1 = WorkUnitDTO(
        id="wu_retry1",
        kind="run_chapter",
        project_id="proj1",
        state=WorkUnitState.RETRY_WAIT,
        idempotency_key="retry1",
    )
    wu2 = WorkUnitDTO(
        id="wu_committed1",
        kind="run_chapter",
        project_id="proj1",
        state=WorkUnitState.COMMITTED,
        idempotency_key="committed1",
    )
    wu3 = WorkUnitDTO(
        id="wu_retry2",
        kind="run_chapter",
        project_id="proj2",
        state=WorkUnitState.RETRY_WAIT,
        idempotency_key="retry2",
    )
    store.sync.create_work_unit(wu1)
    store.sync.create_work_unit(wu2)
    store.sync.create_work_unit(wu3)

    resumable = resumer.list_resumable_work_units()
    assert len(resumable) == 2
    ids = {r.id for r in resumable}
    assert ids == {"wu_retry1", "wu_retry2"}


def test_resumer_get_work_unit(store: ControlPlaneStore, resumer: WorkUnitResumer) -> None:
    wu = WorkUnitDTO(id="wu_get", kind="run_chapter", project_id="proj1")
    store.sync.create_work_unit(wu)

    fetched = resumer.get_work_unit("wu_get")
    assert fetched is not None
    assert fetched.id == "wu_get"

    assert resumer.get_work_unit("nonexistent") is None


def test_resumer_mark_resumed(store: ControlPlaneStore, resumer: WorkUnitResumer) -> None:
    wu = WorkUnitDTO(
        id="wu_resume_test",
        kind="run_chapter",
        project_id="proj1",
        state=WorkUnitState.RETRY_WAIT,
    )
    store.sync.create_work_unit(wu)

    resumer.mark_resumed("wu_resume_test")

    wu_after = store.sync.get_work_unit("wu_resume_test")
    assert wu_after is not None
    # mark_resumed transitions RETRY_WAIT -> QUEUED, not RUNNING.
    # The RUNNING state is set by on_started when the worker actually begins.
    assert wu_after.state == WorkUnitState.QUEUED


def test_resumer_mark_resumed_nonexistent(resumer: WorkUnitResumer) -> None:
    """Marking a nonexistent work unit should not raise."""
    resumer.mark_resumed("nonexistent_id")


def test_resumer_rebuilds_retry_command_from_project_intent(
    store: ControlPlaneStore,
    resumer: WorkUnitResumer,
    tmp_path: Path,
) -> None:
    intent_path = tmp_path / "states" / "control_plane_intents" / "wu_resume.json"
    intent_path.parent.mkdir(parents=True)
    intent_path.write_text(
        json.dumps(
            JobCommand(
                kind=JobKind.RUN_CHAPTER,
                project_id="proj1",
                label="恢复章节",
                payload={"chapter_number": 7},
                mock=True,
            ).model_dump(mode="json"),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store.sync.create_work_unit(
        WorkUnitDTO(
            id="wu_resume",
            kind="run_chapter",
            project_id="proj1",
            state=WorkUnitState.RETRY_WAIT,
            intent_payload_path=str(intent_path),
        )
    )

    command = resumer.rebuild_command("wu_resume")

    assert command is not None
    assert command.job_id == "wu_resume"
    assert command.kind == JobKind.RUN_CHAPTER
    assert command.payload == {"chapter_number": 7}
    assert command.mock is True
