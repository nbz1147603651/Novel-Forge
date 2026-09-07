"""Batch 0 · Early-Constraint hardening unit tests.

Covers the four P0-0 items:
1. locations field hardening + targeted backfill merge
2. ambient-sound seed chain (StoryBible → sound design + script context)
3. upstream revision awareness for voice stages
4. shot-language seed consumption in film shot planning
"""

from __future__ import annotations

from typing import Any

import pytest

from novel_forge.film.schemas import (
    ProductionBible,
    ProductionLocation,
    Screenplay,
    ScreenplayScene,
)
from novel_forge.film.source_projection import FilmSourceProjector
from novel_forge.pipeline.long.story_bible_ops import (
    find_missing_location_fields,
    merge_location_backfill,
)
from novel_forge.tts.schemas import DubbingScript, DubbingSegment, SegmentType
from novel_forge.tts.script_stage_context import (
    ContextProjectionLimits,
    ScriptContextStage,
    ScriptStageContext,
)
from novel_forge.tts.sound_design_extraction import (
    _extract_sound_design_rules,
    _infer_soundscapes_from_contexts,
    _seed_soundscapes_from_locations,
    _upstream_revision_matches,
)
from novel_forge.workspace.tts_ops.execution_shared import (
    _file_revision,
    _project_location_acoustics,
)

# ─── 1. locations hardening ────────────────────────────────────────────────────


def _complete_location() -> dict[str, Any]:
    return {
        "name": "祠堂",
        "location_id": "loc-ancestral-hall",
        "dramatic_function": "宗族对峙",
        "spatial_layout": "三进院落，中轴对称",
        "materials": ["青砖", "朽木"],
        "practical_lights": ["长明灯火"],
        "ambient_sound": ["烛火爆裂", "风铃"],
        "continuity_rules": ["香火方向恒定"],
    }


class TestLocationsFieldHardening:
    def test_complete_location_reports_no_missing(self) -> None:
        bible = {"locations": [_complete_location()]}
        assert find_missing_location_fields(bible) == []

    def test_missing_fields_are_reported_per_location(self) -> None:
        partial = _complete_location()
        partial.pop("ambient_sound")
        partial["materials"] = []
        report = find_missing_location_fields({"locations": [partial]})
        assert len(report) == 1
        assert report[0]["name"] == "祠堂"
        assert set(report[0]["missing"]) == {"ambient_sound", "materials"}

    def test_backfill_only_fills_blank_fields(self) -> None:
        partial = _complete_location()
        partial["ambient_sound"] = []
        bible = {"locations": [partial]}
        merged = merge_location_backfill(
            bible,
            [
                {
                    "name": "祠堂",
                    "location_id": "loc-ancestral-hall",
                    "ambient_sound": ["木鱼声"],
                    "spatial_layout": "不应覆盖已有内容",
                }
            ],
        )
        location = merged["locations"][0]
        assert location["ambient_sound"] == ["木鱼声"]
        assert location["spatial_layout"] == "三进院落，中轴对称"

    def test_backfill_unknown_location_is_ignored(self) -> None:
        bible = {"locations": [_complete_location()]}
        merged = merge_location_backfill(
            bible, [{"name": "不存在的场景", "ambient_sound": ["雨声"]}]
        )
        assert merged["locations"][0] == _complete_location()

    def test_old_project_without_new_fields_degrades(self) -> None:
        # Legacy location lacking shot_language_seed must not raise anywhere.
        legacy = {"locations": [{"name": "旧场景"}]}
        report = find_missing_location_fields(legacy)
        assert report and report[0]["missing"]


# ─── 2. ambient-sound seed chain ───────────────────────────────────────────────


def _script_with_contexts(contexts: list[str]) -> DubbingScript:
    return DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=index,
                segment_type=SegmentType.NARRATION,
                text=f"正文{index}",
                scene_context=context,
            )
            for index, context in enumerate(contexts)
        ],
    )


class TestAmbientSoundSeedChain:
    def test_project_location_acoustics_filters_empty(self) -> None:
        bible = {
            "locations": [
                {"name": "祠堂", "ambient_sound": ["烛火", "风铃"]},
                {"name": "无声音场景"},
                {"name": "雨巷", "sound": "雨声"},
            ]
        }
        cards = _project_location_acoustics(bible)
        assert [card["name"] for card in cards] == ["祠堂", "雨巷"]
        assert cards[1]["ambient_sound"] == ["雨声"]

    def test_seed_soundscapes_applied_when_location_appears(self) -> None:
        script = _script_with_contexts(["祠堂内·夜", "祠堂内·夜", "街市"])
        cues, consumed = _seed_soundscapes_from_locations(
            script, [{"name": "祠堂", "ambient_sound": ["烛火爆裂", "风铃"]}]
        )
        assert consumed == {"祠堂"}
        assert len(cues) == 1
        assert cues[0].name == "祠堂环境底床"
        assert cues[0].start_segment_index == 0
        assert cues[0].end_segment_index == 1
        assert "来自故事圣经场景定义" in cues[0].description

    def test_inference_skips_seeded_contexts_and_fills_gaps(self) -> None:
        script = _script_with_contexts(["祠堂内·夜", "暴雨中的街道"])
        inferred = _infer_soundscapes_from_contexts(
            script, covered_location_names={"祠堂"}
        )
        names = [cue.name for cue in inferred]
        assert all("祠堂" not in name for name in names)
        assert any("雨" in name for name in names)

    def test_rules_path_prefers_seeds_over_inference(self) -> None:
        script = _script_with_contexts(["祠堂内·夜"])
        design = _extract_sound_design_rules(
            script,
            location_sound_seeds=[
                {"name": "祠堂", "ambient_sound": ["烛火爆裂"]}
            ],
        )
        assert any("祠堂环境底床" == cue.name for cue in design.soundscapes)

    def test_script_stage_context_projects_location_acoustics(self) -> None:
        context = ScriptStageContext(
            location_acoustics=(
                {"name": "祠堂", "ambient_sound": ["烛火", "风铃"]},
            ),
            chapter_number=1,
        )
        limits = ContextProjectionLimits(max_items=8, max_text_chars=200)
        cards = context.project(ScriptContextStage.SPOKEN_REWRITE, [], limits=limits)
        assert cards["location_acoustics"] == [
            {"name": "祠堂", "ambient_sound": ["烛火", "风铃"]}
        ]

    def test_script_stage_context_without_acoustics_keeps_legacy_shape(self) -> None:
        context = ScriptStageContext(chapter_number=1)
        limits = ContextProjectionLimits(max_items=8, max_text_chars=200)
        cards = context.project(ScriptContextStage.SPOKEN_REWRITE, [], limits=limits)
        assert "location_acoustics" not in cards


# ─── 3. upstream revision awareness ────────────────────────────────────────────


class TestUpstreamRevision:
    def test_missing_record_matches_legacy_projects(self) -> None:
        assert _upstream_revision_matches(None, {"story_bible": "abc"}) is True

    def test_drift_detected_when_revision_changes(self) -> None:
        recorded = {"story_bible": "old", "character_bible": "same"}
        assert (
            _upstream_revision_matches(recorded, {"story_bible": "new"}) is False
        )
        assert (
            _upstream_revision_matches(recorded, {"story_bible": "old"}) is True
        )

    def test_file_revision_stable_and_missing_safe(self, tmp_path: Any) -> None:
        path = tmp_path / "story_bible.json"
        path.write_text("{}", encoding="utf-8")
        first = _file_revision(path)
        assert first and len(first) == 16
        assert _file_revision(path) == first
        path.write_text('{"v":2}', encoding="utf-8")
        assert _file_revision(path) != first
        assert _file_revision(tmp_path / "missing.json") == ""


# ─── 4. shot-language seed ─────────────────────────────────────────────────────


def _bible_with_seed(seed: str) -> ProductionBible:
    return ProductionBible(
        project_id="p1",
        title="测试",
        locations=[
            ProductionLocation(
                location_id="loc-1",
                name="祠堂",
                shot_language_seed=seed,
            )
        ],
    )


def _screenplay() -> Screenplay:
    return Screenplay(
        title="测试",
        scenes=[
            ScreenplayScene(
                scene_id="sc-01",
                sequence_number=1,
                heading="内景·祠堂·夜",
                location_id="loc-1",
                objective="对峙",
            )
        ],
    )


class TestShotLanguageSeed:
    def test_seed_overrides_parsed_deterministically(self) -> None:
        overrides = FilmSourceProjector._shot_seed_overrides(
            "低机位、手持、35mm、实景光"
        )
        assert overrides["camera_angle"] == "slight_low"
        assert overrides["camera_motion"] == "subtle_handheld"
        assert overrides["lens_mm"] == 35
        assert overrides["lighting"] == "practical"

    def test_empty_seed_returns_no_overrides(self) -> None:
        assert FilmSourceProjector._shot_seed_overrides("") == {}

    def test_shots_consume_seed_and_fallback_without_it(self) -> None:
        seeded = FilmSourceProjector._shots(
            _screenplay(), _bible_with_seed("低机位、手持、35mm")
        )
        fallback = FilmSourceProjector._shots(_screenplay(), _bible_with_seed(""))
        assert len(seeded) == 4 and len(fallback) == 4
        for shot in seeded:
            assert shot.language.camera_angle == "slight_low"
            assert shot.language.camera_motion == "subtle_handheld"
            assert shot.language.lens_mm == 35
            assert "镜头语言偏好" in shot.prompt
        # Without a seed the generic patterns stay intact.
        assert fallback[0].language.shot_size == "wide"
        assert fallback[0].language.lens_mm == 28
        assert "镜头语言偏好" not in fallback[0].prompt

    def test_shot_size_diversity_preserved_when_seed_partial(self) -> None:
        seeded = FilmSourceProjector._shots(
            _screenplay(), _bible_with_seed("柔光")
        )
        sizes = [shot.language.shot_size for shot in seeded]
        assert sizes == ["wide", "medium", "close_up", "extreme_close_up"]
        assert all(shot.language.lighting == "soft" for shot in seeded)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
