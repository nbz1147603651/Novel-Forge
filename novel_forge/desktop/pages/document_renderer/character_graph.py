"""Sub-module of novel_forge.desktop.pages.document_renderer.

Auto-generated in the M3.2 split. Contains character_graph.py renderers.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QContextMenuEvent,
    QFont,
    QFontMetrics,
    QHideEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QMenu, QSizePolicy, QWidget

from novel_forge.core.domain.character_identity import (
    clean_character_name,
    normalize_character_role,
    parenthetical_base_name,
)

# Late imports — placed after stdlib imports to avoid circular imports
# between sibling sub-modules.
from novel_forge.desktop.pages.document_renderer.popups import (
    _CharacterTooltipPopup,
)
from novel_forge.desktop.pages.document_renderer.relationship_matrix import (
    _shorten_text,
)
from novel_forge.desktop.pages.standalone.renderer_html import (
    esc as _esc,
)
from novel_forge.desktop.theme import resolve_qcolor
from novel_forge.desktop.tokens.typography import visualization_font

JsonDict = dict[str, Any]
FeedbackMarker = tuple[float, float, float, JsonDict, JsonDict]
WeaveLine = tuple[float, float, float, JsonDict, JsonDict]
DocumentRenderer = Callable[[dict[str, Any]], QWidget]

# ── Role / relation color resolution ─────────────────────────────────────
# Colors are resolved dynamically from design tokens so they update when the
# theme switches — no manual RGB sync required.

_ROLE_TOKEN_MAP: dict[str, str] = {
    "protagonist": "role.protagonist",
    "deuteragonist": "role.deuteragonist",
    "antagonist": "role.antagonist",
    "supporting": "role.supporting",
    "minor": "role.minor",
}

_ROLE_BG_ALPHA: dict[str, int] = {
    "protagonist": 30,
    "deuteragonist": 30,
    "antagonist": 25,
    "supporting": 25,
    "minor": 22,
}

_RELATION_TOKEN_MAP: dict[str, str] = {
    "bond": "relation.bond",
    "ally": "relation.ally",
    "tension": "relation.tension",
    "identity": "relation.identity",
    "neutral": "relation.neutral",
}


def _role_color(role: str) -> QColor:
    """Resolve the border QColor for a character *role* at call time."""
    token = _ROLE_TOKEN_MAP.get(role, "text.muted")
    return resolve_qcolor(token)


def _role_bg_color(role: str) -> QColor:
    """Resolve the background QColor for a character *role* at call time."""
    token = _ROLE_TOKEN_MAP.get(role, "text.muted")
    alpha = _ROLE_BG_ALPHA.get(role, 22)
    return resolve_qcolor(token, alpha)


def _relation_tone_color(tone: str) -> QColor:
    """Resolve the edge QColor for a relationship *tone* at call time."""
    token = _RELATION_TOKEN_MAP.get(tone, "border.default")
    return resolve_qcolor(token)


_RELATION_TONE_LABELS: dict[str, str] = {
    "bond": "情感/羁绊",
    "ally": "同盟/信任",
    "tension": "冲突/张力",
    "identity": "身份/指代",
    "neutral": "关系",
}

_ROLE_LABELS: dict[str, str] = {
    "protagonist": "主角",
    "deuteragonist": "第二主角",
    "antagonist": "反派",
    "supporting": "配角",
    "minor": "小角色",
}

_LEGACY_ROLE_LABELS: dict[str, str] = {
    "mentioned": "提及",
    "reference": "提及",
    "background": "背景",
    "提及": "提及",
    "仅提及": "提及",
    "背景人物": "背景",
}

_STATUS_LABELS: dict[str, str] = {
    "active": "活跃",
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
}


def _format_node_name(name: str) -> str:
    """Format a character name for display in graph nodes (moved from character_bible to break circular import)."""
    text = clean_character_name(name)
    if not text:
        return ""
    base = parenthetical_base_name(text)
    if base:
        return f"{base}\n{text[len(base) :]}"
    if re.search(r"[\u4e00-\u9fff]", text) and len(text) > 4:
        midpoint = (len(text) + 1) // 2
        return f"{text[:midpoint]}\n{text[midpoint:]}"
    return text


def _role_label_for_display(role: object, gender: object = "") -> str:
    """Return a UI label for a canonical role without misgendering leads."""
    raw = str(role or "").strip()
    raw_key = raw.lower().replace("-", "_")
    if raw in _LEGACY_ROLE_LABELS:
        return _LEGACY_ROLE_LABELS[raw]
    if raw_key in _LEGACY_ROLE_LABELS:
        return _LEGACY_ROLE_LABELS[raw_key]
    normalized = normalize_character_role(role)
    if normalized in {"protagonist", "deuteragonist"}:
        gender_text = str(gender or "").strip().lower()
        if "女" in gender_text or gender_text in {"female", "woman", "f"}:
            return "女主"
        if "男" in gender_text or gender_text in {"male", "man", "m"}:
            return "男主"
        if normalized == "protagonist":
            return "主角"
        return "第二主角"
    return _ROLE_LABELS.get(normalized, str(role or normalized))


def _status_label_for_display(status: object) -> str:
    """Return a compact Chinese lifecycle label for character badges."""
    text = str(status or "").strip()
    key = text.lower()
    if not text or key == "active":
        return ""
    return _STATUS_LABELS.get(key, _STATUS_LABELS.get(text, text))


def _format_age_display(age: object) -> str:
    """Normalize age text for UI display (avoid duplicated '岁')."""
    raw = str(age or "").strip()
    if not raw:
        return ""
    compact = re.sub(r"\s+", "", raw)
    m = re.fullmatch(r"(\d+)(?:岁|歲)?", compact)
    if m:
        return f"{m.group(1)}岁"
    return raw


def _relationship_tone(label: str, edge_type: str) -> str:
    """Classify a relationship into a compact visual tone for the graph."""
    if edge_type != "relationship":
        return "identity"

    text = str(label or "").lower()
    if any(
        token in text
        for token in (
            "敌",
            "仇",
            "冲突",
            "对立",
            "背叛",
            "威胁",
            "紧张",
            "压迫",
            "利用",
            "竞争",
        )
    ):
        return "tension"
    if any(
        token in text
        for token in (
            "恋",
            "爱",
            "吸引",
            "暧昧",
            "亲密",
            "羁绊",
            "家人",
            "血缘",
            "亲情",
        )
    ):
        return "bond"
    if any(
        token in text
        for token in (
            "同盟",
            "盟友",
            "合作",
            "朋友",
            "挚友",
            "师徒",
            "信任",
            "保护",
            "支持",
            "伙伴",
        )
    ):
        return "ally"
    return "neutral"


class _CharacterNode:
    """Layout data for a character node in the graph."""

    def __init__(self, char_data: JsonDict, index: int, total: int) -> None:
        self.name: str = clean_character_name(char_data.get("name", f"角色{index}"))
        self.role: str = normalize_character_role(char_data.get("role", "supporting"))
        self.gender: str = str(char_data.get("gender", "") or "")
        self.age: str = str(char_data.get("age", ""))
        self.appearance: str = char_data.get("appearance", "")
        self.personality: str = char_data.get("personality", "")
        self.backstory: str = char_data.get("backstory", "")
        self.arc: str = char_data.get("arc", "")
        self.relationships: dict[str, str] = char_data.get("relationships", {})
        self.center = QPointF(0, 0)
        self.radius = 0.0
        self.index = index


class _RelationshipEdge:
    """A connection between two character nodes."""

    def __init__(
        self,
        src: _CharacterNode,
        dst: _CharacterNode,
        label: str,
        *,
        edge_type: str = "relationship",
    ) -> None:
        self.src = src
        self.dst = dst
        self.label = label
        self.edge_type = edge_type
        self.tone = _relationship_tone(label, edge_type)


class CharacterGraphWidget(QWidget):
    """Interactive character relationship graph with hover tooltips."""

    focusChanged = Signal(str)
    relationshipCreateRequested = Signal(str, str)
    relationshipEditRequested = Signal(str, str)
    relationshipRemoveRequested = Signal(str, str)
    characterAddRequested = Signal()
    characterEditRequested = Signal(str)
    characterRetireRequested = Signal(str)

    def __init__(
        self,
        characters: list[JsonDict],
        *,
        entity_graph: JsonDict | None = None,
        editable: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(600, 450)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._editable = editable

        self._nodes: list[_CharacterNode] = []
        self._edges: list[_RelationshipEdge] = []
        self._hovered_node: _CharacterNode | None = None
        self._hovered_edge: _RelationshipEdge | None = None
        self._selected_node: _CharacterNode | None = None
        self._selected_edge: _RelationshipEdge | None = None
        self._dragging_edge: _CharacterNode | None = None
        self._drag_current_pos = QPointF(0, 0)
        self._snap_target: _CharacterNode | None = None
        self._name_to_node: dict[str, _CharacterNode] = {}
        self._pulse_phase = 0.0
        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(40)
        self._animation_timer.timeout.connect(self._advance_animation)
        # Lazily-created hover popup (replaces native tooltip delivery for
        # rounded-corner rendering).  Created on first hover, hidden on
        # leave / hide / new-hover-with-content-change.
        self._active_tooltip: _CharacterTooltipPopup | None = None

        # Build nodes
        for i, ch in enumerate(characters):
            node = _CharacterNode(ch, i, len(characters))
            self._nodes.append(node)
            self._name_to_node[node.name] = node

        # Build edges (deduplicated: only keep A→B, skip B→A)
        seen_pairs: set[tuple[str, str, str]] = set()
        for node in self._nodes:
            for target_name, label in node.relationships.items():
                target = self._name_to_node.get(target_name)
                if target is None:
                    continue
                pair_key = (
                    min(node.name, target.name),
                    max(node.name, target.name),
                    "relationship",
                )
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                self._edges.append(_RelationshipEdge(node, target, label))

        # Overlay typed identity/reference links when both endpoints are visible
        # character nodes.  Item/concept links are still available in the
        # EntityGraph artifact; the character graph stays readable.
        for link in (entity_graph or {}).get("entity_links", []) or []:
            if not isinstance(link, dict):
                continue
            source_name = str(link.get("source_name") or "").strip()
            target_name = str(link.get("target_name") or "").strip()
            src = self._name_to_node.get(source_name)
            dst = self._name_to_node.get(target_name)
            if src is None or dst is None or src is dst:
                continue
            edge_type = str(link.get("link_type") or "related_to").strip()
            pair_key = (min(src.name, dst.name), max(src.name, dst.name), edge_type)
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            label = str(link.get("description") or edge_type)
            self._edges.append(_RelationshipEdge(src, dst, label, edge_type=edge_type))

        self._layout_nodes()

    def focus_character(self, name: str) -> None:
        """Select a character node from an external control such as a card."""
        clean_name = clean_character_name(name)
        node = self._name_to_node.get(clean_name)
        if node is None:
            return
        self._selected_node = node
        self._selected_edge = None
        self._sync_animation_state()
        self.focusChanged.emit(node.name)
        self.update()

    def clear_focus(self) -> None:
        """Clear the pinned graph focus."""
        if self._selected_node is None and self._selected_edge is None:
            return
        self._selected_node = None
        self._selected_edge = None
        self._sync_animation_state()
        self.focusChanged.emit("")
        self.update()

    def _advance_animation(self) -> None:
        self._pulse_phase = (self._pulse_phase + 0.035) % 1.0
        self.update()

    def _sync_animation_state(self) -> None:
        active = any(
            (
                self._hovered_node is not None,
                self._hovered_edge is not None,
                self._selected_node is not None,
                self._selected_edge is not None,
                self._dragging_edge is not None,
                self._snap_target is not None,
            )
        )
        if active and not self._animation_timer.isActive():
            self._animation_timer.start()
        elif not active and self._animation_timer.isActive():
            self._animation_timer.stop()

    def _layout_nodes(self) -> None:
        """Position nodes in a radial layout with lead characters near center."""
        if not self._nodes:
            return

        core_nodes = [node for node in self._nodes if node.role in {"protagonist", "deuteragonist"}]
        if not core_nodes:
            core_nodes = [self._nodes[0]]

        # Store layout parameters; actual positions calculated in paintEvent
        self._center_nodes = core_nodes
        self._center_node = core_nodes[0]
        self._orbit_nodes = [node for node in self._nodes if node not in core_nodes]

    def _compute_positions(self) -> None:
        """Recompute node positions based on current widget size."""
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2

        # Node sizes scale with widget
        base_r = min(w, h) * 0.065
        center_count = len(getattr(self, "_center_nodes", []))
        center_r = base_r * (1.35 if center_count <= 1 else 1.18)
        orbit_r = base_r

        center_nodes = getattr(self, "_center_nodes", [self._center_node])
        if len(center_nodes) == 1:
            center_nodes[0].center = QPointF(cx, cy)
            center_nodes[0].radius = center_r
        elif len(center_nodes) == 2:
            offset = min(w, h) * 0.105
            for i, node in enumerate(center_nodes):
                node.center = QPointF(cx + (-offset if i == 0 else offset), cy)
                node.radius = center_r
        else:
            core_dist = min(w, h) * 0.12
            for i, node in enumerate(center_nodes):
                angle = -math.pi / 2 + (2 * math.pi * i / len(center_nodes))
                node.center = QPointF(
                    cx + core_dist * math.cos(angle),
                    cy + core_dist * math.sin(angle),
                )
                node.radius = center_r

        # Orbit radius
        orbit_dist = min(w, h) * 0.34
        n = len(self._orbit_nodes)
        for i, node in enumerate(self._orbit_nodes):
            angle = -math.pi / 2 + (2 * math.pi * i / max(n, 1))
            node.center = QPointF(
                cx + orbit_dist * math.cos(angle),
                cy + orbit_dist * math.sin(angle),
            )
            node.radius = orbit_r

    def _focus_node(self) -> _CharacterNode | None:
        return self._selected_node or self._hovered_node

    def _active_edge(self) -> _RelationshipEdge | None:
        return self._selected_edge or self._hovered_edge

    def _edge_is_active(self, edge: _RelationshipEdge) -> bool:
        active_edge = self._active_edge()
        if active_edge is not None:
            return edge is active_edge
        focus_node = self._focus_node()
        return focus_node is not None and (edge.src is focus_node or edge.dst is focus_node)

    def _node_focus_level(self, node: _CharacterNode) -> str:
        active_edge = self._active_edge()
        if active_edge is not None:
            if node in (active_edge.src, active_edge.dst):
                return "selected"
            return "muted"
        focus_node = self._focus_node()
        if focus_node is None:
            return "normal"
        if node is focus_node:
            return "selected"
        if any(self._edge_is_active(edge) and node in (edge.src, edge.dst) for edge in self._edges):
            return "neighbor"
        return "muted"

    def _hit_node(self, pos: QPointF) -> _CharacterNode | None:
        for node in self._nodes:
            dx = pos.x() - node.center.x()
            dy = pos.y() - node.center.y()
            if math.hypot(dx, dy) <= node.radius * 1.15:
                return node
        return None

    def _hit_node_edge_anchor(self, pos: QPointF, node: _CharacterNode) -> bool:
        distance = math.hypot(pos.x() - node.center.x(), pos.y() - node.center.y())
        return node.radius - 8 <= distance <= node.radius + 10

    def _snap_node_for_drag(self, pos: QPointF, source: _CharacterNode) -> _CharacterNode | None:
        best: tuple[float, _CharacterNode] | None = None
        for node in self._nodes:
            if node is source:
                continue
            distance = math.hypot(pos.x() - node.center.x(), pos.y() - node.center.y())
            if distance > max(30.0, node.radius * 0.85):
                continue
            if best is None or distance < best[0]:
                best = (distance, node)
        return best[1] if best is not None else None

    def _hit_edge(self, pos: QPointF) -> _RelationshipEdge | None:
        for edge in self._edges:
            if self._point_near_segment(pos, edge):
                return edge
        return None

    def paintEvent(self, event: QPaintEvent) -> None:
        self._compute_positions()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(event.rect(), resolve_qcolor("bg.inset"))

        # Draw edges
        for edge in self._edges:
            self._draw_edge(painter, edge)

        self._draw_drag_edge(painter)

        # Draw nodes
        for node in self._nodes:
            self._draw_node(painter, node)

        painter.end()

    def _draw_edge(self, painter: QPainter, edge: _RelationshipEdge) -> None:
        """Draw a relationship line between two character nodes."""
        src_c = edge.src.center
        dst_c = edge.dst.center

        # Calculate edge points on node boundaries
        dx = dst_c.x() - src_c.x()
        dy = dst_c.y() - src_c.y()
        dist = math.hypot(dx, dy)
        if dist < 1:
            return

        ux, uy = dx / dist, dy / dist
        p1 = QPointF(
            src_c.x() + ux * edge.src.radius,
            src_c.y() + uy * edge.src.radius,
        )
        p2 = QPointF(
            dst_c.x() - ux * edge.dst.radius,
            dst_c.y() - uy * edge.dst.radius,
        )

        is_hovered = edge is self._hovered_edge
        is_selected = edge is self._selected_edge
        is_active = self._edge_is_active(edge)
        has_focus = self._focus_node() is not None or self._active_edge() is not None
        tone_color = _relation_tone_color(edge.tone)
        alpha = 45 if has_focus and not is_active else 120
        if is_hovered or is_selected:
            alpha = 220
        elif is_active:
            alpha = 185
        width = 1.2 if has_focus and not is_active else 1.7
        if is_active:
            width = 2.4
        if is_hovered or is_selected:
            width = 3.0
        pen = QPen(
            QColor(tone_color.red(), tone_color.green(), tone_color.blue(), alpha),
            width,
        )
        if edge.edge_type in {"reincarnation_of", "alias_of", "mistaken_as"}:
            pen.setStyle(Qt.PenStyle.DashLine)
        elif edge.edge_type != "relationship":
            pen.setStyle(Qt.PenStyle.DotLine)
        else:
            pen.setStyle(Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        painter.drawLine(p1, p2)

        if is_active:
            pulse_t = (self._pulse_phase + edge.src.index * 0.11 + edge.dst.index * 0.07) % 1.0
            glow_x = p1.x() + (p2.x() - p1.x()) * pulse_t
            glow_y = p1.y() + (p2.y() - p1.y()) * pulse_t
            glow_color = QColor(tone_color.red(), tone_color.green(), tone_color.blue(), 185)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(glow_color))
            painter.drawEllipse(QPointF(glow_x, glow_y), 3.8, 3.8)

        # Relationship label is shown via a hover popup — no canvas text to avoid overlaps.

    def _draw_drag_edge(self, painter: QPainter) -> None:
        if self._dragging_edge is None:
            return
        source = self._dragging_edge
        target_pos = (
            self._snap_target.center if self._snap_target is not None else self._drag_current_pos
        )
        dx = target_pos.x() - source.center.x()
        dy = target_pos.y() - source.center.y()
        distance = math.hypot(dx, dy)
        if distance < 1:
            return
        ux, uy = dx / distance, dy / distance
        start = QPointF(
            source.center.x() + ux * source.radius,
            source.center.y() + uy * source.radius,
        )
        end = target_pos
        if self._snap_target is not None:
            end = QPointF(
                target_pos.x() - ux * self._snap_target.radius,
                target_pos.y() - uy * self._snap_target.radius,
            )
        color = resolve_qcolor("role.supporting", 220 if self._snap_target is not None else 150)
        pen = QPen(color, 2.8 if self._snap_target is not None else 1.8)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(start, end)

    def _find_edge_label_pos(
        self,
        mid: QPointF,
        label_w: float,
        label_h: float,
        edge_dx: float,
        edge_dy: float,
        edge_dist: float,
    ) -> QPointF:
        """Return a label center that avoids all node circles via perpendicular offsets."""
        # Perpendicular unit vector (rotate edge direction 90°)
        perp_x = -edge_dy / edge_dist
        perp_y = edge_dx / edge_dist

        # Half-extents of the label box plus a small clearance margin
        half_w = label_w / 2 + 4
        half_h = label_h / 2 + 4

        for offset in (0, 18, -18, 34, -34, 50, -50):
            cx = mid.x() + perp_x * offset
            cy = mid.y() + perp_y * offset
            overlaps = False
            for node in self._nodes:
                nx, ny = node.center.x(), node.center.y()
                # AABB–circle closest-point test
                closest_x = max(cx - half_w, min(nx, cx + half_w))
                closest_y = max(cy - half_h, min(ny, cy + half_h))
                # radius + 14 accounts for the name text drawn outside the circle
                if math.hypot(closest_x - nx, closest_y - ny) < node.radius + 14:
                    overlaps = True
                    break
            if not overlaps:
                return QPointF(cx, cy)

        return mid  # fallback: original midpoint

    def _draw_node(self, painter: QPainter, node: _CharacterNode) -> None:
        """Draw a single character node."""
        is_hovered = node is self._hovered_node
        is_selected = node is self._selected_node or node is self._snap_target
        focus_level = self._node_focus_level(node)
        if node is self._snap_target:
            focus_level = "selected"
        is_muted = focus_level == "muted"
        scale = (
            1.12
            if is_selected
            else 1.08
            if is_hovered
            else 1.03
            if focus_level == "neighbor"
            else 1.0
        )
        r = node.radius * scale
        color = _role_color(node.role)
        bg_color = _role_bg_color(node.role)
        alpha = 78 if is_muted else 255
        border_alpha = 72 if is_muted else 230

        # Shadow for hovered
        center = node.center
        rect = QRectF(center.x() - r, center.y() - r, 2 * r, 2 * r)

        if is_selected or is_hovered:
            pulse = 0.5 + 0.5 * math.sin(self._pulse_phase * 2 * math.pi)
            halo_r = r + 8 + pulse * 8
            halo_color = QColor(color.red(), color.green(), color.blue(), 38 if is_selected else 28)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(halo_color))
            painter.drawEllipse(
                QRectF(center.x() - halo_r, center.y() - halo_r, 2 * halo_r, 2 * halo_r)
            )

        # Fill
        painter.setPen(Qt.PenStyle.NoPen)
        if is_hovered or is_selected:
            # Brighter background
            painter.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 48)))
        elif is_muted:
            painter.setBrush(QBrush(resolve_qcolor("text.muted", 20)))
        else:
            painter.setBrush(QBrush(bg_color))
        painter.drawEllipse(rect)

        # Border
        pen_width = 2.8 if is_hovered or is_selected else 2.0 if focus_level == "neighbor" else 1.5
        painter.setPen(
            QPen(QColor(color.red(), color.green(), color.blue(), border_alpha), pen_width)
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect)

        # Character name
        painter.setPen(resolve_qcolor("text.heading.deep", alpha))
        name_rect = rect.adjusted(5, 4, -5, -4)
        self._draw_fitted_node_text(
            painter,
            name_rect,
            _format_node_name(node.name),
            base_size=13 if is_hovered else 12,
        )

        # Role tag below
        role_label = _role_label_for_display(node.role, node.gender)
        tag_font = visualization_font(9)
        tag_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(tag_font)
        painter.setPen(QColor(color.red(), color.green(), color.blue(), alpha))
        tag_rect = QRectF(center.x() - 20, center.y() + r + 4, 40, 16)
        painter.drawText(tag_rect, Qt.AlignmentFlag.AlignCenter, role_label)

    @staticmethod
    def _draw_fitted_node_text(
        painter: QPainter,
        rect: QRectF,
        text: str,
        *,
        base_size: int,
    ) -> None:
        """Draw a node label without clipping long Chinese names."""
        flags = Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap
        target = rect.toRect()
        for size in range(base_size, 7, -1):
            font = visualization_font(size)
            font.setWeight(QFont.Weight.Bold)
            metrics = QFontMetrics(font)
            bounds = metrics.boundingRect(target, int(flags), text)
            if bounds.width() <= target.width() and bounds.height() <= target.height():
                painter.setFont(font)
                painter.drawText(target, flags, text)
                return

        font = visualization_font(8)
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(target, flags, text)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._compute_positions()
        pos = event.position() if hasattr(event, "position") else event.localPos()
        if self._dragging_edge is not None:
            self._drag_current_pos = pos
            self._snap_target = self._snap_node_for_drag(pos, self._dragging_edge)
            self._sync_animation_state()
            self.update()
            event.accept()
            return
        old_node = self._hovered_node
        old_edge = self._hovered_edge
        self._hovered_node = self._hit_node(pos)
        self._hovered_edge = None if self._hovered_node is not None else self._hit_edge(pos)
        if self._editable and self._hovered_node is not None:
            if self._hit_node_edge_anchor(pos, self._hovered_node):
                self.setCursor(Qt.CursorShape.CrossCursor)
            else:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()

        if self._hovered_node != old_node or self._hovered_edge != old_edge:
            self._sync_animation_state()
            self.update()
            self._show_tooltip(pos)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._compute_positions()
        pos = event.position() if hasattr(event, "position") else event.localPos()
        node = self._hit_node(pos)
        if node is not None:
            if self._editable and self._hit_node_edge_anchor(pos, node):
                self._dragging_edge = node
                self._drag_current_pos = pos
                self._snap_target = None
                self._hide_popup()
                self._sync_animation_state()
                self.update()
                event.accept()
                return
            self._selected_node = node
            self._selected_edge = None
            self._sync_animation_state()
            self.focusChanged.emit(node.name)
            self.update()
            event.accept()
            return
        edge = self._hit_edge(pos)
        if edge is not None:
            self._selected_node = None
            self._selected_edge = edge
            self._sync_animation_state()
            self.focusChanged.emit(f"{edge.src.name} → {edge.dst.name}")
            self.update()
            event.accept()
            return
        self.clear_focus()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._dragging_edge is None:
            super().mouseReleaseEvent(event)
            return
        source = self._dragging_edge
        target = self._snap_target
        self._dragging_edge = None
        self._snap_target = None
        self._drag_current_pos = QPointF(0, 0)
        self._sync_animation_state()
        self.update()
        if event.button() == Qt.MouseButton.LeftButton and target is not None:
            self.relationshipCreateRequested.emit(source.name, target.name)
            event.accept()
            return
        event.accept()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        if not self._editable:
            super().contextMenuEvent(event)
            return
        self._compute_positions()
        pos = QPointF(event.pos())
        node = self._hit_node(pos)
        edge = None if node is not None else self._hit_edge(pos)
        menu = QMenu(self)
        if node is not None:
            edit_action = menu.addAction("编辑角色")
            retire_action = menu.addAction("标记退场")
            menu.addSeparator()
            add_action = menu.addAction("新增角色")
            action = menu.exec(event.globalPos())
            if action == edit_action:
                self.characterEditRequested.emit(node.name)
            elif action == retire_action:
                self.characterRetireRequested.emit(node.name)
            elif action == add_action:
                self.characterAddRequested.emit()
            return
        if edge is not None:
            edit_rel_action = menu.addAction("编辑关系")
            remove_rel_action = menu.addAction("移除关系")
            action = menu.exec(event.globalPos())
            if action == edit_rel_action:
                self.relationshipEditRequested.emit(edge.src.name, edge.dst.name)
            elif action == remove_rel_action:
                self.relationshipRemoveRequested.emit(edge.src.name, edge.dst.name)
            return
        add_action = menu.addAction("新增角色")
        action = menu.exec(event.globalPos())
        if action == add_action:
            self.characterAddRequested.emit()

    def _point_near_segment(
        self, pt: QPointF, edge: _RelationshipEdge, threshold: float = 10.0
    ) -> bool:
        """Check if a point is near the line segment of an edge."""
        ax, ay = edge.src.center.x(), edge.src.center.y()
        bx, by = edge.dst.center.x(), edge.dst.center.y()
        px, py = pt.x(), pt.y()

        dx, dy = bx - ax, by - ay
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq < 1:
            return False

        t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
        proj_x = ax + t * dx
        proj_y = ay + t * dy
        dist = math.hypot(px - proj_x, py - proj_y)
        return dist <= threshold

    def _show_tooltip(self, pos: QPointF) -> None:
        if self._hovered_node:
            node = self._hovered_node
            meta_parts = [_role_label_for_display(node.role, node.gender)]
            age = _format_age_display(node.age)
            if age:
                meta_parts.append(age)
            lines = [
                f"<div style='font-weight:700;font-size: 11pt;color:[[nf:text.heading.deep]];'>{_esc(node.name)}</div>",
                f"<div style='color:[[nf:accent.deep]];font-weight:700;margin:2px 0 6px;'>"
                f"{_esc(' · '.join(part for part in meta_parts if part))}</div>",
            ]
            for label, value, limit in (
                ("性格", node.personality or node.appearance, 54),
                ("弧光", node.arc or node.backstory, 54),
            ):
                text = _shorten_text(value, limit)
                if text:
                    lines.append(
                        "<div style='margin-top:4px;'>"
                        f"<span style='color:[[nf:text.muted.strong]];font-weight:700;'>{label}</span>："
                        f"{_esc(text)}</div>"
                    )
            if node.relationships:
                rel_items = list(node.relationships.items())
                lines.append(
                    "<div style='margin-top:7px;color:[[nf:text.muted.strong]];font-weight:700;'>关系网络</div>"
                )
                for target, rel in rel_items[:2]:
                    lines.append(
                        "<div style='margin-left:6px;color:[[nf:text.body]];'>"
                        f"→ {_esc(target)}：{_esc(_shorten_text(rel, 28))}</div>"
                    )
                if len(rel_items) > 2:
                    lines.append(
                        f"<div style='margin-left:6px;color:[[nf:text.muted]];'>另有 {len(rel_items) - 2} 条关系</div>"
                    )

            tooltip_html = (
                "<div style='width:276px; max-width:276px; font-family:Songti SC, STSong, SimSun; "
                "font-size: 10pt; line-height:1.45; padding:1px; white-space:normal;'>"
                f"{''.join(lines)}</div>"
            )
            global_pos = self.mapToGlobal(pos.toPoint())
            self._show_popup(tooltip_html, global_pos)
        elif self._hovered_edge:
            edge = self._hovered_edge
            # Show both directions of the relationship
            lines = [
                f"<div style='font-weight:700;color:[[nf:text.heading.deep]];'>{_esc(edge.src.name)} → {_esc(edge.dst.name)}</div>",
                f"<div style='color:[[nf:relation.ally]];font-weight:700;margin:2px 0 6px;'>"
                f"{_esc(_RELATION_TONE_LABELS.get(edge.tone, '关系'))}"
                f" · 图层：{_esc(edge.edge_type)}</div>",
                f"<div>{_esc(_shorten_text(edge.label, 96))}</div>",
            ]
            # Find reverse relationship
            reverse_rel = edge.dst.relationships.get(edge.src.name, "")
            if reverse_rel:
                lines.append(
                    f"<div style='font-weight:700;color:[[nf:text.heading.deep]];margin-top:7px;'>"
                    f"{_esc(edge.dst.name)} → {_esc(edge.src.name)}</div>"
                )
                lines.append(f"<div>{_esc(_shorten_text(reverse_rel, 96))}</div>")

            tooltip_html = (
                "<div style='width:244px; max-width:244px; font-family:Songti SC, STSong, SimSun; "
                "font-size: 10pt; line-height:1.45; padding:1px; white-space:normal;'>"
                f"{''.join(lines)}</div>"
            )
            global_pos = self.mapToGlobal(pos.toPoint())
            self._show_popup(tooltip_html, global_pos)
        else:
            self._hide_popup()

    def _show_popup(self, html: str, global_pos: QPoint) -> None:
        """Display (or update) the hover popup with ``html`` at ``global_pos``.

        Lazily creates ``self._active_tooltip`` on first use.  Re-using
        the same instance avoids the layout / focus churn of destroying
        and re-creating the popup on every ``mouseMoveEvent``.
        """
        popup = self._active_tooltip
        if popup is None:
            popup = _CharacterTooltipPopup(self)
            self._active_tooltip = popup
        popup.set_content(html)
        popup.show_at(global_pos)

    def _hide_popup(self) -> None:
        """Hide and discard the hover popup (no-op if not yet created)."""
        popup = self._active_tooltip
        if popup is None:
            return
        popup.hide()
        popup.deleteLater()
        self._active_tooltip = None

    def leaveEvent(self, event: QEvent) -> None:
        self._hovered_node = None
        self._hovered_edge = None
        self._snap_target = None
        self._dragging_edge = None
        self._sync_animation_state()
        self.update()
        self._hide_popup()

    def hideEvent(self, event: QHideEvent) -> None:
        # Ensure the tooltip popup is torn down when the graph is hidden
        # (e.g. tab switch, window minimize) so it does not linger.
        self._hovered_node = None
        self._hovered_edge = None
        self._selected_node = None
        self._selected_edge = None
        self._snap_target = None
        self._dragging_edge = None
        if self._animation_timer.isActive():
            self._animation_timer.stop()
        self._hide_popup()
        super().hideEvent(event)
