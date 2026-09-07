"""Tests for relationship sync between CharacterBible and StoryKernel."""

from __future__ import annotations

from novel_forge.core.schemas.bible import CharacterBible, CharacterProfile
from novel_forge.story_kernel.entity_projection import stable_entity_id
from novel_forge.story_kernel.relationship_sync import (
    _make_pair_id,
    sync_relationships_from_bible_to_kernel,
)
from novel_forge.story_kernel.schemas import Relationship, StoryKernel


def _make_kernel(project_id: str = "test") -> StoryKernel:
    return StoryKernel(project_id=project_id)


def _make_bible(characters: list[CharacterProfile]) -> CharacterBible:
    return CharacterBible(characters=characters)


def _find_rel(kernel: StoryKernel, name_a: str, name_b: str) -> Relationship | None:
    src_id = stable_entity_id("character", name_a)
    tgt_id = stable_entity_id("character", name_b)
    pair = tuple(sorted([src_id, tgt_id]))
    for rel in kernel.relationships:
        if tuple(sorted([rel.source_entity_id, rel.target_entity_id])) == pair:
            return rel
    return None


class TestMakePairId:
    def test_sorted_order(self) -> None:
        assert _make_pair_id("赵云", "刘备") == "刘备__赵云"

    def test_same_order_both_directions(self) -> None:
        assert _make_pair_id("A", "B") == _make_pair_id("B", "A")

    def test_double_underscore_separator(self) -> None:
        pair_id = _make_pair_id("Alice", "Bob")
        assert "__" in pair_id
        assert pair_id == "Alice__Bob"


class TestSyncRelationshipsFromBibleToKernel:
    def test_basic_sync(self) -> None:
        bible = _make_bible([
            CharacterProfile(name="林远", relationships={"苏晴": "青梅竹马，互相信任"}),
            CharacterProfile(name="苏晴"),
        ])
        kernel = _make_kernel()

        added = sync_relationships_from_bible_to_kernel(bible, kernel)

        assert added == 1
        rel = _find_rel(kernel, "林远", "苏晴")
        assert rel is not None
        assert rel.trust == 0.5
        assert rel.tension == 0.5
        assert rel.label == "青梅竹马，互相信任"

    def test_skip_existing(self) -> None:
        bible = _make_bible([
            CharacterProfile(name="林远", relationships={"苏晴": "青梅竹马"}),
            CharacterProfile(name="苏晴"),
        ])
        kernel = _make_kernel()
        src_id = stable_entity_id("character", "林远")
        tgt_id = stable_entity_id("character", "苏晴")
        kernel.relationships.append(
            Relationship(
                relationship_id="rel_林远__苏晴",
                source_entity_id=src_id,
                target_entity_id=tgt_id,
                label="已有关系",
                trust=0.8,
                tension=0.2,
            )
        )

        added = sync_relationships_from_bible_to_kernel(bible, kernel)

        assert added == 0
        rel = _find_rel(kernel, "林远", "苏晴")
        assert rel is not None
        assert rel.trust == 0.8
        assert rel.tension == 0.2
        assert rel.label == "已有关系"

    def test_dedup_bidirectional(self) -> None:
        bible = _make_bible([
            CharacterProfile(name="林远", relationships={"苏晴": "朋友"}),
            CharacterProfile(name="苏晴", relationships={"林远": "好友"}),
        ])
        kernel = _make_kernel()

        added = sync_relationships_from_bible_to_kernel(bible, kernel)

        assert added == 1
        assert _find_rel(kernel, "林远", "苏晴") is not None

    def test_multiple_characters(self) -> None:
        bible = _make_bible([
            CharacterProfile(name="林远", relationships={"苏晴": "青梅竹马", "赵云": "战友"}),
            CharacterProfile(name="苏晴", relationships={"赵云": "对手"}),
            CharacterProfile(name="赵云"),
        ])
        kernel = _make_kernel()

        added = sync_relationships_from_bible_to_kernel(bible, kernel)

        assert added == 3
        assert _find_rel(kernel, "林远", "苏晴") is not None
        assert _find_rel(kernel, "林远", "赵云") is not None
        assert _find_rel(kernel, "苏晴", "赵云") is not None

    def test_empty_relationships(self) -> None:
        bible = _make_bible([CharacterProfile(name="林远", relationships={})])
        kernel = _make_kernel()

        added = sync_relationships_from_bible_to_kernel(bible, kernel)

        assert added == 0
        assert len(kernel.relationships) == 0

    def test_empty_description_skipped(self) -> None:
        bible = _make_bible([
            CharacterProfile(name="林远", relationships={"苏晴": ""}),
            CharacterProfile(name="苏晴"),
        ])
        kernel = _make_kernel()

        added = sync_relationships_from_bible_to_kernel(bible, kernel)

        assert added == 0

    def test_empty_target_name_skipped(self) -> None:
        bible = _make_bible([
            CharacterProfile(name="林远", relationships={"": "some description"}),
        ])
        kernel = _make_kernel()

        added = sync_relationships_from_bible_to_kernel(bible, kernel)

        assert added == 0

    def test_description_truncated_to_200_chars(self) -> None:
        long_desc = "A" * 300
        bible = _make_bible([
            CharacterProfile(name="林远", relationships={"苏晴": long_desc}),
            CharacterProfile(name="苏晴"),
        ])
        kernel = _make_kernel()

        sync_relationships_from_bible_to_kernel(bible, kernel)

        rel = _find_rel(kernel, "林远", "苏晴")
        assert rel is not None
        assert len(rel.label) == 200

    def test_preserves_partial_existing(self) -> None:
        bible = _make_bible([
            CharacterProfile(name="林远", relationships={"苏晴": "青梅竹马", "赵云": "战友"}),
            CharacterProfile(name="苏晴"),
            CharacterProfile(name="赵云"),
        ])
        kernel = _make_kernel()
        src_id = stable_entity_id("character", "林远")
        tgt_id = stable_entity_id("character", "苏晴")
        kernel.relationships.append(
            Relationship(
                relationship_id="rel_existing",
                source_entity_id=src_id,
                target_entity_id=tgt_id,
                label="existing",
                trust=0.9,
                tension=0.1,
            )
        )

        added = sync_relationships_from_bible_to_kernel(bible, kernel)

        assert added == 1
        existing = _find_rel(kernel, "林远", "苏晴")
        assert existing is not None
        assert existing.trust == 0.9
        assert _find_rel(kernel, "林远", "赵云") is not None

    def test_idempotent(self) -> None:
        bible = _make_bible([
            CharacterProfile(name="林远", relationships={"苏晴": "朋友"}),
            CharacterProfile(name="苏晴"),
        ])
        kernel = _make_kernel()

        added_first = sync_relationships_from_bible_to_kernel(bible, kernel)
        added_second = sync_relationships_from_bible_to_kernel(bible, kernel)

        assert added_first == 1
        assert added_second == 0
        assert len(kernel.relationships) == 1
