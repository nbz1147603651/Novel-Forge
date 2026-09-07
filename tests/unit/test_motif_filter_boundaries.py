"""Unit tests for LLM motif authority and deterministic safety boundaries."""

from __future__ import annotations

from novel_forge.pipeline.steps.continuity_eval.validators import (
    _classify_issue_source,
    _filter_forbidden_elements,
)


def test_issue_source_prefers_llm_motif_provenance() -> None:
    assert (
        _classify_issue_source(
            "破碎的月光",
            motif_context={"active_motifs": [{"name": "月光", "category": "意象"}]},
            bible_anchor_terms=["月光"],
        )
        == "llm"
    )


def test_issue_source_uses_bible_provenance_only_without_llm_match() -> None:
    assert (
        _classify_issue_source(
            "镇北将军",
            motif_context={"active_motifs": []},
            bible_anchor_terms=["镇北将军"],
        )
        == "rule"
    )


def test_empty_semantic_context_is_permissive() -> None:
    assert _filter_forbidden_elements(["普通词汇"], motif_context=None) == ["普通词汇"]


def test_generic_sensory_channel_is_not_a_hard_forbidden_element() -> None:
    assert _filter_forbidden_elements(["声音"], motif_context=None) == []


def test_duplicate_forbidden_elements_are_stably_deduplicated() -> None:
    assert _filter_forbidden_elements(["普通词汇", "普通词汇"]) == ["普通词汇"]
