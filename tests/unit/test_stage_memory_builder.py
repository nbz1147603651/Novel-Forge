"""Tests for stage-aware memory builder policies and adapters."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.constraints.constraint_router import build_stage_cards
from novel_forge.pipeline.long.services.context.stage_memory_builder import (
    MemoryStage,
    StageMemoryBuilder,
    build_finalize_eval_memory_context,
    collect_draft_memory_hints,
    collect_planning_memory_hints,
    get_stage_memory_policy,
    persist_stage_memory_diagnostics_report,
)


class _FakeEpisodic:
    def __init__(self) -> None:
        self.search_calls: list[dict[str, object]] = []

    async def search_by_semantic(self, **kwargs: object):
        self.search_calls.append(dict(kwargs))
        chapter_range = kwargs.get("chapter_range")
        all_results = [
            SimpleNamespace(
                chapter_number=2,
                event_summary="信号首次出现",
                relevance_score=0.78,
            ),
            SimpleNamespace(
                chapter_number=4,
                event_summary="上一章末尾发生冲突",
                relevance_score=0.82,
            ),
        ]
        if chapter_range:
            lo, hi = chapter_range
            return [r for r in all_results if lo <= r.chapter_number <= hi]
        return all_results

    async def get_outline_context(self, **_: object):
        return SimpleNamespace(
            chapter_summary="最近几章围绕异常信号推进。",
            unresolved_questions=["信号来自哪里"],
            relationship_changes=[
                SimpleNamespace(character_a="林远", character_b="周岚", change_type="互信上升")
            ],
            similar_events=[
                SimpleNamespace(
                    chapter_number=3,
                    event_summary="旧章节相似事件",
                    relevance_score=0.7,
                ),
                SimpleNamespace(
                    chapter_number=99,
                    event_summary="未来相似事件不应注入",
                    relevance_score=0.99,
                ),
            ],
        )


class _FakeMotifTracker:
    def __init__(self) -> None:
        self.motif_context_calls: list[dict[str, object]] = []
        self.forward_guidance_calls: list[dict[str, object]] = []

    def get_motifs_for_prompt(self, **kwargs: object):
        self.motif_context_calls.append(dict(kwargs))
        return {
            "forbidden_repetition": ["冷光"],
            "suggested_callbacks": [{"motif": "纸灰", "reason": "呼应旧线"}],
            "active_motifs": [{"motif": "纸灰", "category": "意象"}],
        }

    def get_suggestions_for_chapter(self, **_: object):
        return [
            SimpleNamespace(
                motif_id="m1",
                motif_name="纸灰",
                reason="呼应旧线",
                priority="high",
                suggested_context="在桥段收束时点到为止",
                retired=False,
            ),
            SimpleNamespace(
                motif_id="m2",
                motif_name="冷月",
                reason="不应进入草稿建议",
                priority="medium",
                suggested_context="不应进入",
                retired=False,
            ),
        ]

    async def get_forward_looking_guidance(self, **kwargs: object) -> dict[str, object]:
        self.forward_guidance_calls.append(dict(kwargs))
        return {"suggested_callbacks": ["纸灰"], "avoid": ["冷光"]}


class _FakeMemoryContext:
    def __init__(self) -> None:
        self.episodic_memory = _FakeEpisodic()
        self.motif_tracker = _FakeMotifTracker()
        self.expression_memory = None
        self.settings = SimpleNamespace(
            memory_motif_related_lookback_chapters=4,
            motif_dormant_callback_min_chapters=12,
        )
        self.layered_calls: list[dict[str, object]] = []
        self.history_calls: list[dict[str, object]] = []

    def get_memory_context_for_prompt(self, **kwargs: object) -> dict[str, object]:
        include_critiques = bool(kwargs.get("include_critiques"))
        return {
            "motif_context": {
                "forbidden_repetition": ["冷光"],
                "active_motifs": [{"motif_id": "m1", "name": "纸灰"}],
            },
            "critique_context": "避免重复解释设定。" if include_critiques else "",
        }

    def get_layered_context(self, **kwargs: object) -> dict[str, str]:
        self.layered_calls.append(dict(kwargs))
        return {
            "L0_identity": "标题：测试书 | 题材：科幻",
            "L1_core_memory": "主角已确认异常信号并继续追查。",
            "L2_on_demand": "## 近期事件\n- 第4章：信号再次出现",
            "L3_deep_search": "## 相关历史\n- 第2章：[相关度0.78] 信号首次出现",
        }

    async def search_relevant_history(self, **kwargs: object):
        self.history_calls.append(dict(kwargs))
        return [
            {
                "chapter_number": 2,
                "event_summary": "信号首次出现",
                "relevance_score": 0.78,
            },
            {
                "chapter_number": 99,
                "event_summary": "未来章节事件不应注入",
                "relevance_score": 0.99,
            },
        ]


class _ScopedPromptMemoryContext(_FakeMemoryContext):
    def __init__(self) -> None:
        super().__init__()
        self.prompt_calls: list[dict[str, object]] = []
        self.character_calls: list[tuple[str, dict[str, object]]] = []

    def get_memory_context_for_prompt(self, **kwargs: object) -> dict[str, object]:
        self.prompt_calls.append(dict(kwargs))
        return {
            "summary_context": "全局摘要不应进入草稿。",
            "motif_context": {
                "forbidden_repetition": ["冷光"],
                "active_motifs": [{"motif_id": "m1", "name": "纸灰"}],
            },
            "critique_context": "避免重复解释设定。" if kwargs.get("include_critiques") else "",
        }

    async def get_character_context(self, character_id: str, **kwargs: object) -> dict[str, object]:
        self.character_calls.append((character_id, dict(kwargs)))
        return {
            "character_id": character_id,
            "summary_context": f"{character_id}只知道钟楼线索。",
            "episodic_context": [
                {
                    "chapter_number": 4,
                    "event_summary": f"{character_id}亲眼看到怀表停摆。",
                    "relevance_score": 0.81,
                }
            ],
            "motif_context": {
                "associated_motifs": [
                    {
                        "motif_id": "m1",
                        "name": "纸灰",
                        "thematic_meaning": f"{character_id}的旧线索回流",
                    }
                ]
            },
        }


class _GlobalPromptOnlyMemoryContext(_FakeMemoryContext):
    def __init__(self) -> None:
        super().__init__()
        self.prompt_calls: list[dict[str, object]] = []

    def get_memory_context_for_prompt(self, **kwargs: object) -> dict[str, object]:
        self.prompt_calls.append(dict(kwargs))
        payload: dict[str, object] = {
            "motif_context": {"forbidden_repetition": ["冷光"]},
            "critique_context": "避免重复解释设定。" if kwargs.get("include_critiques") else "",
        }
        if kwargs.get("include_summaries"):
            payload["summary_context"] = "全局摘要不应在 fallback 进入草稿。"
        return payload


class _FakeExpressionMemory:
    async def search_expression_channel_memory(self, **_: object) -> list[dict[str, object]]:
        return [
            {
                "channel": "somatic_reaction",
                "channel_id": "timepiece_body_signal",
                "text": "怀表触发后的身体惊动",
                "source": "expression_zvec",
                "level": "soft",
                "recent_semantic_hits": [
                    {
                        "chapter": 2,
                        "scene_index": 1,
                        "quote": "心口一紧",
                        "context": "时间裂缝",
                        "similarity": 0.91,
                    }
                ],
                "semantic_hit_count": 1,
                "last_seen_chapter": 2,
                "semantic_similarity_max": 0.91,
            }
        ]


def _make_bundle() -> SimpleNamespace:
    return SimpleNamespace(
        chapter_outline=SimpleNamespace(
            goal="追查异常信号来源",
            pov_character="林远",
            setting="旧城天台",
            main_plot_points=["定位信号来源"],
            subplot_points=[],
            involved_characters=["林远", "周岚"],
        ),
        story_bible=SimpleNamespace(
            title="测试书",
            genre="科幻",
            tone="冷峻悬疑",
            premise="主角追查一段改变现实的异常信号。",
        ),
        chapter_state_packet=SimpleNamespace(
            previous_exit_state=SimpleNamespace(open_questions=["信号来自哪里"])
        ),
        character_profiles=[],
        layout=SimpleNamespace(root=SimpleNamespace()),
    )


def _make_runner(settings: SimpleNamespace | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        has_memory_context=lambda: True,
        memory_context=_FakeMemoryContext(),
        _settings=settings or SimpleNamespace(),
    )


def test_stage_policies_define_expected_layers() -> None:
    planning = get_stage_memory_policy(MemoryStage.PLANNING)
    draft = get_stage_memory_policy(MemoryStage.DRAFT)
    finalize = get_stage_memory_policy(MemoryStage.FINALIZE)

    assert planning.layered_layers == ("L1_core_memory",)
    assert draft.layered_layers == ("L1_core_memory",)
    assert draft.include_motif_continuity is True
    assert finalize.layered_layers == ("L1_core_memory",)


@pytest.mark.asyncio
async def test_collect_planning_memory_hints_includes_planning_layers() -> None:
    runner = _make_runner()
    hints = await collect_planning_memory_hints(
        runner,
        _make_bundle(),
        chapter_number=5,
    )

    assert set(hints["layered_context"]) == {"L1_core_memory"}
    assert hints["relevant_history"][0]["event_summary"] == "信号首次出现"
    assert all(item["chapter_number"] < 5 for item in hints["relevant_history"])
    assert hints["motif_continuity"]["forbidden_repetition"] == ["冷光"]
    tracker = runner.memory_context.motif_tracker
    assert tracker.motif_context_calls[0]["related_lookback_chapters"] == 4
    assert tracker.forward_guidance_calls[0]["dormant_callback_min_chapters"] == 12
    assert tracker.forward_guidance_calls[0]["chapter_outline"]["goal"] == "追查异常信号来源"
    assert hints["forward_motif_guidance"] == {
        "suggested_callbacks": ["纸灰"],
        "avoid": ["冷光"],
    }
    assert "critique_context" not in hints
    assert hints["outline_context"]["similar_events"] == [
        {"chapter_number": 3, "event_summary": "旧章节相似事件", "relevance_score": 0.7}
    ]
    assert hints["memory_diagnostics"]["requested_layers"] == ["L1_core_memory"]
    assert hints["memory_diagnostics"]["resolved_layers"] == ["L1_core_memory"]
    assert hints["memory_diagnostics"]["counts"]["relevant_history"] == 2
    assert hints["memory_diagnostics"]["sources"]["relevant_history"] == "fetched"
    assert hints["memory_diagnostics"]["flags"]["has_motif_continuity"] is True


@pytest.mark.parametrize("prefetched", [False, True])
async def test_published_revision_invalidates_outline_hints_but_keeps_actual_history(
    tmp_path, prefetched
):
    runner = _make_runner()
    bundle = _make_bundle()
    bundle.layout.plans_dir = tmp_path
    (tmp_path / "active_planning_revision.json").write_text('{"revision_id":"new"}')
    prior = {"outline_context": {"chapter_summary": "已废弃的规划"}}
    if prefetched:
        prior["relevant_history"] = [
            {"chapter_number": 2, "event_summary": "信号首次出现", "relevance_score": 0.8}
        ]
    result = await StageMemoryBuilder(
        runner,
        bundle,
        chapter_number=5,
        prefetched_hints=prior if prefetched else None,
    ).build(MemoryStage.PLANNING)
    assert result.outline_context == {}
    assert result.relevant_history[0]["event_summary"] == "信号首次出现"
    assert result.diagnostics["sources"]["outline_context"] == "invalidated_by_planning_revision"


@pytest.mark.asyncio
async def test_chapter1_planning_routes_outline_context_without_history_events() -> None:
    runner = _make_runner()
    bundle = _make_bundle()
    hints = await collect_planning_memory_hints(
        runner,
        bundle,
        chapter_number=1,
    )

    assert hints["outline_context"]["chapter_summary"] == "最近几章围绕异常信号推进。"
    assert "previous_chapter_events" not in hints
    assert "relevant_history" not in hints

    cards = build_stage_cards(
        stage="plan",
        chapter_outline=bundle.chapter_outline,
        memory_hints=hints,
    )

    assert "最近几章围绕异常信号推进。" in cards["memory"]["outline_context"]
    assert "信号来自哪里" in cards["memory"]["outline_context"]


@pytest.mark.asyncio
async def test_build_finalize_eval_memory_context_uses_finalize_layers_and_prefetched_history() -> (
    None
):
    ctx = await build_finalize_eval_memory_context(
        _make_runner(),
        _make_bundle(),
        chapter_number=5,
        memory_hints={
            "relevant_history": [
                {"chapter_number": 3, "event_summary": "旧线索回响", "relevance_score": 0.66}
            ],
            "previous_chapter_events": [
                {"chapter_number": 4, "event_summary": "上一章动作接力", "relevance_score": 0.91}
            ],
        },
    )

    assert ctx["memory_relevant_history"][0]["event_summary"] == "旧线索回响"
    assert ctx["memory_previous_chapter_events"][0]["event_summary"] == "上一章动作接力"
    assert ctx["memory_forbidden_repetition"] == ["冷光"]
    assert ctx["memory_layered_context"]["L1_core_memory"] == "主角已确认异常信号并继续追查。"
    assert "L3_deep_search" not in ctx["memory_layered_context"]
    assert ctx["memory_diagnostics"]["sources"]["relevant_history"] == "prefetched"
    assert ctx["memory_diagnostics"]["sources"]["previous_chapter_events"] == "prefetched"
    assert ctx["memory_diagnostics"]["planning_prefetch_reused"] is True
    assert ctx["memory_diagnostics"]["draft_stage_extras_collected"] is False
    assert ctx["memory_diagnostics"]["flags"]["has_outline_context"] is False


@pytest.mark.asyncio
async def test_collect_draft_memory_hints_preserves_tracker_ranked_motif_suggestions() -> None:
    hints = await collect_draft_memory_hints(
        _make_runner(),
        _make_bundle(),
        plan=SimpleNamespace(emotional_arc="由怀疑转向决心"),
        bridge=None,
        chapter_number=5,
        planning_hints={"relevant_history": [], "previous_chapter_events": []},
    )

    assert set(hints["memory_layered_context"]) == {"L1_core_memory"}
    assert hints["motif_continuity"]["suggested_callbacks"] == [
        {"motif": "纸灰", "reason": "呼应旧线"}
    ]
    assert [item["motif_name"] for item in hints["motif_suggestions"]] == ["纸灰", "冷月"]
    assert hints["memory_diagnostics"]["counts"]["motif_suggestions"] == 2
    assert hints["memory_diagnostics"]["sources"]["motif_suggestions"] == "generated"
    assert hints["memory_diagnostics"]["planning_prefetch_reused"] is False
    assert hints["memory_diagnostics"]["draft_stage_extras_collected"] is True


@pytest.mark.asyncio
async def test_draft_prompt_context_uses_only_nonduplicated_auxiliary_context() -> None:
    memory_ctx = _ScopedPromptMemoryContext()
    runner = SimpleNamespace(
        has_memory_context=lambda: True,
        memory_context=memory_ctx,
        _settings=SimpleNamespace(),
    )

    hints = await collect_draft_memory_hints(
        runner,
        _make_bundle(),
        plan=SimpleNamespace(emotional_arc="由怀疑转向决心"),
        bridge=None,
        chapter_number=5,
        planning_hints={"relevant_history": [], "previous_chapter_events": []},
    )

    assert memory_ctx.character_calls == []
    assert memory_ctx.prompt_calls[0]["include_summaries"] is False
    assert memory_ctx.prompt_calls[0]["include_motifs"] is False
    assert memory_ctx.prompt_calls[0]["include_critiques"] is False
    prompt_context = hints.get("memory_prompt_context", {})
    assert prompt_context == {}
    diagnostics = hints["memory_diagnostics"]
    assert diagnostics["prompt_context_scope"] == "global_no_summary"
    assert diagnostics["pov_characters"] == ["林远"]


@pytest.mark.asyncio
async def test_draft_prompt_context_does_not_duplicate_multi_pov_history() -> None:
    memory_ctx = _ScopedPromptMemoryContext()
    runner = SimpleNamespace(
        has_memory_context=lambda: True,
        memory_context=memory_ctx,
        _settings=SimpleNamespace(),
    )
    plan = SimpleNamespace(
        emotional_arc="由怀疑转向决心",
        scene_intents=[
            SimpleNamespace(pov_character="林远"),
            SimpleNamespace(pov_character="周岚"),
        ],
    )

    hints = await collect_draft_memory_hints(
        runner,
        _make_bundle(),
        plan=plan,
        bridge=None,
        chapter_number=5,
        planning_hints={"relevant_history": [], "previous_chapter_events": []},
    )

    assert memory_ctx.character_calls == []
    assert hints.get("memory_prompt_context", {}) == {}
    assert hints["memory_diagnostics"]["prompt_context_scope"] == "global_no_summary"
    assert hints["memory_diagnostics"]["pov_characters"] == ["林远", "周岚"]


@pytest.mark.asyncio
async def test_draft_prompt_context_fallback_disables_global_summary() -> None:
    memory_ctx = _GlobalPromptOnlyMemoryContext()
    runner = SimpleNamespace(
        has_memory_context=lambda: True,
        memory_context=memory_ctx,
        _settings=SimpleNamespace(),
    )

    hints = await collect_draft_memory_hints(
        runner,
        _make_bundle(),
        plan=SimpleNamespace(emotional_arc="由怀疑转向决心"),
        bridge=None,
        chapter_number=5,
        planning_hints={"relevant_history": [], "previous_chapter_events": []},
    )

    assert memory_ctx.prompt_calls[0]["include_summaries"] is False
    assert "summary_context" not in hints.get("memory_prompt_context", {})
    assert hints.get("memory_prompt_context", {}) == {}
    assert hints["memory_diagnostics"]["prompt_context_scope"] == "global_no_summary"


@pytest.mark.asyncio
async def test_collect_draft_memory_hints_uses_bridge_terms_in_queries() -> None:
    runner = _make_runner()
    bridge = SimpleNamespace(
        action_handoff="林晚攥着停摆怀表冲进旧城钟楼",
        opening_location="旧城钟楼",
        emotional_carryover="警觉未退",
        causal_link=SimpleNamespace(
            previous_event="怀表在午夜突然停摆。",
            causal_mechanism="停摆时间对应异常信号峰值，因此林晚必须追踪钟楼源头。",
            unresolved_question="钟楼是否就是信号源？",
            open_threads=["怀表停摆", "钟楼源头"],
        ),
    )

    await collect_draft_memory_hints(
        runner,
        _make_bundle(),
        plan=SimpleNamespace(emotional_arc="由怀疑转向决心"),
        bridge=bridge,
        chapter_number=5,
        planning_hints={},
    )

    history_query = runner.memory_context.episodic_memory.search_calls[0]["query"]
    previous_query = runner.memory_context.episodic_memory.search_calls[-1]["query"]

    assert "怀表在午夜突然停摆" in history_query
    assert "钟楼是否就是信号源" in history_query
    assert "怀表停摆" in history_query
    assert set(runner.memory_context.layered_calls[-1]["budget"].__dict__) >= {
        "l0_identity",
        "l1_core_memory",
        "l2_on_demand",
        "l3_deep_search",
    }
    assert "林晚攥着停摆怀表冲进旧城钟楼" in previous_query


@pytest.mark.asyncio
async def test_bridge_query_keeps_complete_current_chapter_causal_terms() -> None:
    runner = _make_runner()
    bridge = SimpleNamespace(
        action_handoff="林晚冲进旧城钟楼",
        opening_location="旧城钟楼",
        emotional_carryover="警觉未退",
        causal_link=SimpleNamespace(
            previous_event="怀表在午夜突然停摆。",
            causal_mechanism="停摆时间对应异常信号峰值。",
            unresolved_question="钟楼是否就是信号源？",
            open_threads=["怀表停摆", "钟楼源头"],
        ),
    )

    await collect_draft_memory_hints(
        runner,
        _make_bundle(),
        plan=SimpleNamespace(emotional_arc="由怀疑转向决心"),
        bridge=bridge,
        chapter_number=5,
        planning_hints={},
    )

    history_query = runner.memory_context.episodic_memory.search_calls[0]["query"]
    previous_query = runner.memory_context.episodic_memory.search_calls[-1]["query"]

    assert "怀表停摆" in history_query
    assert "怀表停摆" in previous_query
    assert "钟楼源头" in history_query
    assert "钟楼源头" in previous_query


@pytest.mark.asyncio
async def test_stage_memory_builder_merges_expression_zvec_hits_into_profile_record() -> None:
    runner = _make_runner()
    runner.memory_context.expression_memory = _FakeExpressionMemory()
    bundle = _make_bundle()
    bundle.editorial_contract = {
        "expression_channel_profiles": [
            {
                "channel_id": "timepiece_body_signal",
                "channel": "somatic_reaction",
                "label": "怀表触发后的身体惊动",
                "surface_forms": ["心口一紧"],
                "trigger_contexts": ["时间裂缝"],
                "risk_reason": "时间异常反复落在同一种身体反应。",
                "replacement_axes": ["物件操作"],
                "allowed_when": "首次强感应可保留一次。",
                "cooldown_chapters": 3,
                "actor_scope": "global",
                "confidence": 0.8,
            }
        ]
    }

    result = await StageMemoryBuilder(runner, bundle, chapter_number=5).build(MemoryStage.PLANNING)

    records = [
        item
        for item in result.expression_channel_records
        if item.get("channel_id") == "timepiece_body_signal"
    ]
    assert len(records) == 1
    assert records[0]["recent_semantic_hits"][0]["quote"] == "心口一紧"
    assert records[0]["semantic_hit_count"] == 1
    assert result.diagnostics["sources"]["expression_semantic"] == "zvec"


@pytest.mark.asyncio
async def test_stage_memory_builder_emits_summary_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="novel_forge.pipeline.long.stage_memory")

    builder = StageMemoryBuilder(_make_runner(), _make_bundle(), chapter_number=5)
    await builder.build(MemoryStage.PLANNING)

    assert "stage_memory_summary" in caplog.text
    assert "stage=planning" in caplog.text
    assert "chapter=5" in caplog.text
    assert "resolved_layers=L1_core_memory" in caplog.text


def test_persist_stage_memory_diagnostics_report_merges_and_resets(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("memory_diag_demo"))
    layout.ensure_dirs()

    planning_report = persist_stage_memory_diagnostics_report(
        tmp_storage,
        layout,
        5,
        {
            "stage": "planning",
            "chapter_number": 5,
            "requested_layers": ["L0_identity", "L1_core_memory", "L2_on_demand"],
            "resolved_layers": ["L0_identity", "L1_core_memory", "L2_on_demand"],
            "missing_layers": [],
            "counts": {"relevant_history": 1, "previous_chapter_events": 1},
            "flags": {"has_outline_context": True},
            "sources": {"relevant_history": "fetched"},
            "history_reused": False,
            "memory_context_available": True,
        },
    )
    assert planning_report["summary"]["available_stages"] == ["planning"]

    merged_report = persist_stage_memory_diagnostics_report(
        tmp_storage,
        layout,
        5,
        {
            "stage": "draft",
            "chapter_number": 5,
            "requested_layers": [
                "L0_identity",
                "L1_core_memory",
                "L2_on_demand",
                "L3_deep_search",
            ],
            "resolved_layers": [
                "L0_identity",
                "L1_core_memory",
                "L2_on_demand",
                "L3_deep_search",
            ],
            "missing_layers": [],
            "counts": {"relevant_history": 2, "motif_suggestions": 3},
            "flags": {"has_critique_context": True},
            "sources": {"relevant_history": "prefetched"},
            "history_reused": True,
            "memory_context_available": True,
        },
    )
    assert merged_report["summary"]["available_stages"] == ["planning", "draft"]
    assert merged_report["summary"]["history_reused_stages"] == ["draft"]
    assert merged_report["summary"]["counts"]["relevant_history"] == 3

    reset_report = persist_stage_memory_diagnostics_report(
        tmp_storage,
        layout,
        5,
        {
            "stage": "planning",
            "chapter_number": 5,
            "requested_layers": ["L0_identity", "L1_core_memory"],
            "resolved_layers": ["L0_identity"],
            "missing_layers": ["L1_core_memory"],
            "counts": {"relevant_history": 0},
            "flags": {"has_outline_context": False},
            "sources": {"relevant_history": "fetched_empty"},
            "history_reused": False,
            "memory_context_available": True,
        },
    )
    assert reset_report["summary"]["available_stages"] == ["planning"]
    assert list(reset_report["stages"].keys()) == ["planning"]


class _FakeEpisodicWithCharacters:
    """Episodic fake that returns results with characters_involved."""

    def __init__(self) -> None:
        self.search_calls: list[dict[str, object]] = []

    async def search_by_semantic(self, **kwargs: object):
        self.search_calls.append(dict(kwargs))
        return [
            SimpleNamespace(
                chapter_number=4,
                event_summary="POV角色参与的事件",
                relevance_score=0.80,
                characters_involved=["林远", "周岚"],
            ),
            SimpleNamespace(
                chapter_number=3,
                event_summary="其他角色的事件",
                relevance_score=0.90,
                characters_involved=["周岚"],
            ),
            SimpleNamespace(
                chapter_number=2,
                event_summary="无角色信息的事件",
                relevance_score=0.70,
                characters_involved=[],
            ),
        ]

    async def get_outline_context(self, **_: object):
        return SimpleNamespace(
            chapter_summary="最近几章围绕异常信号推进。",
            unresolved_questions=["信号来自哪里"],
            relationship_changes=[],
            similar_events=[],
        )


@pytest.mark.asyncio
async def test_collect_history_preserves_zvec_ranking_scores() -> None:
    episodic = _FakeEpisodicWithCharacters()

    class _MemoryCtx:
        def __init__(self) -> None:
            self.episodic_memory = episodic
            self.motif_tracker = _FakeMotifTracker()
            self.settings = SimpleNamespace(
                memory_motif_related_lookback_chapters=4,
                motif_dormant_callback_min_chapters=12,
            )

        def get_memory_context_for_prompt(self, **_: object) -> dict[str, object]:
            return {
                "motif_context": {
                    "forbidden_repetition": [],
                    "active_motifs": [],
                },
                "critique_context": "",
            }

        def get_layered_context(self, **_: object) -> dict[str, str]:
            return {}

        async def search_relevant_history(self, **_: object):
            return []

    runner = SimpleNamespace(
        has_memory_context=lambda: True,
        memory_context=_MemoryCtx(),
        _settings=SimpleNamespace(),
    )
    bundle = _make_bundle()
    hints = await collect_planning_memory_hints(runner, bundle, chapter_number=5)

    history = hints.get("relevant_history", [])
    assert len(history) >= 1

    pov_item = next(
        (h for h in history if h["event_summary"] == "POV角色参与的事件"),
        None,
    )
    assert pov_item is not None
    assert abs(pov_item["relevance_score"] - 0.80) < 0.001

    other_item = next(
        (h for h in history if h["event_summary"] == "其他角色的事件"),
        None,
    )
    assert other_item is not None
    assert abs(other_item["relevance_score"] - 0.90) < 0.001


@pytest.mark.asyncio
async def test_pov_boost_backward_compatible_no_pov() -> None:
    episodic = _FakeEpisodicWithCharacters()

    class _MemoryCtx:
        def __init__(self) -> None:
            self.episodic_memory = episodic
            self.motif_tracker = _FakeMotifTracker()
            self.settings = SimpleNamespace(
                memory_motif_related_lookback_chapters=4,
                motif_dormant_callback_min_chapters=12,
            )

        def get_memory_context_for_prompt(self, **_: object) -> dict[str, object]:
            return {"motif_context": {"forbidden_repetition": [], "active_motifs": []}}

        def get_layered_context(self, **_: object) -> dict[str, str]:
            return {}

        async def search_relevant_history(self, **_: object):
            return []

    bundle = SimpleNamespace(
        chapter_outline=SimpleNamespace(
            goal="追查异常信号来源",
            pov_character="",
            setting="旧城天台",
            main_plot_points=["定位信号来源"],
            subplot_points=[],
            involved_characters=["林远", "周岚"],
        ),
        story_bible=SimpleNamespace(title="测试书", genre="科幻", tone="冷峻悬疑", premise="测试"),
        chapter_state_packet=SimpleNamespace(
            previous_exit_state=SimpleNamespace(open_questions=[])
        ),
        character_profiles=[],
        layout=SimpleNamespace(root=SimpleNamespace()),
    )

    runner = SimpleNamespace(
        has_memory_context=lambda: True,
        memory_context=_MemoryCtx(),
        _settings=SimpleNamespace(),
    )
    hints = await collect_planning_memory_hints(runner, bundle, chapter_number=5)

    history = hints.get("relevant_history", [])
    pov_item = next(
        (h for h in history if h["event_summary"] == "POV角色参与的事件"),
        None,
    )
    if pov_item is not None:
        assert abs(pov_item["relevance_score"] - 0.80) < 0.001


# ── Regression: chapter 2 generation crash ──────────────────────────
# Bug: 'dict object' has no attribute 'shift_summary' on DRAFT prompt.
# Root cause: _compact_relationship_projection stripped shift_summary=""
# via _drop_empty, then Jinja2 StrictUndefined raised on the missing key.


def test_stage_memory_no_longer_builds_duplicate_relationship_projection() -> None:
    assert "character_prompt_contexts" not in StageMemoryBuilder.__dict__
