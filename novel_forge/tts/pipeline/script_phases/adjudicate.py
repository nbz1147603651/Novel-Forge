"""Phase 2: Speaker adjudication for ambiguous quotes.

Resolves ambiguous quote roles via LLM adjudication, determining whether
quoted text is spoken dialogue, inner thought, or narration, and assigning
the correct speaker character.

Author: novel-forge
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from novel_forge.obs.logger import get_logger

if TYPE_CHECKING:
    from novel_forge.tts.pipeline.generate_script_step import (
        GenerateDubbingScriptInput,
        GenerateDubbingScriptStep,
        _QuoteReviewCandidate,
    )
    from novel_forge.tts.schemas import DubbingScript

_log = get_logger("tts.pipeline.script_phases.adjudicate")


async def phase_adjudicate(
    script: DubbingScript,
    speaker_candidates: list[_QuoteReviewCandidate],
    input_data: GenerateDubbingScriptInput,
    step: GenerateDubbingScriptStep,
) -> DubbingScript:
    """Execute Phase 2: Resolve ambiguous quote roles via LLM adjudication.

    Args:
        script: The script from Phase 1.
        speaker_candidates: Ambiguous quotes needing role/speaker resolution.
        input_data: Original generation input (carries voice_team).
        step: Step instance for infrastructure access.

    Returns:
        Script with adjudicated segment roles and speakers.
    """
    return await step._adjudicate_ambiguous_quote_roles(
        script,
        speaker_candidates,
        input_data.voice_team,
    )
