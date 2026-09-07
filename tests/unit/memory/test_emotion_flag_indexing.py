"""Tests for emotion/flag metadata indexing in EpisodicMemory."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from novel_forge.memory.episodic import EpisodicMemory


@dataclass
class FakeOutcome:
    chapter_summary: str = ""
    source_chapter: int = 0
    character_updates: dict = field(default_factory=dict)
    new_events: list = field(default_factory=list)
    text: str = ""
    creative_report: Any = None
    alignment_report: Any = None
    plan: Any = None


@pytest.mark.asyncio
async def test_index_chapter_persists_emotion_codes_and_flags() -> None:
    memory = EpisodicMemory(
        embedding_config={"model": "fake-embedding", "dimensions": 8},
        use_mock_embeddings=True,
    )
    outcome = FakeOutcome(
        chapter_summary="主角在绝望中做出决定。",
        source_chapter=3,
        character_updates={"主角": {}},
        text="主角陷入绝望，却还是做出决定。这是一切真正的开始。",
    )

    signatures = await memory.index_chapter(outcome)
    entry = memory._index[signatures[0]]

    assert entry.metadata["emotion_codes"] == ["despair"]
    assert entry.metadata["importance_flags"] == ["DECISION", "ORIGIN"]

    results = memory.search_by_temporal(3, 3)
    assert results[0].metadata["emotion_codes"] == ["despair"]
    assert results[0].metadata["importance_flags"] == ["DECISION", "ORIGIN"]
