"""StyleGoldenRetriever — surface high-scoring paragraphs from the same project.

This is the M3 engine for the "In-Context Golden Examples" feedback loop.
Each project that has been evaluated keeps a ``_chapter_meta_cache.json``
with per-chapter ``overall_score`` values. The retriever indexes paragraphs
(60-280 chars by default) from chapters scoring >= 9.0, then returns BM25-
ranked top-N passages matching a scene_intent query.

The retrieved passages are intended to be injected into draft_chapter.j2
so the LLM sees "what good prose looks like in *this* project" rather than
vague style instructions.

Design constraints (from plan §2.2.3):
- No external IO at runtime. The index is built once from disk and held in
  memory; subsequent retrieval is purely CPU-bound BM25.
- Diversity enforced via ``max_per_chapter`` (default 1) — prevents the
  retriever from returning 3 paragraphs all from the same high-scoring chapter.
- No LLM calls. Pure deterministic re-use of the existing
  ``novel_forge.memory.retrieval.bm25_scores`` helper.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from novel_forge.memory.retrieval import bm25_scores

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldenPassage:
    """A single paragraph from a high-scoring chapter, ready to inject into prompts."""

    chapter_number: int
    paragraph_index: int
    text: str
    eval_score: float
    dimensions: dict[str, float] = field(default_factory=dict)

    def __str__(self) -> str:
        score_str = f"{self.eval_score:.1f}" if self.eval_score else "?"
        return f"GoldenPassage(ch{self.chapter_number} #{self.paragraph_index}, score={score_str})"


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------


class StyleGoldenRetriever:
    """Index paragraphs from chapters with eval_score >= threshold, and retrieve top-N.

    Usage::

        retriever = StyleGoldenRetriever(project_dir, eval_cache_path)
        passages = retriever.retrieve_for_query("风 铃声 穿堂", max_results=3)
        for p in passages:
            print(p.chapter_number, p.text)
    """

    DEFAULT_THRESHOLD = 9.0
    DEFAULT_MIN_PARAGRAPH_CHARS = 60
    DEFAULT_MAX_PARAGRAPH_CHARS = 280
    DEFAULT_MAX_PER_CHAPTER = 1

    def __init__(
        self,
        project_dir: Path,
        eval_cache_path: Path,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        min_paragraph_chars: int = DEFAULT_MIN_PARAGRAPH_CHARS,
        max_paragraph_chars: int = DEFAULT_MAX_PARAGRAPH_CHARS,
        max_per_chapter: int = DEFAULT_MAX_PER_CHAPTER,
    ) -> None:
        if min_paragraph_chars <= 0 or max_paragraph_chars <= min_paragraph_chars:
            raise ValueError(
                f"invalid paragraph length bounds: min={min_paragraph_chars} "
                f"max={max_paragraph_chars}"
            )
        if max_per_chapter < 1:
            raise ValueError(f"max_per_chapter must be >= 1, got {max_per_chapter}")

        self._project_dir = Path(project_dir)
        self._eval_cache_path = Path(eval_cache_path)
        self.threshold = threshold
        self.min_paragraph_chars = min_paragraph_chars
        self.max_paragraph_chars = max_paragraph_chars
        self.max_per_chapter = max_per_chapter

        self._eval_cache = self._load_eval_cache(self._eval_cache_path)
        self._index: list[GoldenPassage] = self._build_index()

    # ── Public API ───────────────────────────────────────────────────────

    @property
    def indexed_chapters(self) -> set[int]:
        return {p.chapter_number for p in self._index}

    def retrieve_for_query(
        self,
        query: str,
        *,
        max_results: int = 3,
    ) -> list[GoldenPassage]:
        """Return top-N BM25-ranked GoldenPassages matching the query.

        Diversity is enforced: at most ``self.max_per_chapter`` passages from
        the same chapter are returned. Empty queries fall back to the first
        ``max_results`` indexed passages (graceful degradation for prompts
        that pass empty scene_intent during early init).
        """
        if not self._index:
            return []

        max_results = max(1, max_results)
        query = (query or "").strip()

        if not query:
            # Graceful fallback: take first N indexed passages, respecting diversity.
            return self._diversified_first_n(max_results)

        corpus = [p.text for p in self._index]
        try:
            scores = bm25_scores(query, corpus)
        except Exception as exc:
            logger.warning("StyleGoldenRetriever BM25 failed: %s", exc)
            return self._diversified_first_n(max_results)

        # Sort by descending score; ties broken by chapter number then paragraph index.
        ranked = sorted(
            zip(scores, self._index, strict=True),
            key=lambda pair: (-pair[0], pair[1].chapter_number, pair[1].paragraph_index),
        )

        return self._apply_diversity(ranked, max_results)

    def retrieve_for_scene(
        self,
        scene_intent: dict[str, Any],
        *,
        max_results: int = 3,
    ) -> list[GoldenPassage]:
        """Convenience wrapper: extract query terms from a scene_intent dict.

        Recognized keys (any of):
        - emotional_tone / tone
        - scene_action / action
        - pov_keywords / pov
        - setting / location
        - topic / theme
        - characters_involved
        """
        if not isinstance(scene_intent, dict):
            return self.retrieve_for_query("", max_results=max_results)

        candidate_keys = (
            "emotional_tone", "tone", "scene_action", "action",
            "pov_keywords", "pov", "setting", "location",
            "topic", "theme", "characters_involved",
        )
        parts: list[str] = []
        for key in candidate_keys:
            val = scene_intent.get(key)
            if isinstance(val, str):
                parts.extend(val.split())
            elif isinstance(val, list):
                parts.extend(str(v) for v in val)
        query = " ".join(parts).strip()
        return self.retrieve_for_query(query, max_results=max_results)

    # ── Indexing (private) ───────────────────────────────────────────────

    @staticmethod
    def _load_eval_cache(path: Path) -> dict[int, dict[str, Any]]:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not load eval cache %s: %s", path, exc)
            return {}
        # Keys may be int or string depending on how the cache was written.
        out: dict[int, dict[str, Any]] = {}
        for k, v in (data or {}).items():
            try:
                ch_num = int(str(k).replace("chapter_", ""))
            except (ValueError, AttributeError):
                continue
            if isinstance(v, dict):
                out[ch_num] = v
        return out

    def _build_index(self) -> list[GoldenPassage]:
        index: list[GoldenPassage] = []
        chapters_dir = self._project_dir / "chapters"
        if not chapters_dir.is_dir():
            return index

        for chapter_file in sorted(chapters_dir.glob("chapter_*.md")):
            try:
                chapter_number = int(chapter_file.stem.split("_")[1])
            except (IndexError, ValueError):
                continue

            meta = self._eval_cache.get(chapter_number)
            if not isinstance(meta, dict):
                continue
            try:
                overall = float(meta.get("overall_score", 0.0) or 0.0)
            except (TypeError, ValueError):
                logger.warning(
                    "StyleGoldenRetriever skipped chapter %s with invalid overall_score=%r",
                    chapter_number,
                    meta.get("overall_score"),
                )
                continue
            if overall < self.threshold:
                continue

            try:
                text = chapter_file.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("Could not read %s: %s", chapter_file, exc)
                continue

            paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
            for idx, paragraph in enumerate(paragraphs):
                if not (self.min_paragraph_chars <= len(paragraph) <= self.max_paragraph_chars):
                    continue
                dimensions = meta.get("dimensions") or {}
                if not isinstance(dimensions, dict):
                    dimensions = {}
                index.append(GoldenPassage(
                    chapter_number=chapter_number,
                    paragraph_index=idx,
                    text=paragraph,
                    eval_score=overall,
                    dimensions={
                        str(k): float(v)
                        for k, v in dimensions.items()
                        if isinstance(v, (int, float))
                    },
                ))

        logger.info(
            "StyleGoldenRetriever indexed %d golden passages from %d chapters in %s",
            len(index),
            len({p.chapter_number for p in index}),
            self._project_dir,
        )
        return index

    # ── Diversity (private) ──────────────────────────────────────────────

    def _apply_diversity(
        self,
        ranked: list[tuple[float, GoldenPassage]],
        max_results: int,
    ) -> list[GoldenPassage]:
        """Cap per-chapter contribution to ``self.max_per_chapter``."""
        results: list[GoldenPassage] = []
        per_chapter_count: dict[int, int] = {}
        for _, passage in ranked:
            if len(results) >= max_results:
                break
            current = per_chapter_count.get(passage.chapter_number, 0)
            if current >= self.max_per_chapter:
                continue
            per_chapter_count[passage.chapter_number] = current + 1
            results.append(passage)
        return results

    def _diversified_first_n(self, max_results: int) -> list[GoldenPassage]:
        """Return the first N indexed passages while respecting diversity.

        Used when the query is empty (fallback) or BM25 fails.
        """
        # Group by chapter, take 1 from each in chapter order, repeat until full.
        by_chapter: dict[int, list[GoldenPassage]] = {}
        for passage in sorted(
            self._index, key=lambda p: (p.chapter_number, p.paragraph_index)
        ):
            by_chapter.setdefault(passage.chapter_number, []).append(passage)

        results: list[GoldenPassage] = []
        per_chapter_idx: dict[int, int] = {}
        while len(results) < max_results:
            progressed = False
            for ch_num in sorted(by_chapter.keys()):
                idx = per_chapter_idx.get(ch_num, 0)
                if idx >= self.max_per_chapter:
                    continue
                available = by_chapter[ch_num]
                if idx >= len(available):
                    continue
                results.append(available[idx])
                per_chapter_idx[ch_num] = idx + 1
                progressed = True
                if len(results) >= max_results:
                    break
            if not progressed:
                break
        return results


__all__ = [
    "GoldenPassage",
    "StyleGoldenRetriever",
]
