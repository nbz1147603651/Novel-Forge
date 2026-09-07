"""Integration test: ComplianceGuard whitelist for BOOK_CONSISTENCY_VERIFY.

Verifies end-to-end that:
1. BOOK_CONSISTENCY_VERIFY task type bypasses ComplianceGuard (no ComplianceViolationError)
2. Non-whitelisted task types (e.g. DRAFT_CHAPTER) still trigger ComplianceViolationError
3. Checkpoint/resume functionality works correctly in the verify step
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from novel_forge.core.constants import TaskType
from novel_forge.core.exceptions import ComplianceViolationError
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.prompts.compliance import ComplianceGuard
from tests.helpers.book_audit_payloads import canonical_verified_issue
from tests.helpers.model_adapters import HandlerModelAdapter


class TestBookConsistencyComplianceWhitelist:
    """End-to-end tests for ComplianceGuard whitelist in book consistency verify."""

    # Text that triggers compliance check (contains forbidden pattern)
    FORBIDDEN_PROMPT_FRAGMENT = "请复制原文到新章节"

    def test_compliance_guard_allows_whitelisted_task_type(self) -> None:
        """BOOK_CONSISTENCY_VERIFY should NOT trigger ComplianceViolationError.

        The ComplianceGuard.DEFAULT_SKIP_TASK_TYPES includes 'book_consistency_verify',
        so prompts with this task_type should pass even if they contain forbidden patterns.
        """
        guard = ComplianceGuard()

        # Should NOT raise — whitelisted task type
        guard.check_prompt(
            self.FORBIDDEN_PROMPT_FRAGMENT,
            task_type=TaskType.BOOK_CONSISTENCY_VERIFY.value,
        )

    def test_compliance_guard_allows_book_consistency_task_type(self) -> None:
        """BOOK_CONSISTENCY should also NOT trigger ComplianceViolationError.

        Both book_consistency and book_consistency_verify are in the default whitelist.
        """
        guard = ComplianceGuard()

        # Should NOT raise — whitelisted task type
        guard.check_prompt(
            self.FORBIDDEN_PROMPT_FRAGMENT,
            task_type=TaskType.BOOK_CONSISTENCY.value,
        )

    def test_compliance_guard_blocks_non_whitelisted_task_type(self) -> None:
        """DRAFT_CHAPTER should trigger ComplianceViolationError for forbidden patterns.

        Non-whitelisted task types must still go through compliance checks.
        """
        guard = ComplianceGuard()

        with pytest.raises(ComplianceViolationError):
            guard.check_prompt(
                self.FORBIDDEN_PROMPT_FRAGMENT,
                task_type=TaskType.DRAFT_CHAPTER.value,
            )

    def test_compliance_guard_default_skip_task_types(self) -> None:
        """Verify the default whitelist contains expected task types."""
        guard = ComplianceGuard()

        assert "book_consistency" in guard._skip_task_types
        assert "book_consistency_verify" in guard._skip_task_types

    def test_compliance_guard_custom_skip_task_types(self) -> None:
        """Custom skip_task_types should override the default whitelist."""
        guard = ComplianceGuard(skip_task_types={"custom_task"})

        # Custom task should be allowed
        guard.check_prompt(
            self.FORBIDDEN_PROMPT_FRAGMENT,
            task_type="custom_task",
        )

        # Default whitelisted tasks should now be blocked
        with pytest.raises(ComplianceViolationError):
            guard.check_prompt(
                self.FORBIDDEN_PROMPT_FRAGMENT,
                task_type=TaskType.BOOK_CONSISTENCY_VERIFY.value,
            )

    def test_compliance_guard_disabled_ignores_all(self) -> None:
        """Disabled guard should never raise, regardless of task_type."""
        guard = ComplianceGuard(enabled=False)

        # Should not raise even with non-whitelisted task type
        guard.check_prompt(
            self.FORBIDDEN_PROMPT_FRAGMENT,
            task_type=TaskType.DRAFT_CHAPTER.value,
        )


class TestBookConsistencyVerifyCheckpoint:
    """Test checkpoint/resume functionality in book_consistency_verify."""

    def _make_sample_issues(self) -> list[dict[str, Any]]:
        """Create sample audit issues for testing."""
        return [
            {
                "issue_id": "ch1_timeline_01",
                "category": "timeline",
                "severity": "warning",
                "chapters_involved": [1],
                "primary_chapter": 1,
                "description": "时间线不一致：角色A在第一章出现在两个地点",
                "paragraph_index": 5,
                "confidence": 0.7,
                "verification_status": "suspected",
            },
            {
                "issue_id": "ch2_character_01",
                "category": "character",
                "severity": "error",
                "chapters_involved": [2],
                "primary_chapter": 2,
                "description": "角色状态错误：角色B已死亡但仍在对话",
                "paragraph_index": 12,
                "confidence": 0.8,
                "verification_status": "suspected",
            },
        ]

    def _make_sample_chapter_texts(self) -> list[dict[str, Any]]:
        """Create sample chapter texts for testing."""
        return [
            {
                "chapter_number": 1,
                "text": "第一章：迷雾中的小镇\n\n林远站在十字路口，困惑地看着四周。",
                "numbered_text": "第一章：迷雾中的小镇\n\n林远站在十字路口，困惑地看着四周。",
                "paragraph_count": 3,
                "paragraphs": [
                    "第一章：迷雾中的小镇",
                    "林远站在十字路口，困惑地看着四周。",
                    "空气中弥漫着潮湿的雾气。",
                ],
            },
            {
                "chapter_number": 2,
                "text": "第二章：守夜人的秘密\n\n老守夜人敲响了钟楼的钟声。",
                "numbered_text": "第二章：守夜人的秘密\n\n老守夜人敲响了钟楼的钟声。",
                "paragraph_count": 3,
                "paragraphs": [
                    "第二章：守夜人的秘密",
                    "老守夜人敲响了钟楼的钟声。",
                    "钟声在夜空中回荡。",
                ],
            },
        ]

    def _make_sample_chapter_summaries(self) -> list[dict[str, Any]]:
        """Create sample chapter summaries for testing."""
        return [
            {"chapter_number": 1, "summary": "林远来到雾霭小镇", "key_events": ["到达小镇"]},
            {"chapter_number": 2, "summary": "老守夜人揭示秘密", "key_events": ["钟声响起"]},
        ]

    def test_checkpoint_save_and_load(self, tmp_path: Path) -> None:
        """Verify checkpoint can be saved and loaded correctly."""
        from novel_forge.workspace.book_ops.execution_book_verify import (
            _compute_verify_checksum,
            _load_verify_checkpoint,
            _save_verify_checkpoint,
            _verify_checkpoint_signature,
        )

        issues = self._make_sample_issues()
        signature = _verify_checkpoint_signature(issues)
        checkpoint_file = tmp_path / "verify_checkpoint.json"

        completed = [1]
        verified_by_chapter = {
            1: [
                {
                    "issue_id": "ch1_timeline_01",
                    "status": "verified",
                    "paragraph_index": 5,
                    "confidence": 0.85,
                }
            ]
        }

        payload = {
            "schema_version": 1,
            "signature": signature,
            "completed_chapters": completed,
            "verified_by_chapter": verified_by_chapter,
            "total_chapters": 2,
            "status": "running",
            "checksum": _compute_verify_checksum(completed, verified_by_chapter),
        }
        _save_verify_checkpoint(checkpoint_file, payload)

        loaded = _load_verify_checkpoint(checkpoint_file, expected_signature=signature)

        assert loaded is not None
        assert loaded["completed_chapters"] == [1]
        assert "1" in loaded["verified_by_chapter"]
        assert loaded["status"] == "running"

    def test_checkpoint_signature_mismatch_returns_none(self, tmp_path: Path) -> None:
        """Checkpoint with wrong signature should return None (start fresh)."""
        from novel_forge.workspace.book_ops.execution_book_verify import (
            _load_verify_checkpoint,
            _save_verify_checkpoint,
        )

        checkpoint_file = tmp_path / "verify_checkpoint.json"
        payload = {
            "schema_version": 1,
            "signature": "wrong-signature",
            "completed_chapters": [1],
            "verified_by_chapter": {},
            "total_chapters": 2,
            "status": "running",
        }
        _save_verify_checkpoint(checkpoint_file, payload)

        # Load with different signature
        loaded = _load_verify_checkpoint(
            checkpoint_file, expected_signature="expected-signature"
        )

        assert loaded is None

    def test_checkpoint_corrupt_json_returns_none(self, tmp_path: Path) -> None:
        """Corrupt checkpoint file should return None (start fresh)."""
        from novel_forge.workspace.book_ops.execution_book_verify import _load_verify_checkpoint

        checkpoint_file = tmp_path / "verify_checkpoint.json"
        checkpoint_file.write_text("not valid json {{{", encoding="utf-8")

        loaded = _load_verify_checkpoint(checkpoint_file)

        assert loaded is None

    def test_checkpoint_missing_file_returns_none(self, tmp_path: Path) -> None:
        """Non-existent checkpoint file should return None (start fresh)."""
        from novel_forge.workspace.book_ops.execution_book_verify import _load_verify_checkpoint

        checkpoint_file = tmp_path / "nonexistent_checkpoint.json"
        loaded = _load_verify_checkpoint(checkpoint_file)

        assert loaded is None

    async def test_verify_step_uses_correct_task_type(self, router, builder, runtime_settings) -> None:
        """Verify that the _VerifyStep in execution_book_verify uses BOOK_CONSISTENCY_VERIFY.

        This test confirms the task type flows correctly through the pipeline step,
        ensuring the ComplianceGuard whitelist is applied.
        """
        from novel_forge.workspace.book_ops.execution_book_verify import (
            _run_book_consistency_verify,
        )
        from novel_forge.workspace.runtime import RuntimeServices

        recorded_task_types: list[TaskType] = []

        async def _record_task_type(request: ModelRequest) -> ModelResponse:
            recorded_task_types.append(request.task_type)
            return ModelResponse(
                content=json.dumps(
                    {
                        "verified_issues": [
                            canonical_verified_issue(
                                "ch1_timeline_01",
                                paragraph_index=5,
                                confidence=0.85,
                                evidence="林远站在十字路口",
                            )
                        ]
                    },
                    ensure_ascii=False,
                ),
                model_id="mock-model",
            )

        recording_adapter = HandlerModelAdapter(_record_task_type)
        test_router = type(router)(
            adapters={"mock": recording_adapter},
            default_provider="mock",
        )

        runtime = RuntimeServices(
            router=test_router,
            builder=builder,
            settings=runtime_settings,
            storage=None,
        )

        await _run_book_consistency_verify(
            runtime=runtime,
            report_issues=self._make_sample_issues(),
            chapter_texts=self._make_sample_chapter_texts(),
            chapter_summaries=self._make_sample_chapter_summaries(),
            canon_state_snapshot={"characters": {}},
            max_tokens=1024,
            temperature=0.2,
            concurrency=1,
        )

        # Verify the correct task type was used
        assert len(recorded_task_types) > 0
        assert TaskType.BOOK_CONSISTENCY_VERIFY in recorded_task_types
