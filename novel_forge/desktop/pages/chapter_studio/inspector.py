"""Inspector presenters for chapter studio side-panel widgets."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from novel_forge.desktop.jobs import DesktopJobRecord
from novel_forge.desktop.pages.chapter_studio.autorun import (
    AutoPilotContext,
    build_continuity_hint,
    default_checked_issue_indices,
    should_auto_submit_repair,
)
from novel_forge.desktop.pages.chapter_studio.memory import (
    load_chapter_focus_characters,
)
from novel_forge.desktop.widgets import Badge, Surface, clear_layout
from novel_forge.desktop.workspace import DesktopWorkspaceSnapshot
from novel_forge.workspace.contracts import ChapterWorkspaceSnapshot

__all__ = ["ChapterStudioInspectorPresenter"]

_SEVERITY_TONES = {
    "hard": "danger",
    "high": "danger",
    "critical": "danger",
    "medium": "warning",
}

_SEVERITY_LABELS = {
    "hard": "严重",
    "high": "严重",
    "critical": "严重",
    "medium": "中等",
    "low": "轻微",
}

# ── Issue type classification ────────────────────────────────────────────────

# Targeted PATCH types — only a small matched snippet in the chapter body is
# rewritten; ending and bridge contract are untouched.
_PATCH_SAFE_TYPES: frozenset[str] = frozenset({
    "opening_gap",
    "location_jump",
    "pov_jump",
    "time_marker_invalid",
    "text_repetition",
    "sensory_anchor_repetition",
    "address_form_mismatch",
    "opening_causal_gap",
})

# Boundary WINDOW-PATCH types — targeted rewrite of the last few paragraphs
# (ContinuityRepairStep._BOUNDARY_PATCHABLE_TYPES, closing side only).
# The system combines the previous chapter's exit-state and the next chapter's
# opening bridge, then patches only those tail paragraphs — like a patch but
# anchored at the chapter boundary.
_BOUNDARY_PATCH_TYPES: frozenset[str] = frozenset({
    "closing_gap",
    "ending_ambiguity",
    "bridge_contract_not_followed",
})

# State-inheritance types — downstream chapters may have been generated using
# the incorrect inherited state before this issue was detected.
_STATE_INHERIT_TYPES: frozenset[str] = frozenset({
    "carry_forward_missing",
    "knowledge_contradiction",
    "causal_contradiction",
    "question_ignored",
})

# Union sentinel kept for any code that checks membership in this set.
_CLOSING_SIDE_TYPES: frozenset[str] = (
    _BOUNDARY_PATCH_TYPES
    | _STATE_INHERIT_TYPES
    | frozenset({
        "closing_contract_mismatch",
        "exit_state_gap",
        "custody_break",
        "handoff_missing",
    })
)

# ── Tooltip strings ───────────────────────────────────────────────────────────

_DOWNSTREAM_SAFE_TOOLTIP = (
    "此问题仅影响本章内部片段（开头衔接等），使用局部补丁修复。\n"
    "修复不会改动章节结尾或桥接契约，\n"
    "后续已归档章节内容完全不受影响。"
)

_DOWNSTREAM_BOUNDARY_TOOLTIP = (
    "此问题使用「章末边界补丁」修复：\n"
    "系统结合前一章的退出状态和后续章节的桥接契约，\n"
    "定向改写本章结尾若干段落，类似打补丁但锚定在章末。\n"
    "不会触发全文重写，也不会使下游章节失效，\n"
    "但建议修复后确认邻近章节的桥接上下文是否仍然准确。"
)

_DOWNSTREAM_FULLTEXT_TOOLTIP = (
    "此问题需要全文重写（不支持局部补丁）。\n"
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

_ISSUE_TAG_STYLE = (
    "background: #f5ede3; color: #7c6249; border-radius: 4px; "
    "padding: 1px 5px; font-size: 10px;"
)
_SMALL_BADGE_STYLE = "padding: 1px 5px; font-size: 10px; border-radius: 5px; font-weight: 600;"


class ChapterStudioInspectorPresenter:
    """Own the widget-heavy rendering of chapter studio inspector cards."""

    def __init__(
        self,
        *,
        rel_section: QWidget,
        rel_body: QLabel,
        continuity_section: QWidget,
        issues_section_title: QLabel,
        issues_checklist_layout: QVBoxLayout,
        repair_hint: QLabel,
        queue_auto_submit_repair: Callable[[], None],
        causal_section: QWidget,
        causal_section_title: QLabel,
        causal_checklist_layout: QVBoxLayout,
    ) -> None:
        self._rel_section = rel_section
        self._rel_body = rel_body
        self._continuity_section = continuity_section
        self._issues_section_title = issues_section_title
        self._issues_checklist_layout = issues_checklist_layout
        self._repair_hint = repair_hint
        self._queue_auto_submit_repair = queue_auto_submit_repair
        self._causal_section = causal_section
        self._causal_section_title = causal_section_title
        self._causal_checklist_layout = causal_checklist_layout

    def render_relationship_card(
        self,
        *,
        workspace: DesktopWorkspaceSnapshot | None,
        studio: ChapterWorkspaceSnapshot | None,
    ) -> None:
        """Populate the compact relationship card in the inspector."""
        if studio is None or workspace is None:
            self._rel_section.setVisible(False)
            return

        project_id = studio.project_id
        if not project_id:
            self._rel_section.setVisible(False)
            return

        project_dir = workspace.storage_root / project_id
        try:
            from novel_forge.desktop.pages.document_renderers import (
                render_relationship_compact,
            )
            from novel_forge.story_kernel.relationship_tracker import (
                build_relationship_overview_sync,
            )

            overview = build_relationship_overview_sync(project_dir)
        except Exception:
            self._rel_body.setText("关系数据暂不可用。")
            self._rel_section.setVisible(True)
            return

        if overview.total_relationships == 0:
            self._rel_body.setText(
                '<span style="color:#8b7460; font-size:12px;">'
                "暂未追踪到角色关系。</span>"
            )
            self._rel_section.setVisible(True)
            return

        context_texts = [
            studio.current_title,
            studio.current_goal,
            studio.current_outline_summary,
            studio.previous_summary,
            studio.previous_exit_summary,
            " ".join(studio.carry_forward or []),
        ]
        focus_characters = load_chapter_focus_characters(
            project_dir,
            studio.chapter_number,
        )
        self._rel_body.setText(
            render_relationship_compact(
                overview,
                studio.chapter_number,
                chapter_context_texts=context_texts,
                focus_characters=focus_characters,
            )
        )
        self._rel_section.setVisible(True)

    def render_continuity_checklist(
        self,
        *,
        mode: str,
        studio: ChapterWorkspaceSnapshot | None,
        jobs: list[DesktopJobRecord],
        auto_repair_attempts: dict[tuple[str, int], int],
        max_attempts: int,
        ctx: AutoPilotContext | None = None,
        refreshing: bool = False,
    ) -> None:
        """Populate the continuity issues checklist with styled cards."""
        if refreshing:
            clear_layout(self._issues_checklist_layout)
            self._continuity_section.setVisible(True)
            self._issues_section_title.setText("连贯性问题")
            hint = QLabel("正在执行，等待刷新…")
            hint.setObjectName("cardHint")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._issues_checklist_layout.addWidget(hint)
            return
        previous_checked = set(self.selected_issue_indices())
        clear_layout(self._issues_checklist_layout)
        issues = studio.continuity_issues if studio else []
        if not issues:
            self._continuity_section.setVisible(False)
            return

        self._continuity_section.setVisible(True)
        self._issues_section_title.setText(f"连贯性问题（{len(issues)} 条）")

        last_repair_job = next((job for job in jobs if job.kind == "repair_continuity"), None)
        chapter_key = (studio.project_id, studio.chapter_number) if studio else ("_", 0)
        attempts = auto_repair_attempts.get(chapter_key, 0)

        hint_state = build_continuity_hint(
            last_repair_job=last_repair_job,
            attempts=attempts,
            max_attempts=max_attempts,
        )
        self._repair_hint.setText(hint_state.text)
        self._repair_hint.setProperty("warning", bool(hint_state.warning))
        self._repair_hint.style().unpolish(self._repair_hint)
        self._repair_hint.style().polish(self._repair_hint)

        checked_indices = default_checked_issue_indices(
            mode=mode,
            issues=issues,
            previous_checked=previous_checked,
        )

        for index, issue in enumerate(issues):
            severity = (issue.get("severity") or "").lower()
            summary = issue.get("summary") or f"问题 {index + 1}"
            issue_type = issue.get("issue_type") or ""

            card = Surface("inset")
            card_row = QHBoxLayout(card)
            card_row.setContentsMargins(10, 8, 12, 8)
            card_row.setSpacing(10)

            checkbox = QCheckBox()
            checkbox.setProperty("issue_index", index)
            checkbox.setChecked(index in checked_indices)
            card_row.addWidget(checkbox)

            dot = QLabel("·")
            dot.setObjectName("cardMeta")
            card_row.addWidget(dot)

            text_col = QVBoxLayout()
            text_col.setSpacing(3)
            text_col.setContentsMargins(0, 0, 0, 0)

            header_row = QHBoxLayout()
            header_row.setSpacing(6)
            header_row.setContentsMargins(0, 0, 0, 0)

            if issue_type:
                type_label = QLabel(_ISSUE_TYPE_LABELS.get(issue_type, issue_type))
                type_label.setObjectName("memoryIssueType")
                header_row.addWidget(type_label)

            if severity:
                severity_badge = Badge(
                    _SEVERITY_LABELS.get(severity, severity),
                    tone=_SEVERITY_TONES.get(severity, "default"),
                )
                severity_badge.setObjectName("memoryIssueBadge")
                header_row.addWidget(severity_badge)

            if issue_type in _PATCH_SAFE_TYPES:
                safe_badge = Badge("✓ 局部", tone="success")
                safe_badge.setObjectName("memoryIssueBadge")
                safe_badge.setToolTip(_DOWNSTREAM_SAFE_TOOLTIP)
                header_row.addWidget(safe_badge)
            elif issue_type in _BOUNDARY_PATCH_TYPES:
                boundary_badge = Badge("◧ 章末", tone="warning")
                boundary_badge.setObjectName("memoryIssueBadge")
                boundary_badge.setToolTip(_DOWNSTREAM_BOUNDARY_TOOLTIP)
                header_row.addWidget(boundary_badge)
            elif issue_type in _STATE_INHERIT_TYPES:
                state_badge = Badge("⚠ 状态", tone="warning")
                state_badge.setObjectName("memoryIssueBadge")
                state_badge.setToolTip(_DOWNSTREAM_WARNING_TOOLTIP)
                header_row.addWidget(state_badge)
            else:
                fulltext_badge = Badge("↺ 全文", tone="info")
                fulltext_badge.setObjectName("memoryIssueBadge")
                fulltext_badge.setToolTip(_DOWNSTREAM_FULLTEXT_TOOLTIP)
                header_row.addWidget(fulltext_badge)

            header_row.addStretch()
            text_col.addLayout(header_row)

            summary_label = QLabel(summary)
            summary_label.setObjectName("cardHint")
            summary_label.setWordWrap(True)
            text_col.addWidget(summary_label)

            card_row.addLayout(text_col, 1)
            self._issues_checklist_layout.addWidget(card)

        if ctx is not None and should_auto_submit_repair(ctx):
            QTimer.singleShot(0, self._queue_auto_submit_repair)

    def render_causal_checklist(
        self,
        *,
        studio: ChapterWorkspaceSnapshot | None,
        refreshing: bool = False,
    ) -> None:
        """Populate the causal chain issues panel (read-only display, no repair action)."""
        if refreshing:
            clear_layout(self._causal_checklist_layout)
            self._causal_section.setVisible(True)
            self._causal_section_title.setText("因果链问题")
            hint = QLabel("正在执行，等待刷新…")
            hint.setObjectName("cardHint")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._causal_checklist_layout.addWidget(hint)
            return
        clear_layout(self._causal_checklist_layout)
        issues = studio.causal_issues if studio else []
        if not issues:
            self._causal_section.setVisible(False)
            return

        self._causal_section.setVisible(True)
        self._causal_section_title.setText(f"因果链问题（{len(issues)} 条）")

        for index, issue in enumerate(issues):
            severity = (issue.get("severity") or "").lower()
            summary = issue.get("summary") or f"问题 {index + 1}"
            issue_type = issue.get("issue_type") or ""
            location = issue.get("location") or ""
            fix_suggestion = issue.get("fix_suggestion") or ""

            card = Surface("inset")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 8, 12, 8)
            card_layout.setSpacing(4)

            header_row = QHBoxLayout()
            header_row.setSpacing(6)
            header_row.setContentsMargins(0, 0, 0, 0)

            if issue_type:
                type_label = QLabel(_ISSUE_TYPE_LABELS.get(issue_type, issue_type))
                type_label.setObjectName("memoryIssueTag")
                header_row.addWidget(type_label)

            if severity:
                severity_badge = Badge(
                    _SEVERITY_LABELS.get(severity, severity),
                    tone=_SEVERITY_TONES.get(severity, "default"),
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

            if location:
                loc_label = QLabel(location)
                loc_label.setObjectName("memoryLocLabel")
                header_row.addWidget(loc_label)

            header_row.addStretch()
            card_layout.addLayout(header_row)

            summary_label = QLabel(summary)
            summary_label.setObjectName("cardHint")
            summary_label.setWordWrap(True)
            card_layout.addWidget(summary_label)

            if fix_suggestion:
                fix_label = QLabel(fix_suggestion)
                fix_label.setObjectName("causalFixHint")
                fix_label.setWordWrap(True)
                card_layout.addWidget(fix_label)

            self._causal_checklist_layout.addWidget(card)

    def selected_issue_indices(self) -> list[int]:
        """Return the currently checked issue indices from the rendered cards."""
        selected: list[int] = []
        for checkbox in self._iter_issue_checkboxes():
            issue_index = checkbox.property("issue_index")
            if checkbox.isChecked() and isinstance(issue_index, int):
                selected.append(issue_index)
        return selected

    def has_unselected_issues(self, issues: list[dict[str, Any]]) -> bool:
        """Return True when continuity issues exist and none are selected."""
        return bool(issues) and not self.selected_issue_indices()

    def _iter_issue_checkboxes(self) -> list[QCheckBox]:
        checkboxes: list[QCheckBox] = []
        for index in range(self._issues_checklist_layout.count()):
            item = self._issues_checklist_layout.itemAt(index)
            if item is None:
                continue
            card = item.widget()
            if card is None:
                continue
            for checkbox in card.findChildren(QCheckBox):
                checkboxes.append(checkbox)
                break
        return checkboxes
