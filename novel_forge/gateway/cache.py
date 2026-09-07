"""CachePolicy — optional prompt-hash caching for idempotent tasks."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from novel_forge.gateway.types import ModelRequest, ModelResponse

_logger = logging.getLogger(__name__)


class CachePolicy:
    """In-memory cache keyed on prompt hash. Suitable for idempotent tasks like eval.

    When *db_path* is provided the cache is backed by a SQLite file so
    entries survive process restarts.  In-memory mode is used otherwise.

    Thread-safe via a reentrant lock. Supports context manager protocol.
    Uses OrderedDict to implement LRU eviction.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_size: int = 256,
        db_path: str | Path | None = None,
        ttl_seconds: int = 0,
        telemetry_enabled: bool = True,
    ) -> None:
        self._enabled = enabled
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._store: OrderedDict[str, ModelResponse] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._lock = threading.RLock()
        self._telemetry_enabled = telemetry_enabled
        self._hits_by_task: dict[str, int] = {}
        self._misses_by_task: dict[str, int] = {}

        # ── Optional SQLite backend ──
        self._db: sqlite3.Connection | None = None
        if db_path is not None:
            try:
                p = Path(db_path)
                p.parent.mkdir(parents=True, exist_ok=True)
                self._db = sqlite3.connect(str(p), check_same_thread=False)
                self._db.execute("PRAGMA journal_mode=WAL")
                self._db.execute("PRAGMA synchronous=NORMAL")
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS cache ("
                    "  key TEXT PRIMARY KEY,"
                    "  response TEXT NOT NULL,"
                    "  created_at REAL NOT NULL"
                    ")"
                )
                self._db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_cache_created ON cache(created_at)"
                )
                self._db.commit()
            except Exception as exc:
                _logger.warning("Failed to open cache DB %s: %s", db_path, exc)
                self._db = None

    def __enter__(self) -> "CachePolicy":
        self._lock.acquire()
        return self

    def __exit__(self, *args: Any) -> None:
        self._lock.release()

    def close(self) -> None:
        """Close the SQLite connection and release resources."""
        with self._lock:
            if self._db is not None:
                try:
                    self._db.close()
                except Exception:
                    pass
                self._db = None

    def __del__(self) -> None:
        """Ensure SQLite connection is closed on deletion."""
        try:
            self.close()
        except Exception:
            pass

    # ── Key hashing ──────────────────────────────────────────────

    @staticmethod
    def _hash(request: ModelRequest) -> str:
        raw = json.dumps(
            {
                "messages": request.messages,
                "model_id": request.model_id,
                "provider_id": request.provider_id,
                "max_tokens": request.max_tokens,
                "temperature": request.temperature,
                "task_type": request.task_type.value,
                "top_p": request.top_p,
                "thinking": request.thinking,
                "thinking_mode": request.thinking_mode,
                "multi_turn": request.multi_turn,
                "response_json_schema": request.response_json_schema,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    # ── Read ─────────────────────────────────────────────────────

    def get(self, request: ModelRequest) -> ModelResponse | None:
        if not self._enabled:
            return None
        key = self._hash(request)
        task = request.task_type.value

        with self._lock:
            # Check in-memory first (LRU: move to end on access)
            if key in self._store:
                resp = self._store[key]
                self._hits += 1
                self._hits_by_task[task] = self._hits_by_task.get(task, 0) + 1
                self._store.move_to_end(key)
                if self._telemetry_enabled:
                    _logger.info(
                        "gateway_cache_hit | task=%s | total_hits=%d",
                        task,
                        self._hits_by_task[task],
                    )
                return resp

        # Fallback to SQLite (outside lock to avoid holding it during I/O)
        if self._db is not None:
            try:
                row = self._db.execute(
                    "SELECT response, created_at FROM cache WHERE key = ?", (key,)
                ).fetchone()
                if row is not None:
                    if self._ttl > 0 and (time.time() - row[1]) > self._ttl:
                        with self._lock:
                            self._db.execute("DELETE FROM cache WHERE key = ?", (key,))
                            self._db.commit()
                    else:
                        resp = ModelResponse.model_validate_json(row[0])
                        with self._lock:
                            self._store[key] = resp  # promote to memory (LRU)
                            self._store.move_to_end(key)
                            self._hits += 1
                            self._hits_by_task[task] = self._hits_by_task.get(task, 0) + 1
                        if self._telemetry_enabled:
                            _logger.info(
                                "gateway_cache_hit_db | task=%s | total_hits=%d",
                                task,
                                self._hits_by_task.get(task, 0),
                            )
                        return resp
            except (sqlite3.Error, ValueError, KeyError) as exc:
                _logger.debug("Cache DB read failed: %s", exc)

        with self._lock:
            self._misses += 1
            self._misses_by_task[task] = self._misses_by_task.get(task, 0) + 1
        if self._telemetry_enabled:
            _logger.info(
                "gateway_cache_miss | task=%s | total_misses=%d",
                task,
                self._misses_by_task.get(task, 0),
            )
        return None

    # ── Write ────────────────────────────────────────────────────

    def put(self, request: ModelRequest, response: ModelResponse) -> None:
        if not self._enabled:
            return
        key = self._hash(request)
        task = request.task_type.value

        with self._lock:
            # Evict LRU entry if at capacity
            if len(self._store) >= self._max_size:
                self._store.popitem(last=False)
            self._store[key] = response
            self._store.move_to_end(key)

            # Persist to SQLite (inside lock to avoid race with concurrent get/put)
            if self._db is not None:
                try:
                    payload = response.model_dump_json()
                    self._db.execute(
                        "INSERT OR REPLACE INTO cache (key, response, created_at) VALUES (?, ?, ?)",
                        (key, payload, time.time()),
                    )
                    # Enforce max_size in DB
                    count = self._db.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
                    if count > self._max_size * 2:
                        excess = count - self._max_size
                        self._db.execute(
                            "DELETE FROM cache WHERE key IN ("
                            "  SELECT key FROM cache ORDER BY created_at ASC LIMIT ?"
                            ")",
                            (excess,),
                        )
                    self._db.commit()
                except Exception as exc:
                    _logger.debug("Cache DB write failed: %s", exc)

        if self._telemetry_enabled:
            _logger.info("gateway_cache_put | task=%s | memory_size=%d", task, self.size)

    # ── Management ───────────────────────────────────────────────

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
        if self._db is not None:
            try:
                self._db.execute("DELETE FROM cache")
                self._db.commit()
            except sqlite3.Error as exc:
                _logger.debug("Cache DB clear failed: %s", exc)

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._store)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            hits = self._hits
            misses = self._misses
            hits_by_task = dict(self._hits_by_task)
            misses_by_task = dict(self._misses_by_task)
        db_size = 0
        if self._db is not None:
            try:
                db_size = self._db.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
            except sqlite3.Error:
                pass
        return {
            "enabled": self._enabled,
            "memory_size": self.size,
            "db_size": db_size,
            "max_size": self._max_size,
            "hits": hits,
            "misses": misses,
            "hit_rate": round(hits / max(1, hits + misses), 3),
            "hits_by_task": hits_by_task,
            "misses_by_task": misses_by_task,
        }
