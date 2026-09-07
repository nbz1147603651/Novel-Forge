"""Smoke tests — short + long pipeline end-to-end with MockAdapter.

These are lightweight regression smoke tests that verify the full pipeline
chains produce structurally valid output without real LLM calls.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.chapter_runner import ChapterRunner, ChapterRunnerConfig
from novel_forge.pipeline.short_runner import ShortStoryResult, ShortStoryRunner
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.story_kernel.store import StoryKernelStore

os.environ.setdefault("NOVEL_FORGE_MEMORY_USE_MOCK_EMBEDDINGS", "true")


class TestSmokeShort:
    """Short-story pipeline smoke test."""

    @pytest.fixture
    def runner(self, tmp_path: Path, runtime_settings) -> ShortStoryRunner:
        adapter = MockAdapter()
        router = ModelRouter(
            adapters={"mock": adapter},
            default_provider="mock",
        )
        builder = PromptBuilder()
        storage = FileSystemStorage(tmp_path)
        return ShortStoryRunner(
            router,
            builder,
            storage,
            settings=runtime_settings,
            max_edit_rounds=1,
        )

    @pytest.mark.timeout(120)
    async def test_short_story_smoke(
        self, runner: ShortStoryRunner, tmp_path: Path
    ) -> None:
        spec_input = {
            "title": "烟雾测试短篇",
            "genre": "fantasy",
            "theme": "勇气与牺牲",
            "tone": "epic",
            "length_target": 3000,
            "language": "zh",
            "characters_hint": "一位年轻的魔法师",
            "world_hint": "中世纪奇幻世界",
        }
        result = await runner.run(spec_input, project_id="smoke_short")

        assert isinstance(result, ShortStoryResult)
        assert result.final_text, "final_text should be non-empty"
        assert len(result.final_text) > 100, "final_text should be substantial"

        assert result.eval_report is not None, "eval_report should exist"
        assert result.eval_report.overall_score > 0, "overall_score should be positive"

        if result.creative_summary is not None:
            assert hasattr(result.creative_summary, "characters")
            # MockAdapter returns no SHORT_CREATIVE_SUMMARY — characters may be empty
            if result.creative_summary.characters:
                assert len(result.creative_summary.characters) > 0

        # Verify beats were generated and are non-empty
        assert result.beats is not None, "beats should exist"
        assert len(result.beats.beats) > 0, "beats list should be non-empty"

        project_dir = tmp_path / "smoke_short"
        assert (project_dir / "spec.json").exists()
        assert (project_dir / "chapters" / "short_story.md").exists()


class TestSmokeLong:
    """Long-form chapter pipeline smoke test."""

    @pytest.fixture
    def storage(self, tmp_path: Path) -> FileSystemStorage:
        return FileSystemStorage(tmp_path)

    @pytest.fixture
    def runner(self, storage: FileSystemStorage, runtime_settings) -> ChapterRunner:
        adapter = MockAdapter()
        router = ModelRouter(
            adapters={"mock": adapter},
            default_provider="mock",
        )
        builder = PromptBuilder()
        return ChapterRunner(
            router,
            builder,
            storage,
            config=ChapterRunnerConfig(),
            settings=runtime_settings,
        )

    @pytest.mark.timeout(120)
    async def test_long_chapter_smoke(
        self, runner: ChapterRunner, storage: FileSystemStorage, tmp_path: Path
    ) -> None:
        project_id = "smoke_long"

        init_result = await runner.init_long(
            premise="一个关于时间旅行的故事",
            project_id=project_id,
            genre="scifi",
            tone="mysterious",
            total_chapters=3,
            words_per_chapter=3000,
        )
        assert init_result.project_id == project_id
        assert init_result.outline.total_chapters >= 1

        chapter_result = await runner.run_chapter(project_id, chapter_number=1)

        assert init_result.story_bible is not None
        assert init_result.outline is not None
        assert chapter_result is not None

        assert chapter_result.text, "chapter text should be non-empty"
        assert len(chapter_result.text) > 100, "chapter text should be substantial"

        assert chapter_result.eval_report is not None, "eval_report should exist"
        assert chapter_result.eval_report.overall_score > 0, "overall_score should be positive"

        assert chapter_result.meta.chapter_number == 1
        assert chapter_result.meta.word_count > 0
        assert chapter_result.canon_delta is not None
        assert chapter_result.creative_report is not None
        assert chapter_result.alignment_report is not None

        project_dir = tmp_path / project_id
        assert (project_dir / "chapters" / "chapter_001.md").exists()
        assert (project_dir / "reports" / "chapter_001_alignment.json").exists()

    @pytest.mark.timeout(180)
    async def test_long_chapter_two_chapters_canon_accumulates(
        self, runner: ChapterRunner, storage: FileSystemStorage, tmp_path: Path
    ) -> None:
        """Two-chapter run should accumulate canon state correctly across chapters."""
        project_id = "smoke_long_2ch"

        init_result = await runner.init_long(
            premise="一个关于时间旅行的故事",
            project_id=project_id,
            genre="scifi",
            tone="mysterious",
            total_chapters=3,
            words_per_chapter=3000,
        )
        assert init_result.project_id == project_id

        # Run chapter 1
        chapter1 = await runner.run_chapter(project_id, chapter_number=1)
        assert chapter1 is not None
        assert chapter1.text, "chapter 1 text should be non-empty"
        assert chapter1.canon_delta is not None, "chapter 1 canon_delta should exist"

        # Run chapter 2
        chapter2 = await runner.run_chapter(project_id, chapter_number=2)
        assert chapter2 is not None
        assert chapter2.text, "chapter 2 text should be non-empty"
        assert chapter2.canon_delta is not None, "chapter 2 canon_delta should exist"

        # Verify story kernel state accumulated across chapters
        project_dir = storage.project_dir(project_id)
        layout = ProjectLayout(project_dir)
        kernel_store = StoryKernelStore(layout.story_kernel_db_path)
        try:
            kernel = await kernel_store.load_kernel(project_id)
            assert kernel.current_chapter >= 2, "story kernel should reflect at least 2 chapters"
            snapshots = kernel_store.list_snapshots()
        finally:
            await kernel_store.close()

        # Verify both chapters exist on disk
        assert (project_dir / "chapters" / "chapter_001.md").exists()
        assert (project_dir / "chapters" / "chapter_002.md").exists()

        # Verify story kernel snapshots exist for init and both chapters.
        assert {0, 1, 2}.issubset(set(snapshots)), (
            f"story kernel should have snapshots for init/ch1/ch2, got {snapshots}"
        )
