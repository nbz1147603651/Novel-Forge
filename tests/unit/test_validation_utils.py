"""Tests for enhanced validation utilities."""

from __future__ import annotations

import pytest

from novel_forge.core.utils.validation import (
    ensure_dict,
    ensure_list,
    is_empty,
    is_non_empty,
    is_valid_json,
    safe_coerce_type,
    sanitize_filename,
    validate_api_key_format,
    validate_chapter_number,
    validate_content_length,
    validate_keys,
    validate_max_tokens,
    validate_project_id,
    validate_temperature,
    validate_word_count,
)


class TestValidateKeys:
    """Tests for validate_keys function."""

    @pytest.mark.parametrize(
        "data,required,strict,expected_result,expected_missing,expected_extra",
        [
            # Basic valid case
            ({"a": 1, "b": 2}, ["a"], False, True, [], []),
            # Missing key
            ({"a": 1}, ["a", "b"], False, False, ["b"], []),
            # Empty dict with requirements
            ({}, ["a", "b"], False, False, ["a", "b"], []),
            # Non-dict input
            ("not a dict", ["a"], False, False, ["a"], []),
            # Strict mode: extra keys
            ({"a": 1, "b": 2}, ["a"], True, False, [], ["b"]),
            # Strict mode: exact match
            ({"a": 1}, ["a"], True, True, [], []),
            # Strict mode: missing + extra
            ({"a": 1, "c": 3}, ["a", "b"], True, False, ["b"], ["c"]),
        ],
    )
    def test_validate_keys(self, data, required, strict, expected_result, expected_missing, expected_extra):
        result, missing, extra = validate_keys(data, required, strict=strict)
        assert result is expected_result
        assert set(missing) == set(expected_missing)
        assert set(extra) == set(expected_extra)


class TestValidateProjectId:
    """Tests for validate_project_id function."""

    @pytest.mark.parametrize(
        "project_id,expected_valid,error_contains",
        [
            ("my_project_123", True, ""),
            ("my-project", True, ""),
            ("a" * 255, True, ""),  # max length
            ("", False, "empty"),
            ("   ", False, "empty"),
            ("my project", False, "invalid"),  # space
            ("a" * 256, False, "255"),  # too long
        ],
    )
    def test_project_id(self, project_id, expected_valid, error_contains):
        is_valid, error = validate_project_id(project_id)
        assert is_valid is expected_valid
        if error_contains:
            assert error_contains in error.lower()


class TestValidateChapterNumber:
    """Tests for validate_chapter_number function."""

    @pytest.mark.parametrize(
        "chapter,max_chapters,expected_valid,error_contains",
        [
            (1, 10000, True, ""),
            (50, 100, True, ""),  # custom max
            (0, 10000, False, "at least 1"),
            (-5, 10000, False, "at least 1"),
            ("1", 10000, False, "integer"),
            (10001, 10000, False, "10000"),
        ],
    )
    def test_chapter_number(self, chapter, max_chapters, expected_valid, error_contains):
        is_valid, error = validate_chapter_number(chapter, max_chapters=max_chapters)
        assert is_valid is expected_valid
        if error_contains:
            assert error_contains in error


class TestValidateWordCount:
    """Tests for validate_word_count function."""

    @pytest.mark.parametrize(
        "count,min_words,max_words,expected_valid,error_contains",
        [
            (3000, 0, 1000000, True, ""),
            (0, 0, 1000000, True, ""),  # zero is valid
            (100, 500, 1000000, False, "500"),
            (2000000, 0, 1000000, False, "1000000"),
            ("1000", 0, 1000000, False, "integer"),
        ],
    )
    def test_word_count(self, count, min_words, max_words, expected_valid, error_contains):
        is_valid, error = validate_word_count(count, min_words=min_words, max_words=max_words)
        assert is_valid is expected_valid
        if error_contains:
            assert error_contains in error


class TestValidateTemperature:
    """Tests for validate_temperature function."""

    @pytest.mark.parametrize(
        "temp,expected_valid",
        [
            (0.7, True),
            (0.0, True),
            (2.0, True),
            (1, True),  # integer
            (-0.1, False),
            (2.5, False),
        ],
    )
    def test_temperature(self, temp, expected_valid):
        is_valid, error = validate_temperature(temp)
        assert is_valid is expected_valid
        if not expected_valid:
            assert "0.0" in error and "2.0" in error


class TestValidateMaxTokens:
    """Tests for validate_max_tokens function."""

    @pytest.mark.parametrize(
        "tokens,expected_valid,error_contains",
        [
            (2048, True, ""),
            (1, True, ""),  # minimum
            (0, False, "at least 1"),
            (200001, False, "200000"),
            (1024.5, False, "integer"),
        ],
    )
    def test_max_tokens(self, tokens, expected_valid, error_contains):
        is_valid, error = validate_max_tokens(tokens)
        assert is_valid is expected_valid
        if error_contains:
            assert error_contains in error


class TestSanitizeFilename:
    """Tests for sanitize_filename function."""

    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("my_file.txt", "my_file.txt"),
            ("", "unnamed_file"),
            ("   ", "unnamed_file"),
        ],
    )
    def test_filename_basic(self, filename, expected):
        assert sanitize_filename(filename) == expected

    def test_filename_path_traversal(self):
        result = sanitize_filename("../etc/passwd")
        assert ".." not in result
        assert "/" not in result

    def test_filename_invalid_chars(self):
        result = sanitize_filename("file<>:|?*.txt")
        for ch in "<>:|?*":
            assert ch not in result

    def test_filename_too_long(self):
        result = sanitize_filename("a" * 300 + ".txt")
        assert len(result) <= 255

    def test_filename_leading_dots(self):
        assert not sanitize_filename("...hidden").startswith(".")


class TestValidateContentLength:
    """Tests for validate_content_length function."""

    @pytest.mark.parametrize(
        "content,max_chars,expected_valid,error_contains",
        [
            ("Hello, World!", 1000, True, ""),
            (None, 1000, True, ""),
            ("a" * 1001, 1000, False, "1000"),
            (123, 1000, False, "string"),
        ],
    )
    def test_content_length(self, content, max_chars, expected_valid, error_contains):
        is_valid, error = validate_content_length(content, max_chars=max_chars)
        assert is_valid is expected_valid
        if error_contains:
            assert error_contains in error.lower()


class TestValidateApiKeyFormat:
    """Tests for validate_api_key_format function."""

    @pytest.mark.parametrize(
        "key,provider,expected_valid,error_contains",
        [
            ("sk-1234567890abcdefghijklmnop", "openai", True, ""),
            ("invalid_key", "openai", False, "format"),
            ("", "openai", False, "empty"),
            ("any_key_here", "unknown_provider", False, "unknown provider"),
        ],
    )
    def test_api_key_format(self, key, provider, expected_valid, error_contains):
        is_valid, error = validate_api_key_format(key, provider)
        assert is_valid is expected_valid
        if error_contains:
            assert error_contains in error.lower()


class TestIsValidJson:
    """Tests for is_valid_json function."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ('{"a": 1}', True),
            ('[1, 2, 3]', True),
            ('{}', True),
            ('invalid', False),
            ('{"a": }', False),
            ('', False),
        ],
    )
    def test_is_valid_json(self, text, expected):
        assert is_valid_json(text) is expected


class TestSafeCoerceType:
    """Tests for safe_coerce_type function."""

    @pytest.mark.parametrize(
        "value,target_type,default,expected",
        [
            ("123", int, None, 123),
            ("1.5", float, None, 1.5),
            ("true", bool, None, True),
            ("yes", bool, None, True),
            ("1", bool, None, True),
            ("false", bool, None, False),
            ("no", bool, None, False),
            ("0", bool, None, False),
            ("invalid", int, 0, 0),
            (None, int, 99, 99),
        ],
    )
    def test_safe_coerce_type(self, value, target_type, default, expected):
        result = safe_coerce_type(value, target_type, default=default)
        assert result == expected


class TestEnsureHelpers:
    """Tests for ensure_* helper functions."""

    @pytest.mark.parametrize(
        "input_val,expected",
        [
            ([1, 2, 3], [1, 2, 3]),
            ("hello", ["hello"]),
            (None, []),
        ],
    )
    def test_ensure_list(self, input_val, expected):
        assert ensure_list(input_val) == expected

    @pytest.mark.parametrize(
        "input_val,expected",
        [
            ({"a": 1}, {"a": 1}),
            (None, {}),
        ],
    )
    def test_ensure_dict(self, input_val, expected):
        assert ensure_dict(input_val) == expected


class TestIsNonEmpty:
    """Tests for is_non_empty and is_empty functions."""

    @pytest.mark.parametrize(
        "value,expected_non_empty",
        [
            ("hello", True),
            ("", False),
            (None, False),
            ([1, 2], True),
            ([], False),
        ],
    )
    def test_is_non_empty(self, value, expected_non_empty):
        assert is_non_empty(value) is expected_non_empty
        assert is_empty(value) is not expected_non_empty
