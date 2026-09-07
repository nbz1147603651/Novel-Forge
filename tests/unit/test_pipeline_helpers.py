"""Tests for pipeline helper utilities."""

from __future__ import annotations

from novel_forge.core.utils.pipeline_helpers import (
    format_repair_actions,
    has_prompt_leaks,
    join_repair_actions,
    normalize_threshold,
    safe_bool,
    safe_get,
    save_and_emit,
    validate_threshold,
)


class TestValidateThreshold:
    """Tests for validate_threshold function."""

    def test_value_meets_threshold(self):
        """Test when value meets threshold."""
        assert validate_threshold(8.0, 7.0) is True
        assert validate_threshold(7.0, 7.0) is True

    def test_value_below_threshold(self):
        """Test when value is below threshold."""
        assert validate_threshold(6.0, 7.0) is False
        assert validate_threshold(5.5, 7.0) is False

    def test_value_clamped_to_min(self):
        """Test that value is clamped to minimum."""
        # Value -1 should be clamped to 0, which is below threshold 7
        assert validate_threshold(-1.0, 7.0) is False

    def test_value_clamped_to_max(self):
        """Test that value is clamped to maximum."""
        # Value 15 should be clamped to 10, which meets threshold 7
        assert validate_threshold(15.0, 7.0) is True

    def test_custom_range(self):
        """Test with custom min/max range."""
        assert validate_threshold(50.0, 40.0, min_val=0, max_val=100) is True
        assert validate_threshold(30.0, 40.0, min_val=0, max_val=100) is False


class TestNormalizeThreshold:
    """Tests for normalize_threshold function."""

    def test_float_value(self):
        """Test with float value."""
        assert normalize_threshold(7.5) == 7.5
        assert normalize_threshold(0.0) == 0.0
        assert normalize_threshold(10.0) == 10.0

    def test_int_value(self):
        """Test with int value."""
        assert normalize_threshold(7) == 7.0
        assert normalize_threshold(0) == 0.0
        assert normalize_threshold(10) == 10.0

    def test_string_value(self):
        """Test with string value."""
        assert normalize_threshold("7.5") == 7.5
        assert normalize_threshold("8") == 8.0

    def test_invalid_value_uses_default(self):
        """Test that invalid values use default."""
        assert normalize_threshold(None) == 7.0
        assert normalize_threshold("invalid") == 7.0
        assert normalize_threshold([]) == 7.0
        assert normalize_threshold({}) == 7.0

    def test_custom_default(self):
        """Test with custom default value."""
        assert normalize_threshold(None, default=5.0) == 5.0
        assert normalize_threshold("invalid", default=5.0) == 5.0

    def test_clamped_to_range(self):
        """Test that result is clamped to valid range."""
        assert normalize_threshold(-5.0) == 0.0
        assert normalize_threshold(15.0) == 10.0
        assert normalize_threshold(100.0) == 10.0


class TestHasPromptLeaks:
    """Tests for has_prompt_leaks function."""

    def test_none_report(self):
        """Test with None report."""
        assert has_prompt_leaks(None) is False

    def test_no_leaks(self):
        """Test with report that has no leaks."""
        class FakeReport:
            prompt_leaks = []
        assert has_prompt_leaks(FakeReport()) is False

    def test_has_leaks(self):
        """Test with report that has leaks."""
        class FakeReport:
            prompt_leaks = ["leak1", "leak2"]
        assert has_prompt_leaks(FakeReport()) is True

    def test_empty_string_leaks(self):
        """Test with empty string leaks (should be falsy)."""
        class FakeReport:
            prompt_leaks = [""]
        # bool([""]) is True in Python, but our function checks bool(leaks)
        assert has_prompt_leaks(FakeReport()) is True


class TestFormatRepairActions:
    """Tests for format_repair_actions function."""

    def test_string_actions(self):
        """Test with string actions."""
        actions = ["action1", "action2", "action3"]
        result = format_repair_actions(actions)
        assert result == ["action1", "action2", "action3"]

    def test_limit_applied(self):
        """Test that limit is applied."""
        actions = ["action1", "action2", "action3", "action4", "action5"]
        result = format_repair_actions(actions, limit=3)
        assert len(result) == 3
        assert result == ["action1", "action2", "action3"]

    def test_object_actions(self):
        """Test with object actions."""
        class Action:
            def __init__(self, value):
                self.value = value
            def __str__(self):
                return self.value

        actions = [Action("a1"), Action("a2"), Action("a3")]
        result = format_repair_actions(actions)
        assert result == ["a1", "a2", "a3"]

    def test_empty_strings_filtered(self):
        """Test that empty strings are filtered."""
        actions = ["action1", "", "  ", "action2"]
        result = format_repair_actions(actions)
        assert "" not in result
        assert "action1" in result
        assert "action2" in result


class TestJoinRepairActions:
    """Tests for join_repair_actions function."""

    def test_string_actions(self):
        """Test with string actions."""
        actions = ["action1", "action2", "action3"]
        result = join_repair_actions(actions)
        assert result == "action1；action2；action3"

    def test_custom_separator(self):
        """Test with custom separator."""
        actions = ["action1", "action2"]
        result = join_repair_actions(actions, separator=", ")
        assert result == "action1, action2"

    def test_limit_applied(self):
        """Test that limit is applied."""
        actions = ["action1", "action2", "action3", "action4"]
        result = join_repair_actions(actions, limit=2)
        assert result == "action1；action2"

    def test_empty_list(self):
        """Test with empty list."""
        result = join_repair_actions([])
        assert result == ""


class TestSafeGet:
    """Tests for safe_get function."""

    def test_dict_access(self):
        """Test dictionary access."""
        obj = {"key": "value", "other": "data"}
        assert safe_get(obj, "key") == "value"
        assert safe_get(obj, "missing", "default") == "default"

    def test_object_access(self):
        """Test object attribute access."""
        class Obj:
            def __init__(self):
                self.key = "value"
                self.other = "data"

        obj = Obj()
        assert safe_get(obj, "key") == "value"
        assert safe_get(obj, "missing", "default") == "default"

    def test_none_default(self):
        """Test with None default."""
        assert safe_get({}, "missing") is None
        assert safe_get({}, "missing", None) is None


class TestSafeBool:
    """Tests for safe_bool function."""

    def test_dict_bool_access(self):
        """Test dictionary boolean access."""
        obj = {"flag": True, "other": False}
        assert safe_bool(obj, "flag") is True
        assert safe_bool(obj, "other") is False

    def test_object_bool_access(self):
        """Test object boolean attribute access."""
        class Obj:
            def __init__(self):
                self.flag = True
                self.other = False

        obj = Obj()
        assert safe_bool(obj, "flag") is True
        assert safe_bool(obj, "other") is False

    def test_default_value(self):
        """Test with default value."""
        assert safe_bool({}, "missing") is False
        assert safe_bool({}, "missing", True) is True

    def test_truthy_conversion(self):
        """Test truthy value conversion."""
        assert safe_bool({"val": [1, 2]}, "val") is True
        assert safe_bool({"val": ""}, "val") is False
        assert safe_bool({"val": 0}, "val") is False
        assert safe_bool({"val": 1}, "val") is True


class TestSaveAndEmit:
    """Tests for save_and_emit function."""

    def test_save_and_emit(self):
        """Test save_and_emit function."""
        # Mock objects
        class MockStorage:
            def __init__(self):
                self.saved = {}

            def save_json(self, path, data):
                self.saved[path] = data

        class MockData:
            def model_dump(self, mode):
                return {"key": "value"}

        storage = MockStorage()
        on_step_calls = []

        def on_step(event_name, data):
            on_step_calls.append((event_name, data))

        # Call function
        save_and_emit(storage, "test/path", MockData(), "test_event", on_step)

        # Verify
        assert "test/path" in storage.saved
        assert storage.saved["test/path"] == {"key": "value"}
        assert len(on_step_calls) == 1
        assert on_step_calls[0][0] == "test_event"