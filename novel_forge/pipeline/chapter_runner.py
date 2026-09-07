"""ChapterRunner — orchestrates a single chapter of the long-mode pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Literal

from novel_forge.core.config import Settings
from novel_forge.core.constants import ForeshadowingStatus, PipelineConstants, TaskType
from novel_forge.core.schemas.bible import CharacterBible, StoryBible
from novel_forge.core.schemas.canon import CreativeReport
from novel_forge.core.schemas.chapter import AlignmentReport, ChapterOutcome, ChapterResult
from novel_forge.core.schemas.outline import StoryOutline
from novel_forge.core.schemas.volume import VolumeAuditReport
from novel_forge.gateway.router import ModelRouter
from novel_forge.memory.audit_coordinator import AuditCoordinator
from novel_forge.obs.logger import get_logger
from novel_forge.obs.tracer import PipelineTrace
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.execution_models import ChapterExecutionContext
from novel_forge.pipeline.long.loop import run_long_chapter
from novel_forge.pipeline.long.services import compaction_service
from novel_forge.pipeline.long.services.constraints.validation_service import ValidationService
from novel_forge.pipeline.long.services.context import context_helpers as ctx_h
from novel_forge.pipeline.long.services.generation import llm_helpers as _llm_h
from novel_forge.pipeline.long.services.generation.llm_service import LLMService
from novel_forge.pipeline.long.services.init.init_service import init_long_project
from novel_forge.pipeline.steps.volume_step import VolumeAuditInput, VolumeAuditStep
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.story_kernel.merger import StoryKernelMerger
from novel_forge.story_kernel.retriever import StoryKernelRetriever
from novel_forge.story_kernel.rules import StoryKernelConsistencyRules
from novel_forge.story_kernel.schemas import StoryKernel
from novel_forge.story_kernel.store import StoryKernelStore

if TYPE_CHECKING:
    from novel_forge.memory.integration import MemoryContext

# Backward-compatible alias — canonical definition now lives in core.constants
ChapterRunnerConstants = PipelineConstants
_log = get_logger("pipeline.chapter_runner")


@dataclass
class ChapterRunnerConfig:
    """集中管理ChapterRunner的所有配置参数，替代分散的构造参数。"""

    # 写作模式（WAVE 单次连贯起稿，长篇不再有用户可配置的编辑轮次）
    writing_mode: Literal["whole_chapter", "scene_level"] = "whole_chapter"
    alignment_threshold: float = 7.0

    # 卷管理配置
    volume_auto_chapter_threshold: int = 60
    volume_auto_word_threshold: int = 250_000
    default_chapters_per_volume: int = 20

    # 上下文限制配置
    canon_max_recent_events: int = 40
    canon_max_characters: int = 30
    canon_max_foreshadowing: int = 30
    canon_max_world_facts: int = 80

    # 提示词配置
    prompt_max_character_profiles: int = 12
    prompt_max_profile_field_chars: int = 240
    prompt_profile_source_field_chars: int = 900
    prompt_max_relationships_per_profile: int = 6
    prompt_bridge_brief_chars: int = 220
    prompt_planning_brief_chars: int = 320
    prompt_continuity_brief_chars: int = 260

    # 计划/报告配置
    plan_prev_report_max_deviations: int = 2
    plan_prev_report_max_new_characters: int = 3
    plan_prev_report_text_chars: int = 260
    plan_prev_report_source_chars: int = 1200

    # 压缩配置
    context_compress_enabled: bool = True
    context_compress_min_chars: int = 220
    context_compress_max_tokens: int = 2048
    context_compress_quality_min_score: float = 0.62
    context_compress_llm_verify_enabled: bool = True
    context_compress_llm_verify_margin: float = 0.12
    context_compress_llm_verify_max_items: int = 3
    context_compress_adaptive_skip_enabled: bool = True

    # 章节压缩配置
    chapter_compact_interval: int = 5
    chapter_compact_start_chapter: int = 10
    chapter_compact_stale_chapters: int = 8
    chapter_compact_outline_lookahead: int = 12
    chapter_compact_min_active_characters: int = 8
    chapter_compact_target_world_facts: int = 120
    chapter_compact_keep_recent_world_facts: int = 40
    chapter_compact_archive_resolved_foreshadowing_after: int = 6

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """验证所有参数的有效性和一致性，并将值钳位至安全范围。"""
        if self.writing_mode not in {"whole_chapter", "scene_level"}:
            self.writing_mode = "whole_chapter"
        # Clamp values to safe minimums (absorbs former property-accessor logic)
        self.volume_auto_chapter_threshold = max(1, self.volume_auto_chapter_threshold)
        self.volume_auto_word_threshold = max(1, self.volume_auto_word_threshold)
        self.default_chapters_per_volume = max(5, self.default_chapters_per_volume)
        self.prompt_max_character_profiles = max(1, self.prompt_max_character_profiles)
        self.prompt_max_profile_field_chars = max(60, self.prompt_max_profile_field_chars)
        self.prompt_profile_source_field_chars = max(
            self.prompt_max_profile_field_chars,
            self.prompt_profile_source_field_chars,
        )
        self.prompt_max_relationships_per_profile = max(
            1, self.prompt_max_relationships_per_profile
        )
        self.prompt_bridge_brief_chars = max(120, self.prompt_bridge_brief_chars)
        self.prompt_planning_brief_chars = max(160, self.prompt_planning_brief_chars)
        self.prompt_continuity_brief_chars = max(140, self.prompt_continuity_brief_chars)
        self.plan_prev_report_max_deviations = max(0, self.plan_prev_report_max_deviations)
        self.plan_prev_report_max_new_characters = max(0, self.plan_prev_report_max_new_characters)
        self.plan_prev_report_text_chars = max(60, self.plan_prev_report_text_chars)
        self.plan_prev_report_source_chars = max(
            self.plan_prev_report_text_chars,
            self.plan_prev_report_source_chars,
        )
        self.context_compress_min_chars = max(80, self.context_compress_min_chars)
        self.context_compress_max_tokens = max(512, self.context_compress_max_tokens)
        self.context_compress_quality_min_score = max(
            0.0, min(1.0, self.context_compress_quality_min_score)
        )
        self.context_compress_llm_verify_margin = max(
            0.0, min(0.5, self.context_compress_llm_verify_margin)
        )
        self.context_compress_llm_verify_max_items = max(
            0, self.context_compress_llm_verify_max_items
        )
        self.chapter_compact_interval = max(1, self.chapter_compact_interval)
        self.chapter_compact_start_chapter = max(1, self.chapter_compact_start_chapter)
        self.chapter_compact_stale_chapters = max(2, self.chapter_compact_stale_chapters)
        self.chapter_compact_outline_lookahead = max(1, self.chapter_compact_outline_lookahead)
        self.chapter_compact_min_active_characters = max(
            1, self.chapter_compact_min_active_characters
        )
        self.chapter_compact_target_world_facts = max(1, self.chapter_compact_target_world_facts)
        self.chapter_compact_keep_recent_world_facts = max(
            1, self.chapter_compact_keep_recent_world_facts
        )
        self.chapter_compact_archive_resolved_foreshadowing_after = max(
            1, self.chapter_compact_archive_resolved_foreshadowing_after
        )

        # 交叉验证
        if self.prompt_max_profile_field_chars > self.prompt_profile_source_field_chars:
            raise ValueError(
                f"profile_field_chars ({self.prompt_max_profile_field_chars}) "
                f"should not exceed source_field_chars ({self.prompt_profile_source_field_chars})"
            )

        if self.plan_prev_report_text_chars > self.plan_prev_report_source_chars:
            raise ValueError(
                f"report_text_chars ({self.plan_prev_report_text_chars}) "
                f"should not exceed source_chars ({self.plan_prev_report_source_chars})"
            )

        if self.chapter_compact_keep_recent_world_facts > self.chapter_compact_target_world_facts:
            raise ValueError(
                f"keep_recent_facts ({self.chapter_compact_keep_recent_world_facts}) "
                f"should not exceed target ({self.chapter_compact_target_world_facts})"
            )

    @classmethod
    def from_settings(cls, settings: Settings) -> ChapterRunnerConfig:
        """从全局Settings创建配置实例。"""
        return cls(
            # long form no longer carries edit rounds (WAVE runs once)
            alignment_threshold=settings.long_alignment_threshold,
            volume_auto_chapter_threshold=settings.long_volume_auto_chapter_threshold,
            volume_auto_word_threshold=settings.long_volume_auto_word_threshold,
            default_chapters_per_volume=settings.long_default_chapters_per_volume,
            canon_max_recent_events=settings.canon_context_max_recent_events,
            canon_max_characters=settings.canon_context_max_characters,
            canon_max_foreshadowing=settings.canon_context_max_foreshadowing,
            canon_max_world_facts=settings.canon_context_max_world_facts,
            prompt_max_character_profiles=settings.long_prompt_max_character_profiles,
            prompt_max_profile_field_chars=settings.long_prompt_max_profile_field_chars,
            prompt_profile_source_field_chars=settings.long_prompt_profile_source_field_chars,
            prompt_max_relationships_per_profile=settings.long_prompt_max_relationships_per_profile,
            plan_prev_report_max_deviations=settings.long_plan_prev_report_max_deviations,
            plan_prev_report_max_new_characters=settings.long_plan_prev_report_max_new_characters,
            plan_prev_report_text_chars=settings.long_plan_prev_report_text_chars,
            plan_prev_report_source_chars=settings.long_plan_prev_report_source_chars,
            context_compress_enabled=settings.long_context_compress_enabled,
            context_compress_min_chars=settings.long_context_compress_min_chars,
            context_compress_max_tokens=settings.long_context_compress_max_tokens,
            context_compress_quality_min_score=settings.long_context_compress_quality_min_score,
            context_compress_llm_verify_enabled=settings.long_context_compress_llm_verify_enabled,
            context_compress_llm_verify_margin=settings.long_context_compress_llm_verify_margin,
            context_compress_llm_verify_max_items=settings.long_context_compress_llm_verify_max_items,
            context_compress_adaptive_skip_enabled=(
                settings.long_context_compress_adaptive_skip_enabled
            ),
            chapter_compact_interval=settings.long_chapter_compact_interval,
            chapter_compact_start_chapter=settings.long_chapter_compact_start_chapter,
            chapter_compact_stale_chapters=settings.long_chapter_compact_stale_chapters,
            chapter_compact_outline_lookahead=settings.long_chapter_compact_outline_lookahead,
            chapter_compact_min_active_characters=settings.long_chapter_compact_min_active_characters,
            chapter_compact_target_world_facts=settings.long_chapter_compact_target_world_facts,
            chapter_compact_keep_recent_world_facts=settings.long_chapter_compact_keep_recent_world_facts,
            chapter_compact_archive_resolved_foreshadowing_after=(
                settings.long_chapter_compact_archive_resolved_foreshadowing_after
            ),
        )


@dataclass
class InitLongResult:
    """Result of initializing a long-mode project."""

    project_id: str
    story_bible: StoryBible
    character_bible: CharacterBible
    outline: StoryOutline
    canon_state: StoryKernel
    trace_summary: dict[str, Any] = field(default_factory=dict)


class ChapterRunner:
    """Retrieve → Plan → Draft → Edit(×N) → Extract → Test → Persist."""

    def __init__(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
        storage: FileSystemStorage,
        settings: Settings,
        config: ChapterRunnerConfig | None = None,
        on_step_progress: Callable[[str, Any], None] | None = None,
        memory_context: "MemoryContext | None" = None,
        warn_missing_memory_context: bool = True,
        human_decision_provider: Any | None = None,
    ) -> None:
        """初始化ChapterRunner。

        Args:
            router: 模型路由器
            builder: 提示词构造器
            storage: 文件系统存储
            settings: 全局设置
            config: 运行配置。如果为None，使用默认配置
            on_step_progress: 步骤进度回调函数
            memory_context: 记忆上下文（可选），用于记忆增强的检测
        """
        self._router = router
        self._builder = builder
        self._storage = storage
        self._settings = settings
        self._config = config or ChapterRunnerConfig()
        self._on_step = on_step_progress or (lambda s, d: None)
        self._memory_context = memory_context
        self._human_decision_provider = human_decision_provider

        # 警告：记忆功能已启用但未传入 MemoryContext
        if memory_context is None and warn_missing_memory_context:
            memory_enabled = any(
                [
                    getattr(self._settings, "memory_episodic_enabled", False),
                    getattr(self._settings, "memory_multi_granularity_summary_enabled", False),
                    getattr(self._settings, "memory_motif_tracking_enabled", False),
                ]
            )
            if memory_enabled:
                _log.warning("记忆功能已启用配置但未传入 MemoryContext，所有记忆功能将被静默跳过")

        # 初始化AuditCoordinator用于记忆增强审核
        self._audit_coordinator: AuditCoordinator | None = None
        if self._memory_context is not None:
            try:
                self._audit_coordinator = AuditCoordinator(
                    self._memory_context,
                    enable_caching=True,
                )
                _log.info(
                    "AuditCoordinator initialized | project=%s",
                    getattr(memory_context, "project_id", "unknown"),
                )
            except Exception as exc:
                _log.warning(
                    "Failed to initialize AuditCoordinator: %s",
                    exc,
                )
                self._audit_coordinator = None

        # 初始化拆分的服务
        self._llm_service = LLMService(router, builder, self._on_step, settings=settings)

        # 初始化核心组件
        self._merger = StoryKernelMerger()
        self._rules = StoryKernelConsistencyRules()
        self._retriever = StoryKernelRetriever(
            max_recent_events=self._config.canon_max_recent_events,
            max_characters=self._config.canon_max_characters,
            max_active_foreshadowing=self._config.canon_max_foreshadowing,
            max_world_facts=self._config.canon_max_world_facts,
        )

        # 缓存计算出的派生属性
        self._prompt_relationship_source_chars = max(
            120, min(360, self._config.prompt_profile_source_field_chars)
        )

    @property
    def memory_context(self) -> Any | None:
        """获取记忆上下文（如果有）。"""
        return self._memory_context

    @property
    def audit_coordinator(self) -> AuditCoordinator | None:
        """获取AuditCoordinator实例（如果有）。"""
        return self._audit_coordinator

    def has_memory_context(self) -> bool:
        """检查是否有可用的记忆上下文。"""
        return self._memory_context is not None

    def has_audit_coordinator(self) -> bool:
        """检查是否有可用的AuditCoordinator。"""
        return self._audit_coordinator is not None

    @classmethod
    def from_settings(
        cls,
        router: ModelRouter,
        builder: PromptBuilder,
        storage: FileSystemStorage,
        settings: Settings,
        *,
        writing_mode: str | None = None,
        on_step_progress: Callable[[str, Any], None] | None = None,
        memory_context: "MemoryContext | None" = None,
        warn_missing_memory_context: bool = True,
        human_decision_provider: Any | None = None,
    ) -> ChapterRunner:
        """Build a chapter runner from centralized settings."""
        config = ChapterRunnerConfig.from_settings(settings)
        if writing_mode is not None:
            config.writing_mode = (
                "scene_level" if writing_mode == "scene_level" else "whole_chapter"
            )
            config.validate()
        return cls(
            router,
            builder,
            storage,
            config=config,
            settings=settings,
            on_step_progress=on_step_progress,
            memory_context=memory_context,
            warn_missing_memory_context=warn_missing_memory_context,
            human_decision_provider=human_decision_provider,
        )

    # ── 属性访问器已移除 ──────────────────────────────────────────
    # 验证逻辑已整合至 ChapterRunnerConfig.validate()
    # 所有配置值通过 self._config.xxx 直接访问

    def _coerce_character_bible(self, payload: Any) -> CharacterBible:
        """Validate CharacterBible; if empty list, inject a minimal fallback."""
        return ValidationService.coerce_character_bible(payload, self._on_step)

    def _compact_previous_creative_report(
        self, report: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Keep only compact, relevant fields from previous creative report."""
        return ctx_h.compact_previous_creative_report(
            report,
            max_deviations=self._config.plan_prev_report_max_deviations,
            max_new_characters=self._config.plan_prev_report_max_new_characters,
            source_chars=self._config.plan_prev_report_source_chars,
        )

    async def _compress_prompt_context(
        self,
        previous_creative_report: dict[str, Any] | None,
        character_profiles: list[dict[str, Any]],
        *,
        packet: Any | None = None,
    ) -> dict[str, Any] | str:
        """Compress planning prompt context with a cheap model before final hard limits."""
        return await ctx_h.compress_prompt_context(
            previous_creative_report,
            character_profiles,
            packet=packet,
            report_text_chars=self._config.plan_prev_report_text_chars,
            profile_field_chars=self._config.prompt_max_profile_field_chars,
            bridge_brief_chars=self._config.prompt_bridge_brief_chars,
            planning_brief_chars=self._config.prompt_planning_brief_chars,
            continuity_brief_chars=self._config.prompt_continuity_brief_chars,
            compress_enabled=self._config.context_compress_enabled,
            compress_min_chars=self._config.context_compress_min_chars,
            compress_max_tokens=self._config.context_compress_max_tokens,
            temperature=self._settings.temp_context_compress,
            call_with_retry=self._call_with_retry,
            on_step=self._on_step,
            quality_min_score=self._config.context_compress_quality_min_score,
            llm_verify_enabled=self._config.context_compress_llm_verify_enabled,
            llm_verify_margin=self._config.context_compress_llm_verify_margin,
            llm_verify_max_items=self._config.context_compress_llm_verify_max_items,
            verify_temperature=self._settings.temp_verify_compression,
            adaptive_skip_enabled=self._config.context_compress_adaptive_skip_enabled,
        )

    def _select_character_profiles(
        self,
        character_bible: CharacterBible,
        canon_context: Any,
        pov_character: str,
        involved_characters: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Select a bounded subset of character profiles for prompt injection."""
        return ctx_h.select_character_profiles(
            character_bible,
            canon_context,
            pov_character,
            max_profiles=self._config.prompt_max_character_profiles,
            source_field_chars=self._config.prompt_profile_source_field_chars,
            max_relationships=self._config.prompt_max_relationships_per_profile,
            relationship_source_chars=self._prompt_relationship_source_chars,
            involved_characters=involved_characters,
        )

    @staticmethod
    def _build_known_characters_for_extract(
        canon_state: StoryKernel,
        canon_context: Any,
        character_profiles: list[dict[str, Any]],
    ) -> list[str]:
        """Build a bounded known-character list for extraction prompts."""
        return ctx_h.build_known_characters_for_extract(
            canon_state, canon_context, character_profiles
        )

    @classmethod
    def _remove_opening_echo_from_previous(
        cls,
        chapter_text: str,
        previous_chapter_ending: str,
    ) -> tuple[str, dict[str, Any] | None]:
        return ctx_h.remove_opening_echo(
            chapter_text,
            previous_chapter_ending,
        )

    @classmethod
    def _build_alignment_repair_subplot_summary(
        cls,
        before_report: AlignmentReport | dict[str, Any],
        after_report: AlignmentReport | dict[str, Any],
    ) -> dict[str, Any]:
        return ctx_h.build_alignment_repair_subplot_summary(before_report, after_report)

    @staticmethod
    def _compact_extracted_payload(
        canon_state: StoryKernel,
        canon_delta: ChapterOutcome,
        creative_report: CreativeReport,
    ) -> dict[str, int]:
        """Drop no-op/duplicate extraction fields before validation and merge."""
        return compaction_service.compact_extracted_payload(
            canon_state, canon_delta, creative_report
        )

    async def _call_with_retry(
        self,
        task_type: TaskType,
        context: dict[str, Any],
        *,
        max_tokens: int = PipelineConstants.INITIAL_MAX_TOKENS,
        temperature: float = 0.7,
        required_keys: tuple[str, ...] = (),
        max_retries: int = 2,
        prior_messages: list[dict[str, str]] | None = None,
        thinking: bool = False,
        multi_turn: bool = False,
        _capture_raw: list[str] | None = None,
        include_contract_required_keys: bool = True,
    ) -> dict[str, Any]:
        """Call LLM and parse JSON response, with automatic retry on failure."""
        return await self._llm_service.call_with_retry(
            task_type,
            context,
            max_tokens=max_tokens,
            temperature=temperature,
            required_keys=required_keys,
            max_retries=max_retries,
            prior_messages=prior_messages,
            thinking=thinking,
            multi_turn=multi_turn,
            _capture_raw=_capture_raw,
            include_contract_required_keys=include_contract_required_keys,
        )

    def render_prompt(self, task_type: TaskType, context: dict[str, Any]) -> str:
        """Render one task prompt via the shared prompt builder."""
        return self._builder.render(task_type, context)

    def create_execution_context(self) -> ChapterExecutionContext:
        """Expose an explicit dependency bundle for long-form orchestration."""
        return ChapterExecutionContext(
            storage=self._storage,
            router=self._router,
            builder=self._builder,
            settings=self._settings,
            config=self._config,
            merger=self._merger,
            rules=self._rules,
            on_step=self._on_step,
            select_character_profiles=self._select_character_profiles,
            compact_previous_creative_report=self._compact_previous_creative_report,
            compress_prompt_context=self._compress_prompt_context,
            remove_opening_echo_from_previous=self._remove_opening_echo_from_previous,
            apply_chapter_compaction=self._apply_chapter_compaction,  # type: ignore[arg-type]
            finalize_volume_if_needed=self._finalize_volume_if_needed,  # type: ignore[arg-type]
            is_outline_option_enabled_for_task=self._is_outline_option_enabled_for_task,
            render_prompt=self.render_prompt,
            audit_coordinator=self._audit_coordinator,
            has_audit_coordinator=self.has_audit_coordinator,
            memory_context=self._memory_context,
            has_memory_context=self.has_memory_context,
            human_decision_provider=self._human_decision_provider,
            retriever=self._retriever,
            call_with_retry=self._call_with_retry,
        )

    async def init_long(
        self,
        premise: str,
        *,
        project_id: str,
        genre: str = "",
        tone: str = "",
        title: str = "",
        language: str = "zh",
        characters_hint: str = "",
        world_hint: str = "",
        conflict_hint: str = "",
        pov_hint: str = "",
        opening_style: str = "",
        ending_style: str = "",
        extra_instructions: str = "",
        polish_hint: str = "",
        total_chapters: int = 20,
        words_per_chapter: int = 3000,
        volume_mode: Literal["auto", "on", "off"] = "auto",
        chapters_per_volume: int = 0,
        blueprint_element_preferences: dict[str, Any] | None = None,
        research_enabled: bool = False,
        research_provider: str = "auto",
        research_query_hint: str = "",
        copilot_gates: tuple[str, ...] = (),
        creative_exploration: Literal["adaptive", "single"] = "adaptive",
        planning_commitment: Literal["progressive", "full"] = "full",
    ) -> InitLongResult:
        """Initialize a long-mode project: Enrich → Bible → Outline → StoryKernel."""
        return await init_long_project(
            self,
            premise,
            project_id=project_id,
            genre=genre,
            tone=tone,
            title=title,
            language=language,
            characters_hint=characters_hint,
            world_hint=world_hint,
            conflict_hint=conflict_hint,
            pov_hint=pov_hint,
            opening_style=opening_style,
            ending_style=ending_style,
            extra_instructions=extra_instructions,
            polish_hint=polish_hint,
            total_chapters=total_chapters,
            words_per_chapter=words_per_chapter,
            volume_mode=volume_mode,
            chapters_per_volume=chapters_per_volume,
            blueprint_element_preferences=blueprint_element_preferences,
            research_enabled=research_enabled,
            research_provider=research_provider,
            research_query_hint=research_query_hint,
            copilot_gates=copilot_gates,
            creative_exploration=creative_exploration,
            planning_commitment=planning_commitment,
            human_decision_provider=self._human_decision_provider,
        )

    # ── Router-dependent helpers (cannot be extracted to service modules) ──

    def _resolve_task_provider_model(self, task_type: TaskType) -> tuple[str, str]:
        """Resolve effective provider/model for a task using shared helper."""
        return _llm_h.resolve_task_provider_model(self._router, task_type)

    def _is_outline_option_enabled_for_task(
        self,
        *,
        capability: str,
        enabled: bool,
        allowed_providers_raw: str,
        allowed_models_raw: str,
        task_type: TaskType,
    ) -> bool:
        """Resolve option from task routing override first, else global allowlists."""
        return _llm_h.is_option_enabled_for_task(
            self._router,
            capability=capability,
            enabled=enabled,
            allowed_providers_raw=allowed_providers_raw,
            allowed_models_raw=allowed_models_raw,
            task_type=task_type,
        )

    # ── Compaction / volume wrappers (called from chapter_flow.py) ────────

    def _apply_chapter_compaction(
        self,
        *,
        state: StoryKernel,
        outline: StoryOutline,
        chapter_number: int,
        character_bible: CharacterBible,
    ) -> tuple[StoryKernel, dict[str, Any] | None]:
        cfg = compaction_service.CompactionConfig(
            chapter_compact_interval=self._config.chapter_compact_interval,
            chapter_compact_start_chapter=self._config.chapter_compact_start_chapter,
            chapter_compact_stale_chapters=self._config.chapter_compact_stale_chapters,
            chapter_compact_outline_lookahead=self._config.chapter_compact_outline_lookahead,
            chapter_compact_min_active_characters=self._config.chapter_compact_min_active_characters,
            chapter_compact_target_world_facts=self._config.chapter_compact_target_world_facts,
            chapter_compact_keep_recent_world_facts=self._config.chapter_compact_keep_recent_world_facts,
            chapter_compact_archive_resolved_foreshadowing_after=self._config.chapter_compact_archive_resolved_foreshadowing_after,
        )
        return compaction_service.apply_chapter_compaction(
            state=state,
            outline=outline,
            chapter_number=chapter_number,
            character_bible=character_bible,
            cfg=cfg,
        )

    async def _finalize_volume_if_needed(
        self,
        *,
        layout: ProjectLayout,
        outline: StoryOutline,
        chapter_number: int,
        canon_state: StoryKernel,
        story_bible: StoryBible,
        canon_store: StoryKernelStore,
        trace: PipelineTrace,
        memory_context: "MemoryContext | None" = None,
    ) -> VolumeAuditReport | None:
        """Run volume-end audit and compact canon for next-volume writing."""
        volume = compaction_service.find_volume_for_chapter(outline, chapter_number)
        if volume is None or chapter_number != volume.end_chapter:
            return None

        try:
            canon_state = await canon_store.load_kernel(canon_state.project_id)
        except Exception as exc:
            _log.warning(
                "volume_finalization_reload_kernel_failed | project=%s | chapter=%s | error=%s",
                canon_state.project_id,
                chapter_number,
                exc,
            )

        chapter_summaries = []
        for ch in range(volume.start_chapter, volume.end_chapter + 1):
            summary = canon_state.chapter_summaries.get(ch, "")
            if summary:
                chapter_summaries.append({"chapter": ch, "summary": summary})

        timeline_events = [
            e.model_dump(mode="json")
            for e in canon_state.timeline
            if volume.start_chapter <= e.chapter <= volume.end_chapter
        ][-120:]

        active_characters = [
            {
                "name": name,
                "alive": state.alive,
                "location": state.location,
                "inventory": state.inventory,
            }
            for name, state in canon_state.get_all_characters().items()
        ]

        active_foreshadowing = [
            fs.model_dump(mode="json")
            for fs in canon_state.foreshadowing
            if fs.status in (ForeshadowingStatus.PLANTED, ForeshadowingStatus.REINFORCED)
        ]

        audit_step = VolumeAuditStep(
            self._router,
            self._builder,
            settings=self._settings,
            trace=trace,
        )

        # 从叙事蓝图中提取本卷相关的阶段和角色弧光里程碑
        blueprint_phases: list[dict[str, Any]] = []
        blueprint_arc_milestones: list[dict[str, Any]] = []
        if self._storage.exists(layout.blueprint_path):
            bp_data = self._storage.load_json(layout.blueprint_path)
            for phase in bp_data.get("narrative_phases", []):
                ps = phase.get("chapter_start", 1)
                pe = phase.get("chapter_end", 9999)
                if ps <= volume.end_chapter and pe >= volume.start_chapter:
                    blueprint_phases.append(phase)
            for arc in bp_data.get("character_arcs", []):
                relevant_milestones = [
                    ms
                    for ms in arc.get("milestones", [])
                    if ms.get("chapter_start", 1) <= volume.end_chapter
                    and ms.get("chapter_end", 9999) >= volume.start_chapter
                ]
                if relevant_milestones:
                    blueprint_arc_milestones.append(
                        {
                            "character": arc.get("character", ""),
                            "arc_summary": arc.get("arc_summary", ""),
                            "milestones": relevant_milestones,
                        }
                    )

        # Gather episodic context for volume audit (best-effort)
        episodic_context: dict[str, Any] | None = None
        if memory_context is not None and memory_context.episodic_memory is not None:
            try:
                query_text = volume.title or volume.arc_goal
                if query_text:
                    episodic_context = {
                        "similar_volumes": await memory_context.episodic_memory.search_similar_volumes(
                            query_text=query_text,
                            top_k=3,
                        )
                    }
            except Exception as exc:
                _log.warning("卷审计情景记忆检索失败: %s", exc)

        report = None
        audit_error: Exception | None = None
        for attempt in range(2):  # 1 retry on failure
            try:
                report = await audit_step.run(
                    VolumeAuditInput(
                        volume=volume,
                        story_synopsis=outline.synopsis or story_bible.premise,
                        chapter_summaries=chapter_summaries,
                        timeline_events=timeline_events,
                        active_characters=active_characters,
                        active_foreshadowing=active_foreshadowing,
                        world_fact_keys=list(canon_state.get_all_world_rules().keys()),
                        blueprint_phases=blueprint_phases,
                        blueprint_arc_milestones=blueprint_arc_milestones,
                        episodic_context=episodic_context,
                    )
                )
                break
            except Exception as exc:
                audit_error = exc
                if attempt == 0:
                    _log.warning(
                        "volume_audit_retry | volume=%d | error=%s",
                        volume.volume_number,
                        exc,
                    )
                    self._on_step(
                        "volume_audit_retry",
                        {"volume": volume.volume_number, "attempt": attempt + 1, "error": str(exc)},
                    )
                else:
                    _log.error(
                        "volume_audit_failed_final | volume=%d | error=%s",
                        volume.volume_number,
                        exc,
                    )
                    self._on_step(
                        "volume_audit_failed",
                        {"volume": volume.volume_number, "error": str(exc)},
                    )

        if report is not None:
            self._storage.save_json(
                layout.volume_audit_report_path(volume.volume_number),
                report.model_dump(mode="json"),
            )
            self._on_step("volume_audit", report)

            # Persist critical consistency issues to project issue ledger
            try:
                from novel_forge.core.schemas.issue_ledger import ProjectIssueLedgerEntry
                from novel_forge.pipeline.long.services.issue_ledger import IssueLedgerService

                ledger_svc = IssueLedgerService(layout.states_dir)

                def _issue_field(issue: Any, key: str, default: str = "") -> str:
                    if isinstance(issue, dict):
                        value = issue.get(key, default)
                    else:
                        value = getattr(issue, key, default)
                    return str(value or default)

                critical_issues = [
                    issue
                    for issue in getattr(report, "consistency_issues", [])
                    if _issue_field(issue, "severity") == "critical"
                ]
                if critical_issues:
                    now_iso = datetime.now(timezone.utc).isoformat()
                    ledger_entries = []
                    for idx, issue in enumerate(critical_issues):
                        entry = ProjectIssueLedgerEntry(
                            issue_id=f"VA-V{volume.volume_number}-{idx + 1:03d}",
                            category=_issue_field(issue, "category", "continuity"),
                            severity="critical",
                            source="volume_audit",
                            chapter_range=list(range(volume.start_chapter, volume.end_chapter + 1)),
                            status="open",
                            summary=(
                                _issue_field(issue, "description") or _issue_field(issue, "summary")
                            )[:200],
                            evidence=_issue_field(issue, "evidence")[:500],
                            created_at=now_iso,
                        )
                        ledger_entries.append(entry)
                    ledger_svc.append_entries(ledger_entries)
                    self._on_step(
                        "volume_audit_issues_persisted",
                        {"volume": volume.volume_number, "critical_count": len(ledger_entries)},
                    )
            except Exception as exc:
                _log.warning(
                    "volume_audit_issue_persist_failed | volume=%d | error=%s",
                    volume.volume_number,
                    exc,
                )

            # Emit structured volume_audit_completed event
            consistency_score = getattr(report, "consistency_score", None)

            def _severity(issue: Any) -> str:
                if isinstance(issue, dict):
                    return str(issue.get("severity", "") or "")
                return str(getattr(issue, "severity", "") or "")

            critical_count = len(
                [i for i in getattr(report, "consistency_issues", []) if _severity(i) == "critical"]
            )
            self._on_step(
                "volume_audit_completed",
                {
                    "volume": volume.volume_number,
                    "consistency_score": consistency_score,
                    "critical_count": critical_count,
                },
            )
            if bool(getattr(self._settings, "long_summary_drift_check_enabled", False)):
                try:
                    from novel_forge.core.schemas.issue_ledger import ProjectIssueLedgerEntry
                    from novel_forge.pipeline.long.services.issue_ledger import IssueLedgerService
                    from novel_forge.pipeline.long.services.summary_drift import check_summary_drift

                    drift_result = check_summary_drift(
                        canon_state,
                        volume_summary=str(getattr(report, "volume_summary", "") or ""),
                        chapter_summaries={
                            item["chapter"]: item["summary"]
                            for item in chapter_summaries
                            if item.get("summary")
                        },
                        volume_start=volume.start_chapter,
                        volume_end=volume.end_chapter,
                    )
                    drift_payload = {
                        "volume": volume.volume_number,
                        "chapter_range": [volume.start_chapter, volume.end_chapter],
                        "facts_checked": drift_result.facts_checked,
                        "facts_missing": drift_result.facts_missing,
                        "facts_contradicted": drift_result.facts_contradicted,
                        "issues": [
                            {
                                "category": issue.category,
                                "severity": issue.severity,
                                "summary": issue.summary,
                                "evidence": issue.evidence,
                            }
                            for issue in drift_result.issues
                        ],
                    }
                    self._storage.save_json(
                        layout.reports_dir
                        / f"volume_{volume.volume_number:03d}_summary_drift.json",
                        drift_payload,
                    )
                    if drift_result.issues:
                        now_iso = datetime.now(timezone.utc).isoformat()
                        IssueLedgerService(layout.states_dir).append_entries(
                            [
                                ProjectIssueLedgerEntry(
                                    issue_id=(f"SD-V{volume.volume_number}-{idx + 1:03d}"),
                                    category="summary_drift",
                                    severity=issue.severity
                                    if issue.severity in {"critical", "high", "medium", "low"}
                                    else "medium",
                                    source="summary_drift",
                                    chapter_range=list(
                                        range(volume.start_chapter, volume.end_chapter + 1)
                                    ),
                                    status="open",
                                    summary=issue.summary[:200],
                                    evidence=issue.evidence[:500],
                                    created_at=now_iso,
                                )
                                for idx, issue in enumerate(drift_result.issues)
                            ]
                        )
                    self._on_step(
                        "summary_drift_checked",
                        {
                            "volume": volume.volume_number,
                            "issues": len(drift_result.issues),
                            "critical": drift_result.has_critical,
                        },
                    )
                except Exception as exc:
                    _log.warning(
                        "summary_drift_check_failed | volume=%d | error=%s",
                        volume.volume_number,
                        exc,
                    )
                    self._on_step(
                        "summary_drift_check_failed",
                        {"volume": volume.volume_number, "error": str(exc)},
                    )
            if critical_count > 0 and bool(
                getattr(self._settings, "long_volume_audit_block_on_critical", False)
            ):
                raise RuntimeError(
                    f"Volume audit found {critical_count} critical issue(s) "
                    f"in volume {volume.volume_number}."
                )
        elif audit_error is not None:
            raise audit_error

        compacted = compaction_service.apply_volume_compaction(canon_state, report)
        await canon_store.save_kernel(compacted)
        await canon_store.save_snapshot(compacted.current_chapter)

        # Archive old volume data (timeline, summaries, exit_states, promises)
        try:
            cutoff_volume_number = max(1, volume.volume_number - 2 + 1)
            cutoff_volume = next(
                (
                    v
                    for v in getattr(outline, "volumes", [])
                    if v.volume_number == cutoff_volume_number
                ),
                None,
            )
            archive_before_chapter = (
                cutoff_volume.start_chapter if cutoff_volume is not None else None
            )
            protected_chapters = {
                v.end_chapter
                for v in getattr(outline, "volumes", [])
                if v.end_chapter <= chapter_number
            }
            protected_chapters.add(chapter_number)
            archive_stats = await canon_store.archive_volume_data(
                compacted.project_id,
                current_volume=compacted.active_volume,
                volumes_to_keep=2,
                archive_before_chapter=archive_before_chapter,
                protected_chapters=protected_chapters,
            )
            if any(v > 0 for v in archive_stats.values()):
                self._on_step(
                    "volume_data_archived", {"volume": volume.volume_number, **archive_stats}
                )
        except Exception as exc:
            _log.warning(
                "volume_data_archive_failed | volume=%d | error=%s", volume.volume_number, exc
            )
            self._on_step(
                "volume_data_archive_failed",
                {"volume": volume.volume_number, "error": str(exc)},
            )
        self._on_step(
            "volume_compact",
            {
                "volume": volume.volume_number,
                "active_volume": compacted.active_volume,
                "active_characters": len(compacted.characters),
                "archived_characters": len(compacted.archived_characters),
                "archived_items": len(compacted.archived_items),
                "world_facts": len(compacted.world_facts),
                "archived_world_facts": len(compacted.archived_world_facts),
            },
        )

        # 卷末记忆管理（非阻塞，失败不影响主流程）
        if memory_context is not None:
            try:
                await memory_context.finalize_volume_memory(
                    volume_number=volume.volume_number,
                    start_chapter=volume.start_chapter,
                    end_chapter=volume.end_chapter,
                    audit_report=report,
                )
                self._on_step(
                    "volume_memory_finalize",
                    {
                        "volume": volume.volume_number,
                        "status": "success",
                    },
                )
            except Exception as exc:
                _log.warning("卷末记忆管理失败: %s", exc)
                self._on_step(
                    "volume_memory_finalize",
                    {
                        "volume": volume.volume_number,
                        "status": "error",
                        "error": str(exc),
                    },
                )

        return report

    # ── Public entry points ───────────────────────────────────────────────

    async def run_chapter(
        self,
        project_id: str,
        chapter_number: int,
        *,
        force_regenerate: bool = False,
        chapter_instruction: str = "",
    ) -> ChapterResult:
        """Execute the v2 long-form chapter pipeline via the dedicated long loop."""
        return await run_long_chapter(
            self,
            project_id,
            chapter_number,
            force_regenerate=force_regenerate,
            chapter_instruction=chapter_instruction,
        )
