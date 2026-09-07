"""Text validation utilities for novel content quality checks."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any, Literal

__all__ = [
    "WordCountAssessment",
    "check_word_count",
    "check_revelation_density",
    "assess_word_count",
    "count_chapter_words",
    "display_word_count",
    "text_change_ratio",
]

# Revelation markers: phrases that signal plot reveals or surprise discoveries.
# Ordered by specificity; longer multi-char patterns listed first to avoid
# partial-match issues when counting.
_REVELATION_MARKERS = [
    "意外发现",
    "出乎意料",
    "始料未及",
    "才发现",
    "揭露",
    "揭示",
    "真相",
    "竟然",
    "原来",
]

_REVELATION_RE = re.compile("|".join(re.escape(m) for m in _REVELATION_MARKERS))

# Regex for counting Chinese characters (CJK Unified Ideographs + extensions).
_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\U00020000-\U0002a6df\U0002a700-\U0002ebef]")
_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)?")

# Small identity-based cache for count_chapter_words.
# Avoids repeated regex.findall() on the same text object across pipeline stages.
_WORD_COUNT_CACHE_MAX = 4
_wc_cache: dict[int, tuple[str, int]] = {}  # id -> (text_ref, count)
_wc_cache_order: list[int] = []


WordCountBand = Literal[
    "disabled",
    "ideal",
    "acceptable",
    "buffer",
    "structural",
    "hard_reject",
]
WordCountAction = Literal[
    "accept",
    "light_adjust",
    "structural_restructure",
    "block",
]


@dataclass(frozen=True)
class WordCountAssessment:
    """Word-count gate result using the long-form chapter policy.

    The policy separates generation targets from archive safety:
    ideal ±10%, acceptable ±15%, buffer ±20%, hard reject below 80% or
    above 125%.
    """

    actual: int
    target: int
    ratio: float
    band: WordCountBand
    action: WordCountAction
    ideal_min: int
    ideal_max: int
    acceptable_min: int
    acceptable_max: int
    buffer_min: int
    buffer_max: int
    hard_min: int
    hard_max: int

    @property
    def deviation_pct(self) -> float:
        if self.target <= 0:
            return 0.0 if self.actual == 0 else 100.0
        return round(abs(self.actual - self.target) / self.target * 100.0, 2)

    @property
    def within_acceptable(self) -> bool:
        return self.band in {"ideal", "acceptable"}

    @property
    def requires_restructure(self) -> bool:
        return self.action in {"light_adjust", "structural_restructure"}

    def as_dict(self) -> dict[str, Any]:
        return {
            "actual": self.actual,
            "target": self.target,
            "ratio": round(self.ratio, 3),
            "band": self.band,
            "action": self.action,
            "deviation_pct": self.deviation_pct,
            "ideal_min": self.ideal_min,
            "ideal_max": self.ideal_max,
            "acceptable_min": self.acceptable_min,
            "acceptable_max": self.acceptable_max,
            "buffer_min": self.buffer_min,
            "buffer_max": self.buffer_max,
            "hard_min": self.hard_min,
            "hard_max": self.hard_max,
        }


def count_chapter_words(text: str | None) -> int:
    """Count chapter length with a single project-wide prose metric.

    For Chinese prose this counts CJK ideographs only, excluding whitespace,
    punctuation, Latin snippets, and digits. For non-CJK text it falls back to
    whitespace-like word tokens so short-story tests and English fallbacks stay
    meaningful.

    Uses a small identity-aware cache to avoid repeated regex scans when the
    same text object is counted at multiple pipeline checkpoints.
    """
    if not text or not isinstance(text, str):
        return 0
    tid = id(text)
    cached = _wc_cache.get(tid)
    if cached is not None and cached[0] is text:
        return cached[1]
    cjk_count = len(_CJK_CHAR_RE.findall(text))
    result = cjk_count if cjk_count > 0 else len(_WORD_RE.findall(text))
    # Evict oldest entry if cache is full
    if len(_wc_cache) >= _WORD_COUNT_CACHE_MAX:
        oldest = _wc_cache_order.pop(0)
        _wc_cache.pop(oldest, None)
    _wc_cache[tid] = (text, result)
    _wc_cache_order.append(tid)
    return result


def display_word_count(text: str | None) -> int:
    """Return the canonical prose word count used by UI/result displays.

    Display counts follow rendered prose: known prompt/planning artifacts are
    ignored before the shared chapter-length metric is applied.
    """
    from novel_forge.core.domain.guardrails import scrub_prompt_artifacts

    cleaned, _ = scrub_prompt_artifacts(str(text or ""))
    cleaned = "\n".join(
        line for line in cleaned.splitlines() if not line.lstrip().startswith("#")
    )
    return count_chapter_words(cleaned)


def assess_word_count(text: str | None, target: int) -> WordCountAssessment:
    """Classify text length according to the archive word-count policy."""
    actual = count_chapter_words(text)
    if target <= 0:
        return WordCountAssessment(
            actual=actual,
            target=target,
            ratio=1.0,
            band="disabled",
            action="accept",
            ideal_min=0,
            ideal_max=0,
            acceptable_min=0,
            acceptable_max=0,
            buffer_min=0,
            buffer_max=0,
            hard_min=0,
            hard_max=0,
        )

    ratio = actual / max(target, 1)
    ideal_min = int(round(target * 0.90))
    ideal_max = int(round(target * 1.10))
    acceptable_min = int(round(target * 0.85))
    acceptable_max = int(round(target * 1.15))
    buffer_min = int(round(target * 0.80))
    buffer_max = int(round(target * 1.20))
    hard_min = buffer_min
    hard_max = int(round(target * 1.25))

    if ratio < 0.80 or ratio > 1.25:
        band: WordCountBand = "hard_reject"
        action: WordCountAction = "structural_restructure"
    elif 0.90 <= ratio <= 1.10:
        band = "ideal"
        action = "accept"
    elif 0.85 <= ratio <= 1.15:
        band = "acceptable"
        action = "accept"
    elif 0.80 <= ratio <= 1.20:
        band = "buffer"
        action = "light_adjust"
    else:
        band = "structural"
        action = "structural_restructure"

    return WordCountAssessment(
        actual=actual,
        target=target,
        ratio=ratio,
        band=band,
        action=action,
        ideal_min=ideal_min,
        ideal_max=ideal_max,
        acceptable_min=acceptable_min,
        acceptable_max=acceptable_max,
        buffer_min=buffer_min,
        buffer_max=buffer_max,
        hard_min=hard_min,
        hard_max=hard_max,
    )


def check_word_count(text: str, target: int, tolerance: float = 0.15) -> dict[str, Any]:
    """Check if Chinese character count is within tolerance of target.

    Counts only Chinese characters (CJK ideographs), excluding whitespace,
    punctuation, and Latin characters.

    Args:
        text: Text to validate
        target: Target word (character) count
        tolerance: Allowed deviation ratio (default 0.15 = ±15%)

    Returns:
        Dict with actual_count, target, deviation_pct, within_tolerance, status

    Examples:
        >>> check_word_count("你好世界", 4, 0.15)
        {'actual_count': 4, 'target': 4, 'deviation_pct': 0.0, 'within_tolerance': True, 'status': 'pass'}
        >>> check_word_count("你好", 100, 0.15)
        {'actual_count': 2, 'target': 100, 'deviation_pct': 98.0, 'within_tolerance': False, 'status': 'fail'}
    """
    actual_count = count_chapter_words(text)

    if target <= 0:
        deviation_pct = 0.0 if actual_count == 0 else 100.0
    else:
        deviation_pct = abs(actual_count - target) / target * 100.0

    within_tolerance = deviation_pct <= tolerance * 100.0

    if deviation_pct == 0.0:
        status = "pass"
    elif within_tolerance:
        status = "pass"
    elif deviation_pct <= tolerance * 100.0 * 2:
        status = "warning"
    else:
        status = "fail"

    return {
        "actual_count": actual_count,
        "target": target,
        "deviation_pct": round(deviation_pct, 2),
        "within_tolerance": within_tolerance,
        "status": status,
    }


def check_revelation_density(text: str, max_revelations: int = 2) -> dict[str, Any]:
    """Check if revelation marker density is within budget.

    Counts occurrences of revelation/surprise markers in Chinese prose:
    "原来", "竟然", "才发现", "真相", "揭露", "揭示", "意外发现",
    "出乎意料", "始料未及".

    Args:
        text: Text to validate
        max_revelations: Maximum allowed revelation markers (default 2)

    Returns:
        Dict with revelation_count, max_allowed, within_budget, status

    Examples:
        >>> check_revelation_density("他原来是个好人", 2)
        {'revelation_count': 1, 'max_allowed': 2, 'within_budget': True, 'status': 'pass'}
        >>> check_revelation_density("原来他竟然才发现真相", 2)
        {'revelation_count': 4, 'max_allowed': 2, 'within_budget': False, 'status': 'fail'}
    """
    if not text or not isinstance(text, str):
        revelation_count = 0
    else:
        revelation_count = len(_REVELATION_RE.findall(text))

    within_budget = revelation_count <= max_revelations

    if revelation_count == 0:
        status = "pass"
    elif within_budget:
        status = "pass"
    elif revelation_count <= max_revelations * 2:
        status = "warning"
    else:
        status = "fail"

    return {
        "revelation_count": revelation_count,
        "max_allowed": max_revelations,
        "within_budget": within_budget,
        "status": status,
    }


# ---------------------------------------------------------------------------
# Text change ratio
# ---------------------------------------------------------------------------


def text_change_ratio(before: str, after: str) -> float:
    """Estimate text change ratio: 0.0 = identical, 1.0 = total rewrite.

    Uses ``difflib.SequenceMatcher`` on whitespace-stripped character sequences
    for accurate CJK similarity measurement.

    This is the canonical implementation shared across pipeline stages.
    """
    if before == after:
        return 0.0
    b_stripped = "".join(before.split())
    a_stripped = "".join(after.split())
    if not b_stripped:
        return 1.0 if a_stripped else 0.0
    if not a_stripped:
        return 1.0
    ratio = difflib.SequenceMatcher(None, b_stripped, a_stripped).ratio()
    return round(1.0 - ratio, 4)
