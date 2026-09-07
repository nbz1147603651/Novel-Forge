"""Unit tests for summary hash/version behavior in MemoryContext."""

from __future__ import annotations

from collections import defaultdict

from novel_forge.core.config import Settings
from novel_forge.memory.integration import MemoryContext
from novel_forge.memory.motif import Motif


def _make_ctx() -> MemoryContext:
    return MemoryContext(settings=Settings(), _project_id="test-project")


def test_short_chapter_change_invalidates_stale_summary() -> None:
    ctx = _make_ctx()
    old_text = "a" * 1600
    old_hash = ctx._compute_summary_source_hash(old_text)
    ctx._summary_cache[1] = {
        "text": "旧摘要",
        "source_hash": old_hash,
        "version": 1,
        "updated_at": "",
    }

    should_generate = ctx.should_generate_summary(1, "b" * 300)

    assert should_generate is False
    assert 1 not in ctx._summary_cache
    assert ctx._summary_stats["invalidated_short"] == 1


def test_reindex_same_chapter_when_content_hash_changes() -> None:
    ctx = _make_ctx()
    text_v1 = "第一版" * 600
    text_v2 = "第二版" * 600

    ctx.index_chapter(3, text_v1, async_summarize=False, async_motifs=False)
    hash_v1 = ctx._compute_summary_source_hash(text_v1)
    hash_v2 = ctx._compute_summary_source_hash(text_v2)
    assert ctx._chapter_content_hash[3] == hash_v1

    # Simulate existing summary tied to old text hash.
    ctx._summary_cache[3] = {
        "text": "旧摘要",
        "source_hash": hash_v1,
        "version": 1,
        "updated_at": "",
    }

    ctx.index_chapter(3, text_v2, async_summarize=False, async_motifs=False)

    assert ctx._summary_stats["reindexed"] == 1
    assert ctx._chapter_content_hash[3] == hash_v2
    assert 3 not in ctx._summary_cache


class _MotifStub:
    def __init__(self, occurrence_count: int = 0, first: int = 0, last: int = 0) -> None:
        self.occurrence_count = occurrence_count
        self.first_appearance_chapter = first
        self.last_appearance_chapter = last


class _MotifTrackerStub:
    def __init__(self) -> None:
        self._motifs = {"motif_001": _MotifStub(occurrence_count=2, first=1, last=6)}
        self._chapter_motifs: dict[int, set[str]] = {}
        self._recent_usage: dict[str, list[int]] = defaultdict(list)

    def has_chapter_motifs(self, chapter_number: int) -> bool:
        return bool(self._chapter_motifs.get(chapter_number))


def test_should_extract_motifs_when_cache_exists_but_tracker_missing() -> None:
    ctx = _make_ctx()
    tracker = _MotifTrackerStub()
    ctx._motif_tracker = tracker
    ctx._motif_cache[7] = [{"motif_id": "motif_001", "chapter_number": 7}]

    assert ctx.should_extract_motifs(7, 2200) is True

    tracker._chapter_motifs[7] = {"motif_001"}
    assert ctx.should_extract_motifs(7, 2200) is False


def test_rebuild_motif_stats_from_cache_merges_missing_chapters() -> None:
    ctx = _make_ctx()
    tracker = _MotifTrackerStub()
    ctx._motif_tracker = tracker
    ctx._motif_cache = {
        7: [
            {"motif_id": "motif_001", "chapter_number": 7},
            {"motif_id": "motif_001", "chapter_number": 7},
        ],
        8: [{"motif_id": "motif_001", "chapter_number": 8}],
    }

    ctx._rebuild_motif_stats_from_cache()

    motif = tracker._motifs["motif_001"]
    assert motif.occurrence_count == 3
    assert motif.first_appearance_chapter == 1
    assert motif.last_appearance_chapter == 8
    assert tracker._chapter_motifs[7] == {"motif_001"}
    assert tracker._chapter_motifs[8] == {"motif_001"}
    assert tracker._recent_usage["motif_001"] == [7, 8]


def test_motif_tracker_persistence_preserves_intentionality() -> None:
    ctx = _make_ctx()
    tracker = _MotifTrackerStub()
    tracker._motifs = {
        "motif_unintentional": Motif(
            motif_id="motif_unintentional",
            name="反复雨声",
            category="声音",
            occurrence_count=2,
            first_appearance_chapter=1,
            last_appearance_chapter=2,
            is_intentional=False,
            metadata={
                "category_confidence": 0.88,
                "category_reason": "雨声作为反复听觉通道出现",
                "secondary_categories": ["意象"],
            },
        )
    }
    tracker._chapter_motifs = {1: {"motif_unintentional"}, 2: {"motif_unintentional"}}
    ctx._motif_tracker = tracker

    serialized = ctx._serialize_motif_tracker()

    assert serialized["motifs"]["motif_unintentional"]["is_intentional"] is False
    assert serialized["motifs"]["motif_unintentional"]["metadata"]["category_confidence"] == 0.88

    restored = _MotifTrackerStub()
    restored._motifs = {}
    restored._chapter_motifs = {}
    ctx._motif_tracker = restored
    ctx._deserialize_motif_tracker(serialized)

    assert restored._motifs["motif_unintentional"].is_intentional is False
    assert restored._motifs["motif_unintentional"].metadata["category_reason"] == "雨声作为反复听觉通道出现"
    assert restored._chapter_motifs[2] == {"motif_unintentional"}
