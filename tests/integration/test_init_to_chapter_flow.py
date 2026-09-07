"""Integration test: Init → Chapter 1 → Chapter 2 pipeline flow with MockAdapter."""

from __future__ import annotations

from pathlib import Path

from novel_forge.core.config import Settings
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.workspace.contracts import InitLongRequest, RunChapterRequest
from novel_forge.workspace.helpers.execution_runners import execute_init_long, execute_run_chapter
from novel_forge.workspace.runtime import RuntimeServices


def _make_services(tmp_path: Path) -> RuntimeServices:
    storage = FileSystemStorage(tmp_path)
    router = ModelRouter(
        adapters={"mock": MockAdapter()},
        default_provider="mock",
    )
    builder = PromptBuilder()
    settings = Settings(_env_file=None)
    return RuntimeServices(
        router=router,
        builder=builder,
        storage=storage,
        settings=settings,
    )


class TestInitToChapterFlow:
    async def test_init_long_creates_project(self, tmp_path: Path) -> None:
        services = _make_services(tmp_path)
        request = InitLongRequest(
            premise="记忆回收师发现自己的前半生被人改写",
            genre="scifi",
            tone="suspenseful",
            title="回收记忆的人",
            language="zh",
            characters_hint="记忆回收师、监察官",
            world_hint="近未来海港都市",
            total_chapters=12,
            words_per_chapter=3000,
        )

        result = await execute_init_long(services, request)
        init_result = result.result

        assert result.project_id is not None
        assert len(result.project_id) > 0

        assert init_result.story_bible is not None
        assert init_result.character_bible is not None
        assert init_result.outline is not None
        assert init_result.canon_state is not None

        assert init_result.canon_state.current_chapter == 0

        project_dir = tmp_path / result.project_id
        assert (project_dir / "spec.json").exists()
        assert (project_dir / "story_bible.json").exists()
        assert (project_dir / "character_bible.json").exists()
        assert (project_dir / "outline.json").exists()
        assert (project_dir / "canon" / "canon_current.json").exists()

    async def test_init_long_to_chapter_1(self, tmp_path: Path) -> None:
        """Init → chapter 1: canon_state.current_chapter advances to 1."""
        services = _make_services(tmp_path)

        init_request = InitLongRequest(
            premise="一位年轻魔法师在古老学院中发现禁忌魔法",
            genre="fantasy",
            tone="epic",
            title="魔法学院",
            language="zh",
            characters_hint="年轻魔法师、导师、反派",
            world_hint="中世纪魔法学院",
            total_chapters=10,
            words_per_chapter=3000,
        )
        init_result = await execute_init_long(services, init_request)
        project_id = init_result.project_id

        chapter_request = RunChapterRequest(
            project_id=project_id,
            chapter_number=1,
        )
        chapter_result = await execute_run_chapter(services, chapter_request)

        assert chapter_result.project_id == project_id
        chapter_output = chapter_result.result
        assert chapter_output is not None
        assert len(chapter_output.text) > 100
        assert chapter_output.meta is not None
        assert chapter_output.meta.chapter_number == 1

        canon_path = tmp_path / project_id / "canon" / "canon_current.json"
        assert canon_path.exists()
        canon_data = services.storage.load_json(canon_path)
        assert canon_data["current_chapter"] == 1

        chapters_dir = tmp_path / project_id / "chapters"
        chapter_files = list(chapters_dir.glob("chapter_001*"))
        assert len(chapter_files) > 0

    async def test_init_long_to_chapter_2(self, tmp_path: Path) -> None:
        """Init → ch1 → ch2: canon accumulates across chapters."""
        services = _make_services(tmp_path)

        init_request = InitLongRequest(
            premise="星际探险队在未知星球发现远古文明遗迹",
            genre="scifi",
            tone="mysterious",
            title="星际遗迹",
            language="zh",
            characters_hint="队长、科学家、工程师",
            world_hint="未知星球表面",
            total_chapters=15,
            words_per_chapter=3500,
        )
        init_result = await execute_init_long(services, init_request)
        project_id = init_result.project_id

        ch1_request = RunChapterRequest(
            project_id=project_id,
            chapter_number=1,
        )
        ch1_result = await execute_run_chapter(services, ch1_request)
        assert ch1_result.result.meta.chapter_number == 1

        canon_data_1 = services.storage.load_json(
            tmp_path / project_id / "canon" / "canon_current.json"
        )
        assert canon_data_1["current_chapter"] == 1

        ch2_request = RunChapterRequest(
            project_id=project_id,
            chapter_number=2,
        )
        ch2_result = await execute_run_chapter(services, ch2_request)
        assert ch2_result.result.meta.chapter_number == 2

        canon_data_2 = services.storage.load_json(
            tmp_path / project_id / "canon" / "canon_current.json"
        )
        assert canon_data_2["current_chapter"] == 2

        chapters_dir = tmp_path / project_id / "chapters"
        ch1_files = list(chapters_dir.glob("chapter_001*"))
        ch2_files = list(chapters_dir.glob("chapter_002*"))
        assert len(ch1_files) > 0
        assert len(ch2_files) > 0
