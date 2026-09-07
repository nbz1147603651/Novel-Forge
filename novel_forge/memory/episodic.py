"""Episodic Memory — chapter-level event indexing and retrieval.

Provides semantic and temporal search capabilities for historical events,
enabling recall of archived content when contextually relevant.
"""

from __future__ import annotations

import hashlib
import inspect
import logging
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from novel_forge.core.schemas.volume import VolumeAuditReport
    from novel_forge.memory.critic import CritiqueReport

from novel_forge.core.schemas.chapter import ChapterOutcome
from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.memory.base import (
    EpisodicResult,
    MemoryRetrievalResult,
    SearchQuery,
)
from novel_forge.memory.episodic_models import (
    CritiqueIndex,
    EpisodicIndex,
    OutlineContext,
    OutlinePlotPoint,
    RelationshipChange,
    VolumeAuditIndex,
    generate_lesson_learned,
    summarize_failure_pattern,
)
from novel_forge.memory.vector_store import (  # noqa: F401
    InMemoryVectorStore,
    VectorStore,
    create_vector_store,
)

_log = logging.getLogger(__name__)


class EpisodicMemory:
    """Episodic memory system for chapter-level event storage and retrieval.

    Supports:
    - Semantic search: Find events by meaning/context
    - Temporal search: Find events within chapter ranges
    - Hybrid search: Combine semantic + temporal + filters
    - Automatic indexing from ChapterOutcome

    Embedding Service Integration:
    - Uses EmbeddingService for real vector generation (default)
    - Uses deterministic mock embeddings only when mock mode is explicitly enabled
    """

    def __init__(
        self,
        *,
        embedding_config: dict[str, Any] | None = None,
        use_mock_embeddings: bool = False,
        vector_store_backend: str = "zvec",
        vector_store_path: str | Path | None = None,
        zvec_index_type: str = "hnsw",
        zvec_memory_limit_mb: int = 512,
        extra_int_fields: tuple[str, ...] | None = None,
    ) -> None:
        """Initialize episodic memory.

        Args:
            embedding_config: Configuration for embedding service
                {
                    "provider": "openai",
                    "api_key": "sk-...",
                    "model": "text-embedding-3-small",
                    "base_url": null,
                    "dimensions": 512,
                }
            use_mock_embeddings: If True, always use mock embeddings (test/debug mode).
                Default False - requires a real embedding configuration.
        """
        self._embedding_config = embedding_config
        self._use_mock_embeddings = use_mock_embeddings
        self._embedding_service = None
        self._mock_dimensions: int = 1536  # default; overridden from model config
        self._vector_dimension: int | None = None
        self._vector_store_backend_requested = (
            str(vector_store_backend or "zvec").strip().lower().replace("-", "_")
        )
        self._vector_store_path = Path(vector_store_path) if vector_store_path else None
        self._zvec_index_type = zvec_index_type
        self._zvec_memory_limit_mb = zvec_memory_limit_mb
        self._embedding_cache_max_entries = 2048
        self._embedding_cache: OrderedDict[str, list[float]] = OrderedDict()
        self._embedding_stats: Counter[str] = Counter()
        self._vector_store_extra_int_fields = tuple(
            field
            for field in dict.fromkeys(str(item).strip() for item in (extra_int_fields or ()))
            if field
        )

        if not embedding_config and not use_mock_embeddings:
            raise ValueError(
                "EpisodicMemory requires embedding_config when use_mock_embeddings=False. "
                "Configure NOVEL_FORGE_MEMORY_EMBEDDING_PROFILE_ID for production, "
                "or set NOVEL_FORGE_MEMORY_USE_MOCK_EMBEDDINGS=true only for tests/offline mocks."
            )

        backend_key = self._vector_store_backend_requested
        if backend_key not in {"zvec", "in_memory"}:
            raise ValueError(
                "unknown vector store backend "
                f"{vector_store_backend!r}; expected 'zvec' or 'in_memory'"
            )
        if self._use_mock_embeddings and backend_key == "zvec" and self._vector_store_path is None:
            self._vector_store_backend_requested = "in_memory"

        if embedding_config and not use_mock_embeddings:
            from novel_forge.gateway.embedding import EmbeddingService

            self._embedding_service = EmbeddingService.create_from_config(embedding_config)
            model = embedding_config.get("model", "")
            self._mock_dimensions = (
                int(embedding_config["dimensions"])
                if embedding_config.get("dimensions")
                else EmbeddingService.default_dimensions_for_model(model)
            )
        elif embedding_config:
            # mock mode but config provided — still align dimensions
            from novel_forge.gateway.embedding import EmbeddingService

            model = embedding_config.get("model", "")
            self._mock_dimensions = (
                int(embedding_config["dimensions"])
                if embedding_config.get("dimensions")
                else EmbeddingService.default_dimensions_for_model(model)
            )

        # Real embedding mode intentionally defers vector-store creation until
        # the first actual vector returns.  That vector length is the source of
        # truth, so providers that ignore or change dimension defaults cannot
        # create a mismatched zvec collection.
        self._vector_store: VectorStore | None = None
        if self._use_mock_embeddings or not self._embedding_service:
            self._ensure_vector_store_for_dimension(
                self._mock_dimensions,
                source="mock_or_unconfigured",
            )
        self._index: dict[str, EpisodicIndex] = {}  # signature -> index
        self._chapter_events: dict[int, list[str]] = {}  # chapter -> [signatures]

        # Outline phase storage
        self._outline_index: dict[str, OutlinePlotPoint] = {}  # signature -> outline point
        self._chapter_outlines: dict[int, list[str]] = {}  # chapter -> [signatures]
        self._relationships: dict[
            tuple[str, str], dict[str, RelationshipChange]
        ] = {}  # char_pair -> {chapter -> change}
        self._theme_tracker: dict[str, list[int]] = {}  # theme -> [chapters]
        self._unresolved_questions: list[str] = []

        self._critique_index: dict[str, CritiqueIndex] = {}
        self._chapter_critiques: dict[int, list[str]] = {}

        self._volume_audit_index: dict[str, VolumeAuditIndex] = {}

        # Volume awareness
        self._active_volume: int = 0

    @property
    def vector_store_backend(self) -> str:
        """Actual vector store backend currently in use."""
        if self._vector_store is None:
            return "uninitialized"
        return str(getattr(self._vector_store, "backend_name", "unknown"))

    @property
    def vector_store_path(self) -> str:
        """Project-scoped vector store path, when configured."""
        return str(self._vector_store_path or "")

    async def index_chapter_outcome(
        self,
        chapter_number: int,
        event_summary: str,
        full_text: str = "",
    ) -> list[str]:
        """Index chapter outcomes with simple parameters.

        This method provides a simpler interface than index_chapter(),
        allowing direct indexing without constructing a full ChapterOutcome object.

        Args:
            chapter_number: Chapter number
            event_summary: Summary of the chapter's events
            full_text: Optional full chapter text

        Returns:
            List of indexed entry signatures
        """

        @dataclass
        class SimpleOutcome:
            chapter_summary: str
            source_chapter: int
            text: str
            character_updates: dict[str, Any] = field(default_factory=dict)
            new_events: list[Any] = field(default_factory=list)
            creative_report: Any = None
            alignment_report: Any = None
            plan: Any = None

        outcome = SimpleOutcome(
            chapter_summary=event_summary,
            source_chapter=chapter_number,
            text=full_text,
            character_updates={},
            new_events=[],
            creative_report=None,
            alignment_report=None,
        )

        return await self.index_chapter(outcome)  # type: ignore[arg-type]

    async def index_chapter(self, outcome: ChapterOutcome) -> list[str]:
        """Index a chapter's events for future retrieval.

        Args:
            outcome: Chapter outcome with canon delta and creative report

        Returns:
            List of indexed entry signatures
        """
        entries: list[EpisodicIndex] = []
        # ── P0-R1 defensive guard: tolerate legacy SimpleNamespaces missing
        # fields. The pre-fix canon_delta path in
        # memory/integration.py:640-649 produced namespaces without
        # chapter_summary / source_chapter / character_updates / new_events,
        # raising AttributeError below and silently dropping the chapter's
        # episodic index.
        text = getattr(outcome, "text", "") or ""
        has_summary_attr = hasattr(outcome, "chapter_summary")
        if has_summary_attr:
            raw_summary = getattr(outcome, "chapter_summary", "") or ""
            # chapter_summary attribute present but empty → fall back to text
            # prefix so the chapter is still retrievable from semantic search.
            chapter_summary = raw_summary if raw_summary else (text[:200] if text else "")
        else:
            # No chapter_summary attribute at all (legacy shape): no
            # chapter-level entry, but don't crash.
            chapter_summary = ""

        emotion_codes, importance_flags = self._detect_emotion_flags(text or chapter_summary)

        # Index chapter-level summary
        if chapter_summary:
            entry = EpisodicIndex(
                chapter_number=getattr(outcome, "source_chapter", 0) or 0,
                scene_index=0,
                event_summary=chapter_summary,
                full_text=text,
                characters=list((getattr(outcome, "character_updates", {}) or {}).keys()),
                event_types=self._extract_event_types(outcome),
                emotional_tone=self._extract_emotional_tone(outcome),
                metadata={
                    "word_count": count_chapter_words(text),
                    "alignment_score": getattr(
                        getattr(outcome, "alignment_report", None), "alignment_score", 0
                    )
                    if hasattr(outcome, "alignment_report")
                    else 0,
                    "emotion_codes": emotion_codes,
                    "importance_flags": importance_flags,
                },
            )
            entries.append(entry)

        # Index timeline events
        for event in getattr(outcome, "new_events", None) or []:
            entry = EpisodicIndex(
                chapter_number=event.chapter,
                scene_index=0,
                event_summary=event.event,
                characters=list(event.characters_involved),
                locations=[],
                event_types=["timeline_event"],
                timestamp_in_story=getattr(
                    event,
                    "timestamp_in_story",
                    getattr(event, "in_story_time", ""),
                ),
                metadata={"event_chapter": event.chapter},
            )
            entries.append(entry)

        # Index creative report key moments
        creative_report = getattr(outcome, "creative_report", None)
        if creative_report:
            key_moments = getattr(creative_report, "key_moments", None) or []
            for i, moment in enumerate(key_moments):
                entry = EpisodicIndex(
                    chapter_number=outcome.source_chapter,
                    scene_index=i + 1,
                    event_summary=moment,
                    event_types=["key_moment"],
                    metadata={"source": "creative_report"},
                )
                entries.append(entry)

        # Index scene-level entries from plan.scene_intents
        plan = getattr(outcome, "plan", None)
        if plan is not None:
            scene_intents = getattr(plan, "scene_intents", None) or []
            for i, scene_intent in enumerate(scene_intents):
                scene_summary = getattr(scene_intent, "summary", "")
                if not scene_summary:
                    continue
                characters: list[str] = []
                required_chars = getattr(scene_intent, "required_characters", None) or []
                characters.extend(required_chars)
                motivations = getattr(scene_intent, "character_motivations", None) or []
                for m in motivations:
                    char_name = getattr(m, "character", "")
                    if char_name and char_name not in characters:
                        characters.append(char_name)
                entry = EpisodicIndex(
                    chapter_number=outcome.source_chapter,
                    scene_index=i + 1,
                    event_summary=scene_summary,
                    characters=characters,
                    locations=[loc] if (loc := getattr(scene_intent, "location", "")) else [],
                    event_types=["scene_intent"],
                    timestamp_in_story=getattr(scene_intent, "time_marker", ""),
                    emotional_tone=getattr(scene_intent, "emotional_beat", ""),
                    metadata={
                        "source": "plan_scene_intent",
                        "scene_id": getattr(scene_intent, "scene_id", ""),
                        "purpose": getattr(scene_intent, "purpose", ""),
                        "conflict": getattr(scene_intent, "conflict", ""),
                        "exit_target_state": getattr(scene_intent, "exit_target_state", ""),
                        "relationship_dynamics": getattr(scene_intent, "relationship_dynamics", ""),
                    },
                )
                entries.append(entry)

        return await self._add_index_batch(entries)

    async def index_critique(self, chapter_number: int, report: CritiqueReport) -> list[str]:
        """Index critical/high severity critique issues for persistence.

        Args:
            chapter_number: Chapter number being critiqued
            report: CritiqueReport from CriticAgent

        Returns:
            List of indexed critique entry signatures
        """
        entries: list[CritiqueIndex] = []
        persistable = [i for i in report.issues if i.severity in ("critical", "high")]

        for issue in persistable:
            entry = CritiqueIndex(
                chapter_number=chapter_number,
                issue_type=issue.issue_type,
                severity=issue.severity,
                summary=issue.summary,
                evidence=issue.evidence,
                suggested_fix=issue.suggested_fix,
                affected_chapters=issue.affected_chapters,
                metadata={
                    "confidence": issue.confidence,
                    "report_chapter": report.chapter_number,
                },
            )
            entries.append(entry)

        return await self._add_critique_entries_batch(entries)

    def record_repair_result(
        self,
        critique_signature: str,
        *,
        chapter: int,
        round_num: int,
        strategy: str,
        result: str,
        new_issues: list[str] | None = None,
        score_before: float = 0.0,
        score_after: float = 0.0,
    ) -> bool:
        """Record a repair attempt on an existing critique entry.

        This method activates the previously dead repair_attempts tracking
        in CritiqueIndex. After recording, it automatically updates
        failure_pattern and lesson_learned fields.

        Args:
            critique_signature: MD5 signature of the critique entry to update
            chapter: Chapter number where repair was attempted
            round_num: Which repair round this was (1-based)
            strategy: Repair strategy used (patch/fulltext/rewrite)
            result: Outcome of repair (success/regression/no_op)
            new_issues: List of new issue types introduced by this repair
            score_before: Continuity/causal score before repair
            score_after: Continuity/causal score after repair

        Returns:
            True if the repair result was recorded, False if entry not found
        """
        from datetime import datetime, timezone

        entry = self._critique_index.get(critique_signature)
        if entry is None:
            _log.warning(
                "record_repair_result: critique entry not found | signature=%s",
                critique_signature,
            )
            return False

        entry.repair_attempts.append(
            {
                "chapter": chapter,
                "round": round_num,
                "strategy": strategy,
                "result": result,
                "new_issues_introduced": new_issues or [],
                "score_before": score_before,
                "score_after": score_after,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        # Automatically update failure pattern and lesson learned
        entry.failure_pattern = summarize_failure_pattern(entry)
        entry.lesson_learned = generate_lesson_learned(entry)

        _log.info(
            "repair_result_recorded | chapter=%d | round=%d | strategy=%s | "
            "result=%s | signature=%s",
            chapter,
            round_num,
            strategy,
            result,
            critique_signature[:8],
        )

        return True

    def prune_critique_index(
        self,
        *,
        current_chapter: int,
        max_entries: int = 100,
        distance_threshold: int = 20,
        max_per_chapter: int = 10,
    ) -> dict[str, int]:
        """Prune the critique index to prevent unbounded growth.

        Applies multiple strategies to keep the index manageable:
        1. Hard cap: Remove oldest entries if total exceeds max_entries
        2. Distance-based: Remove entries far from current chapter (no lesson)
        3. Per-chapter cap: Limit entries per chapter to max_per_chapter

        Args:
            current_chapter: The current chapter being generated
            max_entries: Maximum total entries allowed (default: 100)
            distance_threshold: Remove entries beyond this chapter distance
                               if they have no lesson_learned (default: 20)
            max_per_chapter: Maximum entries per chapter (default: 10)

        Returns:
            Dictionary with pruning statistics:
            - "removed_distance": entries removed due to distance
            - "removed_per_chapter": entries removed due to per-chapter cap
            - "removed_hard_cap": entries removed due to hard cap
            - "remaining": total entries after pruning
        """
        stats = {
            "removed_distance": 0,
            "removed_per_chapter": 0,
            "removed_hard_cap": 0,
            "remaining": 0,
        }

        if not self._critique_index:
            return stats

        # Phase 1: Remove entries beyond distance threshold (no lesson)
        to_remove: list[str] = []
        for sig, entry in self._critique_index.items():
            distance = abs(entry.chapter_number - current_chapter)
            if distance > distance_threshold and not entry.lesson_learned:
                to_remove.append(sig)

        for sig in to_remove:
            self._remove_critique_entry(sig)
            stats["removed_distance"] += 1

        # Phase 2: Enforce per-chapter cap
        chapter_entries: dict[int, list[tuple[str, CritiqueIndex]]] = {}
        for sig, entry in self._critique_index.items():
            ch = entry.chapter_number
            if ch not in chapter_entries:
                chapter_entries[ch] = []
            chapter_entries[ch].append((sig, entry))

        to_remove.clear()
        for _ch, entries in chapter_entries.items():
            if len(entries) > max_per_chapter:
                # Sort by: lesson_learned (keep), severity (keep critical), repair_attempts (keep)
                def entry_priority(e_tuple: tuple[str, CritiqueIndex]) -> tuple[bool, bool, int]:
                    sig, entry = e_tuple
                    has_lesson = bool(entry.lesson_learned)
                    is_critical = entry.severity == "critical"
                    has_repair_history = len(entry.repair_attempts) > 0
                    return (has_lesson, is_critical, has_repair_history)

                entries.sort(key=entry_priority, reverse=True)
                excess = entries[max_per_chapter:]
                for sig, _ in excess:
                    to_remove.append(sig)

        for sig in to_remove:
            self._remove_critique_entry(sig)
            stats["removed_per_chapter"] += 1

        # Phase 3: Enforce hard cap (remove oldest entries without lessons)
        while len(self._critique_index) > max_entries:
            # Find entry to remove: prefer removing old entries without lessons
            candidates = [
                (sig, entry)
                for sig, entry in self._critique_index.items()
                if not entry.lesson_learned
            ]

            if not candidates:
                # All entries have lessons, remove oldest by chapter number
                candidates = list(self._critique_index.items())

            # Sort by chapter number (oldest first), then by repair_attempts (fewer first)
            candidates.sort(key=lambda x: (x[1].chapter_number, len(x[1].repair_attempts)))

            oldest_sig = candidates[0][0]
            self._remove_critique_entry(oldest_sig)
            stats["removed_hard_cap"] += 1

        stats["remaining"] = len(self._critique_index)

        if stats["removed_distance"] or stats["removed_per_chapter"] or stats["removed_hard_cap"]:
            _log.info(
                "critique_index_pruned | removed_distance=%d | removed_per_chapter=%d | "
                "removed_hard_cap=%d | remaining=%d | current_chapter=%d",
                stats["removed_distance"],
                stats["removed_per_chapter"],
                stats["removed_hard_cap"],
                stats["remaining"],
                current_chapter,
            )

        return stats

    def _episodic_relevance_proxy(self, entry: EpisodicIndex) -> float:
        """Deterministic O(1) proxy for entry relevance without LLM or vector scan.

        Heuristic combining event type diversity, character count, keyword density,
        and text length. Higher score = more narratively significant entry.
        """
        return (
            len(entry.event_types) * 3.0
            + len(entry.characters) * 1.5
            + len(entry.keywords) * 2.0
            + min(len(entry.full_text) / 1000.0, 5.0)
        )

    def prune_episodic_by_recency(
        self,
        *,
        decay_threshold: int | None = None,
        keep_recent: int = 500,
        keep_relevant: int = 200,
    ) -> dict[str, int]:
        """Prune _index when it exceeds decay_threshold.

        Strategy:
        1. Trigger only if len(self._index) > decay_threshold
        2. Build "keep" set:
           a) Top keep_recent by chapter_number (descending, then scene_index desc)
           b) Top keep_relevant by composite_relevance_score (descending)
        3. Remove everything not in keep set
        4. Return stats: {"removed", "remaining", "triggered", "kept_recent", "kept_relevant"}
        """
        settings = getattr(self, "settings", None)
        threshold = decay_threshold
        if threshold is None:
            threshold = (
                int(getattr(settings, "memory_episodic_decay_threshold", 1000))
                if settings
                else 1000
            )

        stats: dict[str, int] = {
            "removed": 0,
            "remaining": len(self._index),
            "triggered": 0,
            "kept_recent": 0,
            "kept_relevant": 0,
        }

        if len(self._index) <= threshold:
            return stats

        stats["triggered"] = 1

        by_recency = sorted(
            self._index.items(),
            key=lambda item: (item[1].chapter_number, item[1].scene_index),
            reverse=True,
        )
        keep_recent_sigs: set[str] = {sig for sig, _ in by_recency[:keep_recent]}
        stats["kept_recent"] = len(keep_recent_sigs)

        by_relevance = sorted(
            self._index.items(),
            key=lambda item: self._episodic_relevance_proxy(item[1]),
            reverse=True,
        )
        keep_relevant_sigs: set[str] = {sig for sig, _ in by_relevance[:keep_relevant]}
        stats["kept_relevant"] = len(keep_relevant_sigs)

        keep_sigs = keep_recent_sigs | keep_relevant_sigs

        to_remove = [sig for sig in self._index if sig not in keep_sigs]
        for sig in to_remove:
            entry = self._index.pop(sig)
            ch_events = self._chapter_events.get(entry.chapter_number, [])
            if sig in ch_events:
                ch_events.remove(sig)
                if not ch_events:
                    self._chapter_events.pop(entry.chapter_number, None)

        stats["removed"] = len(to_remove)
        stats["remaining"] = len(self._index)

        if to_remove:
            _log.info(
                "episodic_index_pruned | removed=%d | remaining=%d | "
                "threshold=%d | kept_recent=%d | kept_relevant=%d",
                stats["removed"],
                stats["remaining"],
                threshold,
                stats["kept_recent"],
                stats["kept_relevant"],
            )

        return stats

    def on_volume_end(
        self,
        volume_number: int,
        end_chapter: int,
        *,
        critique_prune_kwargs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Notify EpisodicMemory that a volume has ended, trigger volume-scoped pruning.

        Args:
            volume_number: The volume number that just ended
            end_chapter: The chapter number of the volume's last chapter
            critique_prune_kwargs: Optional kwargs forwarded to prune_critique_index

        Returns:
            Dictionary with pruning statistics including volume info
        """
        self._active_volume = volume_number

        # Prune critique index using end_chapter as reference point
        critique_stats = self.prune_critique_index(
            current_chapter=end_chapter,
            **(critique_prune_kwargs or {}),
        )

        stats = {
            "volume_number": volume_number,
            "end_chapter": end_chapter,
            "critique_pruning": critique_stats,
        }

        _log.info(
            "volume_end_processed | volume=%d | end_chapter=%d | critique_remaining=%d",
            volume_number,
            end_chapter,
            critique_stats.get("remaining", 0),
        )

        return stats

    def prune_by_volume(
        self,
        volume_number: int,
        *,
        keep_recent_volumes: int = 2,
        max_chapter_to_keep: int | None = None,
        prune_critiques: bool = True,
    ) -> dict[str, int]:
        """Prune episodic index entries older than the specified volume.

        Keeps entries from the current volume and the N most recent volumes.
        This is a more aggressive pruning than prune_critique_index.

        Args:
            volume_number: Current volume number (entries from older volumes will be pruned)
            keep_recent_volumes: Number of recent volumes to keep (default: 2)
            max_chapter_to_keep: Remove entries with chapter numbers below this threshold.
                EpisodicMemory has no outline access, so callers must provide the boundary.
            prune_critiques: Whether to prune critique entries below the same threshold.

        Returns:
            Dictionary with pruning statistics:
            - "removed_entries": episodic entries removed
            - "removed_outlines": outline entries removed
            - "removed_critiques": critique entries removed
            - "remaining_entries": remaining episodic entries
            - "remaining_outlines": remaining outline entries
            - "remaining_critiques": remaining critique entries
        """
        stats = {
            "removed_entries": 0,
            "removed_outlines": 0,
            "removed_critiques": 0,
            "remaining_entries": len(self._index),
            "remaining_outlines": len(self._outline_index),
            "remaining_critiques": len(self._critique_index),
            "max_chapter_to_keep": int(max_chapter_to_keep or 0),
            "skipped": 0,
        }

        if max_chapter_to_keep is None:
            stats["skipped"] = 1
            _log.debug(
                "prune_by_volume skipped | volume=%d | keep_recent=%d | no chapter threshold",
                volume_number,
                keep_recent_volumes,
            )
            return stats

        threshold = max(1, int(max_chapter_to_keep))

        for chapter in [ch for ch in self._chapter_events if ch < threshold]:
            for sig in list(self._chapter_events.get(chapter, [])):
                if sig in self._index:
                    del self._index[sig]
                    if self._vector_store is not None:
                        self._vector_store.remove(sig)
                    stats["removed_entries"] += 1
            self._chapter_events.pop(chapter, None)

        for chapter in [ch for ch in self._chapter_outlines if ch < threshold]:
            for sig in list(self._chapter_outlines.get(chapter, [])):
                if sig in self._outline_index:
                    del self._outline_index[sig]
                    if self._vector_store is not None:
                        self._vector_store.remove(sig)
                    stats["removed_outlines"] += 1
            self._chapter_outlines.pop(chapter, None)

        if prune_critiques:
            for chapter in [ch for ch in self._chapter_critiques if ch < threshold]:
                for sig in list(self._chapter_critiques.get(chapter, [])):
                    if sig in self._critique_index:
                        self._remove_critique_entry(sig)
                        stats["removed_critiques"] += 1
                self._chapter_critiques.pop(chapter, None)

        stats["remaining_entries"] = len(self._index)
        stats["remaining_outlines"] = len(self._outline_index)
        stats["remaining_critiques"] = len(self._critique_index)

        _log.info(
            "volume_memory_pruned | volume=%d | keep_recent=%d | threshold=%d | "
            "episodic_removed=%d | outline_removed=%d | critique_removed=%d | "
            "episodic_remaining=%d | outline_remaining=%d | critique_remaining=%d",
            volume_number,
            keep_recent_volumes,
            threshold,
            stats["removed_entries"],
            stats["removed_outlines"],
            stats["removed_critiques"],
            stats["remaining_entries"],
            stats["remaining_outlines"],
            stats["remaining_critiques"],
        )

        return stats

    def _remove_critique_entry(self, signature: str) -> None:
        """Remove a critique entry from the index and vector store.

        Args:
            signature: The MD5 signature of the entry to remove
        """
        entry = self._critique_index.get(signature)
        if entry is None:
            return

        # Remove from chapter tracking
        ch = entry.chapter_number
        if ch in self._chapter_critiques:
            if signature in self._chapter_critiques[ch]:
                self._chapter_critiques[ch].remove(signature)
            if not self._chapter_critiques[ch]:
                del self._chapter_critiques[ch]

        if self._vector_store is not None:
            self._vector_store.remove(signature)

        # Remove from index
        del self._critique_index[signature]

    async def _add_critique_entry(self, entry: CritiqueIndex) -> str:
        """Add a critique entry to the index."""
        signatures = await self._add_critique_entries_batch([entry])
        return signatures[0] if signatures else entry.signature()

    async def _add_critique_entries_batch(self, entries: list[CritiqueIndex]) -> list[str]:
        """Add critique entries to the index, batching embedding generation."""
        pending: list[tuple[str, CritiqueIndex]] = []
        signatures: list[str] = []
        for entry in entries:
            sig = entry.signature()
            signatures.append(sig)
            if sig not in self._critique_index:
                pending.append((sig, entry))

        if not pending:
            return signatures

        embeddings = await self._generate_embeddings_batch([entry.summary for _, entry in pending])
        vector_store = self._ensure_vector_store_for_vector(
            embeddings[0],
            source="critique_index_batch",
        )

        vector_items: list[tuple[str, list[float], dict[str, Any]]] = []
        for (sig, entry), embedding in zip(pending, embeddings, strict=True):
            entry.embedding = embedding
            self._critique_index[sig] = entry
            vector_items.append(
                (
                    sig,
                    entry.embedding,
                    {
                        "chapter": entry.chapter_number,
                        "scene_index": entry.scene_index,
                        "issue_type": entry.issue_type,
                        "severity": entry.severity,
                    },
                )
            )
            if entry.chapter_number not in self._chapter_critiques:
                self._chapter_critiques[entry.chapter_number] = []
            self._chapter_critiques[entry.chapter_number].append(sig)

        self._add_vectors(vector_store, vector_items)
        return signatures

    async def search_similar_critiques(
        self,
        *,
        issue_type: str,
        summary: str,
        current_chapter: int,
        top_k: int = 5,
        min_relevance: float = 0.55,
        chapter_range: tuple[int, int] | None = None,
    ) -> list[dict[str, Any]]:
        """Search for similar historical critique entries.

        Actively queries the critique index to find past issues similar to
        the current problem. Returns structured results including repair
        history, lessons learned, and failure patterns.

        Args:
            issue_type: Type of the current issue (e.g., "continuity_error")
            summary: Summary of the current issue
            current_chapter: Current chapter number (to exclude and calculate distance)
            top_k: Maximum number of results to return
            min_relevance: Minimum relevance score threshold (default 0.55)
            chapter_range: Optional (start, end) chapter range filter

        Returns:
            List of structured critique results with repair history
        """

        if not self._critique_index:
            return []

        # Build search query combining issue type and summary
        query_text = f"[{issue_type}] {summary}"

        try:
            # Generate embedding for the query
            query_vector = await self._generate_embedding(query_text)
            vector_store = self._ensure_vector_store_for_vector(
                query_vector,
                source="critique_similarity_search",
            )

            # Build filter
            filter_dict: dict[str, Any] = {}
            if chapter_range:
                filter_dict["chapter"] = {"$gte": chapter_range[0], "$lte": chapter_range[1]}

            # Search vector store with zvec 0.5 hybrid dense + FTS retrieval.
            matches = vector_store.hybrid_search(
                query_vector,
                query_text,
                top_k=top_k * 2,  # Request more to allow filtering
                filter=filter_dict,
            )

            # Convert to structured results
            results: list[dict[str, Any]] = []
            for sig, score, _metadata in matches:
                # Skip if below relevance threshold
                if score < min_relevance:
                    continue

                entry = self._critique_index.get(sig)
                if entry is None:
                    continue

                # Exclude current chapter entries
                if entry.chapter_number == current_chapter:
                    continue

                distance = abs(entry.chapter_number - current_chapter)

                results.append(
                    {
                        "signature": sig,
                        "chapter_number": entry.chapter_number,
                        "issue_type": entry.issue_type,
                        "severity": entry.severity,
                        "summary": entry.summary,
                        "evidence": entry.evidence,
                        "suggested_fix": entry.suggested_fix,
                        "relevance_score": round(score, 3),
                        "distance": distance,
                        "repair_attempts": entry.repair_attempts,
                        "failure_pattern": entry.failure_pattern,
                        "lesson_learned": entry.lesson_learned,
                        "metadata": entry.metadata,
                    }
                )

            # Sort by relevance score descending
            results.sort(key=lambda r: r["relevance_score"], reverse=True)

            return results[:top_k]

        except Exception as e:
            _log.warning(
                "search_similar_critiques failed | issue_type=%s | error=%s",
                issue_type,
                str(e),
            )
            return []

    # === Volume Audit Interface (Implementation in later tasks) ===

    async def index_volume_audit(self, report: VolumeAuditReport) -> str:
        """Index a volume audit report for similarity search.

        Creates a VolumeAuditIndex from the report fields and stores it
        in the volume audit index and vector store. Idempotent: if the
        same report is indexed twice, the existing signature is returned.

        Args:
            report: The VolumeAuditReport to index.

        Returns:
            The MD5 signature of the indexed entry.
        """
        entry = VolumeAuditIndex(
            volume_number=report.volume_number,
            volume_title=report.volume_title,
            chapter_range=report.chapter_range,
            volume_summary=report.volume_summary,
            consistency_score=report.consistency_score,
            consistency_issues=[str(issue) for issue in report.consistency_issues],
            carry_over_characters=report.carry_over_characters,
            carry_over_items=report.carry_over_items,
            carry_over_world_fact_keys=report.carry_over_world_fact_keys,
            carry_over_foreshadowing_ids=report.carry_over_foreshadowing_ids,
            next_volume_focus=report.next_volume_focus,
        )
        return await self._add_volume_audit_entry(entry)

    async def _add_volume_audit_entry(self, entry: VolumeAuditIndex) -> str:
        """Add a volume audit entry to the index and vector store.

        Args:
            entry: The VolumeAuditIndex entry to add.

        Returns:
            The MD5 signature of the entry.
        """
        sig = entry.signature()
        if sig in self._volume_audit_index:
            return sig

        entry.embedding = await self._generate_embedding(entry.volume_summary)
        vector_store = self._ensure_vector_store_for_vector(
            entry.embedding,
            source="volume_audit_index",
        )

        self._volume_audit_index[sig] = entry

        vector_store.add(
            sig,
            entry.embedding,
            {
                "content": entry.volume_summary,
                "volume_number": entry.volume_number,
                "consistency_score": entry.consistency_score,
                "chapter_range": entry.chapter_range,
            },
        )

        return sig

    async def search_similar_volumes(
        self,
        query_text: str,
        top_k: int = 3,
        min_relevance: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Search for similar historical volume audits.

        Queries the volume audit index to find past volumes similar to the
        given query. Returns structured results including consistency scores,
        carry-over elements, and chapter ranges.

        Args:
            query_text: Natural language query to search against volume summaries
            top_k: Maximum number of results to return (default 3)
            min_relevance: Minimum relevance score threshold (default 0.5)

        Returns:
            List of structured volume results sorted by relevance
        """
        if not self._volume_audit_index:
            return []

        try:
            # Generate embedding for the query
            query_vector = await self._generate_embedding(query_text)
            vector_store = self._ensure_vector_store_for_vector(
                query_vector,
                source="volume_similarity_search",
            )

            # Search vector store with zvec 0.5 hybrid dense + FTS retrieval.
            matches = vector_store.hybrid_search(
                query_vector,
                query_text,
                top_k=top_k * 2,  # Request more to allow filtering
            )

            # Convert to structured results
            results: list[dict[str, Any]] = []
            for sig, score, _metadata in matches:
                # Skip if below relevance threshold
                if score < min_relevance:
                    continue

                entry = self._volume_audit_index.get(sig)
                if entry is None:
                    continue

                results.append(
                    {
                        "volume_number": entry.volume_number,
                        "similarity": round(score, 3),
                        "volume_summary": entry.volume_summary,
                        "consistency_score": entry.consistency_score,
                        "chapter_range": entry.chapter_range,
                        "carry_over_characters": entry.carry_over_characters,
                        "carry_over_items": entry.carry_over_items,
                        "relevance_score": round(score, 3),
                        **self._volume_entry_vector_metadata(entry),
                    }
                )

            # Sort by relevance score descending
            results.sort(key=lambda r: r["relevance_score"], reverse=True)

            return results[:top_k]

        except Exception as e:
            _log.warning(
                "search_similar_volumes failed | query=%s | error=%s",
                query_text[:50],
                str(e),
            )
            return []

    @staticmethod
    def _volume_entry_vector_metadata(entry: VolumeAuditIndex) -> dict[str, Any]:
        return {
            "content": " ".join(
                part
                for part in (
                    entry.volume_title,
                    entry.volume_summary,
                    " ".join(entry.consistency_issues),
                    " ".join(entry.next_volume_focus.split()),
                )
                if part
            ),
            "volume_number": entry.volume_number,
            "consistency_score": entry.consistency_score,
            "chapter_range": entry.chapter_range,
        }

    async def search_by_semantic(
        self,
        query: str,
        chapter_range: tuple[int, int] | None = None,
        top_k: int = 5,
        min_relevance: float = 0.5,
        scene_index: int | None = None,
        issue_type: str | None = None,
        characters: list[str] | None = None,
    ) -> list[EpisodicResult]:
        """Search for events by semantic similarity.

        Args:
            query: Natural language query
            chapter_range: Optional (start, end) chapter range filter
            top_k: Maximum results to return
            min_relevance: Minimum relevance score threshold
            scene_index: Optional scene index filter (0 = chapter-level, >0 = specific scene)
            characters: Optional character filter — if provided, only return entries
                where any entry character matches any name in this list

        Returns:
            List of episodic results sorted by relevance
        """
        # Generate query embedding (await async method)
        query_vector = await self._generate_embedding(query)
        vector_store = self._ensure_vector_store_for_vector(
            query_vector,
            source="semantic_query",
        )

        # Build filter
        filter: dict[str, Any] = {}
        if chapter_range:
            filter["chapter"] = {"$gte": chapter_range[0], "$lte": chapter_range[1]}
        if scene_index is not None:
            filter["scene_index"] = scene_index
        if issue_type:
            filter["issue_type"] = issue_type

        # Search vector store with zvec 0.5 hybrid dense + FTS retrieval.
        matches = vector_store.hybrid_search(
            query_vector,
            query,
            top_k=top_k * 2,
            filter=filter,
        )

        # Convert to results
        results: list[EpisodicResult] = []
        for sig, score, metadata in matches:
            if score < min_relevance:
                continue
            entry = self._index.get(sig)
            if entry is None:
                continue
            if characters and not any(c in entry.characters for c in characters):
                continue

            result = EpisodicResult(
                chapter_number=entry.chapter_number,
                event_summary=entry.event_summary,
                scene_index=entry.scene_index,
                relevance_score=round(score, 3),
                text_snippet=entry.event_summary[:200],
                characters_involved=entry.characters[:5],
                timestamp_in_story=entry.timestamp_in_story,
                metadata=metadata,
            )
            results.append(result)

        # Sort by relevance
        results.sort(key=lambda r: r.relevance_score, reverse=True)

        return results[:top_k]

    def search_by_temporal(
        self,
        start_chapter: int,
        end_chapter: int,
        event_type: str | None = None,
        characters: list[str] | None = None,
    ) -> list[EpisodicResult]:
        """Search for events within a chapter range.

        Args:
            start_chapter: Start chapter (inclusive)
            end_chapter: End chapter (inclusive)
            event_type: Optional event type filter
            characters: Optional character filter

        Returns:
            List of episodic results
        """
        results: list[EpisodicResult] = []

        for ch in range(start_chapter, end_chapter + 1):
            if ch not in self._chapter_events:
                continue
            for sig in self._chapter_events[ch]:
                entry = self._index.get(sig)
                if entry is None:
                    continue

                # Apply filters
                if event_type and event_type not in entry.event_types:
                    continue
                if characters and not any(c in entry.characters for c in characters):
                    continue

                result = EpisodicResult(
                    chapter_number=entry.chapter_number,
                    event_summary=entry.event_summary,
                    scene_index=entry.scene_index,
                    relevance_score=1.0,
                    text_snippet=entry.event_summary[:200],
                    characters_involved=entry.characters[:5],
                    timestamp_in_story=entry.timestamp_in_story,
                    metadata={
                        "event_types": entry.event_types,
                        "emotion_codes": list(entry.metadata.get("emotion_codes", []) or []),
                        "importance_flags": list(entry.metadata.get("importance_flags", []) or []),
                    },
                )
                results.append(result)

        return results

    async def search_hybrid(
        self,
        query: SearchQuery,
    ) -> MemoryRetrievalResult:
        """Hybrid search combining semantic + temporal + filters.

        Args:
            query: Structured search query

        Returns:
            Unified retrieval result
        """
        start_time = time.monotonic()

        # Semantic search (await async method)
        semantic_results = await self.search_by_semantic(
            query.query_text,
            chapter_range=query.chapter_range,
            top_k=query.top_k,
            min_relevance=query.min_relevance,
        )

        # If we have enough high-relevance results, return
        high_relevance = [r for r in semantic_results if r.relevance_score >= 0.8]
        if len(high_relevance) >= query.top_k:
            return MemoryRetrievalResult(
                results=high_relevance[: query.top_k],
                query=query,
                retrieval_method="semantic",
                total_candidates=len(semantic_results),
                retrieval_time_ms=(time.monotonic() - start_time) * 1000,
            )

        # Otherwise, combine with temporal search
        if query.chapter_range:
            temporal_results = self.search_by_temporal(
                start_chapter=query.chapter_range[0],
                end_chapter=query.chapter_range[1],
                event_type=query.event_types[0] if query.event_types else None,
                characters=query.characters,
            )
            # Merge and deduplicate
            seen = {r.event_summary for r in semantic_results}
            for r in temporal_results:
                if r.event_summary not in seen:
                    semantic_results.append(r)
                    seen.add(r.event_summary)

        semantic_results.sort(key=lambda r: r.relevance_score, reverse=True)

        return MemoryRetrievalResult(
            results=semantic_results[: query.top_k],
            query=query,
            retrieval_method="hybrid",
            total_candidates=len(semantic_results),
            retrieval_time_ms=(time.monotonic() - start_time) * 1000,
        )

    async def get_recent_events(
        self,
        current_chapter: int,
        lookback: int = 3,
        max_events: int = 20,
    ) -> list[EpisodicResult]:
        """Get recent events from the last N chapters.

        This is a convenience method that replaces CanonRetriever's timeline logic.
        """
        start_chapter = max(1, current_chapter - lookback)
        results = self.search_by_temporal(start_chapter, current_chapter - 1)
        results.sort(key=lambda r: r.chapter_number, reverse=True)
        return results[:max_events]

    async def get_recent_semantic_facts(
        self,
        current_chapter: int,
        lookback: int = 10,
        top_k: int = 8,
        event_types: list[str] | None = None,
    ) -> list[EpisodicResult]:
        """Get semantically relevant facts from recent chapters.

        Combines semantic search with temporal filtering for compression enrichment.
        Returns results sorted by relevance score (highest first).

        Args:
            current_chapter: The current chapter number (events from before this are searched)
            lookback: How many chapters back to search (default 10)
            top_k: Maximum number of results to return (default 8)
            event_types: Optional filter for specific event types

        Returns:
            List of episodic results sorted by relevance score (highest first)
        """
        if current_chapter <= 1:
            return []

        start_chapter = max(1, current_chapter - lookback)
        # Use empty query to get all recent events ranked by recency/relevance
        results = await self.search_by_semantic(
            query="",
            chapter_range=(start_chapter, current_chapter - 1),
            top_k=top_k * 2,
        )

        # Filter by event_types if provided
        if event_types:
            results = [
                r
                for r in results
                if any(et in r.metadata.get("event_types", []) for et in event_types)
            ]

        return results[:top_k]

    async def _add_index(self, entry: EpisodicIndex) -> str:
        """Add an entry to the index."""
        signatures = await self._add_index_batch([entry])
        return signatures[0] if signatures else entry.signature()

    async def _add_index_batch(self, entries: list[EpisodicIndex]) -> list[str]:
        """Add entries to the index, batching embedding generation."""
        pending: list[tuple[str, EpisodicIndex]] = []
        signatures: list[str] = []
        for entry in entries:
            sig = entry.signature()
            signatures.append(sig)
            if sig not in self._index:
                pending.append((sig, entry))

        if not pending:
            return signatures

        embeddings = await self._generate_embeddings_batch(
            [entry.event_summary for _, entry in pending]
        )
        vector_store = self._ensure_vector_store_for_vector(
            embeddings[0],
            source="episodic_index_batch",
        )

        vector_items: list[tuple[str, list[float], dict[str, Any]]] = []
        for (sig, entry), embedding in zip(pending, embeddings, strict=True):
            entry.embedding = embedding
            self._index[sig] = entry
            vector_items.append((sig, entry.embedding, self._entry_vector_metadata(entry)))

            if entry.chapter_number not in self._chapter_events:
                self._chapter_events[entry.chapter_number] = []
            self._chapter_events[entry.chapter_number].append(sig)

        self._add_vectors(vector_store, vector_items)
        return signatures

    @staticmethod
    def _entry_vector_metadata(entry: EpisodicIndex) -> dict[str, Any]:
        return {
            "content": " ".join(
                part
                for part in (
                    entry.event_summary,
                    " ".join(entry.characters),
                    " ".join(entry.locations),
                    " ".join(entry.event_types),
                    " ".join(entry.keywords),
                    entry.emotional_tone,
                    entry.timestamp_in_story,
                )
                if part
            ),
            "chapter": entry.chapter_number,
            "scene_index": entry.scene_index,
            "characters": entry.characters,
            "event_types": entry.event_types,
            "emotion_codes": list(entry.metadata.get("emotion_codes", []) or []),
            "importance_flags": list(entry.metadata.get("importance_flags", []) or []),
        }

    async def _generate_embedding(self, text: str) -> list[float]:
        """Generate one embedding vector for text, using the local cache first."""
        cached = self._embedding_cache_get(text)
        if cached is not None:
            self._ensure_vector_store_for_vector(cached, source="embedding_cache")
            return cached

        if self._use_mock_embeddings:
            embedding = self._mock_embedding(text)
            self._embedding_cache_put(text, embedding)
            self._ensure_vector_store_for_vector(embedding, source="mock_embedding")
            return embedding

        if self._embedding_service is None:
            raise RuntimeError(
                "No embedding service configured. Configure "
                "NOVEL_FORGE_MEMORY_EMBEDDING_PROFILE_ID for real memory retrieval, "
                "or enable mock embeddings only for tests/offline mocks."
            )

        start = time.monotonic()
        result = await self._embedding_service.generate_embedding(text)
        latency_ms = (time.monotonic() - start) * 1000
        embedding = [float(value) for value in result.embedding]
        self._embedding_stats["single_calls"] += 1
        self._embedding_stats["single_items"] += 1
        self._embedding_stats["latency_ms_total"] += int(round(latency_ms))
        self._embedding_stats["last_latency_ms"] = int(round(latency_ms))
        self._embedding_stats["last_dimension"] = len(embedding)
        self._embedding_cache_put(text, embedding)
        self._ensure_vector_store_for_vector(embedding, source="embedding_api")
        return embedding

    async def _generate_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings in input order, preferring batch API calls."""
        if not texts:
            return []

        embeddings: list[list[float] | None] = [None] * len(texts)
        missing_positions: list[int] = []
        missing_texts: list[str] = []
        for idx, text in enumerate(texts):
            cached = self._embedding_cache_get(text)
            if cached is not None:
                embeddings[idx] = cached
                continue
            missing_positions.append(idx)
            missing_texts.append(text)

        if missing_texts:
            if self._use_mock_embeddings:
                generated = [self._mock_embedding(text) for text in missing_texts]
                self._embedding_stats["mock_items"] += len(generated)
            elif self._embedding_service is None:
                raise RuntimeError(
                    "No embedding service configured. Configure "
                    "NOVEL_FORGE_MEMORY_EMBEDDING_PROFILE_ID for real memory retrieval, "
                    "or enable mock embeddings only for tests/offline mocks."
                )
            else:
                generated = await self._generate_missing_embeddings_batch(missing_texts)

            for position, text, embedding in zip(
                missing_positions,
                missing_texts,
                generated,
                strict=True,
            ):
                clean_embedding = [float(value) for value in embedding]
                self._embedding_cache_put(text, clean_embedding)
                embeddings[position] = clean_embedding

        resolved = [embedding for embedding in embeddings if embedding is not None]
        if len(resolved) != len(texts):
            raise RuntimeError("embedding batch generation failed to preserve input order")
        first = resolved[0]
        self._ensure_vector_store_for_vector(first, source="embedding_batch")
        return resolved

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Expose the configured embedding pipeline to the evidence index."""
        return await self._generate_embeddings_batch(texts)

    @property
    def embedding_signature(self) -> str:
        """Stable identity used by dependent indexes to detect stale vectors."""
        provider, model = self._embedding_identity()
        dimension = int(
            self._vector_dimension or self._requested_embedding_dimensions() or self._mock_dimensions
        )
        payload = f"{provider}:{model}:{dimension}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def _generate_missing_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate cache misses in provider-safe chunks with per-chunk fallback."""
        if self._embedding_service is None:
            raise RuntimeError("embedding service is not configured")

        batch_method = getattr(self._embedding_service, "generate_batch", None)
        if callable(batch_method) and len(texts) > 1:
            raw_limit = getattr(self._embedding_service, "max_batch_size", len(texts))
            try:
                batch_limit = max(1, int(raw_limit or len(texts)))
            except (TypeError, ValueError):
                batch_limit = len(texts)
            generated: list[list[float]] = []
            for start_index in range(0, len(texts), batch_limit):
                chunk = texts[start_index : start_index + batch_limit]
                start = time.monotonic()
                try:
                    batch_result = batch_method(chunk)
                    if not inspect.isawaitable(batch_result):
                        raise RuntimeError("batch embedding generate_batch did not return awaitable")
                    results = await batch_result
                    embeddings = [list(result.embedding) for result in results]
                    if len(embeddings) != len(chunk):
                        raise RuntimeError(
                            f"batch embedding returned {len(embeddings)} vectors "
                            f"for {len(chunk)} texts"
                        )
                    latency_ms = (time.monotonic() - start) * 1000
                    dimension = len(embeddings[0]) if embeddings else 0
                    self._embedding_stats["batch_calls"] += 1
                    self._embedding_stats["batch_items"] += len(chunk)
                    self._embedding_stats["latency_ms_total"] += int(round(latency_ms))
                    self._embedding_stats["last_latency_ms"] = int(round(latency_ms))
                    self._embedding_stats["last_batch_size"] = len(chunk)
                    self._embedding_stats["last_dimension"] = dimension
                    generated.extend(embeddings)
                except Exception as exc:
                    self._embedding_stats["batch_fallbacks"] += 1
                    _log.warning(
                        "embedding_batch_failed_falling_back_to_single | "
                        "batch_start=%d | batch_size=%d | error=%s",
                        start_index,
                        len(chunk),
                        exc,
                    )
                    for text in chunk:
                        generated.append(await self._generate_embedding(text))
            return generated

        return [await self._generate_embedding(text) for text in texts]

    def _embedding_cache_get(self, text: str) -> list[float] | None:
        key = self._embedding_cache_key(text)
        embedding = self._embedding_cache.get(key)
        if embedding is None:
            self._embedding_stats["cache_misses"] += 1
            return None
        self._embedding_cache.move_to_end(key)
        self._embedding_stats["cache_hits"] += 1
        return list(embedding)

    def _embedding_cache_put(self, text: str, embedding: list[float]) -> None:
        key = self._embedding_cache_key(text, dimensions=len(embedding))
        self._embedding_cache[key] = list(embedding)
        self._embedding_cache.move_to_end(key)
        while len(self._embedding_cache) > self._embedding_cache_max_entries:
            self._embedding_cache.popitem(last=False)

    def _embedding_cache_key(self, text: str, *, dimensions: int | None = None) -> str:
        provider, model = self._embedding_identity()
        dim = int(dimensions or self._vector_dimension or self._requested_embedding_dimensions())
        text_hash = hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()
        return f"{provider}:{model}:{dim}:{text_hash}"

    def _embedding_identity(self) -> tuple[str, str]:
        if self._use_mock_embeddings:
            return "mock", "deterministic"
        config = self._embedding_config or {}
        provider = str(config.get("provider", "") or "unknown").strip().lower()
        model = str(config.get("model", "") or "unknown").strip().lower()
        return provider, model

    def _requested_embedding_dimensions(self) -> int:
        config = self._embedding_config or {}
        if config.get("dimensions"):
            try:
                return int(config["dimensions"])
            except Exception:
                pass
        return int(self._vector_dimension or self._mock_dimensions or 1536)

    async def shutdown(self) -> None:
        """Release embedding client resources."""
        self.flush_vector_store()
        service = self._embedding_service
        self._embedding_service = None
        if service is None:
            return
        close_hook = getattr(service, "shutdown", None)
        if not callable(close_hook):
            close_hook = getattr(service, "aclose", None)
        if not callable(close_hook):
            return
        result = close_hook()
        if inspect.isawaitable(result):
            await result

    async def aclose(self) -> None:
        """Alias for ``shutdown``."""
        await self.shutdown()

    def flush_vector_store(self) -> None:
        """Flush vector-store writes when the backend supports it."""
        if self._vector_store is None:
            return
        flush = getattr(self._vector_store, "flush", None)
        if callable(flush):
            flush()

    def _ensure_vector_store_for_vector(
        self,
        vector: list[float],
        *,
        source: str,
        reset_existing: bool = False,
    ) -> VectorStore:
        """Initialize or validate the vector store from an actual vector."""
        return self._ensure_vector_store_for_dimension(
            len(vector),
            source=source,
            reset_existing=reset_existing,
        )

    def _ensure_vector_store_for_dimension(
        self,
        dimension: int,
        *,
        source: str,
        reset_existing: bool = False,
    ) -> VectorStore:
        """Create the vector store lazily using the observed embedding dimension."""
        dimension = int(dimension or 0)
        if dimension <= 0:
            raise ValueError(f"embedding dimension must be positive; got {dimension}")

        configured = None
        if self._embedding_config and self._embedding_config.get("dimensions"):
            try:
                configured = int(self._embedding_config["dimensions"])
            except Exception:
                configured = None
        if configured and configured != dimension:
            _log.warning(
                "embedding_dimension_config_mismatch | configured=%d | observed=%d | "
                "model=%s | source=%s | using_observed=true",
                configured,
                dimension,
                (self._embedding_config or {}).get("model", ""),
                source,
            )

        if self._vector_store is not None and reset_existing:
            self.flush_vector_store()
            self._vector_store = None

        if self._vector_store is not None:
            current = self._vector_dimension or int(
                getattr(self._vector_store, "dimension", 0) or 0
            )
            if current == dimension:
                return self._vector_store
            if (
                self._index
                or self._outline_index
                or self._critique_index
                or self._volume_audit_index
            ):
                raise ValueError(
                    "embedding dimension changed after vectors were indexed: "
                    f"expected {current}, got {dimension}. Clear or rebuild memory "
                    "with one embedding model before continuing."
                )
            self.flush_vector_store()
            self._vector_store = None

        self._mock_dimensions = dimension
        self._vector_dimension = dimension
        self._vector_store = create_vector_store(
            backend=self._vector_store_backend_requested,
            path=self._vector_store_path,
            dimension=dimension,
            index_type=self._zvec_index_type,
            memory_limit_mb=self._zvec_memory_limit_mb,
            extra_int_fields=self._vector_store_extra_int_fields,
            reset_existing=reset_existing,
        )
        _log.info(
            "episodic_vector_store_initialized | backend=%s | dimension=%d | path=%s | source=%s",
            self.vector_store_backend,
            dimension,
            self.vector_store_path,
            source,
        )
        return self._vector_store

    def _mock_embedding(self, text: str, dimensions: int | None = None) -> list[float]:
        """Generate a mock embedding based on text hash.

        This is for development/testing when no API key is available.
        Generates a deterministic pseudo-embedding using SHA256.

        Args:
            text: Text to embed
            dimensions: Output vector dimensions.  When *None* (the default),
                uses ``self._mock_dimensions`` which is automatically aligned
                with the configured embedding model.

        Returns:
            Pseudo-embedding vector
        """
        if dimensions is None:
            dimensions = self._mock_dimensions
        # Use SHA256 hash to generate deterministic pseudo-random vector
        import hashlib

        h = hashlib.sha256(text.encode("utf-8")).digest()

        # Extend hash if needed for larger dimensions
        while len(h) < dimensions * 4:
            h += hashlib.sha256(h).digest()

        # Convert bytes to floats in [-1, 1]
        embedding = []
        for i in range(dimensions):
            byte_val = h[i % len(h)]
            embedding.append((byte_val - 128) / 128.0)

        return embedding

    def _extract_event_types(self, outcome: ChapterOutcome) -> list[str]:
        """Extract event type tags from chapter outcome."""
        types: list[str] = []

        # Check for conflict
        if outcome.alignment_report:
            if outcome.alignment_report.conflict_level in ("high", "critical"):
                types.append("high_conflict")

        # Check for character introductions
        if outcome.creative_report and outcome.creative_report.new_characters:
            types.append("character_introduction")

        # Check for revelations
        if outcome.creative_report and outcome.creative_report.key_revelations:
            types.append("revelation")

        # Check for decisions
        if outcome.new_events:
            for event in outcome.new_events:
                if any(
                    word in event.event.lower() for word in ["决定", "选择", "decide", "choose"]
                ):
                    types.append("decision")
                    break

        # Heuristic event-type detection from chapter text
        # (inspired by MemPalace general_extractor memory classification)
        chapter_text = getattr(outcome, "text", "") or ""
        if chapter_text:
            text_lower = chapter_text.lower()
            if any(kw in text_lower for kw in ["死亡", "去世", "牺牲", "kill", "die", "dead"]):
                if "death" not in types:
                    types.append("death")
            if any(kw in text_lower for kw in ["相遇", "重逢", "结识", "遇见", "meet", "reunite"]):
                if "meeting" not in types:
                    types.append("meeting")
            if any(
                kw in text_lower for kw in ["解决", "化解", "和解", "平息", "resolve", "reconcile"]
            ):
                if "resolution" not in types:
                    types.append("resolution")
            if any(
                kw in text_lower for kw in ["危机", "困境", "威胁", "crisis", "threat", "danger"]
            ):
                if "tension" not in types:
                    types.append("tension")
            if any(
                kw in text_lower
                for kw in ["转折", "然而", "但是", "however", "but then", "unexpectedly"]
            ):
                if "pivot" not in types:
                    types.append("pivot")

        if not types:
            types.append("narrative_progress")

        return types

    def _extract_emotional_tone(self, outcome: ChapterOutcome) -> str:
        """Extract emotional tone from chapter outcome.

        Uses ChapterExitState.emotional_state (POV character) as primary source,
        falls back to creative_report highlights keyword extraction.
        """
        # 1. Primary: POV character emotional state from exit state
        exit_state = getattr(outcome, "chapter_exit_state", None)
        if exit_state is not None:
            emotion = getattr(exit_state, "emotional_state", "")
            if emotion:
                return emotion

        # 2. Fallback: extract from creative_report key_moments / creative_highlights
        creative = getattr(outcome, "creative_report", None)
        if creative is not None:
            hints: list[str] = []
            for moment in getattr(creative, "key_moments", []):
                if moment:
                    hints.append(moment)
            for hl in getattr(creative, "creative_highlights", []):
                if hl:
                    hints.append(hl)
            if hints:
                combined = "；".join(hints[:3])
                return combined[:120]

        # 3. Fallback: character_state_deltas emotional changes
        if creative is not None:
            for delta in getattr(creative, "character_state_deltas", []):
                to_state = getattr(delta, "to_state", None)
                if to_state is not None:
                    emo = getattr(to_state, "emotional_state", "")
                    if emo:
                        return emo

        return ""

    @staticmethod
    def _detect_emotion_flags(text: str) -> tuple[list[str], list[str]]:
        """Detect emotion codes and importance flags from text.

        Inspired by MemPalace's AAAK dialect emotion/flag detection.
        Returns (emotion_codes, flags) — both may be empty.

        Emotion codes map to compact labels used in episodic metadata.
        Flags mark structural importance (DECISION, PIVOT, ORIGIN, etc.).
        """
        if not text:
            return [], []

        text_lower = text.lower()

        emotion_map = {
            "vul": ["脆弱", "不安", "vulnerable", "uncertain"],
            "joy": ["喜悦", "欢乐", "joy", "happy", "欣喜"],
            "fear": ["恐惧", "害怕", "fear", "afraid", "scared"],
            "trust": ["信任", "信赖", "trust", "believe"],
            "grief": ["悲伤", "悲痛", "grief", "sorrow", "哀伤"],
            "rage": ["愤怒", "怒火", "rage", "anger", "furious"],
            "love": ["爱", "爱慕", "love", "devotion", "深情"],
            "hope": ["希望", "期盼", "hope", "hopeful"],
            "despair": ["绝望", "despair", "hopeless"],
            "tender": ["温柔", "tender", "gentle", "柔情"],
            "anx": ["焦虑", "anxiety", "anxious", "担忧", "worried"],
            "determ": ["决心", "坚定", "determined", "resolute"],
        }

        detected_emotions: list[str] = []
        for code, keywords in emotion_map.items():
            if any(kw in text_lower for kw in keywords):
                detected_emotions.append(code)
                if len(detected_emotions) >= 3:
                    break

        flag_signals = {
            "DECISION": ["决定", "选择", "decided", "chose", "switched"],
            "ORIGIN": ["开始", "起源", "诞生", "origin", "began", "started", "first time"],
            "PIVOT": ["转折", "然而", "突然", "pivot", "turning point", "changed everything"],
            "CORE": ["核心", "本质", "core", "fundamental", "essential", "always"],
        }

        detected_flags: list[str] = []
        for flag, keywords in flag_signals.items():
            if any(kw in text_lower for kw in keywords):
                detected_flags.append(flag)
                if len(detected_flags) >= 3:
                    break

        return detected_emotions, detected_flags

    async def index_outline_phase(
        self,
        chapter_number: int,
        plot_points: list[str],
        characters: list[str] | None = None,
        pov_character: str = "",
        chapter_goal: str = "",
        themes: list[str] | None = None,
        relationship_changes: list[RelationshipChange] | None = None,
        unresolved_questions: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> list[str]:
        """Index outline phase plot points for semantic retrieval.

        This method enables the outline generation phase to use the same
        memory system as chapter generation, providing unified tracking.

        Args:
            chapter_number: Chapter number
            plot_points: List of main plot points for this chapter
            characters: Characters involved in this chapter
            pov_character: Point-of-view character
            chapter_goal: Goal/summary of this chapter
            themes: Themes appearing in this chapter
            relationship_changes: Relationship changes in this chapter
            unresolved_questions: Questions/suspense introduced

        Returns:
            List of indexed entry signatures
        """
        entries: list[OutlinePlotPoint] = []
        characters = characters or []
        themes = themes or []

        for i, point in enumerate(plot_points):
            event_type = self._classify_plot_point(point)
            entry = OutlinePlotPoint(
                chapter_number=chapter_number,
                plot_point=point,
                event_type=event_type,
                characters=characters,
                themes=themes,
                pov_character=pov_character,
                chapter_goal=chapter_goal,
                metadata={
                    "point_index": i,
                    "is_main_plot": i < 3,
                    **(metadata or {}),
                },
            )

            entries.append(entry)

        signatures = await self._add_outline_entries_batch(entries)

        if relationship_changes:
            for change in relationship_changes:
                self._track_relationship_change(change)

        if themes:
            for theme in themes:
                if theme not in self._theme_tracker:
                    self._theme_tracker[theme] = []
                if chapter_number not in self._theme_tracker[theme]:
                    self._theme_tracker[theme].append(chapter_number)

        if unresolved_questions:
            for q in unresolved_questions:
                if q not in self._unresolved_questions:
                    self._unresolved_questions.append(q)

        return signatures

    async def _add_outline_entry(self, entry: OutlinePlotPoint) -> str:
        """Add an outline entry to the index."""
        signatures = await self._add_outline_entries_batch([entry])
        return signatures[0] if signatures else entry.signature()

    async def _add_outline_entries_batch(self, entries: list[OutlinePlotPoint]) -> list[str]:
        """Add outline entries to the index, batching embedding generation."""
        pending: list[tuple[str, OutlinePlotPoint]] = []
        signatures: list[str] = []
        for entry in entries:
            sig = entry.signature()
            signatures.append(sig)
            if sig not in self._outline_index:
                pending.append((sig, entry))

        if not pending:
            return signatures

        embeddings = await self._generate_embeddings_batch(
            [entry.plot_point for _, entry in pending]
        )
        vector_store = self._ensure_vector_store_for_vector(
            embeddings[0],
            source="outline_index_batch",
        )

        vector_items: list[tuple[str, list[float], dict[str, Any]]] = []
        for (sig, entry), embedding in zip(pending, embeddings, strict=True):
            entry.embedding = embedding
            self._outline_index[sig] = entry
            if entry.chapter_number not in self._chapter_outlines:
                self._chapter_outlines[entry.chapter_number] = []
            self._chapter_outlines[entry.chapter_number].append(sig)
            vector_items.append(
                (
                    sig,
                    entry.embedding,
                    {
                        "chapter": entry.chapter_number,
                        "event_types": ([entry.event_type] if entry.event_type else []),
                        "characters": entry.characters,
                        "is_outline": True,
                        **{
                            str(key): int(value)
                            for key, value in (entry.metadata or {}).items()
                            if isinstance(value, int)
                        },
                    },
                )
            )

        self._add_vectors(vector_store, vector_items)
        return signatures

    def _classify_plot_point(self, plot_point: str) -> str:
        """Classify a plot point into an event type based on keywords."""
        text_lower = plot_point.lower()

        conflict_keywords = ["对抗", "冲突", "战斗", "争夺", "竞争", "挑战", "争执", "斗争"]
        if any(kw in text_lower for kw in conflict_keywords):
            return "conflict"

        revelation_keywords = ["揭露", "揭示", "发现", "真相", "秘密", "曝光", "暴露", "揭晓"]
        if any(kw in text_lower for kw in revelation_keywords):
            return "revelation"

        decision_keywords = ["决定", "选择", "抉择", "决心", "计划", "决策"]
        if any(kw in text_lower for kw in decision_keywords):
            return "decision"

        death_keywords = ["死亡", "去世", "牺牲", "灭亡"]
        if any(kw in text_lower for kw in death_keywords):
            return "death"

        meeting_keywords = ["相遇", "重逢", "相会", "结识", "遇见", "见面", "偶遇"]
        if any(kw in text_lower for kw in meeting_keywords):
            return "meeting"

        tension_keywords = ["紧张", "危机", "困境", "难题", "威胁"]
        if any(kw in text_lower for kw in tension_keywords):
            return "tension"

        resolution_keywords = ["解决", "化解", "和解", "平息", "成功", "突破"]
        if any(kw in text_lower for kw in resolution_keywords):
            return "resolution"

        return "narrative_progress"

    def _track_relationship_change(self, change: RelationshipChange) -> None:
        """Track a relationship change between two characters."""
        pair_key = self._make_pair_key(change.character_a, change.character_b)

        if pair_key not in self._relationships:
            self._relationships[pair_key] = {}

        change_key = f"ch{change.chapter}_{change.change_type}"
        self._relationships[pair_key][change_key] = change

    def _make_pair_key(self, char_a: str, char_b: str) -> tuple[str, str]:
        """Create a canonical key for a character pair."""
        sorted_chars = sorted([char_a.lower(), char_b.lower()])
        return (sorted_chars[0], sorted_chars[1])

    async def get_outline_context(
        self,
        current_chapter: int,
        recent_chapters: int = 5,
        similar_top_k: int = 3,
    ) -> OutlineContext:
        """Get comprehensive context for outline generation.

        This method provides the same semantic search capability to outline
        generation that chapter generation has, creating a unified memory system.

        Args:
            current_chapter: The chapter being generated
            recent_chapters: Number of recent chapters to include
            similar_top_k: Number of similar events to find

        Returns:
            OutlineContext with all relevant memory
        """
        context = OutlineContext()

        if current_chapter <= 1:
            for sig in self._chapter_outlines.get(current_chapter, []):
                entry = self._outline_index.get(sig)
                if entry:
                    context.recent_plot_points.append(entry)
            context.theme_occurrences = {
                theme: chapters
                for theme, chapters in self._theme_tracker.items()
                if current_chapter in chapters
            }
            context.unresolved_questions = [
                question
                for question in self._unresolved_questions
                if f"第{current_chapter}章" in question
            ][-5:]
            context.chapter_summary = self._generate_init_outline_summary(
                current_chapter,
                context.recent_plot_points,
            )
            return context

        recent_start = max(1, current_chapter - recent_chapters)
        for ch in range(recent_start, current_chapter):
            if ch in self._chapter_outlines:
                for sig in self._chapter_outlines[ch]:
                    entry = self._outline_index.get(sig)
                    if entry:
                        context.recent_plot_points.append(entry)

        relationship_lines = []
        for _pair_key, changes in self._relationships.items():
            sorted_changes = sorted(changes.values(), key=lambda x: x.chapter)
            if sorted_changes:
                latest = sorted_changes[-1]
                relationship_lines.append(
                    RelationshipChange(
                        character_a=latest.character_a,
                        character_b=latest.character_b,
                        chapter=latest.chapter,
                        change_type=latest.change_type,
                        description=f"当前信任度: {latest.current_trust}, 紧张度: {latest.current_tension}",
                        current_trust=latest.current_trust,
                        current_tension=latest.current_tension,
                    )
                )
        context.relationship_changes = relationship_lines[-10:]

        context.theme_occurrences = dict(list(self._theme_tracker.items())[-10:])

        context.unresolved_questions = self._unresolved_questions[-10:]

        if recent_start <= current_chapter - 1:
            try:
                context.similar_events = await self.search_by_semantic(
                    query=f"第{current_chapter}章情节发展",
                    chapter_range=(recent_start, current_chapter - 1),
                    top_k=similar_top_k,
                    min_relevance=0.3,
                )
            except Exception as exc:
                _log.warning(
                    "outline_semantic_search_unavailable | current_chapter=%s | "
                    "range=%s-%s | error=%s",
                    current_chapter,
                    recent_start,
                    current_chapter - 1,
                    exc,
                )

        context.chapter_summary = self._generate_outline_summary(context)

        return context

    @staticmethod
    def _generate_init_outline_summary(
        current_chapter: int,
        entries: list[OutlinePlotPoint],
    ) -> str:
        if not entries:
            return ""
        lines = [f"## 初始化大纲参考（第{current_chapter}章）"]
        for entry in entries[:5]:
            point = str(entry.plot_point or "").strip()
            if not point:
                continue
            goal = str(entry.chapter_goal or "").strip()
            prefix = f"第{entry.chapter_number}章"
            if goal:
                prefix += f"「{goal[:30]}」"
            lines.append(f"- {prefix}: {point[:80]}")
        return "\n".join(lines)

    def _generate_outline_summary(self, context: OutlineContext) -> str:
        """Generate a summary of recent outline context for prompts."""
        lines = []

        if context.recent_plot_points:
            lines.append("## 最近章节概览")
            for pp in context.recent_plot_points[-5:]:
                lines.append(
                    f"第{pp.chapter_number}章「{pp.chapter_goal[:30]}」: {pp.plot_point[:40]}..."
                )

        if context.relationship_changes:
            lines.append("\n## 关系动态")
            for rel in context.relationship_changes[-5:]:
                lines.append(f"{rel.character_a} ↔ {rel.character_b}: {rel.description}")

        if context.theme_occurrences:
            lines.append("\n## 主题线索")
            for theme, chapters in list(context.theme_occurrences.items())[-3:]:
                lines.append(f"「{theme}」出现在: 第{', '.join(map(str, chapters[-3:]))}章")

        if context.unresolved_questions:
            lines.append("\n## 待解决")
            for q in context.unresolved_questions[-3:]:
                lines.append(f"• {q}")

        return "\n".join(lines) if lines else ""

    async def search_similar_plot_points(
        self,
        query: str,
        chapter_range: tuple[int, int] | None = None,
        metadata_filter: dict[str, Any] | None = None,
        top_k: int = 5,
    ) -> list[tuple[OutlinePlotPoint, float]]:
        """Search for similar plot points using semantic search.

        Args:
            query: Search query
            chapter_range: Optional chapter range filter
            top_k: Maximum results

        Returns:
            List of (plot_point, similarity_score) tuples
        """
        query_vector = await self._generate_embedding(query)
        vector_store = self._ensure_vector_store_for_vector(
            query_vector,
            source="outline_query",
        )

        filter_dict: dict[str, Any] = {"is_outline": True}
        if chapter_range:
            filter_dict["chapter"] = {"$gte": chapter_range[0], "$lte": chapter_range[1]}
        if metadata_filter:
            for key, value in metadata_filter.items():
                if key == "chapter" and key in filter_dict:
                    existing = filter_dict[key]
                    if isinstance(existing, dict) and isinstance(value, dict):
                        existing_min = existing.get("$gte")
                        existing_max = existing.get("$lte")
                        requested_min = value.get("$gte")
                        requested_max = value.get("$lte")
                        if isinstance(existing_min, int | float) and isinstance(
                            requested_min,
                            int | float,
                        ):
                            existing["$gte"] = max(
                                int(existing_min),
                                int(requested_min),
                            )
                        if isinstance(existing_max, int | float) and isinstance(
                            requested_max,
                            int | float,
                        ):
                            existing["$lte"] = min(
                                int(existing_max),
                                int(requested_max),
                            )
                        continue
                    filter_dict.pop("chapter", None)
                filter_dict[key] = value

        matches = vector_store.hybrid_search(
            query_vector,
            query,
            top_k=top_k * 2,
            filter=filter_dict,
        )

        results = []
        for sig, score, _metadata in matches:
            entry = self._outline_index.get(sig)
            if entry:
                results.append((entry, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def get_memory_stats(self) -> dict[str, Any]:
        """Get statistics about the memory system."""
        scene_entries = sum(1 for e in self._index.values() if e.scene_index > 0)
        chapter_entries = len(self._index) - scene_entries
        vector_count = self._vector_store_vector_count()
        return {
            "total_episodic_entries": len(self._index),
            "chapter_level_entries": chapter_entries,
            "scene_level_entries": scene_entries,
            "total_outline_entries": len(self._outline_index),
            "total_critique_entries": len(self._critique_index),
            "total_vectors_indexed": len(self._index)
            + len(self._outline_index)
            + len(self._critique_index),
            "vector_store_backend": self.vector_store_backend,
            "vector_store_path": self.vector_store_path,
            "vector_store_dimension": self._vector_dimension or self._mock_dimensions,
            "vector_store_initialized": self._vector_store is not None,
            "vector_store_initialization_state": self._vector_store_initialization_state(),
            "vector_store_index_type": self._zvec_index_type,
            "vector_store_vector_count": vector_count,
            "embedding_cache_size": len(self._embedding_cache),
            "embedding_stats": dict(self._embedding_stats),
            "total_chapters_outlined": len(self._chapter_outlines),
            "total_relationships_tracked": len(self._relationships),
            "total_themes_tracked": len(self._theme_tracker),
            "unresolved_questions": len(self._unresolved_questions),
            "theme_details": {
                theme: f"{len(chapters)}次出现 (章节: {chapters[-3:]})"
                for theme, chapters in list(self._theme_tracker.items())[:5]
            },
        }

    def _vector_store_vector_count(self) -> int:
        if self._vector_store is None:
            return 0
        try:
            collection = getattr(self._vector_store, "_collection", None)
            stats = getattr(collection, "stats", None)
            count = int(getattr(stats, "doc_count", 0) or 0)
            if count:
                return count
        except Exception:
            pass
        for attr in ("_metadata", "_vectors"):
            try:
                count = len(getattr(self._vector_store, attr, {}) or {})
                if count:
                    return count
            except Exception:
                continue
        return 0

    def _vector_store_initialization_state(self) -> str:
        if self._vector_store is not None:
            return "initialized"
        if self._embedding_service is not None and not self._use_mock_embeddings:
            return "deferred"
        return "uninitialized"

    def _add_vectors(
        self,
        vector_store: VectorStore,
        items: list[tuple[str, list[float], dict[str, Any]]],
    ) -> None:
        if not items:
            return
        add_many = getattr(vector_store, "add_many", None)
        if callable(add_many):
            add_many(items)
            return
        for sig, embedding, metadata in items:
            vector_store.add(sig, embedding, metadata)

    def hydrate_vector_store_metadata(self) -> int:
        """Refresh vector-store metadata mirror from JSON-backed indexes."""
        if self._vector_store is None:
            return 0
        metadata_map: dict[str, dict[str, Any]] = {}
        for sig, episodic_entry in self._index.items():
            metadata_map[sig] = self._entry_vector_metadata(episodic_entry)
        for sig, outline_entry in self._outline_index.items():
            metadata_map[sig] = {
                "content": " ".join(
                    part
                    for part in (
                        outline_entry.plot_point,
                        " ".join(outline_entry.characters),
                        " ".join(outline_entry.themes),
                        outline_entry.pov_character,
                        outline_entry.chapter_goal,
                    )
                    if part
                ),
                "chapter": outline_entry.chapter_number,
                "event_types": ([outline_entry.event_type] if outline_entry.event_type else []),
                "characters": outline_entry.characters,
                "is_outline": True,
            }
        for sig, critique_entry in self._critique_index.items():
            metadata_map[sig] = {
                "content": " ".join(
                    part
                    for part in (
                        critique_entry.summary,
                        critique_entry.evidence,
                        critique_entry.suggested_fix,
                        critique_entry.issue_type,
                        critique_entry.severity,
                    )
                    if part
                ),
                "chapter": critique_entry.chapter_number,
                "scene_index": critique_entry.scene_index,
                "issue_type": critique_entry.issue_type,
                "severity": critique_entry.severity,
            }
        for sig, volume_entry in self._volume_audit_index.items():
            metadata_map[sig] = self._volume_entry_vector_metadata(volume_entry)
        metadata = getattr(self._vector_store, "_metadata", None)
        if isinstance(metadata, dict):
            metadata.clear()
            metadata.update(metadata_map)
            return len(metadata_map)
        return 0

    def rebuild_vector_store(self) -> int:
        """Rebuild vector index from entries that still carry embeddings.

        Returns:
            Number of vectors re-indexed.
        """
        self._vector_store = None
        self._vector_dimension = None
        cleared = False
        restored = 0
        vector_store: VectorStore | None = None
        vector_items: list[tuple[str, list[float], dict[str, Any]]] = []

        for sig, entry in self._index.items():
            embedding = entry.embedding
            if not embedding and self._use_mock_embeddings:
                embedding = self._mock_embedding(entry.event_summary)
            if not entry.embedding:
                entry.embedding = embedding
            if not embedding:
                continue
            vector_store = self._ensure_vector_store_for_vector(
                embedding,
                source="rebuild_episodic",
                reset_existing=not cleared,
            )
            if not cleared:
                cleared = True
            vector_items.append((sig, embedding, self._entry_vector_metadata(entry)))
            restored += 1

        for sig, outline_entry in self._outline_index.items():
            embedding = outline_entry.embedding
            if not embedding and self._use_mock_embeddings:
                embedding = self._mock_embedding(outline_entry.plot_point)
            if not outline_entry.embedding:
                outline_entry.embedding = embedding
            if not embedding:
                continue
            vector_store = self._ensure_vector_store_for_vector(
                embedding,
                source="rebuild_outline",
                reset_existing=not cleared,
            )
            if not cleared:
                cleared = True
            vector_items.append(
                (
                    sig,
                    embedding,
                    {
                        "content": " ".join(
                            part
                            for part in (
                                outline_entry.plot_point,
                                " ".join(outline_entry.characters),
                                " ".join(outline_entry.themes),
                                outline_entry.pov_character,
                                outline_entry.chapter_goal,
                            )
                            if part
                        ),
                        "chapter": outline_entry.chapter_number,
                        "event_types": (
                            [outline_entry.event_type] if outline_entry.event_type else []
                        ),
                        "characters": outline_entry.characters,
                        "is_outline": True,
                    },
                )
            )
            restored += 1

        for sig, critique_entry in self._critique_index.items():
            embedding = critique_entry.embedding
            if not embedding and self._use_mock_embeddings:
                embedding = self._mock_embedding(critique_entry.summary)
            if not critique_entry.embedding:
                critique_entry.embedding = embedding
            if not embedding:
                continue
            vector_store = self._ensure_vector_store_for_vector(
                embedding,
                source="rebuild_critique",
                reset_existing=not cleared,
            )
            if not cleared:
                cleared = True
            vector_items.append(
                (
                    sig,
                    embedding,
                    {
                        "content": " ".join(
                            part
                            for part in (
                                critique_entry.summary,
                                critique_entry.evidence,
                                critique_entry.suggested_fix,
                                critique_entry.issue_type,
                                critique_entry.severity,
                            )
                            if part
                        ),
                        "chapter": critique_entry.chapter_number,
                        "scene_index": critique_entry.scene_index,
                        "issue_type": critique_entry.issue_type,
                        "severity": critique_entry.severity,
                    },
                )
            )
            restored += 1

        if not cleared:
            if not self._use_mock_embeddings:
                _log.info(
                    "vector_store_rebuild_skipped | reason=no_inline_embeddings | backend=%s",
                    self._vector_store_backend_requested,
                )
                return 0
            vector_store = self._ensure_vector_store_for_dimension(
                self._mock_dimensions,
                source="rebuild_empty",
                reset_existing=True,
            )

        if vector_store is not None:
            self._add_vectors(vector_store, vector_items)
        self.flush_vector_store()
        return restored

    async def rebuild_vector_collection(self) -> dict[str, Any]:
        """Regenerate embeddings and rebuild the backing vector collection."""
        vector_items: list[tuple[str, str, dict[str, Any], Any]] = []
        for sig, episodic_entry in self._index.items():
            vector_items.append(
                (
                    sig,
                    episodic_entry.event_summary,
                    self._entry_vector_metadata(episodic_entry),
                    episodic_entry,
                )
            )
        for sig, outline_entry in self._outline_index.items():
            vector_items.append(
                (
                    sig,
                    outline_entry.plot_point,
                    {
                        "content": " ".join(
                            part
                            for part in (
                                outline_entry.plot_point,
                                " ".join(outline_entry.characters),
                                " ".join(outline_entry.themes),
                                outline_entry.pov_character,
                                outline_entry.chapter_goal,
                            )
                            if part
                        ),
                        "chapter": outline_entry.chapter_number,
                        "event_types": (
                            [outline_entry.event_type] if outline_entry.event_type else []
                        ),
                        "characters": outline_entry.characters,
                        "is_outline": True,
                    },
                    outline_entry,
                )
            )
        for sig, critique_entry in self._critique_index.items():
            vector_items.append(
                (
                    sig,
                    critique_entry.summary,
                    {
                        "content": " ".join(
                            part
                            for part in (
                                critique_entry.summary,
                                critique_entry.evidence,
                                critique_entry.suggested_fix,
                                critique_entry.issue_type,
                                critique_entry.severity,
                            )
                            if part
                        ),
                        "chapter": critique_entry.chapter_number,
                        "scene_index": critique_entry.scene_index,
                        "issue_type": critique_entry.issue_type,
                        "severity": critique_entry.severity,
                    },
                    critique_entry,
                )
            )
        for sig, volume_entry in self._volume_audit_index.items():
            vector_items.append(
                (
                    sig,
                    volume_entry.volume_summary,
                    self._volume_entry_vector_metadata(volume_entry),
                    volume_entry,
                )
            )

        self._vector_store = None
        self._vector_dimension = None
        self._embedding_cache.clear()

        if not vector_items:
            vector_store = self._ensure_vector_store_for_dimension(
                self._requested_embedding_dimensions(),
                source="rebuild_collection_empty",
                reset_existing=True,
            )
            self.flush_vector_store()
            return {
                "rebuilt_vectors": 0,
                "backend": self.vector_store_backend,
                "dimension": self._vector_dimension or self._mock_dimensions,
                "path": self.vector_store_path,
                "index_type": self._zvec_index_type,
            }

        embeddings = await self._generate_embeddings_batch([text for _, text, _, _ in vector_items])
        vector_store = self._ensure_vector_store_for_vector(
            embeddings[0],
            source="rebuild_collection",
            reset_existing=True,
        )

        vectors_to_add: list[tuple[str, list[float], dict[str, Any]]] = []
        for (sig, _text, metadata, entry), embedding in zip(
            vector_items,
            embeddings,
            strict=True,
        ):
            entry.embedding = embedding
            vectors_to_add.append((sig, embedding, metadata))

        self._add_vectors(vector_store, vectors_to_add)
        self.flush_vector_store()
        return {
            "rebuilt_vectors": len(vector_items),
            "backend": self.vector_store_backend,
            "dimension": self._vector_dimension or self._mock_dimensions,
            "path": self.vector_store_path,
            "index_type": self._zvec_index_type,
            "embedding_stats": dict(self._embedding_stats),
        }

    def delete_chapter_memory(self, chapter_number: int) -> list[str]:
        """Delete all memory entries for a specific chapter.

        This is called when a chapter is regenerated to remove stale memory data.

        Args:
            chapter_number: Chapter number to delete memory for

        Returns:
            List of deleted entry signatures
        """
        deleted_signatures: list[str] = []

        if chapter_number in self._chapter_events:
            for sig in self._chapter_events[chapter_number]:
                if sig in self._index:
                    del self._index[sig]
                    if self._vector_store is not None:
                        self._vector_store.remove(sig)
                    deleted_signatures.append(sig)
            del self._chapter_events[chapter_number]

        if chapter_number in self._chapter_outlines:
            for sig in self._chapter_outlines[chapter_number]:
                if sig in self._outline_index:
                    del self._outline_index[sig]
                    if self._vector_store is not None:
                        self._vector_store.remove(sig)
                    deleted_signatures.append(sig)
            del self._chapter_outlines[chapter_number]

        if chapter_number in self._chapter_critiques:
            for sig in self._chapter_critiques[chapter_number]:
                if sig in self._critique_index:
                    del self._critique_index[sig]
                    if self._vector_store is not None:
                        self._vector_store.remove(sig)
                    deleted_signatures.append(sig)
            del self._chapter_critiques[chapter_number]

        for pair_key, changes in list(self._relationships.items()):
            changes_to_remove = [
                key for key, change in changes.items() if change.chapter == chapter_number
            ]
            for key in changes_to_remove:
                del changes[key]
            if not changes:
                del self._relationships[pair_key]

        for theme, chapters in list(self._theme_tracker.items()):
            if chapter_number in chapters:
                chapters.remove(chapter_number)
            if not chapters:
                del self._theme_tracker[theme]

        import logging

        _log = logging.getLogger(__name__)
        _log.info(
            "Deleted episodic memory for chapter %d | entries_removed=%d",
            chapter_number,
            len(deleted_signatures),
        )

        return deleted_signatures

    def delete_chapters_from(self, from_chapter: int) -> list[str]:
        """Delete memory entries for all chapters from from_chapter onwards.

        This is called when downstream chapters are invalidated after an upstream rewrite.

        Args:
            from_chapter: Start chapter number (inclusive) to delete memory for

        Returns:
            List of all deleted entry signatures
        """
        all_deleted: list[str] = []

        chapters_to_delete = sorted(
            set(self._chapter_events.keys()) | set(self._chapter_outlines.keys())
        )
        for ch in chapters_to_delete:
            if ch >= from_chapter:
                all_deleted.extend(self.delete_chapter_memory(ch))

        import logging

        _log = logging.getLogger(__name__)
        _log.info(
            "Deleted episodic memory from chapter %d onwards | total_removed=%d",
            from_chapter,
            len(all_deleted),
        )

        return all_deleted

    def evict_chapters_before(self, before_chapter: int) -> int:
        """Evict in-memory index entries for chapters older than *before_chapter*.

        Unlike ``delete_chapters_from`` (which removes entries *after* a
        chapter for invalidation), this method removes entries *before* a
        chapter to bound the in-memory footprint for long-running projects.
        The vectors remain in the zvec collection for semantic retrieval;
        only the Python-side ``_index`` and ``_chapter_events`` dicts are
        trimmed.

        Args:
            before_chapter: Evict all chapters strictly before this number.

        Returns:
            Number of entries evicted from in-memory index.
        """
        evicted = 0
        chapters_to_evict = [ch for ch in self._chapter_events if ch < before_chapter]
        for chapter in chapters_to_evict:
            sigs = self._chapter_events.pop(chapter, [])
            for sig in sigs:
                self._index.pop(sig, None)
                evicted += 1
        if evicted:
            _log.info(
                "evict_chapters_before | before=%d | evicted=%d | remaining_index=%d",
                before_chapter,
                evicted,
                len(self._index),
            )
        return evicted

    def serialize_outline_data(self) -> dict[str, Any]:
        """Serialize outline phase data for persistence.

        This includes outline plot points, relationships, themes, and unresolved questions.
        This data is indexed during init_long and needs to be shared with chapter generation.

        Returns:
            Dictionary with outline data suitable for JSON serialization
        """
        try:
            outline_entries = {}
            for sig, entry in self._outline_index.items():
                outline_entries[sig] = {
                    "chapter_number": entry.chapter_number,
                    "plot_point": entry.plot_point,
                    "event_type": entry.event_type,
                    "characters": entry.characters,
                    "themes": entry.themes,
                    "pov_character": entry.pov_character,
                    "chapter_goal": entry.chapter_goal,
                    "embedding": entry.embedding,
                    "metadata": entry.metadata,
                }

            return {
                "outline_index": outline_entries,
                "chapter_outlines": {str(k): v for k, v in self._chapter_outlines.items()},
                "relationships": {
                    f"{k[0]}___{k[1]}": {
                        change_key: {
                            "character_a": change.character_a,
                            "character_b": change.character_b,
                            "chapter": change.chapter,
                            "change_type": change.change_type,
                            "description": change.description,
                            "current_trust": change.current_trust,
                            "current_tension": change.current_tension,
                        }
                        for change_key, change in changes.items()
                    }
                    for k, changes in self._relationships.items()
                },
                "theme_tracker": {k: v for k, v in self._theme_tracker.items()},
                "unresolved_questions": list(self._unresolved_questions),
            }
        except Exception as exc:
            import logging

            logging.warning(f"Failed to serialize outline data: {exc}")
            return {}

    def deserialize_outline_data(self, data: dict[str, Any]) -> None:
        """Deserialize outline phase data from persistence.

        This restores outline plot points, relationships, themes, and unresolved questions
        that were indexed during init_long, making them available for chapter generation.

        Args:
            data: Dictionary with serialized outline data
        """
        if not data:
            return

        try:
            if "outline_index" in data:
                for sig, entry_data in data["outline_index"].items():
                    entry = OutlinePlotPoint(
                        chapter_number=entry_data["chapter_number"],
                        plot_point=entry_data["plot_point"],
                        event_type=entry_data.get("event_type", ""),
                        characters=entry_data.get("characters", []),
                        themes=entry_data.get("themes", []),
                        pov_character=entry_data.get("pov_character", ""),
                        chapter_goal=entry_data.get("chapter_goal", ""),
                        embedding=entry_data.get("embedding", []),
                        metadata=entry_data.get("metadata", {}),
                    )
                    self._outline_index[sig] = entry

            if "chapter_outlines" in data:
                self._chapter_outlines.clear()
                for k, v in data["chapter_outlines"].items():
                    self._chapter_outlines[int(k)] = v

            if "relationships" in data:
                self._relationships.clear()
                for pair_key_str, changes in data["relationships"].items():
                    parts = pair_key_str.split("___")
                    if len(parts) == 2:
                        pair_key = (parts[0], parts[1])
                        self._relationships[pair_key] = {}
                        for change_key, change_data in changes.items():
                            change = RelationshipChange(
                                character_a=change_data["character_a"],
                                character_b=change_data["character_b"],
                                chapter=change_data["chapter"],
                                change_type=change_data["change_type"],
                                description=change_data.get("description", ""),
                                current_trust=change_data.get("current_trust", 0.5),
                                current_tension=change_data.get("current_tension", 0.5),
                            )
                            self._relationships[pair_key][change_key] = change

            if "theme_tracker" in data:
                self._theme_tracker.clear()
                for theme, chapters in data["theme_tracker"].items():
                    self._theme_tracker[theme] = chapters

            if "unresolved_questions" in data:
                self._unresolved_questions.clear()
                self._unresolved_questions.extend(data["unresolved_questions"])

            import logging

            logging.info(
                f"Loaded outline data | outlines={len(self._outline_index)} | "
                f"relationships={len(self._relationships)} | themes={len(self._theme_tracker)}"
            )
        except Exception as exc:
            import logging

            logging.warning(f"Failed to deserialize outline data: {exc}")


__all__ = [
    "EpisodicIndex",
    "EpisodicMemory",
    "EpisodicResult",
    "OutlinePlotPoint",
]
