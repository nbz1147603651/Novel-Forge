"""Tests for audit/verify/repair decoupling in book consistency flow."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from novel_forge.workspace.book_ops.execution_book_entry import (
    execute_book_consistency,
    run_book_consistency_audit,
)
from novel_forge.workspace.contracts import BookConsistencyRequest


def _make_runtime(tmp_path: Path) -> MagicMock:
    """Build a minimal mock RuntimeServices."""
    storage = MagicMock()
    storage.root = tmp_path
    storage.existing_project_dir = MagicMock(return_value=tmp_path)
    storage.load_json = MagicMock(return_value={})
    storage.save_json = MagicMock()
    storage.exists = MagicMock(return_value=False)

    settings = MagicMock()
    settings.long_book_audit_use_issue_panel_pool = False
    settings.long_book_audit_max_tokens = 4096
    settings.temp_book_consistency = 0.2
    settings.long_book_audit_location_strictness = "balanced"
    settings.long_book_audit_prompt_hint = ""
    settings.long_book_audit_max_chapters_per_batch = 12
    settings.long_book_audit_use_memory_enhancement = False
    settings.long_book_audit_chapter_max_chars = 12000
    settings.long_book_audit_repair_concurrency = 1
    settings.long_book_audit_generate_repair_report = False
    settings.long_book_audit_default_mode = "summary"

    router = MagicMock()
    builder = MagicMock()

    runtime = MagicMock()
    runtime.storage = storage
    runtime.settings = settings
    runtime.router = router
    runtime.builder = builder
    return runtime


def _mock_kernel_store(tmp_path: Path) -> MagicMock:
    """Build a minimal mock StoryKernelStore that returns an empty canon state."""
    canon_state = MagicMock()
    canon_state.characters = []
    canon_state.timeline = []
    canon_state.foreshadowing = []
    canon_state.plot_threads = {}
    canon_state.items = []
    canon_state.relationships = []
    canon_state.world_rules = []
    canon_state.model_dump = MagicMock(
        return_value={
            "characters": [],
            "timeline": [],
            "foreshadowing": [],
            "plot_threads": {},
            "items": [],
            "relationships": [],
            "world_rules": [],
        }
    )

    store = AsyncMock()
    store.load_kernel = AsyncMock(return_value=canon_state)
    return store


_KERNEL_STORE_PATCH = "novel_forge.story_kernel.store.StoryKernelStore"


def _make_request(project_id: str = "test-project") -> BookConsistencyRequest:
    return BookConsistencyRequest(
        project_id=project_id,
        analysis_mode="summary",
        repair_mode="off",
    )


def _setup_project_dirs(tmp_path: Path) -> None:
    """Create minimal project directory structure."""
    (tmp_path / "chapters").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "canon").mkdir()
    (tmp_path / "states").mkdir()
    (tmp_path / "drafts").mkdir()
    (tmp_path / "plans").mkdir()
    (tmp_path / "exports").mkdir()
    (tmp_path / "memory").mkdir()
    (tmp_path / "logs").mkdir()

    for i in range(1, 4):
        (tmp_path / "chapters" / f"chapter_{i:03d}.md").write_text(
            f"# 第{i}章\n\n这是第{i}章的内容。",
            encoding="utf-8",
        )

    canon_path = tmp_path / "canon" / "canon_current.json"
    canon_path.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "project_id": "test-project",
                "current_chapter": 3,
                "characters": {},
                "timeline": [],
                "foreshadowing": [],
                "plot_threads": {},
            }
        ),
        encoding="utf-8",
    )


class TestAuditOnly:
    """Verify run_book_consistency_audit() runs without triggering verify or repair."""

    @pytest.fixture
    def mock_step_result(self):
        result = MagicMock()
        result.issues = []
        result.summary = "审计完成，未发现问题。"
        result.consistency_score = 9.0
        result.analysis_mode = "summary"
        result.chapters_audited = [1, 2, 3]
        result.truncated_chapters = []
        result.model_dump = MagicMock(
            return_value={
                "issues": [],
                "summary": "审计完成，未发现问题。",
                "consistency_score": 9.0,
                "analysis_mode": "summary",
                "chapters_audited": [1, 2, 3],
                "truncated_chapters": [],
            }
        )
        return result

    @pytest.mark.asyncio
    async def test_audit_only_does_not_call_verify_or_repair(
        self, tmp_path: Path, mock_step_result
    ) -> None:
        _setup_project_dirs(tmp_path)
        runtime = _make_runtime(tmp_path)
        request = _make_request()

        with patch(
            _KERNEL_STORE_PATCH,
            return_value=_mock_kernel_store(tmp_path),
        ):
            with patch(
                "novel_forge.pipeline.steps.book_consistency_step.BookConsistencyStep"
            ) as MockStepClass:
                mock_step = AsyncMock()
                mock_step.run = AsyncMock(return_value=mock_step_result)
                MockStepClass.return_value = mock_step

                with patch(
                    "novel_forge.workspace.book_ops.execution_book_entry._run_book_consistency_verify",
                    new_callable=AsyncMock,
                ) as mock_verify:
                    with patch(
                        "novel_forge.workspace.book_ops.execution_book_entry._run_book_consistency_auto_repair",
                        new_callable=AsyncMock,
                    ) as mock_repair:
                        result = await run_book_consistency_audit(
                            runtime=runtime,
                            request=request,
                            on_step_progress=None,
                        )

                        mock_verify.assert_not_called()
                        mock_repair.assert_not_called()

                        assert result is mock_step_result

    @pytest.mark.asyncio
    async def test_audit_reuses_completed_checkpoint_without_explicit_continue(
        self, tmp_path: Path, mock_step_result
    ) -> None:
        _setup_project_dirs(tmp_path)
        runtime = _make_runtime(tmp_path)
        request = _make_request()

        with patch(
            _KERNEL_STORE_PATCH,
            return_value=_mock_kernel_store(tmp_path),
        ):
            with patch(
                "novel_forge.pipeline.steps.book_consistency_step.BookConsistencyStep"
            ) as MockStepClass:
                mock_step = AsyncMock()
                mock_step.run = AsyncMock(return_value=mock_step_result)
                MockStepClass.return_value = mock_step

                first = await run_book_consistency_audit(
                    runtime=runtime,
                    request=request,
                    on_step_progress=None,
                )

                assert first is mock_step_result
                assert mock_step.run.await_count == 1

        with patch(
            _KERNEL_STORE_PATCH,
            return_value=_mock_kernel_store(tmp_path),
        ):
            with patch(
                "novel_forge.pipeline.steps.book_consistency_step.BookConsistencyStep"
            ) as MockStepClass:
                mock_step = AsyncMock()
                mock_step.run = AsyncMock(side_effect=AssertionError("should reuse checkpoint"))
                MockStepClass.return_value = mock_step

                resumed = await run_book_consistency_audit(
                    runtime=runtime,
                    request=request,
                    on_step_progress=None,
                )

                mock_step.run.assert_not_awaited()
                assert resumed.summary == "审计完成，未发现问题。"
                assert resumed.consistency_score == 9.0

    @pytest.mark.asyncio
    async def test_full_text_checkpoint_is_resumed_by_default(
        self, tmp_path: Path, mock_step_result
    ) -> None:
        _setup_project_dirs(tmp_path)
        runtime = _make_runtime(tmp_path)
        request = BookConsistencyRequest(
            project_id="test-project",
            analysis_mode="full_text",
            repair_mode="off",
            two_phase_enabled=False,
        )
        checkpoint_path = tmp_path / "states" / "book_consistency_audit_checkpoint.json"
        checkpoint_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "signature": "old-step-signature",
                    "status": "failed",
                    "chunks_total": 2,
                    "completed_chunks": [{"index": 1, "parsed_items": []}],
                    "chapter_summaries": [
                        {"chapter_number": 1, "summary": ""},
                        {"chapter_number": 2, "summary": ""},
                        {"chapter_number": 3, "summary": ""},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        async def _assert_resume(input_data: Any) -> Any:
            assert input_data.resume_audit_checkpoint is True
            assert input_data.audit_checkpoint_path == checkpoint_path
            return mock_step_result

        with patch(
            _KERNEL_STORE_PATCH,
            return_value=_mock_kernel_store(tmp_path),
        ):
            with patch(
                "novel_forge.pipeline.steps.book_consistency_step.BookConsistencyStep"
            ) as MockStepClass:
                mock_step = AsyncMock()
                mock_step.run = AsyncMock(side_effect=_assert_resume)
                MockStepClass.return_value = mock_step

                result = await run_book_consistency_audit(
                    runtime=runtime,
                    request=request,
                    on_step_progress=None,
                )

                assert result is mock_step_result
                assert mock_step.run.await_count == 1


class TestCombinedFlow:
    """Verify execute_book_consistency() is audit-only in the global architecture."""

    @pytest.fixture
    def mock_step_result(self):
        result = MagicMock()
        result.issues = []
        result.summary = "审计完成。"
        result.consistency_score = 8.5
        result.analysis_mode = "summary"
        result.chapters_audited = [1, 2, 3]
        result.truncated_chapters = []
        result.auto_repair = None
        result.model_dump = MagicMock(
            return_value={
                "issues": [],
                "summary": "审计完成。",
                "consistency_score": 8.5,
                "analysis_mode": "summary",
                "chapters_audited": [1, 2, 3],
                "truncated_chapters": [],
            }
        )
        return result

    @pytest.mark.asyncio
    async def test_execute_with_repair_mode_off(self, tmp_path: Path, mock_step_result) -> None:
        _setup_project_dirs(tmp_path)
        runtime = _make_runtime(tmp_path)
        request = _make_request()
        request.repair_mode = "off"

        with patch(
            _KERNEL_STORE_PATCH,
            return_value=_mock_kernel_store(tmp_path),
        ):
            with patch(
                "novel_forge.pipeline.steps.book_consistency_step.BookConsistencyStep"
            ) as MockStepClass:
                mock_step = AsyncMock()
                mock_step.run = AsyncMock(return_value=mock_step_result)
                MockStepClass.return_value = mock_step

                with patch(
                    "novel_forge.workspace.book_ops.execution_book_entry._run_book_consistency_verify",
                    new_callable=AsyncMock,
                ) as mock_verify:
                    with patch(
                        "novel_forge.workspace.book_ops.execution_book_entry._run_book_consistency_auto_repair",
                        new_callable=AsyncMock,
                    ) as mock_repair:
                        with patch(
                            "novel_forge.workspace.book_ops.execution_book_entry.create_book_audit_snapshot",
                            return_value=None,
                        ):
                            from novel_forge.workspace.book_ops.execution_book_entry import (
                                execute_book_consistency,
                            )

                            exec_result = await execute_book_consistency(
                                runtime=runtime,
                                request=request,
                                on_step_progress=None,
                            )

                            mock_verify.assert_not_called()
                            mock_repair.assert_not_called()

                            assert exec_result.project_id == "test-project"
                            assert exec_result.result is mock_step_result

    @pytest.mark.asyncio
    async def test_execute_with_repair_mode_targeted(
        self, tmp_path: Path, mock_step_result
    ) -> None:
        _setup_project_dirs(tmp_path)
        runtime = _make_runtime(tmp_path)
        runtime.settings.long_book_audit_default_mode = "full_text"
        request = _make_request()
        request.repair_mode = "targeted"
        request.analysis_mode = "full_text"
        mock_step_result.issues = [
            SimpleNamespace(
                issue_id="issue-1",
                severity="warning",
                description="测试问题",
                primary_chapter=1,
                model_dump=MagicMock(
                    return_value={
                        "issue_id": "issue-1",
                        "severity": "warning",
                        "description": "测试问题",
                        "primary_chapter": 1,
                    }
                ),
            )
        ]
        mock_step_result.model_dump = MagicMock(
            return_value={
                "issues": [
                    {
                        "issue_id": "issue-1",
                        "severity": "warning",
                        "description": "测试问题",
                        "primary_chapter": 1,
                    }
                ],
                "summary": "审计完成。",
                "consistency_score": 8.5,
                "analysis_mode": "summary",
                "chapters_audited": [1, 2, 3],
                "truncated_chapters": [],
            }
        )

        with patch(
            _KERNEL_STORE_PATCH,
            return_value=_mock_kernel_store(tmp_path),
        ):
            with patch(
                "novel_forge.pipeline.steps.book_consistency_step.BookConsistencyStep"
            ) as MockStepClass:
                mock_step = AsyncMock()
                mock_step.run = AsyncMock(return_value=mock_step_result)
                MockStepClass.return_value = mock_step

                with patch(
                    "novel_forge.workspace.book_ops.execution_book_entry._run_book_consistency_verify",
                    new_callable=AsyncMock,
                    return_value=(
                        [],
                        {"total_issues": 1, "verified": 0, "rejected": 1, "remaining": 0},
                    ),
                ) as mock_verify:
                    with patch(
                        "novel_forge.workspace.book_ops.execution_book_entry._run_book_consistency_auto_repair",
                        new_callable=AsyncMock,
                        return_value={"enabled": True, "details": [], "processed_chapters": 0},
                    ) as mock_repair:
                        with patch(
                            "novel_forge.workspace.book_ops.execution_book_entry.create_book_audit_snapshot",
                            return_value={"snapshot_root": str(tmp_path)},
                        ):
                            from novel_forge.workspace.book_ops.execution_book_entry import (
                                execute_book_consistency,
                            )

                            exec_result = await execute_book_consistency(
                                runtime=runtime,
                                request=request,
                                on_step_progress=None,
                            )

                            mock_verify.assert_not_called()
                            mock_repair.assert_not_called()

                            assert exec_result.project_id == "test-project"


class TestFunctionSignatures:
    """Verify the new functions have correct signatures and are importable."""

    def test_run_book_consistency_audit_is_importable(self) -> None:
        from novel_forge.workspace.book_ops.execution_book_entry import run_book_consistency_audit

        assert callable(run_book_consistency_audit)

    def test_entry_module_keeps_compatibility_aliases(self) -> None:
        from novel_forge.workspace.book_ops import execution_book_entry as entry

        assert callable(entry.execute_book_consistency)
        assert callable(entry.execute_global_repair_queue)
        assert callable(entry.run_book_consistency_audit)
        assert callable(entry._run_book_consistency_verify)
        assert callable(entry._run_book_consistency_auto_repair)
        assert callable(entry.create_book_audit_snapshot)
        assert callable(entry.restore_book_audit_snapshot)
        assert callable(entry._execute_book_consistency_legacy)

    def test_legacy_facade_remains_importable(self) -> None:
        from novel_forge.workspace.book_ops import execution_book_consistency as legacy

        assert callable(legacy.execute_book_consistency)
        assert callable(legacy.execute_reevaluate_chapter)
        assert callable(legacy._run_book_consistency_verify)

    def test_execute_book_consistency_is_importable(self) -> None:
        assert callable(execute_book_consistency)

    def test_facade_exports_audit(self) -> None:
        from novel_forge.workspace.execution import run_book_consistency_audit

        assert callable(run_book_consistency_audit)

    def test_facade_exports_execute(self) -> None:
        from novel_forge.workspace.execution import execute_book_consistency

        assert callable(execute_book_consistency)

    def test_facade_exports_verify(self) -> None:
        from novel_forge.workspace.execution import run_book_consistency_verify

        assert callable(run_book_consistency_verify)

    def test_facade_exports_repair(self) -> None:
        from novel_forge.workspace.execution import run_book_consistency_repair

        assert callable(run_book_consistency_repair)


class TestLegacyCompositeAdapter:
    """Characterize the one-cycle adapter without reviving its old internals."""

    async def test_audit_only_delegates_without_running_repair_queue(self) -> None:
        from novel_forge.workspace.book_ops.execution_book_audit_legacy import (
            _execute_book_consistency_legacy,
        )

        runtime = MagicMock()
        request = _make_request()
        audit_execution = SimpleNamespace(project_id=request.project_id, result=object())
        with (
            patch(
                "novel_forge.workspace.book_ops.execution_book_entry.execute_book_consistency",
                new=AsyncMock(return_value=audit_execution),
            ) as audit,
            patch(
                "novel_forge.workspace.book_ops.execution_book_entry.execute_global_repair_queue",
                new=AsyncMock(),
            ) as repair_queue,
        ):
            result = await _execute_book_consistency_legacy(runtime, request)

        assert result is audit_execution
        audit.assert_awaited_once_with(runtime, request, on_step_progress=None)
        repair_queue.assert_not_awaited()

    async def test_targeted_mode_maps_to_ready_repair_queue(self) -> None:
        from novel_forge.workspace.book_ops.execution_book_audit_legacy import (
            _execute_book_consistency_legacy,
        )

        runtime = MagicMock()
        request = BookConsistencyRequest(
            project_id="test-project",
            analysis_mode="summary",
            repair_mode="targeted",
            repair_max_chapters=7,
            verify_before_repair=False,
            rollback_on_failure=False,
            repair_concurrency=3,
        )
        audit_execution = SimpleNamespace(project_id=request.project_id, result=object())
        progress = MagicMock()
        with (
            patch(
                "novel_forge.workspace.book_ops.execution_book_entry.execute_book_consistency",
                new=AsyncMock(return_value=audit_execution),
            ),
            patch(
                "novel_forge.workspace.book_ops.execution_book_entry.execute_global_repair_queue",
                new=AsyncMock(return_value=SimpleNamespace(result={})),
            ) as repair_queue,
        ):
            result = await _execute_book_consistency_legacy(
                runtime,
                request,
                on_step_progress=progress,
            )

        assert result is audit_execution
        queue_request = repair_queue.await_args.args[1]
        assert queue_request.project_id == request.project_id
        assert queue_request.statuses == ["ready"]
        assert queue_request.max_items == 7
        assert queue_request.verify_before_apply is False
        assert queue_request.rollback_on_failure is False
        assert queue_request.concurrency == 3
