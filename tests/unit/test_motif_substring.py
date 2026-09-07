"""Tests for substring-aware proper noun exemption in forbidden element detection."""

from __future__ import annotations

from novel_forge.pipeline.steps.continuity_eval.validators import (
    _detect_forbidden_elements,
    _is_substring_of_known_proper_noun,
)


class TestIsSubstringOfKnownProperNoun:
    """Unit tests for _is_substring_of_known_proper_noun."""

    def test_empty_matched_text_returns_false(self) -> None:
        anchors = frozenset({"月光宝盒", "镇北王爷"})
        assert _is_substring_of_known_proper_noun("", anchors) is False

    def test_empty_anchor_set_returns_false(self) -> None:
        assert _is_substring_of_known_proper_noun("月光", frozenset()) is False

    def test_exact_match_is_not_substring(self) -> None:
        """Exact match (same length) should NOT be considered a substring."""
        anchors = frozenset({"月光"})
        assert _is_substring_of_known_proper_noun("月光", anchors) is False

    def test_matched_text_is_substring_of_anchor(self) -> None:
        """'月光' is a substring of '月光宝盒' — should exempt."""
        anchors = frozenset({"月光宝盒"})
        assert _is_substring_of_known_proper_noun("月光", anchors) is True

    def test_anchor_is_substring_of_matched_text(self) -> None:
        """'王爷' is contained in '镇北王爷' — should exempt."""
        anchors = frozenset({"王爷"})
        assert _is_substring_of_known_proper_noun("镇北王爷", anchors) is True

    def test_no_substring_relation_returns_false(self) -> None:
        anchors = frozenset({"张三", "李四"})
        assert _is_substring_of_known_proper_noun("月光", anchors) is False

    def test_single_char_matched_text_with_longer_anchor(self) -> None:
        """'月' is a substring of '月光宝盒' — should exempt even for single char."""
        anchors = frozenset({"月", "月光宝盒"})
        assert _is_substring_of_known_proper_noun("月", anchors) is True

    def test_multiple_anchors_one_matches(self) -> None:
        anchors = frozenset({"张三", "月光宝盒", "李四"})
        assert _is_substring_of_known_proper_noun("月光", anchors) is True

    def test_case_sensitive_match(self) -> None:
        """Chinese text has no case, but verify exact char matching."""
        anchors = frozenset({"月光宝盒"})
        assert _is_substring_of_known_proper_noun("曰光", anchors) is False


class TestDetectForbiddenElementsSubstringExemption:
    """Integration tests for _detect_forbidden_elements with substring filtering."""

    def test_standalone_forbidden_element_still_detected(self) -> None:
        """Standalone '月光' without any proper noun context should be detected."""
        text = "月光洒在窗台上，冷冷清清。"
        forbidden = ["月光"]
        result = _detect_forbidden_elements(text, forbidden)
        assert len(result) == 1
        assert result[0] == ("月光", "月光")

    def test_diagnostic_forbidden_note_matches_only_source_term(self) -> None:
        text = "他把纸页重新压回袖中，没再抬头。"
        forbidden = ["避免重复使用「袖中」（近8章平均9.0次/章）"]
        result = _detect_forbidden_elements(text, forbidden)
        assert result == [("袖中", "袖中")]

    def test_substring_of_proper_noun_exempted(self) -> None:
        """'月光' within '月光宝盒' should NOT be flagged."""
        text = "他取出了月光宝盒，轻轻打开。"
        forbidden = ["月光"]
        extra_known = ["月光宝盒"]
        result = _detect_forbidden_elements(
            text, forbidden, extra_known_terms=extra_known,
        )
        assert len(result) == 0

    def test_containment_proper_noun_exempted(self) -> None:
        """'王爷' contained in '镇北王爷' should NOT be flagged."""
        text = "镇北王爷驾到，众人跪拜。"
        forbidden = ["王爷"]
        extra_known = ["王爷"]
        result = _detect_forbidden_elements(
            text, forbidden, extra_known_terms=extra_known,
        )
        assert len(result) == 0

    def test_substring_exemption_applies_to_all_occurrences(self) -> None:
        """When '月光宝盒' is a known proper noun, all '月光' matches are exempted."""
        text = "月光洒落。他取出月光宝盒，借着月光查看。"
        forbidden = ["月光"]
        extra_known = ["月光宝盒"]
        result = _detect_forbidden_elements(
            text, forbidden, extra_known_terms=extra_known,
        )
        # Since "月光" is a substring of the known proper noun "月光宝盒",
        # all occurrences of "月光" are exempted.
        assert len(result) == 0

    def test_empty_text_returns_empty(self) -> None:
        result = _detect_forbidden_elements("", ["月光"])
        assert result == []

    def test_empty_forbidden_returns_empty(self) -> None:
        result = _detect_forbidden_elements("月光洒落", [])
        assert result == []

    def test_whitespace_punctuation_noise_still_exempted(self) -> None:
        """'月光' within '月光 宝盒' (with whitespace) should still be exempted."""
        text = "他取出了月光宝盒。"
        forbidden = ["月光"]
        extra_known = ["月光宝盒"]
        result = _detect_forbidden_elements(
            text, forbidden, extra_known_terms=extra_known,
        )
        assert len(result) == 0

    def test_title_case_equivalent_chinese(self) -> None:
        """Chinese has no title case, but verify exact char matching works."""
        text = "冷白灯光在墙面滑过去。"
        forbidden = ["冷白灯光"]
        extra_known = ["冷白灯光"]
        result = _detect_forbidden_elements(
            text, forbidden, extra_known_terms=extra_known,
        )
        # "冷白灯光" is an exact match in anchor terms, so it's a proper noun
        assert len(result) == 0
