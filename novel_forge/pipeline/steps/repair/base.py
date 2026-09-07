"""RepairStepBase — abstract base class for all repair pipeline steps.

Provides a shared skeleton for repair steps (continuity, causal, reading_power)
with 7 abstract hooks and 4 shared methods.  Subclasses implement domain-specific
logic while inheriting common repair orchestration patterns.

Design rationale
----------------
Repair steps intentionally bypass PipelineStep's built-in retry mechanism
(``_call_with_retry``) because they need fine-grained control over:
- Patch vs full-text routing decisions
- Word-count guard enforcement
- Forbidden-element post-validation
- Per-issue escalation tracking

This base class provides shared orchestration without forcing a one-size-fits-all
retry strategy.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Generic

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.core.format_contracts import (
    TextOutputContractError,
    validate_text_output_contract,
)
from novel_forge.core.repair_attempt_guidance import build_repair_attempt_guidance
from novel_forge.gateway.router import ModelRouter
from novel_forge.obs.logger import get_logger
from novel_forge.obs.tracer import PipelineTrace
from novel_forge.pipeline.steps.base import InputT, OutputT, PipelineStep, StepEventCallback
from novel_forge.pipeline.steps.repair.forbidden_checker import check_forbidden
from novel_forge.pipeline.steps.repair.text_utils import extract_revised_text, non_space_len
from novel_forge.prompts.builder import PromptBuilder


class RepairStepBase(PipelineStep[InputT, OutputT], Generic[InputT, OutputT]):
    """Abstract base for repair pipeline steps.

    Subclasses must implement 7 abstract hooks that define:
    - What task type to use
    - How to map issue types to fix guidance
    - How to build LLM prompt context
    - Word-count bounds for the repair
    - How to adapt issues for patch compatibility
    - How to validate revised text
    - When to skip the repair entirely

    Shared methods provide common orchestration:
    - ``_run_patch_repair`` — route narrow issues through ChapterPatchStep
    - ``_run_fulltext_repair`` — call LLM for full-chapter rewrite
    - ``_retry_with_warning`` — one-shot retry with explicit warning
    - ``_check_and_retry_forbidden`` — post-repair forbidden element check + retry
    """

    def __init__(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
        *,
        settings: Settings,
        trace: PipelineTrace | None = None,
        on_step: StepEventCallback | None = None,
    ) -> None:
        super().__init__(router, builder, settings=settings, trace=trace, on_step=on_step)
        self._repair_logger = get_logger(f"pipeline.{self.step_name}")

    @abstractmethod
    def repair_task_type(self) -> TaskType:
        """Return the TaskType used for LLM calls in this repair step."""

    @abstractmethod
    def issue_type_fixes(self) -> dict[str, str]:
        """Return a mapping of issue_type (lowercase) to repair guidance string."""

    @abstractmethod
    def build_repair_context(self, input_data: InputT) -> dict[str, Any]:
        """Build the context dict for the LLM prompt."""

    @abstractmethod
    def compute_word_bounds(
        self,
        orig_len: int,
        issue_types: set[str],
    ) -> tuple[int, int, float, float]:
        """Compute word-count boundaries for the repair.

        Returns:
            Tuple of ``(word_count_min, word_count_max, guard_lo, guard_hi)``.
        """

    @abstractmethod
    def adapt_issue_for_patch(self, issue: Any) -> Any:
        """Adapt a domain-specific issue object to PatchInput-compatible format."""

    @abstractmethod
    def validate_revised_text(
        self,
        original: str,
        revised: str,
    ) -> tuple[bool, str]:
        """Validate the revised text against domain-specific constraints.

        Returns:
            Tuple of ``(is_valid, failure_reason)``.
        """

    def post_repair_canon_validate(
        self,
        original: str,
        revised: str,
        input_data: InputT,
    ) -> tuple[bool, str]:
        return True, ""

    @abstractmethod
    def should_skip(self, input_data: InputT) -> bool | None:
        """Check if this repair step should be skipped entirely.

        Returns:
            True to skip, False to proceed, None for no skip logic.
        """
        return None

    async def _run_patch_repair(
        self,
        chapter_number: int,
        chapter_text: str,
        issues: list[Any],
        *,
        context_size: int = 3,
        style: str = "literary",
        style_profile: dict[str, Any] | None = None,
        continuity_context: dict[str, Any] | None = None,
        boundary_context: dict[str, Any] | None = None,
        causal_link: dict[str, Any] | None = None,
        must_fix_summaries: list[str] | None = None,
        cognitive_constraints: list[dict[str, Any]] | None = None,
    ) -> tuple[str, int, int, bool]:
        """Route issues through ChapterPatchStep for narrow-window repair.

        Returns:
            Tuple of ``(revised_text, patches_applied, patches_attempted, fallback)``.
        """
        from novel_forge.pipeline.steps.patch_step import ChapterPatchStep, PatchInput

        patch_step = ChapterPatchStep(
            self._router,
            self._builder,
            settings=self._settings,
            trace=self._trace,
        )
        patch_result = await patch_step.run(
            PatchInput(
                chapter_number=chapter_number,
                chapter_text=chapter_text,
                issues=issues,
                context_size=context_size,
                style=style,
                style_profile=style_profile,
                continuity_context=continuity_context,
                boundary_context=boundary_context,
                causal_link=causal_link,
                must_fix_summaries=must_fix_summaries or [],
                cognitive_constraints=cognitive_constraints or [],
            )
        )
        return (
            patch_result.revised_text,
            patch_result.patches_applied,
            patch_result.patches_attempted,
            patch_result.fallback,
        )

    async def _run_fulltext_repair(
        self,
        context: dict[str, Any],
        *,
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, bool]:
        """Call LLM for full-chapter repair using the step's TaskType.

        Returns:
            Tuple of ``(revised_text, was_truncated)``.
        """
        task_type = self.repair_task_type()
        request = self._builder.build(
            task_type,
            context,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        response = await self._router.route(request)
        revised_text, was_truncated = extract_revised_text(response.content or "")
        try:
            revised_text = validate_text_output_contract(
                task_type,
                response.content or "",
                revised_text,
                min_chars=1,
            )
        except TextOutputContractError as exc:
            self._repair_logger.warning(
                "%s text contract validation failed | error=%s",
                task_type.value,
                exc,
            )
            return "", True
        return revised_text, was_truncated

    async def _retry_with_warning(
        self,
        context: dict[str, Any],
        warning: str,
        *,
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, bool]:
        """One-shot retry with an explicit warning injected into the context.

        Returns:
            Tuple of ``(revised_text, was_truncated)``.
        """
        retry_ctx = {**context, "_retry_warning": warning}
        retry_ctx.setdefault(
            "repair_attempt_guidance",
            build_repair_attempt_guidance(
                domain=self.step_name.replace("repair_", "") or "generic",
                round_number=2,
                max_rounds=2,
                issues=[],
                previous_strategy="retry_warning",
            ),
        )
        return await self._run_fulltext_repair(
            retry_ctx,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    async def _check_and_retry_forbidden(
        self,
        revised_text: str,
        forbidden_elements: list[str],
        context: dict[str, Any],
        *,
        orig_len: int,
        word_count_min: int,
        word_count_max: int,
        guard_lo: float,
        guard_hi: float,
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, list[str]]:
        """Check revised text for forbidden elements and retry if found.

        Returns:
            Tuple of ``(final_text, remaining_warnings)``.
        """
        if not forbidden_elements or not revised_text:
            return revised_text, []

        hits = check_forbidden(revised_text, forbidden_elements)
        if not hits:
            return revised_text, []

        self._repair_logger.warning(
            "%s: forbidden elements detected after repair: %s — retrying",
            self.step_name,
            ", ".join(f"'{h}'" for h in hits),
        )

        _quoted = [f"'{h}'" for h in hits]
        retry_warning = (
            f"你上一次返回的修复文本中仍包含以下禁用元素：{'、'.join(_quoted)}。\n"
            "禁用元素修复的要求是——必须替换为完全不同的意象或句式，\n"
            "而不是近义替换。请在保持上下文一致的前提下，"
            "将所有残留禁用元素彻底替换。\n"
            f"字数须在 {word_count_min}–{word_count_max} 字内。"
        )

        retry_ctx = {
            **context,
            "chapter_text": revised_text,
            "_retry_warning": retry_warning,
        }
        retry_ctx.setdefault(
            "repair_attempt_guidance",
            build_repair_attempt_guidance(
                domain=self.step_name.replace("repair_", "") or "generic",
                round_number=2,
                max_rounds=2,
                issues=[],
                previous_strategy="forbidden_element_retry",
            ),
        )
        retry_text, retry_truncated = await self._run_fulltext_repair(
            retry_ctx,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        if not retry_text or retry_truncated:
            self._repair_logger.warning(
                "%s: forbidden element retry returned empty/truncated text, keeping previous",
                self.step_name,
            )
            return revised_text, hits

        retry_len = non_space_len(retry_text)
        if orig_len > 0:
            retry_ratio = retry_len / orig_len
            if guard_lo <= retry_ratio <= guard_hi:
                self._repair_logger.info("%s: forbidden element retry accepted", self.step_name)
                revised_text = retry_text
            else:
                self._repair_logger.warning(
                    "%s: forbidden element retry rejected (%.0f%% word ratio)",
                    self.step_name,
                    retry_ratio * 100,
                )

        remaining = check_forbidden(revised_text, forbidden_elements)
        if remaining:
            _quoted_remaining = [f"'{r}'" for r in remaining]
            warnings = [
                f"修复后仍检测到禁用元素：{'、'.join(_quoted_remaining)}。"
                "将在下一轮修复中以阻断归档级别重点处理。"
            ]
            self._repair_logger.warning(
                "%s: forbidden elements still remain after retry: %s",
                self.step_name,
                ", ".join(remaining),
            )
            return revised_text, warnings

        return revised_text, []
