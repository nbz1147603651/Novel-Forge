"""Pre-synthesis dry-run validation for dubbing scripts.

Runs a battery of checks against a finalized script and voice team to
detect issues BEFORE expensive TTS API calls are made.  Produces a
structured report that the UI can surface to the author.

Author: novel-forge
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from novel_forge.obs.logger import get_logger
from novel_forge.tts.schemas import SegmentType

if TYPE_CHECKING:
    from novel_forge.tts.schemas import DubbingScript, VoiceTeamContract

_log = get_logger("tts.pipeline.preflight_check")

_SYNTHESIZABLE_TYPES = frozenset(
    {SegmentType.NARRATION, SegmentType.DIALOGUE, SegmentType.INNER_THOUGHT}
)


@dataclass(frozen=True)
class PreflightIssue:
    """A single pre-flight validation issue."""

    severity: str  # "error" | "warning" | "info"
    code: str
    message: str
    segment_indices: tuple[int, ...] = ()


@dataclass(frozen=True)
class PreflightReport:
    """Result of pre-synthesis validation."""

    passed: bool
    issues: tuple[PreflightIssue, ...] = ()
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def errors(self) -> tuple[PreflightIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "error")

    @property
    def warnings(self) -> tuple[PreflightIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "warning")


def run_preflight_check(
    script: DubbingScript,
    voice_team: VoiceTeamContract,
) -> PreflightReport:
    """Validate a script against synthesis readiness criteria.

    Checks:
    1. spoken_text non-empty for all synthesizable segments
    2. All dialogue segments have a matching voice_team entry
    3. Emotion distribution is not entirely neutral
    4. VocalDirection is not all-default (differentiation check)
    5. No non-speakable characters in spoken_text

    Returns:
        PreflightReport with pass/fail and structured issues.
    """
    issues: list[PreflightIssue] = []
    segments = script.segments

    synthesizable = [s for s in segments if s.segment_type in _SYNTHESIZABLE_TYPES]
    total_synth = len(synthesizable)

    # ── Check 1: spoken_text coverage ──────────────────────────────────────
    empty_spoken = [
        s.segment_index for s in synthesizable
        if not (s.spoken_text and s.spoken_text.strip())
    ]
    if empty_spoken:
        issues.append(PreflightIssue(
            severity="error",
            code="empty_spoken_text",
            message=f"{len(empty_spoken)}/{total_synth} 段缺少 spoken_text",
            segment_indices=tuple(empty_spoken[:20]),
        ))

    # ── Check 2: voice assignment for dialogue ─────────────────────────────
    voice_map = {
        entry.character_id
        for entry in voice_team.entries
        if entry.is_ready and not entry.is_expired
    }
    unvoiced_dialogue = [
        s.segment_index for s in segments
        if s.segment_type == SegmentType.DIALOGUE
        and s.character_id
        and s.character_id not in voice_map
    ]
    if unvoiced_dialogue:
        issues.append(PreflightIssue(
            severity="error",
            code="missing_voice_assignment",
            message=f"{len(unvoiced_dialogue)} 段对话的角色无就绪音色",
            segment_indices=tuple(unvoiced_dialogue[:20]),
        ))

    # ── Check 3: emotion distribution ──────────────────────────────────────
    non_neutral = sum(
        1 for s in synthesizable
        if (hasattr(s.emotion, "value") and s.emotion.value != "neutral")
        or str(s.emotion) != "neutral"
    )
    emotion_ratio = non_neutral / total_synth if total_synth > 0 else 0.0
    if total_synth >= 5 and emotion_ratio < 0.15:
        issues.append(PreflightIssue(
            severity="warning",
            code="flat_emotion",
            message=f"情绪分化度仅 {emotion_ratio:.0%}，表演可能缺乏生动性",
        ))

    # ── Check 4: vocal_direction differentiation ───────────────────────────
    default_directions = sum(
        1 for s in synthesizable
        if s.vocal_direction
        and s.vocal_direction.energy == 0.5
        and s.vocal_direction.tension == 0.3
        and s.vocal_direction.intimacy == 0.5
    )
    if total_synth >= 5 and default_directions / total_synth > 0.8:
        issues.append(PreflightIssue(
            severity="warning",
            code="uniform_vocal_direction",
            message=f"{default_directions}/{total_synth} 段使用默认声腔参数，缺乏差异化",
        ))

    # ── Check 5: non-speakable characters in spoken_text ───────────────────
    import re
    non_speakable_re = re.compile(r"——|—|\[[^\]]*\]|\*[^*]*\*|<[^>]*>")
    bad_text_segments = [
        s.segment_index for s in synthesizable
        if s.spoken_text and non_speakable_re.search(s.spoken_text)
    ]
    if bad_text_segments:
        issues.append(PreflightIssue(
            severity="warning",
            code="non_speakable_chars",
            message=f"{len(bad_text_segments)} 段 spoken_text 含非朗读符号（破折号/方括号/标签）",
            segment_indices=tuple(bad_text_segments[:20]),
        ))

    # ── Summary ────────────────────────────────────────────────────────────
    has_errors = any(i.severity == "error" for i in issues)
    stats = {
        "total_segments": len(segments),
        "synthesizable_segments": total_synth,
        "spoken_text_coverage": 1.0 - (len(empty_spoken) / total_synth if total_synth else 0.0),
        "emotion_differentiation": emotion_ratio,
        "vocal_direction_default_ratio": default_directions / total_synth if total_synth else 0.0,
        "dialogue_segments": sum(1 for s in segments if s.segment_type == SegmentType.DIALOGUE),
        "narration_segments": sum(1 for s in segments if s.segment_type == SegmentType.NARRATION),
    }

    report = PreflightReport(
        passed=not has_errors,
        issues=tuple(issues),
        stats=stats,
    )

    if not report.passed:
        _log.warning(
            "Preflight check FAILED for chapter %d: %s",
            script.chapter_number,
            "; ".join(i.message for i in report.errors),
        )
    else:
        _log.info(
            "Preflight check PASSED for chapter %d (%d warnings)",
            script.chapter_number,
            len(report.warnings),
        )

    return report
