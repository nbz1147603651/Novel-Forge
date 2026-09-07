"""Issue signature utilities for stable issue identification across repair rounds."""

from __future__ import annotations

import hashlib
from typing import Any


def compute_issue_signature(issue: Any) -> str:
    """Compute a stable signature for a causal or continuity issue.

    The signature is computed from issue_type, summary, location, and
    paragraph_start fields using SHA256 hash (first 16 hex characters).
    Missing fields default to empty string for stability.
    """
    issue_type = getattr(issue, "issue_type", "") or ""
    summary = getattr(issue, "summary", "") or ""
    location = getattr(issue, "location", "") or ""
    paragraph_start = getattr(issue, "paragraph_start", 0) or 0

    return hashlib.sha256(
        f"{issue_type}|{summary}|{location}|{paragraph_start}".encode()
    ).hexdigest()[:16]


def compute_report_signature(report: Any) -> str:
    """Compute a signature for a report based on all its issue signatures.

    Combines all individual issue signatures from the report into a single
    hash for report-level identification and deduplication.
    """
    issues = getattr(report, "issues", None)
    if not issues:
        issues = getattr(report, "causal_issues", None) or []

    if not issues:
        return ""

    issue_sigs = [compute_issue_signature(issue) for issue in issues]
    combined = "|".join(issue_sigs)

    return hashlib.sha256(combined.encode()).hexdigest()[:16]