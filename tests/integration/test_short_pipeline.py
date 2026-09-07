"""Integration test: Short-mode pipeline end-to-end with MockAdapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.pipeline.short_runner import ShortStoryResult, ShortStoryRunner
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.story_kernel.store import StoryKernelStore


class TestShortPipeline:
    """End-to-end test of the short-mode pipeline using MockAdapter."""

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

    async def test_full_short_pipeline(
        self, runner: ShortStoryRunner, tmp_path: Path
    ) -> None:
        """Run the full Spec→Beats→Draft→Edit→Evaluate pipeline."""
        spec_input = {
            "title": "测试故事",
            "genre": "fantasy",
            "theme": "勇气与牺牲",
            "tone": "epic",
            "length_target": 3000,
            "language": "zh",
        }
        result = await runner.run(spec_input, project_id="test_short")

        # Check result types
        assert isinstance(result, ShortStoryResult)
        assert result.spec.title == "测试故事"
        assert result.spec.theme == "勇气与牺牲"
        assert len(result.beats.beats) > 0

        # Check final text is non-empty
        assert len(result.final_text) > 100

        # Check eval report
        assert result.eval_report is not None
        assert result.eval_report.overall_score > 0

        # Check files were persisted
        project_dir = tmp_path / "test_short"
        assert (project_dir / "spec.json").exists()
        assert (project_dir / "chapters" / "short_story.md").exists()
        assert (project_dir / "reports" / "eval_report.json").exists()
        assert (project_dir / "reports" / "short_quality_gate.json").exists()
        assert (project_dir / "story_kernel.db").exists()

        store = StoryKernelStore(project_dir / "story_kernel.db", wal_mode=False)
        try:
            kernel = await store.load_kernel("test_short")
        finally:
            await store.close()
        assert kernel.project_mode == "short"
        assert kernel.current_chapter == 1
        assert kernel.chapter_summaries[1]
        assert "short_story_final" in kernel.artifact_refs

    async def test_short_pipeline_different_spec(
        self, runner: ShortStoryRunner
    ) -> None:
        """Verify the pipeline works with varied spec inputs."""
        spec_input = {
            "title": "科幻测试",
            "genre": "scifi",
            "theme": "人工智能觉醒",
            "tone": "dark",
            "length_target": 5000,
            "language": "zh",
        }
        result = await runner.run(spec_input, project_id="test_scifi")
        assert result.spec.genre == "scifi"
        assert result.final_text  # non-empty

    async def test_adaptive_revision_rollout_path_persists_checkpoint(
        self, tmp_path: Path, runtime_settings
    ) -> None:
        adapter = MockAdapter()
        adaptive_runner = ShortStoryRunner(
            ModelRouter(adapters={"mock": adapter}, default_provider="mock"),
            PromptBuilder(),
            FileSystemStorage(tmp_path),
            settings=runtime_settings.model_copy(
                update={"short_adaptive_revision_enabled": True}
            ),
            max_edit_rounds=2,
        )

        result = await adaptive_runner.run(
            {
                "title": "自适应短篇",
                "genre": "mystery",
                "theme": "选择与代价",
                "tone": "restrained",
                "length_target": 1200,
                "language": "zh",
                "ending_style": "阶段性收束",
            },
            project_id="test_adaptive_short",
        )

        assert result.final_text
        checkpoint = (
            tmp_path
            / "test_adaptive_short"
            / "reports"
            / "short_revision_checkpoint.json"
        )
        assert checkpoint.exists()
        assert adaptive_runner._storage.load_json(checkpoint)["status"] == "complete"
