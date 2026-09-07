"""Tests for runner factories that map Settings into runtime instances."""

from __future__ import annotations

import pytest

from novel_forge.core.config import Settings
from novel_forge.pipeline.chapter_runner import ChapterRunner
from novel_forge.pipeline.short_runner import ShortStoryRunner
from novel_forge.pipeline.steps.bridge_step import BridgeStep


def test_chapter_runner_from_settings_maps_long_form_configuration(
    router,
    builder,
    tmp_storage,
) -> None:
    settings = Settings(
        long_alignment_threshold=8.3,
        long_volume_auto_chapter_threshold=88,
        long_volume_auto_word_threshold=360000,
        long_default_chapters_per_volume=18,
        canon_context_max_recent_events=11,
        canon_context_max_characters=12,
        canon_context_max_foreshadowing=13,
        canon_context_max_world_facts=14,
        long_prompt_max_character_profiles=9,
        long_prompt_max_profile_field_chars=180,
        long_prompt_profile_source_field_chars=540,
        long_prompt_max_relationships_per_profile=5,
        long_plan_prev_report_max_deviations=6,
        long_plan_prev_report_max_new_characters=7,
        long_plan_prev_report_text_chars=190,
        long_plan_prev_report_source_chars=640,
        long_context_compress_enabled=False,
        long_context_compress_min_chars=333,
        long_context_compress_max_tokens=1234,
        long_chapter_compact_interval=2,
        long_chapter_compact_start_chapter=3,
        long_chapter_compact_stale_chapters=4,
        long_chapter_compact_outline_lookahead=5,
        long_chapter_compact_min_active_characters=6,
        long_chapter_compact_target_world_facts=10,
        long_chapter_compact_keep_recent_world_facts=8,
        long_chapter_compact_archive_resolved_foreshadowing_after=9,
    )

    runner = ChapterRunner.from_settings(router, builder, tmp_storage, settings)

    cfg = runner._config
    assert cfg.alignment_threshold == 8.3
    assert cfg.volume_auto_chapter_threshold == 88
    assert cfg.volume_auto_word_threshold == 360000
    assert cfg.default_chapters_per_volume == 18
    assert runner._retriever.max_recent_events == 11
    assert runner._retriever.max_characters == 12
    assert runner._retriever.max_active_foreshadowing == 13
    assert runner._retriever.max_world_facts == 14
    assert cfg.prompt_max_character_profiles == 9
    assert cfg.prompt_max_profile_field_chars == 180
    assert cfg.prompt_profile_source_field_chars == 540
    assert cfg.prompt_max_relationships_per_profile == 5
    assert cfg.plan_prev_report_max_deviations == 6
    assert cfg.plan_prev_report_max_new_characters == 7
    assert cfg.plan_prev_report_text_chars == 190
    assert cfg.plan_prev_report_source_chars == 640
    assert cfg.context_compress_enabled is False
    assert cfg.context_compress_min_chars == 333
    assert cfg.context_compress_max_tokens == 1234
    assert cfg.chapter_compact_interval == 2
    assert cfg.chapter_compact_start_chapter == 3
    assert cfg.chapter_compact_stale_chapters == 4
    assert cfg.chapter_compact_outline_lookahead == 5
    assert cfg.chapter_compact_min_active_characters == 6
    assert cfg.chapter_compact_target_world_facts == 10
    assert cfg.chapter_compact_keep_recent_world_facts == 8
    assert cfg.chapter_compact_archive_resolved_foreshadowing_after == 9


def test_short_story_runner_from_settings_uses_short_defaults(
    router,
    builder,
    tmp_storage,
) -> None:
    settings = Settings(short_max_edit_rounds=5)

    runner = ShortStoryRunner.from_settings(router, builder, tmp_storage, settings)

    assert runner._max_edit == 5


def test_short_story_runner_from_settings_allows_override(
    router,
    builder,
    tmp_storage,
) -> None:
    settings = Settings(short_max_edit_rounds=5)

    runner = ShortStoryRunner.from_settings(
        router,
        builder,
        tmp_storage,
        settings,
        max_edit_rounds=2,
    )

    assert runner._max_edit == 2


def test_pipeline_constructors_require_explicit_settings(
    router,
    builder,
    tmp_storage,
) -> None:
    with pytest.raises(TypeError):
        ShortStoryRunner(router, builder, tmp_storage)  # type: ignore[call-arg]

    with pytest.raises(TypeError):
        ChapterRunner(router, builder, tmp_storage)  # type: ignore[call-arg]

    with pytest.raises(TypeError):
        BridgeStep(router, builder)  # type: ignore[call-arg]
