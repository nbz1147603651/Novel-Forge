"""Initialization artifact repair domain handler."""

from __future__ import annotations

from typing import Any

from novel_forge.pipeline.repair_orchestration.domains._typed_context import (
    TypedContextRepairExecutor,
)
from novel_forge.pipeline.repair_orchestration.models import (
    RepairArtifactChange,
    RepairDomain,
    RepairExecutionResult,
    RepairMission,
    RepairPlanCandidate,
    RepairStrategy,
    RepairSurface,
    RepairTarget,
    RepairVerificationResult,
)


class InitArtifactRepairHandler:
    """Concrete adapter for blueprint/outline/chapter-contract artifact repairs."""

    name = "init_artifact_repair"
    _surfaces = {
        RepairSurface.BLUEPRINT,
        RepairSurface.OUTLINE,
        RepairSurface.CHAPTER_CONTRACTS,
        RepairSurface.SCENE_PLAN,
    }

    def __init__(self, runtime: Any | None = None) -> None:
        self._runtime = runtime
        self._executor = TypedContextRepairExecutor(
            name=self.name,
            default_strategy=RepairStrategy.JSON_PATCH,
        )

    def supports(self, target: RepairTarget) -> bool:
        return target.domain == RepairDomain.INIT_ARTIFACT and target.surface in self._surfaces

    async def plan(
        self,
        mission: RepairMission,
        target: RepairTarget,
    ) -> RepairPlanCandidate:
        return await self._executor.plan(
            mission,
            target.id,
            target.summary,
            target.payload,
            target.domain.value,
            target.allowed_strategies,
        )

    async def snapshot(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
    ) -> dict[str, Any]:
        del target
        return await self._executor.snapshot(mission, plan)

    async def execute(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
    ) -> RepairExecutionResult:
        del target
        return await self._executor.execute(mission, plan)

    async def verify(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> RepairVerificationResult:
        del target
        return await self._executor.verify(mission, plan, result)

    async def rollback(
        self,
        mission: RepairMission,
        target: RepairTarget,
        snapshot_payload: dict[str, Any],
    ) -> None:
        del target
        await self._executor.rollback(mission, snapshot_payload)

    async def commit(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> list[RepairArtifactChange]:
        del target
        return await self._executor.commit(mission, plan, result)
