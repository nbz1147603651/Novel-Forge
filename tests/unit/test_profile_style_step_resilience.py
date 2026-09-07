"""Resilience tests for ProfileStyleStep parallel branch failures."""

from __future__ import annotations

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.pipeline.steps.profile_style_step import (
    ProfileStyleInput,
    ProfileStyleStep,
)


def test_profile_style_contract_rejects_overflow_instead_of_silently_truncating() -> None:
    with pytest.raises(ValueError):
        ProfileStyleStep._parse_response(
            {
                "modules": [
                    {
                        "name": f"模块{index}",
                        "rules": ["可执行规则"],
                        "positive_example": "正例",
                        "negative_example": "反例",
                    }
                    for index in range(6)
                ],
                "source_elements": ["故事圣经"],
                "summary": "风格摘要",
                "global_style": {},
            }
        )


def test_profile_style_repairs_bounded_text_without_dropping_module() -> None:
    profile = ProfileStyleStep._parse_response(
        {
            "modules": [
                {
                    "name": "冷峻克制的悬疑现实主义叙事模块",
                    "rules": ["行动与可观察证据先行，禁止用连续内心独白替代现场动作链。" * 2],
                    "positive_example": "她先合上账簿，再看向门缝里没有干透的水迹。" * 3,
                    "negative_example": "海风吹过巷口，她忽然想起过去种种并感到一阵眩晕。" * 3,
                }
            ],
            "source_elements": ["故事圣经"],
            "summary": "冷峻克制，以行动、证据与人物选择承载情绪，避免解释性独白。" * 3,
            "global_style": {},
        }
    )

    module = profile.modules[0]
    assert len(module.name) <= 12
    assert len(module.rules[0]) <= 50
    assert len(module.positive_example) <= 60
    assert len(module.negative_example) <= 60
    assert len(profile.summary) <= 80
    assert module.rules[0].endswith("…")


@pytest.mark.asyncio
async def test_profile_style_step_degrades_when_structure_branch_fails(router, builder) -> None:
    settings = Settings(_env_file=None)
    step = ProfileStyleStep(router, builder, settings=settings)

    style_payload = {
        "modules": [
            {
                "name": "冷叙",
                "rules": ["动作先行"],
                "positive_example": "她收起账簿，先关灯再说话。",
                "negative_example": "她先长篇解释心情。",
            }
        ],
        "summary": "冷叙，短句。",
        "global_style": {"dialogue_ratio": "high", "pace_mode": "fast"},
    }

    async def fake_call(task_type, _context, **_kwargs):
        if task_type == TaskType.PROFILE_STYLE:
            return style_payload
        if task_type == TaskType.PROFILE_STRUCTURE:
            raise RuntimeError("structure branch timeout")
        raise AssertionError(f"unexpected task: {task_type}")

    step._call_with_retry = fake_call  # type: ignore[method-assign]

    profile = await step._execute(
        ProfileStyleInput(
            title="测试",
            genre="romance",
            tone="suspenseful",
            story_bible={},
            character_bible={},
            blueprint_elements={},
        )
    )

    assert profile.summary == "冷叙，短句。"
    assert profile.global_style.dialogue_ratio == "high"
    # structure branch fallback -> defaults remain available
    assert profile.hook_config.preferred_types == ["crisis", "mystery"]


@pytest.mark.asyncio
async def test_profile_style_step_raises_when_style_branch_fails(router, builder) -> None:
    settings = Settings(_env_file=None)
    step = ProfileStyleStep(router, builder, settings=settings)

    async def fake_call(task_type, _context, **_kwargs):
        if task_type == TaskType.PROFILE_STYLE:
            raise RuntimeError("style branch failed")
        if task_type == TaskType.PROFILE_STRUCTURE:
            return {
                "hook_config": {"preferred_types": ["mystery"]},
                "strand_config": {},
                "micro_payoff_config": {},
                "cool_point_config": {},
            }
        raise AssertionError(f"unexpected task: {task_type}")

    step._call_with_retry = fake_call  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="style branch failed"):
        await step._execute(
            ProfileStyleInput(
                title="测试",
                genre="romance",
                tone="suspenseful",
                story_bible={},
                character_bible={},
                blueprint_elements={},
            )
        )


@pytest.mark.asyncio
async def test_profile_style_step_uses_separate_temperatures(router, builder) -> None:
    settings = Settings(_env_file=None)
    settings.temp_profile_style = 0.41
    settings.temp_profile_structure = 0.12
    step = ProfileStyleStep(router, builder, settings=settings)

    captured_temps: dict[str, float] = {}

    async def fake_call(task_type, _context, **kwargs):
        captured_temps[task_type.value] = float(kwargs.get("temperature", -1))
        if task_type == TaskType.PROFILE_STYLE:
            return {
                "modules": [],
                "summary": "ok",
                "global_style": {},
            }
        if task_type == TaskType.PROFILE_STRUCTURE:
            return {
                "hook_config": {},
                "strand_config": {},
                "micro_payoff_config": {},
                "cool_point_config": {},
            }
        raise AssertionError(f"unexpected task: {task_type}")

    step._call_with_retry = fake_call  # type: ignore[method-assign]

    await step._execute(
        ProfileStyleInput(
            title="测试",
            genre="romance",
            tone="suspenseful",
            story_bible={},
            character_bible={},
            blueprint_elements={},
        )
    )

    assert captured_temps["profile_style"] == pytest.approx(0.41)
    assert captured_temps["profile_structure"] == pytest.approx(0.12)


@pytest.mark.asyncio
async def test_profile_style_step_receives_writing_style_mode_in_style_context(router, builder) -> None:
    settings = Settings(_env_file=None)
    step = ProfileStyleStep(router, builder, settings=settings)

    captured_mode: dict[str, str] = {}

    async def fake_call(task_type, context, **_kwargs):
        if task_type == TaskType.PROFILE_STYLE:
            captured_mode["value"] = str(context.get("writing_style_mode", ""))
            return {
                "modules": [],
                "summary": "ok",
                "global_style": {},
            }
        if task_type == TaskType.PROFILE_STRUCTURE:
            return {
                "hook_config": {},
                "strand_config": {},
                "micro_payoff_config": {},
                "cool_point_config": {},
            }
        raise AssertionError(f"unexpected task: {task_type}")

    step._call_with_retry = fake_call  # type: ignore[method-assign]

    await step._execute(
        ProfileStyleInput(
            title="测试",
            genre="romance",
            tone="suspenseful",
            writing_style_mode="literary",
            story_bible={},
            character_bible={},
            blueprint_elements={},
        )
    )

    assert captured_mode["value"] == "literary"


@pytest.mark.asyncio
async def test_profile_style_step_receives_narrative_complexity_in_structure_context(
    router, builder
) -> None:
    settings = Settings(_env_file=None)
    step = ProfileStyleStep(router, builder, settings=settings)

    captured_complexity: dict[str, str] = {}

    async def fake_call(task_type, context, **_kwargs):
        if task_type == TaskType.PROFILE_STYLE:
            return {
                "modules": [],
                "summary": "ok",
                "global_style": {},
            }
        if task_type == TaskType.PROFILE_STRUCTURE:
            captured_complexity["value"] = str(context.get("narrative_complexity", ""))
            return {
                "hook_config": {},
                "strand_config": {},
                "micro_payoff_config": {},
                "cool_point_config": {},
            }
        raise AssertionError(f"unexpected task: {task_type}")

    step._call_with_retry = fake_call  # type: ignore[method-assign]

    await step._execute(
        ProfileStyleInput(
            title="测试",
            genre="romance",
            tone="suspenseful",
            narrative_complexity="epic",
            story_bible={},
            character_bible={},
            blueprint_elements={},
        )
    )

    assert captured_complexity["value"] == "epic"


@pytest.mark.asyncio
async def test_profile_style_step_preserves_reported_source_elements(router, builder) -> None:
    settings = Settings(_env_file=None)
    step = ProfileStyleStep(router, builder, settings=settings)

    async def fake_call(task_type, _context, **_kwargs):
        if task_type == TaskType.PROFILE_STYLE:
            return {
                "modules": [],
                "source_elements": [f"source_{index}" for index in range(12)],
                "summary": "ok",
                "global_style": {},
            }
        if task_type == TaskType.PROFILE_STRUCTURE:
            return {
                "hook_config": {},
                "strand_config": {},
                "micro_payoff_config": {},
                "cool_point_config": {},
            }
        raise AssertionError(f"unexpected task: {task_type}")

    step._call_with_retry = fake_call  # type: ignore[method-assign]

    profile = await step._execute(
        ProfileStyleInput(
            title="测试",
            genre="romance",
            tone="suspenseful",
            story_bible={},
            character_bible={},
            blueprint_elements={},
        )
    )

    assert profile.source_elements == [f"source_{index}" for index in range(12)]
