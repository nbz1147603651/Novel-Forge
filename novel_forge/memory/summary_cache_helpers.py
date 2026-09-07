"""Helpers for legacy summary cache normalization and regeneration decisions."""

from __future__ import annotations

import hashlib
from typing import Any


def compute_summary_source_hash(text: str) -> str:
    """Compute deterministic hash for chapter text."""
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def normalize_summary_cache_entry(raw: Any) -> dict[str, Any] | None:
    """Normalize summary cache entry from legacy/new formats."""
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        return {
            "text": text,
            "source_hash": "",
            "version": 1,
            "updated_at": "",
        }

    if isinstance(raw, dict):
        text = str(raw.get("text", "") or "").strip()
        if not text:
            return None
        try:
            version = int(raw.get("version", 1) or 1)
        except Exception:
            version = 1
        return {
            "text": text,
            "source_hash": str(raw.get("source_hash", "") or ""),
            "version": max(1, version),
            "updated_at": str(raw.get("updated_at", "") or ""),
        }

    return None


def normalize_summary_stats(raw: Any) -> dict[str, int]:
    """Normalize persisted summary stats structure."""
    defaults = {
        "generated": 0,
        "regenerated": 0,
        "hash_skips": 0,
        "legacy_migrated": 0,
        "invalidated_short": 0,
        "reindexed": 0,
    }
    if not isinstance(raw, dict):
        return defaults

    normalized = dict(defaults)
    for key in defaults:
        try:
            normalized[key] = max(0, int(raw.get(key, 0) or 0))
        except Exception:
            normalized[key] = 0
    return normalized


def should_generate_summary(
    *,
    summary_cache: dict[int, Any],
    summary_stats: dict[str, int],
    chapter_number: int,
    chapter_text: str,
) -> bool:
    """Determine if a new summary should be generated for this chapter."""
    current_hash = compute_summary_source_hash(chapter_text)

    cached_raw = summary_cache.get(chapter_number)
    cached = (
        cached_raw
        if isinstance(cached_raw, dict)
        else normalize_summary_cache_entry(cached_raw)
    )
    if cached_raw is not None and cached is not None and not isinstance(cached_raw, dict):
        summary_cache[chapter_number] = cached
        summary_stats["legacy_migrated"] = summary_stats.get("legacy_migrated", 0) + 1
    if not cached:
        if len(chapter_text) < 1000:
            return False
        return True

    cached_hash = str(cached.get("source_hash", "") or "")
    if len(chapter_text) < 1000:
        if not cached_hash or cached_hash != current_hash:
            summary_cache.pop(chapter_number, None)
            summary_stats["invalidated_short"] = summary_stats.get("invalidated_short", 0) + 1
        return False

    if not cached_hash:
        return True

    return cached_hash != current_hash


__all__ = (
    "compute_summary_source_hash",
    "normalize_summary_cache_entry",
    "normalize_summary_stats",
    "should_generate_summary",
)
