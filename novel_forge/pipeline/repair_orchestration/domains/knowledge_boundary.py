"""Knowledge-boundary repair domain for Repair Orchestration v2."""

from __future__ import annotations

from typing import Any

from novel_forge.pipeline.repair_orchestration.audit_events import (
    emit_repair_audit_event,
    issue_audit_payload,
    text_hash,
)
from novel_forge.pipeline.repair_orchestration.domains._shared import (
    fallback_project_id,
    record_domain_repair_ledger,
    resolve_mode,
    text_change_ratio,
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


class KnowledgeBoundaryRepairHandler:
    """V2 adapter for finalize-stage knowledge-boundary repair."""

    name = "knowledge_boundary_repair_loop"

    def supports(self, target: RepairTarget) -> bool:
        return target.domain == RepairDomain.KNOWLEDGE_BOUNDARY and target.surface in {
            RepairSurface.CHAPTER_TEXT,
            RepairSurface.CHAPTER_WINDOW,
        }

    async def plan(
        self,
        mission: RepairMission,
        target: RepairTarget,
    ) -> RepairPlanCandidate:
        del mission
        strategy = RepairStrategy.WINDOW_REWRITE
        raw_strategy = target.payload.get("strategy") or target.payload.get("repair_strategy")
        if raw_strategy:
            try:
                strategy = RepairStrategy(str(raw_strategy))
            except ValueError:
                strategy = RepairStrategy.WINDOW_REWRITE
        return RepairPlanCandidate(
            target_id=target.id,
            strategy=strategy,
            summary=target.summary or "knowledge boundary repair",
            rationale="wrap_existing_knowledge_boundary_repair_loop",
            preview=str(target.payload.get("preview") or target.evidence or ""),
            estimated_change_ratio=float(target.payload.get("estimated_change_ratio") or 0.08),
            crosses_artifact_boundary=False,
            verification_required=True,
        )

    async def snapshot(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
    ) -> dict[str, Any]:
        del target, plan
        return {
            "current_text": str(mission.source_context.get("current_text") or ""),
            "knowledge_boundary_result": mission.source_context.get("knowledge_boundary_result"),
        }

    async def execute(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
    ) -> RepairExecutionResult:
        del target, plan
        from novel_forge.pipeline.long.stages.knowledge_boundary_repair import (
            run_knowledge_boundary_repair_loop,
        )

        ctx = mission.source_context
        required = ("runner", "storage", "bundle", "packet", "current_text", "chapter_number", "findings")
        missing = [key for key in required if key not in ctx]
        if missing:
            raise ValueError(
                f"knowledge_boundary repair mission missing context keys: {', '.join(missing)}"
            )

        before = str(ctx["current_text"])
        result = await run_knowledge_boundary_repair_loop(
            runner=ctx["runner"],
            storage=ctx["storage"],
            bundle=ctx["bundle"],
            packet=ctx["packet"],
            current_text=before,
            chapter_number=int(ctx["chapter_number"]),
            findings=list(ctx.get("findings") or ()),
            trace=ctx.get("trace"),
            max_rounds=int(ctx.get("max_rounds") or 2),
        )
        ctx["knowledge_boundary_result"] = result
        changed = result.current_text != before
        exhausted = bool(getattr(result, "repair_exhausted", False))
        return RepairExecutionResult(
            applied=bool(getattr(result, "repair_attempted", False) and changed),
            payload={
                "repair_exhausted": exhausted,
                "rounds_used": int(getattr(result, "rounds_used", 0) or 0),
                "findings_before": len(getattr(result, "findings_before", []) or []),
                "findings_after": len(getattr(result, "findings_after", []) or []),
            },
            changed_artifacts=["in_memory:current_text"],
            change_ratio=text_change_ratio(before, result.current_text),
            failure_reason="knowledge_boundary_repair_exhausted" if exhausted else "",
        )

    async def verify(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> RepairVerificationResult:
        del mission, target, plan
        if result.payload.get("repair_exhausted"):
            return RepairVerificationResult(
                verified=False,
                confidence=0.3,
                reason=result.failure_reason or "knowledge_boundary_repair_exhausted",
            )
        return RepairVerificationResult(
            verified=True,
            confidence=0.85,
            reason="knowledge_boundary_loop_completed",
        )

    async def rollback(
        self,
        mission: RepairMission,
        target: RepairTarget,
        snapshot_payload: dict[str, Any],
    ) -> None:
        del target
        mission.source_context["current_text"] = str(snapshot_payload.get("current_text") or "")
        if snapshot_payload.get("knowledge_boundary_result") is not None:
            mission.source_context["knowledge_boundary_result"] = snapshot_payload[
                "knowledge_boundary_result"
            ]
        else:
            mission.source_context.pop("knowledge_boundary_result", None)

    async def commit(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> list[RepairArtifactChange]:
        del target, plan
        kb_result = mission.source_context.get("knowledge_boundary_result")
        if kb_result is not None:
            mission.source_context["current_text"] = getattr(kb_result, "current_text", "")
        return [
            RepairArtifactChange(artifact=artifact, metadata={"handler": self.name})
            for artifact in result.changed_artifacts
        ]


async def run_knowledge_boundary_repair_v2(
    *,
    runner: Any,
    storage: Any,
    bundle: Any,
    packet: Any,
    current_text: str,
    chapter_number: int,
    findings: list[Any],
    trace: Any | None = None,
    max_rounds: int = 2,
    control_mode: RepairControlMode | str | None = None,
    snapshot_store: RepairSnapshotStore | None = None,
) -> Any:
    """Run knowledge-boundary repair through v2 and return the legacy result."""

    settings = getattr(runner, "_settings", None)
    mode = resolve_mode(control_mode, settings)
    source_context: dict[str, Any] = {
        "runner": runner,
        "storage": storage,
        "bundle": bundle,
        "packet": packet,
        "current_text": current_text,
        "chapter_number": chapter_number,
        "findings": findings,
        "trace": trace,
        "max_rounds": max_rounds,
    }
    on_step = getattr(runner, "_on_step", None)
    source_hash = text_hash(current_text)
    finding_audit_payloads: list[dict[str, Any]] = []
    for finding in findings:
        payload = issue_audit_payload(
            finding,
            event_type="selected",
            dimension=RepairDomain.KNOWLEDGE_BOUNDARY.value,
            surface=RepairSurface.CHAPTER_WINDOW.value,
            chapter=chapter_number,
            status="selected",
            repair_action="domain_batch",
            source_text_hash=source_hash,
            location_mode="domain_batch",
            extra={"batch_mode": True},
        )
        finding_audit_payloads.append(payload)
        emit_repair_audit_event(on_step, payload)
        emit_repair_audit_event(
            on_step,
            {
                **payload,
                "event_type": "attempted",
                "status": "attempted",
                "repair_action": "domain_batch",
            },
        )
    target = RepairTarget(
        domain=RepairDomain.KNOWLEDGE_BOUNDARY,
        surface=RepairSurface.CHAPTER_WINDOW,
        chapter_number=chapter_number,
        issue_ref=f"knowledge_boundary:{chapter_number}",
        severity="high",
        summary="knowledge boundary repair",
    )
    mission = RepairMission(
        project_id=fallback_project_id(bundle, runner),
        control_mode=mode,
        targets=[target],
        policy={
            "change_budget": float(
                getattr(settings, "long_knowledge_boundary_repair_max_change_ratio", 0.35)
                or 0.35
            )
        },
        source_context=source_context,
    )
    registry = RepairHandlerRegistry()
    registry.register(KnowledgeBoundaryRepairHandler())
    orchestrator = RepairOrchestrator(
        registry=registry,
        snapshot_store=snapshot_store,
        on_step=on_step,
    )
    outcome = await orchestrator.run(mission)
    record_domain_repair_ledger(mission=mission, outcome=outcome, sources=(bundle,))
    kb_result = mission.source_context.get("knowledge_boundary_result")
    if kb_result is None:
        from novel_forge.pipeline.long.stages.knowledge_boundary_repair import (
            KnowledgeBoundaryRepairResult,
        )

        return KnowledgeBoundaryRepairResult(
            current_text=mission.source_context.get("current_text", current_text),
            repair_exhausted=True,
            findings_before=list(findings),
            findings_after=list(findings),
        )
    after_ids = {
        issue_audit_payload(
            finding,
            event_type="identity",
            dimension=RepairDomain.KNOWLEDGE_BOUNDARY.value,
            surface=RepairSurface.CHAPTER_WINDOW.value,
            chapter=chapter_number,
            location_mode="domain_batch",
        )["issue_id"]
        for finding in list(getattr(kb_result, "findings_after", []) or [])
    }
    target_hash = text_hash(str(getattr(kb_result, "current_text", "") or ""))
    for payload in finding_audit_payloads:
        issue_id = str(payload.get("issue_id") or "")
        emit_repair_audit_event(
            on_step,
            {
                **payload,
                "event_type": "finalized",
                "status": "unresolved" if issue_id in after_ids else "resolved",
                "repair_action": "domain_batch_finalize",
                "target_text_hash": target_hash,
            },
        )
    return kb_result
