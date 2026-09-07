"""High-risk prompt context models and validators."""

from __future__ import annotations

from novel_forge.prompts.context_types.book_consistency import (
    BookConsistencyContext,
    validate_book_consistency_context,
)
from novel_forge.prompts.context_types.chapter_flow import (
    CHAPTER_FLOW_PROMPT_CONTEXT_MODELS,
    CHAPTER_FLOW_PROMPT_TASKS,
    ChapterFlowPromptContext,
    ChapterPositionPromptContext,
    normalize_chapter_flow_prompt_context,
)
from novel_forge.prompts.context_types.generate_config import (
    GenerateConfigContext,
    validate_generate_config_context,
)
from novel_forge.prompts.context_types.patch_chapter import (
    PatchChapterContext,
    PatchIssueWindowContext,
    PatchRepairDirectiveContext,
    normalize_patch_chapter_context,
)
from novel_forge.prompts.context_types.plan_outline import (
    PlanOutlineContext,
    validate_plan_outline_context,
)
from novel_forge.prompts.context_types.stage_cards import (
    DraftChapterStageCards,
    validate_draft_stage_cards,
)

__all__ = [
    "BookConsistencyContext",
    "CHAPTER_FLOW_PROMPT_CONTEXT_MODELS",
    "CHAPTER_FLOW_PROMPT_TASKS",
    "ChapterFlowPromptContext",
    "ChapterPositionPromptContext",
    "DraftChapterStageCards",
    "GenerateConfigContext",
    "PatchChapterContext",
    "PatchIssueWindowContext",
    "PatchRepairDirectiveContext",
    "PlanOutlineContext",
    "normalize_patch_chapter_context",
    "normalize_chapter_flow_prompt_context",
    "validate_book_consistency_context",
    "validate_draft_stage_cards",
    "validate_generate_config_context",
    "validate_plan_outline_context",
]
