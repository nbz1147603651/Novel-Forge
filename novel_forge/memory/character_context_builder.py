"""Async character-scoped memory context coordinator."""

from __future__ import annotations

import inspect
from typing import Any

from novel_forge.memory.context_payloads import (
    serialize_character_episodic_results,
    serialize_character_motifs,
    serialize_relationship_projection,
)
from novel_forge.obs.logger import get_logger

_log = get_logger("memory.context")


async def build_character_memory_context(
    character_id: str,
    *,
    entity_knowledge_service: Any = None,
    relationship_query_service: Any = None,
    episodic_memory: Any = None,
    motif_tracker: Any = None,
    summary_service: Any = None,
    current_chapter: int | None = None,
    query: str = "",
    lookback: int = 8,
    top_k: int = 5,
    min_relevance: float = 0.5,
    include_episodic: bool = True,
    include_motifs: bool = True,
    include_summaries: bool = False,
    logger: Any = _log,
) -> dict[str, Any]:
    """Return memory context explicitly scoped to one character."""
    character = str(character_id or "").strip()
    context: dict[str, Any] = {
        "character_id": character,
        "episodic_context": [],
        "motif_context": {"associated_motifs": []},
    }
    if not character:
        return context

    current_entity: dict[str, Any] | None = None
    if entity_knowledge_service is not None:
        try:
            entity = await entity_knowledge_service.get_current(character)
            if isinstance(entity, dict) and entity:
                current_entity = entity
                context["entity_projection"] = {
                    "entity_id": str(entity.get("entity_id", "") or ""),
                    "name": str(entity.get("name", "") or ""),
                    "status": str(entity.get("status", "") or ""),
                    "last_seen_chapter": int(entity.get("last_seen_chapter", 0) or 0),
                    "attributes": dict(entity.get("attributes", {}) or {}),
                }
        except Exception as exc:
            logger.debug(
                "character_entity_projection_failed | character=%s | error=%s",
                character,
                exc,
            )

    if relationship_query_service is not None:
        lookup_key = (
            str(current_entity.get("entity_id", "") or character)
            if current_entity is not None
            else character
        )
        try:
            relationships = await relationship_query_service.get_relationships(lookup_key)
            serialized = serialize_relationship_projection(relationships)
            if serialized:
                context["relationship_projection"] = serialized
        except Exception as exc:
            logger.debug(
                "character_relationship_projection_failed | character=%s | error=%s",
                character,
                exc,
            )

    if include_episodic and episodic_memory:
        try:
            chapter_range: tuple[int, int] | None = None
            if current_chapter is not None:
                end_chapter = int(current_chapter) - 1
                start_chapter = max(1, int(current_chapter) - max(1, int(lookback)))
                if end_chapter >= start_chapter:
                    chapter_range = (start_chapter, end_chapter)
            results = await episodic_memory.search_by_semantic(
                query=query or character,
                chapter_range=chapter_range,
                top_k=max(1, int(top_k)),
                min_relevance=float(min_relevance),
                characters=[character],
            )
            context["episodic_context"] = serialize_character_episodic_results(
                results,
                limit=max(1, int(top_k)),
            )
        except Exception as exc:
            logger.warning(
                "Failed to get character episodic context | character=%s | error=%s",
                character,
                exc,
            )

    if include_motifs and motif_tracker:
        context["motif_context"] = {
            "associated_motifs": serialize_character_motifs(motif_tracker, character)
        }

    if include_summaries and summary_service:
        summary_getter = getattr(summary_service, "get_character_summary_for_context", None)
        if callable(summary_getter):
            try:
                summary = summary_getter(
                    character_id=character,
                    current_chapter=current_chapter,
                )
                if inspect.isawaitable(summary):
                    summary = await summary
                if summary:
                    context["summary_context"] = str(summary)
            except Exception as exc:
                logger.warning(
                    "Failed to get character summary context | character=%s | error=%s",
                    character,
                    exc,
                )
        else:
            context["summary_context_scope"] = "unavailable_character_filter"

    return context


__all__ = ("build_character_memory_context",)
