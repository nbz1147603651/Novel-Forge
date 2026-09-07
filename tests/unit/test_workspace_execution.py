"""Tests for shared workspace execution helpers."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import novel_forge.workspace.execution as execution_module
import novel_forge.workspace.sessions.chapter_session_handlers as chapter_session_handlers
from novel_forge.core.exceptions import ConsistencyViolationError, RecoveryTarget
from novel_forge.core.project_state import ProjectOperation, ProjectState, ProjectStateMachine
from novel_forge.core.schemas.canon import CreativeReport
from novel_forge.core.schemas.chapter import (
    AlignmentReport,
    CausalIssue,
    CausalValidationReport,
    ChapterMeta,
    ChapterOutcome,
    ChapterResult,
)
from novel_forge.core.schemas.continuity import (
    ChapterBridge,
    ChapterPlan,
    ChapterStatePacket,
    ContinuityReport,
    RepairPlan,
)
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.core.schemas.reading_power import ReadingPowerReport
from novel_forge.core.schemas.review import RepairTicket
from novel_forge.core.schemas.story_state import ChapterExitState
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import scoped_stale_chapters
from novel_forge.pipeline.artifact_manifest import ArtifactManifest
from novel_forge.pipeline.finalization_manifest import record_finalization_success
from novel_forge.pipeline.steps.causal_repair_step import CausalRepairResult
from novel_forge.pipeline.steps.continuity_repair_step import ContinuityRepairResult
from novel_forge.pipeline.steps.polish_step import PolishResult
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.story_kernel.store import StoryKernelStore
from novel_forge.workspace.contracts import (
    DecisionCheckpoint,
    DecisionOption,
    InitLongRequest,
    ManualRevisionRequest,
    PolishChapterRequest,
    PrepareChapterRequest,
    RebuildMemoryVectorsRequest,
    ReevaluateChapterRequest,
    RepairCausalRequest,
    RepairContinuityRequest,
    RepairMotifHistoryRequest,
    ResolveChapterCheckpointRequest,
    RunChapterRequest,
    RunShortRequest,
)
from novel_forge.workspace.execution import (
    execute_init_long,
    execute_manual_revision,
    execute_polish_chapter,
    execute_prepare_chapter,
    execute_rebuild_memory_vectors,
    execute_reevaluate_chapter,
    execute_repair_motif_history,
    execute_resolve_chapter_checkpoint,
    execute_run_chapter,
    execute_run_short,
)
from novel_forge.workspace.execution_result import ExecutionResult
from novel_forge.workspace.helpers.execution_runners import _project_lock
from novel_forge.workspace.post_archive_tts import queue_post_archive_tts, run_post_archive_tts
from novel_forge.workspace.repair_ops.execution_repair_causal import (
    _execute_chapter_causal_repair_impl,
)
from novel_forge.workspace.repair_ops.execution_repair_continuity import (
    _execute_chapter_continuity_repair_impl,
)
from novel_forge.workspace.runtime import RuntimeServices
from novel_forge.workspace.sessions.chapter_session_state import (
    GuardCheckpointSessionState,
    PendingChapterReviewState,
    PlanCheckpointSessionState,
    build_guard_checkpoint,
    save_guard_session_state,
    serialize_pending_result,
    summarize_exit_state,
)


class _FakeShortRunner:
    def __init__(self) -> None:
        self.spec_input = None
        self.project_id = None
        self.segmented_mode = None
        self.segment_target_words = None
        self.segment_max_count = None
        self.blueprint_element_preferences = None
        self.research_enabled = None
        self.research_provider = None
        self.research_query_hint = None

    async def run(
        self,
        spec_input,
        *,
        project_id: str,
        segmented_mode=None,
        segment_target_words=None,
        segment_max_count=None,
        blueprint_element_preferences=None,
        research_enabled=False,
        research_provider="auto",
        research_query_hint="",
    ):
        self.spec_input = spec_input
        self.project_id = project_id
        self.segmented_mode = segmented_mode
        self.segment_target_words = segment_target_words
        self.segment_max_count = segment_max_count
        self.blueprint_element_preferences = blueprint_element_preferences
        self.research_enabled = research_enabled
        self.research_provider = research_provider
        self.research_query_hint = research_query_hint
        return SimpleNamespace(
            final_text="ok", eval_report=SimpleNamespace(overall_score=8.0, passed=True)
        )


class _FakeChapterRunner:
    def __init__(self) -> None:
        self.init_args = None
        self.run_args = None
        self.storage: FileSystemStorage | None = None

    async def init_long(self, premise: str, **kwargs):
        self.init_args = {"premise": premise, **kwargs}
        return SimpleNamespace(
            story_bible=SimpleNamespace(title="Demo"),
            outline=SimpleNamespace(
                total_chapters=kwargs["total_chapters"], volume_mode=False, volumes=[]
            ),
            character_bible=SimpleNamespace(characters=[]),
        )

    async def run_chapter(
        self,
        project_id: str,
        chapter_number: int,
        *,
        force_regenerate: bool,
        chapter_instruction: str = "",
    ):
        self.run_args = {
            "project_id": project_id,
            "chapter_number": chapter_number,
            "force_regenerate": force_regenerate,
            "chapter_instruction": chapter_instruction,
        }
        if self.storage is not None:
            _record_required_finalization(self.storage, project_id, chapter_number)
        return SimpleNamespace(meta=SimpleNamespace(word_count=1200))


class _FakeRuntime:
    def __init__(self) -> None:
        self.short = _FakeShortRunner()
        self.chapter = _FakeChapterRunner()
        self.short_runner_kwargs = None

    def create_project_id(self, mode: str) -> str:
        return f"{mode}_auto"

    def short_runner(self, **_kwargs):
        self.short_runner_kwargs = _kwargs
        return self.short

    def chapter_runner(self, **_kwargs):
        return self.chapter


class _FailingChapterRunner(_FakeChapterRunner):
    async def init_long(self, premise: str, **kwargs):  # noqa: ARG002
        raise RuntimeError("init failed")


class _FakeRuntimeWithStorage(_FakeRuntime):
    def __init__(self, storage: FileSystemStorage, *, fail_init: bool = False) -> None:
        super().__init__()
        self.storage = storage
        self.settings = SimpleNamespace(narrative_state_required=True)
        self.chapter.storage = storage
        if fail_init:
            self.chapter = _FailingChapterRunner()
            self.chapter.storage = storage


class _FakeMemoryContext:
    def __init__(self) -> None:
        self.progress_callback = "stale"
        self.warm_start_calls = 0

    def set_progress_callback(self, callback):
        self.progress_callback = callback

    def get_status_summary(self):
        return {"last_indexed_chapter": 0}

    async def warm_start_from_init_artifacts(self):
        self.warm_start_calls += 1
        return {"motif_warmup": True}


class _FakeRuntimeWithWarmMemory(_FakeRuntimeWithStorage):
    def __init__(self, storage: FileSystemStorage) -> None:
        super().__init__(storage)
        self.settings = SimpleNamespace(
            memory_episodic_enabled=True,
            memory_multi_granularity_summary_enabled=False,
            memory_adaptive_compression_enabled=False,
            memory_motif_tracking_enabled=False,
            memory_critic_agent_enabled=False,
            narrative_state_required=True,
        )
        self.memory_context = _FakeMemoryContext()
        self.memory_warm_calls: list[tuple[str, FileSystemStorage]] = []

    async def get_memory_context(self, *, project_id: str, storage: FileSystemStorage):
        self.memory_warm_calls.append((project_id, storage))
        return self.memory_context


def _write_outline(
    storage: FileSystemStorage, project_id: str, *, total_chapters: int
) -> ProjectLayout:
    layout = ProjectLayout(storage.ensure_project_dir(project_id))
    layout.ensure_dirs()
    storage.save_json(
        layout.outline_path,
        {
            "total_chapters": total_chapters,
            "volume_mode": False,
            "volumes": [],
            "chapters": [],
        },
    )
    return layout


def _record_required_finalization(
    storage: FileSystemStorage,
    project_id: str,
    chapter_number: int,
) -> ProjectLayout:
    """Make fake runner output satisfy the same durable finalization contract."""

    layout = ProjectLayout(storage.ensure_project_dir(project_id))
    layout.ensure_dirs()
    chapter_path = layout.chapter_path(chapter_number)
    if not chapter_path.exists():
        storage.save_text(chapter_path, f"第{chapter_number}章测试终稿")
    text_hash = source_text_hash(chapter_path.read_text(encoding="utf-8"))
    manifest = ArtifactManifest(storage, layout)
    for phase in ("final_text", "reports", "narrative_state", "story_kernel"):
        record_finalization_success(
            manifest,
            chapter_number=chapter_number,
            phase=phase,
            text_hash=text_hash,
        )
    return layout


async def test_execute_run_short_passes_all_contract_fields() -> None:
    runtime = _FakeRuntime()
    request = RunShortRequest(
        theme="  雨夜旧宅  ",
        genre="mystery",
        tone="dark",
        length_target=2400,
        segmented_mode="on",
        writing_mode="scene_level",
        segment_target_words=1600,
        segment_max_count=3,
        title="旧宅来信",
        language="en",
        characters_hint="侦探与失踪作家",
        world_hint="海边小镇",
        conflict_hint="十年前命案回潮",
        pov_hint="第一人称",
        opening_style="倒叙切入",
        ending_style="留白",
        extra_instructions="减少旁白",
        research_enabled=True,
        research_provider="mcp_search",
        research_query_hint="海边小镇汛期档案流程",
    )

    execution = await execute_run_short(runtime, request)

    assert execution.project_id == "short_auto"
    assert runtime.short.project_id == "short_auto"
    assert runtime.short.segmented_mode == "on"
    assert runtime.short_runner_kwargs["writing_mode"] == "scene_level"
    assert runtime.short.segment_target_words == 1600
    assert runtime.short.segment_max_count == 3
    assert runtime.short.blueprint_element_preferences == {}
    assert runtime.short.research_enabled is True
    assert runtime.short.research_provider == "mcp_search"
    assert runtime.short.research_query_hint == "海边小镇汛期档案流程"
    assert runtime.short.spec_input == {
        "theme": "  雨夜旧宅  ",
        "genre": "mystery",
        "tone": "dark",
        "length_target": 2400,
        "title": "旧宅来信",
        "language": "en",
        "characters_hint": "侦探与失踪作家",
        "world_hint": "海边小镇",
        "conflict_hint": "十年前命案回潮",
        "pov_hint": "第一人称",
        "opening_style": "倒叙切入",
        "ending_style": "留白",
        "extra_instructions": "减少旁白",
    }


async def test_execute_init_long_passes_extended_fields_and_generated_project_id() -> None:
    runtime = _FakeRuntime()
    request = InitLongRequest(
        premise="记忆回收师发现自己的前半生被人改写",
        genre="scifi",
        tone="suspenseful",
        total_chapters=18,
        words_per_chapter=4200,
        volume_mode="auto",
        chapters_per_volume=6,
        title="回收记忆的人",
        language="zh",
        characters_hint="记忆回收师、监察官",
        world_hint="近未来海港都市",
        conflict_hint="真相越接近越会失忆",
        pov_hint="双视角",
        opening_style="高概念开场",
        ending_style="开放式余韵",
        extra_instructions="强化情绪拉扯",
    )

    execution = await execute_init_long(runtime, request)

    assert execution.project_id == "long_auto"
    assert runtime.chapter.init_args == {
        "premise": "记忆回收师发现自己的前半生被人改写",
        "project_id": "long_auto",
        "genre": "scifi",
        "tone": "suspenseful",
        "title": "回收记忆的人",
        "language": "zh",
        "characters_hint": "记忆回收师、监察官",
        "world_hint": "近未来海港都市",
        "conflict_hint": "真相越接近越会失忆",
        "pov_hint": "双视角",
        "opening_style": "高概念开场",
        "ending_style": "开放式余韵",
        "extra_instructions": "强化情绪拉扯",
        "total_chapters": 18,
        "words_per_chapter": 4200,
        "volume_mode": "auto",
        "chapters_per_volume": 6,
        "blueprint_element_preferences": {},
        "research_enabled": False,
        "research_provider": "auto",
        "research_query_hint": "",
        "creative_exploration": "adaptive",
        "planning_commitment": "full",
    }


async def test_execute_init_long_passes_polish_hint_and_blueprint_preferences() -> None:
    runtime = _FakeRuntime()
    request = InitLongRequest(
        premise="雨城侦探追查一宗无法被记录的案件",
        polish_hint="强化前五章的悬念递进",
        blueprint_element_preferences={
            "preset_id": "mystery",
            "manual_override": True,
        },
    )

    await execute_init_long(runtime, request)

    assert runtime.chapter.init_args["polish_hint"] == "强化前五章的悬念递进"
    assert runtime.chapter.init_args["blueprint_element_preferences"] == {
        "preset_id": "mystery",
        "manual_override": True,
    }


async def test_execute_init_long_passes_research_options() -> None:
    runtime = _FakeRuntime()
    request = InitLongRequest(
        premise="女官追查一宗旧案",
        research_enabled=True,
        research_provider="searxng",
        research_query_hint="唐代司法制度",
    )

    await execute_init_long(runtime, request)

    assert runtime.chapter.init_args["research_enabled"] is True
    assert runtime.chapter.init_args["research_provider"] == "searxng"
    assert runtime.chapter.init_args["research_query_hint"] == "唐代司法制度"


async def test_execute_init_long_persists_outline_ready_project_state(tmp_path) -> None:
    runtime = _FakeRuntimeWithStorage(FileSystemStorage(tmp_path))
    request = InitLongRequest(
        premise="记忆回收师发现自己的前半生被人改写",
        genre="scifi",
        tone="suspenseful",
    )

    execution = await execute_init_long(runtime, request)

    machine = ProjectStateMachine.load_from_disk(
        execution.project_id,
        tmp_path / execution.project_id,
    )
    assert machine.state == ProjectState.OUTLINE_READY
    assert machine.record.last_operation == ProjectOperation.COMPLETE_INIT
    assert machine.record.transition_count == 2


async def test_execute_init_long_binds_memory_context_without_first_chapter_warmup(
    tmp_path,
) -> None:
    storage = FileSystemStorage(tmp_path)
    runtime = _FakeRuntimeWithWarmMemory(storage)
    request = InitLongRequest(
        premise="记忆回收师发现自己的前半生被人改写",
        genre="scifi",
    )

    execution = await execute_init_long(runtime, request)

    assert execution.project_id
    assert runtime.memory_warm_calls == [(execution.project_id, storage)]
    assert runtime.memory_context.progress_callback == "stale"
    assert runtime.memory_context.warm_start_calls == 0


async def test_execute_run_chapter_warm_starts_first_chapter_memory(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    runtime = _FakeRuntimeWithWarmMemory(storage)
    init_execution = await execute_init_long(
        runtime,
        InitLongRequest(
            premise="记忆回收师发现自己的前半生被人改写",
            genre="scifi",
        ),
    )

    await execute_run_chapter(
        runtime,
        RunChapterRequest(
            project_id=init_execution.project_id,
            chapter_number=1,
            force=False,
        ),
    )

    assert runtime.memory_warm_calls == [
        (init_execution.project_id, storage),
        (init_execution.project_id, storage),
    ]
    assert runtime.memory_context.warm_start_calls == 1


async def test_execute_run_chapter_does_not_warm_start_later_chapters(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    runtime = _FakeRuntimeWithWarmMemory(storage)
    init_execution = await execute_init_long(
        runtime,
        InitLongRequest(
            premise="记忆回收师发现自己的前半生被人改写",
            genre="scifi",
        ),
    )

    await execute_run_chapter(
        runtime,
        RunChapterRequest(
            project_id=init_execution.project_id,
            chapter_number=2,
            force=False,
        ),
    )

    assert runtime.memory_context.warm_start_calls == 0


async def test_chapter_session_runner_warm_starts_first_chapter_once(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    runtime = _FakeRuntimeWithWarmMemory(storage)

    await chapter_session_handlers._create_runner_with_memory(
        runtime,
        project_id="long_demo",
        chapter_number=1,
    )
    await chapter_session_handlers._create_runner_with_memory(
        runtime,
        project_id="long_demo",
        chapter_number=1,
    )

    assert runtime.memory_context.warm_start_calls == 1


async def test_chapter_session_memory_lifecycle_uses_acquire_and_releases() -> None:
    class _MemoryContext:
        def __init__(self) -> None:
            self.progress_callback = "stale"

        def set_progress_callback(self, callback):  # noqa: ANN001
            self.progress_callback = callback

    class _Runtime:
        def __init__(self) -> None:
            self.storage = object()
            self.memory_context = _MemoryContext()
            self.runner = object()
            self.runner_kwargs = None
            self.acquire_calls: list[tuple[str, object]] = []
            self.release_calls: list[str] = []

        async def acquire_memory_context(self, *, project_id: str, storage):  # noqa: ANN001
            self.acquire_calls.append((project_id, storage))
            return self.memory_context

        def release_memory_context_lease(self, project_id: str) -> None:
            self.release_calls.append(project_id)

        def chapter_runner(self, **kwargs):  # noqa: ANN001
            self.runner_kwargs = kwargs
            return self.runner

    runtime = _Runtime()
    progress_events: list[tuple[str, object]] = []

    async with chapter_session_handlers._chapter_runner_memory_lifecycle(
        runtime,
        project_id="long_demo",
        chapter_number=2,
        writing_mode="whole_chapter",
        on_step_progress=lambda step, data: progress_events.append((step, data)),
    ) as (runner, memory_ctx):
        assert runner is runtime.runner
        assert memory_ctx is runtime.memory_context
        assert runtime.acquire_calls == [("long_demo", runtime.storage)]
        assert runtime.runner_kwargs["memory_context"] is runtime.memory_context
        assert callable(runtime.memory_context.progress_callback)

    assert runtime.memory_context.progress_callback is None
    assert runtime.release_calls == ["long_demo"]


async def test_resolve_plan_checkpoint_cleans_memory_lifecycle_on_guard_checkpoint(
    monkeypatch,
    tmp_path,
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = _write_minimal_chapter_state(storage, "long_demo", 2)
    cleanup_calls: list[str] = []
    steps: list[tuple[str, object]] = []

    settings = SimpleNamespace(long_plot_guard_mode="off", long_max_consistency_replans=2)
    context = SimpleNamespace(
        storage=storage,
        router=object(),
        builder=object(),
        settings=settings,
        config=SimpleNamespace(),
        on_step=lambda step, data: steps.append((step, data)),
    )
    runner = SimpleNamespace(create_execution_context=lambda: context)
    prepared = SimpleNamespace(bridge=ChapterBridge(to_chapter=2, bridge_summary="桥接"))

    @asynccontextmanager
    async def _fake_chapter_runner_memory_lifecycle(*_args, **_kwargs):
        try:
            yield runner, object()
        finally:
            cleanup_calls.append("cleanup")

    async def _fake_review(*_args, **_kwargs):
        return SimpleNamespace(
            current_text="正文",
            performed_edits=0,
            outcome=ChapterOutcome(
                source_chapter=2,
                chapter_summary="摘要",
                creative_report=CreativeReport(structured_summary="创作报告"),
                chapter_exit_state=ChapterExitState(chapter_number=2),
            ),
            alignment_report=AlignmentReport(
                alignment_score=9.0,
                risk_level="low",
                conflict_level="low",
                summary="对齐",
            ),
            chapter_repair_report=None,
            causal_report=CausalValidationReport(
                causal_score=9.0,
                summary="因果",
                issues=[],
                causal_link_verified=True,
            ),
            continuity_report=ContinuityReport(
                continuity_score=9.0,
                summary="连贯",
                issues=[],
            ),
            repair_plan=RepairPlan(expected_outcome="ok"),
            eval_report=EvalReport(
                overall_score=8.5,
                passed=True,
                threshold=6.0,
                summary="质量",
            ),
            reading_power_report=None,
            guard_compliance_report=None,
            review_findings=(),
            repair_tickets=(),
            warnings=(),
            prepared=prepared,
        )

    monkeypatch.setattr(
        chapter_session_handlers,
        "_chapter_runner_memory_lifecycle",
        _fake_chapter_runner_memory_lifecycle,
    )

    async def _load_prepared(*_args, **_kwargs):
        return prepared

    monkeypatch.setattr(
        chapter_session_handlers,
        "load_prepared_chapter_artifacts",
        _load_prepared,
    )
    monkeypatch.setattr(chapter_session_handlers, "review_chapter_draft", _fake_review)

    result = await chapter_session_handlers.resolve_plan_checkpoint(
        SimpleNamespace(storage=storage),
        ResolveChapterCheckpointRequest(
            project_id="long_demo",
            chapter_number=2,
            checkpoint_id="plan-002",
            option_id="write_now",
            notes="",
        ),
        bundle=SimpleNamespace(
            layout=layout,
            canon_state=SimpleNamespace(current_chapter=1),
            style_profile=None,
        ),
        session_state=PlanCheckpointSessionState(
            checkpoint_id="plan-002",
            project_id="long_demo",
            chapter_number=2,
            canon_watermark=1,
        ),
    )

    assert result.status == "needs_decision"
    assert cleanup_calls == ["cleanup"]


async def test_resolve_plan_checkpoint_cleans_memory_lifecycle_on_consistency_replan(
    monkeypatch,
    tmp_path,
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = _write_minimal_chapter_state(storage, "long_demo", 2)
    cleanup_calls: list[str] = []

    context = SimpleNamespace(
        settings=SimpleNamespace(long_max_consistency_replans=2),
        config=SimpleNamespace(),
        on_step=lambda *_args: None,
    )
    runner = SimpleNamespace(create_execution_context=lambda: context)
    prepared = SimpleNamespace(bridge=ChapterBridge(to_chapter=2, bridge_summary="桥接"))

    @asynccontextmanager
    async def _fake_chapter_runner_memory_lifecycle(*_args, **_kwargs):
        try:
            yield runner, object()
        finally:
            cleanup_calls.append("cleanup")

    async def _fake_review(*_args, **_kwargs):
        raise ConsistencyViolationError(["承接缺失"], replan_target=RecoveryTarget.PLAN)

    async def _fake_prepare_plan_checkpoint(*_args, **_kwargs):
        return SimpleNamespace(
            project_id="long_demo",
            chapter_number=2,
            checkpoint=DecisionCheckpoint(
                checkpoint_id="plan-retry",
                checkpoint_type="plan_checkpoint",
                options=[
                    DecisionOption(
                        option_id="write_now",
                        label="写作",
                        is_recommended=True,
                    )
                ],
            ),
        )

    monkeypatch.setattr(
        chapter_session_handlers,
        "_chapter_runner_memory_lifecycle",
        _fake_chapter_runner_memory_lifecycle,
    )

    async def _load_prepared(*_args, **_kwargs):
        return prepared

    monkeypatch.setattr(
        chapter_session_handlers,
        "load_prepared_chapter_artifacts",
        _load_prepared,
    )
    monkeypatch.setattr(chapter_session_handlers, "review_chapter_draft", _fake_review)
    monkeypatch.setattr(
        chapter_session_handlers,
        "prepare_plan_checkpoint",
        _fake_prepare_plan_checkpoint,
    )

    result = await chapter_session_handlers.resolve_plan_checkpoint(
        SimpleNamespace(storage=storage),
        ResolveChapterCheckpointRequest(
            project_id="long_demo",
            chapter_number=2,
            checkpoint_id="plan-002",
            option_id="write_now",
            notes="",
        ),
        bundle=SimpleNamespace(layout=layout),
        session_state=PlanCheckpointSessionState(
            checkpoint_id="plan-002",
            project_id="long_demo",
            chapter_number=2,
            canon_watermark=1,
        ),
    )

    assert result.status == "needs_decision"
    assert result.checkpoint is not None
    assert "上一轮写作未通过质量校验" in result.checkpoint.prompt
    assert cleanup_calls == ["cleanup"]


async def test_execute_init_long_marks_project_state_failed_on_exception(tmp_path) -> None:
    runtime = _FakeRuntimeWithStorage(FileSystemStorage(tmp_path), fail_init=True)
    request = InitLongRequest(
        premise="记忆回收师发现自己的前半生被人改写",
        genre="scifi",
        tone="suspenseful",
    )

    with pytest.raises(RuntimeError, match="init failed"):
        await execute_init_long(runtime, request)

    machine = ProjectStateMachine.load_from_disk("long_auto", tmp_path / "long_auto")
    assert machine.state == ProjectState.INIT_FAILED
    assert machine.record.last_operation == ProjectOperation.FAIL_INIT
    assert machine.record.transition_count == 2


async def test_execute_run_chapter_advances_project_state_to_writing(tmp_path, monkeypatch) -> None:
    from contextlib import asynccontextmanager

    from novel_forge.workspace import execution_future_planning
    from novel_forge.workspace.helpers import execution_runners

    runtime = _FakeRuntimeWithStorage(FileSystemStorage(tmp_path))
    init_request = InitLongRequest(
        premise="记忆回收师发现自己的前半生被人改写",
        genre="scifi",
        tone="suspenseful",
    )
    init_execution = await execute_init_long(runtime, init_request)

    chapter_lock_active = False
    explored = False
    original_lock = execution_runners._project_lock

    @asynccontextmanager
    async def tracking_lock(*args, **kwargs):
        nonlocal chapter_lock_active
        async with original_lock(*args, **kwargs):
            chapter_lock_active = True
            try:
                yield
            finally:
                chapter_lock_active = False

    async def explore(*args, **kwargs):
        nonlocal explored
        assert not chapter_lock_active
        explored = True
        return {"status": "fixed"}

    monkeypatch.setattr(execution_runners, "_project_lock", tracking_lock)
    monkeypatch.setattr(execution_future_planning, "execute_future_planning", explore)

    run_request = RunChapterRequest(
        project_id=init_execution.project_id,
        chapter_number=1,
        force=False,
    )
    await execute_run_chapter(runtime, run_request)
    assert explored

    machine = ProjectStateMachine.load_from_disk(
        init_execution.project_id,
        tmp_path / init_execution.project_id,
    )
    assert machine.state == ProjectState.WRITING
    assert machine.record.last_operation == ProjectOperation.START_WRITING
    assert machine.record.transition_count == 3


async def test_execute_run_chapter_marks_project_state_completed_at_last_chapter(tmp_path) -> None:
    runtime = _FakeRuntimeWithStorage(FileSystemStorage(tmp_path))
    init_execution = await execute_init_long(
        runtime,
        InitLongRequest(
            premise="记忆回收师发现自己的前半生被人改写",
            genre="scifi",
            tone="suspenseful",
            total_chapters=1,
        ),
    )
    layout = _write_outline(runtime.storage, init_execution.project_id, total_chapters=1)
    runtime.storage.save_text(layout.chapter_path(1), "终章正文")

    await execute_run_chapter(
        runtime,
        RunChapterRequest(
            project_id=init_execution.project_id,
            chapter_number=1,
            force=False,
        ),
    )

    machine = ProjectStateMachine.load_from_disk(
        init_execution.project_id,
        tmp_path / init_execution.project_id,
    )
    assert machine.state == ProjectState.COMPLETED
    assert machine.record.last_operation == ProjectOperation.COMPLETE
    assert machine.record.transition_count == 4


async def test_execute_run_chapter_resumes_completed_project_state_to_writing(tmp_path) -> None:
    runtime = _FakeRuntimeWithStorage(FileSystemStorage(tmp_path))
    init_execution = await execute_init_long(
        runtime,
        InitLongRequest(
            premise="记忆回收师发现自己的前半生被人改写",
            genre="scifi",
            tone="suspenseful",
        ),
    )

    machine = ProjectStateMachine.load_from_disk(
        init_execution.project_id,
        tmp_path / init_execution.project_id,
    )
    machine.execute(ProjectOperation.START_WRITING)
    machine.execute(ProjectOperation.COMPLETE)

    await execute_run_chapter(
        runtime,
        RunChapterRequest(
            project_id=init_execution.project_id,
            chapter_number=1,
            force=False,
        ),
    )

    resumed = ProjectStateMachine.load_from_disk(
        init_execution.project_id,
        tmp_path / init_execution.project_id,
    )
    assert resumed.state == ProjectState.WRITING
    assert resumed.record.last_operation == ProjectOperation.RESUME
    assert resumed.record.transition_count == 5


async def test_execute_run_chapter_passes_force_flag(tmp_path) -> None:
    runtime = _FakeRuntimeWithStorage(FileSystemStorage(tmp_path))
    request = RunChapterRequest(
        project_id="long_demo",
        chapter_number=7,
        force=True,
        notes="保持单 POV，并让本章以雨声结束。",
    )

    execution = await execute_run_chapter(runtime, request)

    assert execution.project_id == "long_demo"
    assert runtime.chapter.run_args == {
        "project_id": "long_demo",
        "chapter_number": 7,
        "force_regenerate": True,
        "chapter_instruction": "保持单 POV，并让本章以雨声结束。",
    }


async def test_chapter_auto_tts_runs_after_success_when_enabled(
    monkeypatch,
    tmp_path,
    runtime_settings,
) -> None:
    import novel_forge.workspace.tts_ops.execution as execution_tts

    project_id = "auto_tts_demo"
    storage = FileSystemStorage(tmp_path)
    storage.ensure_project_dir(project_id)
    layout = ProjectLayout(storage.existing_project_dir(project_id))
    layout.ensure_dirs()
    storage.save_text(layout.chapter_path(3), "第三章终稿")
    assembled_audio = layout.tts_assembled_audio_path(3)
    assembled_audio.parent.mkdir(parents=True, exist_ok=True)
    assembled_audio.write_bytes(b"0" * 128)
    settings = runtime_settings.model_copy(
        update={
            "tts_enabled": True,
            "tts_auto_trigger_after_chapter": True,
            "tts_default_provider": "mock",
        }
    )
    runtime = SimpleNamespace(settings=settings, storage=storage)
    calls: list[dict[str, object]] = []
    events: list[tuple[str, object]] = []

    async def _fake_full_tts_pipeline(**kwargs):
        calls.append(kwargs)
        return ExecutionResult(
            project_id=project_id,
            result={
                "is_complete": True,
                "assembled_audio_path": str(assembled_audio),
            },
        )

    monkeypatch.setattr(execution_tts, "execute_full_tts_pipeline", _fake_full_tts_pipeline)

    queued = queue_post_archive_tts(
        settings=settings,
        storage=storage,
        project_id=project_id,
        chapter_number=3,
        parent_job_id="chapter-job-3",
    )
    assert queued is not None
    assert queued["status"] == "queued"
    assert queued["parent_job_id"] == "chapter-job-3"

    record = await run_post_archive_tts(
        runtime,
        project_id=project_id,
        chapter_number=3,
        on_step_progress=lambda step, payload: events.append((step, payload)),
    )

    assert len(calls) == 1
    assert calls[0]["chapter_text"] == "第三章终稿"
    assert calls[0]["characters"] == []
    assert calls[0]["allow_voice_team_rebuild"] is False
    assert record is not None
    assert record["status"] == "completed"
    assert record["parent_job_id"] == "chapter-job-3"
    assert record["queued_at"]
    assert storage.load_json(layout.tts_auto_run_path(3))["status"] == "completed"
    assert [step for step, _payload in events] == [
        "tts_auto_trigger_started",
        "tts_auto_trigger_completed",
    ]


async def test_chapter_auto_tts_records_partial_result_without_false_success(
    monkeypatch,
    tmp_path,
    runtime_settings,
) -> None:
    import novel_forge.workspace.tts_ops.execution as execution_tts

    project_id = "auto_tts_partial"
    storage = FileSystemStorage(tmp_path)
    storage.ensure_project_dir(project_id)
    layout = ProjectLayout(storage.existing_project_dir(project_id))
    layout.ensure_dirs()
    storage.save_text(layout.chapter_path(1), "第一章已落盘终稿")
    settings = runtime_settings.model_copy(
        update={
            "tts_enabled": True,
            "tts_auto_trigger_after_chapter": True,
            "tts_default_provider": "mock",
        }
    )
    runtime = SimpleNamespace(settings=settings, storage=storage)
    events: list[tuple[str, object]] = []

    async def _fake_full_tts_pipeline(**_kwargs):
        return ExecutionResult(
            project_id=project_id,
            result={
                "is_complete": False,
                "segment_results": [
                    {"status": "completed"},
                    {"status": "failed"},
                ],
            },
        )

    monkeypatch.setattr(execution_tts, "execute_full_tts_pipeline", _fake_full_tts_pipeline)

    record = await run_post_archive_tts(
        runtime,
        project_id=project_id,
        chapter_number=1,
        on_step_progress=lambda step, payload: events.append((step, payload)),
    )

    assert record is not None
    assert record["status"] == "partial"
    assert record["completed_segments"] == 1
    assert record["total_segments"] == 2
    assert [step for step, _payload in events] == [
        "tts_auto_trigger_started",
        "tts_auto_trigger_partial",
    ]


async def test_async_project_lock_allows_same_operation_nested_file_write(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    storage.ensure_project_dir("nested_lock_demo")
    runtime = SimpleNamespace(storage=storage)

    async with _project_lock(runtime, "nested_lock_demo"):
        with storage.project_lock("nested_lock_demo"):
            storage.save_text(
                storage.project_path("nested_lock_demo") / "nested.txt",
                "ok",
            )

    assert storage.load_text(storage.project_path("nested_lock_demo") / "nested.txt") == "ok"


async def test_execute_prepare_chapter_delegates_to_session_helper(monkeypatch) -> None:
    runtime = _FakeRuntime()
    request = PrepareChapterRequest(
        project_id="long_demo",
        chapter_number=3,
        force=False,
        notes="把母女冲突写得更明显",
    )
    captured = {}

    async def _fake_prepare(runtime_obj, request_obj, *, on_step_progress=None):
        captured["runtime"] = runtime_obj
        captured["request"] = request_obj
        captured["has_callback"] = on_step_progress is not None
        return {"project_id": request_obj.project_id, "chapter_number": request_obj.chapter_number}

    monkeypatch.setattr(
        "novel_forge.workspace.helpers.execution_runners.prepare_chapter_session", _fake_prepare
    )

    execution = await execute_prepare_chapter(runtime, request)

    assert execution.project_id == "long_demo"
    assert captured["runtime"] is runtime
    assert captured["request"] == request


async def test_execute_resolve_checkpoint_delegates_to_session_helper(tmp_path, monkeypatch) -> None:
    runtime = _FakeRuntimeWithStorage(FileSystemStorage(tmp_path))
    _record_required_finalization(runtime.storage, "long_demo", 3)
    request = ResolveChapterCheckpointRequest(
        project_id="long_demo",
        chapter_number=3,
        checkpoint_id="plan-003-abcd",
        option_id="write_now",
        notes="",
    )
    captured = {}

    async def _fake_resolve(runtime_obj, request_obj, *, on_step_progress=None):
        captured["runtime"] = runtime_obj
        captured["request"] = request_obj
        captured["has_callback"] = on_step_progress is not None
        return {"project_id": request_obj.project_id, "status": "completed"}

    monkeypatch.setattr(
        "novel_forge.workspace.helpers.execution_runners.resolve_chapter_session", _fake_resolve
    )

    execution = await execute_resolve_chapter_checkpoint(runtime, request)

    assert execution.project_id == "long_demo"
    assert captured["runtime"] is runtime
    assert captured["request"] == request


async def test_execute_resolve_checkpoint_marks_project_state_paused(tmp_path, monkeypatch) -> None:
    runtime = _FakeRuntimeWithStorage(FileSystemStorage(tmp_path))
    init_execution = await execute_init_long(
        runtime,
        InitLongRequest(
            premise="记忆回收师发现自己的前半生被人改写",
            genre="scifi",
            tone="suspenseful",
        ),
    )

    async def _fake_resolve(*_args, **_kwargs):
        return SimpleNamespace(status="needs_decision", metadata={"paused": True})

    monkeypatch.setattr(
        "novel_forge.workspace.helpers.execution_runners.resolve_chapter_session", _fake_resolve
    )

    await execute_resolve_chapter_checkpoint(
        runtime,
        ResolveChapterCheckpointRequest(
            project_id=init_execution.project_id,
            chapter_number=1,
            checkpoint_id="guard-1",
            option_id="pause_for_human",
            notes="",
        ),
    )

    machine = ProjectStateMachine.load_from_disk(
        init_execution.project_id,
        tmp_path / init_execution.project_id,
    )
    assert machine.state == ProjectState.PAUSED
    assert machine.record.last_operation == ProjectOperation.PAUSE
    assert machine.record.transition_count == 4


async def test_execute_resolve_checkpoint_marks_project_state_completed_at_last_chapter(
    tmp_path,
    monkeypatch,
    runtime_settings,
) -> None:
    import novel_forge.workspace.post_archive_tts as post_archive_tts

    runtime = _FakeRuntimeWithStorage(FileSystemStorage(tmp_path))
    runtime.settings = runtime_settings.model_copy(
        update={"tts_enabled": True, "tts_auto_trigger_after_chapter": True}
    )
    init_execution = await execute_init_long(
        runtime,
        InitLongRequest(
            premise="记忆回收师发现自己的前半生被人改写",
            genre="scifi",
            tone="suspenseful",
            total_chapters=1,
        ),
    )
    layout = _write_outline(runtime.storage, init_execution.project_id, total_chapters=1)
    runtime.storage.save_text(layout.chapter_path(1), "终章正文")
    _record_required_finalization(runtime.storage, init_execution.project_id, 1)

    async def _fake_resolve(*_args, **_kwargs):
        return SimpleNamespace(status="completed", metadata={})

    monkeypatch.setattr(
        "novel_forge.workspace.helpers.execution_runners.resolve_chapter_session", _fake_resolve
    )
    tts_calls: list[dict[str, object]] = []

    async def _fake_post_archive_tts(_runtime, **kwargs):
        tts_calls.append(kwargs)
        return {"status": "completed"}

    monkeypatch.setattr(post_archive_tts, "run_post_archive_tts", _fake_post_archive_tts)

    await execute_resolve_chapter_checkpoint(
        runtime,
        ResolveChapterCheckpointRequest(
            project_id=init_execution.project_id,
            chapter_number=1,
            checkpoint_id="guard-1",
            option_id="accept_and_finalize",
            notes="",
        ),
    )

    machine = ProjectStateMachine.load_from_disk(
        init_execution.project_id,
        tmp_path / init_execution.project_id,
    )
    assert machine.state == ProjectState.COMPLETED
    assert machine.record.last_operation == ProjectOperation.COMPLETE
    assert machine.record.transition_count == 4
    assert len(tts_calls) == 1
    assert tts_calls[0]["project_id"] == init_execution.project_id
    assert tts_calls[0]["chapter_number"] == 1
    assert tts_calls[0]["on_step_progress"] is not None


async def test_execute_repair_motif_history_reports_stats_and_progress() -> None:
    class _MemoryContextStub:
        def __init__(self) -> None:
            self.calls = 0

        def repair_motif_history_from_cache(self) -> dict[str, object]:
            self.calls += 1
            return {
                "ok": True,
                "motifs_touched": 4,
                "occurrences_seen": 19,
                "created_motifs": 1,
            }

    class _RuntimeStub:
        def __init__(self, memory_ctx) -> None:  # noqa: ANN001
            self.storage = object()
            self._memory_ctx = memory_ctx

        async def get_memory_context(self, *, project_id: str, storage):  # noqa: ANN001
            assert project_id == "demo_project"
            assert storage is self.storage
            return self._memory_ctx

    memory_ctx = _MemoryContextStub()
    runtime = _RuntimeStub(memory_ctx)
    request = RepairMotifHistoryRequest(project_id="demo_project", chapter_number=6)
    events: list[tuple[str, dict[str, object]]] = []

    def _on_step(step: str, payload: dict[str, object]) -> None:
        events.append((step, payload))

    execution = await execute_repair_motif_history(runtime, request, on_step_progress=_on_step)

    assert execution.project_id == "demo_project"
    assert execution.result["ok"] is True
    assert execution.result["motifs_touched"] == 4
    assert execution.result["occurrences_seen"] == 19
    assert execution.result["chapter_number"] == 6
    assert memory_ctx.calls == 1
    assert [item[0] for item in events] == [
        "motif_history_repair_start",
        "motif_history_repair_done",
    ]


def _build_runtime(tmp_path, runtime_settings) -> RuntimeServices:
    return RuntimeServices(
        settings=runtime_settings,
        router=ModelRouter(adapters={"mock": MockAdapter()}, default_provider="mock"),
        builder=PromptBuilder(),
        storage=FileSystemStorage(tmp_path),
    )


def _write_minimal_chapter_state(
    storage: FileSystemStorage, project_id: str, chapter_num: int
) -> ProjectLayout:
    layout = ProjectLayout(storage.ensure_project_dir(project_id))
    layout.ensure_dirs()
    chapter_outline = ChapterOutline(
        chapter_number=chapter_num,
        title="测试章",
        goal="推进冲突",
    )
    packet = ChapterStatePacket(
        chapter_number=chapter_num,
        chapter_outline=chapter_outline,
        canon_context={},
    )
    storage.save_json(layout.chapter_state_packet_path(chapter_num), packet.model_dump(mode="json"))
    storage.save_json(
        layout.chapter_bridge_path(chapter_num),
        ChapterBridge(to_chapter=chapter_num).model_dump(mode="json"),
    )
    storage.save_json(
        layout.chapter_plan_path(chapter_num),
        ChapterPlan().model_dump(mode="json"),
    )
    storage.save_json(
        layout.continuity_report_path(chapter_num),
        ContinuityReport().model_dump(mode="json"),
    )
    storage.save_json(
        layout.spec_path,
        {"genre": "fantasy", "tone": "epic"},
    )
    storage.save_json(
        layout.outline_path,
        {
            "total_chapters": chapter_num,
            "volume_mode": False,
            "volumes": [],
            "chapters": [
                {
                    "chapter_number": chapter_num,
                    "title": "测试章",
                    "goal": "推进冲突",
                }
            ],
        },
    )
    storage.save_text(layout.chapter_path(chapter_num), "原始正文")
    return layout


def test_summarize_exit_state_unwraps_chapter_outcome() -> None:
    """summarize_exit_state is the single entry point accepting a ChapterOutcome
    container; it must unwrap to chapter_exit_state and surface must_carry_forward.
    summarize_chapter_exit_state itself stays strict on ChapterExitState so mypy
    can intercept accidental container/inner-field confusion.
    """
    outcome = ChapterOutcome(
        source_chapter=2,
        chapter_exit_state=ChapterExitState(
            chapter_number=2,
            must_carry_forward=["承接线索一", "承接线索二"],
        ),
    )

    assert summarize_exit_state(outcome) == "承接线索一；承接线索二"
    assert summarize_exit_state(None) == "本章暂无额外承接事项。"


def test_summarize_exit_state_handles_outcome_without_exit_state() -> None:
    """A ChapterOutcome with chapter_exit_state=None must not crash."""
    outcome = ChapterOutcome(source_chapter=2, chapter_exit_state=None)

    assert summarize_exit_state(outcome) == "本章暂无额外承接事项。"


async def test_execute_manual_revision_writes_status_and_marks_downstream(
    tmp_path,
    runtime_settings,
) -> None:
    runtime = _build_runtime(tmp_path, runtime_settings)
    project_id = "manual_revision_demo"
    layout = ProjectLayout(runtime.storage.ensure_project_dir(project_id))
    layout.ensure_dirs()
    runtime.storage.save_json(
        layout.canon_dir / "canon_current.json",
        {"project_id": project_id, "current_chapter": 2},
    )
    runtime.storage.save_text(layout.chapter_path(1), "old chapter one")
    runtime.storage.save_text(layout.chapter_path(2), "chapter two")
    runtime.storage.save_text(layout.states_dir / "chapter_001_snapshot.txt", "old chapter one")
    runtime.storage.save_text(layout.states_dir / "chapter_002_snapshot.txt", "chapter two")
    runtime.storage.save_json(layout.chapter_checkpoint_path(2), {"chapter": 2})

    execution = await execute_manual_revision(
        runtime,
        ManualRevisionRequest(
            project_id=project_id,
            chapter_number=1,
            text="new chapter one",
            scope="downstream",
            reason="unit_test",
        ),
    )

    assert execution.result["status"] == "applied"
    assert layout.chapter_path(1).read_text(encoding="utf-8") == "new chapter one"
    status_path = layout.states_dir / "final_revision_status" / "chapter_001.json"
    status = runtime.storage.load_json(status_path)
    assert status["reason"] == "unit_test"
    assert status["requires_humanize"] is True
    assert status["requires_final_verification"] is True
    assert status["publication_status"] == "blocked_pending_finalize"
    assert status["affected_chapters"] == [2]
    assert status["invalidated_downstream_chapters"] == [2]
    assert scoped_stale_chapters(runtime.storage, layout) == {2}
    assert not layout.chapter_checkpoint_path(2).exists()
    assert (layout.states_dir / "final_revision_versions" / "chapter_001").exists()


async def test_resolve_guard_checkpoint_uses_pending_outcome_exit_state_summary(
    tmp_path,
    runtime_settings,
    monkeypatch,
) -> None:
    runtime = _build_runtime(tmp_path, runtime_settings)
    layout = _write_minimal_chapter_state(runtime.storage, "long_demo", 2)
    steps: list[tuple[str, object]] = []
    old_outcome = ChapterOutcome(
        source_chapter=2,
        chapter_summary="旧摘要",
        creative_report=CreativeReport(structured_summary="旧创作报告"),
        chapter_exit_state=ChapterExitState(
            chapter_number=2,
            must_carry_forward=["旧承接事项"],
        ),
        structured_summary="旧结构化摘要",
    )
    pending_state = PendingChapterReviewState(
        current_text="旧正文",
        performed_edits=0,
        outcome=old_outcome,
        alignment_report=AlignmentReport(
            alignment_score=9.0,
            risk_level="low",
            conflict_level="low",
            summary="旧对齐",
        ),
        chapter_repair_report=None,
        causal_report=CausalValidationReport(
            causal_score=9.0,
            summary="旧因果",
            issues=[],
            causal_link_verified=True,
        ),
        continuity_report=ContinuityReport(
            continuity_score=9.0,
            summary="旧连贯",
            issues=[],
        ),
        repair_plan=RepairPlan(expected_outcome="old"),
        eval_report=EvalReport(
            overall_score=8.5,
            passed=True,
            threshold=6.0,
            summary="旧质量",
        ),
        guard_decision=None,
        warnings=(),
    )
    bundle = SimpleNamespace(layout=layout, style_profile=None)
    checkpoint = build_guard_checkpoint(
        bundle,
        2,
        current_text=pending_state.current_text,
        alignment_report=pending_state.alignment_report,
        continuity_report=pending_state.continuity_report,
        eval_report=pending_state.eval_report,
        causal_report=pending_state.causal_report,
        guard_decision=None,
    )
    session_state = GuardCheckpointSessionState(
        checkpoint_id=checkpoint.checkpoint_id,
        project_id="long_demo",
        chapter_number=2,
        canon_watermark=0,
        pending_result=serialize_pending_result(pending_state),
    )

    class _FakeRunner:
        _storage = runtime.storage
        _router = runtime.router
        _builder = runtime.builder
        _settings = runtime.settings

        def create_execution_context(self):
            return SimpleNamespace(
                storage=runtime.storage,
                router=runtime.router,
                builder=runtime.builder,
                settings=runtime.settings,
                config=SimpleNamespace(),
                on_step=lambda step, data: steps.append((step, data)),
            )

    @asynccontextmanager
    async def _fake_chapter_runner_memory_lifecycle(*_args, **_kwargs):
        yield _FakeRunner(), None

    async def _fake_prompt_leak_repair(**kwargs):
        return SimpleNamespace(
            applied=False,
            report_updated=False,
            text=kwargs["current_text"],
            chapter_repair_report=kwargs["chapter_repair_report"],
            used_deterministic_fallback=False,
        )

    async def _fake_finalize(_context, *, review, trace, **_kwargs):
        del trace
        assert _kwargs["allow_contract_audit_auto_repair"] is False
        assert _kwargs["allow_carry_forward_auto_repair"] is True
        assert review.outcome.chapter_exit_state is not None
        assert [item.text for item in review.outcome.chapter_exit_state.must_carry_forward] == [
            "旧承接事项"
        ]
        return ChapterResult(
            meta=ChapterMeta(chapter_number=2, title="测试章", word_count=4),
            text="最终正文",
            canon_delta=ChapterOutcome(source_chapter=2, chapter_summary="新摘要"),
            creative_report=CreativeReport(structured_summary="新创作报告"),
            eval_report=EvalReport(
                overall_score=9.1,
                passed=True,
                threshold=6.0,
                summary="新质量",
            ),
            alignment_report=AlignmentReport(
                alignment_score=9.2,
                risk_level="low",
                conflict_level="low",
                summary="新对齐",
            ),
            causal_report=CausalValidationReport(
                causal_score=9.3,
                summary="新因果",
                issues=[],
                causal_link_verified=True,
            ),
            continuity_report=ContinuityReport(
                continuity_score=9.4,
                summary="新连贯",
                issues=[],
            ),
            repair_plan=RepairPlan(expected_outcome="new"),
            bridge=ChapterBridge(to_chapter=2, bridge_summary="新桥接"),
            chapter_exit_state=ChapterExitState(
                chapter_number=2,
                must_carry_forward=["新承接事项"],
            ),
            warnings=[],
        )

    monkeypatch.setattr(
        chapter_session_handlers,
        "_chapter_runner_memory_lifecycle",
        _fake_chapter_runner_memory_lifecycle,
    )
    monkeypatch.setattr(
        chapter_session_handlers,
        "repair_confirmed_prompt_leaks_with_patch",
        _fake_prompt_leak_repair,
    )
    monkeypatch.setattr(
        chapter_session_handlers,
        "word_count_archive_gate_enabled",
        lambda _settings: False,
    )
    monkeypatch.setattr(
        chapter_session_handlers,
        "finalize_chapter_result",
        _fake_finalize,
    )

    result = await chapter_session_handlers.resolve_guard_checkpoint(
        runtime,
        ResolveChapterCheckpointRequest(
            project_id="long_demo",
            chapter_number=2,
            checkpoint_id=checkpoint.checkpoint_id,
            option_id="accept_and_finalize",
            notes="",
        ),
        bundle=bundle,
        session_state=session_state,
        checkpoint=checkpoint,
        on_step_progress=lambda step, data: steps.append((step, data)),
    )

    assert result.status == "completed"
    assert result.chapter_exit_summary == "旧承接事项"
    assert result.preview == "最终正文"
    assert result.metadata["causal_score"] == pytest.approx(9.3)
    assert result.continuity_score == pytest.approx(9.4)


async def test_resolve_guard_checkpoint_recovers_legacy_alignment_archive_block_in_ai_auto_mode(
    tmp_path,
    runtime_settings,
    monkeypatch,
) -> None:
    runtime = _build_runtime(tmp_path, runtime_settings)
    layout = _write_minimal_chapter_state(runtime.storage, "long_demo", 2)
    pending_state = PendingChapterReviewState(
        current_text="进入最终化前正文",
        performed_edits=0,
        outcome=ChapterOutcome(
            source_chapter=2,
            chapter_summary="旧摘要",
            creative_report=CreativeReport(structured_summary="旧创作报告"),
            chapter_exit_state=ChapterExitState(chapter_number=2),
        ),
        alignment_report=AlignmentReport(
            alignment_score=9.0,
            risk_level="low",
            conflict_level="low",
            summary="旧对齐",
        ),
        chapter_repair_report=None,
        causal_report=CausalValidationReport(
            causal_score=9.0,
            summary="旧因果",
            issues=[],
            causal_link_verified=True,
        ),
        continuity_report=ContinuityReport(
            continuity_score=9.0,
            summary="旧连贯",
            issues=[],
        ),
        repair_plan=RepairPlan(expected_outcome="old"),
        eval_report=EvalReport(
            overall_score=8.5,
            passed=True,
            threshold=6.0,
            summary="旧质量",
        ),
        guard_decision=None,
        warnings=(),
    )
    bundle = SimpleNamespace(
        layout=layout,
        style_profile=None,
        canon_state=SimpleNamespace(current_chapter=0),
    )
    checkpoint = build_guard_checkpoint(
        bundle,
        2,
        current_text=pending_state.current_text,
        alignment_report=pending_state.alignment_report,
        continuity_report=pending_state.continuity_report,
        eval_report=pending_state.eval_report,
        causal_report=pending_state.causal_report,
        guard_decision=None,
    )
    session_state = GuardCheckpointSessionState(
        checkpoint_id=checkpoint.checkpoint_id,
        project_id="long_demo",
        chapter_number=2,
        canon_watermark=0,
        pending_result=serialize_pending_result(pending_state),
    )
    low_eval = EvalReport(
        overall_score=2.6,
        passed=False,
        threshold=6.0,
        summary="拼接污染",
    )
    runtime.storage.save_json(layout.eval_report_path(2), low_eval.model_dump(mode="json"))
    orphan_dir = layout.states_dir / "orphan_chapter_drafts" / "chapter_002"
    orphan_dir.mkdir(parents=True, exist_ok=True)
    (orphan_dir / "20260619T195524000000Z.md").write_text("污染正文", encoding="utf-8")
    steps: list[tuple[str, object]] = []

    class _FakeRunner:
        _storage = runtime.storage
        _router = runtime.router
        _builder = runtime.builder
        _settings = runtime.settings

        def create_execution_context(self):
            return SimpleNamespace(
                storage=runtime.storage,
                router=runtime.router,
                builder=runtime.builder,
                settings=runtime.settings,
                config=SimpleNamespace(),
                on_step=lambda step, data: steps.append((step, data)),
            )

    @asynccontextmanager
    async def _fake_chapter_runner_memory_lifecycle(*_args, **_kwargs):
        yield _FakeRunner(), None

    async def _fake_prompt_leak_repair(**kwargs):
        return SimpleNamespace(
            applied=False,
            report_updated=False,
            text=kwargs["current_text"],
            chapter_repair_report=kwargs["chapter_repair_report"],
            used_deterministic_fallback=False,
        )

    async def _fake_finalize(_context, *, review, trace, **_kwargs):
        del _context, review, trace
        refreshed_alignment = AlignmentReport(
            alignment_score=2.6,
            risk_level="high",
            conflict_level="low",
            summary="终端润色后的正文缺少计划落点",
            missing_main_points=["江野确认编号后选择保密"],
            repair_actions=["补写江野确认编号并选择保密的明确决策"],
            source_text_hash=source_text_hash("污染正文"),
        )
        runtime.storage.save_json(
            layout.alignment_report_path(2),
            refreshed_alignment.model_dump(mode="json"),
        )
        raise ConsistencyViolationError(
            ["章节 2 对齐分 2.6 低于归档阈值 8.0，拒绝保存并强制重新规划。"]
        )

    monkeypatch.setattr(
        chapter_session_handlers,
        "_chapter_runner_memory_lifecycle",
        _fake_chapter_runner_memory_lifecycle,
    )
    monkeypatch.setattr(
        chapter_session_handlers,
        "repair_confirmed_prompt_leaks_with_patch",
        _fake_prompt_leak_repair,
    )
    monkeypatch.setattr(
        chapter_session_handlers,
        "word_count_archive_gate_enabled",
        lambda _settings: False,
    )
    monkeypatch.setattr(
        chapter_session_handlers,
        "finalize_chapter_result",
        _fake_finalize,
    )

    result = await chapter_session_handlers.resolve_guard_checkpoint(
        runtime,
        ResolveChapterCheckpointRequest(
            project_id="long_demo",
            chapter_number=2,
            checkpoint_id=checkpoint.checkpoint_id,
            option_id="accept_and_finalize",
            notes="",
            repair_control_mode="ai_auto",
        ),
        bundle=bundle,
        session_state=session_state,
        checkpoint=checkpoint,
        on_step_progress=lambda step, data: steps.append((step, data)),
    )

    assert result.status == "needs_decision"
    assert result.preview == "污染正文"
    assert result.overall_score == pytest.approx(2.6)
    assert result.metadata["archive_quality_block"] is True
    assert result.metadata["alignment_score"] == pytest.approx(2.6)
    assert result.metadata["archive_quality_auto_repair_scheduled"] is True
    assert result.metadata["archive_quality_repair_attempts"] == 0
    assert layout.chapter_review_draft_path(2).read_text(encoding="utf-8") == "污染正文"
    assert result.checkpoint is not None
    assert any(
        option.option_id == "apply_repairs_and_finalize" and option.is_recommended
        for option in result.checkpoint.options
    )
    persisted_session = runtime.storage.load_json(layout.chapter_session_path(2))
    assert persisted_session["pending_result"]["alignment_report"][
        "alignment_score"
    ] == pytest.approx(2.6)
    assert any(step == "archive_quality_retry_checkpoint" for step, _data in steps)


async def test_resolve_guard_checkpoint_recovers_carry_forward_archive_block(
    tmp_path,
    runtime_settings,
    monkeypatch,
) -> None:
    runtime = _build_runtime(tmp_path, runtime_settings)
    layout = _write_minimal_chapter_state(runtime.storage, "long_demo", 2)
    pending_state = PendingChapterReviewState(
        current_text="待归档正文",
        performed_edits=0,
        outcome=ChapterOutcome(
            source_chapter=2,
            chapter_summary="旧摘要",
            creative_report=CreativeReport(structured_summary="旧创作报告"),
            chapter_exit_state=ChapterExitState(chapter_number=2),
        ),
        alignment_report=AlignmentReport(
            alignment_score=9.0,
            risk_level="low",
            conflict_level="low",
            summary="旧对齐",
        ),
        chapter_repair_report=None,
        causal_report=CausalValidationReport(
            causal_score=9.0,
            summary="旧因果",
            issues=[],
            causal_link_verified=True,
        ),
        continuity_report=ContinuityReport(
            continuity_score=9.0,
            summary="旧连贯",
            issues=[],
        ),
        repair_plan=RepairPlan(expected_outcome="old"),
        eval_report=EvalReport(
            overall_score=8.5,
            passed=True,
            threshold=6.0,
            summary="旧质量",
        ),
        guard_decision=None,
        warnings=(),
    )
    bundle = SimpleNamespace(
        layout=layout,
        style_profile=None,
        canon_state=SimpleNamespace(current_chapter=0),
    )
    checkpoint = build_guard_checkpoint(
        bundle,
        2,
        current_text=pending_state.current_text,
        alignment_report=pending_state.alignment_report,
        continuity_report=pending_state.continuity_report,
        eval_report=pending_state.eval_report,
        causal_report=pending_state.causal_report,
        guard_decision=None,
    )
    session_state = GuardCheckpointSessionState(
        checkpoint_id=checkpoint.checkpoint_id,
        project_id="long_demo",
        chapter_number=2,
        canon_watermark=0,
        pending_result=serialize_pending_result(pending_state),
    )
    steps: list[tuple[str, object]] = []

    class _FakeRunner:
        _storage = runtime.storage
        _router = runtime.router
        _builder = runtime.builder
        _settings = runtime.settings

        def create_execution_context(self):
            return SimpleNamespace(
                storage=runtime.storage,
                router=runtime.router,
                builder=runtime.builder,
                settings=runtime.settings,
                config=SimpleNamespace(),
                on_step=lambda step, data: steps.append((step, data)),
            )

    @asynccontextmanager
    async def _fake_chapter_runner_memory_lifecycle(*_args, **_kwargs):
        yield _FakeRunner(), None

    async def _fake_prompt_leak_repair(**kwargs):
        return SimpleNamespace(
            applied=False,
            report_updated=False,
            text=kwargs["current_text"],
            chapter_repair_report=kwargs["chapter_repair_report"],
            used_deterministic_fallback=False,
        )

    async def _fake_finalize(_context, *, review, trace, **_kwargs):
        del _context, review, trace
        raise ConsistencyViolationError(
            ["章节 2 有 1 个上一章必须承接的开放项在本章正文与前章结尾均无文本痕迹。"]
        )

    monkeypatch.setattr(
        chapter_session_handlers,
        "_chapter_runner_memory_lifecycle",
        _fake_chapter_runner_memory_lifecycle,
    )
    monkeypatch.setattr(
        chapter_session_handlers,
        "repair_confirmed_prompt_leaks_with_patch",
        _fake_prompt_leak_repair,
    )
    monkeypatch.setattr(
        chapter_session_handlers,
        "word_count_archive_gate_enabled",
        lambda _settings: False,
    )
    monkeypatch.setattr(
        chapter_session_handlers,
        "finalize_chapter_result",
        _fake_finalize,
    )

    result = await chapter_session_handlers.resolve_guard_checkpoint(
        runtime,
        ResolveChapterCheckpointRequest(
            project_id="long_demo",
            chapter_number=2,
            checkpoint_id=checkpoint.checkpoint_id,
            option_id="accept_and_finalize",
            notes="",
        ),
        bundle=bundle,
        session_state=session_state,
        checkpoint=checkpoint,
        on_step_progress=lambda step, data: steps.append((step, data)),
    )

    assert result.status == "needs_decision"
    assert result.metadata["carry_forward_block"] is True
    assert "必须承接的开放项" in result.metadata["carry_forward_block_summary"]
    assert any(step == "carry_forward_retry_checkpoint" for step, _data in steps)


async def test_resolve_guard_checkpoint_state_block_keeps_exit_summary(
    tmp_path,
    runtime_settings,
    monkeypatch,
) -> None:
    runtime = _build_runtime(tmp_path, runtime_settings)
    layout = _write_minimal_chapter_state(runtime.storage, "long_demo", 2)
    pending_state = PendingChapterReviewState(
        current_text="待归档正文",
        performed_edits=0,
        outcome=ChapterOutcome(
            source_chapter=2,
            chapter_summary="旧摘要",
            creative_report=CreativeReport(structured_summary="旧创作报告"),
            chapter_exit_state=ChapterExitState(
                chapter_number=2,
                must_carry_forward=["状态阻断承接"],
            ),
        ),
        alignment_report=AlignmentReport(
            alignment_score=9.0,
            risk_level="low",
            conflict_level="low",
            summary="旧对齐",
        ),
        chapter_repair_report=None,
        causal_report=CausalValidationReport(
            causal_score=9.0,
            summary="旧因果",
            issues=[],
            causal_link_verified=True,
        ),
        continuity_report=ContinuityReport(
            continuity_score=9.0,
            summary="旧连贯",
            issues=[],
        ),
        repair_plan=RepairPlan(expected_outcome="old"),
        eval_report=EvalReport(
            overall_score=8.5,
            passed=True,
            threshold=6.0,
            summary="旧质量",
        ),
        guard_decision=None,
        warnings=(),
    )
    bundle = SimpleNamespace(
        layout=layout,
        style_profile=None,
        canon_state=SimpleNamespace(current_chapter=0),
    )
    checkpoint = build_guard_checkpoint(
        bundle,
        2,
        current_text=pending_state.current_text,
        alignment_report=pending_state.alignment_report,
        continuity_report=pending_state.continuity_report,
        eval_report=pending_state.eval_report,
        causal_report=pending_state.causal_report,
        guard_decision=None,
    )
    session_state = GuardCheckpointSessionState(
        checkpoint_id=checkpoint.checkpoint_id,
        project_id="long_demo",
        chapter_number=2,
        canon_watermark=0,
        pending_result=serialize_pending_result(pending_state),
    )

    class _FakeRunner:
        _storage = runtime.storage
        _router = runtime.router
        _builder = runtime.builder
        _settings = runtime.settings

        def create_execution_context(self):
            return SimpleNamespace(
                storage=runtime.storage,
                router=runtime.router,
                builder=runtime.builder,
                settings=runtime.settings,
                config=SimpleNamespace(),
                on_step=lambda _step, _data: None,
            )

    @asynccontextmanager
    async def _fake_chapter_runner_memory_lifecycle(*_args, **_kwargs):
        yield _FakeRunner(), None

    async def _fake_prompt_leak_repair(**kwargs):
        return SimpleNamespace(
            applied=False,
            report_updated=False,
            text=kwargs["current_text"],
            chapter_repair_report=kwargs["chapter_repair_report"],
            used_deterministic_fallback=False,
        )

    async def _fake_finalize(_context, *, review, trace, **_kwargs):
        del _context, review, trace
        raise ConsistencyViolationError(["LLM 状态裁判要求阻断归档：缺少证据支撑。"])

    monkeypatch.setattr(
        chapter_session_handlers,
        "_chapter_runner_memory_lifecycle",
        _fake_chapter_runner_memory_lifecycle,
    )
    monkeypatch.setattr(
        chapter_session_handlers,
        "repair_confirmed_prompt_leaks_with_patch",
        _fake_prompt_leak_repair,
    )
    monkeypatch.setattr(
        chapter_session_handlers,
        "word_count_archive_gate_enabled",
        lambda _settings: False,
    )
    monkeypatch.setattr(
        chapter_session_handlers,
        "finalize_chapter_result",
        _fake_finalize,
    )

    result = await chapter_session_handlers.resolve_guard_checkpoint(
        runtime,
        ResolveChapterCheckpointRequest(
            project_id="long_demo",
            chapter_number=2,
            checkpoint_id=checkpoint.checkpoint_id,
            option_id="accept_and_finalize",
            notes="",
        ),
        bundle=bundle,
        session_state=session_state,
        checkpoint=checkpoint,
    )

    assert result.status == "needs_decision"
    assert result.chapter_exit_summary == "状态阻断承接"
    assert result.metadata["state_adjudication_block"] is True


@pytest.mark.parametrize("authoring_wait", [False, True])
async def test_resolve_guard_checkpoint_contract_audit_repair_keeps_exit_summary(
    tmp_path,
    runtime_settings,
    monkeypatch,
    authoring_wait,
) -> None:
    runtime = _build_runtime(tmp_path, runtime_settings)
    layout = _write_minimal_chapter_state(runtime.storage, "long_demo", 2)
    original_archived_text = layout.chapter_path(2).read_bytes()
    pending_state = PendingChapterReviewState(
        current_text="待归档正文",
        performed_edits=0,
        outcome=ChapterOutcome(
            source_chapter=2,
            chapter_summary="旧摘要",
            creative_report=CreativeReport(structured_summary="旧创作报告"),
            chapter_exit_state=ChapterExitState(
                chapter_number=2,
                must_carry_forward=["契约阻断承接"],
            ),
        ),
        alignment_report=AlignmentReport(
            alignment_score=9.0,
            risk_level="low",
            conflict_level="low",
            summary="旧对齐",
        ),
        chapter_repair_report=None,
        causal_report=CausalValidationReport(
            causal_score=9.0,
            summary="旧因果",
            issues=[],
            causal_link_verified=True,
        ),
        continuity_report=ContinuityReport(
            continuity_score=9.0,
            summary="旧连贯",
            issues=[],
        ),
        repair_plan=RepairPlan(expected_outcome="old"),
        eval_report=EvalReport(
            overall_score=8.5,
            passed=True,
            threshold=6.0,
            summary="旧质量",
        ),
        guard_decision=None,
        warnings=(),
    )
    bundle = SimpleNamespace(
        layout=layout,
        style_profile=None,
        canon_state=SimpleNamespace(current_chapter=0),
    )
    checkpoint = build_guard_checkpoint(
        bundle,
        2,
        current_text=pending_state.current_text,
        alignment_report=pending_state.alignment_report,
        continuity_report=pending_state.continuity_report,
        eval_report=pending_state.eval_report,
        causal_report=pending_state.causal_report,
        guard_decision=None,
    )
    session_state = GuardCheckpointSessionState(
        checkpoint_id=checkpoint.checkpoint_id,
        project_id="long_demo",
        chapter_number=2,
        canon_watermark=0,
        pending_result=serialize_pending_result(pending_state),
    )

    class _FakeRunner:
        _storage = runtime.storage
        _router = runtime.router
        _builder = runtime.builder
        _settings = runtime.settings

        def create_execution_context(self):
            return SimpleNamespace(
                storage=runtime.storage,
                router=runtime.router,
                builder=runtime.builder,
                settings=runtime.settings,
                config=SimpleNamespace(),
                on_step=lambda _step, _data: None,
            )

    @asynccontextmanager
    async def _fake_chapter_runner_memory_lifecycle(*_args, **_kwargs):
        yield _FakeRunner(), None

    async def _fake_prompt_leak_repair(**kwargs):
        return SimpleNamespace(
            applied=False,
            report_updated=False,
            text=kwargs["current_text"],
            chapter_repair_report=kwargs["chapter_repair_report"],
            used_deterministic_fallback=False,
        )

    finalization_attempts = []

    async def _fake_finalize(_context, *, review, trace, **_kwargs):
        if authoring_wait:
            from novel_forge.persistence.authoring_store import AuthoringAcceptanceRequired

            finalization_attempts.append(review.refinement_done)
            if review.refinement_done:
                assert review.current_text == "必要检查后的最终正文"
                raise RuntimeError("fixture: accepted final candidate reached")
            exc = AuthoringAcceptanceRequired("必要检查后的最终正文")
            exc.review_state = {"current_text": exc.text}
            raise exc
        raise ConsistencyViolationError(["章节契约执行审计要求阻断归档：repair"])

    monkeypatch.setattr(
        chapter_session_handlers,
        "_chapter_runner_memory_lifecycle",
        _fake_chapter_runner_memory_lifecycle,
    )
    monkeypatch.setattr(
        chapter_session_handlers,
        "repair_confirmed_prompt_leaks_with_patch",
        _fake_prompt_leak_repair,
    )
    monkeypatch.setattr(
        chapter_session_handlers,
        "word_count_archive_gate_enabled",
        lambda _settings: False,
    )
    monkeypatch.setattr(
        chapter_session_handlers,
        "finalize_chapter_result",
        _fake_finalize,
    )
    monkeypatch.setattr(
        chapter_session_handlers,
        "is_contract_audit_block_exception",
        lambda _exc: True,
    )
    monkeypatch.setattr(
        chapter_session_handlers,
        "compile_contract_audit_repair_ticket",
        lambda **_kwargs: RepairTicket(
            ticket_id="contract-test",
            chapter_number=2,
            source_module="contract_execution_audit",
            issue_type="required_progression_missing",
            severity="medium",
            target_summary="补齐契约进展",
            repair_goal="补齐契约进展",
            blocking=True,
        ),
    )

    result = await chapter_session_handlers.resolve_guard_checkpoint(
        runtime,
        ResolveChapterCheckpointRequest(
            project_id="long_demo",
            chapter_number=2,
            checkpoint_id=checkpoint.checkpoint_id,
            option_id="accept_and_finalize",
            notes="",
        ),
        bundle=bundle,
        session_state=session_state,
        checkpoint=checkpoint,
    )

    assert result.status == "needs_decision"
    assert result.chapter_exit_summary == "契约阻断承接"
    if authoring_wait:
        from novel_forge.workspace.sessions.chapter_session_state import load_session_state

        assert result.metadata["authoring_final_acceptance"] is True
        assert result.preview == "必要检查后的最终正文"
        assert layout.chapter_path(2).read_bytes() == original_archived_text
        assert result.checkpoint is not None
        assert result.checkpoint.checkpoint_id != checkpoint.checkpoint_id
        assert not next(
            option
            for option in result.checkpoint.options
            if option.option_id == "accept_and_finalize"
        ).is_recommended
        resumed = load_session_state(bundle, 2)
        assert resumed.pending_result["authoring_refined_text_hash"] == source_text_hash(
            result.preview
        )
        with pytest.raises(RuntimeError, match="accepted final candidate reached"):
            await chapter_session_handlers.resolve_guard_checkpoint(
                runtime,
                ResolveChapterCheckpointRequest(
                    project_id="long_demo",
                    chapter_number=2,
                    checkpoint_id=result.checkpoint.checkpoint_id,
                    option_id="accept_and_finalize",
                    notes="明确验收新正文",
                ),
                bundle=bundle,
                session_state=resumed,
                checkpoint=result.checkpoint,
            )
        assert finalization_attempts == [False, True]
        assert layout.chapter_path(2).read_bytes() == original_archived_text
        return
    assert result.metadata["contract_execution_repair_checkpoint"] is True
    assert result.checkpoint is not None
    assert any(
        option.option_id == "apply_repairs_and_finalize" and option.is_recommended
        for option in result.checkpoint.options
    )


async def test_chapter_causal_repair_impl_accepts_source_text_hash_payload(
    tmp_path,
    runtime_settings,
    monkeypatch,
) -> None:
    runtime = _build_runtime(tmp_path, runtime_settings)
    layout = _write_minimal_chapter_state(runtime.storage, "long_demo", 2)
    runtime.storage.save_json(
        layout.chapter_causal_report_path(2),
        {
            "causal_score": 4.2,
            "summary": "发现因果问题",
            "causal_link_verified": False,
            "source_text_hash": "legacy-hash-value",
            "issues": [
                {
                    "issue_type": "event_without_cause",
                    "severity": "high",
                    "location": "第2段",
                    "summary": "事件缺少触发原因",
                    "evidence": "A突然发生",
                    "fix_suggestion": "补充前置触发",
                }
            ],
        },
    )

    async def _fake_repair_run(_self, payload):
        return CausalRepairResult(
            revised_text=payload.chapter_text,
            issues=list(payload.causal_report.issues),
            applied=False,
            failure_reason="no-op",
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.causal_repair_step.CausalRepairStep.run",
        _fake_repair_run,
    )

    execution = await _execute_chapter_causal_repair_impl(
        runtime,
        RepairCausalRequest(project_id="long_demo", chapter_number=2),
    )

    assert execution.result.applied is False
    assert len(execution.result.issues) == 1
    assert execution.result.issues[0].issue_type == "event_without_cause"
    # No-op repair did not recheck this old report. Do not invent a new hash.
    assert runtime.storage.load_json(layout.chapter_causal_report_path(2))["source_text_hash"] == "legacy-hash-value"


async def test_chapter_causal_repair_impl_resumes_from_progress_after_failure(
    tmp_path,
    runtime_settings,
    monkeypatch,
) -> None:
    runtime = _build_runtime(tmp_path, runtime_settings)
    layout = _write_minimal_chapter_state(runtime.storage, "long_demo", 2)
    issue = CausalIssue(
        issue_type="event_without_cause",
        severity="high",
        location="第2段",
        summary="事件缺少触发原因",
        evidence="A突然发生",
        fix_suggestion="补充前置触发",
    )
    runtime.storage.save_json(
        layout.chapter_causal_report_path(2),
        CausalValidationReport(
            causal_score=4.0,
            summary="需修复",
            causal_link_verified=False,
            issues=[issue],
            source_text_hash="legacy-hash-value",
        ).model_dump(mode="json"),
    )

    calls = {"repair": 0, "reaudit": 0, "postprocess": 0}

    async def _fake_repair_run(_self, _payload):
        calls["repair"] += 1
        return CausalRepairResult(
            revised_text="修复后正文",
            issues=[issue],
            applied=True,
        )

    async def _fake_reaudit_run(_self, _payload):
        calls["reaudit"] += 1
        # Return the same issue so the merge correctly marks it as resolved
        # (issue_type and summary match), keeping _new_causal_issues_count = 0
        return CausalValidationReport(
            causal_score=9.6,
            summary="修复通过",
            issues=[issue],
            causal_link_verified=True,
        )

    async def _flaky_postprocess(*_args, **_kwargs):
        calls["postprocess"] += 1
        if calls["postprocess"] == 1:
            raise RuntimeError("postprocess boom")
        return True

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.causal_repair_step.CausalRepairStep.run",
        _fake_repair_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.steps.causal_validation_step.CausalValidationStep.run",
        _fake_reaudit_run,
    )
    monkeypatch.setattr(
        "novel_forge.workspace.helpers.execution_helpers._run_revised_text_postprocess",
        _flaky_postprocess,
    )

    req = RepairCausalRequest(project_id="long_demo", chapter_number=2)
    with pytest.raises(RuntimeError, match="postprocess boom"):
        await _execute_chapter_causal_repair_impl(runtime, req)

    progress_path = layout.states_dir / "chapter_002_repair_causal_progress.json"
    assert progress_path.exists()

    execution = await _execute_chapter_causal_repair_impl(runtime, req)

    assert execution.result.applied is True
    assert calls["repair"] == 1
    assert calls["reaudit"] == 1
    assert calls["postprocess"] == 2
    assert not progress_path.exists()


async def test_chapter_causal_repair_impl_manual_selection_overrides_exhausted_attempts(
    tmp_path,
    runtime_settings,
    monkeypatch,
) -> None:
    runtime = _build_runtime(tmp_path, runtime_settings)
    layout = _write_minimal_chapter_state(runtime.storage, "long_demo", 2)
    issue = CausalIssue(
        issue_type="opening_causal_gap",
        severity="high",
        location="第1段（开头承接处）",
        summary="开头承接缺失",
        evidence="首段未写明因果",
        fix_suggestion="补首段因果句",
    )
    runtime.storage.save_json(
        layout.chapter_causal_report_path(2),
        CausalValidationReport(
            causal_score=4.0,
            summary="需修复",
            causal_link_verified=False,
            issues=[issue],
        ).model_dump(mode="json"),
    )
    from novel_forge.pipeline.steps.causal_repair_step import CausalRepairStep

    issue_sig = CausalRepairStep.issue_signature(issue)
    runtime.storage.save_json(
        layout.states_dir / "chapter_002_causal_repair_attempts.json",
        {"schema_version": "1.0", "attempts": {issue_sig: 3}},
    )

    calls = {"repair": 0}

    async def _fake_repair_run(_self, payload):
        calls["repair"] += 1
        return CausalRepairResult(
            revised_text=payload.chapter_text,
            issues=list(payload.causal_report.issues),
            applied=False,
            failure_reason="manual retry executed",
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.causal_repair_step.CausalRepairStep.run",
        _fake_repair_run,
    )

    execution = await _execute_chapter_causal_repair_impl(
        runtime,
        RepairCausalRequest(
            project_id="long_demo",
            chapter_number=2,
            issue_indices=[0],
            allow_exhausted_retry=True,
        ),
    )

    assert calls["repair"] == 1
    assert execution.result.failure_reason == "manual retry executed"


async def test_chapter_causal_repair_impl_selected_indices_do_not_override_when_not_manual(
    tmp_path,
    runtime_settings,
    monkeypatch,
) -> None:
    runtime = _build_runtime(tmp_path, runtime_settings)
    layout = _write_minimal_chapter_state(runtime.storage, "long_demo", 2)
    issue = CausalIssue(
        issue_type="opening_causal_gap",
        severity="high",
        location="第1段（开头承接处）",
        summary="开头承接缺失",
        evidence="首段未写明因果",
        fix_suggestion="补首段因果句",
    )
    runtime.storage.save_json(
        layout.chapter_causal_report_path(2),
        CausalValidationReport(
            causal_score=4.0,
            summary="需修复",
            causal_link_verified=False,
            issues=[issue],
        ).model_dump(mode="json"),
    )
    from novel_forge.pipeline.steps.causal_repair_step import CausalRepairStep

    issue_sig = CausalRepairStep.issue_signature(issue)
    runtime.storage.save_json(
        layout.states_dir / "chapter_002_causal_repair_attempts.json",
        {"schema_version": "1.0", "attempts": {issue_sig: 3}},
    )

    calls = {"repair": 0}

    async def _fake_repair_run(_self, payload):
        calls["repair"] += 1
        return CausalRepairResult(
            revised_text=payload.chapter_text,
            issues=list(payload.causal_report.issues),
            applied=False,
            failure_reason="should not run",
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.causal_repair_step.CausalRepairStep.run",
        _fake_repair_run,
    )

    execution = await _execute_chapter_causal_repair_impl(
        runtime,
        RepairCausalRequest(project_id="long_demo", chapter_number=2, issue_indices=[0]),
    )

    assert calls["repair"] == 0
    assert "超过最大尝试次数" in (execution.result.failure_reason or "")


async def test_chapter_causal_repair_impl_recheck_includes_previous_chapter_ending(
    tmp_path,
    runtime_settings,
    monkeypatch,
) -> None:
    runtime = _build_runtime(tmp_path, runtime_settings)
    layout = _write_minimal_chapter_state(runtime.storage, "long_demo", 2)
    runtime.storage.save_text(layout.chapter_path(1), "上一章末尾锚点：铁门坠落，火星四溅。")
    issue = CausalIssue(
        issue_type="opening_causal_gap",
        severity="high",
        location="第1段（开头承接处）",
        summary="开头承接缺失",
        evidence="首段未写明因果",
        fix_suggestion="补首段因果句",
    )
    runtime.storage.save_json(
        layout.chapter_causal_report_path(2),
        CausalValidationReport(
            causal_score=4.0,
            summary="需修复",
            causal_link_verified=False,
            issues=[issue],
        ).model_dump(mode="json"),
    )

    async def _fake_repair_run(_self, payload):
        return CausalRepairResult(
            revised_text=payload.chapter_text + "\n\n补一行因果承接。",
            issues=list(payload.causal_report.issues),
            applied=True,
        )

    captured = {"previous_chapter_ending": None}

    async def _fake_reaudit_run(_self, payload):
        captured["previous_chapter_ending"] = payload.previous_chapter_ending
        return CausalValidationReport(
            causal_score=9.8,
            summary="修复通过",
            causal_link_verified=True,
            issues=[],
        )

    async def _fake_postprocess(*_args, **_kwargs):
        return True

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.causal_repair_step.CausalRepairStep.run",
        _fake_repair_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.steps.causal_validation_step.CausalValidationStep.run",
        _fake_reaudit_run,
    )
    monkeypatch.setattr(
        "novel_forge.workspace.helpers.execution_helpers._run_revised_text_postprocess",
        _fake_postprocess,
    )

    execution = await _execute_chapter_causal_repair_impl(
        runtime,
        RepairCausalRequest(project_id="long_demo", chapter_number=2, issue_indices=[0]),
    )

    assert execution.result.applied is True
    assert "上一章末尾锚点" in (captured["previous_chapter_ending"] or "")


async def test_chapter_continuity_repair_impl_surfaces_recheck_warning(
    tmp_path,
    runtime_settings,
    monkeypatch,
) -> None:
    runtime = _build_runtime(tmp_path, runtime_settings)
    layout = _write_minimal_chapter_state(runtime.storage, "long_demo", 1)
    report = runtime.storage.load_json(layout.continuity_report_path(1))
    report["source_text_hash"] = "previously-checked-text"
    runtime.storage.save_json(layout.continuity_report_path(1), report)
    runtime.storage.save_json(layout.eval_report_path(1), {"overall_score": 8, "source_text_hash": "previously-scored-text"})
    steps: list[tuple[str, object]] = []

    async def _fake_run_repair(_step, _payload):
        return ContinuityRepairResult(
            revised_text="修复后正文",
            repair_plan=RepairPlan(expected_outcome="done"),
            applied=True,
        )

    async def _fail_recheck(_self, _payload):
        raise RuntimeError("recheck boom")

    monkeypatch.setattr("novel_forge.pipeline.long.repair.run_continuity_repair", _fake_run_repair)
    monkeypatch.setattr(
        "novel_forge.pipeline.steps.continuity_eval_step.ContinuityEvalStep.run",
        _fail_recheck,
    )

    execution = await _execute_chapter_continuity_repair_impl(
        runtime,
        RepairContinuityRequest(project_id="long_demo", chapter_number=1),
        on_step_progress=lambda step, data: steps.append((step, data)),
    )

    assert execution.result.applied is True
    assert execution.result.warnings
    assert "continuity 重评估失败" in execution.result.warnings[0]
    assert any(step == "continuity_eval_after_repair_warning" for step, _data in steps)
    assert runtime.storage.load_json(layout.continuity_report_path(1))["source_text_hash"] == "previously-checked-text"
    assert runtime.storage.load_json(layout.eval_report_path(1))["source_text_hash"] == "previously-scored-text"


async def test_execute_reevaluate_chapter_refreshes_report_hashes(
    tmp_path,
    runtime_settings,
    monkeypatch,
) -> None:
    runtime = _build_runtime(tmp_path, runtime_settings)
    layout = _write_minimal_chapter_state(runtime.storage, "long_demo", 2)
    chapter_text = layout.chapter_path(2).read_text(encoding="utf-8")
    expected_hash = execution_module._source_text_hash(chapter_text)
    steps: list[tuple[str, object]] = []

    async def _fake_eval_run(_self, _text):
        return EvalReport(
            overall_score=8.8,
            passed=True,
            threshold=6.0,
            summary="质量稳定",
        )

    async def _fake_alignment_run(_self, _payload):
        return AlignmentReport(
            alignment_score=9.4,
            risk_level="low",
            conflict_level="low",
            summary="对齐稳定",
        )

    async def _fake_cont_run(_self, _payload):
        return ContinuityReport(
            continuity_score=9.1,
            summary="连贯性良好",
            issues=[],
        )

    async def _fake_causal_run(_self, _payload):
        return CausalValidationReport(
            causal_score=9.2,
            summary="因果链清晰",
            issues=[],
            causal_link_verified=True,
        )

    async def _fake_reading_power_run(_self, _payload):
        report = ReadingPowerReport(
            chapter=_payload.chapter_number,
            hook_type="mystery",
            hook_strength="strong",
            prev_hook_fulfilled=True,
            hook_description="门外传来未知脚步声",
        )
        report.compute_score()
        return report

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.evaluate_step.EvaluateStep.run",
        _fake_eval_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.steps.alignment_step.AlignmentStep.run",
        _fake_alignment_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.steps.continuity_eval_step.ContinuityEvalStep.run",
        _fake_cont_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.steps.causal_validation_step.CausalValidationStep.run",
        _fake_causal_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.steps.reading_power_eval_step.ReadingPowerEvalStep.run",
        _fake_reading_power_run,
    )

    execution = await execute_reevaluate_chapter(
        runtime,
        ReevaluateChapterRequest(project_id="long_demo", chapter_number=2),
        on_step_progress=lambda step, data: steps.append((step, data)),
    )

    assert execution.project_id == "long_demo"
    assert execution.result["overall_score"] == pytest.approx(8.8)
    assert execution.result["alignment_score"] == pytest.approx(9.4)
    assert execution.result["continuity_score"] == pytest.approx(9.1)
    assert execution.result["causal_score"] == pytest.approx(9.2)
    assert execution.result["reading_power_score"] == pytest.approx(2.5)
    assert execution.result["reading_power_is_fallback"] is False
    assert execution.result["warnings"] == []
    assert any(step == "reevaluate_chapter" for step, _ in steps)
    assert any(step == "reading_power_eval" for step, _ in steps)

    alignment_payload = runtime.storage.load_json(layout.alignment_report_path(2))
    eval_payload = runtime.storage.load_json(layout.eval_report_path(2))
    cont_payload = runtime.storage.load_json(layout.continuity_report_path(2))
    causal_payload = runtime.storage.load_json(layout.chapter_causal_report_path(2))
    reading_power_payload = runtime.storage.load_json(layout.reading_power_report_path(2))
    assert alignment_payload.get("alignment_score") == pytest.approx(9.4)
    assert eval_payload.get("source_text_hash") == expected_hash
    assert cont_payload.get("source_text_hash") == expected_hash
    assert causal_payload.get("source_text_hash") == expected_hash
    assert reading_power_payload.get("source_text_hash") == expected_hash
    assert reading_power_payload.get("evaluation_status") == "ok"


async def test_execute_reevaluate_chapter_resyncs_guard_checkpoint_snapshot(
    tmp_path,
    runtime_settings,
    monkeypatch,
) -> None:
    runtime = _build_runtime(tmp_path, runtime_settings)
    layout = _write_minimal_chapter_state(runtime.storage, "long_demo", 2)
    _sk_store = StoryKernelStore(layout.story_kernel_db_path)
    await _sk_store.init_db()
    await _sk_store.create_kernel("long_demo")
    await _sk_store.close()
    steps: list[tuple[str, object]] = []

    old_outcome = ChapterOutcome(
        source_chapter=2,
        chapter_summary="旧摘要",
        creative_report=CreativeReport(structured_summary="旧创作报告"),
        chapter_exit_state=ChapterExitState(chapter_number=2, location="旧地点", pov="旧视角"),
        structured_summary="旧结构化摘要",
    )
    pending_state = PendingChapterReviewState(
        current_text="旧正文",
        performed_edits=0,
        outcome=old_outcome,
        alignment_report=AlignmentReport(
            alignment_score=7.0,
            risk_level="medium",
            conflict_level="low",
            summary="旧对齐",
        ),
        chapter_repair_report=None,
        causal_report=CausalValidationReport(
            causal_score=7.2,
            summary="旧因果",
            issues=[],
            causal_link_verified=True,
        ),
        continuity_report=ContinuityReport(
            continuity_score=7.1,
            summary="旧连贯",
            issues=[],
        ),
        repair_plan=RepairPlan(expected_outcome="old"),
        eval_report=EvalReport(
            overall_score=7.3,
            passed=True,
            threshold=6.0,
            summary="旧质量",
        ),
        guard_decision=None,
        warnings=[],
    )
    checkpoint = build_guard_checkpoint(
        SimpleNamespace(layout=layout),
        2,
        current_text="旧正文",
        alignment_report=pending_state.alignment_report,
        continuity_report=pending_state.continuity_report,
        eval_report=pending_state.eval_report,
        causal_report=pending_state.causal_report,
        guard_decision=None,
    )
    runtime.storage.save_json(
        layout.chapter_checkpoint_path(2),
        checkpoint.model_dump(mode="json"),
    )
    save_guard_session_state(
        storage=runtime.storage,
        bundle=SimpleNamespace(layout=layout),
        chapter_number=2,
        checkpoint_id=checkpoint.checkpoint_id,
        project_id="long_demo",
        canon_watermark=0,
        notes="",
        pending_state=pending_state,
    )

    async def _fake_eval_run(_self, _text):
        return EvalReport(
            overall_score=8.8,
            passed=True,
            threshold=6.0,
            summary="质量稳定",
        )

    async def _fake_alignment_run(_self, _payload):
        return AlignmentReport(
            alignment_score=9.4,
            risk_level="low",
            conflict_level="low",
            summary="对齐稳定",
        )

    async def _fake_cont_run(_self, _payload):
        return ContinuityReport(
            continuity_score=9.1,
            summary="连贯性良好",
            issues=[],
        )

    async def _fake_causal_run(_self, _payload):
        return CausalValidationReport(
            causal_score=9.2,
            summary="因果链清晰",
            issues=[],
            causal_link_verified=True,
        )

    async def _fake_reading_power_run(_self, _payload):
        report = ReadingPowerReport(
            chapter=_payload.chapter_number,
            hook_type="mystery",
            hook_strength="strong",
            prev_hook_fulfilled=True,
            hook_description="门外传来未知脚步声",
        )
        report.compute_score()
        return report

    async def _fake_extract_and_validate(
        _runner,
        _bundle,
        _packet,
        _bridge,
        _plan,
        _current_text,
        chapter_number,
        _trace,
        _continuity_report,
        repair_exhausted=False,
    ):
        assert chapter_number == 2
        assert repair_exhausted is True
        return ChapterOutcome(
            source_chapter=2,
            chapter_summary="新摘要",
            creative_report=CreativeReport(structured_summary="新创作报告"),
            chapter_exit_state=ChapterExitState(
                chapter_number=2,
                location="新地点",
                pov="新视角",
            ),
            structured_summary="新结构化摘要",
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.evaluate_step.EvaluateStep.run",
        _fake_eval_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.steps.alignment_step.AlignmentStep.run",
        _fake_alignment_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.steps.continuity_eval_step.ContinuityEvalStep.run",
        _fake_cont_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.steps.causal_validation_step.CausalValidationStep.run",
        _fake_causal_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.steps.reading_power_eval_step.ReadingPowerEvalStep.run",
        _fake_reading_power_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.finalize_report.extract_and_validate",
        _fake_extract_and_validate,
    )

    execution = await execute_reevaluate_chapter(
        runtime,
        ReevaluateChapterRequest(project_id="long_demo", chapter_number=2),
        on_step_progress=lambda step, data: steps.append((step, data)),
    )

    assert execution.result["warnings"] == []
    assert any(step == "reevaluate_chapter" for step, _ in steps)

    refreshed_checkpoint = runtime.storage.load_json(layout.chapter_checkpoint_path(2))
    assert refreshed_checkpoint["checkpoint_id"] == checkpoint.checkpoint_id
    assert "对齐 9.4 / 连贯 9.1 / 质量 8.8 / 因果 9.2" in refreshed_checkpoint["summary"]

    refreshed_session = runtime.storage.load_json(layout.chapter_session_path(2))
    pending_payload = refreshed_session["pending_result"]
    assert pending_payload["current_text"] == "原始正文"
    assert pending_payload["alignment_report"]["alignment_score"] == pytest.approx(9.4)
    assert pending_payload["continuity_report"]["continuity_score"] == pytest.approx(9.1)
    assert pending_payload["causal_report"]["causal_score"] == pytest.approx(9.2)
    assert pending_payload["eval_report"]["overall_score"] == pytest.approx(8.8)
    assert pending_payload["outcome"]["structured_summary"] == "新结构化摘要"


async def test_execute_polish_chapter_surfaces_rescore_warning(
    tmp_path,
    runtime_settings,
    monkeypatch,
) -> None:
    runtime = _build_runtime(tmp_path, runtime_settings)
    _write_minimal_chapter_state(runtime.storage, "long_demo", 1)
    steps: list[tuple[str, object]] = []

    async def _fake_polish(_self, _payload):
        return PolishResult(
            polished_text="润色后正文",
            original_word_count=4,
            polished_word_count=5,
        )

    async def _fail_rescore(_self, _text):
        raise RuntimeError("rescore boom")

    monkeypatch.setattr("novel_forge.pipeline.steps.polish_step.PolishStep.run", _fake_polish)
    monkeypatch.setattr("novel_forge.pipeline.steps.evaluate_step.EvaluateStep.run", _fail_rescore)

    execution = await execute_polish_chapter(
        runtime,
        PolishChapterRequest(project_id="long_demo", chapter_number=1),
        on_step_progress=lambda step, data: steps.append((step, data)),
    )

    assert execution.result.polished_text == "润色后正文"
    assert execution.result.warnings
    assert "质量重评分失败" in execution.result.warnings[0]
    assert any(step == "evaluate_warning" for step, _data in steps)


async def test_execute_polish_chapter_refreshes_exit_state_and_memory_after_text_change(
    tmp_path,
    runtime_settings,
    monkeypatch,
) -> None:
    runtime = _build_runtime(tmp_path, runtime_settings)
    _write_minimal_chapter_state(runtime.storage, "long_demo", 1)

    calls: dict[str, object] = {
        "exit_state": 0,
        "memory": 0,
        "source": "",
    }
    steps: list[tuple[str, object]] = []

    async def _fake_polish(_self, _payload):
        return PolishResult(
            polished_text="润色后正文（已改写）",
            original_word_count=4,
            polished_word_count=8,
        )

    async def _fake_refresh(_runtime, _layout, _chapter_number, _revised_text):
        calls["exit_state"] = int(calls["exit_state"]) + 1

    async def _fake_reindex(
        _runtime,
        _project_id,
        _chapter_number,
        _revised_text,
        *,
        source="post_repair_reindex",
        on_step_progress=None,
    ):
        calls["memory"] = int(calls["memory"]) + 1
        calls["source"] = source

    async def _fail_eval(_self, _payload):
        raise RuntimeError("skip")

    monkeypatch.setattr("novel_forge.pipeline.steps.polish_step.PolishStep.run", _fake_polish)
    monkeypatch.setattr("novel_forge.pipeline.steps.evaluate_step.EvaluateStep.run", _fail_eval)
    monkeypatch.setattr(
        "novel_forge.pipeline.steps.continuity_eval_step.ContinuityEvalStep.run",
        _fail_eval,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.steps.causal_validation_step.CausalValidationStep.run",
        _fail_eval,
    )
    monkeypatch.setattr(
        "novel_forge.workspace.helpers.execution_helpers._refresh_exit_state_after_repair",
        _fake_refresh,
    )
    monkeypatch.setattr(
        "novel_forge.workspace.helpers.execution_helpers._reindex_memory_after_repair",
        _fake_reindex,
    )

    await execute_polish_chapter(
        runtime,
        PolishChapterRequest(project_id="long_demo", chapter_number=1),
        on_step_progress=lambda step, data: steps.append((step, data)),
    )

    assert calls["exit_state"] == 1
    assert calls["memory"] == 1
    assert calls["source"] == "polish_chapter_reindex"
    assert any(step == "chapter_postprocess" for step, _ in steps)


async def test_execute_polish_chapter_skips_reindex_when_text_unchanged(
    tmp_path,
    runtime_settings,
    monkeypatch,
) -> None:
    runtime = _build_runtime(tmp_path, runtime_settings)
    _write_minimal_chapter_state(runtime.storage, "long_demo", 1)

    calls = {"exit_state": 0, "memory": 0}
    steps: list[tuple[str, object]] = []

    async def _fake_polish(_self, _payload):
        return PolishResult(
            polished_text="原始正文",
            original_word_count=4,
            polished_word_count=4,
        )

    async def _fake_refresh(_runtime, _layout, _chapter_number, _revised_text):
        calls["exit_state"] += 1

    async def _fake_reindex(
        _runtime,
        _project_id,
        _chapter_number,
        _revised_text,
        *,
        source="post_repair_reindex",
        on_step_progress=None,
    ):
        calls["memory"] += 1

    async def _fail_eval(_self, _payload):
        raise RuntimeError("skip")

    monkeypatch.setattr("novel_forge.pipeline.steps.polish_step.PolishStep.run", _fake_polish)
    monkeypatch.setattr("novel_forge.pipeline.steps.evaluate_step.EvaluateStep.run", _fail_eval)
    monkeypatch.setattr(
        "novel_forge.pipeline.steps.continuity_eval_step.ContinuityEvalStep.run",
        _fail_eval,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.steps.causal_validation_step.CausalValidationStep.run",
        _fail_eval,
    )
    monkeypatch.setattr(
        "novel_forge.workspace.helpers.execution_helpers._refresh_exit_state_after_repair",
        _fake_refresh,
    )
    monkeypatch.setattr(
        "novel_forge.workspace.helpers.execution_helpers._reindex_memory_after_repair",
        _fake_reindex,
    )

    await execute_polish_chapter(
        runtime,
        PolishChapterRequest(project_id="long_demo", chapter_number=1),
        on_step_progress=lambda step, data: steps.append((step, data)),
    )

    assert calls["exit_state"] == 0
    assert calls["memory"] == 0
    skipped = [
        data
        for step, data in steps
        if step == "chapter_postprocess"
        and isinstance(data, dict)
        and data.get("status") == "skipped"
    ]
    assert skipped


async def test_resolve_checkpoint_with_force_true(monkeypatch) -> None:
    from novel_forge.workspace.sessions import chapter_sessions
    from novel_forge.workspace.sessions.chapter_session_state import GuardCheckpointSessionState

    captured: dict = {}

    class _FakeStorage:
        def existing_project_dir(self, project_id: str):
            return SimpleNamespace()

    runtime = SimpleNamespace(storage=_FakeStorage())
    request = ResolveChapterCheckpointRequest(
        project_id="long_demo",
        chapter_number=3,
        checkpoint_id="guard-001",
        option_id="write_now",
        force=True,
    )

    async def _fake_prepare_long_project(
        *, storage, project_id, chapter_number, force_regenerate=False
    ):
        captured["force_regenerate"] = force_regenerate
        captured["project_id"] = project_id
        captured["chapter_number"] = chapter_number
        return SimpleNamespace(layout=SimpleNamespace(root=None), canon_state=None)

    async def _fake_resolve_guard(*_args, **_kwargs):
        captured["resolved"] = True
        return SimpleNamespace(project_id="long_demo", status="completed")

    monkeypatch.setattr(chapter_sessions, "scoped_stale_chapters", lambda *_a, **_kw: set())
    monkeypatch.setattr(chapter_sessions, "prepare_long_project", _fake_prepare_long_project)
    monkeypatch.setattr(
        chapter_sessions,
        "load_checkpoint",
        lambda *_a, **_kw: DecisionCheckpoint(
            checkpoint_id="guard-001", checkpoint_type="guard_checkpoint"
        ),
    )
    monkeypatch.setattr(
        chapter_sessions,
        "load_session_state",
        lambda *_a, **_kw: GuardCheckpointSessionState(
            stage="guard_checkpoint",
            checkpoint_id="guard-001",
            project_id="long_demo",
            chapter_number=3,
            canon_watermark=2,
        ),
    )
    monkeypatch.setattr(
        chapter_sessions, "_assert_session_matches_current_chain", lambda *_a, **_kw: None
    )
    monkeypatch.setattr(chapter_sessions, "resolve_guard_checkpoint", _fake_resolve_guard)

    await chapter_sessions.resolve_chapter_session(runtime, request)

    assert captured["force_regenerate"] is True
    assert captured["project_id"] == "long_demo"
    assert captured["chapter_number"] == 3


def test_resolve_checkpoint_force_default_false() -> None:
    request = ResolveChapterCheckpointRequest(
        project_id="long_demo",
        chapter_number=3,
        checkpoint_id="plan-001",
        option_id="write_now",
    )
    assert request.force is False


async def test_resolve_checkpoint_force_propagates_through_guard_redirect(monkeypatch) -> None:
    """When user submits a stale plan_checkpoint id but the background task has
    already advanced to guard_checkpoint, the redirect must propagate
    force=True into the synthesized ResolveChapterCheckpointRequest so the
    redirected guard handler retains the regenerate-allowed semantics.
    """
    from novel_forge.workspace.sessions import chapter_sessions
    from novel_forge.workspace.sessions.chapter_session_state import GuardCheckpointSessionState

    captured: dict = {}

    class _FakeStorage:
        def existing_project_dir(self, project_id: str):
            return SimpleNamespace()

    runtime = SimpleNamespace(storage=_FakeStorage())
    request = ResolveChapterCheckpointRequest(
        project_id="long_demo",
        chapter_number=3,
        checkpoint_id="plan-001",
        option_id="write_now",
        force=True,
    )

    async def _fake_prepare_long_project(
        *, storage, project_id, chapter_number, force_regenerate=False
    ):
        captured["force_regenerate"] = force_regenerate
        return SimpleNamespace(layout=SimpleNamespace(root=None), canon_state=None)

    async def _fake_resolve_guard(_runtime, redirected_request, **_kwargs):
        captured["redirected_force"] = redirected_request.force
        captured["redirected_option_id"] = redirected_request.option_id
        captured["redirected_checkpoint_id"] = redirected_request.checkpoint_id
        return SimpleNamespace(project_id="long_demo", status="completed")

    monkeypatch.setattr(chapter_sessions, "scoped_stale_chapters", lambda *_a, **_kw: set())
    monkeypatch.setattr(chapter_sessions, "prepare_long_project", _fake_prepare_long_project)
    monkeypatch.setattr(
        chapter_sessions,
        "load_checkpoint",
        lambda *_a, **_kw: DecisionCheckpoint(
            checkpoint_id="guard-002", checkpoint_type="guard_checkpoint"
        ),
    )
    monkeypatch.setattr(
        chapter_sessions,
        "load_session_state",
        lambda *_a, **_kw: GuardCheckpointSessionState(
            stage="guard_checkpoint",
            checkpoint_id="guard-002",
            project_id="long_demo",
            chapter_number=3,
            canon_watermark=2,
        ),
    )
    monkeypatch.setattr(
        chapter_sessions, "_assert_session_matches_current_chain", lambda *_a, **_kw: None
    )
    monkeypatch.setattr(chapter_sessions, "resolve_guard_checkpoint", _fake_resolve_guard)

    await chapter_sessions.resolve_chapter_session(runtime, request)

    assert captured["force_regenerate"] is True
    assert captured["redirected_force"] is True, (
        "force=True must survive the plan→guard redirect synthesis"
    )
    assert captured["redirected_option_id"] == "pause_for_human"
    assert captured["redirected_checkpoint_id"] == "guard-002"


async def test_execute_rebuild_memory_vectors_rebuilds_episodic_and_expression() -> None:
    class _FakeMemoryContext:
        async def rebuild_vector_collection(self) -> dict[str, object]:
            return {
                "backend": "zvec",
                "index_type": "hnsw",
                "rebuilt_vectors": 12,
                "saved": True,
            }

        async def rebuild_expression_channel_memory(
            self,
            *,
            from_chapter: int | None = None,
            to_chapter: int | None = None,
        ) -> dict[str, object]:
            return {
                "rebuilt_chapters": 2,
                "observations": 4,
                "vectors": 4,
                "from_chapter": from_chapter,
                "to_chapter": to_chapter,
            }

    class _FakeRuntime:
        storage = object()

        async def get_memory_context(self, project_id: str, storage: object | None = None):
            assert project_id == "long_demo"
            assert storage is self.storage
            return _FakeMemoryContext()

    events: list[tuple[str, dict[str, object]]] = []
    result = await execute_rebuild_memory_vectors(
        _FakeRuntime(),  # type: ignore[arg-type]
        RebuildMemoryVectorsRequest(
            project_id="long_demo",
            from_chapter=1,
            to_chapter=2,
        ),
        on_step_progress=lambda step, payload: events.append((step, payload)),
    )

    assert result.project_id == "long_demo"
    assert result.result["episodic"]["rebuilt_vectors"] == 12
    assert result.result["expression"]["vectors"] == 4
    assert [step for step, _payload in events] == [
        "memory_vector_rebuild_start",
        "memory_vector_rebuild_episodic_start",
        "memory_vector_rebuild_episodic_done",
        "memory_vector_rebuild_expression_start",
        "memory_vector_rebuild_expression_done",
        "memory_vector_rebuild_done",
    ]


async def test_execute_rebuild_memory_vectors_skips_unavailable_expression() -> None:
    class _FakeMemoryContext:
        async def rebuild_vector_collection(self) -> dict[str, object]:
            return {"rebuilt_vectors": 3, "saved": True}

        async def rebuild_expression_channel_memory(self, **_kwargs: object) -> dict[str, object]:
            raise RuntimeError("Expression memory is not enabled for this project")

    class _FakeRuntime:
        storage = object()

        async def get_memory_context(self, _project_id: str, storage: object | None = None):
            assert storage is self.storage
            return _FakeMemoryContext()

    result = await execute_rebuild_memory_vectors(
        _FakeRuntime(),  # type: ignore[arg-type]
        RebuildMemoryVectorsRequest(project_id="long_demo"),
    )

    assert result.result["episodic"]["rebuilt_vectors"] == 3
    assert result.result["expression"]["skipped"] == "unavailable"
    assert result.result["warnings"]
