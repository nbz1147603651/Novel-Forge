from __future__ import annotations

import asyncio
import json
from pathlib import Path

from novel_forge.app_service.character_artifacts import (
    add_character,
    retire_character,
    write_character_bible,
    write_relationship_edge,
)
from novel_forge.core.domain.character_identity import stable_character_id
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import scoped_stale_chapters
from novel_forge.story_kernel.schemas import Entity, Relationship, StoryKernel
from novel_forge.story_kernel.store import StoryKernelStore


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


async def _load_story_kernel(project_dir: Path) -> StoryKernel:
    store = StoryKernelStore(project_dir / "story_kernel.db", wal_mode=False)
    try:
        await store.init_db()
        return await store.load_kernel(project_dir.name)
    finally:
        await store.close()


async def _save_story_kernel(project_dir: Path, kernel: StoryKernel) -> None:
    store = StoryKernelStore(project_dir / "story_kernel.db", wal_mode=False)
    try:
        await store.init_db()
        await store.save_kernel(kernel)
    finally:
        await store.close()


def _seed_project(tmp_path: Path) -> Path:
    project_dir = tmp_path / "demo"
    _write_json(
        project_dir / "character_bible.json",
        {
            "characters": [
                {
                    "name": "林舟",
                    "role": "protagonist",
                    "gender": "女",
                    "relationships": {},
                },
                {
                    "name": "沈砚",
                    "role": "deuteragonist",
                    "gender": "男",
                    "relationships": {},
                },
            ]
        },
    )
    return project_dir


def _write_final_chapters(project_dir: Path, *chapter_numbers: int) -> ProjectLayout:
    layout = ProjectLayout(project_dir)
    layout.ensure_dirs()
    for chapter_number in chapter_numbers:
        layout.chapter_path(chapter_number).write_text(
            f"第{chapter_number}章正文",
            encoding="utf-8",
        )
    return layout


def test_write_relationship_edge_syncs_matrix_system_and_entity_graph(tmp_path: Path) -> None:
    project_dir = _seed_project(tmp_path)

    result = write_relationship_edge(
        project_dir,
        "林舟",
        "沈砚",
        "alliance",
        "共同追查旧案，互相提供关键掩护。",
    )

    assert result.character_count == 2
    assert result.relationship_count == 1
    bible = _read_json(project_dir / "character_bible.json")
    characters = {item["name"]: item for item in bible["characters"]}  # type: ignore[index]
    assert characters["林舟"]["relationships"]["沈砚"] == "共同追查旧案，互相提供关键掩护。"
    assert characters["沈砚"]["relationships"]["林舟"] == "共同追查旧案，互相提供关键掩护。"

    matrix = _read_json(project_dir / "states" / "init_v2" / "character_relationship_matrix.json")
    edge = matrix["relationship_matrix"][0]  # type: ignore[index]
    assert edge["relation_type"] == "alliance"
    assert edge["source"] == "manual_ui"

    character_system = _read_json(project_dir / "states" / "init_v2" / "character_system.json")
    system_edge = character_system["relationship_edges"][0]  # type: ignore[index]
    assert system_edge["relation_type"] == "alliance"
    assert system_edge["source_name"] == "林舟"
    assert (project_dir / "narrative_state" / "entity_registry.json").exists()
    assert (project_dir / "narrative_state" / "entity_graph.json").exists()


def test_write_relationship_edge_syncs_story_kernel(tmp_path: Path) -> None:
    project_dir = _seed_project(tmp_path)

    result = write_relationship_edge(
        project_dir,
        "林舟",
        "沈砚",
        "alliance",
        "共同追查旧案，互相提供关键掩护。",
    )

    assert result.story_kernel_synced is True
    kernel = asyncio.run(_load_story_kernel(project_dir))
    entities_by_name = {entity.name: entity for entity in kernel.entities}
    assert set(entities_by_name) == {"林舟", "沈砚"}
    assert entities_by_name["林舟"].attributes["role"] == "protagonist"

    assert len(kernel.relationships) == 1
    relationship = kernel.relationships[0]
    assert relationship.source_entity_id == entities_by_name["林舟"].entity_id
    assert relationship.target_entity_id == entities_by_name["沈砚"].entity_id
    assert relationship.relation_type == "ally"
    assert relationship.label == "共同追查旧案，互相提供关键掩护。"


def test_relationship_edit_does_not_mark_all_generated_chapters_stale(tmp_path: Path) -> None:
    project_dir = _seed_project(tmp_path)
    layout = _write_final_chapters(project_dir, 1, 2)
    asyncio.run(
        _save_story_kernel(project_dir, StoryKernel(project_id=project_dir.name, current_chapter=2))
    )

    result = write_relationship_edge(
        project_dir,
        "林舟",
        "沈砚",
        "alliance",
        "共同追查旧案，互相提供关键掩护。",
    )

    assert result.invalidated_chapters == ()
    storage = FileSystemStorage(project_dir.parent)
    assert scoped_stale_chapters(storage, layout) == set()
    status = _read_json(layout.states_dir / "upstream_revision_status" / "character_bible.json")
    assert status["scope"] == "forward_only"
    assert layout.chapter_path(1).exists()
    assert layout.chapter_path(2).exists()


async def test_story_kernel_sync_is_safe_inside_running_event_loop(tmp_path: Path) -> None:
    project_dir = _seed_project(tmp_path)

    result = write_relationship_edge(
        project_dir,
        "林舟",
        "沈砚",
        "alliance",
        "共同追查旧案，互相提供关键掩护。",
    )

    assert result.story_kernel_synced is True
    kernel = await _load_story_kernel(project_dir)
    assert {entity.name for entity in kernel.entities} == {"林舟", "沈砚"}


def test_renamed_existing_character_marks_whole_book_stale(tmp_path: Path) -> None:
    project_dir = _seed_project(tmp_path)
    layout = _write_final_chapters(project_dir, 1, 2)
    asyncio.run(
        _save_story_kernel(project_dir, StoryKernel(project_id=project_dir.name, current_chapter=2))
    )

    write_character_bible(
        project_dir,
        {
            "characters": [
                {
                    "character_id": stable_character_id("林舟"),
                    "name": "林舟改",
                    "role": "protagonist",
                    "gender": "女",
                    "relationships": {},
                },
                {
                    "name": "沈砚",
                    "role": "deuteragonist",
                    "gender": "男",
                    "relationships": {},
                },
            ]
        },
    )

    storage = FileSystemStorage(project_dir.parent)
    assert scoped_stale_chapters(storage, layout) == {1, 2}
    status = _read_json(layout.states_dir / "upstream_revision_status" / "character_bible.json")
    assert status["scope"] == "whole_book"


def test_existing_relationship_type_flip_marks_whole_book_stale(tmp_path: Path) -> None:
    project_dir = _seed_project(tmp_path)
    layout = _write_final_chapters(project_dir, 1, 2)
    asyncio.run(
        _save_story_kernel(project_dir, StoryKernel(project_id=project_dir.name, current_chapter=2))
    )
    _write_json(
        layout.states_dir / "init_v2" / "character_relationship_matrix.json",
        {
            "relationship_generation_phase": "manual_ui",
            "relationship_matrix": [
                {
                    "character_a": "林舟",
                    "character_b": "沈砚",
                    "relation_type": "alliance",
                    "description": "旧同盟关系。",
                    "confidence": 1.0,
                    "source": "manual_ui",
                }
            ],
        },
    )

    write_relationship_edge(
        project_dir,
        "林舟",
        "沈砚",
        "antagonism",
        "冲突升级，双方已经互相阻挠。",
    )

    storage = FileSystemStorage(project_dir.parent)
    assert scoped_stale_chapters(storage, layout) == {1, 2}
    status = _read_json(layout.states_dir / "upstream_revision_status" / "character_bible.json")
    assert status["scope"] == "whole_book"


def test_removed_character_keeps_whole_book_stale_scope(tmp_path: Path) -> None:
    project_dir = _seed_project(tmp_path)
    layout = _write_final_chapters(project_dir, 1, 2)
    asyncio.run(
        _save_story_kernel(project_dir, StoryKernel(project_id=project_dir.name, current_chapter=2))
    )

    write_character_bible(
        project_dir,
        {
            "characters": [
                {
                    "name": "林舟",
                    "role": "protagonist",
                    "gender": "女",
                    "relationships": {},
                }
            ]
        },
    )

    storage = FileSystemStorage(project_dir.parent)
    assert scoped_stale_chapters(storage, layout) == {1, 2}
    status = _read_json(layout.states_dir / "upstream_revision_status" / "character_bible.json")
    assert status["scope"] == "whole_book"


def test_story_kernel_sync_preserves_relationship_metrics(tmp_path: Path) -> None:
    project_dir = _seed_project(tmp_path)
    lin_id = stable_character_id("林舟")
    shen_id = stable_character_id("沈砚")
    asyncio.run(
        _save_story_kernel(
            project_dir,
            StoryKernel(
                project_id=project_dir.name,
                entities=[
                    Entity(
                        entity_id=lin_id,
                        name="林舟",
                        entity_type="character",
                        attributes={"location": "旧城"},
                    ),
                    Entity(entity_id=shen_id, name="沈砚", entity_type="character"),
                ],
                relationships=[
                    Relationship(
                        relationship_id="rel-existing",
                        source_entity_id=lin_id,
                        target_entity_id=shen_id,
                        relation_type="friend",
                        label="旧关系",
                        trust=0.2,
                        tension=0.8,
                        dependency=0.4,
                        established_chapter=0,
                        last_shift_chapter=3,
                    )
                ],
            ),
        )
    )

    write_relationship_edge(
        project_dir,
        "林舟",
        "沈砚",
        "alliance",
        "共同追查旧案，互相提供关键掩护。",
    )

    kernel = asyncio.run(_load_story_kernel(project_dir))
    lin = next(entity for entity in kernel.entities if entity.entity_id == lin_id)
    assert lin.attributes["location"] == "旧城"
    assert lin.attributes["role"] == "protagonist"

    relationship = kernel.relationships[0]
    assert relationship.relationship_id == "rel-existing"
    assert relationship.relation_type == "ally"
    assert relationship.label == "共同追查旧案，互相提供关键掩护。"
    assert relationship.trust == 0.2
    assert relationship.tension == 0.8
    assert relationship.dependency == 0.4
    assert relationship.last_shift_chapter == 3


def test_add_character_and_retire_character_preserve_projectable_artifacts(tmp_path: Path) -> None:
    project_dir = _seed_project(tmp_path)

    added = add_character(
        project_dir,
        {
            "name": "顾桥",
            "role": "supporting",
            "status": "active",
            "relationships": {"林舟": "提供外部资源。"},
        },
    )
    assert added.character_count == 3

    retired = retire_character(project_dir, "顾桥")
    assert retired.character_count == 3
    bible = _read_json(project_dir / "character_bible.json")
    characters = {item["name"]: item for item in bible["characters"]}  # type: ignore[index]
    assert characters["顾桥"]["status"] == "retired"
    assert (project_dir / "states" / "init_v2" / "character_system.json").exists()
    assert (project_dir / "narrative_state" / "entity_graph.json").exists()
