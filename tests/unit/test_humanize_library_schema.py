"""Tests for HumanizeLibraryEntry schema and LIBRARY_BUILTIN_ENTRIES metadata.

Covers:
  - 30 total entries (26 regex + 4 llm_only; 5 form-level detectors added after
    山风与归人2 audit, cross_chapter_template restored)
  - pattern_id acceptance / rejection
  - extra="forbid" enforcement
  - schema_version inheritance
  - llm_only entries ship disabled
  - 1:1 mapping between regex entries and _HUMANIZE_RULES
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from novel_forge.core.schemas.humanize import HUMANIZE_PATTERN_IDS
from novel_forge.core.schemas.humanize_library import (
    LIBRARY_BUILTIN_ENTRIES,
    LIBRARY_SCHEMA_VERSION,
    HumanizeLibraryEntry,
)
from novel_forge.pipeline.steps.humanize_scan_step import _HUMANIZE_RULES

# ---------------------------------------------------------------------------
# LIBRARY_BUILTIN_ENTRIES structure
# ---------------------------------------------------------------------------


class TestBuiltinEntriesCount:
    """Verify the 26 + 4 split.

    26 regex entries (21 original + 5 form-level AI-flavor detectors added in
    response to the 山风与归人2 audit) + 4 llm_only entries = 30 total.
    The 4th llm_only entry (cross_chapter_template) was previously missing from
    LIBRARY_BUILTIN_ENTRIES but existed in HUMANIZE_PATTERN_IDS.
    """

    EXPECTED_TOTAL = 30
    EXPECTED_REGEX = 26
    EXPECTED_LLM_ONLY = 4

    def test_total(self) -> None:
        assert len(LIBRARY_BUILTIN_ENTRIES) == self.EXPECTED_TOTAL

    def test_regex_llm_split(self) -> None:
        regex_count = sum(
            1 for e in LIBRARY_BUILTIN_ENTRIES if e["detection_method"] == "regex"
        )
        llm_count = sum(
            1 for e in LIBRARY_BUILTIN_ENTRIES if e["detection_method"] == "llm_only"
        )
        assert regex_count == self.EXPECTED_REGEX, f"expected {self.EXPECTED_REGEX} regex, got {regex_count}"
        assert llm_count == self.EXPECTED_LLM_ONLY, f"expected {self.EXPECTED_LLM_ONLY} llm_only, got {llm_count}"


class TestBuiltinEntriesIds:
    """Verify IDs match HUMANIZE_PATTERN_IDS."""

    def test_ids_match_humanize_pattern_ids(self) -> None:
        builtin_ids = {e["pattern_id"] for e in LIBRARY_BUILTIN_ENTRIES}
        assert builtin_ids == set(HUMANIZE_PATTERN_IDS), (
            f"mismatch: {builtin_ids ^ set(HUMANIZE_PATTERN_IDS)}"
        )


class TestBuiltinEntriesLlOnlyDisabled:
    """llm_only entries must ship enabled=False."""

    def test_llm_only_entries_enabled_false(self) -> None:
        llm_entries = [
            e for e in LIBRARY_BUILTIN_ENTRIES if e["detection_method"] == "llm_only"
        ]
        for entry in llm_entries:
            assert entry["enabled"] is False, (
                f'llm_only entry {entry["pattern_id"]} should be disabled'
            )


class TestBuiltinEntriesRegexCoverage:
    """Regex entries must map 1:1 to _HUMANIZE_RULES."""

    def test_regex_entries_cover_rules_one_to_one(self) -> None:
        regex_ids = {
            e["pattern_id"]
            for e in LIBRARY_BUILTIN_ENTRIES
            if e["detection_method"] == "regex"
        }
        rule_ids = {r.pattern_id for r in _HUMANIZE_RULES}
        assert regex_ids == rule_ids, (
            f"regex entries {regex_ids - rule_ids} not in rules, "
            f"rules {rule_ids - regex_ids} not in entries"
        )

    def test_regex_entries_include_executable_detection_config(self) -> None:
        regex_entries = [
            entry for entry in LIBRARY_BUILTIN_ENTRIES if entry["detection_method"] == "regex"
        ]

        assert all(entry["detection_config"]["patterns"] for entry in regex_entries)
        negative = next(
            entry for entry in regex_entries if entry["pattern_id"] == "negative_parallelism"
        )
        assert "而是" in negative["detection_config"]["patterns"][0]


# ---------------------------------------------------------------------------
# HumanizeLibraryEntry construction / validation
# ---------------------------------------------------------------------------


class TestHumanizeLibraryEntry:
    """Validate pattern_id acceptance rules, extra=forbid, and defaults."""

    def test_builtin_id_accepted(self) -> None:
        entry = HumanizeLibraryEntry(
            pattern_id="significance_inflation",
            pattern_name="显著性通胀",
            category="叙事轻重",
            severity="high",
            detection_method="regex",
            source="builtin",
        )
        assert entry.pattern_id == "significance_inflation"

    def test_lib_user_accepted(self) -> None:
        entry = HumanizeLibraryEntry(
            pattern_id="lib_user_abc12345",
            pattern_name="自定义模式",
            category="用户定义",
            severity="medium",
            detection_method="regex",
            source="user",
        )
        assert entry.pattern_id == "lib_user_abc12345"

    def test_lib_imported_accepted(self) -> None:
        entry = HumanizeLibraryEntry(
            pattern_id="lib_imported_abcdef12",
            pattern_name="导入模式",
            category="导入",
            severity="medium",
            detection_method="regex",
            source="imported",
        )
        assert entry.pattern_id == "lib_imported_abcdef12"

    def test_random_garbage_rejected(self) -> None:
        with pytest.raises(ValidationError):
            HumanizeLibraryEntry(
                pattern_id="random_garbage",
                pattern_name="x",
                category="y",
                severity="high",
                detection_method="regex",
                source="builtin",
            )

    def test_lib_user_short_rejected(self) -> None:
        """8 hex characters required after lib_user_ prefix."""
        with pytest.raises(ValidationError):
            HumanizeLibraryEntry(
                pattern_id="lib_user_short",
                pattern_name="x",
                category="y",
                severity="high",
                detection_method="regex",
                source="user",
            )

    def test_extra_forbid(self) -> None:
        """extra="forbid" from VersionedSchema must reject unknown fields."""
        with pytest.raises(ValidationError):
            HumanizeLibraryEntry(
                pattern_id="significance_inflation",
                pattern_name="x",
                category="y",
                severity="high",
                detection_method="regex",
                source="builtin",
                extra_unknown="foo",  # type: ignore[call-arg]
            )

    def test_schema_version_default(self) -> None:
        entry = HumanizeLibraryEntry(
            pattern_id="significance_inflation",
            pattern_name="x",
            category="y",
            severity="high",
            detection_method="regex",
            source="builtin",
        )
        assert entry.schema_version == "2.0"

    def test_library_schema_version_constant(self) -> None:
        assert LIBRARY_SCHEMA_VERSION == "2.0"

    def test_created_at_is_set(self) -> None:
        entry = HumanizeLibraryEntry(
            pattern_id="significance_inflation",
            pattern_name="x",
            category="y",
            severity="high",
            detection_method="regex",
            source="builtin",
        )
        assert entry.created_at is not None

    def test_field_defaults(self) -> None:
        """Optional fields should have sensible defaults."""
        entry = HumanizeLibraryEntry(
            pattern_id="significance_inflation",
            pattern_name="x",
            category="y",
            severity="high",
            detection_method="regex",
            source="builtin",
        )
        assert entry.example_phrases == []
        assert entry.example_template == ""
        assert entry.detection_config == {}
        assert entry.keywords == []
        assert entry.project_id is None
        assert entry.notes == ""
        assert entry.hit_count == 0
        assert entry.first_seen_at is None
        assert entry.last_seen_at is None
        assert entry.last_hit_chapter is None
        assert entry.enabled is True
        assert entry.embedding_signature is None
        assert entry.vector_stale is False
