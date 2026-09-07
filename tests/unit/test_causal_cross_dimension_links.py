"""Tests for causal repair cross-dimension issue linking."""

from __future__ import annotations

from types import SimpleNamespace

from novel_forge.pipeline.long.stages.causal_repair import _detect_cross_dimension_links


def test_detect_cross_dimension_links_uses_overlapping_paragraph_ranges() -> None:
    links = _detect_cross_dimension_links(
        causal_issues_before=[
            SimpleNamespace(
                issue_id="causal-1",
                paragraph_start=3,
                paragraph_end=5,
            )
        ],
        continuity_issues_after=[
            SimpleNamespace(
                issue_id="continuity-1",
                paragraph_start=5,
                paragraph_end=6,
            )
        ],
        source_dimension="causal",
        target_dimension="continuity",
    )

    assert len(links) == 1
    assert links[0].source_issue_id == "causal-1"
    assert links[0].target_issue_id == "continuity-1"


def test_detect_cross_dimension_links_tolerates_dirty_paragraph_fields() -> None:
    links = _detect_cross_dimension_links(
        causal_issues_before=[
            SimpleNamespace(
                issue_id="causal-1",
                paragraph_start="unknown",
                paragraph_end="unknown",
            )
        ],
        continuity_issues_after=[
            SimpleNamespace(
                issue_id="continuity-1",
                paragraph_start="also-unknown",
                paragraph_end="also-unknown",
            )
        ],
        source_dimension="causal",
        target_dimension="continuity",
    )

    assert links == []
