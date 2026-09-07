"""Tests for migrate_to_chapter_shards.py (Wave 3 Task 11).

These tests verify the **safety contract** of the chapter-shard migration tool:
    1. ``--dry-run`` does NOT modify ``project_memory.json`` (byte-level identical)
    2. ``--backup`` creates ``.bak/<UTC-ISO-timestamp>/project_memory.json``
    3. No ``chapter_NNN.json`` shards are created during ``--dry-run``
    4. (active run) Shards are written + global fields preserved in index
    5. (active run) Missing project_memory.json returns clean error report
    6. ``--fallback-double-read`` writes a JSON marker (NOT product code)
    7. ``main(argv)`` exit code: 0 on success, 2 on missing source

All tests use :func:`tmp_path` (pytest built-in) to simulate a project
directory; no real project is touched.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from novel_forge.memory.migrate_to_chapter_shards import (
    BACKUP_DIR_SUFFIX,
    CHAPTER_SHARD_PREFIX,
    DOUBLE_READ_MARKER_NAME,
    PROJECT_MEMORY_FILENAME,
    chapter_shard_filename,
    discover_chapter_shards,
    load_project_memory,
    main,
    perform_backup,
    remove_double_read_marker,
    resolve_project_paths,
    run_migration,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_fake_project_memory(
    tmp_path: Path,
    project_id: str = "fake_proj",
    *,
    last_indexed_chapter: int = 3,
) -> Path:
    """Create a fake project layout with a realistic ``project_memory.json``.

    Returns the project_dir.

    Layout::

        <tmp>/<project>/memory/project_memory.json
    """
    project_dir = tmp_path / project_id
    memory_dir = project_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    # Realistic shape — fields that exist in production code (integration.py:3501-3510)
    payload = {
        "project_id": project_id,
        "last_indexed_chapter": last_indexed_chapter,
        "summary_cache": {
            1: {"summary": "ch1", "tokens": 100},
            2: {"summary": "ch2", "tokens": 150},
            3: {"summary": "ch3", "tokens": 200},
        },
        "chapter_content_hash": {
            1: "hash_ch1_abcdef",
            2: "hash_ch2_ghijkl",
            3: "hash_ch3_mnopqr",
        },
        "summary_stats": {"total_summaries": 3, "avg_tokens": 150},
        # episodic_index is a dict with sub-structure (see integration.py)
        "episodic_index": {
            "vector_store": "fake_vector_store_handle",
            "outline_data": {"outline_stub": True},
            "critique_index": {"critique_meta": True},
            "chapter_events": {
                1: ["event_a", "event_b"],
                2: ["event_c"],
                3: ["event_d", "event_e"],
            },
            "chapter_critiques": {
                1: {"score": 0.8, "issues": []},
                2: {"score": 0.7, "issues": ["minor"]},
            },
        },
        # motif data (still in project_memory.json in the seed — Task 1.3
        # will move these to motif_state.json; this script does NOT touch them)
        "motif_cache": {1: [{"name": "revenge", "weight": 0.7}]},
        "motif_tracker": {1: ["motif_log_1"]},
    }
    project_memory_path = memory_dir / PROJECT_MEMORY_FILENAME
    project_memory_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return project_dir


def _file_fingerprint(path: Path) -> str:
    """SHA-256 hex digest of a file's bytes. Empty string for missing files."""
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _shard_files(memory_dir: Path) -> list[Path]:
    """List all ``chapter_NNN.json`` files in memory_dir (sorted)."""
    if not memory_dir.exists():
        return []
    return sorted(memory_dir.glob(f"{CHAPTER_SHARD_PREFIX}*.json"))


# ---------------------------------------------------------------------------
# Tests — contract assertions (the 3 required by plan)
# ---------------------------------------------------------------------------


def test_dry_run_does_not_modify_project_memory(tmp_path: Path) -> None:
    """``--dry-run`` must keep ``project_memory.json`` byte-identical."""
    project_dir = _seed_fake_project_memory(tmp_path)
    memory_dir = project_dir / "memory"
    project_memory_path = memory_dir / PROJECT_MEMORY_FILENAME

    before_hash = _file_fingerprint(project_memory_path)

    report = run_migration(
        project_id=project_dir.name,
        storage_root=tmp_path,
        dry_run=True,
    )

    after_hash = _file_fingerprint(project_memory_path)

    assert before_hash != "", "seed file should exist before run"
    assert before_hash == after_hash, (
        "dry-run must NOT modify project_memory.json "
        f"(before={before_hash[:12]}, after={after_hash[:12]})"
    )
    assert report.exists is True
    assert report.error_message == ""
    # 报告里应包含扫描结果 (但不写盘)
    assert report.chapter_count == 3, (
        f"expected 3 chapters in seed, got {report.chapter_count}"
    )
    assert report.chapter_numbers == [1, 2, 3]


def test_backup_creates_bak_file(tmp_path: Path) -> None:
    """``--backup`` must create ``.bak/<UTC-ISO-timestamp>/project_memory.json``."""
    project_dir = _seed_fake_project_memory(tmp_path)
    memory_dir = project_dir / "memory"
    project_memory_path = memory_dir / PROJECT_MEMORY_FILENAME

    before_hash = _file_fingerprint(project_memory_path)

    report = run_migration(
        project_id=project_dir.name,
        storage_root=tmp_path,
        backup=True,
    )

    # 1) Report 记录了 backup 路径
    assert report.backup_path != "", "report should record backup path"
    backup_target = Path(report.backup_path)
    assert backup_target.exists()
    assert backup_target.is_dir()
    # 2) .bak/ 目录命名规范: project_memory.json.bak/<timestamp>/
    assert backup_target.parent.name == (
        f"{PROJECT_MEMORY_FILENAME}{BACKUP_DIR_SUFFIX}"
    )
    # 3) 备份文件存在且字节级等于源文件
    backed_up_file = backup_target / PROJECT_MEMORY_FILENAME
    assert backed_up_file.exists()
    assert _file_fingerprint(backed_up_file) == before_hash

    # 4) source file 仍然存在 (备份不删除原文件)
    assert project_memory_path.exists()
    # 5) actions_taken 记录
    assert any("backup" in a for a in report.actions_taken)


def test_shard_files_not_created_in_dry_run(tmp_path: Path) -> None:
    """``--dry-run`` must not create any ``chapter_NNN.json`` files."""
    project_dir = _seed_fake_project_memory(tmp_path)
    memory_dir = project_dir / "memory"

    # 启动前确认: 没有 chapter_NNN.json
    assert _shard_files(memory_dir) == []

    report = run_migration(
        project_id=project_dir.name,
        storage_root=tmp_path,
        dry_run=True,
    )

    # dry-run 完成后: 仍然没有 chapter_NNN.json
    shards = _shard_files(memory_dir)
    assert shards == [], f"dry-run created shard files: {[s.name for s in shards]}"

    # 报告说明"将创建 N 个分片" (注意: 计划要求的 log 信息)
    # 即使在 summary_lines 中也应该提到 chapter count
    assert report.chapter_count == 3
    assert "shard_write" in report.actions_skipped, (
        "dry-run should list shard_write as skipped; "
        f"actions_skipped={report.actions_skipped}"
    )
    assert "index_write" in report.actions_skipped
    assert "backup" in report.actions_skipped


# ---------------------------------------------------------------------------
# Tests — active-run assertions (extras, beyond the 3 required)
# ---------------------------------------------------------------------------


def test_active_run_writes_chapter_shards(tmp_path: Path) -> None:
    """Active run (no dry-run, no backup) must write all ``chapter_NNN.json`` files."""
    project_dir = _seed_fake_project_memory(tmp_path)
    memory_dir = project_dir / "memory"

    report = run_migration(
        project_id=project_dir.name,
        storage_root=tmp_path,
        dry_run=False,
    )

    # 1) 3 个分片文件被创建
    shards = _shard_files(memory_dir)
    assert len(shards) == 3, f"expected 3 shards, got {len(shards)}"
    expected_names = {
        "chapter_001.json",
        "chapter_002.json",
        "chapter_003.json",
    }
    actual_names = {p.name for p in shards}
    assert actual_names == expected_names, (
        f"unexpected shard names: {actual_names}"
    )

    # 2) 每个分片内容是该章数据
    ch1_payload = json.loads((memory_dir / "chapter_001.json").read_text())
    assert ch1_payload["summary_cache"] == {"summary": "ch1", "tokens": 100}
    assert ch1_payload["chapter_content_hash"] == "hash_ch1_abcdef"
    assert "episodic_chapter_events" in ch1_payload
    assert "1" in ch1_payload["episodic_chapter_events"]
    assert ch1_payload["episodic_chapter_events"]["1"] == ["event_a", "event_b"]

    # 3) actions_taken 记录
    assert any("shard_write" in a for a in report.actions_taken)
    assert any("index_write" in a for a in report.actions_taken)


def test_active_run_keeps_global_fields_in_index(tmp_path: Path) -> None:
    """Active run must preserve global fields (last_indexed_chapter, etc.) in
    the rewritten ``project_memory.json``, while replacing shardable fields
    with index placeholders."""
    project_dir = _seed_fake_project_memory(tmp_path)
    memory_dir = project_dir / "memory"
    project_memory_path = memory_dir / PROJECT_MEMORY_FILENAME

    run_migration(
        project_id=project_dir.name,
        storage_root=tmp_path,
        dry_run=False,
    )

    new_payload = json.loads(project_memory_path.read_text())

    # 1) 全局字段保留
    assert new_payload["project_id"] == project_dir.name
    assert new_payload["last_indexed_chapter"] == 3
    assert new_payload["summary_stats"] == {"total_summaries": 3, "avg_tokens": 150}

    # 2) motif 字段保持原样 (本脚本不动 motif)
    # JSON round-trip 把 int key 变 str key — 只对比 values 避免 key 类型耦合
    assert list(new_payload["motif_cache"].values()) == [
        [{"name": "revenge", "weight": 0.7}]
    ]
    assert list(new_payload["motif_tracker"].values()) == [["motif_log_1"]]

    # 3) shardable 字段被替换为 None 占位 (索引)
    assert new_payload["summary_cache"] == {"1": None, "2": None, "3": None}
    assert new_payload["chapter_content_hash"] == {
        "1": None, "2": None, "3": None
    }

    # 4) episodic_index 内部: 全局子字段保留, 章节子字段被索引化
    epi = new_payload["episodic_index"]
    assert epi["vector_store"] == "fake_vector_store_handle"
    assert epi["outline_data"] == {"outline_stub": True}
    assert epi["critique_index"] == {"critique_meta": True}
    assert epi["chapter_events"] == {"1": None, "2": None, "3": None}
    assert epi["chapter_critiques"] == {"1": None, "2": None}

    # 5) 新增 shard_index 元数据
    si = new_payload["shard_index"]
    assert si["shard_format"] == "chapter_NNN.json"
    assert si["chapter_numbers"] == [1, 2, 3]
    assert si["field_map"]["summary_cache"] == [1, 2, 3]
    assert si["field_map"]["chapter_content_hash"] == [1, 2, 3]
    assert si["field_map"]["episodic_chapter_events"] == [1, 2, 3]
    assert si["field_map"]["episodic_chapter_critiques"] == [1, 2]
    assert si["config_format"] in ("sharded",)  # 默认 (env 未设置)
    assert "migrated_at" in si


def test_missing_project_memory_returns_clean_error(tmp_path: Path) -> None:
    """When ``project_memory.json`` does not exist, report error_message and skip."""
    project_dir = tmp_path / "no_such_project"
    (project_dir / "memory").mkdir(parents=True)
    # 故意不写 project_memory.json

    report = run_migration(
        project_id=project_dir.name,
        storage_root=tmp_path,
        dry_run=False,
    )

    assert report.exists is False
    assert report.total_bytes == 0
    assert report.error_message != ""
    assert "not found" in report.error_message
    assert report.chapter_count == 0
    # 任何 shard 文件都不该出现
    assert _shard_files(project_dir / "memory") == []


def test_fallback_double_read_writes_marker(tmp_path: Path) -> None:
    """``--fallback-double-read`` writes ``.chapter_shards_double_read.json``."""
    project_dir = _seed_fake_project_memory(tmp_path)
    marker = project_dir / DOUBLE_READ_MARKER_NAME
    assert not marker.exists()

    report = run_migration(
        project_id=project_dir.name,
        storage_root=tmp_path,
        backup=False,
        fallback_double_read=True,
    )

    assert marker.exists(), "marker file should be created"
    payload = json.loads(marker.read_text())
    assert payload["enabled"] is True
    assert payload["reason"] == "chapter_shards_migration"
    assert payload["chapter_numbers"] == [1, 2, 3]
    assert "instruction" in payload

    # 报告里记录 marker 路径
    assert report.double_read_marker_path == str(marker)
    assert any("fallback_double_read" in a for a in report.actions_taken)

    # 清理 (可重复执行)
    removed = remove_double_read_marker(project_dir)
    assert removed is True
    assert not marker.exists()


def test_main_returns_exit_code_2_on_missing_source(tmp_path: Path) -> None:
    """``main(['--project-id', 'X', '--storage-root', '...'])`` returns 2 when source missing."""
    project_dir = tmp_path / "missing_proj"
    (project_dir / "memory").mkdir(parents=True)

    exit_code = main(
        [
            "--project-id", project_dir.name,
            "--storage-root", str(tmp_path),
            "--quiet",
        ]
    )
    assert exit_code == 2


def test_main_returns_exit_code_0_on_dry_run(tmp_path: Path) -> None:
    """``main`` returns 0 after a successful dry-run."""
    _seed_fake_project_memory(tmp_path)

    exit_code = main(
        [
            "--project-id", "fake_proj",
            "--storage-root", str(tmp_path),
            "--dry-run",
            "--quiet",
        ]
    )
    assert exit_code == 0


# ---------------------------------------------------------------------------
# Tests — internal helpers (extras)
# ---------------------------------------------------------------------------


def test_chapter_shard_filename_format() -> None:
    """``chapter_shard_filename`` uses 3-digit zero-padded naming."""
    assert chapter_shard_filename(1) == "chapter_001.json"
    assert chapter_shard_filename(7) == "chapter_007.json"
    assert chapter_shard_filename(42) == "chapter_042.json"
    assert chapter_shard_filename(123) == "chapter_123.json"


def test_resolve_project_paths_falls_back_to_env(monkeypatch, tmp_path: Path) -> None:
    """``resolve_project_paths`` honors ``$NOVEL_FORGE_STORAGE_ROOT`` when no arg given."""
    monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(tmp_path))
    storage_root_abs, project_dir, project_memory_path = resolve_project_paths("p1")
    assert storage_root_abs == tmp_path.resolve()
    assert project_dir == tmp_path.resolve() / "p1"
    assert project_memory_path == tmp_path.resolve() / "p1" / "memory" / PROJECT_MEMORY_FILENAME


def test_perform_backup_round_trip(tmp_path: Path) -> None:
    """``perform_backup`` copies ``project_memory.json`` byte-identical to a timestamped dir."""
    project_dir = _seed_fake_project_memory(tmp_path)
    project_memory_path = project_dir / "memory" / PROJECT_MEMORY_FILENAME
    before_hash = _file_fingerprint(project_memory_path)

    backup_path = perform_backup(project_memory_path)
    backed_up = backup_path / PROJECT_MEMORY_FILENAME

    assert backed_up.exists()
    assert _file_fingerprint(backed_up) == before_hash
    assert backup_path.name.startswith("20")  # UTC ISO date prefix


def test_discover_chapter_shards_handles_string_keys(tmp_path: Path) -> None:
    """``discover_chapter_shards`` normalizes str chapter keys to int."""
    raw = {
        "summary_cache": {"1": "x", "2": "y", "junk": "skip"},
        "chapter_content_hash": {1: "h1", "2": "h2"},
        "episodic_index": {
            "chapter_events": {"1": ["e1"], 2: ["e2"]},
        },
    }
    chapter_numbers, chapter_payloads, field_index = discover_chapter_shards(raw)
    assert chapter_numbers == [1, 2]
    # "junk" 被丢弃, 剩余两章 (1, 2) 进入 payloads
    assert set(chapter_payloads.keys()) == {1, 2}
    assert chapter_payloads[1]["summary_cache"] == "x"
    assert chapter_payloads[2]["summary_cache"] == "y"
    assert field_index["summary_cache"] == [1, 2]
    assert field_index["chapter_content_hash"] == [1, 2]
    assert field_index["episodic_chapter_events"] == [1, 2]


def test_load_project_memory_returns_empty_for_missing(tmp_path: Path) -> None:
    """``load_project_memory`` returns empty dict when file does not exist."""
    result = load_project_memory(tmp_path / "nope.json")
    assert result == {}


def test_run_migration_is_idempotent_when_no_chapter_data(tmp_path: Path) -> None:
    """When ``project_memory.json`` has no chapter-shardable data, no shards
    are written and project_memory.json is preserved as-is."""
    project_dir = tmp_path / "p"
    memory_dir = project_dir / "memory"
    memory_dir.mkdir(parents=True)
    project_memory_path = memory_dir / PROJECT_MEMORY_FILENAME
    project_memory_path.write_text(
        json.dumps(
            {"project_id": "p", "last_indexed_chapter": 0, "summary_stats": {}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    before_hash = _file_fingerprint(project_memory_path)

    report = run_migration(
        project_id="p",
        storage_root=tmp_path,
        dry_run=False,
    )

    assert report.chapter_count == 0
    assert _shard_files(memory_dir) == []
    # 没有 chapter-shardable 数据时, 不重写 project_memory.json
    assert _file_fingerprint(project_memory_path) == before_hash
    # 应当被记录为 skipped
    assert any("shard_write" in a for a in report.actions_skipped)
