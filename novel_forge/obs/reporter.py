"""QualityReporter — generate summary reports."""

from __future__ import annotations

from novel_forge.core.schemas.eval_schema import EvalReport


class QualityReporter:
    """Generates a quality report from evaluation results."""

    @staticmethod
    def report(eval_report: EvalReport) -> dict[str, object]:
        return {
            "overall_score": eval_report.overall_score,
            "passed": eval_report.passed,
            "threshold": eval_report.threshold,
            "dimensions": {
                s.dimension: {"score": s.score, "comment": s.comment}
                for s in eval_report.scores
            },
            "summary": eval_report.summary,
        }
