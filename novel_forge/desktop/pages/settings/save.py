"""Save-related functionality for the Settings page."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from novel_forge.core.config import get_settings
from novel_forge.desktop.components.dialogs import show_info_message, show_warning_message
from novel_forge.desktop.pages.settings.specs import (
    CREATIVE_TEMPERATURE_SETTING_GROUP,
    collect_setting_spec_env_pairs,
)
from novel_forge.desktop.pages.settings_page_parameters import (
    collect_temperature_jitter_custom_tasks,
)
from novel_forge.gateway.embedding_config import is_embedding_model
from novel_forge.gateway.profiles import (
    _ENV_KEY_MAP,
    _PROFILE_API_KEY_PREFIX,
    TaskRouteEntry,
    invalidate_profiles_cache,
)

if TYPE_CHECKING:
    pass


def _spin_value(widgets: dict[str, Any], key: str) -> int:
    """Safely get a spin box value, returning 0 if widget doesn't exist."""
    w = widgets.get(key)
    if w is None:
        return 0
    try:
        return int(w.value())
    except AttributeError:
        return 0


def _combo_text(widgets: dict[str, Any], key: str) -> str:
    """Safely get a combo box value, returning empty string if missing.

    Settings combo boxes may show localized labels while storing the runtime
    value in ``currentData()``. Persist the runtime value whenever it exists.
    """
    w = widgets.get(key)
    if w is None:
        return ""
    try:
        data = w.currentData()
        if data is not None:
            if isinstance(data, bool):
                return "true" if data else "false"
            return str(data)
        return str(w.currentText())
    except AttributeError:
        return ""


def _bool_text(widgets: dict[str, Any], key: str) -> str:
    """Safely get a boolean widget value as 'true'/'false'."""
    w = widgets.get(key)
    if w is None:
        return "false"
    try:
        return "true" if bool(w.isChecked()) else "false"
    except AttributeError:
        text = _combo_text(widgets, key).strip().lower()
        if text in {"true", "false"}:
            return text
        return "false"


def _combo_data(widgets: dict[str, Any], key: str) -> Any:
    """Safely get a combo box current data, returning None if missing."""
    w = widgets.get(key)
    if w is None:
        return None
    try:
        return w.currentData()
    except AttributeError:
        return None


def _line_text(widgets: dict[str, Any], key: str) -> str:
    """Safely get a line edit text, returning empty string if missing."""
    w = widgets.get(key)
    if w is None:
        return ""
    try:
        return str(w.text()).strip()
    except AttributeError:
        return ""


def _float_value(widgets: dict[str, Any], key: str) -> float:
    """Safely get a double spin box value, returning 0.0 if missing."""
    w = widgets.get(key)
    if w is None:
        return 0.0
    try:
        return float(w.value())
    except AttributeError:
        return 0.0


def _temperature_env_name(task_key: str, spin: Any) -> str:
    """Return the env key for a route temperature spin."""
    setting_attr = ""
    try:
        setting_attr = str(spin.property("setting_attr") or "").strip()
    except AttributeError:
        setting_attr = ""
    if setting_attr:
        return f"NOVEL_FORGE_{setting_attr.upper()}"
    return f"NOVEL_FORGE_TEMP_{task_key.upper()}"


def _temperature_spin_changed(page: Any, spin: Any) -> bool:
    try:
        setting_attr = str(spin.property("setting_attr") or "").strip()
    except AttributeError:
        setting_attr = ""
    if not setting_attr:
        return True
    try:
        current = float(getattr(page._settings, setting_attr))
        return abs(float(spin.value()) - current) > 0.0001
    except Exception:
        return True


def _record_temperature_pair(
    page: Any,
    pairs: dict[str, str],
    *,
    task_key: str,
    spin: Any,
) -> None:
    env_name = _temperature_env_name(task_key, spin)
    value = str(spin.value())
    if env_name not in pairs or _temperature_spin_changed(page, spin):
        pairs[env_name] = value


def collect_param_env_pairs(page: Any) -> dict[str, str]:
    """Collect non-routing parameter values from UI widgets."""
    w = page._param_widgets
    pairs: dict[str, str] = {}

    pairs["NOVEL_FORGE_SHORT_MAX_EDIT_ROUNDS"] = str(_spin_value(w, "_short_max_edit"))

    for key, spin in w.get("_short_temp_spins", {}).items():
        try:
            _record_temperature_pair(page, pairs, task_key=key, spin=spin)
        except AttributeError:
            pass

    pairs["NOVEL_FORGE_LONG_MAX_EDIT_ROUNDS"] = str(_spin_value(w, "_long_max_edit"))
    pairs.update(collect_setting_spec_env_pairs(w, CREATIVE_TEMPERATURE_SETTING_GROUP))
    pairs["NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_CUSTOM_TASKS"] = (
        collect_temperature_jitter_custom_tasks(w)
    )
    pairs["NOVEL_FORGE_DESKTOP_THEME"] = _combo_text(w, "_desktop_theme")
    pairs["NOVEL_FORGE_DESKTOP_UI_FONT_FAMILY"] = _combo_text(w, "_desktop_ui_font_family")
    pairs["NOVEL_FORGE_DESKTOP_READING_FONT_FAMILY"] = _combo_text(
        w, "_desktop_reading_font_family"
    )
    pairs["NOVEL_FORGE_DESKTOP_FONT_SCALE"] = _combo_text(w, "_desktop_font_scale")
    pairs["NOVEL_FORGE_DESKTOP_PET_VISIBLE"] = _combo_text(w, "_desktop_pet_visible")
    pairs["NOVEL_FORGE_DESKTOP_NOTIFICATION_SOUND_ENABLED"] = _combo_text(
        w, "_desktop_notify_sound_enabled"
    )
    pairs["NOVEL_FORGE_DESKTOP_NOTIFICATION_SUCCESS_SOUND"] = _combo_text(
        w, "_desktop_notify_success_sound"
    )
    pairs["NOVEL_FORGE_DESKTOP_NOTIFICATION_DECISION_SOUND"] = _combo_text(
        w, "_desktop_notify_decision_sound"
    )
    pairs["NOVEL_FORGE_DESKTOP_NOTIFICATION_FAILURE_SOUND"] = _combo_text(
        w, "_desktop_notify_failure_sound"
    )
    pairs["NOVEL_FORGE_RESEARCH_ENABLED"] = (
        _bool_text(w, "_research_enabled") if "_research_enabled" in w else "false"
    )
    pairs["NOVEL_FORGE_CHAPTER_INTENT_GUARD_MODE"] = (
        _combo_text(w, "_chapter_intent_guard_mode") or "warn"
    )
    pairs["NOVEL_FORGE_CHAPTER_RESEARCH_REFRESH_ENABLED"] = _bool_text(
        w, "_chapter_research_refresh_enabled"
    )
    pairs["NOVEL_FORGE_CHAPTER_RESEARCH_INSPIRATION_ENABLED"] = _bool_text(
        w, "_chapter_research_inspiration_enabled"
    )
    pairs["NOVEL_FORGE_CHAPTER_RESEARCH_INSPIRATION_COOLDOWN"] = str(
        _spin_value(w, "_chapter_research_inspiration_cooldown") or 3
    )
    pairs["NOVEL_FORGE_SHORT_ADAPTIVE_REVISION_ENABLED"] = _bool_text(
        w, "_short_adaptive_revision_enabled"
    )
    pairs["NOVEL_FORGE_LONG_SINGLE_FINAL_VERIFY_ENABLED"] = _bool_text(
        w, "_long_single_final_verify_enabled"
    )
    pairs["NOVEL_FORGE_RESEARCH_DEFAULT_PROVIDER"] = (
        _combo_text(w, "_research_default_provider") or "auto"
    )
    pairs["NOVEL_FORGE_RESEARCH_HTTP_ENDPOINT"] = _line_text(w, "_research_http_endpoint")
    pairs["NOVEL_FORGE_RESEARCH_API_KEY"] = _line_text(w, "_research_api_key")
    pairs["NOVEL_FORGE_RESEARCH_TIMEOUT_S"] = str(_float_value(w, "_research_timeout_s") or 10.0)
    pairs["NOVEL_FORGE_RESEARCH_MAX_QUERIES"] = str(_spin_value(w, "_research_max_queries") or 3)
    pairs["NOVEL_FORGE_RESEARCH_QUERY_MAX_PARALLEL"] = str(
        _spin_value(w, "_research_query_max_parallel")
        if "_research_query_max_parallel" in w
        else 2
    )
    pairs["NOVEL_FORGE_RESEARCH_RESULTS_PER_QUERY"] = str(
        _spin_value(w, "_research_results_per_query") or 5
    )
    pairs["NOVEL_FORGE_RESEARCH_MAX_RESULTS"] = str(_spin_value(w, "_research_max_results") or 5)
    pairs["NOVEL_FORGE_RESEARCH_RETRY_ATTEMPTS"] = str(
        _spin_value(w, "_research_retry_attempts") if "_research_retry_attempts" in w else 1
    )
    pairs["NOVEL_FORGE_RESEARCH_INCLUDE_DOMAINS"] = _line_text(w, "_research_include_domains")
    pairs["NOVEL_FORGE_RESEARCH_EXCLUDE_DOMAINS"] = _line_text(w, "_research_exclude_domains")
    pairs["NOVEL_FORGE_RESEARCH_LOCALE"] = _line_text(w, "_research_locale")
    pairs["NOVEL_FORGE_RESEARCH_SEARCH_DEPTH"] = _combo_text(w, "_research_search_depth") or "basic"
    pairs["NOVEL_FORGE_RESEARCH_USE_LLM_PLANNING"] = (
        _bool_text(w, "_research_use_llm_planning") if "_research_use_llm_planning" in w else "true"
    )
    pairs["NOVEL_FORGE_RESEARCH_MODEL_PRIOR_ENABLED"] = (
        _bool_text(w, "_research_model_prior_enabled")
        if "_research_model_prior_enabled" in w
        else "false"
    )
    pairs["NOVEL_FORGE_RESEARCH_DOSSIER_ENABLED"] = (
        _bool_text(w, "_research_dossier_enabled") if "_research_dossier_enabled" in w else "true"
    )
    pairs["NOVEL_FORGE_RESEARCH_DOSSIER_MAX_SOURCES"] = str(
        _spin_value(w, "_research_dossier_max_sources") or 8
    )
    pairs["NOVEL_FORGE_OUTLINE_RESEARCH_GROUNDING_ENABLED"] = (
        _bool_text(w, "_outline_research_grounding_enabled")
        if "_outline_research_grounding_enabled" in w
        else "true"
    )
    pairs["NOVEL_FORGE_OUTLINE_RESEARCH_GROUNDING_NOTES_PER_CHAPTER"] = str(
        _spin_value(w, "_outline_research_grounding_notes_per_chapter")
    )
    pairs["NOVEL_FORGE_RESEARCH_MCP_COMMAND"] = _line_text(w, "_research_mcp_command")
    pairs["NOVEL_FORGE_RESEARCH_MCP_ARGS_JSON"] = _line_text(w, "_research_mcp_args_json")
    pairs["NOVEL_FORGE_RESEARCH_MCP_ENV_JSON"] = _line_text(w, "_research_mcp_env_json")
    pairs["NOVEL_FORGE_RESEARCH_MCP_API_KEY_ENV"] = (
        _line_text(w, "_research_mcp_api_key_env") or "MINIMAX_API_KEY"
    )
    pairs["NOVEL_FORGE_RESEARCH_MCP_PROTOCOL_VERSION"] = (
        _line_text(w, "_research_mcp_protocol_version") or "2024-11-05"
    )
    pairs["NOVEL_FORGE_RESEARCH_MCP_STDIO_FRAMING"] = (
        _combo_text(w, "_research_mcp_stdio_framing") or "newline"
    )
    pairs["NOVEL_FORGE_RESEARCH_MCP_INHERIT_ENVIRONMENT"] = (
        _bool_text(w, "_research_mcp_inherit_environment")
        if "_research_mcp_inherit_environment" in w
        else "false"
    )
    pairs["NOVEL_FORGE_RESEARCH_MCP_TOOL_NAME"] = _line_text(w, "_research_mcp_tool_name")
    pairs["NOVEL_FORGE_RESEARCH_MCP_QUERY_ARGUMENT"] = (
        _line_text(w, "_research_mcp_query_argument") or "query"
    )
    pairs["NOVEL_FORGE_RESEARCH_MCP_TOOL_ARGUMENTS_JSON"] = _line_text(
        w, "_research_mcp_tool_arguments_json"
    )
    pairs["NOVEL_FORGE_LONG_ALIGNMENT_THRESHOLD"] = str(_float_value(w, "_long_align_threshold"))
    pairs["NOVEL_FORGE_LONG_PLOT_GUARD_MODE"] = _combo_text(w, "_long_guard_mode")
    pairs["NOVEL_FORGE_LONG_VOLUME_AUTO_CHAPTER_THRESHOLD"] = str(
        _spin_value(w, "_long_vol_auto_ch")
    )
    pairs["NOVEL_FORGE_LONG_VOLUME_AUTO_WORD_THRESHOLD"] = str(
        _spin_value(w, "_long_vol_auto_word")
    )
    pairs["NOVEL_FORGE_LONG_DEFAULT_CHAPTERS_PER_VOLUME"] = str(_spin_value(w, "_long_default_cpv"))
    pairs["NOVEL_FORGE_SPLIT_TASKS_ENABLED"] = _combo_text(w, "_split_tasks_enabled")
    pairs["NOVEL_FORGE_INIT_FRAGMENT_MAX_PARALLEL"] = str(
        _spin_value(w, "_init_fragment_max_parallel")
    )
    pairs["NOVEL_FORGE_INIT_CHARACTER_PROFILE_PARALLEL_MIN_ROSTER"] = str(
        _spin_value(w, "_init_character_profile_parallel_min_roster")
    )
    pairs["NOVEL_FORGE_INIT_CHARACTER_PROFILE_BATCH_SIZE"] = str(
        _spin_value(w, "_init_character_profile_batch_size")
    )
    pairs["NOVEL_FORGE_INIT_KB_PHASE_C_PARALLEL"] = (
        _combo_text(w, "_init_kb_phase_c_parallel")
        if "_init_kb_phase_c_parallel" in w
        else "true"
    )
    pairs["NOVEL_FORGE_INIT_ENTITY_REFERENCE_MAX_PARALLEL"] = str(
        _spin_value(w, "_init_entity_reference_max_parallel")
        if "_init_entity_reference_max_parallel" in w
        else 3
    )
    pairs["NOVEL_FORGE_TEMP_INIT_STORY_BIBLE"] = str(
        _float_value(w, "_temp_init_story_bible") if "_temp_init_story_bible" in w else 0.7
    )
    pairs["NOVEL_FORGE_TEMP_INIT_CHARACTER_BIBLE"] = str(
        _float_value(w, "_temp_init_character_bible")
        if "_temp_init_character_bible" in w
        else 0.7
    )
    pairs["NOVEL_FORGE_TEMP_PLAN_OUTLINE_BATCH"] = str(
        _float_value(w, "_temp_plan_outline_batch") if "_temp_plan_outline_batch" in w else 0.7
    )
    pairs["NOVEL_FORGE_TEMP_PLAN_CHAPTER_CONTRACTS"] = str(
        _float_value(w, "_temp_plan_chapter_contracts")
        if "_temp_plan_chapter_contracts" in w
        else 0.25
    )
    pairs["NOVEL_FORGE_TEMP_INIT_ENTITY_REGISTRY"] = str(
        _float_value(w, "_temp_init_entity_registry")
        if "_temp_init_entity_registry" in w
        else 0.2
    )
    pairs["NOVEL_FORGE_TEMP_INIT_KNOWLEDGE_BOUNDARIES"] = str(
        _float_value(w, "_temp_init_knowledge_boundaries")
        if "_temp_init_knowledge_boundaries" in w
        else 0.3
    )
    pairs["NOVEL_FORGE_OUTLINE_BATCH_SIZE"] = str(_spin_value(w, "_outline_batch_size"))
    pairs["NOVEL_FORGE_INIT_OUTLINE_BEATS_MIN"] = str(_spin_value(w, "_init_outline_beats_min"))
    pairs["NOVEL_FORGE_INIT_OUTLINE_BEATS_MAX"] = str(_spin_value(w, "_init_outline_beats_max"))
    pairs["NOVEL_FORGE_INIT_OUTLINE_MAIN_PLOT_POINTS_MIN"] = str(
        _spin_value(w, "_init_outline_main_points_min")
    )
    pairs["NOVEL_FORGE_INIT_OUTLINE_MAIN_PLOT_POINTS_MAX"] = str(
        _spin_value(w, "_init_outline_main_points_max")
    )
    pairs["NOVEL_FORGE_INIT_OUTLINE_SUBPLOT_POINTS_MAX"] = str(
        _spin_value(w, "_init_outline_subplot_points_max")
    )
    pairs["NOVEL_FORGE_INIT_OUTLINE_ELEMENT_FOCUS_MAX"] = str(
        _spin_value(w, "_init_outline_element_focus_max")
    )
    pairs["NOVEL_FORGE_INIT_OUTLINE_EXPECTED_PAYOFFS_MIN"] = str(
        _spin_value(w, "_init_outline_payoffs_min")
    )
    pairs["NOVEL_FORGE_INIT_OUTLINE_EXPECTED_PAYOFFS_MAX"] = str(
        _spin_value(w, "_init_outline_payoffs_max")
    )
    pairs["NOVEL_FORGE_WORLD_RULE_COUNT_MIN"] = str(_spin_value(w, "_world_rule_count_min"))
    pairs["NOVEL_FORGE_WORLD_RULE_COUNT_MAX"] = str(_spin_value(w, "_world_rule_count_max"))
    pairs["NOVEL_FORGE_WORLD_RULE_HARD_MIN"] = str(_spin_value(w, "_world_rule_hard_min"))
    pairs["NOVEL_FORGE_WORLD_RULE_ALWAYS_ON_HARD_CAP"] = str(
        _spin_value(w, "_world_rule_always_on_cap")
    )
    pairs["NOVEL_FORGE_WORLD_RULE_CATEGORY_MIN"] = str(_spin_value(w, "_world_rule_category_min"))
    pairs["NOVEL_FORGE_WORLD_RULE_ABILITY_REQUIRED"] = _combo_text(
        w, "_world_rule_ability_required"
    )
    pairs["NOVEL_FORGE_WORLD_RULE_BLOCK_ON_VIOLATION"] = _combo_text(
        w, "_world_rule_block_on_violation"
    )
    pairs["NOVEL_FORGE_CHAPTER_CONTRACT_BATCH_SIZE"] = str(
        _spin_value(w, "_chapter_contract_batch_size")
    )
    pairs["NOVEL_FORGE_OUTLINE_THINKING"] = _combo_text(w, "_outline_thinking")
    pairs["NOVEL_FORGE_OUTLINE_THINKING_PROVIDERS"] = _line_text(
        w,
        "_outline_thinking_providers",
    )
    pairs["NOVEL_FORGE_OUTLINE_THINKING_MODELS"] = _line_text(w, "_outline_thinking_models")
    pairs["NOVEL_FORGE_OUTLINE_MULTI_TURN"] = _combo_text(w, "_outline_multi_turn")
    pairs["NOVEL_FORGE_OUTLINE_MULTI_TURN_PROVIDERS"] = _line_text(
        w,
        "_outline_multi_turn_providers",
    )
    pairs["NOVEL_FORGE_OUTLINE_MULTI_TURN_MODELS"] = _line_text(
        w,
        "_outline_multi_turn_models",
    )

    pairs["NOVEL_FORGE_LONG_CHAPTER_COMPACT_INTERVAL"] = str(
        _spin_value(w, "_long_compact_interval")
    )
    pairs["NOVEL_FORGE_LONG_CHAPTER_COMPACT_START_CHAPTER"] = str(
        _spin_value(w, "_long_compact_start")
    )
    pairs["NOVEL_FORGE_LONG_CHAPTER_COMPACT_STALE_CHAPTERS"] = str(
        _spin_value(w, "_long_compact_stale")
    )
    pairs["NOVEL_FORGE_LONG_CHAPTER_COMPACT_OUTLINE_LOOKAHEAD"] = str(
        _spin_value(w, "_long_compact_lookahead")
    )
    pairs["NOVEL_FORGE_LONG_CHAPTER_COMPACT_MIN_ACTIVE_CHARACTERS"] = str(
        _spin_value(w, "_long_compact_min_chars")
    )
    pairs["NOVEL_FORGE_LONG_CHAPTER_COMPACT_TARGET_WORLD_FACTS"] = str(
        _spin_value(w, "_long_compact_target_facts")
    )
    pairs["NOVEL_FORGE_LONG_CHAPTER_COMPACT_KEEP_RECENT_WORLD_FACTS"] = str(
        _spin_value(w, "_long_compact_keep_recent_facts")
    )
    pairs["NOVEL_FORGE_LONG_CHAPTER_COMPACT_ARCHIVE_RESOLVED_FORESHADOWING_AFTER"] = str(
        _spin_value(w, "_long_compact_archive_foreshadowing")
    )
    pairs["NOVEL_FORGE_LONG_PROMPT_MAX_CHARACTER_PROFILES"] = str(
        _spin_value(w, "_long_max_profiles")
    )
    pairs["NOVEL_FORGE_LONG_PROMPT_MAX_PROFILE_FIELD_CHARS"] = str(
        _spin_value(w, "_long_max_profile_chars")
    )
    pairs["NOVEL_FORGE_LONG_PROMPT_MAX_RELATIONSHIPS_PER_PROFILE"] = str(
        _spin_value(w, "_long_max_relations")
    )
    pairs["NOVEL_FORGE_LONG_PLAN_BEATS_MIN"] = str(_spin_value(w, "_long_beats_min"))
    pairs["NOVEL_FORGE_LONG_PLAN_BEATS_MAX"] = str(_spin_value(w, "_long_beats_max"))
    pairs["NOVEL_FORGE_LONG_PLAN_MAX_SCENE_SWITCHES"] = str(_spin_value(w, "_long_scene_switches"))
    pairs["NOVEL_FORGE_LONG_PLAN_SENSORY_NOTES_MAX_ITEMS"] = str(
        _spin_value(w, "_long_sensory_anchor_limit")
    )
    pairs["NOVEL_FORGE_LONG_PLAN_BEAT_MAX_CHARS"] = str(_spin_value(w, "_long_beat_chars"))
    pairs["NOVEL_FORGE_CHAPTER_CONTRACT_CONTEXT_WINDOW"] = str(
        _spin_value(w, "_chapter_contract_context_window")
    )
    pairs["NOVEL_FORGE_CHAPTER_CONTRACT_DYNAMIC_BUDGET_ENABLED"] = _combo_text(
        w,
        "_chapter_contract_dynamic_budget",
    )
    pairs["NOVEL_FORGE_CHAPTER_CONTRACT_HARD_WORDS_PER_ITEM"] = str(
        _spin_value(w, "_chapter_contract_hard_words_per_item")
    )
    pairs["NOVEL_FORGE_CHAPTER_CONTRACT_HARD_MIN_ITEMS"] = str(
        _spin_value(w, "_chapter_contract_hard_min_items")
    )
    pairs["NOVEL_FORGE_CHAPTER_CONTRACT_HARD_MAX_ITEMS"] = str(
        _spin_value(w, "_chapter_contract_hard_max_items")
    )
    pairs["NOVEL_FORGE_CHAPTER_CONTRACT_SOFT_WORDS_PER_ITEM"] = str(
        _spin_value(w, "_chapter_contract_soft_words_per_item")
    )
    pairs["NOVEL_FORGE_CHAPTER_CONTRACT_SOFT_MAX_ITEMS"] = str(
        _spin_value(w, "_chapter_contract_soft_max_items")
    )
    pairs["NOVEL_FORGE_CHAPTER_CONTRACT_STATE_WORDS_PER_ITEM"] = str(
        _spin_value(w, "_chapter_contract_state_words_per_item")
    )
    pairs["NOVEL_FORGE_CHAPTER_CONTRACT_STATE_MAX_ITEMS"] = str(
        _spin_value(w, "_chapter_contract_state_max_items")
    )
    pairs["NOVEL_FORGE_CHAPTER_CONTRACT_MULTI_TURN"] = _combo_text(
        w,
        "_chapter_contract_multi_turn",
    )
    pairs["NOVEL_FORGE_CHAPTER_CONTRACT_MULTI_TURN_PROVIDERS"] = _line_text(
        w,
        "_chapter_contract_multi_turn_providers",
    )
    pairs["NOVEL_FORGE_CHAPTER_CONTRACT_MULTI_TURN_MODELS"] = _line_text(
        w,
        "_chapter_contract_multi_turn_models",
    )
    pairs["NOVEL_FORGE_CONTRACT_COHERENCE_BATCH_SIZE"] = str(
        _spin_value(w, "_contract_coherence_batch_size")
    )
    pairs["NOVEL_FORGE_CONTRACT_COHERENCE_CONTEXT_WINDOW"] = str(
        _spin_value(w, "_contract_coherence_context_window")
    )
    pairs["NOVEL_FORGE_CONTRACT_COHERENCE_MAX_PARALLEL"] = str(
        _spin_value(w, "_contract_coherence_max_parallel")
    )
    pairs["NOVEL_FORGE_INIT_COHERENCE_USE_MEMORY"] = _combo_text(
        w,
        "_init_coh_use_memory",
    )
    pairs["NOVEL_FORGE_INIT_COHERENCE_CLAIM_BATCH_SIZE"] = str(
        _spin_value(w, "_init_coh_claim_batch_size")
    )
    pairs["NOVEL_FORGE_INIT_COHERENCE_CLAIM_PAYLOAD_CHAR_BUDGET"] = str(
        _spin_value(w, "_init_coh_claim_payload_budget")
    )
    pairs["NOVEL_FORGE_INIT_COHERENCE_CLAIM_MAX_PARALLEL"] = str(
        _spin_value(w, "_init_coh_claim_max_parallel")
    )
    pairs["NOVEL_FORGE_INIT_BLUEPRINT_HOLISTIC_CLAIMS_ENABLED"] = _combo_text(
        w,
        "_init_blueprint_holistic_claims",
    )
    pairs["NOVEL_FORGE_INIT_BLUEPRINT_HOLISTIC_CLAIM_MAX_TOKENS"] = str(
        _spin_value(w, "_init_blueprint_holistic_claim_tokens")
    )
    pairs["NOVEL_FORGE_INIT_STREAM_CLAIM_PREFETCH_ENABLED"] = _combo_text(
        w,
        "_init_stream_claim_prefetch",
    )
    pairs["NOVEL_FORGE_INIT_COHERENCE_OVERLAP_CHAPTERS"] = str(
        _spin_value(w, "_init_coh_overlap_chapters")
    )
    pairs["NOVEL_FORGE_INIT_COHERENCE_SEMANTIC_TOP_K"] = str(
        _spin_value(w, "_init_coh_semantic_top_k")
    )
    pairs["NOVEL_FORGE_INIT_COHERENCE_CANDIDATE_MAX_PER_BATCH"] = str(
        _spin_value(w, "_init_coh_candidate_max")
    )
    pairs["NOVEL_FORGE_INIT_COHERENCE_LLM_CANDIDATE_BATCH_SIZE"] = str(
        _spin_value(w, "_init_coh_llm_candidate_batch")
    )
    pairs["NOVEL_FORGE_INIT_COHERENCE_LLM_CANDIDATE_MAX_PARALLEL"] = str(
        _spin_value(w, "_init_coh_llm_candidate_max_parallel")
    )
    pairs["NOVEL_FORGE_INIT_COHERENCE_CONFIDENCE_THRESHOLD"] = str(
        _float_value(w, "_init_coh_confidence_threshold")
    )
    pairs["NOVEL_FORGE_INIT_COHERENCE_RECHECK_AFFECTED_WINDOW"] = str(
        _spin_value(w, "_init_coh_recheck_window")
    )
    pairs["NOVEL_FORGE_INIT_COHERENCE_AUTO_REPAIR"] = _combo_text(
        w,
        "_init_coh_auto_repair",
    )
    pairs["NOVEL_FORGE_INIT_COHERENCE_DETERMINISTIC_REPAIR"] = _combo_text(
        w,
        "_init_coh_deterministic_repair",
    )
    pairs["NOVEL_FORGE_INIT_COHERENCE_MAX_REPAIR_ROUNDS"] = str(
        _spin_value(w, "_init_coh_repair_rounds")
    )
    pairs["NOVEL_FORGE_INIT_COHERENCE_BLOCK_MIN_SEVERITY"] = _combo_text(
        w,
        "_init_coh_block_min_severity",
    )
    pairs["NOVEL_FORGE_INIT_COHERENCE_PATCH_MAX_OPS"] = str(
        _spin_value(w, "_init_coh_patch_max_ops")
    )
    pairs["NOVEL_FORGE_INIT_COHERENCE_TARGET_PATCH_BATCH_SIZE"] = str(
        _spin_value(w, "_init_coh_target_patch_batch_size")
    )
    pairs["NOVEL_FORGE_INIT_SOURCE_ARTIFACT_AUTO_REPAIR"] = _combo_text(
        w,
        "_init_source_artifact_auto_repair",
    )
    pairs["NOVEL_FORGE_INIT_SOURCE_ARTIFACT_REPAIR_ROUNDS"] = str(
        _spin_value(w, "_init_source_artifact_repair_rounds")
    )
    pairs["NOVEL_FORGE_INIT_CLAIM_COVERAGE_ENABLED"] = _combo_text(
        w,
        "_init_claim_coverage_enabled",
    )
    pairs["NOVEL_FORGE_INIT_CLAIM_COVERAGE_BLOCK_P0"] = _combo_text(
        w,
        "_init_claim_coverage_block_p0",
    )
    pairs["NOVEL_FORGE_INIT_CLAIM_COVERAGE_BLOCK_P1"] = _combo_text(
        w,
        "_init_claim_coverage_block_p1",
    )
    pairs["NOVEL_FORGE_INIT_CLAIM_COVERAGE_BLOCK_DEGRADED"] = _combo_text(
        w,
        "_init_claim_coverage_block_degraded",
    )
    pairs["NOVEL_FORGE_INIT_DISABLE_LOCAL_STORY_FALLBACKS"] = _combo_text(
        w,
        "_init_disable_local_story_fallbacks",
    )
    pairs["NOVEL_FORGE_INIT_CREATIVE_REFINEMENT_ENABLED"] = _combo_text(
        w,
        "_init_creative_refinement_enabled",
    )
    pairs["NOVEL_FORGE_INIT_CREATIVE_REFINEMENT_AUTO_APPLY_LOW_RISK"] = _combo_text(
        w,
        "_init_creative_refinement_auto_apply",
    )
    pairs["NOVEL_FORGE_INIT_READINESS_REQUIRED"] = _combo_text(
        w,
        "_init_readiness_required",
    )
    pairs["NOVEL_FORGE_CANON_CONTEXT_MAX_RECENT_EVENTS"] = str(_spin_value(w, "_canon_events"))
    pairs["NOVEL_FORGE_CANON_CONTEXT_MAX_CHARACTERS"] = str(_spin_value(w, "_canon_chars"))
    pairs["NOVEL_FORGE_CANON_CONTEXT_MAX_FORESHADOWING"] = str(_spin_value(w, "_canon_foreshadow"))
    pairs["NOVEL_FORGE_LONG_PLAN_MAX_FORESHADOWING"] = str(_spin_value(w, "_plan_foreshadow"))
    pairs["NOVEL_FORGE_CANON_CONTEXT_MAX_WORLD_FACTS"] = str(_spin_value(w, "_canon_facts"))
    pairs["NOVEL_FORGE_LONG_NARRATIVE_EVIDENCE_CANDIDATE_LIMIT"] = str(
        _spin_value(w, "_narrative_evidence_candidates")
    )
    pairs["NOVEL_FORGE_LONG_NARRATIVE_EVIDENCE_TOKEN_BUDGET"] = str(
        _spin_value(w, "_narrative_evidence_token_budget")
    )
    pairs["NOVEL_FORGE_LONG_BOUNDARY_PREV_TAIL_PARAGRAPHS"] = str(
        _spin_value(w, "_boundary_prev_tail_paragraphs")
    )
    pairs["NOVEL_FORGE_LONG_BOUNDARY_OPENING_PARAGRAPHS"] = str(
        _spin_value(w, "_boundary_opening_paragraphs")
    )
    pairs["NOVEL_FORGE_LONG_DRAFT_PROMPT_DIAGNOSTICS_ENABLED"] = _combo_text(
        w, "_draft_prompt_diag_enabled"
    )
    pairs["NOVEL_FORGE_LONG_DRAFT_PROMPT_WARN_TOKENS"] = str(
        _spin_value(w, "_draft_prompt_warn_tokens")
    )
    pairs["NOVEL_FORGE_LONG_PROMPT_DIAGNOSTICS_ENABLED"] = _combo_text(w, "_prompt_diag_enabled")
    pairs["NOVEL_FORGE_LONG_PROMPT_WARN_TOKENS"] = str(_spin_value(w, "_prompt_warn_tokens"))
    pairs["NOVEL_FORGE_NARRATIVE_STATE_ENABLED"] = _combo_text(w, "_narrative_state_enabled")
    pairs["NOVEL_FORGE_NARRATIVE_STATE_REQUIRED"] = _combo_text(w, "_narrative_state_required")
    pairs["NOVEL_FORGE_NARRATIVE_STATE_MAX_REPAIR_ROUNDS"] = str(
        _spin_value(w, "_narrative_state_repair_rounds")
    )
    pairs["NOVEL_FORGE_NARRATIVE_STATE_CANDIDATE_MAX_COUNT"] = str(
        _spin_value(w, "_narrative_state_candidate_max")
    )
    pairs["NOVEL_FORGE_NARRATIVE_STATE_CANDIDATE_EVIDENCE_LIMIT"] = str(
        _spin_value(w, "_narrative_state_candidate_evidence_limit")
    )
    pairs["NOVEL_FORGE_NARRATIVE_STATE_PENDING_TAIL_ITEMS"] = str(
        _spin_value(w, "_narrative_state_pending_tail")
    )
    pairs["NOVEL_FORGE_NARRATIVE_STATE_FINAL_CONTEXT_MAX_CHARS"] = str(
        _spin_value(w, "_narrative_state_final_context_max_chars")
    )
    pairs["NOVEL_FORGE_NARRATIVE_STATE_FINAL_TARGET_MAX_CHARS"] = str(
        _spin_value(w, "_narrative_state_final_target_max_chars")
    )
    pairs["NOVEL_FORGE_PLOT_PROGRESSION_STRICTNESS"] = _combo_data(
        w, "_plot_progression_strictness"
    ) or _combo_text(w, "_plot_progression_strictness")
    pairs["NOVEL_FORGE_LONG_FUTURE_LEAK_GUARD_ENABLED"] = _combo_text(
        w, "_future_leak_guard_enabled"
    )
    pairs["NOVEL_FORGE_LONG_CONTRACT_AUDIT_ENABLED"] = _combo_text(w, "_contract_audit_enabled")
    pairs["NOVEL_FORGE_LONG_CONTRACT_AUDIT_STRICTNESS"] = _combo_data(
        w, "_contract_audit_strictness"
    ) or _combo_text(w, "_contract_audit_strictness")
    pairs["NOVEL_FORGE_EXPRESSION_CHANNEL_DETECTION_ENABLED"] = _combo_text(
        w, "_expression_channel_detection"
    )
    pairs["NOVEL_FORGE_EXPRESSION_CHANNEL_COOLDOWN_CHAPTERS"] = str(
        _spin_value(w, "_expression_cooldown_window")
    )
    pairs["NOVEL_FORGE_ARC_LIVENESS_WINDOW"] = str(_spin_value(w, "_arc_liveness_window"))
    pairs["NOVEL_FORGE_STAGE_VISIBILITY_DEBUG_ENABLED"] = _combo_text(w, "_stage_visibility_debug")
    pairs["NOVEL_FORGE_EXTRACT_CANON_MAX_EXISTING_THREAD_IDS"] = str(
        _spin_value(w, "_extract_existing_thread_ids")
    )
    pairs["NOVEL_FORGE_EXTRACT_CANON_MAX_PRIOR_RELATIONSHIPS"] = str(
        _spin_value(w, "_extract_prior_relationships")
    )
    pairs["NOVEL_FORGE_EXTRACT_CANON_RECENT_CHARACTER_WINDOW_CHAPTERS"] = str(
        _spin_value(w, "_extract_recent_character_window")
    )
    pairs["NOVEL_FORGE_EXTRACT_CANON_MAX_PRIOR_CHARACTERS"] = str(
        _spin_value(w, "_extract_prior_characters")
    )
    pairs["NOVEL_FORGE_EXTRACT_CANON_MAX_PRIOR_PLOT_THREADS"] = str(
        _spin_value(w, "_extract_prior_plot_threads")
    )
    pairs["NOVEL_FORGE_EXTRACT_CANON_PRIOR_PLOT_THREAD_SUMMARY_CHARS"] = str(
        _spin_value(w, "_extract_prior_plot_thread_summary_chars")
    )
    pairs["NOVEL_FORGE_EXTRACT_CANON_OUTPUT_BASE_TOKENS"] = str(
        _spin_value(w, "_extract_output_base_tokens")
    )
    pairs["NOVEL_FORGE_EXTRACT_CANON_OUTPUT_TOKENS_PER_CHARACTER"] = str(
        _spin_value(w, "_extract_output_per_character_tokens")
    )
    pairs["NOVEL_FORGE_EXTRACT_CANON_OUTPUT_TOKENS_PER_2500_CHARS"] = str(
        _spin_value(w, "_extract_output_per_2500_chars")
    )
    pairs["NOVEL_FORGE_EXTRACT_CANON_OUTPUT_MAX_TOKENS"] = str(
        _spin_value(w, "_extract_output_max_tokens")
    )
    pairs["NOVEL_FORGE_EXTRACT_CANON_ABORT_ON_SEVERE_DAMAGE"] = _combo_text(
        w, "_extract_abort_on_severe_damage"
    )
    pairs["NOVEL_FORGE_EXTRACT_CANON_SEVERE_DAMAGE_MISSING_SECTION_THRESHOLD"] = str(
        _spin_value(w, "_extract_severe_damage_threshold")
    )
    pairs["NOVEL_FORGE_EXTRACT_CANON_MAX_CHARACTER_STATE_DELTAS"] = str(
        _spin_value(w, "_extract_max_character_state_deltas")
    )
    pairs["NOVEL_FORGE_EXTRACT_CANON_MAX_RELATIONSHIP_DELTAS"] = str(
        _spin_value(w, "_extract_max_relationship_deltas")
    )
    pairs["NOVEL_FORGE_EXTRACT_CANON_MAX_PLOT_THREAD_DELTAS"] = str(
        _spin_value(w, "_extract_max_plot_thread_deltas")
    )
    pairs["NOVEL_FORGE_EXTRACT_CANON_MAX_EXIT_STATE_CHARACTERS"] = str(
        _spin_value(w, "_extract_max_exit_state_characters")
    )
    pairs["NOVEL_FORGE_LONG_CONTEXT_COMPRESS_ENABLED"] = _combo_text(w, "_compress_enabled")
    pairs["NOVEL_FORGE_LONG_CONTEXT_COMPRESS_MIN_CHARS"] = str(_spin_value(w, "_compress_min"))
    pairs["NOVEL_FORGE_LONG_CONTEXT_COMPRESS_MAX_TOKENS"] = str(
        _spin_value(w, "_compress_max_tokens")
    )

    for key, spin in w.get("_long_temp_spins", {}).items():
        try:
            _record_temperature_pair(page, pairs, task_key=key, spin=spin)
        except AttributeError:
            pass

    pairs["NOVEL_FORGE_LONG_AI_JUDGE_MAX_CONTEXT_CHAPTERS"] = str(
        _spin_value(w, "_judge_ctx_chapters")
    )
    pairs["NOVEL_FORGE_LONG_AI_JUDGE_MAX_TOKENS"] = str(_spin_value(w, "_judge_max_tokens"))
    pairs["NOVEL_FORGE_LONG_AI_JUDGE_APPLY_ENTITY_ACTIONS"] = _combo_text(w, "_judge_entity")
    pairs["NOVEL_FORGE_LONG_MIN_ACCEPT_SCORE"] = str(_float_value(w, "_min_accept_score"))
    pairs["NOVEL_FORGE_LONG_CONTINUITY_HARD_BLOCK_THRESHOLD"] = str(
        _float_value(w, "_continuity_hard_block")
    )
    pairs["NOVEL_FORGE_LONG_CAUSAL_HARD_BLOCK_THRESHOLD"] = str(
        _float_value(w, "_causal_hard_block")
    )
    pairs["NOVEL_FORGE_LONG_WORD_COUNT_ARCHIVE_GATE_ENABLED"] = _combo_data(
        w, "_word_count_archive_gate_enabled"
    ) or _combo_text(w, "_word_count_archive_gate_enabled")
    pairs["NOVEL_FORGE_LONG_WAVE_WORD_COUNT_POLICY"] = (
        _combo_data(w, "_wave_word_count_policy")
        or _combo_text(w, "_wave_word_count_policy")
        or "inherit"
    )
    pairs["NOVEL_FORGE_LONG_WORD_COUNT_ARCHIVE_GATE_MAX_REJECTIONS"] = str(
        _spin_value(w, "_word_count_archive_gate_max_rejections")
    )
    pairs["NOVEL_FORGE_LONG_READING_POWER_ARCHIVE_POLICY"] = (
        _combo_data(w, "_rp_archive_policy") or _combo_text(w, "_rp_archive_policy") or "floor_only"
    )
    pairs["NOVEL_FORGE_LONG_READING_POWER_HARD_BLOCK_THRESHOLD"] = str(
        _float_value(w, "_rp_hard_block_threshold")
    )
    pairs["NOVEL_FORGE_LONG_GUARD_ARCHIVE_POLICY"] = (
        _combo_data(w, "_guard_archive_policy") or _combo_text(w, "_guard_archive_policy") or "warn"
    )
    pairs["NOVEL_FORGE_LONG_GUARD_ARCHIVE_BLOCK_MIN_CONFIDENCE"] = str(
        _float_value(w, "_guard_archive_confidence")
    )
    pairs["NOVEL_FORGE_LONG_SUMMARY_DRIFT_CHECK_ENABLED"] = _combo_text(
        w, "_summary_drift_check_enabled"
    )
    pairs["NOVEL_FORGE_LONG_QUALITY_TREND_TRACKER_ENABLED"] = _combo_text(
        w, "_quality_trend_tracker_enabled"
    )
    pairs["NOVEL_FORGE_LONG_MACRO_GUARD_ENABLED"] = _combo_text(w, "_macro_guard_enabled")
    pairs["NOVEL_FORGE_LONG_MACRO_GUARD_INTERVAL"] = str(_spin_value(w, "_macro_guard_interval"))
    pairs["NOVEL_FORGE_LONG_MACRO_GUARD_MAX_ADJUSTMENTS_PER_BOOK"] = str(
        _spin_value(w, "_macro_guard_max_adjustments")
    )
    pairs["NOVEL_FORGE_LONG_MACRO_GUARD_COOLDOWN_CHAPTERS"] = str(
        _spin_value(w, "_macro_guard_cooldown")
    )
    pairs["NOVEL_FORGE_LONG_MACRO_GUARD_DRIFT_THRESHOLD_WARNING"] = str(
        _float_value(w, "_macro_guard_drift_warning")
    )
    pairs["NOVEL_FORGE_LONG_MACRO_GUARD_DRIFT_THRESHOLD_ALERT"] = str(
        _float_value(w, "_macro_guard_drift_alert")
    )
    pairs["NOVEL_FORGE_LONG_MACRO_GUARD_DRIFT_THRESHOLD_CRITICAL"] = str(
        _float_value(w, "_macro_guard_drift_critical")
    )
    pairs["NOVEL_FORGE_LONG_MACRO_GUARD_AUTO_APPLY_HINT"] = _combo_text(
        w, "_macro_guard_auto_apply_hint"
    )
    pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_DEFAULT_MODE"] = _combo_data(
        w, "_book_audit_mode"
    ) or _combo_text(w, "_book_audit_mode")
    pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_CHAPTER_MAX_CHARS"] = str(
        _spin_value(w, "_book_audit_chapter_max_chars")
    )
    pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_PROMPT_CHAR_BUDGET"] = str(
        _spin_value(w, "_book_audit_prompt_char_budget")
    )
    pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_MAX_CHAPTERS_PER_BATCH"] = str(
        _spin_value(w, "_book_audit_max_chapters_per_batch")
    )
    pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_MAX_TOKENS"] = str(_spin_value(w, "_book_audit_max_tokens"))
    pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_MAX_ISSUES_PER_CHUNK"] = str(
        _spin_value(w, "_book_audit_max_issues_per_chunk")
    )
    pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_ISSUE_POOL_MAX_ITEMS"] = str(
        _spin_value(w, "_book_audit_issue_pool_max_items")
    )
    pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_LOCATION_STRICTNESS"] = _combo_data(
        w, "_book_audit_location_strictness"
    ) or _combo_text(w, "_book_audit_location_strictness")
    pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_PROMPT_HINT"] = _line_text(w, "_book_audit_prompt_hint")
    pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_TWO_PHASE_ENABLED"] = _bool_text(
        w, "_book_audit_two_phase_enabled"
    )
    pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_TWO_PHASE_THRESHOLD"] = str(
        _float_value(w, "_book_audit_two_phase_threshold")
    )
    pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_TWO_PHASE_MAX_TARGET_CHAPTERS"] = str(
        _spin_value(w, "_book_audit_two_phase_max_target_chapters")
    )
    # Auto repair is controlled by the book-audit dialog now. Keep the persisted
    # env key false so older blank values do not poison Settings() boolean parsing.
    pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_AUTO_REPAIR"] = "false"
    pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_REPAIR_MIN_SEVERITY"] = _combo_data(
        w, "_book_audit_repair_min_severity"
    ) or _combo_text(w, "_book_audit_repair_min_severity")
    pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_REPAIR_MAX_CHAPTERS"] = str(
        _spin_value(w, "_book_audit_repair_max_chapters")
    )
    pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_USE_ISSUE_PANEL_POOL"] = _bool_text(
        w, "_book_audit_use_issue_pool"
    )
    pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_REPAIR_CONCURRENCY"] = str(
        _spin_value(w, "_book_audit_repair_concurrency")
    )
    pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_GENERATE_REPAIR_REPORT"] = _bool_text(
        w, "_book_audit_generate_repair_report"
    )
    pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_REPAIR_GUARD_ENABLED"] = _bool_text(
        w, "_book_audit_repair_guard_enabled"
    )
    pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_PARALLEL_CHUNKS"] = _bool_text(
        w, "_book_audit_parallel_chunks"
    )
    pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_PARALLEL_DIMENSIONS"] = _bool_text(
        w, "_book_audit_parallel_dimensions"
    )
    pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_REPAIR_GUARD_MAX_DELTA_RATIO"] = str(
        _float_value(w, "_book_audit_repair_guard_max_delta_ratio")
    )
    pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_REPAIR_GUARD_MAX_ADDED_CHARS"] = str(
        _spin_value(w, "_book_audit_repair_guard_max_added_chars")
    )
    pairs["NOVEL_FORGE_LONG_CONTINUITY_REPAIR_THRESHOLD"] = str(
        _float_value(w, "_continuity_repair_threshold")
    )
    pairs["NOVEL_FORGE_LONG_CONTINUITY_MAX_REPAIR_ROUNDS"] = str(
        _spin_value(w, "_continuity_max_repair_rounds")
    )
    pairs["NOVEL_FORGE_FORBIDDEN_ELEMENTS_CROSS_CHAPTER_WINDOW"] = str(
        _spin_value(w, "_forbidden_cross_chapter_window")
    )
    pairs["NOVEL_FORGE_FORBIDDEN_ELEMENTS_HARD_MAX_ITEMS"] = str(
        _spin_value(w, "_forbidden_hard_max_items")
    )
    pairs["NOVEL_FORGE_FORBIDDEN_ELEMENTS_SOFT_MAX_ITEMS"] = str(
        _spin_value(w, "_forbidden_soft_max_items")
    )
    pairs["NOVEL_FORGE_FORBIDDEN_ELEMENTS_QUOTA_MAX_ITEMS"] = str(
        _spin_value(w, "_forbidden_quota_max_items")
    )
    pairs["NOVEL_FORGE_FORBIDDEN_ELEMENT_SOURCES_MAX_ITEMS"] = str(
        _spin_value(w, "_forbidden_sources_max_items")
    )
    pairs["NOVEL_FORGE_FORBIDDEN_ELEMENTS_RANK_BY_RELEVANCE"] = _combo_text(
        w,
        "_forbidden_rank_by_relevance",
    )
    pairs["NOVEL_FORGE_LONG_OPENING_GUARD_ENABLED"] = _combo_text(w, "_opening_guard_enabled")
    pairs["NOVEL_FORGE_LONG_OPENING_GUARD_MAX_ISSUES"] = str(
        _spin_value(w, "_opening_guard_max_issues")
    )
    pairs["NOVEL_FORGE_LONG_PLAN_MAX_KEY_REVELATIONS_PER_CHAPTER"] = str(
        _spin_value(w, "_plan_max_key_revelations")
    )
    pairs["NOVEL_FORGE_INIT_CONTINUITY_PROTOCOL_ENABLED"] = _combo_text(
        w, "_init_cont_protocol_enabled"
    )
    pairs["NOVEL_FORGE_INIT_CONTINUITY_LOCATION_TRANSITION_REQUIRED"] = _combo_text(
        w, "_init_cont_loc_required"
    )
    pairs["NOVEL_FORGE_INIT_CONTINUITY_LOCATION_TRANSITION_WINDOW_SENTENCES"] = str(
        _spin_value(w, "_init_cont_loc_window")
    )
    pairs["NOVEL_FORGE_INIT_CONTINUITY_BRIDGE_ECHO_RATIO"] = str(
        _float_value(w, "_init_cont_bridge_ratio")
    )
    pairs["NOVEL_FORGE_INIT_CONTINUITY_BRIDGE_ECHO_MIN_CHARS"] = str(
        _spin_value(w, "_init_cont_bridge_min")
    )
    pairs["NOVEL_FORGE_INIT_CONTINUITY_BRIDGE_ECHO_MAX_CHARS"] = str(
        _spin_value(w, "_init_cont_bridge_max")
    )
    pairs["NOVEL_FORGE_INIT_CONTINUITY_TIME_NOTATION_PROFILE"] = _combo_data(
        w, "_init_cont_time_profile"
    ) or _combo_text(w, "_init_cont_time_profile")
    pairs["NOVEL_FORGE_INIT_CONTINUITY_TRADITIONAL_TIME_KE_RANGE"] = _line_text(
        w, "_init_cont_time_ke_range"
    )
    pairs["NOVEL_FORGE_INIT_CONTINUITY_POV_VISIBILITY_RULE"] = _line_text(w, "_init_cont_pov_rule")
    pairs["NOVEL_FORGE_INIT_CONTINUITY_FORBIDDEN_REPETITION_RULE"] = _line_text(
        w, "_init_cont_forbidden_rule"
    )
    pairs["NOVEL_FORGE_INIT_CONTINUITY_MAX_KEY_REVELATIONS_PER_CHAPTER"] = str(
        _spin_value(w, "_init_cont_max_reveals")
    )
    pairs["NOVEL_FORGE_INIT_CONTINUITY_MIN_UNRESOLVED_THREADS_TO_KEEP"] = str(
        _spin_value(w, "_init_cont_min_unresolved")
    )
    pairs["NOVEL_FORGE_LONG_CAUSAL_REPAIR_ENABLED"] = _combo_text(w, "_causal_repair_enabled")
    pairs["NOVEL_FORGE_LONG_CAUSAL_THRESHOLD"] = str(_float_value(w, "_causal_threshold"))
    pairs["NOVEL_FORGE_LONG_CAUSAL_MAX_REPAIR_ROUNDS"] = str(
        _spin_value(w, "_causal_max_repair_rounds")
    )
    pairs["NOVEL_FORGE_CAUSAL_VALIDATION_FAIL_MODE"] = _combo_data(
        w, "_causal_fail_mode"
    ) or _combo_text(w, "_causal_fail_mode")
    pairs["NOVEL_FORGE_REPAIR_CONTROL_MODE"] = _combo_data(
        w, "_repair_control_mode"
    ) or _combo_text(w, "_repair_control_mode")
    pairs["NOVEL_FORGE_MAX_AUTO_REPAIR_ATTEMPTS"] = str(_spin_value(w, "_max_auto_repair_attempts"))
    pairs["NOVEL_FORGE_REPAIR_MUST_FIX_SEVERITY"] = _combo_text(w, "_repair_must_fix_severity")
    pairs["NOVEL_FORGE_RECHECK_STRATEGY"] = _combo_data(w, "_recheck_strategy") or _combo_text(
        w, "_recheck_strategy"
    )
    pairs["NOVEL_FORGE_CHANGE_BUDGET_THRESHOLD"] = str(_float_value(w, "_change_budget_threshold"))
    pairs["NOVEL_FORGE_REPAIR_DISPLAY_MIN_SEVERITY"] = _combo_text(
        w, "_repair_display_min_severity"
    )
    pairs["NOVEL_FORGE_REPAIR_ALWAYS_REAUDIT"] = _combo_data(
        w, "_repair_always_reaudit"
    ) or _combo_text(w, "_repair_always_reaudit")
    pairs["NOVEL_FORGE_LONG_POLISH_ENABLED"] = _combo_text(w, "_long_polish_enabled")
    pairs["NOVEL_FORGE_HUMANIZE_ENABLED"] = _combo_text(w, "_humanize_enabled")
    pairs["NOVEL_FORGE_HUMANIZE_CHANGE_RATIO_CAP"] = str(
        _float_value(w, "_humanize_change_ratio_cap")
    )
    pairs["NOVEL_FORGE_HUMANIZE_MIN_TEXT_LENGTH"] = str(_spin_value(w, "_humanize_min_text_length"))
    pairs["NOVEL_FORGE_HUMANIZE_MODEL"] = _combo_text(w, "_humanize_model")
    pairs["NOVEL_FORGE_HUMANIZE_PATCH_CONFIDENCE_FLOOR"] = str(
        _float_value(w, "_humanize_patch_confidence_floor")
    )
    pairs["NOVEL_FORGE_HUMANIZE_PARAGRAPH_CONFIDENCE_FLOOR"] = str(
        _float_value(w, "_humanize_paragraph_confidence_floor")
    )
    pairs["NOVEL_FORGE_HUMANIZE_LIBRARY_SIM_THRESHOLD"] = str(
        _float_value(w, "_humanize_library_sim_threshold")
    )
    pairs["NOVEL_FORGE_AUTO_INTRODUCE_CHARACTERS"] = _combo_text(w, "_auto_introduce_chars")
    pairs["NOVEL_FORGE_LONG_AUTO_INTRODUCE_MAX_NEW_CHARACTERS"] = str(
        _spin_value(w, "_auto_introduce_char_limit")
    )
    pairs["NOVEL_FORGE_STYLE_PROFILE_ENABLED"] = _combo_text(w, "_style_profile_enabled")
    pairs["NOVEL_FORGE_STYLE_PROFILE_REQUIRED"] = _combo_text(w, "_style_profile_required")
    pairs["NOVEL_FORGE_LOCAL_CHECK_AS_PRESCREEN"] = (
        "true" if _combo_text(w, "_local_check_enabled") == "prescreen" else "false"
    )
    pairs["NOVEL_FORGE_LOCAL_CHECK_CONFIDENCE_THRESHOLD"] = str(
        _float_value(w, "_local_confidence_threshold")
    )
    pairs["NOVEL_FORGE_LOCAL_GUARDRAILS_TRUST_LEVEL"] = _combo_text(w, "_guardrails_trust")
    pairs["NOVEL_FORGE_PRONOUN_AUTOFIX_MODE"] = _combo_data(
        w, "_pronoun_autofix_mode"
    ) or _combo_text(w, "_pronoun_autofix_mode")
    pairs["NOVEL_FORGE_LOG_LEVEL"] = _combo_text(w, "_log_level")
    pairs["NOVEL_FORGE_LOG_KEEP_RUNS"] = str(_spin_value(w, "_log_keep_runs"))
    pairs["NOVEL_FORGE_API_CALL_TIMEOUT_S"] = str(_spin_value(w, "_api_timeout"))
    pairs["NOVEL_FORGE_STORY_KERNEL_DB_PATH"] = _line_text(w, "_story_kernel_db_path")
    pairs["NOVEL_FORGE_STORY_KERNEL_WAL_MODE"] = _combo_text(w, "_story_kernel_wal_mode")
    pairs["NOVEL_FORGE_STORY_KERNEL_ZVEC_ENABLED"] = _combo_text(w, "_story_kernel_zvec_enabled")
    pairs["NOVEL_FORGE_LLM_FORMAT_RETRY_ATTEMPTS"] = str(
        _spin_value(w, "_llm_format_retry_attempts")
    )
    pairs["NOVEL_FORGE_LLM_FORMAT_RETRY_TEMPERATURE"] = str(
        _float_value(w, "_llm_format_retry_temperature")
    )
    pairs["NOVEL_FORGE_LLM_FORMAT_RETRY_RAW_CHAR_LIMIT"] = str(
        _spin_value(w, "_llm_format_retry_raw_char_limit")
    )
    pairs["NOVEL_FORGE_LLM_FORMAT_REPAIR_ENABLED"] = _combo_text(
        w,
        "_llm_format_repair_enabled",
    )
    pairs["NOVEL_FORGE_LLM_FORMAT_REPAIR_MODEL"] = _combo_text(
        w,
        "_llm_format_repair_model",
    )
    pairs["NOVEL_FORGE_LLM_FORMAT_REPAIR_MAX_TOKENS"] = str(
        _spin_value(w, "_llm_format_repair_max_tokens")
    )
    pairs["NOVEL_FORGE_LLM_FORMAT_REPAIR_RAW_CHAR_LIMIT"] = str(
        _spin_value(w, "_llm_format_repair_raw_char_limit")
    )
    pairs["NOVEL_FORGE_LONG_CHECK_CHAPTER_ENABLED"] = _combo_text(w, "_check_chapter_enabled")
    pairs["NOVEL_FORGE_LONG_MAX_CONSISTENCY_REPLANS"] = str(
        _spin_value(w, "_max_consistency_replans")
    )
    pairs["NOVEL_FORGE_ELEMENT_PROGRESS_LLM_ARBITER_ENABLED"] = _combo_text(
        w, "_element_progress_arbiter_enabled"
    )
    pairs["NOVEL_FORGE_ELEMENT_PROGRESS_LLM_ARBITER_MAX_ITEMS_PER_CHAPTER"] = str(
        _spin_value(w, "_element_progress_arbiter_max_items")
    )
    pairs["NOVEL_FORGE_ELEMENT_PROGRESS_LLM_GRAY_SCORE_LOW"] = str(
        _float_value(w, "_element_progress_gray_low")
    )
    pairs["NOVEL_FORGE_ELEMENT_PROGRESS_LLM_GRAY_SCORE_HIGH"] = str(
        _float_value(w, "_element_progress_gray_high")
    )
    pairs["NOVEL_FORGE_ELEMENT_PROGRESS_LLM_ARBITER_MAX_TOKENS"] = str(
        _spin_value(w, "_element_progress_arbiter_max_tokens")
    )
    pairs["NOVEL_FORGE_ELEMENT_PROGRESS_LLM_ARBITER_TEMPERATURE"] = str(
        _float_value(w, "_element_progress_arbiter_temp")
    )

    pairs["NOVEL_FORGE_MEMORY_EPISODIC_ENABLED"] = _combo_text(w, "_memory_episodic_enabled")
    pairs["NOVEL_FORGE_MEMORY_EMBEDDING_PROFILE_ID"] = (
        _combo_data(w, "_memory_embedding_profile") or "auto"
    )
    pairs["NOVEL_FORGE_MEMORY_VECTOR_STORE_BACKEND"] = _combo_text(
        w, "_memory_vector_store_backend"
    )
    pairs["NOVEL_FORGE_MEMORY_ZVEC_INDEX_TYPE"] = _combo_text(w, "_memory_zvec_index_type")
    pairs["NOVEL_FORGE_MEMORY_SEMANTIC_SEARCH_ENABLED"] = _combo_text(w, "_memory_semantic_search")
    pairs["NOVEL_FORGE_MEMORY_MULTI_GRANULARITY_SUMMARY_ENABLED"] = _combo_text(
        w, "_memory_summary_enabled"
    )
    pairs["NOVEL_FORGE_MEMORY_CHAPTER_SUMMARY_TARGET_WORDS"] = str(
        _spin_value(w, "_memory_chapter_summary_target_words")
    )
    pairs["NOVEL_FORGE_MEMORY_VOLUME_SUMMARY_TARGET_WORDS"] = str(
        _spin_value(w, "_memory_volume_summary_target_words")
    )
    pairs["NOVEL_FORGE_MEMORY_SUMMARY_INPUT_TOKEN_BUDGET"] = str(
        _spin_value(w, "_memory_summary_input_token_budget")
    )
    pairs["NOVEL_FORGE_MEMORY_SUMMARY_RECENT_CHAPTERS"] = str(
        _spin_value(w, "_memory_summary_recent_chapters")
    )
    pairs["NOVEL_FORGE_MEMORY_VOLUME_SUMMARY_INJECT_THRESHOLD"] = str(
        _spin_value(w, "_memory_volume_summary_threshold")
    )
    pairs["NOVEL_FORGE_MEMORY_ADAPTIVE_COMPRESSION_ENABLED"] = _combo_text(
        w, "_memory_compression_enabled"
    )
    pairs["NOVEL_FORGE_MEMORY_MOTIF_TRACKING_ENABLED"] = _combo_text(w, "_memory_motif_enabled")
    pairs["NOVEL_FORGE_MEMORY_MOTIF_CHECK_REPETITION"] = _combo_text(w, "_memory_motif_repetition")
    pairs["NOVEL_FORGE_MOTIF_SUGGESTION_MIN_CHAPTERS"] = str(
        _spin_value(w, "_motif_suggestion_min_chapters")
    )
    pairs["NOVEL_FORGE_MOTIF_SUGGESTION_MIN_OCCURRENCES"] = str(
        _spin_value(w, "_motif_suggestion_min_occurrences")
    )
    pairs["NOVEL_FORGE_MEMORY_MOTIF_RELATED_LOOKBACK_CHAPTERS"] = str(
        _spin_value(w, "_memory_motif_related_lookback")
    )
    pairs["NOVEL_FORGE_MOTIF_PROMPT_TOKEN_BUDGET"] = str(
        _spin_value(w, "_motif_prompt_token_budget")
    )
    pairs["NOVEL_FORGE_MOTIF_PROMPT_MAX_ITEMS"] = str(_spin_value(w, "_motif_prompt_max_items"))
    pairs["NOVEL_FORGE_MOTIF_FORBIDDEN_MAX_ITEMS"] = str(
        _spin_value(w, "_motif_forbidden_max_items")
    )
    pairs["NOVEL_FORGE_MOTIF_REPETITION_LOOKBACK_CHAPTERS"] = str(
        _spin_value(w, "_motif_repetition_lookback")
    )
    pairs["NOVEL_FORGE_MOTIF_REPETITION_RECENT_GAP_CHAPTERS"] = str(
        _spin_value(w, "_motif_repetition_recent_gap")
    )
    pairs["NOVEL_FORGE_MOTIF_DORMANT_CALLBACK_MIN_CHAPTERS"] = str(
        _spin_value(w, "_motif_dormant_callback_min")
    )
    pairs["NOVEL_FORGE_MOTIF_AUTO_FORGET_EPHEMERAL_ENABLED"] = _combo_text(
        w,
        "_motif_auto_forget_ephemeral",
    )
    pairs["NOVEL_FORGE_MOTIF_EPHEMERAL_FORGET_AFTER_CHAPTERS"] = str(
        _spin_value(w, "_motif_ephemeral_forget_after")
    )
    pairs["NOVEL_FORGE_MOTIF_EPHEMERAL_MAX_OCCURRENCES"] = str(
        _spin_value(w, "_motif_ephemeral_max_occurrences")
    )
    pairs["NOVEL_FORGE_MOTIF_EPHEMERAL_IMPORTANCE_THRESHOLD_PCT"] = str(
        _spin_value(w, "_motif_ephemeral_importance_threshold")
    )
    pairs["NOVEL_FORGE_LONG_DRAFT_CHARACTER_HISTORY_LOOKBACK"] = str(
        _spin_value(w, "_draft_char_history_lookback")
    )
    pairs["NOVEL_FORGE_MEMORY_MOTIF_RE_EXTRACT_CONCURRENCY"] = str(
        _spin_value(w, "_memory_motif_re_extract_concurrency")
    )
    pairs["NOVEL_FORGE_TEMP_EXTRACT_MOTIFS"] = str(_float_value(w, "_temp_extract_motifs"))
    pairs["NOVEL_FORGE_MEMORY_CRITIC_AGENT_ENABLED"] = _combo_text(w, "_memory_critic_enabled")
    pairs["NOVEL_FORGE_MEMORY_CRITIC_AGENT_RUN_ASYNC"] = _combo_text(w, "_memory_critic_async")
    pairs["NOVEL_FORGE_MEMORY_CRITIC_AGENT_TIMEOUT_S"] = str(
        _float_value(w, "_memory_critic_timeout")
    )
    pairs["NOVEL_FORGE_MEMORY_CRITIC_AGENT_TIMEOUT_EXTEND_ATTEMPTS"] = str(
        _spin_value(w, "_memory_critic_timeout_extend_attempts")
    )
    pairs["NOVEL_FORGE_MEMORY_CRITIC_AGENT_TIMEOUT_EXTEND_MULTIPLIER"] = str(
        _float_value(w, "_memory_critic_timeout_extend_multiplier")
    )
    pairs["NOVEL_FORGE_MEMORY_CRITIC_AGENT_CACHE_ENABLED"] = _combo_text(
        w, "_memory_critic_cache_enabled"
    )
    pairs["NOVEL_FORGE_MEMORY_CRITIC_AGENT_CACHE_MAX_ENTRIES"] = str(
        _spin_value(w, "_memory_critic_cache_max")
    )
    pairs["NOVEL_FORGE_MEMORY_CONCURRENT_INDEXING"] = _combo_text(w, "_memory_concurrent_indexing")

    pairs["NOVEL_FORGE_OLLAMA_BASE_URL"] = _line_text(w, "_ollama_base_url")
    pairs["NOVEL_FORGE_OLLAMA_MODEL"] = _line_text(w, "_ollama_model")
    pairs["NOVEL_FORGE_OLLAMA_EMBEDDING_MODEL"] = _line_text(w, "_ollama_embedding_model")
    pairs["NOVEL_FORGE_OLLAMA_SIDECAR_ENABLED"] = _combo_text(w, "_ollama_sidecar_enabled")
    pairs["NOVEL_FORGE_OLLAMA_SIDECAR_AUTO_START"] = _combo_text(w, "_ollama_sidecar_auto_start")
    pairs["NOVEL_FORGE_OLLAMA_SIDECAR_BINARY_PATH"] = _line_text(w, "_ollama_sidecar_binary_path")
    pairs["NOVEL_FORGE_OLLAMA_SIDECAR_MODELS_DIR"] = _line_text(w, "_ollama_sidecar_models_dir")
    pairs["NOVEL_FORGE_OLLAMA_SIDECAR_PREFER_LOCAL"] = _combo_text(
        w, "_ollama_sidecar_prefer_local"
    )

    pairs["NOVEL_FORGE_LONG_READING_POWER_REPAIR_ENABLED"] = _combo_text(w, "_rp_repair_enabled")
    pairs["NOVEL_FORGE_LONG_READING_POWER_REPAIR_THRESHOLD"] = str(
        _float_value(w, "_rp_repair_threshold")
    )
    pairs["NOVEL_FORGE_LONG_READING_POWER_MAX_REPAIR_ROUNDS"] = str(
        _spin_value(w, "_rp_max_repair_rounds")
    )
    pairs["NOVEL_FORGE_LONG_READING_POWER_REPAIR_MAX_CHANGE_RATIO"] = str(
        _float_value(w, "_rp_repair_change_ratio")
    )
    pairs["NOVEL_FORGE_READING_POWER_WINDOW_SIZE"] = str(_spin_value(w, "_rp_window_size"))
    pairs["NOVEL_FORGE_READING_POWER_WINDOW_LEFT_OFFSET"] = str(
        _spin_value(w, "_rp_window_left_offset")
    )
    pairs["NOVEL_FORGE_READING_POWER_WINDOW_RIGHT_OFFSET"] = str(
        _spin_value(w, "_rp_window_right_offset")
    )
    pairs["NOVEL_FORGE_READING_POWER_SUSPENSE_DELAY_THRESHOLD"] = str(
        _spin_value(w, "_rp_suspense_delay_threshold")
    )
    pairs["NOVEL_FORGE_READING_POWER_FORCE_RESOLVE_THRESHOLD"] = str(
        _spin_value(w, "_rp_force_resolve_threshold")
    )
    pairs["NOVEL_FORGE_READING_POWER_HOOK_ALTERNATION_THRESHOLD"] = str(
        _spin_value(w, "_rp_hook_alternation_threshold")
    )
    pairs["NOVEL_FORGE_READING_POWER_TENSION_DEVIATION_TOLERANCE"] = str(
        _float_value(w, "_rp_tension_deviation_tolerance")
    )
    pairs["NOVEL_FORGE_READING_POWER_ENABLED"] = _combo_text(w, "_rp_enabled")

    # ── TTS / 配音 ──────────────────────────────────────────
    pairs["NOVEL_FORGE_TTS_ENABLED"] = _combo_text(w, "_tts_enabled")
    pairs["NOVEL_FORGE_TTS_DEFAULT_PROVIDER"] = _combo_text(w, "_tts_default_provider")
    pairs["NOVEL_FORGE_TTS_DEFAULT_MODEL"] = _combo_text(w, "_tts_default_model")
    pairs["NOVEL_FORGE_TTS_DEFAULT_SPEED"] = str(_float_value(w, "_tts_default_speed"))
    pairs["NOVEL_FORGE_TTS_AUTOMATION_MODE"] = _combo_text(w, "_tts_automation_mode")
    pairs["NOVEL_FORGE_TTS_AUDIO_QUALITY_TIER"] = _combo_text(w, "_tts_audio_quality_tier")
    pairs["NOVEL_FORGE_TTS_SUBTITLE_WORD_LEVEL"] = _combo_text(w, "_tts_subtitle_word_level")
    pairs["NOVEL_FORGE_TTS_AUTO_TRIGGER_AFTER_CHAPTER"] = _combo_text(w, "_tts_auto_trigger")
    pairs["NOVEL_FORGE_TTS_VOICE_LIBRARY_SCOPE"] = _combo_text(w, "_tts_voice_library_scope")
    pairs["NOVEL_FORGE_TTS_POST_ARCHIVE_RETRY_ENABLED"] = _combo_text(
        w, "_tts_post_archive_retry_enabled"
    )
    pairs["NOVEL_FORGE_TTS_POST_ARCHIVE_RETRY_DELAY_S"] = str(
        _spin_value(w, "_tts_post_archive_retry_delay_s")
    )
    pairs["NOVEL_FORGE_TTS_MAX_CONCURRENT_SYNTHESIS"] = str(_spin_value(w, "_tts_max_concurrent"))
    pairs["NOVEL_FORGE_TTS_SYNTHESIS_REQUESTS_PER_MINUTE"] = str(_spin_value(w, "_tts_rate_limit"))
    pairs["NOVEL_FORGE_TTS_SYNTHESIS_RATE_LIMIT_COOLDOWN_S"] = str(
        _spin_value(w, "_tts_rate_cooldown")
    )
    pairs["NOVEL_FORGE_TTS_SYNTHESIS_RETRY_LIMIT"] = str(_spin_value(w, "_tts_retry_limit"))
    pairs["NOVEL_FORGE_TTS_BACKGROUND_PIPELINE_CONCURRENCY"] = str(
        _spin_value(w, "_tts_background_concurrency")
    )
    pairs["NOVEL_FORGE_TTS_OUTPUT_FORMAT"] = _combo_text(w, "_tts_output_format")
    pairs["NOVEL_FORGE_TTS_VOICE_DESIGN_ENABLED"] = _combo_text(w, "_tts_voice_design")
    pairs["NOVEL_FORGE_TTS_VOICE_SEMANTIC_MATCHING_ENABLED"] = _combo_text(w, "_tts_voice_semantic")
    pairs["NOVEL_FORGE_TTS_VOICE_LLM_ADJUDICATION_ENABLED"] = _combo_text(
        w, "_tts_voice_llm_adjudication"
    )
    pairs["NOVEL_FORGE_TTS_PARALLEL_VOICE_CLONE"] = _combo_text(w, "_tts_parallel_clone")
    pairs["NOVEL_FORGE_TTS_VOICE_CLONE_TTL_DAYS"] = str(_spin_value(w, "_tts_clone_ttl"))
    pairs["NOVEL_FORGE_TTS_SCRIPT_LLM_REVIEW_ENABLED"] = _combo_text(w, "_tts_script_review")
    pairs["NOVEL_FORGE_TTS_SOUND_DESIGN_ENABLED"] = _combo_text(w, "_tts_sound_design")
    pairs["NOVEL_FORGE_SOUND_GENERATION_ENABLED"] = _combo_text(w, "_tts_sound_gen_enabled")
    pairs["NOVEL_FORGE_TTS_SCRIPT_GENERATION_TEMPERATURE"] = str(
        _float_value(w, "_tts_script_generation_temperature")
    )
    pairs["NOVEL_FORGE_TTS_SCRIPT_GENERATION_TOP_P"] = str(
        _float_value(w, "_tts_script_generation_top_p")
    )
    pairs["NOVEL_FORGE_TTS_REVIEW_ADJUDICATION_TEMPERATURE"] = str(
        _float_value(w, "_tts_review_adjudication_temperature")
    )
    pairs["NOVEL_FORGE_TTS_NARRATOR_PROFILE_TEMPERATURE"] = str(
        _float_value(w, "_tts_narrator_profile_temperature")
    )
    pairs["NOVEL_FORGE_TTS_SOUND_DESIGN_TEMPERATURE"] = str(
        _float_value(w, "_tts_sound_design_temperature")
    )
    pairs["NOVEL_FORGE_TTS_SOUND_DESIGN_TOP_P"] = str(_float_value(w, "_tts_sound_design_top_p"))
    pairs["NOVEL_FORGE_TTS_MONTHLY_COST_BUDGET_USD"] = str(_float_value(w, "_tts_monthly_budget"))
    pairs["NOVEL_FORGE_TTS_BOOK_COST_BUDGET_USD"] = str(_float_value(w, "_tts_book_budget"))
    pairs["NOVEL_FORGE_AUDIO_BUDGET_LIMIT_USD"] = str(_float_value(w, "_tts_chapter_budget"))
    pairs["NOVEL_FORGE_TTS_PROJECT_MAX_STORAGE_MB"] = str(
        _spin_value(w, "_tts_project_max_storage")
    )
    pairs["NOVEL_FORGE_TTS_DASHSCOPE_API_KEY"] = _line_text(w, "_tts_dashscope_api_key")
    pairs["NOVEL_FORGE_TTS_DASHSCOPE_BASE_URL"] = _line_text(w, "_tts_dashscope_base_url")
    pairs["NOVEL_FORGE_TTS_DASHSCOPE_MODEL"] = _combo_text(w, "_tts_dashscope_formal_model")
    pairs["NOVEL_FORGE_TTS_DASHSCOPE_PREVIEW_MODEL"] = _combo_text(
        w, "_tts_dashscope_preview_model"
    )
    pairs["NOVEL_FORGE_TTS_DASHSCOPE_VOICE_CLONE_MODEL"] = _combo_text(
        w, "_tts_dashscope_clone_model"
    )
    pairs["NOVEL_FORGE_TTS_DASHSCOPE_VOICE_DESIGN_MODEL"] = _combo_text(
        w, "_tts_dashscope_design_model"
    )
    pairs["NOVEL_FORGE_TTS_DASHSCOPE_OPTIMIZE_INSTRUCTIONS"] = _combo_text(
        w, "_tts_dashscope_optimize_instructions"
    )
    pairs["NOVEL_FORGE_TTS_DASHSCOPE_VOICE_CLONE_ENABLE_PREPROCESS"] = _combo_text(
        w, "_tts_dashscope_clone_preprocess"
    )
    pairs["NOVEL_FORGE_TTS_DASHSCOPE_CNY_PER_USD"] = str(
        _float_value(w, "_tts_dashscope_cny_per_usd")
    )
    pairs["NOVEL_FORGE_AUDIO_QUALITY_PRESET"] = _combo_text(w, "_audio_quality_preset")
    pairs["NOVEL_FORGE_AUDIO_LOCATION_POLICY"] = _combo_text(w, "_audio_location_policy")

    page_settings = getattr(page, "_settings", None)
    raw_audio_overrides = str(getattr(page_settings, "audio_plugin_overrides", "{}") or "{}")
    try:
        parsed_audio_overrides = json.loads(raw_audio_overrides)
    except (TypeError, ValueError):
        parsed_audio_overrides = {}
    audio_overrides = (
        {str(key): str(value) for key, value in parsed_audio_overrides.items()}
        if isinstance(parsed_audio_overrides, dict)
        else {}
    )
    # Bailian's four TTS routes are model-bound settings above.  Drop legacy
    # plugin locks so they cannot silently override the newly selected models;
    # constraints_from_settings will rebuild exact plugin routes from them.
    for model_bound_stage in ("voice_design", "voice_clone", "tts_preview", "tts_formal"):
        audio_overrides.pop(model_bound_stage, None)
    route_combos = w.get("_audio_plugin_route_combos")
    if isinstance(route_combos, dict):
        for stage, combo in route_combos.items():
            stage_key = str(getattr(stage, "value", stage))
            audio_overrides.pop(stage_key, None)
            try:
                plugin_id = str(combo.currentData() or "")
            except AttributeError:
                plugin_id = ""
            if plugin_id:
                audio_overrides[stage_key] = plugin_id
    pairs["NOVEL_FORGE_AUDIO_PLUGIN_OVERRIDES"] = json.dumps(
        audio_overrides, ensure_ascii=False, sort_keys=True
    )
    pairs["NOVEL_FORGE_TTS_MINIMAX_API_KEY"] = _line_text(w, "_tts_minimax_api_key")
    pairs["NOVEL_FORGE_TTS_MINIMAX_BASE_URL"] = _line_text(w, "_tts_minimax_base_url")
    pairs["NOVEL_FORGE_TTS_MINIMAX_BITRATE"] = _combo_text(w, "_tts_minimax_bitrate")
    pairs["NOVEL_FORGE_TTS_MINIMAX_FORCE_CBR"] = _combo_text(w, "_tts_minimax_force_cbr")

    return pairs


def _read_existing_env_keys(env_path: Path) -> set[str]:
    """Read all key names currently present in the .env file."""
    if not env_path.is_file():
        return set()
    keys: set[str] = set()
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        keys.add(stripped.split("=", 1)[0].strip())
    return keys


def _compute_orphaned_env_keys(page: Any) -> set[str]:
    """Compute .env keys that belong to deleted profiles and should be stripped.

    After a profile is removed from ``page._config`` its API-key env var may
    still linger in .env.  If left there, ``_merge_env_discovered_profiles``
    will auto-recreate the profile on next load, making deletion ineffective.
    """
    managed_keys = page._config.managed_api_key_env_vars()
    env_path = getattr(page._store, "env_path", None)
    if env_path is None:
        return set()
    existing_keys = _read_existing_env_keys(env_path)

    # Per-profile dynamic keys (NOVEL_FORGE_PROFILE_API_KEY_*) that are no longer managed
    orphaned_profile_keys = {
        k
        for k in existing_keys
        if k.startswith(_PROFILE_API_KEY_PREFIX + "_") and k not in managed_keys
    }

    # Standard provider keys whose provider no longer has any profile at all
    current_providers = {p.provider for p in page._config.profiles}
    orphaned_standard_keys = {
        env_key
        for provider, env_key in _ENV_KEY_MAP.items()
        if env_key not in managed_keys
        and env_key in existing_keys
        and provider not in current_providers
    }

    return orphaned_profile_keys | orphaned_standard_keys


def save_settings(page: Any, *, silent: bool = False) -> bool:
    """Persist model profiles + routing + parameters to files."""
    blocked_tasks: list[str] = []
    blocked_fallback_tasks: list[str] = []
    for task_key, row_widget in page._route_rows.items():
        route = row_widget.get_route()
        fallback_routes = row_widget.get_fallback_routes()
        if route is not None:
            profile = page._config.get_profile(route.profile_id)
            if (
                profile is None
                or not profile.is_key_configured
                or is_embedding_model(profile.provider, profile.model_id)
            ):
                blocked_tasks.append(task_key)
                if task_key in page._config.routes:
                    del page._config.routes[task_key]
                continue
            page._config.routes[task_key] = route
        elif task_key in page._config.routes:
            del page._config.routes[task_key]

        valid_fallbacks: list[TaskRouteEntry] = []
        for fb in fallback_routes:
            profile = page._config.get_profile(fb.profile_id)
            if (
                profile is None
                or not profile.is_key_configured
                or is_embedding_model(profile.provider, profile.model_id)
            ):
                blocked_fallback_tasks.append(task_key)
                continue
            valid_fallbacks.append(fb)
        if valid_fallbacks:
            page._config.fallback_routes[task_key] = valid_fallbacks[:3]
        elif task_key in page._config.fallback_routes:
            del page._config.fallback_routes[task_key]

    for group_key, bulk_row in page._group_bulk_rows.items():
        route = bulk_row.get_bulk_route()
        if route is None:
            if group_key in page._config.group_bulk_routes:
                del page._config.group_bulk_routes[group_key]
            continue
        profile = page._config.get_profile(route.profile_id)
        if (
            profile is None
            or not profile.is_key_configured
            or is_embedding_model(profile.provider, profile.model_id)
        ):
            if group_key in page._config.group_bulk_routes:
                del page._config.group_bulk_routes[group_key]
            continue
        valid_bulk_fallbacks: list[TaskRouteEntry] = []
        for fb in bulk_row.get_bulk_fallback_routes():
            fb_profile = page._config.get_profile(fb.profile_id)
            if (
                fb_profile is None
                or not fb_profile.is_key_configured
                or is_embedding_model(fb_profile.provider, fb_profile.model_id)
            ):
                continue
            valid_bulk_fallbacks.append(fb)
        page._config.group_bulk_routes[group_key] = {
            "profile_id": route.profile_id,
            "thinking": route.thinking,
            "thinking_mode": route.thinking_mode,
            "multi_turn": route.multi_turn,
            "fallback_routes": [asdict(e) for e in valid_bulk_fallbacks],
        }

    if blocked_tasks:
        names = "、".join(blocked_tasks)
        show_warning_message(
            page,
            "存在不可用于生成的路由",
            f"以下步骤选择了未配置 API Key 或嵌入模型，已自动跳过保存：\n{names}",
        )
    if blocked_fallback_tasks:
        names = "、".join(sorted(set(blocked_fallback_tasks)))
        show_warning_message(
            page,
            "存在不可用于生成的备用路由",
            f"以下步骤的备用路由包含未配置 API Key 或嵌入模型，已自动跳过：\n{names}",
        )

    ollama_generation_model = _line_text(page._param_widgets, "_ollama_model")
    if ollama_generation_model and is_embedding_model("ollama", ollama_generation_model):
        if not silent:
            show_warning_message(
                page,
                "Ollama 模型类型不匹配",
                f"生成模型不能使用嵌入模型：{ollama_generation_model}",
            )
        return False

    ollama_embedding_model = _line_text(page._param_widgets, "_ollama_embedding_model")
    if ollama_embedding_model and not is_embedding_model("ollama", ollama_embedding_model):
        if not silent:
            show_warning_message(
                page,
                "Ollama 模型类型不匹配",
                f"嵌入模型不能使用生成模型：{ollama_embedding_model}",
            )
        return False

    env_pairs = collect_param_env_pairs(page)
    routing_env_pairs = page._config.to_env_pairs()
    env_pairs.update(routing_env_pairs)

    # Compute orphaned env keys from deleted profiles so they are stripped from .env.
    # Without this, _merge_env_discovered_profiles would resurrect deleted profiles.
    strip_env_keys = _compute_orphaned_env_keys(page)

    save_result = page._store.save(
        config=page._config,
        env_pairs=env_pairs,
        blocked_tasks=blocked_tasks,
        strip_env_keys=strip_env_keys,
    )
    # Invalidate the module-level profiles cache so the next load_or_import_profiles()
    # call re-reads from disk and does not return a stale config.
    invalidate_profiles_cache()
    reload_store = getattr(page._store, "reload", None)
    if callable(reload_store):
        reload_store()
    if hasattr(page, "_settings"):
        page._settings = get_settings()
    page._last_saved_signature = _build_signature(page)
    page.settings_saved.emit()
    if not silent:
        if save_result.env_path is None:
            show_warning_message(
                page,
                "部分保存成功",
                f"模型配置已保存到 {save_result.profiles_path.name}\n"
                "未找到 .env，因此仅保存了模型/路由配置；运行参数保持现状。",
            )
        else:
            show_info_message(
                page,
                "保存成功",
                f"模型与路由已保存到 {save_result.profiles_path.name}\n"
                f"运行参数已写入 {save_result.env_path.name}\n"
                "新的任务提交会立即读取这两处最新配置。",
            )
    return True


def _build_signature(page: Any) -> str:
    route_map: dict[str, dict[str, object]] = {}
    for task_key, row_widget in page._route_rows.items():
        route = row_widget.get_route()
        if route is None:
            continue
        route_map[task_key] = {
            "profile_id": route.profile_id,
            "thinking": route.thinking,
            "thinking_mode": route.thinking_mode,
            "multi_turn": route.multi_turn,
        }
    fallback_map: dict[str, list[dict[str, object]]] = {}
    for task_key, row_widget in page._route_rows.items():
        entries = row_widget.get_fallback_routes()
        if not entries:
            continue
        fallback_map[task_key] = [
            {
                "profile_id": entry.profile_id,
                "thinking": entry.thinking,
                "thinking_mode": entry.thinking_mode,
                "multi_turn": entry.multi_turn,
            }
            for entry in entries
        ]
    group_bulk_routes: dict[str, dict[str, object]] = {}
    for group_key, bulk_row in page._group_bulk_rows.items():
        route = bulk_row.get_bulk_route()
        if route is None:
            continue
        group_bulk_routes[group_key] = {
            "profile_id": route.profile_id,
            "thinking": route.thinking,
            "thinking_mode": route.thinking_mode,
            "multi_turn": route.multi_turn,
            "fallback_routes": [asdict(e) for e in bulk_row.get_bulk_fallback_routes()],
        }

    payload = {
        "profiles": [
            {
                "profile_id": p.profile_id,
                "display_name": p.display_name,
                "provider": p.provider,
                "model_id": p.model_id,
                "api_key": p.api_key,
                "base_url": p.base_url,
            }
            for p in page._config.profiles
        ],
        "routes": route_map,
        "fallback_routes": fallback_map,
        "default_profile_id": page._config.default_profile_id,
        "params": collect_param_env_pairs(page),
        "group_bulk_routes": group_bulk_routes,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _merge_materialized_signature(
    baseline_signature: str,
    before_signature: str,
    after_signature: str,
) -> str:
    """Extend a saved baseline with fields created by lazy UI materialization.

    Building a deferred settings section changes the shape of the UI-backed
    signature even when the user has not edited anything. Only copy values
    whose representation changed during that synchronous build. Values that
    already differed before the build remain untouched, so genuine user edits
    continue to compare dirty afterwards.
    """
    try:
        baseline = json.loads(baseline_signature)
        before = json.loads(before_signature)
        after = json.loads(after_signature)
    except (TypeError, json.JSONDecodeError):
        return baseline_signature
    if not all(isinstance(payload, dict) for payload in (baseline, before, after)):
        return baseline_signature

    missing = object()
    for field in ("params", "routes", "fallback_routes", "group_bulk_routes"):
        baseline_values = baseline.get(field)
        before_values = before.get(field)
        after_values = after.get(field)
        if not all(
            isinstance(values, dict) for values in (baseline_values, before_values, after_values)
        ):
            continue
        for key in set(before_values) | set(after_values):
            if before_values.get(key, missing) == after_values.get(key, missing):
                continue
            if key in after_values:
                baseline_values[key] = after_values[key]
            else:
                baseline_values.pop(key, None)

    return json.dumps(baseline, ensure_ascii=False, sort_keys=True)
