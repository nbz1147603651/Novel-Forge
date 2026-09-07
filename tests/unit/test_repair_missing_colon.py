"""Tests for repair_missing_colon_delimiters."""

from __future__ import annotations

import json

from novel_forge.core.response_repair.json_blocks import repair_missing_colon_delimiters


class TestRepairMissingColonDelimiters:
    """Test cases for missing colon delimiter repair."""

    def test_basic_string_value(self) -> None:
        """Missing colon between key and string value."""
        text = '{"name" "张三"}'
        result = repair_missing_colon_delimiters(text)
        assert json.loads(result) == {"name": "张三"}

    def test_basic_numeric_value(self) -> None:
        """Missing colon between key and numeric value."""
        text = '{"age" 25}'
        result = repair_missing_colon_delimiters(text)
        assert json.loads(result) == {"age": 25}

    def test_basic_object_value(self) -> None:
        """Missing colon between key and object value."""
        text = '{"outer" {"inner": "val"}}'
        result = repair_missing_colon_delimiters(text)
        assert json.loads(result) == {"outer": {"inner": "val"}}

    def test_basic_array_value(self) -> None:
        """Missing colon between key and array value."""
        text = '{"items" [1, 2, 3]}'
        result = repair_missing_colon_delimiters(text)
        assert json.loads(result) == {"items": [1, 2, 3]}

    def test_boolean_values(self) -> None:
        """Missing colon with boolean values."""
        text = '{"flag" true, "other" false}'
        result = repair_missing_colon_delimiters(text)
        assert json.loads(result) == {"flag": True, "other": False}

    def test_null_value(self) -> None:
        """Missing colon with null value."""
        text = '{"data" null}'
        result = repair_missing_colon_delimiters(text)
        assert json.loads(result) == {"data": None}

    def test_multiple_missing_colons(self) -> None:
        """Multiple keys missing colons."""
        text = '{"a" "b", "c" "d"}'
        result = repair_missing_colon_delimiters(text)
        assert json.loads(result) == {"a": "b", "c": "d"}

    def test_mixed_colons(self) -> None:
        """Some keys have colons, some don't."""
        text = '{"a": "b", "c" "d", "e": "f"}'
        result = repair_missing_colon_delimiters(text)
        assert json.loads(result) == {"a": "b", "c": "d", "e": "f"}

    def test_nested_object_missing_colon(self) -> None:
        """Nested object with missing colon."""
        text = '{"outer" {"inner" "val"}}'
        result = repair_missing_colon_delimiters(text)
        assert json.loads(result) == {"outer": {"inner": "val"}}

    def test_chinese_content(self) -> None:
        """Chinese content in values should not be corrupted."""
        text = '{"角色" "沈鹿溪", "地点" "有风小筑"}'
        result = repair_missing_colon_delimiters(text)
        assert json.loads(result) == {"角色": "沈鹿溪", "地点": "有风小筑"}

    def test_valid_json_unchanged(self) -> None:
        """Valid JSON should be returned unchanged."""
        text = '{"name": "张三", "age": 25}'
        result = repair_missing_colon_delimiters(text)
        assert result == text

    def test_string_value_not_corrupted(self) -> None:
        """String values containing key-like patterns should not be corrupted."""
        text = '{"note": "key" "value" should not break"}'
        # This should not crash - the function should handle it gracefully
        # The result may or may not be valid JSON, but it shouldn't corrupt
        # the string content
        result = repair_missing_colon_delimiters(text)
        # The function must never alter content inside a quoted value string.
        # Regression: previously a spurious colon was inserted inside the value
        # when the state machine lost track of string boundaries.
        assert "should not break" in result

    def test_bare_key_missing_open_quote_does_not_corrupt_value(self) -> None:
        """A key missing its opening quote must not corrupt the value string.

        Regression for the ``编辑契约校验阻断`` incident: when an LLM emits
        ``{element_id":"horror_dread_rhythm"}`` (the key's opening quote is
        dropped), the old state machine misread the value's opening quote as a
        key close and then inserted a ``:`` before the first ``t``/``f``/``n``/
        digit/quote inside the value -- corrupting ``horror_dread_rhythm`` to
        ``horror_dread_rhy:thm``. The repair must leave such malformed input
        unchanged so a safer downstream strategy can handle it.
        """
        text = '{"editorial_element_directives":[{"element_id":"pacing_breath_window"},{element_id":"horror_dread_rhythm"}]}'
        result = repair_missing_colon_delimiters(text)
        # The value must remain intact.
        assert "horror_dread_rhythm" in result
        assert "rhy:thm" not in result
        # No value-internal colon should ever be introduced.
        for corrupt in ("rhy:thm", "i:nformation", "rela:tion", "edi:torial"):
            assert corrupt not in result

    def test_bare_key_missing_open_quote_multiple_values(self) -> None:
        """Multiple bare-key members in the same object must not corrupt values.

        Mirrors the real MiniMax response where every ``element_id`` key after
        the first was emitted without its opening quote.
        """
        text = (
            '{"items":['
            '{element_id":"horror_dread_rhythm"},'
            '{element_id":"information_asymmetry"},'
            '{element_id":"relationship_trust_arc"},'
            '{element_id":"editorial_revelation_ladder"}'
            "]}"
        )
        result = repair_missing_colon_delimiters(text)
        assert "horror_dread_rhythm" in result
        assert "information_asymmetry" in result
        assert "relationship_trust_arc" in result
        assert "editorial_revelation_ladder" in result
        # None of the values should be split by an inserted colon.
        for corrupt in (
            "rhy:thm",
            "i:nformation",
            "rela:tion",
            "edi:torial",
        ):
            assert corrupt not in result

    def test_empty_string(self) -> None:
        """Empty string should return unchanged."""
        text = ""
        result = repair_missing_colon_delimiters(text)
        assert result == text

    def test_no_quotes(self) -> None:
        """Text without quotes should return unchanged."""
        text = "{no quotes here}"
        result = repair_missing_colon_delimiters(text)
        assert result == text

    def test_negative_number(self) -> None:
        """Missing colon with negative number."""
        text = '{"temp" -5}'
        result = repair_missing_colon_delimiters(text)
        assert json.loads(result) == {"temp": -5}

    def test_whitespace_before_value(self) -> None:
        """Whitespace between key and value should be preserved."""
        text = '{"key"   "value"}'
        result = repair_missing_colon_delimiters(text)
        assert json.loads(result) == {"key": "value"}

    def test_newline_before_value(self) -> None:
        """Newline between key and value."""
        text = '{"key"\n"value"}'
        result = repair_missing_colon_delimiters(text)
        assert json.loads(result) == {"key": "value"}

    def test_array_items_unchanged(self) -> None:
        """Array items should not be affected (no colons needed)."""
        text = '["a", "b", "c"]'
        result = repair_missing_colon_delimiters(text)
        assert result == text
        assert json.loads(result) == ["a", "b", "c"]

    def test_complex_nested_structure(self) -> None:
        """Complex nested structure with missing colons."""
        text = '{"users" [{"name" "张三"}, {"name": "李四"}]}'
        result = repair_missing_colon_delimiters(text)
        parsed = json.loads(result)
        assert parsed == {"users": [{"name": "张三"}, {"name": "李四"}]}

    def test_escaped_quotes_in_string(self) -> None:
        """Escaped quotes inside strings should not confuse the parser."""
        text = '{"note": "He said \\"hello\\"", "other" "value"}'
        result = repair_missing_colon_delimiters(text)
        parsed = json.loads(result)
        assert parsed["note"] == 'He said "hello"'
        assert parsed["other"] == "value"
