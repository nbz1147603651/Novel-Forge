"""Tests for Issue Ledger feedback loop: unresolved issues → banned_phrases."""

from __future__ import annotations

from novel_forge.pipeline.long.stages.continuity_repair import _extract_pattern_from_issue


class FakeIssue:
    def __init__(self, summary: str, issue_type: str = "continuity_error") -> None:
        self.summary = summary
        self.issue_type = issue_type


class TestExtractPatternFromIssue:
    def test_quoted_phrase_extracted(self) -> None:
        issue = FakeIssue('发现重复表达"深吸一口气"，建议替换')
        result = _extract_pattern_from_issue(issue)
        assert result == "深吸一口气"

    def test_chinese_quotes(self) -> None:
        issue = FakeIssue('发现重复表达"目光坚定"，建议替换')
        result = _extract_pattern_from_issue(issue)
        assert result == "目光坚定"

    def test_single_quotes_fallback(self) -> None:
        issue = FakeIssue("发现重复表达'陈词滥调'，建议替换")
        result = _extract_pattern_from_issue(issue)
        assert result is not None
        assert len(result) >= 2

    def test_fallback_with_keyword(self) -> None:
        issue = FakeIssue("重复句式模板：他冷冷地看着她，连续出现三次")
        result = _extract_pattern_from_issue(issue)
        assert result is not None
        assert len(result) >= 2

    def test_no_keyword_no_fallback(self) -> None:
        issue = FakeIssue("这是一个普通的连续性问题")
        result = _extract_pattern_from_issue(issue)
        assert result is None

    def test_empty_summary(self) -> None:
        issue = FakeIssue("")
        result = _extract_pattern_from_issue(issue)
        assert result is None

    def test_none_summary(self) -> None:
        issue = FakeIssue(None)  # type: ignore
        result = _extract_pattern_from_issue(issue)
        assert result is None

    def test_short_quoted_phrase_fallback(self) -> None:
        issue = FakeIssue('重复"a"，建议替换')
        result = _extract_pattern_from_issue(issue)
        assert result is not None
        assert len(result) >= 2


class TestIssueLedgerBannedPhrasesIntegration:
    def test_unresolved_repetition_issue_adds_banned_phrase(self) -> None:
        class FakePacket:
            def __init__(self) -> None:
                self.banned_phrases: list[str] = []

        packet = FakePacket()
        issue = FakeIssue('发现重复表达"深吸一口气"，建议替换')

        phrase = _extract_pattern_from_issue(issue)
        if phrase and phrase not in packet.banned_phrases:
            packet.banned_phrases.append(phrase)

        assert "深吸一口气" in packet.banned_phrases

    def test_duplicate_phrase_not_added_twice(self) -> None:
        class FakePacket:
            def __init__(self) -> None:
                self.banned_phrases: list[str] = ["深吸一口气"]

        packet = FakePacket()
        issue = FakeIssue('发现重复表达"深吸一口气"，建议替换')

        phrase = _extract_pattern_from_issue(issue)
        if phrase and phrase not in packet.banned_phrases:
            packet.banned_phrases.append(phrase)

        assert len(packet.banned_phrases) == 1

    def test_non_repetition_issue_not_extracted(self) -> None:
        issue = FakeIssue("时间线矛盾：第3章和第5章事件冲突")
        result = _extract_pattern_from_issue(issue)
        assert result is None
