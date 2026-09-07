"""Evidence-bearing selection between the current plan and one future alternative."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class PlanningImpactCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    passed: StrictBool
    evidence: str = Field(min_length=1)


class FutureOutlineDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    selected: Literal["original", "candidate"]
    facts: PlanningImpactCheck
    user_intent: PlanningImpactCheck
    motivation: PlanningImpactCheck
    causality: PlanningImpactCheck
    promises: PlanningImpactCheck
    contracts: PlanningImpactCheck
    creative_gain: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)

    @property
    def permits_publication(self) -> bool:
        return (
            self.selected == "candidate"
            and self.confidence >= 0.75
            and all(
                check.passed
                for check in (
                    self.facts,
                    self.user_intent,
                    self.motivation,
                    self.causality,
                    self.promises,
                    self.contracts,
                )
            )
        )
