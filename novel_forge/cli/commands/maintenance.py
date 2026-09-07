"""Maintenance and review CLI commands."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from novel_forge.cli.chapter_helpers import _detect_latest_creative_report_chapter
from novel_forge.cli.runtime_config import (
    resolve_bool_option,
    resolve_choice_option,
    resolve_float_option,
    resolve_int_option,
    resolve_str_option,
)
from novel_forge.cli.runtime_helpers import (
    _build_router,
    _create_runtime_services,
    _get_storage,
    _load_cli_config,
    _resolve_config_value,
)
from novel_forge.common.plot_guard import (
    _apply_outline_adjustment,
    _rollback_and_cleanup_chapter,
)
from novel_forge.core.config import get_settings
from novel_forge.core.constants import TaskType
from novel_forge.core.parsing.parse_utils import safe_parse_json
from novel_forge.core.schemas.canon import CreativeReport
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.story_kernel.store import StoryKernelStore
from novel_forge.workspace.contracts import (
    BookConsistencyRequest,
    BookEditorialAuditRequest,
    GlobalRepairQueueRequest,
    SyncChapterContractsRequest,
)

console = Console()


def _build_character_profile(
    name: str,
    role: str,
    description: str,
    chapter_num: int,
    relationships: dict[str, Any] | None = None,
    enriched: dict[str, Any] | None = None,
    suffix: str = "",
) -> Any:
    """Create a CharacterProfile from sync-bible data, with optional LLM-enriched fields."""
    from novel_forge.core.schemas.bible import CharacterProfile

    data: dict[str, Any] = {
        "name": name,
        "role": role,
        "appearance": (enriched or {}).get("appearance", ""),
        "personality": (enriched or {}).get("personality", ""),
        "backstory": (enriched or {}).get("backstory", description),
        "arc": (enriched or {}).get("arc", ""),
        "relationships": relationships or {},
        "notes": f"由 sync-bible {suffix}添加（第{chapter_num}章）",
    }
    return CharacterProfile.model_validate(data)


# ─── rollback-chapter ───────────────────────────────────────────────


def rollback_chapter(
    project_id: str = typer.Option(..., help="Project ID"),
    to_chapter: int = typer.Option(..., help="Target chapter to rollback to (0 = initial state)"),
) -> None:
    """Rollback canon and chapters to a previous state.

    This allows you to regenerate chapters from a specific point.
    Example: rollback to chapter 1 to regenerate chapter 2 and onwards.
    """
    storage = _get_storage()
    layout = ProjectLayout(storage.project_path(project_id))
    canon_store = StoryKernelStore(layout.root)

    if not canon_store.exists():
        console.print(f"[red]❌ Error:[/red] Canon not found for project '{project_id}'")
        console.print("[dim]Run init-long first to initialize the project.[/dim]")
        raise typer.Exit(1)

    current = canon_store.load()

    if to_chapter < 0:
        console.print("[red]❌ Error:[/red] Target chapter must be >= 0")
        raise typer.Exit(1)

    if to_chapter >= current.current_chapter:
        console.print(
            f"[yellow]⚠️  Warning:[/yellow] Canon is already at chapter {current.current_chapter}"
        )
        console.print(
            f"[dim]Cannot rollback to chapter {to_chapter} (must be < {current.current_chapter})[/dim]"
        )
        raise typer.Exit(1)

    affected_chapters = list(range(to_chapter + 1, current.current_chapter + 1))

    console.print("\n[bold yellow]⚠️  Canon 回滚操作[/bold yellow]")
    console.print(f"[dim]项目: {project_id}[/dim]")
    console.print(f"[dim]当前章节: {current.current_chapter} → 目标章节: {to_chapter}[/dim]\n")

    console.print("[yellow]以下章节的 canon 数据将被回滚：[/yellow]")
    for ch in affected_chapters:
        console.print(f"  • 第 {ch} 章")
    console.print(
        "\n[dim]章节文件（chapters/chapter_*.md）不会被删除，但重新生成时会被覆盖[/dim]\n"
    )

    if not typer.confirm("确认执行回滚？", default=False):
        console.print("[dim]已取消[/dim]")
        raise typer.Exit(0)

    try:
        canon_store.rollback_to(to_chapter)

        console.print("\n[bold green]✅ 回滚成功！[/bold green]")
        console.print(f"[dim]Canon 已回滚到第 {to_chapter} 章状态[/dim]\n")

        next_ch = to_chapter + 1
        console.print("[cyan]后续操作：[/cyan]")
        console.print(f"  重新生成第 {next_ch} 章：")
        console.print(
            f"  [bold]novel-forge run-chapter --project-id {project_id} --chapter {next_ch}[/bold]\n"
        )

    except ValueError as exc:
        console.print(f"[red]❌ Error:[/red] {exc}")
        available = canon_store.list_snapshots()
        if available:
            console.print(f"\n[dim]可用的快照章节: {available}[/dim]")
        raise typer.Exit(1) from exc


# ─── restore-version ────────────────────────────────────────────────


def restore_version(
    project_id: str = typer.Option(..., help="Project ID"),
    chapter: int = typer.Option(..., help="章节号"),
    version: Optional[int] = typer.Option(None, help="目标版本号（不指定则列出可用版本）"),
) -> None:
    """将章节恢复到指定草稿版本。

    先用 --chapter N 查看可用版本列表，再用 --version V 恢复。
    恢复前会自动备份当前发布版本。
    """
    from novel_forge.core.utils.version_diff import (
        list_draft_versions,
        restore_chapter_version,
    )

    storage = _get_storage()
    layout = ProjectLayout(storage.project_path(project_id))

    versions = list_draft_versions(layout.drafts_dir, chapter)
    if not versions:
        console.print(f"[red]❌ 第 {chapter} 章没有可用的草稿版本。[/red]")
        raise typer.Exit(1)

    if version is None:
        # 列出可用版本
        table = Table(title=f"第 {chapter} 章 — 可用版本")
        table.add_column("版本号", style="cyan", justify="right")
        table.add_column("标签", style="green")
        table.add_column("字数", style="yellow", justify="right")
        for v in versions:
            table.add_row(str(v.version), v.label, str(v.word_count))
        console.print(table)
        console.print(
            f"\n[dim]使用 --version <版本号> 恢复到指定版本。\n"
            f"例如: novel-forge restore-version --project-id {project_id} "
            f"--chapter {chapter} --version {versions[0].version}[/dim]"
        )
        return

    # 确认操作
    version_map = {v.version: v for v in versions}
    if version not in version_map:
        console.print(f"[red]❌ 版本 {version} 不存在。[/red]")
        available = [str(v.version) for v in versions]
        console.print(f"[dim]可用版本: {', '.join(available)}[/dim]")
        raise typer.Exit(1)

    target = version_map[version]
    console.print("\n[bold yellow]⚠️  版本恢复操作[/bold yellow]")
    console.print(f"[dim]项目: {project_id} / 第 {chapter} 章[/dim]")
    console.print(f"[dim]恢复到: v{version} ({target.label}, {target.word_count} 字)[/dim]")
    console.print("[dim]当前发布版本将备份到 v_pre_restore_backup.md[/dim]\n")

    if not typer.confirm("确认执行恢复？", default=False):
        console.print("[dim]已取消[/dim]")
        raise typer.Exit(0)

    try:
        restored = restore_chapter_version(
            layout.drafts_dir,
            layout.chapters_dir,
            chapter,
            version,
        )
        console.print("\n[bold green]✅ 恢复成功！[/bold green]")
        console.print(f"[dim]第 {chapter} 章已恢复到 v{restored.version} ({restored.label})[/dim]")
    except ValueError as exc:
        console.print(f"[red]❌ {exc}[/red]")
        raise typer.Exit(1) from exc


# ─── ab-test ────────────────────────────────────────────────────────


def ab_test(
    mock: bool = typer.Option(False, "--mock", help="Force mock mode (for testing/debugging)"),
    theme: str = typer.Option("一场暴风雨中的相遇", help="Story theme"),
) -> None:
    """Run an A/B test comparing two model configurations."""

    async def _run() -> None:
        from novel_forge.eval.ab_test import ABTestRunner
        from novel_forge.eval.evaluator import DraftEvaluator

        settings = get_settings()
        router = _build_router(mock=mock)
        builder = PromptBuilder()
        evaluator = DraftEvaluator(router, builder, settings=settings)
        ab_runner = ABTestRunner(router, evaluator)

        console.print(f"\n[bold cyan]🔬 A/B 测试:[/bold cyan] {theme}")
        console.print("[dim]  • 比较两个模型配置的生成质量[/dim]\n")

        request = builder.build(
            TaskType.DRAFT,
            {
                "spec": {
                    "genre": "literary",
                    "tone": "lyrical",
                    "length_target": 1000,
                    "language": "zh",
                },
                "beats": {
                    "beats": [
                        {
                            "sequence": 1,
                            "summary": theme,
                            "tension_level": 5,
                            "characters_involved": [],
                            "setting": "",
                        }
                    ]
                },
            },
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(complete_style="green", finished_style="bold green"),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]运行 A/B 测试...", total=4)

            progress.update(task, advance=1, description="[yellow]生成模型 A 输出...")
            progress.update(task, advance=1, description="[yellow]生成模型 B 输出...")

            result = await ab_runner.run(
                request,
                model_a="mock-model-a",
                model_b="mock-model-b",
            )

            progress.update(task, advance=2, completed=4, description="[green]✅ A/B 测试完成")

        table = Table(title="A/B Test Results")
        table.add_column("Metric", style="cyan")
        table.add_column("Model A", style="green")
        table.add_column("Model B", style="yellow")

        table.add_row("Model", result.response_a.model_id, result.response_b.model_id)
        table.add_row(
            "Score",
            f"{result.eval_a.overall_score}",
            f"{result.eval_b.overall_score}",
        )
        table.add_row(
            "Tokens",
            str(result.response_a.tokens),
            str(result.response_b.tokens),
        )
        table.add_row("Winner", result.winner, "")
        table.add_row("Score Delta", str(result.score_delta), "")

        console.print(table)

    asyncio.run(_run())


# ─── sync-bible ─────────────────────────────────────────────────────


def sync_bible(
    project_id: Optional[str] = typer.Option(None, "--project-id", help="项目ID"),
    chapter: Optional[int] = typer.Option(None, "--chapter", help="从哪一章的创作报告同步"),
    smart: Optional[bool] = typer.Option(
        None, "--smart/--no-smart", help="使用LLM智能调整大纲（额外API调用）"
    ),
    no_outline: Optional[bool] = typer.Option(
        None, "--no-outline/--outline", help="跳过大纲调整，仅同步角色/地点"
    ),
    auto_yes: Optional[bool] = typer.Option(
        None, "-y", "--auto-yes/--interactive", help="自动接受所有建议（非交互模式）"
    ),
    mock: Optional[bool] = typer.Option(
        None, "--mock/--no-mock", help="Force mock mode (for testing/debugging)"
    ),
    config: Optional[str] = typer.Option(None, "--config", help="运行参数配置文件（JSON）"),
) -> None:
    """交互式同步创作报告到 Bible/Outline"""
    from rich.prompt import Confirm, Prompt

    from novel_forge.core.schemas.bible import CharacterBible, StoryBible

    cfg, cfg_path = _load_cli_config(config)
    resolved_project_id = _resolve_config_value(
        resolve_str_option,
        project_id,
        cfg,
        section="sync_bible",
        key="project_id",
        default="",
    )
    if not resolved_project_id:
        raise typer.BadParameter(
            "project_id 未提供；请使用 --project-id 或在配置文件 sync_bible.project_id 设置"
        )
    requested_chapter = _resolve_config_value(
        resolve_int_option,
        chapter,
        cfg,
        section="sync_bible",
        key="chapter",
        default=0,
        minimum=0,
    )
    chapter_inferred = requested_chapter == 0
    if chapter_inferred:
        inferred_layout = ProjectLayout(_get_storage().project_path(resolved_project_id))
        latest_report_chapter = _detect_latest_creative_report_chapter(inferred_layout)
        if latest_report_chapter is None:
            raise typer.BadParameter(
                "未指定 --chapter 且未找到 creative report；请先生成章节或显式传入 --chapter",
                param_hint="--chapter",
            )
        resolved_chapter_num = latest_report_chapter
    else:
        resolved_chapter_num = requested_chapter
    resolved_smart = _resolve_config_value(
        resolve_bool_option,
        smart,
        cfg,
        section="sync_bible",
        key="smart",
        default=False,
    )
    resolved_no_outline = _resolve_config_value(
        resolve_bool_option,
        no_outline,
        cfg,
        section="sync_bible",
        key="no_outline",
        default=False,
    )
    resolved_auto_yes = _resolve_config_value(
        resolve_bool_option,
        auto_yes,
        cfg,
        section="sync_bible",
        key="auto_yes",
        default=False,
    )
    resolved_mock = _resolve_config_value(
        resolve_bool_option,
        mock,
        cfg,
        section="sync_bible",
        key="mock",
        default=False,
    )

    async def _sync() -> None:
        if cfg_path is not None:
            console.print(f"[dim]📁 使用运行配置: {cfg_path}[/dim]")
        if chapter_inferred:
            console.print(
                f"[dim]🧭 未显式指定章节，自动选择最新报告: Chapter {resolved_chapter_num}[/dim]"
            )
        settings = get_settings()
        storage = _get_storage()
        router = _build_router(mock=resolved_mock)
        builder = PromptBuilder()
        layout = ProjectLayout(storage.project_path(resolved_project_id))

        report_path = layout.creative_report_path(resolved_chapter_num)
        if not storage.exists(report_path):
            console.print(f"[red]错误：创作报告不存在 {report_path}[/red]")
            raise typer.Exit(1)

        report_data = storage.load_json(report_path)
        report = CreativeReport.model_validate(report_data)

        console.print(
            f"\n[bold cyan]🔍 分析 Chapter {resolved_chapter_num} 的创作报告...[/bold cyan]\n"
        )

        # ═══════════════════════════════════════
        # 1. 同步角色
        # ═══════════════════════════════════════
        if report.new_characters:
            console.print("[bold yellow]━━━ 🆕 新增角色 ━━━[/bold yellow]\n")

            char_bible_data = storage.load_json(layout.characters_path)
            char_bible = CharacterBible.model_validate(char_bible_data)
            existing_names = {c.name for c in char_bible.characters}

            added_count = 0
            added_profiles: list[Any] = []  # 追踪本次新增的 CharacterProfile，用于后续 canon 同步
            for new_char in report.new_characters:
                if new_char.name in existing_names:
                    console.print(f"  [dim]跳过已存在角色：{new_char.name}[/dim]\n")
                    continue

                icon = "⭐" if new_char.should_add_to_bible else "  "
                console.print(f"{icon} [bold]{new_char.name}[/bold] ({new_char.role_in_story})")
                if new_char.description:
                    console.print(f"   {new_char.description}")
                if new_char.relationship_to_existing:
                    rel_str = ", ".join(
                        f"{k}→{v}" for k, v in new_char.relationship_to_existing.items()
                    )
                    console.print(f"   [dim]关系: {rel_str}[/dim]")
                if new_char.should_add_to_bible:
                    console.print("   [green]💡 系统建议：应该加入角色设定[/green]")

                default_yes = resolved_auto_yes or new_char.should_add_to_bible
                should_add = resolved_auto_yes or Confirm.ask(
                    "\n是否添加到 character_bible.json？",
                    default=default_yes,
                )

                if should_add:
                    from novel_forge.core.schemas.bible import CharacterProfile

                    new_profile: CharacterProfile
                    if resolved_smart:
                        console.print("   [yellow]🤖 正在调用 LLM 丰富角色信息...[/yellow]")

                        chapter_path = layout.chapter_path(resolved_chapter_num)
                        if storage.exists(chapter_path):
                            chapter_text = storage.load_text(chapter_path)
                        else:
                            draft_dir = layout.chapter_draft_dir(resolved_chapter_num)
                            if draft_dir.exists():
                                draft_files = sorted(draft_dir.glob("v*_*.md"))
                                chapter_text = (
                                    storage.load_text(draft_files[-1]) if draft_files else ""
                                )
                            else:
                                chapter_text = ""

                        if chapter_text:
                            try:
                                enrich_request = builder.build(
                                    TaskType.ENRICH_CHARACTER,
                                    {
                                        "character_name": new_char.name,
                                        "role": new_char.role_in_story,
                                        "chapter_number": resolved_chapter_num,
                                        "relationships": new_char.relationship_to_existing,
                                        "chapter_text": chapter_text[:4000],
                                    },
                                    max_tokens=calculate_route_aware_max_tokens(
                                        router,
                                        TaskType.ENRICH_CHARACTER,
                                        max(1800, min(len(chapter_text[:4000]) // 2, 3600)),
                                        prompt_overhead=2200,
                                        min_tokens=2048,
                                    ),
                                    temperature=settings.temp_enrich_character,
                                )
                                enrich_response = await router.route(enrich_request)
                                enriched_data = safe_parse_json(enrich_response.content)

                                new_profile = _build_character_profile(
                                    new_char.name,
                                    new_char.role_in_story,
                                    new_char.description,
                                    resolved_chapter_num,
                                    new_char.relationship_to_existing,
                                    enriched=enriched_data,
                                    suffix="智能",
                                )
                                console.print("   [green]✅ 已添加（智能丰富）[/green]\n")
                            except Exception as e:
                                console.print("   [yellow]⚠️ LLM 丰富失败，使用基本信息[/yellow]")
                                console.print(f"   [dim]错误: {e!s}[/dim]")
                                new_profile = _build_character_profile(
                                    new_char.name,
                                    new_char.role_in_story,
                                    new_char.description,
                                    resolved_chapter_num,
                                    new_char.relationship_to_existing,
                                )
                                console.print("   [green]✅ 已添加（基本模式）[/green]\n")
                        else:
                            new_profile = _build_character_profile(
                                new_char.name,
                                new_char.role_in_story,
                                new_char.description,
                                resolved_chapter_num,
                                new_char.relationship_to_existing,
                            )
                            console.print("   [green]✅ 已添加[/green]\n")
                    else:
                        new_profile = _build_character_profile(
                            new_char.name,
                            new_char.role_in_story,
                            new_char.description,
                            resolved_chapter_num,
                            new_char.relationship_to_existing,
                        )
                        console.print("   [green]✅ 已添加[/green]\n")

                    char_bible.characters.append(new_profile)
                    added_profiles.append(new_profile)
                    added_count += 1
                else:
                    console.print("   [dim]⏭️  跳过[/dim]\n")

            if added_count > 0:
                storage.save_json(layout.characters_path, char_bible.model_dump(mode="json"))
                console.print(
                    f"[green]✅ 已添加 {added_count} 个角色到 character_bible.json[/green]\n"
                )
                # 同步新角色的 CharacterState 到 canon_current.json
                # 确保手动添加的角色与自动建档角色享有同等状态追踪起点
                _canon_store = StoryKernelStore(layout.root)
                if _canon_store.exists():
                    try:
                        from novel_forge.core.schemas.story_state import (
                            CharacterState as _CharState,
                        )

                        _canon_state = _canon_store.load()
                        _seeded: list[str] = []
                        for _prof in added_profiles:
                            if (
                                _prof.name
                                and _canon_state.get_character_by_name(_prof.name) is None
                            ):
                                _canon_state.set_character(
                                    _prof.name,
                                    _CharState(
                                        name=_prof.name,
                                        gender=_prof.gender or "",
                                    ),
                                )
                                _seeded.append(_prof.name)
                        if _seeded:
                            _canon_store.save(_canon_state, snapshot=False)
                            console.print(
                                f"[dim]   📎 CharacterState 已同步到 canon_current.json：{', '.join(_seeded)}[/dim]"
                            )
                    except Exception as _exc:
                        console.print(
                            f"[yellow]   ⚠️  canon 状态同步失败（不影响主流程）：{_exc}[/yellow]"
                        )

        # ═══════════════════════════════════════
        # 2. 同步地点
        # ═══════════════════════════════════════
        if report.new_locations:
            console.print("[bold cyan]━━━ 📍 新增地点 ━━━[/bold cyan]\n")
            console.print(f"   {', '.join(report.new_locations)}\n")

            should_add_loc = resolved_auto_yes or Confirm.ask(
                "是否添加到 story_bible.json 的地理设定？",
                default=True,
            )

            if should_add_loc:
                story_bible_data = storage.load_json(layout.bible_path)
                story_bible = StoryBible.model_validate(story_bible_data)

                existing_geo = story_bible.geography or ""
                new_locations_str = "、".join(report.new_locations)
                if existing_geo:
                    story_bible.geography += (
                        f"；{new_locations_str}（第{resolved_chapter_num}章新增）"
                    )
                else:
                    story_bible.geography = new_locations_str

                storage.save_json(layout.bible_path, story_bible.model_dump(mode="json"))
                console.print("   [green]✅ 已添加[/green]\n")

        # ═══════════════════════════════════════
        # 3. 同步物品
        # ═══════════════════════════════════════
        if report.new_key_items:
            console.print("[bold magenta]━━━ 🔑 关键物品 ━━━[/bold magenta]\n")
            console.print(f"   {', '.join(report.new_key_items)}")
            console.print("   [dim]提示：关键物品可添加到角色的 inventory 或世界观设定中[/dim]\n")

        # ═══════════════════════════════════════
        # 4. 大纲调整
        # ═══════════════════════════════════════
        if not resolved_no_outline and report.plot_deviations:
            console.print("[bold red]━━━ 📖 剧情偏离分析 ━━━[/bold red]\n")

            for dev in report.plot_deviations:
                level_color = {"minor": "green", "moderate": "yellow", "major": "red"}.get(
                    dev.deviation_level, "white"
                )
                console.print(f"  [{level_color}]● {dev.deviation_level.upper()}[/{level_color}]")
                console.print(f"     原计划：{dev.outline_plan}")
                console.print(f"     实际情节：{dev.actual_plot}")
                if dev.reason:
                    console.print(f"     原因：{dev.reason}")
                if dev.impact_on_future:
                    console.print(f"     影响：{dev.impact_on_future}")
                console.print()

            if resolved_auto_yes:
                choice = "1"
            else:
                console.print("选择操作：")
                if resolved_smart:
                    console.print("  [1] 接受偏离，智能调整后续大纲（LLM模式：重新规划）")
                else:
                    console.print("  [1] 接受偏离，调整后续大纲（快速模式：直接用建议）")
                console.print("  [2] 保持大纲不变（手动协调）")
                console.print("  [3] 回退重写本章（回滚 canon 并删除本章产物）")
                console.print("  [q] 跳过")
                choice = Prompt.ask("\n你的选择", choices=["1", "2", "3", "q"], default="2")

            if choice == "1":
                await _apply_outline_adjustment(
                    layout=layout,
                    storage=storage,
                    router=router,
                    builder=builder,
                    settings=settings,
                    report=report,
                    report_data=report_data,
                    completed_chapter=resolved_chapter_num,
                    smart=resolved_smart,
                )
            elif choice == "3":
                confirm_delete = Confirm.ask(
                    f"\n⚠️  确认回滚 canon 并删除 chapter_{resolved_chapter_num:03d} 相关产物？",
                    default=False,
                )
                if confirm_delete:
                    _rollback_and_cleanup_chapter(
                        layout=layout,
                        chapter_number=resolved_chapter_num,
                    )
                    console.print(
                        f"   [green]✅ 已回滚并清理，可重新运行 run-chapter --chapter {resolved_chapter_num}[/green]\n"
                    )

        # ═══════════════════════════════════════
        # 5. 创作亮点
        # ═══════════════════════════════════════
        if report.creative_highlights:
            console.print("[bold green]━━━ ✨ 创作亮点 ━━━[/bold green]\n")
            for highlight in report.creative_highlights:
                console.print(f"  • {highlight}")
            console.print()

        # ═══════════════════════════════════════
        # 6. 下一章建议
        # ═══════════════════════════════════════
        if report.suggestions_for_next_chapter:
            console.print("[bold blue]━━━ 💡 下一章建议 ━━━[/bold blue]\n")
            console.print(f"  {report.suggestions_for_next_chapter}\n")

        console.print("[bold green]✅ 同步完成！[/bold green]\n")

    asyncio.run(_sync())


# ─── book-audit ──────────────────────────────────────────────────────


def _parse_chapter_range(value: str | None) -> list[int]:
    """Parse a CLI chapter range like ``1,2,5-8`` into sorted chapter numbers."""
    raw = (value or "").strip()
    if not raw:
        return []
    chapters: set[int] = set()
    for part in raw.replace("，", ",").split(","):
        item = part.strip()
        if not item:
            continue
        if "-" in item:
            left, right, *extra = item.split("-")
            if extra:
                raise typer.BadParameter(f"章节范围格式无效: {item}")
            try:
                start = int(left.strip())
                end = int(right.strip())
            except ValueError as exc:
                raise typer.BadParameter(f"章节范围格式无效: {item}") from exc
            if start <= 0 or end <= 0 or start > end:
                raise typer.BadParameter(f"章节范围必须为正序正整数: {item}")
            chapters.update(range(start, end + 1))
            continue
        try:
            chapter = int(item)
        except ValueError as exc:
            raise typer.BadParameter(f"章节号必须是整数: {item}") from exc
        if chapter <= 0:
            raise typer.BadParameter(f"章节号必须大于 0: {item}")
        chapters.add(chapter)
    return sorted(chapters)


def _audit_progress_description(stage: str, data: object) -> str:
    if stage == "book_consistency_start":
        return "收集全书上下文..."
    if stage == "book_consistency_issue_pool_ready":
        return "汇总章节问题池..."
    if stage == "book_consistency_two_phase_start":
        return "运行摘要筛查..."
    if stage == "book_consistency_two_phase_targeted_start":
        return "运行定向全文深审..."
    if stage == "book_consistency_chunk_progress":
        if isinstance(data, dict):
            done = data.get("completed_chunks", data.get("completed", "?"))
            total = data.get("total_chunks", data.get("total", "?"))
            return f"全书分批审计 {done}/{total}..."
        return "全书分批审计..."
    if stage == "book_consistency_verify_start":
        return "逐章验证审计问题..."
    if stage == "book_consistency_repair_start":
        return "准备自动修复..."
    if stage == "book_consistency_repair_progress":
        if isinstance(data, dict):
            done = data.get("completed", data.get("completed_chapters", "?"))
            total = data.get("total", data.get("total_chapters", "?"))
            return f"逐章修复 {done}/{total}..."
        return "逐章修复..."
    if stage == "book_consistency_post_audit_start":
        return "修复后定向复审..."
    if stage == "book_consistency_report_written":
        return "写入审计报告..."
    return stage.replace("_", " ")


def book_audit(
    project_id: Optional[str] = typer.Option(None, "--project-id", help="项目 ID"),
    chapter_range: Optional[str] = typer.Option(
        None,
        "--chapters",
        help="章节范围，如 1,2,5-8；不填则审计所有已完成章节",
    ),
    analysis_mode: Optional[str] = typer.Option(
        None,
        "--mode",
        help="审计模式：auto / summary / full_text",
    ),
    prompt_hint: Optional[str] = typer.Option(None, "--hint", help="本次审计附加提示词"),
    location_strictness: Optional[str] = typer.Option(
        None,
        "--location-strictness",
        help="定位严格度：strict / balanced / loose",
    ),
    repair: Optional[bool] = typer.Option(
        None,
        "--repair/--no-repair",
        help="已迁移：book-audit 只生成修复队列，不直接改正文",
    ),
    repair_min_severity: Optional[str] = typer.Option(
        None,
        "--repair-min-severity",
        help="自动修复最低严重度：critical / warning / info",
    ),
    repair_max_chapters: Optional[int] = typer.Option(
        None,
        "--repair-max-chapters",
        help="自动修复最多处理章节数",
    ),
    repair_concurrency: Optional[int] = typer.Option(
        None,
        "--repair-concurrency",
        help="自动修复并发数，1 最稳",
    ),
    post_repair_targeted_audit: Optional[bool] = typer.Option(
        None,
        "--post-repair-targeted-audit/--no-post-repair-targeted-audit",
        help="修复后是否对已修章节追加一次定向小审计",
    ),
    continue_from_audit: Optional[bool] = typer.Option(
        None,
        "--continue-from-audit/--fresh-audit",
        help="已迁移：请使用 repair-audit-queue 执行全书修复队列",
    ),
    continue_audit_from_checkpoint: Optional[bool] = typer.Option(
        None,
        "--continue-checkpoint/--ignore-checkpoint",
        help="从上次分批审计检查点续审",
    ),
    reset_audit_checkpoint: Optional[bool] = typer.Option(
        None,
        "--reset-checkpoint/--keep-checkpoint",
        help="开始前清除已有审计检查点",
    ),
    parallel_chunks: Optional[bool] = typer.Option(
        None,
        "--parallel-chunks/--sequential-chunks",
        help="分块审计是否并行",
    ),
    parallel_dimensions: Optional[bool] = typer.Option(
        None,
        "--parallel-dimensions/--sequential-dimensions",
        help="全书审计是否按维度裁剪上下文并受控并行",
    ),
    max_tokens: Optional[int] = typer.Option(None, "--max-tokens", help="覆盖输出 token 上限"),
    temperature: Optional[float] = typer.Option(None, "--temperature", help="覆盖审计温度"),
    mock: Optional[bool] = typer.Option(
        None,
        "--mock/--no-mock",
        help="强制使用 Mock 模式（调试用）",
    ),
    verbose: Optional[bool] = typer.Option(
        None,
        "-v",
        "--verbose/--quiet",
        help="显示详细步骤事件",
    ),
    config: Optional[str] = typer.Option(None, "--config", help="运行参数配置文件（JSON）"),
) -> None:
    """Run a whole-book consistency audit from the CLI."""

    cfg, cfg_path = _load_cli_config(config)
    section = "book_audit"

    resolved_project_id = _resolve_config_value(
        resolve_str_option,
        project_id,
        cfg,
        section=section,
        key="project_id",
        default="",
    )
    if not resolved_project_id:
        raise typer.BadParameter(
            "project_id 未提供；请使用 --project-id 或在配置文件 book_audit.project_id 设置"
        )
    resolved_chapter_range = _parse_chapter_range(
        _resolve_config_value(
            resolve_str_option,
            chapter_range,
            cfg,
            section=section,
            key="chapter_range",
            default="",
        )
    )
    resolved_analysis_mode = _resolve_config_value(
        resolve_choice_option,
        analysis_mode,
        cfg,
        section=section,
        key="analysis_mode",
        default="auto",
        choices=("auto", "summary", "full_text"),
    )
    resolved_prompt_hint = _resolve_config_value(
        resolve_str_option,
        prompt_hint,
        cfg,
        section=section,
        key="prompt_hint",
        default="",
    )
    resolved_location_strictness = _resolve_config_value(
        resolve_choice_option,
        location_strictness,
        cfg,
        section=section,
        key="location_strictness",
        default="balanced",
        choices=("strict", "balanced", "loose"),
    )
    resolved_repair = _resolve_config_value(
        resolve_bool_option,
        repair,
        cfg,
        section=section,
        key="repair",
        default=False,
    )
    resolved_continue_from_audit = _resolve_config_value(
        resolve_bool_option,
        continue_from_audit,
        cfg,
        section=section,
        key="continue_from_audit",
        default=False,
    )
    requested_repair_queue_execution = resolved_repair or resolved_continue_from_audit
    resolved_repair_mode = "off"
    resolved_repair_min_severity = _resolve_config_value(
        resolve_choice_option,
        repair_min_severity,
        cfg,
        section=section,
        key="repair_min_severity",
        default="warning",
        choices=("critical", "warning", "info"),
    )
    resolved_repair_max_chapters = _resolve_config_value(
        resolve_int_option,
        repair_max_chapters,
        cfg,
        section=section,
        key="repair_max_chapters",
        default=20,
        minimum=1,
        maximum=500,
    )
    resolved_repair_concurrency = _resolve_config_value(
        resolve_int_option,
        repair_concurrency,
        cfg,
        section=section,
        key="repair_concurrency",
        default=1,
        minimum=1,
        maximum=8,
    )
    resolved_post_repair_targeted_audit = _resolve_config_value(
        resolve_bool_option,
        post_repair_targeted_audit,
        cfg,
        section=section,
        key="post_repair_targeted_audit",
        default=False,
    )
    resolved_continue_audit_from_checkpoint = _resolve_config_value(
        resolve_bool_option,
        continue_audit_from_checkpoint,
        cfg,
        section=section,
        key="continue_audit_from_checkpoint",
        default=False,
    )
    resolved_reset_audit_checkpoint = _resolve_config_value(
        resolve_bool_option,
        reset_audit_checkpoint,
        cfg,
        section=section,
        key="reset_audit_checkpoint",
        default=False,
    )
    resolved_parallel_chunks = _resolve_config_value(
        resolve_bool_option,
        parallel_chunks,
        cfg,
        section=section,
        key="parallel_chunks",
        default=True,
    )
    resolved_parallel_dimensions = _resolve_config_value(
        resolve_bool_option,
        parallel_dimensions,
        cfg,
        section=section,
        key="parallel_dimensions",
        default=False,
    )
    resolved_max_tokens = _resolve_config_value(
        resolve_int_option,
        max_tokens,
        cfg,
        section=section,
        key="max_tokens",
        default=0,
        minimum=0,
        maximum=65536,
    )
    resolved_temperature = _resolve_config_value(
        resolve_float_option,
        temperature,
        cfg,
        section=section,
        key="temperature",
        default=None,
        minimum=0.0,
        maximum=2.0,
    )
    resolved_mock = _resolve_config_value(
        resolve_bool_option,
        mock,
        cfg,
        section=section,
        key="mock",
        default=False,
    )
    resolved_verbose = _resolve_config_value(
        resolve_bool_option,
        verbose,
        cfg,
        section=section,
        key="verbose",
        default=False,
    )

    async def _run() -> None:
        from novel_forge.workspace.execution import execute_book_consistency

        if cfg_path is not None:
            console.print(f"[dim]📁 使用运行配置: {cfg_path}[/dim]")
        if requested_repair_queue_execution:
            console.print(
                "[yellow]提示：book-audit 现在只生成报告和修复队列；"
                "正文修改请使用 repair-audit-queue。[/yellow]"
            )
        settings = get_settings()
        runtime = _create_runtime_services(settings=settings, mock=resolved_mock)
        request = BookConsistencyRequest(
            project_id=resolved_project_id,
            chapter_range=resolved_chapter_range,
            analysis_mode=resolved_analysis_mode,
            prompt_hint=resolved_prompt_hint,
            location_strictness=resolved_location_strictness,
            max_tokens=resolved_max_tokens or None,
            temperature=resolved_temperature,
            repair_mode=resolved_repair_mode,
            repair_min_severity=resolved_repair_min_severity,
            repair_max_chapters=resolved_repair_max_chapters,
            repair_concurrency=resolved_repair_concurrency,
            post_repair_targeted_audit=resolved_post_repair_targeted_audit,
            continue_from_audit=resolved_continue_from_audit,
            continue_audit_from_checkpoint=resolved_continue_audit_from_checkpoint,
            reset_audit_checkpoint=resolved_reset_audit_checkpoint,
            parallel_chunks=resolved_parallel_chunks,
            parallel_dimensions=resolved_parallel_dimensions,
        )

        chapter_label = (
            ",".join(str(ch) for ch in resolved_chapter_range)
            if resolved_chapter_range
            else "全部已完成章节"
        )
        console.print("\n[bold cyan]全书一致性审计[/bold cyan]")
        console.print(f"[dim]项目: {resolved_project_id}[/dim]")
        console.print(f"[dim]章节: {chapter_label}[/dim]")
        console.print(f"[dim]模式: {resolved_analysis_mode} / 架构: global_audit_v1[/dim]\n")

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(complete_style="green", finished_style="bold green"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]准备审计...", total=None)

            def on_progress(stage: str, data: object) -> None:
                description = _audit_progress_description(stage, data)
                progress.update(task, description=f"[cyan]{description}")
                if resolved_verbose:
                    console.print(f"[dim]{stage}: {data}[/dim]")

            execution = await execute_book_consistency(
                runtime,
                request,
                on_step_progress=on_progress,
            )
            progress.update(task, description="[bold green]审计完成")

        layout = ProjectLayout(runtime.storage.existing_project_dir(resolved_project_id))
        report_path = layout.reports_dir / "book_consistency_audit.json"
        report_payload: dict[str, Any]
        if report_path.exists():
            report_payload = runtime.storage.load_json(report_path)
        elif hasattr(execution.result, "model_dump"):
            report_payload = execution.result.model_dump(mode="json")
        else:
            report_payload = {}

        issues = report_payload.get("issues", [])
        issue_count = len(issues) if isinstance(issues, list) else 0
        acceptance = report_payload.get("acceptance")
        if not isinstance(acceptance, dict):
            acceptance = {}
        score = report_payload.get("consistency_score", 0.0)
        analysis = report_payload.get("analysis_mode", resolved_analysis_mode)

        table = Table(title="审计结果")
        table.add_column("指标", style="cyan")
        table.add_column("值", style="green")
        table.add_row("分析模式", str(analysis))
        table.add_row("一致性评分", str(score))
        table.add_row("问题数", str(issue_count))
        if acceptance:
            table.add_row("验收状态", str(acceptance.get("status", "")))
            table.add_row("导出建议", str(acceptance.get("recommendation", "")))
        table.add_row("审计报告", str(report_path))

        queue_summary = report_payload.get("repair_queue_summary")
        if isinstance(queue_summary, dict):
            table.add_row(
                "Ready 队列项", str(queue_summary.get("ready", queue_summary.get("ready_count", 0)))
            )
            table.add_row(
                "Verify-first",
                str(queue_summary.get("verify_first", queue_summary.get("verify_first_count", 0))),
            )
        table.add_row("审计库", str(layout.global_audit_db_path))
        console.print()
        console.print(table)

    asyncio.run(_run())


# ─── repair-audit-queue ───────────────────────────────────────────────


def repair_audit_queue(
    project_id: str = typer.Option(..., "--project-id", help="项目 ID"),
    run_id: str = typer.Option("", "--run-id", help="审计 run_id；为空则使用最新完成的审计"),
    max_items: int = typer.Option(20, "--max-items", min=1, max=500, help="最多处理的队列项"),
    no_verify: bool = typer.Option(
        False,
        "--no-verify",
        help="跳过执行前 source_hash 校验（不推荐）",
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="显示详细步骤事件"),
    mock: bool = typer.Option(False, "--mock/--no-mock", help="强制使用 Mock 模式（调试用）"),
) -> None:
    """Execute ready repair tickets produced by book-audit."""

    async def _run() -> None:
        from novel_forge.workspace.execution import execute_global_repair_queue

        settings = get_settings()
        runtime = _create_runtime_services(settings=settings, mock=mock)
        request = GlobalRepairQueueRequest(
            project_id=project_id,
            run_id=run_id,
            statuses=["ready"],
            max_items=max_items,
            verify_before_apply=not no_verify,
            rollback_on_failure=True,
        )

        console.print("\n[bold cyan]执行全书修复队列[/bold cyan]")
        console.print(f"[dim]项目: {project_id}[/dim]")
        console.print(f"[dim]run_id: {run_id or 'latest'}[/dim]\n")

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(complete_style="green", finished_style="bold green"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]准备修复队列...", total=None)

            def on_progress(stage: str, data: object) -> None:
                progress.update(task, description=f"[cyan]{stage.replace('_', ' ')}")
                if verbose:
                    console.print(f"[dim]{stage}: {data}[/dim]")

            execution = await execute_global_repair_queue(
                runtime,
                request,
                on_step_progress=on_progress,
            )
            progress.update(task, description="[bold green]修复队列处理完成")

        result = execution.result if isinstance(execution.result, dict) else {}
        table = Table(title="修复队列结果")
        table.add_column("指标", style="cyan")
        table.add_column("值", style="green")
        for key in ("run_id", "requested", "processed", "applied", "blocked", "failed", "skipped"):
            table.add_row(key, str(result.get(key, "")))
        console.print()
        console.print(table)

    asyncio.run(_run())


# ─── editorial-audit ─────────────────────────────────────────────────


def _editorial_audit_progress_description(stage: str, data: object) -> str:
    if stage == "book_editorial_audit_start":
        if isinstance(data, dict):
            chapters = data.get("chapters") or []
            return f"准备出版级编辑审计（{len(chapters)} 章）..."
        return "准备出版级编辑审计..."
    if stage == "book_editorial_audit":
        return "汇总编辑 findings 与修订计划..."
    if stage == "book_editorial_audit_report_written":
        return "写入出版级编辑审计报告..."
    return stage.replace("_", " ")


def editorial_audit(
    project_id: str = typer.Option(..., "--project-id", help="项目 ID"),
    chapter_range: Optional[str] = typer.Option(
        None,
        "--chapters",
        help="章节范围，如 1,2,5-8；不填则审计所有已完成章节",
    ),
    prompt_hint: str = typer.Option("", "--hint", help="本次编辑审计附加提示词"),
    batch_size: int = typer.Option(12, "--batch-size", min=1, max=100, help="每批审计章节数"),
    max_tokens: int = typer.Option(
        8192, "--max-tokens", min=512, max=65536, help="输出 token 上限"
    ),
    temperature: float = typer.Option(0.2, "--temperature", min=0.0, max=2.0, help="审计温度"),
    batch_timeout_s: float = typer.Option(
        0.0,
        "--batch-timeout-s",
        min=0.0,
        help="单个编辑审计批次超时秒数；0 表示仅使用网关自身超时",
    ),
    mock: bool = typer.Option(False, "--mock/--no-mock", help="强制使用 Mock 模式（调试用）"),
    verbose: bool = typer.Option(False, "-v", "--verbose/--quiet", help="显示详细步骤事件"),
) -> None:
    """Run a publication-level editorial audit from the CLI."""

    resolved_chapter_range = _parse_chapter_range(chapter_range)

    async def _run() -> None:
        from novel_forge.workspace.execution import execute_book_editorial_audit

        settings = get_settings()
        runtime = _create_runtime_services(settings=settings, mock=mock)
        request = BookEditorialAuditRequest(
            project_id=project_id,
            chapter_range=resolved_chapter_range,
            prompt_hint=prompt_hint,
            max_tokens=max_tokens,
            temperature=temperature,
            batch_size=batch_size,
            batch_timeout_s=batch_timeout_s,
        )

        chapter_label = (
            ",".join(str(chapter) for chapter in resolved_chapter_range)
            if resolved_chapter_range
            else "全部已完成章节"
        )
        console.print("\n[bold cyan]全书出版级编辑审计[/bold cyan]")
        console.print(f"[dim]项目: {project_id}[/dim]")
        console.print(f"[dim]章节: {chapter_label}[/dim]")
        console.print(f"[dim]批大小: {batch_size} / 温度: {temperature}[/dim]\n")

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(complete_style="green", finished_style="bold green"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]准备编辑审计...", total=None)

            def on_progress(stage: str, data: object) -> None:
                description = _editorial_audit_progress_description(stage, data)
                progress.update(task, description=f"[cyan]{description}")
                if verbose:
                    console.print(f"[dim]{stage}: {data}[/dim]")

            execution = await execute_book_editorial_audit(
                runtime,
                request,
                on_step_progress=on_progress,
            )
            progress.update(task, description="[bold green]编辑审计完成")

        layout = ProjectLayout(runtime.storage.existing_project_dir(project_id))
        report_path = layout.reports_dir / "book_editorial_audit.json"
        result = execution.result if isinstance(execution.result, dict) else {}
        findings = result.get("findings", [])
        metrics = result.get("metrics", {})
        structured_plan = (
            metrics.get("structured_revision_plan", {}) if isinstance(metrics, dict) else {}
        )
        actions = structured_plan.get("actions", []) if isinstance(structured_plan, dict) else []

        table = Table(title="出版级编辑审计结果")
        table.add_column("指标", style="cyan")
        table.add_column("值", style="green")
        table.add_row("问题数", str(len(findings) if isinstance(findings, list) else 0))
        table.add_row("结构化修订动作", str(len(actions) if isinstance(actions, list) else 0))
        table.add_row("审计报告", str(report_path))

        console.print()
        console.print(table)

    asyncio.run(_run())


# ─── repair-motif-history ────────────────────────────────────────────


def repair_motif_history(
    project_id: str = typer.Option(..., help="Project ID"),
    chapter_number: int = typer.Option(..., help="Current chapter number (>= 1)"),
    force_re_extract: bool = typer.Option(
        False, "--force-re-extract", help="Re-extract motifs from archived chapter text"
    ),
    start_chapter: int = typer.Option(
        1, "--start-chapter", help="Start chapter for re-extraction (default: 1)"
    ),
    end_chapter: int | None = typer.Option(
        None, "--end-chapter", help="End chapter for re-extraction (default: chapter_number)"
    ),
    mock: bool = typer.Option(False, "--mock", help="Force mock mode (for testing/debugging)"),
) -> None:
    """Repair/backfill motif history stats for an existing project.

    Two-layer strategy:
    - Layer 1: Rebuild stats from existing _motif_cache (always runs).
    - Layer 2: If --force-re-extract is set, re-extract motifs from archived
      chapter text for chapters with empty cache.

    Examples:
        novel-forge repair-motif-history --project-id my_novel --chapter-number 5
        novel-forge repair-motif-history --project-id my_novel --chapter-number 5 --force-re-extract
        novel-forge repair-motif-history --project-id my_novel --chapter-number 10 --force-re-extract --start-chapter 3 --end-chapter 8
    """

    async def _repair() -> None:
        from novel_forge.cli.runtime_helpers import _create_runtime_services
        from novel_forge.workspace.contracts import RepairMotifHistoryRequest
        from novel_forge.workspace.execution import execute_repair_motif_history

        settings = get_settings()
        runtime = _create_runtime_services(settings=settings, mock=mock)

        resolved_end = end_chapter if end_chapter is not None else chapter_number

        request = RepairMotifHistoryRequest(
            project_id=project_id,
            chapter_number=chapter_number,
            force_re_extract=force_re_extract,
            start_chapter=start_chapter,
            end_chapter=resolved_end,
        )

        mode_label = (
            "强制重提取（Layer 1 + Layer 2）" if force_re_extract else "仅缓存重建（Layer 1）"
        )
        console.print("\n[bold cyan]🔧 母题历史修复[/bold cyan]")
        console.print(f"[dim]项目: {project_id}[/dim]")
        console.print(f"[dim]章节: {chapter_number}[/dim]")
        console.print(f"[dim]重提取范围: 第 {start_chapter}-{resolved_end} 章[/dim]")
        console.print(f"[dim]模式: {mode_label}[/dim]")
        console.print()

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(complete_style="green", finished_style="bold green"),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]正在修复母题历史...", total=1)

            def on_progress(stage: str, data: object) -> None:
                if stage == "motif_history_repair_start":
                    progress.update(task, description="[yellow]Layer 1: 重建缓存统计...")
                elif stage == "motif_repair_layer1_start":
                    progress.update(task, description="[yellow]Layer 1: 扫描现有缓存...")
                elif stage == "motif_repair_layer1_done":
                    progress.update(task, description="[green]Layer 1: 缓存重建完成")
                elif stage == "motif_repair_layer2_start":
                    progress.update(task, description="[yellow]Layer 2: 重提取母题...")
                elif stage == "motif_repair_layer2_scanning":
                    progress.update(task, description="[yellow]Layer 2: 扫描章节文本...")
                elif stage == "motif_repair_layer2_progress":
                    ch = data.get("chapter", "?") if isinstance(data, dict) else "?"
                    progress.update(task, description=f"[yellow]Layer 2: 提取第 {ch} 章...")
                elif stage == "motif_repair_layer2_done":
                    progress.update(task, description="[green]Layer 2: 重提取完成")
                elif stage == "motif_history_repair_done":
                    progress.update(
                        task, advance=1, completed=1, description="[bold green]✅ 母题历史修复完成"
                    )
                elif stage == "motif_history_repair_error":
                    progress.update(task, description="[red]❌ 修复过程中出错")

            result = await execute_repair_motif_history(
                runtime,
                request,
                on_step_progress=on_progress,
            )

        console.print()

        if getattr(result, "result", None) and isinstance(result.result, dict):
            stats = result.result
            if stats.get("ok"):
                table = Table(title="母题历史修复结果")
                table.add_column("指标", style="cyan")
                table.add_column("值", style="green")

                table.add_row("Layer 1 缓存重建", str(stats.get("layer1_rebuilt", "")))
                table.add_row("Layer 2 重提取", str(stats.get("layer2_rebuilt", "")))
                table.add_row("修复的章节数", str(stats.get("repaired_chapters", "")))
                table.add_row("总母题数", str(stats.get("total_motifs", "")))

                console.print(table)
                console.print("\n[bold green]✅ 母题历史修复成功！[/bold green]")
            else:
                reason = stats.get("reason", "未知原因")
                console.print(f"[red]❌ 修复失败: {reason}[/red]")
        else:
            console.print("[yellow]⚠️  修复完成（返回格式异常）[/yellow]")
            console.print(f"[dim]{result}[/dim]")

    asyncio.run(_repair())


def rebuild_memory_vectors(
    project_id: str = typer.Option(..., help="Project ID"),
    mock: bool = typer.Option(
        False,
        "--mock",
        help="Use mock runtime and in_memory backend; intended for tests only.",
    ),
) -> None:
    """Explicitly rebuild the project's episodic vector collection."""

    async def _rebuild() -> None:
        settings = get_settings()
        runtime = _create_runtime_services(settings=settings, mock=mock)
        memory_context = await runtime.get_memory_context(project_id)
        if memory_context is None:
            console.print(f"[red]❌ Error:[/red] Memory context unavailable for '{project_id}'")
            raise typer.Exit(1)

        console.print("\n[bold cyan]🔧 重建记忆向量集合[/bold cyan]")
        console.print(f"[dim]项目: {project_id}[/dim]")
        console.print(f"[dim]模式: {'mock/in_memory' if mock else 'zvec'}[/dim]")
        console.print()

        try:
            result = await memory_context.rebuild_vector_collection()
        except Exception as exc:
            console.print(f"[red]❌ 重建失败:[/red] {exc}")
            console.print(
                "[dim]请确认已安装 zvec，并配置可用的 embedding profile；"
                "旧开发数据可通过本命令重新生成 collection。[/dim]"
            )
            raise typer.Exit(1) from exc

        table = Table(title="记忆向量重建结果")
        table.add_column("指标", style="cyan")
        table.add_column("值", style="green")
        table.add_row("后端", str(result.get("backend", "")))
        table.add_row("路径", str(result.get("path", "")))
        table.add_row("维度", str(result.get("dimension", "")))
        table.add_row("索引类型", str(result.get("index_type", "")))
        table.add_row("向量数", str(result.get("rebuilt_vectors", 0)))
        table.add_row("已保存", str(result.get("saved", False)))
        console.print(table)
        console.print("\n[bold green]✅ 记忆向量集合重建完成[/bold green]")

    asyncio.run(_rebuild())


def rebuild_expression_memory(
    project_id: str = typer.Option(..., help="Project ID"),
    from_chapter: int | None = typer.Option(
        None,
        "--from-chapter",
        min=1,
        help="First chapter to rebuild; omitted means earliest archived chapter.",
    ),
    to_chapter: int | None = typer.Option(
        None,
        "--to-chapter",
        min=1,
        help="Last chapter to rebuild; omitted means latest archived chapter.",
    ),
    mock: bool = typer.Option(
        False,
        "--mock",
        help="Use mock runtime and in_memory backend; intended for tests only.",
    ),
) -> None:
    """Rebuild expression-channel semantic evidence memory from archived chapters."""

    async def _rebuild() -> None:
        settings = get_settings()
        runtime = _create_runtime_services(settings=settings, mock=mock)
        memory_context = await runtime.get_memory_context(project_id)
        if memory_context is None:
            console.print(f"[red]❌ Error:[/red] Memory context unavailable for '{project_id}'")
            raise typer.Exit(1)

        console.print("\n[bold cyan]🔧 重建表达通道语义记忆[/bold cyan]")
        console.print(f"[dim]项目: {project_id}[/dim]")
        console.print(f"[dim]章节范围: {from_chapter or '最早'} - {to_chapter or '最新'}[/dim]")
        console.print(f"[dim]模式: {'mock/in_memory' if mock else 'zvec'}[/dim]")
        console.print()

        try:
            result = await memory_context.rebuild_expression_channel_memory(
                from_chapter=from_chapter,
                to_chapter=to_chapter,
            )
        except Exception as exc:
            console.print(f"[red]❌ 重建失败:[/red] {exc}")
            console.print(
                "[dim]请确认已启用表达通道检测、语义记忆和可用 embedding；"
                "如项目缺少表达画像，本命令会先尝试从现有章节刷新。[/dim]"
            )
            raise typer.Exit(1) from exc

        table = Table(title="表达通道语义记忆重建结果")
        table.add_column("指标", style="cyan")
        table.add_column("值", style="green")
        table.add_row("回填章节数", str(result.get("rebuilt_chapters", 0)))
        table.add_row("观察项", str(result.get("observations", 0)))
        table.add_row("向量数", str(result.get("vectors", 0)))
        table.add_row("刷新表达画像", str(result.get("refreshed_profiles", False)))
        table.add_row("跳过原因", str(result.get("skipped", "") or "-"))
        status = result.get("status", {}) if isinstance(result.get("status"), dict) else {}
        table.add_row("当前观察项总数", str(status.get("expression_observation_count", "")))
        console.print(table)
        console.print("\n[bold green]✅ 表达通道语义记忆重建完成[/bold green]")

    asyncio.run(_rebuild())


# ─── reextract-chapter-contract ────────────────────────────────────────


def reextract_chapter_contract(
    project_id: str = typer.Option(..., help="Project ID"),
    chapter: int = typer.Option(
        0,
        "--chapter",
        min=0,
        help="Specific chapter number whose outline changed (0 = auto-detect from outline fingerprint).",
    ),
    chapters: str = typer.Option(
        "",
        "--chapters",
        help="Comma-separated chapter numbers (e.g. '3,4,5'). Overrides --chapter.",
    ),
    cascade: bool = typer.Option(
        True,
        "--cascade/--no-cascade",
        help="Also refresh chapters referenced by entry_state_requirements.",
    ),
    no_rebuild_milestones: bool = typer.Option(
        False,
        "--no-rebuild-milestones",
        help="Skip rebuilding the plot milestone index.",
    ),
    no_mark_stale: bool = typer.Option(
        False,
        "--no-mark-stale",
        help="Skip marking downstream artifacts stale.",
    ),
    max_cascade_depth: int = typer.Option(
        3,
        "--max-cascade-depth",
        min=1,
        max=10,
        help="Maximum recursion depth for cascade propagation.",
    ),
    mock: bool = typer.Option(
        False,
        "--mock",
        help="Use mock runtime; intended for tests only.",
    ),
) -> None:
    """Re-sync chapter contracts after an outline edit (local regeneration).

    Refreshes ``chapter_contracts.json`` and ``plot_milestone_index.json``
    for the affected + cascade chapter set, marks downstream artifacts
    stale, and never modifies chapter prose.
    """

    async def _run() -> None:
        affected: list[int] = []
        if chapters.strip():
            for token in chapters.split(","):
                token = token.strip()
                if not token:
                    continue
                try:
                    n = int(token)
                except ValueError:
                    console.print(f"[red]❌ 非法章节号:[/red] {token}")
                    raise typer.Exit(1) from None
                if n > 0:
                    affected.append(n)
        elif chapter > 0:
            affected = [chapter]

        settings = get_settings()
        runtime = _create_runtime_services(settings=settings, mock=mock)

        request = SyncChapterContractsRequest(
            project_id=project_id,
            affected_chapter_numbers=affected,
            cascade_downstream=cascade,
            rebuild_milestones=not no_rebuild_milestones,
            mark_stale=not no_mark_stale,
            max_cascade_depth=max_cascade_depth,
        )

        console.print("\n[bold cyan]🔄 同步章节契约[/bold cyan]")
        console.print(f"[dim]项目: {project_id}[/dim]")
        if affected:
            console.print(f"[dim]显式章节: {affected}[/dim]")
        else:
            console.print("[dim]显式章节: 自动检测 (基于 outline 指纹)[/dim]")
        console.print(f"[dim]级联刷新: {'开启' if cascade else '关闭'}[/dim]")
        console.print(f"[dim]重建 milestone: {'关闭' if no_rebuild_milestones else '开启'}[/dim]")
        console.print(f"[dim]标记 stale: {'关闭' if no_mark_stale else '开启'}[/dim]")
        console.print()

        from novel_forge.workspace.execution import execute_sync_chapter_contracts

        try:
            result = await execute_sync_chapter_contracts(
                runtime,
                request,
                on_step_progress=lambda name, payload: _print_sync_step(name, payload),
            )
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]❌ 同步失败:[/red] {exc}")
            raise typer.Exit(1) from exc

        summary = result.result if isinstance(result.result, dict) else {}
        if "error" in summary:
            console.print(f"[red]❌ 同步失败:[/red] {summary['error']}")
            raise typer.Exit(1)
        if summary.get("status") == "requires_manual_scope":
            reason = str(summary.get("manual_scope_reason") or "无法自动判断局部范围")
            console.print(f"[yellow]需要指定同步章节:[/yellow] {reason}")
            console.print("[dim]请使用 --chapter 或 --chapters 显式指定本次大纲修改的章节。[/dim]")
            raise typer.Exit(1)

        table = Table(title="章节契约同步结果")
        table.add_column("指标", style="cyan")
        table.add_column("值", style="green")
        table.add_row("刷新契约", str(summary.get("refreshed", 0)))
        cascade_list = summary.get("cascade") or []
        table.add_row("级联章节", ", ".join(str(n) for n in cascade_list) or "-")
        table.add_row("milestone 重建", str(summary.get("milestones_rebuilt", 0)))
        table.add_row("stale 标记", str(summary.get("stale_marked", 0)))
        table.add_row("session_id", str(summary.get("session_id", "")))
        table.add_row("耗时 (s)", f"{summary.get('duration_s', 0):.2f}")
        console.print(table)
        console.print("\n[bold green]✅ 章节契约同步完成 (prose 未修改)[/bold green]")

    asyncio.run(_run())


def _print_sync_step(name: str, payload: Any) -> None:
    """Render a single step event for CLI progress display."""
    data = payload if isinstance(payload, dict) else {}
    desc = ""
    if name == "sync_start":
        focus = data.get("focus") or []
        desc = f"开始同步 · 焦点章节 {len(focus)} 个: {focus}"
    elif name == "sync_chapter_loading":
        desc = f"加载章节上下文 · {data.get('count', 0)} 章"
    elif name == "sync_chapter_llm":
        chapters = data.get("chapters") or []
        desc = f"调用 LLM 重提取契约 · {len(chapters)} 章"
    elif name == "sync_chapter_saving":
        desc = "写回 chapter_contracts.json"
    elif name == "sync_milestone_rebuilding":
        old_hash = str(data.get("old_hash", "") or "")[:12]
        desc = f"重建 plot_milestone_index (old_hash={old_hash})"
    elif name == "sync_marking_stale":
        desc = f"标记下游产物 stale · {data.get('count', 0)} 个"
    elif name == "sync_llm_error":
        desc = f"LLM 调用失败: {data.get('error', '')}"
    elif name == "sync_done":
        desc = "同步完成"
    if desc:
        console.print(f"  [cyan]·[/cyan] {desc}")


# ─── repair-outline-resume ───────────────────────────────────────────


def repair_outline_resume(
    project_id: str = typer.Option(..., help="Project ID"),
    apply: bool = typer.Option(
        False,
        "--apply/--dry-run",
        help="执行修复；默认只审计不移动文件",
    ),
    total_chapters: Optional[int] = typer.Option(
        None,
        "--total-chapters",
        min=1,
        help="显式指定总章节数；通常可从 outline/session/checkpoint 推断",
    ),
) -> None:
    """Audit or repair dirty resumable init-outline state."""
    from novel_forge.pipeline.long.services.init.init_outline_recovery import (
        repair_outline_resume_state,
    )

    storage = _get_storage()
    layout = ProjectLayout(storage.project_path(project_id))
    result = repair_outline_resume_state(
        storage,
        layout,
        apply=apply,
        total_chapters=total_chapters,
    )

    canonical = result.get("canonical_outline", {})
    checkpoints = result.get("checkpoints", {})
    entity_audit = result.get("entity_name_audit", {})

    table = Table(title="大纲断点审计/修复")
    table.add_column("项目", style="cyan")
    table.add_column("值", style="green")
    table.add_row("project_id", project_id)
    table.add_row("模式", "apply" if apply else "dry-run")
    table.add_row("canonical outline", str(canonical.get("path", "")))
    table.add_row("canonical 完整", str(bool(canonical.get("complete"))))
    table.add_row("canonical partial", str(bool(canonical.get("partial"))))
    table.add_row("ledger checkpoints", str(checkpoints.get("ledger_checkpoint_count", 0)))
    table.add_row("accepted chapters", str(checkpoints.get("accepted_chapters_done", 0)))
    missing = checkpoints.get("missing_from_accepted") or []
    table.add_row("accepted 缺章", ", ".join(str(item) for item in missing[:20]) or "-")
    dirty = checkpoints.get("dirty") or []
    table.add_row("dirty checkpoints", str(len(dirty)))
    orphan = checkpoints.get("orphan") or []
    table.add_row("orphan checkpoints", str(len(orphan)))
    table.add_row("实体名问题", str(len(entity_audit.get("issues") or [])))
    table.add_row("持久化需修复", str(bool(result.get("needs_persistence_repair"))))
    table.add_row("实体名需修复", str(bool(result.get("needs_entity_repair"))))
    moved = result.get("moved_artifacts") or []
    table.add_row("已隔离文件", str(len(moved)))
    console.print(table)

    if moved:
        console.print("[dim]隔离位置：[/dim]")
        for path in moved[:12]:
            console.print(f"  • {path}")
    if result.get("needs_persistence_repair") and not apply:
        console.print("\n[yellow]检测到可修复状态；确认后使用 --apply 执行隔离与断点重建。[/yellow]")
    elif result.get("needs_entity_repair"):
        console.print(
            "\n[yellow]检测到实体名漂移；--apply 只处理断点/partial 持久化，"
            "实体名需通过大纲/契约定向修复或重新生成下游契约处理。[/yellow]"
        )


def regenerate_outline(
    project_id: str = typer.Option(..., help="Project ID"),
    apply: bool = typer.Option(
        False,
        "--apply/--dry-run",
        help="执行隔离；默认只列出将移动的 outline 及下游产物",
    ),
) -> None:
    """Prepare a project so init-long regenerates the outline from blueprint."""
    from novel_forge.pipeline.long.services.init.init_outline_recovery import (
        prepare_outline_regeneration,
    )

    storage = _get_storage()
    layout = ProjectLayout(storage.project_path(project_id))
    result = prepare_outline_regeneration(storage, layout, apply=apply)

    table = Table(title="重新生成大纲准备")
    table.add_column("项目", style="cyan")
    table.add_column("值", style="green")
    table.add_row("project_id", project_id)
    table.add_row("模式", "apply" if apply else "dry-run")
    table.add_row("将隔离产物", str(result.get("artifact_count", 0)))
    moved = result.get("moved_artifacts") or []
    table.add_row("已隔离产物", str(len(moved)))
    console.print(table)

    artifacts = result.get("artifacts") or []
    if artifacts and not apply:
        console.print("[dim]将隔离：[/dim]")
        for item in artifacts[:24]:
            if isinstance(item, dict):
                console.print(f"  • {item.get('path')} ({item.get('kind')})")
    if moved:
        console.print("[dim]已隔离到：[/dim]")
        for path in moved[:24]:
            console.print(f"  • {path}")
        console.print("\n[green]完成。再次运行 init-long 将从大纲阶段重新生成。[/green]")
    elif not artifacts:
        console.print("[dim]没有发现需要隔离的大纲/下游产物。[/dim]")
    elif not apply:
        console.print("\n[yellow]确认后使用 --apply 执行隔离，再重新运行 init-long。[/yellow]")


# ─── register ───────────────────────────────────────────────────────


def register(app: typer.Typer) -> None:
    """Register maintenance commands."""
    app.command(name="rollback-chapter")(rollback_chapter)
    app.command(name="restore-version")(restore_version)
    app.command(name="ab-test")(ab_test)
    app.command(name="sync-bible")(sync_bible)
    app.command(name="book-audit")(book_audit)
    app.command(name="repair-audit-queue")(repair_audit_queue)
    app.command(name="editorial-audit")(editorial_audit)
    app.command(name="repair-motif-history")(repair_motif_history)
    app.command(name="rebuild-memory-vectors")(rebuild_memory_vectors)
    app.command(name="rebuild-expression-memory")(rebuild_expression_memory)
    app.command(name="reextract-chapter-contract")(reextract_chapter_contract)
    app.command(name="repair-outline-resume")(repair_outline_resume)
    app.command(name="regenerate-outline")(regenerate_outline)
