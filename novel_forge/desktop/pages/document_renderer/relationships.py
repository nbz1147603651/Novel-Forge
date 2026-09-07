"""Relationship dashboard renderers extracted from the main document facade."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QTextBrowser

from novel_forge.desktop.pages.standalone.renderer_html import base_css as _base_css
from novel_forge.desktop.pages.standalone.renderer_html import esc as _esc
from novel_forge.desktop.pages.standalone.renderer_html import make_browser as _make_browser
from novel_forge.desktop.pages.standalone.renderer_html import rel_metric_bar as _rel_metric_bar
from novel_forge.desktop.pages.standalone.renderer_html import tension_color as _tension_color
from novel_forge.desktop.pages.standalone.renderer_html import trust_color as _trust_color

if TYPE_CHECKING:
    from novel_forge.story_kernel.relationship_tracker import RelationshipOverview

_REL_CSS = """
.pair-card {
    background: rgba([[nf:bg.surface]], 0.8);
    border: 1px solid rgba([[nf:border.default]], 0.15);
    border-radius: 10px;
    padding: 14px 18px;
    margin: 8px 0;
}
.pair-header {
    font-family: "Songti SC", "STSong", "Georgia", serif;
    font-size: 15pt;
    font-weight: 700;
    color: [[nf:text.heading.deep]];
    margin-bottom: 6px;
}
.pair-status {
    display: inline-block;
    background: rgba([[nf:accent.primary]], 0.10);
    color: [[nf:accent.deep]];
    border: 1px solid rgba([[nf:accent.primary]], 0.25);
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 11pt;
    font-weight: 600;
    margin-left: 8px;
}
.metric-row {
    display: flex;
    align-items: center;
    margin: 4px 0;
}
.metric-label {
    color: [[nf:text.muted.strong]];
    font-size: 12pt;
    font-weight: 700;
    min-width: 48px;
}
.metric-bar-bg {
    background: rgba([[nf:bg.control.hover]], 0.5);
    border-radius: 4px;
    height: 6px;
    flex: 1;
    margin: 0 8px;
    overflow: hidden;
}
.metric-bar-fill {
    height: 6px;
    border-radius: 4px;
}
.metric-val {
    font-size: 12pt;
    font-weight: 700;
    min-width: 32px;
    text-align: right;
}
.timeline-row {
    margin: 2px 0;
    padding: 4px 0;
    border-bottom: 1px solid rgba([[nf:border.default]], 0.06);
}
.timeline-ch {
    font-size: 11pt;
    font-weight: 700;
    color: [[nf:text.muted.strong]];
    min-width: 50px;
    display: inline-block;
}
.timeline-event {
    font-size: 12pt;
    color: [[nf:text.tab.hover]];
}
.timeline-status-change {
    font-size: 11pt;
    color: [[nf:accent.primary]];
    font-weight: 600;
}
.stat-hero {
    background: rgba([[nf:accent.primary]], 0.06);
    border: 1px solid rgba([[nf:accent.primary]], 0.15);
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 12px;
    text-align: center;
}
.stat-number {
    font-family: "Songti SC", "STSong", "Georgia", serif;
    font-size: 28pt;
    font-weight: 700;
    color: [[nf:accent.primary]];
}
.stat-label {
    font-size: 12pt;
    color: [[nf:text.muted.strong]];
    margin-top: 2px;
}
.tension-alert {
    background: rgba([[nf:status.danger.deep]], 0.06);
    border-left: 3px solid rgba([[nf:status.danger.deep]], 0.5);
    padding: 8px 14px;
    margin: 6px 0;
    border-radius: 0 6px 6px 0;
    font-size: 12pt;
    color: [[nf:status.danger.dark]];
}
.shift-item {
    background: rgba([[nf:status.success.warm]], 0.06);
    border-left: 3px solid rgba([[nf:status.success.warm]], 0.4);
    padding: 8px 14px;
    margin: 4px 0;
    border-radius: 0 6px 6px 0;
    font-size: 12pt;
}
.filter-hint {
    color: [[nf:text.muted]];
    font-size: 12pt;
    font-style: italic;
    margin: 8px 0;
}
.empty-hint {
    color: [[nf:text.muted]];
    font-size: 13pt;
    text-align: center;
    padding: 40px 20px;
}
"""


def render_relationship_overview(
    overview: "RelationshipOverview",
    *,
    filter_character: str = "",
) -> QTextBrowser:
    """Render a full relationship overview as a rich HTML dashboard."""
    timelines = overview.timelines
    if filter_character:
        lowered = filter_character.lower()
        timelines = [
            timeline
            for timeline in timelines
            if timeline.character_a.lower() == lowered or timeline.character_b.lower() == lowered
        ]

    parts: list[str] = ['<div style="display:flex; gap:12px; flex-wrap:wrap;">']
    parts.append(
        f'<div class="stat-hero" style="flex:1; min-width:120px;"><div class="stat-number">{overview.total_relationships}</div><div class="stat-label">追踪关系对</div></div>'
    )
    parts.append(
        f'<div class="stat-hero" style="flex:1; min-width:120px;"><div class="stat-number">{len(overview.high_tension_pairs)}</div><div class="stat-label">高张力关系</div></div>'
    )
    parts.append(
        f'<div class="stat-hero" style="flex:1; min-width:120px;"><div class="stat-number">{len(overview.recent_shifts)}</div><div class="stat-label">近期变动</div></div>'
    )
    parts.append("</div>")

    if overview.high_tension_pairs:
        parts.append("<h2>⚡ 高张力关系</h2>")
        for pair_id in overview.high_tension_pairs:
            timeline = next((item for item in overview.timelines if item.pair_id == pair_id), None)
            if timeline is not None:
                parts.append(
                    f'<div class="tension-alert"><b>{_esc(timeline.character_a)} — {_esc(timeline.character_b)}</b> '
                    f'张力 {timeline.current_tension:.0%}　状态：{_esc(timeline.current_status or "未知")}</div>'
                )

    if overview.recent_shifts:
        parts.append("<h2>📋 近期关系变动</h2>")
        for shift in overview.recent_shifts:
            chars = shift.get("characters", [])
            char_str = " — ".join(_esc(character) for character in chars) if chars else _esc(shift.get("pair_id", ""))
            parts.append(
                f'<div class="shift-item"><b>{char_str}</b>　第{shift.get("chapter", "?")}章<br>{_esc(shift.get("event", ""))}</div>'
            )

    if not timelines:
        parts.append('<div class="empty-hint">暂无追踪的人物关系。<br>完成章节创作后，角色之间的关系变化将在此展示。</div>')
    else:
        if filter_character:
            parts.append(f'<p class="filter-hint">筛选：与「{_esc(filter_character)}」相关的关系</p>')
        parts.append(f"<h2>关系演化（{len(timelines)} 对）</h2>")
        for timeline in timelines:
            parts.append('<div class="pair-card">')
            parts.append(
                f'<div class="pair-header">{_esc(timeline.character_a)} ↔ {_esc(timeline.character_b)}'
                f'<span class="pair-status">{_esc(timeline.current_status or "未定义")}</span></div>'
            )
            parts.append(_rel_metric_bar("信任", timeline.current_trust, _trust_color(timeline.current_trust)))
            parts.append(_rel_metric_bar("张力", timeline.current_tension, _tension_color(timeline.current_tension)))
            if timeline.snapshots:
                parts.append('<div style="margin-top:8px;">')
                parts.append('<div style="font-size: 11pt; color:[[nf:text.muted.strong]]; font-weight:700; margin-bottom:4px;">演变轨迹</div>')
                previous_status = ""
                for snap in timeline.snapshots:
                    event_html = _esc(snap.shift_event) if snap.shift_event else "<i style='color:[[nf:text.disabled]];'>—</i>"
                    status_html = ""
                    if snap.public_status and snap.public_status != previous_status:
                        status_html = f' <span class="timeline-status-change">→ {_esc(snap.public_status)}</span>'
                        previous_status = snap.public_status
                    parts.append(
                        f'<div class="timeline-row"><span class="timeline-ch">第{snap.chapter_number}章</span>'
                        f'<span class="timeline-event">{event_html}</span>{status_html}</div>'
                    )
                parts.append("</div>")
            parts.append("</div>")

    html = (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        f"<style>{_base_css()}{_REL_CSS}</style></head><body><h1>角色关系追踪</h1>"
        f"{''.join(parts)}</body></html>"
    )
    return _make_browser(html)


def render_relationship_compact(
    overview: "RelationshipOverview",
    current_chapter: int,
    chapter_context_texts: list[str] | None = None,
    focus_characters: list[str] | None = None,
) -> str:
    """Render a compact HTML snippet for the chapter studio inspector card.

    Shows only relationships relevant to the current chapter:
    1. Relationships with changes at the current chapter (highest priority)
    2. Relationships with recent changes (within 2 chapters)
    3. If no explicit changes exist, show chapter-related ongoing states
       so the card remains informative.
    """
    _NEARBY_WINDOW = 2
    _MAX_NEARBY_ITEMS = 4
    _MAX_RELATED_ITEMS = 4
    _MAX_CURRENT_ITEMS = 5

    def _normalize(text: str) -> str:
        return "".join(str(text or "").split()).lower()

    context_blob = _normalize(" ".join(chapter_context_texts or []))
    context_matched_pairs: set[str] = set()
    if context_blob:
        for timeline in overview.timelines:
            char_a = (timeline.character_a or "").strip()
            char_b = (timeline.character_b or "").strip()
            names = [char_a, char_b]
            # Avoid excessive false positives from one-character names in Chinese text.
            if any(name and len(name) >= 2 and _normalize(name) in context_blob for name in names):
                context_matched_pairs.add(timeline.pair_id)

    focus_norm = {
        _normalize(name)
        for name in (focus_characters or [])
        if str(name or "").strip()
    }
    focus_matched_pairs: set[str] = set()
    if focus_norm:
        for timeline in overview.timelines:
            char_a_norm = _normalize(timeline.character_a)
            char_b_norm = _normalize(timeline.character_b)
            if char_a_norm in focus_norm or char_b_norm in focus_norm:
                focus_matched_pairs.add(timeline.pair_id)

    # 1. Exact current-chapter changes
    current_changes_raw: list[tuple[bool, str]] = []  # (is_focus, html)
    current_change_pairs: set[str] = set()
    for timeline in overview.timelines:
        snapshots = sorted(timeline.snapshots, key=lambda item: item.chapter_number)
        for idx, snap in enumerate(snapshots):
            if snap.chapter_number == current_chapter:
                previous = snapshots[idx - 1] if idx > 0 else None
                has_change_signal = bool((snap.shift_event or "").strip())
                if previous is not None and not has_change_signal:
                    has_change_signal = (
                        (snap.public_status or "").strip() != (previous.public_status or "").strip()
                        or abs(float(snap.trust) - float(previous.trust)) > 0.001
                        or abs(float(snap.tension) - float(previous.tension)) > 0.001
                    )
                if previous is None and not has_change_signal:
                    has_change_signal = (
                        bool((snap.public_status or "").strip())
                        or abs(float(snap.trust) - 0.5) > 0.001
                        or abs(float(snap.tension) - 0.5) > 0.001
                    )
                if not has_change_signal:
                    break

                label = f"{_esc(timeline.character_a)}↔{_esc(timeline.character_b)}"
                metrics = f"信任{snap.trust:.0%} · 张力{snap.tension:.0%}"
                desc_bits: list[str] = []
                if snap.public_status:
                    desc_bits.append(_esc(snap.public_status))
                if snap.shift_event:
                    desc_bits.append(_esc(snap.shift_event))
                desc_html = (
                    '<div style="font-size: 11pt; color:[[nf:text.guidance]]; margin-top:2px; line-height:1.4;">'
                    + "，".join(desc_bits)
                    + "</div>"
                ) if desc_bits else ""
                is_focus = timeline.pair_id in focus_matched_pairs
                current_changes_raw.append((
                    is_focus,
                    f'<b>{label}</b>'
                    f'<span style="font-size: 11pt; color:[[nf:text.muted]]; font-weight:400;">　{metrics}</span>'
                    + desc_html
                ))
                current_change_pairs.add(timeline.pair_id)
                break

    # Sort: focus (main) characters first, then cap to _MAX_CURRENT_ITEMS
    current_changes_raw.sort(key=lambda x: (not x[0],))
    current_changes_total = len(current_changes_raw)
    current_changes = [html for _, html in current_changes_raw[:_MAX_CURRENT_ITEMS]]

    # 2. Recent nearby changes (within _NEARBY_WINDOW chapters, excluding exact)
    nearby_changes: list[str] = []
    for timeline in overview.timelines:
        # Skip if already shown in current changes
        if timeline.pair_id in current_change_pairs:
            continue
        snapshots = sorted(timeline.snapshots, key=lambda item: item.chapter_number)
        for snap in snapshots:
            if (
                snap.shift_event
                and abs(snap.chapter_number - current_chapter) <= _NEARBY_WINDOW
                and snap.chapter_number != current_chapter
            ):
                label = f"{_esc(timeline.character_a)}↔{_esc(timeline.character_b)}"
                status_text = _esc(snap.public_status or timeline.current_status or "")
                if len(status_text) > 30:
                    status_text = status_text[:28] + "…"
                event_text = _esc(snap.shift_event)
                if len(event_text) > 30:
                    event_text = event_text[:28] + "…"
                nearby_changes.append(
                    f"<b>{label}</b>：{status_text}"
                    f'<span style="color:[[nf:text.muted.strong]];font-size: 11pt;">（第{snap.chapter_number}章 {event_text}）</span>'
                )
                break

    parts: list[str] = []
    if current_changes:
        parts.append(
            '<div style="font-size: 11pt;color:[[nf:text.muted.strong]];font-weight:700;margin-bottom:4px;">本章关系变化</div>'
        )
        parts.extend(
            f'<div style="margin:3px 0; font-size: 12pt; line-height:1.5; color:[[nf:text.artifact]];">{c}</div>'
            for c in current_changes
        )
        if current_changes_total > _MAX_CURRENT_ITEMS:
            hidden = current_changes_total - _MAX_CURRENT_ITEMS
            parts.append(
                f'<div style="font-size: 11pt; color:[[nf:text.muted]]; margin-top:2px;">另有 {hidden} 对关系变化，点击「查看全局关系」了解详情</div>'
            )

    if nearby_changes:
        if current_changes:
            parts.append('<div style="margin-top:6px;"></div>')
        parts.append(
            '<div style="font-size: 11pt;color:[[nf:text.muted.strong]];font-weight:700;margin-bottom:4px;">近期关系动态</div>'
        )
        parts.extend(
            f'<div style="margin:2px 0; font-size: 12pt; line-height:1.5; color:[[nf:text.artifact]];">{c}</div>'
            for c in nearby_changes[:_MAX_NEARBY_ITEMS]
        )

    if parts:
        total = overview.total_relationships
        remaining = total - current_changes_total - min(len(nearby_changes), _MAX_NEARBY_ITEMS)
        if remaining > 0:
            parts.append(
                f'<div style="font-size: 11pt; color:[[nf:text.muted]]; margin-top:4px;">'
                f'共追踪 {total} 对关系，点击「查看全局关系」了解更多</div>'
            )
        return "".join(parts)

    # 3. No explicit chapter deltas: show chapter-related ongoing relationships.
    related_lines: list[tuple[tuple[int, float, str], str]] = []
    candidate_timelines = overview.timelines
    title = "本章相关关系（状态延续）"
    summary_prefix = ""
    if focus_matched_pairs:
        candidate_timelines = [timeline for timeline in overview.timelines if timeline.pair_id in focus_matched_pairs]
        title = "本章相关关系（按本章计划角色）"
        summary_prefix = f"已匹配 {len(focus_matched_pairs)} 对计划角色关系；"
    elif context_matched_pairs:
        candidate_timelines = [timeline for timeline in overview.timelines if timeline.pair_id in context_matched_pairs]
        title = "本章相关关系（按本章上下文匹配）"
        summary_prefix = f"已匹配 {len(context_matched_pairs)} 对本章相关关系；"
    for timeline in candidate_timelines:
        snapshots = sorted(timeline.snapshots, key=lambda item: item.chapter_number)
        anchor = None
        for snap in snapshots:
            if snap.chapter_number <= current_chapter:
                anchor = snap
            else:
                break
        if anchor is None and snapshots:
            anchor = snapshots[-1]

        status_text = ""
        trust = float(timeline.current_trust)
        tension = float(timeline.current_tension)
        anchor_chapter = 0
        if anchor is not None:
            status_text = (anchor.public_status or "").strip()
            trust = float(anchor.trust)
            tension = float(anchor.tension)
            anchor_chapter = int(anchor.chapter_number)
        if not status_text:
            status_text = (timeline.current_status or "").strip() or "关系延续"

        gap = abs(current_chapter - anchor_chapter) if anchor_chapter > 0 else 999
        pair_label = f"{_esc(timeline.character_a)}↔{_esc(timeline.character_b)}"
        chapter_hint = (
            f'<span style="color:[[nf:text.muted.strong]];font-size: 11pt;">（最近状态：第{anchor_chapter}章）</span>'
            if anchor_chapter > 0
            else ""
        )
        metrics_inline = f"信任{trust:.0%} · 张力{tension:.0%}"
        desc_html_line = (
            '<div style="font-size: 11pt; color:[[nf:text.guidance]]; margin-top:2px; line-height:1.4;">'
            + _esc(status_text)
            + (f"　{chapter_hint}" if chapter_hint else "")
            + "</div>"
        )
        line = (
            f'<b>{pair_label}</b>'
            f'<span style="font-size: 11pt; color:[[nf:text.muted]]; font-weight:400;">　{metrics_inline}</span>'
            + desc_html_line
        )
        # Sort by chapter proximity first, then by higher tension, then by pair id.
        related_lines.append(((gap, -tension, timeline.pair_id), line))

    if related_lines:
        related_lines.sort(key=lambda item: item[0])
        body = "".join(
            f'<div style="margin:2px 0; font-size: 12pt; line-height:1.5; color:[[nf:text.artifact]];">{line}</div>'
            for _, line in related_lines[:_MAX_RELATED_ITEMS]
        )
        return (
            '<div style="font-size: 11pt;color:[[nf:text.muted.strong]];font-weight:700;margin-bottom:4px;">'
            + title
            + "</div>"
            + body
            + (
                '<div style="font-size: 11pt; color:[[nf:text.muted]]; margin-top:4px;">'
                + summary_prefix
                + f'共追踪 {overview.total_relationships} 对关系，点击「查看全局关系」了解更多</div>'
            )
        )

    # Fallback: no chapter-relevant relationships, show brief summary.
    total = overview.total_relationships
    if total > 0:
        return (
            f'<span style="color:[[nf:text.muted]]; font-size: 12pt;">'
            f'共追踪 {total} 对角色关系，本章暂无可展示的关系详情。'
            f'点击「查看全局关系」了解详情。</span>'
        )

    return '<span style="color:[[nf:text.muted]]; font-size: 12pt;">暂未追踪到角色关系。</span>'
