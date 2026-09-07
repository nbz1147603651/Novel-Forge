"""Retrieval and indexing methods for MemoryContext."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from novel_forge.memory.base import MemoryBudgetConfig
from novel_forge.memory.canon_history_queries import (
    get_character_history as _get_character_history_impl,
)
from novel_forge.memory.canon_history_queries import (
    get_relationship_evolution as _get_relationship_evolution_impl,
)
from novel_forge.memory.character_context_builder import build_character_memory_context
from novel_forge.memory.context_payloads import (
    serialize_character_episodic_results as _serialize_character_episodic_payloads,
)
from novel_forge.memory.context_payloads import (
    serialize_character_motifs as _serialize_character_motif_payloads,
)
from novel_forge.memory.context_payloads import (
    serialize_due_foreshadows as _serialize_due_foreshadow_payloads,
)
from novel_forge.memory.context_payloads import (
    serialize_relationship_projection as _serialize_relationship_projection_payloads,
)
from novel_forge.memory.integration_motif import (
    extract_motifs_async as _motif_extract_async,
)
from novel_forge.memory.integration_motif import (
    schedule_motif_extraction as _motif_schedule_extraction,
)
from novel_forge.memory.integration_prompt_context import (
    build_critique_context_for_prompt as _prompt_build_critique_context,
)
from novel_forge.memory.integration_prompt_context import (
    legacy_summary_context_for_prompt as _prompt_legacy_summary_context,
)
from novel_forge.memory.integration_summary import (
    generate_summary_async as _summary_generate_async,
)
from novel_forge.memory.layered_context_builder import build_layered_memory_context
from novel_forge.memory.prompt_context_builder import build_prompt_memory_context
from novel_forge.memory.relevant_history_sync import (
    search_relevant_history_sync as _search_relevant_history_sync_impl,
)
from novel_forge.memory.summary_cache_helpers import (
    should_generate_summary as _should_generate_summary_impl,
)
from novel_forge.obs.logger import get_logger

if TYPE_CHECKING:
    pass


_log = get_logger("memory.context")


class RetrievalMixin:
    """Mixin providing search, indexing, and prompt context methods."""

    async def search_relevant_history(
        self,
        query: str,
        current_chapter: int,
        lookback: int = 5,
        top_k: int = 5,
        min_relevance: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Search for relevant historical events using semantic retrieval.

        This is a key method for memory-enhanced auditing. It retrieves
        historical events that are semantically relevant to the current
        context, providing crucial cross-chapter awareness.

        Args:
            query: Search query describing the context to find
            current_chapter: Current chapter being processed
            lookback: Number of chapters to look back
            top_k: Maximum number of results to return
            min_relevance: Minimum relevance score threshold

        Returns:
            List of relevant historical events with metadata
        """
        if not self._episodic_memory:
            return []

        try:
            start_chapter = max(1, current_chapter - lookback)
            end_chapter = current_chapter - 1

            results = await self._episodic_memory.search_by_semantic(
                query=query,
                chapter_range=(start_chapter, end_chapter),
                top_k=top_k,
                min_relevance=min_relevance,
            )

            return [
                {
                    "chapter_number": r.chapter_number,
                    "event_summary": r.event_summary,
                    "scene_index": r.scene_index,
                    "relevance_score": r.relevance_score,
                    "characters_involved": r.characters_involved[:5]
                    if r.characters_involved
                    else [],
                    "timestamp_in_story": r.timestamp_in_story or "",
                }
                for r in results
            ]

        except Exception as exc:
            _log.warning("Failed to search relevant history: %s", exc)
            return []

    @staticmethod
    def _tokenize_query_for_fallback(query: str) -> list[str]:
        tokens = [
            token.strip().lower()
            for token in re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", str(query or ""))
            if token and len(token.strip()) >= 2
        ]
        deduped: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            if token in seen:
                continue
            seen.add(token)
            deduped.append(token)
        return deduped

    def search_relevant_history_sync(
        self,
        query: str,
        current_chapter: int,
        lookback: int = 5,
        top_k: int = 5,
        min_relevance: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Synchronous wrapper for relevant-history search.

        Used by synchronous context-building paths where awaiting is not possible.
        If called inside an active event loop, falls back to temporal retrieval
        with lightweight keyword scoring.
        """
        return _search_relevant_history_sync_impl(
            episodic_memory=self._episodic_memory,
            async_search=self.search_relevant_history,
            query=query,
            current_chapter=current_chapter,
            lookback=lookback,
            top_k=top_k,
            min_relevance=min_relevance,
            logger=_log,
        )

    def get_relationship_evolution(
        self,
        character_name: str,
        current_chapter: int,
        lookback: int = 10,
    ) -> list[dict[str, Any]]:
        """Get relationship evolution for a character across recent chapters.

        Reads from canon state snapshots to build a timeline of how the
        character's relationships have evolved (trust/tension shifts).

        Args:
            character_name: Name of the POV character.
            current_chapter: Current chapter being processed.
            lookback: Number of chapters to look back.

        Returns:
            List of relationship evolution entries, each with partner name,
            status changes, and shift events.
        """
        return _get_relationship_evolution_impl(
            storage=self._storage,
            project_id=self._project_id,
            character_name=character_name,
            current_chapter=current_chapter,
            lookback=lookback,
            logger=_log,
        )

    def get_character_history(
        self,
        character_name: str,
        current_chapter: int,
        lookback: int = 10,
    ) -> list[dict[str, Any]]:
        """Get character's historical state from recent chapters.

        Useful for character consistency checking in audit steps.

        Args:
            character_name: Name of the character to look up
            current_chapter: Current chapter being processed
            lookback: Number of chapters to look back

            Returns:
            List of character states from recent chapters
        """
        return _get_character_history_impl(
            storage=self._storage,
            project_id=self._project_id,
            character_name=character_name,
            current_chapter=current_chapter,
            lookback=lookback,
            logger=_log,
        )

    def should_generate_summary(self, chapter_number: int, chapter_text: str) -> bool:
        """Determine if a new summary should be generated for this chapter."""
        return _should_generate_summary_impl(
            summary_cache=self._summary_cache,
            summary_stats=self._summary_stats,
            chapter_number=chapter_number,
            chapter_text=chapter_text,
        )

    def _refresh_single_chapter_memory(self, chapter_number: int) -> None:
        """Refresh memory slices for one chapter before re-indexing updated content."""
        if self._episodic_memory:
            try:
                delete_one = getattr(self._episodic_memory, "delete_chapter_memory", None)
                if callable(delete_one):
                    delete_one(chapter_number)
            except Exception as exc:
                _log.warning(
                    "Failed to refresh episodic chapter memory | chapter=%d | error=%s",
                    chapter_number,
                    exc,
                )

        if self._motif_tracker:
            try:
                delete_motifs = getattr(self._motif_tracker, "delete_chapter_motifs", None)
                if callable(delete_motifs):
                    # Build per-motif occurrence counts from motif_cache so the
                    # tracker can properly decrement even when _occurrence_log
                    # is empty (e.g. after deserialization).
                    cache_occ_counts: dict[str, int] | None = None
                    cached_entries = self._motif_cache.get(chapter_number)
                    if cached_entries and isinstance(cached_entries, list):
                        cache_occ_counts = {}
                        for entry in cached_entries:
                            mid = ""
                            if isinstance(entry, dict):
                                mid = str(entry.get("motif_id", "") or "").strip()
                            else:
                                mid = str(getattr(entry, "motif_id", "") or "").strip()
                            if mid:
                                cache_occ_counts[mid] = cache_occ_counts.get(mid, 0) + 1
                    delete_motifs(chapter_number, cache_occurrence_counts=cache_occ_counts)
            except Exception as exc:
                _log.warning(
                    "Failed to refresh motif chapter memory | chapter=%d | error=%s",
                    chapter_number,
                    exc,
                )

        if self._expression_memory:
            try:
                self._expression_memory.delete_chapter(chapter_number)
            except Exception as exc:
                _log.debug(
                    "Failed to refresh expression channel memory | chapter=%d | error=%s",
                    chapter_number,
                    exc,
                )

        if self._style_rule_tracker is not None:
            try:
                self._style_rule_tracker.delete_chapter_rules(chapter_number)
            except Exception as exc:
                _log.debug(
                    "Failed to refresh style-rule tracker | chapter=%d | error=%s",
                    chapter_number,
                    exc,
                )

        self._summary_cache.pop(chapter_number, None)
        self._motif_cache.pop(chapter_number, None)
        self._chapter_content_hash.pop(chapter_number, None)

    def should_extract_motifs(self, chapter_number: int, text_length: int) -> bool:
        """Determine if motifs should be extracted for this chapter."""
        if text_length < 500:
            _log.debug(
                "should_extract_motifs | chapter=%d | SKIP (text too short: %d)",
                chapter_number,
                text_length,
            )
            return False

        if self._motif_tracker is None:
            _log.debug(
                "should_extract_motifs | chapter=%d | SKIP (motif_tracker is None)",
                chapter_number,
            )
            return False

        has_tracker_data = False
        has_chapter_motifs = getattr(self._motif_tracker, "has_chapter_motifs", None)
        if callable(has_chapter_motifs):
            try:
                has_tracker_data = bool(has_chapter_motifs(chapter_number))
            except Exception:
                has_tracker_data = False
        else:
            chapter_motifs = getattr(self._motif_tracker, "_chapter_motifs", {})
            if isinstance(chapter_motifs, dict):
                has_tracker_data = bool(chapter_motifs.get(chapter_number))

        if has_tracker_data:
            _log.debug(
                "should_extract_motifs | chapter=%d | SKIP (already has tracker data)",
                chapter_number,
            )
            return False

        # Check if _motif_cache already has data for this chapter
        cache_has_data = bool(self._motif_cache.get(chapter_number))
        _log.info(
            "should_extract_motifs | chapter=%d | text_len=%d | tracker_has_data=%s | cache_has_data=%s | will_extract=%s",
            chapter_number,
            text_length,
            has_tracker_data,
            cache_has_data,
            not cache_has_data or len(self._motif_cache.get(chapter_number, [])) == 0,
        )

        # ``_motif_cache`` may contain prefilled occurrences (e.g. from
        # CriticAgent cache warmup) before tracker stats are recorded.
        # In that case we still run extract_from_chapter(), which will reuse
        # the cached LLM extraction and rebuild tracker counters.
        return True

    def index_chapter(
        self,
        chapter_number: int,
        text: str,
        creative_report_text: str = "",
        *,
        async_summarize: bool = True,
        async_motifs: bool = True,
    ) -> None:
        """Index a chapter in all memory systems.

        Args:
            chapter_number: Chapter number
            text: Chapter text
            creative_report_text: Creative report text
            async_summarize: Whether to generate summary asynchronously
            async_motifs: Whether to extract motifs asynchronously
        """
        summary_source_hash = self._compute_summary_source_hash(text)
        previous_hash = str(self._chapter_content_hash.get(chapter_number, "") or "")
        if chapter_number <= self._last_indexed_chapter:
            if previous_hash and previous_hash == summary_source_hash:
                # Content unchanged — still need to check if motifs are missing.
                motifs_missing = (
                    async_motifs
                    and self._motif_tracker is not None
                    and self.should_extract_motifs(chapter_number, len(text))
                )
                if not motifs_missing:
                    _log.debug(
                        "Skipping index for chapter %d (content hash unchanged)", chapter_number
                    )
                    return
                _log.info(
                    "Content hash unchanged for chapter %d but motifs missing — extracting motifs only",
                    chapter_number,
                )
                self._schedule_motif_extraction(chapter_number, text, chapter_outline=None)
                return
            if previous_hash and previous_hash != summary_source_hash:
                _log.info("Refreshing chapter %d memory due content hash change", chapter_number)
                self._refresh_single_chapter_memory(chapter_number)
                self._summary_stats["reindexed"] = self._summary_stats.get("reindexed", 0) + 1
            else:
                # No hash baseline (e.g. after restart) — fall through to re-index
                _log.info(
                    "Re-indexing chapter %d (no hash baseline after restart)",
                    chapter_number,
                )

        if len(text) < 1000 and chapter_number in self._summary_cache:
            self._summary_cache.pop(chapter_number, None)
            self._summary_stats["invalidated_short"] = (
                self._summary_stats.get("invalidated_short", 0) + 1
            )

        self._chapter_content_hash[chapter_number] = summary_source_hash

        # Emit indexing started event
        self._emit_progress(
            "indexing_started",
            {
                "chapter": chapter_number,
                "text_length": len(text),
                "report_length": len(creative_report_text),
            },
        )

        _log.info(
            "Indexing chapter %d | text_len=%d | report_len=%d",
            chapter_number,
            len(text),
            len(creative_report_text),
        )

        # Index episodic memory
        if self._episodic_memory:
            try:
                scheduled = self._schedule_background_task(
                    self._episodic_memory.index_chapter_outcome(
                        chapter_number=chapter_number,
                        event_summary=creative_report_text or text[:200],
                        full_text=text,
                    ),
                    tag="episodic_index",
                )
                if scheduled:
                    _log.debug("Scheduled episodic memory indexing for chapter %d", chapter_number)
                else:
                    _log.warning(
                        "Could not schedule episodic indexing for chapter %d (no event loop)",
                        chapter_number,
                    )

                # Emit episodic memory done event
                self._emit_progress(
                    "episodic_done",
                    {
                        "chapter": chapter_number,
                        "success": scheduled,
                    },
                )
            except Exception as exc:
                _log.error("Failed to index episodic memory: %s", exc, exc_info=True)
                self._emit_progress(
                    "episodic_done",
                    {
                        "chapter": chapter_number,
                        "success": False,
                        "error": str(exc),
                    },
                )

        # Extract motifs
        motifs_extracted = False
        motifs_count = 0
        if (
            async_motifs
            and self._motif_tracker
            and self.should_extract_motifs(chapter_number, len(text))
        ):
            motifs_extracted = self._schedule_motif_extraction(
                chapter_number, text, chapter_outline=None
            )

        # Schedule async summary generation
        if (
            async_summarize
            and self._summary_service
            and self.should_generate_summary(chapter_number, text)
        ):
            scheduled = self._schedule_background_task(
                self._generate_summary_async(
                    chapter=chapter_number,
                    text=text,
                    report=creative_report_text,
                    source_hash=summary_source_hash,
                ),
                tag="summary_generate",
            )
            if scheduled:
                _log.debug("Scheduled async summary generation for chapter %d", chapter_number)

                # Emit summary scheduled event
                self._emit_progress(
                    "summary_scheduled",
                    {
                        "chapter": chapter_number,
                        "success": True,
                    },
                )
            else:
                _log.error("Failed to schedule summary generation")
                self._emit_progress(
                    "summary_scheduled",
                    {
                        "chapter": chapter_number,
                        "success": False,
                        "error": "failed_to_schedule",
                    },
                )

        self._last_indexed_chapter = max(self._last_indexed_chapter, chapter_number)

        _log.info(
            "Chapter %d indexed successfully | motifs=%s | last_indexed=%d",
            chapter_number,
            motifs_extracted,
            self._last_indexed_chapter,
        )

        # Emit indexing complete event
        self._emit_progress(
            "indexing_complete",
            {
                "chapter": chapter_number,
                "motifs_extracted": motifs_extracted,
                "motifs_count": motifs_count,
                "summary_scheduled": async_summarize and self._summary_service is not None,
                "last_indexed": self._last_indexed_chapter,
            },
        )

    async def _generate_summary_async(
        self,
        *,
        chapter: int,
        text: str,
        report: str,
        source_hash: str,
    ) -> None:
        """Generate summary asynchronously under the MemoryContext lock."""
        await _summary_generate_async(
            self,
            chapter=chapter,
            text=text,
            report=report,
            source_hash=source_hash,
        )

    async def _extract_motifs_async(
        self,
        chapter_number: int,
        text: str,
        chapter_outline: dict[str, Any] | None = None,
    ) -> None:
        """Extract motifs asynchronously and store results in cache."""
        await _motif_extract_async(self, chapter_number, text, chapter_outline)

    def _schedule_motif_extraction(
        self,
        chapter_number: int,
        text: str,
        chapter_outline: dict[str, Any] | None = None,
    ) -> bool:
        """Schedule motif extraction as a background task."""
        return _motif_schedule_extraction(self, chapter_number, text, chapter_outline)

    def get_memory_context_for_prompt(
        self,
        current_chapter: int,
        include_motifs: bool = True,
        include_summaries: bool = True,
        summary_granularity: str = "chapter",
        include_critiques: bool = True,
    ) -> dict[str, Any]:
        """Get memory context formatted for prompt injection.

        Args:
            current_chapter: Current chapter number
            include_motifs: Whether to include motif data
            include_summaries: Whether to include summaries
            summary_granularity: Granularity level for summaries
            include_critiques: Whether to include recent critical/high critiques

        Returns:
            Dictionary for prompt injection
        """
        return build_prompt_memory_context(
            current_chapter=current_chapter,
            settings=self.settings,
            motif_tracker=self._motif_tracker,
            summary_service=self._summary_service,
            episodic_memory=self._episodic_memory,
            include_motifs=include_motifs,
            include_summaries=include_summaries,
            summary_granularity=summary_granularity,
            include_critiques=include_critiques,
            legacy_summary_getter=self._legacy_summary_context_for_prompt,
            critique_context_builder=self._build_critique_context_for_prompt,
            logger=_log,
        )

    async def aget_memory_context_for_prompt(
        self,
        current_chapter: int,
        include_motifs: bool = True,
        include_summaries: bool = True,
        summary_granularity: str = "chapter",
        include_critiques: bool = True,
    ) -> dict[str, Any]:
        """Async prompt context with read-only story-kernel projections."""
        context = self.get_memory_context_for_prompt(
            current_chapter=current_chapter,
            include_motifs=include_motifs,
            include_summaries=include_summaries,
            summary_granularity=summary_granularity,
            include_critiques=include_critiques,
        )
        if self._foreshadow_reminder is not None:
            try:
                due = await self._foreshadow_reminder.get_due(current_chapter)
                context["foreshadow_due"] = self._serialize_due_foreshadows(due)
            except Exception as exc:
                _log.debug(
                    "memory_prompt_foreshadow_due_failed | chapter=%d | error=%s",
                    current_chapter,
                    exc,
                )
        return context

    @staticmethod
    def _serialize_due_foreshadows(raw: Any, *, limit: int = 6) -> list[dict[str, Any]]:
        return _serialize_due_foreshadow_payloads(raw, limit=limit)

    @staticmethod
    def _serialize_relationship_projection(raw: Any, *, limit: int = 6) -> list[dict[str, Any]]:
        return _serialize_relationship_projection_payloads(raw, limit=limit)

    async def get_character_context(
        self,
        character_id: str,
        *,
        current_chapter: int | None = None,
        query: str = "",
        lookback: int = 8,
        top_k: int = 5,
        min_relevance: float = 0.5,
        include_episodic: bool = True,
        include_motifs: bool = True,
        include_summaries: bool = False,
    ) -> dict[str, Any]:
        """Return memory context that is explicitly scoped to one character.

        This is a conservative first-class API for POV/character-aware prompt
        assembly.  It only includes subsystems that can be filtered by
        character.  Chapter summaries are intentionally omitted unless the
        summary service exposes its own character-scoped method.
        """
        return await build_character_memory_context(
            character_id,
            entity_knowledge_service=self._entity_knowledge_service,
            relationship_query_service=self._relationship_query_service,
            episodic_memory=self._episodic_memory,
            motif_tracker=self._motif_tracker,
            summary_service=self._summary_service,
            current_chapter=current_chapter,
            query=query,
            lookback=lookback,
            top_k=top_k,
            min_relevance=min_relevance,
            include_episodic=include_episodic,
            include_motifs=include_motifs,
            include_summaries=include_summaries,
            logger=_log,
        )

    @staticmethod
    def _serialize_character_episodic_results(
        results: Any,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        return _serialize_character_episodic_payloads(results, limit=limit)

    def _serialize_character_motifs(self, character: str) -> list[dict[str, Any]]:
        return _serialize_character_motif_payloads(self._motif_tracker, character)

    def _legacy_summary_context_for_prompt(self, current_chapter: int) -> str:
        return _prompt_legacy_summary_context(self, current_chapter)

    def get_layered_context(
        self,
        *,
        current_chapter: int,
        budget: MemoryBudgetConfig | None = None,
        project_identity: str = "",
        query: str = "",
    ) -> dict[str, str]:
        """Assemble memory context with explicit per-layer token budgets.

        Inspired by MemPalace's 4-layer MemoryStack (L0-L3), this method
        builds context in four layers, each with its own character budget:

        - **L0 (identity)**: Project identity — title, genre, style, core premise
        - **L1 (core_memory)**: Recent chapter summaries + key character states
        - **L2 (on_demand)**: Semantically relevant historical events
        - **L3 (deep_search)**: Full semantic search for specific queries

        Args:
            current_chapter: Current chapter being generated.
            budget: Explicit token budgets per layer.  Falls back to defaults.
            project_identity: L0 identity text (book title, genre, style, etc.).
            query: Natural language query for L3 deep search.

        Returns:
            Dict with keys ``L0_identity``, ``L1_core_memory``,
            ``L2_on_demand``, ``L3_deep_search``. Selected content is preserved
            completely; budgets report pressure but never authorize prefix
            truncation. Empty strings indicate a layer has no data.
        """
        return build_layered_memory_context(
            current_chapter=current_chapter,
            budget=budget,
            project_identity=project_identity,
            query=query,
            settings=self.settings,
            summary_service=self._summary_service,
            episodic_memory=self._episodic_memory,
            volume_finder=self._find_volume_for_chapter,
            relevant_history_search=self.search_relevant_history_sync,
            logger=_log,
        )

    @staticmethod
    def _build_critique_context_for_prompt(
        current_chapter: int,
        *,
        episodic_memory: Any = None,
        max_chars: int = 500,
        include_lessons: bool = True,
        max_entries: int = 8,
        distance_decay: int = 15,
    ) -> str:
        """Build a compact critique context string from recent critical/high entries."""
        return _prompt_build_critique_context(
            current_chapter,
            episodic_memory=episodic_memory,
            max_chars=max_chars,
            include_lessons=include_lessons,
            max_entries=max_entries,
            distance_decay=distance_decay,
        )

    def get_memory_status_for_ui(self) -> dict[str, Any]:
        """Get memory status formatted for UI consumption.

        This method returns the memory state in a format that can be directly
        used by the UI layer (Desktop application) to display memory information.

        Returns:
            Dictionary with keys: indexed_chapters, motifs, motif_suggestions,
            repetition_warnings, memory_module_status
        """
        motifs: list[dict[str, Any]] = []
        motif_suggestions: list[dict[str, Any]] = []
        repetition_warnings: list[dict[str, Any]] = []

        if self._motif_tracker:
            try:
                for motif_id, motif in self._motif_tracker.motifs.items():
                    motifs.append(
                        {
                            "motif_id": motif_id,
                            "name": motif.name,
                            "category": motif.category.value
                            if hasattr(motif.category, "value")
                            else str(motif.category),
                            "description": motif.description,
                            "occurrence_count": motif.occurrence_count,
                            "last_chapter": motif.last_appearance_chapter,
                            "first_chapter": motif.first_appearance_chapter,
                            "category_confidence": (motif.metadata or {}).get(
                                "category_confidence"
                            ),
                            "category_reason": (motif.metadata or {}).get("category_reason", ""),
                            "secondary_categories": (motif.metadata or {}).get(
                                "secondary_categories",
                                [],
                            ),
                            "classification_status": (motif.metadata or {}).get(
                                "category_status",
                                "",
                            ),
                            "motif_role": (motif.metadata or {}).get("motif_role", "unknown"),
                            "importance_score": (motif.metadata or {}).get("importance_score"),
                            "retired_reason": (motif.metadata or {}).get("retired_reason", ""),
                        }
                    )

                suggestions = self._motif_tracker.get_suggestions_for_chapter(
                    current_chapter=self._last_indexed_chapter,
                    current_context="",
                    min_chapters_since=getattr(self.settings, "motif_suggestion_min_chapters", 5),
                    min_occurrences=getattr(self.settings, "motif_suggestion_min_occurrences", 3),
                )
                motif_suggestions = [
                    {
                        "motif_id": s.motif_id if hasattr(s, "motif_id") else "",
                        "motif_name": s.motif_name if hasattr(s, "motif_name") else "",
                        "suggestion": s.reason if hasattr(s, "reason") else str(s),
                        "priority": s.priority.value
                        if hasattr(s.priority, "value")
                        else str(getattr(s, "priority", "medium")),
                    }
                    for s in suggestions[:5]
                ]
            except Exception as exc:
                _log.warning("Failed to get motif data for UI: %s", exc)
        chapter_motifs: dict[str, list[str]] = {}
        if self._motif_tracker:
            try:
                raw_chapter_motifs = getattr(self._motif_tracker, "_chapter_motifs", {})
                if isinstance(raw_chapter_motifs, dict):
                    chapter_motifs = {
                        str(chapter): sorted(str(motif_id) for motif_id in motif_ids)
                        for chapter, motif_ids in raw_chapter_motifs.items()
                        if motif_ids
                    }
            except Exception as exc:
                _log.warning("Failed to get chapter motif map for UI: %s", exc)

        episodic_count = 0
        unresolved_questions: list[str] = []
        if self._episodic_memory:
            try:
                episodic_count = len(getattr(self._episodic_memory, "_chapter_events", {}))
            except Exception:
                episodic_count = 0
            try:
                raw_questions = getattr(self._episodic_memory, "_unresolved_questions", [])
                if isinstance(raw_questions, list):
                    unresolved_questions = [
                        str(item).strip() for item in raw_questions if str(item).strip()
                    ][:20]
            except Exception:
                unresolved_questions = []

        outline_stats: dict[str, Any] = {}
        vector_store_backend = ""
        if self._episodic_memory:
            try:
                if hasattr(self._episodic_memory, "get_memory_stats"):
                    outline_stats = self._episodic_memory.get_memory_stats()
                    vector_store_backend = str(outline_stats.get("vector_store_backend", "") or "")
            except Exception:
                outline_stats = {}
            if not vector_store_backend:
                vector_store_backend = str(
                    getattr(self._episodic_memory, "vector_store_backend", "") or ""
                )
            if vector_store_backend == "uninitialized":
                vector_store_backend = str(
                    getattr(self._episodic_memory, "_vector_store_backend_requested", "")
                    or vector_store_backend
                )

        return {
            "indexed_chapters": episodic_count or self._last_indexed_chapter,
            "motifs": motifs,
            "motif_suggestions": motif_suggestions,
            "repetition_warnings": repetition_warnings,
            "chapter_motifs": chapter_motifs,
            "unresolved_questions": unresolved_questions,
            "memory_module_status": {
                "episodic_enabled": self._episodic_memory is not None,
                "summary_enabled": self._summary_service is not None,
                "motif_enabled": self._motif_tracker is not None,
                "compression_enabled": self._compression_service is not None,
                "critic_enabled": self._critic_agent is not None,
                "expression_memory_enabled": self._expression_memory is not None,
                "entity_knowledge_enabled": self._entity_knowledge_service is not None,
                "relationship_query_enabled": self._relationship_query_service is not None,
                "foreshadow_reminder_enabled": self._foreshadow_reminder is not None,
                "expression_observation_count": int(
                    getattr(self._expression_memory, "observation_count", 0) or 0
                ),
                "embedding_mode": self._embedding_mode,
                "vector_store_backend": vector_store_backend,
                "vector_store_initialization_state": str(
                    outline_stats.get("vector_store_initialization_state", "") or ""
                ),
                "vector_store_path": str(outline_stats.get("vector_store_path", "") or ""),
                "vector_store_dimension": int(outline_stats.get("vector_store_dimension", 0) or 0),
                "vector_store_index_type": str(
                    outline_stats.get("vector_store_index_type", "") or ""
                ),
                "vector_store_vector_count": int(
                    outline_stats.get("vector_store_vector_count", 0) or 0
                ),
                "embedding_stats": dict(outline_stats.get("embedding_stats", {}) or {}),
            },
            "cached_summaries": len(self._summary_cache),
            "summary_hash_bound": sum(
                1 for entry in self._summary_cache.values() if entry.get("source_hash")
            ),
            "chapter_hash_tracked": len(self._chapter_content_hash),
            "summary_stats": dict(self._summary_stats),
            "last_indexed_chapter": self._last_indexed_chapter,
            "outline_stats": outline_stats,
        }

    def get_status_summary(self) -> dict[str, Any]:
        """Get a summary of memory module status."""
        episodic_count = 0
        vector_store_backend = ""
        vector_store_path = ""
        vector_store_dimension = 0
        vector_store_index_type = ""
        vector_store_vector_count = 0
        vector_store_initialization_state = ""
        embedding_stats: dict[str, Any] = {}
        if self._episodic_memory:
            try:
                episodic_count = len(getattr(self._episodic_memory, "_chapter_events", {}))
            except Exception:
                episodic_count = 0
            vector_store_backend = str(
                getattr(self._episodic_memory, "vector_store_backend", "") or ""
            )
            if vector_store_backend == "uninitialized":
                vector_store_backend = str(
                    getattr(self._episodic_memory, "_vector_store_backend_requested", "")
                    or vector_store_backend
                )
            try:
                stats = self._episodic_memory.get_memory_stats()
            except Exception:
                stats = {}
            vector_store_path = str(stats.get("vector_store_path", "") or "")
            vector_store_dimension = int(stats.get("vector_store_dimension", 0) or 0)
            vector_store_index_type = str(stats.get("vector_store_index_type", "") or "")
            vector_store_vector_count = int(stats.get("vector_store_vector_count", 0) or 0)
            vector_store_initialization_state = str(
                stats.get("vector_store_initialization_state", "") or ""
            )
            embedding_stats = dict(stats.get("embedding_stats", {}) or {})

        motif_count = 0
        if self._motif_tracker:
            try:
                motif_count = len(getattr(self._motif_tracker, "_motifs", {}))
            except Exception:
                motif_count = 0

        return {
            "project_id": self._project_id,
            "episodic_indexed_chapters": episodic_count,
            "cached_summaries": len(self._summary_cache),
            "summary_hash_bound": sum(
                1 for entry in self._summary_cache.values() if entry.get("source_hash")
            ),
            "chapter_hash_tracked": len(self._chapter_content_hash),
            "summary_stats": dict(self._summary_stats),
            "cached_motifs": len(self._motif_cache),
            "motif_count": motif_count,
            "last_indexed_chapter": self._last_indexed_chapter,
            "vector_store_backend": vector_store_backend,
            "vector_store_path": vector_store_path,
            "vector_store_dimension": vector_store_dimension,
            "vector_store_index_type": vector_store_index_type,
            "vector_store_vector_count": vector_store_vector_count,
            "vector_store_initialization_state": vector_store_initialization_state,
            "expression_memory_enabled": self._expression_memory is not None,
            "entity_knowledge_enabled": self._entity_knowledge_service is not None,
            "relationship_query_enabled": self._relationship_query_service is not None,
            "foreshadow_reminder_enabled": self._foreshadow_reminder is not None,
            "expression_observation_count": int(
                getattr(self._expression_memory, "observation_count", 0) or 0
            ),
            "embedding_mode": self._embedding_mode,
            "embedding_stats": embedding_stats,
        }

