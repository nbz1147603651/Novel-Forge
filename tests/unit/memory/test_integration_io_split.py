"""Tests for integration_io path helper extraction.

After the revamp(memory) commit, integration_io.py exposes:
  - Private path helpers: _memory_path(storage, project_id), _journal_path(memory_path)
  - Public IO functions: save_to_disk(ctx), load_from_disk(ctx)
  - MemoryIOContext dataclass for IO function arguments

These tests verify:
  1. The path helpers compute the expected Paths
  2. MemoryContext still exposes the same public _get_memory_path / _get_memory_journal_path
  3. A full save+load roundtrip still works (no state-sync regression)
  4. The new MemoryIOContext-based API roundtrips correctly
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from novel_forge.memory.integration_io import (
    MemoryIOContext,
    _journal_path,
    _memory_path,
)
from novel_forge.memory.integration_io import (
    load_from_disk as io_load_from_disk,
)
from novel_forge.memory.integration_io import (
    save_to_disk as io_save_to_disk,
)


def test_memory_path_with_storage() -> None:
    """With a FileSystemStorage, the path uses storage.root."""
    storage = MagicMock()
    storage.root = Path("/data")
    path = _memory_path(storage, "myproject")
    assert path == Path("/data/myproject/memory/project_memory.json")


def test_memory_path_without_storage_falls_back() -> None:
    """Without storage, falls back to a Path built from project_id."""
    path = _memory_path(None, "myproject")
    assert path == Path("myproject/memory/project_memory.json")


def test_journal_path_uses_pending_name() -> None:
    """Journal path uses 'project_memory.pending.json' as filename."""
    memory_path = Path("/data/proj/memory/project_memory.json")
    journal = _journal_path(memory_path)
    assert journal == Path("/data/proj/memory/project_memory.pending.json")
    assert journal.parent == memory_path.parent


def test_memory_context_public_path_methods_still_work() -> None:
    """Verify MemoryContext._get_memory_path / _get_memory_journal_path
    still return the same paths after the integration_io refactor.
    """
    from novel_forge.memory.integration import MemoryContext

    storage = MagicMock()
    storage.root = Path("/data")
    ctx = MemoryContext(_project_id="proj", _storage=storage)
    assert ctx._get_memory_path() == Path("/data/proj/memory/project_memory.json")
    assert ctx._get_memory_journal_path() == Path("/data/proj/memory/project_memory.pending.json")


def test_memory_context_save_load_roundtrip_via_public_api() -> None:
    """End-to-end check: the revamped MemoryContext still does save+load
    correctly with the MemoryIOContext-based path through integration_io.

    CRITICAL invariant: after load_from_disk(), the in-memory state on
    MemoryContext must reflect what was written to disk (no stale copy).
    The revamp uses callbacks (get_last_indexed_chapter / set_last_indexed_chapter)
    on the IO context to keep MemoryContext authoritative.

    STRENGTHENED (P3): now asserts ALL mutable state dicts are restored, not
    just ``_last_indexed_chapter``.
    """
    from novel_forge.memory.integration import MemoryContext
    from novel_forge.persistence.filesystem import FileSystemStorage

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        storage = FileSystemStorage(tmp_path)
        project_id = "test_roundtrip"
        memory_dir = tmp_path / project_id / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        storage.save_json(
            memory_dir / "project_memory.json",
            {
                "last_indexed_chapter": 5,
                "summary_cache": {1: {"text": "chapter one", "source_hash": ""}},
                "chapter_content_hash": {1: "abc123"},
                "summary_stats": {"legacy_migrated": 0, "generated": 0},
            },
        )

        ctx = MemoryContext(_project_id=project_id, _storage=storage)
        result = ctx.load_from_disk()
        assert result is True
        # CRITICAL: after load_from_disk, MemoryContext._last_indexed_chapter
        # must reflect the loaded value (callback-based sync, not value-copy).
        assert ctx._last_indexed_chapter == 5
        # Mutable state dicts restored via in-place updates on the wrappers.
        assert 1 in ctx._summary_cache
        assert ctx._chapter_content_hash.get(1) == "abc123"
        # _summary_stats is a non-empty dict with the recognized keys.
        assert isinstance(ctx._summary_stats, dict)
        assert "legacy_migrated" in ctx._summary_stats


def test_memory_io_context_dataclass_carries_state() -> None:
    """Verify MemoryIOContext can be constructed with all required fields
    (storage, project_id, mutable state dicts, callbacks) and that
    save_to_disk / load_from_disk accept it.

    STRENGTHENED (P3): now asserts ALL mutable state dicts (summary_cache,
    chapter_content_hash, summary_stats) are restored, not just
    ``last_indexed_chapter``.
    """
    from novel_forge.persistence.filesystem import FileSystemStorage

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        storage = FileSystemStorage(tmp_path)
        project_id = "test_ctx"
        memory_dir = tmp_path / project_id / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)

        # Mock callbacks that satisfy the MemoryIOContext interface.
        last_holder = {"value": 0}
        summary_cache: dict[int, dict] = {}
        chapter_content_hash: dict[int, str] = {}
        summary_stats: dict[str, int] = {"generated": 0, "legacy_migrated": 0}
        motif_cache: dict[int, list] = {}

        ctx_io = MemoryIOContext(
            storage=storage,
            project_id=project_id,
            summary_cache=summary_cache,
            chapter_content_hash=chapter_content_hash,
            summary_stats=summary_stats,
            motif_cache=motif_cache,
            get_last_indexed_chapter=lambda: last_holder["value"],
            set_last_indexed_chapter=lambda v: last_holder.__setitem__("value", v),
            summary_service=None,
            serialize_episodic_index=lambda: {},
            deserialize_episodic_index=lambda data: None,
            serialize_motif_tracker=lambda: {},
            deserialize_motif_tracker=lambda data: None,
            normalize_summary_cache_entry=lambda raw: (
                dict(raw) if isinstance(raw, dict) else None
            ),
            normalize_summary_stats=lambda raw: dict(raw) if isinstance(raw, dict) else {},
            rebuild_motif_stats_from_cache=lambda: {},
            load_outline_episodic_from_init=lambda: None,
            flush_episodic_vector_store=lambda: None,
            flush_expression_memory=lambda: None,
        )

        # Roundtrip: set ALL mutable state, save, then load
        last_holder["value"] = 3
        summary_cache[1] = {"text": "chapter one", "source_hash": ""}
        chapter_content_hash[1] = "hash-one"
        summary_stats["generated"] = 1
        summary_stats["legacy_migrated"] = 0

        save_result = io_save_to_disk(ctx_io)
        assert save_result is True
        assert (memory_dir / "project_memory.json").exists()

        # Reset ALL mutable state, then load
        last_holder["value"] = 0
        summary_cache.clear()
        chapter_content_hash.clear()
        summary_stats.clear()
        load_result = io_load_from_disk(ctx_io)
        assert load_result is True
        # ALL mutable state restored (P3 strengthened)
        assert last_holder["value"] == 3
        assert 1 in summary_cache
        assert summary_cache[1].get("text") == "chapter one"
        assert chapter_content_hash.get(1) == "hash-one"
        assert summary_stats.get("generated") == 1


def test_memory_context_save_to_disk_delegates_to_integration_io() -> None:
    """CRITICAL (P1 fix): MemoryContext.save_to_disk must call
    integration_io.save_to_disk. Without this delegation, integration_io
    is dead code and the refactor accomplishes nothing.

    Verified by patching ``novel_forge.memory.integration._io_save_to_disk``
    and asserting it was called once with a MemoryIOContext wired to
    this MemoryContext.
    """
    from novel_forge.memory.integration import MemoryContext
    from novel_forge.persistence.filesystem import FileSystemStorage

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        storage = FileSystemStorage(tmp_path)
        project_id = "test_delegate_save"
        memory_dir = tmp_path / project_id / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        # Stub a project_memory.json with valid data
        storage.save_json(
            memory_dir / "project_memory.json",
            {
                "last_indexed_chapter": 0,
                "summary_cache": {},
                "chapter_content_hash": {},
                "summary_stats": {},
            },
        )

        ctx = MemoryContext(_project_id=project_id, _storage=storage)
        ctx._last_indexed_chapter = 2

        with patch(
            "novel_forge.memory.integration.persistence_mixin._io_save_to_disk",
            wraps=io_save_to_disk,
        ) as mock_io_save:
            result = ctx.save_to_disk()
            assert result is True
            assert mock_io_save.call_count == 1, (
                "MemoryContext.save_to_disk must call integration_io.save_to_disk "
                "exactly once. Without delegation the refactor is dead code."
            )
            call_args = mock_io_save.call_args
            ctx_io_arg = (
                call_args.args[0] if call_args.args else call_args.kwargs.get("ctx")
            )
            assert isinstance(ctx_io_arg, MemoryIOContext)
            assert ctx_io_arg.project_id == project_id
            assert ctx_io_arg.storage is storage
            assert ctx_io_arg.summary_service is None
            assert ctx_io_arg.get_last_indexed_chapter() == 2


def test_memory_context_load_from_disk_delegates_to_integration_io() -> None:
    """CRITICAL (P1 fix): MemoryContext.load_from_disk must call
    integration_io.load_from_disk.
    """
    from novel_forge.memory.integration import MemoryContext
    from novel_forge.persistence.filesystem import FileSystemStorage

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        storage = FileSystemStorage(tmp_path)
        project_id = "test_delegate_load"
        memory_dir = tmp_path / project_id / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        storage.save_json(
            memory_dir / "project_memory.json",
            {
                "last_indexed_chapter": 4,
                "summary_cache": {1: {"text": "first", "source_hash": ""}},
                "chapter_content_hash": {},
                "summary_stats": {},
            },
        )

        ctx = MemoryContext(_project_id=project_id, _storage=storage)

        with patch(
            "novel_forge.memory.integration.persistence_mixin._io_load_from_disk",
            wraps=io_load_from_disk,
        ) as mock_io_load:
            result = ctx.load_from_disk()
            assert result is True
            assert mock_io_load.call_count == 1, (
                "MemoryContext.load_from_disk must call integration_io.load_from_disk "
                "exactly once. Without delegation the refactor is dead code."
            )
            call_args = mock_io_load.call_args
            ctx_io_arg = (
                call_args.args[0] if call_args.args else call_args.kwargs.get("ctx")
            )
            assert isinstance(ctx_io_arg, MemoryIOContext)
            assert ctx_io_arg.project_id == project_id
            assert ctx_io_arg.storage is storage
            assert ctx_io_arg.summary_service is None

        # Post-delegation state must be updated via callbacks
        assert ctx._last_indexed_chapter == 4
        assert 1 in ctx._summary_cache
