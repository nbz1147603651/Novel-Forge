"""Multi-take selection for high-stakes TTS segments.

Synthesizing the same segment several times and keeping the best take
improves expression fidelity for emotionally intense or key dialogue lines
at the cost of extra provider quota.  This module keeps the policy
deterministic and cheap:

- :func:`take_count` decides *whether* a segment deserves multiple takes
  (intensity >= threshold, or high-energy dialogue lines).
- :func:`score_take` ranks one take in [0, 1] using duration plausibility
  and emotion-energy consistency (reusing the RMS heuristics from
  ``segment_quality``), so the *best* take is chosen on objective grounds
  rather than first-write-wins.
- :func:`select_best_take` picks the highest-scoring take, breaking ties
  toward the first generated one (cheapest and most predictable).

When no acoustic evidence is available (FFmpeg missing, probe failure) the
score degrades gracefully to the duration term alone.

Author: novel-forge
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novel_forge.obs.logger import get_logger

_log = get_logger(__name__)

# 情绪能量分组（与 segment_quality 保持一致）。
_HIGH_ENERGY_EMOTIONS = frozenset(
    {"happy", "angry", "surprised", "anxious", "playful", "determined"}
)
_LOW_ENERGY_EMOTIONS = frozenset({"sad", "whisper", "tender", "nostalgic"})

# RMS 阈值（dBFS）：高能量情绪期望 > -28 dBFS，低能量情绪期望 < -18 dBFS。
_EMOTION_RMS_HIGH_FLOOR = -28.0
_EMOTION_RMS_LOW_CEILING = -18.0

# 中文 TTS 平均语速（毫秒/字 @ speed=1.0），用于时长合理性评分。
_MS_PER_CHAR = 230.0
# 时长偏离可接受区间（ratio = duration / expected）。
_DURATION_RATIO_IDEAL = (0.85, 1.25)
_DURATION_RATIO_TOLERABLE = (0.6, 1.5)

# 每个质量警告的扣分。
_QUALITY_WARNING_PENALTY = 0.12


@dataclass(frozen=True)
class TakeEvidence:
    """Objective evidence collected for one synthesized take."""

    audio_path: Path
    duration_ms: int
    quality_warnings: tuple[str, ...] = ()
    rms_dbfs: float | None = None


def take_count(
    segment: Any,
    *,
    enabled: bool,
    min_intensity: float,
    max_takes: int,
) -> int:
    """Return how many takes *segment* should generate (1 = single take).

    Multi-take triggers:

    - ``emotion_intensity >= min_intensity`` (default 0.7), or
    - high-energy dialogue lines (DIALOGUE with an emotion in the
      high-energy set and intensity >= 0.5) — key dialogue carries the
      scene's expression load even below the intensity threshold.

    When ``max_takes >= 3`` and intensity is clearly above the threshold,
    a third take is generated (2-3 samples per the plan).
    """
    if not enabled:
        return 1
    intensity = float(getattr(segment, "emotion_intensity", 0.0) or 0.0)
    emotion = getattr(segment, "emotion", None)
    emotion_value = str(getattr(emotion, "value", "") or "").lower()
    if emotion_value in ("", "neutral"):
        # 中性情绪的高强度多为标注噪声，多 Take 只为表达型段生成。
        return 1
    high_energy = emotion_value in _HIGH_ENERGY_EMOTIONS

    is_dialogue = str(
        getattr(getattr(segment, "segment_type", None), "value", "")
    ).lower() == "dialogue"
    key_dialogue = is_dialogue and high_energy and intensity >= 0.5

    if intensity >= min_intensity or key_dialogue:
        if max_takes >= 3 and intensity >= min_intensity + 0.15:
            return 3
        return 2
    return 1


def _expected_duration_ms(text: str, speed: float) -> float:
    """Estimate a natural duration for *text* at the given speed."""
    normalized = "".join(
        ch for ch in text if not ch.isspace() and ch not in "。！？，、；：…—"
    )
    chars = max(1, len(normalized))
    return chars * _MS_PER_CHAR / max(0.3, speed)


def score_take(
    segment: Any,
    evidence: TakeEvidence,
    *,
    speed: float = 1.0,
) -> float:
    """Score one take in [0, 1]; higher is better.

    Terms:

    1. **Duration plausibility** (0-0.6): the take's duration should be
       close to the expected natural duration; both truncated and bloated
       takes lose points.
    2. **Emotion-energy consistency** (0-0.4): when RMS is available, a
       high-energy emotion expects louder audio and a low-energy emotion
       expects quieter audio.
    3. **Quality warnings** (-0.12 each, capped): advisory warnings from the
       quality gate reduce the score.
    """
    expected = _expected_duration_ms(
        str(getattr(segment, "spoken_text", "") or segment.text or ""),
        speed,
    )
    ratio = evidence.duration_ms / max(expected, 1.0)

    # 时长分：线性内插 [tolerable → ideal → tolerable]。
    low_ideal, high_ideal = _DURATION_RATIO_IDEAL
    low_tol, high_tol = _DURATION_RATIO_TOLERABLE
    if low_tol <= ratio <= low_ideal:
        duration_score = 0.6 * (ratio - low_tol) / max(1e-6, low_ideal - low_tol)
    elif low_ideal <= ratio <= high_ideal:
        duration_score = 0.6
    elif high_ideal <= ratio <= high_tol:
        duration_score = 0.6 * (high_tol - ratio) / max(1e-6, high_tol - high_ideal)
    else:
        duration_score = 0.0

    emotion = getattr(segment, "emotion", None)
    emotion_value = str(getattr(emotion, "value", "") or "").lower()
    energy_score = 0.4
    rms = evidence.rms_dbfs
    if rms is not None and emotion_value:
        if emotion_value in _HIGH_ENERGY_EMOTIONS:
            if rms >= _EMOTION_RMS_HIGH_FLOOR:
                energy_score = 0.4
            else:
                energy_score = 0.4 * max(
                    0.0, 1.0 + (rms - _EMOTION_RMS_HIGH_FLOOR) / 20.0
                )
        elif emotion_value in _LOW_ENERGY_EMOTIONS:
            if rms <= _EMOTION_RMS_LOW_CEILING:
                energy_score = 0.4
            else:
                energy_score = 0.4 * max(
                    0.0, 1.0 - (rms - _EMOTION_RMS_LOW_CEILING) / 20.0
                )

    warning_penalty = min(0.6, len(evidence.quality_warnings) * _QUALITY_WARNING_PENALTY)
    score = max(0.0, min(1.0, duration_score + energy_score - warning_penalty))
    _log.debug(
        "Take score=%.2f (duration=%.2f energy=%.2f warnings=%d) for segment %s",
        score,
        duration_score,
        energy_score,
        len(evidence.quality_warnings),
        getattr(segment, "segment_index", "?"),
    )
    return score


def select_best_take(
    takes: list[tuple[Any, TakeEvidence]],
) -> tuple[Any, TakeEvidence]:
    """Pick the highest-scoring take; ties favor the first (earliest) one.

    Args:
        takes: ``(result, evidence)`` pairs in generation order.

    Returns:
        The best ``(result, evidence)`` pair.  Raises ``ValueError`` when
        *takes* is empty.
    """
    if not takes:
        raise ValueError("select_best_take called with no takes")
    best_pair = takes[0]
    best_score = -1.0
    for pair in takes:
        score = score_take(pair[0], pair[1])
        if score > best_score:
            best_score = score
            best_pair = pair
    return best_pair


__all__ = [
    "TakeEvidence",
    "take_count",
    "score_take",
    "select_best_take",
]
