"""Bounded prompt evidence projections for initialization research."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from novel_forge.narrative_state.evidence_contracts import (
    RetrievalEvidenceCard,
    RetrievalEvidencePack,
)
from novel_forge.pipeline.context_governance import (
    EvidenceBudgetExceeded,
    estimate_json_tokens,
    select_ranked_evidence,
)
from novel_forge.research.contracts import ResearchDossier, ResearchReport


class MissingMandatoryResearchEvidence(ValueError):
    """Raised when a must-priority query has no usable external fact evidence."""


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _card_id(*, kind: str, source_ref: str, excerpt: str) -> str:
    raw = "\x1f".join((kind, source_ref, excerpt)).encode("utf-8")
    return f"research:{sha256(raw).hexdigest()[:20]}"


def _research_cards(dossier: ResearchDossier) -> list[RetrievalEvidenceCard]:
    source_refs = getattr(dossier, "source_refs", []) or []
    refs = [_clean(item) for item in source_refs if _clean(item)] or [
        f"research:{getattr(dossier, 'provider', '') or 'unknown'}"
    ]
    cards: list[RetrievalEvidenceCard] = []
    fact_items = [
        *(
            f"现实约束：{item}"
            for item in (getattr(dossier, "real_world_constraints", []) or [])
        ),
        *(f"术语：{item}" for item in (getattr(dossier, "terminology", []) or [])),
    ]
    for index, raw_excerpt in enumerate(fact_items):
        excerpt = _clean(raw_excerpt)
        if not excerpt:
            continue
        source_ref = refs[index % len(refs)]
        cards.append(
            RetrievalEvidenceCard(
                card_id=_card_id(
                    kind="external_fact", source_ref=source_ref, excerpt=excerpt
                ),
                kind="external_fact",
                source_ref=source_ref,
                excerpt=excerpt[:600],
                authority="supporting",
            )
        )
    for index, raw_excerpt in enumerate(getattr(dossier, "inspiration_notes", []) or []):
        excerpt = _clean(raw_excerpt)
        if not excerpt:
            continue
        source_ref = refs[index % len(refs)]
        cards.append(
            RetrievalEvidenceCard(
                card_id=_card_id(
                    kind="external_inspiration", source_ref=source_ref, excerpt=excerpt
                ),
                kind="external_inspiration",
                source_ref=source_ref,
                excerpt=excerpt[:600],
                authority="supporting",
            )
        )
    return cards


def build_init_retrieval_evidence_pack(
    *,
    report: ResearchReport,
    dossier: ResearchDossier,
    purpose: str,
    token_budget: int = 1800,
) -> RetrievalEvidencePack:
    """Build one bounded pack using the shared ranked-evidence boundary."""

    cards = _research_cards(dossier)
    fact_cards = [card for card in cards if card.kind == "external_fact"]
    report_queries = list(getattr(report, "queries", []) or [])
    must_queries = [query for query in report_queries if query.priority == "must"]
    usable_source_refs = [
        _clean(item)
        for item in (getattr(dossier, "source_refs", []) or [])
        if _clean(item)
    ]
    if must_queries and (
        dossier.status != "succeeded"
        or len(fact_cards) < len(must_queries)
        or not usable_source_refs
    ):
        missing = "; ".join(_clean(query.query) for query in must_queries)
        raise MissingMandatoryResearchEvidence(f"must research evidence missing: {missing}")

    mandatory = fact_cards[: len(must_queries)] if must_queries else []
    try:
        selection = select_ranked_evidence(
            cards,
            mandatory_items=mandatory,
            token_budget=token_budget,
            estimate_tokens=lambda card: estimate_json_tokens(card.model_dump(mode="json")),
            identity=lambda card: card.card_id,
        )
    except EvidenceBudgetExceeded as exc:
        raise MissingMandatoryResearchEvidence(str(exc)) from exc

    selected = list(selection.selected)
    source_hashes = list(
        dict.fromkeys(card.source_hash or card.content_hash for card in selected)
    )
    query_text = "；".join(_clean(query.query) for query in report_queries if _clean(query.query))
    pack_seed = "\x1f".join(
        (
            purpose,
            str(getattr(dossier, "spec_fingerprint", "") or ""),
            str(getattr(dossier, "config_fingerprint", "") or ""),
        )
    )
    return RetrievalEvidencePack(
        pack_id=f"init-research:{sha256(pack_seed.encode('utf-8')).hexdigest()[:20]}",
        purpose=purpose,
        query=query_text,
        source_hashes=source_hashes,
        evidence_cards=selected,
        candidate_limit=len(cards),
        evidence_token_budget=max(0, int(token_budget)),
        estimated_evidence_tokens=selection.used_tokens,
        retrieved_candidate_count=len(cards),
        omitted_candidate_count_lower_bound=selection.omitted_count,
        has_more_evidence=selection.omitted_count > 0,
    )


def research_uncertainty_notes(
    *, report: ResearchReport, dossier: ResearchDossier
) -> list[str]:
    """Expose should/nice degradation without turning it into a hard fact."""

    notes = [
        _clean(item)
        for item in (getattr(dossier, "uncertainty_notes", []) or [])
        if _clean(item)
    ]
    if dossier.status != "succeeded" or not getattr(dossier, "source_refs", []):
        notes.extend(
            f"未满足 {query.priority} 研究：{_clean(query.query)}"
            for query in (getattr(report, "queries", []) or [])
            if query.priority in {"should", "nice"} and _clean(query.query)
        )
    return list(dict.fromkeys(notes))


__all__ = (
    "MissingMandatoryResearchEvidence",
    "build_init_retrieval_evidence_pack",
    "research_uncertainty_notes",
)
