"""Render-regression tests for Novel Forge prompt templates.

Ensures that each TaskType renders without exception and produces
structurally valid output (exactly one unified injected contract block).
Tests use fixture-based rendering via PromptBuilder — no exact text matching.
"""

from __future__ import annotations

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.pipeline.long.services.constraints.constraint_router import build_stage_cards
from novel_forge.prompts.builder import PromptBuilder

# ---------------------------------------------------------------------------
# Assertion helper
# ---------------------------------------------------------------------------


def assert_exactly_one_output_format_block(rendered: str) -> None:
    """Assert that *rendered* contains exactly one unified contract heading."""
    count = rendered.count("## 统一格式契约（系统注入）")
    assert count == 1, (
        f"Expected exactly one unified contract block, found {count}.\n"
        f"Rendered preview (first 500 chars):\n{rendered[:500]}"
    )


def assert_no_output_format_block(rendered: str) -> None:
    """Assert that *rendered* contains no legacy output-format heading."""
    count = rendered.count("## 输出格式")
    assert count == 0, (
        f"Expected zero '## 输出格式' blocks for text-output template, found {count}.\n"
        f"Rendered preview (first 500 chars):\n{rendered[:500]}"
    )


def assert_creative_specificity_guard(rendered: str) -> None:
    assert "定义式否定转折" in rendered
    assert "动作后果" in rendered


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def builder() -> PromptBuilder:
    return PromptBuilder()


def _canon_context() -> dict:
    """Minimal canon context shared across chapter-level fixtures."""
    return {
        "characters": {
            "林晚": {
                "alive": True,
                "location": "机库",
                "emotional_state": "警惕",
                "last_seen_chapter": 2,
            },
            "周临": {
                "alive": True,
                "location": "机库",
                "emotional_state": "冷静",
                "last_seen_chapter": 2,
            },
        },
        "recent_events": [
            {"chapter": 1, "event": "林晚首次进入机库"},
            {"chapter": 2, "event": "周临提供情报"},
        ],
        "active_foreshadowing": [
            {"description": "机库深处的异常信号"},
        ],
        "immutable_facts": ["机库位于城市地下三层"],
        "world_facts": {"社会结构": "机库由地下自治委员会管理"},
        "entity_reference_graph": {
            "identity_links": [
                {
                    "source": "小林",
                    "target": "林晚",
                    "link_type": "alias_of",
                    "description": "周临对林晚的私下称呼。",
                }
            ],
            "context_links": [
                {
                    "source": "异常信号",
                    "target": "隐藏终端",
                    "link_type": "emitted_by",
                    "description": "异常信号来自隐藏终端。",
                }
            ],
        },
    }


def _style_profile() -> dict:
    """Minimal style profile."""
    return {
        "global_style": {
            "dialogue_ratio": "balanced",
            "pace_mode": "moderate",
            "environment_ratio": "20%",
            "emotional_style": "balanced",
            "info_density": "medium",
        },
        "summary": "悬疑网文风格，推进要快，句子短促。",
        "cool_point_config": {
            "preferred_patterns": ["反常细节触发推理"],
            "density_per_chapter": "1-2处",
        },
        "modules": [
            {
                "name": "推进节奏",
                "rules": ["段末尽量留动作钩子。"],
                "positive_example": "门闩响起，她没有回头。",
                "negative_example": "她缓慢地回忆了很多往事。",
            },
        ],
    }


def _narrative_contract() -> dict:
    """Minimal narrative contract."""
    return {
        "core_question": "机库信号从何而来？",
        "resolution_target": "查明信号来源",
        "ending_strategy": "余韵式",
        "final_emotional_landing": "不安与期待交织",
    }


def _character_identity_cards() -> list[dict]:
    return [
        {"name": "林晚", "identity": "机库工程师", "role": "protagonist"},
        {"name": "周临", "identity": "情报员", "role": "deuteragonist"},
    ]


# -- PLAN_CHAPTER fixture --


def _plan_chapter_context() -> dict:
    return {
        "chapter_outline": {
            "chapter_number": 3,
            "title": "回声",
            "goal": "查明机库异常信号的来源",
            "pov_character": "林晚",
            "setting": "地下机库",
            "expected_word_count": 3000,
            "involved_characters": ["林晚", "周临"],
            "main_plot_points": ["发现信号源"],
            "subplot_points": ["林晚与周临的信任试探"],
            "beats_summary": ["进入机库", "发现异常", "追踪信号", "遭遇阻碍"],
            "time_anchor": "深夜",
            "time_gap_from_prev": "2小时",
            "countdown_state": "D-3",
            "pov_switch": False,
        },
        "canon_context": _canon_context(),
        "style_profile": _style_profile(),
        "narrative_contract": _narrative_contract(),
        "character_identity_cards": _character_identity_cards(),
        "active_relationships": [
            {
                "characters": ["林晚", "周临"],
                "public_status": "暂时合作",
                "trust": 3,
                "tension": 7,
                "last_shift_event": "周临分享了部分情报",
                "notes": "单向信任",
            },
        ],
        "chapter_bridge": {
            "opening_time": "深夜 02:00",
            "opening_location": "地下机库入口",
            "opening_pov": "林晚",
            "transition_mode": "direct_continue",
            "emotional_carryover": "紧张与好奇交织",
            "action_handoff": "林晚推开机库大门",
            "pending_questions": ["信号源是否人为？"],
            "bridge_summary": "林晚独自进入机库调查",
            "forbidden_repetition": ["金属摩擦声"],
        },
        "accumulated_forbidden_repetition": ["冰冷的金属触感"],
        "reading_power_hint": {
            "chapter_hook": "章尾揭示信号来自内部人员",
            "in_chapter_payoffs": ["林晚发现隐藏终端"],
            "prev_chapter_overall_score": 7.5,
        },
        "element_progress_hint": {
            "summary": "悬疑要素连续两章弱命中",
            "recent_missed": [],
            "recent_weak": [
                {
                    "element_id": "suspense",
                    "last_chapter": 2,
                    "count": 2,
                    "latest_reason": "悬念铺垫不足",
                },
            ],
        },
        "element_selection": {
            "required_elements": [
                {
                    "name": "悬疑",
                    "prompt_hint": "保持悬念张力",
                    "element_id": "suspense",
                    "category": "tone",
                },
            ],
            "extension_elements": [
                {
                    "name": "科技细节",
                    "prompt_hint": "融入合理的技术描写",
                    "element_id": "tech",
                    "category": "world",
                },
            ],
            "focus_constraints": ["悬疑要素须在至少2个scene中体现"],
        },
        "dynamic_element_focus": ["suspense"],
        "planning_involved_characters": ["林晚", "周临"],
        "time_context": {
            "prev_time_anchor": "深夜 00:00",
            "current_time_anchor": "深夜 02:00",
            "time_gap_from_prev": "2小时",
            "current_time_span": "2-3小时",
            "countdown_state": "D-3",
            "is_flashback": False,
        },
        "pov_hint": "有限第三人称，仅林晚视角",
        "known_issues_to_avoid": [
            {"category": "POV", "summary": "上一章偷写了周临的内心活动"},
        ],
        "chapter_state_packet": {
            "planning_context_brief": "前情：林晚已发现机库异常",
            "narrative_context": {
                "current_phase_name": "调查阶段",
                "current_phase_description": "主角开始主动调查异常信号",
                "current_phase_tension_level": "中等",
                "current_phase_time_context": "深夜",
                "current_phase_locations": ["地下机库", "控制室"],
                "current_phase_key_characters": ["林晚", "周临"],
                "current_phase_key_events": ["发现信号", "进入机库"],
                "active_subplot_names": ["信任线"],
                "active_subplot_weave_hints": ["注意林晚对周临的戒备"],
                "subplot_dependency_warnings": [],
                "is_turning_point": False,
                "next_turning_point_chapter": 5,
                "next_turning_point_description": "信号源身份揭晓",
            },
            "world_setting_brief": "近未来城市，地下设施发达",
        },
        "memory_hints": {
            "relevant_history": [
                {"chapter_number": 1, "event_summary": "首次发现异常信号", "relevance_score": 0.85},
            ],
            "outline_context": {
                "chapter_summary": "调查阶段核心章节",
                "unresolved_questions": ["信号源是谁？"],
                "relationship_changes": ["林晚开始怀疑周临"],
            },
        },
        "must_carry_forward": ["异常信号线索"],
        "previous_creative_report": {
            "suggestions_for_next_chapter": "增加技术细节描写",
            "plot_deviations": [],
        },
        "strand_hint": {
            "strand_distribution": {"quest": 5, "fire": 3, "constellation": 2},
            "strand_alerts": [
                {"message": "Fire线连续3章未推进", "suggestion": "安排冲突场景"},
            ],
        },
    }


# -- DRAFT_CHAPTER fixture --


def _draft_chapter_context() -> dict:
    return {
        "chapter_number": 3,
        "chapter_title": "回声",
        "pov_character": "林晚",
        "tone": "冷峻",
        "target_word_count": 3000,
        "genre": "科幻悬疑",
        "chapter_goal": "查明机库异常信号的来源",
        "plan": {
            "scene_intents": [
                {
                    "scene_id": "scene_01",
                    "summary": "林晚进入机库，发现异常信号",
                    "purpose": "建立场景",
                    "conflict": "信号来源不明",
                    "required_characters": ["林晚"],
                    "character_motivations": [
                        {
                            "character": "林晚",
                            "motivation": "确认信号是否来自内部人员",
                            "stake": "若判断失误会暴露调查行动",
                        }
                    ],
                    "entry_state_refs": ["上一章留下的不信任"],
                    "required_outcome": "确认信号存在",
                    "exit_target_state": "警觉状态",
                    "location": "地下机库",
                    "time_marker": "深夜 02:00",
                    "relationship_dynamics": "林晚独自行动",
                    "emotional_beat": "紧张",
                    "sensory_notes": "金属冷光、低频嗡鸣",
                    "target_words": 600,
                },
                {
                    "scene_id": "scene_02",
                    "summary": "追踪信号至隐藏终端",
                    "purpose": "推进调查",
                    "conflict": "终端加密",
                    "required_characters": ["林晚"],
                    "entry_state_refs": ["确认信号存在"],
                    "required_outcome": "破解第一层加密",
                    "exit_target_state": "发现内部人员痕迹",
                    "location": "控制室",
                    "time_marker": "深夜 02:30",
                    "relationship_dynamics": "",
                    "emotional_beat": "专注到震惊",
                    "sensory_notes": "屏幕蓝光、键盘敲击声",
                    "target_words": 800,
                },
            ],
            "opening_contract": "以林晚推开机库大门的动作开场",
            "closing_contract": "以发现内部人员访问记录收尾",
            "required_state_transitions": ["从怀疑到确认内部人员介入"],
            "emotional_arc": "紧张→专注→震惊",
            "relationship_evolution": ["林晚对周临的戒备加深"],
            "forbidden_elements": ["冰冷的金属触感"],
            "forbidden_elements_soft": ["金属摩擦声"],
            "intentional_callbacks": ["异常信号的嗡鸣声"],
            "chapter_type": "discovery",
            "foreshadowing_plan": ["暗示信号与更大阴谋相关"],
            "key_revelations": ["信号来自内部终端"],
        },
        "canon_context": _canon_context(),
        "style_profile": _style_profile(),
        "narrative_contract": _narrative_contract(),
        "narrative_context": {
            "active_subplot_names": ["信任线"],
            "active_subplot_weave_hints": ["信任线在本章通过隐藏终端线索反哺主线调查"],
            "subplot_dependency_warnings": ["信任线第3章依赖密信线第5章，尚未发生"],
        },
        "character_identity_cards": _character_identity_cards(),
        "bridge": {
            "opening_time": "深夜 02:00",
            "opening_location": "地下机库入口",
            "opening_pov": "林晚",
            "transition_mode": "direct_continue",
            "emotional_carryover": "紧张与好奇交织",
            "action_handoff": "林晚推开机库大门",
            "pending_questions": ["信号源是否人为？"],
            "sensory_anchors": ["金属冷光"],
        },
        "reading_power_hint": {
            "chapter_hook": "章尾揭示信号来自内部人员",
            "in_chapter_payoffs": ["林晚发现隐藏终端"],
            "prev_chapter_overall_score": 7.5,
        },
        "causal_link": {
            "previous_event": "林晚首次发现异常信号",
            "causal_mechanism": "信号引导林晚深入调查",
            "unresolved_question": "信号源是谁？",
            "open_threads": ["信号来源", "周临的真实立场"],
        },
        "memory_previous_chapter_events": [
            {"chapter_number": 2, "event_summary": "周临提供部分情报"},
        ],
        "memory_relevant_history": [],
        "memory_prompt_context": {
            "summary_context": "林晚已对周临产生怀疑",
            "motif_context": {"forbidden_repetition": ["冰冷的金属"]},
        },
        "memory_motif_suggestions": [],
        "critique_context": "",
        "known_issues_to_avoid": [],
        "pov_switch": False,
        "address_rules": "角色身份与场景上下文",
        "weak_senses": ["味觉"],
        "element_selection": {
            "extension_elements": [
                {
                    "name": "悬疑",
                    "prompt_hint": "保持悬念张力",
                    "element_id": "suspense",
                    "category": "tone",
                },
            ],
            "focus_constraints": ["悬疑要素须在正文中明确体现"],
        },
        "element_focus": ["suspense"],
        "subplot_weave_guidance": "注意信任线的推进",
    }


# -- EDIT_CHAPTER fixture --


def _edit_chapter_context() -> dict:
    return {
        "chapter_number": 3,
        "tone": "冷峻",
        "iteration": 1,
        "target_word_count": 3000,
        "chapter_goal": "查明机库异常信号的来源",
        "main_plot_points": ["发现信号源", "确认内部人员介入"],
        "subplot_points": ["林晚与周临的信任试探"],
        "canon_context": _canon_context(),
        "style_profile": _style_profile(),
        "plan": _draft_chapter_context()["plan"],
        "draft_text": (
            "林晚推开机库大门，金属冷光映在她的脸上。\n"
            "低频的嗡鸣声从深处传来。她握紧手电筒，一步步向前走去。\n"
            "控制室的屏幕还亮着，上面显示着一串陌生的访问记录。\n"
            "她的心跳加速——这是内部人员留下的痕迹。"
        ),
        "chapter_repair_report": {
            "factual_errors": ["上一章将机库位置写错为地上"],
        },
        "alignment_report": {
            "missing_main_points": [],
        },
        "causal_fix_instructions": [],
        "pronouns_fix_instruction": "",
        "word_count_warning": "",
        "opening_risk_note": "",
        "style": "webnovel",
        "chapter_type": "discovery",
        "reading_power_hint": {
            "chapter_hook": "章尾揭示信号来自内部人员",
            "in_chapter_payoffs": ["林晚发现隐藏终端"],
            "prev_chapter_overall_score": 7.5,
        },
        "reading_power_repair_focus": [],
        "element_selection": {
            "extension_elements": [
                {
                    "name": "悬疑",
                    "prompt_hint": "保持悬念张力",
                    "element_id": "suspense",
                    "category": "tone",
                },
            ],
            "focus_constraints": ["悬疑要素须在正文中明确体现"],
        },
        "element_focus": ["suspense"],
        "character_identity_cards": _character_identity_cards(),
    }


# -- DRAFT (short) fixture --


def _draft_short_context() -> dict:
    return {
        "spec": {
            "genre": "悬疑",
            "theme": "一个关于真相与谎言的故事",
            "tone": "suspenseful",
            "length_target": 5000,
            "language": "zh",
            "characters_hint": "一位侦探和一位嫌疑人",
            "world_hint": "现代都市",
            "conflict_hint": "真相与谎言的博弈",
            "pov_hint": "有限第三人称",
            "opening_style": "异象开篇",
            "ending_style": "余韵式收束",
            "extra_instructions": "",
        },
        "beats": {
            "beats": [
                {
                    "sequence": 1,
                    "beat_type": "opening",
                    "summary": "侦探接到神秘电话",
                    "tension_level": 3,
                    "characters_involved": ["侦探"],
                    "setting": "侦探事务所",
                    "emotional_note": "好奇",
                },
                {
                    "sequence": 2,
                    "beat_type": "catalyst",
                    "summary": "电话那头的人声称知道一桩旧案的真相",
                    "tension_level": 5,
                    "characters_involved": ["侦探", "神秘人"],
                    "setting": "电话通话",
                    "emotional_note": "紧张",
                },
                {
                    "sequence": 3,
                    "beat_type": "midpoint",
                    "summary": "侦探发现线索指向自己信任的人",
                    "tension_level": 8,
                    "characters_involved": ["侦探"],
                    "setting": "档案室",
                    "emotional_note": "震惊",
                },
                {
                    "sequence": 4,
                    "beat_type": "finale",
                    "summary": "侦探面对真相做出选择",
                    "tension_level": 9,
                    "characters_involved": ["侦探", "嫌疑人"],
                    "setting": "审讯室",
                    "emotional_note": "决断",
                },
            ],
            "structure_note": "四幕结构",
        },
        "style_profile": _style_profile(),
        "blueprint": {
            "synopsis": "侦探追查一桩旧案，发现真相出人意料",
            "emotional_arc": "好奇→紧张→震惊→决断",
            "ending_strategy": "余韵式",
            "anchor_elements": {
                "time_frame": "三天内",
                "primary_locations": ["侦探事务所", "档案室", "审讯室"],
                "core_characters": [
                    {"name": "侦探", "role": "protagonist"},
                    {"name": "嫌疑人", "role": "antagonist"},
                ],
                "central_event": "旧案真相揭晓",
            },
            "turning_points": [
                {"description": "发现线索指向信任的人", "position_percent": 50},
            ],
            "element_selection": {
                "extension_elements": [
                    {
                        "name": "悬疑",
                        "prompt_hint": "保持悬念张力",
                        "element_id": "suspense",
                        "category": "tone",
                    },
                ],
            },
        },
        "execution_plan": {
            "opening_contract": "以神秘电话开场",
            "ending_contract": "以侦探的选择收尾",
            "anchor_guardrails": {
                "time_frame": "三天内",
                "locations": ["侦探事务所", "档案室", "审讯室"],
                "characters": ["侦探", "嫌疑人"],
                "central_event": "旧案真相揭晓",
            },
            "beat_execution_plan": [
                {
                    "sequence": 1,
                    "word_budget": 1000,
                    "phase_name": "开端",
                    "phase_goal": "建立悬念",
                },
                {
                    "sequence": 2,
                    "word_budget": 1200,
                    "phase_name": "发展",
                    "phase_goal": "推进调查",
                },
                {
                    "sequence": 3,
                    "word_budget": 1300,
                    "phase_name": "高潮",
                    "phase_goal": "揭示真相",
                },
                {
                    "sequence": 4,
                    "word_budget": 1500,
                    "phase_name": "结局",
                    "phase_goal": "做出选择",
                },
            ],
            "completion_contract": {
                "core_question": "真相是什么？",
                "resolution_target": "侦探做出选择",
                "ending_strategy": "余韵式",
                "final_emotional_landing": "复杂与释然",
            },
        },
        "target_word_count": 5000,
    }


# -- EDIT (short) fixture --


def _edit_short_context() -> dict:
    return {
        "iteration": 1,
        "beats": {
            "beats": [
                {
                    "sequence": 1,
                    "beat_type": "opening",
                    "summary": "侦探接到神秘电话",
                    "tension_level": 3,
                },
                {
                    "sequence": 2,
                    "beat_type": "catalyst",
                    "summary": "电话那头的人声称知道一桩旧案的真相",
                    "tension_level": 5,
                },
                {
                    "sequence": 3,
                    "beat_type": "midpoint",
                    "summary": "侦探发现线索指向自己信任的人",
                    "tension_level": 8,
                },
                {
                    "sequence": 4,
                    "beat_type": "finale",
                    "summary": "侦探面对真相做出选择",
                    "tension_level": 9,
                },
            ],
        },
        "draft_text": (
            "电话铃响起时，侦探正在整理旧案卷宗。\n"
            "他接起电话，那头传来一个低沉的声音：'我知道那件事的真相。'\n"
            "侦探的手指微微颤抖。他追查了三年，终于有了线索。\n"
            "三天后，他站在审讯室里，面对那个他最信任的人。\n"
            "'为什么？'他问。对方沉默了很久，最终只说了一句：'为了保护你。'"
        ),
        "style_profile": _style_profile(),
        "blueprint": {
            "synopsis": "侦探追查一桩旧案，发现真相出人意料",
            "emotional_arc": "好奇→紧张→震惊→决断",
            "ending_strategy": "余韵式",
            "anchor_elements": {
                "time_frame": "三天内",
                "primary_locations": ["侦探事务所", "审讯室"],
                "core_characters": [
                    {"name": "侦探", "role": "protagonist"},
                    {"name": "嫌疑人", "role": "antagonist"},
                ],
                "central_event": "旧案真相揭晓",
            },
            "element_selection": {
                "extension_elements": [
                    {
                        "name": "悬疑",
                        "prompt_hint": "保持悬念张力",
                        "element_id": "suspense",
                        "category": "tone",
                    },
                ],
            },
        },
        "execution_plan": {
            "opening_contract": "以神秘电话开场",
            "ending_contract": "以侦探的选择收尾",
            "beat_execution_plan": [
                {"sequence": 1, "word_budget": 1000, "phase_name": "开端"},
                {"sequence": 2, "word_budget": 1200, "phase_name": "发展"},
                {"sequence": 3, "word_budget": 1300, "phase_name": "高潮"},
                {"sequence": 4, "word_budget": 1500, "phase_name": "结局"},
            ],
            "completion_contract": {
                "core_question": "真相是什么？",
                "resolution_target": "侦探做出选择",
                "ending_strategy": "余韵式",
                "final_emotional_landing": "复杂与释然",
            },
        },
        "target_word_count": 5000,
        "tone": "suspenseful",
        "opening_style": "异象开篇",
        "ending_style": "余韵式收束",
        "prompt_leak_hits": [],
        "word_count_warning": "",
        "completeness_warning": "",
        "completeness_issues": [],
        "eval_feedback": None,
    }


def _evaluate_context() -> dict:
    return {
        "draft_text": (
            "电话铃响起时，侦探正在整理旧案卷宗。\n"
            "他接起电话，那头传来一个低沉的声音：'我知道那件事的真相。'"
        ),
        "style_profile": _style_profile(),
        "tone": "suspenseful",
        "genre": "mystery",
        "target_word_count": 3000,
    }


def _polish_context() -> dict:
    return {
        "chapter_number": 1,
        "chapter_title": "雪港来信",
        "chapter_text": (
            "电话铃响起时，侦探正在整理旧案卷宗。\n"
            "他接起电话，那头传来一个低沉的声音：'我知道那件事的真相。'"
        ),
        "tone": "suspenseful",
        "genre": "mystery",
        "pov_character": "侦探",
        "style_profile": _style_profile(),
    }


def _alignment_context_with_extra_global_fields() -> dict:
    return {
        "chapter_outline": {
            "chapter_number": 2,
            "title": "熟悉的陌生人",
            "goal": "沈念卿确认金镯与旧世记忆存在关联",
            "main_plot_points": ["金镯触发旧世记忆碎片", "沈念卿决定追查陆云峥"],
            "subplot_points": ["金镯与情绪失控形成隐性联动"],
            "beats_summary": ["梦醒", "金镯发热", "试探陆云峥", "决定追查"],
        },
        "chapter_plan": {
            "scene_intents": [
                {
                    "summary": "沈念卿在峰会后醒来，金镯再次发热",
                    "required_outcome": "确认金镯与旧世记忆有关",
                },
                {
                    "summary": "她与陆云峥短暂交锋",
                    "required_outcome": "形成下一步追查目标",
                },
            ],
        },
        "chapter_text": "沈念卿攥紧金镯，决定追查陆云峥与旧世记忆的关系。",
        "genre": "不应泄漏的都市奇幻题材",
        "pov_hint": "不应泄漏的陆云峥副视角30%与沈鹤卿旁观视角15%",
        "narrative_context": {
            "current_phase_name": "不应泄漏的宏观调查阶段",
            "current_phase_description": "不应泄漏的全书阶段说明",
            "current_phase_tension_level": "高",
            "current_phase_time_context": "不应泄漏的全局时间锚",
            "current_phase_locations": ["不应泄漏的全局场景"],
            "current_phase_key_characters": ["不应泄漏的全局人物"],
            "active_subplot_names": ["不应泄漏的全局支线"],
            "is_turning_point": True,
            "turning_point_description": "不应泄漏的全局转折",
        },
    }


def _plan_card_context(context: dict) -> dict:
    narrative_context = context.get("narrative_context")
    if not narrative_context:
        packet_context = context.get("chapter_state_packet", {})
        if isinstance(packet_context, dict):
            narrative_context = packet_context.get("narrative_context")
    packet = {
        "character_profiles": context.get("character_identity_cards", []),
        "chapter_contract": {},
        "must_carry_forward": context.get("must_carry_forward", []),
        "guard_constraints": [],
        "narrative_context": narrative_context,
    }
    focus = context.get("dynamic_element_focus")
    if not focus:
        outline = context.get("chapter_outline", {})
        focus = outline.get("element_focus", []) if isinstance(outline, dict) else []
    return {
        "stage_cards": build_stage_cards(
            stage="plan",
            packet=packet,
            chapter_outline=context.get("chapter_outline", {}),
            bridge=context.get("chapter_bridge"),
            canon_context=context.get("canon_context"),
            memory_hints=context.get("memory_hints"),
            style_profile=context.get("style_profile"),
            narrative_contract=context.get("narrative_contract"),
            reading_power_hint=context.get("reading_power_hint"),
            element_selection=context.get("element_selection"),
            element_focus=focus,
            pov_hint=context.get("pov_hint", ""),
        )
    }


def _draft_card_context(context: dict) -> dict:
    packet = {
        "chapter_number": context.get("chapter_number", 0),
        "chapter_contract": {},
        "character_profiles": context.get("character_identity_cards", []),
        "must_carry_forward": [],
        "guard_constraints": context.get("guard_constraints", []),
        "narrative_context": context.get("narrative_context"),
    }
    outline = {
        "chapter_number": context.get("chapter_number", 0),
        "title": context.get("chapter_title", ""),
        "goal": context.get("chapter_goal", ""),
        "pov_character": context.get("pov_character", ""),
        "setting": context.get("setting", ""),
        "expected_word_count": context.get("target_word_count", 0),
    }
    memory_hints = {
        "previous_chapter_events": context.get("memory_previous_chapter_events", []),
        "relevant_history": context.get("memory_relevant_history", []),
        "motif_suggestions": context.get("memory_motif_suggestions", []),
        "memory_prompt_context": context.get("memory_prompt_context", {}),
        "critique_context": context.get("critique_context", ""),
    }
    focus = context.get("dynamic_element_focus") or context.get("element_focus")
    return {
        "chapter_number": context.get("chapter_number", 0),
        "target_word_count": context.get("target_word_count", 0),
        "stage_cards": build_stage_cards(
            stage="draft",
            packet=packet,
            chapter_outline=outline,
            bridge=context.get("bridge"),
            plan=context.get("plan"),
            canon_context=context.get("canon_context"),
            memory_hints=memory_hints,
            style_profile=context.get("style_profile"),
            narrative_contract=context.get("narrative_contract"),
            reading_power_hint=context.get("reading_power_hint"),
            element_selection=context.get("element_selection"),
            element_focus=focus,
            weak_senses=context.get("weak_senses", []),
            pov_hint=context.get("pov_hint", ""),
        ),
    }


def _edit_card_context(context: dict) -> dict:
    outline = {
        "chapter_number": context.get("chapter_number", 0),
        "goal": context.get("chapter_goal", ""),
        "expected_word_count": context.get("target_word_count", 0),
    }
    packet = {
        "chapter_number": context.get("chapter_number", 0),
        "chapter_contract": {},
        "character_profiles": context.get("character_identity_cards", []),
        "must_carry_forward": [],
        "guard_constraints": [],
    }
    focus = context.get("dynamic_element_focus") or context.get("element_focus")
    result = {
        "chapter_number": context.get("chapter_number", 0),
        "target_word_count": context.get("target_word_count", 0),
        "draft_text": context.get("draft_text", ""),
        "iteration": context.get("iteration", 1),
        "chapter_repair_report": context.get("chapter_repair_report"),
        "word_count_warning": context.get("word_count_warning", ""),
        "stage_cards": build_stage_cards(
            stage="edit",
            packet=packet,
            chapter_outline=outline,
            plan=context.get("plan"),
            canon_context=context.get("canon_context"),
            memory_hints={},
            style_profile=context.get("style_profile"),
            reading_power_hint=context.get("reading_power_hint"),
            element_selection=context.get("element_selection"),
            element_focus=focus,
            pov_hint=context.get("pov_hint", ""),
        ),
    }
    return {key: value for key, value in result.items() if value is not None}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPlanChapterRender:
    def test_renders_without_exception(self, builder: PromptBuilder) -> None:
        rendered = builder.render(
            TaskType.PLAN_CHAPTER, _plan_card_context(_plan_chapter_context())
        )
        assert isinstance(rendered, str)
        assert len(rendered) > 0

    def test_exactly_one_output_format_block(self, builder: PromptBuilder) -> None:
        rendered = builder.render(
            TaskType.PLAN_CHAPTER, _plan_card_context(_plan_chapter_context())
        )
        assert_exactly_one_output_format_block(rendered)

    def test_output_format_declares_required_keys(self, builder: PromptBuilder) -> None:
        rendered = builder.render(
            TaskType.PLAN_CHAPTER, _plan_card_context(_plan_chapter_context())
        )
        assert "顶层必须包含字段" in rendered
        assert "scene_intents" in rendered
        assert "opening_contract" in rendered
        assert "closing_contract" in rendered
        assert "required_state_transitions" in rendered
        assert "chapter_type" in rendered

    def test_renders_literary_contract_card(self, builder: PromptBuilder) -> None:
        prompt_context = _plan_card_context(_plan_chapter_context())
        prompt_context["stage_cards"]["source"] = {
            "chapter_contract": {},
            "literary_contract": {
                "schema": "literary_contract_v1",
                "character": {"pov_character": "林晚", "required_characters": ["林晚"]},
                "plot": {"required_events": ["进入机库"]},
                "setting": {"primary_location": "地下机库"},
                "pov": {"pov_character": "林晚", "pov_scope": "limited"},
                "theme": {"primary_theme": "信任", "theme_duties": ["信任需要代价"]},
                "style": {"narrative_voice": "冷峻限知", "character_voices": []},
            },
        }

        rendered = builder.render(TaskType.PLAN_CHAPTER, prompt_context)

        assert "## 六要素文学合同卡（P0）" in rendered
        assert "本卡来自 `source.literary_contract`" in rendered
        assert "人物必须落入 `character_motivations`" in rendered
        assert "信任需要代价" in rendered

    def test_plan_instructs_sensory_notes_as_candidate_anchors(
        self,
        builder: PromptBuilder,
    ) -> None:
        rendered = builder.render(
            TaskType.PLAN_CHAPTER, _plan_card_context(_plan_chapter_context())
        )

        assert "`sensory_notes` 输出 2-4 个候选感官锚点" in rendered
        assert "正文只取前 1-2 个落地" in rendered

    def test_reading_power_tension_target_is_rendered(self, builder: PromptBuilder) -> None:
        context = _plan_chapter_context()
        context["reading_power_hint"] = {
            "recommended_hook_type": "mystery",
            "hook_type_constraint": "mystery",
            "tension_target": 6.2,
            "suspense_by_urgency": {"critical": [], "high": []},
        }

        rendered = builder.render(TaskType.PLAN_CHAPTER, _plan_card_context(context))

        assert "【P2·张力目标】预期张力=6.2" in rendered
        assert "【P1·钩子类型建议】本章章尾优先使用：mystery" in rendered

    def test_renders_identity_cards_and_bridge_causal_link(
        self,
        builder: PromptBuilder,
    ) -> None:
        context = _plan_chapter_context()
        context["chapter_bridge"]["causal_link"] = {
            "previous_event": "林晚在上一章捕捉到异常信号。",
            "causal_mechanism": "信号残留指向地下机库，因此她必须立刻追踪源头。",
            "unresolved_question": "信号是否来自内部人员？",
            "open_threads": ["隐藏终端", "周临立场"],
        }

        rendered = builder.render(TaskType.PLAN_CHAPTER, _plan_card_context(context))

        assert "角色身份卡" in rendered
        assert "林晚（protagonist）：机库工程师" in rendered
        assert "因果链（必须转写进 scene_01 与 required_state_transitions）" in rendered
        assert "信号残留指向地下机库" in rendered
        assert "隐藏终端；周临立场" in rendered

    def test_renders_subplot_weave_hints(self, builder: PromptBuilder) -> None:
        rendered = builder.render(
            TaskType.PLAN_CHAPTER, _plan_card_context(_plan_chapter_context())
        )

        assert "## 支线交织卡（P1）" in rendered
        assert "本章活跃支线：信任线" in rendered
        assert "注意林晚对周临的戒备" in rendered
        assert "依赖未满足的节点不得提前兑现" in rendered

    def test_plan_keeps_required_elements_without_forcing_all_extensions(
        self,
        builder: PromptBuilder,
    ) -> None:
        context = _plan_chapter_context()
        context["chapter_outline"]["element_focus"] = []
        context["dynamic_element_focus"] = None
        context["element_selection"] = {
            "required_elements": [
                {
                    "name": "基础叙事完整性",
                    "prompt_hint": "保证每章有明确目标、冲突和后果",
                    "element_id": "core_integrity",
                    "category": "core",
                },
            ],
            "extension_elements": [
                {
                    "name": "不应被列出的扩展要素",
                    "prompt_hint": "这条提示不应在无focus章节中展开",
                    "element_id": "unused_extension",
                    "category": "optional",
                },
            ],
            "focus_constraints": [],
        }

        rendered = builder.render(TaskType.PLAN_CHAPTER, _plan_card_context(context))

        assert "基础叙事完整性" in rendered
        assert "不应被列出的扩展要素" not in rendered
        assert "这条提示不应在无focus章节中展开" not in rendered


class TestDraftChapterRender:
    def test_renders_without_exception(self, builder: PromptBuilder) -> None:
        rendered = builder.render(
            TaskType.DRAFT_CHAPTER, _draft_card_context(_draft_chapter_context())
        )
        assert isinstance(rendered, str)
        assert len(rendered) > 0

    def test_renders_bounded_regeneration_admission(self, builder: PromptBuilder) -> None:
        context = _draft_card_context(_draft_chapter_context())
        context["draft_regeneration"] = {
            "minimum_word_count": 2450,
            "prompt_leak_detected": True,
            "requires_full_rewrite": True,
        }

        rendered = builder.render(TaskType.DRAFT_CHAPTER, context)

        assert "本次重写准入" in rendered
        assert "2450" in rendered
        assert "规则编号" in rendered

    def test_renders_when_scene_omits_pov_knowledge_constraints(
        self, builder: PromptBuilder
    ) -> None:
        context = _draft_card_context(_draft_chapter_context())
        for scene in context["stage_cards"]["plan"]["scene_intents"]:
            scene.pop("pov_knowledge_constraints", None)

        rendered = builder.render(TaskType.DRAFT_CHAPTER, context)

        assert isinstance(rendered, str)
        assert "scene_01" in rendered
        assert "POV 知识边界约束（严格遵守）" not in rendered

    def test_renders_pov_knowledge_constraints_when_present(self, builder: PromptBuilder) -> None:
        context = _draft_card_context(_draft_chapter_context())
        context["stage_cards"]["plan"]["scene_intents"][0]["pov_knowledge_constraints"] = {
            "forbidden_knowledge": ["隐藏终端真实操作者"],
            "sensory_limits": ["看不见控制室内部"],
            "scope_label": "limited",
        }

        rendered = builder.render(TaskType.DRAFT_CHAPTER, context)

        assert "POV 知识边界约束（严格遵守）" in rendered
        assert "隐藏终端真实操作者" in rendered
        assert "看不见控制室内部" in rendered

    def test_renders_when_entity_projection_lacks_optional_fields(
        self, builder: PromptBuilder
    ) -> None:
        context = _draft_chapter_context()
        context["memory_prompt_context"] = {
            **context.get("memory_prompt_context", {}),
            "character_prompt_contexts": [
                {
                    "character": "林晚",
                    "summary_context": "已对周临产生怀疑",
                    "entity_projection": {
                        "entity_id": "char_linyuan",
                        "name": "林晚",
                    },
                }
            ],
        }
        # Regression: StrictUndefined used to raise 'no attribute emotional_state'
        # when _compact_entity_projection stripped the location/emotional_state keys.
        rendered = builder.render(TaskType.DRAFT_CHAPTER, _draft_card_context(context))
        assert isinstance(rendered, str)
        assert "林晚" in rendered

    def test_renders_entity_projection_fields_when_present(self, builder: PromptBuilder) -> None:
        context = _draft_chapter_context()
        context["memory_prompt_context"] = {
            **context.get("memory_prompt_context", {}),
            "character_prompt_contexts": [
                {
                    "character": "林晚",
                    "summary_context": "已对周临产生怀疑",
                    "entity_projection": {
                        "entity_id": "char_linyuan",
                        "name": "林晚",
                        "status": "alive",
                        "location": "机库",
                        "emotional_state": "警惕",
                    },
                }
            ],
        }
        rendered = builder.render(TaskType.DRAFT_CHAPTER, _draft_card_context(context))
        assert "情绪=警惕" not in rendered
        assert "地点=机库" not in rendered
        assert "状态=alive" not in rendered

    # ── Regression: relationship_projection StrictUndefined crash ─────
    # Bug: chapter 2 generation crashed with
    #   RuntimeError: 'dict object' has no attribute 'shift_summary'
    # Root cause: stage_memory_builder._compact_relationship_projection
    # uses _drop_empty which strips `shift_summary=""`. The Jinja2 template
    # (StrictUndefined) then raises on the missing key.

    def test_renders_when_relationship_projection_rel_dict_is_empty(
        self, builder: PromptBuilder
    ) -> None:
        context = _draft_chapter_context()
        context["memory_prompt_context"] = {
            **context.get("memory_prompt_context", {}),
            "character_prompt_contexts": [
                {
                    "character": "林晚",
                    "summary_context": "已对周临产生怀疑",
                    "entity_projection": {
                        "entity_id": "char_linyuan",
                        "name": "林晚",
                    },
                    "relationship_projection": [{}],
                }
            ],
        }
        rendered = builder.render(TaskType.DRAFT_CHAPTER, _draft_card_context(context))
        assert isinstance(rendered, str)
        assert "林晚" in rendered

    def test_renders_when_relationship_projection_has_label_only(
        self, builder: PromptBuilder
    ) -> None:
        context = _draft_chapter_context()
        context["memory_prompt_context"] = {
            **context.get("memory_prompt_context", {}),
            "character_prompt_contexts": [
                {
                    "character": "林晚",
                    "summary_context": "已对周临产生怀疑",
                    "entity_projection": {
                        "entity_id": "char_linyuan",
                        "name": "林晚",
                    },
                    "relationship_projection": [
                        {
                            "source_entity_id": "char_a",
                            "target_entity_id": "char_b",
                            "label": "师兄妹",
                        },
                    ],
                }
            ],
        }
        rendered = builder.render(TaskType.DRAFT_CHAPTER, _draft_card_context(context))
        assert "师兄妹" not in rendered

    def test_renders_when_relationship_projection_has_relation_type_only(
        self, builder: PromptBuilder
    ) -> None:
        context = _draft_chapter_context()
        context["memory_prompt_context"] = {
            **context.get("memory_prompt_context", {}),
            "character_prompt_contexts": [
                {
                    "character": "林晚",
                    "summary_context": "已对周临产生怀疑",
                    "entity_projection": {
                        "entity_id": "char_linyuan",
                        "name": "林晚",
                    },
                    "relationship_projection": [
                        {
                            "source_entity_id": "char_a",
                            "target_entity_id": "char_b",
                            "relation_type": "mentor",
                        },
                    ],
                }
            ],
        }
        rendered = builder.render(TaskType.DRAFT_CHAPTER, _draft_card_context(context))
        assert "mentor" not in rendered

    def test_renders_when_relationship_projection_uses_legacy_shape(
        self, builder: PromptBuilder
    ) -> None:
        context = _draft_chapter_context()
        context["memory_prompt_context"] = {
            **context.get("memory_prompt_context", {}),
            "character_prompt_contexts": [
                {
                    "character": "沈清漪",
                    "summary_context": "对玄昱心存疑虑",
                    "entity_projection": {
                        "entity_id": "char_shen",
                        "name": "沈清漪",
                    },
                    "relationship_projection": [
                        {
                            "source_entity_id": "char_shen",
                            "target_entity_id": "char_xuan",
                            "label": "",
                            "status": "active",
                        },
                    ],
                }
            ],
        }
        rendered = builder.render(TaskType.DRAFT_CHAPTER, _draft_card_context(context))
        assert isinstance(rendered, str)
        assert "沈清漪" not in rendered
        assert "关系上下文" not in rendered

    def test_exactly_one_output_format_block(self, builder: PromptBuilder) -> None:
        rendered = builder.render(
            TaskType.DRAFT_CHAPTER, _draft_card_context(_draft_chapter_context())
        )
        assert_exactly_one_output_format_block(rendered)

    def test_renders_stage_scoped_style_profile_constraints(self, builder: PromptBuilder) -> None:
        context = _draft_chapter_context()
        context["style_profile"] = {
            **context["style_profile"],
            "source_elements": ["story_bible", "synopsis", "tone"],
            "pacing_config": {
                "min_tension_chapters": 2,
                "climax_spacing_chapters": 8,
            },
            "hook_config": {
                "preferred_types": ["mystery", "emotion"],
                "strength_baseline": "medium",
                "chapter_end_required": True,
            },
            "micro_payoff_config": {
                "preferred_types": ["clue", "relationship"],
                "min_per_chapter": 2,
            },
            "hook_score_config": {
                "hook_score_strong": 4.0,
                "hook_score_medium": 2.5,
                "hook_score_weak": 1.0,
                "payoff_cap": 3,
                "transition_penalty": 1.0,
            },
            "reading_power_window_config": {
                "window_size": 5,
                "window_left_offset": 0,
                "window_right_offset": 0,
                "suspense_delay_threshold": 3,
                "force_resolve_threshold": 5,
                "hook_alternation_threshold": 2,
                "max_consecutive_same_hook": 3,
                "tension_deviation_tolerance": 1.5,
                "tension_recovery_factor": 0.3,
                "hook_strength_weights": {
                    "hook_strength": 0.25,
                    "payoff_density": 0.20,
                    "suspense_timing": 0.20,
                    "hook_alternation": 0.15,
                    "tension_match": 0.20,
                },
                "payoff_cap": 3,
                "critical_score_threshold": 3.0,
                "warning_score_threshold": 5.0,
                "enable_force_resolve": True,
                "enable_hook_alternation_check": True,
                "enable_tension_recovery": True,
            },
        }

        rendered = builder.render(TaskType.DRAFT_CHAPTER, _draft_card_context(context))

        assert "## 风格胶囊" in rendered
        assert "段末尽量留动作钩子" in rendered
        assert "章末必须形成可执行钩子" in rendered
        assert "每章最低" in rendered
        assert "来源要素：story_bible、synopsis、tone" not in rendered
        assert "低张力上限：2 章" not in rendered
        assert "追读力窗口配置" not in rendered

    def test_uses_dynamic_focus_without_rendering_all_extensions(
        self,
        builder: PromptBuilder,
    ) -> None:
        context = _draft_chapter_context()
        context["element_selection"] = {
            "extension_elements": [
                {
                    "name": "动态聚焦要素",
                    "prompt_hint": "这条动态要素需要落地",
                    "element_id": "dynamic_focus",
                    "category": "optional",
                },
                {
                    "name": "未激活扩展要素",
                    "prompt_hint": "这条未激活提示不应出现",
                    "element_id": "inactive_extension",
                    "category": "optional",
                },
            ],
            "focus_constraints": ["动态聚焦要素须自然落地"],
        }
        context["element_focus"] = []
        context["dynamic_element_focus"] = ["dynamic_focus"]

        rendered = builder.render(TaskType.DRAFT_CHAPTER, _draft_card_context(context))

        assert "动态聚焦要素" in rendered
        assert "这条动态要素需要落地" in rendered
        assert "动态聚焦要素须自然落地" in rendered
        assert "未激活扩展要素" not in rendered
        assert "这条未激活提示不应出现" not in rendered

    def test_renders_execution_fields_for_front_loaded_quality(
        self,
        builder: PromptBuilder,
    ) -> None:
        rendered = builder.render(
            TaskType.DRAFT_CHAPTER, _draft_card_context(_draft_chapter_context())
        )

        assert "角色身份卡（称谓/动机/行为边界）" in rendered
        assert "林晚（protagonist）：机库工程师" in rendered
        assert "确认信号是否来自内部人员" in rendered
        assert "若判断失误会暴露调查行动" in rendered
        assert "入场锚：上一章留下的不信任" in rendered
        assert "本章悬念问题" in rendered
        assert "本章只埋伏笔/误导，不提前解释：暗示信号与更大阴谋相关" in rendered
        assert "实体/身份指代图" in rendered
        assert "反常细节触发推理" in rendered
        assert "正文开头窗口必须完成 opening_bridge → scene_01" in rendered
        # sensory_notes content is now visible to DRAFT (creative dimensions enhancement).
        assert "候选感官锚点" not in rendered  # meta-rule text
        assert "金属冷光、低频嗡鸣" in rendered  # sensory_notes content is now rendered
        assert "`sensory_notes` 是候选锚点" in rendered
        assert "## 支线交织执行卡" in rendered
        assert "信任线在本章通过隐藏终端线索反哺主线调查" in rendered
        assert "信任线第3章依赖密信线第5章，尚未发生" in rendered
        assert "同一表达通道" in rendered
        assert "最多完整展开一次" in rendered
        assert_creative_specificity_guard(rendered)

    def test_no_focus_does_not_render_all_extension_elements(
        self,
        builder: PromptBuilder,
    ) -> None:
        context = _draft_chapter_context()
        context["element_selection"] = {
            "extension_elements": [
                {
                    "name": "无聚焦扩展要素",
                    "prompt_hint": "无聚焦时不应兜底展开",
                    "element_id": "inactive_extension",
                    "category": "optional",
                },
            ],
        }
        context["element_focus"] = []
        context.pop("dynamic_element_focus", None)

        rendered = builder.render(TaskType.DRAFT_CHAPTER, _draft_card_context(context))

        assert "叙事要素重点约束" not in rendered
        assert "无聚焦扩展要素" not in rendered
        assert "无聚焦时不应兜底展开" not in rendered


class TestEditChapterRender:
    def test_renders_without_exception(self, builder: PromptBuilder) -> None:
        rendered = builder.render(
            TaskType.EDIT_CHAPTER, _edit_card_context(_edit_chapter_context())
        )
        assert isinstance(rendered, str)
        assert len(rendered) > 0

    def test_exactly_one_output_format_block(self, builder: PromptBuilder) -> None:
        rendered = builder.render(
            TaskType.EDIT_CHAPTER, _edit_card_context(_edit_chapter_context())
        )
        assert_exactly_one_output_format_block(rendered)

    def test_edit_uses_compact_style_constraints(self, builder: PromptBuilder) -> None:
        context = _edit_chapter_context()
        context["style_profile"] = {
            **context["style_profile"],
            "hook_config": {
                "preferred_types": ["mystery"],
                "strength_baseline": "medium",
                "chapter_end_required": True,
            },
            "micro_payoff_config": {
                "preferred_types": ["clue"],
                "min_per_chapter": 2,
            },
            "pacing_config": {
                "min_tension_chapters": 2,
                "climax_spacing_chapters": 8,
            },
            "reading_power_window_config": {
                "window_size": 5,
                "force_resolve_threshold": 5,
                "hook_alternation_threshold": 2,
            },
        }

        rendered = builder.render(TaskType.EDIT_CHAPTER, _edit_card_context(context))

        assert "## 风格胶囊" in rendered
        assert "段末尽量留动作钩子" in rendered
        assert "每章最低：2 个" in rendered
        assert "低张力上限" not in rendered
        assert "追读力窗口配置" not in rendered

    def test_edit_renders_identity_cards(self, builder: PromptBuilder) -> None:
        rendered = builder.render(
            TaskType.EDIT_CHAPTER, _edit_card_context(_edit_chapter_context())
        )

        assert "角色身份卡（编辑时不得改坏）" in rendered
        assert "林晚（protagonist）：机库工程师" in rendered
        assert "感官候选：金属冷光、低频嗡鸣" in rendered
        assert "优先压缩重复通道" in rendered
        assert "不因计划里有多个感官提示就新增细节" in rendered
        assert_creative_specificity_guard(rendered)

    def test_no_focus_does_not_render_all_extension_elements(
        self,
        builder: PromptBuilder,
    ) -> None:
        context = _edit_chapter_context()
        context["element_selection"] = {
            "extension_elements": [
                {
                    "name": "编辑未激活扩展要素",
                    "prompt_hint": "编辑阶段无focus时不应展开",
                    "element_id": "inactive_extension",
                    "category": "optional",
                },
            ],
        }
        context["element_focus"] = []
        context.pop("dynamic_element_focus", None)

        rendered = builder.render(TaskType.EDIT_CHAPTER, _edit_card_context(context))

        assert "叙事要素重点约束" not in rendered
        assert "编辑未激活扩展要素" not in rendered
        assert "编辑阶段无focus时不应展开" not in rendered


class TestAlignmentRender:
    def test_renders_without_exception(self, builder: PromptBuilder) -> None:
        rendered = builder.render(
            TaskType.CHECK_ALIGNMENT, _alignment_context_with_extra_global_fields()
        )
        assert isinstance(rendered, str)
        assert len(rendered) > 0

    def test_exactly_one_output_format_block(self, builder: PromptBuilder) -> None:
        rendered = builder.render(
            TaskType.CHECK_ALIGNMENT, _alignment_context_with_extra_global_fields()
        )
        assert_exactly_one_output_format_block(rendered)

    def test_alignment_prompt_uses_only_chapter_scope_inputs(
        self,
        builder: PromptBuilder,
    ) -> None:
        rendered = builder.render(
            TaskType.CHECK_ALIGNMENT, _alignment_context_with_extra_global_fields()
        )

        assert "沈念卿确认金镯与旧世记忆存在关联" in rendered
        assert "金镯触发旧世记忆碎片" in rendered
        assert "确认金镯与旧世记忆有关" in rendered
        assert "不应泄漏的都市奇幻题材" not in rendered
        assert "不应泄漏的陆云峥副视角30%" not in rendered
        assert "不应泄漏的宏观调查阶段" not in rendered
        assert "不应泄漏的全局场景" not in rendered
        assert "不应泄漏的全局转折" not in rendered


class TestEditorialAndSceneValidationBoundaries:
    def test_editorial_prompt_renders_chapter_hard_boundaries(
        self,
        builder: PromptBuilder,
    ) -> None:
        rendered = builder.render(
            TaskType.CHECK_EDITORIAL,
            {
                "chapter_number": 23,
                "chapter_text": "她把短片存成草稿，没有发布。",
                "editorial_contract": {
                    "project_title": "测试",
                    "theme_policies": ["主题通过行动呈现。"],
                },
                "chapter_contract": {
                    "forbidden_changes": ["不得让沈鹿溪在本章完成最终发布，最终发布留给第24章"],
                    "completion_criteria": ["完成最终剪辑，发布尚未发生"],
                },
                "forbidden_reveal_boundaries": [{"rule": "不得让风能路灯在本章提前亮起，属第24章"}],
                "local_findings": [],
                "local_metrics": {},
            },
        )

        assert "当前章硬边界" in rendered
        assert "不得让沈鹿溪在本章完成最终发布" in rendered
        assert "不得让风能路灯在本章提前亮起" in rendered
        assert "若项目编辑契约/初始化风险与本章硬边界冲突" in rendered

    def test_scene_plan_validation_renders_bridge_transition_evidence(
        self,
        builder: PromptBuilder,
    ) -> None:
        rendered = builder.render(
            TaskType.VALIDATE_SCENE_PLAN,
            {
                "chapter_number": 23,
                "target_word_count": 5000,
                "chapter_card": {
                    "chapter_number": 23,
                    "title": "窗口之半",
                    "goal": "完成剪辑但不发布。",
                    "target_word_count": 5000,
                },
                "bridge_card": {
                    "action_handoff": "沈鹿溪沿民宿楼梯走到楼顶。",
                    "causal_link": {"causal_mechanism": "时间推进到清晨，她从房间移动到楼顶。"},
                },
                "scene_plan": {
                    "scene_intents": [
                        {
                            "scene_id": "scene_01",
                            "draft_order": 1,
                            "scene_goal": "开场承接楼顶剪辑。",
                            "dependency_scene_ids": [],
                            "owned_events": ["完成最终剪辑"],
                            "owned_revelations": [],
                            "owned_state_changes": [],
                            "entry_state": "沈鹿溪抵达楼顶。",
                            "exit_state": "短片已完成。",
                            "handoff_to_next": "等待信号窗口。",
                        }
                    ],
                    "opening_contract": "开场必须写清从民宿房间到楼顶的路径。",
                    "closing_contract": "发布尚未发生。",
                },
            },
        )

        assert "桥接因果" in rendered
        assert "不得再输出“Bridge 未解释移动过程/地点跳转未解释”类 issue" in rendered
        assert "若报告地点/时间跳转问题" in rendered


class TestDraftShortRender:
    def test_renders_without_exception(self, builder: PromptBuilder) -> None:
        rendered = builder.render(TaskType.DRAFT, _draft_short_context())
        assert isinstance(rendered, str)
        assert len(rendered) > 0

    def test_exactly_one_output_format_block(self, builder: PromptBuilder) -> None:
        rendered = builder.render(TaskType.DRAFT, _draft_short_context())
        assert_exactly_one_output_format_block(rendered)
        assert_creative_specificity_guard(rendered)


class TestEditShortRender:
    def test_renders_without_exception(self, builder: PromptBuilder) -> None:
        rendered = builder.render(TaskType.EDIT, _edit_short_context())
        assert isinstance(rendered, str)
        assert len(rendered) > 0

    def test_exactly_one_output_format_block(self, builder: PromptBuilder) -> None:
        rendered = builder.render(TaskType.EDIT, _edit_short_context())
        assert_exactly_one_output_format_block(rendered)
        assert_creative_specificity_guard(rendered)


class TestEvaluateRender:
    def test_renders_without_dialogue_ratio_map_context(self, builder: PromptBuilder) -> None:
        rendered = builder.render(TaskType.EVALUATE, _evaluate_context())
        assert isinstance(rendered, str)
        assert "对话占比应在" in rendered

    def test_intent_compliance_rubric_is_adaptive_only(self, builder: PromptBuilder) -> None:
        legacy = builder.render(TaskType.EVALUATE, _evaluate_context())
        adaptive_context = _evaluate_context()
        adaptive_context["require_intent_compliance"] = True
        adaptive = builder.render(TaskType.EVALUATE, adaptive_context)

        assert "维度八：intent_compliance" not in legacy
        assert "维度八：intent_compliance" in adaptive
        assert "intent_compliance < 9" in adaptive


class TestPolishRender:
    def test_renders_without_dialogue_ratio_map_context(self, builder: PromptBuilder) -> None:
        rendered = builder.render(TaskType.POLISH_CHAPTER, _polish_context())
        assert isinstance(rendered, str)
        assert "对话密度" in rendered
        assert "编辑契约卡" not in rendered
        assert_creative_specificity_guard(rendered)

    def test_renders_stage_card_capsules(self, builder: PromptBuilder) -> None:
        context = _polish_context()
        context["stage_cards"] = {
            "source": {
                "source_artifact_id": "chapter_source_slice:001",
                "chapter_contract": {
                    "cognitive_constraints": [
                        {
                            "claim_id": "claim_polish",
                            "cognitive_subjects": ["侦探"],
                            "cognitive_object": "来电者身份",
                            "cognitive_level": "suspicion",
                            "action_level": "internal",
                            "reader_awareness": "partial",
                            "character_knowledge_coverage": {"侦探": "partial"},
                        }
                    ]
                },
            },
            "editorial": {
                "body_signal_budget_per_high_emotion_scene": 2,
                "character_voices": [
                    {
                        "character": "侦探",
                        "sentence_profile": "短句，先观察再判断。",
                        "explanation_bias": "少解释。",
                    }
                ],
            },
            "quality": {
                "chapter_hook": "以未接通的电话制造下一章牵引",
                "in_chapter_payoffs": ["兑现旧案卷宗中的号码"],
            },
            "memory": {
                "expression_channel_records": [
                    {
                        "channel": "body_signal",
                        "channel_id": "hand_tremble",
                        "text": "手指发抖",
                        "reason": "近期重复",
                        "replacement_axes": ["动作选择", "对白延迟"],
                        "recent_semantic_hits": [{"chapter": 2, "quote": "他的手指抖了一下"}],
                    }
                ],
            },
            "style": {
                "summary": "冷静克制，信息从动作中露出。",
                "modules": [{"name": "侦探语体", "rules": ["少解释", "保留推理空白"]}],
                "banned_phrases": ["莫名其妙地心头一紧"],
            },
        }

        rendered = builder.render(TaskType.POLISH_CHAPTER, context)

        assert "编辑契约卡" in rendered
        assert "高情绪场景身体信号预算" in rendered
        assert "追读力胶囊" in rendered
        assert "风格胶囊" in rendered
        assert "表达通道冷却" in rendered
        assert "莫名其妙地心头一紧" in rendered
        assert "claim_id=claim_polish" in rendered
        assert "character_knowledge_coverage=侦探=partial" in rendered
        assert rendered.count("claim_id=claim_polish") == 1


# ---------------------------------------------------------------------------
# Regression: chapter 2 generation crash (弈心锁玉 / 第 2 章)
# Bug: continuity_repair.j2:106/111 and downstream prompts failed with
#   'dict object' has no attribute 'repair_attempt_guidance'
#   'dict object' has no attribute 'escalation_note'
#   'dict object' has no attribute 'shift_summary'
# Root cause: PromptBuilder._with_common_optional_defaults did not provide
# StrictUndefined-safe defaults for memory_context nested keys, and the
# continuity_repair.j2 template accessed them via the unsafe
# `memory_context.X if memory_context else none` pattern.
# ---------------------------------------------------------------------------


class TestRepairContextDefaultsRegression:
    """Guard rails: repair prompts must render even with sparse memory_context."""

    def test_continuity_repair_renders_when_memory_context_is_none(
        self, builder: PromptBuilder
    ) -> None:
        context = {
            "chapter_number": 2,
            "chapter_text": "正文",
            "memory_context": None,
            "continuity_report": {"issues": []},
        }
        rendered = builder.render(TaskType.REPAIR_CONTINUITY, context)
        assert isinstance(rendered, str)
        assert len(rendered) > 0

    def test_continuity_repair_renders_when_memory_context_is_empty_dict(
        self, builder: PromptBuilder
    ) -> None:
        context = {
            "chapter_number": 2,
            "chapter_text": "正文",
            "memory_context": {},
            "continuity_report": {"issues": []},
        }
        rendered = builder.render(TaskType.REPAIR_CONTINUITY, context)
        assert isinstance(rendered, str)
        assert len(rendered) > 0

    def test_continuity_repair_renders_when_memory_context_is_partial_dict(
        self, builder: PromptBuilder
    ) -> None:
        context = {
            "chapter_number": 2,
            "chapter_text": "正文",
            "memory_context": {"unrelated_key": "value"},
            "continuity_report": {"issues": []},
        }
        rendered = builder.render(TaskType.REPAIR_CONTINUITY, context)
        assert isinstance(rendered, str)
        assert len(rendered) > 0

    def test_continuity_repair_includes_repair_guidance_when_provided(
        self, builder: PromptBuilder
    ) -> None:
        guidance = {
            "round_number": 2,
            "max_rounds": 3,
            "strategy_id": "anchor_reconstruction",
            "new_direction": "先恢复余波",
            "avoid": "不要只补一个说明句",
            "diagnosis": ["首轮失败"],
            "issue_focus": ["余波缺失"],
        }
        context = {
            "chapter_number": 2,
            "chapter_text": "正文",
            "memory_context": {"unrelated": "x"},
            "repair_attempt_guidance": guidance,
            "continuity_report": {"issues": []},
        }
        rendered = builder.render(TaskType.REPAIR_CONTINUITY, context)
        assert "本轮修复策略更新" in rendered
        assert "anchor_reconstruction" in rendered

    def test_continuity_repair_includes_escalation_note_when_provided(
        self, builder: PromptBuilder
    ) -> None:
        context = {
            "chapter_number": 2,
            "chapter_text": "正文",
            "memory_context": {"unrelated": "x"},
            "escalation_note": "## 升级提示:连续两轮未恢复锚点",
            "continuity_report": {"issues": []},
        }
        rendered = builder.render(TaskType.REPAIR_CONTINUITY, context)
        assert "历史修复提醒" in rendered
        assert "升级提示" in rendered

    def test_continuity_repair_survives_when_only_memory_context_legacy_keys(
        self, builder: PromptBuilder
    ) -> None:
        """The legacy `memory_context.escalation_note` access pattern still works."""
        context = {
            "chapter_number": 2,
            "chapter_text": "正文",
            "memory_context": {
                "escalation_note": "## 旧版升级提示",
                "repair_attempt_guidance": {
                    "round_number": 1,
                    "max_rounds": 2,
                    "strategy_id": "different_surface",
                    "new_direction": "换用动作",
                    "avoid": "不要重复",
                    "diagnosis": [],
                    "issue_focus": [],
                },
            },
            "continuity_report": {"issues": []},
        }
        rendered = builder.render(TaskType.REPAIR_CONTINUITY, context)
        assert "历史修复提醒" in rendered
        assert "旧版升级提示" in rendered
        assert "本轮修复策略更新" in rendered


class TestRelationshipProjectionShiftSummaryRegression:
    """Guard rails: draft_chapter must render even when shift_summary is missing."""

    def test_draft_chapter_renders_when_relationship_has_no_keys(
        self, builder: PromptBuilder
    ) -> None:
        context = _draft_chapter_context()
        context["memory_prompt_context"] = {
            **context.get("memory_prompt_context", {}),
            "character_prompt_contexts": [
                {
                    "character": "林晚",
                    "summary_context": "已对周临产生怀疑",
                    "entity_projection": {
                        "entity_id": "char_linyuan",
                        "name": "林晚",
                    },
                    "relationship_projection": [{}],
                }
            ],
        }
        rendered = builder.render(TaskType.DRAFT_CHAPTER, _draft_card_context(context))
        assert isinstance(rendered, str)
        assert "林晚" in rendered

    def test_draft_chapter_renders_when_relationship_partial_only_label(
        self, builder: PromptBuilder
    ) -> None:
        context = _draft_chapter_context()
        context["memory_prompt_context"] = {
            **context.get("memory_prompt_context", {}),
            "character_prompt_contexts": [
                {
                    "character": "林晚",
                    "summary_context": "已对周临产生怀疑",
                    "entity_projection": {
                        "entity_id": "char_linyuan",
                        "name": "林晚",
                    },
                    "relationship_projection": [
                        {"source_entity_id": "a", "target_entity_id": "b"},
                    ],
                }
            ],
        }
        rendered = builder.render(TaskType.DRAFT_CHAPTER, _draft_card_context(context))
        assert isinstance(rendered, str)
        assert "林晚" in rendered

    def test_draft_chapter_renders_when_relationship_has_all_legacy_keys(
        self, builder: PromptBuilder
    ) -> None:
        context = _draft_chapter_context()
        context["memory_prompt_context"] = {
            **context.get("memory_prompt_context", {}),
            "character_prompt_contexts": [
                {
                    "character": "林晚",
                    "summary_context": "已对周临产生怀疑",
                    "entity_projection": {
                        "entity_id": "char_linyuan",
                        "name": "林晚",
                    },
                    "relationship_projection": [
                        {
                            "source_entity_id": "char_a",
                            "target_entity_id": "char_b",
                            "shift_summary": "信任加深",
                        },
                    ],
                }
            ],
        }
        rendered = builder.render(TaskType.DRAFT_CHAPTER, _draft_card_context(context))
        assert isinstance(rendered, str)
        assert "信任加深" not in rendered


def test_final_state_prompt_preserves_all_accepted_cognitive_updates(
    builder: PromptBuilder,
) -> None:
    context = {
        "chapter_number": 10,
        "chapter_contract": "{}",
        "contract_targets": "[]",
        "current_state": "{}",
        "contract_coverage_report": "{}",
        "candidates": "[]",
        "decisions": "[]",
        "pre_block_recheck": False,
    }

    rendered = builder.render(TaskType.ADJUDICATE_FINAL_STATE, context)
    english = builder.render(
        TaskType.ADJUDICATE_FINAL_STATE,
        {**context, "prompt_locale": "en", "output_language": "en"},
    )

    assert "不得只取前 N 条" in rendered
    assert "character_knowledge_coverage" in rendered
    assert "unknown|partial|full" in rendered
    assert "never take only a local top-N prefix" in english
    assert "character_knowledge_coverage" in english
