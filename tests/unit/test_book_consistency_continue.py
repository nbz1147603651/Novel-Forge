"""Regression tests for whole-book consistency audit resume mode."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.book_ops.execution_book_continue import (
    _book_repair_detail_was_applied,
    _continue_repair_from_audit,
    _reconstruct_book_consistency_issues,
)
from novel_forge.workspace.contracts import BookConsistencyRequest


class _MemoryStorage:
    def __init__(self, initial: dict[Path, Any]) -> None:
        self.data = {Path(path): value for path, value in initial.items()}

    def load_json(self, path: Path) -> Any:
        return self.data[Path(path)]

    def save_json(self, path: Path, data: Any) -> None:
        self.data[Path(path)] = data


def test_book_resume_recognizes_applied_detail_shapes() -> None:
    assert _book_repair_detail_was_applied({"applied": True})
    assert _book_repair_detail_was_applied({"status": "applied"})
    assert _book_repair_detail_was_applied({"continuity_applied": True})
    assert _book_repair_detail_was_applied({"causal_applied": True})
    assert not _book_repair_detail_was_applied({"status": "failed"})


def test_book_resume_reconstructs_audit_issues_from_json() -> None:
    issues = _reconstruct_book_consistency_issues(
        [
            {
                "issue_id": "timeline-2",
                "category": "timeline",
                "severity": "critical",
                "primary_chapter": "2",
                "chapters_involved": ["2", "4"],
                "description": "时间线冲突。",
                "paragraph_index": [3, 4],
                "paragraph_span": ["3", "4"],
                "confidence": "0.82",
            }
        ]
    )

    assert len(issues) == 1
    assert issues[0].issue_id == "timeline-2"
    assert issues[0].primary_chapter == 2
    assert issues[0].chapters_involved == [2, 4]
    assert issues[0].paragraph_index == 3
    assert issues[0].paragraph_span == [3, 4]
    assert issues[0].confidence == 0.82


async def test_book_resume_all_done_writes_repair_report_with_original_issue_count(
    tmp_path: Path,
) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    audit_path = reports_dir / "book_consistency_audit.json"
    repair_report_path = reports_dir / "book_consistency_repair_report.json"
    audit_payload = {
        "summary": "上一轮审计发现两个问题。",
        "consistency_score": 7.2,
        "analysis_mode": "full_text",
        "chapters_audited": [1, 2],
        "issues": [
            {
                "issue_id": "ch1-name",
                "category": "naming",
                "severity": "warning",
                "primary_chapter": 1,
                "chapters_involved": [1],
                "description": "称谓不一致。",
            },
            {
                "issue_id": "ch2-time",
                "category": "timeline",
                "severity": "critical",
                "primary_chapter": 2,
                "chapters_involved": [2],
                "description": "时间线冲突。",
            },
        ],
        "auto_repair": {
            "enabled": True,
            "repair_concurrency": 1,
            "targeted_chapters": 2,
            "processed_chapters": 2,
            "applied_chapters": 2,
            "failed_chapters": 0,
            "details": [
                {"chapter_number": 1, "status": "applied"},
                {"chapter_number": 2, "continuity_applied": True},
            ],
        },
    }
    storage = _MemoryStorage({audit_path: audit_payload})
    runtime = SimpleNamespace(storage=storage)
    layout = SimpleNamespace(reports_dir=reports_dir)
    events: list[tuple[str, dict[str, Any]]] = []

    execution = await _continue_repair_from_audit(
        runtime=runtime,
        request=BookConsistencyRequest(
            project_id="resume_probe",
            repair_mode="targeted",
            continue_from_audit=True,
        ),
        layout=layout,
        completed_chapters=[1, 2],
        on_step_progress=lambda step, payload: events.append((step, payload)),
    )

    repair_report = storage.data[repair_report_path]
    assert len(execution.result.issues) == 2
    assert repair_report["analysis"]["issue_count"] == 2
    assert repair_report["task_flow"]["processed_chapters"] == 2
    assert repair_report["task_flow"]["applied_chapters"] == 2
    assert [name for name, _ in events] == [
        "book_consistency_start",
        "book_consistency_repair_report_written",
        "book_consistency_report_written",
    ]


async def test_book_resume_repair_creates_backup_metadata(tmp_path: Path) -> None:
    for name in ("chapters", "drafts", "reports", "states", "logs"):
        (tmp_path / name).mkdir()
    layout = ProjectLayout(tmp_path)
    audit_path = layout.reports_dir / "book_consistency_audit.json"
    audit_payload = {
        "summary": "上一轮审计发现一个问题。",
        "consistency_score": 7.2,
        "analysis_mode": "summary",
        "chapters_audited": [1],
        "issues": [
            {
                "issue_id": "ch1-name",
                "category": "naming",
                "severity": "warning",
                "primary_chapter": 1,
                "chapters_involved": [1],
                "description": "称谓不一致。",
            }
        ],
    }
    audit_path.write_text("{}", encoding="utf-8")
    storage = _MemoryStorage({audit_path: audit_payload})
    runtime = SimpleNamespace(storage=storage)
    events: list[tuple[str, dict[str, Any]]] = []

    with patch(
        "novel_forge.workspace.book_ops.execution_book_continue._run_book_consistency_auto_repair",
        new_callable=AsyncMock,
    ) as mock_repair:
        mock_repair.return_value = {
            "enabled": True,
            "details": [{"chapter_number": 1, "status": "applied"}],
            "applied_chapters": 1,
        }
        execution = await _continue_repair_from_audit(
            runtime=runtime,
            request=BookConsistencyRequest(
                project_id="resume_probe",
                repair_mode="targeted",
                continue_from_audit=True,
                rollback_on_failure=False,
                generate_repair_report=False,
                use_issue_panel_pool=False,
            ),
            layout=layout,
            completed_chapters=[1],
            on_step_progress=lambda step, payload: events.append((step, payload)),
        )

    saved = storage.data[audit_path]
    assert execution.result.auto_repair["backup"]["snapshot_root"]
    assert execution.result.auto_repair["backup"]["auto_rollback_on_failure"] is False
    assert saved["backup"]["snapshot_root"] == execution.result.auto_repair["backup"][
        "snapshot_root"
    ]
    assert any(name == "book_consistency_backup_created" for name, _ in events)


async def test_book_resume_recovers_full_checkpoint_before_repair(tmp_path: Path) -> None:
    for name in ("chapters", "drafts", "reports", "states", "logs"):
        (tmp_path / name).mkdir()
    layout = ProjectLayout(tmp_path)
    (layout.chapters_dir / "chapter_001.md").write_text("第一章错误。", encoding="utf-8")
    (layout.chapters_dir / "chapter_002.md").write_text("第二章错误。", encoding="utf-8")
    (layout.states_dir / "book_consistency_audit_checkpoint.json").write_text(
        """
        {
          "status": "completed",
          "chunks_total": 1,
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
                      "description": "第一章错误。",
                      "evidence": "第一章错误",
                      "fix_mode": "repair_continuity",
                      "fix_action": "replace",
                      "confidence": 0.9
                    },
                    {
                      "issue_id": "b",
                      "primary_chapter": 2,
                      "chapters_involved": [2],
                      "category": "naming",
                      "severity": "critical",
                      "description": "第二章错误。",
                      "evidence": "第二章错误",
                      "fix_mode": "repair_continuity",
                      "fix_action": "replace",
                      "confidence": 0.9
                    }
                  ],
                  "repair_plan": []
                }
              ]
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    audit_path = layout.reports_dir / "book_consistency_audit.json"
    truncated_payload = {
        "summary": "恢复日志里的截断报告。",
        "analysis_mode": "summary",
        "chapters_audited": [1],
        "issues": [
            {
                "issue_id": "a",
                "primary_chapter": 1,
                "chapters_involved": [1],
                "category": "timeline",
                "severity": "warning",
                "description": "第一章错误。",
            }
        ],
        "request": {"recovered_from_failed_run": "run-1"},
        "recovered_from": {"run_id": "run-1"},
    }
    audit_path.write_text("{}", encoding="utf-8")
    storage = _MemoryStorage({audit_path: truncated_payload})
    runtime = SimpleNamespace(storage=storage)

    with patch(
        "novel_forge.workspace.book_ops.execution_book_continue._run_book_consistency_auto_repair",
        new_callable=AsyncMock,
    ) as mock_repair:
        mock_repair.return_value = {
            "enabled": True,
            "details": [],
            "applied_chapters": 0,
        }
        await _continue_repair_from_audit(
            runtime=runtime,
            request=BookConsistencyRequest(
                project_id="resume_probe",
                repair_mode="targeted",
                continue_from_audit=True,
                verify_before_repair=False,
                generate_repair_report=False,
                use_issue_panel_pool=False,
            ),
            layout=layout,
            completed_chapters=[1, 2],
        )

    repaired_issues = mock_repair.call_args.kwargs["report_issues"]
    saved = storage.data[audit_path]
    assert [item["issue_id"] for item in repaired_issues] == ["a", "b"]
    assert saved["checkpoint_recovery"]["raw_issue_count"] == 2
    assert saved["repair_readiness"]["status"] == "ready"
