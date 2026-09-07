"""Job snapshot cache — process-local LRU + physical file signature.

Caches `ModelCallSnapshot` per run_id. Cache invalidation is driven by
the signature (file_count + latest_mtime_ns + total_size_bytes) so that
append-only model_calls/ folders (pipeline writes new JSON continuously)
invalidate the cache as soon as a new file appears.

TTL is a backstop only — TTL > physical IO cost would mask genuine
invalidation, so we keep TTL modest (60s default) and rely on signature
invalidation as the primary mechanism.

Concurrency: get_or_load uses a single threading.Lock around cache dict
mutation; loader() runs OUTSIDE the lock so concurrent reads don't
serialize on disk IO.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class _SnapshotSignature:
    """Physical signature of a model_calls/ directory.

    file_count: number of *.json files
    latest_mtime_ns: max mtime across all files (ns)
    total_size_bytes: sum of stat().st_size
    """

    file_count: int
    latest_mtime_ns: int
    total_size_bytes: int

    @classmethod
    def compute(cls, model_calls_dir: Path) -> _SnapshotSignature:
        if not model_calls_dir.is_dir():
            return cls(0, 0, 0)
        count = 0
        latest_ns = 0
        total = 0
        for p in model_calls_dir.rglob("*.json"):
            try:
                st = p.stat()
            except OSError:
                continue
            count += 1
            total += st.st_size
            if st.st_mtime_ns > latest_ns:
                latest_ns = st.st_mtime_ns
        return cls(count, latest_ns, total)


class JobSnapshotCache:
    """LRU cache from run_id -> (timestamp, signature, snapshot)."""

    def __init__(self, max_entries: int = 64, ttl_s: float = 60.0):
        self._cache: OrderedDict[str, tuple[float, _SnapshotSignature, Any]] = (
            OrderedDict()
        )
        self._lock = threading.Lock()
        self._max = max_entries
        self._ttl = ttl_s

    def get_or_load(
        self,
        run_id: str,
        model_calls_dir: Path,
        loader: Callable[[], Any],
    ) -> Any:
        current_sig = _SnapshotSignature.compute(model_calls_dir)
        now = time.time()
        with self._lock:
            entry = self._cache.get(run_id)
            if entry is not None:
                ts, sig, snap = entry
                if sig == current_sig and (now - ts) < self._ttl:
                    self._cache.move_to_end(run_id)
                    return snap
                # signature mismatch or TTL expired
                self._cache.pop(run_id, None)

        # IO outside lock
        snap = loader()

        with self._lock:
            self._cache[run_id] = (now, current_sig, snap)
            self._cache.move_to_end(run_id)
            while len(self._cache) > self._max:
                self._cache.popitem(last=False)
        return snap

    def invalidate(self, run_id: str) -> None:
        with self._lock:
            self._cache.pop(run_id, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


__all__ = ["JobSnapshotCache", "_SnapshotSignature"]