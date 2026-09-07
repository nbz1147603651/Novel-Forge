"""LRU artifact cache for TTS upstream data within a session.

Caches serialized voice_team, style_profile, narrator_profile and other
upstream artifacts to avoid redundant file I/O and re-serialization when
multiple TTS operations target the same project in quick succession.

Author: novel-forge
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Callable, TypeVar, cast

from novel_forge.obs.logger import get_logger

_log = get_logger("tts.runtime.artifact_cache")

_T = TypeVar("_T")

# Default TTL for cached entries (seconds). Voice team and style profile
# rarely change within a single editing session.  Synthesis itself takes far
# longer than 120s per segment, so the longer TTL lets retry/resume actually
# hit cached upstream artifacts instead of evicting them before reuse.
_DEFAULT_TTL_S = 600.0


class TTSArtifactCache:
    """Thread-safe LRU cache for TTS upstream artifacts.

    Usage::

        cache = TTSArtifactCache(max_size=32)
        voice_team = cache.get_or_load(
            f"{project_id}:voice_team",
            loader=lambda: load_voice_team(layout),
        )

    Author: novel-forge
    """

    def __init__(self, max_size: int = 32, ttl_s: float = _DEFAULT_TTL_S) -> None:
        self._max_size = max(1, max_size)
        self._ttl_s = max(1.0, ttl_s)
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get_or_load(self, key: str, loader: Callable[[], _T]) -> _T:
        """Return cached value or load via *loader*, caching the result.

        Author: novel-forge
        """
        with self._lock:
            if key in self._cache:
                timestamp, value = self._cache[key]
                if time.monotonic() - timestamp < self._ttl_s:
                    # Move to end (most recently used).
                    self._cache.move_to_end(key)
                    self._hits += 1
                    return cast(_T, value)
                # Expired — remove and reload.
                del self._cache[key]

        # Load outside the lock to avoid blocking other threads.
        value = loader()

        with self._lock:
            self._cache[key] = (time.monotonic(), value)
            self._cache.move_to_end(key)
            self._misses += 1
            # Evict oldest entries beyond capacity.
            while len(self._cache) > self._max_size:
                evicted_key, _ = self._cache.popitem(last=False)
                _log.debug("TTSArtifactCache evicted: %s", evicted_key)

        return value

    def invalidate(self, prefix: str) -> int:
        """Remove all entries whose key starts with *prefix*.

        Returns the number of entries removed.

        Author: novel-forge
        """
        with self._lock:
            keys_to_remove = [k for k in self._cache if k.startswith(prefix)]
            for key in keys_to_remove:
                del self._cache[key]
            if keys_to_remove:
                _log.debug(
                    "TTSArtifactCache invalidated %d entries for prefix %r",
                    len(keys_to_remove),
                    prefix,
                )
            return len(keys_to_remove)

    def invalidate_project(self, project_id: str) -> int:
        """Convenience: invalidate all cached artifacts for a project.

        Author: novel-forge
        """
        return self.invalidate(f"{project_id}:")

    def clear(self) -> None:
        """Remove all cached entries.

        Author: novel-forge
        """
        with self._lock:
            self._cache.clear()
            _log.debug("TTSArtifactCache cleared")

    @property
    def stats(self) -> dict[str, int]:
        """Return cache hit/miss statistics.

        Author: novel-forge
        """
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "size": len(self._cache),
                "max_size": self._max_size,
            }


# Module-level singleton for shared use across TTS workspace operations.
_global_tts_artifact_cache: TTSArtifactCache | None = None
_global_cache_lock = threading.Lock()


def get_tts_artifact_cache() -> TTSArtifactCache:
    """Return the module-level shared TTS artifact cache singleton.

    Author: novel-forge
    """
    global _global_tts_artifact_cache
    if _global_tts_artifact_cache is None:
        with _global_cache_lock:
            if _global_tts_artifact_cache is None:
                _global_tts_artifact_cache = TTSArtifactCache()
    return _global_tts_artifact_cache
