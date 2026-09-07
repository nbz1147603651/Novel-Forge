"""Continuity repair orchestration helpers."""

from __future__ import annotations

from novel_forge.pipeline.steps.continuity_repair_step import (
    ContinuityRepairInput,
    ContinuityRepairResult,
    ContinuityRepairStep,
)


async def run_continuity_repair(
    step: ContinuityRepairStep,
    payload: ContinuityRepairInput,
) -> ContinuityRepairResult:
    """Run continuity repair even when it resolves to a no-op."""
    return await step.run(payload)
