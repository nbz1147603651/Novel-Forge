"""Tests for memory enhancement failure warning in book audit."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
    settings.long_book_audit_use_memory_enhancement = True  # Enable memory enhancement
    settings.long_book_audit_chapter_max_chars = 12000
    settings.long_book_audit_repair_concurrency = 1
    settings.long_book_audit_generate_repair_report = False
    settings.long_book_audit_default_mode = "summary"
    settings.long_book_audit_two_phase_enabled = False

    router = MagicMock()
    builder = MagicMock()

    runtime = MagicMock()
    runtime.storage = storage
    runtime.settings = settings
    runtime.router = router
    runtime.builder = builder
    return runtime


def _make_request(project_id: str = "test-project") -> BookConsistencyRequest:
    return BookConsistencyRequest(
        project_id=project_id,
        analysis_mode="full_text",  # Must be full_text for memory enhancement
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


class TestMemoryEnhancementWarning:
    """Tests for memory enhancement failure warning behavior."""

    @pytest.fixture
    def mock_step_result(self):
        result = MagicMock()
        result.issues = []
        result.summary = "审计完成，未发现问题。"
        result.consistency_score = 9.0
        result.analysis_mode = "full_text"
        result.chapters_audited = [1, 2, 3]
        result.truncated_chapters = []
        result.model_dump = MagicMock(
            return_value={
                "issues": [],
                "summary": "审计完成，未发现问题。",
                "consistency_score": 9.0,
                "analysis_mode": "full_text",
                "chapters_audited": [1, 2, 3],
                "truncated_chapters": [],
            }
        )
        return result

    @staticmethod
    def _mock_kernel_store(tmp_path: Path) -> MagicMock:
        canon_state = MagicMock()
        canon_state.characters = []
        canon_state.timeline = []
        canon_state.foreshadowing = []
        canon_state.plot_threads = {}
        canon_state.items = []
        canon_state.relationships = []
        canon_state.world_rules = []
        canon_state.model_dump = MagicMock(return_value={
            "characters": [], "timeline": [], "foreshadowing": [],
            "plot_threads": {}, "items": [], "relationships": [], "world_rules": [],
        })
        store = AsyncMock()
        store.load_kernel = AsyncMock(return_value=canon_state)
        return store

    @pytest.mark.asyncio
    async def test_failure_warning(
        self, tmp_path: Path, mock_step_result, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify warning is logged when memory enhancement fails."""
        _setup_project_dirs(tmp_path)
        runtime = _make_runtime(tmp_path)
        request = _make_request()

        mock_memory_ctx = MagicMock()
        runtime.get_memory_context = AsyncMock(return_value=mock_memory_ctx)

        with caplog.at_level(logging.WARNING):
            with patch(
                "novel_forge.story_kernel.store.StoryKernelStore",
                return_value=self._mock_kernel_store(tmp_path),
            ):
                with patch(
                    "novel_forge.pipeline.steps.book_consistency_step.BookConsistencyStep"
                ) as MockStepClass:
                    mock_step = AsyncMock()
                    mock_step.run = AsyncMock(return_value=mock_step_result)
                    MockStepClass.return_value = mock_step

                    with patch(
                        "novel_forge.memory.audit_coordinator.AuditCoordinator"
                    ) as MockCoordinatorClass:
                        mock_coordinator_instance = MagicMock()
                        mock_coordinator_instance.prepare_audit_context = AsyncMock(
                            side_effect=RuntimeError("Simulated memory enhancement failure")
                        )
                        MockCoordinatorClass.return_value = mock_coordinator_instance

                        from novel_forge.workspace.book_ops.execution_book_entry import (
                            run_book_consistency_audit,
                        )

                        result = await run_book_consistency_audit(
                            runtime=runtime,
                            request=request,
                            on_step_progress=None,
                        )

                        # Verify audit still completed
                        assert result is mock_step_result

                        # Verify warning was logged
                        assert any(
                            "Memory enhancement failed for book audit" in record.message
                            for record in caplog.records
                        ), f"Expected warning not found in: {[r.message for r in caplog.records]}"

    @pytest.mark.asyncio
    async def test_result_contains_failed_flag(self, tmp_path: Path, mock_step_result) -> None:
        """Verify memory_enhancement_failed=True in report_payload when enhancement fails."""
        _setup_project_dirs(tmp_path)
        runtime = _make_runtime(tmp_path)
        request = _make_request()
        request.rollback_on_failure = False

        mock_memory_ctx = MagicMock()
        runtime.get_memory_context = AsyncMock(return_value=mock_memory_ctx)

        with patch(
            "novel_forge.story_kernel.store.StoryKernelStore",
            return_value=self._mock_kernel_store(tmp_path),
        ):
            with patch(
                "novel_forge.pipeline.steps.book_consistency_step.BookConsistencyStep"
            ) as MockStepClass:
                mock_step = AsyncMock()
                mock_step.run = AsyncMock(return_value=mock_step_result)
                MockStepClass.return_value = mock_step

                with patch(
                    "novel_forge.memory.audit_coordinator.AuditCoordinator"
                ) as MockCoordinatorClass:
                    mock_coordinator_instance = MagicMock()
                    mock_coordinator_instance.prepare_audit_context = AsyncMock(
                        side_effect=RuntimeError("Simulated memory enhancement failure")
                    )
                    MockCoordinatorClass.return_value = mock_coordinator_instance

                    from novel_forge.workspace.book_ops.execution_book_entry import (
                        execute_book_consistency,
                    )

                    await execute_book_consistency(
                        runtime=runtime,
                        request=request,
                        on_step_progress=None,
                    )

                    payload = json.loads(
                        (tmp_path / "reports" / "book_consistency_audit.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    assert payload["request"].get("memory_enhancement_failed") is True

    @pytest.mark.asyncio
    async def test_success_no_flag(self, tmp_path: Path, mock_step_result) -> None:
        """Verify memory_enhancement_failed=False when enhancement succeeds."""
        _setup_project_dirs(tmp_path)
        runtime = _make_runtime(tmp_path)
        request = _make_request()
        request.rollback_on_failure = False

        mock_memory_ctx = MagicMock()
        runtime.get_memory_context = AsyncMock(return_value=mock_memory_ctx)

        with patch(
            "novel_forge.story_kernel.store.StoryKernelStore",
            return_value=self._mock_kernel_store(tmp_path),
        ):
            with patch(
                "novel_forge.pipeline.steps.book_consistency_step.BookConsistencyStep"
            ) as MockStepClass:
                mock_step = AsyncMock()
                mock_step.run = AsyncMock(return_value=mock_step_result)
                MockStepClass.return_value = mock_step

                with patch(
                    "novel_forge.memory.audit_coordinator.AuditCoordinator"
                ) as MockCoordinatorClass:
                    mock_audit_context = MagicMock()
                    mock_audit_context.get_summary_for_prompt = MagicMock(
                        return_value="Mock memory summary"
                    )
                    mock_coordinator_instance = MagicMock()
                    mock_coordinator_instance.prepare_audit_context = AsyncMock(
                        return_value=mock_audit_context
                    )
                    MockCoordinatorClass.return_value = mock_coordinator_instance

                    from novel_forge.workspace.book_ops.execution_book_entry import (
                        execute_book_consistency,
                    )

                    await execute_book_consistency(
                        runtime=runtime,
                        request=request,
                        on_step_progress=None,
                    )

                    payload = json.loads(
                        (tmp_path / "reports" / "book_consistency_audit.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    assert payload["request"].get("memory_enhancement_failed") is False
