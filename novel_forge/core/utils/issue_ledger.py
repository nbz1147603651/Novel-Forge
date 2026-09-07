"""Issue Ledger — stable issue tracking across repair rounds.

Tracks issues by content fingerprint to distinguish:
- **unresolved**: existed before repair, still present after
- **new**: did not exist before, appeared after repair (regression)
- **resolved**: existed before, gone after repair
- **downgraded**: severity decreased after repair
- **upgraded**: severity increased after repair (escalation)

Also detects **likely false positives** — resolved issues that probably
disappeared due to LLM evaluation variance rather than genuine repair.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from novel_forge.core.utils.audit_issue import (
    legacy_issue_fingerprint,
    normalize_audit_issue,
    normalize_location_key,
    severity_rank,
)


class IssueStatus(str, Enum):
    UNRESOLVED = "unresolved"
    NEW = "new"
    RESOLVED = "resolved"
    DOWNGRADED = "downgraded"
    UPGRADED = "upgraded"


_SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def _sev_rank(sev: str) -> int:
    return severity_rank(sev)


def _normalize_location_key(location: str) -> str:
    """Normalize location text for loose matching (e.g. 开头第1段 / 第1段)."""
    return normalize_location_key(location)


def _fingerprint(issue_type: str, summary: str, evidence: str = "", location: str = "") -> str:
    """Stable fingerprint for an issue based on its semantic content.

    Uses issue_type + normalised summary. Location is used only as a fallback when
    summary is absent. Evidence is used only when both summary and location are absent.
    """
    return legacy_issue_fingerprint(issue_type, summary, evidence, location)


def _identity_fingerprint(
    *,
    issue_id: str,
    issue_type: str,
    summary: str,
    evidence: str = "",
    location: str = "",
) -> str:
    """Return the most stable available identity key for an issue.

    Continuity issues now carry ``issue_id`` across repair/recheck rounds.  When
    present, prefer it over wording-derived fingerprints so a recheck can keep
    tracking the same unresolved problem even if the model rephrases summary or
    evidence.  Older issue types keep the historical content fingerprint.
    """
    cleaned_id = str(issue_id or "").strip()
    if cleaned_id:
        return f"id:{cleaned_id}"
    return legacy_issue_fingerprint(issue_type, summary, evidence, location)


@dataclass(frozen=True)
class IssuePrint:
    """Immutable snapshot of one issue for ledger comparison."""

    fingerprint: str
    issue_type: str
    severity: str
    summary: str
    issue_id: str = ""
    location: str = ""
    likely_false_positive: bool = False
    """True if this resolved issue is suspected to be a false positive."""
    location_confidence: float = 0.0
    """Detection confidence from the original evaluation (0.0-1.0)."""

    @classmethod
    def from_issue(cls, issue: Any) -> IssuePrint:
        audit_issue = normalize_audit_issue(issue)
        itype = audit_issue.issue_type
        sev = audit_issue.severity
        summ = audit_issue.summary
        ev = audit_issue.evidence or audit_issue.evidence_quote
        loc = audit_issue.location
        issue_id = audit_issue.issue_id
        loc_conf = audit_issue.location_confidence
        return cls(
            fingerprint=_identity_fingerprint(
                issue_id=issue_id,
                issue_type=itype,
                summary=summ,
                evidence=ev,
                location=loc,
            ),
            issue_type=itype,
            severity=sev,
            summary=summ,
            issue_id=issue_id,
            location=loc,
            location_confidence=loc_conf,
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> IssuePrint:
        audit_issue = normalize_audit_issue(d)
        itype = audit_issue.issue_type
        sev = audit_issue.severity
        summ = audit_issue.summary
        ev = audit_issue.evidence or audit_issue.evidence_quote
        loc = audit_issue.location
        issue_id = audit_issue.issue_id
        loc_conf = audit_issue.location_confidence
        return cls(
            fingerprint=_identity_fingerprint(
                issue_id=issue_id,
                issue_type=itype,
                summary=summ,
                evidence=ev,
                location=loc,
            ),
            issue_type=itype,
            severity=sev,
            summary=summ,
            issue_id=issue_id,
            location=loc,
            location_confidence=loc_conf,
        )


@dataclass
class LedgerEntry:
    """One tracked issue with its status across rounds."""

    issue: IssuePrint
    status: IssueStatus
    prev_severity: str | None = None  # only for downgraded/upgraded


@dataclass
class LedgerDiff:
    """Result of comparing two issue snapshots."""

    resolved: list[IssuePrint] = field(default_factory=list)
    unresolved: list[IssuePrint] = field(default_factory=list)
    new_issues: list[IssuePrint] = field(default_factory=list)
    downgraded: list[LedgerEntry] = field(default_factory=list)
    upgraded: list[LedgerEntry] = field(default_factory=list)
    likely_false_positive_count: int = 0
    """Number of resolved issues flagged as likely false positives."""

    @property
    def new_high_critical(self) -> list[IssuePrint]:
        """New issues at high or critical severity — the key regression signal."""
        return [i for i in self.new_issues if _sev_rank(i.severity) >= 3]

    @property
    def resolved_high_critical(self) -> list[IssuePrint]:
        """Resolved issues that were high or critical severity."""
        return [i for i in self.resolved if _sev_rank(i.severity) >= 3]

    @property
    def adjusted_resolved_high_critical(self) -> list[IssuePrint]:
        """Resolved high/critical issues excluding likely false positives."""
        return [i for i in self.resolved_high_critical if not i.likely_false_positive]

    @property
    def net_fix_count(self) -> int:
        """Resolved minus new — positive = net improvement."""
        return len(self.resolved) - len(self.new_issues)

    @property
    def net_high_critical_fix_count(self) -> int:
        """Resolved high/critical minus new high/critical — net improvement."""
        return len(self.resolved_high_critical) - len(self.new_high_critical)

    @property
    def high_critical_resolved_weight(self) -> int:
        """Severity-weighted sum of genuinely resolved high/critical issues."""
        return sum(_sev_rank(i.severity) for i in self.adjusted_resolved_high_critical)

    @property
    def high_critical_new_weight(self) -> int:
        """Severity-weighted sum of newly introduced high/critical issues."""
        return sum(_sev_rank(i.severity) for i in self.new_high_critical)

    @property
    def high_critical_net_weight(self) -> int:
        """Resolved minus new severity weight for high/critical issues."""
        return self.high_critical_resolved_weight - self.high_critical_new_weight

    @property
    def repair_effectiveness_score(self) -> float:
        """Weighted effectiveness of this repair round (0.0-1.0).

        Formula: weighted_resolved / weighted_total
        where weights = severity_rank (critical=4, high=3, medium=2, low=1).
        Returns 0.0 when no issues existed before repair.
        """
        all_issues = (
            self.unresolved + self.resolved + self.new_issues
            + [e.issue for e in self.downgraded + self.upgraded]
        )
        total_weight = sum(_sev_rank(i.severity) for i in all_issues)
        if total_weight == 0:
            return 0.0
        resolved_weight = sum(_sev_rank(i.severity) for i in self.resolved)
        new_weight = sum(_sev_rank(i.severity) for i in self.new_issues)
        net_weight = max(0, resolved_weight - new_weight)
        return round(net_weight / total_weight, 3)

    @property
    def has_regression(self) -> bool:
        """True if repair introduced any new high/critical issues without a strict net gain.

        Policy: roll back whenever the repair introduces a new high/critical issue
        AND the number of new issues is NOT strictly fewer than the number resolved
        at that severity level.  Likely false-positive resolved issues are excluded
        from the resolved count so that phantom "fixes" don't offset real regressions.

        This means:
        - fixed 3 critical, introduced 1 high → resolved_hc=3, new_hc=1 → net=2 → NO rollback
        - fixed 1 critical, introduced 1 high  → resolved_hc=1, new_hc=1 → net=0 → ROLLBACK
        - fixed 0,          introduced 1 high  → resolved_hc=0, new_hc=1 → net=-1 → ROLLBACK
        - fixed 1 FP only,  introduced 1 high  → adjusted_resolved=0, new_hc=1 → net=-1 → ROLLBACK
        """
        if not self.new_high_critical:
            return False
        adjusted_net = len(self.adjusted_resolved_high_critical) - len(self.new_high_critical)
        # Roll back unless genuinely resolved strictly outnumber new problems
        return adjusted_net <= 0

    def to_step_payload(self) -> dict[str, Any]:
        """Compact dict for on_step / logging."""
        return {
            "resolved": len(self.resolved),
            "unresolved": len(self.unresolved),
            "new_issues": len(self.new_issues),
            "new_high_critical": len(self.new_high_critical),
            "resolved_high_critical": len(self.resolved_high_critical),
            "adjusted_resolved_high_critical": len(self.adjusted_resolved_high_critical),
            "net_high_critical_fix_count": self.net_high_critical_fix_count,
            "high_critical_net_weight": self.high_critical_net_weight,
            "downgraded": len(self.downgraded),
            "upgraded": len(self.upgraded),
            "net_fix_count": self.net_fix_count,
            "has_regression": self.has_regression,
            "likely_false_positive_count": self.likely_false_positive_count,
            "repair_effectiveness_score": self.repair_effectiveness_score,
        }


def diff_issues(
    before: list[Any],
    after: list[Any],
    *,
    repaired_issue_types: set[str] | None = None,
    repair_was_no_op: bool = False,
) -> LedgerDiff:
    """Compare two issue lists and classify every issue.

    Args:
        before: issues from the pre-repair evaluation
        after: issues from the post-repair evaluation
        repaired_issue_types: set of issue types that were targeted by the repair
            step.  Used to detect false positives (resolved issues whose type was
            not repaired).  When *None*, this heuristic is disabled.
        repair_was_no_op: when *True*, any "resolved" issue is likely a false
            positive because the text was not actually modified.

    Both lists can contain objects with attributes (Pydantic models)
    or plain ``dict``s.
    """
    def _to_print(item: Any) -> IssuePrint:
        if isinstance(item, dict):
            return IssuePrint.from_dict(item)
        return IssuePrint.from_issue(item)

    before_map: dict[str, IssuePrint] = {}
    for item in before:
        p = _to_print(item)
        before_map[p.fingerprint] = p

    after_map: dict[str, IssuePrint] = {}
    for item in after:
        p = _to_print(item)
        after_map[p.fingerprint] = p

    diff = LedgerDiff()

    matched_before: set[str] = set()
    matched_after: set[str] = set()

    def _classify_pair(bp: IssuePrint, ap: IssuePrint) -> None:
        prev_rank = _sev_rank(bp.severity)
        curr_rank = _sev_rank(ap.severity)
        if curr_rank < prev_rank:
            diff.downgraded.append(
                LedgerEntry(issue=ap, status=IssueStatus.DOWNGRADED, prev_severity=bp.severity)
            )
        elif curr_rank > prev_rank:
            diff.upgraded.append(
                LedgerEntry(issue=ap, status=IssueStatus.UPGRADED, prev_severity=bp.severity)
            )
        else:
            diff.unresolved.append(ap)

    # Pass 1: exact fingerprint matches.
    for fp, bp in before_map.items():
        ap = after_map.get(fp)
        if ap is None:
            continue
        matched_before.add(fp)
        matched_after.add(fp)
        _classify_pair(bp, ap)

    # Pass 2: fallback fuzzy pairing by issue_type + normalized location.
    before_unmatched = [bp for fp, bp in before_map.items() if fp not in matched_before]
    after_unmatched = [ap for fp, ap in after_map.items() if fp not in matched_after]

    before_by_key: dict[tuple[str, str], list[IssuePrint]] = {}
    for bp in before_unmatched:
        key = (bp.issue_type.strip().lower(), _normalize_location_key(bp.location))
        before_by_key.setdefault(key, []).append(bp)

    after_by_key: dict[tuple[str, str], list[IssuePrint]] = {}
    for ap in after_unmatched:
        key = (ap.issue_type.strip().lower(), _normalize_location_key(ap.location))
        after_by_key.setdefault(key, []).append(ap)

    for key, b_items in before_by_key.items():
        a_items = after_by_key.get(key, [])
        if not a_items:
            continue
        if not key[0]:
            continue
        if not key[1]:
            # No location anchor: avoid aggressive fuzzy pairing.
            continue
        pair_count = min(len(b_items), len(a_items))
        for idx in range(pair_count):
            _classify_pair(b_items[idx], a_items[idx])
            matched_before.add(b_items[idx].fingerprint)
            matched_after.add(a_items[idx].fingerprint)

    # Pass 3: location-empty issues — pair by issue_type + summary prefix.
    # LLMs often rephrase the same structural issue between repair rounds, causing
    # fingerprint mismatches.  Using the first N chars of the normalised summary as
    # a grouping key catches these rephrasing collisions without being too aggressive.
    _SUMMARY_PREFIX_LEN = 8

    def _summary_prefix_key(ip: IssuePrint) -> str:
        return "".join(ip.summary.split())[:_SUMMARY_PREFIX_LEN].lower()

    before_unmatched3 = [bp for fp, bp in before_map.items() if fp not in matched_before and not bp.location]
    after_unmatched3 = [ap for fp, ap in after_map.items() if fp not in matched_after and not ap.location]

    before_by_sumprefix: dict[tuple[str, str], list[IssuePrint]] = {}
    for bp in before_unmatched3:
        prefix = _summary_prefix_key(bp)
        if prefix:
            before_by_sumprefix.setdefault((bp.issue_type.strip().lower(), prefix), []).append(bp)

    after_by_sumprefix: dict[tuple[str, str], list[IssuePrint]] = {}
    for ap in after_unmatched3:
        prefix = _summary_prefix_key(ap)
        if prefix:
            after_by_sumprefix.setdefault((ap.issue_type.strip().lower(), prefix), []).append(ap)

    for key, b_items in before_by_sumprefix.items():
        a_items = after_by_sumprefix.get(key, [])
        if not a_items:
            continue
        pair_count = min(len(b_items), len(a_items))
        for idx in range(pair_count):
            _classify_pair(b_items[idx], a_items[idx])
            matched_before.add(b_items[idx].fingerprint)
            matched_after.add(a_items[idx].fingerprint)

    # Remaining unmatched issues.
    for fp, bp in before_map.items():
        if fp not in matched_before:
            diff.resolved.append(bp)
    for fp, ap in after_map.items():
        if fp not in matched_after:
            diff.new_issues.append(ap)

    # ── False positive detection on resolved issues ──
    # Mark likely_false_positive when a resolved issue is suspected to have
    # disappeared due to LLM evaluation variance rather than genuine repair.
    _fp_count = 0
    _CONFIDENCE_FLOOR = 0.5
    resolved_flagged: list[IssuePrint] = []
    for ip in diff.resolved:
        is_fp = False
        # Rule 1: repair was a no-op → text unchanged → issue cannot be genuinely fixed
        if repair_was_no_op:
            is_fp = True
        # Rule 2: original detection was unreliable (low location_confidence)
        elif ip.location_confidence > 0.0 and ip.location_confidence < _CONFIDENCE_FLOOR:
            is_fp = True
        # Rule 3: issue type was not targeted by repair → likely evaluation variance
        elif repaired_issue_types is not None and ip.issue_type.strip().lower() not in repaired_issue_types:
            is_fp = True

        if is_fp:
            _fp_count += 1
            resolved_flagged.append(dataclasses.replace(ip, likely_false_positive=True))
        else:
            resolved_flagged.append(ip)
    diff.resolved = resolved_flagged
    diff.likely_false_positive_count = _fp_count

    return diff
