"""Reusable CLI progress helpers."""

from __future__ import annotations

from typing import Any, Callable

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)


def _new_cli_progress(console: Console) -> Progress:
    """Create a unified progress bar style for CLI commands."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(complete_style="green", finished_style="bold green"),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    )


def _make_progress_tracker(
    *,
    progress: Progress,
    task_id: TaskID,
    step_names: dict[str, str],
    verbose: bool,
    verbose_callback_factory: Callable[[bool], Callable[[str, Any], None]],
) -> Callable[[str, Any], None]:
    """Create a reusable progress+verbose callback for pipeline steps."""
    verbose_callback = verbose_callback_factory(True) if verbose else None

    def track_step(step: str, data: Any) -> None:
        if verbose_callback is not None:
            verbose_callback(step, data)

        description = step_names.get(step)
        if description:
            progress.update(task_id, advance=1, description=f"[green]{description}")
            return

        if step.startswith("plan_outline_batch_"):
            parts = step.split("_")
            if len(parts) >= 5:
                batch_start = parts[3]
                batch_end = parts[4]
                chapters_done = data.get("chapters_done", "?") if isinstance(data, dict) else "?"
                chapters_total = data.get("chapters_total", "?") if isinstance(data, dict) else "?"
                progress.update(
                    task_id,
                    description=f"[blue]📋 大纲批次 {batch_start}–{batch_end} 完成 ({chapters_done}/{chapters_total}章)",
                )
            return

        if step.startswith("edit_"):
            round_num = step.split("_")[1]
            progress.update(
                task_id,
                advance=1,
                description=f"[yellow]✅ 编辑轮次 {round_num} 完成",
            )

    return track_step


def _compose_step_callbacks(
    *callbacks: Callable[[str, Any], None] | None,
) -> Callable[[str, Any], None]:
    """Run multiple step callbacks in sequence."""
    active_callbacks = [cb for cb in callbacks if cb is not None]

    def combined(step: str, data: Any) -> None:
        for callback in active_callbacks:
            callback(step, data)

    return combined
