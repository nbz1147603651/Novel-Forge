"""Tests for long-form polish context preservation and enrichment."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.pipeline.long.stages.draft import (
    _build_pre_polish_patch_issues,
    apply_polish_pass,
    run_post_repair_polish_layer,
)
from novel_forge.pipeline.steps.polish_step import PolishResult


class _PolishContextStub:
    def __init__(self, *, settings) -> None:
        self.router = object()
        self.builder = object()
        self.settings = settings
        self.events: list[tuple[str, object]] = []

    def on_step(self, step: str, data: object) -> None:
        self.events.append((step, data))


@pytest.mark.asyncio
async def test_apply_polish_pass_keeps_causal_report_and_warnings(
    monkeypatch: pytest.MonkeyPatch,
    runtime_settings,
) -> None:
    context = _PolishContextStub(settings=runtime_settings)

    review = SimpleNamespace(
        prepared=SimpleNamespace(
            bundle=SimpleNamespace(
                chapter_outline=SimpleNamespace(
                    chapter_number=2,
                    title="回声节点",
                    pov_character="林远",
                ),
                story_bible=SimpleNamespace(
                    tone="克制",
                    genre="mystery",
                ),
            )
        ),
        current_text="原始章节正文",
        performed_edits=2,
        outcome=SimpleNamespace(),
        alignment_report=SimpleNamespace(),
        chapter_repair_report=SimpleNamespace(
            prompt_leaks=["【情绪锚】残留"],
            factual_errors=["角色称谓不一致"],
            expression_errors=["个别句式不通顺"],
        ),
        continuity_report=SimpleNamespace(
            issues=[
                SimpleNamespace(severity="high", summary="开场承接上一章余波不足"),
            ]
        ),
        repair_plan=SimpleNamespace(),
        causal_report=SimpleNamespace(
            issues=[
                SimpleNamespace(severity="high", summary="关键事件触发链条缺失"),
            ]
        ),
        eval_report=SimpleNamespace(summary="整体可读性稳定，建议强化承接细节。"),
        warnings=["因果链校验发现 1 个高优先级问题，建议人工复核。"],
    )

    captured: dict[str, str] = {}

    async def _fake_polish_run(self, payload):  # noqa: ANN001
        captured["continuity_notes"] = payload.continuity_notes
        return PolishResult(
            polished_text="润色后正文",
            original_word_count=6,
            polished_word_count=5,
        )

    monkeypatch.setattr("novel_forge.pipeline.steps.polish_step.PolishStep.run", _fake_polish_run)

    result = await apply_polish_pass(
        context,
        review,
        trace=SimpleNamespace(),
    )

    notes = captured.get("continuity_notes", "")
    assert "跨章连贯性问题" in notes
    assert "本章校验遗留" in notes
    assert "因果链高优先级问题" in notes
    assert result.causal_report is review.causal_report
    assert result.warnings == review.warnings


@pytest.mark.asyncio
async def test_post_repair_polish_layer_uses_compact_repair_hints(
    monkeypatch: pytest.MonkeyPatch,
    runtime_settings,
) -> None:
    context = _PolishContextStub(settings=runtime_settings)

    prepared = SimpleNamespace(
        bundle=SimpleNamespace(
            chapter_outline=SimpleNamespace(
                chapter_number=3,
                title="棋亭明牌",
                pov_character="沈清漪",
                expected_word_count=1200,
            ),
            story_bible=SimpleNamespace(tone="克制", genre="权谋"),
            style_profile=None,
        )
    )
    chapter_repair_report = SimpleNamespace(
        expression_errors=["整句级重复：重复2次：我当时年纪尚轻"],
        repair_actions=["删除远处复述，保留棋亭原句。"],
        prompt_leaks=[],
        factual_errors=[],
    )
    reading_power_report = SimpleNamespace(
        suggestions=["章尾钩子可以更具体地落在姜维清的下一步动作上。"],
        next_chapter_reason="蜀国长公主不会就此罢手。",
    )

    captured: dict[str, object] = {}

    async def _no_kernel(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    async def _fake_polish_run(self, payload):  # noqa: ANN001
        captured["repair_hints"] = payload.repair_hints
        captured["continuity_notes"] = payload.continuity_notes
        captured["target_word_count"] = payload.target_word_count
        return PolishResult(
            polished_text="精修后正文",
            original_word_count=4,
            polished_word_count=4,
        )

    monkeypatch.setattr("novel_forge.pipeline.long.stages.draft.load_story_kernel_composer", _no_kernel)
    monkeypatch.setattr("novel_forge.pipeline.steps.polish_step.PolishStep.run", _fake_polish_run)

    text, changed = await run_post_repair_polish_layer(
        context,
        prepared,
        "原始章节正文",
        trace=SimpleNamespace(),
        chapter_repair_report=chapter_repair_report,
        continuity_report=SimpleNamespace(issues=[]),
        causal_report=SimpleNamespace(issues=[]),
        reading_power_report=reading_power_report,
        warnings=["当前字数 6，目标 1200，明显偏短。"],
        trigger_reason="edit_rounds_zero",
    )

    assert text == "精修后正文"
    assert changed is True
    assert captured["target_word_count"] == 1200
    assert "整句级重复" in str(captured["repair_hints"])
    assert len(str(captured["repair_hints"])) <= 950
    assert context.events[-1][0] == "polish"
    assert context.events[-1][1]["stage"] == "post_repair"


def test_pre_polish_patch_issues_only_accept_exact_repeated_quotes() -> None:
    report = SimpleNamespace(
        expression_errors=[
            "正文出现整句级重复：重复2次：我当时年纪尚轻，只觉得那位少年才俊令人钦慕",
            "说明性表达偏多，但没有唯一锚点",
        ]
    )
    text = (
        "第一段。\n\n"
        "我当时年纪尚轻，只觉得那位少年才俊令人钦慕。\n\n"
        "第三段。\n\n"
        "我当时年纪尚轻，只觉得那位少年才俊令人钦慕。"
    )

    issues = _build_pre_polish_patch_issues(text, report)

    assert len(issues) == 1
    assert issues[0].issue_type == "text_repetition"
    assert issues[0].evidence_quote == "我当时年纪尚轻，只觉得那位少年才俊令人钦慕"
    assert issues[0].paragraph_start == 2
