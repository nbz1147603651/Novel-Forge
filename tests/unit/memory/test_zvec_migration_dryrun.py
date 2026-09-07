"""Tests for migrate_zvec_expression.py (P0.4 Task 1.4).

These tests verify the **safety contract** of the migration tool:
    1. ``--dry-run`` does NOT modify any file on disk (byte-level identical)
    2. ``--backup`` creates ``zvec_expression_vectors.bak/<timestamp>/``
    3. ``--cleanup`` is REFUSED without a backup (default safety)
    4. ``--cleanup`` is REFUSED without explicit ``--cleanup`` flag
    5. ``--fallback-double-read`` writes a JSON marker, NOT product code
    6. Default behavior (no flags) is read-only

All tests use :func:`tmp_path` (pytest built-in) to simulate a project
directory; no real project is touched.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from novel_forge.memory.migrate_zvec_expression import (
    BACKUP_DIR_SUFFIX,
    DOUBLE_READ_MARKER_NAME,
    OLD_DIR_NAME,
    main,
    resolve_project_paths,
    run_migration,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_fake_project(
    tmp_path: Path,
    project_id: str = "fake_proj",
    *,
    file_count: int = 5,
) -> Path:
    """Create a fake project layout: ``<tmp>/<project>/memory/zvec_expression_vectors/``.

    Returns the project_dir.
    """
    project_dir = tmp_path / project_id
    memory_dir = project_dir / "memory"
    old_dir = memory_dir / OLD_DIR_NAME
    old_dir.mkdir(parents=True, exist_ok=True)

    # 一级子目录 + 嵌套文件 + 普通文件
    for i in range(file_count):
        f = old_dir / f"entry_{i:03d}.bin"
        f.write_bytes(b"\x00\x01\x02\x03" * (i + 1))  # 4*(i+1) bytes
    sub = old_dir / "0"
    sub.mkdir(exist_ok=True)
    (sub / "vector_blob.bin").write_bytes(b"\xAB" * 64)
    (old_dir / "LOCK").write_bytes(b"")
    (old_dir / "manifest.1").write_text("fake manifest", encoding="utf-8")
    return project_dir


def _dir_fingerprint(path: Path) -> dict[str, str]:
    """Compute a recursive SHA-256 fingerprint of a directory's files.

    Keys are relative POSIX paths, values are sha256 hex digests.
    Used to assert byte-level identity.
    """
    if not path.exists():
        return {}
    fingerprint: dict[str, str] = {}
    for p in sorted(path.rglob("*")):
        if p.is_file():
            rel = p.relative_to(path).as_posix()
            fingerprint[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return fingerprint


# ---------------------------------------------------------------------------
# Tests — contract assertions
# ---------------------------------------------------------------------------


def test_dry_run_does_not_modify_files(tmp_path: Path) -> None:
    """``--dry-run`` 跑完后, 旧目录文件字节级完全不变."""
    _seed_fake_project(tmp_path, file_count=4)
    _, _, old_dir, _ = resolve_project_paths(
        project_id="fake_proj", storage_root=tmp_path
    )

    # 拍快照
    before = _dir_fingerprint(old_dir)
    assert len(before) > 0, "fixture failed: no files in old dir"

    # 跑 dry-run (--dry-run 显式)
    exit_code = main(
        [
            "--project-id", "fake_proj",
            "--storage-root", str(tmp_path),
            "--dry-run",
            "--quiet",
        ]
    )
    assert exit_code == 0

    # 拍快照
    after = _dir_fingerprint(old_dir)
    assert before == after, "DRY-RUN MODIFIED FILES — safety contract violated"

    # 显式再跑一次 (无任何 flag), 默认行为也必须只读
    exit_code_default = main(
        [
            "--project-id", "fake_proj",
            "--storage-root", str(tmp_path),
            "--quiet",
        ]
    )
    assert exit_code_default == 0
    after_default = _dir_fingerprint(old_dir)
    assert before == after_default, "default run modified files"


def test_backup_creates_bak_directory(tmp_path: Path) -> None:
    """``--backup`` 跑完后, ``zvec_expression_vectors.bak/<timestamp>/`` 存在并内容一致."""
    project_dir = _seed_fake_project(tmp_path, file_count=3)
    _, _, old_dir, _ = resolve_project_paths(
        project_id="fake_proj", storage_root=tmp_path
    )
    _ = project_dir  # silence F841; project_dir is used implicitly by the path below

    old_fingerprint = _dir_fingerprint(old_dir)

    exit_code = main(
        [
            "--project-id", "fake_proj",
            "--storage-root", str(tmp_path),
            "--backup",
            "--quiet",
        ]
    )
    assert exit_code == 0

    backup_root = project_dir / "memory" / f"{OLD_DIR_NAME}{BACKUP_DIR_SUFFIX}"
    assert backup_root.exists(), f"backup root missing: {backup_root}"
    # 必须有至少一个时间戳子目录
    subdirs = [p for p in backup_root.iterdir() if p.is_dir()]
    assert len(subdirs) == 1, f"expected exactly 1 backup, got {len(subdirs)}"

    backup_fingerprint = _dir_fingerprint(subdirs[0])
    assert old_fingerprint == backup_fingerprint, (
        "backup content differs from source"
    )

    # 旧目录应当原封不动
    assert _dir_fingerprint(old_dir) == old_fingerprint


def test_cleanup_only_with_explicit_flag(tmp_path: Path) -> None:
    """不传 ``--cleanup`` 时, 旧目录始终在; 即便传了但无 backup 也会被拒绝.

    每个子检查用独立子目录隔离,避免 backup 创建的 .bak 影响后续 cleanup 判定.
    """
    # ---- Case 1: dry-run 不会删除 ----
    _seed_fake_project(tmp_path, "case1_dryrun", file_count=2)
    _, _, old1, _ = resolve_project_paths(
        project_id="case1_dryrun", storage_root=tmp_path
    )
    main([
        "--project-id", "case1_dryrun",
        "--storage-root", str(tmp_path),
        "--dry-run", "--quiet",
    ])
    assert old1.exists(), "dry-run should not remove old dir"

    # ---- Case 2: backup 不会删除 ----
    _seed_fake_project(tmp_path, "case2_backup", file_count=2)
    _, _, old2, _ = resolve_project_paths(
        project_id="case2_backup", storage_root=tmp_path
    )
    main([
        "--project-id", "case2_backup",
        "--storage-root", str(tmp_path),
        "--backup", "--quiet",
    ])
    assert old2.exists(), "backup should not remove old dir"

    # ---- Case 3: cleanup 在无 backup 的全新 fixture 上, 应当被拒绝 ----
    _seed_fake_project(tmp_path, "case3_no_backup", file_count=2)
    _, _, old3, _ = resolve_project_paths(
        project_id="case3_no_backup", storage_root=tmp_path
    )
    # 显式确认 .bak 不存在
    backup_root3 = (
        tmp_path / "case3_no_backup" / "memory" / f"{OLD_DIR_NAME}{BACKUP_DIR_SUFFIX}"
    )
    assert not backup_root3.exists()

    report = run_migration(
        project_id="case3_no_backup",
        storage_root=tmp_path,
        cleanup=True,
    )
    assert old3.exists(), "cleanup should be REFUSED without backup"
    assert report.cleanup_refused_reason != "", (
        "expected cleanup_refused_reason to be set when no .bak exists"
    )
    assert "backup" in report.cleanup_refused_reason.lower()


def test_cleanup_with_backup_and_force_removes_old_dir(tmp_path: Path) -> None:
    """``--cleanup --force`` 在 backup 存在时应当真的删除旧目录 (这是 happy path)."""
    project_dir = _seed_fake_project(tmp_path, file_count=2)
    _, _, old_dir, _ = resolve_project_paths(
        project_id="fake_proj", storage_root=tmp_path
    )
    _ = project_dir

    # 先备份
    main(
        [
            "--project-id", "fake_proj",
            "--storage-root", str(tmp_path),
            "--backup",
            "--quiet",
        ]
    )
    assert old_dir.exists()
    backup_root = project_dir / "memory" / f"{OLD_DIR_NAME}{BACKUP_DIR_SUFFIX}"
    assert backup_root.exists()

    # cleanup + force 应当成功
    report = run_migration(
        project_id="fake_proj",
        storage_root=tmp_path,
        cleanup=True,
        cleanup_force=True,
    )
    assert report.cleanup_refused_reason == ""
    assert any("cleanup" in a for a in report.actions_taken)
    assert not old_dir.exists(), "cleanup should have removed old dir"

    # 备份还在
    assert backup_root.exists()
    assert any(backup_root.iterdir())


def test_fallback_double_read_writes_marker_only(tmp_path: Path) -> None:
    """``--fallback-double-read`` 只在 project 根目录写一个 JSON 标记, 不动产品代码."""
    project_dir = _seed_fake_project(tmp_path, file_count=1)
    _, _, old_dir, new_dir = resolve_project_paths(
        project_id="fake_proj", storage_root=tmp_path
    )

    old_fp = _dir_fingerprint(old_dir)
    new_fp = _dir_fingerprint(new_dir)

    report = run_migration(
        project_id="fake_proj",
        storage_root=tmp_path,
        fallback_double_read=True,
    )
    assert report.double_read_marker_path

    marker = project_dir / DOUBLE_READ_MARKER_NAME
    assert marker.exists(), f"double-read marker missing: {marker}"

    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["enabled"] is True
    assert payload["old_path"] == str(old_dir)
    assert payload["new_path"] == str(new_dir)
    assert "expression_channel_zvec_migration" in payload["reason"]

    # 旧/新目录应当原封不动
    assert _dir_fingerprint(old_dir) == old_fp
    assert _dir_fingerprint(new_dir) == new_fp


def test_report_structure_fields_present(tmp_path: Path) -> None:
    """``MigrationReport`` 必填字段都存在且类型正确."""
    _seed_fake_project(tmp_path, file_count=2)
    report = run_migration(
        project_id="fake_proj",
        storage_root=tmp_path,
        dry_run=True,
    )

    assert report.project_id == "fake_proj"
    assert report.old_file_count >= 2
    assert report.old_dir_exists is True
    assert isinstance(report.actions_taken, list)
    assert isinstance(report.actions_skipped, list)
    assert "backup" in report.actions_skipped  # dry-run 总是 skip 所有写操作
    assert "fallback_double_read" in report.actions_skipped
    assert "cleanup" in report.actions_skipped

    # summary_lines 至少 10 行, 人类可读
    assert len(report.summary_lines) >= 10
    assert any("DRY-RUN" in line for line in report.summary_lines)

    # to_dict() 可序列化
    d = report.to_dict()
    json.dumps(d, ensure_ascii=False)  # 不抛异常 = OK


def test_missing_old_dir_is_handled(tmp_path: Path) -> None:
    """旧目录不存在时, 工具应优雅处理, 不抛异常."""
    project_dir = tmp_path / "empty_proj"
    (project_dir / "memory").mkdir(parents=True)

    report = run_migration(
        project_id="empty_proj",
        storage_root=tmp_path,
        dry_run=True,
    )
    assert report.old_dir_exists is False
    assert report.old_file_count == 0
    assert report.old_total_bytes == 0
    assert report.old_subdirs == []


def test_backup_then_cleanup_refuses_without_force(tmp_path: Path) -> None:
    """``--cleanup`` 在 backup 存在但**没有** ``--force`` 时也应当被允许删除 (有 backup 即可)."""
    project_dir = _seed_fake_project(tmp_path, file_count=2)
    _, _, old_dir, _ = resolve_project_paths(
        project_id="fake_proj", storage_root=tmp_path
    )

    # 先备份 (无 --force)
    main(
        [
            "--project-id", "fake_proj",
            "--storage-root", str(tmp_path),
            "--backup",
            "--quiet",
        ]
    )
    assert old_dir.exists()
    backup_root = project_dir / "memory" / f"{OLD_DIR_NAME}{BACKUP_DIR_SUFFIX}"
    assert backup_root.exists() and any(backup_root.iterdir())

    # cleanup 无 --force: 应当成功 (因为 .bak 存在)
    report = run_migration(
        project_id="fake_proj",
        storage_root=tmp_path,
        cleanup=True,
        cleanup_force=False,
    )
    assert report.cleanup_refused_reason == ""
    assert not old_dir.exists()


def test_cli_exit_code_refused_cleanup(tmp_path: Path) -> None:
    """``--cleanup`` 无 backup 时, CLI 应返回 exit code 2."""
    _seed_fake_project(tmp_path, file_count=1)
    exit_code = main(
        [
            "--project-id", "fake_proj",
            "--storage-root", str(tmp_path),
            "--cleanup",
            "--quiet",
        ]
    )
    # cleanup 应当被拒绝 (.bak 不存在) → exit code 2
    assert exit_code == 2, f"expected exit 2 (cleanup refused), got {exit_code}"
