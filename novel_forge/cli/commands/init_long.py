"""Long-project initialization CLI command."""

from __future__ import annotations

import asyncio
from typing import Any, Literal, Optional, cast

import typer
from rich.console import Console
from rich.panel import Panel

from novel_forge.cli.display import _make_step_callback
from novel_forge.cli.error_handling import (
    _build_error_trace_summary,
    _print_cli_error_panel,
)
from novel_forge.cli.progress_helpers import (
    _compose_step_callbacks,
    _make_progress_tracker,
    _new_cli_progress,
)
from novel_forge.cli.runtime_config import (
    resolve_bool_option,
    resolve_choice_option,
    resolve_int_option,
    resolve_json_dict_option,
    resolve_str_option,
)
from novel_forge.cli.runtime_helpers import (
    _create_runtime_services,
    _get_storage,
    _load_cli_config,
    _resolve_common_cli_controls,
    _resolve_story_spec_options,
)
from novel_forge.core.config import get_settings
from novel_forge.core.domain.story_defaults import (
    DEFAULT_GENRE,
    DEFAULT_LONG_PREMISE,
    DEFAULT_TONE,
)
from novel_forge.obs.project_logger import ProjectRunLogger
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.progress import cli_step_labels
from novel_forge.workspace.contracts import InitLongRequest
from novel_forge.workspace.execution import execute_init_long
from novel_forge.workspace.runtime import RuntimeServices

console = Console()

_INIT_PROGRESS_STEP_NAMES = cli_step_labels("init_long")


def init_long(
    premise: Optional[str] = typer.Option(None, help="Story premise"),
    genre: Optional[str] = typer.Option(None, help="Genre"),
    tone: Optional[str] = typer.Option(None, help="Tone"),
    title: Optional[str] = typer.Option(None, help="Working title (optional)"),
    language: Optional[str] = typer.Option(None, help="Output language code, e.g. zh/en"),
    characters_hint: Optional[str] = typer.Option(None, help="Main characters hint (optional)"),
    world_hint: Optional[str] = typer.Option(None, help="World/setting hint (optional)"),
    conflict_hint: Optional[str] = typer.Option(None, help="Core conflict hint (optional)"),
    pov_hint: Optional[str] = typer.Option(None, help="POV hint (optional)"),
    opening_style: Optional[str] = typer.Option(None, help="Opening style hint (optional)"),
    ending_style: Optional[str] = typer.Option(None, help="Ending style hint (optional)"),
    extra_instructions: Optional[str] = typer.Option(
        None, help="Extra creative constraints (optional)"
    ),
    polish_hint: Optional[str] = typer.Option(None, "--polish-hint", help="立项后大纲精修指令"),
    research_enabled: Optional[bool] = typer.Option(
        None,
        "--research/--no-research",
        help="是否在规格确认后执行联网资料检索",
    ),
    research_provider: Optional[str] = typer.Option(
        None,
        "--research-provider",
        help=(
            "资料检索 provider: auto / noop / tavily / brave / searxng / "
            "http_json / bailian_web_search / mcp_search"
        ),
    ),
    research_query_hint: Optional[str] = typer.Option(
        None,
        "--research-query-hint",
        help="补充资料检索方向",
    ),
    regenerate_outline: bool = typer.Option(
        False,
        "--regenerate-outline",
        help="隔离现有大纲及下游初始化产物，并从已有蓝图重新生成章节大纲",
    ),
    blueprint_element_preferences: Optional[str] = typer.Option(
        None,
        "--element-prefs",
        "--blueprint-element-preferences",
        help="蓝图要素偏好 JSON 对象",
    ),
    total_chapters: Optional[int] = typer.Option(None, "--total-chapters", help="目标章节数（建议10-30章）"),
    words_per_chapter: Optional[int] = typer.Option(None, "--words-per-chapter", help="每章目标字数"),
    volume_mode: Optional[str] = typer.Option(
        None, "--volume-mode", help="分卷模式: auto / on / off"
    ),
    chapters_per_volume: Optional[int] = typer.Option(
        None, "--chapters-per-volume", help="每卷章节数（0=自动）", min=0
    ),
    mock: Optional[bool] = typer.Option(
        None, "--mock/--no-mock", help="Force mock mode (for testing/debugging)"
    ),
    project_id: Optional[str] = typer.Option(None, help="Project ID"),
    # 长篇 WAVE 单次连贯起稿，--edit-rounds 已下线（保留接受以兼容旧命令但不生效）。
    verbose: Optional[bool] = typer.Option(
        None, "--verbose/--no-verbose", "-v", help="Show detailed output for each pipeline step",
    ),
    config: Optional[str] = typer.Option(None, "--config", help="运行参数配置文件（JSON）"),
) -> None:
    """Initialize a long-mode novel project."""
    cfg, cfg_path = _load_cli_config(config)
    settings = get_settings()
    resolved_premise = resolve_str_option(
        premise, cfg, section="init_long", key="premise", default=DEFAULT_LONG_PREMISE,
    )
    resolved_genre = resolve_str_option(
        genre, cfg, section="init_long", key="genre", default=DEFAULT_GENRE,
    )
    resolved_tone = resolve_str_option(
        tone, cfg, section="init_long", key="tone", default=DEFAULT_TONE,
    )
    resolved_spec_options = _resolve_story_spec_options(
        cfg,
        section="init_long",
        title=title,
        language=language,
        characters_hint=characters_hint,
        world_hint=world_hint,
        conflict_hint=conflict_hint,
        pov_hint=pov_hint,
        opening_style=opening_style,
        ending_style=ending_style,
        extra_instructions=extra_instructions,
    )
    resolved_polish_hint = resolve_str_option(
        polish_hint, cfg, section="init_long", key="polish_hint", default="",
    )
    resolved_research_enabled = resolve_bool_option(
        research_enabled,
        cfg,
        section="init_long",
        key="research_enabled",
        default=False,
    )
    resolved_research_provider = resolve_str_option(
        research_provider,
        cfg,
        section="init_long",
        key="research_provider",
        default="auto",
    )
    resolved_research_query_hint = resolve_str_option(
        research_query_hint,
        cfg,
        section="init_long",
        key="research_query_hint",
        default="",
    )
    resolved_blueprint_element_preferences = resolve_json_dict_option(
        blueprint_element_preferences,
        cfg,
        section="init_long",
        key="blueprint_element_preferences",
        default={},
    )
    resolved_total_chapters = resolve_int_option(
        total_chapters, cfg, section="init_long", key="total_chapters", default=20, minimum=1,
    )
    resolved_words_per_chapter = resolve_int_option(
        words_per_chapter, cfg, section="init_long", key="words_per_chapter", default=3000, minimum=500,
    )
    resolved_volume_mode = cast(
        Literal["auto", "on", "off"],
        resolve_choice_option(
            volume_mode, cfg, section="init_long", key="volume_mode",
            default="auto", choices=("auto", "on", "off"),
        ),
    )
    resolved_chapters_per_volume = resolve_int_option(
        chapters_per_volume, cfg, section="init_long", key="chapters_per_volume", default=0, minimum=0,
    )
    resolved_controls = _resolve_common_cli_controls(
        cfg,
        section="init_long",
        mock=mock,
        project_id=project_id,
        verbose=verbose,
    )
    resolved_mock = resolved_controls["mock"]
    resolved_project_id = resolved_controls["project_id"]
    resolved_verbose = resolved_controls["verbose"]
    run_logger: ProjectRunLogger | None = None
    run_runtime: RuntimeServices | None = None
    step_history: list[str] = []

    async def _run() -> None:
        nonlocal run_logger, run_runtime
        if cfg_path is not None:
            console.print(f"[dim]📁 使用运行配置: {cfg_path}[/dim]")
        runtime = _create_runtime_services(settings=settings, mock=resolved_mock)
        run_runtime = runtime

        pid = resolved_project_id or runtime.create_project_id("long")
        layout = ProjectLayout(runtime.storage.ensure_project_dir(pid))
        layout.ensure_dirs()
        run_logger = ProjectRunLogger(
            layout=layout,
            project_id=pid,
            command="init-long",
            keep_runs=settings.log_keep_runs,
            metadata={
                "config_path": str(cfg_path) if cfg_path is not None else None,
                "mock": resolved_mock,
                "premise": resolved_premise,
                "genre": resolved_genre,
                "tone": resolved_tone,
                "spec_options": resolved_spec_options,
                "total_chapters": resolved_total_chapters,
                "words_per_chapter": resolved_words_per_chapter,
                "volume_mode": resolved_volume_mode,
                "chapters_per_volume": resolved_chapters_per_volume,
                "polish_hint": resolved_polish_hint,
                "research_enabled": resolved_research_enabled,
                "research_provider": resolved_research_provider,
                "research_query_hint": resolved_research_query_hint,
                "regenerate_outline": regenerate_outline,
                "blueprint_element_preferences": resolved_blueprint_element_preferences,
                "verbose": resolved_verbose,
            },
        )
        with run_logger.activate(), runtime.router.observe(run_logger.record_router_event):
            console.print(f"\n[bold cyan]🚀 初始化长篇项目:[/bold cyan] {pid}")
            console.print(
                f"[dim]  • 类型: {resolved_genre} | 基调: {resolved_tone} | "
                f"语言: {resolved_spec_options['language']}[/dim]"
            )
            console.print(
                f"[dim]  • 目标: {resolved_total_chapters}章 × {resolved_words_per_chapter}字/章 = "
                f"{resolved_total_chapters * resolved_words_per_chapter:,}字[/dim]\n"
            )
            console.print(
                f"[dim]  • 分卷: {resolved_volume_mode} | 每卷章节: "
                f"{resolved_chapters_per_volume if resolved_chapters_per_volume > 0 else settings.long_default_chapters_per_volume}[/dim]\n"
            )
            console.print(f"[dim]  • 运行日志: {run_logger.run_dir}[/dim]\n")
            
            with _new_cli_progress(console) as progress:
                main_task = progress.add_task("[cyan]正在初始化...", total=5)
                progress_callback = _make_progress_tracker(
                    progress=progress,
                    task_id=main_task,
                    step_names=_INIT_PROGRESS_STEP_NAMES,
                    verbose=resolved_verbose,
                    verbose_callback_factory=_make_step_callback,
                )

                def _capture_step(step: str, _data: Any) -> None:
                    step_history.append(step)

                track_step = _compose_step_callbacks(
                    progress_callback,
                    run_logger.log_step,
                    _capture_step,
                )

                execution = await execute_init_long(
                    runtime,
                    InitLongRequest(
                        project_id=pid,
                        premise=resolved_premise,
                        genre=resolved_genre,
                        tone=resolved_tone,
                        total_chapters=resolved_total_chapters,
                        words_per_chapter=resolved_words_per_chapter,
                        volume_mode=resolved_volume_mode,
                        chapters_per_volume=resolved_chapters_per_volume,
                        polish_hint=resolved_polish_hint,
                        research_enabled=resolved_research_enabled,
                        research_provider=resolved_research_provider,
                        research_query_hint=resolved_research_query_hint,
                        regenerate_outline=regenerate_outline,
                        blueprint_element_preferences=resolved_blueprint_element_preferences,
                        **resolved_spec_options,
                    ),
                    on_step_progress=track_step,
                )
                result = execution.result
                
                progress.update(main_task, completed=5, description="[bold green]✨ 项目初始化完成！")

            console.print(
                Panel(
                    f"Title: {result.story_bible.title}\n"
                    f"Premise: {result.story_bible.premise}\n"
                    f"Characters: {', '.join(c.name for c in result.character_bible.characters)}\n"
                    f"Chapters planned: {result.outline.total_chapters}\n"
                    f"Volume mode: {'ON' if result.outline.volume_mode else 'OFF'}"
                    f"{f' ({len(result.outline.volumes)} volumes)' if result.outline.volume_mode else ''}",
                    title=f"[bold]Long Project Initialized — {pid}[/bold]",
                )
            )
            console.print(f"[dim]Output saved to: {runtime.storage.root / pid}[/dim]")
            run_logger.finalize(
                status="success",
                result={
                    "project_id": pid,
                    "title": result.story_bible.title,
                    "characters": [c.name for c in result.character_bible.characters],
                    "total_chapters": result.outline.total_chapters,
                    "volume_mode": result.outline.volume_mode,
                    "volume_count": len(result.outline.volumes),
                },
                trace_summary=result.trace_summary,
            )

    try:
        asyncio.run(_run())
    except KeyboardInterrupt as exc:
        if run_logger is not None:
            run_logger.finalize(status="interrupted")
        console.print("\n")
        console.print(
            Panel(
                "[yellow]⚠️  初始化已中断[/yellow]\n\n"
                "已完成的步骤已自动保存（spec/bible/outline/canon）。你可以：\n"
                "• 再次运行相同的 init-long 命令，系统会从断点继续\n"
                "• 查看已生成的配置文件\n"
                f"• 数据位置：[cyan]{(run_runtime.storage.root if run_runtime is not None else _get_storage().root) / (resolved_project_id or 'long_*')}[/cyan]",
                title="💡 中断提示",
                border_style="yellow",
            )
        )
        raise typer.Exit(130) from exc
    except Exception as exc:
        if run_logger is not None:
            run_logger.finalize(
                status="error",
                trace_summary=_build_error_trace_summary(step_history),
                error=exc,
            )
        _print_cli_error_panel(
            command="init-long",
            exc=exc,
            run_dir=run_logger.run_dir if run_logger is not None else None,
        )
        raise typer.Exit(1) from exc


def register(app: typer.Typer) -> None:
    """Register long-project initialization commands."""
    app.command(name="init-long")(init_long)
