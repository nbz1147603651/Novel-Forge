"""LLM-only entity-reference adjudication over bounded retrieved evidence."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.narrative_state.evidence_contracts import (
    EntityReferenceAdjudication,
    EntityReferenceAdjudicationBatch,
    RetrievalEvidencePack,
)

EntityAdjudicationCall = Callable[..., Awaitable[dict[str, Any]]]
_log = logging.getLogger(__name__)


def _validated_batch(
    raw: Any,
    *,
    mentions: list[dict[str, Any]],
    evidence_pack: RetrievalEvidencePack,
) -> list[EntityReferenceAdjudication]:
    batch = EntityReferenceAdjudicationBatch.model_validate(raw)
    expected_mentions = [str(item.get("mention") or "").strip() for item in mentions]
    expected_mentions = [item for item in expected_mentions if item]
    actual = [decision.mention for decision in batch.decisions]
    if len(actual) != len(expected_mentions) or set(actual) != set(expected_mentions):
        raise ValueError("entity adjudication must return exactly one decision for every mention")
    if len(actual) != len(set(actual)):
        raise ValueError("entity adjudication must not return duplicate mention decisions")
    expected_candidates = {
        str(item.get("mention") or "").strip(): [
            str(candidate or "").strip()
            for candidate in item.get("candidate_entity_ids", []) or []
            if str(candidate or "").strip()
        ]
        for item in mentions
    }
    allowed_evidence_refs = {card.source_ref for card in evidence_pack.evidence_cards}
    canonical_names_by_id = {
        entity_id: card.canonical_name
        for card in evidence_pack.evidence_cards
        for entity_id in card.entity_ids
        if card.canonical_name
    }
    for decision in batch.decisions:
        if decision.candidate_entity_ids != expected_candidates.get(decision.mention, []):
            raise ValueError("entity adjudication must echo the presented candidate ids exactly")
        if any(ref not in allowed_evidence_refs for ref in decision.evidence_refs):
            raise ValueError("entity adjudication referenced evidence outside the presented pack")
        expected_name = canonical_names_by_id.get(decision.selected_entity_id)
        if expected_name and decision.selected_canonical_name != expected_name:
            raise ValueError("entity adjudication canonical name must match candidate evidence")
    return batch.decisions


async def adjudicate_entity_references(
    *,
    call: EntityAdjudicationCall,
    purpose: str,
    mentions: list[dict[str, Any]],
    evidence_pack: RetrievalEvidencePack,
    max_tokens: int = 2400,
    temperature: float = 0.0,
    independent_review: bool = False,
) -> list[EntityReferenceAdjudication]:
    """Ask an LLM to decide identities; local code only validates structure.

    ``mentions`` must already carry the candidate ids returned by Zvec.  Their
    order is retrieval order only and is not exposed as a score to the model.
    """
    clean_mentions: list[dict[str, Any]] = []
    seen_mentions: set[str] = set()
    for item in mentions:
        mention = str(item.get("mention") or "").strip()
        if not mention or mention in seen_mentions:
            continue
        seen_mentions.add(mention)
        clean_mentions.append(item)
    if not clean_mentions:
        return []

    async def _call_batch(
        request_mentions: list[dict[str, Any]],
        *,
        is_independent_review: bool,
    ) -> list[EntityReferenceAdjudication]:
        validation_error = ""
        for semantic_attempt in range(1, 3):
            response = await call(
                TaskType.ADJUDICATE_ENTITY_REFERENCES,
                {
                    "purpose": purpose,
                    "mentions": request_mentions,
                    "evidence_pack": evidence_pack.model_dump(mode="json"),
                    "independent_review": is_independent_review,
                    "adjudication_validation_error": validation_error,
                    "adjudication_semantic_attempt": semantic_attempt,
                },
                max_tokens=max_tokens,
                temperature=temperature,
                required_keys=("decisions", "summary"),
                max_retries=3,
            )
            try:
                return _validated_batch(
                    response,
                    mentions=request_mentions,
                    evidence_pack=evidence_pack,
                )
            except ValueError as exc:
                validation_error = str(exc)
                if semantic_attempt >= 2:
                    raise
        raise ValueError(validation_error or "entity adjudication validation failed")

    primary = await _call_batch(clean_mentions, is_independent_review=False)
    if not independent_review:
        return primary

    reviewed_mentions = [
        mention
        for mention in clean_mentions
        if next(
            decision.requires_independent_review
            for decision in primary
            if decision.mention == str(mention.get("mention") or "").strip()
        )
    ]
    if not reviewed_mentions:
        return primary

    try:
        independent = await _call_batch(reviewed_mentions, is_independent_review=True)
    except Exception as exc:
        _log.warning(
            "entity_reference_independent_review_degraded | mentions=%d | error=%s",
            len(reviewed_mentions),
            exc,
        )
        return [
            decision.model_copy(
                update={
                    "requires_independent_review": True,
                    "rationale": (
                        f"{decision.rationale}；独立复核暂不可用，保留主裁决并标记待复核。"
                    ).strip("；"),
                }
            )
            if decision.mention
            in {str(item.get("mention") or "").strip() for item in reviewed_mentions}
            else decision
            for decision in primary
        ]
    independent_by_mention = {decision.mention: decision for decision in independent}
    reconciled: list[EntityReferenceAdjudication] = []
    for decision in primary:
        verifier = independent_by_mention.get(decision.mention)
        if verifier is None:
            reconciled.append(decision)
        elif (
            verifier.verdict == decision.verdict
            and verifier.selected_entity_id == decision.selected_entity_id
            and verifier.selected_canonical_name == decision.selected_canonical_name
        ):
            reconciled.append(decision)
        else:
            # This is not a semantic fallback: disagreement is persisted as an
            # explicit abstention so no local matcher can select a winner.
            reconciled.append(
                EntityReferenceAdjudication(
                    mention=decision.mention,
                    verdict="ambiguous",
                    candidate_entity_ids=decision.candidate_entity_ids,
                    evidence_refs=list(
                        dict.fromkeys([*decision.evidence_refs, *verifier.evidence_refs])
                    ),
                    rationale="独立 LLM 裁决不一致，保留为歧义等待后续证据。",
                    requires_independent_review=True,
                )
            )
    return reconciled


__all__ = ["EntityAdjudicationCall", "adjudicate_entity_references"]
