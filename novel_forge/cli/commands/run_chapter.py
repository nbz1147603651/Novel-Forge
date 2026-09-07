"""Chapter-generation CLI command."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import typer
from rich.console import Console
from rich.panel import Panel

from novel_forge.cli.chapter_helpers import _detect_next_chapter, _load_outline_total_chapters
from novel_forge.cli.chapter_runner import AutoChapterRunner, ChapterRunner
from novel_forge.cli.error_handling import _print_cli_error_panel
from novel_forge.cli.runtime_config import (
    resolve_bool_option,
    resolve_choice_option,
    resolve_int_option,
    resolve_str_option,
)
from novel_forge.cli.runtime_helpers import (
    _create_runtime_services,
    _get_storage,
    _load_cli_config,
    _resolve_config_value,
)
from novel_forge.common.plot_guard import PlotGuardHandler
from novel_forge.core.config import get_settings
from novel_forge.obs.project_logger import ProjectRunLogger
from novel_forge.persistence.models import ProjectLayout

console = Console()


def run_chapter(
    project_id: Optional[str] = typer.Option(None, help="Project ID"),
    chapter: Optional[int] = typer.Option(None, help="Chapter number to generate"),
    auto: Optional[bool] = typer.Option(
        None, "--auto/--no-auto", "-a",
        help="Auto-generate subsequent chapters until end (outline total or --end-chapter)",
    ),
    end_chapter: Optional[int] = typer.Option(
        None, "--end-chapter", help="Auto mode end chapter (0 = use outline total_chapters)", min=0,
    ),
    ai_judge_apply_mode: Optional[str] = typer.Option(
        None, "--ai-judge-apply-mode",
        help="AI Judge 应用策略: assist(先给建议再确认) / trust(自动应用)",
    ),
    force: Optional[bool] = typer.Option(
        None, "--force/--no-force", "-f",
        help="Force regenerate if chapter already exists (auto-rollback canon)",
    ),
    mock: Optional[bool] = typer.Option(
        None, "--mock/--no-mock", help="Force mock mode (for testing/debugging)",
    ),
    verbose: Optional[bool] = typer.Option(
        None, "--verbose/--no-verbose", "-v", help="Show detailed output for each pipeline step",
    ),
    config: Optional[str] = typer.Option(None, "--config", help="运行参数配置文件（JSON）"),
) -> None:
    """Generate chapter(s) for a long-mode project.

    Use --force/-f to regenerate an existing chapter (will auto-rollback canon to previous state).
    In --auto mode, major plot deviations pause before generating next chapter.
    """
    cfg, cfg_path = _load_cli_config(config)

    # Resolve configuration values
    resolved_project_id = _resolve_config_value(
        resolve_str_option, project_id, cfg, section="run_chapter", key="project_id", default="",
    )
    if not resolved_project_id:
        raise typer.BadParameter("project_id 未提供；请使用 --project-id 或在配置文件 run_chapter.project_id 设置")

    requested_chapter = _resolve_config_value(
        resolve_int_option, chapter, cfg, section="run_chapter", key="chapter", default=0, minimum=0,
    )

    # Infer chapter number if not provided
    inferred_chapter = requested_chapter == 0
    if inferred_chapter:
        layout = ProjectLayout(_get_storage().project_path(resolved_project_id))
        resolved_chapter = _detect_next_chapter(layout)
    else:
        resolved_chapter = requested_chapter

    settings = get_settings()

    # Resolve all configuration options
    resolved_auto = _resolve_config_value(
        resolve_bool_option, auto, cfg, section="run_chapter", key="auto", default=False,
    )
    resolved_end_chapter = _resolve_config_value(
        resolve_int_option, end_chapter, cfg, section="run_chapter", key="end_chapter", default=0, minimum=0,
    )
    resolved_ai_judge_apply_mode = _resolve_config_value(
        resolve_choice_option, ai_judge_apply_mode, cfg, section="run_chapter",
        key="ai_judge_apply_mode", default=settings.long_ai_judge_apply_mode, choices=("assist", "trust"),
    )
    resolved_force = _resolve_config_value(
        resolve_bool_option, force, cfg, section="run_chapter", key="force", default=False,
    )
    resolved_mock = _resolve_config_value(
        resolve_bool_option, mock, cfg, section="run_chapter", key="mock", default=False,
    )
    resolved_verbose = _resolve_config_value(
        resolve_bool_option, verbose, cfg, section="run_chapter", key="verbose", default=False,
    )

    # Track execution state
    current_state = {"chapter": resolved_chapter}
    completed_chapters: list[int] = []
    chapter_trace_summaries: list[dict[str, Any]] = []
    run_logger: ProjectRunLogger | None = None
    run_runtime: Any = None

    async def _run() -> None:
        nonlocal run_logger, run_runtime, completed_chapters, chapter_trace_summaries

        if cfg_path is not None:
            console.print(f"[dim]📁 使用运行配置: {cfg_path}[/dim]")
        if inferred_chapter:
            console.print(f"[dim]🧭 未显式指定章节，自动推断为第 {resolved_chapter} 章[/dim]")

        runtime = _create_runtime_services(settings=settings, mock=resolved_mock)
        run_runtime = runtime

        layout = ProjectLayout(runtime.storage.project_path(resolved_project_id))
        layout.ensure_dirs()

        # Initialize run logger
        run_logger = ProjectRunLogger(
            layout=layout,
            project_id=resolved_project_id,
            command="run-chapter",
            keep_runs=settings.log_keep_runs,
            metadata={
                "config_path": str(cfg_path) if cfg_path is not None else None,
                "mock": resolved_mock,
                "chapter": resolved_chapter,
                "inferred_chapter": inferred_chapter,
                "auto": resolved_auto,
                "end_chapter": resolved_end_chapter,
                "ai_judge_apply_mode": resolved_ai_judge_apply_mode,
                "force": resolved_force,
                "verbose": resolved_verbose,
            },
        )
        try:
            with run_logger.activate(), runtime.router.observe(run_logger.record_router_event):
                console.print(f"[dim]🪵 运行日志: {run_logger.run_dir}[/dim]")

                # Handle auto mode
                if resolved_auto:
                    await _run_auto_mode(
                        runtime=runtime,
                        layout=layout,
                        resolved_chapter=resolved_chapter,
                        resolved_end_chapter=resolved_end_chapter,
                        resolved_force=resolved_force,
                        resolved_mock=resolved_mock,
                        resolved_verbose=resolved_verbose,
                        resolved_ai_judge_apply_mode=resolved_ai_judge_apply_mode,
                        run_logger=run_logger,
                        current_state=current_state,
                    )
                else:
                    await _run_single_mode(
                        runtime=runtime,
                        layout=layout,
                        resolved_chapter=resolved_chapter,
                        resolved_force=resolved_force,
                        resolved_mock=resolved_mock,
                        resolved_verbose=resolved_verbose,
                        resolved_ai_judge_apply_mode=resolved_ai_judge_apply_mode,
                        run_logger=run_logger,
                        current_state=current_state,
                    )

                # Finalize run logger
                if run_logger is not None:
                    run_logger.finalize(
                        status="success",
                        result={
                            "project_id": resolved_project_id,
                            "requested_start_chapter": resolved_chapter,
                            "auto": resolved_auto,
                            "completed_chapters": completed_chapters,
                            "last_chapter": completed_chapters[-1] if completed_chapters else None,
                        },
                        trace_summary={"chapters": chapter_trace_summaries},
                    )
        finally:
            await runtime.shutdown()

    async def _run_auto_mode(
        *,
        runtime: Any,
        layout: ProjectLayout,
        resolved_chapter: int,
        resolved_end_chapter: int,
        resolved_force: bool,
        resolved_mock: bool,
        resolved_verbose: bool,
        resolved_ai_judge_apply_mode: str,
        run_logger: ProjectRunLogger | None,
        current_state: dict[str, int],
    ) -> None:
        """Run in auto mode (generate multiple chapters)."""
        nonlocal completed_chapters, chapter_trace_summaries

        try:
            outline_total = _load_outline_total_chapters(layout, runtime.storage)
        except FileNotFoundError as exc:
            console.print("[red]❌ Error:[/red] 未找到 outline.json，无法使用自动模式。")
            raise typer.Exit(1) from exc

        auto_end = outline_total if resolved_end_chapter == 0 else resolved_end_chapter

        if auto_end > outline_total:
            console.print(
                f"[red]❌ Error:[/red] end_chapter ({auto_end}) 超过 outline.total_chapters ({outline_total})"
            )
            raise typer.Exit(1)
        if resolved_chapter > auto_end:
            console.print(
                f"[red]❌ Error:[/red] 起始章节 {resolved_chapter} 已超过结束章节 {auto_end}"
            )
            raise typer.Exit(1)

        console.print(f"[dim]🤖 自动模式: 第 {resolved_chapter} 章 → 第 {auto_end} 章[/dim]")

        auto_runner = AutoChapterRunner(
            project_id=resolved_project_id,
            start_chapter=resolved_chapter,
            end_chapter=auto_end,
            force=resolved_force,
            mock=resolved_mock,
            verbose=resolved_verbose,
            ai_judge_apply_mode=resolved_ai_judge_apply_mode,
            run_logger=run_logger,
        )

        result = await auto_runner.run(runtime=runtime)
        completed_chapters = result["completed_chapters"]
        chapter_trace_summaries = result["chapter_trace_summaries"]
        current_state["chapter"] = result["last_chapter"] or resolved_chapter

    async def _run_single_mode(
        *,
        runtime: Any,
        layout: ProjectLayout,
        resolved_chapter: int,
        resolved_force: bool,
        resolved_mock: bool,
        resolved_verbose: bool,
        resolved_ai_judge_apply_mode: str,
        run_logger: ProjectRunLogger | None,
        current_state: dict[str, int],
    ) -> None:
        """Run in single chapter mode."""
        nonlocal completed_chapters, chapter_trace_summaries

        chapter_runner = ChapterRunner(
            project_id=resolved_project_id,
            force=resolved_force,
            mock=resolved_mock,
            verbose=resolved_verbose,
            auto_mode=False,
            ai_judge_apply_mode=resolved_ai_judge_apply_mode,
            run_logger=run_logger,
        )

        result = await chapter_runner.run(resolved_chapter, runtime=runtime)
        if result is None:
            completed_chapters = []
            chapter_trace_summaries = []
            current_state["chapter"] = resolved_chapter
            return
        completed_chapters = [resolved_chapter]
        chapter_trace_summaries = [{"chapter": resolved_chapter, "trace": result.trace_summary}]

        # Handle plot guard for single mode
        plot_guard_handler = PlotGuardHandler(
            layout=layout,
            storage=runtime.storage,
            router=runtime.router,
            builder=runtime.builder,
            settings=runtime.settings,
            ai_judge_apply_mode=resolved_ai_judge_apply_mode,
            auto_mode=False,
        )
        await plot_guard_handler.handle_major_deviation(
            chapter_number=resolved_chapter,
            result=result,
        )

        current_state["chapter"] = resolved_chapter

    # Run the async function and handle errors
    try:
        asyncio.run(_run())
    except KeyboardInterrupt as exc:
        # Best-effort graceful shutdown of HTTP connections to avoid stalling
        if run_runtime is not None:
            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(run_runtime.shutdown())
                loop.close()
            except Exception:
                pass
        _handle_keyboard_interrupt(
            exc=exc,
            run_logger=run_logger,
            resolved_project_id=resolved_project_id,
            completed_chapters=completed_chapters,
            current_state=current_state,
            run_runtime=run_runtime,
        )
    except Exception as exc:
        if run_runtime is not None:
            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(run_runtime.shutdown())
                loop.close()
            except Exception:
                pass
        _handle_error(
            exc=exc,
            run_logger=run_logger,
            resolved_project_id=resolved_project_id,
            completed_chapters=completed_chapters,
            current_state=current_state,
        )


def _handle_keyboard_interrupt(
    *,
    exc: KeyboardInterrupt,
    run_logger: ProjectRunLogger | None,
    resolved_project_id: str,
    completed_chapters: list[int],
    current_state: dict[str, Any],
    run_runtime: Any,
) -> None:
    """Handle keyboard interrupt gracefully."""
    if run_logger is not None:
        run_logger.finalize(
            status="interrupted",
            result={
                "project_id": resolved_project_id,
                "completed_chapters": completed_chapters,
                "current_chapter": current_state["chapter"],
            },
            trace_summary={},
        )

    console.print("\n")
    console.print(
        Panel(
            "[yellow]⚠️  章节生成已中断[/yellow]\n\n"
            "已完成的步骤已自动保存（plan/draft/edit）。你可以：\n"
            f"• 再次运行 [cyan]novel-forge run-chapter --project-id {resolved_project_id} --chapter {current_state['chapter']}[/cyan]\n"
            "• 该章节会重新完整生成（当前不支持章节内步骤级断点跳过）\n"
            "• 若提示章节已存在，可追加 [cyan]--force[/cyan] 自动回滚后重生成\n"
            "• 查看已生成的草稿文件\n"
            f"• 数据位置：[cyan]{(run_runtime.storage.root if run_runtime is not None else _get_storage().root) / resolved_project_id}[/cyan]",
            title="💡 中断提示",
            border_style="yellow",
        )
    )
    raise typer.Exit(130) from exc


def _handle_error(
    *,
    exc: Exception,
    run_logger: ProjectRunLogger | None,
    resolved_project_id: str,
    completed_chapters: list[int],
    current_state: dict[str, Any],
) -> None:
    """Handle errors gracefully."""
    if run_logger is not None:
        run_logger.finalize(
            status="error",
            result={
                "project_id": resolved_project_id,
                "completed_chapters": completed_chapters,
                "current_chapter": current_state["chapter"],
            },
            trace_summary={},
            error=exc,
        )
    _print_cli_error_panel(
        command="run-chapter",
        exc=exc,
        run_dir=run_logger.run_dir if run_logger is not None else None,
    )
    raise typer.Exit(1) from exc


def register(app: typer.Typer) -> None:
    """Register long-project chapter commands."""
    app.command(name="run-chapter")(run_chapter)
