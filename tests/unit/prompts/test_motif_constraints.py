"""Check the instructions actually delivered to models, including conditional branches."""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.prompts.registry import PromptRegistry


@pytest.mark.parametrize("locale,compat", [("zh", False), ("en", False), ("zh", True)])
@pytest.mark.parametrize("priority", ["high", "medium", "low"])
def test_rendered_motif_priority_never_requires_a_response(locale, compat, priority):
    directory = Path(__file__).parents[3] / "novel_forge/prompts/prompts" if compat else None
    builder = PromptBuilder(PromptRegistry(prompts_dir=directory, locale=locale))
    prompt = builder.render(
        TaskType.DRAFT_CHAPTER,
        {
            "chapter_number": 10,
            "chapter_text": "正文",
            "output_language": "English" if locale == "en" else "Chinese",
            "stage_cards": {
                "memory": {
                    "motif_suggestions": [
                        {
                            "motif_name": "雨声",
                            "priority": priority,
                            "reason": "契约自然需要时可用",
                            "suggested_context": "",
                            "retired": False,
                        },
                        {
                            "motif_name": "退休意象测试",
                            "priority": "high",
                            "reason": "不可出现",
                            "suggested_context": "",
                            "retired": True,
                        },
                    ]
                }
            },
        },
    )
    assert "雨声" in prompt
    assert "退休意象测试" not in prompt
    assert "必须回应" not in prompt and "Must Respond" not in prompt
    assert "建议呼应" not in prompt and "Recommend Echoing" not in prompt
    assert ("可参考，可省略" if locale == "zh" else "Optional Reference") in prompt


@pytest.mark.parametrize("locale", ["zh", "en"])
def test_wave_render_does_not_force_optional_callbacks(locale):
    prompt = PromptBuilder(PromptRegistry(locale=locale)).render(
        TaskType.WAVE_CHAPTER,
        {
            "chapter_number": 2,
            "draft_text": "她走出了门。",
            "chapter_text": "她走出了门。",
            "output_language": "English" if locale == "en" else "Chinese",
            "stage_cards": {
                "plan": {
                    "cross_scene_intent": {
                        "cross_scene_references": [
                            {
                                "from_scene": "scene_01",
                                "to_scene": "scene_02",
                                "ref_type": "echo",
                                "description": "雨声",
                                "requirement": {"satisfaction": "optional"},
                            }
                        ]
                    }
                }
            },
        },
    )
    assert "80%" not in prompt
    assert "optional" in prompt


def test_cross_chapter_motif_repetition_is_softened_in_planning_prompts() -> None:
    prompts_root = (
        Path(__file__).parent.parent.parent.parent / "novel_forge" / "prompts" / "prompts"
    )
    bridge = (prompts_root / "writing" / "bridge_chapter.j2").read_text(encoding="utf-8")
    plan = (prompts_root / "planning" / "plan_chapter.j2").read_text(encoding="utf-8")

    assert "必须全部回避" not in bridge
    assert "必须完全回避" not in bridge
    assert "跨章高频修辞提示（软约束" in bridge
    assert "母题避重复提示（软约束）" in plan


@pytest.mark.parametrize("locale,compat", [("zh", False), ("en", False), ("zh", True)])
def test_evaluation_renders_functional_risks_not_word_bans(locale, compat):
    directory = Path(__file__).parents[3] / "novel_forge/prompts/prompts" if compat else None
    prompt = PromptBuilder(PromptRegistry(prompts_dir=directory, locale=locale)).render(
        TaskType.EVALUATE,
        {
            "draft_text": "她卖掉戒指换来车票。",
            "memory_forbidden_repetition": ["戒指"],
            "output_language": "English" if locale == "en" else "Chinese",
        },
    )
    assert "戒指" in prompt
    assert "高频意象禁用检查" not in prompt and "Imagery Prohibition Check" not in prompt
    assert ("仅凭频次" if locale == "zh" else "frequency or intentionality alone") in prompt


def test_wave_and_evaluate_do_not_reintroduce_motif_pressure() -> None:
    prompts_root = (
        Path(__file__).parent.parent.parent.parent / "novel_forge" / "prompts" / "prompts"
    )
    wave = (prompts_root / "writing" / "wave_chapter.j2").read_text(encoding="utf-8")
    evaluate = (prompts_root / "writing" / "evaluate_draft.j2").read_text(encoding="utf-8")

    assert "母题应埋点" not in wave
    assert "母题连续性仅作软参考，不为呼应硬补" in wave
    assert "仅作为风格连续性旁证" in evaluate
    assert "不得要求正文为完成母题而新增意象、符号或主题解释" in evaluate
    assert "完全未体现**高优先级母题，优先在 style / engagement 维度轻微扣分" not in evaluate
