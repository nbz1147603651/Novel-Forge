"""受控 Chapter Shard 迁移工具 (Wave 3 Task 11 骨架).

重要设计原则 (源自 plan memory-architecture-v3 Task 11):
- **不**修改 ``integration.py:save_to_disk`` / ``load_from_disk`` (本次只做骨架迁移脚本)
- **不**删除 ``project_memory.json`` (保留为索引)
- **不**在脚本外修改任何产品代码
- **不**实现真实的 sharded 加载逻辑 (留作 Plan 后续工作)
- 默认 (无任何 flag) 行为: 只读 + 报告, 不动一个字节

迁移目标结构::

    <project>/memory/
        project_memory.json   # 索引 + 全局状态 (last_indexed_chapter, summary_stats 等)
        chapter_001.json      # 单章分片 (summary_cache[1], chapter_content_hash[1], ... )
        chapter_002.json
        ...
        chapter_NNN.json
        motif_state.json      # 保留 (Task 1.3 已拆出)

Chapter-shardable 字段分类策略:
    按 chapter 拆 (写入 chapter_NNN.json):
        - ``summary_cache[<chapter>]``                 → 章节摘要缓存
        - ``chapter_content_hash[<chapter>]``         → 章节内容指纹
        - ``episodic_index.chapter_events[<chapter>]`` → 章节情景事件
        - ``episodic_index.chapter_critiques[<chapter>]`` → 章节评审记录

    保留全局 (留在 project_memory.json):
        - ``last_indexed_chapter``                     → 索引游标
        - ``summary_stats``                            → 跨章统计
        - ``episodic_index.vector_store``              → 向量存储元数据
        - ``episodic_index.outline_data``              → 大纲数据
        - ``episodic_index.critique_index``            → 全局评审索引
        - ``project_id``                               → 项目标识
        - ``motif_cache`` / ``motif_tracker``          → Task 1.3 负责,本脚本跳过

CLI 用法::

    # 仅报告,不动文件 (默认)
    python -m novel_forge.memory.migrate_to_chapter_shards --project-id 弈局谋心

    # 显式 dry-run
    python -m novel_forge.memory.migrate_to_chapter_shards \\
        --project-id 弈局谋心 --dry-run

    # 备份到 .bak/<UTC-ISO-timestamp>/project_memory.json
    python -m novel_forge.memory.migrate_to_chapter_shards \\
        --project-id 弈局谋心 --backup

    # 标记"双读 fallback" 模式 (写一个 JSON 标记, 不修改产品代码)
    python -m novel_forge.memory.migrate_to_chapter_shards \\
        --project-id 弈局谋心 --fallback-double-read

配置项占位 (本次不实现实际加载逻辑):
    ``NOVEL_FORGE_MEMORY_FORMAT=sharded|monolithic`` 默认 sharded
    实际集成到 ``load_from_disk`` 留给 Plan 后续任务
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
from typing import Any, Sequence

_log = logging.getLogger("novel_forge.memory.migrate_to_chapter_shards")

# ---------------------------------------------------------------------------
# 路径与文件命名常量
# ---------------------------------------------------------------------------

PROJECT_MEMORY_FILENAME = "project_memory.json"
CHAPTER_SHARD_PREFIX = "chapter_"
CHAPTER_SHARD_SUFFIX = ".json"
BACKUP_DIR_SUFFIX = ".bak"
DOUBLE_READ_MARKER_NAME = ".chapter_shards_double_read.json"

# 默认存储根 (当环境变量和 CLI 参数都未指定时)
DEFAULT_STORAGE_ROOT = "./data"


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class MigrationReport:
    """一次迁移调用的结构化报告.

    Attributes:
        project_id: 项目 ID
        storage_root: 存储根目录绝对路径
        project_memory_path: project_memory.json 绝对路径
        exists: project_memory.json 是否存在
        total_bytes: project_memory.json 字节大小
        global_fields: 保留在 project_memory.json 的全局字段
        shardable_fields: 按 chapter 分片的字段列表
        chapter_count: 实际可分片的章节数
        chapter_numbers: 排序后的章节号列表
        total_shard_bytes_estimated: 所有分片预估字节总数
        actions_taken: 实际执行的写操作列表
        actions_skipped: 由于 dry-run/guard 跳过的写操作列表
        backup_path: 若做了 backup, 记录备份目标路径
        double_read_marker_path: 若写了双读标记, 记录路径
        summary_lines: 给人类读的多行摘要 (在 CLI 末尾打印)
    """

    project_id: str
    storage_root: str
    project_memory_path: str
    exists: bool
    total_bytes: int
    global_fields: list[str] = field(default_factory=list)
    shardable_fields: list[str] = field(default_factory=list)
    chapter_count: int = 0
    chapter_numbers: list[int] = field(default_factory=list)
    total_shard_bytes_estimated: int = 0
    actions_taken: list[str] = field(default_factory=list)
    actions_skipped: list[str] = field(default_factory=list)
    backup_path: str = ""
    double_read_marker_path: str = ""
    summary_lines: list[str] = field(default_factory=list)
    error_message: str = ""

    def to_dict(self) -> dict[str, object]:
        """转为可 JSON 序列化的 dict."""
        return asdict(self)


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------


def resolve_project_paths(
    project_id: str,
    storage_root: str | Path | None = None,
) -> tuple[Path, Path, Path]:
    """根据 project_id + storage_root 解析 3 个关键路径.

    Args:
        project_id: 项目 ID (例: "弈局谋心")
        storage_root: 存储根目录. 优先级: 显式参数 > ``NOVEL_FORGE_STORAGE_ROOT``
            环境变量 > ``./data`` (项目默认)

    Returns:
        ``(storage_root_abs, project_dir, project_memory_path)``
    """
    if storage_root is None:
        env_root = os.environ.get("NOVEL_FORGE_STORAGE_ROOT")
        storage_root = Path(env_root) if env_root else Path.cwd() / DEFAULT_STORAGE_ROOT

    storage_root_abs = Path(storage_root).expanduser().resolve()
    project_dir = storage_root_abs / project_id
    project_memory_path = project_dir / "memory" / PROJECT_MEMORY_FILENAME
    return storage_root_abs, project_dir, project_memory_path


# ---------------------------------------------------------------------------
# 扫描 (只读)
# ---------------------------------------------------------------------------


def load_project_memory(project_memory_path: Path) -> dict[str, Any]:
    """读取 ``project_memory.json`` 并返回原始 dict.

    这是纯只读操作 — 不修改任何文件. 文件不存在时返回空 dict,
    JSON 解析失败时记录错误并返回空 dict (caller 应当据此报告).
    """
    if not project_memory_path.exists():
        return {}
    try:
        text = project_memory_path.read_text(encoding="utf-8")
    except OSError as exc:
        _log.warning("Failed to read %s: %s", project_memory_path, exc)
        return {}
    try:
        result: dict[str, Any] = json.loads(text)
        return result
    except json.JSONDecodeError as exc:
        _log.warning("Failed to parse %s: %s", project_memory_path, exc)
        return {}


def _coerce_int_chapter(key: Any) -> int | None:
    """把 chapter key 强转成 int. 失败返回 None."""
    if isinstance(key, int):
        return key
    if isinstance(key, str):
        s = key.strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            # 试 float 形式 ("1.0" -> 1)
            try:
                return int(float(s))
            except ValueError:
                return None
    if isinstance(key, float):
        return int(key)
    return None


def _normalize_chapter_dict(raw: Any) -> dict[int, Any]:
    """把 ``dict[key, value]`` 中 key 强转成 int chapter; 失败的丢弃."""
    if not isinstance(raw, dict):
        return {}
    out: dict[int, Any] = {}
    for k, v in raw.items():
        n = _coerce_int_chapter(k)
        if n is not None:
            out[n] = v
    return out


def discover_chapter_shards(
    project_memory: dict[str, Any],
) -> tuple[list[int], dict[int, dict[str, Any]], dict[str, list[int]]]:
    """扫描 ``project_memory.json`` 中所有 chapter-shardable 字段.

    Args:
        project_memory: 原始 project_memory dict

    Returns:
        ``(chapter_numbers, chapter_payloads, shardable_field_index)``
        - chapter_numbers: 排序去重后的全部 chapter 编号
        - chapter_payloads: ``{chapter_N: {field_name: field_value, ...}, ...}``
        - shardable_field_index: ``{field_name: [chapter_N, ...], ...}`` 哪个
          shardable 字段为哪几章贡献了数据 (便于报告)
    """
    # 1) summary_cache: dict[chapter -> summary]
    summary_cache = _normalize_chapter_dict(project_memory.get("summary_cache", {}))

    # 2) chapter_content_hash: dict[chapter -> hash string]
    content_hash = _normalize_chapter_dict(project_memory.get("chapter_content_hash", {}))

    # 3) episodic_index (可能是 dict 包含 chapter_events / chapter_critiques 子结构)
    raw_epi = project_memory.get("episodic_index", {})
    epi_chapter_events: dict[int, Any] = {}
    epi_chapter_critiques: dict[int, Any] = {}
    if isinstance(raw_epi, dict):
        epi_chapter_events = _normalize_chapter_dict(
            raw_epi.get("chapter_events", {})
        )
        epi_chapter_critiques = _normalize_chapter_dict(
            raw_epi.get("chapter_critiques", {})
        )

    # 4) 汇总所有 chapter
    all_chapter_sets: list[set[int]] = [
        set(summary_cache.keys()),
        set(content_hash.keys()),
        set(epi_chapter_events.keys()),
        set(epi_chapter_critiques.keys()),
    ]
    chapter_numbers = sorted(set().union(*all_chapter_sets))

    # 5) 构造 per-chapter payload (只包含 *有数据* 的字段)
    chapter_payloads: dict[int, dict[str, Any]] = {}
    for ch in chapter_numbers:
        payload: dict[str, Any] = {}
        if ch in summary_cache:
            payload["summary_cache"] = summary_cache[ch]
        if ch in content_hash:
            payload["chapter_content_hash"] = content_hash[ch]
        if ch in epi_chapter_events:
            payload.setdefault("episodic_chapter_events", {})[str(ch)] = (
                epi_chapter_events[ch]
            )
        if ch in epi_chapter_critiques:
            payload.setdefault("episodic_chapter_critiques", {})[str(ch)] = (
                epi_chapter_critiques[ch]
            )
        chapter_payloads[ch] = payload

    # 6) 字段级索引 (用于报告)
    shardable_field_index: dict[str, list[int]] = {
        "summary_cache": sorted(summary_cache.keys()),
        "chapter_content_hash": sorted(content_hash.keys()),
        "episodic_chapter_events": sorted(epi_chapter_events.keys()),
        "episodic_chapter_critiques": sorted(epi_chapter_critiques.keys()),
    }

    return chapter_numbers, chapter_payloads, shardable_field_index


def compute_global_fields(
    project_memory: dict[str, Any],
    chapter_numbers: list[int],
) -> dict[str, Any]:
    """构造"留在 project_memory.json"的索引 dict.

    分类策略:
        保留全局 (不动):
            - project_id
            - last_indexed_chapter
            - summary_stats
            - episodic_index.vector_store / outline_data / critique_index
            - motif_cache / motif_tracker (Task 1.3 负责)

        shardable 字段处理:
            - summary_cache: 替换为 ``{str(ch): None}`` 索引占位
            - chapter_content_hash: 替换为 ``{str(ch): None}`` 索引占位
            - episodic_index.chapter_events / chapter_critiques: 同上

        索引新增字段:
            - ``shard_index.chapter_numbers`` : 排序后的 chapter 列表
            - ``shard_index.field_map``        : shardable field -> 哪些 chapter
            - ``shard_index.shard_format``     : "chapter_NNN.json"
            - ``shard_index.migrated_at``      : UTC ISO-8601
    """
    global_payload: dict[str, Any] = {
        "project_id": project_memory.get("project_id"),
        "last_indexed_chapter": project_memory.get("last_indexed_chapter", 0),
        "summary_stats": project_memory.get("summary_stats", {}),
    }

    # motif (Task 1.3 范围,本脚本只透传,不动)
    for motif_key in ("motif_cache", "motif_tracker"):
        if motif_key in project_memory:
            global_payload[motif_key] = project_memory[motif_key]

    # episodic_index: 保留 vector_store / outline_data / critique_index,
    # 把 chapter_events / chapter_critiques 替换为索引
    raw_epi = project_memory.get("episodic_index", {})
    if isinstance(raw_epi, dict):
        new_epi: dict[str, Any] = {}
        for k, v in raw_epi.items():
            if k in ("chapter_events", "chapter_critiques"):
                # 用 str-key 占位 dict 表达 "数据已迁移到分片"
                placeholder: dict[str, None] = {}
                if isinstance(v, dict):
                    for ch in v.keys():
                        ch_int = _coerce_int_chapter(ch)
                        placeholder[str(ch_int) if ch_int is not None else str(ch)] = None
                new_epi[k] = placeholder
            else:
                # vector_store / outline_data / critique_index 等保留
                new_epi[k] = v
        global_payload["episodic_index"] = new_epi

    # summary_cache / chapter_content_hash: 替换为索引占位
    for top_key in ("summary_cache", "chapter_content_hash"):
        if top_key in project_memory and isinstance(project_memory[top_key], dict):
            placeholder_dict: dict[str, None] = {}
            for k in project_memory[top_key].keys():
                k_int = _coerce_int_chapter(k)
                placeholder_dict[str(k_int) if k_int is not None else str(k)] = None
            global_payload[top_key] = placeholder_dict
        elif top_key in project_memory:
            # 非 dict (异常结构): 保持原值,不强行覆盖
            global_payload[top_key] = project_memory[top_key]

    # 索引元数据
    global_payload["shard_index"] = {
        "shard_format": f"{CHAPTER_SHARD_PREFIX}NNN{CHAPTER_SHARD_SUFFIX}",
        "chapter_numbers": [int(ch) for ch in chapter_numbers],
        "field_map": {
            "summary_cache": [
                int(ch) for ch in project_memory.get("summary_cache", {}).keys()
                if _coerce_int_chapter(ch) is not None
            ],
            "chapter_content_hash": [
                int(ch) for ch in project_memory.get("chapter_content_hash", {}).keys()
                if _coerce_int_chapter(ch) is not None
            ],
            "episodic_chapter_events": [
                int(ch)
                for ch in (
                    project_memory.get("episodic_index", {}).get("chapter_events", {}).keys()
                    if isinstance(project_memory.get("episodic_index"), dict)
                    else []
                )
                if _coerce_int_chapter(ch) is not None
            ],
            "episodic_chapter_critiques": [
                int(ch)
                for ch in (
                    project_memory.get("episodic_index", {}).get("chapter_critiques", {}).keys()
                    if isinstance(project_memory.get("episodic_index"), dict)
                    else []
                )
                if _coerce_int_chapter(ch) is not None
            ],
        },
        "migrated_at": datetime.now(timezone.utc).isoformat(),
        "migrator": "novel_forge.memory.migrate_to_chapter_shards",
        "config_format": os.environ.get("NOVEL_FORGE_MEMORY_FORMAT", "sharded"),
    }

    return global_payload


# ---------------------------------------------------------------------------
# 文件名 / 备份 / 标记
# ---------------------------------------------------------------------------


def chapter_shard_filename(chapter: int) -> str:
    """``chapter_001.json`` 风格, 三位补零."""
    return f"{CHAPTER_SHARD_PREFIX}{int(chapter):03d}{CHAPTER_SHARD_SUFFIX}"


def chapter_shard_path(memory_dir: Path, chapter: int) -> Path:
    """单章分片绝对路径."""
    return memory_dir / chapter_shard_filename(chapter)


def _estimate_payload_bytes(payload: Any) -> int:
    """预估 payload 序列化为 JSON 后的字节数 (UTF-8).

    用来在 dry-run 报告里告诉用户"分片后会占多少空间", 无需实际写盘.
    """
    try:
        return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def perform_backup(project_memory_path: Path) -> Path:
    """把 ``project_memory.json`` 复制到 ``.bak/<UTC-ISO-timestamp>/``.

    Args:
        project_memory_path: project_memory.json 绝对路径

    Returns:
        备份目标绝对路径

    Raises:
        FileNotFoundError: project_memory.json 不存在
        FileExistsError: 同时间戳的备份已存在 (微秒概率, 但显式保护)
    """
    if not project_memory_path.exists():
        raise FileNotFoundError(f"project_memory.json not found: {project_memory_path}")

    memory_dir = project_memory_path.parent
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_root = memory_dir / f"{PROJECT_MEMORY_FILENAME}{BACKUP_DIR_SUFFIX}"
    backup_target = backup_root / timestamp
    if backup_target.exists():
        raise FileExistsError(f"backup target already exists: {backup_target}")
    backup_target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(project_memory_path, backup_target / PROJECT_MEMORY_FILENAME)
    return backup_target


def write_double_read_marker(
    project_dir: Path,
    *,
    project_memory_path: Path,
    chapter_numbers: list[int],
) -> Path:
    """在 ``<project_dir>`` 下写一个 ``.chapter_shards_double_read.json`` 标记.

    该文件表达 "迁移期间双读" 的意图 — 任何未来 ``load_from_disk`` 的
    读取逻辑 (或外层 wrapper) 可通过
    ``Path(<project_dir>).joinpath('.chapter_shards_double_read.json').exists()``
    来判断是否启用 fallback.

    本工具**不**修改产品代码,只生成意图标记.
    """
    marker = project_dir / DOUBLE_READ_MARKER_NAME
    payload = {
        "enabled": True,
        "reason": "chapter_shards_migration",
        "project_memory_path": str(project_memory_path),
        "chapter_numbers": [int(ch) for ch in chapter_numbers],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "instruction": (
            "When this marker exists, read code SHOULD fall back from "
            "project_memory.json to chapter_NNN.json shards on lookup miss. "
            "Delete this file to disable."
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
) -> MigrationReport:
    """执行一次受控 chapter-shard 迁移并返回结构化报告.

    参数语义:
        - ``dry_run=True`` 时, backup / fallback 全部只报告不执行
        - ``backup=True``: 把 ``project_memory.json`` 复制到 ``.bak/<timestamp>/``
        - ``fallback_double_read=True``: 写 ``.chapter_shards_double_read.json`` 标记
        - 任意参数都默认是 ``False`` — 默认行为 = 纯只读报告

    Returns:
        :class:`MigrationReport` 实例
    """
    storage_root_abs, project_dir, project_memory_path = resolve_project_paths(
        project_id, storage_root
    )
    memory_dir = project_dir / "memory"

    exists = project_memory_path.exists()
    total_bytes = project_memory_path.stat().st_size if exists else 0

    report = MigrationReport(
        project_id=project_id,
        storage_root=str(storage_root_abs),
        project_memory_path=str(project_memory_path),
        exists=exists,
        total_bytes=total_bytes,
    )

    if not exists:
        report.error_message = (
            f"project_memory.json not found at {project_memory_path}"
        )
        _log.warning(report.error_message)
        report.actions_skipped.append("scan (source file missing)")
        report.summary_lines = _build_summary_lines(report, dry_run_active=dry_run)
        return report

    # ---- 扫描 (只读) ----
    project_memory = load_project_memory(project_memory_path)
    chapter_numbers, chapter_payloads, shardable_field_index = discover_chapter_shards(
        project_memory
    )

    report.chapter_numbers = chapter_numbers
    report.chapter_count = len(chapter_numbers)
    report.shardable_fields = list(shardable_field_index.keys())
    report.global_fields = [
        "project_id",
        "last_indexed_chapter",
        "summary_stats",
        "motif_cache",
        "motif_tracker",
        "episodic_index.vector_store",
        "episodic_index.outline_data",
        "episodic_index.critique_index",
    ]
    # 估算分片总字节
    est_total = 0
    for ch in chapter_numbers:
        est_total += _estimate_payload_bytes(chapter_payloads[ch])
    report.total_shard_bytes_estimated = est_total

    _log.info(
        "Scan complete: %d chapters to shard, %d bytes estimated for shards",
        report.chapter_count,
        est_total,
    )
    if report.chapter_count > 0:
        first_ch = chapter_numbers[0]
        last_ch = chapter_numbers[-1]
        _log.info(
            "Will create %d chapter shard files (chapters %d-%d)",
            report.chapter_count,
            first_ch,
            last_ch,
        )
    else:
        _log.info("No chapter-shardable data found — nothing to migrate")

    # ---- dry-run 短路 ----
    if dry_run:
        _log.info("DRY-RUN, no changes")
        report.actions_skipped.extend(
            ["backup", "fallback_double_read", "shard_write", "index_write"]
        )
        report.summary_lines = _build_summary_lines(report, dry_run_active=True)
        return report

    # ---- 实际写操作 (从这开始才允许触碰磁盘) ----

    if backup:
        try:
            backup_path = perform_backup(project_memory_path)
            report.backup_path = str(backup_path)
            report.actions_taken.append(f"backup -> {backup_path}")
            _log.info("Backup created: %s", backup_path)
        except FileNotFoundError as exc:
            _log.warning("Backup skipped: %s", exc)
            report.actions_skipped.append("backup (source file missing)")

    # 写每个 chapter_NNN.json 分片
    if report.chapter_count > 0:
        memory_dir.mkdir(parents=True, exist_ok=True)
        written_files: list[str] = []
        for ch in chapter_numbers:
            shard = chapter_shard_path(memory_dir, ch)
            shard.write_text(
                json.dumps(chapter_payloads[ch], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            written_files.append(str(shard))
        report.actions_taken.append(
            f"shard_write -> {len(written_files)} files (chapters {chapter_numbers[0]}-{chapter_numbers[-1]})"
        )
        _log.info("Wrote %d chapter shard files", len(written_files))

        # 写回 project_memory.json (索引 + 全局状态)
        global_payload = compute_global_fields(project_memory, chapter_numbers)
        project_memory_path.write_text(
            json.dumps(global_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        report.actions_taken.append("index_write -> project_memory.json")
        _log.info("Wrote sharded project_memory.json index at %s", project_memory_path)
    else:
        report.actions_skipped.append("shard_write (no chapters)")

    if fallback_double_read:
        marker = write_double_read_marker(
            project_dir,
            project_memory_path=project_memory_path,
            chapter_numbers=chapter_numbers,
        )
        report.double_read_marker_path = str(marker)
        report.actions_taken.append(f"fallback_double_read -> {marker}")
        _log.info("Double-read marker written: %s", marker)
        _log.info(
            "NOTE: production code MUST check this marker to honor fallback"
        )

    report.summary_lines = _build_summary_lines(report, dry_run_active=False)
    return report


def _build_summary_lines(
    report: MigrationReport, *, dry_run_active: bool
) -> list[str]:
    """构造给人类读的多行 summary, 末尾由 CLI 打印."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("Chapter Shard Migration Report (Task 11 skeleton)")
    lines.append("=" * 72)
    lines.append(f"Project ID              : {report.project_id}")
    lines.append(f"Storage root            : {report.storage_root}")
    lines.append(f"project_memory.json     : {report.project_memory_path}")
    lines.append(f"  exists                : {report.exists}")
    lines.append(f"  total bytes           : {report.total_bytes}")
    lines.append(f"  error                 : {report.error_message or '(none)'}")
    lines.append("-" * 72)
    lines.append(
        f"Chapter count           : {report.chapter_count}"
    )
    if report.chapter_numbers:
        lines.append(
            f"  range                 : {report.chapter_numbers[0]}-{report.chapter_numbers[-1]}"
        )
        # 打印最多前 20 个 chapter 编号,避免刷屏
        preview = report.chapter_numbers[:20]
        lines.append(
            f"  numbers (first 20)   : {preview}"
            + (
                " ..."
                if len(report.chapter_numbers) > 20
                else ""
            )
        )
    else:
        lines.append("  numbers               : (none)")
    lines.append(
        f"Estimated shard bytes   : {report.total_shard_bytes_estimated}"
    )
    lines.append("-" * 72)
    lines.append("Shardable fields (按 chapter 拆):")
    if report.shardable_fields:
        for f in report.shardable_fields:
            lines.append(f"  - {f}")
    else:
        lines.append("  (none)")
    lines.append("Global fields (保留在 project_memory.json):")
    if report.global_fields:
        for f in report.global_fields:
            lines.append(f"  - {f}")
    else:
        lines.append("  (none)")
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
        lines.append(f"Backup target           : {report.backup_path}")
    if report.double_read_marker_path:
        lines.append(f"Double-read marker      : {report.double_read_marker_path}")
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

    Flags 命名与 plan/task 严格对齐, 便于 QA 脚本调用:
        --project-id (required)
        --dry-run
        --backup
        --fallback-double-read
        --storage-root (可选, 默认 $NOVEL_FORGE_STORAGE_ROOT 或 ./data)
    """
    parser = argparse.ArgumentParser(
        prog="novel_forge.memory.migrate_to_chapter_shards",
        description=(
            "受控 Chapter Shard 迁移工具 (Wave 3 Task 11 骨架). "
            "默认行为: 只读 + 报告, 不动文件. "
            "不修改 integration.py, 真实 sharded 加载逻辑由 Plan 后续任务处理."
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
        help="不写盘, 只报告将做什么 (默认行为, 显式声明更清晰)",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="把 project_memory.json 复制到 .bak/<UTC-ISO-timestamp>/",
    )
    parser.add_argument(
        "--fallback-double-read",
        action="store_true",
        help=(
            "在 <project>/ 下写 .chapter_shards_double_read.json 标记 — "
            "产品代码可在读取时检测该标记以启用旧格式 fallback. "
            "本工具本身不修改产品代码."
        ),
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

    # source file missing 时, exit code = 2 便于脚本区分
    if report.error_message:
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
