"""EvaluateStep — scores a draft using the DraftEvaluator."""

from __future__ import annotations

import logging
from typing import Any

from novel_forge.core.config import Settings
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.utils.text_validation import (
    check_revelation_density,
    check_word_count,
)
from novel_forge.eval.evaluator import DraftEvaluator
from novel_forge.gateway.router import ModelRouter
from novel_forge.obs.tracer import PipelineTrace
from novel_forge.pipeline.steps.base import PipelineStep
from novel_forge.pipeline.steps.evaluate_context import project_evaluate_context
from novel_forge.pipeline.steps.step_registry import register_step
from novel_forge.prompts.builder import PromptBuilder

_log = logging.getLogger(__name__)


@register_step("evaluate")
class EvaluateStep(PipelineStep[str, EvalReport]):
    """Draft text → DraftEvaluator → EvalReport."""

    def __init__(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
        *,
        settings: Settings,
        trace: PipelineTrace | None = None,
        threshold: float = 6.0,
        extra_context: dict[str, Any] | None = None,
        kernel_context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(router, builder, settings=settings, trace=trace)
        self._evaluator = DraftEvaluator(
            router,
            builder,
            threshold=threshold,
            settings=self.settings,
        )
        self._kernel_context: dict[str, Any] = kernel_context or {}
        merged: dict[str, Any] = {}
        if kernel_context:
            merged.update(kernel_context)
        if extra_context:
            merged.update(extra_context)
        self._extra_context = project_evaluate_context(merged)

    @property
    def step_name(self) -> str:
        return "evaluate"

    async def _execute(self, input_data: str) -> EvalReport:
        report = await self._evaluator.evaluate(input_data, **self._extra_context)

        # ── Text validation (logging only, non-blocking) ──────────────────
        target = int(self._extra_context.get("target_word_count") or 0)
        word_count_gate_enabled = bool(self._extra_context.get("word_count_gate_enabled", True))
        wc_result = (
            check_word_count(input_data, target, tolerance=0.15)
            if target > 0 and word_count_gate_enabled
            else None
        )
        rev_result = check_revelation_density(input_data, max_revelations=2)

        _log.info(
            "eval_text_validation | wc_status=%s | actual=%d | target=%d | "
            "revelation_status=%s | count=%d",
            wc_result["status"] if wc_result is not None else "skipped",
            wc_result["actual_count"] if wc_result is not None else 0,
            wc_result["target"] if wc_result is not None else target,
            rev_result["status"],
            rev_result["revelation_count"],
        )

        if wc_result is not None and wc_result["status"] != "pass":
            _log.warning(
                "eval_word_count_%s | actual=%d | target=%d | deviation=%.1f%%",
                wc_result["status"],
                wc_result["actual_count"],
                wc_result["target"],
                wc_result["deviation_pct"],
            )
        if rev_result["status"] != "pass":
            _log.warning(
                "eval_revelation_density_%s | count=%d | max_allowed=%d",
                rev_result["status"],
                rev_result["revelation_count"],
                rev_result["max_allowed"],
            )

        return report
