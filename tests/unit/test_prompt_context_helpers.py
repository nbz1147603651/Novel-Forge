"""Tests for prompt context helpers."""

from __future__ import annotations

from dataclasses import dataclass, field

from novel_forge.core.domain.language import (
    contains_traditional_chinese,
    describe_language,
    normalize_text_for_language,
)
from novel_forge.pipeline.long.services.context.context_helpers import (
    precompute_plan_canon_context,
)
from novel_forge.prompts.context_helpers import (
    format_canon_characters,
    format_events,
    format_list_or_default,
    recommend_character_count,
)


class TestFormatListOrDefault:

    def test_none_returns_default(self):
        assert format_list_or_default(None) == "无"

    def test_empty_list_returns_default(self):
        assert format_list_or_default([]) == "无"

    def test_joins_items(self):
        assert format_list_or_default(["a", "b", "c"]) == "a；b；c"

    def test_custom_sep(self):
        assert format_list_or_default(["x", "y"], sep="、") == "x、y"

    def test_custom_default(self):
        assert format_list_or_default([], default="空") == "空"

    def test_strips_whitespace(self):
        assert format_list_or_default(["  a  ", "b "]) == "a；b"

    def test_filters_empty_strings(self):
        assert format_list_or_default(["a", "", "  ", "b"]) == "a；b"


class TestLanguageHelpers:

    def test_plain_zh_means_simplified_chinese(self):
        assert describe_language("zh") == "简体中文（zh-Hans）"

    def test_explicit_traditional_remains_traditional(self):
        assert describe_language("zh-Hant") == "繁体中文（zh-Hant）"

    def test_normalizes_common_traditional_markers_to_simplified(self):
        assert (
            normalize_text_for_language("陸雲崢與沈念卿會重逢，心底尖叫著", "zh")
            == "陆云峥与沈念卿会重逢，心底尖叫着"
        )

    def test_detects_common_traditional_markers(self):
        assert contains_traditional_chinese("陸雲崢")
        assert not contains_traditional_chinese("陆云峥")


class TestFormatCanonCharacters:

    def test_none_returns_fallback(self):
        assert "暂无" in format_canon_characters(None)

    def test_empty_dict_returns_fallback(self):
        assert "暂无" in format_canon_characters({})

    def test_renders_character(self):
        @dataclass
        class MockChar:
            alive: bool = True
            location: str = "客厅"
            emotional_state: str = "紧张"
            inventory: list = field(default_factory=list)

        result = format_canon_characters({"张三": MockChar()})
        assert "张三" in result
        assert "客厅" in result
        assert "紧张" in result


class TestFormatEvents:

    def test_none_returns_fallback(self):
        assert "暂无" in format_events(None)

    def test_renders_event(self):
        @dataclass
        class MockEvent:
            chapter: int = 3
            description: str = "发现密道"
            characters_involved: list = field(default_factory=lambda: ["李四"])

        result = format_events([MockEvent()])
        assert "第3章" in result
        assert "发现密道" in result
        assert "李四" in result


class TestRecommendCharacterCount:

    def test_none_returns_empty(self):
        assert recommend_character_count(None) == ""

    def test_short_novel(self):
        result = recommend_character_count(50_000)
        assert "3-5" in result

    def test_medium_novel(self):
        result = recommend_character_count(200_000)
        assert "5-8" in result

    def test_long_novel(self):
        result = recommend_character_count(500_000)
        assert "8-12" in result

    def test_epic_novel(self):
        result = recommend_character_count(1_000_000)
        assert "12-15" in result


class TestPrecomputePlanCanonContext:

    def _full_canon(self) -> dict:
        return {
            "characters": {
                "林晚": {"alive": True, "location": "机库", "emotional_state": "警惕"},
                "周临": {"alive": True, "location": "控制室", "emotional_state": "冷静"},
                "赵远": {"alive": True, "location": "总部", "emotional_state": "焦虑"},
            },
            "immutable_facts": ["机库位于城市地下三层"],
            "active_foreshadowing": [{"description": "异常信号"}],
            "recent_events": [{"chapter": 2, "event": "周临提供情报"}],
            "extra_field": "should be dropped",
            "another_extra": {"nested": "data"},
        }

    def test_non_compressible_fields_preserved(self):
        canon = self._full_canon()
        result = precompute_plan_canon_context(canon)
        assert result["immutable_facts"] == ["机库位于城市地下三层"]
        assert result["active_foreshadowing"] == [{"description": "异常信号"}]
        assert result["recent_events"] == [{"chapter": 2, "event": "周临提供情报"}]

    def test_extra_fields_dropped(self):
        canon = self._full_canon()
        result = precompute_plan_canon_context(canon)
        assert "extra_field" not in result
        assert "another_extra" not in result

    def test_characters_filtered_by_involved(self):
        canon = self._full_canon()
        result = precompute_plan_canon_context(canon, involved_characters=["林晚", "周临"])
        assert "林晚" in result["characters"]
        assert "周临" in result["characters"]
        assert "赵远" not in result["characters"]

    def test_all_characters_kept_when_no_involved(self):
        canon = self._full_canon()
        result = precompute_plan_canon_context(canon)
        assert len(result["characters"]) == 3

    def test_all_characters_kept_when_empty_involved(self):
        canon = self._full_canon()
        result = precompute_plan_canon_context(canon, involved_characters=[])
        assert len(result["characters"]) == 3

    def test_none_canon_returns_empty(self):
        assert precompute_plan_canon_context(None) == {}

    def test_non_dict_canon_returns_empty(self):
        assert precompute_plan_canon_context([]) == {}

    def test_empty_canon_returns_empty(self):
        assert precompute_plan_canon_context({}) == {}

    def test_missing_characters_handled(self):
        canon = {"immutable_facts": ["fact1"]}
        result = precompute_plan_canon_context(canon, involved_characters=["林晚"])
        assert result["immutable_facts"] == ["fact1"]
        assert "characters" not in result
