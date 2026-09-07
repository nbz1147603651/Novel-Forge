"""Tests for HumanizeLibrary observability events (13 structured events)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from novel_forge.core.schemas.humanize_library import (
    LIBRARY_SCHEMA_VERSION,
    HumanizeLibraryEntry,
)
from novel_forge.memory.humanize_library_store import (
    HumanizeLibrary,
    LibraryLockTimeoutError,
    seed_builtin_patterns,
)
from novel_forge.memory.humanize_retrieval import HumanizeLibraryRetriever
from novel_forge.obs.project_logger import ProjectRunLogger, set_project_logger
from novel_forge.persistence.models import ProjectLayout

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENTRY = HumanizeLibraryEntry(
    pattern_id="lib_user_abc12345",
    pattern_name="用户模式",
    category="测试",
    severity="high",
    detection_method="regex",
    source="user",
    keywords=["test"],
    example_phrases=["示例"],
)


def _make_entry(**overrides: Any) -> HumanizeLibraryEntry:
    defaults: dict[str, Any] = {
        "pattern_id": "lib_user_aabb0011",
        "pattern_name": "Test",
        "category": "测试",
        "severity": "medium",
        "detection_method": "regex",
        "source": "user",
    }
    defaults.update(overrides)
    return HumanizeLibraryEntry(**defaults)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def event_logger(tmp_path: Path) -> ProjectRunLogger:
    """Set up a ProjectRunLogger backed by tmp_path and register it globally."""
    layout = ProjectLayout(tmp_path)
    logger = ProjectRunLogger(layout=layout, project_id="test", command="observability-test")
    set_project_logger(logger)
    yield logger
    set_project_logger(None)


def _read_events(logger: ProjectRunLogger) -> list[dict[str, Any]]:
    """Read all events from the logger's events.jsonl."""
    path = logger.events_path
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _events_named(events: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [e for e in events if e.get("event") == name]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInitializationEvents:
    """Events 1-2: initialized, unavailable."""

    def test_initialized_on_from_path(self, event_logger: ProjectRunLogger, tmp_path: Path) -> None:
        db_path = tmp_path / "test_lib.db"
        lib = HumanizeLibrary.from_path(db_path)
        events = _read_events(event_logger)
        init_events = _events_named(events, "humanize_library.initialized")
        assert len(init_events) >= 1
        data = init_events[0]["data"]
        assert data["total_entries"] == 0
        assert data["path"].endswith("test_lib.db")
        assert data["schema_version"] == LIBRARY_SCHEMA_VERSION
        lib.close()

    def test_initialized_on_in_memory(self, event_logger: ProjectRunLogger) -> None:
        lib = HumanizeLibrary.in_memory()
        events = _read_events(event_logger)
        init_events = _events_named(events, "humanize_library.initialized")
        assert len(init_events) >= 1
        assert init_events[0]["data"]["path"] == ":memory:"
        lib.close()

    def test_unavailable_on_bad_path(self, event_logger: ProjectRunLogger) -> None:
        bad_dir = Path("/nonexistent_xyzzy/humanize_library.db")
        with pytest.raises((OSError, sqlite3.OperationalError)):
            HumanizeLibrary.from_path(bad_dir)
        events = _read_events(event_logger)
        unavailable = _events_named(events, "humanize_library.unavailable")
        assert len(unavailable) >= 1
        assert "error_type" in unavailable[0]["data"]
        assert "error_msg" in unavailable[0]["data"]


class TestSeedMigrationEvents:
    """Events 4-5: seed_completed, migration_applied."""

    def test_seed_completed(self, event_logger: ProjectRunLogger, tmp_path: Path) -> None:
        db_path = tmp_path / "seed_test.db"
        lib = HumanizeLibrary.from_path(db_path)
        added = seed_builtin_patterns(lib)
        assert added > 0
        events = _read_events(event_logger)
        seed_events = _events_named(events, "humanize_library.seed_completed")
        assert len(seed_events) >= 1
        data = seed_events[0]["data"]
        assert data["added"] == added
        assert data["total"] >= added
        lib.close()

    def test_seed_completed_skips_duplicates(self, event_logger: ProjectRunLogger, tmp_path: Path) -> None:
        db_path = tmp_path / "seed_dup_test.db"
        lib = HumanizeLibrary.from_path(db_path)
        seed_builtin_patterns(lib)
        # Clear events after first seed
        event_logger.events_path.write_text("")
        added = seed_builtin_patterns(lib)
        assert added == 0
        events = _read_events(event_logger)
        seed_events = _events_named(events, "humanize_library.seed_completed")
        assert len(seed_events) >= 1
        assert seed_events[0]["data"]["added"] == 0
        lib.close()

    def test_migration_applied(self, event_logger: ProjectRunLogger, tmp_path: Path) -> None:
        db_path = tmp_path / "migrate_test.db"
        lib = HumanizeLibrary.from_path(db_path)
        # run_migrations is called in __init__, but if no migrations pending
        # it won't emit. We test that no migration event was emitted (no steps)
        events = _read_events(event_logger)
        mig_events = _events_named(events, "humanize_library.migration_applied")
        # Fresh lib has no pending migrations
        assert len(mig_events) == 0
        lib.close()


class TestLockEvents:
    """Events 6-7: lock_acquired, lock_timeout."""

    def test_lock_acquired(self, event_logger: ProjectRunLogger, tmp_path: Path) -> None:
        db_path = tmp_path / "lock_test.db"
        lib = HumanizeLibrary.from_path(db_path)
        with lib.library_lock("test_op"):
            pass
        events = _read_events(event_logger)
        lock_events = _events_named(events, "humanize_library.lock_acquired")
        lock_events = [e for e in lock_events if e["data"].get("operation") == "test_op"]
        assert len(lock_events) >= 1
        assert lock_events[0]["data"]["operation"] == "test_op"
        assert lock_events[0]["data"]["duration_ms"] >= 0
        lib.close()

    def test_lock_timeout(self, event_logger: ProjectRunLogger, tmp_path: Path) -> None:
        db_path = tmp_path / "lock_timeout_test.db"
        lib = HumanizeLibrary.from_path(db_path)
        # Lower timeout to speed test
        lib._lock_timeout = 0.1
        # Lock the file externally to force timeout
        lock_path = tmp_path / "humanize_library.lock"
        fd = open(lock_path, "w")
        import fcntl
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        try:
            with pytest.raises(LibraryLockTimeoutError):
                with lib.library_lock("timeout_op"):
                    pass
        finally:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            fd.close()
        events = _read_events(event_logger)
        timeout_events = _events_named(events, "humanize_library.lock_timeout")
        timeout_events = [e for e in timeout_events if e["data"].get("operation") == "timeout_op"]
        assert len(timeout_events) >= 1
        assert timeout_events[0]["data"]["waited_ms"] >= 0
        lib.close()


class TestCRUDEvents:
    """Events 8-9: entry_added, entry_bumped."""

    def test_entry_added(self, event_logger: ProjectRunLogger, tmp_path: Path) -> None:
        db_path = tmp_path / "crud_test.db"
        lib = HumanizeLibrary.from_path(db_path)
        lib.add(_ENTRY)
        events = _read_events(event_logger)
        added = _events_named(events, "humanize_library.entry_added")
        assert len(added) >= 1
        assert added[0]["data"]["pattern_id"] == "lib_user_abc12345"
        assert added[0]["data"]["source"] == "user"
        lib.close()

    def test_entry_bumped_aggregate(self, event_logger: ProjectRunLogger, tmp_path: Path) -> None:
        db_path = tmp_path / "bump_test.db"
        lib = HumanizeLibrary.from_path(db_path)
        lib.add(_ENTRY)
        # Provide a mock "report" with hits
        class MockReport:
            hits = [{"pattern_id": "lib_user_abc12345", "score": 0.85}]

        count = lib.bump_hits_from_report(MockReport(), chapter=1)
        assert count == 1
        events = _read_events(event_logger)
        bumped = _events_named(events, "humanize_library.entry_bumped")
        assert len(bumped) >= 1
        assert bumped[0]["data"]["count"] == 1
        assert bumped[0]["data"]["chapter"] == 1
        lib.close()


class TestDegradedEvent:
    """Event 3: degraded."""

    def test_degraded_on_vec_search_failure(self, event_logger: ProjectRunLogger, tmp_path: Path) -> None:
        db_path = tmp_path / "degraded_test.db"
        lib = HumanizeLibrary.from_path(db_path)
        # Set up a zvec store that raises
        broken_store = MagicMock()
        broken_store.search.side_effect = RuntimeError("zvec down")
        lib.setup_zvec(broken_store)

        result = lib.vec_search([0.1, 0.2], top_k=5)
        assert result == []  # Graceful degradation
        events = _read_events(event_logger)
        degraded = _events_named(events, "humanize_library.degraded")
        assert len(degraded) >= 1
        assert degraded[0]["data"]["fallback_mode"] == "fts5_only"
        lib.close()


class TestRebuildProgressEvent:
    """Event 13: rebuild_progress."""

    def test_rebuild_progress(self, event_logger: ProjectRunLogger, tmp_path: Path) -> None:
        db_path = tmp_path / "rebuild_test.db"
        lib = HumanizeLibrary.from_path(db_path)
        seed_builtin_patterns(lib)

        class FakeEmbedder:
            def embed(self, texts: list[str]) -> list[list[float]]:
                return [[0.1] * 16 for _ in texts]

        result = lib.rebuild_vectors(FakeEmbedder(), batch_size=64)
        assert result["embedded"] > 0
        events = _read_events(event_logger)
        progress = _events_named(events, "humanize_library.rebuild_progress")
        assert len(progress) >= 1
        assert progress[0]["data"]["processed"] > 0
        assert progress[0]["data"]["total"] > 0
        lib.close()


class TestRetrievalEvents:
    """Events 10-12: retrieval.invoked, retrieved.completed, retrieval.failed."""

    def test_retrieval_invoked_and_completed(
        self, event_logger: ProjectRunLogger, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "retrieval_test.db"
        lib = HumanizeLibrary.from_path(db_path)
        seed_builtin_patterns(lib)
        retriever = HumanizeLibraryRetriever(top_k=5, sim_threshold=0.0)

        chapter_text = "这是一个测试章节。它包含一些内容。"
        retriever.retrieve(lib, chapter_text)
        events = _read_events(event_logger)
        invoked = _events_named(events, "humanize_library.retrieval.invoked")
        completed = _events_named(events, "humanize_library.retrieval.completed")
        assert len(invoked) >= 1
        assert invoked[0]["data"]["top_k"] == 5
        assert invoked[0]["data"]["query_sentences"] >= 1
        assert len(completed) >= 1
        assert "duration_ms" in completed[0]["data"]
        lib.close()

    def test_retrieval_failed(self, event_logger: ProjectRunLogger, tmp_path: Path) -> None:
        db_path = tmp_path / "retrieval_fail_test.db"
        lib = HumanizeLibrary.from_path(db_path)
        seed_builtin_patterns(lib)
        retriever = HumanizeLibraryRetriever(top_k=5)

        # Force retrieval failure by patching _retrieve_impl to raise
        with patch.object(
            retriever,
            "_retrieve_impl",
            side_effect=RuntimeError("retrieval crashed"),
        ):
            result = retriever.retrieve(lib, chapter_text="测试内容。")
        assert result == []
        events = _read_events(event_logger)
        failed = _events_named(events, "humanize_library.retrieval.failed")
        assert len(failed) >= 1
        assert failed[0]["data"]["error_type"] == "retrieval_error"
        assert failed[0]["data"]["fallback"] == "empty_list"


class TestEmitSafety:
    """Event emission failures must never block the caller."""

    def test_emit_failure_tolerated(self, tmp_path: Path) -> None:
        """_safe_log_event should not raise when get_project_logger fails."""
        # Patch get_project_logger to raise
        with patch(
            "novel_forge.memory.humanize_library_store.get_project_logger",
            side_effect=RuntimeError("logger down"),
        ):
            lib = HumanizeLibrary.in_memory()
            assert lib.list_all() == []
            lib.close()

    def test_emit_no_logger_set(self, tmp_path: Path) -> None:
        """When no logger is set, events are silently skipped."""
        set_project_logger(None)
        lib = HumanizeLibrary.in_memory()
        assert lib.list_all() == []
        lib.close()


class TestAggregateEventChecks:
    """Verify specific aggregate behavior."""

    def test_entry_bumped_empty_report_no_event(self, event_logger: ProjectRunLogger, tmp_path: Path) -> None:
        db_path = tmp_path / "nobump_test.db"
        lib = HumanizeLibrary.from_path(db_path)

        class EmptyReport:
            hits = []

        count = lib.bump_hits_from_report(EmptyReport(), chapter=1)
        assert count == 0
        events = _read_events(event_logger)
        bumped = _events_named(events, "humanize_library.entry_bumped")
        bumped = [e for e in bumped if e["data"].get("chapter") == 1]
        assert len(bumped) == 0
        lib.close()
