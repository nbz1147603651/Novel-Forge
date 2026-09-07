"""Tests for chapter-level compaction and prompt-context compression."""

from __future__ import annotations

import pytest

from novel_forge.core.schemas.bible import CharacterBible, CharacterProfile
from novel_forge.core.schemas.continuity import ChapterStatePacket
from novel_forge.core.schemas.outline import ChapterOutline, StoryOutline
from novel_forge.core.schemas.story_state import (
    ChapterExitState,
    PlotThreadState,
    RelationshipState,
)
from novel_forge.pipeline.chapter_runner import ChapterRunner, ChapterRunnerConfig
from novel_forge.story_kernel.schemas import (
    Entity,
    PromiseLedger,
    StoryKernel,
    TimelineAnchor,
)


def _make_outline() -> StoryOutline:
    return StoryOutline(
        total_chapters=8,
        chapters=[
            ChapterOutline(chapter_number=1, title="c1", goal="开场"),
            ChapterOutline(chapter_number=2, title="c2", goal="推进"),
            ChapterOutline(chapter_number=3, title="c3", goal="推进"),
            ChapterOutline(chapter_number=4, title="c4", goal="推进"),
            ChapterOutline(chapter_number=5, title="c5", goal="推进"),
            ChapterOutline(
                chapter_number=6,
                title="c6",
                goal="主角与关键配角乙会合，继续主线",
                main_plot_points=["配角乙提供关键线索"],
                pov_character="主角甲",
            ),
            ChapterOutline(chapter_number=7, title="c7", goal="升级冲突"),
            ChapterOutline(chapter_number=8, title="c8", goal="收束"),
        ],
    )


def _make_bible() -> CharacterBible:
    return CharacterBible(
        characters=[
            CharacterProfile(name="主角甲", role="protagonist"),
            CharacterProfile(name="配角乙", role="supporting"),
            CharacterProfile(name="路人丙", role="minor"),
        ]
    )


def test_apply_chapter_compaction_respects_future_outline(router, builder, tmp_storage) -> None:
    from novel_forge.core.config import Settings

    config = ChapterRunnerConfig(
        chapter_compact_interval=1,
        chapter_compact_start_chapter=1,
        chapter_compact_stale_chapters=2,
        chapter_compact_outline_lookahead=3,
        chapter_compact_min_active_characters=1,
        chapter_compact_target_world_facts=2,
        chapter_compact_keep_recent_world_facts=1,
        chapter_compact_archive_resolved_foreshadowing_after=2,
    )
    runner = ChapterRunner(
        router,
        builder,
        tmp_storage,
        config=config,
        settings=Settings(_env_file=None),
    )

    state = StoryKernel(
        project_id="p",
        current_chapter=5,
        entities=[
            Entity(
                entity_id="char_主角甲",
                name="主角甲",
                entity_type="character",
                attributes={"location": "城内"},
                last_seen_chapter=5,
            ),
            Entity(
                entity_id="char_配角乙",
                name="配角乙",
                entity_type="character",
                attributes={"location": "河港"},
                last_seen_chapter=5,
            ),
            Entity(
                entity_id="char_路人丙",
                name="路人丙",
                entity_type="character",
                attributes={"location": "街市"},
                last_seen_chapter=2,
            ),
        ],
        timeline=[
            TimelineAnchor(
                anchor_id="t1", chapter=1, event="主角登场", characters_involved=["主角甲"]
            ),
            TimelineAnchor(
                anchor_id="t2", chapter=2, event="配角登场", characters_involved=["配角乙"]
            ),
            TimelineAnchor(
                anchor_id="t3", chapter=2, event="路人登场", characters_involved=["路人丙"]
            ),
        ],
        promise_ledger=[
            PromiseLedger(
                entry_id="fs_old",
                description="旧线索",
                planted_chapter=1,
                status="paid",
                payoff_chapter=2,
            )
        ],
    )

    compacted, report = runner._apply_chapter_compaction(
        state=state,
        outline=_make_outline(),
        chapter_number=5,
        character_bible=_make_bible(),
    )

    assert report is not None
    assert "路人丙" in report["archived_characters"]
    assert "路人丙" in compacted.archived_characters
    # 配角乙 is protected by future outline mentions - still in entities
    assert any(e.name == "配角乙" for e in compacted.entities)
    assert len(compacted.world_facts) <= 2
    assert all(p.entry_id != "fs_old" for p in compacted.promise_ledger)
    assert any(p.entry_id == "fs_old" for p in compacted.archived_promises)


@pytest.mark.asyncio
async def test_prompt_context_disabled_compression_preserves_sources(
    router, builder, tmp_storage
) -> None:
    from novel_forge.core.config import Settings

    config = ChapterRunnerConfig(
        context_compress_enabled=False,
        plan_prev_report_text_chars=60,
        prompt_max_profile_field_chars=80,
    )
    runner = ChapterRunner(
        router,
        builder,
        tmp_storage,
        config=config,
        settings=Settings(_env_file=None),
    )
    previous_report = {
        "plot_deviations": [
            {
                "outline_plan": "A" * 300,
                "actual_plot": "B" * 280,
                "deviation_level": "major",
                "reason": "C" * 260,
                "impact_on_future": "D" * 260,
            }
        ],
        "suggestions_for_next_chapter": "E" * 260,
        "new_characters": [{"name": "甲", "role_in_story": "minor", "description": "F" * 260}],
    }
    profiles = [
        {
            "name": "主角甲",
            "role": "protagonist",
            "appearance": "G" * 220,
            "personality": "H" * 220,
            "backstory": "I" * 220,
            "arc": "J" * 220,
            "notes": "K" * 200,
            "relationships": {"配角乙": "L" * 180},
        }
    ]

    stats = await runner._compress_prompt_context(previous_report, profiles)
    assert stats["candidates"] > 0
    assert stats["compressed"] == 0
    assert stats["direct_trim"] == 0
    assert stats["preserved_overflow"] > 0
    assert stats["hard_truncation_allowed"] is False
    dev = previous_report["plot_deviations"][0]
    assert dev["outline_plan"] == "A" * 300
    assert previous_report["suggestions_for_next_chapter"] == "E" * 260
    assert previous_report["new_characters"][0]["description"] == "F" * 260
    assert profiles[0]["appearance"] == "G" * 220
    assert profiles[0]["notes"] == "K" * 200
    assert profiles[0]["relationships"]["配角乙"] == "L" * 180


@pytest.mark.asyncio
async def test_prompt_context_generates_task_specific_briefs(router, builder, tmp_storage) -> None:
    from novel_forge.core.config import Settings

    config = ChapterRunnerConfig(
        context_compress_enabled=False,
        plan_prev_report_text_chars=60,
        prompt_max_profile_field_chars=80,
        prompt_bridge_brief_chars=140,
        prompt_planning_brief_chars=180,
        prompt_continuity_brief_chars=150,
    )
    runner = ChapterRunner(
        router,
        builder,
        tmp_storage,
        config=config,
        settings=Settings(_env_file=None),
    )
    previous_report = {
        "plot_deviations": [
            {
                "outline_plan": "原计划在图书馆会合并立刻交换情报。" * 5,
                "actual_plot": "实际剧情改为主角先被守夜人试探，再被迫延后交换线索。" * 5,
                "deviation_level": "major",
                "reason": "守夜人不信任主角，需要额外试探。" * 5,
                "impact_on_future": "后续章节必须先补足双方建立最低信任的过程。" * 5,
            }
        ],
        "suggestions_for_next_chapter": "下一章必须让主角完成进入图书馆、接触档案室并与守夜人达成最低限度合作。"
        * 3,
    }
    profiles = [
        {
            "name": "主角甲",
            "role": "protagonist",
            "appearance": "G" * 220,
            "personality": "H" * 220,
            "backstory": "I" * 220,
            "arc": "J" * 220,
            "notes": "K" * 200,
            "relationships": {"守夜人": "L" * 180},
        }
    ]
    packet = ChapterStatePacket(
        chapter_number=2,
        chapter_outline=ChapterOutline(
            chapter_number=2,
            title="裂缝",
            goal="让主角进入图书馆并推进守夜人与档案室线索",
            pov_character="主角甲",
            setting="废弃图书馆",
            expected_word_count=2600,
        ),
        canon_context={
            "characters": {},
            "recent_events": [
                {
                    "chapter": 1,
                    "event": "主角在钟楼下收到匿名钥匙并发现钥匙沾有血迹。",
                    "characters_involved": ["主角甲"],
                },
                {
                    "chapter": 1,
                    "event": "守夜人警告主角不要在午夜后靠近图书馆。",
                    "characters_involved": ["主角甲", "守夜人"],
                },
            ],
            "active_foreshadowing": [
                {"description": "匿名钥匙能开启档案室最深处的门", "status": "planted"},
            ],
        },
        previous_exit_state=ChapterExitState(
            chapter_number=1,
            time_marker="第一夜，午夜前",
            location="图书馆外",
            pov="主角甲",
            active_goals=["进入图书馆", "确认匿名钥匙的来源"],
            open_questions=["守夜人为何知道钥匙的事"],
            must_carry_forward=["主角仍握着那把带血钥匙"],
        ),
        previous_chapter_ending="主角把那把带血钥匙攥进掌心，转身朝图书馆侧门走去。" * 4,
        previous_creative_report=previous_report,
        active_relationships=[
            RelationshipState(
                pair_id="主角甲__守夜人",
                characters=["主角甲", "守夜人"],
                public_status="相互试探",
                last_shift_event="守夜人发现主角手里的钥匙后态度骤变",
                last_updated_chapter=1,
            )
        ],
        active_plot_threads=[
            PlotThreadState(
                thread_id="archive_key",
                title="带血钥匙",
                status="active",
                next_payoff_window="第2-3章",
                summary="钥匙与档案室秘密直接相关。",
                last_touched_chapter=1,
            )
        ],
        must_carry_forward=["主角仍握着那把带血钥匙", "守夜人暂未信任主角"],
        accumulated_forbidden_repetition=["指节发白", "冷风钻进袖口"],
    )

    stats = await runner._compress_prompt_context(previous_report, profiles, packet=packet)

    assert stats["briefs_generated"] == 3
    assert stats["budget_pressure"] > 0
    assert packet.bridge_context_brief
    assert packet.planning_context_brief
    assert packet.continuity_context_brief
    assert len(packet.bridge_context_brief) <= 140
    assert len(packet.planning_context_brief) <= 180
    assert len(packet.continuity_context_brief) <= 150
