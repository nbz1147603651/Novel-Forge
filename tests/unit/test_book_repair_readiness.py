from __future__ import annotations

from pathlib import Path

from novel_forge.workspace.book_ops.execution_book_precision import (
    prepare_report_issues_for_precision_repair,
    recover_completed_audit_payload_from_checkpoint,
    summarize_auto_repair_admission,
)


def test_precision_repair_readiness_renames_duplicates_and_blocks_manual_rewrite(
    tmp_path: Path,
) -> None:
    chapters_dir = tmp_path / "chapters"
    chapters_dir.mkdir()
    (chapters_dir / "chapter_001.md").write_text(
        "第一段没有问题。\n\n第二段里保留了错误称谓。",
        encoding="utf-8",
    )
    layout = type("Layout", (), {"chapters_dir": chapters_dir})()

    prepared, readiness = prepare_report_issues_for_precision_repair(
        [
            {
                "issue_id": "dup",
                "primary_chapter": 1,
                "chapters_involved": [1],
                "severity": "warning",
                "category": "naming",
                "description": "称谓不一致。",
                "evidence": "错误称谓",
                "paragraph_index": 1,
                "fix_mode": "repair_continuity",
                "fix_action": "replace",
                "confidence": 0.9,
            },
            {
                "issue_id": "dup",
                "primary_chapter": 1,
                "chapters_involved": [1],
                "severity": "critical",
                "category": "narrative_drift",
                "description": "需要大段重写。",
                "evidence": "第二段里保留了错误称谓",
                "paragraph_index": 2,
                "fix_mode": "manual_patch",
                "fix_action": "rewrite",
                "confidence": 0.85,
            },
        ],
        completed_chapters=[1],
        layout=layout,
    )

    assert len({item["issue_id"] for item in prepared}) == 2
    assert prepared[0]["repair_anchor"]["anchor_type"] == "evidence_exact"
    assert prepared[0]["paragraph_index"] == 2
    assert prepared[0]["auto_repair_eligible"] is True
    assert prepared[1]["original_issue_id"] == "dup"
    assert prepared[1]["auto_repair_eligible"] is False
    assert prepared[1]["repair_readiness"]["status"] == "manual_review"
    assert readiness["duplicate_issue_ids"] == ["dup"]
    assert readiness["manual_review_count"] == 1


def test_recover_completed_audit_payload_from_checkpoint_restores_full_issue_list(
    tmp_path: Path,
) -> None:
    states_dir = tmp_path / "states"
    states_dir.mkdir()
    checkpoint_path = states_dir / "book_consistency_audit_checkpoint.json"
    checkpoint_path.write_text(
        """
        {
          "status": "completed",
          "chunks_total": 2,
          "chapter_summaries": [
            {"chapter_number": 1, "summary": "一"},
            {"chapter_number": 2, "summary": "二"}
          ],
          "completed_chunks": [
            {
              "index": 1,
              "parsed_items": [
                {
                  "issues": [
                    {
                      "issue_id": "a",
                      "primary_chapter": 1,
                      "chapters_involved": [1],
                      "category": "timeline",
                      "severity": "warning",
                      "description": "A"
                    }
                  ],
                  "repair_plan": [{"chapter_number": 1, "issue_ids": ["a"]}],
                  "summary": "chunk 1",
                  "consistency_score": 7.1
                }
              ]
            },
            {
              "index": 2,
              "parsed_items": [
                {
                  "issues": [
                    {
                      "issue_id": "b",
                      "primary_chapter": 2,
                      "chapters_involved": [2],
                      "category": "naming",
                      "severity": "critical",
                      "description": "B"
                    }
                  ],
                  "repair_plan": [{"chapter_number": 2, "issue_ids": ["b"]}],
                  "summary": "chunk 2",
                  "consistency_score": 6.5
                }
              ]
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    layout = type("Layout", (), {"states_dir": states_dir})()

    recovered = recover_completed_audit_payload_from_checkpoint(
        layout,
        {
            "issues": [{"issue_id": "truncated"}],
            "request": {"recovered_from_failed_run": "run-1"},
        },
    )

    assert recovered is not None
    assert [item["issue_id"] for item in recovered["issues"]] == ["a", "b"]
    assert recovered["chapters_audited"] == [1, 2]
    assert recovered["checkpoint_recovery"]["mode"] == "completed_chunks"
    assert recovered["request"]["canonical_rebuilt_from_checkpoint"] is True


def test_auto_repair_admission_summary_uses_llm_readiness_flags() -> None:
    summary = summarize_auto_repair_admission(
        [
            {
                "issue_id": "ready-1",
                "primary_chapter": 2,
                "severity": "warning",
                "auto_repair_eligible": True,
                "repair_readiness": {"status": "ready", "reasons": []},
            },
            {
                "issue_id": "manual-1",
                "primary_chapter": 4,
                "severity": "critical",
                "auto_repair_eligible": False,
                "repair_readiness": {
                    "status": "manual_review",
                    "reasons": ["manual_patch_rewrite_requires_human"],
                },
                "description": "需要结构重写。",
            },
            {
                "issue_id": "low-1",
                "primary_chapter": 5,
                "severity": "info",
                "auto_repair_eligible": True,
            },
        ],
        min_severity="warning",
    )

    assert summary["eligible_issue_count"] == 1
    assert summary["ineligible_issue_count"] == 1
    assert summary["below_threshold_count"] == 1
    assert summary["eligible_chapters"] == [2]
    assert summary["ineligible_chapters"] == [4]
    assert summary["by_status"]["ready"] == 1
    assert summary["by_status"]["manual_review"] == 1
    assert summary["by_reason"]["manual_patch_rewrite_requires_human"] == 1
    assert summary["ineligible_issues"][0]["issue_id"] == "manual-1"
