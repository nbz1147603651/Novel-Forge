"""Unit tests for the KB ∥ StyleProfile ∥ EntityGraph three-task parallel phase.

Covers the ``init_kb_phase_c_parallel`` switch introduced in the init
efficiency plan (S2):

- parallel mode: knowledge-boundary LLM call overlaps StyleProfile and
  EntityGraph instead of blocking them serially;
- serial mode: the switch restores the original order (KB first, then
  Style ∥ Entity with the post-KB character bible);
- KB failure degrades to a warning without blocking Phase C or the rest of
  the initialization pipeline.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.core.schemas import (
    CharacterBible,
    CharacterProfile,
    StoryBible,
)
from novel_forge.core.schemas.blueprint_elements import BlueprintElementSelection
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.core.schemas.style_profile import ProjectStyleProfile
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.init import init_orchestrator as init_impl


class _TraceCtxMgr:
    def __enter__(self) -> "_TraceCtxMgr":
        return self

    def __exit__(self, *_args: object) -> bool:
        return False


class _LaxSettings(SimpleNamespace):
    """Settings namespace that defaults any unknown attribute to None."""

    def __getattr__(self, name: str) -> Any:
        return None


class _LaxCtx(SimpleNamespace):
    """Context namespace that defaults any unknown attribute to None."""

    def __getattr__(self, name: str) -> Any:
        return None


def _build_context(tmp_path: Any) -> tuple[Any, list[tuple[str, object]]]:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("kb_phase_c"))
    layout.ensure_dirs()
    events: list[tuple[str, object]] = []
    ctx = _LaxCtx(
        storage=storage,
        layout=layout,
        config=SimpleNamespace(
            volume_auto_chapter_threshold=9999,
            volume_auto_word_threshold=999999,
            default_chapters_per_volume=10,
        ),
        settings=_LaxSettings(
            style_profile_enabled=True,
            style_profile_required=False,
            init_stream_claim_prefetch_enabled=False,
            narrative_state_enabled=False,
            research_dossier_enabled=True,
            outline_thinking=False,
            outline_multi_turn=True,
            chapter_contract_multi_turn=True,
        ),
        router=None,
        builder=None,
        on_step=lambda step, data: events.append((step, data)),
        trace=SimpleNamespace(step=lambda _name: _TraceCtxMgr(), summary=lambda: {}),
        is_outline_option_enabled=lambda **_kwargs: False,
    )
    return ctx, events


def _mock_full_init_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    ctx: Any,
    *,
    kb_failure: bool = False,
    kb_delay_s: float = 0.15,
    record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wire every stage of init_long_project behind the Phase C gate.

    Returns a shared record namespace used to assert task overlap/order.
    """
    record = record or {
        "kb_events": [],
        "style_events": [],
        "entity_events": [],
        "kb_in_flight": False,
    }
    globals_dict = init_impl.__dict__

    async def fake_call_with_retry(task_type: Any, payload: dict[str, object], **_kwargs: object) -> dict[str, object]:
        from novel_forge.core.constants import TaskType

        if task_type == TaskType.INIT_KNOWLEDGE_BOUNDARIES:
            record["kb_events"].append(("start", time.monotonic()))
            record["kb_in_flight"] = True
            if kb_failure:
                raise RuntimeError("kb boom")
            await asyncio.sleep(kb_delay_s)
            record["kb_in_flight"] = False
            record["kb_events"].append(("end", time.monotonic()))
            return {"characters": []}
        return payload

    async def fake_spec_run(*_args: object, **_kwargs: object) -> StorySpec:
        return StorySpec(
            theme="测试故事",
            genre="xuanhuan",
            tone="serious",
            title="测试",
            language="zh",
            length_target=6000,
        )

    class _Step:
        """Duck-typed stand-in for pipeline steps instantiated with args."""

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class _SpecStep(_Step):
        async def run(self, *_args: object, **_kwargs: object) -> StorySpec:
            return await fake_spec_run()

    class _BlueprintSelectStep(_Step):
        async def run(self, *_args: object, **_kwargs: object) -> BlueprintElementSelection:
            return BlueprintElementSelection()

    class _StyleStep(_Step):
        async def run(self, *_args: object, **_kwargs: object) -> ProjectStyleProfile:
            record["style_events"].append(("start", time.monotonic(), record["kb_in_flight"]))
            record["style_sibling_tasks"] = _count_sibling_tasks()
            await asyncio.sleep(0.02)
            record["style_events"].append(("end", time.monotonic()))
            return _style_instance

    class _EditorialStep(_Step):
        async def run(self, *_args: object, **_kwargs: object) -> Any:
            return SimpleNamespace(
                title="测试",
                total_chapters=2,
                character_voices=[],
                climax_markers=[],
                symbol_policies=[],
                theme_policies=[],
                scene_resistance_rules=[],
                expression_channel_profiles=[],
                revelation_ladder=[],
                editorial_element_directives=[],
                model_dump=lambda mode="json": {},
            )

    async def fake_story_bible(*_args: object, **_kwargs: object) -> StoryBible:
        return StoryBible(premise="测试故事", title="测试")

    async def fake_character_bible(*_args: object, **_kwargs: object) -> CharacterBible:
        return CharacterBible(characters=[CharacterProfile(name="沈珩")])

    async def fake_blueprint_select(*_args: object, **_kwargs: object) -> BlueprintElementSelection:
        return BlueprintElementSelection()

    async def fake_style_step(*_args: object, **_kwargs: object) -> ProjectStyleProfile:
        record["style_events"].append(("start", time.monotonic(), record["kb_in_flight"]))
        await asyncio.sleep(0.02)
        record["style_events"].append(("end", time.monotonic()))
        return ProjectStyleProfile()

    async def fake_blueprint(*_args: object, **_kwargs: object) -> Any:
        from novel_forge.core.schemas.outline import NarrativeBlueprint

        return NarrativeBlueprint(
            title="测试蓝图",
            synopsis="测试故事",
            total_chapters=2,
            volumes=[],
            key_turning_points=[],
            narrative_phases=[],
            subplot_plan=[],
            suspense_schedule=[],
        )

    async def fake_refine_profile(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "schema_version": "v1",
            "genre_tags": [],
            "project_ontology": {"state_axes": [], "payoff_types": []},
            "summary": "ok",
        }

    async def fake_editorial_run(*_args: object, **_kwargs: object) -> Any:
        from novel_forge.core.schemas.editorial_contract import EditorialContract

        return EditorialContract(
            title="测试",
            total_chapters=2,
            character_voices=[],
            climax_markers=[],
            symbol_policies=[],
        )
    async def fake_outline(*_args: object, **_kwargs: object) -> Any:
        from novel_forge.core.schemas.outline import ChapterOutline, StoryOutline

        return StoryOutline(
            total_chapters=2,
            synopsis="测试故事",
            chapters=[
                ChapterOutline(
                    chapter_number=number,
                    title=f"第{number}章",
                    goal="推进主线",
                    beats_summary=["完成本章事件。"],
                    main_plot_points=["主线推进。"],
                )
                for number in (1, 2)
            ],
        )

    async def fake_grounding(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(
            status="skipped",
            chapter_notes=[],
            warnings=[],
            prompt_context=lambda **_kwargs: {},
            model_dump=lambda mode="json": {},
        )

    async def fake_contracts(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"llm_contract": {}, "chapter_contracts": {"chapter_contracts": []}}

    async def fake_persist(*_args: object, **kwargs: object) -> dict[str, object]:
        record["persisted"] = {
            "style_profile": kwargs.get("style_profile"),
            "character_bible": kwargs.get("character_bible"),
            "entity_graph": kwargs.get("entity_graph"),
            "outline": kwargs.get("outline"),
            "chapter_contracts": kwargs.get("chapter_contracts"),
        }
        return {"chapter_contracts": []}

    async def fake_finish(*_args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(project_id=kwargs["project_id"])

    def fake_research_report() -> SimpleNamespace:
        return SimpleNamespace(
            status="failed",
            enabled=False,
            provider="noop",
            warnings=[],
            sources=[],
            model_prior=SimpleNamespace(status="skipped"),
            prompt_context=lambda: {},
            model_dump=lambda mode="json": {},
        )

    async def fake_web_research(*_args: object, **_kwargs: object) -> Any:
        return fake_research_report()

    async def fake_dossier(*_args: object, **_kwargs: object) -> Any:
        dossier = fake_research_report()
        dossier.model_dump = lambda mode="json": {}
        return dossier

    async def fake_noop_async(*_args: object, **_kwargs: object) -> None:
        return None

    # Deterministic singletons so the two A/B branches produce identical
    # payload objects for the persisted-artifact equivalence assertions.
    _style_instance = ProjectStyleProfile()
    _rp_config_instance = SimpleNamespace()

    async def fake_refine_blueprint(*_args: object, **_kwargs: object) -> Any:
        return _kwargs["blueprint"]

    def fake_noop(*_args: object, **_kwargs: object) -> None:
        return None

    def fake_health(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"checks": {}}

    # ctx-level mocks
    ctx.call_with_retry = fake_call_with_retry

    # module-level mocks (must hit init_orchestrator's globals, which is what
    # init_long_project resolves at call time)
    monkeypatch.setattr(init_impl, "build_init_context", lambda *_args: ctx)
    setitem = lambda name, value: monkeypatch.setitem(globals_dict, name, value)  # noqa: E731
    setattr_g = lambda name, value: monkeypatch.setattr(init_impl, name, value)  # noqa: E731

    setattr_g("_ensure_long_init_request_fresh", fake_noop)
    setattr_g("_detect_init_resume_anchor", fake_noop)
    setattr_g("_load_source_artifacts_resume_bundle", fake_noop)
    setitem("SpecStep", _SpecStep)
    setitem("run_init_web_research", fake_web_research)
    setitem("synthesize_init_research_dossier", fake_dossier)
    setitem("build_story_bible_split", fake_story_bible)
    setitem("BlueprintElementSelectStep", _BlueprintSelectStep)
    setitem("build_character_bible_split", fake_character_bible)
    setitem("_load_cached_character_bible_for_mode", lambda *_a, **_k: None)
    setitem(
        "_load_or_generate_character_relationship_matrix",
        lambda *_a, **_k: asyncio.sleep(0) or [],
    )
    setitem("InitV2BlockCache", lambda _root: SimpleNamespace(
        load_success=lambda *_a, **_k: None,
        save_success=lambda *_a, **_k: None,
        path_for=lambda *_a: ctx.layout.root / "cache.json",
    ))
    setitem("ProfileStyleStep", _StyleStep)
    setitem("NarrativeStateStore", lambda _root: SimpleNamespace(
        save_entity_registry=fake_noop,
        root=ctx.layout.root,
    ))
    setitem("_entity_registry_needs_llm_supplement", lambda *_a, **_k: False)

    def _count_sibling_tasks() -> int:
        """Number of other not-yet-finished tasks in the current loop."""
        current = asyncio.current_task()
        return len(
            [task for task in asyncio.all_tasks() if task is not current and not task.done()]
        )

    def fake_build_entity_graph(*_args: object, **_kwargs: object) -> Any:
        record["entity_events"].append(("start", time.monotonic(), record["kb_in_flight"]))
        record["entity_sibling_tasks"] = _count_sibling_tasks()
        from novel_forge.core.schemas.init_v2 import EntityGraph

        return (
            SimpleNamespace(entities=[]),
            EntityGraph(entities=[], entity_links=[]),
        )

    setitem("build_entity_graph", fake_build_entity_graph)
    setitem(
        "build_reading_power_window_config_from_settings",
        lambda _settings: _rp_config_instance,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.steps.profile_style_step.ProfileStyleStep",
        _StyleStep,
    )
    setitem("manifest_for_context", lambda _ctx: SimpleNamespace(record_success=fake_noop))
    setitem("generate_or_resume_blueprint", fake_blueprint)
    setitem("validate_blueprint", lambda *_a, **_k: SimpleNamespace(errors=[], warnings=[]))
    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.blueprint.blueprint_validation.validate_blueprint",
        lambda *_a, **_k: SimpleNamespace(errors=[], warnings=[]),
    )
    setitem("_load_reusable_init_coherence_profile", lambda *_a, **_k: None)
    setitem("refine_init_coherence_profile", fake_refine_profile)
    setitem("_with_init_entity_catalog", lambda profile, **_k: profile)
    setitem("_maybe_refine_blueprint_creatively", fake_refine_blueprint)
    setitem("_load_reusable_init_coherence_report", lambda *_a, **_k: {
        "blocked": False,
        "verdict": "accept",
        "issues": [],
        "summary": "ok",
    })
    setitem("_request_init_copilot_gate", fake_noop_async)
    setitem("_load_reusable_editorial_contract", lambda *_a, **_k: None)
    setitem("EditorialContractStep", _EditorialStep)
    setitem("validate_editorial_contract", lambda *_a, **_k: SimpleNamespace(
        findings=[],
        summary="ok",
        metrics={},
        model_dump=lambda mode="json": {},
    ))
    setitem("_persist_blueprint_sidecars", fake_noop)
    setitem("_batched_generate_outline", fake_outline)
    setitem("_load_reusable_outline_research_grounding", lambda *_a, **_k: None)
    setitem("ground_outline_research", fake_grounding)
    setitem("initialize_narrative_state_and_contracts", fake_contracts)
    setitem("_save_init_readiness", lambda *_a, **_k: {"allowed": True})
    setitem("_persist_source_artifacts_from_init", fake_persist)
    setitem("_finish_init_after_source_artifacts", fake_finish)
    setitem("sync_narrative_evidence", fake_noop_async)
    setitem("validate_plan_outline_context", fake_noop)
    setitem("build_init_entity_catalog", lambda *_a, **_k: {})
    setitem("calculate_route_aware_max_tokens", lambda *_a, **_k: 4096)
    setitem("record_story_bible_health", fake_health)
    setitem("record_character_system_health", fake_health)
    setitem("record_character_bible_health", fake_health)
    setitem("record_entity_graph_health", fake_health)
    setitem("record_creative_packet_health", fake_health)
    setitem("assert_upstream_health_allows_progress", fake_noop)
    return record


@pytest.mark.asyncio
async def test_kb_phase_c_parallel_overlaps_style_and_entity(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, events = _build_context(tmp_path)
    record = _mock_full_init_pipeline(monkeypatch, ctx)

    result = await init_impl.init_long_project(
        SimpleNamespace(),
        "测试故事",
        project_id="kb_parallel",
        total_chapters=2,
    )

    assert result.project_id == "kb_parallel"
    # Style and Entity both ran, and while each ran, the other two Phase C
    # tasks were already scheduled and not finished: the three tasks were
    # gathered concurrently instead of awaiting each other serially.
    assert record["style_events"], "StyleProfile should have run"
    assert record["entity_events"], "EntityGraph build should have run"
    assert record.get("style_sibling_tasks", 0) >= 2
    assert record.get("entity_sibling_tasks", 0) >= 2
    assert len(record["kb_events"]) == 2, "KB LLM call should have run"


@pytest.mark.asyncio
async def test_kb_phase_c_serial_mode_keeps_kb_before_style_and_entity(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, _events = _build_context(tmp_path)
    ctx.settings.init_kb_phase_c_parallel = False
    record = _mock_full_init_pipeline(monkeypatch, ctx)

    result = await init_impl.init_long_project(
        SimpleNamespace(),
        "测试故事",
        project_id="kb_serial",
        total_chapters=2,
    )

    assert result.project_id == "kb_serial"
    kb_end = [e for e in record["kb_events"] if e[0] == "end"][0]
    style_start = [e for e in record["style_events"] if e[0] == "start"][0]
    entity_start = [e for e in record["entity_events"] if e[0] == "start"][0]
    # Serial mode: KB fully finished before Style/Entity even started, and no
    # task overlapped the KB call.
    assert kb_end[1] < style_start[1]
    assert kb_end[1] < entity_start[1]
    assert style_start[2] is False
    assert entity_start[2] is False


@pytest.mark.asyncio
async def test_kb_phase_c_ab_equivalence_of_persisted_artifacts(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock-level A/B: the switch must not change downstream artifact payloads.

    Both branches run the same mock pipeline on identical inputs; the
    artifacts handed to _persist_source_artifacts_from_init (style_profile,
    character_bible, entity_graph, outline, chapter_contracts) must be
    equivalent, proving the parallel refactor introduces no payload drift.
    """
    ctx_parallel, _ = _build_context(tmp_path)
    record_parallel = _mock_full_init_pipeline(monkeypatch, ctx_parallel)
    await init_impl.init_long_project(
        SimpleNamespace(),
        "测试故事",
        project_id="ab_parallel",
        total_chapters=2,
    )

    ctx_serial, _ = _build_context(tmp_path)
    ctx_serial.settings.init_kb_phase_c_parallel = False
    record_serial = _mock_full_init_pipeline(
        monkeypatch,
        ctx_serial,
        record={
            "kb_events": [],
            "style_events": [],
            "entity_events": [],
            "kb_in_flight": False,
        },
    )
    await init_impl.init_long_project(
        SimpleNamespace(),
        "测试故事",
        project_id="ab_serial",
        total_chapters=2,
    )

    persisted_parallel = record_parallel["persisted"]
    persisted_serial = record_serial["persisted"]

    def _stable(payload: Any) -> Any:
        """Recursively strip auto-generated metadata fields.

        reading_power_window_config is derived from settings at runtime, not
        from Phase C inputs, so it is excluded from the artifact comparison.
        """
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump(mode="json")

        def _strip(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    key: _strip(item)
                    for key, item in value.items()
                    if key
                    not in ("created_at", "schema_version", "reading_power_window_config")
                }
            if isinstance(value, list):
                return [_strip(item) for item in value]
            return value

        return _strip(payload)

    for key in (
        "style_profile",
        "character_bible",
        "entity_graph",
        "outline",
        "chapter_contracts",
    ):
        assert _stable(persisted_parallel[key]) == _stable(persisted_serial[key]), key


@pytest.mark.asyncio
async def test_kb_phase_c_failure_degrades_to_warning_without_blocking(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, events = _build_context(tmp_path)
    record = _mock_full_init_pipeline(monkeypatch, ctx, kb_failure=True)

    result = await init_impl.init_long_project(
        SimpleNamespace(),
        "测试故事",
        project_id="kb_failure",
        total_chapters=2,
    )

    assert result.project_id == "kb_failure"
    failed_steps = [step for step, _payload in events if step == "init_knowledge_boundaries_failed"]
    assert failed_steps, "KB failure must surface as a warning event"
    # StyleProfile and EntityGraph still ran, and the pipeline completed.
    assert record["style_events"]
    assert record["entity_events"]
