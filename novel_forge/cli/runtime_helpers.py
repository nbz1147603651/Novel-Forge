"""Runtime/config helpers shared by CLI commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional, TypedDict

import typer
from rich.console import Console

from novel_forge.cli.runtime_config import (
    RuntimeConfigError,
    load_runtime_config,
    resolve_bool_option,
    resolve_int_option,
    resolve_str_option,
)
from novel_forge.core.config import Settings, get_settings
from novel_forge.gateway.factory import ModelRouterBuilder, TaskRoutingParseError
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.factory import create_storage_backend
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.pipeline.chapter_runner import ChapterRunner
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.workspace.runtime import RuntimeServices, create_runtime_services

console = Console()


def _build_router(*, mock: bool = False) -> ModelRouter:
    """Build a ModelRouter from current settings."""
    settings = get_settings()
    builder = ModelRouterBuilder(settings)
    try:
        router = builder.build(mock=mock)
    except TaskRoutingParseError as exc:
        raise typer.BadParameter(str(exc), param_hint="NOVEL_FORGE_TASK_ROUTING") from exc
    if mock:
        console.print("[dim]🧪 Mock 模式（调试用，返回预设内容）[/dim]")
    return router


def _get_storage() -> FileSystemStorage:
    settings = get_settings()
    return create_storage_backend(settings)


def _create_runtime_services(
    *,
    settings: Settings | None = None,
    mock: bool = False,
) -> RuntimeServices:
    """Build a CLI runtime via the shared workspace runtime assembly path."""
    resolved_settings = settings or get_settings()
    try:
        runtime = create_runtime_services(resolved_settings, mock=mock)
    except TaskRoutingParseError as exc:
        raise typer.BadParameter(str(exc), param_hint="NOVEL_FORGE_TASK_ROUTING") from exc
    if mock:
        console.print("[dim]🧪 Mock 模式（调试用，返回预设内容）[/dim]")
    return runtime


def _load_cli_config(config_path: Optional[str]) -> tuple[dict[str, Any], Path | None]:
    try:
        return load_runtime_config(config_path)
    except RuntimeConfigError as exc:
        raise typer.BadParameter(str(exc), param_hint="--config") from exc


def _resolve_config_value(resolver: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Resolve a value from runtime config and map config errors to CLI errors."""
    try:
        return resolver(*args, **kwargs)
    except RuntimeConfigError as exc:
        raise typer.BadParameter(str(exc), param_hint="--config") from exc


_STORY_SPEC_OPTION_DEFAULTS: dict[str, str] = {
    "title": "",
    "language": "zh",
    "characters_hint": "",
    "world_hint": "",
    "conflict_hint": "",
    "pov_hint": "",
    "opening_style": "",
    "ending_style": "",
    "extra_instructions": "",
}


def _resolve_story_spec_options(
    cfg: dict[str, Any],
    *,
    section: str,
    title: Optional[str],
    language: Optional[str],
    characters_hint: Optional[str],
    world_hint: Optional[str],
    conflict_hint: Optional[str],
    pov_hint: Optional[str],
    opening_style: Optional[str],
    ending_style: Optional[str],
    extra_instructions: Optional[str],
) -> dict[str, str]:
    """Resolve shared StorySpec optional fields from CLI/config."""
    cli_values: dict[str, Optional[str]] = {
        "title": title,
        "language": language,
        "characters_hint": characters_hint,
        "world_hint": world_hint,
        "conflict_hint": conflict_hint,
        "pov_hint": pov_hint,
        "opening_style": opening_style,
        "ending_style": ending_style,
        "extra_instructions": extra_instructions,
    }

    resolved: dict[str, str] = {}
    for key, cli_value in cli_values.items():
        resolved[key] = resolve_str_option(
            cli_value,
            cfg,
            section=section,
            key=key,
            default=_STORY_SPEC_OPTION_DEFAULTS[key],
        )
    return resolved


def _build_story_spec_input(
    *,
    theme: str,
    genre: str,
    tone: str,
    length_target: int,
    spec_options: dict[str, str],
) -> dict[str, Any]:
    """Compose StorySpec input payload for SpecStep/short runner."""
    return {
        "theme": theme,
        "genre": genre,
        "tone": tone,
        "length_target": length_target,
        **spec_options,
    }


class CommonControlOptions(TypedDict):
    mock: bool
    project_id: str
    edit_rounds: int
    verbose: bool


def _resolve_common_cli_controls(
    cfg: dict[str, Any],
    *,
    section: str,
    mock: Optional[bool],
    project_id: Optional[str],
    verbose: Optional[bool],
    edit_rounds: Optional[int] = None,
    edit_rounds_default: int = 0,
    edit_rounds_minimum: int = 0,
    edit_rounds_maximum: int | None = None,
) -> CommonControlOptions:
    """Resolve common CLI controls shared by run-short/init-long."""
    return {
        "mock": resolve_bool_option(
            mock,
            cfg,
            section=section,
            key="mock",
            default=False,
        ),
        "project_id": resolve_str_option(
            project_id,
            cfg,
            section=section,
            key="project_id",
            default="",
        ),
        "edit_rounds": resolve_int_option(
            edit_rounds,
            cfg,
            section=section,
            key="edit_rounds",
            default=edit_rounds_default,
            minimum=edit_rounds_minimum,
            maximum=edit_rounds_maximum,
        ),
        "verbose": resolve_bool_option(
            verbose,
            cfg,
            section=section,
            key="verbose",
            default=False,
        ),
    }


def _create_chapter_runner(
    *,
    router: ModelRouter,
    builder: PromptBuilder,
    storage: FileSystemStorage,
    settings: Settings,
    on_step_progress: Callable[[str, Any], None] | None = None,
) -> ChapterRunner:
    """Build ChapterRunner with centralized CLI/runtime settings mapping."""
    return ChapterRunner.from_settings(
        router,
        builder,
        storage,
        settings,
        on_step_progress=on_step_progress,
    )
