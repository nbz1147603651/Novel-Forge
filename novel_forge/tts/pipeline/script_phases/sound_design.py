"""Phase 6: Sound design extraction and creative bible application.

Extracts sound design elements (BGM, SFX, soundscapes) from the script,
applies the audio creative bible, and backfills ambient soundscapes when
the script lacks them.

Author: novel-forge
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from novel_forge.obs.logger import get_logger
from novel_forge.tts.creative_direction import apply_audio_creative_bible

if TYPE_CHECKING:
    from novel_forge.tts.pipeline.generate_script_step import (
        GenerateDubbingScriptInput,
        GenerateDubbingScriptStep,
        ScriptGenerationContext,
    )
    from novel_forge.tts.schemas import DubbingScript

_log = get_logger("tts.pipeline.script_phases.sound_design")


async def phase_sound_design(
    script: DubbingScript,
    input_data: GenerateDubbingScriptInput,
    ctx: ScriptGenerationContext,
    step: GenerateDubbingScriptStep,
) -> DubbingScript:
    """Execute Phase 6: Sound design extraction + creative bible application.

    Args:
        script: The rewritten script from Phase 5.
        input_data: Original generation input (carries audio_creative_bible, library_assets).
        ctx: Generation context (carries story_context).
        step: Step instance for LLM infrastructure.

    Returns:
        Script with sound design elements extracted and applied.
    """
    from novel_forge.tts.sound_design_extraction import extract_sound_design  # noqa: PLC0415

    script = await extract_sound_design(
        script,
        bible=input_data.audio_creative_bible,
        library_assets=input_data.library_assets,
        story_context=ctx.story_context,
        scene_intents=input_data.scene_intents or [],
        location_sound_seeds=input_data.location_acoustics,
        reference_style_profile=input_data.reference_style_profile,
        reference_style_strength=input_data.reference_style_strength,
        upstream_revision=input_data.upstream_revision,
        router=step._router,
        builder=step._builder,
        settings=step._settings,
    )

    # Backfill ambient soundscapes if the script has none
    script = step._ensure_soundscape_design(script)

    # Apply audio creative bible constraints
    if input_data.audio_creative_bible is not None:
        script = apply_audio_creative_bible(
            script,
            input_data.audio_creative_bible,
            scene_count_hint=len(input_data.scene_intents or []),
        )

    # ── 叙事弧线（P3-5）────────────────────────────────────────────────────
    # 输出章节级旁白距离规划（开场 close / 发展 medium / 高潮按情绪强度决定
    # close 或 medium / 收束 distant），写入旁白段 narrator_distance；
    # finalize 阶段的 _enrich_voice_direction 会把距离映射到
    # vocal_direction.intimacy / breathiness / delivery_style。
    from novel_forge.tts.pipeline.narration_arc import apply_narration_arc  # noqa: PLC0415

    script = apply_narration_arc(
        script,
        enabled=bool(getattr(step._settings, "tts_narration_arc_enabled", True)),
    )

    return script
