"""Integration test: Blueprint fragment generation with new schema fields.

Tests the end-to-end fragment assembly flow (BlueprintFragments ↔ NarrativeBlueprint)
and weave density validation, using deterministic data (no LLM calls).

New fields under test: emotional_arcs, causal_chains, subplot_collisions, subversion_points.
"""

from __future__ import annotations

from novel_forge.core.schemas.init_v2 import BlueprintFragments
from novel_forge.core.schemas.outline import (
    CausalChain,
    EmotionalArc,
    NarrativeBlueprint,
    SubplotCollision,
    SubplotPlan,
    SubplotWeaveLink,
    SubversionPoint,
)
from novel_forge.pipeline.long.services.blueprint.blueprint_payloads import (
    pre_normalize_blueprint_payload,
)
from novel_forge.pipeline.long.services.init.init_v2 import (
    assemble_blueprint_from_fragments,
    blueprint_to_fragments,
)
from novel_forge.pipeline.long.services.weave_validation import (
    WeaveValidationResult,
    validate_subplot_weave,
)

# ── Fixtures ───────────────────────────────────────────────────


def _make_blueprint_with_new_fields() -> NarrativeBlueprint:
    """NarrativeBlueprint populated with all four new schema fields."""
    return NarrativeBlueprint(
        synopsis="测试故事：一位记忆回收师发现前半生被改写",
        emotional_arcs=[
            EmotionalArc(
                arc_name="困惑到觉醒",
                emotion_type="悬疑",
                peak_chapters=[5, 10],
                valley_chapters=[3, 8],
                description="从迷茫到真相大白的情感曲线",
                related_characters=["林远"],
                related_subplots=["失忆之谜"],
            ),
        ],
        causal_chains=[
            CausalChain(
                chain_name="时间裂缝连锁",
                trigger_chapter=1,
                trigger_action="发现时间裂缝",
                intermediate_chapters=[3, 5],
                payoff_chapter=8,
                payoff_event="理解时间循环的真相",
                escalation="每次穿越代价递增",
                involved_subplots=["时间裂缝"],
            ),
        ],
        subplot_collisions=[
            SubplotCollision(
                collision_chapter=7,
                involved_subplots=["时间裂缝", "失忆之谜"],
                collision_type="融合",
                outcome="两条支线在图书馆交汇，揭示共同源头",
                ripple_effects=["林远身份确认"],
                setup_chapters=[3, 5],
            ),
        ],
        subversion_points=[
            SubversionPoint(
                chapter=10,
                expected_outcome="主角成功破解时间循环",
                actual_outcome="主角发现自己就是循环的制造者",
                setup_chapters=[1, 5, 8],
                justification="前期铺垫暗示怀表的来源",
                related_subplots=["时间裂缝"],
            ),
        ],
    )


def _make_fragments_dict_with_new_fields() -> dict:
    """Raw dict payload mimicking LLM output for BlueprintFragments with new fields."""
    return {
        "synopsis": "测试故事：一位记忆回收师发现前半生被改写",
        "emotional_arcs": [
            {
                "arc_name": "困惑到觉醒",
                "emotion_type": "悬疑",
                "peak_chapters": [5, 10],
                "valley_chapters": [3, 8],
            },
        ],
        "causal_chains": [
            {
                "chain_name": "时间裂缝连锁",
                "trigger_chapter": 1,
                "trigger_action": "发现时间裂缝",
                "payoff_chapter": 8,
                "payoff_event": "理解时间循环的真相",
            },
        ],
        "subplot_collisions": [
            {
                "collision_chapter": 7,
                "involved_subplots": ["时间裂缝", "失忆之谜"],
                "collision_type": "融合",
                "outcome": "两条支线在图书馆交汇",
            },
        ],
        "subversion_points": [
            {
                "chapter": 10,
                "expected_outcome": "主角成功破解时间循环",
                "actual_outcome": "主角发现自己就是循环的制造者",
                "justification": "前期铺垫暗示",
            },
        ],
    }


def _make_blueprint_with_subplots() -> NarrativeBlueprint:
    """NarrativeBlueprint with well-connected subplots for weave validation."""
    return NarrativeBlueprint(
        synopsis="测试支线交织",
        narrative_phases=[],
        subplot_plan=[
            SubplotPlan(
                name="时间裂缝",
                description="探索时间裂缝的真相",
                priority="primary",
                involved_chapters=[1, 3, 5, 7, 9],
                resolution_chapter=9,
                resolution_target="theme_echo",
                resolution_type="resolve",
                weave_links=[
                    SubplotWeaveLink(
                        source_type="main_plot",
                        source_ref="林远发现裂缝",
                        target_subplot="时间裂缝",
                        trigger_chapter=1,
                        link_type="trigger_start",
                        description="主线触发支线启动",
                    ),
                    SubplotWeaveLink(
                        source_type="subplot",
                        source_ref="时间裂缝",
                        target_subplot="主线",
                        trigger_chapter=5,
                        link_type="trigger_turn",
                        description="裂缝扩大引发转折",
                    ),
                    SubplotWeaveLink(
                        source_type="subplot",
                        source_ref="时间裂缝",
                        target_subplot="主线",
                        trigger_chapter=9,
                        link_type="feed_main",
                        description="支线反哺主线",
                    ),
                ],
            ),
            SubplotPlan(
                name="失忆之谜",
                description="主角的失忆真相",
                priority="normal",
                involved_chapters=[2, 4, 6],
                resolution_chapter=6,
                resolution_target="character_fate:林远",
                resolution_type="reveal",
                weave_links=[
                    SubplotWeaveLink(
                        source_type="main_plot",
                        source_ref="林远失忆",
                        target_subplot="失忆之谜",
                        trigger_chapter=2,
                        link_type="trigger_start",
                        description="主线触发支线启动",
                    ),
                    SubplotWeaveLink(
                        source_type="subplot",
                        source_ref="失忆之谜",
                        target_subplot="主线",
                        trigger_chapter=6,
                        link_type="reveal_key",
                        description="支线揭示关键信息",
                    ),
                ],
            ),
        ],
    )


# ── BlueprintFragments: new fields ─────────────────────────────


class TestBlueprintFragmentsNewFields:
    """BlueprintFragments accepts and stores the four new schema fields."""

    def test_fragments_model_validate_with_new_fields(self) -> None:
        data = _make_fragments_dict_with_new_fields()
        fragments = BlueprintFragments.model_validate(data)
        assert len(fragments.emotional_arcs) == 1
        assert fragments.emotional_arcs[0]["arc_name"] == "困惑到觉醒"
        assert len(fragments.causal_chains) == 1
        assert fragments.causal_chains[0]["chain_name"] == "时间裂缝连锁"
        assert len(fragments.subplot_collisions) == 1
        assert fragments.subplot_collisions[0]["collision_chapter"] == 7
        assert len(fragments.subversion_points) == 1
        assert fragments.subversion_points[0]["chapter"] == 10

    def test_fragments_defaults_new_fields_to_empty(self) -> None:
        fragments = BlueprintFragments()
        assert fragments.emotional_arcs == []
        assert fragments.causal_chains == []
        assert fragments.subplot_collisions == []
        assert fragments.subversion_points == []

    def test_fragments_roundtrip_dump_load(self) -> None:
        data = _make_fragments_dict_with_new_fields()
        fragments = BlueprintFragments.model_validate(data)
        dumped = fragments.model_dump(mode="json")
        restored = BlueprintFragments.model_validate(dumped)
        assert len(restored.emotional_arcs) == 1
        assert len(restored.causal_chains) == 1
        assert len(restored.subplot_collisions) == 1
        assert len(restored.subversion_points) == 1


# ── blueprint_to_fragments: preserves new fields ───────────────


class TestBlueprintToFragments:
    """blueprint_to_fragments copies new fields from NarrativeBlueprint."""

    def test_preserves_emotional_arcs(self) -> None:
        bp = _make_blueprint_with_new_fields()
        fragments = blueprint_to_fragments(bp)
        assert len(fragments.emotional_arcs) == 1
        assert fragments.emotional_arcs[0]["arc_name"] == "困惑到觉醒"

    def test_preserves_causal_chains(self) -> None:
        bp = _make_blueprint_with_new_fields()
        fragments = blueprint_to_fragments(bp)
        assert len(fragments.causal_chains) == 1
        assert fragments.causal_chains[0]["chain_name"] == "时间裂缝连锁"

    def test_preserves_subplot_collisions(self) -> None:
        bp = _make_blueprint_with_new_fields()
        fragments = blueprint_to_fragments(bp)
        assert len(fragments.subplot_collisions) == 1
        assert fragments.subplot_collisions[0]["collision_chapter"] == 7

    def test_preserves_subversion_points(self) -> None:
        bp = _make_blueprint_with_new_fields()
        fragments = blueprint_to_fragments(bp)
        assert len(fragments.subversion_points) == 1
        assert fragments.subversion_points[0]["chapter"] == 10

    def test_preserves_all_new_fields_together(self) -> None:
        bp = _make_blueprint_with_new_fields()
        fragments = blueprint_to_fragments(bp)
        assert fragments.emotional_arcs
        assert fragments.causal_chains
        assert fragments.subplot_collisions
        assert fragments.subversion_points


# ── assemble_blueprint_from_fragments ──────────────────────────


class TestAssembleBlueprintFromFragments:
    """assemble_blueprint_from_fragments creates a valid NarrativeBlueprint."""

    def test_assemble_from_dict_creates_valid_blueprint(self) -> None:
        data = _make_fragments_dict_with_new_fields()
        bp = assemble_blueprint_from_fragments(data)
        assert isinstance(bp, NarrativeBlueprint)
        assert bp.synopsis == "测试故事：一位记忆回收师发现前半生被改写"

    def test_assemble_has_new_field_attributes(self) -> None:
        """Assembled blueprint has the four new fields as attributes (with defaults)."""
        data = _make_fragments_dict_with_new_fields()
        bp = assemble_blueprint_from_fragments(data)
        assert hasattr(bp, "emotional_arcs")
        assert hasattr(bp, "causal_chains")
        assert hasattr(bp, "subplot_collisions")
        assert hasattr(bp, "subversion_points")

    def test_assemble_from_blueprint_fragments_model(self) -> None:
        """assemble_blueprint_from_fragments accepts BlueprintFragments model directly."""
        data = _make_fragments_dict_with_new_fields()
        fragments = BlueprintFragments.model_validate(data)
        bp = assemble_blueprint_from_fragments(fragments)
        assert isinstance(bp, NarrativeBlueprint)
        assert bp.synopsis == "测试故事：一位记忆回收师发现前半生被改写"


# ── pre_normalize_blueprint_payload ────────────────────────────


class TestPreNormalizeBlueprintPayload:
    """pre_normalize_blueprint_payload preserves new field data."""

    def test_normalize_preserves_new_fields(self) -> None:
        data = _make_fragments_dict_with_new_fields()
        normalized = pre_normalize_blueprint_payload(data, total_chapters=12)
        assert isinstance(normalized, dict)
        assert "emotional_arcs" in normalized
        assert "causal_chains" in normalized
        assert "subplot_collisions" in normalized
        assert "subversion_points" in normalized

    def test_normalize_produces_valid_fragments(self) -> None:
        data = _make_fragments_dict_with_new_fields()
        normalized = pre_normalize_blueprint_payload(data, total_chapters=12)
        if isinstance(normalized, dict):
            fragments = BlueprintFragments.model_validate(normalized)
            assert len(fragments.emotional_arcs) == 1
            assert len(fragments.causal_chains) == 1

    def test_normalize_handles_empty_new_fields(self) -> None:
        data = {"synopsis": "仅概述"}
        normalized = pre_normalize_blueprint_payload(data, total_chapters=8)
        if isinstance(normalized, dict):
            # New keys may or may not be added; no crash is the goal
            fragments = BlueprintFragments.model_validate(normalized)
            assert fragments.emotional_arcs == []
            assert fragments.causal_chains == []


# ── Weave validation: density warnings ─────────────────────────


class TestWeaveValidationDensity:
    """validate_subplot_weave detects density issues."""

    def test_dense_subplots_minimal_warnings(self) -> None:
        """Well-connected subplots produce no density-related warnings."""
        bp = _make_blueprint_with_subplots()
        result = validate_subplot_weave(bp)
        assert isinstance(result, WeaveValidationResult)
        density_warnings = [w for w in result.warnings if "交织关系" in w]
        assert len(density_warnings) == 0

    def test_sparse_primary_subplot_triggers_density_warning(self) -> None:
        """Primary subplot with only 1 weave link triggers density warning."""
        bp = NarrativeBlueprint(
            subplot_plan=[
                SubplotPlan(
                    name="薄弱支线",
                    priority="primary",
                    involved_chapters=[1, 3, 5, 7],
                    resolution_chapter=7,
                    resolution_target="theme_echo",
                    resolution_type="resolve",
                    weave_links=[
                        SubplotWeaveLink(
                            source_type="main_plot",
                            source_ref="触发",
                            target_subplot="薄弱支线",
                            trigger_chapter=1,
                            link_type="trigger_start",
                        ),
                    ],
                ),
            ],
        )
        result = validate_subplot_weave(bp)
        density_warnings = [w for w in result.warnings if "交织关系" in w]
        assert len(density_warnings) > 0

    def test_missing_feedback_triggers_suggestion(self) -> None:
        """Subplot without feed_main/reveal_key triggers suggestion."""
        bp = NarrativeBlueprint(
            subplot_plan=[
                SubplotPlan(
                    name="无反哺支线",
                    priority="normal",
                    involved_chapters=[1, 2],
                    weave_links=[
                        SubplotWeaveLink(
                            source_type="main_plot",
                            source_ref="触发",
                            target_subplot="无反哺支线",
                            trigger_chapter=1,
                            link_type="trigger_start",
                        ),
                    ],
                ),
            ],
        )
        result = validate_subplot_weave(bp)
        feedback_suggestions = [
            s for s in result.suggestions if "回馈主线" in s
        ]
        assert len(feedback_suggestions) > 0

    def test_collision_alignment_out_of_range_warns(self) -> None:
        """Collision chapter outside involved chapters triggers warning."""
        bp = NarrativeBlueprint(
            subplot_plan=[
                SubplotPlan(
                    name="支线A",
                    involved_chapters=[1, 2, 3],
                ),
            ],
            subplot_collisions=[
                SubplotCollision(
                    collision_chapter=10,
                    involved_subplots=["支线A"],
                    collision_type="冲突",
                ),
            ],
        )
        result = validate_subplot_weave(bp)
        collision_warnings = [w for w in result.warnings if "碰撞" in w]
        assert len(collision_warnings) > 0

    def test_validation_returns_structured_result(self) -> None:
        """validate_subplot_weave returns loop/feedback status and dict."""
        bp = _make_blueprint_with_subplots()
        result = validate_subplot_weave(bp)
        assert isinstance(result, WeaveValidationResult)
        result_dict = result.to_dict()
        assert "warnings" in result_dict
        assert "suggestions" in result_dict
        assert "loop_check_passed" in result_dict
        assert "feedback_check_passed" in result_dict
        assert "is_valid" in result_dict

    def test_dense_subplots_loop_and_feedback_pass(self) -> None:
        """Well-connected subplots pass loop and feedback checks."""
        bp = _make_blueprint_with_subplots()
        result = validate_subplot_weave(bp)
        assert result.loop_check_passed is True
        assert result.feedback_check_passed is True


# ── End-to-end: fragment generation flow ───────────────────────


class TestEndToEndFragmentGeneration:
    """End-to-end: blueprint → fragments → normalize → assemble → validate."""

    def test_full_flow_preserves_core_fields(self) -> None:
        """Complete roundtrip preserves synopsis and structural fields."""
        bp = _make_blueprint_with_new_fields()
        # Step 1: Blueprint → Fragments
        fragments = blueprint_to_fragments(bp)
        assert fragments.synopsis == bp.synopsis
        # Step 2: Normalize
        normalized = pre_normalize_blueprint_payload(
            fragments.model_dump(mode="json"), total_chapters=12
        )
        assert isinstance(normalized, dict)
        # Step 3: Assemble
        bp_reassembled = assemble_blueprint_from_fragments(normalized)
        assert isinstance(bp_reassembled, NarrativeBlueprint)
        assert bp_reassembled.synopsis == bp.synopsis

    def test_full_flow_with_subplots_validates_weave(self) -> None:
        """Create blueprint with subplots → validate weave → check density."""
        bp = _make_blueprint_with_subplots()
        fragments = blueprint_to_fragments(bp)
        normalized = pre_normalize_blueprint_payload(
            fragments.model_dump(mode="json"), total_chapters=12
        )
        bp_reassembled = assemble_blueprint_from_fragments(normalized)
        result = validate_subplot_weave(bp_reassembled)
        assert isinstance(result, WeaveValidationResult)
        # Dense subplots should pass loop/feedback
        assert result.loop_check_passed is True
        assert result.feedback_check_passed is True

    def test_fragments_preserve_new_fields_through_dump_load(self) -> None:
        """New fields survive BlueprintFragments dump → normalize → load cycle."""
        bp = _make_blueprint_with_new_fields()
        fragments = blueprint_to_fragments(bp)
        dumped = fragments.model_dump(mode="json")
        # Verify new fields in the dump
        assert "emotional_arcs" in dumped
        assert "causal_chains" in dumped
        assert "subplot_collisions" in dumped
        assert "subversion_points" in dumped
        # Reload
        restored = BlueprintFragments.model_validate(dumped)
        assert len(restored.emotional_arcs) == 1
        assert len(restored.causal_chains) == 1
        assert len(restored.subplot_collisions) == 1
        assert len(restored.subversion_points) == 1

    def test_full_flow_new_fields_in_fragments(self) -> None:
        """End-to-end: new fields are preserved in fragments through full pipeline."""
        bp = _make_blueprint_with_new_fields()
        # Blueprint → Fragments
        fragments = blueprint_to_fragments(bp)
        # Fragments → Dict → Normalize → Fragments
        normalized = pre_normalize_blueprint_payload(
            fragments.model_dump(mode="json"), total_chapters=12
        )
        if isinstance(normalized, dict):
            restored_fragments = BlueprintFragments.model_validate(normalized)
            assert len(restored_fragments.emotional_arcs) == 1
            assert restored_fragments.emotional_arcs[0]["arc_name"] == "困惑到觉醒"
            assert len(restored_fragments.causal_chains) == 1
            assert len(restored_fragments.subplot_collisions) == 1
            assert len(restored_fragments.subversion_points) == 1

    def test_full_flow_with_collisions_validates_alignment(self) -> None:
        """End-to-end: blueprint with collisions validates collision alignment."""
        bp_subplots = _make_blueprint_with_subplots()
        shared_chapter = 4
        bp_full = NarrativeBlueprint(
            synopsis="完整蓝图",
            subplot_plan=[
                SubplotPlan(
                    name="时间裂缝",
                    description="探索时间裂缝的真相",
                    priority="primary",
                    involved_chapters=[1, 3, shared_chapter, 5, 7, 9],
                    resolution_chapter=9,
                    resolution_target="theme_echo",
                    resolution_type="resolve",
                    weave_links=bp_subplots.subplot_plan[0].weave_links,
                ),
                bp_subplots.subplot_plan[1],
            ],
            subplot_collisions=[
                SubplotCollision(
                    collision_chapter=shared_chapter,
                    involved_subplots=["时间裂缝", "失忆之谜"],
                    collision_type="冲突",
                    outcome="支线冲突",
                ),
            ],
        )
        result = validate_subplot_weave(bp_full)
        assert isinstance(result, WeaveValidationResult)
        collision_warnings = [w for w in result.warnings if "碰撞" in w]
        assert len(collision_warnings) == 0

    def test_full_flow_collisions_out_of_range(self) -> None:
        """End-to-end: collision chapter outside involved chapters warns."""
        bp_subplots = _make_blueprint_with_subplots()
        bp_full = NarrativeBlueprint(
            synopsis="完整蓝图",
            subplot_plan=bp_subplots.subplot_plan,
            subplot_collisions=[
                SubplotCollision(
                    collision_chapter=15,
                    involved_subplots=["时间裂缝"],
                    collision_type="融合",
                    outcome="超出范围的碰撞",
                ),
            ],
        )
        result = validate_subplot_weave(bp_full)
        collision_warnings = [w for w in result.warnings if "碰撞" in w]
        assert len(collision_warnings) > 0
