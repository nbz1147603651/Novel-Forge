"""Tests for structured issue prompt rendering helpers."""

from __future__ import annotations

from types import SimpleNamespace

from novel_forge.core.utils.issue_prompt_blocks import build_issue_prompt_block


def test_build_issue_prompt_block_includes_id_and_structured_fields() -> None:
    block = build_issue_prompt_block(
        {
            "issue_id": "ch003-causal-abcdef123456",
            "issue_type": "event_without_cause",
            "severity": "high",
            "location": "中段",
            "paragraph_start": 4,
            "paragraph_end": 5,
            "summary": "关键行动缺少前因",
            "evidence": "角色突然决定离开",
            "fix_suggestion": "补足决策前的压力和信息来源",
            "fix_actions": ["补一句触发条件"],
            "postconditions": [{"description": "读者能看出行动由前文触发"}],
        }
    )

    assert "[Issue ch003-causal-abcdef123456]" in block
    assert "类型: event_without_cause" in block
    assert "位置: 中段（第4-5段）" in block
    assert "建议修复: 补足决策前的压力和信息来源" in block
    assert "验收条件: 读者能看出行动由前文触发" in block


def test_build_issue_prompt_block_tolerates_non_numeric_paragraph_fields() -> None:
    block = build_issue_prompt_block(
        SimpleNamespace(
            issue_id="continuity-issue",
            issue_type="timeline_conflict",
            severity="medium",
            paragraph_start="not-a-number",
            paragraph_end="also-bad",
            summary="时间线描述冲突",
        )
    )

    assert "[Issue continuity-issue]" in block
    assert "问题: 时间线描述冲突" in block
