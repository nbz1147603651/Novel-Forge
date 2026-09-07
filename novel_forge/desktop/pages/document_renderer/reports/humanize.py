"""Sub-module of novel_forge.desktop.pages.document_renderer.reports.

Auto-generated in the M3.4 split. Contains humanize.py renderers.

NOTE: Several humanize-specific helpers (``_HUMANIZE_SEVERITY_LABELS``,
``_HUMANIZE_SEVERITY_ORDER``, ``_humanize_*``, ``_generic_tag``) live in
the parent ``__init__.py`` (not yet migrated). That coupling is preserved
until a future P2 iteration moves the humanize-specific render helpers
into a dedicated module.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QTextBrowser

from novel_forge.desktop.pages.document_renderer.reports import (
    _HUMANIZE_SEVERITY_LABELS,
    _HUMANIZE_SEVERITY_ORDER,
    _humanize_bool_label,
    _humanize_category_distribution,
    _humanize_metric,
    _humanize_score_label,
    _humanize_severity_tone,
)
from novel_forge.desktop.pages.document_renderer.reports._common import (
    _BIBLE_FIELD_LABELS,
    _GENRE_LABELS,
    _TONE_LABELS,
    _safe_float,
)
from novel_forge.desktop.pages.document_renderer.reports.generic_report import _generic_tag
from novel_forge.desktop.pages.standalone.renderer_html import esc as _esc
from novel_forge.desktop.pages.standalone.renderer_html import html_wrap as _html_wrap
from novel_forge.desktop.pages.standalone.renderer_html import make_browser as _make_browser
from novel_forge.desktop.pages.standalone.renderer_html import nl2br as _nl2br
from novel_forge.desktop.pages.standalone.renderer_html import score_color as _score_color

__all__ = [
    "render_story_bible",
    "render_spec",
    "render_humanize_report_body_html",
    "render_humanize_report",
]


def _safe_int(value: Any, default: int = 0) -> int:
    """Best-effort integer conversion for renderer counters.

    Unlike the ``evaluation`` / ``generic_report`` variants, this one coerces
    booleans to the ``default`` value rather than ``int(bool)`` and accepts
    an explicit ``default`` parameter.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            return int(float(text))
        except ValueError:
            return default
    return default


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


def render_humanize_report_body_html(data: dict[str, Any]) -> str:
    """Render HumanizeReport JSON as a scan-friendly report body."""
    score = _safe_float(data.get("humanize_score"))
    total_hits = _safe_int(data.get("total_hits"), 0)
    critical_hits = _safe_int(data.get("critical_hits"), 0)
    summary = str(data.get("summary") or "").strip()
    raw_hits = data.get("pattern_hits")
    pattern_hits = (
        [hit for hit in raw_hits if isinstance(hit, dict)] if isinstance(raw_hits, list) else []
    )
    raw_categories = data.get("hits_by_category")
    hits_by_category = raw_categories if isinstance(raw_categories, dict) else {}

    parts: list[str] = []
    score_text = "—" if score is None else f"{max(0.0, min(10.0, score)):.1f}"
    score_color = "[[nf:text.muted]]" if score is None else _score_color(max(0.0, min(10.0, score)))

    parts.append(
        '<div class="section" style="text-align:center; padding:20px;">'
        f'<div style="font-size: 42pt; font-weight:700; '
        f"font-family:'Songti SC',serif; color:{score_color};\">{_esc(score_text)}</div>"
        f'<div style="font-size: 13pt; color:{score_color}; font-weight:700;">'
        f"{_esc(_humanize_score_label(score))}</div>"
        '<div style="color:[[nf:text.muted]]; font-size: 12pt; margin-top:4px;">Humanize Score</div>'
        "</div>"
    )

    parts.append(
        '<div class="metric-grid">'
        + _humanize_metric("总命中", total_hits)
        + _humanize_metric("严重命中", critical_hits, color="[[nf:status.danger.deep]]" if critical_hits else None)
        + _humanize_metric("分类数", len(hits_by_category))
        + _humanize_metric("可修复", sum(1 for hit in pattern_hits if hit.get("actionable")))
        + "</div>"
    )

    if summary:
        parts.append(f'<h2>摘要</h2><div class="hint-block">{_nl2br(summary)}</div>')

    parts.append("<h2>分类分布</h2>")
    parts.append(_humanize_category_distribution(hits_by_category))

    parts.append("<h2>模式命中</h2>")
    if not pattern_hits:
        parts.append('<div class="hint-block">未发现明显 AI 痕迹。</div>')
    else:
        sorted_hits = sorted(
            pattern_hits,
            key=lambda hit: (
                _HUMANIZE_SEVERITY_ORDER.get(str(hit.get("severity") or "").lower(), 99),
                _safe_int(hit.get("paragraph_index"), 0),
                str(hit.get("pattern_id") or ""),
            ),
        )
        for index, hit in enumerate(sorted_hits, 1):
            severity = str(hit.get("severity") or "").strip().lower()
            severity_label = _HUMANIZE_SEVERITY_LABELS.get(severity, severity or "未知")
            pattern_name = str(hit.get("pattern_name") or hit.get("pattern_id") or f"命中 {index}")
            category = str(hit.get("category") or "未分类")
            confidence = _safe_float(hit.get("confidence"))
            confidence_text = "—" if confidence is None else f"{confidence:.2f}"
            paragraph_index = _safe_int(hit.get("paragraph_index"), 0)
            evidence = str(hit.get("evidence_quote") or "").strip()
            suggestion = str(hit.get("suggestion") or "").strip()
            source = str(hit.get("source") or "").strip()
            tags = (
                _generic_tag(severity_label, tone=_humanize_severity_tone(severity))
                + _generic_tag(category)
                + _generic_tag(_humanize_bool_label(hit.get("actionable")))
            )
            if source:
                tags += _generic_tag(source)

            parts.append(
                '<div class="mini-card">'
                f"<h3>{_esc(pattern_name)}</h3>"
                f'<div style="margin:2px 0 8px 0;">{tags}</div>'
                "<table><tbody>"
                f"<tr><th>段落</th><td>第 {paragraph_index + 1} 段</td></tr>"
                f"<tr><th>置信度</th><td>{_esc(confidence_text)}</td></tr>"
                + (f"<tr><th>证据</th><td>{_nl2br(evidence)}</td></tr>" if evidence else "")
                + (f"<tr><th>建议</th><td>{_nl2br(suggestion)}</td></tr>" if suggestion else "")
                + "</tbody></table></div>"
            )

    return "\n".join(parts)


def render_humanize_report(data: dict[str, Any]) -> QTextBrowser:
    """Render HumanizeReport JSON as a scan-friendly report view."""
    chapter_number = _safe_int(data.get("chapter_number"), 0)
    title_suffix = f" · 第 {chapter_number} 章" if chapter_number > 0 else ""
    return _make_browser(
        _html_wrap(render_humanize_report_body_html(data), f"拟人化扫描{title_suffix}")
    )
