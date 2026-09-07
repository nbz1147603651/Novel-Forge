"""Tests for migrate_zvec_collections.py (Wave 3 / Task 3.1).

These tests verify the **safety contract** of the 4-collection migration tool:
    1. ``--dry-run`` does NOT modify any file on disk (byte-level identical)
    2. ``--backup`` creates ``.bak/<timestamp>/<old_dir_name>/`` for EACH old dir
    3. ``--cleanup`` is REFUSED without a backup (default safety)
    4. ``--cleanup`` is REFUSED without explicit ``--cleanup`` flag
    5. ``--fallback-double-read`` writes a JSON marker with 2 mappings, NOT product code
    6. Default behavior (no flags) is read-only
    7. episodic ``zvec_vectors/`` and Task 1.4 ``zvec_expression_vectors/`` are
       NEVER touched (protected)

All tests use :func:`tmp_path` (pytest built-in) to simulate a project
directory; no real project is touched.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from novel_forge.memory.migrate_zvec_collections import (
    BACKUP_DIR_SUFFIX,
    COLLECTION_PAIRS,
    DOUBLE_READ_MARKER_NAME,
    EPISODIC_DIR_NAME,
    PROTECTED_OLD_DIRS,
    build_specs,
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
    file_count_per_dir: int = 4,
    include_episodic: bool = True,
    include_expression: bool = True,
) -> Path:
    """Create a fake project layout with all 4 zvec collections.

    Returns the project_dir.
    """
    project_dir = tmp_path / project_id
    memory_dir = project_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    for old_name, _ in COLLECTION_PAIRS:
        old_dir = memory_dir / old_name
        old_dir.mkdir(parents=True, exist_ok=True)
        for i in range(file_count_per_dir):
            f = old_dir / f"entry_{i:03d}.bin"
            f.write_bytes(b"\x00\x01\x02\x03" * (i + 1))
        sub = old_dir / "0"
        sub.mkdir(exist_ok=True)
        (sub / "vector_blob.bin").write_bytes(b"\xAB" * 64)
        (old_dir / "LOCK").write_bytes(b"")
        (old_dir / "manifest.1").write_text("fake manifest", encoding="utf-8")

    if include_episodic:
        epi = memory_dir / EPISODIC_DIR_NAME
        epi.mkdir(parents=True, exist_ok=True)
        (epi / "episodic_0.bin").write_bytes(b"\xCD" * 32)

    if include_expression:
        expr = memory_dir / "zvec_expression_vectors"
        expr.mkdir(parents=True, exist_ok=True)
        (expr / "expression_0.bin").write_bytes(b"\xEF" * 32)

    return project_dir


def _dir_fingerprint(path: Path) -> dict[str, str]:
    """Compute a recursive SHA-256 fingerprint of a directory's files.

    Keys are relative POSIX paths, values are sha256 hex digests.
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
# Tests — required by spec (3 minimum)
# ---------------------------------------------------------------------------


def test_dry_run_does_not_modify_files(tmp_path: Path) -> None:
    """``--dry-run`` 跑完后, 两个旧目录文件字节级完全不变; 默认行为 (无 flag) 同."""
    _seed_fake_project(tmp_path, file_count_per_dir=4)
    _, _, memory_dir, old_dirs, _ = resolve_project_paths(
        project_id="fake_proj", storage_root=tmp_path
    )

    # 拍 4 个 collection 目录的快照 (2 old + episodic + expression)
    protected_paths = [
        memory_dir / EPISODIC_DIR_NAME,
        memory_dir / "zvec_expression_vectors",
    ]
    before = {p: _dir_fingerprint(p) for p in (*old_dirs, *protected_paths)}
    assert all(len(v) > 0 for v in before.values()), "fixture failed: empty fingerprints"

    # 跑 dry-run
    exit_code = main(
        [
            "--project-id", "fake_proj",
            "--storage-root", str(tmp_path),
            "--dry-run",
            "--quiet",
        ]
    )
    assert exit_code == 0

    after = {p: _dir_fingerprint(p) for p in (*old_dirs, *protected_paths)}
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
    after_default = {p: _dir_fingerprint(p) for p in (*old_dirs, *protected_paths)}
    assert before == after_default, "default run modified files"


def test_backup_creates_bak_directory(tmp_path: Path) -> None:
    """``--backup`` 跑完后, ``.bak/<timestamp>/<old_dir>/`` 对每个 collection 都存在."""
    project_dir = _seed_fake_project(tmp_path, file_count_per_dir=3)
    _, _, memory_dir, old_dirs, _ = resolve_project_paths(
        project_id="fake_proj", storage_root=tmp_path
    )

    # 拍源 fingerprint
    source_fingerprints = {p: _dir_fingerprint(p) for p in old_dirs}

    exit_code = main(
        [
            "--project-id", "fake_proj",
            "--storage-root", str(tmp_path),
            "--backup",
            "--quiet",
        ]
    )
    assert exit_code == 0

    backup_root = memory_dir / BACKUP_DIR_SUFFIX
    assert backup_root.exists(), f"backup root missing: {backup_root}"

    # 必须恰好 1 个时间戳子目录 (per-run 共享一个 timestamp)
    ts_subdirs = [p for p in backup_root.iterdir() if p.is_dir()]
    assert len(ts_subdirs) == 1, f"expected exactly 1 timestamp dir, got {len(ts_subdirs)}"
    ts_dir = ts_subdirs[0]

    # 在该时间戳子目录下, 每个 old_dir_name 都要有备份
    backed_up = {p.name for p in ts_dir.iterdir() if p.is_dir()}
    expected = {old.name for old in old_dirs}
    assert backed_up == expected, (
        f"backup coverage mismatch: got {backed_up}, expected {expected}"
    )

    # 每个备份的 fingerprint 必须等于源
    for src, bname in zip(old_dirs, [p.name for p in old_dirs], strict=True):
        backup_path = ts_dir / bname
        assert _dir_fingerprint(backup_path) == source_fingerprints[src], (
            f"backup content differs from source for {bname}"
        )

    # 源应当原封不动
    assert all(_dir_fingerprint(p) == source_fingerprints[p] for p in old_dirs)

    # 项目目录被工具间接引用 (lint)
    _ = project_dir


def test_cleanup_only_with_explicit_flag(tmp_path: Path) -> None:
    """不传 ``--cleanup`` 时, 旧目录始终在; 即便传了但无 backup 也会被拒绝.

    每个子检查用独立子目录隔离, 避免 backup 创建的 .bak 影响后续 cleanup 判定.
    """
    # ---- Case 1: dry-run 不会删除 ----
    _seed_fake_project(tmp_path, "case1_dryrun", file_count_per_dir=2)
    _, _, _, old1, _ = resolve_project_paths(
        project_id="case1_dryrun", storage_root=tmp_path
    )
    main([
        "--project-id", "case1_dryrun",
        "--storage-root", str(tmp_path),
        "--dry-run", "--quiet",
    ])
    assert all(o.exists() for o in old1), "dry-run should not remove old dirs"

    # ---- Case 2: backup 不会删除 ----
    _seed_fake_project(tmp_path, "case2_backup", file_count_per_dir=2)
    _, _, _, old2, _ = resolve_project_paths(
        project_id="case2_backup", storage_root=tmp_path
    )
    main([
        "--project-id", "case2_backup",
        "--storage-root", str(tmp_path),
        "--backup", "--quiet",
    ])
    assert all(o.exists() for o in old2), "backup should not remove old dirs"

    # ---- Case 3: cleanup 在无 backup 的全新 fixture 上, 应当被拒绝 ----
    _seed_fake_project(tmp_path, "case3_no_backup", file_count_per_dir=2)
    _, project_dir3, memory_dir3, old3, _ = resolve_project_paths(
        project_id="case3_no_backup", storage_root=tmp_path
    )
    _ = project_dir3
    # 显式确认 .bak 不存在
    backup_root3 = memory_dir3 / BACKUP_DIR_SUFFIX
    assert not backup_root3.exists()

    report = run_migration(
        project_id="case3_no_backup",
        storage_root=tmp_path,
        cleanup=True,
    )
    assert all(o.exists() for o in old3), "cleanup should be REFUSED without backup"
    # 至少一个 collection 的 cleanup 应当被拒绝
    assert any(pc.cleanup_refused_reason for pc in report.collections), (
        "expected at least one cleanup_refused_reason when no .bak exists"
    )


# ---------------------------------------------------------------------------
# Tests — extra edge cases (Task 1.4 parity)
# ---------------------------------------------------------------------------


def test_cleanup_with_backup_and_force_removes_old_dirs(tmp_path: Path) -> None:
    """``--cleanup --force`` 在 backup 存在时应当真的删除 2 个旧目录 (happy path)."""
    _seed_fake_project(tmp_path, file_count_per_dir=2)
    _, _, memory_dir, old_dirs, _ = resolve_project_paths(
        project_id="fake_proj", storage_root=tmp_path
    )

    # 先备份
    main(
        [
            "--project-id", "fake_proj",
            "--storage-root", str(tmp_path),
            "--backup",
            "--quiet",
        ]
    )
    assert all(o.exists() for o in old_dirs)
    backup_root = memory_dir / BACKUP_DIR_SUFFIX
    assert backup_root.exists() and any(backup_root.iterdir())

    # cleanup + force 应当成功
    report = run_migration(
        project_id="fake_proj",
        storage_root=tmp_path,
        cleanup=True,
        cleanup_force=True,
    )
    assert all(pc.cleanup_refused_reason == "" for pc in report.collections)
    assert all(
        any("cleanup" in a for a in pc.actions_taken)
        for pc in report.collections
    )
    assert all(not o.exists() for o in old_dirs), "cleanup should have removed old dirs"

    # 备份还在
    assert backup_root.exists()
    assert any(backup_root.iterdir())


def test_fallback_double_read_writes_marker_with_two_mappings(tmp_path: Path) -> None:
    """``--fallback-double-read`` 只在 project 根目录写一个 JSON 标记, 含 2 条映射."""
    project_dir = _seed_fake_project(tmp_path, file_count_per_dir=1)
    _, _, _, old_dirs, new_subdirs = resolve_project_paths(
        project_id="fake_proj", storage_root=tmp_path
    )

    old_fps = {p: _dir_fingerprint(p) for p in old_dirs}
    new_fps = {p: _dir_fingerprint(p) for p in new_subdirs}

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
    assert "zvec_4collection_migration" in payload["reason"]
    assert "mappings" in payload
    assert len(payload["mappings"]) == len(COLLECTION_PAIRS), (
        f"expected {len(COLLECTION_PAIRS)} mappings, got {len(payload['mappings'])}"
    )

    # 验证每条映射都正确
    mapping_pairs = {
        (m["old_dir_name"], m["new_subdir_name"])
        for m in payload["mappings"]
    }
    expected_pairs = set(COLLECTION_PAIRS)
    assert mapping_pairs == expected_pairs, (
        f"mapping mismatch: got {mapping_pairs}, expected {expected_pairs}"
    )

    # 旧/新目录应当原封不动
    for p in old_dirs:
        assert _dir_fingerprint(p) == old_fps[p]
    for p in new_subdirs:
        assert _dir_fingerprint(p) == new_fps[p]


def test_report_structure_fields_present(tmp_path: Path) -> None:
    """``MigrationReport`` 必填字段都存在且类型正确."""
    _seed_fake_project(tmp_path, file_count_per_dir=2)
    report = run_migration(
        project_id="fake_proj",
        storage_root=tmp_path,
        dry_run=True,
    )

    assert report.project_id == "fake_proj"
    assert len(report.collections) == len(COLLECTION_PAIRS)
    for pc in report.collections:
        assert pc.old_file_count >= 2
        assert pc.old_dir_exists is True
        assert isinstance(pc.actions_taken, list)
        assert isinstance(pc.actions_skipped, list)

    # dry-run 总是 skip 所有写操作 (per-collection 都应有 cleanup skip)
    assert all("cleanup" in " ".join(pc.actions_skipped) for pc in report.collections)

    # summary_lines 至少 10 行
    assert len(report.summary_lines) >= 10
    assert any("DRY-RUN" in line for line in report.summary_lines)

    # to_dict() 可序列化
    d = report.to_dict()
    json.dumps(d, ensure_ascii=False)


def test_missing_old_dirs_are_handled(tmp_path: Path) -> None:
    """旧目录都不存在时, 工具应优雅处理, 不抛异常."""
    project_dir = tmp_path / "empty_proj"
    (project_dir / "memory").mkdir(parents=True)

    report = run_migration(
        project_id="empty_proj",
        storage_root=tmp_path,
        dry_run=True,
    )
    assert len(report.collections) == len(COLLECTION_PAIRS)
    for pc in report.collections:
        assert pc.old_dir_exists is False
        assert pc.old_file_count == 0
        assert pc.old_total_bytes == 0


def test_episodic_and_expression_dirs_are_never_touched(tmp_path: Path) -> None:
    """episodic ``zvec_vectors/`` 和 expression ``zvec_expression_vectors/`` 永远不动.

    即便 ``--backup --cleanup --fallback-double-read`` 三个 flag 一起传.
    """
    _seed_fake_project(tmp_path, file_count_per_dir=2)
    _, _, memory_dir, old_dirs, _ = resolve_project_paths(
        project_id="fake_proj", storage_root=tmp_path
    )

    protected_paths = [
        memory_dir / EPISODIC_DIR_NAME,
        memory_dir / "zvec_expression_vectors",
    ]
    protected_fp_before = {p: _dir_fingerprint(p) for p in protected_paths}

    main(
        [
            "--project-id", "fake_proj",
            "--storage-root", str(tmp_path),
            "--backup",
            "--fallback-double-read",
            "--cleanup",
            "--force",
            "--quiet",
        ]
    )

    # episodic + expression 应当原封不动
    for p in protected_paths:
        assert _dir_fingerprint(p) == protected_fp_before[p], (
            f"protected dir {p} was modified"
        )
        assert p.exists(), f"protected dir {p} was removed"


def test_cli_exit_code_refused_cleanup(tmp_path: Path) -> None:
    """``--cleanup`` 无 backup 时, CLI 应返回 exit code 2."""
    _seed_fake_project(tmp_path, file_count_per_dir=1)
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


def test_protected_old_dirs_cannot_be_added_to_pairs() -> None:
    """防御性检查: COLLECTION_PAIRS 不应包含 PROTECTED_OLD_DIRS (episodic / expression).

    这是 build_specs() 的前置条件. 如果未来有人把 ``zvec_vectors`` 加进
    COLLECTION_PAIRS, run_migration() 会抛 ValueError, 防止误删 episodic.
    """
    specs = build_specs()
    spec_names = {s.old_dir_name for s in specs}
    assert spec_names.isdisjoint(PROTECTED_OLD_DIRS), (
        f"COLLECTION_PAIRS overlaps PROTECTED_OLD_DIRS: "
        f"{spec_names & PROTECTED_OLD_DIRS}"
    )


def test_storage_root_fallback_chain(monkeypatch: object, tmp_path: Path) -> None:
    """``--storage-root`` > env ``$NOVEL_FORGE_STORAGE_ROOT`` > ``./data``."""
    # 1) 显式参数优先
    _seed_fake_project(tmp_path, "explicit_wins", file_count_per_dir=1)
    _, project_dir_explicit, _, old_explicit, _ = resolve_project_paths(
        project_id="explicit_wins", storage_root=tmp_path
    )
    assert project_dir_explicit.parent == tmp_path.resolve()
    assert all(
        o.parent.parent.parent == tmp_path.resolve() for o in old_explicit
    )

    # 2) env var (在 fixture 之外)
    import tempfile
    with tempfile.TemporaryDirectory() as env_root:
        monkeypatch_ = getattr(monkeypatch, "setenv", None)
        assert monkeypatch_ is not None
        monkeypatch_("NOVEL_FORGE_STORAGE_ROOT", env_root)
        _seed_fake_project(Path(env_root), "env_wins", file_count_per_dir=1)
        _, project_dir_env, _, old_env, _ = resolve_project_paths(
            project_id="env_wins"
        )
        assert project_dir_env.parent == Path(env_root).resolve()
        assert all(
            o.parent.parent.parent == Path(env_root).resolve() for o in old_env
        )


def test_backup_subdir_uses_old_dir_name(tmp_path: Path) -> None:
    """``--backup`` 产生的备份子目录名必须等于 ``old_dir_name`` (便于恢复)."""
    _seed_fake_project(tmp_path, file_count_per_dir=1)
    _, _, memory_dir, old_dirs, _ = resolve_project_paths(
        project_id="fake_proj", storage_root=tmp_path
    )

    report = run_migration(
        project_id="fake_proj",
        storage_root=tmp_path,
        backup=True,
    )
    # 每个 per-collection 报告的 backup_path 末尾都应是 old_dir_name
    for pc, src in zip(report.collections, old_dirs, strict=True):
        assert pc.backup_path.endswith(src.name), (
            f"backup path {pc.backup_path} does not end with old dir name {src.name}"
        )
        # 备份路径确实存在
        assert Path(pc.backup_path).exists()
    _ = memory_dir  # lint
