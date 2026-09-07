"""Tests for retrieval-only narrative evidence contracts and Zvec facade."""

from __future__ import annotations

from pathlib import Path

from novel_forge.memory.narrative_evidence import (
    NarrativeEvidenceIndex,
    narrative_state_evidence_cards,
)
from novel_forge.narrative_state.evidence_contracts import RetrievalEvidenceCard


class _EmbeddingProvider:
    def __init__(self, signature: str = "embedding-a") -> None:
        self.signature = signature
        self.calls = 0

    @property
    def embedding_signature(self) -> str:
        return self.signature

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        # A deterministic tiny embedding suitable for in-memory retrieval tests.
        return [
            [
                float(sum(ord(char) for char in text) % 17 + 1),
                float(len(text) % 13 + 1),
                float(sum(ord(char) for char in set(text)) % 19 + 1),
            ]
            for text in texts
        ]


async def test_evidence_index_returns_only_visible_cards_and_hides_scores(tmp_path: Path) -> None:
    index = NarrativeEvidenceIndex(
        root=tmp_path / "evidence",
        embedding_provider=_EmbeddingProvider(),
        backend="in_memory",
    )
    await index.upsert_cards(
        [
            RetrievalEvidenceCard(
                card_id="past",
                kind="event",
                source_ref="chapter_002",
                excerpt="沈砚在雨夜交出铜印。",
                chapter_number=2,
                authority="accepted",
                canon_revision="r1",
            ),
            RetrievalEvidenceCard(
                card_id="future",
                kind="event",
                source_ref="chapter_099",
                excerpt="终局才揭示铜印真正用途。",
                chapter_number=99,
                authority="accepted",
                canon_revision="r1",
            ),
        ]
    )

    pack = await index.build_pack(
        purpose="chapter_planning",
        query="铜印的过去事件",
        canon_revision="r1",
        max_visible_chapter=8,
        candidate_limit=8,
        evidence_token_budget=2400,
    )

    assert [card.card_id for card in pack.evidence_cards] == ["past"]
    assert not hasattr(pack.evidence_cards[0], "score")
    assert pack.candidate_limit == 8
    assert pack.estimated_evidence_tokens <= pack.evidence_token_budget


async def test_time_filter_is_applied_before_ranking_and_token_budget_is_reported(
    tmp_path: Path,
) -> None:
    index = NarrativeEvidenceIndex(
        root=tmp_path / "evidence",
        embedding_provider=_EmbeddingProvider(),
        backend="in_memory",
    )
    cards = [
        RetrievalEvidenceCard(
            card_id="visible",
            kind="accepted_state",
            source_ref="chapter_003",
            excerpt="旧城门的通行凭验仍由顾青岚保管。" * 3,
            chapter_number=3,
            authority="accepted",
        )
    ]
    cards.extend(
        RetrievalEvidenceCard(
            card_id=f"future-{index}",
            kind="accepted_state",
            source_ref=f"chapter_{90 + index}",
            excerpt="旧城门通行凭验的终局用途。",
            chapter_number=90 + index,
            authority="accepted",
        )
        for index in range(20)
    )
    await index.upsert_cards(cards)

    pack = await index.build_pack(
        purpose="chapter_planning",
        query="旧城门通行凭验",
        canon_revision="r3",
        max_visible_chapter=8,
        candidate_limit=3,
        evidence_token_budget=160,
        kinds={"accepted_state"},
    )

    assert [card.card_id for card in pack.evidence_cards] == ["visible"]
    assert pack.retrieved_candidate_count == 1
    assert pack.estimated_evidence_tokens <= 160
    assert pack.has_more_evidence is False


async def test_evidence_budget_reports_omitted_ranked_cards(tmp_path: Path) -> None:
    index = NarrativeEvidenceIndex(
        root=tmp_path / "evidence",
        embedding_provider=_EmbeddingProvider(),
        backend="in_memory",
    )
    await index.upsert_cards(
        [
            RetrievalEvidenceCard(
                card_id=f"history-{item}",
                kind="event",
                source_ref=f"chapter_{item:03d}",
                excerpt=(f"历史事件-{item}-" + "证据" * 80),
                chapter_number=item,
            )
            for item in range(1, 5)
        ]
    )
    pack = await index.build_pack(
        purpose="chapter_planning",
        query="历史事件",
        canon_revision="r4",
        max_visible_chapter=10,
        candidate_limit=4,
        evidence_token_budget=256,
    )

    assert pack.has_more_evidence is True
    assert pack.omitted_candidate_count_lower_bound > 0
    assert pack.estimated_evidence_tokens <= pack.evidence_token_budget


async def test_evidence_index_only_reembeds_changed_cards(tmp_path: Path) -> None:
    provider = _EmbeddingProvider()
    index = NarrativeEvidenceIndex(
        root=tmp_path / "evidence",
        embedding_provider=provider,
        backend="in_memory",
    )
    original = RetrievalEvidenceCard(
        card_id="entity_1",
        kind="entity",
        source_ref="registry:entity_1",
        excerpt="顾青岚，别名青岚，医师。",
        authority="accepted",
        canon_revision="r1",
    )
    first = await index.upsert_cards([original])
    second = await index.upsert_cards([original])
    revised = original.model_copy(update={"excerpt": "顾青岚，医师，曾在北境行医。"})
    third = await index.upsert_cards([revised])

    assert first.updated_cards == 1
    assert second.updated_cards == 0
    assert third.updated_cards == 1


async def test_evidence_index_rebuilds_when_embedding_signature_changes(tmp_path: Path) -> None:
    provider = _EmbeddingProvider()
    index = NarrativeEvidenceIndex(
        root=tmp_path / "evidence",
        embedding_provider=provider,
        backend="in_memory",
    )
    await index.upsert_cards(
        [
            RetrievalEvidenceCard(
                card_id="entity_1",
                kind="entity",
                source_ref="registry:entity_1",
                excerpt="顾青岚，医师。",
                authority="accepted",
            )
        ]
    )
    provider.signature = "embedding-b"

    pack = await index.build_pack(
        purpose="chapter_review",
        query="顾青岚",
        canon_revision="r2",
        max_visible_chapter=3,
    )

    assert [card.card_id for card in pack.evidence_cards] == ["entity_1"]
    manifest = (tmp_path / "evidence" / "evidence_cards.json").read_text(encoding="utf-8")
    assert "embedding-b" in manifest


async def test_pack_identity_changes_when_card_content_changes(tmp_path: Path) -> None:
    index = NarrativeEvidenceIndex(
        root=tmp_path / "evidence",
        embedding_provider=_EmbeddingProvider(),
        backend="in_memory",
    )
    original = RetrievalEvidenceCard(
        card_id="state_1",
        kind="accepted_state",
        source_ref="ledger:state_1",
        excerpt="顾青岚仍在北境。",
        chapter_number=2,
        authority="accepted",
    )
    await index.upsert_cards([original])
    first = await index.build_pack(
        purpose="review",
        query="顾青岚",
        canon_revision="r1",
        max_visible_chapter=3,
    )
    await index.upsert_cards(
        [original.model_copy(update={"excerpt": "顾青岚已经离开北境。"})]
    )
    second = await index.build_pack(
        purpose="review",
        query="顾青岚",
        canon_revision="r1",
        max_visible_chapter=3,
    )

    assert first.pack_id != second.pack_id


async def test_authoritative_sync_prunes_superseded_state_cards(tmp_path: Path) -> None:
    index = NarrativeEvidenceIndex(
        root=tmp_path / "evidence",
        embedding_provider=_EmbeddingProvider(),
        backend="in_memory",
    )
    first = RetrievalEvidenceCard(
        card_id="state:first",
        kind="accepted_state",
        source_ref="ledger:first",
        excerpt="第一条状态。",
        chapter_number=1,
        authority="accepted",
    )
    second = RetrievalEvidenceCard(
        card_id="state:second",
        kind="accepted_state",
        source_ref="ledger:second",
        excerpt="第二条状态。",
        chapter_number=2,
        authority="accepted",
    )
    await index.sync_cards([first, second], replace_kinds={"accepted_state"})
    physical_ids = list(getattr(index._store, "_vectors", {}))
    assert physical_ids
    assert all(
        physical_id.startswith("card_")
        and ":" not in physical_id
        and len(physical_id) <= 64
        for physical_id in physical_ids
    )
    stats = await index.sync_cards([second], replace_kinds={"accepted_state"})

    pack = await index.build_pack(
        purpose="review",
        query="状态",
        canon_revision="r2",
        max_visible_chapter=3,
    )

    assert stats.removed_cards == 1
    assert [card.card_id for card in pack.evidence_cards] == ["state:second"]


def test_state_projection_preserves_llm_authority_and_source_evidence() -> None:
    cards, _ = narrative_state_evidence_cards(
        entity_registry={
            "entities": [
                {
                    "entity_id": "char_qinglan",
                    "name": "顾青岚",
                    "entity_type": "character",
                    "aliases": ["青岚"],
                }
            ]
        },
        ledger_entries=[
            {
                "entry_id": "entry_1",
                "chapter_number": 3,
                "summary": "顾青岚离开北境。",
                "state_update": {
                    "entity_id": "char_qinglan",
                    "state_path": "characters.char_qinglan.location",
                    "value": "中州",
                },
                "source_text_hash": "hash-3",
                "evidence_status": "active",
                "evidence": [{"quote": "她越过界碑，再没有回头。", "found": True}],
            }
        ],
    )

    entity_card = next(card for card in cards if card.kind == "entity")
    state_card = next(card for card in cards if card.kind == "accepted_state")
    assert entity_card.canonical_name == "顾青岚"
    assert state_card.entity_ids == ["char_qinglan"]
    assert state_card.authority == "accepted"
    assert "她越过界碑" in state_card.excerpt
