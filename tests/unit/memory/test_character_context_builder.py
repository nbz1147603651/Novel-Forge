from __future__ import annotations

from types import SimpleNamespace

from novel_forge.memory.character_context_builder import build_character_memory_context


class _EntityService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_current(self, character: str) -> dict[str, object]:
        self.calls.append(character)
        return {
            "entity_id": "char_linyuan",
            "name": character,
            "status": "active",
            "last_seen_chapter": "5",
            "attributes": {"location": "钟楼"},
        }


class _RelationshipService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_relationships(self, lookup_key: str) -> list[dict[str, object]]:
        self.calls.append(lookup_key)
        return [
            {
                "relationship_id": "rel_watch",
                "source_entity_id": lookup_key,
                "target_entity_id": "char_guard",
                "label": "线索托付",
                "last_shift_chapter": 5,
                "shift_summary": "守夜人交出怀表",
            }
        ]


class _EpisodicMemory:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def search_by_semantic(self, **kwargs: object) -> list[SimpleNamespace]:
        self.calls.append(dict(kwargs))
        return [
            SimpleNamespace(
                chapter_number=4,
                event_summary="林远接过怀表。",
                scene_index=2,
                relevance_score=0.91,
                characters_involved=["林远"],
                timestamp_in_story="夜",
            )
        ]


class _SummaryService:
    async def get_character_summary_for_context(
        self,
        *,
        character_id: str,
        current_chapter: int | None,
    ) -> str:
        return f"{character_id}@{current_chapter}"


async def test_character_context_builder_coordinates_projection_and_retrieval() -> None:
    entity_service = _EntityService()
    relationship_service = _RelationshipService()
    episodic = _EpisodicMemory()

    result = await build_character_memory_context(
        " 林远 ",
        entity_knowledge_service=entity_service,
        relationship_query_service=relationship_service,
        episodic_memory=episodic,
        motif_tracker=SimpleNamespace(motifs={}),
        summary_service=_SummaryService(),
        current_chapter=7,
        query="怀表",
        include_summaries=True,
    )

    assert entity_service.calls == ["林远"]
    assert relationship_service.calls == ["char_linyuan"]
    assert episodic.calls == [
        {
            "query": "怀表",
            "chapter_range": (1, 6),
            "top_k": 5,
            "min_relevance": 0.5,
            "characters": ["林远"],
        }
    ]
    assert result["entity_projection"]["last_seen_chapter"] == 5
    assert result["relationship_projection"][0]["relationship_id"] == "rel_watch"
    assert result["episodic_context"][0]["event_summary"] == "林远接过怀表。"
    assert result["summary_context"] == "林远@7"


async def test_character_context_builder_blank_character_returns_empty_defaults() -> None:
    result = await build_character_memory_context(
        "  ",
        entity_knowledge_service=_EntityService(),
        relationship_query_service=_RelationshipService(),
        episodic_memory=_EpisodicMemory(),
    )

    assert result == {
        "character_id": "",
        "episodic_context": [],
        "motif_context": {"associated_motifs": []},
    }
