"""Tests for resumable long-outline batching."""

from __future__ import annotations

import json
from typing import Any

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.bible import CharacterBible
from novel_forge.core.schemas.outline import (
    ChapterEmotionalPlan,
    ChapterOutline,
    NarrativeBlueprint,
    NarrativePhase,
    StoryOutline,
)
from novel_forge.obs.tracer import PipelineTrace
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.artifact_manifest import manifest_for_context
from novel_forge.pipeline.long.services.init.init_outline_batch import (
    _outline_entity_audit,
    outline_reveal_guard_input_hashes,
)
from novel_forge.pipeline.long.services.init.init_outline_helpers import (
    _build_outline_tracker_context,
    _load_outline_conversation_history,
    _load_partial_outline_chapters_from_session,
    _save_outline_batch_checkpoint,
    _save_outline_resume_state,
)
from novel_forge.pipeline.long.services.init.init_outline_recovery import (
    audit_outline_resume_state,
    prepare_outline_regeneration,
    repair_outline_resume_state,
)
from novel_forge.pipeline.long.services.init.init_service import (
    InitLongServiceContext,
    _batched_generate_outline,
    _cached_outline_matches_reveal_guard,
    _record_outline_exchange,
    _repair_outline_reveal_guard_manifest_from_downstream,
    _sanitize_outline_conversation_history,
)
from novel_forge.pipeline.long.services.init.init_v2 import hash_payload


def _chapter(num: int) -> ChapterOutline:
    return ChapterOutline(
        chapter_number=num,
        title=f"潮汐暗线{num}",
        goal=f"陆栖追查第{num}处海港密账，并确认下一处交易线索。",
        beats_summary=[
            f"陆栖在海港旧仓发现第{num}处密账编号。",
            "同伴用码头货单核对出交易时间差。",
            "反派线人试图烧毁对应船运记录。",
            "陆栖保住半页残账并锁定下一处码头。",
        ],
        main_plot_points=[
            f"陆栖确认第{num}处海港密账与旧案资金有关。",
            "船运残账指向下一处交易码头，形成续章追查目标。",
        ],
        subplot_points=["同伴对陆栖隐瞒旧案细节的信任裂缝加深。"],
        pov_character="陆栖",
        setting="海港都市",
        expected_word_count=3200,
        notes="",
    )


def _accepted_batch_record(
    *,
    batch_start: int,
    batch_end: int,
    checkpoint: dict[str, Any],
    session_id: str,
) -> dict[str, Any]:
    return {
        "batch_start": batch_start,
        "batch_end": batch_end,
        "accepted_chapters": list(range(batch_start, batch_end + 1)),
        "checkpoint_file": f"batch_{batch_start:03d}_{batch_end:03d}.json",
        "content_hash": checkpoint["content_hash"],
        "source_hash": checkpoint.get("source_hash", ""),
        "session_id": session_id,
        "entity_audit": {"ok": True, "issue_count": 0},
    }


class _StubRegistry:
    def render(self, task_type: TaskType, **ctx: Any) -> str:
        return f"{task_type.value}:{ctx.get('batch_start')}-{ctx.get('batch_end')}"


class _StubBuilder:
    def __init__(self) -> None:
        self._registry = _StubRegistry()


class _OutlineRouter:
    def output_limit_for_task(self, _task_type: TaskType, **_kwargs: Any) -> int:
        return 65536


class _OutlineRunner:
    def __init__(self, storage, responses: list[Any], *, multi_turn: bool = True) -> None:
        self._storage = storage
        self._builder = _StubBuilder()
        self._responses = list(responses)
        self._multi_turn = multi_turn
        self.calls: list[dict[str, Any]] = []
        self.steps: list[tuple[str, Any]] = []

    def _on_step(self, step: str, data: Any) -> None:
        self.steps.append((step, data))

    def _is_outline_option_enabled_for_task(
        self,
        *,
        capability: str,
        enabled: bool,
        allowed_providers_raw: str,
        allowed_models_raw: str,
        task_type: TaskType,
    ) -> bool:
        if capability == "multi_turn" and task_type == TaskType.PLAN_OUTLINE_CONTINUE:
            return self._multi_turn
        return False

    async def _call_with_retry(
        self,
        task_type: TaskType,
        ctx: dict[str, Any],
        *,
        max_tokens: int,
        temperature: float,
        required_keys: tuple[str, ...],
        prior_messages: list[dict[str, str]] | None = None,
        thinking: bool = False,
        multi_turn: bool = False,
        _capture_raw: list[str] | None = None,
        max_retries: int = 2,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "task_type": task_type,
                "ctx": ctx,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "required_keys": required_keys,
                "prior_messages": prior_messages,
                "thinking": thinking,
                "multi_turn": multi_turn,
            }
        )
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if _capture_raw is not None and not _capture_raw:
            _capture_raw.append(response["raw"])
        return {"chapters": response["chapters"]}


class _FailingOutlineMemory:
    async def get_outline_context(self, **_kwargs: Any) -> Any:
        raise RuntimeError("zvec vector store could not be initialized")


class _FallbackOutlineTracker:
    def get_context_for_prompt(self, current_chapter: int) -> dict[str, str]:
        return {
            "relationship_summary": f"第{current_chapter}章关系摘要",
            "themes_summary": "主题仍围绕旧案信任裂缝推进。",
            "key_events_summary": "陆栖保住残账并锁定下一处码头。",
            "unresolved_summary": "下一处交易码头尚未确认。",
        }


def _settings(*, batch_size: int = 5, multi_turn: bool = True) -> Settings:
    return Settings(
        _env_file=None,
        outline_batch_size=batch_size,
        outline_multi_turn=multi_turn,
        memory_episodic_enabled=False,
    )


def _build_ctx(
    runner: _OutlineRunner,
    layout: ProjectLayout,
    settings: Settings,
) -> InitLongServiceContext:
    """Build an InitLongServiceContext from a stub runner."""
    return InitLongServiceContext(
        storage=runner._storage,
        router=_OutlineRouter(),  # type: ignore[arg-type]
        builder=runner._builder,
        settings=settings,
        config=None,  # type: ignore[arg-type]
        layout=layout,
        trace=PipelineTrace(),
        on_step=runner._on_step,
        call_with_retry=runner._call_with_retry,
        coerce_character_bible=None,
        is_outline_option_enabled=runner._is_outline_option_enabled_for_task,
    )


def _blueprint() -> NarrativeBlueprint:
    return NarrativeBlueprint(
        synopsis="旧物中的秘密把现实一点点撕开。",
        volume_mode=False,
        volumes=[],
        narrative_phases=[],
        key_turning_points=[],
        character_arcs=[],
        subplot_plan=[],
        ending_strategy="让真相在代价中落地。",
    )


def _identity_chapter(number: int, *, subject: str = "陆栖") -> ChapterOutline:
    return _chapter(number).model_copy(
        update={
            "emotional_plan": ChapterEmotionalPlan(
                subject_entity_id=subject,
                pressure_source="账本遭人焚毁",
                relationship_choice="向同伴交出证据",
                exit_aftertaste="终于选择信任同伴",
            ),
        }
    )


def _identity_catalog() -> list[dict[str, Any]]:
    return [
        {"entity_id": "char_lu", "name": "陆栖", "entity_type": "character"},
        {"entity_id": "loc_port", "name": "海港", "entity_type": "location"},
    ]


@pytest.mark.parametrize("multi_turn", [False, True])
async def test_progressive_batches_and_repairs_always_receive_entity_catalog(
    tmp_storage,
    multi_turn: bool,
) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("identity_context"))
    layout.ensure_dirs()
    batches = [list(range(1, 4)), list(range(4, 7)), list(range(7, 10)), [10], [10]]
    responses = []
    for i, numbers in enumerate(batches):
        chapters = [
            _identity_chapter(n, subject="loc_port" if i == 3 else "陆栖").model_dump(mode="json")
            for n in numbers
        ]
        responses.append({"chapters": chapters, "raw": json.dumps({"chapters": chapters})})
    runner = _OutlineRunner(tmp_storage, responses, multi_turn=multi_turn)
    outline = await _batched_generate_outline(
        _build_ctx(runner, layout, _settings(batch_size=3, multi_turn=multi_turn)),
        existing_outline=None,
        outline_ctx={"words_per_chapter": 3200},
        blueprint=_blueprint(),
        total_chapters=60,
        words_per_chapter=3200,
        use_volume_mode=False,
        effective_chapters_per_volume=10,
        character_bible=CharacterBible.model_validate({"characters": [{"name": "陆栖"}]}),
        entity_registry={"entities": _identity_catalog()},
        target_end_chapter=10,
        design_through_chapter=5,
    )
    assert len(runner.calls) == 5
    for call in runner.calls:
        assert {item["entity_id"] for item in call["ctx"]["outline_entity_catalog"]} == {
            "char_lu",
            "loc_port",
        }
    assert runner.calls[1]["ctx"]["chapter_design_matrix"]["chapters"][-1]["chapter_number"] == 5
    assert runner.calls[2]["ctx"]["chapter_design_matrix"] is None
    assert runner.calls[-1]["ctx"]["outline_quality_feedback"]
    assert outline.hard_through_chapter == 5
    assert outline.planned_through_chapter == 10
    checkpoint = tmp_storage.load_json(layout.states_dir / "outline_batches/batch_007_009.json")
    assert checkpoint["source_hashes"]["outline_identity_contract"]
    for chapter in checkpoint["chapters"]:
        assert chapter["pov_character_id"] == ""
        assert chapter["cast_plan"]["required_character_ids"] == []
        assert chapter["emotional_plan"]["subject_entity_id"] == "char_lu"


@pytest.mark.parametrize("invalid_subject", [False, True])
async def test_resume_migrates_identity_or_restarts_first_invalid_batch(
    tmp_storage,
    invalid_subject: bool,
) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("identity_resume"))
    layout.ensure_dirs()
    session_id = "identity-old-session"
    records = []
    for start, end in [(1, 5), (6, 9)]:
        chapters = [
            _identity_chapter(n, subject="unknown_actor" if n == 7 and invalid_subject else "陆栖")
            for n in range(start, end + 1)
        ]
        checkpoint = _save_outline_batch_checkpoint(
            tmp_storage,
            layout,
            total_chapters=60,
            batch_start=start,
            batch_end=end,
            chapters=chapters,
            missing_chapters=[],
            status="accepted",
            session_id=session_id,
        )
        records.append(
            _accepted_batch_record(
                batch_start=start, batch_end=end, checkpoint=checkpoint, session_id=session_id
            )
        )
    tmp_storage.save_json(
        layout.outline_session_path,
        {
            "total_chapters": 60,
            "last_safe_chapter": 9,
            "session_id": session_id,
            "accepted_batches": records,
            "conversation_history": [{"role": "assistant", "content": "stale identity references"}],
        },
    )
    runner = _OutlineRunner(tmp_storage, [RuntimeError("stop after resume migration")])
    with pytest.raises(RuntimeError, match="stop after resume migration"):
        await _batched_generate_outline(
            _build_ctx(runner, layout, _settings()),
            existing_outline=None,
            outline_ctx={"words_per_chapter": 3200},
            blueprint=_blueprint(),
            total_chapters=60,
            words_per_chapter=3200,
            use_volume_mode=False,
            effective_chapters_per_volume=10,
            character_bible={"characters": [{"name": "陆栖"}]},
            entity_registry={"entities": _identity_catalog()},
            target_end_chapter=10,
            design_through_chapter=5,
        )
    assert runner.calls[0]["ctx"]["batch_start"] == (6 if invalid_subject else 10)
    recovered = _load_partial_outline_chapters_from_session(tmp_storage, layout, total_chapters=60)
    assert len(recovered) == (5 if invalid_subject else 9)
    assert _outline_entity_audit(recovered, {}, entity_catalog=_identity_catalog())["ok"]
    session = tmp_storage.load_json(layout.outline_session_path)
    assert session["conversation_history"] == []
    assert list((layout.states_dir / "outline_identity_backups").glob("session_*.json"))
    assert all(ch.emotional_plan.subject_entity_id == "char_lu" for ch in recovered)
    assert session["accepted_batches"][0]["content_hash"] != records[0]["content_hash"]
    backups = sorted((layout.states_dir / "outline_identity_backups").glob("*.json"))
    second_runner = _OutlineRunner(tmp_storage, [RuntimeError("stop on second resume")])
    with pytest.raises(RuntimeError, match="stop on second resume"):
        await _batched_generate_outline(
            _build_ctx(second_runner, layout, _settings()),
            existing_outline=None, outline_ctx={"words_per_chapter": 3200},
            blueprint=_blueprint(), total_chapters=60, words_per_chapter=3200,
            use_volume_mode=False, effective_chapters_per_volume=10,
            character_bible={"characters": [{"name": "陆栖"}]},
            entity_registry={"entities": _identity_catalog()},
            target_end_chapter=10, design_through_chapter=5,
        )
    assert backups == sorted((layout.states_dir / "outline_identity_backups").glob("*.json"))
    assert not any(step == "plan_outline_resume_identity_migrated" for step, _ in second_runner.steps)


def _blueprint_with_phase() -> NarrativeBlueprint:
    return NarrativeBlueprint(
        synopsis="旧物中的秘密把现实一点点撕开。",
        volume_mode=False,
        volumes=[],
        narrative_phases=[
            NarrativePhase(
                phase_name="中段加压",
                chapter_start=9,
                chapter_end=15,
                description="调查升级并让阻力外显。",
                key_events=["外部阻力升级"],
                tension_level="渐升",
            )
        ],
        key_turning_points=[],
        character_arcs=[],
        subplot_plan=[],
        ending_strategy="让真相在代价中落地。",
    )


@pytest.mark.asyncio
async def test_outline_context_falls_back_to_tracker_when_episodic_memory_fails() -> None:
    context = await _build_outline_tracker_context(
        tracker=_FallbackOutlineTracker(),  # type: ignore[arg-type]
        episodic_memory=_FailingOutlineMemory(),  # type: ignore[arg-type]
        current_chapter=8,
        existing_chapters=[_chapter(6), _chapter(7)],
    )

    assert context["relationship_summary"] == "第8章关系摘要"
    assert context["key_events_summary"] == "陆栖保住残账并锁定下一处码头。"
    assert "第7章" in context["recent_chapters_summary"]
    assert "旧案信任裂缝" in context["unified_memory_context"]


def test_complete_outline_cache_requires_reveal_guard_manifest(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("outline_reveal_guard_cache"))
    layout.ensure_dirs()
    runner = _OutlineRunner(tmp_storage, responses=[])
    ctx = _build_ctx(runner, layout, _settings())
    outline = StoryOutline(
        total_chapters=2,
        synopsis="旧物中的秘密把现实一点点撕开。",
        volume_mode=False,
        volumes=[],
        chapters=[_chapter(1), _chapter(2)],
    )
    editorial_contract = {
        "revelation_ladder": [
            {
                "thread": "小书童身份线",
                "stage": "无人见过疑云确认",
                "target_chapter": 15,
                "allowed_disclosure": "第15章前只能保持怀疑",
            }
        ]
    }

    assert not _cached_outline_matches_reveal_guard(
        ctx,
        editorial_contract=editorial_contract,
        outline=outline,
    )

    manifest_for_context(ctx).record_success(
        artifact="outline",
        workflow="init_long",
        step="plan_outline",
        input_hashes=outline_reveal_guard_input_hashes(editorial_contract),
        output_hashes={"outline": hash_payload(outline)},
        paths={"outline": str(layout.outline_path)},
    )

    assert _cached_outline_matches_reveal_guard(
        ctx,
        editorial_contract=editorial_contract,
        outline=outline,
    )


def test_complete_outline_reveal_guard_manifest_repaired_from_downstream_contracts(
    tmp_storage,
) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("outline_reveal_guard_downstream"))
    layout.ensure_dirs()
    runner = _OutlineRunner(tmp_storage, responses=[])
    ctx = _build_ctx(runner, layout, _settings())
    outline = StoryOutline(
        total_chapters=2,
        synopsis="旧物中的秘密把现实一点点撕开。",
        volume_mode=False,
        volumes=[],
        chapters=[_chapter(1), _chapter(2)],
    )
    editorial_contract = {
        "revelation_ladder": [
            {
                "thread": "小书童身份线",
                "stage": "无人见过疑云确认",
                "target_chapter": 15,
                "allowed_disclosure": "第15章前只能保持怀疑",
            }
        ]
    }
    tmp_storage.save_json(
        layout.plans_dir / "chapter_contracts.json",
        {
            "chapter_contracts": [
                {
                    "chapter_number": number,
                    "title": f"契约{number}",
                    "entry_state_requirements": [],
                    "required_events": [f"第{number}章推进旧案线索。"],
                    "allowed_changes": [],
                    "forbidden_changes": [],
                    "promise_ops": [],
                    "relationship_ops": [],
                    "item_ops": [],
                    "knowledge_ops": [],
                    "cognitive_constraints": [],
                    "new_character_candidates": [],
                    "exit_state_targets": [],
                    "required_progressions": [f"第{number}章锁定新证据。"],
                    "allowed_progressions": [],
                    "forbidden_progressions": [],
                    "completion_criteria": [f"第{number}章证据落盘。"],
                    "future_leak_risks": [],
                    "source": "unit",
                }
                for number in (1, 2)
            ]
        },
    )

    assert not _cached_outline_matches_reveal_guard(
        ctx,
        editorial_contract=editorial_contract,
        outline=outline,
    )

    repaired = _repair_outline_reveal_guard_manifest_from_downstream(
        ctx,
        editorial_contract=editorial_contract,
        outline=outline,
    )

    assert repaired is True
    assert _cached_outline_matches_reveal_guard(
        ctx,
        editorial_contract=editorial_contract,
        outline=outline,
    )


@pytest.mark.asyncio
async def test_batched_outline_rejects_partial_resume_without_reveal_guard_session(
    tmp_storage,
) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("outline_reveal_guard_partial"))
    layout.ensure_dirs()
    existing_outline = StoryOutline(
        total_chapters=4,
        synopsis="旧物中的秘密把现实一点点撕开。",
        volume_mode=False,
        volumes=[],
        chapters=[_chapter(1), _chapter(2)],
    )
    editorial_contract = {
        "revelation_ladder": [
            {
                "thread": "小书童身份线",
                "stage": "无人见过疑云确认",
                "target_chapter": 15,
                "allowed_disclosure": "第15章前只能保持怀疑",
            }
        ]
    }
    runner = _OutlineRunner(
        tmp_storage,
        responses=[
            {
                "chapters": [_chapter(num).model_dump(mode="json") for num in range(1, 3)],
                "raw": '{"chapters": [1,2]}',
            },
            {
                "chapters": [_chapter(num).model_dump(mode="json") for num in range(3, 5)],
                "raw": '{"chapters": [3,4]}',
            },
        ],
        multi_turn=False,
    )
    ctx = _build_ctx(runner, layout, _settings(batch_size=2, multi_turn=False))

    outline = await _batched_generate_outline(
        ctx,
        existing_outline=existing_outline,
        outline_ctx={"words_per_chapter": 3200},
        blueprint=_blueprint(),
        total_chapters=4,
        words_per_chapter=3200,
        use_volume_mode=False,
        effective_chapters_per_volume=10,
        editorial_contract=editorial_contract,
    )

    assert [chapter.chapter_number for chapter in outline.chapters] == [1, 2, 3, 4]
    assert runner.calls[0]["ctx"]["batch_start"] == 1
    assert any(step == "plan_outline_resume_rejected" for step, _ in runner.steps)


@pytest.mark.parametrize("target_end", [10, 12])
async def test_published_progressive_outline_survives_cleaned_generation_session(tmp_storage, target_end):
    layout = ProjectLayout(tmp_storage.project_dir("published_progressive"))
    layout.ensure_dirs()
    existing = StoryOutline(
        total_chapters=80, hard_through_chapter=5, planned_through_chapter=10,
        chapters=[_identity_chapter(n) for n in range(1, 11)],
    )
    original = [chapter.model_dump(mode="json") for chapter in existing.chapters[:5]]
    responses = [] if target_end == 10 else [{
        "chapters": [_identity_chapter(n).model_dump(mode="json") for n in range(11, 13)],
        "raw": '{"chapters":[11,12]}',
    }]
    runner = _OutlineRunner(tmp_storage, responses, multi_turn=False)
    result = await _batched_generate_outline(
        _build_ctx(runner, layout, _settings(batch_size=2, multi_turn=False)),
        existing_outline=existing,
        outline_ctx={"words_per_chapter": 3200}, blueprint=_blueprint(),
        total_chapters=80, words_per_chapter=3200, use_volume_mode=False,
        effective_chapters_per_volume=10,
        character_bible=CharacterBible.model_validate({"characters": [{"name": "陆栖"}]}),
        entity_registry={"entities": _identity_catalog()},
        editorial_contract={"revelation_ladder": [{"thread": "身份", "target_chapter": 15}]},
        target_start_chapter=6, target_end_chapter=target_end,
        design_through_chapter=target_end, preserve_committed_chapters=True,
    )
    assert result.total_chapters == 80
    assert result.hard_through_chapter == target_end
    assert [ch.chapter_number for ch in result.chapters] == list(range(1, target_end + 1))
    assert [ch.model_dump(mode="json") for ch in result.chapters[:5]] == original
    assert all(call["ctx"]["batch_start"] >= 11 for call in runner.calls)
    assert not any(step == "plan_outline_resume_rejected" for step, _ in runner.steps)
    assert not layout.outline_session_path.exists()


@pytest.mark.asyncio
async def test_batched_outline_resume_restores_multi_turn_history(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("resume_outline"))
    layout.ensure_dirs()
    existing_outline = StoryOutline(
        total_chapters=36,
        synopsis="旧物中的秘密把现实一点点撕开。",
        volume_mode=False,
        volumes=[],
        chapters=[_chapter(num) for num in range(1, 31)],
    )
    tmp_storage.save_json(layout.outline_path, existing_outline.model_dump(mode="json"))
    saved_history = [
        {"role": "user", "content": "plan_outline_batch:26-30"},
        {"role": "assistant", "content": '{"chapters": [26,27,28,29,30]}'},
    ]
    tmp_storage.save_json(
        layout.outline_session_path,
        {
            "total_chapters": 36,
            "chapters_done": 30,
            "latest_chapter_number": 30,
            "conversation_history": saved_history,
        },
    )
    runner = _OutlineRunner(
        tmp_storage,
        responses=[
            {
                "chapters": [_chapter(num).model_dump(mode="json") for num in range(31, 35)],
                "raw": '{"chapters": [31,32,33,34]}',
            },
            {
                "chapters": [_chapter(num).model_dump(mode="json") for num in range(35, 37)],
                "raw": '{"chapters": [35,36]}',
            },
        ],
        multi_turn=True,
    )

    settings = _settings(batch_size=5, multi_turn=True)
    ctx = _build_ctx(runner, layout, settings)

    outline = await _batched_generate_outline(
        ctx,
        existing_outline=existing_outline,
        outline_ctx={"words_per_chapter": 3200},
        blueprint=_blueprint(),
        total_chapters=36,
        words_per_chapter=3200,
        use_volume_mode=False,
        effective_chapters_per_volume=10,
    )

    assert outline.total_chapters == 36
    assert max(ch.chapter_number for ch in outline.chapters) == 36
    assert runner.calls[0]["task_type"] == TaskType.PLAN_OUTLINE_CONTINUE
    assert runner.calls[0]["prior_messages"] == saved_history
    assert not layout.outline_session_path.exists()


@pytest.mark.asyncio
async def test_batched_outline_resume_falls_back_to_batch_mode_when_history_missing(
    tmp_storage,
) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("resume_outline_without_history"))
    layout.ensure_dirs()
    existing_outline = StoryOutline(
        total_chapters=36,
        synopsis="旧物中的秘密把现实一点点撕开。",
        volume_mode=False,
        volumes=[],
        chapters=[_chapter(num) for num in range(1, 31)],
    )
    tmp_storage.save_json(layout.outline_path, existing_outline.model_dump(mode="json"))

    runner = _OutlineRunner(
        tmp_storage,
        responses=[
            {
                "chapters": [_chapter(num).model_dump(mode="json") for num in range(31, 35)],
                "raw": '{"chapters": [31,32,33,34]}',
            },
            {
                "chapters": [_chapter(num).model_dump(mode="json") for num in range(35, 37)],
                "raw": '{"chapters": [35,36]}',
            },
        ],
        multi_turn=True,
    )

    settings = _settings(batch_size=5, multi_turn=True)
    ctx = _build_ctx(runner, layout, settings)

    outline = await _batched_generate_outline(
        ctx,
        existing_outline=existing_outline,
        outline_ctx={"words_per_chapter": 3200},
        blueprint=_blueprint(),
        total_chapters=36,
        words_per_chapter=3200,
        use_volume_mode=False,
        effective_chapters_per_volume=10,
    )

    assert outline.total_chapters == 36
    assert max(ch.chapter_number for ch in outline.chapters) == 36
    assert runner.calls[0]["task_type"] == TaskType.PLAN_OUTLINE_BATCH
    assert runner.calls[1]["task_type"] == TaskType.PLAN_OUTLINE_CONTINUE
    assert runner.calls[1]["prior_messages"]
    assert not layout.outline_session_path.exists()


@pytest.mark.asyncio
async def test_batched_outline_injects_density_settings(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("outline_density_settings"))
    layout.ensure_dirs()
    runner = _OutlineRunner(
        tmp_storage,
        responses=[
            {
                "chapters": [_chapter(num).model_dump(mode="json") for num in range(1, 3)],
                "raw": '{"chapters": [1,2]}',
            },
        ],
        multi_turn=False,
    )

    settings = Settings(
        _env_file=None,
        outline_batch_size=2,
        outline_multi_turn=False,
        memory_episodic_enabled=False,
        init_outline_beats_min=5,
        init_outline_beats_max=9,
        init_outline_main_plot_points_min=2,
        init_outline_main_plot_points_max=5,
        init_outline_subplot_points_max=2,
        init_outline_element_focus_max=2,
        init_outline_expected_payoffs_min=1,
        init_outline_expected_payoffs_max=2,
    )
    ctx = _build_ctx(runner, layout, settings)

    await _batched_generate_outline(
        ctx,
        existing_outline=None,
        outline_ctx={"words_per_chapter": 3200},
        blueprint=_blueprint(),
        total_chapters=2,
        words_per_chapter=3200,
        use_volume_mode=False,
        effective_chapters_per_volume=10,
    )

    assert runner.calls[0]["ctx"]["outline_density"] == {
        "beats_min": 5,
        "beats_max": 9,
        "main_plot_points_min": 2,
        "main_plot_points_max": 5,
        "subplot_points_max": 2,
        "element_focus_max": 2,
        "expected_payoffs_min": 1,
        "expected_payoffs_max": 2,
    }


@pytest.mark.asyncio
async def test_batched_outline_continue_injects_phase_rhythm_guidance(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("resume_outline_rhythm"))
    layout.ensure_dirs()
    existing_outline = StoryOutline(
        total_chapters=20,
        synopsis="旧物中的秘密把现实一点点撕开。",
        volume_mode=False,
        volumes=[],
        chapters=[_chapter(num) for num in range(1, 11)],
    )
    tmp_storage.save_json(layout.outline_path, existing_outline.model_dump(mode="json"))
    tmp_storage.save_json(
        layout.outline_session_path,
        {
            "total_chapters": 20,
            "chapters_done": 10,
            "latest_chapter_number": 10,
            "conversation_history": [
                {"role": "user", "content": "plan_outline_batch:6-10"},
                {"role": "assistant", "content": '{"chapters": [6,7,8,9,10]}'},
            ],
        },
    )
    runner = _OutlineRunner(
        tmp_storage,
        responses=[
            {
                "chapters": [_chapter(num).model_dump(mode="json") for num in range(11, 15)],
                "raw": '{"chapters": [11,12,13,14]}',
            },
            {
                "chapters": [_chapter(num).model_dump(mode="json") for num in range(15, 19)],
                "raw": '{"chapters": [15,16,17,18]}',
            },
            {
                "chapters": [_chapter(num).model_dump(mode="json") for num in range(19, 21)],
                "raw": '{"chapters": [19,20]}',
            },
        ],
        multi_turn=True,
    )

    settings = _settings(batch_size=5, multi_turn=True)
    ctx = _build_ctx(runner, layout, settings)

    outline = await _batched_generate_outline(
        ctx,
        existing_outline=existing_outline,
        outline_ctx={"words_per_chapter": 3200},
        blueprint=_blueprint_with_phase(),
        total_chapters=20,
        words_per_chapter=3200,
        use_volume_mode=False,
        effective_chapters_per_volume=10,
    )

    assert outline.total_chapters == 20
    assert runner.calls[0]["task_type"] == TaskType.PLAN_OUTLINE_CONTINUE
    assert runner.calls[0]["ctx"]["phase_rhythm_guidance"]
    assert "每2-3章至少安排一次阻力升级" in runner.calls[0]["ctx"]["phase_rhythm_guidance"]


@pytest.mark.asyncio
async def test_batched_outline_resume_persists_completed_batches_before_later_failure(
    tmp_storage,
) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("resume_outline_failure"))
    layout.ensure_dirs()
    session_id = "resume-failure-session"
    checkpoint = _save_outline_batch_checkpoint(
        tmp_storage,
        layout,
        total_chapters=40,
        batch_start=1,
        batch_end=30,
        chapters=[_chapter(num) for num in range(1, 31)],
        missing_chapters=[],
        status="accepted",
        session_id=session_id,
    )
    tmp_storage.save_json(
        layout.outline_session_path,
        {
            "schema_version": "1.1",
            "status": "recoverable",
            "session_id": session_id,
            "total_chapters": 40,
            "chapters_done": 30,
            "latest_chapter_number": 30,
            "last_safe_chapter": 30,
            "conversation_history": [],
            "accepted_batches": [
                _accepted_batch_record(
                    batch_start=1,
                    batch_end=30,
                    checkpoint=checkpoint,
                    session_id=session_id,
                )
            ],
        },
    )
    runner = _OutlineRunner(
        tmp_storage,
        responses=[
            {
                "chapters": [_chapter(num).model_dump(mode="json") for num in range(31, 35)],
                "raw": '{"chapters": [31,32,33,34]}',
            },
            json.JSONDecodeError("bad json", "{", 1),
        ],
        multi_turn=True,
    )

    settings = _settings(batch_size=5, multi_turn=True)
    ctx = _build_ctx(runner, layout, settings)

    with pytest.raises(json.JSONDecodeError):
        await _batched_generate_outline(
            ctx,
            existing_outline=None,
            outline_ctx={"words_per_chapter": 3200},
            blueprint=_blueprint(),
            total_chapters=40,
            words_per_chapter=3200,
            use_volume_mode=False,
            effective_chapters_per_volume=10,
        )

    assert not layout.outline_path.exists()

    session_payload = tmp_storage.load_json(layout.outline_session_path)
    assert session_payload["chapters_done"] == 34
    assert session_payload["latest_chapter_number"] == 34
    assert session_payload["conversation_history"][-1]["role"] == "assistant"
    assert [record["batch_start"] for record in session_payload["accepted_batches"]] == [1, 31]
    checkpoint = tmp_storage.load_json(layout.states_dir / "outline_batches/batch_031_034.json")
    assert checkpoint["status"] == "accepted"
    pending_events = [
        payload
        for _step, payload in runner.steps
        if payload.get("current_batch_status") == "pending"
        and payload.get("safe_saved_chapter") == 34
    ]
    assert pending_events
    assert pending_events[-1]["current_batch_status"] == "pending"
    assert pending_events[-1]["safe_saved_chapter"] == 34
    assert pending_events[-1]["chapters_done"] == 34
    assert "当前批次未验收，不计入断点恢复点" in pending_events[-1]["message"]


@pytest.mark.asyncio
async def test_batched_outline_stops_when_batch_accepts_no_chapters(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("outline_empty_batch_guard"))
    layout.ensure_dirs()
    bad_chapter = _chapter(99).model_dump(mode="json")
    runner = _OutlineRunner(
        tmp_storage,
        responses=[
            {"chapters": [bad_chapter], "raw": '{"chapters": [99]}'},
            {"chapters": [bad_chapter], "raw": '{"chapters": [99]}'},
            {"chapters": [bad_chapter], "raw": '{"chapters": [99]}'},
        ],
        multi_turn=True,
    )

    settings = _settings(batch_size=5, multi_turn=True)
    ctx = _build_ctx(runner, layout, settings)

    with pytest.raises(ValueError, match="章节大纲批次未完整落盘"):
        await _batched_generate_outline(
            ctx,
            existing_outline=None,
            outline_ctx={"words_per_chapter": 3200},
            blueprint=_blueprint(),
            total_chapters=4,
            words_per_chapter=3200,
            use_volume_mode=False,
            effective_chapters_per_volume=10,
        )

    session_payload = tmp_storage.load_json(layout.outline_session_path)
    assert session_payload["chapters_done"] == 0
    checkpoint = tmp_storage.load_json(layout.states_dir / "outline_batches/batch_001_004.json")
    assert checkpoint["status"] == "incomplete"
    assert checkpoint["accepted_chapters"] == []
    assert checkpoint["missing_chapters"] == [1, 2, 3, 4]
    assert any(step == "plan_outline_batch_incomplete" for step, _ in runner.steps)


@pytest.mark.asyncio
async def test_batched_outline_does_not_recover_checkpoint_without_session(
    tmp_storage,
) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("outline_batch_checkpoint_recovery"))
    layout.ensure_dirs()
    _save_outline_batch_checkpoint(
        tmp_storage,
        layout,
        total_chapters=4,
        batch_start=1,
        batch_end=2,
        chapters=[_chapter(1), _chapter(2)],
        missing_chapters=[],
        status="accepted",
    )
    runner = _OutlineRunner(
        tmp_storage,
        responses=[
            {
                "chapters": [_chapter(num).model_dump(mode="json") for num in range(1, 3)],
                "raw": '{"chapters": [1,2]}',
            },
            {
                "chapters": [_chapter(num).model_dump(mode="json") for num in range(3, 5)],
                "raw": '{"chapters": [3,4]}',
            },
        ],
        multi_turn=True,
    )

    settings = _settings(batch_size=2, multi_turn=True)
    ctx = _build_ctx(runner, layout, settings)

    outline = await _batched_generate_outline(
        ctx,
        existing_outline=None,
        outline_ctx={"words_per_chapter": 3200},
        blueprint=_blueprint(),
        total_chapters=4,
        words_per_chapter=3200,
        use_volume_mode=False,
        effective_chapters_per_volume=10,
    )

    assert [chapter.chapter_number for chapter in outline.chapters] == [1, 2, 3, 4]
    assert runner.calls[0]["ctx"]["batch_start"] == 1
    assert runner.calls[0]["ctx"]["batch_end"] == 2
    assert not layout.outline_session_path.exists()


@pytest.mark.asyncio
async def test_batched_outline_recovers_only_from_session_ledger(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("outline_session_ledger_recovery"))
    layout.ensure_dirs()
    session_id = "session-ledger"
    checkpoint = _save_outline_batch_checkpoint(
        tmp_storage,
        layout,
        total_chapters=4,
        batch_start=1,
        batch_end=2,
        chapters=[_chapter(1), _chapter(2)],
        missing_chapters=[],
        status="accepted",
        session_id=session_id,
    )
    tmp_storage.save_json(
        layout.outline_session_path,
        {
            "schema_version": "1.1",
            "status": "recoverable",
            "session_id": session_id,
            "total_chapters": 4,
            "chapters_done": 2,
            "latest_chapter_number": 2,
            "last_safe_chapter": 2,
            "conversation_history": [],
            "accepted_batches": [
                _accepted_batch_record(
                    batch_start=1,
                    batch_end=2,
                    checkpoint=checkpoint,
                    session_id=session_id,
                )
            ],
        },
    )
    runner = _OutlineRunner(
        tmp_storage,
        responses=[
            {
                "chapters": [_chapter(num).model_dump(mode="json") for num in range(3, 5)],
                "raw": '{"chapters": [3,4]}',
            },
        ],
        multi_turn=True,
    )

    settings = _settings(batch_size=2, multi_turn=True)
    ctx = _build_ctx(runner, layout, settings)

    outline = await _batched_generate_outline(
        ctx,
        existing_outline=None,
        outline_ctx={"words_per_chapter": 3200},
        blueprint=_blueprint(),
        total_chapters=4,
        words_per_chapter=3200,
        use_volume_mode=False,
        effective_chapters_per_volume=10,
    )

    assert [chapter.chapter_number for chapter in outline.chapters] == [1, 2, 3, 4]
    assert runner.calls[0]["ctx"]["batch_start"] == 3
    assert runner.calls[0]["ctx"]["batch_end"] == 4
    assert not layout.outline_session_path.exists()


@pytest.mark.asyncio
async def test_batched_outline_finalizes_complete_session_ledger(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("outline_session_complete"))
    layout.ensure_dirs()
    session_id = "session-complete"
    checkpoint = _save_outline_batch_checkpoint(
        tmp_storage,
        layout,
        total_chapters=4,
        batch_start=1,
        batch_end=4,
        chapters=[_chapter(num) for num in range(1, 5)],
        missing_chapters=[],
        status="accepted",
        session_id=session_id,
    )
    tmp_storage.save_json(
        layout.outline_session_path,
        {
            "schema_version": "1.1",
            "status": "recoverable",
            "session_id": session_id,
            "total_chapters": 4,
            "chapters_done": 4,
            "latest_chapter_number": 4,
            "last_safe_chapter": 4,
            "conversation_history": [],
            "accepted_batches": [
                _accepted_batch_record(
                    batch_start=1,
                    batch_end=4,
                    checkpoint=checkpoint,
                    session_id=session_id,
                )
            ],
        },
    )
    runner = _OutlineRunner(tmp_storage, responses=[], multi_turn=True)
    ctx = _build_ctx(runner, layout, _settings(batch_size=2, multi_turn=True))

    outline = await _batched_generate_outline(
        ctx,
        existing_outline=None,
        outline_ctx={"words_per_chapter": 3200},
        blueprint=_blueprint(),
        total_chapters=4,
        words_per_chapter=3200,
        use_volume_mode=False,
        effective_chapters_per_volume=10,
    )

    assert [chapter.chapter_number for chapter in outline.chapters] == [1, 2, 3, 4]
    assert runner.calls == []
    assert layout.outline_path.exists()
    assert not layout.outline_session_path.exists()


def test_outline_checkpoint_loader_ignores_pending_batches(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("outline_pending_checkpoint"))
    layout.ensure_dirs()
    _save_outline_batch_checkpoint(
        tmp_storage,
        layout,
        total_chapters=4,
        batch_start=1,
        batch_end=2,
        chapters=[_chapter(1), _chapter(2)],
        missing_chapters=[],
        status="pending",
        raw_response='{"chapters": [1, 2]}',
    )

    assert (
        _load_partial_outline_chapters_from_session(
            tmp_storage,
            layout,
            total_chapters=4,
        )
        == []
    )


def test_outline_resume_repair_quarantines_partial_canonical_outline(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("outline_recovery_apply"))
    layout.ensure_dirs()
    session_id = "repair-session"
    partial_outline = StoryOutline(
        total_chapters=4,
        synopsis="旧物中的秘密把现实一点点撕开。",
        volume_mode=False,
        volumes=[],
        chapters=[_chapter(1), _chapter(2)],
    )
    tmp_storage.save_json(layout.outline_path, partial_outline.model_dump(mode="json"))
    tmp_storage.save_json(
        layout.plans_dir / "chapter_contracts.json",
        {"chapter_contracts": [{"chapter_number": 1}]},
    )
    checkpoint = _save_outline_batch_checkpoint(
        tmp_storage,
        layout,
        total_chapters=4,
        batch_start=1,
        batch_end=2,
        chapters=[_chapter(1), _chapter(2)],
        missing_chapters=[],
        status="accepted",
        session_id=session_id,
    )
    tmp_storage.save_json(
        layout.outline_session_path,
        {
            "schema_version": "1.1",
            "status": "recoverable",
            "session_id": session_id,
            "total_chapters": 4,
            "chapters_done": 2,
            "latest_chapter_number": 2,
            "last_safe_chapter": 2,
            "accepted_batches": [
                _accepted_batch_record(
                    batch_start=1,
                    batch_end=2,
                    checkpoint=checkpoint,
                    session_id=session_id,
                )
            ],
        },
    )

    dry_run = audit_outline_resume_state(tmp_storage, layout, total_chapters=4)
    assert dry_run["needs_repair"] is True
    assert dry_run["canonical_outline"]["partial"] is True

    result = repair_outline_resume_state(tmp_storage, layout, apply=True, total_chapters=4)

    assert result["applied"] is True
    assert not layout.outline_path.exists()
    assert not (layout.plans_dir / "chapter_contracts.json").exists()
    assert result["moved_artifacts"]
    session_payload = tmp_storage.load_json(layout.outline_session_path)
    assert session_payload["status"] == "recoverable"
    assert session_payload["chapters_done"] == 2
    assert session_payload["last_safe_chapter"] == 2
    assert (layout.reports_dir / "outline_resume_repair.json").exists()


def test_outline_resume_repair_quarantines_orphan_accepted_checkpoints(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("outline_recovery_orphan_checkpoint"))
    layout.ensure_dirs()
    session_id = "current-session"
    checkpoint = _save_outline_batch_checkpoint(
        tmp_storage,
        layout,
        total_chapters=4,
        batch_start=1,
        batch_end=2,
        chapters=[_chapter(1), _chapter(2)],
        missing_chapters=[],
        status="accepted",
        session_id=session_id,
    )
    _save_outline_batch_checkpoint(
        tmp_storage,
        layout,
        total_chapters=4,
        batch_start=3,
        batch_end=4,
        chapters=[_chapter(3), _chapter(4)],
        missing_chapters=[],
        status="accepted",
        session_id="old-session",
    )
    tmp_storage.save_json(
        layout.outline_session_path,
        {
            "schema_version": "1.1",
            "status": "recoverable",
            "session_id": session_id,
            "total_chapters": 4,
            "chapters_done": 2,
            "latest_chapter_number": 2,
            "last_safe_chapter": 2,
            "accepted_batches": [
                _accepted_batch_record(
                    batch_start=1,
                    batch_end=2,
                    checkpoint=checkpoint,
                    session_id=session_id,
                )
            ],
        },
    )

    audit = audit_outline_resume_state(tmp_storage, layout, total_chapters=4)

    assert audit["checkpoints"]["accepted_chapters_done"] == 2
    assert len(audit["checkpoints"]["orphan"]) == 1
    assert audit["needs_persistence_repair"] is True

    result = repair_outline_resume_state(tmp_storage, layout, apply=True, total_chapters=4)

    assert result["applied"] is True
    assert not (layout.states_dir / "outline_batches" / "batch_003_004.json").exists()
    session_payload = tmp_storage.load_json(layout.outline_session_path)
    assert session_payload["chapters_done"] == 2
    assert len(session_payload["accepted_batches"]) == 1


def test_outline_resume_repair_apply_is_noop_when_persistence_is_clean(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("outline_recovery_noop"))
    layout.ensure_dirs()
    outline = StoryOutline(
        total_chapters=2,
        synopsis="旧物中的秘密把现实一点点撕开。",
        volume_mode=False,
        volumes=[],
        chapters=[_chapter(1), _chapter(2)],
    )
    tmp_storage.save_json(layout.outline_path, outline.model_dump(mode="json"))

    result = repair_outline_resume_state(tmp_storage, layout, apply=True, total_chapters=2)

    assert result["applied"] is False
    assert result["skipped_reason"] == "no_persistence_repair_needed"
    assert layout.outline_path.exists()
    assert not layout.outline_session_path.exists()


def test_prepare_outline_regeneration_quarantines_outline_downstream_only(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("outline_regeneration"))
    layout.ensure_dirs()
    outline = StoryOutline(
        total_chapters=2,
        synopsis="旧物中的秘密把现实一点点撕开。",
        volume_mode=False,
        volumes=[],
        chapters=[_chapter(1), _chapter(2)],
    )
    tmp_storage.save_json(layout.blueprint_path, _blueprint().model_dump(mode="json"))
    tmp_storage.save_json(layout.outline_path, outline.model_dump(mode="json"))
    tmp_storage.save_json(layout.outline_session_path, {"total_chapters": 2})
    _save_outline_batch_checkpoint(
        tmp_storage,
        layout,
        total_chapters=2,
        batch_start=1,
        batch_end=2,
        chapters=[_chapter(1), _chapter(2)],
        missing_chapters=[],
        status="accepted",
    )
    tmp_storage.save_json(layout.plans_dir / "chapter_contracts.json", {"chapter_contracts": []})

    dry_run = prepare_outline_regeneration(tmp_storage, layout, apply=False)
    assert dry_run["applied"] is False
    assert dry_run["artifact_count"] >= 4
    assert layout.outline_path.exists()

    result = prepare_outline_regeneration(tmp_storage, layout, apply=True)

    assert result["applied"] is True
    assert not layout.outline_path.exists()
    assert not layout.outline_session_path.exists()
    assert not (layout.states_dir / "outline_batches").exists()
    assert not (layout.plans_dir / "chapter_contracts.json").exists()
    assert layout.blueprint_path.exists()
    assert result["moved_artifacts"]


@pytest.mark.asyncio
async def test_batched_outline_repair_continue_ctx_includes_blueprint(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("outline_repair_ctx"))
    layout.ensure_dirs()

    runner = _OutlineRunner(
        tmp_storage,
        responses=[
            {
                "chapters": [_chapter(num).model_dump(mode="json") for num in range(1, 4)],
                "raw": '{"chapters": [1,2,3]}',
            },
            {
                "chapters": [_chapter(4).model_dump(mode="json")],
                "raw": '{"chapters": [4]}',
            },
        ],
        multi_turn=False,
    )

    settings = _settings(batch_size=4, multi_turn=False)
    ctx = _build_ctx(runner, layout, settings)
    blueprint = _blueprint()

    outline = await _batched_generate_outline(
        ctx,
        existing_outline=None,
        outline_ctx={
            "words_per_chapter": 3200,
            "spec": {"genre": "romance", "theme": "x", "tone": "dark"},
            "story_bible": {"era": "架空时代"},
            "character_bible": {"characters": []},
            "style_profile": {"summary": "冷峻权谋"},
        },
        blueprint=blueprint,
        total_chapters=4,
        words_per_chapter=3200,
        use_volume_mode=False,
        effective_chapters_per_volume=10,
    )

    assert outline.total_chapters == 4
    assert [ch.chapter_number for ch in outline.chapters] == [1, 2, 3, 4]
    assert len(runner.calls) == 2
    assert runner.calls[0]["task_type"] == TaskType.PLAN_OUTLINE_BATCH
    assert runner.calls[1]["task_type"] == TaskType.PLAN_OUTLINE_CONTINUE
    assert runner.calls[1]["ctx"]["blueprint"].synopsis == blueprint.synopsis
    assert "blueprint_element_selection" in runner.calls[1]["ctx"]


def test_outline_exchange_keeps_latest_assistant_full_and_summarizes_older() -> None:
    history: list[dict[str, str]] = []
    raw_first = json.dumps(
        {
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "海雾",
                    "goal": "建立冲突入口并抛出疑问",
                    "main_plot_points": ["主角在旧港发现匿名线索"],
                }
            ]
        },
        ensure_ascii=False,
    )
    raw_second = json.dumps(
        {
            "chapters": [
                {
                    "chapter_number": 6,
                    "title": "逆潮",
                    "goal": "推进对抗并锁定关键证据",
                    "main_plot_points": ["主角在仓库与对手首次正面碰撞"],
                }
            ]
        },
        ensure_ascii=False,
    )

    _record_outline_exchange(
        None,
        history,
        TaskType.PLAN_OUTLINE_BATCH,
        {"batch_start": 1, "batch_end": 5},
        raw_first,
        is_first_batch=True,
    )
    _record_outline_exchange(
        None,
        history,
        TaskType.PLAN_OUTLINE_CONTINUE,
        {"batch_start": 6, "batch_end": 10},
        raw_second,
        is_first_batch=False,
    )

    assistant_msgs = [item["content"] for item in history if item["role"] == "assistant"]
    assert len(assistant_msgs) == 2
    assert assistant_msgs[-1] == raw_second
    assert assistant_msgs[0].startswith("[章节输出摘要]")


def test_sanitize_history_keeps_only_latest_assistant_full() -> None:
    full_first = json.dumps(
        {"chapters": [{"chapter_number": 11, "title": "雾桥", "goal": "推进调查"}]},
        ensure_ascii=False,
    )
    full_second = json.dumps(
        {"chapters": [{"chapter_number": 12, "title": "夜灯", "goal": "兑现转折"}]},
        ensure_ascii=False,
    )

    sanitized = _sanitize_outline_conversation_history(
        [
            {"role": "user", "content": "这是第一轮完整用户提示，长度很长 " * 50},
            {"role": "assistant", "content": full_first},
            {"role": "user", "content": "这是第二轮完整用户提示，长度很长 " * 50},
            {"role": "assistant", "content": full_second},
        ]
    )

    assistant_msgs = [item["content"] for item in sanitized if item["role"] == "assistant"]
    user_msgs = [item["content"] for item in sanitized if item["role"] == "user"]
    assert len(assistant_msgs) == 2
    assert assistant_msgs[-1] == full_second
    assert assistant_msgs[0].startswith("[章节输出摘要]")
    assert all(len(msg) <= 321 for msg in user_msgs)


def test_outline_resume_ignores_history_without_accepted_chapters(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("outline_empty_history"))
    layout.ensure_dirs()
    history = [
        {"role": "user", "content": "plan_outline_batch:1-6"},
        {"role": "assistant", "content": '{"chapters": [{"chapter_number": 1}]}'},
    ]

    _save_outline_resume_state(
        tmp_storage,
        layout,
        total_chapters=70,
        chapter_map={},
        conversation_history=history,
        history_window_rounds=2,
    )

    payload = tmp_storage.load_json(layout.outline_session_path)
    assert payload["chapters_done"] == 0
    assert payload["conversation_history"] == []
    assert (
        _load_outline_conversation_history(
            tmp_storage,
            layout,
            total_chapters=70,
            history_window_rounds=2,
        )
        == []
    )
    assert (
        _load_partial_outline_chapters_from_session(
            tmp_storage,
            layout,
            total_chapters=70,
        )
        == []
    )


@pytest.mark.asyncio
async def test_outline_resume_does_not_recover_chapters_from_session_history(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("outline_session_recovery"))
    layout.ensure_dirs()
    recovered_raw = json.dumps(
        {"chapters": [_chapter(num).model_dump(mode="json") for num in range(1, 3)]},
        ensure_ascii=False,
    )
    recovered_raw = f"```json\n{recovered_raw}\n```"
    tmp_storage.save_json(
        layout.outline_session_path,
        {
            "total_chapters": 4,
            "chapters_done": 0,
            "latest_chapter_number": 0,
            "conversation_history": [
                {"role": "user", "content": "plan_outline_batch:1-2"},
                {"role": "assistant", "content": recovered_raw},
            ],
        },
    )

    assert (
        _load_partial_outline_chapters_from_session(
            tmp_storage,
            layout,
            total_chapters=4,
        )
        == []
    )
