"""Integration tests for previous-exit to next-opening continuity artifacts."""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.pipeline.chapter_runner import ChapterRunner, ChapterRunnerConfig
from novel_forge.prompts.builder import PromptBuilder


@pytest.mark.asyncio
async def test_previous_chapter_exit_flows_into_next_opening(
    tmp_path: Path,
    runtime_settings,
) -> None:
    storage = FileSystemStorage(tmp_path)
    runner = ChapterRunner(
        ModelRouter(
            adapters={"mock": MockAdapter()},
            default_provider="mock",
        ),
        PromptBuilder(),
        storage,
        config=ChapterRunnerConfig(),
        settings=runtime_settings,
    )

    project_id = "bridge_chain"
    await runner.init_long(
        premise="一个关于时间裂缝的连续性测试故事",
        project_id=project_id,
        genre="scifi",
        tone="mysterious",
        total_chapters=3,
        words_per_chapter=1800,
    )

    chapter_1 = await runner.run_chapter(project_id, 1)
    chapter_2 = await runner.run_chapter(project_id, 2)

    project_dir = tmp_path / project_id
    assert chapter_1.chapter_exit_state is not None
    assert chapter_2.bridge is not None
    assert (project_dir / "states" / "chapter_001_exit_state.json").exists()
    assert (project_dir / "plans" / "chapter_002_bridge.json").exists()
    assert (project_dir / "reports" / "chapter_002_continuity.json").exists()
