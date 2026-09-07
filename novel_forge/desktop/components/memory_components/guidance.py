"""Sub-module of novel_forge.desktop.components.memory_components.

Auto-generated in the M3.8 split. Contains guidance.py classes.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

from PySide6.QtCore import (
    Signal,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.components.primitives import Surface, clear_layout

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


class MemoryGuidanceCard(Surface):
    """Card displaying memory-guided repair insights.

    Shows:
    - Matched similar issues from history
    - Recommended strategies
    - Strategies to avoid
    - Historical success/failure records
    """

    guidance_dismissed = Signal()

    def __init__(
        self,
        guidance: dict[str, Any],
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("inset", parent)
        _stabilize_memory_surface(self)
        self._guidance = guidance
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        # Header with title and dismiss button
        header = QHBoxLayout()
        header.setSpacing(8)

        title_label = QLabel("🧠 历史修复指导")
        title_label.setObjectName("memoryGuidanceTitle")
        header.addWidget(title_label)

        header.addStretch()

        dismiss_btn = QPushButton("✕ 忽略")
        dismiss_btn.setObjectName("memoryGuidanceDismiss")
        dismiss_btn.clicked.connect(self._on_dismiss)
        header.addWidget(dismiss_btn)

        layout.addLayout(header)

        guidance = self._guidance

        # Matched issues section
        if guidance.get("matched_issues"):
            issues_label = QLabel("类似问题历史：")
            issues_label.setObjectName("memoryGuidanceIssues")
            layout.addWidget(issues_label)

            for item in guidance["matched_issues"]:
                issue_text = f"· 第{item['chapter']}章 [{item['issue_type']}]：{item['summary']}"
                issue_label = QLabel(issue_text)
                issue_label.setObjectName("memoryGuidanceIssueText")
                issue_label.setWordWrap(True)
                layout.addWidget(issue_label)

                if item.get("lesson_learned"):
                    lesson_label = QLabel(f"  → 教训：{item['lesson_learned']}")
                    lesson_label.setObjectName("memoryGuidanceLesson")
                    lesson_label.setWordWrap(True)
                    layout.addWidget(lesson_label)

        # Recommended strategies section
        if guidance.get("recommended_strategies"):
            rec_label = QLabel("推荐策略：")
            rec_label.setObjectName("memoryGuidanceRec")
            layout.addWidget(rec_label)

            for strategy in guidance["recommended_strategies"]:
                strategy_label = QLabel(f"✓ {strategy}")
                strategy_label.setObjectName("memoryGuidanceStrategy")
                strategy_label.setWordWrap(True)
                layout.addWidget(strategy_label)

        # Avoid strategies section
        if guidance.get("avoid_strategies"):
            avoid_label = QLabel("避免策略：")
            avoid_label.setObjectName("memoryGuidanceAvoid")
            layout.addWidget(avoid_label)

            for strategy in guidance["avoid_strategies"]:
                strategy_label = QLabel(f"✗ {strategy}")
                strategy_label.setObjectName("memoryGuidanceAvoidStrategy")
                strategy_label.setWordWrap(True)
                layout.addWidget(strategy_label)

        # Footer with success rate and warning
        footer = QHBoxLayout()
        footer.setSpacing(16)

        if "success_rate" in guidance:
            rate = guidance["success_rate"]
            rate_label = QLabel(f"历史成功率：{rate:.0%}")
            rate_label.setObjectName("memoryBadge")
            if rate >= 0.6:
                rate_label.setObjectName("memoryGuidanceRateHigh")
            elif rate >= 0.3:
                rate_label.setObjectName("memoryGuidanceRateMid")
            else:
                rate_label.setObjectName("memoryGuidanceRateLow")
            footer.addWidget(rate_label)

        if guidance.get("warning"):
            warning_label = QLabel(f"⚠ {guidance['warning']}")
            warning_label.setObjectName("memoryGuidanceWarning")
            warning_label.setWordWrap(True)
            footer.addWidget(warning_label)

        footer.addStretch()
        layout.addLayout(footer)

    def _on_dismiss(self) -> None:
        """Handle dismiss button click."""
        self.setVisible(False)
        self.guidance_dismissed.emit()

    def update_guidance(self, guidance: dict[str, Any]) -> None:
        """Update the guidance display with new data."""
        self._guidance = guidance
        # Clear existing layout and rebuild
        layout = self.layout()
        if layout is not None:
            clear_layout(layout)
        self._build_ui()
        self.setVisible(True)

