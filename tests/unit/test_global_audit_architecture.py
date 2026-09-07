"""Tests for the global whole-book audit architecture."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.common.global_audit_dimensions import (
    GLOBAL_AUDIT_DIMENSION_SPECS,
    GlobalAuditDimensionSpec,
    _validate_global_audit_dimension_specs,
)
from novel_forge.core.review.review_contracts import source_text_hash
from novel_forge.persistence.models import ProjectLayout
from novel_forge.story_kernel.contracts import BOOK_CONSISTENCY_CONTRACT
from novel_forge.workspace.book_ops.execution_book_entry import execute_global_repair_queue
from novel_forge.workspace.contracts import GlobalRepairQueueRequest
from novel_forge.workspace.global_audit import (
    GlobalAuditPlanner,
    GlobalAuditStore,
    GlobalRepairQueueExecutor,
    SemanticEvidenceLocator,
    build_chapter_paragraph_index,
)


def _make_layout(tmp_path: Path) -> ProjectLayout:
    layout = ProjectLayout(tmp_path)
    layout.chapters_dir.mkdir(parents=True)
    layout.states_dir.mkdir(parents=True)
    for chapter in range(1, 7):
        layout.chapter_path(chapter).write_text(
            f"# 第{chapter}章\n\n伏笔证据 {chapter}\n\n角色状态 {chapter}",
            encoding="utf-8",
        )
    return layout


class _ManifestCapableStorage:
    def __init__(self, root: Path) -> None:
        self._root = root

    def existing_project_dir(self, project_id: str) -> Path:
        return self._root

    def ensure_project_dir(self, project_id: str) -> Path:
        return self._root

    def exists(self, path: Path) -> bool:
        return Path(path).exists()

    def load_json(self, path: Path) -> dict:
        file_path = Path(path)
        if not file_path.exists():
            return {}
        return json.loads(file_path.read_text(encoding="utf-8"))

    def save_json(self, path: Path, payload: dict) -> None:
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def save_text(self, path: Path, text: str) -> None:
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(text, encoding="utf-8")


def test_global_audit_planner_builds_volume_and_non_contiguous_slices() -> None:
    planner = GlobalAuditPlanner()
    slices = planner.plan(
        completed_chapters=[1, 2, 3, 4, 5, 6],
        outline={
            "volumes": [
                {"title": "卷一", "chapter_range": [1, 3]},
                {"title": "卷二", "chapter_range": [4, 6]},
            ]
        },
        blueprint={"phases": [{"name": "起势"}, {"name": "反转"}]},
        kernel_context={
            "promise_ledger": [{"planted_chapter": 1, "payoff_chapter": 5}],
            "plot_threads": [{"chapters": [2, 4, 6]}],
            "motif_protocols": [{"occurrences": [{"chapter_number": 1}, {"chapter_number": 6}]}],
        },
    )

    kinds = {item.slice_kind for item in slices}
    assert "whole_book" in kinds
    assert "volume" in kinds
    assert "blueprint_phase" in kinds
    assert "promise_thread" in kinds
    assert "plot_thread" in kinds
    assert "motif" in kinds
    assert any(item.chapters == [2, 4, 6] for item in slices if item.slice_kind == "plot_thread")


def test_global_audit_store_indexes_paragraphs_and_hash_changes(tmp_path: Path) -> None:
    layout = _make_layout(tmp_path)
    rows, paragraphs, hashes = build_chapter_paragraph_index(layout, [1, 2])
    store = GlobalAuditStore(layout.global_audit_db_path)
    store.start_run(
        run_id="run_1",
        project_id="project",
        chapter_numbers=[1, 2],
        analysis_mode="full_text",
    )
    store.replace_chapter_paragraph_index("run_1", rows)

    with sqlite3.connect(layout.global_audit_db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM chapter_paragraph_index").fetchone()[0]

    assert count == len(rows)
    assert paragraphs[1]
    old_hash = hashes[1]
    layout.chapter_path(1).write_text("# 第1章\n\n伏笔证据已改写", encoding="utf-8")
    _, _, new_hashes = build_chapter_paragraph_index(layout, [1])
    assert new_hashes[1] != old_hash


def test_semantic_locator_is_candidate_recall_not_auto_gate(tmp_path: Path) -> None:
    layout = _make_layout(tmp_path)
    _, paragraphs, hashes = build_chapter_paragraph_index(layout, [1])
    locator = SemanticEvidenceLocator(
        semantic_search=lambda query, limit: [
            {
                "chapter_number": 1,
                "paragraph_span": [2, 2],
                "text": "语义候选",
                "source_hash": hashes[1],
                "confidence": 0.42,
            }
        ]
    )

    candidates = locator.locate_issue(
        {"primary_chapter": 1, "description": "找伏笔", "evidence": "不存在的证据"},
        paragraph_lookup=lambda chapter: paragraphs.get(chapter, []),
        source_hash_lookup=lambda chapter: hashes.get(chapter, ""),
    )

    semantic = [item for item in candidates if item["locator_method"] == "semantic"]
    assert semantic
    assert semantic[0]["confidence"] < 0.7


def test_repair_queue_compiles_ready_ticket_from_exact_anchor(tmp_path: Path) -> None:
    layout = _make_layout(tmp_path)
    _, paragraphs, hashes = build_chapter_paragraph_index(layout, [1])
    queue = GlobalRepairQueueExecutor(
        paragraph_lookup=lambda chapter: paragraphs.get(chapter, []),
        source_hash_lookup=lambda chapter: hashes.get(chapter, ""),
        completed_chapters=[1],
    ).build_queue(
        run_id="run_1",
        issues=[
            {
                "issue_id": "issue_1",
                "primary_chapter": 1,
                "chapters_involved": [1],
                "category": "timeline",
                "severity": "warning",
                "description": "伏笔证据位置需要修复",
                "evidence": "伏笔证据 1",
                "paragraph_index": 2,
            }
        ],
        slices=[
            {
                "slice_id": "whole_book",
                "slice_kind": "whole_book",
                "chapters": [1],
                "boundary_chapters": [1],
                "focus_dimensions": ["timeline_arc"],
                "source_refs": [],
                "status": "pending",
            }
        ],
    )

    assert queue.summary["ready"] == 1
    assert queue.items[0]["ticket"]
    assert queue.items[0]["source_hash"] == hashes[1]


def test_book_consistency_contract_reads_new_global_kernel_fields() -> None:
    assert "chapter_exit_states" in BOOK_CONSISTENCY_CONTRACT.reads
    assert "plot_threads" in BOOK_CONSISTENCY_CONTRACT.reads


def test_global_audit_dimension_specs_validate_topology() -> None:
    _validate_global_audit_dimension_specs(GLOBAL_AUDIT_DIMENSION_SPECS)
    cyclic = (
        GlobalAuditDimensionSpec(
            name="a",
            task_type=TaskType.BOOK_CONSISTENCY,
            prompt_hint="a",
            required_context_keys=(),
            slice_kinds=(),
            semantic_query_templates=(),
            output_focus=(),
            dependencies=("b",),
        ),
        GlobalAuditDimensionSpec(
            name="b",
            task_type=TaskType.BOOK_CONSISTENCY,
            prompt_hint="b",
            required_context_keys=(),
            slice_kinds=(),
            semantic_query_templates=(),
            output_focus=(),
            dependencies=("a",),
        ),
    )
    with pytest.raises(ValueError, match="dependency cycle"):
        _validate_global_audit_dimension_specs(cyclic)


def _seed_ready_queue_item(
    store: GlobalAuditStore,
    *,
    run_id: str,
    chapter: int,
    queue_item_id: str,
    source_hash: str,
) -> None:
    store.replace_repair_queue_items(
        run_id,
        [
            {
                "queue_item_id": queue_item_id,
                "finding_id": queue_item_id.replace("queue", "finding"),
                "slice_id": "whole_book",
                "status": "ready",
                "target_chapter": chapter,
                "paragraph_span": [1, 1],
                "source_hash": source_hash,
                "ticket": {
                    "dimension": "character_arc",
                    "chapter_number": chapter,
                    "target_paragraph_start": 1,
                    "target_paragraph_end": 1,
                    "severity": "warning",
                    "issue_type": "global_audit",
                    "target_summary": "测试修复",
                    "repair_goal": "测试修复",
                    "source_text_hash": source_hash,
                    "metadata": {},
                },
                "attempt_count": 0,
                "last_verification_result": None,
            }
        ],
    )


async def test_global_repair_queue_concurrency_keeps_same_chapter_serial(
    tmp_path: Path,
) -> None:
    layout = _make_layout(tmp_path)
    store = GlobalAuditStore(layout.global_audit_db_path)
    run_id = "run_queue_parallel"
    store.start_run(
        run_id=run_id,
        project_id="project",
        chapter_numbers=[1, 2],
        analysis_mode="full_text",
    )
    store.finish_run(run_id=run_id, status="completed", result_summary={}, coverage_metrics={})

    text_1 = layout.chapter_path(1).read_text(encoding="utf-8")
    text_2 = layout.chapter_path(2).read_text(encoding="utf-8")
    hash_1 = source_text_hash(text_1)
    hash_2 = source_text_hash(text_2)
    store.replace_repair_queue_items(
        run_id,
        [
            {
                "queue_item_id": "queue_1a",
                "finding_id": "finding_1a",
                "slice_id": "whole_book",
                "status": "ready",
                "target_chapter": 1,
                "paragraph_span": [1, 1],
                "source_hash": hash_1,
                "ticket": {
                    "dimension": "character_arc",
                    "chapter_number": 1,
                    "target_paragraph_start": 1,
                    "target_paragraph_end": 1,
                    "source_text_hash": hash_1,
                    "metadata": {},
                },
            },
            {
                "queue_item_id": "queue_1b",
                "finding_id": "finding_1b",
                "slice_id": "whole_book",
                "status": "ready",
                "target_chapter": 1,
                "paragraph_span": [1, 1],
                "source_hash": hash_1,
                "ticket": {
                    "dimension": "character_arc",
                    "chapter_number": 1,
                    "target_paragraph_start": 1,
                    "target_paragraph_end": 1,
                    "source_text_hash": hash_1,
                    "metadata": {},
                },
            },
            {
                "queue_item_id": "queue_2",
                "finding_id": "finding_2",
                "slice_id": "whole_book",
                "status": "ready",
                "target_chapter": 2,
                "paragraph_span": [1, 1],
                "source_hash": hash_2,
                "ticket": {
                    "dimension": "character_arc",
                    "chapter_number": 2,
                    "target_paragraph_start": 1,
                    "target_paragraph_end": 1,
                    "source_text_hash": hash_2,
                    "metadata": {},
                },
            },
        ],
    )

    active_by_chapter: dict[int, int] = {}
    max_same_chapter = 0
    total_active = 0
    max_total_active = 0

    async def fake_repair(runtime, mission, *, on_step_progress=None):
        nonlocal max_same_chapter, total_active, max_total_active
        chapter = int(mission.targets[0].chapter_number or 0)
        active_by_chapter[chapter] = active_by_chapter.get(chapter, 0) + 1
        total_active += 1
        max_same_chapter = max(max_same_chapter, active_by_chapter[chapter])
        max_total_active = max(max_total_active, total_active)
        try:
            import asyncio

            await asyncio.sleep(0.02)
            return SimpleNamespace(result=SimpleNamespace(applied=True))
        finally:
            active_by_chapter[chapter] -= 1
            total_active -= 1

    runtime = SimpleNamespace(
        storage=_ManifestCapableStorage(tmp_path),
        settings=SimpleNamespace(repair_control_mode="ai_auto", long_repair_max_change_ratio=1.0),
    )
    with patch(
        "novel_forge.workspace.repair_ops.execution_repair_v2.execute_repair",
        new=fake_repair,
    ):
        result = await execute_global_repair_queue(
            runtime,
            GlobalRepairQueueRequest(
                project_id="project",
                run_id=run_id,
                concurrency=3,
                rollback_on_failure=False,
            ),
        )

    assert result.result["processed"] == 3
    assert result.result["applied"] == 3
    assert max_same_chapter == 1
    assert max_total_active > 1


async def test_global_repair_queue_rolls_back_failed_item(tmp_path: Path) -> None:
    layout = _make_layout(tmp_path)
    store = GlobalAuditStore(layout.global_audit_db_path)
    run_id = "run_queue_rollback"
    store.start_run(
        run_id=run_id,
        project_id="project",
        chapter_numbers=[1],
        analysis_mode="full_text",
    )
    store.finish_run(run_id=run_id, status="completed", result_summary={}, coverage_metrics={})
    original = layout.chapter_path(1).read_text(encoding="utf-8")
    _seed_ready_queue_item(
        store,
        run_id=run_id,
        chapter=1,
        queue_item_id="queue_fail",
        source_hash=source_text_hash(original),
    )

    async def failing_repair(runtime, mission, *, on_step_progress=None):
        layout.chapter_path(1).write_text("mutated text", encoding="utf-8")
        raise RuntimeError("repair failed")

    runtime = SimpleNamespace(
        storage=_ManifestCapableStorage(tmp_path),
        settings=SimpleNamespace(repair_control_mode="ai_auto", long_repair_max_change_ratio=1.0),
    )
    with patch(
        "novel_forge.workspace.repair_ops.execution_repair_v2.execute_repair",
        new=failing_repair,
    ):
        result = await execute_global_repair_queue(
            runtime,
            GlobalRepairQueueRequest(
                project_id="project",
                run_id=run_id,
                rollback_on_failure=True,
            ),
        )

    assert result.result["failed"] == 1
    assert result.result["results"][0]["rolled_back"] is True
    assert layout.chapter_path(1).read_text(encoding="utf-8") == original


async def test_global_repair_queue_marks_changed_prose_pending_final_chain(
    tmp_path: Path,
) -> None:
    layout = _make_layout(tmp_path)
    store = GlobalAuditStore(layout.global_audit_db_path)
    run_id = "run_queue_finalize"
    store.start_run(
        run_id=run_id,
        project_id="project",
        chapter_numbers=[1],
        analysis_mode="full_text",
    )
    store.finish_run(run_id=run_id, status="completed", result_summary={}, coverage_metrics={})
    original = layout.chapter_path(1).read_text(encoding="utf-8")
    _seed_ready_queue_item(
        store,
        run_id=run_id,
        chapter=1,
        queue_item_id="queue_finalize",
        source_hash=source_text_hash(original),
    )

    async def changed_repair(runtime, mission, *, on_step_progress=None):
        layout.chapter_path(1).write_text(original + "\n\n修复后的承接。", encoding="utf-8")
        return SimpleNamespace(result=SimpleNamespace(applied=True))

    runtime = SimpleNamespace(
        storage=_ManifestCapableStorage(tmp_path),
        settings=SimpleNamespace(repair_control_mode="ai_auto", long_repair_max_change_ratio=1.0),
    )
    with patch(
        "novel_forge.workspace.repair_ops.execution_repair_v2.execute_repair",
        new=changed_repair,
    ):
        result = await execute_global_repair_queue(
            runtime,
            GlobalRepairQueueRequest(
                project_id="project",
                run_id=run_id,
                rollback_on_failure=False,
            ),
        )

    item = result.result["results"][0]
    assert item["status"] == "needs_finalize"
    assert item["requires_humanize"] is True
    assert item["requires_final_verification"] is True
    marker = json.loads(
        (layout.states_dir / "final_revision_status" / "chapter_001.json").read_text(
            encoding="utf-8"
        )
    )
    assert marker["publication_status"] == "blocked_pending_finalize"


async def test_global_repair_queue_rejects_non_ready_statuses(tmp_path: Path) -> None:
    layout = _make_layout(tmp_path)
    store = GlobalAuditStore(layout.global_audit_db_path)
    run_id = "run_queue_statuses"
    store.start_run(
        run_id=run_id,
        project_id="project",
        chapter_numbers=[1],
        analysis_mode="full_text",
    )
    store.finish_run(run_id=run_id, status="completed", result_summary={}, coverage_metrics={})

    runtime = SimpleNamespace(storage=_ManifestCapableStorage(tmp_path))
    with pytest.raises(ValueError, match="不支持的状态"):
        await execute_global_repair_queue(
            runtime,
            GlobalRepairQueueRequest(
                project_id="project",
                run_id=run_id,
                statuses=["ready", "manual_review"],
            ),
        )
