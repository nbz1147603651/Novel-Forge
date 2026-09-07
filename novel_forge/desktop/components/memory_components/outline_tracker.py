"""Sub-module of novel_forge.desktop.components.memory_components.

Auto-generated in the M3.8 split. Contains outline_tracker.py classes.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

from PySide6.QtCore import (
    Qt,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# Cross-references to sibling sub-modules (originally in a single file).
from novel_forge.desktop.components.memory_components.context import ContextOverviewCard
from novel_forge.desktop.components.primitives import Surface, clear_layout
from novel_forge.desktop.ui_density import configure_tab_widget_density

JsonDict = dict[str, Any]


def _stabilize_memory_surface(surface: Surface, *, hover: bool = False) -> None:
    """Make memory-panel cards stable inside scroll areas on macOS/Qt."""
    surface.setGraphicsEffect(None)  # type: ignore[arg-type]
    surface.setProperty("memoryPanelSurface", True)
    surface.setProperty("memoryHover", hover)


def _memory_inset_card(parent: QWidget | None = None, *, hover: bool = False) -> Surface:
    card = Surface("inset", parent)
    _stabilize_memory_surface(card, hover=hover)
    return card


# ── Issue type classification ────────────────────────────────────────────────

# Targeted PATCH types — only a small matched snippet inside the chapter is
# rewritten; the chapter ending and bridge contract remain untouched.
_PATCH_SAFE_TYPES: frozenset[str] = frozenset(
    {
        # ContinuityRepairStep._PATCHABLE_TYPES minus boundary types
        "opening_gap",
        "location_jump",
        "pov_jump",
        "time_marker_invalid",
        "text_repetition",
        "sensory_anchor_repetition",
        # CausalRepairStep._PATCH_ONLY_TYPES
        "address_form_mismatch",
        "opening_causal_gap",
    }
)

# Boundary WINDOW-PATCH types — uses a targeted window on the last few paragraphs
# (ContinuityRepairStep._BOUNDARY_PATCHABLE_TYPES, closing side only).
# The system looks at this chapter’s ending together with the next chapter’s
# opening bridge, then rewrites only those tail paragraphs — like a patch,
# but anchored at the chapter boundary.
_BOUNDARY_PATCH_TYPES: frozenset[str] = frozenset(
    {
        "closing_gap",
        "ending_ambiguity",
        "bridge_contract_not_followed",
    }
)

# State-inheritance types — even after targeted repair, downstream chapters may
# already have been written based on the incorrect inherited state.
_STATE_INHERIT_TYPES: frozenset[str] = frozenset(
    {
        "carry_forward_missing",  # 必承要素缺失，后续章节基于不完整状态生成
        "knowledge_contradiction",  # 角色知识状态与 Canon 矛盾，被后续章节继承
        "causal_contradiction",  # 因果矛盾在末尾影响后续推导
        "question_ignored",  # 悬念未回应，级联到后续章节
    }
)

# Union of all types that touch the chapter ending / bridge contract
# (kept for any downstream consumers that reference this set).
_CLOSING_SIDE_TYPES: frozenset[str] = (
    _BOUNDARY_PATCH_TYPES
    | _STATE_INHERIT_TYPES
    | frozenset(
        {
            "closing_contract_mismatch",
            "exit_state_gap",
            "custody_break",
            "handoff_missing",
        }
    )
)

# ── Tooltip strings ───────────────────────────────────────────────────────────

_DOWNSTREAM_SAFE_TOOLTIP = (
    "此问题仅影响本章内部片段（开头衔接等），使用局部补丁修复。\n"
    "修复不会改动章节结尾或桥接契约，\n"
    "后续已归档章节内容完全不受影响。"
)

_DOWNSTREAM_BOUNDARY_TOOLTIP = (
    "此问题使用「章末边界补丁」修复：\n"
    "系统结合前一章的退出状态和本章末尾的桥接兑约，\n"
    "定向改写结尾若干段，类似补丁但郁章尾。\n"
    "修复不会触发全文重写，也不会使下游章节失效，\n"
    "但建议修复后确认邻近章节的桥接上下文是否仍然准确。"
)

_DOWNSTREAM_FULLTEXT_TOOLTIP = (
    "此问题需要全文重写（不支持博丁）。\n"
    "修复会重新生成整章正文，章节结尾可能随之改变。\n"
    "不会使已归档后续章节失效，但如需生成新章节，\n"
    "建议确认现有桥接上下文是否仍然准确。"
)

_DOWNSTREAM_WARNING_TOOLTIP = (
    "此问题类型可能导致后续章节继承了错误状态。\n"
    "即使修复了本章，已生成的后续章节也可能是基于修复前状态写成的。\n"
    "建议修复后回顾后续章节的相关要素是否与本章修复结果一致。"
)

# Human-readable Chinese labels for raw issue_type keys.
_ISSUE_TYPE_LABELS: dict[str, str] = {
    # Structural / continuity
    "continuity_gap": "连贯缺口",
    "carry_forward_missing": "承接缺失",
    "character_inconsistency": "角色不一致",
    "timeline_conflict": "时间冲突",
    "location_inconsistency": "场景不一致",
    "plot_contradiction": "情节矛盾",
    "tone_shift": "基调偏移",
    "relationship_inconsistency": "关系不一致",
    "state_contradiction": "状态矛盾",
    "factual_error": "事实错误",
    "prompt_leak": "提词泄漏",
    "location_jump": "场景跳切",
    "custody_break": "托管中断",
    "pov_jump": "视角跳切",
    "forbidden_element_violation": "硬禁元素",
    "forbidden_element_usage": "意象复用",
    "text_repetition": "文本重复",
    "sensory_anchor_repetition": "感官重复",
    "opening_gap": "开场衔接",
    "closing_gap": "结尾衔接",
    "closing_contract_mismatch": "结尾契约",
    "information_consistency": "信息一致",
    "relationship_change_support": "关系支撑",
    "relationship_development": "关系发展",
    "bridge_contract_not_followed": "桥接契约",
    "bridge_contract_violation": "桥接契约",
    "knowledge_contradiction": "知识矛盾",
    "handoff_missing": "交接缺失",
    "ending_ambiguity": "结尾模糊",
    "exit_state_gap": "退出状态",
    "causal_contradiction": "因果矛盾",
    "question_ignored": "悬念搁置",
    # Causal
    "opening_causal_gap": "开场因果",
    "missing_causal_transition": "因果过渡",
    "event_without_cause": "无因事件",
    "unmotivated_decision": "无动机决策",
    "question_resolved_too_early": "悬念提前",
    "address_form_mismatch": "称谓不一致",
    "unmotivated_action": "动机不足",
    "missing_consequence": "缺失后果",
    "timeline_inconsistency": "时间冲突",
    "logic_gap": "逻辑断层",
    "contradiction": "前后矛盾",
    "cause_missing": "因由缺失",
    "effect_missing": "结果缺失",
    "pacing_issue": "节奏问题",
    "foreshadowing_orphan": "埋线未收",
}


def _issue_signature(issue: dict[str, Any], *, kind: str) -> str:
    signature = str(issue.get("signature") or issue.get("issue_signature") or "").strip()
    if signature:
        return signature
    issue_obj = SimpleNamespace(**issue)
    try:
        if kind == "causal":
            from novel_forge.pipeline.steps.causal_repair_step import CausalRepairStep

            return CausalRepairStep.issue_signature(issue_obj)
        from novel_forge.pipeline.steps.continuity_repair_step import ContinuityRepairStep

        return ContinuityRepairStep.issue_signature(issue_obj)
    except Exception:
        pass
    parts = [
        str(issue.get("issue_type", "") or ""),
        str(issue.get("summary", "") or ""),
        str(issue.get("location", "") or issue.get("evidence", "") or ""),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _clip_issue_text(value: str, *, limit: int = 220) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip("，。、；： \n") + "…"


def _issue_value_text(value: Any, *, limit: int = 220) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, dict):
        for key in (
            "summary",
            "description",
            "instruction",
            "rationale",
            "target_window",
            "text",
            "quote",
        ):
            text = _issue_value_text(value.get(key), limit=limit)
            if text:
                return text
        parts: list[str] = []
        for item in value.values():
            text = _issue_value_text(item, limit=80)
            if text:
                parts.append(text)
        return _clip_issue_text("；".join(parts), limit=limit)
    if isinstance(value, (list, tuple, set)):
        parts = []
        for item in value:
            text = _issue_value_text(item, limit=80)
            if text:
                parts.append(text)
        return _clip_issue_text("；".join(parts), limit=limit)
    return _clip_issue_text(str(value), limit=limit)


def _issue_display_summary(issue: dict[str, Any], index: int) -> str:
    for key in (
        "summary",
        "description",
        "diagnostic_note",
        "rationale",
        "message",
        "fix_suggestion",
        "suggested_fix",
        "repair_suggestion",
        "repair_directive",
        "missing_anchors",
        "postconditions",
        "fix_actions",
        "evidence",
        "evidence_quote",
        "location",
    ):
        text = _issue_value_text(issue.get(key))
        if text:
            return text
    return f"问题 {index + 1}"


# Compact inline badge style for issue type labels — matches motif card tag sizing.
_ISSUE_TAG_STYLE = (
    "background: #f5ede3; color: #7c6249; border-radius: 4px; padding: 1px 5px; font-size: 10px;"
)
# Compact override for Badge widgets inside issue cards.
_SMALL_BADGE_STYLE = "padding: 1px 5px; font-size: 10px; border-radius: 5px; font-weight: 600;"


class OutlineTrackerBadge(QWidget):
    """Badge displaying outline tracker status.

    Shows the number of tracked relationships, themes, and unresolved threads.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("outlineTrackerBadge")
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(4)

        self._icon = QLabel("🔗")
        self._icon.setObjectName("outlineTrackerIcon")
        layout.addWidget(self._icon)

        self._label = QLabel("大纲追踪: 0 条关系")
        self._label.setObjectName("outlineTrackerLabelDefault")
        layout.addWidget(self._label)

        layout.addStretch()

    def update_status(
        self,
        relationships: int = 0,
        themes: int = 0,
        threads: int = 0,
        unresolved: int = 0,
    ) -> None:
        parts = []
        if relationships > 0:
            parts.append(f"{relationships} 关系")
        if themes > 0:
            parts.append(f"{themes} 主题")
        if threads > 0:
            parts.append(f"{threads} 线索")
        if unresolved > 0:
            parts.append(f"{unresolved} 待解")

        if parts:
            status_text = "大纲追踪: " + " | ".join(parts)
            self._label.setText(status_text)
            if unresolved > 2:
                self.setObjectName("outlineTrackerBadgeWarning")
                self._label.setObjectName("outlineTrackerLabelWarning")
            else:
                self.setObjectName("outlineTrackerBadge")
                self._label.setObjectName("outlineTrackerLabelDefault")
        else:
            self._label.setText("大纲追踪: 未激活")
            self.setObjectName("outlineTrackerBadge")
            self._label.setObjectName("outlineTrackerLabelInactive")


class OutlineTrackerPanel(QWidget):
    """Panel displaying outline tracker information during outline generation.

    Shows:
    - Current relationship changes
    - Theme appearances
    - Key events
    - Unresolved plot threads
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("outlineTrackerContent")
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._tab_widget = QTabWidget()
        self._tab_widget.setObjectName("memoryTabs")
        configure_tab_widget_density(self._tab_widget, "compact")
        layout.addWidget(self._tab_widget)

        self._overview_tab = QWidget()
        self._relationships_tab = QWidget()
        self._themes_tab = QWidget()
        self._threads_tab = QWidget()

        self._tab_widget.addTab(self._overview_tab, "概览")
        self._tab_widget.addTab(self._relationships_tab, "关系")
        self._tab_widget.addTab(self._themes_tab, "主题")
        self._tab_widget.addTab(self._threads_tab, "悬念")

        self._build_overview_tab()
        self._build_relationships_tab()
        self._build_themes_tab()
        self._build_threads_tab()

    def _build_overview_tab(self) -> None:
        layout = QVBoxLayout(self._overview_tab)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        self._overview_relationships = ContextOverviewCard("关系变化")
        layout.addWidget(self._overview_relationships)

        self._overview_themes = ContextOverviewCard("主题出现")
        layout.addWidget(self._overview_themes)

        self._overview_events = ContextOverviewCard("关键事件")
        layout.addWidget(self._overview_events)

        self._overview_unresolved = ContextOverviewCard("待解决悬念")
        layout.addWidget(self._overview_unresolved)

        layout.addStretch()

    def _build_relationships_tab(self) -> None:
        layout = QVBoxLayout(self._relationships_tab)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("outlineTrackerContent")
        self._relationships_layout = QVBoxLayout(content)
        self._relationships_layout.setContentsMargins(0, 0, 0, 0)
        self._relationships_layout.setSpacing(6)
        self._relationships_layout.addStretch()

        scroll.setWidget(content)
        layout.addWidget(scroll)

    def _build_themes_tab(self) -> None:
        layout = QVBoxLayout(self._themes_tab)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("outlineTrackerContent")
        self._themes_layout = QVBoxLayout(content)
        self._themes_layout.setContentsMargins(0, 0, 0, 0)
        self._themes_layout.setSpacing(6)
        self._themes_layout.addStretch()

        scroll.setWidget(content)
        layout.addWidget(scroll)

    def _build_threads_tab(self) -> None:
        layout = QVBoxLayout(self._threads_tab)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("outlineTrackerContent")
        self._threads_layout = QVBoxLayout(content)
        self._threads_layout.setContentsMargins(0, 0, 0, 0)
        self._threads_layout.setSpacing(6)
        self._threads_layout.addStretch()

        scroll.setWidget(content)
        layout.addWidget(scroll)

    def update_from_summary(self, summary: JsonDict) -> None:
        """Update panel from outline tracker summary."""
        relationship_summary = summary.get("relationship_summary", "")
        themes_summary = summary.get("themes_summary", "")
        key_events_summary = summary.get("key_events_summary", "")
        unresolved_summary = summary.get("unresolved_summary", "")

        self._overview_relationships.set_content(relationship_summary)
        self._overview_themes.set_content(themes_summary)
        self._overview_events.set_content(key_events_summary)
        self._overview_unresolved.set_content(unresolved_summary)

    def update_relationships(self, relationships: list[JsonDict]) -> None:
        """Update relationships list."""
        clear_layout(self._relationships_layout)

        for rel in relationships:
            card = _memory_inset_card()
            layout = QVBoxLayout(card)
            layout.setContentsMargins(10, 8, 10, 8)
            layout.setSpacing(4)

            header = QHBoxLayout()
            header.setSpacing(6)

            char_a = rel.get("character_a", "")
            char_b = rel.get("character_b", "")
            rel_type = rel.get("relationship_type", "")

            title = QLabel(f"{char_a} ↔ {char_b}")
            title.setObjectName("memoryRelTitle")
            header.addWidget(title)

            if rel_type:
                type_label = QLabel(f"[{rel_type}]")
                type_label.setObjectName("memoryRelType")
                header.addWidget(type_label)

            header.addStretch()
            layout.addLayout(header)

            desc = rel.get("description", "")
            if desc:
                desc_label = QLabel(desc)
                desc_label.setObjectName("memoryBody")
                desc_label.setWordWrap(True)
                layout.addWidget(desc_label)

            shift_events = rel.get("shift_events", [])
            if shift_events:
                events_label = QLabel("变化: " + " | ".join(shift_events[-2:]))
                events_label.setObjectName("memoryRelEvents")
                events_label.setWordWrap(True)
                layout.addWidget(events_label)

            self._relationships_layout.addWidget(card)

        self._relationships_layout.addStretch()

    def update_themes(self, themes: list[JsonDict]) -> None:
        """Update themes list."""
        clear_layout(self._themes_layout)

        theme_counts: dict[str, list[int]] = {}
        for theme in themes:
            theme_name = theme.get("theme", "")
            chapter = theme.get("chapter", 0)
            if theme_name not in theme_counts:
                theme_counts[theme_name] = []
            theme_counts[theme_name].append(chapter)

        for theme_name, chapters in theme_counts.items():
            card = _memory_inset_card()
            layout = QVBoxLayout(card)
            layout.setContentsMargins(10, 8, 10, 8)
            layout.setSpacing(4)

            header = QHBoxLayout()
            header.setSpacing(6)

            name_label = QLabel(theme_name)
            name_label.setObjectName("memoryThemeName")
            header.addWidget(name_label)

            header.addStretch()

            count_label = QLabel(f"×{len(chapters)}")
            count_label.setObjectName("memoryThemeCount")
            header.addWidget(count_label)

            layout.addLayout(header)

            chapters_str = "第 " + ", ".join(str(c) for c in sorted(chapters)) + " 章"
            chapters_label = QLabel(chapters_str)
            chapters_label.setObjectName("memoryThemeChapters")
            layout.addWidget(chapters_label)

            self._themes_layout.addWidget(card)

        if not theme_counts:
            empty = QLabel("暂无主题数据")
            empty.setObjectName("memoryHint")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._themes_layout.addWidget(empty)

        self._themes_layout.addStretch()

    def update_threads(self, threads: list[JsonDict]) -> None:
        """Update plot threads list."""
        clear_layout(self._threads_layout)

        active_threads = [t for t in threads if t.get("status") == "active"]
        resolved_threads = [t for t in threads if t.get("status") == "resolved"]

        if active_threads:
            active_title = QLabel("进行中的悬念")
            active_title.setObjectName("memoryThreadTitleActive")
            self._threads_layout.addWidget(active_title)

            for thread in active_threads:
                card = self._build_thread_card(thread, is_active=True)
                self._threads_layout.addWidget(card)

        if resolved_threads:
            if active_threads:
                separator = QFrame()
                separator.setObjectName("railSep")
                separator.setFixedHeight(1)
                self._threads_layout.addWidget(separator)

            resolved_title = QLabel("已解决的悬念")
            resolved_title.setObjectName("memoryThreadTitleResolved")
            self._threads_layout.addWidget(resolved_title)

            for thread in resolved_threads:
                card = self._build_thread_card(thread, is_active=False)
                self._threads_layout.addWidget(card)

        if not threads:
            empty = QLabel("暂无悬念数据")
            empty.setObjectName("memoryHint")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._threads_layout.addWidget(empty)

        self._threads_layout.addStretch()

    def _build_thread_card(self, thread: JsonDict, is_active: bool = True) -> Surface:
        """Build a card for a plot thread."""
        card = _memory_inset_card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(6)

        name = thread.get("name", "")
        name_label = QLabel(name)
        name_label.setObjectName("memoryThreadName")
        header.addWidget(name_label)

        header.addStretch()

        status_label = QLabel("进行中" if is_active else "已解决")
        status_color = "#e65100" if is_active else "#7c6249"
        status_label.setObjectName("memoryMeta")
        status_label.setStyleSheet(f"color: {status_color}; font-size: 10px;")
        header.addWidget(status_label)

        layout.addLayout(header)

        introduced = thread.get("introduced_chapter", 0)
        if introduced:
            intro_label = QLabel(f"引入: 第{introduced}章")
            intro_label.setObjectName("memoryThreadIntro")
            layout.addWidget(intro_label)

        resolved = thread.get("resolved_chapter", 0)
        if resolved and not is_active:
            resolved_label = QLabel(f"解决: 第{resolved}章")
            resolved_label.setObjectName("memoryThreadResolved")
            layout.addWidget(resolved_label)

        events = thread.get("key_events", [])
        if events:
            events_label = QLabel(" | ".join(events[:2]))
            events_label.setObjectName("memoryHint")
            events_label.setWordWrap(True)
            layout.addWidget(events_label)

        return card

    def set_empty(self) -> None:
        """Reset panel to empty state."""
        self._overview_relationships.set_content("暂无关系变化记录。")
        self._overview_themes.set_content("暂无主题出现记录。")
        self._overview_events.set_content("暂无关键事件记录。")
        self._overview_unresolved.set_content("暂无待解决的悬念。")

        clear_layout(self._relationships_layout)
        empty = QLabel("暂无关系数据")
        empty.setObjectName("memoryHint")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._relationships_layout.addWidget(empty)
        self._relationships_layout.addStretch()

        clear_layout(self._themes_layout)
        empty = QLabel("暂无主题数据")
        empty.setObjectName("memoryHint")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._themes_layout.addWidget(empty)
        self._themes_layout.addStretch()

        clear_layout(self._threads_layout)
        empty = QLabel("暂无悬念数据")
        empty.setObjectName("memoryHint")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._threads_layout.addWidget(empty)
        self._threads_layout.addStretch()

