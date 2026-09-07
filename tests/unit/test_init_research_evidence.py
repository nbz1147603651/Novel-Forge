from __future__ import annotations

import pytest

from novel_forge.research.contracts import (
    ResearchDossier,
    ResearchQuery,
    ResearchReport,
)
from novel_forge.research.evidence import (
    MissingMandatoryResearchEvidence,
    build_init_retrieval_evidence_pack,
    research_uncertainty_notes,
)


def _report(priority: str = "must") -> ResearchReport:
    return ResearchReport(
        enabled=True,
        provider="mcp",
        status="succeeded",
        queries=[ResearchQuery(query="清代驿站制度", priority=priority)],
        spec_fingerprint="spec-1",
    )


def test_must_evidence_requires_fact_and_real_source_ref() -> None:
    dossier = ResearchDossier(
        enabled=True,
        provider="mcp",
        status="succeeded",
        real_world_constraints=["驿站递送有固定程限"],
        source_refs=[],
        spec_fingerprint="spec-1",
    )

    with pytest.raises(MissingMandatoryResearchEvidence, match="清代驿站"):
        build_init_retrieval_evidence_pack(
            report=_report(), dossier=dossier, purpose="story_bible"
        )


def test_pack_contains_only_bounded_source_excerpt_and_authority() -> None:
    dossier = ResearchDossier(
        enabled=True,
        provider="mcp",
        status="succeeded",
        real_world_constraints=["驿站递送有固定程限"],
        terminology=["铺递"],
        inspiration_notes=["可将驿铃作为听觉母题"],
        source_refs=["https://example.test/source"],
        spec_fingerprint="spec-1",
        config_fingerprint="cfg-1",
    )

    pack = build_init_retrieval_evidence_pack(
        report=_report(), dossier=dossier, purpose="story_bible", token_budget=500
    )

    assert pack.evidence_cards
    assert {card.kind for card in pack.evidence_cards} <= {
        "external_fact",
        "external_inspiration",
    }
    assert all(card.source_ref == "https://example.test/source" for card in pack.evidence_cards)
    assert all(card.authority == "supporting" for card in pack.evidence_cards)
    assert all(len(card.excerpt) <= 600 for card in pack.evidence_cards)


def test_should_failure_degrades_to_uncertainty_instead_of_blocking() -> None:
    report = _report(priority="should")
    dossier = ResearchDossier(enabled=True, provider="mcp", status="failed")

    pack = build_init_retrieval_evidence_pack(
        report=report, dossier=dossier, purpose="character"
    )
    notes = research_uncertainty_notes(report=report, dossier=dossier)

    assert pack.evidence_cards == []
    assert any("should" in item and "清代驿站" in item for item in notes)

