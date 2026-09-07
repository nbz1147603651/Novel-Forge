"""ProjectIssueLedger service — read/write/dedup/TTL for project-level issues.

Storage: ``<project>/states/issue_ledger.jsonl`` (append-only, same strategy
as state_ledger.jsonl).

Injection budget:
- ``load_prev_known_issues()`` injects at most 6 entries (highest severity first)
- Only injects entries with ``status in ("open", "repairing")`` and
  ``chapter_range`` covering the recent 10 chapters
- Format: ``{category, severity, summary}``, each entry ≤ 150 chars
"""

from __future__ import annotations

import json
import logging
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if sys.platform != "win32":
    import fcntl
else:
    fcntl = None  # type: ignore[assignment]

from novel_forge.core.schemas.issue_ledger import (
    ProjectIssueLedger,
    ProjectIssueLedgerEntry,
)

_log = logging.getLogger(__name__)

_SEVERITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}

_MAX_INJECT = 6
_MAX_INJECT_CHARS = 150


class IssueLedgerService:
    """Read/write/dedup/TTL service for the project issue ledger.

    Parameters
    ----------
    states_dir:
        Path to the project's ``states/`` directory.
    """

    def __init__(self, states_dir: Path) -> None:
        self._dir = Path(states_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "issue_ledger.jsonl"

    @property
    def path(self) -> Path:
        return self._path

    @contextmanager
    def _write_lock(self) -> Any:
        if sys.platform == "win32" or fcntl is None:
            yield
            return

        lock_path = self._dir / "issue_ledger.lock"
        with open(lock_path, "a", encoding="utf-8") as lock_fh:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def load_entries(self) -> list[ProjectIssueLedgerEntry]:
        """Load all entries from the JSONL file with dedup (last-writer-wins)."""
        if not self._path.exists():
            return []
        entries: list[ProjectIssueLedgerEntry] = []
        seen_ids: dict[str, int] = {}
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            _log.warning("issue_ledger_read_failed | path=%s | error=%s", self._path, exc)
            return []
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError:
                _log.warning("issue_ledger_corrupt_line | line=%d", line_no)
                continue
            try:
                entry = ProjectIssueLedgerEntry.model_validate(data)
            except Exception:
                _log.warning("issue_ledger_invalid_entry | line=%d", line_no)
                continue
            eid = entry.issue_id
            if eid in seen_ids:
                entries[seen_ids[eid]] = entry  # last-writer-wins
            else:
                seen_ids[eid] = len(entries)
                entries.append(entry)
        return entries

    def load_active(self) -> list[ProjectIssueLedgerEntry]:
        """Load only active (open/repairing) entries."""
        return [e for e in self.load_entries() if e.is_active]

    def load_snapshot(self) -> ProjectIssueLedger:
        """Load a full snapshot of active + archived entries."""
        all_entries = self.load_entries()
        return ProjectIssueLedger(
            active=[e for e in all_entries if e.is_active],
            archived=[e for e in all_entries if not e.is_active],
        )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def append_entries(self, entries: list[ProjectIssueLedgerEntry]) -> None:
        """Append entries to the JSONL file (idempotent by issue_id)."""
        if not entries:
            return
        lines = [
            json.dumps(e.model_dump(mode="json"), ensure_ascii=False) + "\n"
            for e in entries
        ]
        try:
            with self._write_lock():
                with open(self._path, "a", encoding="utf-8") as fh:
                    fh.writelines(lines)
        except OSError as exc:
            _log.warning("issue_ledger_write_failed | path=%s | error=%s", self._path, exc)

    def upsert_entry(self, entry: ProjectIssueLedgerEntry) -> None:
        """Upsert a single entry (append; dedup happens on load)."""
        self.append_entries([entry])

    def resolve_entry(
        self,
        issue_id: str,
        *,
        resolved_chapter: int | None = None,
    ) -> bool:
        """Mark an entry as resolved. Returns True if the entry was found."""
        entries = self.load_entries()
        found = False
        updates: list[ProjectIssueLedgerEntry] = []
        for entry in entries:
            if entry.issue_id == issue_id and entry.is_active:
                entry.status = "resolved"
                entry.resolved_at = datetime.now(timezone.utc).isoformat()
                entry.resolved_chapter = resolved_chapter
                updates.append(entry)
                found = True
        if updates:
            self.append_entries(updates)
        return found

    # ------------------------------------------------------------------
    # TTL expiry
    # ------------------------------------------------------------------

    def expire_old_entries(self, current_chapter: int) -> int:
        """Archive entries whose TTL has expired. Returns count of expired entries."""
        entries = self.load_entries()
        expired: list[ProjectIssueLedgerEntry] = []
        for entry in entries:
            if not entry.is_active:
                continue
            # Check if TTL has expired based on chapter_range
            max_ch = max(entry.chapter_range) if entry.chapter_range else 0
            if max_ch > 0 and current_chapter - max_ch > entry.ttl_chapters:
                entry.status = "deferred"
                expired.append(entry)
        if expired:
            self.append_entries(expired)
            _log.info(
                "issue_ledger_ttl_expire | chapter=%d | expired=%d",
                current_chapter,
                len(expired),
            )
        return len(expired)

    # ------------------------------------------------------------------
    # Injection for prompt context
    # ------------------------------------------------------------------

    def load_prev_known_issues(
        self,
        current_chapter: int,
        *,
        max_entries: int = _MAX_INJECT,
        lookback_chapters: int = 10,
    ) -> list[dict[str, str]]:
        """Load active issues for prompt injection.

        Returns a list of dicts with ``{category, severity, summary}`` keys,
        sorted by severity (critical first), limited to *max_entries*.
        Only includes entries whose ``chapter_range`` overlaps the recent
        *lookback_chapters* window.
        """
        active = self.load_active()
        window_start = max(1, current_chapter - lookback_chapters)

        # Filter by chapter_range overlap
        relevant = []
        for entry in active:
            if not entry.chapter_range:
                # No chapter range → include if recently created
                relevant.append(entry)
                continue
            if any(ch >= window_start for ch in entry.chapter_range):
                relevant.append(entry)

        # Sort by severity (critical first)
        relevant.sort(key=lambda e: _SEVERITY_ORDER.get(e.severity, 99))

        # Format for injection
        result: list[dict[str, str]] = []
        for entry in relevant[:max_entries]:
            summary = entry.summary[:_MAX_INJECT_CHARS]
            result.append({
                "category": entry.category,
                "severity": entry.severity,
                "summary": summary,
            })
        return result


__all__ = ["IssueLedgerService"]
