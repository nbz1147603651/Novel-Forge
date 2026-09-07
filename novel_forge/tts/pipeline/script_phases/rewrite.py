"""Phase 5: Spoken-text rewrite with retry strategy.

Wraps the independent ``spoken_text_rewrite`` module with:
- **Retry strategy**: up to N rounds (``tts_spoken_rewrite_max_rounds``, default 3)
- **Exponential backoff**: 2s / 4s / 8s between rounds
- **Partial success merge**: each round retries only failed batches
- **Failure blocking**: after all rounds exhausted, raises ScriptCompletenessError

Author: novel-forge
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from novel_forge.obs.logger import get_logger
from novel_forge.tts.pipeline.script_phases import ScriptCompletenessError, ScriptCompletenessReport

if TYPE_CHECKING:
    from novel_forge.tts.pipeline.generate_script_step import (
        GenerateDubbingScriptInput,
        GenerateDubbingScriptStep,
    )
    from novel_forge.tts.schemas import DubbingScript
    from novel_forge.tts.script_stage_context import ScriptStageContext

_log = get_logger("tts.pipeline.script_phases.rewrite")


async def phase_rewrite(
    script: DubbingScript,
    input_data: GenerateDubbingScriptInput,
    script_stage_context: ScriptStageContext,
    step: GenerateDubbingScriptStep,
) -> DubbingScript:
    """Execute Phase 5: Spoken-text rewrite with retry strategy.

    Calls the independent ``rewrite_spoken_text`` module and retries on
    LLM call failures.  If all retry rounds are exhausted with failures
    still present, raises :class:`ScriptCompletenessError` to block the
    pipeline and prevent incomplete scripts from being persisted.

    Args:
        script: The reviewed script from Phase 4.
        input_data: Original generation input (carries voice_team, chapter_number).
        script_stage_context: Stage context for rewrite prompt projection.
        step: Step instance for LLM infrastructure and settings.

    Returns:
        Script with spoken_text fields populated by the rewrite pass.

    Raises:
        ScriptCompletenessError: When all retry rounds fail to produce
            acceptable spoken_text coverage.
    """
    from novel_forge.tts.spoken_text_rewrite import rewrite_spoken_text  # noqa: PLC0415

    max_rounds = int(getattr(step._settings, "tts_spoken_rewrite_max_rounds", 3))
    backoff_base = float(getattr(step._settings, "tts_spoken_rewrite_retry_backoff_s", 2.0))

    for round_idx in range(max_rounds):
        script = await rewrite_spoken_text(
            script,
            input_data.voice_team,
            router=step._router,
            builder=step._builder,
            settings=step._settings,
            context=script_stage_context,
            platform_id=(input_data.target_provider or step._settings.tts_default_provider),
            chapter_number=input_data.chapter_number,
            on_step=step._on_step_event,
        )

        # Check if there were LLM call failures in this round
        meta = script.metadata.get("spoken_text_rewrite", {})
        rejection_reasons = meta.get("rejection_reasons", {}) if isinstance(meta, dict) else {}
        llm_failed_count = int(rejection_reasons.get("llm_call_failed", 0)) + int(
            rejection_reasons.get("llm_call_failed_permanent", 0)
        )

        if llm_failed_count == 0:
            # No LLM failures — rewrite pass succeeded
            _log.info(
                "Spoken-text rewrite completed successfully on round %d/%d",
                round_idx + 1,
                max_rounds,
            )
            return script

        _log.warning(
            "Spoken-text rewrite round %d/%d had %d LLM call failures",
            round_idx + 1,
            max_rounds,
            llm_failed_count,
        )
        step._on_step_event(
            "tts_spoken_rewrite_retry",
            {
                "chapter": input_data.chapter_number,
                "round": round_idx + 1,
                "max_rounds": max_rounds,
                "llm_call_failed": llm_failed_count,
            },
        )

        # Early-exit on permanent route failure: retrying cannot resolve a
        # capability gap (e.g. no provider supports native structured output).
        if meta.get("permanent_route_failure"):
            _log.warning(
                "Spoken-text rewrite hit permanent route failure; "
                "skipping remaining %d round(s)",
                max_rounds - round_idx - 1,
            )
            break

        # Exponential backoff before next retry (skip after last round)
        if round_idx < max_rounds - 1:
            backoff_seconds = backoff_base * (2**round_idx)
            _log.info("Backing off %.1fs before retry round %d", backoff_seconds, round_idx + 2)
            await asyncio.sleep(backoff_seconds)

    # All retry rounds exhausted — check if we still have failures
    meta = script.metadata.get("spoken_text_rewrite", {})
    rejection_reasons = meta.get("rejection_reasons", {}) if isinstance(meta, dict) else {}
    llm_failed_count = int(rejection_reasons.get("llm_call_failed", 0)) + int(
        rejection_reasons.get("llm_call_failed_permanent", 0)
    )

    if llm_failed_count > 0:
        # Permanent route failure: degrade gracefully instead of blocking.
        # The spoken-text rewrite is an enhancement step; using the original
        # literary text as spoken_text is a safe fallback that keeps the
        # pipeline flowing.
        if isinstance(meta, dict) and meta.get("permanent_route_failure"):
            degraded_segments = [
                (
                    seg.model_copy(update={"spoken_text": seg.text})
                    if not (seg.spoken_text and seg.spoken_text.strip())
                    else seg
                )
                for seg in script.segments
            ]
            script = script.model_copy(update={"segments": degraded_segments})
            step._on_step_event(
                "tts_spoken_rewrite_degraded",
                {
                    "chapter": input_data.chapter_number,
                    "reason": "permanent_route_failure",
                    "llm_call_failed": llm_failed_count,
                    "fallback": "original_text_as_spoken_text",
                },
            )
            _log.warning(
                "Spoken-text rewrite degraded: using original text as spoken_text "
                "(%d segments failed due to permanent route failure)",
                llm_failed_count,
            )
            return script

        # Transient errors exhausted all retries — block the pipeline.
        # Compute coverage for the report
        rewritable_types = {"narration", "dialogue", "inner_thought"}
        rewritable_segments = [
            seg for seg in script.segments if seg.segment_type.value in rewritable_types
        ]
        total_rewritable = len(rewritable_segments)
        with_spoken = sum(1 for seg in rewritable_segments if seg.spoken_text and seg.spoken_text.strip())
        coverage = with_spoken / total_rewritable if total_rewritable > 0 else 0.0

        report = ScriptCompletenessReport(
            passed=False,
            spoken_text_coverage=coverage,
            failures=[
                f"口语改写 LLM 调用失败 {llm_failed_count} 段（已重试 {max_rounds} 轮）",
                f"spoken_text 覆盖率 {coverage:.1%} 不足",
            ],
        )
        step._on_step_event(
            "tts_spoken_rewrite_exhausted",
            {
                "chapter": input_data.chapter_number,
                "max_rounds": max_rounds,
                "llm_call_failed": llm_failed_count,
                "spoken_text_coverage": coverage,
            },
        )
        raise ScriptCompletenessError(report)

    return script
