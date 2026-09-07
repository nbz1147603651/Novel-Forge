"""Private typed-context executor utilities for Repair Orchestration v2 domains."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from novel_forge.pipeline.repair_orchestration.context import (
    artifact_repair_context,
    text_repair_context,
)
from novel_forge.pipeline.repair_orchestration.domains._shared import (
    restore_text_path,
    snapshot_text_path,
    text_change_ratio,
)
from novel_forge.pipeline.repair_orchestration.models import (
    RepairArtifactChange,
    RepairExecutionResult,
    RepairMission,
    RepairPlanCandidate,
    RepairStrategy,
    RepairVerificationResult,
)


class TypedContextRepairExecutor:
    """Shared executor for handlers whose concrete candidate is supplied in typed context."""

    def __init__(
        self,
        *,
        name: str,
        default_strategy: RepairStrategy,
    ) -> None:
        self.name = name
        self.default_strategy = default_strategy

    async def plan(
        self,
        mission: RepairMission,
        target_id: str,
        summary: str,
        payload: dict[str, Any],
        domain_value: str,
        allowed_strategies: list[RepairStrategy],
    ) -> RepairPlanCandidate:
        del mission
        strategy = self.default_strategy
        raw_strategy = payload.get("strategy") or payload.get("repair_strategy")
        if raw_strategy:
            try:
                strategy = RepairStrategy(str(raw_strategy))
            except ValueError:
                strategy = self.default_strategy
        if allowed_strategies and strategy not in allowed_strategies:
            strategy = allowed_strategies[0]
        return RepairPlanCandidate(
            target_id=target_id,
            strategy=strategy,
            summary=summary or f"{domain_value} repair",
            rationale=str(payload.get("rationale") or f"{self.name}_typed_context"),
            preview=str(payload.get("preview") or ""),
            estimated_change_ratio=float(payload.get("estimated_change_ratio") or 0.0),
            crosses_artifact_boundary=bool(payload.get("crosses_artifact_boundary", False)),
            verification_required=bool(payload.get("verification_required", True)),
            metadata=dict(payload.get("plan_metadata") or {}),
        )

    async def snapshot(
        self,
        mission: RepairMission,
        plan: RepairPlanCandidate,
    ) -> dict[str, Any]:
        del plan
        snapshots: list[dict[str, Any]] = []
        for raw_path in self._context_paths(mission):
            snapshots.append(snapshot_text_path(Path(raw_path)))
        return {
            "source_context": dict(mission.source_context),
            "paths": snapshots,
        }

    async def execute(
        self,
        mission: RepairMission,
        plan: RepairPlanCandidate,
    ) -> RepairExecutionResult:
        del plan
        if self._is_artifact_context(mission):
            return self._execute_artifact_context(mission)
        return self._execute_text_context(mission)

    async def verify(
        self,
        mission: RepairMission,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> RepairVerificationResult:
        del plan
        if result.failure_reason:
            return RepairVerificationResult(
                verified=False,
                confidence=0.2,
                reason=result.failure_reason,
            )
        if bool(mission.source_context.get("verification_passed", True)) is False:
            return RepairVerificationResult(
                verified=False,
                confidence=0.2,
                reason=str(mission.source_context.get("verification_reason") or "verification_failed"),
            )
        if not result.applied:
            return RepairVerificationResult(
                verified=False,
                confidence=0.0,
                reason="repair_not_applied",
            )
        return RepairVerificationResult(verified=True, confidence=0.8, reason="repair_applied")

    async def rollback(
        self,
        mission: RepairMission,
        snapshot_payload: dict[str, Any],
    ) -> None:
        source_context = snapshot_payload.get("source_context")
        if isinstance(source_context, dict):
            mission.source_context.clear()
            mission.source_context.update(source_context)
        for item in snapshot_payload.get("paths") or []:
            if isinstance(item, dict):
                restore_text_path(item)

    async def commit(
        self,
        mission: RepairMission,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> list[RepairArtifactChange]:
        del mission, plan
        return [
            RepairArtifactChange(artifact=artifact, metadata={"handler": self.name})
            for artifact in result.changed_artifacts
        ]

    def _context_paths(self, mission: RepairMission) -> list[Path]:
        paths: list[Path] = []
        for key in ("path", "artifact_path"):
            raw = mission.source_context.get(key)
            if raw:
                paths.append(Path(raw))
        for raw in mission.source_context.get("artifact_paths") or ():
            if raw:
                paths.append(Path(raw))
        return list(dict.fromkeys(paths))

    def _is_artifact_context(self, mission: RepairMission) -> bool:
        return "artifact" in mission.source_context and (
            "repaired_payload" in mission.source_context
            or "payload" in mission.source_context
            or "repaired_text" in mission.source_context
        )

    def _execute_text_context(self, mission: RepairMission) -> RepairExecutionResult:
        ctx = text_repair_context(mission, handler=self.name)
        before = ctx.current_text
        after = ctx.repaired_text
        mission.source_context[ctx.text_key] = after
        if ctx.path is not None:
            ctx.path.parent.mkdir(parents=True, exist_ok=True)
            ctx.path.write_text(after, encoding="utf-8")
        return RepairExecutionResult(
            applied=after != before,
            changed_artifacts=[ctx.artifact],
            change_ratio=text_change_ratio(before, after),
            failure_reason=ctx.failure_reason,
        )

    def _execute_artifact_context(self, mission: RepairMission) -> RepairExecutionResult:
        ctx = artifact_repair_context(mission, handler=self.name)
        changed_artifacts = [ctx.artifact]
        if ctx.path is not None:
            ctx.path.parent.mkdir(parents=True, exist_ok=True)
            if ctx.repaired_payload is not None:
                ctx.path.write_text(
                    json.dumps(ctx.repaired_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            else:
                ctx.path.write_text(ctx.repaired_text, encoding="utf-8")
            changed_artifacts = [str(ctx.path)]
        mission.source_context["artifact_result"] = ctx.repaired_payload
        return RepairExecutionResult(
            applied=ctx.applied,
            payload={"artifact": ctx.artifact},
            changed_artifacts=changed_artifacts,
            change_ratio=float(mission.source_context.get("change_ratio") or 0.0),
            failure_reason=ctx.failure_reason,
        )
