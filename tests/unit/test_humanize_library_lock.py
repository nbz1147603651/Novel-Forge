"""Tests for fcntl lock concurrency in HumanizeLibrary."""

from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from novel_forge.core.schemas.humanize_library import HumanizeLibraryEntry
from novel_forge.memory.humanize_library_store import (
    HumanizeLibrary,
    LibraryLockTimeoutError,
)


def _make_entry(suffix: str) -> HumanizeLibraryEntry:
    return HumanizeLibraryEntry(
        pattern_id=f"lib_user_{suffix}",
        pattern_name=f"Pattern {suffix}",
        category="测试",
        severity="medium",
        detection_method="regex",
        source="user",
    )


# ---------------------------------------------------------------------------
# Single-process lock tests
# ---------------------------------------------------------------------------


class TestLockSingleProcess:
    def test_lock_context_manager(self, tmp_path: Path) -> None:
        lib = HumanizeLibrary.from_path(tmp_path / "library.db")
        lib.add(_make_entry("aabb0011"))
        assert lib.get("lib_user_aabb0011") is not None
        lib.close()

    def test_lock_in_memory_noop(self) -> None:
        """In-memory library should skip locking."""
        lib = HumanizeLibrary.in_memory()
        with lib.library_lock("test"):
            lib.add(_make_entry("aabb0011"))
        assert lib.get("lib_user_aabb0011") is not None
        lib.close()

    def test_lock_timeout(self, tmp_path: Path) -> None:
        """Lock with very short timeout should raise if already held."""
        lib = HumanizeLibrary.from_path(tmp_path / "library.db")
        lib._lock_timeout = 0.01  # Very short timeout

        # Acquire lock manually
        lock_path = tmp_path / "humanize_library.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        import fcntl

        fd = open(lock_path, "a+")  # noqa: SIM115
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)

        try:
            with pytest.raises(LibraryLockTimeoutError):
                with lib.library_lock("test"):
                    pass
        finally:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            fd.close()
            lib.close()


# ---------------------------------------------------------------------------
# Multi-process lock tests
# ---------------------------------------------------------------------------


def _writer_process(path_str: str, pid_hex: str, count: int) -> None:
    import time as _time

    db_path = Path(path_str) / "library.db"
    lib = None
    for _ in range(5):
        try:
            lib = HumanizeLibrary.from_path(db_path)
            break
        except Exception:
            _time.sleep(0.2)
    if lib is None:
        return
    for i in range(count):
        pid = f"lib_user_{pid_hex}{i:04x}"
        entry = HumanizeLibraryEntry(
            pattern_id=pid,
            pattern_name=f"Pattern {pid_hex}{i:04x}",
            category="测试",
            severity="medium",
            detection_method="regex",
            source="user",
        )
        for _retry in range(5):
            try:
                lib.add(entry)
                break
            except Exception:
                _time.sleep(0.1)
    lib.close()


class TestLockMultiProcess:
    @pytest.mark.timeout(30)
    def test_concurrent_writes_no_data_loss(self, tmp_path: Path) -> None:
        count_per_process = 20
        p1 = multiprocessing.Process(
            target=_writer_process, args=(str(tmp_path), "aa11", count_per_process)
        )
        p2 = multiprocessing.Process(
            target=_writer_process, args=(str(tmp_path), "bb22", count_per_process)
        )

        p1.start()
        p2.start()
        p1.join(timeout=25)
        p2.join(timeout=25)

        lib = HumanizeLibrary.from_path(tmp_path / "library.db")
        all_entries = lib.list_all()
        lib.close()

        assert len(all_entries) == count_per_process * 2

    @pytest.mark.timeout(30)
    def test_concurrent_writes_different_ids(self, tmp_path: Path) -> None:
        count_per_process = 10
        p1 = multiprocessing.Process(
            target=_writer_process, args=(str(tmp_path), "cc33", count_per_process)
        )
        p2 = multiprocessing.Process(
            target=_writer_process, args=(str(tmp_path), "dd44", count_per_process)
        )

        p1.start()
        p2.start()
        p1.join(timeout=25)
        p2.join(timeout=25)

        lib = HumanizeLibrary.from_path(tmp_path / "library.db")
        all_entries = lib.list_all()
        ids = {e.pattern_id for e in all_entries}
        lib.close()

        expected_c = {f"lib_user_cc33{i:04x}" for i in range(count_per_process)}
        expected_d = {f"lib_user_dd44{i:04x}" for i in range(count_per_process)}
        assert expected_c.issubset(ids)
        assert expected_d.issubset(ids)
