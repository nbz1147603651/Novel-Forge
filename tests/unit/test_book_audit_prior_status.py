"""Tests for prior whole-book audit status shown before rerun/continue."""

from __future__ import annotations

import json
from pathlib import Path

from novel_forge.desktop.pages.chapter_studio.actions import _summarize_prior_book_audit
from novel_forge.workspace.book_ops.execution_book_recovery import summarize_book_audit_checkpoint


def test_summarize_prior_book_audit_reports_repair_progress(tmp_path: Path) -> None:
    audit_path = tmp_path / "book_consistency_audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "analysis_mode": "full_text",
                "chapters_audited": [1, 2, 3, 4],
                "consistency_score": 7.25,
                "issues": [{"issue_id": "a"}, {"issue_id": "b"}],
                "auto_repair": {
                    "targeted_chapters": 3,
                    "processed_chapters": 2,
                    "applied_chapters": 1,
                    "failed_chapters": 1,
                    "excluded_chapters": [4, 5],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = _summarize_prior_book_audit(audit_path)

    assert "已审 4 章" in summary
    assert "发现 2 项问题" in summary
    assert "评分 7.2/10" in summary
    assert "修复进度 2/3 章" in summary
    assert "待续修章节：第 4、5 章" in summary


def test_summarize_prior_book_audit_handles_unreadable_json(tmp_path: Path) -> None:
    audit_path = tmp_path / "book_consistency_audit.json"
    audit_path.write_text("{broken", encoding="utf-8")

    assert "无法读取" in _summarize_prior_book_audit(audit_path)


def test_summarize_book_audit_checkpoint_reports_failed_batch(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "book_consistency_audit_checkpoint.json"
    checkpoint_path.write_text(
        json.dumps(
            {
                "status": "failed",
                "chunks_total": 5,
                "completed_chunks": [{"index": 1}, {"index": 2}],
                "failed_chunk": 3,
                "failed_chapters": [13, 14],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = summarize_book_audit_checkpoint(checkpoint_path)

    assert "已完成 2/5 批" in summary
    assert "停在第 3 批" in summary
    assert "13、14" in summary


def test_summarize_book_audit_checkpoint_reports_final_result(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "book_consistency_audit_checkpoint.json"
    checkpoint_path.write_text(
        json.dumps(
            {
                "status": "completed",
                "chunks_total": 0,
                "completed_chunks": [],
                "final_result": {"issues": [{"issue_id": "a"}, {"issue_id": "b"}]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = summarize_book_audit_checkpoint(checkpoint_path)

    assert "已完成的审计结果检查点" in summary
    assert "2 条问题" in summary


def test_summarize_book_audit_checkpoint_reports_summary_cache(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "book_consistency_audit_checkpoint.json"
    checkpoint_path.write_text(
        json.dumps(
            {
                "status": "summary_completed",
                "chunks_total": 0,
                "completed_chunks": [],
                "two_phase_summary_result": {"issues": []},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = summarize_book_audit_checkpoint(checkpoint_path)

    assert "摘要检查点" in summary
    assert "继续全文批次" in summary
