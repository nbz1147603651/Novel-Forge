"""Sound-asset generation orchestration between TTS synthesis and final mix."""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from novel_forge.core.config import Settings
from novel_forge.persistence.filesystem import atomic_write_bytes
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.assets.sound_library import (
    load_sound_library,
    resolve_sound_cues,
    save_sound_library,
)
from novel_forge.tts.platform.schemas import AudioExecutionPlan, AudioExecutionStage
from novel_forge.tts.runtime.storage_limits import ensure_project_audio_write
from novel_forge.tts.schemas import (
    ChapterSoundResolutionReport,
    DubbingScript,
    SoundAsset,
)
from novel_forge.tts.sound_generation.models.catalog import SOUND_MODEL_CATALOG
from novel_forge.tts.sound_generation.postprocess import postprocess_generated_audio
from novel_forge.tts.sound_generation.providers.base import SoundGenerationProvider
from novel_forge.tts.sound_generation.providers.registry import SoundGenerationRegistry
from novel_forge.tts.sound_generation.quality_gate import evaluate_generated_audio
from novel_forge.tts.sound_generation.schemas import (
    GeneratedSoundAsset,
    SoundGenerationAttempt,
    SoundGenerationKind,
    SoundGenerationRequest,
    SoundGenerationSummary,
)
from novel_forge.tts.sound_generation.style_profile import (
    derive_deterministic_seed,
    load_style_profile,
)

_SOUND_STAGE_BY_KIND = {
    SoundGenerationKind.SFX: AudioExecutionStage.SFX,
    SoundGenerationKind.BGM: AudioExecutionStage.MUSIC,
    SoundGenerationKind.SOUNDSCAPE: AudioExecutionStage.SOUNDSCAPE,
}


@dataclass(frozen=True)
class SoundGenerationOutcome:
    """Final resolution report plus durable generation provenance."""

    resolution: ChapterSoundResolutionReport
    summary: SoundGenerationSummary


class SoundGenerationService:
    """Resolve existing cues first, then safely generate only missing assets.

    The service never lets a failed generation fail the narration run.  It also
    persists generated candidates to the normal asset library, which gives
    subsequent chapters deterministic reuse rather than a new random sound for
    every retry.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        layout: ProjectLayout,
        registry: SoundGenerationRegistry | None = None,
        execution_plan: AudioExecutionPlan | None = None,
    ) -> None:
        self._layout = layout
        # Weights are application resources and can be reused by every novel.
        # The project still owns only generated assets, cue resolutions and
        # reproducibility metadata; no model download is written under tts/.
        self._settings = settings
        self._registry = registry or SoundGenerationRegistry(self._settings)
        self._execution_plan = execution_plan

    def required_execution_stages(
        self,
        script: DubbingScript,
    ) -> set[AudioExecutionStage]:
        """Return only generation stages that will actually call a provider.

        Existing approved assets are resolved before preflight, and an existing
        generated candidate awaiting review is a cache hit rather than another
        model invocation. Optional Stable Audio/small-* runtimes therefore do
        not block a MiniMax-led production run that can reuse local assets.
        """

        if not (
            self._settings.sound_generation_enabled
            and self._settings.sound_generation_auto_generate
        ):
            return set()
        library = load_sound_library(self._layout)
        report = resolve_sound_cues(layout=self._layout, script=script, library=library)
        required: set[AudioExecutionStage] = set()
        for request in self._build_requests(script, report):
            stage = _SOUND_STAGE_BY_KIND[request.kind]
            try:
                prepared = self._prepare_request(request)
            except ValueError:
                # Keep the stage active so preflight reports the missing or
                # invalid frozen route instead of silently skipping the cue.
                required.add(stage)
                continue
            if self._find_cached_asset(library.assets, prepared) is None:
                required.add(stage)
        return required

    async def resolve_or_generate(
        self,
        script: DubbingScript,
        *,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> SoundGenerationOutcome:
        """Resolve cues and optionally generate assets absent from the library."""
        library = load_sound_library(self._layout)
        initial_report = resolve_sound_cues(layout=self._layout, script=script, library=library)
        summary = SoundGenerationSummary(
            chapter_number=script.chapter_number,
            enabled=self._settings.sound_generation_enabled,
            auto_generate=self._settings.sound_generation_auto_generate,
            auto_approve=self._settings.sound_generation_auto_approve,
        )
        if not (
            self._settings.sound_generation_enabled
            and self._settings.sound_generation_auto_generate
            and initial_report.unresolved_count
        ):
            return SoundGenerationOutcome(resolution=initial_report, summary=summary)

        requests = self._build_requests(script, initial_report)
        self._emit_progress(
            on_progress,
            "tts_sound_generation_start",
            {
                "chapter": script.chapter_number,
                "missing": len(requests),
                "auto_approve": self._settings.sound_generation_auto_approve,
            },
        )
        library_changed = False
        failed_cue_keys: set[tuple[str, int]] = set()

        # ── Concurrent cue generation (P1-4) ─────────────────────────────────
        # Generation is network-IO dominated, so independent cues run in
        # parallel up to the configured cap.  Library mutation stays on the
        # caller side after every task finishes, keeping persistence race-free
        # while each cue still fails independently.
        max_concurrent = max(
            1,
            min(
                len(requests),
                int(getattr(self._settings, "tts_sound_generation_max_concurrent", 2) or 2),
            ),
        )
        _semaphore = asyncio.Semaphore(max_concurrent)

        @dataclass
        class _CueOutcome:
            ordinal: int
            attempt: SoundGenerationAttempt | None = None
            asset: SoundAsset | None = None
            promote_asset_id: str | None = None
            failed_key: tuple[str, int] | None = None

        async def _generate_cue(
            ordinal: int,
            request: SoundGenerationRequest,
        ) -> _CueOutcome:
            """Generate one cue; returns a result for the caller to apply."""
            async with _semaphore:
                prepared = request
                try:
                    prepared = self._prepare_request(request)
                    existing = self._find_cached_asset(library.assets, prepared)
                    if existing is not None:
                        promote = bool(
                            self._settings.sound_generation_auto_approve
                            and existing.approval_status == "pending"
                        )
                        effective_status = "approved" if promote else existing.approval_status
                        status = "cached" if effective_status == "approved" else "pending_review"
                        attempt = SoundGenerationAttempt(
                            request=prepared,
                            status=status,
                            asset_id=existing.asset_id,
                            provider=existing.generation_provider,
                            model_id=existing.generation_model,
                            route_plugin_id=existing.generation_route_plugin_id,
                            route_plugin_version=existing.generation_route_plugin_version,
                            route_endpoint=existing.generation_route_endpoint,
                            request_hash=existing.generation_request_hash,
                            output_hash=existing.generation_output_hash,
                        )
                        return _CueOutcome(
                            ordinal,
                            attempt=attempt,
                            promote_asset_id=existing.asset_id if promote else None,
                        )
                    provider = self._provider_for(prepared)
                    self._emit_progress(
                        on_progress,
                        "tts_sound_generation_cue",
                        {
                            "chapter": script.chapter_number,
                            "cue": prepared.cue_label,
                            "kind": prepared.kind.value,
                            "provider": prepared.provider,
                            "model": prepared.model_id,
                            "current": ordinal,
                            "total": len(requests),
                        },
                    )
                    # Generate with quality gate and multi-seed retry.
                    max_attempts = 1 + self._settings.sound_generation_max_retries
                    generated: GeneratedSoundAsset | None = None
                    quality_passed = False
                    started_at = time.perf_counter()
                    for attempt_idx in range(max_attempts):
                        attempt_request = prepared
                        if attempt_idx > 0:
                            # Derive a different seed for retry.
                            base_seed = prepared.seed if prepared.seed is not None else 0
                            retry_seed = (base_seed + attempt_idx) % (2**31)
                            attempt_request = prepared.model_copy(update={"seed": retry_seed})
                        candidate = await provider.generate(attempt_request)
                        report = await evaluate_generated_audio(
                            candidate.audio_data, attempt_request, self._settings
                        )
                        if report.passed:
                            generated = candidate
                            quality_passed = True
                            break
                        # Keep the last candidate as fallback.
                        generated = candidate
                    latency_ms = (time.perf_counter() - started_at) * 1000
                    if generated is None:
                        raise RuntimeError("Generation produced no candidate")
                    # If quality gate never passed, force pending_review status.
                    override_approval: Literal["approved", "pending", "rejected"] | None = None
                    if not quality_passed:
                        override_approval = "pending"
                    asset = await self._persist_generated_asset(
                        prepared, generated, approval_status=override_approval
                    )
                    status = (
                        "generated" if asset.approval_status == "approved" else "pending_review"
                    )
                    attempt = SoundGenerationAttempt(
                        request=prepared,
                        status=status,
                        asset_id=asset.asset_id,
                        provider=generated.provider,
                        model_id=generated.model_id,
                        route_plugin_id=str(prepared.metadata.get("route_plugin_id") or ""),
                        route_plugin_version=str(
                            prepared.metadata.get("route_plugin_version") or ""
                        ),
                        route_endpoint=str(prepared.metadata.get("route_endpoint") or ""),
                        request_hash=prepared.fingerprint,
                        output_hash=asset.generation_output_hash,
                        latency_ms=latency_ms,
                        cost_usd=generated.cost_usd,
                    )
                    return _CueOutcome(ordinal, attempt=attempt, asset=asset)
                except Exception as exc:
                    # Remember which cue failed so the final resolution can
                    # degrade it to an approved same-kind library asset instead
                    # of leaving the chapter blocked on unresolved_sound_cues.
                    failed_key = (prepared.kind.value, prepared.cue_index)
                    attempt = SoundGenerationAttempt(
                        request=prepared,
                        status="failed",
                        provider=prepared.provider,
                        model_id=prepared.model_id,
                        route_plugin_id=str(prepared.metadata.get("route_plugin_id") or ""),
                        route_plugin_version=str(
                            prepared.metadata.get("route_plugin_version") or ""
                        ),
                        route_endpoint=str(prepared.metadata.get("route_endpoint") or ""),
                        request_hash=prepared.fingerprint,
                        error_message=str(exc)[:500],
                    )
                    return _CueOutcome(ordinal, attempt=attempt, failed_key=failed_key)

        outcomes = await asyncio.gather(
            *(_generate_cue(ordinal, request) for ordinal, request in enumerate(requests, start=1))
        )
        for outcome in outcomes:
            if outcome.attempt is not None:
                summary.attempts.append(outcome.attempt)
            if outcome.failed_key is not None:
                failed_cue_keys.add(outcome.failed_key)
            if outcome.promote_asset_id is not None:
                library.assets = [
                    (
                        item.model_copy(update={"approval_status": "approved"})
                        if item.asset_id == outcome.promote_asset_id
                        else item
                    )
                    for item in library.assets
                ]
                library_changed = True
            if outcome.asset is not None:
                library.assets = [
                    item for item in library.assets if item.asset_id != outcome.asset.asset_id
                ]
                library.assets.append(outcome.asset)
                library_changed = True

        if library_changed:
            save_sound_library(self._layout, library)
        final_report = resolve_sound_cues(
            layout=self._layout,
            script=script,
            library=library,
            fallback_cue_keys=failed_cue_keys or None,
        )
        self._emit_progress(
            on_progress,
            "tts_sound_generation_complete",
            {
                "chapter": script.chapter_number,
                "generated": summary.generated_count,
                "cached": summary.cached_count,
                "pending_review": summary.pending_review_count,
                "failed": summary.failed_count,
                "resolved": final_report.matched_count,
                "unresolved": final_report.unresolved_count,
            },
        )
        return SoundGenerationOutcome(resolution=final_report, summary=summary)

    async def generate_project_palette(
        self,
        story_context: dict[str, Any],
        *,
        music_variants: int = 3,
        ambience_variants: int = 2,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> list[SoundAsset]:
        """Generate a reusable, review-first sound palette for one novel.

        This is deliberately separate from chapter cue resolution.  The
        palette is generated from stable work-level context (premise, genre,
        tone, world and audio aesthetic), saved as pending candidates, and
        only becomes eligible for a chapter mix after the author auditions
        and approves it in Voice Studio.
        """

        requests = self._build_project_palette_requests(
            story_context,
            music_variants=max(0, min(music_variants, 6)),
            ambience_variants=max(0, min(ambience_variants, 6)),
        )
        if not requests:
            return []

        library = load_sound_library(self._layout)
        created: list[SoundAsset] = []
        failures: list[str] = []
        self._emit_progress(
            on_progress,
            "tts_sound_palette_start",
            {"total": len(requests), "music": music_variants, "ambience": ambience_variants},
        )
        for ordinal, request in enumerate(requests, start=1):
            try:
                prepared = self._prepare_request(request)
                existing = self._find_cached_asset(library.assets, prepared)
                if existing is not None:
                    created.append(existing)
                    self._emit_progress(
                        on_progress,
                        "tts_sound_palette_candidate",
                        {
                            "current": ordinal,
                            "total": len(requests),
                            "label": prepared.cue_label,
                            "kind": prepared.kind.value,
                            "cached": True,
                        },
                    )
                    continue
                provider = self._provider_for(prepared)
                self._emit_progress(
                    on_progress,
                    "tts_sound_palette_candidate",
                    {
                        "current": ordinal,
                        "total": len(requests),
                        "label": prepared.cue_label,
                        "kind": prepared.kind.value,
                    },
                )
                generated = await provider.generate(prepared)
                asset = await self._persist_generated_asset(
                    prepared,
                    generated,
                    approval_status="pending",
                )
                library.assets = [
                    item for item in library.assets if item.asset_id != asset.asset_id
                ]
                library.assets.append(asset)
                created.append(asset)
            except Exception as exc:
                failures.append(f"{request.cue_label}: {exc}")

        if created:
            save_sound_library(self._layout, library)
        self._emit_progress(
            on_progress,
            "tts_sound_palette_complete",
            {
                "generated": len(created),
                "failed": len(failures),
                "errors": failures[:5],
            },
        )
        if not created and failures:
            raise RuntimeError("；".join(failures[:3]))
        return created

    def _prepare_request(self, request: SoundGenerationRequest) -> SoundGenerationRequest:
        """Resolve one cue exclusively through the frozen execution plan when present."""
        if self._execution_plan is None:
            return self._registry.prepare_request(request)
        stage = _SOUND_STAGE_BY_KIND[request.kind]
        assignment = self._execution_plan.assignment_for(stage)
        if assignment is None or assignment.primary is None:
            raise ValueError(f"AudioExecutionPlan 在 {stage.value} 阶段没有符合位置/隐私策略的路由")
        primary = assignment.primary
        provider_id = primary.provider_id
        model_id = primary.model_id
        if not provider_id or not model_id:
            raise ValueError(f"AudioExecutionPlan 的 {stage.value} 路由快照不完整")
        metadata = dict(request.metadata)
        metadata.update(
            {
                "execution_plan_id": self._execution_plan.plan_id,
                "route_plugin_id": primary.plugin_id,
                "route_plugin_version": primary.plugin_version,
                "route_endpoint": primary.endpoint,
            }
        )
        return self._registry.prepare_request(
            request.model_copy(
                update={
                    "provider": provider_id,
                    "model_id": model_id,
                    "metadata": metadata,
                }
            )
        )

    def _provider_for(self, request: SoundGenerationRequest) -> SoundGenerationProvider:
        """Instantiate the adapter from the same frozen target used to prepare the cue."""
        return self._registry.get_provider(
            request.provider,
            endpoint=str(request.metadata.get("route_endpoint") or ""),
        )

    def _build_project_palette_requests(
        self,
        story_context: dict[str, Any],
        *,
        music_variants: int,
        ambience_variants: int,
    ) -> list[SoundGenerationRequest]:
        title = str(story_context.get("title") or "未命名作品").strip()
        genre = str(story_context.get("genre") or "叙事文学").strip()
        premise = str(story_context.get("premise") or story_context.get("theme") or "").strip()
        tone = str(story_context.get("tone") or "克制、沉浸").strip()
        era = str(story_context.get("era") or story_context.get("world") or "").strip()
        audio_aesthetic = str(story_context.get("audio_aesthetic") or "").strip()
        themes_raw = story_context.get("themes") or []
        themes = "、".join(str(item).strip() for item in themes_raw if str(item).strip())
        stable_context = "；".join(
            part
            for part in (
                f"作品《{title}》",
                f"类型：{genre}",
                f"核心梗概：{premise[:500]}" if premise else "",
                f"整体气质：{tone}",
                f"世界/时代：{era[:240]}" if era else "",
                f"主题：{themes[:240]}" if themes else "",
                f"声音美学：{audio_aesthetic[:300]}" if audio_aesthetic else "",
            )
            if part
        )
        music_directions = (
            ("作品主题", "建立可反复出现的主音乐身份，含蓄、辨识度高、能承载全书核心情绪"),
            ("推进与悬念", "用于转折、调查或冲突推进，保持动力但不能抢过对白"),
            ("人物与余韵", "用于亲密、回忆和章节收束，留白充足、情绪真诚不过度煽情"),
            ("世界与远景", "体现时代和世界尺度，适合作为卷章开场或地点建立"),
            ("暗线与不安", "低强度心理张力，适合秘密、疑虑和危险逼近"),
            ("希望与回响", "克制的明亮走向，适合阶段性兑现和人物成长"),
        )
        ambience_directions = (
            ("作品基础环境", "从世界、时代和主要活动空间提炼可循环的低存在感环境底床"),
            ("标志性空间", "提炼最具作品辨识度的地点声景，强调空间远近和真实层次"),
            ("夜间低噪", "适用于夜谈、潜行和内心段落的稳定环境层，无突发前景事件"),
            ("日常公共空间", "适用于常见公共场景的低强度人群与空间底噪，不含可辨对白"),
            ("自然远景", "适用于户外转场的风、植被、水体或远处生物声，平稳可循环"),
            ("室内房间音", "适用于私密对白的细微室内底噪和空间反射，不含机械突发声"),
        )
        requests: list[SoundGenerationRequest] = []
        for index, (label, direction) in enumerate(music_directions[:music_variants]):
            requests.append(
                SoundGenerationRequest(
                    request_id=f"project-palette-bgm-{index}",
                    chapter_number=1,
                    kind=SoundGenerationKind.BGM,
                    cue_index=index,
                    cue_label=f"{title} · {label}",
                    prompt=(
                        f"Create an original instrumental audiobook score. {stable_context}. "
                        f"Direction: {direction}. Subtle under narration, memorable but sparse, "
                        "no vocals, no lyrics, no copyrighted melody, clean loop-friendly ending."
                    ),
                    negative_prompt=(
                        "speech, narration, singing, lyrics, recognizable copyrighted melody, "
                        "watermark, clipping, aggressive mastering"
                    ),
                    duration_ms=min(60_000, self._max_duration_ms),
                    loop=True,
                    output_format=self._output_format,
                    metadata={
                        "asset_tags": [title, genre, tone, label, "作品级", "可复用", "纯音乐"]
                    },
                )
            )
        for index, (label, direction) in enumerate(ambience_directions[:ambience_variants]):
            requests.append(
                SoundGenerationRequest(
                    request_id=f"project-palette-soundscape-{index}",
                    chapter_number=1,
                    kind=SoundGenerationKind.SOUNDSCAPE,
                    cue_index=index,
                    cue_label=f"{title} · {label}",
                    prompt=(
                        f"Create a seamless environmental ambience bed for an audiobook. "
                        f"{stable_context}. Direction: {direction}. Natural stereo depth, stable "
                        "room tone, low intensity, no foreground event, no speech, no music."
                    ),
                    negative_prompt=(
                        "intelligible speech, narration, singing, music, foreground impact, "
                        "watermark, clipping, hard loop seam"
                    ),
                    duration_ms=min(30_000, self._max_duration_ms),
                    loop=True,
                    output_format=self._output_format,
                    metadata={
                        "asset_tags": [title, genre, era, label, "作品级", "可复用", "环境声"]
                    },
                )
            )
        return requests

    def _build_requests(
        self,
        script: DubbingScript,
        report: ChapterSoundResolutionReport,
    ) -> list[SoundGenerationRequest]:
        requests: list[SoundGenerationRequest] = []
        story_context = self._story_context_text(script)
        style_profile = load_style_profile(self._layout)
        style_suffix = style_profile.style_suffix()
        spectral_hint = style_profile.spectral_hint
        for resolution in report.resolutions:
            if resolution.status == "matched":
                continue
            if resolution.cue_kind == SoundGenerationKind.BGM.value:
                bgm_cue = script.bgm_suggestions[resolution.cue_index]
                label = bgm_cue.track_name or bgm_cue.mood or "章节氛围配乐"
                detail = ", ".join(item for item in (bgm_cue.mood, bgm_cue.track_name) if item)
                prompt = (
                    f"Instrumental background score for an audiobook scene: {detail or label}. "
                    f"Narrative role: {bgm_cue.narrative_role}; "
                    f"intensity: {bgm_cue.intensity:.2f}. "
                    f"{style_suffix} "
                    f"{story_context} "
                    "Support story progression and leave spectral space for narration; "
                    "no vocals, no abrupt mastering changes."
                )
                duration_ms = self._cue_duration(
                    bgm_cue.start_ms,
                    bgm_cue.end_ms,
                    default_ms=60_000,
                )
                tags = [
                    label,
                    bgm_cue.mood,
                    bgm_cue.track_name,
                    *bgm_cue.mood_tags,
                    bgm_cue.narrative_role,
                ]
                kind = SoundGenerationKind.BGM
                loop = bgm_cue.loop
            elif resolution.cue_kind == SoundGenerationKind.SOUNDSCAPE.value:
                soundscape_cue = script.soundscapes[resolution.cue_index]
                label = soundscape_cue.name
                detail = soundscape_cue.description or soundscape_cue.name
                prompt = (
                    f"Seamless loopable environmental ambience for an audiobook: {detail}. "
                    f"{style_suffix} "
                    f"Environmental density: {soundscape_cue.density:.2f}. "
                    f"{story_context} "
                    "Natural stereo depth, stable room tone, low intensity, clean loop boundary, "
                    "no foreground event, no speech, no music, no abrupt spectral changes."
                )
                if spectral_hint:
                    prompt += f" {spectral_hint}."
                duration_ms = self._cue_duration(
                    soundscape_cue.start_ms,
                    soundscape_cue.end_ms,
                    default_ms=30_000,
                )
                tags = [label, soundscape_cue.asset_hint, soundscape_cue.description]
                kind = SoundGenerationKind.SOUNDSCAPE
                loop = soundscape_cue.loop
            else:
                sfx_cue = script.sfx_cues[resolution.cue_index]
                label = sfx_cue.effect_name
                detail = sfx_cue.description or sfx_cue.effect_name
                prompt = (
                    f"Isolated, clean narrative sound effect: {detail}. "
                    f"{style_suffix} "
                    f"Narrative priority: {sfx_cue.narrative_priority}/100. "
                    "One clearly defined event with a short clean tail, no speech, no music, "
                    "no watermark, no digital artifacts."
                )
                if spectral_hint:
                    prompt += f" {spectral_hint}."
                duration_ms = min(
                    max(500, sfx_cue.duration_ms or 3_000),
                    self._max_duration_ms,
                )
                tags = [label, sfx_cue.description]
                kind = SoundGenerationKind.SFX
                loop = False
            # Build the request with style-profile-driven seed and negative prompt.
            request = SoundGenerationRequest(
                request_id=(f"chapter-{script.chapter_number}-{kind.value}-{resolution.cue_index}"),
                chapter_number=script.chapter_number,
                kind=kind,
                cue_index=resolution.cue_index,
                cue_label=label,
                prompt=prompt,
                negative_prompt=style_profile.negative_prompt_for(kind),
                duration_ms=duration_ms,
                loop=loop,
                output_format=self._output_format,
                seed=None,  # Will be derived below.
                metadata={"asset_tags": [tag for tag in tags if tag]},
            )
            # Derive deterministic seed for cross-chapter consistency.
            deterministic_seed = derive_deterministic_seed(style_profile, request)
            request = request.model_copy(update={"seed": deterministic_seed})
            requests.append(request)
        return requests

    @staticmethod
    def _story_context_text(script: DubbingScript) -> str:
        raw = script.metadata.get("story_sound_context")
        if not isinstance(raw, dict):
            return ""
        values = [
            raw.get("title"),
            raw.get("genre"),
            raw.get("premise") or raw.get("theme"),
            raw.get("tone"),
            raw.get("audio_aesthetic"),
        ]
        compact = "; ".join(str(value).strip() for value in values if str(value or "").strip())
        return f"Work-level identity: {compact[:900]}." if compact else ""

    @property
    def _max_duration_ms(self) -> int:
        return self._settings.sound_generation_max_duration_s * 1000

    @property
    def _output_format(self) -> str:
        configured = self._settings.sound_generation_output_format.strip().lower()
        return configured if configured in {"wav", "mp3", "flac"} else "wav"

    def _cue_duration(self, start_ms: int, end_ms: int, *, default_ms: int) -> int:
        if end_ms > start_ms:
            return min(max(500, end_ms - start_ms), self._max_duration_ms)
        return min(default_ms, self._max_duration_ms)

    def _find_cached_asset(
        self,
        assets: list[SoundAsset],
        request: SoundGenerationRequest,
    ) -> SoundAsset | None:
        for asset in assets:
            if asset.approval_status == "rejected":
                continue
            if asset.generation_request_hash != request.fingerprint:
                continue
            path = self._layout.tts_sound_assets_dir / asset.relative_path
            if path.is_file() and path.stat().st_size > 0:
                return asset
        return None

    async def _persist_generated_asset(
        self,
        request: SoundGenerationRequest,
        generated: GeneratedSoundAsset,
        *,
        approval_status: Literal["approved", "pending", "rejected"] | None = None,
    ) -> SoundAsset:
        fingerprint = request.fingerprint
        suffix = generated.audio_format.lower()
        destination = (
            self._layout.tts_generated_sound_assets_dir
            / request.kind.value
            / f"{fingerprint[:24]}.{suffix}"
        )
        # Apply deterministic post-processing before writing to disk.
        processed_data = await postprocess_generated_audio(
            generated.audio_data, request, self._settings
        )
        ensure_project_audio_write(
            layout=self._layout,
            settings=self._settings,
            destination=destination,
            incoming_bytes=len(processed_data),
            artifact=f"生成的{request.kind.value}音频",
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(destination, processed_data)
        descriptor = SOUND_MODEL_CATALOG.get(generated.model_id)
        tags = [
            str(item).strip()
            for item in request.metadata.get("asset_tags", [])
            if str(item).strip()
        ]
        relative_path = str(destination.relative_to(self._layout.tts_sound_assets_dir))
        return SoundAsset(
            asset_id=f"generated_{request.kind.value}_{fingerprint[:16]}",
            kind=request.kind.value,
            display_name=request.cue_label,
            relative_path=relative_path,
            tags=tags,
            loopable=request.loop,
            default_volume=0.18 if request.kind == SoundGenerationKind.SOUNDSCAPE else 0.25,
            license_note=descriptor.license_summary
            if descriptor
            else "Generated asset; review provider terms.",
            commercial_use_status=(
                descriptor.default_commercial_use_status
                if descriptor
                else "review_required"
            ),
            source="generated",
            approval_status=(
                approval_status
                or ("approved" if self._settings.sound_generation_auto_approve else "pending")
            ),
            generation_provider=generated.provider,
            generation_model=generated.model_id,
            generation_request_hash=fingerprint,
            generation_output_hash=hashlib.sha256(processed_data).hexdigest(),
            generation_route_plugin_id=str(request.metadata.get("route_plugin_id") or ""),
            generation_route_plugin_version=str(request.metadata.get("route_plugin_version") or ""),
            generation_route_endpoint=str(request.metadata.get("route_endpoint") or ""),
            generation_prompt=request.prompt,
            generated_at=datetime.now(timezone.utc),
            generation_repository_id=(
                descriptor.repository_id if descriptor else (generated.repository_id or "")
            ),
            aigc_watermark=generated.aigc_watermark,
        )

    def find_bgm_by_mood(
        self,
        mood_tags: list[str],
        *,
        narrative_role: str = "",
    ) -> list[SoundAsset]:
        """Find approved BGM assets matching the given mood tags.

        Complements the request_hash-based cache with fuzzy mood matching.
        Returns assets sorted by relevance (tag overlap descending).
        """
        library = load_sound_library(self._layout)
        approved_bgm = [
            asset
            for asset in library.assets
            if asset.kind == "bgm" and asset.approval_status == "approved"
        ]
        if not mood_tags:
            return approved_bgm

        scored: list[tuple[float, SoundAsset]] = []
        mood_set = {tag.casefold() for tag in mood_tags}
        for asset in approved_bgm:
            asset_tags_lower = {tag.casefold() for tag in asset.tags}
            overlap = len(asset_tags_lower & mood_set)
            role_bonus = 0.3 if narrative_role.casefold() in asset_tags_lower else 0.0
            score = float(overlap) + role_bonus
            if score > 0:
                scored.append((score, asset))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [asset for _, asset in scored]

    @staticmethod
    def _emit_progress(
        callback: Callable[[str, dict[str, Any]], None] | None,
        event: str,
        data: dict[str, Any],
    ) -> None:
        if callback is not None:
            callback(event, data)
