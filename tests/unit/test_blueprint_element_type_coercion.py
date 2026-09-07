"""Tests for ``mode="before"`` field validators on BlueprintElementCard.

These validators call ``stringify_text_value`` to flatten nested dict/list
structures that LLM outputs sometimes produce for string-typed element fields.
"""

from __future__ import annotations

from typing import Any

import pytest

from novel_forge.core.schemas.blueprint_elements import BlueprintElementCard
from tests.unit.test_schema_type_coercion_helpers import assert_field_coerces_to_string

# ── BlueprintElementCard ───────────────────────────────────────

BLUEPRINT_ELEMENT_STRING_FIELDS: list[str] = [
    "element_id",
    "name",
    "description",
    "rationale",
    "prompt_hint",
    "ui_hint",
    "selection_reason",
]


class TestBlueprintElementCardStringFieldCoercion:
    """Verify all 7 text fields coerce non-string input to str."""

    @pytest.mark.parametrize("field", BLUEPRINT_ELEMENT_STRING_FIELDS)
    @pytest.mark.parametrize(
        ("input_val", "expected"),
        [
            # plain string — passthrough
            ("some text", None),
            # None → ""
            (None, ""),
            # int → str
            (42, "42"),
            # float → str
            (3.14, "3.14"),
            # dict → flattened
            ({"key": "value"}, "key: value"),
            # list → joined
            (["a", "b"], "a；b"),
            # empty string → ""
            ("", ""),
            # empty list → ""
            ([], ""),
            # empty dict → ""
            ({}, ""),
        ],
        ids=[
            "plain_string",
            "none",
            "int",
            "float",
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
        assert_field_coerces_to_string(BlueprintElementCard, field, input_val, expected)

    @pytest.mark.parametrize("field", BLUEPRINT_ELEMENT_STRING_FIELDS)
    def test_field_default_is_empty_string(self, field: str) -> None:
        instance = BlueprintElementCard()
        # ui_hint defaults to "checklist" — skip exact empty-string assertion
        if field == "ui_hint":
            assert getattr(instance, field) == "checklist"
            return
        assert getattr(instance, field) == "", (
            f"Expected {field} to default to '', got {getattr(instance, field)!r}"
        )


# ── Non-target fields untouched ────────────────────────────────


class TestBlueprintElementCardNonTargetFields:
    """Verify fields without validators are not affected."""

    def test_category_preserves_string(self) -> None:
        instance = BlueprintElementCard(category="plot")
        assert instance.category == "plot"

    def test_tier_preserves_literal(self) -> None:
        instance = BlueprintElementCard(tier="required")
        assert instance.tier == "required"

    def test_recommended_genres_preserves_list(self) -> None:
        instance = BlueprintElementCard(recommended_genres=["scifi", "fantasy"])
        assert instance.recommended_genres == ["scifi", "fantasy"]

    def test_library_source_preserves_string(self) -> None:
        instance = BlueprintElementCard(library_source="external")
        assert instance.library_source == "external"

    def test_selection_source_preserves_string(self) -> None:
        instance = BlueprintElementCard(selection_source="llm")
        assert instance.selection_source == "llm"

    def test_selection_score_preserves_float(self) -> None:
        instance = BlueprintElementCard(selection_score=0.85)
        assert instance.selection_score == 0.85

    def test_user_weight_preserves_float_or_none(self) -> None:
        instance = BlueprintElementCard(user_weight=None)
        assert instance.user_weight is None
        instance = BlueprintElementCard(user_weight=75.0)
        assert instance.user_weight == 75.0

    def test_user_locked_preserves_bool(self) -> None:
        instance = BlueprintElementCard(user_locked=True)
        assert instance.user_locked is True

    def test_user_enabled_preserves_bool_or_none(self) -> None:
        instance = BlueprintElementCard(user_enabled=None)
        assert instance.user_enabled is None
        instance = BlueprintElementCard(user_enabled=False)
        assert instance.user_enabled is False
