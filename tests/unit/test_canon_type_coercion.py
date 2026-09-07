"""Tests for ``mode="before"`` field validators on CreativeReport and NewCharacterDetail.

These validators call ``stringify_text_value`` to flatten nested dict/list
structures that LLM outputs sometimes produce for string-typed creative fields.
"""

from __future__ import annotations

from typing import Any

import pytest

from novel_forge.core.schemas.canon import CreativeReport, NewCharacterDetail, PlotDeviation
from novel_forge.core.schemas.chapter import ChapterOutcome
from novel_forge.core.schemas.story_state import ChapterExitState
from tests.unit.test_schema_type_coercion_helpers import assert_field_coerces_to_string

# ── CreativeReport ────────────────────────────────────────────


CREATIVE_REPORT_STRING_FIELDS: list[str] = [
    "structured_summary",
]


class TestCreativeReportStringFieldCoercion:
    """Verify ``structured_summary`` coerces non-string input to str."""

    @pytest.mark.parametrize("field", CREATIVE_REPORT_STRING_FIELDS)
    @pytest.mark.parametrize(
        ("input_val", "expected"),
        [
            # plain string — passthrough
            ("故事中主角发现了一个秘密", None),
            # None → ""
            (None, ""),
            # int → str
            (42, "42"),
            # dict → flattened
            ({"主线": "主角的成长", "副线": "爱情的萌芽"}, "主线: 主角的成长；副线: 爱情的萌芽"),
            # list → joined
            (["主角的觉醒", "反派的阴谋"], "主角的觉醒；反派的阴谋"),
            # empty container → ""
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
        assert_field_coerces_to_string(CreativeReport, field, input_val, expected)

    @pytest.mark.parametrize("field", CREATIVE_REPORT_STRING_FIELDS)
    def test_field_default_is_empty_string(self, field: str) -> None:
        instance = CreativeReport()
        assert getattr(instance, field) == "", (
            f"Expected {field} to default to '', got {getattr(instance, field)!r}"
        )


# ── NewCharacterDetail ────────────────────────────────────────


DESCRIPTION_NON_STRING_INPUTS: list[tuple[str, Any, str]] = [
    ("plain_string", "一个神秘的角色", "一个神秘的角色"),
    ("none", None, ""),
    ("int", 42, "42"),
    ("dict", {"外貌": "高大的身影"}, "外貌: 高大的身影"),
    ("list_of_str", ["神秘", "强大"], "神秘；强大"),
    ("list_of_dict", [{"性格": 1}, {"背景": 2}], "性格: 1；背景: 2"),
    ("empty_string", "", ""),
    ("empty_list", [], ""),
    ("empty_dict", {}, ""),
]


class TestNewCharacterDetailDescriptionCoercion:
    """Verify ``description`` coerces non-string input to str."""

    @pytest.mark.parametrize(
        ("label", "input_val", "expected"),
        DESCRIPTION_NON_STRING_INPUTS,
        ids=[e[0] for e in DESCRIPTION_NON_STRING_INPUTS],
    )
    def test_field_coerces_to_string(
        self,
        label: str,  # noqa: ARG002
        input_val: Any,
        expected: str,
    ) -> None:
        instance = NewCharacterDetail(
            name="Foo",
            first_appearance_chapter=1,
            description=input_val,
        )
        assert instance.description == expected, (
            f"Expected description={expected!r}, got {instance.description!r}"
        )
        assert isinstance(instance.description, str)

    def test_default_is_empty_string(self) -> None:
        instance = NewCharacterDetail(name="Foo", first_appearance_chapter=1)
        assert instance.description == ""


RELATIONSHIP_INPUTS: list[tuple[str, Any, dict[str, str]]] = [
    (
        "dict_str_str",
        {"Alice": "friend", "Bob": "rival"},
        {"Alice": "friend", "Bob": "rival"},
    ),
    (
        "dict_str_list",
        {"Alice": ["friend", "ally"], "Bob": "rival"},
        {"Alice": "friend；ally", "Bob": "rival"},
    ),
    (
        "dict_str_int",
        {"Alice": 42, "Bob": "rival"},
        {"Alice": "42", "Bob": "rival"},
    ),
    (
        "dict_str_dict",
        {"Alice": {"role": "friend"}, "Bob": "rival"},
        {"Alice": "role: friend", "Bob": "rival"},
    ),
    (
        "empty_dict",
        {},
        {},
    ),
    (
        "none_input",
        None,
        {},
    ),
    (
        "string_input",
        "some_string",
        {},
    ),
    (
        "list_input",
        ["Alice", "Bob"],
        {},
    ),
]


class TestNewCharacterDetailRelationshipsCoercion:
    """Verify ``relationship_to_existing`` coerces values to string."""

    @pytest.mark.parametrize(
        ("label", "input_val", "expected"),
        RELATIONSHIP_INPUTS,
        ids=[e[0] for e in RELATIONSHIP_INPUTS],
    )
    def test_relationship_coercion(
        self,
        label: str,  # noqa: ARG002
        input_val: Any,
        expected: dict[str, str],
    ) -> None:
        instance = NewCharacterDetail(
            name="Foo",
            first_appearance_chapter=1,
            relationship_to_existing=input_val,
        )
        assert instance.relationship_to_existing == expected, (
            f"Expected {expected!r}, got {instance.relationship_to_existing!r}"
        )

    def test_default_is_empty_dict(self) -> None:
        instance = NewCharacterDetail(name="Foo", first_appearance_chapter=1)
        assert instance.relationship_to_existing == {}


# ── Non-target fields untouched ───────────────────────────────


class TestCreativeReportNonTargetFields:
    """Verify fields without validators are not affected."""

    def test_creative_highlights_preserves_list(self) -> None:
        highlights = ["精彩描写", "角色成长"]
        report = CreativeReport(creative_highlights=highlights)
        assert report.creative_highlights == highlights

    def test_suggestions_preserves_string(self) -> None:
        report = CreativeReport(suggestions_for_next_chapter="继续推进主线")
        assert report.suggestions_for_next_chapter == "继续推进主线"


class TestNewCharacterDetailNonTargetFields:
    """Verify non-target fields on NewCharacterDetail are unaffected."""

    def test_name_preserves_string(self) -> None:
        instance = NewCharacterDetail(name="Alice", first_appearance_chapter=1)
        assert instance.name == "Alice"

    def test_confidence_preserves_float(self) -> None:
        instance = NewCharacterDetail(
            name="Alice",
            first_appearance_chapter=1,
            confidence=0.85,
        )
        assert instance.confidence == 0.85

    def test_should_add_to_bible_preserves_bool(self) -> None:
        instance = NewCharacterDetail(
            name="Alice",
            first_appearance_chapter=1,
            should_add_to_bible=True,
        )
        assert instance.should_add_to_bible is True

    def test_evidence_preserves_list(self) -> None:
        evidence = ["found in chapter 3", "mentioned in bridge"]
        instance = NewCharacterDetail(
            name="Alice",
            first_appearance_chapter=1,
            evidence=evidence,
        )
        assert instance.evidence == evidence


# ── ChapterOutcome ──────────────────────────────────────────────


CANON_DELTA_STRING_FIELDS: list[str] = [
    "chapter_summary",
]


class TestChapterOutcomeStringFieldCoercion:
    """Verify ``chapter_summary`` coerces non-string input to str."""

    @pytest.mark.parametrize("field", CANON_DELTA_STRING_FIELDS)
    @pytest.mark.parametrize(
        ("input_val", "expected"),
        [
            ("故事中主角发现了一个秘密", None),
            (None, ""),
            (42, "42"),
            ({"主线": "主角的成长"}, "主线: 主角的成长"),
            (["主角的觉醒", "反派的阴谋"], "主角的觉醒；反派的阴谋"),
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
        instance = ChapterOutcome(source_chapter=1, **{field: input_val})
        value = getattr(instance, field)
        assert isinstance(value, str), (
            f"Expected {field} to be str, got {type(value).__name__}: {value!r}"
        )
        if expected is not None:
            assert value == expected
        else:
            from novel_forge.core.utils.type_coerce import stringify_text_value

            assert value == stringify_text_value(input_val)

    @pytest.mark.parametrize("field", CANON_DELTA_STRING_FIELDS)
    def test_field_default_is_empty_string(self, field: str) -> None:
        instance = ChapterOutcome(source_chapter=1)
        assert getattr(instance, field) == "", (
            f"Expected {field} to default to '', got {getattr(instance, field)!r}"
        )


# ── ChapterOutcome ──────────────────────────────────────────


EXTRACTED_CANON_STRING_FIELDS: list[str] = [
    "structured_summary",
]


class TestExtractedCanonStringFieldCoercion:
    """Verify ``structured_summary`` coerces non-string input to str."""

    @pytest.mark.parametrize("field", EXTRACTED_CANON_STRING_FIELDS)
    @pytest.mark.parametrize(
        ("input_val", "expected"),
        [
            ("故事摘要内容", None),
            (None, ""),
            (99, "99"),
            ({"key": "val"}, "key: val"),
            (["a", "b"], "a；b"),
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
        instance = ChapterOutcome(
            source_chapter=1,
            chapter_exit_state=ChapterExitState(chapter_number=1),
            **{field: input_val},
        )
        value = getattr(instance, field)
        assert isinstance(value, str), (
            f"Expected {field} to be str, got {type(value).__name__}: {value!r}"
        )
        if expected is not None:
            assert value == expected
        else:
            from novel_forge.core.utils.type_coerce import stringify_text_value

            assert value == stringify_text_value(input_val)

    @pytest.mark.parametrize("field", EXTRACTED_CANON_STRING_FIELDS)
    def test_field_default_is_empty_string(self, field: str) -> None:
        instance = ChapterOutcome(
            source_chapter=1,
            chapter_exit_state=ChapterExitState(chapter_number=1),
        )
        assert getattr(instance, field) == "", (
            f"Expected {field} to default to '', got {getattr(instance, field)!r}"
        )


# ── PlotDeviation ──────────────────────────────────────────


class TestPlotDeviationActualPlotCoercion:
    """Verify ``actual_plot`` coerces non-string input to str."""

    @pytest.mark.parametrize(
        ("input_val", "expected"),
        [
            ("故事主线", None),
            (None, ""),
            (7, "7"),
            ({"情节": "转折"}, "情节: 转折"),
            (["发展", "高潮"], "发展；高潮"),
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
    def test_actual_plot_coerces_to_string(
        self,
        input_val: Any,
        expected: str | None,
    ) -> None:
        instance = PlotDeviation(
            outline_plan="planned outline",
            actual_plot=input_val,
        )
        assert isinstance(instance.actual_plot, str), (
            f"Expected actual_plot to be str, got {type(instance.actual_plot).__name__}"
        )
        if expected is not None:
            assert instance.actual_plot == expected
        else:
            from novel_forge.core.utils.type_coerce import stringify_text_value

            assert instance.actual_plot == stringify_text_value(input_val)


class TestPlotDeviationReasonCoercion:
    """Verify ``reason`` coerces non-string input to str."""

    @pytest.mark.parametrize(
        ("input_val", "expected"),
        [
            ("角色动机变化", None),
            (None, ""),
            (3, "3"),
            ({"原因": "角色成长"}, "原因: 角色成长"),
            (["偏离大纲"], "偏离大纲"),
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
    def test_reason_coerces_to_string(
        self,
        input_val: Any,
        expected: str | None,
    ) -> None:
        instance = PlotDeviation(
            outline_plan="planned outline",
            actual_plot="actual direction",
            reason=input_val,
        )
        assert isinstance(instance.reason, str), (
            f"Expected reason to be str, got {type(instance.reason).__name__}"
        )
        if expected is not None:
            assert instance.reason == expected
        else:
            from novel_forge.core.utils.type_coerce import stringify_text_value

            assert instance.reason == stringify_text_value(input_val)

    def test_reason_default_is_empty_string(self) -> None:
        instance = PlotDeviation(
            outline_plan="planned outline",
            actual_plot="actual direction",
        )
        assert instance.reason == "", (
            f"Expected reason to default to '', got {instance.reason!r}"
        )


class TestPlotDeviationNonTargetFields:
    """Verify fields without validators are not affected."""

    def test_outline_plan_preserves_string(self) -> None:
        instance = PlotDeviation(outline_plan="大纲计划", actual_plot="实际情节")
        assert instance.outline_plan == "大纲计划"

    def test_deviation_level_preserves_string(self) -> None:
        instance = PlotDeviation(
            outline_plan="p", actual_plot="a", deviation_level="major"
        )
        assert instance.deviation_level == "major"

    def test_impact_on_future_preserves_string(self) -> None:
        instance = PlotDeviation(
            outline_plan="p", actual_plot="a", impact_on_future="影响后续章节"
        )
        assert instance.impact_on_future == "影响后续章节"
