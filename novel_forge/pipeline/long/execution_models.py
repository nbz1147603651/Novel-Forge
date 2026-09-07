"""Shared context and stage artifacts for long-form chapter execution.

This module provides typed context objects and data structures for the
chapter execution pipeline. All types are designed to be immutable
(frozen dataclasses) to ensure predictable state management.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional, Tuple

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.bible import CharacterBible
from novel_forge.core.schemas.chapter import (
    AlignmentReport,
    CausalValidationReport,
    ChapterOutcome,
    ChapterRepairReport,
)
from novel_forge.core.schemas.continuity import (
    ChapterBridge,
    ChapterPlan,
    ChapterStatePacket,
    ContinuityReport,
    RepairPlan,
)
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.schemas.reading_power import ReadingPowerReport
from novel_forge.core.schemas.review import RepairTicket, ReviewFinding
from novel_forge.obs.tracer import PipelineTrace
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.pipeline.long.preflight import LongProjectBundle
from novel_forge.story_kernel.schemas import StoryKernel

if TYPE_CHECKING:
    from novel_forge.gateway.router import ModelRouter
    from novel_forge.prompts.builder import PromptBuilder
    from novel_forge.story_kernel.merger import StoryKernelMerger
    from novel_forge.story_kernel.rules import StoryKernelConsistencyRules


# =============================================================================
# Type Aliases for Callbacks
# =============================================================================

StepCallback = Callable[[str, dict[str, Any]], None]
"""Callback for reporting step progress.

Args:
    step_name: Name of the step (e.g., "draft", "edit_1")
    data: Step-specific data dictionary
"""

SelectCharacterProfiles = Callable[..., List[Dict[str, Any]]]
"""Select character profiles for prompt injection.

Args:
    character_bible: Character bible containing all profiles
    canon_context: Current canon context
    pov_character: Point-of-view character name
    involved_characters: Optional list of characters involved in this chapter
Returns:
    List of selected profile dictionaries
"""

CompactPreviousCreativeReport = Callable[[Optional[Dict[str, Any]]], Optional[Dict[str, Any]]]
"""Compact previous creative report to fit context limits.

Args:
    report: Previous creative report or None
Returns:
    Compacted report or None
"""

CompressPromptContext = Callable[..., Awaitable[Dict[str, Any]]]
"""Compress prompt context using LLM.

The actual signature varies; use ... to avoid complexity.
Returns:
    Compression statistics dictionary
"""

RemoveOpeningEcho = Callable[[str, str], Tuple[str, Optional[Dict[str, Any]]]]
"""Remove opening echo from previous chapter ending.

Args:
    chapter_text: Current chapter text
    previous_ending: Previous chapter ending text
Returns:
    Tuple of (cleaned text, dedup stats or None)
"""

ApplyChapterCompaction = Callable[
    [StoryKernel, Any, int, CharacterBible], Tuple[StoryKernel, Optional[Dict[str, Any]]]
]
"""Apply chapter compaction to canon state.

Args:
    state: Current canon state
    outline: Story outline
    chapter_number: Current chapter number
    character_bible: Character bible
Returns:
    Tuple of (compacted state, compaction report or None)
"""

FinalizeVolumeIfNeeded = Callable[
    [Any, Any, int, StoryKernel, Any, Any, PipelineTrace], Awaitable[Optional[Any]]
]
"""Finalize volume if current chapter ends a volume.

Args:
    layout: Project layout
    outline: Story outline
    chapter_number: Current chapter number
    canon_state: Current canon state
    story_bible: Story bible
    canon_store: Canon store
    trace: Pipeline trace
Returns:
    Volume audit report or None
"""

OutlineOptionEnabled = Callable[..., bool]
"""Check if outline option is enabled for task."""

RenderPrompt = Callable[[TaskType, Dict[str, Any]], str]
"""Render prompt for task type.

Args:
    task_type: Task type constant
    context: Prompt context dictionary
Returns:
    Rendered prompt string
"""


# Import after type aliases to avoid circular dependency issues
from novel_forge.gateway.router import ModelRouter  # noqa: E402
from novel_forge.prompts.builder import PromptBuilder  # noqa: E402
from novel_forge.story_kernel.merger import StoryKernelMerger  # noqa: E402
from novel_forge.story_kernel.rules import StoryKernelConsistencyRules  # noqa: E402


@dataclass(frozen=True)
class ChapterExecutionContext:
    """Explicit dependencies for the long-form chapter flow.

    This context object bundles all dependencies needed for chapter
    execution, eliminating the need for services to access runner
    internals directly.

    Attributes:
        storage: File system storage interface
        router: Model router for LLM calls
        builder: Prompt builder for rendering templates
        settings: Global settings (read-only snapshot)
        config: Chapter runner configuration
        merger: Canon merger for state updates
        rules: Consistency rules validator
        on_step: Callback for reporting step progress
        select_character_profiles: Profile selection function
        compact_previous_creative_report: Report compaction function
        compress_prompt_context: Context compression function
        remove_opening_echo_from_previous: Opening dedup function
        apply_chapter_compaction: Chapter compaction function
        finalize_volume_if_needed: Volume finalization function
        is_outline_option_enabled_for_task: Feature flag checker
        render_prompt: Prompt rendering function
    """

    storage: FileSystemStorage
    router: ModelRouter
    builder: PromptBuilder
    settings: Settings
    config: Any  # ChapterRunnerConfig — avoid circular TYPE_CHECKING import
    merger: StoryKernelMerger
    rules: StoryKernelConsistencyRules
    on_step: StepCallback
    select_character_profiles: SelectCharacterProfiles
    compact_previous_creative_report: CompactPreviousCreativeReport
    compress_prompt_context: CompressPromptContext
    remove_opening_echo_from_previous: RemoveOpeningEcho
    apply_chapter_compaction: ApplyChapterCompaction
    finalize_volume_if_needed: FinalizeVolumeIfNeeded
    is_outline_option_enabled_for_task: OutlineOptionEnabled
    render_prompt: RenderPrompt
    audit_coordinator: Any = None
    has_audit_coordinator: Callable[[], bool] | None = None
    memory_context: Any = None
    has_memory_context: Callable[[], bool] | None = None
    human_decision_provider: Any = None
    retriever: Any = None  # StoryKernelRetriever — optional, for memory manager
    call_with_retry: Any = None

    def __post_init__(self) -> None:
        """Ensure callable guards are always invokable.

        Stages and services call ``runner.has_audit_coordinator()`` and
        ``runner.has_memory_context()`` as methods.  When the optional
        callable fields are ``None``, fall back to a simple null-check on
        the underlying coordinator / context so callers never see a
        ``TypeError``.
        """
        if self.has_audit_coordinator is None:
            ac = self.audit_coordinator
            object.__setattr__(self, "has_audit_coordinator", lambda: ac is not None)
        if self.has_memory_context is None:
            mc = self.memory_context
            object.__setattr__(self, "has_memory_context", lambda: mc is not None)
        if self.human_decision_provider is None:
            from novel_forge.pipeline.long.human_decision import AutoDecisionProvider

            object.__setattr__(
                self,
                "human_decision_provider",
                AutoDecisionProvider(default_choice="continue_without_escalation"),
            )

    # ── Underscore-prefixed property aliases ───────────────────────────────
    # These mirror the legacy adapter interface so that stages/services
    # can access ``runner._storage``, ``runner._router``, etc. uniformly
    # whether *runner* is the raw context or the legacy proxy.

    @property
    def _storage(self) -> FileSystemStorage:
        return self.storage

    @property
    def _router(self) -> ModelRouter:
        return self.router

    @property
    def _builder(self) -> PromptBuilder:
        return self.builder

    @property
    def _settings(self) -> Settings:
        return self.settings

    @property
    def _config(self) -> Any:
        return self.config

    @property
    def _merger(self) -> StoryKernelMerger:
        return self.merger

    @property
    def _rules(self) -> StoryKernelConsistencyRules:
        return self.rules

    @property
    def _on_step(self) -> StepCallback:
        return self.on_step

    @property
    def _human_decision_provider(self) -> Any:
        return self.human_decision_provider

    async def _call_with_retry(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if not callable(self.call_with_retry):
            raise RuntimeError("Chapter execution context has no model-call adapter")
        result = await self.call_with_retry(*args, **kwargs)
        return result if isinstance(result, dict) else {}

    @property
    def _retriever(self) -> Any:
        return self.retriever

    @property
    def _select_character_profiles(self) -> SelectCharacterProfiles:
        return self.select_character_profiles

    @property
    def _compact_previous_creative_report(self) -> CompactPreviousCreativeReport:
        return self.compact_previous_creative_report

    @property
    def _compress_prompt_context(self) -> CompressPromptContext:
        return self.compress_prompt_context

    @property
    def _remove_opening_echo_from_previous(self) -> RemoveOpeningEcho:
        return self.remove_opening_echo_from_previous

    @property
    def _apply_chapter_compaction(self) -> ApplyChapterCompaction:
        return self.apply_chapter_compaction

    @property
    def _finalize_volume_if_needed(self) -> FinalizeVolumeIfNeeded:
        return self.finalize_volume_if_needed

    @property
    def _is_outline_option_enabled_for_task(self) -> OutlineOptionEnabled:
        return self.is_outline_option_enabled_for_task


class FlowContextAdapter:
    """Lightweight adapter providing underscore-prefixed attribute access.

    Wraps either a ``ChapterExecutionContext`` or a test mock (e.g., ``SimpleNamespace``)
    and provides uniform access via ``_router``, ``_builder``, etc. Falls back to
    non-underscore attributes if underscore ones don't exist, enabling test mocks
    that use ``SimpleNamespace(router=..., builder=...)`` to work seamlessly.
    """

    __slots__ = ("_ctx", "_runtime_state")

    def __init__(self, ctx: Any) -> None:
        object.__setattr__(self, "_ctx", ctx)
        object.__setattr__(self, "_runtime_state", {})

    def __getattr__(self, name: str) -> Any:
        runtime_state = object.__getattribute__(self, "_runtime_state")
        if name in runtime_state:
            return runtime_state[name]
        ctx = object.__getattribute__(self, "_ctx")
        if name.startswith("_"):
            bare_name = name[1:]
            # Try bare name first (e.g., _router -> router)
            if hasattr(ctx, bare_name):
                return getattr(ctx, bare_name)
            # Fall back to underscore name (e.g., _router -> _router)
            if hasattr(ctx, name):
                return getattr(ctx, name)
        return getattr(ctx, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"_ctx", "_runtime_state"}:
            object.__setattr__(self, name, value)
            return
        # ChapterExecutionContext is frozen, while a few review helpers need
        # short-lived runner-local state (for example pending opening-guard
        # issues). Keep that state on the adapter instead of mutating context.
        runtime_state = object.__getattribute__(self, "_runtime_state")
        runtime_state[name] = value

    def __delattr__(self, name: str) -> None:
        runtime_state = object.__getattribute__(self, "_runtime_state")
        if name in runtime_state:
            del runtime_state[name]
            return
        raise AttributeError(name)

    def has_audit_coordinator(self) -> bool:
        ctx = object.__getattribute__(self, "_ctx")
        has_fn = getattr(ctx, "has_audit_coordinator", None)
        if callable(has_fn):
            return has_fn()
        return getattr(ctx, "audit_coordinator", None) is not None

    def has_memory_context(self) -> bool:
        ctx = object.__getattribute__(self, "_ctx")
        has_fn = getattr(ctx, "has_memory_context", None)
        if callable(has_fn):
            return has_fn()
        return getattr(ctx, "memory_context", None) is not None

    @property
    def audit_coordinator(self) -> Any:
        ctx = object.__getattribute__(self, "_ctx")
        return getattr(ctx, "audit_coordinator", None)

    @property
    def memory_context(self) -> Any:
        ctx = object.__getattribute__(self, "_ctx")
        return getattr(ctx, "memory_context", None)


@dataclass(frozen=True)
class PreparedChapterArtifacts:
    """Artifacts produced by the planning stage.

    Attributes:
        bundle: Long project bundle with core data
        packet: Chapter state packet with context
        bridge: Chapter bridge connecting previous/current
        plan: Chapter plan with beat-by-beat outline
    """

    bundle: LongProjectBundle
    packet: ChapterStatePacket
    bridge: ChapterBridge
    plan: ChapterPlan
    memory_hints: Dict[str, Any] | None = None
    scene_plan_validation_report: Dict[str, Any] | None = None
    window_manager: Any | None = None
    """追读力窗口管理器（运行时对象，不序列化）。"""
    window_config: Any | None = None
    """追读力窗口配置（运行时对象，不序列化）。"""
    reading_power_hint: Dict[str, Any] | None = None
    """追读力提示，用于传递给 draft/edit 阶段。"""


@dataclass(frozen=True)
class ChapterReviewArtifacts:
    """Artifacts produced by draft/review before final persistence.

    Attributes:
        prepared: Planning stage artifacts
        current_text: Current chapter text after Generate/Review stages
        performed_edits: Legacy counter for text-transform passes; WAVE counts as 1
        outcome: Chapter outcome with canon delta
        alignment_report: Alignment check report
        chapter_repair_report: Chapter repair report or None
        continuity_report: Continuity evaluation report
        repair_plan: Repair plan from continuity fix
        eval_report: Evaluation report or None
    """

    prepared: PreparedChapterArtifacts
    current_text: str
    performed_edits: int
    outcome: ChapterOutcome
    alignment_report: AlignmentReport
    chapter_repair_report: ChapterRepairReport | None
    continuity_report: ContinuityReport
    repair_plan: RepairPlan
    causal_report: CausalValidationReport | None = None
    eval_report: EvalReport | None = None
    reading_power_report: ReadingPowerReport | None = None
    guard_compliance_report: dict[str, Any] | None = None
    world_rule_report: Any | None = None
    review_findings: list[ReviewFinding] | None = None
    repair_tickets: list[RepairTicket] | None = None
    warnings: list[str] | None = None
    allow_word_count_archive_bypass: bool = False
    quality_reports_stale_after_text_change: bool = False
    quality_reports_stale_reason: str = ""
    total_repair_rounds_used: int = 0
    pov_drift_findings: list[ReviewFinding] | None = None
    pov_drift_tickets: list[RepairTicket] | None = None
    budget_status: str | None = None  # None=ok, "budget_blocked"=insufficient budget
    refinement_done: bool = False
    final_verify_done: bool = False


@dataclass(frozen=True)
class ChapterExecutionState:
    """Full execution state for direct chapter runs.

    Attributes:
        prepared: Planning stage artifacts
        review: Review stage artifacts
        trace: Pipeline trace with metrics
    """

    prepared: PreparedChapterArtifacts
    review: ChapterReviewArtifacts
    trace: PipelineTrace


__all__ = [
    # Type aliases
    "StepCallback",
    "SelectCharacterProfiles",
    "CompactPreviousCreativeReport",
    "CompressPromptContext",
    "RemoveOpeningEcho",
    "ApplyChapterCompaction",
    "FinalizeVolumeIfNeeded",
    "OutlineOptionEnabled",
    "RenderPrompt",
    # Data classes
    "ChapterExecutionContext",
    "PreparedChapterArtifacts",
    "ChapterReviewArtifacts",
    "ChapterExecutionState",
]
