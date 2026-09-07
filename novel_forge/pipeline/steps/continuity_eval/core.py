"""ContinuityEvalStep — core step class and _execute entry point."""

from __future__ import annotations

from typing import Any

from novel_forge.core.config import get_settings
from novel_forge.core.constants import TaskType
from novel_forge.core.review.review_contracts import normalize_review_mode
from novel_forge.core.schemas.continuity import ContinuityReport
from novel_forge.core.utils.boundary_windows import (
    DEFAULT_OPENING_PARAGRAPHS,
    DEFAULT_PREVIOUS_TAIL_PARAGRAPHS,
    coerce_paragraph_count,
)
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.steps.base import PipelineStep
from novel_forge.pipeline.steps.continuity_eval.context import (
    ContinuityEvalInput,
    build_llm_context,
)
from novel_forge.pipeline.steps.continuity_eval.evaluator import _EvaluatorMixin

_logger = get_logger(__name__)


class ContinuityEvalStep(_EvaluatorMixin, PipelineStep[ContinuityEvalInput, ContinuityReport]):
    """Audit continuity across chapter boundaries with local heuristics + LLM."""

    @property
    def step_name(self) -> str:
        return "check_continuity"

    async def _execute(self, input_data: ContinuityEvalInput) -> ContinuityReport:
        settings = get_settings()
        input_data = input_data.model_copy(
            update={
                "boundary_prev_tail_paragraphs": coerce_paragraph_count(
                    getattr(
                        self.settings,
                        "long_boundary_prev_tail_paragraphs",
                        DEFAULT_PREVIOUS_TAIL_PARAGRAPHS,
                    ),
                    default=DEFAULT_PREVIOUS_TAIL_PARAGRAPHS,
                ),
                "boundary_opening_paragraphs": coerce_paragraph_count(
                    getattr(
                        self.settings,
                        "long_boundary_opening_paragraphs",
                        DEFAULT_OPENING_PARAGRAPHS,
                    ),
                    default=DEFAULT_OPENING_PARAGRAPHS,
                    maximum=8,
                ),
            }
        )
        use_local_as_prescreen = getattr(settings, "local_check_as_prescreen", True)
        confidence_threshold = getattr(settings, "local_check_confidence_threshold", 0.7)
        review_mode = normalize_review_mode(
            "targeted_recheck" if input_data.recheck_mode or input_data.strict_review else "full"
        )

        local_issues: list[dict[str, Any]] = []
        if use_local_as_prescreen:
            local_issues = self._build_local_issues(input_data)
            if input_data.recheck_mode and input_data.repaired_issue_types:
                _repaired_set = {t.lower() for t in input_data.repaired_issue_types}
                local_issues = [
                    iss
                    for iss in local_issues
                    if (iss.get("issue_type") or "").lower() in _repaired_set
                ]

        _review_temp = getattr(self.settings, "temp_post_repair_review", 0.0)
        _review_prompt = getattr(self.settings, "long_post_repair_review_independent_prompt", "")

        llm_context, temperature = build_llm_context(
            input_data,
            number_paragraphs_fn=self._number_paragraphs,
            local_issues=local_issues,
            use_local_as_prescreen=use_local_as_prescreen,
            confidence_threshold=confidence_threshold,
            strict_review=input_data.strict_review,
            review_temp=_review_temp,
            review_prompt=_review_prompt,
            default_temp=self.settings.temp_check_continuity,
        )

        data = await self._call_with_retry(
            TaskType.CHECK_CONTINUITY,
            llm_context,
            max_tokens=self._dynamic_max_tokens(
                TaskType.CHECK_CONTINUITY,
                max(2500, len(input_data.chapter_text) // 2),
                prompt_overhead=3200,
                min_tokens=4096,
            ),
            temperature=temperature,
        )
        normalized = self._normalize_report(
            data,
            input_data,
            local_issues=local_issues if use_local_as_prescreen else None,
            use_local_as_prescreen=use_local_as_prescreen,
            confidence_threshold=confidence_threshold,
        )
        report = ContinuityReport.model_validate(normalized)
        report = report.model_copy(update={"review_mode": review_mode})

        if input_data.recheck_mode and (
            input_data.prior_issues or input_data.must_resolve_summaries
        ):
            report = self._apply_recheck_focus(
                report,
                must_resolve_summaries=input_data.must_resolve_summaries,
                prior_issues=input_data.prior_issues,
            )
            report = report.model_copy(update={"review_mode": review_mode})

        return report
