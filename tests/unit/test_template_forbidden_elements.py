"""Tests for forbidden elements constraint rendering in templates."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from jinja2.ext import do  # noqa: I001


def _noop_render_style_golden_examples(*_args: object, **_kwargs: object) -> str:
    """Stand-in for novel_forge.prompts.style_golden_helpers.

    Pure-Environment tests don't have PromptRegistry globals registered,
    so we inject a no-op here. The actual helper is exercised in
    ``tests/unit/test_prompt_builder_style_golden.py``.
    """
    return ""


def _get_template_env() -> Environment:
    prompts_root = (
        Path(__file__)
        .parent.parent.parent
        / "novel_forge"
        / "prompts"
        / "prompts"
    )
    env = Environment(
        loader=FileSystemLoader(str(prompts_root)),
        autoescape=select_autoescape(["html", "xml"]),
        keep_trailing_newline=True,
        extensions=[do],
    )
    # M3.5: register the no-op helper so templates that call it can render
    # in this pure-Environment context. Real rendering goes through
    # PromptRegistry, which has the actual implementation.
    env.globals["render_style_golden_examples"] = _noop_render_style_golden_examples
    return env


def test_causal_repair_template_with_forbidden() -> None:
    env = _get_template_env()
    template = env.get_template("checking/causal_repair_typed.j2")

    context = {
        "chapter_number": 5,
        "issue_types": ["opening_causal_gap"],
        "typed_issues": {
            "opening_causal_gap": [
                {
                    "summary": "开头与上章结尾脱节",
                    "evidence": "上章结尾主角在逃亡，本章开头却在喝茶",
                    "fix_suggestion": "需要建立过渡",
                }
            ]
        },
        "previous_chapter_ending": "主角正在逃亡途中，忽然听见身后传来脚步声。",
        "causal_link": {
            "previous_event": "主角正在逃亡",
            "causal_mechanism": "脚步声意味着追兵",
            "unresolved_question": "主角能否逃脱",
        },
        "bridge": {
            "transition_mode": "direct_continue",
            "emotional_carryover": "紧张",
            "action_handoff": "继续逃跑",
        },
        "forbidden_elements": ["雷雨交加", "突然转身"],
        "forbidden_elements_soft": ["夜色深沉"],
        "intentional_callbacks": ["母亲的叮嘱"],
        "stage_cards": {
            "plan": {
                "forbidden_elements": ["雷雨交加", "突然转身"],
                "forbidden_elements_soft": ["夜色深沉"],
                "intentional_callbacks": ["母亲的叮嘱"],
            },
            "style": {
                "summary": "冷静克制",
                "modules": [{"name": "动作显压", "rules": ["用选择和代价承载紧张"]}],
                "banned_phrases": ["目光坚定"],
            },
            "editorial": {
                "character_voices": [
                    {
                        "character": "主角",
                        "sentence_profile": "短句先压事实。",
                        "emotion_syntax": "紧张时句子断开。",
                        "sample_lines": ["账不对。"],
                        "taboo_patterns": ["长篇自白"],
                    }
                ]
            },
        },
        "chapter_text": "主角继续逃跑。",
        "word_count_min": 1000,
        "word_count_max": 2000,
    }

    rendered = template.render(context)

    assert "### 🚫 禁用元素清单（硬性约束）" in rendered
    assert "以下意象/句式在近章已过度使用" in rendered
    assert "雷雨交加" in rendered
    assert "突然转身" in rendered
    assert "### ⚠️ 建议回避元素（软性约束）" in rendered
    assert "夜色深沉" in rendered
    assert "### 🔄 有意回环（允许复用）" in rendered
    assert "母亲的叮嘱" in rendered
    assert "## 风格与声纹保持（P1，不覆盖因果 ticket）" in rendered
    assert "动作显压" in rendered
    assert "账不对。" in rendered


def test_continuity_repair_template_with_function() -> None:
    env = _get_template_env()
    template = env.get_template("checking/continuity_repair.j2")

    context = {
        "chapter_number": 3,
        "chapter_text": "主角走进房间。",
        "word_count": 1500,
        "word_count_min": 1200,
        "word_count_max": 1800,
        "chapter_plan": {
            "forbidden_elements": ["金色光芒", "寒意刺骨"],
        },
        "stage_cards": {
            "plan": {
                "forbidden_elements": ["金色光芒", "寒意刺骨"],
            },
            "style": {
                "summary": "冷静克制",
                "modules": [{"name": "动作显压", "rules": ["用选择和代价承载紧张"]}],
                "banned_phrases": ["目光坚定"],
            },
            "editorial": {
                "character_voices": [
                    {
                        "character": "主角",
                        "sentence_profile": "短句先压事实。",
                        "emotion_syntax": "紧张时句子断开。",
                        "sample_lines": ["账不对。"],
                        "taboo_patterns": ["长篇自白"],
                    }
                ]
            },
        },
        "item_function": {
            "金色光芒": "表达神秘力量的出现",
            "寒意刺骨": "表达危险或恐惧的情绪",
        },
        "repair_plan": {
            "must_keep": ["主角的台词"],
            "must_change": ["环境描写"],
        },
        "continuity_report": {
            "issues": [
                {
                    "issue_type": "location_inconsistency",
                    "severity": "medium",
                    "summary": "地点描述不一致",
                    "location": "第2段",
                    "evidence": "前文说在屋内，后文写到窗外",
                }
            ]
        },
        "chapter_bridge": {
            "opening_time": "清晨",
            "opening_location": "主角家中",
            "opening_pov": "主角",
            "transition_mode": "time_skip",
            "emotional_carryover": "平静",
            "action_handoff": "无",
        },
    }

    rendered = template.render(context)

    assert "## 🚫 禁用元素清单（正文中不得出现以下任何意象/句式及其近义变体）" in rendered
    assert "金色光芒" in rendered
    assert "寒意刺骨" in rendered
    assert "表达神秘力量的出现" in rendered
    assert "表达危险或恐惧的情绪" in rendered
    assert "请确保新意象能实现同样的叙事功能" in rendered
    assert "## 风格与声纹保持（P1，不覆盖 ticket）" in rendered
    assert "动作显压" in rendered
    assert "账不对。" in rendered


def test_draft_template_with_callbacks() -> None:
    env = _get_template_env()
    template = env.get_template("writing/draft_chapter.j2")

    context = {
        "chapter_number": 7,
        "chapter_title": "重逢",
        "pov_character": "主角",
        "tone": "悬疑",
        "target_word_count": 3000,
        "genre": "玄幻",
        "plan": {
            "opening_contract": "主角进入废弃寺庙",
            "closing_contract": "发现神秘符号",
            "scene_intents": [
                {
                    "scene_id": "scene_01",
                    "summary": "主角进入寺庙",
                    "purpose": "建立氛围",
                    "conflict": "内心恐惧",
                    "required_characters": ["主角"],
                    "required_outcome": "进入成功",
                    "exit_target_state": "警觉状态",
                    "time_marker": "黄昏",
                    "location": "废弃寺庙",
                }
            ],
            "required_state_transitions": ["恐惧到勇敢"],
            "emotional_arc": "低落→高亢",
            "forbidden_elements": ["闪电", "回声"],
            "forbidden_elements_soft": ["灰尘"],
            "intentional_callbacks": ["师父的教诲", "童年的伙伴"],
        },
        "causal_link": {
            "previous_event": "主角离开村庄",
            "causal_mechanism": "追寻真相",
            "unresolved_question": "真相是什么",
            "open_threads": ["师父的下落"],
        },
        "bridge": {
            "opening_time": "黄昏",
            "opening_location": "废弃寺庙外",
            "opening_pov": "主角",
            "transition_mode": "direct_continue",
            "action_handoff": "继续前行",
            "emotional_carryover": "警惕",
        },
        "canon_context": {
            "characters": {"主角": {"gender": "男", "pronoun": "他"}},
        },
        "style_profile": None,
        "stage_cards": {
            "chapter": {
                "chapter_number": 7,
                "title": "重逢",
                "pov_character": "主角",
                "target_word_count": 3000,
            },
            "bridge": {
                "opening_time": "黄昏",
                "opening_location": "废弃寺庙外",
                "opening_pov": "主角",
                "transition_mode": "direct_continue",
                "action_handoff": "继续前行",
                "emotional_carryover": "警惕",
            },
            "plan": {
                "opening_contract": "主角进入废弃寺庙",
                "closing_contract": "发现神秘符号",
                "scene_intents": [
                    {
                        "scene_id": "scene_01",
                        "summary": "主角进入寺庙",
                        "purpose": "建立氛围",
                        "conflict": "内心恐惧",
                        "required_characters": ["主角"],
                        "required_outcome": "进入成功",
                        "exit_target_state": "警觉状态",
                        "time_marker": "黄昏",
                        "location": "废弃寺庙",
                    }
                ],
                "required_state_transitions": ["恐惧到勇敢"],
                "forbidden_elements": ["闪电", "回声"],
                "forbidden_elements_soft": ["灰尘"],
                "intentional_callbacks": ["师父的教诲", "童年的伙伴"],
            },
        },
    }

    rendered = template.render(context)

    assert "### 🔄 有意回环（允许复用）" in rendered
    assert "师父的教诲" in rendered
    assert "童年的伙伴" in rendered
    assert "不受禁用元素约束" in rendered
    assert "### 硬禁元素" in rendered
    assert "闪电" in rendered
    assert "回声" in rendered
    assert "### 跨章节已过度使用的意象（建议整个通道替换，不局限于词面）" in rendered
    assert "灰尘" in rendered


def test_all_templates_empty_forbidden() -> None:
    env = _get_template_env()

    causal_template = env.get_template("checking/causal_repair_typed.j2")
    causal_context = {
        "chapter_number": 1,
        "issue_types": ["opening_causal_gap"],
        "typed_issues": {
            "opening_causal_gap": [
                {
                    "summary": "开头脱节",
                    "evidence": "证据",
                    "fix_suggestion": "建议",
                }
            ]
        },
        "previous_chapter_ending": "上章结尾。",
        "causal_link": {
            "previous_event": "事件",
            "causal_mechanism": "机制",
            "unresolved_question": "悬念",
        },
        "bridge": {
            "transition_mode": "direct_continue",
        },
        "forbidden_elements": [],
        "forbidden_elements_soft": [],
        "intentional_callbacks": [],
        "chapter_text": "正文内容。",
        "word_count_min": 1000,
        "word_count_max": 2000,
    }
    causal_rendered = causal_template.render(causal_context)

    assert "### 🚫 禁用元素清单" not in causal_rendered
    assert "### ⚠️ 建议回避元素" not in causal_rendered
    assert "### 🔄 有意回环" not in causal_rendered

    continuity_template = env.get_template("checking/continuity_repair.j2")
    continuity_context = {
        "chapter_number": 1,
        "chapter_text": "正文。",
        "word_count": 1000,
        "word_count_min": 800,
        "word_count_max": 1200,
        "chapter_plan": {
            "forbidden_elements": [],
        },
        "item_function": {},
        "repair_plan": {
            "must_keep": [],
            "must_change": [],
        },
        "continuity_report": {
            "issues": [
                {
                    "issue_type": "test",
                    "severity": "low",
                    "summary": "测试问题",
                }
            ]
        },
        "chapter_bridge": {
            "opening_time": "白天",
            "opening_location": "室内",
            "opening_pov": "主角",
            "transition_mode": "direct_continue",
            "emotional_carryover": "平静",
            "action_handoff": "无",
        },
    }
    continuity_rendered = continuity_template.render(continuity_context)

    assert "## 🚫 禁用元素清单" not in continuity_rendered

    draft_template = env.get_template("writing/draft_chapter.j2")
    draft_context = {
        "chapter_number": 1,
        "chapter_title": "测试章节",
        "pov_character": "主角",
        "tone": "正常",
        "target_word_count": 2000,
        "plan": {
            "opening_contract": "测试",
            "closing_contract": "测试",
            "scene_intents": [
                {
                    "scene_id": "s1",
                    "summary": "测试场景",
                    "purpose": "测试",
                    "conflict": "无",
                    "required_characters": ["主角"],
                    "required_outcome": "完成",
                    "exit_target_state": "结束",
                    "time_marker": "白天",
                    "location": "室内",
                }
            ],
            "required_state_transitions": [],
            "emotional_arc": "平稳",
            "forbidden_elements": [],
            "forbidden_elements_soft": [],
            "intentional_callbacks": [],
        },
        "causal_link": None,
        "bridge": None,
        "canon_context": {
            "characters": {"主角": {"gender": "男", "pronoun": "他"}},
        },
        "style_profile": None,
    }
    draft_rendered = draft_template.render(draft_context)

    assert "### 硬禁元素" not in draft_rendered
    assert "### 跨章节已过度使用的意象" not in draft_rendered
    assert "### 🔄 有意回环" not in draft_rendered
