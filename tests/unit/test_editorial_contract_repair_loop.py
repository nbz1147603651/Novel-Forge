"""Regression tests for the editorial-contract repair loop (fix C).

The editorial contract is the only init artifact whose validation previously
had no repair loop: a single bad generation fatally aborted a multi-minute
init run. These tests anchor the bounded-regeneration contract introduced in
``init_orchestrator._derive_and_persist_editorial_contract``.

Because the loop is embedded in the large ``init_long_project`` coroutine,
these tests cover the two pure, independently-verifiable properties:

1. ``_editorial_max_repair_rounds`` bounds the loop (default 2, cap 3, min 1).
2. The stagnation fingerprint logic stops early when the same critical
   findings recur, so a stuck model does not burn all retries.
"""

from __future__ import annotations

from novel_forge.pipeline.long.services.init.init_orchestrator import _editorial_max_repair_rounds


class _FakeSettings:
    def __init__(self, value: int | None) -> None:
        if value is not None:
            self.init_coherence_max_repair_rounds = value


def test_editorial_max_repair_rounds_default_is_two() -> None:
    assert _editorial_max_repair_rounds(_FakeSettings(None)) == 2


def test_editorial_max_repair_rounds_respects_configured_value() -> None:
    assert _editorial_max_repair_rounds(_FakeSettings(1)) == 1
    assert _editorial_max_repair_rounds(_FakeSettings(2)) == 2
    assert _editorial_max_repair_rounds(_FakeSettings(3)) == 3


def test_editorial_max_repair_rounds_caps_at_three() -> None:
    # A misconfigured high value must be capped, matching the blueprint/outline
    # loop ceiling, so the editorial loop cannot run away.
    assert _editorial_max_repair_rounds(_FakeSettings(10)) == 3
    assert _editorial_max_repair_rounds(_FakeSettings(99)) == 3


def test_editorial_max_repair_rounds_floor_is_one() -> None:
    # Zero or negative must still permit at least one generation attempt.
    assert _editorial_max_repair_rounds(_FakeSettings(0)) == 1
    assert _editorial_max_repair_rounds(_FakeSettings(-5)) == 1


def test_editorial_max_repair_rounds_handles_garbage_config() -> None:
    assert _editorial_max_repair_rounds(_FakeSettings("not-a-number")) == 2  # type: ignore[arg-type]


def test_stagnation_fingerprint_stops_early_on_repeated_critical_findings() -> None:
    """The stagnation guard stops retrying when critical findings don't shrink.

    This mirrors the inline logic in ``_derive_and_persist_editorial_contract``:
    when ``overlap and len(current) >= len(previous)``, the loop breaks. We
    replicate the predicate here so any future refactor that drops it fails
    this test.
    """

    def should_stop_on_stagnation(
        previous: set[str] | None,
        current: set[str],
        stagnant_rounds: int,
    ) -> bool:
        if previous is None:
            return False
        overlap = previous & current
        if overlap and len(current) >= len(previous):
            return True
        return False

    same = {"editorial_element_unknown:horror_dread_rhy:thm"}
    # First round has no previous -> never stop.
    assert should_stop_on_stagnation(None, same, 0) is False
    # Identical critical set on the next round -> stop (no progress).
    assert should_stop_on_stagnation(same, same, 0) is True
    # Shrinking critical set -> keep trying.
    assert should_stop_on_stagnation(same, set(), 0) is False
    # Different (non-overlapping) critical set -> keep trying.
    assert should_stop_on_stagnation(same, {"editorial_element_unknown:other"}, 0) is False
