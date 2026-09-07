"""Evaluation logic and scoring — re-export shim.

The evaluator has been split into three focused modules:
- normalizer.py: issue normalization, dedup, anchor enrichment
- local_checks.py: _build_local_issues and heuristic detection
- scoring.py: _normalize_report, _apply_recheck_focus, _issue_penalty

This file re-exports the combined _EvaluatorMixin for backward compatibility
with any code that may have imported it directly.
"""

from __future__ import annotations

from novel_forge.pipeline.steps.continuity_eval.local_checks import _LocalChecks
from novel_forge.pipeline.steps.continuity_eval.scoring import _Scorer


class _EvaluatorMixin(_Scorer, _LocalChecks):
    """Combined evaluation mixin — inherits from _Scorer and _LocalChecks."""
    pass
