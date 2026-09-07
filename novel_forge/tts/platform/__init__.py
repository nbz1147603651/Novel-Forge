"""Capability-driven audio model planning and plugin discovery."""

from __future__ import annotations

from novel_forge.tts.platform.catalog import builtin_audio_plugin_manifests
from novel_forge.tts.platform.planner import AudioExecutionPlanner
from novel_forge.tts.platform.provider_catalog import (
    TTSProviderModelSpec,
    TTSProviderSettingSpec,
    TTSProviderSpec,
    TTSSettingOption,
    normalize_tts_provider_id,
    tts_provider_capabilities,
    tts_provider_catalog,
    tts_provider_spec,
)
from novel_forge.tts.platform.registry import AudioPluginRegistry
from novel_forge.tts.platform.schemas import (
    AudioCapability,
    AudioExecutionPlan,
    AudioExecutionStage,
    AudioHardwareProfile,
    AudioLocationPolicy,
    AudioPluginManifest,
    AudioProjectConstraints,
    AudioQualityPreset,
    ModelScorecard,
)

__all__ = [
    "AudioCapability",
    "AudioExecutionPlan",
    "AudioHardwareProfile",
    "AudioLocationPolicy",
    "AudioExecutionPlanner",
    "AudioExecutionStage",
    "AudioPluginManifest",
    "AudioPluginRegistry",
    "AudioProjectConstraints",
    "AudioQualityPreset",
    "ModelScorecard",
    "TTSProviderModelSpec",
    "TTSProviderSettingSpec",
    "TTSProviderSpec",
    "TTSSettingOption",
    "builtin_audio_plugin_manifests",
    "normalize_tts_provider_id",
    "tts_provider_capabilities",
    "tts_provider_catalog",
    "tts_provider_spec",
]
