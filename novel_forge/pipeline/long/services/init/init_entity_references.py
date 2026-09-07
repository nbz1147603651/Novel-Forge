"""Evidence-backed entity reference resolution for init coherence claims.

This module is intentionally narrow.  The canonical entity registry remains
the structured source of truth.  Exact registry facts are projected directly
into bounded evidence packs; Zvec is queried only for dynamic narrative
evidence when the fixed directory alone cannot bound the candidates.  The LLM
still makes every identity decision.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any, cast

from novel_forge.core.schemas.init_coherence import CoherenceClaim
from novel_forge.memory.narrative_evidence import NarrativeEvidenceService
from novel_forge.narrative_state.entity_adjudication import adjudicate_entity_references
from novel_forge.narrative_state.evidence_contracts import (
    EntityReferenceAdjudication,
    RetrievalEvidenceCard,
    RetrievalEvidencePack,
)
from novel_forge.pipeline.context_governance import (
    EvidenceBudgetExceeded,
    estimate_json_tokens,
    select_ranked_evidence,
)

_BATCH_SIZE = 6
_CANDIDATES_PER_MENTION = 4
_DIRECT_CATALOG_LIMIT = 24
_DECISION_CACHE_MAX_ENTRIES = 2048
_DEFAULT_OCCURRENCE_LIMIT = 12
_EntityDecisionCacheKey = tuple[
    str,
    str,
    tuple[str, ...],
    tuple[tuple[str, str, str], ...],
]
_log = logging.getLogger(__name__)


def entity_catalog_revision(entity_catalog: dict[str, Any] | None) -> str:
    entities = entity_catalog.get("allowed_entities") if isinstance(entity_catalog, dict) else []
    return _stable_hash(entities if isinstance(entities, list) else [])


def entity_catalog_evidence_cards(
    entity_catalog: dict[str, Any] | None,
) -> tuple[list[RetrievalEvidenceCard], str]:
    """Project entity source records into supporting evidence cards.

    Aliases remain quoted source evidence inside the card.  They never become
    a local matching rule or an output mapping.
    """
    entities = entity_catalog.get("allowed_entities") if isinstance(entity_catalog, dict) else []
    if not isinstance(entities, list):
        return [], ""
    source_hash = _stable_hash(entities)
    cards: list[RetrievalEvidenceCard] = []
    for raw in entities:
        if not isinstance(raw, dict):
            continue
        entity_id = str(raw.get("entity_id") or "").strip()
        canonical_name = str(raw.get("canonical_name") or raw.get("name") or "").strip()
        if not entity_id or not canonical_name:
            continue
        entity_type = str(raw.get("entity_type") or "unknown").strip() or "unknown"
        aliases = _clean_texts(raw.get("aliases"))
        excerpt = "；".join(
            part
            for part in (
                f"规范实体：{canonical_name}",
                f"实体类型：{entity_type}",
                f"来源别称：{'、'.join(aliases)}" if aliases else "",
            )
            if part
        )
        cards.append(
            RetrievalEvidenceCard(
                card_id=f"entity:{entity_id}",
                kind="entity",
                source_ref=f"entity_catalog/{entity_id}",
                excerpt=excerpt,
                entity_ids=[entity_id],
                canonical_name=canonical_name,
                authority="supporting",
                canon_revision=source_hash[:24],
                source_hash=source_hash,
            )
        )
    return cards, source_hash


async def adjudicate_init_claim_entity_references(
    ctx: Any,
    *,
    profile: dict[str, Any],
    claims: list[CoherenceClaim],
    stage: str = "",
) -> list[CoherenceClaim]:
    """Resolve only LLM-confirmed identity references in ``claims``.

    Fixed entity facts are not written into the vector index.  Small catalogs
    and exact ID/name/alias candidates are projected directly from structured
    storage.  Zvec contributes only already-indexed narrative evidence for
    ambiguous mentions in larger catalogs.
    """
    catalog = profile.get("entity_catalog")
    revision = entity_catalog_revision(catalog)
    if not claims:
        return claims
    cards, revision = entity_catalog_evidence_cards(catalog)
    if not cards:
        return _mark_unadjudicated(
            claims,
            reason="entity_catalog_empty",
            revision=revision,
        )

    occurrences = _claim_reference_occurrences(claims)
    if not occurrences:
        return _mark_not_required(claims, revision=revision)
    mentions = list(occurrences)
    all_decisions: dict[str, EntityReferenceAdjudication] = {}
    decision_cache = _entity_decision_cache(ctx)
    cache_hits = 0
    settings = getattr(ctx, "settings", None)
    evidence_token_budget = max(
        800,
        int(
            getattr(
                settings,
                "init_entity_reference_batch_evidence_token_budget",
                3600,
            )
            or 3600
        ),
    )
    occurrence_limit = max(
        4,
        min(
            100,
            int(
                getattr(
                    settings,
                    "init_entity_reference_occurrence_limit",
                    _DEFAULT_OCCURRENCE_LIMIT,
                )
                or _DEFAULT_OCCURRENCE_LIMIT
            ),
        ),
    )
    max_parallel = max(
        1,
        min(
            8,
            int(getattr(settings, "init_entity_reference_max_parallel", 3) or 3),
        ),
    )

    async def _adjudicate_batch(mention_batch: list[str]) -> None:
        nonlocal cache_hits
        try:
            packs, _cards, _revision = await build_entity_candidate_packs(
                ctx,
                entity_catalog=catalog,
                mentions=mention_batch,
                purpose="init_claim_entity_reference",
            )
            request_mentions, shared_pack = _request_from_packs(
                mention_batch,
                packs,
                occurrences=occurrences,
                revision=revision,
                evidence_token_budget=evidence_token_budget,
                occurrence_limit=occurrence_limit,
            )
            request_by_mention = {str(item.get("mention") or ""): item for item in request_mentions}
            pending_mentions: list[dict[str, Any]] = []
            batch_decisions: dict[str, EntityReferenceAdjudication] = {}
            for request_mention in request_mentions:
                key = _entity_decision_cache_key(
                    revision=revision,
                    request_mention=request_mention,
                    evidence_pack=shared_pack,
                )
                cached = decision_cache.get(key)
                if cached is None:
                    pending_mentions.append(request_mention)
                    continue
                batch_decisions[cached.mention] = cached
                cache_hits += 1

            if pending_mentions:
                decisions = await adjudicate_entity_references(
                    call=ctx.call_with_retry,
                    purpose="初始化一致性 claim 的实体指称裁决",
                    mentions=pending_mentions,
                    evidence_pack=shared_pack,
                    max_tokens=2600,
                    temperature=0.0,
                    independent_review=True,
                )
                for decision in decisions:
                    batch_decisions[decision.mention] = decision
                    resolved_request = request_by_mention.get(decision.mention)
                    if resolved_request is None or not _cacheable_entity_decision(decision):
                        continue
                    key = _entity_decision_cache_key(
                        revision=revision,
                        request_mention=resolved_request,
                        evidence_pack=shared_pack,
                    )
                    if len(decision_cache) < _DECISION_CACHE_MAX_ENTRIES:
                        decision_cache[key] = decision
            all_decisions.update(batch_decisions)
        except EvidenceBudgetExceeded:
            if len(mention_batch) <= 1:
                _log.warning(
                    "init_claim_entity_adjudication_budget_exceeded | mention=%s | budget=%d",
                    mention_batch,
                    evidence_token_budget,
                )
                return
            midpoint = max(1, len(mention_batch) // 2)
            await _adjudicate_batch(mention_batch[:midpoint])
            await _adjudicate_batch(mention_batch[midpoint:])
        except Exception as exc:
            _log.warning(
                "init_claim_entity_adjudication_batch_degraded | mentions=%s | error=%s",
                mention_batch,
                exc,
                exc_info=True,
            )
            on_step = getattr(ctx, "on_step", None)
            if callable(on_step):
                on_step(
                    "init_claim_entity_adjudication_degraded",
                    {
                        "claims": len(claims),
                        "mentions": mention_batch,
                        "stage": stage,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )

    semaphore = asyncio.Semaphore(max_parallel)

    async def _run_batch(mention_batch: list[str]) -> None:
        async with semaphore:
            await _adjudicate_batch(mention_batch)

    batches = [
        mentions[offset : offset + _BATCH_SIZE] for offset in range(0, len(mentions), _BATCH_SIZE)
    ]
    await asyncio.gather(*(_run_batch(batch) for batch in batches))
    if cache_hits:
        on_step = getattr(ctx, "on_step", None)
        if callable(on_step):
            on_step(
                "init_claim_entity_adjudication_cache_hit",
                {
                    "claims": len(claims),
                    "mentions": len(mentions),
                    "cache_hits": cache_hits,
                    "max_parallel": max_parallel,
                    # This is an observation inside an init-coherence gate, not
                    # a new top-level milestone.  Preserve the owning gate so
                    # every UI can project the event onto the right visible
                    # task-flow step instead of displaying its raw event key.
                    "stage": stage,
                },
            )
    if not all_decisions:
        return _mark_unadjudicated(
            claims,
            reason="all_adjudication_batches_failed",
            revision=revision,
        )
    return _apply_decisions(
        claims,
        all_decisions,
        occurrences,
        revision=revision,
    )


def _evidence_service(ctx: Any) -> NarrativeEvidenceService | None:
    memory_context = getattr(ctx, "memory_context", None)
    service = getattr(memory_context, "narrative_evidence_service", None)
    if service is None:
        return None
    # The concrete service is used at runtime.  The structural check keeps
    # this boundary testable without making a fake service a semantic source.
    if callable(getattr(service, "evidence_pack", None)):
        return cast(NarrativeEvidenceService, service)
    return None


async def _candidate_pack_for_mention(
    mention: str,
    *,
    catalog_cards: list[RetrievalEvidenceCard],
    exact_candidate_ids: set[str],
    lexical_candidate_ids: set[str],
    revision: str,
    service: NarrativeEvidenceService | None,
    purpose: str,
    max_visible_chapter: int,
) -> RetrievalEvidencePack:
    """Build candidates without persisting fixed registry rows in Zvec."""
    exact_cards = [
        card
        for card in catalog_cards
        if any(entity_id in exact_candidate_ids for entity_id in card.entity_ids)
    ]
    if exact_cards:
        candidate_cards = exact_cards
        narrative_cards: list[RetrievalEvidenceCard] = []
    elif len(catalog_cards) <= _DIRECT_CATALOG_LIMIT:
        candidate_cards = list(catalog_cards)
        narrative_cards = []
    else:
        narrative_cards = []
        if service is not None:
            narrative_pack = await service.evidence_pack(
                purpose=purpose,
                query=mention,
                canon_revision=revision[:24],
                max_visible_chapter=max_visible_chapter,
                source_hashes=[revision],
                candidate_limit=_CANDIDATES_PER_MENTION,
                evidence_token_budget=1600,
                kinds={
                    "accepted_state",
                    "event",
                    "claim",
                    "relationship",
                    "knowledge",
                    "adjudication",
                },
            )
            narrative_cards = list(narrative_pack.evidence_cards)
        candidate_ids = lexical_candidate_ids | {
            entity_id for card in narrative_cards for entity_id in card.entity_ids if entity_id
        }
        candidate_cards = [
            card
            for card in catalog_cards
            if any(entity_id in candidate_ids for entity_id in card.entity_ids)
        ]
    pack_cards = list(
        {card.card_id: card for card in [*candidate_cards, *narrative_cards]}.values()
    )
    return RetrievalEvidencePack(
        pack_id=_stable_hash(
            {
                "purpose": purpose,
                "mention": mention,
                "cards": [card.card_id for card in pack_cards],
                "revision": revision,
            }
        )[:24],
        purpose=purpose,
        query=mention,
        canon_revision=revision[:24],
        max_visible_chapter=max_visible_chapter,
        source_hashes=[revision],
        evidence_cards=pack_cards,
    )


async def build_entity_candidate_packs(
    ctx: Any,
    *,
    entity_catalog: dict[str, Any] | None,
    mentions: list[str],
    purpose: str,
    max_visible_chapter: int = 0,
) -> tuple[list[RetrievalEvidencePack], list[RetrievalEvidenceCard], str]:
    """Project fixed facts directly and add Zvec narrative evidence only if needed."""
    catalog_cards, revision = entity_catalog_evidence_cards(entity_catalog)
    service = _evidence_service(ctx)
    raw_entities = (
        entity_catalog.get("allowed_entities", []) if isinstance(entity_catalog, dict) else []
    )
    exact_ids_by_mention: dict[str, set[str]] = {}
    lexical_ids_by_mention: dict[str, set[str]] = {}
    for mention in mentions:
        token = str(mention or "").strip()
        exact_ids_by_mention[mention] = {
            str(entity.get("entity_id") or "").strip()
            for entity in raw_entities
            if isinstance(entity, dict)
            and token
            and token
            in {
                str(entity.get("entity_id") or "").strip(),
                str(entity.get("canonical_name") or entity.get("name") or "").strip(),
                *_clean_texts(entity.get("aliases")),
            }
            and str(entity.get("entity_id") or "").strip()
        }
        lexical_ids_by_mention[mention] = _lexical_candidate_ids(token, catalog_cards)
    packs = await asyncio.gather(
        *[
            _candidate_pack_for_mention(
                mention,
                catalog_cards=catalog_cards,
                exact_candidate_ids=exact_ids_by_mention.get(mention, set()),
                lexical_candidate_ids=lexical_ids_by_mention.get(mention, set()),
                revision=revision,
                service=service,
                purpose=purpose,
                max_visible_chapter=max_visible_chapter,
            )
            for mention in mentions
        ]
    )
    return list(packs), catalog_cards, revision


def _lexical_candidate_ids(
    mention: str,
    catalog_cards: list[RetrievalEvidenceCard],
) -> set[str]:
    """Shortlist near spellings as candidates; the LLM still decides identity."""

    token = _normalized_entity_token(mention)
    if len(token) < 2:
        return set()
    scored: list[tuple[float, str]] = []
    for card in catalog_cards:
        candidate = _normalized_entity_token(card.canonical_name)
        if len(candidate) < 2:
            continue
        score = SequenceMatcher(None, token, candidate).ratio()
        same_leading_char = token[0] == candidate[0]
        if token in candidate or candidate in token:
            score = max(score, 0.9)
        if score < 0.67 and not (same_leading_char and score >= 0.5):
            continue
        for entity_id in card.entity_ids:
            if entity_id:
                scored.append((score, entity_id))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return {entity_id for _, entity_id in scored[:_CANDIDATES_PER_MENTION]}


def _normalized_entity_token(value: Any) -> str:
    return re.sub(r"[\s\-_·•—（）()\[\]【】《》“”'\"：:]", "", str(value or "").strip())


def _claim_reference_occurrences(claims: list[CoherenceClaim]) -> dict[str, list[dict[str, str]]]:
    occurrences: dict[str, list[dict[str, str]]] = defaultdict(list)
    for claim in claims:
        raw = claim.metadata.get("raw_entity_references")
        raw = raw if isinstance(raw, dict) else {}
        raw_mentions = raw.get("entity_mentions", claim.entity_mentions)
        raw_subject_ids = raw.get("subject_ids", claim.subject_ids)
        raw_cognitive_subjects = raw.get("cognitive_subjects", claim.cognitive_subjects)
        raw_awareness = raw.get("character_knowledge_coverage", claim.character_knowledge_coverage)
        for mention in _clean_texts(raw_mentions):
            occurrences[mention].append({"claim_id": claim.claim_id, "field": "entity_mentions"})
        for mention in _clean_texts(raw_subject_ids):
            occurrences[mention].append({"claim_id": claim.claim_id, "field": "subject_ids"})
        for mention in _clean_texts(raw_cognitive_subjects):
            occurrences[mention].append({"claim_id": claim.claim_id, "field": "cognitive_subjects"})
        for mention in _clean_texts(list(raw_awareness) if isinstance(raw_awareness, dict) else []):
            occurrences[mention].append(
                {"claim_id": claim.claim_id, "field": "character_knowledge_coverage"}
            )
    return dict(occurrences)


def _request_from_packs(
    mentions: list[str],
    packs: list[RetrievalEvidencePack],
    *,
    occurrences: dict[str, list[dict[str, str]]],
    revision: str,
    evidence_token_budget: int = 3600,
    occurrence_limit: int = _DEFAULT_OCCURRENCE_LIMIT,
) -> tuple[list[dict[str, Any]], RetrievalEvidencePack]:
    cards_by_id: dict[str, RetrievalEvidenceCard] = {}
    for _mention, pack in zip(mentions, packs, strict=True):
        for card in pack.evidence_cards:
            cards_by_id.setdefault(card.card_id, card)

    mandatory_cards = [card for card in cards_by_id.values() if card.kind == "entity"]
    ranked_cards = [card for card in cards_by_id.values() if card.kind != "entity"]
    selection = select_ranked_evidence(
        ranked_cards,
        token_budget=evidence_token_budget,
        estimate_tokens=lambda card: estimate_json_tokens(card.model_dump(mode="json")),
        mandatory_items=mandatory_cards,
        identity=lambda card: card.card_id,
    )
    selected_cards = list(selection.selected)
    selected_ids = {card.card_id for card in selected_cards}

    request_mentions: list[dict[str, Any]] = []
    for mention, pack in zip(mentions, packs, strict=True):
        visible_cards = [card for card in pack.evidence_cards if card.card_id in selected_ids]
        candidate_ids: list[str] = []
        refs: list[str] = []
        for card in visible_cards:
            refs.append(card.source_ref)
            if card.kind == "entity":
                for entity_id in card.entity_ids:
                    if entity_id not in candidate_ids:
                        candidate_ids.append(entity_id)
        all_occurrences = occurrences.get(mention, [])
        bounded_occurrences = _bounded_claim_occurrences(
            all_occurrences,
            limit=occurrence_limit,
        )
        request_mentions.append(
            {
                "mention": mention,
                "candidate_entity_ids": candidate_ids,
                "candidate_evidence_refs": refs,
                "claim_occurrences": bounded_occurrences,
                "claim_occurrence_count": len(all_occurrences),
                "claim_occurrences_truncated": len(bounded_occurrences) < len(all_occurrences),
            }
        )
    payload = json.dumps(
        {"mentions": mentions, "cards": sorted(cards_by_id), "revision": revision},
        ensure_ascii=False,
        sort_keys=True,
    )
    shared_pack = RetrievalEvidencePack(
        pack_id=hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24],
        purpose="init_claim_entity_reference",
        query="；".join(mentions),
        canon_revision=revision[:24],
        max_visible_chapter=0,
        source_hashes=[revision],
        evidence_cards=selected_cards,
        candidate_limit=len(cards_by_id),
        evidence_token_budget=selection.token_budget,
        estimated_evidence_tokens=selection.used_tokens,
        retrieved_candidate_count=len(cards_by_id),
        omitted_candidate_count_lower_bound=selection.omitted_count,
        has_more_evidence=selection.omitted_count > 0,
    )
    return request_mentions, shared_pack


def _bounded_claim_occurrences(
    occurrences: list[dict[str, str]],
    *,
    limit: int,
) -> list[dict[str, str]]:
    """Keep representative occurrence ids without sending the full local index."""
    bounded_limit = max(1, int(limit or 1))
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    by_field: dict[str, list[dict[str, str]]] = defaultdict(list)
    for item in occurrences:
        claim_id = str(item.get("claim_id") or "").strip()
        field = str(item.get("field") or "").strip()
        key = (claim_id, field)
        if not claim_id or not field or key in seen:
            continue
        seen.add(key)
        normalized = {"claim_id": claim_id, "field": field}
        deduped.append(normalized)
        by_field[field].append(normalized)
    if len(deduped) <= bounded_limit:
        return deduped

    selected: list[dict[str, str]] = []
    selected_keys: set[tuple[str, str]] = set()

    def _append(item: dict[str, str]) -> None:
        key = (item["claim_id"], item["field"])
        if key in selected_keys or len(selected) >= bounded_limit:
            return
        selected_keys.add(key)
        selected.append(item)

    # Preserve both ends of every semantic field before filling from source order.
    for field_items in by_field.values():
        _append(field_items[0])
    for field_items in by_field.values():
        _append(field_items[-1])
    for item in deduped:
        _append(item)
    return selected


def _entity_decision_cache(
    ctx: Any,
) -> dict[_EntityDecisionCacheKey, EntityReferenceAdjudication]:
    cache = getattr(ctx, "_init_entity_reference_decision_cache", None)
    if isinstance(cache, dict):
        return cast(dict[_EntityDecisionCacheKey, EntityReferenceAdjudication], cache)
    cache = {}
    try:
        ctx._init_entity_reference_decision_cache = cache
    except (AttributeError, TypeError):
        pass
    return cache


def _entity_decision_cache_key(
    *,
    revision: str,
    request_mention: dict[str, Any],
    evidence_pack: RetrievalEvidencePack,
) -> _EntityDecisionCacheKey:
    evidence_refs = {
        str(item or "").strip()
        for item in request_mention.get("candidate_evidence_refs", []) or []
        if str(item or "").strip()
    }
    evidence_fingerprint = tuple(
        sorted(
            (
                card.source_ref,
                str(card.source_hash or ""),
                str(card.canon_revision or ""),
            )
            for card in evidence_pack.evidence_cards
            if card.source_ref in evidence_refs
        )
    )
    return (
        revision,
        str(request_mention.get("mention") or "").strip(),
        tuple(str(item or "").strip() for item in request_mention.get("candidate_entity_ids", [])),
        evidence_fingerprint,
    )


def _cacheable_entity_decision(decision: EntityReferenceAdjudication) -> bool:
    return decision.verdict == "resolved" and not decision.requires_independent_review


def _apply_decisions(
    claims: list[CoherenceClaim],
    decisions: dict[str, EntityReferenceAdjudication],
    occurrences: dict[str, list[dict[str, str]]],
    *,
    revision: str,
) -> list[CoherenceClaim]:
    result: list[CoherenceClaim] = []
    for claim in claims:
        raw_references = _raw_references_for_claim(claim)
        raw_awareness = raw_references["character_knowledge_coverage"]
        relevant = {
            mention: decision
            for mention, decision in decisions.items()
            if any(item["claim_id"] == claim.claim_id for item in occurrences.get(mention, []))
        }
        subject_ids = _resolved_values(raw_references["subject_ids"], relevant, value="id")
        cognitive_subjects = _resolved_values(
            raw_references["cognitive_subjects"], relevant, value="name"
        )
        entity_mentions = _canonicalized_mentions(
            raw_references["entity_mentions"],
            relevant,
        )
        character_knowledge_coverage: dict[str, str] = {}
        for mention, awareness in raw_awareness.items():
            decision = relevant.get(str(mention))
            if decision is None or decision.verdict != "resolved":
                continue
            character_knowledge_coverage.setdefault(decision.selected_canonical_name, awareness)
        metadata = dict(claim.metadata)
        metadata["raw_entity_references"] = raw_references
        metadata["entity_reference_adjudications"] = [
            decision.model_dump(mode="json") for decision in relevant.values()
        ]
        claim_mentions = {
            str(item)
            for values in raw_references.values()
            for item in (values.keys() if isinstance(values, dict) else values)
            if str(item).strip()
        }
        metadata["entity_adjudication_status"] = (
            "adjudicated" if claim_mentions <= set(relevant) else "partial"
        )
        metadata["entity_reference_revision"] = revision
        text_updates = _canonicalized_claim_text_fields(claim, relevant)
        result.append(
            claim.model_copy(
                update={
                    **text_updates,
                    "entity_mentions": entity_mentions,
                    "subject_ids": subject_ids,
                    "cognitive_subjects": cognitive_subjects,
                    "character_knowledge_coverage": character_knowledge_coverage,
                    "metadata": metadata,
                }
            )
        )
    return result


def _canonicalized_mentions(
    raw_values: list[str],
    decisions: dict[str, EntityReferenceAdjudication],
) -> list[str]:
    values: list[str] = []
    for mention in raw_values:
        decision = decisions.get(str(mention))
        value = (
            decision.selected_canonical_name
            if decision is not None
            and decision.verdict == "resolved"
            and decision.selected_canonical_name
            else str(mention)
        )
        if value and value not in values:
            values.append(value)
    return values


def _canonicalized_claim_text_fields(
    claim: CoherenceClaim,
    decisions: dict[str, EntityReferenceAdjudication],
) -> dict[str, str]:
    replacements = {
        mention: decision.selected_canonical_name
        for mention, decision in decisions.items()
        if decision.verdict == "resolved"
        and decision.selected_canonical_name
        and mention != decision.selected_canonical_name
    }
    updates: dict[str, str] = {}
    for field in (
        "claim_text",
        "subject_text",
        "state_before",
        "state_after",
        "evidence",
        "cognitive_object",
    ):
        value = str(getattr(claim, field, "") or "")
        repaired = value
        for mention, canonical in sorted(
            replacements.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            repaired = repaired.replace(mention, canonical)
        if repaired != value:
            updates[field] = repaired
    return updates


def _resolved_values(
    raw_values: list[str],
    decisions: dict[str, EntityReferenceAdjudication],
    *,
    value: str,
) -> list[str]:
    resolved: list[str] = []
    for mention in raw_values:
        decision = decisions.get(str(mention))
        if decision is None or decision.verdict != "resolved":
            continue
        selected = (
            decision.selected_entity_id if value == "id" else decision.selected_canonical_name
        )
        if selected and selected not in resolved:
            resolved.append(selected)
    return resolved


def _raw_references_for_claim(claim: CoherenceClaim) -> dict[str, Any]:
    previous_raw = claim.metadata.get("raw_entity_references")
    previous_raw = previous_raw if isinstance(previous_raw, dict) else {}
    raw_awareness_value = previous_raw.get(
        "character_knowledge_coverage", claim.character_knowledge_coverage
    )
    raw_awareness = dict(raw_awareness_value) if isinstance(raw_awareness_value, dict) else {}
    return {
        "entity_mentions": _clean_texts(previous_raw.get("entity_mentions", claim.entity_mentions)),
        "subject_ids": _clean_texts(previous_raw.get("subject_ids", claim.subject_ids)),
        "cognitive_subjects": _clean_texts(
            previous_raw.get("cognitive_subjects", claim.cognitive_subjects)
        ),
        "character_knowledge_coverage": raw_awareness,
    }


def _mark_unadjudicated(
    claims: list[CoherenceClaim],
    *,
    reason: str,
    revision: str,
) -> list[CoherenceClaim]:
    result: list[CoherenceClaim] = []
    for claim in claims:
        raw_references = _raw_references_for_claim(claim)
        result.append(
            claim.model_copy(
                update={
                    "subject_ids": [],
                    "cognitive_subjects": [],
                    "character_knowledge_coverage": {},
                    "metadata": {
                        **claim.metadata,
                        "raw_entity_references": raw_references,
                        "entity_adjudication_status": "unavailable",
                        "entity_adjudication_reason": reason,
                        "entity_reference_revision": revision,
                    },
                }
            )
        )
    return result


def _mark_not_required(
    claims: list[CoherenceClaim],
    *,
    revision: str,
) -> list[CoherenceClaim]:
    return [
        claim.model_copy(
            update={
                "metadata": {
                    **claim.metadata,
                    "raw_entity_references": _raw_references_for_claim(claim),
                    "entity_adjudication_status": "not_required",
                    "entity_reference_revision": revision,
                }
            }
        )
        for claim in claims
    ]


def _clean_texts(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "adjudicate_init_claim_entity_references",
    "build_entity_candidate_packs",
    "entity_catalog_evidence_cards",
    "entity_catalog_revision",
]
