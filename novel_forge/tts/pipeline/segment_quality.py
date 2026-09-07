"""Provider-neutral objective gate applied before a voice take enters the timeline."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from novel_forge.tts.runtime.audio_runtime import ffmpeg_executable
from novel_forge.tts.runtime.speakable_text import normalize_speakable_text
from novel_forge.tts.schemas import TTSRequest, TTSResponse

# 情绪能量分组：用于启发式检查情绪与音频能量的一致性
_HIGH_ENERGY_EMOTIONS = frozenset(
    {"happy", "angry", "surprised", "anxious", "playful", "determined"}
)
_LOW_ENERGY_EMOTIONS = frozenset({"sad", "whisper", "tender", "nostalgic"})

# RMS 阈值（dBFS）：高能量情绪期望 > -28 dBFS，低能量情绪期望 < -18 dBFS
_EMOTION_RMS_HIGH_FLOOR = -28.0
_EMOTION_RMS_LOW_CEILING = -18.0

# Provider 自报时长可信度下限：低于该值几乎必为字段单位错误（如把字符数当作毫秒）。
# 典型中文 TTS 最短有效人声片段 > 200ms；50ms 作为保守硬下限，留出极短语气词空间。
_PROVIDER_DURATION_HARD_FLOOR_MS = 50
# Provider 自报时长对应的 cps 上限：典型中文 TTS 4-25 字/秒，>50 字/秒必为字段错误。
_PROVIDER_DURATION_MAX_CPS = 50.0

# Known provider success statuses across all TTS platforms.
# - "" / "2" / "success" / "completed": normalized or MiniMax HTTP statuses
# - "stop": DashScope/Qwen-TTS official completion status (finish_reason)
# - "streaming_complete": MiniMax streaming path completion marker
# Adapters SHOULD normalize at the boundary (see dashscope_adapter
# _normalize_finish_reason); this set is a defensive second layer.
_PROVIDER_SUCCESS_STATUSES = frozenset(
    {"", "2", "success", "completed", "stop", "streaming_complete"}
)


def trustworthy_duration_ms(
    reported_ms: int,
    request_text: str,
    *,
    hard_floor_ms: int = _PROVIDER_DURATION_HARD_FLOOR_MS,
    max_cps: float = _PROVIDER_DURATION_MAX_CPS,
) -> int | None:
    """Return ``reported_ms`` only when it is plausibly the real audio duration.

    Provider-reported ``duration_ms`` is sometimes a wrong-unit or wrong-field
    value (e.g. MiniMax ``extra_info.audio_length`` has been observed returning
    the *character count* instead of milliseconds, producing 72ms for a 72-char
    narration).  Such values slip past ``response.duration_ms or fallback``
    because they are non-zero, and then trip the segment quality gate's
    ``duration_ms < minimum_duration_ms`` hard check, causing up to
    ``retry_limit`` blind retries that produce the same wrong value.

    This helper applies two sanity checks before trusting the provider value:

    1. ``reported_ms >= hard_floor_ms``: a real human utterance is rarely
       shorter than ~50ms; anything below is almost certainly a unit/field
       error and should defer to local FFmpeg probing.
    2. ``cps <= max_cps``: when the normalized text has >= 4 speakable units,
       the implied characters-per-second must be within human speech range.
       >50 cps is impossible for natural speech and indicates the duration
       field carries the wrong unit.

    Returns ``None`` when the value is not trustworthy, so the caller can fall
    back to ``_probe_duration_ms`` (local FFmpeg measurement of the on-disk
    audio).  Returns the original ``reported_ms`` when it passes both checks.
    """
    if reported_ms <= 0:
        return None
    if reported_ms < hard_floor_ms:
        return None
    normalized = normalize_speakable_text(request_text)
    # Punctuation-only text (e.g. "————") has no speakable content; any
    # provider-reported duration for such input is meaningless.
    if not normalized and request_text.strip():
        return None
    if len(normalized) >= 4:
        cps = len(normalized) / (reported_ms / 1000.0)
        if cps > max_cps:
            return None
    return reported_ms


@dataclass(frozen=True)
class SegmentQualityDecision:
    passed: bool
    warnings: tuple[str, ...] = ()
    characters_per_second: float = 0.0
    # Failure category enables callers to choose retry strategies:
    # "acoustic" → parameter adjustment (speed reduction) may converge;
    # "provider_status" → provider-side issue, parameter changes are futile;
    # "integrity" → payload corruption, plain retry is appropriate.
    failure_category: str = ""


def _measure_segment_loudness(path: Path) -> tuple[float | None, float | None, float | None]:
    """Probe one on-disk take for RMS, peak (dBFS), and silence ratio.

    Uses a single FFmpeg ``astats`` + ``silencedetect`` pass. Returns
    ``(rms_dbfs, peak_dbfs, silence_ratio)`` where ``silence_ratio`` is the
    fraction of the duration flagged as silence (0.0-1.0). Any value may be
    ``None`` when FFmpeg is unavailable or parsing fails - callers treat None
    as "not measured" and skip the corresponding warning.
    """
    if not path.is_file() or path.stat().st_size <= 0:
        return None, None, None
    # astats reports RMS/peak per channel on stderr; silencedetect emits start/end
    # events we sum into a silence ratio. Combined in one filter graph to avoid a
    # second subprocess invocation.
    command = [
        ffmpeg_executable(),
        "-hide_banner",
        "-nostats",
        "-i",
        str(path),
        "-af",
        "silencedetect=noise=-50dB:d=0.5,astats=metadata=1:reset=0",
        "-f",
        "null",
        "-",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, check=False, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None, None, None
    output = completed.stderr.decode("utf-8", errors="replace")

    rms_dbfs: float | None = None
    peak_dbfs: float | None = None
    # astats (metadata=1) prints "RMS level dB: -23.4" and "Peak level dB: -1.2"
    # per channel on stderr. Take the last match (overall/last channel).
    rms_match = re.findall(r"RMS level dB:\s*(-?\d+(?:\.\d+)?)", output)
    peak_match = re.findall(r"Peak level dB:\s*(-?\d+(?:\.\d+)?)", output)
    if rms_match:
        rms_dbfs = float(rms_match[-1])
    if peak_match:
        peak_dbfs = float(peak_match[-1])

    # silencedetect emits "silence_start: 1.2" / "silence_end: 2.8 | ..." pairs.
    starts = re.findall(r"silence_start:\s*(\d+(?:\.\d+)?)", output)
    ends = re.findall(r"silence_end:\s*(\d+(?:\.\d+)?)", output)
    silence_ratio: float | None = None
    if starts and ends:
        total_silence = 0.0
        for start, end in zip(starts, ends, strict=False):
            total_silence += max(0.0, float(end) - float(start))
        # Use the last astats duration hint if present, else fall back to None.
        duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
        if duration_match:
            hours, minutes, seconds = duration_match.groups()
            total_duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
            if total_duration > 0:
                silence_ratio = min(1.0, total_silence / total_duration)
    return rms_dbfs, peak_dbfs, silence_ratio


def evaluate_synthesized_segment(
    request: TTSRequest,
    response: TTSResponse,
    *,
    duration_ms: int,
    audio_size_bytes: int,
    minimum_duration_ms: int,
    maximum_characters_per_second: float,
    audio_path: Path | None = None,
    min_rms_db: float | None = None,
    max_peak_db: float | None = None,
    max_silence_ratio: float | None = None,
) -> SegmentQualityDecision:
    """Reject clearly corrupt or implausibly accelerated provider output.

    The first checks (size/duration/cps/provider status) are hard gates that
    fail the take. The optional loudness/silence checks (only run when
    ``audio_path`` and a threshold are supplied) append *warnings* rather than
    failing - a quiet or near-silent take is suspicious but may be a legitimate
    whispered line, so the author should review rather than auto-reject.
    """
    warnings: list[str] = []
    if audio_size_bytes < 128:
        return SegmentQualityDecision(
            False, ("返回的音频负载过小，可能已损坏。",), failure_category="integrity"
        )
    if duration_ms <= 0:
        return SegmentQualityDecision(
            False, ("无法获取人声片段时长。",), failure_category="integrity"
        )
    if duration_ms < minimum_duration_ms:
        return SegmentQualityDecision(
            False,
            (f"人声片段仅 {duration_ms}ms，低于最小阈值。",),
            failure_category="acoustic",
        )
    normalized = normalize_speakable_text(request.text)
    cps = len(normalized) / max(duration_ms / 1000.0, 0.001)
    if len(normalized) >= 4 and cps > maximum_characters_per_second:
        return SegmentQualityDecision(
            False,
            (f"人声速率 {cps:.1f} 字符/秒，可能存在截断或超速。",),
            cps,
            failure_category="acoustic",
        )
    reported_status = response.take_evidence.provider_status
    if reported_status not in _PROVIDER_SUCCESS_STATUSES:
        return SegmentQualityDecision(
            False,
            (f"供应商报告非完成状态：{reported_status}。",),
            cps,
            failure_category="provider_status",
        )
    invisible_ratio = response.take_evidence.invisible_character_ratio
    if invisible_ratio > 0.05:
        warnings.append(f"供应商报告不可见字符比例 {invisible_ratio:.1%}。")

    # Optional loudness / dead-air advisory checks. Only run when the caller
    # supplies an on-disk path and at least one threshold.
    if audio_path is not None and (
        min_rms_db is not None or max_peak_db is not None or max_silence_ratio is not None
    ):
        rms_dbfs, peak_dbfs, silence_ratio = _measure_segment_loudness(audio_path)
        if rms_dbfs is not None and min_rms_db is not None and rms_dbfs < min_rms_db:
            warnings.append(
                f"人声 RMS {rms_dbfs:.1f} dBFS 低于阈值 {min_rms_db:.1f}，可能过轻或为空音。"
            )
        if peak_dbfs is not None and max_peak_db is not None and peak_dbfs > max_peak_db:
            warnings.append(
                f"人声峰值 {peak_dbfs:.1f} dBFS 高于阈值 {max_peak_db:.1f}，可能存在爆音。"
            )
        if (
            silence_ratio is not None
            and max_silence_ratio is not None
            and silence_ratio > max_silence_ratio
        ):
            warnings.append(
                f"静音比例 {silence_ratio:.0%} 高于阈值 {max_silence_ratio:.0%}，可能存在异常停顿。"
            )

        # 情绪一致性启发式：对比请求情绪与音频能量
        emotion_hint = str(request.emotion or "").lower()
        if rms_dbfs is not None and emotion_hint:
            if emotion_hint in _HIGH_ENERGY_EMOTIONS and rms_dbfs < _EMOTION_RMS_HIGH_FLOOR:
                warnings.append(
                    f"情绪 '{emotion_hint}' 期望较高能量，但 RMS {rms_dbfs:.1f} dBFS 偏低，情绪表达可能不足。"
                )
            elif emotion_hint in _LOW_ENERGY_EMOTIONS and rms_dbfs > _EMOTION_RMS_LOW_CEILING:
                warnings.append(
                    f"情绪 '{emotion_hint}' 期望较低能量，但 RMS {rms_dbfs:.1f} dBFS 偏高，情绪表达可能过度。"
                )

    return SegmentQualityDecision(True, tuple(warnings), cps)
