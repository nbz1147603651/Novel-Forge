"""EditStep — iteratively edits a draft with bounded rounds."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.core.format_contracts import TextOutputContractError
from novel_forge.core.parsing.text_utils import extract_text_content
from novel_forge.core.schemas.draft import EditResult
from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.pipeline.steps.base import PipelineStep
from novel_forge.pipeline.steps.prompt_diagnostics import log_prompt_diagnostics
from novel_forge.pipeline.steps.step_registry import register_step

_log = logging.getLogger(__name__)


def _is_soft_edit_model_error(exc: ModelGatewayError) -> bool:
    """Return True for invalid/truncated edit text that should preserve the draft."""
    if isinstance(exc.__cause__, TextOutputContractError):
        return True
    return "TEXT 流式输出被截断且过短" in str(exc)


@dataclass
class EditInput:
    """Input for edit step."""

    task_type: TaskType  # EDIT or EDIT_CHAPTER
    draft_text: str
    context: dict[str, Any]  # additional template variables
    max_rounds: int = 2
    iteration: int = 1
    prior_messages: list[dict[str, str]] | None = None
    capture_raw: list[str] | None = None
    reading_power_hint: dict[str, Any] | None = None
    kernel_context: dict[str, Any] | None = None  # StoryKernel field slices (from ContextComposer)
    chapter_source_slice: Any | None = None


@register_step("edit")
class EditStep(PipelineStep[EditInput, EditResult]):
    """Draft → model → EditResult (single round)."""

    @property
    def step_name(self) -> str:
        return "edit"

    async def _execute(self, input_data: EditInput) -> EditResult:
        # Calculate safe max_tokens considering draft length and prompt overhead
        draft_length = len(input_data.draft_text)
        if input_data.task_type == TaskType.EDIT_CHAPTER:
            max_tokens = self._dynamic_max_tokens(
                input_data.task_type,
                draft_length,
                prompt_overhead=3000,  # Edit prompts include alignment/continuity reports
                safety_margin=0.80,
                min_tokens=6144,
            )
        else:
            max_tokens = self._dynamic_max_tokens(
                input_data.task_type,
                draft_length,
                prompt_overhead=2000,
                safety_margin=0.80,
                min_tokens=4096,
            )

        temperature = (
            getattr(self.settings, "temp_edit_chapter", 0.7)
            if input_data.task_type == TaskType.EDIT_CHAPTER
            else getattr(self.settings, "temp_edit", 0.7)
        )

        ctx: dict[str, Any] = {}
        if input_data.kernel_context is not None:
            ctx.update(input_data.kernel_context)
        ctx.update(input_data.context)
        if input_data.chapter_source_slice is not None:
            from novel_forge.pipeline.long.services.context.source_artifacts import (
                project_stage_source_cards,
            )

            stage_cards = dict(ctx.get("stage_cards") or {})
            stage_cards.setdefault(
                "source",
                project_stage_source_cards(input_data.chapter_source_slice, stage="repair"),
            )
            ctx["stage_cards"] = stage_cards
        ctx["draft_text"] = input_data.draft_text
        ctx["iteration"] = input_data.iteration
        # Merge reading_power_hint into context if provided
        if input_data.reading_power_hint is not None:
            ctx["reading_power_hint"] = input_data.reading_power_hint

        request = self._builder.build(
            input_data.task_type,
            ctx,
            max_tokens=max_tokens,
            temperature=temperature,
            prior_messages=input_data.prior_messages,
            multi_turn=input_data.prior_messages is not None,
        )
        if input_data.task_type == TaskType.EDIT_CHAPTER:
            log_prompt_diagnostics(
                _log,
                event="edit_prompt_diagnostics",
                request=request,
                context=ctx,
                settings=self.settings,
                enabled_attr="long_prompt_diagnostics_enabled",
                warn_attr="long_prompt_warn_tokens",
                chapter=input_data.context.get("chapter_number", "?"),
                on_event=self._on_step_event,
            )
        # Route through the streaming path for TEXT_ONLY edit tasks so the
        # desktop UI receives llm_stream_* events.  ``_call_with_retry`` returns
        # the validated text; response metadata (finish_reason / token counts)
        # is no longer available, so truncation is inferred from text length.
        try:
            result_text = await self._call_with_retry(
                input_data.task_type,
                ctx,
                max_tokens=max_tokens,
                temperature=temperature,
                prior_messages=input_data.prior_messages,
                multi_turn=input_data.prior_messages is not None,
                validate_text_output=True,
            )
        except ModelGatewayError as exc:
            # Streaming path raises ModelGatewayError when the TEXT output
            # contract is violated (e.g. JSON instead of prose).  Preserve the
            # original soft-fail behavior: keep the previous draft.  Other
            # gateway failures must propagate so outer retry/error handling can
            # see real provider/network/auth failures.
            if not _is_soft_edit_model_error(exc):
                raise
            _log.warning(
                "edit_text_contract_guard | round=%d | error=%s | keeping previous draft",
                input_data.iteration,
                exc,
            )
            return EditResult(
                revised_text=input_data.draft_text,
                edit_notes=[
                    f"Round {input_data.iteration} edit skipped: invalid TEXT output; "
                    "kept previous draft"
                ],
                iteration=input_data.iteration,
            )

        if input_data.capture_raw is not None and not input_data.capture_raw:
            input_data.capture_raw.append(str(result_text))

        text = extract_text_content(str(result_text))
        # Without the ModelResponse metadata (streaming path returns text only),
        # infer truncation from text length vs the token budget.  A Chinese
        # token is roughly 1.5 chars; if the output fills ≥97% of the budget
        # it is likely truncated.
        text_char_len = len(text)
        is_truncated = (
            max_tokens > 0
            and text_char_len > 0
            and text_char_len >= max_tokens * 1.5 * 0.97
        )

        # ── Empty content fast-fail ─────────────────────────────────────────────
        if not text or not text.strip():
            if is_truncated:
                _log.warning(
                    "edit_empty_truncation_guard | round=%d | max_tokens=%d | "
                    "text_chars=%d | keeping previous draft",
                    input_data.iteration,
                    max_tokens,
                    text_char_len,
                )
                return EditResult(
                    revised_text=input_data.draft_text,
                    edit_notes=[
                        f"Round {input_data.iteration} edit skipped: empty truncated output; "
                        "kept previous draft"
                    ],
                    iteration=input_data.iteration,
                )
            raise ModelGatewayError(
                f"编辑第 {input_data.iteration} 轮返回空内容"
                f"（max_tokens={max_tokens}, text_chars={text_char_len}）",
                is_transient=True,
            )

        # ── Truncation guard ──────────────────────────────────────────────
        # If the model hit the token limit, the edit output is likely
        # truncated and should NOT replace the (longer) previous draft.
        draft_len = count_chapter_words(input_data.draft_text)
        text_len = count_chapter_words(text)
        is_regressed = draft_len > 0 and text_len < draft_len * 0.5
        edit_notes = [f"Round {input_data.iteration} edit completed"]

        if is_truncated or is_regressed:
            fallback_reason = "truncated" if is_truncated else "regressed"
            _log.warning(
                "edit_truncation_guard | round=%d | "
                "draft_chars=%d | edit_chars=%d | keeping previous draft",
                input_data.iteration,
                draft_len,
                text_len,
            )
            text = input_data.draft_text
            edit_notes = [
                f"Round {input_data.iteration} edit skipped: {fallback_reason} output; "
                "kept previous draft"
            ]

        return EditResult(
            revised_text=text,
            edit_notes=edit_notes,
            iteration=input_data.iteration,
        )
