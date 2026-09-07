"""Tests for memory continuity and soft-reference rendering in draft_chapter.j2."""

from __future__ import annotations

from pathlib import Path


def test_memory_sections_split_hard_continuity_from_soft_references() -> None:
    template_path = Path(__file__).parent.parent.parent.parent / "novel_forge" / "prompts" / "prompts" / "writing" / "draft_chapter.j2"
    content = template_path.read_text(encoding="utf-8")

    assert "## 记忆连续性（硬约束）" in content
    assert "## 记忆参考（软约束）" in content


def test_memory_previous_chapter_events_has_mandatory_language() -> None:
    template_path = Path(__file__).parent.parent.parent.parent / "novel_forge" / "prompts" / "prompts" / "writing" / "draft_chapter.j2"
    content = template_path.read_text(encoding="utf-8")

    assert "正文开头必须承接，且不得与以下事件矛盾" in content


def test_memory_relevant_history_has_mandatory_language() -> None:
    template_path = Path(__file__).parent.parent.parent.parent / "novel_forge" / "prompts" / "prompts" / "writing" / "draft_chapter.j2"
    content = template_path.read_text(encoding="utf-8")

    assert "事件背景" in content


def test_memory_summary_context_has_mandatory_language() -> None:
    template_path = Path(__file__).parent.parent.parent.parent / "novel_forge" / "prompts" / "prompts" / "writing" / "draft_chapter.j2"
    content = template_path.read_text(encoding="utf-8")

    assert "以下摘要为前文核心内容，必须在叙事中保持连贯" in content


def test_memory_forbidden_repetition_has_mandatory_language() -> None:
    template_path = Path(__file__).parent.parent.parent.parent / "novel_forge" / "prompts" / "prompts" / "writing" / "draft_chapter.j2"
    content = template_path.read_text(encoding="utf-8")

    assert "以下是近期高频表达机制" in content
    assert "保持连贯" in content


def test_memory_block_conditional_preserved() -> None:
    template_path = Path(__file__).parent.parent.parent.parent / "novel_forge" / "prompts" / "prompts" / "writing" / "draft_chapter.j2"
    content = template_path.read_text(encoding="utf-8")

    assert "has_memory_continuity" in content
    assert "has_memory_soft_refs" in content
    assert "memory.previous_chapter_events" in content
    assert "memory.relevant_history" in content
    assert "memory.summary_context" in content
    assert "memory.motif_suggestions" in content
    assert "memory.soft_forbidden_themes" in content


def test_old_soft_reference_memory_anchor_removed() -> None:
    template_path = Path(__file__).parent.parent.parent.parent / "novel_forge" / "prompts" / "prompts" / "writing" / "draft_chapter.j2"
    content = template_path.read_text(encoding="utf-8")

    assert "## 记忆锚点" not in content
