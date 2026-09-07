"""DraftStep — generates a prose draft from beats or chapter plan."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.domain.guardrails import detect_prompt_leaks
from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.core.format_contracts import (
    TextOutputContractError,
    validate_text_output_contract,
)
from novel_forge.core.parsing.text_utils import extract_text_content
from novel_forge.core.schemas.draft import Draft
from novel_forge.core.utils.text_validation import (
    check_revelation_density,
    check_word_count,
    count_chapter_words,
)
from novel_forge.pipeline.steps.base import PipelineStep
from novel_forge.pipeline.steps.prompt_diagnostics import log_prompt_diagnostics
from novel_forge.pipeline.steps.step_registry import register_step

_log = logging.getLogger(__name__)


@dataclass
class DraftInput:
    """Input for draft generation (works for both short & long mode)."""

    task_type: TaskType  # DRAFT or DRAFT_CHAPTER
    context: dict[str, Any]  # template variables
    prior_messages: list[dict[str, str]] | None = None
    multi_turn: bool = False
    reading_power_hint: dict[str, Any] | None = None  # 阅读力提示（可选）
    kernel_context: dict[str, Any] | None = None  # StoryKernel field slices (from ContextComposer)
    # Long-form callers opt into these two admission checks.  The shared step
    # remains compatible with short-form and focused scene callers that use
    # their own bounded validators.
    minimum_word_count: int = 0
    reject_prompt_leaks: bool = False


@register_step("draft")
class DraftStep(PipelineStep[DraftInput, Draft]):
    """Beats/Plan → model → Draft."""

    @property
    def step_name(self) -> str:
        return "draft"

    async def _execute(self, input_data: DraftInput) -> Draft:
        # Calculate safe max_tokens considering target output and prompt overhead
        target_words = input_data.context.get("target_word_count", 3000)
        if input_data.task_type in {TaskType.DRAFT_CHAPTER, TaskType.DRAFT_SCENE}:
            # Long-form chapters: use a generous ceiling; the router will clamp
            # to the actual model limit automatically.
            max_tokens = self._dynamic_max_tokens(
                input_data.task_type,
                target_words,
                prompt_overhead=2500,
                min_tokens=6144,
            )
        else:
            # Short-form content
            max_tokens = self._dynamic_max_tokens(
                input_data.task_type,
                target_words,
                prompt_overhead=2000,
                min_tokens=4096,
            )

        temperature = (
            self.settings.temp_draft_chapter
            if input_data.task_type in {TaskType.DRAFT_CHAPTER, TaskType.DRAFT_SCENE}
            else self.settings.temp_draft
        )

        # Merge reading_power_hint into context if provided
        ctx: dict[str, Any] = {}
        if input_data.kernel_context is not None:
            ctx.update(input_data.kernel_context)
        ctx.update(input_data.context)
        if input_data.reading_power_hint is not None:
            ctx["reading_power_hint"] = input_data.reading_power_hint

        def _build_request() -> Any:
            return self._builder.build(
                input_data.task_type,
                ctx,
                max_tokens=max_tokens,
                temperature=temperature,
                prior_messages=input_data.prior_messages,
                multi_turn=input_data.multi_turn or input_data.prior_messages is not None,
            )

        request = _build_request()
        if input_data.task_type in {TaskType.DRAFT_CHAPTER, TaskType.DRAFT_SCENE}:
            log_prompt_diagnostics(
                _log,
                event="draft_prompt_diagnostics",
                request=request,
                context=ctx,
                settings=self.settings,
                enabled_attr="long_draft_prompt_diagnostics_enabled",
                warn_attr="long_draft_prompt_warn_tokens",
                chapter=input_data.context.get("chapter_number", "?"),
                on_event=self._on_step_event,
            )

        target_word_count = input_data.context.get("target_word_count", 3000)
        chapter_num = input_data.context.get("chapter_number", "?")
        minimum_word_count = max(0, int(input_data.minimum_word_count or 0))
        max_attempts = 2
        text = ""
        last_error: ModelGatewayError | None = None

        def _mark_regeneration(*, actual_word_count: int, prompt_leaks: list[str]) -> None:
            ctx["draft_regeneration"] = {
                "minimum_word_count": minimum_word_count,
                "prompt_leak_detected": bool(prompt_leaks),
                "requires_full_rewrite": True,
            }
            self._on_step_event(
                "draft_regeneration_required",
                {
                    "chapter": chapter_num,
                    "attempt": attempt,
                    "actual_word_count": actual_word_count,
                    "minimum_word_count": minimum_word_count,
                    "prompt_leak_count": len(prompt_leaks),
                },
            )

        for attempt in range(1, max_attempts + 1):
            # A semantic retry adds bounded feedback to the prompt.  Rebuild
            # the non-streaming request too; streaming builds from ``ctx`` in
            # the shared LLM service.
            if attempt > 1:
                request = _build_request()
            response: Any
            if bool(
                getattr(self.settings, "long_streaming_text_enabled", True)
            ) and input_data.task_type in {
                TaskType.DRAFT,
                TaskType.DRAFT_CHAPTER,
                TaskType.DRAFT_SCENE,
            }:
                response_content = await self._call_with_retry(
                    input_data.task_type,
                    ctx,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    max_retries=max_attempts,
                    prior_messages=input_data.prior_messages,
                    multi_turn=input_data.multi_turn or input_data.prior_messages is not None,
                )
                response = SimpleNamespace(
                    content=str(response_content),
                    finish_reason="stop",
                    completion_tokens=0,
                )
            else:
                response = await self._router.route(request)
            text = extract_text_content(response.content)

            semantic_error: ModelGatewayError | None = None
            try:
                text = validate_text_output_contract(
                    input_data.task_type,
                    response.content,
                    text,
                    min_chars=1,
                )
            except TextOutputContractError as exc:
                contract_leaks = (
                    detect_prompt_leaks(text, max_hits=4) if input_data.reject_prompt_leaks else []
                )
                if contract_leaks:
                    # The shared text contract rejected the output first.  It
                    # is still a long-form admission failure, so retain the
                    # same bounded-rewrite feedback and terminal semantics.
                    _mark_regeneration(
                        actual_word_count=count_chapter_words(text),
                        prompt_leaks=contract_leaks,
                    )
                if not str(response.content or "").strip():
                    semantic_error = ModelGatewayError(
                        f"第 {chapter_num} 章草稿生成返回空内容（finish_reason={response.finish_reason}, "
                        f"completion_tokens={response.completion_tokens}, max_tokens={request.max_tokens}）",
                        is_transient=True,
                    )
                else:
                    semantic_error = ModelGatewayError(
                        f"第 {chapter_num} 章草稿生成格式无效：{exc}",
                        is_transient=True,
                    )

            if semantic_error is None and (not text or not text.strip()):
                semantic_error = ModelGatewayError(
                    f"第 {chapter_num} 章草稿生成返回空内容（finish_reason={response.finish_reason}, "
                    f"completion_tokens={response.completion_tokens}, max_tokens={request.max_tokens}）",
                    is_transient=True,
                )
            elif semantic_error is None:
                is_truncated = response.finish_reason == "length" or (
                    response.completion_tokens > 0
                    and request.max_tokens > 0
                    and response.completion_tokens >= request.max_tokens * 0.97
                )
                if is_truncated:
                    text_chars = count_chapter_words(text)
                    _log.warning(
                        "draft_truncation_warning | finish_reason=%s | "
                        "completion_tokens=%d | max_tokens=%d | text_chars=%d",
                        response.finish_reason,
                        response.completion_tokens,
                        request.max_tokens,
                        text_chars,
                    )
                    if text_chars < target_word_count * 0.3:
                        semantic_error = ModelGatewayError(
                            f"第 {chapter_num} 章草稿被截断且输出过短（{text_chars} 字符 < "
                            f"目标 {target_word_count} 的 30%，finish_reason={response.finish_reason}, "
                            f"completion_tokens={response.completion_tokens}）",
                            is_transient=True,
                        )

            if semantic_error is None:
                actual_word_count = count_chapter_words(text)
                prompt_leaks = (
                    detect_prompt_leaks(text, max_hits=4) if input_data.reject_prompt_leaks else []
                )
                too_short = minimum_word_count > 0 and actual_word_count < minimum_word_count
                if too_short or prompt_leaks:
                    # Do not persist the failed candidate.  The next (and only
                    # remaining) semantic attempt regenerates from the same
                    # approved plan, with no authority or scope expansion.
                    _mark_regeneration(
                        actual_word_count=actual_word_count,
                        prompt_leaks=prompt_leaks,
                    )
                    reasons: list[str] = []
                    if too_short:
                        reasons.append(
                            f"正文过短（{actual_word_count} 字，最低要求 {minimum_word_count} 字）"
                        )
                    if prompt_leaks:
                        reasons.append("出现规划/规则/审校指导信息")
                    semantic_error = ModelGatewayError(
                        f"第 {chapter_num} 章草稿未通过生成准入：{'；'.join(reasons)}",
                        is_transient=True,
                    )

            if semantic_error is None:
                break

            last_error = semantic_error
            if attempt >= max_attempts:
                if "draft_regeneration" in ctx:
                    # The long-form caller explicitly requested a single
                    # admission retry.  A second short/leaking draft is a
                    # terminal chapter-generation failure, not a signal for
                    # the job runner to keep spending on blind retries.
                    raise ModelGatewayError(str(semantic_error), is_transient=False)
                raise semantic_error
            _log.warning(
                "draft_semantic_retry | task=%s | attempt=%d/%d | error=%s",
                input_data.task_type.value,
                attempt,
                max_attempts,
                semantic_error,
            )
        else:
            if last_error is not None:
                raise last_error

        # ── Text validation (logging only, non-blocking) ──────────────────
        wc_result = check_word_count(text, target_word_count, tolerance=0.15)
        if wc_result["status"] != "pass":
            _log.warning(
                "draft_word_count_%s | actual=%d | target=%d | deviation=%.1f%%",
                wc_result["status"],
                wc_result["actual_count"],
                wc_result["target"],
                wc_result["deviation_pct"],
            )

        rev_result = check_revelation_density(text, max_revelations=2)
        if rev_result["status"] != "pass":
            _log.warning(
                "draft_revelation_density_%s | count=%d | max_allowed=%d",
                rev_result["status"],
                rev_result["revelation_count"],
                rev_result["max_allowed"],
            )

        return Draft(text=text, iteration=1)
