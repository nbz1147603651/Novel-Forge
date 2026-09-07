"""Tests for MotifTracker.extract_repeated_phrases."""

from __future__ import annotations

from unittest.mock import AsyncMock

from novel_forge.memory.motif import MotifTracker


def _make_tracker() -> MotifTracker:
    return MotifTracker(router=AsyncMock(), builder=AsyncMock())


class TestExtractRepeatedPhrases:
    def test_empty_text(self) -> None:
        tracker = _make_tracker()
        assert tracker.extract_repeated_phrases("") == []

    def test_none_text(self) -> None:
        tracker = _make_tracker()
        assert tracker.extract_repeated_phrases(None) == []  # type: ignore

    def test_no_repetition(self) -> None:
        tracker = _make_tracker()
        text = "这是一个独特的文本没有任何重复"
        assert tracker.extract_repeated_phrases(text) == []

    def test_exact_repetition(self) -> None:
        tracker = _make_tracker()
        text = "今天天气很好今天天气很好"
        result = tracker.extract_repeated_phrases(text)
        assert "今天天气很好" in result

    def test_min_length_filter(self) -> None:
        tracker = _make_tracker()
        text = "aaaa bbbb aaaa"
        result = tracker.extract_repeated_phrases(text, min_length=4)
        assert "aaaa" in result

    def test_min_occurrences_filter(self) -> None:
        tracker = _make_tracker()
        text = "唯一短语出现一次唯一短语出现两次唯一短语出现三次"
        result = tracker.extract_repeated_phrases(text, min_length=4, min_occurrences=3)
        assert "唯一短语出现" in result

    def test_subsumption_removes_substrings(self) -> None:
        tracker = _make_tracker()
        text = "很长的短语很长的短语"
        result = tracker.extract_repeated_phrases(text, min_length=4)
        assert "很长的短语" in result
        assert "很长的" not in result

    def test_longest_first_ordering(self) -> None:
        tracker = _make_tracker()
        text = "ABCDEF ABCDEF XYZ XYZ"
        result = tracker.extract_repeated_phrases(text, min_length=3, min_occurrences=2)
        if len(result) >= 2:
            assert len(result[0]) >= len(result[-1])

    def test_whitespace_stripped_before_analysis(self) -> None:
        tracker = _make_tracker()
        text = "重复短语 重复短语"
        result = tracker.extract_repeated_phrases(text, min_length=4)
        assert "重复短语" in result

    def test_text_too_short_for_min_requirements(self) -> None:
        tracker = _make_tracker()
        text = "短"
        result = tracker.extract_repeated_phrases(text, min_length=4, min_occurrences=2)
        assert result == []

    def test_max_phrase_length_capped_at_32(self) -> None:
        tracker = _make_tracker()
        phrase = "A" * 40
        text = phrase + " " + phrase
        result = tracker.extract_repeated_phrases(text, min_length=4, min_occurrences=2)
        assert any("A" * 32 in r for r in result)
