"""Shared workspace execution helpers for API and desktop entrypoints.

This module is a thin re-export shim. All business logic lives in the
execution_* submodules so that each entrypoint (CLI/API/Desktop) imports
from a single stable location while the implementation can evolve in
smaller, focused files.
"""

from __future__ import annotations

from novel_forge.obs.logger import get_logger
from novel_forge.workspace.execution_result import ExecutionResult, StepCallback

_log = get_logger("workspace.execution")


# Re-export public runners
# Re-export book consistency / reevaluation
from novel_forge.workspace.book_ops.execution_book_editorial import (  # noqa: E402
    execute_book_editorial_audit,
)
from novel_forge.workspace.book_ops.execution_book_entry import (  # noqa: E402
    execute_book_consistency,
    execute_global_repair_queue,
    run_book_consistency_audit,
)
from novel_forge.workspace.book_ops.execution_book_reevaluate import (  # noqa: E402
    execute_reevaluate_chapter,
)
from novel_forge.workspace.book_ops.execution_book_repair import (  # noqa: E402
    _run_book_consistency_auto_repair as run_book_consistency_repair,
)
from novel_forge.workspace.book_ops.execution_book_verify import (  # noqa: E402
    _run_book_consistency_verify as run_book_consistency_verify,
)

# Re-export export
from novel_forge.workspace.execution_export import execute_export_book  # noqa: E402

# Re-export outline extension
from novel_forge.workspace.execution_extend_outline import execute_extend_outline  # noqa: E402

# Re-export manual revision
from novel_forge.workspace.execution_manual_revision import execute_manual_revision  # noqa: E402

# Re-export memory maintenance
from novel_forge.workspace.execution_memory import execute_rebuild_memory_vectors  # noqa: E402

# Re-export polish
from novel_forge.workspace.execution_polish import execute_polish_chapter  # noqa: E402

# Re-export repair operations
from novel_forge.workspace.execution_repair import (  # noqa: E402
    execute_repair,
    execute_repair_motif_history,
)
from novel_forge.workspace.helpers.execution_runners import (  # noqa: E402
    create_project_workspace,
    execute_init_long,
    execute_prepare_chapter,
    execute_reextract_relationships,
    execute_resolve_chapter_checkpoint,
    execute_run_chapter,
    execute_run_short,
    execute_sync_chapter_contracts,
    short_spec_input_from_request,
)
from novel_forge.workspace.helpers.execution_state import _source_text_hash  # noqa: E402,F401

# Re-export TTS operations
from novel_forge.workspace.tts_ops.execution import (  # noqa: E402
    execute_accept_segment_take,
    execute_build_narrator_profile,
    execute_build_voice_team,
    execute_clone_character_voice,
    execute_design_character_voice,
    execute_export_audio_delivery,
    execute_export_audiobook_delivery,
    execute_export_audiobook_package,
    execute_full_tts_pipeline,
    execute_generate_dubbing_script,
    execute_list_tts_voices,
    execute_prepare_voice_team_previews,
    execute_preview_character_voice,
    execute_preview_narrator_voice,
    execute_reassemble_chapter_audio,
    execute_resolve_dubbing_speakers,
    execute_synthesize_chapter,
    execute_synthesize_segment,
)

__all__ = [
    "ExecutionResult",
    "StepCallback",
    "create_project_workspace",
    "execute_accept_segment_take",
    "execute_book_consistency",
    "execute_book_editorial_audit",
    "execute_build_narrator_profile",
    "execute_build_voice_team",
    "execute_clone_character_voice",
    "execute_design_character_voice",
    "execute_export_audio_delivery",
    "execute_export_audiobook_delivery",
    "execute_extend_outline",
    "execute_export_audiobook_package",
    "execute_export_book",
    "execute_full_tts_pipeline",
    "execute_generate_dubbing_script",
    "execute_global_repair_queue",
    "execute_init_long",
    "execute_list_tts_voices",
    "execute_prepare_voice_team_previews",
    "execute_manual_revision",
    "execute_polish_chapter",
    "execute_prepare_chapter",
    "execute_preview_character_voice",
    "execute_preview_narrator_voice",
    "execute_rebuild_memory_vectors",
    "execute_reassemble_chapter_audio",
    "execute_resolve_dubbing_speakers",
    "execute_reevaluate_chapter",
    "execute_reextract_relationships",
    "execute_repair",
    "execute_repair_motif_history",
    "execute_resolve_chapter_checkpoint",
    "execute_run_chapter",
    "execute_run_short",
    "execute_synthesize_chapter",
    "execute_synthesize_segment",
    "execute_sync_chapter_contracts",
    "run_book_consistency_audit",
    "run_book_consistency_repair",
    "run_book_consistency_verify",
    "short_spec_input_from_request",
]
