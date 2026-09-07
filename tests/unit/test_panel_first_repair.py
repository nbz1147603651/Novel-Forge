"""Tests for panel-first repair expansion and evidence-anchored patch candidates."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.workspace.book_ops.execution_book_repair import (
    _select_panel_first_expansion_indices,
)
from novel_forge.workspace.helpers.execution_helpers import (
    _report_severity_rank,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_report_issue(
    *,
    severity: str = "critical",
    location: str = "",
    summary: str = "",
    evidence: str = "",
    issue_type: str = "continuity_gap",
    fix_actions: list[str] | None = None,
    rewrite_scope: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        severity=severity,
        location=location,
        summary=summary,
        evidence=evidence,
        issue_type=issue_type,
        fix_actions=fix_actions or [],
        fix_suggestion="修复建议",
        rewrite_scope=rewrite_scope,
    )


def _make_book_issue(
    *,
    severity: str = "critical",
    category: str = "timeline",
    primary_chapter: int = 1,
    paragraph_index: int = 0,
    location: str = "",
    description: str = "",
    evidence: str = "",
    fix_mode: str = "repair_causal",
    confidence: float = 0.8,
    linked_issue_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "category": category,
        "primary_chapter": primary_chapter,
        "chapters_involved": [primary_chapter],
        "paragraph_index": paragraph_index,
        "location": location,
        "description": description,
        "evidence": evidence,
        "fix_mode": fix_mode,
        "confidence": confidence,
        "linked_issue_refs": linked_issue_refs or [],
    }


# ── Tests: Panel severity threshold ──────────────────────────────────────────


class TestPanelSeverityRanking:
    """Verify the severity rank used for panel-first expansion."""

    def test_high_and_above_meet_threshold(self) -> None:
        threshold = _report_severity_rank("high")
        assert _report_severity_rank("high") >= threshold
        assert _report_severity_rank("critical") >= threshold
        assert _report_severity_rank("major") >= threshold

    def test_medium_and_below_do_not_meet_threshold(self) -> None:
        threshold = _report_severity_rank("high")
        assert _report_severity_rank("medium") < threshold
        assert _report_severity_rank("warning") < threshold
        assert _report_severity_rank("info") < threshold


# ── Tests: Panel expansion selects high+ issues ─────────────────────────────


class TestPanelFirstExpansion:
    """Test that panel-first expansion includes high+/critical panel issues."""

    def test_panel_expansion_adds_high_severity_indices(self) -> None:
        """High severity issues not matched by book audit should be added."""
        report_issues = [
            _make_report_issue(severity="critical", summary="critical issue"),
            _make_report_issue(severity="medium", summary="medium issue"),
            _make_report_issue(severity="high", summary="high issue"),
            _make_report_issue(severity="info", summary="info issue"),
        ]

        # Simulate: book audit matched only index 1 (medium)
        matched_indices: set[int] = {1}

        added_indices = _select_panel_first_expansion_indices(report_issues, matched_indices)
        matched_indices.update(added_indices)

        # Should add index 0 (critical) and index 2 (high), not index 3 (info)
        assert len(added_indices) == 2
        assert matched_indices == {0, 1, 2}

    def test_panel_expansion_does_not_duplicate_existing(self) -> None:
        """Already-matched high severity issues are not double-counted."""
        report_issues = [
            _make_report_issue(severity="critical", summary="already matched"),
            _make_report_issue(severity="high", summary="also matched"),
        ]

        matched_indices: set[int] = {0, 1}  # Both already matched by book audit

        added_indices = _select_panel_first_expansion_indices(report_issues, matched_indices)

        assert added_indices == []  # No new additions
        assert matched_indices == {0, 1}

    def test_panel_expansion_with_empty_report(self) -> None:
        """Empty report issues should produce zero panel expansion."""
        report_issues: list[Any] = []
        matched_indices: set[int] = set()

        added_indices = _select_panel_first_expansion_indices(report_issues, matched_indices)

        assert added_indices == []


# ── Tests: match_mode reporting ──────────────────────────────────────────────


class TestMatchModeReporting:
    """Verify match_mode correctly reflects panel/ref/fuzzy contributions."""

    @staticmethod
    def _build_match_mode(
        *, panel_added: int, ref_matched: int, fuzzy_added: int,
    ) -> str:
        parts: list[str] = []
        if panel_added > 0:
            parts.append("panel")
        if ref_matched > 0:
            parts.append("ref")
        if fuzzy_added > 0:
            parts.append("fuzzy")
        return "+".join(parts) if parts else "none"

    def test_panel_only(self) -> None:
        assert self._build_match_mode(panel_added=3, ref_matched=0, fuzzy_added=0) == "panel"

    def test_panel_plus_ref(self) -> None:
        assert self._build_match_mode(panel_added=2, ref_matched=1, fuzzy_added=0) == "panel+ref"

    def test_all_modes(self) -> None:
        assert self._build_match_mode(panel_added=1, ref_matched=2, fuzzy_added=1) == "panel+ref+fuzzy"

    def test_ref_plus_fuzzy(self) -> None:
        assert self._build_match_mode(panel_added=0, ref_matched=3, fuzzy_added=2) == "ref+fuzzy"

    def test_none(self) -> None:
        assert self._build_match_mode(panel_added=0, ref_matched=0, fuzzy_added=0) == "none"


# ── Tests: Evidence-anchored patch candidate expansion ───────────────────────


class TestEvidenceAnchoredPatchCandidate:
    """Test the expanded _is_patch_candidate logic in ContinuityRepairStep."""

    @pytest.fixture()
    def _step_cls(self):
        from novel_forge.pipeline.steps.continuity_repair_step import ContinuityRepairStep
        return ContinuityRepairStep

    def _make_continuity_issue(
        self,
        *,
        issue_type: str = "continuity_gap",
        rewrite_scope: str = "",
        evidence: str = "",
        location: str = "",
        severity: str = "high",
    ) -> SimpleNamespace:
        return SimpleNamespace(
            issue_type=issue_type,
            rewrite_scope=rewrite_scope,
            evidence=evidence,
            location=location,
            severity=severity,
            summary="test",
            fix_actions=[],
        )

    def test_patchable_type_still_works(self, _step_cls) -> None:
        """Known patchable types should remain patchable."""
        issue = self._make_continuity_issue(
            issue_type="opening_gap", rewrite_scope="chapter",
        )
        assert _step_cls._is_patch_candidate(issue) is True

    def test_non_patchable_type_with_evidence_and_scope(self, _step_cls) -> None:
        """Non-patchable type WITH evidence + narrow scope should now be patchable."""
        issue = self._make_continuity_issue(
            issue_type="information_consistency",
            rewrite_scope="第5-8段",
            evidence="苏令婉此前说过她从未去过京城",
        )
        assert _step_cls._is_patch_candidate(issue) is True

    def test_non_patchable_type_with_evidence_but_chapter_scope(self, _step_cls) -> None:
        """Non-patchable type with evidence but chapter-level scope should NOT be patchable."""
        issue = self._make_continuity_issue(
            issue_type="information_consistency",
            rewrite_scope="chapter",
            evidence="苏令婉此前说过她从未去过京城",
        )
        assert _step_cls._is_patch_candidate(issue) is False

    def test_non_patchable_type_with_evidence_but_empty_scope(self, _step_cls) -> None:
        """Non-patchable type with evidence but empty scope should NOT be patchable."""
        issue = self._make_continuity_issue(
            issue_type="information_consistency",
            rewrite_scope="",
            evidence="很长的evidence文本",
        )
        assert _step_cls._is_patch_candidate(issue) is False

    def test_non_patchable_type_with_short_evidence(self, _step_cls) -> None:
        """Non-patchable type with too-short evidence should NOT be patchable."""
        issue = self._make_continuity_issue(
            issue_type="information_consistency",
            rewrite_scope="第5段",
            evidence="短",
        )
        assert _step_cls._is_patch_candidate(issue) is False

    def test_non_patchable_type_no_evidence(self, _step_cls) -> None:
        """Non-patchable type without evidence should NOT be patchable."""
        issue = self._make_continuity_issue(
            issue_type="information_consistency",
            rewrite_scope="第5段",
            evidence="",
        )
        assert _step_cls._is_patch_candidate(issue) is False


# ── Tests: Infer patch location for evidence-anchored issues ─────────────────


class TestInferPatchLocation:
    """Test _infer_patch_location handles evidence-anchored issues."""

    @pytest.fixture()
    def _step_cls(self):
        from novel_forge.pipeline.steps.continuity_repair_step import ContinuityRepairStep
        return ContinuityRepairStep

    def test_location_field_used_for_non_boundary_types(self, _step_cls) -> None:
        """When issue has a specific location, use it even for non-boundary types."""
        issue = SimpleNamespace(
            issue_type="information_consistency",
            rewrite_scope="",  # empty scope
            location="第12段",
            evidence="evidence text",
        )
        result = _step_cls._infer_patch_location(issue)
        assert result == "第12段"

    def test_scope_preferred_over_location(self, _step_cls) -> None:
        """Non-empty, non-chapter scope should take priority over location."""
        issue = SimpleNamespace(
            issue_type="continuity_gap",
            rewrite_scope="第5-8段",
            location="第6段",
            evidence="",
        )
        result = _step_cls._infer_patch_location(issue)
        assert result == "第5-8段"

    def test_opening_type_fallback(self, _step_cls) -> None:
        """Opening types should fall back to '开头1-3段'."""
        issue = SimpleNamespace(
            issue_type="opening_gap",
            rewrite_scope="",
            location="",
            evidence="",
        )
        result = _step_cls._infer_patch_location(issue)
        assert result == "开头1-3段"

    def test_closing_type_fallback(self, _step_cls) -> None:
        """Closing types should fall back to '结尾1-3段'."""
        issue = SimpleNamespace(
            issue_type="closing_gap",
            rewrite_scope="",
            location="",
            evidence="",
        )
        result = _step_cls._infer_patch_location(issue)
        assert result == "结尾1-3段"
