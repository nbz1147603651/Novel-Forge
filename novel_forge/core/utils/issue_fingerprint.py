"""Stable issue fingerprinting for cross-recheck tracking.

After a repair round, recheck may assign new issue IDs even when the
underlying problem is unchanged.  ``stable_issue_fingerprint`` produces
a deterministic hash from ``issue_type + evidence_quote + paragraph_start``
so that ``resolved_issue_ids`` can be matched by fingerprint rather than
by raw ID set-difference.
"""

from __future__ import annotations

import hashlib
from typing import Any

__all__ = ["stable_issue_fingerprint"]

_FINGERPRINT_EVIDENCE_CHARS = 80


def stable_issue_fingerprint(issue: Any) -> str:
    """Compute a 16-char hex fingerprint for a continuity/causal/review issue.

    Works with any object exposing ``issue_type``, ``evidence_quote``
    (or ``evidence``), and ``paragraph_start`` attributes, or a plain dict
    with equivalent keys.

    The fingerprint is intentionally *not* a UUID — it is a short, stable
    identifier suitable for set-based matching across recheck cycles.
    """
    issue_type = _get(issue, "issue_type", "")
    evidence = _get(issue, "evidence_quote", "") or _get(issue, "evidence", "")
    paragraph_start = str(_get(issue, "paragraph_start", 0))

    # Normalize evidence: collapse whitespace, truncate.
    evidence_norm = "".join(str(evidence).split())[:_FINGERPRINT_EVIDENCE_CHARS]

    raw = f"{issue_type}|{evidence_norm}|{paragraph_start}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _get(source: Any, key: str, default: Any) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)
