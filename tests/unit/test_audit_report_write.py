"""Tests for unified audit report write logic."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from novel_forge.workspace.book_ops.execution_book_entry import execute_book_consistency
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
    settings.long_book_audit_two_phase_enabled = False

    router = MagicMock()
    builder = MagicMock()

    runtime = MagicMock()
    runtime.storage = storage
    runtime.settings = settings
    runtime.router = router
    runtime.builder = builder
    return runtime


def _make_request(
    project_id: str = "test-project",
    repair_mode: str = "off",
    rollback_on_failure: bool = False,
    analysis_mode: str = "summary",
) -> BookConsistencyRequest:
    return BookConsistencyRequest(
        project_id=project_id,
        analysis_mode=analysis_mode,
        repair_mode=repair_mode,
        rollback_on_failure=rollback_on_failure,
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


def _versioned_audit_files(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "reports").glob("book_consistency_audit_[0-9]*_[0-9]*.json"))


def _make_mock_result():
    result = MagicMock()
    result.issues = [{"id": "issue1", "severity": "medium", "description": "Test issue"}]
    result.summary = "审计完成，发现1个问题。"
    result.consistency_score = 7.5
    result.analysis_mode = "summary"
    result.chapters_audited = [1, 2, 3]
    result.truncated_chapters = []
    result.auto_repair = None
    result.model_dump = MagicMock(
        return_value={
            "issues": [{"id": "issue1", "severity": "medium", "description": "Test issue"}],
            "summary": "审计完成，发现1个问题。",
            "consistency_score": 7.5,
            "analysis_mode": "summary",
            "chapters_audited": [1, 2, 3],
            "truncated_chapters": [],
        }
    )
    return result


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


class TestSingleWrite:
    """Verify report is written exactly once."""

    @pytest.mark.asyncio
    async def test_single_write_non_targeted_mode(self, tmp_path: Path) -> None:
        """When repair_mode != 'targeted', report saved exactly once after audit phase."""
        _setup_project_dirs(tmp_path)
        runtime = _make_runtime(tmp_path)
        request = _make_request(repair_mode="off", rollback_on_failure=False)
        mock_result = _make_mock_result()

        with patch(_KERNEL_STORE_PATCH, return_value=_mock_kernel_store(tmp_path)):
            with patch(
                "novel_forge.pipeline.steps.book_consistency_step.BookConsistencyStep"
            ) as MockStepClass:
                mock_step = AsyncMock()
                mock_step.run = AsyncMock(return_value=mock_result)
                MockStepClass.return_value = mock_step

                with patch(
                    "novel_forge.workspace.book_ops.execution_book_entry._run_book_consistency_verify",
                    new_callable=AsyncMock,
                ) as mock_verify:
                    with patch(
                        "novel_forge.workspace.book_ops.execution_book_entry._run_book_consistency_auto_repair",
                        new_callable=AsyncMock,
                    ) as mock_repair:
                        await execute_book_consistency(
                            runtime=runtime,
                            request=request,
                            on_step_progress=None,
                        )

                        verify_call_count = mock_verify.call_count
                        repair_call_count = mock_repair.call_count
                        assert verify_call_count == 0, (
                            f"verify should not be called in non-targeted mode, got {verify_call_count}"
                        )
                        assert repair_call_count == 0, (
                            f"repair should not be called in non-targeted mode, got {repair_call_count}"
                        )
                        audit_files = _versioned_audit_files(tmp_path)
                        assert len(audit_files) == 1
                        assert (tmp_path / "reports" / "book_consistency_audit.json").exists()

    @pytest.mark.asyncio
    async def test_single_write_targeted_mode(self, tmp_path: Path) -> None:
        """Even targeted mode writes one audit report and does not execute repair."""
        _setup_project_dirs(tmp_path)
        runtime = _make_runtime(tmp_path)
        request = _make_request(
            repair_mode="targeted", rollback_on_failure=False, analysis_mode="full_text"
        )
        mock_result = _make_mock_result()

        with patch(_KERNEL_STORE_PATCH, return_value=_mock_kernel_store(tmp_path)):
            with patch(
                "novel_forge.pipeline.steps.book_consistency_step.BookConsistencyStep"
            ) as MockStepClass:
                mock_step = AsyncMock()
                mock_step.run = AsyncMock(return_value=mock_result)
                MockStepClass.return_value = mock_step

                with patch(
                    "novel_forge.workspace.book_ops.execution_book_entry._run_book_consistency_verify",
                    new_callable=AsyncMock,
                ) as mock_verify:
                    mock_verify.return_value = ([], {})
                    with patch(
                        "novel_forge.workspace.book_ops.execution_book_entry._run_book_consistency_auto_repair",
                        new_callable=AsyncMock,
                    ) as mock_repair:
                        await execute_book_consistency(
                            runtime=runtime,
                            request=request,
                            on_step_progress=None,
                        )

                        verify_call_count = mock_verify.call_count
                        repair_call_count = mock_repair.call_count

                        assert verify_call_count == 0, (
                            f"verify should not be called by audit entry, got {verify_call_count}"
                        )
                        assert repair_call_count == 0, (
                            f"repair should not be called by audit entry, got {repair_call_count}"
                        )

                        audit_files = _versioned_audit_files(tmp_path)
                        assert len(audit_files) == 1, (
                            f"Expected 1 audit file from audit-only entry, got {len(audit_files)}"
                        )


class TestRollbackDeletesReport:
    """Verify rollback is not part of the audit-only entry."""

    @pytest.mark.asyncio
    async def test_rollback_deletes_pre_repair_report(self, tmp_path: Path) -> None:
        """Targeted repair fields are ignored; no rollback path is entered."""
        _setup_project_dirs(tmp_path)
        runtime = _make_runtime(tmp_path)
        request = _make_request(
            repair_mode="targeted", rollback_on_failure=True, analysis_mode="full_text"
        )
        mock_result = _make_mock_result()

        saved_calls = []
        original_save_json = runtime.storage.save_json

        def track_save_json(path, payload):
            saved_calls.append((path, payload))
            return original_save_json(path, payload)

        runtime.storage.save_json = track_save_json

        with patch(_KERNEL_STORE_PATCH, return_value=_mock_kernel_store(tmp_path)):
            with patch(
                "novel_forge.pipeline.steps.book_consistency_step.BookConsistencyStep"
            ) as MockStepClass:
                mock_step = AsyncMock()
                mock_step.run = AsyncMock(return_value=mock_result)
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
                            return_value={"snapshot_root": str(tmp_path / "snapshot")},
                        ):
                            with patch(
                                "novel_forge.workspace.book_ops.execution_book_entry.restore_book_audit_snapshot",
                            ):
                                await execute_book_consistency(
                                    runtime=runtime,
                                    request=request,
                                    on_step_progress=None,
                                )

                                mock_verify.assert_not_called()
                                mock_repair.assert_not_called()
                                assert (
                                    tmp_path / "reports" / "book_consistency_audit.json"
                                ).exists()


class TestReportContentMatchesFinal:
    """Verify report content matches final result."""

    @pytest.mark.asyncio
    async def test_report_content_matches_non_targeted(self, tmp_path: Path) -> None:
        """Report content matches the audit result when repair_mode != 'targeted'."""
        _setup_project_dirs(tmp_path)
        runtime = _make_runtime(tmp_path)
        request = _make_request(repair_mode="off", rollback_on_failure=False)
        mock_result = _make_mock_result()

        with patch(_KERNEL_STORE_PATCH, return_value=_mock_kernel_store(tmp_path)):
            with patch(
                "novel_forge.pipeline.steps.book_consistency_step.BookConsistencyStep"
            ) as MockStepClass:
                mock_step = AsyncMock()
                mock_step.run = AsyncMock(return_value=mock_result)
                MockStepClass.return_value = mock_step

                await execute_book_consistency(
                    runtime=runtime,
                    request=request,
                    on_step_progress=None,
                )

                saved_payload = json.loads(
                    (tmp_path / "reports" / "book_consistency_audit.json").read_text(
                        encoding="utf-8"
                    )
                )

                assert saved_payload["summary"].startswith("共发现 1 个一致性问题")
                assert len(saved_payload["issues"]) == 1
                assert saved_payload["issues"][0]["id"] == "issue1"

    @pytest.mark.asyncio
    async def test_report_content_matches_targeted(self, tmp_path: Path) -> None:
        """Report content remains audit output when repair_mode == 'targeted'."""
        _setup_project_dirs(tmp_path)
        runtime = _make_runtime(tmp_path)
        request = _make_request(
            repair_mode="targeted", rollback_on_failure=False, analysis_mode="full_text"
        )
        mock_result = _make_mock_result()

        with patch(_KERNEL_STORE_PATCH, return_value=_mock_kernel_store(tmp_path)):
            with patch(
                "novel_forge.pipeline.steps.book_consistency_step.BookConsistencyStep"
            ) as MockStepClass:
                mock_step = AsyncMock()
                mock_step.run = AsyncMock(return_value=mock_result)
                MockStepClass.return_value = mock_step

                with patch(
                    "novel_forge.workspace.book_ops.execution_book_entry._run_book_consistency_verify",
                    new_callable=AsyncMock,
                ) as mock_verify:
                    mock_verify.return_value = ([], {})
                    with patch(
                        "novel_forge.workspace.book_ops.execution_book_entry._run_book_consistency_auto_repair",
                        new_callable=AsyncMock,
                    ) as mock_repair:
                        await execute_book_consistency(
                            runtime=runtime,
                            request=request,
                            on_step_progress=None,
                        )

                        audit_files = _versioned_audit_files(tmp_path)
                        assert audit_files, "No audit save found"
                        saved_payload = json.loads(audit_files[-1].read_text(encoding="utf-8"))

                        assert "issues" in saved_payload
                        assert "summary" in saved_payload
                        assert saved_payload["request"]["architecture"] == "global_audit_v1"
                        assert (
                            saved_payload["request"]["repair_queue_execution"]
                            == "separate_workflow"
                        )
                        mock_verify.assert_not_called()
                        mock_repair.assert_not_called()
