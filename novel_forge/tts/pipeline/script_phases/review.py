"""Phase 4: Rule-based review + LLM expert review.

Two-stage review: deterministic rule-based repair (numeric overrides, SFX
recovery, humanization) followed by optional LLM expert performance review
that applies targeted repairs via structured decisions.

Author: novel-forge
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from novel_forge.obs.logger import get_logger
from novel_forge.tts.script_review import review_and_repair_dubbing_script

if TYPE_CHECKING:
    from novel_forge.tts.pipeline.generate_script_step import (
        GenerateDubbingScriptInput,
        GenerateDubbingScriptStep,
    )
    from novel_forge.tts.schemas import DubbingScript
    from novel_forge.tts.script_stage_context import ScriptStageContext

_log = get_logger("tts.pipeline.script_phases.review")


def phase_review_rules(
    script: DubbingScript,
    input_data: GenerateDubbingScriptInput,
    step: GenerateDubbingScriptStep,
) -> DubbingScript:
    """Execute Phase 4a: Deterministic rule-based review and repair.

    Applies humanization library, removes numeric overrides, recovers
    event SFX, and repairs common LLM generation artifacts.

    Args:
        script: The normalized script from Phase 3.
        input_data: Original generation input.
        step: Step instance for event emission.

    Returns:
        Repaired script with rule-based fixes applied.
    """
    script = review_and_repair_dubbing_script(script, use_humanize_library=True)
    review = script.metadata.get("professional_script_review")
    step._on_step_event(
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


async def phase_review_llm(
    script: DubbingScript,
    input_data: GenerateDubbingScriptInput,
    script_stage_context: ScriptStageContext,
    step: GenerateDubbingScriptStep,
) -> DubbingScript:
    """Execute Phase 4b: Expert LLM review of the dubbing script.

    Runs the independently routed dubbing-specific performance review that
    applies targeted repairs via structured decisions.

    Args:
        script: The rule-reviewed script.
        input_data: Original generation input (carries voice_team).
        script_stage_context: Stage context for prompt projection.
        step: Step instance for LLM infrastructure.

    Returns:
        Script with LLM review decisions applied.
    """
    return await step._review_dubbing_script_professionally(
        script,
        input_data.voice_team,
        script_stage_context,
    )
