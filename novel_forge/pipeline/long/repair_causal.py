"""Causal repair orchestration helpers."""

from __future__ import annotations

from novel_forge.pipeline.steps.causal_repair_step import (
    CausalRepairInput,
    CausalRepairResult,
    CausalRepairStep,
)


async def run_causal_repair(
    step: CausalRepairStep,
    payload: CausalRepairInput,
) -> CausalRepairResult:
    """Run causal repair. Works for both auto-pipeline and manual repair paths."""
    return await step.run(payload)
