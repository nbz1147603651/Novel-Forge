"""Phase 1: LLM script generation with rule-based fallback.

Orchestrates batch LLM generation, per-batch fallback, source reconciliation,
and script merging.  All heavy lifting is delegated to infrastructure methods
on the ``step`` instance (``_call_with_retry``, ``_generate_script_llm``, etc.)
so that this module stays focused on phase-level flow control.

Author: novel-forge
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from novel_forge.obs.logger import get_logger

if TYPE_CHECKING:
    from novel_forge.tts.pipeline.generate_script_step import (
        GenerateDubbingScriptInput,
        GenerateDubbingScriptStep,
        ScriptGenerationContext,
        _QuoteReviewCandidate,
    )
    from novel_forge.tts.schemas import DubbingScript

_log = get_logger("tts.pipeline.script_phases.generate")


async def phase_generate(
    input_data: GenerateDubbingScriptInput,
    ctx: ScriptGenerationContext,
    step: GenerateDubbingScriptStep,
) -> tuple[DubbingScript, list[_QuoteReviewCandidate]]:
    """Execute Phase 1: LLM generation + rule-based fallback + source reconciliation.

    Returns:
        A tuple of (script, speaker_candidates) where speaker_candidates are
        ambiguous quotes requiring Phase 2 adjudication.
    """
    character_map = step._build_character_map(
        input_data.voice_team, input_data.character_voices
    )
    ctx.character_names = {
        entry.character_id: entry.character_name
        for entry in input_data.voice_team.entries
        if entry.character_id
    }
    ctx.speaker_adjudication_cards = step._build_speaker_adjudication_cards(
        input_data.character_voices,
        input_data.voice_team,
    )

    script = await step._generate_script_llm(input_data, character_map)

    speaker_candidates: list[Any] = []
    if script is not None:
        try:
            script, speaker_candidates = step._reconcile_script_with_source(
                script,
                input_data.chapter_text,
                character_map,
            )
        except ValueError as exc:
            _log.warning("LLM script source reconciliation failed: %s", exc)
            step._on_step_event(
                "tts_script_llm_rejected",
                {
                    "chapter": input_data.chapter_number,
                    "reason": str(exc),
                    "stage": "source_reconciliation",
                },
            )
            script = None
            speaker_candidates = []

    if script is None:
        _log.warning("LLM script generation failed, falling back to rule-based")
        step._on_step_event(
            "tts_script_rule_fallback",
            {"chapter": input_data.chapter_number, "reason": "llm_generation_failed"},
        )
        script = await step._generate_script_rule_based(
            input_data.chapter_text,
            input_data.chapter_number,
            character_map,
        )
        script, speaker_candidates = step._reconcile_script_with_source(
            script,
            input_data.chapter_text,
            character_map,
        )

    return script, speaker_candidates
