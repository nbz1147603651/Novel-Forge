"""Sub-module of novel_forge.desktop.pages.document_renderer.story_artifacts.

Created in P2g. Contains the two book-consistency renderers and the
2 label dictionaries + 9 private helpers that previously lived in the
parent ``__init__.py``:

- ``render_book_consistency_report`` — audit view
- ``render_book_consistency_repair_report`` — repair view
- ``_CONSISTENCY_CATEGORY_LABELS``, ``_SEVERITY_STYLES``, ``_VERIFICATION_STATUS_LABELS``
  — small label dictionaries
- 9 helpers: ``_book_tag``, ``_book_metric``, ``_repair_status_label``,
  ``_verification_counts_from_results``, ``_verification_counts_from_summary``,
  ``_format_verification_counts``, ``_format_admission_summary``,
  ``_render_book_task_flow``, plus deep-link ``_chapter_deep_link`` import

Cross-package dependency: ``generic_key_label`` is NOT required here.
The renderer pulls in ``_chapter_deep_link`` from ``renderer_html`` for
deep-linking from per-chapter items back to the chapter reader.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QTextBrowser

from novel_forge.desktop.pages.standalone.renderer_html import (
    chapter_deep_link as _chapter_deep_link,
)
from novel_forge.desktop.pages.standalone.renderer_html import esc as _esc
from novel_forge.desktop.pages.standalone.renderer_html import html_wrap as _html_wrap
from novel_forge.desktop.pages.standalone.renderer_html import make_browser as _make_browser
from novel_forge.desktop.pages.standalone.renderer_html import nl2br as _nl2br
from novel_forge.desktop.pages.standalone.renderer_html import score_color as _score_color

JsonDict = dict[str, Any]

__all__ = [
    "render_book_consistency_report",
    "render_book_consistency_repair_report",
]


_CONSISTENCY_CATEGORY_LABELS: dict[str, str] = {
    "naming": "名称一致性",
    "timeline": "时间线",
    "worldbuilding": "世界观漂移",
    "character_state": "角色状态连续性",
    "narrative_drift": "叙事漂移",
}

_SEVERITY_STYLES: dict[str, tuple[str, str]] = {
    "critical": ("[[nf:status.danger.deep]]", "严重"),
    "high": ("[[nf:status.danger.deep]]", "严重"),
    "warning": ("[[nf:accent.primary]]", "警告"),
    "major": ("[[nf:accent.primary]]", "重要"),
    "medium": ("[[nf:accent.primary]]", "重要"),
    "info": ("[[nf:text.muted]]", "提示"),
    "minor": ("[[nf:text.fallback.status]]", "轻微"),
    "low": ("[[nf:text.fallback.status]]", "轻微"),
}


def _book_tag(text: str, *, tone: str = "muted") -> str:
    safe = _esc(str(text))
    if tone == "ok":
        return (
            '<span class="tag-muted tag" '
            'style="color:[[nf:status.success.warm]]; background:rgba([[nf:status.success.warm]], 0.08); '
            'border-color:rgba([[nf:status.success.warm]], 0.16);">'
            f"{safe}</span>"
        )
    if tone == "warn":
        return (
            '<span class="tag-muted tag" '
            'style="color:[[nf:status.danger.deep]]; background:rgba([[nf:status.danger.deep]], 0.08); '
            'border-color:rgba([[nf:status.danger.deep]], 0.16);">'
            f"{safe}</span>"
        )
    if tone == "active":
        return f'<span class="tag">{safe}</span>'
    return f'<span class="tag-muted tag">{safe}</span>'


def _book_metric(label: str, value: object, *, tone: str = "default") -> str:
    color = {"ok": "[[nf:status.success.warm]]", "warn": "[[nf:status.danger.deep]]", "active": "[[nf:accent.deep]]"}.get(tone, "[[nf:text.heading.deep]]")
    return (
        '<div class="section" style="text-align:center; padding:12px;">'
        f'<div style="font-size: 20pt; font-weight:700; color:{color};">{_esc(str(value))}</div>'
        f'<div style="color:[[nf:text.muted]]; font-size: 11pt; margin-top:2px;">{_esc(label)}</div>'
        "</div>"
    )


def _repair_status_label(status: str) -> str:
    return {
        "applied": "已应用",
        "skipped": "未匹配",
        "failed": "失败",
        "blocked": "已阻止",
        "no_change": "执行后无改动",
        "done": "已完成",
        "queued": "已排程",
        "completed": "已完成",
    }.get(status, status or "未知")


_VERIFICATION_STATUS_LABELS: dict[str, str] = {
    "resolved": "已解决",
    "partial": "部分解决",
    "unresolved": "未解决",
    "regressed": "疑似回归",
    "failed": "失败",
    "blocked": "已阻止",
    "skipped": "已跳过",
}


def _verification_counts_from_results(results: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not isinstance(results, list):
        return counts
    for item in results:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "") or "").strip()
        if not status:
            continue
        counts[status] = counts.get(status, 0) + 1
    return counts


def _verification_counts_from_summary(summary: Any) -> dict[str, int]:
    if not isinstance(summary, dict):
        return {}
    by_status = summary.get("by_status", {})
    if not isinstance(by_status, dict):
        return {}
    counts: dict[str, int] = {}
    for status, count in by_status.items():
        try:
            value = int(count or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            counts[str(status)] = value
    return counts


def _format_verification_counts(counts: dict[str, int]) -> str:
    if not counts:
        return ""
    ordered_statuses = [
        "resolved",
        "partial",
        "unresolved",
        "regressed",
        "failed",
        "blocked",
        "skipped",
    ]
    parts = [
        f"{_VERIFICATION_STATUS_LABELS.get(status, status)} {counts[status]} 项"
        for status in ordered_statuses
        if counts.get(status, 0) > 0
    ]
    parts.extend(
        f"{_VERIFICATION_STATUS_LABELS.get(status, status)} {count} 项"
        for status, count in sorted(counts.items())
        if status not in ordered_statuses and count > 0
    )
    return "，".join(parts)


def _format_admission_summary(summary: Any) -> str:
    if not isinstance(summary, dict):
        return ""

    def _int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    eligible = _int(summary.get("eligible_issue_count"))
    ineligible = _int(summary.get("ineligible_issue_count"))
    below = _int(summary.get("below_threshold_count"))
    if eligible <= 0 and ineligible <= 0 and below <= 0:
        return ""
    lines = [f"修复准入：{eligible} 条进入自动修复，{ineligible} 条降级为人工/计划处理。"]
    by_status = summary.get("by_status")
    if isinstance(by_status, dict) and by_status:
        status_labels = {
            "ready": "定位充分",
            "verify_first": "需先复核定位",
            "manual_review": "人工复核",
            "blocked": "已阻断",
        }
        status_parts = []
        for key, value in sorted(by_status.items()):
            count = _int(value)
            if count > 0:
                status_parts.append(f"{status_labels.get(str(key), str(key))} {count}")
        if status_parts:
            lines.append(f"准入状态：{'，'.join(status_parts)}。")
    by_reason = summary.get("by_reason")
    if isinstance(by_reason, dict) and by_reason:
        reason_parts = [
            f"{str(key)} {_int(value)}"
            for key, value in list(by_reason.items())[:5]
            if _int(value) > 0
        ]
        if reason_parts:
            lines.append(f"复核/降级原因：{'，'.join(reason_parts)}。")
    if below > 0:
        lines.append(f"低于本次严重度阈值：{below} 条。")
    return "<br>".join(_esc(line) for line in lines)


def _render_book_task_flow(
    *,
    request: dict[str, Any],
    issue_count: int,
    score: float | None,
    auto_repair: dict[str, Any] | None = None,
    repair_report: dict[str, Any] | None = None,
) -> str:
    auto = auto_repair or {}
    task_flow = repair_report.get("task_flow", {}) if isinstance(repair_report, dict) else {}
    matching = repair_report.get("matching", {}) if isinstance(repair_report, dict) else {}
    post_repair = (
        repair_report.get("post_repair_summary", {})
        if isinstance(repair_report, dict)
        else auto.get("post_repair_summary", {})
    )
    verify = (
        repair_report.get("verify", {})
        if isinstance(repair_report, dict)
        else auto.get("verify", {})
    )

    repair_mode = str(request.get("repair_mode") or task_flow.get("repair_mode") or "off")
    issue_pool_size = int(request.get("issue_pool_size") or matching.get("issue_pool_size") or 0)
    targeted = int(auto.get("targeted_chapters") or task_flow.get("targeted_chapters") or 0)
    processed = int(auto.get("processed_chapters") or task_flow.get("processed_chapters") or 0)
    applied = int(auto.get("applied_chapters") or task_flow.get("applied_chapters") or 0)
    failed = int(auto.get("failed_chapters") or task_flow.get("failed_chapters") or 0)
    blocked = int(auto.get("blocked_chapters") or task_flow.get("blocked_chapters") or 0)
    repair_items = (
        repair_report.get("chapters", [])
        if isinstance(repair_report, dict)
        else auto.get("details", [])
    )
    if not isinstance(repair_items, list):
        repair_items = []
    no_change = sum(
        1 for item in repair_items if isinstance(item, dict) and item.get("status") == "no_change"
    )
    checked = int(post_repair.get("issues_checked", 0) or 0) if isinstance(post_repair, dict) else 0
    closed = int(post_repair.get("issues_closed", 0) or 0) if isinstance(post_repair, dict) else 0
    remaining = (
        int(post_repair.get("issues_remaining", 0) or 0) if isinstance(post_repair, dict) else 0
    )
    verified = int(verify.get("verified", 0) or 0) if isinstance(verify, dict) else 0
    rejected = int(verify.get("rejected", 0) or 0) if isinstance(verify, dict) else 0
    warnings = (
        repair_report.get("cross_chapter_warnings", [])
        if isinstance(repair_report, dict)
        else auto.get("cross_chapter_warnings", [])
    )
    warning_count = len(warnings) if isinstance(warnings, list) else 0
    verification_counts = _verification_counts_from_summary(
        repair_report.get("verification_summary", {}) if isinstance(repair_report, dict) else {}
    )
    if not verification_counts:
        verification_counts = _verification_counts_from_summary(
            auto.get("verification_summary", {})
        )
    if not verification_counts and repair_items:
        for item in repair_items:
            if not isinstance(item, dict):
                continue
            for status, count in _verification_counts_from_results(
                item.get("verification_results", [])
            ).items():
                verification_counts[status] = verification_counts.get(status, 0) + count
    unresolved = int(verification_counts.get("unresolved", 0) or 0)
    regressed = int(verification_counts.get("regressed", 0) or 0)

    score_text = f"{score:.1f}" if isinstance(score, (int, float)) else "未记录"
    rows = [
        (
            "准备审计",
            "已完成",
            f"范围 {len(request.get('chapter_range', []) or [])} 章；模式 {request.get('analysis_mode', 'auto')}",
            "ok",
        ),
        ("问题池对齐", "已完成", f"锚点 {issue_pool_size} 条", "ok"),
        (
            "一致性审计",
            "已完成",
            f"问题 {issue_count} 个；评分 {score_text}",
            "warn" if issue_count else "ok",
        ),
        ("审计归档", "已完成", "reports/book_consistency_audit.json", "ok"),
        (
            "逐章验证",
            "已完成" if verified or rejected else "按需跳过",
            f"通过 {verified} 条；剔除 {rejected} 条"
            if verified or rejected
            else "仅全文定向修复时启用",
            "ok" if verified or rejected else "muted",
        ),
        (
            "修复排程",
            "已排程" if repair_mode == "targeted" else "未启用",
            f"目标 {targeted} 章；模式 {repair_mode}",
            "active" if repair_mode == "targeted" else "muted",
        ),
        (
            "逐章修复",
            "已完成" if processed else "按需跳过",
            f"处理 {processed}/{targeted} 章；改动 {applied} 章；无改动 {no_change} 章；阻止 {blocked} 章；失败 {failed} 章",
            "warn" if failed or blocked or no_change else ("ok" if processed else "muted"),
        ),
        (
            "修复复核",
            "已完成" if checked or warning_count else "随修复集成",
            f"本地证据关闭 {closed}/{checked}；残留 {remaining}；未解决票据 {unresolved}；回归 {regressed}；跨章提醒 {warning_count}",
            "warn"
            if remaining or unresolved or regressed or warning_count
            else ("ok" if checked else "muted"),
        ),
        ("报告归档", "已完成", "审计报告 / 修复报告", "ok"),
    ]

    complete_statuses = {"已完成", "已排程", "按需跳过", "未启用", "随修复集成"}
    completed_count = sum(
        1 for _title, status, _detail, _tone in rows if status in complete_statuses
    )
    active_title = next(
        (title for title, status, _detail, _tone in rows if status not in complete_statuses),
        rows[-1][0],
    )
    artifact_text = (
        "审计报告 + 修复报告"
        if repair_mode == "targeted" and (processed or isinstance(repair_report, dict))
        else "审计报告"
    )
    html_rows = [
        '<div class="section" style="padding:10px 12px;">'
        '<div style="display:grid; grid-template-columns:repeat(3, minmax(0,1fr)); gap:8px;">'
        + _book_metric("当前阶段", active_title, tone="active")
        + _book_metric("阶段进度", f"{completed_count}/{len(rows)}")
        + _book_metric("产物", artifact_text, tone="ok")
        + "</div></div>"
    ]

    for idx, (title, status, detail, tone) in enumerate(rows, start=1):
        html_rows.append(
            '<div class="rule-item" style="border-left-color:rgba([[nf:border.default]], 0.24);">'
            f'<span style="color:[[nf:text.muted]]; font-size: 11pt;">{idx}/{len(rows)}</span> '
            f"<b>{_esc(title)}</b> {_book_tag(status, tone=tone)}"
            f'<br><span style="color:[[nf:text.chapter.rail]]; font-size: 12pt;">{_esc(detail)}</span>'
            "</div>"
        )
    return '<div class="section">' + "\n".join(html_rows) + "</div>"


def render_book_consistency_report(data: JsonDict) -> QTextBrowser:
    """Render a book_consistency_audit.json as formatted HTML."""
    score = data.get("consistency_score")
    issues = data.get("issues", [])
    summary = data.get("summary", "")
    request = data.get("request", {}) if isinstance(data.get("request"), dict) else {}
    auto_repair = data.get("auto_repair") if isinstance(data.get("auto_repair"), dict) else None
    acceptance = data.get("acceptance") if isinstance(data.get("acceptance"), dict) else {}
    quality_metrics = (
        data.get("quality_metrics") if isinstance(data.get("quality_metrics"), dict) else {}
    )
    field_sources = data.get("field_sources") if isinstance(data.get("field_sources"), dict) else {}

    parts: list[str] = []

    if score is not None:
        score_val = float(score)
        color = _score_color(score_val)
        issue_count = len(issues)
        badge_text = "全书一致" if issue_count == 0 else f"{issue_count} 个问题"
        badge_bg = "[[nf:status.success.warm]]" if issue_count == 0 else "[[nf:accent.primary]]"
        parts.append(
            f'<div class="section" style="text-align:center; padding:20px;">'
            f'<div style="font-size: 42pt; font-weight:700; '
            f"font-family:'Songti SC',serif; color:{color};\">{score_val:.1f}</div>"
            f'<div style="color:[[nf:text.muted]]; font-size: 12pt; margin-top:2px;">全书一致性评分</div>'
            f'<div style="margin-top:6px;">'
            f'<span class="tag" style="color:{badge_bg};">{badge_text}</span>'
            f"</div></div>"
        )

    if acceptance:
        status = str(acceptance.get("status", "") or "")
        status_label = {
            "ready_to_export": "建议导出",
            "pass_with_notes": "可导出，保留提示",
            "review_recommended": "建议复核",
            "needs_attention": "需要处理",
        }.get(status, status or "未判定")
        status_tone = (
            "ok"
            if status == "ready_to_export"
            else ("warn" if status in {"review_recommended", "needs_attention"} else "active")
        )
        recommendation = str(acceptance.get("recommendation", "") or "")
        parts.append("<h2>终章验收</h2>")
        parts.append(
            '<div class="section" style="padding:10px 12px;">'
            '<div style="display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:8px;">'
            + _book_metric("验收状态", status_label, tone=status_tone)
            + _book_metric(
                "严重问题",
                str(acceptance.get("critical_issues", 0)),
                tone="warn" if acceptance.get("critical_issues") else "ok",
            )
            + _book_metric(
                "警告问题",
                str(acceptance.get("warning_issues", 0)),
                tone="warn" if acceptance.get("warning_issues") else "ok",
            )
            + _book_metric(
                "下一步",
                "导出" if acceptance.get("suggest_export") else "复核",
                tone="ok" if acceptance.get("suggest_export") else "warn",
            )
            + "</div>"
            + (
                f'<div class="hint-block" style="margin-top:8px;">{_esc(recommendation)}</div>'
                if recommendation
                else ""
            )
            + "</div>"
        )

    if request:
        chapters = request.get("chapter_range", [])
        chapter_count = len(chapters) if isinstance(chapters, list) else 0
        analysis_mode = str(request.get("analysis_mode", "auto") or "auto")
        location = str(request.get("location_strictness", "balanced") or "balanced")
        repair_mode = str(request.get("repair_mode", "off") or "off")
        pool_size = int(request.get("issue_pool_size", 0) or 0)
        parts.append(
            '<table style="table-layout:fixed;"><tr><td>'
            + _book_metric("审计范围", f"{chapter_count} 章")
            + "</td><td>"
            + _book_metric("审计模式", analysis_mode)
            + "</td><td>"
            + _book_metric("定位严格度", location)
            + "</td><td>"
            + _book_metric("问题池", f"{pool_size} 条", tone="active" if pool_size else "default")
            + "</td></tr></table>"
        )
        parts.append(
            '<div style="text-align:center; margin:4px 0 10px 0;">'
            + _book_tag(
                f"修复模式：{repair_mode}", tone="active" if repair_mode == "targeted" else "muted"
            )
            + _book_tag(f"批量上限：{request.get('audit_max_chapters_per_batch', 'auto')}")
            + _book_tag(f"输出上限：{request.get('max_tokens', 'auto')}")
            + "</div>"
        )

    if summary:
        parts.append(f'<h2>审计概要</h2><div class="hint-block">{_nl2br(_esc(summary))}</div>')

    if quality_metrics or field_sources:
        metric_bits: list[str] = []
        if quality_metrics:
            coverage = float(quality_metrics.get("coverage_ratio", 0.0) or 0.0)
            miss = float(quality_metrics.get("estimated_miss_rate", 0.0) or 0.0)
            depth = str(quality_metrics.get("audit_depth", "") or "")
            metric_bits.append(
                _book_metric("覆盖率", f"{coverage:.0%}", tone="ok" if coverage >= 0.8 else "warn")
            )
            metric_bits.append(
                _book_metric("估计漏检", f"{miss:.0%}", tone="warn" if miss >= 0.2 else "ok")
            )
            metric_bits.append(_book_metric("审计深度", depth or "未记录"))
        if field_sources:
            metric_bits.append(
                _book_metric(
                    "概要来源",
                    "AI 原生" if field_sources.get("summary") == "llm" else "系统推导",
                )
            )
            metric_bits.append(
                _book_metric(
                    "评分来源",
                    "AI 原生" if field_sources.get("consistency_score") == "llm" else "系统推导",
                )
            )
        parts.append(
            '<div class="section" style="padding:10px 12px;">'
            '<div style="display:grid; grid-template-columns:repeat(5, minmax(0,1fr)); gap:8px;">'
            + "".join(metric_bits)
            + "</div></div>"
        )

    score_for_flow = float(score) if isinstance(score, (int, float)) else None
    parts.append("<h2>任务流与产物</h2>")
    parts.append(
        _render_book_task_flow(
            request=request,
            issue_count=len(issues),
            score=score_for_flow,
            auto_repair=auto_repair,
        )
    )

    if issues:
        by_cat: dict[str, list[JsonDict]] = {}
        for issue in issues:
            if isinstance(issue, dict):
                category = issue.get("category", "other")
                by_cat.setdefault(category, []).append(issue)
            else:
                by_cat.setdefault("other", []).append({"description": str(issue)})

        for category, category_issues in by_cat.items():
            cat_label = _CONSISTENCY_CATEGORY_LABELS.get(category, category)
            parts.append(f"<h2>{_esc(cat_label)} · {len(category_issues)} 项</h2>")
            for issue in category_issues:
                severity = issue.get("severity", "minor")
                desc = issue.get("description", "")
                suggestion = issue.get("suggestion", "")
                chapters = issue.get("chapters_involved", [])
                location = str(issue.get("location", "") or "")
                _raw_pi = issue.get("paragraph_index", 0) or 0
                if isinstance(_raw_pi, list):
                    _raw_pi = _raw_pi[0] if _raw_pi else 0
                try:
                    paragraph_index = int(_raw_pi)
                except (TypeError, ValueError):
                    paragraph_index = 0
                evidence = str(issue.get("evidence", "") or "").strip()
                fix_mode = str(issue.get("fix_mode", "") or "").strip()
                fix_action = str(issue.get("fix_action", "") or "").strip()
                confidence = issue.get("confidence", None)
                linked_refs = issue.get("linked_issue_refs", [])
                sev_color, sev_label = _SEVERITY_STYLES.get(
                    severity,
                    ("[[nf:text.muted]]", severity),
                )
                parts.append(
                    f'<div class="rule-item" style="border-left-color:{sev_color};">'
                    f'<span class="tag" style="color:{sev_color}; font-size: 10pt;">'
                    f"{sev_label}</span> {_esc(desc)}"
                )
                if chapters:
                    chapter_str = ", ".join(f"第{chapter}章" for chapter in chapters)
                    parts.append(
                        f'<br><span style="color:[[nf:text.muted]]; font-size: 12pt;">'
                        f"涉及章节：{_esc(chapter_str)}</span>"
                    )
                if paragraph_index > 0 or location:
                    location_text = location or f"第{paragraph_index}段"
                    parts.append(
                        f'<br><span style="color:[[nf:text.muted]]; font-size: 12pt;">'
                        f"定位：{_esc(location_text)}</span>"
                    )
                if evidence:
                    parts.append(
                        f'<br><span style="color:[[nf:text.memory.review]]; font-size: 12pt;">'
                        f"证据：{_esc(evidence)}</span>"
                    )
                if fix_mode or fix_action or confidence is not None:
                    fix_meta: list[str] = []
                    if fix_mode:
                        fix_meta.append(f"模式={fix_mode}")
                    if fix_action:
                        fix_meta.append(f"动作={fix_action}")
                    if isinstance(confidence, (int, float)):
                        fix_meta.append(f"置信度={float(confidence):.2f}")
                    if fix_meta:
                        parts.append(
                            f'<br><span style="color:[[nf:text.muted]]; font-size: 12pt;">'
                            f"修复建议：{_esc('，'.join(fix_meta))}</span>"
                        )
                if isinstance(linked_refs, list) and linked_refs:
                    linked_parts: list[str] = []
                    for ref in linked_refs[:3]:
                        if not isinstance(ref, dict):
                            continue
                        ch = int(ref.get("chapter_number", 0) or 0)
                        lane = str(ref.get("lane", "") or "").strip()
                        idx = int(ref.get("index", -1) or -1)
                        if ch > 0 and lane and idx >= 0:
                            linked_parts.append(f"ch{ch}:{lane}[{idx}]")
                    if linked_parts:
                        parts.append(
                            f'<br><span style="color:[[nf:text.chapter.rail]]; font-size: 12pt;">'
                            f"问题池锚点：{_esc('，'.join(linked_parts))}</span>"
                        )
                if suggestion:
                    parts.append(
                        f'<br><span style="color:[[nf:text.memory.fix]]; font-size: 12pt;">'
                        f"💡 {_esc(suggestion)}</span>"
                    )
                parts.append("</div>")

    if isinstance(auto_repair, dict) and auto_repair.get("enabled"):
        parts.append("<h2>自动修复结果</h2>")
        processed = int(auto_repair.get("processed_chapters", 0) or 0)
        applied = int(auto_repair.get("applied_chapters", 0) or 0)
        failed = int(auto_repair.get("failed_chapters", 0) or 0)
        blocked = int(auto_repair.get("blocked_chapters", 0) or 0)
        repair_details = [item for item in auto_repair.get("details", []) if isinstance(item, dict)]
        no_change = sum(1 for item in repair_details if item.get("status") == "no_change")
        skipped = sum(1 for item in repair_details if item.get("status") == "skipped")
        unaccounted_no_write = max(0, processed - applied - failed - blocked - no_change - skipped)
        min_sev = str(auto_repair.get("min_severity", "warning") or "warning")
        matched_ref = int(auto_repair.get("matched_by_ref", 0) or 0)
        matched_fuzzy = int(auto_repair.get("matched_by_fuzzy", 0) or 0)
        panel_expanded = int(auto_repair.get("panel_expanded", 0) or 0)
        post_targeted = (
            auto_repair.get("post_repair_targeted_audit")
            if isinstance(auto_repair.get("post_repair_targeted_audit"), dict)
            else {}
        )

        # ── Summary row ───────────────────────────────────────────────
        sev_label = {"warning": "中及以上", "critical": "严重", "info": "全部"}.get(
            min_sev, min_sev
        )
        summary_parts = [f"扫描 {processed} 个含问题章节（严重度阈值：{sev_label}）"]
        if applied > 0:
            summary_parts.append(f'<span style="color:[[nf:status.success.warm]]">✓ 成功修复 {applied} 章</span>')
        if no_change > 0:
            summary_parts.append(f'<span style="color:[[nf:text.muted]]">— {no_change} 章执行后无改动</span>')
        if skipped > 0:
            summary_parts.append(f'<span style="color:[[nf:text.muted]]">— {skipped} 章未匹配可执行修复</span>')
        if unaccounted_no_write > 0:
            summary_parts.append(
                f'<span style="color:[[nf:text.muted]]">— {unaccounted_no_write} 章未写入</span>'
            )
        if failed > 0:
            summary_parts.append(f'<span style="color:[[nf:accent.primary]]">✗ {failed} 章失败</span>')
        if blocked > 0:
            summary_parts.append(
                f'<span style="color:[[nf:accent.primary]]">已阻止并回滚 {blocked} 章疑似污染写入</span>'
            )
        parts.append(f'<div class="hint-block">{"　".join(summary_parts)}</div>')

        admission_text = _format_admission_summary(auto_repair.get("admission_summary"))
        if admission_text:
            parts.append(
                '<div style="color:[[nf:text.chapter.rail]]; font-size: 12pt; margin:2px 0 8px 0;">'
                f"{admission_text}</div>"
            )

        # ── Matching strategy note ────────────────────────────────────
        match_notes: list[str] = []
        if matched_ref > 0:
            match_notes.append(f"引用锚点匹配 {matched_ref} 处")
        if matched_fuzzy > 0:
            match_notes.append(f"文本模糊匹配 {matched_fuzzy} 处")
        if panel_expanded > 0:
            match_notes.append(f"面板高优先级扩展 {panel_expanded} 处")
        if match_notes:
            parts.append(
                f'<div style="color:[[nf:text.muted]]; font-size: 12pt; margin:2px 0 8px 0;">'
                f"{'；'.join(match_notes)}</div>"
            )

        verification_counts = _verification_counts_from_summary(
            auto_repair.get("verification_summary", {})
        )
        if verification_counts:
            parts.append(
                '<div style="color:[[nf:text.chapter.rail]]; font-size: 12pt; margin:2px 0 8px 0;">'
                f"票据验证：{_esc(_format_verification_counts(verification_counts))}</div>"
            )

        regression_summary = auto_repair.get("regression_summary")
        if isinstance(regression_summary, dict):
            total_regressions = int(regression_summary.get("total_regressions", 0) or 0)
            if total_regressions > 0:
                by_severity = regression_summary.get("by_severity", {})
                severity_parts: list[str] = []
                if isinstance(by_severity, dict):
                    for key, label in (
                        ("critical", "严重"),
                        ("warning", "警告"),
                        ("info", "提示"),
                    ):
                        count = int(by_severity.get(key, 0) or 0)
                        if count > 0:
                            severity_parts.append(f"{label} {count}")
                severity_text = f"（{'，'.join(severity_parts)}）" if severity_parts else ""
                parts.append(
                    '<div style="color:[[nf:status.danger.deep]]; font-size: 12pt; margin:2px 0 8px 0;">'
                    f"规则回归候选：命中 {total_regressions} 项本地信号，"
                    f"需 LLM/人工复核{_esc(severity_text)}</div>"
                )

        if post_targeted:
            post_status = str(post_targeted.get("status", "") or "")
            target_chapters = post_targeted.get("target_chapters", [])
            issue_count = int(post_targeted.get("issue_count", 0) or 0)
            post_tone = "ok" if post_status == "completed" and issue_count == 0 else "warn"
            parts.append(
                '<div class="rule-item" style="margin:6px 0;">'
                f"<b>修复后二次小审计</b> {_book_tag(post_status or '未运行', tone=post_tone)}"
                f'<br><span style="color:[[nf:text.chapter.rail]]; font-size: 12pt;">'
                f"复查章节：{_esc('、'.join(str(ch) for ch in target_chapters) or '无')}；"
                f"命中问题：{issue_count}</span></div>"
            )
        elif applied > 0 or no_change > 0 or skipped > 0:
            parts.append(
                '<div class="rule-item" style="margin:6px 0;">'
                f"<b>修复后二次小审计</b> {_book_tag('未开启/无记录', tone='muted')}"
                "</div>"
            )

        # ── Per-chapter details ───────────────────────────────────────
        _STATUS_ICON = {
            "applied": "✓",
            "no_change": "—",
            "skipped": "—",
            "blocked": "!",
            "failed": "✗",
            "error": "✗",
        }
        _STATUS_COLOR = {
            "applied": "[[nf:status.success.warm]]",
            "no_change": "[[nf:text.muted]]",
            "skipped": "[[nf:text.muted]]",
            "blocked": "[[nf:accent.primary]]",
            "failed": "[[nf:accent.primary]]",
            "error": "[[nf:accent.primary]]",
        }
        for item in repair_details:
            ch = int(item.get("chapter_number", 0) or 0)
            status = str(item.get("status", "") or "")
            reason = str(item.get("reason", "") or "")
            cont_applied = bool(item.get("continuity_applied", False))
            causal_applied = bool(item.get("causal_applied", False))
            match_mode = str(item.get("match_mode", "") or "")
            icon = _STATUS_ICON.get(status, "·")
            color = _STATUS_COLOR.get(status, "[[nf:text.body]]")

            # Build human-readable detail
            detail_parts: list[str] = []
            if status == "applied":
                if cont_applied:
                    detail_parts.append("连贯性修复 ✓")
                if causal_applied:
                    detail_parts.append("因果修复 ✓")
                if not cont_applied and not causal_applied:
                    detail_parts.append("已写入")
                mode_label = {"panel": "面板优先", "fuzzy": "模糊匹配", "ref": "引用锚点"}.get(
                    match_mode, match_mode
                )
                if mode_label:
                    detail_parts.append(f"匹配方式：{mode_label}")
            elif status == "no_change":
                detail_parts.append("修复器执行完成但未写入正文")
                if reason:
                    detail_parts.append(reason)
            elif status == "skipped":
                detail_parts.append(reason or "无匹配的可执行修复项")
            elif status == "blocked":
                detail_parts.append(reason or "修复结果未通过防污染闸门，已回滚")
            elif status in ("failed", "error"):
                detail_parts.append(reason or "修复执行失败")

            item_verification = _format_verification_counts(
                _verification_counts_from_results(item.get("verification_results", []))
            )
            if item_verification:
                detail_parts.append(f"票据验证：{item_verification}")
            post_check = item.get("post_repair_check")
            if isinstance(post_check, dict):
                checked = int(post_check.get("issues_checked", 0) or 0)
                closed = int(post_check.get("issues_closed", 0) or 0)
                remaining = int(post_check.get("issues_remaining", 0) or 0)
                detail_parts.append(f"证据复核：关闭 {closed}/{checked}，残留 {remaining}")
            manual_reason = str(item.get("manual_review_reason", "") or "").strip()
            if manual_reason:
                detail_parts.append(f"需人工复核：{manual_reason}")

            detail_str = "　".join(detail_parts)
            parts.append(
                f'<div class="rule-item" style="margin:3px 0;">'
                f'<span style="color:{color}; font-weight:600;">{icon} 第 {ch} 章</span>'
                + (f'　<span style="color:[[nf:text.body]];">{_esc(detail_str)}</span>' if detail_str else "")
                + "</div>"
            )

        # ── Excluded chapters (cut by max_chapters cap) ──────────────
        excl = auto_repair.get("excluded_chapters")
        if isinstance(excl, list) and excl:
            excl_nums = "、".join(f"第 {c} 章" for c in sorted(int(c) for c in excl))
            max_ch = int(auto_repair.get("max_chapters", 0) or 0)
            cap_note = f"（本次上限 {max_ch} 章）" if max_ch > 0 else ""
            parts.append(
                f'<div style="margin-top:10px; padding:6px 10px; border-left:3px solid [[nf:brand.logo.border]]; '
                f'background:[[nf:bg.surface]]; color:[[nf:accent.rank.label]]; font-size: 12pt;">'
                f"⚠ 尚有 {len(excl)} 个章节存在问题、本次未处理{cap_note}，"
                f"建议下次运行时继续修复：<br/>"
                f'<span style="font-weight:600;">{excl_nums}</span></div>'
            )

    return _make_browser(_html_wrap("\n".join(parts), "全书一致性审计"))


def render_book_consistency_repair_report(data: JsonDict) -> QTextBrowser:
    """Render reports/book_consistency_repair_report.json as formatted HTML."""
    project_id = str(data.get("project_id", "") or "")
    summary = str(data.get("summary", "") or "").strip()
    analysis = data.get("analysis", {}) if isinstance(data.get("analysis"), dict) else {}
    task_flow = data.get("task_flow", {}) if isinstance(data.get("task_flow"), dict) else {}
    matching = data.get("matching", {}) if isinstance(data.get("matching"), dict) else {}
    chapters = data.get("chapters", [])
    if not isinstance(chapters, list):
        chapters = []

    targeted = int(task_flow.get("targeted_chapters", 0) or 0)
    processed = int(task_flow.get("processed_chapters", 0) or 0)
    applied = int(task_flow.get("applied_chapters", 0) or 0)
    failed = int(task_flow.get("failed_chapters", 0) or 0)
    blocked = int(task_flow.get("blocked_chapters", 0) or 0)
    no_change = sum(
        1 for item in chapters if isinstance(item, dict) and item.get("status") == "no_change"
    )
    issue_count = int(analysis.get("issue_count", 0) or 0)
    score = float(analysis.get("consistency_score", 0.0) or 0.0)
    matched_ref = int(matching.get("matched_by_ref", 0) or 0)
    matched_fuzzy = int(matching.get("matched_by_fuzzy", 0) or 0)
    panel_expanded = int(matching.get("panel_expanded", 0) or 0)
    pool_size = int(matching.get("issue_pool_size", 0) or 0)
    concurrency = int(task_flow.get("repair_concurrency", 1) or 1)

    parts: list[str] = []
    parts.append(
        '<div class="section" style="text-align:center; padding:20px;">'
        f"<div style=\"font-size: 42pt; font-weight:700; font-family:'Songti SC',serif; color:{_score_color(score)};\">{score:.1f}</div>"
        '<div style="color:[[nf:text.muted]]; font-size: 12pt; margin-top:2px;">审计后一致性评分</div>'
        f'<div style="margin-top:8px;"><span class="tag-muted tag">问题 {issue_count}</span> '
        f'<span class="tag-muted tag">已处理 {processed}/{targeted} 章</span> '
        f'<span class="tag-muted tag">改动 {applied} 章</span> '
        f'<span class="tag-muted tag">无改动 {no_change} 章</span> '
        f'<span class="tag-muted tag">阻止 {blocked} 章</span></div>'
        "</div>"
    )

    if summary:
        parts.append(f'<h2>修复概要</h2><div class="hint-block">{_nl2br(_esc(summary))}</div>')

    admission_text = _format_admission_summary(data.get("admission_summary"))
    if admission_text:
        parts.append(f'<h2>自动修复准入</h2><div class="hint-block">{admission_text}</div>')

    parts.append("<h2>任务流与产物</h2>")
    parts.append(
        _render_book_task_flow(
            request={
                "chapter_range": analysis.get("chapters_audited", []),
                "analysis_mode": analysis.get("analysis_mode", "summary"),
                "repair_mode": task_flow.get("repair_mode", "targeted"),
                "issue_pool_size": pool_size,
            },
            issue_count=issue_count,
            score=score,
            repair_report=data,
        )
    )
    parts.append(
        '<div style="text-align:center; margin:4px 0 10px 0;">'
        + _book_tag(f"状态：{task_flow.get('status', 'completed')}")
        + _book_tag(f"并发上限：{concurrency}")
        + _book_tag(f"阻止章节：{blocked}", tone="warn" if blocked else "muted")
        + _book_tag(f"失败章节：{failed}", tone="warn" if failed else "muted")
        + "</div>"
    )

    if pool_size > 0 or panel_expanded > 0:
        parts.append("<h2>精准定位匹配</h2>")
        match_line = (
            f"问题池总量：{pool_size}；引用锚点命中：{matched_ref}；文本回退命中：{matched_fuzzy}。"
        )
        if panel_expanded > 0:
            match_line += (
                f"<br/>面板优先扩展：{panel_expanded} 条"
                "（高/关键级别章节面板问题优先纳入修复，精度高于全书审计）。"
            )
        parts.append(f'<div class="hint-block">{match_line}</div>')

    post_repair = data.get("post_repair_summary")
    if isinstance(post_repair, dict) and post_repair:
        checked = int(post_repair.get("issues_checked", 0) or 0)
        closed = int(post_repair.get("issues_closed", 0) or 0)
        remaining = int(post_repair.get("issues_remaining", 0) or 0)
        needs_manual = data.get("needs_manual_review", [])
        manual_text = ""
        if isinstance(needs_manual, list) and needs_manual:
            manual_text = "；需人工复核：" + "、".join(f"第 {int(ch)} 章" for ch in needs_manual)
        parts.append("<h2>修复后复核</h2>")
        parts.append(
            '<div class="hint-block">'
            f"本地证据复核（仅统计已写入章节）：关闭 {closed}/{checked} 项，"
            f"仍存在 {remaining} 项{_esc(manual_text)}。"
            "</div>"
        )

    verification_counts = _verification_counts_from_summary(data.get("verification_summary", {}))
    if verification_counts:
        parts.append(
            "<h2>票据验证</h2>"
            f'<div class="hint-block">{_esc(_format_verification_counts(verification_counts))}。</div>'
        )

    regression_summary = data.get("regression_summary")
    if isinstance(regression_summary, dict):
        total_regressions = int(regression_summary.get("total_regressions", 0) or 0)
        if total_regressions > 0:
            by_severity = regression_summary.get("by_severity", {})
            severity_parts: list[str] = []
            if isinstance(by_severity, dict):
                for key, label in (("critical", "严重"), ("warning", "警告"), ("info", "提示")):
                    count = int(by_severity.get(key, 0) or 0)
                    severity_parts.append(f"{label} {count}")
            parts.append(
                "<h2>规则回归候选</h2>"
                '<div class="hint-block">'
                f"命中 {total_regressions} 项本地信号，需 LLM/人工复核"
                + (f"（{'，'.join(severity_parts)}）" if severity_parts else "")
                + "。</div>"
            )

    post_targeted = data.get("post_repair_targeted_audit")
    if isinstance(post_targeted, dict) and post_targeted:
        post_status = str(post_targeted.get("status", "") or "")
        target_chapters = post_targeted.get("target_chapters", [])
        issue_count = int(post_targeted.get("issue_count", 0) or 0)
        parts.append(
            "<h2>修复后二次小审计</h2>"
            '<div class="hint-block">'
            f"状态：{_esc(post_status or '未运行')}；"
            f"复查章节：{_esc('、'.join(str(ch) for ch in target_chapters) or '无')}；"
            f"命中问题：{issue_count}。</div>"
        )
    elif applied > 0 or no_change > 0:
        parts.append('<h2>修复后二次小审计</h2><div class="hint-block">未开启或无记录。</div>')

    cross_warnings = data.get("cross_chapter_warnings")
    if isinstance(cross_warnings, list) and cross_warnings:
        parts.append("<h2>跨章影响提醒</h2>")
        for warning in cross_warnings[:12]:
            parts.append(f'<div class="rule-item">{_nl2br(_esc(str(warning)))}</div>')

    if chapters:
        parts.append("<h2>逐章修复明细</h2>")
        for item in chapters:
            if not isinstance(item, dict):
                continue
            chapter_number = int(item.get("chapter_number", 0) or 0)
            status = str(item.get("status", "") or "")
            reason = str(item.get("reason", "") or "")
            cont_indices = item.get("continuity_issue_indices", [])
            causal_indices = item.get("causal_issue_indices", [])
            matched_issue_count = int(item.get("matched_issue_count", 0) or 0)
            match_mode = str(item.get("match_mode", "") or "")
            ch_panel_expanded = int(item.get("panel_expanded", 0) or 0)
            label_color = "[[nf:status.success.warm]]" if status == "applied" else "[[nf:text.muted]]"
            if status in {"failed", "skipped", "blocked"}:
                label_color = "[[nf:status.danger.deep]]" if status in {"failed", "blocked"} else "[[nf:text.muted]]"
            chapter_link = (
                _chapter_deep_link(project_id, chapter_number)
                if project_id and chapter_number > 0
                else f"第 {chapter_number} 章"
            )
            parts.append(
                f'<div class="rule-item" style="border-left-color:{label_color};">'
                f'<span class="tag" style="color:{label_color};">'
                f"{_esc(_repair_status_label(status))}</span> "
                f"{chapter_link}"
            )
            parts.append(
                f'<br><span style="color:[[nf:text.muted]]; font-size: 12pt;">'
                f"命中问题数：{matched_issue_count}；匹配模式：{_esc(match_mode or 'n/a')}"
            )
            if ch_panel_expanded > 0:
                parts.append(f"；面板扩展：{ch_panel_expanded}")
            parts.append("</span>")
            parts.append(
                f'<br><span style="color:[[nf:text.chapter.rail]]; font-size: 12pt;">'
                f"连贯性索引：{_esc(str(cont_indices))}；因果索引：{_esc(str(causal_indices))}</span>"
            )
            if reason:
                parts.append(
                    f'<br><span style="color:[[nf:status.danger.deep]]; font-size: 12pt;">原因：{_esc(reason)}</span>'
                )
            item_verification = _format_verification_counts(
                _verification_counts_from_results(item.get("verification_results", []))
            )
            if item_verification:
                parts.append(
                    f'<br><span style="color:[[nf:text.chapter.rail]]; font-size: 12pt;">'
                    f"票据验证：{_esc(item_verification)}</span>"
                )
            post_check = item.get("post_repair_check")
            if isinstance(post_check, dict):
                checked = int(post_check.get("issues_checked", 0) or 0)
                closed = int(post_check.get("issues_closed", 0) or 0)
                remaining = int(post_check.get("issues_remaining", 0) or 0)
                parts.append(
                    f'<br><span style="color:[[nf:text.chapter.rail]]; font-size: 12pt;">'
                    f"本地证据复核：关闭 {closed}/{checked}，残留 {remaining}</span>"
                )
            manual_reason = str(item.get("manual_review_reason", "") or "").strip()
            if manual_reason:
                parts.append(
                    f'<br><span style="color:[[nf:status.danger.deep]]; font-size: 12pt;">'
                    f"需人工复核：{_esc(manual_reason)}</span>"
                )
            parts.append("</div>")

    if not parts:
        parts.append('<div class="hint-block">暂无修复报告数据。</div>')
    browser = _make_browser(_html_wrap("\n".join(parts), "全书修复报告"))
    if project_id:
        browser.setProperty("has_deep_links", True)
    return browser
