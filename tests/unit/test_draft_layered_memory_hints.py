"""Tests for layered memory injection in draft-stage hint collection."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.pipeline.long.stages.draft import _collect_draft_memory_hints


class _MemoryContextStub:
    def __init__(self) -> None:
        self.layered_calls: list[dict[str, object]] = []

    def get_memory_context_for_prompt(self, **_: object) -> dict[str, object]:
        return {
            "motif_context": {"forbidden_repetition": ["冷光"]},
            "critique_context": "避免重复解释设定。",
        }

    def get_layered_context(self, **kwargs: object) -> dict[str, str]:
        self.layered_calls.append(kwargs)
        return {
            "L0_identity": "标题：测试书 | 题材：科幻",
            "L1_core_memory": "上一章主角发现了异常信号。",
            "L2_on_demand": "## 近期事件\n- 第4章：发现异常信号",
            "L3_deep_search": "## 相关历史\n- 第2章：[相关度0.78] 信号首次出现",
        }

    motif_tracker = None


@pytest.mark.asyncio
async def test_collect_draft_memory_hints_includes_layered_context() -> None:
    memory_ctx = _MemoryContextStub()
    runner = SimpleNamespace(
        has_memory_context=lambda: True,
        memory_context=memory_ctx,
    )
    bundle = SimpleNamespace(
        chapter_outline=SimpleNamespace(
            goal="追查异常信号来源",
            title="异常回波",
            pov_character="林远",
            setting="旧城天台",
            main_plot_points=["定位信号来源"],
            subplot_points=[],
            involved_characters=["林远", "周岚"],
        ),
        story_bible=SimpleNamespace(
            title="测试书",
            premise="主角追查一段改变现实的异常信号。",
            tone="冷峻悬疑",
            genre="科幻",
        ),
        character_profiles=[],
    )
    plan = SimpleNamespace(emotional_arc="由怀疑转向决心")

    hints = await _collect_draft_memory_hints(
        runner,
        bundle,
        plan,
        bridge=None,
        chapter_number=5,
    )

    assert "memory_prompt_context" not in hints
    assert hints["memory_layered_context"]["L1_core_memory"] == "上一章主角发现了异常信号。"
    assert set(hints["memory_layered_context"]) == {"L1_core_memory"}
    assert memory_ctx.layered_calls
    assert "query" not in memory_ctx.layered_calls[0]
