"""Tests for StoryKernel field contracts — step-level read/write/immutable declarations.

TDD: These tests define the expected shape and coverage of field contracts.
"""

from __future__ import annotations

import pytest

from novel_forge.story_kernel.contracts import (
    ALIGNMENT_CONTRACT,
    ALL_CONTRACTS,
    BOOK_CONSISTENCY_CONTRACT,
    BRIDGE_CONTRACT,
    CAUSAL_REPAIR_CONTRACT,
    CAUSAL_VALIDATE_CONTRACT,
    CHARACTER_INTRO_CONTRACT,
    CHECK_CHAPTER_CONTRACT,
    CONTINUITY_EVAL_CONTRACT,
    CONTINUITY_REPAIR_CONTRACT,
    DRAFT_CONTRACT,
    EDIT_CONTRACT,
    EVALUATE_CONTRACT,
    EXTRACT_CONTRACT,
    FORBIDDEN_SOURCES_CONTRACT,
    MACRO_GUARD_CONTRACT,
    PATCH_CONTRACT,
    PLAN_CONTRACT,
    POLISH_CONTRACT,
    PROFILE_STYLE_CONTRACT,
    PRONOUN_CHECK_CONTRACT,
    READING_POWER_EVAL_CONTRACT,
    READING_POWER_REPAIR_CONTRACT,
    STATE_ADJUDICATION_CONTRACT,
    VALID_FIELD_NAMES,
    VOLUME_CONTRACT,
    FieldContract,
)

# ---------------------------------------------------------------------------
# Known StoryKernel field names
# ---------------------------------------------------------------------------

EXPECTED_FIELDS = {
    "project_id",
    "current_chapter",
    "active_volume",
    "title",
    "premise",
    "world_rules",
    "entities",
    "relationships",
    "timeline",
    "object_ledger",
    "knowledge_ledger",
    "access_ledger",
    "promise_ledger",
    "motif_protocols",
    "business_dependencies",
    "chapter_summaries",
    "chapter_exit_states",
    "plot_threads",
    "banned_phrases",
    "notes",
}


# ---------------------------------------------------------------------------
# FieldContract dataclass tests
# ---------------------------------------------------------------------------


class TestFieldContract:
    def test_frozen(self) -> None:
        """FieldContract is frozen — direct assignment raises."""
        c = FieldContract(
            step_name="test",
            reads=frozenset({"entities"}),
            writes=frozenset(),
            immutable=frozenset({"world_rules"}),
        )
        with pytest.raises(AttributeError):
            c.step_name = "other"  # type: ignore[misc]

    def test_reads_writes_immutable_are_frozensets(self) -> None:
        c = FieldContract(
            step_name="test",
            reads=frozenset({"entities", "relationships"}),
            writes=frozenset({"timeline"}),
            immutable=frozenset({"world_rules"}),
        )
        assert isinstance(c.reads, frozenset)
        assert isinstance(c.writes, frozenset)
        assert isinstance(c.immutable, frozenset)

    def test_no_overlap_writes_immutable(self) -> None:
        """A field cannot be both writable and immutable."""
        for name, contract in ALL_CONTRACTS.items():
            overlap = contract.writes & contract.immutable
            assert not overlap, (
                f"Contract '{name}' has fields in both writes and immutable: {overlap}"
            )

    def test_reads_subset_of_valid_fields(self) -> None:
        """All read fields must be valid StoryKernel field names."""
        for name, contract in ALL_CONTRACTS.items():
            unknown = contract.reads - VALID_FIELD_NAMES
            assert not unknown, (
                f"Contract '{name}' reads unknown fields: {unknown}"
            )

    def test_writes_subset_of_valid_fields(self) -> None:
        """All written fields must be valid StoryKernel field names."""
        for name, contract in ALL_CONTRACTS.items():
            unknown = contract.writes - VALID_FIELD_NAMES
            assert not unknown, (
                f"Contract '{name}' writes unknown fields: {unknown}"
            )

    def test_immutable_subset_of_valid_fields(self) -> None:
        """All immutable fields must be valid StoryKernel field names."""
        for name, contract in ALL_CONTRACTS.items():
            unknown = contract.immutable - VALID_FIELD_NAMES
            assert not unknown, (
                f"Contract '{name}' has unknown immutable fields: {unknown}"
            )


# ---------------------------------------------------------------------------
# Contract count and naming
# ---------------------------------------------------------------------------


class TestContractCoverage:
    def test_24_contracts_defined(self) -> None:
        assert len(ALL_CONTRACTS) == 24

    def test_all_contract_names_present(self) -> None:
        expected_names = {
            "BRIDGE_CONTRACT",
            "PLAN_CONTRACT",
            "DRAFT_CONTRACT",
            "EDIT_CONTRACT",
            "CONTINUITY_EVAL_CONTRACT",
            "CONTINUITY_REPAIR_CONTRACT",
            "CAUSAL_VALIDATE_CONTRACT",
            "CAUSAL_REPAIR_CONTRACT",
            "EXTRACT_CONTRACT",
            "ALIGNMENT_CONTRACT",
            "CHECK_CHAPTER_CONTRACT",
            "READING_POWER_EVAL_CONTRACT",
            "READING_POWER_REPAIR_CONTRACT",
            "POLISH_CONTRACT",
            "EVALUATE_CONTRACT",
            "VOLUME_CONTRACT",
            "BOOK_CONSISTENCY_CONTRACT",
            "MACRO_GUARD_CONTRACT",
            "STATE_ADJUDICATION_CONTRACT",
            "PATCH_CONTRACT",
            "PRONOUN_CHECK_CONTRACT",
            "FORBIDDEN_SOURCES_CONTRACT",
            "PROFILE_STYLE_CONTRACT",
            "CHARACTER_INTRO_CONTRACT",
        }
        actual = set(ALL_CONTRACTS.keys())
        assert actual == expected_names

    def test_valid_field_names_match_story_kernel(self) -> None:
        """VALID_FIELD_NAMES must match the StoryKernel schema fields."""
        assert VALID_FIELD_NAMES == EXPECTED_FIELDS


# ---------------------------------------------------------------------------
# Individual contract semantics
# ---------------------------------------------------------------------------


class TestBridgeContract:
    def test_reads_context(self) -> None:
        assert "entities" in BRIDGE_CONTRACT.reads
        assert "relationships" in BRIDGE_CONTRACT.reads
        assert "timeline" in BRIDGE_CONTRACT.reads
        assert "chapter_summaries" in BRIDGE_CONTRACT.reads

    def test_writes_bridge_summary(self) -> None:
        assert "chapter_summaries" in BRIDGE_CONTRACT.writes

    def test_immutable_world_rules(self) -> None:
        assert "world_rules" in BRIDGE_CONTRACT.immutable


class TestPlanContract:
    def test_reads_broad_context(self) -> None:
        assert "entities" in PLAN_CONTRACT.reads
        assert "world_rules" in PLAN_CONTRACT.reads
        assert "promise_ledger" in PLAN_CONTRACT.reads

    def test_read_only_on_kernel(self) -> None:
        """Plan step produces a plan document; it does not modify kernel fields."""
        assert len(PLAN_CONTRACT.writes) == 0


class TestDraftContract:
    def test_reads_narrative_context(self) -> None:
        assert "entities" in DRAFT_CONTRACT.reads
        assert "world_rules" in DRAFT_CONTRACT.reads

    def test_read_only_on_kernel(self) -> None:
        """Draft step produces chapter text; it does not modify kernel fields."""
        assert len(DRAFT_CONTRACT.writes) == 0


class TestEditContract:
    def test_reads_for_consistency(self) -> None:
        assert "entities" in EDIT_CONTRACT.reads
        assert "knowledge_ledger" in EDIT_CONTRACT.reads
        assert "access_ledger" in EDIT_CONTRACT.reads
        assert "banned_phrases" in EDIT_CONTRACT.reads

    def test_read_only_on_kernel(self) -> None:
        assert len(EDIT_CONTRACT.writes) == 0


class TestContinuityEvalContract:
    def test_reads_state_for_comparison(self) -> None:
        assert "entities" in CONTINUITY_EVAL_CONTRACT.reads
        assert "knowledge_ledger" in CONTINUITY_EVAL_CONTRACT.reads

    def test_read_only(self) -> None:
        assert len(CONTINUITY_EVAL_CONTRACT.writes) == 0


class TestContinuityRepairContract:
    def test_reads_state(self) -> None:
        assert "entities" in CONTINUITY_REPAIR_CONTRACT.reads
        assert "world_rules" in CONTINUITY_REPAIR_CONTRACT.reads

    def test_immutable_world_rules(self) -> None:
        assert "world_rules" in CONTINUITY_REPAIR_CONTRACT.immutable


class TestCausalValidateContract:
    def test_reads_causal_context(self) -> None:
        assert "business_dependencies" in CAUSAL_VALIDATE_CONTRACT.reads
        assert "knowledge_ledger" in CAUSAL_VALIDATE_CONTRACT.reads

    def test_read_only(self) -> None:
        assert len(CAUSAL_VALIDATE_CONTRACT.writes) == 0


class TestCausalRepairContract:
    def test_reads_causal_context(self) -> None:
        assert "business_dependencies" in CAUSAL_REPAIR_CONTRACT.reads

    def test_immutable_world_rules(self) -> None:
        assert "world_rules" in CAUSAL_REPAIR_CONTRACT.immutable


class TestExtractContract:
    def test_writes_many_groups(self) -> None:
        """Extract step populates most kernel field groups from chapter text."""
        assert "entities" in EXTRACT_CONTRACT.writes
        assert "relationships" in EXTRACT_CONTRACT.writes
        assert "timeline" in EXTRACT_CONTRACT.writes
        assert "knowledge_ledger" in EXTRACT_CONTRACT.writes
        assert "object_ledger" in EXTRACT_CONTRACT.writes
        assert "promise_ledger" in EXTRACT_CONTRACT.writes
        assert "chapter_summaries" in EXTRACT_CONTRACT.writes

    def test_immutable_world_rules(self) -> None:
        assert "world_rules" in EXTRACT_CONTRACT.immutable


class TestAlignmentContract:
    def test_read_only(self) -> None:
        assert len(ALIGNMENT_CONTRACT.writes) == 0


class TestCheckChapterContract:
    def test_reads_for_validation(self) -> None:
        assert "world_rules" in CHECK_CHAPTER_CONTRACT.reads
        assert "banned_phrases" in CHECK_CHAPTER_CONTRACT.reads

    def test_read_only(self) -> None:
        assert len(CHECK_CHAPTER_CONTRACT.writes) == 0


class TestReadingPowerEvalContract:
    def test_read_only(self) -> None:
        assert len(READING_POWER_EVAL_CONTRACT.writes) == 0


class TestReadingPowerRepairContract:
    def test_immutable_world_rules(self) -> None:
        assert "world_rules" in READING_POWER_REPAIR_CONTRACT.immutable


class TestPolishContract:
    def test_reads_for_style(self) -> None:
        assert "banned_phrases" in POLISH_CONTRACT.reads
        assert "motif_protocols" in POLISH_CONTRACT.reads

    def test_immutable_world_rules(self) -> None:
        assert "world_rules" in POLISH_CONTRACT.immutable


class TestEvaluateContract:
    def test_read_only(self) -> None:
        assert len(EVALUATE_CONTRACT.writes) == 0


class TestVolumeContract:
    def test_read_only(self) -> None:
        assert len(VOLUME_CONTRACT.writes) == 0


class TestBookConsistencyContract:
    def test_reads_all_groups(self) -> None:
        """Book consistency audit reads all kernel field groups."""
        for field in EXPECTED_FIELDS:
            assert field in BOOK_CONSISTENCY_CONTRACT.reads, (
                f"BOOK_CONSISTENCY_CONTRACT should read '{field}'"
            )

    def test_read_only(self) -> None:
        assert len(BOOK_CONSISTENCY_CONTRACT.writes) == 0


class TestMacroGuardContract:
    def test_reads_all_groups(self) -> None:
        """Macro guard audit reads all kernel field groups."""
        for field in EXPECTED_FIELDS:
            assert field in MACRO_GUARD_CONTRACT.reads, (
                f"MACRO_GUARD_CONTRACT should read '{field}'"
            )

    def test_read_only(self) -> None:
        assert len(MACRO_GUARD_CONTRACT.writes) == 0


class TestStateAdjudicationContract:
    def test_writes_state_fields(self) -> None:
        """State adjudication can modify entity and ledger fields."""
        assert "entities" in STATE_ADJUDICATION_CONTRACT.writes
        assert "relationships" in STATE_ADJUDICATION_CONTRACT.writes
        assert "knowledge_ledger" in STATE_ADJUDICATION_CONTRACT.writes

    def test_immutable_world_rules(self) -> None:
        assert "world_rules" in STATE_ADJUDICATION_CONTRACT.immutable


class TestPatchContract:
    def test_read_only_on_kernel(self) -> None:
        """Patch step modifies chapter text only, not kernel fields."""
        assert len(PATCH_CONTRACT.writes) == 0


class TestPronounCheckContract:
    def test_reads_entities(self) -> None:
        assert "entities" in PRONOUN_CHECK_CONTRACT.reads

    def test_read_only(self) -> None:
        assert len(PRONOUN_CHECK_CONTRACT.writes) == 0


class TestForbiddenSourcesContract:
    def test_reads_banned_phrases(self) -> None:
        assert "banned_phrases" in FORBIDDEN_SOURCES_CONTRACT.reads

    def test_read_only(self) -> None:
        assert len(FORBIDDEN_SOURCES_CONTRACT.writes) == 0


class TestProfileStyleContract:
    def test_read_only(self) -> None:
        assert len(PROFILE_STYLE_CONTRACT.writes) == 0


class TestCharacterIntroContract:
    def test_writes_entities_and_relationships(self) -> None:
        assert "entities" in CHARACTER_INTRO_CONTRACT.writes
        assert "relationships" in CHARACTER_INTRO_CONTRACT.writes

    def test_immutable_world_rules(self) -> None:
        assert "world_rules" in CHARACTER_INTRO_CONTRACT.immutable
