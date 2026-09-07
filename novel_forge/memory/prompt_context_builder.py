"""Synchronous prompt memory context assembly helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from novel_forge.obs.logger import get_logger

_log = get_logger("memory.context")


def build_prompt_memory_context(
    *,
    current_chapter: int,
    settings: Any,
    motif_tracker: Any = None,
    summary_service: Any = None,
    episodic_memory: Any = None,
    include_motifs: bool = True,
    include_summaries: bool = True,
    summary_granularity: str = "chapter",
    include_critiques: bool = True,
    legacy_summary_getter: Callable[[int], str] | None = None,
    critique_context_builder: Callable[..., str] | None = None,
    logger: Any = _log,
) -> dict[str, Any]:
    """Get memory context formatted for prompt injection."""
    context: dict[str, Any] = {}

    if include_motifs and motif_tracker:
        try:
            raw_lookback = getattr(
                settings,
                "memory_motif_related_lookback_chapters",
                None,
            )
            lookback = max(
                0,
                int(2 if raw_lookback is None else raw_lookback),
            )
            context["motif_context"] = motif_tracker.get_motifs_for_prompt(
                current_chapter=current_chapter,
                related_lookback_chapters=lookback,
            )
        except Exception as exc:
            logger.warning("Failed to get motif context: %s", exc)

    if include_summaries and summary_service:
        try:
            context["summary_context"] = summary_service.get_summary_for_context(
                current_chapter=current_chapter,
                granularity=summary_granularity,
            )
        except Exception as exc:
            logger.warning("Failed to get summary context: %s", exc)
    elif include_summaries and legacy_summary_getter is not None:
        legacy_summary = legacy_summary_getter(current_chapter)
        if legacy_summary:
            context["summary_context"] = legacy_summary

    if include_critiques and episodic_memory and critique_context_builder is not None:
        context["critique_context"] = critique_context_builder(
            current_chapter=current_chapter,
            episodic_memory=episodic_memory,
        )

    return context


__all__ = ("build_prompt_memory_context",)
