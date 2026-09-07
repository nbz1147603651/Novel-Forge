"""Sub-module of novel_forge.desktop.pages.document_renderer.story_artifacts.

Auto-generated in the M3.3 split. Contains outline.py renderers.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QTextBrowser

from novel_forge.core.schemas.outline import normalize_outline_beat_text
from novel_forge.desktop.pages.standalone.renderer_html import esc as _esc
from novel_forge.desktop.pages.standalone.renderer_html import html_wrap as _html_wrap
from novel_forge.desktop.pages.standalone.renderer_html import make_browser as _make_browser
from novel_forge.desktop.pages.standalone.renderer_html import nl2br as _nl2br

JsonDict = dict[str, Any]

__all__ = ["render_outline"]


def render_outline(data: JsonDict) -> QTextBrowser:
    """Render outline.json as a nicely formatted chapter directory."""
    total = data.get("total_chapters", "?")
    synopsis = data.get("synopsis", "")
    chapters = data.get("chapters", [])
    volume_mode = data.get("volume_mode", False)
    volumes = data.get("volumes", [])

    parts: list[str] = [
        f'<div style="text-align:center; margin-bottom:12px;">'
        f'<span class="tag-muted tag">共 {total} 章</span></div>'
    ]

    if synopsis:
        parts.append(f'<div class="hint-block">{_nl2br(synopsis)}</div>')

    if volume_mode and volumes:
        vol_map: dict[int, JsonDict] = {}
        for volume in volumes:
            for cn in range(
                volume.get("start_chapter", 1),
                volume.get("end_chapter", 1) + 1,
            ):
                vol_map[cn] = volume

        current_vol: JsonDict | None = None
        for chapter in chapters:
            vol = vol_map.get(chapter.get("chapter_number", 0))
            if vol and vol is not current_vol:
                current_vol = vol
                vn = vol.get("volume_number", "?")
                vt = vol.get("title", "")
                arc = vol.get("arc_goal", "")
                parts.append(f"<h2>第 {vn} 卷 · {_esc(vt)}</h2>")
                if arc:
                    parts.append(
                        f'<div style="color:[[nf:text.secondary]]; font-size: 12pt; '
                        f'margin-bottom:8px;">卷目标：{_esc(arc)}</div>'
                    )
            parts.append(_outline_chapter_html(chapter))  # noqa: F821
    else:
        for chapter in chapters:
            parts.append(_outline_chapter_html(chapter))  # noqa: F821

    return _make_browser(_html_wrap("\n".join(parts), "全书大纲"))


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
