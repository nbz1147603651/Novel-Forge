"""Whole-book consistency dialog payload helpers."""

from __future__ import annotations

from typing import Any


def book_dialog_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def book_dialog_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def book_dialog_issue_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    return book_dialog_int(value, 0)


def book_dialog_chapter_label(chapters: Any, *, limit: int = 8) -> str:
    if not isinstance(chapters, (list, tuple, set)):
        return ""
    resolved = sorted(
        {int(ch) for ch in chapters if isinstance(ch, (int, str)) and str(ch).strip().isdigit()}
    )
    if not resolved:
        return ""
    limited = resolved[:limit]
    ranges: list[tuple[int, int]] = []
    start = limited[0]
    prev = limited[0]
    for chapter in limited[1:]:
        if chapter == prev + 1:
            prev = chapter
            continue
        ranges.append((start, prev))
        start = prev = chapter
    ranges.append((start, prev))
    parts = [f"第 {start} 章" if start == end else f"第 {start}-{end} 章" for start, end in ranges]
    text = "、".join(parts)
    if len(resolved) > len(limited):
        text += f" 等 {len(resolved)} 章"
    return text


def book_dialog_unique_entries(entries: list[str], *, limit: int = 12) -> str:
    seen: set[str] = set()
    clean: list[str] = []
    for entry in entries:
        item = str(entry or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        clean.append(item)
    if not clean:
        return ""
    text = "、".join(clean[:limit])
    if len(clean) > limit:
        text += f" 等 {len(clean)} 项"
    return text


def book_dialog_stat_cards(main_text: str) -> list[tuple[str, str]]:
    cards: list[tuple[str, str]] = []
    for part in [item.strip() for item in str(main_text or "").split("　·　") if item.strip()]:
        if part.startswith("一致性评分 "):
            cards.append(("一致性评分", part.removeprefix("一致性评分 ")))
        elif part.startswith("发现问题 "):
            cards.append(("发现问题", part.removeprefix("发现问题 ")))
        elif part.startswith("严重 "):
            cards.append(("问题等级", part))
        elif part.startswith("验证过滤 "):
            cards.append(("验证过滤", part.removeprefix("验证过滤 ")))
        elif part.startswith("修复 "):
            cards.append(("自动修复", part.removeprefix("修复 ")))
        elif part.startswith("阻止 "):
            cards.append(("防污染闸门", part.removeprefix("阻止 ")))
        else:
            cards.append(("状态", part))
    return cards


def book_dialog_severity_counts(result: dict[str, Any]) -> tuple[int, int, int]:
    raw_acceptance = result.get("acceptance")
    acceptance = raw_acceptance if isinstance(raw_acceptance, dict) else {}
    critical = book_dialog_issue_count(acceptance.get("critical_issues"))
    warning = book_dialog_issue_count(acceptance.get("warning_issues"))
    info = book_dialog_issue_count(acceptance.get("info_issues"))
    if critical or warning or info:
        return critical, warning, info
    raw_issues = result.get("issues")
    issues = raw_issues if isinstance(raw_issues, list) else []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        severity = str(issue.get("severity", "") or "").strip().lower()
        if severity in {"critical", "high", "major"}:
            critical += 1
        elif severity in {"warning", "medium"}:
            warning += 1
        else:
            info += 1
    return critical, warning, info


def book_dialog_acceptance_lines(result: dict[str, Any], auto_repair: Any) -> list[str]:
    acceptance = result.get("acceptance") if isinstance(result.get("acceptance"), dict) else {}
    if not acceptance and isinstance(auto_repair, dict):
        acceptance = (
            auto_repair.get("acceptance") if isinstance(auto_repair.get("acceptance"), dict) else {}
        )
    if not acceptance:
        return []
    status = str(acceptance.get("status", "") or "").strip()
    recommendation = str(acceptance.get("recommendation", "") or "").strip()
    suggest_export = acceptance.get("suggest_export")
    status_labels = {
        "needs_attention": "需先处理",
        "review_recommended": "建议复核",
        "ready_to_export": "可进入导出",
        "pass_with_notes": "可导出但保留备注",
    }
    suffix = ""
    if suggest_export is True:
        suffix = "（可进入导出/发布前检查）"
    elif suggest_export is False:
        suffix = "（暂不建议直接导出）"
    label = status_labels.get(status, status or "未标注")
    if recommendation:
        return [f"验收建议：{label}，{recommendation}{suffix}"]
    return [f"验收建议：{label}{suffix}"]


def book_dialog_quality_lines(result: dict[str, Any]) -> list[str]:
    metrics = result.get("quality_metrics")
    if not isinstance(metrics, dict):
        return []
    coverage = book_dialog_float(metrics.get("coverage_ratio"), -1.0)
    miss = book_dialog_float(metrics.get("estimated_miss_rate"), -1.0)
    audit_depth = str(metrics.get("audit_depth", "") or "").strip()
    parts: list[str] = []
    if audit_depth:
        parts.append(f"深度 {audit_depth}")
    if coverage >= 0:
        parts.append(f"覆盖率 {coverage * 100:.0f}%")
    if miss >= 0:
        parts.append(f"估计漏检 {miss * 100:.0f}%")
    dimension_scores = metrics.get("dimension_scores")
    top_dims: list[str] = []
    if isinstance(dimension_scores, dict):
        for key, value in sorted(dimension_scores.items())[:4]:
            score = book_dialog_float(value, 0.0)
            top_dims.append(f"{key} {score:.1f}")
    lines = [f"审计质量：{'，'.join(parts)}。"] if parts else []
    if top_dims:
        lines.append(f"维度得分：{'，'.join(top_dims)}。")
    return lines


def book_dialog_post_audit_issue_lines(post_targeted: dict[str, Any]) -> list[str]:
    post_issues = post_targeted.get("issues")
    if not isinstance(post_issues, list) or not post_issues:
        return []
    severity_labels = {
        "critical": "严重",
        "high": "严重",
        "warning": "警告",
        "medium": "警告",
        "info": "提示",
        "low": "提示",
    }
    lines = ["二次审计详情："]
    for issue in post_issues[:3]:
        if not isinstance(issue, dict):
            continue
        severity = severity_labels.get(str(issue.get("severity", "") or "").lower(), "问题")
        category = str(issue.get("category", "") or issue.get("issue_type", "") or "未分类")
        desc = str(issue.get("description", "") or issue.get("summary", "") or "").strip()
        involved = issue.get("chapters_involved")
        if not isinstance(involved, (list, tuple, set)):
            involved = []
        chapter = book_dialog_chapter_label(
            [issue.get("primary_chapter"), *involved],
            limit=3,
        )
        prefix = f"  - {severity}/{category}"
        if chapter:
            prefix += f"/{chapter}"
        lines.append(f"{prefix}：{desc[:90]}")
    if len(post_issues) > 3:
        lines.append(f"  - 另有 {len(post_issues) - 3} 项请在审计报告中查看。")
    return lines


def book_dialog_cross_warning_lines(cross_warnings: Any) -> list[str]:
    if not isinstance(cross_warnings, list) or not cross_warnings:
        return []
    entries: list[str] = []
    for warning in cross_warnings[:4]:
        if not isinstance(warning, dict):
            continue
        chapter = book_dialog_int(warning.get("chapter_number"), 0)
        shared_entities = warning.get("shared_entities") or []
        if isinstance(shared_entities, str):
            shared_entities = [shared_entities]
        elif not isinstance(shared_entities, (list, tuple, set)):
            shared_entities = []
        entities = [str(item).strip() for item in shared_entities if str(item).strip()][:4]
        prop_count = book_dialog_int(warning.get("propagation_issue_count"), 0)
        note = str(warning.get("note", "") or "").strip()
        detail = f"第 {chapter} 章" if chapter else "未知章节"
        if entities:
            detail += f"（{', '.join(entities)}）"
        if prop_count > 0:
            detail += f"，传播疑点 {prop_count} 项"
        if note:
            detail += f"，{note[:40]}"
        entries.append(detail)
    if not entries:
        return []
    suffix = f"；另有 {len(cross_warnings) - 4} 条" if len(cross_warnings) > 4 else ""
    return [f"跨章关联提醒：{'；'.join(entries)}{suffix}。"]


def book_dialog_admission_lines(auto_repair: Any) -> list[str]:
    if not isinstance(auto_repair, dict):
        return []
    summary = auto_repair.get("admission_summary")
    if not isinstance(summary, dict):
        return []
    eligible = book_dialog_int(summary.get("eligible_issue_count"), 0)
    ineligible = book_dialog_int(summary.get("ineligible_issue_count"), 0)
    below = book_dialog_int(summary.get("below_threshold_count"), 0)
    if eligible <= 0 and ineligible <= 0 and below <= 0:
        return []
    lines = [f"修复准入：{eligible} 条进入自动修复，{ineligible} 条降级为人工/计划处理。"]
    eligible_chapters = book_dialog_chapter_label(summary.get("eligible_chapters") or [], limit=12)
    if eligible_chapters:
        lines.append(f"自动修复候选章节：{eligible_chapters}")
    ineligible_chapters = book_dialog_chapter_label(
        summary.get("ineligible_chapters") or [],
        limit=12,
    )
    if ineligible_chapters:
        lines.append(f"降级章节：{ineligible_chapters}")
    by_status = summary.get("by_status")
    if isinstance(by_status, dict) and by_status:
        status_labels = {
            "ready": "定位充分",
            "verify_first": "需先复核定位",
            "manual_review": "人工复核",
            "blocked": "已阻断",
        }
        status_parts = []
        for status, count in sorted(by_status.items()):
            value = book_dialog_int(count, 0)
            if value > 0:
                status_parts.append(f"{status_labels.get(str(status), str(status))} {value}")
        if status_parts:
            lines.append(f"准入状态：{'，'.join(status_parts)}。")
    by_reason = summary.get("by_reason")
    if isinstance(by_reason, dict) and by_reason:
        reason_parts = [
            f"{key} {value}"
            for key, value in list(by_reason.items())[:4]
            if book_dialog_int(value, 0) > 0
        ]
        if reason_parts:
            lines.append(f"降级/复核原因：{'，'.join(reason_parts)}。")
    if below > 0:
        lines.append(f"严重度低于本次阈值：{below} 条未进入自动修复队列。")
    return lines


def book_dialog_auto_repair_extra_lines(auto_repair: Any) -> list[str]:
    if not isinstance(auto_repair, dict):
        return []
    lines: list[str] = []

    propagation_summary = auto_repair.get("propagation_summary")
    if isinstance(propagation_summary, dict):
        total = book_dialog_int(propagation_summary.get("total_issues"), 0)
        if total > 0:
            by_severity = propagation_summary.get("by_severity")
            severity_parts: list[str] = []
            if isinstance(by_severity, dict):
                for key, label in (("critical", "严重"), ("warning", "警告"), ("info", "提示")):
                    count = book_dialog_int(by_severity.get(key), 0)
                    if count > 0:
                        severity_parts.append(f"{label} {count}")
            suffix = f"（{'，'.join(severity_parts)}）" if severity_parts else ""
            by_type = propagation_summary.get("by_type")
            type_suffix = ""
            if isinstance(by_type, dict) and by_type:
                top_types = [
                    f"{key} {value}"
                    for key, value in list(sorted(by_type.items()))[:3]
                    if book_dialog_int(value, 0) > 0
                ]
                if top_types:
                    type_suffix = f"，类型：{'，'.join(top_types)}"
            lines.append(
                f"传播候选：命中 {total} 项本地信号，需 LLM/人工复核{suffix}{type_suffix}。"
            )

    regression_summary = auto_repair.get("regression_summary")
    if isinstance(regression_summary, dict):
        total = book_dialog_int(regression_summary.get("total_regressions"), 0)
        if total > 0:
            chapters = book_dialog_chapter_label(
                regression_summary.get("chapters_with_regressions") or [],
                limit=6,
            )
            by_severity = regression_summary.get("by_severity")
            severity_suffix = ""
            if isinstance(by_severity, dict):
                severity_parts = []
                for key, label in (("critical", "严重"), ("warning", "警告"), ("info", "提示")):
                    count = book_dialog_int(by_severity.get(key), 0)
                    if count > 0:
                        severity_parts.append(f"{label} {count}")
                if severity_parts:
                    severity_suffix = f"（{'，'.join(severity_parts)}）"
            by_type = regression_summary.get("by_type")
            type_suffix = ""
            if isinstance(by_type, dict) and by_type:
                top_types = [
                    f"{key} {value}"
                    for key, value in list(sorted(by_type.items()))[:3]
                    if book_dialog_int(value, 0) > 0
                ]
                if top_types:
                    type_suffix = f"，类型：{'，'.join(top_types)}"
            lines.append(
                "规则回归候选："
                + (f"{chapters}，" if chapters else "")
                + f"共 {total} 项本地信号，需 LLM/人工复核{severity_suffix}{type_suffix}。"
            )

    lines.extend(book_dialog_cross_warning_lines(auto_repair.get("cross_chapter_warnings")))
    return lines


def build_book_consistency_dialog_payload(result: dict[str, Any]) -> tuple[str, str, str]:
    """Build the whole-book audit completion dialog text from a result payload."""
    issue_count = result.get("issue_count", 0)
    score = result.get("consistency_score")
    summary = result.get("summary", "")
    analysis_mode = result.get("analysis_mode", "summary")
    chapters_audited = result.get("chapters_audited", [])
    auto_repair = result.get("auto_repair")
    repair_queue_summary = result.get("repair_queue_summary")

    mode_label = "全文深审" if analysis_mode == "full_text" else "摘要审计"
    critical, warning, info = book_dialog_severity_counts(result)

    stat_parts: list[str] = []
    if isinstance(score, (int, float)):
        stat_parts.append(f"一致性评分 {float(score):.1f} / 10")
    stat_parts.append(f"发现问题 {issue_count} 条")
    if critical or warning or info:
        stat_parts.append(f"严重 {critical} / 警告 {warning} / 提示 {info}")

    verify_info = auto_repair.get("verify") if isinstance(auto_repair, dict) else None
    if isinstance(verify_info, dict) and book_dialog_int(verify_info.get("rejected"), 0) > 0:
        stat_parts.append(f"验证过滤 {verify_info['rejected']} 条幻觉")

    if isinstance(auto_repair, dict):
        targeted = book_dialog_int(auto_repair.get("targeted_chapters"), 0)
        applied = book_dialog_int(auto_repair.get("applied_chapters"), 0)
        blocked = book_dialog_int(auto_repair.get("blocked_chapters"), 0)
        if targeted > 0:
            stat_parts.append(f"修复 {applied}/{targeted} 章")
            if blocked > 0:
                stat_parts.append(f"阻止 {blocked} 章")
    if isinstance(repair_queue_summary, dict):
        ready = book_dialog_int(
            repair_queue_summary.get("ready", repair_queue_summary.get("ready_count")),
            0,
        )
        verify_first = book_dialog_int(
            repair_queue_summary.get(
                "verify_first",
                repair_queue_summary.get("verify_first_count"),
            ),
            0,
        )
        stat_parts.append(f"修复队列 ready {ready} / 待验证 {verify_first}")

    main_text = "　·　".join(stat_parts)
    info_lines: list[str] = [
        f"审计模式：{mode_label}，覆盖 {len(chapters_audited)} 章",
        *book_dialog_acceptance_lines(result, auto_repair),
        *book_dialog_quality_lines(result),
    ]
    coverage = result.get("coverage_metrics")
    if isinstance(coverage, dict) and coverage.get("db_path"):
        info_lines.append(f"审计库：{coverage.get('db_path')}")
    return main_text, "\n".join(info_lines), str(summary or "")


__all__ = (
    "book_dialog_acceptance_lines",
    "book_dialog_admission_lines",
    "book_dialog_auto_repair_extra_lines",
    "book_dialog_chapter_label",
    "book_dialog_cross_warning_lines",
    "book_dialog_float",
    "book_dialog_int",
    "book_dialog_issue_count",
    "book_dialog_post_audit_issue_lines",
    "book_dialog_quality_lines",
    "book_dialog_severity_counts",
    "book_dialog_stat_cards",
    "book_dialog_unique_entries",
    "build_book_consistency_dialog_payload",
)
