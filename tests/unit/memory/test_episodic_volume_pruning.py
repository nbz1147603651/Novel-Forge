"""Tests for volume-boundary pruning in EpisodicMemory."""

from __future__ import annotations

from novel_forge.memory.episodic import CritiqueIndex, EpisodicMemory, OutlinePlotPoint


async def test_prune_by_volume_skips_without_chapter_threshold() -> None:
    memory = EpisodicMemory(use_mock_embeddings=True)
    await memory.index_chapter_outcome(1, "第一章摘要", "第一章正文")

    stats = memory.prune_by_volume(volume_number=2, keep_recent_volumes=2)

    assert stats["skipped"] == 1
    assert stats["removed_entries"] == 0
    assert len(memory._index) == 1
    assert 1 in memory._chapter_events


async def test_prune_by_volume_removes_old_episodic_and_outline_entries() -> None:
    memory = EpisodicMemory(use_mock_embeddings=True)

    for chapter in range(1, 6):
        await memory.index_chapter_outcome(chapter, f"第{chapter}章摘要", f"第{chapter}章正文")
        await memory._add_outline_entry(
            OutlinePlotPoint(
                chapter_number=chapter,
                plot_point=f"第{chapter}章情节",
            )
        )

    stats = memory.prune_by_volume(
        volume_number=3,
        keep_recent_volumes=2,
        max_chapter_to_keep=3,
        prune_critiques=False,
    )

    assert stats["skipped"] == 0
    assert stats["removed_entries"] == 2
    assert stats["removed_outlines"] == 2
    assert stats["remaining_entries"] == 3
    assert stats["remaining_outlines"] == 3
    assert set(memory._chapter_events) == {3, 4, 5}
    assert set(memory._chapter_outlines) == {3, 4, 5}
    assert all(entry.chapter_number >= 3 for entry in memory._index.values())
    assert all(entry.chapter_number >= 3 for entry in memory._outline_index.values())


async def test_prune_by_volume_can_remove_old_critiques() -> None:
    memory = EpisodicMemory(use_mock_embeddings=True)
    old_sig = await memory._add_critique_entry(
        CritiqueIndex(
            chapter_number=1,
            issue_type="causal_gap",
            severity="medium",
            summary="旧卷因果问题",
        )
    )
    kept_sig = await memory._add_critique_entry(
        CritiqueIndex(
            chapter_number=3,
            issue_type="causal_gap",
            severity="medium",
            summary="近期因果问题",
        )
    )

    stats = memory.prune_by_volume(
        volume_number=3,
        keep_recent_volumes=2,
        max_chapter_to_keep=3,
        prune_critiques=True,
    )

    assert stats["removed_critiques"] == 1
    assert old_sig not in memory._critique_index
    assert kept_sig in memory._critique_index
    assert 1 not in memory._chapter_critiques
    assert set(memory._chapter_critiques) == {3}


async def test_prune_by_volume_can_leave_critiques_for_existing_policy() -> None:
    memory = EpisodicMemory(use_mock_embeddings=True)
    old_sig = await memory._add_critique_entry(
        CritiqueIndex(
            chapter_number=1,
            issue_type="continuity_gap",
            severity="medium",
            summary="旧卷连续性问题",
        )
    )

    stats = memory.prune_by_volume(
        volume_number=3,
        keep_recent_volumes=2,
        max_chapter_to_keep=3,
        prune_critiques=False,
    )

    assert stats["removed_critiques"] == 0
    assert old_sig in memory._critique_index
    assert 1 in memory._chapter_critiques
