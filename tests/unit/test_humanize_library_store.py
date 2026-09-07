"""Tests for HumanizeLibrary CRUD, hit_count, stats, and basic operations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from novel_forge.core.schemas.humanize_library import (
    HumanizeLibraryEntry,
)
from novel_forge.memory.humanize_library_store import (
    HumanizeLibrary,
    ImportReport,
    LibraryDuplicateError,
    LibraryError,
    LibraryReadOnlyError,
    LibraryStats,
    seed_builtin_patterns,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_entry(
    pattern_id: str = "lib_user_aabb0011",
    **overrides: Any,
) -> HumanizeLibraryEntry:
    defaults: dict[str, Any] = {
        "pattern_id": pattern_id,
        "pattern_name": "Test Pattern",
        "category": "测试",
        "severity": "medium",
        "detection_method": "regex",
        "source": "user",
    }
    defaults.update(overrides)
    return HumanizeLibraryEntry(**defaults)


@pytest.fixture()
def mem_lib() -> HumanizeLibrary:
    """In-memory library for testing."""
    lib = HumanizeLibrary.in_memory()
    yield lib
    lib.close()


@pytest.fixture()
def tmp_lib(tmp_path: Path) -> HumanizeLibrary:
    """On-disk library in a temp directory."""
    db_path = tmp_path / "library.db"
    lib = HumanizeLibrary.from_path(db_path)
    yield lib
    lib.close()


# ---------------------------------------------------------------------------
# In-memory CRUD
# ---------------------------------------------------------------------------


class TestInMemoryCRUD:
    def test_add_and_get(self, mem_lib: HumanizeLibrary) -> None:
        entry = _make_entry()
        mem_lib.add(entry)
        result = mem_lib.get(entry.pattern_id)
        assert result is not None
        assert result.pattern_id == entry.pattern_id
        assert result.pattern_name == "Test Pattern"

    def test_get_nonexistent(self, mem_lib: HumanizeLibrary) -> None:
        assert mem_lib.get("lib_user_00000000") is None

    def test_add_duplicate_raises(self, mem_lib: HumanizeLibrary) -> None:
        entry = _make_entry()
        mem_lib.add(entry)
        with pytest.raises(LibraryDuplicateError):
            mem_lib.add(entry)

    def test_list_all(self, mem_lib: HumanizeLibrary) -> None:
        assert mem_lib.list_all() == []
        mem_lib.add(_make_entry("lib_user_aabb0011"))
        mem_lib.add(_make_entry("lib_user_aabb0022", pattern_name="Second"))
        all_entries = mem_lib.list_all()
        assert len(all_entries) == 2
        ids = {e.pattern_id for e in all_entries}
        assert ids == {"lib_user_aabb0011", "lib_user_aabb0022"}

    def test_update(self, mem_lib: HumanizeLibrary) -> None:
        entry = _make_entry()
        mem_lib.add(entry)
        updated = mem_lib.update(entry.pattern_id, pattern_name="Updated Name")
        assert updated.pattern_name == "Updated Name"
        assert updated.pattern_id == entry.pattern_id
        assert updated.vector_stale is True
        assert updated.embedding_signature is None
        # Verify persisted
        fetched = mem_lib.get(entry.pattern_id)
        assert fetched is not None
        assert fetched.pattern_name == "Updated Name"

    def test_update_nonexistent_raises(self, mem_lib: HumanizeLibrary) -> None:
        with pytest.raises(LibraryError):
            mem_lib.update("lib_user_00000000", pattern_name="x")

    def test_update_builtin_raises(self, mem_lib: HumanizeLibrary) -> None:
        seed_builtin_patterns(mem_lib)
        with pytest.raises(LibraryReadOnlyError):
            mem_lib.update("significance_inflation", pattern_name="x")

    def test_remove(self, mem_lib: HumanizeLibrary) -> None:
        entry = _make_entry()
        mem_lib.add(entry)
        assert mem_lib.remove(entry.pattern_id) is True
        assert mem_lib.get(entry.pattern_id) is None

    def test_remove_nonexistent(self, mem_lib: HumanizeLibrary) -> None:
        assert mem_lib.remove("lib_user_00000000") is False

    def test_remove_builtin_raises(self, mem_lib: HumanizeLibrary) -> None:
        seed_builtin_patterns(mem_lib)
        with pytest.raises(LibraryReadOnlyError):
            mem_lib.remove("significance_inflation")

    def test_enable_disable(self, mem_lib: HumanizeLibrary) -> None:
        entry = _make_entry()
        mem_lib.add(entry)
        assert mem_lib.get(entry.pattern_id).enabled is True
        mem_lib.disable(entry.pattern_id)
        assert mem_lib.get(entry.pattern_id).enabled is False
        mem_lib.enable(entry.pattern_id)
        assert mem_lib.get(entry.pattern_id).enabled is True


# ---------------------------------------------------------------------------
# Hit tracking
# ---------------------------------------------------------------------------


class TestHitTracking:
    def test_bump_hit(self, mem_lib: HumanizeLibrary) -> None:
        entry = _make_entry()
        mem_lib.add(entry)
        mem_lib.bump_hit(entry.pattern_id, chapter=3, score=0.85)
        result = mem_lib.get(entry.pattern_id)
        assert result is not None
        assert result.hit_count == 1
        assert result.last_hit_chapter == 3
        assert result.last_seen_at is not None
        assert result.first_seen_at is not None

    def test_bump_hit_multiple(self, mem_lib: HumanizeLibrary) -> None:
        entry = _make_entry()
        mem_lib.add(entry)
        mem_lib.bump_hit(entry.pattern_id, chapter=1, score=0.5)
        mem_lib.bump_hit(entry.pattern_id, chapter=2, score=0.6)
        mem_lib.bump_hit(entry.pattern_id, chapter=3, score=0.7)
        result = mem_lib.get(entry.pattern_id)
        assert result is not None
        assert result.hit_count == 3
        assert result.last_hit_chapter == 3

    def test_bump_hit_nonexistent_no_error(self, mem_lib: HumanizeLibrary) -> None:
        # Should not raise
        mem_lib.bump_hit("lib_user_00000000", chapter=1, score=0.5)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class TestStats:
    def test_empty_stats(self, mem_lib: HumanizeLibrary) -> None:
        s = mem_lib.stats()
        assert isinstance(s, LibraryStats)
        assert s.total == 0
        assert s.enabled == 0

    def test_stats_with_entries(self, mem_lib: HumanizeLibrary) -> None:
        mem_lib.add(_make_entry("lib_user_aabb0011"))
        mem_lib.add(
            _make_entry(
                "lib_user_aabb0022",
                detection_method="llm_only",
                enabled=False,
            )
        )
        s = mem_lib.stats()
        assert s.total == 2
        assert s.enabled == 1
        assert s.regex_count == 1
        assert s.llm_only_count == 1
        assert s.user_count == 2

    def test_is_healthy(self, mem_lib: HumanizeLibrary) -> None:
        assert mem_lib.is_healthy() is True


# ---------------------------------------------------------------------------
# Seed builtin
# ---------------------------------------------------------------------------


class TestSeedBuiltin:
    EXPECTED_TOTAL = 30  # 26 regex (21 original + 5 form-level AI-flavor) + 4 llm_only
    EXPECTED_REGEX = 26
    EXPECTED_LLM_ONLY = 4

    def test_seed_builtin_entries(self, mem_lib: HumanizeLibrary) -> None:
        added = seed_builtin_patterns(mem_lib)
        assert added == self.EXPECTED_TOTAL
        all_entries = mem_lib.list_all()
        assert len(all_entries) == self.EXPECTED_TOTAL

    def test_seed_idempotent(self, mem_lib: HumanizeLibrary) -> None:
        added1 = seed_builtin_patterns(mem_lib)
        added2 = seed_builtin_patterns(mem_lib)
        assert added1 == self.EXPECTED_TOTAL
        assert added2 == 0
        assert len(mem_lib.list_all()) == self.EXPECTED_TOTAL

    def test_seed_counts(self, mem_lib: HumanizeLibrary) -> None:
        seed_builtin_patterns(mem_lib)
        all_entries = mem_lib.list_all()
        regex_count = sum(1 for e in all_entries if e.detection_method == "regex")
        llm_count = sum(1 for e in all_entries if e.detection_method == "llm_only")
        assert regex_count == self.EXPECTED_REGEX
        assert llm_count == self.EXPECTED_LLM_ONLY

    def test_seed_llm_disabled(self, mem_lib: HumanizeLibrary) -> None:
        seed_builtin_patterns(mem_lib)
        all_entries = mem_lib.list_all()
        llm_entries = [e for e in all_entries if e.detection_method == "llm_only"]
        assert all(e.enabled is False for e in llm_entries)


# ---------------------------------------------------------------------------
# On-disk persistence
# ---------------------------------------------------------------------------


class TestOnDisk:
    def test_persist_and_reload(self, tmp_path: Path) -> None:
        db_path = tmp_path / "library.db"
        lib1 = HumanizeLibrary.from_path(db_path)
        lib1.add(_make_entry("lib_user_aabb0011"))
        lib1.close()

        lib2 = HumanizeLibrary.from_path(db_path)
        result = lib2.get("lib_user_aabb0011")
        assert result is not None
        assert result.pattern_name == "Test Pattern"
        lib2.close()

    def test_from_path_directory(self, tmp_path: Path) -> None:
        lib = HumanizeLibrary.from_path(tmp_path)
        lib.add(_make_entry("lib_user_aabb0011"))
        assert lib.get("lib_user_aabb0011") is not None
        lib.close()


# ---------------------------------------------------------------------------
# Export / Import
# ---------------------------------------------------------------------------


class TestExportImport:
    def test_export_import_roundtrip(self, mem_lib: HumanizeLibrary) -> None:
        seed_builtin_patterns(mem_lib)
        mem_lib.add(_make_entry("lib_user_aabb0011"))
        blob = mem_lib.export()
        assert isinstance(blob, bytes)
        assert len(blob) > 0

        # Import into fresh library
        lib2 = HumanizeLibrary.in_memory()
        report = lib2.import_archive(blob)
        assert isinstance(report, ImportReport)
        # Builtin entries should be skipped (already seeded)
        # User entry should be imported
        assert report.imported >= 1

    def test_import_skip_strategy(self, mem_lib: HumanizeLibrary) -> None:
        mem_lib.add(_make_entry("lib_user_aabb0011", pattern_name="Original"))
        blob = mem_lib.export()

        lib2 = HumanizeLibrary.in_memory()
        lib2.add(_make_entry("lib_user_aabb0011", pattern_name="Existing"))
        report = lib2.import_archive(blob, merge_strategy="skip")
        assert report.skipped >= 1
        assert lib2.get("lib_user_aabb0011").pattern_name == "Existing"

    def test_import_overwrite_strategy(self, mem_lib: HumanizeLibrary) -> None:
        mem_lib.add(_make_entry("lib_user_aabb0011", pattern_name="Original"))
        blob = mem_lib.export()

        lib2 = HumanizeLibrary.in_memory()
        lib2.add(_make_entry("lib_user_aabb0011", pattern_name="Existing"))
        report = lib2.import_archive(blob, merge_strategy="overwrite")
        assert report.overwritten >= 1
        assert lib2.get("lib_user_aabb0011").pattern_name == "Original"


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class TestContextManager:
    def test_context_manager(self) -> None:
        with HumanizeLibrary.in_memory() as lib:
            lib.add(_make_entry("lib_user_aabb0011"))
            assert lib.get("lib_user_aabb0011") is not None

    def test_close_idempotent(self) -> None:
        lib = HumanizeLibrary.in_memory()
        lib.close()
        lib.close()  # Should not raise


# ---------------------------------------------------------------------------
# Vector management (with fake Zvec)
# ---------------------------------------------------------------------------


class TestVectorManagement:
    def test_vec_search_no_zvec(self, mem_lib: HumanizeLibrary) -> None:
        result = mem_lib.vec_search([0.0] * 64, top_k=5)
        assert result == []

    def test_vec_search_with_fake_zvec(self, mem_lib: HumanizeLibrary) -> None:
        from tests.unit._fake_zvec_humanize import FakeZvecStore

        fake = FakeZvecStore()
        fake.add("lib_user_aabb0011", [1.0, 0.0, 0.0], {"pattern_id": "lib_user_aabb0011"})
        fake.add("lib_user_aabb0022", [0.0, 1.0, 0.0], {"pattern_id": "lib_user_aabb0022"})
        mem_lib.setup_zvec(fake)

        result = mem_lib.vec_search([1.0, 0.0, 0.0], top_k=2)
        assert len(result) == 2
        assert result[0][0] == "lib_user_aabb0011"
        assert result[0][1] > result[1][1]

    def test_rebuild_vectors(self, mem_lib: HumanizeLibrary) -> None:
        from tests.unit._fake_zvec_humanize import FakeZvecStore

        seed_builtin_patterns(mem_lib)
        fake = FakeZvecStore()
        mem_lib.setup_zvec(fake)

        class FakeEmbedder:
            def embed(self, texts: list[str]) -> list[list[float]]:
                return [[1.0, 0.0, 0.0] for _ in texts]

        result = mem_lib.rebuild_vectors(FakeEmbedder())
        assert result["embedded"] == 30
        assert result["failed"] == 0
        assert fake.count == 30
        assert all(meta.get("content") for meta in fake._metadata.values())

        # Verify embedding signatures are set
        for entry in mem_lib.list_all():
            assert entry.embedding_signature is not None
            assert entry.vector_stale is False

    def test_ensure_vector_index_attaches_and_persists_metadata(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from tests.unit._fake_zvec_humanize import FakeZvecStore

        stores: list[FakeZvecStore] = []

        class FakePersistentZvec(FakeZvecStore):
            def __init__(self, *args: object, **kwargs: object) -> None:
                super().__init__()
                stores.append(self)

        monkeypatch.setattr(
            "novel_forge.memory.zvec_store.ZvecVectorStore",
            FakePersistentZvec,
        )
        lib = HumanizeLibrary.from_path(tmp_path / "library.db")
        seed_builtin_patterns(lib)

        class FakeEmbedder:
            def embed(self, texts: list[str]) -> list[list[float]]:
                return [[1.0, 0.0, 0.0] for _text in texts]

        available = lib.ensure_vector_index(
            FakeEmbedder(),
            provider="test-provider",
            model="test-model",
        )

        assert available is True
        assert lib.vector_available is True
        assert stores and stores[0].count == len(lib.list_all())
        metadata = json.loads((tmp_path / "zvec_meta.json").read_text(encoding="utf-8"))
        assert metadata["dimension"] == 3
        assert metadata["provider"] == "test-provider"
        assert all(entry.embedding_signature for entry in lib.list_all())
