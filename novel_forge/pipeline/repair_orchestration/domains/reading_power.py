"""Reading-power adapter for Repair Orchestration v2."""

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


class ReadingPowerRepairHandler:
    """V2 adapter for the long-chapter reading-power repair loop."""

    name = "reading_power_repair_loop"

    def supports(self, target: RepairTarget) -> bool:
        return target.domain == RepairDomain.READING_POWER and target.surface in {
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
            summary=target.summary or "reading power repair",
            rationale="wrap_existing_reading_power_repair_loop",
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
        snapshots: list[dict[str, Any]] = []
        bundle = ctx.get("bundle")
        chapter_number = int(ctx.get("chapter_number") or 0)
        if getattr(bundle, "layout", None) is not None and chapter_number > 0:
            snapshots.extend(
                snapshot_layout_paths(
                    bundle,
                    chapter_number,
                    ("reading_power_report_path",),
                )
            )
        return {
            "current_text": str(ctx.get("current_text") or ""),
            "paths": snapshots,
        }

    async def execute(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
    ) -> RepairExecutionResult:
        del target, plan
        from novel_forge.pipeline.long.stages.reading_power_repair import (
            _execute_reading_power_repair_loop,
        )

        ctx = mission.source_context
        required = ("runner", "bundle", "packet", "bridge", "plan", "current_text", "chapter_number", "trace")
        missing = [key for key in required if key not in ctx]
        if missing:
            raise ValueError(f"reading_power repair mission missing context keys: {', '.join(missing)}")

        before = str(ctx["current_text"])
        result = await _execute_reading_power_repair_loop(
            ctx["runner"],
            ctx["bundle"],
            ctx["packet"],
            ctx["bridge"],
            ctx["plan"],
            before,
            int(ctx["chapter_number"]),
            ctx["trace"],
            window_manager=ctx.get("window_manager"),
            window_config=ctx.get("window_config"),
            repair_tickets=ctx.get("repair_tickets"),
            precomputed_reading_power_report=ctx.get("precomputed_reading_power_report"),
            precomputed_reading_power_text_hash=ctx.get("precomputed_reading_power_text_hash"),
            max_reading_power_rounds=ctx.get("max_reading_power_rounds"),
        )
        ctx["reading_power_result"] = result
        changed = result.current_text != before
        return RepairExecutionResult(
            applied=bool(getattr(result, "applied", False) or changed),
            payload={
                "rounds_used": int(getattr(result, "rounds_used", 0) or 0),
                "repair_exhausted": bool(getattr(result, "repair_exhausted", False)),
                "best_effort_accepted": bool(getattr(result, "best_effort_accepted", False)),
                "needs_human_review": bool(getattr(result, "needs_human_review", False)),
            },
            changed_artifacts=["in_memory:current_text", "reading_power_report"],
            change_ratio=text_change_ratio(before, result.current_text),
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
        loop_result = mission.source_context.get("reading_power_result")
        report = getattr(loop_result, "report", None)
        is_fallback = bool(
            getattr(report, "is_fallback", False)
            or str(getattr(report, "evaluation_status", "") or "").strip().lower()
            == "fallback"
        )
        if is_fallback:
            unavailable_reason = str(
                getattr(report, "fallback_reason", "") or "reading_power_unavailable"
            )
        else:
            unavailable_reason = ""
        return verify_long_working_candidate(
            dimension="reading_power",
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
        remember_rejected_long_candidate(mission.source_context, "reading_power_result")
        restore_snapshot_paths(snapshot_payload)

    async def commit(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> list[RepairArtifactChange]:
        del target, plan
        rp_result = mission.source_context.get("reading_power_result")
        if rp_result is not None:
            mission.source_context["current_text"] = getattr(rp_result, "current_text", "")
        return [
            RepairArtifactChange(artifact=artifact, metadata={"handler": self.name})
            for artifact in result.changed_artifacts
        ]


async def run_reading_power_repair_v2(
    *,
    runner: Any,
    bundle: Any,
    packet: Any,
    bridge: Any,
    plan: Any,
    current_text: str,
    chapter_number: int,
    trace: Any,
    control_mode: RepairControlMode | str | None = None,
    window_manager: Any | None = None,
    window_config: Any | None = None,
    repair_tickets: list[Any] | tuple[Any, ...] | None = None,
    precomputed_reading_power_report: Any | None = None,
    precomputed_reading_power_text_hash: str | None = None,
    max_reading_power_rounds: int | None = None,
    snapshot_store: RepairSnapshotStore | None = None,
) -> Any:
    """Run reading-power repair through the v2 orchestrator and return legacy result."""

    settings = getattr(runner, "_settings", None)
    mode = resolve_mode(control_mode, settings)
    source_context: dict[str, Any] = {
        "runner": runner,
        "bundle": bundle,
        "packet": packet,
        "bridge": bridge,
        "plan": plan,
        "current_text": current_text,
        "chapter_number": chapter_number,
        "trace": trace,
        "window_manager": window_manager,
        "window_config": window_config,
        "repair_tickets": repair_tickets,
        "precomputed_reading_power_report": precomputed_reading_power_report,
        "precomputed_reading_power_text_hash": precomputed_reading_power_text_hash,
        "max_reading_power_rounds": max_reading_power_rounds,
    }
    target = RepairTarget(
        domain=RepairDomain.READING_POWER,
        surface=RepairSurface.CHAPTER_TEXT,
        chapter_number=chapter_number,
        issue_ref=f"reading_power:{chapter_number}",
        severity="medium",
        summary="reading power repair",
    )
    mission = RepairMission(
        project_id=fallback_project_id(bundle, runner),
        control_mode=mode,
        targets=[target],
        policy={
            "change_budget": float(
                getattr(settings, "long_reading_power_repair_max_change_ratio", 0.35) or 0.35
            ),
            # Inner loop manages multi-round; prevent outer orchestrator multiplication.
            "retry_verification_failure": False,
            "retry_handler_exception": False,
        },
        source_context=source_context,
        max_rounds=1,
    )
    registry = RepairHandlerRegistry()
    registry.register(ReadingPowerRepairHandler())
    orchestrator = RepairOrchestrator(
        registry=registry,
        snapshot_store=snapshot_store,
        on_step=getattr(runner, "_on_step", None),
    )
    outcome = await orchestrator.run(mission)
    record_domain_repair_ledger(mission=mission, outcome=outcome, sources=(bundle,))
    rp_result = mission.source_context.get("reading_power_result")
    candidate_result = rp_result or mission.source_context.get("reading_power_result_rejected")
    verification = outcome.attempts[-1].verification if outcome.attempts else None
    record_long_candidate_cases(
        bundle=bundle,
        runner=runner,
        dimension="reading_power",
        chapter_number=chapter_number,
        baseline_text=current_text,
        loop_result=candidate_result,
        verification=verification,
    )
    if rp_result is None:
        from novel_forge.pipeline.long.stages.reading_power_repair import (
            ReadingPowerRepairLoopResult,
        )

        return ReadingPowerRepairLoopResult(
            current_text=mission.source_context.get("current_text", current_text),
            report=None,
            text_hash=None,
            needs_human_review=True,
        )
    return rp_result
