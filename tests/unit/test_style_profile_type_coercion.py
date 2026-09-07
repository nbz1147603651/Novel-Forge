"""Tests for StyleModule mode="before" type coercion (positive_example, negative_example).

Verifies that stringify_text_value is applied before validation so that
non-string LLM outputs (dict, list, int, None) are flattened to strings.
"""

from __future__ import annotations

import pytest

from novel_forge.core.schemas.style_profile import StyleModule


class TestStyleModuleExampleCoercion:
    """Parametrized coverage for positive_example / negative_example type coercion."""

    @pytest.mark.parametrize(
        ("field", "raw", "expected"),
        [
            # ── string input ──────────────────────────────────────────
            ("positive_example", "她落笔三行。", "她落笔三行。"),
            ("negative_example", "絮叨写法。", "絮叨写法。"),
            # ── wrapped string (no-op) ────────────────────────────────
            ("positive_example", "  带空白的文本  ", "带空白的文本"),
            # ── None / empty → "" ─────────────────────────────────────
            ("positive_example", None, ""),
            ("negative_example", None, ""),
            ("positive_example", "", ""),
            ("negative_example", "", ""),
            # ── int → str ────────────────────────────────────────────
            ("positive_example", 42, "42"),
            ("negative_example", 999, "999"),
            # ── float → str ──────────────────────────────────────────
            ("negative_example", 3.14, "3.14"),
            # ── bool → str ───────────────────────────────────────────
            ("positive_example", True, "True"),
            # ── list → flattened str ─────────────────────────────────
            ("positive_example", ["短句", "动作"], "短句；动作"),
            ("negative_example", ["冗余描写", "拖沓节奏"], "冗余描写；拖沓节奏"),
            # ── dict → "key: value" ──────────────────────────────────
            (
                "positive_example",
                {"核心": "短句推进", "效果": "冷峻"},
                "核心: 短句推进；效果: 冷峻",
            ),
            # ── empty list / dict → "" ───────────────────────────────
            ("positive_example", [], ""),
            ("negative_example", {}, ""),
        ],
    )
    def test_example_coercion(self, field: str, raw: object, expected: str) -> None:
        """Verify that the before-validator flattens raw input into expected str."""
        mod = StyleModule(name="测试", rules=["rule1"], **{field: raw})  # type: ignore[arg-type]
        assert getattr(mod, field) == expected

    @pytest.mark.parametrize(
        ("raw_pos", "raw_neg"),
        [
            # mixed: both fields get coerced independently
            (None, "文字"),
            ([], {"k": "v"}),
        ],
    )
    def test_both_fields_independent(self, raw_pos: object, raw_neg: object) -> None:
        """Both fields can be non-string independently without interference."""
        mod = StyleModule(
            name="测试",
            rules=["r1"],
            positive_example=raw_pos,  # type: ignore[arg-type]
            negative_example=raw_neg,  # type: ignore[arg-type]
        )
        assert isinstance(mod.positive_example, str)
        assert isinstance(mod.negative_example, str)
