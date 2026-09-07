"""Typed context for the high-risk PATCH_CHAPTER render path."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from novel_forge.common.constants import TaskType
from novel_forge.prompts.context_types.base import PromptContextModel, validate_prompt_context


class PatchRepairDirectiveContext(PromptContextModel):
    """Sparse audit directive normalized for StrictUndefined templates."""

    target_window: str = ""
    repair_strategy: str = ""
    recommended_rewrite: str = ""
    required_context: list[str] = Field(default_factory=list)
    required_anchors: list[str] = Field(default_factory=list)
    validation_focus: list[str] = Field(default_factory=list)
    conflict_policy: str = ""
    repair_order: str = ""


class PatchAnchorContext(PromptContextModel):
    role: str = ""
    text: str = ""
    source: str = ""


class PatchPostconditionContext(PromptContextModel):
    description: str = ""


class PatchHistoryStrategyContext(PromptContextModel):
    preferred_strategy: str = ""
    reason: str = ""
    confidence: float | None = None
    warning: str = ""


class PatchHistoryIssueContext(PromptContextModel):
    chapter: int = 0
    chapter_number: int = 0
    issue_type: str = ""
    summary: str = ""
    lesson_learned: str = ""
    failure_pattern: str = ""


class PatchHistoryGuidanceContext(PromptContextModel):
    strategy_recommendation: PatchHistoryStrategyContext | None = None
    matched_issues: list[PatchHistoryIssueContext] = Field(default_factory=list)
    recommended_strategies: list[str] = Field(default_factory=list)
    avoid_strategies: list[str] = Field(default_factory=list)
    success_rate: float = 0.0
    total_attempts: int = 0
    warning: str = ""

    @field_validator("strategy_recommendation", mode="before")
    @classmethod
    def _empty_strategy_is_absent(cls, value: Any) -> Any:
        return None if value in (None, {}) else value


class PatchCausalLinkContext(PromptContextModel):
    previous_event: str = ""
    causal_mechanism: str = ""
    unresolved_question: str = ""
    character_profiles: list[Any] = Field(default_factory=list)
    memory_guidance: PatchHistoryGuidanceContext | None = None

    @field_validator("memory_guidance", mode="before")
    @classmethod
    def _empty_guidance_is_absent(cls, value: Any) -> Any:
        return None if value in (None, {}) else value


class PatchContinuityContext(PromptContextModel):
    previous_exit_state: str = ""
    must_carry_forward: list[str] = Field(default_factory=list)
    character_states: dict[str, Any] = Field(default_factory=dict)
    escalation_note: str = ""
    memory_guidance: PatchHistoryGuidanceContext | None = None

    @field_validator("memory_guidance", mode="before")
    @classmethod
    def _empty_guidance_is_absent(cls, value: Any) -> Any:
        return None if value in (None, {}) else value


class PatchBoundaryContext(PromptContextModel):
    focus: str = ""
    previous_chapter_ending: str = ""
    previous_chapter_ending_policy: str = ""
    opening_contract: str = ""
    closing_contract: str = ""


class PatchIssueWindowContext(PromptContextModel):
    """One fully normalized issue window consumed by patch_chapter.j2."""

    severity: str = "medium"
    location: str = ""
    summary: str = ""
    fix_suggestion: str = ""
    issue_type: str = ""
    evidence_quote: str = ""
    fix_mode: str = "replace"
    missing_anchors: list[PatchAnchorContext] = Field(default_factory=list)
    postconditions: list[PatchPostconditionContext] = Field(default_factory=list)
    repair_directive: PatchRepairDirectiveContext | None = None
    window_text: str = ""
    para_start: int = 0
    para_end: int = 0
    editable_para_start: int = 0
    editable_para_end: int = 0

    @field_validator("repair_directive", mode="before")
    @classmethod
    def _empty_directive_is_absent(cls, value: Any) -> Any:
        return None if value in (None, {}) else value


class PatchChapterContext(PromptContextModel):
    """Complete context contract for PATCH_CHAPTER prompt rendering."""

    chapter_number: int = 0
    issues_with_windows: list[PatchIssueWindowContext]
    causal_link: PatchCausalLinkContext | None = None
    continuity_context: PatchContinuityContext | None = None
    boundary_context: PatchBoundaryContext | None = None
    must_fix_summaries: list[str] = Field(default_factory=list)
    cognitive_constraints: list[dict[str, Any]] = Field(default_factory=list)
    style: str = "literary"
    style_profile: Any | None = None

    @field_validator("causal_link", "continuity_context", "boundary_context", mode="before")
    @classmethod
    def _empty_nested_context_is_absent(cls, value: Any) -> Any:
        return None if value in (None, {}) else value


def normalize_patch_chapter_context(
    context: dict[str, Any],
    *,
    source: str = "patch_chapter",
) -> dict[str, Any]:
    """Validate and materialize every optional nested template field."""

    validated = validate_prompt_context(
        PatchChapterContext,
        context,
        task_type=TaskType.PATCH_CHAPTER,
        source=source,
    )
    return validated.model_dump(mode="python")


__all__ = [
    "PatchBoundaryContext",
    "PatchCausalLinkContext",
    "PatchChapterContext",
    "PatchContinuityContext",
    "PatchIssueWindowContext",
    "PatchRepairDirectiveContext",
    "normalize_patch_chapter_context",
]
