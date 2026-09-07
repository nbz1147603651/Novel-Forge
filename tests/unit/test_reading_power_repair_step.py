"""Tests for ReadingPowerRepairStep."""

from __future__ import annotations

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.schemas.reading_power_repair import (
    ReadingPowerIssue,
    ReadingPowerRepairInput,
)
from novel_forge.pipeline.steps.reading_power_repair_step import (
    ReadingPowerRepairStep,
)

ORIGINAL_TEXT = "夜幕降临，风伏京站在悬崖边缘，望着远处的灯火。她握紧了手中的密钥，心中涌起一股不安。远处的山峦在月光下显得朦胧而神秘，仿佛在诉说着什么。她深吸一口气，转身走向黑暗中的小路。风在耳边呼啸，像是在催促她加快脚步。她知道，前方等待着她的将是一场无法回避的对峙。"


def _make_step(router, builder, llm_response=""):
    """Create a ReadingPowerRepairStep with a mocked _call_with_retry."""
    settings = Settings(_env_file=None)
    step = ReadingPowerRepairStep(router, builder, settings=settings)

    async def fake_call(*_args, **_kwargs):
        return {"content": llm_response}

    step._call_with_retry = fake_call
    return step


@pytest.mark.asyncio
async def test_empty_issues_noop(router, builder) -> None:
    """Empty issues list → no-op, applied=False, failure_reason contains '无需修复'."""
    step = _make_step(router, builder)
    result = await step.run(ReadingPowerRepairInput(
        chapter_text=ORIGINAL_TEXT,
        issues=[],
        expected_hook="悬念钩子",
        expected_payoffs=["密钥的力量揭示"],
        previous_hook_description="上章结尾：追兵逼近",
    ))

    assert result.applied is False
    assert result.revised_text == ORIGINAL_TEXT
    assert "无需修复" in result.failure_reason


@pytest.mark.asyncio
async def test_hook_weakness_triggers_repair(router, builder) -> None:
    """hook_too_weak issue → applied=True, revised_text != original."""
    llm_response = (
        "夜幕降临，风伏京站在悬崖边缘，望着远处的灯火。她握紧了手中的密钥，"
        "指节因用力而泛白。密钥突然微微发烫，一道幽蓝的光芒从缝隙中渗出——"
        "那不是她熟悉的温度。远处的山峦在月光下显得朦胧而神秘，仿佛有什么"
        "东西正在苏醒。她深吸一口气，转身走向黑暗中的小路。风在耳边呼啸，"
        "像是在催促她加快脚步。她知道，前方等待着她的将是一场无法回避的对峙。"
        "而密钥的光芒，正在指引她走向那个答案。"
    )
    step = _make_step(router, builder, llm_response=llm_response)

    result = await step.run(ReadingPowerRepairInput(
        chapter_text=ORIGINAL_TEXT,
        issues=["章尾钩子力度偏弱，hook_strength is 'weak'"],
        expected_hook="悬念钩子",
        expected_payoffs=["密钥的力量揭示"],
        previous_hook_description="上章结尾：追兵逼近",
    ))

    assert result.applied is True
    assert result.revised_text != ORIGINAL_TEXT
    assert result.revised_text == llm_response


@pytest.mark.asyncio
async def test_multi_issue_repair(router, builder) -> None:
    """Multiple issues → applied=True."""
    llm_response = (
        "夜幕降临，风伏京站在悬崖边缘。密钥在她掌心发烫，幽蓝光芒如呼吸般闪烁。"
        "她想起老守夜人的话：'当密钥苏醒，时间裂缝就会打开。'远处的灯火次第熄灭，"
        "像是某种信号。山峦在月光下沉默，但她能感觉到——有什么东西正在靠近。"
        "她握紧密钥，转身走入黑暗。风在耳边呼啸，前方是无法回避的对峙。"
        "而密钥的光芒，正指向那个她一直逃避的答案。"
    )
    step = _make_step(router, builder, llm_response=llm_response)

    result = await step.run(ReadingPowerRepairInput(
        chapter_text=ORIGINAL_TEXT,
        issues=[
            "章尾钩子力度偏弱",
            "章内微兑现不足（0/1）",
            "上一章钩子没有被回应",
        ],
        expected_hook="悬念钩子",
        expected_payoffs=["密钥的力量揭示", "守夜人预言兑现"],
        previous_hook_description="上章结尾：追兵逼近",
    ))

    assert result.applied is True
    assert len(result.issues_addressed) == 3


@pytest.mark.asyncio
async def test_empty_llm_response_fallback(router, builder) -> None:
    """LLM returns empty content → applied=False, failure_reason contains '空响应'."""
    step = _make_step(router, builder, llm_response="")

    result = await step.run(ReadingPowerRepairInput(
        chapter_text=ORIGINAL_TEXT,
        issues=["章尾钩子力度偏弱"],
        expected_hook="悬念钩子",
        expected_payoffs=["密钥的力量揭示"],
        previous_hook_description="上章结尾：追兵逼近",
    ))

    assert result.applied is False
    assert result.revised_text == ORIGINAL_TEXT
    assert "空响应" in result.failure_reason


@pytest.mark.asyncio
async def test_truncated_text_rejected(router, builder) -> None:
    """LLM returns severely truncated text (<50% original) → applied=False, failure_reason contains '截断'."""
    short_response = "她走了。"
    step = _make_step(router, builder, llm_response=short_response)

    result = await step.run(ReadingPowerRepairInput(
        chapter_text=ORIGINAL_TEXT,
        issues=["章尾钩子力度偏弱"],
        expected_hook="悬念钩子",
        expected_payoffs=["密钥的力量揭示"],
        previous_hook_description="上章结尾：追兵逼近",
    ))

    assert result.applied is False
    assert result.revised_text == ORIGINAL_TEXT
    assert "截断" in result.failure_reason


@pytest.mark.asyncio
async def test_planning_terms_rejected(router, builder) -> None:
    """LLM output contains planning terms → applied=False, failure_reason contains '规划术语'."""
    planning_response = (
        "首先，需要在开头增加密钥的细节描写。其次，章尾应该加入更具体的悬念。"
        "最后，确保微兑现在中段有所体现。综上所述，建议按以上方案修改。"
    )
    step = _make_step(router, builder, llm_response=planning_response)

    result = await step.run(ReadingPowerRepairInput(
        chapter_text=ORIGINAL_TEXT,
        issues=["章尾钩子力度偏弱"],
        expected_hook="悬念钩子",
        expected_payoffs=["密钥的力量揭示"],
        previous_hook_description="上章结尾：追兵逼近",
    ))

    assert result.applied is False
    assert result.revised_text == ORIGINAL_TEXT
    assert "规划术语" in result.failure_reason


@pytest.mark.asyncio
async def test_json_wrapped_response_unwrapped(router, builder) -> None:
    """JSON wrapped response → correctly extracted."""
    json_response = '{"revised_text": "夜幕降临，风伏京站在悬崖边缘，密钥在她掌心发烫。' \
                    '幽蓝光芒如呼吸般闪烁，仿佛在回应她内心的不安。远处的灯火次第熄灭，' \
                    '山峦在月光下沉默。她握紧密钥，转身走入黑暗。风在耳边呼啸，前方是' \
                    '无法回避的对峙。密钥的光芒正指向那个她一直逃避的答案。"}'
    step = _make_step(router, builder, llm_response=json_response)

    result = await step.run(ReadingPowerRepairInput(
        chapter_text=ORIGINAL_TEXT,
        issues=["章尾钩子力度偏弱"],
        expected_hook="悬念钩子",
        expected_payoffs=["密钥的力量揭示"],
        previous_hook_description="上章结尾：追兵逼近",
    ))

    assert result.applied is True
    assert result.revised_text.startswith("夜幕降临")
    assert "密钥在她掌心发烫" in result.revised_text
    assert not result.revised_text.startswith("{")


@pytest.mark.asyncio
async def test_think_tags_stripped(router, builder) -> None:
    """think tags in response → correctly stripped."""
    think_response = (
        '<think>\n嗯，用户需要加强章尾钩子。我需要在保持原文基调的前提下，'
        '增加具体的悬念元素和微兑现。\n</think>\n\n'
        "夜幕降临，风伏京站在悬崖边缘，密钥在她掌心发烫。幽蓝光芒如呼吸般闪烁，"
        "仿佛在回应她内心的不安。远处的灯火次第熄灭，山峦在月光下沉默。"
        "她握紧密钥，转身走入黑暗。风在耳边呼啸，前方是无法回避的对峙。"
        "密钥的光芒正指向那个她一直逃避的答案。"
    )
    step = _make_step(router, builder, llm_response=think_response)

    result = await step.run(ReadingPowerRepairInput(
        chapter_text=ORIGINAL_TEXT,
        issues=["章尾钩子力度偏弱"],
        expected_hook="悬念钩子",
        expected_payoffs=["密钥的力量揭示"],
        previous_hook_description="上章结尾：追兵逼近",
    ))

    assert result.applied is True
    assert not result.revised_text.startswith("<think>")
    assert "</think>" not in result.revised_text
    assert result.revised_text.startswith("夜幕降临")


@pytest.mark.asyncio
async def test_iteration_number_stays_out_of_prompt_context(router, builder) -> None:
    """iteration is loop bookkeeping and should not inflate the prompt context."""
    captured_context = {}

    async def capturing_call(task_type, context, **_kwargs):
        captured_context.update(context)
        return {"content": ORIGINAL_TEXT + " 新增的悬念段落。"}

    settings = Settings(_env_file=None)
    step = ReadingPowerRepairStep(router, builder, settings=settings)
    step._call_with_retry = capturing_call

    result = await step.run(ReadingPowerRepairInput(
        chapter_text=ORIGINAL_TEXT,
        issues=["章尾钩子力度偏弱"],
        expected_hook="悬念钩子",
        expected_payoffs=["密钥的力量揭示"],
        previous_hook_description="上章结尾：追兵逼近",
        iteration=96,
    ))

    assert result.applied is True
    assert "iteration" not in captured_context
    assert "stage_cards" in captured_context


@pytest.mark.asyncio
async def test_dedicated_temperature_used(router, builder) -> None:
    """Reading power repair uses its dedicated temperature setting."""
    captured_kwargs = {}

    async def capturing_call(_task_type, _context, **kwargs):
        captured_kwargs.update(kwargs)
        return {"content": ORIGINAL_TEXT + " 她忽然听见密钥深处传来第二个人的呼吸声。"}

    settings = Settings(
        _env_file=None,
        temp_repair_causal=0.11,
        temp_repair_reading_power=0.47,
    )
    step = ReadingPowerRepairStep(router, builder, settings=settings)
    step._call_with_retry = capturing_call

    result = await step.run(ReadingPowerRepairInput(
        chapter_text=ORIGINAL_TEXT,
        issues=["章尾钩子力度偏弱"],
        expected_hook="悬念钩子",
        expected_payoffs=["密钥的力量揭示"],
        previous_hook_description="上章结尾：追兵逼近",
    ))

    assert result.applied is True
    assert captured_kwargs["temperature"] == pytest.approx(0.47)


def test_repair_context_supplies_typed_template_fields(router, builder) -> None:
    """The dedicated prompt gets structured issue/hook/payoff context, not just raw strings."""
    settings = Settings(_env_file=None)
    step = ReadingPowerRepairStep(router, builder, settings=settings)

    context = step.build_repair_context(ReadingPowerRepairInput(
        chapter_number=8,
        chapter_text=ORIGINAL_TEXT,
        issues=[
            ReadingPowerIssue(
                issue_type="hook_missing",
                severity="high",
                summary="章尾缺少明确钩子",
                evidence="hook_type is none",
                fix_suggestion="补强章尾悬念",
                location="章尾",
            )
        ],
        expected_hook={
            "hook_type": "mystery",
            "hook_strength": "strong",
            "hook_description": "密钥真正用途露出",
        },
        expected_payoffs=[
            {
                "payoff_type": "information",
                "description": "密钥发热证明旧传说为真",
                "strength": "medium",
            }
        ],
        previous_hook_description="追兵逼近",
        forbidden_elements=["金丝颤动", "母亲的叮嘱"],
        forbidden_elements_soft=["惨淡月光", "金丝颤动"],
        intentional_callbacks=["母亲的叮嘱"],
    ))

    assert context["chapter_number"] == 8
    assert context["issue_types"] == ["hook_missing"]
    assert context["typed_issues"]["hook_missing"][0]["location"] == "章尾"
    assert context["expected_hook"]["hook_type"] == "mystery"
    assert context["expected_payoffs"][0]["description"] == "密钥发热证明旧传说为真"
    assert context["forbidden_elements"] == ["金丝颤动"]
    assert context["forbidden_elements_soft"] == ["惨淡月光"]
    assert context["intentional_callbacks"] == ["母亲的叮嘱"]


@pytest.mark.asyncio
async def test_window_strategy_used_for_located_multi_paragraph_issue(
    router,
    builder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Located issues in multi-paragraph text should use the targeted window path."""
    text = "\n\n".join([f"第{i}段正文" for i in range(1, 8)])
    settings = Settings(_env_file=None)
    step = ReadingPowerRepairStep(router, builder, settings=settings)
    calls = {"window": 0, "fulltext": 0}

    async def _fake_window(_input, _issues, current_text, window_size=3):
        calls["window"] += 1
        return current_text + "\n\n窗口修复后的章尾钩子。"

    async def _fail_call(*_args, **_kwargs):
        calls["fulltext"] += 1
        raise AssertionError("located multi-paragraph issue should not use direct fulltext")

    monkeypatch.setattr(step, "_window_repair", _fake_window)
    step._call_with_retry = _fail_call

    result = await step.run(ReadingPowerRepairInput(
        chapter_text=text,
        issues=["第6段章尾钩子力度偏弱"],
        expected_hook="悬念钩子",
        expected_payoffs=[],
        previous_hook_description="",
    ))

    assert result.applied is True
    assert calls["window"] == 1
    assert calls["fulltext"] == 0
    assert "窗口修复后的章尾钩子" in result.revised_text
