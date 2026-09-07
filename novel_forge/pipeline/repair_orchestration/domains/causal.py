"""Causal adapter for Repair Orchestration v2."""

from __future__ import annotations

from typing import Any

from novel_forge.pipeline.repair_orchestration.domains._chapter import (
    restore_snapshot_paths,
    snapshot_layout_paths,
)
from novel_forge.pipeline.repair_orchestration.domains._shared import (
    fallback_project_id,
    record_domain_repair_ledger,
    resolve_mode,
    text_change_ratio,
)
from novel_forge.pipeline.repair_orchestration.domains.long_chapter import (
    record_long_candidate_cases,
    remember_rejected_long_candidate,
    verify_long_working_candidate,
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


class CausalRepairHandler:
    """V2 adapter for the active long-chapter causal repair loop."""

    name = "causal_repair_loop"

    def supports(self, target: RepairTarget) -> bool:
        return target.domain == RepairDomain.CAUSAL and target.surface in {
            RepairSurface.CHAPTER_TEXT,
            RepairSurface.CHAPTER_WINDOW,
        }

    async def plan(
        self,
        mission: RepairMission,
        target: RepairTarget,
    ) -> RepairPlanCandidate:
        strategy = RepairStrategy.LLM_PATCH
        raw_strategy = target.payload.get("strategy") or target.payload.get("repair_strategy")
        if raw_strategy:
            try:
                strategy = RepairStrategy(str(raw_strategy))
            except ValueError:
                strategy = RepairStrategy.LLM_PATCH
        return RepairPlanCandidate(
            target_id=target.id,
            strategy=strategy,
            summary=target.summary or "causal repair",
            rationale="wrap_existing_causal_repair_loop",
            preview=str(target.payload.get("preview") or ""),
            estimated_change_ratio=float(target.payload.get("estimated_change_ratio") or 0.05),
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
        ctx = mission.source_context
        chapter_number = int(ctx.get("chapter_number") or 0)
        return {
            "current_text": str(ctx.get("current_text") or ""),
            "paths": snapshot_layout_paths(
                ctx.get("bundle"),
                chapter_number,
                (
                    "alignment_report_path",
                    "continuity_report_path",
                    "chapter_repair_report_path",
                    "chapter_causal_report_path",
                ),
            ),
        }

    async def execute(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
    ) -> RepairExecutionResult:
        del target, plan
        from novel_forge.pipeline.long.stages.causal_repair import _execute_causal_repair_loop

        ctx = mission.source_context
        required = (
            "runner",
            "bundle",
            "packet",
            "bridge",
            "plan",
            "current_text",
            "alignment_report",
            "continuity_report",
            "chapter_repair_report",
            "chapter_number",
            "trace",
            "repair_thresholds",
            "prev_chapter_ending",
        )
        missing = [key for key in required if key not in ctx]
        if missing:
            raise ValueError(f"causal repair mission missing context keys: {', '.join(missing)}")

        before = str(ctx["current_text"])
        result = await _execute_causal_repair_loop(
            ctx["runner"],
            ctx["bundle"],
            ctx["packet"],
            ctx["bridge"],
            ctx["plan"],
            ctx.get("on_step") or getattr(ctx["runner"], "_on_step", None),
            current_text=before,
            alignment_report=ctx["alignment_report"],
            continuity_report=ctx["continuity_report"],
            chapter_repair_report=ctx["chapter_repair_report"],
            chapter_number=int(ctx["chapter_number"]),
            trace=ctx["trace"],
            repair_thresholds=ctx["repair_thresholds"],
            prev_chapter_ending=str(ctx["prev_chapter_ending"] or ""),
            max_causal_rounds=ctx.get("max_causal_rounds"),
            initial_causal_report=ctx.get("initial_causal_report"),
        )
        ctx["causal_result"] = result
        changed = result.current_text != before
        return RepairExecutionResult(
            applied=bool(getattr(result, "applied", False) or changed),
            payload={
                "rounds_used": int(getattr(result, "rounds_used", 0) or 0),
                "repair_exhausted": bool(getattr(result, "repair_exhausted", False)),
                "best_effort_accepted": bool(getattr(result, "best_effort_accepted", False)),
                "needs_human_review": bool(getattr(result, "needs_human_review", False)),
            },
            changed_artifacts=[
                "in_memory:current_text",
                "causal_report",
                "alignment_report",
                "continuity_report",
                "chapter_repair_report",
            ],
            change_ratio=text_change_ratio(before, result.current_text),
            warnings=list(getattr(result, "causal_warnings", []) or []),
            failure_reason=str(getattr(result, "best_effort_reason", "") or ""),
        )

    async def verify(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> RepairVerificationResult:
        del target, plan
        loop_result = mission.source_context.get("causal_result")
        report = getattr(loop_result, "causal_report", None)
        validation_status = str(getattr(report, "validation_status", "ok") or "ok").lower()
        if validation_status != "ok":
            unavailable_reason = f"causal_validation_{validation_status}"
        else:
            unavailable_reason = ""
        return verify_long_working_candidate(
            dimension="causal",
            loop_result=loop_result,
            execution=result,
            unavailable_reason=unavailable_reason,
        )

    async def rollback(
        self,
        mission: RepairMission,
        target: RepairTarget,
        snapshot_payload: dict[str, Any],
    ) -> None:
        del target
        mission.source_context["current_text"] = str(snapshot_payload.get("current_text") or "")
        remember_rejected_long_candidate(mission.source_context, "causal_result")
        restore_snapshot_paths(snapshot_payload)

    async def commit(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> list[RepairArtifactChange]:
        del target, plan
        causal_result = mission.source_context.get("causal_result")
        if causal_result is not None:
            mission.source_context["current_text"] = getattr(causal_result, "current_text", "")
        return [
            RepairArtifactChange(artifact=artifact, metadata={"handler": self.name})
            for artifact in result.changed_artifacts
        ]


async def run_causal_repair_v2(
    *,
    runner: Any,
    bundle: Any,
    packet: Any,
    bridge: Any,
    plan: Any,
    on_step: Any,
    current_text: str,
    alignment_report: Any,
    continuity_report: Any,
    chapter_repair_report: Any | None,
    chapter_number: int,
    trace: Any,
    repair_thresholds: Any,
    prev_chapter_ending: str,
    control_mode: RepairControlMode | str | None = None,
    max_causal_rounds: int | None = None,
    initial_causal_report: Any | None = None,
    snapshot_store: RepairSnapshotStore | None = None,
) -> Any:
    """Run causal repair through the v2 orchestrator and return legacy result."""

    settings = getattr(runner, "_settings", None)
    mode = resolve_mode(control_mode, settings)
    source_context: dict[str, Any] = {
        "runner": runner,
        "bundle": bundle,
        "packet": packet,
        "bridge": bridge,
        "plan": plan,
        "on_step": on_step,
        "current_text": current_text,
        "alignment_report": alignment_report,
        "continuity_report": continuity_report,
        "chapter_repair_report": chapter_repair_report,
        "chapter_number": chapter_number,
        "trace": trace,
        "repair_thresholds": repair_thresholds,
        "prev_chapter_ending": prev_chapter_ending,
        "max_causal_rounds": max_causal_rounds,
        "initial_causal_report": initial_causal_report,
    }
    target = RepairTarget(
        domain=RepairDomain.CAUSAL,
        surface=RepairSurface.CHAPTER_TEXT,
        chapter_number=chapter_number,
        issue_ref=f"causal:{chapter_number}",
        severity="critical",
        summary="causal repair",
    )
    mission = RepairMission(
        project_id=fallback_project_id(bundle, runner),
        control_mode=mode,
        targets=[target],
        policy={
            "change_budget": float(getattr(repair_thresholds, "change_budget", 0.15) or 0.15),
            # The inner CausalRepairRunner already manages multi-round repair
            # via max_causal_rounds.  Setting max_rounds=1 prevents the outer
            # orchestrator from re-running the entire handler on verify failure.
            "retry_verification_failure": False,
            "retry_handler_exception": False,
        },
        source_context=source_context,
        max_rounds=1,
    )
    registry = RepairHandlerRegistry()
    registry.register(CausalRepairHandler())
    orchestrator = RepairOrchestrator(
        registry=registry,
        snapshot_store=snapshot_store,
        on_step=on_step,
    )
    outcome = await orchestrator.run(mission)
    record_domain_repair_ledger(mission=mission, outcome=outcome, sources=(bundle,))
    causal_result = mission.source_context.get("causal_result")
    candidate_result = causal_result or mission.source_context.get("causal_result_rejected")
    verification = outcome.attempts[-1].verification if outcome.attempts else None
    record_long_candidate_cases(
        bundle=bundle,
        runner=runner,
        dimension="causal",
        chapter_number=chapter_number,
        baseline_text=current_text,
        loop_result=candidate_result,
        verification=verification,
    )
    if causal_result is None:
        from novel_forge.pipeline.long.stages.causal_repair import CausalRepairLoopResult

        return CausalRepairLoopResult(
            current_text=mission.source_context.get("current_text", current_text),
            causal_report=initial_causal_report,
            alignment_report=alignment_report,
            continuity_report=continuity_report,
            chapter_repair_report=chapter_repair_report,
            causal_warnings=[],
            rounds_used=0,
            needs_human_review=True,
        )
    return causal_result
