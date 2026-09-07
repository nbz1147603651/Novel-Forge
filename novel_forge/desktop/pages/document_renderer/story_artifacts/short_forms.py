"""Sub-module of novel_forge.desktop.pages.document_renderer.story_artifacts.

Created in P2c. Contains renderers for the short-form artefacts that used
to live in the parent ``__init__.py``:

- ``render_short_creative_summary`` — `short_creative_summary.json` view
- ``render_short_blueprint`` — short-story narrative blueprint view

Both functions are self-contained — they only depend on
``renderer_html`` helpers (``_esc`` / ``_nl2br`` / ``_html_wrap`` /
``_make_browser``) — so they don't share any private helpers with the
other story_artifacts submodules.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QTextBrowser

from novel_forge.desktop.pages.standalone.renderer_html import esc as _esc
from novel_forge.desktop.pages.standalone.renderer_html import html_wrap as _html_wrap
from novel_forge.desktop.pages.standalone.renderer_html import make_browser as _make_browser
from novel_forge.desktop.pages.standalone.renderer_html import nl2br as _nl2br

JsonDict = dict[str, Any]

__all__ = [
    "render_short_creative_summary",
    "render_short_blueprint",
]


def render_short_creative_summary(data: dict[str, Any]) -> QTextBrowser:
    """Render short_creative_summary.json as a formatted HTML view."""
    parts: list[str] = []

    characters = data.get("characters", [])
    if characters:
        parts.append(f"<h2>角色分析 · {len(characters)}</h2>")
        for char in characters:
            if not isinstance(char, dict):
                continue
            name = char.get("name", "未知")
            role = char.get("role", "")
            arc = char.get("arc_summary", "")
            traits = char.get("key_traits", [])
            parts.append('<div class="section" style="margin-bottom:10px;">')
            parts.append(f"<strong>{_esc(name)}</strong>")
            if role:
                parts.append(f' <span class="tag">{_esc(role)}</span>')
            if traits:
                tags = " ".join(
                    f'<span class="tag" style="background:rgba([[nf:accent.primary]], 0.08);">'
                    f"{_esc(trait)}</span>"
                    for trait in traits
                )
                parts.append(f"<br>{tags}")
            if arc:
                parts.append(f'<br><span style="color:[[nf:text.body.alt]];">{_esc(arc)}</span>')
            relationships = char.get("relationships", [])
            if relationships:
                rel_items: list[str] = []
                for relation in relationships:
                    if isinstance(relation, dict):
                        target = relation.get("target", "")
                        relation_type = relation.get("type", "")
                        note = relation.get("note", "")
                        label = f"{_esc(target)}（{_esc(relation_type)}）"
                        if note:
                            label += f"：{_esc(note)}"
                        rel_items.append(label)
                if rel_items:
                    parts.append(
                        f'<br><span style="color:[[nf:text.muted]]; font-size: 12pt;">'
                        f"关系：{'、'.join(rel_items)}</span>"
                    )
            parts.append("</div>")

    narrative = data.get("narrative_analysis", {}) or {}
    if narrative:
        parts.append("<h2>叙事结构</h2>")
        structure = narrative.get("structure_type", "")
        if structure:
            parts.append(f'<div class="hint-block">📐 结构类型：{_esc(structure)}</div>')
        pacing = narrative.get("pacing_assessment", "")
        if pacing:
            parts.append(f'<div class="hint-block">⏱️ 节奏：{_esc(pacing)}</div>')
        tension = narrative.get("tension_curve", "")
        tension_note = narrative.get("tension_curve_note", "")
        if tension:
            color = {"high": "[[nf:status.success.deep]]", "medium": "[[nf:accent.rank.label]]", "low": "[[nf:accent.primary]]"}.get(
                tension.lower(),
                "[[nf:text.body.alt]]",
            )
            parts.append(
                f'<div class="hint-block">📈 张力匹配度：'
                f'<span style="color:{color}; font-weight:600;">{_esc(tension)}</span>'
            )
            if tension_note:
                parts.append(f"<br>{_esc(tension_note)}")
            parts.append("</div>")
        hook = narrative.get("opening_hook", "")
        if hook:
            parts.append(f'<div class="hint-block">🎣 开篇钩子：{_esc(hook)}</div>')
        ending = narrative.get("ending_impact", "")
        if ending:
            parts.append(f'<div class="hint-block">🏁 结尾收束：{_esc(ending)}</div>')
        turning_points = narrative.get("turning_points", [])
        if turning_points:
            parts.append(f"<h3>关键转折 · {len(turning_points)}</h3>")
            for turning_point in turning_points:
                if isinstance(turning_point, dict):
                    desc = turning_point.get("description", "")
                    effectiveness = turning_point.get("effectiveness", "")
                    eff_color = {
                        "高": "[[nf:status.success.deep]]",
                        "中": "[[nf:accent.rank.label]]",
                        "低": "[[nf:accent.primary]]",
                    }.get(effectiveness, "[[nf:text.body.alt]]")
                    parts.append(
                        f'<div class="rule-item">{_esc(desc)}'
                        f' <span style="color:{eff_color}; font-weight:600;">'
                        f"[{_esc(effectiveness)}]</span></div>"
                    )

    thematic = data.get("thematic_analysis", {}) or {}
    if thematic:
        parts.append("<h2>主题分析</h2>")
        core = thematic.get("core_theme", "")
        if core:
            parts.append(
                f'<div class="section" style="font-size: 14pt; '
                f"font-family:'Songti SC',serif; padding:12px 16px;\">"
                f"🎯 {_esc(core)}</div>"
            )
        delivery = thematic.get("theme_delivery", "")
        if delivery:
            parts.append(f'<div class="hint-block">📝 传达方式：{_esc(delivery)}</div>')
        symbols = thematic.get("symbolic_elements", [])
        if symbols:
            parts.append(
                f'<div class="hint-block">🔮 象征元素：'
                f"{'、'.join(_esc(str(symbol)) for symbol in symbols)}</div>"
            )
        resonance = thematic.get("emotional_resonance", "")
        if resonance:
            parts.append(f'<div class="hint-block">💫 情感共鸣：{_esc(resonance)}</div>')

    highlights = data.get("creative_highlights", [])
    if highlights:
        parts.append("<h2>创作亮点</h2>")
        for highlight in highlights:
            parts.append(f'<div class="hint-block">✦ {_esc(str(highlight))}</div>')

    suggestions = data.get("improvement_suggestions", [])
    if suggestions:
        parts.append("<h2>改进建议</h2>")
        for index, suggestion in enumerate(suggestions, 1):
            parts.append(f'<div class="rule-item">{index}. {_esc(str(suggestion))}</div>')

    fulfillment = data.get("beat_fulfillment", [])
    if fulfillment:
        fulfilled_count = sum(1 for item in fulfillment if item.get("fulfilled"))
        total = len(fulfillment)
        rate = fulfilled_count / max(total, 1)
        rate_color = "[[nf:status.success.deep]]" if rate >= 0.8 else "[[nf:accent.rank.label]]" if rate >= 0.5 else "[[nf:accent.primary]]"
        parts.append(
            f"<h2>节拍落地 · "
            f'<span style="color:{rate_color};">{fulfilled_count}/{total}</span></h2>'
        )
        for item in fulfillment:
            if not isinstance(item, dict):
                continue
            sequence = item.get("sequence", "?")
            ok = item.get("fulfilled", False)
            note = item.get("note", "")
            icon = "✅" if ok else "❌"
            parts.append(
                f'<div class="rule-item" style="border-left-color:'
                f'{"[[nf:status.success.deep]]" if ok else "[[nf:accent.primary]]"};">'
                f"{icon} 节拍 {sequence}：{_esc(note)}</div>"
            )

    if not parts:
        parts.append(
            '<div style="color:[[nf:text.muted]]; padding:32px; text-align:center;">暂无创作分析数据</div>'
        )

    return _make_browser(_html_wrap("\n".join(parts), "短篇创作分析"))


def render_short_blueprint(data: JsonDict) -> QTextBrowser:
    """Render a short-story narrative blueprint as rich HTML."""
    parts: list[str] = []

    synopsis = data.get("synopsis", "")
    if synopsis:
        parts.append(
            f'<div class="section">'
            f"<h3>故事梗概</h3>"
            f'<div class="kv-value">{_nl2br(synopsis)}</div>'
            f"</div>"
        )

    selection = data.get("element_selection", {})
    if isinstance(selection, dict):
        req = selection.get("required_elements", [])
        ext = selection.get("extension_elements", [])
        summary = selection.get("selector_summary", "")
        if summary or req or ext:
            parts.append("<h2>叙事要素选择</h2>")
            if summary:
                parts.append(f'<div class="hint-block">{_nl2br(str(summary))}</div>')

            def _to_chip(items: Any) -> str:
                chips: list[str] = []
                if isinstance(items, list):
                    for raw in items:
                        if not isinstance(raw, dict):
                            continue
                        name = raw.get("name", "")
                        element_id = raw.get("element_id", "")
                        if not name and not element_id:
                            continue
                        label = str(name or element_id)
                        if element_id:
                            label = f"{label} · {element_id}"
                        chips.append(
                            f'<span class="tag-muted tag" style="margin:0 4px 4px 0;">{_esc(label)}</span>'
                        )
                return "".join(chips)

            if req:
                parts.append(
                    f'<div class="section"><h3>必要项</h3><div>{_to_chip(req)}</div></div>'
                )
            if ext:
                parts.append('<div class="section"><h3>扩展项</h3>')
                for raw in ext:
                    if not isinstance(raw, dict):
                        continue
                    name = raw.get("name", "")
                    reason = raw.get("selection_reason", "")
                    prompt_hint = raw.get("prompt_hint", "")
                    parts.append(
                        '<div class="rule-item">'
                        f"<b>{_esc(str(name))}</b>"
                        + (f"<br>原因：{_esc(str(reason))}" if reason else "")
                        + (
                            f'<br><span style="font-size: 11pt;color:[[nf:text.muted.strong]];">提示词：{_esc(str(prompt_hint))}</span>'
                            if prompt_hint
                            else ""
                        )
                        + "</div>"
                    )
                parts.append("</div>")

    anchor = data.get("anchor_elements", {})
    if anchor and isinstance(anchor, dict):
        anchor_parts: list[str] = []
        time_frame = anchor.get("time_frame", "")
        if time_frame:
            anchor_parts.append(f"<b>⏰ 时间</b>：{_esc(time_frame)}")
        locations = anchor.get("primary_locations", [])
        if locations:
            anchor_parts.append(
                f"<b>📍 场景</b>：{_esc('、'.join(str(location) for location in locations))}"
            )
        characters = anchor.get("core_characters", [])
        if characters:
            char_strs = []
            for char in characters:
                if isinstance(char, dict):
                    name = char.get("name", "")
                    role = char.get("role", "")
                    char_strs.append(f"{_esc(name)}（{_esc(role)}）" if role else _esc(name))
            if char_strs:
                anchor_parts.append(f"<b>👤 人物</b>：{'、'.join(char_strs)}")
        central_event = anchor.get("central_event", "")
        if central_event:
            anchor_parts.append(f"<b>🎯 核心事件</b>：{_esc(central_event)}")
        if anchor_parts:
            parts.append(
                "<h2>⚓ 叙事锚定</h2>"
                '<div class="hint-block">' + "<br>".join(anchor_parts) + "</div>"
            )

    phases = data.get("narrative_phases", [])
    if phases:
        parts.append("<h2>叙事阶段</h2>")
        for phase in phases:
            name = phase.get("phase_name", "")
            start = phase.get("position_start", 0)
            end = phase.get("position_end", 100)
            desc = phase.get("description", "")
            tension = phase.get("tension_level", 0)
            emotional = phase.get("emotional_focus", "")
            time_setting = phase.get("time_setting", "")
            location = phase.get("location", "")
            chars_present = phase.get("characters_present", [])
            key_event = phase.get("key_event", "")
            tension_value = tension if isinstance(tension, (int, float)) else 5
            tension_color = (
                "[[nf:status.success.deep]]" if tension_value <= 3 else "[[nf:accent.rank.label]]" if tension_value <= 6 else "[[nf:accent.primary]]"
            )
            bar_width = max(5, min(int(tension_value) * 10, 100))
            badges: list[str] = []
            if time_setting:
                badges.append(f"⏰ {_esc(time_setting)}")
            if location:
                badges.append(f"📍 {_esc(location)}")
            if chars_present:
                badges.append(f"👤 {_esc('、'.join(str(char) for char in chars_present))}")
            anchor_line = ""
            if badges:
                anchor_line = (
                    '<div style="margin-top:3px;font-size: 11pt;color:[[nf:text.body.alt]];">'
                    + " &nbsp;│&nbsp; ".join(badges)
                    + "</div>"
                )
            event_line = ""
            if key_event:
                event_line = (
                    f'<div style="margin-top:2px;font-size: 11pt;color:[[nf:accent.deep]];">'
                    f"▸ {_esc(key_event)}</div>"
                )
            parts.append(
                f'<div class="rule-item">'
                f"<b>{_esc(name)}</b>"
                f'<span class="tag-muted tag" style="margin-left:8px;">'
                f"{start}%–{end}%</span><br>"
                f"{_nl2br(desc)}<br>"
                f'<span style="font-size: 11pt;color:[[nf:text.muted.strong]];">情感焦点：{_esc(emotional)}</span>'
                f"{anchor_line}{event_line}"
                f'<div style="display:flex;align-items:center;margin-top:4px;">'
                f'<span style="font-size: 11pt;color:[[nf:text.muted.strong]];min-width:36px;">张力</span>'
                f'<div style="background:rgba([[nf:bg.control.hover]], 0.5);border-radius:4px;'
                f'height:6px;flex:1;margin:0 8px;overflow:hidden;">'
                f'<div style="width:{bar_width}%;height:6px;border-radius:4px;'
                f'background:{tension_color};"></div></div>'
                f'<span style="font-size: 11pt;font-weight:700;color:{tension_color};">'
                f"{tension}/10</span></div>"
                f"</div>"
            )

    turning_points = data.get("turning_points", [])
    if turning_points:
        parts.append("<h2>关键转折</h2>")
        for turning_point in turning_points:
            pos = turning_point.get("position_percent", "?")
            desc = turning_point.get("description", "")
            impact = turning_point.get("impact", "")
            parts.append(
                f'<div class="theme-item">'
                f'<span class="tag tag-muted" style="margin-right:6px;">{pos}%</span>'
                f"<b>{_esc(desc)}</b><br>"
                f'<span style="font-size: 12pt;color:[[nf:text.muted.strong]];">影响：{_esc(impact)}</span>'
                f"</div>"
            )

    arcs = data.get("character_arcs", [])
    if arcs:
        parts.append("<h2>角色弧线</h2>")
        for arc in arcs:
            name = arc.get("character", "")
            summary = arc.get("arc_summary", "")
            moment = arc.get("key_moment", "")
            parts.append(
                f'<div class="rule-item">'
                f"<b>{_esc(name)}</b>：{_nl2br(summary)}<br>"
                f'<span style="font-size: 11pt;color:[[nf:text.muted]];">🔑 关键时刻：{_esc(moment)}'
                f"</span></div>"
            )

    emotional_arc = data.get("emotional_arc", "")
    if emotional_arc:
        parts.append(f'<h2>情感走势</h2><div class="hint-block">{_nl2br(emotional_arc)}</div>')

    ending = data.get("ending_strategy", "")
    if ending:
        parts.append(f'<h2>收束策略</h2><div class="hint-block">{_nl2br(ending)}</div>')

    if not parts:
        parts.append(
            '<div style="color:[[nf:text.muted]]; padding:32px; text-align:center;">暂无叙事蓝图数据</div>'
        )

    return _make_browser(_html_wrap("\n".join(parts), "短篇叙事蓝图"))
