from __future__ import annotations

import re
from pathlib import Path

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.format_contracts import OutputKind, get_task_format_contract
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.prompts.registry import _PROMPTS_DIR, _TASK_TEMPLATE_MAP

_METADATA_MARKERS = (
    "【备注】",
    "服务步骤：",
    "作用简述：",
    "上游输入：",
    "下游输出：",
)

_OUTPUT_HELPERS = ("standard_json_output(", "standard_text_output(", "causal_output_format(")


def _strip_jinja_comments(text: str) -> str:
    return re.sub(r"\{#.*?#\}", "", text, flags=re.DOTALL)


def _generic_context() -> dict[str, object]:
    return {
        "constraint": "必须保留关键线索",
        "chapter_text": "林晚推门进入机库，抬头看见顶灯骤亮。",
        "chapter_number": 3,
        "chapters_audited": [1, 2, 3],
        "audit_entries": [
            {
                "chapter_number": 1,
                "alignment_score": 8.1,
                "continuity_score": 7.9,
                "causal_score": 8.0,
                "deviation_count": 0,
                "deviation_levels": [],
                "new_characters": [],
                "risk_level": "low",
            }
        ],
        "outline": {
            "synopsis": "主角在地下机库追查异常信号。",
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "异响",
                    "goal": "埋下异常信号",
                    "beats_summary": ["异响出现", "主角追查"],
                },
                {
                    "chapter_number": 2,
                    "title": "回声",
                    "goal": "确认信号来自机库内部",
                    "beats_summary": ["潜入机库", "发现异常终端"],
                },
            ],
        },
        "canon_stats": {
            "character_count": 2,
            "foreshadowing_count": 1,
            "world_facts_count": 3,
            "active_threads": [
                {"thread_id": "signal", "title": "异常信号来源"},
                {"thread_id": "trust", "title": "林晚与周临的互信"},
            ],
        },
        "spec": {
            "title": "机库回声",
            "genre": "悬疑",
            "premise": "主角调查地下机库的异常信号",
            "theme": "信任与真相",
            "length_target": 6000,
            "target_audience": "网文读者",
        },
        "story_bible": {
            "world": "近未来城市",
            "tone": "紧张、冷硬",
            "themes": ["信任", "真相"],
        },
        "character_bible": {
            "characters": [
                {
                    "name": "林晚",
                    "role": "protagonist",
                    "personality": "冷静谨慎",
                    "backstory": "前机库工程师",
                    "arc": "从自保到主动追查",
                    "relationships": {"周临": "暂时合作"},
                    "notes": "",
                }
            ]
        },
        "style_profile": {
            "summary": "短句推进，悬念驱动。",
            "global_style": {"dialogue_ratio": "balanced", "pace_mode": "fast"},
            "modules": [],
        },
        "chapter_outline": {
            "chapter_number": 3,
            "title": "断续信号",
            "goal": "确认信号源身份",
            "pov_character": "林晚",
            "setting": "地下机库",
            "expected_word_count": 3000,
            "involved_characters": ["林晚", "周临"],
            "main_plot_points": ["追踪信号源"],
            "subplot_points": ["信任试探"],
            "beats_summary": ["进入机库", "发现终端", "遭遇拦截"],
        },
        "canon_context": {
            "characters": {
                "林晚": {"alive": True, "location": "机库"},
                "周临": {"alive": True, "location": "机库"},
            },
            "recent_events": [{"chapter": 2, "event": "发现异常终端"}],
            "active_foreshadowing": [{"description": "终端上传记录"}],
            "immutable_facts": ["机库位于地下三层"],
        },
        "chapter_bridge": {
            "opening_time": "深夜",
            "opening_location": "地下机库入口",
            "opening_pov": "林晚",
            "action_handoff": "林晚推门进入机库",
            "pending_questions": ["谁在操控信号源？"],
            "bridge_summary": "主角准备深入机库调查。",
        },
        "chapter_plan": {
            "summary": "主角深入机库并确认异常信号来自内部人员。",
            "scene_intents": [
                {"scene_id": "s1", "summary": "进入机库并观察环境", "purpose": "建立悬念"},
                {"scene_id": "s2", "summary": "发现异常终端", "purpose": "推进主线"},
            ],
        },
        "chapter_plan_scenes": [
            {"scene_id": "s1", "summary": "进入机库并观察环境", "purpose": "建立悬念"},
            {"scene_id": "s2", "summary": "发现异常终端", "purpose": "推进主线"},
        ],
        "causal_link": {
            "previous_event": "周临透露机库有人篡改记录。",
            "causal_mechanism": "内部权限被滥用导致异常信号持续存在。",
            "unresolved_question": "谁在操控异常信号源？",
            "open_threads": ["异常终端来源", "内部人员身份"],
        },
        "bridge": {
            "transition_mode": "direct_continue",
            "emotional_carryover": "紧张与警惕",
            "action_handoff": "林晚推门进入机库",
            "opening_location": "地下机库入口",
            "opening_time": "深夜",
        },
        "previous_chapter_ending": "灯光熄灭前，周临只说了一句：‘信号不是从外面来的。’",
        "character_profiles": [
            {
                "name": "林晚",
                "identity": "前机库工程师",
                "abilities": "熟悉旧终端维护流程",
                "backstory": "曾参与机库底层改造",
                "goals": "找出信号源并证明自己无罪",
                "personality": "谨慎、执拗",
                "social_status": "普通技术员",
                "gender": "女",
            }
        ],
        "issue_types": ["opening_causal_gap"],
        "typed_issues": {
            "opening_causal_gap": [
                {
                    "summary": "开头没有承接上一章终端线索。",
                    "evidence": "开头直接写环境，没有继续异常信号。",
                    "fix_suggestion": "让林晚进入机库后先确认终端异响。",
                    "location": "第1-2段",
                }
            ],
            "hook_missing": [
                {
                    "summary": "结尾没有形成下一章点击欲。",
                    "evidence": "最后只是情绪收束。",
                    "fix_suggestion": "增加终端身份揭示前的打断。",
                }
            ],
            "hook_too_weak": [],
            "payoff_missing": [],
            "prev_hook_unfulfilled": [],
            "outline_mismatch": [],
        },
        "word_count_min": 2600,
        "word_count_max": 3400,
        "window_mode": False,
        "forbidden_elements": [],
        "forbidden_elements_soft": [],
        "intentional_callbacks": [],
        "previous_hook_description": "终端异常记录指向内部权限。",
        "expected_hook": {
            "hook_type": "mystery",
            "hook_strength": "strong",
            "hook_description": "章尾揭示信号源可能是熟人。",
        },
        "expected_payoffs": [
            {"payoff_type": "clue", "description": "确认异常信号来自内部终端", "strength": "medium"}
        ],
        "reading_power_hint": {
            "chapter_hook": "章尾揭示信号源可能是熟人。",
            "in_chapter_payoffs": ["确认异常终端属于内部权限"],
            "prev_chapter_overall_score": 7.6,
        },
        "element": {
            "element_id": "suspense",
            "name": "悬疑",
            "category": "tone",
            "prompt_hint": "保持悬念张力",
            "description": "让读者持续追问信号源身份",
        },
        "rule_eval": {"status": "weak", "score": 1.2, "evidence": ["悬念铺垫不足"]},
        "context": {
            "plan_excerpt": "计划摘要",
            "chapter_excerpt": "正文摘要",
            "quality_excerpt": "质检摘要",
        },
        "plot_guard_mode": "warn",
        "next_chapter_number": 4,
        "alignment_report": {"alignment_score": 8.0, "summary": "对齐良好"},
        "continuity_report": {"continuity_score": 7.8, "issues": []},
        "causal_report": {"causal_score": 8.1, "issues": []},
        "eval_report": {"scores": []},
        "plot_deviations": [],
        "new_characters": [],
        "new_locations": [],
        "new_key_items": [],
        "outline_window": [],
        "total_chapters": 12,
        "original_chapters": [],
        "completed_chapter": 3,
        "actual_plot_summary": "本章确认信号源与内部人员有关。",
        "start_adjust_from": 4,
    }


@pytest.mark.parametrize("template_path", sorted(_PROMPTS_DIR.rglob("*.j2")))
def test_metadata_markers_only_exist_inside_jinja_comments(template_path: Path) -> None:
    raw = template_path.read_text(encoding="utf-8")
    uncommented = _strip_jinja_comments(raw)
    for marker in _METADATA_MARKERS:
        assert marker not in uncommented, f"{template_path} leaked metadata marker: {marker}"


@pytest.mark.parametrize(
    ("task_type", "template_name"),
    list(_TASK_TEMPLATE_MAP.items()),
    ids=lambda item: item.value if isinstance(item, TaskType) else str(item),
)
def test_registered_templates_keep_output_boundary_single_and_explicit(
    task_type: TaskType,
    template_name: str,
) -> None:
    raw = (_PROMPTS_DIR / template_name).read_text(encoding="utf-8")
    explicit_output_helpers = sum(raw.count(token) for token in _OUTPUT_HELPERS)
    contract = get_task_format_contract(task_type)

    assert explicit_output_helpers == 0, (
        f"{task_type.value} still defines template output helpers in {template_name}"
    )
    assert "## 输出格式" not in raw, f"{task_type.value} keeps legacy output heading"
    assert "## 输出协议" not in raw, f"{task_type.value} keeps legacy output protocol heading"

    assert contract is not None, f"{task_type.value} lacks TaskFormatContract"
    assert contract.output_kind in {OutputKind.JSON, OutputKind.TEXT}


def test_macro_guard_prompt_uses_clear_canon_thread_boundaries() -> None:
    rendered = PromptBuilder().render(TaskType.MACRO_GUARD_AUDIT, _generic_context())

    assert "活跃剧情线数：2" in rendered
    assert "异常信号来源；林晚与周临的互信" in rendered
    assert "统一格式契约（系统注入）" in rendered
    assert '"dimensions"' in rendered
    assert '"drift_score"' in rendered
    assert '"adjustment_plan"' in rendered


def test_guard_constraint_prompt_consumes_passed_chapter_number() -> None:
    rendered = PromptBuilder().render(TaskType.GUARD_CONSTRAINT_CHECK, _generic_context())

    assert "第 3 章完整正文" in rendered


def test_guard_constraint_prompt_preserves_chapter_tail() -> None:
    context = _generic_context()
    context["chapter_text"] = "开端" + "甲" * 9000 + "关键收束"

    rendered = PromptBuilder().render(TaskType.GUARD_CONSTRAINT_CHECK, context)

    assert "关键收束" in rendered
