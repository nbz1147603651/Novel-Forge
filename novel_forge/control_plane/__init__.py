"""Runtime Control Plane.

A persistent, SQLite-backed control plane that provides:
- WorkUnit / RunAttempt lifecycle tracking (survives restarts)
- StageExecution with input/output hash lineage
- Immutable ArtifactManifest (content-addressed metadata)
- Append-only EventLedger for auditing
- ResourceVersion tracking for committed project resources

The control plane is opt-in (``runtime_control_enabled`` setting) and
designed for progressive adoption: phases 0-2 are shadow-writes that
do not affect existing pipeline behavior.
"""

from __future__ import annotations

from novel_forge.control_plane.call_ledger import CallLedger, get_call_ledger
from novel_forge.control_plane.capacity import (
    CapacityScheduler,
    get_global_capacity_scheduler,
)
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
from novel_forge.control_plane.factory import (
    create_shadow_recorder,
    get_control_plane_store,
    reset_control_plane_store,
    resolve_db_path,
)
from novel_forge.control_plane.harness import (
    HarnessCheckResult,
    HarnessViolationError,
    StageHarness,
    get_enforcement_mode,
    set_enforcement_mode,
)
from novel_forge.control_plane.health import (
    AdmitDecision,
    ProviderHealthEntry,
    RoutingHealthRegistry,
    get_global_health_registry,
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
from novel_forge.control_plane.plane import RuntimeControlPlane, get_cached_control_plane
from novel_forge.control_plane.protocol import NullOrchestrationBackend, OrchestrationBackend
from novel_forge.control_plane.reconciler import ReconcileResult, StartupReconciler
from novel_forge.control_plane.registry import (
    STAGE_DAG_EDGES,
    STAGE_DEFINITIONS,
    get_stage_definition,
    list_stage_names,
)
from novel_forge.control_plane.resume import (
    IdempotencyCheckResult,
    WorkUnitResumer,
    check_idempotent_submit,
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
from novel_forge.control_plane.stage_definitions import StageDefinition
from novel_forge.control_plane.stage_recorder import StageRecorder, get_stage_recorder
from novel_forge.control_plane.store import ControlPlaneStore, SyncFacade

__all__ = [
    # Store
    "ControlPlaneStore",
    "SyncFacade",
    "get_control_plane_store",
    "reset_control_plane_store",
    "resolve_db_path",
    "create_shadow_recorder",
    # Enums
    "AssuranceLevel",
    "ArtifactKind",
    "EventSeverity",
    "Priority",
    "ResourceKind",
    "RunAttemptState",
    "StageState",
    "WorkUnitState",
    # DTOs
    "ArtifactManifestDTO",
    "EventLedgerEntryDTO",
    "ResourceVersionDTO",
    "RunAttemptDTO",
    "StageExecutionDTO",
    "WorkUnitDTO",
    "utc_now_iso",
    # ORM
    "ControlPlaneBase",
    "WorkUnitORM",
    "RunAttemptORM",
    "StageExecutionORM",
    "EventLedgerORM",
    "ArtifactManifestORM",
    "ResourceVersionORM",
    # Health
    "RoutingHealthRegistry",
    "ProviderHealthEntry",
    "AdmitDecision",
    "get_global_health_registry",
    # Capacity
    "CapacityScheduler",
    "get_global_capacity_scheduler",
    # Stage recording
    "StageRecorder",
    "get_stage_recorder",
    "CallLedger",
    "get_call_ledger",
    # Reconciliation & resume
    "StartupReconciler",
    "ReconcileResult",
    "IdempotencyCheckResult",
    "WorkUnitResumer",
    "check_idempotent_submit",
    # Stage definitions & harness
    "StageDefinition",
    "StageHarness",
    "HarnessCheckResult",
    "HarnessViolationError",
    "STAGE_DEFINITIONS",
    "STAGE_DAG_EDGES",
    "get_stage_definition",
    "list_stage_names",
    "set_enforcement_mode",
    "get_enforcement_mode",
    # Unified plane
    "RuntimeControlPlane",
    "get_cached_control_plane",
    # Orchestration protocol
    "OrchestrationBackend",
    "NullOrchestrationBackend",
]
