"""Allowlisted runtime-parameter projection and persistence for Engine settings.

This module is the shared catalog for Engine settings commands and their read
projection.  It accepts frontend field IDs only through explicit maps, so a
client can never turn an arbitrary key into an environment variable name.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Protocol

from pydantic import TypeAdapter, ValidationError

from novel_forge.app_service.settings_parameter_extensions import apply_param_map_extensions
from novel_forge.core.config import Settings


class SettingsRouteTemperatureCommand(Protocol):
    """The route fields needed to persist a task-specific temperature."""

    temperature: float | None


_PARAM_ENV_MAP: dict[str, str] = {
    "short-max-edit": "NOVEL_FORGE_SHORT_MAX_EDIT_ROUNDS",
    "outline-batch": "NOVEL_FORGE_OUTLINE_BATCH_SIZE",
    "alignment-threshold": "NOVEL_FORGE_LONG_ALIGNMENT_THRESHOLD",
    "continuity-threshold": "NOVEL_FORGE_LONG_CONTINUITY_THRESHOLD",
    "causal-threshold": "NOVEL_FORGE_LONG_CAUSAL_THRESHOLD",
    "reading-power-threshold": "NOVEL_FORGE_LONG_READING_POWER_THRESHOLD",
    "guard-mode": "NOVEL_FORGE_LONG_PLOT_GUARD_MODE",
    "repair-rounds": "NOVEL_FORGE_LONG_TOTAL_REPAIR_ROUNDS_CAP",
    "reading-repair": "NOVEL_FORGE_LONG_READING_POWER_REPAIR_ENABLED",
    "polish-enabled": "NOVEL_FORGE_LONG_POLISH_ENABLED",
    "polish-threshold": "NOVEL_FORGE_LONG_POLISH_AUTO_TRIGGER_THRESHOLD",
    "humanize-enabled": "NOVEL_FORGE_HUMANIZE_ENABLED",
    "humanize-intensity": "NOVEL_FORGE_HUMANIZE_INTENSITY",
    "tts-enabled": "NOVEL_FORGE_TTS_ENABLED",
    "tts-provider": "NOVEL_FORGE_TTS_DEFAULT_PROVIDER",
    "tts-model": "NOVEL_FORGE_TTS_DEFAULT_MODEL",
    "tts-speed": "NOVEL_FORGE_TTS_DEFAULT_SPEED",
    "tts-output-format": "NOVEL_FORGE_TTS_OUTPUT_FORMAT",
    "tts-automation-mode": "NOVEL_FORGE_TTS_AUTOMATION_MODE",
    "tts-concurrency": "NOVEL_FORGE_TTS_MAX_CONCURRENT_SYNTHESIS",
    "tts-voice-library-scope": "NOVEL_FORGE_TTS_VOICE_LIBRARY_SCOPE",
    "tts-auto-trigger-after-chapter": "NOVEL_FORGE_TTS_AUTO_TRIGGER_AFTER_CHAPTER",
    "tts-post-archive-retry-enabled": "NOVEL_FORGE_TTS_POST_ARCHIVE_RETRY_ENABLED",
    "tts-post-archive-retry-delay-s": "NOVEL_FORGE_TTS_POST_ARCHIVE_RETRY_DELAY_S",
    "tts-voice-design-enabled": "NOVEL_FORGE_TTS_VOICE_DESIGN_ENABLED",
    "tts-voice-semantic-matching-enabled": "NOVEL_FORGE_TTS_VOICE_SEMANTIC_MATCHING_ENABLED",
    "tts-voice-semantic-top-k": "NOVEL_FORGE_TTS_VOICE_SEMANTIC_TOP_K",
    "tts-monthly-cost-budget-usd": "NOVEL_FORGE_TTS_MONTHLY_COST_BUDGET_USD",
    "tts-book-cost-budget-usd": "NOVEL_FORGE_TTS_BOOK_COST_BUDGET_USD",
    "tts-project-max-storage-mb": "NOVEL_FORGE_TTS_PROJECT_MAX_STORAGE_MB",
    "audio-budget-limit-usd": "NOVEL_FORGE_AUDIO_BUDGET_LIMIT_USD",
    # Voice Studio / platform settings.  Keep this explicit allowlist in sync
    # with the Web implementation: the write endpoint must never turn an
    # arbitrary field id into an environment variable name.
    "audio-quality-preset": "NOVEL_FORGE_AUDIO_QUALITY_PRESET",
    "audio-location-policy": "NOVEL_FORGE_AUDIO_LOCATION_POLICY",
    "audio-accelerator-preference": "NOVEL_FORGE_AUDIO_ACCELERATOR_PREFERENCE",
    "local-model-resource-budget": "NOVEL_FORGE_LOCAL_MODEL_RESOURCE_BUDGET",
    "local-model-resource-wait-timeout-s": "NOVEL_FORGE_LOCAL_MODEL_RESOURCE_WAIT_TIMEOUT_S",
    "audio-memory-budget": "NOVEL_FORGE_AUDIO_MEMORY_BUDGET",
    "audio-plugin-overrides": "NOVEL_FORGE_AUDIO_PLUGIN_OVERRIDES",
    "audio-dual-alignment-validation": "NOVEL_FORGE_AUDIO_DUAL_ALIGNMENT_VALIDATION",
    "audio-qwen3-asr-base-url": "NOVEL_FORGE_AUDIO_QWEN3_ASR_BASE_URL",
    # Secrets are write-only.  They deliberately do not appear in
    # ``_PARAM_SETTINGS_ATTR`` below, so a settings read cannot expose them.
    "audio-qwen3-asr-api-key": "NOVEL_FORGE_AUDIO_QWEN3_ASR_API_KEY",
    "audio-whisperx-base-url": "NOVEL_FORGE_AUDIO_WHISPERX_BASE_URL",
    "audio-sherpa-base-url": "NOVEL_FORGE_AUDIO_SHERPA_BASE_URL",
    "audio-mfa-command": "NOVEL_FORGE_AUDIO_MFA_COMMAND",
    "audio-plugin-manifest-dirs": "NOVEL_FORGE_AUDIO_PLUGIN_MANIFEST_DIRS",
    "tts-alignment-repair-rounds": "NOVEL_FORGE_TTS_ALIGNMENT_REPAIR_ROUNDS",
    "tts-audio-quality-tier": "NOVEL_FORGE_TTS_AUDIO_QUALITY_TIER",
    "tts-max-text-error-rate": "NOVEL_FORGE_TTS_MAX_TEXT_ERROR_RATE",
    "tts-master-quality-gate-blocking": "NOVEL_FORGE_TTS_MASTER_QUALITY_GATE_BLOCKING",
    "tts-subtitle-word-level": "NOVEL_FORGE_TTS_SUBTITLE_WORD_LEVEL",
    "tts-minimax-base-url": "NOVEL_FORGE_TTS_MINIMAX_BASE_URL",
    "tts-minimax-group-id": "NOVEL_FORGE_TTS_MINIMAX_GROUP_ID",
    "tts-minimax-bitrate": "NOVEL_FORGE_TTS_MINIMAX_BITRATE",
    "tts-minimax-channel": "NOVEL_FORGE_TTS_MINIMAX_CHANNEL",
    "tts-minimax-language-boost": "NOVEL_FORGE_TTS_MINIMAX_LANGUAGE_BOOST",
    "tts-minimax-force-cbr": "NOVEL_FORGE_TTS_MINIMAX_FORCE_CBR",
    "tts-minimax-english-normalization": "NOVEL_FORGE_TTS_MINIMAX_ENGLISH_NORMALIZATION",
    "tts-minimax-continuous-sound": "NOVEL_FORGE_TTS_MINIMAX_CONTINUOUS_SOUND_DEFAULT",
    "tts-minimax-async-timeout-s": "NOVEL_FORGE_TTS_MINIMAX_ASYNC_TIMEOUT_S",
    "tts-aigc-watermark": "NOVEL_FORGE_TTS_AIGC_WATERMARK_DEFAULT",
    "tts-minimax-api-key": "NOVEL_FORGE_TTS_MINIMAX_API_KEY",
    "tts-dashscope-base-url": "NOVEL_FORGE_TTS_DASHSCOPE_BASE_URL",
    "tts-dashscope-model": "NOVEL_FORGE_TTS_DASHSCOPE_MODEL",
    "tts-dashscope-preview-model": "NOVEL_FORGE_TTS_DASHSCOPE_PREVIEW_MODEL",
    "tts-dashscope-voice-clone-model": "NOVEL_FORGE_TTS_DASHSCOPE_VOICE_CLONE_MODEL",
    "tts-dashscope-voice-design-model": "NOVEL_FORGE_TTS_DASHSCOPE_VOICE_DESIGN_MODEL",
    "tts-dashscope-optimize-instructions": "NOVEL_FORGE_TTS_DASHSCOPE_OPTIMIZE_INSTRUCTIONS",
    "tts-dashscope-voice-clone-enable-preprocess": (
        "NOVEL_FORGE_TTS_DASHSCOPE_VOICE_CLONE_ENABLE_PREPROCESS"
    ),
    "tts-dashscope-cny-per-usd": "NOVEL_FORGE_TTS_DASHSCOPE_CNY_PER_USD",
    "tts-dashscope-api-key": "NOVEL_FORGE_TTS_DASHSCOPE_API_KEY",
    "tts-tencent-secret-id": "NOVEL_FORGE_TTS_TENCENT_SECRET_ID",
    "tts-tencent-secret-key": "NOVEL_FORGE_TTS_TENCENT_SECRET_KEY",
    "tts-tencent-project-id": "NOVEL_FORGE_TTS_TENCENT_PROJECT_ID",
    "tts-tencent-voice-type": "NOVEL_FORGE_TTS_TENCENT_VOICE_TYPE",
    "tts-tencent-primary-language": "NOVEL_FORGE_TTS_TENCENT_PRIMARY_LANGUAGE",
    "tts-tencent-emotion-intensity": "NOVEL_FORGE_TTS_TENCENT_EMOTION_INTENSITY",
    "tts-tencent-segment-rate": "NOVEL_FORGE_TTS_TENCENT_SEGMENT_RATE",
    "tts-volcengine-base-url": "NOVEL_FORGE_TTS_VOLCENGINE_BASE_URL",
    "tts-volcengine-model": "NOVEL_FORGE_TTS_VOLCENGINE_MODEL",
    "tts-volcengine-resource-id": "NOVEL_FORGE_TTS_VOLCENGINE_RESOURCE_ID",
    "volcengine-ark-api-key": "NOVEL_FORGE_VOLCENGINE_ARK_API_KEY",
    "tts-mimo-base-url": "NOVEL_FORGE_TTS_MIMO_BASE_URL",
    "tts-mimo-model": "NOVEL_FORGE_TTS_MIMO_MODEL",
    "tts-mimo-api-key": "NOVEL_FORGE_TTS_MIMO_API_KEY",
    "tts-local-base-url": "NOVEL_FORGE_TTS_LOCAL_BASE_URL",
    "tts-local-model": "NOVEL_FORGE_TTS_LOCAL_MODEL",
    "tts-local-api-key": "NOVEL_FORGE_TTS_LOCAL_API_KEY",
    "tts-qwen3-base-url": "NOVEL_FORGE_TTS_QWEN3_BASE_URL",
    "tts-qwen3-formal-model": "NOVEL_FORGE_TTS_QWEN3_FORMAL_MODEL",
    "tts-qwen3-preview-model": "NOVEL_FORGE_TTS_QWEN3_PREVIEW_MODEL",
    "tts-qwen3-design-model": "NOVEL_FORGE_TTS_QWEN3_DESIGN_MODEL",
    "tts-qwen3-clone-model": "NOVEL_FORGE_TTS_QWEN3_CLONE_MODEL",
    "tts-qwen3-api-key": "NOVEL_FORGE_TTS_QWEN3_API_KEY",
    "tts-cosyvoice-base-url": "NOVEL_FORGE_TTS_COSYVOICE_BASE_URL",
    "tts-cosyvoice-model": "NOVEL_FORGE_TTS_COSYVOICE_MODEL",
    "tts-cosyvoice-mode": "NOVEL_FORGE_TTS_COSYVOICE_MODE",
    "tts-cosyvoice-sample-rate": "NOVEL_FORGE_TTS_COSYVOICE_SAMPLE_RATE",
    "tts-openvoice-model": "NOVEL_FORGE_TTS_OPENVOICE_MODEL",
    "tts-openvoice-checkpoint-dir": "NOVEL_FORGE_TTS_OPENVOICE_CHECKPOINT_DIR",
    "tts-openvoice-device": "NOVEL_FORGE_TTS_OPENVOICE_DEVICE",
    "tts-openvoice-language": "NOVEL_FORGE_TTS_OPENVOICE_LANGUAGE",
    "tts-background-pipeline-concurrency": "NOVEL_FORGE_TTS_BACKGROUND_PIPELINE_CONCURRENCY",
    "tts-script-max-concurrent-batches": "NOVEL_FORGE_TTS_SCRIPT_MAX_CONCURRENT_BATCHES",
    "tts-synthesis-rpm": "NOVEL_FORGE_TTS_SYNTHESIS_REQUESTS_PER_MINUTE",
    "tts-rate-limit-cooldown-s": "NOVEL_FORGE_TTS_SYNTHESIS_RATE_LIMIT_COOLDOWN_S",
    "tts-synthesis-retry-limit": "NOVEL_FORGE_TTS_SYNTHESIS_RETRY_LIMIT",
    "tts-sample-rate": "NOVEL_FORGE_TTS_SAMPLE_RATE",
    "tts-voice-clone-ttl-days": "NOVEL_FORGE_TTS_VOICE_CLONE_TTL_DAYS",
    "tts-parallel-voice-clone": "NOVEL_FORGE_TTS_PARALLEL_VOICE_CLONE",
    "tts-narrator-voice-id": "NOVEL_FORGE_TTS_NARRATOR_VOICE_ID",
    "sound-generation-enabled": "NOVEL_FORGE_SOUND_GENERATION_ENABLED",
    "sound-generation-auto-generate": "NOVEL_FORGE_SOUND_GENERATION_AUTO_GENERATE",
    "sound-generation-auto-approve": "NOVEL_FORGE_SOUND_GENERATION_AUTO_APPROVE",
    "tts-sound-design-enabled": "NOVEL_FORGE_TTS_SOUND_DESIGN_ENABLED",
    "tts-script-llm-review-enabled": "NOVEL_FORGE_TTS_SCRIPT_LLM_REVIEW_ENABLED",
    "tts-script-llm-review-max-output-tokens": "NOVEL_FORGE_TTS_SCRIPT_LLM_REVIEW_MAX_OUTPUT_TOKENS",
    "tts-voice-llm-adjudication-enabled": "NOVEL_FORGE_TTS_VOICE_LLM_ADJUDICATION_ENABLED",
    "tts-voice-llm-adjudication-min-match-score": "NOVEL_FORGE_TTS_VOICE_LLM_ADJUDICATION_MIN_MATCH_SCORE",
    "tts-voice-llm-adjudication-max-candidates": "NOVEL_FORGE_TTS_VOICE_LLM_ADJUDICATION_MAX_CANDIDATES",
    "tts-voice-llm-adjudication-choices-per-character": "NOVEL_FORGE_TTS_VOICE_LLM_ADJUDICATION_CHOICES_PER_CHARACTER",
    "tts-voice-llm-adjudication-auto-select-score": "NOVEL_FORGE_TTS_VOICE_LLM_ADJUDICATION_AUTO_SELECT_SCORE",
    "tts-voice-llm-adjudication-max-output-tokens": "NOVEL_FORGE_TTS_VOICE_LLM_ADJUDICATION_MAX_OUTPUT_TOKENS",
    "tts-script-generation-temperature": "NOVEL_FORGE_TTS_SCRIPT_GENERATION_TEMPERATURE",
    "tts-review-adjudication-temperature": "NOVEL_FORGE_TTS_REVIEW_ADJUDICATION_TEMPERATURE",
    "tts-narrator-profile-temperature": "NOVEL_FORGE_TTS_NARRATOR_PROFILE_TEMPERATURE",
    "tts-sound-design-temperature": "NOVEL_FORGE_TTS_SOUND_DESIGN_TEMPERATURE",
    "init-coh-use-memory": "NOVEL_FORGE_INIT_COHERENCE_USE_MEMORY",
    "init-coh-use-blueprint": "NOVEL_FORGE_INIT_COHERENCE_USE_BLUEPRINT",
    "init-coh-cross-check": "NOVEL_FORGE_INIT_COHERENCE_CROSS_CHECK",
    "init-coh-strict-mode": "NOVEL_FORGE_INIT_COHERENCE_STRICT_MODE",
    "research-enabled": "NOVEL_FORGE_RESEARCH_ENABLED",
    "research-depth": "NOVEL_FORGE_RESEARCH_DEPTH",
}

_PARAM_SETTINGS_ATTR: dict[str, str] = {
    "short-max-edit": "short_max_edit_rounds",
    "outline-batch": "outline_batch_size",
    "alignment-threshold": "long_alignment_threshold",
    "continuity-threshold": "long_continuity_threshold",
    "causal-threshold": "long_causal_threshold",
    "reading-power-threshold": "long_reading_power_threshold",
    "guard-mode": "long_plot_guard_mode",
    "repair-rounds": "long_total_repair_rounds_cap",
    "reading-repair": "long_reading_power_repair_enabled",
    "polish-enabled": "long_polish_enabled",
    "polish-threshold": "long_polish_auto_trigger_threshold",
    "humanize-enabled": "humanize_enabled",
    "humanize-intensity": "humanize_intensity",
    "tts-enabled": "tts_enabled",
    "tts-provider": "tts_default_provider",
    "tts-model": "tts_default_model",
    "tts-speed": "tts_default_speed",
    "tts-output-format": "tts_output_format",
    "tts-automation-mode": "tts_automation_mode",
    "tts-concurrency": "tts_max_concurrent_synthesis",
    "tts-voice-library-scope": "tts_voice_library_scope",
    "tts-auto-trigger-after-chapter": "tts_auto_trigger_after_chapter",
    "tts-post-archive-retry-enabled": "tts_post_archive_retry_enabled",
    "tts-post-archive-retry-delay-s": "tts_post_archive_retry_delay_s",
    "tts-voice-design-enabled": "tts_voice_design_enabled",
    "tts-voice-semantic-matching-enabled": "tts_voice_semantic_matching_enabled",
    "tts-voice-semantic-top-k": "tts_voice_semantic_top_k",
    "tts-monthly-cost-budget-usd": "tts_monthly_cost_budget_usd",
    "tts-book-cost-budget-usd": "tts_book_cost_budget_usd",
    "tts-project-max-storage-mb": "tts_project_max_storage_mb",
    "audio-budget-limit-usd": "audio_budget_limit_usd",
    "tts-minimax-base-url": "tts_minimax_base_url",
    "tts-minimax-group-id": "tts_minimax_group_id",
    "tts-minimax-bitrate": "tts_minimax_bitrate",
    "tts-minimax-channel": "tts_minimax_channel",
    "tts-minimax-language-boost": "tts_minimax_language_boost",
    "tts-minimax-english-normalization": "tts_minimax_english_normalization",
    "tts-minimax-continuous-sound": "tts_minimax_continuous_sound_default",
    "tts-minimax-async-timeout-s": "tts_minimax_async_timeout_s",
    "tts-aigc-watermark": "tts_aigc_watermark_default",
    "tts-dashscope-base-url": "tts_dashscope_base_url",
    "tts-dashscope-model": "tts_dashscope_model",
    "tts-dashscope-preview-model": "tts_dashscope_preview_model",
    "tts-dashscope-voice-clone-model": "tts_dashscope_voice_clone_model",
    "tts-dashscope-voice-design-model": "tts_dashscope_voice_design_model",
    "tts-dashscope-optimize-instructions": "tts_dashscope_optimize_instructions",
    "tts-dashscope-voice-clone-enable-preprocess": ("tts_dashscope_voice_clone_enable_preprocess"),
    "tts-dashscope-cny-per-usd": "tts_dashscope_cny_per_usd",
    "tts-tencent-project-id": "tts_tencent_project_id",
    "tts-tencent-voice-type": "tts_tencent_voice_type",
    "tts-tencent-primary-language": "tts_tencent_primary_language",
    "tts-tencent-emotion-intensity": "tts_tencent_emotion_intensity",
    "tts-tencent-segment-rate": "tts_tencent_segment_rate",
    "tts-volcengine-base-url": "tts_volcengine_base_url",
    "tts-volcengine-model": "tts_volcengine_model",
    "tts-volcengine-resource-id": "tts_volcengine_resource_id",
    "tts-mimo-base-url": "tts_mimo_base_url",
    "tts-mimo-model": "tts_mimo_model",
    "tts-local-base-url": "tts_local_base_url",
    "tts-local-model": "tts_local_model",
    "tts-qwen3-base-url": "tts_qwen3_base_url",
    "tts-qwen3-formal-model": "tts_qwen3_formal_model",
    "tts-qwen3-preview-model": "tts_qwen3_preview_model",
    "tts-qwen3-design-model": "tts_qwen3_design_model",
    "tts-qwen3-clone-model": "tts_qwen3_clone_model",
    "tts-cosyvoice-base-url": "tts_cosyvoice_base_url",
    "tts-cosyvoice-model": "tts_cosyvoice_model",
    "tts-cosyvoice-mode": "tts_cosyvoice_mode",
    "tts-cosyvoice-sample-rate": "tts_cosyvoice_sample_rate",
    "tts-openvoice-model": "tts_openvoice_model",
    "tts-openvoice-checkpoint-dir": "tts_openvoice_checkpoint_dir",
    "tts-openvoice-device": "tts_openvoice_device",
    "tts-openvoice-language": "tts_openvoice_language",
    "tts-background-pipeline-concurrency": "tts_background_pipeline_concurrency",
    "tts-script-max-concurrent-batches": "tts_script_max_concurrent_batches",
    "tts-synthesis-rpm": "tts_synthesis_requests_per_minute",
    "tts-rate-limit-cooldown-s": "tts_synthesis_rate_limit_cooldown_s",
    "tts-synthesis-retry-limit": "tts_synthesis_retry_limit",
    "tts-sample-rate": "tts_sample_rate",
    "tts-voice-clone-ttl-days": "tts_voice_clone_ttl_days",
    "tts-parallel-voice-clone": "tts_parallel_voice_clone",
    "tts-narrator-voice-id": "tts_narrator_voice_id",
    "sound-generation-enabled": "sound_generation_enabled",
    "sound-generation-auto-generate": "sound_generation_auto_generate",
    "sound-generation-auto-approve": "sound_generation_auto_approve",
    "tts-sound-design-enabled": "tts_sound_design_enabled",
    "tts-script-llm-review-enabled": "tts_script_llm_review_enabled",
    "tts-script-llm-review-max-output-tokens": "tts_script_llm_review_max_output_tokens",
    "tts-voice-llm-adjudication-enabled": "tts_voice_llm_adjudication_enabled",
    "tts-voice-llm-adjudication-min-match-score": "tts_voice_llm_adjudication_min_match_score",
    "tts-voice-llm-adjudication-max-candidates": "tts_voice_llm_adjudication_max_candidates",
    "tts-voice-llm-adjudication-choices-per-character": "tts_voice_llm_adjudication_choices_per_character",
    "tts-voice-llm-adjudication-auto-select-score": "tts_voice_llm_adjudication_auto_select_score",
    "tts-voice-llm-adjudication-max-output-tokens": "tts_voice_llm_adjudication_max_output_tokens",
    "tts-script-generation-temperature": "tts_script_generation_temperature",
    "tts-review-adjudication-temperature": "tts_review_adjudication_temperature",
    "tts-narrator-profile-temperature": "tts_narrator_profile_temperature",
    "tts-sound-design-temperature": "tts_sound_design_temperature",
    "audio-quality-preset": "audio_quality_preset",
    "audio-location-policy": "audio_location_policy",
    "audio-accelerator-preference": "audio_accelerator_preference",
    "local-model-resource-budget": "local_model_resource_budget",
    "local-model-resource-wait-timeout-s": "local_model_resource_wait_timeout_s",
    "audio-memory-budget": "audio_memory_budget",
    "audio-plugin-overrides": "audio_plugin_overrides",
    "audio-dual-alignment-validation": "audio_dual_alignment_validation",
    "audio-qwen3-asr-base-url": "audio_qwen3_asr_base_url",
    "audio-whisperx-base-url": "audio_whisperx_base_url",
    "audio-sherpa-base-url": "audio_sherpa_base_url",
    "audio-mfa-command": "audio_mfa_command",
    "audio-plugin-manifest-dirs": "audio_plugin_manifest_dirs",
    "tts-alignment-repair-rounds": "tts_alignment_repair_rounds",
    "tts-audio-quality-tier": "tts_audio_quality_tier",
    "tts-max-text-error-rate": "tts_max_text_error_rate",
    "tts-master-quality-gate-blocking": "tts_master_quality_gate_blocking",
    "tts-subtitle-word-level": "tts_subtitle_word_level",
    "tts-minimax-force-cbr": "tts_minimax_force_cbr",
    "init-coh-use-memory": "init_coherence_use_memory",
    "init-coh-use-blueprint": "init_coherence_use_blueprint",
    "init-coh-cross-check": "init_coherence_cross_check",
    "init-coh-strict-mode": "init_coherence_strict_mode",
    "research-enabled": "research_enabled",
    "research-depth": "research_depth",
}

# Fire-tune creation parameters beyond the TTS/audio base allowlist (aligned
# with the legacy PySide collect_param_env_pairs surface).  Must run before
# any request handler reads the maps.
apply_param_map_extensions(_PARAM_ENV_MAP, _PARAM_SETTINGS_ATTR)


def get_creation_parameters(settings: Settings) -> dict[str, str]:
    """Read current parameter values from Settings for the frontend form."""
    result: dict[str, str] = {}
    for field_id, attr_name in _PARAM_SETTINGS_ATTR.items():
        val = getattr(settings, attr_name, None)
        if val is not None:
            if field_id == "local-check-mode":
                result[field_id] = "prescreen" if bool(val) else "off"
            else:
                result[field_id] = str(val).lower() if isinstance(val, bool) else str(val)
    return result


def validate_creation_parameters(
    params: dict[str, str],
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Validate frontend parameter strings against their real Settings fields."""

    accepted: dict[str, str] = {}
    rejected: list[dict[str, str]] = []
    for field_id, raw_value in params.items():
        if field_id not in _PARAM_ENV_MAP:
            rejected.append(
                {"route_id": f"__setting__:{field_id}", "reason": "unknown setting"}
            )
            continue
        value = str(raw_value).strip()
        if field_id == "local-check-mode":
            value = {"prescreen": "true", "off": "false"}.get(value, value)
        if len(value) > 20_000 or "\n" in value or "\r" in value:
            rejected.append(
                {"route_id": f"__setting__:{field_id}", "reason": "invalid setting value"}
            )
            continue

        attr_name = _PARAM_SETTINGS_ATTR.get(field_id)
        if not value:
            # Credential fields are write-only: an empty UI value means
            # preserve the existing secret, not erase it.  Empty ordinary
            # fields clear their explicit env override and fall back to Settings.
            if attr_name is not None:
                accepted[field_id] = ""
            continue
        if attr_name is None:
            accepted[field_id] = value
            continue

        model_field = Settings.model_fields[attr_name]
        annotated_type = (
            Annotated[model_field.annotation, *model_field.metadata]
            if model_field.metadata
            else model_field.annotation
        )
        try:
            TypeAdapter(annotated_type).validate_python(value)
        except ValidationError:
            rejected.append(
                {
                    "route_id": f"__setting__:{field_id}",
                    "reason": "value is outside the Settings contract",
                }
            )
            continue
        accepted[field_id] = value
    return accepted, rejected


def persist_settings_environment(
    *,
    creation_parameters: dict[str, str] | None,
    creative_temperature: dict[str, Any] | None,
    routes: dict[str, SettingsRouteTemperatureCommand] | None,
    theme_id: str | None,
    font_preferences: dict[str, Any] | None,
) -> None:
    """Persist only known, credential-free settings using PySide's env names."""

    pairs: dict[str, str] = {}
    keys_to_clear: set[str] = set()
    if creation_parameters:
        pairs.update(creation_parameter_env_pairs(creation_parameters))
        keys_to_clear.update(creation_parameter_env_keys_to_clear(creation_parameters))
    if creative_temperature is not None:
        creative_pairs = creative_temperature_env_pairs(creative_temperature)
        pairs.update({key: value for key, value in creative_pairs.items() if value.strip()})
        keys_to_clear.update(key for key, value in creative_pairs.items() if not value.strip())
    if routes:
        pairs.update(route_temperature_env_pairs(routes))
        keys_to_clear.update(route_temperature_env_keys_to_clear(routes))
    if theme_id is not None and theme_id.strip():
        pairs["NOVEL_FORGE_DESKTOP_THEME"] = theme_id.strip()
    if font_preferences is not None:
        ui_family = str(font_preferences.get("ui_family", "") or "").strip()
        reading_family = str(font_preferences.get("reading_family", "") or "").strip()
        try:
            scale = float(font_preferences.get("scale", 1.0))
        except (TypeError, ValueError):
            scale = 1.0
        if ui_family in {"source_sans", "system_sans", "literary_serif"}:
            pairs["NOVEL_FORGE_DESKTOP_UI_FONT_FAMILY"] = ui_family
        if reading_family in {"source_serif", "ui_sans", "calligraphy"}:
            pairs["NOVEL_FORGE_DESKTOP_READING_FONT_FAMILY"] = reading_family
        pairs["NOVEL_FORGE_DESKTOP_FONT_SCALE"] = str(min(1.2, max(0.9, scale)))
    if pairs:
        persist_env_pairs(pairs)
    keys_to_clear.difference_update(pairs)
    if keys_to_clear:
        clear_env_keys(keys_to_clear)


def creation_parameter_env_pairs(params: dict[str, str]) -> dict[str, str]:
    return {
        env_key: value.strip()
        for field_id, value in params.items()
        if (env_key := _PARAM_ENV_MAP.get(field_id)) is not None and value.strip()
    }


def creation_parameter_env_keys_to_clear(params: dict[str, str]) -> set[str]:
    """Return ordinary setting overrides explicitly cleared by the user."""

    return {
        env_key
        for field_id, value in params.items()
        if field_id in _PARAM_SETTINGS_ATTR
        and (env_key := _PARAM_ENV_MAP.get(field_id)) is not None
        and not value.strip()
    }


def creative_temperature_env_pairs(value: dict[str, Any]) -> dict[str, str]:
    """Map the React creative-temperature command to the native env contract."""

    scope = str(value.get("scope", "recommended") or "recommended").strip().lower()
    if scope not in {"recommended", "chapter_core", "init_and_chapter", "custom"}:
        scope = "recommended"
    try:
        down_delta = float(
            value.get("down_delta", value.get("lower_delta", value.get("top_p", 0.3)))
        )
        up_delta = float(
            value.get("up_delta", value.get("upper_delta", value.get("temperature", 0.1)))
        )
    except (TypeError, ValueError):
        return {}
    if not 0.0 <= down_delta <= 2.0 or not 0.0 <= up_delta <= 2.0:
        return {}
    custom_task_keys = value.get("custom_task_keys", ())
    if not isinstance(custom_task_keys, list):
        custom_task_keys = []
    from novel_forge.core.parsing.temperature_jitter import (
        is_temperature_jitter_protected,
        parse_temperature_task_keys,
        serialize_temperature_task_keys,
    )

    selected = tuple(
        task
        for task in parse_temperature_task_keys(custom_task_keys)
        if not is_temperature_jitter_protected(task)
    )
    return {
        "NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_ENABLED": str(
            bool(value.get("enabled", True))
        ).lower(),
        "NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_SCOPE": scope,
        "NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_DOWN_DELTA": str(down_delta),
        "NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_UP_DELTA": str(up_delta),
        "NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_CUSTOM_TASKS": serialize_temperature_task_keys(
            selected
        ),
    }


def route_temperature_env_pairs(
    routes: dict[str, SettingsRouteTemperatureCommand],
) -> dict[str, str]:
    """Persist task temperatures by their shared Settings attribute names."""

    from novel_forge.core.task_catalog import LONG_TEMPERATURE_TASKS, SHORT_TEMPERATURE_TASKS

    temperature_tasks = {
        task.key: task for task in (*SHORT_TEMPERATURE_TASKS, *LONG_TEMPERATURE_TASKS)
    }
    pairs: dict[str, str] = {}
    for task_key, command in routes.items():
        task = temperature_tasks.get(task_key)
        if task is None or command.temperature is None:
            continue
        pairs[f"NOVEL_FORGE_{task.setting_attr.upper()}"] = str(command.temperature)
    return pairs


def route_temperature_env_keys_to_clear(
    routes: dict[str, SettingsRouteTemperatureCommand],
) -> set[str]:
    """Clear task temperature overrides when a route returns to its default."""

    from novel_forge.core.task_catalog import LONG_TEMPERATURE_TASKS, SHORT_TEMPERATURE_TASKS

    temperature_tasks = {
        task.key: task for task in (*SHORT_TEMPERATURE_TASKS, *LONG_TEMPERATURE_TASKS)
    }
    return {
        f"NOVEL_FORGE_{task.setting_attr.upper()}"
        for task_key, command in routes.items()
        if (task := temperature_tasks.get(task_key)) is not None
        and command.temperature is None
    }


def persist_env_pairs(pairs: dict[str, str], *, env_path: Path | None = None) -> None:
    """Atomically merge trusted setting values into the desktop's writable .env."""

    import os
    import tempfile

    from novel_forge.core.config import get_writable_env_path

    target = get_writable_env_path() if env_path is None else env_path
    target.parent.mkdir(parents=True, exist_ok=True)
    existing_lines: list[str] = []
    if target.exists():
        existing_lines = target.read_text(encoding="utf-8").splitlines()

    # Build map of existing env keys
    env_map: dict[str, int] = {}  # key -> line index
    for i, line in enumerate(existing_lines):
        if "=" in line and not line.strip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            env_map[key] = i

    # Update or append
    for env_key, value in pairs.items():
        normalized = str(value).strip()
        if not env_key or not normalized or "\n" in normalized or "\r" in normalized:
            continue
        new_line = f"{env_key}={normalized}"
        if env_key in env_map:
            existing_lines[env_map[env_key]] = new_line
        else:
            existing_lines.append(new_line)
            env_map[env_key] = len(existing_lines) - 1

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write("\n".join(existing_lines) + "\n")
        temporary_path = handle.name
    os.replace(temporary_path, target)


def clear_env_keys(keys: set[str], *, env_path: Path | None = None) -> None:
    """Remove explicit allowlisted settings without accepting arbitrary input maps."""

    import os
    import tempfile

    from novel_forge.core.config import get_writable_env_path

    wanted = {str(key).strip() for key in keys if str(key).strip()}
    if not wanted:
        return
    target = get_writable_env_path() if env_path is None else env_path
    if not target.exists():
        return
    original = target.read_text(encoding="utf-8").splitlines()
    retained = [
        line
        for line in original
        if not ("=" in line and line.split("=", 1)[0].strip() in wanted)
    ]
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write("\n".join(retained) + "\n")
        temporary_path = handle.name
    os.replace(temporary_path, target)


def persist_creation_parameters(params: dict[str, str]) -> None:
    """Backward-compatible entry point for creation-parameter-only callers."""

    persist_env_pairs(creation_parameter_env_pairs(params))

__all__ = [
    "SettingsRouteTemperatureCommand",
    "_PARAM_ENV_MAP",
    "_PARAM_SETTINGS_ATTR",
    "creation_parameter_env_keys_to_clear",
    "creation_parameter_env_pairs",
    "clear_env_keys",
    "creative_temperature_env_pairs",
    "get_creation_parameters",
    "persist_creation_parameters",
    "persist_env_pairs",
    "persist_settings_environment",
    "route_temperature_env_pairs",
    "route_temperature_env_keys_to_clear",
    "validate_creation_parameters",
]
