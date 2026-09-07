"""Tests for blueprint schema classes (blueprint_elements.py + outline.py NarrativeBlueprint).

Covers: BlueprintElementCard, BlueprintElementPreferenceItem,
       BlueprintElementPreferenceConfig, BlueprintElementSelection.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from novel_forge.core.schemas.blueprint_elements import (
    BlueprintElementCard,
    BlueprintElementPreferenceConfig,
    BlueprintElementPreferenceItem,
    BlueprintElementSelection,
)
from novel_forge.core.schemas.init_v2 import BlueprintFragments
from novel_forge.core.schemas.outline import (
    CausalChain,
    EmotionalArc,
    NarrativeBlueprint,
    SubplotCollision,
    SubversionPoint,
)

# ── Imports ────────────────────────────────────────────────────


class TestBlueprintSchemaImports:
    """All blueprint schema classes can be imported without error."""

    def test_blueprint_element_card_import(self) -> None:
        """BlueprintElementCard is importable."""
        assert BlueprintElementCard is not None

    def test_blueprint_element_preference_item_import(self) -> None:
        """BlueprintElementPreferenceItem is importable."""
        assert BlueprintElementPreferenceItem is not None

    def test_blueprint_element_preference_config_import(self) -> None:
        """BlueprintElementPreferenceConfig is importable."""
        assert BlueprintElementPreferenceConfig is not None

    def test_blueprint_element_selection_import(self) -> None:
        """BlueprintElementSelection is importable."""
        assert BlueprintElementSelection is not None


# ── BlueprintElementCard ───────────────────────────────────────


class TestBlueprintElementCard:
    """Tests for BlueprintElementCard schema."""

    def test_minimal_creation(self) -> None:
        """BlueprintElementCard can be created with only required fields."""
        card = BlueprintElementCard(
            element_id="elem_001",
            name="情感弧线",
            category="emotion",
            description="控制故事的情感走向",
            rationale="情感是故事的核心驱动力",
        )
        assert card.element_id == "elem_001"
        assert card.name == "情感弧线"
        assert card.category == "emotion"
        assert card.description == "控制故事的情感走向"
        assert card.rationale == "情感是故事的核心驱动力"

    def test_default_values(self) -> None:
        """BlueprintElementCard uses correct defaults for optional fields."""
        card = BlueprintElementCard()
        assert card.element_id == ""
        assert card.name == ""
        assert card.category == ""
        assert card.tier == "extension"
        assert card.ui_hint == "checklist"
        assert card.library_source == "builtin"
        assert card.selection_source == ""
        assert card.selection_score == 0.0
        assert card.user_weight is None
        assert card.user_locked is False
        assert card.user_enabled is None
        assert card.recommended_genres == []

    def test_tier_literal_required(self) -> None:
        """tier field accepts 'required' literal value."""
        card = BlueprintElementCard(
            tier="required",
            description="test",
            rationale="test",
        )
        assert card.tier == "required"

    def test_tier_literal_extension(self) -> None:
        """tier field accepts 'extension' literal value."""
        card = BlueprintElementCard(
            tier="extension",
            description="test",
            rationale="test",
        )
        assert card.tier == "extension"

    def test_tier_rejects_invalid(self) -> None:
        """tier field rejects values other than 'required' or 'extension'."""
        with pytest.raises(ValidationError):
            BlueprintElementCard(
                tier="invalid_tier",
                description="test",
                rationale="test",
            )

    def test_selection_score_range(self) -> None:
        """selection_score must be >= 0."""
        card = BlueprintElementCard(selection_score=0.0, description="test", rationale="test")
        assert card.selection_score == 0.0
        card = BlueprintElementCard(selection_score=100.0, description="test", rationale="test")
        assert card.selection_score == 100.0

    def test_selection_score_rejects_negative(self) -> None:
        """selection_score rejects negative values."""
        with pytest.raises(ValidationError):
            BlueprintElementCard(
                selection_score=-1.0,
                description="test",
                rationale="test",
            )

    def test_user_weight_range(self) -> None:
        """user_weight accepts values in [0, 100] or None."""
        card = BlueprintElementCard(user_weight=None, description="test", rationale="test")
        assert card.user_weight is None
        card = BlueprintElementCard(user_weight=0.0, description="test", rationale="test")
        assert card.user_weight == 0.0
        card = BlueprintElementCard(user_weight=100.0, description="test", rationale="test")
        assert card.user_weight == 100.0

    def test_user_weight_rejects_out_of_range(self) -> None:
        """user_weight rejects values outside [0, 100]."""
        with pytest.raises(ValidationError):
            BlueprintElementCard(
                user_weight=150.0,
                description="test",
                rationale="test",
            )

    def test_model_validate_from_dict(self) -> None:
        """BlueprintElementCard can be created via model_validate with a dict."""
        card = BlueprintElementCard.model_validate(
            {
                "element_id": "elem_002",
                "name": "伏笔",
                "category": "structure",
                "description": "管理故事中的伏笔与呼应",
                "rationale": "伏笔增加故事的层次感",
                "tier": "required",
            }
        )
        assert card.element_id == "elem_002"
        assert card.tier == "required"

    def test_extra_fields_ignored(self) -> None:
        """Extra fields are silently ignored (model_config extra='ignore')."""
        card = BlueprintElementCard(
            element_id="elem_003",
            name="test",
            category="test",
            description="test",
            rationale="test",
            unknown_field="should_be_ignored",
        )
        assert card.element_id == "elem_003"
        assert not hasattr(card, "unknown_field")


# ── BlueprintElementPreferenceItem ─────────────────────────────


class TestBlueprintElementPreferenceItem:
    """Tests for BlueprintElementPreferenceItem schema."""

    def test_minimal_creation(self) -> None:
        """BlueprintElementPreferenceItem can be created with minimal fields."""
        item = BlueprintElementPreferenceItem(element_id="ext_001")
        assert item.element_id == "ext_001"
        assert item.enabled is None
        assert item.locked is False
        assert item.weight == 50.0

    def test_weight_range_boundaries(self) -> None:
        """weight field accepts values in [0, 100]."""
        item = BlueprintElementPreferenceItem(weight=0.0)
        assert item.weight == 0.0
        item = BlueprintElementPreferenceItem(weight=100.0)
        assert item.weight == 100.0

    def test_weight_rejects_out_of_range(self) -> None:
        """weight field rejects values outside [0, 100]."""
        with pytest.raises(ValidationError):
            BlueprintElementPreferenceItem(weight=-0.1)
        with pytest.raises(ValidationError):
            BlueprintElementPreferenceItem(weight=100.1)

    def test_enabled_toggle(self) -> None:
        """enabled field accepts True, False, and None."""
        item = BlueprintElementPreferenceItem(enabled=True)
        assert item.enabled is True
        item = BlueprintElementPreferenceItem(enabled=False)
        assert item.enabled is False
        item = BlueprintElementPreferenceItem(enabled=None)
        assert item.enabled is None

    def test_locked_flag(self) -> None:
        """locked field defaults to False."""
        item = BlueprintElementPreferenceItem()
        assert item.locked is False
        item = BlueprintElementPreferenceItem(locked=True)
        assert item.locked is True


# ── BlueprintElementPreferenceConfig ───────────────────────────


class TestBlueprintElementPreferenceConfig:
    """Tests for BlueprintElementPreferenceConfig schema."""

    def test_minimal_creation(self) -> None:
        """BlueprintElementPreferenceConfig can be created with minimal fields."""
        config = BlueprintElementPreferenceConfig()
        assert config.preset_id == ""
        assert config.manual_override is False
        assert config.items == []

    def test_with_items(self) -> None:
        """BlueprintElementPreferenceConfig accepts list of preference items."""
        items = [
            BlueprintElementPreferenceItem(element_id="ext_001", enabled=True, weight=80.0),
            BlueprintElementPreferenceItem(element_id="ext_002", locked=True),
        ]
        config = BlueprintElementPreferenceConfig(
            preset_id="mystery_standard",
            manual_override=True,
            items=items,
        )
        assert config.preset_id == "mystery_standard"
        assert config.manual_override is True
        assert len(config.items) == 2
        assert config.items[0].element_id == "ext_001"
        assert config.items[0].weight == 80.0
        assert config.items[1].locked is True


# ── BlueprintElementSelection ──────────────────────────────────


class TestBlueprintElementSelection:
    """Tests for BlueprintElementSelection schema."""

    def test_minimal_creation(self) -> None:
        """BlueprintElementSelection can be created with minimal fields."""
        selection = BlueprintElementSelection()
        assert selection.library_version == "2026.05"
        assert selection.mode == "long"
        assert selection.required_elements == []
        assert selection.extension_elements == []
        assert selection.quality_config_elements == []

    def test_with_elements(self) -> None:
        """BlueprintElementSelection accepts lists of element cards."""
        required = [
            BlueprintElementCard(
                element_id="req_001",
                name="主角弧光",
                category="character",
                description="主角的成长轨迹",
                rationale="主角必须有成长",
                tier="required",
            ),
        ]
        extension = [
            BlueprintElementCard(
                element_id="ext_001",
                name="情感弧线",
                category="emotion",
                description="情感走向",
                rationale="增强感染力",
            ),
        ]
        selection = BlueprintElementSelection(
            mode="short",
            selector_summary="选择了1个必需要素和1个扩展要素",
            required_elements=required,
            extension_elements=extension,
        )
        assert selection.mode == "short"
        assert len(selection.required_elements) == 1
        assert selection.required_elements[0].element_id == "req_001"
        assert selection.required_elements[0].tier == "required"
        assert len(selection.extension_elements) == 1
        assert selection.extension_elements[0].tier == "extension"

    def test_invalid_mode_rejected(self) -> None:
        """mode field only accepts 'short' or 'long'."""
        with pytest.raises(ValidationError):
            BlueprintElementSelection(mode="invalid")


# ── EmotionalArc ──────────────────────────────────────────────


class TestEmotionalArc:
    """Tests for EmotionalArc schema (outline.py)."""

    def test_import(self) -> None:
        """EmotionalArc is importable."""
        assert EmotionalArc is not None

    def test_default_values(self) -> None:
        """EmotionalArc uses correct defaults for all fields."""
        arc = EmotionalArc()
        assert arc.arc_name == ""
        assert arc.emotion_type == ""
        assert arc.peak_chapters == []
        assert arc.valley_chapters == []
        assert arc.description == ""
        assert arc.related_characters == []
        assert arc.related_subplots == []

    def test_full_creation(self) -> None:
        """EmotionalArc can be created with all fields populated."""
        arc = EmotionalArc(
            arc_name="主角成长弧",
            emotion_type="紧张",
            peak_chapters=[5, 12, 20],
            valley_chapters=[3, 10],
            description="从恐惧到勇敢的情感变化",
            related_characters=["李明", "张华"],
            related_subplots=["复仇线", "身世线"],
        )
        assert arc.arc_name == "主角成长弧"
        assert arc.emotion_type == "紧张"
        assert arc.peak_chapters == [5, 12, 20]
        assert arc.valley_chapters == [3, 10]
        assert arc.description == "从恐惧到勇敢的情感变化"
        assert arc.related_characters == ["李明", "张华"]
        assert arc.related_subplots == ["复仇线", "身世线"]

    def test_model_validate_from_dict(self) -> None:
        """EmotionalArc can be created via model_validate with LLM-like JSON."""
        data = {
            "arc_name": "温馨线",
            "emotion_type": "温馨",
            "peak_chapters": [8, 15],
            "valley_chapters": [6],
            "description": "家庭团聚的情感高潮",
            "related_characters": ["奶奶"],
            "related_subplots": ["亲情线"],
        }
        arc = EmotionalArc.model_validate(data)
        assert arc.arc_name == "温馨线"
        assert arc.emotion_type == "温馨"
        assert arc.peak_chapters == [8, 15]

    def test_empty_lists_accepted(self) -> None:
        """EmotionalArc accepts empty lists for all list fields."""
        arc = EmotionalArc(
            peak_chapters=[],
            valley_chapters=[],
            related_characters=[],
            related_subplots=[],
        )
        assert arc.peak_chapters == []
        assert arc.valley_chapters == []
        assert arc.related_characters == []
        assert arc.related_subplots == []

    def test_string_coercion_for_str_list_fields(self) -> None:
        """String inputs to list[str] fields are coerced to single-item lists."""
        arc = EmotionalArc(related_characters="李明", related_subplots="复仇线")
        assert arc.related_characters == ["李明"]
        assert arc.related_subplots == ["复仇线"]

    def test_text_field_coercion(self) -> None:
        """Text fields handle None and whitespace gracefully."""
        arc = EmotionalArc(arc_name=None, emotion_type="  ", description=None)
        assert arc.arc_name == ""
        assert arc.emotion_type == ""
        assert arc.description == ""


# ── CausalChain ───────────────────────────────────────────────


class TestCausalChain:
    """Tests for CausalChain schema (outline.py)."""

    def test_import(self) -> None:
        """CausalChain is importable."""
        assert CausalChain is not None

    def test_default_values(self) -> None:
        """CausalChain uses correct defaults for all fields."""
        chain = CausalChain()
        assert chain.chain_name == ""
        assert chain.trigger_chapter == 0
        assert chain.trigger_action == ""
        assert chain.intermediate_chapters == []
        assert chain.payoff_chapter == 0
        assert chain.payoff_event == ""
        assert chain.escalation == ""
        assert chain.involved_subplots == []

    def test_full_creation(self) -> None:
        """CausalChain can be created with all fields populated."""
        chain = CausalChain(
            chain_name="背叛因果链",
            trigger_chapter=3,
            trigger_action="主角发现盟友的秘密",
            intermediate_chapters=[5, 7, 9],
            payoff_chapter=12,
            payoff_event="盟友反目，主角陷入绝境",
            escalation="每章揭示更多真相",
            involved_subplots=["盟友线", "阴谋线"],
        )
        assert chain.chain_name == "背叛因果链"
        assert chain.trigger_chapter == 3
        assert chain.trigger_action == "主角发现盟友的秘密"
        assert chain.intermediate_chapters == [5, 7, 9]
        assert chain.payoff_chapter == 12
        assert chain.payoff_event == "盟友反目，主角陷入绝境"
        assert chain.escalation == "每章揭示更多真相"
        assert chain.involved_subplots == ["盟友线", "阴谋线"]

    def test_model_validate_from_dict(self) -> None:
        """CausalChain can be created via model_validate with LLM-like JSON."""
        data = {
            "chain_name": "身份揭露",
            "trigger_chapter": 1,
            "trigger_action": "一封匿名信",
            "intermediate_chapters": [2, 4],
            "payoff_chapter": 6,
            "payoff_event": "真相大白",
            "escalation": "层层递进",
            "involved_subplots": ["悬疑线"],
        }
        chain = CausalChain.model_validate(data)
        assert chain.chain_name == "身份揭露"
        assert chain.trigger_chapter == 1
        assert chain.intermediate_chapters == [2, 4]

    def test_trigger_chapter_zero_boundary(self) -> None:
        """trigger_chapter accepts 0 (unspecified)."""
        chain = CausalChain(trigger_chapter=0)
        assert chain.trigger_chapter == 0

    def test_trigger_chapter_rejects_negative(self) -> None:
        """trigger_chapter rejects negative values."""
        with pytest.raises(ValidationError):
            CausalChain(trigger_chapter=-1)

    def test_payoff_chapter_rejects_negative(self) -> None:
        """payoff_chapter rejects negative values."""
        with pytest.raises(ValidationError):
            CausalChain(payoff_chapter=-1)

    def test_string_coercion_for_involved_subplots(self) -> None:
        """String input to involved_subplots is coerced to single-item list."""
        chain = CausalChain(involved_subplots="阴谋线")
        assert chain.involved_subplots == ["阴谋线"]

    def test_text_field_coercion(self) -> None:
        """Text fields handle None gracefully."""
        chain = CausalChain(
            chain_name=None, trigger_action=None, payoff_event=None, escalation=None
        )
        assert chain.chain_name == ""
        assert chain.trigger_action == ""
        assert chain.payoff_event == ""
        assert chain.escalation == ""


# ── SubplotCollision ──────────────────────────────────────────


class TestSubplotCollision:
    """Tests for SubplotCollision schema (outline.py)."""

    def test_import(self) -> None:
        """SubplotCollision is importable."""
        assert SubplotCollision is not None

    def test_default_values(self) -> None:
        """SubplotCollision uses correct defaults for all fields."""
        collision = SubplotCollision()
        assert collision.collision_chapter == 0
        assert collision.involved_subplots == []
        assert collision.collision_type == ""
        assert collision.outcome == ""
        assert collision.ripple_effects == []
        assert collision.setup_chapters == []

    def test_full_creation(self) -> None:
        """SubplotCollision can be created with all fields populated."""
        collision = SubplotCollision(
            collision_chapter=10,
            involved_subplots=["复仇线", "爱情线"],
            collision_type="冲突",
            outcome="主角被迫在复仇与爱情间抉择",
            ripple_effects=["复仇线暂停", "爱情线升温"],
            setup_chapters=[7, 8, 9],
        )
        assert collision.collision_chapter == 10
        assert collision.involved_subplots == ["复仇线", "爱情线"]
        assert collision.collision_type == "冲突"
        assert collision.outcome == "主角被迫在复仇与爱情间抉择"
        assert collision.ripple_effects == ["复仇线暂停", "爱情线升温"]
        assert collision.setup_chapters == [7, 8, 9]

    def test_model_validate_from_dict(self) -> None:
        """SubplotCollision can be created via model_validate with LLM-like JSON."""
        data = {
            "collision_chapter": 15,
            "involved_subplots": ["阴谋线", "身世线", "复仇线"],
            "collision_type": "融合",
            "outcome": "三条线索汇聚揭示真相",
            "ripple_effects": ["主线推进"],
            "setup_chapters": [12, 13, 14],
        }
        collision = SubplotCollision.model_validate(data)
        assert collision.collision_chapter == 15
        assert len(collision.involved_subplots) == 3
        assert collision.collision_type == "融合"

    def test_collision_chapter_zero_boundary(self) -> None:
        """collision_chapter accepts 0 (unspecified)."""
        collision = SubplotCollision(collision_chapter=0)
        assert collision.collision_chapter == 0

    def test_collision_chapter_rejects_negative(self) -> None:
        """collision_chapter rejects negative values."""
        with pytest.raises(ValidationError):
            SubplotCollision(collision_chapter=-1)

    def test_string_coercion_for_list_fields(self) -> None:
        """String inputs to list[str] fields are coerced to single-item lists."""
        collision = SubplotCollision(
            involved_subplots="复仇线", ripple_effects="影响A"
        )
        assert collision.involved_subplots == ["复仇线"]
        assert collision.ripple_effects == ["影响A"]

    def test_text_field_coercion(self) -> None:
        """Text fields handle None gracefully."""
        collision = SubplotCollision(collision_type=None, outcome=None)
        assert collision.collision_type == ""
        assert collision.outcome == ""


# ── SubversionPoint ───────────────────────────────────────────


class TestSubversionPoint:
    """Tests for SubversionPoint schema (outline.py)."""

    def test_import(self) -> None:
        """SubversionPoint is importable."""
        assert SubversionPoint is not None

    def test_default_values(self) -> None:
        """SubversionPoint uses correct defaults for all fields."""
        point = SubversionPoint()
        assert point.chapter == 0
        assert point.expected_outcome == ""
        assert point.actual_outcome == ""
        assert point.setup_chapters == []
        assert point.justification == ""
        assert point.related_subplots == []

    def test_full_creation(self) -> None:
        """SubversionPoint can be created with all fields populated."""
        point = SubversionPoint(
            chapter=8,
            expected_outcome="主角击败反派",
            actual_outcome="反派其实是主角的父亲",
            setup_chapters=[5, 6, 7],
            justification="前文埋下身份伏笔，符合角色动机",
            related_subplots=["身世线", "反派线"],
        )
        assert point.chapter == 8
        assert point.expected_outcome == "主角击败反派"
        assert point.actual_outcome == "反派其实是主角的父亲"
        assert point.setup_chapters == [5, 6, 7]
        assert point.justification == "前文埋下身份伏笔，符合角色动机"
        assert point.related_subplots == ["身世线", "反派线"]

    def test_model_validate_from_dict(self) -> None:
        """SubversionPoint can be created via model_validate with LLM-like JSON."""
        data = {
            "chapter": 20,
            "expected_outcome": "大团圆结局",
            "actual_outcome": "主角选择牺牲自我",
            "setup_chapters": [18, 19],
            "justification": "呼应开篇的牺牲主题",
            "related_subplots": ["牺牲线"],
        }
        point = SubversionPoint.model_validate(data)
        assert point.chapter == 20
        assert point.expected_outcome == "大团圆结局"
        assert point.actual_outcome == "主角选择牺牲自我"

    def test_chapter_zero_boundary(self) -> None:
        """chapter accepts 0 (unspecified)."""
        point = SubversionPoint(chapter=0)
        assert point.chapter == 0

    def test_chapter_rejects_negative(self) -> None:
        """chapter rejects negative values."""
        with pytest.raises(ValidationError):
            SubversionPoint(chapter=-1)

    def test_string_coercion_for_related_subplots(self) -> None:
        """String input to related_subplots is coerced to single-item list."""
        point = SubversionPoint(related_subplots="身世线")
        assert point.related_subplots == ["身世线"]

    def test_text_field_coercion(self) -> None:
        """Text fields handle None gracefully."""
        point = SubversionPoint(
            expected_outcome=None, actual_outcome=None, justification=None
        )
        assert point.expected_outcome == ""
        assert point.actual_outcome == ""
        assert point.justification == ""

    def test_empty_setup_chapters(self) -> None:
        """SubversionPoint accepts empty setup_chapters list."""
        point = SubversionPoint(setup_chapters=[])
        assert point.setup_chapters == []


# ── NarrativeBlueprint new fields ─────────────────────────────


class TestNarrativeBlueprintNewFields:
    """Tests for NarrativeBlueprint accepting the four new schema fields."""

    def test_blueprint_accepts_emotional_arcs(self) -> None:
        """NarrativeBlueprint accepts emotional_arcs field."""
        arc = EmotionalArc(arc_name="test_arc", emotion_type="紧张")
        bp = NarrativeBlueprint(emotional_arcs=[arc])
        assert len(bp.emotional_arcs) == 1
        assert bp.emotional_arcs[0].arc_name == "test_arc"

    def test_blueprint_accepts_causal_chains(self) -> None:
        """NarrativeBlueprint accepts causal_chains field."""
        chain = CausalChain(chain_name="test_chain", trigger_chapter=1)
        bp = NarrativeBlueprint(causal_chains=[chain])
        assert len(bp.causal_chains) == 1
        assert bp.causal_chains[0].chain_name == "test_chain"

    def test_blueprint_accepts_subplot_collisions(self) -> None:
        """NarrativeBlueprint accepts subplot_collisions field."""
        collision = SubplotCollision(collision_chapter=5, collision_type="冲突")
        bp = NarrativeBlueprint(subplot_collisions=[collision])
        assert len(bp.subplot_collisions) == 1
        assert bp.subplot_collisions[0].collision_chapter == 5

    def test_blueprint_accepts_subversion_points(self) -> None:
        """NarrativeBlueprint accepts subversion_points field."""
        point = SubversionPoint(chapter=10, expected_outcome="胜利")
        bp = NarrativeBlueprint(subversion_points=[point])
        assert len(bp.subversion_points) == 1
        assert bp.subversion_points[0].chapter == 10

    def test_blueprint_defaults_all_new_fields_to_empty(self) -> None:
        """NarrativeBlueprint defaults all four new fields to empty lists."""
        bp = NarrativeBlueprint()
        assert bp.emotional_arcs == []
        assert bp.causal_chains == []
        assert bp.subplot_collisions == []
        assert bp.subversion_points == []

    def test_blueprint_model_validate_with_all_new_fields(self) -> None:
        """NarrativeBlueprint.model_validate works with LLM-like JSON containing new fields."""
        data = {
            "synopsis": "一个关于成长的故事",
            "emotional_arcs": [
                {"arc_name": "成长弧", "emotion_type": "紧张", "peak_chapters": [10]}
            ],
            "causal_chains": [
                {"chain_name": "背叛链", "trigger_chapter": 3, "payoff_chapter": 15}
            ],
            "subplot_collisions": [
                {
                    "collision_chapter": 12,
                    "involved_subplots": ["A", "B"],
                    "collision_type": "融合",
                }
            ],
            "subversion_points": [
                {"chapter": 8, "expected_outcome": "胜利", "actual_outcome": "失败"}
            ],
        }
        bp = NarrativeBlueprint.model_validate(data)
        assert bp.synopsis == "一个关于成长的故事"
        assert len(bp.emotional_arcs) == 1
        assert bp.emotional_arcs[0].arc_name == "成长弧"
        assert len(bp.causal_chains) == 1
        assert bp.causal_chains[0].trigger_chapter == 3
        assert len(bp.subplot_collisions) == 1
        assert bp.subplot_collisions[0].collision_type == "融合"
        assert len(bp.subversion_points) == 1
        assert bp.subversion_points[0].actual_outcome == "失败"


# ── BlueprintFragments new fields ─────────────────────────────


class TestBlueprintFragmentsNewFields:
    """Tests for BlueprintFragments accepting the four new schema fields."""

    def test_fragments_accepts_emotional_arcs(self) -> None:
        """BlueprintFragments accepts emotional_arcs as list[dict]."""
        frags = BlueprintFragments(
            emotional_arcs=[{"arc_name": "test", "emotion_type": "温馨"}]
        )
        assert len(frags.emotional_arcs) == 1
        assert frags.emotional_arcs[0]["arc_name"] == "test"

    def test_fragments_accepts_causal_chains(self) -> None:
        """BlueprintFragments accepts causal_chains as list[dict]."""
        frags = BlueprintFragments(
            causal_chains=[{"chain_name": "test", "trigger_chapter": 1}]
        )
        assert len(frags.causal_chains) == 1
        assert frags.causal_chains[0]["chain_name"] == "test"

    def test_fragments_accepts_subplot_collisions(self) -> None:
        """BlueprintFragments accepts subplot_collisions as list[dict]."""
        frags = BlueprintFragments(
            subplot_collisions=[{"collision_chapter": 5, "collision_type": "互助"}]
        )
        assert len(frags.subplot_collisions) == 1
        assert frags.subplot_collisions[0]["collision_type"] == "互助"

    def test_fragments_accepts_subversion_points(self) -> None:
        """BlueprintFragments accepts subversion_points as list[dict]."""
        frags = BlueprintFragments(
            subversion_points=[{"chapter": 10, "expected_outcome": "活"}]
        )
        assert len(frags.subversion_points) == 1
        assert frags.subversion_points[0]["chapter"] == 10

    def test_fragments_defaults_all_new_fields_to_empty(self) -> None:
        """BlueprintFragments defaults all four new fields to empty lists."""
        frags = BlueprintFragments()
        assert frags.emotional_arcs == []
        assert frags.causal_chains == []
        assert frags.subplot_collisions == []
        assert frags.subversion_points == []

    def test_fragments_model_validate_with_new_fields(self) -> None:
        """BlueprintFragments.model_validate works with dict containing new fields."""
        data = {
            "synopsis": "test synopsis",
            "emotional_arcs": [{"arc_name": "arc1"}],
            "causal_chains": [{"chain_name": "chain1"}],
            "subplot_collisions": [{"collision_chapter": 3}],
            "subversion_points": [{"chapter": 7}],
        }
        frags = BlueprintFragments.model_validate(data)
        assert frags.synopsis == "test synopsis"
        assert len(frags.emotional_arcs) == 1
        assert len(frags.causal_chains) == 1
        assert len(frags.subplot_collisions) == 1
        assert len(frags.subversion_points) == 1
