"""Short-story generation CLI command."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

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
    resolve_int_option,
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
    DEFAULT_SHORT_THEME,
    DEFAULT_TONE,
)
from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.obs.project_logger import ProjectRunLogger
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.progress import cli_step_labels
from novel_forge.workspace.contracts import RunShortRequest
from novel_forge.workspace.execution import execute_run_short
from novel_forge.workspace.runtime import RuntimeServices

console = Console()

_SHORT_PROGRESS_STEP_NAMES = cli_step_labels("run_short")


def run_short(
    theme: Optional[str] = typer.Option(None, help="Story theme"),
    genre: Optional[str] = typer.Option(None, help="Genre"),
    tone: Optional[str] = typer.Option(None, help="Tone"),
    length: Optional[int] = typer.Option(None, help="Target word count"),
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
    mock: Optional[bool] = typer.Option(
        None,
        "--mock/--no-mock",
        help="Force mock mode (for testing/debugging)",
    ),
    project_id: Optional[str] = typer.Option(None, help="Project ID (auto-generated if empty)"),
    edit_rounds: Optional[int] = typer.Option(
        None, help="Max edit/revision rounds (0 = skip editing)"
    ),
    writing_mode: Optional[str] = typer.Option(
        None,
        help="Writing mode: auto, whole_chapter, or scene_level",
    ),
    verbose: Optional[bool] = typer.Option(
        None,
        "--verbose/--no-verbose",
        "-v",
        help="Show detailed output for each pipeline step",
    ),
    config: Optional[str] = typer.Option(None, "--config", help="运行参数配置文件（JSON）"),
) -> None:
    """Generate a short story end-to-end."""

    cfg, cfg_path = _load_cli_config(config)
    settings = get_settings()
    resolved_theme = resolve_str_option(
        theme,
        cfg,
        section="run_short",
        key="theme",
        default=DEFAULT_SHORT_THEME,
    )
    resolved_genre = resolve_str_option(
        genre,
        cfg,
        section="run_short",
        key="genre",
        default=DEFAULT_GENRE,
    )
    resolved_tone = resolve_str_option(
        tone,
        cfg,
        section="run_short",
        key="tone",
        default=DEFAULT_TONE,
    )
    resolved_length = resolve_int_option(
        length,
        cfg,
        section="run_short",
        key="length",
        default=3000,
        minimum=100,
    )
    resolved_spec_options = _resolve_story_spec_options(
        cfg,
        section="run_short",
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
    resolved_controls = _resolve_common_cli_controls(
        cfg,
        section="run_short",
        mock=mock,
        project_id=project_id,
        edit_rounds=edit_rounds,
        verbose=verbose,
        edit_rounds_default=settings.short_max_edit_rounds,
        edit_rounds_minimum=0,
    )
    resolved_mock = resolved_controls["mock"]
    resolved_project_id = resolved_controls["project_id"]
    resolved_edit_rounds = resolved_controls["edit_rounds"]
    resolved_verbose = resolved_controls["verbose"]
    resolved_writing_mode = resolve_str_option(
        writing_mode,
        cfg,
        section="run_short",
        key="writing_mode",
        default="auto",
    )
    run_logger: ProjectRunLogger | None = None
    run_runtime: RuntimeServices | None = None
    step_history: list[str] = []

    async def _run() -> None:
        nonlocal run_logger, run_runtime
        if cfg_path is not None:
            console.print(f"[dim]📁 使用运行配置: {cfg_path}[/dim]")
        runtime = _create_runtime_services(settings=settings, mock=resolved_mock)
        run_runtime = runtime

        pid = resolved_project_id or runtime.create_project_id("short")
        layout = ProjectLayout(runtime.storage.ensure_project_dir(pid))
        layout.ensure_dirs()
        run_logger = ProjectRunLogger(
            layout=layout,
            project_id=pid,
            command="run-short",
            keep_runs=settings.log_keep_runs,
            metadata={
                "config_path": str(cfg_path) if cfg_path is not None else None,
                "mock": resolved_mock,
                "theme": resolved_theme,
                "genre": resolved_genre,
                "tone": resolved_tone,
                "length": resolved_length,
                "spec_options": resolved_spec_options,
                "edit_rounds": resolved_edit_rounds,
                "writing_mode": resolved_writing_mode,
                "verbose": resolved_verbose,
            },
        )
        with run_logger.activate(), runtime.router.observe(run_logger.record_router_event):
            console.print(f"\n[bold cyan]📝 生成短篇故事:[/bold cyan] {pid}")
            console.print(
                f"[dim]  • 类型: {resolved_genre} | 基调: {resolved_tone} | "
                f"语言: {resolved_spec_options['language']} | 目标字数: {resolved_length:,}[/dim]\n"
            )
            console.print(f"[dim]  • 运行日志: {run_logger.run_dir}[/dim]\n")

            total_steps = 3 + resolved_edit_rounds + 1

            with _new_cli_progress(console) as progress:
                main_task = progress.add_task("[cyan]生成短篇故事...", total=total_steps)
                progress_callback = _make_progress_tracker(
                    progress=progress,
                    task_id=main_task,
                    step_names=_SHORT_PROGRESS_STEP_NAMES,
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

                execution = await execute_run_short(
                    runtime,
                    RunShortRequest(
                        project_id=pid,
                        theme=resolved_theme,
                        genre=resolved_genre,
                        tone=resolved_tone,
                        length_target=resolved_length,
                        max_edit_rounds=resolved_edit_rounds,
                        writing_mode=resolved_writing_mode,
                        **resolved_spec_options,
                    ),
                    on_step_progress=track_step,
                )
                result = execution.result

                progress.update(
                    main_task,
                    completed=total_steps,
                    description="[bold green]✨ 短篇故事生成完成！",
                )

            console.print(
                Panel(
                    result.final_text[:500] + "\n..."
                    if len(result.final_text) > 500
                    else result.final_text,
                    title=f"[bold]Short Story — {pid}[/bold]",
                    subtitle=f"Score: {result.eval_report.overall_score}/10 | "
                    f"Words: {count_chapter_words(result.final_text)} | "
                    f"Passed: {'✅' if result.eval_report.passed else '❌'}",
                )
            )
            console.print(f"[dim]Output saved to: {runtime.storage.root / pid}[/dim]")
            run_logger.finalize(
                status="success",
                result={
                    "project_id": pid,
                    "word_count": count_chapter_words(result.final_text),
                    "score": result.eval_report.overall_score,
                    "passed": result.eval_report.passed,
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
                "[yellow]⚠️  生成已中断[/yellow]\n\n"
                "已完成的步骤已自动保存。你可以：\n"
                "• 查看已生成的中间文件\n"
                "• 使用相同的 project-id 重新运行（输入未变化时会从已完成步骤继续；输入变化时会自动清理旧产物后重跑）\n"
                f"• 数据位置：[cyan]{(run_runtime.storage.root if run_runtime is not None else _get_storage().root) / (resolved_project_id or 'short_*')}[/cyan]",
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
            command="run-short",
            exc=exc,
            run_dir=run_logger.run_dir if run_logger is not None else None,
        )
        raise typer.Exit(1) from exc


def register(app: typer.Typer) -> None:
    """Register short-story workflow commands."""
    app.command(name="run-short")(run_short)
