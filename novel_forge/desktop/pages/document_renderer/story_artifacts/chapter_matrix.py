"""Sub-module of novel_forge.desktop.pages.document_renderer.story_artifacts.

Created in P2d. Contains renderers and helpers for chapter-level design-matrix
displays that previously lived in the parent ``__init__.py``:

- ``render_chapter_design_matrix`` — chapter constraint matrix view
- ``_chapter_design_matrix_item_html`` — single-chapter renderer
- ``_outline_chapter_html`` — outline-style chapter renderer

All three are self-contained — they only depend on ``renderer_html`` helpers
(``_esc`` / ``_nl2br`` / ``_html_wrap`` / ``_make_browser``) and the
``normalize_outline_beat_text`` schema helper. No shared private helpers.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QTextBrowser

from novel_forge.core.schemas.outline import normalize_outline_beat_text
from novel_forge.desktop.pages.standalone.renderer_html import esc as _esc
from novel_forge.desktop.pages.standalone.renderer_html import html_wrap as _html_wrap
from novel_forge.desktop.pages.standalone.renderer_html import make_browser as _make_browser

JsonDict = dict[str, Any]

__all__ = ["render_chapter_design_matrix"]


def render_chapter_design_matrix(data: JsonDict) -> QTextBrowser:
    """Render chapter_design_matrix.json as pre-outline structural constraints."""
    chapters = [item for item in data.get("chapters", []) if isinstance(item, dict)]
    total = data.get("total_chapters") or len(chapters) or "?"
    entity_catalog = data.get("entity_catalog", [])
    entity_count = len(entity_catalog) if isinstance(entity_catalog, list) else 0

    parts: list[str] = [
        '<div style="text-align:center; margin-bottom:12px;">'
        f'<span class="tag-muted tag">结构覆盖 {total} 章</span>'
        f'<span class="tag-muted tag">实体 {entity_count}</span>'
        "</div>",
        "<div class=\"hint-block\">"
        "章节设计矩阵是章节大纲生成前的结构约束：锁定角色 ID，并提供蓝图压力与"
        "情感弧证据。它不替 AI 编写情绪计划，也不是可断点续跑的章节大纲正文；正文仍以 "
        "<code>outline.json</code> 为准。"
        "</div>",
    ]

    for chapter in chapters:
        parts.append(_chapter_design_matrix_item_html(chapter))

    return _make_browser(_html_wrap("\n".join(parts), "章节设计矩阵"))


def _chapter_design_matrix_item_html(chapter: JsonDict) -> str:
    num = chapter.get("chapter_number", "?")
    plot_duties = [
        str(item).strip()
        for item in chapter.get("plot_duties", [])
        if str(item).strip()
    ]
    cast_plan = chapter.get("cast_plan") if isinstance(chapter.get("cast_plan"), dict) else {}
    emotional_brief = (
        chapter.get("emotional_brief")
        if isinstance(chapter.get("emotional_brief"), dict)
        else {}
    )
    pov = cast_plan.get("pov_entity_id") or chapter.get("pov_entity_id") or ""
    required = cast_plan.get("required_character_ids", [])
    required_count = len(required) if isinstance(required, list) else 0
    support = cast_plan.get("support_character_ids", [])
    support_count = len(support) if isinstance(support, list) else 0
    pressure_evidence = [
        str(item).strip()
        for item in emotional_brief.get("pressure_evidence", [])
        if str(item).strip()
    ]
    arc_evidence = [
        str(item).strip()
        for item in emotional_brief.get("arc_evidence", [])
        if str(item).strip()
    ]
    scene_goals = [
        str(item).strip()
        for item in chapter.get("scene_design_goals", [])
        if str(item).strip()
    ]

    meta_parts: list[str] = []
    if pov:
        meta_parts.append(f'<span class="tag">{_esc(pov)}</span>')
    if required_count:
        meta_parts.append(f'<span class="tag-muted tag">必需角色 {required_count}</span>')
    if support_count:
        meta_parts.append(f'<span class="tag-muted tag">支援角色 {support_count}</span>')
    meta = " ".join(meta_parts)

    lines = [
        f'<a name="matrix-chapter-{_esc(num)}"></a>',
        '<div class="section" style="padding:10px 14px;">',
        '<div style="display:flex; align-items:baseline; gap:8px;">',
        f'<b style="color:[[nf:text.heading.deep]]; font-size: 14pt;">第 {num} 章 · 结构约束</b>',
        f"{meta}</div>",
    ]
    if pressure_evidence:
        lines.append(
            f'<div style="color:[[nf:text.body]]; font-size: 12pt; margin-top:4px;">'
            f"压力证据：{_esc('；'.join(pressure_evidence[:2]))}</div>"
        )
    if arc_evidence:
        lines.append(
            f'<div style="color:[[nf:text.secondary]]; font-size: 12pt; margin-top:3px;">'
            f"情感弧证据：{_esc('；'.join(arc_evidence[:2]))}</div>"
        )
    duties = plot_duties or scene_goals
    if duties:
        lines.append('<div style="margin-top:6px; font-size: 12pt; color:[[nf:text.secondary]];">')
        for duty in duties[:4]:
            lines.append(f"<div>· {_esc(duty)}</div>")
        if len(duties) > 4:
            lines.append(f'<div class="tag-muted tag">另有 {len(duties) - 4} 条约束</div>')
        lines.append("</div>")
    lines.append("</div>")
    return "\n".join(lines)


def _outline_chapter_html(chapter: JsonDict) -> str:
    num = chapter.get("chapter_number", "?")
    title = chapter.get("title", "未命名")
    goal = chapter.get("goal", "")
    pov = chapter.get("pov_character", "")
    pov_switch = chapter.get("pov_switch", False)
    setting = chapter.get("setting", "")
    word_count = chapter.get("expected_word_count", 0)
    beats = chapter.get("beats_summary", [])

    meta_parts: list[str] = []
    if pov:
        meta_parts.append(f'<span class="tag">{_esc(pov)}</span>')
    if pov_switch:
        meta_parts.append(
            '<span class="tag" style="background:rgba([[nf:motif.purple]], 0.12); '
            'color:[[nf:motif.purple]]; border-color:rgba([[nf:motif.purple]], 0.3);">&#x21C4; 视角切换</span>'
        )
    if setting:
        meta_parts.append(f'<span class="tag-muted tag">{_esc(setting)}</span>')
    if word_count:
        meta_parts.append(f'<span class="tag-muted tag">{word_count:,}字</span>')
    meta = " ".join(meta_parts)

    lines = [
        f'<a name="chapter-{_esc(num)}"></a>',
        '<div class="section" style="padding:10px 14px;">',
        '<div style="display:flex; align-items:baseline; gap:8px;">',
        f'<b style="color:[[nf:text.heading.deep]]; font-size: 14pt;">第 {num} 章 · {_esc(title)}</b>',
        f"{meta}</div>",
    ]
    if goal:
        lines.append(
            f'<div style="color:[[nf:text.body]]; font-size: 12pt; margin-top:4px;">目标：{_esc(goal)}</div>'
        )
    if beats:
        lines.append('<div style="margin-top:6px; font-size: 12pt; color:[[nf:text.secondary]];">')
        for beat in beats:
            normalized_beat = normalize_outline_beat_text(beat)
            if normalized_beat:
                lines.append(f"<div>· {_esc(normalized_beat)}</div>")
        lines.append("</div>")
    lines.append("</div>")
    return "\n".join(lines)
