"""Tests for SceneIntent field validators — ``mode="before"`` type coercion.

Ensures all 18 string-typed creative fields on ``SceneIntent`` are coerced
from non-string inputs (``dict``, ``list``, ``int``, ``None``) via the shared
``stringify_text_value`` utility.
"""

from __future__ import annotations

from typing import Any

import pytest

from novel_forge.core.schemas.continuity import (
    ChapterBridge,
    ChapterPlan,
    CharacterMotivationHint,
    NarrativeBlueprintContext,
    SceneIntent,
)

SCENE_INTENT_STRING_FIELDS: list[str] = [
    "summary",
    "purpose",
    "conflict",
    "required_outcome",
    "exit_target_state",
    "location",
    "time_marker",
    "relationship_dynamics",
    "emotional_beat",
    "sensory_notes",
    "choice_pressure",
    "scene_resistance",
    "revelation_level",
    "symbol_usage_policy",
    "scene_goal",
    "handoff_to_next",
    "entry_state",
    "exit_state",
]

NON_STRING_INPUTS: list[tuple[str, Any]] = [
    ("dict", {"nested": "value"}),
    ("list", ["item1", "item2"]),
    ("int", 42),
    ("none", None),
]

_REQUIRED_FIELDS: dict[str, Any] = {
    "scene_id": "s1",
    "summary": "test",
}


def _make_instance(field_name: str, field_value: Any) -> SceneIntent:
    kwargs = dict(_REQUIRED_FIELDS)
    kwargs[field_name] = field_value
    return SceneIntent(**kwargs)


class TestSceneIntentStringFieldCoercion:
    @pytest.mark.parametrize("field_name", SCENE_INTENT_STRING_FIELDS)
    @pytest.mark.parametrize(
        ("input_label", "non_string_input"),
        NON_STRING_INPUTS,
    )
    def test_field_coerces_to_string(
        self,
        field_name: str,
        input_label: str,
        non_string_input: Any,
    ) -> None:
        instance = _make_instance(field_name, non_string_input)
        value = getattr(instance, field_name)
        assert isinstance(value, str), (
            f"Expected field '{field_name}' to be str, got {type(value).__name__}: {value!r}"
        )

    @pytest.mark.parametrize("field_name", SCENE_INTENT_STRING_FIELDS)
    def test_field_round_trips_string_value(self, field_name: str) -> None:
        original = "  some text value  "
        instance = _make_instance(field_name, original)
        value = getattr(instance, field_name)
        assert value == original.strip(), (
            f"Expected {field_name} to be '{original.strip()}', got {value!r}"
        )


class TestSceneIntentNonStringFieldsUnchanged:
    def test_required_characters_preserves_list(self) -> None:
        chars = ["Alice", "Bob"]
        instance = SceneIntent(scene_id="s1", summary="test", required_characters=chars)
        assert instance.required_characters == chars

    def test_body_signal_budget_preserves_int(self) -> None:
        instance = SceneIntent(scene_id="s1", summary="test", body_signal_budget=3)
        assert instance.body_signal_budget == 3

    def test_target_words_preserves_int(self) -> None:
        instance = SceneIntent(scene_id="s1", summary="test", target_words=2000)
        assert instance.target_words == 2000

    def test_pov_switch_allowed_preserves_bool(self) -> None:
        instance = SceneIntent(scene_id="s1", summary="test", pov_switch_allowed=True)
        assert instance.pov_switch_allowed is True

    def test_draft_order_preserves_int(self) -> None:
        instance = SceneIntent(scene_id="s1", summary="test", draft_order=3)
        assert instance.draft_order == 3

    def test_dialogue_voice_targets_preserves_dict(self) -> None:
        targets = {"Alice": "soft", "Bob": "gruff"}
        instance = SceneIntent(scene_id="s1", summary="test", dialogue_voice_targets=targets)
        assert instance.dialogue_voice_targets == targets

    def test_entry_state_refs_preserves_list(self) -> None:
        refs = ["ref1", "ref2"]
        instance = SceneIntent(scene_id="s1", summary="test", entry_state_refs=refs)
        assert instance.entry_state_refs == refs

    def test_character_motivations_preserves_list(self) -> None:
        hints = [CharacterMotivationHint(character="Alice")]
        instance = SceneIntent(scene_id="s1", summary="test", character_motivations=hints)
        assert instance.character_motivations == hints


# ── ChapterBridge ─────────────────────────────────────────────────


BRIDGE_TEXT_FIELDS: list[str] = [
    "opening_time",
    "opening_location",
    "opening_pov",
    "transition_mode",
    "emotional_carryover",
    "action_handoff",
    "bridge_summary",
]


def _make_bridge(**overrides: Any) -> ChapterBridge:
    kwargs: dict[str, Any] = {"to_chapter": 2, "from_chapter": 1, **overrides}
    return ChapterBridge(**kwargs)


class TestChapterBridgeFieldCoercion:
    @pytest.mark.parametrize("field", BRIDGE_TEXT_FIELDS)
    def test_dict_coerces(self, field: str) -> None:
        inst = _make_bridge(**{field: {"nested": "object", "key": 42}})
        value = getattr(inst, field)
        assert isinstance(value, str)
        assert value == "nested: object；key: 42"

    @pytest.mark.parametrize("field", BRIDGE_TEXT_FIELDS)
    def test_list_coerces(self, field: str) -> None:
        inst = _make_bridge(**{field: ["item one", "item two", "item three"]})
        value = getattr(inst, field)
        assert isinstance(value, str)
        assert value == "item one；item two；item three"

    @pytest.mark.parametrize("field", BRIDGE_TEXT_FIELDS)
    def test_int_coerces(self, field: str) -> None:
        inst = _make_bridge(**{field: 42})
        value = getattr(inst, field)
        assert isinstance(value, str)
        assert value == "42"

    @pytest.mark.parametrize("field", BRIDGE_TEXT_FIELDS)
    def test_none_coerces(self, field: str) -> None:
        inst = _make_bridge(**{field: None})
        value = getattr(inst, field)
        assert isinstance(value, str)
        assert value == ""

    @pytest.mark.parametrize("field", BRIDGE_TEXT_FIELDS)
    def test_string_passthrough(self, field: str) -> None:
        inst = _make_bridge(**{field: "hello world"})
        value = getattr(inst, field)
        assert isinstance(value, str)
        assert value == "hello world"

    @pytest.mark.parametrize("field", BRIDGE_TEXT_FIELDS)
    def test_list_of_dicts_coerces(self, field: str) -> None:
        inst = _make_bridge(**{field: [{"a": "1"}, {"b": "2"}]})
        value = getattr(inst, field)
        assert isinstance(value, str)
        assert value == "a: 1；b: 2"

    def test_defaults_preserved(self) -> None:
        inst = _make_bridge()
        assert isinstance(inst.opening_time, str) and inst.opening_time == ""
        assert isinstance(inst.opening_location, str) and inst.opening_location == ""
        assert isinstance(inst.opening_pov, str) and inst.opening_pov == ""
        assert isinstance(inst.transition_mode, str) and inst.transition_mode == ""
        assert isinstance(inst.emotional_carryover, str) and inst.emotional_carryover == ""
        assert isinstance(inst.action_handoff, str) and inst.action_handoff == ""
        assert isinstance(inst.bridge_summary, str) and inst.bridge_summary == ""


# ── ChapterPlan ────────────────────────────────────────────────


class TestChapterPlanTypeCoercion:
    @pytest.mark.parametrize(
        ("field", "raw", "expected"),
        [
            ("opening_contract", None, ""),
            ("opening_contract", "", ""),
            ("opening_contract", 42, "42"),
            ("opening_contract", True, "True"),
            ("opening_contract", 3.14, "3.14"),
            ("opening_contract", {"theme": "betrayal"}, "theme: betrayal"),
            ("opening_contract", ["a", "b"], "a；b"),
            ("opening_contract", "  spaced  ", "spaced"),
            ("opening_contract", "hello", "hello"),
            ("closing_contract", None, ""),
            ("closing_contract", 0, "0"),
            ("closing_contract", {"note": "end"}, "note: end"),
            ("closing_contract", "goodbye", "goodbye"),
            ("emotional_arc", None, ""),
            ("emotional_arc", 99, "99"),
            (
                "emotional_arc",
                {"from": "sad", "to": "happy"},
                "from: sad；to: happy",
            ),
            ("emotional_arc", "tragic", "tragic"),
        ],
    )
    def test_str_field_coercion(self, field: str, raw: Any, expected: str) -> None:
        plan = ChapterPlan(**{field: raw})
        assert getattr(plan, field) == expected

    @pytest.mark.parametrize(
        ("field", "raw", "expected"),
        [
            ("foreshadowing_plan", "a single clue", ["a single clue"]),
            ("foreshadowing_plan", 123, ["123"]),
            ("foreshadowing_plan", None, []),
            ("foreshadowing_plan", {"k": "v"}, ["k: v"]),
            ("relationship_evolution", "tension rises", ["tension rises"]),
            ("relationship_evolution", True, ["True"]),
            ("relationship_evolution", None, []),
            ("forbidden_elements", "don't use this", ["don't use this"]),
            ("forbidden_elements", 0, ["0"]),
            ("forbidden_elements", None, []),
        ],
    )
    def test_list_field_nonlist_input(
        self,
        field: str,
        raw: Any,
        expected: list[str],
    ) -> None:
        plan = ChapterPlan(**{field: raw})
        assert getattr(plan, field) == expected

    @pytest.mark.parametrize(
        ("field", "raw", "expected"),
        [
            ("foreshadowing_plan", ["clue A", "clue B"], ["clue A", "clue B"]),
            ("relationship_evolution", ["closer", "conflict"], ["closer", "conflict"]),
            ("forbidden_elements", ["blood", "knife"], ["blood", "knife"]),
            ("forbidden_elements", [{"image": "blood"}, "knife"], ["image: blood", "knife"]),
        ],
    )
    def test_list_field_multi_item_list_input(
        self,
        field: str,
        raw: list[Any],
        expected: list[str],
    ) -> None:
        plan = ChapterPlan(**{field: raw})
        assert getattr(plan, field) == expected

    def test_defaults_unchanged(self) -> None:
        plan = ChapterPlan()
        assert plan.opening_contract == ""
        assert plan.closing_contract == ""
        assert plan.emotional_arc == ""
        assert plan.foreshadowing_plan == []
        assert plan.relationship_evolution == []
        assert plan.forbidden_elements == []

    def test_nested_dict_on_emotional_arc(self) -> None:
        plan = ChapterPlan(
            emotional_arc={
                "start": "hopeful",
                "midpoint": {"low": "despair"},
                "end": "relief",
            },
        )
        assert plan.emotional_arc == "start: hopeful；midpoint: low: despair；end: relief"

    def test_empty_dict_on_opening_contract(self) -> None:
        plan = ChapterPlan(opening_contract={})
        assert plan.opening_contract == ""

    def test_empty_list_on_opening_contract(self) -> None:
        plan = ChapterPlan(opening_contract=[])
        assert plan.opening_contract == ""


class TestNarrativeBlueprintContext:
    """mode="before" validators on NarrativeBlueprintContext string fields."""

    NARRATIVE_CTX_STRING_FIELDS: list[str] = [
        "current_phase_name",
        "current_phase_description",
        "current_phase_tension_level",
        "current_phase_time_context",
        "turning_point_description",
        "next_turning_point_description",
    ]

    @pytest.mark.parametrize("field", NARRATIVE_CTX_STRING_FIELDS)
    def test_coerces_dict(self, field: str) -> None:
        ctx = NarrativeBlueprintContext(**{field: {"zh": "val"}})  # type: ignore[arg-type]
        assert isinstance(getattr(ctx, field), str)

    @pytest.mark.parametrize("field", NARRATIVE_CTX_STRING_FIELDS)
    def test_coerces_list(self, field: str) -> None:
        ctx = NarrativeBlueprintContext(**{field: ["a", "b"]})  # type: ignore[arg-type]
        val = getattr(ctx, field)
        assert isinstance(val, str)
        assert "；" in val or val == ""

    @pytest.mark.parametrize("field", NARRATIVE_CTX_STRING_FIELDS)
    def test_coerces_int(self, field: str) -> None:
        ctx = NarrativeBlueprintContext(**{field: 42})  # type: ignore[arg-type]
        assert getattr(ctx, field) == "42"

    @pytest.mark.parametrize("field", NARRATIVE_CTX_STRING_FIELDS)
    def test_coerces_none(self, field: str) -> None:
        ctx = NarrativeBlueprintContext(**{field: None})  # type: ignore[arg-type]
        assert getattr(ctx, field) == ""

    @pytest.mark.parametrize("field", NARRATIVE_CTX_STRING_FIELDS)
    def test_keeps_str(self, field: str) -> None:
        ctx = NarrativeBlueprintContext(**{field: "测试值"})  # type: ignore[arg-type]
        assert getattr(ctx, field) == "测试值"

    def test_defaults_unchanged(self) -> None:
        ctx = NarrativeBlueprintContext()
        for field in self.NARRATIVE_CTX_STRING_FIELDS:
            assert getattr(ctx, field) == "", f"Expected {field} default to be ''"
        assert ctx.is_turning_point is False
        assert ctx.next_turning_point_chapter == 0

    def test_non_string_fields_unchanged(self) -> None:
        ctx = NarrativeBlueprintContext(
            is_turning_point=True,
            next_turning_point_chapter=5,
        )
        assert ctx.is_turning_point is True
        assert ctx.next_turning_point_chapter == 5
