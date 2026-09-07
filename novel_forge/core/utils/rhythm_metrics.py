"""Chapter rhythm / structure metrics.

These functions compute a compact "rhythm signature" from chapter prose —
sentence-length distribution, short/long sentence ratios, paragraph pacing —
and a similarity score between two signatures. They underpin two features:

1. humanize cross-chapter structural-template detection (Improvement 3): flag
   when the current chapter's rhythm signature is near-identical to recent
   chapters, i.e. the same pacing pattern is calcifying into a template.
2. blueprint rhythm-curve conformance (Improvement 4): compare the actual
   rhythm against the per-chapter target declared in the blueprint.

The sentence-splitting approach is extracted from
``humanize_scan_step._prescreen_monotone_rhythm`` and generalized to the whole
chapter (not just a single paragraph's first 6 sentences).
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any

_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;]+")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
_SHORT_SENTENCE_MAX = 8
_LONG_SENTENCE_MIN = 45


@dataclass(frozen=True)
class ChapterRhythmSignature:
    """Compact rhythm fingerprint of a chapter's prose.

    Fields are chosen so two chapters with the same pacing feel produce near-
    identical signatures, while genuinely different rhythms diverge. All
    numeric fields are JSON-serializable for prompt injection and persistence.
    """

    avg_sentence_len: float
    std_sentence_len: float
    short_sentence_ratio: float
    long_sentence_ratio: float
    paragraph_count: int
    avg_paragraph_len: float
    # Per-paragraph average sentence length, downsampled to <= 12 buckets so
    # the "shape" of the pacing curve is comparable across chapters of unequal
    # length without a fixed-length assumption.
    paragraph_pacing_curve: list[float] = field(default_factory=list)
    sentence_count: int = 0

    def to_dict(self) -> dict[str, float | int | list[float]]:
        return {
            "avg_sentence_len": round(self.avg_sentence_len, 2),
            "std_sentence_len": round(self.std_sentence_len, 2),
            "short_sentence_ratio": round(self.short_sentence_ratio, 3),
            "long_sentence_ratio": round(self.long_sentence_ratio, 3),
            "paragraph_count": self.paragraph_count,
            "avg_paragraph_len": round(self.avg_paragraph_len, 2),
            "paragraph_pacing_curve": [round(x, 2) for x in self.paragraph_pacing_curve],
            "sentence_count": self.sentence_count,
        }


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in _PARAGRAPH_SPLIT_RE.split(text) if p.strip()]


def _downsample(values: list[float], target_buckets: int = 12) -> list[float]:
    """Resample a sequence to a fixed number of buckets by averaging.

    Lets two chapters of very different length produce comparable pacing
    curves: a 200-paragraph chapter and a 50-paragraph chapter both yield 12
    buckets representing their pacing shape.
    """
    if not values:
        return []
    if len(values) <= target_buckets:
        return [float(v) for v in values]
    bucket_size = len(values) / target_buckets
    out: list[float] = []
    for i in range(target_buckets):
        start = int(i * bucket_size)
        end = int((i + 1) * bucket_size)
        chunk = values[start:max(end, start + 1)]
        out.append(sum(chunk) / len(chunk) if chunk else 0.0)
    return out


def compute_chapter_rhythm_signature(text: str) -> ChapterRhythmSignature:
    """Compute a rhythm signature for ``text``.

    Returns a zeroed signature for empty/degenerate input so callers can
    compare safely without None-handling.
    """
    if not text or not text.strip():
        return ChapterRhythmSignature(0.0, 0.0, 0.0, 0.0, 0, 0.0)

    sentences = _split_sentences(text)
    if not sentences:
        return ChapterRhythmSignature(0.0, 0.0, 0.0, 0.0, 0, 0.0)

    lengths = [len(s) for s in sentences]
    n = len(lengths)
    avg = statistics.mean(lengths)
    std = statistics.pstdev(lengths) if n > 1 else 0.0
    short = sum(1 for length in lengths if length <= _SHORT_SENTENCE_MAX) / n
    long = sum(1 for length in lengths if length >= _LONG_SENTENCE_MIN) / n

    paragraphs = _split_paragraphs(text)
    para_count = len(paragraphs)
    para_lengths = [len(_split_sentences(p)) for p in paragraphs if p]
    avg_para_len = statistics.mean(para_lengths) if para_lengths else 0.0
    # Pacing curve: per-paragraph average sentence length, downsampled.
    para_avg_lens = [
        statistics.mean([len(s) for s in _split_sentences(p)]) if _split_sentences(p) else 0.0
        for p in paragraphs
    ]
    pacing_curve = _downsample(para_avg_lens)

    return ChapterRhythmSignature(
        avg_sentence_len=avg,
        std_sentence_len=std,
        short_sentence_ratio=short,
        long_sentence_ratio=long,
        paragraph_count=para_count,
        avg_paragraph_len=avg_para_len,
        paragraph_pacing_curve=pacing_curve,
        sentence_count=n,
    )


def compute_structure_similarity(
    sig_a: ChapterRhythmSignature | dict[str, Any],
    sig_b: ChapterRhythmSignature | dict[str, Any],
) -> float:
    """Return a 0-1 similarity between two rhythm signatures.

    1.0 = near-identical pacing shape (potential template); 0.0 = totally
    divergent. Combines scalar-feature distance (avg/std/short/long ratios)
    with the paragraph-pacing-curve shape distance (so two chapters with the
    same per-paragraph rhythm — e.g. imagery always at the same position —
    score high).
    """
    a = sig_a.to_dict() if isinstance(sig_a, ChapterRhythmSignature) else dict(sig_a)
    b = sig_b.to_dict() if isinstance(sig_b, ChapterRhythmSignature) else dict(sig_b)

    def _f(d: dict[str, Any], key: str, default: float = 0.0) -> float:
        try:
            return float(d.get(key, default) or default)
        except (TypeError, ValueError):
            return default

    # Scalar feature distance: normalize each feature to a 0-1 contribution.
    # Use relative difference clamped to [0,1].
    def _rel_diff(x: float, y: float) -> float:
        denom = max(abs(x), abs(y), 1.0)
        return min(1.0, abs(x - y) / denom)

    scalar_keys = ("avg_sentence_len", "std_sentence_len", "short_sentence_ratio", "long_sentence_ratio")
    scalar_distance = sum(_rel_diff(_f(a, k), _f(b, k)) for k in scalar_keys) / len(scalar_keys)

    # Pacing-curve shape distance via normalized sequence correlation.
    curve_a = list(a.get("paragraph_pacing_curve") or [])
    curve_b = list(b.get("paragraph_pacing_curve") or [])
    curve_distance = _curve_distance(curve_a, curve_b)

    # Weight: scalar features 60%, curve shape 40%.
    distance = 0.6 * scalar_distance + 0.4 * curve_distance
    return max(0.0, min(1.0, 1.0 - distance))


def _curve_distance(curve_a: list[float], curve_b: list[float]) -> float:
    """Distance between two pacing curves in [0,1] (0=identical shape)."""
    if not curve_a or not curve_b:
        return 1.0
    # Align to the shorter via downsampling so length differences don't dominate.
    target = min(len(curve_a), len(curve_b), 12)
    a = _downsample(curve_a, target) if len(curve_a) > target else curve_a
    b = _downsample(curve_b, target) if len(curve_b) > target else curve_b
    n = min(len(a), len(b))
    if n == 0:
        return 1.0
    # Normalized mean absolute difference.
    total = 0.0
    for i in range(n):
        denom = max(abs(a[i]), abs(b[i]), 1.0)
        total += abs(a[i] - b[i]) / denom
    return min(1.0, total / n)


__all__ = [
    "ChapterRhythmSignature",
    "compute_chapter_rhythm_signature",
    "compute_structure_similarity",
]
