"""Dead Letter Queue — persists failed model requests for later inspection/retry."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from novel_forge.persistence.filesystem import atomic_write_json

_log = __import__("novel_forge.obs.logger", fromlist=["get_logger"]).get_logger("gateway.dlq")


@dataclass(frozen=True)
class DeadLetterEntry:
    """A single failed request record."""

    entry_id: str
    task_type: str
    error: str
    routes_tried: list[dict[str, str]]
    request: dict[str, Any]
    timestamp: float
    retry_count: int = 0


class DeadLetterQueue:
    """Persistent dead letter queue for failed model gateway requests.

    Each failed request is stored as an individual JSON file in
    ``data/<project>/dlq/`` using atomic writes.
    """

    def __init__(self, storage_dir: Path, max_entries: int = 100) -> None:
        self._storage_dir = storage_dir / "dlq"
        self._max_entries = max_entries
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._in_memory: list[DeadLetterEntry] = []
        self._lock = RLock()
        self._load_existing()

    def _load_existing(self) -> None:
        entries: list[DeadLetterEntry] = []
        for f in sorted(self._storage_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                entries.append(
                    DeadLetterEntry(
                        entry_id=data["entry_id"],
                        task_type=data["task_type"],
                        error=data["error"],
                        routes_tried=data["routes_tried"],
                        request=data["request"],
                        timestamp=data["timestamp"],
                        retry_count=data.get("retry_count", 0),
                    )
                )
            except (json.JSONDecodeError, KeyError, OSError) as exc:
                _log.warning("dlq_load_failed | file=%s | error=%s", f, exc)
        with self._lock:
            self._in_memory = entries
            self._enforce_max_entries()

    def enqueue(
        self,
        *,
        task_type: str,
        request: dict[str, Any],
        error: str,
        routes_tried: list[dict[str, str]],
    ) -> str:
        entry_id = uuid.uuid4().hex[:12]
        entry = DeadLetterEntry(
            entry_id=entry_id,
            task_type=task_type,
            error=error,
            routes_tried=routes_tried,
            request=request,
            timestamp=time.time(),
        )
        with self._lock:
            self._in_memory.append(entry)
            self._persist_entry(entry)
            self._enforce_max_entries()
        return entry_id

    def get_failed(self) -> list[DeadLetterEntry]:
        with self._lock:
            return list(self._in_memory)

    def retry(self, entry_id: str) -> DeadLetterEntry | None:
        with self._lock:
            for i, entry in enumerate(self._in_memory):
                if entry.entry_id == entry_id:
                    updated = entry.__class__(
                        entry_id=entry.entry_id,
                        task_type=entry.task_type,
                        error=entry.error,
                        routes_tried=entry.routes_tried,
                        request=entry.request,
                        timestamp=entry.timestamp,
                        retry_count=entry.retry_count + 1,
                    )
                    self._in_memory[i] = updated
                    self._persist_entry(updated)
                    return updated
        return None

    def purge(self, entry_id: str | None = None) -> int:
        with self._lock:
            if entry_id is not None:
                before = len(self._in_memory)
                self._in_memory = [e for e in self._in_memory if e.entry_id != entry_id]
                removed = before - len(self._in_memory)
                if removed:
                    self._remove_file(entry_id)
                return removed
            count = len(self._in_memory)
            for entry in self._in_memory:
                self._remove_file(entry.entry_id)
            self._in_memory = []
            return count

    def size(self) -> int:
        with self._lock:
            return len(self._in_memory)

    def _persist_entry(self, entry: DeadLetterEntry) -> None:
        path = self._storage_dir / f"{entry.entry_id}.json"
        atomic_write_json(
            path,
            {
                "entry_id": entry.entry_id,
                "task_type": entry.task_type,
                "error": entry.error,
                "routes_tried": entry.routes_tried,
                "request": entry.request,
                "timestamp": entry.timestamp,
                "retry_count": entry.retry_count,
            },
        )

    def _remove_file(self, entry_id: str) -> None:
        path = self._storage_dir / f"{entry_id}.json"
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            _log.warning("dlq_remove_failed | file=%s | error=%s", path, exc)

    def _enforce_max_entries(self) -> None:
        if self._max_entries <= 0:
            return
        while len(self._in_memory) > self._max_entries:
            oldest = self._in_memory.pop(0)
            self._remove_file(oldest.entry_id)
            _log.info(
                "dlq_purge_oldest | entry_id=%s | size=%d", oldest.entry_id, len(self._in_memory)
            )
