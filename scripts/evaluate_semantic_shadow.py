#!/usr/bin/env python3
"""Evaluate semantic shadow evidence without calling models or changing rollout flags."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from novel_forge.core.semantic_acceptance import (  # noqa: E402
    SemanticEvaluatedJudgment,
    evaluate_semantic_acceptance,
    load_semantic_acceptance_samples,
)
from novel_forge.persistence.repair_shadow import RepairShadowLedger  # noqa: E402

_DEFAULT_CORPUS = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "semantic_consistency"
    / "acceptance_v1.json"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=_DEFAULT_CORPUS)
    parser.add_argument(
        "--benchmark-results",
        type=Path,
        help="Optional JSON list of exact-corpus model judgments.",
    )
    parser.add_argument(
        "--project-root",
        action="append",
        type=Path,
        default=[],
        help="Project whose existing R7a shadow ledger contributes reviewed real samples.",
    )
    parser.add_argument("--minimum-real-reviewed", type=int, default=100)
    parser.add_argument("--json", action="store_true")
    return parser


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    samples = load_semantic_acceptance_samples(_load_json(args.corpus))
    evaluations: list[SemanticEvaluatedJudgment] = []
    if args.benchmark_results is not None:
        payload = _load_json(args.benchmark_results)
        if not isinstance(payload, list):
            raise ValueError("benchmark results must be a JSON list")
        evaluations.extend(SemanticEvaluatedJudgment.model_validate(item) for item in payload)
    for root in args.project_root:
        evaluations.extend(RepairShadowLedger(root).semantic_evaluations())
    report = evaluate_semantic_acceptance(
        samples,
        evaluations,
        minimum_real_reviewed=max(1, args.minimum_real_reviewed),
    )
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        print(
            "corpus="
            f"{report.corpus_total} "
            f"({report.corpus_conflicts}/{report.corpus_compatible}/"
            f"{report.corpus_ambiguous}) ready={report.corpus_ready}"
        )
        print(
            f"benchmark={report.benchmark.reviewed} passed={report.benchmark.passed}; "
            f"real={report.real_shadow.reviewed} passed={report.real_shadow.passed}; "
            f"main={report.main_gate.reviewed} passed={report.main_gate.passed}"
        )
        print(
            f"eligible_for_blocking={report.eligible_for_blocking}; "
            f"eligible_for_legacy_removal={report.eligible_for_legacy_removal}"
        )
        if report.reasons:
            print("reasons=" + ",".join(report.reasons))
    # A valid corpus/report is a successful audit run. Eligibility is reported
    # as data and never mutates the feature flag or exits as an operational error.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
