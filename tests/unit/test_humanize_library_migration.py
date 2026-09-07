"""Tests for schema migration in HumanizeLibrary."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from novel_forge.core.schemas.humanize_library import LIBRARY_SCHEMA_VERSION
from novel_forge.memory.humanize_library_store import (
    _MIGRATIONS,
    HumanizeLibrary,
    LibrarySchemaVersionMismatchError,
    MigrationReport,
)

# ---------------------------------------------------------------------------
# Fresh database
# ---------------------------------------------------------------------------


class TestFreshDatabase:
    def test_fresh_db_has_current_version(self, tmp_path: Path) -> None:
        lib = HumanizeLibrary.from_path(tmp_path / "library.db")
        # Verify meta table has current version
        cur = lib._conn.execute(
            "SELECT value FROM meta WHERE key = ?", ("schema_version",)
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == LIBRARY_SCHEMA_VERSION
        lib.close()

    def test_run_migrations_on_fresh(self, tmp_path: Path) -> None:
        lib = HumanizeLibrary.from_path(tmp_path / "library.db")
        report = lib.run_migrations()
        assert isinstance(report, MigrationReport)
        assert report.to_version == LIBRARY_SCHEMA_VERSION
        assert report.success is True
        assert report.steps_applied == []
        lib.close()


# ---------------------------------------------------------------------------
# Version mismatch
# ---------------------------------------------------------------------------


class TestVersionMismatch:
    def test_newer_version_raises(self, tmp_path: Path) -> None:
        """Opening a DB with a newer schema version should raise."""
        db_path = tmp_path / "library.db"
        # Create a valid DB first
        lib = HumanizeLibrary.from_path(db_path)
        lib.close()

        # Manually bump the version to something newer
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE meta SET value = ? WHERE key = ?",
            ("99.0", "schema_version"),
        )
        conn.commit()
        conn.close()

        # Opening should raise
        with pytest.raises(LibrarySchemaVersionMismatchError):
            HumanizeLibrary.from_path(db_path)

    def test_older_version_runs_migrations(self, tmp_path: Path) -> None:
        """Opening a DB with an older version should run migrations."""
        db_path = tmp_path / "library.db"
        lib = HumanizeLibrary.from_path(db_path)
        lib.close()

        # Set version to something older (but valid for chain)
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE meta SET value = ? WHERE key = ?",
            ("1.0", "schema_version"),
        )
        conn.commit()
        conn.close()

        # Opening should succeed and run migrations
        lib2 = HumanizeLibrary.from_path(db_path)
        # Verify version is now current
        cur = lib2._conn.execute(
            "SELECT value FROM meta WHERE key = ?", ("schema_version",)
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == LIBRARY_SCHEMA_VERSION
        lib2.close()


# ---------------------------------------------------------------------------
# Migration registry
# ---------------------------------------------------------------------------


class TestMigrationRegistry:
    def test_migrations_dict_exists(self) -> None:
        assert isinstance(_MIGRATIONS, dict)

    def test_no_migrations_for_v2(self) -> None:
        """v2.0 is the initial version, so no migrations should exist."""
        assert len(_MIGRATIONS) == 0


# ---------------------------------------------------------------------------
# In-memory migration
# ---------------------------------------------------------------------------


class TestInMemoryMigration:
    def test_in_memory_migration(self) -> None:
        lib = HumanizeLibrary.in_memory()
        report = lib.run_migrations()
        assert report.to_version == LIBRARY_SCHEMA_VERSION
        assert report.success is True
        lib.close()
