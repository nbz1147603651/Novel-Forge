"""Mixin module: notifications methods for ``NovelForgeDesktopWindow``.

Auto-extracted in the M3.1 giant-file-split refactor. Methods live here so the
main :file:`window/__init__.py` facade can compose them via mixin inheritance.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QUrl,
)
from PySide6.QtGui import QDesktopServices

import novel_forge.desktop.page_registrations as _page_registrations  # noqa: F401
from novel_forge.desktop.book_consistency_dialog import (
    book_dialog_admission_lines as _book_dialog_admission_lines,
)
from novel_forge.desktop.book_consistency_dialog import (
    book_dialog_auto_repair_extra_lines as _book_dialog_auto_repair_extra_lines,
)
from novel_forge.desktop.book_consistency_dialog import (
    book_dialog_chapter_label as _book_dialog_chapter_label,
)
from novel_forge.desktop.book_consistency_dialog import (
    book_dialog_post_audit_issue_lines as _book_dialog_post_audit_issue_lines,
)
from novel_forge.desktop.book_consistency_dialog import (
    book_dialog_stat_cards as _book_dialog_stat_cards,
)
from novel_forge.desktop.book_consistency_dialog import (
    book_dialog_unique_entries as _book_dialog_unique_entries,
)
from novel_forge.desktop.book_consistency_dialog import (
    build_book_consistency_dialog_payload as _build_book_consistency_dialog_payload,
)
from novel_forge.desktop.widgets import show_info_message

_logger = logging.getLogger(__name__)

_WINDOW_DEFAULT_MIN_WIDTH = 1180
_WINDOW_DEFAULT_MIN_HEIGHT = 760
_WINDOW_DEFAULT_START_WIDTH = 1440
_WINDOW_DEFAULT_START_HEIGHT = 900
_WINDOW_SCREEN_WIDTH_RATIO = 0.92
_WINDOW_SCREEN_HEIGHT_RATIO = 0.90
_COMPACT_WIDTH_THRESHOLD = 1360
_COMPACT_HEIGHT_THRESHOLD = 820

if TYPE_CHECKING:
    pass


class NotificationsMixin:
    """Mixin that contributes the **notifications** method group."""

    def _notify_export_complete(self, job: object) -> None:
        """Show export path info dialog and reveal the output folder."""
        from pathlib import Path as _Path

        result = getattr(job, "result", None) or {}
        export_path = result.get("path", "")
        if export_path:
            chapters_n = result.get("chapters_exported", "?")
            show_info_message(
                self,
                "导出完成",
                f"已导出 {chapters_n} 章至：\n{export_path}",
            )
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(_Path(export_path).parent)))
        else:
            label = getattr(job, "label", "")
            self.show_priority_status(f"任务完成：{label}", 4000, self._STATUS_SUCCESS)

    def _notify_book_consistency_complete(self, job: object) -> None:
        """Show a summary dialog when whole-book consistency audit finishes."""
        result = getattr(job, "result", None) or {}
        if not isinstance(result, dict):
            result = {}
        main_text, initial_info_text, summary = _build_book_consistency_dialog_payload(result)
        overview_lines: list[str] = initial_info_text.splitlines() if initial_info_text else []
        verification_lines: list[str] = []
        repair_lines: list[str] = []
        queue_lines: list[str] = []
        post_audit_lines: list[str] = []
        risk_lines: list[str] = []
        auto_repair = result.get("auto_repair")
        repair_queue_summary = result.get("repair_queue_summary")
        if isinstance(repair_queue_summary, dict):
            ready = int(
                repair_queue_summary.get("ready", repair_queue_summary.get("ready_count", 0)) or 0
            )
            verify_first = int(
                repair_queue_summary.get(
                    "verify_first",
                    repair_queue_summary.get("verify_first_count", 0),
                )
                or 0
            )
            manual = int(
                repair_queue_summary.get(
                    "manual_review",
                    repair_queue_summary.get("manual_review_count", 0),
                )
                or 0
            )
            blocked = int(
                repair_queue_summary.get("blocked", repair_queue_summary.get("blocked_count", 0))
                or 0
            )
            queue_lines.append(
                f"已生成修复队列：ready {ready} 项，待验证 {verify_first} 项，"
                f"人工复核 {manual} 项，阻止 {blocked} 项。"
            )
            queue_lines.append("全书审计不会直接修改正文；需要改正文时请执行独立修复队列。")

        verify_info = auto_repair.get("verify") if isinstance(auto_repair, dict) else None
        if isinstance(verify_info, dict):
            v_total = int(verify_info.get("total_issues", 0) or 0)
            v_rejected = int(verify_info.get("rejected", 0) or 0)
            v_remaining = int(verify_info.get("remaining", 0) or 0)
            v_confirmed = int(verify_info.get("confirmed", 0) or 0)
            v_suspected = int(verify_info.get("suspected", 0) or 0)
            if v_total > 0:
                verify_parts = [f"共 {v_total} 条"]
                if v_confirmed > 0:
                    verify_parts.append(f"确认 {v_confirmed}")
                if v_suspected > 0:
                    verify_parts.append(f"可疑 {v_suspected}")
                if v_rejected > 0:
                    verify_parts.append(f"过滤幻觉 {v_rejected}")
                verify_parts.append(f"保留 {v_remaining} 条进入修复")
                verification_lines.append(f"逐章验证：{'，'.join(verify_parts)}。")
        if isinstance(auto_repair, dict):
            targeted = int(auto_repair.get("targeted_chapters", 0) or 0)
            applied = int(auto_repair.get("applied_chapters", 0) or 0)
            failed = int(auto_repair.get("failed_chapters", 0) or 0)
            blocked = int(auto_repair.get("blocked_chapters", 0) or 0)
            details = auto_repair.get("details") or []
            verification_counts: dict[str, int] = {}
            repair_lines.extend(_book_dialog_admission_lines(auto_repair))
            verification_summary = auto_repair.get("verification_summary")
            if isinstance(verification_summary, dict):
                by_status = verification_summary.get("by_status")
                if isinstance(by_status, dict):
                    for status, count in by_status.items():
                        try:
                            count_value = int(count or 0)
                        except (TypeError, ValueError):
                            count_value = 0
                        if count_value > 0:
                            verification_counts[str(status)] = count_value
            no_change_chs = sorted(
                set(
                    int(d.get("chapter_number", 0))
                    for d in details
                    if isinstance(d, dict) and d.get("status") == "no_change"
                )
            )
            no_change_chs = sorted(
                int(c) for c in auto_repair.get("no_change_chapter_numbers", no_change_chs) or []
            )
            skipped_chs = sorted(
                set(
                    int(d.get("chapter_number", 0))
                    for d in details
                    if isinstance(d, dict) and d.get("status") == "skipped"
                )
            )
            skipped_chs = sorted(
                int(c) for c in auto_repair.get("skipped_chapter_numbers", skipped_chs) or []
            )
            no_change = int(
                auto_repair.get("no_change_unique_chapter_count", len(no_change_chs)) or 0
            )
            skipped = int(auto_repair.get("skipped_unique_chapter_count", len(skipped_chs)) or 0)
            unaccounted_no_write = max(
                0, targeted - applied - failed - blocked - no_change - skipped
            )
            if targeted > 0:
                repair_lines.append(
                    f"自动修复：在 {targeted} 个含问题章节中，"
                    f"成功写入 {applied} 章"
                    + (f"，{no_change} 章执行后无改动" if no_change > 0 else "")
                    + (f"，{skipped} 章未匹配可执行修复" if skipped > 0 else "")
                    + (f"，{unaccounted_no_write} 章未写入" if unaccounted_no_write > 0 else "")
                    + (f"，{blocked} 章被防污染闸门回滚" if blocked > 0 else "")
                    + (f"，{failed} 章失败" if failed > 0 else "")
                    + "。"
                )
            # Show which specific chapters were applied/skipped/excluded
            applied_chs = sorted(
                int(c) for c in auto_repair.get("applied_chapter_numbers", []) or []
            )
            if not applied_chs:
                applied_chs = sorted(
                    set(
                        int(d.get("chapter_number", 0))
                        for d in details
                        if isinstance(d, dict) and d.get("status") == "applied"
                    )
                )
            blocked_chs = sorted(
                int(c) for c in auto_repair.get("blocked_chapter_numbers", []) or []
            )
            if not blocked_chs:
                blocked_chs = sorted(
                    set(
                        int(d.get("chapter_number", 0))
                        for d in details
                        if isinstance(d, dict) and d.get("status") == "blocked"
                    )
                )
            excl = auto_repair.get("excluded_chapters") or []
            excl_chs = sorted(int(c) for c in excl)
            if applied_chs:
                ch_str = _book_dialog_chapter_label(applied_chs, limit=16)
                repair_lines.append(f"本次写入：{ch_str}")
            if no_change_chs:
                unresolved_by_chapter: dict[int, int] = {}
                for item in details:
                    if not isinstance(item, dict) or item.get("status") != "no_change":
                        continue
                    ch = int(item.get("chapter_number", 0) or 0)
                    unresolved = sum(
                        1
                        for check in item.get("verification_results", []) or []
                        if isinstance(check, dict) and check.get("status") == "unresolved"
                    )
                    unresolved_by_chapter[ch] = max(unresolved_by_chapter.get(ch, 0), unresolved)
                no_change_parts = [
                    f"第 {ch} 章（{count} 项未解决）" if count > 0 else f"第 {ch} 章"
                    for ch, count in sorted(unresolved_by_chapter.items())
                ]
                repair_lines.append(
                    f"执行后无改动：{_book_dialog_unique_entries(no_change_parts, limit=14)}"
                )
            if skipped_chs:
                ch_str = _book_dialog_chapter_label(skipped_chs, limit=16)
                repair_lines.append(f"未匹配：{ch_str}")
            if blocked_chs:
                ch_str = _book_dialog_chapter_label(blocked_chs, limit=16)
                risk_lines.append(f"已阻止并回滚：{ch_str}")
            if excl_chs:
                ch_str = _book_dialog_chapter_label(excl_chs, limit=16)
                repair_lines.append(f"待下次处理：{ch_str}")
            if int(auto_repair.get("applied_write_count", applied) or 0) > applied:
                repair_lines.append(
                    f"诊断：同章多次写入已按章节合并显示，原始写入记录 "
                    f"{int(auto_repair.get('applied_write_count', applied) or 0)} 条。"
                )
            # Post-repair evidence check summary
            post_repair = auto_repair.get("post_repair_summary")
            if isinstance(post_repair, dict):
                pr_checked = int(post_repair.get("issues_checked", 0) or 0)
                pr_closed = int(post_repair.get("issues_closed", 0) or 0)
                pr_remaining = int(post_repair.get("issues_remaining", 0) or 0)
                if pr_checked > 0:
                    verification_lines.append(
                        f"修复复核：{pr_closed}/{pr_checked} 项问题证据已从文本中消除"
                        + (f"，{pr_remaining} 项仍残留。" if pr_remaining else "。")
                    )
            if verification_counts:
                status_labels = {
                    "resolved": "已解决",
                    "partial": "部分解决",
                    "unresolved": "未解决",
                    "regressed": "疑似回归",
                    "failed": "失败",
                    "blocked": "已阻止",
                    "skipped": "已跳过",
                }
                ordered_statuses = [
                    "resolved",
                    "partial",
                    "unresolved",
                    "regressed",
                    "failed",
                    "blocked",
                    "skipped",
                ]
                status_parts = [
                    f"{status_labels.get(status, status)} {verification_counts[status]} 项"
                    for status in ordered_statuses
                    if verification_counts.get(status, 0) > 0
                ]
                status_parts.extend(
                    f"{status_labels.get(status, status)} {count} 项"
                    for status, count in sorted(verification_counts.items())
                    if status not in ordered_statuses
                )
                verification_lines.append(f"票据验证：{'，'.join(status_parts)}。")

            risk_lines.extend(_book_dialog_auto_repair_extra_lines(auto_repair))

            post_targeted = auto_repair.get("post_repair_targeted_audit")
            if isinstance(post_targeted, dict):
                post_status = str(post_targeted.get("status", "") or "")
                target_chapters = post_targeted.get("target_chapters", [])
                target_chs = [
                    int(ch)
                    for ch in target_chapters
                    if isinstance(ch, (int, str)) and str(ch).strip().isdigit()
                ]
                if post_status == "completed":
                    post_issue_count = int(post_targeted.get("issue_count", 0) or 0)
                    target_text = _book_dialog_chapter_label(target_chs, limit=12) or "无"
                    post_audit_lines.append(
                        f"修复后二次审计：复查 {target_text}，"
                        f"命中 {post_issue_count} 项剩余/新增问题。"
                    )
                    post_issues = post_targeted.get("issues") or []
                    issue_chs: set[int] = set()
                    for issue in post_issues:
                        if not isinstance(issue, dict):
                            continue
                        involved = issue.get("chapters_involved", [])
                        if not isinstance(involved, (list, tuple, set)):
                            involved = []
                        raw_chapters = [issue.get("primary_chapter"), *involved]
                        for raw_ch in raw_chapters:
                            if isinstance(raw_ch, (int, str)) and str(raw_ch).strip().isdigit():
                                issue_chs.add(int(raw_ch))
                    if issue_chs:
                        ch_str = _book_dialog_chapter_label(sorted(issue_chs), limit=12)
                        post_audit_lines.append(f"二次审计命中章节：{ch_str}")
                    post_audit_lines.extend(_book_dialog_post_audit_issue_lines(post_targeted))
                elif post_status == "skipped":
                    reason = str(post_targeted.get("reason", "") or "无复查目标")
                    post_audit_lines.append(f"修复后二次审计：已跳过（{reason}）。")
                elif post_status == "failed":
                    post_audit_lines.append("修复后二次审计：运行失败，请在工件中查看诊断。")
            elif targeted > 0:
                post_audit_lines.append("修复后二次审计：未开启或无记录。")

            # Needs manual review
            manual_chs = sorted(
                int(d.get("chapter_number", 0))
                for d in details
                if isinstance(d, dict) and d.get("needs_manual_review")
            )
            if manual_chs:
                ch_str = _book_dialog_chapter_label(manual_chs, limit=16)
                risk_lines.append(f"⚠ 需人工复核：{ch_str}")

        if summary:
            overview_lines.append(f"报告摘要：{summary[:300]}")

        sections: list[tuple[str, list[str]]] = [
            ("概览", overview_lines or [main_text]),
        ]
        if repair_lines:
            sections.append(("自动修复", repair_lines))
        if queue_lines:
            sections.append(("修复队列", queue_lines))
        if verification_lines:
            sections.append(("验证复核", verification_lines))
        if post_audit_lines:
            sections.append(("二次审计", post_audit_lines))
        if risk_lines:
            sections.append(("风险提醒", risk_lines))

        # Resolve through the public facade at call time.  Besides preserving
        # the pre-split import surface, this keeps downstream monkeypatching of
        # ``novel_forge.desktop.window.show_structured_result_dialog`` working.
        from novel_forge.desktop import window as window_facade

        window_facade.show_structured_result_dialog(
            self,
            "全书一致性审计完成",
            headline=main_text,
            stats=_book_dialog_stat_cards(main_text),
            sections=sections,
        )
