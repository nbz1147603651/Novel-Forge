from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.chapter import ChapterRepairReport
from novel_forge.pipeline.long.stages.prompt_leak_repair import (
    repair_confirmed_prompt_leaks_with_patch,
)


@pytest.mark.asyncio
async def test_prompt_leak_repair_prefers_patch_step() -> None:
    captured: dict[str, Any] = {}
    events: list[tuple[str, dict[str, Any]]] = []

    class _Builder:
        def build(self, task_type, context, *, max_tokens, temperature):
            captured["task_type"] = task_type
            captured["context"] = context
            return SimpleNamespace()

    class _Router:
        async def route(self, request):
            return SimpleNamespace(
                content=(
                    '{"patches":[{"original":"opening_contract：本章应承接上章。",'
                    '"replacement":"檐下雨声更急，她想起上一夜未尽的话。"}]}'
                )
            )

    text = (
        "她停在檐下。opening_contract：本章应承接上章。"
        + "后续正文继续推进。" * 80
    )
    result = await repair_confirmed_prompt_leaks_with_patch(
        router=_Router(),
        builder=_Builder(),
        settings=SimpleNamespace(
            patch_chapter_max_tokens=4096,
            patch_executor_version="v1",
            temp_patch_chapter=0.1,
        ),
        trace=SimpleNamespace(),
        chapter_number=7,
        current_text=text,
        chapter_repair_report=ChapterRepairReport(
            prompt_leaks=["opening_contract：本章应承接上章。"]
        ),
        on_step=lambda step, payload: events.append((step, payload)),
    )

    assert result.applied is True
    assert result.used_deterministic_fallback is False
    assert "opening_contract" not in result.text
    assert "檐下雨声更急" in result.text
    assert captured["task_type"] is TaskType.PATCH_CHAPTER
    assert captured["context"]["issues_with_windows"][0]["issue_type"] == "prompt_leak"
    assert result.chapter_repair_report.prompt_leaks == []
    assert any(step == "prompt_leak_patch_repair_complete" for step, _ in events)


@pytest.mark.asyncio
async def test_prompt_leak_repair_falls_back_when_patch_does_not_apply() -> None:
    class _Builder:
        def build(self, task_type, context, *, max_tokens, temperature):
            return SimpleNamespace()

    class _Router:
        async def route(self, request):
            return SimpleNamespace(content='{"patches":[]}')

    text = "她刚踏进门，正文里却混进【交接】这样的规划标记。" + "正文。" * 200
    result = await repair_confirmed_prompt_leaks_with_patch(
        router=_Router(),
        builder=_Builder(),
        settings=SimpleNamespace(
            patch_chapter_max_tokens=4096,
            patch_executor_version="v1",
            temp_patch_chapter=0.1,
        ),
        trace=SimpleNamespace(),
        chapter_number=7,
        current_text=text,
        chapter_repair_report=ChapterRepairReport(prompt_leaks=["【交接】"]),
    )

    assert result.applied is True
    assert result.used_deterministic_fallback is True
    assert "【交接】" not in result.text


@pytest.mark.asyncio
async def test_prompt_leak_repair_ignores_in_world_bracketed_title() -> None:
    class _Builder:
        def build(self, task_type, context, *, max_tokens, temperature):
            raise AssertionError("in-world text should not call patch repair")

    text = "邮件标题赫然写着：【紧急通知】关于贵司订单交付计划的调整说明。" + "正文。" * 200
    report = ChapterRepairReport(prompt_leaks=["【紧急通知】"])
    result = await repair_confirmed_prompt_leaks_with_patch(
        router=object(),
        builder=_Builder(),
        settings=SimpleNamespace(),
        trace=SimpleNamespace(),
        chapter_number=7,
        current_text=text,
        chapter_repair_report=report,
    )

    assert result.applied is False
    assert result.report_updated is True
    assert result.ignored_leaks == ("【紧急通知】",)
    assert result.text == text
    assert result.chapter_repair_report.prompt_leaks == []
