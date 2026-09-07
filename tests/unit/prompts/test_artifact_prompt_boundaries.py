"""Prompt boundary checks for the long-form artifact pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

PROMPTS_DIR = (
    Path(__file__).resolve().parents[3]
    / "novel_forge"
    / "prompts"
    / "packs"
    / "zh"
    / "templates"
)


MAIN_CHAIN_TEMPLATES = (
    "writing/bridge_chapter.j2",
    "planning/plan_chapter.j2",
    "planning/plan_chapter_scenes.j2",
    "writing/draft_chapter.j2",
    "writing/draft_scene.j2",
    "writing/wave_chapter.j2",
)

REPAIR_TEMPLATES = (
    "writing/edit_chapter.j2",
    "checking/continuity_repair.j2",
    "checking/causal_repair_typed.j2",
    "checking/repair_reading_power.j2",
    "checking/guardrail_repair.j2",
    "checking/repair_adjudicated_issue.j2",
)

CANON_MEMORY_TEMPLATES = (
    "kernel/extract_candidate_state_deltas.j2",
    "kernel/extract_canon_delta.j2",
    "kernel/extract_canon_delta_fragment.j2",
    "kernel/extract_chapter_summary_exit.j2",
    "kernel/extract_character_state_deltas.j2",
    "kernel/extract_relationship_deltas.j2",
    "kernel/extract_plot_thread_deltas.j2",
    "kernel/extract_motifs.j2",
)


def _template_text(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _render(name: str, context: dict[str, Any]) -> str:
    env = Environment(loader=FileSystemLoader(str(PROMPTS_DIR)), autoescape=False)
    # M3.5: register the style-golden helper as a no-op so templates can
    # render in this pure-Environment context. Real rendering goes through
    # PromptRegistry.
    env.globals.setdefault(
        "render_style_golden_examples",
        lambda *_a, **_k: "",
    )
    return env.get_template(name).render(**context)


def _source_card() -> dict[str, Any]:
    return {
        "source_artifact_id": "chapter_source_slice:003",
        "source_hashes": {"chapter_contract_index": "abc123"},
        "chapter_contract": {
            "chapter_number": 3,
            "title": "旧名污染回避",
            "required_events": ["沈照在书院外确认账册线索"],
            "forbidden_progressions": ["不得提前确认没有书童"],
            "future_leak_risks": ["不得坐实玄奘旧名"],
            "exit_state_targets": ["沈照带着疑点离开"],
        },
        "relevant_entities": [
            {
                "entity_id": "char_shenzhao",
                "entity_type": "character",
                "canonical_name": "沈照",
                "aliases": ["沈先生"],
            }
        ],
        "forbidden_reveal_boundaries": [
            {"kind": "future_leak", "text": "不得提前确认没有书童"}
        ],
        "story_foundation": {"world_rules": ["公文不能凭空出现"]},
        "style_voice": {
            "style_profile": {"summary": "克制、具象"},
            "character_voices": [{"name": "沈照", "voice": "短句，少解释"}],
        },
    }


def _stage_cards() -> dict[str, Any]:
    return {
        "source": _source_card(),
        "chapter": {
            "chapter_number": 3,
            "title": "旧名污染回避",
            "goal": "推进账册线索",
            "pov_character": "沈照",
            "setting": "书院外",
            "target_word_count": 1200,
        },
        "bridge": {
            "opening_time": "翌日清晨",
            "opening_location": "书院外",
            "opening_pov": "沈照",
            "action_handoff": "沈照收起上一章留下的账册残页",
            "pending_questions": ["账册是谁送来的"],
            "causal_link": {
                "previous_event": "账册残页出现",
                "causal_mechanism": "残页指向书院",
                "unresolved_question": "送信人是谁",
            },
        },
        "plan": {
            "scene_intents": [
                {
                    "scene_id": "scene_01",
                    "summary": "沈照抵达书院外",
                    "purpose": "承接账册线索",
                    "conflict": "门房回避",
                    "required_characters": ["沈照"],
                    "required_outcome": "得到下一条线索",
                    "exit_target_state": "疑点保留",
                    "target_words": 500,
                    "pov_character": "沈照",
                    "time_marker": "清晨",
                    "location": "书院外",
                }
            ],
            "cross_scene_intent": {
                "cross_scene_references": [
                    {
                        "ref_type": "callback",
                        "from_scene": "scene_01",
                        "to_scene": "scene_02",
                        "description": "账册残页回响",
                    }
                ],
                "pacing_curve": [2, 4],
            },
            "opening_contract": "从账册残页承接",
            "closing_contract": "保留疑点",
        },
        "contract": {"required_events": ["沈照在书院外确认账册线索"]},
        "characters": [{"name": "沈照", "role": "protagonist", "identity": "查账者"}],
        "style": {"summary": "克制、具象"},
        "editorial": {"character_voices": [{"character": "沈照", "sentence_profile": "短句"}]},
        "quality": {},
        "memory": {},
        "repair": {},
    }


def test_shared_source_contract_renders_into_main_chain_prompts() -> None:
    context = {
        "stage_cards": _stage_cards(),
        "chapter_number": 3,
        "chapter_title": "旧名污染回避",
        "target_word_count": 1200,
        "draft_text": "沈照停在书院外。",
        "current_scene": _stage_cards()["plan"]["scene_intents"][0],
        "completed_scene_handoffs": [],
        "adjacent_scene_handoffs": [],
    }

    for template in MAIN_CHAIN_TEMPLATES:
        rendered = _render(template, context)
        assert "Source Artifact 契约（P0）" in rendered, template
        assert "source_artifact_id：chapter_source_slice:003" in rendered, template
        assert "cards.source` 是本章唯一初始化权威投影" in rendered, template
        assert "forbidden_reveal_boundaries" in rendered, template
        assert "不得提前确认没有书童" in rendered, template
        expected_event_hits = 0 if template == "writing/bridge_chapter.j2" else 1
        assert rendered.count("沈照在书院外确认账册线索") == expected_event_hits, template
        assert "aliases=沈先生" not in rendered, template


def test_main_chain_templates_do_not_directly_consume_legacy_big_bundles() -> None:
    forbidden_phrases = (
        "完整 story_bible",
        "完整 character_bible",
        "完整 editorial_contract",
        "完整 narrative_contract",
        "完整初始化大包",
        "完整人物库",
        "全量含 voice",
        "全 35 字段",
    )

    for template in MAIN_CHAIN_TEMPLATES:
        text = _template_text(template)
        assert "cards.source" in text, template
        for phrase in forbidden_phrases:
            assert phrase not in text, f"{template} still contains {phrase}"


def test_repair_prompts_are_ticket_window_first() -> None:
    for template in REPAIR_TEMPLATES:
        text = _template_text(template)
        assert "render_artifact_source_contract" in text, template
        assert "ticket/window" in text, template
        assert "不自由重写整章" in text, template
        assert "宏观创作规划" in text or "新增剧情" in text or "新增事实" in text, template


def test_canon_memory_extractors_only_write_final_text_facts() -> None:
    for template in CANON_MEMORY_TEMPLATES:
        text = _template_text(template)
        assert "FinalArtifact" in text, template
        assert "最终正文" in text, template
        assert "正文证据" in text or "实际出现" in text, template


def test_initialization_prompts_define_canonical_entity_boundaries() -> None:
    init_templates = (
        "initialization/init_entity_registry.j2",
        "initialization/init_narrative_contract.j2",
        "planning/plan_chapter_contracts.j2",
        "initialization/init_character_roster.j2",
        "initialization/init_character_profile_batch.j2",
        "initialization/init_character_relationship_matrix.j2",
        "initialization/init_character_arc_plan.j2",
    )

    for template in init_templates:
        text = _template_text(template)
        assert "canonical" in text or "Canonical" in text or "规范名" in text, template
        assert "别名" in text or "alias" in text or "旧名" in text, template
