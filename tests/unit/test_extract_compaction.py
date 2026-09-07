"""Tests for extraction compaction helpers in ChapterRunner."""

from __future__ import annotations

from novel_forge.core.schemas.chapter import ChapterOutcome
from novel_forge.core.schemas.story_state import CharacterState
from novel_forge.pipeline.chapter_runner import ChapterRunner
from novel_forge.story_kernel.retriever import KernelContext
from novel_forge.story_kernel.schemas import PromiseLedger, TimelineAnchor


def test_compact_extracted_payload_prunes_noop_and_duplicates(sample_canon_state) -> None:
    lin_entity = sample_canon_state.get_entity_by_name("林远")
    unchanged_lin = CharacterState(
        name=lin_entity.name,
        alive=lin_entity.status != "destroyed",
        location=lin_entity.attributes.get("location", ""),
        emotional_state=lin_entity.attributes.get("emotional_state", ""),
        inventory=list(lin_entity.attributes.get("inventory", [])),
        knowledge=[],
        notes=lin_entity.notes,
    )
    guard_entity = sample_canon_state.get_entity_by_name("老守夜人")
    changed_guard = CharacterState(
        name=guard_entity.name,
        alive=guard_entity.status != "destroyed",
        location="旧钟楼地下室",
        emotional_state=guard_entity.attributes.get("emotional_state", ""),
    )

    delta = ChapterOutcome(
        source_chapter=2,
        character_updates={
            "林远": unchanged_lin,
            "老守夜人": changed_guard,
        },
        new_events=[
            TimelineAnchor(
                anchor_id="evt_dup1",
                chapter=2,
                event="林远进入时间裂缝",
                characters_involved=["char_林远"],
                in_story_time="第一天傍晚",
            ),
            TimelineAnchor(
                anchor_id="evt_dup2",
                chapter=2,
                event="林远进入时间裂缝",
                characters_involved=["char_林远"],
                in_story_time="第一天傍晚",
            ),
        ],
        foreshadowing_updates=[
            PromiseLedger(
                entry_id="fs_watch",
                description="怀表停在午夜十二点",
                planted_chapter=1,
                status="planted",
            ),
            PromiseLedger(
                entry_id="fs_new",
                description="井下锁链声",
                planted_chapter=2,
                status="planted",
            ),
        ],
        new_world_facts={
            "时间裂缝入口": "废弃图书馆",
            "裂缝边界": "可见玻璃状波纹",
        },
        chapter_summary="summary",
    )

    from novel_forge.core.schemas.canon import CreativeReport, NewCharacterDetail

    report = CreativeReport(
        new_characters=[
            NewCharacterDetail(
                name="林远",
                first_appearance_chapter=2,
                role_in_story="minor",
            ),
            NewCharacterDetail(
                name="新角色甲",
                first_appearance_chapter=2,
                role_in_story="supporting",
            ),
            NewCharacterDetail(
                name="新角色甲",
                first_appearance_chapter=2,
                role_in_story="supporting",
            ),
        ]
    )

    stats = ChapterRunner._compact_extracted_payload(sample_canon_state, delta, report)

    assert "林远" not in delta.character_updates
    assert "老守夜人" in delta.character_updates
    # archived_world_facts fixture is empty, so neither new_world_fact is pruned
    assert len(delta.new_world_facts) == 2
    assert len(delta.new_events) == 1
    assert len(delta.foreshadowing_updates) == 1
    assert delta.foreshadowing_updates[0].entry_id == "fs_new"
    assert [c.name for c in report.new_characters] == ["新角色甲"]
    assert stats["dropped_noop_character_updates"] == 1
    assert stats["dropped_noop_world_facts"] == 0
    assert stats["dropped_noop_foreshadowing_updates"] == 1
    assert stats["dropped_duplicate_events"] == 1
    assert stats["dropped_known_report_characters"] == 1
    assert stats["dropped_duplicate_report_characters"] == 1


def test_build_known_characters_for_extract_uses_context_then_profiles(sample_canon_state) -> None:
    context = KernelContext(
        characters={"老守夜人": {"name": "老守夜人", "alive": True, "location": "钟楼"}},
        recent_events=[],
        active_foreshadowing=[],
        world_facts={},
        previous_chapter_summary="",
    )
    profiles = [
        {"name": "林远"},
        {"name": "新角色甲"},
        {"name": "林远"},
    ]

    known = ChapterRunner._build_known_characters_for_extract(
        sample_canon_state,
        context,
        profiles,
    )

    assert "老守夜人" in known
    assert "林远" in known
    assert "新角色甲" in known


def test_build_known_characters_for_extract_falls_back_to_canon(sample_canon_state) -> None:
    context = KernelContext()
    known = ChapterRunner._build_known_characters_for_extract(
        sample_canon_state,
        context,
        [],
    )
    entity_names = [e.name for e in sample_canon_state.entities]
    assert known == entity_names
