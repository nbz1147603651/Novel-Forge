"""Sub-module of novel_forge.desktop.pages.document_renderer.reports.

Migration P2-step: ``render_story_bible`` (story_bible.json) and
``render_spec`` (spec.json) live here.

Both rely on ``_BIBLE_FIELD_LABELS``, ``_GENRE_LABELS``, ``_TONE_LABELS``
which are shared via :mod:`._common`.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QTextBrowser

from novel_forge.desktop.pages.document_renderer.reports._common import (
    _BIBLE_FIELD_LABELS,
    _GENRE_LABELS,
    _TONE_LABELS,
)
from novel_forge.desktop.pages.standalone.renderer_html import esc as _esc
from novel_forge.desktop.pages.standalone.renderer_html import html_wrap as _html_wrap
from novel_forge.desktop.pages.standalone.renderer_html import make_browser as _make_browser
from novel_forge.desktop.pages.standalone.renderer_html import nl2br as _nl2br

__all__ = ["render_story_bible", "render_spec"]


def _render_world_rule_book(rule_book: dict[str, Any]) -> list[str]:
    """Render the structured ``world_rule_book`` as detailed HTML rule cards.

    The structured rule book (introduced in ec88814c) is the authoritative
    source of truth: each rule carries category, severity, trigger_conditions,
    forbidden_behavior, cost_or_consequence, and exceptions. The legacy
    ``rules`` string list is only a display summary, so we render the full
    structure here so authors can review executable rules.
    """
    parts: list[str] = []
    description = str(rule_book.get("description") or "").strip()
    if description:
        parts.append(
            f'<div class="section"><h3>规则账本概述</h3>'
            f'<div class="kv-value">{_nl2br(description)}</div></div>'
        )

    rules = rule_book.get("rules")
    if not isinstance(rules, list) or not rules:
        return parts

    parts.append("<h2>世界规则账本</h2>")

    _severity_label = {"hard": "硬约束", "soft": "软约束"}
    _category_label = {
        "time_space": "时空",
        "social_language": "社会/语言",
        "resource_material": "资源/物质",
        "information": "信息",
        "ability_tech": "能力/技术",
        "general": "通用",
    }

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        content = str(rule.get("content") or "").strip()
        if not content:
            continue

        rule_id = str(rule.get("rule_id") or "").strip()
        severity = str(rule.get("severity") or "").strip()
        category = str(rule.get("category") or "").strip()
        always_on = bool(rule.get("always_on"))

        badges: list[str] = []
        if severity:
            sev_label = _severity_label.get(severity, severity)
            sev_cls = "tag" if severity == "hard" else "tag-muted tag"
            badges.append(f'<span class="{sev_cls}">{_esc(sev_label)}</span>')
        if category:
            cat_label = _category_label.get(category, category)
            badges.append(f'<span class="tag-muted tag">{_esc(cat_label)}</span>')
        if always_on:
            badges.append('<span class="tag">始终生效</span>')

        header = (
            f'<div class="rule-id">{_esc(rule_id)}</div>' if rule_id else ""
        )
        badge_row = (
            f'<div class="rule-badges">{"".join(badges)}</div>' if badges else ""
        )

        detail_rows: list[str] = []
        for key, label in (
            ("trigger_conditions", "触发条件"),
            ("allowed_behavior", "允许行为"),
            ("forbidden_behavior", "禁止行为"),
            ("cost_or_consequence", "代价/后果"),
            ("exceptions", "例外"),
        ):
            value = rule.get(key)
            if isinstance(value, list):
                text = "；".join(str(v).strip() for v in value if str(v).strip())
            else:
                text = str(value or "").strip()
            if text:
                detail_rows.append(
                    f'<div class="rule-detail-row"><span class="rule-detail-label">'
                    f'{_esc(label)}</span><span class="rule-detail-value">'
                    f'{_nl2br(text)}</span></div>'
                )

        tags = rule.get("applicability_tags")
        if isinstance(tags, list) and tags:
            tag_text = "、".join(str(t).strip() for t in tags if str(t).strip())
            if tag_text:
                detail_rows.append(
                    f'<div class="rule-detail-row"><span class="rule-detail-label">'
                    f'适用标签</span><span class="rule-detail-value">'
                    f'{_esc(tag_text)}</span></div>'
                )

        details = (
            f'<div class="rule-details">{"".join(detail_rows)}</div>'
            if detail_rows
            else ""
        )

        parts.append(
            f'<div class="rule-card">{header}{badge_row}'
            f'<div class="rule-content">{_nl2br(content)}</div>{details}</div>'
        )

    return parts


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

    world_rule_book = data.get("world_rule_book")
    if isinstance(world_rule_book, dict) and world_rule_book:
        parts.extend(_render_world_rule_book(world_rule_book))

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
