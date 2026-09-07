"""Runtime-contract artifact repair domain for Repair Orchestration v2."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from novel_forge.pipeline.repair_orchestration.domains._shared import (
    fallback_project_id,
    record_domain_repair_ledger,
    resolve_mode,
)
from novel_forge.pipeline.repair_orchestration.models import (
    RepairArtifactChange,
    RepairControlMode,
    RepairDomain,
    RepairExecutionResult,
    RepairMission,
    RepairPlanCandidate,
    RepairStrategy,
    RepairSurface,
    RepairTarget,
    RepairVerificationResult,
)
from novel_forge.pipeline.repair_orchestration.orchestrator import RepairOrchestrator
from novel_forge.pipeline.repair_orchestration.registry import RepairHandlerRegistry
from novel_forge.pipeline.repair_orchestration.snapshot import RepairSnapshotStore


def _runtime_contract_paths(layout: Any, chapter_number: int) -> list[Path]:
    paths: list[Path] = []
    plans_dir = getattr(layout, "plans_dir", None)
    if plans_dir is not None:
        paths.append(Path(plans_dir) / "chapter_contracts.json")
    for attr in ("plot_milestone_index_path", "init_readiness_artifact_path"):
        path = getattr(layout, attr, None)
        if path is not None:
            paths.append(Path(path))
    source_artifact_path = getattr(layout, "source_artifact_path", None)
    if callable(source_artifact_path):
        paths.append(Path(source_artifact_path("chapter_contract_index")))
    chapter_source_slice_path = getattr(layout, "chapter_source_slice_path", None)
    if callable(chapter_source_slice_path):
        paths.append(Path(chapter_source_slice_path(chapter_number)))
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


def _snapshot_runtime_contract_artifacts(
    storage: Any,
    layout: Any,
    chapter_number: int,
) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for path in _runtime_contract_paths(layout, chapter_number):
        exists = bool(storage.exists(path)) if hasattr(storage, "exists") else path.exists()
        payload = None
        if exists:
            payload = storage.load_json(path) if hasattr(storage, "load_json") else None
        snapshots.append({"path": str(path), "exists": exists, "payload": payload})
    return snapshots


def _restore_runtime_contract_artifacts(storage: Any, snapshots: list[dict[str, Any]]) -> None:
    for item in snapshots:
        path = Path(str(item.get("path") or ""))
        if bool(item.get("exists")):
            payload = item.get("payload")
            if isinstance(payload, dict):
                if hasattr(storage, "save_json"):
                    storage.save_json(path, payload)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(str(payload), encoding="utf-8")
        else:
            try:
                path.unlink()
            except FileNotFoundError:
                pass


class RuntimeContractRepairHandler:
    """V2 adapter for cross-artifact runtime contract repair."""

    name = "runtime_contract_repair"

    def supports(self, target: RepairTarget) -> bool:
        return (
            target.domain == RepairDomain.RUNTIME_CONTRACT
            and target.surface == RepairSurface.CHAPTER_CONTRACTS
        )

    async def plan(
        self,
        mission: RepairMission,
        target: RepairTarget,
    ) -> RepairPlanCandidate:
        del mission
        return RepairPlanCandidate(
            target_id=target.id,
            strategy=RepairStrategy.JSON_PATCH,
            summary=target.summary or "runtime contract artifact repair",
            rationale="wrap_existing_runtime_contract_repair_service",
            preview=str(target.payload.get("preview") or target.evidence or ""),
            estimated_change_ratio=float(target.payload.get("estimated_change_ratio") or 0.1),
            crosses_artifact_boundary=True,
            verification_required=True,
        )

    async def snapshot(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
    ) -> dict[str, Any]:
        del target, plan
        ctx = mission.source_context
        review = ctx.get("review")
        bundle = getattr(getattr(review, "prepared", None), "bundle", None)
        chapter_number = int(
            getattr(getattr(bundle, "chapter_outline", None), "chapter_number", 0) or 0
        )
        layout = getattr(bundle, "layout", None)
        storage = getattr(ctx.get("context"), "storage", None)
        artifact_snapshots: list[dict[str, Any]] = []
        if storage is not None and layout is not None and chapter_number > 0:
            artifact_snapshots = _snapshot_runtime_contract_artifacts(
                storage,
                layout,
                chapter_number,
            )
        return {
            "artifact_snapshots": artifact_snapshots,
            "runtime_contract_result": ctx.get("runtime_contract_result"),
        }

    async def execute(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
    ) -> RepairExecutionResult:
        del target, plan
        from novel_forge.pipeline.long.services.runtime_contract_repair import (
            RuntimeContractRepairService,
        )

        ctx = mission.source_context
        required = ("context", "review", "trace", "ticket")
        missing = [key for key in required if key not in ctx]
        if missing:
            raise ValueError(
                f"runtime_contract repair mission missing context keys: {', '.join(missing)}"
            )

        outcome = await RuntimeContractRepairService(
            ctx["context"],
            review=ctx["review"],
            trace=ctx["trace"],
        ).repair(ticket=ctx["ticket"])
        ctx["runtime_contract_result"] = outcome
        return RepairExecutionResult(
            applied=bool(getattr(outcome, "applied", False)),
            payload={
                "applied": bool(getattr(outcome, "applied", False)),
                "has_chapter_contracts": getattr(outcome, "chapter_contracts", None) is not None,
                "has_chapter_source_slice": getattr(outcome, "chapter_source_slice", None) is not None,
            },
            changed_artifacts=[
                "plans/chapter_contracts.json",
                "source_artifact:chapter_contract_index",
                "chapter_source_slice",
            ],
            change_ratio=0.0,
            failure_reason=str(getattr(outcome, "reason", "") or ""),
        )

    async def verify(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> RepairVerificationResult:
        del mission, target, plan
        if not result.payload.get("applied"):
            return RepairVerificationResult(
                verified=False,
                confidence=0.25,
                reason=result.failure_reason or "runtime_contract_repair_not_applied",
            )
        if not result.payload.get("has_chapter_contracts"):
            return RepairVerificationResult(
                verified=False,
                confidence=0.25,
                reason="runtime_contract_repair_missing_contracts",
            )
        return RepairVerificationResult(
            verified=True,
            confidence=0.8,
            reason="runtime_contract_artifacts_repaired",
        )

    async def rollback(
        self,
        mission: RepairMission,
        target: RepairTarget,
        snapshot_payload: dict[str, Any],
    ) -> None:
        del target
        storage = getattr(mission.source_context.get("context"), "storage", None)
        if storage is not None:
            _restore_runtime_contract_artifacts(
                storage,
                list(snapshot_payload.get("artifact_snapshots") or []),
            )
        if snapshot_payload.get("runtime_contract_result") is not None:
            mission.source_context["runtime_contract_result"] = snapshot_payload[
                "runtime_contract_result"
            ]
        else:
            mission.source_context.pop("runtime_contract_result", None)

    async def commit(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> list[RepairArtifactChange]:
        del mission, target, plan
        return [
            RepairArtifactChange(artifact=artifact, metadata={"handler": self.name})
            for artifact in result.changed_artifacts
        ]


async def run_runtime_contract_repair_v2(
    *,
    context: Any,
    review: Any,
    trace: Any,
    ticket: Any,
    control_mode: RepairControlMode | str | None = None,
    snapshot_store: RepairSnapshotStore | None = None,
) -> Any:
    """Run runtime contract repair through v2 and return the legacy outcome."""

    settings = getattr(context, "settings", None)
    mode = resolve_mode(control_mode, settings)
    bundle = getattr(getattr(review, "prepared", None), "bundle", None)
    chapter_number = int(
        getattr(getattr(bundle, "chapter_outline", None), "chapter_number", 0) or 0
    )
    source_context: dict[str, Any] = {
        "context": context,
        "review": review,
        "trace": trace,
        "ticket": ticket,
    }
    target = RepairTarget(
        domain=RepairDomain.RUNTIME_CONTRACT,
        surface=RepairSurface.CHAPTER_CONTRACTS,
        chapter_number=chapter_number,
        issue_ref=str(getattr(ticket, "ticket_id", "") or f"runtime_contract:{chapter_number}"),
        severity="high",
        summary=str(
            getattr(ticket, "target_summary", "")
            or getattr(ticket, "issue_type", "")
            or "runtime contract artifact repair"
        ),
        evidence=str(getattr(ticket, "evidence", "") or ""),
    )
    mission = RepairMission(
        project_id=fallback_project_id(bundle),
        control_mode=mode,
        targets=[target],
        policy={"change_budget": 1.0},
        source_context=source_context,
    )
    registry = RepairHandlerRegistry()
    registry.register(RuntimeContractRepairHandler())
    orchestrator = RepairOrchestrator(
        registry=registry,
        snapshot_store=snapshot_store,
        on_step=getattr(context, "on_step", None),
    )
    outcome = await orchestrator.run(mission)
    record_domain_repair_ledger(mission=mission, outcome=outcome, sources=(review,))
    runtime_result = mission.source_context.get("runtime_contract_result")
    if runtime_result is None:
        from novel_forge.pipeline.long.services.runtime_contract_repair import (
            RuntimeContractRepairOutcome,
        )

        return RuntimeContractRepairOutcome(
            applied=False,
            reason="runtime_contract_repair_requires_human_review",
        )
    return runtime_result
