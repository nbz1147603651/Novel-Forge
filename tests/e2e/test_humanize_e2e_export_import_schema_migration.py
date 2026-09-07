"""E2E: schema migration scenarios for cross-machine export/import."""

from __future__ import annotations

import json
import tarfile
import tempfile
from pathlib import Path

import pytest

from novel_forge.core.schemas.humanize_library import (
    LIBRARY_SCHEMA_VERSION,
    HumanizeLibraryEntry,
)
from novel_forge.memory.humanize_library_store import (
    HumanizeLibrary,
    LibrarySchemaVersionMismatchError,
    seed_builtin_patterns,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_archive_blob(
    schema_version: str,
    entries: list[dict] | None = None,
) -> bytes:
    """Build a tar.gz archive blob with a custom schema_version in library.json."""
    payload = {
        "schema_version": schema_version,
        "entries": entries or [],
    }
    with tempfile.TemporaryDirectory() as td:
        json_path = Path(td) / "library.json"
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tar_path = Path(td) / "archive.tar.gz"
        with tarfile.open(str(tar_path), "w:gz") as tar:
            tar.add(str(json_path), arcname="library.json")
        return tar_path.read_bytes()


def _sample_entry_dict(**overrides: object) -> dict:
    """Return a minimal valid entry dict for archive injection."""
    base: dict = {
        "pattern_id": "lib_user_deadbeef",
        "pattern_name": "迁移测试模式",
        "category": "测试",
        "severity": "medium",
        "detection_method": "regex",
        "source": "user",
        "keywords": ["迁移"],
        "embedding_signature": None,
        "vector_stale": False,
        "schema_version": "2.0",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_export_with_old_schema_version_then_import_runs_migrations(
    tmp_path: Path,
) -> None:
    """Archive with schema_version='1.0' → import → entries still import (import is lenient).

    The import_archive path validates each entry individually via Pydantic and does
    not reject based on the top-level schema_version.  After import the target
    library's own schema version remains LIBRARY_SCHEMA_VERSION.
    """
    entry = _sample_entry_dict(schema_version="1.0")
    blob = _build_archive_blob(schema_version="1.0", entries=[entry])

    dst = HumanizeLibrary.from_path(tmp_path / "dst")
    try:
        report = dst.import_archive(blob)

        # Entry should be imported successfully
        assert report.imported == 1
        assert report.skipped == 0

        # Verify entry exists
        imported = dst.get("lib_user_deadbeef")
        assert imported is not None
        assert imported.pattern_name == "迁移测试模式"

        # Target library schema version is current
        assert report.schema_upgraded_to == LIBRARY_SCHEMA_VERSION

        # Verify the target library's own migration state is current
        migration_report = dst.run_migrations()
        assert migration_report.from_version == LIBRARY_SCHEMA_VERSION
        assert migration_report.to_version == LIBRARY_SCHEMA_VERSION
        assert migration_report.success is True
    finally:
        dst.close()


def test_import_newer_schema_version_raises(tmp_path: Path) -> None:
    """Library with newer schema version in meta → run_migrations raises.

    Note: import_archive itself does not check the archive's top-level
    schema_version.  The LibrarySchemaVersionMismatchError is raised by
    run_migrations() when the on-disk meta table has a version newer than
    the code supports.
    """
    dst = HumanizeLibrary.from_path(tmp_path / "dst")
    try:
        # Manually set meta schema_version to something newer
        dst._conn.execute(  # noqa: SLF001
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("schema_version", "99.0"),
        )
        dst._conn.commit()  # noqa: SLF001

        with pytest.raises(LibrarySchemaVersionMismatchError, match="99.0"):
            dst.run_migrations()
    finally:
        dst.close()


def test_schema_migration_roundtrip(tmp_path: Path) -> None:
    """Verify library schema matches LIBRARY_SCHEMA_VERSION after full lifecycle."""
    # Create and seed source
    src = HumanizeLibrary.from_path(tmp_path / "src")
    try:
        seed_builtin_patterns(src)
        src.add(
            HumanizeLibraryEntry(
                pattern_id="lib_user_00000001",
                pattern_name="迁移往返测试",
                category="测试",
                severity="medium",
                detection_method="regex",
                source="user",
                keywords=["往返"],
            )
        )

        # Verify source schema is current
        src_migration = src.run_migrations()
        assert src_migration.from_version == LIBRARY_SCHEMA_VERSION
        assert src_migration.to_version == LIBRARY_SCHEMA_VERSION

        blob = src.export()
    finally:
        src.close()

    # Import to target
    dst = HumanizeLibrary.from_path(tmp_path / "dst")
    try:
        report = dst.import_archive(blob)
        assert report.imported == 31  # 30 builtin + 1 user
        assert report.schema_upgraded_to == LIBRARY_SCHEMA_VERSION

        # Target migration state is current
        dst_migration = dst.run_migrations()
        assert dst_migration.from_version == LIBRARY_SCHEMA_VERSION
        assert dst_migration.to_version == LIBRARY_SCHEMA_VERSION
        assert dst_migration.success is True
        assert len(dst_migration.steps_applied) == 0  # no pending migrations

        # All entries accessible
        assert len(dst.list_all()) == 31
    finally:
        dst.close()
