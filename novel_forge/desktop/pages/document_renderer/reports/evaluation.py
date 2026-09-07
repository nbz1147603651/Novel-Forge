"""Sub-module of novel_forge.desktop.pages.document_renderer.reports.

Auto-generated in the M3.4 split. Contains evaluation.py renderers.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QTextBrowser

from novel_forge.desktop.pages.document_renderer.reports._common import (
    _BIBLE_FIELD_LABELS,
    _GENRE_LABELS,
    _TONE_LABELS,
    _eval_dim_label,
    _safe_float,
)
from novel_forge.desktop.pages.standalone.renderer_html import esc as _esc
from novel_forge.desktop.pages.standalone.renderer_html import html_wrap as _html_wrap
from novel_forge.desktop.pages.standalone.renderer_html import make_browser as _make_browser
from novel_forge.desktop.pages.standalone.renderer_html import nl2br as _nl2br
from novel_forge.desktop.pages.standalone.renderer_html import score_bar_html as _score_bar_html

__all__ = [
    "render_story_bible",
    "render_spec",
    "render_eval_report_body_html",
    "render_eval_report",
]


def _safe_int(value: Any) -> int:
    """Best-effort integer conversion for renderer counters."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        try:
            return int(float(text))
        except ValueError:
            return 0
    return 0


def render_story_bible(data: dict[str, Any]) -> QTextBrowser:
    """Render story_bible.json as a richly formatted HTML view."""
    title = data.get("title", "世界观设定")
    parts: list[str] = []

    for key, label in _BIBLE_FIELD_LABELS.items():
        value = data.get(key, "")
        if value:
            parts.append(
                f'<div class="section"><h3>{_esc(label)}</h3>'
                f'<div class="kv-value">{_nl2br(value)}</div></div>'
            )

    rules = data.get("rules", [])
    if rules:
        parts.append("<h2>世界规则</h2>")
        for rule in rules:
            parts.append(f'<div class="rule-item">{_nl2br(rule)}</div>')

    themes = data.get("themes", [])
    if themes:
        parts.append("<h2>核心主题</h2>")
        for theme in themes:
            parts.append(f'<div class="theme-item">{_nl2br(theme)}</div>')

    return _make_browser(_html_wrap("\n".join(parts), title))


def render_spec(data: dict[str, Any]) -> QTextBrowser:
    """Render spec.json as a richly formatted HTML view."""
    title = data.get("title", "故事规格")
    genre = data.get("genre", "")
    tone = data.get("tone", "")
    length = data.get("length_target", 0)
    lang = data.get("language", "zh")

    parts: list[str] = []
    badges: list[str] = []
    if genre:
        badges.append(f'<span class="tag">{_esc(_GENRE_LABELS.get(genre, genre) or genre)}</span>')
    if tone:
        badges.append(f'<span class="tag">{_esc(_TONE_LABELS.get(tone, tone) or tone)}</span>')
    if length:
        badges.append(f'<span class="tag-muted tag">{length:,} 字</span>')
    if lang:
        badges.append(
            f'<span class="tag-muted tag">{_esc({"zh": "中文", "en": "English"}.get(lang, lang) or lang)}</span>'
        )
    if badges:
        parts.append(f'<div style="margin: 8px 0 12px 0;">{"".join(badges)}</div>')

    theme = data.get("theme", "")
    if theme:
        parts.append(
            f'<div class="section"><h3>主题概述</h3>'
            f'<div class="kv-value">{_nl2br(theme)}</div></div>'
        )

    for key, label in (
        ("characters_hint", "角色提示"),
        ("world_hint", "世界观提示"),
        ("conflict_hint", "冲突提示"),
        ("pov_hint", "视角提示"),
        ("opening_style", "开篇风格"),
        ("ending_style", "收束风格"),
        ("extra_instructions", "附加指令"),
    ):
        value = data.get(key, "")
        if value:
            parts.append(f'<h3>{_esc(label)}</h3><div class="hint-block">{_nl2br(value)}</div>')

    return _make_browser(_html_wrap("\n".join(parts), title))


def render_eval_report_body_html(data: dict[str, Any]) -> str:
    """Render eval report JSON as a formatted HTML body."""
    overall = _safe_float(data.get("overall_score"))
    passed = data.get("passed")
    threshold = _safe_float(data.get("threshold"))
    if threshold is None:
        threshold = 6.0
    summary = data.get("summary", "")
    scores = data.get("scores", [])

    parts: list[str] = []
    if overall is not None:
        mark_color = "[[nf:status.success.warm]]" if passed else "[[nf:status.danger.deep]]"
        mark_text = "✓ 通过" if passed else "✗ 未通过"
        parts.append(
            f'<div class="section" style="text-align:center; padding:20px;">'
            f"<div style=\"font-size: 42pt; font-weight:700; font-family:'Songti SC',serif; color:{mark_color};\">{overall:.1f}</div>"
            f'<div style="font-size: 14pt; color:{mark_color}; font-weight:600;">{mark_text}</div>'
            f'<div style="color:[[nf:text.muted]]; font-size: 12pt; margin-top:4px;">阈值 {threshold:.1f}</div></div>'
        )

    if scores:
        parts.append("<h2>各维度评分</h2>")
        parts.append('<div class="section" style="padding:10px 12px;">')
        for item in scores:
            if not isinstance(item, dict):
                parts.append(
                    f'<div class="hint-block" style="margin:8px 0;">{_esc(str(item))}</div>'
                )
                continue

            dim = item.get("dimension", "?")
            comment = item.get("comment", "")
            score_val = _safe_float(item.get("score"))
            if score_val is None:
                score_val = 0.0
            score_val = max(0.0, min(10.0, score_val))
            parts.append(
                '<div style="margin:10px 0; padding:10px 12px; '
                "border:1px solid rgba([[nf:border.default]], 0.14); "
                'border-radius:8px; background:rgba([[nf:white]], 0.52);">'
                f'<div style="margin-bottom:6px;"><span class="kv-label" '
                f'style="font-size: 13pt; color:[[nf:text.tab.hover]];">{_esc(_eval_dim_label(dim))}</span></div>'
                f"{_score_bar_html(score_val)}"
            )
            if comment:
                parts.append(
                    f'<div style="color:[[nf:text.secondary]]; font-size: 12pt; margin-top:6px; '
                    f'line-height:1.65;">{_nl2br(comment)}</div>'
                )
            parts.append("</div>")
        parts.append("</div>")

    if summary:
        parts.append(f'<h2>总结</h2><div class="hint-block">{_nl2br(summary)}</div>')

    repair_suggestions = data.get("repair_suggestions", [])
    if repair_suggestions:
        parts.append("<h2>修复建议</h2>")
        # Map dimension to which repair module handles it automatically
        _DIM_REPAIR_MAP = {
            "causal_chain": ("因果校验", "[[nf:status.success.warm]]"),
            "continuity": ("连贯性修复", "[[nf:status.success.warm]]"),
        }
        has_manual_only = any(
            isinstance(s, dict) and s.get("dimension", "") not in _DIM_REPAIR_MAP
            for s in repair_suggestions
        )
        for suggestion in repair_suggestions:
            if isinstance(suggestion, dict):
                dim = suggestion.get("dimension", "")
                dim_label = _eval_dim_label(dim)
                priority = suggestion.get("priority", "medium")
                issue = _esc(suggestion.get("issue", ""))
                location = _esc(suggestion.get("location", ""))
                fix = _esc(suggestion.get("suggestion", ""))
                prio_color = {"high": "[[nf:status.danger.deep]]", "medium": "[[nf:status.warning.alt]]", "low": "[[nf:chart.8]]"}.get(
                    priority, "[[nf:text.muted]]"
                )
                # Adoption badge: show which repair module handles this dimension
                adoption_module, adoption_color = _DIM_REPAIR_MAP.get(dim, (None, None))
                adoption_badge = (
                    f' <span style="font-size: 10pt; color:{adoption_color}; '
                    f"background:rgba([[nf:status.success.warm]], 0.08); padding:1px 5px; border-radius:3px; "
                    f'font-weight:500;">已纳入{adoption_module}</span>'
                    if adoption_module
                    else ' <span style="font-size: 10pt; color:[[nf:text.muted]]; '
                    "background:rgba([[nf:text.muted]], 0.08); padding:1px 5px; border-radius:3px; "
                    'font-weight:400;">需手动处理</span>'
                )
                parts.append(
                    f'<div class="hint-block">'
                    f'<span style="color:{prio_color}; font-weight:600;">[{_esc(priority)}]</span> '
                    f'<span style="font-weight:600;">{dim_label}</span>'
                    f"{(' · ' + location) if location else ''}"
                    f"{adoption_badge}<br/>"
                    f"💡 {issue}{'：' + fix if fix else ''}"
                    f"</div>"
                )
            else:
                parts.append(f'<div class="hint-block">💡 {_esc(str(suggestion))}</div>')
        if has_manual_only:
            parts.append(
                '<div style="color:[[nf:text.muted]]; font-size: 11pt; margin-top:4px; padding:0 4px;">'
                "标注「需手动处理」的建议不属于因果/连贯性修复范畴，不会被自动采纳，需人工参考后在正文中手动修改。"
                "</div>"
            )

    return "\n".join(parts)


def render_eval_report(data: dict[str, Any]) -> QTextBrowser:
    """Render eval report JSON as a formatted HTML view."""
    return _make_browser(_html_wrap(render_eval_report_body_html(data), "质量评估报告"))
