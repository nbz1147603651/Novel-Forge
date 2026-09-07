"""Sub-module of novel_forge.desktop.pages.document_renderer.story_artifacts.

Created in P2f. Contains ``render_narrative_blueprint_fragments``, the
fragmented long-form blueprint view renderer.

The 8 ``_fragment_*`` helpers that it depends on (and that several other
not-yet-migrated renderers in the parent ``__init__.py`` also use) are
left in the parent ``__init__.py`` for now — this submodule imports them
from there.

Cross-package dependency: ``generic_key_label`` is imported from
``document_renderer_reports``.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QTextBrowser

# Imported from the parent package so the helper-shared-by-other-renderers
# coupling stays in one place. Future P2 iterations can move the
# ``_fragment_*`` helpers here once their co-renderers (init_coherence_*,
# init_conflict_*, init_claim_contract_coverage, reading_power_*)
# have been migrated into their own submodules.
from novel_forge.desktop.pages.document_renderer.story_artifacts._common import (
    _fragment_badge,
    _fragment_chapter_span,
    _fragment_count_row,
    _fragment_join,
    _fragment_table,
    _fragment_text,
)
from novel_forge.desktop.pages.standalone.renderer_html import esc as _esc
from novel_forge.desktop.pages.standalone.renderer_html import html_wrap as _html_wrap
from novel_forge.desktop.pages.standalone.renderer_html import make_browser as _make_browser
from novel_forge.desktop.pages.standalone.renderer_html import nl2br as _nl2br

JsonDict = dict[str, Any]

__all__ = ["render_narrative_blueprint_fragments"]


def render_narrative_blueprint_fragments(data: JsonDict) -> QTextBrowser:
    """Render fragmented long-form blueprint cache as a scan-friendly artifact."""
    parts: list[str] = []

    synopsis = _fragment_text(data.get("synopsis"))
    assembly_mode = _fragment_text(data.get("assembly_mode"))
    volume_mode = bool(data.get("volume_mode", False))
    volumes = data.get("volumes", [])
    phases = data.get("narrative_phases", [])
    turning_points = data.get("key_turning_points", [])
    character_arcs = data.get("character_arcs", [])
    subplots = data.get("subplot_plan", [])
    suspense = data.get("suspense_schedule", [])
    ending = _fragment_text(data.get("ending_strategy"))

    meta: list[str] = []
    if assembly_mode:
        meta.append(_fragment_badge(f"组装：{assembly_mode}"))
    meta.append(_fragment_badge(f"分卷模式：{'开启' if volume_mode else '关闭'}"))
    parts.append(
        '<div style="margin-bottom:10px;">'
        + "".join(f'<span style="margin-right:4px;">{item}</span>' for item in meta)
        + "</div>"
    )

    count_cells = [
        _fragment_count_row("分卷", volumes),
        _fragment_count_row("阶段", phases),
        _fragment_count_row("转折", turning_points),
        _fragment_count_row("角色弧", character_arcs),
        _fragment_count_row("支线", subplots),
        _fragment_count_row("悬念", suspense),
    ]
    parts.append(
        '<table style="margin-bottom:12px;"><tbody>'
        + "<tr>"
        + "".join(count_cells[:3])
        + "</tr><tr>"
        + "".join(count_cells[3:])
        + "</tr></tbody></table>"
    )

    if synopsis:
        parts.append(
            '<div class="section"><h3>全书概要</h3>'
            f'<div class="kv-value">{_nl2br(synopsis)}</div></div>'
        )

    if isinstance(volumes, list) and volumes:
        rows = []
        for volume in volumes:
            if not isinstance(volume, dict):
                continue
            title = _fragment_text(volume.get("title") or volume.get("name") or "分卷")
            rows.append(
                [
                    _esc(_fragment_text(volume.get("volume_num") or volume.get("volume") or "")),
                    _esc(title),
                    _esc(_fragment_chapter_span(volume)),
                    _nl2br(_fragment_text(volume.get("arc_goal") or volume.get("goal"))),
                    _esc(_fragment_join(volume.get("milestone_targets"), limit=4)),
                ]
            )
        if rows:
            parts.append("<h2>分卷规划</h2>")
            parts.append(_fragment_table(["卷", "标题", "章节", "目标", "里程碑"], rows))

    if isinstance(phases, list) and phases:
        rows = []
        for phase in phases:
            if not isinstance(phase, dict):
                continue
            rows.append(
                [
                    _esc(_fragment_text(phase.get("phase_name") or phase.get("title") or "阶段")),
                    _esc(_fragment_chapter_span(phase)),
                    _esc(_fragment_text(phase.get("tension_level") or phase.get("tension"))),
                    _nl2br(_fragment_text(phase.get("description") or phase.get("goal"))),
                    _esc(_fragment_join(phase.get("key_events"), limit=5)),
                ]
            )
        if rows:
            parts.append("<h2>叙事阶段</h2>")
            parts.append(_fragment_table(["阶段", "章节", "张力", "说明", "关键事件"], rows))

    if isinstance(turning_points, list) and turning_points:
        rows = []
        for point in turning_points:
            if not isinstance(point, dict):
                continue
            title = _fragment_text(point.get("title") or point.get("name") or "关键转折")
            before_after = " → ".join(
                item
                for item in (
                    _fragment_text(point.get("state_before")),
                    _fragment_text(point.get("state_after")),
                )
                if item
            )
            rows.append(
                [
                    _esc(_fragment_chapter_span(point)),
                    _esc(title),
                    _nl2br(_fragment_text(point.get("description") or point.get("event"))),
                    _esc(before_after or "—"),
                ]
            )
        if rows:
            parts.append("<h2>关键转折</h2>")
            parts.append(_fragment_table(["章节", "节点", "变化", "状态前后"], rows))

    if isinstance(character_arcs, list) and character_arcs:
        parts.append("<h2>角色弧光</h2>")
        for arc in character_arcs:
            if not isinstance(arc, dict):
                continue
            name = _fragment_text(arc.get("character") or arc.get("name") or "角色")
            summary = _fragment_text(arc.get("arc_summary") or arc.get("summary"))
            milestones = _fragment_join(arc.get("milestones"), limit=5)
            body = f"<b>{_esc(name)}</b>"
            if summary:
                body += f"：{_nl2br(summary)}"
            if milestones != "—":
                body += f'<br><span style="color:[[nf:text.muted.strong]];">里程碑：{_esc(milestones)}</span>'
            parts.append(f'<div class="rule-item">{body}</div>')

    if isinstance(subplots, list) and subplots:
        rows = []
        for subplot in subplots:
            if not isinstance(subplot, dict):
                continue
            resolution = []
            if subplot.get("resolution_chapter"):
                resolution.append(f"Ch.{subplot.get('resolution_chapter')}")
            if subplot.get("resolution_target"):
                resolution.append(_fragment_text(subplot.get("resolution_target")))
            rows.append(
                [
                    _esc(_fragment_text(subplot.get("name") or "支线")),
                    _esc(_fragment_chapter_span(subplot)),
                    _esc(_fragment_text(subplot.get("priority") or "normal")),
                    _nl2br(_fragment_text(subplot.get("description"))),
                    _esc(" / ".join(resolution) or "—"),
                    _esc(_fragment_join(subplot.get("chapter_events"), limit=4)),
                ]
            )
        if rows:
            parts.append("<h2>支线计划</h2>")
            parts.append(
                _fragment_table(["名称", "章节", "优先级", "说明", "收束", "节点事件"], rows)
            )

    if isinstance(suspense, list) and suspense:
        rows = []
        for item in suspense:
            if not isinstance(item, dict):
                continue
            title = _fragment_text(
                item.get("title")
                or item.get("suspense_id")
                or item.get("mystery")
                or item.get("question")
                or "悬念"
            )
            rows.append(
                [
                    _esc(title),
                    _esc(_fragment_text(item.get("suspense_type") or item.get("type"))),
                    _esc(
                        _fragment_text(item.get("introduce_chapter") or item.get("setup_chapter"))
                    ),
                    _esc(_fragment_text(item.get("resolve_chapter") or item.get("payoff_chapter"))),
                    _nl2br(_fragment_text(item.get("description") or item.get("payoff"))),
                ]
            )
        if rows:
            parts.append("<h2>悬念规划</h2>")
            parts.append(_fragment_table(["悬念", "类型", "提出", "回收", "说明"], rows))

    if ending:
        parts.append(f'<h2>收束策略</h2><div class="hint-block">{_nl2br(ending)}</div>')

    if not parts:
        parts.append('<div class="hint-block">暂无叙事蓝图分块数据。</div>')

    return _make_browser(_html_wrap("\n".join(parts), "叙事蓝图分块"))
