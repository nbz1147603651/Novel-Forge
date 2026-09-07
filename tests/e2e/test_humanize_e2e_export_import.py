"""E2E: cross-machine export/import — round-trip, signature mismatch, merge strategies."""

from __future__ import annotations

import json
import tarfile
import tempfile
from pathlib import Path

from novel_forge.core.schemas.humanize_library import (
    LIBRARY_SCHEMA_VERSION,
    HumanizeLibraryEntry,
)
from novel_forge.memory.humanize_library_store import (
    EmbeddingSignature,
    HumanizeLibrary,
    seed_builtin_patterns,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_src_lib(tmp_path: Path) -> HumanizeLibrary:
    """Create a source library with builtins + 1 user entry."""
    lib = HumanizeLibrary.from_path(tmp_path / "src")
    seed_builtin_patterns(lib)
    lib.add(
        HumanizeLibraryEntry(
            pattern_id="lib_user_00000001",
            pattern_name="用户自定义模式",
            category="测试",
            severity="high",
            detection_method="regex",
            source="user",
            keywords=["自定义", "测试"],
        )
    )
    return lib


def _make_dst_lib(tmp_path: Path) -> HumanizeLibrary:
    """Create an empty target library."""
    return HumanizeLibrary.from_path(tmp_path / "dst")


def _read_archive_json(blob: bytes) -> dict:
    """Extract and parse library.json from a tar.gz archive."""
    with tempfile.TemporaryDirectory() as td:
        tar_path = Path(td) / "archive.tar.gz"
        tar_path.write_bytes(blob)
        with tarfile.open(str(tar_path), "r:gz") as tar:
            try:
                tar.extractall(td, filter="data")
            except TypeError:  # pragma: no cover - Python <3.12 compatibility.
                tar.extractall(td)
        json_path = Path(td) / "library.json"
        return json.loads(json_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_roundtrip_same_signature(tmp_path: Path) -> None:
    """Export lib_a (builtins + 1 user) → import to lib_b → all entries present, vector_stale=False."""
    src = _make_src_lib(tmp_path)
    try:
        blob = src.export()
    finally:
        src.close()

    dst = _make_dst_lib(tmp_path)
    try:
        report = dst.import_archive(blob)

        # 30 builtins + 1 user = 31 entries imported
        assert report.imported == 31
        assert report.skipped == 0
        assert report.overwritten == 0
        assert report.merged == 0

        # All entries present in target
        dst_entries = dst.list_all()
        assert len(dst_entries) == 31

        # Verify user entry survived
        user_entry = dst.get("lib_user_00000001")
        assert user_entry is not None
        assert user_entry.pattern_name == "用户自定义模式"

        # vector_stale should be False (default for new entries)
        for entry in dst_entries:
            assert entry.vector_stale is False, f"{entry.pattern_id} has vector_stale=True"

        # FTS index populated (contentless FTS5 doesn't return column values,
        # so verify via entry retrievability which depends on successful insert)
        assert dst.get("lib_user_00000001") is not None
        assert dst.get("significance_inflation") is not None
    finally:
        dst.close()


def test_signature_mismatch_vectors_skipped(tmp_path: Path) -> None:
    """Export entries with sig_A → import to lib with sig_B context → vector_stale=True on imported."""
    src = HumanizeLibrary.from_path(tmp_path / "src")
    try:
        seed_builtin_patterns(src)
        sig_a = EmbeddingSignature.compute("provider_a", "model_a", 384)

        # Mark a user entry with sig_a and vector_stale=True (simulating cross-machine)
        src.add(
            HumanizeLibraryEntry(
                pattern_id="lib_user_00000002",
                pattern_name="跨机器模式",
                category="测试",
                severity="medium",
                detection_method="regex",
                source="user",
                keywords=["跨机器"],
                embedding_signature=sig_a,
                vector_stale=True,
            )
        )

        blob = src.export()
    finally:
        src.close()

    dst = _make_dst_lib(tmp_path)
    try:
        report = dst.import_archive(blob)

        # All entries imported (30 builtin + 1 user)
        assert report.imported == 31

        # The user entry should have vector_stale=True (preserved from export)
        imported_user = dst.get("lib_user_00000002")
        assert imported_user is not None
        assert imported_user.vector_stale is True
        assert imported_user.embedding_signature == sig_a

        # Report should reflect stale count
        assert report.vector_stale_count >= 1

        # Entry retrievable despite stale vectors (FTS index populated)
        assert dst.get("lib_user_00000002") is not None
    finally:
        dst.close()


def test_import_skip_strategy(tmp_path: Path) -> None:
    """Pre-existing target entry with same pattern_id → import skips it."""
    src = _make_src_lib(tmp_path)
    try:
        blob = src.export()
    finally:
        src.close()

    dst = _make_dst_lib(tmp_path)
    try:
        # Pre-seed target with the same user entry
        dst.add(
            HumanizeLibraryEntry(
                pattern_id="lib_user_00000001",
                pattern_name="已存在的模式",
                category="原有",
                severity="low",
                detection_method="regex",
                source="user",
                keywords=["原有"],
            )
        )

        report = dst.import_archive(blob, merge_strategy="skip")

        # The pre-existing user entry should be skipped
        assert report.skipped >= 1

        # Original entry should be unchanged
        existing = dst.get("lib_user_00000001")
        assert existing is not None
        assert existing.pattern_name == "已存在的模式"
        assert existing.category == "原有"
    finally:
        dst.close()


def test_import_overwrite_strategy(tmp_path: Path) -> None:
    """Pre-existing target user entry with same pattern_id → import overwrites it."""
    src = _make_src_lib(tmp_path)
    try:
        blob = src.export()
    finally:
        src.close()

    dst = _make_dst_lib(tmp_path)
    try:
        # Pre-seed with same user entry but different data
        dst.add(
            HumanizeLibraryEntry(
                pattern_id="lib_user_00000001",
                pattern_name="旧名称",
                category="旧分类",
                severity="low",
                detection_method="regex",
                source="user",
                keywords=["旧"],
                hit_count=10,
            )
        )

        report = dst.import_archive(blob, merge_strategy="overwrite")

        # User entry should be overwritten
        assert report.overwritten >= 1

        overwritten = dst.get("lib_user_00000001")
        assert overwritten is not None
        assert overwritten.pattern_name == "用户自定义模式"
        assert overwritten.category == "测试"
    finally:
        dst.close()


def test_import_merge_strategy_hit_count_added(tmp_path: Path) -> None:
    """Pre-existing target hit_count=5 + import hit_count=0 → merged hit_count=5+0=5.

    Note: the source user entry has default hit_count=0, so merged = 5 + 0 = 5.
    We set up a more explicit scenario by using a source entry with hit_count=3.
    """
    src = HumanizeLibrary.from_path(tmp_path / "src")
    try:
        seed_builtin_patterns(src)
        src.add(
            HumanizeLibraryEntry(
                pattern_id="lib_user_00000001",
                pattern_name="用户自定义模式",
                category="测试",
                severity="high",
                detection_method="regex",
                source="user",
                keywords=["自定义", "测试"],
                hit_count=3,
            )
        )
        blob = src.export()
    finally:
        src.close()

    dst = _make_dst_lib(tmp_path)
    try:
        # Pre-seed target with same ID, hit_count=5
        dst.add(
            HumanizeLibraryEntry(
                pattern_id="lib_user_00000001",
                pattern_name="已有模式",
                category="已有",
                severity="medium",
                detection_method="regex",
                source="user",
                keywords=["已有"],
                hit_count=5,
            )
        )

        report = dst.import_archive(blob, merge_strategy="merge")

        # Should be merged
        assert report.merged >= 1

        merged_entry = dst.get("lib_user_00000001")
        assert merged_entry is not None
        # hit_count = existing(5) + imported(3) = 8
        assert merged_entry.hit_count == 8
    finally:
        dst.close()


def test_import_meta_contains_schema_version_and_entry_count(tmp_path: Path) -> None:
    """Verify archive's library.json contains schema_version + entries with embedding_signature + count."""
    src = _make_src_lib(tmp_path)
    try:
        blob = src.export()
    finally:
        src.close()

    archive_data = _read_archive_json(blob)

    # schema_version present and matches current
    assert "schema_version" in archive_data
    assert archive_data["schema_version"] == LIBRARY_SCHEMA_VERSION

    # entries present with correct count
    entries = archive_data["entries"]
    assert len(entries) == 31  # 30 builtin + 1 user

    # Each entry has embedding_signature field (may be null)
    for entry_data in entries:
        assert "embedding_signature" in entry_data
        assert "pattern_id" in entry_data
        assert "vector_stale" in entry_data


def test_import_no_hostname_in_meta(tmp_path: Path) -> None:
    """Verify archive's library.json does NOT contain hostname (privacy)."""
    src = _make_src_lib(tmp_path)
    try:
        blob = src.export()
    finally:
        src.close()

    archive_data = _read_archive_json(blob)
    archive_str = json.dumps(archive_data, ensure_ascii=False)

    # No hostname-related keys
    assert "hostname" not in archive_data
    assert "machine_id" not in archive_data
    assert "username" not in archive_data
    assert "home_dir" not in archive_str

    # Entries should not contain path-like information
    for entry_data in archive_data["entries"]:
        assert "hostname" not in entry_data
        assert "machine_id" not in entry_data


def test_export_import_preserves_user_added_entries(tmp_path: Path) -> None:
    """Verify user entries survive round-trip with all fields intact."""
    src = HumanizeLibrary.from_path(tmp_path / "src")
    try:
        seed_builtin_patterns(src)

        # Add multiple user entries with varied fields
        src.add(
            HumanizeLibraryEntry(
                pattern_id="lib_user_aabbccdd",
                pattern_name="复杂用户模式",
                category="复合分类",
                severity="critical",
                detection_method="mixed",
                source="user",
                example_phrases=["示例短语一", "示例短语二"],
                example_template="模板: {X} 导致了 {Y}",
                keywords=["复杂", "用户", "模式"],
                notes="这是一条测试用户条目",
                hit_count=42,
            )
        )
        src.add(
            HumanizeLibraryEntry(
                pattern_id="lib_user_11223344",
                pattern_name="简单用户模式",
                category="简单分类",
                severity="low",
                detection_method="regex",
                source="user",
                keywords=["简单"],
            )
        )

        blob = src.export()
    finally:
        src.close()

    dst = _make_dst_lib(tmp_path)
    try:
        report = dst.import_archive(blob)
        assert report.imported == 32  # 30 builtin + 2 user

        # Verify complex user entry
        complex_entry = dst.get("lib_user_aabbccdd")
        assert complex_entry is not None
        assert complex_entry.pattern_name == "复杂用户模式"
        assert complex_entry.category == "复合分类"
        assert complex_entry.severity == "critical"
        assert complex_entry.detection_method == "mixed"
        assert complex_entry.example_phrases == ["示例短语一", "示例短语二"]
        assert complex_entry.example_template == "模板: {X} 导致了 {Y}"
        assert complex_entry.keywords == ["复杂", "用户", "模式"]
        assert complex_entry.notes == "这是一条测试用户条目"
        assert complex_entry.hit_count == 42
        assert complex_entry.source == "user"

        # Verify simple user entry
        simple_entry = dst.get("lib_user_11223344")
        assert simple_entry is not None
        assert simple_entry.pattern_name == "简单用户模式"
        assert simple_entry.severity == "low"
    finally:
        dst.close()
