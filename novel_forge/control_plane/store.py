"""ControlPlaneStore - async SQLAlchemy 2.0 store with sync bridge.

The control plane must be accessible from:
- async contexts (FastAPI request handlers, pipeline stages)
- sync threads (JobService ``_JobWorker`` daemon threads, Desktop Qt threads)

To support both without forcing callers to manage event loops, the store
holds a dedicated daemon asyncio loop. Sync methods delegate to it via
``asyncio.run_coroutine_threadsafe``. Async methods run on the caller's
own loop (FastAPI / pipeline).

Pattern mirrors :class:`novel_forge.story_kernel.store.StoryKernelStore`:
AsyncEngine + async_sessionmaker, WAL journal mode, foreign keys ON.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import threading
from concurrent.futures import Future
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Coroutine, TypeVar

from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from novel_forge.control_plane.enums import (
    AssuranceLevel,
    Priority,
    RunAttemptState,
    StageState,
    WorkUnitState,
)
from novel_forge.control_plane.orm import (
    ArtifactManifestORM,
    ControlPlaneBase,
    EventLedgerORM,
    ResourceVersionORM,
    RunAttemptORM,
    StageExecutionORM,
    WorkUnitORM,
)
from novel_forge.control_plane.schemas import (
    ArtifactManifestDTO,
    EventLedgerEntryDTO,
    ResourceVersionDTO,
    RunAttemptDTO,
    StageExecutionDTO,
    WorkUnitDTO,
)

_log = logging.getLogger("novel_forge.control_plane.store")

_T = TypeVar("_T")


# ---------------------------------------------------------------------------
# Helpers (mirrors story_kernel/store.py)
# ---------------------------------------------------------------------------


def _sqlite_url_from_path(database_path: str | Path) -> str:
    raw = str(database_path or "").strip()
    if not raw:
        raise ValueError("Control plane database path must not be empty.")
    return f"sqlite+aiosqlite:///{Path(raw).expanduser()}"


def _ensure_sqlite_parent_dir(database_path: str | Path) -> None:
    Path(database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# ORM <-> DTO conversion
# ---------------------------------------------------------------------------


def _work_unit_from_orm(orm: WorkUnitORM) -> WorkUnitDTO:
    return WorkUnitDTO(
        id=orm.id,
        kind=orm.kind,
        project_id=orm.project_id,
        priority=Priority(orm.priority),
        intent_payload_path=orm.intent_payload_path,
        state=WorkUnitState(orm.state),
        assurance=AssuranceLevel(orm.assurance),
        idempotency_key=orm.idempotency_key,
        label=orm.label,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
        heartbeat_at=orm.heartbeat_at,
    )


def _work_unit_to_orm(dto: WorkUnitDTO, orm: WorkUnitORM | None = None) -> WorkUnitORM:
    target = orm or WorkUnitORM()
    target.id = dto.id
    target.kind = dto.kind
    target.project_id = dto.project_id
    target.priority = dto.priority.value
    target.intent_payload_path = dto.intent_payload_path
    target.state = dto.state.value
    target.assurance = dto.assurance.value
    target.idempotency_key = dto.idempotency_key
    target.label = dto.label
    target.created_at = dto.created_at
    target.updated_at = dto.updated_at
    target.heartbeat_at = dto.heartbeat_at
    return target


def _run_attempt_from_orm(orm: RunAttemptORM) -> RunAttemptDTO:
    return RunAttemptDTO(
        id=orm.id,
        work_unit_id=orm.work_unit_id,
        attempt_number=orm.attempt_number,
        runtime_config_version=orm.runtime_config_version,
        state=RunAttemptState(orm.state),
        error_kind=orm.error_kind,
        error_summary=_json.loads(orm.error_summary_json) if orm.error_summary_json else {},
        started_at=orm.started_at,
        ended_at=orm.ended_at,
        heartbeat_at=orm.heartbeat_at,
    )


def _run_attempt_to_orm(dto: RunAttemptDTO, orm: RunAttemptORM | None = None) -> RunAttemptORM:
    target = orm or RunAttemptORM()
    target.id = dto.id
    target.work_unit_id = dto.work_unit_id
    target.attempt_number = dto.attempt_number
    target.runtime_config_version = dto.runtime_config_version
    target.state = dto.state.value
    target.error_kind = dto.error_kind
    target.error_summary_json = _json.dumps(dto.error_summary, ensure_ascii=False, default=str)
    target.started_at = dto.started_at
    target.ended_at = dto.ended_at
    target.heartbeat_at = dto.heartbeat_at
    return target


def _stage_from_orm(orm: StageExecutionORM) -> StageExecutionDTO:
    return StageExecutionDTO(
        id=orm.id,
        run_attempt_id=orm.run_attempt_id,
        stage_name=orm.stage_name,
        task_type=orm.task_type,
        input_artifact_hashes=(
            _json.loads(orm.input_artifact_hashes_json) if orm.input_artifact_hashes_json else {}
        ),
        idempotency_key=orm.idempotency_key,
        retry_budget=orm.retry_budget,
        retries_used=orm.retries_used,
        output_artifact_hash=orm.output_artifact_hash,
        committed_version=orm.committed_version,
        state=StageState(orm.state),
        heartbeat_at=orm.heartbeat_at,
        started_at=orm.started_at,
        ended_at=orm.ended_at,
    )


def _stage_to_orm(
    dto: StageExecutionDTO, orm: StageExecutionORM | None = None
) -> StageExecutionORM:
    target = orm or StageExecutionORM()
    target.id = dto.id
    target.run_attempt_id = dto.run_attempt_id
    target.stage_name = dto.stage_name
    target.task_type = dto.task_type
    target.input_artifact_hashes_json = _json.dumps(
        dto.input_artifact_hashes, ensure_ascii=False, default=str
    )
    target.idempotency_key = dto.idempotency_key
    target.retry_budget = dto.retry_budget
    target.retries_used = dto.retries_used
    target.output_artifact_hash = dto.output_artifact_hash
    target.committed_version = dto.committed_version
    target.state = dto.state.value
    target.heartbeat_at = dto.heartbeat_at
    target.started_at = dto.started_at
    target.ended_at = dto.ended_at
    return target


def _event_from_orm(orm: EventLedgerORM) -> EventLedgerEntryDTO:
    return EventLedgerEntryDTO(
        id=orm.id,
        run_attempt_id=orm.run_attempt_id,
        stage_execution_id=orm.stage_execution_id,
        event_type=orm.event_type,
        severity=orm.severity,
        payload=_json.loads(orm.payload_json) if orm.payload_json else {},
        created_at=orm.created_at,
    )


def _artifact_from_orm(orm: ArtifactManifestORM) -> ArtifactManifestDTO:
    validation_result = (
        _json.loads(orm.validation_result_json) if orm.validation_result_json else {}
    )
    lineage = validation_result.pop("_lineage_v2", {})
    if not isinstance(lineage, dict):
        lineage = {}
    return ArtifactManifestDTO(
        id=orm.id,
        sha256=orm.sha256,
        artifact_kind=orm.artifact_kind,
        project_id=orm.project_id,
        scope=orm.scope,
        content_path=orm.content_path,
        content_size=orm.content_size,
        mime_type=orm.mime_type,
        source_artifact_hashes=(
            _json.loads(orm.source_artifact_hashes_json) if orm.source_artifact_hashes_json else {}
        ),
        parent_artifact_id=orm.parent_artifact_id,
        parent_artifact_refs=list(lineage.get("parent_artifact_refs") or []),
        source_text_hash=str(lineage.get("source_text_hash") or ""),
        input_signature=str(lineage.get("input_signature") or "legacy_unknown"),
        schema_version=int(lineage.get("schema_version") or 1),
        workflow_version=str(lineage.get("workflow_version") or "legacy_unknown"),
        config_fingerprint=str(lineage.get("config_fingerprint") or "legacy_unknown"),
        model_fingerprint=str(lineage.get("model_fingerprint") or "legacy_unknown"),
        quality_status=str(lineage.get("quality_status") or "legacy_unknown"),
        degradation_reason=str(lineage.get("degradation_reason") or ""),
        derivation_status=str(lineage.get("derivation_status") or "legacy_unknown"),
        output_version=int(lineage.get("output_version") or 0),
        prompt_hash=orm.prompt_hash,
        template_version=orm.template_version,
        task_type=orm.task_type,
        route=orm.route,
        model_params=_json.loads(orm.model_params_json) if orm.model_params_json else {},
        response_hash=orm.response_hash,
        validation_result=validation_result,
        created_at=orm.created_at,
        created_by_stage_execution_id=orm.created_by_stage_execution_id or None,
    )


def _artifact_to_orm(
    dto: ArtifactManifestDTO, orm: ArtifactManifestORM | None = None
) -> ArtifactManifestORM:
    target = orm or ArtifactManifestORM()
    target.id = dto.id
    target.sha256 = dto.sha256
    target.artifact_kind = dto.artifact_kind.value
    target.project_id = dto.project_id
    target.scope = dto.scope
    target.content_path = dto.content_path
    target.content_size = dto.content_size
    target.mime_type = dto.mime_type
    target.source_artifact_hashes_json = _json.dumps(
        dto.source_artifact_hashes, ensure_ascii=False, default=str
    )
    target.parent_artifact_id = dto.parent_artifact_id
    target.prompt_hash = dto.prompt_hash
    target.template_version = dto.template_version
    target.task_type = dto.task_type
    target.route = dto.route
    target.model_params_json = _json.dumps(dto.model_params, ensure_ascii=False, default=str)
    target.response_hash = dto.response_hash
    validation_result = dict(dto.validation_result)
    validation_result["_lineage_v2"] = {
        "parent_artifact_refs": list(dto.parent_artifact_refs),
        "source_text_hash": dto.source_text_hash,
        "input_signature": dto.input_signature,
        "schema_version": dto.schema_version,
        "workflow_version": dto.workflow_version,
        "config_fingerprint": dto.config_fingerprint,
        "model_fingerprint": dto.model_fingerprint,
        "quality_status": dto.quality_status,
        "degradation_reason": dto.degradation_reason,
        "derivation_status": dto.derivation_status,
        "output_version": dto.output_version,
    }
    target.validation_result_json = _json.dumps(validation_result, ensure_ascii=False, default=str)
    target.created_at = dto.created_at
    # NULL (not "") so the FK constraint is satisfied when no stage is linked
    target.created_by_stage_execution_id = dto.created_by_stage_execution_id or None  # type: ignore[assignment]
    return target


def _resource_from_orm(orm: ResourceVersionORM) -> ResourceVersionDTO:
    return ResourceVersionDTO(
        id=orm.id,
        work_unit_id=orm.work_unit_id,
        resource_kind=orm.resource_kind,
        project_id=orm.project_id,
        resource_ref=orm.resource_ref,
        version_number=orm.version_number,
        committed_artifact_id=orm.committed_artifact_id or None,
        committed_at=orm.committed_at,
    )


def _resource_to_orm(
    dto: ResourceVersionDTO, orm: ResourceVersionORM | None = None
) -> ResourceVersionORM:
    target = orm or ResourceVersionORM()
    target.id = dto.id
    target.work_unit_id = dto.work_unit_id
    target.resource_kind = dto.resource_kind.value
    target.project_id = dto.project_id
    target.resource_ref = dto.resource_ref
    target.version_number = dto.version_number
    # NULL (not "") so the FK constraint is satisfied when no artifact is linked
    target.committed_artifact_id = dto.committed_artifact_id or None  # type: ignore[assignment]
    target.committed_at = dto.committed_at
    return target


# ---------------------------------------------------------------------------
# ControlPlaneStore (async)
# ---------------------------------------------------------------------------


class ControlPlaneStore:
    """Async SQLAlchemy 2.0 store for the runtime control plane.

    Uses a dedicated daemon event loop so that sync callers (JobService
    worker threads, Desktop Qt threads) can safely invoke the :class:`SyncFacade`
    without colliding with the caller's own loop or the API singleton's loop.
    """

    _db_path: str
    _db_url: str
    _engine: AsyncEngine
    _session_factory: async_sessionmaker[AsyncSession]

    def __init__(
        self,
        db_path: str | Path,
        *,
        echo: bool = False,
        wal_mode: bool = True,
    ) -> None:
        self._db_path = str(Path(str(db_path)).expanduser())
        self._db_url = _sqlite_url_from_path(self._db_path)
        if self._db_path != ":memory:":
            _ensure_sqlite_parent_dir(self._db_path)
        self._configure_engine(echo=echo, wal_mode=wal_mode)
        self._sync_facade: SyncFacade | None = None
        self._sync_lock = threading.Lock()

    @classmethod
    def in_memory(cls, *, echo: bool = False) -> "ControlPlaneStore":
        """Create an ephemeral in-memory store for tests."""
        store = cls.__new__(cls)
        store._db_path = ":memory:"
        store._db_url = "sqlite+aiosqlite://"
        store._configure_engine(echo=echo, wal_mode=False)
        store._sync_facade = None
        store._sync_lock = threading.Lock()
        return store

    def _configure_engine(self, *, echo: bool, wal_mode: bool) -> None:
        self._engine: AsyncEngine = create_async_engine(self._db_url, echo=echo)

        @event.listens_for(self._engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            if wal_mode and self._db_path != ":memory:":
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        """Yield an auto-committing / auto-rolling-back session."""
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def init_db(self) -> None:
        """Create all tables if they do not exist.

        Also installs a partial UNIQUE index on ``work_units.idempotency_key``
        restricted to ACTIVE states (queued/running/retry_wait/waiting_human).
        This is the storage-layer backstop that makes it impossible to insert two
        active work units for the same intent, even if an application-level
        dedup path is bypassed (e.g. a race between processes).  Terminal rows
        (committed/cancelled/failed) and empty keys are excluded so historical
        duplicates and keyless units do not violate the constraint.

        Index creation is best-effort: if pre-existing duplicate active rows block
        it (the index cannot be built), we log a warning and continue — the
        application-level dedup in ``ShadowRecorder.on_submit`` still protects
        against new duplicates until the offending rows are cleaned up.
        """
        async with self._engine.begin() as conn:
            await conn.run_sync(ControlPlaneBase.metadata.create_all)
            try:
                await conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS ux_work_units_idem_active "
                        "ON work_units (idempotency_key) "
                        "WHERE state IN ('queued', 'running', 'retry_wait', 'waiting_human') "
                        "AND idempotency_key != ''"
                    )
                )
            except Exception:
                _log.warning(
                    "Could not create partial unique index ux_work_units_idem_active "
                    "(likely blocked by pre-existing duplicate active work units). "
                    "Application-level idempotency dedup remains active; run a one-time "
                    "cleanup of duplicate work_units to enable the DB backstop.",
                    exc_info=True,
                )

    async def close(self) -> None:
        """Dispose of the engine, stop the daemon loop, and release resources.

        Safe to call from any context:
        - If called from the daemon loop itself (via ``sync._run(close())``),
          the engine is disposed and the loop is signaled to stop without
          self-join (the thread will exit naturally).
        - If called from an async context (FastAPI/pipeline), the daemon
          thread is joined after the loop stops.
        """
        with self._sync_lock:
            facade = self._sync_facade
            self._sync_facade = None

        # Dispose the engine (can run on any loop)
        await self._engine.dispose()

        if facade is not None:
            facade._stop_loop()

    @property
    def sync(self) -> "SyncFacade":
        """Return the sync bridge facade, starting the daemon loop on first access."""
        with self._sync_lock:
            if self._sync_facade is None:
                self._sync_facade = SyncFacade(self)
            return self._sync_facade

    # ------------------------------------------------------------------
    # WorkUnit
    # ------------------------------------------------------------------

    async def create_work_unit(self, dto: WorkUnitDTO) -> WorkUnitDTO:
        async with self._session() as session:
            session.add(_work_unit_to_orm(dto))
        return dto

    async def get_work_unit(self, work_unit_id: str) -> WorkUnitDTO | None:
        async with self._session() as session:
            orm = await session.get(WorkUnitORM, work_unit_id)
            return _work_unit_from_orm(orm) if orm else None

    async def update_work_unit_state(
        self,
        work_unit_id: str,
        state: WorkUnitState,
        *,
        assurance: AssuranceLevel | None = None,
        heartbeat_at: str = "",
        force: bool = False,
    ) -> WorkUnitDTO | None:
        from novel_forge.control_plane.schemas import utc_now_iso

        async with self._session() as session:
            orm = await session.get(WorkUnitORM, work_unit_id)
            if orm is None:
                return None
            current = WorkUnitState(orm.state)
            if not force and not current.can_transition_to(state):
                _log.warning(
                    "Invalid WorkUnit state transition: %s -> %s (id=%s)",
                    current.value,
                    state.value,
                    work_unit_id,
                )
                return _work_unit_from_orm(orm)
            orm.state = state.value
            orm.updated_at = utc_now_iso()
            if assurance is not None:
                orm.assurance = assurance.value
            if heartbeat_at:
                orm.heartbeat_at = heartbeat_at
            return _work_unit_from_orm(orm)

    async def find_by_idempotency_key(self, key: str) -> WorkUnitDTO | None:
        if not key:
            return None
        async with self._session() as session:
            result = await session.execute(
                select(WorkUnitORM).where(WorkUnitORM.idempotency_key == key).limit(1)
            )
            orm = result.scalar_one_or_none()
            return _work_unit_from_orm(orm) if orm else None

    async def list_active_work_units(self) -> list[WorkUnitDTO]:
        """Return all work units in a non-terminal state (for reconciliation)."""
        active_states = [
            WorkUnitState.QUEUED.value,
            WorkUnitState.RUNNING.value,
            WorkUnitState.RETRY_WAIT.value,
            WorkUnitState.WAITING_HUMAN.value,
        ]
        async with self._session() as session:
            result = await session.execute(
                select(WorkUnitORM).where(WorkUnitORM.state.in_(active_states))
            )
            return [_work_unit_from_orm(o) for o in result.scalars().all()]

    # ------------------------------------------------------------------
    # RunAttempt
    # ------------------------------------------------------------------

    async def create_run_attempt(self, dto: RunAttemptDTO) -> RunAttemptDTO:
        async with self._session() as session:
            session.add(_run_attempt_to_orm(dto))
        return dto

    async def get_run_attempt(self, attempt_id: str) -> RunAttemptDTO | None:
        async with self._session() as session:
            orm = await session.get(RunAttemptORM, attempt_id)
            return _run_attempt_from_orm(orm) if orm else None

    async def list_run_attempts(self, work_unit_id: str) -> list[RunAttemptDTO]:
        async with self._session() as session:
            result = await session.execute(
                select(RunAttemptORM)
                .where(RunAttemptORM.work_unit_id == work_unit_id)
                .order_by(RunAttemptORM.attempt_number)
            )
            return [_run_attempt_from_orm(o) for o in result.scalars().all()]

    async def update_run_attempt(
        self,
        attempt_id: str,
        *,
        state: RunAttemptState | None = None,
        error_kind: str | None = None,
        error_summary: dict[str, Any] | None = None,
        heartbeat_at: str = "",
        ended_at: str = "",
    ) -> RunAttemptDTO | None:
        async with self._session() as session:
            orm = await session.get(RunAttemptORM, attempt_id)
            if orm is None:
                return None
            if state is not None:
                orm.state = state.value
            if error_kind is not None:
                orm.error_kind = error_kind
            if error_summary is not None:
                orm.error_summary_json = _json.dumps(error_summary, ensure_ascii=False, default=str)
            if heartbeat_at:
                orm.heartbeat_at = heartbeat_at
            if ended_at:
                orm.ended_at = ended_at
            return _run_attempt_from_orm(orm)

    async def list_stale_attempts(self, heartbeat_before: str) -> list[RunAttemptDTO]:
        """Return attempts still running whose heartbeat is older than the cutoff."""
        active_states = [
            RunAttemptState.RUNNING.value,
            RunAttemptState.RETRY_WAIT.value,
            RunAttemptState.WAITING_HUMAN.value,
        ]
        async with self._session() as session:
            result = await session.execute(
                select(RunAttemptORM)
                .where(RunAttemptORM.state.in_(active_states))
                .where(RunAttemptORM.heartbeat_at < heartbeat_before)
                .where(RunAttemptORM.heartbeat_at != "")
            )
            return [_run_attempt_from_orm(o) for o in result.scalars().all()]

    # ------------------------------------------------------------------
    # StageExecution
    # ------------------------------------------------------------------

    async def create_stage_execution(self, dto: StageExecutionDTO) -> StageExecutionDTO:
        async with self._session() as session:
            session.add(_stage_to_orm(dto))
        return dto

    async def get_stage_execution(self, stage_id: str) -> StageExecutionDTO | None:
        async with self._session() as session:
            orm = await session.get(StageExecutionORM, stage_id)
            return _stage_from_orm(orm) if orm else None

    async def list_stage_executions(self, run_attempt_id: str) -> list[StageExecutionDTO]:
        async with self._session() as session:
            result = await session.execute(
                select(StageExecutionORM)
                .where(StageExecutionORM.run_attempt_id == run_attempt_id)
                .order_by(StageExecutionORM.started_at)
            )
            return [_stage_from_orm(o) for o in result.scalars().all()]

    async def update_stage_execution(
        self,
        stage_id: str,
        *,
        state: StageState | None = None,
        output_artifact_hash: str | None = None,
        retries_used: int | None = None,
        committed_version: int | None = None,
        heartbeat_at: str = "",
        ended_at: str = "",
    ) -> StageExecutionDTO | None:
        async with self._session() as session:
            orm = await session.get(StageExecutionORM, stage_id)
            if orm is None:
                return None
            if state is not None:
                orm.state = state.value
            if output_artifact_hash is not None:
                orm.output_artifact_hash = output_artifact_hash
            if retries_used is not None:
                orm.retries_used = retries_used
            if committed_version is not None:
                orm.committed_version = committed_version
            if heartbeat_at:
                orm.heartbeat_at = heartbeat_at
            if ended_at:
                orm.ended_at = ended_at
            return _stage_from_orm(orm)

    # ------------------------------------------------------------------
    # EventLedger
    # ------------------------------------------------------------------

    async def append_event(self, dto: EventLedgerEntryDTO) -> EventLedgerEntryDTO:
        async with self._session() as session:
            session.add(
                EventLedgerORM(
                    id=dto.id,
                    run_attempt_id=dto.run_attempt_id,
                    stage_execution_id=dto.stage_execution_id,
                    event_type=dto.event_type,
                    severity=dto.severity.value,
                    payload_json=_json.dumps(dto.payload, ensure_ascii=False, default=str),
                    created_at=dto.created_at,
                )
            )
        return dto

    async def list_events(
        self, run_attempt_id: str | None = None, limit: int = 500
    ) -> list[EventLedgerEntryDTO]:
        async with self._session() as session:
            stmt = select(EventLedgerORM)
            if run_attempt_id is not None:
                stmt = stmt.where(EventLedgerORM.run_attempt_id == run_attempt_id)
            stmt = stmt.order_by(EventLedgerORM.seq.desc()).limit(limit)
            result = await session.execute(stmt)
            return [_event_from_orm(o) for o in result.scalars().all()]

    # ------------------------------------------------------------------
    # ArtifactManifest
    # ------------------------------------------------------------------

    async def record_artifact(self, dto: ArtifactManifestDTO) -> ArtifactManifestDTO:
        async with self._session() as session:
            session.add(_artifact_to_orm(dto))
        return dto

    async def get_artifact(self, artifact_id: str) -> ArtifactManifestDTO | None:
        async with self._session() as session:
            orm = await session.get(ArtifactManifestORM, artifact_id)
            return _artifact_from_orm(orm) if orm else None

    async def find_artifact_by_sha256(self, sha256: str) -> ArtifactManifestDTO | None:
        async with self._session() as session:
            result = await session.execute(
                select(ArtifactManifestORM).where(ArtifactManifestORM.sha256 == sha256).limit(1)
            )
            orm = result.scalar_one_or_none()
            return _artifact_from_orm(orm) if orm else None

    # ------------------------------------------------------------------
    # ResourceVersion
    # ------------------------------------------------------------------

    async def record_resource_version(self, dto: ResourceVersionDTO) -> ResourceVersionDTO:
        async with self._session() as session:
            session.add(_resource_to_orm(dto))
        return dto

    async def list_resource_versions(
        self, project_id: str, resource_kind: str, resource_ref: str
    ) -> list[ResourceVersionDTO]:
        async with self._session() as session:
            result = await session.execute(
                select(ResourceVersionORM)
                .where(ResourceVersionORM.project_id == project_id)
                .where(ResourceVersionORM.resource_kind == resource_kind)
                .where(ResourceVersionORM.resource_ref == resource_ref)
                .order_by(ResourceVersionORM.version_number.desc())
            )
            return [_resource_from_orm(o) for o in result.scalars().all()]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    async def execute_raw(self, sql: str) -> None:
        """Execute a raw SQL statement (for migrations / maintenance only)."""
        async with self._engine.begin() as conn:
            await conn.execute(text(sql))


# ---------------------------------------------------------------------------
# SyncFacade - daemon-loop bridge for sync callers
# ---------------------------------------------------------------------------


class SyncFacade:
    """Synchronous bridge to :class:`ControlPlaneStore`.

    Starts a dedicated daemon thread running its own asyncio event loop.
    Sync methods submit coroutines to that loop via
    ``asyncio.run_coroutine_threadsafe`` and block on the result.

    This lets JobService worker threads and Desktop Qt threads call the
    control plane without managing their own event loops or colliding
    with an already-running loop.
    """

    def __init__(self, store: ControlPlaneStore) -> None:
        self._store = store
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._start()

    def _start(self) -> None:
        self._thread = threading.Thread(
            target=self._run_loop, name="control-plane-daemon", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=10.0)
        if self._loop is None:
            raise RuntimeError("Control plane daemon loop failed to start within 10s.")

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            try:
                # Cancel any pending tasks
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            finally:
                loop.close()

    def _stop_loop(self) -> None:
        """Signal the daemon loop to stop WITHOUT joining (non-blocking).

        Used when ``close()`` is called from within the daemon loop itself
        (via ``sync._run(close())``). The loop will exit naturally after the
        current coroutine completes.
        """
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)

    def _shutdown(self) -> None:
        """Stop the daemon loop AND join the thread (blocking).

        Only call from OUTSIDE the daemon thread (e.g. from the main thread
        or an async caller).
        """
        self._stop_loop()
        if self._thread is not None:
            # Don't join if we're somehow on the daemon thread itself
            if threading.current_thread() is not self._thread:
                self._thread.join(timeout=5.0)
        self._loop = None
        self._thread = None

    def _run(self, coro: Coroutine[Any, Any, _T], timeout: float = 30.0) -> _T:
        """Submit a coroutine to the daemon loop and block on the result."""
        if self._loop is None or not self._loop.is_running():
            raise RuntimeError("Control plane daemon loop is not running.")
        future: Future[_T] = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    # --- WorkUnit ---

    def create_work_unit(self, dto: WorkUnitDTO) -> WorkUnitDTO:
        return self._run(self._store.create_work_unit(dto))

    def get_work_unit(self, work_unit_id: str) -> WorkUnitDTO | None:
        return self._run(self._store.get_work_unit(work_unit_id))

    def update_work_unit_state(
        self,
        work_unit_id: str,
        state: WorkUnitState,
        *,
        assurance: AssuranceLevel | None = None,
        heartbeat_at: str = "",
        force: bool = False,
    ) -> WorkUnitDTO | None:
        return self._run(
            self._store.update_work_unit_state(
                work_unit_id,
                state,
                assurance=assurance,
                heartbeat_at=heartbeat_at,
                force=force,
            )
        )

    def find_by_idempotency_key(self, key: str) -> WorkUnitDTO | None:
        return self._run(self._store.find_by_idempotency_key(key))

    def list_active_work_units(self) -> list[WorkUnitDTO]:
        return self._run(self._store.list_active_work_units())

    # --- RunAttempt ---

    def create_run_attempt(self, dto: RunAttemptDTO) -> RunAttemptDTO:
        return self._run(self._store.create_run_attempt(dto))

    def update_run_attempt(
        self,
        attempt_id: str,
        *,
        state: RunAttemptState | None = None,
        error_kind: str | None = None,
        error_summary: dict[str, Any] | None = None,
        heartbeat_at: str = "",
        ended_at: str = "",
    ) -> RunAttemptDTO | None:
        return self._run(
            self._store.update_run_attempt(
                attempt_id,
                state=state,
                error_kind=error_kind,
                error_summary=error_summary,
                heartbeat_at=heartbeat_at,
                ended_at=ended_at,
            )
        )

    def list_run_attempts(self, work_unit_id: str) -> list[RunAttemptDTO]:
        return self._run(self._store.list_run_attempts(work_unit_id))

    def list_stale_attempts(self, heartbeat_before: str) -> list[RunAttemptDTO]:
        return self._run(self._store.list_stale_attempts(heartbeat_before))

    # --- StageExecution ---

    def create_stage_execution(self, dto: StageExecutionDTO) -> StageExecutionDTO:
        return self._run(self._store.create_stage_execution(dto))

    def get_stage_execution(self, stage_id: str) -> StageExecutionDTO | None:
        return self._run(self._store.get_stage_execution(stage_id))

    def list_stage_executions(self, run_attempt_id: str) -> list[StageExecutionDTO]:
        return self._run(self._store.list_stage_executions(run_attempt_id))

    def update_stage_execution(
        self,
        stage_id: str,
        *,
        state: StageState | None = None,
        output_artifact_hash: str | None = None,
        retries_used: int | None = None,
        committed_version: int | None = None,
        heartbeat_at: str = "",
        ended_at: str = "",
    ) -> StageExecutionDTO | None:
        return self._run(
            self._store.update_stage_execution(
                stage_id,
                state=state,
                output_artifact_hash=output_artifact_hash,
                retries_used=retries_used,
                committed_version=committed_version,
                heartbeat_at=heartbeat_at,
                ended_at=ended_at,
            )
        )

    # --- EventLedger ---

    def append_event(self, dto: EventLedgerEntryDTO) -> EventLedgerEntryDTO:
        return self._run(self._store.append_event(dto))

    def list_events(
        self, run_attempt_id: str | None = None, limit: int = 500
    ) -> list[EventLedgerEntryDTO]:
        return self._run(self._store.list_events(run_attempt_id, limit))

    # --- ArtifactManifest ---

    def record_artifact(self, dto: ArtifactManifestDTO) -> ArtifactManifestDTO:
        return self._run(self._store.record_artifact(dto))

    def get_artifact(self, artifact_id: str) -> ArtifactManifestDTO | None:
        return self._run(self._store.get_artifact(artifact_id))

    def find_artifact_by_sha256(self, sha256: str) -> ArtifactManifestDTO | None:
        return self._run(self._store.find_artifact_by_sha256(sha256))

    # --- ResourceVersion ---

    def record_resource_version(self, dto: ResourceVersionDTO) -> ResourceVersionDTO:
        return self._run(self._store.record_resource_version(dto))

    def list_resource_versions(
        self, project_id: str, resource_kind: str, resource_ref: str
    ) -> list[ResourceVersionDTO]:
        return self._run(
            self._store.list_resource_versions(project_id, resource_kind, resource_ref)
        )
