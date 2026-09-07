"""Configured whole-book repair must remain an isolated proposal batch."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from novel_forge.app_service.repair_commands import RepairCommands
from novel_forge.core.authoring import AuthoringPolicy
from novel_forge.persistence.authoring_store import AuthoringStore, story_input_version
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.repair_case_store import RepairCaseStore, repair_content_hash
from novel_forge.workspace.book_ops.execution_book_audit_post_repair import (
    _lightweight_evidence_verify,
)
from novel_forge.workspace.book_ops.execution_book_candidate_batch import (
    prepare_book_repair_candidate_batch,
)
from novel_forge.workspace.book_ops.execution_book_entry import execute_global_repair_queue
from novel_forge.workspace.contracts import GlobalRepairQueueRequest
from novel_forge.workspace.execution_result import ExecutionResult
from novel_forge.workspace.global_audit import GlobalAuditStore


def _active_project(tmp_path: Path, *, chapters: int = 1) -> tuple[Any, Path]:
    root = tmp_path / "story"
    layout = ProjectLayout(root)
    layout.chapters_dir.mkdir(parents=True)
    layout.states_dir.mkdir(parents=True)
    for chapter in range(1, chapters + 1):
        layout.chapter_path(chapter).write_text(
            f"第{chapter}章。\n线索在这里断裂。\n收束。",
            encoding="utf-8",
        )
    store = AuthoringStore(root)
    policy = store.set_policy(
        AuthoringPolicy(mode="coauthor", end_chapter=chapters),
        expected_version=0,
    )
    store.start(expected_version=policy.version, input_version=story_input_version(root))
    runtime = SimpleNamespace(
        storage=FileSystemStorage(tmp_path),
        settings=SimpleNamespace(
            long_book_audit_repair_guard_enabled=True,
            long_book_audit_repair_guard_max_delta_ratio=0.5,
            long_book_audit_repair_guard_max_added_chars=1200,
        ),
    )
    return runtime, root


def _queue_item(root: Path, chapter: int) -> dict[str, Any]:
    text = ProjectLayout(root).chapter_path(chapter).read_text(encoding="utf-8")
    return {
        "queue_item_id": f"queue-{chapter}",
        "finding_id": f"finding-{chapter}",
        "target_chapter": chapter,
        "source_hash": repair_content_hash(text),
        "ticket": {
            "finding_ids": [f"finding-{chapter}"],
            "dimension": "plot_thread_liveness",
            "issue_type": "broken_clue",
            "severity": "warning",
            "target_summary": "线索未承接",
            "repair_goal": "承接线索",
            "target_paragraph_start": 2,
            "target_paragraph_end": 2,
            "source_text_hash": repair_content_hash(text),
            "must_preserve": ["结局"],
            "metadata": {"evidence_quote": "线索在这里断裂。"},
        },
    }


async def _passing_quality(
    runtime: Any,
    request: GlobalRepairQueueRequest,
    baseline_texts: dict[int, str],
    candidate_texts: dict[int, str],
    items_by_chapter: dict[int, list[dict[str, Any]]],
    on_step_progress: Any,
) -> dict[str, Any]:
    del runtime, request, baseline_texts, on_step_progress
    return {
        "contamination_guards": {
            str(chapter): {"ok": True, "reason_codes": []} for chapter in candidate_texts
        },
        "original_rechecks_by_chapter": {
            str(chapter): {
                "executed": True,
                "expected_count": len(items_by_chapter[chapter]),
                "residual_issues": [],
                "stats": {
                    "checked": len(items_by_chapter[chapter]),
                    "rejected": len(items_by_chapter[chapter]),
                    "confirmed": 0,
                    "suspected": 0,
                    "errors": 0,
                },
                "passed": True,
            }
            for chapter in candidate_texts
        },
        "regressions_by_chapter": {str(chapter): [] for chapter in candidate_texts},
        "propagation_by_chapter": {str(chapter): [] for chapter in candidate_texts},
        "post_repair_targeted_audit": {"status": "completed", "issues": []},
    }


@pytest.mark.asyncio
async def test_configured_book_repair_builds_cases_without_touching_live_text(
    tmp_path: Path,
) -> None:
    runtime, root = _active_project(tmp_path)
    live_path = ProjectLayout(root).chapter_path(1)
    original = live_path.read_text(encoding="utf-8")

    async def candidate_executor(
        candidate_runtime: Any,
        request: GlobalRepairQueueRequest,
        on_step_progress: Any,
    ) -> ExecutionResult[Any]:
        del on_step_progress
        path = ProjectLayout(
            candidate_runtime.storage.existing_project_dir(request.project_id)
        ).chapter_path(1)
        path.write_text(original.replace("断裂", "得到承接"), encoding="utf-8")
        return ExecutionResult(
            project_id=request.project_id,
            result={
                "results": [
                    {
                        "queue_item_id": "queue-1",
                        "target_chapter": 1,
                        "status": "needs_finalize",
                        "applied": True,
                        "text_changed": True,
                    }
                ]
            },
        )

    summary = await prepare_book_repair_candidate_batch(
        runtime=runtime,
        request=GlobalRepairQueueRequest(project_id="story"),
        run_id="audit-run",
        queue_items=[_queue_item(root, 1)],
        candidate_executor=candidate_executor,
        quality_evaluator=_passing_quality,
    )

    assert summary["status"] == "awaiting_approval"
    assert summary["formal_writes"] == 0
    assert live_path.read_text(encoding="utf-8") == original
    case = RepairCaseStore(root).load_case(summary["case_ids"][0])
    assert case is not None
    assert case.status == "awaiting_approval"
    assert case.authority == "proposal_required"
    assert case.receipt is None
    assert case.proposal_id == f"book-batch:{summary['batch_id']}"
    assert RepairCaseStore(root).get_blob(case.latest_candidate.blob_hash) == original.replace(
        "断裂", "得到承接"
    )
    detail = RepairCommands(runtime.storage).detail("story", case.case_id)
    assert detail.capabilities.publish is False
    assert detail.capabilities.request_approval is False
    assert "不允许逐章应用" in detail.capabilities.reason


@pytest.mark.asyncio
async def test_one_failed_chapter_keeps_entire_book_batch_unpublishable(tmp_path: Path) -> None:
    runtime, root = _active_project(tmp_path, chapters=2)
    originals = {
        chapter: ProjectLayout(root).chapter_path(chapter).read_text(encoding="utf-8")
        for chapter in (1, 2)
    }

    async def candidate_executor(
        candidate_runtime: Any,
        request: GlobalRepairQueueRequest,
        on_step_progress: Any,
    ) -> ExecutionResult[Any]:
        del on_step_progress
        layout = ProjectLayout(candidate_runtime.storage.existing_project_dir(request.project_id))
        for chapter in (1, 2):
            layout.chapter_path(chapter).write_text(
                originals[chapter].replace("断裂", "得到承接"),
                encoding="utf-8",
            )
        return ExecutionResult(
            project_id=request.project_id,
            result={
                "results": [
                    {
                        "queue_item_id": "queue-1",
                        "target_chapter": 1,
                        "status": "needs_finalize",
                        "applied": True,
                        "text_changed": True,
                    },
                    {
                        "queue_item_id": "queue-2",
                        "target_chapter": 2,
                        "status": "failed",
                        "applied": False,
                        "text_changed": False,
                    },
                ]
            },
        )

    summary = await prepare_book_repair_candidate_batch(
        runtime=runtime,
        request=GlobalRepairQueueRequest(project_id="story"),
        run_id="audit-run",
        queue_items=[_queue_item(root, 1), _queue_item(root, 2)],
        candidate_executor=candidate_executor,
        quality_evaluator=_passing_quality,
    )

    assert summary["status"] == "manual_required"
    cases = [RepairCaseStore(root).load_case(case_id) for case_id in summary["case_ids"]]
    assert all(case is not None for case in cases)
    assert all(case.status != "awaiting_approval" for case in cases if case is not None)
    assert all(
        ProjectLayout(root).chapter_path(chapter).read_text(encoding="utf-8") == originals[chapter]
        for chapter in (1, 2)
    )


@pytest.mark.asyncio
async def test_missing_original_issue_recheck_keeps_batch_unpublishable(tmp_path: Path) -> None:
    runtime, root = _active_project(tmp_path)
    original = ProjectLayout(root).chapter_path(1).read_text(encoding="utf-8")

    async def candidate_executor(
        candidate_runtime: Any,
        request: GlobalRepairQueueRequest,
        on_step_progress: Any,
    ) -> ExecutionResult[Any]:
        del on_step_progress
        ProjectLayout(
            candidate_runtime.storage.existing_project_dir(request.project_id)
        ).chapter_path(1).write_text(
            original.replace("断裂", "得到承接"),
            encoding="utf-8",
        )
        return ExecutionResult(
            project_id=request.project_id,
            result={
                "results": [
                    {
                        "queue_item_id": "queue-1",
                        "target_chapter": 1,
                        "status": "needs_finalize",
                        "applied": True,
                        "text_changed": True,
                    }
                ]
            },
        )

    async def quality_without_original_recheck(*args: Any, **kwargs: Any) -> dict[str, Any]:
        quality = await _passing_quality(*args, **kwargs)
        quality.pop("original_rechecks_by_chapter")
        return quality

    summary = await prepare_book_repair_candidate_batch(
        runtime=runtime,
        request=GlobalRepairQueueRequest(project_id="story"),
        run_id="audit-run",
        queue_items=[_queue_item(root, 1)],
        candidate_executor=candidate_executor,
        quality_evaluator=quality_without_original_recheck,
    )

    assert summary["status"] == "manual_required"
    gate = summary["chapter_gates"]["1"]
    assert gate["reasons"] == ["original_ticket_recheck_failed"]
    case = RepairCaseStore(root).load_case(summary["case_ids"][0])
    assert case is not None
    assert case.status == "failed"


@pytest.mark.asyncio
async def test_stale_book_queue_fails_before_candidate_execution(tmp_path: Path) -> None:
    runtime, root = _active_project(tmp_path)
    item = _queue_item(root, 1)
    item["source_hash"] = "0" * 64
    called = False

    async def candidate_executor(*args: Any, **kwargs: Any) -> ExecutionResult[Any]:
        nonlocal called
        called = True
        raise AssertionError("stale input must fail before candidate execution")

    summary = await prepare_book_repair_candidate_batch(
        runtime=runtime,
        request=GlobalRepairQueueRequest(project_id="story"),
        run_id="audit-run",
        queue_items=[item],
        candidate_executor=candidate_executor,
        quality_evaluator=_passing_quality,
    )

    assert summary["status"] == "stale"
    assert summary["formal_writes"] == 0
    assert called is False


@pytest.mark.asyncio
async def test_live_edit_during_candidate_work_marks_entire_batch_stale(tmp_path: Path) -> None:
    runtime, root = _active_project(tmp_path)
    live_path = ProjectLayout(root).chapter_path(1)
    original = live_path.read_text(encoding="utf-8")

    async def candidate_executor(
        candidate_runtime: Any,
        request: GlobalRepairQueueRequest,
        on_step_progress: Any,
    ) -> ExecutionResult[Any]:
        del on_step_progress
        candidate_path = ProjectLayout(
            candidate_runtime.storage.existing_project_dir(request.project_id)
        ).chapter_path(1)
        candidate_path.write_text(original.replace("断裂", "得到承接"), encoding="utf-8")
        live_path.write_text(original + "\n作者同期补充。", encoding="utf-8")
        return ExecutionResult(
            project_id=request.project_id,
            result={
                "results": [
                    {
                        "queue_item_id": "queue-1",
                        "target_chapter": 1,
                        "status": "needs_finalize",
                        "applied": True,
                        "text_changed": True,
                    }
                ]
            },
        )

    summary = await prepare_book_repair_candidate_batch(
        runtime=runtime,
        request=GlobalRepairQueueRequest(project_id="story"),
        run_id="audit-run",
        queue_items=[_queue_item(root, 1)],
        candidate_executor=candidate_executor,
        quality_evaluator=_passing_quality,
    )

    assert summary["status"] == "stale"
    assert summary["formal_writes"] == 0
    assert any(item["kind"] == "chapter_source_changed" for item in summary["stale_reasons"])
    case = RepairCaseStore(root).load_case(summary["case_ids"][0])
    assert case is not None
    assert case.status == "stale"
    assert live_path.read_text(encoding="utf-8") == original + "\n作者同期补充。"


@pytest.mark.asyncio
async def test_configured_global_queue_routes_to_candidate_batch(tmp_path: Path) -> None:
    runtime, root = _active_project(tmp_path)
    layout = ProjectLayout(root)
    audit_store = GlobalAuditStore(layout.global_audit_db_path)
    run_id = "configured-run"
    audit_store.start_run(
        run_id=run_id,
        project_id="story",
        chapter_numbers=[1],
        analysis_mode="full_text",
    )
    audit_store.finish_run(
        run_id=run_id,
        status="completed",
        result_summary={},
        coverage_metrics={},
    )
    item = _queue_item(root, 1)
    item.update(slice_id="whole_book", status="ready", paragraph_span=[2, 2])
    audit_store.replace_repair_queue_items(run_id, [item])
    original = layout.chapter_path(1).read_text(encoding="utf-8")
    prepared = {
        "run_id": run_id,
        "batch_id": "a" * 32,
        "mode": "candidate_batch",
        "status": "awaiting_approval",
        "case_ids": ["book-case"],
        "formal_writes": 0,
    }

    with patch(
        "novel_forge.workspace.book_ops.execution_book_candidate_batch."
        "prepare_book_repair_candidate_batch",
        new=AsyncMock(return_value=prepared),
    ) as prepare_mock:
        with patch(
            "novel_forge.workspace.repair_ops.execution_repair_v2.execute_repair",
            new=AsyncMock(side_effect=AssertionError("configured path must not write live prose")),
        ):
            result = await execute_global_repair_queue(
                runtime,
                GlobalRepairQueueRequest(project_id="story", run_id=run_id),
            )

    assert result.result == prepared
    assert prepare_mock.await_count == 1
    assert layout.chapter_path(1).read_text(encoding="utf-8") == original


def test_post_repair_evidence_check_reads_the_actual_candidate(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path)
    layout.chapters_dir.mkdir(parents=True)
    layout.chapter_draft_dir(1).mkdir(parents=True)
    layout.chapter_review_draft_path(1).write_text("旧证据仍在。", encoding="utf-8")
    layout.chapter_path(1).write_text("旧证据已经关闭。", encoding="utf-8")
    issue = {"issue_id": "issue", "primary_chapter": 1, "evidence": "旧证据仍在。"}

    _old_filtered, old_stats = _lightweight_evidence_verify([issue], [1], layout)
    new_filtered, new_stats = _lightweight_evidence_verify(
        [issue],
        [1],
        layout,
        prefer_official_text=True,
    )

    assert old_stats["confirmed"] == 1
    assert new_stats["rejected"] == 1
    assert new_filtered == []
