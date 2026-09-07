"""Short-form quality gate helpers inspired by long-form final gating."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from novel_forge.pipeline.quality_gate import QualityGate, QualityGateReport


@dataclass(frozen=True)
class ShortQualityGateThresholds:
    """Configurable short-form quality gate thresholds."""

    min_overall_score: float = 7.4
    continuity_floor: float = 7.0
    style_floor: float = 7.0
    engagement_floor: float = 7.0


def build_short_quality_gate_thresholds(settings: Any) -> ShortQualityGateThresholds:
    """Build short-form gate thresholds from Settings."""

    return ShortQualityGateThresholds(
        min_overall_score=float(
            getattr(settings, "short_quality_gate_min_overall_score", 7.4) or 7.4
        ),
        continuity_floor=float(
            getattr(settings, "short_quality_gate_continuity_floor", 7.0) or 7.0
        ),
        style_floor=float(getattr(settings, "short_quality_gate_style_floor", 7.0) or 7.0),
        engagement_floor=float(
            getattr(settings, "short_quality_gate_engagement_floor", 7.0) or 7.0
        ),
    )


def evaluate_short_quality_gate(
    eval_report: Any,
    thresholds: ShortQualityGateThresholds,
    *,
    require_intent_compliance: bool = False,
) -> QualityGateReport:
    """Run the final short-form quality gate on evaluation output."""

    gate = QualityGate(eval_threshold=thresholds.min_overall_score)
    gate.check_eval(eval_report)
    gate.check_eval_dimension(eval_report, "continuity", thresholds.continuity_floor)
    gate.check_eval_dimension(eval_report, "style", thresholds.style_floor)
    gate.check_eval_dimension(eval_report, "engagement", thresholds.engagement_floor)
    if require_intent_compliance:
        gate.check_eval_dimension(eval_report, "intent_compliance", 9.0)
    return gate.report()


def serialize_short_quality_gate_report(
    report: QualityGateReport,
    thresholds: ShortQualityGateThresholds,
    *,
    attempted_repair: bool,
    best_effort_accepted: bool,
) -> dict[str, Any]:
    """Serialize short-form quality gate result for persistence and UI."""

    checks = [
        {
            "dimension": item.dimension,
            "score": item.score,
            "threshold": item.threshold,
            "passed": item.passed,
            "message": item.message,
            "details": dict(item.details or {}),
        }
        for item in report.checks
    ]
    return {
        "verdict": report.verdict.value,
        "passed": not report.failed_checks,
        "summary": report.summary,
        "attempted_repair": attempted_repair,
        "best_effort_accepted": best_effort_accepted,
        "failed_dimensions": [item.dimension for item in report.failed_checks],
        "thresholds": {
            "min_overall_score": thresholds.min_overall_score,
            "continuity_floor": thresholds.continuity_floor,
            "style_floor": thresholds.style_floor,
            "engagement_floor": thresholds.engagement_floor,
        },
        "checks": checks,
    }
