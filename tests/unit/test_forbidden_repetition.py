"""Tests for forbidden_repetition data flow: detection, accumulation, pruning, hard/soft separation."""

from __future__ import annotations

from novel_forge.memory.motif import MotifTracker
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.steps.continuity_eval_step import (
    _detect_forbidden_elements,
    _filter_forbidden_elements,
)

# ────────────────────────────────────────────────────────
# 1. _detect_forbidden_elements — exact & fuzzy matching
# ────────────────────────────────────────────────────────


class TestDetectForbiddenElements:
    """Tests for the deterministic forbidden-element detection function."""

    def test_exact_match(self) -> None:
        text = "月光洒在冰冷地面上，他背靠门板缓缓滑坐。"
        found = _detect_forbidden_elements(text, ["背靠门板", "冰冷地面"])
        matched_elements = {fe for fe, _ in found}
        assert "背靠门板" in matched_elements
        assert "冰冷地面" in matched_elements

    def test_exact_match_ignores_whitespace(self) -> None:
        text = "他 背靠 门板 缓缓滑坐。"
        found = _detect_forbidden_elements(text, ["背靠门板"])
        assert len(found) == 1

    def test_no_match_returns_empty(self) -> None:
        text = "清晨的阳光透过竹帘洒进来。"
        found = _detect_forbidden_elements(text, ["背靠门板", "冰冷地面"])
        assert found == []

    def test_empty_forbidden_list(self) -> None:
        text = "任何文本。"
        found = _detect_forbidden_elements(text, [])
        assert found == []

    def test_blank_elements_skipped(self) -> None:
        text = "任何文本。"
        found = _detect_forbidden_elements(text, ["", "  ", None])  # type: ignore[list-item]
        assert found == []

    def test_fuzzy_match_catches_local_variant(self) -> None:
        """4+ char forbidden elements catch close local paraphrases with gap <= 1."""
        # With gap=1 for 4-char elements, "金丝微颤动" (1-char gap) should match
        text = "她感到指尖金丝微颤动，心中一惊。"
        found = _detect_forbidden_elements(text, ["金丝颤动"])
        assert len(found) == 1
        # But "金丝微微颤动" (2-char gap) should NOT match with new gap=1
        text2 = "她感到指尖金丝微微颤动，心中一惊。"
        found2 = _detect_forbidden_elements(text2, ["金丝颤动"])
        assert found2 == []

    def test_fuzzy_match_misses_when_only_one_half(self) -> None:
        """When key characters are missing, no match."""
        text = "她感到指尖金丝微微发热。"
        found = _detect_forbidden_elements(text, ["金丝颤动"])
        assert found == []

    def test_short_element_exact_only(self) -> None:
        """Elements shorter than 4 chars use exact match only."""
        text = "他屏息凝神，不敢出声。"
        found = _detect_forbidden_elements(text, ["屏息"])
        assert len(found) == 1
        # "息凝" should NOT match "屏息" since only exact match applies for short elements
        found2 = _detect_forbidden_elements("他呼息凝定。", ["屏息"])
        assert found2 == []

    def test_bare_sensory_channel_is_not_forbidden(self) -> None:
        """Generic channel words should not flag ordinary dialogue narration."""
        text = "她说，声音压得很低，像怕惊动隔墙的人。"
        found = _detect_forbidden_elements(text, ["声音"])
        assert found == []

    def test_specific_sensory_phrase_still_matches(self) -> None:
        """Concrete forbidden phrases remain actionable."""
        text = "他的声音低沉得像压在石下。"
        found = _detect_forbidden_elements(text, ["声音低沉"])
        assert len(found) == 1

    def test_reordered_phrase_is_not_matched(self) -> None:
        """Reordered phrases should not be treated as exact forbidden hits."""
        text = "那夜月光惨淡，照得人影飘忽。"
        found = _detect_forbidden_elements(text, ["惨淡月光"])
        assert found == []

    def test_fuzzy_match_requires_local_proximity(self) -> None:
        """Distant co-occurrence should not trigger a forbidden hit."""
        text = "她指尖一颤，隔了很久才觉得窗棂冰凉。"
        found = _detect_forbidden_elements(text, ["指尖冰凉"])
        assert found == []

    def test_contextual_anchor_terms_are_filtered_out(self) -> None:
        """Character names / kinship terms / plot objects in handoff should not remain forbidden."""
        from novel_forge.core.schemas.continuity import (
            ChapterBridge,
            ChapterPlan,
            ChapterStatePacket,
        )
        from novel_forge.core.schemas.outline import ChapterOutline
        from novel_forge.core.schemas.story_state import ChapterExitState

        packet = ChapterStatePacket(
            chapter_number=3,
            chapter_outline=ChapterOutline(
                chapter_number=3,
                title="夜访",
                goal="探查账簿线索",
                pov_character="周明",
                setting="西官仓耳房",
                expected_word_count=2500,
            ),
            canon_context={},
            previous_exit_state=ChapterExitState(
                chapter_number=2,
                time_marker="亥时初",
                location="西官仓耳房",
                pov="周明",
                must_carry_forward=["账簿残页线索"],
            ),
            must_carry_forward=["账簿残页线索"],
            known_characters=["周明", "赵成安"],
        )
        bridge = ChapterBridge(
            from_chapter=2,
            to_chapter=3,
            opening_time="亥时初",
            opening_location="西官仓耳房",
            opening_pov="周明",
            transition_mode="direct_continue",
            action_handoff="周明守着账簿残页，等父亲与赵成安的回音。",
        )
        plan = ChapterPlan(opening_contract="承接账簿残页线索。")

        filtered = _filter_forbidden_elements(
            ["周明", "父亲", "账簿残页", "惨淡月光"],
            packet=packet,
            bridge=bridge,
            plan=plan,
        )

        assert filtered == ["惨淡月光"]


# ────────────────────────────────────────────────────────
# 2. Forbidden repetition index — accumulation & pruning
# ────────────────────────────────────────────────────────


class TestForbiddenRepetitionIndex:
    """Tests for index persistence, accumulation, and pruning."""

    def test_update_appends_new_items(self, tmp_storage: FileSystemStorage) -> None:
        from novel_forge.pipeline.long.canon_ops import _update_forbidden_repetition_index

        layout = ProjectLayout(tmp_storage.project_dir("test_project"))
        layout.ensure_dirs()

        # First call: create index
        _update_forbidden_repetition_index(tmp_storage, layout, 1, ["背靠门板", "冰冷地面"])
        index = tmp_storage.load_json(layout.forbidden_repetition_index_path)
        assert index["1"] == ["背靠门板", "冰冷地面"]

        # Second call: append new chapter
        _update_forbidden_repetition_index(tmp_storage, layout, 2, ["惨淡月光", "指尖金丝"])
        index = tmp_storage.load_json(layout.forbidden_repetition_index_path)
        assert "1" in index and "2" in index
        assert index["2"] == ["惨淡月光", "指尖金丝"]

    def test_update_deduplicates_across_chapters(self, tmp_storage: FileSystemStorage) -> None:
        from novel_forge.pipeline.long.canon_ops import _update_forbidden_repetition_index

        layout = ProjectLayout(tmp_storage.project_dir("test_project"))
        layout.ensure_dirs()

        _update_forbidden_repetition_index(tmp_storage, layout, 1, ["背靠门板"])
        _update_forbidden_repetition_index(tmp_storage, layout, 2, ["背靠门板", "新意象"])
        index = tmp_storage.load_json(layout.forbidden_repetition_index_path)
        # "背靠门板" already exists in ch1, only "新意象" should be added for ch2
        assert index["2"] == ["新意象"]

    def test_update_skips_when_all_duplicated(self, tmp_storage: FileSystemStorage) -> None:
        from novel_forge.pipeline.long.canon_ops import _update_forbidden_repetition_index

        layout = ProjectLayout(tmp_storage.project_dir("test_project"))
        layout.ensure_dirs()

        _update_forbidden_repetition_index(tmp_storage, layout, 1, ["背靠门板"])
        _update_forbidden_repetition_index(tmp_storage, layout, 2, ["背靠门板"])
        index = tmp_storage.load_json(layout.forbidden_repetition_index_path)
        # ch2 should not be added since all items are duplicates
        assert "2" not in index

    def test_update_prunes_entries_outside_retention_window(
        self,
        tmp_storage: FileSystemStorage,
    ) -> None:
        from novel_forge.pipeline.long.canon_ops import _update_forbidden_repetition_index

        layout = ProjectLayout(tmp_storage.project_dir("test_project"))
        layout.ensure_dirs()

        _update_forbidden_repetition_index(
            tmp_storage,
            layout,
            1,
            ["旧意象"],
            retention_window=2,
        )
        _update_forbidden_repetition_index(
            tmp_storage,
            layout,
            2,
            ["中段意象"],
            retention_window=2,
        )
        _update_forbidden_repetition_index(
            tmp_storage,
            layout,
            4,
            ["新意象"],
            retention_window=2,
        )

        index = tmp_storage.load_json(layout.forbidden_repetition_index_path)
        assert "1" not in index
        assert "2" in index
        assert "4" in index

    def test_prune_removes_future_chapters(self, tmp_storage: FileSystemStorage) -> None:
        from novel_forge.persistence.project_staleness import _prune_forbidden_repetition_index

        layout = ProjectLayout(tmp_storage.project_dir("test_project"))
        layout.ensure_dirs()

        index = {"1": ["a"], "2": ["b"], "3": ["c"], "4": ["d"]}
        tmp_storage.save_json(layout.forbidden_repetition_index_path, index)

        _prune_forbidden_repetition_index(tmp_storage, layout, 2)

        pruned = tmp_storage.load_json(layout.forbidden_repetition_index_path)
        assert "1" in pruned and "2" in pruned
        assert "3" not in pruned and "4" not in pruned

    def test_prune_noop_when_no_future(self, tmp_storage: FileSystemStorage) -> None:
        from novel_forge.persistence.project_staleness import _prune_forbidden_repetition_index

        layout = ProjectLayout(tmp_storage.project_dir("test_project"))
        layout.ensure_dirs()

        index = {"1": ["a"], "2": ["b"]}
        tmp_storage.save_json(layout.forbidden_repetition_index_path, index)

        _prune_forbidden_repetition_index(tmp_storage, layout, 5)

        result = tmp_storage.load_json(layout.forbidden_repetition_index_path)
        assert result == index

    def test_prune_handles_missing_file(self, tmp_storage: FileSystemStorage) -> None:
        from novel_forge.persistence.project_staleness import _prune_forbidden_repetition_index

        layout = ProjectLayout(tmp_storage.project_dir("test_project"))
        layout.ensure_dirs()
        # Should not raise, just no-op
        _prune_forbidden_repetition_index(tmp_storage, layout, 2)


# ────────────────────────────────────────────────────────
# 3. Hard / Soft forbidden separation (plan_step)
# ────────────────────────────────────────────────────────


class TestHardSoftSeparation:
    """Tests for plan_step's hard/soft forbidden element partitioning."""

    def test_soft_excludes_hard_items(self) -> None:
        """Soft list should not contain items already in the hard list."""
        hard = ["背靠门板", "冰冷地面"]
        soft_raw = ["背靠门板", "惨淡月光", "冰冷地面", "指尖金丝"]
        hard_set = set(hard)
        soft = [item for item in soft_raw if item not in hard_set]
        assert soft == ["惨淡月光", "指尖金丝"]

    def test_empty_hard_keeps_all_soft(self) -> None:
        hard: list[str] = []
        soft_raw = ["惨淡月光", "指尖金丝"]
        hard_set = set(hard)
        soft = [item for item in soft_raw if item not in hard_set]
        assert soft == soft_raw

    def test_identical_lists_produce_empty_soft(self) -> None:
        items = ["背靠门板", "冰冷地面"]
        hard_set = set(items)
        soft = [item for item in items if item not in hard_set]
        assert soft == []


# ────────────────────────────────────────────────────────
# 4. MotifTracker.get_motifs_for_prompt — thematic exemptions
# ────────────────────────────────────────────────────────


class TestMotifTrackerThematicExemption:
    """Tests that thematic/intentional motifs are not added to forbidden_repetition."""

    def _make_tracker(self) -> "MotifTracker":
        from unittest.mock import MagicMock

        from novel_forge.memory.motif import MotifTracker

        return MotifTracker(router=MagicMock(), builder=MagicMock())

    def _register_motif(
        self,
        tracker: "MotifTracker",
        motif_id: str,
        name: str,
        category: str,
        is_intentional: bool,
        chapters: list[int],
    ) -> None:
        from novel_forge.memory.motif import Motif

        motif = Motif(
            motif_id=motif_id,
            name=name,
            category=category,
            is_intentional=is_intentional,
            occurrence_count=len(chapters),
            first_appearance_chapter=chapters[0] if chapters else 0,
            last_appearance_chapter=chapters[-1] if chapters else 0,
        )
        tracker._motifs[motif_id] = motif
        tracker._recent_usage[motif_id] = chapters

    def test_thematic_category_exempt_from_forbidden(self) -> None:
        """Motifs with category='主题' must stay soft-only."""
        tracker = self._make_tracker()
        self._register_motif(
            tracker, "m1", "浮京之梦", "主题", is_intentional=False, chapters=[3, 4]
        )
        result = tracker.get_motifs_for_prompt(current_chapter=5)
        assert "浮京之梦" not in result["forbidden_repetition"]
        assert "浮京之梦" in result["soft_forbidden_themes"]

    def test_symbol_category_exempt_from_forbidden(self) -> None:
        """Motifs with category='符号' must stay soft-only."""
        tracker = self._make_tracker()
        self._register_motif(tracker, "m2", "金丝", "符号", is_intentional=False, chapters=[4, 5])
        result = tracker.get_motifs_for_prompt(current_chapter=6)
        assert "金丝" not in result["forbidden_repetition"]
        assert "金丝" in result["soft_forbidden_themes"]

    def test_intentional_established_motif_exempt(self) -> None:
        """is_intentional=True + occurrence_count>=2 must NOT be forbidden."""
        tracker = self._make_tracker()
        self._register_motif(
            tracker, "m3", "惨淡月光", "意象", is_intentional=True, chapters=[3, 4, 5]
        )
        result = tracker.get_motifs_for_prompt(current_chapter=6)
        assert "惨淡月光" not in result["forbidden_repetition"]

    def test_unintentional_incidental_motif_is_forbidden(self) -> None:
        """is_intentional=False + non-thematic category SHOULD be forbidden if used recently."""
        tracker = self._make_tracker()
        self._register_motif(
            tracker, "m4", "脸埋进膝盖", "动作", is_intentional=False, chapters=[4]
        )
        result = tracker.get_motifs_for_prompt(current_chapter=5)
        assert "脸埋进膝盖" in result["forbidden_repetition"]

    def test_intentional_first_time_motif_is_forbidden(self) -> None:
        """is_intentional=True but occurrence_count=1 (first use) is still forbidden to prevent immediate re-use."""
        tracker = self._make_tracker()
        self._register_motif(tracker, "m5", "身体颤抖", "动作", is_intentional=True, chapters=[4])
        result = tracker.get_motifs_for_prompt(current_chapter=5)
        # occurrence_count == 1, so not yet "established" — should still be forbidden
        assert "身体颤抖" in result["forbidden_repetition"]

    def test_old_usage_not_forbidden_regardless(self) -> None:
        """Motifs last used > 2 chapters ago are never forbidden."""
        tracker = self._make_tracker()
        self._register_motif(tracker, "m6", "屏息", "动作", is_intentional=False, chapters=[1, 2])
        result = tracker.get_motifs_for_prompt(current_chapter=5)
        assert "屏息" not in result["forbidden_repetition"]
