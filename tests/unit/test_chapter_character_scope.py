"""Tests for evidence-backed chapter character scoping."""

from __future__ import annotations

from types import SimpleNamespace

from novel_forge.core.schemas.bible import CharacterBible, CharacterProfile
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.pipeline.long.services.context.context_helpers import (
    resolve_chapter_character_names,
    select_character_profiles,
)


def _bible() -> CharacterBible:
    return CharacterBible(
        characters=[
            CharacterProfile(name="沈岸", role="protagonist"),
            CharacterProfile(name="林小满", role="supporting"),
            CharacterProfile(name="陈半仙", role="supporting"),
            CharacterProfile(name="江野", role="supporting"),
            CharacterProfile(name="苏晚", role="supporting", status="retired"),
        ]
    )


def test_resolve_chapter_characters_requires_outline_evidence() -> None:
    outline = ChapterOutline(
        chapter_number=2,
        title="入梦",
        goal="沈岸进入梦境，林小满在现实端监测神经信号。",
        beats_summary=["林小满发现读数异常并提醒沈岸撤离。"],
        main_plot_points=["沈岸决定继续深入。"],
        involved_characters=["沈岸", "林小满", "陈半仙", "江野", "苏晚"],
        pov_character="沈岸",
    )

    assert resolve_chapter_character_names(_bible(), outline) == ["沈岸", "林小满"]


def test_resolve_chapter_characters_keeps_explicit_retired_projection() -> None:
    outline = ChapterOutline(
        chapter_number=3,
        title="残影",
        goal="沈岸在梦境里看见苏晚的记忆投影。",
        beats_summary=["苏晚留下半句警告后消失。"],
        involved_characters=["沈岸", "苏晚"],
        pov_character="沈岸",
    )

    assert resolve_chapter_character_names(_bible(), outline) == ["沈岸", "苏晚"]


def test_resolve_chapter_characters_prefers_longest_overlapping_name() -> None:
    bible = CharacterBible(
        characters=[
            CharacterProfile(name="沈岸", role="protagonist"),
            CharacterProfile(name="小满", role="supporting"),
            CharacterProfile(name="林小满", role="supporting"),
        ]
    )
    outline = ChapterOutline(
        chapter_number=2,
        goal="沈岸请林小满检查设备。",
        pov_character="沈岸",
    )

    assert resolve_chapter_character_names(bible, outline) == ["沈岸", "林小满"]


def test_profile_projection_does_not_pad_or_hard_truncate() -> None:
    canon_context = SimpleNamespace(characters={}, active_relationships=[])
    selected = select_character_profiles(
        _bible(),
        canon_context,
        "沈岸",
        max_profiles=1,
        source_field_chars=200,
        max_relationships=3,
        relationship_source_chars=100,
        involved_characters=["沈岸", "林小满", "陈半仙"],
    )

    assert [profile["name"] for profile in selected] == ["沈岸", "林小满", "陈半仙"]
    assert all("appearance" in profile for profile in selected)


def test_profile_projection_falls_back_to_pov_only() -> None:
    canon_context = SimpleNamespace(
        characters={"沈岸": {}, "林小满": {}, "陈半仙": {}},
        active_relationships=[],
    )
    selected = select_character_profiles(
        _bible(),
        canon_context,
        "沈岸",
        max_profiles=16,
        source_field_chars=200,
        max_relationships=3,
        relationship_source_chars=100,
        involved_characters=None,
    )

    assert [profile["name"] for profile in selected] == ["沈岸"]
