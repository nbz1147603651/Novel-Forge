from __future__ import annotations

from types import SimpleNamespace

from novel_forge.pipeline.long.services.init.init_coherence_v2 import (
    _adaptive_recheck_claim_limit,
)


def test_adaptive_limit_scales_with_focus_window() -> None:
    settings = SimpleNamespace(
        init_coherence_recheck_max_claims=240,
        init_coherence_recheck_claims_per_chapter=40,
    )
    # 12-chapter window: 40 * 12 = 480 beats the 240 base cap.  This is the
    # shape that previously failed deterministically at claims=278.
    assert _adaptive_recheck_claim_limit(settings, list(range(1, 13))) == 480
    # Small windows keep the base cap.
    assert _adaptive_recheck_claim_limit(settings, [1, 2, 3]) == 240


def test_adaptive_limit_disabled_with_zero_per_chapter() -> None:
    settings = SimpleNamespace(
        init_coherence_recheck_max_claims=240,
        init_coherence_recheck_claims_per_chapter=0,
    )
    assert _adaptive_recheck_claim_limit(settings, list(range(1, 13))) == 240


def test_adaptive_limit_uses_defaults_for_missing_settings() -> None:
    settings = SimpleNamespace()
    assert _adaptive_recheck_claim_limit(settings, list(range(1, 13))) == 480
    # No focus chapters: plain base cap.
    assert _adaptive_recheck_claim_limit(settings, []) == 240
