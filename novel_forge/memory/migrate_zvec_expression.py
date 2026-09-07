"""受控 ExpressionChannel Zvec 合并迁移工具 (P0.4 骨架).

重要设计原则 (源自 plan memory-architecture-v3 Task 5):
- **绝不**自动删除旧目录 — 仅当显式 ``--cleanup`` 才允许删除
- **绝不**实现真实 zvec 数据合并 — 完整 4 collection 合并放在 Task 3.1
  本工具只做 "dry-run + backup + structure check" 骨架
- **绝不**修改 ``expression_channel.py``、``zvec_store.py``、``integration.py``
  ``--fallback-double-read`` 通过写一个 JSON 标记文件来表达意图,不修改产品代码
- 默认 (无任何 flag) 行为: 只读 + 报告,不动一个字节

CLI 用法::

    # 仅报告,不动文件 (默认)
    python -m novel_forge.memory.migrate_zvec_expression --project-id 弈局谋心

    # 显式 dry-run
    python -m novel_forge.memory.migrate_zvec_expression \\
        --project-id 弈局谋心 --dry-run

    # 备份到 .bak/<timestamp>/
    python -m novel_forge.memory.migrate_zvec_expression \\
        --project-id 弈局谋心 --backup

    # 标记"双读 fallback" 模式 (写一个 JSON 标记,不修改产品代码)
    python -m novel_forge.memory.migrate_zvec_expression \\
        --project-id 弈局谋心 --fallback-double-read

    # 显式删除旧目录 (需先 backup,除非 --force)
    python -m novel_forge.memory.migrate_zvec_expression \\
        --project-id 弈局谋心 --cleanup
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

_log = logging.getLogger("novel_forge.memory.migrate_zvec_expression")

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------

OLD_DIR_NAME = "zvec_expression_vectors"
NEW_DIR_NAME = "zvec_vectors"
BACKUP_DIR_SUFFIX = ".bak"
DOUBLE_READ_MARKER_NAME = ".zvec_double_read.json"


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class MigrationReport:
    """一次迁移调用的结构化报告.

    Attributes:
        project_id: 项目 ID
        storage_root: 存储根目录绝对路径
        old_dir: 旧 zvec_expression_vectors 目录绝对路径
        new_dir: 新 zvec_vectors 目录绝对路径
        old_dir_exists: 旧目录是否存在
        old_file_count: 旧目录下文件数 (递归)
        old_total_bytes: 旧目录下文件总字节数
        old_subdirs: 旧目录下一级子目录列表
        actions_taken: 实际执行的写操作列表
        actions_skipped: 由于 dry-run/guard 跳过的写操作列表
        cleanup_refused_reason: 若 cleanup 被拒绝,记录原因
        backup_path: 若做了 backup,记录备份目标路径
        double_read_marker_path: 若写了双读标记,记录路径
        summary_lines: 给人类读的多行摘要 (在 CLI 末尾打印)
    """

    project_id: str
    storage_root: str
    old_dir: str
    new_dir: str
    old_dir_exists: bool
    old_file_count: int
    old_total_bytes: int
    old_subdirs: list[str] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)
    actions_skipped: list[str] = field(default_factory=list)
    cleanup_refused_reason: str = ""
    backup_path: str = ""
    double_read_marker_path: str = ""
    summary_lines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """转为可 JSON 序列化的 dict."""
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------


def resolve_project_paths(
    project_id: str,
    storage_root: str | Path | None = None,
) -> tuple[Path, Path, Path, Path]:
    """根据 project_id + storage_root 解析 4 个关键路径.

    Args:
        project_id: 项目 ID (例: "弈局谋心")
        storage_root: 存储根目录. 优先级: 显式参数 > ``NOVEL_FORGE_STORAGE_ROOT``
            环境变量 > ``./data`` (项目默认)

    Returns:
        ``(storage_root_abs, project_dir, old_dir, new_dir)``
    """
    if storage_root is None:
        env_root = os.environ.get("NOVEL_FORGE_STORAGE_ROOT")
        storage_root = Path(env_root) if env_root else Path.cwd() / "data"

    storage_root_abs = Path(storage_root).expanduser().resolve()
    project_dir = storage_root_abs / project_id
    memory_dir = project_dir / "memory"
    old_dir = memory_dir / OLD_DIR_NAME
    new_dir = memory_dir / NEW_DIR_NAME
    return storage_root_abs, project_dir, old_dir, new_dir


# ---------------------------------------------------------------------------
# 扫描 (只读)
# ---------------------------------------------------------------------------


def scan_old_dir(old_dir: Path) -> tuple[int, int, list[str]]:
    """递归扫描旧目录,返回 (file_count, total_bytes, top_level_subdirs).

    这是纯只读操作 — 不修改任何文件. 即便旧目录不存在也安全返回零值.
    """
    if not old_dir.exists() or not old_dir.is_dir():
        return 0, 0, []
    file_count = 0
    total_bytes = 0
    for p in old_dir.rglob("*"):
        if p.is_file():
            file_count += 1
            try:
                total_bytes += p.stat().st_size
            except OSError:
                # 符号链接失效等不致命错误,跳过
                continue
    top_level_subdirs = sorted(
        entry.name for entry in old_dir.iterdir() if entry.is_dir()
    )
    return file_count, total_bytes, top_level_subdirs


# ---------------------------------------------------------------------------
# 核心操作
# ---------------------------------------------------------------------------


def perform_backup(old_dir: Path, memory_dir: Path) -> Path:
    """把 ``old_dir`` 复制到 ``memory_dir/<OLD_DIR_NAME>.bak/<timestamp>/``.

    Args:
        old_dir: 要备份的旧目录绝对路径
        memory_dir: 旧目录的父目录 (通常是 ``<project>/memory``)

    Returns:
        备份目标绝对路径

    Raises:
        FileNotFoundError: 旧目录不存在
        FileExistsError: 同时间戳的备份已存在 (微秒概率,但显式保护)
    """
    if not old_dir.exists() or not old_dir.is_dir():
        raise FileNotFoundError(f"old dir does not exist: {old_dir}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_root = memory_dir / f"{OLD_DIR_NAME}{BACKUP_DIR_SUFFIX}"
    backup_target = backup_root / timestamp
    if backup_target.exists():
        raise FileExistsError(f"backup target already exists: {backup_target}")
    backup_root.mkdir(parents=True, exist_ok=True)
    # copytree 会创建 backup_target 自己
    shutil.copytree(old_dir, backup_target, symlinks=False)
    return backup_target


def write_double_read_marker(
    project_dir: Path,
    *,
    old_dir: Path,
    new_dir: Path,
) -> Path:
    """在 ``<project_dir>`` 下写一个 ``.zvec_double_read.json`` 标记文件.

    该文件表达 "迁移期间双读" 的意图 — 任何未来 ``expression_channel.py``
    的读取逻辑 (或外层 wrapper) 可通过 ``Path(<project_dir>).joinpath(
    '.zvec_double_read.json').exists()`` 来判断是否启用 fallback.

    本工具**不**修改产品代码,只生成意图标记.
    """
    marker = project_dir / DOUBLE_READ_MARKER_NAME
    payload = {
        "enabled": True,
        "reason": "expression_channel_zvec_migration",
        "old_path": str(old_dir),
        "new_path": str(new_dir),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "instruction": (
            "When this marker exists, read code SHOULD fall back from old_path "
            "to new_path on lookup miss. Delete this file to disable."
        ),
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
    """删除旧目录. 默认要求 ``.bak/`` 存在,除非 ``force=True``.

    Args:
        old_dir: 旧目录绝对路径
        memory_dir: 父目录 (用于检查 ``.bak/`` 存在性)
        force: 跳过 .bak 存在性检查

    Returns:
        ``(deleted, refused_reason)`` — refused_reason 非空时代表拒绝删除
    """
    if not old_dir.exists():
        return False, "old_dir_not_found"

    if not force:
        backup_root = memory_dir / f"{OLD_DIR_NAME}{BACKUP_DIR_SUFFIX}"
        if not backup_root.exists() or not any(backup_root.iterdir()):
            return False, "no_backup_found_pass_--force_to_override"

    shutil.rmtree(old_dir)
    return True, ""


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


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
    """执行一次受控迁移并返回结构化报告.

    参数语义:
        - ``dry_run=True`` 时,backup/cleanup/fallback 全部只报告不执行
        - ``backup=True``: 把旧目录复制到 ``.bak/<timestamp>/`` (无 dry-run 时)
        - ``fallback_double_read=True``: 写 ``.zvec_double_read.json`` 标记
        - ``cleanup=True``: 删除旧目录 (需 backup 存在,除非 ``cleanup_force``)
        - 任意参数都默认是 ``False`` — 默认行为 = 纯只读报告

    Returns:
        :class:`MigrationReport` 实例
    """
    storage_root_abs, project_dir, old_dir, new_dir = resolve_project_paths(
        project_id, storage_root
    )
    memory_dir = project_dir / "memory"

    file_count, total_bytes, top_level_subdirs = scan_old_dir(old_dir)
    report = MigrationReport(
        project_id=project_id,
        storage_root=str(storage_root_abs),
        old_dir=str(old_dir),
        new_dir=str(new_dir),
        old_dir_exists=old_dir.exists() and old_dir.is_dir(),
        old_file_count=file_count,
        old_total_bytes=total_bytes,
        old_subdirs=top_level_subdirs,
    )

    # ---- 报告阶段: 总是先说"将做什么" ----
    _log.info(
        "Scan complete: %s files, %d bytes across %d top-level subdirs",
        file_count,
        total_bytes,
        len(top_level_subdirs),
    )
    if file_count > 0:
        _log.info("Will migrate %d entries from %s", file_count, old_dir)
        _log.info("  -> target (logical): %s", new_dir)
    else:
        _log.info("Old dir %s is empty or missing — nothing to migrate", old_dir)

    # ---- dry-run 短路 ----
    if dry_run:
        _log.info("DRY-RUN, no changes")
        report.actions_skipped.extend(
            ["backup", "fallback_double_read", "cleanup"]
        )
        report.summary_lines = _build_summary_lines(report, dry_run_active=True)
        return report

    # ---- 实际写操作 (从这开始才允许触碰磁盘) ----

    if backup:
        try:
            backup_path = perform_backup(old_dir, memory_dir)
            report.backup_path = str(backup_path)
            report.actions_taken.append(f"backup -> {backup_path}")
            _log.info("Backup created: %s", backup_path)
        except FileNotFoundError as exc:
            _log.warning("Backup skipped: %s", exc)
            report.actions_skipped.append("backup (old dir missing)")

    if fallback_double_read:
        marker = write_double_read_marker(
            project_dir, old_dir=old_dir, new_dir=new_dir
        )
        report.double_read_marker_path = str(marker)
        report.actions_taken.append(f"fallback_double_read -> {marker}")
        _log.info("Double-read marker written: %s", marker)
        _log.info(
            "NOTE: production code MUST check this marker to honor fallback"
        )

    if cleanup:
        deleted, refused_reason = perform_cleanup(
            old_dir, memory_dir, force=cleanup_force
        )
        if deleted:
            report.actions_taken.append(f"cleanup -> {old_dir}")
            _log.info("Old dir removed: %s", old_dir)
        else:
            report.cleanup_refused_reason = refused_reason
            report.actions_skipped.append(f"cleanup (refused: {refused_reason})")
            _log.warning("Cleanup refused: %s", refused_reason)

    report.summary_lines = _build_summary_lines(report, dry_run_active=False)
    return report


def _build_summary_lines(
    report: MigrationReport, *, dry_run_active: bool
) -> list[str]:
    """构造给人类读的多行 summary, 末尾由 CLI 打印."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("ExpressionChannel Zvec Migration Report")
    lines.append("=" * 72)
    lines.append(f"Project ID         : {report.project_id}")
    lines.append(f"Storage root       : {report.storage_root}")
    lines.append(f"Old dir            : {report.old_dir}")
    lines.append(f"  exists           : {report.old_dir_exists}")
    lines.append(f"  file count       : {report.old_file_count}")
    lines.append(f"  total bytes      : {report.old_total_bytes}")
    lines.append(f"  top-level subdirs: {', '.join(report.old_subdirs) or '(none)'}")
    lines.append(f"New dir (logical)  : {report.new_dir}")
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
    if report.backup_path:
        lines.append(f"Backup target      : {report.backup_path}")
    if report.double_read_marker_path:
        lines.append(f"Double-read marker : {report.double_read_marker_path}")
    if report.cleanup_refused_reason:
        lines.append(
            f"Cleanup refused    : {report.cleanup_refused_reason} "
            "(pass --force to override, after a --backup)"
        )
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
    """构造 argparse parser.

    Flags 命名与 plan/task 严格对齐,便于 QA 脚本调用:
        --project-id (required)
        --dry-run
        --backup
        --fallback-double-read
        --cleanup
        --force (cleanup 时的覆盖标志)
        --storage-root (可选, 默认 $NOVEL_FORGE_STORAGE_ROOT 或 ./data)
    """
    parser = argparse.ArgumentParser(
        prog="novel_forge.memory.migrate_zvec_expression",
        description=(
            "受控 ExpressionChannel Zvec 合并迁移工具 (P0.4 骨架). "
            "默认行为: 只读 + 报告, 不动文件. "
            "完整 4 collection 合并放在 Task 3.1."
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
        help="把 zvec_expression_vectors/ 复制到 zvec_expression_vectors.bak/<timestamp>/",
    )
    parser.add_argument(
        "--fallback-double-read",
        action="store_true",
        help=(
            "在 <project>/ 下写 .zvec_double_read.json 标记 — "
            "产品代码可在读取时检测该标记以启用旧路径 fallback. "
            "本工具本身不修改产品代码."
        ),
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="显式删除 zvec_expression_vectors/ 目录 (需先 --backup,除非 --force)",
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
    """CLI 入口. 返回 exit code (0 = 成功/报告完成, 1 = 错误)."""
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

    # 末尾打印 summary (始终)
    for line in report.summary_lines:
        print(line)

    if args.json_output:
        print("---JSON-REPORT-START---")
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        print("---JSON-REPORT-END---")

    # cleanup 被拒绝时, exit code = 2 便于脚本区分
    if report.cleanup_refused_reason:
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
