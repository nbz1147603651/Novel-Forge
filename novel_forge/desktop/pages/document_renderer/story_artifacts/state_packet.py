"""Sub-module of novel_forge.desktop.pages.document_renderer.story_artifacts.

Created in P2e. Contains the chapter state-packet renderer and its private
helpers that previously lived in the parent ``__init__.py``:

- ``render_state_packet`` — chapter state packet view
- 7 private helpers (``_clean_state_value``, ``_normalize_foreshadowing_status``,
  ``_extract_nested_str``, ``_format_thread_label``, ``_format_relationship_label``,
  ``_format_recent_event_label``, ``_format_character_end_state``)
- 3 character-role label dictionaries (``_SP_ROLE_LABELS``,
  ``_SP_ROLE_COLORS``, ``_SP_ROLE_BORDER``)

Cross-package dependency: ``generic_key_label`` is imported from
``document_renderer_reports`` to format prev_exit dict keys.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from PySide6.QtWidgets import QTextBrowser

from novel_forge.desktop.pages.document_renderer.story_artifacts._common import (
    _format_age_display,
)
from novel_forge.desktop.pages.document_renderer_reports import generic_key_label
from novel_forge.desktop.pages.standalone.renderer_html import esc as _esc
from novel_forge.desktop.pages.standalone.renderer_html import html_wrap as _html_wrap
from novel_forge.desktop.pages.standalone.renderer_html import make_browser as _make_browser
from novel_forge.desktop.pages.standalone.renderer_html import nl2br as _nl2br

JsonDict = dict[str, Any]

__all__ = ["render_state_packet"]


_SP_ROLE_LABELS: dict[str, str] = {
    "protagonist": "主角",
    "deuteragonist": "第二主角",
    "antagonist": "反派",
    "supporting": "配角",
    "minor": "次要角色",
    "mentor": "导师",
}

_SP_ROLE_COLORS: dict[str, str] = {
    "protagonist": "rgba([[nf:accent.primary]], 0.12)",
    "deuteragonist": "rgba([[nf:status.success.warm]], 0.10)",
    "antagonist": "rgba([[nf:status.danger]], 0.10)",
    "supporting": "rgba([[nf:border.default]], 0.10)",
    "mentor": "rgba([[nf:relation.identity]], 0.10)",
    "minor": "rgba([[nf:outline.ignored]], 0.10)",
}

_SP_ROLE_BORDER: dict[str, str] = {
    "protagonist": "rgba([[nf:accent.primary]], 0.35)",
    "deuteragonist": "rgba([[nf:status.success.warm]], 0.35)",
    "antagonist": "rgba([[nf:status.danger]], 0.35)",
    "supporting": "rgba([[nf:border.default]], 0.28)",
    "mentor": "rgba([[nf:relation.identity]], 0.35)",
    "minor": "rgba([[nf:outline.ignored]], 0.28)",
}


def _clean_state_value(value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    return str(value or "").strip()


def _normalize_foreshadowing_status(status: Any) -> str:
    raw = _clean_state_value(status)
    if not raw:
        return ""
    if "." in raw:
        raw = raw.rsplit(".", 1)[-1]
    return raw.strip().upper()


def _extract_nested_str(value: Any, *keys: str) -> str:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return _clean_state_value(current)


def _format_thread_label(thread: Any) -> str:
    if not isinstance(thread, dict):
        return _clean_state_value(thread)
    title = _clean_state_value(thread.get("title") or thread.get("thread_id"))
    status = _clean_state_value(thread.get("status"))
    owners = thread.get("owners", [])
    owner_text = ""
    if isinstance(owners, list) and owners:
        owner_text = "、".join(
            _clean_state_value(name) for name in owners if _clean_state_value(name)
        )
    summary = _clean_state_value(thread.get("summary"))
    pieces: list[str] = []
    if status:
        pieces.append(f"状态：{status}")
    if owner_text:
        pieces.append(f"角色：{owner_text}")
    if summary:
        pieces.append(summary)
    meta = "；".join(pieces)
    return f"{title} — {meta}" if meta else title


def _format_relationship_label(relation: Any) -> str:
    if not isinstance(relation, dict):
        return _clean_state_value(relation)
    characters = relation.get("characters", [])
    pair = ""
    if isinstance(characters, list):
        names = [_clean_state_value(name) for name in characters if _clean_state_value(name)]
        if names:
            pair = " ↔ ".join(names[:2])
    pair = pair or _clean_state_value(relation.get("pair_id"))
    status = _clean_state_value(relation.get("public_status"))
    shift = _clean_state_value(relation.get("last_shift_event"))
    metrics: list[str] = []
    trust = relation.get("trust")
    tension = relation.get("tension")
    if isinstance(trust, (int, float)):
        metrics.append(f"信任={trust:.2f}")
    if isinstance(tension, (int, float)):
        metrics.append(f"张力={tension:.2f}")
    parts = [status] if status else []
    if shift:
        parts.append(f"变化：{shift}")
    if metrics:
        parts.append("，".join(metrics))
    suffix = "；".join(part for part in parts if part)
    return f"{pair} — {suffix}" if suffix else pair


def _format_recent_event_label(event: Any) -> str:
    if not isinstance(event, dict):
        return _clean_state_value(event)
    chapter = event.get("chapter")
    chapter_tag = f"[第{chapter}章] " if chapter else ""
    description = _clean_state_value(event.get("event"))
    chars = event.get("characters_involved", [])
    chars_text = ""
    if isinstance(chars, list):
        items = [_clean_state_value(name) for name in chars if _clean_state_value(name)]
        if items:
            chars_text = f"（涉及：{'、'.join(items[:3])}）"
    return chapter_tag + description + chars_text


def _format_character_end_state(name: str, state: Any) -> str:
    raw_name = _clean_state_value(name) or "未知角色"
    if not isinstance(state, dict):
        return raw_name
    alive = state.get("alive")
    alive_text = ""
    if isinstance(alive, bool):
        alive_text = "存活" if alive else "死亡"
    location = _clean_state_value(state.get("location")) or _extract_nested_str(
        state, "physical", "location"
    )
    emotion = _clean_state_value(state.get("emotional_state")) or _extract_nested_str(
        state, "emotional", "primary_emotion"
    )
    drive = _extract_nested_str(state, "motivation", "current_drive")
    pieces = [
        piece
        for piece in [
            alive_text,
            f"地点：{location}" if location else "",
            f"情绪：{emotion}" if emotion else "",
            f"驱动：{drive}" if drive else "",
        ]
        if piece
    ]
    if not pieces:
        return raw_name
    return f"{raw_name} — " + "；".join(pieces)


def render_state_packet(data: JsonDict) -> QTextBrowser:
    """Render a chapter state packet JSON as a richly formatted HTML view."""
    chapter_num = data.get("chapter_number", "?")
    vol_num = data.get("current_volume_number", 1)
    outline: JsonDict = data.get("chapter_outline", {})
    canon: JsonDict = data.get("canon_context", {})
    char_profiles: list[Any] = data.get("character_profiles", [])
    prev_ending: str = data.get("previous_chapter_ending", "")
    prev_exit: JsonDict | None = data.get("previous_exit_state")
    prev_vol_summary: str = data.get("previous_volume_summary", "")

    title = outline.get("title", "")
    goal = outline.get("goal", "")
    beats: list[Any] = outline.get("beats_summary", [])
    main_pts: list[Any] = outline.get("main_plot_points", [])
    sub_pts: list[Any] = outline.get("subplot_points", [])
    subplot_focus: str = outline.get("subplot_focus", "")
    pov: str = outline.get("pov_character", "")
    pov_switch: bool = outline.get("pov_switch", False)
    setting: str = outline.get("setting", "")
    word_count: int = outline.get("expected_word_count", 0)
    notes: str = outline.get("notes", "")

    parts: list[str] = []
    display_title = f"第 {chapter_num} 章 · {title}" if title else f"第 {chapter_num} 章"
    vol_tag = (
        f'<span style="display:inline-block; background:rgba([[nf:border.default]], 0.10); '
        f"border:1px solid rgba([[nf:border.default]], 0.25); border-radius:5px; "
        f'padding:2px 8px; font-size: 11pt; color:[[nf:text.chapter.rail]]; margin-left:8px;">'
        f"第 {vol_num} 卷</span>"
        if vol_num
        else ""
    )
    parts.append(
        f'<div style="background: rgba([[nf:bg.inset]], 0.5); border-radius:12px; '
        f"padding: 18px 22px 14px 22px; margin-bottom:14px; "
        f'border: 1px solid rgba([[nf:border.default]], 0.14);">'
        f"<div style=\"font-family:'Songti SC','STSong',Georgia,serif; "
        f'font-size: 20pt; font-weight:700; color:[[nf:text.heading.deep]];">'
        f"{_esc(display_title)}{vol_tag}</div>"
    )

    meta_tags: list[str] = []
    if pov:
        meta_tags.append(f'<span class="tag-muted tag">👁 {_esc(pov)} 视角</span>')
    if pov_switch:
        meta_tags.append(
            '<span class="tag-muted tag" style="background:rgba([[nf:motif.purple]], 0.10); '
            'color:[[nf:motif.purple]]; border-color:rgba([[nf:motif.purple]], 0.25);">'
            "&#x21C4; 视角切换章</span>"
        )
    if word_count:
        meta_tags.append(f'<span class="tag-muted tag">📝 预计 {word_count:,} 字</span>')
    if setting:
        setting_preview = setting[:40]
        suffix = "…" if len(setting) > 40 else ""
        meta_tags.append(f'<span class="tag-muted tag">📍 {_esc(setting_preview)}{suffix}</span>')
    if meta_tags:
        parts.append(f'<div style="margin-top:10px;">{"".join(meta_tags)}</div>')
    parts.append("</div>")

    if goal:
        parts.append(
            f"<h2>章节目标</h2>"
            f'<div class="hint-block" style="font-size: 14pt; line-height:1.75;">'
            f"{_nl2br(goal)}</div>"
        )

    if beats:
        parts.append(f"<h2>情节节拍 · 共 {len(beats)} 拍</h2>")
        for index, beat in enumerate(beats, 1):
            parts.append(
                f'<div style="display:flex; align-items:flex-start; margin:8px 0;">'
                f'<div style="min-width:26px; height:26px; background:rgba([[nf:accent.primary]], 0.12); '
                f"border-radius:50%; display:inline-flex; align-items:center; "
                f"justify-content:center; font-size: 12pt; font-weight:700; "
                f"color:[[nf:accent.deep]]; margin-right:10px; flex-shrink:0; "
                f'line-height:26px; text-align:center;">{index}</div>'
                f'<div class="kv-value" style="padding-top:4px; flex:1;">'
                f"{_nl2br(beat)}</div></div>"
            )

    if main_pts:
        parts.append("<h2>主线要点</h2>")
        for point in main_pts:
            parts.append(f'<div class="rule-item">{_nl2br(point)}</div>')

    if sub_pts or subplot_focus:
        parts.append("<h2>支线要点</h2>")
        if subplot_focus:
            parts.append(
                f'<div class="kv-row"><span class="kv-label">支线焦点：</span>'
                f'<span class="kv-value">{_nl2br(subplot_focus)}</span></div>'
            )
        for point in sub_pts:
            parts.append(f'<div class="theme-item">{_nl2br(point)}</div>')

    if notes:
        parts.append(f'<h2>编剧备注</h2><div class="hint-block">{_nl2br(notes)}</div>')

    if char_profiles:
        parts.append(f"<h2>本章人物 · 共 {len(char_profiles)} 位</h2>")
        for char in char_profiles:
            name = char.get("name", "未知")
            role = char.get("role", "")
            age = char.get("age", "")
            gender = char.get("gender", "")
            status = char.get("status", "active")
            appearance = char.get("appearance", "")
            personality = char.get("personality", "")
            backstory = char.get("backstory", "")
            arc = char.get("arc", "")
            relationships: JsonDict = char.get("relationships", {})

            role_label = _SP_ROLE_LABELS.get(role, role)
            background = _SP_ROLE_COLORS.get(role, "rgba([[nf:bg.inset]], 0.5)")
            border = _SP_ROLE_BORDER.get(role, "rgba([[nf:border.default]], 0.15)")

            parts.append(
                f'<div style="background:{background}; border:1px solid {border}; '
                f'border-radius:10px; padding:14px 18px; margin:8px 0;">'
                f'<div style="display:flex; align-items:center; margin-bottom:8px;">'
                f"<span style=\"font-family:'Songti SC',serif; font-size: 16pt; "
                f'font-weight:700; color:[[nf:text.heading.deep]];">{_esc(name)}</span>'
            )
            if role_label:
                parts.append(
                    f'<span style="display:inline-block; background:rgba([[nf:white]], 0.6); '
                    f"border:1px solid {border}; border-radius:5px; "
                    f"padding:1px 7px; font-size: 11pt; font-weight:600; "
                    f'color:[[nf:text.body.alt]]; margin-left:8px;">{_esc(role_label)}</span>'
                )
            age_text = _format_age_display(age)
            if age_text:
                parts.append(
                    f'<span style="color:[[nf:text.muted]]; font-size: 12pt; margin-left:8px;">'
                    f"{_esc(age_text)}</span>"
                )
            if gender:
                parts.append(
                    f'<span style="color:[[nf:text.muted]]; font-size: 12pt; margin-left:8px;">'
                    f"{_esc(gender)}</span>"
                )
            if status and status != "active":
                status_label = {
                    "强健": "休眠",
                    "dormant": "休眠",
                    "retired": "退场",
                    "deceased": "已故",
                    "dead": "已故",
                    "died": "已故",
                    "已故": "已故",
                    "故去": "已故",
                    "死亡": "已故",
                    "离世": "已故",
                    "逝世": "已故",
                }.get(status) or status
                status_color = {
                    "dormant": "[[nf:text.char.status]]",
                    "retired": "[[nf:text.char.retired]]",
                    "deceased": "[[nf:text.char.retired]]",
                    "dead": "[[nf:text.char.retired]]",
                    "died": "[[nf:text.char.retired]]",
                }.get(status) or "[[nf:text.char.status]]"
                parts.append(
                    f'<span style="display:inline-block; background:{status_color}18; '
                    f"color:{status_color}; border:1px solid {status_color}35; "
                    f'border-radius:5px; padding:1px 6px; font-size: 10pt; '
                    f'font-weight:600; margin-left:8px;">{_esc(status_label)}</span>'
                )
            parts.append("</div>")

            for field_text, field_label in (
                (appearance, "外貌"),
                (personality, "性格"),
                (backstory, "背景"),
                (arc, "成长弧"),
            ):
                if field_text:
                    parts.append(
                        f'<div class="kv-row" style="margin:5px 0;">'
                        f'<span class="kv-label">{field_label}：</span>'
                        f'<div class="kv-value" style="display:inline;">'
                        f"{_nl2br(field_text)}</div></div>"
                    )

            if relationships:
                rel_tags = "".join(
                    f'<span class="tag-muted tag">{_esc(key)}</span> '
                    f'<span class="kv-value" style="font-size: 12pt;">{_nl2br(value)}</span>  '
                    for key, value in relationships.items()
                )
                parts.append(
                    f'<div class="kv-row" style="margin-top:8px;">'
                    f'<span class="kv-label">关系网络：</span>'
                    f'<div style="margin-top:4px;">{rel_tags}</div></div>'
                )

            parts.append("</div>")

    must_carry: list[Any] = canon.get("must_carry_forward", [])
    active_threads: list[Any] = canon.get("active_plot_threads", [])
    active_rels: list[Any] = canon.get("active_relationships", [])
    active_fore: list[Any] = canon.get("active_foreshadowing", [])
    recent_events: list[Any] = canon.get("recent_events", [])
    prev_summary: str = canon.get("previous_chapter_summary", "")

    has_canon = any(
        [
            must_carry,
            active_threads,
            active_rels,
            active_fore,
            recent_events,
            prev_summary,
            prev_ending,
            prev_exit,
        ]
    )
    if has_canon:
        parts.append("<h2>叙事上下文</h2>")

    if prev_ending:
        parts.append(
            f'<div class="section" style="margin-bottom:10px;">'
            f'<h3 style="margin-top:0;">上章结尾</h3>'
            f'<div class="kv-value">{_nl2br(prev_ending)}</div>'
            f"</div>"
        )
    elif prev_summary:
        parts.append(
            f'<div class="section" style="margin-bottom:10px;">'
            f'<h3 style="margin-top:0;">上章摘要</h3>'
            f'<div class="kv-value">{_nl2br(prev_summary)}</div>'
            f"</div>"
        )

    if prev_vol_summary:
        parts.append(
            f'<div class="section" style="margin-bottom:10px;">'
            f'<h3 style="margin-top:0;">上卷摘要</h3>'
            f'<div class="kv-value">{_nl2br(prev_vol_summary)}</div>'
            f"</div>"
        )

    if prev_exit and isinstance(prev_exit, dict):
        exit_items = [
            f'<div class="kv-row"><span class="kv-label">'
            f"{_esc(str(generic_key_label(key)))}：</span>"
            f'<span class="kv-value">{_esc(str(value))}</span></div>'
            for key, value in prev_exit.items()
            if key not in ("schema_version", "created_at", "character_end_states") and value
        ]
        if exit_items:
            parts.append(
                '<div class="section" style="margin-bottom:10px;">'
                '<h3 style="margin-top:0;">上章退出状态</h3>' + "\n".join(exit_items) + "</div>"
            )
        char_end_states = prev_exit.get("character_end_states")
        if isinstance(char_end_states, dict) and char_end_states:
            parts.append(
                '<div class="section" style="margin-bottom:10px;">'
                '<h3 style="margin-top:0;">角色收束状态</h3>'
            )
            for char_name, char_state in char_end_states.items():
                label = _format_character_end_state(str(char_name), char_state)
                if label:
                    parts.append(f'<div class="rule-item">{_nl2br(label)}</div>')
            parts.append("</div>")

    if must_carry:
        parts.append(
            '<div style="background:rgba([[nf:accent.primary]], 0.06); '
            "border:1px dashed rgba([[nf:accent.primary]], 0.30); "
            'border-radius:10px; padding:12px 16px; margin:8px 0;">'
            '<div style="font-weight:700; color:[[nf:accent.deep]]; font-size: 13pt; '
            'margin-bottom:6px;">⚠ 必须承接</div>'
        )
        for item in must_carry:
            # Carry-forward items may be structured dicts/objects; project text.
            if isinstance(item, dict):
                display = str(item.get("text") or item.get("item") or item)
            else:
                text_attr = getattr(item, "text", None)
                display = str(text_attr) if text_attr is not None and not isinstance(item, str) else str(item)
            parts.append(
                f'<div style="margin:4px 0; padding-left:8px; '
                f"border-left:2px solid rgba([[nf:accent.primary]], 0.4); "
                f'font-size: 13pt; color:[[nf:text.tab.hover]];">{_nl2br(display)}</div>'
            )
        parts.append("</div>")

    if active_threads:
        parts.append(f'<h3 style="margin-top:12px;">活跃情节线 · {len(active_threads)} 条</h3>')
        for thread in active_threads:
            label = _format_thread_label(thread)
            parts.append(f'<div class="rule-item">{_nl2br(label)}</div>')

    if active_rels:
        parts.append(f'<h3 style="margin-top:12px;">活跃关系 · {len(active_rels)} 条</h3>')
        for relation in active_rels:
            label = _format_relationship_label(relation)
            parts.append(f'<div class="theme-item">{_nl2br(label)}</div>')

    if active_fore:
        parts.append(f'<h3 style="margin-top:12px;">活跃伏笔 · {len(active_fore)} 处</h3>')
        for foreshadowing in active_fore:
            if isinstance(foreshadowing, dict):
                # 格式化伏笔字典数据
                fs_id = _clean_state_value(foreshadowing.get("id", ""))
                description = _clean_state_value(foreshadowing.get("description", ""))
                notes = _clean_state_value(foreshadowing.get("notes", ""))
                status = _clean_state_value(foreshadowing.get("status", ""))
                planted_chapter = foreshadowing.get("planted_chapter", 0)
                resolved_chapter = foreshadowing.get("resolved_chapter", 0)

                # 状态标签和颜色
                status_label = _normalize_foreshadowing_status(status)
                status_color_map = {
                    "PLANTED": ("[[nf:accent.rank.label]]", "🌱 已埋设"),
                    "REINFORCED": ("[[nf:accent.primary]]", "🔗 已强化"),
                    "REVEALED": ("[[nf:status.success.deep]]", "✅ 已揭晓"),
                    "ABANDONED": ("[[nf:text.char.status]]", "🗑 已废弃"),
                    # Backward/legacy aliases
                    "RESOLVED": ("[[nf:status.success.deep]]", "✅ 已回收"),
                    "ARCHIVED": ("[[nf:text.char.status]]", "📦 已归档"),
                }
                status_color, status_display = status_color_map.get(
                    status_label, ("[[nf:text.char.status]]", status_label or "状态未知")
                )

                # 防止 "description == id" 导致正文重复显示占位符
                body_text = description
                if body_text and fs_id and body_text == fs_id:
                    body_text = ""
                if not body_text and notes:
                    body_text = notes
                if not body_text:
                    body_text = "（未提供伏笔描述）"

                # 伏笔 ID 标签
                id_tag = (
                    f'<span class="tag-muted tag" style="margin-right:6px;">{_esc(fs_id)}</span>'
                    if fs_id
                    else ""
                )

                # 种植章节信息
                chapter_info = ""
                if planted_chapter > 0:
                    chapter_info = f'<span style="color:[[nf:text.muted]]; font-size: 11pt;">📍 埋设：第 {planted_chapter} 章</span>'
                    if resolved_chapter > 0:
                        chapter_info += f' → <span style="color:[[nf:status.success.deep]]; font-size: 11pt;">✅ 回收：第 {resolved_chapter} 章</span>'

                parts.append(
                    f'<div style="background:rgba([[nf:accent.rank.label]], 0.06); '
                    f"border:1px dashed rgba([[nf:accent.rank.label]], 0.30); "
                    f'border-radius:8px; padding:10px 14px; margin:6px 0;">'
                    f'<div style="display:flex; align-items:center; margin-bottom:4px;">'
                    f"{id_tag}"
                    f'<span style="display:inline-block; background:{status_color}18; '
                    f"color:{status_color}; border:1px solid {status_color}35; "
                    f'border-radius:5px; padding:1px 7px; font-size: 10pt; font-weight:600;">'
                    f"{status_display}</span>"
                    f'<div style="flex:1;"></div>'
                    f"{chapter_info}"
                    f"</div>"
                    f'<div style="color:[[nf:text.body]]; font-size: 13pt; line-height:1.6;">'
                    f"{_nl2br(_esc(body_text))}"
                    f"</div>"
                    f"</div>"
                )
            else:
                # 字符串格式的伏笔
                label = foreshadowing if isinstance(foreshadowing, str) else str(foreshadowing)
                parts.append(f'<div class="hint-block" style="margin:4px 0;">{_nl2br(label)}</div>')

    if recent_events:
        parts.append(f'<h3 style="margin-top:12px;">近期事件 · {len(recent_events)} 项</h3>')
        for event in recent_events:
            label = _format_recent_event_label(event)
            parts.append(f'<div class="hint-block" style="margin:4px 0;">{_nl2br(label)}</div>')

    return _make_browser(_html_wrap("\n".join(parts), f"章节上下文 · {display_title}"))
