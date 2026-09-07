"""Tests for CriticAgent timeout auto-extension behavior."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from novel_forge.core.schemas.story_state import PlotThreadState
from novel_forge.memory.critic import CriticAgent
from novel_forge.story_kernel.schemas import StoryKernel


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


@pytest.mark.asyncio
async def test_plot_thread_check_accepts_story_kernel_list_shape() -> None:
    agent = CriticAgent(
        router=_DummyRouter(),
        builder=_DummyBuilder(),
        cache_enabled=False,
    )
    kernel = StoryKernel(
        project_id="plot-thread-list",
        plot_threads=[
            PlotThreadState(
                thread_id="pt_old",
                title="旧线索",
                last_touched_chapter=1,
            )
        ],
    )

    issues = await agent._check_plot_threads(
        chapter_number=13,
        chapter_text="测试正文",
        canon_state=kernel,
    )

    assert len(issues) == 1
    assert issues[0].summary == "主线线程「旧线索」已12章未推进"
    assert issues[0].evidence == "上次提及：第1章"


@pytest.mark.asyncio
async def test_critic_timeout_extension_allows_pending_check_to_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = CriticAgent(
        router=_DummyRouter(),
        builder=_DummyBuilder(),
        check_timeout_s=0.03,
        timeout_extend_attempts=1,
        timeout_extend_multiplier=3.0,
        cache_enabled=False,
    )

    async def _character(*args, **kwargs):
        return []

    async def _slow_causal(*args, **kwargs):
        await asyncio.sleep(0.06)
        return []

    async def _plot_threads(*args, **kwargs):
        return []

    async def _strengths(*args, **kwargs):
        return ["结构清晰"]

    monkeypatch.setattr(agent, "_check_character_consistency", _character)
    monkeypatch.setattr(agent, "_check_causal_chain", _slow_causal)
    monkeypatch.setattr(agent, "_check_plot_threads", _plot_threads)
    monkeypatch.setattr(agent, "_identify_strengths", _strengths)

    report = await agent.critique_chapter(
        chapter_number=3,
        chapter_text="测试正文",
        canon_state=_canon_state(),
        chapter_outline=_outline(),
        check_alignment=False,
    )

    execution = report.metadata["execution"]
    assert execution["checks_timed_out"] == []
    assert execution["timeout_extensions_used"] == 1
    assert len(execution["timeout_windows_s"]) == 2


@pytest.mark.asyncio
async def test_critic_timeout_extension_still_reports_timeout_when_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = CriticAgent(
        router=_DummyRouter(),
        builder=_DummyBuilder(),
        check_timeout_s=0.03,
        timeout_extend_attempts=1,
        timeout_extend_multiplier=2.0,
        cache_enabled=False,
    )

    async def _character(*args, **kwargs):
        return []

    async def _very_slow_causal(*args, **kwargs):
        await asyncio.sleep(0.25)
        return []

    async def _plot_threads(*args, **kwargs):
        return []

    async def _strengths(*args, **kwargs):
        return []

    monkeypatch.setattr(agent, "_check_character_consistency", _character)
    monkeypatch.setattr(agent, "_check_causal_chain", _very_slow_causal)
    monkeypatch.setattr(agent, "_check_plot_threads", _plot_threads)
    monkeypatch.setattr(agent, "_identify_strengths", _strengths)

    report = await agent.critique_chapter(
        chapter_number=3,
        chapter_text="测试正文",
        canon_state=_canon_state(),
        chapter_outline=_outline(),
        check_alignment=False,
    )

    execution = report.metadata["execution"]
    assert "causal" in execution["checks_timed_out"]
    assert execution["timeout_extensions_used"] == 1
    assert any("已自动延长 1 次" in warning for warning in report.warnings)


@pytest.mark.asyncio
async def test_critic_incomplete_ratio_warning_when_over_half(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When >50% of checks are incomplete (timeout + failed), a WARNING log is emitted."""
    agent = CriticAgent(
        router=_DummyRouter(),
        builder=_DummyBuilder(),
        check_timeout_s=0.01,
        timeout_extend_attempts=0,
        cache_enabled=False,
    )

    async def _character(*args, **kwargs):
        return []

    async def _slow_causal(*args, **kwargs):
        await asyncio.sleep(10)
        return []

    async def _slow_plot(*args, **kwargs):
        await asyncio.sleep(10)
        return []

    async def _slow_strengths(*args, **kwargs):
        await asyncio.sleep(10)
        return []

    monkeypatch.setattr(agent, "_check_character_consistency", _character)
    monkeypatch.setattr(agent, "_check_causal_chain", _slow_causal)
    monkeypatch.setattr(agent, "_check_plot_threads", _slow_plot)
    monkeypatch.setattr(agent, "_identify_strengths", _slow_strengths)

    report = await agent.critique_chapter(
        chapter_number=3,
        chapter_text="测试正文",
        canon_state=_canon_state(),
        chapter_outline=_outline(),
        check_alignment=False,
    )

    execution = report.metadata["execution"]
    # 3 out of 4 checks timed out → incomplete_ratio = 0.75 > 0.5
    assert execution["incomplete_ratio"] == 0.75
    assert len(execution["checks_incomplete"]) == 3
    assert any("完成率过低" in warning for warning in report.warnings)


@pytest.mark.asyncio
async def test_critic_incomplete_ratio_no_warning_when_under_half(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When <=50% of checks are incomplete, no extra warning is emitted."""
    agent = CriticAgent(
        router=_DummyRouter(),
        builder=_DummyBuilder(),
        check_timeout_s=0.01,
        timeout_extend_attempts=0,
        cache_enabled=False,
    )

    async def _character(*args, **kwargs):
        return []

    async def _slow_causal(*args, **kwargs):
        await asyncio.sleep(10)
        return []

    async def _plot_threads(*args, **kwargs):
        return []

    async def _strengths(*args, **kwargs):
        return ["结构清晰"]

    monkeypatch.setattr(agent, "_check_character_consistency", _character)
    monkeypatch.setattr(agent, "_check_causal_chain", _slow_causal)
    monkeypatch.setattr(agent, "_check_plot_threads", _plot_threads)
    monkeypatch.setattr(agent, "_identify_strengths", _strengths)

    report = await agent.critique_chapter(
        chapter_number=3,
        chapter_text="测试正文",
        canon_state=_canon_state(),
        chapter_outline=_outline(),
        check_alignment=False,
    )

    execution = report.metadata["execution"]
    # 1 out of 4 checks timed out → incomplete_ratio = 0.25 <= 0.5
    assert execution["incomplete_ratio"] == 0.25
    assert all("完成率过低" not in warning for warning in report.warnings)
