"""Tests for StartupReconciler - crash recovery via control plane."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from novel_forge.control_plane.enums import (
    AssuranceLevel,
    RunAttemptState,
    StageState,
    WorkUnitState,
)
from novel_forge.control_plane.reconciler import ReconcileResult, StartupReconciler
from novel_forge.control_plane.schemas import (
    ArtifactManifestDTO,
    RunAttemptDTO,
    StageExecutionDTO,
    WorkUnitDTO,
    utc_now_iso,
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


def _old_timestamp(seconds_ago: int = 600) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


async def _create_stale_work_unit(
    store: ControlPlaneStore,
    *,
    work_unit_id: str = "wu_stale",
    project_id: str = "proj1",
    attempt_id: str = "wu_stale_a1",
    heartbeat_seconds_ago: int = 600,
) -> tuple[str, str]:
    """Create a work unit + attempt that appears stale (old heartbeat)."""
    old_hb = _old_timestamp(heartbeat_seconds_ago)
    wu = WorkUnitDTO(
        id=work_unit_id,
        kind="run_chapter",
        project_id=project_id,
        state=WorkUnitState.RUNNING,
        idempotency_key="stale_key",
    )
    await store.create_work_unit(wu)

    attempt = RunAttemptDTO(
        id=attempt_id,
        work_unit_id=work_unit_id,
        attempt_number=1,
        state=RunAttemptState.RUNNING,
        heartbeat_at=old_hb,
    )
    await store.create_run_attempt(attempt)
    return work_unit_id, attempt_id


# ---------------------------------------------------------------------------
# ReconcileResult
# ---------------------------------------------------------------------------


def test_reconcile_result_needs_attention() -> None:
    r = ReconcileResult()
    assert r.needs_attention == 0
    r.retry_wait = 2
    r.waiting_human = 1
    assert r.needs_attention == 3


# ---------------------------------------------------------------------------
# Reconciliation with no active work units
# ---------------------------------------------------------------------------


def test_reconcile_empty(store: ControlPlaneStore) -> None:
    """Reconciliation with no active work units should be a no-op."""
    reconciler = StartupReconciler(store, heartbeat_timeout_s=300)
    result = reconciler.reconcile()
    assert result.total_scanned == 0
    assert result.committed == 0
    assert result.retry_wait == 0
    assert result.waiting_human == 0


# ---------------------------------------------------------------------------
# Stale attempt with valid output -> committed
# ---------------------------------------------------------------------------


def test_reconcile_stale_with_valid_output(store: ControlPlaneStore, tmp_path: Path) -> None:
    """A stale attempt with a valid stage output should be marked committed."""
    wu_id, attempt_id = _create_stale_work_unit_sync(store)

    # Create a stage execution with output
    stage = StageExecutionDTO(
        run_attempt_id=attempt_id,
        stage_name="final",
        task_type="FINALIZE",
        state=StageState.DONE,
        output_artifact_hash="out_hash_123",
    )
    store.sync.create_stage_execution(stage)

    # Create an artifact manifest with a content path that exists
    output_file = tmp_path / "chapter_001.json"
    output_file.write_text('{"chapter": 1}')
    artifact = ArtifactManifestDTO(
        sha256="out_hash_123",
        artifact_kind="stage_artifact",  # type: ignore[arg-type]
        project_id="proj1",
        content_path=str(output_file),
    )
    store.sync.record_artifact(artifact)

    reconciler = StartupReconciler(store, heartbeat_timeout_s=300, storage_root=tmp_path)
    result = reconciler.reconcile()

    assert result.total_scanned == 1
    assert result.committed == 1
    assert result.retry_wait == 0
    assert result.waiting_human == 0

    wu = store.sync.get_work_unit(wu_id)
    assert wu.state == WorkUnitState.COMMITTED


def test_reconcile_stale_with_valid_output_relative_path(
    store: ControlPlaneStore, tmp_path: Path
) -> None:
    """Relative content_path should be resolved from storage_root."""
    wu_id, attempt_id = _create_stale_work_unit_sync(store)

    stage = StageExecutionDTO(
        run_attempt_id=attempt_id,
        stage_name="final",
        task_type="FINALIZE",
        state=StageState.DONE,
        output_artifact_hash="rel_hash",
    )
    store.sync.create_stage_execution(stage)

    # Create file at storage_root / relative_path
    rel_path = "states/chapter_001_artifacts/draft.json"
    full_path = tmp_path / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text('{"draft": true}')

    artifact = ArtifactManifestDTO(
        sha256="rel_hash",
        artifact_kind="stage_artifact",  # type: ignore[arg-type]
        project_id="proj1",
        content_path=rel_path,
    )
    store.sync.record_artifact(artifact)

    reconciler = StartupReconciler(store, heartbeat_timeout_s=300, storage_root=tmp_path)
    result = reconciler.reconcile()

    assert result.committed == 1
    wu = store.sync.get_work_unit(wu_id)
    assert wu.state == WorkUnitState.COMMITTED


# ---------------------------------------------------------------------------
# Stale attempt with missing output -> retry_wait or waiting_human
# ---------------------------------------------------------------------------


def test_reconcile_stale_no_output_no_stages(store: ControlPlaneStore, tmp_path: Path) -> None:
    """A stale attempt with no completed stages should be marked retry_wait."""
    wu_id, attempt_id = _create_stale_work_unit_sync(store)

    reconciler = StartupReconciler(store, heartbeat_timeout_s=300, storage_root=tmp_path)
    result = reconciler.reconcile()

    assert result.total_scanned == 1
    assert result.retry_wait == 1
    assert result.committed == 0
    assert result.waiting_human == 0

    wu = store.sync.get_work_unit(wu_id)
    assert wu.state == WorkUnitState.RETRY_WAIT
    assert wu.assurance == AssuranceLevel.DEGRADED


def test_reconcile_stale_partial_stages_no_output(store: ControlPlaneStore, tmp_path: Path) -> None:
    """A chapter attempt with partial stages but no final output remains retryable."""
    wu_id, attempt_id = _create_stale_work_unit_sync(store)

    # Create a completed stage with no output hash
    stage = StageExecutionDTO(
        run_attempt_id=attempt_id,
        stage_name="draft",
        state=StageState.DONE,
        output_artifact_hash="",  # No output hash
    )
    store.sync.create_stage_execution(stage)

    reconciler = StartupReconciler(store, heartbeat_timeout_s=300, storage_root=tmp_path)
    result = reconciler.reconcile()

    assert result.waiting_human == 0
    assert result.committed == 0
    assert result.retry_wait == 1

    wu = store.sync.get_work_unit(wu_id)
    assert wu.state == WorkUnitState.RETRY_WAIT


def test_reconcile_stale_output_file_missing(store: ControlPlaneStore, tmp_path: Path) -> None:
    """Stage has output hash but the file is gone -> retry_wait."""
    wu_id, attempt_id = _create_stale_work_unit_sync(store)

    stage = StageExecutionDTO(
        run_attempt_id=attempt_id,
        stage_name="wave",
        state=StageState.DONE,
        output_artifact_hash="gone_hash",
    )
    store.sync.create_stage_execution(stage)

    # Artifact manifest points to a file that doesn't exist
    artifact = ArtifactManifestDTO(
        sha256="gone_hash",
        artifact_kind="stage_artifact",  # type: ignore[arg-type]
        project_id="proj1",
        content_path=str(tmp_path / "nonexistent.json"),
    )
    store.sync.record_artifact(artifact)

    reconciler = StartupReconciler(store, heartbeat_timeout_s=300, storage_root=tmp_path)
    result = reconciler.reconcile()

    assert result.retry_wait == 1
    assert result.committed == 0


# ---------------------------------------------------------------------------
# Non-stale (recent heartbeat) -> skip
# ---------------------------------------------------------------------------


def test_reconcile_recent_heartbeat_skipped(store: ControlPlaneStore, tmp_path: Path) -> None:
    """A work unit with a recent heartbeat should not be reconciled."""
    wu = WorkUnitDTO(
        id="wu_recent",
        kind="run_chapter",
        project_id="proj1",
        state=WorkUnitState.RUNNING,
    )
    store.sync.create_work_unit(wu)

    attempt = RunAttemptDTO(
        id="wu_recent_a1",
        work_unit_id="wu_recent",
        attempt_number=1,
        state=RunAttemptState.RUNNING,
        heartbeat_at=utc_now_iso(),  # Recent
    )
    store.sync.create_run_attempt(attempt)

    reconciler = StartupReconciler(store, heartbeat_timeout_s=300, storage_root=tmp_path)
    result = reconciler.reconcile()

    assert result.total_scanned == 1
    assert result.committed == 0
    assert result.retry_wait == 0
    assert result.waiting_human == 0
    assert result.already_terminal == 0


# ---------------------------------------------------------------------------
# Already terminal work unit -> state sync
# ---------------------------------------------------------------------------


def test_reconcile_terminal_attempt_state_sync(store: ControlPlaneStore, tmp_path: Path) -> None:
    """If the attempt is terminal but work unit isn't, sync the state."""
    wu = WorkUnitDTO(
        id="wu_sync",
        kind="run_chapter",
        project_id="proj1",
        state=WorkUnitState.RUNNING,  # Work unit says running
    )
    store.sync.create_work_unit(wu)

    attempt = RunAttemptDTO(
        id="wu_sync_a1",
        work_unit_id="wu_sync",
        attempt_number=1,
        state=RunAttemptState.COMMITTED,  # But attempt is committed
        heartbeat_at=_old_timestamp(600),
    )
    store.sync.create_run_attempt(attempt)

    reconciler = StartupReconciler(store, heartbeat_timeout_s=300, storage_root=tmp_path)
    result = reconciler.reconcile()

    assert result.already_terminal == 1
    wu_after = store.sync.get_work_unit("wu_sync")
    assert wu_after.state == WorkUnitState.COMMITTED


# ---------------------------------------------------------------------------
# No heartbeat at all
# ---------------------------------------------------------------------------


def test_reconcile_no_heartbeat(store: ControlPlaneStore, tmp_path: Path) -> None:
    """An attempt with no heartbeat should be marked retry_wait."""
    wu = WorkUnitDTO(
        id="wu_nohb",
        kind="run_chapter",
        project_id="proj1",
        state=WorkUnitState.RUNNING,
    )
    store.sync.create_work_unit(wu)

    attempt = RunAttemptDTO(
        id="wu_nohb_a1",
        work_unit_id="wu_nohb",
        attempt_number=1,
        state=RunAttemptState.RUNNING,
        heartbeat_at="",  # No heartbeat
    )
    store.sync.create_run_attempt(attempt)

    reconciler = StartupReconciler(store, heartbeat_timeout_s=300, storage_root=tmp_path)
    result = reconciler.reconcile()

    assert result.retry_wait == 1


# ---------------------------------------------------------------------------
# Multiple work units
# ---------------------------------------------------------------------------


def test_reconcile_multiple_work_units(store: ControlPlaneStore, tmp_path: Path) -> None:
    """Reconcile multiple work units with different states."""
    # WU1: stale, no output -> retry_wait
    _create_stale_work_unit_sync(store, work_unit_id="wu1", attempt_id="wu1_a1")

    # WU2: stale, valid output -> committed
    _create_stale_work_unit_sync(store, work_unit_id="wu2", attempt_id="wu2_a1")
    stage = StageExecutionDTO(
        run_attempt_id="wu2_a1",
        stage_name="final",
        state=StageState.DONE,
        output_artifact_hash="wu2_hash",
    )
    store.sync.create_stage_execution(stage)
    output_file = tmp_path / "wu2_out.json"
    output_file.write_text("{}")
    artifact = ArtifactManifestDTO(
        sha256="wu2_hash",
        artifact_kind="stage_artifact",  # type: ignore[arg-type]
        project_id="proj1",
        content_path=str(output_file),
    )
    store.sync.record_artifact(artifact)

    # WU3: recent heartbeat -> skip
    wu3 = WorkUnitDTO(id="wu3", kind="run_chapter", project_id="proj1", state=WorkUnitState.RUNNING)
    store.sync.create_work_unit(wu3)
    attempt3 = RunAttemptDTO(
        id="wu3_a1",
        work_unit_id="wu3",
        attempt_number=1,
        state=RunAttemptState.RUNNING,
        heartbeat_at=utc_now_iso(),
    )
    store.sync.create_run_attempt(attempt3)

    reconciler = StartupReconciler(store, heartbeat_timeout_s=300, storage_root=tmp_path)
    result = reconciler.reconcile()

    assert result.total_scanned == 3
    assert result.committed == 1
    assert result.retry_wait == 1
    assert result.already_terminal == 0  # wu3 was skipped (not terminal)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _create_stale_work_unit_sync(
    store: ControlPlaneStore,
    *,
    work_unit_id: str = "wu_stale",
    project_id: str = "proj1",
    attempt_id: str = "wu_stale_a1",
    heartbeat_seconds_ago: int = 600,
) -> tuple[str, str]:
    """Sync version of _create_stale_work_unit for sync tests."""
    old_hb = _old_timestamp(heartbeat_seconds_ago)
    wu = WorkUnitDTO(
        id=work_unit_id,
        kind="run_chapter",
        project_id=project_id,
        state=WorkUnitState.RUNNING,
        idempotency_key=f"{work_unit_id}_key",
    )
    store.sync.create_work_unit(wu)

    attempt = RunAttemptDTO(
        id=attempt_id,
        work_unit_id=work_unit_id,
        attempt_number=1,
        state=RunAttemptState.RUNNING,
        heartbeat_at=old_hb,
    )
    store.sync.create_run_attempt(attempt)
    return work_unit_id, attempt_id
