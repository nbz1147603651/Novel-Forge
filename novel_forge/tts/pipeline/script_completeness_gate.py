"""Script completeness gate — pre-persistence quality validation.

Evaluates a finalized dubbing script against configurable quality thresholds
before it is allowed to be persisted as a production artifact.  The gate
checks spoken_text coverage, emotion differentiation, and voice assignment
completeness.

Author: novel-forge
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from novel_forge.obs.logger import get_logger
from novel_forge.tts.pipeline.script_phases import ScriptCompletenessReport
from novel_forge.tts.schemas import SegmentType

if TYPE_CHECKING:
    from novel_forge.core.config import Settings
    from novel_forge.tts.schemas import DubbingScript, VoiceTeamContract

_log = get_logger("tts.pipeline.script_completeness_gate")

# Segment types eligible for spoken-text rewrite.
_REWRITABLE_TYPES = frozenset(
    {
        SegmentType.NARRATION,
        SegmentType.DIALOGUE,
        SegmentType.INNER_THOUGHT,
    }
)

# A percentage-based variety gate is not statistically meaningful for one or
# two spoken segments: requiring 15% would effectively force 50-100% of a
# short, legitimately neutral passage to be marked emotional.
_MIN_EMOTION_SAMPLE_SEGMENTS = 3


def validate_script_completeness(
    script: DubbingScript,
    voice_team: VoiceTeamContract,
    settings: Settings,
) -> ScriptCompletenessReport:
    """Evaluate script completeness against configurable quality thresholds.

    This gate runs before script persistence and before synthesis to ensure
    that only production-quality scripts flow downstream.

    Args:
        script: The finalized dubbing script to evaluate.
        voice_team: Voice team contract (for voice assignment validation).
        settings: Application settings (carries threshold configuration).

    Returns:
        A :class:`ScriptCompletenessReport` with pass/fail status and metrics.
    """
    # Check if gate is enabled
    if not bool(getattr(settings, "tts_script_gate_enabled", True)):
        return ScriptCompletenessReport(passed=True)

    failures: list[str] = []

    # ── Metric 1: spoken_text coverage ──────────────────────────────────────
    rewritable_segments = [
        seg for seg in script.segments if seg.segment_type in _REWRITABLE_TYPES
    ]
    total_rewritable = len(rewritable_segments)
    with_spoken_text = sum(
        1 for seg in rewritable_segments if seg.spoken_text and seg.spoken_text.strip()
    )
    spoken_text_coverage = with_spoken_text / total_rewritable if total_rewritable > 0 else 1.0

    threshold_spoken = float(getattr(settings, "tts_script_gate_spoken_text_coverage", 0.6))
    if spoken_text_coverage < threshold_spoken:
        failures.append(
            f"spoken_text 覆盖率 {spoken_text_coverage:.1%} < 阈值 {threshold_spoken:.0%}"
            f"（{with_spoken_text}/{total_rewritable} 段有 spoken_text）"
        )

    # ── Metric 2: emotion differentiation ───────────────────────────────────
    # Only spoken segments can carry a meaningful vocal emotion.  Silence,
    # music and sound-effect cues must not dilute this denominator.
    emotion_segments = rewritable_segments
    total_segments = len(emotion_segments)
    non_neutral_count = sum(
        1
        for seg in emotion_segments
        if seg.emotion.value != "neutral" or (seg.emotion_intensity or 0.5) != 0.5
    )
    emotion_differentiation = non_neutral_count / total_segments if total_segments > 0 else 1.0

    threshold_emotion = float(getattr(settings, "tts_script_gate_emotion_differentiation", 0.15))
    if (
        total_segments >= _MIN_EMOTION_SAMPLE_SEGMENTS
        and emotion_differentiation < threshold_emotion
    ):
        failures.append(
            f"情绪分化度 {emotion_differentiation:.1%} < 阈值 {threshold_emotion:.0%}"
            f"（{non_neutral_count}/{total_segments} 段非 neutral）"
        )

    # ── Metric 3: voice assignment coverage ─────────────────────────────────
    dialogue_segments = [
        seg for seg in script.segments if seg.segment_type == SegmentType.DIALOGUE
    ]
    total_dialogue = len(dialogue_segments)
    with_character = sum(1 for seg in dialogue_segments if seg.character_id)
    voice_assignment_coverage = with_character / total_dialogue if total_dialogue > 0 else 1.0

    # Voice assignment must be 100% (speaker adjudication guarantees this)
    if voice_assignment_coverage < 1.0:
        failures.append(
            f"配音角色分配不完整：{with_character}/{total_dialogue} 对话段有 character_id"
        )

    # ── Metric 4: LLM rewrite hard failure check ────────────────────────────
    # Only block on LLM failures when no fallback was applied.  When the
    # rule-based sanitization fallback filled spoken_text, the LLM failure
    # is acceptable (degraded but functional).
    rewrite_meta = script.metadata.get("spoken_text_rewrite", {})
    if isinstance(rewrite_meta, dict):
        rule_based_fallback = bool(rewrite_meta.get("rule_based_fallback"))
        rejection_reasons = rewrite_meta.get("rejection_reasons", {})
        llm_failed = int(rejection_reasons.get("llm_call_failed", 0))
        if llm_failed > 0 and not rule_based_fallback:
            failures.append(
                f"口语改写存在 {llm_failed} 段 LLM 调用硬失败"
            )

    passed = len(failures) == 0

    if not passed:
        _log.warning(
            "Script completeness gate FAILED for chapter %d: %s",
            script.chapter_number,
            "; ".join(failures),
        )
    else:
        _log.info(
            "Script completeness gate PASSED for chapter %d "
            "(spoken=%.1f%%, emotion=%.1f%%, voice=%.1f%%)",
            script.chapter_number,
            spoken_text_coverage * 100,
            emotion_differentiation * 100,
            voice_assignment_coverage * 100,
        )

    return ScriptCompletenessReport(
        passed=passed,
        spoken_text_coverage=spoken_text_coverage,
        emotion_differentiation=emotion_differentiation,
        voice_assignment_coverage=voice_assignment_coverage,
        failures=failures,
    )
