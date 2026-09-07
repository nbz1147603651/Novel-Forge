"""Policy decisions for Repair Orchestration v2."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel

from novel_forge.pipeline.repair_orchestration.models import (
    RepairControlMode,
    RepairPlanCandidate,
    RepairStrategy,
)


class RepairRiskLevel(str, Enum):
    """Coarse risk buckets used by mode decisions."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RepairPolicyDecision(BaseModel):
    """Decision produced before a handler may execute a plan."""

    action: Literal["execute", "request_approval", "preview_only", "block"]
    reason: str
    risk: RepairRiskLevel
    requires_human: bool = False


class RepairPolicyEngine:
    """Central decision matrix for manual, assisted, and automatic repair modes."""

    HIGH_RISK_STRATEGIES = {
        RepairStrategy.WINDOW_REWRITE,
        RepairStrategy.FULLTEXT_REWRITE,
    }
    LOW_RISK_STRATEGIES = {
        RepairStrategy.LOCAL_PATCH,
        RepairStrategy.LLM_PATCH,
        RepairStrategy.JSON_PATCH,
        RepairStrategy.FORMAT_REPAIR,
        RepairStrategy.LOCAL_FALLBACK,
        RepairStrategy.VERIFY_ONLY,
    }

    def __init__(self, *, assisted_budget_confirm_ratio: float = 0.5) -> None:
        self._assisted_budget_confirm_ratio = assisted_budget_confirm_ratio

    def classify_risk(
        self,
        plan: RepairPlanCandidate,
        *,
        change_budget: float,
    ) -> RepairRiskLevel:
        if plan.strategy in self.HIGH_RISK_STRATEGIES:
            return RepairRiskLevel.HIGH
        if plan.crosses_artifact_boundary:
            return RepairRiskLevel.HIGH
        if change_budget > 0 and plan.estimated_change_ratio > change_budget:
            return RepairRiskLevel.HIGH
        if (
            change_budget > 0
            and plan.estimated_change_ratio > change_budget * self._assisted_budget_confirm_ratio
        ):
            return RepairRiskLevel.MEDIUM
        return RepairRiskLevel.LOW

    def decide(
        self,
        *,
        mode: RepairControlMode,
        plan: RepairPlanCandidate,
        change_budget: float,
        consecutive_failures: int = 0,
        verification_available: bool = True,
        hard_block: bool = False,
        data_damage_risk: bool = False,
    ) -> RepairPolicyDecision:
        risk = self.classify_risk(plan, change_budget=change_budget)

        if hard_block or data_damage_risk:
            return RepairPolicyDecision(
                action="request_approval",
                reason="hard_block_or_data_damage_risk",
                risk=risk,
                requires_human=True,
            )
        if mode == RepairControlMode.MANUAL:
            return RepairPolicyDecision(
                action="preview_only",
                reason="manual_mode_never_executes_without_user_approval",
                risk=risk,
                requires_human=True,
            )
        if mode == RepairControlMode.AI_ASSISTED:
            if risk in {RepairRiskLevel.MEDIUM, RepairRiskLevel.HIGH}:
                return RepairPolicyDecision(
                    action="request_approval",
                    reason="ai_assisted_requires_approval_for_medium_or_high_risk",
                    risk=risk,
                    requires_human=True,
                )
            return RepairPolicyDecision(
                action="execute",
                reason="ai_assisted_low_risk_auto_execute",
                risk=risk,
            )
        if not verification_available or not plan.verification_required:
            return RepairPolicyDecision(
                action="request_approval",
                reason="ai_auto_requires_review_when_verification_is_unavailable",
                risk=risk,
                requires_human=True,
            )
        if consecutive_failures >= 3:
            return RepairPolicyDecision(
                action="request_approval",
                reason="ai_auto_escalates_after_consecutive_failures",
                risk=risk,
                requires_human=True,
            )
        return RepairPolicyDecision(
            action="execute",
            reason="ai_auto_policy_allows_execution",
            risk=risk,
        )
