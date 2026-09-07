"""Unit tests for CriticAgent route-aware cache behavior."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.core.constants import TaskType
from novel_forge.memory.critic import CriticAgent


class _DummyRouter:
    def __init__(self) -> None:
        self.default_provider = "mock"
        self.task_route_overrides: dict = {}
        self._tier_to_model = {}


class _DummyBuilder:
    pass


def _canon_state() -> SimpleNamespace:
    return SimpleNamespace(
        characters={},
        timeline=[],
        foreshadowing=[],
        chapter_exit_states={},
        plot_threads={},
    )


def _outline() -> SimpleNamespace:
    return SimpleNamespace(goal="", main_plot_points=[])


def _patch_fast_checks(
    monkeypatch: pytest.MonkeyPatch,
    agent: CriticAgent,
) -> dict[str, int]:
    calls = {"character": 0, "causal": 0, "plot_threads": 0, "strengths": 0}

    async def _character(*args, **kwargs):
        calls["character"] += 1
        return []

    async def _causal(*args, **kwargs):
        calls["causal"] += 1
        return []

    async def _plot_threads(*args, **kwargs):
        calls["plot_threads"] += 1
        return []

    async def _strengths(*args, **kwargs):
        calls["strengths"] += 1
        return []

    monkeypatch.setattr(agent, "_check_character_consistency", _character)
    monkeypatch.setattr(agent, "_check_causal_chain", _causal)
    monkeypatch.setattr(agent, "_check_plot_threads", _plot_threads)
    monkeypatch.setattr(agent, "_identify_strengths", _strengths)
    return calls


@pytest.mark.asyncio
async def test_critic_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    router = _DummyRouter()
    agent = CriticAgent(
        router=router,
        builder=_DummyBuilder(),
        cache_enabled=True,
        cache_max_entries=8,
    )

    calls = {"character": 0, "causal": 0, "plot_threads": 0, "strengths": 0}

    async def _character(*args, **kwargs):
        calls["character"] += 1
        return []

    async def _causal(*args, **kwargs):
        calls["causal"] += 1
        return []

    async def _plot_threads(*args, **kwargs):
        calls["plot_threads"] += 1
        return []

    async def _strengths(*args, **kwargs):
        calls["strengths"] += 1
        return ["结构清晰"]

    monkeypatch.setattr(agent, "_check_character_consistency", _character)
    monkeypatch.setattr(agent, "_check_causal_chain", _causal)
    monkeypatch.setattr(agent, "_check_plot_threads", _plot_threads)
    monkeypatch.setattr(agent, "_identify_strengths", _strengths)

    first = await agent.critique_chapter(
        chapter_number=5,
        chapter_text="测试正文" * 500,
        canon_state=_canon_state(),
        chapter_outline=_outline(),
        check_alignment=False,
    )
    second = await agent.critique_chapter(
        chapter_number=5,
        chapter_text="测试正文" * 500,
        canon_state=_canon_state(),
        chapter_outline=_outline(),
        check_alignment=False,
    )

    assert calls == {"character": 1, "causal": 1, "plot_threads": 1, "strengths": 1}
    assert first.metadata["execution"]["cache_hit"] is False
    assert second.metadata["execution"]["cache_hit"] is True


@pytest.mark.asyncio
async def test_critic_cache_invalidates_when_route_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    router = _DummyRouter()
    agent = CriticAgent(
        router=router,
        builder=_DummyBuilder(),
        cache_enabled=True,
        cache_max_entries=8,
    )

    calls = {"character": 0, "causal": 0, "plot_threads": 0, "strengths": 0}

    async def _character(*args, **kwargs):
        calls["character"] += 1
        return []

    async def _causal(*args, **kwargs):
        calls["causal"] += 1
        return []

    async def _plot_threads(*args, **kwargs):
        calls["plot_threads"] += 1
        return []

    async def _strengths(*args, **kwargs):
        calls["strengths"] += 1
        return []

    monkeypatch.setattr(agent, "_check_character_consistency", _character)
    monkeypatch.setattr(agent, "_check_causal_chain", _causal)
    monkeypatch.setattr(agent, "_check_plot_threads", _plot_threads)
    monkeypatch.setattr(agent, "_identify_strengths", _strengths)

    await agent.critique_chapter(
        chapter_number=6,
        chapter_text="测试正文" * 400,
        canon_state=_canon_state(),
        chapter_outline=_outline(),
        check_alignment=False,
    )

    router.task_route_overrides = {
        TaskType.CRITIC_CHARACTER: SimpleNamespace(
            provider="mock-2",
            model_id="model-b",
            thinking=False,
            multi_turn=False,
        )
    }

    second = await agent.critique_chapter(
        chapter_number=6,
        chapter_text="测试正文" * 400,
        canon_state=_canon_state(),
        chapter_outline=_outline(),
        check_alignment=False,
    )

    assert calls == {"character": 2, "causal": 2, "plot_threads": 2, "strengths": 2}
    assert second.metadata["execution"]["cache_hit"] is False


@pytest.mark.asyncio
async def test_critic_cache_invalidates_when_bridge_context_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = _DummyRouter()
    agent = CriticAgent(
        router=router,
        builder=_DummyBuilder(),
        cache_enabled=True,
        cache_max_entries=8,
    )
    calls = _patch_fast_checks(monkeypatch, agent)

    await agent.critique_chapter(
        chapter_number=7,
        chapter_text="测试正文" * 400,
        canon_state=_canon_state(),
        chapter_outline=_outline(),
        check_alignment=False,
        chapter_bridge=SimpleNamespace(action_handoff="先调查旧码头"),
        previous_chapter_ending="旧码头的灯忽然熄灭。",
    )
    second = await agent.critique_chapter(
        chapter_number=7,
        chapter_text="测试正文" * 400,
        canon_state=_canon_state(),
        chapter_outline=_outline(),
        check_alignment=False,
        chapter_bridge=SimpleNamespace(action_handoff="改去钟楼寻找线索"),
        previous_chapter_ending="钟楼的钟声提前响起。",
    )

    assert calls == {"character": 2, "causal": 2, "plot_threads": 2, "strengths": 2}
    assert second.metadata["execution"]["cache_hit"] is False


@pytest.mark.asyncio
async def test_critic_cache_invalidates_when_memory_context_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = _DummyRouter()
    agent = CriticAgent(
        router=router,
        builder=_DummyBuilder(),
        cache_enabled=True,
        cache_max_entries=8,
    )
    calls = _patch_fast_checks(monkeypatch, agent)

    await agent.critique_chapter(
        chapter_number=8,
        chapter_text="测试正文" * 400,
        canon_state=_canon_state(),
        chapter_outline=_outline(),
        check_alignment=False,
        audit_context=SimpleNamespace(
            character_histories={"林远": [{"event_summary": "林远上一章仍在旧码头。"}]},
        ),
        memory_hints={"relevant_history": [{"event_summary": "旧码头的灯熄灭。"}]},
    )
    second = await agent.critique_chapter(
        chapter_number=8,
        chapter_text="测试正文" * 400,
        canon_state=_canon_state(),
        chapter_outline=_outline(),
        check_alignment=False,
        audit_context=SimpleNamespace(
            character_histories={"林远": [{"event_summary": "林远上一章已抵达钟楼。"}]},
        ),
        memory_hints={"relevant_history": [{"event_summary": "钟楼的钟声提前响起。"}]},
    )

    assert calls == {"character": 2, "causal": 2, "plot_threads": 2, "strengths": 2}
    assert second.metadata["execution"]["cache_hit"] is False
