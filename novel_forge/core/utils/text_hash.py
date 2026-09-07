"""Canonical source-text hash utility.

Single source of truth for ``source_text_hash`` used across the codebase.
All private ``_source_text_hash`` copies in workspace/ and pipeline/ modules
share this exact implementation (SHA-256 of UTF-8 encoded text).
"""

from __future__ import annotations

import hashlib

# Small LRU cache for repeated hashing of the same text within a chapter flow.
# A cache of 4 entries covers the common pattern where the same ``current_text``
# is hashed at multiple review checkpoints without modification.
_HASH_CACHE_MAX = 4
_hash_cache: dict[int, tuple[str, str]] = {}  # id -> (text_ref, hash)
_hash_cache_order: list[int] = []


def source_text_hash(text: str) -> str:
    """Return a stable SHA-256 hex digest for *text*.

    Mirrors the behaviour of every private ``_source_text_hash`` copy so that
    cache-invalidation logic remains identical after migration.

    Uses a small identity-aware cache with content verification: when the exact
    same string object is hashed repeatedly (common in the review pipeline),
    the cached result is returned without recomputing SHA-256.
    """
    tid = id(text)
    cached = _hash_cache.get(tid)
    if cached is not None and cached[0] is text:
        return cached[1]
    result = hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()
    # Evict oldest entry if cache is full
    if len(_hash_cache) >= _HASH_CACHE_MAX:
        oldest = _hash_cache_order.pop(0)
        _hash_cache.pop(oldest, None)
    _hash_cache[tid] = (text, result)
    _hash_cache_order.append(tid)
    return result
