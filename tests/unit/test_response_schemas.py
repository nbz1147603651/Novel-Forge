"""Tests for response_schemas — LLM output structural validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from novel_forge.common.constants import TaskType
from novel_forge.core.format_contracts import validate_json_output_contract
from novel_forge.core.parsing.response_schemas import (
    get_response_schema,
    validate_response_schema,
)
from novel_forge.core.schemas.book_consistency_verify import BookConsistencyVerifyResult
from scripts.audit_contract_envelope_alignment import (
    collect_envelope_alignment_rows,
    collect_mock_contract_rows,
)

# ---------------------------------------------------------------------------
# Registry coverage
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_main_chain_tasks_have_schemas(self) -> None:
        for tt in (
            TaskType.BRIDGE_CHAPTER,
            TaskType.PLAN_CHAPTER,
            TaskType.CHECK_ALIGNMENT,
            TaskType.CHECK_CONTINUITY,
            TaskType.VALIDATE_CAUSAL,
            TaskType.EXTRACT_CANON,
            TaskType.EXTRACT_MOTIFS,
            TaskType.ADJUST_OUTLINE,
            TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS,
            TaskType.ADJUDICATE_ENTITY_REFERENCES,
            TaskType.REFINE_INIT_ARTIFACTS_FROM_SYNOPSIS,
        ):
            assert get_response_schema(tt) is not None, f"Missing schema for {tt}"

    def test_auxiliary_json_tasks_have_schemas(self) -> None:
        for tt in (
            TaskType.ELEMENT_PROGRESS_ARBITER,
            TaskType.GUARD_CONSTRAINT_CHECK,
            TaskType.MACRO_GUARD_AUDIT,
            TaskType.KNOWLEDGE_BOUNDARY_AUDIT,
            TaskType.VOLUME_AUDIT,
            TaskType.CHECK_EDITORIAL,
            TaskType.BOOK_CONSISTENCY,
            TaskType.BOOK_CONSISTENCY_VERIFY,
            TaskType.BOOK_EDITORIAL_AUDIT,
            TaskType.TTS_BUILD_NARRATOR_PROFILE,
            TaskType.TTS_GENERATE_DUBBING_SCRIPT,
            TaskType.TTS_ADJUDICATE_SCRIPT_SEGMENTS,
            TaskType.TTS_REVIEW_DUBBING_SCRIPT,
            TaskType.TTS_ANALYZE_DUBBING_STYLE,
            TaskType.TTS_ADJUDICATE_VOICE_MATCH,
        ):
            assert get_response_schema(tt) is not None, f"Missing schema for {tt}"

    def test_text_tasks_have_no_schema(self) -> None:
        for tt in (TaskType.DRAFT_CHAPTER, TaskType.EDIT_CHAPTER, TaskType.DRAFT):
            assert get_response_schema(tt) is None

    def test_unregistered_task_returns_none(self) -> None:
        assert get_response_schema(TaskType.DRAFT_CHAPTER) is None

    def test_contract_required_keys_align_with_response_envelopes(self) -> None:
        mismatches = [row for row in collect_envelope_alignment_rows() if row.missing_required_keys]
        assert mismatches == []

    def test_mock_payloads_satisfy_runtime_contracts(self) -> None:
        mismatches = [row for row in collect_mock_contract_rows() if row.error]
        assert mismatches == []

    @pytest.mark.parametrize(
        "task_type",
        (
            TaskType.ADJUDICATE_CONTRACT_COMPLETION,
            TaskType.PLAN_OUTLINE,
            TaskType.EVALUATE_READING_POWER,
            TaskType.RECONCILE_ENTITIES,
            TaskType.REPAIR_STRATEGY_DIAGNOSE,
            TaskType.AUDIT_POV_DRIFT,
            TaskType.HUMANIZE_SCAN,
        ),
    )
    def test_recently_aligned_json_tasks_have_schemas(self, task_type: TaskType) -> None:
        assert get_response_schema(task_type) is not None, f"Missing schema for {task_type}"


# ---------------------------------------------------------------------------
# validate_response_schema: passthrough when no schema
# ---------------------------------------------------------------------------


class TestNoSchema:
    def test_no_op_for_unregistered_task(self) -> None:
        validate_response_schema({"anything": True}, TaskType.DRAFT_CHAPTER)


# ---------------------------------------------------------------------------
# Score fields: numeric coercion & rejection
# ---------------------------------------------------------------------------


class TestScoreCoercion:
    @pytest.mark.parametrize(
        "task_type, field",
        [
            (TaskType.CHECK_CONTINUITY, "continuity_score"),
            (TaskType.VALIDATE_CAUSAL, "causal_score"),
            (TaskType.CHECK_ALIGNMENT, "alignment_score"),
        ],
    )
    def test_numeric_score_accepted(self, task_type: TaskType, field: str) -> None:
        data = {field: 8.5, "issues": [], "summary": ""}
        validate_response_schema(data, task_type)

    @pytest.mark.parametrize(
        "task_type, field",
        [
            (TaskType.CHECK_CONTINUITY, "continuity_score"),
            (TaskType.VALIDATE_CAUSAL, "causal_score"),
            (TaskType.CHECK_ALIGNMENT, "alignment_score"),
        ],
    )
    def test_numeric_string_score_accepted(self, task_type: TaskType, field: str) -> None:
        data = {field: "8.5", "issues": [], "summary": ""}
        validate_response_schema(data, task_type)

    @pytest.mark.parametrize(
        "task_type, field",
        [
            (TaskType.CHECK_CONTINUITY, "continuity_score"),
            (TaskType.VALIDATE_CAUSAL, "causal_score"),
            (TaskType.CHECK_ALIGNMENT, "alignment_score"),
        ],
    )
    def test_non_numeric_score_rejected(self, task_type: TaskType, field: str) -> None:
        data = {field: "excellent", "issues": [], "summary": ""}
        with pytest.raises(ValidationError):
            validate_response_schema(data, task_type)


# ---------------------------------------------------------------------------
# List fields: coercion & rejection
# ---------------------------------------------------------------------------


class TestListCoercion:
    def test_list_passes(self) -> None:
        validate_response_schema(
            {"continuity_score": 9, "issues": [{"type": "x"}]},
            TaskType.CHECK_CONTINUITY,
        )

    def test_single_dict_coerced_to_list(self) -> None:
        validate_response_schema(
            {"continuity_score": 9, "issues": {"type": "x"}},
            TaskType.CHECK_CONTINUITY,
        )

    def test_none_coerced_to_empty_list(self) -> None:
        validate_response_schema(
            {"continuity_score": 9, "issues": None},
            TaskType.CHECK_CONTINUITY,
        )

    def test_integer_rejected_as_list(self) -> None:
        with pytest.raises(ValidationError):
            validate_response_schema(
                {"continuity_score": 9, "issues": 42},
                TaskType.CHECK_CONTINUITY,
            )


class TestCreativeDirectionCandidatesResponse:
    def test_nested_packet_shape_is_accepted(self) -> None:
        validate_response_schema(
            {
                "candidates": [
                    {
                        "candidate_id": "direction_a",
                        "packet": {
                            "emotional_engine": ["信任在选择中承压"],
                            "scene_potential": ["公开场合的两难选择"],
                        },
                        "preserved_intent_ids": ["user:premise"],
                    }
                ]
            },
            TaskType.INIT_CREATIVE_DIRECTION_CANDIDATES,
        )

    def test_flat_candidate_is_rejected_inside_format_retry_boundary(self) -> None:
        with pytest.raises(ValidationError, match="packet"):
            validate_response_schema(
                {
                    "candidates": [
                        {
                            "candidate_id": "direction_a",
                            "title": "模型自行猜测的旧形状",
                            "emotional_engine": "信任在选择中承压",
                            "preserved_intent_ids": ["user:premise"],
                        }
                    ]
                },
                TaskType.INIT_CREATIVE_DIRECTION_CANDIDATES,
            )


class TestTTSResponseSchemas:
    def test_narrator_profile_accepts_numeric_speed_string(self) -> None:
        validate_response_schema(
            {
                "voice_type": "warm",
                "base_speed": "1.05",
                "emotional_range": "moderate",
                "narration_distance": "medium",
                "style_keywords": ["沉稳", "清晰"],
            },
            TaskType.TTS_BUILD_NARRATOR_PROFILE,
        )

    def test_dubbing_script_rejects_non_list_collection(self) -> None:
        with pytest.raises(ValidationError):
            validate_response_schema(
                {
                    "segments": 42,
                    "bgm_suggestions": [],
                    "sfx_cues": [],
                    "soundscapes": [],
                    "scene_transitions": [],
                },
                TaskType.TTS_GENERATE_DUBBING_SCRIPT,
            )

    def test_speaker_adjudication_requires_summary(self) -> None:
        with pytest.raises(ValidationError):
            validate_response_schema(
                {"decisions": []},
                TaskType.TTS_ADJUDICATE_SCRIPT_SEGMENTS,
            )

    def test_voice_match_adjudication_requires_summary(self) -> None:
        with pytest.raises(ValidationError):
            validate_response_schema(
                {"decisions": []},
                TaskType.TTS_ADJUDICATE_VOICE_MATCH,
            )

    def test_dubbing_review_requires_coverage_and_verdict(self) -> None:
        with pytest.raises(ValidationError):
            validate_response_schema(
                {"decisions": [], "summary": "已审校"},
                TaskType.TTS_REVIEW_DUBBING_SCRIPT,
            )


# ---------------------------------------------------------------------------
# Dict fields: EXTRACT_CANON nested dicts
# ---------------------------------------------------------------------------


class TestDictCoercion:
    def test_valid_extract_canon(self) -> None:
        data = {
            "canon_delta": {"new_events": []},
            "creative_report": {"new_characters": []},
            "chapter_exit_state": {"pov": "张三"},
            "character_state_deltas": [],
            "relationship_deltas": [],
            "plot_thread_deltas": [],
            "structured_summary": "测试摘要",
        }
        validate_response_schema(data, TaskType.EXTRACT_CANON)

    def test_string_for_dict_field_rejected(self) -> None:
        data = {
            "canon_delta": "none",
            "creative_report": {},
            "chapter_exit_state": {},
        }
        with pytest.raises(ValidationError):
            validate_response_schema(data, TaskType.EXTRACT_CANON)

    def test_list_for_dict_field_rejected(self) -> None:
        data = {
            "canon_delta": [1, 2, 3],
            "creative_report": {},
            "chapter_exit_state": {},
        }
        with pytest.raises(ValidationError):
            validate_response_schema(data, TaskType.EXTRACT_CANON)


class TestCanonAuxSchemas:
    def test_init_coherence_claims_require_cognitive_fields(self) -> None:
        claim = {
            "claim_id": "claim_missing_cognitive",
            "artifact": "outline",
            "source_path": "/chapters/0",
            "claim_type": "knowledge",
            "claim_text": "玄昱确认沈清漪身份。",
            "evidence": "第1章明确确认。",
            "chapter_numbers": [1],
        }

        with pytest.raises(ValidationError):
            validate_response_schema(
                {
                    "claims": [claim],
                    "coverage_status": "complete",
                    "unprocessed_source_refs": [],
                },
                TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
            )

    def test_init_coherence_claims_accept_conservative_cognitive_fields(self) -> None:
        validate_response_schema(
            {
                "claims": [
                    {
                        "claim_id": "claim_conservative",
                        "artifact": "outline",
                        "source_path": "/chapters/0",
                        "claim_type": "event",
                        "claim_text": "第一章发生关键事件。",
                        "evidence": "大纲只写关键事件。",
                        "chapter_numbers": [1],
                        "cognitive_subjects": [],
                        "cognitive_object": "",
                        "cognitive_level": "unaware",
                        "action_level": "none",
                        "reader_awareness": "unknown",
                        "character_knowledge_coverage": {},
                        "cognitive_chapter": None,
                        "public_reveal_chapter": None,
                        "foreshadow_chapters": [],
                    }
                ],
                "coverage_status": "complete",
                "unprocessed_source_refs": [],
            },
            TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
        )

    def test_init_coherence_claims_reject_invalid_coverage_status(self) -> None:
        with pytest.raises(ValidationError):
            validate_response_schema(
                {
                    "claims": [],
                    "coverage_status": "assumed_complete",
                    "unprocessed_source_refs": [],
                },
                TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
            )

    def test_valid_extract_motifs(self) -> None:
        validate_response_schema(
            {
                "motifs": [
                    {
                        "motif_id": "motif_001",
                        "name": "月光",
                        "category": "意象",
                        "category_confidence": 0.86,
                        "category_reason": "作为视觉隐喻反复出现",
                        "secondary_categories": ["颜色"],
                        "motif_role": "recurring_image",
                        "importance_score": 0.68,
                        "description": "反复出现的冷白月光",
                        "thematic_meaning": "映照主人公从逃避到直面真相的心理转折",
                        "is_intentional": True,
                        "occurrences": [
                            {
                                "paragraph_index": 3,
                                "text_snippet": "月光落在指节上",
                                "context": "关键抉择前",
                            }
                        ],
                    }
                ]
            },
            TaskType.EXTRACT_MOTIFS,
        )

    def test_extract_motifs_rejects_non_list_motifs(self) -> None:
        with pytest.raises(ValidationError):
            validate_response_schema({"motifs": "月光"}, TaskType.EXTRACT_MOTIFS)

    def test_valid_adjust_outline(self) -> None:
        validate_response_schema(
            {
                "adjusted_chapters": [
                    {
                        "chapter_number": 4,
                        "title": "新的章节标题",
                        "goal": "承接上一章结尾",
                        "beats_summary": ["进入新场景", "发现线索", "制造悬念"],
                        "pov_character": "林远",
                        "setting": "废弃图书馆",
                        "expected_word_count": 3000,
                    }
                ],
                "adjustment_summary": "后续章节自然衔接实际情节。",
            },
            TaskType.ADJUST_OUTLINE,
        )

    def test_adjust_outline_rejects_string_chapter_number(self) -> None:
        with pytest.raises(ValidationError):
            validate_response_schema(
                {
                    "adjusted_chapters": [{"chapter_number": "4", "beats_summary": []}],
                    "adjustment_summary": "",
                },
                TaskType.ADJUST_OUTLINE,
            )


# ---------------------------------------------------------------------------
# PLAN_CHAPTER specifics
# ---------------------------------------------------------------------------


class TestPlanChapter:
    def test_minimal_valid_plan(self) -> None:
        validate_response_schema(
            {
                "scene_intents": [{"scene_id": "s1", "summary": "开场"}],
                "cross_scene_intent": {
                    "cross_scene_references": [],
                    "pacing_curve": [3],
                },
            },
            TaskType.PLAN_CHAPTER,
        )

    def test_scene_intents_single_dict_coerced(self) -> None:
        validate_response_schema(
            {
                "scene_intents": {"scene_id": "s1", "summary": "开场"},
                "cross_scene_intent": {
                    "cross_scene_references": [],
                    "pacing_curve": [3],
                },
            },
            TaskType.PLAN_CHAPTER,
        )

    def test_required_literals_publish_closed_planning_owned_shape(self) -> None:
        validate_response_schema(
            {
                "scene_intents": [],
                "required_literals": [
                    {
                        "contract_id": "secret-boundary",
                        "literal": "有些事情不是你想知道就能知道的",
                        "scene_id": "scene_02",
                        "reason": "后文必须逐字回指",
                        "placement_hint": "林小满拒绝回答时",
                    }
                ],
                "cross_scene_intent": {
                    "cross_scene_references": [],
                    "pacing_curve": [],
                },
            },
            TaskType.PLAN_CHAPTER,
        )

        with pytest.raises(ValidationError):
            validate_response_schema(
                {
                    "scene_intents": [],
                    "required_literals": [
                        {
                            "contract_id": "secret-boundary",
                            "literal": "指定原文",
                            "locally_inferred": True,
                        }
                    ],
                    "cross_scene_intent": {
                        "cross_scene_references": [],
                        "pacing_curve": [],
                    },
                },
                TaskType.PLAN_CHAPTER,
            )

    def test_scene_intents_as_integer_rejected(self) -> None:
        with pytest.raises(ValidationError):
            validate_response_schema(
                {
                    "scene_intents": 3,
                    "cross_scene_intent": {
                        "cross_scene_references": [],
                        "pacing_curve": [3],
                    },
                },
                TaskType.PLAN_CHAPTER,
            )

    def test_cross_scene_intent_required(self) -> None:
        with pytest.raises(ValidationError):
            validate_response_schema(
                {"scene_intents": [{"scene_id": "s1", "summary": "开场"}]},
                TaskType.PLAN_CHAPTER,
            )

    def test_cross_scene_intent_requires_two_child_fields(self) -> None:
        with pytest.raises(ValidationError):
            validate_response_schema(
                {
                    "scene_intents": [{"scene_id": "s1", "summary": "开场"}],
                    "cross_scene_intent": {"cross_scene_references": []},
                },
                TaskType.PLAN_CHAPTER,
            )

    def test_cross_scene_intent_rejects_extra_child_fields(self) -> None:
        with pytest.raises(ValidationError):
            validate_response_schema(
                {
                    "scene_intents": [{"scene_id": "s1", "summary": "开场"}],
                    "cross_scene_intent": {
                        "cross_scene_references": [],
                        "pacing_curve": [3],
                        "pov_character": "林远",
                    },
                },
                TaskType.PLAN_CHAPTER,
            )

    def test_world_rule_application_schema_publishes_canonical_reason_field(self) -> None:
        schema_model = get_response_schema(TaskType.PLAN_CHAPTER)
        assert schema_model is not None
        schema = schema_model.model_json_schema()
        item_schema = schema["properties"]["world_rule_applications"]["items"]
        ref_name = item_schema["$ref"].rsplit("/", 1)[-1]
        properties = schema["$defs"][ref_name]["properties"]

        assert "not_applicable_reason" in properties
        assert "reason" not in properties

    def test_world_rule_reason_drift_reaches_domain_compatibility_layer(self) -> None:
        validate_response_schema(
            {
                "scene_intents": [{"scene_id": "s1", "summary": "开场"}],
                "world_rule_applications": [
                    {
                        "rule_id": "wr_01",
                        "applicability": "not_applicable",
                        "reason": "本章未触发夜禁。",
                    }
                ],
                "cross_scene_intent": {
                    "cross_scene_references": [],
                    "pacing_curve": [3],
                },
            },
            TaskType.PLAN_CHAPTER,
        )

    def test_extra_fields_allowed(self) -> None:
        validate_response_schema(
            {
                "scene_intents": [],
                "chapter_type": "crisis",
                "cross_scene_intent": {
                    "cross_scene_references": [],
                    "pacing_curve": [],
                },
                "unknown_future_field": "value",
            },
            TaskType.PLAN_CHAPTER,
        )


class TestPlanChapterScenes:
    def test_scene_plan_requires_explicit_literal_adjudication_array(self) -> None:
        validate_response_schema(
            {
                "scene_plan": {
                    "scene_intents": [],
                    "world_rule_applications": [],
                    "required_literals": [],
                    "cross_scene_intent": {
                        "cross_scene_references": [],
                        "pacing_curve": [],
                    },
                }
            },
            TaskType.PLAN_CHAPTER_SCENES,
        )

        with pytest.raises(ValidationError):
            validate_response_schema(
                {
                    "scene_plan": {
                        "scene_intents": [],
                        "world_rule_applications": [],
                        "cross_scene_intent": {
                            "cross_scene_references": [],
                            "pacing_curve": [],
                        },
                    }
                },
                TaskType.PLAN_CHAPTER_SCENES,
            )

    def test_scene_plan_requires_world_rule_and_wave_handoffs(self) -> None:
        base = {
            "scene_intents": [],
            "world_rule_applications": [],
            "required_literals": [],
            "cross_scene_intent": {
                "cross_scene_references": [],
                "pacing_curve": [],
            },
        }

        for missing in ("world_rule_applications", "cross_scene_intent"):
            payload = dict(base)
            payload.pop(missing)
            with pytest.raises(ValidationError):
                validate_response_schema(
                    {"scene_plan": payload},
                    TaskType.PLAN_CHAPTER_SCENES,
                )

    def test_scene_plan_schema_publishes_world_rule_application_fields(self) -> None:
        schema_model = get_response_schema(TaskType.PLAN_CHAPTER_SCENES)
        assert schema_model is not None
        schema = schema_model.model_json_schema()
        payload_ref = schema["properties"]["scene_plan"]["$ref"].rsplit("/", 1)[-1]
        payload_schema = schema["$defs"][payload_ref]
        assert {
            "scene_intents",
            "world_rule_applications",
            "required_literals",
            "cross_scene_intent",
        }.issubset(payload_schema["required"])

        item_ref = payload_schema["properties"]["world_rule_applications"]["items"]["$ref"].rsplit(
            "/", 1
        )[-1]
        properties = schema["$defs"][item_ref]["properties"]
        assert {
            "rule_id",
            "scene_id",
            "applicability",
            "usage",
            "expected_evidence",
            "forbidden_boundary",
            "not_applicable_reason",
        }.issubset(properties)


# ---------------------------------------------------------------------------
# BRIDGE_CHAPTER specifics
# ---------------------------------------------------------------------------


class TestBridgeChapter:
    def test_valid_bridge(self) -> None:
        validate_response_schema(
            {"action_handoff": "角色继续前进", "pending_questions": ["谁在跟踪？"]},
            TaskType.BRIDGE_CHAPTER,
        )

    def test_pending_questions_string_coerced(self) -> None:
        validate_response_schema(
            {"action_handoff": "test", "pending_questions": "single question"},
            TaskType.BRIDGE_CHAPTER,
        )

    def test_bridge_prompt_contract_fields_are_schema_fields(self) -> None:
        validate_response_schema(
            {
                "opening_time": "次日清晨",
                "opening_location": "旧图书馆",
                "opening_pov": "林远",
                "transition_mode": "direct_continue",
                "emotional_carryover": "紧张未退",
                "action_handoff": "林远仍握着怀表",
                "causal_link": {"previous_event": "怀表停摆"},
                "pending_questions": [],
                "forbidden_repetition": [],
                "opening_acceptance_criteria": ["开头必须出现怀表状态"],
            },
            TaskType.BRIDGE_CHAPTER,
        )

    def test_bridge_nested_handoffs_reject_undeclared_fields(self) -> None:
        with pytest.raises(ValidationError, match="unexpected_debug"):
            validate_response_schema(
                {
                    "causal_link": {
                        "previous_event": "怀表停摆",
                        "unexpected_debug": "不能传入下游",
                    }
                },
                TaskType.BRIDGE_CHAPTER,
            )


# ---------------------------------------------------------------------------
# BEATS & EVALUATE
# ---------------------------------------------------------------------------


class TestShortForm:
    def test_beats_valid(self) -> None:
        validate_response_schema(
            {
                "beats": [
                    {"sequence": 1, "summary": "开头", "tension_level": 3},
                    {"sequence": 2, "summary": "结尾", "tension_level": 6},
                ]
            },
            TaskType.BEATS,
        )

    def test_beats_tension_level_is_a_bounded_integer(self) -> None:
        validate_response_schema(
            {"beats": [{"sequence": 1, "summary": "冲突升级", "tension_level": 7}]},
            TaskType.BEATS,
        )
        validate_response_schema(
            {"beats": [{"sequence": 1, "summary": "冲突升级", "tension_level": "7"}]},
            TaskType.BEATS,
        )

        with pytest.raises(ValidationError):
            validate_response_schema(
                {
                    "beats": [
                        {"sequence": 1, "summary": "冲突升级", "tension_level": "渐升"}
                    ]
                },
                TaskType.BEATS,
            )

    def test_beats_typed_envelope_preserves_legacy_mapping_container(self) -> None:
        validate_response_schema(
            {
                "beats": {
                    "opening": {"sequence": 1, "summary": "开场", "tension_level": 3}
                }
            },
            TaskType.BEATS,
        )

    def test_evaluate_valid(self) -> None:
        validate_response_schema(
            {
                "scores": [{"dimension": "plot", "score": 8, "comment": "有效"}],
                "overall_score": 8.0,
                "passed": True,
                "threshold": 6.0,
                "summary": "质量稳定",
                "repair_suggestions": [],
            },
            TaskType.EVALUATE,
        )

    def test_evaluate_scores_scalar_rejected(self) -> None:
        with pytest.raises(ValidationError):
            validate_response_schema(
                {
                    "scores": 8,
                    "overall_score": 8.0,
                    "passed": True,
                    "threshold": 6.0,
                    "summary": "质量稳定",
                    "repair_suggestions": [],
                },
                TaskType.EVALUATE,
            )

    def test_short_blueprint_valid(self) -> None:
        validate_response_schema(
            {
                "synopsis": "概要",
                "anchor_elements": {"time_frame": "一夜"},
                "narrative_phases": [],
                "turning_points": [],
                "character_arcs": [],
                "emotional_arc": "紧张到释然",
                "ending_strategy": "余韵式",
            },
            TaskType.SHORT_BLUEPRINT,
        )

    def test_short_creative_summary_valid(self) -> None:
        validate_response_schema(
            {
                "characters": [],
                "narrative_analysis": {"pacing_assessment": "紧凑"},
                "thematic_analysis": {"core_theme": "选择"},
                "creative_highlights": [],
                "improvement_suggestions": [],
                "beat_fulfillment": [],
            },
            TaskType.SHORT_CREATIVE_SUMMARY,
        )


class TestLongInitEnvelopeAlignment:
    def test_plan_outline_declares_quality_spine_fields(self) -> None:
        validate_response_schema(
            {
                "synopsis": "概要",
                "volume_mode": False,
                "volumes": [],
                "narrative_phases": [],
                "key_turning_points": [],
                "character_arcs": [],
                "subplot_plan": [],
                "suspense_schedule": [],
                "ending_strategy": "收束",
                "emotional_arcs": [],
                "causal_chains": [],
                "subplot_collisions": [],
                "subversion_points": [],
            },
            TaskType.PLAN_OUTLINE,
        )

    def test_contract_completion_declares_domain_fields(self) -> None:
        validate_response_schema(
            {
                "verdict": "reject",
                "severity": "high",
                "rationale": "缺少必需推进",
                "repair_or_replan_decision": "repair",
                "missing_required_progressions": ["A"],
                "missing_knowledge_ops": ["B"],
                "forbidden_progression_hits": [],
                "future_leak_hits": [],
                "evidence_quotes": ["证据"],
                "should_block_archive": True,
                "contract_completion_score": "4.5",
                "unexpected_progressions": [],
            },
            TaskType.ADJUDICATE_CONTRACT_COMPLETION,
        )

    def test_reading_power_declares_scored_dimensions(self) -> None:
        validate_response_schema(
            {
                "hook_type": "mystery",
                "hook_strength": "medium",
                "hook_description": "发现新证据",
                "prev_hook_fulfilled": False,
                "micro_payoffs": [{"payoff_type": "clue", "description": "线索兑现"}],
                "is_transition": True,
                "next_chapter_reason": "证据指向新地点",
                "information_pacing": "slow",
                "information_pacing_score": "0.5",
                "main_plot_depth": "stalled",
                "main_plot_advancement_notes": "主线推进不足",
                "tension_match": "depressed",
                "tension_match_score": "0.4",
                "revelation_count": "1",
                "revelation_over_budget": False,
                "consecutive_main_plot_stall": "2",
                "character_drive": "weak",
                "character_drive_notes": "角色偏被动",
            },
            TaskType.EVALUATE_READING_POWER,
        )

    def test_auxiliary_json_tasks_have_declared_envelopes(self) -> None:
        validate_response_schema(
            {
                "resolutions": [
                    {
                        "unresolved_name": "阿洛",
                        "resolution": "alias",
                        "canonical_name": "洛宁",
                        "confidence": "0.8",
                        "reasoning": "上下文指向同一角色",
                    }
                ]
            },
            TaskType.RECONCILE_ENTITIES,
        )
        validate_response_schema(
            {
                "preferred_strategy": "patch",
                "confidence": "0.7",
                "reason": "问题集中",
                "root_causes": ["missing_anchor"],
                "risk_flags": [],
                "diagnostic_summary": "局部修复足够",
            },
            TaskType.REPAIR_STRATEGY_DIAGNOSE,
        )
        validate_response_schema(
            {"verdict": "ok", "issues": []},
            TaskType.AUDIT_POV_DRIFT,
        )
        validate_response_schema(
            {
                "source_text_hash": "abc",
                "chapter_number": "3",
                "total_hits": "1",
                "hits_by_category": {"template": 1},
                "critical_hits": "0",
                "pattern_hits": [],
                "humanize_score": "8.0",
                "summary": "整体自然",
            },
            TaskType.HUMANIZE_SCAN,
        )


class TestElementProgressArbiter:
    def test_valid_payload(self) -> None:
        validate_response_schema(
            {"status": "hit", "confidence": 0.8, "reason": "正文已明确落实"},
            TaskType.ELEMENT_PROGRESS_ARBITER,
        )

    def test_non_numeric_confidence_rejected(self) -> None:
        with pytest.raises(ValidationError):
            validate_response_schema(
                {"status": "hit", "confidence": "high", "reason": "test"},
                TaskType.ELEMENT_PROGRESS_ARBITER,
            )


class TestAuditSchemas:
    def test_book_consistency_nested_issue_valid(self) -> None:
        validate_response_schema(
            {
                "issues": [
                    {
                        "issue_id": "ch12_timeline_01",
                        "category": "timeline",
                        "severity": "warning",
                        "chapters_involved": [11, 12],
                        "primary_chapter": 12,
                        "issue_type": "time_order_conflict",
                        "location": "第12段",
                        "paragraph_index": 12,
                        "paragraph_span": [12, 12],
                        "evidence": "短证据",
                        "description": "描述",
                        "suggestion": "建议",
                        "fix_mode": "repair_causal",
                        "fix_action": "replace",
                        "confidence": 0.8,
                        "evidence_pairs": [
                            {"chapter_number": 11, "evidence": "证据", "claim": "事实"}
                        ],
                        "verification_questions": ["是否同一时间层？"],
                        "handoff_notes": "验证提示",
                        "linked_issue_refs": [{"chapter_number": 12, "lane": "causal", "index": 3}],
                    }
                ],
                "repair_plan": [
                    {
                        "chapter_number": 12,
                        "issue_ids": ["ch12_timeline_01"],
                        "priority": "critical-first",
                        "strategy": "targeted",
                    }
                ],
                "summary": "摘要",
                "consistency_score": 8.5,
            },
            TaskType.BOOK_CONSISTENCY,
        )

    def test_book_consistency_issues_scalar_rejected(self) -> None:
        with pytest.raises(ValidationError):
            validate_response_schema(
                {
                    "issues": 3,
                    "repair_plan": [],
                    "summary": "摘要",
                    "consistency_score": 8.5,
                },
                TaskType.BOOK_CONSISTENCY,
            )

    def test_book_consistency_verify_nested_issue_valid(self) -> None:
        validate_response_schema(
            {
                "verified_issues": [
                    {
                        "issue_id": "ch12_timeline_01",
                        "status": "verified",
                        "description": "描述",
                        "severity": "warning",
                        "confidence": 0.8,
                        "evidence": "证据",
                        "paragraph_index": 12,
                        "paragraph_span": [12, 12],
                        "location": "第12段",
                        "anchor_type": "explicit_para",
                        "location_confidence": 0.9,
                        "evidence_pairs": [],
                        "adjudication_notes": "裁决说明",
                        "repair_scope": {
                            "target": "第12段",
                            "allowed_changes": ["修正时间表述"],
                            "forbidden_changes": ["改动事实"],
                            "preserve": ["角色动机"],
                        },
                        "fix_mode": "repair_causal",
                        "fix_action": "replace",
                        "postconditions": [{"check": "时间顺序", "expected": "一致"}],
                        "rejection_reason": "",
                    }
                ]
            },
            TaskType.BOOK_CONSISTENCY_VERIFY,
        )

    def test_book_consistency_verify_contract_and_model_reject_nested_extra_keys(self) -> None:
        payload = {
            "verified_issues": [
                {
                    "issue_id": "ch12_timeline_01",
                    "status": "verified",
                    "description": "描述",
                    "severity": "warning",
                    "confidence": 0.8,
                    "evidence": "证据",
                    "paragraph_index": 12,
                    "paragraph_span": [12, 12],
                    "location": "第12段",
                    "anchor_type": "explicit_para",
                    "location_confidence": 0.9,
                    "evidence_pairs": [],
                    "adjudication_notes": "裁决说明",
                    "repair_scope": {
                        "target": "第12段",
                        "allowed_changes": ["修正时间表述"],
                        "forbidden_changes": ["改动事实"],
                        "preserve": ["角色动机"],
                    },
                    "fix_mode": "repair_causal",
                    "fix_action": "replace",
                    "postconditions": [{"check": "时间顺序", "expected": "一致"}],
                    "rejection_reason": "",
                    "legacy_hint": "旧字段不得穿过最终边界",
                }
            ]
        }

        with pytest.raises(ValueError, match=r"unexpected property `legacy_hint`"):
            validate_json_output_contract(TaskType.BOOK_CONSISTENCY_VERIFY, payload)
        with pytest.raises(ValidationError, match="legacy_hint"):
            BookConsistencyVerifyResult.model_validate(payload)

    def test_editorial_audit_nested_finding_valid(self) -> None:
        validate_response_schema(
            {
                "summary": "编辑审计摘要",
                "findings": [
                    {
                        "issue_type": "voice_convergence",
                        "severity": "medium",
                        "chapter_number": 3,
                        "summary": "声纹趋同",
                        "evidence": ["证据"],
                        "recommendation": "调整对白节奏",
                        "confidence": 0.8,
                        "metadata": {},
                    }
                ],
                "revision_plan": ["调整第3章对白"],
                "metrics": {"editorial_score": 8.0},
            },
            TaskType.CHECK_EDITORIAL,
        )


class TestGuardConstraintCheck:
    def test_valid_payload(self) -> None:
        validate_response_schema(
            {
                "status": "compliant",
                "confidence": 0.7,
                "evidence": "正文片段",
                "notes": "符合约束",
            },
            TaskType.GUARD_CONSTRAINT_CHECK,
        )

    def test_non_numeric_confidence_rejected(self) -> None:
        with pytest.raises(ValidationError):
            validate_response_schema(
                {
                    "status": "partial",
                    "confidence": "uncertain",
                    "evidence": "正文片段",
                    "notes": "test",
                },
                TaskType.GUARD_CONSTRAINT_CHECK,
            )


class TestMacroGuardAudit:
    def test_valid_payload(self) -> None:
        validate_response_schema(
            {
                "dimensions": {
                    "outline_alignment": 0.7,
                    "character_arc_consistency": 0.8,
                },
                "recommended_action": "warning_hint",
            },
            TaskType.MACRO_GUARD_AUDIT,
        )

    def test_dimensions_must_be_dict(self) -> None:
        with pytest.raises(ValidationError):
            validate_response_schema(
                {
                    "dimensions": [1, 2, 3],
                    "recommended_action": "warning_hint",
                },
                TaskType.MACRO_GUARD_AUDIT,
            )


class TestBookConsistencySchema:
    def test_repair_plan_is_type_checked_but_not_required(self) -> None:
        validate_response_schema(
            {
                "issues": [],
                "summary": "未发现问题",
                "consistency_score": 9.0,
            },
            TaskType.BOOK_CONSISTENCY,
        )
        validate_response_schema(
            {
                "issues": [],
                "repair_plan": {"chapter_number": 1},
                "summary": "未发现问题",
                "consistency_score": 9.0,
            },
            TaskType.BOOK_CONSISTENCY,
        )

    def test_invalid_repair_plan_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            validate_response_schema(
                {
                    "issues": [],
                    "repair_plan": 123,
                    "summary": "未发现问题",
                    "consistency_score": 9.0,
                },
                TaskType.BOOK_CONSISTENCY,
            )


class TestVolumeAuditSchema:
    def test_valid_payload(self) -> None:
        validate_response_schema(
            {
                "volume_number": 1,
                "volume_summary": "第一卷主线收束。",
                "milestone_status": [],
                "carry_over_characters": [],
                "carry_over_foreshadowing_ids": [],
                "next_volume_focus": "推进第二卷冲突。",
            },
            TaskType.VOLUME_AUDIT,
        )

    def test_volume_number_is_required(self) -> None:
        with pytest.raises(ValidationError):
            validate_response_schema(
                {
                    "volume_summary": "第一卷主线收束。",
                    "milestone_status": [],
                    "carry_over_characters": [],
                    "carry_over_foreshadowing_ids": [],
                    "next_volume_focus": "推进第二卷冲突。",
                },
                TaskType.VOLUME_AUDIT,
            )


class TestContractCoherenceResponse:
    """Tests for _ContractCoherenceResponse LenientStr coercion."""

    @pytest.mark.parametrize(
        "task_type",
        [
            TaskType.ADJUDICATE_CONTRACT_COHERENCE,
            TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES,
        ],
    )
    def test_change_intent_none_coerced_to_empty_string(self, task_type: TaskType) -> None:
        """LLM may return null for change_intent when verdict=accept."""
        validate_response_schema(
            {
                "schema_version": "audit_v2",
                "dimension": "init_coherence",
                "verdict": "accept",
                "score": 10,
                "issues": [],
                "summary": "无冲突",
                "metadata": {},
                "source_refs": [],
                "repair_scope": [],
                "preserve": [],
                "change_intent": None,
                "blocked": False,
            },
            task_type,
        )

    @pytest.mark.parametrize(
        "task_type",
        [
            TaskType.ADJUDICATE_CONTRACT_COHERENCE,
            TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES,
        ],
    )
    def test_string_fields_none_coerced(self, task_type: TaskType) -> None:
        """All str fields should accept None → ''."""
        validate_response_schema(
            {
                "schema_version": None,
                "dimension": None,
                "verdict": None,
                "score": 1.0,
                "issues": [],
                "summary": None,
                "metadata": {},
                "source_refs": [],
                "repair_scope": [],
                "preserve": [],
                "change_intent": None,
                "blocked": False,
            },
            task_type,
        )

    def test_valid_string_fields_pass_through(self) -> None:
        validate_response_schema(
            {
                "schema_version": "audit_v2",
                "dimension": "init_coherence",
                "verdict": "accept",
                "score": 10,
                "issues": [],
                "summary": "无冲突",
                "metadata": {"total_candidates": 12},
                "source_refs": ["ref1"],
                "repair_scope": [],
                "preserve": ["ref1"],
                "change_intent": "保留现有表述",
                "blocked": False,
            },
            TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES,
        )


class TestResearchTaskSchemas:
    """Schemas for the MCP research tasks (GROUND_OUTLINE_RESEARCH etc.).

    These tasks have format contracts with enforce_required_keys=True but
    previously lacked registered response schemas, leaving their outputs
    structurally unvalidated. These tests guard the envelope alignment.
    """

    def test_ground_outline_research_valid_payload(self) -> None:
        validate_response_schema(
            {
                "summary": "校准摘要",
                "global_notes": ["全书通用提醒"],
                "chapter_notes": [
                    {
                        "chapter_number": 1,
                        "reminders": ["提醒"],
                        "fact_risks": [],
                        "source_refs": [],
                    }
                ],
                "fact_risks": [{"risk": "时间线风险"}],
                "terminology": ["术语"],
                "source_refs": ["ref1"],
            },
            TaskType.GROUND_OUTLINE_RESEARCH,
        )

    def test_ground_outline_research_none_summary_coerced(self) -> None:
        validate_response_schema(
            {
                "summary": None,
                "global_notes": None,
                "chapter_notes": None,
                "fact_risks": None,
                "terminology": None,
                "source_refs": None,
            },
            TaskType.GROUND_OUTLINE_RESEARCH,
        )

    def test_plan_init_research_queries_valid_payload(self) -> None:
        validate_response_schema(
            {
                "queries": [
                    {
                        "query": "搜索词",
                        "rationale": "需要核实背景",
                        "intent": "补充真实性",
                        "priority": "must",
                        "locale": "zh-CN",
                        "source_preferences": ["官方资料"],
                        "recency_required": False,
                        "risk_if_missing": "设定可信度不足",
                    }
                ],
                "knowledge_gaps": ["知识空白"],
            },
            TaskType.PLAN_INIT_RESEARCH_QUERIES,
        )

    def test_plan_init_research_queries_rejects_misspelled_content_field(self) -> None:
        with pytest.raises(ValueError):
            validate_response_schema(
                {
                    "queries": [
                        {
                            "query": "搜索词",
                            "rationale": "需要核实背景",
                            "intent": "补充真实性",
                            "priority": "must",
                            "locale": "zh-CN",
                            "source_preferences": ["官方资料"],
                            "recurrency_required": False,
                            "risk_if_missing": "设定可信度不足",
                        }
                    ],
                    "knowledge_gaps": ["知识空白"],
                },
                TaskType.PLAN_INIT_RESEARCH_QUERIES,
            )

    def test_synthesize_init_research_dossier_valid_payload(self) -> None:
        validate_response_schema(
            {
                "summary": "资料摘要",
                "real_world_constraints": ["约束"],
                "terminology": ["术语"],
                "inspiration_notes": ["灵感"],
                "uncertainty_notes": ["不确定"],
                "source_refs": ["ref1"],
            },
            TaskType.SYNTHESIZE_INIT_RESEARCH_DOSSIER,
        )

    def test_synthesize_model_prior_research_valid_payload(self) -> None:
        validate_response_schema(
            {
                "notes": ["知识要点"],
                "terminology": ["术语"],
                "uncertainty_notes": ["不确定"],
            },
            TaskType.SYNTHESIZE_MODEL_PRIOR_RESEARCH,
        )

    @pytest.mark.parametrize(
        "task_type",
        [
            TaskType.GROUND_OUTLINE_RESEARCH,
            TaskType.PLAN_INIT_RESEARCH_QUERIES,
            TaskType.SYNTHESIZE_INIT_RESEARCH_DOSSIER,
            TaskType.SYNTHESIZE_MODEL_PRIOR_RESEARCH,
        ],
    )
    def test_research_task_has_registered_schema(self, task_type: TaskType) -> None:
        assert get_response_schema(task_type) is not None, f"Missing schema for {task_type}"
