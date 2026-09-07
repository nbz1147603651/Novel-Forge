"""Tests for StoryKernelRetriever context size limits."""

from __future__ import annotations

from novel_forge.core.schemas.story_state import PlotThreadState
from novel_forge.story_kernel.retriever import StoryKernelRetriever
from novel_forge.story_kernel.schemas import (
    Entity,
    PromiseLedger,
    StoryKernel,
    TimelineAnchor,
)


def _entity(name: str, **kwargs) -> Entity:
    defaults = {"entity_type": "character", "status": "active", "last_seen_chapter": 0}
    defaults.update(kwargs)
    return Entity(
        entity_id=f"char_{name}",
        name=name,
        **defaults,
    )


def test_retriever_applies_context_limits() -> None:
    entities = [
        _entity(f"角色{i}", last_seen_chapter=10 + i // 5)
        for i in range(50)
    ]
    timeline = [
        TimelineAnchor(
            anchor_id=f"evt_{i}",
            chapter=10 + i // 5,
            event=f"事件{i}",
            characters_involved=[f"char_角色{i % 20}"],
        )
        for i in range(80)
    ]
    promise_ledger = [
        PromiseLedger(
            entry_id=f"fs_{i}",
            description=f"伏笔{i}",
            planted_chapter=i + 1,
            status="planted",
        )
        for i in range(50)
    ]

    state = StoryKernel(
        project_id="p1",
        current_chapter=20,
        entities=entities,
        timeline=timeline,
        promise_ledger=promise_ledger,
    )

    retriever = StoryKernelRetriever(
        recent_chapters=5,
        max_recent_events=15,
        max_characters=12,
        max_active_foreshadowing=10,
        max_world_facts=20,
    )
    ctx = retriever.get_context(state, for_chapter=21)

    assert len(ctx.characters) <= 12
    assert len(ctx.recent_events) <= 15
    assert len(ctx.active_foreshadowing) <= 10
    assert len(ctx.world_facts) <= 20


def test_retriever_excludes_future_chapter_state_from_context() -> None:
    state = StoryKernel(
        project_id="p1",
        current_chapter=5,
        entities=[
            _entity("过去角色", last_seen_chapter=2),
            _entity("未来角色", last_seen_chapter=4),
            _entity("未来死亡者", status="destroyed", last_seen_chapter=4),
        ],
        timeline=[
            TimelineAnchor(anchor_id="e1", chapter=1, event="第一章事件"),
            TimelineAnchor(anchor_id="e2", chapter=2, event="第二章事件"),
            TimelineAnchor(anchor_id="e3", chapter=4, event="第四章事件"),
        ],
        promise_ledger=[
            PromiseLedger(
                entry_id="old",
                description="旧伏笔",
                planted_chapter=1,
                status="planted",
            ),
            PromiseLedger(
                entry_id="future",
                description="未来伏笔",
                planted_chapter=4,
                status="planted",
            ),
            PromiseLedger(
                entry_id="future_revealed",
                description="未来揭示",
                planted_chapter=1,
                status="paid",
                payoff_chapter=4,
            ),
        ],
        plot_threads=[
            PlotThreadState(thread_id="past", title="旧线", last_touched_chapter=2),
            PlotThreadState(thread_id="future", title="未来线", last_touched_chapter=4),
        ],
    )

    ctx = StoryKernelRetriever().get_context(state, for_chapter=3)

    assert [event.event for event in ctx.recent_events] == ["第一章事件", "第二章事件"]
    assert set(ctx.characters) == {"过去角色"}
    assert [item.entry_id for item in ctx.active_foreshadowing] == ["old"]
    assert [thread.thread_id for thread in ctx.active_plot_threads] == ["past"]
    assert not any("未来揭示" in fact for fact in ctx.immutable_facts)
    assert not any("未来死亡者" in fact for fact in ctx.immutable_facts)
