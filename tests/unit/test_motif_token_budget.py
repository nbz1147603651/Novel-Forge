"""Tests for advisory token pressure in get_motifs_for_prompt().

Verifies that:
- No truncation occurs when under budget
- Over-budget selected evidence remains complete
- Environment variable configuration works
- Warnings are logged when pressure exceeds the threshold
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

from novel_forge.memory.motif import Motif, MotifPromptBudget, MotifTracker


def _make_tracker() -> MotifTracker:
    return MotifTracker(router=AsyncMock(), builder=AsyncMock())


def _add_motif(
    tracker: MotifTracker,
    motif_id: str,
    name: str,
    category: str = "意象",
    retired: bool = False,
    chapters: list[int] | None = None,
    thematic_meaning: str = "",
    is_intentional: bool = True,
) -> None:
    chapters = chapters or [1]
    motif = Motif(
        motif_id=motif_id,
        name=name,
        category=category,
        is_intentional=is_intentional,
        occurrence_count=len(chapters),
        first_appearance_chapter=chapters[0] if chapters else 0,
        last_appearance_chapter=chapters[-1] if chapters else 0,
        retired=retired,
        thematic_meaning=thematic_meaning or f"{name}→meaning",
    )
    tracker._motifs[motif_id] = motif
    tracker._recent_usage[motif_id] = chapters
    for ch in chapters:
        if ch not in tracker._chapter_motifs:
            tracker._chapter_motifs[ch] = set()
        tracker._chapter_motifs[ch].add(motif_id)


class TestMotifPromptBudgetDataclass:
    """Tests for the MotifPromptBudget configuration class."""

    def test_default_values(self) -> None:
        budget = MotifPromptBudget()
        assert budget.max_items == 8
        assert budget.budget_tokens == 500
        assert budget.min_forbidden == 3

    def test_from_env_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "novel_forge.memory.motif._settings_or_env_int",
                side_effect=lambda _env, _attr, default: default,
            ):
                budget = MotifPromptBudget.from_env()
            assert budget.budget_tokens == 500
            assert budget.max_items == 8

    def test_from_env_custom_budget(self) -> None:
        with patch.dict(
            os.environ,
            {
                "NOVEL_FORGE_MOTIF_PROMPT_TOKEN_BUDGET": "1000",
                "NOVEL_FORGE_MOTIF_PROMPT_MAX_ITEMS": "12",
            },
        ):
            budget = MotifPromptBudget.from_env()
            assert budget.budget_tokens == 1000
            assert budget.max_items == 12


class TestTokenBudgetCapping:
    """Tests for non-destructive motif budget pressure."""

    def test_no_budget_no_truncation(self) -> None:
        """When under budget, no truncation should occur."""
        tracker = _make_tracker()
        for i in range(3):
            _add_motif(tracker, f"motif-{i}", f"母题{i}", chapters=[2])

        with patch.dict(os.environ, {"NOVEL_FORGE_MOTIF_PROMPT_TOKEN_BUDGET": "5000"}):
            result = tracker.get_motifs_for_prompt(current_chapter=4)

        assert len(result["active_motifs"]) == 3
        assert len(result["forbidden_repetition"]) <= 3

    def test_budget_with_lots_of_content_preserves_selected_evidence(self) -> None:
        """Over-budget motif evidence is preserved for route-level handling."""
        tracker = _make_tracker()
        # Add many motifs with long thematic meanings to exceed budget
        for i in range(15):
            _add_motif(
                tracker,
                f"motif-{i}",
                f"母题{i}",
                chapters=[1, 2],
                thematic_meaning=f"母题{i}→这是一个非常长的主题意义描述，用于增加token数量" * 5,
            )

        with patch.dict(os.environ, {"NOVEL_FORGE_MOTIF_PROMPT_TOKEN_BUDGET": "200"}):
            result = tracker.get_motifs_for_prompt(current_chapter=4)

        from novel_forge.core.parsing.token_utils import estimate_dict_tokens

        assert estimate_dict_tokens(result) > 200
        assert all("thematic_meaning" in item for item in result["active_motifs"])

    def test_forbidden_alone_exceeds_budget_truncates_to_min(self) -> None:
        """When forbidden_repetition alone exceeds budget, truncate to min_forbidden."""
        tracker = _make_tracker()
        # Add motifs that will all be in forbidden (recent usage, not thematic)
        for i in range(10):
            _add_motif(
                tracker,
                f"motif-{i}",
                f"意象{i}" * 10,  # Long names to exceed budget
                category="意象",
                chapters=[2],  # Recent chapter
                thematic_meaning="x" * 200,
            )

        with patch.dict(
            os.environ,
            {
                "NOVEL_FORGE_MOTIF_PROMPT_TOKEN_BUDGET": "100",
                "NOVEL_FORGE_MOTIF_PROMPT_MAX_ITEMS": "8",
            },
        ):
            result = tracker.get_motifs_for_prompt(current_chapter=4)

        # Should preserve at least min_forbidden (3) items
        assert len(result["forbidden_repetition"]) >= 3

    def test_priority_order_forbidden_preserved_over_callbacks(self) -> None:
        """forbidden_repetition should be preserved over suggested_callbacks."""
        tracker = _make_tracker()
        # Add motifs that generate forbidden entries
        for i in range(5):
            _add_motif(
                tracker,
                f"motif-{i}",
                f"母题{i}",
                category="动作",
                chapters=[2],
                thematic_meaning="x" * 100,
                is_intentional=False,
            )

        with patch.dict(os.environ, {"NOVEL_FORGE_MOTIF_PROMPT_TOKEN_BUDGET": "300"}):
            result = tracker.get_motifs_for_prompt(current_chapter=4)

        # forbidden should be preserved, callbacks may be truncated
        assert len(result["forbidden_repetition"]) >= 3

    def test_default_forbidden_prompt_hints_are_focused(self) -> None:
        """Recent motif repetition should produce a short hint list, not a broad ban list."""
        tracker = _make_tracker()
        for i in range(8):
            _add_motif(
                tracker,
                f"motif-{i}",
                f"意象{i}",
                category="意象",
                chapters=[2],
                is_intentional=False,
            )

        with patch.dict(
            os.environ,
            {
                "NOVEL_FORGE_MOTIF_PROMPT_TOKEN_BUDGET": "5000",
                "NOVEL_FORGE_MOTIF_FORBIDDEN_MAX_ITEMS": "3",
            },
        ):
            result = tracker.get_motifs_for_prompt(current_chapter=4, max_motifs=8)

        assert len(result["forbidden_repetition"]) == 3

    def test_forbidden_prompt_hint_limit_can_be_overridden(self) -> None:
        tracker = _make_tracker()
        for i in range(8):
            _add_motif(
                tracker,
                f"motif-{i}",
                f"意象{i}",
                category="意象",
                chapters=[2],
                is_intentional=False,
            )

        with patch.dict(
            os.environ,
            {
                "NOVEL_FORGE_MOTIF_PROMPT_TOKEN_BUDGET": "5000",
                "NOVEL_FORGE_MOTIF_FORBIDDEN_MAX_ITEMS": "5",
            },
        ):
            result = tracker.get_motifs_for_prompt(current_chapter=4, max_motifs=8)

        assert len(result["forbidden_repetition"]) == 5

    def test_minimum_coherence_constraint(self) -> None:
        """forbidden_repetition should never go below min_forbidden (3)."""
        tracker = _make_tracker()
        for i in range(8):
            _add_motif(
                tracker,
                f"motif-{i}",
                f"母题{'长' * 20}{i}",
                category="意象",
                chapters=[2],
                thematic_meaning="y" * 150,
            )

        with patch.dict(os.environ, {"NOVEL_FORGE_MOTIF_PROMPT_TOKEN_BUDGET": "50"}):
            result = tracker.get_motifs_for_prompt(current_chapter=4)

        assert len(result["forbidden_repetition"]) >= 3

    def test_warning_logged_on_preserved_overflow(self, caplog) -> None:
        """Pressure logs make the lossless overflow policy explicit."""
        tracker = _make_tracker()
        for i in range(10):
            _add_motif(
                tracker,
                f"motif-{i}",
                f"母题{'长' * 15}{i}",
                chapters=[2],
                thematic_meaning="z" * 100,
            )

        with patch.dict(os.environ, {"NOVEL_FORGE_MOTIF_PROMPT_TOKEN_BUDGET": "100"}):
            with caplog.at_level("WARNING"):
                tracker.get_motifs_for_prompt(current_chapter=4)

        assert any("motif_token_budget" in record.message for record in caplog.records)
        assert any(
            "selected_evidence_preserved=true" in record.message for record in caplog.records
        )

    def test_budget_keeps_complete_thematic_meaning(self) -> None:
        """High pressure must not delete a selected motif's semantic meaning."""
        tracker = _make_tracker()
        result = {
            "active_motifs": [
                {
                    "motif_id": "m1",
                    "name": "雨",
                    "category": "意象",
                    "occurrence_count": 2,
                    "thematic_meaning": "雨水象征清洗、误认、重生以及角色对过往因果的重新解释" * 8,
                    "importance_score": 0.5,
                }
            ],
            "forbidden_repetition": [],
            "suggested_callbacks": [],
            "repeated_phrases": [],
        }

        with patch.dict(os.environ, {"NOVEL_FORGE_MOTIF_PROMPT_TOKEN_BUDGET": "80"}):
            tracker._apply_token_budget(result)

        assert result["active_motifs"][0]["thematic_meaning"].endswith("角色对过往因果的重新解释")

    def test_no_warning_when_under_budget(self, caplog) -> None:
        """No warning should be logged when under budget."""
        tracker = _make_tracker()
        for i in range(2):
            _add_motif(tracker, f"motif-{i}", f"母题{i}", chapters=[2])

        with patch.dict(os.environ, {"NOVEL_FORGE_MOTIF_PROMPT_TOKEN_BUDGET": "5000"}):
            with caplog.at_level("WARNING"):
                tracker.get_motifs_for_prompt(current_chapter=4)

        assert not any("motif_token_budget" in record.message for record in caplog.records)

    def test_return_type_keys_unchanged(self) -> None:
        """Public return type keys stay stable under advisory pressure."""
        tracker = _make_tracker()
        for i in range(10):
            _add_motif(
                tracker,
                f"motif-{i}",
                f"母题{i}",
                chapters=[2],
                thematic_meaning="x" * 100,
            )

        with patch.dict(os.environ, {"NOVEL_FORGE_MOTIF_PROMPT_TOKEN_BUDGET": "100"}):
            result = tracker.get_motifs_for_prompt(current_chapter=4)

        expected_keys = {
            "active_motifs",
            "forbidden_repetition",
            "suggested_callbacks",
            "repeated_phrases",
            "unified_guidance",
            "soft_forbidden_themes",
        }
        assert set(result.keys()) == expected_keys

    def test_custom_env_budget_applied(self) -> None:
        """Custom budget from env var should be respected."""
        tracker = _make_tracker()
        for i in range(8):
            _add_motif(
                tracker,
                f"motif-{i}",
                f"母题{i}",
                chapters=[2],
                thematic_meaning="x" * 80,
            )

        # With tight budget, should truncate
        with patch.dict(os.environ, {"NOVEL_FORGE_MOTIF_PROMPT_TOKEN_BUDGET": "150"}):
            result_tight = tracker.get_motifs_for_prompt(current_chapter=4)

        # With loose budget, should not truncate
        with patch.dict(os.environ, {"NOVEL_FORGE_MOTIF_PROMPT_TOKEN_BUDGET": "5000"}):
            result_loose = tracker.get_motifs_for_prompt(current_chapter=4)

        # Tight budget should result in fewer or equal tokens
        from novel_forge.core.parsing.token_utils import estimate_dict_tokens

        assert estimate_dict_tokens(result_tight) <= estimate_dict_tokens(result_loose)

    def test_repeated_phrases_not_affected_by_budget(self) -> None:
        """repeated_phrases should not be truncated by budget logic."""
        tracker = _make_tracker()
        _add_motif(tracker, "motif-0", "母题0", chapters=[2])

        chapter_text = "这是一个测试文本。测试文本应该被检测到重复。重复的内容很重要。" * 10

        with patch.dict(os.environ, {"NOVEL_FORGE_MOTIF_PROMPT_TOKEN_BUDGET": "50"}):
            result = tracker.get_motifs_for_prompt(
                current_chapter=4,
                chapter_text=chapter_text,
            )

        # repeated_phrases should still be populated
        assert "repeated_phrases" in result

    def test_retired_stats_not_counted_in_budget(self) -> None:
        """retired_stats should be added after budget capping."""
        tracker = _make_tracker()
        _add_motif(tracker, "motif-0", "母题0", chapters=[2])
        _add_motif(tracker, "motif-retired-0", "已退休", retired=True, chapters=[2])

        with patch.dict(os.environ, {"NOVEL_FORGE_MOTIF_PROMPT_TOKEN_BUDGET": "100"}):
            result = tracker.get_motifs_for_prompt(
                current_chapter=4,
                include_retired_in_stats=True,
            )

        assert "retired_stats" in result
        assert result["retired_stats"]["count"] == 1
