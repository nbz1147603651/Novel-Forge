"""Jieba-backed tokenization for Phase 0a retrieval evaluation."""

from __future__ import annotations

import re

import jieba  # type: ignore[import-untyped]

_STOPWORDS = frozenset({"的", "了", "在", "是", "和", "与", "对", "把", "被", "从"})
_HAS_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def tokenize_for_recall(text: str, *, min_len: int = 2, max_len: int = 12) -> set[str]:
    """Tokenize Chinese narrative text for deterministic recall metrics."""

    if not text:
        return set()
    tokens: set[str] = set()
    for raw in jieba.cut(str(text), cut_all=False):
        token = str(raw or "").strip()
        if not token or token in _STOPWORDS:
            continue
        if len(token) < min_len or len(token) > max_len:
            continue
        if not _HAS_CJK_RE.search(token):
            continue
        tokens.add(token)
    return tokens
