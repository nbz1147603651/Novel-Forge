"""Regression tests for whole-book consistency auto-repair."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from novel_forge.core.review.review_contracts import (
    compile_repair_ticket_from_finding,
    normalize_issue_to_finding,
)
from novel_forge.workspace.book_ops.execution_book_repair import (
    _run_book_consistency_auto_repair,
    _ticket_verifications_for_detail,
)
from novel_forge.workspace.contracts import BookConsistencyRequest


async def test_book_auto_repair_parallel_queue_keeps_group_shape(tmp_path: Path) -> None:
    """Parallel repair should enumerate grouped chapters without unpacking failures."""
    storage = MagicMock()
    storage.existing_project_dir.return_value = tmp_path
    storage.exists.return_value = False

    runtime = SimpleNamespace(
        storage=storage,
        settings=SimpleNamespace(
            long_book_audit_repair_concurrency=1,
            long_book_audit_panel_first_expansion=False,
        ),
    )
    request = BookConsistencyRequest(
        project_id="parallel-repair-probe",
        repair_mode="targeted",
        repair_concurrency=2,
        repair_max_chapters=2,
        panel_first_expansion=False,
    )
    report_issues: list[dict[str, Any]] = [
        {
            "issue_id": "ch1-continuity",
            "primary_chapter": 1,
            "chapters_involved": [1],
            "severity": "warning",
            "category": "continuity",
            "description": "第一章存在轻微连贯性问题。",
        },
        {
            "issue_id": "ch2-continuity",
            "primary_chapter": 2,
            "chapters_involved": [2],
            "severity": "warning",
            "category": "continuity",
            "description": "第二章存在轻微连贯性问题。",
        },
    ]

    with patch(
        "novel_forge.workspace.book_ops.execution_book_repair.execute_reevaluate_chapter",
        new_callable=AsyncMock,
    ):
        result = await _run_book_consistency_auto_repair(
            runtime=runtime,
            request=request,
            report_issues=report_issues,
            on_step_progress=None,
        )

    assert result["repair_concurrency"] == 2
    assert result["targeted_chapters"] == 2
    assert result["processed_chapters"] == 2
    assert [item["chapter_number"] for item in result["details"]] == [1, 2]
    assert result["admission_summary"]["eligible_issue_count"] == 2
    assert result["admission_summary"]["missing_readiness_count"] == 2


async def test_book_auto_repair_uses_ticket_fallback_without_report_match(
    tmp_path: Path,
) -> None:
    """Book-only issues should become synthetic chapter repair inputs."""
    (tmp_path / "chapters").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "chapters" / "chapter_001.md").write_text(
        "第一段。\n\n第二段里保留了错误称谓。",
        encoding="utf-8",
    )

    storage = MagicMock()
    storage.existing_project_dir.return_value = tmp_path
    storage.exists.return_value = False

    runtime = SimpleNamespace(
        storage=storage,
        settings=SimpleNamespace(
            long_book_audit_repair_concurrency=1,
            long_book_audit_panel_first_expansion=False,
            long_book_audit_repair_guard_enabled=False,
        ),
    )
    request = BookConsistencyRequest(
        project_id="ticket-fallback-probe",
        repair_mode="targeted",
        repair_max_chapters=1,
        panel_first_expansion=False,
    )
    report_issues: list[dict[str, Any]] = [
        {
            "issue_id": "name-1",
            "primary_chapter": 1,
            "chapters_involved": [1],
            "severity": "warning",
            "category": "naming",
            "description": "书级审计发现称谓前后不一致。",
            "evidence": "错误称谓",
            "paragraph_index": 2,
            "fix_mode": "repair_continuity",
        }
    ]
    captured: dict[str, Any] = {}

    async def fake_execute_repair(*args: Any, **kwargs: Any) -> SimpleNamespace:
        captured["mission"] = args[1]
        return SimpleNamespace(result=SimpleNamespace(applied=False, attempts=[]))

    with patch(
        "novel_forge.workspace.book_ops.execution_book_repair.execute_reevaluate_chapter",
        new_callable=AsyncMock,
    ):
        with patch(
            "novel_forge.workspace.book_ops.execution_book_repair.execute_repair",
            new=fake_execute_repair,
        ):
            result = await _run_book_consistency_auto_repair(
                runtime=runtime,
                request=request,
                report_issues=report_issues,
                on_step_progress=None,
            )

    repair_mission = captured["mission"]
    continuity_target = next(
        target for target in repair_mission.targets if target.domain.value == "continuity"
    )
    causal_target = next(target for target in repair_mission.targets if target.domain.value == "causal")
    detail = result["details"][0]

    assert continuity_target.payload["synthetic_issues"]
    assert continuity_target.payload["synthetic_issues"][0]["paragraph_start"] == 2
    assert causal_target.payload["synthetic_issues"] == []
    assert detail["matched_by_ticket"] == 1
    assert detail["match_mode"] == "ticket"
    assert detail["repair_tickets"]
    assert detail["verification_results"][0]["status"] == "unresolved"
    assert result["verification_summary"]["by_status"]["unresolved"] == 1


def test_ticket_verification_prefers_ticket_evidence() -> None:
    """Per-ticket verification should not collapse mixed outcomes to one chapter status."""
    resolved_finding = normalize_issue_to_finding(
        {
            "issue_id": "resolved",
            "primary_chapter": 1,
            "category": "naming",
            "description": "旧称谓已移除。",
            "evidence": "旧称谓",
            "paragraph_index": 2,
        },
        source_module="book_consistency_audit",
        chapter_number=1,
    )
    unresolved_finding = normalize_issue_to_finding(
        {
            "issue_id": "unresolved",
            "primary_chapter": 1,
            "category": "naming",
            "description": "残留称谓仍在。",
            "evidence": "残留称谓",
            "paragraph_index": 3,
        },
        source_module="book_consistency_audit",
        chapter_number=1,
    )
    detail = {
        "status": "applied",
        "applied": True,
        "match_mode": "ticket",
        "post_repair_check": {
            "issues_checked": 2,
            "issues_closed": 1,
            "issues_remaining": 1,
        },
    }

    results = _ticket_verifications_for_detail(
        tickets=[
            compile_repair_ticket_from_finding(resolved_finding),
            compile_repair_ticket_from_finding(unresolved_finding),
        ],
        detail=detail,
        current_text="第一段。\n\n新的称谓已经改好。\n\n残留称谓仍在此处。",
    )

    assert [item["status"] for item in results] == ["resolved", "unresolved"]
    assert results[1]["remaining_evidence"] == ["残留称谓"]


def test_normalize_issue_to_finding_preserves_fix_action_metadata() -> None:
    finding = normalize_issue_to_finding(
        {
            "issue_id": "action-1",
            "primary_chapter": 1,
            "category": "naming",
            "description": "称谓需要替换。",
            "evidence": "旧称谓",
            "paragraph_index": 2,
            "fix_mode": "repair_continuity",
            "fix_action": "replace",
        },
        source_module="book_consistency_audit",
        chapter_number=1,
    )

    assert finding.metadata["fix_mode"] == "repair_continuity"
    assert finding.metadata["fix_action"] == "replace"
