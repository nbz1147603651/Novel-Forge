"""Deterministic style metrics for chapter quality assessment.

MVP metrics (round 1):
- ``dialogue_ratio_pct`` — quoted dialogue characters vs total characters
- ``repeated_image_phrases`` — n-gram repetition detection for imagery
- ``banned_phrase_hits`` — occurrences of configured banned phrases

Integration: produces a ``StyleMetricsReport`` that maps to a
``QualityCheckResult`` for the quality gate's ``style_dialogue_ratio`` dimension.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from novel_forge.core.schemas.style_profile import DIALOGUE_RATIO_MAP

_logger = logging.getLogger(__name__)

# ── Chinese + ASCII dialogue quote patterns ──────────────────────────
# Chinese: "…" or 「…」 or 『…』
# ASCII: "…"
_DIALOGUE_PATTERNS = [
    re.compile(r'\u201c([^\u201d]*)\u201d'),  # "…"
    re.compile(r'\u300c([^\u300d]*)\u300d'),  # 「…」
    re.compile(r'\u300e([^\u300f]*)\u300f'),  # 『…』
    re.compile(r'"([^"]*)"'),                  # "…"
]

# N-gram range for repetition detection (Chinese characters)
_NGRAM_MIN = 4
_NGRAM_MAX = 10
_REPETITION_THRESHOLD = 3  # phrase must appear >= this many times

# ── Default gate configuration ───────────────────────────────────────
_DEFAULT_GATE_MODE = "warn"
_DEFAULT_REPAIR_THRESHOLD_PCT = 25


@dataclass(frozen=True)
class StyleMetricsReport:
    """Deterministic style metrics for a single chapter."""

    dialogue_ratio_pct: float = 0.0
    """Effective dialogue percentage used by the style gate."""

    total_dialogue_ratio_pct: float = 0.0
    """Legacy all-speaker dialogue percentage."""

    dialogue_char_count: int = 0
    """Effective dialogue characters used by the style gate."""

    total_dialogue_char_count: int = 0
    """All-speaker characters inside dialogue quotes."""

    protagonist_dialogue_char_count: int = 0
    """Dialogue characters attributed to protagonist names."""

    non_protagonist_dialogue_char_count: int = 0
    """Dialogue characters attributed to named non-protagonist speakers."""

    unknown_speaker_dialogue_char_count: int = 0
    """Dialogue characters with no reliable speaker attribution."""

    total_char_count: int = 0
    """Total characters in the chapter text."""

    repeated_image_phrases: list[dict[str, Any]] = field(default_factory=list)
    """Phrases (4-10 chars) appearing >= 3 times, excluding pure whitespace."""

    banned_phrase_hits: list[dict[str, Any]] = field(default_factory=list)
    """Banned phrases found in the text with hit count."""

    dialogue_target_level: str = ""
    """Target dialogue ratio level from style profile: 'high', 'medium', 'low'."""

    dialogue_target_range: tuple[int, int] = (0, 0)
    """Target percentage range from DIALOGUE_RATIO_MAP."""

    character_silence_mode: bool = False
    """Whether dialogue_ratio is interpreted as non-protagonist dialogue."""


def compute_style_metrics(
    text: str,
    style_profile: dict[str, Any] | None,
    *,
    protagonist_names: list[str] | None = None,
    character_silence: bool = False,
) -> StyleMetricsReport:
    """Compute deterministic style metrics from chapter text.

    Parameters
    ----------
    text:
        Chapter prose text.
    style_profile:
        Raw style profile dict (from ``LongProjectBundle.style_profile``).
    protagonist_names:
        Names that should count as protagonist dialogue when
        ``character_silence`` is enabled.
    character_silence:
        If true, the gate-facing ratio is non-protagonist dialogue / total
        characters; legacy total dialogue is still recorded in details.
    """
    if not text:
        return StyleMetricsReport(character_silence_mode=character_silence)

    # Dialogue ratio
    total_chars = len(text)
    split = split_dialogue_ratio(text, protagonist_names=protagonist_names)
    total_dialogue_chars = split.total_dialogue_chars
    total_ratio_pct = (
        total_dialogue_chars / total_chars * 100.0
        if total_chars > 0
        else 0.0
    )
    if character_silence:
        dialogue_chars = split.non_protagonist_dialogue_chars
    else:
        dialogue_chars = total_dialogue_chars
    ratio_pct = (dialogue_chars / total_chars * 100.0) if total_chars > 0 else 0.0

    # Target from style profile
    target_level = _get_dialogue_target_level(style_profile)
    target_range = DIALOGUE_RATIO_MAP.get(target_level, (0, 0))

    # Repetition detection
    repeated = _find_repeated_phrases(text)

    # Banned phrases
    banned = _find_banned_phrase_hits(text, style_profile)

    return StyleMetricsReport(
        dialogue_ratio_pct=round(ratio_pct, 2),
        total_dialogue_ratio_pct=round(total_ratio_pct, 2),
        dialogue_char_count=dialogue_chars,
        total_dialogue_char_count=total_dialogue_chars,
        protagonist_dialogue_char_count=split.protagonist_dialogue_chars,
        non_protagonist_dialogue_char_count=split.non_protagonist_dialogue_chars,
        unknown_speaker_dialogue_char_count=split.unknown_speaker_dialogue_chars,
        total_char_count=total_chars,
        repeated_image_phrases=repeated,
        banned_phrase_hits=banned,
        dialogue_target_level=target_level,
        dialogue_target_range=target_range,
        character_silence_mode=character_silence,
    )


def style_metrics_to_quality_check(
    report: StyleMetricsReport,
    *,
    gate_mode: str = _DEFAULT_GATE_MODE,
    repair_threshold_pct: float = _DEFAULT_REPAIR_THRESHOLD_PCT,
) -> QualityCheckResultCompat:
    """Convert a StyleMetricsReport into a quality gate check result.

    Parameters
    ----------
    report:
        The computed style metrics.
    gate_mode:
        ``'warn'`` — below threshold records warning, does not fail.
        ``'block'`` — below threshold fails the gate.
    repair_threshold_pct:
        Percentage below which the check triggers repair instruction.
    """
    ratio = report.dialogue_ratio_pct
    target_low = report.dialogue_target_range[0] if report.dialogue_target_range else 0
    threshold = float(repair_threshold_pct)

    # Pass if above target range low; warn/fail based on gate_mode
    passed = ratio >= target_low if target_low > 0 else ratio >= threshold

    if gate_mode == "warn":
        # In warn mode, never hard-fail — always pass the gate check itself
        passed = True

    message_parts = []
    if report.character_silence_mode:
        ratio_label = (
            f"对话比例 {ratio:.1f}%（沉默模式，非主角对话；"
            f"全章 {report.total_dialogue_ratio_pct:.1f}%）"
        )
    else:
        ratio_label = f"对话比例 {ratio:.1f}%"

    if ratio < threshold:
        message_parts.append(
            f"{ratio_label}低于修复阈值 {threshold:.0f}%"
        )
    elif target_low > 0 and ratio < target_low:
        message_parts.append(
            f"{ratio_label}低于目标范围 {target_low}%"
        )
    else:
        message_parts.append(ratio_label)

    if report.repeated_image_phrases:
        message_parts.append(
            f"重复短语 {len(report.repeated_image_phrases)} 处"
        )
    if report.banned_phrase_hits:
        message_parts.append(
            f"禁用短语 {sum(h.get('count', 0) for h in report.banned_phrase_hits)} 次命中"
        )

    return QualityCheckResultCompat(
        dimension="style_dialogue_ratio",
        score=ratio,
        threshold=threshold,
        passed=passed,
        message="；".join(message_parts),
        details={
            "dialogue_ratio_pct": ratio,
            "total_dialogue_ratio_pct": report.total_dialogue_ratio_pct,
            "dialogue_char_count": report.dialogue_char_count,
            "total_dialogue_char_count": report.total_dialogue_char_count,
            "protagonist_dialogue_char_count": report.protagonist_dialogue_char_count,
            "non_protagonist_dialogue_char_count": (
                report.non_protagonist_dialogue_char_count
            ),
            "unknown_speaker_dialogue_char_count": (
                report.unknown_speaker_dialogue_char_count
            ),
            "total_char_count": report.total_char_count,
            "target_level": report.dialogue_target_level,
            "target_range": list(report.dialogue_target_range),
            "character_silence_mode": report.character_silence_mode,
            "repeated_phrase_count": len(report.repeated_image_phrases),
            "banned_phrase_hit_count": sum(
                h.get("count", 0) for h in report.banned_phrase_hits
            ),
            "gate_mode": gate_mode,
        },
    )


# ---------------------------------------------------------------------------
# Compatibility shim — avoids circular import with quality_gate.py
# ---------------------------------------------------------------------------


@dataclass
class QualityCheckResultCompat:
    """Structurally identical to ``QualityCheckResult`` for safe conversion."""

    dimension: str
    score: float
    threshold: float
    passed: bool
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _count_dialogue_chars(text: str) -> int:
    """Count characters inside dialogue quote pairs."""
    total = 0
    for pattern in _DIALOGUE_PATTERNS:
        for match in pattern.finditer(text):
            total += len(match.group(1))
    return total


# ---------------------------------------------------------------------------
# Speaker split (added in M4 — see docs/ai_flavor_quality.md)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DialogueSpeakerSplit:
    """Aggregate counts of dialogue characters by speaker role.

    Used to resolve the structural conflict between spec.dialogue_ratio="high"
    and spec.character_silence=True (protagonist barely speaks): when silence
    is on, the effective ratio is non_protagonist / total_chars rather than
    total_dialogue / total_chars.
    """

    protagonist_dialogue_chars: int = 0
    non_protagonist_dialogue_chars: int = 0
    unknown_speaker_dialogue_chars: int = 0

    @property
    def total_dialogue_chars(self) -> int:
        return (
            self.protagonist_dialogue_chars
            + self.non_protagonist_dialogue_chars
            + self.unknown_speaker_dialogue_chars
        )


#: Pattern matching "<name>说|答|问|道|低声|轻声道..." attribution before a
#: dialogue segment. Non-greedy name capture plus a strict verb-class suffix
#: prevent the regex from swallowing the dialogue text into the name group.
_SPEAKER_ATTRIBUTION_RE = re.compile(
    r"(?P<name>[\u4e00-\u9fffA-Za-z]{2,6}?)(?:低声|轻声)?(?:说|答|问|道|答道|开口道|应道|应了一声)"
)


def extract_dialogue_speakers(text: str) -> list[dict[str, Any]]:
    """Return one record per dialogue segment, including speaker role.

    Speaker role is computed by scanning backward from each quote pair for an
    attribution like "陈屿说" / "沈鹿溪低声说". If the matched name appears in
    ``protagonist_names`` (passed separately to the aggregator), the segment
    is classified accordingly. Otherwise, role defaults to "unknown".
    """
    if not text:
        return []

    records: list[dict[str, Any]] = []
    # Look at each dialogue segment with a small backward window for attribution.
    BACKWARD_WINDOW = 24

    for pattern in _DIALOGUE_PATTERNS:
        for match in pattern.finditer(text):
            inner = match.group(1)
            window_start = max(0, match.start() - BACKWARD_WINDOW)
            window = text[window_start : match.start()]
            speaker_name = ""
            m = _SPEAKER_ATTRIBUTION_RE.search(window)
            if m:
                speaker_name = m.group("name")
            records.append({
                "text": inner,
                "char_count": len(inner),
                "speaker_name": speaker_name,
                "speaker_role": "unknown",  # assigned later by aggregator
                "start": match.start(),
                "end": match.end(),
            })
    records.sort(key=lambda r: r["start"])
    return records


def split_dialogue_ratio(
    text: str,
    *,
    protagonist_names: list[str] | None = None,
) -> DialogueSpeakerSplit:
    """Aggregate extracted dialogue segments into a DialogueSpeakerSplit.

    ``protagonist_names`` is a list of character names that the project
    declares as the protagonist. Names can be:
    - full Chinese names (e.g. "沈鹿溪")
    - surnames (e.g. "沈") for narrative that only uses surnames
    """
    segments = extract_dialogue_speakers(text)
    protagonist_set = set(protagonist_names or [])

    protagonist_chars = 0
    non_protagonist_chars = 0
    unknown_chars = 0

    for seg in segments:
        name = seg["speaker_name"]
        chars = seg["char_count"]
        if not name:
            unknown_chars += chars
            continue
        # Match full name OR surname (first char of name).
        is_protagonist = name in protagonist_set
        if not is_protagonist and len(name) >= 2:
            surname = name[0]
            if surname in protagonist_set:
                is_protagonist = True
        if is_protagonist:
            protagonist_chars += chars
            seg["speaker_role"] = "protagonist"
        else:
            non_protagonist_chars += chars
            seg["speaker_role"] = "non_protagonist"

    return DialogueSpeakerSplit(
        protagonist_dialogue_chars=protagonist_chars,
        non_protagonist_dialogue_chars=non_protagonist_chars,
        unknown_speaker_dialogue_chars=unknown_chars,
    )


def compute_silence_aware_dialogue_ratio(
    text: str,
    *,
    protagonist_names: list[str] | None = None,
    character_silence: bool = False,
) -> dict[str, float]:
    """Compute dialogue ratios with optional silence-aware semantics.

    Returns a dict with:
    - total_ratio: dialogue_chars / total_chars (the legacy ratio)
    - protagonist_ratio: protagonist_dialogue_chars / total_chars
    - non_protagonist_ratio: non_protagonist_dialogue_chars / total_chars
    - effective_ratio: the one to compare against spec.dialogue_ratio;
      equals non_protagonist_ratio when character_silence=True,
      otherwise equals total_ratio.

    Use this to decide whether a chapter passes the style_profile dialogue_ratio
    requirement without punishing silence-mode projects for protagonist silence.
    """
    total_chars = max(1, len(text))
    split = split_dialogue_ratio(text, protagonist_names=protagonist_names)

    total_ratio = round(split.total_dialogue_chars / total_chars * 100, 2)
    protagonist_ratio = round(split.protagonist_dialogue_chars / total_chars * 100, 2)
    non_protagonist_ratio = round(
        split.non_protagonist_dialogue_chars / total_chars * 100, 2
    )

    if character_silence:
        effective = non_protagonist_ratio
    else:
        effective = total_ratio

    return {
        "total_ratio": total_ratio,
        "protagonist_ratio": protagonist_ratio,
        "non_protagonist_ratio": non_protagonist_ratio,
        "effective_ratio": effective,
        "total_dialogue_chars": split.total_dialogue_chars,
        "protagonist_dialogue_chars": split.protagonist_dialogue_chars,
        "non_protagonist_dialogue_chars": split.non_protagonist_dialogue_chars,
        "character_silence_mode": character_silence,
    }


def _get_dialogue_target_level(style_profile: dict[str, Any] | None) -> str:
    """Extract dialogue ratio level from raw style profile dict."""
    if not style_profile or not isinstance(style_profile, dict):
        return ""
    global_style = style_profile.get("global_style") or {}
    if not isinstance(global_style, dict):
        return ""
    level = str(global_style.get("dialogue_ratio", "") or "").strip().lower()
    return level if level in DIALOGUE_RATIO_MAP else ""


def _find_repeated_phrases(text: str) -> list[dict[str, Any]]:
    """Find n-gram phrases (4-10 chars) appearing >= threshold times."""
    # Strip dialogue content to focus on narrative imagery repetition
    # (dialogue naturally repeats common phrases)
    clean = re.sub(r'[\s\n\r]+', '', text)

    # Only process if text is long enough
    if len(clean) < _NGRAM_MIN * 2:
        return []

    # Build n-gram counter
    counter: Counter[str] = Counter()
    for n in range(_NGRAM_MIN, _NGRAM_MAX + 1):
        for i in range(len(clean) - n + 1):
            ngram = clean[i:i + n]
            # Skip if mostly punctuation or whitespace
            alpha_chars = sum(1 for c in ngram if c.isalpha() or '\u4e00' <= c <= '\u9fff')
            if alpha_chars < n * 0.7:
                continue
            counter[ngram] += 1

    # Filter: only keep phrases appearing >= threshold
    repeated: list[dict[str, Any]] = []
    seen_longer: set[str] = set()

    # Process longest first — if a longer phrase is repeated, skip its substrings
    for ngram, count in sorted(counter.items(), key=lambda x: (-len(x[0]), -x[1])):
        if count < _REPETITION_THRESHOLD:
            continue
        # Skip if this ngram is a substring of an already-found longer phrase
        if any(ngram in longer for longer in seen_longer):
            continue
        seen_longer.add(ngram)
        repeated.append({
            "phrase": ngram,
            "count": count,
            "length": len(ngram),
        })

    # Limit to top 20 by count
    repeated.sort(key=lambda x: x["count"], reverse=True)
    return repeated[:20]


def _find_banned_phrase_hits(
    text: str,
    style_profile: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Count occurrences of banned phrases in the text."""
    if not style_profile or not isinstance(style_profile, dict):
        return []
    global_style = style_profile.get("global_style") or {}
    if not isinstance(global_style, dict):
        return []
    banned = global_style.get("banned_phrases") or global_style.get("forbidden_phrases") or []
    if not isinstance(banned, list):
        return []

    hits = []
    for phrase in banned:
        phrase_str = str(phrase or "").strip()
        if not phrase_str:
            continue
        count = text.count(phrase_str)
        if count > 0:
            hits.append({
                "phrase": phrase_str,
                "count": count,
            })
    return hits
