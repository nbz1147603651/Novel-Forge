"""AuditQualityMetrics — quality metrics for book consistency audits.

Extracted from workspace/audit_quality_metrics.py so that pipeline can
import this type without a reverse dependency on workspace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AuditQualityMetrics:
    """Quality metrics derived from a book consistency audit.

    Attributes:
        coverage_ratio: Ratio of content audited vs total content (0.0-1.0).
        estimated_miss_rate: Estimated rate of missed issues (0.0-1.0).
        dimension_scores: Per-dimension quality scores (naming, timeline,
            worldbuilding, character_state, narrative_drift).
        audit_depth: Depth of audit performed (quick/full/detailed).
        total_issues_found: Total number of issues detected.
        critical_issues_found: Number of critical-severity issues detected.
    """

    coverage_ratio: float = 0.0
    estimated_miss_rate: float = 0.0
    dimension_scores: dict[str, float] = field(default_factory=dict)
    audit_depth: str = "quick"
    total_issues_found: int = 0
    critical_issues_found: int = 0

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "coverage_ratio": self.coverage_ratio,
            "estimated_miss_rate": self.estimated_miss_rate,
            "dimension_scores": self.dimension_scores,
            "audit_depth": self.audit_depth,
            "total_issues_found": self.total_issues_found,
            "critical_issues_found": self.critical_issues_found,
        }
