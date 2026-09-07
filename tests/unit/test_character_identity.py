"""Regression tests for character identity normalization."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from novel_forge.core.domain.character_identity import (
    dedupe_candidate_character_names,
    normalize_character_role,
    resolve_existing_character_name,
    stable_character_id,
)
from novel_forge.core.schemas.bible import CharacterBible, CharacterProfile
from novel_forge.core.schemas.canon import CreativeReport, NewCharacterDetail
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.stages.character_intro import auto_register_from_creative_report
from novel_forge.prompts.builder import PromptBuilder


def test_parenthetical_stage_name_resolves_to_existing_character() -> None:
    assert resolve_existing_character_name("周芷若（狱中）", {"周芷若"}) == "周芷若"
    assert dedupe_candidate_character_names(
        ["周芷若（狱中）", "前世女工转世"],
        existing_names={"周芷若"},
    ) == ["前世女工转世"]


def test_parenthetical_stage_candidates_collapse_to_base_name() -> None:
    assert dedupe_candidate_character_names(
        ["苏曼华（祖母）", "苏曼华（前世）"],
        existing_names=set(),
    ) == ["苏曼华"]


def test_role_prose_is_normalized_to_canonical_bucket() -> None:
    assert normalize_character_role("minor + 外滩附近老字号面包店店主") == "minor"
    assert normalize_character_role("major + 陈伯庸后人代表") == "supporting"
    assert normalize_character_role("反派棋手") == "antagonist"


def test_character_profile_generates_stable_id_and_keeps_identity_fields() -> None:
    profile = CharacterProfile(
        name="顾墨白",
        role="supporting",
        social_status="摄影师",
        abilities="暗房修复与影像取证",
    )

    assert profile.character_id == stable_character_id("顾墨白")
    assert profile.social_status == "摄影师"
    assert profile.abilities == "暗房修复与影像取证"


def test_auto_register_skips_parenthetical_existing_character(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    bundle = SimpleNamespace(
        layout=layout,
        character_bible=CharacterBible(
            characters=[CharacterProfile(name="周芷若", role="antagonist")]
        ),
    )
    report = CreativeReport(
        new_characters=[
            NewCharacterDetail(
                name="周芷若（狱中）",
                first_appearance_chapter=46,
                role_in_story="major + 狱中阶段",
                should_add_to_bible=True,
            )
        ]
    )

    result = asyncio.run(
        auto_register_from_creative_report(
            router=cast(ModelRouter, object()),
            builder=cast(PromptBuilder, object()),
            storage=FileSystemStorage(tmp_path),
            settings=SimpleNamespace(long_auto_introduce_max_new_characters=2),
            bundle=cast(Any, bundle),
            chapter_number=46,
            chapter_text="周芷若在狱中直面真相。",
            creative_report=report,
            trace=cast(Any, object()),
        )
    )

    assert result == []
    assert [c.name for c in bundle.character_bible.characters] == ["周芷若"]


def test_auto_register_skips_upstream_rejected_character_label(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    bundle = SimpleNamespace(
        layout=layout,
        character_bible=CharacterBible(characters=[CharacterProfile(name="程砚秋")]),
    )
    report = CreativeReport(
        new_characters=[
            NewCharacterDetail(
                name="前世女工转世",
                first_appearance_chapter=38,
                role_in_story="minor + 线索身份",
                importance="incidental",
                should_add_to_bible=True,
            )
        ]
    )

    result = asyncio.run(
        auto_register_from_creative_report(
            router=cast(ModelRouter, object()),
            builder=cast(PromptBuilder, object()),
            storage=FileSystemStorage(tmp_path),
            settings=SimpleNamespace(long_auto_introduce_max_new_characters=2),
            bundle=cast(Any, bundle),
            chapter_number=38,
            chapter_text="程砚秋确认前世女工转世的线索指向苏曼华。",
            creative_report=report,
            trace=cast(Any, object()),
        )
    )

    assert result == []
    assert [c.name for c in bundle.character_bible.characters] == ["程砚秋"]
