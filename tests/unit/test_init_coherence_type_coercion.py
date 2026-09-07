"""Tests for ``mode="before"`` field validators on InitCoherenceProfile, CoherenceClaim, InitCreativeRefinementSuggestion.

These validators call ``stringify_text_value`` to flatten nested dict/list
structures that LLM outputs sometimes produce for string-typed creative fields.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from novel_forge.core.schemas.init_coherence import (
    CoherenceClaim,
    InitCoherenceProfile,
    InitCreativeRefinementSuggestion,
)
from novel_forge.narrative_state.schemas import CognitiveConstraint
from tests.unit.test_schema_type_coercion_helpers import assert_field_coerces_to_string

# ── Helpers ────────────────────────────────────────────────────

_COHERENCE_CLAIM_REQUIRED: dict[str, Any] = {
    "claim_id": "c1",
    "artifact": "story_bible",
    "source_path": "/dev/chaos",
    "cognitive_subjects": ["玄昱"],
    "cognitive_object": "沈清漪身份",
    "cognitive_level": "confirmed",
    "action_level": "internal",
    "reader_awareness": "partial",
    "character_knowledge_coverage": {"玄昱": "partial"},
    "cognitive_chapter": None,
    "public_reveal_chapter": None,
    "foreshadow_chapters": [],
    "claim_text": "something happened",
    "evidence": "sourced",
}

_REFINEMENT_REQUIRED: dict[str, Any] = {
    "suggestion_id": "s1",
}


def _make_claim(**overrides: Any) -> dict[str, Any]:
    """Return a dict with required CoherenceClaim fields + overrides."""
    data = dict(_COHERENCE_CLAIM_REQUIRED)
    data.update(overrides)
    return data


# ── InitCoherenceProfile ───────────────────────────────────────


INIT_COHERENCE_PROFILE_STRING_FIELDS: list[str] = [
    "summary",
]


class TestInitCoherenceProfileStringFieldCoercion:
    """Verify ``summary`` coerces non-string input to str."""

    @pytest.mark.parametrize("field", INIT_COHERENCE_PROFILE_STRING_FIELDS)
    @pytest.mark.parametrize(
        ("input_val", "expected"),
        [
            ("故事是一个关于成长的叙事", None),
            (None, ""),
            (42, "42"),
            ({"核心": "成长"}, "核心: 成长"),
            (["成长", "探索"], "成长；探索"),
            ("", ""),
            ([], ""),
            ({}, ""),
            (3.14, "3.14"),
            (True, "True"),
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
            "float",
            "bool",
        ],
    )
    def test_field_coerces_to_string(
        self,
        field: str,
        input_val: Any,
        expected: str | None,
    ) -> None:
        assert_field_coerces_to_string(InitCoherenceProfile, field, input_val, expected)

    @pytest.mark.parametrize("field", INIT_COHERENCE_PROFILE_STRING_FIELDS)
    def test_field_default_is_empty_string(self, field: str) -> None:
        instance = InitCoherenceProfile()
        assert getattr(instance, field) == "", (
            f"Expected {field} to default to '', got {getattr(instance, field)!r}"
        )


# ── CoherenceClaim ─────────────────────────────────────────────


COHERENCE_CLAIM_CREATIVE_FIELDS: list[str] = [
    "subject_text",
    "axis",
    "state_before",
    "state_after",
    "event_type",
]


class _CoherenceClaimCoercionBase:
    """Shared coercion test helpers for CoherenceClaim fields.

    CoherenceClaim has 5 required fields (claim_id, artifact, source_path,
    claim_text, evidence), so we cannot use ``assert_field_coerces_to_string``
    which only passes the target field.  Instead we build a full kwargs dict.
    """

    @staticmethod
    def _coerce_and_assert(
        field: str,
        input_val: Any,
        expected: str | None,
    ) -> None:
        kwargs = _make_claim(**{field: input_val})
        instance = CoherenceClaim(**kwargs)
        value = getattr(instance, field)
        assert isinstance(value, str), (
            f"Expected {field} to be str, got {type(value).__name__}: {value!r}"
        )
        if expected is not None:
            assert value == expected, f"Expected {field}={expected!r}, got {value!r}"


class TestCoherenceClaimCreativeFieldCoercion(_CoherenceClaimCoercionBase):
    """Verify the 5 creative string fields coerce non-string input to str.

    The ``normalize_optional_text`` validator also covers ``source_field``,
    ``payoff_id``, and ``payoff_kind``, but those are tested separately below.
    """

    @pytest.mark.parametrize("field", COHERENCE_CLAIM_CREATIVE_FIELDS)
    @pytest.mark.parametrize(
        ("input_val", "expected"),
        [
            ("some text", None),
            (None, ""),
            (99, "99"),
            ({"key": "val"}, "key: val"),
            (["a", "b"], "a；b"),
            ("", ""),
            ([], ""),
            ({}, ""),
            (True, "True"),
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
            "bool",
        ],
    )
    def test_field_coerces_to_string(
        self,
        field: str,
        input_val: Any,
        expected: str | None,
    ) -> None:
        self._coerce_and_assert(field, input_val, expected)

    @pytest.mark.parametrize("field", COHERENCE_CLAIM_CREATIVE_FIELDS)
    def test_field_default_is_empty_string(self, field: str) -> None:
        instance = CoherenceClaim(**_COHERENCE_CLAIM_REQUIRED)
        assert getattr(instance, field) == "", (
            f"Expected {field} to default to '', got {getattr(instance, field)!r}"
        )


class TestCoherenceClaimOtherOptionalTextFieldCoercion(_CoherenceClaimCoercionBase):
    """Verify ``source_field``, ``payoff_id``, ``payoff_kind`` also use stringify_text_value.

    These 3 fields are covered by the same ``normalize_optional_text`` validator
    but were not explicitly listed in the task requirements.  They are tested
    here to ensure no regression.
    """

    OTHER_OPTIONAL_TEXT_FIELDS: list[str] = [
        "source_field",
        "payoff_id",
        "payoff_kind",
    ]

    @pytest.mark.parametrize("field", OTHER_OPTIONAL_TEXT_FIELDS)
    @pytest.mark.parametrize(
        ("input_val", "expected"),
        [
            ("text", None),
            (None, ""),
            (42, "42"),
            ({"nested": "val"}, "nested: val"),
            (["x", "y"], "x；y"),
            ("", ""),
            ([], ""),
        ],
        ids=["plain_string", "none", "int", "dict", "list", "empty_string", "empty_list"],
    )
    def test_field_coerces_to_string(
        self,
        field: str,
        input_val: Any,
        expected: str | None,
    ) -> None:
        self._coerce_and_assert(field, input_val, expected)

    @pytest.mark.parametrize("field", OTHER_OPTIONAL_TEXT_FIELDS)
    def test_field_default_is_empty_string(self, field: str) -> None:
        instance = CoherenceClaim(**_COHERENCE_CLAIM_REQUIRED)
        assert getattr(instance, field) == "", (
            f"Expected {field} to default to '', got {getattr(instance, field)!r}"
        )


class TestCoherenceClaimRequiredFieldsUntouched:
    """Verify required string fields (not in normalize_optional_text) are untouched."""

    REQUIRED_FIELDS: list[str] = [
        "claim_id",
        "artifact",
        "source_path",
        "claim_text",
        "evidence",
    ]

    @pytest.mark.parametrize("field", REQUIRED_FIELDS)
    def test_required_field_accepts_string(self, field: str) -> None:
        instance = CoherenceClaim(**_COHERENCE_CLAIM_REQUIRED)
        value = getattr(instance, field)
        assert isinstance(value, str) and value, (
            f"Expected {field} to be non-empty str, got {value!r}"
        )


class TestCoherenceClaimCognitiveChapterAnchors:
    @pytest.mark.parametrize("field", ("cognitive_chapter", "public_reveal_chapter"))
    def test_positive_integer_or_null_is_accepted(self, field: str) -> None:
        claim = CoherenceClaim(**_make_claim(**{field: "12"}))

        assert getattr(claim, field) == 12

    @pytest.mark.parametrize("field", ("cognitive_chapter", "public_reveal_chapter"))
    @pytest.mark.parametrize("value", ("abc", "0", 0, -3, True))
    def test_invalid_anchor_is_rejected(self, field: str, value: Any) -> None:
        with pytest.raises(ValidationError):
            CoherenceClaim(**_make_claim(**{field: value}))

    @pytest.mark.parametrize("field", ("cognitive_chapter", "public_reveal_chapter"))
    @pytest.mark.parametrize("value", ("", None))
    def test_empty_or_null_anchor_is_normalized_to_none(self, field: str, value: Any) -> None:
        claim = CoherenceClaim(**_make_claim(**{field: value}))

        assert getattr(claim, field) is None


class TestCognitiveConstraintChapterAnchors:
    @pytest.mark.parametrize("field", ("cognitive_chapter", "public_reveal_chapter"))
    @pytest.mark.parametrize("value", ("abc", 0, -1, False))
    def test_invalid_anchor_is_rejected(self, field: str, value: Any) -> None:
        with pytest.raises(ValidationError):
            CognitiveConstraint(claim_id="claim_1", **{field: value})


# ── InitCreativeRefinementSuggestion ───────────────────────────


INIT_REFINEMENT_STRING_FIELDS: list[str] = [
    "rationale",
]


class TestInitCreativeRefinementSuggestionRationaleCoercion:
    """Verify ``rationale`` coerces non-string input to str."""

    @pytest.mark.parametrize("field", INIT_REFINEMENT_STRING_FIELDS)
    @pytest.mark.parametrize(
        ("input_val", "expected"),
        [
            ("因为这样更好", None),
            (None, ""),
            (7, "7"),
            ({"原因": "连贯性"}, "原因: 连贯性"),
            (["原因A", "原因B"], "原因A；原因B"),
            ("", ""),
            ([], ""),
            ({}, ""),
            (False, "False"),
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
            "bool",
        ],
    )
    def test_field_coerces_to_string(
        self,
        field: str,
        input_val: Any,
        expected: str | None,
    ) -> None:
        kwargs = dict(_REFINEMENT_REQUIRED)
        kwargs[field] = input_val
        instance = InitCreativeRefinementSuggestion(**kwargs)
        value = getattr(instance, field)
        assert isinstance(value, str), (
            f"Expected {field} to be str, got {type(value).__name__}: {value!r}"
        )
        if expected is not None:
            assert value == expected, f"Expected {field}={expected!r}, got {value!r}"

    @pytest.mark.parametrize("field", INIT_REFINEMENT_STRING_FIELDS)
    def test_field_default_is_empty_string(self, field: str) -> None:
        instance = InitCreativeRefinementSuggestion(**_REFINEMENT_REQUIRED)
        assert getattr(instance, field) == "", (
            f"Expected {field} to default to '', got {getattr(instance, field)!r}"
        )


class TestInitCreativeRefinementSuggestionOtherFieldsUntouched:
    """Verify other optional text fields (artifact, target_path, benefit_type) are also coerced.

    These are covered by the same ``normalize_optional_text_fields`` validator
    as ``rationale`` but aren't the primary target of this task.  Quick check
    ensures no regression.
    """

    @pytest.mark.parametrize(
        ("field", "input_val", "expected"),
        [
            ("artifact", {"nested": "val"}, "nested: val"),
            ("target_path", ["a", "b"], "a；b"),
            ("benefit_type", 42, "42"),
        ],
        ids=["artifact_dict", "target_path_list", "benefit_type_int"],
    )
    def test_field_coerces(
        self,
        field: str,
        input_val: Any,
        expected: str,
    ) -> None:
        kwargs = dict(_REFINEMENT_REQUIRED)
        kwargs[field] = input_val
        instance = InitCreativeRefinementSuggestion(**kwargs)
        assert getattr(instance, field) == expected, (
            f"Expected {field}={expected!r}, got {getattr(instance, field)!r}"
        )
