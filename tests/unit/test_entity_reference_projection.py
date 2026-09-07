from __future__ import annotations

from types import SimpleNamespace

from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.entity_reference import (
    compact_entity_reference_graph,
    normalize_prompt_entity_reference_graph,
)
from novel_forge.pipeline.long.services.constraints.constraint_router import build_stage_cards


def test_chapter_entity_projection_preserves_all_scoped_identity_links(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("长篇"))
    layout.ensure_dirs()
    identities = [
        {
            "source_id": "char_main",
            "target_id": f"identity_{index}",
            "link_type": "former_identity_of",
            "time_layer": f"前史-{index}",
            "description": f"第 {index} 个已登记身份层",
        }
        for index in range(20)
    ]
    storage.save_json(
        layout.root / "narrative_state" / "entity_graph.json",
        {
            "entities": [
                {"entity_id": "char_main", "name": "玄昱", "entity_type": "character"},
                *[
                    {
                        "entity_id": f"identity_{index}",
                        "name": f"身份{index}",
                        "entity_type": "concept",
                    }
                    for index in range(20)
                ],
                {"entity_id": "item_key", "name": "魂玉", "entity_type": "item"},
                {"entity_id": "loc_future", "name": "终局祭坛", "entity_type": "location"},
            ],
            "entity_links": [
                *identities,
                {
                    "source_id": "char_main",
                    "target_id": "item_key",
                    "link_type": "owned_by",
                    "description": "本章相关物件",
                },
                {
                    "source_id": "char_main",
                    "target_id": "loc_future",
                    "link_type": "located_in",
                    "description": "未来章节地点",
                },
            ],
        },
    )
    outline = SimpleNamespace(
        chapter_number=8,
        title="魂玉回声",
        goal="核验魂玉",
        setting="账房",
        pov_character="玄昱",
        involved_characters=["玄昱"],
    )

    graph = compact_entity_reference_graph(
        layout,
        chapter_outline=outline,
        relevant_entities=[
            {"entity_id": "char_main", "canonical_name": "玄昱"},
            {"entity_id": "item_key", "canonical_name": "魂玉"},
        ],
    )

    assert len(graph["identity_links"]) == 20
    assert [item["target"] for item in graph["identity_links"]] == [
        f"身份{index}" for index in range(20)
    ]
    assert graph["context_links"] == [
        {
            "source": "玄昱",
            "source_type": "character",
            "target": "魂玉",
            "target_type": "item",
            "link_type": "owned_by",
            "description": "本章相关物件",
        }
    ]
    assert "终局祭坛" not in str(graph)


def test_stage_card_keeps_entity_time_layer_without_second_truncation() -> None:
    graph = normalize_prompt_entity_reference_graph(
        {
            "identity_links": [
                {
                    "source": "玄昱",
                    "target": f"旧名{index}",
                    "link_type": "alias_of",
                    "time_layer": f"阶段{index}",
                }
                for index in range(18)
            ]
        }
    )

    cards = build_stage_cards(
        stage="draft",
        packet=SimpleNamespace(chapter_contract={}),
        chapter_outline=SimpleNamespace(
            chapter_number=3,
            title="旧名",
            goal="身份试探",
            pov_character="玄昱",
            involved_characters=["玄昱"],
        ),
        canon_context={"entity_reference_graph": graph},
    )

    links = cards["state"]["entity_reference_graph"]["identity_links"]
    assert len(links) == 18
    assert links[-1]["time_layer"] == "阶段17"
