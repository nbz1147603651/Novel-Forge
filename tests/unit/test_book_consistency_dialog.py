"""Tests for the desktop whole-book audit completion dialog."""

from __future__ import annotations

from types import SimpleNamespace

import novel_forge.desktop.window as window


def test_book_consistency_dialog_surfaces_repair_decision_fields(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_show_structured_result_dialog(
        parent: object,
        title: str,
        *,
        headline: str = "",
        stats: list[tuple[str, str]] | None = None,
        sections: list[tuple[str, list[str]]] | None = None,
        button_text: str = "知道了",
    ) -> None:
        captured["title"] = title
        captured["headline"] = headline
        captured["stats"] = stats or []
        captured["sections"] = sections or []
        captured["button_text"] = button_text

    monkeypatch.setattr(window, "show_structured_result_dialog", fake_show_structured_result_dialog)

    job = SimpleNamespace(
        result={
            "issue_count": 9,
            "consistency_score": 8.2,
            "analysis_mode": "full_text",
            "chapters_audited": [1, 2, 3, 4],
            "summary": "仍需复核关键身份线。",
            "acceptance": {
                "status": "review_recommended",
                "recommendation": "建议人工复核剩余问题与二次审计命中章节，再决定是否导出。",
                "suggest_export": False,
                "critical_issues": 2,
                "warning_issues": 3,
                "info_issues": 4,
            },
            "quality_metrics": {
                "coverage_ratio": 0.92,
                "estimated_miss_rate": 0.08,
                "audit_depth": "detailed",
                "dimension_scores": {"timeline": 7.5, "naming": 9.0},
            },
            "auto_repair": {
                "targeted_chapters": 3,
                "applied_chapters": 2,
                "failed_chapters": 0,
                "blocked_chapters": 1,
                "admission_summary": {
                    "eligible_issue_count": 6,
                    "ineligible_issue_count": 2,
                    "below_threshold_count": 1,
                    "eligible_chapters": [3, 4, 5],
                    "ineligible_chapters": [8],
                    "by_status": {"ready": 6, "manual_review": 2},
                    "by_reason": {"manual_patch_rewrite_requires_human": 2},
                },
                "details": [
                    {"chapter_number": 3, "status": "applied"},
                    {"chapter_number": 8, "status": "blocked", "needs_manual_review": True},
                ],
                "verify": {
                    "total_issues": 9,
                    "confirmed": 5,
                    "suspected": 2,
                    "rejected": 2,
                    "remaining": 7,
                },
                "post_repair_summary": {
                    "issues_checked": 7,
                    "issues_closed": 4,
                    "issues_remaining": 3,
                },
                "verification_summary": {
                    "by_status": {"resolved": 4, "unresolved": 2, "regressed": 1}
                },
                "propagation_summary": {
                    "total_issues": 3,
                    "by_severity": {"critical": 1, "warning": 2},
                    "by_type": {"timeline": 2},
                },
                "regression_summary": {
                    "total_regressions": 1,
                    "by_severity": {"warning": 1},
                    "by_type": {"timeline": 1},
                    "chapters_with_regressions": [8],
                },
                "post_repair_targeted_audit": {
                    "status": "completed",
                    "target_chapters": [3, 8],
                    "issue_count": 2,
                    "issues": [
                        {
                            "severity": "warning",
                            "category": "timeline",
                            "description": "第 3 章修复后仍保留时间线断点。",
                            "primary_chapter": 3,
                            "chapters_involved": [8],
                        }
                    ],
                },
                "cross_chapter_warnings": [
                    {
                        "chapter_number": 7,
                        "shared_entities": ["沈念卿", "陆云峥"],
                        "propagation_issue_count": 2,
                        "note": "身份设定仍需后续照应",
                    }
                ],
            },
        }
    )

    window.NovelForgeDesktopWindow._notify_book_consistency_complete(object(), job)

    assert captured["title"] == "全书一致性审计完成"
    assert "一致性评分 8.2 / 10" in str(captured["headline"])
    assert "严重 2 / 警告 3 / 提示 4" in str(captured["headline"])
    assert "验证过滤 2 条幻觉" in str(captured["headline"])
    assert "阻止 1 章" in str(captured["headline"])

    stats = captured["stats"]
    assert ("一致性评分", "8.2 / 10") in stats
    assert ("问题等级", "严重 2 / 警告 3 / 提示 4") in stats

    sections = dict(captured["sections"])
    assert {"概览", "自动修复", "验证复核", "二次审计", "风险提醒"} <= set(sections)
    info = "\n".join(line for lines in sections.values() for line in lines)
    assert "审计模式：全文深审，覆盖 4 章" in info
    assert "验收建议：建议复核" in info
    assert "暂不建议直接导出" in info
    assert "审计质量：深度 detailed，覆盖率 92%，估计漏检 8%。" in info
    assert "维度得分：naming 9.0，timeline 7.5。" in info
    assert "修复准入：6 条进入自动修复，2 条降级为人工/计划处理。" in info
    assert "自动修复候选章节：第 3-5 章" in info
    assert "降级章节：第 8 章" in info
    assert "准入状态：人工复核 2，定位充分 6。" in info
    assert "降级/复核原因：manual_patch_rewrite_requires_human 2。" in info
    assert "逐章验证：共 9 条，确认 5，可疑 2，过滤幻觉 2，保留 7 条进入修复。" in info
    assert "票据验证：已解决 4 项，未解决 2 项，疑似回归 1 项。" in info
    assert "传播候选：命中 3 项本地信号，需 LLM/人工复核（严重 1，警告 2），类型：timeline 2。" in info
    assert (
        "规则回归候选：第 8 章，共 1 项本地信号，需 LLM/人工复核"
        "（警告 1），类型：timeline 1。"
    ) in info
    assert "修复后二次审计：复查 第 3 章、第 8 章，命中 2 项剩余/新增问题。" in info
    assert "二次审计详情：" in info
    assert "警告/timeline/第 3 章、第 8 章：第 3 章修复后仍保留时间线断点。" in info
    assert "跨章关联提醒：第 7 章（沈念卿, 陆云峥），传播疑点 2 项" in info
    assert "需人工复核：第 8 章" in info


def test_book_dialog_compacts_duplicate_chapter_lists() -> None:
    assert window._book_dialog_chapter_label([5, 6, 7, 8, 8, 25, 27, 28]) == (
        "第 5-8 章、第 25 章、第 27-28 章"
    )
