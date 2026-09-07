"""Typed contracts shared by narrative evidence retrieval and LLM adjudication.

The models in this module intentionally keep retrieval and judgement separate:
Zvec may rank a card, but only an LLM adjudication can attach narrative
meaning to it.  Cards are immutable source projections; packs are bounded
views prepared for one task invocation.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from novel_forge.core.schemas.base import VersionedSchema

EvidenceAuthority = Literal["accepted", "supporting"]
EvidenceKind = Literal[
    "entity",
    "accepted_state",
    "event",
    "claim",
    "relationship",
    "knowledge",
    "motif",
    "adjudication",
    "external_fact",
    "external_inspiration",
]
EntityReferenceVerdict = Literal[
    "resolved",
    "ambiguous",
    "new_entity",
    "insufficient_evidence",
]


def evidence_content_hash(*, kind: str, source_ref: str, excerpt: str) -> str:
    """Return a stable content identity without assigning narrative meaning."""
    payload = "\x1f".join((str(kind), str(source_ref), str(excerpt)))
    return sha256(payload.encode("utf-8")).hexdigest()


class RetrievalEvidenceCard(VersionedSchema):
    """One retrievable source fragment with explicit authority and provenance."""

    model_config = ConfigDict(extra="forbid")

    card_id: str
    kind: EvidenceKind
    source_ref: str
    excerpt: str
    chapter_number: int = Field(default=0, ge=0)
    entity_ids: list[str] = Field(default_factory=list)
    canonical_name: str = ""
    authority: EvidenceAuthority = "supporting"
    canon_revision: str = ""
    source_hash: str = ""
    content_hash: str = ""

    @field_validator(
        "card_id",
        "source_ref",
        "excerpt",
        "canonical_name",
        "canon_revision",
        "source_hash",
        mode="before",
    )
    @classmethod
    def _clean_text(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("entity_ids", mode="before")
    @classmethod
    def _clean_entity_ids(cls, value: object) -> list[str]:
        values = value if isinstance(value, list) else [value]
        result: list[str] = []
        for item in values:
            entity_id = str(item or "").strip()
            if entity_id and entity_id not in result:
                result.append(entity_id)
        return result

    def model_post_init(self, __context: object) -> None:
        if not self.content_hash:
            object.__setattr__(
                self,
                "content_hash",
                evidence_content_hash(
                    kind=self.kind,
                    source_ref=self.source_ref,
                    excerpt=self.excerpt,
                ),
            )


class RetrievalEvidencePack(VersionedSchema):
    """A bounded, versioned task view of dynamic evidence with provenance."""

    model_config = ConfigDict(extra="forbid")

    pack_id: str
    purpose: str
    query: str
    canon_revision: str = ""
    max_visible_chapter: int = Field(default=0, ge=0)
    source_hashes: list[str] = Field(default_factory=list)
    evidence_cards: list[RetrievalEvidenceCard] = Field(default_factory=list)
    candidate_limit: int = Field(default=0, ge=0)
    evidence_token_budget: int = Field(default=0, ge=0)
    estimated_evidence_tokens: int = Field(default=0, ge=0)
    retrieved_candidate_count: int = Field(default=0, ge=0)
    omitted_candidate_count_lower_bound: int = Field(default=0, ge=0)
    has_more_evidence: bool = False

    @field_validator("pack_id", "purpose", "query", "canon_revision", mode="before")
    @classmethod
    def _clean_pack_text(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("source_hashes", mode="before")
    @classmethod
    def _clean_text_list(cls, value: object) -> list[str]:
        values = value if isinstance(value, list) else [value]
        result: list[str] = []
        for item in values:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
        return result


class EntityReferenceAdjudication(VersionedSchema):
    """LLM-only semantic verdict for one raw entity mention.

    ``candidate_entity_ids`` is the exact candidate set that was presented to
    the model.  Local code may only check that a resolved id belongs to this
    set; it must never infer a replacement from aliases or vector scores.
    """

    model_config = ConfigDict(extra="forbid")

    mention: str
    verdict: EntityReferenceVerdict
    selected_entity_id: str = ""
    selected_canonical_name: str = ""
    candidate_entity_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    rationale: str = ""
    requires_independent_review: bool = False

    @field_validator(
        "mention",
        "selected_entity_id",
        "selected_canonical_name",
        "rationale",
        mode="before",
    )
    @classmethod
    def _clean_adjudication_text(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("candidate_entity_ids", "evidence_refs", mode="before")
    @classmethod
    def _clean_adjudication_list(cls, value: object) -> list[str]:
        values = value if isinstance(value, list) else [value]
        result: list[str] = []
        for item in values:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
        return result

    @model_validator(mode="after")
    def _validate_selected_candidate(self) -> "EntityReferenceAdjudication":
        if self.verdict == "resolved":
            if not self.selected_entity_id:
                raise ValueError("resolved entity adjudication requires selected_entity_id")
            if self.selected_entity_id not in self.candidate_entity_ids:
                raise ValueError("selected_entity_id must be one of the presented candidates")
            if not self.selected_canonical_name:
                raise ValueError("resolved entity adjudication requires selected_canonical_name")
        elif self.selected_entity_id or self.selected_canonical_name:
            raise ValueError("only resolved adjudications may include a selected entity")
        return self


class EntityReferenceAdjudicationBatch(VersionedSchema):
    """One structured LLM response covering a bounded set of raw mentions."""

    model_config = ConfigDict(extra="forbid")

    decisions: list[EntityReferenceAdjudication] = Field(default_factory=list)
    summary: str = ""


__all__ = [
    "EntityReferenceAdjudication",
    "EntityReferenceAdjudicationBatch",
    "EntityReferenceVerdict",
    "EvidenceAuthority",
    "EvidenceKind",
    "RetrievalEvidenceCard",
    "RetrievalEvidencePack",
    "evidence_content_hash",
]
