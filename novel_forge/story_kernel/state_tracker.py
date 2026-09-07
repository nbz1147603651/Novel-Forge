"""StateTracker — merges chapter outcomes into StoryKernel.

Migrated from canon/state_tracker.py.  Only the StoryKernelMerger path is
retained; CanonMerger / CanonState dependencies have been removed.

Pure algorithms for fuzzy thread matching and deduplication are preserved as
static/class methods for reuse across the codebase.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from novel_forge.story_kernel.merger import StoryKernelMerger
from novel_forge.story_kernel.schemas import StoryKernel

if TYPE_CHECKING:
    from novel_forge.core.schemas.chapter import ChapterOutcome
    from novel_forge.core.schemas.story_state import PlotThreadState

# Suffixes commonly appended by the LLM that create duplicate thread IDs.
_THREAD_SUFFIX_RE = re.compile(r"(线|索|案|之谜|谜团|阴谋|真相|伏笔|主线)$")


class StateTracker:
    """Merge chapter outcomes into a StoryKernel.

    Delegates to :class:`StoryKernelMerger` for the actual field-level merge.
    Also exposes pure fuzzy-matching utilities for thread deduplication.
    """

    def __init__(
        self,
        kernel_merger: StoryKernelMerger | None = None,
    ) -> None:
        self._kernel_merger = kernel_merger or StoryKernelMerger()

    # ── Public merge API ────────────────────────────────────────────

    def merge_outcome_to_kernel(
        self, kernel: StoryKernel, outcome: ChapterOutcome
    ) -> StoryKernel:
        """Apply *outcome* on top of *kernel* and return a new StoryKernel.

        Delegates to :meth:`StoryKernelMerger.merge_outcome`.  The original
        kernel is never mutated.
        """
        return self._kernel_merger.merge_outcome(kernel, outcome)

    # ── Fuzzy thread matching (pure algorithms) ─────────────────────

    @staticmethod
    def _normalize_thread_id(thread_id: str) -> str:
        """Strip common Chinese suffixes for dedup matching."""
        return _THREAD_SUFFIX_RE.sub("", thread_id).strip()

    # Minimum similarity ratio (0–1) for fuzzy thread ID / title matching.
    # 0.75 is intentionally conservative: avoids false positives on Chinese
    # thread IDs that share common two-character prefixes (e.g. "辽国阴谋" vs
    # "辽国萨满") while still catching synonym renames ("苏令帴怪病情" vs
    # "苏令帴怪病").
    _FUZZY_THRESHOLD: float = 0.75

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        """Character-level similarity ratio between two strings (0–1)."""
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a, b).ratio()

    @classmethod
    def _find_canonical_thread_id(
        cls,
        incoming_id: str,
        existing_threads: dict[str, PlotThreadState],
        *,
        incoming_title: str = "",
    ) -> str:
        """Return the existing thread_id that best matches *incoming_id*.

        Three-layer lookup (first match wins):

        1. Exact key match.
        2. Suffix-normalised exact match (线/索/案/之谜… stripped).
        3. Fuzzy character-similarity match against existing IDs *and* titles,
           also comparing incoming_title when the LLM provides it.
           Only accepts matches above ``_FUZZY_THRESHOLD`` to avoid false
           positives.

        Among multiple candidates at the same layer, always prefer the thread
        with the highest ``last_touched_chapter`` (most recent anchor wins).
        """
        # --- Layer 1: exact match ---
        if incoming_id in existing_threads:
            return incoming_id

        # Guard: empty after normalisation — skip dedup to avoid spurious merge.
        norm_incoming = cls._normalize_thread_id(incoming_id)
        if not norm_incoming:
            return incoming_id

        # --- Layer 2: suffix-normalised exact match ---
        candidates = [
            eid
            for eid in existing_threads
            if cls._normalize_thread_id(eid) == norm_incoming
        ]
        if candidates:
            return max(
                candidates,
                key=lambda eid: (existing_threads[eid].last_touched_chapter, eid),
            )

        # --- Layer 3: fuzzy similarity ---
        # Build candidate scores: for each existing thread, take the max
        # similarity across (incoming_id, incoming_title) × (eid, thread.title).
        norm_title = (
            cls._normalize_thread_id(incoming_title) if incoming_title else ""
        )
        best_id: str | None = None
        best_score: float = 0.0
        for eid, thread_state in existing_threads.items():
            norm_eid = cls._normalize_thread_id(eid)
            scores = [
                cls._similarity(norm_incoming, norm_eid),
                cls._similarity(incoming_id, eid),
            ]
            if incoming_title:
                scores.append(cls._similarity(incoming_title, eid))
                if thread_state.title:
                    scores.append(
                        cls._similarity(incoming_title, thread_state.title)
                    )
            if norm_title:
                scores.append(cls._similarity(norm_title, norm_eid))
            if thread_state.title:
                scores.append(cls._similarity(incoming_id, thread_state.title))
            score = max(scores)
            if score > best_score:
                best_score = score
                best_id = eid

        if best_id is not None and best_score >= cls._FUZZY_THRESHOLD:
            return best_id

        return incoming_id


__all__ = ["StateTracker"]
