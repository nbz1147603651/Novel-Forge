"""Offline repair replay must be deterministic and write-free."""

from __future__ import annotations

from pathlib import Path

from novel_forge.pipeline.repair_orchestration.replay import replay_repair_fixtures


def test_fixed_repair_replay_set_passes_without_modifying_fixtures() -> None:
    fixtures = Path(__file__).parents[1] / "fixtures" / "repair_replay"
    before = {path.name: path.read_bytes() for path in fixtures.glob("*.json")}

    results = replay_repair_fixtures(fixtures)

    assert len(results) == 3
    assert all(result.passed for result in results)
    assert {path.name: path.read_bytes() for path in fixtures.glob("*.json")} == before


def test_duration_replay_preserves_raw_values_and_proves_normalized_equivalence() -> None:
    fixture = (
        Path(__file__).parents[1]
        / "fixtures"
        / "repair_replay"
        / "duration_equivalence.json"
    )

    [result] = replay_repair_fixtures(fixture)

    assert result.passed is True
    assert result.checks == ("raw_values_retained", "normalized_comparator_replayed")
