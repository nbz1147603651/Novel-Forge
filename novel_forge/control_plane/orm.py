"""Runtime Control Plane ORM models.

Six tables backing the control plane ledger:

- ``work_units``       : user intent (e.g. "generate chapter 12", "synthesize ch12 audio")
- ``run_attempts``     : one actual execution attempt of a WorkUnit
- ``stage_executions`` : per-stage execution record with input/output hashes
- ``event_ledger``     : append-only immutable event stream
- ``artifact_manifest``: immutable content-addressed artifact metadata
- ``resource_versions``: committed version of a project resource

Content (prose, JSON, audio) is NOT stored here - only metadata, hashes,
and paths. The filesystem remains the content store, content-addressed by
SHA-256.
"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class ControlPlaneBase(DeclarativeBase):
    """Shared declarative base for all control-plane ORM models."""

    pass


class WorkUnitORM(ControlPlaneBase):
    """A user-intent work unit (e.g. "generate chapter 12")."""

    __tablename__ = "work_units"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(256), nullable=False, default="", index=True)
    priority: Mapped[str] = mapped_column(String(8), nullable=False, default="p1")
    intent_payload_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    assurance: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    idempotency_key: Mapped[str] = mapped_column(
        String(256), nullable=False, default="", index=True
    )
    label: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)
    heartbeat_at: Mapped[str] = mapped_column(String(40), nullable=False, default="")

    def __repr__(self) -> str:
        return f"<WorkUnitORM id={self.id!r} kind={self.kind!r} state={self.state!r}>"


class RunAttemptORM(ControlPlaneBase):
    """One actual execution attempt of a WorkUnit."""

    __tablename__ = "run_attempts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    work_unit_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("work_units.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    runtime_config_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="running", index=True)
    error_kind: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    error_summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    started_at: Mapped[str] = mapped_column(String(40), nullable=False)
    ended_at: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    heartbeat_at: Mapped[str] = mapped_column(String(40), nullable=False, default="")

    def __repr__(self) -> str:
        return (
            f"<RunAttemptORM id={self.id!r} work_unit={self.work_unit_id!r} #{self.attempt_number}>"
        )


class StageExecutionORM(ControlPlaneBase):
    """Per-stage execution record with input/output hash lineage."""

    __tablename__ = "stage_executions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_attempt_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("run_attempts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    input_artifact_hashes_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    idempotency_key: Mapped[str] = mapped_column(
        String(256), nullable=False, default="", index=True
    )
    retry_budget: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    retries_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_artifact_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    committed_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="running", index=True)
    heartbeat_at: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    started_at: Mapped[str] = mapped_column(String(40), nullable=False)
    ended_at: Mapped[str] = mapped_column(String(40), nullable=False, default="")

    def __repr__(self) -> str:
        return f"<StageExecutionORM id={self.id!r} stage={self.stage_name!r} state={self.state!r}>"


class EventLedgerORM(ControlPlaneBase):
    """Append-only immutable event stream for auditing and reconciliation."""

    __tablename__ = "event_ledger"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    run_attempt_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("run_attempts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    stage_execution_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("stage_executions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, index=True)

    def __repr__(self) -> str:
        return (
            f"<EventLedgerORM seq={self.seq} type={self.event_type!r} severity={self.severity!r}>"
        )


class ArtifactManifestORM(ControlPlaneBase):
    """Immutable content-addressed artifact metadata (content lives on filesystem)."""

    __tablename__ = "artifact_manifest"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    artifact_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(256), nullable=False, default="", index=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="chapter")
    content_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    source_artifact_hashes_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    parent_artifact_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    template_version: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    route: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    model_params_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    response_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    validation_result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    created_by_stage_execution_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("stage_executions.id", ondelete="SET NULL"), nullable=True
    )

    def __repr__(self) -> str:
        return f"<ArtifactManifestORM id={self.id!r} kind={self.artifact_kind!r} sha256={self.sha256[:12]}...>"


class ResourceVersionORM(ControlPlaneBase):
    """A committed version of a project resource (chapter, canon, report, audio, voice)."""

    __tablename__ = "resource_versions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    work_unit_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("work_units.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resource_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(256), nullable=False, default="", index=True)
    resource_ref: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    committed_artifact_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("artifact_manifest.id", ondelete="SET NULL"), nullable=True
    )
    committed_at: Mapped[str] = mapped_column(String(40), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<ResourceVersionORM kind={self.resource_kind!r} "
            f"ref={self.resource_ref!r} v{self.version_number}>"
        )


# Convenience export for create_all
ALL_MODELS = (
    WorkUnitORM,
    RunAttemptORM,
    StageExecutionORM,
    EventLedgerORM,
    ArtifactManifestORM,
    ResourceVersionORM,
)

# Unused import suppression: Float kept for potential future cost columns.
_ = Float
