"""Capability-based model planner for preview, production, and master audio."""

from __future__ import annotations

from collections.abc import Iterable

from novel_forge.tts.platform.hardware import detect_audio_hardware
from novel_forge.tts.platform.registry import AudioPluginRegistry
from novel_forge.tts.platform.schemas import (
    AudioCapability,
    AudioExecutionPlan,
    AudioExecutionStage,
    AudioHardwareProfile,
    AudioLanguageRoute,
    AudioLocationPolicy,
    AudioMemoryClass,
    AudioPluginManifest,
    AudioProjectConstraints,
    AudioQualityClass,
    AudioQualityPreset,
    AudioRouteTarget,
    AudioStageRoute,
    ModelScorecard,
)

_STAGE_CAPABILITY: dict[AudioExecutionStage, AudioCapability] = {
    AudioExecutionStage.VOICE_DESIGN: AudioCapability.VOICE_DESIGN,
    AudioExecutionStage.VOICE_CLONE: AudioCapability.VOICE_CLONE,
    AudioExecutionStage.TTS_PREVIEW: AudioCapability.SPEECH_SYNTHESIS,
    AudioExecutionStage.TTS_FORMAL: AudioCapability.SPEECH_SYNTHESIS,
    AudioExecutionStage.ASR: AudioCapability.ASR,
    AudioExecutionStage.ALIGN: AudioCapability.FORCED_ALIGNMENT,
    AudioExecutionStage.ALIGNMENT_VALIDATOR: AudioCapability.FORCED_ALIGNMENT,
    AudioExecutionStage.VAD: AudioCapability.VAD,
    AudioExecutionStage.SFX: AudioCapability.SFX_GENERATION,
    AudioExecutionStage.MUSIC: AudioCapability.MUSIC_GENERATION,
    AudioExecutionStage.SOUNDSCAPE: AudioCapability.SOUNDSCAPE_GENERATION,
    AudioExecutionStage.RENDERER: AudioCapability.AUDIO_RENDER,
    AudioExecutionStage.QUALITY: AudioCapability.QUALITY_EVALUATION,
}

_VOICE_RESOURCE_STAGES = {
    AudioExecutionStage.VOICE_DESIGN,
    AudioExecutionStage.VOICE_CLONE,
    AudioExecutionStage.TTS_PREVIEW,
    AudioExecutionStage.TTS_FORMAL,
}

_MEMORY_RANK = {
    AudioMemoryClass.LIGHT: 0,
    AudioMemoryClass.MEDIUM: 1,
    AudioMemoryClass.HIGH: 2,
}

_QUALITY_RANK = {
    AudioQualityClass.PREVIEW: 0,
    AudioQualityClass.BALANCED: 1,
    AudioQualityClass.PRODUCTION: 2,
    AudioQualityClass.MASTER: 3,
}


class AudioExecutionPlanner:
    """Build an auditable model plan from capabilities and user constraints."""

    def __init__(
        self,
        registry: AudioPluginRegistry,
        *,
        scorecards: Iterable[ModelScorecard] = (),
    ) -> None:
        self._registry = registry
        self._scorecards = {item.plugin_id: item for item in scorecards}

    def plan(
        self,
        constraints: AudioProjectConstraints,
        *,
        hardware: AudioHardwareProfile | None = None,
    ) -> AudioExecutionPlan:
        hardware = hardware or detect_audio_hardware(accelerator_preference=constraints.accelerator)
        warnings: list[str] = []
        routes: list[AudioStageRoute] = []
        stages = [
            AudioExecutionStage.VOICE_DESIGN,
            AudioExecutionStage.VOICE_CLONE,
            AudioExecutionStage.TTS_PREVIEW,
            AudioExecutionStage.TTS_FORMAL,
            AudioExecutionStage.ASR,
            AudioExecutionStage.ALIGN,
            AudioExecutionStage.VAD,
            AudioExecutionStage.SFX,
            AudioExecutionStage.MUSIC,
            AudioExecutionStage.SOUNDSCAPE,
            AudioExecutionStage.RENDERER,
            AudioExecutionStage.QUALITY,
        ]
        if constraints.require_alignment_validation:
            stages.insert(
                stages.index(AudioExecutionStage.ALIGN) + 1,
                AudioExecutionStage.ALIGNMENT_VALIDATOR,
            )

        chosen: dict[AudioExecutionStage, str] = {}
        for stage in stages:
            assignment = self._choose_stage(
                stage,
                constraints=constraints,
                hardware=hardware,
                excluded={chosen.get(AudioExecutionStage.ALIGN, "")}
                if stage == AudioExecutionStage.ALIGNMENT_VALIDATOR
                else set(),
            )
            routes.append(assignment)
            if assignment.primary is not None:
                chosen[stage] = assignment.primary.plugin_id
            else:
                warnings.append(f"{stage.value} 没有满足当前约束的可用插件。")

        language_routes = [
            self._language_route(language, constraints=constraints, hardware=hardware)
            for language in constraints.languages
            if language and language != "auto"
        ]
        for route in language_routes:
            if not route.aligner_plugin_id:
                warnings.append(f"{route.language} 暂无词/字级对齐器，将降级为片段时间轴。")

        return AudioExecutionPlan(
            preset=constraints.preset,
            location_policy=constraints.location_policy,
            budget_limit_usd=constraints.budget_limit_usd,
            memory_budget=constraints.memory_budget,
            routes=routes,
            language_routes=language_routes,
            warnings=warnings,
            hardware=hardware,
        )

    def _choose_stage(
        self,
        stage: AudioExecutionStage,
        *,
        constraints: AudioProjectConstraints,
        hardware: AudioHardwareProfile,
        languages: list[str] | None = None,
        excluded: set[str] | None = None,
    ) -> AudioStageRoute:
        override = constraints.plugin_overrides.get(stage, "")
        capability = _STAGE_CAPABILITY[stage]
        language_list = languages if languages is not None else constraints.languages
        excluded = excluded or set()
        if override:
            manifest = self._registry.get(override)
            if manifest is not None and manifest.supplies(capability):
                if not self._location_allowed(manifest, constraints):
                    return AudioStageRoute(
                        stage=stage,
                        reason="固定插件违反当前运行位置/隐私边界，已拒绝执行。",
                        overridden=True,
                        languages=list(language_list),
                    )
                unsupported = [
                    language
                    for language in language_list
                    if not manifest.capabilities.supports_language(language)
                ]
                reason = "用户已固定此阶段插件。"
                if unsupported:
                    reason += f" 注意：未声明支持 {', '.join(unsupported)}。"
                route = self._route_target(manifest, constraints)
                fallback_candidates = self._registry.candidates(
                    capability,
                    languages=language_list,
                    constraints=constraints,
                )
                fallback_candidates = [
                    item
                    for item in fallback_candidates
                    if item.plugin_id not in excluded | {manifest.plugin_id}
                ]
                ranked_fallbacks = sorted(
                    fallback_candidates,
                    key=lambda item: self._score(item, constraints, hardware, stage),
                    reverse=True,
                )[:3]
                return AudioStageRoute(
                    stage=stage,
                    primary=route,
                    fallbacks=[
                        self._route_target(
                            item,
                            constraints,
                            voice_mapping_required=(
                                stage in _VOICE_RESOURCE_STAGES
                                and item.provider_id != manifest.provider_id
                            ),
                        )
                        for item in ranked_fallbacks
                    ],
                    reason=reason,
                    overridden=True,
                    languages=list(language_list),
                )

        candidates = self._registry.candidates(
            capability,
            languages=language_list,
            constraints=constraints,
        )
        candidates = [item for item in candidates if item.plugin_id not in excluded]
        ranked = sorted(
            candidates,
            key=lambda item: self._score(item, constraints, hardware, stage),
            reverse=True,
        )
        if not ranked:
            return AudioStageRoute(stage=stage, languages=list(language_list))
        primary = ranked[0]
        fallbacks = ranked[1:4]
        primary_route = self._route_target(primary, constraints)
        return AudioStageRoute(
            stage=stage,
            primary=primary_route,
            fallbacks=[
                self._route_target(
                    item,
                    constraints,
                    voice_mapping_required=(
                        stage in _VOICE_RESOURCE_STAGES and item.provider_id != primary.provider_id
                    ),
                )
                for item in fallbacks
            ],
            reason=self._reason(primary, constraints, hardware),
            languages=list(language_list),
        )

    def _language_route(
        self,
        language: str,
        *,
        constraints: AudioProjectConstraints,
        hardware: AudioHardwareProfile,
    ) -> AudioLanguageRoute:
        explicit = constraints.language_overrides.get(language, "")
        align_constraints = constraints.model_copy(deep=True)
        if explicit:
            align_constraints.plugin_overrides[AudioExecutionStage.ALIGN] = explicit
        asr = self._choose_stage(
            AudioExecutionStage.ASR,
            constraints=constraints,
            hardware=hardware,
            languages=[language],
        )
        aligner = self._choose_stage(
            AudioExecutionStage.ALIGN,
            constraints=align_constraints,
            hardware=hardware,
            languages=[language],
        )
        return AudioLanguageRoute(
            language=language,
            asr_plugin_id=asr.primary.plugin_id if asr.primary else "",
            aligner_plugin_id=aligner.primary.plugin_id if aligner.primary else "",
            fallback_plugin_ids=[item.plugin_id for item in aligner.fallbacks],
            reason=aligner.reason or asr.reason,
        )

    def _score(
        self,
        manifest: AudioPluginManifest,
        constraints: AudioProjectConstraints,
        hardware: AudioHardwareProfile,
        stage: AudioExecutionStage,
    ) -> float:
        quality_weight = 0.56
        latency_weight = 0.18
        if constraints.preset in {
            AudioQualityPreset.QUICK_PREVIEW,
            AudioQualityPreset.LOW_RESOURCE,
        }:
            quality_weight, latency_weight = 0.32, 0.42
        elif constraints.preset == AudioQualityPreset.MASTER:
            quality_weight, latency_weight = 0.72, 0.08

        score = (
            manifest.quality.quality_score * quality_weight
            + manifest.quality.latency_score * latency_weight
            + manifest.quality.priority / 1000.0
        )
        scorecard = self._scorecards.get(manifest.plugin_id)
        if scorecard is not None and scorecard.sample_count > 0:
            score += scorecard.quality_score * 0.28

        memory_budget = min(
            _MEMORY_RANK[constraints.memory_budget],
            _MEMORY_RANK[hardware.memory_class],
        )
        memory_cost = _MEMORY_RANK[manifest.runtime.memory_class]
        if memory_cost > memory_budget:
            score -= 0.48 * (memory_cost - memory_budget)
        if constraints.accelerator not in {"", "auto"} and (
            constraints.accelerator not in manifest.runtime.accelerators
            and "cloud" not in manifest.runtime.accelerators
        ):
            score -= 0.3
        if not manifest.runtime.is_cloud:
            score += 0.22 if manifest.runtime.managed_by_app else -0.08
        if constraints.location_policy == AudioLocationPolicy.PREFER_CLOUD:
            score += 0.22 if manifest.runtime.is_cloud else -0.05
        elif constraints.location_policy == AudioLocationPolicy.PREFER_LOCAL:
            score += 0.12 if manifest.capabilities.offline else -0.18
        elif constraints.location_policy == AudioLocationPolicy.HYBRID:
            prefers_cloud = stage in _VOICE_RESOURCE_STAGES
            if prefers_cloud:
                score += 0.2 if manifest.runtime.is_cloud else -0.08
            else:
                score += 0.18 if manifest.capabilities.offline else -0.2
        if constraints.preset == AudioQualityPreset.QUICK_PREVIEW and "preview" in manifest.tags:
            score += 0.25
        if constraints.preset == AudioQualityPreset.MASTER:
            score += _QUALITY_RANK[manifest.quality.profile] * 0.06
        return score

    @staticmethod
    def _route_target(
        manifest: AudioPluginManifest,
        constraints: AudioProjectConstraints,
        *,
        voice_mapping_required: bool = False,
    ) -> AudioRouteTarget:
        return AudioRouteTarget(
            plugin_id=manifest.plugin_id,
            provider_id=manifest.provider_id,
            model_id=manifest.model_id,
            plugin_version=manifest.plugin_version,
            endpoint=(
                constraints.plugin_endpoints.get(manifest.plugin_id)
                or manifest.runtime.default_endpoint
            ),
            credential_settings=list(manifest.runtime.credential_settings),
            execution=manifest.runtime.execution,
            offline=manifest.capabilities.offline,
            license_summary=manifest.license_summary,
            voice_mapping_required=voice_mapping_required,
        )

    @staticmethod
    def _location_allowed(
        manifest: AudioPluginManifest,
        constraints: AudioProjectConstraints,
    ) -> bool:
        if (
            constraints.location_policy == AudioLocationPolicy.LOCAL_ONLY
            and manifest.runtime.is_cloud
        ):
            return False
        if (
            constraints.location_policy == AudioLocationPolicy.CLOUD_ONLY
            and not manifest.runtime.is_cloud
        ):
            return False
        return True

    @staticmethod
    def _reason(
        manifest: AudioPluginManifest,
        constraints: AudioProjectConstraints,
        hardware: AudioHardwareProfile,
    ) -> str:
        location = "本地离线" if manifest.capabilities.offline else "云端"
        quality = {
            AudioQualityClass.PREVIEW: "试听",
            AudioQualityClass.BALANCED: "均衡",
            AudioQualityClass.PRODUCTION: "成片",
            AudioQualityClass.MASTER: "精校",
        }[manifest.quality.profile]
        reason = f"匹配“{quality}”质量、{location}能力与 {hardware.accelerator.upper()} 运行偏好。"
        if constraints.preset == AudioQualityPreset.MASTER and manifest.capabilities.confidence:
            reason += " 可输出置信信息供质量门复核。"
        return reason
