"""Sub-module of novel_forge.desktop.pages.document_renderer.

Auto-generated in the M3.2 split. Contains subplot.py renderers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtWidgets import (
    QTabWidget,
    QTextBrowser,
    QWidget,
)

# Late imports — placed after stdlib imports to avoid circular imports
# between sibling sub-modules.
from novel_forge.desktop.pages.document_renderer._utils import (
    load_json_dict as _load_json_dict,
)
from novel_forge.desktop.pages.standalone.renderer_html import (
    esc as _esc,
)
from novel_forge.desktop.pages.standalone.renderer_html import (
    html_wrap as _html_wrap,
)
from novel_forge.desktop.pages.standalone.renderer_html import (
    make_browser as _make_browser,
)

JsonDict = dict[str, Any]
FeedbackMarker = tuple[float, float, float, JsonDict, JsonDict]
WeaveLine = tuple[float, float, float, JsonDict, JsonDict]
DocumentRenderer = Callable[[dict[str, Any]], QWidget]


# Link type labels and strong-feedback markers are defined in
# narrative_blueprint. These local copies duplicate the values used by
# subplot.py's helpers so we avoid the circular import
# (narrative_blueprint → subplot → narrative_blueprint).
_LINK_TYPE_LABELS: dict[str, str] = {
    "trigger_start": "触发启动",
    "trigger_turn": "触发转折",
    "constrain": "约束走向",
    "enable": "提供条件",
    "conflict": "制造冲突",
    "feed_main": "反哺主线",
    "reveal_key": "揭露关键",
    "create_tension": "制造张力",
    "theme_echo": "呼应主题",
}

_STRONG_FEEDBACK_LINK_TYPES: set[str] = {"feed_main", "reveal_key"}

_SUBPLOT_PRIORITY_LABELS: dict[str, str] = {
    "primary": "准主线",
    "normal": "常规",
    "background": "背景",
}

_SUBPLOT_STATUS_LABELS: dict[str, str] = {
    "planned_closed_loop": "计划已闭环",
    "planned_open_loop": "计划未闭环",
    "not_started": "未启动",
    "active": "推进中",
    "dormant": "暂伏",
    "resolved": "已收束",
    "needs_closure": "待收束",
    "overdue": "逾期未收",
    "unplanned": "未规划",
}

_RESOLUTION_TARGET_LABELS: dict[str, str] = {
    "main_turning_point": "主线转折",
    "theme_echo": "主题回响",
    "character_fate": "角色命运",
}

_RESOLUTION_TYPE_LABELS: dict[str, str] = {
    "resolve": "解决",
    "reveal": "揭露",
    "ascend": "升华",
    "merge": "合并",
}


def _as_positive_int(value: Any) -> int:
    """Convert value to a non-negative int (duplicate to avoid circular import with narrative_blueprint)."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _format_chapter_list(chapters: Any) -> str:
    if not isinstance(chapters, list):
        return "未配置"
    values = [_as_positive_int(chapter) for chapter in chapters]
    values = [chapter for chapter in values if chapter > 0]
    if not values:
        return "未配置"
    return "、".join(f"Ch.{chapter}" for chapter in values)


def _raw_subplot_status(
    *,
    planned_chapters: list[int],
    current_chapter: int,
    resolution_chapter: int,
    closed_loop: bool,
) -> str:
    if current_chapter <= 0:
        return "planned_closed_loop" if closed_loop else "planned_open_loop"
    if not planned_chapters:
        return "unplanned"
    first = planned_chapters[0]
    last = planned_chapters[-1]
    if current_chapter < first:
        return "not_started"
    if current_chapter in planned_chapters:
        return "active"
    if resolution_chapter and current_chapter >= resolution_chapter:
        return "resolved" if closed_loop else "needs_closure"
    if current_chapter <= last:
        return "dormant"
    return "overdue"


def _raw_subplot_execution_matrix(data: JsonDict, *, current_chapter: int = 0) -> JsonDict:
    rows: list[JsonDict] = []
    subplots = data.get("subplot_plan", [])
    if not isinstance(subplots, list):
        subplots = []

    for subplot in subplots:
        if not isinstance(subplot, dict):
            continue
        planned_chapters = sorted(
            {
                chapter
                for chapter in (
                    _as_positive_int(item) for item in subplot.get("involved_chapters", [])
                )
                if chapter > 0
            }
        )
        future_chapters = [chapter for chapter in planned_chapters if chapter >= current_chapter]
        next_planned = future_chapters[0] if current_chapter > 0 and future_chapters else 0
        resolution_chapter = _as_positive_int(subplot.get("resolution_chapter"))
        trigger_links: list[JsonDict] = []
        feedback_links: list[JsonDict] = []
        cross_subplot_links: list[JsonDict] = []

        links = subplot.get("weave_links", [])
        if not isinstance(links, list):
            links = []
        name = str(subplot.get("name") or "未命名支线")
        for link in links:
            if not isinstance(link, dict):
                continue
            payload = {
                "source_type": str(link.get("source_type") or ""),
                "source_ref": str(link.get("source_ref") or ""),
                "target_subplot": str(link.get("target_subplot") or ""),
                "trigger_chapter": _as_positive_int(link.get("trigger_chapter")),
                "link_type": str(link.get("link_type") or ""),
                "description": str(link.get("description") or ""),
            }
            if payload["source_type"] == "main_plot" and payload["link_type"] in {
                "trigger_start",
                "trigger_turn",
            }:
                trigger_links.append(payload)
            if payload["link_type"] in _STRONG_FEEDBACK_LINK_TYPES:
                feedback_links.append(payload)
            target = str(payload.get("target_subplot") or "")
            if payload["source_type"] == "subplot" or (target and target not in {"主线", name}):
                cross_subplot_links.append(payload)

        notes: list[str] = []
        closed_loop = bool(trigger_links and feedback_links and resolution_chapter > 0)
        if not trigger_links:
            notes.append("缺少主线触发")
        if not feedback_links:
            notes.append("缺少反哺主线")
        if resolution_chapter <= 0:
            notes.append("缺少收束章节")

        rows.append(
            {
                "name": name,
                "priority": str(subplot.get("priority") or "normal"),
                "status": _raw_subplot_status(
                    planned_chapters=planned_chapters,
                    current_chapter=current_chapter,
                    resolution_chapter=resolution_chapter,
                    closed_loop=closed_loop,
                ),
                "planned_chapters": planned_chapters,
                "next_planned_chapter": next_planned,
                "resolution_chapter": resolution_chapter,
                "resolution_target": str(subplot.get("resolution_target") or ""),
                "resolution_type": str(subplot.get("resolution_type") or ""),
                "trigger_links": trigger_links,
                "feedback_links": feedback_links,
                "cross_subplot_links": cross_subplot_links,
                "closed_loop": closed_loop,
                "notes": notes,
            }
        )

    closed = sum(1 for row in rows if row.get("closed_loop"))
    ratio = round(closed / len(rows), 3) if rows else 1.0
    return {"current_chapter": current_chapter, "closed_loop_ratio": ratio, "rows": rows}


def _subplot_matrix_from_blueprint(
    data: JsonDict,
    *,
    project_path: Path | None = None,
) -> JsonDict:
    stored: JsonDict | None = None
    if project_path is not None:
        stored = _load_json_dict(project_path / "plans" / "subplot_execution_matrix.json")
    current_chapter = _as_positive_int(stored.get("current_chapter") if stored else 0)

    try:
        from novel_forge.core.schemas.outline import NarrativeBlueprint
        from novel_forge.pipeline.long.services.weave_validation import (
            build_subplot_execution_matrix,
        )

        blueprint = NarrativeBlueprint.model_validate(data)
        return build_subplot_execution_matrix(
            blueprint,
            current_chapter=current_chapter,
        ).to_dict()
    except (ImportError, ValueError, TypeError, AttributeError):
        if isinstance(data.get("subplot_plan"), list):
            return _raw_subplot_execution_matrix(data, current_chapter=current_chapter)
        return stored or {"current_chapter": current_chapter, "closed_loop_ratio": 1.0, "rows": []}


def _format_matrix_link_list(links: Any) -> str:
    if not isinstance(links, list) or not links:
        return "未配置"
    items: list[str] = []
    for link in links[:4]:
        if not isinstance(link, dict):
            continue
        chapter = _as_positive_int(link.get("trigger_chapter"))
        chapter_text = f"Ch.{chapter} " if chapter > 0 else ""
        link_type = str(link.get("link_type") or "")
        type_label = _LINK_TYPE_LABELS.get(link_type, link_type or "交织")
        target = str(link.get("target_subplot") or "")
        description = str(link.get("description") or "").strip()
        tail = f" → {target}" if target else ""
        if description:
            tail += f"：{description}"
        items.append(f"{chapter_text}{type_label}{tail}".strip())
    if len(links) > 4:
        items.append(f"另 {len(links) - 4} 条")
    return "；".join(_esc(item) for item in items if item) or "未配置"


def render_subplot_execution_matrix(data: JsonDict) -> QTextBrowser:
    """Render subplot activation, feedback and closure plans as a compact matrix."""
    rows = data.get("rows", [])
    if not isinstance(rows, list):
        rows = []
    ratio = data.get("closed_loop_ratio", 1.0)
    try:
        closed_percent = int(round(float(ratio) * 100))
    except (TypeError, ValueError):
        closed_percent = 0
    current_chapter = _as_positive_int(data.get("current_chapter"))

    parts: list[str] = [
        "<h2>支线执行矩阵</h2>",
        '<div class="hint-block">'
        f"闭环率 {closed_percent}% · 支线 {len(rows)} 条"
        + (f" · 当前第 {current_chapter} 章" if current_chapter else "")
        + "</div>",
    ]

    if not rows:
        parts.append('<div class="empty-state">暂无支线执行矩阵。</div>')
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "未命名支线")
        priority = str(row.get("priority") or "normal")
        priority_label = _SUBPLOT_PRIORITY_LABELS.get(priority, priority)
        status = str(row.get("status") or "")
        status_label = _SUBPLOT_STATUS_LABELS.get(status, status or "未标记")
        closed_loop = bool(row.get("closed_loop"))
        closed_label = "已闭环" if closed_loop else "未闭环"
        closed_color = "[[nf:role.supporting]]" if closed_loop else "[[nf:accent.primary]]"

        resolution_chapter = _as_positive_int(row.get("resolution_chapter"))
        resolution_target = str(row.get("resolution_target") or "")
        resolution_type = str(row.get("resolution_type") or "")
        resolution_bits: list[str] = []
        if resolution_chapter:
            resolution_bits.append(f"Ch.{resolution_chapter}")
        if resolution_target:
            resolution_bits.append(
                _RESOLUTION_TARGET_LABELS.get(resolution_target, resolution_target)
            )
        if resolution_type:
            resolution_bits.append(_RESOLUTION_TYPE_LABELS.get(resolution_type, resolution_type))
        resolution_text = " / ".join(_esc(bit) for bit in resolution_bits) or "未配置"

        notes = row.get("notes", [])
        if isinstance(notes, list) and notes:
            notes_html = (
                '<div style="margin-top:6px;color:[[nf:accent.primary]];font-size: 12pt;">待补强：'
                + _esc("；".join(str(note) for note in notes if str(note).strip()))
                + "</div>"
            )
        else:
            notes_html = (
                '<div style="margin-top:6px;color:[[nf:role.supporting]];font-size: 12pt;">结构闭环完整。</div>'
            )

        planned_text = _format_chapter_list(row.get("planned_chapters"))
        next_chapter = _as_positive_int(row.get("next_planned_chapter"))
        next_text = f"Ch.{next_chapter}" if next_chapter else "未进入执行态"
        trigger_text = _format_matrix_link_list(row.get("trigger_links"))
        feedback_text = _format_matrix_link_list(row.get("feedback_links"))
        cross_text = _format_matrix_link_list(row.get("cross_subplot_links"))

        parts.append(
            '<div class="theme-item">'
            f'<div style="font-weight:700;color:[[nf:text.heading.deep]];">{_esc(name)}'
            f'<span class="tag-muted tag" style="margin-left:8px;">{_esc(priority_label)}</span>'
            f'<span class="tag-muted tag" style="margin-left:4px;">{_esc(status_label)}</span>'
            f'<span class="tag" style="margin-left:4px;color:{closed_color};">'
            f"{_esc(closed_label)}</span></div>"
            f'<div class="kv-row"><span class="kv-label">计划章节：</span>'
            f'<span class="kv-value">{_esc(planned_text)}</span></div>'
            f'<div class="kv-row"><span class="kv-label">下一节点：</span>'
            f'<span class="kv-value">{_esc(next_text)}</span></div>'
            f'<div class="kv-row"><span class="kv-label">收束设计：</span>'
            f'<span class="kv-value">{resolution_text}</span></div>'
            f'<div class="kv-row"><span class="kv-label">主线触发：</span>'
            f'<span class="kv-value">{trigger_text}</span></div>'
            f'<div class="kv-row"><span class="kv-label">反哺主线：</span>'
            f'<span class="kv-value">{feedback_text}</span></div>'
            f'<div class="kv-row"><span class="kv-label">横向交织：</span>'
            f'<span class="kv-value">{cross_text}</span></div>'
            f"{notes_html}"
            "</div>"
        )

    browser = _make_browser(_html_wrap("\n".join(parts), "支线执行矩阵"))
    browser.setObjectName("subplotExecutionMatrixTab")
    return browser


def _add_or_replace_subplot_matrix_tab(
    tabs: QTabWidget,
    data: JsonDict,
    project_path: Path | None,
) -> None:
    matrix_data = _subplot_matrix_from_blueprint(data, project_path=project_path)
    new_widget = render_subplot_execution_matrix(matrix_data)

    existing_idx = -1
    subplot_manager_idx = -1
    for idx in range(tabs.count()):
        widget = tabs.widget(idx)
        if widget is None:
            continue
        if widget.objectName() == "subplotExecutionMatrixTab":
            existing_idx = idx
        if tabs.tabText(idx) == "支线管理":
            subplot_manager_idx = idx

    if existing_idx >= 0:
        old_widget = tabs.widget(existing_idx)
        assert old_widget is not None
        tabs.removeTab(existing_idx)
        old_widget.deleteLater()
        tabs.insertTab(existing_idx, new_widget, "支线矩阵")
        return

    insert_idx = subplot_manager_idx if subplot_manager_idx >= 0 else tabs.count()
    tabs.insertTab(insert_idx, new_widget, "支线矩阵")

