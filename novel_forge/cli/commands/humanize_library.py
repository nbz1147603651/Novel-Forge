"""novel-forge humanize-library command group.

拟人化库管理命令 — list / add / remove / enable / disable / find-duplicates
/ merge / export / import / stats
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from novel_forge.cli.error_handling import handle_cli_errors
from novel_forge.core.schemas.humanize_library import HumanizeLibraryEntry
from novel_forge.memory.humanize_library_store import HumanizeLibrary

console = Console()

app = typer.Typer(
    name="humanize-library",
    help="拟人化库管理命令",
    no_args_is_help=True,
)


def _open_library(path: str | None = None) -> HumanizeLibrary:
    """Open library at default path or a custom path."""
    if path:
        import os

        os.environ["NOVEL_FORGE_HUMANIZE_LIBRARY_PATH"] = path
    return HumanizeLibrary.from_default_path()


@app.command("list")
@handle_cli_errors
def list_cmd(
    limit: int = typer.Option(20, "--limit", "-n", help="最多显示条目数"),
    source: Optional[str] = typer.Option(
        None, "--source", help="过滤来源 (builtin/user/imported)"
    ),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON 格式"),
) -> None:
    """列出拟人化库中的所有条目。"""
    lib = _open_library()
    try:
        entries = lib.list_all()
        if source:
            entries = [e for e in entries if e.source == source]
        entries = entries[:limit]

        if json_output:
            data = []
            for e in entries:
                dumped = e.model_dump(
                    mode="json", exclude={"schema_version", "created_at"}
                )
                data.append(dumped)
            typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            table = Table(title=f"拟人化库条目 (共 {len(entries)} 条)")
            table.add_column("ID", style="cyan", no_wrap=True)
            table.add_column("名称", style="white")
            table.add_column("来源", style="yellow")
            table.add_column("严重度", style="magenta")
            table.add_column("命中数", justify="right")
            for e in entries:
                table.add_row(
                    e.pattern_id,
                    e.pattern_name,
                    e.source,
                    e.severity,
                    str(e.hit_count),
                )
            console.print(table)
    finally:
        lib.close()


@app.command("add")
@handle_cli_errors
def add_cmd(
    name: str = typer.Option(..., "--name", "-n", help="模式名称"),
    severity: str = typer.Option(
        "medium", "--severity", "-s", help="critical/high/medium/low"
    ),
    example: str = typer.Option("", "--example", "-e", help="示例短语"),
    category: str = typer.Option("用户自定义", "--category", "-c", help="分类"),
    keywords: str = typer.Option("", "--keywords", "-k", help="逗号分隔关键词"),
    project_id: Optional[str] = typer.Option(
        None, "--project-id", "-p", help="限定项目 ID (None=全局)"
    ),
) -> None:
    """添加新条目到拟人化库。"""
    lib = _open_library()
    try:
        hex_id = uuid.uuid4().hex[:8]
        pattern_id = f"lib_user_{hex_id}"
        kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
        entry = HumanizeLibraryEntry(
            pattern_id=pattern_id,
            pattern_name=name,
            category=category,
            severity=severity,  # type: ignore[arg-type]
            example_phrases=[example] if example else [],
            keywords=kw_list,
            detection_method="regex",
            source="user",
            project_id=project_id,
        )
        lib.add(entry)
        typer.echo(f"✓ 已添加: {pattern_id} — {name}")
    finally:
        lib.close()


@app.command("remove")
@handle_cli_errors
def remove_cmd(
    pattern_id: str = typer.Argument(..., help="模式 ID"),
) -> None:
    """从拟人化库中删除条目 (builtin 条目不可删除)。"""
    lib = _open_library()
    try:
        if lib.remove(pattern_id):
            typer.echo(f"✓ 已删除: {pattern_id}")
        else:
            typer.echo(f"✗ 未找到: {pattern_id}", err=True)
            sys.exit(1)
    finally:
        lib.close()


@app.command("enable")
@handle_cli_errors
def enable_cmd(
    pattern_id: str = typer.Argument(..., help="模式 ID"),
) -> None:
    """启用模式。"""
    lib = _open_library()
    try:
        lib.enable(pattern_id)
        typer.echo(f"✓ 已启用: {pattern_id}")
    finally:
        lib.close()


@app.command("disable")
@handle_cli_errors
def disable_cmd(
    pattern_id: str = typer.Argument(..., help="模式 ID"),
) -> None:
    """禁用模式。"""
    lib = _open_library()
    try:
        lib.disable(pattern_id)
        typer.echo(f"✓ 已禁用: {pattern_id}")
    finally:
        lib.close()


@app.command("find-duplicates")
@handle_cli_errors
def find_duplicates_cmd(
    threshold: float = typer.Option(
        0.85, "--threshold", "-t", help="相似度阈值"
    ),
) -> None:
    """查找库中的重复条目。"""
    lib = _open_library()
    try:
        pairs = lib.find_duplicates()
        filtered = [(a, b, sim) for a, b, sim in pairs if sim >= threshold]
        if not filtered:
            typer.echo("未发现重复条目。")
        else:
            for a, b, sim in filtered:
                typer.echo(
                    f"  {a.pattern_id} ↔ {b.pattern_id} (similarity={sim:.3f})"
                )
            typer.echo(f"共 {len(filtered)} 对。")
    finally:
        lib.close()


@app.command("merge")
@handle_cli_errors
def merge_cmd(
    target: str = typer.Argument(..., help="目标 ID (保留)"),
    source_id: str = typer.Argument(..., help="源 ID (合并后删除)"),
) -> None:
    """合并两个条目 (保留 target, 删除 source)。"""
    lib = _open_library()
    try:
        target_entry = lib.get(target)
        source_entry = lib.get(source_id)
        if not target_entry or not source_entry:
            typer.echo("✗ 条目不存在", err=True)
            sys.exit(1)

        # Merge: combine keywords, examples, sum hit_count
        merged_kw = list(
            set((target_entry.keywords or []) + (source_entry.keywords or []))
        )
        merged_examples = list(
            set(
                (target_entry.example_phrases or [])
                + (source_entry.example_phrases or [])
            )
        )
        new_hit_count = target_entry.hit_count + source_entry.hit_count

        lib.update(
            target,
            keywords=merged_kw,
            example_phrases=merged_examples,
            hit_count=new_hit_count,
        )
        lib.remove(source_id)
        typer.echo(f"✓ 已合并: {source_id} → {target}")
    finally:
        lib.close()


@app.command("export")
@handle_cli_errors
def export_cmd(
    output: Path = typer.Argument(..., help="输出 tar.gz 路径"),  # noqa: B008
) -> None:
    """导出库到 tar.gz 文件。"""
    lib = _open_library()
    try:
        data = lib.export()
        output.write_bytes(data)
        typer.echo(
            f"✓ 已导出到: {output} ({len(data):,} bytes)"
        )
    finally:
        lib.close()


@app.command("import")
@handle_cli_errors
def import_cmd(
    archive: Path = typer.Argument(..., help="输入 tar.gz 路径"),  # noqa: B008
    strategy: str = typer.Option(
        "skip", "--strategy", help="skip/overwrite/merge"
    ),
) -> None:
    """从 tar.gz 文件导入库。"""
    lib = _open_library()
    try:
        data = archive.read_bytes()
        report = lib.import_archive(data, merge_strategy=strategy)  # type: ignore[arg-type]
        typer.echo("✓ 导入完成:")
        stats_table = Table(show_header=False)
        stats_table.add_column("指标", style="cyan")
        stats_table.add_column("值", justify="right")
        for key, val in [
            ("导入", report.imported),
            ("跳过", report.skipped),
            ("覆盖", report.overwritten),
            ("合并", report.merged),
            ("向量过期", report.vector_stale_count),
        ]:
            stats_table.add_row(key, str(val))
        console.print(stats_table)
    finally:
        lib.close()


@app.command("stats")
@handle_cli_errors
def stats_cmd() -> None:
    """显示库的统计信息。"""
    lib = _open_library()
    try:
        stats = lib.stats()
        table = Table(title="拟人化库统计")
        table.add_column("指标", style="cyan")
        table.add_column("值", justify="right")
        for key, val in [
            ("总数", stats.total),
            ("已启用", stats.enabled),
            ("正则检测", stats.regex_count),
            ("LLM 检测", stats.llm_only_count),
            ("用户定义", stats.user_count),
            ("导入", stats.imported_count),
            ("向量过期", stats.stale_vector_count),
            ("最后更新", stats.last_updated_at or "never"),
        ]:
            table.add_row(key, str(val))
        console.print(table)
    finally:
        lib.close()
