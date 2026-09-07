"""Tests for TextMutation, MUTATION_IMPACT, and helpers."""

from __future__ import annotations

import pytest

from novel_forge.pipeline.long.stages.text_mutation import (
    ALL_DIMENSIONS,
    MUTATION_IMPACT,
    ReviewDimension,
    TextMutation,
    TextMutationKind,
    merge_mutations,
    mutation_report_kinds,
)


class TestReviewDimension:
    def test_all_members_present(self):
        expected = {
            "continuity", "causal", "alignment",
            "reading_power", "chapter_repair", "guard_compliance",
        }
        actual = {d.value for d in ReviewDimension}
        assert actual == expected

    def test_all_dimensions_is_frozenset(self):
        assert isinstance(ALL_DIMENSIONS, frozenset)
        assert len(ALL_DIMENSIONS) == len(ReviewDimension)


class TestTextMutationKind:
    def test_unknown_exists(self):
        assert TextMutationKind.UNKNOWN.value == "unknown"

    def test_all_kinds_in_mapping(self):
        for kind in TextMutationKind:
            assert kind in MUTATION_IMPACT, f"{kind} missing from MUTATION_IMPACT"


class TestMUTATION_IMPACT:
    def test_unknown_maps_to_all(self):
        assert MUTATION_IMPACT[TextMutationKind.UNKNOWN] == ALL_DIMENSIONS

    def test_pronoun_mechanical_is_empty(self):
        assert MUTATION_IMPACT[TextMutationKind.PRONOUN_MECHANICAL_FIX] == frozenset()

    def test_humanize_is_all(self):
        assert MUTATION_IMPACT[TextMutationKind.HUMANIZE_LAYER] == ALL_DIMENSIONS

    def test_continuity_patch_only_continuity(self):
        dims = MUTATION_IMPACT[TextMutationKind.CONTINUITY_REPAIR_PATCH]
        assert dims == frozenset({ReviewDimension.CONTINUITY})

    def test_continuity_fulltext_is_broader(self):
        patch_dims = MUTATION_IMPACT[TextMutationKind.CONTINUITY_REPAIR_PATCH]
        ft_dims = MUTATION_IMPACT[TextMutationKind.CONTINUITY_REPAIR_FULLTEXT]
        assert patch_dims < ft_dims  # strict subset

    def test_reading_power_includes_alignment(self):
        dims = MUTATION_IMPACT[TextMutationKind.READING_POWER_REPAIR]
        assert ReviewDimension.READING_POWER in dims
        assert ReviewDimension.ALIGNMENT in dims


class TestTextMutation:
    def test_frozen(self):
        m = TextMutation(kind=TextMutationKind.WAVE)
        with pytest.raises(AttributeError):
            m.kind = TextMutationKind.UNKNOWN  # type: ignore[misc]

    def test_affected_dimensions_derived(self):
        m = TextMutation(kind=TextMutationKind.CAUSAL_REPAIR)
        assert m.affected_dimensions == frozenset(
            {ReviewDimension.CAUSAL, ReviewDimension.CONTINUITY}
        )

    def test_caller_cannot_override_dimensions(self):
        """TextMutation does not accept affected_dimensions as constructor arg."""
        # This must raise TypeError — no 'affected_dimensions' parameter.
        with pytest.raises(TypeError):
            TextMutation(  # type: ignore[call-arg]
                kind=TextMutationKind.PRONOUN_MECHANICAL_FIX,
                affected_dimensions=frozenset({ReviewDimension.CAUSAL}),
            )

    def test_ticket_and_issue_ids(self):
        m = TextMutation(
            kind=TextMutationKind.CAUSAL_REPAIR,
            ticket_ids=("t1", "t2"),
            issue_ids=("i1",),
        )
        assert m.ticket_ids == ("t1", "t2")
        assert m.issue_ids == ("i1",)


class TestMergeMutations:
    def test_empty(self):
        assert merge_mutations([]) == frozenset()

    def test_single(self):
        m = TextMutation(kind=TextMutationKind.CONTINUITY_REPAIR_PATCH)
        assert merge_mutations([m]) == frozenset({ReviewDimension.CONTINUITY})

    def test_union(self):
        a = TextMutation(kind=TextMutationKind.CONTINUITY_REPAIR_PATCH)
        b = TextMutation(kind=TextMutationKind.READING_POWER_REPAIR)
        merged = merge_mutations([a, b])
        assert ReviewDimension.CONTINUITY in merged
        assert ReviewDimension.READING_POWER in merged
        assert ReviewDimension.ALIGNMENT in merged

    def test_unknown_dominates(self):
        a = TextMutation(kind=TextMutationKind.PRONOUN_MECHANICAL_FIX)
        b = TextMutation(kind=TextMutationKind.UNKNOWN)
        merged = merge_mutations([a, b])
        assert merged == ALL_DIMENSIONS


class TestMutationReportKinds:
    def test_off_returns_empty_for_nonsemantic(self):
        m = TextMutation(kind=TextMutationKind.PRONOUN_MECHANICAL_FIX)
        kinds = mutation_report_kinds([m])
        assert kinds == ()

    def test_allow_narrowing_false_returns_all(self):
        m = TextMutation(kind=TextMutationKind.PRONOUN_MECHANICAL_FIX)
        kinds = mutation_report_kinds([m], allow_narrowing=False)
        assert set(kinds) == {d.value for d in ALL_DIMENSIONS}

    def test_enforce_returns_affected(self):
        m = TextMutation(kind=TextMutationKind.CAUSAL_REPAIR)
        kinds = mutation_report_kinds([m])
        assert set(kinds) == {"causal", "continuity"}

    def test_multiple_mutations_union(self):
        a = TextMutation(kind=TextMutationKind.CONTINUITY_REPAIR_PATCH)
        b = TextMutation(kind=TextMutationKind.READING_POWER_REPAIR)
        kinds = mutation_report_kinds([a, b])
        assert "continuity" in kinds
        assert "reading_power" in kinds
        assert "alignment" in kinds
