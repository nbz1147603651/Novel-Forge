"""Tests for book audit performance optimization features: two-phase audit, checkpoint, verify, repair."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from novel_forge.core.config import Settings
from novel_forge.workspace.book_ops.execution_book_entry import (
    _extract_flagged_chapters_from_result,
)
from novel_forge.workspace.book_ops.execution_book_recovery import (
    invalidate_checkpoint,
    validate_checkpoint,
)
from novel_forge.workspace.book_ops.execution_book_repair import (
    _compute_repair_checksum,
    _load_repair_checkpoint,
    _repair_checkpoint_signature,
    _save_repair_checkpoint,
)
from novel_forge.workspace.book_ops.execution_book_verify import (
    _compute_chapters_checksum,
    _compute_verify_checksum,
    _load_verify_checkpoint,
    _run_book_consistency_verify,
    _save_verify_checkpoint,
    _verify_checkpoint_signature,
)


class TestTwoPhaseAuditConfig:
    """Config defaults for two-phase book audit."""

    def test_two_phase_enabled_default_true(self):
        settings = Settings(_env_file=None)
        assert settings.long_book_audit_two_phase_enabled is True

    def test_two_phase_threshold_default_07(self):
        settings = Settings(_env_file=None)
        assert settings.long_book_audit_two_phase_threshold == 0.7

    def test_two_phase_max_target_chapters_default_24(self):
        settings = Settings(_env_file=None)
        assert settings.long_book_audit_two_phase_max_target_chapters == 24

    def test_audit_output_caps_default(self):
        settings = Settings(_env_file=None)
        assert settings.long_book_audit_max_issues_per_chunk == 12
        assert settings.long_book_audit_issue_pool_max_items == 160

    def test_max_chapters_per_batch_12(self):
        settings = Settings(_env_file=None)
        assert settings.long_book_audit_max_chapters_per_batch == 12

    def test_repair_concurrency_1(self):
        settings = Settings(_env_file=None)
        assert settings.long_book_audit_repair_concurrency == 1

    def test_chapter_max_chars_20000(self):
        settings = Settings(_env_file=None)
        assert settings.long_book_audit_chapter_max_chars == 20000


class TestCheckpointValidation:
    """Checkpoint validation logic from execution_book_recovery.py."""

    def test_validate_checkpoint_valid(self, tmp_path):
        checkpoint_data = {
            "schema_version": 1,
            "signature": "test_signature",
            "status": "running",
            "chunks_total": 5,
            "completed_chunks": [1, 2, 3],
            "checksum": _compute_test_checksum([1, 2, 3]),
        }
        checkpoint_file = tmp_path / "checkpoint.json"
        checkpoint_file.write_text(json.dumps(checkpoint_data), encoding="utf-8")

        result = validate_checkpoint(checkpoint_file)

        assert result["valid"] is True
        assert result["error"] is None
        assert result["status"] == "running"
        assert result["completed"] == 3
        assert result["total"] == 5

    def test_validate_checkpoint_missing_fields(self, tmp_path):
        checkpoint_data = {
            "schema_version": 1,
            "status": "running",
        }
        checkpoint_file = tmp_path / "checkpoint.json"
        checkpoint_file.write_text(json.dumps(checkpoint_data), encoding="utf-8")

        result = validate_checkpoint(checkpoint_file)

        assert result["valid"] is False
        assert "Missing required fields" in result["error"]
        assert "signature" in result["error"]
        assert "chunks_total" in result["error"]
        assert "completed_chunks" in result["error"]

    def test_validate_checkpoint_checksum_mismatch(self, tmp_path):
        checkpoint_data = {
            "schema_version": 1,
            "signature": "test_signature",
            "status": "running",
            "chunks_total": 5,
            "completed_chunks": [1, 2, 3],
            "checksum": "invalid_checksum_value",
        }
        checkpoint_file = tmp_path / "checkpoint.json"
        checkpoint_file.write_text(json.dumps(checkpoint_data), encoding="utf-8")

        result = validate_checkpoint(checkpoint_file)

        assert result["valid"] is False
        assert "Checksum mismatch" in result["error"]

    def test_validate_checkpoint_backward_compat(self, tmp_path):
        checkpoint_data = {
            "schema_version": 1,
            "signature": "test_signature",
            "status": "completed",
            "chunks_total": 3,
            "completed_chunks": [1, 2],
        }
        checkpoint_file = tmp_path / "checkpoint.json"
        checkpoint_file.write_text(json.dumps(checkpoint_data), encoding="utf-8")

        result = validate_checkpoint(checkpoint_file)

        assert result["valid"] is True
        assert result["error"] is None
        assert result["status"] == "completed"

    def test_validate_checkpoint_file_not_found(self, tmp_path):
        non_existent = tmp_path / "does_not_exist.json"

        result = validate_checkpoint(non_existent)

        assert result["valid"] is False
        assert "not found" in result["error"]

    def test_validate_checkpoint_invalid_json(self, tmp_path):
        checkpoint_file = tmp_path / "checkpoint.json"
        checkpoint_file.write_text("not valid json {", encoding="utf-8")

        result = validate_checkpoint(checkpoint_file)

        assert result["valid"] is False
        assert "Failed to read checkpoint" in result["error"]

    def test_invalidate_checkpoint_renames_file(self, tmp_path):
        checkpoint_file = tmp_path / "checkpoint.json"
        checkpoint_file.write_text("{}", encoding="utf-8")

        invalidate_checkpoint(checkpoint_file)

        assert not checkpoint_file.exists()
        assert checkpoint_file.with_suffix(".json.invalidated").exists()


class TestVerifyCheckpoint:
    """Verify checkpoint logic from execution_book_verify.py."""

    def test_compute_chapters_checksum_deterministic(self):
        checksum1 = _compute_chapters_checksum([1, 2, 3])
        checksum2 = _compute_chapters_checksum([1, 2, 3])
        assert checksum1 == checksum2

    def test_compute_chapters_checksum_different_inputs(self):
        checksum1 = _compute_chapters_checksum([1, 2, 3])
        checksum2 = _compute_chapters_checksum([1, 2, 4])
        assert checksum1 != checksum2

    def test_save_load_checkpoint_roundtrip(self, tmp_path):
        checkpoint_path = tmp_path / "verify_checkpoint.json"
        payload = {
            "schema_version": 1,
            "completed_chapters": [1, 2, 3],
            "verified_by_chapter": {},
            "total_chapters": 5,
            "status": "running",
            "checksum": _compute_verify_checksum([1, 2, 3], {}),
        }

        _save_verify_checkpoint(checkpoint_path, payload)
        loaded = _load_verify_checkpoint(checkpoint_path)

        assert loaded is not None
        assert loaded["schema_version"] == 1
        assert loaded["completed_chapters"] == [1, 2, 3]
        assert loaded["total_chapters"] == 5
        assert loaded["status"] == "running"

    def test_load_checkpoint_corrupt_json(self, tmp_path):
        checkpoint_path = tmp_path / "corrupt_checkpoint.json"
        checkpoint_path.write_text("not valid json", encoding="utf-8")

        result = _load_verify_checkpoint(checkpoint_path)

        assert result is None

    def test_load_checkpoint_checksum_mismatch(self, tmp_path):
        checkpoint_path = tmp_path / "bad_checksum_checkpoint.json"
        payload = {
            "schema_version": 1,
            "completed_chapters": [1, 2, 3],
            "total_chapters": 5,
            "status": "running",
            "checksum": "wrong_checksum_value",
        }
        checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")

        result = _load_verify_checkpoint(checkpoint_path)

        assert result is None

    def test_load_checkpoint_with_cached_verify_outputs(self, tmp_path):
        checkpoint_path = tmp_path / "verify_cached.json"
        verified_by_chapter = {
            1: [{"issue_id": "issue-1", "status": "rejected", "confidence": 0.95}]
        }
        payload = {
            "schema_version": 1,
            "completed_chapters": [1],
            "verified_by_chapter": verified_by_chapter,
            "total_chapters": 1,
            "status": "running",
            "checksum": _compute_verify_checksum([1], verified_by_chapter),
        }
        _save_verify_checkpoint(checkpoint_path, payload)

        loaded = _load_verify_checkpoint(checkpoint_path)

        assert loaded is not None
        assert loaded["verified_by_chapter"]["1"][0]["status"] == "rejected"

    def test_load_checkpoint_rejects_stale_verify_signature(self, tmp_path):
        checkpoint_path = tmp_path / "verify_stale.json"
        payload = {
            "schema_version": 1,
            "signature": "old_issue_set",
            "completed_chapters": [1],
            "verified_by_chapter": {},
            "total_chapters": 1,
            "status": "running",
            "checksum": _compute_verify_checksum([1], {}),
        }
        _save_verify_checkpoint(checkpoint_path, payload)

        loaded = _load_verify_checkpoint(
            checkpoint_path,
            expected_signature="new_issue_set",
        )

        assert loaded is None


class TestRepairCheckpoint:
    """Repair checkpoint logic from execution_book_repair.py."""

    def test_compute_repair_checksum_deterministic(self):
        checksum1 = _compute_repair_checksum([1, 2, 3])
        checksum2 = _compute_repair_checksum([1, 2, 3])
        assert checksum1 == checksum2

    def test_save_load_repair_checkpoint_roundtrip(self, tmp_path):
        checkpoint_path = tmp_path / "repair_checkpoint.json"
        payload = {
            "schema_version": 1,
            "completed_chapters": [1, 2],
            "applied_chapters": [1],
            "total_chapters": 5,
            "status": "running",
            "checksum": _compute_repair_checksum([1, 2]),
        }

        _save_repair_checkpoint(checkpoint_path, payload)
        loaded = _load_repair_checkpoint(checkpoint_path)

        assert loaded is not None
        assert loaded["schema_version"] == 1
        assert loaded["completed_chapters"] == [1, 2]
        assert loaded["applied_chapters"] == [1]
        assert loaded["total_chapters"] == 5

    def test_load_repair_checkpoint_corrupt(self, tmp_path):
        checkpoint_path = tmp_path / "corrupt_repair.json"
        checkpoint_path.write_text("invalid json", encoding="utf-8")

        result = _load_repair_checkpoint(checkpoint_path)

        assert result is None

    def test_load_repair_checkpoint_checksum_mismatch(self, tmp_path):
        checkpoint_path = tmp_path / "bad_repair_checksum.json"
        payload = {
            "schema_version": 1,
            "completed_chapters": [1, 2],
            "applied_chapters": [1],
            "total_chapters": 5,
            "status": "running",
            "checksum": "bad_checksum",
        }
        checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")

        result = _load_repair_checkpoint(checkpoint_path)

        assert result is None

    def test_load_repair_checkpoint_rejects_stale_signature(self, tmp_path):
        checkpoint_path = tmp_path / "stale_repair.json"
        payload = {
            "schema_version": 1,
            "signature": "old_issue_set",
            "completed_chapters": [1, 2],
            "applied_chapters": [1],
            "total_chapters": 2,
            "status": "running",
            "checksum": _compute_repair_checksum([1, 2]),
        }
        checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")

        result = _load_repair_checkpoint(
            checkpoint_path,
            expected_signature="new_issue_set",
        )

        assert result is None

    def test_repair_checkpoint_signature_changes_with_issue_set(self):
        first = _repair_checkpoint_signature(
            [(1, [{"issue_id": "a", "description": "旧问题", "paragraph_index": 1}])],
            min_severity="warning",
            max_chapters=12,
        )
        second = _repair_checkpoint_signature(
            [(1, [{"issue_id": "a", "description": "新问题", "paragraph_index": 1}])],
            min_severity="warning",
            max_chapters=12,
        )

        assert first != second


class TestVerifyConfidenceGate:
    """Verify-stage confidence handling."""

    @pytest.mark.asyncio
    async def test_high_confidence_verified_issue_is_retained_as_confirmed(self, monkeypatch):
        async def fake_call_with_retry(self, task_type, context, **kwargs):
            return {
                "verified_issues": [
                    {
                        "issue_id": "ch1_timeline_01",
                        "status": "verified",
                        "paragraph_index": 1,
                        "evidence": "甲在夜里离开。",
                        "confidence": 0.93,
                    }
                ]
            }

        from novel_forge.pipeline.steps.base import PipelineStep

        monkeypatch.setattr(PipelineStep, "_call_with_retry", fake_call_with_retry)
        runtime = SimpleNamespace(router=None, builder=None, settings=Settings(_env_file=None))

        updated, stats = await _run_book_consistency_verify(
            runtime=runtime,
            report_issues=[
                {
                    "issue_id": "ch1_timeline_01",
                    "primary_chapter": 1,
                    "category": "timeline",
                    "severity": "critical",
                    "description": "时间线矛盾",
                    "confidence": 0.6,
                }
            ],
            chapter_texts=[
                {
                    "chapter_number": 1,
                    "numbered_text": "[P1] 甲在夜里离开。",
                    "paragraph_count": 1,
                    "paragraphs": ["甲在夜里离开。"],
                }
            ],
            chapter_summaries=[{"chapter_number": 1, "summary": "夜里离开"}],
            canon_state_snapshot={},
        )

        assert len(updated) == 1
        assert updated[0]["issue_id"] == "ch1_timeline_01"
        assert updated[0]["verification_status"] == "confirmed"
        assert updated[0]["verify_confidence"] == 0.93
        assert stats["removed_count"] == 0

    @pytest.mark.asyncio
    async def test_resume_applies_cached_rejected_issue(self, tmp_path, monkeypatch):
        async def fail_if_called(self, task_type, context, **kwargs):
            raise AssertionError("completed checkpoint should skip model call")

        from novel_forge.pipeline.steps.base import PipelineStep

        monkeypatch.setattr(PipelineStep, "_call_with_retry", fail_if_called)
        checkpoint_path = tmp_path / "verify_resume.json"
        verified_by_chapter = {
            1: [{"issue_id": "issue-1", "status": "rejected", "confidence": 0.95}]
        }
        _save_verify_checkpoint(
            checkpoint_path,
            {
                "schema_version": 1,
                "signature": _verify_checkpoint_signature(
                    [
                        {
                            "issue_id": "issue-1",
                            "primary_chapter": 1,
                            "category": "timeline",
                            "severity": "warning",
                            "description": "误报",
                            "confidence": 0.7,
                        }
                    ]
                ),
                "completed_chapters": [1],
                "verified_by_chapter": verified_by_chapter,
                "total_chapters": 1,
                "status": "running",
                "checksum": _compute_verify_checksum([1], verified_by_chapter),
            },
        )

        runtime = SimpleNamespace(router=None, builder=None, settings=Settings(_env_file=None))
        updated, stats = await _run_book_consistency_verify(
            runtime=runtime,
            report_issues=[
                {
                    "issue_id": "issue-1",
                    "primary_chapter": 1,
                    "category": "timeline",
                    "severity": "warning",
                    "description": "误报",
                    "confidence": 0.7,
                }
            ],
            chapter_texts=[
                {
                    "chapter_number": 1,
                    "numbered_text": "[P1] 没有矛盾。",
                    "paragraph_count": 1,
                    "paragraphs": ["没有矛盾。"],
                }
            ],
            chapter_summaries=[{"chapter_number": 1, "summary": "平稳"}],
            canon_state_snapshot={},
            checkpoint_path=checkpoint_path,
        )

        assert updated == []
        assert stats["rejected"] == 1
        assert stats["remaining"] == 0


class TestExtractFlaggedChapters:
    """Extract flagged chapters logic from execution_book_entry.py."""

    def test_extract_from_empty_result(self):
        class EmptyResult:
            issues = []

        result = EmptyResult()
        flagged = _extract_flagged_chapters_from_result(result)

        assert flagged == set()

    def test_extract_from_dict_issues(self):
        class DictResult:
            issues = [
                {
                    "primary_chapter": 3,
                    "chapters_involved": [3, 4, 5],
                },
                {
                    "primary_chapter": 7,
                    "chapters_involved": [7],
                },
                {
                    "primary_chapter": 10,
                    "chapters_involved": [9, 10],
                },
            ]

        result = DictResult()
        flagged = _extract_flagged_chapters_from_result(result)

        assert flagged == {3, 4, 5, 7, 9, 10}


def _compute_test_checksum(chapters: list[int]) -> str:
    """Compute SHA256 checksum for test purposes (same algorithm as module functions)."""
    import hashlib

    data = json.dumps(chapters, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
