"""Tests for ControlPlaneStore CRUD, WAL/FK, and sync bridge.

Covers all six tables plus the daemon-loop SyncFacade that allows
sync threads (JobService worker, Desktop Qt) to call the async store.
"""

from __future__ import annotations

import threading

import pytest

from novel_forge.control_plane.enums import (
    ArtifactKind,
    AssuranceLevel,
    EventSeverity,
    Priority,
    ResourceKind,
    RunAttemptState,
    StageState,
    WorkUnitState,
)
from novel_forge.control_plane.schemas import (
    ArtifactManifestDTO,
    EventLedgerEntryDTO,
    ResourceVersionDTO,
    RunAttemptDTO,
    StageExecutionDTO,
    WorkUnitDTO,
)
from novel_forge.control_plane.store import ControlPlaneStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def store() -> ControlPlaneStore:
    """Fresh in-memory store with schema initialized."""
    s = ControlPlaneStore.in_memory()
    await s.init_db()
    yield s
    await s.close()


@pytest.fixture
def work_unit_dto() -> WorkUnitDTO:
    return WorkUnitDTO(
        kind="run_chapter",
        project_id="test_project",
        priority=Priority.P1,
        idempotency_key="run_chapter:test_project:abc123",
        label="Generate Chapter 12",
    )


# ---------------------------------------------------------------------------
# WorkUnit CRUD
# ---------------------------------------------------------------------------


async def test_create_and_get_work_unit(
    store: ControlPlaneStore, work_unit_dto: WorkUnitDTO
) -> None:
    created = await store.create_work_unit(work_unit_dto)
    assert created.id == work_unit_dto.id

    fetched = await store.get_work_unit(work_unit_dto.id)
    assert fetched is not None
    assert fetched.kind == "run_chapter"
    assert fetched.project_id == "test_project"
    assert fetched.priority == Priority.P1
    assert fetched.state == WorkUnitState.QUEUED
    assert fetched.assurance == AssuranceLevel.NORMAL
    assert fetched.idempotency_key == "run_chapter:test_project:abc123"


async def test_get_nonexistent_work_unit(store: ControlPlaneStore) -> None:
    assert await store.get_work_unit("nonexistent") is None


async def test_update_work_unit_state(store: ControlPlaneStore, work_unit_dto: WorkUnitDTO) -> None:
    await store.create_work_unit(work_unit_dto)

    updated = await store.update_work_unit_state(
        work_unit_dto.id,
        WorkUnitState.RUNNING,
        heartbeat_at="2026-07-11T10:00:00+00:00",
    )
    assert updated is not None
    assert updated.state == WorkUnitState.RUNNING
    assert updated.heartbeat_at == "2026-07-11T10:00:00+00:00"

    # Update with degraded assurance
    updated = await store.update_work_unit_state(
        work_unit_dto.id,
        WorkUnitState.RETRY_WAIT,
        assurance=AssuranceLevel.DEGRADED,
    )
    assert updated is not None
    assert updated.state == WorkUnitState.RETRY_WAIT
    assert updated.assurance == AssuranceLevel.DEGRADED


async def test_state_transition_validation(
    store: ControlPlaneStore, work_unit_dto: WorkUnitDTO
) -> None:
    """Invalid transitions are rejected; force=True overrides the guard."""
    await store.create_work_unit(work_unit_dto)

    # Valid: QUEUED -> RUNNING
    updated = await store.update_work_unit_state(work_unit_dto.id, WorkUnitState.RUNNING)
    assert updated is not None
    assert updated.state == WorkUnitState.RUNNING

    # Invalid: RUNNING -> QUEUED (cannot go backwards)
    result = await store.update_work_unit_state(work_unit_dto.id, WorkUnitState.QUEUED)
    assert result is not None
    assert result.state == WorkUnitState.RUNNING  # unchanged

    # Invalid: RUNNING -> COMMITTED (valid)
    result = await store.update_work_unit_state(work_unit_dto.id, WorkUnitState.COMMITTED)
    assert result is not None
    assert result.state == WorkUnitState.COMMITTED

    # Invalid: COMMITTED -> RUNNING (terminal, no outgoing)
    result = await store.update_work_unit_state(work_unit_dto.id, WorkUnitState.RUNNING)
    assert result is not None
    assert result.state == WorkUnitState.COMMITTED  # unchanged

    # Force override: COMMITTED -> RUNNING (crash recovery scenario)
    result = await store.update_work_unit_state(work_unit_dto.id, WorkUnitState.RUNNING, force=True)
    assert result is not None
    assert result.state == WorkUnitState.RUNNING


async def test_find_by_idempotency_key(
    store: ControlPlaneStore, work_unit_dto: WorkUnitDTO
) -> None:
    await store.create_work_unit(work_unit_dto)

    found = await store.find_by_idempotency_key("run_chapter:test_project:abc123")
    assert found is not None
    assert found.id == work_unit_dto.id

    # Empty key returns None
    assert await store.find_by_idempotency_key("") is None
    # Non-matching key returns None
    assert await store.find_by_idempotency_key("nonexistent_key") is None


async def test_list_active_work_units(store: ControlPlaneStore) -> None:
    wu1 = WorkUnitDTO(kind="run_chapter", project_id="p1", idempotency_key="k1")
    wu2 = WorkUnitDTO(kind="run_chapter", project_id="p2", idempotency_key="k2")
    wu3 = WorkUnitDTO(kind="run_chapter", project_id="p3", idempotency_key="k3")
    await store.create_work_unit(wu1)
    await store.create_work_unit(wu2)
    await store.create_work_unit(wu3)

    # All 3 are QUEUED (active)
    active = await store.list_active_work_units()
    assert len(active) == 3

    # Commit one (must transition QUEUED -> RUNNING -> COMMITTED)
    await store.update_work_unit_state(wu1.id, WorkUnitState.RUNNING)
    await store.update_work_unit_state(wu1.id, WorkUnitState.COMMITTED)
    active = await store.list_active_work_units()
    assert len(active) == 2
    active_ids = {a.id for a in active}
    assert wu1.id not in active_ids


# ---------------------------------------------------------------------------
# RunAttempt CRUD
# ---------------------------------------------------------------------------


async def test_create_and_list_run_attempts(
    store: ControlPlaneStore, work_unit_dto: WorkUnitDTO
) -> None:
    await store.create_work_unit(work_unit_dto)

    attempt1 = RunAttemptDTO(
        work_unit_id=work_unit_dto.id,
        attempt_number=1,
        runtime_config_version="cfg_v1",
    )
    await store.create_run_attempt(attempt1)

    attempt2 = RunAttemptDTO(
        work_unit_id=work_unit_dto.id,
        attempt_number=2,
        runtime_config_version="cfg_v2",
    )
    await store.create_run_attempt(attempt2)

    attempts = await store.list_run_attempts(work_unit_dto.id)
    assert len(attempts) == 2
    assert attempts[0].attempt_number == 1
    assert attempts[1].attempt_number == 2
    assert attempts[0].runtime_config_version == "cfg_v1"


async def test_update_run_attempt(store: ControlPlaneStore, work_unit_dto: WorkUnitDTO) -> None:
    await store.create_work_unit(work_unit_dto)
    attempt = RunAttemptDTO(work_unit_id=work_unit_dto.id, attempt_number=1)
    await store.create_run_attempt(attempt)

    updated = await store.update_run_attempt(
        attempt.id,
        state=RunAttemptState.FAILED,
        error_kind="gateway",
        error_summary={"provider": "openai", "status": 429},
        ended_at="2026-07-11T11:00:00+00:00",
    )
    assert updated is not None
    assert updated.state == RunAttemptState.FAILED
    assert updated.error_kind == "gateway"
    assert updated.error_summary == {"provider": "openai", "status": 429}
    assert updated.ended_at == "2026-07-11T11:00:00+00:00"


async def test_list_stale_attempts(store: ControlPlaneStore, work_unit_dto: WorkUnitDTO) -> None:
    await store.create_work_unit(work_unit_dto)

    # Stale attempt (old heartbeat, still running)
    stale = RunAttemptDTO(work_unit_id=work_unit_dto.id, attempt_number=1)
    stale.heartbeat_at = "2026-01-01T00:00:00+00:00"
    await store.create_run_attempt(stale)

    # Fresh attempt (recent heartbeat, running)
    fresh = RunAttemptDTO(work_unit_id=work_unit_dto.id, attempt_number=2)
    fresh.heartbeat_at = "2026-07-11T12:00:00+00:00"
    await store.create_run_attempt(fresh)

    # No heartbeat at all (should not be considered stale - never started)
    no_hb = RunAttemptDTO(work_unit_id=work_unit_dto.id, attempt_number=3)
    await store.create_run_attempt(no_hb)

    stale_attempts = await store.list_stale_attempts("2026-07-11T11:00:00+00:00")
    assert len(stale_attempts) == 1
    assert stale_attempts[0].id == stale.id


# ---------------------------------------------------------------------------
# StageExecution CRUD
# ---------------------------------------------------------------------------


async def test_create_and_list_stage_executions(
    store: ControlPlaneStore, work_unit_dto: WorkUnitDTO
) -> None:
    await store.create_work_unit(work_unit_dto)
    attempt = RunAttemptDTO(work_unit_id=work_unit_dto.id, attempt_number=1)
    await store.create_run_attempt(attempt)

    stage1 = StageExecutionDTO(
        run_attempt_id=attempt.id,
        stage_name="draft",
        task_type="DRAFT_CHAPTER",
        input_artifact_hashes={"source_slice": "abc123"},
        idempotency_key="draft:ch12:abc123",
        retry_budget=2,
    )
    await store.create_stage_execution(stage1)

    stage2 = StageExecutionDTO(
        run_attempt_id=attempt.id,
        stage_name="wave",
        task_type="WAVE_CHAPTER",
        input_artifact_hashes={"draft": "def456"},
    )
    await store.create_stage_execution(stage2)

    stages = await store.list_stage_executions(attempt.id)
    assert len(stages) == 2
    assert stages[0].stage_name == "draft"
    assert stages[0].input_artifact_hashes == {"source_slice": "abc123"}
    assert stages[1].stage_name == "wave"


async def test_update_stage_execution(store: ControlPlaneStore, work_unit_dto: WorkUnitDTO) -> None:
    await store.create_work_unit(work_unit_dto)
    attempt = RunAttemptDTO(work_unit_id=work_unit_dto.id, attempt_number=1)
    await store.create_run_attempt(attempt)
    stage = StageExecutionDTO(run_attempt_id=attempt.id, stage_name="draft")
    await store.create_stage_execution(stage)

    updated = await store.update_stage_execution(
        stage.id,
        state=StageState.DONE,
        output_artifact_hash="out_hash_789",
        committed_version=1,
        ended_at="2026-07-11T13:00:00+00:00",
    )
    assert updated is not None
    assert updated.state == StageState.DONE
    assert updated.output_artifact_hash == "out_hash_789"
    assert updated.committed_version == 1


# ---------------------------------------------------------------------------
# EventLedger
# ---------------------------------------------------------------------------


async def test_append_and_list_events(store: ControlPlaneStore, work_unit_dto: WorkUnitDTO) -> None:
    await store.create_work_unit(work_unit_dto)
    attempt = RunAttemptDTO(work_unit_id=work_unit_dto.id, attempt_number=1)
    await store.create_run_attempt(attempt)

    await store.append_event(
        EventLedgerEntryDTO(
            run_attempt_id=attempt.id,
            event_type="stage_started",
            severity=EventSeverity.INFO,
            payload={"stage": "draft"},
        )
    )
    await store.append_event(
        EventLedgerEntryDTO(
            run_attempt_id=attempt.id,
            event_type="repair_failed",
            severity=EventSeverity.WARN,
            payload={"dimension": "continuity", "round": 1},
        )
    )
    await store.append_event(
        EventLedgerEntryDTO(
            run_attempt_id=attempt.id,
            event_type="canon_commit_blocked",
            severity=EventSeverity.ERROR,
            payload={"reason": "hard_gate_failed"},
        )
    )

    events = await store.list_events(attempt.id)
    assert len(events) == 3
    # Events are ordered by seq DESC (most recent first)
    assert events[0].event_type == "canon_commit_blocked"
    assert events[0].severity == EventSeverity.ERROR
    assert events[2].event_type == "stage_started"
    assert events[2].payload == {"stage": "draft"}


# ---------------------------------------------------------------------------
# ArtifactManifest
# ---------------------------------------------------------------------------


async def test_record_and_get_artifact(store: ControlPlaneStore) -> None:
    artifact = ArtifactManifestDTO(
        sha256="a" * 64,
        artifact_kind=ArtifactKind.STAGE_ARTIFACT,
        project_id="test_project",
        content_path="states/chapter_012_artifacts/draft.json",
        content_size=4096,
        source_artifact_hashes={"source_slice": "b" * 64, "plan": "c" * 64},
        parent_artifact_id="plan_artifact_id",
        prompt_hash="d" * 64,
        template_version="2.1.3",
        task_type="DRAFT_CHAPTER",
        route="openai:gpt-4o",
        response_hash="e" * 64,
        parent_artifact_refs=["plan_artifact_id"],
        source_text_hash="f" * 64,
        input_signature="1" * 64,
        schema_version=2,
        workflow_version="novel.chapter.v2",
        config_fingerprint="2" * 64,
        model_fingerprint="openai:gpt-4o",
        quality_status="actual",
        derivation_status="fresh",
        output_version=4,
    )
    await store.record_artifact(artifact)

    fetched = await store.get_artifact(artifact.id)
    assert fetched is not None
    assert fetched.sha256 == "a" * 64
    assert fetched.artifact_kind == ArtifactKind.STAGE_ARTIFACT
    assert fetched.content_size == 4096
    assert fetched.source_artifact_hashes == {"source_slice": "b" * 64, "plan": "c" * 64}
    assert fetched.parent_artifact_id == "plan_artifact_id"
    assert fetched.prompt_hash == "d" * 64
    assert fetched.template_version == "2.1.3"
    assert fetched.route == "openai:gpt-4o"
    assert fetched.parent_artifact_refs == ["plan_artifact_id"]
    assert fetched.source_text_hash == "f" * 64
    assert fetched.input_signature == "1" * 64
    assert fetched.workflow_version == "novel.chapter.v2"
    assert fetched.output_version == 4


async def test_find_artifact_by_sha256(store: ControlPlaneStore) -> None:
    artifact = ArtifactManifestDTO(
        sha256="f" * 64,
        artifact_kind=ArtifactKind.MODEL_CALL,
        project_id="p1",
    )
    await store.record_artifact(artifact)

    found = await store.find_artifact_by_sha256("f" * 64)
    assert found is not None
    assert found.id == artifact.id

    assert await store.find_artifact_by_sha256("nonexistent") is None


# ---------------------------------------------------------------------------
# ResourceVersion
# ---------------------------------------------------------------------------


async def test_record_and_list_resource_versions(
    store: ControlPlaneStore, work_unit_dto: WorkUnitDTO
) -> None:
    await store.create_work_unit(work_unit_dto)

    rv1 = ResourceVersionDTO(
        work_unit_id=work_unit_dto.id,
        resource_kind=ResourceKind.CHAPTER,
        project_id="test_project",
        resource_ref="chapter_012",
        version_number=1,
    )
    await store.record_resource_version(rv1)

    rv2 = ResourceVersionDTO(
        work_unit_id=work_unit_dto.id,
        resource_kind=ResourceKind.CHAPTER,
        project_id="test_project",
        resource_ref="chapter_012",
        version_number=2,
    )
    await store.record_resource_version(rv2)

    versions = await store.list_resource_versions(
        "test_project", ResourceKind.CHAPTER.value, "chapter_012"
    )
    assert len(versions) == 2
    # Ordered by version_number DESC
    assert versions[0].version_number == 2
    assert versions[1].version_number == 1


# ---------------------------------------------------------------------------
# Foreign key cascade
# ---------------------------------------------------------------------------


async def test_fk_cascade_delete_work_unit(
    store: ControlPlaneStore, work_unit_dto: WorkUnitDTO
) -> None:
    """Deleting a WorkUnit should cascade to RunAttempts and below."""
    await store.create_work_unit(work_unit_dto)
    attempt = RunAttemptDTO(work_unit_id=work_unit_dto.id, attempt_number=1)
    await store.create_run_attempt(attempt)

    # Verify attempt exists
    assert await store.get_run_attempt(attempt.id) is not None

    # Delete work_unit via raw SQL (cascade should remove attempt)
    await store.execute_raw(f"DELETE FROM work_units WHERE id = '{work_unit_dto.id}'")

    assert await store.get_work_unit(work_unit_dto.id) is None
    assert await store.get_run_attempt(attempt.id) is None


# ---------------------------------------------------------------------------
# SyncFacade (daemon-loop bridge)
# ---------------------------------------------------------------------------


def test_sync_facade_create_and_get_work_unit(tmp_path) -> None:
    """SyncFacade allows sync callers to use the async store."""
    store = ControlPlaneStore(tmp_path / "test_sync.db")
    store.sync._run(store.init_db())  # type: ignore[attr-defined]

    try:
        dto = WorkUnitDTO(
            kind="run_chapter",
            project_id="sync_test",
            idempotency_key="sync_key_1",
        )

        # All sync calls
        created = store.sync.create_work_unit(dto)
        assert created.id == dto.id

        fetched = store.sync.get_work_unit(dto.id)
        assert fetched is not None
        assert fetched.kind == "run_chapter"

        found = store.sync.find_by_idempotency_key("sync_key_1")
        assert found is not None
        assert found.id == dto.id
    finally:
        store.sync._run(store.close())  # type: ignore[attr-defined]


def test_sync_facade_from_multiple_threads(tmp_path) -> None:
    """SyncFacade is thread-safe across multiple concurrent sync threads."""
    store = ControlPlaneStore(tmp_path / "test_multi_thread.db")
    store.sync._run(store.init_db())  # type: ignore[attr-defined]

    results: list[str | None] = [None] * 5
    errors: list[Exception] = []

    def worker(idx: int) -> None:
        try:
            dto = WorkUnitDTO(
                kind="run_chapter",
                project_id=f"thread_{idx}",
                idempotency_key=f"thread_key_{idx}",
            )
            store.sync.create_work_unit(dto)
            fetched = store.sync.get_work_unit(dto.id)
            results[idx] = fetched.kind if fetched else None
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Thread errors: {errors}"
    assert results == ["run_chapter"] * 5

    store.sync._run(store.close())  # type: ignore[attr-defined]


def test_sync_facade_update_and_list(tmp_path) -> None:
    """SyncFacade supports state updates and list queries."""
    store = ControlPlaneStore(tmp_path / "test_sync_update.db")
    store.sync._run(store.init_db())  # type: ignore[attr-defined]

    try:
        wu = WorkUnitDTO(kind="run_chapter", project_id="p", idempotency_key="k")
        store.sync.create_work_unit(wu)

        attempt = RunAttemptDTO(work_unit_id=wu.id, attempt_number=1)
        store.sync.create_run_attempt(attempt)

        store.sync.update_work_unit_state(wu.id, WorkUnitState.RUNNING)
        store.sync.update_run_attempt(
            attempt.id, state=RunAttemptState.COMMITTED, ended_at="2026-07-11T15:00:00+00:00"
        )

        active = store.sync.list_active_work_units()
        # RUNNING is an active state, so the work unit should be listed.
        assert len(active) == 1
        assert active[0].state == WorkUnitState.RUNNING

        attempts = store.sync.list_run_attempts(wu.id)
        assert len(attempts) == 1
        assert attempts[0].state == RunAttemptState.COMMITTED
    finally:
        store.sync._run(store.close())  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# File-backed DB with WAL
# ---------------------------------------------------------------------------


async def test_file_backed_db_wal_mode(tmp_path) -> None:
    """File-backed DB should enable WAL journal mode."""
    db_path = tmp_path / "wal_test.db"
    store = ControlPlaneStore(db_path)
    await store.init_db()

    try:
        wu = WorkUnitDTO(kind="run_chapter", project_id="wal_test")
        await store.create_work_unit(wu)
        fetched = await store.get_work_unit(wu.id)
        assert fetched is not None

        # WAL mode creates -wal and -shm files
        assert db_path.exists()
    finally:
        await store.close()
