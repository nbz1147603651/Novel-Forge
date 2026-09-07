from __future__ import annotations

import json
from pathlib import Path

from novel_forge.core.semantic_acceptance import (
    REQUIRED_PHENOMENA,
    SemanticEvaluatedJudgment,
    evaluate_semantic_acceptance,
    load_semantic_acceptance_samples,
)

_CORPUS = (
    Path(__file__).parents[1]
    / "fixtures"
    / "semantic_consistency"
    / "acceptance_v1.json"
)


def _samples():
    return load_semantic_acceptance_samples(
        json.loads(_CORPUS.read_text(encoding="utf-8"))
    )


def _verdict(expected: str) -> tuple[str, bool]:
    if expected == "conflict":
        return "needs_repair", False
    if expected == "compatible":
        return "accept", False
    return "ambiguous", True


def _perfect_benchmark():
    result = []
    for sample in _samples():
        verdict, routed = _verdict(sample.expected)
        result.append(
            SemanticEvaluatedJudgment(
                evaluation_id=sample.sample_id,
                origin="benchmark",
                source_hash=sample.source_hash,
                expected=sample.expected,
                critical=sample.critical,
                verdict=verdict,
                routed_to_human=routed,
                model_call_id=f"benchmark:{sample.sample_id}",
            )
        )
    return result


def _perfect_real():
    result = []
    samples = _samples()
    for index in range(100):
        sample = samples[index]
        verdict, routed = _verdict(sample.expected)
        result.append(
            SemanticEvaluatedJudgment(
                evaluation_id=f"real:{index:03d}",
                origin="real_shadow",
                source_hash=f"real-source-{index:03d}",
                expected=sample.expected,
                critical=sample.critical,
                verdict=verdict,
                routed_to_human=routed,
                model_call_id=f"real-call:{index:03d}",
            )
        )
    return result


def _perfect_main_gate():
    return [
        item.model_copy(
            update={
                "evaluation_id": item.evaluation_id.replace("real:", "main:"),
                "origin": "main_gate",
                "model_call_id": item.model_call_id.replace("real-call:", "main-call:"),
                "legacy_fallback_verdict": (
                    "conflict" if item.expected == "conflict" else "no_conflict"
                ),
            }
        )
        for item in _perfect_real()
    ]


def test_acceptance_corpus_has_required_distribution_and_phenomena() -> None:
    report = evaluate_semantic_acceptance(_samples(), [])

    assert report.corpus_total == 100
    assert report.corpus_conflicts == 50
    assert report.corpus_compatible == 30
    assert report.corpus_ambiguous == 20
    assert REQUIRED_PHENOMENA.issubset(report.corpus_phenomena)
    assert report.corpus_ready is True
    assert report.eligible_for_blocking is False
    assert "real_shadow_samples_insufficient" in report.reasons


def test_blocking_requires_benchmark_and_reviewed_real_thresholds() -> None:
    samples = _samples()
    benchmark_only = evaluate_semantic_acceptance(samples, _perfect_benchmark())
    accepted = evaluate_semantic_acceptance(
        samples,
        [*_perfect_benchmark(), *_perfect_real()],
    )

    assert benchmark_only.benchmark.passed is True
    assert benchmark_only.eligible_for_blocking is False
    assert accepted.benchmark.critical_conflict_recall == 1.0
    assert accepted.benchmark.hard_block_precision == 1.0
    assert accepted.real_shadow.reviewed == 100
    assert accepted.eligible_for_blocking is True
    # SC5 additionally requires proving that the legacy fallback has no unique
    # true positive.  SC4 quality alone can never authorize its deletion.
    assert accepted.eligible_for_legacy_removal is False


def test_legacy_removal_needs_100_main_gate_reviews_and_zero_unique_hits() -> None:
    samples = _samples()
    benchmark = _perfect_benchmark()
    real = _perfect_real()
    main_gate = _perfect_main_gate()
    evidence = [*benchmark, *real, *main_gate]
    removable = evaluate_semantic_acceptance(samples, evidence)
    missed = main_gate[0].model_copy(
        update={"verdict": "ambiguous", "routed_to_human": True}
    )
    with_unique_hit = evaluate_semantic_acceptance(
        samples,
        [*benchmark, *real, missed, *main_gate[1:]],
    )

    assert removable.main_gate.reviewed == 100
    assert removable.eligible_for_legacy_removal is True
    assert with_unique_hit.legacy_unique_true_positives == 1
    assert with_unique_hit.eligible_for_legacy_removal is False
    assert "legacy_has_unique_true_positive" in with_unique_hit.reasons


def test_false_block_duplicate_call_or_silent_ambiguity_fails_gate() -> None:
    samples = _samples()
    benchmark = _perfect_benchmark()
    benchmark[50] = benchmark[50].model_copy(
        update={"verdict": "reject"},
    )
    benchmark[80] = benchmark[80].model_copy(
        update={"routed_to_human": False},
    )
    benchmark[1] = benchmark[1].model_copy(
        update={"model_call_id": benchmark[0].model_call_id},
    )
    report = evaluate_semantic_acceptance(samples, [*benchmark, *_perfect_real()])

    assert report.benchmark.compatible_hard_blocks == 1
    assert report.benchmark.ambiguous_not_routed == 1
    assert report.benchmark.duplicate_model_calls == 1
    assert report.eligible_for_blocking is False
