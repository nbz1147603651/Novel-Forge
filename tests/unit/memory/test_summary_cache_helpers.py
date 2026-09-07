from __future__ import annotations

from novel_forge.memory.integration import MemoryContext
from novel_forge.memory.summary_cache_helpers import (
    compute_summary_source_hash,
    normalize_summary_cache_entry,
    normalize_summary_stats,
    should_generate_summary,
)


def test_summary_cache_entry_normalization_preserves_legacy_and_new_shapes() -> None:
    legacy_entry = {
        "text": "旧摘要",
        "source_hash": "",
        "version": 1,
        "updated_at": "",
    }
    new_entry = {
        "text": "新摘要",
        "source_hash": "hash-1",
        "version": 1,
        "updated_at": "123",
    }

    raw_new_entry = {
        "text": " 新摘要 ",
        "source_hash": "hash-1",
        "version": "bad",
        "updated_at": 123,
    }

    assert normalize_summary_cache_entry("  旧摘要  ") == legacy_entry
    assert MemoryContext._normalize_summary_cache_entry("  旧摘要  ") == legacy_entry
    assert normalize_summary_cache_entry(raw_new_entry) == new_entry
    assert MemoryContext._normalize_summary_cache_entry(raw_new_entry) == new_entry
    assert normalize_summary_cache_entry("") is None
    assert MemoryContext._normalize_summary_cache_entry("") is None
    assert normalize_summary_cache_entry({"text": "  "}) is None
    assert MemoryContext._normalize_summary_cache_entry({"text": "  "}) is None


def test_summary_stats_normalization_clamps_invalid_values() -> None:
    raw_stats = {
        "generated": "3",
        "regenerated": -2,
        "hash_skips": "bad",
        "legacy_migrated": 1,
        "invalidated_short": None,
        "reindexed": 5,
        "ignored": 99,
    }
    expected = {
        "generated": 3,
        "regenerated": 0,
        "hash_skips": 0,
        "legacy_migrated": 1,
        "invalidated_short": 0,
        "reindexed": 5,
    }

    assert normalize_summary_stats(raw_stats) == expected
    assert MemoryContext._normalize_summary_stats(raw_stats) == expected


def test_should_generate_summary_migrates_legacy_cache_and_tracks_hashes() -> None:
    ctx = MemoryContext()
    long_text = "x" * 1200
    source_hash = compute_summary_source_hash(long_text)
    ctx._summary_cache[2] = "旧摘要"

    assert ctx.should_generate_summary(2, long_text) is True
    assert ctx._summary_cache[2] == {
        "text": "旧摘要",
        "source_hash": "",
        "version": 1,
        "updated_at": "",
    }
    assert ctx._summary_stats["legacy_migrated"] == 1

    ctx._summary_cache[2]["source_hash"] = source_hash
    assert ctx.should_generate_summary(2, long_text) is False
    assert ctx.should_generate_summary(2, long_text + "changed") is True


def test_should_generate_summary_helper_updates_cache_and_stats() -> None:
    long_text = "x" * 1200
    summary_cache: dict[int, object] = {2: "旧摘要"}
    summary_stats: dict[str, int] = {}

    assert (
        should_generate_summary(
            summary_cache=summary_cache,
            summary_stats=summary_stats,
            chapter_number=2,
            chapter_text=long_text,
        )
        is True
    )
    assert summary_cache[2] == {
        "text": "旧摘要",
        "source_hash": "",
        "version": 1,
        "updated_at": "",
    }
    assert summary_stats["legacy_migrated"] == 1


def test_should_generate_summary_invalidates_stale_short_chapter_summary() -> None:
    ctx = MemoryContext()
    ctx._summary_cache[1] = {
        "text": "旧短章摘要",
        "source_hash": "stale",
        "version": 1,
        "updated_at": "",
    }

    assert ctx.should_generate_summary(1, "short chapter") is False
    assert 1 not in ctx._summary_cache
    assert ctx._summary_stats["invalidated_short"] == 1


def test_should_generate_summary_skips_uncached_short_chapters() -> None:
    ctx = MemoryContext()

    assert ctx.should_generate_summary(9, "short chapter") is False
    assert ctx.should_generate_summary(9, "x" * 1200) is True
