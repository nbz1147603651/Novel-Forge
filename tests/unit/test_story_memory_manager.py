"""Tests for StoryMemoryManager chapter packet assembly."""

from __future__ import annotations

import os
from pathlib import Path

from novel_forge.core.schemas.bible import CharacterBible, CharacterProfile
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.core.schemas.story_state import (
    ChapterExitState,
    PlotThreadState,
)
from novel_forge.narrative_state.evidence_contracts import (
    RetrievalEvidenceCard,
    RetrievalEvidencePack,
)
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.chapter_runner import ChapterRunner, ChapterRunnerConfig
from novel_forge.pipeline.long.context import StoryMemoryManager
from novel_forge.pipeline.long.helpers import load_previous_chapter_ending
from novel_forge.story_kernel.retriever import StoryKernelRetriever
from novel_forge.story_kernel.schemas import Relationship as KernelRelationship


class _MemoryContextStub:
    def __init__(self) -> None:
        self.narrative_evidence_service = self

    async def evidence_pack(
        self,
        *,
        purpose: str,
        query: str,
        canon_revision: str,
        max_visible_chapter: int,
        candidate_limit: int,
        evidence_token_budget: int,
    ) -> RetrievalEvidencePack:
        assert "第2章" in query
        assert max_visible_chapter == 1
        return RetrievalEvidencePack(
            pack_id="pack",
            purpose=purpose,
            query=query,
            canon_revision=canon_revision,
            max_visible_chapter=max_visible_chapter,
            evidence_cards=[
                RetrievalEvidenceCard(
                    card_id="state_1",
                    kind="accepted_state",
                    source_ref="narrative_state/state_ledger/state_1",
                    excerpt="林远在图书馆外发现异常符号",
                    chapter_number=1,
                    authority="accepted",
                )
            ][:candidate_limit],
            candidate_limit=candidate_limit,
            evidence_token_budget=evidence_token_budget,
        )


async def test_story_memory_manager_builds_state_packet(
    router,
    builder,
    tmp_storage,
    runtime_settings,
    sample_canon_state,
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "story_memory"
    layout = ProjectLayout(project_dir)
    layout.ensure_dirs()
    tmp_storage.save_text(layout.chapter_path(1), "第一章结尾。\n\n林远握紧怀表，朝图书馆走去。")
    tmp_storage.save_json(
        layout.creative_report_path(1),
        {
            "must_carry_forward": ["林远必须进入图书馆"],
            "suggestions_for_next_chapter": "直接从图书馆开场。",
        },
    )
    tmp_storage.save_json(
        layout.guard_report_path(1),
        {
            "chapter_number": 1,
            "decision": {
                "next_chapter_constraints": ["不得提前揭露守夜人的真实身份"],
            },
            "next_chapter_handoff": {
                "status": "accepted",
                "target_chapter": 2,
                "constraints": ["不得提前揭露守夜人的真实身份"],
            },
        },
    )

    sample_canon_state.chapter_exit_states[1] = ChapterExitState(
        chapter_number=1,
        time_marker="第一天，黄昏",
        location="图书馆外",
        pov="林远",
        active_goals=["进入图书馆"],
        open_questions=["老守夜人为何知道这么多"],
        must_carry_forward=["林远必须进入图书馆"],
    )
    sample_canon_state.relationships.append(KernelRelationship(
        relationship_id="林远__老守夜人",
        source_entity_id="char_林远",
        target_entity_id="char_老守夜人",
        label="引路人与被引导者",
        last_shift_chapter=1,
    ))
    sample_canon_state.plot_threads.append(PlotThreadState(
        thread_id="time_rift_truth",
        title="时间裂缝真相",
        last_touched_chapter=1,
    ))

    runner = ChapterRunner(
        router,
        builder,
        tmp_storage,
        config=ChapterRunnerConfig(),
        settings=runtime_settings,
    )
    manager = StoryMemoryManager(storage=tmp_storage, retriever=StoryKernelRetriever())
    packet = await manager.build_packet(
        layout=layout,
        canon_state=sample_canon_state,
        chapter_number=2,
        chapter_outline=ChapterOutline(
            chapter_number=2,
            title="裂缝",
            goal="让林远进入图书馆并推进裂缝真相线",
            pov_character="林远",
            setting="废弃图书馆",
            expected_word_count=2500,
        ),
        current_volume=None,
        character_bible=CharacterBible(
            characters=[
                CharacterProfile(name="林远", role="protagonist"),
                CharacterProfile(name="老守夜人", role="supporting"),
            ]
        ),
        character_profile_selector=runner._select_character_profiles,
        previous_report_compactor=runner._compact_previous_creative_report,
    )

    assert packet.chapter_number == 2
    assert packet.previous_exit_state is not None
    assert packet.previous_exit_state.location == "图书馆外"
    assert "图书馆" in packet.previous_chapter_ending
    assert [item.text for item in packet.must_carry_forward] == ["林远必须进入图书馆"]
    assert packet.active_relationships[0].pair_id == "林远__老守夜人"
    assert packet.active_plot_threads[0].thread_id == "time_rift_truth"
    assert "林远" in packet.known_characters
    assert packet.guard_constraints == ["不得提前揭露守夜人的真实身份"]
    canon_chars = packet.canon_context.get("characters", {})
    assert isinstance(canon_chars, dict)
    assert set(canon_chars) == {"林远"}
    assert canon_chars["林远"]["location"] == "雾霭小镇"
    assert "schema_version" not in canon_chars["林远"]


def test_story_memory_manager_does_not_use_unaccepted_guard_handoff() -> None:
    """A saved judge report must not constrain writing before acceptance."""

    manager = StoryMemoryManager(storage=object(), retriever=object())
    recorded, constraints = manager._extract_guard_handoff_constraints(
        {
            "next_chapter_handoff": {
                "status": "not_accepted",
                "target_chapter": 2,
                "constraints": ["不得提前揭露守夜人的真实身份"],
            }
        },
        target_chapter=2,
    )

    assert recorded is True
    assert constraints == []


def test_load_previous_chapter_ending_prefers_freshest_artifact(
    tmp_storage,
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "fresh_previous"
    layout = ProjectLayout(project_dir)
    layout.ensure_dirs()
    archived = layout.chapter_path(1)
    draft = layout.chapter_draft_path(1, 0)
    tmp_storage.save_text(archived, "旧正式章。\n\n旧结尾。")
    tmp_storage.save_text(draft, "新草稿章。\n\n新结尾。")
    os.utime(archived, (1_700_000_000, 1_700_000_000))
    os.utime(draft, (1_700_000_010, 1_700_000_010))

    ending = load_previous_chapter_ending(tmp_storage, layout, 1, min_chars=1, max_chars=200)

    assert "新结尾" in ending
    assert "旧结尾" not in ending


def test_load_previous_chapter_ending_can_use_tail_paragraph_count(
    tmp_storage,
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "previous_tail_paragraphs"
    layout = ProjectLayout(project_dir)
    layout.ensure_dirs()
    tmp_storage.save_text(layout.chapter_path(1), "第一段。\n\n第二段。\n\n第三段。\n\n第四段。")

    ending = load_previous_chapter_ending(
        tmp_storage,
        layout,
        1,
        max_chars=200,
        tail_paragraphs=2,
    )

    assert ending == "第三段。\n\n第四段。"


async def test_story_memory_manager_planning_brief_deferred_to_planning_stage(
    router,
    builder,
    tmp_storage,
    runtime_settings,
    sample_canon_state,
    tmp_path: Path,
) -> None:
    """Planning keeps semantic evidence separate from authoritative canon."""
    project_dir = tmp_path / "story_memory_brief"
    layout = ProjectLayout(project_dir)
    layout.ensure_dirs()

    sample_canon_state.chapter_exit_states[1] = ChapterExitState(
        chapter_number=1,
        time_marker="第一天，黄昏",
        location="图书馆外",
        pov="林远",
        active_goals=["进入图书馆"],
        open_questions=["守夜人到底在隐瞒什么"],
        must_carry_forward=["继续追查异常符号"],
    )

    runner = ChapterRunner(
        router,
        builder,
        tmp_storage,
        config=ChapterRunnerConfig(),
        settings=runtime_settings,
    )
    manager = StoryMemoryManager(
        storage=tmp_storage,
        retriever=StoryKernelRetriever(),
        memory_context=_MemoryContextStub(),
    )
    packet = await manager.build_packet(
        layout=layout,
        canon_state=sample_canon_state,
        chapter_number=2,
        chapter_outline=ChapterOutline(
            chapter_number=2,
            title="裂缝",
            goal="让林远进入图书馆并推进裂缝真相线",
            pov_character="林远",
            setting="废弃图书馆",
            expected_word_count=2500,
        ),
        current_volume=None,
        character_bible=CharacterBible(
            characters=[
                CharacterProfile(name="林远", role="protagonist"),
            ]
        ),
        character_profile_selector=runner._select_character_profiles,
        previous_report_compactor=runner._compact_previous_creative_report,
    )

    semantic_events = packet.canon_context.get("recent_events", [])
    assert not any("异常符号" in event["event"] for event in semantic_events)
    evidence_cards = packet.retrieval_evidence_pack["evidence_cards"]
    assert any("异常符号" in card["excerpt"] for card in evidence_cards)

    # planning_context_brief should remain a lightweight structural brief built
    # from canon/exit-state data.
    brief = packet.planning_context_brief
    assert isinstance(brief, str)
    # Open questions from exit state must appear
    assert "守夜人到底在隐瞒什么" in brief
    # Must-carry-forward constraints must appear
    assert "继续追查异常符号" in brief


async def test_story_memory_manager_uses_character_bible_gender_when_canon_conflicts(
    router,
    builder,
    tmp_storage,
    runtime_settings,
    sample_canon_state,
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "story_memory_gender"
    layout = ProjectLayout(project_dir)
    layout.ensure_dirs()

    entity = sample_canon_state.get_entity_by_name("林远")
    if entity:
        entity.attributes["gender"] = "女"

    runner = ChapterRunner(
        router,
        builder,
        tmp_storage,
        config=ChapterRunnerConfig(),
        settings=runtime_settings,
    )
    manager = StoryMemoryManager(storage=tmp_storage, retriever=StoryKernelRetriever())
    packet = await manager.build_packet(
        layout=layout,
        canon_state=sample_canon_state,
        chapter_number=2,
        chapter_outline=ChapterOutline(
            chapter_number=2,
            title="裂缝",
            goal="推进主线",
            pov_character="林远",
            setting="废弃图书馆",
            expected_word_count=2500,
        ),
        current_volume=None,
        character_bible=CharacterBible(
            characters=[
                CharacterProfile(name="林远", role="protagonist", gender="男"),
            ]
        ),
        character_profile_selector=runner._select_character_profiles,
        previous_report_compactor=runner._compact_previous_creative_report,
    )

    canon_chars = packet.canon_context.get("characters", {})
    assert canon_chars["林远"]["gender"] == "男"


async def test_story_memory_manager_injects_entity_reference_graph(
    router,
    builder,
    tmp_storage,
    runtime_settings,
    sample_canon_state,
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "story_memory_entity_graph"
    layout = ProjectLayout(project_dir)
    layout.ensure_dirs()
    tmp_storage.save_json(
        layout.root / "narrative_state" / "entity_graph.json",
        {
            "schema_version": "2.0",
            "entities": [
                {
                    "entity_id": "char_linyuan",
                    "name": "林远",
                    "entity_type": "character",
                    "aliases": [],
                },
                {
                    "entity_id": "char_gujinxiu",
                    "name": "顾锦绣",
                    "entity_type": "character",
                    "aliases": [],
                },
                {
                    "entity_id": "item_watch",
                    "name": "铜质怀表",
                    "entity_type": "item",
                    "aliases": ["怀表"],
                },
                {
                    "entity_id": "concept_rift",
                    "name": "时间裂缝",
                    "entity_type": "concept",
                    "aliases": [],
                },
            ],
            "entity_links": [
                {
                    "source_id": "alias_xiaolin",
                    "target_id": "char_linyuan",
                    "source_name": "小林",
                    "target_name": "林远",
                    "link_type": "alias_of",
                    "description": "老守夜人对林远的称呼。",
                    "confidence": 1.0,
                },
                {
                    "source_id": "char_linyuan",
                    "target_id": "char_gujinxiu",
                    "source_name": "林远",
                    "target_name": "顾锦绣",
                    "link_type": "reincarnation_of",
                    "time_layer": "modern_to_republic",
                    "description": "前世身份线索，只用于指代消歧。",
                    "confidence": 0.9,
                },
                {
                    "source_id": "char_linyuan",
                    "target_id": "item_watch",
                    "source_name": "林远",
                    "target_name": "铜质怀表",
                    "link_type": "keeper_of",
                    "description": "林远随身携带的触发物。",
                    "confidence": 0.8,
                },
                {
                    "source_id": "item_watch",
                    "target_id": "concept_rift",
                    "source_name": "铜质怀表",
                    "target_name": "时间裂缝",
                    "link_type": "symbolic_association",
                    "description": "怀表指向时间异常。",
                    "confidence": 0.7,
                },
            ],
        },
    )

    runner = ChapterRunner(
        router,
        builder,
        tmp_storage,
        config=ChapterRunnerConfig(),
        settings=runtime_settings,
    )
    manager = StoryMemoryManager(storage=tmp_storage, retriever=StoryKernelRetriever())
    packet = await manager.build_packet(
        layout=layout,
        canon_state=sample_canon_state,
        chapter_number=2,
        chapter_outline=ChapterOutline(
            chapter_number=2,
            title="裂缝",
            goal="让林远带着铜质怀表进入图书馆并推进时间裂缝真相线",
            pov_character="林远",
            setting="废弃图书馆",
            involved_characters=["林远", "老守夜人"],
            expected_word_count=2500,
        ),
        current_volume=None,
        character_bible=CharacterBible(
            characters=[
                CharacterProfile(name="林远", role="protagonist"),
                CharacterProfile(name="老守夜人", role="supporting"),
            ]
        ),
        character_profile_selector=runner._select_character_profiles,
        previous_report_compactor=runner._compact_previous_creative_report,
    )

    graph = packet.canon_context.get("entity_reference_graph")
    assert graph
    identity_links = {
        (item["source"], item["target"], item["link_type"]) for item in graph["identity_links"]
    }
    assert ("小林", "林远", "alias_of") in identity_links
    assert ("林远", "顾锦绣", "reincarnation_of") in identity_links
    context_links = {
        (item["source"], item["target"], item["link_type"]) for item in graph["context_links"]
    }
    assert ("林远", "铜质怀表", "keeper_of") in context_links
    assert ("铜质怀表", "时间裂缝", "symbolic_association") in context_links
