"""Humanize library retrieval — sentence splitting, BM25 + vector fusion."""

from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from novel_forge.memory.humanize_library_store import _safe_log_event
from novel_forge.memory.retrieval import bm25_scores, tokenize_bm25_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentence splitter
# ---------------------------------------------------------------------------

_SENTENCE_RE = re.compile(r"[^。！？\n]+[。！？]?")
_UNSAFE_NESTED_QUANTIFIER_RE = re.compile(r"\([^)]*[*+][^)]*\)\s*(?:[*+]|\{)")
_REGEX_FLAG_MASK = re.IGNORECASE | re.MULTILINE | re.DOTALL


@dataclass(frozen=True)
class _TextWindow:
    text: str
    start: int
    end: int
    paragraph_index: int


def iter_sentences(text: str) -> list[str]:
    """Split text into sentences using Chinese punctuation boundaries."""
    raw = _SENTENCE_RE.findall(text or "")
    return [s.strip() for s in raw if s.strip()]


# ---------------------------------------------------------------------------
# Score normalization
# ---------------------------------------------------------------------------


def normalize_scores(scores: list[float]) -> list[float]:
    """Min-max normalize a list of scores to [0, 1].

    Returns all zeros if the list is empty or all values are identical.
    """
    if not scores:
        return []
    lo = min(scores)
    hi = max(scores)
    span = hi - lo
    if span == 0:
        return [0.0] * len(scores)
    return [(s - lo) / span for s in scores]


# ---------------------------------------------------------------------------
# Union fuse
# ---------------------------------------------------------------------------


def union_fuse(
    vec_hits: list[tuple[str, float]],
    fts_hits: list[tuple[str, float]],
) -> dict[str, float]:
    """Fuse vector (cosine) and FTS (BM25) hits via normalized union + max.

    * BM25 scores: min-max normalize per-batch to [0, 1].
    * Cosine scores: clip to [0, 1] (already in range for cosine similarity).
    * Union by pattern_id; final score = max(normalized_bm25, cosine).

    Returns dict of pattern_id -> final score.
    """
    result: dict[str, float] = {}

    # Normalize BM25 scores
    if fts_hits:
        fts_ids = [h[0] for h in fts_hits]
        fts_raw = [h[1] for h in fts_hits]
        fts_norm = normalize_scores(fts_raw)
        for pid, score in zip(fts_ids, fts_norm, strict=True):
            result[pid] = max(result.get(pid, 0.0), score)

    # Cosine scores — clip to [0, 1]
    for pid, score in vec_hits:
        clipped = max(0.0, min(1.0, score))
        result[pid] = max(result.get(pid, 0.0), clipped)

    return result


# ---------------------------------------------------------------------------
# HumanizeLibraryRetriever
# ---------------------------------------------------------------------------


class HumanizeLibraryRetriever:
    """Retrieve relevant humanize patterns for a chapter text.

    Uses an LRU embedding cache (OrderedDict, max 2048 entries) and
    normalized union fusion of BM25 + cosine scores.
    """

    def __init__(
        self,
        *,
        embedder: Any | None = None,
        top_k: int = 30,
        sim_threshold: float = 0.3,
        cache_max: int = 2048,
    ) -> None:
        self._embedder = embedder
        self._top_k = top_k
        self._sim_threshold = sim_threshold
        self._cache_max = cache_max
        self._embedding_cache: OrderedDict[str, list[float]] = OrderedDict()

    # -- Public API ----------------------------------------------------------

    def retrieve(
        self,
        library: Any,
        chapter_text: str,
        top_k: int | None = None,
        *,
        project_id: str | None = None,
        scan_text: str | None = None,
    ) -> list[dict[str, Any]]:
        """Discover library-backed candidates across the complete text.

        Executable regexes, example phrases, and keyword conjunctions are
        scanned exhaustively.  ``top_k`` limits only semantic candidates per
        text window; it never truncates deterministic matches.

        Returns list of dicts with keys:
            pattern_id, pattern_name, category, severity,
            evidence_quote, source, similarity

        Any exception during retrieval: log warning, return empty list.
        """
        effective_k = top_k if top_k is not None else self._top_k
        _safe_log_event(
            "humanize_library.retrieval.invoked",
            {
                "chapter": 0,
                "query_sentences": max(1, len(chapter_text) // 80),
                "top_k": effective_k,
            },
        )
        t0 = time.monotonic()
        try:
            result = self._retrieve_impl(
                library,
                chapter_text,
                effective_k,
                project_id=project_id,
                scan_text=scan_text,
            )
            duration_ms = (time.monotonic() - t0) * 1000
            deterministic_hits = sum(
                1 for hit in result if str(hit.get("match_method", "")).startswith("deterministic")
            )
            semantic_hits = len(result) - deterministic_hits
            _safe_log_event(
                "humanize_library.retrieval.completed",
                {
                    "chapter": 0,
                    "deterministic_hits": deterministic_hits,
                    "semantic_hits": semantic_hits,
                    "fts_hits": sum(
                        1 for hit in result if hit.get("match_method") == "semantic_bm25"
                    ),
                    "vec_hits": sum(
                        1 for hit in result if hit.get("match_method") == "semantic_vector"
                    ),
                    "union_hits": len(result),
                    "duration_ms": round(duration_ms, 1),
                    "normalization_method": "min_max",
                },
            )
            return result
        except Exception:
            duration_ms = (time.monotonic() - t0) * 1000
            logger.warning(
                "humanize_library.retrieval.failed",
                exc_info=True,
            )
            _safe_log_event(
                "humanize_library.retrieval.failed",
                {
                    "chapter": 0,
                    "error_type": "retrieval_error",
                    "duration_ms": round(duration_ms, 1),
                    "fallback": "empty_list",
                },
            )
            return []

    # -- Internal ------------------------------------------------------------

    def _retrieve_impl(
        self,
        library: Any,
        chapter_text: str,
        top_k: int,
        *,
        project_id: str | None = None,
        scan_text: str | None = None,
    ) -> list[dict[str, Any]]:
        """Run deterministic full coverage, then bounded semantic recall."""
        from novel_forge.memory.humanize_library_store import HumanizeLibrary

        if not isinstance(library, HumanizeLibrary):
            return []

        all_entries = library.list_all()
        enabled_entries = [
            entry
            for entry in all_entries
            if entry.enabled and (entry.project_id is None or entry.project_id == project_id)
        ]
        if not enabled_entries:
            return []

        searchable_text = scan_text if scan_text is not None else chapter_text
        if len(searchable_text) != len(chapter_text):
            searchable_text = chapter_text
        windows = self._text_windows(searchable_text)
        if not windows:
            return []

        deterministic = self._deterministic_candidates(
            enabled_entries,
            chapter_text,
            searchable_text,
            windows,
        )
        semantic = self._semantic_candidates(
            enabled_entries,
            chapter_text,
            windows,
            library,
            top_k=max(1, top_k),
        )
        return self._dedupe_and_sort([*deterministic, *semantic])

    @staticmethod
    def _text_windows(text: str) -> list[_TextWindow]:
        windows: list[_TextWindow] = []
        offset = 0
        for paragraph_index, raw_paragraph in enumerate((text or "").split("\n\n")):
            paragraph = raw_paragraph.strip()
            if not paragraph:
                offset += len(raw_paragraph) + 2
                continue
            leading = len(raw_paragraph) - len(raw_paragraph.lstrip())
            start = offset + leading
            windows.append(
                _TextWindow(
                    text=paragraph,
                    start=start,
                    end=start + len(paragraph),
                    paragraph_index=paragraph_index,
                )
            )
            offset += len(raw_paragraph) + 2
        return windows

    @classmethod
    def _configured_patterns(cls, entry: Any) -> list[re.Pattern[str]]:
        config = entry.detection_config if isinstance(entry.detection_config, dict) else {}
        raw_patterns = config.get("patterns", config.get("pattern", config.get("regex", [])))
        if isinstance(raw_patterns, str):
            patterns = [raw_patterns]
        elif isinstance(raw_patterns, list):
            patterns = [str(item) for item in raw_patterns if str(item)]
        else:
            patterns = []
        raw_flags = config.get("flags", 0)
        flags = int(raw_flags) & _REGEX_FLAG_MASK if isinstance(raw_flags, int) else 0
        compiled: list[re.Pattern[str]] = []
        for pattern in patterns:
            if not cls._regex_is_safe(pattern):
                logger.warning(
                    "humanize_library.regex_rejected | pattern_id=%s",
                    entry.pattern_id,
                )
                continue
            try:
                compiled.append(re.compile(pattern, flags))
            except re.error:
                logger.warning(
                    "humanize_library.regex_invalid | pattern_id=%s",
                    entry.pattern_id,
                )
        return compiled

    @staticmethod
    def _regex_is_safe(pattern: str) -> bool:
        return bool(
            pattern
            and len(pattern) <= 500
            and not _UNSAFE_NESTED_QUANTIFIER_RE.search(pattern)
        )

    @staticmethod
    def _candidate_payload(
        entry: Any,
        *,
        evidence: str,
        start: int,
        end: int,
        paragraph_index: int,
        similarity: float,
        match_method: str,
    ) -> dict[str, Any]:
        return {
            "pattern_id": entry.pattern_id,
            "pattern_name": entry.pattern_name,
            "category": entry.category,
            "severity": entry.severity,
            "evidence_quote": evidence[:200],
            "paragraph_index": paragraph_index,
            "span_start": start,
            "span_end": end,
            "source": entry.source,
            "similarity": round(max(0.0, min(1.0, similarity)), 4),
            "match_method": match_method,
        }

    @classmethod
    def _deterministic_candidates(
        cls,
        entries: list[Any],
        chapter_text: str,
        searchable_text: str,
        windows: list[_TextWindow],
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for entry in entries:
            confidence = float(entry.detection_config.get("confidence", 0.95) or 0.95)
            for pattern in cls._configured_patterns(entry):
                # Scan bounded windows so malformed user expressions cannot span
                # the entire chapter or hide the source paragraph.
                for window in windows:
                    for match in pattern.finditer(window.text):
                        if match.end() <= match.start():
                            continue
                        start = window.start + match.start()
                        end = window.start + match.end()
                        results.append(
                            cls._candidate_payload(
                                entry,
                                evidence=chapter_text[start:end],
                                start=start,
                                end=end,
                                paragraph_index=window.paragraph_index,
                                similarity=confidence,
                                match_method="deterministic_regex",
                            )
                        )

            for phrase in dict.fromkeys(str(item).strip() for item in entry.example_phrases):
                if not phrase:
                    continue
                for match in re.finditer(re.escape(phrase), searchable_text):
                    results.append(
                        cls._candidate_payload(
                            entry,
                            evidence=match.group(0),
                            start=match.start(),
                            end=match.end(),
                            paragraph_index=cls._paragraph_index_for_offset(windows, match.start()),
                            similarity=1.0,
                            match_method="deterministic_example",
                        )
                    )

            keywords = [str(item).strip() for item in entry.keywords if len(str(item).strip()) >= 2]
            min_hits = int(entry.detection_config.get("min_keyword_hits", 2) or 2)
            if keywords and min_hits > 0:
                for window in windows:
                    matched = [keyword for keyword in dict.fromkeys(keywords) if keyword in window.text]
                    if len(matched) < min(min_hits, len(set(keywords))):
                        continue
                    results.append(
                        cls._candidate_payload(
                            entry,
                            evidence=window.text,
                            start=window.start,
                            end=window.end,
                            paragraph_index=window.paragraph_index,
                            similarity=min(1.0, len(matched) / max(min_hits, 1)),
                            match_method="deterministic_keywords",
                        )
                    )
        return results

    def _semantic_candidates(
        self,
        entries: list[Any],
        chapter_text: str,
        windows: list[_TextWindow],
        library: Any,
        *,
        top_k: int,
    ) -> list[dict[str, Any]]:
        semantic_entries = [
            entry
            for entry in entries
            if entry.detection_method in {"llm_only", "vector_only", "mixed"}
            or (entry.source != "builtin" and not self._configured_patterns(entry))
        ]
        if not semantic_entries:
            return []
        entry_map = {entry.pattern_id: entry for entry in semantic_entries}
        documents = [self._entry_document(entry) for entry in semantic_entries]
        results: list[dict[str, Any]] = []
        per_window_k = min(max(1, top_k), len(semantic_entries))
        window_vectors: list[list[float] | None] = [None] * len(windows)
        if self._embedder is not None and bool(getattr(library, "vector_available", False)):
            window_vectors = self._get_embeddings([window.text for window in windows])
        for window, query_vec in zip(windows, window_vectors, strict=True):
            bm25 = self._bm25_window_hits(window.text, semantic_entries, documents, per_window_k)
            vectors = self._vector_window_hits(
                window.text,
                library,
                entry_map,
                per_window_k,
                query_vec=query_vec,
            )
            fused = union_fuse(vectors, bm25)
            for pattern_id, score in sorted(fused.items(), key=lambda item: item[1], reverse=True)[
                :per_window_k
            ]:
                if score < self._sim_threshold:
                    continue
                entry = entry_map.get(pattern_id)
                if entry is None:
                    continue
                method = (
                    "semantic_vector"
                    if any(pid == pattern_id for pid, _score in vectors)
                    else "semantic_bm25"
                )
                results.append(
                    self._candidate_payload(
                        entry,
                        evidence=window.text,
                        start=window.start,
                        end=window.end,
                        paragraph_index=window.paragraph_index,
                        similarity=score,
                        match_method=method,
                    )
                )
        return results

    @staticmethod
    def _entry_document(entry: Any) -> str:
        parts = [entry.pattern_name, entry.category, *entry.keywords, *entry.example_phrases]
        if entry.example_template:
            parts.append(entry.example_template)
        if entry.notes:
            parts.append(entry.notes)
        return " ".join(str(part) for part in parts if str(part).strip())

    @staticmethod
    def _bm25_window_hits(
        text: str,
        entries: list[Any],
        documents: list[str],
        top_k: int,
    ) -> list[tuple[str, float]]:
        scores = bm25_scores(text, documents)
        query_tokens = set(tokenize_bm25_text(text))
        candidates: list[tuple[str, float]] = []
        for entry, document, raw_score in zip(entries, documents, scores, strict=True):
            document_tokens = set(tokenize_bm25_text(document))
            overlap = query_tokens & document_tokens
            if raw_score <= 0 or len(overlap) < 2:
                continue
            lexical_coverage = len(overlap) / max(1, min(len(document_tokens), 12))
            candidates.append((entry.pattern_id, lexical_coverage))
        return sorted(candidates, key=lambda item: item[1], reverse=True)[:top_k]

    def _vector_window_hits(
        self,
        text: str,
        library: Any,
        entry_map: dict[str, Any],
        top_k: int,
        *,
        query_vec: list[float] | None = None,
    ) -> list[tuple[str, float]]:
        if self._embedder is None or not bool(getattr(library, "vector_available", False)):
            return []
        if query_vec is None:
            return []
        try:
            return [
                (pattern_id, score)
                for pattern_id, score in library.vec_search(
                    query_vec,
                    top_k=max(top_k, self._top_k),
                    query_text=text,
                )
                if pattern_id in entry_map
            ][:top_k]
        except Exception:
            logger.debug("vector_window_search_failed", exc_info=True)
            return []

    @staticmethod
    def _paragraph_index_for_offset(windows: list[_TextWindow], offset: int) -> int:
        for window in windows:
            if window.start <= offset < window.end:
                return window.paragraph_index
        return max(0, len(windows) - 1)

    @staticmethod
    def _dedupe_and_sort(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        by_key: dict[tuple[str, int, int], dict[str, Any]] = {}
        for result in results:
            key = (
                str(result.get("pattern_id") or ""),
                int(result.get("span_start") or 0),
                int(result.get("span_end") or 0),
            )
            existing = by_key.get(key)
            if existing is None or float(result.get("similarity", 0.0)) > float(
                existing.get("similarity", 0.0)
            ):
                by_key[key] = result
        return sorted(
            by_key.values(),
            key=lambda item: (
                -severity_rank.get(str(item.get("severity") or "low"), 0),
                int(item.get("span_start") or 0),
                str(item.get("pattern_id") or ""),
            ),
        )

    def _get_embeddings(self, texts: list[str]) -> list[list[float] | None]:
        """Resolve a batch through the LRU cache with one provider call."""

        if not texts:
            return []
        results: list[list[float] | None] = [None] * len(texts)
        missing_by_key: OrderedDict[str, tuple[str, list[int]]] = OrderedDict()
        for index, text in enumerate(texts):
            key = str(hash(text))
            cached = self._embedding_cache.get(key)
            if cached is not None:
                self._embedding_cache.move_to_end(key)
                results[index] = list(cached)
                continue
            if key not in missing_by_key:
                missing_by_key[key] = (text, [index])
            else:
                missing_by_key[key][1].append(index)

        if not missing_by_key or self._embedder is None:
            return results
        try:
            missing_items = list(missing_by_key.items())
            vectors = self._embedder.embed([item[1][0] for item in missing_items])
            if len(vectors) != len(missing_items):
                return results
            for (key, (_text, indices)), vector in zip(missing_items, vectors, strict=True):
                if not vector:
                    continue
                self._embedding_cache[key] = list(vector)
                self._embedding_cache.move_to_end(key)
                for index in indices:
                    results[index] = list(vector)
            while len(self._embedding_cache) > self._cache_max:
                self._embedding_cache.popitem(last=False)
        except Exception:
            logger.debug("embed_batch_failed", exc_info=True)
        return results
