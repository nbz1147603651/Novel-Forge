"""AuditCoordinator — unified context preparation for memory-enhanced auditing.

This module provides a centralized coordinator that:
1. Prepares memory-enhanced context from multiple sources
2. Provides semantic search capabilities for cross-chapter awareness
3. Caches results for potential reuse

Note: The primary auditing is handled by CriticAgent. This module serves as
a utility for preparing structured context when needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from novel_forge.obs.logger import get_logger

if TYPE_CHECKING:
    from novel_forge.memory.integration import MemoryContext

_logger = get_logger("memory.audit_coordinator")


@dataclass
class AuditContext:
    """Unified context bundle for memory-enhanced auditing.

    This dataclass aggregates all memory-enhanced context for
    cross-chapter awareness.
    """

    chapter_number: int
    project_id: str = ""

    prev_chapter_summary: str | None = None
    prev_chapter_exit_state: dict[str, Any] | None = None
    relevant_history: list[dict[str, Any]] = field(default_factory=list)

    character_histories: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    unresolved_questions: list[dict[str, Any]] = field(default_factory=list)
    similar_past_chapters: list[dict[str, Any]] = field(default_factory=list)

    motif_context: dict[str, Any] | None = None
    motif_warnings: list[dict[str, Any]] = field(default_factory=list)

    style_rule_context: dict[str, Any] | None = None
    style_rule_warnings: list[dict[str, Any]] = field(default_factory=list)

    entity_context: dict[str, dict[str, Any]] = field(default_factory=dict)
    relationship_context: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    foreshadow_due: list[dict[str, Any]] = field(default_factory=list)

    memory_available: bool = True
    memory_status: dict[str, Any] = field(default_factory=dict)

    def has_relevant_history(self) -> bool:
        """Check if relevant historical context is available."""
        return len(self.relevant_history) > 0

    def has_character_history(self) -> bool:
        """Check if character history is available."""
        return len(self.character_histories) > 0

    def has_motif_context(self) -> bool:
        """Check if motif context is available."""
        return self.motif_context is not None

    def get_summary_for_prompt(self) -> str:
        """Generate a compact summary of available memory context."""
        parts = []

        if self.prev_chapter_summary:
            parts.append(f"上一章摘要：{self.prev_chapter_summary[:200]}...")

        if self.prev_chapter_exit_state:
            location = self.prev_chapter_exit_state.get("location", "未知")
            time = self.prev_chapter_exit_state.get("time_marker", "")
            pov = self.prev_chapter_exit_state.get("pov", "")
            parts.append(f"上章状态：地点={location}，时间={time}，视角={pov}")

        if self.relevant_history:
            events = [f"第{r['chapter_number']}章: {r['event_summary'][:50]}..." for r in self.relevant_history[:3]]
            parts.append(f"相关历史事件：{'；'.join(events)}")

        if self.unresolved_questions:
            questions = [f"第{q['chapter_number']}章的悬念" for q in self.unresolved_questions[:3]]
            parts.append(f"待解决的悬念：{'；'.join(questions)}")

        if self.motif_warnings:
            warnings = [w.get("motif_name", "未知母题") for w in self.motif_warnings if w.get("severity") in ("high", "critical")]
            if warnings:
                parts.append(f"母题重复警告：{', '.join(warnings)}")

        if self.style_rule_warnings:
            warnings = [
                w.get("motif_name", "未知技法")
                for w in self.style_rule_warnings
                if w.get("severity") in ("high", "critical")
            ]
            if warnings:
                parts.append(f"技法模板化警告：{', '.join(warnings)}")

        if self.style_rule_context and self.style_rule_context.get("style_rule_guidance"):
            parts.append(self.style_rule_context["style_rule_guidance"])

        if self.entity_context:
            names = list(self.entity_context.keys())[:5]
            parts.append(f"当前实体状态：{', '.join(names)}")

        if self.relationship_context:
            names = list(self.relationship_context.keys())[:3]
            parts.append(f"关系上下文：{', '.join(names)}")

        if self.foreshadow_due:
            descriptions = [
                str(item.get("description", ""))[:40]
                for item in self.foreshadow_due[:3]
                if item.get("description")
            ]
            if descriptions:
                parts.append(f"待回收伏笔：{'；'.join(descriptions)}")

        return "\n".join(parts) if parts else ""


class AuditCoordinator:
    """Coordinates memory-enhanced context preparation.

    Provides a centralized way to prepare context from memory services.
    The primary auditing is handled by CriticAgent; this class serves as
    a utility for quick context retrieval when needed.

    Usage:
        coordinator = AuditCoordinator(memory_context)
        context = await coordinator.prepare_audit_context(chapter_num, chapter_text)
    """

    # Warning suppression constants
    _MOTIF_WARNING_COOLDOWN = 3  # Don't repeat same motif warning within N chapters
    _HISTORY_DECAY_FACTOR = 0.7  # Decay factor for old history events
    _HISTORY_MIN_RELEVANCE = 0.3  # Minimum decayed relevance to keep

    def __init__(
        self,
        memory_context: MemoryContext,
        *,
        enable_caching: bool = True,
    ) -> None:
        """Initialize the audit coordinator.

        Args:
            memory_context: MemoryContext instance for data retrieval
            enable_caching: Whether to cache results (default True)
        """
        self._memory = memory_context
        self._enable_caching = enable_caching
        self._cache: dict[str, dict[int, Any]] = {}

        # Track warning suppression to prevent repetitive warnings across chapters
        # Format: {motif_name: last_chapter_warned}
        self._warning_suppression: dict[str, int] = {}

        # Track shown history events to prevent duplicates across chapters
        # Format: set of (chapter_number, event_summary_hash)
        self._shown_history_events: set[tuple[int, str]] = set()

    @property
    def memory_context(self) -> MemoryContext:
        """Access the underlying memory context."""
        return self._memory

    async def prepare_audit_context(
        self,
        chapter_number: int,
        chapter_text: str = "",
        chapter_type: str = "",
        active_characters: list[str] | None = None,
    ) -> AuditContext:
        """Prepare unified memory-enhanced context for auditing.

        Aggregates data from all memory services into a structured format.

        Args:
            chapter_number: Current chapter being audited
            chapter_text: Full text of the chapter (optional, for motif checks)
            chapter_type: Type of chapter (e.g., "action", "dialogue", "transition")
            active_characters: List of character names active in this chapter

        Returns:
            AuditContext with all memory-enhanced data
        """
        import time
        start_time = time.monotonic()

        _logger.info(
            "audit_context_start | chapter=%d | text_length=%d | chapter_type=%s | characters=%d",
            chapter_number,
            len(chapter_text),
            chapter_type,
            len(active_characters) if active_characters else 0,
        )

        ctx = AuditContext(
            chapter_number=chapter_number,
            project_id=self._memory.project_id,
        )

        if not self._memory:
            ctx.memory_available = False
            _logger.warning("audit_context_no_memory | chapter=%d", chapter_number)
            return ctx

        try:
            prev_chapter = chapter_number - 1

            ctx.prev_chapter_summary = self._memory.get_cached_summary(prev_chapter)
            ctx.prev_chapter_exit_state = self._memory.get_chapter_exit_state(prev_chapter)

            _logger.debug(
                "audit_context_fetch | chapter=%d | prev_summary=%s | prev_exit=%s",
                chapter_number,
                ctx.prev_chapter_summary is not None,
                ctx.prev_chapter_exit_state is not None,
            )

            # Fetch relevant history with deduplication and decay
            raw_history = await self._memory.search_relevant_history(
                query="角色状态变化 关键事件 重要情节点",
                current_chapter=chapter_number,
                lookback=5,
                top_k=5,
            )
            ctx.relevant_history = self._deduplicate_and_decay_history(
                raw_history,
                current_chapter=chapter_number,
            )

            _logger.debug(
                "audit_context_history | chapter=%d | raw=%d | filtered=%d",
                chapter_number,
                len(raw_history),
                len(ctx.relevant_history),
            )

            if active_characters:
                for char_name in active_characters[:10]:
                    char_history = self._memory.get_character_history(
                        character_name=char_name,
                        current_chapter=chapter_number,
                        lookback=10,
                    )
                    if char_history:
                        ctx.character_histories[char_name] = char_history

                _logger.debug(
                    "audit_context_characters | chapter=%d | fetched=%d",
                    chapter_number,
                    len(ctx.character_histories),
                )

            entity_knowledge = getattr(self._memory, "entity_knowledge_service", None)
            relationship_query = getattr(self._memory, "relationship_query_service", None)
            if active_characters and entity_knowledge is not None:
                for char_name in active_characters[:10]:
                    try:
                        current_entity = await entity_knowledge.get_current(char_name)
                    except Exception as exc:
                        _logger.debug(
                            "audit_context_entity_lookup_failed | chapter=%d | entity=%s | error=%s",
                            chapter_number,
                            char_name,
                            exc,
                        )
                        current_entity = None
                    if isinstance(current_entity, dict) and current_entity:
                        ctx.entity_context[char_name] = current_entity

                    if relationship_query is None:
                        continue
                    lookup_key = (
                        str(current_entity.get("entity_id", "") or char_name)
                        if isinstance(current_entity, dict)
                        else char_name
                    )
                    try:
                        relationships = await relationship_query.get_relationships(lookup_key)
                    except Exception as exc:
                        _logger.debug(
                            "audit_context_relationship_lookup_failed | chapter=%d | "
                            "entity=%s | error=%s",
                            chapter_number,
                            lookup_key,
                            exc,
                        )
                        relationships = []
                    if relationships:
                        ctx.relationship_context[char_name] = relationships[:8]

                _logger.debug(
                    "audit_context_projection_entities | chapter=%d | entities=%d | relationships=%d",
                    chapter_number,
                    len(ctx.entity_context),
                    len(ctx.relationship_context),
                )

            foreshadow_reminder = getattr(self._memory, "foreshadow_reminder", None)
            if foreshadow_reminder is not None:
                try:
                    due = await foreshadow_reminder.get_due(chapter_number)
                except Exception as exc:
                    _logger.debug(
                        "audit_context_foreshadow_due_failed | chapter=%d | error=%s",
                        chapter_number,
                        exc,
                    )
                    due = []
                if isinstance(due, list):
                    ctx.foreshadow_due = [item for item in due if isinstance(item, dict)][:10]

            ctx.unresolved_questions = await self._memory.search_relevant_history(
                query="未解决的悬念 因果链 问题",
                current_chapter=chapter_number,
                lookback=20,
                top_k=5,
                min_relevance=0.4,
            )

            _logger.debug(
                "audit_context_questions | chapter=%d | unresolved=%d",
                chapter_number,
                len(ctx.unresolved_questions),
            )

            if chapter_type:
                ctx.similar_past_chapters = await self._memory.search_relevant_history(
                    query=f"章节类型: {chapter_type}",
                    current_chapter=chapter_number,
                    lookback=chapter_number - 1,
                    top_k=3,
                )

                _logger.debug(
                    "audit_context_similar | chapter=%d | type=%s | found=%d",
                    chapter_number,
                    chapter_type,
                    len(ctx.similar_past_chapters),
                )

            if self._memory.motif_tracker and chapter_text:
                try:
                    raw_related_lookback = getattr(
                        getattr(self._memory, "settings", object()),
                        "memory_motif_related_lookback_chapters",
                        None,
                    )
                    lookback = max(
                        0,
                        int(2 if raw_related_lookback is None else raw_related_lookback),
                    )
                    ctx.motif_context = self._memory.motif_tracker.get_motifs_for_prompt(
                        current_chapter=chapter_number,
                        related_lookback_chapters=lookback,
                        chapter_text=chapter_text,
                    )

                    if len(chapter_text) >= 500:
                        lookback = getattr(
                            self._memory.settings,
                            "motif_repetition_lookback_chapters",
                            None,
                        )
                        if lookback is None:
                            lookback = getattr(
                                self._memory.settings,
                                "memory_motif_related_lookback_chapters",
                                None,
                            )
                        lookback = 5 if lookback is None else lookback
                        repetition_gap = getattr(
                            self._memory.settings,
                            "motif_repetition_recent_gap_chapters",
                            2,
                        ) or 2
                        repetitions = await self._memory.motif_tracker.check_unintentional_repetition(
                            chapter_number=chapter_number,
                            chapter_text=chapter_text,
                            lookback_chapters=lookback,
                            repetition_gap_chapters=repetition_gap,
                        )
                        raw_warnings = [
                            {
                                "motif_name": r.motif_name,
                                "chapter_number": r.chapter_number,
                                "previous_chapters": r.previous_chapters,
                                "severity": r.severity,
                                "suggestion": r.suggestion,
                            }
                            for r in repetitions
                        ]
                        # Apply deduplication and cooldown to prevent repetitive warnings
                        ctx.motif_warnings = self._deduplicate_motif_warnings(
                            raw_warnings,
                            current_chapter=chapter_number,
                        )

                        _logger.debug(
                            "audit_context_motifs | chapter=%d | active=%d | raw_warnings=%d | filtered_warnings=%d",
                            chapter_number,
                            len(ctx.motif_context.get("active_motifs", [])) if ctx.motif_context else 0,
                            len(raw_warnings),
                            len(ctx.motif_warnings),
                        )
                except Exception as exc:
                    _logger.warning("Failed to get motif context: %s", exc)

            if self._memory.style_rule_tracker and chapter_text:
                try:
                    ctx.style_rule_context = self._memory.style_rule_tracker.get_rules_for_prompt(
                        current_chapter=chapter_number,
                    )
                    if len(chapter_text) >= 500:
                        lookback = getattr(
                            self._memory.settings,
                            "style_rule_repetition_lookback_chapters",
                            5,
                        ) or 5
                        repetition_gap = getattr(
                            self._memory.settings,
                            "style_rule_repetition_recent_gap_chapters",
                            2,
                        ) or 2
                        repetitions = self._memory.style_rule_tracker.check_rule_repetition(
                            chapter_number=chapter_number,
                            chapter_text=chapter_text,
                            lookback_chapters=lookback,
                            repetition_gap_chapters=repetition_gap,
                        )
                        raw_warnings = [
                            {
                                # _deduplicate_motif_warnings keys on "motif_name";
                                # reuse it by populating motif_name with the rule.
                                "motif_name": f"{r.module_name}：{r.rule_name}",
                                "chapter_number": r.chapter_number,
                                "previous_chapters": r.previous_chapters,
                                "severity": r.severity,
                                "suggestion": r.suggestion,
                            }
                            for r in repetitions
                        ]
                        ctx.style_rule_warnings = self._deduplicate_motif_warnings(
                            raw_warnings,
                            current_chapter=chapter_number,
                        )
                        _logger.debug(
                            "audit_context_style_rules | chapter=%d | raw_warnings=%d | filtered=%d",
                            chapter_number,
                            len(raw_warnings),
                            len(ctx.style_rule_warnings),
                        )
                except Exception as exc:
                    _logger.warning("Failed to get style-rule context: %s", exc)

            ctx.memory_status = self._memory.get_status_summary()

            elapsed_ms = (time.monotonic() - start_time) * 1000

            _logger.info(
                "audit_context_done | chapter=%d | history=%d | characters=%d | questions=%d | warnings=%d | elapsed_ms=%.2f",
                chapter_number,
                len(ctx.relevant_history),
                len(ctx.character_histories),
                len(ctx.unresolved_questions),
                len(ctx.motif_warnings),
                elapsed_ms,
            )

        except Exception as exc:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            _logger.error(
                "audit_context_failed | chapter=%d | error=%s | elapsed_ms=%.2f",
                chapter_number,
                exc,
                elapsed_ms,
                exc_info=True,
            )
            ctx.memory_available = False

        return ctx

    def cache_result(
        self,
        audit_type: str,
        chapter_number: int,
        result: Any,
    ) -> None:
        """Cache an audit result for potential reuse."""
        if not self._enable_caching:
            return

        if audit_type not in self._cache:
            self._cache[audit_type] = {}

        self._cache[audit_type][chapter_number] = result
        _logger.debug("Cached %s result for chapter %d", audit_type, chapter_number)

    def get_cached_result(
        self,
        audit_type: str,
        chapter_number: int,
    ) -> Any | None:
        """Retrieve a cached result if available."""
        if not self._enable_caching:
            return None
        return self._cache.get(audit_type, {}).get(chapter_number)

    def clear_cache(self, audit_type: str | None = None) -> None:
        """Clear cached results."""
        if audit_type:
            self._cache.pop(audit_type, None)
        else:
            self._cache.clear()
        _logger.debug("Cleared audit cache: %s", audit_type or "all")

    def _deduplicate_motif_warnings(
        self,
        warnings: list[dict[str, Any]],
        current_chapter: int,
    ) -> list[dict[str, Any]]:
        """Deduplicate and apply cooldown to motif warnings.

        Prevents the same motif from generating warnings in consecutive chapters.
        Uses a cooldown period to suppress repetitive warnings.

        Args:
            warnings: Raw motif warnings from motif_tracker
            current_chapter: Current chapter number

        Returns:
            Filtered and aggregated warnings
        """
        if not warnings:
            return []

        # Clean up old suppression entries (older than cooldown period)
        self._warning_suppression = {
            name: last_chap
            for name, last_chap in self._warning_suppression.items()
            if current_chapter - last_chap < self._MOTIF_WARNING_COOLDOWN
        }

        # Group warnings by motif_name for aggregation
        grouped: dict[str, list[dict[str, Any]]] = {}
        for w in warnings:
            motif_name = w.get("motif_name", "")
            if motif_name:
                grouped.setdefault(motif_name, []).append(w)

        result: list[dict[str, Any]] = []
        for motif_name, motif_warnings in grouped.items():
            # Check cooldown
            last_warned = self._warning_suppression.get(motif_name, 0)
            if current_chapter - last_warned < self._MOTIF_WARNING_COOLDOWN:
                _logger.debug(
                    "motif_warning_suppressed | motif=%s | chapter=%d | last_warned=%d | cooldown=%d",
                    motif_name,
                    current_chapter,
                    last_warned,
                    self._MOTIF_WARNING_COOLDOWN,
                )
                continue

            # Aggregate warnings for this motif
            if len(motif_warnings) > 1:
                # Multiple warnings for same motif: aggregate previous_chapters
                all_previous = set()
                highest_severity = "medium"
                for w in motif_warnings:
                    all_previous.update(w.get("previous_chapters", []))
                    if self._severity_rank(w.get("severity", "medium")) > self._severity_rank(highest_severity):
                        highest_severity = w.get("severity", "medium")

                aggregated = {
                    "motif_name": motif_name,
                    "chapter_number": current_chapter,
                    "previous_chapters": sorted(all_previous)[-5:],  # Keep recent 5
                    "severity": highest_severity,
                    "suggestion": motif_warnings[0].get("suggestion", ""),
                    "aggregated_count": len(motif_warnings),
                }
                result.append(aggregated)
            else:
                result.append(motif_warnings[0])

            # Update suppression tracking
            self._warning_suppression[motif_name] = current_chapter

        return result

    def _deduplicate_and_decay_history(
        self,
        history: list[dict[str, Any]],
        current_chapter: int,
    ) -> list[dict[str, Any]]:
        """Deduplicate history events and apply relevance decay.

        Prevents the same event from appearing in multiple consecutive chapters
        and reduces relevance of older events.

        Args:
            history: Raw history events from search_relevant_history
            current_chapter: Current chapter number

        Returns:
            Filtered and decayed history events
        """
        import hashlib

        if not history:
            return []

        # Clean up old shown events (older than lookback window)
        self._shown_history_events = {
            (chap, ev_hash)
            for chap, ev_hash in self._shown_history_events
            if current_chapter - chap <= 5
        }

        result: list[dict[str, Any]] = []
        for event in history:
            chapter_num = event.get("chapter_number", 0)
            event_summary = event.get("event_summary", "")
            relevance_score = float(event.get("relevance_score", 0.0))

            # Create hash for deduplication
            event_hash = hashlib.md5(event_summary.encode()).hexdigest()[:12]

            # Skip if this event was already shown recently
            if (chapter_num, event_hash) in self._shown_history_events:
                _logger.debug(
                    "history_event_deduplicated | chapter=%d | event_summary=%s...",
                    current_chapter,
                    event_summary[:30],
                )
                continue

            # Apply decay based on age
            chapters_ago = current_chapter - chapter_num
            decayed_score = relevance_score * (self._HISTORY_DECAY_FACTOR ** chapters_ago)

            # Filter out low-relevance events after decay
            if decayed_score < self._HISTORY_MIN_RELEVANCE:
                continue

            # Add decayed score to event
            decayed_event = dict(event)
            decayed_event["original_relevance"] = relevance_score
            decayed_event["decayed_relevance"] = round(decayed_score, 3)

            result.append(decayed_event)

            # Track this event as shown
            self._shown_history_events.add((chapter_num, event_hash))

        # Sort by decayed relevance (descending)
        result.sort(key=lambda x: x.get("decayed_relevance", 0), reverse=True)

        return result[:5]  # Limit to top 5

    @staticmethod
    def _severity_rank(severity: str) -> int:
        """Convert severity string to numeric rank for comparison."""
        rank_map = {
            "critical": 4,
            "high": 3,
            "medium": 2,
            "low": 1,
        }
        return rank_map.get(severity.lower().strip(), 0)
