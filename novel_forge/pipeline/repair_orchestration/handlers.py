"""Handler protocol for Repair Orchestration v2."""

from __future__ import annotations

from typing import Any, Protocol

from novel_forge.pipeline.repair_orchestration.models import (
    RepairArtifactChange,
    RepairExecutionResult,
    RepairMission,
    RepairPlanCandidate,
    RepairTarget,
    RepairVerificationResult,
)


class RepairHandler(Protocol):
    """Domain-specific adapter used by the generic repair orchestrator."""

    name: str

    def supports(self, target: RepairTarget) -> bool:
        """Return True when this handler can repair the target."""

    async def plan(
        self,
        mission: RepairMission,
        target: RepairTarget,
    ) -> RepairPlanCandidate:
        """Build a candidate plan without mutating project state."""

    async def snapshot(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
    ) -> dict[str, Any]:
        """Return handler-owned rollback data for the target."""

    async def execute(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
    ) -> RepairExecutionResult:
        """Apply the planned repair to an in-memory payload or durable artifact."""

    async def verify(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> RepairVerificationResult:
        """Verify the repair result before it may be committed."""

    async def rollback(
        self,
        mission: RepairMission,
        target: RepairTarget,
        snapshot_payload: dict[str, Any],
    ) -> None:
        """Rollback a failed or rejected repair attempt."""

    async def commit(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> list[RepairArtifactChange]:
        """Commit a verified repair and return changed artifact metadata."""
