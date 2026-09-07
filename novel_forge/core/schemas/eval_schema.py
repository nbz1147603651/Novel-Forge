"""Evaluation scores and reports."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from novel_forge.core.schemas.base import VersionedSchema


class EvalScore(VersionedSchema):
    """A single evaluation dimension."""

    dimension: str = Field(
        description=(
            "e.g. 'consistency', 'continuity', 'plot_progression', "
            "'character', 'style', 'engagement', 'pacing'."
        )
    )
    score: float = Field(ge=0.0, le=10.0, description="Score on 0–10 scale.")
    comment: str = Field(default="", description="Evaluator rationale.")


class RepairSuggestion(VersionedSchema):
    """A specific repair suggestion for a draft issue."""

    issue: str = Field(description="Brief description of the issue.")
    location: str = Field(description="Where the issue occurs (e.g. '第3段', '开头部分').")
    suggestion: str = Field(description="Concrete suggestion to fix the issue.")
    priority: str = Field(
        default="medium",
        description="Priority level: 'high', 'medium', or 'low'.",
    )
    dimension: str = Field(
        default="",
        description="Which evaluation dimension this belongs to.",
    )


class EvalReport(VersionedSchema):
    """Aggregated evaluation report for a draft."""

    scores: list[EvalScore] = Field(default_factory=list)
    overall_score: float = Field(
        default=0.0,
        ge=0.0,
        le=10.0,
        description="Weighted average of dimension scores.",
    )
    passed: bool = Field(
        default=False,
        description="Whether the draft meets quality threshold.",
    )
    threshold: float = Field(default=6.0, ge=0.0, le=10.0)
    summary: str = Field(default="", description="One-paragraph summary.")
    repair_suggestions: list[RepairSuggestion] = Field(
        default_factory=list,
        description="Specific repair suggestions for issues found.",
    )
    rubric_version: str = Field(
        default="",
        description="Evaluator rubric version used to produce this report.",
    )
    prompt_template_hash: str = Field(
        default="",
        description="Short SHA-256 hash of the prompt template used for this report.",
    )
    score_confidence: str = Field(
        default="normal",
        description="Confidence label for score shape: normal, suspect, or fallback.",
    )
    evaluation_status: str = Field(
        default="ok",
        description="Evaluation availability: ok/fallback/timeout/degraded.",
    )
    is_fallback: bool = Field(
        default=False,
        description="True when no trustworthy model evaluation was completed.",
    )
    fallback_reason: str = Field(
        default="",
        description="Machine-readable reason for an unavailable/degraded evaluation.",
    )
    score_diagnostics: dict[str, Any] = Field(
        default_factory=dict,
        description="Non-blocking diagnostics for repeated/anchored score patterns.",
    )
    source_text_hash: str = Field(
        default="",
        description="SHA-256 hash of source chapter text used for this report (compat field).",
    )
    ai_flavor_advisory: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "M5 rubric v3 — advisory block for AI-flavor patterns. Derived "
            "deterministically from a HumanizeReport (see DraftEvaluator."
            "_build_ai_flavor_advisory). Contains hit_count, by_pattern_id, "
            "by_severity, evidence, and a deterministic_score (same formula "
            "as QualityGate.check_ai_flavor but exposed as advisory only — "
            "this field NEVER affects overall_score or passed. Future rubric "
            "versions may promote it to a hard gate once distribution is "
            "calibrated (see scripts/calibrate_ai_flavor_distribution.py)."
        ),
    )

    def compute_overall(self) -> None:
        """Recalculate overall_score from individual scores and set passed flag."""
        if self.scores:
            avg = sum(s.score for s in self.scores) / len(self.scores)
            object.__setattr__(self, "overall_score", round(avg, 2))
        object.__setattr__(self, "passed", self.overall_score >= self.threshold)
