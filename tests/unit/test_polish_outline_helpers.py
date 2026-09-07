"""Unit tests for polish outline helper functions."""

from __future__ import annotations

import pytest

from novel_forge.core.schemas.outline import StoryOutline

# ── _normalize_polish_suggestions ─────────────────────────────────


class TestNormalizePolishSuggestions:
    """Tests for _normalize_polish_suggestions(value)."""

    def test_normalize_list_input(self) -> None:
        """List input should be returned as-is (deduplicated)."""
        from novel_forge.pipeline.steps.polish_outline_step import (  # type: ignore[import-untyped]
            _normalize_polish_suggestions,
        )

        result = _normalize_polish_suggestions(["建议1", "建议2", "建议3"])
        assert result == ["建议1", "建议2", "建议3"]

    def test_normalize_semicolon_string(self) -> None:
        """Semicolon-separated string should be split and cleaned."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _normalize_polish_suggestions,
        )

        result = _normalize_polish_suggestions("建议1；建议2；建议3")
        assert result == ["建议1", "建议2", "建议3"]

    def test_normalize_removes_duplicates(self) -> None:
        """Duplicate entries should be removed, preserving order."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _normalize_polish_suggestions,
        )

        result = _normalize_polish_suggestions(["A", "A", "B"])
        assert result == ["A", "B"]

    def test_normalize_truncates_at_8(self) -> None:
        """More than 8 entries should be truncated to 8."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _normalize_polish_suggestions,
        )

        items = [str(i) for i in range(12)]
        result = _normalize_polish_suggestions(items)
        assert len(result) <= 8

    def test_normalize_empty_input_none(self) -> None:
        """None input should return empty list."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _normalize_polish_suggestions,
        )

        result = _normalize_polish_suggestions(None)
        assert result == []

    def test_normalize_empty_input_list(self) -> None:
        """Empty list input should return empty list."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _normalize_polish_suggestions,
        )

        result = _normalize_polish_suggestions([])
        assert result == []


# ── _normalize_focus_fields ───────────────────────────────────────


class TestNormalizeFocusFields:
    """Tests for _normalize_focus_fields(value)."""

    def test_focus_list_input(self) -> None:
        """List of field names should be returned as-is."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _normalize_focus_fields,
        )

        result = _normalize_focus_fields(["synopsis", "key_scenes"])
        assert result == ["goal", "main_plot_points"]

    def test_focus_comma_string(self) -> None:
        """Comma-separated string should be split."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _normalize_focus_fields,
        )

        result = _normalize_focus_fields("synopsis,key_scenes")
        assert result == ["goal", "main_plot_points"]

    def test_focus_dict_input(self) -> None:
        """Dict with boolean values should filter only True keys."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _normalize_focus_fields,
        )

        result = _normalize_focus_fields({"synopsis": True, "key_scenes": False})
        assert result == ["goal"]

    def test_focus_empty_none(self) -> None:
        """None should return empty list."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _normalize_focus_fields,
        )

        result = _normalize_focus_fields(None)
        assert result == []


# ── _parse_chapter_range ──────────────────────────────────────────


class TestParseChapterRange:
    """Tests for _parse_chapter_range(value, total_chapters)."""

    def test_range_dash_format(self) -> None:
        """'3-7' should return [3, 4, 5, 6, 7]."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _parse_chapter_range,
        )

        result = _parse_chapter_range("3-7", total_chapters=20)
        assert result == [3, 4, 5, 6, 7]

    def test_range_comma_format(self) -> None:
        """'1,5,10' should return [1, 5, 10]."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _parse_chapter_range,
        )

        result = _parse_chapter_range("1,5,10", total_chapters=20)
        assert result == [1, 5, 10]

    def test_range_single_number(self) -> None:
        """'7' should return [7]."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _parse_chapter_range,
        )

        result = _parse_chapter_range("7", total_chapters=20)
        assert result == [7]

    def test_range_chinese_format(self) -> None:
        """'第3至5章、第8章' should support Chinese range/list syntax."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _parse_chapter_range,
        )

        result = _parse_chapter_range("第3至5章、第8章", total_chapters=20)
        assert result == [3, 4, 5, 8]

    def test_range_none_or_empty(self) -> None:
        """None or '' should return None (meaning all chapters)."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _parse_chapter_range,
        )

        result_none = _parse_chapter_range(None, total_chapters=20)
        result_empty = _parse_chapter_range("", total_chapters=20)
        assert result_none is None
        assert result_empty is None

    def test_range_out_of_bounds(self) -> None:
        """Range exceeding total chapters should raise ValueError."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _parse_chapter_range,
        )

        with pytest.raises(ValueError):
            _parse_chapter_range("25-30", total_chapters=20)


# ── _filter_chapters_by_range ──────────────────────────────────────


@pytest.fixture
def sample_outline() -> StoryOutline:
    """Construct a 5-chapter StoryOutline for testing."""
    from novel_forge.core.schemas.outline import ChapterOutline

    chapters = [
        ChapterOutline(
            chapter_number=i,
            title=f"第{i}章",
            goal=f"第{i}章目标",
            involved_characters=["角色A"],
        )
        for i in range(1, 6)
    ]
    return StoryOutline(total_chapters=5, chapters=chapters)


class TestFilterChaptersByRange:
    """Tests for _filter_chapters_by_range(outline, chapter_range)."""

    def test_filter_range_returns_subset(self, sample_outline: StoryOutline) -> None:
        """Range (2, 4) should return chapters 2, 3, 4."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _filter_chapters_by_range,
        )

        result = _filter_chapters_by_range(sample_outline, (2, 4))
        assert len(result) == 3
        assert [ch.chapter_number for ch in result] == [2, 3, 4]

    def test_filter_none_returns_all(self, sample_outline: StoryOutline) -> None:
        """None range should return all chapters."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _filter_chapters_by_range,
        )

        result = _filter_chapters_by_range(sample_outline, None)
        assert len(result) == 5

    def test_filter_single_chapter(self, sample_outline: StoryOutline) -> None:
        """Range (3, 3) should return only chapter 3."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _filter_chapters_by_range,
        )

        result = _filter_chapters_by_range(sample_outline, (3, 3))
        assert len(result) == 1
        assert result[0].chapter_number == 3

    def test_filter_out_of_bounds_raises(self, sample_outline: StoryOutline) -> None:
        """Range beyond total chapters should raise ValueError."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _filter_chapters_by_range,
        )

        with pytest.raises(ValueError):
            _filter_chapters_by_range(sample_outline, (6, 8))


# ── _merge_adjusted_chapters ──────────────────────────────────────


class TestMergeAdjustedChapters:
    """Tests for _merge_adjusted_chapters(original, adjusted_data)."""

    def test_merge_only_updates_specified_chapters(self, sample_outline: StoryOutline) -> None:
        """Only chapters present in adjusted_data should change."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _merge_adjusted_chapters,
        )

        adjusted_data = [
            {"chapter_number": 2, "goal": "新的第二章目标"},
            {"chapter_number": 4, "goal": "新的第四章目标"},
        ]
        result = _merge_adjusted_chapters(sample_outline, adjusted_data)
        # Check chapters 2 and 4 changed
        assert result.chapters[1].goal == "新的第二章目标"
        assert result.chapters[3].goal == "新的第四章目标"
        # Check chapters 1, 3, 5 unchanged
        assert result.chapters[0].goal == "第1章目标"
        assert result.chapters[2].goal == "第3章目标"
        assert result.chapters[4].goal == "第5章目标"

    def test_merge_preserves_unmodified_fields(self, sample_outline: StoryOutline) -> None:
        """Only specified fields in adjusted chapters should change."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _merge_adjusted_chapters,
        )

        # Only update goal, keep other fields
        adjusted_data = [
            {
                "chapter_number": 2,
                "goal": "新目标",
                "involved_characters": ["角色B"],  # should be ignored - key_scenes not present
            },
        ]
        result = _merge_adjusted_chapters(sample_outline, adjusted_data)
        assert result.chapters[1].goal == "新目标"
        # involved_characters WAS in the adjusted data, so it should be updated
        assert result.chapters[1].involved_characters == ["角色B"]

    def test_merge_empty_adjustments_no_changes(self, sample_outline: StoryOutline) -> None:
        """Empty adjusted_data should leave outline unchanged."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _merge_adjusted_chapters,
        )

        result = _merge_adjusted_chapters(sample_outline, [])
        # Compare serialized forms
        import json

        assert json.dumps(result.model_dump(mode="json"), sort_keys=True) == json.dumps(
            sample_outline.model_dump(mode="json"), sort_keys=True
        )

    def test_merge_validates_output_schema(self, sample_outline: StoryOutline) -> None:
        """Merged result should pass Pydantic validation."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _merge_adjusted_chapters,
        )

        adjusted_data = [{"chapter_number": 1, "goal": "更新目标"}]
        result = _merge_adjusted_chapters(sample_outline, adjusted_data)
        # Should validate without errors
        validated = StoryOutline.model_validate(result.model_dump(mode="json"))
        assert validated.total_chapters == 5

    def test_merge_maps_legacy_field_aliases_without_leaking_extra_attrs(
        self, sample_outline: StoryOutline
    ) -> None:
        """Legacy prompt/UI aliases should map to real ChapterOutline fields."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _merge_adjusted_chapters,
        )

        adjusted_data = [
            {
                "chapter_number": 1,
                "synopsis": "新的第一章概要",
                "key_scenes": ["场景A"],
                "unknown_field": "不应进入模型",
            },
        ]
        result = _merge_adjusted_chapters(sample_outline, adjusted_data)

        assert result.chapters[0].goal == "新的第一章概要"
        assert result.chapters[0].main_plot_points == ["场景A"]
        assert not hasattr(result.chapters[0], "synopsis")
        assert not hasattr(result.chapters[0], "unknown_field")

    def test_merge_allows_title_updates(self, sample_outline: StoryOutline) -> None:
        """Title-focused polish should be able to change only chapter titles."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _merge_adjusted_chapters,
        )

        result = _merge_adjusted_chapters(
            sample_outline,
            [{"chapter_number": 2, "title": "暗潮初起"}],
        )

        assert result.chapters[1].title == "暗潮初起"
        assert result.chapters[1].goal == "第2章目标"

    def test_changed_chapters_between_reports_actual_changes(
        self, sample_outline: StoryOutline
    ) -> None:
        """Ignored/no-op adjustment rows should not be reported as changed."""
        from novel_forge.pipeline.steps.polish_outline_step import (
            _changed_chapters_between,
            _merge_adjusted_chapters,
        )

        merged = _merge_adjusted_chapters(
            sample_outline,
            [
                {"chapter_number": 1, "unknown_field": "忽略"},
                {"chapter_number": 3, "title": "第三章新标题"},
            ],
        )

        assert _changed_chapters_between(sample_outline, merged) == [3]
