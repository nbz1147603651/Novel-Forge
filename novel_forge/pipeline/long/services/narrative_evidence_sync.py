"""Thin orchestration for keeping narrative evidence retrieval current.

The narrative-state store remains authoritative.  This module only projects
already-adjudicated records into the optional retrieval index, so failures are
observable but never rewrite or reinterpret story state.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from novel_forge.narrative_state.store import NarrativeStateStore

_log = logging.getLogger(__name__)


async def sync_narrative_evidence(
    *,
    memory_context: Any | None,
    project_root: Path,
    ledger_entries: list[Any] | None = None,
    full_rebuild: bool = False,
) -> dict[str, Any]:
    """Synchronize accepted registry/state records into Zvec.

    ``full_rebuild`` is reserved for repair/migration flows that can invalidate
    old ledger entries.  Normal chapter finalization passes only the newly
    accepted entries, keeping the cost bounded for very long novels.
    """
    service = getattr(memory_context, "narrative_evidence_service", None)
    sync = getattr(service, "sync_narrative_state", None)
    if not callable(sync):
        return {"status": "skipped", "reason": "evidence_service_unavailable"}

    store = NarrativeStateStore(project_root)
    registry = store.load_entity_registry()
    entries = store.load_ledger_entries() if full_rebuild else list(ledger_entries or [])
    try:
        stats = await sync(
            entity_registry=registry,
            ledger_entries=entries,
            replace=full_rebuild,
        )
    except Exception as exc:  # retrieval is optional; canon/state stay authoritative
        _log.warning(
            "narrative_evidence_sync_failed | project=%s | full_rebuild=%s | error=%s",
            project_root,
            full_rebuild,
            exc,
            exc_info=True,
        )
        return {
            "status": "degraded",
            "reason": "sync_failed",
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
        }
    return {
        "status": "updated",
        "full_rebuild": full_rebuild,
        "indexed_cards": int(getattr(stats, "indexed_cards", 0) or 0),
        "updated_cards": int(getattr(stats, "updated_cards", 0) or 0),
        "removed_cards": int(getattr(stats, "removed_cards", 0) or 0),
        "rebuilt_cards": int(getattr(stats, "rebuilt_cards", 0) or 0),
    }


__all__ = ["sync_narrative_evidence"]
