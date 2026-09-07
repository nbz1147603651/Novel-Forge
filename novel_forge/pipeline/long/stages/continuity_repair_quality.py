"""Quality gate helpers for continuity repair."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.long.decisions import RepairThresholds

_logger = get_logger("pipeline.continuity_repair")


@dataclass(frozen=True)
class QualityGateResult:
    """Result of the repair quality gate."""

    passed: bool
    is_rollback: bool
    is_secondary_repair: bool
    score_before: float
    score_after: float
    score_delta: float
    reason: str = ""


async def guard_repair_quality_gate(
    *,
    alignment_score_before: float,
    alignment_score_after: float,
    original_text: str,
    repaired_text: str,
    runner: Any,
    on_step: Callable[[str, Any], None],
    chapter_number: int,
    max_secondary_attempts: int = 1,
) -> tuple[str, bool, QualityGateResult]:
    """Detect post-repair regression and choose retry or rollback."""
    score_delta = alignment_score_after - alignment_score_before
    rollback_threshold = getattr(
        runner._settings, "quality_gate_rollback_delta", RepairThresholds.rollback_delta
    )

    if score_delta >= -rollback_threshold:
        return (
            repaired_text,
            False,
            QualityGateResult(
                passed=True,
                is_rollback=False,
                is_secondary_repair=False,
                score_before=alignment_score_before,
                score_after=alignment_score_after,
                score_delta=score_delta,
                reason=f"Score delta {score_delta:+.2f} >= -{rollback_threshold}",
            ),
        )

    _logger.warning(
        f"Quality gate: alignment score dropped from {alignment_score_before:.1f} to "
        f"{alignment_score_after:.1f} (delta={score_delta:.2f}), "
        f"threshold={rollback_threshold}"
    )
    on_step(
        "quality_gate_regression_detected",
        {
            "chapter": chapter_number,
            "score_before": alignment_score_before,
            "score_after": alignment_score_after,
            "score_delta": score_delta,
            "rollback_threshold": rollback_threshold,
            "max_secondary_attempts": max_secondary_attempts,
        },
    )

    if max_secondary_attempts > 0:
        on_step(
            "quality_gate_secondary_repair_requested",
            {
                "chapter": chapter_number,
                "attempts": max_secondary_attempts,
                "conservative_max_change_ratio": 0.03,
            },
        )
        return (
            repaired_text,
            False,
            QualityGateResult(
                passed=True,
                is_rollback=False,
                is_secondary_repair=True,
                score_before=alignment_score_before,
                score_after=alignment_score_after,
                score_delta=score_delta,
                reason="Secondary repair requested with max_change_ratio=0.03",
            ),
        )

    _logger.warning("Quality gate: no secondary attempts remaining, rolling back to original text")
    on_step(
        "quality_gate_rollback",
        {
            "chapter": chapter_number,
            "secondary_attempts": 0,
            "final_score": alignment_score_after,
            "rollback_score": alignment_score_before,
            "reason": "No secondary attempts, rolling back to pre-repair version",
        },
    )

    return (
        original_text,
        True,
        QualityGateResult(
            passed=False,
            is_rollback=True,
            is_secondary_repair=False,
            score_before=alignment_score_before,
            score_after=alignment_score_before,
            score_delta=0.0,
            reason=(
                f"Rollback: score dropped from {alignment_score_before:.1f} "
                f"to {alignment_score_after:.1f}"
            ),
        ),
    )
