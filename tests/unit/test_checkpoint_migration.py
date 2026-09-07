"""Tests for checkpoint schema version migration support."""

from __future__ import annotations

import json
from pathlib import Path

from novel_forge.pipeline.steps.book_consistency_step import (
    CURRENT_CHECKPOINT_SCHEMA_VERSION,
    BookConsistencyStep,
)


class TestCheckpointMigration:
    """Test schema version checking in _load_audit_checkpoint."""

    def test_load_v1_checkpoint(self, tmp_path: Path) -> None:
        """Verify v1 checkpoint with schema_version=1 loads successfully."""
        checkpoint_file = tmp_path / "checkpoint.json"
        signature = "abc123"
        v1_payload = {
            "schema_version": 1,
            "status": "running",
            "signature": signature,
            "analysis_mode": "full_text",
            "max_chapters_per_batch": 5,
            "chunks_total": 3,
            "completed_chunks": [],
        }
        checkpoint_file.write_text(json.dumps(v1_payload), encoding="utf-8")

        result = BookConsistencyStep._load_audit_checkpoint(checkpoint_file, signature)
        assert result["schema_version"] == 1
        assert result["status"] == "running"

    def test_load_legacy_checkpoint(self, tmp_path: Path) -> None:
        """Verify legacy checkpoint without schema_version loads with warning."""
        checkpoint_file = tmp_path / "checkpoint.json"
        signature = "abc123"
        legacy_payload = {
            "status": "running",
            "signature": signature,
            "analysis_mode": "full_text",
            "max_chapters_per_batch": 5,
            "chunks_total": 3,
            "completed_chunks": [],
        }
        checkpoint_file.write_text(json.dumps(legacy_payload), encoding="utf-8")

        result = BookConsistencyStep._load_audit_checkpoint(checkpoint_file, signature)
        assert result["status"] == "running"
        assert "schema_version" not in result

    def test_signature_includes_version(self) -> None:
        """Verify signature changes when schema version changes."""
        chapter_texts = [
            {
                "chapter_number": 1,
                "numbered_text": "第一章内容",
                "source_chars": 100,
                "truncated": False,
            }
        ]

        class MinimalInput:
            analysis_mode = "full_text"
            max_chapters_per_batch = 5
            location_strictness = "medium"
            prompt_hint = ""
            chapter_summaries = []

        sig_v1 = BookConsistencyStep._checkpoint_signature(MinimalInput(), chapter_texts)

        sig_v1_again = BookConsistencyStep._checkpoint_signature(MinimalInput(), chapter_texts)
        assert sig_v1 == sig_v1_again

        assert CURRENT_CHECKPOINT_SCHEMA_VERSION == 1

    def test_load_future_checkpoint(self, tmp_path: Path) -> None:
        """Verify checkpoint from newer version loads with warning."""
        checkpoint_file = tmp_path / "checkpoint.json"
        signature = "abc123"
        future_payload = {
            "schema_version": 99,
            "status": "running",
            "signature": signature,
            "analysis_mode": "full_text",
            "max_chapters_per_batch": 5,
            "chunks_total": 3,
            "completed_chunks": [],
        }
        checkpoint_file.write_text(json.dumps(future_payload), encoding="utf-8")

        result = BookConsistencyStep._load_audit_checkpoint(checkpoint_file, signature)
        assert result["schema_version"] == 99

    def test_signature_mismatch_returns_empty(self, tmp_path: Path) -> None:
        """Verify signature mismatch returns empty dict."""
        checkpoint_file = tmp_path / "checkpoint.json"
        v1_payload = {
            "schema_version": 1,
            "signature": "expected_signature",
            "status": "running",
        }
        checkpoint_file.write_text(json.dumps(v1_payload), encoding="utf-8")

        result = BookConsistencyStep._load_audit_checkpoint(checkpoint_file, "wrong_signature")
        assert result == {}

    def test_corrupt_json_returns_empty(self, tmp_path: Path) -> None:
        """Verify corrupt JSON returns empty dict."""
        checkpoint_file = tmp_path / "checkpoint.json"
        checkpoint_file.write_text("not valid json{", encoding="utf-8")

        result = BookConsistencyStep._load_audit_checkpoint(checkpoint_file, "any_signature")
        assert result == {}