"""Tests for BM25 sync fallback tokenization."""

from __future__ import annotations

from novel_forge.memory.integration import _bm25_scores, _tokenize_bm25_text


def test_tokenize_bm25_text_splits_chinese_into_bigrams() -> None:
    tokens = _tokenize_bm25_text("张三和李四的关系变化")

    assert "张三" in tokens
    assert "李四" in tokens
    assert "关系" in tokens
    assert "变化" in tokens


def test_bm25_scores_chinese_related_text_above_zero() -> None:
    scores = _bm25_scores(
        "张三和李四的关系变化",
        [
            "张三与李四在雨夜决裂，关系骤然恶化。",
            "王五独自离开了旧城区。",
        ],
    )

    assert scores[0] > 0
    assert scores[0] > scores[1]
