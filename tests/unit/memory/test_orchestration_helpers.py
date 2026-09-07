from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_forge.memory.motif_repair_orchestration import (
    MotifRepairContext,
    re_extract_motifs_from_archived_chapters,
    repair_motif_history_from_cache,
)
from novel_forge.memory.reindex_orchestration import (
    invalidate_chapter_memory,
    rebuild_vector_collection,
)
from novel_forge.memory.vector_shard_io import load_vector_shard, save_vector_shard
from novel_forge.persistence.filesystem import FileSystemStorage


class _RebuildableEpisodic:
    def __init__(self) -> None:
        self.rebuild_calls = 0

    async def rebuild_vector_collection(self) -> dict[str, int]:
        self.rebuild_calls += 1
        return {"vectors": 3}


async def test_rebuild_vector_collection_persists_and_attaches_status() -> None:
    episodic = _RebuildableEpisodic()
    saved = {"count": 0}

    def _save() -> bool:
        saved["count"] += 1
        return True

    result = await rebuild_vector_collection(
        episodic_memory=episodic,
        save_to_disk=_save,
        get_status_summary=lambda: {"cached_motifs": 2},
    )

    assert result == {"vectors": 3, "saved": True, "status": {"cached_motifs": 2}}
    assert episodic.rebuild_calls == 1
    assert saved["count"] == 1


class _InvalidationEpisodic:
    def delete_chapters_from(self, from_chapter: int) -> list[int]:
        return [from_chapter, from_chapter + 1]


class _InvalidationMotifs:
    def delete_chapters_from(self, from_chapter: int) -> int:
        return from_chapter + 4


class _InvalidationExpression:
    def __init__(self) -> None:
        self.deleted: list[int] = []
        self.saved = False

    def delete_chapter(self, chapter: int) -> int:
        self.deleted.append(chapter)
        return 1

    def save(self) -> None:
        self.saved = True


def test_invalidate_chapter_memory_clears_state_and_updates_last_indexed() -> None:
    expression = _InvalidationExpression()
    last_indexed = {"value": 5}
    summary_cache = {1: {"text": "keep"}, 3: {"text": "drop"}}
    chapter_hash = {1: "keep", 3: "drop"}
    motif_cache = {2: ["keep"], 4: ["drop"]}

    result = invalidate_chapter_memory(
        from_chapter=3,
        episodic_memory=_InvalidationEpisodic(),
        motif_tracker=_InvalidationMotifs(),
        summary_cache=summary_cache,
        chapter_content_hash=chapter_hash,
        motif_cache=motif_cache,
        expression_memory=expression,
        get_last_indexed_chapter=lambda: last_indexed["value"],
        set_last_indexed_chapter=lambda value: last_indexed.__setitem__("value", value),
        logger=logging.getLogger("test.memory.reindex"),
    )

    assert result == {
        "from_chapter": 3,
        "episodic_removed": 2,
        "motifs_removed": 7,
        "summaries_cleared": 1,
        "motif_cache_cleared": 1,
        "expression_removed": 3,
    }
    assert summary_cache == {1: {"text": "keep"}}
    assert chapter_hash == {1: "keep"}
    assert motif_cache == {2: ["keep"]}
    assert expression.deleted == [3, 4, 5]
    assert expression.saved is True
    assert last_indexed["value"] == 2


def test_vector_shard_helpers_roundtrip_legacy_embeddings(tmp_path: Path) -> None:
    storage = FileSystemStorage(tmp_path)
    shard_path = tmp_path / "p" / "memory" / "episodic_vectors.json"
    episodic = SimpleNamespace(
        _index={"event": SimpleNamespace(embedding=[0.1, 0.2])},
        _outline_index={"outline": SimpleNamespace(embedding=[0.3, 0.4])},
        _critique_index={"critique": SimpleNamespace(embedding=[])},
    )

    assert save_vector_shard(
        storage=storage,
        shard_path=shard_path,
        episodic_memory=episodic,
        logger=logging.getLogger("test.memory.vector_shard"),
    )

    loaded = load_vector_shard(
        storage=storage,
        shard_path=shard_path,
        logger=logging.getLogger("test.memory.vector_shard"),
    )
    assert set(loaded) == {"event", "outline:outline"}
    assert loaded["event"] == pytest.approx([0.1, 0.2])
    assert loaded["outline:outline"] == pytest.approx([0.3, 0.4])


class _MotifExtractor:
    def __init__(self) -> None:
        self._motifs: dict[str, object] = {}
        self._extraction_cache: dict[int, list[dict[str, object]]] = {}
        self.calls: list[int] = []

    async def extract_from_chapter(
        self,
        *,
        chapter_number: int,
        chapter_text: str,
    ) -> list[dict[str, object]]:
        self.calls.append(chapter_number)
        return [
            {
                "motif_id": f"m{chapter_number}",
                "name": chapter_text.strip()[:4],
                "chapter_number": chapter_number,
            }
        ]


def _motif_context(
    tmp_path: Path,
    tracker: _MotifExtractor,
    motif_cache: dict[int, list[object]] | None = None,
):
    storage = FileSystemStorage(tmp_path)
    save_calls = {"count": 0}
    lock = asyncio.Lock()

    def _save() -> bool:
        save_calls["count"] += 1
        return True

    ctx = MotifRepairContext(
        settings=SimpleNamespace(memory_motif_re_extract_concurrency=2),
        storage=storage,
        project_id="proj",
        motif_tracker=tracker,
        motif_cache=motif_cache if motif_cache is not None else {},
        ensure_lock=lambda: lock,
        rebuild_motif_stats_from_cache=lambda: {"saved": False, "updated": False},
        save_to_disk=_save,
        logger=logging.getLogger("test.memory.motif_repair"),
    )
    return ctx, save_calls


async def test_re_extract_motifs_reads_archived_chapters_and_updates_cache(
    tmp_path: Path,
) -> None:
    tracker = _MotifExtractor()
    project_dir = tmp_path / "proj"
    chapters_dir = project_dir / "chapters"
    chapters_dir.mkdir(parents=True)
    (chapters_dir / "chapter_001.md").write_text("第一章文本", encoding="utf-8")
    (chapters_dir / "chapter_002.md").write_text("第二章文本", encoding="utf-8")
    ctx, save_calls = _motif_context(tmp_path, tracker)

    result = await re_extract_motifs_from_archived_chapters(
        ctx,
        start_chapter=1,
        end_chapter=2,
    )

    assert result["chapters_processed"] == 2
    assert result["motifs_extracted"] == 2
    assert set(ctx.motif_cache) == {1, 2}
    assert tracker.calls == [1, 2]
    assert save_calls["count"] == 1


async def test_repair_motif_history_merges_extraction_cache_and_saves(
    tmp_path: Path,
) -> None:
    tracker = _MotifExtractor()
    tracker._extraction_cache = {3: [{"motif_id": "m3", "chapter_number": 3}]}
    ctx, save_calls = _motif_context(tmp_path, tracker)

    result = await repair_motif_history_from_cache(ctx)

    assert result["ok"] is True
    assert result["layer1"]["extraction_cache_merged"] == 1
    assert result["layer1"]["saved"] is True
    assert ctx.motif_cache == {3: [{"motif_id": "m3", "chapter_number": 3}]}
    assert save_calls["count"] == 1
