"""Tests for LLM-only entity reference adjudication during init coherence."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.core.schemas.init_coherence import CoherenceClaim
from novel_forge.narrative_state.entity_adjudication import adjudicate_entity_references
from novel_forge.narrative_state.evidence_contracts import (
    RetrievalEvidenceCard,
    RetrievalEvidencePack,
)
from novel_forge.pipeline.context_governance import estimate_json_tokens
from novel_forge.pipeline.long.services.init.init_entity_references import (
    _request_from_packs,
    adjudicate_init_claim_entity_references,
    build_entity_candidate_packs,
    entity_catalog_evidence_cards,
)


def _claim() -> CoherenceClaim:
    return CoherenceClaim.model_validate(
        {
            "claim_id": "outline_1_claim_1",
            "artifact": "outline",
            "source_path": "/chapters/0",
            "subject_ids": ["阿清"],
            "entity_mentions": ["阿清"],
            "claim_text": "阿清确认钥匙归属。",
            "evidence": "阿清将钥匙收起。",
            "cognitive_subjects": ["阿清"],
            "cognitive_object": "钥匙归属",
            "cognitive_level": "confirmed",
            "action_level": "internal",
            "reader_awareness": "full",
            "character_knowledge_coverage": {"阿清": "full"},
            "cognitive_chapter": 1,
            "public_reveal_chapter": None,
            "foreshadow_chapters": [],
        }
    )


class _EvidenceService:
    def __init__(self) -> None:
        self.cards: list[Any] = []

    async def index_cards(self, cards: list[Any]) -> object:
        raise AssertionError("fixed entity registry must not be persisted in the vector index")

    async def evidence_pack(self, **kwargs: Any) -> RetrievalEvidencePack:
        return RetrievalEvidencePack(
            pack_id="pack",
            purpose=str(kwargs["purpose"]),
            query=str(kwargs["query"]),
            canon_revision=str(kwargs["canon_revision"]),
            max_visible_chapter=0,
            evidence_cards=list(self.cards),
        )


async def test_init_claim_entities_are_resolved_only_from_llm_verdict() -> None:
    service = _EvidenceService()

    async def call(_task: Any, context: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        mention = context["mentions"][0]
        return {
            "decisions": [
                {
                    "mention": mention["mention"],
                    "verdict": "resolved",
                    "selected_entity_id": "char_qingyi",
                    "selected_canonical_name": "沈清漪",
                    "candidate_entity_ids": mention["candidate_entity_ids"],
                    "evidence_refs": ["entity_catalog/char_qingyi"],
                    "rationale": "证据卡列出该来源别称。",
                    "requires_independent_review": False,
                }
            ],
            "summary": "已裁决。",
        }

    ctx = SimpleNamespace(
        memory_context=SimpleNamespace(narrative_evidence_service=service),
        call_with_retry=call,
    )
    profile = {
        "entity_catalog": {
            "allowed_entities": [
                {
                    "entity_id": "char_qingyi",
                    "canonical_name": "沈清漪",
                    "entity_type": "character",
                    "aliases": ["阿清"],
                }
            ]
        }
    }

    resolved = await adjudicate_init_claim_entity_references(
        ctx, profile=profile, claims=[_claim()]
    )

    assert resolved[0].subject_ids == ["char_qingyi"]
    assert resolved[0].entity_mentions == ["沈清漪"]
    assert resolved[0].claim_text == "沈清漪确认钥匙归属。"
    assert resolved[0].evidence == "沈清漪将钥匙收起。"
    assert resolved[0].cognitive_subjects == ["沈清漪"]
    assert resolved[0].character_knowledge_coverage == {"沈清漪": "full"}
    assert resolved[0].metadata["raw_entity_references"]["subject_ids"] == ["阿清"]
    assert resolved[0].metadata["entity_adjudication_status"] == "adjudicated"
    assert resolved[0].metadata["entity_reference_revision"]


async def test_small_fixed_catalog_does_not_require_a_vector_service() -> None:
    async def call(_task: Any, context: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        mention = context["mentions"][0]
        return {
            "decisions": [
                {
                    "mention": mention["mention"],
                    "verdict": "resolved",
                    "selected_entity_id": "char_qingyi",
                    "selected_canonical_name": "沈清漪",
                    "candidate_entity_ids": mention["candidate_entity_ids"],
                    "evidence_refs": ["entity_catalog/char_qingyi"],
                    "rationale": "结构化目录中的显式别名证据。",
                    "requires_independent_review": False,
                }
            ],
            "summary": "已裁决。",
        }

    ctx = SimpleNamespace(memory_context=None, call_with_retry=call)
    profile = {
        "entity_catalog": {
            "allowed_entities": [
                {
                    "entity_id": "char_qingyi",
                    "canonical_name": "沈清漪",
                    "entity_type": "character",
                    "aliases": ["阿清"],
                }
            ]
        }
    }

    resolved = await adjudicate_init_claim_entity_references(
        ctx, profile=profile, claims=[_claim()]
    )

    assert resolved[0].subject_ids == ["char_qingyi"]


async def test_unresolved_entity_is_not_locally_mapped_from_alias() -> None:
    service = _EvidenceService()

    async def call(_task: Any, context: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        mention = context["mentions"][0]
        return {
            "decisions": [
                {
                    "mention": mention["mention"],
                    "verdict": "ambiguous",
                    "selected_entity_id": "",
                    "selected_canonical_name": "",
                    "candidate_entity_ids": mention["candidate_entity_ids"],
                    "evidence_refs": [],
                    "rationale": "不能区分。",
                    "requires_independent_review": False,
                }
            ],
            "summary": "保留歧义。",
        }

    ctx = SimpleNamespace(
        memory_context=SimpleNamespace(narrative_evidence_service=service),
        call_with_retry=call,
    )
    profile = {
        "entity_catalog": {
            "allowed_entities": [
                {
                    "entity_id": "char_qingyi",
                    "canonical_name": "沈清漪",
                    "entity_type": "character",
                    "aliases": ["阿清"],
                }
            ]
        }
    }

    unresolved = await adjudicate_init_claim_entity_references(
        ctx, profile=profile, claims=[_claim()]
    )

    assert unresolved[0].subject_ids == []
    assert unresolved[0].cognitive_subjects == []
    assert unresolved[0].character_knowledge_coverage == {}
    assert unresolved[0].metadata["raw_entity_references"]["subject_ids"] == ["阿清"]


def test_catalog_projects_aliases_as_evidence_not_lookup_table() -> None:
    cards, _ = entity_catalog_evidence_cards(
        {
            "allowed_entities": [
                {
                    "entity_id": "char_qingyi",
                    "canonical_name": "沈清漪",
                    "entity_type": "character",
                    "aliases": ["阿清"],
                }
            ]
        }
    )

    assert cards[0].entity_ids == ["char_qingyi"]
    assert "来源别称：阿清" in cards[0].excerpt


def test_shared_entity_evidence_budget_keeps_authority_and_reports_omissions() -> None:
    entity_card = entity_catalog_evidence_cards(
        {
            "allowed_entities": [
                {
                    "entity_id": "char_qingyi",
                    "canonical_name": "沈清漪",
                    "entity_type": "character",
                    "aliases": ["阿清"],
                }
            ]
        }
    )[0][0]
    history_card = RetrievalEvidenceCard(
        card_id="history_1",
        kind="claim",
        source_ref="claims/history_1",
        excerpt="历史叙事证据" * 100,
        entity_ids=["char_qingyi"],
    )
    source_pack = RetrievalEvidencePack(
        pack_id="source",
        purpose="test",
        query="阿清",
        evidence_cards=[entity_card, history_card],
    )
    authority_cost = estimate_json_tokens(entity_card.model_dump(mode="json"))

    mentions, shared_pack = _request_from_packs(
        ["阿清"],
        [source_pack],
        occurrences={"阿清": []},
        revision="revision-1",
        evidence_token_budget=authority_cost,
    )

    assert [card.card_id for card in shared_pack.evidence_cards] == [entity_card.card_id]
    assert shared_pack.estimated_evidence_tokens <= shared_pack.evidence_token_budget
    assert shared_pack.omitted_candidate_count_lower_bound == 1
    assert shared_pack.has_more_evidence is True
    assert mentions[0]["candidate_entity_ids"] == ["char_qingyi"]
    assert mentions[0]["candidate_evidence_refs"] == [entity_card.source_ref]


def test_entity_request_compacts_occurrences_but_preserves_field_boundaries() -> None:
    entity_card = entity_catalog_evidence_cards(
        {
            "allowed_entities": [
                {
                    "entity_id": "char_qingyi",
                    "canonical_name": "沈清漪",
                    "entity_type": "character",
                    "aliases": ["阿清"],
                }
            ]
        }
    )[0][0]
    source_pack = RetrievalEvidencePack(
        pack_id="source",
        purpose="test",
        query="阿清",
        evidence_cards=[entity_card],
    )
    fields = [
        "entity_mentions",
        "subject_ids",
        "cognitive_subjects",
        "character_knowledge_coverage",
    ]
    occurrences = [
        {"claim_id": f"claim_{field}_{index}", "field": field}
        for field in fields
        for index in range(5)
    ]

    mentions, _shared_pack = _request_from_packs(
        ["阿清"],
        [source_pack],
        occurrences={"阿清": occurrences},
        revision="revision-1",
        occurrence_limit=8,
    )

    request = mentions[0]
    assert request["claim_occurrence_count"] == 20
    assert request["claim_occurrences_truncated"] is True
    assert len(request["claim_occurrences"]) == 8
    assert {item["field"] for item in request["claim_occurrences"]} == set(fields)
    for field in fields:
        selected_ids = {
            item["claim_id"] for item in request["claim_occurrences"] if item["field"] == field
        }
        assert f"claim_{field}_0" in selected_ids
        assert f"claim_{field}_4" in selected_ids


async def test_resolved_entity_decision_is_reused_for_unchanged_evidence() -> None:
    calls = 0
    events: list[tuple[str, dict[str, Any]]] = []

    async def call(_task: Any, context: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        mention = context["mentions"][0]
        return {
            "decisions": [
                {
                    "mention": mention["mention"],
                    "verdict": "resolved",
                    "selected_entity_id": "char_qingyi",
                    "selected_canonical_name": "沈清漪",
                    "candidate_entity_ids": mention["candidate_entity_ids"],
                    "evidence_refs": ["entity_catalog/char_qingyi"],
                    "rationale": "结构化目录中的显式别名证据。",
                    "requires_independent_review": False,
                }
            ],
            "summary": "已裁决。",
        }

    ctx = SimpleNamespace(
        memory_context=None,
        call_with_retry=call,
        on_step=lambda step, data: events.append((step, data)),
    )
    profile = {
        "entity_catalog": {
            "allowed_entities": [
                {
                    "entity_id": "char_qingyi",
                    "canonical_name": "沈清漪",
                    "entity_type": "character",
                    "aliases": ["阿清"],
                }
            ]
        }
    }

    first = await adjudicate_init_claim_entity_references(ctx, profile=profile, claims=[_claim()])
    later_claim = _claim().model_copy(update={"claim_id": "outline_2_claim_1"})
    second = await adjudicate_init_claim_entity_references(
        ctx,
        profile=profile,
        claims=[later_claim],
        stage="outline_inheritance",
    )

    assert calls == 1
    assert first[0].subject_ids == second[0].subject_ids == ["char_qingyi"]
    cache_event = next(
        data for step, data in events if step == "init_claim_entity_adjudication_cache_hit"
    )
    assert cache_event["stage"] == "outline_inheritance"

    changed_profile = {
        "entity_catalog": {
            "allowed_entities": [
                *profile["entity_catalog"]["allowed_entities"],
                {
                    "entity_id": "char_new",
                    "canonical_name": "新角色",
                    "entity_type": "character",
                },
            ]
        }
    }
    await adjudicate_init_claim_entity_references(
        ctx,
        profile=changed_profile,
        claims=[_claim()],
    )
    assert calls == 2


async def test_entity_decisions_requiring_review_are_not_cached() -> None:
    calls = 0

    async def call(_task: Any, context: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        mention = context["mentions"][0]
        return {
            "decisions": [
                {
                    "mention": mention["mention"],
                    "verdict": "resolved",
                    "selected_entity_id": "char_qingyi",
                    "selected_canonical_name": "沈清漪",
                    "candidate_entity_ids": mention["candidate_entity_ids"],
                    "evidence_refs": ["entity_catalog/char_qingyi"],
                    "rationale": "身份变化需要独立复核。",
                    "requires_independent_review": True,
                }
            ],
            "summary": "已裁决。",
        }

    ctx = SimpleNamespace(memory_context=None, call_with_retry=call)
    profile = {
        "entity_catalog": {
            "allowed_entities": [
                {
                    "entity_id": "char_qingyi",
                    "canonical_name": "沈清漪",
                    "entity_type": "character",
                    "aliases": ["阿清"],
                }
            ]
        }
    }

    await adjudicate_init_claim_entity_references(ctx, profile=profile, claims=[_claim()])
    await adjudicate_init_claim_entity_references(ctx, profile=profile, claims=[_claim()])

    # Each pass performs a primary call and the required independent review.
    assert calls == 4


async def test_entity_adjudication_batches_run_with_bounded_parallelism() -> None:
    active = 0
    max_active = 0
    calls = 0

    async def call(_task: Any, context: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        nonlocal active, calls, max_active
        calls += 1
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {
            "decisions": [
                {
                    "mention": mention["mention"],
                    "verdict": "resolved",
                    "selected_entity_id": mention["candidate_entity_ids"][0],
                    "selected_canonical_name": mention["mention"],
                    "candidate_entity_ids": mention["candidate_entity_ids"],
                    "evidence_refs": mention["candidate_evidence_refs"],
                    "rationale": "结构化目录中的同名实体。",
                    "requires_independent_review": False,
                }
                for mention in context["mentions"]
            ],
            "summary": "已裁决。",
        }

    names = [f"角色{index}" for index in range(13)]
    claims = [
        _claim().model_copy(
            update={
                "claim_id": f"claim_{index}",
                "subject_ids": [name],
                "entity_mentions": [name],
                "cognitive_subjects": [name],
                "character_knowledge_coverage": {name: "full"},
            }
        )
        for index, name in enumerate(names)
    ]
    profile = {
        "entity_catalog": {
            "allowed_entities": [
                {
                    "entity_id": f"char_{index}",
                    "canonical_name": name,
                    "entity_type": "character",
                }
                for index, name in enumerate(names)
            ]
        }
    }
    ctx = SimpleNamespace(
        memory_context=None,
        call_with_retry=call,
        settings=SimpleNamespace(init_entity_reference_max_parallel=3),
    )

    resolved = await adjudicate_init_claim_entity_references(
        ctx,
        profile=profile,
        claims=claims,
    )

    assert len(resolved) == 13
    assert calls == 3
    assert 2 <= max_active <= 3


async def test_large_catalog_shortlists_near_spelling_for_llm_without_local_mapping() -> None:
    catalog = {
        "allowed_entities": [
            {
                "entity_id": f"location_{index}",
                "canonical_name": f"地点{index}",
                "entity_type": "location",
            }
            for index in range(30)
        ]
        + [
            {
                "entity_id": "char_shen_an",
                "canonical_name": "沈岸",
                "entity_type": "character",
            }
        ]
    }

    packs, _, _ = await build_entity_candidate_packs(
        SimpleNamespace(memory_context=None),
        entity_catalog=catalog,
        mentions=["沈潮"],
        purpose="test",
    )

    candidate_ids = {entity_id for card in packs[0].evidence_cards for entity_id in card.entity_ids}
    assert "char_shen_an" in candidate_ids
    assert len(candidate_ids) <= 4


async def test_adjudication_rejects_hallucinated_candidate_set() -> None:
    pack = RetrievalEvidencePack(
        pack_id="pack",
        purpose="entity_reference",
        query="阿清",
        evidence_cards=entity_catalog_evidence_cards(
            {
                "allowed_entities": [
                    {
                        "entity_id": "char_qingyi",
                        "canonical_name": "沈清漪",
                        "entity_type": "character",
                        "aliases": ["阿清"],
                    }
                ]
            }
        )[0],
    )

    async def call(_task: Any, _context: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        return {
            "decisions": [
                {
                    "mention": "阿清",
                    "verdict": "resolved",
                    "selected_entity_id": "char_invented",
                    "selected_canonical_name": "虚构实体",
                    "candidate_entity_ids": ["char_invented"],
                    "evidence_refs": [],
                    "rationale": "错误地发明候选。",
                    "requires_independent_review": False,
                }
            ],
            "summary": "invalid",
        }

    with pytest.raises(ValueError, match="echo the presented candidate ids"):
        await adjudicate_entity_references(
            call=call,
            purpose="test",
            mentions=[{"mention": "阿清", "candidate_entity_ids": ["char_qingyi"]}],
            evidence_pack=pack,
        )


async def test_entity_adjudication_retries_incomplete_semantic_batch() -> None:
    pack = RetrievalEvidencePack(
        pack_id="pack",
        purpose="entity_reference",
        query="阿清；陈默",
        evidence_cards=entity_catalog_evidence_cards(
            {
                "allowed_entities": [
                    {
                        "entity_id": "char_qingyi",
                        "canonical_name": "沈清漪",
                        "entity_type": "character",
                        "aliases": ["阿清"],
                    },
                    {
                        "entity_id": "char_chenmo",
                        "canonical_name": "陈默",
                        "entity_type": "character",
                    },
                ]
            }
        )[0],
    )
    calls: list[dict[str, Any]] = []

    async def call(_task: Any, context: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        calls.append(context)
        mentions = context["mentions"]
        selected = mentions[:1] if len(calls) == 1 else mentions
        return {
            "decisions": [
                {
                    "mention": item["mention"],
                    "verdict": "insufficient_evidence",
                    "selected_entity_id": "",
                    "selected_canonical_name": "",
                    "candidate_entity_ids": item["candidate_entity_ids"],
                    "evidence_refs": [],
                    "rationale": "证据不足。",
                    "requires_independent_review": False,
                }
                for item in selected
            ],
            "summary": "已裁决。",
        }

    decisions = await adjudicate_entity_references(
        call=call,
        purpose="test",
        mentions=[
            {"mention": "阿清", "candidate_entity_ids": ["char_qingyi"]},
            {"mention": "陈默", "candidate_entity_ids": ["char_chenmo"]},
        ],
        evidence_pack=pack,
    )

    assert len(decisions) == 2
    assert len(calls) == 2
    assert "exactly one decision" in calls[1]["adjudication_validation_error"]
