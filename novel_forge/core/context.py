"""Execution context objects for explicit dependency injection.

This module provides typed context objects that bundle dependencies
for specific execution scenarios, eliminating the need for services
to reach back into runner internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Literal

from novel_forge.common.interfaces import ModelRouterProtocol
from novel_forge.core.config import Settings
from novel_forge.core.schemas.bible import CharacterBible
from novel_forge.obs.tracer import PipelineTrace
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.prompts.builder import PromptBuilder

if TYPE_CHECKING:
    from novel_forge.pipeline.chapter_runner import ChapterRunnerConfig
    from novel_forge.story_kernel.merger import StoryKernelMerger
    from novel_forge.story_kernel.retriever import StoryKernelRetriever
    from novel_forge.story_kernel.rules import StoryKernelConsistencyRules


@dataclass
class LLMServiceContext:
    """Context for LLM service operations.
    
    Bundles the minimal dependencies needed for LLM calls.
    """
    router: ModelRouterProtocol
    builder: PromptBuilder
    settings: Settings
    on_step: Callable[[str, Any], None] = field(default_factory=lambda: lambda s, d: None)


@dataclass
class CanonContext:
    """Context for canon-related operations.
    
    Provides access to canon state management and retrieval.
    """
    merger: StoryKernelMerger
    rules: StoryKernelConsistencyRules
    retriever: StoryKernelRetriever
    storage: FileSystemStorage
    layout: ProjectLayout
    trace: PipelineTrace
    on_step: Callable[[str, Any], None] = field(default_factory=lambda: lambda s, d: None)


@dataclass
class OutlineContext:
    """Context for outline generation and manipulation.
    
    Bundles dependencies for outline-related services.
    """
    storage: FileSystemStorage
    builder: PromptBuilder
    router: ModelRouterProtocol
    settings: Settings
    layout: ProjectLayout
    trace: PipelineTrace
    on_step: Callable[[str, Any], None] = field(default_factory=lambda: lambda s, d: None)
    
    batch_size: int = 4
    thinking_enabled: bool = False
    multi_turn_enabled: bool = False


@dataclass
class ChapterExecutionContext:
    """Comprehensive context for chapter execution.
    
    This is the main context object passed to chapter flow orchestration,
    containing all dependencies and configuration needed for the pipeline.
    """
    storage: FileSystemStorage
    router: ModelRouterProtocol
    builder: PromptBuilder
    settings: Settings
    config: 'ChapterRunnerConfig'
    
    merger: StoryKernelMerger
    rules: StoryKernelConsistencyRules
    
    on_step: Callable[[str, Any], None] = field(default_factory=lambda: lambda s, d: None)
    
    select_character_profiles: Callable[[CharacterBible, Any, str], list[dict[str, Any]]] = field(
        default_factory=lambda: lambda bible, ctx, pov: []
    )
    compact_previous_creative_report: Callable[[dict[str, Any] | None], dict[str, Any] | None] = field(
        default_factory=lambda: lambda r: r
    )
    compress_prompt_context: Callable[..., Any] = field(
        default_factory=lambda: lambda *a, **k: {}
    )
    remove_opening_echo_from_previous: Callable[[str, str], tuple[str, dict[str, Any] | None]] = field(
        default_factory=lambda: lambda t, p: (t, None)
    )
    apply_chapter_compaction: Callable[..., Any] = field(
        default_factory=lambda: lambda *a, **k: (None, None)
    )
    finalize_volume_if_needed: Callable[..., Any] = field(
        default_factory=lambda: lambda *a, **k: None
    )
    is_outline_option_enabled_for_task: Callable[..., bool] = field(
        default_factory=lambda: lambda *_a, **_k: False
    )
    render_prompt: Callable[[Any, dict[str, Any]], str] = field(
        default_factory=lambda: lambda t, c: ""
    )
    
    @property
    def trace(self) -> PipelineTrace:
        """Lazy trace access."""
        raise AttributeError("Trace must be created per-execution")


@dataclass
class InitLongContext:
    """Context for long-form project initialization.
    
    Bundles dependencies and configuration for the init_long flow.
    """
    storage: FileSystemStorage
    router: ModelRouterProtocol
    builder: PromptBuilder
    settings: Settings
    config: 'ChapterRunnerConfig'
    trace: PipelineTrace
    on_step: Callable[[str, Any], None] = field(default_factory=lambda: lambda s, d: None)
    
    project_id: str = ""
    genre: str = ""
    tone: str = ""
    title: str = ""
    language: str = "zh"
    total_chapters: int = 20
    words_per_chapter: int = 3000
    volume_mode: Literal["auto", "on", "off"] = "auto"
    chapters_per_volume: int = 0


@dataclass
class VolumeContext:
    """Context for volume-related operations.
    
    Used for volume audit and compaction operations.
    """
    storage: FileSystemStorage
    router: ModelRouterProtocol
    builder: PromptBuilder
    settings: Settings
    trace: PipelineTrace
    on_step: Callable[[str, Any], None] = field(default_factory=lambda: lambda s, d: None)
    
    volume_number: int = 1
    chapter_summaries: list[dict[str, Any]] = field(default_factory=list)
    timeline_events: list[dict[str, Any]] = field(default_factory=list)
    active_characters: list[dict[str, Any]] = field(default_factory=list)
    active_foreshadowing: list[dict[str, Any]] = field(default_factory=list)
    world_fact_keys: list[str] = field(default_factory=list)


@dataclass
class PromptRenderContext:
    """Context for prompt rendering operations.
    
    Provides a clean interface for rendering prompts without
    exposing the underlying PromptBuilder implementation.
    """
    builder: PromptBuilder
    
    def render(self, task_type: Any, context: dict[str, Any]) -> str:
        """Render a prompt for the given task type."""
        return self.builder.render(task_type, context)
    
    def build(
        self,
        task_type: Any,
        context: dict[str, Any],
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> Any:
        """Build a model request for the given task type."""
        return self.builder.build(task_type, context, max_tokens=max_tokens, temperature=temperature)
