from __future__ import annotations

import asyncio
import json
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from novel_forge.common.constants import TaskType
from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.core.schemas.bible import CharacterBible, CharacterProfile, StoryBible
from novel_forge.core.schemas.outline import ChapterCastPlan, ChapterOutline, StoryOutline
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.gateway.base import ProviderAdapter
from novel_forge.gateway.router import ModelRouter, TaskRouteOverride
from novel_forge.gateway.types import ModelRequest, ModelResponse, StreamChunk
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.generation.llm_service import LLMService
from novel_forge.pipeline.long.services.init import init_contract_flow as init_contract_flow_module
from novel_forge.pipeline.long.services.init.init_chapter_contracts import (
    _normalize_unregistered_sequential_item_ids,
    _synchronize_chapter_contract_cast_plans,
)
from novel_forge.pipeline.long.services.init.init_coherence import InitCoherenceError
from novel_forge.pipeline.long.services.init.init_coherence_v2 import CLAIM_LEDGER_JSON
from novel_forge.pipeline.long.services.init.init_service import (
    _assert_chapter_contracts_ready_to_persist,
    _batched_adjudicate_contract_coherence,
    _batched_generate_chapter_contracts,
    _chapter_contract_batch_max_tokens,
    _chapter_contract_batch_outline_payload,
    _chapter_contract_input_hashes,
    _ensure_chapter_contract_coverage,
    _llm_narrative_contract_input_hashes,
    _load_reusable_chapter_contracts,
    _load_reusable_llm_narrative_contract,
    _persist_chapter_contract_runtime_artifacts,
    _persist_llm_narrative_contract,
    _source_artifact_resume_rebuild_chapters,
    initialize_narrative_state_and_contracts,
    merge_reusable_and_rebuilt_chapter_contracts,
    partition_chapter_contracts_for_resume,
)
from novel_forge.pipeline.long.services.init_repair import (
    InitArtifact,
    InitRepairContext,
    InitRepairOrchestrator,
    get_init_repair_policy,
)
from novel_forge.pipeline.long.services.init_repair.policies.chapter_contracts import (
    _outline_backfill_chapter_contract,
)


def _outline() -> StoryOutline:
    return StoryOutline(
        total_chapters=2,
        chapters=[
            ChapterOutline(
                chapter_number=1,
                title="开端",
                goal="建立时间裂缝谜题",
                beats_summary=["主角醒来", "发现怀表"],
                main_plot_points=["时间裂缝出现"],
                expected_word_count=800,
            ),
            ChapterOutline(
                chapter_number=2,
                title="追踪",
                goal="追查钟楼线索",
                beats_summary=["进入钟楼"],
                main_plot_points=["线索指向旧图书馆"],
                expected_word_count=800,
            ),
        ],
    )


def test_synchronize_contract_cast_plan_uses_authoritative_outline_identity() -> None:
    outline = _outline()
    chapter = outline.chapters[0].model_copy(
        update={
            "pov_character_id": "char_current",
            "involved_character_ids": ["char_current"],
            "required_character_ids": ["char_current"],
            "support_character_ids": [],
            "cast_plan": ChapterCastPlan(
                pov_entity_id="char_current",
                required_character_ids=["char_current"],
                support_character_ids=[],
                mention_only_entity_ids=["loc_current"],
                forbidden_active_character_ids=[],
            ),
        }
    )
    outline = outline.model_copy(update={"chapters": [chapter, outline.chapters[1]]})
    contracts = {
        "chapter_contracts": [
            {
                "chapter_number": 1,
                "pov_character_id": "char_stale",
                "involved_character_ids": ["char_stale", "loc_stale"],
                "required_character_ids": ["char_stale"],
                "support_character_ids": [],
                "cast_plan": {
                    "pov_entity_id": "char_stale",
                    "required_character_ids": ["char_stale"],
                    "mention_only_entity_ids": ["loc_stale"],
                },
            }
        ]
    }

    changed = _synchronize_chapter_contract_cast_plans(contracts, outline)

    assert changed == [1]
    contract = contracts["chapter_contracts"][0]
    assert contract["pov_character_id"] == "char_current"
    assert contract["cast_plan"] == chapter.cast_plan.model_dump(mode="json")
    assert "loc_stale" not in str(contract)


def test_normalize_unregistered_item_ids_preserves_operation_without_false_identity() -> None:
    contracts = {
        "chapter_contracts": [
            {
                "chapter_number": 9,
                "item_ops": [
                    {
                        "entity_id": "item_19",
                        "operation": "transfer",
                        "description": "老周将与密钥有关的记忆包裹交给沈岸。",
                    },
                    {
                        "entity_id": "item_20",
                        "operation": "conceal",
                        "description": "老周藏起婚书残片。",
                    },
                ],
            }
        ]
    }
    catalog = {
        "allowed_entities": [
            {
                "entity_id": "item_07",
                "canonical_name": "婚书残片",
                "entity_type": "item",
                "aliases": [],
            }
        ]
    }

    changes = _normalize_unregistered_sequential_item_ids(
        contracts,
        entity_catalog=catalog,
    )

    operations = contracts["chapter_contracts"][0]["item_ops"]
    assert operations[0]["entity_id"] == ""
    assert operations[0]["description"].endswith("交给沈岸。")
    assert operations[1]["entity_id"] == "item_07"
    assert {change["action"] for change in changes} == {
        "clear_false_identity",
        "map_exact_description",
    }


def test_chapter_contract_input_hashes_include_outline_research_grounding() -> None:
    outline = _outline()
    narrative_contract = {"global": ["保留旧城档案主线"]}
    base = _chapter_contract_input_hashes(
        outline=outline,
        narrative_contract=narrative_contract,
        outline_research_grounding={"summary": "档案馆流程提醒"},
    )
    changed = _chapter_contract_input_hashes(
        outline=outline,
        narrative_contract=narrative_contract,
        outline_research_grounding={"summary": "旧城改造流程提醒"},
    )

    assert "outline_research_grounding" in base
    assert base["outline_research_grounding"] != changed["outline_research_grounding"]


def _outline_with_chapters(count: int) -> StoryOutline:
    return StoryOutline(
        total_chapters=count,
        synopsis="时间裂缝正在扩大。",
        chapters=[
            ChapterOutline(
                chapter_number=index,
                title=f"第{index}章",
                goal=f"完成第{index}章目标",
                beats_summary=[f"第{index}章节拍"],
                main_plot_points=[f"第{index}章主线事件"],
                expected_word_count=800,
            )
            for index in range(1, count + 1)
        ],
    )


class _Trace:
    def step(self, _name: str) -> Any:
        return nullcontext()


class _OutputLimitRouter:
    def __init__(self, output_limit: int) -> None:
        self.output_limit = output_limit

    def output_limit_for_task(self, _task_type: Any, **_kwargs: Any) -> int:
        return self.output_limit


class _ChapterContractCtx:
    def __init__(self) -> None:
        self.settings = type(
            "_Settings",
            (),
            {
                "chapter_contract_context_window": 2,
                "chapter_contract_multi_turn": True,
                "chapter_contract_multi_turn_providers": "tongyi,deepseek,minimax",
                "chapter_contract_multi_turn_models": "",
                "chapter_contract_dynamic_budget_enabled": True,
                "chapter_contract_hard_words_per_item": 1000,
                "chapter_contract_hard_min_items": 3,
                "chapter_contract_hard_max_items": 6,
                "chapter_contract_soft_words_per_item": 900,
                "chapter_contract_soft_max_items": 5,
                "chapter_contract_state_words_per_item": 1200,
                "chapter_contract_state_max_items": 4,
                "contract_coherence_batch_size": 2,
                "contract_coherence_context_window": 1,
            },
        )()
        self.router = object()
        self.trace = _Trace()
        self.calls: list[dict[str, Any]] = []
        self.kwargs: list[dict[str, Any]] = []
        self.events: list[tuple[str, dict[str, Any]]] = []

    def on_step(self, step: str, payload: dict[str, Any]) -> None:
        self.events.append((step, payload))

    def is_outline_option_enabled(
        self,
        *,
        capability: str,
        enabled: bool,
        allowed_providers_raw: str,
        allowed_models_raw: str,
        task_type: Any,
    ) -> bool:
        return bool(enabled)

    async def call_with_retry(
        self,
        _task_type: Any,
        context: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(context)
        self.kwargs.append(_kwargs)
        chapters = context["outline"]["chapters"]
        response = {
            "chapter_contracts": [
                {
                    "chapter_number": int(chapter["chapter_number"]),
                    "title": f"LLM 第{chapter['chapter_number']}章",
                    "required_events": [f"LLM 第{chapter['chapter_number']}章事件"],
                    "source": "plan_chapter_contracts",
                }
                for chapter in chapters
            ]
        }
        raw_sink = _kwargs.get("_capture_raw")
        if isinstance(raw_sink, list):
            raw_sink.append(json.dumps(response, ensure_ascii=False))
        return response


def test_chapter_contract_coverage_backfills_missing_chapters() -> None:
    payload, coverage = _ensure_chapter_contract_coverage({"chapter_contracts": []}, _outline())

    assert coverage["complete"] is True
    assert coverage["backfilled_chapters"] == [1, 2]
    assert [item["chapter_number"] for item in payload["chapter_contracts"]] == [1, 2]
    assert payload["chapter_contracts"][0]["source"] == "outline_backfill"
    assert "建立时间裂缝谜题" in payload["chapter_contracts"][0]["required_events"]


def test_chapter_contract_coverage_preserves_valid_contracts_and_discards_noise() -> None:
    payload, coverage = _ensure_chapter_contract_coverage(
        {
            "chapter_contracts": [
                {
                    "chapter_number": 1,
                    "title": "LLM 标题",
                    "required_events": "必须发现怀表",
                    "source": "plan_chapter_contracts",
                },
                {"chapter_number": 99, "required_events": ["越界"]},
                {"chapter_number": 1, "required_events": ["重复"]},
            ]
        },
        _outline(),
    )

    assert coverage["backfilled_chapters"] == [2]
    assert coverage["discarded_count"] == 1
    assert coverage["duplicate_count"] == 1
    assert payload["chapter_contracts"][0]["title"] == "LLM 标题"
    assert payload["chapter_contracts"][0]["required_events"] == ["必须发现怀表"]
    assert payload["chapter_contracts"][1]["source"] == "outline_backfill"


def test_chapter_contract_source_marker_copy_is_not_treated_as_local_backfill() -> None:
    payload, coverage = _ensure_chapter_contract_coverage(
        {
            "chapter_contracts": [
                {
                    "chapter_number": 1,
                    "title": "LLM 标题",
                    "required_events": ["LLM 生成的精简事件"],
                    "required_progressions": ["LLM 生成的精简推进"],
                    "source": "outline_backfill",
                }
            ]
        },
        _outline(),
    )

    assert coverage["backfilled_chapters"] == [2]
    assert payload["chapter_contracts"][0]["source"] == "plan_chapter_contracts"
    assert payload["chapter_contracts"][0]["required_events"] == ["LLM 生成的精简事件"]


def test_outline_backfill_contract_budget_scales_by_chapter_word_count() -> None:
    chapter = ChapterOutline(
        chapter_number=1,
        title="长章",
        goal="完成本章核心目标",
        main_plot_points=[f"主线事件{i}" for i in range(1, 6)],
        beats_summary=[f"细节节拍{i}" for i in range(1, 9)],
        subplot_points=["支线铺垫1", "支线铺垫2", "支线铺垫3", "支线铺垫4"],
        expected_word_count=4500,
    )
    settings = SimpleNamespace(
        chapter_contract_dynamic_budget_enabled=True,
        chapter_contract_hard_words_per_item=1000,
        chapter_contract_hard_min_items=3,
        chapter_contract_hard_max_items=5,
        chapter_contract_soft_words_per_item=900,
        chapter_contract_soft_max_items=4,
        chapter_contract_state_words_per_item=1200,
        chapter_contract_state_max_items=3,
    )

    contract = _outline_backfill_chapter_contract(chapter, settings=settings)

    assert len(contract["required_progressions"]) == 5
    assert len(contract["required_events"]) == 3
    assert len(contract["allowed_progressions"]) == 4
    assert len(contract["completion_criteria"]) <= 3
    assert "细节节拍8" not in contract["allowed_progressions"]


def test_reusable_llm_narrative_contract_survives_deterministic_rebuild(tmp_path: Any) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("project"))
    storage.save_json(
        layout.narrative_contract_path,
        {
            "world_rules": ["规则"],
            "llm_contract": {
                "world_rules": [{"rule_id": "WR001", "rule": "医案必须有证据链"}],
                "character_arcs": [{"character": "沈知微", "arc": "从自证到共证"}],
                "plot_threads": [
                    {
                        "thread_name": "银镯刻字",
                        "chapters": "1-3",
                        "resolution": "第3章确认刻字来源",
                    }
                ],
            },
        },
    )
    ctx = SimpleNamespace(storage=storage, layout=layout)

    contract = _load_reusable_llm_narrative_contract(ctx)

    assert contract is not None
    assert contract["world_rules"][0]["rule_id"] == "WR001"
    assert contract["plot_threads"][0]["thread_name"] == "银镯刻字"
    assert contract["plot_threads"][0]["chapters"] == "1-3"


def test_reusable_llm_narrative_contract_rejects_input_hash_drift(tmp_path: Any) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("project"))
    storage.save_json(
        layout.narrative_contract_path,
        {
            "world_rules": ["规则"],
            "llm_contract_input_hashes": {"spec": "old"},
            "llm_contract": {
                "world_rules": [{"rule_id": "WR001", "rule": "医案必须有证据链"}],
                "character_arcs": [{"character": "沈知微", "arc": "从自证到共证"}],
                "plot_threads": [{"thread_name": "银镯刻字"}],
            },
        },
    )
    ctx = SimpleNamespace(storage=storage, layout=layout)

    contract = _load_reusable_llm_narrative_contract(
        ctx,
        input_hashes={"spec": "new"},
    )

    assert contract is None


def test_reusable_llm_narrative_contract_accepts_legacy_envelope_self_hash(
    tmp_path: Any,
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("project"))
    storage.save_json(
        layout.narrative_contract_path,
        {
            "world_rules": ["规则"],
            "llm_contract_input_hashes": {
                "spec": "same",
                "deterministic_contract": "legacy-pre-attachment-hash",
            },
            "llm_contract": {
                "world_rules": [{"rule_id": "WR001", "rule": "医案必须有证据链"}],
                "character_arcs": [],
                "plot_threads": [{"thread_name": "银镯刻字"}],
            },
        },
    )
    ctx = SimpleNamespace(storage=storage, layout=layout)

    contract = _load_reusable_llm_narrative_contract(
        ctx,
        input_hashes={"spec": "same", "deterministic_contract": "projected-hash"},
    )

    assert contract is not None
    assert contract["plot_threads"][0]["thread_name"] == "银镯刻字"


def test_persist_llm_narrative_contract_records_input_hashes(tmp_path: Any) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("project"))
    storage.save_json(layout.narrative_contract_path, {"world_rules": ["规则"]})
    ctx = SimpleNamespace(storage=storage, layout=layout)

    _persist_llm_narrative_contract(
        ctx,
        llm_contract={
            "world_rules": [{"rule_id": "WR001", "rule": "医案必须有证据链"}],
            "character_arcs": [],
            "plot_threads": [{"thread_name": "银镯刻字"}],
        },
        state_store=None,
        source="deterministic_projection",
        input_hashes={"spec": "hash-spec"},
    )

    payload = storage.load_json(layout.narrative_contract_path)
    assert payload["llm_contract_input_hashes"] == {"spec": "hash-spec"}
    assert payload["llm_contract_source"] == "deterministic_projection"
    assert payload["deterministic_contract_snapshot"] == {"world_rules": ["规则"]}

    stable_hashes = _llm_narrative_contract_input_hashes(
        spec={"title": "test"},
        story_bible={},
        character_bible={},
        entity_registry={},
        deterministic_contract=payload,
    )
    bare_hashes = _llm_narrative_contract_input_hashes(
        spec={"title": "test"},
        story_bible={},
        character_bible={},
        entity_registry={},
        deterministic_contract={"world_rules": ["规则"]},
    )
    assert stable_hashes["deterministic_contract"] == bare_hashes["deterministic_contract"]


async def test_initialize_contracts_when_narrative_state_disabled_keeps_chapter_contracts(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("deterministic_contracts"))
    layout.ensure_dirs()
    storage.save_json(
        layout.blueprint_path,
        {
            "synopsis": "沈珩追查旧案。",
            "plot_threads": [{"thread_name": "旧案", "chapters": "1"}],
        },
    )
    events: list[tuple[str, Any]] = []

    async def fail_call_with_retry(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("narrative_state_disabled should not call LLM contract generation")

    ctx = SimpleNamespace(
        storage=storage,
        layout=layout,
        settings=SimpleNamespace(
            narrative_state_enabled=False,
            narrative_state_required=True,
            init_narrative_contract_llm_enabled=True,
            init_chapter_contract_resume_strict_noise=True,
            init_coherence_auto_repair=True,
        ),
        router=object(),
        trace=_Trace(),
        on_step=lambda step, payload: events.append((step, payload)),
        call_with_retry=fail_call_with_retry,
    )
    outline = StoryOutline(
        total_chapters=2,
        hard_through_chapter=1,
        planned_through_chapter=2,
        synopsis="沈珩追查旧案。",
        chapters=[
            ChapterOutline(
                chapter_number=1,
                title="旧案重启",
                goal="发现旧案线索",
                beats_summary=["沈珩找到账册。"],
                main_plot_points=["旧案重新进入主线。"],
            ),
            ChapterOutline(
                chapter_number=2,
                title="钟楼追踪",
                goal="追查账册来源",
                beats_summary=["沈珩进入钟楼。"],
                main_plot_points=["线索指向旧图书馆。"],
            ),
        ],
    )
    coverage = {
        "complete": True,
        "missing_chapters": [],
        "backfilled_chapters": [],
        "discarded_count": 0,
        "duplicate_count": 0,
        "local_fallback_accepted": False,
    }

    class _FailingStateStore:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("NarrativeStateStore must not be created when disabled")

    async def fake_batched_generate_chapter_contracts(
        *_args: Any, **_kwargs: Any
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return (
            {
                "chapter_contracts": [
                    {
                        "chapter_number": 1,
                        "title": "旧案重启",
                        "required_events": ["沈珩找到账册。"],
                        "source": "plan_chapter_contracts",
                    }
                ]
            },
            dict(coverage),
        )

    coherence_artifacts: list[dict[str, Any]] = []

    async def fake_run_init_coherence_v2_gate(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        coherence_artifacts.append(kwargs["artifacts"])
        return {
            "verdict": "accept",
            "blocked": False,
            "issues": [],
            "summary": "ok",
        }

    coverage_artifacts: list[dict[str, Any]] = []

    def fake_claim_coverage_audit(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        coverage_artifacts.append(kwargs["artifacts"])
        return {
            "verdict": "accept",
            "blocked": False,
            "issues": [],
            "summary": "ok",
        }

    monkeypatch.setattr(init_contract_flow_module, "NarrativeStateStore", _FailingStateStore)
    monkeypatch.setattr(
        init_contract_flow_module,
        "_batched_generate_chapter_contracts",
        fake_batched_generate_chapter_contracts,
    )
    monkeypatch.setattr(
        init_contract_flow_module,
        "_persist_chapter_contract_runtime_artifacts",
        lambda *_args, **_kwargs: SimpleNamespace(milestones=[]),
    )
    monkeypatch.setattr(
        init_contract_flow_module,
        "run_init_coherence_v2_gate",
        fake_run_init_coherence_v2_gate,
    )
    monkeypatch.setattr(
        init_contract_flow_module,
        "run_init_claim_contract_coverage_audit",
        fake_claim_coverage_audit,
    )

    result = await initialize_narrative_state_and_contracts(
        ctx=ctx,
        layout=layout,
        story_bible=StoryBible(premise="沈珩追查旧案。"),
        character_bible=CharacterBible(characters=[CharacterProfile(name="沈珩")]),
        blueprint=SimpleNamespace(model_dump=lambda mode="json": {"synopsis": "沈珩追查旧案。"}),
        outline=outline,
        language="zh",
        words_per_chapter=3000,
        settings=ctx.settings,
        on_step=ctx.on_step,
        project_id="deterministic_contracts",
        entity_registry=SimpleNamespace(entities=[]),
        init_entity_catalog={
            "allowed_entities": [],
            "allowed_entity_ids": [],
            "allowed_entity_names": [],
            "alias_to_entity": {},
            "policy": "测试",
        },
        outline_ctx={},
        total_chapters=1,
        enriched_spec=StorySpec(
            theme="旧案",
            genre="xuanhuan",
            tone="serious",
            length_target=3000,
        ),
        init_coherence_profile={},
        init_coherence_reports={
            "blueprint_coherence": None,
            "outline_inheritance": None,
            "contract_coherence": None,
            "claim_contract_coverage": None,
            "source_artifacts": None,
        },
        init_repairs=[],
    )

    assert result["chapter_contracts"]["chapter_contracts"][0]["chapter_number"] == 1
    contract_payload = storage.load_json(layout.narrative_contract_path)
    assert (
        contract_payload["llm_contract_source"]
        == "deterministic_projection_narrative_state_disabled"
    )
    assert coverage_artifacts[-1]["outline"]["total_chapters"] == 2
    assert coverage_artifacts[-1]["outline"]["hard_through_chapter"] == 1
    assert coverage_artifacts[-1]["outline"]["planned_through_chapter"] == 2
    assert coherence_artifacts[-1]["outline"] == coverage_artifacts[-1]["outline"]
    assert "init_narrative_contract_skipped" in [step for step, _payload in events]
    assert "plan_chapter_contracts" in [step for step, _payload in events]


def test_reusable_chapter_contracts_validate_coverage_and_keep_ledger(tmp_path: Any) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("project"))
    outline = _outline()
    ctx = SimpleNamespace(storage=storage, layout=layout)
    storage.save_json(
        layout.plans_dir / "chapter_contracts.json",
        {
            "chapter_contracts": [
                {
                    "chapter_number": 1,
                    "title": "开端契约",
                    "required_events": ["发现怀表"],
                    "source": "plan_chapter_contracts",
                },
                {
                    "chapter_number": 2,
                    "title": "追踪契约",
                    "required_events": ["进入钟楼"],
                    "source": "plan_chapter_contracts",
                },
            ]
        },
    )
    storage.save_json(
        layout.progression_ledger_path, {"entries": [{"chapter": 1}], "last_chapter": 1}
    )

    loaded = _load_reusable_chapter_contracts(ctx, outline=outline)

    assert loaded is not None
    chapter_contracts, coverage = loaded
    assert coverage["complete"] is True
    assert [item["chapter_number"] for item in chapter_contracts["chapter_contracts"]] == [1, 2]

    narrative_contract = {"plot_threads": []}
    assert (
        _load_reusable_chapter_contracts(
            ctx,
            outline=outline,
            narrative_contract=narrative_contract,
        )
        is None
    )

    milestone_index = _persist_chapter_contract_runtime_artifacts(
        ctx,
        outline=outline,
        chapter_contracts=chapter_contracts,
        llm_contract=narrative_contract,
        project_id="project",
    )

    assert len(milestone_index.milestones) == 2
    assert storage.load_json(layout.progression_ledger_path)["last_chapter"] == 1

    cached_with_hashes = _load_reusable_chapter_contracts(
        ctx,
        outline=outline,
        narrative_contract=narrative_contract,
    )
    assert cached_with_hashes is not None
    assert (
        _load_reusable_chapter_contracts(
            ctx,
            outline=outline.model_copy(update={"synopsis": "changed"}),
            narrative_contract=narrative_contract,
        )
        is None
    )
    assert (
        _load_reusable_chapter_contracts(
            ctx,
            outline=outline,
            narrative_contract={"plot_threads": [{"thread_name": "changed"}]},
        )
        is None
    )


def test_reusable_chapter_contracts_reject_backfilled_or_noisy_cache(tmp_path: Any) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("project"))
    outline = _outline()
    ctx = SimpleNamespace(storage=storage, layout=layout)
    storage.save_json(
        layout.plans_dir / "chapter_contracts.json",
        {
            "chapter_contracts": [
                {
                    "chapter_number": 1,
                    "title": "开端契约",
                    "required_events": ["发现怀表"],
                    "source": "plan_chapter_contracts",
                },
                _outline_backfill_chapter_contract(outline.chapters[1]),
                {"chapter_number": 99, "title": "越界噪声"},
            ]
        },
    )

    assert _load_reusable_chapter_contracts(ctx, outline=outline) is None


def test_reusable_chapter_contracts_relaxed_partitions_backfilled_cache(tmp_path: Any) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("project"))
    outline = _outline()
    ctx = SimpleNamespace(storage=storage, layout=layout, settings=SimpleNamespace())
    storage.save_json(
        layout.plans_dir / "chapter_contracts.json",
        {
            "chapter_contracts": [
                {
                    "chapter_number": 1,
                    "title": "开端契约",
                    "required_events": ["发现怀表"],
                    "source": "plan_chapter_contracts",
                }
            ]
        },
    )

    reusable = _load_reusable_chapter_contracts(ctx, outline=outline, strict_noise=False)

    assert reusable is not None
    assert reusable.reusable_chapters == [1]
    assert reusable.rebuild_chapters == [2]
    assert [item["chapter_number"] for item in reusable.payload["chapter_contracts"]] == [1]


def test_partition_chapter_contracts_for_resume_marks_schema_invalid_chapters() -> None:
    outline = _outline()
    payload, coverage = _ensure_chapter_contract_coverage(
        {
            "chapter_contracts": [
                {
                    "chapter_number": 1,
                    "title": "开端契约",
                    "required_events": ["发现怀表"],
                    "source": "plan_chapter_contracts",
                },
                {
                    "chapter_number": 2,
                    "title": "追踪契约",
                    "required_events": ["进入钟楼"],
                    "source": "plan_chapter_contracts",
                },
            ]
        },
        outline,
    )

    result = partition_chapter_contracts_for_resume(
        payload,
        coverage,
        outline=outline,
        schema_invalid_chapters=[2],
    )

    assert result.reusable_chapters == [1]
    assert result.rebuild_chapters == [2]
    assert [item["chapter_number"] for item in result.payload["chapter_contracts"]] == [1]


def test_source_artifact_resume_scope_marks_chapter_for_rebuild(tmp_path: Any) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("project"))
    ctx = SimpleNamespace(storage=storage, layout=layout)
    storage.save_json(
        layout.reports_dir / "init_readiness.json",
        {
            "allowed": False,
            "blocked": True,
            "stages": {
                "contract_coherence": {"blocked": False, "verdict": "accept"},
                "claim_contract_coverage": {"blocked": False, "verdict": "accept"},
                "source_artifacts": {
                    "blocked": True,
                    "verdict": "needs_repair",
                    "issues": [
                        {
                            "type": "unresolved_contract_entity",
                            "description": "第 2 章契约引用未登记角色/实体：未知",
                            "repair_scope": [
                                {
                                    "artifact": "chapter_contracts",
                                    "chapters": [2],
                                    "fields": ["cognitive_constraints"],
                                }
                            ],
                        }
                    ],
                },
            },
        },
    )

    assert _source_artifact_resume_rebuild_chapters(ctx, outline=_outline()) == [2]


def test_reusable_chapter_contracts_force_rebuild_survives_output_hash_drift(
    tmp_path: Any,
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("project"))
    outline = _outline()
    ctx = SimpleNamespace(storage=storage, layout=layout, settings=SimpleNamespace())
    narrative_contract = {"plot_threads": []}
    chapter_contracts = {
        "chapter_contracts": [
            {
                "chapter_number": 1,
                "title": "开端契约",
                "required_events": ["发现怀表"],
                "source": "plan_chapter_contracts",
            },
            {
                "chapter_number": 2,
                "title": "追踪契约",
                "required_events": ["进入钟楼"],
                "source": "plan_chapter_contracts",
            },
        ]
    }
    _persist_chapter_contract_runtime_artifacts(
        ctx,
        outline=outline,
        chapter_contracts=chapter_contracts,
        llm_contract=narrative_contract,
        project_id="project",
    )
    drifted = storage.load_json(layout.plans_dir / "chapter_contracts.json")
    drifted["chapter_contracts"][1]["title"] = "追踪契约（待修）"
    storage.save_json(layout.plans_dir / "chapter_contracts.json", drifted)

    assert (
        _load_reusable_chapter_contracts(
            ctx,
            outline=outline,
            narrative_contract=narrative_contract,
        )
        is None
    )

    reusable = _load_reusable_chapter_contracts(
        ctx,
        outline=outline,
        narrative_contract=narrative_contract,
        force_rebuild_chapters=[2],
    )

    assert reusable is not None
    assert reusable.reusable_chapters == [1]
    assert reusable.rebuild_chapters == [2]
    assert [item["chapter_number"] for item in reusable.payload["chapter_contracts"]] == [1]


async def test_focused_chapter_contract_generation_only_calls_target_chapters() -> None:
    ctx = _ChapterContractCtx()
    outline = _outline_with_chapters(4)

    payload, coverage = await _batched_generate_chapter_contracts(
        ctx,
        outline=outline,
        narrative_contract={"plot_threads": []},
        project_id="project",
        focus_chapters=[2, 4],
    )

    assert coverage["complete"] is True
    assert [item["chapter_number"] for item in payload["chapter_contracts"]] == [2, 4]
    called_chapters = [
        [int(chapter["chapter_number"]) for chapter in call["outline"]["chapters"]]
        for call in ctx.calls
    ]
    assert called_chapters == [[2, 4]]


def test_merge_reusable_and_rebuilt_chapter_contracts_restores_full_coverage() -> None:
    outline = _outline()
    merged, coverage = merge_reusable_and_rebuilt_chapter_contracts(
        {
            "chapter_contracts": [
                {
                    "chapter_number": 1,
                    "title": "开端契约",
                    "required_events": ["发现怀表"],
                    "source": "plan_chapter_contracts",
                }
            ]
        },
        {
            "chapter_contracts": [
                {
                    "chapter_number": 2,
                    "title": "重建契约",
                    "required_events": ["进入钟楼"],
                    "source": "plan_chapter_contracts",
                }
            ]
        },
        outline=outline,
    )

    assert coverage["complete"] is True
    assert [item["chapter_number"] for item in merged["chapter_contracts"]] == [1, 2]
    assert merged["chapter_contracts"][1]["title"] == "重建契约"


def test_reusable_chapter_contracts_accept_local_fallback_marker(tmp_path: Any) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("project"))
    outline = _outline()
    ctx = SimpleNamespace(storage=storage, layout=layout, settings=SimpleNamespace())
    storage.save_json(
        layout.plans_dir / "chapter_contracts.json",
        {
            "chapter_contracts": [
                {
                    "chapter_number": 1,
                    "title": "开端契约",
                    "required_events": ["发现怀表"],
                    "source": "plan_chapter_contracts",
                },
                {
                    "chapter_number": 2,
                    "title": "追踪契约",
                    "required_events": ["进入钟楼"],
                    "source": "plan_chapter_contracts",
                },
            ],
            "coverage": {"local_fallback_accepted": True},
        },
    )

    reusable = _load_reusable_chapter_contracts(ctx, outline=outline)

    assert reusable is not None
    chapter_contracts, coverage = reusable
    assert len(chapter_contracts["chapter_contracts"]) == 2
    assert coverage["local_fallback_accepted"] is True


def test_chapter_contract_source_defaults_to_llm_for_present_items() -> None:
    payload, coverage = _ensure_chapter_contract_coverage(
        {
            "chapter_contracts": [
                {
                    "chapter_number": 1,
                    "title": "开端契约",
                    "required_events": ["发现怀表"],
                },
                {
                    "chapter_number": 2,
                    "title": "追踪契约",
                    "required_events": ["进入钟楼"],
                },
            ]
        },
        _outline(),
    )

    assert coverage["backfilled_chapters"] == []
    assert {item["source"] for item in payload["chapter_contracts"]} == {"plan_chapter_contracts"}


def test_chapter_contracts_with_backfill_are_not_persist_ready() -> None:
    chapter_contracts, coverage = _ensure_chapter_contract_coverage(
        {
            "chapter_contracts": [
                {
                    "chapter_number": 1,
                    "title": "开端契约",
                    "required_events": ["发现怀表"],
                    "source": "plan_chapter_contracts",
                }
            ]
        },
        _outline(),
    )

    try:
        _assert_chapter_contracts_ready_to_persist(chapter_contracts, coverage)
    except InitCoherenceError as exc:
        assert "backfilled_chapters" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("backfilled chapter contracts should not be persisted as clean output")


def test_chapter_contract_policy_accepts_local_fallback_for_missing_contracts() -> None:
    outline = _outline()
    policy = get_init_repair_policy(InitArtifact.CHAPTER_CONTRACTS)

    outcome = asyncio.run(
        InitRepairOrchestrator(policy).repair(
            {
                "chapter_contracts": [
                    {
                        "chapter_number": 1,
                        "title": "开端契约",
                        "required_events": ["发现怀表"],
                        "source": "plan_chapter_contracts",
                    }
                ]
            },
            InitRepairContext(
                service_ctx=None,
                outline_ctx={},
                total_chapters=2,
                artifacts={"outline": outline},
            ),
        )
    )

    assert outcome.report.is_valid
    assert outcome.repaired is True
    assert outcome.payload["coverage"]["local_fallback_accepted"] is True
    assert outcome.payload["coverage"]["backfilled_chapters"] == [2]
    assert [item["chapter_number"] for item in outcome.payload["chapter_contracts"]] == [1, 2]


def test_chapter_contract_policy_llm_repair_forwards_entity_catalog() -> None:
    outline = _outline()
    service_ctx = _ChapterContractCtx()
    entity_catalog = {
        "allowed_entities": [
            {
                "entity_id": "char_xuanyu",
                "canonical_name": "玄昱",
                "entity_type": "character",
                "aliases": [],
            }
        ],
        "policy": "只允许白名单实体。",
    }
    policy = get_init_repair_policy(InitArtifact.CHAPTER_CONTRACTS)

    outcome = asyncio.run(
        InitRepairOrchestrator(policy).repair(
            {
                "chapter_contracts": [
                    {
                        "chapter_number": 1,
                        "title": "开端契约",
                        "required_events": ["发现怀表"],
                        "source": "plan_chapter_contracts",
                    }
                ]
            },
            InitRepairContext(
                service_ctx=service_ctx,
                outline_ctx={},
                total_chapters=2,
                artifacts={
                    "outline": outline,
                    "narrative_contract": {"world_rules": []},
                    "entity_catalog": entity_catalog,
                },
            ),
        )
    )

    assert outcome.report.is_valid
    assert service_ctx.calls
    assert service_ctx.calls[0]["outline"]["entity_catalog"] == entity_catalog


def test_chapter_contract_max_tokens_uses_routed_model_output_capacity() -> None:
    ctx = SimpleNamespace(router=_OutputLimitRouter(32768))

    assert _chapter_contract_batch_max_tokens(ctx, 4) == 32768
    assert _chapter_contract_batch_max_tokens(ctx, 1) == 16384


def test_chapter_contract_batch_projects_large_entity_catalog() -> None:
    outline = _outline_with_chapters(2)
    entities = [
        {
            "entity_id": f"loc_{index}",
            "canonical_name": f"地点{index}",
            "entity_type": "location",
            "aliases": [f"别名{index}"],
        }
        for index in range(120)
    ]
    entities.insert(
        0,
        {
            "entity_id": "char_lead",
            "canonical_name": "主角",
            "entity_type": "character",
            "aliases": [],
        },
    )
    entity_catalog = {
        "allowed_entities": entities,
        "alias_to_entity": {},
    }

    payload = _chapter_contract_batch_outline_payload(
        outline,
        list(outline.chapters[:1]),
        settings=SimpleNamespace(chapter_contract_entity_catalog_max_entities=16),
        entity_catalog=entity_catalog,
    )

    projected = payload["entity_catalog"]
    assert len(projected["allowed_entities"]) <= 16
    assert "char_lead" in projected["allowed_entity_ids"]
    assert projected["projection"] == {
        "mode": "characters_plus_batch_exact_mentions",
        "selected_count": len(projected["allowed_entities"]),
        "full_count": 121,
        "max_entities": 16,
    }


def test_batched_generate_chapter_contracts_scopes_outline_and_merges_coverage() -> None:
    ctx = _ChapterContractCtx()
    outline = _outline_with_chapters(5)

    payload, coverage = asyncio.run(
        _batched_generate_chapter_contracts(
            ctx,  # type: ignore[arg-type]
            outline=outline,
            narrative_contract={"world_rules": [], "character_arcs": [], "plot_threads": []},
            project_id="unit-project",
        )
    )

    assert [[ch["chapter_number"] for ch in call["outline"]["chapters"]] for call in ctx.calls] == [
        [1, 2, 3, 4],
        [5],
    ]
    assert [item["chapter_number"] for item in payload["chapter_contracts"]] == [1, 2, 3, 4, 5]
    assert payload["chapter_contracts"][0]["title"] == "LLM 第1章"
    assert payload["chapter_contracts"][1]["source"] == "plan_chapter_contracts"
    assert coverage["complete"] is True
    assert coverage["batch_count"] == 2
    assert coverage["batch_backfilled_chapters"] == []
    assert ctx.calls[0]["outline"]["contract_batch"]["chapter_numbers"] == [1, 2, 3, 4]
    assert [item["chapter_number"] for item in ctx.calls[0]["outline"]["contract_scaffold"]] == [
        1,
        2,
        3,
        4,
    ]
    assert "source" not in ctx.calls[0]["outline"]["contract_scaffold"][0]
    assert (
        ctx.calls[0]["outline"]["contract_scaffold"][0]["contract_budget"]["hard_required_limit"]
        == 3
    )
    assert "完成第1章目标" in ctx.calls[0]["outline"]["contract_scaffold"][0]["required_events"]
    assert [
        item["chapter_number"] for item in ctx.calls[0]["outline"]["context_chapters"]["next"]
    ] == [
        5,
    ]
    assert [
        item["chapter_number"] for item in ctx.calls[1]["outline"]["context_chapters"]["previous"]
    ] == [3, 4]
    assert [
        item["chapter_number"] for item in ctx.calls[1]["outline"]["previous_chapter_contracts"]
    ] == [1, 2, 3, 4]
    assert ctx.kwargs[0]["multi_turn"] is True
    assert ctx.kwargs[0]["prior_messages"] is None
    assert ctx.kwargs[1]["prior_messages"]
    assert "上一批章节契约摘要" in ctx.kwargs[1]["prior_messages"][-1]["content"]
    assert [step for step, _ in ctx.events] == [
        "plan_chapter_contracts_batch_1_4",
        "plan_chapter_contracts_batch_5_5",
    ]


def test_batched_generate_chapter_contracts_injects_init_claim_constraints(tmp_path: Any) -> None:
    ctx = _ChapterContractCtx()
    ctx.storage = FileSystemStorage(tmp_path)
    ctx.layout = ProjectLayout(ctx.storage.ensure_project_dir("project"))
    ctx.storage.save_json(
        ctx.layout.memory_dir / CLAIM_LEDGER_JSON,
        {
            "active_claim_ids": ["claim_identity"],
            "claims_by_id": {
                "claim_identity": {
                    "status": "active",
                    "claim_id": "claim_identity",
                    "artifact": "outline",
                    "source_path": "/chapters/0/goal",
                    "source_field": "goal",
                    "chapter_numbers": [1],
                    "claim_type": "knowledge",
                    "claim_text": "玄昱确认沈清漪即青阳会盟少年，但只在心里知道。",
                    "evidence": "第1章内心确认，第50章公开相认。",
                    "cognitive_subjects": ["玄昱"],
                    "cognitive_object": "沈清漪即青阳会盟少年",
                    "cognitive_level": "confirmed",
                    "action_level": "internal",
                    "reader_awareness": "full",
                    "character_knowledge_coverage": {"玄昱": "full", "沈清漪": "unknown"},
                    "cognitive_chapter": 1,
                    "public_reveal_chapter": 50,
                    "foreshadow_chapters": [1, 3, 7],
                }
            },
        },
    )

    asyncio.run(
        _batched_generate_chapter_contracts(
            ctx,  # type: ignore[arg-type]
            outline=_outline_with_chapters(2),
            narrative_contract={"world_rules": [], "character_arcs": [], "plot_threads": []},
            project_id="unit-project",
        )
    )

    constraints = ctx.calls[0]["outline"]["init_claim_constraints"]
    first_constraint = constraints["1"][0]
    assert first_constraint["claim_id"] == "claim_identity"
    assert first_constraint["cognitive_subjects"] == ["玄昱"]
    assert first_constraint["cognitive_object"] == "沈清漪即青阳会盟少年"
    assert first_constraint["cognitive_level"] == "confirmed"
    assert first_constraint["action_level"] == "internal"
    assert first_constraint["public_reveal_chapter"] == 50


class _PartialFormatRepairChapterContractCtx(_ChapterContractCtx):
    async def call_with_retry(
        self,
        _task_type: Any,
        context: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(context)
        self.kwargs.append(_kwargs)
        chapters = context["outline"]["chapters"]
        items = [
            {
                "chapter_number": int(chapter["chapter_number"]),
                "title": f"LLM 第{chapter['chapter_number']}章",
                "required_events": [f"LLM 第{chapter['chapter_number']}章事件"],
                "source": "plan_chapter_contracts",
            }
            for chapter in chapters[:-1]
        ]
        return {
            "chapter_contracts": items,
            "coverage": {
                "local_fallback_accepted": True,
                "partial_format_repair_accepted": True,
            },
        }


def test_batched_generate_chapter_contracts_propagates_partial_format_repair_marker() -> None:
    ctx = _PartialFormatRepairChapterContractCtx()
    outline = _outline_with_chapters(4)

    payload, coverage = asyncio.run(
        _batched_generate_chapter_contracts(
            ctx,  # type: ignore[arg-type]
            outline=outline,
            narrative_contract={"world_rules": [], "character_arcs": [], "plot_threads": []},
            project_id="unit-project",
        )
    )

    assert [item["chapter_number"] for item in payload["chapter_contracts"]] == [1, 2, 3, 4]
    assert payload["chapter_contracts"][-1]["source"] == "outline_backfill"
    assert coverage["batch_backfilled_chapters"] == [4]
    assert coverage["local_fallback_accepted"] is True
    assert coverage["partial_format_repair_accepted"] is True
    _assert_chapter_contracts_ready_to_persist(payload, coverage, allow_local_fallback=True)


def test_chapter_contract_batch_payload_respects_zero_context_window() -> None:
    outline = _outline_with_chapters(3)

    payload = _chapter_contract_batch_outline_payload(
        outline,
        [outline.chapters[1]],
        previous_contracts=[
            {
                "chapter_number": 1,
                "title": "上一章",
                "required_events": ["上一章事件"],
            }
        ],
        context_window=0,
    )

    assert payload["context_chapters"]["previous"] == []
    assert payload["context_chapters"]["next"] == []
    assert payload["previous_chapter_contracts"] == []


class _SplitChapterContractCtx(_ChapterContractCtx):
    def __init__(self) -> None:
        super().__init__()
        self.failed_once = False

    async def call_with_retry(
        self,
        _task_type: Any,
        context: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        numbers = [int(chapter["chapter_number"]) for chapter in context["outline"]["chapters"]]
        if numbers == [1, 2, 3, 4] and not self.failed_once:
            self.failed_once = True
            self.calls.append(context)
            self.kwargs.append(_kwargs)
            raise RuntimeError("simulated truncated batch")
        return await super().call_with_retry(_task_type, context, **_kwargs)


class _NetworkFailureChapterContractCtx(_ChapterContractCtx):
    async def call_with_retry(
        self,
        _task_type: Any,
        context: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(context)
        self.kwargs.append(_kwargs)
        raise ModelGatewayError(
            "provider DNS unavailable",
            is_transient=True,
            failure_categories=["timeout", "network_error"],
        )


class _AutonomousRecoveryAdapter(ProviderAdapter):
    def __init__(
        self,
        provider_name: str,
        *,
        partial_text: str = "",
        stream_error: Exception,
    ) -> None:
        self._provider_name = provider_name
        self._partial_text = partial_text
        self._stream_error = stream_error
        self.stream_calls = 0
        self.complete_calls = 0

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def default_model(self) -> str | None:
        return f"{self._provider_name}-model"

    async def complete(self, request: ModelRequest) -> ModelResponse:
        del request
        self.complete_calls += 1
        api_connection_error = type("APIConnectionError", (Exception,), {})
        raise api_connection_error("Connection error.")

    async def stream(self, request: ModelRequest, *, on_final=None):
        del request, on_final
        self.stream_calls += 1
        if self._partial_text:
            yield StreamChunk(content=self._partial_text)
        raise self._stream_error


class _ChapterContractRequestBuilder:
    def build(
        self,
        task_type: Any,
        context: dict[str, Any],
        *,
        max_tokens: int,
        temperature: float,
        top_p: float | None = None,
        prior_messages: list[dict[str, str]] | None = None,
        thinking: bool = False,
        multi_turn: bool = False,
    ) -> ModelRequest:
        del context, prior_messages
        return ModelRequest(
            task_type=task_type,
            messages=[{"role": "user", "content": "plan chapter contracts"}],
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p if top_p is not None else 1.0,
            thinking=thinking,
            multi_turn=multi_turn,
        )


class _AutonomousRecoveryChapterContractCtx(_ChapterContractCtx):
    def __init__(self, router: ModelRouter) -> None:
        super().__init__()
        self.router = router
        self.settings.llm_format_repair_enabled = False
        self.llm_service = LLMService(
            router,
            _ChapterContractRequestBuilder(),  # type: ignore[arg-type]
            self.on_step,
            settings=self.settings,
        )

    async def call_with_retry(
        self,
        task_type: Any,
        context: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(context)
        self.kwargs.append(kwargs)
        result = await self.llm_service.call_with_retry(task_type, context, **kwargs)
        assert isinstance(result, dict)
        return result


def test_batched_generate_chapter_contracts_splits_failed_batches_without_duplicates() -> None:
    ctx = _SplitChapterContractCtx()
    outline = _outline_with_chapters(4)

    payload, coverage = asyncio.run(
        _batched_generate_chapter_contracts(
            ctx,  # type: ignore[arg-type]
            outline=outline,
            narrative_contract={"world_rules": [], "character_arcs": [], "plot_threads": []},
            project_id="unit-project",
        )
    )

    assert [[ch["chapter_number"] for ch in call["outline"]["chapters"]] for call in ctx.calls] == [
        [1, 2, 3, 4],
        [1, 2],
        [3, 4],
    ]
    assert [item["chapter_number"] for item in payload["chapter_contracts"]] == [1, 2, 3, 4]
    assert coverage["duplicate_count"] == 0
    assert [step for step, _ in ctx.events] == [
        "plan_chapter_contracts_split",
        "plan_chapter_contracts_batch_1_4",
    ]


def test_batched_generate_chapter_contracts_does_not_split_transport_failure() -> None:
    ctx = _NetworkFailureChapterContractCtx()
    outline = _outline_with_chapters(4)

    try:
        asyncio.run(
            _batched_generate_chapter_contracts(
                ctx,  # type: ignore[arg-type]
                outline=outline,
                narrative_contract={"world_rules": [], "character_arcs": [], "plot_threads": []},
                project_id="unit-project",
            )
        )
    except ModelGatewayError as exc:
        assert exc.is_transient_error is True
    else:  # pragma: no cover - protects the regression setup
        raise AssertionError("expected transport failure")

    assert len(ctx.calls) == 1
    assert [chapter["chapter_number"] for chapter in ctx.calls[0]["outline"]["chapters"]] == [
        1,
        2,
        3,
        4,
    ]
    assert [step for step, _ in ctx.events] == ["plan_chapter_contracts_transport_deferred"]


def test_chapter_contracts_autonomously_recover_real_route_failure_chain(tmp_path: Any) -> None:
    complete_rows = [
        {
            "chapter_number": chapter_number,
            "title": f"LLM 第{chapter_number}章",
            "source": "plan_chapter_contracts",
        }
        for chapter_number in (1, 2, 3)
    ]
    complete_json = json.dumps({"chapter_contracts": complete_rows}, ensure_ascii=False)
    truncated = (
        complete_json[:-2]
        + ', {"chapter_number": 4, "title": "正在生成但连接中断'
    )
    api_connection_error = type("APIConnectionError", (Exception,), {})
    primary = _AutonomousRecoveryAdapter(
        "primary",
        partial_text=truncated,
        stream_error=asyncio.TimeoutError("stream idle timeout"),
    )
    fallback = _AutonomousRecoveryAdapter(
        "fallback",
        stream_error=api_connection_error("Connection error."),
    )
    router = ModelRouter(
        adapters={"primary": primary, "fallback": fallback},
        default_provider="primary",
        task_fallbacks={
            TaskType.PLAN_CHAPTER_CONTRACTS: [
                TaskRouteOverride(provider="fallback", model_id="fallback-model")
            ]
        },
        request_timeout_s=0,
        workflow_timeout_s=0,
    )
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("autonomous-recovery"))
    ctx = _AutonomousRecoveryChapterContractCtx(router)
    ctx.storage = storage
    ctx.layout = layout

    async def _no_sleep(_seconds: float) -> None:
        return None

    with patch(
        "novel_forge.pipeline.long.services.generation.llm_service.asyncio.sleep",
        new=_no_sleep,
    ):
        payload, coverage = asyncio.run(
            _batched_generate_chapter_contracts(
                ctx,  # type: ignore[arg-type]
                outline=_outline_with_chapters(4),
                narrative_contract={"world_rules": [], "character_arcs": [], "plot_threads": []},
                project_id="autonomous-recovery",
            )
        )

    assert [item["chapter_number"] for item in payload["chapter_contracts"]] == [1, 2, 3, 4]
    assert payload["chapter_contracts"][-1]["source"] == "outline_backfill"
    assert coverage["local_fallback_accepted"] is True
    assert coverage["partial_format_repair_accepted"] is True
    assert coverage["backfilled_chapters"] == [4]
    assert primary.stream_calls == 1
    assert primary.complete_calls == 3
    assert fallback.complete_calls == 3
    event_names = [step for step, _ in ctx.events]
    assert "json_stream_fallback_to_route" in event_names
    assert "json_stream_partial_recovery_succeeded" in event_names
    assert "plan_chapter_contracts_split" not in event_names
    partial = storage.load_json(layout.plans_dir / "chapter_contracts.partial.json")
    assert [item["chapter_number"] for item in partial["chapter_contracts"]] == [1, 2, 3]


def test_batched_generate_chapter_contracts_resumes_validated_partial_batches(
    tmp_path: Any,
) -> None:
    class _FailOnThirdChapterCtx(_ChapterContractCtx):
        async def call_with_retry(
            self,
            task_type: Any,
            context: dict[str, Any],
            **kwargs: Any,
        ) -> dict[str, Any]:
            numbers = [int(chapter["chapter_number"]) for chapter in context["outline"]["chapters"]]
            if 3 in numbers:
                self.calls.append(context)
                self.kwargs.append(kwargs)
                raise RuntimeError("simulated terminal chapter failure")
            return await super().call_with_retry(task_type, context, **kwargs)

    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("partial-project"))
    outline = _outline_with_chapters(4)
    failing = _FailOnThirdChapterCtx()
    failing.storage = storage
    failing.layout = layout

    try:
        asyncio.run(
            _batched_generate_chapter_contracts(
                failing,  # type: ignore[arg-type]
                outline=outline,
                narrative_contract={"plot_threads": []},
                project_id="partial-project",
            )
        )
    except RuntimeError as exc:
        assert "terminal chapter failure" in str(exc)
    else:  # pragma: no cover - protects the regression setup
        raise AssertionError("expected the first run to fail")

    partial = storage.load_json(layout.plans_dir / "chapter_contracts.partial.json")
    assert [item["chapter_number"] for item in partial["chapter_contracts"]] == [1, 2]

    resumed = _ChapterContractCtx()
    resumed.storage = storage
    resumed.layout = layout
    payload, coverage = asyncio.run(
        _batched_generate_chapter_contracts(
            resumed,  # type: ignore[arg-type]
            outline=outline,
            narrative_contract={"plot_threads": []},
            project_id="partial-project",
        )
    )

    assert [
        [chapter["chapter_number"] for chapter in call["outline"]["chapters"]]
        for call in resumed.calls
    ] == [[3, 4]]
    assert [item["chapter_number"] for item in payload["chapter_contracts"]] == [1, 2, 3, 4]
    assert coverage["complete"] is True
    assert resumed.events[0][0] == "plan_chapter_contracts_partial_resumed"


class _ContractCoherenceCtx(_ChapterContractCtx):
    async def call_with_retry(
        self,
        _task_type: Any,
        context: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(context)
        self.kwargs.append(_kwargs)
        current = context["narrative_contract"]["chapter_contracts"]["current"]
        numbers = [int(item["chapter_number"]) for item in current]
        if numbers == [3, 4]:
            return {
                "verdict": "needs_repair",
                "issues": [
                    {
                        "severity": "high",
                        "description": "第3-4章承诺回收顺序不清",
                        "resolution": "明确第4章承接第3章的出口状态",
                    }
                ],
                "summary": "发现中段契约风险。",
            }
        return {"verdict": "accept", "issues": [], "summary": "未发现硬冲突。"}


def test_batched_adjudicate_contract_coherence_uses_compact_global_index_and_merges() -> None:
    ctx = _ContractCoherenceCtx()
    chapter_contracts = {
        "chapter_contracts": [
            {
                "chapter_number": index,
                "title": f"第{index}章",
                "required_events": [f"第{index}章必须事件", "次要事件"],
                "exit_state_targets": [f"第{index}章出口状态"],
            }
            for index in range(1, 6)
        ]
    }

    report = asyncio.run(
        _batched_adjudicate_contract_coherence(
            ctx,  # type: ignore[arg-type]
            narrative_contract={"world_rules": ["时间裂缝不可逆"]},
            chapter_contracts=chapter_contracts,
            project_id="unit-project",
        )
    )

    assert [
        [
            item["chapter_number"]
            for item in call["narrative_contract"]["chapter_contracts"]["current"]
        ]
        for call in ctx.calls
    ] == [[1, 2], [3, 4], [5]]
    first_payload = ctx.calls[0]["narrative_contract"]
    second_payload = ctx.calls[1]["narrative_contract"]
    assert len(first_payload["all_chapter_contract_index"]) == 5
    assert [
        item["chapter_number"] for item in second_payload["chapter_contracts"]["previous_context"]
    ] == [2]
    assert [
        item["chapter_number"] for item in second_payload["chapter_contracts"]["next_context"]
    ] == [5]
    assert report["verdict"] == "needs_repair"
    assert report["batch_count"] == 3
    assert report["issues"][0]["severity"] == "high"
    assert [step for step, _ in ctx.events] == [
        "adjudicate_contract_coherence_batch_1_2",
        "adjudicate_contract_coherence_batch_3_4",
        "adjudicate_contract_coherence_batch_5_5",
    ]


def test_batched_generate_chapter_contracts_backfills_cognitive_constraints(
    tmp_path: Any,
) -> None:
    """LLM is expected to copy every init_claim_constraint; the runner
    must deterministically backfill any missing entries into
    cognitive_constraints so the audit does not block the run.
    """
    ctx = _ChapterContractCtx()
    ctx.settings.init_claim_constraints_backfill_enabled = True
    ctx.storage = FileSystemStorage(tmp_path)
    ctx.layout = ProjectLayout(ctx.storage.ensure_project_dir("project"))
    ctx.storage.save_json(
        ctx.layout.memory_dir / CLAIM_LEDGER_JSON,
        {
            "active_claim_ids": ["claim_a", "claim_b"],
            "claims_by_id": {
                "claim_a": {
                    "status": "active",
                    "claim_id": "claim_a",
                    "artifact": "outline",
                    "source_path": "/chapters/0/goal",
                    "source_field": "goal",
                    "chapter_numbers": [1],
                    "claim_type": "knowledge",
                    "claim_text": "A 认知",
                    "evidence": "A 证据",
                    "cognitive_subjects": ["玄策"],
                    "cognitive_object": "A 对象",
                    "cognitive_level": "confirmed",
                    "action_level": "internal",
                    "reader_awareness": "full",
                    "character_knowledge_coverage": {"玄策": "full"},
                    "cognitive_chapter": 1,
                    "public_reveal_chapter": 50,
                    "foreshadow_chapters": [1, 3],
                },
                "claim_b": {
                    "status": "active",
                    "claim_id": "claim_b",
                    "artifact": "blueprint",
                    "source_path": "/volumes/0/milestone_targets/2",
                    "source_field": "milestone_targets",
                    "chapter_numbers": [1],
                    "claim_type": "event",
                    "claim_text": "B 事件",
                    "evidence": "B 证据",
                    "cognitive_subjects": ["商溟"],
                    "cognitive_object": "B 对象",
                    "cognitive_level": "partial",
                    "action_level": "hinted",
                    "reader_awareness": "partial",
                    "character_knowledge_coverage": {"商溟": "full"},
                    "cognitive_chapter": 1,
                    "public_reveal_chapter": 38,
                    "foreshadow_chapters": [],
                },
            },
        },
    )

    payload, _coverage = asyncio.run(
        _batched_generate_chapter_contracts(
            ctx,  # type: ignore[arg-type]
            outline=_outline_with_chapters(2),
            narrative_contract={"world_rules": [], "character_arcs": [], "plot_threads": []},
            project_id="unit-project",
        )
    )

    chapter1 = next(item for item in payload["chapter_contracts"] if item["chapter_number"] == 1)
    cog_ids = {
        str(item.get("claim_id"))
        for item in chapter1.get("cognitive_constraints", [])
        if isinstance(item, dict)
    }
    assert "claim_a" in cog_ids
    assert "claim_b" in cog_ids


def test_batched_generate_chapter_contracts_preserves_all_cognitive_backfill(
    tmp_path: Any,
) -> None:
    ctx = _ChapterContractCtx()
    ctx.settings.init_claim_constraints_backfill_enabled = True
    ctx.storage = FileSystemStorage(tmp_path)
    ctx.layout = ProjectLayout(ctx.storage.ensure_project_dir("project"))
    claims_by_id = {}
    for index in range(130):
        claim_id = f"claim_{index}"
        claims_by_id[claim_id] = {
            "status": "active",
            "claim_id": claim_id,
            "artifact": "outline",
            "source_path": f"/chapters/0/claim/{index}",
            "source_field": "goal",
            "chapter_numbers": [1],
            "claim_type": "knowledge",
            "claim_text": f"第{index}条认知约束。",
            "evidence": f"第{index}条证据。",
            "cognitive_subjects": ["玄策"],
            "cognitive_object": f"对象{index}",
            "cognitive_level": "confirmed",
            "action_level": "internal",
            "reader_awareness": "partial",
            "character_knowledge_coverage": {"玄策": "partial"},
            "cognitive_chapter": 1 if index >= 10 else None,
            "public_reveal_chapter": None,
            "foreshadow_chapters": [],
        }
    ctx.storage.save_json(
        ctx.layout.memory_dir / CLAIM_LEDGER_JSON,
        {
            "active_claim_ids": list(claims_by_id),
            "claims_by_id": claims_by_id,
        },
    )

    payload, coverage = asyncio.run(
        _batched_generate_chapter_contracts(
            ctx,  # type: ignore[arg-type]
            outline=_outline_with_chapters(1),
            narrative_contract={"world_rules": [], "character_arcs": [], "plot_threads": []},
            project_id="unit-project",
        )
    )

    chapter1 = payload["chapter_contracts"][0]
    constraints = chapter1["cognitive_constraints"]
    kept_ids = {item["claim_id"] for item in constraints}
    assert len(constraints) == 130
    assert kept_ids == set(claims_by_id)
    assert "cognitive_constraints_capped" not in coverage


def test_backfill_repairs_placeholder_cognitive_constraints(tmp_path: Any) -> None:
    """The contract-coherence LLM sometimes emits ``cognitive_constraints``
    entries whose only filled field is ``claim_id`` (placeholder rows).
    The backfill must re-hydrate the entry from the claim ledger so the
    audit has claim_text / cognitive_object / cognitive_subjects to match
    against. This pins the repair path: an existing placeholder for
    ``claim_placeholder`` is rewritten in place using the ledger row.
    """
    from novel_forge.pipeline.long.services.init.init_service import (
        _backfill_cognitive_constraints,
        _init_claim_constraints_by_chapter,
    )

    ctx = _ChapterContractCtx()
    ctx.settings.init_claim_constraints_backfill_enabled = True
    ctx.storage = FileSystemStorage(tmp_path)
    ctx.layout = ProjectLayout(ctx.storage.ensure_project_dir("project"))
    ctx.storage.save_json(
        ctx.layout.memory_dir / CLAIM_LEDGER_JSON,
        {
            "active_claim_ids": ["claim_placeholder"],
            "claims_by_id": {
                "claim_placeholder": {
                    "status": "active",
                    "claim_id": "claim_placeholder",
                    "artifact": "blueprint",
                    "source_path": "/volumes/0/milestone_targets/2",
                    "source_field": "milestone_targets",
                    "chapter_numbers": [2],
                    "claim_type": "payoff",
                    "claim_text": "角色在第 2 章交出间谍名册完成有限救赎",
                    "evidence": "卷四目标要求兑现该承诺。",
                    "cognitive_subjects": ["商漾"],
                    "cognitive_object": "蜀谍网名册",
                    "cognitive_level": "confirmed",
                    "action_level": "revealed",
                    "reader_awareness": "full",
                    "character_knowledge_coverage": {"商漾": "full"},
                    "cognitive_chapter": 2,
                    "public_reveal_chapter": 2,
                    "foreshadow_chapters": [],
                },
            },
        },
    )

    constraints_by_chapter = _init_claim_constraints_by_chapter(ctx)
    selected = {2: constraints_by_chapter.get(2, [])}

    chapter_contracts = {
        "chapter_contracts": [
            {
                "chapter_number": 2,
                "title": "登基大典",
                "cognitive_constraints": [
                    {
                        "claim_id": "claim_placeholder",
                        "claim_text": "",
                        "cognitive_subjects": [],
                        "cognitive_object": "",
                    },  # placeholder with blank fields
                ],
            }
        ]
    }
    result = _backfill_cognitive_constraints(chapter_contracts, selected, settings=ctx.settings)
    constraint = result["chapter_contracts"][0]["cognitive_constraints"][0]
    assert constraint["claim_id"] == "claim_placeholder"
    # The repair path must have filled the text and object fields.
    assert "间谍名册" in str(constraint.get("claim_text") or "")
    assert "蜀谍网名册" in str(constraint.get("cognitive_object") or "")
    assert "商漾" in (constraint.get("cognitive_subjects") or [])


def test_claim_constraint_entity_catalog_exact_alias_only() -> None:
    from novel_forge.core.schemas.init_coherence import CoherenceClaim
    from novel_forge.pipeline.long.services.context.source_artifacts import (
        build_init_entity_catalog,
    )
    from novel_forge.pipeline.long.services.init.init_service import (
        _cognitive_constraint_from_claim,
    )

    entity_catalog = build_init_entity_catalog(
        {
            "entities": [
                {
                    "entity_id": "char_qingyi",
                    "name": "沈清漪",
                    "entity_type": "character",
                    "aliases": ["清漪"],
                }
            ],
            "entity_links": [],
        },
        {"characters": [{"name": "沈清漪", "character_id": "char_qingyi"}]},
    )
    claim = CoherenceClaim.model_validate(
        {
            "claim_id": "claim_qingyi",
            "artifact": "blueprint",
            "source_path": "/key_turning_points/0",
            "claim_text": "清漪确认魂玉线索，清湍只是模型误写。",
            "evidence": "清漪确认线索。",
            "subject_ids": ["char_qingyi"],
            "cognitive_subjects": ["清漪", "清湍"],
            "cognitive_object": "魂玉线索",
            "cognitive_level": "confirmed",
            "action_level": "internal",
            "reader_awareness": "partial",
            "character_knowledge_coverage": {"清漪": "full", "清湍": "partial"},
            "cognitive_chapter": 1,
            "public_reveal_chapter": None,
            "foreshadow_chapters": [],
        }
    )

    constraint = _cognitive_constraint_from_claim(claim, entity_catalog=entity_catalog)

    assert constraint["cognitive_subjects"] == ["沈清漪"]
    assert constraint["character_knowledge_coverage"] == {"沈清漪": "full"}


def test_init_claim_constraint_prompt_projection_and_backfill_are_lossless() -> None:
    """Prompt projection drops provenance weight, never semantic constraint rows."""
    from novel_forge.pipeline.long.services.init.init_service import (
        _select_init_claim_constraints_for_backfill,
        _select_init_claim_constraints_for_batch,
    )

    constraints_by_chapter = {
        1: [
            {
                "claim_id": f"claim_{index}",
                "claim_text": f"约束 {index}",
                "character_knowledge_coverage": {"沈清漪": "partial"},
                "source_path": f"/claims/{index}",
                "evidence": f"证据 {index}",
            }
            for index in range(5)
        ]
    }

    prompt_selected = _select_init_claim_constraints_for_batch(
        constraints_by_chapter,
        [1],
    )
    backfill_selected = _select_init_claim_constraints_for_backfill(
        constraints_by_chapter,
        [1],
    )

    expected_ids = [f"claim_{index}" for index in range(5)]
    assert [item["claim_id"] for item in prompt_selected["1"]] == expected_ids
    assert [item["claim_id"] for item in backfill_selected["1"]] == expected_ids
    assert prompt_selected["1"][0]["character_knowledge_coverage"] == {"沈清漪": "partial"}
    assert "source_path" not in prompt_selected["1"][0]
    assert "evidence" not in prompt_selected["1"][0]
    assert backfill_selected["1"][0]["evidence"] == "证据 0"
