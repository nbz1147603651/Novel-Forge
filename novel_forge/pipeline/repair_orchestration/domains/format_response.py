"""Repair Orchestration v2 handler for protocol-level JSON response repair."""

from __future__ import annotations

from typing import Any

from novel_forge.core.response_repair.orchestrator import (
    FormatRepairContext,
    FormatRepairResult,
    LLMRepairCallable,
    LocalRepairAcceptor,
    RawRequiredKeyLossClassifier,
    RepairSource,
    RepairValidator,
    repair_json_object_response,
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


class FormatResponseRepairHandler:
    """Handler that routes JSON response repair through the v2 orchestration contract."""

    name = "format_response_repair"

    def __init__(
        self,
        *,
        context: FormatRepairContext | None = None,
        validator: RepairValidator | None = None,
        accept_local_repair: LocalRepairAcceptor | None = None,
        classify_raw_required_key_loss: RawRequiredKeyLossClassifier | None = None,
        llm_repair: LLMRepairCallable | None = None,
    ) -> None:
        self._context = context
        self._validator = validator
        self._accept_local_repair = accept_local_repair
        self._classify_raw_required_key_loss = classify_raw_required_key_loss
        self._llm_repair = llm_repair
        self.result: FormatRepairResult | None = None

    def supports(self, target: RepairTarget) -> bool:
        return (
            target.domain == RepairDomain.FORMAT_RESPONSE
            and target.surface == RepairSurface.RESPONSE_JSON
        )

    async def plan(
        self,
        mission: RepairMission,
        target: RepairTarget,
    ) -> RepairPlanCandidate:
        context = self._resolve_context(mission)
        task_type = getattr(getattr(context, "task_type", None), "value", "unknown")
        attempt = int(getattr(context, "attempt", 0) or 0)
        max_attempts = int(getattr(context, "max_attempts", 0) or 0)
        return RepairPlanCandidate(
            target_id=target.id,
            strategy=RepairStrategy.FORMAT_REPAIR,
            summary=target.summary or "repair structured model response",
            rationale="protocol_response_json_repair",
            preview="",
            estimated_change_ratio=0.0,
            crosses_artifact_boundary=False,
            verification_required=True,
            metadata={
                "task_type": task_type,
                "attempt": attempt,
                "max_attempts": max_attempts,
            },
        )

    async def snapshot(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
    ) -> dict[str, Any]:
        del mission, target, plan
        return {"protocol_only": True}

    async def execute(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
    ) -> RepairExecutionResult:
        del target, plan
        context = self._resolve_context(mission)
        validator = self._resolve_validator(mission)
        self.result = await repair_json_object_response(
            context,
            validator=validator,
            accept_local_repair=self._resolve_accept_local_repair(mission),
            classify_raw_required_key_loss=self._resolve_raw_required_key_loss_classifier(
                mission
            ),
            llm_repair=self._resolve_llm_repair(mission),
        )
        source = self.result.source.value if self.result.source is not None else ""
        error = self.result.error
        return RepairExecutionResult(
            applied=bool(self.result.success and self.result.data is not None),
            payload={
                "source": source,
                "strategy": self.result.strategy,
                "risk": self.result.risk.value,
                "repair_action": self.result.repair_action,
                "diagnostics": dict(self.result.diagnostics),
            },
            changed_artifacts=[],
            change_ratio=0.0,
            failure_reason="" if error is None else f"{type(error).__name__}: {error}",
        )

    async def verify(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> RepairVerificationResult:
        del mission, target, plan
        if self.result is None or not result.applied:
            return RepairVerificationResult(
                verified=False,
                confidence=0.0,
                reason=result.failure_reason or "format_response_repair_failed",
            )
        return RepairVerificationResult(
            verified=True,
            confidence=1.0,
            reason="format_response_validator_passed",
        )

    async def rollback(
        self,
        mission: RepairMission,
        target: RepairTarget,
        snapshot_payload: dict[str, Any],
    ) -> None:
        del mission, target, snapshot_payload

    async def commit(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> list[RepairArtifactChange]:
        del mission, target, plan, result
        return []

    def _resolve_context(self, mission: RepairMission) -> FormatRepairContext:
        context = self._context or mission.source_context.get("format_context")
        if context is None:
            context = mission.source_context.get("context")
        if context is None:
            raise ValueError("format_response repair mission missing context keys: format_context")
        return context

    def _resolve_validator(self, mission: RepairMission) -> RepairValidator:
        validator = self._validator or mission.source_context.get("validator")
        if validator is None:
            raise ValueError("format_response repair mission missing context keys: validator")
        return validator

    def _resolve_accept_local_repair(
        self,
        mission: RepairMission,
    ) -> LocalRepairAcceptor | None:
        return self._accept_local_repair or mission.source_context.get("accept_local_repair")

    def _resolve_raw_required_key_loss_classifier(
        self,
        mission: RepairMission,
    ) -> RawRequiredKeyLossClassifier | None:
        return self._classify_raw_required_key_loss or mission.source_context.get(
            "classify_raw_required_key_loss"
        )

    def _resolve_llm_repair(self, mission: RepairMission) -> LLMRepairCallable | None:
        return self._llm_repair or mission.source_context.get("llm_repair")


async def run_format_response_repair_v2(
    context: FormatRepairContext,
    *,
    validator: RepairValidator,
    accept_local_repair: LocalRepairAcceptor | None = None,
    classify_raw_required_key_loss: RawRequiredKeyLossClassifier | None = None,
    llm_repair: LLMRepairCallable | None = None,
    on_step: Any = None,
) -> FormatRepairResult:
    """Repair JSON response content under the v2 protocol-only repair domain."""

    handler = FormatResponseRepairHandler(
        context=context,
        validator=validator,
        accept_local_repair=accept_local_repair,
        classify_raw_required_key_loss=classify_raw_required_key_loss,
        llm_repair=llm_repair,
    )
    registry = RepairHandlerRegistry()
    registry.register(handler)
    target = RepairTarget(
        domain=RepairDomain.FORMAT_RESPONSE,
        surface=RepairSurface.RESPONSE_JSON,
        issue_ref=context.task_type.value,
        severity="protocol",
        summary="structured response format repair",
        allowed_strategies=[RepairStrategy.FORMAT_REPAIR],
    )
    mission = RepairMission(
        project_id="protocol:format_response",
        control_mode=RepairControlMode.AI_AUTO,
        targets=[target],
        policy={"change_budget": 1.0},
        source_context={
            "task_type": context.task_type.value,
            "protocol_only": True,
            "repair_control_mode_bypass": True,
        },
        max_rounds=1,
    )
    await RepairOrchestrator(registry=registry, on_step=on_step).run(mission)
    if handler.result is not None:
        return handler.result
    return FormatRepairResult(
        success=False,
        source=RepairSource.LLM if llm_repair is not None else RepairSource.LOCAL,
        error=ValueError("Format response repair did not produce a candidate"),
    )
