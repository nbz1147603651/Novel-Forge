"""Tests for canonical clean_str in novel_forge.core.utils.string.

Covers the union of behaviors from all in-scope private _clean_str copies
found during the pipeline refactor audit (Task 1).
"""

from __future__ import annotations

from novel_forge.core.utils.string import clean_str

# ── Basic behavior (all copies agree) ──────────────────────────────────


class TestCleanStrBasic:
    """Core clean_str behavior shared by all private copies."""

    def test_none_returns_empty(self) -> None:
        assert clean_str(None) == ""

    def test_strips_whitespace(self) -> None:
        assert clean_str("  hello  ") == "hello"

    def test_strips_tabs_and_newlines(self) -> None:
        assert clean_str("\t\nhello\n\t") == "hello"

    def test_empty_string_returns_empty(self) -> None:
        assert clean_str("") == ""

    def test_whitespace_only_returns_empty(self) -> None:
        assert clean_str("   ") == ""

    def test_preserves_internal_whitespace(self) -> None:
        """strip() only removes leading/trailing, not internal spaces."""
        assert clean_str("  hello world  ") == "hello world"

    def test_converts_int_to_string(self) -> None:
        assert clean_str(42) == "42"

    def test_converts_float_to_string(self) -> None:
        assert clean_str(3.14) == "3.14"

    def test_chinese_text_stripped(self) -> None:
        assert clean_str("  你好世界  ") == "你好世界"

    def test_mixed_chinese_english(self) -> None:
        assert clean_str("  hello 世界  ") == "hello 世界"


# ── Falsy-value handling (from draft.py, stage_memory_builder.py, etc.) ─


class TestCleanStrFalsy:
    """Falsy values beyond None should return empty string.

    This matches the `str(value or "").strip()` pattern found in
    pipeline/long/stages/draft.py, stage_memory_diagnostics.py,
    and stage_memory_builder.py.
    """

    def test_false_returns_empty(self) -> None:
        assert clean_str(False) == ""

    def test_zero_returns_empty(self) -> None:
        assert clean_str(0) == ""

    def test_empty_list_returns_empty(self) -> None:
        assert clean_str([]) == ""

    def test_empty_dict_returns_empty(self) -> None:
        assert clean_str({}) == ""

    def test_empty_set_returns_empty(self) -> None:
        assert clean_str(set()) == ""

    def test_truthy_int_preserved(self) -> None:
        assert clean_str(1) == "1"

    def test_truthy_list_stringified(self) -> None:
        """Non-empty list is truthy, so str() is applied."""
        result = clean_str(["a"])
        assert result  # non-empty result


# ── Limit parameter (from continuity_eval/context.py) ──────────────────


class TestCleanStrLimit:
    """Optional limit parameter for truncation with ellipsis.

    Matches the behavior in pipeline/steps/continuity_eval/context.py:85
    which adds `*, limit: int | None = None` with Chinese-aware truncation.
    """

    def test_no_limit_returns_full(self) -> None:
        assert clean_str("hello world") == "hello world"

    def test_limit_none_returns_full(self) -> None:
        assert clean_str("hello world", limit=None) == "hello world"

    def test_under_limit_unchanged(self) -> None:
        assert clean_str("hello", limit=10) == "hello"

    def test_at_limit_unchanged(self) -> None:
        assert clean_str("hello", limit=5) == "hello"

    def test_over_limit_truncated(self) -> None:
        result = clean_str("hello world", limit=8)
        assert len(result) <= 8
        assert result.endswith("…")

    def test_truncation_strips_chinese_punctuation(self) -> None:
        """Truncation point should strip trailing Chinese punctuation."""
        text = "这是第一段。这是第二段。这是第三段"
        result = clean_str(text, limit=8)
        # Should not end with "，" "。" "、" "；" "：" or whitespace
        assert not result.rstrip("…").endswith(("，", "。", "、", "；", "：", " ", "\n"))

    def test_truncation_strips_english_punctuation(self) -> None:
        text = "first sentence. second sentence. third"
        result = clean_str(text, limit=10)
        assert result.endswith("…")
        # The char before "…" should not be a stripped punctuation
        before_ellipsis = result[:-1]  # strip the "…"
        assert not before_ellipsis.endswith((" ", "\n"))

    def test_limit_with_none_value(self) -> None:
        assert clean_str(None, limit=10) == ""

    def test_limit_with_falsy_value(self) -> None:
        assert clean_str([], limit=10) == ""

    def test_limit_one(self) -> None:
        """Edge case: limit=1 should produce just ellipsis or single char."""
        result = clean_str("hello", limit=1)
        assert len(result) <= 1

    def test_limit_chinese_text(self) -> None:
        text = "这是一个很长的中文句子需要被截断"
        result = clean_str(text, limit=8)
        assert len(result) <= 8
        assert result.endswith("…")


# ── Edge cases ─────────────────────────────────────────────────────────


class TestCleanStrEdgeCases:
    """Edge cases from various private copy usage patterns."""

    def test_bool_true(self) -> None:
        assert clean_str(True) == "True"

    def test_nested_whitespace_chinese(self) -> None:
        assert clean_str(" \t 你好 \n ") == "你好"

    def test_string_with_only_chinese_punctuation(self) -> None:
        """Punctuation-only strings are not empty after strip."""
        result = clean_str("，。、")
        assert result == "，。、"

    def test_multiline_text(self) -> None:
        text = "  line1\n  line2\n  line3  "
        result = clean_str(text)
        assert result == "line1\n  line2\n  line3"

    def test_returns_str_type(self) -> None:
        """Return type must always be str."""
        assert isinstance(clean_str(None), str)
        assert isinstance(clean_str(42), str)
        assert isinstance(clean_str("hello"), str)
        assert isinstance(clean_str([]), str)
