"""End-to-end tests for outline polish: UI → pipeline → outline file validation.

Tests verify the complete data flow using mock adapters.
"""

from __future__ import annotations

import json

import pytest

from novel_forge.core.schemas.outline import ChapterOutline, StoryOutline


@pytest.fixture
def three_chapter_outline() -> StoryOutline:
    """Build a 3-chapter StoryOutline for E2E testing."""
    chapters = [
        ChapterOutline(
            chapter_number=i,
            title=f"第{i}章",
            goal=f"第{i}章原始概要",
            main_plot_points=[f"场景{i}-A", f"场景{i}-B"],
            involved_characters=["主角色"],
            subplot_focus="初始阶段" if i == 1 else "发展阶段" if i == 2 else "高潮阶段",
        )
        for i in range(1, 4)
    ]
    return StoryOutline(total_chapters=3, chapters=chapters)


@pytest.fixture
def single_chapter_outline() -> StoryOutline:
    """Build a 1-chapter StoryOutline for edge case testing."""
    chapter = ChapterOutline(chapter_number=1, goal="唯一章节概要")
    return StoryOutline(total_chapters=1, chapters=[chapter])


class TestPolishE2E:
    """End-to-end tests for the outline polish pipeline."""

    def test_normalize_suggestions_idempotent(
        self,
    ) -> None:
        """suggestion normalization should handle all input types."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _normalize_polish_suggestions,
        )

        # List
        assert _normalize_polish_suggestions(["A", "B", "C"]) == ["A", "B", "C"]
        # Semi-colon string
        assert _normalize_polish_suggestions("A；B；C") == ["A", "B", "C"]
        # With duplicates
        assert _normalize_polish_suggestions(["A", "A", "B"]) == ["A", "B"]
        # None
        assert _normalize_polish_suggestions(None) == []

    def test_merge_adjusted_preserves_unmodified(
        self, three_chapter_outline: StoryOutline,
    ) -> None:
        """Merging adjusted chapters should leave unmentioned chapters untouched."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _merge_adjusted_chapters,
        )

        original = three_chapter_outline
        adjusted_data = [
            {"chapter_number": 2, "goal": "第二章修改版"},
        ]
        result = _merge_adjusted_chapters(original, adjusted_data)

        # Chapter 1 and 3 unchanged (field-level check)
        assert result.chapters[0].goal == original.chapters[0].goal
        assert result.chapters[0].main_plot_points == original.chapters[0].main_plot_points
        assert result.chapters[2].goal == original.chapters[2].goal
        assert result.chapters[2].main_plot_points == original.chapters[2].main_plot_points
        # Chapter 2 updated
        assert result.chapters[1].goal == "第二章修改版"
        assert result.chapters[1].main_plot_points == original.chapters[1].main_plot_points  # unchanged field

    def test_merge_multiple_chapters(
        self, three_chapter_outline: StoryOutline,
    ) -> None:
        """Multiple adjusted chapters should all be applied correctly."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _merge_adjusted_chapters,
        )

        adjusted = [
            {"chapter_number": 1, "goal": "新第一章"},
            {"chapter_number": 3, "goal": "新第三章", "main_plot_points": ["最终对决"]},
        ]
        result = _merge_adjusted_chapters(three_chapter_outline, adjusted)

        assert result.chapters[0].goal == "新第一章"
        assert result.chapters[1].goal == "第2章原始概要"  # unchanged
        assert result.chapters[2].goal == "新第三章"
        assert result.chapters[2].main_plot_points == ["最终对决"]

    def test_polish_validates_output_schema(
        self, three_chapter_outline: StoryOutline,
    ) -> None:
        """Merged StoryOutline should pass Pydantic validation."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _merge_adjusted_chapters,
        )

        adjusted = [
            {"chapter_number": 1, "goal": "新概要", "involved_characters": ["新角色"]},
        ]
        result = _merge_adjusted_chapters(three_chapter_outline, adjusted)

        # Validate round-trip
        validated = StoryOutline.model_validate(result.model_dump(mode="json"))
        assert validated.total_chapters == 3
        assert validated.chapters[0].goal == "新概要"

    def test_filter_preserves_chapter_number_order(
        self, three_chapter_outline: StoryOutline,
    ) -> None:
        """Filter should preserve chapter number ordering."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _filter_chapters_by_range,
        )

        result = _filter_chapters_by_range(three_chapter_outline, (1, 3))
        assert [ch.chapter_number for ch in result] == [1, 2, 3]

    def test_outline_polish_step_has_correct_name(self, runtime_settings) -> None:
        """PolishOutlineStep should report step name correctly."""
        from novel_forge.gateway.adapters.mock import MockAdapter
        from novel_forge.gateway.router import ModelRouter
        from novel_forge.pipeline.steps.polish_outline_step import PolishOutlineStep
        from novel_forge.prompts.builder import PromptBuilder

        router = ModelRouter(
            adapters={"mock": MockAdapter()},
            default_provider="mock",
        )
        builder = PromptBuilder()
        step = PolishOutlineStep(router, builder, settings=runtime_settings)
        assert step.step_name == "polish_outline"

    def test_outline_polish_accepts_empty_outline(self) -> None:
        """PolishOutlineInput should accept None outline gracefully."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            PolishOutlineInput,
        )

        inp = PolishOutlineInput(story_outline=None, user_hint="test")
        assert inp.user_hint == "test"
        assert inp.story_outline is None

    def test_single_chapter_merge(
        self, single_chapter_outline: StoryOutline,
    ) -> None:
        """Edge case: single chapter outline should merge correctly."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _merge_adjusted_chapters,
        )

        adjusted = [{"chapter_number": 1, "goal": "唯一章节新概要"}]
        result = _merge_adjusted_chapters(single_chapter_outline, adjusted)
        assert result.chapters[0].goal == "唯一章节新概要"
        assert result.total_chapters == 1

    def test_empty_adjustments_noop(
        self, three_chapter_outline: StoryOutline,
    ) -> None:
        """Empty adjustments should return outline unchanged."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _merge_adjusted_chapters,
        )

        result = _merge_adjusted_chapters(three_chapter_outline, [])
        # Serialize both to JSON for comparison
        j1 = json.dumps(result.model_dump(mode="json"), sort_keys=True)
        j2 = json.dumps(three_chapter_outline.model_dump(mode="json"), sort_keys=True)
        assert j1 == j2
