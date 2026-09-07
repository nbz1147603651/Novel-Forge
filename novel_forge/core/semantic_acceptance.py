"""Pure acceptance metrics for semantic-consistency shadow evaluation.

The evaluator contains rollout thresholds, not narrative semantics.  It never
decides whether two facts conflict; it only compares reviewed labels with
model verdicts and verifies that shadow execution stayed evidence-only.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from novel_forge.core.semantic_consistency import semantic_payload_hash

SemanticExpectedLabel = Literal["conflict", "compatible", "ambiguous"]
SemanticModelVerdict = Literal[
    "accept",
    "needs_repair",
    "reject",
    "ambiguous",
    "defer",
    "failed",
]
SemanticEvaluationOrigin = Literal["benchmark", "real_shadow", "main_gate"]
LegacyFallbackVerdict = Literal["conflict", "no_conflict", "not_run"]

REQUIRED_PHENOMENA = frozenset(
    {
        "synonym_rewrite",
        "ordinal_deadline",
        "flashback",
        "hypothetical",
        "character_misbelief",
        "cross_field",
        "cross_source",
    }
)


class SemanticAcceptanceSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str = Field(min_length=1)
    expected: SemanticExpectedLabel
    critical: bool = False
    phenomena: list[str] = Field(min_length=1)
    source_a: str = Field(min_length=1)
    source_b: str = Field(min_length=1)

    @property
    def source_hash(self) -> str:
        return semantic_payload_hash(
            {
                "sample_id": self.sample_id,
                "source_a": self.source_a,
                "source_b": self.source_b,
            }
        )


class SemanticEvaluatedJudgment(BaseModel):
    """One model result plus an exact human/fixture label."""

    model_config = ConfigDict(extra="forbid")

    evaluation_id: str = Field(min_length=1)
    origin: SemanticEvaluationOrigin
    source_hash: str = Field(min_length=1)
    expected: SemanticExpectedLabel
    critical: bool = False
    verdict: SemanticModelVerdict
    structured_success: bool = True
    routed_to_human: bool = False
    model_call_id: str = ""
    official_write_count: int = Field(default=0, ge=0)
    stale_approval_publish_count: int = Field(default=0, ge=0)
    legacy_fallback_verdict: LegacyFallbackVerdict = "not_run"
    reviewed: bool = True

    @model_validator(mode="after")
    def _validate_result(self) -> "SemanticEvaluatedJudgment":
        if self.structured_success and self.verdict == "failed":
            raise ValueError("a failed verdict cannot be a structured success")
        return self


class SemanticAcceptanceMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = 0
    reviewed: int = 0
    conflicts: int = 0
    compatible: int = 0
    ambiguous: int = 0
    critical_conflict_recall: float = 0.0
    hard_block_precision: float = 0.0
    compatible_hard_blocks: int = 0
    ambiguous_not_routed: int = 0
    structured_success_rate: float = 0.0
    official_write_count: int = 0
    stale_approval_publish_count: int = 0
    duplicate_model_calls: int = 0
    passed: bool = False


class SemanticAcceptanceReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corpus_total: int
    corpus_conflicts: int
    corpus_compatible: int
    corpus_ambiguous: int
    corpus_phenomena: list[str]
    corpus_ready: bool
    benchmark: SemanticAcceptanceMetrics
    real_shadow: SemanticAcceptanceMetrics
    main_gate: SemanticAcceptanceMetrics
    legacy_unique_true_positives: int
    eligible_for_blocking: bool
    eligible_for_legacy_removal: bool
    reasons: list[str]


def load_semantic_acceptance_samples(payload: object) -> list[SemanticAcceptanceSample]:
    if not isinstance(payload, list):
        raise ValueError("semantic acceptance corpus must be a list")
    samples = [SemanticAcceptanceSample.model_validate(item) for item in payload]
    ids = [item.sample_id for item in samples]
    if len(ids) != len(set(ids)):
        raise ValueError("semantic acceptance sample ids must be unique")
    return samples


def _metrics(
    evaluations: list[SemanticEvaluatedJudgment],
    *,
    minimum_reviewed: int,
) -> SemanticAcceptanceMetrics:
    reviewed = [item for item in evaluations if item.reviewed]
    counts = Counter(item.expected for item in reviewed)
    critical_conflicts = [
        item for item in reviewed if item.expected == "conflict" and item.critical
    ]
    hard_blocks = [item for item in reviewed if item.verdict in {"needs_repair", "reject"}]
    true_hard_blocks = [item for item in hard_blocks if item.expected == "conflict"]
    compatible_hard_blocks = sum(
        1
        for item in reviewed
        if item.expected == "compatible" and item.verdict in {"needs_repair", "reject"}
    )
    ambiguous_not_routed = sum(
        1
        for item in reviewed
        if item.expected == "ambiguous"
        and not (
            item.verdict in {"ambiguous", "defer"}
            and item.routed_to_human
        )
    )
    critical_recall = (
        sum(
            1
            for item in critical_conflicts
            if item.verdict in {"needs_repair", "reject"}
        )
        / len(critical_conflicts)
        if critical_conflicts
        else 0.0
    )
    hard_block_precision = (
        len(true_hard_blocks) / len(hard_blocks) if hard_blocks else 0.0
    )
    structured_success_rate = (
        sum(1 for item in reviewed if item.structured_success) / len(reviewed)
        if reviewed
        else 0.0
    )
    call_ids = [item.model_call_id for item in reviewed if item.model_call_id]
    duplicate_calls = len(call_ids) - len(set(call_ids))
    official_writes = sum(item.official_write_count for item in reviewed)
    stale_publishes = sum(item.stale_approval_publish_count for item in reviewed)
    passed = (
        len(reviewed) >= minimum_reviewed
        and bool(critical_conflicts)
        and critical_recall == 1.0
        and hard_block_precision >= 0.98
        and compatible_hard_blocks == 0
        and ambiguous_not_routed == 0
        and structured_success_rate >= 0.99
        and official_writes == 0
        and stale_publishes == 0
        and duplicate_calls == 0
    )
    return SemanticAcceptanceMetrics(
        total=len(evaluations),
        reviewed=len(reviewed),
        conflicts=counts["conflict"],
        compatible=counts["compatible"],
        ambiguous=counts["ambiguous"],
        critical_conflict_recall=critical_recall,
        hard_block_precision=hard_block_precision,
        compatible_hard_blocks=compatible_hard_blocks,
        ambiguous_not_routed=ambiguous_not_routed,
        structured_success_rate=structured_success_rate,
        official_write_count=official_writes,
        stale_approval_publish_count=stale_publishes,
        duplicate_model_calls=duplicate_calls,
        passed=passed,
    )


def evaluate_semantic_acceptance(
    samples: list[SemanticAcceptanceSample],
    evaluations: list[SemanticEvaluatedJudgment],
    *,
    minimum_real_reviewed: int = 100,
) -> SemanticAcceptanceReport:
    """Evaluate fixed-corpus quality and separately require reviewed real shadows."""

    sample_by_id = {item.sample_id: item for item in samples}
    benchmark: list[SemanticEvaluatedJudgment] = []
    real: list[SemanticEvaluatedJudgment] = []
    main_gate: list[SemanticEvaluatedJudgment] = []
    for evaluation in evaluations:
        if evaluation.origin == "benchmark":
            sample = sample_by_id.get(evaluation.evaluation_id)
            if sample is None:
                raise ValueError(
                    f"benchmark result references unknown sample: {evaluation.evaluation_id}"
                )
            if (
                evaluation.source_hash != sample.source_hash
                or evaluation.expected != sample.expected
                or evaluation.critical != sample.critical
            ):
                raise ValueError(
                    f"benchmark result is not bound to its exact label/source: {sample.sample_id}"
                )
            benchmark.append(evaluation)
        elif evaluation.origin == "real_shadow":
            real.append(evaluation)
        else:
            main_gate.append(evaluation)

    counts = Counter(item.expected for item in samples)
    phenomena = sorted({value for item in samples for value in item.phenomena})
    corpus_ready = (
        len(samples) >= 100
        and counts["conflict"] >= 50
        and counts["compatible"] >= 30
        and counts["ambiguous"] >= 20
        and REQUIRED_PHENOMENA.issubset(phenomena)
    )
    benchmark_metrics = _metrics(benchmark, minimum_reviewed=len(samples))
    real_metrics = _metrics(real, minimum_reviewed=minimum_real_reviewed)
    main_gate_metrics = _metrics(main_gate, minimum_reviewed=100)
    eligible = corpus_ready and benchmark_metrics.passed and real_metrics.passed
    legacy_unique_true_positives = sum(
        1
        for item in main_gate
        if item.reviewed
        and item.expected == "conflict"
        and item.legacy_fallback_verdict == "conflict"
        and item.verdict not in {"needs_repair", "reject"}
    )
    legacy_removal = (
        eligible
        and main_gate_metrics.passed
        and legacy_unique_true_positives == 0
        and all(item.legacy_fallback_verdict != "not_run" for item in main_gate)
    )
    reasons: list[str] = []
    if not corpus_ready:
        reasons.append("fixed_corpus_incomplete")
    if not benchmark_metrics.passed:
        reasons.append("benchmark_thresholds_not_met")
    if real_metrics.reviewed < minimum_real_reviewed:
        reasons.append("real_shadow_samples_insufficient")
    elif not real_metrics.passed:
        reasons.append("real_shadow_thresholds_not_met")
    if main_gate_metrics.reviewed < 100:
        reasons.append("main_gate_reviewed_samples_insufficient")
    elif not main_gate_metrics.passed:
        reasons.append("main_gate_thresholds_not_met")
    if any(item.legacy_fallback_verdict == "not_run" for item in main_gate):
        reasons.append("legacy_parallel_comparison_incomplete")
    if legacy_unique_true_positives:
        reasons.append("legacy_has_unique_true_positive")
    return SemanticAcceptanceReport(
        corpus_total=len(samples),
        corpus_conflicts=counts["conflict"],
        corpus_compatible=counts["compatible"],
        corpus_ambiguous=counts["ambiguous"],
        corpus_phenomena=phenomena,
        corpus_ready=corpus_ready,
        benchmark=benchmark_metrics,
        real_shadow=real_metrics,
        main_gate=main_gate_metrics,
        legacy_unique_true_positives=legacy_unique_true_positives,
        eligible_for_blocking=eligible,
        eligible_for_legacy_removal=legacy_removal,
        reasons=reasons,
    )


__all__ = [
    "REQUIRED_PHENOMENA",
    "SemanticAcceptanceMetrics",
    "SemanticAcceptanceReport",
    "SemanticAcceptanceSample",
    "SemanticEvaluatedJudgment",
    "evaluate_semantic_acceptance",
    "load_semantic_acceptance_samples",
]
