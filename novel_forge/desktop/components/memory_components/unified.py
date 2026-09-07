"""Sub-module of novel_forge.desktop.components.memory_components.

Auto-generated in the M3.8 split. Contains unified.py classes.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

from PySide6.QtCore import (
    QPropertyAnimation,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

# Cross-references to sibling sub-modules (originally in a single file).
from novel_forge.desktop.components.memory_components.context import ContextOverviewCard
from novel_forge.desktop.components.memory_components.motifs import MemoryMotifCard
from novel_forge.desktop.components.memory_components.suggestions import MemorySuggestionCard
from novel_forge.desktop.components.memory_components.warnings import MemoryRepetitionWarning
from novel_forge.desktop.components.primitives import ActionButton, Badge, Surface, clear_layout
from novel_forge.desktop.theme import resolve_qcolor
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


class UnifiedMemoryPanel(QWidget):
    """Unified memory panel combining context overview, motifs, relationships, and issues."""

    chapter_warnings_clear_requested = Signal()

    TAB_SWITCH_FADE_DURATION_MS: int = 200

    def __init__(
        self,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("memoryPanelRoot")
        self._tab_fade_anim: QPropertyAnimation | None = None
        self._build_ui()

    def _build_ui(self) -> None:

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tab_widget = QTabWidget()
        self._tab_widget.setObjectName("memoryTabs")
        configure_tab_widget_density(self._tab_widget, "compact")
        self._tab_widget.tabBar().setExpanding(True)
        layout.addWidget(self._tab_widget)

        self._overview_tab = QWidget()
        self._motifs_tab = QWidget()
        self._relations_tab = QWidget()
        self._issues_tab = QWidget()
        self._reading_power_tab = QWidget()
        self._guardrail_tab = QWidget()
        self._story_control_tab = QWidget()
        self._overview_tab.setObjectName("memoryTabPage")
        self._motifs_tab.setObjectName("memoryTabPage")
        self._relations_tab.setObjectName("memoryTabPage")
        self._issues_tab.setObjectName("memoryTabPage")
        self._reading_power_tab.setObjectName("memoryTabPage")
        self._guardrail_tab.setObjectName("memoryTabPage")
        self._story_control_tab.setObjectName("memoryTabPage")

        self._tab_widget.addTab(self._overview_tab, "概览")
        self._tab_widget.addTab(self._motifs_tab, "母题")
        self._tab_widget.addTab(self._relations_tab, "关系")
        self._tab_widget.addTab(self._issues_tab, "问题")
        self._tab_widget.addTab(self._reading_power_tab, "追读力")
        self._tab_widget.addTab(self._guardrail_tab, "护栏")
        self._tab_widget.addTab(self._story_control_tab, "控制")

        self._tab_widget.currentChanged.connect(self._on_tab_changed)

        self._build_overview_tab()
        self._build_motifs_tab()
        self._build_relations_tab()
        self._build_issues_tab()
        self._build_reading_power_tab()
        self._build_guardrail_tab()
        self._build_story_control_tab()

    def _on_tab_changed(self, index: int) -> None:
        """Fade in the newly-active tab page over 200ms.

        D1-safe (opacity-only).  Skips index -1 (no tab active) and
        suppresses the initial signal by guarding on a None check.
        """
        if index < 0:
            return
        page = self._tab_widget.widget(index)
        if page is None:
            return
        from novel_forge.desktop.motion import Motion

        if self._tab_fade_anim is not None:
            Motion.stop_safely(self._tab_fade_anim)
            self._tab_fade_anim = None

        anim = Motion.fade_in(
            page,
            duration=self.TAB_SWITCH_FADE_DURATION_MS,
            easing="standard",
            delete_when_stopped=False,
        )
        anim.finished.connect(lambda _anim=anim: self._clear_tab_fade_anim(_anim))
        self._tab_fade_anim = anim

    def _clear_tab_fade_anim(self, anim: QPropertyAnimation) -> None:
        if self._tab_fade_anim is anim:
            self._tab_fade_anim = None

    def _build_overview_tab(self) -> None:
        layout = QVBoxLayout(self._overview_tab)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        self._checkpoint_card = ContextOverviewCard("待决策节点", highlight=True)
        layout.addWidget(self._checkpoint_card)

        self._carry_forward_card = ContextOverviewCard("承接要点")
        layout.addWidget(self._carry_forward_card)

        self._suggestions_card = ContextOverviewCard("后续提示")
        layout.addWidget(self._suggestions_card)

        self._unresolved_card = ContextOverviewCard("全书悬念")
        self._unresolved_card.setVisible(False)
        layout.addWidget(self._unresolved_card)

        layout.addWidget(self._make_separator())

        self._scores_card = ContextOverviewCard("评分")
        layout.addWidget(self._scores_card)

        layout.addStretch()

    def _build_motifs_tab(self) -> None:
        layout = QVBoxLayout(self._motifs_tab)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        motifs_scroll = QScrollArea()
        motifs_scroll.setObjectName("memoryTabScroll")
        motifs_scroll.setWidgetResizable(True)
        motifs_scroll.setFrameShape(QFrame.Shape.NoFrame)
        motifs_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        motifs_content = QWidget()
        motifs_content.setObjectName("memoryPanelContent")
        motifs_content_layout = QVBoxLayout(motifs_content)
        motifs_content_layout.setContentsMargins(0, 0, 0, 0)
        motifs_content_layout.setSpacing(8)

        motifs_title = QLabel("已提取母题")
        motifs_title.setObjectName("memoryMeta")
        motifs_content_layout.addWidget(motifs_title)

        self._motifs_layout = QVBoxLayout()
        self._motifs_layout.setContentsMargins(0, 0, 0, 0)
        self._motifs_layout.setSpacing(6)
        motifs_content_layout.addLayout(self._motifs_layout)

        suggestion_title = QLabel("建议与提醒")
        suggestion_title.setObjectName("memoryMeta")
        motifs_content_layout.addWidget(suggestion_title)

        self._suggestions_layout = QVBoxLayout()
        self._suggestions_layout.setContentsMargins(0, 0, 0, 0)
        self._suggestions_layout.setSpacing(6)
        motifs_content_layout.addLayout(self._suggestions_layout)
        motifs_content_layout.addStretch()

        motifs_scroll.setWidget(motifs_content)
        layout.addWidget(motifs_scroll, 1)

        # Row 1: repair button on its own line
        motif_btn_row = QHBoxLayout()
        motif_btn_row.setSpacing(6)

        self._motif_repair_btn = ActionButton("修补母题历史", variant="secondary")
        self._motif_repair_btn.setObjectName("memorySmallButton")
        self._motif_repair_btn.setToolTip(
            "一次性回填历史章节的母题统计，修复“第 n 章后冻结/待命不变”等旧数据问题。\n"
            "仅修补记忆数据，不会改动正文内容。"
        )
        motif_btn_row.addWidget(self._motif_repair_btn)
        motif_btn_row.addStretch()
        layout.addLayout(motif_btn_row)

        # Row 2: lookback range control on a separate line
        lookback_row = QHBoxLayout()
        lookback_row.setSpacing(6)

        lookback_label = QLabel("显示范围：前")
        lookback_label.setObjectName("memoryMeta")
        lookback_row.addWidget(lookback_label)

        self._motif_lookback_spin = QSpinBox()
        self._motif_lookback_spin.setRange(0, 20)
        self._motif_lookback_spin.setValue(2)
        self._motif_lookback_spin.setSuffix("章")
        self._motif_lookback_spin.setObjectName("memorySmallButton")
        lookback_row.addWidget(self._motif_lookback_spin)

        lookback_tail = QLabel("+ 本章")
        lookback_tail.setObjectName("memoryMeta")
        lookback_row.addWidget(lookback_tail)

        lookback_row.addStretch()
        layout.addLayout(lookback_row)

        self._motif_repair_hint = QLabel("")
        self._motif_repair_hint.setObjectName("memoryHint")
        self._motif_repair_hint.setWordWrap(True)
        self._motif_repair_hint.setVisible(False)
        layout.addWidget(self._motif_repair_hint)

    def _build_relations_tab(self) -> None:
        layout = QVBoxLayout(self._relations_tab)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        relations_title = QLabel("角色关系")
        relations_title.setObjectName("memoryMeta")
        layout.addWidget(relations_title)

        self._relations_browser = QTextBrowser()
        self._relations_browser.setObjectName("relationsContent")
        self._relations_browser.setFrameShape(QFrame.Shape.NoFrame)
        self._relations_browser.setOpenExternalLinks(False)
        self._relations_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._relations_browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._relations_browser.document().setDocumentMargin(4)
        # Lazy import to break the components -> pages -> widgets -> components
        # circular import chain.  ``IncrementalDocumentRenderer`` is only
        # needed when the relations browser is initialised.
        from novel_forge.desktop.pages.document_renderer.incremental import (
            IncrementalDocumentRenderer,
        )

        self._relations_renderer = IncrementalDocumentRenderer(self._relations_browser)
        layout.addWidget(self._relations_browser, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._relations_action_btn = ActionButton("查看全局关系 →", variant="secondary")
        self._relations_action_btn.setObjectName("memorySmallButton")
        btn_row.addWidget(self._relations_action_btn)

        self._relations_extract_btn = ActionButton("重新提取关系", variant="secondary")
        self._relations_extract_btn.setObjectName("memorySmallButton")
        self._relations_extract_btn.setToolTip(
            "重新运行关系提取，更新 canon 中的关系数据。\n可选择仅提取当前章节或全部已完成章节。"
        )
        btn_row.addWidget(self._relations_extract_btn)

        layout.addLayout(btn_row)

        self._relations_extract_hint = QLabel("")
        self._relations_extract_hint.setObjectName("memoryHint")
        self._relations_extract_hint.setWordWrap(True)
        self._relations_extract_hint.setVisible(False)
        layout.addWidget(self._relations_extract_hint)

    def _build_story_control_tab(self) -> None:
        from novel_forge.desktop.pages.document_renderer.incremental import (
            IncrementalDocumentRenderer,
        )

        layout = QVBoxLayout(self._story_control_tab)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        title = QLabel("剧情控制")
        title.setObjectName("memoryMeta")
        layout.addWidget(title)

        self._story_control_browser = QTextBrowser()
        self._story_control_browser.setObjectName("relationsContent")
        self._story_control_browser.setFrameShape(QFrame.Shape.NoFrame)
        self._story_control_browser.setOpenExternalLinks(False)
        self._story_control_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._story_control_browser.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._story_control_browser.document().setDocumentMargin(4)
        self._story_control_renderer = IncrementalDocumentRenderer(self._story_control_browser)
        layout.addWidget(self._story_control_browser, 1)

    def _build_issues_tab(self) -> None:
        layout = QVBoxLayout(self._issues_tab)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        warning_header = QHBoxLayout()
        warning_header.setContentsMargins(0, 0, 0, 0)
        warning_header.setSpacing(6)

        self._chapter_warning_title = QLabel("章节提醒")
        self._chapter_warning_title.setObjectName("memoryWarningTitle")
        warning_header.addWidget(self._chapter_warning_title)
        warning_header.addStretch()

        self._chapter_warning_clear_btn = ActionButton("清理", variant="secondary")
        self._chapter_warning_clear_btn.setObjectName("memoryWarningClearBtn")
        self._chapter_warning_clear_btn.setToolTip(
            "隐藏当前章节已查看的提醒；当提醒内容变化时会再次显示。"
        )
        self._chapter_warning_clear_btn.clicked.connect(self.chapter_warnings_clear_requested.emit)
        warning_header.addWidget(self._chapter_warning_clear_btn)
        layout.addLayout(warning_header)

        self._chapter_warning_audit_hint = QLabel("")
        self._chapter_warning_audit_hint.setObjectName("memoryHint")
        self._chapter_warning_audit_hint.setWordWrap(True)
        layout.addWidget(self._chapter_warning_audit_hint)

        self._chapter_warning_scroll = QScrollArea()
        self._chapter_warning_scroll.setObjectName("memoryTabScroll")
        self._chapter_warning_scroll.setWidgetResizable(True)
        self._chapter_warning_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._chapter_warning_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        warning_content = QWidget()
        warning_content.setObjectName("memoryPanelContent")
        self._chapter_warning_layout = QVBoxLayout(warning_content)
        self._chapter_warning_layout.setContentsMargins(0, 0, 0, 0)
        self._chapter_warning_layout.setSpacing(6)
        self._chapter_warning_layout.addStretch()

        self._chapter_warning_scroll.setWidget(warning_content)
        layout.addWidget(self._chapter_warning_scroll, 1)

        layout.addWidget(self._make_separator())

        self._continuity_title = QLabel("连贯性问题")
        self._continuity_title.setObjectName("memoryWarningTitle")
        layout.addWidget(self._continuity_title)

        self._continuity_scroll = QScrollArea()
        self._continuity_scroll.setObjectName("memoryTabScroll")
        self._continuity_scroll.setWidgetResizable(True)
        self._continuity_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._continuity_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        continuity_content = QWidget()
        continuity_content.setObjectName("memoryPanelContent")
        self._continuity_layout = QVBoxLayout(continuity_content)
        self._continuity_layout.setContentsMargins(0, 0, 0, 0)
        self._continuity_layout.setSpacing(6)
        self._continuity_layout.addStretch()

        self._continuity_scroll.setWidget(continuity_content)
        layout.addWidget(self._continuity_scroll, 2)

        layout.addWidget(self._make_separator())

        self._causal_title = QLabel("因果链问题")
        self._causal_title.setObjectName("memoryWarningTitle")
        layout.addWidget(self._causal_title)

        self._causal_status_hint = QLabel("")
        self._causal_status_hint.setObjectName("memoryHint")
        self._causal_status_hint.setWordWrap(True)
        self._causal_status_hint.setVisible(False)
        layout.addWidget(self._causal_status_hint)

        self._causal_scroll = QScrollArea()
        self._causal_scroll.setObjectName("memoryTabScroll")
        self._causal_scroll.setWidgetResizable(True)
        self._causal_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._causal_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        causal_content = QWidget()
        causal_content.setObjectName("memoryPanelContent")
        self._causal_layout = QVBoxLayout(causal_content)
        self._causal_layout.setContentsMargins(0, 0, 0, 0)
        self._causal_layout.setSpacing(6)
        self._causal_layout.addStretch()

        self._causal_scroll.setWidget(causal_content)
        layout.addWidget(self._causal_scroll, 2)

        self._reevaluate_btn = ActionButton("重新评估", variant="secondary")
        self._reevaluate_btn.setObjectName("memorySmallButton")
        self._reevaluate_btn.setToolTip(
            "重新运行对齐、质量、连贯性、因果与追读力评估，不改动正文。\n"
            "若当前停在归档决策，还会同步刷新检查点摘要与待归档快照。"
        )
        layout.addWidget(self._reevaluate_btn)

        self._repair_btn = ActionButton("修复选中", variant="secondary")
        self._repair_btn.setObjectName("memorySmallButton")
        self._repair_btn.setToolTip(
            "「✓ 局部」：中段补丁，结尾不动，后续章节完全不受影响。\n"
            "「◧ 章末」：章末边界补丁，结合前后桥接定向改写尾段。\n"
            "「↺ 全文」：全文重写，章末可能随之改变，建议确认桥接。\n"
            "「⚠ 状态」：可能致使后续章节继承了修复前的错误状态。\n"
            "所有修复均不会使已归档的后续章节标记「已失效」。"
        )
        layout.addWidget(self._repair_btn)

        self._repair_hint = QLabel(
            "「✓ 局部」中段补丁；「◧ 章末」章末边界补丁；「↺ 全文」全文重写；「⚠ 状态」注意状态继承。均不触发失效。"
        )
        self._repair_hint.setObjectName("memoryHint")
        self._repair_hint.setWordWrap(True)
        layout.addWidget(self._repair_hint)

    def _build_guardrail_tab(self) -> None:
        from novel_forge.desktop.pages.document_renderer.incremental import (
            IncrementalDocumentRenderer,
        )

        layout = QVBoxLayout(self._guardrail_tab)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        guardrail_scroll = QScrollArea()
        guardrail_scroll.setObjectName("memoryTabScroll")
        guardrail_scroll.setWidgetResizable(True)
        guardrail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        guardrail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        guardrail_content = QWidget()
        guardrail_content.setObjectName("memoryPanelContent")
        guardrail_content_layout = QVBoxLayout(guardrail_content)
        guardrail_content_layout.setContentsMargins(0, 0, 0, 0)
        guardrail_content_layout.setSpacing(8)

        prev_title = QLabel("来自上一章的约束")
        prev_title.setObjectName("memoryReviewPrevTitle")
        guardrail_content_layout.addWidget(prev_title)

        self._prev_constraints_browser = QTextBrowser()
        self._prev_constraints_browser.setObjectName("memoryBrowser")
        self._prev_constraints_browser.setOpenExternalLinks(False)
        self._prev_constraints_browser.setMaximumHeight(200)
        self._prev_constraints_renderer = IncrementalDocumentRenderer(
            self._prev_constraints_browser
        )
        guardrail_content_layout.addWidget(self._prev_constraints_browser)

        guardrail_content_layout.addWidget(self._make_separator())

        next_title = QLabel("对下一章的护栏交接")
        next_title.setObjectName("memoryReviewPrevTitle")
        guardrail_content_layout.addWidget(next_title)

        self._next_constraints_browser = QTextBrowser()
        self._next_constraints_browser.setObjectName("memoryBrowser")
        self._next_constraints_browser.setOpenExternalLinks(False)
        self._next_constraints_browser.setMaximumHeight(200)
        self._next_constraints_renderer = IncrementalDocumentRenderer(
            self._next_constraints_browser
        )
        guardrail_content_layout.addWidget(self._next_constraints_browser)

        guardrail_content_layout.addStretch()

        guardrail_scroll.setWidget(guardrail_content)
        layout.addWidget(guardrail_scroll, 1)

    def _build_reading_power_tab(self) -> None:
        from novel_forge.desktop.pages.document_renderer.incremental import (
            IncrementalDocumentRenderer,
        )

        layout = QVBoxLayout(self._reading_power_tab)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        rp_scroll = QScrollArea()
        rp_scroll.setObjectName("memoryTabScroll")
        rp_scroll.setWidgetResizable(True)
        rp_scroll.setFrameShape(QFrame.Shape.NoFrame)
        rp_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        rp_content = QWidget()
        rp_content.setObjectName("memoryPanelContent")
        rp_content_layout = QVBoxLayout(rp_content)
        rp_content_layout.setContentsMargins(0, 0, 0, 0)
        rp_content_layout.setSpacing(8)

        self._rp_score_label = QLabel("追读力评分")
        self._rp_score_label.setObjectName("memoryReviewScoreLabel")
        rp_content_layout.addWidget(self._rp_score_label)

        self._rp_score_value = QLabel("--/10")
        self._rp_score_value.setObjectName("memoryReviewScoreValue")
        rp_content_layout.addWidget(self._rp_score_value)

        rp_content_layout.addWidget(self._make_separator())

        self._rp_hook_label = QLabel("章尾钩子")
        self._rp_hook_label.setObjectName("memoryReviewDetailLabel")
        rp_content_layout.addWidget(self._rp_hook_label)

        self._rp_hook_value = QLabel("--")
        self._rp_hook_value.setObjectName("memoryReviewDetailValue")
        self._rp_hook_value.setWordWrap(True)
        rp_content_layout.addWidget(self._rp_hook_value)

        rp_content_layout.addWidget(self._make_separator())

        self._rp_outline_match_label = QLabel("大纲匹配")
        self._rp_outline_match_label.setObjectName("memoryReviewDetailLabel")
        rp_content_layout.addWidget(self._rp_outline_match_label)

        self._rp_outline_match_value = QLabel("--")
        self._rp_outline_match_value.setObjectName("memoryReviewDetailValue")
        self._rp_outline_match_value.setWordWrap(True)
        rp_content_layout.addWidget(self._rp_outline_match_value)

        rp_content_layout.addWidget(self._make_separator())

        self._rp_suggestions_label = QLabel("改进建议")
        self._rp_suggestions_label.setObjectName("memoryReviewDetailLabel")
        rp_content_layout.addWidget(self._rp_suggestions_label)

        self._rp_suggestions_browser = QTextBrowser()
        self._rp_suggestions_browser.setObjectName("memoryBrowser")
        self._rp_suggestions_browser.setOpenExternalLinks(False)
        self._rp_suggestions_browser.setMaximumHeight(150)
        self._rp_suggestions_renderer = IncrementalDocumentRenderer(self._rp_suggestions_browser)
        rp_content_layout.addWidget(self._rp_suggestions_browser)

        rp_content_layout.addWidget(self._make_separator())

        self._rp_history_label = QLabel("钩子历史")
        self._rp_history_label.setObjectName("memoryReviewDetailLabel")
        rp_content_layout.addWidget(self._rp_history_label)

        self._rp_history_browser = QTextBrowser()
        self._rp_history_browser.setObjectName("memoryBrowser")
        self._rp_history_browser.setOpenExternalLinks(False)
        self._rp_history_browser.setMaximumHeight(100)
        self._rp_history_renderer = IncrementalDocumentRenderer(self._rp_history_browser)
        rp_content_layout.addWidget(self._rp_history_browser)

        rp_content_layout.addStretch()

        rp_scroll.setWidget(rp_content)
        layout.addWidget(rp_scroll, 1)

    def _make_separator(self) -> QFrame:
        sep = QFrame()
        sep.setObjectName("railSep")
        sep.setFixedHeight(1)
        return sep

    def update_checkpoint(self, content: str) -> None:
        self._checkpoint_card.set_content(content if content else "当前没有待处理 checkpoint。")

    def update_carry_forward(self, content: str) -> None:
        self._carry_forward_card.set_content(content if content else "暂无承接要点。")

    def update_suggestions(self, content: str) -> None:
        self._suggestions_card.set_content(content if content else "暂无后续提示。")

    def update_unresolved(self, content: str) -> None:
        """Update the 全书悬念 card; hide it when there is no content."""
        if content:
            self._unresolved_card.set_content(content)
            self._unresolved_card.setVisible(True)
        else:
            self._unresolved_card.setVisible(False)

    def update_scores(self, content: str) -> None:
        self._scores_card.set_content(content if content else "暂无评分数据。")

    def update_relations(self, content: str) -> None:
        text = content if content else "暂无关系数据。"
        html = (
            '<html><head><meta charset="utf-8">'
            "<style>"
            'body { font-family: "PingFang SC","Helvetica Neue","Arial"; font-size:12px; '
            "color:#4f4337; background:transparent; margin:0; padding:0; line-height:1.5; }"
            "b { color:#3a2d24; }"
            "</style></head>"
            f"<body>{text}</body></html>"
        )
        self._relations_renderer.update_content(html)

    def update_motifs(
        self,
        motifs: list[JsonDict],
        suggestions: list[JsonDict],
        warnings: list[JsonDict],
    ) -> None:
        clear_layout(self._motifs_layout)

        if motifs:
            for motif in motifs:
                card = MemoryMotifCard(
                    motif_id=motif.get("motif_id", ""),
                    category=motif.get("category", ""),
                    description=motif.get("description", ""),
                    occurrence_count=motif.get("occurrence_count", 0),
                    last_chapter=motif.get("last_chapter", 0),
                    status=motif.get("status", ""),
                    name=motif.get("name", ""),
                )
                self._motifs_layout.addWidget(card)
        else:
            hint = QLabel("暂无母题数据")
            hint.setObjectName("memoryHint")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._motifs_layout.addWidget(hint)

        self._motifs_layout.addStretch()

        # Populate suggestions / warnings section
        clear_layout(self._suggestions_layout)
        if suggestions:
            for s in suggestions:
                suggestion_card = MemorySuggestionCard(
                    motif_id=s.get("motif_id", ""),
                    suggestion=s.get("suggestion", ""),
                    priority=s.get("priority", "medium"),
                    motif_name=s.get("motif_name", ""),
                )
                self._suggestions_layout.addWidget(suggestion_card)

        if warnings:
            for warning in warnings:
                warning_card = MemoryRepetitionWarning(
                    motif_id=warning.get("motif_id", ""),
                    message=warning.get("message", "") or "检测到潜在重复表达",
                    severity=warning.get("severity", "medium"),
                )
                self._suggestions_layout.addWidget(warning_card)

        if not suggestions and not warnings:
            hint = QLabel("暂无母题建议或重复提醒")
            hint.setObjectName("memoryHint")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._suggestions_layout.addWidget(hint)

        self._suggestions_layout.addStretch()

    def update_guardrail(
        self,
        prev_constraints: list[str] | None = None,
        prev_compliance: list[JsonDict] | None = None,
        next_constraints: list[str] | None = None,
        next_handoff_status: str | None = None,
    ) -> None:
        prev_list = prev_constraints or []
        prev_comp = prev_compliance or []
        next_list = next_constraints or []
        handoff_status = str(next_handoff_status or "none").strip().lower()

        if prev_list:
            lines = ["<b>上一章 Plot Guard 生成的约束（本章需遵守）</b><br/>"]
            for i, constraint in enumerate(prev_list, 1):
                comp = next(
                    (c for c in prev_comp if c.get("constraint") == constraint),
                    None,
                )
                if comp:
                    status = comp.get("status", "unknown")
                    if status == "compliant":
                        color = "#2f6d4c"
                        marker = "✓"
                    elif status == "partial":
                        color = "#b65634"
                        marker = "◐"
                    else:
                        color = "#9a352b"
                        marker = "✗"
                    notes = comp.get("notes", "")
                    lines.append(f'<span style="color: {color};">{marker} {i}. {constraint}</span>')
                    if notes:
                        lines.append(
                            f'<span style="color: #8b7460; font-size: 11px;">　{notes}</span>'
                        )
                else:
                    lines.append(f"{i}. {constraint}")
            self._prev_constraints_renderer.update_content(
                '<html><head><meta charset="utf-8"></head>'
                '<body style="font-size: 12px; color: #4f4337; background: transparent; margin: 0; padding: 0; line-height: 1.6;">'
                + "<br/>".join(lines)
                + "</body></html>"
            )
        else:
            self._prev_constraints_renderer.update_content(
                '<html><head><meta charset="utf-8"></head>'
                '<body style="font-size: 12px; color: #8b7460; background: transparent; margin: 0; padding: 0;">'
                "暂无来自上一章的护栏约束。"
                "</body></html>"
            )

        if next_list:
            heading = {
                "accepted": "本章已确认的护栏约束（下一章将遵守）",
                "pending": "本章护栏建议（尚未确认，不会带入下一章）",
                "not_accepted": "本章未采纳的护栏建议（不会带入下一章）",
            }.get(handoff_status, "本章 Plot Guard 生成的约束")
            lines = [f"<b>{heading}</b><br/>"]
            for i, constraint in enumerate(next_list, 1):
                lines.append(f"{i}. {constraint}")
            self._next_constraints_renderer.update_content(
                '<html><head><meta charset="utf-8"></head>'
                '<body style="font-size: 12px; color: #4f4337; background: transparent; margin: 0; padding: 0; line-height: 1.6;">'
                + "<br/>".join(lines)
                + "</body></html>"
            )
        else:
            empty_message = {
                "accepted": "本章已确认：无需向下一章交接护栏约束。",
                "not_accepted": "本章护栏建议未被采纳，不会带入下一章。",
            }.get(handoff_status, "本章尚未生成对下一章的护栏约束。")
            self._next_constraints_renderer.update_content(
                '<html><head><meta charset="utf-8"></head>'
                '<body style="font-size: 12px; color: #8b7460; background: transparent; margin: 0; padding: 0;">'
                + empty_message
                + "</body></html>"
            )

    def update_story_control(self, content: str) -> None:
        body = content or "暂无剧情控制诊断。"
        self._story_control_renderer.update_content(
            '<html><head><meta charset="utf-8"></head>'
            '<body style="font-size: 12px; color: #4f4337; background: transparent; '
            'margin: 0; padding: 0; line-height: 1.6;">'
            f"{body}</body></html>"
        )

    def update_chapter_warnings(self, warnings: list[str]) -> None:
        clear_layout(self._chapter_warning_layout)

        warning_items = [str(item).strip() for item in (warnings or []) if str(item).strip()]
        self._chapter_warning_clear_btn.setEnabled(bool(warning_items))
        stale_report_warning = any(
            "旧版正文" in text or "未绑定正文版本" in text for text in warning_items
        )
        if not warning_items:
            self._chapter_warning_title.setText("章节提醒")
            self._chapter_warning_audit_hint.setText("审核状态：当前无章节提醒。")
            hint = QLabel("暂无章节提醒")
            hint.setObjectName("memoryHint")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._chapter_warning_layout.addWidget(hint)
            self._chapter_warning_layout.addStretch()
            return

        self._chapter_warning_title.setText(f"章节提醒（{len(warning_items)} 条）")
        if stale_report_warning:
            self._chapter_warning_audit_hint.setText(
                "审核状态：提醒尚未与当前正文重新对齐，请先执行“重新评估”。"
            )
        else:
            self._chapter_warning_audit_hint.setText(
                "审核状态：提醒已重新审核，以下为当前仍需处理的项。"
            )
        for text in warning_items:
            card = _memory_inset_card()
            row = QHBoxLayout(card)
            row.setContentsMargins(10, 8, 10, 8)
            row.setSpacing(8)

            marker = QLabel("!")
            marker.setObjectName("memoryWarningMarker")
            row.addWidget(marker, 0, Qt.AlignmentFlag.AlignTop)

            body = QLabel(text)
            body.setObjectName("memoryHint")
            body.setWordWrap(True)
            row.addWidget(body, 1)

            self._chapter_warning_layout.addWidget(card)

        self._chapter_warning_layout.addStretch()

    def update_continuity_issues(
        self,
        issues: list[JsonDict],
        checked_indices: set[int] | None = None,
        issue_type_map: dict[int, str] | None = None,
        orig_indices: list[int] | None = None,
    ) -> None:
        clear_layout(self._continuity_layout)

        if issue_type_map is None:
            issue_type_map = {}
        if checked_indices is None:
            checked_indices = set()

        if not issues:
            self._continuity_title.setText("连贯性问题")
            empty = QLabel("暂无连贯性问题")
            empty.setObjectName("memoryHint")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._continuity_layout.addWidget(empty)
            self._continuity_layout.addStretch()
            return

        self._continuity_title.setText(f"连贯性问题（{len(issues)} 条）")

        for filtered_pos, issue in enumerate(issues):
            orig_idx = orig_indices[filtered_pos] if orig_indices is not None else filtered_pos
            card = self._build_checklist_issue_card(
                issue, orig_idx, checked_indices, issue_type_map.get(orig_idx, "")
            )
            self._continuity_layout.addWidget(card)

        self._continuity_layout.addStretch()

    def update_causal_issues(
        self,
        issues: list[JsonDict],
        issue_type_map: dict[int, str] | None = None,
        checked_indices: set[int] | None = None,
        status_hint: str = "",
    ) -> None:
        clear_layout(self._causal_layout)

        if issue_type_map is None:
            issue_type_map = {}
        if checked_indices is None:
            checked_indices = set()
        hint_text = str(status_hint or "").strip()
        self._causal_status_hint.setVisible(bool(hint_text))
        self._causal_status_hint.setText(hint_text)

        if not issues:
            self._causal_title.setText("因果链问题")
            empty = QLabel("暂无因果链问题")
            empty.setObjectName("memoryHint")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._causal_layout.addWidget(empty)
            self._causal_layout.addStretch()
            return

        self._causal_title.setText(f"因果链问题（{len(issues)} 条）")

        for index, issue in enumerate(issues):
            card = self._build_causal_issue_card(
                issue, index, checked_indices, issue_type_map.get(index, "")
            )
            self._causal_layout.addWidget(card)

        self._causal_layout.addStretch()

    def update_repair_hint(self, text: str, is_warning: bool = False) -> None:
        self._repair_hint.setText(text)
        self._repair_hint.setObjectName("memoryRepairHint")
        self._repair_hint.setProperty("state", "idle" if is_warning else "running")
        self._repair_hint.style().unpolish(self._repair_hint)
        self._repair_hint.style().polish(self._repair_hint)

    def set_continuity_section_visible(self, visible: bool) -> None:
        self._continuity_title.setVisible(visible)
        self._continuity_scroll.setVisible(visible)

    def set_causal_section_visible(self, visible: bool) -> None:
        self._causal_title.setVisible(visible)
        self._causal_scroll.setVisible(visible)

    def set_repair_section_visible(self, visible: bool) -> None:
        self._repair_btn.setVisible(visible)
        self._repair_hint.setVisible(visible)

    def selected_issue_indices(self) -> list[int]:
        selected: list[int] = []
        for index in range(self._continuity_layout.count()):
            item = self._continuity_layout.itemAt(index)
            if item is None:
                continue
            card = item.widget()
            if card is None:
                continue
            for checkbox in card.findChildren(QCheckBox):
                issue_index = checkbox.property("issue_index")
                if checkbox.isChecked() and isinstance(issue_index, int):
                    selected.append(issue_index)
                break
        return selected

    def selected_causal_issue_indices(self) -> list[int]:
        selected: list[int] = []
        for index in range(self._causal_layout.count()):
            item = self._causal_layout.itemAt(index)
            if item is None:
                continue
            card = item.widget()
            if card is None:
                continue
            for checkbox in card.findChildren(QCheckBox):
                issue_index = checkbox.property("issue_index")
                if checkbox.isChecked() and isinstance(issue_index, int):
                    selected.append(issue_index)
                break
        return selected

    def selected_issue_signatures(self) -> list[str]:
        selected: list[str] = []
        for index in range(self._continuity_layout.count()):
            item = self._continuity_layout.itemAt(index)
            if item is None:
                continue
            card = item.widget()
            if card is None:
                continue
            for checkbox in card.findChildren(QCheckBox):
                sig = str(checkbox.property("issue_signature") or "")
                if checkbox.isChecked() and sig:
                    selected.append(sig)
                break
        return selected

    def selected_causal_issue_signatures(self) -> list[str]:
        selected: list[str] = []
        for index in range(self._causal_layout.count()):
            item = self._causal_layout.itemAt(index)
            if item is None:
                continue
            card = item.widget()
            if card is None:
                continue
            for checkbox in card.findChildren(QCheckBox):
                sig = str(checkbox.property("issue_signature") or "")
                if checkbox.isChecked() and sig:
                    selected.append(sig)
                break
        return selected

    def get_exhausted_issues(self, attempt_counters: dict[str, int]) -> list[str]:
        _MAX_PER_ISSUE_ATTEMPTS = 3
        exhausted: list[str] = []
        for layout in (self._continuity_layout, self._causal_layout):
            for index in range(layout.count()):
                item = layout.itemAt(index)
                if item is None:
                    continue
                card = item.widget()
                if card is None:
                    continue
                for checkbox in card.findChildren(QCheckBox):
                    sig = str(checkbox.property("issue_signature") or "")
                    if sig and int(attempt_counters.get(sig, 0) or 0) >= _MAX_PER_ISSUE_ATTEMPTS:
                        exhausted.append(sig)
                    break
        return exhausted

    def update_reading_power(
        self,
        score: float | None = None,
        hook_type: str = "",
        hook_strength: str = "",
        hook_description: str = "",
        outline_hook_match: JsonDict | None = None,
        outline_payoff_coverage: JsonDict | None = None,
        suggestions: list[str] | None = None,
        hook_history: list[str] | None = None,
    ) -> None:
        if score is not None:
            if score < 5.0:
                grade = "fail"
            elif score < 6.0:
                grade = "warn"
            else:
                grade = "pass"
            self._rp_score_value.setText(f"{score:.1f}/10")
            self._rp_score_value.setProperty("grade", grade)
            self._rp_score_value.style().unpolish(self._rp_score_value)
            self._rp_score_value.style().polish(self._rp_score_value)
        else:
            self._rp_score_value.setText("--/10")
            self._rp_score_value.setProperty("grade", "none")
            self._rp_score_value.style().unpolish(self._rp_score_value)
            self._rp_score_value.style().polish(self._rp_score_value)

        hook_text = f"{hook_type} ({hook_strength})" if hook_type else "--"
        if hook_description:
            hook_text += f"\n{hook_description}"
        self._rp_hook_value.setText(hook_text if hook_text != "--" else "暂无钩子数据")

        outline_text = "--"
        if outline_hook_match:
            match_type = outline_hook_match.get("match_type", "different")
            matched = outline_hook_match.get("matched", False)
            reason = outline_hook_match.get("reason", "")
            outline_text = f"钩子匹配: {match_type}" if not matched else f"钩子匹配: ✓ {match_type}"
            if reason:
                outline_text += f"\n{reason}"
        if outline_payoff_coverage:
            coverage_ratio = outline_payoff_coverage.get("coverage_ratio", 0.0)
            covered = outline_payoff_coverage.get("covered_count", 0)
            total = outline_payoff_coverage.get("total_expected", 0)
            missing = outline_payoff_coverage.get("missing", [])
            outline_text += f"\n微兑现覆盖: {covered}/{total} ({coverage_ratio:.0%})"
            if missing:
                outline_text += f"\n缺失: {', '.join(missing[:3])}"
        self._rp_outline_match_value.setText(
            outline_text if outline_text != "--" else "暂无大纲匹配数据"
        )

        suggestions_html = ""
        if suggestions:
            suggestions_html = "<ul style='margin:0; padding-left:16px;'>"
            for s in suggestions:
                suggestions_html += f"<li style='margin-bottom:4px;'>{s}</li>"
            suggestions_html += "</ul>"
        else:
            _tm = resolve_qcolor("text.muted")
            suggestions_html = f"<span style='color:{_tm.name()};'>暂无改进建议</span>"
        _tb = resolve_qcolor("text.body")
        self._rp_suggestions_renderer.update_content(
            f'<html><head><meta charset="utf-8">'
            f"<style>body {{ font-family: 'PingFang SC','Helvetica Neue','Arial'; font-size:11px; color:{_tb.name()}; background:transparent; margin:0; padding:0; }}</style></head>"
            f"<body>{suggestions_html}</body></html>"
        )

        history_html = ""
        if hook_history:
            _mrv = resolve_qcolor("text.memory.review.value")
            history_html = "<div style='font-size:11px;'>"
            for i, h in enumerate(hook_history[-10:], 1):
                history_html += f"<span style='color:{_mrv.name()};'>{i}. {h}</span><br/>"
            history_html += "</div>"
        else:
            _tm = resolve_qcolor("text.muted")
            history_html = f"<span style='color:{_tm.name()};'>暂无钩子历史</span>"
        _tb = resolve_qcolor("text.body")
        self._rp_history_renderer.update_content(
            f'<html><head><meta charset="utf-8">'
            f"<style>body {{ font-family: 'PingFang SC','Helvetica Neue','Arial'; font-size:11px; color:{_tb.name()}; background:transparent; margin:0; padding:0; }}</style></head>"
            f"<body>{history_html}</body></html>"
        )

    def _build_issue_card(
        self,
        issue: JsonDict,
        show_severity: bool = True,
    ) -> Surface:
        """Build a card widget for displaying an issue.

        Args:
            issue: Issue dictionary with keys: severity, summary, evidence, suggested_fix
            show_severity: Whether to show severity badge

        Returns:
            Surface widget containing the issue card
        """
        card = _memory_inset_card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        severity = issue.get("severity", "medium")

        if show_severity:
            header = QHBoxLayout()
            header.setSpacing(6)

            severity_dot = QFrame()
            severity_dot.setFixedSize(8, 8)
            severity_dot.setObjectName("memorySeverityDot")
            severity_dot.setProperty("severity", severity)
            header.addWidget(severity_dot)

            severity_label = QLabel(severity.upper())
            severity_label.setObjectName("memorySeverityLabel")
            severity_label.setProperty("severity", severity)
            header.addWidget(severity_label)

            header.addStretch()
            layout.addLayout(header)

        summary = issue.get("summary", "")
        if summary:
            summary_label = QLabel(summary)
            summary_label.setObjectName("memoryBody")
            summary_label.setWordWrap(True)
            layout.addWidget(summary_label)

        evidence = issue.get("evidence", "")
        if evidence and len(evidence) < 200:
            evidence_label = QLabel(f"证据: {evidence}")
            evidence_label.setObjectName("memoryEvidenceLabel")
            evidence_label.setWordWrap(True)
            layout.addWidget(evidence_label)

        suggested_fix = issue.get("suggested_fix", "")
        if suggested_fix:
            fix_label = QLabel(f"建议: {suggested_fix}")
            fix_label.setObjectName("memoryFixLabel")
            fix_label.setWordWrap(True)
            layout.addWidget(fix_label)

        affected = issue.get("affected_chapters", [])
        if affected:
            chapters_str = ", ".join(str(c) for c in affected[:3])
            if len(affected) > 3:
                chapters_str += "..."
            affected_label = QLabel(f"影响章节: {chapters_str}")
            affected_label.setObjectName("memoryAffectedLabel")
            layout.addWidget(affected_label)

        return card

    def _build_checklist_issue_card(
        self,
        issue: JsonDict,
        index: int,
        checked_indices: set[int],
        issue_type: str,
    ) -> Surface:
        card = _memory_inset_card()
        card_row = QHBoxLayout(card)
        card_row.setContentsMargins(10, 8, 12, 8)
        card_row.setSpacing(10)

        checkbox = QCheckBox()
        checkbox.setProperty("issue_index", index)
        checkbox.setProperty("issue_signature", _issue_signature(issue, kind="continuity"))
        checkbox.setChecked(index in checked_indices)
        card_row.addWidget(checkbox)

        dot = QLabel("·")
        dot.setObjectName("memoryMeta")
        card_row.addWidget(dot)

        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        text_col.setContentsMargins(0, 0, 0, 0)

        header_row = QHBoxLayout()
        header_row.setSpacing(6)
        header_row.setContentsMargins(0, 0, 0, 0)

        if issue_type:
            display_name = _ISSUE_TYPE_LABELS.get(issue_type, issue_type)
            type_label = QLabel(display_name)
            type_label.setObjectName("memoryIssueTag")
            header_row.addWidget(type_label)

        severity = issue.get("severity", "medium")

        severity_tones = {
            "hard": "danger",
            "high": "danger",
            "critical": "danger",
            "medium": "warning",
        }
        severity_labels = {
            "hard": "严重",
            "high": "严重",
            "critical": "严重",
            "medium": "中等",
            "low": "轻微",
        }

        severity_badge = Badge(
            severity_labels.get(severity) or severity,
            tone=severity_tones.get(severity, "default"),
        )
        severity_badge.setObjectName("memorySmallBadge")
        header_row.addWidget(severity_badge)

        if issue_type in _PATCH_SAFE_TYPES:
            safe_badge = Badge("✓ 局部", tone="success")
            safe_badge.setObjectName("memorySmallBadge")
            safe_badge.setToolTip(_DOWNSTREAM_SAFE_TOOLTIP)
            header_row.addWidget(safe_badge)
        elif issue_type in _BOUNDARY_PATCH_TYPES:
            boundary_badge = Badge("◧ 章末", tone="warning")
            boundary_badge.setObjectName("memorySmallBadge")
            boundary_badge.setToolTip(_DOWNSTREAM_BOUNDARY_TOOLTIP)
            header_row.addWidget(boundary_badge)
        elif issue_type in _STATE_INHERIT_TYPES:
            state_badge = Badge("⟳ 状态", tone="info")
            state_badge.setObjectName("memorySmallBadge")
            state_badge.setToolTip(_DOWNSTREAM_WARNING_TOOLTIP)
            header_row.addWidget(state_badge)
        else:
            fulltext_badge = Badge("全文", tone="default")
            fulltext_badge.setObjectName("memorySmallBadge")
            fulltext_badge.setToolTip(_DOWNSTREAM_FULLTEXT_TOOLTIP)
            header_row.addWidget(fulltext_badge)

        header_row.addStretch()
        text_col.addLayout(header_row)

        summary = _issue_display_summary(issue, index)
        summary_label = QLabel(summary)
        summary_label.setObjectName("memoryHint")
        summary_label.setWordWrap(True)
        text_col.addWidget(summary_label)

        card_row.addLayout(text_col, 1)
        return card

    def _build_causal_issue_card(
        self,
        issue: JsonDict,
        index: int,
        checked_indices: set[int],
        issue_type: str,
    ) -> Surface:
        card = _memory_inset_card()
        card_row = QHBoxLayout(card)
        card_row.setContentsMargins(10, 8, 12, 8)
        card_row.setSpacing(10)

        checkbox = QCheckBox()
        checkbox.setProperty("issue_index", index)
        checkbox.setProperty("issue_signature", _issue_signature(issue, kind="causal"))
        checkbox.setChecked(index in checked_indices)
        card_row.addWidget(checkbox)

        dot = QLabel("·")
        dot.setObjectName("memoryMeta")
        card_row.addWidget(dot)

        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        text_col.setContentsMargins(0, 0, 0, 0)

        header_row = QHBoxLayout()
        header_row.setSpacing(6)
        header_row.setContentsMargins(0, 0, 0, 0)

        if issue_type:
            display_name = _ISSUE_TYPE_LABELS.get(issue_type, issue_type)
            type_label = QLabel(display_name)
            type_label.setObjectName("memoryIssueTag")
            header_row.addWidget(type_label)

        severity = issue.get("severity", "medium")
        severity_tones = {
            "hard": "danger",
            "high": "danger",
            "critical": "danger",
            "medium": "warning",
        }
        severity_labels = {
            "hard": "严重",
            "high": "严重",
            "critical": "严重",
            "medium": "中等",
            "low": "轻微",
        }

        severity_badge = Badge(
            severity_labels.get(severity) or severity,
            tone=severity_tones.get(severity, "default"),
        )
        severity_badge.setObjectName("memorySmallBadge")
        header_row.addWidget(severity_badge)

        if issue_type in _PATCH_SAFE_TYPES:
            safe_badge = Badge("✓ 局部", tone="success")
            safe_badge.setObjectName("memorySmallBadge")
            safe_badge.setToolTip(_DOWNSTREAM_SAFE_TOOLTIP)
            header_row.addWidget(safe_badge)
        elif issue_type in _BOUNDARY_PATCH_TYPES:
            boundary_badge = Badge("◧ 章末", tone="warning")
            boundary_badge.setObjectName("memorySmallBadge")
            boundary_badge.setToolTip(_DOWNSTREAM_BOUNDARY_TOOLTIP)
            header_row.addWidget(boundary_badge)
        elif issue_type in _STATE_INHERIT_TYPES:
            state_badge = Badge("⚠ 状态", tone="warning")
            state_badge.setObjectName("memorySmallBadge")
            state_badge.setToolTip(_DOWNSTREAM_WARNING_TOOLTIP)
            header_row.addWidget(state_badge)
        else:
            fulltext_badge = Badge("↺ 全文", tone="info")
            fulltext_badge.setObjectName("memorySmallBadge")
            fulltext_badge.setToolTip(_DOWNSTREAM_FULLTEXT_TOOLTIP)
            header_row.addWidget(fulltext_badge)

        location = issue.get("location") or ""
        if location:
            loc_label = QLabel(location)
            loc_label.setObjectName("memoryLocLabel")
            header_row.addWidget(loc_label)

        header_row.addStretch()
        text_col.addLayout(header_row)

        summary = _issue_display_summary(issue, index)
        if summary:
            summary_label = QLabel(summary)
            summary_label.setObjectName("memoryHint")
            summary_label.setWordWrap(True)
            text_col.addWidget(summary_label)

        fix_suggestion = (
            _issue_value_text(issue.get("fix_suggestion"))
            or _issue_value_text(issue.get("suggested_fix"))
            or _issue_value_text(issue.get("repair_suggestion"))
        )
        if fix_suggestion == summary:
            fix_suggestion = ""
        if fix_suggestion:
            fix_label = QLabel(fix_suggestion)
            fix_label.setObjectName("memoryFixHintLabel")
            fix_label.setWordWrap(True)
            text_col.addWidget(fix_label)

        card_row.addLayout(text_col, 1)
        return card

    def set_empty_motifs(self) -> None:
        clear_layout(self._motifs_layout)
        empty = QLabel("暂无母题数据\n完成章节后自动提取母题")
        empty.setObjectName("memoryHint")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._motifs_layout.addWidget(empty)
        self._motifs_layout.addStretch()
        clear_layout(self._suggestions_layout)
        suggestion_empty = QLabel("暂无母题建议或重复提醒")
        suggestion_empty.setObjectName("memoryHint")
        suggestion_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._suggestions_layout.addWidget(suggestion_empty)
        self._suggestions_layout.addStretch()

    def set_empty_overview(self) -> None:
        self._checkpoint_card.set_content("当前没有待处理 checkpoint。")
        self._carry_forward_card.set_content("暂无承接要点。")
        self._suggestions_card.set_content("暂无后续提示。")
        self._scores_card.set_content("暂无评分数据。")
        self._unresolved_card.setVisible(False)

    def set_empty_relations(self) -> None:
        self._relations_browser.setPlainText("暂无关系数据。")

    def set_empty_continuity(self) -> None:
        self.update_chapter_warnings([])

        self._continuity_title.setText("连贯性问题")
        clear_layout(self._continuity_layout)
        empty = QLabel("暂无连贯性问题")
        empty.setObjectName("memoryHint")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._continuity_layout.addWidget(empty)
        self._continuity_layout.addStretch()

        self._causal_title.setText("因果链问题")
        self._causal_status_hint.setText("")
        self._causal_status_hint.setVisible(False)
        clear_layout(self._causal_layout)
        empty = QLabel("暂无因果链问题")
        empty.setObjectName("memoryHint")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._causal_layout.addWidget(empty)
        self._causal_layout.addStretch()

    def set_refreshing_continuity(self) -> None:
        """Show a 'refreshing' placeholder in the continuity issues area."""
        clear_layout(self._continuity_layout)
        self._continuity_title.setText("连贯性问题")
        self.set_continuity_section_visible(True)
        hint = QLabel("正在执行，等待刷新…")
        hint.setObjectName("memoryHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._continuity_layout.addWidget(hint)
        self._continuity_layout.addStretch()

    def set_refreshing_causal(self) -> None:
        """Show a 'refreshing' placeholder in the causal issues area."""
        clear_layout(self._causal_layout)
        self._causal_title.setText("因果链问题")
        self._causal_status_hint.setText("")
        self._causal_status_hint.setVisible(False)
        self.set_causal_section_visible(True)
        hint = QLabel("正在执行，等待刷新…")
        hint.setObjectName("memoryHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._causal_layout.addWidget(hint)
        self._causal_layout.addStretch()

    def get_checkpoint_widget(self) -> ContextOverviewCard:
        return self._checkpoint_card

    def get_carry_forward_widget(self) -> ContextOverviewCard:
        return self._carry_forward_card

    def get_suggestions_widget(self) -> ContextOverviewCard:
        return self._suggestions_card

    def get_scores_widget(self) -> ContextOverviewCard:
        return self._scores_card

    def get_relations_widget(self) -> QTextBrowser:
        return self._relations_browser

    def get_relations_action_button(self) -> ActionButton:
        return self._relations_action_btn

    def get_relations_extract_button(self) -> ActionButton:
        return self._relations_extract_btn

    def update_relations_extract_hint(self, text: str, is_warning: bool = False) -> None:
        if text:
            self._relations_extract_hint.setText(text)
            self._relations_extract_hint.setVisible(True)
            self._relations_extract_hint.setObjectName(
                "memoryHintWarning" if is_warning else "memoryHintSuccess"
            )
        else:
            self._relations_extract_hint.setVisible(False)

    def get_motif_repair_button(self) -> ActionButton:
        return self._motif_repair_btn

    def get_motif_lookback_spinbox(self) -> QSpinBox:
        return self._motif_lookback_spin

    def set_motif_lookback_chapters(self, value: int) -> None:
        normalized = max(0, min(20, int(value)))
        if self._motif_lookback_spin.value() == normalized:
            return
        self._motif_lookback_spin.blockSignals(True)
        self._motif_lookback_spin.setValue(normalized)
        self._motif_lookback_spin.blockSignals(False)

    def update_motif_repair_hint(self, text: str, is_warning: bool = False) -> None:
        if text:
            self._motif_repair_hint.setText(text)
            self._motif_repair_hint.setVisible(True)
            self._motif_repair_hint.setObjectName(
                "memoryHintWarning" if is_warning else "memoryHintSuccess"
            )
        else:
            self._motif_repair_hint.setVisible(False)

    def get_repair_button(self) -> ActionButton:
        return self._repair_btn

    def get_reevaluate_button(self) -> ActionButton:
        return self._reevaluate_btn

    def get_chapter_warnings_clear_button(self) -> ActionButton:
        return self._chapter_warning_clear_btn

    def get_continuity_layout(self) -> QVBoxLayout:
        return self._continuity_layout

    def get_causal_layout(self) -> QVBoxLayout:
        return self._causal_layout
