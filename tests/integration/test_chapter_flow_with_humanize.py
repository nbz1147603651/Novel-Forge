"""Integration test: Humanize layer in the long-mode chapter pipeline.

Tests that the humanize scan step:
1. Runs when enabled via settings
2. Respects the config disable flag
3. Does not introduce narrative regression with an empty report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.schemas.humanize import HumanizeReport
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.pipeline.chapter_runner import ChapterRunner, ChapterRunnerConfig
from novel_forge.pipeline.long.stages.humanize_layer import HumanizeLayerResult
from novel_forge.prompts.builder import PromptBuilder


class TestChapterFlowWithHumanize:
    """Verify humanize layer integration in the chapter pipeline."""

    @pytest.fixture
    def storage(self, tmp_path: Path) -> FileSystemStorage:
        return FileSystemStorage(tmp_path)

    @pytest.fixture
    def _make_runner(self, storage: FileSystemStorage):
        """Factory to build a ChapterRunner with custom settings."""

        def _factory(settings: Settings) -> ChapterRunner:
            adapter = MockAdapter()
            router = ModelRouter(
                adapters={"mock": adapter},
                default_provider="mock",
            )
            return ChapterRunner(
                router,
                PromptBuilder(),
                storage,
                config=ChapterRunnerConfig(),
                settings=settings,
            )

        return _factory

    async def test_chapter_flow_with_humanize_enabled(
        self,
        _make_runner: Any,
        storage: FileSystemStorage,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Full pipeline with Humanize enabled emits humanize_scan step."""
        settings = Settings(_env_file=None)
        settings.humanize_enabled = True
        runner = _make_runner(settings)
        project_id = "test_humanize_enabled"

        # Init project
        await runner.init_long(
            premise="一个关于时间旅行的故事",
            project_id=project_id,
            genre="scifi",
            tone="mysterious",
            total_chapters=3,
            words_per_chapter=1500,
        )

        # Monkeypatch run_humanize_layer to return a deterministic result
        # that simulates a successful humanize pass (report with findings, no text change).
        async def _mock_run_humanize_layer(
            runner: Any,
            bundle: Any,
            packet: Any,
            current_text: str,
            chapter_number: int,
            trace: Any,
            *,
            settings: Any | None = None,
        ) -> HumanizeLayerResult:
            report = HumanizeReport(
                chapter_number=chapter_number,
                total_hits=1,
                hits_by_category={"AI 句式": 1},
                critical_hits=0,
                pattern_hits=[],
                humanize_score=8.5,
                summary="Mock humanize: 检测到轻微 AI 痕迹，无需修复。",
            )
            return HumanizeLayerResult(
                current_text=current_text,
                report=report,
                patches_applied=0,
                skipped_reason="",
            )

        monkeypatch.setattr(
            "novel_forge.pipeline.long.chapter_flow.run_humanize_layer",
            _mock_run_humanize_layer,
        )

        # Track pipeline steps
        steps: list[str] = []
        runner._on_step = lambda step, _data: steps.append(step)

        # Run chapter 1
        chapter_result = await runner.run_chapter(project_id, chapter_number=1)

        # Verify chapter result is valid
        assert chapter_result.meta.chapter_number == 1
        assert chapter_result.meta.word_count > 0
        assert len(chapter_result.text) > 100

        # Verify humanize_scan step was emitted
        assert "humanize_scan" in steps

    async def test_chapter_flow_with_humanize_disabled(
        self,
        _make_runner: Any,
        storage: FileSystemStorage,
        tmp_path: Path,
    ) -> None:
        """Full pipeline with Humanize disabled skips the humanize layer."""
        settings = Settings(_env_file=None)
        settings.humanize_enabled = False  # explicit, though default
        runner = _make_runner(settings)
        project_id = "test_humanize_disabled"

        await runner.init_long(
            premise="一个关于时间旅行的故事",
            project_id=project_id,
            genre="scifi",
            tone="mysterious",
            total_chapters=3,
            words_per_chapter=1500,
        )

        steps: list[str] = []
        runner._on_step = lambda step, _data: steps.append(step)

        chapter_result = await runner.run_chapter(project_id, chapter_number=1)

        # Chapter still generates successfully
        assert chapter_result.meta.chapter_number == 1
        assert chapter_result.meta.word_count > 0

        # Humanize step was NOT emitted (layer skipped)
        assert "humanize_scan" not in steps

    async def test_chapter_flow_humanize_no_regression(
        self,
        _make_runner: Any,
        storage: FileSystemStorage,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Humanize returning empty report does not regress chapter quality."""
        settings = Settings(_env_file=None)
        settings.humanize_enabled = True
        runner = _make_runner(settings)
        project_id = "test_humanize_no_regression"

        await runner.init_long(
            premise="一个关于时间旅行的故事",
            project_id=project_id,
            genre="scifi",
            tone="mysterious",
            total_chapters=3,
            words_per_chapter=1500,
        )

        # Monkeypatch to return empty report (no AI patterns found)
        async def _mock_empty_humanize(
            runner: Any,
            bundle: Any,
            packet: Any,
            current_text: str,
            chapter_number: int,
            trace: Any,
            *,
            settings: Any | None = None,
        ) -> HumanizeLayerResult:
            return HumanizeLayerResult(
                current_text=current_text,
                report=None,
                patches_applied=0,
                skipped_reason="",
            )

        monkeypatch.setattr(
            "novel_forge.pipeline.long.chapter_flow.run_humanize_layer",
            _mock_empty_humanize,
        )

        steps: list[str] = []
        runner._on_step = lambda step, _data: steps.append(step)

        chapter_result = await runner.run_chapter(project_id, chapter_number=1)

        # Chapter passes quality gates
        assert chapter_result.meta.chapter_number == 1
        assert chapter_result.meta.word_count > 0
        assert chapter_result.eval_report is not None
        assert chapter_result.eval_report.overall_score > 0
        assert chapter_result.alignment_report is not None
        assert chapter_result.alignment_report.alignment_score >= 0
        assert chapter_result.canon_delta is not None

        # Humanize layer was invoked (step tracked by the mock)
        # Note: When report is None, the on_step is NOT called in the real code,
        # so we verify via text quality instead.
        assert len(chapter_result.text) > 100
