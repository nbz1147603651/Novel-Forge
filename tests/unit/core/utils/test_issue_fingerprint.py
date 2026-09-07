"""Tests for stable_issue_fingerprint."""

from __future__ import annotations

from types import SimpleNamespace

from novel_forge.core.utils.issue_fingerprint import stable_issue_fingerprint


class TestStableIssueFingerprint:
    def test_deterministic(self):
        issue = SimpleNamespace(
            issue_type="continuity_gap",
            evidence_quote="角色A在第一章受伤",
            paragraph_start=3,
        )
        fp1 = stable_issue_fingerprint(issue)
        fp2 = stable_issue_fingerprint(issue)
        assert fp1 == fp2

    def test_different_issues_different_fingerprints(self):
        a = SimpleNamespace(
            issue_type="continuity_gap",
            evidence_quote="角色A受伤",
            paragraph_start=3,
        )
        b = SimpleNamespace(
            issue_type="causal_break",
            evidence_quote="角色B消失",
            paragraph_start=7,
        )
        assert stable_issue_fingerprint(a) != stable_issue_fingerprint(b)

    def test_same_issue_across_recheck(self):
        """Recheck may assign new IDs but same content → same fingerprint."""
        original = SimpleNamespace(
            issue_type="continuity_gap",
            evidence_quote="角色A在第一章受伤",
            paragraph_start=3,
        )
        rechecked = SimpleNamespace(
            issue_type="continuity_gap",
            evidence_quote="角色A在第一章受伤",
            paragraph_start=3,
        )
        assert stable_issue_fingerprint(original) == stable_issue_fingerprint(rechecked)

    def test_dict_input(self):
        issue = {
            "issue_type": "pov_jump",
            "evidence_quote": "视角切换",
            "paragraph_start": 5,
        }
        fp = stable_issue_fingerprint(issue)
        assert isinstance(fp, str)
        assert len(fp) == 16

    def test_missing_fields_fallback(self):
        issue = SimpleNamespace(issue_type="unknown_type")
        fp = stable_issue_fingerprint(issue)
        assert isinstance(fp, str)
        assert len(fp) == 16

    def test_evidence_quote_preferred_over_evidence(self):
        a = SimpleNamespace(
            issue_type="t",
            evidence_quote="quoted",
            evidence="raw evidence",
            paragraph_start=1,
        )
        b = SimpleNamespace(
            issue_type="t",
            evidence_quote="quoted",
            paragraph_start=1,
        )
        assert stable_issue_fingerprint(a) == stable_issue_fingerprint(b)

    def test_whitespace_normalization(self):
        a = SimpleNamespace(
            issue_type="t",
            evidence_quote="foo   bar",
            paragraph_start=1,
        )
        b = SimpleNamespace(
            issue_type="t",
            evidence_quote="foobar",
            paragraph_start=1,
        )
        assert stable_issue_fingerprint(a) == stable_issue_fingerprint(b)

    def test_length_16_hex(self):
        issue = {"issue_type": "x", "evidence_quote": "y", "paragraph_start": 0}
        fp = stable_issue_fingerprint(issue)
        assert len(fp) == 16
        # Must be valid hex.
        int(fp, 16)
