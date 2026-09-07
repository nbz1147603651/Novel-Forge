from __future__ import annotations

from types import SimpleNamespace

from novel_forge.common.constants import TaskType
from novel_forge.pipeline.long.services.constraints.cognitive_constraints import (
    project_cognitive_constraints,
)
from novel_forge.pipeline.long.services.constraints.constraint_router import (
    _build_knowledge_card,
    _merge_source_runtime_contract,
    build_draft_cards,
    build_outline_reveal_window,
    build_plan_cards,
    build_repair_cards,
    build_stage_cards,
    build_wave_cards,
)
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.prompts.registry import PromptRegistry


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


def _editorial_contract_payload() -> dict:
    return {
        "project_title": "声纹测试",
        "character_voices": [
            {
                "character": "令昭",
                "sentence_profile": "短句，先压住事实再反问。",
                "explanation_bias": "少解释，用账簿细节替代情绪。",
                "emotion_syntax": "紧张时句子断开，少用完整抒情句。",
                "signature_moves": ["停半拍后追问"],
                "taboo_patterns": ["长篇自白"],
                "sample_lines": ["账不对。"],
            },
            {
                "character": "沈鹤卿",
                "sentence_profile": "慢句，常以礼貌收束威胁。",
                "explanation_bias": "只给半句理由。",
                "emotion_syntax": "愤怒时反而更客气。",
                "signature_moves": ["称呼对方全名"],
                "taboo_patterns": ["直白认输"],
                "sample_lines": ["令昭，你看错了。"],
            },
        ],
        "climax_markers": [
            {
                "chapter_number": 20,
                "climax_type": "main",
                "description": "账簿真相公开。",
                "expected_aftermath_chapters": 3,
            }
        ],
        "denouement_budget": {
            "expected_chapters": 3,
            "max_confirmation_scenes": 1,
            "required_new_functions": ["公开真相后的新阻力"],
            "forbidden_repeats": [],
        },
        "theme_policies": ["主题必须通过选择呈现。"],
        "symbol_policies": [
            {
                "symbol": "账簿残页",
                "narrative_function": "真相证据",
                "explanation_policy": "explain_once",
                "escalation_rule": "只在证据升级时解释。",
                "max_explicit_explanations": 1,
            }
        ],
        "scene_resistance_rules": [
            {
                "scene_type": "搜查",
                "required_resistance": "空间遮挡和时间压力同时存在。",
                "examples": ["门外脚步", "雨声掩盖"],
            }
        ],
    }


def test_draft_cards_route_contract_bridge_plan_without_critique_dump() -> None:
    packet = _ns(
        chapter_number=2,
        chapter_contract={
            "required_events": ["女主发现账簿残页"],
            "forbidden_changes": ["禁止沈鹤卿在本章出场"],
            "exit_state_targets": ["女主带着残页离开"],
        },
        must_carry_forward=["上一章末尾门外脚步声必须承接"],
        guard_constraints=["禁止陆云峥正面出场"],
        canon_context={"immutable_facts": ["沈鹤卿此时不在京城"]},
        character_profiles=[
            {"name": "令昭", "role": "女主", "identity": "被卷入账簿案的人"},
        ],
    )
    outline = _ns(
        chapter_number=2,
        title="暗账",
        goal="让令昭拿到账簿残页",
        pov_character="令昭",
        setting="雨夜巷口",
        expected_word_count=4200,
        main_plot_points=["发现账簿残页"],
        subplot_points=[],
        beats_summary=[],
    )
    plan = _ns(
        opening_contract="前300字续接门外脚步声",
        closing_contract="令昭带残页离开",
        chapter_type="crisis",
        opening_bridge={
            "opening_time": "当夜",
            "opening_location": "巷口",
            "opening_pov": "令昭",
            "action_handoff": "她仍按着门闩，听见脚步逼近",
            "boundary_window_policy": {
                "previous_tail_paragraphs": 5,
                "opening_paragraphs": 3,
            },
        },
        scene_intents=[
            _ns(
                scene_id="scene_01",
                summary="令昭避开门外搜查",
                purpose="承接上章危机",
                conflict="不能被搜到残页",
                required_characters=["令昭"],
                character_motivations=[
                    _ns(character="令昭", motivation="藏起残页", stake="暴露即被抓")
                ],
                entry_state_refs=["门外脚步声"],
                required_outcome="藏起残页",
                exit_target_state="暂时脱身",
                scene_goal="在不暴露残页的前提下摆脱搜查",
                forbidden_overlap=["不得提前揭示门外人身份"],
                handoff_to_next="令昭携残页转入巷口",
                entry_state="门外搜查逼近",
                exit_state="令昭暂时脱身但被盯上",
                draft_order=1,
                location="巷口",
                time_marker="当夜",
                sensory_notes="雨声；木门潮气",
                pov_knowledge_constraints={
                    "forbidden_knowledge": ["门外人的真实身份"],
                    "sensory_limits": ["看不见门外搜查者的手势"],
                    "scope_label": "limited",
                },
                target_words=900,
            )
        ],
        required_state_transitions=["残页从未知变为令昭掌握"],
        emotional_arc="惊惧转为决意",
        relationship_evolution=[],
        key_revelations=[],
        foreshadowing_plan=[],
        forbidden_elements=[],
        forbidden_elements_soft=[],
        forbidden_elements_quota=[],
        intentional_callbacks=[],
    )

    cards = build_draft_cards(
        packet=packet,
        chapter_outline=outline,
        plan=plan,
        canon_context=packet.canon_context,
        memory_hints={
            "previous_chapter_events": [
                {"chapter_number": 1, "event_summary": "令昭听见门外脚步声"}
            ],
            "critique_context": "这段未归档批评不应进入 draft",
        },
        style_profile={
            "summary": "冷静克制",
            "global_style": {"dialogue_ratio": "medium", "pace_mode": "fast"},
        },
    )

    assert cards["contract"]["required_events"] == ["女主发现账簿残页"]
    assert "禁止沈鹤卿在本章出场" in cards["contract"]["forbidden_changes"]
    assert "bridge" not in cards
    assert cards["plan"]["opening_bridge"]["action_handoff"] == "她仍按着门闩，听见脚步逼近"
    assert cards["plan"]["opening_bridge"]["boundary_window_policy"] == {
        "previous_tail_paragraphs": 5,
        "opening_paragraphs": 3,
    }
    assert cards["plan"]["scene_intents"][0]["required_outcome"] == "藏起残页"
    # DRAFT now sees the atomic P0 ownership checklist (owned_events) so it can
    # verify each hard outcome instead of only seeing a summary string.
    assert cards["plan"]["scene_intents"][0]["owned_events"] == ["藏起残页"]
    # DRAFT sees craft fields, emotional/sensory/dialogue dimensions, plus P0 POV constraints.
    assert set(cards["plan"]["scene_intents"][0]) == {
        "scene_id",
        "summary",
        "purpose",
        "conflict",
        "required_characters",
        "character_motivations",
        "entry_state_refs",
        "required_outcome",
        "owned_events",
        "owned_revelations",
        "owned_state_changes",
        "dramatic_question",
        "exit_target_state",
        "location",
        "time_marker",
        "choice_pressure",
        "scene_resistance",
        "scene_goal",
        "forbidden_overlap",
        "handoff_to_next",
        "entry_state",
        "exit_state",
        "draft_order",
        "revelation_level",
        "symbol_usage_policy",
        "body_signal_budget",
        "target_words",
        "pov_character",
        "pov_scope",
        "pov_switch_allowed",
        "pov_switch_marker_required",
        "pov_knowledge_constraints",
        "world_rule_ids",
        "world_rule_usage",
        "world_rule_evidence_expectations",
        "world_rule_forbidden_boundaries",
        # Creative dimensions now visible to DRAFT for upstream quality.
        "emotional_beat",
        "sensory_notes",
        "dialogue_voice_targets",
        "dialogue_subtext",
        "sensory_focus",
        "relationship_dynamics",
    }
    assert cards["plan"]["scene_intents"][0]["pov_switch_allowed"] is False
    assert cards["plan"]["scene_intents"][0]["pov_switch_marker_required"] is True
    assert cards["plan"]["scene_intents"][0]["pov_knowledge_constraints"] == {
        "forbidden_knowledge": ["门外人的真实身份"],
        "sensory_limits": ["看不见门外搜查者的手势"],
        "scope_label": "limited",
    }
    assert cards["plan"]["scene_intents"][0]["sensory_notes"] == "雨声；木门潮气"
    assert cards["characters"][0]["name"] == "令昭"
    assert "repair_lessons" not in cards["memory"]


def test_retrieval_evidence_is_routed_to_reasoning_stages_not_prose_generation() -> None:
    packet = _ns(
        chapter_number=5,
        retrieval_evidence_pack={
            "pack_id": "pack-5",
            "max_visible_chapter": 4,
            "evidence_cards": [
                {
                    "source_ref": "narrative_state/state_ledger/state_4",
                    "excerpt": "已裁定状态：密钥仍由顾青岚保管。",
                    "authority": "accepted",
                }
            ],
            "candidate_limit": 24,
            "evidence_token_budget": 2400,
            "estimated_evidence_tokens": 120,
            "retrieved_candidate_count": 9,
            "omitted_candidate_count_lower_bound": 1,
            "has_more_evidence": True,
        },
    )
    outline = _ns(chapter_number=5, pov_character="顾青岚")

    plan_cards = build_stage_cards(stage="plan", packet=packet, chapter_outline=outline)
    review_cards = build_stage_cards(stage="review", packet=packet, chapter_outline=outline)
    draft_cards = build_stage_cards(stage="draft", packet=packet, chapter_outline=outline)
    wave_cards = build_stage_cards(stage="wave", packet=packet, chapter_outline=outline)

    assert "retrieval_evidence" in plan_cards
    assert "retrieval_evidence" in review_cards
    assert "retrieval_evidence" not in draft_cards
    assert "retrieval_evidence" not in wave_cards
    assert plan_cards["retrieval_evidence"]["selection"]["has_more_evidence"] is True

    plan_prompt = PromptBuilder().render(
        TaskType.PLAN_CHAPTER,
        {"stage_cards": plan_cards},
    )
    assert "规划用动态叙事证据" in plan_prompt
    assert "已裁定状态：密钥仍由顾青岚保管" in plan_prompt
    assert "至少仍有 1 条未装入" in plan_prompt


def test_memory_capsule_uses_top_level_unified_guidance() -> None:
    cards = build_plan_cards(
        packet=_ns(chapter_number=3, chapter_contract={}, canon_context={}),
        chapter_outline=_ns(chapter_number=3, goal="追查账簿", pov_character="令昭"),
        memory_hints={
            "motif_continuity": {
                "active_motifs": [{"motif": "残页"}],
                "forbidden_repetition": ["冷光"],
            },
            "unified_guidance": "本章轻触残页母题，不要重复冷光意象。",
        },
    )

    assert cards["memory"]["unified_guidance"] == "本章轻触残页母题，不要重复冷光意象。"


def test_contract_card_routes_cognitive_constraints() -> None:
    cognitive_constraint = {
        "claim_id": "claim_identity",
        "claim_text": "玄昱确认沈清漪即青阳会盟少年，但只在心里知道。",
        "cognitive_subjects": ["玄昱"],
        "cognitive_object": "沈清漪即青阳会盟少年",
        "cognitive_level": "confirmed",
        "action_level": "internal",
        "reader_awareness": "full",
        "character_knowledge_coverage": {"玄昱": "full", "沈清漪": "unknown"},
        "cognitive_chapter": 1,
        "public_reveal_chapter": 50,
        "foreshadow_chapters": [1, 3, 7],
    }
    cards = build_stage_cards(
        stage="plan",
        packet=_ns(
            chapter_number=1,
            chapter_contract={"cognitive_constraints": [cognitive_constraint]},
            canon_context={},
        ),
        chapter_outline=_ns(chapter_number=1, goal="洞房夜试探", pov_character="玄昱"),
    )

    assert cards["contract"]["cognitive_constraints"] == [cognitive_constraint]


def test_outline_reveal_window_projects_editorial_ladder_for_batch() -> None:
    editorial_contract = {
        "revelation_ladder": [
            {
                "thread": "小书童身份线",
                "stage_order": 1,
                "stage": "无人见过疑云确认",
                "target_chapter": 15,
                "trigger": "清漪询问内侍",
                "allowed_disclosure": "只确认无人见过，不解释真实身份",
                "required_action_consequence": "清漪开始调旧档",
            },
            {
                "thread": "小书童身份线",
                "stage_order": 2,
                "stage": "真实身份揭露",
                "target_chapter": 45,
                "trigger": "情绪失控",
                "allowed_disclosure": "揭露幼态人格",
                "required_action_consequence": "二人转向联手",
            },
        ]
    }

    window = build_outline_reveal_window(
        editorial_contract,
        batch_start=1,
        batch_end=5,
        total_chapters=60,
    )

    assert window["future_guardrails"][0]["target_chapter"] == 15
    assert "强钩子" not in window["priority_rule"]
    assert "不得写成坐实" in window["allowed_policy"]

    prompt = PromptBuilder().render(
        TaskType.PLAN_OUTLINE_BATCH,
        {
            "spec": _ns(
                genre="权谋言情",
                theme="身份与信任",
                tone="悬疑",
                length_target="60章",
                language="zh",
                characters_hint="沈清漪、玄昱",
                world_hint="三国鼎立",
                conflict_hint="和亲与暗线",
                pov_hint="第三人称限知",
                opening_style="从婚仪切入",
                ending_style="同盟收束",
                extra_instructions="",
            ),
            "blueprint": _ns(
                synopsis="沈清漪入魏后逐步发现小书童异常。",
                narrative_phases=[],
                key_turning_points=[],
                character_arcs=[],
                subplot_plan=[],
                ending_strategy="",
                element_selection=_ns(extension_elements=[]),
            ),
            "batch_start": 1,
            "batch_end": 5,
            "words_per_chapter": 5000,
            "previous_chapters": None,
            "outline_tracker_context": None,
            "outline_density": {},
            "style_profile": None,
            "creative_director_packet": None,
            "relationship_overview": [],
            "reveal_window": window,
        },
    )

    assert "叙事揭示边界（P0）" in prompt
    assert "目标第15章 小书童身份线 / 无人见过疑云确认" in prompt


def test_plan_prompts_render_sparse_cognitive_constraints() -> None:
    sparse_constraint = {
        "claim_id": "claim_sparse",
        "claim_text": "玄昱只在心里确认沈清漪身份，不公开说破。",
        "cognitive_subjects": ["玄昱"],
        "cognitive_object": "沈清漪身份",
        "cognitive_level": "confirmed",
        "action_level": "internal",
        "reader_awareness": "partial",
    }
    cards = build_stage_cards(
        stage="plan",
        packet=_ns(
            chapter_number=1,
            chapter_contract={"cognitive_constraints": [sparse_constraint]},
            canon_context={},
        ),
        chapter_outline=_ns(
            chapter_number=1,
            title="杯底藏锋",
            goal="合卺酒夜试探",
            notes="",
            pov_character="沈清漪",
            setting="邺城魏宫婚殿",
            expected_word_count=5000,
            beats_summary=[],
        ),
    )

    routed_constraint = cards["contract"]["cognitive_constraints"][0]
    assert routed_constraint["public_reveal_chapter"] is None
    assert routed_constraint["cognitive_chapter"] is None
    assert routed_constraint["foreshadow_chapters"] == []

    plan_prompt = PromptBuilder().render(TaskType.PLAN_CHAPTER, {"stage_cards": cards})
    assert "claim_id=claim_sparse" in plan_prompt
    assert "公开章=第" not in plan_prompt

    # Simulate a legacy cached card that predates runtime normalization.
    routed_constraint.pop("public_reveal_chapter")
    routed_constraint.pop("cognitive_chapter")
    scene_prompt = PromptBuilder().render(
        TaskType.PLAN_CHAPTER_SCENES,
        {"stage_cards": cards, "scene_plan_validation_issues": []},
    )
    assert "claim_id=claim_sparse" in scene_prompt


def test_runtime_prompts_render_cognitive_constraints_for_draft_wave_and_edit() -> None:
    cards = {
        "chapter": {
            "chapter_number": 2,
            "title": "空阶问童子",
            "goal": "清漪只产生疑心，不确认小书童无人见过",
            "pov_character": "沈清漪",
            "setting": "东宫",
            "target_word_count": 1200,
        },
        "contract": {
            "required_events": ["清漪听到关于小书童的传闻"],
            "forbidden_changes": [],
            "forbidden_progressions": ["不得坐实小书童无人见过"],
            "future_leak_risks": ["提前确认小书童无人见过"],
            "exit_state_targets": [],
            "must_carry_forward": [],
            "cognitive_constraints": [
                {
                    "claim_id": "claim_ladder_15",
                    "claim_text": "第15章才能确认小书童无人见过",
                    "cognitive_subjects": ["沈清漪"],
                    "cognitive_object": "小书童无人见过",
                    "cognitive_level": "suspicion",
                    "action_level": "hinted",
                    "reader_awareness": "partial",
                    "character_knowledge_coverage": {"沈清漪": "partial", "玄昱": "unknown"},
                    "cognitive_chapter": 15,
                    "public_reveal_chapter": 45,
                }
            ],
        },
        "bridge": {},
        "plan": {
            "scene_intents": [
                {
                    "scene_id": "scene_01",
                    "summary": "清漪听见传闻",
                    "purpose": "制造疑心",
                    "conflict": "信息不足",
                    "required_characters": ["沈清漪"],
                    "character_motivations": [],
                    "entry_state_refs": [],
                    "required_outcome": "清漪只记录疑点",
                    "exit_target_state": "疑心增加",
                    "target_words": 800,
                    "pov_character": "沈清漪",
                }
            ],
            "cross_scene_intent": {"cross_scene_references": [], "pacing_curve": [2]},
        },
    }
    cards["source"] = {"chapter_contract": cards["contract"]}
    builder = PromptBuilder()

    draft_prompt = builder.render(TaskType.DRAFT_CHAPTER, {"stage_cards": cards})
    wave_prompt = builder.render(
        TaskType.WAVE_CHAPTER,
        {"stage_cards": cards, "draft_text": "沈清漪听见传闻，暂且记下疑点。"},
    )
    edit_prompt = builder.render(
        TaskType.EDIT_CHAPTER,
        {"stage_cards": cards, "draft_text": "沈清漪听见传闻。", "iteration": 1},
    )
    english_draft_prompt = builder.render(
        TaskType.DRAFT_CHAPTER,
        {"stage_cards": cards, "prompt_locale": "en", "output_language": "en"},
    )

    for prompt in (draft_prompt, wave_prompt, edit_prompt):
        assert "claim_id=claim_ladder_15" in prompt
        assert "character_knowledge_coverage=沈清漪=partial, 玄昱=unknown" in prompt
        assert prompt.count("claim_id=claim_ladder_15") == 1
    assert "不得坐实小书童无人见过" in wave_prompt
    assert "claim_id=claim_ladder_15" in english_draft_prompt
    assert "character_knowledge_coverage=沈清漪=partial, 玄昱=unknown" in english_draft_prompt


def test_repair_prompts_render_reveal_guard_contract() -> None:
    cards = {
        "chapter": {"chapter_number": 2, "title": "空阶问童子", "target_word_count": 1200},
        "contract": {
            "required_events": ["清漪听到关于小书童的传闻"],
            "forbidden_changes": [],
            "forbidden_progressions": ["不得坐实小书童无人见过"],
            "future_leak_risks": ["提前确认小书童无人见过"],
            "exit_state_targets": [],
            "must_carry_forward": [],
            "cognitive_constraints": [
                {
                    "claim_id": "claim_ladder_15",
                    "claim_text": "第15章才能确认小书童无人见过",
                    "cognitive_subjects": ["沈清漪"],
                    "cognitive_object": "小书童无人见过",
                    "cognitive_level": "suspicion",
                    "action_level": "hinted",
                    "reader_awareness": "partial",
                    "character_knowledge_coverage": {"沈清漪": "partial", "玄昱": "unknown"},
                    "cognitive_chapter": 15,
                    "public_reveal_chapter": 45,
                }
            ],
        },
        "bridge": {"action_handoff": ""},
        "knowledge": {"character_cards": [], "chapter_ops": []},
        "plan": {
            "closing_contract": "只留下疑点",
            "scene_intents": [],
            "forbidden_elements": [],
            "forbidden_elements_soft": [],
            "intentional_callbacks": [],
        },
        "repair": {"do_not_introduce": ["确认型物证"]},
    }
    cards["source"] = {"chapter_contract": cards["contract"]}
    builder = PromptBuilder()

    continuity_prompt = builder.render(
        TaskType.REPAIR_CONTINUITY,
        {
            "chapter_number": 2,
            "chapter_text": "沈清漪听见传闻。",
            "continuity_report": {"issues": []},
            "repair_plan": {"no_op": True},
            "stage_cards": cards,
            "word_count_min": 800,
            "word_count_max": 1500,
        },
    )
    causal_prompt = builder.render(
        TaskType.REPAIR_CAUSAL,
        {
            "chapter_number": 2,
            "chapter_text": "沈清漪听见传闻。",
            "numbered_chapter_text": "",
            "causal_link": None,
            "previous_chapter_ending": "",
            "chapter_plan_scenes": [],
            "character_whitelist_note": "",
            "escalation_note": "",
            "memory_guidance": None,
            "typed_issues": {},
            "issue_types": [],
            "stage_cards": cards,
            "word_count_min": 800,
            "word_count_max": 1500,
        },
    )
    reading_power_prompt = builder.render(
        TaskType.REPAIR_READING_POWER,
        {
            "chapter_number": 2,
            "chapter_text": "沈清漪听见传闻。",
            "typed_issues": {},
            "issue_types": [],
            "stage_cards": cards,
            "expected_hook": None,
            "expected_payoffs": [],
            "previous_hook_description": "",
            "character_whitelist_note": "",
            "escalation_note": "",
            "memory_guidance": None,
            "forbidden_elements": [],
            "forbidden_elements_soft": [],
            "intentional_callbacks": [],
            "word_count_min": 800,
            "word_count_max": 1500,
        },
    )

    for prompt in (continuity_prompt, causal_prompt, reading_power_prompt):
        assert "claim_id=claim_ladder_15" in prompt
        assert "character_knowledge_coverage=沈清漪=partial, 玄昱=unknown" in prompt
        assert prompt.count("claim_id=claim_ladder_15") == 1
        assert "不得坐实小书童无人见过" in prompt
        assert "提前确认小书童无人见过" in prompt
        assert "小书童无人见过" in prompt


def test_long_chain_prompts_render_sparse_nested_stage_cards() -> None:
    stage_cards = {
        "chapter": {
            "chapter_number": 1,
            "title": "杯底藏锋",
            "goal": "合卺酒夜试探",
            "pov_character": "沈清漪",
            "setting": "邺城魏宫婚殿",
            "target_word_count": 1200,
        },
        "contract": {
            "required_events": ["完成合卺酒试探"],
            "milestone_window": {"current": []},
            "cognitive_constraints": [{"claim_id": "claim_sparse"}],
        },
        "bridge": {
            "opening_time": "戌时末",
            "opening_location": "魏宫婚殿",
            "opening_pov": "沈清漪",
            "action_handoff": "沈清漪迈过门槛。",
            "causal_link": {"previous_event": "拜堂礼成"},
        },
        "plan": {
            "opening_contract": "承接迈入婚殿",
            "closing_contract": "留下合卺酒悬念",
            "scene_intents": [{"scene_id": "scene_01"}],
            "expression_channel_records": [
                {
                    "text": "眼神一动",
                    "recent_semantic_hits": [{"quote": "旧章例句"}],
                }
            ],
        },
        "memory": {
            "previous_chapter_events": [{"chapter_number": 0}],
            "relevant_history": [{"event_summary": "青阳旧事只可暗示"}],
            "expression_channel_records": [{"channel": "action_tag"}],
            "character_prompt_contexts": [
                {
                    "character": "沈清漪",
                    "episodic_context": [{"chapter_number": 0}],
                    "motif_context": {"associated_motifs": [{"name": "残玉"}]},
                }
            ],
            "motif_suggestions": [{"motif_name": "残玉"}],
        },
        "element": {
            "required_elements": [{"element_id": "spy_cover_integrity"}],
            "focused_extension_elements": [{"name": "信息差"}],
            "recent_missed": [{"element_id": "spy_cover_integrity"}],
            "recent_weak": [{"last_chapter": 0}],
        },
        "editorial": {
            "climax_distance": 1,
            "main_climax": {},
            "character_voices": [{"character": "沈清漪"}],
            "symbol_policies": [{"symbol": "残玉"}],
            "scene_resistance_rules": [{}],
            "revelation_ladder": [{"thread": "青阳真相"}],
            "editorial_element_directives": [{"element_id": "spy_cover_integrity"}],
        },
        "knowledge": {
            "character_cards": [
                {
                    "character": "沈清漪",
                    "chapter_promised_changes": [{"fact": "察觉酒中异样"}],
                }
            ],
            "global_ops": [{"fact": "合卺礼已开始"}],
        },
        "state": {
            "authoritative_narrative_state": {
                "pending_items": [{"candidate_id": "state-001"}],
            },
            "entity_reference_graph": {
                "identity_links": [{"source": "残玉", "target": "青阳"}],
                "context_links": [{"source": "合卺酒"}],
            },
        },
        "strand": {"strand_alerts": [{"strand": "主线"}]},
        "characters": [{"name": "沈清漪"}],
    }

    builder = PromptBuilder()
    plan_prompt = builder.render(TaskType.PLAN_CHAPTER, {"stage_cards": stage_cards})
    draft_prompt = builder.render(
        TaskType.DRAFT_CHAPTER,
        {"stage_cards": stage_cards, "target_word_count": 1200, "weak_senses": []},
    )
    edit_prompt = builder.render(
        TaskType.EDIT_CHAPTER,
        {
            "stage_cards": stage_cards,
            "target_word_count": 1200,
            "iteration": 1,
            "draft_text": "沈清漪停在殿门前。",
        },
    )

    assert "claim_id=claim_sparse" in plan_prompt
    assert "state-001" in draft_prompt
    assert "沈清漪停在殿门前。" in edit_prompt


def test_contract_card_preserves_all_llm_cognitive_constraints_in_order() -> None:
    constraints = [
        {
            "claim_id": "distant",
            "claim_text": "远期公开，不应挤占当前章。",
            "cognitive_subjects": ["玄昱"],
            "cognitive_object": "远期真相",
            "cognitive_level": "partial",
            "action_level": "hinted",
            "reader_awareness": "partial",
            "public_reveal_chapter": 20,
        },
        {
            "claim_id": "foreshadow_now",
            "claim_text": "本章必须铺垫残玉。",
            "cognitive_subjects": ["清漪"],
            "cognitive_object": "残玉异动",
            "cognitive_level": "suspicion",
            "action_level": "hinted",
            "reader_awareness": "partial",
            "foreshadow_chapters": [3],
        },
        {
            "claim_id": "reveal_now",
            "claim_text": "本章公开账簿真相。",
            "cognitive_subjects": ["令昭"],
            "cognitive_object": "账簿真相",
            "cognitive_level": "acknowledged",
            "action_level": "revealed",
            "reader_awareness": "full",
            "public_reveal_chapter": 3,
        },
        {
            "claim_id": "confirm_now",
            "claim_text": "本章角色内心确认身份。",
            "cognitive_subjects": ["玄昱"],
            "cognitive_object": "身份真相",
            "cognitive_level": "confirmed",
            "action_level": "internal",
            "reader_awareness": "full",
            "cognitive_chapter": 3,
        },
    ]

    cards = build_stage_cards(
        stage="plan",
        packet=_ns(
            chapter_number=3,
            chapter_contract={"cognitive_constraints": constraints},
            canon_context={},
        ),
        chapter_outline=_ns(chapter_number=3, goal="处理当前章真相", pov_character="令昭"),
        settings=_ns(),
    )

    selected_ids = [item["claim_id"] for item in cards["contract"]["cognitive_constraints"]]
    assert selected_ids == ["distant", "foreshadow_now", "reveal_now", "confirm_now"]


def test_cognitive_projection_dedupes_provenance_only_duplicates_not_conflicts() -> None:
    base = {
        "claim_id": "claim_identity",
        "claim_text": "身份只到怀疑层。",
        "cognitive_subjects": ["沈清漪"],
        "cognitive_object": "玄昱的身份",
        "cognitive_level": "suspicion",
        "action_level": "hinted",
        "reader_awareness": "partial",
        "character_knowledge_coverage": {"沈清漪": "partial"},
    }
    projected = project_cognitive_constraints(
        [
            {**base, "evidence": "证据 A"},
            {**base, "evidence": "证据 B"},
            {**base, "cognitive_level": "confirmed", "evidence": "冲突证据"},
        ]
    )

    assert len(projected) == 2
    assert [item["cognitive_level"] for item in projected] == ["suspicion", "confirmed"]
    assert projected[0]["character_knowledge_coverage"] == {"沈清漪": "partial"}


def test_source_world_rule_card_replaces_duplicate_full_rule_channel() -> None:
    contract_card = {
        "required_events": ["见到铃声证据"],
        "world_rules": ["全局规则 A", "全局规则 B"],
    }
    source_cards = {
        "chapter_contract": {"required_events": ["见到铃声证据"]},
        "world_rule_card": {
            "always_on": [{"rule": {"rule_id": "rule_a", "content": "全局规则 A"}}],
            "relevant_rules": [],
        },
    }

    merged = _merge_source_runtime_contract(contract_card, source_cards)
    fallback = _merge_source_runtime_contract(
        contract_card,
        {"chapter_contract": {"required_events": ["见到铃声证据"]}},
    )

    assert "world_rules" not in merged
    assert fallback["world_rules"] == ["全局规则 A", "全局规则 B"]


def test_reading_power_repair_cards_receive_contract_plan_and_knowledge() -> None:
    packet = _ns(
        chapter_number=6,
        chapter_contract={
            "required_events": ["令昭确认账簿残页真伪"],
            "forbidden_changes": ["不得让沈鹤卿直接承认真相"],
            "exit_state_targets": ["令昭获得下一步线索"],
            "knowledge_ops": [{"character": "令昭", "target_text": "确认残页上的暗记"}],
        },
        canon_context={},
        character_profiles=[{"name": "令昭", "role": "女主"}],
    )
    outline = _ns(
        chapter_number=6,
        goal="确认残页暗记",
        pov_character="令昭",
        involved_characters=["令昭"],
    )
    plan = _ns(
        closing_contract="章尾必须留下下一处账房地址",
        scene_intents=[],
        forbidden_elements=["冷光"],
        forbidden_elements_soft=[],
    )
    bridge = _ns(pending_questions=["残页暗记指向哪里"], causal_link={})
    kernel_context = {
        "entities": [{"entity_id": "c_lingzhao", "name": "令昭"}],
        "knowledge_ledger": [
            {
                "entity_id": "c_lingzhao",
                "knowledge_type": "known",
                "fact": "令昭已经见过残页暗记",
                "revealed_in_chapter": 5,
            }
        ],
    }

    cards = build_repair_cards(
        stage="reading_power_repair",
        packet=packet,
        chapter_outline=outline,
        bridge=bridge,
        plan=plan,
        kernel_context=kernel_context,
    )

    assert cards["contract"]["required_events"] == ["令昭确认账簿残页真伪"]
    assert cards["plan"]["closing_contract"] == "章尾必须留下下一处账房地址"
    assert cards["bridge"]["pending_questions"] == ["残页暗记指向哪里"]
    assert cards["knowledge"]["character_cards"][0]["character"] == "令昭"


def test_editorial_voice_contract_reaches_plan_and_draft_prompts() -> None:
    packet = _ns(
        chapter_number=2,
        chapter_contract={"required_events": ["令昭发现账簿残页"]},
        must_carry_forward=[],
        guard_constraints=[],
        canon_context={},
        character_profiles=[{"name": "令昭", "role": "女主"}],
    )
    outline = _ns(
        chapter_number=2,
        title="暗账",
        goal="让令昭拿到账簿残页",
        pov_character="令昭",
        involved_characters=["令昭"],
        setting="雨夜巷口",
        expected_word_count=1200,
        main_plot_points=["发现账簿残页"],
        subplot_points=[],
        beats_summary=[],
    )
    plan = _ns(
        opening_contract="前300字续接门外脚步声",
        closing_contract="令昭带残页离开",
        scene_intents=[
            _ns(
                scene_id="scene_01",
                summary="令昭避开门外搜查",
                purpose="承接上章危机",
                conflict="不能被搜到残页",
                required_characters=["令昭"],
                character_motivations=[],
                entry_state_refs=[],
                required_outcome="藏起残页",
                exit_target_state="暂时脱身",
                location="巷口",
                time_marker="当夜",
                sensory_notes="雨声",
                dialogue_voice_targets={"令昭": "短句，先压住事实再反问。"},
                target_words=1200,
            )
        ],
    )

    contract = _editorial_contract_payload()
    plan_cards = build_plan_cards(
        packet=packet,
        chapter_outline=outline,
        editorial_contract=contract,
    )
    draft_cards = build_draft_cards(
        packet=packet,
        chapter_outline=outline,
        plan=plan,
        editorial_contract=contract,
    )

    assert plan_cards["editorial"]["character_voices"][0]["character"] == "令昭"
    assert len(plan_cards["editorial"]["character_voices"]) == 1
    # DRAFT stage must NOT receive character_voices (6-phase long-form
    # architecture: voice is a WAVE responsibility, not a DRAFT one).
    assert draft_cards["editorial"].get("character_voices", []) == []

    builder = PromptBuilder()
    plan_prompt = builder.render(TaskType.PLAN_CHAPTER, {"stage_cards": plan_cards})
    draft_prompt = builder.render(
        TaskType.DRAFT_CHAPTER,
        {"stage_cards": draft_cards, "target_word_count": 1200, "weak_senses": []},
    )

    assert "角色声纹目标" in plan_prompt
    assert "令昭：短句，先压住事实再反问。" in plan_prompt
    # The "对白声纹" token now appears in DRAFT via scene_intent's
    # dialogue_voice_targets (upstream quality: DRAFT sees per-scene voice targets).
    assert "对白声纹" in draft_prompt
    # "长篇自白" comes from character_voices.taboo_patterns which is still
    # excluded from DRAFT (voice profiles remain a WAVE/PLAN responsibility).
    assert "长篇自白" not in draft_prompt


def test_draft_prompt_renders_routed_pov_voice() -> None:
    builder = PromptBuilder()
    prompt = builder.render(
        TaskType.DRAFT_CHAPTER,
        {
            "target_word_count": 1200,
            "stage_cards": {
                "chapter": {
                    "chapter_number": 2,
                    "title": "暗账",
                    "goal": "拿到账簿残页",
                    "pov_character": "令昭",
                    "target_word_count": 1200,
                },
                "contract": {},
                "plan": {
                    "scene_intents": [
                        {
                            "scene_id": "scene_01",
                            "summary": "令昭避开门外搜查",
                            "purpose": "承接上章危机",
                            "conflict": "不能被搜到残页",
                            "required_characters": ["令昭"],
                            "character_motivations": [],
                            "entry_state_refs": [],
                            "required_outcome": "藏起残页",
                            "exit_target_state": "暂时脱身",
                            "target_words": 1200,
                            "pov_character": "令昭",
                            "pov_knowledge_constraints": {},
                        }
                    ],
                },
                "characters": [
                    {
                        "name": "令昭",
                        "role": "女主",
                        "identity": "被卷入账簿案的人",
                        "personality": "克制警觉",
                        "voice": "短句，先压住事实再反问。",
                    }
                ],
            },
        },
    )

    assert "声纹=短句，先压住事实再反问。" in prompt


def test_wave_prompt_renders_editorial_voice_samples() -> None:
    packet = _ns(
        chapter_number=2,
        chapter_contract={"required_events": ["令昭发现账簿残页"]},
        must_carry_forward=[],
        guard_constraints=[],
        canon_context={},
        character_profiles=[
            {
                "name": "令昭",
                "role": "女主",
                "identity": "被卷入账簿案的人",
                "voice": "短句，先压住事实再反问。",
            }
        ],
    )
    outline = _ns(
        chapter_number=2,
        title="暗账",
        goal="让令昭拿到账簿残页",
        pov_character="令昭",
        involved_characters=["令昭"],
        setting="雨夜巷口",
        expected_word_count=1200,
        main_plot_points=["发现账簿残页"],
        subplot_points=[],
        beats_summary=[],
    )
    bridge = _ns(
        action_handoff="她仍按着门闩",
        causal_link=_ns(
            previous_event="门外脚步声逼近",
            causal_mechanism="逼迫令昭立刻藏起账簿",
            unresolved_question="门外是谁",
            open_threads=[],
        ),
    )
    plan = _ns(
        opening_contract="前300字续接门外脚步声",
        closing_contract="令昭带残页离开",
        scene_intents=[
            _ns(
                scene_id="scene_01",
                summary="令昭避开门外搜查",
                purpose="承接上章危机",
                conflict="不能被搜到残页",
                required_characters=["令昭"],
                character_motivations=[],
                entry_state_refs=[],
                required_outcome="藏起残页",
                exit_target_state="暂时脱身",
                target_words=1200,
                pov_character="令昭",
                pov_knowledge_constraints={},
            )
        ],
        cross_scene_intent={
            "cross_scene_references": [
                {
                    "from_scene": "scene_01",
                    "to_scene": "scene_01",
                    "ref_type": "echo",
                    "description": "账簿残页形成回环",
                }
            ],
            "pacing_curve": [4],
        },
    )
    cards = build_wave_cards(
        packet=packet,
        chapter_outline=outline,
        bridge=bridge,
        plan=plan,
        editorial_contract=_editorial_contract_payload(),
    )

    prompt = PromptBuilder().render(
        TaskType.WAVE_CHAPTER,
        {
            "stage_cards": cards,
            "draft_text": "令昭按着账簿残页，听见门外脚步停下。",
            "target_word_count": 1200,
        },
    )

    assert "编辑声纹契约（P1）" in prompt
    assert "账不对。" in prompt
    assert "禁止原句复用" in prompt
    assert "长篇自白" in prompt


def test_repair_cards_include_lessons_only_in_repair_stage() -> None:
    draft_cards = build_stage_cards(
        stage="draft",
        memory_hints={"critique_context": "旧问题"},
    )
    repair_cards = build_stage_cards(
        stage="continuity_repair",
        memory_hints={"critique_context": "旧问题"},
    )
    edit_cards = build_stage_cards(
        stage="edit",
        memory_hints={"critique_context": "旧问题"},
    )

    assert "repair_lessons" not in draft_cards.get("memory", {})
    # repair / edit use independent memory paths (memory_guidance / memory_context),
    # not the stage_cards.memory capsule — capsule is empty or absent
    assert "repair_lessons" not in repair_cards.get("memory", {})
    assert "repair_lessons" not in edit_cards.get("memory", {})


def test_subjective_quality_hints_are_stage_scoped() -> None:
    hint = {
        "chapter_hook": "用门外脚步开钩",
        "recommended_hook_type": "suspense",
        "hook_type_constraint": "开头必须承接上一章危机",
        "tension_target": 7.5,
        "outline_expected_hook": {"type": "suspense", "target": "门外是谁"},
        "in_chapter_payoffs": ["回收脚步声"],
        "force_resolve_suspense": ["门外脚步声"],
        "payoff_guidance": "用行动回收，不要解释性独白",
        "strand_recommendation": "推进账簿线",
        "turning_point_warning": "避免中段停滞",
    }

    bridge_cards = build_stage_cards(stage="bridge", reading_power_hint=hint)
    draft_cards = build_stage_cards(
        stage="draft",
        reading_power_hint=hint,
        weak_senses=["触觉"],
    )
    edit_cards = build_stage_cards(
        stage="edit",
        reading_power_hint=hint,
        weak_senses=["嗅觉"],
    )

    assert bridge_cards["quality"] == {
        "hook_type_constraint": "开头必须承接上一章危机",
        "force_resolve_suspense": ["门外脚步声"],
    }
    assert "chapter_hook" not in bridge_cards["quality"]
    assert "in_chapter_payoffs" not in bridge_cards["quality"]
    assert "outline_expected_hook" not in bridge_cards["quality"]
    assert "strand_recommendation" not in bridge_cards["quality"]

    assert draft_cards["quality"] == {
        "chapter_hook": "用门外脚步开钩",
        "hook_type_constraint": "开头必须承接上一章危机",
        "in_chapter_payoffs": ["回收脚步声"],
        "force_resolve_suspense": ["门外脚步声"],
        "payoff_guidance": "用行动回收，不要解释性独白",
        "turning_point_warning": "避免中段停滞",
        "weak_senses": ["触觉"],
    }

    assert edit_cards["quality"] == {
        "force_resolve_suspense": ["门外脚步声"],
        "payoff_guidance": "用行动回收，不要解释性独白",
        "turning_point_warning": "避免中段停滞",
        "weak_senses": ["嗅觉"],
    }


def test_plan_cards_include_lightweight_characters_with_dynamic_status() -> None:
    packet = _ns(
        chapter_contract={},
        must_carry_forward=[],
        guard_constraints=[],
        character_profiles=[
            {
                "name": "程砚秋",
                "role": "合伙人",
                "gender": "女",
                "personality": "冷静谨慎",
                "backstory": "知晓部分怀表真相",
            }
        ],
    )
    outline = _ns(
        chapter_number=3,
        title="怀表记忆",
        goal="陆云峥找到古董店线索",
        pov_character="陆云峥",
        setting="办公室",
        expected_word_count=4200,
        main_plot_points=[],
        subplot_points=[],
        beats_summary=[],
    )

    cards = build_plan_cards(
        packet=packet,
        chapter_outline=outline,
        canon_context={"characters": {"程砚秋": {"social_status": "智云合伙人"}}},
    )

    assert cards["characters"][0]["name"] == "程砚秋"
    assert cards["characters"][0]["identity"] == "智云合伙人。合伙人"
    assert "personality" not in cards["characters"][0]


def test_plan_cards_format_forward_motif_guidance_for_prompt() -> None:
    cards = build_plan_cards(
        packet=_ns(),
        chapter_outline=_ns(chapter_number=8, title="回声", goal="收束旧线"),
        memory_hints={
            "forward_motif_guidance": {
                "dormant_callbacks": [
                    {
                        "name": "纸灰",
                        "chapters_since": 21,
                        "thematic_meaning": "秘密被烧毁后的残留证据",
                    }
                ],
                "plot_matched_motifs": [
                    {
                        "name": "金镯",
                        "match_reason": "与本章母女旧案收束相关",
                    }
                ],
            }
        },
    )

    assert cards["memory"]["forward_motif_guidance"] == [
        "长线参考：纸灰（已间隔21章）；可参考：秘密被烧毁后的残留证据",
        "剧情参考：金镯；可参考：与本章母女旧案收束相关",
    ]


def test_plan_cards_filter_conceptual_forward_motif_guidance() -> None:
    cards = build_plan_cards(
        packet=_ns(),
        chapter_outline=_ns(chapter_number=8, title="回声", goal="收束旧线"),
        memory_hints={
            "forward_motif_guidance": {
                "plot_matched_motifs": [
                    {
                        "name": "金镯",
                        "category": "符号",
                        "match_reason": "与本章母女旧案收束相关",
                    },
                    {
                        "name": "雨声",
                        "category": "声音",
                        "match_reason": "与本章氛围相关",
                    },
                ],
            }
        },
    )

    assert cards["memory"]["forward_motif_guidance"] == [
        "剧情参考：雨声；可参考：与本章氛围相关",
    ]


def test_plan_element_progress_records_normalize_required_prompt_fields() -> None:
    cards = build_plan_cards(
        packet=_ns(),
        chapter_outline=_ns(
            chapter_number=2,
            title="渡口",
            goal="推进第二章计划",
            notes="【自动修复提示】Bridge 必须解释移动过程",
            pov_character="令昭",
            setting="码头",
            expected_word_count=4200,
        ),
        element_progress_hint={
            "summary": "近几章要素执行待加强：建议优先关注 romance_emotional_barriers",
            "recommended_focus_ids": ["romance_emotional_barriers"],
            "recent_missed": [
                {
                    "element_id": "romance_emotional_barriers",
                    "last_chapter": 1,
                    "count": 1,
                }
            ],
            "recent_weak": [
                {
                    "element_id": "mystery_clue_ledger",
                    "last_chapter": 1,
                    "count": 1,
                    "latest_reason": "规则信号：quality_negative_match",
                }
            ],
        },
    )

    assert cards["element"]["recent_missed"][0]["latest_reason"] == "未记录具体原因"
    prompt = PromptBuilder().render(TaskType.PLAN_CHAPTER, {"stage_cards": cards})
    assert "Bridge 必须解释移动过程" in prompt
    assert "近期未命中：romance_emotional_barriers（第1章；未记录具体原因）" in prompt
    assert "近期弱命中：mystery_clue_ledger（第1章；规则信号：quality_negative_match）" in prompt


def test_draft_prompt_renders_sparse_stage_card_records_without_missing_fields() -> None:
    packet = _ns(
        chapter_number=2,
        chapter_contract={"required_events": ["找到怀表刻字"]},
        must_carry_forward=[],
        guard_constraints=[],
        canon_context={
            "authoritative_narrative_state": {
                "pending_items": [{"candidate_id": "state-001"}],
            },
            "entity_reference_graph": {
                "identity_links": [{"source": "怀表", "target": "金镯"}],
            },
        },
        character_profiles=[{"name": "陆云峥", "role": "男主"}],
    )
    outline = _ns(
        chapter_number=2,
        title="至暗时刻",
        goal="找到怀表刻字",
        pov_character="陆云峥",
        setting="办公室",
        expected_word_count=1200,
        main_plot_points=[],
        subplot_points=[],
        beats_summary=[],
    )
    plan = _ns(
        opening_contract="陆云峥回到办公室，指尖触到怀表。",
        closing_contract="确认刻字线索",
        scene_intents=[
            _ns(
                scene_id="scene_01",
                summary="陆云峥确认怀表刻字",
                required_outcome="确认刻字线索",
                exit_target_state="开始调查",
            )
        ],
        expression_channel_records=[
            {
                "channel": "action_tag",
                "channel_id": "eye",
                "text": "眼神闪动",
                "replacement_axes": ["物件操作"],
                "allowed_when": "核心试探时可保留一次。",
            },
        ],
    )

    cards = build_draft_cards(
        packet=packet,
        chapter_outline=outline,
        plan=plan,
        canon_context=packet.canon_context,
        memory_hints={
            "expression_channel_records": [
                {
                    "channel": "action_tag",
                    "channel_id": "breath",
                    "text": "屏住呼吸",
                    "replacement_axes": ["对白停顿"],
                    "allowed_when": "生死关头可保留一次。",
                }
            ]
        },
        element_selection={
            "required_elements": [{"element_id": "mystery", "name": "悬疑"}],
        },
    )

    prompt = PromptBuilder().render(
        TaskType.DRAFT_CHAPTER,
        {"stage_cards": cards, "target_word_count": 1200, "weak_senses": []},
    )

    assert "眼神闪动" in prompt
    assert "屏住呼吸" in prompt
    assert "物件操作" in prompt
    assert "生死关头可保留一次" in prompt
    assert "怀表 --related--> 金镯" in prompt


def test_stage_cards_project_story_kernel_knowledge_boundaries() -> None:
    packet = _ns(
        chapter_number=2,
        chapter_contract={
            "knowledge_ops": [
                {
                    "type": "known",
                    "character": "令昭",
                    "description": "账簿残页是真的",
                    "evidence": "残页是真的",
                }
            ]
        },
        must_carry_forward=[],
        guard_constraints=[],
        canon_context={},
        character_profiles=[{"name": "令昭", "role": "女主"}],
    )
    outline = _ns(
        chapter_number=2,
        title="暗账",
        goal="验证账簿残页",
        pov_character="令昭",
        involved_characters=["令昭"],
        setting="雨夜巷口",
        expected_word_count=1200,
        main_plot_points=[],
        subplot_points=[],
        beats_summary=[],
    )
    kernel_context = {
        "entities": [
            {
                "entity_id": "char_ling_zhao",
                "name": "令昭",
                "entity_type": "character",
            }
        ],
        "knowledge_ledger": [
            {
                "entry_id": "k1",
                "entity_id": "char_ling_zhao",
                "fact": "沈鹤卿不在京城",
                "knowledge_type": "known",
            }
        ],
    }

    cards = build_stage_cards(
        stage="draft",
        packet=packet,
        chapter_outline=outline,
        kernel_context=kernel_context,
    )

    assert cards["knowledge"]["source"] == "story_kernel"
    assert cards["knowledge"]["character_cards"][0]["known_facts"] == ["沈鹤卿不在京城"]
    assert (
        cards["knowledge"]["character_cards"][0]["chapter_promised_changes"][0]["target_text"]
        == "令昭获知：账簿残页是真的"
    )

    prompt = PromptBuilder().render(
        TaskType.DRAFT_CHAPTER,
        {"stage_cards": cards, "target_word_count": 1200, "weak_senses": []},
    )

    assert "角色知识边界" in prompt
    assert "沈鹤卿不在京城" in prompt
    assert "令昭获知：账簿残页是真的" in prompt


def test_plan_prompt_renders_sparse_causal_link_without_missing_fields() -> None:
    outline = _ns(
        chapter_number=4,
        title="渡口",
        goal="承接上一章余波",
        pov_character="沈念卿",
        setting="渡口",
        expected_word_count=1600,
        main_plot_points=[],
        subplot_points=[],
        beats_summary=[],
    )
    bridge = _ns(
        opening_time="清晨",
        opening_location="渡口",
        opening_pov="沈念卿",
        action_handoff="沈念卿接到电话。",
        causal_link=_ns(
            previous_event="上一章电话铃响起",
            causal_mechanism="",
            unresolved_question="",
            open_threads=[],
        ),
    )

    cards = build_stage_cards(stage="plan", chapter_outline=outline, bridge=bridge)

    assert cards["bridge"]["causal_link"] == {
        "previous_event": "上一章电话铃响起",
        "causal_mechanism": "",
        "unresolved_question": "",
        "open_threads": [],
    }
    prompt = PromptBuilder().render(TaskType.PLAN_CHAPTER, {"stage_cards": cards})
    assert "上一章电话铃响起 →" in prompt


def test_causal_repair_prompt_renders_sparse_top_level_causal_link() -> None:
    prompt = PromptBuilder().render(
        TaskType.REPAIR_CAUSAL,
        {
            "chapter_number": 6,
            "chapter_text": "旧正文",
            "numbered_chapter_text": "",
            "typed_issues": {
                "opening_causal_gap": [
                    {
                        "summary": "开头承接断裂",
                        "location": "开头",
                        "evidence": "首段直接跳场",
                        "fix_suggestion": "补足上章余波",
                    }
                ]
            },
            "causal_link": {"previous_event": "上一章刚发生争执"},
            "previous_chapter_ending": "",
            "word_count_min": 10,
            "word_count_max": 80,
            "memory_guidance": {},
            "repair_attempt_guidance": {},
            "escalation_note": "",
            "character_whitelist_note": "",
            "chapter_plan_scenes": [],
            "stage_cards": {"bridge": {}},
        },
    )

    assert "上一章刚发生争执" in prompt
    assert "因果机制：" in prompt


def test_prompt_builder_causal_link_defaults_preserve_extra_context() -> None:
    normalized = PromptBuilder._normalize_causal_link(
        {
            "previous_event": "上一章刚发生追击",
            "character_profiles": [{"name": "沈念卿"}],
            "memory_guidance": {"warning": "避免重复旧修法"},
        }
    )

    assert normalized["causal_mechanism"] == ""
    assert normalized["unresolved_question"] == ""
    assert normalized["open_threads"] == []
    assert normalized["character_profiles"] == [{"name": "沈念卿"}]
    assert normalized["memory_guidance"] == {"warning": "避免重复旧修法"}


def test_repair_card_preserves_contract_and_blocks_new_pov() -> None:
    packet = _ns(
        must_carry_forward=["承接门外脚步声"],
        chapter_contract={"forbidden_changes": ["禁止沈鹤卿在本章出场"]},
    )
    cards = build_repair_cards(stage="continuity_repair", packet=packet)

    assert "承接门外脚步声" in cards["repair"]["preserve"]
    assert "禁止沈鹤卿在本章出场" in cards["repair"]["do_not_introduce"]
    assert "新 POV 段" in cards["repair"]["do_not_introduce"]


def test_stage_cards_do_not_truncate_or_limit_selected_source_fields() -> None:
    long_event = (
        "女主必须发现完整账簿残页，并确认残页背面的暗号与上一章铜铃声对应，不能省略暗号来源。" * 8
    )
    all_events = [f"事件{i}" for i in range(20)]
    packet = _ns(
        chapter_contract={
            "required_events": [long_event, *all_events],
            "forbidden_changes": ["禁止沈鹤卿在本章出场"],
        },
        must_carry_forward=[],
        guard_constraints=[],
        canon_context={"immutable_facts": [long_event]},
    )
    outline = _ns(
        chapter_number=3,
        title="长契约",
        goal=long_event,
        pov_character="令昭",
        setting="密室",
        expected_word_count=5000,
        main_plot_points=all_events,
        subplot_points=[],
        beats_summary=[],
    )
    bridge = _ns(
        action_handoff=long_event,
        causal_link=_ns(
            previous_event=long_event,
            causal_mechanism=long_event,
            unresolved_question=long_event,
            open_threads=all_events,
        ),
    )
    plan = _ns(
        scene_intents=[],
        opening_contract="",
        closing_contract="",
        opening_bridge={
            "action_handoff": long_event,
            "causal_link": {
                "previous_event": long_event,
                "causal_mechanism": long_event,
                "unresolved_question": long_event,
                "open_threads": all_events,
            },
        },
    )

    cards = build_stage_cards(
        stage="draft",
        packet=packet,
        chapter_outline=outline,
        bridge=bridge,
        plan=plan,
    )

    assert cards["chapter"]["goal"] == long_event
    assert cards["contract"]["required_events"][0] == long_event
    assert len(cards["contract"]["required_events"]) == 21
    assert "bridge" not in cards
    assert cards["plan"]["opening_bridge"]["action_handoff"] == long_event
    assert cards["plan"]["opening_bridge"]["causal_link"]["causal_mechanism"] == long_event
    assert cards["plan"]["opening_bridge"]["causal_link"]["open_threads"] == all_events


def test_draft_cards_keep_atomic_ownership_and_executable_scene_handoffs() -> None:
    outline = _ns(chapter_number=3, goal="取回账簿", pov_character="令昭")
    plan = _ns(
        scene_intents=[
            {
                "scene_id": "scene_01",
                "summary": "令昭潜入账房",
                "required_outcome": "取得残页；避开守卫",
                "owned_events": ["取得残页；避开守卫"],
                "owned_revelations": ["残页上有暗号"],
                "owned_state_changes": ["令昭从空手变为持有残页"],
                "forbidden_overlap": ["不得提前揭示暗号含义"],
                "scene_goal": "取得残页并保持隐蔽",
                "handoff_to_next": "令昭把残页收入袖中",
                "entry_state": "令昭尚未进入账房",
                "exit_state": "令昭持有残页",
                "draft_order": 2,
            }
        ],
        opening_contract="",
        closing_contract="",
    )

    cards = build_stage_cards(stage="draft", chapter_outline=outline, plan=plan)
    scene = cards["plan"]["scene_intents"][0]

    assert scene["owned_events"] == ["取得残页", "避开守卫"]
    assert scene["owned_revelations"] == ["残页上有暗号"]
    assert scene["owned_state_changes"] == ["令昭从空手变为持有残页"]
    assert scene["forbidden_overlap"] == ["不得提前揭示暗号含义"]
    assert scene["scene_goal"] == "取得残页并保持隐蔽"
    assert scene["handoff_to_next"] == "令昭把残页收入袖中"
    assert scene["entry_state"] == "令昭尚未进入账房"
    assert scene["exit_state"] == "令昭持有残页"
    assert scene["draft_order"] == 2
    assert "dependency_scene_ids" not in scene
    assert "parallel_group" not in scene


def test_draft_cards_do_not_fallback_to_raw_bridge_for_opening_bridge() -> None:
    packet = _ns(chapter_contract={}, must_carry_forward=[], canon_context={})
    outline = _ns(chapter_number=3, goal="追查账簿", pov_character="令昭")
    bridge = _ns(action_handoff="不应进入 DRAFT 的 raw bridge")
    plan = _ns(scene_intents=[], opening_contract="", closing_contract="")

    cards = build_stage_cards(
        stage="draft",
        packet=packet,
        chapter_outline=outline,
        bridge=bridge,
        plan=plan,
    )

    assert "bridge" not in cards
    assert "opening_bridge" not in cards["plan"]


def test_card_first_draft_prompt_does_not_render_legacy_context_dump() -> None:
    registry = PromptRegistry()
    prompt = registry.render(
        TaskType.DRAFT_CHAPTER,
        stage_cards={
            "chapter": {
                "chapter_number": 2,
                "title": "暗账",
                "goal": "拿到账簿残页",
                "pov_character": "令昭",
                "target_word_count": 4200,
            },
            "contract": {
                "required_events": ["发现账簿残页"],
                "forbidden_changes": ["禁止沈鹤卿在本章出场"],
            },
            "bridge": {"action_handoff": "她仍按着门闩"},
            "plan": {
                "scene_intents": [
                    {
                        "scene_id": "scene_01",
                        "summary": "令昭避开搜查",
                        "required_outcome": "藏起残页",
                        "scene_goal": "保住残页并摆脱搜查",
                        "forbidden_overlap": ["不得提前揭示搜查者身份"],
                        "handoff_to_next": "令昭转入巷口",
                        "entry_state": "搜查逼近",
                        "exit_state": "暂时脱身",
                        "draft_order": 1,
                        "exit_target_state": "暂时脱身",
                    }
                ]
            },
        },
        target_word_count=4200,
        weak_senses=[],
    )

    assert "P0 执行卡" in prompt
    assert "禁止沈鹤卿在本章出场" in prompt
    assert "保住残页并摆脱搜查" in prompt
    assert "不得提前揭示搜查者身份" in prompt
    assert "令昭转入巷口" in prompt
    assert "搜查逼近" in prompt
    assert "记忆约束（★ 必须回应）" not in prompt


# ---------------------------------------------------------------------------
# Task 1+2: Temporal + visibility filtering in _build_knowledge_card
# ---------------------------------------------------------------------------


def _make_knowledge_entry(
    entity_id: str,
    fact: str,
    knowledge_type: str = "known",
    source_chapter: int = 0,
    revealed_in_chapter: int = 0,
    visibility: str = "private",
) -> dict:
    """Helper to build a knowledge ledger entry with temporal/visibility fields."""
    return {
        "entry_id": f"k_{entity_id}_{fact[:8]}",
        "entity_id": entity_id,
        "fact": fact,
        "knowledge_type": knowledge_type,
        "source_chapter": source_chapter,
        "revealed_in_chapter": revealed_in_chapter,
        "visibility": visibility,
    }


def _make_kernel_context(
    entities: list[dict] | None = None,
    knowledge_ledger: list[dict] | None = None,
) -> dict:
    """Helper to build a kernel_context dict for _build_knowledge_card."""
    return {
        "entities": entities or [],
        "knowledge_ledger": knowledge_ledger or [],
    }


def _make_outline_for_knowledge(
    chapter_number: int = 3,
    pov_character: str = "令昭",
    involved_characters: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        chapter_number=chapter_number,
        title="测试章",
        goal="测试",
        pov_character=pov_character,
        involved_characters=involved_characters or [pov_character],
        setting="测试场景",
        expected_word_count=1200,
        main_plot_points=[],
        subplot_points=[],
        beats_summary=[],
    )


def test_knowledge_card_filters_future_source_chapter() -> None:
    """Entries with source_chapter > current_chapter should be excluded."""
    kernel = _make_kernel_context(
        entities=[{"entity_id": "char_lz", "name": "令昭", "entity_type": "character"}],
        knowledge_ledger=[
            _make_knowledge_entry("char_lz", "已知事实A", source_chapter=1),
            _make_knowledge_entry("char_lz", "未来事实B", source_chapter=5),
        ],
    )
    outline = _make_outline_for_knowledge(chapter_number=3, pov_character="令昭")

    result = _build_knowledge_card(
        kernel_context=kernel,
        chapter_contract={},
        outline=outline,
        plan=None,
        stage="draft",
        current_chapter=3,
        pov_character="令昭",
    )

    known_facts = result["character_cards"][0]["known_facts"]
    assert "已知事实A" in known_facts
    assert "未来事实B" not in known_facts


def test_knowledge_card_filters_private_entry_for_non_pov() -> None:
    """PRIVATE entries for entities other than POV character should be excluded."""
    kernel = _make_kernel_context(
        entities=[
            {"entity_id": "char_lz", "name": "令昭", "entity_type": "character"},
            {"entity_id": "char_hq", "name": "沈鹤卿", "entity_type": "character"},
        ],
        knowledge_ledger=[
            _make_knowledge_entry("char_hq", "沈鹤卿的秘密", visibility="private"),
            _make_knowledge_entry("char_hq", "沈鹤卿的公开信息", visibility="public"),
        ],
    )
    # outline focuses on 沈鹤卿 so he's in wanted_ids
    outline = _make_outline_for_knowledge(
        chapter_number=3,
        pov_character="令昭",
        involved_characters=["令昭", "沈鹤卿"],
    )

    result = _build_knowledge_card(
        kernel_context=kernel,
        chapter_contract={},
        outline=outline,
        plan=None,
        stage="draft",
        current_chapter=3,
        pov_character="令昭",
    )

    hq_card = next(c for c in result["character_cards"] if c["character"] == "沈鹤卿")
    assert "沈鹤卿的公开信息" in hq_card["known_facts"]
    assert "沈鹤卿的秘密" not in hq_card["known_facts"]


def test_knowledge_card_allows_pov_to_see_own_private_entries() -> None:
    """POV character should see their own PRIVATE entries."""
    kernel = _make_kernel_context(
        entities=[{"entity_id": "char_lz", "name": "令昭", "entity_type": "character"}],
        knowledge_ledger=[
            _make_knowledge_entry("char_lz", "令昭的私密心事", visibility="private"),
        ],
    )
    outline = _make_outline_for_knowledge(chapter_number=3, pov_character="令昭")

    result = _build_knowledge_card(
        kernel_context=kernel,
        chapter_contract={},
        outline=outline,
        plan=None,
        stage="draft",
        current_chapter=3,
        pov_character="令昭",
    )

    known_facts = result["character_cards"][0]["known_facts"]
    assert "令昭的私密心事" in known_facts


def test_knowledge_card_filters_unrevealed_entry() -> None:
    """Entries with revealed_in_chapter > 0 and > current_chapter should be excluded."""
    kernel = _make_kernel_context(
        entities=[{"entity_id": "char_lz", "name": "令昭", "entity_type": "character"}],
        knowledge_ledger=[
            _make_knowledge_entry("char_lz", "已揭示的真相", revealed_in_chapter=2),
            _make_knowledge_entry("char_lz", "未揭示的真相", revealed_in_chapter=10),
        ],
    )
    outline = _make_outline_for_knowledge(chapter_number=3, pov_character="令昭")

    result = _build_knowledge_card(
        kernel_context=kernel,
        chapter_contract={},
        outline=outline,
        plan=None,
        stage="draft",
        current_chapter=3,
        pov_character="令昭",
    )

    known_facts = result["character_cards"][0]["known_facts"]
    assert "已揭示的真相" in known_facts
    assert "未揭示的真相" not in known_facts


def test_knowledge_card_backward_compat_skips_temporal_when_chapter_zero() -> None:
    """When current_chapter=0, temporal filtering should be skipped."""
    kernel = _make_kernel_context(
        entities=[{"entity_id": "char_lz", "name": "令昭", "entity_type": "character"}],
        knowledge_ledger=[
            _make_knowledge_entry("char_lz", "未来事实", source_chapter=99),
        ],
    )
    outline = _make_outline_for_knowledge(chapter_number=3, pov_character="令昭")

    result = _build_knowledge_card(
        kernel_context=kernel,
        chapter_contract={},
        outline=outline,
        plan=None,
        stage="draft",
        current_chapter=0,
        pov_character="",
    )

    known_facts = result["character_cards"][0]["known_facts"]
    assert "未来事实" in known_facts


def test_knowledge_card_backward_compat_skips_visibility_when_pov_empty() -> None:
    """When pov_character is empty, visibility filtering should be skipped."""
    kernel = _make_kernel_context(
        entities=[{"entity_id": "char_lz", "name": "令昭", "entity_type": "character"}],
        knowledge_ledger=[
            _make_knowledge_entry("char_lz", "私密事实", visibility="private"),
        ],
    )
    outline = _make_outline_for_knowledge(chapter_number=3, pov_character="令昭")

    result = _build_knowledge_card(
        kernel_context=kernel,
        chapter_contract={},
        outline=outline,
        plan=None,
        stage="draft",
        current_chapter=0,
        pov_character="",
    )

    known_facts = result["character_cards"][0]["known_facts"]
    assert "私密事实" in known_facts


def test_knowledge_card_combined_filtering() -> None:
    """All three filters should combine with AND logic."""
    kernel = _make_kernel_context(
        entities=[
            {"entity_id": "char_lz", "name": "令昭", "entity_type": "character"},
            {"entity_id": "char_hq", "name": "沈鹤卿", "entity_type": "character"},
        ],
        knowledge_ledger=[
            # PASS all: source_ch=1<=3, visibility=public, revealed_in=0
            _make_knowledge_entry("char_lz", "公共事实", source_chapter=1, visibility="public"),
            # FAIL temporal: source_ch=5>3
            _make_knowledge_entry("char_lz", "未来事实", source_chapter=5, visibility="public"),
            # FAIL visibility: private + non-POV
            _make_knowledge_entry("char_hq", "鹤卿私事", source_chapter=1, visibility="private"),
            # FAIL revealed: revealed_in=10>3
            _make_knowledge_entry(
                "char_lz", "未揭示", source_chapter=1, visibility="public", revealed_in_chapter=10
            ),
            # PASS all: source_ch=2<=3, private but is POV, revealed_in=2<=3
            _make_knowledge_entry(
                "char_lz", "令昭秘密", source_chapter=2, visibility="private", revealed_in_chapter=2
            ),
        ],
    )
    outline = _make_outline_for_knowledge(
        chapter_number=3,
        pov_character="令昭",
        involved_characters=["令昭", "沈鹤卿"],
    )

    result = _build_knowledge_card(
        kernel_context=kernel,
        chapter_contract={},
        outline=outline,
        plan=None,
        stage="draft",
        current_chapter=3,
        pov_character="令昭",
    )

    lz_card = next(c for c in result["character_cards"] if c["character"] == "令昭")
    assert "公共事实" in lz_card["known_facts"]
    assert "未来事实" not in lz_card["known_facts"]
    assert "未揭示" not in lz_card["known_facts"]
    assert "令昭秘密" in lz_card["known_facts"]

    hq_card = next(c for c in result["character_cards"] if c["character"] == "沈鹤卿")
    assert "鹤卿私事" not in hq_card.get("known_facts", [])


def test_knowledge_card_via_stage_cards_applies_filters() -> None:
    """Integration: build_stage_cards should thread filters through to _build_knowledge_card."""
    kernel = _make_kernel_context(
        entities=[{"entity_id": "char_lz", "name": "令昭", "entity_type": "character"}],
        knowledge_ledger=[
            _make_knowledge_entry("char_lz", "已知事实", source_chapter=1, visibility="public"),
            _make_knowledge_entry("char_lz", "未来事实", source_chapter=10, visibility="public"),
        ],
    )
    packet = _ns(
        chapter_number=3,
        chapter_contract={},
        must_carry_forward=[],
        guard_constraints=[],
        canon_context={},
        character_profiles=[{"name": "令昭", "role": "女主"}],
    )
    outline = _make_outline_for_knowledge(chapter_number=3, pov_character="令昭")

    cards = build_stage_cards(
        stage="draft",
        packet=packet,
        chapter_outline=outline,
        kernel_context=kernel,
    )

    known_facts = cards["knowledge"]["character_cards"][0]["known_facts"]
    assert "已知事实" in known_facts
    assert "未来事实" not in known_facts


def test_bridge_opening_evidence_is_complete_and_respects_knowledge_boundaries() -> None:
    """Bridge receives all scoped opening facts, never hidden/future material."""

    packet = _ns(
        chapter_number=3,
        chapter_contract={
            "cognitive_constraints": [
                {
                    "claim_id": "claim_bridge",
                    "cognitive_subjects": ["令昭"],
                    "cognitive_object": "账簿残页真相",
                    "cognitive_level": "suspicion",
                    "action_level": "internal",
                    "reader_awareness": "partial",
                    "character_knowledge_coverage": {"令昭": "partial"},
                }
            ]
        },
        must_carry_forward=[],
    )
    outline = _make_outline_for_knowledge(
        chapter_number=3,
        pov_character="令昭",
        involved_characters=["令昭", "沈鹤卿", "陆云峥", "秦曜"],
    )
    kernel = {
        "entities": [
            {"entity_id": "char_lz", "name": "令昭", "entity_type": "character"},
            {"entity_id": "char_sh", "name": "沈鹤卿", "entity_type": "character"},
            {"entity_id": "char_ly", "name": "陆云峥", "entity_type": "character"},
            {"entity_id": "char_qy", "name": "秦曜", "entity_type": "character"},
        ],
        "relationships": [
            {
                "source_entity_id": "char_lz",
                "target_entity_id": "char_sh",
                "label": "互相试探",
                "status": "strained",
                "shift_summary": "账簿残页让信任下降",
                "last_shift_chapter": 3,
            },
            {
                "source_entity_id": "char_lz",
                "target_entity_id": "char_ly",
                "label": "盟友",
                "last_shift_chapter": 2,
            },
            {
                "source_entity_id": "char_lz",
                "target_entity_id": "char_qy",
                "label": "旧识",
                "last_shift_chapter": 1,
            },
        ],
        "knowledge_ledger": [
            _make_knowledge_entry("char_lz", "账簿残页指向库房", source_chapter=2),
            _make_knowledge_entry("char_lz", "沈鹤卿可能在撒谎", "suspected", source_chapter=3),
            _make_knowledge_entry("char_lz", "不可泄露的真相", "secret_kept", source_chapter=2),
            _make_knowledge_entry("char_lz", "第4章才可见的事实", source_chapter=4),
        ],
        "promise_ledger": [
            {
                "description": "库房钥匙的来源",
                "promise_type": "suspense",
                "status": "hinted",
                "payoff_chapter": 3,
            },
            {
                "description": "第六章才兑现的身份线索",
                "promise_type": "foreshadow",
                "status": "planted",
                "payoff_chapter": 6,
            },
        ],
    }

    cards = build_stage_cards(
        stage="bridge",
        packet=packet,
        chapter_outline=outline,
        canon_context=kernel,
    )

    evidence = cards["opening_evidence"]
    assert len(evidence["relationship_shifts"]) == 3
    assert evidence["relationship_shifts"][0]["with_character"] == "沈鹤卿"
    assert {item["fact"] for item in evidence["pov_knowledge"]} == {
        "账簿残页指向库房",
        "沈鹤卿可能在撒谎",
    }
    assert "不可泄露的真相" not in str(evidence)
    assert "第4章才可见的事实" not in str(evidence)
    assert evidence["due_promises"] == [
        {"description": "库房钥匙的来源", "type": "suspense", "payoff_chapter": 3}
    ]

    prompt = PromptBuilder().render(TaskType.BRIDGE_CHAPTER, {"stage_cards": cards})
    assert "开场决策证据（P0，最小必要集）" in prompt
    assert "账簿残页指向库房" in prompt
    assert "claim_id=claim_bridge" in prompt
    assert "character_knowledge_coverage=令昭=partial" in prompt
