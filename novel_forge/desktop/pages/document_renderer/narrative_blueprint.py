"""Sub-module of novel_forge.desktop.pages.document_renderer.

Auto-generated in the M3.2 split. Contains narrative_blueprint.py renderers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QHideEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.pages.document_renderer.incremental import update_browser_html

# Late imports — placed after stdlib imports to avoid circular imports
# between sibling sub-modules.
from novel_forge.desktop.pages.document_renderer.popups import (
    _BlueprintTooltipPopup,
    _configure_artifact_tabs,
)
from novel_forge.desktop.pages.document_renderer.subplot import (
    _add_or_replace_subplot_matrix_tab,
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
from novel_forge.desktop.pages.standalone.renderer_html import (
    nl2br as _nl2br,
)
from novel_forge.desktop.pages.standalone.subplot_manager import (
    SubplotManagerPanel,
    _is_event_driven_arc,
)
from novel_forge.desktop.theme import resolve_qcolor
from novel_forge.desktop.tokens.typography import visualization_font

JsonDict = dict[str, Any]
FeedbackMarker = tuple[float, float, float, JsonDict, JsonDict]
WeaveLine = tuple[float, float, float, JsonDict, JsonDict]
DocumentRenderer = Callable[[dict[str, Any]], QWidget]

# ── Tension / weave-link color resolution ───────────────────────────────────
# Colors are resolved dynamically from design tokens so they update when the
# theme switches — no manual RGB sync required.
_TENSION_TOKEN_MAP: dict[str, str] = {
    "渐升": "accent.warm",
    "高": "accent.light",
    "高潮": "accent.primary",
    "回落": "role.supporting",
}


def _tension_color(tension_key: str) -> QColor:
    """Resolve the phase-tension QColor at call time."""
    token = _TENSION_TOKEN_MAP.get(tension_key, "border.default")
    return resolve_qcolor(token)


_WEAVE_LINK_TOKEN_MAP: dict[str, str] = {
    "trigger_start": "accent.primary",
    "trigger_turn": "accent.light",
    "constrain": "text.muted",
    "enable": "role.supporting",
    "conflict": "status.danger.deep",
    "feed_main": "status.info",
    "reveal_key": "motif.purple",
    "create_tension": "status.danger",
    "theme_echo": "accent.slider.warm",
}


def _weave_link_color(link_type: str) -> QColor:
    """Resolve the weave-link edge QColor at call time."""
    token = _WEAVE_LINK_TOKEN_MAP.get(link_type, "border.default")
    return resolve_qcolor(token)


def _html_token_color(token: str) -> str:
    """Resolve a rich-text color at render time for the active desktop theme."""
    return resolve_qcolor(token).name()

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

_FEEDBACK_LINK_TYPES: set[str] = {
    "feed_main",
    "reveal_key",
    "create_tension",
    "theme_echo",
}

_TIMELINE_MIN_CHAPTER_PX = 14
_TIMELINE_BASE_MIN_WIDTH = 700
_TIMELINE_SECTION_GAP = 22
_TIMELINE_MAINLINE_LABEL_GAP_PX = 46


def _as_positive_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _first_positive_int(data: JsonDict, *keys: str) -> int:
    for key in keys:
        value = _as_positive_int(data.get(key))
        if value:
            return value
    return 0


def _chapter_value(data: JsonDict, *extra_keys: str) -> int:
    return _first_positive_int(data, *extra_keys, "chapter_number", "chapter")


def _chapter_start(data: JsonDict) -> int:
    return _chapter_value(data, "chapter_start", "start_chapter")


def _chapter_end(data: JsonDict) -> int:
    return _chapter_value(data, "chapter_end", "end_chapter")


def _estimate_chapters_from_blueprint(data: JsonDict) -> int:
    chapters = data.get("chapters", [])
    if chapters:
        return len(chapters)
    phases = data.get("narrative_phases", [])
    max_ch = 0
    for ph in phases:
        if isinstance(ph, dict):
            end = ph.get("chapter_end", 0) or ph.get("end_chapter", 0)
            if end > max_ch:
                max_ch = end
    if max_ch > 0:
        return max_ch
    subplots = data.get("subplot_plan", [])
    for sp in subplots:
        if isinstance(sp, dict):
            chs = sp.get("involved_chapters", [])
            if chs:
                max_ch = max(max_ch, max(chs))
    return max_ch


def _normalize_tension_key(value: Any) -> str:
    """Map model-generated tension labels onto the renderer palette."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text in _TENSION_TOKEN_MAP:
        return text
    if any(token in text for token in ("critical", "climax", "高潮", "爆发")):
        return "高潮"
    if any(token in text for token in ("high", "紧张", "高")):
        return "高"
    if any(token in text for token in ("resolving", "resolution", "回落", "收束", "缓和")):
        return "回落"
    if any(token in text for token in ("rising", "medium", "渐升", "上升")):
        return "渐升"
    return ""


def _infer_blueprint_total_chapters(blueprint: JsonDict) -> int:
    """Infer timeline width from every chapter-bearing blueprint field."""
    max_chapter = 0
    for volume in blueprint.get("volumes", []) or []:
        if isinstance(volume, dict):
            max_chapter = max(max_chapter, _chapter_end(volume))
    for phase in blueprint.get("narrative_phases", []) or []:
        if isinstance(phase, dict):
            max_chapter = max(max_chapter, _chapter_end(phase))
    for tp in blueprint.get("key_turning_points", []) or []:
        if isinstance(tp, dict):
            max_chapter = max(max_chapter, _chapter_value(tp))
    for arc in blueprint.get("character_arcs", []) or []:
        if not isinstance(arc, dict):
            continue
        for milestone in arc.get("milestones", []) or []:
            if isinstance(milestone, dict):
                max_chapter = max(max_chapter, _chapter_end(milestone))
    for subplot in blueprint.get("subplot_plan", []) or []:
        if not isinstance(subplot, dict):
            continue
        max_chapter = max(max_chapter, _chapter_value(subplot, "resolution_chapter"))
        for chapter in subplot.get("involved_chapters", []) or []:
            max_chapter = max(max_chapter, _as_positive_int(chapter))
        for event in subplot.get("chapter_events", []) or []:
            if isinstance(event, dict):
                max_chapter = max(max_chapter, _chapter_value(event))
        for link in subplot.get("weave_links", []) or []:
            if isinstance(link, dict):
                max_chapter = max(max_chapter, _chapter_value(link, "trigger_chapter"))
    for suspense in blueprint.get("suspense_schedule", []) or []:
        if isinstance(suspense, dict):
            max_chapter = max(
                max_chapter,
                _chapter_value(suspense, "introduce_chapter"),
                _chapter_value(suspense, "resolve_chapter", "chapter_end", "end_chapter"),
            )
    return max_chapter or 24


def _normalize_narrative_blueprint_for_display(data: JsonDict) -> JsonDict:
    """Apply schema-level display normalization when the blueprint is schema-safe.

    Artifact rendering usually receives raw JSON from disk. Normalizing here lets
    old saved blueprints benefit from schema invariants, especially subplot
    timeline nodes synthesized from ``involved_chapters``.
    """
    try:
        from novel_forge.core.schemas.outline import NarrativeBlueprint

        return NarrativeBlueprint.model_validate(data).model_dump(mode="json")
    except (ImportError, ValueError, TypeError, AttributeError):
        return data


def _extract_mainline_nodes(blueprint: JsonDict) -> list[JsonDict]:
    """Return explicit mainline nodes for display.

    The schema represents the main plot through synopsis, phases, and turning
    points. Prefer turning points; if they are absent, fall back to phase
    endings so the UI still shows a visible mainline spine.
    """
    nodes: list[dict[str, Any]] = []
    for point in blueprint.get("key_turning_points", []) or []:
        if not isinstance(point, dict):
            continue
        chapter = _chapter_value(point)
        if chapter < 1:
            continue
        description = str(point.get("description", "") or "").strip()
        label = str(point.get("title") or point.get("name") or "").strip()
        if not label:
            label = "关键转折"
        nodes.append(
            {
                "chapter": chapter,
                "label": label,
                "description": description,
                "location": point.get("location", ""),
                "characters": point.get("characters_involved", []) or point.get("characters", []),
                "source": "turning_point",
            }
        )

    if not nodes:
        for phase in blueprint.get("narrative_phases", []) or []:
            if not isinstance(phase, dict):
                continue
            chapter = _chapter_end(phase) or _chapter_start(phase)
            if chapter < 1:
                continue
            name = str(phase.get("phase_name", "") or "").strip()
            nodes.append(
                {
                    "chapter": chapter,
                    "label": name or "阶段收束",
                    "description": str(phase.get("description", "") or "").strip(),
                    "location": "、".join(
                        str(loc) for loc in phase.get("primary_locations", []) or []
                    ),
                    "characters": phase.get("key_characters", []),
                    "source": "phase",
                }
            )

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    for node in sorted(nodes, key=lambda item: item["chapter"]):
        marker = (int(node["chapter"]), str(node["label"]), str(node["description"]))
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(node)
    return deduped


def _character_arc_span(arc: JsonDict, total_chapters: int) -> tuple[int, int]:
    """Infer the visible span for one character arc lane."""
    total = max(1, total_chapters)
    starts: list[int] = []
    ends: list[int] = []

    direct_start = _chapter_start(arc)
    direct_end = _chapter_end(arc)
    if direct_start:
        starts.append(direct_start)
    if direct_end:
        ends.append(direct_end)

    for milestone in arc.get("milestones", []) or []:
        if not isinstance(milestone, dict):
            continue
        ms_start = _chapter_start(milestone) or _chapter_value(milestone)
        ms_end = _chapter_end(milestone) or ms_start
        if ms_start:
            starts.append(ms_start)
        if ms_end:
            ends.append(ms_end)

    if not starts and not ends:
        return 1, total

    start = min(starts or ends)
    end = max(ends or starts)
    start = max(1, min(start, total))
    end = max(start, min(end, total))

    if start == end:
        padding = max(2, total // 20)
        start = max(1, start - padding)
        end = min(total, end + padding)

    return start, end


class NarrativeBlueprintWidget(QWidget):
    """Visual timeline for the narrative blueprint showing phases,
    turning points, character arcs, and subplot lanes."""

    def __init__(self, blueprint: JsonDict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        blueprint = _normalize_narrative_blueprint_for_display(blueprint)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._phases = blueprint.get("narrative_phases", [])
        self._turning_points = blueprint.get("key_turning_points", [])
        self._mainline_nodes = _extract_mainline_nodes(blueprint)
        self._character_arcs = blueprint.get("character_arcs", [])
        self._subplots = blueprint.get("subplot_plan", [])
        self._synopsis = blueprint.get("synopsis", "")
        self._ending = blueprint.get("ending_strategy", "")

        self._total_chapters = _infer_blueprint_total_chapters(blueprint)

        self._hovered_phase: JsonDict | None = None
        self._hovered_tp: JsonDict | None = None
        self._hovered_arc: JsonDict | None = None
        self._hovered_arc_ms: tuple[JsonDict, JsonDict] | None = None
        self._hovered_subplot: JsonDict | None = None
        self._hovered_subplot_node: tuple[JsonDict, int, str] | None = None
        self._weave_lines: list[WeaveLine] = []
        self._feedback_markers: list[FeedbackMarker] = []
        self._collapsed_subplots: set[str] = set()
        self._subplot_name_rects: list[tuple[QRectF, str]] = []
        self._active_tooltip: _BlueprintTooltipPopup | None = None
        self._update_timeline_size_hint()

    def set_blueprint_data(self, data: dict[str, Any]) -> None:
        """Update internal blueprint data and trigger re-render.

        Called when subplot CRUD operations modify the blueprint,
        ensuring the timeline reflects the latest state.
        """
        data = _normalize_narrative_blueprint_for_display(data)
        self._phases = data.get("narrative_phases", [])
        self._turning_points = data.get("key_turning_points", [])
        self._mainline_nodes = _extract_mainline_nodes(data)
        self._character_arcs = data.get("character_arcs", [])
        self._subplots = data.get("subplot_plan", [])
        self._synopsis = data.get("synopsis", "")
        self._ending = data.get("ending_strategy", "")
        self._total_chapters = _infer_blueprint_total_chapters(data)
        self._update_timeline_size_hint()
        self.update()

    def _update_timeline_size_hint(self) -> None:
        min_width = max(
            _TIMELINE_BASE_MIN_WIDTH,
            self._total_chapters * _TIMELINE_MIN_CHAPTER_PX + 260,
        )
        subplot_count = len(self._subplots)
        arc_count = len(self._character_arcs)
        subplot_lane_h, subplot_lane_gap = self._subplot_lane_metrics(subplot_count)
        estimated_subplots_h = (
            0 if subplot_count == 0 else 58 + subplot_count * (subplot_lane_h + subplot_lane_gap)
        )
        estimated_arcs_h = 56 if arc_count == 0 else 30 + arc_count * 38 + 20
        min_height = max(
            560,
            40 + 72 + 44 + 78 + estimated_subplots_h + estimated_arcs_h + 30,
        )
        self.setMinimumSize(min_width, int(min_height))

    def _subplot_lane_metrics(self, subplot_count: int) -> tuple[int, int]:
        """Return lane height/gap with enough vertical air for link markers."""
        if subplot_count > 12:
            return 24, 6
        if subplot_count > 7:
            return 26, 7
        return 28, 8

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = event.position() if hasattr(event, "position") else event.localPos()
        for rect, name in getattr(self, "_subplot_name_rects", []):
            if rect.contains(pos):
                if name in self._collapsed_subplots:
                    self._collapsed_subplots.discard(name)
                else:
                    self._collapsed_subplots.add(name)
                self.update()
                return
        super().mousePressEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        margin_left = max(160, min(260, int(w * 0.14)))
        margin_right = 30
        margin_top = 40
        track_width = w - margin_left - margin_right

        y_cursor = margin_top

        painter.setPen(QPen(resolve_qcolor("separator", 30), 1))
        for ch_num in range(1, self._total_chapters + 1):
            x = margin_left + (ch_num - 0.5) / self._total_chapters * track_width
            painter.drawLine(int(x), margin_top, int(x), int(self.height()))

        # ── Section: Narrative Phases ──
        phase_h = 52
        self._draw_section_header(painter, "叙事阶段", margin_left, y_cursor)
        y_cursor += phase_h + 8
        self._phase_rects: list[tuple[QRectF, JsonDict]] = []

        if not self._phases:
            empty_font = visualization_font(10)
            painter.setFont(empty_font)
            painter.setPen(resolve_qcolor("text.muted"))
            painter.drawText(
                QRectF(margin_left, y_cursor - phase_h + 10, track_width, 30),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                "暂无叙事阶段数据",
            )
        else:
            for phase in self._phases:
                start = _chapter_start(phase) or 1
                end = _chapter_end(phase) or start
                x = margin_left + (start - 1) / self._total_chapters * track_width
                w_bar = max(4, (end - start + 1) / self._total_chapters * track_width)
                rect = QRectF(x, y_cursor - phase_h + 8, w_bar, phase_h)
                self._phase_rects.append((rect, phase))

                tension = phase.get("tension_level", "")
                tension_key = _normalize_tension_key(tension)
                color = _tension_color(tension_key)
                is_hovered = phase is self._hovered_phase

                self._draw_gradient_phase_bar(painter, rect, color, is_hovered)

                pen_w = 2.0 if is_hovered else 1.2
                painter.setPen(QPen(color, pen_w))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect, 6, 6)

                name_font = visualization_font(10)
                name_font.setWeight(QFont.Weight.Bold)
                detail_font = visualization_font(8)
                fm_name = QFontMetrics(name_font)
                fm_detail = QFontMetrics(detail_font)
                inner_w = w_bar - 12
                PAD_V = 5
                name_h = fm_name.height()
                detail_h = fm_detail.height()
                text_y_name = rect.top() + PAD_V
                text_y_detail = text_y_name + name_h + 3
                if inner_w > 30 and text_y_detail + detail_h + PAD_V <= rect.bottom():
                    elided_name = fm_name.elidedText(
                        phase.get("phase_name", ""), Qt.TextElideMode.ElideRight, int(inner_w)
                    )
                    painter.setFont(name_font)
                    painter.setPen(resolve_qcolor("text.heading.deep"))
                    painter.drawText(
                        QRectF(rect.left() + 6, text_y_name, inner_w, name_h),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                        elided_name,
                    )
                    detail_text = f"Ch.{start}-{end} · {tension}"
                    elided_detail = fm_detail.elidedText(
                        detail_text, Qt.TextElideMode.ElideRight, int(inner_w)
                    )
                    painter.setFont(detail_font)
                    painter.setPen(resolve_qcolor("text.muted.strong"))
                    painter.drawText(
                        QRectF(rect.left() + 6, text_y_detail, inner_w, detail_h),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                        elided_detail,
                    )
                elif inner_w > 30 and name_h + PAD_V * 2 <= phase_h:
                    elided_name = fm_name.elidedText(
                        phase.get("phase_name", ""), Qt.TextElideMode.ElideRight, int(inner_w)
                    )
                    painter.setFont(name_font)
                    painter.setPen(resolve_qcolor("text.heading.deep"))
                    painter.drawText(
                        QRectF(rect.left() + 6, text_y_name, inner_w, name_h),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                        elided_name,
                    )

        y_cursor += 12

        # ── Chapter axis with alternating bands ──
        axis_y = y_cursor
        band_h = 20

        # Alternating background bands for chapters
        for ch_num in range(1, self._total_chapters + 1):
            x_left = margin_left + (ch_num - 1) / self._total_chapters * track_width
            x_right = margin_left + ch_num / self._total_chapters * track_width
            band_rect = QRectF(x_left, axis_y - band_h / 2, x_right - x_left, band_h)
            if ch_num % 2 == 0:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(resolve_qcolor("separator", 18))
                painter.drawRect(band_rect)

        # Main axis line
        painter.setPen(QPen(resolve_qcolor("separator"), 1.5))
        painter.drawLine(
            int(margin_left),
            int(axis_y),
            int(margin_left + track_width),
            int(axis_y),
        )

        tick_font = visualization_font(8)
        painter.setFont(tick_font)
        painter.setPen(resolve_qcolor("text.muted"))
        tick_step = 1 if self._total_chapters <= 12 else (3 if self._total_chapters <= 30 else 5)
        for ch_num in range(1, self._total_chapters + 1):
            x = margin_left + (ch_num - 0.5) / self._total_chapters * track_width
            if ch_num == 1 or ch_num == self._total_chapters or ch_num % tick_step == 0:
                painter.drawLine(int(x), int(axis_y - 4), int(x), int(axis_y + 4))
                painter.drawText(
                    QRectF(x - 12, axis_y + 5, 24, 14),
                    Qt.AlignmentFlag.AlignCenter,
                    str(ch_num),
                )

        y_cursor = axis_y + 24

        self._draw_section_header(painter, "主线骨架", margin_left, y_cursor)
        tp_y = y_cursor + 24
        tp_r = 11
        self._tp_rects: list[tuple[QRectF, JsonDict]] = []
        mainline_center_y = tp_y + tp_r
        x_start = margin_left + 0.5 / self._total_chapters * track_width
        x_end = margin_left + (self._total_chapters - 0.5) / self._total_chapters * track_width

        painter.setPen(QPen(resolve_qcolor("accent.primary", 120), 2.5))
        painter.drawLine(int(x_start), int(mainline_center_y), int(x_end), int(mainline_center_y))

        mainline_label_font = visualization_font(8)
        painter.setFont(mainline_label_font)
        painter.setPen(resolve_qcolor("accent.dark"))
        painter.drawText(
            QRectF(margin_left, tp_y - 16, track_width, 14),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "主线推进：关键转折与阶段收束",
        )

        self._mainline_label_rects: list[QRectF] = []
        last_label_x_by_row = [-10_000.0, -10_000.0]
        mainline_label_rows = 1
        for tp in self._mainline_nodes:
            ch = _chapter_value(tp) or 1
            x = margin_left + (ch - 0.5) / self._total_chapters * track_width
            rect = QRectF(x - tp_r, tp_y, tp_r * 2, tp_r * 2)
            self._tp_rects.append((rect, tp))
            is_hovered = tp is self._hovered_tp
            self._draw_glow_diamond(painter, x, tp_y, tp_r, resolve_qcolor("accent.primary"), is_hovered)
            ch_font = visualization_font(8)
            ch_font.setWeight(QFont.Weight.Bold)
            painter.setFont(ch_font)
            painter.setPen(resolve_qcolor("accent.dark"))
            row = 0
            if x - last_label_x_by_row[0] < _TIMELINE_MAINLINE_LABEL_GAP_PX:
                row = 1
            other_row = 1 - row
            if (
                x - last_label_x_by_row[row] < _TIMELINE_MAINLINE_LABEL_GAP_PX
                and x - last_label_x_by_row[other_row] >= _TIMELINE_MAINLINE_LABEL_GAP_PX
            ):
                row = other_row
            label_rect = QRectF(x - 22, tp_y + tp_r * 2 + 3 + row * 13, 44, 14)
            self._mainline_label_rects.append(label_rect)
            last_label_x_by_row[row] = x
            mainline_label_rows = max(mainline_label_rows, row + 1)
            painter.drawText(
                label_rect,
                Qt.AlignmentFlag.AlignCenter,
                f"Ch.{ch}",
            )

        y_cursor = tp_y + tp_r * 2 + 4 + mainline_label_rows * 13 + _TIMELINE_SECTION_GAP

        if self._subplots:
            self._draw_section_header(painter, "支线计划", margin_left, y_cursor)
            subplot_count = len(self._subplots)
            subplot_lane_h, subplot_lane_gap = self._subplot_lane_metrics(subplot_count)
            feedback_bus_y = y_cursor + 36
            subplot_section_start = y_cursor + 58
            if self._subplots:
                painter.setPen(QPen(resolve_qcolor("accent.primary", 80), 1.2, Qt.PenStyle.DashLine))
                painter.drawLine(
                    int(margin_left),
                    int(feedback_bus_y),
                    int(margin_left + track_width),
                    int(feedback_bus_y),
                )
                bus_font = visualization_font(7)
                painter.setFont(bus_font)
                painter.setPen(resolve_qcolor("accent.dark", 150))
                painter.drawText(
                    QRectF(margin_left, feedback_bus_y - 20, track_width, 14),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    "支线反哺/主题回响汇入主线",
                )
            self._subplot_name_rects = []
            subplot_colors = [
                resolve_qcolor("status.success.job"),
                resolve_qcolor("status.info"),
                resolve_qcolor("role.antagonist"),
                resolve_qcolor("accent.slider.warm"),
                resolve_qcolor("accent.light"),
                resolve_qcolor("accent.warm"),
                resolve_qcolor("text.muted.soft"),
                resolve_qcolor("role.supporting"),
            ]
            self._subplot_rects: list[tuple[QRectF, JsonDict]] = []
            self._subplot_node_rects: list[tuple[QRectF, JsonDict, int, str]] = []
            for sp_idx, sp in enumerate(self._subplots):
                color = subplot_colors[sp_idx % len(subplot_colors)]
                lane_y = subplot_section_start + sp_idx * (subplot_lane_h + subplot_lane_gap)

                # Name label — elide if needed
                sp_name = sp.get("name", f"支线{sp_idx + 1}")
                is_collapsed = sp_name in self._collapsed_subplots
                name_font = visualization_font(8)
                painter.setFont(name_font)
                fm = QFontMetrics(name_font)
                elided = fm.elidedText(sp_name, Qt.TextElideMode.ElideRight, int(margin_left - 18))

                # Collapse indicator
                display_name = f"▶ {elided}" if is_collapsed else f"▼ {elided}"
                name_rect = QRectF(4, lane_y, margin_left - 14, subplot_lane_h)
                self._subplot_name_rects.append((name_rect, sp_name))

                painter.setPen(resolve_qcolor("text.body"))
                painter.drawText(
                    name_rect,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    display_name,
                )

                # Skip lane content for collapsed subplots
                if is_collapsed:
                    continue

                # Involved chapters as dots connected by a line
                chapters = sorted(
                    ch
                    for ch in (_as_positive_int(item) for item in sp.get("involved_chapters", []))
                    if ch
                )
                event_map = self._subplot_event_map(sp)
                if chapters:
                    x_first = margin_left + (chapters[0] - 0.5) / self._total_chapters * track_width
                    x_last = margin_left + (chapters[-1] - 0.5) / self._total_chapters * track_width
                    center_y = lane_y + subplot_lane_h / 2
                    painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 100), 2.0))
                    painter.drawLine(
                        int(x_first),
                        int(center_y),
                        int(x_last),
                        int(center_y),
                    )

                    chapters_with_events = sorted(event_map.keys())
                    for ch in chapters_with_events:
                        x = margin_left + (ch - 0.5) / self._total_chapters * track_width
                        event_text = event_map.get(ch, "")
                        is_node_hovered = (
                            self._hovered_subplot_node is not None
                            and self._hovered_subplot_node[0] is sp
                            and self._hovered_subplot_node[1] == ch
                        )
                        is_lane_hovered = sp is self._hovered_subplot
                        dot_r = 4.8 if is_node_hovered else (4.2 if is_lane_hovered else 3.5)
                        painter.setPen(QPen(color, 1.5))
                        painter.setBrush(
                            QColor(
                                color.red(),
                                color.green(),
                                color.blue(),
                                95 if is_node_hovered else (75 if is_lane_hovered else 50),
                            )
                        )
                        dot_rect = QRectF(x - dot_r, center_y - dot_r, dot_r * 2, dot_r * 2)
                        self._subplot_node_rects.append((dot_rect, sp, ch, event_text))
                        painter.drawEllipse(QPointF(x, center_y), dot_r, dot_r)

                        depends_on = self._get_depends_on_for_event(sp, ch)
                        if depends_on:
                            painter.setPen(QPen(resolve_qcolor("accent.slider.warm"), 1.0))
                            painter.setBrush(resolve_qcolor("accent.slider.warm", 120))
                            marker_x = x + dot_r + 3
                            marker_y = center_y - dot_r - 2
                            painter.drawEllipse(QPointF(marker_x, marker_y), 2.5, 2.5)

                # Full bar background for reference
                sp_rect = QRectF(margin_left, lane_y, track_width, subplot_lane_h)
                self._subplot_rects.append((sp_rect, sp))

            # Weave lines between subplot lanes — collect first, then draw with overlap prevention
            self._weave_lines = []
            self._feedback_markers = []

            # Pass 1: collect all weave links grouped by trigger_chapter
            _pending_feedback: list[tuple[int, float, JsonDict, JsonDict]] = []
            _pending_weave: list[tuple[int, float, float, JsonDict, JsonDict]] = []
            _feedback_by_ch: dict[int, list[tuple[int, float, JsonDict, JsonDict]]] = {}
            _weave_by_ch: dict[int, list[tuple[int, float, float, JsonDict, JsonDict]]] = {}

            for sp_idx, sp in enumerate(self._subplots):
                sp_name = sp.get("name", f"支线{sp_idx + 1}")
                if sp_name in self._collapsed_subplots:
                    continue
                involved_chapters = sp.get("involved_chapters", [])
                if isinstance(involved_chapters, list) and involved_chapters:
                    _sp_last_active = max(_as_positive_int(ch) for ch in involved_chapters)
                else:
                    _sp_last_active = 0
                weave_links = sp.get("weave_links", [])
                for link in weave_links:
                    trigger_ch = _chapter_value(link, "trigger_chapter")
                    if trigger_ch < 1:
                        continue

                    link_type = link.get("link_type", "")
                    target_subplot = link.get("target_subplot", "")

                    if _sp_last_active > 0 and trigger_ch > _sp_last_active:
                        continue

                    if link_type in _FEEDBACK_LINK_TYPES or target_subplot == "主线":
                        y_from = (
                            subplot_section_start
                            + sp_idx * (subplot_lane_h + subplot_lane_gap)
                            + subplot_lane_h / 2
                        )
                        feedback_entry = (trigger_ch, y_from, link, sp)
                        _pending_feedback.append(feedback_entry)
                        _feedback_by_ch.setdefault(trigger_ch, []).append(feedback_entry)
                    else:
                        target_idx = None
                        for t_idx, t_sp in enumerate(self._subplots):
                            if t_sp.get("name") == target_subplot:
                                target_idx = t_idx
                                break

                        if target_idx is not None and target_idx != sp_idx:
                            y_from = (
                                subplot_section_start
                                + sp_idx * (subplot_lane_h + subplot_lane_gap)
                                + subplot_lane_h / 2
                            )
                            y_to = (
                                subplot_section_start
                                + target_idx * (subplot_lane_h + subplot_lane_gap)
                                + subplot_lane_h / 2
                            )
                            weave_entry = (trigger_ch, y_from, y_to, link, sp)
                            _pending_weave.append(weave_entry)
                            _weave_by_ch.setdefault(trigger_ch, []).append(weave_entry)

            # Helper: pen style + arrow direction by link_type
            def _weave_pen_style(link_type: str) -> tuple[Qt.PenStyle, str]:
                if link_type in {"feed_main", "reveal_key", "theme_echo"}:
                    return Qt.PenStyle.SolidLine, "up"
                if link_type in {"trigger_start", "trigger_turn"}:
                    return Qt.PenStyle.DashLine, "down"
                return Qt.PenStyle.DotLine, "up"

            # Pass 2: draw feedback links with horizontal offset for overlap prevention
            for trigger_ch, feedback_group in _feedback_by_ch.items():
                base_x = margin_left + (trigger_ch - 0.5) / self._total_chapters * track_width
                count = len(feedback_group)
                for i, (_, y_from, link, sp) in enumerate(feedback_group):
                    offset = (i - (count - 1) / 2) * 16
                    x = base_x + offset
                    y_to = feedback_bus_y

                    self._feedback_markers.append((x, y_from, y_to, link, sp))

                    link_type = link.get("link_type", "")
                    wc = self._get_weave_link_color(link_type)
                    pen_style, arrow_dir = _weave_pen_style(link_type)
                    line_color = QColor(wc.red(), wc.green(), wc.blue(), 170)
                    painter.setPen(QPen(line_color, 1.7, pen_style))
                    painter.drawLine(int(x), int(y_from), int(x), int(y_to))

                    painter.setPen(QPen(wc, 2.0))
                    painter.setBrush(QColor(wc.red(), wc.green(), wc.blue(), 90))
                    painter.drawEllipse(QPointF(x, y_to), 4.5, 4.5)

                    if arrow_dir == "up":
                        painter.drawLine(int(x - 4), int(y_to + 7), int(x), int(y_to + 3))
                        painter.drawLine(int(x + 4), int(y_to + 7), int(x), int(y_to + 3))
                    else:
                        painter.drawLine(int(x - 4), int(y_to - 7), int(x), int(y_to - 3))
                        painter.drawLine(int(x + 4), int(y_to - 7), int(x), int(y_to - 3))

                    if count > 1 and i == count - 1:
                        self._draw_weave_count_badge(
                            painter,
                            base_x,
                            feedback_bus_y - 1,
                            count,
                            resolve_qcolor("accent.primary"),
                        )

            # Pass 3: draw weave lines with horizontal offset for overlap prevention
            for trigger_ch, weave_group in _weave_by_ch.items():
                base_x = margin_left + (trigger_ch - 0.5) / self._total_chapters * track_width
                count = len(weave_group)
                for i, (_, y_from, y_to, link, sp) in enumerate(weave_group):
                    offset = (i - (count - 1) / 2) * 16
                    x = base_x + offset

                    self._weave_lines.append((x, y_from, y_to, link, sp))

                    link_type = link.get("link_type", "")
                    wc = self._get_weave_link_color(link_type)
                    pen_style, _ = _weave_pen_style(link_type)
                    line_color = QColor(wc.red(), wc.green(), wc.blue(), 160)
                    painter.setPen(QPen(line_color, 1.7, pen_style))
                    painter.drawLine(int(x), int(y_from), int(x), int(y_to))

                    mid_y = (y_from + y_to) / 2
                    painter.setPen(QPen(wc, 1.8))
                    painter.setBrush(QColor(wc.red(), wc.green(), wc.blue(), 100))
                    painter.drawEllipse(QPointF(x, mid_y), 3.8, 3.8)

                    if count > 1 and i == count - 1:
                        self._draw_weave_count_badge(painter, base_x, mid_y, count, wc)

            y_cursor = (
                subplot_section_start
                + len(self._subplots) * (subplot_lane_h + subplot_lane_gap)
                + _TIMELINE_SECTION_GAP
            )
        else:
            self._subplot_rects = []
            self._subplot_node_rects = []
            self._subplot_name_rects = []
            self._weave_lines = []
            self._feedback_markers = []

        self._arc_ms_rects: list[tuple[QRectF, JsonDict, JsonDict]] = []
        self._arc_lane_rects: list[tuple[QRectF, JsonDict]] = []
        self._draw_section_header(painter, "角色弧光", margin_left, y_cursor)
        y_cursor += 26

        if not self._character_arcs:
            empty_font = visualization_font(10)
            painter.setFont(empty_font)
            painter.setPen(resolve_qcolor("text.muted"))
            painter.drawText(
                QRectF(margin_left, y_cursor, track_width, 30),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                "暂无角色弧光数据",
            )
            y_cursor += 30
        else:
            arc_lane_h = 32
            arc_section_start = y_cursor
            arc_bar_h = 20
            arc_colors = [
                resolve_qcolor("accent.primary"),
                resolve_qcolor("accent.light"),
                resolve_qcolor("role.antagonist"),
                resolve_qcolor("role.supporting"),
                resolve_qcolor("text.muted"),
            ]
            for arc_idx, arc in enumerate(self._character_arcs):
                name = arc.get("character", "")
                color = arc_colors[arc_idx % len(arc_colors)]
                lane_y = arc_section_start + arc_idx * (arc_lane_h + 6)
                bar_y = lane_y + (arc_lane_h - arc_bar_h) / 2

                name_font = visualization_font(9)
                name_font.setWeight(QFont.Weight.DemiBold)
                painter.setFont(name_font)
                painter.setPen(resolve_qcolor("text.heading"))
                fm = QFontMetrics(name_font)
                name_rect = QRectF(4, lane_y, margin_left - 14, arc_lane_h)
                elided = fm.elidedText(name, Qt.TextElideMode.ElideRight, int(margin_left - 18))
                painter.drawText(
                    name_rect,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    elided,
                )

                track_rect = QRectF(margin_left, bar_y, track_width, arc_bar_h)
                self._arc_lane_rects.append((track_rect, arc))
                is_arc_hovered = arc is self._hovered_arc or (
                    self._hovered_arc_ms is not None and self._hovered_arc_ms[0] is arc
                )
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(color.red(), color.green(), color.blue(), 15))
                painter.drawRoundedRect(track_rect, 6, 6)

                span_start, span_end = _character_arc_span(arc, self._total_chapters)
                span_x = margin_left + (span_start - 1) / self._total_chapters * track_width
                span_end_x = margin_left + span_end / self._total_chapters * track_width
                span_rect = QRectF(span_x, bar_y, max(10, span_end_x - span_x), arc_bar_h)

                lighter_color = QColor(
                    min(color.red() + 25, 255),
                    min(color.green() + 25, 255),
                    min(color.blue() + 25, 255),
                    70 if is_arc_hovered else 55,
                )
                painter.setBrush(lighter_color)
                painter.drawRoundedRect(span_rect, 6, 6)
                painter.setPen(QPen(color, 1.6 if is_arc_hovered else 1.2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(span_rect, 6, 6)

                span_text = f"Ch.{span_start}-{span_end}"
                if span_rect.width() > 74:
                    meta_font = visualization_font(8)
                    painter.setFont(meta_font)
                    painter.setPen(resolve_qcolor("text.heading", 190))
                    painter.drawText(
                        span_rect.adjusted(8, 0, -8, 0),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        QFontMetrics(meta_font).elidedText(
                            span_text, Qt.TextElideMode.ElideRight, int(span_rect.width() - 16)
                        ),
                    )

                milestones = arc.get("milestones", [])
                for ms in milestones:
                    if not isinstance(ms, dict):
                        continue
                    ms_start = _chapter_start(ms) or _chapter_value(ms)
                    if ms_start < 1:
                        continue
                    ms_end = _chapter_end(ms) or ms_start
                    ms_mid = (ms_start + ms_end) / 2
                    x = margin_left + (ms_mid - 0.5) / self._total_chapters * track_width
                    marker_rect = QRectF(x - 7, bar_y - 4, 14, arc_bar_h + 8)
                    self._arc_ms_rects.append((marker_rect, arc, ms))

                    is_hovered = (arc, ms) == (
                        self._hovered_arc_ms[0] if self._hovered_arc_ms else None,
                        self._hovered_arc_ms[1] if self._hovered_arc_ms else None,
                    )
                    marker_w = 7 if is_hovered else 5.5
                    marker_h = arc_bar_h + 4
                    ms_color = QColor(
                        min(color.red() + 20, 255),
                        min(color.green() + 20, 255),
                        min(color.blue() + 20, 255),
                        220 if is_hovered else 160,
                    )
                    painter.setPen(QPen(color, 1.2))
                    painter.setBrush(ms_color)
                    painter.drawRoundedRect(
                        QRectF(x - marker_w / 2, bar_y - 2, marker_w, marker_h),
                        marker_w / 2,
                        marker_w / 2,
                    )

            y_cursor = arc_section_start + len(self._character_arcs) * (arc_lane_h + 6) + 16

        # Update minimum height
        self.setMinimumHeight(int(y_cursor + 20))
        painter.end()

    def _draw_section_header(
        self, painter: QPainter, text: str, margin_left: float, y: float, icon: str = ""
    ) -> None:
        header_h = 22
        bar_rect = QRectF(0, y, margin_left - 8, header_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(resolve_qcolor("separator", 25))
        painter.drawRoundedRect(bar_rect.adjusted(8, 0, 0, 0), 4, 4)
        accent_rect = QRectF(8, y + 4, 3, header_h - 8)
        painter.setBrush(resolve_qcolor("accent.primary"))
        painter.drawRoundedRect(accent_rect, 1, 1)
        font = visualization_font(9)
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(resolve_qcolor("text.body.warm"))
        display_text = f"{icon} {text}" if icon else text
        painter.drawText(
            QRectF(16, y, margin_left - 24, header_h),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            display_text,
        )

    def _draw_gradient_phase_bar(
        self, painter: QPainter, rect: QRectF, color: QColor, is_hovered: bool
    ) -> None:
        shadow_offset = 2 if is_hovered else 1
        if is_hovered:
            shadow_rect = rect.adjusted(shadow_offset, shadow_offset, shadow_offset, shadow_offset)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 20))
            painter.drawRoundedRect(shadow_rect, 6, 6)
        gradient_top = QColor(
            min(color.red() + 30, 255),
            min(color.green() + 30, 255),
            min(color.blue() + 30, 255),
            color.alpha(),
        )
        steps = 4
        step_h = rect.height() / steps
        for i in range(steps):
            t = i / (steps - 1) if steps > 1 else 0
            r = int(color.red() + (gradient_top.red() - color.red()) * (1 - t))
            g = int(color.green() + (gradient_top.green() - color.green()) * (1 - t))
            b = int(color.blue() + (gradient_top.blue() - color.blue()) * (1 - t))
            alpha = int(color.alpha() * (0.85 + 0.15 * (1 - t)))
            step_rect = QRectF(rect.left(), rect.top() + i * step_h, rect.width(), step_h + 1)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(r, g, b, alpha))
            painter.drawRect(step_rect)

    def _draw_weave_count_badge(
        self, painter: QPainter, x: float, y: float, count: int, color: QColor
    ) -> None:
        if count <= 1:
            return
        font = visualization_font(7)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        text = f"+{count}"
        fm = QFontMetrics(font)
        badge_w = max(18, fm.horizontalAdvance(text) + 8)
        badge_rect = QRectF(x - badge_w / 2, y - 9, badge_w, 16)
        painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 180), 1.0))
        painter.setBrush(resolve_qcolor("bg.surface.elevated", 225))
        painter.drawRoundedRect(badge_rect, 7, 7)
        painter.setPen(QColor(color.red(), color.green(), color.blue(), 210))
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_glow_diamond(
        self, painter: QPainter, x: float, y: float, size: float, color: QColor, is_hovered: bool
    ) -> QPolygonF:
        half = size
        diamond = QPolygonF(
            [
                QPointF(x, y),
                QPointF(x + half, y + half),
                QPointF(x, y + half * 2),
                QPointF(x - half, y + half),
            ]
        )
        if is_hovered:
            glow_size = half + 4
            glow_diamond = QPolygonF(
                [
                    QPointF(x, y - 2),
                    QPointF(x + glow_size, y + half),
                    QPointF(x, y + half * 2 + 2),
                    QPointF(x - glow_size, y + half),
                ]
            )
            glow_color = QColor(color.red(), color.green(), color.blue(), 40)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow_color)
            painter.drawPolygon(glow_diamond)
        fill_color = QColor(color.red(), color.green(), color.blue(), 180 if is_hovered else 120)
        painter.setBrush(fill_color)
        painter.setPen(QPen(color, 2.2 if is_hovered else 1.6))
        painter.drawPolygon(diamond)
        inner_size = half * 0.5
        inner_diamond = QPolygonF(
            [
                QPointF(x, y + half - inner_size),
                QPointF(x + inner_size, y + half),
                QPointF(x, y + half + inner_size),
                QPointF(x - inner_size, y + half),
            ]
        )
        highlight_color = QColor(255, 255, 255, 60 if is_hovered else 30)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(highlight_color)
        painter.drawPolygon(inner_diamond)
        return diamond

    def _draw_enhanced_weave_line(
        self, painter: QPainter, x: float, y_from: float, y_to: float, color: QColor, link_type: str
    ) -> None:
        pen_style = Qt.PenStyle.SolidLine
        if link_type in {"trigger_start", "trigger_turn"}:
            pen_style = Qt.PenStyle.DashLine
        painter.setPen(QPen(color, 2.0, pen_style))
        painter.drawLine(int(x), int(y_from), int(x), int(y_to))
        arrow_size = 6
        mid_y = y_to
        painter.setPen(QPen(color, 2.5))
        painter.setBrush(color)
        arrow_path = [
            QPointF(x - arrow_size / 2, mid_y - arrow_size),
            QPointF(x + arrow_size / 2, mid_y - arrow_size),
            QPointF(x, mid_y),
        ]
        arrow_polygon = QPolygonF(arrow_path)
        painter.drawPolygon(arrow_polygon)

    def _draw_section_label(
        self, painter: QPainter, text: str, margin_left: float, y: float
    ) -> None:
        """Draw a section label right-aligned to the left margin."""
        font = visualization_font(9)
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(resolve_qcolor("text.body.warm"))
        painter.drawText(
            QRectF(4, y, margin_left - 14, 20),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            text,
        )

    def _subplot_event_map(self, subplot: JsonDict) -> dict[int, str]:
        """Return chapter -> event map for one subplot lane."""
        event_map: dict[int, str] = {}
        chapter_events = subplot.get("chapter_events", [])
        if isinstance(chapter_events, list):
            for item in chapter_events:
                if not isinstance(item, dict):
                    continue
                chapter_number = _chapter_value(item)
                if chapter_number < 1:
                    continue
                event_map[chapter_number] = str(item.get("event", "") or "").strip()
        return event_map

    def _default_subplot_node_event(self, subplot: JsonDict, chapter: int) -> str:
        name = str(subplot.get("name", "") or "").strip()
        if name:
            return f"第 {chapter} 章推进支线「{name}」。"
        return f"第 {chapter} 章推进该支线。"

    def _get_weave_link_color(self, link_type: str) -> QColor:
        return _weave_link_color(link_type)

    def _get_depends_on_for_event(self, subplot: JsonDict, chapter: int) -> list[Any]:
        for item in subplot.get("chapter_events", []):
            if not isinstance(item, dict):
                continue
            if _chapter_value(item) == chapter:
                depends_on = item.get("depends_on", [])
                return depends_on if isinstance(depends_on, list) else []
        return []

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position() if hasattr(event, "position") else event.localPos()
        self._hovered_phase = None
        self._hovered_tp = None
        self._hovered_arc = None
        self._hovered_arc_ms = None
        self._hovered_subplot = None
        self._hovered_subplot_node = None

        # Check phases
        for rect, phase in getattr(self, "_phase_rects", []):
            if rect.contains(pos):
                self._hovered_phase = phase
                break

        # Check turning points
        if not self._hovered_phase:
            for rect, tp in getattr(self, "_tp_rects", []):
                expanded = rect.adjusted(-4, -4, 4, 4)
                if expanded.contains(pos):
                    self._hovered_tp = tp
                    break

        # Check arc milestones
        if not self._hovered_phase and not self._hovered_tp:
            for rect, arc, ms in getattr(self, "_arc_ms_rects", []):
                if rect.contains(pos):
                    self._hovered_arc_ms = (arc, ms)
                    break

        # Check whole character arc lanes
        if not self._hovered_phase and not self._hovered_tp and not self._hovered_arc_ms:
            for rect, arc in getattr(self, "_arc_lane_rects", []):
                if rect.contains(pos):
                    self._hovered_arc = arc
                    break

        # Check subplot chapter nodes
        if (
            not self._hovered_phase
            and not self._hovered_tp
            and not self._hovered_arc_ms
            and not self._hovered_arc
        ):
            for rect, sp, chapter, event_text in getattr(self, "_subplot_node_rects", []):
                expanded = rect.adjusted(-5, -5, 5, 5)
                if expanded.contains(pos):
                    self._hovered_subplot_node = (sp, chapter, event_text)
                    self._hovered_subplot = sp
                    break

        # Check subplots
        if (
            not self._hovered_phase
            and not self._hovered_tp
            and not self._hovered_arc_ms
            and not self._hovered_arc
            and not self._hovered_subplot_node
        ):
            for rect, sp in getattr(self, "_subplot_rects", []):
                if rect.contains(pos):
                    self._hovered_subplot = sp
                    break

        self.update()
        self._show_blueprint_tooltip(pos)

    def _show_blueprint_tooltip(self, pos: QPointF) -> None:
        if self._hovered_phase:
            phase = self._hovered_phase
            phase_start = _chapter_start(phase) or "?"
            phase_end = _chapter_end(phase) or "?"
            lines = [
                f"<b style='font-size: 14pt;'>{_esc(phase.get('phase_name', ''))}</b>",
                f"<span style='color:{_html_token_color('text.muted.strong')}'>"
                f"Ch.{phase_start}-{phase_end}"
                f" · 张力：{_esc(phase.get('tension_level', ''))}</span>",
                f"<br>{_esc(phase.get('description', ''))}",
            ]
            events = phase.get("key_events", [])
            if events:
                lines.append("<br><b>关键事件：</b>")
                for ev in events:
                    lines.append(f"&nbsp;&nbsp;· {_esc(ev)}")
            # Anchoring info
            tc = phase.get("time_context", "")
            locs = phase.get("primary_locations", [])
            chars = phase.get("key_characters", [])
            if tc or locs or chars:
                lines.append("<br><b>⚓ 锚定</b>")
                if tc:
                    lines.append(f"&nbsp;&nbsp;⏰ {_esc(tc)}")
                if locs:
                    lines.append(
                        f"&nbsp;&nbsp;📍 {_esc('、'.join(str(location) for location in locs))}"
                    )
                if chars:
                    lines.append(f"&nbsp;&nbsp;👤 {_esc('、'.join(str(c) for c in chars))}")
            self._show_tip(pos, lines)
        elif self._hovered_tp:
            tp = self._hovered_tp
            tp_chapter = _chapter_value(tp) or "?"
            tp_label = str(tp.get("label") or tp.get("title") or "主线节点")
            lines = [
                f"<b>{_esc(tp_label)} · 第 {tp_chapter} 章</b>",
                f"{_esc(tp.get('description', ''))}",
            ]
            tp_loc = tp.get("location", "")
            tp_chars = tp.get("characters", []) or tp.get("characters_involved", [])
            if tp_loc:
                lines.append(f"📍 {_esc(tp_loc)}")
            if tp_chars:
                lines.append(f"👤 {_esc('、'.join(str(c) for c in tp_chars))}")
            self._show_tip(pos, lines)
        elif self._hovered_arc_ms:
            arc, ms = self._hovered_arc_ms
            ms_start = _chapter_start(ms) or "?"
            ms_end = _chapter_end(ms) or "?"
            lines = [
                f"<b>{_esc(arc.get('character', ''))} 角色弧光</b>",
                f"<span style='color:{_html_token_color('text.muted.strong')}'>Ch.{ms_start}-{ms_end}</span>",
                f"{_esc(ms.get('description', ''))}",
                f"<br><i style='color:{_html_token_color('text.muted')}'>总弧：{_esc(arc.get('arc_summary', ''))}</i>",
            ]
            self._show_tip(pos, lines)
        elif self._hovered_arc:
            arc = self._hovered_arc
            span_start, span_end = _character_arc_span(arc, self._total_chapters)
            milestone_count = len(
                [ms for ms in arc.get("milestones", []) or [] if isinstance(ms, dict)]
            )
            lines = [
                f"<b>{_esc(arc.get('character', ''))} 角色弧光</b>",
                f"<span style='color:{_html_token_color('text.muted.strong')}'>Ch.{span_start}-{span_end}"
                f" · {milestone_count} 个里程碑</span>",
                f"{_esc(arc.get('arc_summary', ''))}",
            ]
            self._show_tip(pos, lines)
        elif self._hovered_subplot_node:
            sp, chapter, event_text = self._hovered_subplot_node
            event_body = event_text.strip() if isinstance(event_text, str) else ""
            if not event_body:
                event_body = self._default_subplot_node_event(sp, chapter)
            lines = [
                f"<b>{_esc(sp.get('name', ''))} · 节点</b>",
                f"<span style='color:{_html_token_color('text.muted.strong')}'>第 {chapter} 章</span>",
                f"{_esc(event_body)}",
            ]
            if not str(event_text or "").strip():
                lines.append(
                    f"<i style='color:{_html_token_color('accent.deep')}'>"
                    "当前蓝图未提供逐节点事件，建议在 chapter_events 中补充。</i>"
                )

            weave_links = sp.get("weave_links", [])
            relevant_links = [lk for lk in weave_links if lk.get("trigger_chapter") == chapter]
            if relevant_links:
                lines.append("<br><b>🔗 交织关系：</b>")
                for lk in relevant_links:
                    link_type_raw = lk.get("link_type", "")
                    link_type_cn = _LINK_TYPE_LABELS.get(link_type_raw, link_type_raw)
                    target = lk.get("target_subplot", "")
                    if link_type_raw in _FEEDBACK_LINK_TYPES:
                        direction = "↑"
                        target_display = "主线" if target == "主线" else target
                    else:
                        direction = "↓"
                        target_display = target
                    lines.append(
                        f"&nbsp;&nbsp;{direction} {link_type_cn}「{target_display}」："
                        f"{_esc(lk.get('description', ''))}"
                    )

            event_data: JsonDict = next(
                (
                    e
                    for e in sp.get("chapter_events", [])
                    if isinstance(e, dict) and e.get("chapter_number") == chapter
                ),
                {},
            )
            depends_on = event_data.get("depends_on", [])
            if depends_on:
                lines.append("<br><b>📎 依赖：</b>")
                for dep in depends_on:
                    lines.append(f"&nbsp;&nbsp;· {_esc(dep)}")

            self._show_tip(pos, lines)
        elif self._hovered_subplot:
            sp = self._hovered_subplot
            chapters = sp.get("involved_chapters", [])
            ch_str = ", ".join(str(c) for c in chapters)
            lines = [
                f"<b>{_esc(sp.get('name', ''))}</b>",
                f"{_esc(sp.get('description', ''))}",
                f"<span style='color:{_html_token_color('text.muted.strong')}'>涉及章节：{_esc(ch_str)}</span>",
            ]
            event_map = self._subplot_event_map(sp)
            if event_map:
                lines.append("<br><b>节点事件：</b>")
                for chapter in sorted(event_map):
                    text = event_map.get(chapter, "")
                    if not text:
                        text = self._default_subplot_node_event(sp, chapter)
                    lines.append(f"&nbsp;&nbsp;· Ch.{chapter}：{_esc(text)}")
            self._show_tip(pos, lines)
        else:
            self._hide_popup()

    def _show_tip(self, pos: QPointF, lines: list[str]) -> None:
        html = (
            f"<div style='max-width:300px; font-family:Songti SC, STSong, SimSun; "
            f"font-size: 10pt; line-height:1.45; padding:2px;'>"
            f"{'<br>'.join(lines)}</div>"
        )
        popup = self._active_tooltip
        if popup is None:
            popup = _BlueprintTooltipPopup(self)
            self._active_tooltip = popup
        popup.set_content(html)
        popup.show_at(self.mapToGlobal(pos.toPoint()))

    def _hide_popup(self) -> None:
        popup = self._active_tooltip
        if popup is None:
            return
        popup.hide()
        popup.deleteLater()
        self._active_tooltip = None

    def leaveEvent(self, event: QEvent) -> None:
        self._hovered_phase = None
        self._hovered_tp = None
        self._hovered_arc = None
        self._hovered_arc_ms = None
        self._hovered_subplot = None
        self._hovered_subplot_node = None
        self.update()
        self._hide_popup()

    def hideEvent(self, event: QHideEvent) -> None:
        self._hide_popup()
        super().hideEvent(event)


def _build_narrative_with_subplot_panel(
    data: JsonDict,
    tabs: QTabWidget,
    project_path: Path,
    timeline: "NarrativeBlueprintWidget | None" = None,
) -> QWidget:
    """Add subplot management as a tab to the narrative blueprint tabs."""
    # Avoid double initial render (load_data + set_blueprint_data) which can
    # leave stale row widgets visible until deleteLater() flushes.
    subplot_panel = SubplotManagerPanel(project_path=project_path, auto_load=False)
    subplot_panel.set_blueprint_data(data)
    tabs.addTab(subplot_panel, "支线管理")

    def _on_data_changed() -> None:
        subplot_panel.refresh()
        fresh_data: JsonDict = data
        if timeline is not None:
            blueprint_path = project_path / "plans" / "narrative_blueprint.json"
            if blueprint_path.exists():
                try:
                    with open(blueprint_path, "r", encoding="utf-8") as f:
                        fresh_data = json.load(f)
                    fresh_data = _normalize_narrative_blueprint_for_display(fresh_data)
                    timeline.set_blueprint_data(fresh_data)
                except (json.JSONDecodeError, OSError):
                    pass
        _add_or_replace_subplot_matrix_tab(tabs, fresh_data, project_path)

    subplot_panel.data_changed.connect(_on_data_changed)

    return tabs


def render_narrative_blueprint(data: JsonDict, project_path: Path | str | None = None) -> QWidget:
    """Build a two-tab widget: Tab1 = synopsis + ending, Tab2 = visual timeline.

    Optionally integrates a SubplotManagerPanel when project_path is provided.
    """
    data = _normalize_narrative_blueprint_for_display(data)
    tabs = _configure_artifact_tabs(QTabWidget(), "narrativeBlueprintTabs")

    # ── Tab 1: Story Synopsis + Ending Strategy ──
    overview_widget = QWidget()
    overview_layout = QVBoxLayout(overview_widget)
    overview_layout.setContentsMargins(0, 0, 0, 0)
    overview_layout.setSpacing(0)

    overview_parts: list[str] = []
    synopsis = data.get("synopsis", "")
    if synopsis:
        overview_parts.append(
            f'<div class="section">'
            f"<h3>故事概要</h3>"
            f'<div class="kv-value">{_nl2br(synopsis)}</div>'
            f"</div>"
        )

    element_selection = data.get("element_selection", {})
    if isinstance(element_selection, dict):
        required_items = element_selection.get("required_elements", [])
        extension_items = element_selection.get("extension_elements", [])
        selector_summary = element_selection.get("selector_summary", "")
        if selector_summary or required_items or extension_items:
            overview_parts.append("<h2>叙事要素选择</h2>")
            if selector_summary:
                overview_parts.append(
                    f'<div class="hint-block">{_nl2br(str(selector_summary))}</div>'
                )
            if isinstance(required_items, list) and required_items:
                req_labels = [
                    _esc(str(item.get("name") or item.get("element_id") or ""))
                    for item in required_items
                    if isinstance(item, dict)
                ]
                req_text = "、".join([text for text in req_labels if text])
                if req_text:
                    overview_parts.append(f'<div class="rule-item"><b>必要项</b>：{req_text}</div>')
            if isinstance(extension_items, list) and extension_items:
                for item in extension_items:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("name", "")
                    reason = item.get("selection_reason", "")
                    hint = item.get("prompt_hint", "")
                    overview_parts.append(
                        '<div class="rule-item">'
                        f"<b>{_esc(str(name))}</b>"
                        + (f"<br>选用原因：{_esc(str(reason))}" if reason else "")
                        + (
                            f'<br><span class="blueprint-secondary">提示词：{_esc(str(hint))}</span>'
                            if hint
                            else ""
                        )
                        + "</div>"
                    )

    mainline_nodes = _extract_mainline_nodes(data)
    if mainline_nodes:
        overview_parts.append("<h2>主线骨架</h2>")
        for node in mainline_nodes:
            chapter = node.get("chapter", "?")
            label = str(node.get("label", "") or "主线节点")
            description = str(node.get("description", "") or "").strip()
            location = str(node.get("location", "") or "").strip()
            characters = node.get("characters", [])
            meta: list[str] = [f"Ch.{chapter}"]
            if location:
                meta.append(f"场景：{_esc(location)}")
            if isinstance(characters, list) and characters:
                meta.append(f"角色：{_esc('、'.join(str(char) for char in characters))}")
            elif isinstance(characters, str) and characters:
                meta.append(f"角色：{_esc(characters)}")
            meta_html = (
                f'<span class="tag-muted tag" style="margin-left:8px;">{" | ".join(meta)}</span>'
            )
            overview_parts.append(
                f'<div class="rule-item"><b>{_esc(label)}</b>{meta_html}<br>'
                f"{_nl2br(description)}</div>"
            )

    # Character arcs summary
    character_arcs = data.get("character_arcs", [])
    if character_arcs:
        total_ch_for_arc = _estimate_chapters_from_blueprint(data)
        overview_parts.append("<h2>角色弧光总览</h2>")
        for arc in character_arcs:
            name = arc.get("character", "")
            arc_summary = arc.get("arc_summary", "")
            if name:
                convertible_badge = ""
                if isinstance(arc, dict) and _is_event_driven_arc(arc, total_ch_for_arc):
                    convertible_badge = (
                        ' <span class="blueprint-convertible-badge" '
                        'title="事件驱动型弧光，可转换为独立支线">可转支线</span>'
                    )
                overview_parts.append(
                    f'<div class="rule-item"><b>{_esc(name)}</b>：{_nl2br(arc_summary)}'
                    f"{convertible_badge}</div>"
                )

    # Narrative phases with anchoring
    phases = data.get("narrative_phases", [])
    if phases:
        overview_parts.append("<h2>叙事阶段</h2>")
        for ph in phases:
            ph_name = ph.get("phase_name", "")
            ch_s = _chapter_start(ph) or "?"
            ch_e = _chapter_end(ph) or "?"
            desc = ph.get("description", "")
            tension = ph.get("tension_level", "")
            tc = ph.get("time_context", "")
            locs = ph.get("primary_locations", [])
            chars = ph.get("key_characters", [])
            events = ph.get("key_events", [])
            # Anchoring badges
            badges: list[str] = []
            if tc:
                badges.append(f"⏰ {_esc(tc)}")
            if locs:
                badges.append(f"📍 {_esc('、'.join(str(location) for location in locs))}")
            if chars:
                badges.append(f"👤 {_esc('、'.join(str(c) for c in chars))}")
            anchor_html = ""
            if badges:
                anchor_html = (
                    '<div class="blueprint-meta" style="margin-top:3px;">'
                    + " &nbsp;│&nbsp; ".join(badges)
                    + "</div>"
                )
            events_html = ""
            if events:
                events_html = (
                    '<div class="blueprint-secondary" style="margin-top:2px;">'
                    "关键事件：" + _esc("；".join(str(e) for e in events)) + "</div>"
                )
            overview_parts.append(
                f'<div class="rule-item">'
                f"<b>{_esc(ph_name)}</b>"
                f'<span class="tag-muted tag" style="margin-left:8px;">'
                f"Ch.{ch_s}-{ch_e}</span>"
                f'<span class="tag-muted tag" style="margin-left:4px;">'
                f"{_esc(tension)}</span><br>"
                f"{_nl2br(desc)}"
                f"{anchor_html}{events_html}"
                f"</div>"
            )

    # Subplot summaries
    subplots = data.get("subplot_plan", [])
    if subplots:
        overview_parts.append("<h2>支线计划</h2>")
        for sp in subplots:
            sp_name = sp.get("name", "")
            sp_desc = sp.get("description", "")
            chapters = sp.get("involved_chapters", [])
            ch_str = ", ".join(str(c) for c in chapters)

            priority = sp.get("priority", "normal")
            resolution_ch = sp.get("resolution_chapter", 0)
            resolution_target = sp.get("resolution_target", "")
            resolution_type = sp.get("resolution_type", "")
            meta_badges: list[str] = []
            if priority != "normal":
                priority_labels = {"primary": "🔴 准主线", "background": "⚪ 背景"}
                priority_label = priority_labels.get(priority)
                meta_badges.append(priority_label if priority_label else str(priority or "normal"))
            if resolution_ch > 0:
                meta_badges.append(f"收束于 Ch.{resolution_ch}")
            if resolution_target:
                meta_badges.append(f"收束目标：{_esc(resolution_target)}")
            if resolution_type:
                type_labels = {
                    "resolve": "解决",
                    "reveal": "揭露",
                    "ascend": "升华",
                    "merge": "合并",
                }
                meta_badges.append(f"收束类型：{type_labels.get(resolution_type, resolution_type)}")
            meta_html = ""
            if meta_badges:
                meta_html = (
                    f'<br><span class="blueprint-meta">'
                    f"{' | '.join(meta_badges)}</span>"
                )

            weave_links = sp.get("weave_links", [])
            weave_html = ""
            if weave_links:
                weave_lines_list: list[str] = []
                for lk in weave_links:
                    link_type_raw = lk.get("link_type", "")
                    link_type_cn = _LINK_TYPE_LABELS.get(link_type_raw, link_type_raw)
                    if link_type_raw in _FEEDBACK_LINK_TYPES:
                        direction = "↑"
                        target_display = (
                            "主线"
                            if lk.get("target_subplot") == "主线"
                            else lk.get("target_subplot", "")
                        )
                    else:
                        direction = "↓"
                        target_display = lk.get("target_subplot", "")
                    weave_lines_list.append(f"{direction}{link_type_cn}「{target_display}」")
                weave_html = (
                    f'<br><span class="blueprint-note">'
                    f"🔗 交织：{_esc('；'.join(weave_lines_list))}"
                    f"</span>"
                )

            chapter_events = sp.get("chapter_events", [])
            event_lines: list[str] = []
            if isinstance(chapter_events, list):
                for item in chapter_events:
                    if not isinstance(item, dict):
                        continue
                    chapter_number = _chapter_value(item)
                    if chapter_number < 1:
                        continue
                    event_text = str(item.get("event", "") or "").strip()
                    if not event_text:
                        event_text = f"第 {chapter_number} 章推进该支线。"
                    event_lines.append(f"Ch.{chapter_number}：{_esc(event_text)}")
            events_html = (
                (
                    '<br><span class="blueprint-meta">节点事件：'
                    + _esc("；".join(event_lines))
                    + "</span>"
                )
                if event_lines
                else ""
            )
            overview_parts.append(
                f'<div class="theme-item">'
                f"<b>{_esc(sp_name)}</b>：{_nl2br(sp_desc)}"
                f'<br><span class="blueprint-meta">涉及章节：{_esc(ch_str)}</span>'
                f"{meta_html}"
                f"{weave_html}"
                f"{events_html}"
                f"</div>"
            )

    ending = data.get("ending_strategy", "")
    if ending:
        overview_parts.append(f'<h2>收束策略</h2><div class="hint-block">{_nl2br(ending)}</div>')

    overview_html = "\n".join(overview_parts)
    browser: QTextBrowser | None = None
    if overview_html:
        browser = _make_browser(_html_wrap(overview_html, "叙事蓝图"))
        overview_layout.addWidget(browser, 1)
    else:
        empty = QLabel("暂无概要与收束策略信息。")
        empty.setObjectName("narrativeBlueprintEmpty")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overview_layout.addWidget(empty, 1)

    tabs.addTab(overview_widget, "概要与收束")

    # ── Tab 2: Visual Timeline ──
    timeline_widget = QWidget()
    timeline_layout = QVBoxLayout(timeline_widget)
    timeline_layout.setContentsMargins(0, 0, 0, 0)
    timeline_layout.setSpacing(0)

    scroll = QScrollArea()
    scroll.setObjectName("docTransparentScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(scroll.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    timeline = NarrativeBlueprintWidget(data)
    scroll.setWidget(timeline)
    timeline_layout.addWidget(scroll, 1)

    hint = QLabel("悬停各元素可查看详情：阶段描述、转折内容、支线说明与节点事件、角色弧光")
    hint.setObjectName("narrativeTimelineHint")
    hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
    timeline_layout.addWidget(hint)

    tabs.addTab(timeline_widget, "叙事时间线")

    def _refresh_theme() -> None:
        # The overview is a standalone QTextBrowser, not a RichDocumentViewer,
        # so it needs its own theme refresh hook.  Re-wrapping the already
        # escaped body regenerates the shared token-based document CSS.
        if browser is not None:
            update_browser_html(browser, _html_wrap(overview_html, "叙事蓝图"))
        timeline.update()

    # ``apply_desktop_theme`` discovers opt-in widgets through this protocol.
    # Dynamic instance attributes are intentionally used for QTabWidget-based
    # renderers elsewhere in the document layer as well.
    tabs.refresh_theme_colors = _refresh_theme  # type: ignore[attr-defined]
    _add_or_replace_subplot_matrix_tab(
        tabs,
        data,
        Path(project_path) if project_path else None,
    )

    # ── Subplot management integration ──
    if project_path:
        return _build_narrative_with_subplot_panel(data, tabs, Path(project_path), timeline)

    return tabs
