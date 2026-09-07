"""Tests for motif category governance and prompt projection policy."""

from __future__ import annotations

from novel_forge.memory.motif_governance import (
    allows_forward_prompt_guidance,
    allows_hard_repetition_forbid,
    allows_prompt_callback,
    allows_unified_strengthen,
    default_role_for_category,
    is_conceptual_category,
    is_prompt_soft_only_category,
    normalize_motif_category,
    should_auto_retire_category,
)


def test_normalize_motif_category_falls_back_to_imagery() -> None:
    assert normalize_motif_category("未知类别") == "意象"
    assert normalize_motif_category("") == "意象"
    assert normalize_motif_category("主题") == "主题"


def test_default_role_for_category() -> None:
    assert default_role_for_category("主题") == "theme_anchor"
    assert default_role_for_category("符号") == "core_symbol"
    assert default_role_for_category("声音") == "recurring_image"
    assert default_role_for_category("技法") == "one_off_rhetoric"


def test_conceptual_categories_are_soft_only_in_prompts() -> None:
    for category in ("主题", "符号"):
        assert is_conceptual_category(category) is True
        assert is_prompt_soft_only_category(category) is True
        assert allows_hard_repetition_forbid(category) is False
        assert allows_prompt_callback(category) is False
        assert allows_forward_prompt_guidance(category) is False
        assert allows_unified_strengthen(category) is False
        assert should_auto_retire_category(category) is False


def test_concrete_expression_categories_can_receive_prompt_governance() -> None:
    for category in ("意象", "动作", "感官", "颜色", "声音", "技法"):
        assert is_conceptual_category(category) is False
        assert is_prompt_soft_only_category(category) is False
        assert allows_hard_repetition_forbid(category) is True
        assert allows_prompt_callback(category) is True
        assert allows_forward_prompt_guidance(category) is True
        assert allows_unified_strengthen(category) is True
        assert should_auto_retire_category(category) is True
