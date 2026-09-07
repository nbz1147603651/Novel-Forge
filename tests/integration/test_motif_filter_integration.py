"""Integration coverage for LLM-authored motif categories in validation."""

from __future__ import annotations

from novel_forge.pipeline.steps.continuity_eval.validators import (
    _filter_forbidden_elements,
)


def test_llm_rhetorical_category_remains_actionable_without_anchor_evidence() -> None:
    result = _filter_forbidden_elements(
        ["镇北将军"],
        motif_context={
            "active_motifs": [{"name": "镇北将军", "category": "意象"}],
        },
    )
    assert result == ["镇北将军"]


def test_llm_thematic_category_is_not_treated_as_prose_repetition() -> None:
    result = _filter_forbidden_elements(
        ["镇北将军"],
        motif_context={
            "active_motifs": [{"name": "镇北将军", "category": "符号"}],
        },
    )
    assert result == []


def test_exact_bible_anchor_protects_a_rhetorical_motif() -> None:
    result = _filter_forbidden_elements(
        ["破碎的月光"],
        motif_context={
            "active_motifs": [{"name": "破碎的月光", "category": "意象"}],
        },
        bible_anchor_terms=["破碎的月光"],
    )
    assert result == []


def test_first_matching_llm_motif_owns_category() -> None:
    result = _filter_forbidden_elements(
        ["冷雨"],
        motif_context={
            "active_motifs": [
                {"name": "冷雨", "category": "意象"},
                {"name": "雨", "category": "符号"},
            ],
        },
    )
    assert result == ["冷雨"]
