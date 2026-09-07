"""Settings bridge for the audio plugin registry and planner."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novel_forge.core.config import Settings
from novel_forge.tts.platform.planner import AudioExecutionPlanner
from novel_forge.tts.platform.registry import AudioPluginRegistry
from novel_forge.tts.platform.schemas import (
    AudioCapability,
    AudioExecutionPlan,
    AudioExecutionStage,
    AudioLocationPolicy,
    AudioMemoryClass,
    AudioProjectConstraints,
    AudioQualityPreset,
    ModelScorecard,
)


def _default_tts_plugin(
    registry: AudioPluginRegistry,
    provider_id: str,
    preset: AudioQualityPreset,
    model_id: str = "",
) -> str:
    """Select a provider-native speech plugin from declarative manifests."""

    normalized = provider_id.strip().lower()
    manifest_provider = "dashscope" if normalized == "bailian" else normalized
    candidates = [
        manifest
        for manifest in registry.candidates(AudioCapability.SPEECH_SYNTHESIS)
        if manifest.provider_id.strip().lower() == manifest_provider
    ]
    if not candidates:
        return ""
    exact = [manifest for manifest in candidates if manifest.model_id == model_id]
    if exact:
        return exact[0].plugin_id
    preferred_tag = "preview" if preset == AudioQualityPreset.QUICK_PREVIEW else "formal"
    preferred = [manifest for manifest in candidates if preferred_tag in manifest.tags]
    return (preferred or candidates)[0].plugin_id


def _json_mapping(raw: str) -> dict[str, str]:
    try:
        payload = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, Mapping):
        return {}
    return {
        str(key).strip(): str(value).strip()
        for key, value in payload.items()
        if str(key).strip() and str(value).strip()
    }


def constraints_from_settings(
    settings: Settings,
    *,
    languages: list[str] | None = None,
    registry: AudioPluginRegistry | None = None,
) -> AudioProjectConstraints:
    try:
        preset = AudioQualityPreset(settings.audio_quality_preset)
    except ValueError:
        preset = AudioQualityPreset.PRODUCTION
    try:
        location_policy = AudioLocationPolicy(settings.audio_location_policy)
    except ValueError:
        location_policy = AudioLocationPolicy.HYBRID
    try:
        memory = AudioMemoryClass(settings.audio_memory_budget)
    except ValueError:
        memory = AudioMemoryClass.MEDIUM
    overrides: dict[AudioExecutionStage, str] = {}
    for stage, plugin_id in _json_mapping(settings.audio_plugin_overrides).items():
        try:
            parsed_stage = AudioExecutionStage(stage)
        except ValueError:
            continue
        overrides[parsed_stage] = plugin_id
    resolved_registry = registry or registry_from_settings(settings)
    provider_plugin = _default_tts_plugin(
        resolved_registry,
        settings.tts_default_provider,
        preset,
        (
            settings.tts_dashscope_model
            if settings.tts_default_provider.strip().lower() in {"bailian", "dashscope"}
            else ""
        ),
    )
    provider_manifest = resolved_registry.get(provider_plugin) if provider_plugin else None
    if provider_manifest is not None:
        provider_id = provider_manifest.provider_id.strip().lower()
        for stage, capability in (
            (AudioExecutionStage.VOICE_DESIGN, AudioCapability.VOICE_DESIGN),
            (AudioExecutionStage.VOICE_CLONE, AudioCapability.VOICE_CLONE),
            (AudioExecutionStage.TTS_PREVIEW, AudioCapability.SPEECH_SYNTHESIS),
            (AudioExecutionStage.TTS_FORMAL, AudioCapability.SPEECH_SYNTHESIS),
        ):
            if stage in overrides:
                continue
            native = [
                manifest
                for manifest in resolved_registry.candidates(capability)
                if manifest.provider_id.strip().lower() == provider_id
            ]
            target_model = ""
            if provider_id == "dashscope":
                if stage == AudioExecutionStage.VOICE_DESIGN:
                    target_model = settings.tts_dashscope_voice_design_model
                elif stage == AudioExecutionStage.VOICE_CLONE:
                    target_model = settings.tts_dashscope_voice_clone_model
                elif stage == AudioExecutionStage.TTS_PREVIEW:
                    target_model = (
                        settings.tts_dashscope_preview_model or settings.tts_dashscope_model
                    )
                else:
                    target_model = settings.tts_dashscope_model
            exact = [manifest for manifest in native if manifest.model_id == target_model]
            if exact:
                native = exact
            elif stage == AudioExecutionStage.TTS_PREVIEW:
                preview = [manifest for manifest in native if "preview" in manifest.tags]
                native = preview or native
            elif stage == AudioExecutionStage.TTS_FORMAL:
                formal = [manifest for manifest in native if "formal" in manifest.tags]
                native = formal or native
            if native:
                overrides[stage] = native[0].plugin_id
    disabled = {item.strip() for item in settings.audio_disabled_plugins.split(",") if item.strip()}
    plugin_endpoints = {
        manifest.plugin_id: str(
            (
                getattr(settings, manifest.runtime.endpoint_setting, "")
                if manifest.runtime.endpoint_setting
                else ""
            )
            or manifest.runtime.default_endpoint
        ).rstrip("/")
        for manifest in resolved_registry.all()
        if manifest.runtime.endpoint_setting or manifest.runtime.default_endpoint
    }
    return AudioProjectConstraints(
        preset=preset,
        location_policy=location_policy,
        languages=languages or ["zh"],
        accelerator=settings.audio_accelerator_preference,
        memory_budget=memory,
        plugin_overrides=overrides,
        language_overrides=_json_mapping(settings.audio_language_overrides),
        disabled_plugins=disabled,
        require_alignment_validation=settings.audio_dual_alignment_validation,
        plugin_endpoints=plugin_endpoints,
        budget_limit_usd=settings.audio_budget_limit_usd,
    )


def registry_from_settings(settings: Settings) -> AudioPluginRegistry:
    registry = AudioPluginRegistry.builtins()
    directories = [
        item.strip() for item in settings.audio_plugin_manifest_dirs.split(",") if item.strip()
    ]
    registry.load_manifest_dirs(directories)
    return registry


def load_scorecards(path: str | Path | None) -> list[ModelScorecard]:
    if path is None:
        return []
    scorecard_path = Path(path)
    if not scorecard_path.is_file():
        return []
    try:
        payload: Any = json.loads(scorecard_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    raw_items = payload if isinstance(payload, list) else payload.get("scorecards", [])
    if not isinstance(raw_items, list):
        return []
    scorecards: list[ModelScorecard] = []
    for raw in raw_items:
        try:
            scorecards.append(ModelScorecard.model_validate(raw))
        except Exception:
            continue
    return scorecards


def build_audio_execution_plan(
    settings: Settings,
    *,
    languages: list[str] | None = None,
    scorecard_path: str | Path | None = None,
    project_id: str = "",
) -> AudioExecutionPlan:
    registry = registry_from_settings(settings)
    planner = AudioExecutionPlanner(registry, scorecards=load_scorecards(scorecard_path))
    plan = planner.plan(constraints_from_settings(settings, languages=languages, registry=registry))
    frozen_at = datetime.now(timezone.utc)
    digest_payload = {
        "location_policy": plan.location_policy.value,
        "preset": plan.preset.value,
        "budget_limit_usd": plan.budget_limit_usd,
        "memory_budget": plan.memory_budget.value,
        "routes": [item.model_dump(mode="json") for item in plan.routes],
        "language_routes": [item.model_dump(mode="json") for item in plan.language_routes],
        "hardware": plan.hardware.model_dump(mode="json"),
    }
    digest = hashlib.sha256(
        json.dumps(digest_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return plan.model_copy(
        update={
            "plan_id": f"audio-{digest[:16]}",
            "project_id": project_id,
            "frozen": True,
            "frozen_at": frozen_at,
            "manifest_digest": digest,
        }
    )
