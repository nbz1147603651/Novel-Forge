"""Tests for draft prompt context compaction."""

from __future__ import annotations

from novel_forge.pipeline.long.services.context.stage_memory_builder import (
    _compact_memory_layered_context,
    _compact_memory_prompt_context,
)


def test_compact_memory_prompt_context_keeps_only_due_foreshadows() -> None:
    raw = {
        "summary_context": "这是摘要上下文",
        "motif_context": {
            "forbidden_repetition": ["雨声", "铁锈味", "冷光"],
            "active_motifs": [{"name": "不应保留"}],
            "suggested_callbacks": [{"motif": "不应保留"}],
        },
        "foreshadow_due": [
            {
                "entry_id": "promise_1",
                "description": "怀表停摆的原因待回收",
                "planted_chapter": 2,
                "promise_type": "mystery",
            }
        ],
    }

    compacted = _compact_memory_prompt_context(raw)

    assert compacted == {
        "foreshadow_due": [
            {
                "entry_id": "promise_1",
                "description": "怀表停摆的原因待回收",
                "planted_chapter": 2,
                "promise_type": "mystery",
            }
        ]
    }


def test_compact_memory_prompt_context_drops_empty_motif_context() -> None:
    raw = {
        "summary_context": "只保留摘要",
        "motif_context": {
            "forbidden_repetition": [],
            "active_motifs": [{"name": "不会保留"}],
        },
    }

    compacted = _compact_memory_prompt_context(raw)

    assert compacted == {}


def test_compact_memory_layered_context_keeps_expected_layers() -> None:
    raw = {
        "L0_identity": "标题：测试书 | 题材：科幻",
        "L1_core_memory": "最近一章中，主角发现异常信号。",
        "L2_on_demand": "## 近期事件\n- 第4章：发现异常信号",
        "L3_deep_search": "## 相关历史\n- 第2章：[相关度0.78] 信号首次出现",
    }

    compacted = _compact_memory_layered_context(raw, enabled_layers=("L1_core_memory",))

    assert compacted == {"L1_core_memory": "最近一章中，主角发现异常信号。"}
