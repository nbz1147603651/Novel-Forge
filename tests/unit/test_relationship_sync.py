"""Tests for relationship_sync.py — additional edge cases and integration."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.core.schemas.bible import CharacterBible, CharacterProfile
from novel_forge.pipeline.long.stages.character_intro import _sync_character_relationships_to_canon
from novel_forge.story_kernel.entity_projection import stable_entity_id
from novel_forge.story_kernel.relationship_sync import (
    _make_pair_id,
    sync_relationships_from_bible_to_kernel,
)
from novel_forge.story_kernel.schemas import StoryKernel


def _make_kernel(project_id: str = "test") -> StoryKernel:
    return StoryKernel(project_id=project_id)


def _make_bible(characters: list[CharacterProfile]) -> CharacterBible:
    return CharacterBible(characters=characters)


def _find_rel(kernel: StoryKernel, name_a: str, name_b: str):
    src_id = stable_entity_id("character", name_a)
    tgt_id = stable_entity_id("character", name_b)
    pair = tuple(sorted([src_id, tgt_id]))
    for rel in kernel.relationships:
        if tuple(sorted([rel.source_entity_id, rel.target_entity_id])) == pair:
            return rel
    return None


class TestMakePairIdEdgeCases:
    def test_unicode_sorting(self) -> None:
        pair = _make_pair_id("赵", "钱")
        assert pair == "赵__钱"

    def test_single_char_names(self) -> None:
        pair = _make_pair_id("A", "B")
        assert pair == "A__B"

    def test_mixed_language_names(self) -> None:
        pair = _make_pair_id("Alice", "林远")
        assert pair == "Alice__林远"

    def test_pair_id_contains_double_underscore(self) -> None:
        pair = _make_pair_id("X", "Y")
        assert "__" in pair
        assert pair.count("__") == 1


class TestSyncEdgeCases:
    def test_character_with_empty_name_skipped(self) -> None:
        bible = _make_bible([
            CharacterProfile(name="", relationships={"苏晴": "朋友"}),
        ])
        kernel = _make_kernel()

        added = sync_relationships_from_bible_to_kernel(bible, kernel)

        assert added == 0
        assert len(kernel.relationships) == 0

    def test_whitespace_only_target_skipped(self) -> None:
        bible = _make_bible([
            CharacterProfile(name="林远", relationships={"   ": "朋友"}),
        ])
        kernel = _make_kernel()

        added = sync_relationships_from_bible_to_kernel(bible, kernel)

        assert added == 0

    def test_whitespace_only_description_skipped(self) -> None:
        bible = _make_bible([
            CharacterProfile(name="林远", relationships={"苏晴": "   "}),
            CharacterProfile(name="苏晴"),
        ])
        kernel = _make_kernel()

        added = sync_relationships_from_bible_to_kernel(bible, kernel)

        assert added == 0

    def test_notes_field_set_on_synced_relationships(self) -> None:
        bible = _make_bible([
            CharacterProfile(name="林远", relationships={"苏晴": "朋友"}),
            CharacterProfile(name="苏晴"),
        ])
        kernel = _make_kernel()

        sync_relationships_from_bible_to_kernel(bible, kernel)

        rel = _find_rel(kernel, "林远", "苏晴")
        assert rel is not None
        assert rel.notes == "从角色档案同步"

    def test_default_trust_tension_values(self) -> None:
        bible = _make_bible([
            CharacterProfile(name="林远", relationships={"苏晴": "朋友"}),
            CharacterProfile(name="苏晴"),
        ])
        kernel = _make_kernel()

        sync_relationships_from_bible_to_kernel(bible, kernel)

        rel = _find_rel(kernel, "林远", "苏晴")
        assert rel is not None
        assert rel.trust == 0.5
        assert rel.tension == 0.5

    def test_large_scale_sync(self) -> None:
        characters = []
        for i in range(10):
            rels = {f"角色{j}": f"关系{j}" for j in range(i + 1, 10)}
            characters.append(CharacterProfile(name=f"角色{i}", relationships=rels))
        bible = _make_bible(characters)
        kernel = _make_kernel()

        added = sync_relationships_from_bible_to_kernel(bible, kernel)

        expected = 10 * 9 // 2
        assert added == expected
        assert len(kernel.relationships) == expected

    def test_non_roster_relationship_target_still_synced(self) -> None:
        bible = _make_bible([
            CharacterProfile(name="林远", relationships={"金镯": "前世记忆触发物"}),
        ])
        kernel = _make_kernel()

        added = sync_relationships_from_bible_to_kernel(bible, kernel)

        assert added == 0


@pytest.mark.asyncio
async def test_character_intro_sync_helper_persists_new_relationships(tmp_path) -> None:
    from novel_forge.core.config import Settings
    from novel_forge.pipeline.long.services.context.story_kernel_context import story_kernel_db_path
    from novel_forge.story_kernel.store import StoryKernelStore

    bible = _make_bible([
        CharacterProfile(name="林远", relationships={"苏晴": "朋友"}),
        CharacterProfile(name="苏晴"),
    ])
    events: list[tuple[str, object]] = []
    layout = SimpleNamespace(root=tmp_path)
    bundle = SimpleNamespace(
        layout=layout,
        character_bible=bible,
        project_id="sync_demo",
    )
    settings = Settings(_env_file=None)

    store = StoryKernelStore(story_kernel_db_path(settings, layout))
    await store.init_db()
    await store.create_kernel("sync_demo")
    await store.close()

    added = await _sync_character_relationships_to_canon(
        bundle=bundle,
        settings=settings,
        on_step=lambda step, payload: events.append((step, payload)),
    )

    assert added == 1
    assert events == [("character_relationship_sync", {"added": 1})]
