"""Phase 3: Normalization — merge continuations, fold micro-segments, validate fidelity.

Deterministic post-processing that repairs segmentation artifacts without
LLM calls: merges narrator fragments split inside unfinished clauses, folds
single-character dialogue blips, and validates source-text fidelity.

Author: novel-forge
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from novel_forge.obs.logger import get_logger

if TYPE_CHECKING:
    from novel_forge.tts.pipeline.generate_script_step import (
        GenerateDubbingScriptInput,
        GenerateDubbingScriptStep,
    )
    from novel_forge.tts.schemas import DubbingScript

_log = get_logger("tts.pipeline.script_phases.normalize")


def phase_normalize(
    script: DubbingScript,
    input_data: GenerateDubbingScriptInput,
    character_map: dict[str, str],
    step: GenerateDubbingScriptStep,
) -> DubbingScript:
    """Execute Phase 3: Merge continuations, fold single-char dialogue, validate fidelity.

    Args:
        script: The adjudicated script from Phase 2.
        input_data: Original generation input (carries chapter_text).
        character_map: Character alias → canonical ID mapping.
        step: Step instance for infrastructure access.

    Returns:
        Normalized script with repaired segmentation.
    """
    # Step 1: Merge narration continuations (fragments split inside clauses)
    script, _continuation_index_map, continuation_merge_count = (
        step._merge_narration_continuations(script)
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

    # Step 2: Fold single-character dialogue into adjacent segments
    script, single_char_fold_count = step._fold_single_char_dialogue(script)
    if single_char_fold_count:
        metadata = dict(script.metadata)
        reconciliation = metadata.get("source_reconciliation")
        if isinstance(reconciliation, dict):
            metadata["source_reconciliation"] = {
                **reconciliation,
                "single_char_dialogue_folded": single_char_fold_count,
            }
        else:
            metadata["source_reconciliation"] = {
                "single_char_dialogue_folded": single_char_fold_count,
            }
        script = script.model_copy(update={"metadata": metadata})

    # Step 3: Validate source fidelity (throws ValueError on failure)
    step._validate_script_source_fidelity(
        script,
        input_data.chapter_text,
        character_map,
    )

    return script
