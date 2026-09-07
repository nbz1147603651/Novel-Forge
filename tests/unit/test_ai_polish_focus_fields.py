"""Tests for AI polish focus-field enhancement plumbing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.core.constants import TaskType
from novel_forge.desktop.ai_generate import (
    AI_CREATIVE_NOTE_FIELD,
    AI_POLISH_SUGGESTIONS_FIELD,
    _normalize_focus_fields,
    generate_config,
    polish_config,
)


class _FakeBuilder:
    def __init__(self) -> None:
        self.last_task_type = None
        self.last_context = None
        self.last_max_tokens = None
        self.last_temperature = None

    def build(self, task_type, context, max_tokens, temperature):
        self.last_task_type = task_type
        self.last_context = context
        self.last_max_tokens = max_tokens
        self.last_temperature = temperature
        return SimpleNamespace(task_type=task_type, context=context)


class _FakeRouter:
    def __init__(self, content: str | list[str]) -> None:
        self._contents = [content] if isinstance(content, str) else list(content)
        self.last_request = None
        self.requests = []

    async def route(self, request):
        self.last_request = request
        self.requests.append(request)
        content = self._contents[min(len(self.requests) - 1, len(self._contents) - 1)]
        return SimpleNamespace(content=content, total_tokens=123)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            [
                "premise",
                "title",
                "characters_hint",
                "world_hint",
                "conflict_hint",
                "pov_hint",
                "ending_style",
                "unknown",
                "premise",
            ],
            [
                "premise",
                "title",
                "characters_hint",
                "world_hint",
                "conflict_hint",
                "pov_hint",
                "ending_style",
            ],
        ),
        (
            "theme，world_hint,opening_style,extra_instructions",
            ["theme", "world_hint", "opening_style", "extra_instructions"],
        ),
        (
            {"pov_hint": True, "opening_style": False, "ending_style": 1},
            ["pov_hint", "ending_style"],
        ),
        (None, []),
    ],
)
def test_normalize_focus_fields(raw, expected) -> None:
    assert _normalize_focus_fields(raw) == expected


@pytest.mark.asyncio
async def test_generate_config_includes_generation_mode_and_creative_profile() -> None:
    builder = _FakeBuilder()
    router = _FakeRouter(
        "{"
        '"premise": "新变体前提", '
        '"characters_hint": "新人物", '
        '"world_hint": "新世界", '
        '"conflict_hint": "新冲突", '
        '"polish_suggestions": ["强化钩子", "提高冲突", "加深情感", "细化世界"], '
        '"creative_note": {'
        '"core_pitch": "旧上海雨夜里的资产谜局", '
        '"design_intent": "保留民国与关系钩子，同时提高商业悬念。", '
        '"preserved_constraints": ["民国背景"], '
        '"field_rationales": {"premise": "换成更强的关系钩子"}, '
        '"risks": ["避免金融设定过度解释"], '
        '"next_moves": ["强化前三章场景钩子"], '
        '"anti_drift_check": "所有改写均围绕原始前提展开，未引入新设定"'
        "}"
        "}"
    )
    runtime = SimpleNamespace(router=router, builder=builder)

    result = await generate_config(
        mode="long",
        user_hint="保留民国背景，换一个更强的关系钩子",
        current_config={
            "premise": "原前提",
            "genre": "romance",
            "tone": "warm",
            "total_chapters": 30,
            "words_per_chapter": 4000,
            "blueprint_element_preferences": {"preset_id": "romance"},
        },
        generation_mode="variant",
        creative_profile={
            "style": "high_concept",
            "novelty": "bold",
            "conflict": "high_pressure",
            "emotion": "intense",
            "custom_brief": "更海派、更宿命，但不要改掉民国背景",
        },
        hard_constraints={
            "genre": "悬疑言情",
            "tone": "悬疑",
            "total_chapters": 36,
            "words_per_chapter": 5200,
        },
        runtime=runtime,
    )

    assert builder.last_task_type == TaskType.GENERATE_CONFIG
    assert builder.last_context["generation_mode"] == "variant"
    assert builder.last_context["output_language"] == "zh"
    assert builder.last_context["prompt_locale"] == "zh"
    assert builder.last_context["creative_profile"]["style"] == "high_concept"
    assert "高概念强钩子" in builder.last_context["creative_profile_text"]
    assert "更海派、更宿命" in builder.last_context["creative_profile_text"]
    assert builder.last_context["hard_constraints"] == {
        "genre": "悬疑言情",
        "tone": "悬疑",
        "total_chapters": 36,
        "words_per_chapter": 5200,
    }
    assert "题材（genre）：悬疑言情" in builder.last_context["hard_constraints_text"]
    assert "每章字数（words_per_chapter）：5200" in builder.last_context["hard_constraints_text"]
    assert "故事前提（premise）" in builder.last_context["story_synopsis_impact_text"]
    assert "不是为了影响而影响" in builder.last_context["story_synopsis_impact_text"]
    assert "数字本身不必生硬写入梗概" in builder.last_context["story_synopsis_impact_text"]
    assert "创作配置字段" in builder.last_context["output_fields_text"]
    assert "故事前提（premise）：原前提" in builder.last_context["anchor_constraints_text"]
    assert "总章节数（total_chapters）：36" in builder.last_context["anchor_constraints_text"]
    assert '"premise": "原前提"' in builder.last_context["current_config_json"]
    assert result["premise"] == "新变体前提"
    assert result["genre"] == "悬疑言情"
    assert result["tone"] == "悬疑"
    assert result["total_chapters"] == 36
    assert result["words_per_chapter"] == 5200
    assert result["blueprint_element_preferences"] == {"preset_id": "romance"}
    assert result[AI_CREATIVE_NOTE_FIELD]["core_pitch"] == "旧上海雨夜里的资产谜局"
    assert result[AI_CREATIVE_NOTE_FIELD]["field_rationales"]["premise"] == "换成更强的关系钩子"


@pytest.mark.asyncio
async def test_generate_config_uses_english_prompt_pack_for_english_workflow() -> None:
    builder = _FakeBuilder()
    router = _FakeRouter('{"theme": "An English-language mystery"}')
    runtime = SimpleNamespace(router=router, builder=builder)

    result = await generate_config(
        mode="short",
        current_config={"language": "en"},
        generation_mode="fill_blanks",
        runtime=runtime,
    )

    assert builder.last_context["output_language"] == "en"
    assert builder.last_context["prompt_locale"] == "en"
    assert result["theme"] == "An English-language mystery"
    assert result["language"] == "en"


@pytest.mark.asyncio
async def test_generate_config_fill_blanks_preserves_existing_fields() -> None:
    builder = _FakeBuilder()
    router = _FakeRouter(
        "{"
        '"theme": "AI 生成主题", '
        '"characters_hint": "AI 生成人物", '
        '"world_hint": "AI 生成世界", '
        '"conflict_hint": "AI 生成冲突", '
        '"extra_instructions": "AI 生成指令"'
        "}"
    )
    runtime = SimpleNamespace(router=router, builder=builder)

    result = await generate_config(
        mode="short",
        current_config={
            "theme": "已有人类主题",
            "genre": "",
            "tone": "neutral",
            "characters_hint": "",
            "world_hint": "已有人类世界",
            "conflict_hint": "",
            "extra_instructions": "",
        },
        generation_mode="fill_blanks",
        runtime=runtime,
    )

    assert result["theme"] == "已有人类主题"
    assert result["world_hint"] == "已有人类世界"
    assert result["characters_hint"] == "AI 生成人物"
    assert result["conflict_hint"] == "AI 生成冲突"


@pytest.mark.asyncio
async def test_generate_config_uses_runtime_temperature_setting() -> None:
    builder = _FakeBuilder()
    router = _FakeRouter('{"theme": "AI 生成主题"}')
    runtime = SimpleNamespace(
        router=router,
        builder=builder,
        settings=SimpleNamespace(temp_generate_config=0.0),
    )

    await generate_config(
        mode="short",
        current_config={"theme": ""},
        generation_mode="fill_blanks",
        runtime=runtime,
    )

    assert builder.last_task_type == TaskType.GENERATE_CONFIG
    assert builder.last_temperature == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_polish_config_includes_focus_fields_in_prompt_context() -> None:
    builder = _FakeBuilder()
    router = _FakeRouter(
        "{"
        '"premise": "强化后的前提", '
        '"pov_hint": "双主角交替视角，关键场景切林枫", '
        '"opening_style": "冷开场：先抛冲突再补信息", '
        '"ending_style": "HE余韵收束，留一丝未言明的悬念", '
        '"extra_instructions": "减少解释性旁白，提升动作与感官细节", '
        '"polish_suggestions": ["强化双视角切换", "提高开场钩子密度", "结尾留余韵", "减少直白说教"], '
        '"creative_note": {'
        '"core_pitch": "双视角误判带出关系张力", '
        '"design_intent": "在不替换主设定的前提下强化开结钩子。", '
        '"preserved_constraints": ["保留原始前提"], '
        '"field_rationales": {"opening_style": "用冷开场提升入卷速度"}, '
        '"risks": ["避免结尾悬念喧宾夺主"], '
        '"next_moves": ["继续压缩旁白"], '
        '"anti_drift_check": "所有改写均围绕原始前提展开，未引入新设定"'
        "}"
        "}"
    )
    runtime = SimpleNamespace(router=router, builder=builder)

    result = await polish_config(
        mode="long",
        current_config={
            "premise": "原始前提",
            "characters_hint": "原始人物",
            "world_hint": "原始世界观",
            "conflict_hint": "原始冲突",
            "pov_hint": "原始视角。" + "保持第三人称限知，" * 30 + "尾部保留标记",
            "opening_style": "原始开篇",
            "ending_style": "原始结尾",
            "extra_instructions": "原始指令",
        },
        user_hint="重点强化开场与结尾",
        selected_suggestions=["提升冲突密度"],
        focus_fields=[
            "premise",
            "characters_hint",
            "world_hint",
            "conflict_hint",
            "opening_style",
            "ending_style",
            "bad_field",
        ],
        runtime=runtime,
    )

    assert builder.last_task_type == TaskType.POLISH_CONFIG
    first_context = router.requests[0].context
    assert first_context is not None
    assert first_context["focus_fields"] == [
        "premise",
        "characters_hint",
        "world_hint",
        "conflict_hint",
        "opening_style",
        "ending_style",
    ]
    assert "故事前提（premise）" in first_context["story_synopsis_impact_text"]
    assert "提升冲突密度" in first_context["story_synopsis_impact_text"]
    assert "重点字段会参与梗概联动" in first_context["story_synopsis_impact_text"]
    assert "才同步校准" in first_context["story_synopsis_impact_text"]
    assert "本轮共选择 6 个重点润色字段" in first_context["polish_focus_coverage_text"]
    assert "不要只改其中 4-5 个字段" in first_context["polish_focus_coverage_text"]
    assert first_context["editable_config_fields"] == first_context["focus_fields"]
    assert '"premise": "原始前提"' in first_context["current_config_json"]
    assert '"pov_hint": "原始视角' not in first_context["current_config_json"]
    assert "叙事视角（pov_hint）" in first_context["anchor_constraints_text"]
    assert "尾部保留标记" in first_context["anchor_constraints_text"]
    assert result["opening_style"].startswith("冷开场")
    assert result["ending_style"].startswith("HE余韵")
    assert AI_POLISH_SUGGESTIONS_FIELD in result
    assert result[AI_CREATIVE_NOTE_FIELD]["core_pitch"] == "双视角误判带出关系张力"
    assert (
        result[AI_CREATIVE_NOTE_FIELD]["field_rationales"]["opening_style"]
        == "用冷开场提升入卷速度"
    )


@pytest.mark.asyncio
async def test_polish_config_retries_uncovered_focus_fields() -> None:
    builder = _FakeBuilder()
    router = _FakeRouter(
        [
            "{"
            '"premise": "强化后的前提", '
            '"world_hint": "强化后的世界", '
            '"polish_suggestions": ["补足人物", "强化冲突", "细化世界", "提高钩子"]'
            "}",
            '{"characters_hint": "补足后的主角群", "conflict_hint": "补足后的三重冲突"}',
        ]
    )
    runtime = SimpleNamespace(router=router, builder=builder)

    result = await polish_config(
        mode="long",
        current_config={
            "premise": "原始前提",
            "characters_hint": "原始人物",
            "world_hint": "原始世界观",
            "conflict_hint": "原始冲突",
        },
        user_hint="全字段都增强",
        focus_fields=["premise", "characters_hint", "world_hint", "conflict_hint"],
        runtime=runtime,
    )

    assert len(router.requests) == 2
    assert result["premise"] == "强化后的前提"
    assert result["world_hint"] == "强化后的世界"
    assert result["characters_hint"] == "补足后的主角群"
    assert result["conflict_hint"] == "补足后的三重冲突"
    assert builder.last_context["focus_fields"] == ["characters_hint", "conflict_hint"]
    assert builder.last_context["editable_config_fields"] == ["characters_hint", "conflict_hint"]
    assert '"characters_hint": "原始人物"' in builder.last_context["current_config_json"]
    assert '"premise": "原始前提"' not in builder.last_context["current_config_json"]
    assert "上一轮润色没有让以下重点字段产生可感知变化" in builder.last_context["user_hint"]


@pytest.mark.asyncio
async def test_polish_config_accepts_nested_patch_payload_and_drops_uneditable_fields() -> None:
    builder = _FakeBuilder()
    router = _FakeRouter(
        "{"
        '"patch": ['
        '{"field": "world_hint", "value": "润色后的世界观"}, '
        '{"field": "title", "value": "不应被改的标题"}'
        "], "
        '"polish_suggestions": ["补足世界", "收紧冲突", "提高钩子", "保留人设"]'
        "}"
    )
    runtime = SimpleNamespace(router=router, builder=builder)

    result = await polish_config(
        mode="long",
        current_config={
            "title": "原始标题",
            "premise": "原始前提",
            "characters_hint": "原始人物",
            "world_hint": "原始世界观",
            "conflict_hint": "原始冲突",
        },
        user_hint="只强化世界观",
        focus_fields=["world_hint"],
        runtime=runtime,
    )

    assert len(router.requests) == 1
    assert result["world_hint"] == "润色后的世界观"
    assert result["title"] == "原始标题"
    assert AI_POLISH_SUGGESTIONS_FIELD in result


@pytest.mark.asyncio
async def test_polish_config_keeps_first_result_when_focus_retry_is_invalid() -> None:
    builder = _FakeBuilder()
    router = _FakeRouter(
        [
            "{"
            '"premise": "强化后的前提", '
            '"world_hint": "强化后的世界", '
            '"polish_suggestions": ["补足人物", "强化冲突", "细化世界", "提高钩子"]'
            "}",
            "这次返回了说明文字而不是 JSON",
            "仍然不是 JSON",
        ]
    )
    runtime = SimpleNamespace(router=router, builder=builder)

    result = await polish_config(
        mode="long",
        current_config={
            "premise": "原始前提",
            "characters_hint": "原始人物",
            "world_hint": "原始世界观",
            "conflict_hint": "原始冲突",
        },
        user_hint="全字段都增强",
        focus_fields=["premise", "characters_hint", "world_hint", "conflict_hint"],
        runtime=runtime,
    )

    assert len(router.requests) == 3
    assert result["premise"] == "强化后的前提"
    assert result["world_hint"] == "强化后的世界"
    assert result["characters_hint"] == "原始人物"
    assert result["conflict_hint"] == "原始冲突"


@pytest.mark.asyncio
async def test_polish_config_uses_runtime_temperature_setting() -> None:
    builder = _FakeBuilder()
    router = _FakeRouter('{"theme": "润色主题"}')
    runtime = SimpleNamespace(
        router=router,
        builder=builder,
        settings=SimpleNamespace(temp_polish_config=0.36),
    )

    await polish_config(
        mode="short",
        current_config={"theme": "原始主题"},
        runtime=runtime,
    )

    assert builder.last_task_type == TaskType.POLISH_CONFIG
    assert builder.last_temperature == pytest.approx(0.36)


@pytest.mark.asyncio
async def test_polish_config_preserves_desktop_passthrough_fields() -> None:
    builder = _FakeBuilder()
    router = _FakeRouter(
        "{"
        '"theme": "强化后的主题", '
        '"extra_instructions": "保留原设定并强化节奏", '
        '"polish_suggestions": ["强化节奏", "保留蓝图偏好", "提升人物张力", "优化开篇"]'
        "}"
    )
    runtime = SimpleNamespace(router=router, builder=builder)
    blueprint_preferences = {
        "preset_id": "romance",
        "manual_override": True,
        "items": [
            {
                "element_id": "meet_cute",
                "enabled": True,
                "locked": False,
                "weight": 78.0,
            }
        ],
    }

    result = await polish_config(
        mode="short",
        current_config={
            "theme": "原始主题",
            "genre": "romance",
            "tone": "warm",
            "length_target": 6000,
            "max_edit_rounds": 2,
            "segment_trigger_words": 6800,
            "characters_hint": "原始人物",
            "world_hint": "原始世界",
            "conflict_hint": "原始冲突",
            "pov_hint": "第三人称限知",
            "opening_style": "原始开篇",
            "ending_style": "原始结尾",
            "extra_instructions": "原始指令",
            "blueprint_element_preferences": blueprint_preferences,
        },
        user_hint="只强化文案，不动结构偏好",
        runtime=runtime,
    )

    assert result["theme"] == "强化后的主题"
    assert result["segment_trigger_words"] == 6800
    assert result["blueprint_element_preferences"] == blueprint_preferences


@pytest.mark.asyncio
async def test_polish_config_preserves_long_research_preset_fields() -> None:
    builder = _FakeBuilder()
    router = _FakeRouter('{"premise": "强化后的前提"}')
    runtime = SimpleNamespace(router=router, builder=builder)

    result = await polish_config(
        mode="long",
        current_config={
            "premise": "原始前提",
            "research_enabled": True,
            "research_provider": "brave",
            "research_query_hint": "城市更新 档案制度",
        },
        user_hint="只强化故事前提",
        runtime=runtime,
    )

    assert result["premise"] == "强化后的前提"
    assert result["research_enabled"] is True
    assert result["research_provider"] == "brave"
    assert result["research_query_hint"] == "城市更新 档案制度"


@pytest.mark.asyncio
async def test_polish_config_ignores_legacy_writing_style_output() -> None:
    builder = _FakeBuilder()
    router = _FakeRouter(
        "{"
        '"theme": "强化后的主题", '
        '"writing_style": "literary", '
        '"extra_instructions": "保留原设定并强化节奏"'
        "}"
    )
    runtime = SimpleNamespace(router=router, builder=builder)
    result = await polish_config(
        mode="short",
        current_config={
            "theme": "原始主题",
            "genre": "romance",
            "tone": "warm",
            "length_target": 6000,
            "max_edit_rounds": 2,
            "characters_hint": "原始人物",
            "world_hint": "原始世界",
            "conflict_hint": "原始冲突",
            "pov_hint": "第三人称限知",
            "opening_style": "原始开篇",
            "ending_style": "原始结尾",
            "extra_instructions": "原始指令",
        },
        user_hint="改为文学风格",
        runtime=runtime,
    )
    assert result["theme"] == "强化后的主题"
    assert result["extra_instructions"] == "保留原设定并强化节奏"
    assert "writing_style" not in result
    assert result.get("_style_profile_needs_refresh") is None


@pytest.mark.asyncio
async def test_polish_config_output_fields_exclude_legacy_writing_style() -> None:
    builder = _FakeBuilder()
    router = _FakeRouter('{"theme": "强化后的主题", "extra_instructions": "保留原设定并强化节奏"}')
    runtime = SimpleNamespace(router=router, builder=builder)
    result = await polish_config(
        mode="short",
        current_config={
            "theme": "原始主题",
            "genre": "romance",
            "tone": "warm",
            "length_target": 6000,
            "max_edit_rounds": 2,
            "characters_hint": "原始人物",
            "world_hint": "原始世界",
            "conflict_hint": "原始冲突",
            "pov_hint": "第三人称限知",
            "opening_style": "原始开篇",
            "ending_style": "原始结尾",
            "extra_instructions": "原始指令",
        },
        user_hint="不改变风格",
        runtime=runtime,
    )
    assert result["theme"] == "强化后的主题"
    assert "writing_style" not in builder.last_context["output_fields_text"]
    assert "writing_style" not in result
