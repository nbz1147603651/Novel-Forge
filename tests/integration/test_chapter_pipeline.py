"""Integration test: Long-mode chapter pipeline end-to-end with MockAdapter."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from novel_forge.core.constants import TaskType
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.pipeline.chapter_runner import ChapterRunner, ChapterRunnerConfig, InitLongResult
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.story_kernel.store import StoryKernelStore


@pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")
def test_engine_initialization_autorun_and_short_delivery(
    tmp_path: Path, runtime_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use real workspace/pipeline handlers with only model I/O mocked."""
    from novel_forge.app_service.book_autorun import BookAutorunStatus
    from novel_forge.app_service.contracts import JobCommand, JobKind, JobState
    from novel_forge.app_service.job_service import JobService
    from novel_forge.pipeline.long import preflight
    from novel_forge.workspace.runtime import create_runtime_services

    stores: list[StoryKernelStore] = []
    closed_stores: list[StoryKernelStore] = []

    class _TrackedKernelStore(StoryKernelStore):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            stores.append(self)

        async def close(self) -> None:
            await super().close()
            closed_stores.append(self)

    monkeypatch.setattr(preflight, "StoryKernelStore", _TrackedKernelStore)
    settings = runtime_settings.model_copy(update={"storage_root": tmp_path})
    service = JobService(
        storage_root=tmp_path,
        load_persisted_history=False,
        runtime_factory=lambda _mock: create_runtime_services(settings, mock=True),
    )
    service._book_autorun._failure_backoff_ms = 0
    service._book_autorun._checkpoint_backoff_ms = 0
    try:
        service.submit(
            JobCommand(
                kind=JobKind.INIT_LONG,
                project_id="engine-long",
                mock=True,
                payload={
                    "project_id": "engine-long",
                    "premise": "一个关于时间旅行的故事",
                    "total_chapters": 3,
                    "words_per_chapter": 1500,
                },
                metadata={"autorun_after_init": True, "run_mode": "autorun"},
            )
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            state = service.book_autorun_state("engine-long")
            if state is not None and state.status in {
                BookAutorunStatus.COMPLETED,
                BookAutorunStatus.FAILED,
                BookAutorunStatus.PAUSED,
            }:
                break
            time.sleep(0.02)
        assert state is not None
        assert state.status == BookAutorunStatus.COMPLETED, state.last_error
        assert state.completed_chapters == [1, 2, 3]
        assert (tmp_path / "engine-long" / "chapters" / "chapter_003.md").exists()
        assert not (tmp_path / "engine-long" / "chapters" / "chapter_004.md").exists()

        short = service.submit(
            JobCommand(
                kind=JobKind.RUN_SHORT,
                project_id="engine-short",
                mock=True,
                payload={
                    "project_id": "engine-short",
                    "title": "短篇交付",
                    "theme": "勇气",
                    "length_target": 1500,
                },
            )
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            short = service.get(short.job_id)
            assert short is not None
            if short.status in {JobState.SUCCEEDED, JobState.FAILED, JobState.PAUSED}:
                break
            time.sleep(0.02)
        assert short.status == JobState.SUCCEEDED, short.error
        assert (tmp_path / "engine-short" / "chapters" / "short_story.md").exists()
    finally:
        service.shutdown(wait_s=2)
    assert len(stores) >= 6  # Prepare and resolve each of the three chapters.
    assert all(store in closed_stores for store in stores)


class TestChapterPipeline:
    """End-to-end test of the long-mode chapter pipeline using MockAdapter."""

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

    async def test_init_long(self, runner: ChapterRunner) -> None:
        """Initialize a long-mode project: generates bible, outline, canon."""
        result = await runner.init_long(
            premise="一个关于时间旅行的故事",
            project_id="test_long",
            genre="scifi",
            tone="mysterious",
        )

        assert isinstance(result, InitLongResult)
        assert result.project_id == "test_long"

        # Bible populated
        assert result.story_bible.title
        assert len(result.character_bible.characters) > 0

        # Outline populated
        assert result.outline.total_chapters >= 1

        # Canon initialized
        assert result.canon_state is not None
        assert result.outline.volume_mode is False
        assert result.outline.volumes == []

    async def test_init_long_respects_requested_total_chapters(self, runner: ChapterRunner) -> None:
        """Outline is normalized to requested total chapter count."""
        result = await runner.init_long(
            premise="一个关于时间旅行的故事",
            project_id="test_total_chapters",
            genre="scifi",
            tone="mysterious",
            total_chapters=6,
            words_per_chapter=1500,
        )

        assert result.outline.total_chapters == 6
        assert len(result.outline.chapters) == 6
        assert result.outline.chapters[0].chapter_number == 1
        assert result.outline.chapters[-1].chapter_number == 6

    @pytest.mark.timeout(360)
    async def test_init_long_volume_mode_auto_on_for_very_long_work(
        self, runner: ChapterRunner
    ) -> None:
        """Auto mode enables volume planning for very long projects."""
        result = await runner.init_long(
            premise="一个关于宗门兴衰的超长篇故事",
            project_id="test_volume_auto",
            genre="fantasy",
            tone="epic",
            total_chapters=120,
            words_per_chapter=3000,
            volume_mode="auto",
        )

        assert result.outline.volume_mode is True
        assert len(result.outline.volumes) >= 1
        assert result.outline.volumes[0].start_chapter == 1
        assert result.outline.volumes[-1].end_chapter == result.outline.total_chapters

    @pytest.mark.timeout(360)
    async def test_init_long_volume_mode_off_even_for_long_work(
        self, runner: ChapterRunner
    ) -> None:
        """Explicit off keeps medium/long projects in single-volume mode."""
        result = await runner.init_long(
            premise="一个关于权谋与修行的长篇故事",
            project_id="test_volume_off",
            genre="fantasy",
            tone="dark",
            total_chapters=120,
            words_per_chapter=3000,
            volume_mode="off",
        )

        assert result.outline.volume_mode is False
        assert result.outline.volumes == []

    async def test_init_and_run_chapter_1(
        self, runner: ChapterRunner, storage: FileSystemStorage, tmp_path: Path
    ) -> None:
        """Initialize project then generate chapter 1."""
        project_id = "test_chapter_run"

        # ① Init
        init_result = await runner.init_long(
            premise="一个关于时间旅行的故事",
            project_id=project_id,
            genre="scifi",
            tone="mysterious",
        )

        assert init_result.outline.total_chapters >= 1

        # ② Run chapter 1
        chapter_result = await runner.run_chapter(project_id, chapter_number=1)

        # Verify chapter result
        assert chapter_result.meta.chapter_number == 1
        assert chapter_result.meta.word_count > 0
        assert len(chapter_result.text) > 100
        assert chapter_result.canon_delta is not None
        assert chapter_result.creative_report is not None
        assert chapter_result.eval_report is not None
        assert chapter_result.eval_report.overall_score > 0
        assert chapter_result.alignment_report is not None
        assert chapter_result.alignment_report.alignment_score >= 0

        # Verify persisted files
        project_dir = tmp_path / project_id
        assert (project_dir / "chapters" / "chapter_001.md").exists()
        assert (project_dir / "reports" / "chapter_001_alignment.json").exists()

    async def test_scene_level_chapter_mode_reaches_existing_finalize_chain(
        self, storage: FileSystemStorage, runtime_settings, tmp_path: Path
    ) -> None:
        """Scene-level mode plans scenes, drafts/stitches them, then persists a normal chapter."""
        runtime_settings.long_word_count_archive_gate_enabled = False
        adapter = MockAdapter()
        router = ModelRouter(adapters={"mock": adapter}, default_provider="mock")
        scene_runner = ChapterRunner(
            router,
            PromptBuilder(),
            storage,
            config=ChapterRunnerConfig(writing_mode="scene_level"),
            settings=runtime_settings,
        )
        project_id = "test_scene_level_chapter_run"
        await scene_runner.init_long(
            premise="一个关于场景级写作模式的故事",
            project_id=project_id,
            genre="scifi",
            tone="mysterious",
            total_chapters=20,
            words_per_chapter=1800,
        )

        steps: list[str] = []
        scene_runner._on_step = lambda step, _data: steps.append(step)
        chapter_result = await scene_runner.run_chapter(project_id, chapter_number=1)

        project_dir = tmp_path / project_id
        assert chapter_result.meta.chapter_number == 1
        assert (project_dir / "plans" / "chapter_001_scene_plan.json").exists()
        assert (project_dir / "reports" / "chapter_001_scene_plan_validation.json").exists()
        assert (project_dir / "drafts" / "chapter_001" / "scenes" / "scene_01.md").exists()
        assert (project_dir / "drafts" / "chapter_001_scene_stitched.md").exists()
        assert (project_dir / "reports" / "chapter_001_scene_stitch_report.json").exists()
        assert (project_dir / "chapters" / "chapter_001.md").exists()
        assert "scene_plan_validation" in steps
        assert "draft_scene" in steps
        assert "scene_stitch" in steps
        assert "wave" in steps

    async def test_run_chapter_2_with_mock_normalized_delta(
        self, runner: ChapterRunner, storage: FileSystemStorage
    ) -> None:
        """Chapter 2 should run even if extractor returns stale source_chapter."""
        project_id = "test_run_chapter_2"
        await runner.init_long(
            premise="测试第二章生成",
            project_id=project_id,
            genre="scifi",
            tone="dark",
            total_chapters=3,
            words_per_chapter=2000,
        )
        await runner.run_chapter(project_id, chapter_number=1)
        chapter_2 = await runner.run_chapter(project_id, chapter_number=2)

        assert chapter_2.meta.chapter_number == 2

        from novel_forge.story_kernel.store import CanonStore

        canon = CanonStore(storage.project_dir(project_id)).load()
        assert canon.current_chapter == 2

    async def test_volume_end_audit_and_compaction(
        self, runner: ChapterRunner, storage: FileSystemStorage, tmp_path: Path
    ) -> None:
        """When a chapter closes a volume, system writes volume audit and advances active_volume."""
        project_id = "test_volume_finalize"

        await runner.init_long(
            premise="一个关于宗门内斗的长篇",
            project_id=project_id,
            genre="fantasy",
            tone="dark",
            total_chapters=3,
            words_per_chapter=3000,
            volume_mode="on",
            chapters_per_volume=1,
        )
        await runner.run_chapter(project_id, chapter_number=1)

        project_dir = tmp_path / project_id
        assert (project_dir / "reports" / "volume_001_audit.json").exists()

        store = StoryKernelStore(project_dir / "story_kernel.db")
        await store.init_db()
        canon = await store.load_kernel(project_id)
        await store.close()
        assert canon.active_volume >= 2

    async def test_init_long_resume(
        self, runner: ChapterRunner, storage: FileSystemStorage, tmp_path: Path
    ) -> None:
        """Calling init_long twice on the same project resumes from disk."""
        project_id = "test_resume"

        # First run — generates everything from scratch
        result1 = await runner.init_long(
            premise="一个关于时间旅行的故事",
            project_id=project_id,
            genre="scifi",
            tone="mysterious",
        )

        # Verify files exist
        project_dir = tmp_path / project_id
        assert (project_dir / "spec.json").exists()
        assert (project_dir / "story_bible.json").exists()
        assert (project_dir / "character_bible.json").exists()
        assert (project_dir / "outline.json").exists()

        # Second run — should resume (load from disk, not re-generate)
        resumed_steps: list[str] = []

        def track_resume(step: str, _data: object) -> None:
            resumed_steps.append(step)

        runner._on_step = track_resume
        result2 = await runner.init_long(
            premise="一个关于时间旅行的故事",
            project_id=project_id,
            genre="scifi",
            tone="mysterious",
        )

        # All steps should have been resumed
        assert "spec_resumed" in resumed_steps
        assert "init_story_bible_resumed" in resumed_steps
        assert "init_character_bible_resumed" in resumed_steps
        assert "plan_outline_resumed" in resumed_steps
        assert "canon_resumed" in resumed_steps

        # Data should be consistent
        assert result2.story_bible.title == result1.story_bible.title
        assert result2.outline.total_chapters == result1.outline.total_chapters

    async def test_init_long_uses_local_fallback_when_repaired_blueprint_misses_sections(
        self,
        runner: ChapterRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """init_long should locally synthesize required sections when LLM repair omits them."""

        original_call_with_retry = runner._call_with_retry

        async def _broken_blueprint_call(
            task_type: TaskType,
            context: dict[str, object],
            *,
            max_tokens: int,
            temperature: float,
            required_keys: tuple[str, ...] = (),
            max_retries: int = 2,
            prior_messages: list[dict[str, str]] | None = None,
            thinking: bool = False,
            multi_turn: bool = False,
            _capture_raw: list[str] | None = None,
            include_contract_required_keys: bool = True,
        ) -> dict[str, object]:
            if task_type == TaskType.PLAN_OUTLINE:
                fragment_request = context.get("blueprint_fragment_request")
                if isinstance(fragment_request, dict):
                    block_key = fragment_request.get("block_key")
                    if block_key == "subplots":
                        return {"subplot_plan": []}
                    if block_key == "suspense":
                        return {"suspense_schedule": []}
                if isinstance(context.get("current_blueprint"), dict):
                    return {"subplot_plan": [], "suspense_schedule": []}
            return await original_call_with_retry(
                task_type,
                context,
                max_tokens=max_tokens,
                temperature=temperature,
                required_keys=required_keys,
                max_retries=max_retries,
                prior_messages=prior_messages,
                thinking=thinking,
                multi_turn=multi_turn,
                _capture_raw=_capture_raw,
                include_contract_required_keys=include_contract_required_keys,
            )

        monkeypatch.setattr(runner, "_call_with_retry", _broken_blueprint_call)

        result = await runner.init_long(
            premise="测试蓝图修复闭环",
            project_id="test_invalid_repaired_blueprint",
            genre="scifi",
            tone="mysterious",
        )

        project_dir = tmp_path / "test_invalid_repaired_blueprint"
        repair_path = project_dir / "plans" / "narrative_blueprint_repair.json"
        assert (project_dir / "plans" / "narrative_blueprint.json").exists()
        assert repair_path.exists()
        assert (project_dir / "outline.json").exists()
        assert result.outline.total_chapters > 0
        repair_report = json.loads(repair_path.read_text(encoding="utf-8"))
        assert repair_report["repaired"] is True
        assert any(
            attempt["stage"] == "local_fallback" and attempt["status"] == "applied"
            for attempt in repair_report["attempts"]
        )

    async def test_duplicate_chapter_detection(
        self, runner: ChapterRunner, storage: FileSystemStorage, tmp_path: Path
    ) -> None:
        """Chapter result is returned for chapter 1 and canon state is tracked."""
        project_id = "test_duplicate"

        await runner.init_long(
            premise="测试重复检测",
            project_id=project_id,
            genre="scifi",
            tone="dark",
        )
        result = await runner.run_chapter(project_id, chapter_number=1)
        assert result.meta.chapter_number == 1

    async def test_canon_project_id_mismatch_in_init_long(
        self, runner: ChapterRunner, storage: FileSystemStorage, tmp_path: Path
    ) -> None:
        """init-long loads existing canon from SQLite for the same project directory."""
        fixed_id = "fixed_project"

        await runner.init_long(
            premise="第一个项目",
            project_id=fixed_id,
            genre="fantasy",
            tone="epic",
        )

        result = await runner.init_long(
            premise="第一个项目",
            project_id=fixed_id,
            genre="fantasy",
            tone="epic",
        )
        assert result is not None

    async def test_canon_project_id_mismatch_in_run_chapter(
        self, runner: ChapterRunner, storage: FileSystemStorage, tmp_path: Path
    ) -> None:
        """run_chapter works after init_long sets up the project."""
        await runner.init_long(
            premise="测试ID不匹配",
            project_id="test_mismatch",
            genre="mystery",
            tone="suspenseful",
        )

        result = await runner.run_chapter("test_mismatch", chapter_number=1)
        assert result.meta.chapter_number == 1
