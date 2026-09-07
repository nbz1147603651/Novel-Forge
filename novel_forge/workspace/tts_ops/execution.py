"""TTS workspace execution entry point (re-export shim).

The implementation was split by domain into execution_shared /
execution_assets / execution_voice_team / execution_script /
execution_synthesis / execution_export.  This module re-exports every
symbol so existing API routes, CLI commands and Desktop workers keep
working unchanged.

Domain dependency graph (acyclic):
    shared <- assets <- voice_team <- script <- synthesis <- export
"""

from __future__ import annotations

from novel_forge.workspace.tts_ops.execution_assets import (  # noqa: F401
    _allows_parallel_voice_build,
    _apply_preview_audio_controls,
    _clear_voice_library_activation_deadlines,
    _existing_preview_cache_path,
    _load_audio_execution_plan,
    _load_or_freeze_audio_execution_plan,
    _persist_voice_library_updates,
    _preview_cache_path,
    _preview_format,
    _recent_audio_reference_lufs,
    _sync_narrator_voice_team_binding,
    _synthesize_preview_with_timeout,
    _write_preview_audio,
)
from novel_forge.workspace.tts_ops.execution_export import (  # noqa: F401
    execute_export_audio_delivery,
    execute_export_audiobook_delivery,
    execute_export_audiobook_package,
    execute_full_tts_pipeline,
    execute_reassemble_chapter_audio,
)
from novel_forge.workspace.tts_ops.execution_script import (  # noqa: F401
    _authoritative_text_mismatch,
    _coerce_nonnegative_int,
    _repair_unresolved_speakers,
    _script_source_audit_error,
    _script_source_mismatch,
    execute_analyze_dubbing_style_reference,
    execute_generate_dubbing_script,
    execute_resolve_dubbing_speakers,
    execute_save_dubbing_script,
    tts_artifact_source_mismatch,
    tts_audio_result_script_hash,
    tts_audio_result_script_mismatch,
    tts_audio_result_source_hash,
)
from novel_forge.workspace.tts_ops.execution_shared import (  # noqa: F401
    REASON_CLONE_NOT_READY,
    REASON_EXPIRED,
    REASON_MISSING,
    REASON_NARRATOR_UNAVAILABLE,
    REASON_PENDING_APPROVAL,
    REASON_PROVIDER_MISMATCH,
    REASON_UNKNOWN,
    RETRYABLE_REASONS,
    _emit_tts_progress,
    _ensure_managed_plan_runtimes,
    _ensure_managed_tts_runtime,
    _load_json,
    _load_tts_upstream_context,
    _load_voice_team,
    _merge_character_inputs,
    _normalize_style_profile,
    _resolve_provider,
    _source_text_hash,
    _stable_hash,
)
from novel_forge.workspace.tts_ops.execution_synthesis import (  # noqa: F401
    execute_accept_segment_take,
    execute_synthesize_chapter,
    execute_synthesize_segment,
)
from novel_forge.workspace.tts_ops.execution_voice_team import (  # noqa: F401
    _activate_minimax_narrator,
    _activate_minimax_team_voices,
    _activate_minimax_voice,
    _apply_execution_tts_assignment,
    _clear_activated_voice_deadlines,
    _diagnose_narrator_reuse,
    _diagnose_voice_team_reuse,
    _lock_used_voice_identities,
    _replace_voice_entry,
    _voice_team_content_hash,
    execute_approve_character_voice,
    execute_assign_catalog_voice,
    execute_build_narrator_profile,
    execute_build_voice_team,
    execute_clone_character_voice,
    execute_confirm_voice_team,
    execute_design_character_voice,
    execute_list_tts_voices,
    execute_prepare_voice_team_previews,
    execute_preview_character_voice,
    execute_preview_narrator_voice,
    execute_update_voice_performance,
)
from novel_forge.workspace.tts_ops.lock_manager import (  # noqa: F401
    bounded_preview_timeout as _bounded_preview_timeout,
)
from novel_forge.workspace.tts_ops.lock_manager import (
    tts_project_lock as _tts_project_lock,
)
from novel_forge.workspace.tts_ops.lock_manager import (
    tts_voice_library_lock as _tts_voice_library_lock,
)
from novel_forge.workspace.tts_ops.lock_manager import (
    with_interactive_preview_tts_lock as _with_interactive_preview_tts_lock,
)
from novel_forge.workspace.tts_ops.lock_manager import (
    with_tts_project_lock as _with_tts_project_lock,
)
from novel_forge.workspace.tts_ops.preflight import (  # noqa: F401
    _enrich_preflight_failure,
    _preflight_failure_guidance,
    _preflight_failure_severity,
)
from novel_forge.workspace.tts_ops.progress import (  # noqa: F401
    _persist_synthesis_progress_snapshot,
    _resolve_reusable_takes,
)
from novel_forge.workspace.tts_ops.voice_assignment import (  # noqa: F401
    _assign_narrator_voice,
    _narrator_profile_content_hash,
    _narrator_voice_match_input,
)

__all__ = [
    "_ensure_managed_tts_runtime",
    "_ensure_managed_plan_runtimes",
    "_emit_tts_progress",
    "_load_json",
    "_stable_hash",
    "_resolve_provider",
    "_load_voice_team",
    "REASON_MISSING",
    "REASON_PROVIDER_MISMATCH",
    "REASON_CLONE_NOT_READY",
    "REASON_PENDING_APPROVAL",
    "REASON_EXPIRED",
    "REASON_NARRATOR_UNAVAILABLE",
    "REASON_UNKNOWN",
    "RETRYABLE_REASONS",
    "_source_text_hash",
    "_normalize_style_profile",
    "_load_tts_upstream_context",
    "_merge_character_inputs",
    "_synthesize_preview_with_timeout",
    "_persist_voice_library_updates",
    "_clear_voice_library_activation_deadlines",
    "_recent_audio_reference_lufs",
    "_load_audio_execution_plan",
    "_load_or_freeze_audio_execution_plan",
    "_sync_narrator_voice_team_binding",
    "_preview_cache_path",
    "_existing_preview_cache_path",
    "_preview_format",
    "_write_preview_audio",
    "_allows_parallel_voice_build",
    "_apply_preview_audio_controls",
    "_voice_team_content_hash",
    "_apply_execution_tts_assignment",
    "_replace_voice_entry",
    "_diagnose_voice_team_reuse",
    "_diagnose_narrator_reuse",
    "_clear_activated_voice_deadlines",
    "_lock_used_voice_identities",
    "_activate_minimax_voice",
    "_activate_minimax_team_voices",
    "_activate_minimax_narrator",
    "execute_build_narrator_profile",
    "execute_build_voice_team",
    "execute_list_tts_voices",
    "execute_preview_character_voice",
    "execute_prepare_voice_team_previews",
    "execute_preview_narrator_voice",
    "execute_clone_character_voice",
    "execute_design_character_voice",
    "execute_approve_character_voice",
    "execute_update_voice_performance",
    "execute_assign_catalog_voice",
    "execute_confirm_voice_team",
    "tts_artifact_source_mismatch",
    "tts_audio_result_source_hash",
    "tts_audio_result_script_hash",
    "tts_audio_result_script_mismatch",
    "_authoritative_text_mismatch",
    "_script_source_mismatch",
    "_script_source_audit_error",
    "execute_save_dubbing_script",
    "execute_analyze_dubbing_style_reference",
    "execute_generate_dubbing_script",
    "_coerce_nonnegative_int",
    "execute_resolve_dubbing_speakers",
    "_repair_unresolved_speakers",
    "execute_synthesize_chapter",
    "execute_synthesize_segment",
    "execute_accept_segment_take",
    "execute_reassemble_chapter_audio",
    "execute_export_audio_delivery",
    "execute_export_audiobook_delivery",
    "execute_export_audiobook_package",
    "execute_full_tts_pipeline",
    "_bounded_preview_timeout",
    "_tts_project_lock",
    "_tts_voice_library_lock",
    "_with_interactive_preview_tts_lock",
    "_with_tts_project_lock",
    "_enrich_preflight_failure",
    "_preflight_failure_guidance",
    "_preflight_failure_severity",
    "_persist_synthesis_progress_snapshot",
    "_resolve_reusable_takes",
    "_assign_narrator_voice",
    "_narrator_profile_content_hash",
    "_narrator_voice_match_input",
]
