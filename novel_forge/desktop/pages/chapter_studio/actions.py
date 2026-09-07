"""Action/submit-method mixin for ChapterStudioPage.

Extracted to keep chapter_studio_page.py focused on coordination logic.
All methods use ``self`` and are mixed into ``ChapterStudioPage`` via
inheritance — no free-standing functions, no stub delegates needed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from PySide6.QtWidgets import QMessageBox

from novel_forge.common.constants import TaskType
from novel_forge.core.config import get_settings
from novel_forge.desktop.widgets import (
    MessageBoxAction,
    clear_layout,
    show_message_box,
    show_warning_message,
)
from novel_forge.desktop.workflow_requests import (
    build_prepare_chapter_request,
    build_reevaluate_chapter_request,
    build_repair_continuity_request,
    build_repair_issues_request,
    build_repair_motif_history_request,
    build_resolve_chapter_checkpoint_request,
)
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.workspace.book_ops.execution_book_recovery import summarize_book_audit_checkpoint
from novel_forge.workspace.contracts import DecisionOption

from .contract import ChapterStudioMixinBase

if TYPE_CHECKING:
    pass  # future typed self attributes


def _recommended_book_audit_max_tokens(model_output_limit: int | None) -> int:
    """Return a model-aware default for whole-book audit JSON output."""
    if model_output_limit is None or model_output_limit <= 0:
        return 8192
    limit = max(512, min(int(model_output_limit), 65536))
    if limit <= 8192:
        return limit
    if limit <= 16384:
        return min(12288, limit)
    return min(16384, limit)


def _book_consistency_route_entry(profile_config: Any) -> Any | None:
    """Return the configured whole-book audit route across legacy key variants."""
    for task_key in (TaskType.BOOK_CONSISTENCY.value, TaskType.BOOK_CONSISTENCY.name):
        route = profile_config.routes.get(task_key)
        if route is not None:
            return route
    return None


def _summarize_prior_book_audit(path: Path) -> str:
    """Return a compact user-facing summary of the prior whole-book audit."""
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "检测到上次审计报告，但报告内容无法读取。建议重新审计。"
    if not isinstance(data, dict):
        return "检测到上次审计报告，但报告格式异常。建议重新审计。"

    issues = data.get("issues", [])
    issue_count = len(issues) if isinstance(issues, list) else 0
    request_data = data.get("request", {})
    request_payload = request_data if isinstance(request_data, dict) else {}
    chapters = data.get("chapters_audited") or request_payload.get("chapter_range", [])
    chapter_count = len(chapters) if isinstance(chapters, list) else 0
    mode = str(data.get("analysis_mode") or request_payload.get("analysis_mode") or "未知")
    score = data.get("consistency_score")

    parts = [f"上次审计：{mode}，已审 {chapter_count} 章，发现 {issue_count} 项问题"]
    try:
        score_value = float(str(score)) if score is not None else -1.0
    except ValueError:
        score_value = -1.0
    if score_value >= 0:
        parts.append(f"评分 {score_value:.1f}/10")

    auto_repair = data.get("auto_repair")
    if isinstance(auto_repair, dict):
        targeted = int(auto_repair.get("targeted_chapters", 0) or 0)
        processed = int(auto_repair.get("processed_chapters", 0) or 0)
        applied = int(auto_repair.get("applied_chapters", 0) or 0)
        failed = int(auto_repair.get("failed_chapters", 0) or 0)
        if targeted or processed or applied or failed:
            parts.append(
                f"修复进度 {processed}/{targeted} 章，已改动 {applied} 章，失败 {failed} 章"
            )
        excluded = auto_repair.get("excluded_chapters")
        if isinstance(excluded, list) and excluded:
            preview = "、".join(str(ch) for ch in excluded[:8])
            suffix = "…" if len(excluded) > 8 else ""
            parts.append(f"待续修章节：第 {preview}{suffix} 章")
        elif targeted and processed >= targeted and failed == 0:
            parts.append("没有记录到剩余待续修章节")
    else:
        parts.append("尚未进入自动修复阶段")

    return "；".join(parts) + "。"


class ChapterStudioActionMixin(ChapterStudioMixinBase):
    """Mixin providing all ``_submit_*`` and related action methods for ChapterStudioPage."""

    def _reset_chapter_warning_clear_state(self) -> None:
        """Invalidate cleared-warning cache before launching jobs that may refresh reports."""
        if self._studio is None:
            return
        key = (self._studio.project_id, self._studio.chapter_number)
        self._state.dismissed_warning_fingerprints.pop(key, None)

    # ── Layout helpers (used by submit methods and page event handlers) ───

    def _project_layout(self) -> Any | None:
        """Return ``ProjectLayout`` for the active project, or ``None``."""
        if self._studio is None or self._workspace is None:
            return None
        from novel_forge.persistence.models import ProjectLayout

        return ProjectLayout(self._workspace.storage_root / self._studio.project_id)

    @staticmethod
    def _completed_chapter_numbers(layout: Any) -> list[int]:
        """List finished chapter numbers from *layout.chapters_dir*."""

        def _chapter_sort_key(p: Path) -> int:
            m = re.search(r"chapter_(\d+)", p.name)
            return int(m.group(1)) if m else 0

        nums: list[int] = []
        for p in sorted(layout.chapters_dir.glob("chapter_*.md"), key=_chapter_sort_key):
            try:
                nums.append(int(p.stem.split("_")[1]))
            except (IndexError, ValueError):
                continue
        return nums

    # ── Chapter prepare / checkpoint ─────────────────────────────

    def _selected_rewrite_strategy(self) -> str:
        combo = getattr(self, "_rewrite_strategy_combo", None)
        if combo is None:
            return "auto"
        value = combo.currentData()
        return str(value or "auto")

    def _selected_writing_mode(self) -> str:
        selector = getattr(self, "_writing_mode_selector", None)
        if selector is None:
            return "whole_chapter"
        current_mode = getattr(selector, "current_mode", None)
        if callable(current_mode):
            return str(current_mode() or "whole_chapter")
        return "whole_chapter"

    def _selected_repair_control_mode(self) -> Literal["manual", "ai_assisted", "ai_auto"]:
        mode = str(getattr(self, "_mode", "manual") or "manual")
        return {
            "manual": "manual",
            "suggest": "ai_assisted",
            "auto": "ai_auto",
            "book_auto": "ai_auto",
        }.get(mode, "manual")

    def _chapter_already_generated_on_disk(self) -> bool:
        """Return True when canon and final text already cover the selected chapter."""

        if self._studio is None or self._workspace is None:
            return False
        chapter_number = self.current_chapter_number()
        if chapter_number < 1:
            return False
        project_id = self.current_project_id()
        if not project_id:
            return False
        project_dir = self._workspace.storage_root / project_id
        chapter_path = project_dir / "chapters" / f"chapter_{chapter_number:03d}.md"
        canon_path = project_dir / "canon" / "canon_current.json"
        if not chapter_path.exists() or not canon_path.exists():
            return False
        try:
            payload = json.loads(canon_path.read_text(encoding="utf-8"))
            current_chapter = int(payload.get("current_chapter", 0) or 0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return False
        return current_chapter >= chapter_number

    def _submit_prepare(self, *, force: bool = False, extra_continuity_notes: str = "") -> None:
        if not force and self._chapter_already_generated_on_disk():
            chapter_number = self.current_chapter_number()
            next_hint = ""
            if self._studio and chapter_number < self._studio.total_chapters:
                next_hint = f"请前往第 {chapter_number + 1} 章继续，"
            show_warning_message(
                self.window(),
                "章节已归档",
                (
                    f"第 {chapter_number} 章已经归档，不能再次普通准备章节方案。"
                    f"{next_hint}如需重写本章，请使用「重新生成章节」。"
                ),
            )
            return
        if not self._feasibility_check_word_count():
            return
        user_notes = self._notes.toPlainText()
        if extra_continuity_notes:
            user_notes = (user_notes + "\n" + extra_continuity_notes).strip()
        rewrite_strategy = self._selected_rewrite_strategy() if force else "auto"
        try:
            request = build_prepare_chapter_request(
                project_id=self.current_project_id(),
                chapter_number=self.current_chapter_number(),
                force=force,
                notes=user_notes,
                rewrite_strategy=rewrite_strategy,
                writing_mode=self._selected_writing_mode(),
                repair_control_mode=self._selected_repair_control_mode(),
            )
        except Exception as exc:
            show_warning_message(self.window(), "输入有误", str(exc))
            return
        if force:
            strategy_label = {
                "auto": "自动判断",
                "sequential": "普通重写",
                "compatible": "兼容后文",
                "reconstruct": "重构后续",
                "surgical": "外科修补",
            }.get(rewrite_strategy, "自动判断")
            self._action_title.setText("重新生成中…")
            self._action_summary.setText(
                f"正在按「{strategy_label}」准备章节方案，请稍候。"
            )
        else:
            self._action_title.setText("方案生成中…")
            self._action_summary.setText("正在构建章节上下文、桥接与章节计划，请稍候。")
        self._action_badge.setText("提交中")
        self._action_badge.set_tone("warning")
        clear_layout(self._action_buttons)
        self._mark_notes_committed()
        self._reset_chapter_warning_clear_state()
        self.prepare_requested.emit(request)

    def _handle_checkpoint_option(self, option: DecisionOption) -> None:
        if self._studio is None or self._studio.pending_checkpoint is None:
            return
        try:
            request = build_resolve_chapter_checkpoint_request(
                project_id=self._studio.project_id,
                chapter_number=self._studio.chapter_number,
                checkpoint_id=self._studio.pending_checkpoint.checkpoint_id,
                option_id=option.option_id,
                notes=self._notes.toPlainText(),
                repair_control_mode=self._selected_repair_control_mode(),
            )
        except Exception as exc:
            show_warning_message(self.window(), "输入有误", str(exc))
            return
        # 记录本次提交的 checkpoint_id，用于在 bind_studio 未及时刷新时
        # 检测陈旧的 pending_checkpoint，防止自动驾驶重复提交。
        self._last_submitted_checkpoint_id = self._studio.pending_checkpoint.checkpoint_id
        # Task 3.2: record user's choice for workspace-layer side-effect execution
        self._state.last_user_checkpoint_choice = {
            "option_id": option.option_id,
            "label": option.label,
            "is_recommended": option.is_recommended,
            "semantic_tag": getattr(option, "semantic_tag", None),
            "checkpoint_id": self._studio.pending_checkpoint.checkpoint_id,
        }
        self._action_title.setText(f'正在执行"{option.label}"…')
        self._action_summary.setText("任务已提交，请在任务流中查看进度。")
        self._action_badge.setText("提交中")
        self._action_badge.set_tone("warning")
        clear_layout(self._action_buttons)
        _submitted_with_notes = option.option_id == "regenerate_plan_with_notes"
        self._mark_notes_committed(submitted_with_notes=_submitted_with_notes)
        self._reset_chapter_warning_clear_state()
        self.resolve_requested.emit(request)

    # ── Resume from progress (断点续传) ────────────────────────────

    def _on_resume_from_progress(self, checkpoint: Any) -> None:
        """Resume chapter execution from the last saved progress checkpoint."""
        if self._studio is None:
            return
        try:
            request = build_resolve_chapter_checkpoint_request(
                project_id=self._studio.project_id,
                chapter_number=self._studio.chapter_number,
                checkpoint_id=checkpoint.checkpoint_id,
                option_id="resume_from_progress",
                notes=self._notes.toPlainText(),
                repair_control_mode=self._selected_repair_control_mode(),
            )
        except Exception as exc:
            show_warning_message(self.window(), "恢复失败", str(exc))
            return
        self._action_title.setText("从断点恢复中…")
        self._action_summary.setText("正在从上次失败的步骤继续执行，跳过已完成的阶段。")
        self._action_badge.setText("恢复中")
        self._action_badge.set_tone("warning")
        clear_layout(self._action_buttons)
        self.resolve_requested.emit(request)

    # ── Continuity / issue repair ─────────────────────────────────

    def _submit_repair_continuity(self) -> None:
        """Submit a continuity repair job for the selected issues."""
        if self._studio is None or self._memory_presenter is None:
            return
        selected = self._memory_presenter.selected_issue_indices()
        if not selected:
            show_warning_message(self.window(), "未选择", "请至少勾选一个连贯性 问题后再修复。")
            return
        request = build_repair_continuity_request(
            project_id=self._studio.project_id,
            chapter_number=self._studio.chapter_number,
            issue_indices=selected,
            repair_control_mode=self._selected_repair_control_mode(),
        )
        self._reset_chapter_warning_clear_state()
        self.repair_continuity_requested.emit(request)

    def _submit_repair_issues(self) -> None:
        """Submit a repair job for selected continuity and causal chain issues."""
        if self._studio is None or self._memory_presenter is None:
            return
        continuity_selected = self._memory_presenter.selected_issue_indices()
        causal_selected = self._memory_presenter.selected_causal_issue_indices()
        if not continuity_selected and not causal_selected:
            continuity_selected = list(range(len(self._studio.continuity_issues or [])))
            causal_selected = list(range(len(self._studio.causal_issues or [])))
        if not continuity_selected and not causal_selected:
            return
        continuity_sigs = self._memory_presenter.selected_issue_signatures()
        causal_sigs = self._memory_presenter.selected_causal_issue_signatures()
        selected_sigs = set(continuity_sigs) | set(causal_sigs)
        attempt_counters = self._load_causal_repair_attempt_counters()
        exhausted_in_selection = [
            sig for sig in selected_sigs if int(attempt_counters.get(sig, 0) or 0) >= 3
        ]
        allow_exhausted = False
        if exhausted_in_selection:
            choice = show_message_box(
                self,
                "确认重试已达上限的问题",
                "以下问题已尝试修复多次仍未解决，是否强制重试？",
                informative_text="已超上限的问题签名:\n" + "\n".join(exhausted_in_selection[:5]),
                icon=QMessageBox.Icon.Question,
                actions=(
                    MessageBoxAction(
                        key="force",
                        text="强制重试",
                        role=QMessageBox.ButtonRole.AcceptRole,
                        variant="danger",
                        default=True,
                    ),
                    MessageBoxAction(
                        key="skip",
                        text="跳过这些问题",
                        role=QMessageBox.ButtonRole.RejectRole,
                        variant="secondary",
                    ),
                ),
                escape_key="skip",
            )
            if choice == "skip":
                new_continuity = []
                new_causal = []
                new_continuity_sigs = []
                new_causal_sigs = []
                for i, sig in enumerate(continuity_sigs):
                    if sig not in exhausted_in_selection:
                        new_continuity.append(continuity_selected[i])
                        new_continuity_sigs.append(sig)
                for i, sig in enumerate(causal_sigs):
                    if sig not in exhausted_in_selection:
                        new_causal.append(causal_selected[i])
                        new_causal_sigs.append(sig)
                continuity_selected = new_continuity
                causal_selected = new_causal
                continuity_sigs = new_continuity_sigs
                causal_sigs = new_causal_sigs
                if not continuity_selected and not causal_selected:
                    return
                allow_exhausted = False
            elif choice == "force":
                allow_exhausted = True
            else:
                return
        request = build_repair_issues_request(
            project_id=self._studio.project_id,
            chapter_number=self._studio.chapter_number,
            continuity_issue_indices=continuity_selected,
            causal_issue_indices=causal_selected,
            continuity_issue_signatures=continuity_sigs,
            causal_issue_signatures=causal_sigs,
            allow_exhausted_retry=allow_exhausted,
            repair_control_mode=self._selected_repair_control_mode(),
        )
        self._reset_chapter_warning_clear_state()
        self.repair_issues_requested.emit(request)

    def _load_causal_repair_attempt_counters(self) -> dict[str, int]:
        if self._studio is None:
            return {}
        try:
            settings = get_settings()
            storage = FileSystemStorage(settings.storage_root)
            path = (
                storage.project_path(self._studio.project_id)
                / "states"
                / (f"chapter_{self._studio.chapter_number:03d}_causal_repair_attempts.json")
            )
            if not path.exists():
                return {}
            raw = storage.load_json(path) or {}
            payload = raw.get("attempts", raw) if isinstance(raw, dict) else {}
            if not isinstance(payload, dict):
                return {}
            return {str(k): int(v) for k, v in payload.items() if isinstance(k, str) and k}
        except Exception:
            return {}

    def _submit_reevaluate_chapter(self) -> None:
        """Submit a report-only re-evaluation job (no text repair)."""
        if self._studio is None:
            return
        try:
            request = build_reevaluate_chapter_request(
                project_id=self._studio.project_id,
                chapter_number=self._studio.chapter_number,
            )
        except Exception as exc:
            show_warning_message(self.window(), "输入有误", str(exc))
            return
        self._action_title.setText("重新评估中…")
        self._action_summary.setText(
            "正在重新运行对齐、质量、连贯、因果与追读力评估，并同步当前检查点；不会改动正文。"
        )
        self._action_badge.setText("评估中")
        self._action_badge.set_tone("warning")
        clear_layout(self._action_buttons)
        self._reset_chapter_warning_clear_state()
        self.reevaluate_requested.emit(request)

    def _submit_reextract_relationships(self) -> None:
        """Submit a job to re-extract relationships from existing chapter text.

        Shows a confirmation dialog letting the user choose between
        extracting for the current chapter only or all completed chapters.
        """
        if self._studio is None:
            return
        from novel_forge.workspace.contracts import ReextractRelationshipsRequest

        ch = self._studio.chapter_number
        choice = show_message_box(
            self,
            "重新提取关系",
            "将对章节文本重新运行关系提取，更新 canon 中的角色关系数据。",
            informative_text="请选择提取范围：",
            icon=QMessageBox.Icon.Question,
            actions=(
                MessageBoxAction(
                    key="current",
                    text=f"仅第 {ch} 章",
                    role=QMessageBox.ButtonRole.AcceptRole,
                    variant="primary",
                    default=True,
                ),
                MessageBoxAction(
                    key="all",
                    text="全部章节",
                    role=QMessageBox.ButtonRole.AcceptRole,
                    variant="secondary",
                ),
                MessageBoxAction(
                    key="cancel",
                    text="取消",
                    role=QMessageBox.ButtonRole.RejectRole,
                    variant="secondary",
                ),
            ),
            escape_key="cancel",
        )
        if choice == "cancel" or not choice:
            return

        chapter_number = ch if choice == "current" else 0
        request = ReextractRelationshipsRequest(
            project_id=self._studio.project_id,
            chapter_number=chapter_number,
        )
        self.reextract_relationships_requested.emit(request)
        # Update hint to show in-progress
        scope_label = f"第 {ch} 章" if choice == "current" else "全部章节"
        if hasattr(self, "_memory_panel_widget"):
            self._memory_panel_widget.update_relations_extract_hint(
                f"正在重新提取关系（{scope_label}）…"
            )
            self._memory_panel_widget.get_relations_extract_button().setEnabled(False)

    def _submit_repair_motif_history(self) -> None:
        """Show repair-mode dialog then submit motif history repair job."""
        if self._studio is None:
            return

        action = show_message_box(
            self,
            "修补母题历史",
            "选择修补模式：",
            informative_text=(
                "快速修补：从已有缓存重建统计（秒级完成）\n"
                "强制重新提取：从归档章节正文重新生成母题（每章需调用 LLM，耗时较长）"
            ),
            icon=QMessageBox.Icon.Question,
            actions=(
                MessageBoxAction(
                    key="quick",
                    text="快速修补",
                    role=QMessageBox.ButtonRole.AcceptRole,
                    variant="primary",
                    default=True,
                ),
                MessageBoxAction(
                    key="force",
                    text="强制重新提取",
                    role=QMessageBox.ButtonRole.ActionRole,
                    variant="secondary",
                ),
                MessageBoxAction(
                    key="cancel",
                    text="取消",
                    role=QMessageBox.ButtonRole.RejectRole,
                    variant="secondary",
                ),
            ),
            escape_key="cancel",
        )

        if action == "cancel":
            return

        force = action == "force"
        request = build_repair_motif_history_request(
            project_id=self._studio.project_id,
            chapter_number=self._studio.chapter_number,
            force_re_extract=force,
            start_chapter=1,
        )
        self.repair_motif_history_requested.emit(request)
        if hasattr(self, "_memory_panel_widget"):
            label = "正在强制重新提取母题…" if force else "正在修补母题历史…"
            self._memory_panel_widget.update_motif_repair_hint(label)
            self._memory_panel_widget.get_motif_repair_button().setEnabled(False)

    def _get_continuity_issue_notes(self) -> str:
        """Return formatted continuity and causal issue summaries for inclusion in notes."""
        continuity_issues = self._studio.continuity_issues if self._studio else []
        causal_issues = self._studio.causal_issues if self._studio else []
        parts = []
        if continuity_issues:
            for i, issue in enumerate(continuity_issues):
                summary = issue.get("summary") or f"问题 {i + 1}"
                issue_type = issue.get("issue_type") or ""
                severity = issue.get("severity") or "medium"
                prefix = f"[{issue_type}] " if issue_type else ""
                line = f"{prefix}({severity}) {summary}"
                location = issue.get("location") or issue.get("rewrite_scope") or ""
                if location:
                    line += f"（位置：{location}）"
                evidence = issue.get("evidence") or ""
                if evidence:
                    line += f"\n  原文引用：{evidence}"
                fix_actions = issue.get("fix_actions") or []
                if fix_actions:
                    line += f"\n  建议修复：{'；'.join(str(a) for a in fix_actions)}"
                parts.append(line)
        if causal_issues:
            for i, issue in enumerate(causal_issues):
                summary = issue.get("summary") or f"因果问题 {i + 1}"
                issue_type = issue.get("issue_type") or ""
                severity = issue.get("severity") or "medium"
                prefix = f"[{issue_type}] " if issue_type else ""
                line = f"{prefix}({severity}) {summary}"
                location = issue.get("location") or ""
                if location:
                    line += f"（位置：{location}）"
                evidence = issue.get("evidence") or ""
                if evidence:
                    line += f"\n  原文引用：{evidence}"
                fix_suggestion = issue.get("fix_suggestion") or ""
                if fix_suggestion:
                    line += f"\n  建议修复：{fix_suggestion}"
                parts.append(line)
        if not parts:
            return ""
        return "需注意以下问题：\n" + "\n".join(f"• {p}" for p in parts)

    def _has_unselected_continuity_issues(self) -> bool:
        """Return True if continuity or causal issues exist and none are checked."""
        if self._memory_presenter is None:
            return False
        continuity_issues = self._studio.continuity_issues if self._studio else []
        causal_issues = self._studio.causal_issues if self._studio else []
        return bool(continuity_issues or causal_issues)

    def _prompt_continuity_then_execute(
        self,
        action: Callable[[str], None],
        include_hint: str,
        skip_label: str,
    ) -> None:
        """Show continuity-issue dialog if needed, then call *action* with extra notes."""
        extra = ""
        if self._has_unselected_continuity_issues():
            continuity_issues = self._studio.continuity_issues if self._studio else []
            causal_issues = self._studio.causal_issues if self._studio else []
            n = len(continuity_issues) + len(causal_issues)
            result = show_message_box(
                self.window(),
                "章节问题提醒",
                f"本章有 {n} 个问题尚未修复（连贯性 {len(continuity_issues)} 个，因果链 {len(causal_issues)} 个）。",
                informative_text=include_hint,
                icon=QMessageBox.Icon.Question,
                actions=(
                    MessageBoxAction(
                        "include", "纳入参考", QMessageBox.ButtonRole.AcceptRole, "primary", True
                    ),
                    MessageBoxAction(
                        "skip", skip_label, QMessageBox.ButtonRole.RejectRole, "secondary"
                    ),
                ),
                escape_key="skip",
            )
            if result == "include":
                extra = self._get_continuity_issue_notes()
            elif result == "":
                return
        action(extra)

    def _submit_regen_with_continuity_check(self) -> None:
        """重新生成章节：若有未选中的连贯性问题则先询问是否纳入参考。"""
        self._prompt_continuity_then_execute(
            action=lambda extra: self._submit_prepare(force=True, extra_continuity_notes=extra),
            include_hint=(
                "是否将这些问题纳入重新生成的方向参考？\n\n"
                "「纳入参考」：AI 重新生成时将参考这些问题，尽量避免重现。\n"
                "「直接重生成」：按现有重写方向生成，忽略连贯性问题。"
            ),
            skip_label="直接重生成",
        )

    def _submit_polish_with_continuity_check(self) -> None:
        """精修润色：若有未选中的连贯性问题则先询问是否纳入润色参考。"""
        self._prompt_continuity_then_execute(
            action=lambda extra: self._submit_polish(extra_continuity_notes=extra),
            include_hint=(
                "是否将这些问题纳入润色参考？\n\n"
                "「纳入参考」：AI 润色时将参考这些问题并尝试修正。\n"
                "「仅润色文学性」：不考虑连贯性问题，仅优化文字表达。"
            ),
            skip_label="仅润色文学性",
        )

    # ── Polish / book-level ───────────────────────────────────────

    def _submit_polish(self, extra_continuity_notes: str = "") -> None:
        """Submit a polish job for the current chapter."""
        if self._studio is None:
            return
        from novel_forge.desktop.workflow_requests import build_polish_chapter_request

        user_notes = self._notes.toPlainText()
        combined = (
            (user_notes + "\n" + extra_continuity_notes).strip()
            if extra_continuity_notes
            else user_notes
        )
        request = build_polish_chapter_request(
            project_id=self._studio.project_id,
            chapter_number=self._studio.chapter_number,
            notes=combined,
        )
        self._action_title.setText("精修润色中…")
        self._action_summary.setText("AI 正在对本章进行文学性打磨，请稍候。")
        clear_layout(self._action_buttons)
        self._mark_notes_committed()
        self.polish_requested.emit(request)

    def _submit_book_consistency(self) -> None:
        """Submit a whole-book consistency audit with scope selection dialog."""
        layout = self._project_layout()
        if layout is None:
            return
        from novel_forge.core.config import get_settings
        from novel_forge.desktop.workflow_requests import build_book_consistency_request

        from .dialogs import BookAuditDialog

        completed = self._completed_chapter_numbers(layout)

        if len(completed) < 2:
            show_warning_message(
                self, "章节不足", "至少需要 2 个已完成章节才能 进行全书一致性审计。"
            )
            return

        settings = get_settings()

        # ── 查询 BOOK_CONSISTENCY 任务路由模型的输出 token 上限 ─────────────
        _model_max_output_tokens: int | None = None
        try:
            from novel_forge.gateway.profiles import (
                get_model_max_output_tokens,
                load_or_import_profiles,
            )

            _pcfg = load_or_import_profiles(settings)
            _route = _book_consistency_route_entry(_pcfg)
            _prof = _pcfg.get_profile(_route.profile_id) if _route else None
            if _prof is None and _pcfg.default_profile_id:
                _prof = _pcfg.get_profile(_pcfg.default_profile_id)
            if _prof:
                _model_max_output_tokens = get_model_max_output_tokens(_prof.model_id)
        except Exception:
            pass

        _default_audit_max_tokens = _recommended_book_audit_max_tokens(_model_max_output_tokens)

        # ── 检查是否存在上次审计报告（决定是否显示续修选项）─────────
        _prior_audit_path = layout.reports_dir / "book_consistency_audit.json"
        _audit_checkpoint_path = layout.states_dir / "book_consistency_audit_checkpoint.json"
        _has_prior_audit = _prior_audit_path.exists()
        _has_audit_checkpoint = _audit_checkpoint_path.exists()
        _prior_audit_status = (
            _summarize_prior_book_audit(_prior_audit_path) if _has_prior_audit else ""
        )
        _audit_checkpoint_status = (
            summarize_book_audit_checkpoint(_audit_checkpoint_path) if _has_audit_checkpoint else ""
        )

        dlg = BookAuditDialog(
            completed,
            default_analysis_mode=getattr(settings, "long_book_audit_default_mode", "full_text"),
            default_prompt_hint=getattr(settings, "long_book_audit_prompt_hint", ""),
            default_location_strictness=getattr(
                settings, "long_book_audit_location_strictness", "balanced"
            ),
            default_max_tokens=_default_audit_max_tokens,
            model_max_output_tokens=_model_max_output_tokens,
            default_temperature=float(getattr(settings, "temp_book_consistency", 0.2)),
            default_audit_max_chapters_per_batch=int(
                getattr(settings, "long_book_audit_max_chapters_per_batch", 12)
            ),
            default_audit_max_issues_per_chunk=int(
                getattr(settings, "long_book_audit_max_issues_per_chunk", 12)
            ),
            default_audit_issue_pool_max_items=int(
                getattr(settings, "long_book_audit_issue_pool_max_items", 160)
            ),
            default_chapter_max_chars=int(
                getattr(settings, "long_book_audit_chapter_max_chars", 12000)
            ),
            default_two_phase_enabled=bool(
                getattr(settings, "long_book_audit_two_phase_enabled", True)
            ),
            default_two_phase_threshold=float(
                getattr(settings, "long_book_audit_two_phase_threshold", 0.7)
            ),
            default_two_phase_max_target_chapters=int(
                getattr(settings, "long_book_audit_two_phase_max_target_chapters", 24)
            ),
            default_repair_mode="targeted"
            if bool(getattr(settings, "long_book_audit_auto_repair", False))
            else "off",
            default_repair_min_severity=getattr(
                settings, "long_book_audit_repair_min_severity", "warning"
            ),
            default_repair_max_chapters=int(
                getattr(settings, "long_book_audit_repair_max_chapters", 12)
            ),
            default_use_issue_panel_pool=bool(
                getattr(settings, "long_book_audit_use_issue_panel_pool", True)
            ),
            default_panel_first_expansion=bool(
                getattr(settings, "long_book_audit_panel_first_expansion", True)
            ),
            default_repair_concurrency=int(
                getattr(settings, "long_book_audit_repair_concurrency", 1)
            ),
            default_generate_repair_report=bool(
                getattr(settings, "long_book_audit_generate_repair_report", True)
            ),
            default_verify_before_repair=bool(
                getattr(settings, "long_book_audit_verify_before_repair", True)
            ),
            default_post_repair_targeted_audit=bool(
                getattr(settings, "long_book_audit_post_repair_targeted_audit", False)
            ),
            default_repair_guard_enabled=bool(
                getattr(settings, "long_book_audit_repair_guard_enabled", True)
            ),
            default_repair_guard_max_delta_ratio=float(
                getattr(settings, "long_book_audit_repair_guard_max_delta_ratio", 0.12)
            ),
            default_repair_guard_max_added_chars=int(
                getattr(settings, "long_book_audit_repair_guard_max_added_chars", 600)
            ),
            default_parallel_chunks=bool(
                getattr(settings, "long_book_audit_parallel_chunks", True)
            ),
            default_parallel_dimensions=bool(
                getattr(settings, "long_book_audit_parallel_dimensions", True)
            ),
            has_prior_audit=_has_prior_audit,
            prior_audit_status=_prior_audit_status,
            has_audit_checkpoint=_has_audit_checkpoint,
            audit_checkpoint_status=_audit_checkpoint_status,
            parent=self.window(),
        )
        dlg.exec()
        if not dlg.was_accepted():
            return

        assert self._studio is not None  # guarded by _project_layout()
        request = build_book_consistency_request(
            project_id=self._studio.project_id,
            chapter_range=dlg.get_chapter_range(),
            analysis_mode=dlg.get_analysis_mode(),
            prompt_hint=dlg.get_prompt_hint(),
            location_strictness=dlg.get_location_strictness(),
            max_tokens=dlg.get_max_tokens(),
            temperature=dlg.get_temperature(),
            audit_max_chapters_per_batch=dlg.get_audit_max_chapters_per_batch(),
            audit_max_issues_per_chunk=dlg.get_audit_max_issues_per_chunk(),
            audit_issue_pool_max_items=dlg.get_audit_issue_pool_max_items(),
            chapter_max_chars=dlg.get_chapter_max_chars(),
            two_phase_enabled=dlg.get_two_phase_enabled(),
            two_phase_threshold=dlg.get_two_phase_threshold(),
            two_phase_max_target_chapters=dlg.get_two_phase_max_target_chapters(),
            repair_mode=dlg.get_repair_mode(),
            repair_min_severity=dlg.get_repair_min_severity(),
            repair_max_chapters=dlg.get_repair_max_chapters(),
            allow_exhausted_retry=dlg.get_allow_exhausted_retry(),
            use_issue_panel_pool=dlg.get_use_issue_panel_pool(),
            repair_concurrency=dlg.get_repair_concurrency(),
            generate_repair_report=dlg.get_generate_repair_report(),
            panel_first_expansion=dlg.get_panel_first_expansion(),
            verify_before_repair=dlg.get_verify_before_repair(),
            post_repair_targeted_audit=dlg.get_post_repair_targeted_audit(),
            repair_guard_enabled=dlg.get_repair_guard_enabled(),
            repair_guard_max_delta_ratio=dlg.get_repair_guard_max_delta_ratio(),
            repair_guard_max_added_chars=dlg.get_repair_guard_max_added_chars(),
            auto_continue=dlg.get_auto_continue(),
            continue_from_audit=dlg.get_continue_from_audit(),
            continue_audit_from_checkpoint=dlg.get_continue_audit_from_checkpoint(),
            reset_audit_checkpoint=dlg.get_reset_audit_checkpoint(),
            parallel_chunks=dlg.get_parallel_chunks(),
            parallel_dimensions=dlg.get_parallel_dimensions(),
            rollback_on_failure=True,
        )
        scope_desc = (
            "全部已完成章节"
            if not dlg.get_chapter_range()
            else f"{len(dlg.get_chapter_range())} 个选定章节"
        )
        analysis_desc = "全文深审" if request.analysis_mode == "full_text" else "摘要审计"
        if request.analysis_mode == "full_text" and request.two_phase_enabled:
            batch_desc = f"（漏斗≤{request.two_phase_max_target_chapters}章，每批≤{request.audit_max_chapters_per_batch}章）"
        elif request.analysis_mode == "full_text":
            batch_desc = f"（每批≤{request.audit_max_chapters_per_batch}章）"
        else:
            batch_desc = ""
        repair_desc = (
            f"完成后自动修复（阈值≥{request.repair_min_severity}，最多{request.repair_max_chapters}章，并发≤{request.repair_concurrency}）"
            if request.repair_mode == "targeted"
            else "仅审计，不自动修复"
        )
        pool_desc = "启用问题池精准锚定" if request.use_issue_panel_pool else "不使用问题池锚定"
        report_desc = (
            "将生成卷帙修复报告" if request.generate_repair_report else "不生成卷帙修复报告"
        )
        verify_desc = "修复前逐章验证" if request.verify_before_repair else ""
        post_audit_desc = "修复后二次小审计" if request.post_repair_targeted_audit else ""
        guard_desc = "修复后防污染回滚" if request.repair_guard_enabled else ""
        if request.continue_from_audit:
            self._action_title.setText("续修模式：继续修复上次未完成的章节…")
            self._action_summary.setText(
                "跳过审计阶段，直接加载上次审计结果中尚未修复的章节继续修复。"
                + (f"{verify_desc}。" if verify_desc else "")
                + (f"{post_audit_desc}。" if post_audit_desc else "")
                + (f"{guard_desc}。" if guard_desc else "")
                + f"{report_desc}。"
            )
        elif request.continue_audit_from_checkpoint:
            self._action_title.setText("继续审计：从上次中断的批次恢复…")
            self._action_summary.setText(
                f"将复用已完成的分批审计结果，只继续未完成批次；完成后{repair_desc}，{report_desc}。"
            )
        else:
            self._action_title.setText("全书一致性审计中…")
            self._action_summary.setText(
                f"AI 正在以{analysis_desc}{batch_desc}检查{scope_desc}的名称、时间线、世界观一致性。{repair_desc}，{pool_desc}，{report_desc}。"
                + (f"　{verify_desc}。" if verify_desc else "")
                + (f"　{post_audit_desc}。" if post_audit_desc else "")
                + (f"　{guard_desc}。" if guard_desc else "")
            )
        clear_layout(self._action_buttons)
        self.book_consistency_requested.emit(request)

    def _submit_export(self) -> None:
        """Submit an export job with format/chapter/path selection dialog."""
        layout = self._project_layout()
        if layout is None:
            return
        from novel_forge.desktop.workflow_requests import build_export_book_request

        from .dialogs import ExportDialog

        completed = self._completed_chapter_numbers(layout)

        if not completed:
            show_warning_message(self, "无可导出章节", "当前项目没有已完成的章节可供导出。")
            return

        # Resolve default book title from spec.json → outline.json
        default_book_title = ""
        try:
            if layout.spec_path.exists():
                from novel_forge.core.config import get_settings  # noqa: PLC0415

                _settings = get_settings()
                import json as _json  # noqa: PLC0415

                spec_data = _json.loads(layout.spec_path.read_text(encoding="utf-8"))
                default_book_title = spec_data.get("title", "")
            if not default_book_title and layout.outline_path.exists():
                import json as _json  # noqa: PLC0415

                outline_data = _json.loads(layout.outline_path.read_text(encoding="utf-8"))
                default_book_title = outline_data.get("title", "")
        except Exception:
            pass

        default_dir = layout.root / "exports"
        dlg = ExportDialog(
            completed, default_dir, book_title=default_book_title, parent=self.window()
        )
        dlg.exec()
        if not dlg.was_accepted():
            return

        assert self._studio is not None  # guarded by _project_layout()
        fmt = cast(Literal["markdown", "epub", "txt"], dlg.get_format())
        request = build_export_book_request(
            project_id=self._studio.project_id,
            format=fmt,
            chapter_range=dlg.get_chapter_range(),
            output_dir=str(dlg.get_output_dir()),
            book_title=dlg.get_book_title(),
        )
        fmt_label = {"markdown": "Markdown", "epub": "EPUB", "txt": "纯文本"}.get(
            request.format, request.format
        )
        ch_desc = (
            "全部已完成章节"
            if not request.chapter_range
            else f"{len(request.chapter_range)} 个选定章节"
        )
        self._action_title.setText(f"导出 {fmt_label} 中…")
        self._action_summary.setText(
            f"正在将{ch_desc}导出为 {fmt_label} 格式至 {dlg.get_output_dir()}。"
        )
        clear_layout(self._action_buttons)
        self.export_requested.emit(request)
