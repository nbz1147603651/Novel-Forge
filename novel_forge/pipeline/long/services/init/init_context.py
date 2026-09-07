"""Init service context and runner protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.gateway.router import ModelRouter
from novel_forge.obs.tracer import PipelineTrace
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout

if TYPE_CHECKING:
    from novel_forge.pipeline.chapter_runner import ChapterRunnerConfig


class RunnerProtocol(Protocol):
    """Minimal interface needed from ChapterRunner for init_long operations."""

    _storage: FileSystemStorage
    _router: ModelRouter
    _builder: Any
    _settings: Settings
    _config: ChapterRunnerConfig
    _on_step: Any

    async def _call_with_retry(
        self,
        task_type: TaskType,
        context: dict[str, Any],
        *,
        max_tokens: int,
        temperature: float,
        required_keys: tuple[str, ...] = (),
        max_retries: int = 2,
        prior_messages: list[dict[str, str]] | None = None,
        thinking: bool = False,
        multi_turn: bool = False,
        _capture_raw: list[str] | None = None,
        include_contract_required_keys: bool = True,
    ) -> dict[str, Any]: ...

    def _coerce_character_bible(self, payload: Any) -> Any: ...

    def _is_outline_option_enabled_for_task(
        self,
        *,
        capability: str,
        enabled: bool,
        allowed_providers_raw: str,
        allowed_models_raw: str,
        task_type: TaskType,
    ) -> bool: ...


@dataclass
class InitLongServiceContext:
    """Bundles all dependencies and configuration for init_long operations.

    This replaces the pattern of accessing runner._xxx private attributes.
    """

    storage: FileSystemStorage
    router: ModelRouter
    builder: Any
    settings: Settings
    config: Any  # ChapterRunnerConfig — typed as Any to break circular TYPE_CHECKING import
    layout: ProjectLayout
    trace: PipelineTrace
    on_step: Any

    # Helper methods (passed as callables)
    call_with_retry: Any = None
    coerce_character_bible: Any = None
    is_outline_option_enabled: Any = None
    memory_context: Any = None


def build_init_context(
    runner: RunnerProtocol,
    project_id: str,
) -> InitLongServiceContext:
    """Build an explicit context from runner dependencies."""
    layout = ProjectLayout(runner._storage.ensure_project_dir(project_id))
    layout.ensure_dirs()
    trace = PipelineTrace()

    return InitLongServiceContext(
        storage=runner._storage,
        router=runner._router,
        builder=runner._builder,
        settings=runner._settings,
        config=runner._config,
        layout=layout,
        trace=trace,
        on_step=runner._on_step,
        call_with_retry=runner._call_with_retry,
        coerce_character_bible=runner._coerce_character_bible,
        is_outline_option_enabled=runner._is_outline_option_enabled_for_task,
        memory_context=getattr(runner, "memory_context", None),
    )
