"""Generate Dubbing Script pipeline step.

Uses LLM to convert chapter text into structured dubbing script
with segment types, emotion tags, and timing information.
Integrates upstream pipeline outputs (character_bible, style_profile,
editorial_contract, narrator_profile) for rich annotation.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.core.config import Settings
from novel_forge.gateway.router import ModelRouter
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.steps.base import PipelineStep, StepEventCallback
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.tts.creative_direction import apply_audio_creative_bible
from novel_forge.tts.platform.rewrite_profiles import load_rewrite_profile
from novel_forge.tts.runtime.speakable_text import normalize_speakable_text
from novel_forge.tts.schemas import (
    AudioCreativeBible,
    BGMTiming,
    ChapterTTSMetadata,
    DubbingScript,
    DubbingSegment,
    DubbingStyleProfile,
    EmotionTag,
    LanguageRun,
    NarratorVoiceProfile,
    ParalinguisticTag,
    SceneTransition,
    SegmentType,
    SFXCue,
    SoundAsset,
    SoundscapeCue,
    SpeedCurvePoint,
    VocalDirection,
    VocalTag,
    VoiceTeamContract,
)
from novel_forge.tts.script_integrity import (
    compute_dubbing_script_hash,
    compute_source_text_hash,
    refresh_segment_uid,
)
from novel_forge.tts.script_review import (
    annotate_dubbing_llm_review_status,
    apply_dubbing_review_decisions,
    apply_tts_platform_contract,
    build_dubbing_review_stage_cards,
    review_and_repair_dubbing_script,
)
from novel_forge.tts.script_stage_context import (
    ContextProjectionLimits,
    ScriptContextStage,
    ScriptStageContext,
)
from novel_forge.tts.spoken_text_rewrite import sanitize_for_speech

_log = get_logger("tts.pipeline.generate_script")

# Common Chinese speech verbs that can terminate an attribution clause before a
# colon (e.g. "客户说：" / "沈岸沉声道：").  Sorted longest-first so multi-character
# verbs are stripped before their single-character suffixes when recovering the
# speaker subject for fuzzy attribution.
_COLON_SPEECH_VERBS = tuple(
    sorted(
        {
            "说道",
            "问道",
            "答道",
            "喊道",
            "叫道",
            "笑道",
            "哭道",
            "怒道",
            "叹道",
            "沉声道",
            "冷声道",
            "低声道",
            "轻声道",
            "接口道",
            "补充道",
            "追问道",
            "高声",
            "低声",
            "沉声",
            "冷声",
            "反问",
            "回答",
            "开口",
            "低语",
            "呢喃",
            "说",
            "道",
            "问",
            "答",
            "喊",
            "叫",
            "嚷",
            "叹",
        },
        key=len,
        reverse=True,
    )
)


@dataclass
class ScriptGenerationContext:
    """Per-invocation transient state for one script generation run.

    Consolidates all temporary state that was previously scattered across
    ``self._*`` attributes on the step instance, improving concurrency safety
    and testability.

    Author: novel-forge
    """

    tts_metadata: dict[str, Any] | None = None
    scene_intents: list[dict[str, Any]] | None = None
    narrator_distance: str = ""
    story_context: dict[str, Any] = field(default_factory=dict)
    chapter_number: int = 0
    target_provider: str = ""
    target_model: str = ""
    character_names: dict[str, str] = field(default_factory=dict)
    speaker_adjudication_cards: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class GenerateDubbingScriptInput:
    """Input for dubbing script generation."""

    chapter_number: int
    chapter_text: str
    voice_team: VoiceTeamContract
    # ── 上游数据（可选，用于丰富标注） ─────────────────────────────────────────
    character_voices: list[dict[str, Any]] = field(default_factory=list)
    """editorial_contract.character_voices 声纹矩阵"""
    style_profile: dict[str, Any] = field(default_factory=dict)
    """风格档案"""
    scene_context: dict[str, Any] = field(default_factory=dict)
    """场景上下文 (location, time_frame, atmosphere, intent)"""
    story_context: dict[str, Any] = field(default_factory=dict)
    """作品级声音上下文（类型、梗概、时代、主题与声音美学）"""
    narrator_profile: NarratorVoiceProfile | None = None
    """旁白声音画像"""
    audio_creative_bible: AudioCreativeBible | None = None
    """项目级声音身份与混音密度合同"""
    # ── TTS 元数据（来自章节 Pipeline Finalize 阶段） ───────────────────────
    tts_metadata: ChapterTTSMetadata | None = None
    """章节级 TTS 元数据（场景情绪映射、节奏标注、表达通道约束等）"""
    scene_intents: list[dict[str, Any]] | None = None
    """场景意图列表（来自 ChapterPlan.scene_intents），用于场景边界锚定"""
    # ── 兼容旧字段 ───────────────────────────────────────────────────────────
    style_hints: str = ""
    # ── 声音设计提取输入 ─────────────────────────────────────────────────────
    library_assets: list[SoundAsset] = field(default_factory=list)
    """项目声音库已批准资产列表，用于 BGM 库优先匹配"""
    location_acoustics: list[dict[str, Any]] = field(default_factory=list)
    """StoryBible.locations 声学种子卡（name + ambient_sound）；
    初始化定义优先于脚本反推，反推退化为补差"""
    upstream_revision: dict[str, str] = field(default_factory=dict)
    """上游 StoryBible/CharacterBible 内容指纹；上游变化时标记「来源已更新」
    并按需重提取，保留已锁定工作"""
    reference_style_profile: DubbingStyleProfile | None = None
    """用户参考配音脚本抽取出的非复述式风格画像；不包含参考原文。"""
    reference_style_strength: float = 0.0
    """参考风格影响强度；事实、角色声纹和平台硬约束始终优先。"""
    target_provider: str = ""
    """本次脚本将要送往的显式 TTS 平台。"""
    target_model: str = ""
    """与 target_provider 匹配的正式合成模型。"""


@dataclass(frozen=True)
class _SourceQuoteEvidence:
    """One source-anchored quote awaiting bounded role/speaker adjudication."""

    candidate_id: str
    paragraph_index: int
    quote_index: int
    text: str
    normalized_text: str
    normalized_start: int
    normalized_end: int
    context: str
    paragraph_before: str = ""
    paragraph_text: str = ""
    paragraph_after: str = ""
    explicit_character_id: str = ""
    explicit_reason: str = ""
    role_status: str = "ambiguous"
    role_reason: str = ""
    attribution_window: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class _QuoteReviewCandidate:
    """Bounded input for second-pass quote-role and speaker adjudication."""

    candidate_id: str
    segment_index: int
    paragraph_index: int
    quote_index: int
    text: str
    context: str
    current_character_id: str
    paragraph_before: str = ""
    paragraph_text: str = ""
    paragraph_after: str = ""
    role_status: str = "ambiguous"
    role_reason: str = ""
    attribution_window: list[dict[str, str]] = field(default_factory=list)


# NOTE: ScriptGenerationPipeline has been replaced by ScriptOrchestrator
# (see script_orchestrator.py).  The phase-based composition now lives in
# script_phases/ package.  GenerateDubbingScriptStep remains as the
# PipelineStep shell that delegates to ScriptOrchestrator.


class _LegacyScriptGenerationPipeline:
    """DEPRECATED: Kept only for backward compatibility with existing tests.

    New code should use ScriptOrchestrator from script_orchestrator.py.
    This class will be removed in a future release.
    """

    def __init__(self, step: GenerateDubbingScriptStep) -> None:
        self._step = step

    async def run(
        self,
        input_data: GenerateDubbingScriptInput,
        ctx: ScriptGenerationContext,
        script_stage_context: ScriptStageContext,
    ) -> DubbingScript:
        """Execute all generation phases in sequence (legacy path)."""
        from novel_forge.tts.pipeline.script_orchestrator import ScriptOrchestrator

        orchestrator = ScriptOrchestrator(self._step)
        return await orchestrator.run(input_data, ctx, script_stage_context)

    # ── Phase: Generate (steps ②③) ──────────────────────────────────────────

    async def _generate(
        self,
        input_data: GenerateDubbingScriptInput,
        ctx: ScriptGenerationContext,
        character_map: dict[str, str],
    ) -> tuple[DubbingScript, list[Any]]:
        """LLM generation with rule-based fallback + source reconciliation."""
        script = await self._step._generate_script_llm(input_data, character_map)

        if script is not None:
            try:
                script, speaker_candidates = self._step._reconcile_script_with_source(
                    script,
                    input_data.chapter_text,
                    character_map,
                )
            except ValueError as exc:
                _log.warning("LLM script source reconciliation failed: %s", exc)
                self._step._on_step_event(
                    "tts_script_llm_rejected",
                    {
                        "chapter": input_data.chapter_number,
                        "reason": str(exc),
                        "stage": "source_reconciliation",
                    },
                )
                script = None
                speaker_candidates = []

        if script is None:
            _log.warning("LLM script generation failed, falling back to rule-based")
            self._step._on_step_event(
                "tts_script_rule_fallback",
                {"chapter": input_data.chapter_number, "reason": "llm_generation_failed"},
            )
            script = await self._step._generate_script_rule_based(
                input_data.chapter_text,
                input_data.chapter_number,
                character_map,
            )
            script, speaker_candidates = self._step._reconcile_script_with_source(
                script,
                input_data.chapter_text,
                character_map,
            )
        return script, speaker_candidates

    # ── Phase: Adjudicate (step ④) ──────────────────────────────────────────

    async def _adjudicate(
        self,
        script: DubbingScript,
        speaker_candidates: list[Any],
        input_data: GenerateDubbingScriptInput,
    ) -> DubbingScript:
        """Resolve ambiguous quote roles via LLM adjudication."""
        return await self._step._adjudicate_ambiguous_quote_roles(
            script,
            speaker_candidates,
            input_data.voice_team,
        )

    # ── Phase: Normalize (steps ⑤⑥⑦) ───────────────────────────────────────

    def _normalize(
        self,
        script: DubbingScript,
        input_data: GenerateDubbingScriptInput,
        character_map: dict[str, str],
    ) -> DubbingScript:
        """Merge continuations, fold single-char dialogue, validate fidelity."""
        script, _continuation_index_map, continuation_merge_count = (
            self._step._merge_narration_continuations(script)
        )
        if continuation_merge_count:
            metadata = dict(script.metadata)
            reconciliation = metadata.get("source_reconciliation")
            if isinstance(reconciliation, dict):
                metadata["source_reconciliation"] = {
                    **reconciliation,
                    "narration_continuations_merged": int(
                        reconciliation.get("narration_continuations_merged") or 0
                    )
                    + continuation_merge_count,
                }
            script = script.model_copy(update={"metadata": metadata})

        script, _single_char_fold_count = self._step._fold_single_char_dialogue(script)
        if _single_char_fold_count:
            metadata = dict(script.metadata)
            reconciliation = metadata.get("source_reconciliation")
            if isinstance(reconciliation, dict):
                metadata["source_reconciliation"] = {
                    **reconciliation,
                    "single_char_dialogue_folded": _single_char_fold_count,
                }
            else:
                metadata["source_reconciliation"] = {
                    "single_char_dialogue_folded": _single_char_fold_count,
                }
            script = script.model_copy(update={"metadata": metadata})

        self._step._validate_script_source_fidelity(
            script,
            input_data.chapter_text,
            character_map,
        )
        return script

    # ── Phase: Review Rules (step ⑧) ────────────────────────────────────────

    def _review_rules(
        self,
        script: DubbingScript,
        input_data: GenerateDubbingScriptInput,
    ) -> DubbingScript:
        """Deterministic rule-based review and repair."""
        script = review_and_repair_dubbing_script(script, use_humanize_library=True)
        review = script.metadata.get("professional_script_review")
        self._step._on_step_event(
            "tts_script_professional_review_complete",
            {
                "chapter": input_data.chapter_number,
                "removed_numeric_overrides": (
                    int(review.get("removed_generated_numeric_overrides") or 0)
                    if isinstance(review, dict)
                    else 0
                ),
                "recovered_event_sfx": (
                    len(review.get("recovered_event_sfx") or []) if isinstance(review, dict) else 0
                ),
            },
        )
        return script

    # ── Phase: Review LLM (step ⑨) ──────────────────────────────────────────

    async def _review_llm(
        self,
        script: DubbingScript,
        input_data: GenerateDubbingScriptInput,
        script_stage_context: ScriptStageContext,
    ) -> DubbingScript:
        """Expert LLM review of the dubbing script."""
        return await self._step._review_dubbing_script_professionally(
            script,
            input_data.voice_team,
            script_stage_context,
        )

    # ── Phase: Review + Rewrite parallel (steps ⑨⑩) ────────────────────────

    async def _review_and_rewrite(
        self,
        script: DubbingScript,
        input_data: GenerateDubbingScriptInput,
        script_stage_context: ScriptStageContext,
    ) -> DubbingScript:
        """Run expert review and spoken-text rewrite concurrently.

        Review writes metadata and performance repairs; rewrite writes
        spoken_text fields.  Since they target different fields, they can
        safely execute in parallel, reducing latency by ~30-50%.
        """
        reviewed_task = self._review_llm(script, input_data, script_stage_context)
        rewritten_task = self._rewrite(script, input_data, script_stage_context)
        reviewed, rewritten = await asyncio.gather(reviewed_task, rewritten_task)

        # Merge spoken_text from rewrite result into reviewed script.
        # Both operated on the same input, so segment indices align.
        if len(reviewed.segments) == len(rewritten.segments):
            merged_segments = [
                (
                    seg.model_copy(update={"spoken_text": rw_seg.spoken_text})
                    if rw_seg.spoken_text and rw_seg.spoken_text != seg.spoken_text
                    else seg
                )
                for seg, rw_seg in zip(reviewed.segments, rewritten.segments, strict=True)
            ]
            return reviewed.model_copy(update={"segments": merged_segments})
        # Segment count mismatch (unexpected) — prefer reviewed result.
        _log.warning(
            "Review/rewrite segment count mismatch (%d vs %d); skipping spoken_text merge",
            len(reviewed.segments),
            len(rewritten.segments),
        )
        return reviewed

    # ── Phase: Rewrite (step ⑩) ─────────────────────────────────────────────

    async def _rewrite(
        self,
        script: DubbingScript,
        input_data: GenerateDubbingScriptInput,
        script_stage_context: ScriptStageContext,
    ) -> DubbingScript:
        """Independent spoken-text rewrite for natural speech."""
        from novel_forge.tts.spoken_text_rewrite import rewrite_spoken_text  # noqa: PLC0415

        return await rewrite_spoken_text(
            script,
            input_data.voice_team,
            router=self._step._router,
            builder=self._step._builder,
            settings=self._step._settings,
            context=script_stage_context,
            platform_id=self._step._settings.tts_default_provider,
            chapter_number=input_data.chapter_number,
            on_step=self._step._on_step_event,
        )

    # ── Phase: Sound Design (steps ⑪⑫⑬) ────────────────────────────────────

    async def _sound_design(
        self,
        script: DubbingScript,
        input_data: GenerateDubbingScriptInput,
        ctx: ScriptGenerationContext,
    ) -> DubbingScript:
        """Sound design extraction + creative bible application."""
        from novel_forge.tts.sound_design_extraction import extract_sound_design  # noqa: PLC0415

        script = await extract_sound_design(
            script,
            bible=input_data.audio_creative_bible,
            library_assets=input_data.library_assets,
            story_context=ctx.story_context,
            scene_intents=input_data.scene_intents or [],
            location_sound_seeds=input_data.location_acoustics,
            upstream_revision=input_data.upstream_revision,
            router=self._step._router,
            builder=self._step._builder,
            settings=self._step._settings,
        )
        script = self._step._ensure_soundscape_design(script)
        if input_data.audio_creative_bible is not None:
            script = apply_audio_creative_bible(
                script,
                input_data.audio_creative_bible,
                scene_count_hint=len(input_data.scene_intents or []),
            )
        return script

    # ── Phase: Finalize (steps ⑭⑮) ─────────────────────────────────────────

    def _finalize(
        self,
        script: DubbingScript,
        input_data: GenerateDubbingScriptInput,
        ctx: ScriptGenerationContext,
    ) -> DubbingScript:
        """Voice direction enrichment, platform contract, hash computation."""
        script = script.model_copy(
            update={
                "segments": [
                    self._step._enrich_voice_direction(segment) for segment in script.segments
                ],
                "metadata": {
                    **script.metadata,
                    "story_sound_context": dict(ctx.story_context),
                },
            }
        )
        script = apply_tts_platform_contract(
            script,
            provider_id=self._step._settings.tts_default_provider,
            model_id=self._step._settings.tts_default_model,
        )
        script.script_hash = self._step._compute_hash(script)
        script.source_text_hash = compute_source_text_hash(input_data.chapter_text)
        self._step._on_step_event(
            "tts_script_parsed",
            {
                "chapter": input_data.chapter_number,
                "segment_count": len(script.segments),
                "character_count": len(ctx.character_names),
            },
        )
        _log.info(
            "Dubbing script generated: %d segments, %d dialogue, %d narration, "
            "%d soundscapes, %d bgm, %d sfx, %d transitions",
            len(script.segments),
            len(script.dialogue_segments),
            len(script.narration_segments),
            len(script.soundscapes),
            len(script.bgm_suggestions),
            len(script.sfx_cues),
            len(script.scene_transitions),
        )
        return script


# Backward-compatible alias for any code that imported ScriptGenerationPipeline.
ScriptGenerationPipeline = _LegacyScriptGenerationPipeline


class GenerateDubbingScriptStep(PipelineStep[GenerateDubbingScriptInput, DubbingScript]):
    """Generate structured dubbing script from chapter text.

    Uses LLM to analyze text and produce:
    - Segments (narration, dialogue, inner thought, BGM, SFX)
    - Emotion tags per segment (primary + sub emotion)
    - Character identification for dialogue
    - Paralinguistic tags (pauses, breath, laughs, etc.)
    - Speed curves and stress words
    - Scene transitions
    - BGM/SFX suggestions

    Falls back to rule-based generation when LLM is unavailable.
    """

    _LLM_BATCH_TARGET_CHARS = 1400
    _MAX_PROMPT_LOCATION_SIGNATURES = 8

    def __init__(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
        *,
        settings: Settings,
        on_step: StepEventCallback | None = None,
    ) -> None:
        super().__init__(router, builder, settings=settings, on_step=on_step)

    # ── Backward-compatible property proxies for tests that set _tts_metadata etc. ──

    def _ensure_ctx(self) -> ScriptGenerationContext:
        if not hasattr(self, "_ctx"):
            self._ctx = ScriptGenerationContext()
        return self._ctx

    @property
    def _tts_metadata(self) -> dict[str, Any] | None:
        return self._ensure_ctx().tts_metadata

    @_tts_metadata.setter
    def _tts_metadata(self, value: dict[str, Any] | None) -> None:
        self._ensure_ctx().tts_metadata = value

    @property
    def _scene_intents(self) -> list[dict[str, Any]] | None:
        return self._ensure_ctx().scene_intents

    @_scene_intents.setter
    def _scene_intents(self, value: list[dict[str, Any]] | None) -> None:
        self._ensure_ctx().scene_intents = value

    @property
    def _narrator_distance(self) -> str:
        return self._ensure_ctx().narrator_distance

    @_narrator_distance.setter
    def _narrator_distance(self, value: str) -> None:
        self._ensure_ctx().narrator_distance = value

    @property
    def _story_context(self) -> dict[str, Any]:
        return self._ensure_ctx().story_context

    @_story_context.setter
    def _story_context(self, value: dict[str, Any]) -> None:
        self._ensure_ctx().story_context = value

    @property
    def _chapter_number(self) -> int:
        return self._ensure_ctx().chapter_number

    @_chapter_number.setter
    def _chapter_number(self, value: int) -> None:
        self._ensure_ctx().chapter_number = value

    @property
    def _character_names(self) -> dict[str, str]:
        return self._ensure_ctx().character_names

    @_character_names.setter
    def _character_names(self, value: dict[str, str]) -> None:
        self._ensure_ctx().character_names = value

    @property
    def _speaker_adjudication_character_cards(self) -> list[dict[str, Any]]:
        return self._ensure_ctx().speaker_adjudication_cards

    @_speaker_adjudication_character_cards.setter
    def _speaker_adjudication_character_cards(self, value: list[dict[str, Any]]) -> None:
        self._ensure_ctx().speaker_adjudication_cards = value

    @property
    def step_name(self) -> str:
        return "tts_generate_dubbing_script"

    async def _execute(self, input_data: GenerateDubbingScriptInput) -> DubbingScript:
        """Generate dubbing script from chapter text.

        Delegates to :class:`ScriptOrchestrator` which organizes the
        7-phase flow with completeness gate enforcement.
        """
        _log.info(
            "Generating dubbing script for chapter %d (%d chars)",
            input_data.chapter_number,
            len(input_data.chapter_text),
        )

        # Consolidate per-invocation transient state into a single context object.
        self._ctx = ScriptGenerationContext(
            tts_metadata=(
                input_data.tts_metadata.model_dump(mode="json") if input_data.tts_metadata else None
            ),
            scene_intents=input_data.scene_intents,
            narrator_distance=(
                input_data.narrator_profile.narration_distance if input_data.narrator_profile else ""
            ),
            story_context=dict(input_data.story_context),
            chapter_number=input_data.chapter_number,
            target_provider=input_data.target_provider,
            target_model=input_data.target_model,
        )
        script_stage_context = ScriptStageContext(
            character_voices=tuple(input_data.character_voices),
            style_profile=input_data.style_profile,
            story_context=input_data.story_context,
            narrator_profile=input_data.narrator_profile,
            tts_metadata=input_data.tts_metadata,
            scene_intents=tuple(input_data.scene_intents or ()),
            location_acoustics=tuple(input_data.location_acoustics or ()),
            reference_style_profile=input_data.reference_style_profile,
            reference_style_strength=input_data.reference_style_strength,
            chapter_number=input_data.chapter_number,
        )

        from novel_forge.tts.pipeline.script_orchestrator import ScriptOrchestrator

        orchestrator = ScriptOrchestrator(self)
        return await orchestrator.run(input_data, self._ctx, script_stage_context)

    async def _review_dubbing_script_professionally(
        self,
        script: DubbingScript,
        voice_team: VoiceTeamContract,
        stage_context: ScriptStageContext | None = None,
    ) -> DubbingScript:
        """Run the independently routed dubbing-specific performance review."""

        if not bool(getattr(self._settings, "tts_script_llm_review_enabled", True)):
            self._on_step_event(
                "tts_script_llm_review_skipped",
                {"chapter": script.chapter_number, "reason": "disabled"},
            )
            return annotate_dubbing_llm_review_status(script, status="skipped", reason="disabled")

        cards = build_dubbing_review_stage_cards(
            script,
            voice_team,
            tts_platform=(
                getattr(self._ensure_ctx(), "target_provider", "")
                or self._settings.tts_default_provider
            ),
            tts_model=(
                getattr(self._ensure_ctx(), "target_model", "")
                or self._settings.tts_default_model
            ),
        )
        cards.update(
            (stage_context or ScriptStageContext()).project(
                ScriptContextStage.PROFESSIONAL_REVIEW,
                script.segments,
                limits=ContextProjectionLimits.from_settings(self._settings),
            )
        )
        configured_max_tokens = max(
            2048,
            int(getattr(self._settings, "tts_script_llm_review_max_output_tokens", 8192)),
        )
        self._on_step_event(
            "tts_script_llm_review_start",
            {
                "chapter": script.chapter_number,
                "segment_count": len(script.segments),
                "task": TaskType.TTS_REVIEW_DUBBING_SCRIPT.value,
            },
        )
        try:
            response = await self._call_with_retry(
                TaskType.TTS_REVIEW_DUBBING_SCRIPT,
                {"stage_cards": cards},
                max_tokens=self._dynamic_max_tokens(
                    TaskType.TTS_REVIEW_DUBBING_SCRIPT,
                    max(4000, len(script.segments) * 160),
                    prompt_overhead=3500 + sum(len(segment.text) for segment in script.segments),
                    min_tokens=min(4096, configured_max_tokens),
                    max_cap=configured_max_tokens,
                ),
                temperature=float(
                    getattr(self._settings, "tts_review_adjudication_temperature", 0.0)
                ),
                required_keys=(
                    "decisions",
                    "reviewed_segment_count",
                    "overall_verdict",
                    "summary",
                ),
                max_retries=2,
            )
            if not isinstance(response, dict):
                raise TypeError("Dubbing script reviewer returned non-object JSON")
            reviewed = apply_dubbing_review_decisions(
                script,
                response,
                allowed_character_ids={
                    entry.character_id for entry in voice_team.entries if entry.character_id
                },
            )
        except Exception as exc:
            _log.warning("Specialist dubbing script review unavailable: %s", exc)
            self._on_step_event(
                "tts_script_llm_review_unavailable",
                {
                    "chapter": script.chapter_number,
                    "reason": str(exc),
                    "task": TaskType.TTS_REVIEW_DUBBING_SCRIPT.value,
                },
            )
            return annotate_dubbing_llm_review_status(
                script,
                status="unavailable",
                reason=str(exc),
            )

        review = reviewed.metadata.get("professional_script_review")
        llm_review = review.get("llm_review") if isinstance(review, dict) else None
        self._on_step_event(
            "tts_script_llm_review_complete",
            {
                "chapter": script.chapter_number,
                "status": (
                    str(llm_review.get("status") or "passed")
                    if isinstance(llm_review, dict)
                    else "passed"
                ),
                "applied_repairs": (
                    len(llm_review.get("applied_performance_repairs") or [])
                    if isinstance(llm_review, dict)
                    else 0
                ),
                "manual_review_count": (
                    len(llm_review.get("manual_review_segment_indices") or [])
                    if isinstance(llm_review, dict)
                    else 0
                ),
            },
        )
        return reviewed

    def _ensure_soundscape_design(self, script: DubbingScript) -> DubbingScript:
        """Backfill an editable ambience bed when a script has none.

        Soundscape *design* is part of the chapter script, not a request to
        generate an audio file.  Models may legitimately omit ``soundscapes``
        (and the deterministic script fallback has no sound-design pass at
        all), which previously left a chapter with "声场 0 项" after the user
        had clicked Generate Script.  Add one restrained, segment-anchored
        ambience cue for each contiguous scene context.  Asset resolution and
        optional generated-audio routing remain separate downstream choices.
        """

        if script.soundscapes or not script.segments:
            return script

        groups: list[tuple[str, int, int]] = []
        for segment in script.segments:
            context = str(segment.scene_context or "").strip()
            if groups and groups[-1][0] == context:
                previous_context, start_index, _ = groups[-1]
                groups[-1] = (previous_context, start_index, segment.segment_index)
            else:
                groups.append((context, segment.segment_index, segment.segment_index))

        cues: list[SoundscapeCue] = []
        for context, start_index, end_index in groups:
            label_context = context.split("｜", maxsplit=1)[0].strip() if context else ""
            label = f"{label_context[:28]}环境底床" if label_context else "场景环境底床"
            description_context = context or "当前叙事空间"
            cues.append(
                SoundscapeCue(
                    name=label,
                    description=(
                        f"为「{description_context[:160]}」铺设低存在感、可循环的环境底床；"
                        "保持对白清晰，不含可辨语言、突发前景音效或音乐。"
                    ),
                    start_segment_index=start_index,
                    end_segment_index=end_index,
                    volume=0.14,
                    ducking_db=8.0,
                    density=0.25,
                )
            )

        metadata = dict(script.metadata)
        metadata["soundscape_design"] = {
            "mode": "automatic_backfill",
            "reason": "script_output_missing_soundscapes",
            "cue_count": len(cues),
        }
        self._on_step_event(
            "tts_soundscape_auto_designed",
            {"chapter": script.chapter_number, "cue_count": len(cues)},
        )
        return script.model_copy(update={"soundscapes": cues, "metadata": metadata})

    def _build_character_map(
        self,
        voice_team: VoiceTeamContract,
        character_voices: list[dict[str, Any]] | None = None,
    ) -> dict[str, str]:
        """Build character aliases -> canonical character IDs.

        Script parsing must preserve story identity.  Voice lookup happens in
        the synthesis stage, so mapping names directly to voice IDs here loses
        the character ID required by the downstream voice team lookup.

        ``character_voices`` (the editorial voice-profile matrix) may carry extra
        alias/appellation fields for a character; when present they are indexed
        too so short-form names used in the prose can be attributed.  Short forms
        that are not explicitly listed are still recovered by the fuzzy
        attribution fallback in ``_explicit_speaker_evidence``.
        """
        aliases: dict[str, str] = {}
        for entry in voice_team.entries:
            character_id = str(entry.character_id or "").strip()
            character_name = str(entry.character_name or "").strip()
            if not character_id:
                continue
            aliases[character_id] = character_id
            if character_name:
                aliases[character_name] = character_id
        # Index any alias-like fields carried by the voice profiles so that
        # short-form appellations in the prose resolve to the canonical ID.
        for profile in character_voices or []:
            if not isinstance(profile, dict):
                continue
            profile_name = str(
                profile.get("character") or profile.get("character_name") or ""
            ).strip()
            canonical_id = aliases.get(profile_name, "")
            if not canonical_id:
                continue
            for field_name in ("aliases", "alias", "alternative_names", "appellations"):
                raw = profile.get(field_name)
                if not isinstance(raw, list):
                    continue
                for item in raw:
                    alias = str(item or "").strip()
                    if alias and alias not in aliases:
                        aliases[alias] = canonical_id
        return aliases

    async def _generate_script_llm(
        self,
        input_data: GenerateDubbingScriptInput,
        character_map: dict[str, str],
    ) -> DubbingScript | None:
        """LLM-based script generation (primary path).

        Builds prompt with upstream context and parses structured output.
        Returns None on failure (caller should fallback).
        """
        batches = self._partition_chapter_text(input_data.chapter_text)
        max_concurrent = max(
            1,
            min(
                len(batches),
                int(getattr(self._settings, "tts_script_max_concurrent_batches", 2)),
            ),
        )
        semaphore = asyncio.Semaphore(max_concurrent)

        async def generate_batch(batch_index: int, batch_text: str) -> tuple[DubbingScript, bool]:
            stage_cards = self._build_stage_cards(
                input_data,
                character_map,
                chapter_text=batch_text,
            )
            stage_cards["generation_batch"] = {
                "batch_index": batch_index,
                "batch_count": len(batches),
                "previous_context": batches[batch_index - 1][-240:] if batch_index > 0 else "",
                "next_context": (
                    batches[batch_index + 1][:240] if batch_index + 1 < len(batches) else ""
                ),
            }
            self._on_step_event(
                "tts_script_llm_batch_start",
                {
                    "chapter": input_data.chapter_number,
                    "batch_index": batch_index + 1,
                    "batch_count": len(batches),
                    "source_chars": len(batch_text),
                    "max_concurrent": max_concurrent,
                },
            )
            fallback = False
            try:
                batch_timeout_s = float(
                    getattr(self._settings, "tts_script_batch_timeout_s", 360.0) or 360.0
                )
                async with semaphore:
                    raw_json = await asyncio.wait_for(
                        self._call_with_retry(
                            TaskType.TTS_GENERATE_DUBBING_SCRIPT,
                            {"stage_cards": stage_cards},
                            max_tokens=self._dynamic_max_tokens(
                                TaskType.TTS_GENERATE_DUBBING_SCRIPT,
                                max(14000, len(batch_text) * 12),
                                prompt_overhead=6000 + len(batch_text),
                                min_tokens=12288,
                                max_cap=24576,
                            ),
                            temperature=float(
                                getattr(self._settings, "tts_script_generation_temperature", 0.2)
                            ),
                            top_p=float(
                                getattr(self._settings, "tts_script_generation_top_p", 0.95)
                            ),
                            required_keys=("segments",),
                            max_retries=2,
                        ),
                        timeout=batch_timeout_s,
                    )
                if not isinstance(raw_json, dict):
                    raise TypeError("Dubbing script model returned non-object JSON")
                batch_script = self._parse_script_from_json(
                    raw_json,
                    input_data.chapter_number,
                    character_map,
                )
                self._validate_script_text_fidelity(batch_script, batch_text)
                mode = "llm"
            except Exception as exc:
                fallback = True
                mode = "rule_fallback"
                _log.warning(
                    "LLM dubbing batch %d/%d failed, using local fallback: %s",
                    batch_index + 1,
                    len(batches),
                    exc,
                )
                batch_script = await self._generate_script_rule_based(
                    batch_text,
                    input_data.chapter_number,
                    character_map,
                )
            self._on_step_event(
                "tts_script_llm_batch_complete",
                {
                    "chapter": input_data.chapter_number,
                    "batch_index": batch_index + 1,
                    "batch_count": len(batches),
                    "segment_count": len(batch_script.segments),
                    "mode": mode,
                },
            )
            return batch_script, fallback

        try:
            batch_results = await asyncio.gather(
                *(generate_batch(index, text) for index, text in enumerate(batches))
            )
            scripts = [script for script, _fallback in batch_results]
            fallback_batches = [
                index for index, (_script, fallback) in enumerate(batch_results) if fallback
            ]

            script = self._merge_script_batches(
                scripts,
                chapter_number=input_data.chapter_number,
                fallback_batches=fallback_batches,
            )
            self._validate_script_text_fidelity(script, input_data.chapter_text)
            _log.info(
                "LLM script generation succeeded: %d segments across %d batches (%d fallback)",
                len(script.segments),
                len(batches),
                len(fallback_batches),
            )
            return script
        except Exception as exc:
            _log.warning("LLM script generation failed: %s", exc)
            self._on_step_event(
                "tts_script_llm_rejected",
                {"chapter": input_data.chapter_number, "reason": str(exc)},
            )
            return None

    @classmethod
    def _partition_chapter_text(cls, text: str) -> list[str]:
        """Partition at prose boundaries so structured output stays below route limits."""

        paragraphs = [part.strip() for part in re.split(r"\n+", text) if part.strip()]
        units: list[str] = []
        for paragraph in paragraphs:
            if len(paragraph) <= cls._LLM_BATCH_TARGET_CHARS:
                units.append(paragraph)
                continue
            sentences = [part for part in re.split(r"(?<=[。！？!?；;])", paragraph) if part]
            current = ""
            for sentence in sentences:
                if current and len(current) + len(sentence) > cls._LLM_BATCH_TARGET_CHARS:
                    units.append(current)
                    current = ""
                while len(sentence) > cls._LLM_BATCH_TARGET_CHARS:
                    room = cls._LLM_BATCH_TARGET_CHARS - len(current)
                    current += sentence[:room]
                    units.append(current)
                    current = ""
                    sentence = sentence[room:]
                current += sentence
            if current:
                units.append(current)

        batches: list[str] = []
        current_units: list[str] = []
        current_chars = 0
        for unit in units:
            separator_chars = 1 if current_units else 0
            if (
                current_units
                and current_chars + separator_chars + len(unit) > cls._LLM_BATCH_TARGET_CHARS
            ):
                batches.append("\n".join(current_units))
                current_units = []
                current_chars = 0
            current_units.append(unit)
            current_chars += (1 if current_chars else 0) + len(unit)
        if current_units:
            batches.append("\n".join(current_units))
        return batches or [text]

    def _merge_script_batches(
        self,
        scripts: list[DubbingScript],
        *,
        chapter_number: int,
        fallback_batches: list[int],
    ) -> DubbingScript:
        """Merge independently validated batches and remap every cue anchor."""

        segments: list[DubbingSegment] = []
        bgm_suggestions: list[BGMTiming] = []
        sfx_cues: list[SFXCue] = []
        soundscapes: list[SoundscapeCue] = []
        scene_transitions: list[SceneTransition] = []
        for script in scripts:
            offset = len(segments)
            segments.extend(
                segment.model_copy(update={"segment_index": offset + index})
                for index, segment in enumerate(script.segments)
            )
            bgm_suggestions.extend(
                cue.model_copy(
                    update={
                        "start_segment_index": self._shift_segment_index(
                            cue.start_segment_index, offset
                        ),
                        "end_segment_index": self._shift_segment_index(
                            cue.end_segment_index, offset
                        ),
                    }
                )
                for cue in script.bgm_suggestions
            )
            sfx_cues.extend(
                cue.model_copy(
                    update={
                        "trigger_segment_index": self._shift_segment_index(
                            cue.trigger_segment_index, offset
                        )
                    }
                )
                for cue in script.sfx_cues
            )
            soundscapes.extend(
                cue.model_copy(
                    update={
                        "start_segment_index": self._shift_segment_index(
                            cue.start_segment_index, offset
                        ),
                        "end_segment_index": self._shift_segment_index(
                            cue.end_segment_index, offset
                        ),
                    }
                )
                for cue in script.soundscapes
            )
            scene_transitions.extend(script.scene_transitions)

        return DubbingScript(
            chapter_number=chapter_number,
            segments=segments,
            bgm_suggestions=bgm_suggestions,
            sfx_cues=sfx_cues,
            soundscapes=soundscapes,
            scene_transitions=scene_transitions,
            total_estimated_duration_ms=sum(self._estimate_duration(item) for item in segments),
            metadata={
                "generation_batches": {
                    "batch_count": len(scripts),
                    "fallback_batch_indices": fallback_batches,
                }
            },
        )

    @staticmethod
    def _shift_segment_index(value: int | None, offset: int) -> int | None:
        return value + offset if value is not None else None

    def _build_stage_cards(
        self,
        input_data: GenerateDubbingScriptInput,
        character_map: dict[str, str],
        *,
        chapter_text: str | None = None,
    ) -> dict[str, Any]:
        """Build stage_cards dict for prompt template rendering."""
        source_text = input_data.chapter_text if chapter_text is None else chapter_text
        cards: dict[str, Any] = {
            "chapter_text": source_text,
            "voice_team": self._project_voice_team(input_data.voice_team, input_data.chapter_text),
            "character_voices": self._project_character_voices(
                input_data.character_voices,
                input_data.chapter_text,
            ),
            "style_profile": {
                key: input_data.style_profile[key]
                for key in ("speed_range", "emotional_tone", "literary_style", "narrative_pace")
                if key in input_data.style_profile
            },
            "scene_context": input_data.scene_context,
            "story_context": {
                key: input_data.story_context[key]
                for key in (
                    "title",
                    "genre",
                    "premise",
                    "theme",
                    "tone",
                    "world",
                    "era",
                    "themes",
                    "audio_aesthetic",
                )
                if key in input_data.story_context
            },
            "tts_platform": input_data.target_provider or self._settings.tts_default_provider,
            "tts_model": input_data.target_model or self._settings.tts_default_model,
            "chapter_number": input_data.chapter_number,
            "narrative_position": {
                "chapter_number": input_data.chapter_number,
                "is_first_chapter": input_data.chapter_number == 1,
                "is_opening": True,  # At generation time we don't yet know segment positions
            },
        }

        # Inject platform-specific rewrite profile for spoken_text generation guidance.
        cards["rewrite_profile"] = load_rewrite_profile(
            input_data.target_provider or self._settings.tts_default_provider
        )

        if input_data.narrator_profile:
            cards["narrator_profile"] = input_data.narrator_profile.model_dump()

        # Inject TTS metadata for richer LLM annotation
        if input_data.tts_metadata:
            cards["tts_metadata"] = input_data.tts_metadata.model_dump(mode="json")

        # Inject scene intents for boundary-aware script generation
        if input_data.scene_intents:
            cards["scene_intents"] = [
                {
                    key: scene[key]
                    for key in (
                        "scene_id",
                        "location",
                        "time_marker",
                        "emotional_beat",
                        "purpose",
                        "summary",
                        "pov_character",
                        "conflict",
                        "dialogue_subtext",
                        "sensory_notes",
                        "relationship_dynamics",
                    )
                    if key in scene
                }
                for scene in input_data.scene_intents
            ]
        if input_data.audio_creative_bible is not None:
            cards["audio_creative_bible"] = self._project_audio_creative_bible(
                input_data.audio_creative_bible,
                input_data.scene_intents or [],
                input_data.scene_context,
            )

        return cards

    @staticmethod
    def _project_voice_team(
        voice_team: VoiceTeamContract,
        chapter_text: str,
    ) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        for entry in voice_team.entries:
            projected: dict[str, Any] = {
                "character_id": entry.character_id,
                "character_name": entry.character_name,
                # 身份硬约束：性别/年龄/角色定位。下游 LLM 必须据此标注 tone_hint
                # 与 vocal_direction，避免把女角色写成男声口吻（反之亦然）。
                "gender": entry.character_gender,
                "age": entry.character_age,
                "role": entry.character_role,
                "voice_id": entry.voice_id,
            }
            if entry.character_name and entry.character_name in chapter_text:
                performance_profile = entry.performance_profile
                directions = (
                    performance_profile.conditional_directions
                    if performance_profile is not None
                    else []
                )
                if directions:
                    projected["performance_profile"] = {
                        "conditional_directions": [
                            item.model_dump(mode="json") for item in directions
                        ]
                    }
            entries.append(projected)
        return {"entries": entries}

    @staticmethod
    def _project_character_voices(
        character_voices: list[dict[str, Any]],
        chapter_text: str,
    ) -> list[dict[str, Any]]:
        projected: list[dict[str, Any]] = []
        for voice in character_voices:
            name = str(voice.get("character") or voice.get("character_name") or "").strip()
            if name and name not in chapter_text:
                continue
            projected.append(
                {
                    key: voice[key]
                    for key in (
                        "character",
                        "character_name",
                        "sentence_profile",
                        "explanation_bias",
                        "emotion_syntax",
                        "signature_moves",
                        "taboo_patterns",
                        "sample_lines",
                    )
                    if key in voice
                }
            )
        return projected

    @staticmethod
    def _build_speaker_adjudication_cards(
        character_voices: list[dict[str, Any]],
        voice_team: VoiceTeamContract,
    ) -> list[dict[str, Any]]:
        """Project character evidence for source-anchored speaker adjudication.

        The role adjudicator needs more than a bare name/ID list when an
        untagged line belongs to a character introduced outside the immediate
        paragraph.  These cards retain only attribution-relevant voice-profile
        clues and sample lines; they never substitute for source evidence.
        """
        profiles_by_name: dict[str, dict[str, Any]] = {}
        for profile in character_voices:
            if not isinstance(profile, dict):
                continue
            name = str(profile.get("character") or profile.get("character_name") or "").strip()
            if name:
                profiles_by_name[name] = profile

        cards: list[dict[str, Any]] = []
        for entry in voice_team.entries:
            if not entry.character_id:
                continue
            profile = profiles_by_name.get(entry.character_name, {})
            cards.append(
                {
                    "character_id": entry.character_id,
                    "character_name": entry.character_name,
                    # 身份硬约束：裁决说话人时必须与角色性别一致，避免把女角色
                    # 的台词归给男角色（反之亦然）。来自 VoiceCastEntry 持久化字段。
                    "gender": entry.character_gender,
                    "age": entry.character_age,
                    "role": entry.character_role,
                    "sentence_profile": str(profile.get("sentence_profile") or "")[:240],
                    "emotion_syntax": str(profile.get("emotion_syntax") or "")[:240],
                    "signature_moves": [
                        str(item)[:80]
                        for item in profile.get("signature_moves", [])[:5]
                        if str(item).strip()
                    ],
                    "sample_lines": [
                        str(item)[:120]
                        for item in profile.get("sample_lines", [])[:4]
                        if str(item).strip()
                    ],
                }
            )
        return cards

    @classmethod
    def _project_audio_creative_bible(
        cls,
        bible: AudioCreativeBible,
        scene_intents: list[dict[str, Any]],
        scene_context: dict[str, Any],
    ) -> dict[str, Any]:
        payload = bible.model_dump(mode="json")
        locations = {
            str(scene.get("location") or "").strip()
            for scene in scene_intents
            if isinstance(scene, dict)
        }
        locations.add(str(scene_context.get("location") or "").strip())
        locations.discard("")
        signatures = payload.get("location_sound_signatures")
        if isinstance(signatures, dict):
            ranked = sorted(
                signatures.items(),
                key=lambda item: (
                    -max(
                        (
                            2 if location == item[0] else 1
                            for location in locations
                            if location in item[0] or item[0] in location
                        ),
                        default=0,
                    ),
                    len(item[0]),
                    item[0],
                ),
            )
            relevant = [
                (name, descriptors)
                for name, descriptors in ranked
                if any(location in name or name in location for location in locations)
            ]
            selected = (relevant or ranked)[: cls._MAX_PROMPT_LOCATION_SIGNATURES]
            payload["location_sound_signatures"] = dict(selected)
        return payload

    def _extract_json_from_response(self, content: str) -> dict[str, Any]:
        """Extract JSON from LLM response (handles markdown code blocks)."""
        # Try to find JSON in code block
        if "```json" in content:
            start = content.index("```json") + 7
            end = content.index("```", start)
            content = content[start:end].strip()
        elif "```" in content:
            start = content.index("```") + 3
            end = content.index("```", start)
            content = content[start:end].strip()

        data = json.loads(content)
        if not isinstance(data, dict):
            raise TypeError("Dubbing script response must be a JSON object")
        return data

    def _parse_script_from_json(
        self,
        data: dict[str, Any],
        chapter_number: int,
        character_map: dict[str, str],
    ) -> DubbingScript:
        """Parse DubbingScript from LLM JSON output."""
        segments: list[DubbingSegment] = []

        raw_segments = data.get("segments", [])
        if not isinstance(raw_segments, list):
            raw_segments = []
        for index, seg_data in enumerate(raw_segments):
            if not isinstance(seg_data, dict):
                continue
            segment = self._parse_segment(seg_data, character_map, fallback_index=index)
            segments.append(segment)

        # Parse BGM suggestions
        bgm_suggestions: list[BGMTiming] = []
        for bgm_data in data.get("bgm_suggestions", []):
            try:
                bgm_suggestions.append(BGMTiming.model_validate(bgm_data))
            except Exception as exc:
                _log.warning("Failed to parse BGM suggestion: %s", exc)

        # Parse SFX cues
        sfx_cues: list[SFXCue] = []
        for sfx_data in data.get("sfx_cues", []):
            try:
                sfx_cues.append(SFXCue.model_validate(sfx_data))
            except Exception as exc:
                _log.warning("Failed to parse SFX cue: %s", exc)

        # Parse persistent environmental layers (rain, wind, room tone, crowds).
        soundscapes: list[SoundscapeCue] = []
        for soundscape_data in data.get("soundscapes", []):
            try:
                soundscapes.append(SoundscapeCue.model_validate(soundscape_data))
            except Exception as exc:
                _log.warning("Failed to parse soundscape cue: %s", exc)

        # Parse scene transitions
        scene_transitions: list[SceneTransition] = []
        for trans_data in data.get("scene_transitions", []):
            try:
                scene_transitions.append(SceneTransition.model_validate(trans_data))
            except Exception as exc:
                _log.warning("Failed to parse scene transition: %s", exc)

        # Estimate total duration
        total_duration = sum(self._estimate_duration(s) for s in segments)

        return DubbingScript(
            chapter_number=chapter_number,
            segments=segments,
            bgm_suggestions=bgm_suggestions,
            sfx_cues=sfx_cues,
            soundscapes=soundscapes,
            scene_transitions=scene_transitions,
            total_estimated_duration_ms=total_duration,
        )

    def _parse_segment(
        self,
        data: dict[str, Any],
        character_map: dict[str, str],
        *,
        fallback_index: int = 0,
    ) -> DubbingSegment:
        """Parse a single DubbingSegment from JSON."""
        # Parse segment type
        seg_type_str = data.get("segment_type", "narration")
        try:
            segment_type = SegmentType(seg_type_str)
        except ValueError:
            segment_type = SegmentType.NARRATION
        source_text = str(data.get("text", "") or "")

        # Parse emotion
        emotion_str = data.get("emotion", "neutral")
        try:
            emotion = EmotionTag(emotion_str)
        except ValueError:
            emotion = EmotionTag.NEUTRAL

        # Parse sub_emotion
        sub_emotion = None
        sub_emotion_str = data.get("sub_emotion")
        if sub_emotion_str:
            try:
                sub_emotion = EmotionTag(sub_emotion_str)
            except ValueError:
                pass

        # Parse character
        raw_character_id = str(data.get("character_id", "") or "").strip()
        raw_character_name = str(data.get("character_name", "") or "").strip()
        character_id, character_name = self._resolve_character(
            raw_character_id or raw_character_name,
            raw_character_name,
            character_map,
        )
        if segment_type not in (SegmentType.DIALOGUE, SegmentType.INNER_THOUGHT):
            character_id = ""
            character_name = raw_character_name if segment_type == SegmentType.NARRATION else ""

        # Parse paralinguistic tags
        paralinguistic_tags: list[ParalinguisticTag] = []
        for tag_data in data.get("paralinguistic_tags", []):
            try:
                paralinguistic_tags.append(ParalinguisticTag.model_validate(tag_data))
            except Exception:
                pass

        # Parse speed curve
        speed_curve: list[SpeedCurvePoint] = []
        for point_data in data.get("speed_curve", []):
            try:
                speed_curve.append(SpeedCurvePoint.model_validate(point_data))
            except Exception:
                pass

        # Parse transition
        transition = None
        if data.get("transition"):
            try:
                transition = SceneTransition.model_validate(data["transition"])
            except Exception:
                pass

        language_runs: list[LanguageRun] = []
        for run_data in data.get("language_runs", []):
            try:
                language_runs.append(LanguageRun.model_validate(run_data))
            except Exception:
                pass

        vocal_tags: list[VocalTag] = []
        for tag_data in data.get("vocal_tags", []):
            try:
                vocal_tags.append(VocalTag.model_validate(tag_data))
            except Exception:
                pass
        try:
            vocal_direction = VocalDirection.model_validate(data.get("vocal_direction") or {})
        except Exception:
            vocal_direction = VocalDirection()

        spoken_text = self._validated_spoken_text(
            source_text,
            str(data.get("spoken_text", "") or ""),
            segment_type=segment_type,
        )

        return DubbingSegment(
            segment_index=fallback_index,
            segment_type=segment_type,
            character_id=character_id,
            character_name=character_name,
            text=source_text,
            spoken_text=spoken_text,
            emotion=emotion,
            sub_emotion=sub_emotion,
            tone_hint=data.get("tone_hint", ""),
            speed_override=data.get("speed_override"),
            vol_override=data.get("vol_override"),
            pitch_override=data.get("pitch_override"),
            dml_tags=data.get("dml_tags", []),
            start_ms=data.get("start_ms", 0),
            end_ms=data.get("end_ms", 0),
            source_paragraph=data.get("source_paragraph", 0),
            stress_words=data.get("stress_words", []),
            paralinguistic_tags=paralinguistic_tags,
            speed_curve=speed_curve,
            scene_context=data.get("scene_context", ""),
            transition=transition,
            narrator_distance=data.get("narrator_distance", ""),
            pronunciation_overrides=data.get("pronunciation_overrides", []),
            language_boost=data.get("language_boost", "auto"),
            language_code=str(data.get("language_code") or "auto"),
            language_runs=language_runs,
            vocal_direction=vocal_direction,
            vocal_tags=vocal_tags,
            platform_extensions=data.get("platform_extensions", {}),
            voice_effect=data.get("voice_effect", {}),
        )

    @staticmethod
    def _validated_spoken_text(
        source_text: str,
        candidate: str,
        *,
        segment_type: SegmentType,
    ) -> str:
        """Accept only a bounded, source-anchored expansion for short speech.

        DEPRECATED (Phase 4.1): spoken_text generation is now unified into the
        independent rewrite step (rewrite_spoken_text). This method always returns
        empty string to avoid duplicate generation paths and reduce token cost.
        The independent rewrite step handles all spoken_text generation with
        better context and validation.
        """
        # Phase 4.1: Disable inline spoken_text acceptance.
        # All spoken_text is now generated by the independent rewrite step.
        return ""

    def _enrich_voice_direction(self, segment: DubbingSegment) -> DubbingSegment:
        """Compute per-segment vocal direction from emotion, distance, and intensity.

        Replaces the former tag-only backfill with data-driven 6D parameter
        computation.  Each segment gets differentiated vocal_direction based on:
        1. Emotion preset (energy, articulation, breathiness, tension, intimacy)
        2. Narrator distance override (for narration segments)
        3. Intensity scaling (deviation from 0.5 baseline)
        """
        from novel_forge.tts.rules import load_vocal_direction_config
        from novel_forge.tts.schemas import SegmentType, VocalDirection

        config = load_vocal_direction_config()

        # Resolve emotion key
        emotion_key = (
            segment.emotion.value
            if hasattr(segment.emotion, "value")
            else str(segment.emotion or "neutral")
        )
        preset = config.emotion_presets.get(emotion_key)
        if preset is None:
            preset = config.emotion_presets.get("neutral")

        # Start from emotion preset values
        if preset is not None:
            energy = preset.energy
            articulation = preset.articulation
            breathiness = preset.breathiness
            tension = preset.tension
            intimacy = preset.intimacy
            delivery_style = preset.delivery_style
        else:
            energy, articulation, breathiness = 0.5, 0.6, 0.2
            tension, intimacy, delivery_style = 0.3, 0.5, "natural"

        # Apply narrator distance override for narration segments
        if segment.segment_type == SegmentType.NARRATION and segment.narrator_distance:
            distance_preset = config.narrator_distance_presets.get(
                segment.narrator_distance
            )
            if distance_preset is not None:
                intimacy = distance_preset.intimacy
                breathiness = distance_preset.breathiness
                delivery_style = distance_preset.delivery_style

        # Apply intensity scaling: deviate from preset baseline proportionally
        intensity = float(getattr(segment, "emotion_intensity", 0.5) or 0.5)
        if abs(intensity - 0.5) > 0.05 and emotion_key != "neutral":
            delta = (intensity - 0.5) * config.intensity_scale_multiplier
            delta = max(-config.intensity_max_delta, min(config.intensity_max_delta, delta))
            energy = max(0.0, min(1.0, energy + delta))
            tension = max(0.0, min(1.0, tension + delta * 0.7))

        # Build the enriched VocalDirection
        vocal_direction = VocalDirection(
            delivery_style=delivery_style,
            energy=round(energy, 3),
            articulation=round(articulation, 3),
            breathiness=round(breathiness, 3),
            resonance=0.0,
            tension=round(tension, 3),
            intimacy=round(intimacy, 3),
            intent=segment.vocal_direction.intent if segment.vocal_direction else "",
        )

        # Backfill language/tags (preserved from original logic)
        language_code = self._normalize_language_code(
            segment.language_code or segment.language_boost
        )
        language_runs = list(segment.language_runs) or self._infer_language_runs(
            segment.text,
            fallback=language_code,
        )
        if language_code == "auto" and language_runs:
            language_code = language_runs[0].language
        tags = list(segment.vocal_tags)
        existing = {(tag.category, tag.key) for tag in tags}
        additions = [
            VocalTag(
                category="emotion",
                key="primary",
                value=emotion_key,
                source="migrated",
            ),
            VocalTag(
                category="delivery",
                key="style",
                value=vocal_direction.delivery_style,
                source="system",
            ),
            VocalTag(
                category="language",
                key="primary",
                value=language_code,
                source="system",
            ),
        ]
        if segment.stress_words:
            additions.append(
                VocalTag(
                    category="prosody",
                    key="stress_words",
                    value="，".join(segment.stress_words),
                    source="migrated",
                )
            )
        tags.extend(tag for tag in additions if (tag.category, tag.key) not in existing)
        return segment.model_copy(
            update={
                "vocal_direction": vocal_direction,
                "language_code": language_code,
                "language_runs": language_runs,
                "vocal_tags": tags,
            }
        )

    @staticmethod
    def _normalize_language_code(value: str) -> str:
        normalized = str(value or "auto").strip().lower()
        aliases = {
            "chinese": "zh",
            "chinese,yue": "yue",
            "english": "en",
            "japanese": "ja",
            "korean": "ko",
            "cantonese": "yue",
        }
        return aliases.get(normalized, normalized or "auto")

    @classmethod
    def _infer_language_runs(cls, text: str, *, fallback: str = "auto") -> list[LanguageRun]:
        """Create conservative script-based runs; ASR later verifies the result."""

        def classify(char: str) -> str:
            code = ord(char)
            if 0x3040 <= code <= 0x30FF:
                return "ja"
            if 0xAC00 <= code <= 0xD7AF:
                return "ko"
            if 0x4E00 <= code <= 0x9FFF:
                return "zh" if fallback in {"", "auto"} else fallback
            if char.isascii() and char.isalpha():
                return "en"
            return ""

        runs: list[LanguageRun] = []
        run_language = ""
        run_start = 0
        for index, char in enumerate(text):
            language = classify(char)
            if not language:
                continue
            if not run_language:
                run_language = language
                run_start = index
                continue
            if language != run_language:
                chunk = text[run_start:index].strip()
                if chunk:
                    start = run_start + max(text[run_start:index].find(chunk), 0)
                    runs.append(
                        LanguageRun(
                            language=run_language,
                            text=chunk,
                            start_char=start,
                            end_char=start + len(chunk),
                        )
                    )
                run_language = language
                run_start = index
        if run_language:
            chunk = text[run_start:].strip()
            if chunk:
                start = run_start + max(text[run_start:].find(chunk), 0)
                runs.append(
                    LanguageRun(
                        language=run_language,
                        text=chunk,
                        start_char=start,
                        end_char=start + len(chunk),
                    )
                )
        if not runs and text.strip():
            stripped = text.strip()
            start = max(text.find(stripped), 0)
            runs.append(
                LanguageRun(
                    language=fallback or "auto",
                    text=stripped,
                    start_char=start,
                    end_char=start + len(stripped),
                )
            )
        return runs

    def _resolve_character(
        self,
        raw_id_or_name: str,
        raw_name: str,
        character_map: dict[str, str],
    ) -> tuple[str, str]:
        """Resolve model-provided IDs/names to the canonical team identity."""
        candidate = (raw_id_or_name or raw_name).strip()
        if not candidate:
            return "", raw_name
        if candidate in character_map:
            canonical_id = character_map[candidate]
            names = getattr(self, "_character_names", {})
            return canonical_id, names.get(canonical_id, raw_name)

        matches = {
            canonical_id
            for alias, canonical_id in character_map.items()
            if candidate in alias or alias in candidate
        }
        if len(matches) == 1:
            canonical_id = next(iter(matches))
            names = getattr(self, "_character_names", {})
            return canonical_id, names.get(canonical_id, raw_name)
        return "", raw_name or candidate

    async def _generate_script_rule_based(
        self,
        text: str,
        chapter_number: int,
        character_map: dict[str, str],
    ) -> DubbingScript:
        """Rule-based script generation (fallback when LLM not available).

        Parses text for dialogue markers and assigns segments.
        """
        segments: list[DubbingSegment] = []
        segment_index = 0

        # A generated chapter uses one logical paragraph per line, but exports
        # are inconsistent about whether they leave a blank line between them.
        # Treat either form as a boundary so fallback mode never merges an
        # entire scene into an uneditable narrator segment.
        paragraphs = re.split(r"\n+", text)
        source_before = ""
        previous_speaker_id = ""

        for para_idx, paragraph in enumerate(paragraphs):
            paragraph = paragraph.strip()
            if not paragraph:
                continue

            # Check if paragraph contains dialogue
            paragraph_scene_id = self._match_paragraph_to_scene(paragraph)
            dialogue_segments = self._extract_dialogues(
                paragraph,
                character_map,
                scene_id=paragraph_scene_id,
                source_before=source_before,
                previous_speaker_id=previous_speaker_id,
            )

            if dialogue_segments:
                last_end = 0
                for ds in dialogue_segments:
                    # Keep every non-quote character between direct-speech
                    # spans.  It is narration, not disposable attribution.
                    narration_text = paragraph[last_end : ds["start"]].strip()
                    if narration_text:
                        _narr_emotion, _narr_intensity = self._infer_emotion(
                            narration_text,
                            0,
                            len(narration_text),
                            scene_id=paragraph_scene_id,
                            segment_position=segment_index,
                            total_segments=len(paragraphs),
                            is_narration=True,
                        )
                        segments.append(
                            DubbingSegment(
                                segment_index=segment_index,
                                segment_type=SegmentType.NARRATION,
                                text=narration_text,
                                spoken_text=sanitize_for_speech(narration_text),
                                emotion=_narr_emotion,
                                emotion_intensity=_narr_intensity,
                                source_paragraph=para_idx,
                                scene_context=self._scene_context(paragraph_scene_id),
                                narrator_distance=getattr(self, "_narrator_distance", ""),
                            )
                        )
                        segment_index += 1

                    segments.append(
                        DubbingSegment(
                            segment_index=segment_index,
                            segment_type=SegmentType.DIALOGUE,
                            character_id=ds.get("character_id", ""),
                            character_name=ds.get("character_name", ""),
                            text=ds["text"],
                            spoken_text=sanitize_for_speech(ds["text"]),
                            emotion=ds.get("emotion", EmotionTag.NEUTRAL),
                            emotion_intensity=ds.get("emotion_intensity", 0.5),
                            source_paragraph=para_idx,
                            scene_context=self._scene_context(paragraph_scene_id),
                        )
                    )
                    segment_index += 1
                    last_end = ds["end"]
                    if ds["character_id"]:
                        previous_speaker_id = ds["character_id"]

                if last_end < len(paragraph):
                    narration_text = paragraph[last_end:].strip()
                    if narration_text:
                        _narr_emotion, _narr_intensity = self._infer_emotion(
                            narration_text,
                            0,
                            len(narration_text),
                            scene_id=paragraph_scene_id,
                            segment_position=segment_index,
                            total_segments=len(paragraphs),
                            is_narration=True,
                        )
                        segments.append(
                            DubbingSegment(
                                segment_index=segment_index,
                                segment_type=SegmentType.NARRATION,
                                text=narration_text,
                                spoken_text=sanitize_for_speech(narration_text),
                                emotion=_narr_emotion,
                                emotion_intensity=_narr_intensity,
                                source_paragraph=para_idx,
                                scene_context=self._scene_context(paragraph_scene_id),
                                narrator_distance=getattr(self, "_narrator_distance", ""),
                            )
                        )
                        segment_index += 1
            else:
                # Pure narration paragraph
                _para_emotion, _para_intensity = self._infer_emotion(
                    paragraph,
                    0,
                    len(paragraph),
                    scene_id=paragraph_scene_id,
                    segment_position=segment_index,
                    total_segments=len(paragraphs),
                    is_narration=True,
                )
                segments.append(
                    DubbingSegment(
                        segment_index=segment_index,
                        segment_type=SegmentType.NARRATION,
                        text=paragraph,
                        spoken_text=sanitize_for_speech(paragraph),
                        emotion=_para_emotion,
                        emotion_intensity=_para_intensity,
                        source_paragraph=para_idx,
                        scene_context=self._scene_context(paragraph_scene_id),
                        narrator_distance=getattr(self, "_narrator_distance", ""),
                    )
                )
                segment_index += 1

            source_before = f"{source_before}\n{paragraph}"[-1000:]

        # Estimate total duration
        total_duration = sum(self._estimate_duration(s) for s in segments)

        return DubbingScript(
            chapter_number=chapter_number,
            segments=segments,
            total_estimated_duration_ms=total_duration,
        )

    def _extract_dialogues(
        self,
        text: str,
        character_map: dict[str, str],
        *,
        scene_id: str | None = None,
        source_before: str = "",
        previous_speaker_id: str = "",
    ) -> list[dict[str, Any]]:
        """Extract direct speech in document order without deleting narration.

        Only high-confidence turn structure becomes dialogue in fallback mode.
        Semantically ambiguous quoted spans stay inside narration because the
        fallback path has no model available to adjudicate them safely.
        """
        del source_before, previous_speaker_id
        quote_pattern = re.compile(
            r"“(?P<curly>[^”\n]+)”|「(?P<corner>[^」\n]+)」|『(?P<book>[^』\n]+)』|\"(?P<straight>[^\"\n]+)\""
        )
        dialogues: list[dict[str, Any]] = []
        paragraph_has_direct_turn = False
        for match in quote_pattern.finditer(text):
            content = next((value for value in match.groupdict().values() if value is not None), "")
            content = content.strip()
            if not content:
                continue
            has_sentence_delivery = content.rstrip().endswith(("。", "！", "？", "!", "?", "…"))
            has_direct_structure = self._has_direct_speech_structure(
                text,
                match.start(),
                content,
            ) or (paragraph_has_direct_turn and has_sentence_delivery)
            if not has_direct_structure:
                continue
            paragraph_has_direct_turn = True
            character_id, character_name = self._infer_dialogue_speaker(
                text,
                match.start(),
                match.end(),
                character_map,
            )
            _dlg_emotion, _dlg_intensity = self._infer_emotion(
                text,
                match.start(),
                match.end(),
                scene_id=scene_id,
                character_name=character_name or None,
                is_narration=False,
            )
            dialogues.append(
                {
                    "text": content,
                    "character_id": character_id,
                    "character_name": character_name,
                    "emotion": _dlg_emotion,
                    "emotion_intensity": _dlg_intensity,
                    "start": match.start(),
                    "end": match.end(),
                }
            )
        return dialogues

    @staticmethod
    def _has_direct_speech_structure(
        text: str,
        quote_start: int,
        content: str,
    ) -> bool:
        """Recognize only language-independent, high-confidence turn structure.

        Semantic role is deliberately not inferred from topic words.  A quote
        is structurally direct speech only when it is introduced as a turn or
        occupies the start of a prose paragraph with sentence-level delivery.
        Everything else remains bounded input for semantic adjudication.
        """
        before = text[:quote_start].strip()
        paragraph_prefix = re.split(r"\n+", before)[-1].strip()
        introduced_turn = paragraph_prefix.endswith(("：", ":"))
        starts_paragraph_turn = not paragraph_prefix and content.rstrip().endswith(
            ("。", "！", "？", "!", "?", "…")
        )
        return introduced_turn or starts_paragraph_turn

    def _infer_dialogue_speaker(
        self,
        text: str,
        quote_start: int,
        quote_end: int,
        character_map: dict[str, str],
    ) -> tuple[str, str]:
        """Resolve only a structurally attributed speaker."""
        character_id, _reason = self._explicit_speaker_evidence(
            text,
            quote_start,
            quote_end,
            character_map,
        )
        if not character_id:
            return "", ""
        return character_id, getattr(self, "_character_names", {}).get(character_id, "")

    @staticmethod
    def _strip_quoted_content(text: str) -> str:
        """Remove quoted words before looking for a grammatical speaker."""
        return re.sub(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"]*"', "", text)

    @staticmethod
    def _normalized_source_text(text: str) -> str:
        """Normalize only whitespace and quote marks omitted for TTS delivery."""
        text = text or ""
        # Standalone punctuation-only separator lines ("---", "***", "……")
        # are dropped by _merge_narration_continuations during normalization.
        # Strip the same lines from the expected source so fidelity validation
        # stays consistent with the drop rule.  Single-char lines are left
        # untouched so character-level offset mapping keeps working.
        text = re.sub(r"(?m)^[^“”「」『』\"\w\s]{2,}$", "", text)
        return re.sub(r'[\s"“”「」『』]', "", text)

    def _validate_script_text_fidelity(
        self,
        script: DubbingScript,
        source_text: str,
    ) -> None:
        """Validate lossless source coverage without trusting model labels."""
        expected_text = self._normalized_source_text(source_text)
        actual_text = self._normalized_source_text(
            "".join(segment.text for segment in script.segments)
        )
        if not expected_text or actual_text != expected_text:
            raise ValueError("dubbing script does not preserve the chapter source text")

    def _source_dialogue_evidence(
        self,
        source_text: str,
        character_map: dict[str, str],
    ) -> list[_SourceQuoteEvidence]:
        """Classify source quotes and attach only high-confidence speaker evidence."""
        paragraphs = re.split(r"\n+", source_text)
        quote_pattern = re.compile(
            r"“(?P<curly>[^”\n]+)”|「(?P<corner>[^」\n]+)」|"
            r'『(?P<book>[^』\n]+)』|"(?P<straight>[^"\n]+)"'
        )

        def attribution_window(paragraph_index: int) -> list[dict[str, str]]:
            """Return a small source-only character-mention window for the LLM."""
            window: list[dict[str, str]] = []
            seen: set[tuple[int, str]] = set()
            for index in range(
                max(0, paragraph_index - 2), min(len(paragraphs), paragraph_index + 3)
            ):
                paragraph = paragraphs[index].strip()
                if not paragraph:
                    continue
                for alias, character_id in character_map.items():
                    alias = str(alias or "").strip()
                    if not alias or alias == character_id or alias not in paragraph:
                        continue
                    key = (index, character_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    window.append(
                        {
                            "paragraph_index": str(index),
                            "character_id": character_id,
                            "character_name": getattr(self, "_character_names", {}).get(
                                character_id,
                                alias,
                            ),
                            "evidence": paragraph[:480],
                        }
                    )
            return window

        evidence: list[_SourceQuoteEvidence] = []
        for paragraph_index, paragraph in enumerate(paragraphs):
            paragraph_has_direct_turn = False
            quote_index = 0
            for match in quote_pattern.finditer(paragraph):
                content = next(
                    (value for value in match.groupdict().values() if value is not None),
                    "",
                ).strip()
                if not content:
                    continue
                explicit_character_id, explicit_reason = self._explicit_speaker_evidence(
                    paragraph,
                    match.start(),
                    match.end(),
                    character_map,
                )
                has_direct_structure = self._has_direct_speech_structure(
                    paragraph,
                    match.start(),
                    content,
                ) or (
                    paragraph_has_direct_turn
                    and content.rstrip().endswith(("。", "！", "？", "!", "?", "…"))
                )
                if has_direct_structure:
                    paragraph_has_direct_turn = True
                role_status = (
                    "spoken" if explicit_character_id or has_direct_structure else "ambiguous"
                )
                role_reason = (
                    explicit_reason
                    if explicit_character_id
                    else "direct_speech_structure"
                    if has_direct_structure
                    else "semantic_adjudication_required"
                )
                context_parts = [
                    item.strip()
                    for item in (
                        paragraphs[paragraph_index - 1] if paragraph_index > 0 else "",
                        paragraph,
                        paragraphs[paragraph_index + 1]
                        if paragraph_index + 1 < len(paragraphs)
                        else "",
                    )
                    if item.strip()
                ]
                paragraph_before = (
                    paragraphs[paragraph_index - 1].strip() if paragraph_index > 0 else ""
                )
                paragraph_after = (
                    paragraphs[paragraph_index + 1].strip()
                    if paragraph_index + 1 < len(paragraphs)
                    else ""
                )
                evidence.append(
                    _SourceQuoteEvidence(
                        candidate_id=f"p{paragraph_index}_q{quote_index}",
                        paragraph_index=paragraph_index,
                        quote_index=quote_index,
                        text=content,
                        normalized_text=self._normalized_source_text(content),
                        normalized_start=len(
                            self._normalized_source_text(paragraph[: match.start()])
                        ),
                        normalized_end=len(self._normalized_source_text(paragraph[: match.start()]))
                        + len(self._normalized_source_text(content)),
                        context="\n".join(context_parts),
                        paragraph_before=paragraph_before,
                        paragraph_text=paragraph.strip(),
                        paragraph_after=paragraph_after,
                        explicit_character_id=explicit_character_id,
                        explicit_reason=explicit_reason,
                        role_status=role_status,
                        role_reason=role_reason,
                        attribution_window=attribution_window(paragraph_index),
                    )
                )
                quote_index += 1
        return evidence

    def _explicit_speaker_evidence(
        self,
        paragraph: str,
        quote_start: int,
        quote_end: int,
        character_map: dict[str, str],
    ) -> tuple[str, str]:
        """Return only an ID anchored by a name in a colon-introduced turn."""
        del quote_end
        before = self._strip_quoted_content(paragraph[:quote_start]).rstrip()
        if not before.endswith(("：", ":")):
            return "", ""

        clause = re.split(r"[。！？!?\n]", before[:-1])[-1]
        matches: list[tuple[int, str]] = []
        for alias, character_id in character_map.items():
            alias = str(alias or "").strip()
            if not alias or alias == character_id:
                continue
            position = clause.rfind(alias)
            if position >= 0:
                matches.append((position, character_id))
        if matches:
            _position, character_id = max(matches)
            return character_id, "named_colon_turn"
        return self._fuzzy_speaker_from_clause(clause, character_map)

    @staticmethod
    def _fuzzy_speaker_from_clause(
        clause: str,
        character_map: dict[str, str],
    ) -> tuple[str, str]:
        """Recover a speaker from a colon attribution clause by unique fuzzy match.

        Exact alias matching fails when the prose refers to a character by a
        short form (e.g. "客户") while the voice team registers a descriptive
        full name (e.g. "晚清深宅案客户").  After stripping a trailing speech
        verb, the subject is fuzzy-matched against every alias; a speaker is
        returned only when exactly one canonical character matches, so ambiguous
        short forms never produce a guess.
        """
        clause = clause.strip()
        if not clause:
            return "", ""
        subject = clause
        for verb in _COLON_SPEECH_VERBS:
            if subject.endswith(verb) and len(subject) > len(verb):
                subject = subject[: -len(verb)]
                break
        subject = subject.rstrip("：:，,、地 ").strip()
        if len(subject) < 2:
            return "", ""
        matches = {
            character_id
            for alias, character_id in character_map.items()
            if alias and alias != character_id and (subject in alias or alias in subject)
        }
        if len(matches) == 1:
            return next(iter(matches)), "fuzzy_colon_turn"
        return "", ""

    def _align_segment_paragraphs(
        self,
        script: DubbingScript,
        source_text: str,
    ) -> list[DubbingSegment]:
        """Derive paragraph ownership from exact source offsets, not model output."""
        self._validate_script_text_fidelity(script, source_text)
        paragraph_ranges: list[tuple[int, int, int]] = []
        source_cursor = 0
        for paragraph_index, paragraph in enumerate(re.split(r"\n+", source_text)):
            normalized = self._normalized_source_text(paragraph)
            if not normalized:
                continue
            paragraph_ranges.append(
                (paragraph_index, source_cursor, source_cursor + len(normalized))
            )
            source_cursor += len(normalized)

        aligned: list[DubbingSegment] = []
        segment_cursor = 0
        for segment in script.segments:
            normalized = self._normalized_source_text(segment.text)
            segment_end = segment_cursor + len(normalized)
            owner = next(
                (
                    paragraph_index
                    for paragraph_index, paragraph_start, paragraph_end in paragraph_ranges
                    if segment_cursor >= paragraph_start
                    and segment_cursor < paragraph_end
                    and segment_end <= paragraph_end
                ),
                None,
            )
            if owner is None:
                raise ValueError("dubbing segment crosses a source paragraph boundary")
            aligned.append(segment.model_copy(update={"source_paragraph": owner}))
            segment_cursor = segment_end
        return aligned

    def _expand_embedded_quote_segments(
        self,
        aligned_segments: list[DubbingSegment],
        evidence: list[_SourceQuoteEvidence],
    ) -> tuple[list[DubbingSegment], dict[int, int], dict[int, int], int]:
        """Split quotes swallowed by a narration segment into reviewable turns.

        LLMs occasionally preserve the source text but return an entire
        ``action + quote + action`` paragraph as narration.  Text-fidelity
        validation alone cannot catch that acoustic ownership error.  Exact
        paragraph-relative quote offsets let us recover a bounded spoken
        candidate without guessing its role or speaker; semantic adjudication
        still decides whether the quoted text was actually spoken.
        """

        evidence_by_paragraph: dict[int, list[_SourceQuoteEvidence]] = {}
        for item in evidence:
            evidence_by_paragraph.setdefault(item.paragraph_index, []).append(item)
        for items in evidence_by_paragraph.values():
            items.sort(key=lambda item: (item.normalized_start, item.normalized_end))

        expanded: list[DubbingSegment] = []
        origin_first: dict[int, int] = {}
        origin_last: dict[int, int] = {}
        paragraph_cursors: dict[int, int] = {}
        extracted_count = 0

        for segment in aligned_segments:
            paragraph_index = segment.source_paragraph
            normalized = self._normalized_source_text(segment.text)
            segment_start = paragraph_cursors.get(paragraph_index, 0)
            segment_end = segment_start + len(normalized)
            paragraph_cursors[paragraph_index] = segment_end
            origin_index = segment.segment_index
            origin_first.setdefault(origin_index, len(expanded))

            embedded = [
                item
                for item in evidence_by_paragraph.get(paragraph_index, [])
                if segment.segment_type == SegmentType.NARRATION
                and item.normalized_start >= segment_start
                and item.normalized_end <= segment_end
            ]
            if not embedded:
                expanded.append(segment)
                origin_last[origin_index] = len(expanded) - 1
                continue

            # Map normalized character positions back to the model's raw text.
            # Quote marks and whitespace may have been omitted by the model,
            # so the source evidence supplies the canonical quote contents.
            raw_positions = [
                index
                for index, character in enumerate(segment.text)
                if self._normalized_source_text(character)
            ]
            raw_cursor = 0
            for item in embedded:
                local_start = item.normalized_start - segment_start
                local_end = item.normalized_end - segment_start
                if local_start >= len(raw_positions) or local_end <= local_start:
                    continue
                raw_start = raw_positions[local_start]
                raw_end = raw_positions[min(local_end, len(raw_positions)) - 1] + 1
                before = segment.text[raw_cursor:raw_start].rstrip()
                if before.endswith(("“", "「", "『", '"')):
                    before = before[:-1].rstrip()
                if before:
                    expanded.append(
                        segment.model_copy(
                            update={
                                "text": before,
                                "spoken_text": "",
                                "character_id": "",
                                "character_name": "",
                                "segment_type": SegmentType.NARRATION,
                            }
                        )
                    )
                character_id = item.explicit_character_id
                expanded.append(
                    segment.model_copy(
                        update={
                            "text": item.text,
                            "spoken_text": "",
                            "segment_type": SegmentType.DIALOGUE,
                            "character_id": character_id,
                            "character_name": getattr(self, "_character_names", {}).get(
                                character_id,
                                "",
                            ),
                            "narrator_distance": "",
                        }
                    )
                )
                extracted_count += 1
                raw_cursor = raw_end
                while raw_cursor < len(segment.text) and segment.text[raw_cursor] in '”」』"':
                    raw_cursor += 1
            after = segment.text[raw_cursor:].lstrip()
            if after:
                expanded.append(
                    segment.model_copy(
                        update={
                            "text": after,
                            "spoken_text": "",
                            "character_id": "",
                            "character_name": "",
                            "segment_type": SegmentType.NARRATION,
                        }
                    )
                )
            origin_last[origin_index] = len(expanded) - 1

        reindexed = [
            segment.model_copy(update={"segment_index": index})
            for index, segment in enumerate(expanded)
        ]
        return reindexed, origin_first, origin_last, extracted_count

    def _reconcile_script_with_source(
        self,
        script: DubbingScript,
        source_text: str,
        character_map: dict[str, str],
    ) -> tuple[DubbingScript, list[_QuoteReviewCandidate]]:
        """Repair quote classification and build a bounded speaker-review set."""
        aligned_segments = self._align_segment_paragraphs(script, source_text)
        evidence = self._source_dialogue_evidence(source_text, character_map)
        (
            aligned_segments,
            origin_first,
            origin_last,
            embedded_quote_extractions,
        ) = self._expand_embedded_quote_segments(aligned_segments, evidence)
        by_key: dict[tuple[int, str], list[_SourceQuoteEvidence]] = {}
        for item in evidence:
            by_key.setdefault(
                (item.paragraph_index, item.normalized_text),
                [],
            ).append(item)

        evidence_by_old_index: dict[int, _SourceQuoteEvidence] = {}
        converted_indices: set[int] = set()
        explicit_corrections: list[dict[str, Any]] = []
        classified: list[DubbingSegment] = []
        spoken_types = {SegmentType.DIALOGUE, SegmentType.INNER_THOUGHT}
        for old_index, segment in enumerate(aligned_segments):
            updated = segment
            if segment.segment_type in spoken_types:
                key = (
                    segment.source_paragraph,
                    self._normalized_source_text(segment.text),
                )
                candidates = by_key.get(key, [])
                if not candidates:
                    converted_indices.add(old_index)
                    updated = segment.model_copy(
                        update={
                            "segment_type": SegmentType.NARRATION,
                            "character_id": "",
                            "character_name": "",
                            "spoken_text": "",
                            "tone_hint": "",
                        }
                    )
                else:
                    source_evidence = candidates.pop(0)
                    evidence_by_old_index[old_index] = source_evidence
                    if (
                        source_evidence.explicit_character_id
                        and segment.character_id != source_evidence.explicit_character_id
                    ):
                        character_id = source_evidence.explicit_character_id
                        updated = segment.model_copy(
                            update={
                                "character_id": character_id,
                                "character_name": getattr(self, "_character_names", {}).get(
                                    character_id,
                                    "",
                                ),
                            }
                        )
                        explicit_corrections.append(
                            {
                                "candidate_id": source_evidence.candidate_id,
                                "segment_index": old_index,
                                "character_id": character_id,
                                "reason": source_evidence.explicit_reason,
                            }
                        )
            classified.append(updated)

        if any(item.role_status == "spoken" for remaining in by_key.values() for item in remaining):
            raise ValueError("dubbing script omitted or misclassified direct speech")

        # When a visible quoted value was emitted as a one-character dialogue,
        # fold it back into its adjacent narrator span.  This removes the
        # unstable micro-segment instead of merely changing its voice.
        merged: list[DubbingSegment] = []
        merged_old_indices: list[list[int]] = []
        merged_contains_conversion: list[bool] = []
        for old_index, segment in enumerate(classified):
            is_converted = old_index in converted_indices
            can_merge = bool(
                merged
                and segment.segment_type == SegmentType.NARRATION
                and merged[-1].segment_type == SegmentType.NARRATION
                and segment.source_paragraph == merged[-1].source_paragraph
                and (is_converted or merged_contains_conversion[-1])
            )
            if can_merge:
                previous = merged[-1]
                merged[-1] = previous.model_copy(
                    update={
                        "text": previous.text + segment.text,
                        "spoken_text": "",
                        "transition": previous.transition or segment.transition,
                    }
                )
                merged_old_indices[-1].append(old_index)
                merged_contains_conversion[-1] = True
            else:
                merged.append(segment)
                merged_old_indices.append([old_index])
                merged_contains_conversion.append(is_converted)

        old_to_new: dict[int, int] = {}
        reindexed: list[DubbingSegment] = []
        for new_index, (segment, old_indices) in enumerate(
            zip(merged, merged_old_indices, strict=True)
        ):
            reindexed.append(segment.model_copy(update={"segment_index": new_index}))
            for old_index in old_indices:
                old_to_new[old_index] = new_index

        def remap(index: int | None, *, end_boundary: bool = False) -> int | None:
            if index is None:
                return None
            expanded_index = (
                origin_last.get(index, index) if end_boundary else origin_first.get(index, index)
            )
            return old_to_new.get(expanded_index, expanded_index)

        updated_bgm = [
            cue.model_copy(
                update={
                    "start_segment_index": remap(cue.start_segment_index),
                    "end_segment_index": remap(cue.end_segment_index, end_boundary=True),
                }
            )
            for cue in script.bgm_suggestions
        ]
        updated_sfx = [
            cue.model_copy(update={"trigger_segment_index": remap(cue.trigger_segment_index)})
            for cue in script.sfx_cues
        ]
        updated_soundscapes = [
            cue.model_copy(
                update={
                    "start_segment_index": remap(cue.start_segment_index),
                    "end_segment_index": remap(cue.end_segment_index, end_boundary=True),
                }
            )
            for cue in script.soundscapes
        ]

        speaker_candidates: list[_QuoteReviewCandidate] = []
        for old_index, source_evidence in evidence_by_old_index.items():
            if source_evidence.explicit_character_id:
                continue
            new_index = old_to_new[old_index]
            segment = reindexed[new_index]
            speaker_candidates.append(
                _QuoteReviewCandidate(
                    candidate_id=source_evidence.candidate_id,
                    segment_index=new_index,
                    paragraph_index=source_evidence.paragraph_index,
                    quote_index=source_evidence.quote_index,
                    text=source_evidence.text,
                    context=source_evidence.context,
                    current_character_id=segment.character_id,
                    paragraph_before=source_evidence.paragraph_before,
                    paragraph_text=source_evidence.paragraph_text,
                    paragraph_after=source_evidence.paragraph_after,
                    role_status=source_evidence.role_status,
                    role_reason=source_evidence.role_reason,
                    attribution_window=source_evidence.attribution_window,
                )
            )

        metadata = {
            **script.metadata,
            "source_reconciliation": {
                "status": "passed",
                "unanchored_spoken_segments_reclassified": len(converted_indices),
                "embedded_quotes_extracted_for_review": embedded_quote_extractions,
                "explicit_speaker_corrections": explicit_corrections,
                "ambiguous_quote_candidates": len(speaker_candidates),
            },
        }
        reconciled = script.model_copy(
            update={
                "segments": reindexed,
                "bgm_suggestions": updated_bgm,
                "sfx_cues": updated_sfx,
                "soundscapes": updated_soundscapes,
                "metadata": metadata,
            }
        )
        self._validate_script_text_fidelity(reconciled, source_text)
        return reconciled, speaker_candidates

    @staticmethod
    def _merge_role_reclassified_narration(
        script: DubbingScript,
        converted_indices: set[int],
    ) -> tuple[DubbingScript, dict[int, int]]:
        """Fold semantic-role corrections into adjacent narrator spans."""
        if not converted_indices:
            return script, {index: index for index in range(len(script.segments))}

        merged: list[DubbingSegment] = []
        grouped_indices: list[list[int]] = []
        group_contains_conversion: list[bool] = []
        for old_index, segment in enumerate(script.segments):
            is_converted = old_index in converted_indices
            can_merge = bool(
                merged
                and segment.segment_type == SegmentType.NARRATION
                and merged[-1].segment_type == SegmentType.NARRATION
                and segment.source_paragraph == merged[-1].source_paragraph
                and (is_converted or group_contains_conversion[-1])
            )
            if can_merge:
                previous = merged[-1]
                merged[-1] = previous.model_copy(
                    update={
                        "text": previous.text + segment.text,
                        "spoken_text": "",
                        "transition": previous.transition or segment.transition,
                    }
                )
                grouped_indices[-1].append(old_index)
                group_contains_conversion[-1] = True
            else:
                merged.append(segment)
                grouped_indices.append([old_index])
                group_contains_conversion.append(is_converted)

        old_to_new: dict[int, int] = {}
        reindexed: list[DubbingSegment] = []
        for new_index, (segment, old_indices) in enumerate(
            zip(merged, grouped_indices, strict=True)
        ):
            reindexed.append(segment.model_copy(update={"segment_index": new_index}))
            for old_index in old_indices:
                old_to_new[old_index] = new_index

        def remap(index: int | None) -> int | None:
            return None if index is None else old_to_new.get(index, index)

        return (
            script.model_copy(
                update={
                    "segments": reindexed,
                    "bgm_suggestions": [
                        cue.model_copy(
                            update={
                                "start_segment_index": remap(cue.start_segment_index),
                                "end_segment_index": remap(cue.end_segment_index),
                            }
                        )
                        for cue in script.bgm_suggestions
                    ],
                    "sfx_cues": [
                        cue.model_copy(
                            update={"trigger_segment_index": remap(cue.trigger_segment_index)}
                        )
                        for cue in script.sfx_cues
                    ],
                    "soundscapes": [
                        cue.model_copy(
                            update={
                                "start_segment_index": remap(cue.start_segment_index),
                                "end_segment_index": remap(cue.end_segment_index),
                            }
                        )
                        for cue in script.soundscapes
                    ],
                }
            ),
            old_to_new,
        )

    @staticmethod
    def _merge_narration_continuations(
        script: DubbingScript,
    ) -> tuple[DubbingScript, dict[int, int], int]:
        """Repair narrator fragments split inside an unfinished source clause.

        Script generation may split for breathing and subtitle cadence, but a
        lead-in ending in a colon or connective punctuation must stay attached
        to the narrator text that completes it.  This only merges same-source-
        paragraph narration and preserves genuine sentence boundaries.

        Additionally, when the current segment's text consists entirely of
        continuation punctuation (e.g. "——"), it is merged into the previous
        segment regardless of what the previous segment ends with.  This
        prevents LLM mis-segmentation from producing un-speakable narration
        fragments that pass through all downstream validation.
        """
        continuation_marks = ("：", ":", "，", ",", "；", ";", "、", "——")
        merged: list[DubbingSegment] = []
        grouped_indices: list[list[int]] = []
        merged_count = 0
        dropped_count = 0
        for old_index, segment in enumerate(script.segments):
            previous = merged[-1] if merged else None
            # Check if current segment is pure punctuation (no speakable chars).
            _current_is_pure_punct = bool(
                segment.segment_type == SegmentType.NARRATION
                and segment.text.strip()
                and not normalize_speakable_text(segment.text)
            )
            should_merge = bool(
                previous is not None
                and previous.segment_type == SegmentType.NARRATION
                and segment.segment_type == SegmentType.NARRATION
                and previous.source_paragraph == segment.source_paragraph
                and previous.transition is None
                and segment.transition is None
                and (
                    previous.text.rstrip().endswith(continuation_marks)
                    or _current_is_pure_punct
                )
            )
            if should_merge and previous is not None:
                text = previous.text + segment.text
                merged[-1] = previous.model_copy(
                    update={
                        "text": text,
                        "spoken_text": "",
                        "language_runs": [LanguageRun(language=previous.language_code, text=text)],
                    }
                )
                grouped_indices[-1].append(old_index)
                merged_count += 1
            elif _current_is_pure_punct:
                # Scene break / separator from a different paragraph: drop it
                # entirely.  These segments (e.g. "---", "——", "***") carry no
                # speakable content and must never reach TTS synthesis.
                dropped_count += 1
            else:
                merged.append(segment)
                grouped_indices.append([old_index])

        if not merged_count and not dropped_count:
            return script, {index: index for index in range(len(script.segments))}, 0

        old_to_new: dict[int, int] = {}
        reindexed: list[DubbingSegment] = []
        for new_index, (segment, old_indices) in enumerate(
            zip(merged, grouped_indices, strict=True)
        ):
            reindexed.append(segment.model_copy(update={"segment_index": new_index}))
            for old_index in old_indices:
                old_to_new[old_index] = new_index

        def remap(index: int | None) -> int | None:
            return None if index is None else old_to_new.get(index, index)

        return (
            script.model_copy(
                update={
                    "segments": reindexed,
                    "bgm_suggestions": [
                        cue.model_copy(
                            update={
                                "start_segment_index": remap(cue.start_segment_index),
                                "end_segment_index": remap(cue.end_segment_index),
                            }
                        )
                        for cue in script.bgm_suggestions
                    ],
                    "sfx_cues": [
                        cue.model_copy(
                            update={"trigger_segment_index": remap(cue.trigger_segment_index)}
                        )
                        for cue in script.sfx_cues
                    ],
                    "soundscapes": [
                        cue.model_copy(
                            update={
                                "start_segment_index": remap(cue.start_segment_index),
                                "end_segment_index": remap(cue.end_segment_index),
                            }
                        )
                        for cue in script.soundscapes
                    ],
                }
            ),
            old_to_new,
            merged_count + dropped_count,
        )

    @staticmethod
    def _fold_single_char_dialogue(
        script: DubbingScript,
    ) -> tuple[DubbingScript, int]:
        """Fold single-character / punctuation-only dialogue into adjacent segments.

        LLMs occasionally emit one-character dialogue segments ("嗯", "啊", "。",
        "…") that pass source-evidence validation but produce unnatural TTS
        output: the synthesizer renders a single syllable with full emotional
        weight, creating an abrupt blip in the audio stream.

        Folding rules:
        - A spoken segment (dialogue/inner_thought) with ≤ 1 speakable character
          is merged into the nearest adjacent segment in the same source paragraph.
        - Preference: merge into the *previous* segment if it exists in the same
          paragraph; otherwise merge into the *next* segment.
        - The merged text is concatenated; emotion is inherited from the target
          segment (the single char doesn't carry independent emotional weight).
        - BGM/SFX/Soundscape anchors are remapped to the new indices.
        - Segments with explicit artistic intent (paralinguistic_tags, transition)
          are preserved even if short.

        Returns (updated_script, fold_count).
        """
        import re as _re

        spoken_types = {SegmentType.DIALOGUE, SegmentType.INNER_THOUGHT}

        def _speakable_len(text: str) -> int:
            return len(_re.sub(r"[\s\W_]+", "", str(text), flags=_re.UNICODE))

        # Identify fold candidates: spoken segments with ≤1 speakable char
        # that lack explicit artistic intent markers.
        fold_indices: set[int] = set()
        for idx, segment in enumerate(script.segments):
            if segment.segment_type not in spoken_types:
                continue
            if _speakable_len(segment.text) > 1:
                continue
            # Preserve segments with explicit artistic intent.
            if segment.paralinguistic_tags or segment.transition is not None:
                continue
            # Preserve segments with an attributed speaker — a single-char
            # command like "坐。" assigned through adjudication is intentional.
            if segment.character_id:
                continue
            fold_indices.add(idx)

        if not fold_indices:
            return script, 0

        # Build merged segment list.
        merged: list[DubbingSegment] = []
        grouped_indices: list[list[int]] = []
        fold_count = 0

        for idx, segment in enumerate(script.segments):
            if idx not in fold_indices:
                merged.append(segment)
                grouped_indices.append([idx])
                continue

            fold_count += 1
            # Try to merge into the previous segment (same paragraph).
            if merged and merged[-1].source_paragraph == segment.source_paragraph:
                prev = merged[-1]
                combined_text = prev.text + segment.text
                merged[-1] = prev.model_copy(
                    update={
                        "text": combined_text,
                        "spoken_text": "",
                        # Inherit emotion from the target (longer) segment.
                        "segment_type": prev.segment_type,
                        "character_id": prev.character_id,
                        "character_name": prev.character_name,
                    }
                )
                grouped_indices[-1].append(idx)
            else:
                # No valid previous target; keep as placeholder and merge into
                # the next segment in a second pass.  For now, append a marker.
                merged.append(segment)
                grouped_indices.append([idx])

        # Second pass: fold remaining single-char segments forward into next.
        final: list[DubbingSegment] = []
        final_groups: list[list[int]] = []
        for i, (seg, group) in enumerate(zip(merged, grouped_indices, strict=True)):
            if (
                seg.segment_type in spoken_types
                and _speakable_len(seg.text) <= 1
                and not seg.paralinguistic_tags
                and seg.transition is None
                and not seg.character_id
                and i + 1 < len(merged)
                and merged[i + 1].source_paragraph == seg.source_paragraph
            ):
                # Merge forward into next segment.
                next_seg = merged[i + 1]
                combined_text = seg.text + next_seg.text
                merged[i + 1] = next_seg.model_copy(
                    update={
                        "text": combined_text,
                        "spoken_text": "",
                        "segment_type": next_seg.segment_type,
                        "character_id": next_seg.character_id,
                        "character_name": next_seg.character_name,
                    }
                )
                grouped_indices[i + 1] = group + grouped_indices[i + 1]
                # Skip this segment (it's been absorbed forward).
                continue
            final.append(seg)
            final_groups.append(group)

        if not fold_count:
            return script, 0

        # Reindex and remap anchors.
        old_to_new: dict[int, int] = {}
        reindexed: list[DubbingSegment] = []
        for new_index, (segment, old_indices) in enumerate(
            zip(final, final_groups, strict=True)
        ):
            reindexed.append(segment.model_copy(update={"segment_index": new_index}))
            for old_index in old_indices:
                old_to_new[old_index] = new_index

        def remap(index: int | None) -> int | None:
            return None if index is None else old_to_new.get(index, index)

        updated = script.model_copy(
            update={
                "segments": reindexed,
                "bgm_suggestions": [
                    cue.model_copy(
                        update={
                            "start_segment_index": remap(cue.start_segment_index),
                            "end_segment_index": remap(cue.end_segment_index),
                        }
                    )
                    for cue in script.bgm_suggestions
                ],
                "sfx_cues": [
                    cue.model_copy(
                        update={"trigger_segment_index": remap(cue.trigger_segment_index)}
                    )
                    for cue in script.sfx_cues
                ],
                "soundscapes": [
                    cue.model_copy(
                        update={
                            "start_segment_index": remap(cue.start_segment_index),
                            "end_segment_index": remap(cue.end_segment_index),
                        }
                    )
                    for cue in script.soundscapes
                ],
            }
        )
        return updated, fold_count

    @staticmethod
    def _prior_assignment_is_confirmable(
        candidate_id: str,
        current_character_id: str,
        allowed_character_ids: set[str],
        raw_decisions: list[dict[str, Any]],
    ) -> bool:
        """Return True when the generator's speaker prior may be kept.

        “不确定 ≠ 错误”：裁决器掌握的上下文（±3 段）少于生成器（全章），
        当它无法验证先验归属（speaker_decision is None）但也拿不出高置信
        反证时，生成器的全章归属是更强的证据，应保留而不是降级为人工复核。

        安全约束：
        - 先验必须是合法演员（current_character_id ∈ allowed_character_ids）；
        - raw_decisions 中不存在针对该候选的“高置信（≥0.72）改判到另一
          合法角色”的反证。只要裁决器曾高置信地指向别人，无论证据是否
          锚定成功，都视为反证，回退 unresolved 兜底。
        """
        if not current_character_id or current_character_id not in allowed_character_ids:
            return False
        for decision in raw_decisions:
            if str(decision.get("candidate_id", "")).strip() != candidate_id:
                continue
            try:
                confidence = float(decision.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            if confidence < 0.72:
                continue
            rebuttal_id = str(decision.get("character_id", "")).strip()
            if (
                rebuttal_id
                and rebuttal_id in allowed_character_ids
                and rebuttal_id != current_character_id
            ):
                return False
        return True

    async def _adjudicate_ambiguous_quote_roles(
        self,
        script: DubbingScript,
        candidates: list[_QuoteReviewCandidate],
        voice_team: VoiceTeamContract,
    ) -> DubbingScript:
        """Adjudicate acoustic role first, then speaker, without rewriting text."""
        if not candidates:
            return script.model_copy(
                update={
                    "metadata": {
                        **script.metadata,
                        "speaker_adjudication": {
                            "status": "not_needed",
                            "candidate_count": 0,
                            "decisions": [],
                            "unresolved_segment_indices": [],
                        },
                    }
                }
            )

        allowed_character_ids = {
            entry.character_id for entry in voice_team.entries if entry.character_id
        }
        candidate_cards = []
        for candidate in candidates:
            seg_idx = candidate.segment_index
            # Dialogue flow: character_id sequence of ±3 dialogue segments around current.
            dialogue_flow: list[str | None] = []
            for offset in range(-3, 4):
                probe_idx = seg_idx + offset
                if 0 <= probe_idx < len(script.segments) and probe_idx != seg_idx:
                    probe_seg = script.segments[probe_idx]
                    if probe_seg.character_id:
                        dialogue_flow.append(probe_seg.character_id)
            # Dominant speaker in same paragraph.
            para_speakers: dict[str, int] = {}
            for s in script.segments:
                if s.source_paragraph == candidate.paragraph_index and s.character_id:
                    para_speakers[s.character_id] = para_speakers.get(s.character_id, 0) + 1
            dominant_speaker = (
                max(para_speakers, key=para_speakers.get)  # type: ignore[arg-type]
                if para_speakers
                else None
            )
            candidate_cards.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "segment_index": candidate.segment_index,
                    "source_paragraph": candidate.paragraph_index,
                    "source_quote_index": candidate.quote_index,
                    "text": candidate.text,
                    "context": candidate.context,
                    "paragraph_before": candidate.paragraph_before,
                    "paragraph_text": candidate.paragraph_text,
                    "paragraph_after": candidate.paragraph_after,
                    "attribution_window": candidate.attribution_window,
                    "current_character_id": candidate.current_character_id,
                    "current_segment_role": script.segments[candidate.segment_index].segment_type.value,
                    "structural_role_status": candidate.role_status,
                    "structural_role_reason": candidate.role_reason,
                    "dialogue_flow": dialogue_flow,
                    "dominant_speaker_in_paragraph": dominant_speaker,
                    "previous_segment": (
                        {
                            "segment_type": script.segments[
                                candidate.segment_index - 1
                            ].segment_type.value,
                            "character_id": script.segments[candidate.segment_index - 1].character_id,
                            "text": script.segments[candidate.segment_index - 1].text,
                        }
                        if candidate.segment_index > 0
                        else None
                    ),
                    "next_segment": (
                        {
                            "segment_type": script.segments[
                                candidate.segment_index + 1
                            ].segment_type.value,
                            "character_id": script.segments[candidate.segment_index + 1].character_id,
                            "text": script.segments[candidate.segment_index + 1].text,
                        }
                        if candidate.segment_index + 1 < len(script.segments)
                        else None
                    ),
                }
            )
        cards = {
            "candidates": candidate_cards,
            "voice_team": [
                {
                    "character_id": entry.character_id,
                    "character_name": entry.character_name,
                    "gender": entry.character_gender,
                    "age": entry.character_age,
                    "role": entry.character_role,
                }
                for entry in voice_team.entries
                if entry.character_id
            ],
            "character_evidence": getattr(self, "_speaker_adjudication_character_cards", []),
        }
        accepted_roles: dict[str, dict[str, Any]] = {}
        accepted_speakers: dict[str, dict[str, Any]] = {}
        raw_decisions: list[dict[str, Any]] = []
        status = "passed"
        candidate_map = {candidate.candidate_id: candidate for candidate in candidates}

        def record_decisions(raw_values: object, *, pass_name: str) -> None:
            """Accept only source-anchored decisions from one adjudication pass."""
            decisions = raw_values if isinstance(raw_values, list) else []
            seen_in_pass: set[str] = set()
            for decision in decisions:
                if not isinstance(decision, dict):
                    continue
                candidate_id = str(decision.get("candidate_id", "")).strip()
                segment_role = str(decision.get("segment_role", "")).strip()
                verdict = str(decision.get("verdict", "")).strip()
                character_id = str(decision.get("character_id", "")).strip()
                evidence_text = str(decision.get("evidence", "")).strip()
                try:
                    confidence = float(decision.get("confidence", 0.0))
                except (TypeError, ValueError):
                    confidence = 0.0
                if candidate_id not in candidate_map or candidate_id in seen_in_pass:
                    continue
                seen_in_pass.add(candidate_id)
                source_candidate = candidate_map[candidate_id]
                evidence_is_anchored = bool(
                    evidence_text
                    and self._normalized_source_text(evidence_text)
                    in self._normalized_source_text(source_candidate.context)
                )
                role_is_allowed = segment_role in {
                    SegmentType.DIALOGUE.value,
                    SegmentType.INNER_THOUGHT.value,
                    SegmentType.NARRATION.value,
                    "ambiguous",
                }
                role_respects_structure = not (
                    source_candidate.role_status == "spoken"
                    and segment_role == SegmentType.NARRATION.value
                )
                role_verified = bool(
                    role_is_allowed
                    and role_respects_structure
                    and segment_role != "ambiguous"
                    and confidence >= 0.72
                    and evidence_is_anchored
                )
                speaker_verified = bool(
                    verdict == "resolved"
                    and character_id in allowed_character_ids
                    and confidence >= 0.72
                    and evidence_is_anchored
                )
                decision_record = {
                    "candidate_id": candidate_id,
                    "segment_role": segment_role,
                    "verdict": verdict,
                    "character_id": character_id,
                    "confidence": confidence,
                    "evidence": evidence_text,
                    "rationale": str(decision.get("rationale", "")).strip(),
                    "role_verified": role_verified,
                    "speaker_verified": speaker_verified,
                    "adjudication_pass": pass_name,
                }
                raw_decisions.append(decision_record)
                if role_verified:
                    accepted_roles[candidate_id] = decision_record
                if speaker_verified:
                    accepted_speakers[candidate_id] = decision_record

        self._on_step_event(
            "tts_script_segment_adjudication_start",
            {"candidate_count": len(candidates)},
        )
        try:
            response = await self._call_with_retry(
                TaskType.TTS_ADJUDICATE_SCRIPT_SEGMENTS,
                {"stage_cards": cards},
                max_tokens=self._dynamic_max_tokens(
                    TaskType.TTS_ADJUDICATE_SCRIPT_SEGMENTS,
                    max(4096, len(candidates) * 220),
                    prompt_overhead=3000 + sum(len(item.context) for item in candidates),
                    min_tokens=4096,
                    max_cap=16384,
                ),
                temperature=float(
                    getattr(self._settings, "tts_review_adjudication_temperature", 0.0)
                ),
                required_keys=("decisions", "summary"),
                max_retries=2,
            )
            record_decisions(
                response.get("decisions", []) if isinstance(response, dict) else [],
                pass_name="initial",
            )
            retry_candidates = [
                candidate
                for candidate in candidates
                if candidate.candidate_id not in accepted_roles
                or (
                    str(accepted_roles[candidate.candidate_id]["segment_role"])
                    in {SegmentType.DIALOGUE.value, SegmentType.INNER_THOUGHT.value}
                    and candidate.candidate_id not in accepted_speakers
                )
            ]
            if retry_candidates:
                retry_ids = {candidate.candidate_id for candidate in retry_candidates}
                self._on_step_event(
                    "tts_script_segment_adjudication_repair_start",
                    {"candidate_count": len(retry_candidates)},
                )
                repair_response = await self._call_with_retry(
                    TaskType.TTS_ADJUDICATE_SCRIPT_SEGMENTS,
                    {
                        "stage_cards": {
                            **cards,
                            "candidates": [
                                card
                                for card in candidate_cards
                                if str(card["candidate_id"]) in retry_ids
                            ],
                            "adjudication_pass": "source_repair",
                        }
                    },
                    max_tokens=self._dynamic_max_tokens(
                        TaskType.TTS_ADJUDICATE_SCRIPT_SEGMENTS,
                        max(4096, len(retry_candidates) * 260),
                        prompt_overhead=3500
                        + sum(len(candidate.context) for candidate in retry_candidates),
                        min_tokens=4096,
                        max_cap=16384,
                    ),
                    temperature=float(
                        getattr(self._settings, "tts_review_adjudication_temperature", 0.0)
                    ),
                    required_keys=("decisions", "summary"),
                    max_retries=2,
                )
                record_decisions(
                    repair_response.get("decisions", [])
                    if isinstance(repair_response, dict)
                    else [],
                    pass_name="source_repair",
                )
        except Exception as exc:
            status = "unavailable"
            _log.warning("Speaker adjudication unavailable: %s", exc)
            self._on_step_event(
                "tts_script_segment_adjudication_unavailable",
                {"reason": str(exc), "candidate_count": len(candidates)},
            )

        segments = list(script.segments)
        unresolved: list[int] = []
        prior_confirmed_segment_indices: list[int] = []
        converted_to_narration: set[int] = set()
        role_fallback_indices: list[int] = []
        for candidate in candidates:
            role_decision = accepted_roles.get(candidate.candidate_id)
            segment = segments[candidate.segment_index]
            if candidate.role_status == "spoken":
                selected_role = (
                    str(role_decision["segment_role"])
                    if role_decision is not None
                    else segment.segment_type.value
                )
            elif role_decision is not None:
                selected_role = str(role_decision["segment_role"])
            else:
                # Preserve the source-aligned role and stop at the review gate.
                # Silently converting an uncertain spoken line to narration
                # produces a convincing but incorrect speaker, which is harder
                # to detect than an explicit unresolved segment.
                selected_role = segment.segment_type.value
                role_fallback_indices.append(candidate.segment_index)

            if selected_role == SegmentType.NARRATION.value:
                if role_decision is not None:
                    segments[candidate.segment_index] = segment.model_copy(
                        update={
                            "segment_type": SegmentType.NARRATION,
                            "character_id": "",
                            "character_name": "",
                            "spoken_text": "",
                        }
                    )
                    converted_to_narration.add(candidate.segment_index)
                else:
                    unresolved.append(candidate.segment_index)
                continue

            target_type = (
                SegmentType.INNER_THOUGHT
                if selected_role == SegmentType.INNER_THOUGHT.value
                else SegmentType.DIALOGUE
            )
            speaker_decision = accepted_speakers.get(candidate.candidate_id)
            if speaker_decision is None:
                if self._prior_assignment_is_confirmable(
                    candidate.candidate_id,
                    candidate.current_character_id,
                    allowed_character_ids,
                    raw_decisions,
                ):
                    # 先验保留：裁决器未验证但也未反驳生成归属，接受先验并
                    # 记入 prior_confirmed_segment_indices（可审计），不再进 unresolved。
                    prior_id = candidate.current_character_id
                    segments[candidate.segment_index] = refresh_segment_uid(
                        segment.model_copy(
                            update={
                                "segment_type": target_type,
                                "character_id": prior_id,
                                "character_name": getattr(self, "_character_names", {}).get(
                                    prior_id,
                                    segment.character_name or "",
                                ),
                            }
                        )
                    )
                    prior_confirmed_segment_indices.append(candidate.segment_index)
                    continue
                segments[candidate.segment_index] = segment.model_copy(
                    update={"segment_type": target_type}
                )
                unresolved.append(candidate.segment_index)
                continue
            character_id = str(speaker_decision["character_id"])
            # segment_type / character_id are segment_uid inputs: model_copy
            # bypasses validators, so refresh the content-addressed identity
            # to keep reusable-take matching correct downstream.
            segments[candidate.segment_index] = refresh_segment_uid(
                segments[candidate.segment_index].model_copy(
                    update={
                        "segment_type": target_type,
                        "character_id": character_id,
                        "character_name": getattr(self, "_character_names", {}).get(
                            character_id,
                            "",
                        ),
                    }
                )
            )

        role_applied_script, old_to_new = self._merge_role_reclassified_narration(
            script.model_copy(update={"segments": segments}),
            converted_to_narration,
        )
        unresolved = sorted({old_to_new.get(index, index) for index in unresolved})
        prior_confirmed_segment_indices = sorted(
            {old_to_new.get(index, index) for index in prior_confirmed_segment_indices}
        )
        role_fallback_indices = sorted(
            {old_to_new.get(index, index) for index in role_fallback_indices}
        )
        role_applied_script, continuation_old_to_new, continuation_merge_count = (
            self._merge_narration_continuations(role_applied_script)
        )
        unresolved = sorted({continuation_old_to_new.get(index, index) for index in unresolved})
        prior_confirmed_segment_indices = sorted(
            {
                continuation_old_to_new.get(index, index)
                for index in prior_confirmed_segment_indices
            }
        )
        role_fallback_indices = sorted(
            {continuation_old_to_new.get(index, index) for index in role_fallback_indices}
        )
        if status == "passed" and (unresolved or role_fallback_indices):
            status = "needs_review"
        metadata = {
            **role_applied_script.metadata,
            "speaker_adjudication": {
                "status": status,
                "candidate_count": len(candidates),
                "decisions": raw_decisions,
                "unresolved_segment_indices": unresolved,
                "prior_confirmed_segment_indices": prior_confirmed_segment_indices,
                "role_fallback_segment_indices": role_fallback_indices,
                "narrative_reclassification_count": len(converted_to_narration),
                "narration_continuation_merge_count": continuation_merge_count,
            },
        }
        reconciliation = metadata.get("source_reconciliation")
        if isinstance(reconciliation, dict):
            metadata["source_reconciliation"] = {
                **reconciliation,
                "semantic_quotes_reclassified": len(converted_to_narration),
                "narration_continuations_merged": continuation_merge_count,
            }
        self._on_step_event(
            "tts_script_segment_adjudication_complete",
            {
                "candidate_count": len(candidates),
                "accepted_role_count": len(accepted_roles),
                "accepted_speaker_count": len(accepted_speakers),
                "prior_confirmed_count": len(prior_confirmed_segment_indices),
                "unresolved_count": len(unresolved),
                "narrative_reclassification_count": len(converted_to_narration),
                "narration_continuation_merge_count": continuation_merge_count,
                "status": status,
            },
        )
        return role_applied_script.model_copy(update={"metadata": metadata})

    def _validate_script_source_fidelity(
        self,
        script: DubbingScript,
        source_text: str,
        character_map: dict[str, str],
    ) -> None:
        """Validate source coverage, quote role, and explicit attribution."""
        self._validate_script_text_fidelity(script, source_text)
        expected = self._source_dialogue_evidence(source_text, character_map)
        by_key: dict[tuple[int, str], list[_SourceQuoteEvidence]] = {}
        for item in expected:
            by_key.setdefault(
                (item.paragraph_index, item.normalized_text),
                [],
            ).append(item)

        adjudication = script.metadata.get("speaker_adjudication")
        approved_roles: dict[str, str] = {}
        unresolved_indices: set[int] = set()
        if isinstance(adjudication, dict):
            for value in adjudication.get("unresolved_segment_indices") or []:
                try:
                    unresolved_indices.add(int(value))
                except (TypeError, ValueError):
                    continue
            for decision in adjudication.get("decisions") or []:
                if not isinstance(decision, dict):
                    continue
                try:
                    confidence = float(decision.get("confidence", 0.0))
                except (TypeError, ValueError):
                    continue
                role = str(decision.get("segment_role", ""))
                if (
                    decision.get("role_verified") is True
                    and confidence >= 0.72
                    and role
                    in {
                        SegmentType.DIALOGUE.value,
                        SegmentType.INNER_THOUGHT.value,
                        SegmentType.NARRATION.value,
                    }
                ):
                    approved_roles[str(decision.get("candidate_id", ""))] = role

        for segment in script.segments:
            if segment.segment_type not in {SegmentType.DIALOGUE, SegmentType.INNER_THOUGHT}:
                continue
            key = (segment.source_paragraph, self._normalized_source_text(segment.text))
            candidates = by_key.get(key, [])
            if not candidates:
                raise ValueError(
                    "dubbing script misclassified narrative quotation as direct speech"
                )
            source_evidence = candidates.pop(0)
            approved_role = approved_roles.get(source_evidence.candidate_id, "")
            if (
                source_evidence.role_status != "spoken"
                and approved_role
                not in {
                    SegmentType.DIALOGUE.value,
                    SegmentType.INNER_THOUGHT.value,
                }
                and segment.segment_index not in unresolved_indices
            ):
                raise ValueError(
                    "dubbing script misclassified narrative quotation as direct speech"
                )
            if (
                source_evidence.explicit_character_id
                and segment.character_id != source_evidence.explicit_character_id
            ):
                raise ValueError("dubbing script contradicts explicit speaker attribution")
        if any(
            item.role_status == "spoken"
            or approved_roles.get(item.candidate_id)
            in {SegmentType.DIALOGUE.value, SegmentType.INNER_THOUGHT.value}
            for remaining in by_key.values()
            for item in remaining
        ):
            raise ValueError("dubbing script omitted or misclassified direct speech")

    def _infer_emotion(
        self,
        text: str,
        start: int,
        end: int,
        *,
        scene_id: str | None = None,
        character_name: str | None = None,
        segment_position: int = 0,
        total_segments: int = 1,
        is_narration: bool = True,
    ) -> tuple[EmotionTag, float]:
        """Infer emotion and intensity from surrounding context.

        Returns:
            A tuple of (emotion, intensity) where intensity is 0.0-1.0.

        Priority:
        1. Character emotion trajectory (character_emotion_trajectories) if available
        2. Scene-level TTS metadata (scene_emotion_map) if available
        3. Context-aware keyword fallback with narrative-position calibration
        """
        # Priority 1: Character emotion trajectory (most specific)
        if character_name and scene_id and hasattr(self, "_ctx") and self._ctx.tts_metadata:
            trajectories = self._ctx.tts_metadata.get("character_emotion_trajectories", {})
            char_traj = trajectories.get(character_name, [])
            for entry in char_traj:
                if entry.get("scene_id") == scene_id:
                    emotion_str = entry.get("emotion", "")
                    if emotion_str:
                        try:
                            return EmotionTag(emotion_str), float(entry.get("intensity", 0.5))
                        except ValueError:
                            pass

        # Priority 2: Scene-level TTS metadata
        # (caller should pass scene_id when scene_intents are available)
        # This is checked via the instance-level _tts_metadata set in _execute
        if scene_id and hasattr(self, "_ctx") and self._ctx.tts_metadata:
            for annotation in self._ctx.tts_metadata.get("scene_emotion_map", []):
                if annotation.get("scene_id") == scene_id:
                    emotion_str = annotation.get("dominant_emotion", "neutral")
                    intensity = float(annotation.get("emotion_intensity", 0.5))
                    try:
                        return EmotionTag(emotion_str), intensity
                    except ValueError:
                        pass

        # Priority 3: Context-aware keyword fallback with narrative-position calibration
        from novel_forge.tts.emotion_inference import infer_emotion_with_context

        # Wider context window (±80 chars) captures paragraph-level emotion signals.
        context_start = max(0, start - 80)
        context_end = min(len(text), end + 80)
        context = text[context_start:context_end]

        # Supplement with scene emotional_beat when available.
        if scene_id:
            for intent in getattr(self, "_scene_intents", None) or []:
                if str(intent.get("scene_id", "")) == scene_id:
                    scene_emotion_hint = str(intent.get("emotional_beat", "") or "")
                    if scene_emotion_hint:
                        context = f"{context} {scene_emotion_hint}"
                    break

        chapter_number = getattr(self, "_chapter_number", 0)
        return infer_emotion_with_context(
            context,
            chapter_number=chapter_number,
            segment_position=segment_position,
            total_segments=total_segments,
            is_narration=is_narration,
        )

    def _scene_context(self, scene_id: str | None) -> str:
        """Return a compact, human-readable scene context for a segment."""
        if not scene_id:
            return ""
        for intent in getattr(self, "_scene_intents", None) or []:
            if str(intent.get("scene_id", "")) != scene_id:
                continue
            parts = [
                str(intent.get("location", "") or "").strip(),
                str(intent.get("time_marker", "") or "").strip(),
                str(intent.get("emotional_beat", "") or "").strip(),
            ]
            return "｜".join(part for part in parts if part)
        return ""

    def _estimate_duration(self, segment: DubbingSegment) -> int:
        """Estimate segment duration in milliseconds."""
        text_len = len(segment.text)
        # Approximate: 200ms per Chinese character for normal speech
        base_duration = text_len * 200

        # Apply speed modifier
        speed = segment.speed_override or 1.0
        return int(base_duration / speed)

    def _match_paragraph_to_scene(self, paragraph: str) -> str | None:
        """Try to match a paragraph to a scene_id via location keyword overlap.

        Returns the best-matching scene_id, or None if no scene_intents are
        available or no location keyword appears in the paragraph.
        """
        scene_intents = getattr(self, "_scene_intents", None)
        if not scene_intents:
            return None
        best_id: str | None = None
        best_hits = 0
        for intent in scene_intents:
            candidates = [
                str(intent.get("location", "") or "").strip(),
                str(intent.get("summary", "") or "").strip(),
                str(intent.get("emotional_beat", "") or "").strip(),
            ]
            candidates = [candidate for candidate in candidates if candidate]
            if not candidates:
                continue
            hits = 0
            for candidate in candidates:
                # Prefer meaningful chunks over individual characters.  The
                # previous implementation iterated characters and therefore
                # never matched Chinese locations (all have length 1).
                chunks = [candidate]
                for separator in ("、", ",", "，", "/", "|"):
                    chunks = [part for chunk in chunks for part in chunk.split(separator)]
                hits += sum(
                    1 for chunk in chunks if len(chunk.strip()) >= 2 and chunk.strip() in paragraph
                )
            if hits > best_hits:
                best_hits = hits
                best_id = intent.get("scene_id")
        return best_id if best_hits > 0 else None

    def _compute_hash(self, content: str | DubbingScript) -> str:
        """Compute hash for change detection."""
        if isinstance(content, DubbingScript):
            return compute_dubbing_script_hash(content)
        return compute_source_text_hash(content)
