"""Tests for control-plane Pydantic DTOs - serialization round-trips."""

from __future__ import annotations

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
    utc_now_iso,
)


def test_work_unit_dto_defaults() -> None:
    dto = WorkUnitDTO(kind="run_chapter")
    assert dto.id  # auto-generated
    assert dto.priority == Priority.P1
    assert dto.state == WorkUnitState.QUEUED
    assert dto.assurance == AssuranceLevel.NORMAL
    assert dto.created_at  # auto-generated
    # created_at and updated_at use independent default_factory calls,
    # so they may differ by microseconds - just check both are set.
    assert dto.updated_at


def test_work_unit_dto_round_trip() -> None:
    dto = WorkUnitDTO(
        kind="init_long",
        project_id="proj1",
        priority=Priority.P0,
        idempotency_key="init_long:proj1:hash",
        label="Initialize long-form project",
    )
    data = dto.model_dump(mode="json")
    restored = WorkUnitDTO.model_validate(data)

    assert restored.kind == "init_long"
    assert restored.project_id == "proj1"
    assert restored.priority == Priority.P0
    assert restored.state == WorkUnitState.QUEUED
    assert restored.idempotency_key == "init_long:proj1:hash"


def test_run_attempt_dto_round_trip() -> None:
    dto = RunAttemptDTO(
        work_unit_id="wu1",
        attempt_number=3,
        runtime_config_version="cfg_v3",
        state=RunAttemptState.FAILED,
        error_kind="gateway",
        error_summary={"provider": "deepseek", "error": "rate_limit"},
    )
    data = dto.model_dump(mode="json")
    restored = RunAttemptDTO.model_validate(data)

    assert restored.work_unit_id == "wu1"
    assert restored.attempt_number == 3
    assert restored.state == RunAttemptState.FAILED
    assert restored.error_kind == "gateway"
    assert restored.error_summary == {"provider": "deepseek", "error": "rate_limit"}


def test_stage_execution_dto_round_trip() -> None:
    dto = StageExecutionDTO(
        run_attempt_id="ra1",
        stage_name="draft",
        task_type="DRAFT_CHAPTER",
        input_artifact_hashes={"source_slice": "hash1", "plan": "hash2"},
        idempotency_key="draft:ch1:hash1",
        retry_budget=3,
        output_artifact_hash="out_hash",
        committed_version=2,
        state=StageState.DONE,
    )
    data = dto.model_dump(mode="json")
    restored = StageExecutionDTO.model_validate(data)

    assert restored.run_attempt_id == "ra1"
    assert restored.stage_name == "draft"
    assert restored.input_artifact_hashes == {"source_slice": "hash1", "plan": "hash2"}
    assert restored.retry_budget == 3
    assert restored.output_artifact_hash == "out_hash"
    assert restored.state == StageState.DONE


def test_event_ledger_entry_dto_round_trip() -> None:
    dto = EventLedgerEntryDTO(
        run_attempt_id="ra1",
        event_type="repair_failed",
        severity=EventSeverity.WARN,
        payload={"dimension": "continuity", "round": 2, "error": "stale_hash"},
    )
    data = dto.model_dump(mode="json")
    restored = EventLedgerEntryDTO.model_validate(data)

    assert restored.event_type == "repair_failed"
    assert restored.severity == EventSeverity.WARN
    assert restored.payload == {"dimension": "continuity", "round": 2, "error": "stale_hash"}


def test_event_ledger_nullable_relations() -> None:
    """EventLedgerEntry allows null run_attempt_id and stage_execution_id."""
    dto = EventLedgerEntryDTO(
        run_attempt_id=None,
        stage_execution_id=None,
        event_type="system_shutdown",
    )
    assert dto.run_attempt_id is None
    assert dto.stage_execution_id is None


def test_artifact_manifest_dto_round_trip() -> None:
    dto = ArtifactManifestDTO(
        sha256="a" * 64,
        artifact_kind=ArtifactKind.MODEL_CALL,
        project_id="proj1",
        content_path="logs/run1/model_calls/001.json",
        content_size=2048,
        source_artifact_hashes={"prompt": "b" * 64},
        prompt_hash="c" * 64,
        template_version="2.1.3",
        task_type="DRAFT_CHAPTER",
        route="openai:gpt-4o",
        model_params={"temperature": 0.8, "max_tokens": 4096},
        response_hash="d" * 64,
        validation_result={"valid": True, "keys_present": True},
        parent_artifact_refs=["plan:012"],
        source_text_hash="e" * 64,
        input_signature="f" * 64,
        schema_version=2,
        workflow_version="novel.chapter.v2",
        config_fingerprint="1" * 64,
        model_fingerprint="openai:gpt-4o",
        quality_status="degraded",
        degradation_reason="local_fallback",
        derivation_status="fresh",
        output_version=3,
    )
    data = dto.model_dump(mode="json")
    restored = ArtifactManifestDTO.model_validate(data)

    assert restored.sha256 == "a" * 64
    assert restored.artifact_kind == ArtifactKind.MODEL_CALL
    assert restored.source_artifact_hashes == {"prompt": "b" * 64}
    assert restored.model_params == {"temperature": 0.8, "max_tokens": 4096}
    assert restored.validation_result == {"valid": True, "keys_present": True}
    assert restored.parent_artifact_refs == ["plan:012"]
    assert restored.input_signature == "f" * 64
    assert restored.quality_status == "degraded"
    assert restored.output_version == 3


def test_resource_version_dto_round_trip() -> None:
    dto = ResourceVersionDTO(
        work_unit_id="wu1",
        resource_kind=ResourceKind.CANON,
        project_id="proj1",
        resource_ref="story_kernel.db",
        version_number=5,
        committed_artifact_id="art1",
    )
    data = dto.model_dump(mode="json")
    restored = ResourceVersionDTO.model_validate(data)

    assert restored.resource_kind == ResourceKind.CANON
    assert restored.resource_ref == "story_kernel.db"
    assert restored.version_number == 5
    assert restored.committed_artifact_id == "art1"


def test_resource_version_nullable_artifact() -> None:
    dto = ResourceVersionDTO(
        work_unit_id="wu1",
        resource_kind=ResourceKind.CHAPTER,
        committed_artifact_id=None,
    )
    assert dto.committed_artifact_id is None


def test_utc_now_iso_returns_valid_iso() -> None:
    ts = utc_now_iso()
    assert "T" in ts
    assert ts.endswith("+00:00") or ts.endswith("Z")


# ---------------------------------------------------------------------------
# Enum property tests
# ---------------------------------------------------------------------------


def test_work_unit_state_terminal_and_active() -> None:
    assert WorkUnitState.COMMITTED.is_terminal
    assert WorkUnitState.CANCELLED.is_terminal
    assert WorkUnitState.FAILED.is_terminal
    assert not WorkUnitState.RUNNING.is_terminal
    assert not WorkUnitState.QUEUED.is_terminal

    # is_active includes all non-terminal states (matches list_active_work_units)
    assert WorkUnitState.QUEUED.is_active
    assert WorkUnitState.RUNNING.is_active
    assert WorkUnitState.RETRY_WAIT.is_active
    assert WorkUnitState.WAITING_HUMAN.is_active
    assert not WorkUnitState.COMMITTED.is_active
    assert not WorkUnitState.FAILED.is_active
    assert not WorkUnitState.CANCELLED.is_active


def test_work_unit_state_valid_transitions() -> None:
    # Valid transitions
    assert WorkUnitState.QUEUED.can_transition_to(WorkUnitState.RUNNING)
    assert WorkUnitState.QUEUED.can_transition_to(WorkUnitState.CANCELLED)
    assert WorkUnitState.RUNNING.can_transition_to(WorkUnitState.COMMITTED)
    assert WorkUnitState.RUNNING.can_transition_to(WorkUnitState.FAILED)
    assert WorkUnitState.RUNNING.can_transition_to(WorkUnitState.RETRY_WAIT)
    assert WorkUnitState.RETRY_WAIT.can_transition_to(WorkUnitState.QUEUED)
    assert WorkUnitState.WAITING_HUMAN.can_transition_to(WorkUnitState.QUEUED)
    assert WorkUnitState.WAITING_HUMAN.can_transition_to(WorkUnitState.COMMITTED)

    # Invalid transitions
    assert not WorkUnitState.QUEUED.can_transition_to(WorkUnitState.COMMITTED)
    assert not WorkUnitState.RUNNING.can_transition_to(WorkUnitState.QUEUED)
    assert not WorkUnitState.COMMITTED.can_transition_to(WorkUnitState.RUNNING)
    assert not WorkUnitState.FAILED.can_transition_to(WorkUnitState.QUEUED)
    assert not WorkUnitState.CANCELLED.can_transition_to(WorkUnitState.RUNNING)


def test_priority_rank() -> None:
    assert Priority.P0.rank < Priority.P1.rank < Priority.P2.rank < Priority.P3.rank
    assert Priority.P0.rank == 0
    assert Priority.P3.rank == 3


def test_run_attempt_state_terminal() -> None:
    assert RunAttemptState.COMMITTED.is_terminal
    assert RunAttemptState.FAILED.is_terminal
    assert RunAttemptState.CANCELLED.is_terminal
    assert not RunAttemptState.RUNNING.is_terminal
