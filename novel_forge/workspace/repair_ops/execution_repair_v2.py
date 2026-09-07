"""Unified Repair Orchestration v2 workspace entrypoint."""

from __future__ import annotations

from typing import Any, Callable

from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.repair_orchestration import (
    RepairDomain,
    RepairHandlerRegistry,
    RepairMission,
    RepairOrchestrator,
    RepairOutcome,
    RepairSnapshotStore,
)
from novel_forge.pipeline.repair_orchestration.ledger import (
    append_repair_ledger_record_for_layout,
)
from novel_forge.workspace.execution_result import ExecutionResult, StepCallback
from novel_forge.workspace.runtime import RuntimeServices


def _record_repair_v2_ledger(
    runtime: RuntimeServices,
    mission: RepairMission,
    outcome: RepairOutcome,
) -> None:
    project_dir = runtime.storage.existing_project_dir(mission.project_id)
    layout = ProjectLayout(project_dir)
    append_repair_ledger_record_for_layout(layout, mission, outcome)


def _is_protocol_repair_mission(mission: RepairMission) -> bool:
    if bool(mission.source_context.get("protocol_only", False)):
        return True
    return any(target.domain == RepairDomain.FORMAT_RESPONSE for target in mission.targets)


def _should_record_repair_v2_ledger(mission: RepairMission) -> bool:
    return not _is_protocol_repair_mission(mission)


def build_default_repair_registry(
    runtime: RuntimeServices,
    *,
    on_step_progress: StepCallback = None,
    on_audit_update: Callable[[str, int, dict[str, Any]], None] | None = None,
) -> RepairHandlerRegistry:
    """Return the default v2 repair registry.

    All project repair handlers are registered here. Protocol-only format
    response repair is registered for domain completeness but does not write
    project ledger records.
    """

    from novel_forge.workspace.repair_v2_handlers import (
        WorkspaceCausalRepairHandler,
        WorkspaceContinuityRepairHandler,
    )

    registry = RepairHandlerRegistry()
    from novel_forge.pipeline.repair_orchestration import (
        BookConsistencyRepairHandler,
        FormatResponseRepairHandler,
        GuardrailRepairHandler,
        InitArtifactRepairHandler,
        KnowledgeBoundaryRepairHandler,
        PromptLeakRepairHandler,
        ReadingPowerRepairHandler,
        RuntimeContractRepairHandler,
        ShortStoryRepairHandler,
        StateAdjudicationRepairHandler,
    )

    registry.register(
        WorkspaceContinuityRepairHandler(
            runtime,
            on_step_progress=on_step_progress,
            on_audit_update=on_audit_update,
        )
    )
    registry.register(
        WorkspaceCausalRepairHandler(
            runtime,
            on_step_progress=on_step_progress,
            on_audit_update=on_audit_update,
        )
    )
    registry.register(ReadingPowerRepairHandler())
    registry.register(KnowledgeBoundaryRepairHandler())
    registry.register(PromptLeakRepairHandler())
    registry.register(RuntimeContractRepairHandler())
    registry.register(ShortStoryRepairHandler())
    registry.register(FormatResponseRepairHandler())
    registry.register(GuardrailRepairHandler(runtime))
    registry.register(InitArtifactRepairHandler(runtime))
    registry.register(BookConsistencyRepairHandler(runtime, repair_executor=execute_repair))
    registry.register(StateAdjudicationRepairHandler(runtime))
    return registry


async def execute_repair(
    runtime: RuntimeServices,
    mission: RepairMission,
    *,
    on_step_progress: StepCallback = None,
    registry: RepairHandlerRegistry | None = None,
    snapshot_store: RepairSnapshotStore | None = None,
    on_audit_update: Callable[[str, int, dict[str, Any]], None] | None = None,
) -> ExecutionResult[RepairOutcome]:
    """Execute a Repair Orchestration v2 mission."""

    from pathlib import Path

    from novel_forge.persistence.foundation_guard import require_versioned_maintenance_write

    storage = getattr(runtime, "storage", None)
    # Protocol repairs are in-memory parser corrections, not project writes.
    protocol_only = bool(mission.targets) and all(
        target.domain == RepairDomain.FORMAT_RESPONSE for target in mission.targets
    )
    root = None
    if storage is not None and not protocol_only:
        project_path = getattr(storage, "project_path", None)
        if callable(project_path):
            root = project_path(mission.project_id)
        else:
            existing_project_dir = getattr(storage, "existing_project_dir", None)
            if callable(existing_project_dir):
                root = existing_project_dir(mission.project_id)
    if isinstance(root, Path) and not protocol_only:
        require_versioned_maintenance_write(root, "旧修复任务")

    if registry is None:
        registry = build_default_repair_registry(
            runtime,
            on_step_progress=on_step_progress,
            on_audit_update=on_audit_update,
        )
    if snapshot_store is None and not _is_protocol_repair_mission(mission):
        layout = ProjectLayout(runtime.storage.existing_project_dir(mission.project_id))
        snapshot_store = RepairSnapshotStore(
            project_id=mission.project_id,
            root=layout.states_dir / "repair_v2_snapshots",
        )
    active_registry = registry
    orchestrator = RepairOrchestrator(
        registry=active_registry,
        snapshot_store=snapshot_store,
        on_step=on_step_progress,
    )
    outcome = await orchestrator.run(mission)
    if _should_record_repair_v2_ledger(mission):
        _record_repair_v2_ledger(runtime, mission, outcome)
    return ExecutionResult(project_id=mission.project_id, result=outcome)
