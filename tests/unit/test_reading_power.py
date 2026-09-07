"""Tests for ReadingPowerReport schema and compute_score logic.

Phase 3: Updated for multiplicative calibration formula.
- Hook multiplier: 2.5→2.0 (strong hook normalized = 8.0, not 10.0)
- Payoff cap: 3→5 (more payoffs contribute before capping)
- Bonuses/penalties: additive → multiplicative ratios (×1.03~1.05)
- Score bounds remain 0-10
"""

from __future__ import annotations

import pytest

from novel_forge.core.schemas.reading_power import (
    MicroPayoff,
    MicroPayoffType,
    ReadingPowerReport,
)


def _make_report(**kwargs) -> ReadingPowerReport:
    defaults = dict(chapter=1, hook_type="none", hook_strength="weak", prev_hook_fulfilled=True)
    defaults.update(kwargs)
    return ReadingPowerReport(**defaults)


# ── Hook scoring ─────────────────────────────────────────────────────────


def test_strong_hook_scores_4_pts() -> None:
    r = _make_report(hook_type="crisis", hook_strength="strong")
    r.compute_score()
    # hook_norm=8.0*0.25=2.0, main_plot=6.0*0.15=0.9, weighted=2.9
    # ×1.03 fulfilled=2.987, -0.5 deficit=2.487
    assert r.overall_score == pytest.approx(2.5)


def test_medium_hook_scores_2_5_pts() -> None:
    r = _make_report(hook_type="mystery", hook_strength="medium")
    r.compute_score()
    # hook_norm=5.0*0.25=1.25, main_plot=0.9, weighted=2.15
    # ×1.03=2.2145, -0.5=1.7145
    assert r.overall_score == pytest.approx(1.7)


def test_weak_hook_scores_1_pt() -> None:
    r = _make_report(hook_type="emotion", hook_strength="weak")
    r.compute_score()
    # hook_norm=2.0*0.25=0.5, main_plot=0.9, weighted=1.4
    # ×1.03=1.442, -0.5=0.942
    assert r.overall_score == pytest.approx(0.9)


def test_no_hook_scores_0_hook_pts() -> None:
    r = _make_report(hook_type="none", hook_strength="weak")
    r.compute_score()
    # hook_norm=0, main_plot=0.9, weighted=0.9
    # ×1.03=0.927, -0.5=0.427
    assert r.overall_score == pytest.approx(0.4)


def test_transition_chapter_no_hook_gets_penalty() -> None:
    r = _make_report(hook_type="none", hook_strength="weak", is_transition=True)
    r.compute_score()
    # weighted=0.9, ×1.03=0.927, -1.0 transition=-0.073, -0.5 deficit=-0.573
    # floor at 0
    assert r.overall_score == pytest.approx(0.0)


def test_transition_chapter_with_hook_no_penalty() -> None:
    r = _make_report(hook_type="crisis", hook_strength="medium", is_transition=True)
    r.compute_score()
    # hook_norm=5.0*0.25=1.25, main_plot=0.9, weighted=2.15
    # ×1.03=2.2145, no transition penalty (has hook), -0.5=1.7145
    assert r.overall_score == pytest.approx(1.7)


# ── Micro-payoff scoring ─────────────────────────────────────────────────


def test_three_payoffs_add_score() -> None:
    payoffs = [
        MicroPayoff(payoff_type=MicroPayoffType.INFORMATION),
        MicroPayoff(payoff_type=MicroPayoffType.RELATIONSHIP),
        MicroPayoff(payoff_type=MicroPayoffType.ABILITY),
    ]
    r = _make_report(hook_type="crisis", hook_strength="strong", micro_payoffs=payoffs)
    r.compute_score()
    # hook_norm=8.0*0.25=2.0
    # payoffs: 3×(1.0*1.0*0.7)=2.1, norm=min(10,2.1*2.0)=4.2, *0.20=0.84
    # main_plot=0.9, weighted=3.74, ×1.03=3.8522
    assert r.overall_score == pytest.approx(3.9)


def test_payoffs_capped_at_5() -> None:
    payoffs = [MicroPayoff(payoff_type=MicroPayoffType.CLUE) for _ in range(6)]
    r = _make_report(hook_type="crisis", hook_strength="strong", micro_payoffs=payoffs)
    r.compute_score()
    # hook_norm=8.0*0.25=2.0
    # payoffs: 6×(1.2*1.2*0.7)=6.048, norm=min(10,6.048*2.0)=10.0, *0.20=2.0
    # main_plot=0.9, weighted=4.9, ×1.03=5.047
    assert r.overall_score == pytest.approx(5.0)


def test_zero_payoffs_get_default_deficit_penalty() -> None:
    r = _make_report(hook_type="desire", hook_strength="medium")
    r.compute_score()
    # hook_norm=5.0*0.25=1.25, main_plot=0.9, weighted=2.15
    # ×1.03=2.2145, -0.5=1.7145
    assert r.overall_score == pytest.approx(1.7)


def test_payoff_strength_affects_score() -> None:
    payoffs = [
        MicroPayoff(payoff_type=MicroPayoffType.INFORMATION, strength="strong"),
        MicroPayoff(payoff_type=MicroPayoffType.RELATIONSHIP, strength="weak"),
    ]
    r = _make_report(hook_type="crisis", hook_strength="strong", micro_payoffs=payoffs)
    r.compute_score()
    # hook_norm=8.0*0.25=2.0
    # payoffs: 1.2*1.0*0.7 + 0.6*1.0*0.7 = 0.84+0.42=1.26, norm=2.52, *0.20=0.504
    # main_plot=0.9, weighted=3.404, ×1.03=3.506
    assert r.overall_score == pytest.approx(3.5)


# ── Hook fulfillment ─────────────────────────────────────────────────────


def test_unfulfilled_prev_hook_loses_score() -> None:
    r = _make_report(
        hook_type="mystery", hook_strength="strong", prev_hook_fulfilled=False
    )
    r.compute_score()
    # hook_norm=8.0*0.25=2.0, main_plot=0.9, weighted=2.9
    # ÷1.03 unfulfilled=2.8155, -0.5=2.3155
    assert r.overall_score == pytest.approx(2.3)


# ── Score bounds ─────────────────────────────────────────────────────────


def test_score_never_exceeds_10() -> None:
    payoffs = [MicroPayoff(payoff_type=MicroPayoffType.EMOTION) for _ in range(10)]
    r = _make_report(
        hook_type="crisis",
        hook_strength="strong",
        micro_payoffs=payoffs,
        prev_hook_fulfilled=True,
    )
    r.compute_score()
    assert r.overall_score <= 10.0


def test_score_never_below_0() -> None:
    r = _make_report(
        hook_type="none",
        hook_strength="weak",
        is_transition=True,
        prev_hook_fulfilled=False,
    )
    r.compute_score()
    assert r.overall_score >= 0.0


# ── Suggestions ──────────────────────────────────────────────────────────


def test_no_hook_generates_suggestion() -> None:
    r = _make_report(hook_type="none", hook_strength="weak")
    r.compute_score()
    assert any("钩子" in s for s in r.suggestions)


def test_strong_hook_no_missing_hook_suggestion() -> None:
    r = _make_report(hook_type="crisis", hook_strength="strong")
    r.compute_score()
    assert not any("缺少钩子" in s for s in r.suggestions)
