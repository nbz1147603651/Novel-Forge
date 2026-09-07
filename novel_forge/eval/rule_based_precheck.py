"""P1-2: Rule-based precheck for eval score inflation prevention.

Runs deterministic text analysis BEFORE LLM eval to establish score ceilings.
When the LLM assigns a dimension score above the ceiling, it is clamped down.
This prevents false perfect scores (e.g., Ch1 getting 10.0 despite text duplication).

Design principles:
- Pure functions, no LLM calls, no side effects
- Each check returns a ceiling for a specific dimension
- Ceilings are upper bounds: LLM can score lower but not higher
- Conservative thresholds to avoid false positives
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PrecheckResult:
    """Result of rule-based precheck with per-dimension score ceilings."""

    ceilings: dict[str, float] = field(default_factory=dict)
    """Map of dimension_name → maximum allowed score."""

    violations: list[str] = field(default_factory=list)
    """Human-readable descriptions of detected issues."""

    @property
    def has_violations(self) -> bool:
        return len(self.violations) > 0


# ── Detection functions ────────────────────────────────────────────────────────

_EXACT_PHRASE_REPEAT_RE = re.compile(
    r"(?P<phrase>[\u4e00-\u9fff]{4,15})(?:[，,、；;：:\s]*)(?P=phrase)"
)

_EM_DASH_RE = re.compile(r"——")

_DIALOGUE_RE = re.compile(r"[「""][^」""]*[」""]")


def _detect_text_duplication(text: str) -> int:
    """Count exact phrase repetitions (N-gram duplicates)."""
    matches = _EXACT_PHRASE_REPEAT_RE.findall(text)
    return len(matches)


def _detect_em_dash_density(text: str) -> int:
    """Count em-dash occurrences outside dialogue."""
    # Remove dialogue spans
    masked = _DIALOGUE_RE.sub("", text)
    return len(_EM_DASH_RE.findall(masked))


def _detect_dialogue_ratio(text: str) -> float:
    """Calculate dialogue character ratio."""
    dialogue_chars = sum(len(m.group()) for m in _DIALOGUE_RE.finditer(text))
    total_chars = max(len(text), 1)
    return dialogue_chars / total_chars


def _detect_sentence_length_variance(text: str) -> float:
    """Calculate sentence length variance (low = monotonous pacing)."""
    sentences = re.split(r"[。！？\n]+", text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 2]
    if len(sentences) < 5:
        return 100.0  # Too few sentences to judge
    lengths = [len(s) for s in sentences]
    mean = sum(lengths) / len(lengths)
    variance = sum((x - mean) ** 2 for x in lengths) / len(lengths)
    return variance


# ── Main precheck entry point ──────────────────────────────────────────────────


def run_rule_based_precheck(
    text: str,
    *,
    duplication_ceiling: float = 8.0,
    em_dash_density_threshold: int = 10,
    em_dash_style_ceiling: float = 7.0,
    dialogue_ratio_threshold: float = 0.15,
    dialogue_engagement_ceiling: float = 7.0,
    pacing_variance_threshold: float = 50.0,
    pacing_ceiling: float = 8.0,
) -> PrecheckResult:
    """Run deterministic precheck on chapter text and return score ceilings.

    Args:
        text: Chapter text to analyze.
        duplication_ceiling: Max overall score when text duplication detected.
        em_dash_density_threshold: Em-dash count threshold for style penalty.
        em_dash_style_ceiling: Max style score when em-dash overuse detected.
        dialogue_ratio_threshold: Min dialogue ratio for engagement.
        dialogue_engagement_ceiling: Max engagement score when dialogue too low.
        pacing_variance_threshold: Min sentence length variance for pacing.
        pacing_ceiling: Max pacing score when monotonous rhythm detected.

    Returns:
        PrecheckResult with per-dimension ceilings and violation descriptions.
    """
    ceilings: dict[str, float] = {}
    violations: list[str] = []

    # ── 1. Text duplication → overall cap ─────────────────────────────────
    dup_count = _detect_text_duplication(text)
    if dup_count > 2:
        ceilings["overall"] = duplication_ceiling
        violations.append(
            f"文本重复检测：发现 {dup_count} 处精确短语重复 → 综合分上限 {duplication_ceiling:.1f}"
        )

    # ── 2. Em-dash density → style cap ────────────────────────────────────
    em_dash_count = _detect_em_dash_density(text)
    if em_dash_count > em_dash_density_threshold:
        ceilings["style"] = em_dash_style_ceiling
        violations.append(
            f"破折号密度：{em_dash_count} 个（阈值 {em_dash_density_threshold}）→ 风格分上限 {em_dash_style_ceiling:.1f}"
        )

    # ── 3. Dialogue ratio → engagement cap ────────────────────────────────
    dialogue_ratio = _detect_dialogue_ratio(text)
    if dialogue_ratio < dialogue_ratio_threshold:
        ceilings["engagement"] = dialogue_engagement_ceiling
        violations.append(
            f"对话比例：{dialogue_ratio:.1%}（阈值 {dialogue_ratio_threshold:.0%}）→ 吸引力分上限 {dialogue_engagement_ceiling:.1f}"
        )

    # ── 4. Sentence length variance → pacing cap ──────────────────────────
    variance = _detect_sentence_length_variance(text)
    if variance < pacing_variance_threshold:
        ceilings["pacing"] = pacing_ceiling
        violations.append(
            f"句长方差：{variance:.1f}（阈值 {pacing_variance_threshold:.1f}）→ 节奏分上限 {pacing_ceiling:.1f}"
        )

    return PrecheckResult(ceilings=ceilings, violations=violations)


def apply_score_ceilings(
    scores: dict[str, Any],
    overall_score: float,
    precheck: PrecheckResult,
) -> tuple[dict[str, Any], float]:
    """Clamp LLM eval scores to precheck ceilings.

    Args:
        scores: Dict of dimension → score from LLM eval.
        overall_score: Overall score from LLM eval.
        precheck: PrecheckResult with ceilings.

    Returns:
        (clamped_scores, clamped_overall) tuple.
    """
    if not precheck.has_violations:
        return scores, overall_score

    clamped_scores = dict(scores)
    for dim, ceiling in precheck.ceilings.items():
        if dim == "overall":
            continue
        if dim in clamped_scores:
            current = clamped_scores[dim]
            current_val = float(current) if isinstance(current, (int, float)) else 0.0
            if hasattr(current, "score"):
                # EvalDimensionScore object
                if current_val > ceiling:
                    clamped_scores[dim] = ceiling
            elif current_val > ceiling:
                clamped_scores[dim] = ceiling

    clamped_overall = overall_score
    if "overall" in precheck.ceilings:
        clamped_overall = min(overall_score, precheck.ceilings["overall"])

    return clamped_scores, clamped_overall
