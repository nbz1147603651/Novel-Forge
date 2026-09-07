"""Sub-module of novel_forge.desktop.pages.document_renderer.

Auto-generated in the M3.2 split. Contains character_bible.py renderers.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QKeyEvent,
    QMouseEvent,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from novel_forge.core.domain.character_identity import (
    clean_character_name,
    normalize_character_role,
    parenthetical_base_name,
)

# Late imports — placed after stdlib imports to avoid circular imports
# between sibling sub-modules.
from novel_forge.desktop.pages.document_renderer.character_graph import (
    CharacterGraphWidget,
    _format_age_display,
    _role_label_for_display,
    _status_label_for_display,
)
from novel_forge.desktop.pages.document_renderer.relationship_matrix import (
    _shorten_text,
)

JsonDict = dict[str, Any]
FeedbackMarker = tuple[float, float, float, JsonDict, JsonDict]
WeaveLine = tuple[float, float, float, JsonDict, JsonDict]
DocumentRenderer = Callable[[dict[str, Any]], QWidget]


def render_character_bible(data: JsonDict, *, entity_graph: JsonDict | None = None) -> QWidget:
    """Build a composite widget: character list on left, relationship graph on right."""
    characters = _coalesce_character_profiles_for_display(data.get("characters", []))
    if not characters:
        label = QLabel("角色数据为空。")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return label

    from PySide6.QtWidgets import QSplitter

    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setObjectName("docSplitter")
    splitter.setHandleWidth(1)
    splitter.setChildrenCollapsible(False)

    # ── Left: character card list ──
    left = QWidget()
    left.setMinimumWidth(220)
    left_layout = QVBoxLayout(left)
    left_layout.setContentsMargins(0, 0, 0, 0)
    left_layout.setSpacing(0)

    cards_scroll = QScrollArea()
    cards_scroll.setWidgetResizable(True)
    cards_scroll.setFrameShape(cards_scroll.Shape.NoFrame)
    cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    cards_scroll.setObjectName("docTransparentScroll")

    cards_widget = QWidget()
    cards_layout = QVBoxLayout(cards_widget)
    cards_layout.setContentsMargins(10, 8, 6, 8)
    cards_layout.setSpacing(8)

    character_cards: list[_CollapsibleCharacterCard] = []
    for ch in characters:
        card = _build_character_card(ch)
        if isinstance(card, _CollapsibleCharacterCard):
            character_cards.append(card)
        cards_layout.addWidget(card)
    cards_layout.addStretch()

    cards_scroll.setWidget(cards_widget)
    left_layout.addWidget(cards_scroll, 1)

    splitter.addWidget(left)

    # ── Right: relationship graph ──
    right = QWidget()
    right_layout = QVBoxLayout(right)
    right_layout.setContentsMargins(0, 0, 0, 0)
    right_layout.setSpacing(0)

    graph = CharacterGraphWidget(characters, entity_graph=entity_graph)
    graph.setMinimumHeight(400)
    right_layout.addWidget(graph, 1)

    focus_label = QLabel("点击角色卡或图谱节点，可聚焦一跳关系；点击空白处清除焦点")
    focus_label.setObjectName("charGraphFocus")
    focus_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    right_layout.addWidget(focus_label)

    def _on_graph_focus_changed(name: str) -> None:
        focus_label.setText(
            f"当前聚焦：{name} 的直接关系"
            if name
            else "点击角色卡或图谱节点，可聚焦一跳关系；点击空白处清除焦点"
        )
        for card in character_cards:
            card.set_selected(card.character_name == name)

    graph.focusChanged.connect(_on_graph_focus_changed)
    for card in character_cards:
        card.focusRequested.connect(graph.focus_character)

    hint = QLabel("颜色=关系语义，流光=当前焦点；实线=人物关系，虚线/点线=身份、别名或语义链接")
    hint.setObjectName("charGraphHint")
    hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
    right_layout.addWidget(hint)

    splitter.addWidget(right)

    splitter.setStretchFactor(0, 2)
    splitter.setStretchFactor(1, 3)

    layout.addWidget(splitter, 1)

    return container


def _build_character_card(ch: JsonDict) -> QWidget:
    """Build a character card with a stable summary and expandable details."""
    return _CollapsibleCharacterCard(ch)


class _CollapsibleCharacterCard(QWidget):
    """Compact character card whose full profile opens on click."""

    focusRequested = Signal(str)

    def __init__(self, ch: JsonDict) -> None:
        super().__init__()
        self._character = ch
        self._expanded = False
        self._toggle_label: QLabel | None = None
        self._details: QWidget | None = None
        self.character_name = clean_character_name(ch.get("name", "未知")) or "未知"

        role = normalize_character_role(ch.get("role", "supporting"))
        self.setObjectName("charCard")
        self.setProperty("role", role)
        self.setProperty("expanded", False)
        self.setProperty("selected", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        layout.addLayout(self._build_header())

        summary = _build_character_summary(ch)
        if summary:
            summary_label = self._make_label(summary, "charCardSummary")
            summary_label.setToolTip(summary)
            layout.addWidget(summary_label)

        self._details = self._build_details()
        self._details.setVisible(False)
        layout.addWidget(self._details)

    def _build_header(self) -> QHBoxLayout:
        ch = self._character
        name = self.character_name
        role = normalize_character_role(ch.get("role", "supporting"))
        role_label = _role_label_for_display(role, ch.get("gender", ""))

        header = QHBoxLayout()
        header.setSpacing(8)

        self._toggle_label = self._make_label("▸", "charCardToggle")
        self._toggle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._toggle_label.setFixedWidth(14)
        header.addWidget(self._toggle_label)

        name_label = self._make_label(name, "charCardName")
        name_label.setWordWrap(True)
        header.addWidget(name_label)

        badge = self._make_label(role_label, "charCardRoleBadge")
        badge.setProperty("role", role)
        header.addWidget(badge)

        age_text = _format_age_display(ch.get("age", ""))
        if age_text:
            age_label = self._make_label(age_text, "charCardMeta")
            header.addWidget(age_label)

        gender = ch.get("gender", "")
        if gender:
            gender_label = self._make_label(str(gender), "charCardMeta")
            header.addWidget(gender_label)

        status = ch.get("status", "active")
        status_label = _status_label_for_display(status)
        if status_label:
            status_badge = self._make_label(status_label, "charCardStatusBadge")
            status_badge.setProperty("status", status)
            header.addWidget(status_badge)

        header.addStretch()
        return header

    def _build_details(self) -> QWidget:
        detail = QWidget()
        detail.setObjectName("charCardDetails")
        layout = QVBoxLayout(detail)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(5)

        fields = (
            ("身份", self._character.get("social_status")),
            ("能力", self._character.get("abilities")),
            ("外貌", self._character.get("appearance")),
            ("性格", self._character.get("personality")),
            ("背景", self._character.get("backstory")),
            ("弧光", self._character.get("arc")),
            ("备注", self._character.get("notes")),
        )
        for title, value in fields:
            text = " ".join(str(value or "").split())
            if not text:
                continue
            label = self._make_label(f"{title}：{text}", "charCardDetailLine")
            layout.addWidget(label)

        relationships = self._character.get("relationships")
        if isinstance(relationships, dict) and relationships:
            rel_title = self._make_label("关系：", "charCardDetailTitle")
            layout.addWidget(rel_title)
            for target, rel in relationships.items():
                rel_text = " ".join(str(rel or "").split())
                if not rel_text:
                    continue
                rel_label = self._make_label(
                    f"→ {clean_character_name(target) or target}：{rel_text}",
                    "charCardRelationLine",
                )
                layout.addWidget(rel_label)

        return detail

    def _make_label(self, text: str, object_name: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName(object_name)
        label.setWordWrap(True)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        return label

    def _toggle_expanded(self) -> None:
        self._expanded = not self._expanded
        if self._details is not None:
            self._details.setVisible(self._expanded)
        if self._toggle_label is not None:
            self._toggle_label.setText("▾" if self._expanded else "▸")
        self.setProperty("expanded", self._expanded)
        self.style().unpolish(self)
        self.style().polish(self)
        self.updateGeometry()

    def set_selected(self, selected: bool) -> None:
        if bool(self.property("selected")) == selected:
            return
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.focusRequested.emit(self.character_name)
            self._toggle_expanded()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.focusRequested.emit(self.character_name)
            self._toggle_expanded()
            event.accept()
            return
        super().keyPressEvent(event)


def _build_character_summary(ch: JsonDict) -> str:
    """Return a short always-visible profile summary for a character card."""
    parts: list[str] = []
    for title, key in (("外貌", "appearance"), ("性格", "personality"), ("背景", "backstory")):
        text = _shorten_text(ch.get(key), 48)
        if text:
            parts.append(f"{title}：{text}")
        if len(parts) >= 2:
            break
    if not parts:
        arc = _shorten_text(ch.get("arc"), 110)
        if arc:
            parts.append(f"弧光：{arc}")
    if not parts:
        notes = _shorten_text(ch.get("notes"), 110)
        if notes:
            parts.append(f"备注：{notes}")
    return _shorten_text(" · ".join(parts), 150)


def _format_node_name(name: str) -> str:
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


def _merge_display_character(target: JsonDict, incoming: JsonDict, *, alias: str | None) -> None:
    if alias:
        aliases = list(target.get("aliases") or [])
        if alias not in aliases:
            aliases.append(alias)
        target["aliases"] = aliases

    for key in (
        "age",
        "gender",
        "status",
        "time_layer",
        "social_status",
        "abilities",
        "appearance",
        "personality",
        "backstory",
        "arc",
        "notes",
    ):
        if not str(target.get(key, "") or "").strip() and str(incoming.get(key, "") or "").strip():
            target[key] = incoming.get(key)

    target_rels = target.setdefault("relationships", {})
    incoming_rels = incoming.get("relationships", {})
    if isinstance(target_rels, dict) and isinstance(incoming_rels, dict):
        for rel_name, rel_desc in incoming_rels.items():
            clean_target = clean_character_name(rel_name)
            if clean_target and clean_target not in target_rels:
                target_rels[clean_target] = str(rel_desc or "").strip()


def _coalesce_character_profiles_for_display(raw_characters: Any) -> list[JsonDict]:
    """Coalesce stage-qualified duplicate profiles for the viewer only."""
    if not isinstance(raw_characters, list):
        return []
    characters = [dict(item) for item in raw_characters if isinstance(item, dict)]
    merged: dict[str, JsonDict] = {}
    order: list[str] = []
    name_aliases: dict[str, str] = {}

    for character in characters:
        raw_name = clean_character_name(character.get("name"))
        if not raw_name:
            continue
        base = parenthetical_base_name(raw_name)
        canonical = base or raw_name
        name_aliases[raw_name] = canonical
        if canonical not in merged:
            entry = dict(character)
            entry["name"] = canonical
            entry["role"] = normalize_character_role(entry.get("role"))
            if raw_name != canonical:
                entry["aliases"] = [raw_name]
            merged[canonical] = entry
            order.append(canonical)
        else:
            _merge_display_character(
                merged[canonical],
                character,
                alias=raw_name if raw_name != canonical else None,
            )

    for character in merged.values():
        relationships = character.get("relationships", {})
        if not isinstance(relationships, dict):
            character["relationships"] = {}
            continue
        rewritten: dict[str, str] = {}
        for rel_name, rel_desc in relationships.items():
            clean_target = clean_character_name(rel_name)
            target = name_aliases.get(clean_target, clean_target)
            if target and target != character["name"]:
                rewritten.setdefault(target, str(rel_desc or "").strip())
        character["relationships"] = rewritten

    return [merged[name] for name in order]
