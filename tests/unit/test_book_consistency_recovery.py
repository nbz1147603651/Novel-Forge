"""Tests for whole-book audit rollback snapshots."""

from __future__ import annotations

import hashlib
import json

from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.book_audit_checkpoint_store import BookAuditCheckpointStore
from novel_forge.workspace.book_ops.execution_book_recovery import (
    book_audit_checkpoint_path,
    create_book_audit_snapshot,
    restore_book_audit_snapshot,
    validate_checkpoint,
)


def test_book_audit_snapshot_restores_mutation_directories(tmp_path) -> None:  # noqa: ANN001
    layout = ProjectLayout(tmp_path)
    layout.chapters_dir.mkdir(parents=True)
    layout.reports_dir.mkdir(parents=True)
    layout.states_dir.mkdir(parents=True)
    layout.drafts_dir.mkdir(parents=True)
    (layout.chapters_dir / "chapter_001.md").write_text("before", encoding="utf-8")
    (layout.reports_dir / "book_consistency_audit.json").write_text("{}", encoding="utf-8")

    snapshot = create_book_audit_snapshot(layout)
    (layout.chapters_dir / "chapter_001.md").write_text("after", encoding="utf-8")
    (layout.reports_dir / "new_report.json").write_text("new", encoding="utf-8")

    restore_book_audit_snapshot(layout, snapshot)

    assert (layout.chapters_dir / "chapter_001.md").read_text(encoding="utf-8") == "before"
    assert not (layout.reports_dir / "new_report.json").exists()


def test_book_audit_checkpoint_store_preserves_legacy_paths(tmp_path) -> None:  # noqa: ANN001
    layout = ProjectLayout(tmp_path)
    store = BookAuditCheckpointStore(layout)

    assert store.audit_path == book_audit_checkpoint_path(layout)
    assert store.audit_path.name == "book_consistency_audit_checkpoint.json"
    assert store.verify_path.name == "book_consistency_verify_checkpoint.json"
    assert store.repair_path.name == "book_consistency_repair_checkpoint.json"


def test_book_audit_checkpoint_store_reads_existing_audit_payload(tmp_path) -> None:  # noqa: ANN001
    layout = ProjectLayout(tmp_path)
    store = BookAuditCheckpointStore(layout)
    completed_chunks = [{"chunk": 1, "chapters": [1, 2]}]
    checksum = hashlib.sha256(
        json.dumps(completed_chunks, ensure_ascii=False, sort_keys=True, default=str).encode(
            "utf-8"
        )
    ).hexdigest()
    payload = {
        "schema_version": 1,
        "signature": "sig",
        "status": "running",
        "chunks_total": 2,
        "completed_chunks": completed_chunks,
        "checksum": checksum,
    }

    store.write_audit_checkpoint(payload)

    assert store.read_audit_checkpoint() == payload
    assert validate_checkpoint(store.audit_path) == {
        "valid": True,
        "error": None,
        "status": "running",
        "completed": 1,
        "total": 2,
    }


def test_book_audit_checkpoint_store_keeps_checksum_validation(tmp_path) -> None:  # noqa: ANN001
    layout = ProjectLayout(tmp_path)
    store = BookAuditCheckpointStore(layout)
    store.write_audit_checkpoint(
        {
            "schema_version": 1,
            "signature": "sig",
            "status": "running",
            "chunks_total": 1,
            "completed_chunks": [{"chunk": 1}],
            "checksum": "bad",
        }
    )

    validation = store.validate_audit_checkpoint()

    assert validation["valid"] is False
    assert validation["error"] == "Checksum mismatch: completed_chunks data may be corrupted"
