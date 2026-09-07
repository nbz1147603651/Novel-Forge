"""Tests for EpisodicMemory automatic decay pruning (prune_episodic_by_recency)."""

from __future__ import annotations

from novel_forge.memory.episodic import EpisodicIndex, EpisodicMemory


def _make_entry(
    chapter: int,
    *,
    scene: int = 0,
    event_types: list[str] | None = None,
    characters: list[str] | None = None,
    keywords: list[str] | None = None,
    full_text: str = "",
) -> EpisodicIndex:
    return EpisodicIndex(
        chapter_number=chapter,
        event_summary=f"Event in chapter {chapter}",
        scene_index=scene,
        full_text=full_text,
        characters=characters or [],
        locations=[],
        event_types=event_types or [],
        emotional_tone="",
        timestamp_in_story="",
        keywords=keywords or [],
        embedding=[],
        metadata={},
    )


def test_no_prune_below_threshold() -> None:
    memory = EpisodicMemory(use_mock_embeddings=True)
    for ch in range(1, 1001):
        entry = _make_entry(ch)
        sig = entry.signature()
        memory._index[sig] = entry
        memory._chapter_events.setdefault(ch, []).append(sig)

    stats = memory.prune_episodic_by_recency(decay_threshold=1000)

    assert stats["triggered"] == 0
    assert stats["removed"] == 0
    assert stats["remaining"] == 1000
    assert len(memory._index) == 1000


def test_prune_above_threshold() -> None:
    memory = EpisodicMemory(use_mock_embeddings=True)
    for ch in range(1, 1501):
        entry = _make_entry(ch)
        sig = entry.signature()
        memory._index[sig] = entry
        memory._chapter_events.setdefault(ch, []).append(sig)

    stats = memory.prune_episodic_by_recency(decay_threshold=1000, keep_recent=500, keep_relevant=200)

    assert stats["triggered"] == 1
    assert stats["removed"] > 0
    assert stats["remaining"] <= 700
    assert len(memory._index) == stats["remaining"]


def test_keeps_recent_chapters() -> None:
    memory = EpisodicMemory(use_mock_embeddings=True)
    for ch in range(1, 1201):
        entry = _make_entry(ch)
        sig = entry.signature()
        memory._index[sig] = entry
        memory._chapter_events.setdefault(ch, []).append(sig)

    memory.prune_episodic_by_recency(decay_threshold=1000, keep_recent=500, keep_relevant=0)

    recent_chapters = set(range(701, 1201))
    for entry in memory._index.values():
        assert entry.chapter_number in recent_chapters


def test_keeps_high_relevance_old_entry() -> None:
    memory = EpisodicMemory(use_mock_embeddings=True)
    for ch in range(1, 1201):
        entry = _make_entry(ch)
        sig = entry.signature()
        memory._index[sig] = entry
        memory._chapter_events.setdefault(ch, []).append(sig)

    high_sig = _make_entry(
        chapter=5,
        event_types=["conflict", "revelation", "decision", "climax"],
        characters=["Alice", "Bob", "Charlie", "Diana", "Eve"],
        keywords=["sword", "betrayal", "prophecy", "kingdom", "war", "magic"],
        full_text="x" * 5000,
    ).signature()
    memory._index[high_sig] = _make_entry(
        chapter=5,
        event_types=["conflict", "revelation", "decision", "climax"],
        characters=["Alice", "Bob", "Charlie", "Diana", "Eve"],
        keywords=["sword", "betrayal", "prophecy", "kingdom", "war", "magic"],
        full_text="x" * 5000,
    )
    memory._chapter_events.setdefault(5, []).append(high_sig)

    memory.prune_episodic_by_recency(decay_threshold=1000, keep_recent=500, keep_relevant=200)

    assert high_sig in memory._index


def test_settings_override_threshold() -> None:
    memory = EpisodicMemory(use_mock_embeddings=True)
    for ch in range(1, 301):
        entry = _make_entry(ch)
        sig = entry.signature()
        memory._index[sig] = entry
        memory._chapter_events.setdefault(ch, []).append(sig)

    class FakeSettings:
        memory_episodic_decay_threshold = 200

    memory.settings = FakeSettings()

    stats = memory.prune_episodic_by_recency(keep_recent=100, keep_relevant=50)

    assert stats["triggered"] == 1
    assert stats["remaining"] <= 150
