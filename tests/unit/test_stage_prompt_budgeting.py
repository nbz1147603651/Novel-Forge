from __future__ import annotations

from types import SimpleNamespace

from novel_forge.common.constants import TaskType
from novel_forge.pipeline.long.services.constraints.constraint_router import build_stage_cards
from novel_forge.prompts.builder import PromptBuilder


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


def test_stage_hard_facts_are_not_locally_truncated() -> None:
    settings = _ns(
        narrative_state_pending_tail_items=12,
    )
    packet = _ns(
        chapter_number=7,
        chapter_contract={
            "required_events": ["林晚确认异常信号来源"],
            "forbidden_changes": ["周临不得知晓终端密码"],
        },
        guard_constraints=["不得提前揭示幕后组织"],
    )
    outline = _ns(
        chapter_number=7,
        title="回声",
        goal="确认信号来源",
        pov_character="林晚",
        setting="地下机库",
        expected_word_count=4200,
    )
    canon_context = {"immutable_facts": [f"硬事实 {idx}" for idx in range(10)]}

    bridge_cards = build_stage_cards(
        stage="bridge",
        packet=packet,
        chapter_outline=outline,
        canon_context=canon_context,
        settings=settings,
    )
    plan_cards = build_stage_cards(
        stage="plan",
        packet=packet,
        chapter_outline=outline,
        canon_context=canon_context,
        settings=settings,
    )

    expected_facts = [f"硬事实 {idx}" for idx in range(10)]
    assert bridge_cards["contract"]["hard_facts"] == expected_facts
    assert plan_cards["contract"]["hard_facts"] == expected_facts
    assert plan_cards["contract"]["required_events"] == ["林晚确认异常信号来源"]
    assert plan_cards["contract"]["forbidden_changes"] == ["周临不得知晓终端密码"]
    assert plan_cards["contract"]["guard_constraints"] == ["不得提前揭示幕后组织"]


def test_bridge_preserves_upstream_hard_fact_projection_in_source_order() -> None:
    packet = _ns(
        chapter_number=7,
        chapter_contract={"required_events": ["林晚确认异常信号来源"]},
        character_profiles=[{"name": "林晚"}, {"name": "周临"}],
    )
    outline = _ns(
        chapter_number=7,
        title="回声",
        goal="确认信号来源",
        pov_character="林晚",
        setting="地下机库",
        expected_word_count=4200,
    )
    canon_context = {
        "immutable_facts": [
            *[f"外城档案硬事实 {idx}" for idx in range(20)],
            "林晚不能离开地下机库",
            "地下机库内无法使用公网通讯",
        ]
    }

    cards = build_stage_cards(
        stage="bridge",
        packet=packet,
        chapter_outline=outline,
        canon_context=canon_context,
    )

    hard_facts = cards["contract"]["hard_facts"]
    assert hard_facts == canon_context["immutable_facts"]


def test_bridge_prompt_renders_relevant_hard_fact_safety_window(builder: PromptBuilder) -> None:
    hard_fact = "林晚不能离开地下机库"
    rendered = builder.render(
        TaskType.BRIDGE_CHAPTER,
        {
            "stage_cards": {
                "chapter": {
                    "chapter_number": 7,
                    "title": "回声",
                    "goal": "确认信号来源",
                    "pov_character": "林晚",
                    "setting": "地下机库",
                },
                "contract": {"hard_facts": [hard_fact]},
                "bridge": {},
            }
        },
    )

    assert hard_fact in rendered
    assert "相关硬事实" in rendered


def test_authoritative_state_history_paths_are_not_duplicated_into_prompt() -> None:
    settings = _ns(
        narrative_state_pending_tail_items=1,
    )
    outline = _ns(
        chapter_number=12,
        title="门后",
        goal="推进终端线索",
        pov_character="林晚",
        setting="地下机库",
        expected_word_count=4200,
    )
    canon_context = {
        "authoritative_narrative_state": {
            "last_chapter": 11,
            "recent_summaries": [f"第{idx}章摘要" for idx in range(5)],
            "facts_by_path": {f"characters.linwan.fact_{idx}": f"事实 {idx}" for idx in range(10)},
            "pending_items": [{"summary": f"待处理 {idx}"} for idx in range(4)],
            "accepted_updates": [{"summary": "完整流水不应直接进入章节提示词"}],
        }
    }

    cards = build_stage_cards(
        stage="draft",
        chapter_outline=outline,
        canon_context=canon_context,
        settings=settings,
    )

    state = cards["state"]["authoritative_narrative_state"]
    assert state["last_chapter"] == 11
    assert "recent_summaries" not in state
    assert "facts_by_path" not in state
    assert state["pending_items"] == [
        {"summary": "待处理 3", "candidate_id": "", "pending_id": ""}
    ]
    assert "accepted_updates" not in state


def test_draft_prompt_renders_hard_facts_once(builder: PromptBuilder) -> None:
    hard_fact = "【世界规则】时间不可逆"
    rendered = builder.render(
        TaskType.DRAFT_CHAPTER,
        {
            "chapter_number": 1,
            "target_word_count": 1200,
            "stage_cards": {
                "chapter": {
                    "chapter_number": 1,
                    "title": "回声",
                    "goal": "推进异常信号线索",
                    "pov_character": "林晚",
                    "target_word_count": 1200,
                },
                "contract": {
                    "hard_facts": [hard_fact],
                    "required_events": ["林晚确认异常信号来源"],
                },
                "bridge": {},
                "plan": {"scene_intents": []},
            },
        },
    )

    assert rendered.count(hard_fact) == 1
    assert "不可违反的事实" in rendered


def test_plan_prompt_consumes_time_strand_and_element_progress(builder: PromptBuilder) -> None:
    rendered = builder.render(
        TaskType.PLAN_CHAPTER,
        {
            "stage_cards": {
                "chapter": {
                    "chapter_number": 7,
                    "title": "回声",
                    "goal": "确认信号来源",
                    "pov_character": "林晚",
                    "setting": "地下机库",
                    "target_word_count": 4200,
                },
                "contract": {"required_events": ["确认信号来源"]},
                "bridge": {
                    "opening_time": "深夜 02:00",
                    "opening_location": "地下机库",
                    "opening_pov": "林晚",
                    "action_handoff": "林晚推开机库门",
                },
                "characters": [{"name": "林晚", "role": "主角", "identity": "调查者"}],
                "element": {
                    "progress_summary": "近几章要素执行待加强：建议优先关注 suspense",
                    "recommended_focus_ids": ["suspense"],
                    "recent_weak": [
                        {
                            "element_id": "suspense",
                            "last_chapter": 6,
                            "latest_reason": "悬念铺垫不足",
                        }
                    ],
                },
                "time": {
                    "prev_time_anchor": "深夜 00:00",
                    "current_time_anchor": "深夜 02:00",
                    "time_gap_from_prev": "2小时",
                },
                "strand": {
                    "strand_distribution": {"quest": "70%", "fire": "10%"},
                    "strand_alerts": [
                        {
                            "strand": "quest",
                            "message": "主线连续占主导",
                            "suggestion": "加入关系线轻触",
                        }
                    ],
                },
            }
        },
    )

    assert "时间连续性卡" in rendered
    assert "深夜 00:00" in rendered
    assert "要素执行回补" in rendered
    assert "悬念铺垫不足" in rendered
    assert "情节线节奏卡" in rendered
    assert "主线连续占主导" in rendered
