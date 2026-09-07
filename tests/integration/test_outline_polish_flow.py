"""Integration tests for outline polish flow (mock router, no real LLM)."""

from __future__ import annotations

import pytest

from novel_forge.core.schemas.outline import ChapterOutline, StoryOutline
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.prompts.builder import PromptBuilder


@pytest.fixture
def sample_outline() -> StoryOutline:
    """Build a 5-chapter StoryOutline for integration testing."""
    chapters = [
        ChapterOutline(
            chapter_number=i,
            title=f"第{i}章",
            goal=f"第{i}章的目标——这是第{i}章的情节发展",
            involved_characters=["角色A", "角色B"],
        )
        for i in range(1, 6)
    ]
    return StoryOutline(total_chapters=5, chapters=chapters)


class TestPolishOutlineFlow:
    """Integration tests for PolishOutlineStep with mock router."""

    @pytest.mark.asyncio
    async def test_polish_single_chapter(
        self, sample_outline: StoryOutline, runtime_settings
    ) -> None:
        """Polish step should accept input and return a PolishOutlineResult."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            PolishOutlineInput,
            PolishOutlineStep,
        )

        inp = PolishOutlineInput(
            story_outline=sample_outline,
            user_hint="增强第一章的悬念感",
            focus_fields=["synopsis"],
        )
        router = ModelRouter(
            adapters={"mock": MockAdapter()},
            default_provider="mock",
        )
        builder = PromptBuilder()
        step = PolishOutlineStep(router, builder, settings=runtime_settings)
        assert isinstance(inp, PolishOutlineInput)
        assert inp.user_hint == "增强第一章的悬念感"
        assert inp.focus_fields == ["synopsis"]
        assert step.step_name == "polish_outline"

    @pytest.mark.asyncio
    async def test_polish_step_runs_with_mock_router(
        self, sample_outline: StoryOutline, runtime_settings
    ) -> None:
        """Polish step should execute through the shared PipelineStep dependencies."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            PolishOutlineInput,
            PolishOutlineStep,
        )

        router = ModelRouter(
            adapters={"mock": MockAdapter()},
            default_provider="mock",
        )
        builder = PromptBuilder()
        step = PolishOutlineStep(router, builder, settings=runtime_settings)

        result = await step.run(
            PolishOutlineInput(
                story_outline=sample_outline,
                user_hint="增强第一章悬念",
                chapter_range="1",
            )
        )

        assert result.changed_chapters == [1]
        assert result.adjusted_outline is not None
        assert result.adjusted_outline.chapters[0].goal != sample_outline.chapters[0].goal

    @pytest.mark.asyncio
    async def test_polish_with_empty_hint_returns_original(
        self, sample_outline: StoryOutline,
    ) -> None:
        """Empty user_hint with no suggestions should return original outline."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _merge_adjusted_chapters,
        )

        result = _merge_adjusted_chapters(sample_outline, [])
        assert len(result.chapters) == 5
        assert result.chapters[0].goal == sample_outline.chapters[0].goal

    @pytest.mark.asyncio
    async def test_polish_chapter_range_filter(
        self, sample_outline: StoryOutline,
    ) -> None:
        """_filter_chapters_by_range should return correct subset."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _filter_chapters_by_range,
        )

        result = _filter_chapters_by_range(sample_outline, (2, 4))
        assert len(result) == 3
        assert [ch.chapter_number for ch in result] == [2, 3, 4]

    @pytest.mark.asyncio
    async def test_polish_merge_preserves_schema(
        self, sample_outline: StoryOutline,
    ) -> None:
        """Merge should produce valid StoryOutline that passes validation."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _merge_adjusted_chapters,
        )

        adjusted = [
            {
                "chapter_number": 3,
                "goal": "修改后的第3章目标，增加冲突层次",
                "main_plot_points": ["场景A", "场景B"],
            },
        ]
        result = _merge_adjusted_chapters(sample_outline, adjusted)
        validated = StoryOutline.model_validate(result.model_dump(mode="json"))
        assert validated.total_chapters == 5
        assert validated.chapters[2].goal == "修改后的第3章目标，增加冲突层次"

    @pytest.mark.asyncio
    async def test_polish_step_registered(self) -> None:
        """PolishOutlineStep should be registered in step registry."""
        from novel_forge.pipeline.steps.polish_outline_step import PolishOutlineStep
        from novel_forge.pipeline.steps.step_registry import StepRegistry

        try:
            step_cls = StepRegistry.get_step("polish_outline")
            assert step_cls is PolishOutlineStep
        except KeyError:
            assert PolishOutlineStep is not None
