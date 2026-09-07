"""Tests for relationship tracker overview fallbacks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_forge.story_kernel.relationship_tracker import build_relationship_overview_sync
from novel_forge.story_kernel.schemas import Relationship
from novel_forge.story_kernel.store import CanonStore


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_relationship_overview_uses_current_canon_when_no_snapshots(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    store = CanonStore(project_dir)
    state = store.init("proj")
    from novel_forge.story_kernel.schemas import Entity
    state.entities.append(
        Entity(entity_id="char_风伏京", name="风伏京", entity_type="character")
    )
    state.entities.append(
        Entity(entity_id="char_罗浮", name="罗浮", entity_type="character")
    )
    state.relationships.append(
        Relationship(
            relationship_id="rel_风伏京__罗浮",
            source_entity_id="char_风伏京",
            target_entity_id="char_罗浮",
            label="盟友",
            trust=0.78,
            tension=0.22,
            shift_summary="并肩应对宫变",
            last_shift_chapter=5,
        )
    )
    store.save(state, snapshot=False)

    overview = build_relationship_overview_sync(project_dir)

    assert overview.total_relationships == 1
    timeline = overview.timelines[0]
    assert timeline.character_a == "风伏京"
    assert timeline.character_b == "罗浮"
    assert timeline.current_trust == 0.78
    assert timeline.current_tension == 0.22


def test_relationship_overview_falls_back_to_character_bible(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    store = CanonStore(project_dir)
    store.init("proj")

    _write_json(
        project_dir / "character_bible.json",
        {
            "characters": [
                {
                    "name": "风伏京",
                    "role": "protagonist",
                    "relationships": {"罗浮": "师徒亦友"},
                },
                {
                    "name": "罗浮",
                    "role": "supporting",
                    "relationships": {"风伏京": "亦师亦友"},
                },
            ]
        },
    )

    overview = build_relationship_overview_sync(project_dir)

    assert overview.total_relationships == 1
    timeline = overview.timelines[0]
    assert {timeline.character_a, timeline.character_b} == {"风伏京", "罗浮"}
    assert timeline.current_status


def test_relationship_overview_reuses_valid_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "proj"
    store = CanonStore(project_dir)
    state = store.init("proj")
    state.current_chapter = 1
    from novel_forge.story_kernel.schemas import Entity
    state.entities.append(
        Entity(entity_id="char_风伏京", name="风伏京", entity_type="character")
    )
    state.entities.append(
        Entity(entity_id="char_罗浮", name="罗浮", entity_type="character")
    )
    state.relationships.append(
        Relationship(
            relationship_id="rel_风伏京__罗浮",
            source_entity_id="char_风伏京",
            target_entity_id="char_罗浮",
            label="盟友",
            trust=0.78,
            tension=0.22,
            last_shift_chapter=1,
        )
    )
    store.save(state, snapshot=True)

    overview = build_relationship_overview_sync(project_dir)
    assert overview.total_relationships == 1
    assert (project_dir / "story_kernel" / "relationship_overview_cache.json").exists()

    def _fail_load_snapshot(self: CanonStore, chapter: int):
        raise AssertionError("cache should avoid snapshot reload")

    monkeypatch.setattr(CanonStore, "load_snapshot", _fail_load_snapshot)

    cached = build_relationship_overview_sync(project_dir)
    assert cached.total_relationships == 1
    assert cached.timelines[0].character_a == "风伏京"


def test_canon_store_prunes_old_snapshots(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    store = CanonStore(project_dir)
    state = store.init("proj")
    for chapter in range(1, 6):
        state.current_chapter = chapter
        store.save(state, snapshot=True)

    deleted = store.prune_snapshots(keep_recent=2, keep_chapters={2})

    assert deleted == [1, 3]
    assert store.list_snapshots() == [2, 4, 5]
