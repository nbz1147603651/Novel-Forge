"""Tests for ``mode="before"`` field validators on ``CharacterAnalysis``,
``NarrativeAnalysis``, and ``ThematicAnalysis`` in ``short_creative.py``.

These validators call ``stringify_text_value`` to flatten nested dict/list
structures that LLM outputs sometimes produce for string-typed creative fields.
"""

from __future__ import annotations

from typing import Any

import pytest

from novel_forge.core.schemas.short_creative import (
    CharacterAnalysis,
    NarrativeAnalysis,
    ThematicAnalysis,
)
from tests.unit.test_schema_type_coercion_helpers import (
    assert_field_coerces_to_string,
    assert_list_field_coerces,
)

# ── CharacterAnalysis ──────────────────────────────────────────


class TestCharacterAnalysisArcSummaryCoercion:
    """Verify ``arc_summary`` coerces non-string input to str."""

    @pytest.mark.parametrize(
        ("input_val", "expected"),
        [
            ("主角从一个懦弱的少年成长为英雄", None),
            (None, ""),
            (42, "42"),
            ({"起点": "懦弱", "终点": "英雄"}, "起点: 懦弱；终点: 英雄"),
            (["懦弱", "成长", "英雄"], "懦弱；成长；英雄"),
            ("", ""),
            ([], ""),
            ({}, ""),
        ],
        ids=[
            "plain_string",
            "none",
            "int",
            "dict",
            "list",
            "empty_string",
            "empty_list",
            "empty_dict",
        ],
    )
    def test_arc_summary_coerces_to_string(
        self,
        input_val: Any,
        expected: str | None,
    ) -> None:
        assert_field_coerces_to_string(
            CharacterAnalysis, "arc_summary", input_val, expected
        )

    def test_arc_summary_default_is_empty_string(self) -> None:
        instance = CharacterAnalysis()
        assert instance.arc_summary == ""


class TestCharacterAnalysisKeyTraitsCoercion:
    """Verify ``key_traits`` coerces non-list input to list[str]."""

    @pytest.mark.parametrize(
        ("input_val", "expected"),
        [
            (["勇敢", "智慧", "犹豫"], ["勇敢", "智慧", "犹豫"]),
            ("勇敢", ["勇敢"]),
            (None, []),
            ("", [""]),
            ([], []),
        ],
        ids=[
            "list_of_strings",
            "bare_string",
            "none",
            "empty_string",
            "empty_list",
        ],
    )
    def test_key_traits_coerces_to_list(
        self,
        input_val: Any,
        expected: list[str],
    ) -> None:
        assert_list_field_coerces(
            CharacterAnalysis, "key_traits", input_val, expected
        )

    def test_key_traits_default_is_empty_list(self) -> None:
        instance = CharacterAnalysis()
        assert instance.key_traits == []


# ── NarrativeAnalysis ─────────────────────────────────────────


NARRATIVE_STRING_FIELDS: list[str] = [
    "pacing_assessment",
    "tension_curve",
    "tension_curve_note",
    "structure_type",
    "opening_hook",
    "ending_impact",
]


class TestNarrativeAnalysisStringFieldCoercion:
    """Verify all string-typed creative fields coerce non-string input to str."""

    @pytest.mark.parametrize("field", NARRATIVE_STRING_FIELDS)
    @pytest.mark.parametrize(
        ("input_val", "expected"),
        [
            ("plain text", None),
            (None, ""),
            (99, "99"),
            ({"nested": "value"}, "nested: value"),
            (["item_a", "item_b"], "item_a；item_b"),
            ("", ""),
            ([], ""),
            ({}, ""),
        ],
        ids=[
            "plain_string",
            "none",
            "int",
            "dict",
            "list",
            "empty_string",
            "empty_list",
            "empty_dict",
        ],
    )
    def test_field_coerces_to_string(
        self,
        field: str,
        input_val: Any,
        expected: str | None,
    ) -> None:
        assert_field_coerces_to_string(NarrativeAnalysis, field, input_val, expected)

    @pytest.mark.parametrize("field", NARRATIVE_STRING_FIELDS)
    def test_field_default_is_empty_string(self, field: str) -> None:
        instance = NarrativeAnalysis()
        assert getattr(instance, field) == "", (
            f"Expected {field} to default to '', got {getattr(instance, field)!r}"
        )


# ── ThematicAnalysis ───────────────────────────────────────────


THEMATIC_STRING_FIELDS: list[str] = [
    "core_theme",
    "theme_delivery",
    "emotional_resonance",
]


class TestThematicAnalysisStringFieldCoercion:
    """Verify all string-typed creative fields coerce non-string input to str."""

    @pytest.mark.parametrize("field", THEMATIC_STRING_FIELDS)
    @pytest.mark.parametrize(
        ("input_val", "expected"),
        [
            ("plain text", None),
            (None, ""),
            (True, "True"),
            ({"key": "value"}, "key: value"),
            (["first", "second"], "first；second"),
            ("", ""),
            ([], ""),
            ({}, ""),
        ],
        ids=[
            "plain_string",
            "none",
            "bool",
            "dict",
            "list",
            "empty_string",
            "empty_list",
            "empty_dict",
        ],
    )
    def test_field_coerces_to_string(
        self,
        field: str,
        input_val: Any,
        expected: str | None,
    ) -> None:
        assert_field_coerces_to_string(ThematicAnalysis, field, input_val, expected)

    @pytest.mark.parametrize("field", THEMATIC_STRING_FIELDS)
    def test_field_default_is_empty_string(self, field: str) -> None:
        instance = ThematicAnalysis()
        assert getattr(instance, field) == "", (
            f"Expected {field} to default to '', got {getattr(instance, field)!r}"
        )


# ── Non-target fields untouched ────────────────────────────────


class TestCharacterAnalysisNonTargetFields:
    """Verify fields without validators are not affected."""

    def test_name_preserves_string(self) -> None:
        instance = CharacterAnalysis(name="Alice")
        assert instance.name == "Alice"

    def test_role_preserves_string(self) -> None:
        instance = CharacterAnalysis(role="protagonist")
        assert instance.role == "protagonist"

    def test_relationships_preserves_list(self) -> None:
        rels = [{"target": "Bob", "type": "friend", "note": "childhood"}]
        instance = CharacterAnalysis(relationships=rels)
        assert len(instance.relationships) == 1
        assert instance.relationships[0].target == "Bob"


class TestNarrativeAnalysisNonTargetFields:
    """Verify fields without validators are not affected."""

    def test_turning_points_preserves_list(self) -> None:
        tps = [{"description": "转折", "effectiveness": "high"}]
        instance = NarrativeAnalysis(turning_points=tps)
        assert len(instance.turning_points) == 1
        assert instance.turning_points[0].description == "转折"


class TestThematicAnalysisNonTargetFields:
    """Verify fields without validators are not affected."""

    def test_symbolic_elements_preserves_list(self) -> None:
        elems = ["剑", "镜"]
        instance = ThematicAnalysis(symbolic_elements=elems)
        assert instance.symbolic_elements == ["剑", "镜"]
