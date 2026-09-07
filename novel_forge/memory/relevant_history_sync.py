"""Synchronous relevant-history retrieval helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from novel_forge.core.infra.async_runner import run_async_coro
from novel_forge.memory.retrieval import bm25_scores
from novel_forge.obs.logger import get_logger

_log = get_logger("memory.context")


def search_relevant_history_sync(
    *,
    episodic_memory: Any,
    async_search: Callable[..., Awaitable[list[dict[str, Any]]]],
    query: str,
    current_chapter: int,
    lookback: int = 5,
    top_k: int = 5,
    min_relevance: float = 0.5,
    logger: Any = _log,
) -> list[dict[str, Any]]:
    """Run semantic relevant-history search from sync code when possible.

    If called inside an active event loop, fall back to temporal retrieval plus
    lightweight BM25 scoring so callers do not try to nest event loops.
    """
    if not episodic_memory:
        return []

    if current_chapter <= 1:
        return []

    try:
        asyncio.get_running_loop()
        loop_running = True
    except RuntimeError:
        loop_running = False

    if not loop_running:
        try:
            return run_async_coro(
                async_search(
                    query=query,
                    current_chapter=current_chapter,
                    lookback=lookback,
                    top_k=top_k,
                    min_relevance=min_relevance,
                )
            )
        except Exception as exc:
            logger.warning("Sync relevant history (async path) failed: %s", exc)
            return []

    return search_relevant_history_temporal_fallback(
        episodic_memory=episodic_memory,
        query=query,
        current_chapter=current_chapter,
        lookback=lookback,
        top_k=top_k,
        min_relevance=min_relevance,
        logger=logger,
    )


def search_relevant_history_temporal_fallback(
    *,
    episodic_memory: Any,
    query: str,
    current_chapter: int,
    lookback: int = 5,
    top_k: int = 5,
    min_relevance: float = 0.5,
    logger: Any = _log,
) -> list[dict[str, Any]]:
    """Search temporal events with BM25 + recency scoring."""
    try:
        start_chapter = max(1, current_chapter - lookback)
        end_chapter = current_chapter - 1
        if end_chapter < start_chapter:
            return []

        temporal_results = episodic_memory.search_by_temporal(
            start_chapter=start_chapter,
            end_chapter=end_chapter,
        )
        if not temporal_results:
            return []

        documents = []
        result_list = []
        for result in temporal_results:
            doc_text = " ".join(
                [
                    str(getattr(result, "event_summary", "") or ""),
                    " ".join(getattr(result, "characters_involved", []) or []),
                    str(getattr(result, "timestamp_in_story", "") or ""),
                ]
            )
            documents.append(doc_text)
            result_list.append(result)

        bm25_raw = bm25_scores(query, documents)
        max_bm25 = max(bm25_raw) if bm25_raw else 0.0
        bm25_norm = [s / max_bm25 for s in bm25_raw] if max_bm25 > 0 else [0.0] * len(bm25_raw)

        scored: list[dict[str, Any]] = []
        for idx, (result, bm25_n) in enumerate(zip(result_list, bm25_norm, strict=True)):
            distance = max(0, current_chapter - int(getattr(result, "chapter_number", 0) or 0))
            recency_score = max(0.0, 1.0 - (distance / max(lookback, 1)))
            relevance = round(bm25_n * 0.6 + recency_score * 0.4, 3)
            if relevance < min_relevance:
                continue

            scored.append(
                {
                    "chapter_number": int(getattr(result, "chapter_number", 0) or 0),
                    "event_summary": str(getattr(result, "event_summary", "") or ""),
                    "scene_index": int(getattr(result, "scene_index", 0) or 0),
                    "relevance_score": relevance,
                    "characters_involved": list(
                        getattr(result, "characters_involved", []) or []
                    )[:5],
                    "timestamp_in_story": str(getattr(result, "timestamp_in_story", "") or ""),
                    "bm25_score": round(bm25_raw[idx], 3),
                }
            )

        scored.sort(key=lambda item: float(item.get("relevance_score", 0.0)), reverse=True)
        return scored[:top_k]

    except Exception as exc:
        logger.warning("Sync relevant history (fallback path) failed: %s", exc)
        return []


__all__ = (
    "search_relevant_history_sync",
    "search_relevant_history_temporal_fallback",
)
