"""Lightweight retrieval helpers for memory fallback ranking."""

from __future__ import annotations

import math
import re

_BM25_TOKEN_CHUNK_RE = re.compile(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}")


def tokenize_bm25_text(text: str) -> list[str]:
    """Tokenize mixed Latin/CJK text for lightweight BM25 ranking."""
    tokens: list[str] = []
    for match in _BM25_TOKEN_CHUNK_RE.finditer(str(text or "").lower()):
        chunk = match.group(0)
        if re.fullmatch(r"[a-z0-9_]{2,}", chunk):
            tokens.append(chunk)
            continue
        if len(chunk) == 2:
            tokens.append(chunk)
            continue
        tokens.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
    return tokens


def bm25_scores(
    query: str,
    documents: list[str],
    k1: float = 1.5,
    b: float = 0.75,
) -> list[float]:
    """Return Okapi-BM25 scores for *query* against each document."""
    n_docs = len(documents)
    query_terms = set(tokenize_bm25_text(query))
    if not query_terms or n_docs == 0:
        return [0.0] * n_docs

    tokenized = [tokenize_bm25_text(d) for d in documents]
    doc_lens = [len(toks) for toks in tokenized]
    if not any(doc_lens):
        return [0.0] * n_docs
    avgdl = sum(doc_lens) / n_docs or 1.0

    df: dict[str, int] = {term: 0 for term in query_terms}
    for toks in tokenized:
        for term in set(toks) & query_terms:
            df[term] += 1

    idf = {
        term: math.log((n_docs - df[term] + 0.5) / (df[term] + 0.5) + 1)
        for term in query_terms
    }

    scores: list[float] = []
    for toks, dl in zip(tokenized, doc_lens, strict=True):
        if dl == 0:
            scores.append(0.0)
            continue
        tf: dict[str, int] = {}
        for token in toks:
            if token in query_terms:
                tf[token] = tf.get(token, 0) + 1
        score = 0.0
        for term, freq in tf.items():
            numerator = freq * (k1 + 1)
            denominator = freq + k1 * (1 - b + b * dl / avgdl)
            score += idf[term] * numerator / denominator
        scores.append(score)
    return scores


# Backward-compatible private aliases used by older tests/imports.
_tokenize_bm25_text = tokenize_bm25_text
_bm25_scores = bm25_scores
