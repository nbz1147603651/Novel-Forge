"""Tests for continuity eval fallback mechanisms (cold-start resilience)."""

from __future__ import annotations

from novel_forge.core.domain.bible_derived_provider import _load_defaults_cached
from novel_forge.pipeline.steps.continuity_eval_step import _filter_forbidden_elements

# Load defaults at module level
_rhetorical_default, _kinship_default, _emotion_default = _load_defaults_cached()


class TestMotifContextFallback:
    def test_empty_motif_context_uses_fallback_sets(self):
        assert _filter_forbidden_elements(
            ["月光"], motif_context={}, rhetorical_hints=_rhetorical_default
        ) == ["月光"]
        assert _filter_forbidden_elements(
            ["父亲"], motif_context={}, kinship_terms=_kinship_default
        ) == []

    def test_invalid_motif_context_active_motifs_not_list(self):
        result = _filter_forbidden_elements(
            ["父亲"],
            motif_context={"active_motifs": "not-a-list"},
            kinship_terms=_kinship_default,
        )
        assert result == []

    def test_motif_context_exception_no_raise(self):
        result = _filter_forbidden_elements(
            ["月光"],
            motif_context="totally-invalid",  # type: ignore[arg-type]
            rhetorical_hints=_rhetorical_default,
        )
        assert result == ["月光"]


class TestBibleAnchorTermsFallback:
    def test_empty_bible_anchor_terms_uses_fallback_kinship(self):
        assert _filter_forbidden_elements(
            ["父亲"], bible_anchor_terms=[], kinship_terms=_kinship_default
        ) == []

    def test_non_empty_bible_anchor_terms_used(self):
        custom_term = "镇北将军"
        assert custom_term not in _kinship_default
        assert _filter_forbidden_elements([custom_term]) == [custom_term]
        # With bible_anchor_terms, it becomes a known anchor → filtered out
        assert _filter_forbidden_elements([custom_term], bible_anchor_terms=[custom_term]) == []


class TestCombinedFallbackPriority:
    def test_motif_context_takes_precedence_over_bible_terms(self):
        """Priority 1: valid motif_context → motif category filtering."""
        motif_context = {
            "active_motifs": [
                {"name": "月光", "category": "意象"},
            ]
        }
        # "月光" matches a rhetorical-category motif → kept as forbidden
        # even if bible_anchor_terms might consider it otherwise
        assert _filter_forbidden_elements(["月光"], motif_context=motif_context) == ["月光"]

    def test_llm_motif_category_is_not_overwritten_by_local_anchor_rules(self):
        motif_context = {
            "active_motifs": [
                {"name": "镇北将军", "category": "主题"},
            ]
        }

        assert _filter_forbidden_elements(
            ["镇北将军"],
            motif_context=motif_context,
            bible_anchor_terms=["镇北将军"],
        ) == []

    def test_thematic_motif_filtered_even_when_in_bible_terms(self):
        """Thematic motifs are always filtered, regardless of bible_anchor_terms."""
        motif_context = {
            "active_motifs": [
                {"name": "宿命", "category": "主题"},
            ]
        }
        # "宿命" is a thematic motif → filtered out
        assert _filter_forbidden_elements(["宿命"], motif_context=motif_context) == []

    def test_bible_terms_used_when_motif_context_empty(self):
        """Priority 2: when motif_context is empty, bible_anchor_terms should apply."""
        custom_term = "镇北将军"
        assert _filter_forbidden_elements(
            [custom_term],
            motif_context={},
            bible_anchor_terms=[custom_term],
        ) == []

    def test_fallback_sets_when_both_dynamic_sources_empty(self):
        assert _filter_forbidden_elements(
            ["父亲"],
            motif_context={},
            bible_anchor_terms=[],
            kinship_terms=_kinship_default,
        ) == []
        assert _filter_forbidden_elements(
            ["月光"],
            motif_context={},
            bible_anchor_terms=[],
            rhetorical_hints=_rhetorical_default,
        ) == ["月光"]
