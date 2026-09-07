"""受控 4-collection Zvec 重组迁移工具 (Wave 3 / Task 3.1 骨架).

重要设计原则 (源自 plan memory-architecture-v3 Task 10):
- **绝不**自动删除旧目录 — 仅当显式 ``--cleanup`` 才允许删除
- **绝不**实现真实 zvec 数据合并 — 把 2 个独立 collection
  (``zvec_init_coherence_vectors`` + ``zvec_outline_vectors``) 合并到
  ``zvec_vectors/{init_coherence,outline}/`` 的**实际写盘**留作后续任务.
  本工具只做 "dry-run + backup + structure check" 骨架.
- **绝不**修改 ``integration.py`` / ``zvec_store.py`` / ``init_coherence_v2.py``
  / ``init_outline_batch.py`` / ``create_vector_store`` 接口.
  ``--fallback-double-read`` 通过写一个 JSON 标记文件来表达意图,不修改产品代码.
- 默认 (无任何 flag) 行为: 只读 + 报告,不动一个字节.

CLI 用法::

    # 仅报告,不动文件 (默认)
    python -m novel_forge.memory.migrate_zvec_collections --project-id 弈局谋心

    # 显式 dry-run
    python -m novel_forge.memory.migrate_zvec_collections \\
        --project-id 弈局谋心 --dry-run

    # 备份到 .bak/<timestamp>/<old_dir>/ (每个 old_dir 单独一个子目录)
    python -m novel_forge.memory.migrate_zvec_collections \\
        --project-id 弈局谋心 --backup

    # 标记"双读 fallback" 模式 (写一个 JSON 标记,不修改产品代码)
    python -m novel_forge.memory.migrate_zvec_collections \\
        --project-id 弈局谋心 --fallback-double-read

    # 显式删除旧目录 (需先 backup,除非 --force)
    python -m novel_forge.memory.migrate_zvec_collections \\
        --project-id 弈局谋心 --cleanup

与 Task 1.4 (1-to-1 合并) 的差异:
- 本任务是 **2-to-2 重组** — 两个独立 collection 各自映射到
  ``zvec_vectors/<sub>/`` 下独立子目录.
- 备份按 per-old-dir 组织: ``.bak/<timestamp>/<old_dir_name>/``,
  不是平铺所有文件到一个备份根.
- 双读标记记录**两条** old→new 映射,而不是一条.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

_log = logging.getLogger("novel_forge.memory.migrate_zvec_collections")

# ---------------------------------------------------------------------------
# 路径常量 & 2-to-2 重组映射
# ---------------------------------------------------------------------------

#: zvec_vectors/ 是 episodic collection 的当前位置 — **保留不动**.
EPISODIC_DIR_NAME = "zvec_vectors"

#: 受控迁移要重组的旧 collection 列表 — 每个元素是 (旧目录名, 新的 zvec_vectors/ 子目录名).
#: 这是**唯一**定义 2-to-2 映射的地方;所有 backup/cleanup/marker 都从这里派生.
COLLECTION_PAIRS: tuple[tuple[str, str], ...] = (
    ("zvec_init_coherence_vectors", "init_coherence"),
    ("zvec_outline_vectors", "outline"),
)

#: 不允许的旧目录名 (防止误删 episodic 或 expression).
PROTECTED_OLD_DIRS: frozenset[str] = frozenset({EPISODIC_DIR_NAME, "zvec_expression_vectors"})

BACKUP_DIR_SUFFIX = ".bak"
DOUBLE_READ_MARKER_NAME = ".zvec_collections_double_read.json"


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CollectionMigrationSpec:
    """单对 (old_dir_name, new_subdir_name) 的不可变迁移规格."""

    old_dir_name: str
    new_subdir_name: str

    @property
    def backup_subdir_name(self) -> str:
        """备份时, 在 ``.bak/<timestamp>/`` 下的子目录名 (= old_dir_name)."""
        return f"{self.old_dir_name}{BACKUP_DIR_SUFFIX}"


@dataclass
class PerCollectionReport:
    """单个 collection 对的迁移报告子项.

    Attributes:
        old_dir_name: 旧目录相对名 (例: ``zvec_init_coherence_vectors``)
        new_subdir_name: 重组后位置 (例: ``init_coherence``) — 相对 zvec_vectors/
        old_dir: 旧目录绝对路径
        new_dir: 重组目标绝对路径 (``<memory>/zvec_vectors/<new_subdir_name>``)
        old_dir_exists: 旧目录是否实际存在
        old_file_count: 旧目录下文件数 (递归)
        old_total_bytes: 旧目录下文件总字节数
        backup_path: 若做了 backup,记录备份目标绝对路径
        actions_taken: 该 collection 对上实际执行的写操作列表
        actions_skipped: 由于 dry-run/guard 跳过的写操作列表
        cleanup_refused_reason: 若 cleanup 被拒绝,记录原因
    """

    old_dir_name: str
    new_subdir_name: str
    old_dir: str
    new_dir: str
    old_dir_exists: bool
    old_file_count: int
    old_total_bytes: int
    backup_path: str = ""
    actions_taken: list[str] = field(default_factory=list)
    actions_skipped: list[str] = field(default_factory=list)
    cleanup_refused_reason: str = ""


@dataclass
class MigrationReport:
    """一次 4-collection 迁移调用的结构化报告.

    Attributes:
        project_id: 项目 ID
        storage_root: 存储根目录绝对路径
        episodic_dir: zvec_vectors/ (episodic) 绝对路径 — 保留不动
        collections: per-collection 子报告列表 (顺序与 ``COLLECTION_PAIRS`` 一致)
        actions_taken: 跨所有 collection 实际执行的写操作列表
        actions_skipped: 由于 dry-run/guard 跳过的写操作列表
        double_read_marker_path: 若写了双读标记,记录路径
        summary_lines: 给人类读的多行摘要 (在 CLI 末尾打印)
    """

    project_id: str
    storage_root: str
    episodic_dir: str
    collections: list[PerCollectionReport] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)
    actions_skipped: list[str] = field(default_factory=list)
    double_read_marker_path: str = ""
    summary_lines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """转为可 JSON 序列化的 dict."""
        return asdict(self)


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------


def build_specs() -> tuple[CollectionMigrationSpec, ...]:
    """从模块常量 ``COLLECTION_PAIRS`` 派生不可变 migration 规格列表.

    独立成函数便于测试时注入自定义映射 (虽然生产代码不会这么做).
    """
    return tuple(
        CollectionMigrationSpec(old_dir_name=old, new_subdir_name=new)
        for old, new in COLLECTION_PAIRS
    )


def resolve_project_paths(
    project_id: str,
    storage_root: str | Path | None = None,
) -> tuple[Path, Path, Path, tuple[Path, ...], tuple[Path, ...]]:
    """根据 project_id + storage_root 解析关键路径.

    Args:
        project_id: 项目 ID (例: "弈局谋心")
        storage_root: 存储根目录. 优先级: 显式参数 > ``NOVEL_FORGE_STORAGE_ROOT``
            环境变量 > ``./data`` (项目默认)

    Returns:
        ``(storage_root_abs, project_dir, memory_dir, old_dirs, new_subdirs)`` —
        ``old_dirs`` / ``new_subdirs`` 与 ``COLLECTION_PAIRS`` 一一对应.
    """
    if storage_root is None:
        env_root = os.environ.get("NOVEL_FORGE_STORAGE_ROOT")
        storage_root = Path(env_root) if env_root else Path.cwd() / "data"

    storage_root_abs = Path(storage_root).expanduser().resolve()
    project_dir = storage_root_abs / project_id
    memory_dir = project_dir / "memory"

    specs = build_specs()
    old_dirs = tuple(memory_dir / spec.old_dir_name for spec in specs)
    new_subdirs = tuple(memory_dir / EPISODIC_DIR_NAME / spec.new_subdir_name for spec in specs)
    return storage_root_abs, project_dir, memory_dir, old_dirs, new_subdirs


# ---------------------------------------------------------------------------
# 扫描 (只读)
# ---------------------------------------------------------------------------


def scan_old_dir(old_dir: Path) -> tuple[int, int]:
    """递归扫描旧目录, 返回 ``(file_count, total_bytes)``.

    这是纯只读操作 — 不修改任何文件. 即便旧目录不存在也安全返回零值.
    """
    if not old_dir.exists() or not old_dir.is_dir():
        return 0, 0
    file_count = 0
    total_bytes = 0
    for p in old_dir.rglob("*"):
        if p.is_file():
            file_count += 1
            try:
                total_bytes += p.stat().st_size
            except OSError:
                continue
    return file_count, total_bytes


# ---------------------------------------------------------------------------
# 核心操作 — 全部 per-collection 隔离
# ---------------------------------------------------------------------------


def perform_backup(
    old_dir: Path,
    memory_dir: Path,
    timestamp: str,
    spec: CollectionMigrationSpec,
) -> Path:
    """把 ``old_dir`` 复制到 ``memory_dir/.bak/<timestamp>/<old_dir_name>/``.

    Args:
        old_dir: 要备份的旧目录绝对路径
        memory_dir: 旧目录的父目录 (通常是 ``<project>/memory``)
        timestamp: UTC ISO-8601 时间戳 (与 ``run_migration`` 内的 timestamp 共享)
        spec: 当前 collection 对的迁移规格 (用于确定备份子目录名)

    Returns:
        备份目标绝对路径

    Raises:
        FileNotFoundError: 旧目录不存在
        FileExistsError: 同时间戳的备份已存在
    """
    if not old_dir.exists() or not old_dir.is_dir():
        raise FileNotFoundError(f"old dir does not exist: {old_dir}")

    backup_root = _make_backup_root(memory_dir)
    backup_target = backup_root / timestamp / spec.old_dir_name
    if backup_target.exists():
        raise FileExistsError(f"backup target already exists: {backup_target}")
    backup_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(old_dir, backup_target, symlinks=False)
    return backup_target


def _make_backup_root(memory_dir: Path) -> Path:
    """返回 ``<memory_dir>/.bak`` 的绝对路径 (小工具, 避免重复)."""
    return memory_dir / BACKUP_DIR_SUFFIX  # BACKUP_DIR_SUFFIX = ".bak"


def _backup_root_exists(memory_dir: Path) -> bool:
    """检查 ``<memory_dir>/.bak/`` 至少含一个时间戳子目录."""
    root = _make_backup_root(memory_dir)
    if not root.exists() or not root.is_dir():
        return False
    return any(p.is_dir() for p in root.iterdir())


def write_double_read_marker(
    project_dir: Path,
    *,
    specs: Sequence[CollectionMigrationSpec],
    old_dirs: Sequence[Path],
    new_subdirs: Sequence[Path],
) -> Path:
    """在 ``<project_dir>`` 下写一个 ``.zvec_collections_double_read.json`` 标记文件.

    标记记录**所有** old→new 映射, 任何未来产品代码 (或外层 wrapper) 可通过
    ``Path(<project_dir>).joinpath('.zvec_collections_double_read.json').exists()``
    来判断是否启用 fallback. 本工具**不**修改产品代码, 只生成意图标记.
    """
    marker = project_dir / DOUBLE_READ_MARKER_NAME
    payload: dict[str, object] = {
        "enabled": True,
        "reason": "zvec_4collection_migration",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "instruction": (
            "When this marker exists, read code SHOULD fall back from each "
            "old_path to its new_path on lookup miss. Delete this file to "
            "disable. Mappings listed in 'mappings' (per spec)."
        ),
        "mappings": [
            {
                "old_dir_name": spec.old_dir_name,
                "new_subdir_name": spec.new_subdir_name,
                "old_path": str(old),
                "new_path": str(new),
            }
            for spec, old, new in zip(specs, old_dirs, new_subdirs, strict=True)
        ],
    }
    marker.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return marker


def remove_double_read_marker(project_dir: Path) -> bool:
    """删除双读标记文件. 返回是否真的删了."""
    marker = project_dir / DOUBLE_READ_MARKER_NAME
    if marker.exists():
        marker.unlink()
        return True
    return False


def perform_cleanup(
    old_dir: Path,
    memory_dir: Path,
    *,
    force: bool = False,
) -> tuple[bool, str]:
    """删除单对 collection 的旧目录. 默认要求 ``.bak/`` 存在,除非 ``force=True``.

    Args:
        old_dir: 旧目录绝对路径
        memory_dir: 父目录 (用于检查 ``.bak/`` 存在性)
        force: 跳过 .bak 存在性检查

    Returns:
        ``(deleted, refused_reason)`` — refused_reason 非空时代表拒绝删除
    """
    if not old_dir.exists():
        return False, "old_dir_not_found"

    if not force and not _backup_root_exists(memory_dir):
        return False, "no_backup_found_pass_--force_to_override"

    shutil.rmtree(old_dir)
    return True, ""


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _build_per_collection_report(
    spec: CollectionMigrationSpec,
    old_dir: Path,
    new_dir: Path,
) -> PerCollectionReport:
    """构造单个 collection 对的 per-collection 报告 (scan-only, 不写盘)."""
    file_count, total_bytes = scan_old_dir(old_dir)
    return PerCollectionReport(
        old_dir_name=spec.old_dir_name,
        new_subdir_name=spec.new_subdir_name,
        old_dir=str(old_dir),
        new_dir=str(new_dir),
        old_dir_exists=old_dir.exists() and old_dir.is_dir(),
        old_file_count=file_count,
        old_total_bytes=total_bytes,
    )


def run_migration(
    project_id: str,
    *,
    storage_root: str | Path | None = None,
    dry_run: bool = False,
    backup: bool = False,
    fallback_double_read: bool = False,
    cleanup: bool = False,
    cleanup_force: bool = False,
) -> MigrationReport:
    """执行一次受控 4-collection 迁移并返回结构化报告.

    参数语义 (与 Task 1.4 完全一致):
        - ``dry_run=True`` 时, backup/cleanup/fallback 全部只报告不执行
        - ``backup=True``: 把每个旧目录复制到 ``.bak/<timestamp>/<old>/`` (无 dry-run 时)
        - ``fallback_double_read=True``: 写 ``.zvec_collections_double_read.json`` 标记
        - ``cleanup=True``: 删除每个旧目录 (需 backup 存在, 除非 ``cleanup_force``)
        - 任意参数都默认是 ``False`` — 默认行为 = 纯只读报告

    Returns:
        :class:`MigrationReport` 实例
    """
    storage_root_abs, project_dir, memory_dir, old_dirs, new_subdirs = resolve_project_paths(
        project_id, storage_root
    )
    specs = build_specs()

    # ---- 防御: 拒绝危险输入 ----
    for spec in specs:
        if spec.old_dir_name in PROTECTED_OLD_DIRS:
            raise ValueError(
                f"refusing to migrate protected dir: {spec.old_dir_name}"
            )

    # ---- 报告阶段: 总是先说"将做什么" ----
    for spec, old_dir in zip(specs, old_dirs, strict=True):
        file_count, total_bytes = scan_old_dir(old_dir)
        if file_count > 0:
            _log.info(
                "Will migrate %d entries (%d bytes) from %s -> %s",
                file_count,
                total_bytes,
                old_dir,
                memory_dir / EPISODIC_DIR_NAME / spec.new_subdir_name,
            )
        else:
            _log.info("Old dir %s is empty or missing — nothing to migrate", old_dir)

    # 构造 per-collection 报告 (只读扫描, 不写盘)
    per_collection: list[PerCollectionReport] = [
        _build_per_collection_report(spec, old, new)
        for spec, old, new in zip(specs, old_dirs, new_subdirs, strict=True)
    ]
    report = MigrationReport(
        project_id=project_id,
        storage_root=str(storage_root_abs),
        episodic_dir=str(memory_dir / EPISODIC_DIR_NAME),
        collections=per_collection,
    )

    # ---- dry-run 短路 ----
    if dry_run:
        _log.info("DRY-RUN, no changes")
        for pc in report.collections:
            pc.actions_skipped.extend(
                [
                    f"backup (would copy {pc.old_dir_name} to .bak/<ts>/{pc.old_dir_name}/)",
                    f"cleanup (would remove {pc.old_dir_name})",
                ]
            )
        report.actions_skipped.append("fallback_double_read")
        report.summary_lines = _build_summary_lines(report, dry_run_active=True)
        return report

    # ---- 实际写操作 (从这开始才允许触碰磁盘) ----
    # 共用一个 timestamp, 让一次 run 的所有备份落在同一目录
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    if backup:
        for spec, old_dir, pc in zip(specs, old_dirs, report.collections, strict=True):
            try:
                backup_path = perform_backup(
                    old_dir, memory_dir, timestamp, spec
                )
                pc.backup_path = str(backup_path)
                action = f"backup({spec.old_dir_name}) -> {backup_path}"
                pc.actions_taken.append(action)
                report.actions_taken.append(action)
                _log.info("Backup created: %s", backup_path)
            except FileNotFoundError as exc:
                _log.warning("Backup skipped for %s: %s", spec.old_dir_name, exc)
                pc.actions_skipped.append(f"backup ({spec.old_dir_name}: dir missing)")

    if fallback_double_read:
        marker = write_double_read_marker(
            project_dir,
            specs=specs,
            old_dirs=old_dirs,
            new_subdirs=new_subdirs,
        )
        report.double_read_marker_path = str(marker)
        action = f"fallback_double_read -> {marker}"
        report.actions_taken.append(action)
        _log.info("Double-read marker written: %s", marker)
        _log.info(
            "NOTE: production code MUST check this marker to honor fallback"
        )

    if cleanup:
        for spec, old_dir, pc in zip(specs, old_dirs, report.collections, strict=True):
            deleted, refused_reason = perform_cleanup(
                old_dir, memory_dir, force=cleanup_force
            )
            if deleted:
                action = f"cleanup({spec.old_dir_name}) -> removed"
                pc.actions_taken.append(action)
                report.actions_taken.append(action)
                _log.info("Old dir removed: %s", old_dir)
            else:
                pc.cleanup_refused_reason = refused_reason
                pc.actions_skipped.append(
                    f"cleanup({spec.old_dir_name}: refused, {refused_reason})"
                )
                _log.warning(
                    "Cleanup refused for %s: %s", spec.old_dir_name, refused_reason
                )

    report.summary_lines = _build_summary_lines(report, dry_run_active=False)
    return report


def _build_summary_lines(
    report: MigrationReport, *, dry_run_active: bool
) -> list[str]:
    """构造给人类读的多行 summary, 末尾由 CLI 打印."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("Zvec 4-Collection Migration Report (Wave 3 / Task 3.1)")
    lines.append("=" * 72)
    lines.append(f"Project ID         : {report.project_id}")
    lines.append(f"Storage root       : {report.storage_root}")
    lines.append(f"Episodic dir       : {report.episodic_dir} (preserved)")
    lines.append("-" * 72)
    for idx, pc in enumerate(report.collections, start=1):
        lines.append(
            f"[{idx}] {pc.old_dir_name} -> zvec_vectors/{pc.new_subdir_name}/"
        )
        lines.append(f"    old path     : {pc.old_dir}")
        lines.append(f"    new path     : {pc.new_dir}")
        lines.append(f"    exists       : {pc.old_dir_exists}")
        lines.append(f"    file count   : {pc.old_file_count}")
        lines.append(f"    total bytes  : {pc.old_total_bytes}")
        if pc.backup_path:
            lines.append(f"    backup       : {pc.backup_path}")
        if pc.cleanup_refused_reason:
            lines.append(
                f"    cleanup      : REFUSED ({pc.cleanup_refused_reason})"
            )
        elif any("cleanup" in a for a in pc.actions_taken):
            lines.append("    cleanup      : removed")
    lines.append("-" * 72)
    lines.append("Actions taken:")
    if report.actions_taken:
        for action in report.actions_taken:
            lines.append(f"  [OK]   {action}")
    else:
        lines.append("  (none)")
    lines.append("Actions skipped:")
    if report.actions_skipped:
        for action in report.actions_skipped:
            lines.append(f"  [SKIP] {action}")
    else:
        lines.append("  (none)")
    if report.double_read_marker_path:
        lines.append(f"Double-read marker : {report.double_read_marker_path}")
    lines.append("=" * 72)
    if dry_run_active:
        lines.append("MODE: DRY-RUN — no changes were made to disk")
    else:
        lines.append("MODE: ACTIVE — see actions above")
    lines.append("=" * 72)
    return lines


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    """构造 argparse parser. Flags 命名与 plan/task 严格对齐."""
    parser = argparse.ArgumentParser(
        prog="novel_forge.memory.migrate_zvec_collections",
        description=(
            "受控 4-collection Zvec 重组迁移工具 (Wave 3 / Task 3.1 骨架). "
            "默认行为: 只读 + 报告, 不动文件. "
            "实际把旧 collection 数据写入 zvec_vectors/<sub>/ 留作后续任务."
        ),
    )
    parser.add_argument(
        "--project-id",
        required=True,
        help="项目 ID (例如: 弈局谋心)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="不写盘,只报告将做什么 (默认行为,显式声明更清晰)",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help=(
            "把每个旧 zvec 目录复制到 <memory>/.bak/<UTC-timestamp>/<old_dir_name>/. "
            "2 个 collection 各得一个独立备份子目录."
        ),
    )
    parser.add_argument(
        "--fallback-double-read",
        action="store_true",
        help=(
            "在 <project>/ 下写 .zvec_collections_double_read.json 标记 — "
            "产品代码可在读取时检测该标记以启用旧路径 fallback. "
            "本工具本身不修改产品代码."
        ),
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help=(
            "显式删除 2 个旧 zvec 目录 "
            "(需先 --backup,除非 --force)"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="配合 --cleanup: 跳过 .bak 存在性检查",
    )
    parser.add_argument(
        "--storage-root",
        default=None,
        help=(
            "存储根目录. 优先级: 此参数 > $NOVEL_FORGE_STORAGE_ROOT > ./data"
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="只打 WARNING+ 日志 (默认 INFO)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="末尾额外输出 JSON 报告 (便于脚本消费)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口. 返回 exit code (0 = 成功/报告完成, 2 = cleanup 被拒绝)."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="[%(levelname)s] %(message)s",
    )

    try:
        report = run_migration(
            project_id=args.project_id,
            storage_root=args.storage_root,
            dry_run=args.dry_run,
            backup=args.backup,
            fallback_double_read=args.fallback_double_read,
            cleanup=args.cleanup,
            cleanup_force=args.force,
        )
    except Exception as exc:  # pragma: no cover — 顶层兜底
        _log.error("Migration failed: %s", exc)
        return 1

    for line in report.summary_lines:
        print(line)

    if args.json_output:
        print("---JSON-REPORT-START---")
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        print("---JSON-REPORT-END---")

    # 任意一个 collection 的 cleanup 被拒绝时, exit code = 2
    if any(pc.cleanup_refused_reason for pc in report.collections):
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
