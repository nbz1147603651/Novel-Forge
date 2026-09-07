"""Validation tests for high-risk typed prompt contexts."""

from __future__ import annotations

import warnings

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.exceptions import ValidationError as NovelForgeValidationError
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.prompts.context_types import (
    validate_book_consistency_context,
    validate_draft_stage_cards,
    validate_generate_config_context,
    validate_plan_outline_context,
)
from novel_forge.prompts.context_types.base import PromptContextModel, has_prompt_fields
from scripts.prompt_snapshot_cases import snapshot_case_by_id


def test_plan_outline_context_validates_full_and_fragment() -> None:
    full = snapshot_case_by_id("plan_outline_full").context
    fragment = snapshot_case_by_id("plan_outline_fragment").context

    validate_plan_outline_context(full, source="test.full")
    validate_plan_outline_context(fragment, source="test.fragment")


def test_plan_outline_context_rejects_missing_and_wrong_type() -> None:
    context = dict(snapshot_case_by_id("plan_outline_full").context)
    context.pop("spec")
    with pytest.raises(NovelForgeValidationError):
        validate_plan_outline_context(context, source="test.missing")

    context = dict(snapshot_case_by_id("plan_outline_full").context)
    context["total_chapters"] = "24"
    with pytest.raises(NovelForgeValidationError):
        validate_plan_outline_context(context, source="test.wrong_type")


def test_generate_config_context_validates_generate_polish_and_partial() -> None:
    validate_generate_config_context(
        snapshot_case_by_id("generate_config_short").context,
        task_type=TaskType.GENERATE_CONFIG,
        source="test.short",
    )
    validate_generate_config_context(
        snapshot_case_by_id("generate_config_long").context,
        task_type=TaskType.GENERATE_CONFIG,
        source="test.long",
    )
    validate_generate_config_context(
        snapshot_case_by_id("polish_config_partial").context,
        task_type=TaskType.POLISH_CONFIG,
        source="test.polish",
    )


def test_generate_config_context_rejects_missing_and_wrong_type() -> None:
    context = dict(snapshot_case_by_id("generate_config_short").context)
    context.pop("operation")
    with pytest.raises(NovelForgeValidationError):
        validate_generate_config_context(
            context,
            task_type=TaskType.GENERATE_CONFIG,
            source="test.missing",
        )

    context = dict(snapshot_case_by_id("polish_config_partial").context)
    context["focus_fields"] = "conflict_hint"
    with pytest.raises(NovelForgeValidationError):
        validate_generate_config_context(
            context,
            task_type=TaskType.POLISH_CONFIG,
            source="test.wrong_type",
        )


def test_book_consistency_context_validates_summary_full_text_and_dimension() -> None:
    validate_book_consistency_context(
        snapshot_case_by_id("book_consistency_summary").context,
        source="test.summary",
    )
    validate_book_consistency_context(
        snapshot_case_by_id("book_consistency_full_text").context,
        source="test.full_text",
    )
    validate_book_consistency_context(
        snapshot_case_by_id("book_consistency_active_dimension").context,
        task_type=TaskType.BOOK_CONSISTENCY_TIMELINE,
        source="test.dimension",
    )


def test_book_consistency_context_rejects_missing_and_wrong_type() -> None:
    context = dict(snapshot_case_by_id("book_consistency_summary").context)
    context.pop("chapter_summaries")
    with pytest.raises(NovelForgeValidationError):
        validate_book_consistency_context(context, source="test.missing")

    context = dict(snapshot_case_by_id("book_consistency_summary").context)
    context["analysis_mode"] = "deep"
    with pytest.raises(NovelForgeValidationError):
        validate_book_consistency_context(context, source="test.wrong_type")


def test_draft_stage_cards_validate_after_builder_normalization() -> None:
    context = PromptBuilder._with_common_optional_defaults(
        snapshot_case_by_id("draft_chapter_stage_cards").context
    )

    validate_draft_stage_cards(context["stage_cards"], source="test.draft")


def test_draft_stage_cards_reject_raw_missing_and_wrong_type() -> None:
    with pytest.raises(NovelForgeValidationError):
        validate_draft_stage_cards({}, source="test.missing")

    context = PromptBuilder._with_common_optional_defaults(
        snapshot_case_by_id("draft_chapter_regular").context
    )
    stage_cards = dict(context["stage_cards"])
    stage_cards["chapter"] = []
    with pytest.raises(NovelForgeValidationError):
        validate_draft_stage_cards(stage_cards, source="test.wrong_type")


def test_has_prompt_fields_reads_pydantic_fields_from_model_class() -> None:
    class DemoPromptContext(PromptContextModel):
        title: str

    context = DemoPromptContext(title="魂玉")
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        assert has_prompt_fields(context, ("title",))

    assert not any("model_fields" in str(item.message) for item in captured)
