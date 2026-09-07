"""Checkpoint helpers for book consistency auto-repair."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from novel_forge.workspace.book_audit_checkpoint_store import BookAuditCheckpointStore
from novel_forge.workspace.book_ops.execution_book_common import _log


def _save_repair_checkpoint(path: Path | None, payload: dict[str, Any]) -> None:
    """Persist repair-phase checkpoint atomically."""
    BookAuditCheckpointStore.write_checkpoint_payload(path, payload)


def _load_repair_checkpoint(
    path: Path | None,
    expected_signature: str | None = None,
) -> dict[str, Any] | None:
    """Load and validate a repair-phase checkpoint."""
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _log.warning("book_consistency_repair: corrupt checkpoint at %s, starting fresh", path)
        return None
    if not isinstance(payload, dict):
        return None
    if expected_signature is not None:
        signature = str(payload.get("signature", "") or "")
        if signature != expected_signature:
            _log.info("book_consistency_repair: checkpoint signature mismatch, starting fresh")
            return None
    checksum_stored = payload.get("checksum")
    completed = payload.get("completed_chapters", [])
    if checksum_stored is not None and isinstance(completed, list):
        chunks_data = json.dumps(completed, ensure_ascii=False, sort_keys=True, default=str)
        checksum_computed = hashlib.sha256(chunks_data.encode("utf-8")).hexdigest()
        if checksum_computed != str(checksum_stored):
            _log.warning(
                "book_consistency_repair: checksum mismatch in checkpoint at %s, starting fresh",
                path,
            )
            return None
    return payload


def _compute_repair_checksum(chapters: list[int]) -> str:
    """Compute SHA256 checksum for a list of chapter numbers."""
    data = json.dumps(chapters, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _repair_checkpoint_signature(
    chapter_groups: list[tuple[int, list[dict[str, Any]]]],
    *,
    min_severity: str,
    max_chapters: int,
) -> str:
    """Bind a repair checkpoint to the exact issue set and repair scope."""

    def _int_value(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    payload: dict[str, Any] = {
        "min_severity": min_severity,
        "max_chapters": max_chapters,
        "chapters": [],
    }
    for chapter_num, issues in chapter_groups:
        payload["chapters"].append(
            {
                "chapter_number": chapter_num,
                "issues": [
                    {
                        "issue_id": str(issue.get("issue_id", "") or ""),
                        "category": str(issue.get("category", "") or ""),
                        "severity": str(issue.get("severity", "") or ""),
                        "primary_chapter": _int_value(issue.get("primary_chapter")),
                        "paragraph_index": _int_value(issue.get("paragraph_index")),
                        "description": str(issue.get("description", "") or "")[:300],
                    }
                    for issue in issues
                    if isinstance(issue, dict)
                ],
            }
        )
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
