"""Compatibility recovery helpers for whole-book consistency audit and repair."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.book_audit_checkpoint_store import (
    BookAuditCheckpointStore,
)
from novel_forge.workspace.book_audit_checkpoint_store import (
    invalidate_checkpoint as _invalidate_checkpoint,
)
from novel_forge.workspace.book_audit_checkpoint_store import (
    summarize_book_audit_checkpoint as _summarize_book_audit_checkpoint,
)
from novel_forge.workspace.book_audit_checkpoint_store import (
    validate_checkpoint as _validate_checkpoint,
)

__all__ = [
    "book_audit_checkpoint_path",
    "create_book_audit_snapshot",
    "restore_book_audit_snapshot",
    "summarize_book_audit_checkpoint",
    "validate_checkpoint",
    "invalidate_checkpoint",
]


def book_audit_checkpoint_path(layout: ProjectLayout) -> Path:
    """Path for resumable full-text audit checkpoints."""
    return BookAuditCheckpointStore(layout).audit_path


def create_book_audit_snapshot(layout: ProjectLayout) -> dict[str, Any]:
    """Copy mutation-prone project directories before a whole-book repair run."""
    return BookAuditCheckpointStore(layout).create_snapshot()


def restore_book_audit_snapshot(layout: ProjectLayout, manifest: dict[str, Any]) -> None:
    """Restore project directories captured by ``create_book_audit_snapshot``."""
    BookAuditCheckpointStore(layout).restore_snapshot(manifest)


def summarize_book_audit_checkpoint(path: Path) -> str:
    """Return a compact status line for an audit checkpoint file."""
    return _summarize_book_audit_checkpoint(path)


def validate_checkpoint(path: Path) -> dict[str, Any]:
    """Validate a book audit checkpoint file."""
    return _validate_checkpoint(path)


def invalidate_checkpoint(path: Path) -> None:
    """Rename a checkpoint file with .invalidated suffix to mark it as unusable."""
    _invalidate_checkpoint(path)
