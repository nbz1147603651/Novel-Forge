"""PolishStep — literary polish pass for publication-quality prose."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.parsing.text_utils import extract_text_content
from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.pipeline.steps.base import PipelineStep
from novel_forge.pipeline.steps.step_registry import register_step

_logger = logging.getLogger(__name__)


@dataclass
class PolishInput:
    """Input for the polish step."""

    chapter_number: int
    chapter_title: str
    chapter_text: str
    tone: str = ""
    genre: str = ""
    pov_character: str = ""
    eval_summary: str = ""
    repair_hints: str = ""
    continuity_notes: str = ""
    style: str = "literary"
    style_profile: dict[str, Any] | None = None
    target_word_count: int = 0
    word_count_guidance: str = ""
    kernel_context: dict[str, Any] | None = None
    chapter_source_slice: Any | None = None
    total_chapters: int = 0
    is_last_chapter: bool = False
    chapter_position: dict[str, Any] | None = None
    editorial: dict[str, Any] | None = None
    """Editorial contract projection for polish-stage constraints."""
    quality: dict[str, Any] | None = None
    """Reading power capsule for polish-stage hook/payoff preservation."""
    memory: dict[str, Any] | None = None
    """Memory hints (expression_channel_records etc.) for polish-stage cooling."""
    style_capsule: dict[str, Any] | None = None
    """Style capsule (summary, modules, banned_phrases) for polish-stage reference."""


@dataclass
class PolishResult:
    """Result of the polish step."""

    polished_text: str
    original_word_count: int
    polished_word_count: int
    warnings: list[str] = field(default_factory=list)


@register_step("polish")
class PolishStep(PipelineStep[PolishInput, PolishResult]):
    """Apply literary polish to a draft that has already passed quality gates."""

    @property
    def step_name(self) -> str:
        return "polish"

    async def _execute(self, input_data: PolishInput) -> PolishResult:
        original_wc = count_chapter_words(input_data.chapter_text)
        max_tokens = self._dynamic_max_tokens(
            TaskType.POLISH_CHAPTER,
            len(input_data.chapter_text),
            prompt_overhead=3000,
            safety_margin=0.80,
            min_tokens=4096,
        )

        ctx: dict[str, Any] = {}
        if input_data.kernel_context is not None:
            ctx.update(input_data.kernel_context)
        ctx.update(
            {
                "chapter_number": input_data.chapter_number,
                "chapter_title": input_data.chapter_title,
                "chapter_text": input_data.chapter_text,
                "tone": input_data.tone,
                "genre": input_data.genre,
                "pov_character": input_data.pov_character,
                "eval_summary": input_data.eval_summary,
                "repair_hints": input_data.repair_hints,
                "continuity_notes": input_data.continuity_notes,
                "style": input_data.style,
                "style_profile": input_data.style_profile,
                "target_word_count": input_data.target_word_count,
                "word_count_guidance": input_data.word_count_guidance,
                "total_chapters": input_data.total_chapters,
                "is_last_chapter": input_data.is_last_chapter,
                "chapter_position": input_data.chapter_position or {},
            }
        )
        stage_cards: dict[str, Any] = {"stage": "polish"}
        if input_data.chapter_source_slice is not None:
            from novel_forge.pipeline.long.services.context.source_artifacts import (
                project_stage_source_cards,
            )

            stage_cards["source"] = project_stage_source_cards(
                input_data.chapter_source_slice,
                stage="polish",
            )
        if input_data.chapter_position:
            stage_cards["chapter"] = {
                "chapter_number": input_data.chapter_number,
                "total_chapters": input_data.total_chapters,
                "is_last_chapter": input_data.is_last_chapter,
                "chapter_position": input_data.chapter_position,
            }
        if input_data.editorial:
            stage_cards["editorial"] = input_data.editorial
        if input_data.quality:
            stage_cards["quality"] = input_data.quality
        if input_data.memory:
            stage_cards["memory"] = input_data.memory
        if input_data.style_capsule:
            stage_cards["style"] = input_data.style_capsule
        if len(stage_cards) > 1:
            ctx["stage_cards"] = stage_cards

        try:
            response_content = await self._call_with_retry(
                TaskType.POLISH_CHAPTER,
                ctx,
                max_tokens=max_tokens,
                temperature=self.settings.temp_edit_chapter,
            )
            # _call_with_retry returns parsed dict or string content
            if isinstance(response_content, dict):
                polished_text = response_content.get("polished_text", "")
            else:
                polished_text = extract_text_content(response_content)
        except Exception as exc:
            _logger.warning(
                "LLM chapter polish failed (chapter %d), using original text: %s",
                input_data.chapter_number,
                exc,
            )
            polished_text = input_data.chapter_text

        if not polished_text:
            polished_text = input_data.chapter_text

        polished_wc = count_chapter_words(polished_text)

        return PolishResult(
            polished_text=polished_text,
            original_word_count=original_wc,
            polished_word_count=polished_wc,
        )
