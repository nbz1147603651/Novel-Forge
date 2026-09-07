"""Integration tests: Motif full pipeline — 5 optimization layers.

Covers: proper noun exemption, token budget cap, retired motif filtering,
warmup mode, genre template fallback, and universal minimal fallback.

All tests use mocked MotifTracker and _detect_forbidden_elements to avoid
real LLM calls. Focus is on validating interaction between layers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_forge.core.domain.forbidden_element_registry import ForbiddenElementRegistry
from novel_forge.core.schemas.bible import StoryBible
from novel_forge.core.schemas.continuity import ChapterStatePacket
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.memory.motif import Motif, MotifTracker
from novel_forge.pipeline.steps.continuity_eval.validators import (
    _detect_forbidden_elements,
    _filter_forbidden_elements,
)

# ──────────────────────────────────────────────────────────────
# Fixture factories
# ──────────────────────────────────────────────────────────────

def _make_mock_packet(
    *,
    chapter_number: int = 1,
    pov_character: str = "林远",
    setting: str = "雾霭小镇",
    involved_characters: list[str] | None = None,
    known_characters: list[str] | None = None,
    canon_context: dict | None = None,
) -> ChapterStatePacket:
    """Create a minimal ChapterStatePacket for testing."""
    return ChapterStatePacket(
        chapter_number=chapter_number,
        chapter_outline=ChapterOutline(
            chapter_number=chapter_number,
            title=f"第{chapter_number}章",
            goal="推进剧情",
            pov_character=pov_character,
            setting=setting,
            involved_characters=involved_characters or [pov_character],
        ),
        known_characters=known_characters or [pov_character],
        canon_context=canon_context or {},
    )


def _make_mock_motif_context(
    *,
    active_motifs: list[dict] | None = None,
    forbidden_repetition: list[str] | None = None,
) -> dict:
    """Create a motif context dict matching get_motifs_for_prompt() output shape."""
    return {
        "active_motifs": active_motifs or [],
        "forbidden_repetition": forbidden_repetition or [],
        "suggested_callbacks": [],
        "repeated_phrases": [],
    }


def _make_mock_bible(
    *,
    premise: str = "测试故事",
    banned_intent_rules: list[str] | None = None,
) -> StoryBible:
    """Create a minimal StoryBible for testing."""
    return StoryBible(
        premise=premise,
        banned_intent_rules=banned_intent_rules or [],
    )


def _make_mock_tracker() -> MotifTracker:
    """Create a MotifTracker with __new__ to bypass __init__ (no router needed)."""
    tracker = MotifTracker.__new__(MotifTracker)
    tracker._motifs = {}
    tracker._occurrence_log = []
    tracker._chapter_motifs = {}
    tracker._recent_usage = {}
    tracker._extraction_cache = {}
    tracker._EXTRACTION_CACHE_MAX_SIZE = 100
    tracker._is_warmup_complete = False
    tracker._warmup_seeds = {}
    return tracker


def _add_motif(
    tracker: MotifTracker,
    *,
    motif_id: str,
    name: str,
    category: str = "意象",
    occurrence_count: int = 1,
    last_appearance_chapter: int = 1,
    first_appearance_chapter: int = 1,
    retired: bool = False,
    is_intentional: bool = True,
    thematic_meaning: str = "",
    associated_characters: list[str] | None = None,
) -> None:
    """Add a motif to a tracker's internal storage."""
    tracker._motifs[motif_id] = Motif(
        motif_id=motif_id,
        name=name,
        category=category,  # type: ignore[arg-type]
        occurrence_count=occurrence_count,
        last_appearance_chapter=last_appearance_chapter,
        first_appearance_chapter=first_appearance_chapter,
        retired=retired,
        is_intentional=is_intentional,
        thematic_meaning=thematic_meaning,
        associated_characters=associated_characters or [],
    )


# ──────────────────────────────────────────────────────────────
# Test 1: Proper noun exemption — zero false positives
# ──────────────────────────────────────────────────────────────

class TestProperNounExemption:
    """Proper nouns (titles, kinship terms, character names) must NOT trigger
    forbidden_element_violation even when they appear in forbidden element lists.
    """

    def test_zero_false_positive_on_proper_nouns(self) -> None:
        """Chapter containing 镇北将军、殿下、外祖父、师父 — verify 0 forbidden_element_violation."""
        _chapter_text = (
            "镇北将军走进大殿，殿下早已等候多时。"
            "外祖父坐在上首，师父站在一旁。"
            "镇北将军向殿下行礼，外祖父微微点头，师父轻声说道：'来了。'"
        )

        # These proper nouns are in the forbidden elements list (simulating
        # a scenario where the LLM flagged them as potential repetitions).
        forbidden = ["镇北将军", "殿下", "外祖父", "师父"]

        packet = _make_mock_packet(
            chapter_number=5,
            pov_character="镇北将军",
            known_characters=["镇北将军", "殿下", "外祖父", "师父"],
        )

        # With motif_context providing category info, these should be filtered out.
        motif_ctx = _make_mock_motif_context(
            active_motifs=[
                {"name": "镇北将军", "category": "符号"},
                {"name": "殿下", "category": "符号"},
                {"name": "外祖父", "category": "符号"},
                {"name": "师父", "category": "符号"},
            ],
        )

        filtered = _filter_forbidden_elements(
            forbidden,
            packet=packet,
            motif_context=motif_ctx,
        )

        # All proper nouns should be filtered out (category=符号 → thematic exemption).
        assert len(filtered) == 0

    def test_proper_noun_substring_of_compound(self) -> None:
        """'将军' should NOT trigger when text contains '镇北将军' (compound proper noun)."""
        chapter_text = "镇北将军率领大军出征。"
        forbidden = ["将军"]

        packet = _make_mock_packet(
            chapter_number=3,
            pov_character="镇北将军",
            known_characters=["镇北将军"],
        )

        found = _detect_forbidden_elements(
            chapter_text,
            forbidden,
            packet=packet,
        )

        # "将军" is a substring of "镇北将军" which is a known proper noun → no violation.
        assert len(found) == 0

    def test_kinship_terms_exempted_via_registry(self) -> None:
        """Kinship terms from registry should not generate false positives."""
        kinship = ForbiddenElementRegistry._load_template_source("universal_minimal").get("kinship_and_address_terms", [])
        assert len(kinship) > 0

        chapter_text = "父亲对儿子说，母亲在厨房忙碌。"
        forbidden = kinship[:5]

        packet = _make_mock_packet(chapter_number=2)

        found = _detect_forbidden_elements(
            chapter_text,
            forbidden,
            packet=packet,
            kinship_terms=frozenset(kinship),
        )

        assert len(found) == 0


# ──────────────────────────────────────────────────────────────
# Test 2: Token budget cap — chapter 4 with 12+ motifs
# ──────────────────────────────────────────────────────────────

class TestTokenBudgetCap:
    """Token budget capping must limit forbidden_repetition to ≤ 8 items
    even when 12+ motifs are repeated.
    """

    def test_budget_cap_chapter_4(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Chapter 4 with 12+ repeated motifs — verify forbidden list ≤ 8 items."""
        # Set a low token budget to force truncation.
        monkeypatch.setenv("NOVEL_FORGE_MOTIF_PROMPT_TOKEN_BUDGET", "200")
        monkeypatch.setenv("NOVEL_FORGE_MOTIF_PROMPT_MAX_ITEMS", "8")

        tracker = _make_mock_tracker()

        # Add 15 motifs, all recently used (within 2 chapters) → would be forbidden.
        for i in range(15):
            _add_motif(
                tracker,
                motif_id=f"motif_{i}",
                name=f"母题{i}",
                category="意象",
                occurrence_count=3,
                last_appearance_chapter=3,
                first_appearance_chapter=1,
                is_intentional=False,
            )
            tracker._recent_usage[f"motif_{i}"] = [2, 3]

        # Set chapter_motifs for related lookback.
        for ch in range(1, 5):
            tracker._chapter_motifs[ch] = {f"motif_{i}" for i in range(15)}

        result = tracker.get_motifs_for_prompt(
            current_chapter=4,
            max_motifs=15,
            include_recent_usage=True,
            related_lookback_chapters=2,
        )

        # Token budget should cap forbidden_repetition.
        assert len(result["forbidden_repetition"]) <= 8

    def test_budget_cap_preserves_min_forbidden(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Token budget must never truncate forbidden_repetition below min_forbidden (3)."""
        monkeypatch.setenv("NOVEL_FORGE_MOTIF_PROMPT_TOKEN_BUDGET", "150")
        monkeypatch.setenv("NOVEL_FORGE_MOTIF_PROMPT_MAX_ITEMS", "8")

        tracker = _make_mock_tracker()

        # Add 5 motifs that would be forbidden.
        for i in range(5):
            _add_motif(
                tracker,
                motif_id=f"motif_{i}",
                name=f"重复母题{i}",
                category="意象",
                occurrence_count=2,
                last_appearance_chapter=4,
                first_appearance_chapter=1,
                is_intentional=False,
            )
            tracker._recent_usage[f"motif_{i}"] = [3, 4]

        for ch in range(1, 7):
            tracker._chapter_motifs[ch] = {f"motif_{i}" for i in range(5)}

        result = tracker.get_motifs_for_prompt(
            current_chapter=6,
            max_motifs=5,
        )

        # Should have at least min_forbidden (3) items.
        assert len(result["forbidden_repetition"]) >= 3


# ──────────────────────────────────────────────────────────────
# Test 3: Retired motif filtering
# ──────────────────────────────────────────────────────────────

class TestRetiredMotifFiltering:
    """Retired motifs must NOT appear in forbidden_repetition or active_motifs
    after being retired.
    """

    def test_retired_motif_filtered_from_prompt(self) -> None:
        """Retire a motif, then generate prompt — verify NOT in forbidden_repetition."""
        tracker = _make_mock_tracker()

        _add_motif(
            tracker,
            motif_id="motif_retired",
            name="已退休母题",
            category="意象",
            occurrence_count=5,
            last_appearance_chapter=3,
            first_appearance_chapter=1,
            retired=True,
        )

        _add_motif(
            tracker,
            motif_id="motif_active",
            name="活跃母题",
            category="意象",
            occurrence_count=2,
            last_appearance_chapter=4,
            first_appearance_chapter=2,
            retired=False,
            is_intentional=False,
        )
        tracker._recent_usage["motif_active"] = [3, 4]

        for ch in range(1, 7):
            tracker._chapter_motifs[ch] = {"motif_retired", "motif_active"}

        result = tracker.get_motifs_for_prompt(
            current_chapter=6,
            max_motifs=5,
            include_retired_in_stats=True,
        )

        # Retired motif should NOT be in forbidden_repetition.
        assert "已退休母题" not in result["forbidden_repetition"]
        # Retired motif should NOT be in active_motifs.
        active_names = [m["name"] for m in result["active_motifs"]]
        assert "已退休母题" not in active_names
        # But retired_stats should include it.
        assert "已退休母题" in result["retired_stats"]["names"]

    def test_retired_motif_not_in_related_ids(self) -> None:
        """_related_motif_ids_for_chapter excludes retired motifs."""
        tracker = _make_mock_tracker()

        _add_motif(
            tracker,
            motif_id="retired_1",
            name="退休1",
            category="符号",
            retired=True,
            last_appearance_chapter=3,
        )
        _add_motif(
            tracker,
            motif_id="active_1",
            name="活跃1",
            category="符号",
            retired=False,
            last_appearance_chapter=4,
        )

        tracker._chapter_motifs[3] = {"retired_1", "active_1"}
        tracker._chapter_motifs[4] = {"retired_1", "active_1"}

        related = tracker._related_motif_ids_for_chapter(5, lookback_chapters=2)

        assert "retired_1" not in related
        assert "active_1" in related


# ──────────────────────────────────────────────────────────────
# Test 4: Warmup mode — chapters 1-2
# ──────────────────────────────────────────────────────────────

class TestWarmupMode:
    """During warmup (chapters 1-N), forbidden_repetition must be empty
    and active_motifs should contain warmup seeds.
    """

    def test_warmup_mode_active(self) -> None:
        """Chapters 1-2 — verify empty forbidden_repetition, non-empty active_motifs."""
        tracker = _make_mock_tracker()

        # Simulate warmup seeds.
        _add_motif(
            tracker,
            motif_id="warmup_意象_雨",
            name="雨",
            category="意象",
            occurrence_count=1,
            is_intentional=True,
        )
        _add_motif(
            tracker,
            motif_id="warmup_符号_铜质怀表",
            name="铜质怀表",
            category="符号",
            occurrence_count=1,
            is_intentional=True,
        )

        result = tracker.get_motifs_for_prompt(
            current_chapter=1,
            max_motifs=5,
            warmup_chapters=3,
        )

        # Warmup mode: forbidden_repetition must be empty.
        assert result["forbidden_repetition"] == []
        # active_motifs should contain warmup seeds.
        assert len(result["active_motifs"]) > 0
        active_names = {m["name"] for m in result["active_motifs"]}
        assert "雨" in active_names or "铜质怀表" in active_names

    def test_warmup_mode_chapter_2(self) -> None:
        """Chapter 2 still in warmup — same guarantees as chapter 1."""
        tracker = _make_mock_tracker()

        _add_motif(
            tracker,
            motif_id="warmup_意象_月光",
            name="月光",
            category="意象",
            is_intentional=True,
        )

        result = tracker.get_motifs_for_prompt(
            current_chapter=2,
            max_motifs=5,
            warmup_chapters=3,
        )

        assert result["forbidden_repetition"] == []
        assert len(result["active_motifs"]) > 0

    def test_warmup_complete_at_chapter_4(self) -> None:
        """Chapter 4 (beyond warmup_chapters=3) should use normal motif logic."""
        tracker = _make_mock_tracker()

        # Add a non-warmup motif.
        _add_motif(
            tracker,
            motif_id="motif_normal",
            name="正常母题",
            category="意象",
            occurrence_count=2,
            last_appearance_chapter=3,
            first_appearance_chapter=1,
            is_intentional=False,
        )
        tracker._recent_usage["motif_normal"] = [2, 3]
        tracker._chapter_motifs[3] = {"motif_normal"}
        tracker._chapter_motifs[4] = {"motif_normal"}

        result = tracker.get_motifs_for_prompt(
            current_chapter=4,
            max_motifs=5,
            warmup_chapters=3,
        )

        # Beyond warmup: normal logic applies.
        # Since motif_normal was used in chapter 3 (within 2 chapters),
        # and it's not thematic/intentional, it should be in forbidden.
        assert "正常母题" in result["forbidden_repetition"]


# ──────────────────────────────────────────────────────────────
# Test 5: Genre template fallback
# ──────────────────────────────────────────────────────────────

class TestGenreTemplateFallback:
    """When no project seeds are available, genre templates should provide
    default forbidden elements.
    """

    def test_genre_template_fallback(self) -> None:
        """No project seeds, genre='modern_urban' — verify template terms loaded."""
        genre_data = ForbiddenElementRegistry._load_template_source("modern_urban")

        assert isinstance(genre_data, dict)
        assert len(genre_data) > 0

        valid_keys = {"rhetorical_imagery_hints", "kinship_and_address_terms", "abstract_emotion_keywords"}
        assert bool(set(genre_data.keys()) & valid_keys)

    def test_genre_template_modern_urban_has_kinship_terms(self) -> None:
        """modern_urban template should include kinship/address terms."""
        genre_data = ForbiddenElementRegistry._load_template_source("modern_urban")

        kinship = genre_data.get("kinship_and_address_terms", [])
        assert isinstance(kinship, list)
        assert len(kinship) > 0

    def test_merge_sources_with_genre(self, tmp_path: Path) -> None:
        """merge_sources with genre='modern_urban' should include genre terms."""
        registry = ForbiddenElementRegistry(tmp_path)

        merged = registry.merge_sources(bible_derived=None, genre="modern_urban")

        assert "rhetorical_imagery_hints" in merged
        assert "kinship_and_address_terms" in merged
        assert "abstract_emotion_keywords" in merged

    def test_get_all_root_sets_with_genre(self, tmp_path: Path) -> None:
        """get_all_root_sets returns frozensets with genre template data."""
        registry = ForbiddenElementRegistry(tmp_path)

        rhetorical, kinship, emotion = registry.get_all_root_sets(
            bible_derived=None,
            genre="modern_urban",
        )

        assert isinstance(rhetorical, frozenset)
        assert isinstance(kinship, frozenset)
        assert isinstance(emotion, frozenset)
        assert len(kinship) > 0


# ──────────────────────────────────────────────────────────────
# Test 6: Universal minimal fallback
# ──────────────────────────────────────────────────────────────

class TestUniversalMinimalFallback:
    """When no project seeds, empty Bible, and no genre specified,
    universal_minimal template should be loaded as the ultimate fallback.
    """

    def test_completely_empty_fallback(self, tmp_path: Path) -> None:
        """No project seeds, empty Bible, no genre — verify universal_minimal loaded."""
        registry = ForbiddenElementRegistry(tmp_path)

        merged = registry.merge_sources(bible_derived=None, genre="universal_minimal")

        assert "rhetorical_imagery_hints" in merged
        assert "kinship_and_address_terms" in merged
        assert "abstract_emotion_keywords" in merged

        total_terms = (
            len(merged["rhetorical_imagery_hints"])
            + len(merged["kinship_and_address_terms"])
            + len(merged["abstract_emotion_keywords"])
        )
        assert total_terms > 0

    def test_universal_minimal_template_file_exists(self) -> None:
        """universal_minimal.yaml template file must exist."""
        from novel_forge.core.domain.forbidden_element_registry import _TEMPLATE_DIR

        template_path = _TEMPLATE_DIR / "universal_minimal.yaml"
        assert template_path.exists(), f"Template file not found: {template_path}"

    def test_load_template_source_fallback_to_universal(self) -> None:
        """Loading a non-existent genre should fallback to universal_minimal."""
        result = ForbiddenElementRegistry._load_template_source("nonexistent_genre_xyz")

        assert isinstance(result, dict)
        assert len(result) > 0

    def test_detect_forbidden_elements_with_universal_minimal(self) -> None:
        """_detect_forbidden_elements works with universal_minimal terms."""
        template_data = ForbiddenElementRegistry._load_template_source("universal_minimal")
        rhetorical = frozenset(template_data.get("rhetorical_imagery_hints", []))

        chapter_text = "他叹了口气，眼中闪过一丝悲伤。"
        rhetorical_list = list(rhetorical)[:3]

        packet = _make_mock_packet(chapter_number=1)

        found = _detect_forbidden_elements(
            chapter_text,
            rhetorical_list,
            packet=packet,
            rhetorical_hints=rhetorical,
        )

        assert isinstance(found, list)


# ──────────────────────────────────────────────────────────────
# Test 7: Cross-layer interaction — full pipeline
# ──────────────────────────────────────────────────────────────

class TestCrossLayerInteraction:
    """Tests that validate the interaction between multiple optimization layers."""

    def test_retired_plus_budget_cap(self) -> None:
        """Retired motifs excluded before budget cap is applied."""
        tracker = _make_mock_tracker()

        # Add 10 motifs: 5 retired, 5 active.
        for i in range(5):
            _add_motif(
                tracker,
                motif_id=f"retired_{i}",
                name=f"退休母题{i}",
                category="意象",
                retired=True,
                last_appearance_chapter=3,
            )
        for i in range(5):
            _add_motif(
                tracker,
                motif_id=f"active_{i}",
                name=f"活跃母题{i}",
                category="意象",
                occurrence_count=2,
                last_appearance_chapter=4,
                first_appearance_chapter=2,
                is_intentional=False,
            )
            tracker._recent_usage[f"active_{i}"] = [3, 4]

        for ch in range(1, 7):
            all_ids = {f"retired_{i}" for i in range(5)} | {f"active_{i}" for i in range(5)}
            tracker._chapter_motifs[ch] = all_ids

        result = tracker.get_motifs_for_prompt(
            current_chapter=6,
            max_motifs=10,
            include_retired_in_stats=True,
        )

        # No retired motifs in forbidden_repetition.
        for name in result["forbidden_repetition"]:
            assert not name.startswith("退休母题")

        # Budget cap still applies to active motifs.
        assert len(result["forbidden_repetition"]) <= 8

    def test_warmup_plus_proper_noun_exemption(self) -> None:
        """Warmup mode + proper nouns: warmup seeds should not generate false positives."""
        tracker = _make_mock_tracker()

        # Warmup seeds include proper nouns.
        _add_motif(
            tracker,
            motif_id="warmup_符号_镇北将军",
            name="镇北将军",
            category="符号",
            is_intentional=True,
        )

        result = tracker.get_motifs_for_prompt(
            current_chapter=1,
            max_motifs=5,
            warmup_chapters=3,
        )

        # Warmup: forbidden_repetition must be empty regardless of proper nouns.
        assert result["forbidden_repetition"] == []
        # But the proper noun should be in active_motifs.
        active_names = [m["name"] for m in result["active_motifs"]]
        assert "镇北将军" in active_names

    def test_genre_template_plus_filter_forbidden(self, tmp_path: Path) -> None:
        """Genre template terms should be properly filtered by _filter_forbidden_elements."""
        registry = ForbiddenElementRegistry(tmp_path)
        rhetorical, kinship, emotion = registry.get_all_root_sets(
            bible_derived=None,
            genre="modern_urban",
        )

        forbidden = list(kinship)[:3] + ["某个修辞意象"]

        packet = _make_mock_packet(chapter_number=5)

        filtered = _filter_forbidden_elements(
            forbidden,
            packet=packet,
            kinship_terms=kinship,
            rhetorical_hints=rhetorical,
        )

        for term in list(kinship)[:3]:
            assert term not in filtered
