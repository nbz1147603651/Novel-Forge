"""Book consistency repair domain handler."""

from __future__ import annotations

from typing import Any, Coroutine, Protocol

from novel_forge.pipeline.repair_orchestration.context import (
    RepairContextError,
    book_repair_queue_context,
)
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


class RepairExecutorFn(Protocol):
    """Protocol for the workspace execute_repair callback."""

    def __call__(
        self, runtime: Any, mission: RepairMission
    ) -> Coroutine[Any, Any, Any]: ...


class BookConsistencyRepairHandler:
    """Concrete adapter for whole-book ticket repair queues."""

    name = "book_consistency_repair"
    _surfaces = {RepairSurface.BOOK_CHAPTER_SET, RepairSurface.CHAPTER_TEXT}

    def __init__(
        self,
        runtime: Any | None = None,
        repair_executor: RepairExecutorFn | None = None,
    ) -> None:
        self._runtime = runtime
        self._repair_executor = repair_executor
        self._executor = TypedContextRepairExecutor(
            name=self.name,
            default_strategy=RepairStrategy.LLM_PATCH,
        )

    def supports(self, target: RepairTarget) -> bool:
        return target.domain == RepairDomain.BOOK_CONSISTENCY and target.surface in self._surfaces

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
        if "chapter_missions" not in mission.source_context:
            return await self._executor.execute(mission, plan)
        if self._runtime is None:
            raise RepairContextError("book_consistency_repair requires runtime for chapter_missions")
        if self._repair_executor is None:
            raise RepairContextError(
                "book_consistency_repair requires repair_executor for chapter_missions; "
                "inject workspace execute_repair via BookConsistencyRepairHandler(repair_executor=...)"
            )
        execute_repair = self._repair_executor

        queue = book_repair_queue_context(mission, handler=self.name)
        resolved = 0
        remaining = 0
        warnings: list[str] = []
        changed: list[str] = []
        for chapter_mission in queue.chapter_missions:
            execution = await execute_repair(self._runtime, chapter_mission)
            outcome = execution.result
            resolved += len(outcome.targets_resolved)
            remaining += len(outcome.targets_remaining)
            warnings.extend(outcome.warnings)
            changed.extend(change.artifact for change in outcome.artifacts_changed)
        if remaining:
            warnings.append("book_consistency_targets_remaining")
        mission.source_context["book_consistency_result"] = {
            "chapter_missions": len(queue.chapter_missions),
            "targets_resolved": resolved,
            "targets_remaining": remaining,
            "needs_human_review": remaining > 0,
        }
        return RepairExecutionResult(
            applied=bool(resolved > 0),
            payload=dict(mission.source_context["book_consistency_result"]),
            changed_artifacts=changed or ["book_consistency_queue"],
            warnings=warnings,
        )

    async def verify(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> RepairVerificationResult:
        del target
        if "chapter_missions" in mission.source_context:
            return RepairVerificationResult(
                verified=True,
                confidence=0.8,
                reason="book_consistency_queue_completed",
            )
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
