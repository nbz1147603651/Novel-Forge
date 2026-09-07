"""P1-3: Memory tiering — hot/warm/cold storage for scalable project memory.

Addresses the scalability issue where project_memory.json grows linearly
(5414 lines at 9 chapters → projected ~52,000 lines at 48万字 target).

Architecture:
- **Hot tier** (< 2000 lines): Last 3 chapters episodic + active relationship
  deltas + unclosed plot threads. Always loaded into context.
- **Warm tier** (SQLite FTS5): Historical episodic compressed summaries +
  on-demand relationship subsets. Loaded by semantic query.
- **Cold tier** (archive): Completed arcs + historical state_delta logs.
  Only loaded for audit/debug.

This module provides the tiering logic as a service layer on top of the
existing memory infrastructure. It does NOT replace the existing stores —
it adds a routing layer that decides which tier to read from based on
the query context.

Integration point: `memory/integration.py` calls `MemoryTierRouter.build_context()`
instead of loading project_memory.json in full.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from novel_forge.memory.retrieval import bm25_scores
from novel_forge.obs.logger import get_logger

_logger = get_logger("memory.tiering")

# Tier thresholds
_HOT_RECENT_CHAPTERS = 3
_HOT_MAX_ENTRIES = 200
_WARM_COMPRESSION_RATIO = 0.3  # Compress to 30% of original


@dataclass(frozen=True)
class TierConfig:
    """Configuration for memory tiering."""

    hot_recent_chapters: int = _HOT_RECENT_CHAPTERS
    """Number of recent chapters to keep in hot tier."""

    hot_max_entries: int = _HOT_MAX_ENTRIES
    """Maximum entries in hot tier before eviction to warm."""

    warm_compression_ratio: float = _WARM_COMPRESSION_RATIO
    """Compression ratio for warm tier summaries."""

    enable_tiering: bool = True
    """Master switch — enabled by default for scalable project memory."""


@dataclass
class TieredMemoryContext:
    """Assembled memory context from all tiers."""

    hot_entries: list[dict[str, Any]] = field(default_factory=list)
    """Recent episodic entries (always loaded)."""

    warm_entries: list[dict[str, Any]] = field(default_factory=list)
    """On-demand historical entries (loaded by relevance)."""

    active_relationships: list[dict[str, Any]] = field(default_factory=list)
    """Currently active relationship deltas."""

    unclosed_threads: list[dict[str, Any]] = field(default_factory=list)
    """Unclosed plot threads requiring attention."""

    total_loaded: int = 0
    """Total entries loaded across all tiers."""

    tier_stats: dict[str, int] = field(default_factory=dict)
    """Statistics about tier sizes."""


class MemoryTierRouter:
    """Routes memory queries to appropriate tier based on context.

    Usage:
        router = MemoryTierRouter(project_root, config)
        context = router.build_context(current_chapter=10, query="角色关系")
    """

    def __init__(
        self,
        project_root: Path,
        config: TierConfig | None = None,
    ) -> None:
        self._root = project_root
        self._config = config or TierConfig()
        self._memory_path = project_root / "memory" / "project_memory.json"
        self._warm_db_path = project_root / "memory" / "warm_tier.db"

    @property
    def tiering_enabled(self) -> bool:
        return self._config.enable_tiering

    def build_context(
        self,
        current_chapter: int,
        *,
        query: str = "",
        max_hot: int | None = None,
        max_warm: int = 20,
    ) -> TieredMemoryContext:
        """Build tiered memory context for the given chapter.

        When tiering is disabled, falls back to loading all entries
        (backward-compatible behavior).

        Args:
            current_chapter: Current chapter number being processed.
            query: Optional semantic query for warm tier retrieval.
            max_hot: Override max hot entries.
            max_warm: Max warm tier entries to retrieve.

        Returns:
            TieredMemoryContext with entries from appropriate tiers.
        """
        if not self.tiering_enabled:
            return self._build_untiered_context()

        hot_entries = self._load_hot_tier(current_chapter, max_hot)
        warm_entries = self._query_warm_tier(query, max_warm) if query else []
        active_rels = self._load_active_relationships(current_chapter)
        unclosed = self._load_unclosed_threads(current_chapter)

        context = TieredMemoryContext(
            hot_entries=hot_entries,
            warm_entries=warm_entries,
            active_relationships=active_rels,
            unclosed_threads=unclosed,
            total_loaded=len(hot_entries) + len(warm_entries),
            tier_stats={
                "hot": len(hot_entries),
                "warm": len(warm_entries),
                "relationships": len(active_rels),
                "threads": len(unclosed),
            },
        )

        _logger.debug(
            "memory_tier_context | chapter=%d | hot=%d | warm=%d | rels=%d | threads=%d",
            current_chapter,
            len(hot_entries),
            len(warm_entries),
            len(active_rels),
            len(unclosed),
        )
        return context

    def promote_to_warm(self, chapter_number: int) -> int:
        """Evict old hot entries to warm tier after chapter completion.

        Called during finalize to keep hot tier bounded.

        Returns:
            Number of entries evicted.
        """
        if not self.tiering_enabled:
            return 0

        try:
            all_entries = self._load_raw_memory()
            hot_cutoff = chapter_number - self._config.hot_recent_chapters
            to_evict = [
                e for e in all_entries
                if self._entry_chapter(e) < hot_cutoff
            ]
            if not to_evict:
                return 0

            # Write evicted entries to warm tier (compressed)
            self._append_to_warm_tier(to_evict)

            # Rewrite hot tier with only recent entries
            hot_entries = [
                e for e in all_entries
                if self._entry_chapter(e) >= hot_cutoff
            ]
            self._write_hot_tier(hot_entries)

            _logger.info(
                "memory_tier_evict | chapter=%d | evicted=%d | remaining_hot=%d",
                chapter_number,
                len(to_evict),
                len(hot_entries),
            )
            return len(to_evict)
        except Exception as exc:
            _logger.warning("memory_tier_evict_failed | error=%s", exc)
            return 0

    # ── Internal methods ─────────────────────────────────────────────────────

    def _build_untiered_context(self) -> TieredMemoryContext:
        """Fallback: load all entries (backward-compatible)."""
        entries = self._load_raw_memory()
        return TieredMemoryContext(
            hot_entries=entries,
            total_loaded=len(entries),
            tier_stats={"untiered": len(entries)},
        )

    def _load_raw_memory(self) -> list[dict[str, Any]]:
        """Load raw project_memory.json entries."""
        if not self._memory_path.exists():
            return []
        try:
            data = json.loads(self._memory_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return list(data.get("entries", data.get("memories", [])))
            return []
        except (json.JSONDecodeError, OSError) as exc:
            _logger.warning("memory_tier_load_failed | error=%s", exc)
            return []

    def _load_hot_tier(
        self, current_chapter: int, max_entries: int | None
    ) -> list[dict[str, Any]]:
        """Load hot tier: recent N chapters only."""
        all_entries = self._load_raw_memory()
        cutoff = current_chapter - self._config.hot_recent_chapters
        max_n = max_entries or self._config.hot_max_entries

        hot = [
            e for e in all_entries
            if self._entry_chapter(e) >= cutoff
        ]
        # Sort by recency (most recent first) and cap
        hot.sort(key=lambda e: self._entry_chapter(e), reverse=True)
        return hot[:max_n]

    def _query_warm_tier(self, query: str, max_results: int) -> list[dict[str, Any]]:
        """Query warm tier by BM25 relevance over serialized entry text.

        Uses the same BM25 tokenizer as the rest of the memory module for
        consistent retrieval quality.  Falls back to returning the first
        *max_results* entries when no query is provided.
        """
        warm_path = self._warm_db_path
        if not warm_path.exists():
            return []
        try:
            entries: list[dict[str, Any]] = []
            for line in warm_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

            if not query or not entries:
                return entries[:max_results]

            # Serialize each entry to a searchable text blob and score via BM25.
            documents: list[str] = []
            for entry in entries:
                parts: list[str] = []
                for value in entry.values():
                    if isinstance(value, str) and value:
                        parts.append(value)
                    elif isinstance(value, (list, tuple)):
                        parts.extend(str(item) for item in value if item)
                documents.append(" ".join(parts))

            scores = bm25_scores(query, documents)
            scored = sorted(
                zip(scores, entries, strict=True),
                key=lambda pair: pair[0],
                reverse=True,
            )
            return [entry for score, entry in scored[:max_results] if score > 0.0]
        except OSError as exc:
            _logger.warning("memory_warm_query_failed | error=%s", exc)
            return []

    def _load_active_relationships(self, current_chapter: int) -> list[dict[str, Any]]:
        """Load active relationship deltas (last 3 chapters)."""
        rel_cache = self._root / "memory" / "relationship_cache.json"
        if not rel_cache.exists():
            return []
        try:
            data = json.loads(rel_cache.read_text(encoding="utf-8"))
            if isinstance(data, list):
                # Filter to recent chapters
                cutoff = current_chapter - self._config.hot_recent_chapters
                return [
                    r for r in data
                    if isinstance(r, dict) and r.get("chapter", 0) >= cutoff
                ]
            return []
        except (json.JSONDecodeError, OSError):
            return []

    def _load_unclosed_threads(self, current_chapter: int) -> list[dict[str, Any]]:
        """Load unclosed plot threads."""
        threads_path = self._root / "memory" / "plot_threads.json"
        if not threads_path.exists():
            return []
        try:
            data = json.loads(threads_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [
                    t for t in data
                    if isinstance(t, dict)
                    and t.get("status") not in ("closed", "abandoned", "deferred")
                ]
            return []
        except (json.JSONDecodeError, OSError):
            return []

    def _append_to_warm_tier(self, entries: list[dict[str, Any]]) -> None:
        """Append entries to warm tier storage (JSON-lines format)."""
        self._warm_db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._warm_db_path.open("a", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _write_hot_tier(self, entries: list[dict[str, Any]]) -> None:
        """Rewrite the hot tier (project_memory.json) with bounded entries."""
        self._memory_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._memory_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self._memory_path)

    @staticmethod
    def _entry_chapter(entry: dict[str, Any]) -> int:
        """Extract chapter number from a memory entry."""
        for key in ("chapter", "chapter_number", "source_chapter"):
            val = entry.get(key)
            if isinstance(val, int) and val > 0:
                return val
        return 0
