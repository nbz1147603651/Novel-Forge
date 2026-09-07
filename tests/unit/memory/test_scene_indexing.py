"""Tests for scene-level indexing in EpisodicMemory."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from novel_forge.memory.episodic import EpisodicMemory
from novel_forge.memory.integration import MemoryContext
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout


@dataclass
class FakeSceneIntent:
    scene_id: str
    summary: str
    purpose: str = ""
    conflict: str = ""
    required_characters: list[str] = field(default_factory=list)
    character_motivations: list = field(default_factory=list)
    location: str = ""
    time_marker: str = ""
    emotional_beat: str = ""
    relationship_dynamics: str = ""
    exit_target_state: str = ""


@dataclass
class FakePlan:
    scene_intents: list = field(default_factory=list)


@dataclass
class FakeTimelineEvent:
    chapter: int
    event: str
    characters_involved: list[str] = field(default_factory=list)
    timestamp_in_story: str = ""


@dataclass
class FakeCreativeReport:
    key_moments: list[str] = field(default_factory=list)


@dataclass
class FakeOutcome:
    chapter_summary: str = ""
    source_chapter: int = 0
    character_updates: dict = field(default_factory=dict)
    new_events: list = field(default_factory=list)
    text: str = ""
    creative_report: Any = None
    alignment_report: Any = None
    plan: Any = None


class FakeEpisodicRecorder:
    def __init__(self) -> None:
        self.rich_outcome: Any = None
        self.coarse_call: dict[str, Any] | None = None

    async def index_chapter(self, outcome: Any) -> list[str]:
        self.rich_outcome = outcome
        return ["rich-signature"]

    async def index_chapter_outcome(
        self,
        *,
        chapter_number: int,
        event_summary: str,
        full_text: str,
    ) -> list[str]:
        self.coarse_call = {
            "chapter_number": chapter_number,
            "event_summary": event_summary,
            "full_text": full_text,
        }
        return ["coarse-signature"]


@pytest.fixture
def memory():
    return EpisodicMemory(
        embedding_config={"model": "fake-embedding", "dimensions": 8},
        use_mock_embeddings=True,
    )


@pytest.mark.asyncio
async def test_index_chapter_with_scene_intents_creates_scene_entries(memory):
    """Chapter with 3 scene_intents → 4 entries (1 chapter + 3 scenes)."""
    plan = FakePlan(
        scene_intents=[
            FakeSceneIntent(
                scene_id="s1",
                summary="林远进入废弃图书馆",
                purpose="探索未知",
                required_characters=["林远"],
                location="废弃图书馆",
                time_marker="夜晚",
            ),
            FakeSceneIntent(
                scene_id="s2",
                summary="发现时间裂缝",
                conflict="与未知力量对抗",
                required_characters=["林远", "神秘人"],
                location="时间裂缝",
                emotional_beat="紧张",
            ),
            FakeSceneIntent(
                scene_id="s3",
                summary="决定穿越裂缝",
                purpose="做出关键抉择",
                required_characters=["林远"],
                exit_target_state="决心坚定",
            ),
        ]
    )

    outcome = FakeOutcome(
        chapter_summary="林远在废弃图书馆发现时间裂缝并决定穿越。",
        source_chapter=5,
        character_updates={"林远": {}},
        text="林远推开了图书馆的门……",
        plan=plan,
    )

    signatures = await memory.index_chapter(outcome)

    # 1 chapter summary + 3 scene intents = 4 entries
    assert len(signatures) == 4

    stats = memory.get_memory_stats()
    assert stats["total_episodic_entries"] == 4
    assert stats["chapter_level_entries"] == 1
    assert stats["scene_level_entries"] == 3


@pytest.mark.asyncio
async def test_finalize_chapter_memory_uses_rich_result_and_saved_plan(tmp_path):
    """Finalization should not collapse rich chapter data into one coarse entry."""
    storage = FileSystemStorage(tmp_path)
    project_id = "rich_memory"
    layout = ProjectLayout(storage.ensure_project_dir(project_id))
    layout.ensure_dirs()
    storage.save_json(
        layout.chapter_plan_path(4),
        {
            "scene_intents": [
                {
                    "scene_id": "s1",
                    "summary": "林远在钟楼复盘旧符号",
                    "required_characters": ["林远"],
                    "location": "钟楼",
                }
            ]
        },
    )

    recorder = FakeEpisodicRecorder()
    context = MemoryContext()
    context._storage = storage
    context._project_id = project_id
    context._episodic_memory = recorder

    chapter_result = FakeOutcome(
        chapter_summary="林远复盘旧符号。",
        source_chapter=4,
        new_events=[
            FakeTimelineEvent(
                chapter=4,
                event="林远确认旧符号来自钟楼。",
                characters_involved=["林远"],
            )
        ],
        text="林远站在钟楼下……",
        creative_report=FakeCreativeReport(key_moments=["钟楼旧符号被重新确认"]),
    )

    stats = await context.finalize_chapter_memory(
        chapter_number=4,
        text="林远站在钟楼下……",
        creative_report_text="林远复盘旧符号。",
        chapter_result=chapter_result,
    )

    assert stats["tasks_ok"] == ["episodic"]
    assert recorder.coarse_call is None
    assert recorder.rich_outcome is not None
    assert recorder.rich_outcome.new_events[0].event == "林远确认旧符号来自钟楼。"
    assert recorder.rich_outcome.creative_report.key_moments == ["钟楼旧符号被重新确认"]
    assert recorder.rich_outcome.plan.scene_intents[0].summary == "林远在钟楼复盘旧符号"


@pytest.mark.asyncio
async def test_delete_chapter_memory_removes_scene_entries(memory):
    """Delete chapter memory → all scene entries deleted."""
    plan = FakePlan(
        scene_intents=[
            FakeSceneIntent(scene_id="s1", summary="场景一", required_characters=["A"]),
            FakeSceneIntent(scene_id="s2", summary="场景二", required_characters=["B"]),
        ]
    )

    outcome = FakeOutcome(
        chapter_summary="章节摘要",
        source_chapter=3,
        character_updates={"A": {}},
        plan=plan,
    )

    signatures = await memory.index_chapter(outcome)
    assert len(signatures) == 3  # 1 chapter + 2 scenes

    deleted = memory.delete_chapter_memory(3)
    assert len(deleted) == 3
    assert set(deleted) == set(signatures)

    stats = memory.get_memory_stats()
    assert stats["total_episodic_entries"] == 0
    assert stats["scene_level_entries"] == 0


@pytest.mark.asyncio
async def test_search_by_semantic_retrieves_scene_entries(memory):
    """search_by_semantic() can retrieve scene-level entries."""
    plan = FakePlan(
        scene_intents=[
            FakeSceneIntent(
                scene_id="s1",
                summary="林远在雨中等待",
                required_characters=["林远"],
                emotional_beat="忧郁",
            ),
            FakeSceneIntent(
                scene_id="s2",
                summary="林远与敌人战斗",
                conflict="激烈对抗",
                required_characters=["林远", "敌人"],
                emotional_beat="紧张",
            ),
        ]
    )

    outcome = FakeOutcome(
        chapter_summary="林远经历了一场战斗。",
        source_chapter=7,
        character_updates={"林远": {}},
        plan=plan,
    )

    await memory.index_chapter(outcome)

    # Search for scene-level content
    results = await memory.search_by_semantic(
        "林远与敌人战斗",
        chapter_range=(1, 10),
        top_k=5,
        min_relevance=0.0,
    )
    assert len(results) >= 1
    # The scene entry should have scene_index > 0
    scene_results = [r for r in results if r.scene_index > 0]
    assert len(scene_results) >= 1


@pytest.mark.asyncio
async def test_search_by_semantic_scene_index_filter(memory):
    """search_by_semantic() filters by scene_index."""
    plan = FakePlan(
        scene_intents=[
            FakeSceneIntent(scene_id="s1", summary="林远在雨中等待"),
            FakeSceneIntent(scene_id="s2", summary="林远与敌人战斗"),
            FakeSceneIntent(scene_id="s3", summary="林远胜利后离开"),
        ]
    )

    outcome = FakeOutcome(
        chapter_summary="章节摘要",
        source_chapter=1,
        plan=plan,
    )

    await memory.index_chapter(outcome)

    # Filter for scene_index=2 only
    results = await memory.search_by_semantic(
        "林远",
        chapter_range=(1, 1),
        top_k=10,
        min_relevance=0.0,
        scene_index=2,
    )
    assert len(results) >= 1
    assert all(r.scene_index == 2 for r in results)

    # Filter for chapter-level only (scene_index=0)
    chapter_results = await memory.search_by_semantic(
        "章节摘要",
        chapter_range=(1, 1),
        top_k=10,
        min_relevance=0.0,
        scene_index=0,
    )
    assert len(chapter_results) >= 1
    assert all(r.scene_index == 0 for r in chapter_results)


@pytest.mark.asyncio
async def test_index_chapter_without_plan_graceful_degradation(memory):
    """Chapter without plan attribute → only chapter-level entry."""
    outcome = FakeOutcome(
        chapter_summary="没有计划的章节",
        source_chapter=10,
    )

    signatures = await memory.index_chapter(outcome)
    assert len(signatures) == 1

    stats = memory.get_memory_stats()
    assert stats["total_episodic_entries"] == 1
    assert stats["scene_level_entries"] == 0


@pytest.mark.asyncio
async def test_index_chapter_with_empty_scene_intents(memory):
    """Chapter with plan but empty scene_intents → only chapter-level entry."""
    plan = FakePlan(scene_intents=[])
    outcome = FakeOutcome(
        chapter_summary="空场景章节",
        source_chapter=11,
        plan=plan,
    )

    signatures = await memory.index_chapter(outcome)
    assert len(signatures) == 1


@pytest.mark.asyncio
async def test_scene_entry_contains_metadata(memory):
    """Scene entries contain plan metadata."""
    plan = FakePlan(
        scene_intents=[
            FakeSceneIntent(
                scene_id="s1",
                summary="关键场景",
                purpose="揭示真相",
                conflict="内心挣扎",
                location="密室",
                time_marker="午夜",
                emotional_beat="恐惧",
                exit_target_state="觉醒",
                relationship_dynamics="信任破裂",
            )
        ]
    )

    outcome = FakeOutcome(
        chapter_summary="章节摘要",
        source_chapter=1,
        plan=plan,
    )

    await memory.index_chapter(outcome)

    # Find the scene entry
    scene_entry = None
    for entry in memory._index.values():
        if entry.scene_index > 0:
            scene_entry = entry
            break

    assert scene_entry is not None
    assert scene_entry.event_summary == "关键场景"
    assert scene_entry.metadata["source"] == "plan_scene_intent"
    assert scene_entry.metadata["scene_id"] == "s1"
    assert scene_entry.metadata["purpose"] == "揭示真相"
    assert scene_entry.metadata["conflict"] == "内心挣扎"
    assert scene_entry.locations == ["密室"]
    assert scene_entry.timestamp_in_story == "午夜"
    assert scene_entry.emotional_tone == "恐惧"
