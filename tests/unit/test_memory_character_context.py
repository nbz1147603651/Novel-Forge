from __future__ import annotations

from types import SimpleNamespace

from novel_forge.memory.integration import MemoryContext


class _FakeEpisodicMemory:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def search_by_semantic(self, **kwargs: object) -> list[SimpleNamespace]:
        self.calls.append(dict(kwargs))
        return [
            SimpleNamespace(
                chapter_number=3,
                event_summary="林远亲眼看见纸灰落进水里。",
                scene_index=1,
                relevance_score=0.83,
                characters_involved=["林远", "周岚"],
                timestamp_in_story="夜",
            )
        ]


class _FakeEntityKnowledgeService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_current(self, character: str) -> dict[str, object]:
        self.calls.append(character)
        return {
            "entity_id": "char_linyuan",
            "name": character,
            "status": "active",
            "last_seen_chapter": "4",
            "attributes": {"location": "钟楼"},
        }


class _FakeRelationshipQueryService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_relationships(self, entity_id: str) -> list[dict[str, object]]:
        self.calls.append(entity_id)
        return [
            {
                "relationship_id": "rel_mentor",
                "source_entity_id": entity_id,
                "target_entity_id": "char_mentor",
                "relation_type": "mentor",
                "label": "师徒",
                "last_shift_chapter": 4,
                "shift_summary": "守夜人留下线索",
            }
        ]


class _AsyncCharacterSummaryService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def get_character_summary_for_context(
        self,
        *,
        character_id: str,
        current_chapter: int | None,
    ) -> str:
        self.calls.append(
            {"character_id": character_id, "current_chapter": current_chapter}
        )
        return f"{character_id}只知道钟楼线索。"


async def test_character_context_scopes_episodic_and_motifs() -> None:
    episodic = _FakeEpisodicMemory()
    ctx = MemoryContext()
    ctx._episodic_memory = episodic
    ctx._motif_tracker = SimpleNamespace(
        motifs={
            "m1": SimpleNamespace(
                motif_id="m1",
                name="纸灰",
                category="symbol",
                associated_characters=["林远"],
                last_appearance_chapter=3,
                thematic_meaning="旧线索回流",
                retired=False,
            ),
            "m2": SimpleNamespace(
                motif_id="m2",
                name="冷光",
                category="image",
                associated_characters=["周岚"],
                last_appearance_chapter=4,
                thematic_meaning="旁线压力",
                retired=False,
            ),
        }
    )

    result = await ctx.get_character_context("林远", current_chapter=5, query="纸灰")

    assert episodic.calls[0]["characters"] == ["林远"]
    assert episodic.calls[0]["chapter_range"] == (1, 4)
    assert result["episodic_context"][0]["event_summary"] == "林远亲眼看见纸灰落进水里。"
    motifs = result["motif_context"]["associated_motifs"]
    assert [item["motif_id"] for item in motifs] == ["m1"]


async def test_character_context_does_not_fall_back_to_global_summary() -> None:
    ctx = MemoryContext()
    ctx._summary_service = SimpleNamespace(
        get_summary_for_context=lambda **_: "全局摘要不应进入角色上下文。"
    )

    result = await ctx.get_character_context("林远", current_chapter=5, include_summaries=True)

    assert "summary_context" not in result
    assert result["summary_context_scope"] == "unavailable_character_filter"


async def test_character_context_uses_entity_id_for_relationship_lookup() -> None:
    entity_service = _FakeEntityKnowledgeService()
    relationship_service = _FakeRelationshipQueryService()
    ctx = MemoryContext()
    ctx._entity_knowledge_service = entity_service
    ctx._relationship_query_service = relationship_service

    result = await ctx.get_character_context(
        " 林远 ",
        current_chapter=5,
        include_episodic=False,
        include_motifs=False,
    )

    assert entity_service.calls == ["林远"]
    assert relationship_service.calls == ["char_linyuan"]
    assert result["entity_projection"] == {
        "entity_id": "char_linyuan",
        "name": "林远",
        "status": "active",
        "last_seen_chapter": 4,
        "attributes": {"location": "钟楼"},
    }
    assert result["relationship_projection"][0]["relationship_id"] == "rel_mentor"


async def test_character_context_accepts_async_character_summary() -> None:
    summary_service = _AsyncCharacterSummaryService()
    ctx = MemoryContext()
    ctx._summary_service = summary_service

    result = await ctx.get_character_context(
        "林远",
        current_chapter=6,
        include_episodic=False,
        include_motifs=False,
        include_summaries=True,
    )

    assert summary_service.calls == [{"character_id": "林远", "current_chapter": 6}]
    assert result["summary_context"] == "林远只知道钟楼线索。"
