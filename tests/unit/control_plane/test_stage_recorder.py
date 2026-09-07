"""Tests for StageRecorder - stage execution and artifact lineage recording."""

from __future__ import annotations

import pytest

from novel_forge.control_plane.enums import (
    ArtifactKind,
    EventSeverity,
    StageState,
)
from novel_forge.control_plane.schemas import (
    RunAttemptDTO,
    WorkUnitDTO,
)
from novel_forge.control_plane.stage_recorder import (
    StageRecorder,
    _hash_json,
    compute_sha256,
    hash_json,
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
def recorder(store: ControlPlaneStore) -> StageRecorder:
    return StageRecorder(store)


@pytest.fixture
def disabled_recorder() -> StageRecorder:
    return StageRecorder(None)


@pytest.fixture
async def setup_work_unit_and_attempt(store: ControlPlaneStore) -> tuple[str, str]:
    """Create a WorkUnit + RunAttempt and return (work_unit_id, attempt_id)."""
    wu = WorkUnitDTO(kind="run_chapter", project_id="proj1")
    await store.create_work_unit(wu)
    attempt = RunAttemptDTO(work_unit_id=wu.id, attempt_number=1)
    await store.create_run_attempt(attempt)
    return wu.id, attempt.id


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------


def test_compute_sha256_string() -> None:
    h = compute_sha256("hello")
    assert len(h) == 64
    assert h == compute_sha256("hello")  # deterministic


def test_compute_sha256_bytes() -> None:
    h = compute_sha256(b"hello")
    assert h == compute_sha256("hello")


def test_hash_json_deterministic() -> None:
    payload = {"b": 2, "a": 1}
    h1 = hash_json(payload)
    h2 = hash_json({"a": 1, "b": 2})  # different key order
    assert h1 == h2  # sort_keys makes it deterministic


def test_hash_json_differs_on_content() -> None:
    assert hash_json({"a": 1}) != hash_json({"a": 2})


# ---------------------------------------------------------------------------
# Disabled recorder
# ---------------------------------------------------------------------------


def test_disabled_recorder_no_op(disabled_recorder: StageRecorder) -> None:
    assert not disabled_recorder.enabled
    # All should be no-ops
    assert disabled_recorder.begin_stage("ra1", "draft") is None
    disabled_recorder.end_stage(None)
    disabled_recorder.heartbeat(None)
    assert (
        disabled_recorder.record_artifact(
            sha256="x", artifact_kind=ArtifactKind.STAGE_ARTIFACT, project_id="p"
        )
        is None
    )
    disabled_recorder.record_event(event_type="test")


# ---------------------------------------------------------------------------
# Stage lifecycle
# ---------------------------------------------------------------------------


def test_begin_and_end_stage(
    recorder: StageRecorder,
    store: ControlPlaneStore,
    setup_work_unit_and_attempt: tuple[str, str],
) -> None:
    _, attempt_id = setup_work_unit_and_attempt

    stage_id = recorder.begin_stage(
        attempt_id,
        "draft",
        task_type="DRAFT_CHAPTER",
        input_artifact_hashes={"source_slice": "abc123"},
        idempotency_key="draft:ch1:abc123",
        retry_budget=3,
    )
    assert stage_id is not None

    # Verify stage was created
    stage = store.sync.get_stage_execution(stage_id)
    assert stage is not None
    assert stage.stage_name == "draft"
    assert stage.task_type == "DRAFT_CHAPTER"
    assert stage.state == StageState.RUNNING
    assert stage.input_artifact_hashes == {"source_slice": "abc123"}
    assert stage.retry_budget == 3

    # End the stage
    recorder.end_stage(
        stage_id,
        output_artifact_hash="out_hash_456",
        state=StageState.DONE,
        committed_version=1,
    )

    stage = store.sync.get_stage_execution(stage_id)
    assert stage.state == StageState.DONE
    assert stage.output_artifact_hash == "out_hash_456"
    assert stage.committed_version == 1
    assert stage.ended_at != ""


def test_end_stage_failed(
    recorder: StageRecorder,
    store: ControlPlaneStore,
    setup_work_unit_and_attempt: tuple[str, str],
) -> None:
    _, attempt_id = setup_work_unit_and_attempt
    stage_id = recorder.begin_stage(attempt_id, "repair", task_type="REPAIR_CONTINUITY")
    assert stage_id is not None

    recorder.end_stage(stage_id, state=StageState.FAILED, retries_used=2)

    stage = store.sync.get_stage_execution(stage_id)
    assert stage.state == StageState.FAILED
    assert stage.retries_used == 2


def test_heartbeat_updates_timestamp(
    recorder: StageRecorder,
    store: ControlPlaneStore,
    setup_work_unit_and_attempt: tuple[str, str],
) -> None:
    _, attempt_id = setup_work_unit_and_attempt
    stage_id = recorder.begin_stage(attempt_id, "wave")
    assert stage_id is not None

    import time

    time.sleep(0.01)
    recorder.heartbeat(stage_id)

    stage = store.sync.get_stage_execution(stage_id)
    assert stage.heartbeat_at != ""


# ---------------------------------------------------------------------------
# Artifact lineage
# ---------------------------------------------------------------------------


def test_record_artifact(
    recorder: StageRecorder,
    store: ControlPlaneStore,
) -> None:
    artifact_id = recorder.record_artifact(
        sha256="a" * 64,
        artifact_kind=ArtifactKind.STAGE_ARTIFACT,
        project_id="proj1",
        content_path="states/chapter_001_artifacts/draft.json",
        content_size=4096,
        source_artifact_hashes={"source_slice": "b" * 64, "plan": "c" * 64},
        parent_artifact_id="plan:001",
        task_type="DRAFT_CHAPTER",
        route="openai:gpt-4o",
        prompt_hash="d" * 64,
        response_hash="e" * 64,
        template_version="2.1.3",
    )
    assert artifact_id is not None

    fetched = store.sync.get_artifact(artifact_id)
    assert fetched is not None
    assert fetched.sha256 == "a" * 64
    assert fetched.artifact_kind == ArtifactKind.STAGE_ARTIFACT
    assert fetched.source_artifact_hashes == {"source_slice": "b" * 64, "plan": "c" * 64}
    assert fetched.parent_artifact_id == "plan:001"
    assert fetched.prompt_hash == "d" * 64


def test_record_stage_artifact_with_payload(
    recorder: StageRecorder,
    store: ControlPlaneStore,
) -> None:
    """record_stage_artifact computes hash from payload and records lineage."""
    payload = {"text": "Chapter 1 draft text...", "metadata": {"words": 500}}
    source_hashes = {"source_slice": "abc", "plan": "def"}

    artifact_id = recorder.record_stage_artifact(
        project_id="proj1",
        chapter_number=1,
        artifact_type="scene_draft",
        artifact_payload=payload,
        source_hashes=source_hashes,
        previous_artifact_id="plan:001",
        content_path="states/chapter_001_artifacts/scene_draft.json",
    )
    assert artifact_id is not None

    fetched = store.sync.get_artifact(artifact_id)
    assert fetched is not None
    assert fetched.artifact_kind == ArtifactKind.STAGE_ARTIFACT
    assert fetched.sha256 == _hash_json(payload)
    assert fetched.source_artifact_hashes == source_hashes
    assert fetched.parent_artifact_id == "plan:001"


# ---------------------------------------------------------------------------
# Event ledger
# ---------------------------------------------------------------------------


def test_record_event(
    recorder: StageRecorder,
    store: ControlPlaneStore,
    setup_work_unit_and_attempt: tuple[str, str],
) -> None:
    wu_id, attempt_id = setup_work_unit_and_attempt
    stage_id = recorder.begin_stage(attempt_id, "draft")

    recorder.record_event(
        run_attempt_id=attempt_id,
        stage_execution_id=stage_id,
        event_type="stage_started",
        severity=EventSeverity.INFO,
        payload={"stage": "draft", "chapter": 1},
    )

    recorder.record_event(
        run_attempt_id=attempt_id,
        stage_execution_id=stage_id,
        event_type="repair_failed",
        severity=EventSeverity.WARN,
        payload={"dimension": "continuity"},
    )

    events = store.sync.list_events(attempt_id)
    assert len(events) == 2
    # Most recent first (seq DESC)
    assert events[0].event_type == "repair_failed"
    assert events[0].severity == EventSeverity.WARN
    assert events[0].stage_execution_id == stage_id


# ---------------------------------------------------------------------------
# Full stage lineage chain
# ---------------------------------------------------------------------------


def test_full_stage_lineage_chain(
    recorder: StageRecorder,
    store: ControlPlaneStore,
    setup_work_unit_and_attempt: tuple[str, str],
) -> None:
    """Simulate the 6-phase stage artifact chain: bridge->plan->draft->wave->review->final."""
    _, attempt_id = setup_work_unit_and_attempt

    stages = ["bridge", "plan", "scene_draft", "wave", "review", "final"]
    artifact_ids: list[str] = []
    prev_id = ""

    for stage_type in stages:
        stage_id = recorder.begin_stage(attempt_id, stage_type)
        assert stage_id is not None

        payload = {"stage": stage_type, "content": f"data for {stage_type}"}
        source_hashes = {"source_slice": "slice_hash"}
        if prev_id:
            source_hashes["previous"] = prev_id

        artifact_id = recorder.record_stage_artifact(
            project_id="proj1",
            chapter_number=1,
            artifact_type=stage_type,
            artifact_payload=payload,
            source_hashes=source_hashes,
            previous_artifact_id=prev_id,
            stage_execution_id=stage_id,
        )
        assert artifact_id is not None
        artifact_ids.append(artifact_id)

        recorder.end_stage(stage_id, output_artifact_hash=_hash_json(payload))
        prev_id = artifact_id

    # Verify all 6 stages recorded
    stage_executions = store.sync.list_stage_executions(attempt_id)
    assert len(stage_executions) == 6
    assert all(s.state == StageState.DONE for s in stage_executions)

    # Verify all 6 artifacts recorded with lineage chain
    for i, aid in enumerate(artifact_ids):
        artifact = store.sync.get_artifact(aid)
        assert artifact is not None
        assert artifact.artifact_kind == ArtifactKind.STAGE_ARTIFACT
        if i > 0:
            assert artifact.parent_artifact_id == artifact_ids[i - 1]


# ---------------------------------------------------------------------------
# Exception swallowing
# ---------------------------------------------------------------------------


def test_methods_swallow_exceptions() -> None:
    """StageRecorder methods must never raise, even if the store fails."""
    from unittest.mock import MagicMock

    broken_store = MagicMock()
    broken_store.sync.create_stage_execution.side_effect = RuntimeError("DB down")
    broken_store.sync.update_stage_execution.side_effect = RuntimeError("DB down")
    broken_store.sync.record_artifact.side_effect = RuntimeError("DB down")
    broken_store.sync.append_event.side_effect = RuntimeError("DB down")

    recorder = StageRecorder(broken_store)

    # None should raise
    recorder.begin_stage("ra1", "draft")
    recorder.end_stage("fake_id")
    recorder.heartbeat("fake_id")
    recorder.record_artifact(sha256="x", artifact_kind=ArtifactKind.STAGE_ARTIFACT, project_id="p")
    recorder.record_stage_artifact(
        project_id="p",
        chapter_number=1,
        artifact_type="draft",
        artifact_payload={},
        source_hashes={},
    )
    recorder.record_event(event_type="test")
