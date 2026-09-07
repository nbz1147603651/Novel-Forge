"""Tests for mode="before" field validators on MicroPayoff and ReadingPowerReport.

These validators call stringify_text_value to flatten nested dict/list
structures that LLM outputs sometimes produce for str fields.
"""

from __future__ import annotations

from typing import Any

import pytest

from novel_forge.core.schemas.reading_power import MicroPayoff, MicroPayoffType, ReadingPowerReport


@pytest.mark.parametrize(
    ("description_input", "strength_input", "expected_description", "expected_strength"),
    [
        # plain strings pass through
        ("一个伏笔兑现", "strong", "一个伏笔兑现", "strong"),
        # None → empty
        (None, "medium", "", "medium"),
        # int → str
        (42, "weak", "42", "weak"),
        # dict → flattened
        ({"desc": "秘密揭露", "effect": "推动剧情"}, "medium", "desc: 秘密揭露；effect: 推动剧情", "medium"),
        # list → joined
        (["第一层", "第二层"], "strong", "第一层；第二层", "strong"),
        # empty containers → ""
        ("", "medium", "", "medium"),
        ([], "weak", "", "weak"),
        ({}, "strong", "", "strong"),
    ],
)
def test_micropayoff_field_coercion(
    description_input: Any,
    strength_input: Any,
    expected_description: str,
    expected_strength: str,
) -> None:
    """mode='before' validators on MicroPayoff.description and .strength."""
    mp = MicroPayoff(payoff_type=MicroPayoffType.INFORMATION, description=description_input, strength=strength_input)
    assert mp.description == expected_description
    assert mp.strength == expected_strength


@pytest.mark.parametrize(
    ("field", "input_val", "expected"),
    [
        ("hook_description", "章尾留悬念：敌人逼近城市", "章尾留悬念：敌人逼近城市"),
        ("hook_description", None, ""),
        ("hook_description", 123, "123"),
        ("hook_description", {"text": "敌人逼近"}, "text: 敌人逼近"),
        ("hook_description", ["悬念A", "悬念B"], "悬念A；悬念B"),
        ("hook_description", "", ""),
        ("hook_description", [], ""),
        ("hook_description", {}, ""),
        ("next_chapter_reason", "读者想知道主角如何脱困", "读者想知道主角如何脱困"),
        ("next_chapter_reason", None, ""),
        ("next_chapter_reason", {"reason": "主角被困"}, "reason: 主角被困"),
        ("next_chapter_reason", ["悬念"], "悬念"),
        ("main_plot_advancement_notes", "主线推进：主角获得钥匙", "主线推进：主角获得钥匙"),
        ("main_plot_advancement_notes", None, ""),
        ("main_plot_advancement_notes", {"progress": "获得钥匙"}, "progress: 获得钥匙"),
        ("main_plot_advancement_notes", ["推进A", "推进B"], "推进A；推进B"),
        ("main_plot_advancement_notes", "", ""),
    ],
)
def test_reading_power_report_field_coercion(
    field: str,
    input_val: Any,
    expected: str,
) -> None:
    """mode='before' validators on ReadingPowerReport str fields."""
    kwargs: dict[str, Any] = {
        "chapter": 1,
        field: input_val,
    }
    report = ReadingPowerReport(**kwargs)
    assert getattr(report, field) == expected
