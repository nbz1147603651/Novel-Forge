"""Phase 7: Finalization — voice direction enrichment, platform contract, hash.

Final pass that enriches voice direction metadata, applies the TTS platform
contract (provider/model-specific constraints), computes content hashes for
change detection, and emits the final script-parsed event.

Author: novel-forge
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from novel_forge.obs.logger import get_logger
from novel_forge.tts.script_integrity import compute_source_text_hash
from novel_forge.tts.script_review import apply_tts_platform_contract

if TYPE_CHECKING:
    from novel_forge.tts.pipeline.generate_script_step import (
        GenerateDubbingScriptInput,
        GenerateDubbingScriptStep,
        ScriptGenerationContext,
    )
    from novel_forge.tts.schemas import DubbingScript

_log = get_logger("tts.pipeline.script_phases.finalize")


def phase_finalize(
    script: DubbingScript,
    input_data: GenerateDubbingScriptInput,
    ctx: ScriptGenerationContext,
    step: GenerateDubbingScriptStep,
) -> DubbingScript:
    """Execute Phase 7: Voice direction enrichment, platform contract, hash.

    Args:
        script: The sound-designed script from Phase 6.
        input_data: Original generation input (carries chapter_text, chapter_number).
        ctx: Generation context (carries story_context, character_names).
        step: Step instance for settings and event emission.

    Returns:
        Finalized script ready for persistence and downstream synthesis.
    """
    # Enrich voice direction for all segments
    script = script.model_copy(
        update={
            "segments": [
                step._enrich_voice_direction(segment) for segment in script.segments
            ],
            "metadata": {
                **script.metadata,
                "story_sound_context": dict(ctx.story_context),
                "target_tts": {
                    "provider": input_data.target_provider or step._settings.tts_default_provider,
                    "model": input_data.target_model or step._settings.tts_default_model,
                },
                **(
                    {
                        "reference_dubbing_style": {
                            "profile_id": input_data.reference_style_profile.profile_id,
                            "source_name": input_data.reference_style_profile.source_name,
                            "source_hash": input_data.reference_style_profile.source_hash,
                            "analysis_mode": input_data.reference_style_profile.analysis_mode,
                            "confidence": input_data.reference_style_profile.confidence,
                            "strength": input_data.reference_style_strength,
                        }
                    }
                    if input_data.reference_style_profile is not None
                    and input_data.reference_style_strength > 0.0
                    else {}
                ),
            },
        }
    )

    # Apply TTS platform contract (provider/model-specific constraints)
    script = apply_tts_platform_contract(
        script,
        provider_id=input_data.target_provider or step._settings.tts_default_provider,
        model_id=input_data.target_model or step._settings.tts_default_model,
    )

    # Compute content hashes for change detection
    script.script_hash = step._compute_hash(script)
    script.source_text_hash = compute_source_text_hash(input_data.chapter_text)

    # Emit final event
    step._on_step_event(
        "tts_script_parsed",
        {
            "chapter": input_data.chapter_number,
            "segment_count": len(script.segments),
            "character_count": len(ctx.character_names),
        },
    )

    _log.info(
        "Dubbing script generated: %d segments, %d dialogue, %d narration, "
        "%d soundscapes, %d bgm, %d sfx, %d transitions",
        len(script.segments),
        len(script.dialogue_segments),
        len(script.narration_segments),
        len(script.soundscapes),
        len(script.bgm_suggestions),
        len(script.sfx_cues),
        len(script.scene_transitions),
    )

    return script
