"""Prompt-leak repair domain for Repair Orchestration v2."""

from __future__ import annotations

from typing import Any

from novel_forge.pipeline.repair_orchestration.audit_events import (
    emit_repair_audit_event,
    issue_audit_payload,
    text_hash,
)
from novel_forge.pipeline.repair_orchestration.domains._shared import (
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


class PromptLeakRepairHandler:
    """V2 adapter for localized prompt-leak repair."""

    name = "prompt_leak_patch_repair"

    def supports(self, target: RepairTarget) -> bool:
        return target.domain == RepairDomain.PROMPT_LEAK and target.surface == RepairSurface.CHAPTER_TEXT

    async def plan(
        self,
        mission: RepairMission,
        target: RepairTarget,
    ) -> RepairPlanCandidate:
        del mission
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
            summary=target.summary or "prompt leak repair",
            rationale="wrap_existing_prompt_leak_patch_repair",
            preview=str(target.payload.get("preview") or target.evidence or ""),
            estimated_change_ratio=float(target.payload.get("estimated_change_ratio") or 0.03),
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
            "chapter_repair_report": mission.source_context.get("chapter_repair_report"),
            "prompt_leak_result": mission.source_context.get("prompt_leak_result"),
        }

    async def execute(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
    ) -> RepairExecutionResult:
        del target, plan
        from novel_forge.pipeline.long.stages.prompt_leak_repair import (
            repair_confirmed_prompt_leaks_with_patch,
        )

        ctx = mission.source_context
        required = (
            "router",
            "builder",
            "settings",
            "trace",
            "chapter_number",
            "current_text",
            "chapter_repair_report",
        )
        missing = [key for key in required if key not in ctx]
        if missing:
            raise ValueError(f"prompt_leak repair mission missing context keys: {', '.join(missing)}")

        before = str(ctx["current_text"])
        result = await repair_confirmed_prompt_leaks_with_patch(
            router=ctx["router"],
            builder=ctx["builder"],
            settings=ctx["settings"],
            trace=ctx["trace"],
            chapter_number=int(ctx["chapter_number"]),
            current_text=before,
            chapter_repair_report=ctx.get("chapter_repair_report"),
            style_profile=ctx.get("style_profile"),
            on_step=ctx.get("on_step"),
            allow_deterministic_fallback=bool(ctx.get("allow_deterministic_fallback", True)),
        )
        ctx["prompt_leak_result"] = result
        changed = result.text != before
        report_updated = bool(getattr(result, "report_updated", False))
        return RepairExecutionResult(
            applied=bool(getattr(result, "applied", False) or changed or report_updated),
            payload={
                "repaired_leaks": len(getattr(result, "repaired_leaks", ()) or ()),
                "remaining_leaks": len(getattr(result, "remaining_leaks", ()) or ()),
                "ignored_leaks": len(getattr(result, "ignored_leaks", ()) or ()),
                "report_updated": report_updated,
                "used_deterministic_fallback": bool(
                    getattr(result, "used_deterministic_fallback", False)
                ),
            },
            changed_artifacts=["in_memory:current_text", "chapter_repair_report"],
            change_ratio=text_change_ratio(before, result.text),
            failure_reason=str(getattr(result, "failure_reason", "") or ""),
        )

    async def verify(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> RepairVerificationResult:
        del mission, target, plan
        if int(result.payload.get("remaining_leaks") or 0) > 0:
            return RepairVerificationResult(
                verified=False,
                confidence=0.25,
                reason=result.failure_reason or "prompt_leaks_remaining",
            )
        if result.failure_reason and not result.applied:
            return RepairVerificationResult(
                verified=False,
                confidence=0.25,
                reason=result.failure_reason,
            )
        return RepairVerificationResult(verified=True, confidence=0.85, reason="prompt_leak_clean")

    async def rollback(
        self,
        mission: RepairMission,
        target: RepairTarget,
        snapshot_payload: dict[str, Any],
    ) -> None:
        del target
        mission.source_context["current_text"] = str(snapshot_payload.get("current_text") or "")
        mission.source_context["chapter_repair_report"] = snapshot_payload.get("chapter_repair_report")
        if snapshot_payload.get("prompt_leak_result") is not None:
            mission.source_context["prompt_leak_result"] = snapshot_payload["prompt_leak_result"]
        else:
            mission.source_context.pop("prompt_leak_result", None)

    async def commit(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> list[RepairArtifactChange]:
        del target, plan
        prompt_result = mission.source_context.get("prompt_leak_result")
        if prompt_result is not None:
            mission.source_context["current_text"] = getattr(prompt_result, "text", "")
            mission.source_context["chapter_repair_report"] = getattr(
                prompt_result,
                "chapter_repair_report",
                mission.source_context.get("chapter_repair_report"),
            )
        return [
            RepairArtifactChange(artifact=artifact, metadata={"handler": self.name})
            for artifact in result.changed_artifacts
        ]


async def run_prompt_leak_repair_v2(
    *,
    router: Any,
    builder: Any,
    settings: Any,
    trace: Any,
    chapter_number: int,
    current_text: str,
    chapter_repair_report: Any | None,
    style_profile: dict[str, Any] | None = None,
    on_step: Any | None = None,
    allow_deterministic_fallback: bool = True,
    control_mode: RepairControlMode | str | None = None,
    snapshot_store: RepairSnapshotStore | None = None,
) -> Any:
    """Run prompt-leak repair through v2 and return the legacy result."""

    mode = resolve_mode(control_mode, settings)
    source_context: dict[str, Any] = {
        "router": router,
        "builder": builder,
        "settings": settings,
        "trace": trace,
        "chapter_number": chapter_number,
        "current_text": current_text,
        "chapter_repair_report": chapter_repair_report,
        "style_profile": style_profile,
        "on_step": on_step,
        "allow_deterministic_fallback": allow_deterministic_fallback,
    }
    source_hash = text_hash(current_text)
    leak_audit_payloads: list[dict[str, Any]] = []
    for leak in list(getattr(chapter_repair_report, "prompt_leaks", []) or []):
        payload = issue_audit_payload(
            leak,
            event_type="selected",
            dimension=RepairDomain.PROMPT_LEAK.value,
            surface=RepairSurface.CHAPTER_TEXT.value,
            chapter=chapter_number,
            status="selected",
            repair_action="report_batch",
            source_text_hash=source_hash,
            location_mode="domain_batch",
            extra={"batch_mode": True},
        )
        leak_audit_payloads.append(payload)
        emit_repair_audit_event(on_step, payload)
        emit_repair_audit_event(
            on_step,
            {
                **payload,
                "event_type": "attempted",
                "status": "attempted",
                "repair_action": "report_batch",
            },
        )
    target = RepairTarget(
        domain=RepairDomain.PROMPT_LEAK,
        surface=RepairSurface.CHAPTER_TEXT,
        chapter_number=chapter_number,
        issue_ref=f"prompt_leak:{chapter_number}",
        severity="high",
        summary="prompt leak repair",
    )
    mission = RepairMission(
        project_id=str(getattr(settings, "project_id", "") or ""),
        control_mode=mode,
        targets=[target],
        policy={
            "change_budget": float(
                getattr(settings, "long_prompt_leak_repair_max_change_ratio", 0.15) or 0.15
            )
        },
        source_context=source_context,
    )
    registry = RepairHandlerRegistry()
    registry.register(PromptLeakRepairHandler())
    orchestrator = RepairOrchestrator(
        registry=registry,
        snapshot_store=snapshot_store,
        on_step=on_step,
    )
    await orchestrator.run(mission)
    prompt_result = mission.source_context.get("prompt_leak_result")
    if prompt_result is None:
        from novel_forge.pipeline.long.stages.prompt_leak_repair import PromptLeakRepairResult

        return PromptLeakRepairResult(
            text=mission.source_context.get("current_text", current_text),
            chapter_repair_report=mission.source_context.get(
                "chapter_repair_report",
                chapter_repair_report,
            ),
            remaining_leaks=tuple(getattr(chapter_repair_report, "prompt_leaks", ()) or ()),
            failure_reason="prompt_leak_repair_requires_human_review",
        )
    remaining_ids = {
        issue_audit_payload(
            leak,
            event_type="identity",
            dimension=RepairDomain.PROMPT_LEAK.value,
            surface=RepairSurface.CHAPTER_TEXT.value,
            chapter=chapter_number,
            location_mode="domain_batch",
        )["issue_id"]
        for leak in list(getattr(prompt_result, "remaining_leaks", []) or [])
    }
    target_hash = text_hash(str(getattr(prompt_result, "text", "") or ""))
    for payload in leak_audit_payloads:
        issue_id = str(payload.get("issue_id") or "")
        emit_repair_audit_event(
            on_step,
            {
                **payload,
                "event_type": "finalized",
                "status": "unresolved" if issue_id in remaining_ids else "resolved",
                "repair_action": "report_batch_finalize",
                "target_text_hash": target_hash,
                "fallback_action": "deterministic_fallback"
                if bool(getattr(prompt_result, "used_deterministic_fallback", False))
                else "",
            },
        )
    return prompt_result
