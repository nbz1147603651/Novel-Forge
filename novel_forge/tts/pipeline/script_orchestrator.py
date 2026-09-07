"""Script orchestrator — phase-based pipeline composition.

Replaces the legacy ``ScriptGenerationPipeline`` class with a modular
orchestrator that:
1. Calls 7 independent phases in sequence
2. Runs LLM review and spoken-text rewrite concurrently (Phase 4b + 5)
3. Executes the completeness gate after finalization
4. Emits structured step events for each phase transition
5. Raises :class:`ScriptCompletenessError` to block incomplete scripts

Author: novel-forge
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from novel_forge.obs.logger import get_logger
from novel_forge.tts.pipeline.script_completeness_gate import validate_script_completeness
from novel_forge.tts.pipeline.script_phases import ScriptCompletenessError, ScriptCompletenessReport
from novel_forge.tts.pipeline.script_phases.adjudicate import phase_adjudicate
from novel_forge.tts.pipeline.script_phases.emotion_label import phase_emotion_label
from novel_forge.tts.pipeline.script_phases.finalize import phase_finalize
from novel_forge.tts.pipeline.script_phases.generate import phase_generate
from novel_forge.tts.pipeline.script_phases.normalize import phase_normalize
from novel_forge.tts.pipeline.script_phases.review import phase_review_llm, phase_review_rules
from novel_forge.tts.pipeline.script_phases.rewrite import phase_rewrite
from novel_forge.tts.pipeline.script_phases.sound_design import phase_sound_design
from novel_forge.tts.repair_contract import verify_tts_repair_candidate
from novel_forge.tts.script_integrity import refresh_segment_uid

if TYPE_CHECKING:
    from novel_forge.tts.pipeline.generate_script_step import (
        GenerateDubbingScriptInput,
        GenerateDubbingScriptStep,
        ScriptGenerationContext,
    )
    from novel_forge.tts.schemas import DubbingScript
    from novel_forge.tts.script_stage_context import ScriptStageContext

_log = get_logger("tts.pipeline.script_orchestrator")


class ScriptOrchestrator:
    """Orchestrates the 7-phase script generation pipeline.

    Each phase is an independent module that can be tested in isolation.
    The orchestrator handles:
    - Phase sequencing and data flow
    - Concurrent execution of review + rewrite (Phase 4b + 5)
    - Completeness gate enforcement
    - Structured event emission for observability
    """

    def __init__(self, step: GenerateDubbingScriptStep) -> None:
        self._step = step

    async def run(
        self,
        input_data: GenerateDubbingScriptInput,
        ctx: ScriptGenerationContext,
        script_stage_context: ScriptStageContext,
    ) -> DubbingScript:
        """Execute all 7 phases and the completeness gate.

        Args:
            input_data: Generation input (chapter text, voice team, upstream data).
            ctx: Per-invocation transient context.
            script_stage_context: Stage context for prompt projection.

        Returns:
            A complete, gate-validated dubbing script ready for persistence.

        Raises:
            ScriptCompletenessError: When the script fails the completeness gate.
        """
        step = self._step
        settings = step._settings

        # ── Phase 1: Generate ───────────────────────────────────────────────
        self._emit_phase_event("generate", "start", input_data.chapter_number)
        script, speaker_candidates = await phase_generate(input_data, ctx, step)
        self._emit_phase_event(
            "generate",
            "complete",
            input_data.chapter_number,
            {"segment_count": len(script.segments)},
        )

        # ── Phase 2: Adjudicate ─────────────────────────────────────────────
        self._emit_phase_event("adjudicate", "start", input_data.chapter_number)
        script = await phase_adjudicate(script, speaker_candidates, input_data, step)
        self._emit_phase_event("adjudicate", "complete", input_data.chapter_number)

        # ── Phase 3: Normalize ──────────────────────────────────────────────
        self._emit_phase_event("normalize", "start", input_data.chapter_number)
        character_map = step._build_character_map(
            input_data.voice_team, input_data.character_voices
        )
        script = phase_normalize(script, input_data, character_map, step)
        self._emit_phase_event("normalize", "complete", input_data.chapter_number)

        # ── Phase 4a: Review Rules ──────────────────────────────────────────
        self._emit_phase_event("review_rules", "start", input_data.chapter_number)
        script = phase_review_rules(script, input_data, step)
        self._emit_phase_event("review_rules", "complete", input_data.chapter_number)
        repair_baseline = script.model_copy(deep=True)

        # ── Phase 4b + 5: Review LLM + Rewrite (concurrent) ─────────────────
        self._emit_phase_event("review_llm_and_rewrite", "start", input_data.chapter_number)
        script = await self._review_and_rewrite_concurrent(
            script, input_data, script_stage_context
        )
        self._emit_phase_event("review_llm_and_rewrite", "complete", input_data.chapter_number)

        # ── Phase 5.6: LLM emotion fine-labeling ──────────────────────────
        # Runs after the review+rewrite merge and before pause-marker /
        # onomatopoeia injection so the injected tags consume the
        # fine-labeled emotion signals.  Deterministic validation (unknown
        # values fall back to keyword inference, intensity is clamped)
        # guarantees this phase can never regress expression metadata.
        if bool(getattr(settings, "tts_emotion_label_enabled", True)):
            self._emit_phase_event("emotion_label", "start", input_data.chapter_number)
            script = await phase_emotion_label(
                script, input_data, script_stage_context, step
            )
            self._emit_phase_event("emotion_label", "complete", input_data.chapter_number)

        # ── Phase 5.5: Deterministic pause-marker injection ────────────────
        # Runs after the review+rewrite merge so it covers both the LLM
        # rewrite path and the rule-based sanitization fallback.  Only
        # platforms that natively support `<#X#>` syntax receive markers;
        # other providers keep plain text so markers are never read aloud.
        script = self._inject_pause_markers(script, input_data)

        # ── Phase 6: Sound Design ───────────────────────────────────────────
        self._emit_phase_event("sound_design", "start", input_data.chapter_number)
        script = await phase_sound_design(script, input_data, ctx, step)
        self._emit_phase_event("sound_design", "complete", input_data.chapter_number)

        # ── Phase 7: Finalize ───────────────────────────────────────────────
        self._emit_phase_event("finalize", "start", input_data.chapter_number)
        script = phase_finalize(script, input_data, ctx, step)
        self._emit_phase_event("finalize", "complete", input_data.chapter_number)

        # ── Completeness Gate ───────────────────────────────────────────────
        report = validate_script_completeness(
            script,
            input_data.voice_team,
            step._settings,
        )
        if not report.passed:
            # ── Emotion repair: attempt self-healing before blocking ──
            script, report = self._attempt_emotion_repair(
                script, report, input_data, step
            )

        if not report.passed:
            # ── Spoken-text fallback: copy text → spoken_text ──
            script, report = self._attempt_spoken_text_fallback(
                script, report, input_data, step
            )

        if not report.passed:
            # ── Voice assignment fallback: unassigned dialogue → narration ──
            script, report = self._attempt_voice_assignment_fallback(
                script, report, input_data, step
            )

        repair_verification = verify_tts_repair_candidate(
            repair_baseline,
            script,
            source_text=input_data.chapter_text,
            completeness_report=report,
        )
        metadata = dict(script.metadata)
        metadata["repair_verification"] = repair_verification.model_dump(mode="json")
        script = script.model_copy(update={"metadata": metadata})

        if not repair_verification.passed:
            report = ScriptCompletenessReport(
                passed=False,
                spoken_text_coverage=report.spoken_text_coverage,
                emotion_differentiation=report.emotion_differentiation,
                voice_assignment_coverage=report.voice_assignment_coverage,
                failures=repair_verification.failures,
            )
            self._emit_phase_event(
                "completeness_gate",
                "failed",
                input_data.chapter_number,
                {
                    "failures": report.failures,
                    "diagnostics": {
                        "spoken_text_coverage": report.spoken_text_coverage,
                        "emotion_differentiation": report.emotion_differentiation,
                        "voice_assignment_coverage": report.voice_assignment_coverage,
                        "rewrite_meta": script.metadata.get("spoken_text_rewrite", {}),
                        "review_status": script.metadata.get(
                            "professional_script_review", {}
                        )
                        .get("llm_review", {})
                        .get("status", "unknown"),
                    },
                },
            )
            raise ScriptCompletenessError(report, script=script)

        self._emit_phase_event("completeness_gate", "passed", input_data.chapter_number)

        # Attach completeness report to metadata for auditability
        metadata = dict(script.metadata)
        metadata["completeness_report"] = {
            "passed": report.passed,
            "spoken_text_coverage": report.spoken_text_coverage,
            "emotion_differentiation": report.emotion_differentiation,
            "voice_assignment_coverage": report.voice_assignment_coverage,
        }
        script = script.model_copy(update={"metadata": metadata})

        return script

    async def _review_and_rewrite_concurrent(
        self,
        script: DubbingScript,
        input_data: GenerateDubbingScriptInput,
        script_stage_context: ScriptStageContext,
    ) -> DubbingScript:
        """Run LLM review and spoken-text rewrite concurrently.

        Review writes metadata and performance repairs; rewrite writes
        spoken_text fields.  Since they target different fields, they can
        safely execute in parallel, reducing latency by ~30-50%.

        If the rewrite phase raises ScriptCompletenessError (all LLM retries
        exhausted), we degrade gracefully: copy sanitized original text into
        spoken_text so the pipeline continues.  The completeness gate will
        still validate the final result.
        """
        step = self._step

        reviewed_task = phase_review_llm(script, input_data, script_stage_context, step)
        rewritten_task = phase_rewrite(script, input_data, script_stage_context, step)

        # Gather with return_exceptions to handle rewrite failure gracefully
        results = await asyncio.gather(
            reviewed_task, rewritten_task, return_exceptions=True
        )
        reviewed = results[0]
        rewritten = results[1]

        # Handle rewrite failure: degrade to sanitized original text
        if isinstance(rewritten, BaseException):
            _log.warning(
                "Spoken-text rewrite phase failed (%s: %s); "
                "degrading to rule-based sanitization fallback",
                type(rewritten).__name__,
                rewritten,
            )
            step._on_step_event(
                "tts_spoken_rewrite_degraded",
                {
                    "chapter": input_data.chapter_number,
                    "reason": f"{type(rewritten).__name__}: {rewritten}",
                    "fallback": "rule_based_sanitization",
                },
            )
            rewritten = self._apply_rule_based_spoken_fallback(script, input_data)

        # Handle review failure: use original script as reviewed base
        if isinstance(reviewed, BaseException):
            _log.warning(
                "LLM review phase failed (%s: %s); using pre-review script",
                type(reviewed).__name__,
                reviewed,
            )
            reviewed = script

        return self._merge_review_and_rewrite(script, reviewed, rewritten)

    def _inject_pause_markers(
        self,
        script: DubbingScript,
        input_data: GenerateDubbingScriptInput,
    ) -> DubbingScript:
        """Deterministic ``<#X#>`` pause-marker injection for MiniMax.

        Covered paths: LLM rewrite success and rule-based sanitization
        fallback — both merge through :meth:`_review_and_rewrite_concurrent`
        before this point.  Marker rules are pure punctuation / paragraph /
        dialogue-structure heuristics (see
        ``tts/pipeline/pause_marker_injection.py``), so no LLM call is made
        and the pass is idempotent (existing markers are stripped first).

        Skipped when the provider does not natively support ``<#X#>`` so the
        markers are never read out as literal text on other platforms.
        """
        settings = self._step._settings
        if not bool(getattr(settings, "tts_pause_marker_enabled", True)):
            return script
        platform_id = str(
            input_data.target_provider
            or getattr(settings, "tts_default_provider", "minimax")
            or "minimax"
        ).strip().lower().replace("-", "_")
        platforms = [
            str(item or "").strip().lower().replace("-", "_")
            for item in (getattr(settings, "tts_pause_marker_platforms", None) or ["minimax"])
        ]
        if platform_id not in platforms:
            return script

        from novel_forge.tts.pipeline.pause_marker_injection import (  # noqa: PLC0415
            inject_pause_markers,
        )

        self._step._on_step_event(
            "tts_pause_marker_injection",
            {
                "chapter": input_data.chapter_number,
                "platform": platform_id,
            },
        )
        model_id = str(
            input_data.target_model or getattr(settings, "tts_default_model", "") or ""
        )
        return inject_pause_markers(
            script,
            platform=platform_id,
            platforms=platforms,
            model_id=model_id,
            onomatopoeia_enabled=bool(
                getattr(settings, "tts_onomatopoeia_enabled", True)
            ),
            onomatopoeia_min_intensity=float(
                getattr(settings, "tts_onomatopoeia_min_intensity", 0.6)
            ),
        )

    def _apply_rule_based_spoken_fallback(
        self,
        script: DubbingScript,
        input_data: GenerateDubbingScriptInput,
    ) -> DubbingScript:
        """Deterministic fallback: sanitize original text into spoken_text.

        Uses :func:`sanitize_for_speech` to strip non-speakable characters
        (em-dashes, brackets, stage directions) from the original literary
        text and populate spoken_text.  This ensures the pipeline can always
        produce synthesizable output even when LLM rewrite is unavailable.
        """
        from novel_forge.tts.schemas import SegmentType
        from novel_forge.tts.spoken_text_rewrite import sanitize_for_speech

        rewritable_types = {SegmentType.NARRATION, SegmentType.DIALOGUE, SegmentType.INNER_THOUGHT}
        segments = list(script.segments)
        filled_count = 0
        for i, seg in enumerate(segments):
            if seg.segment_type in rewritable_types and not (seg.spoken_text and seg.spoken_text.strip()):
                sanitized = sanitize_for_speech(seg.text)
                if sanitized:
                    segments[i] = seg.model_copy(update={"spoken_text": sanitized})
                    filled_count += 1

        if filled_count == 0:
            return script

        metadata = dict(script.metadata)
        metadata["spoken_text_rewrite"] = {
            **(metadata.get("spoken_text_rewrite") or {}),
            "rule_based_fallback": True,
            "filled_segments": filled_count,
        }
        repaired = script.model_copy(update={"segments": segments, "metadata": metadata})
        _log.info(
            "Rule-based spoken_text fallback filled %d segments for chapter %d",
            filled_count,
            input_data.chapter_number,
        )
        return repaired

    @staticmethod
    def _merge_review_and_rewrite(
        base: DubbingScript,
        reviewed: DubbingScript,
        rewritten: DubbingScript,
    ) -> DubbingScript:
        """Merge parallel branches by durable segment identity.

        Review may itself repair ``spoken_text``.  Such a specialist decision
        must win over the general rewrite branch; rewrite supplies text only
        when review left that field unchanged from the common base.
        """
        base_by_id = {segment.segment_index: segment for segment in base.segments}
        reviewed_by_id = {segment.segment_index: segment for segment in reviewed.segments}
        rewritten_by_id = {segment.segment_index: segment for segment in rewritten.segments}
        identity_sets = (set(base_by_id), set(reviewed_by_id), set(rewritten_by_id))
        unique_counts_match = all(
            len(items) == len(segments)
            for items, segments in (
                (base_by_id, base.segments),
                (reviewed_by_id, reviewed.segments),
                (rewritten_by_id, rewritten.segments),
            )
        )
        if not unique_counts_match or len(set(map(frozenset, identity_sets))) != 1:
            _log.warning(
                "Review/rewrite segment identity mismatch; skipping spoken_text merge "
                "(base=%s reviewed=%s rewritten=%s)",
                sorted(base_by_id),
                sorted(reviewed_by_id),
                sorted(rewritten_by_id),
            )
            return reviewed

        merged_segments = []
        for reviewed_segment in reviewed.segments:
            segment_id = reviewed_segment.segment_index
            base_segment = base_by_id[segment_id]
            rewritten_segment = rewritten_by_id[segment_id]
            review_changed_text = reviewed_segment.spoken_text != base_segment.spoken_text
            rewrite_text = rewritten_segment.spoken_text
            if (
                not review_changed_text
                and rewrite_text
                and rewrite_text != reviewed_segment.spoken_text
            ):
                reviewed_segment = reviewed_segment.model_copy(
                    update={"spoken_text": rewrite_text}
                )
            merged_segments.append(refresh_segment_uid(reviewed_segment))

        metadata = dict(reviewed.metadata)
        if "spoken_text_rewrite" in rewritten.metadata:
            metadata["spoken_text_rewrite"] = rewritten.metadata["spoken_text_rewrite"]
        return reviewed.model_copy(update={"segments": merged_segments, "metadata": metadata})

    def _emit_phase_event(
        self,
        phase: str,
        status: str,
        chapter_number: int,
        extra: dict | None = None,
    ) -> None:
        """Emit a structured phase transition event."""
        payload = {
            "chapter": chapter_number,
            "phase": phase,
            "status": status,
        }
        if extra:
            payload.update(extra)
        self._step._on_step_event(f"tts_script_phase_{status}", payload)

    def _attempt_emotion_repair(
        self,
        script: DubbingScript,
        report: ScriptCompletenessReport,
        input_data: GenerateDubbingScriptInput,
        step: GenerateDubbingScriptStep,
    ) -> tuple[DubbingScript, ScriptCompletenessReport]:
        """Attempt emotion enrichment repair when the gate fails on emotion differentiation.

        Only triggers when the emotion differentiation metric is the failing
        dimension.  Uses rule-based text analysis to upgrade neutral segments
        that carry clear emotional signals, then re-validates the gate.

        Returns:
            Tuple of (possibly repaired script, re-validated report).
        """
        # Only repair if emotion differentiation is the failing metric
        emotion_failures = [
            f for f in report.failures if "情绪分化度" in f
        ]
        if not emotion_failures:
            return script, report

        _log.info(
            "Attempting emotion repair for chapter %d (current: %.1f%%)",
            input_data.chapter_number,
            report.emotion_differentiation * 100,
        )
        self._emit_phase_event(
            "emotion_repair",
            "start",
            input_data.chapter_number,
            {"emotion_differentiation": report.emotion_differentiation},
        )

        from novel_forge.tts.pipeline.emotion_repair import enrich_script_emotions

        target = float(
            getattr(step._settings, "tts_script_gate_emotion_differentiation", 0.15)
        )
        repaired_script = enrich_script_emotions(
            script,
            target_differentiation=target,
        )

        # Re-validate the gate
        new_report = validate_script_completeness(
            repaired_script,
            input_data.voice_team,
            step._settings,
        )

        if new_report.passed:
            _log.info(
                "Emotion repair succeeded for chapter %d (%.1f%% → %.1f%%)",
                input_data.chapter_number,
                report.emotion_differentiation * 100,
                new_report.emotion_differentiation * 100,
            )
            self._emit_phase_event(
                "emotion_repair",
                "succeeded",
                input_data.chapter_number,
                {
                    "before": report.emotion_differentiation,
                    "after": new_report.emotion_differentiation,
                },
            )
        else:
            _log.warning(
                "Emotion repair insufficient for chapter %d (%.1f%% → %.1f%%, still failing)",
                input_data.chapter_number,
                report.emotion_differentiation * 100,
                new_report.emotion_differentiation * 100,
            )
            self._emit_phase_event(
                "emotion_repair",
                "insufficient",
                input_data.chapter_number,
                {
                    "before": report.emotion_differentiation,
                    "after": new_report.emotion_differentiation,
                    "remaining_failures": new_report.failures,
                },
            )

        return repaired_script, new_report

    def _attempt_spoken_text_fallback(
        self,
        script: DubbingScript,
        report: ScriptCompletenessReport,
        input_data: GenerateDubbingScriptInput,
        step: GenerateDubbingScriptStep,
    ) -> tuple[DubbingScript, ScriptCompletenessReport]:
        """Fallback: copy text to spoken_text for segments missing it.

        This is a zero-LLM-cost deterministic repair that resolves the most
        common spoken_text coverage failure: the rewrite phase failed or was
        skipped, but the original text is perfectly usable for synthesis.
        """
        spoken_failures = [f for f in report.failures if "spoken_text" in f]
        if not spoken_failures:
            return script, report

        _log.info(
            "Attempting spoken_text fallback for chapter %d (current coverage: %.1f%%)",
            input_data.chapter_number,
            report.spoken_text_coverage * 100,
        )
        self._emit_phase_event(
            "spoken_text_fallback", "start", input_data.chapter_number
        )

        from novel_forge.tts.schemas import SegmentType
        from novel_forge.tts.spoken_text_rewrite import sanitize_for_speech

        rewritable_types = {SegmentType.NARRATION, SegmentType.DIALOGUE, SegmentType.INNER_THOUGHT}
        segments = list(script.segments)
        filled_count = 0
        for i, seg in enumerate(segments):
            if seg.segment_type in rewritable_types and not (seg.spoken_text and seg.spoken_text.strip()):
                # Use sanitized original text as spoken_text fallback.
                # sanitize_for_speech strips em-dashes, brackets, stage
                # directions and other non-speakable characters.
                fallback_text = sanitize_for_speech(seg.text)
                if fallback_text:
                    segments[i] = seg.model_copy(update={"spoken_text": fallback_text})
                    filled_count += 1

        if filled_count == 0:
            return script, report

        repaired_script = script.model_copy(update={"segments": segments})
        # Record the fallback in metadata for auditability.
        metadata = dict(repaired_script.metadata)
        metadata["spoken_text_fallback"] = {"filled_segments": filled_count}
        repaired_script = repaired_script.model_copy(update={"metadata": metadata})

        new_report = validate_script_completeness(
            repaired_script, input_data.voice_team, step._settings
        )

        if new_report.passed:
            _log.info(
                "Spoken-text fallback succeeded for chapter %d (filled %d segments)",
                input_data.chapter_number,
                filled_count,
            )
            self._emit_phase_event(
                "spoken_text_fallback", "succeeded", input_data.chapter_number,
                {"filled_segments": filled_count},
            )
        else:
            self._emit_phase_event(
                "spoken_text_fallback", "insufficient", input_data.chapter_number,
                {"filled_segments": filled_count, "remaining_failures": new_report.failures},
            )

        return repaired_script, new_report

    def _attempt_voice_assignment_fallback(
        self,
        script: DubbingScript,
        report: ScriptCompletenessReport,
        input_data: GenerateDubbingScriptInput,
        step: GenerateDubbingScriptStep,
    ) -> tuple[DubbingScript, ScriptCompletenessReport]:
        """Fallback: convert unassigned dialogue segments to narration.

        When speaker adjudication cannot determine the character and no
        human is available to confirm, converting to narration is the safe
        fallback: the narrator voice will read the line, which is always
        better than blocking the entire pipeline.
        """
        voice_failures = [f for f in report.failures if "角色分配" in f]
        if not voice_failures:
            return script, report

        _log.info(
            "Attempting voice assignment fallback for chapter %d",
            input_data.chapter_number,
        )
        self._emit_phase_event(
            "voice_assignment_fallback", "start", input_data.chapter_number
        )

        from novel_forge.tts.schemas import SegmentType

        segments = list(script.segments)
        converted_count = 0
        for i, seg in enumerate(segments):
            if seg.segment_type == SegmentType.DIALOGUE and not seg.character_id:
                segments[i] = seg.model_copy(
                    update={
                        "segment_type": SegmentType.NARRATION,
                        "character_id": "",
                        "character_name": "",
                    }
                )
                converted_count += 1

        if converted_count == 0:
            return script, report

        repaired_script = script.model_copy(update={"segments": segments})
        metadata = dict(repaired_script.metadata)
        metadata["voice_assignment_fallback"] = {"converted_to_narration": converted_count}
        # Clear unresolved indices since we converted them.
        adjudication = dict(metadata.get("speaker_adjudication") or {})
        adjudication["unresolved_segment_indices"] = []
        adjudication["status"] = "passed"
        metadata["speaker_adjudication"] = adjudication
        repaired_script = repaired_script.model_copy(update={"metadata": metadata})

        new_report = validate_script_completeness(
            repaired_script, input_data.voice_team, step._settings
        )

        if new_report.passed:
            _log.info(
                "Voice assignment fallback succeeded for chapter %d (%d segments → narration)",
                input_data.chapter_number,
                converted_count,
            )
            self._emit_phase_event(
                "voice_assignment_fallback", "succeeded", input_data.chapter_number,
                {"converted_to_narration": converted_count},
            )
        else:
            self._emit_phase_event(
                "voice_assignment_fallback", "insufficient", input_data.chapter_number,
                {"converted_to_narration": converted_count, "remaining_failures": new_report.failures},
            )

        return repaired_script, new_report
