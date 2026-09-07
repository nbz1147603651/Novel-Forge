"""Schemas for init-time coherence claims, retrieval, and adjudication."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.core.schemas.cognition import (
    ActionLevel,
    AwarenessLevel,
    CognitiveLevel,
    normalize_character_knowledge_coverage,
)
from novel_forge.core.utils.type_coerce import stringify_text_value

ClaimType = Literal[
    "state",
    "event",
    "payoff",
    "dependency",
    "relationship",
    "world_rule",
    "knowledge",
    "promise",
    "other",
]
_CLAIM_TYPE_VALUES = frozenset(
    {
        "state",
        "event",
        "payoff",
        "dependency",
        "relationship",
        "world_rule",
        "knowledge",
        "promise",
        "other",
    }
)

Temporality = Literal[
    "actual",
    "flashback",
    "foreshadow",
    "vision",
    "hypothetical",
    "planned",
    "unknown",
]

InitCreativeRiskLevel = Literal["low", "medium", "high", "critical"]
ClaimCoverageStatus = Literal["complete", "partial", "uncertain"]


class ProjectOntology(VersionedSchema):
    """Project-local ontology derived by the LLM, not hardcoded by genre."""

    model_config = ConfigDict(extra="ignore")

    domains: list[str] = Field(default_factory=list)
    entity_types: list[str] = Field(default_factory=list)
    state_axes: list[str] = Field(default_factory=list)
    relationship_axes: list[str] = Field(default_factory=list)
    payoff_types: list[str] = Field(default_factory=list)
    irreversible_event_markers: list[str] = Field(default_factory=list)
    temporal_markers: list[str] = Field(default_factory=list)
    terminology: dict[str, str] = Field(default_factory=dict)

    @field_validator(
        "domains",
        "entity_types",
        "state_axes",
        "relationship_axes",
        "payoff_types",
        "irreversible_event_markers",
        "temporal_markers",
        mode="before",
    )
    @classmethod
    def normalize_string_lists(cls, value: Any) -> list[str]:
        if value is None:
            return []
        values = value if isinstance(value, list) else [value]
        result: list[str] = []
        for item in values:
            text = ""
            if isinstance(item, dict):
                for key in (
                    "name",
                    "label",
                    "value",
                    "id",
                    "type",
                    "marker",
                    "term",
                    "axis",
                    "event_type",
                    "payoff_type",
                    "temporal_marker",
                ):
                    candidate = item.get(key)
                    if candidate is not None and str(candidate).strip():
                        text = str(candidate).strip()
                        break
            else:
                text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
        return result

    @field_validator("terminology", mode="before")
    @classmethod
    def normalize_terminology(cls, value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {str(key): str(val or "") for key, val in value.items() if str(key).strip()}


class InitCoherenceProfile(VersionedSchema):
    """Narrative-coherence lens for one project."""

    model_config = ConfigDict(extra="ignore")

    genre_tags: list[str] = Field(default_factory=list)
    narrative_modes: list[str] = Field(default_factory=list)
    project_ontology: ProjectOntology = Field(default_factory=ProjectOntology)
    conflict_lens: list[str] = Field(default_factory=list)
    extraction_guidance: list[str] = Field(default_factory=list)
    summary: str = ""

    @field_validator("summary", mode="before")
    @classmethod
    def _coerce_summary(cls, value: Any) -> str:
        return stringify_text_value(value)


class ClaimChapterRange(VersionedSchema):
    """Optional chapter span a claim applies to."""

    model_config = ConfigDict(extra="ignore")

    start: int = Field(default=0, ge=0)
    end: int = Field(default=0, ge=0)

    @field_validator("start", "end", mode="before")
    @classmethod
    def empty_range_endpoint_to_zero(cls, value: Any) -> int:
        if value is None or str(value).strip() == "":
            return 0
        return int(value)

    @model_validator(mode="after")
    def normalize_range(self) -> ClaimChapterRange:
        if self.end and self.start and self.end < self.start:
            self.start, self.end = self.end, self.start
        return self


class CoherenceClaim(VersionedSchema):
    """Structured narrative fact extracted from init artifacts."""

    model_config = ConfigDict(extra="ignore")

    claim_id: str
    artifact: str
    source_path: str
    source_field: str = ""
    chapter_numbers: list[int] = Field(default_factory=list)
    chapter_range: ClaimChapterRange | None = None
    subject_ids: list[str] = Field(default_factory=list)
    subject_text: str = ""
    entity_mentions: list[str] = Field(default_factory=list)
    axis: str = ""
    claim_type: ClaimType = "other"
    claim_text: str
    state_before: str = ""
    state_after: str = ""
    event_type: str = ""
    payoff_id: str = ""
    payoff_kind: str = ""
    irreversible: bool = False
    temporality: Temporality = "unknown"
    evidence: str
    cognitive_subjects: list[str]
    cognitive_object: str
    cognitive_level: CognitiveLevel
    action_level: ActionLevel
    reader_awareness: AwarenessLevel
    character_knowledge_coverage: dict[str, AwarenessLevel]
    cognitive_chapter: int | None
    public_reveal_chapter: int | None
    foreshadow_chapters: list[int]
    reveal_chapter: int | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="before")
    @classmethod
    def normalize_metadata(cls, value: Any) -> dict[str, Any]:
        """Coerce ``None``/non-dict drift to an empty metadata dict.

        Models occasionally emit ``"metadata": null`` or a non-dict value when
        they have no project-specific keys to record. Pydantic otherwise rejects
        the strict ``dict[str, Any]`` type, which forces a wasteful retry of
        the whole claim-extraction call.
        """
        if value is None or isinstance(value, dict):
            return value if isinstance(value, dict) else {}
        return {}

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value: Any) -> float:
        """Allow lossless numeric conversion but reject invented confidence."""

        if isinstance(value, bool):
            raise ValueError("confidence must be a number between 0 and 1")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError("confidence must be a number between 0 and 1") from None
        if number < 0.0 or number > 1.0:
            raise ValueError("confidence must be a number between 0 and 1")
        return number

    @field_validator("claim_type", mode="before")
    @classmethod
    def normalize_claim_type(cls, value: Any) -> str:
        """Normalize spelling only; unknown semantics must be re-judged by an LLM."""

        text = str(value or "").strip().lower()
        if text in _CLAIM_TYPE_VALUES:
            return text
        raise ValueError("claim_type must be a supported claim type")

    @field_validator("irreversible", mode="before")
    @classmethod
    def normalize_irreversible(cls, value: Any) -> bool:
        """Keep the model's explicit boolean decision; never default it locally."""

        if isinstance(value, bool):
            return value
        raise ValueError("irreversible must be a boolean")

    @field_validator("cognitive_level", "action_level", "reader_awareness", mode="before")
    @classmethod
    def normalize_cognitive_enums(cls, value: Any) -> str | None:
        """Normalize spelling only; Pydantic rejects missing or invalid semantics."""
        if value is None:
            return None
        text = str(value).strip().lower()
        return text or None

    @field_validator("payoff_id", "payoff_kind", mode="before")
    @classmethod
    def _coerce_payoff_null_to_empty(cls, v: Any) -> str:
        """Coerce ``null`` to ``""`` so the model can safely omit payoff metadata.

        Models occasionally emit ``"payoff_id": null`` / ``"payoff_kind": null``
        instead of leaving the field empty, which Pydantic otherwise rejects
        because the field is strictly ``str``.
        """
        if v is None:
            return ""
        return str(v).strip()

    @field_validator(
        "source_field",
        "subject_text",
        "axis",
        "state_before",
        "state_after",
        "event_type",
        "payoff_id",
        "payoff_kind",
        "cognitive_object",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str:
        return stringify_text_value(value)

    @field_validator("claim_id", "artifact", "source_path", "claim_text", "evidence")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("field must not be empty")
        return text

    @field_validator("chapter_numbers", mode="before")
    @classmethod
    def normalize_chapter_numbers(cls, value: Any) -> list[int]:
        if value is None:
            return []
        values = value if isinstance(value, list) else [value]
        normalized: list[int] = []
        for item in values:
            try:
                number = int(item)
            except (TypeError, ValueError):
                continue
            if number >= 1 and number not in normalized:
                normalized.append(number)
        return sorted(normalized)

    @field_validator("chapter_range", mode="before")
    @classmethod
    def normalize_chapter_range(cls, value: Any) -> Any:
        if value is None or isinstance(value, (ClaimChapterRange, dict)):
            return value
        values = value if isinstance(value, list) else [value]
        normalized: list[int] = []
        for item in values:
            try:
                number = int(item)
            except (TypeError, ValueError):
                continue
            if number >= 1:
                normalized.append(number)
        if not normalized:
            return None
        return {"start": min(normalized), "end": max(normalized)}

    @field_validator("subject_ids", "entity_mentions", mode="before")
    @classmethod
    def normalize_subject_ids(cls, value: Any) -> list[str]:
        if value is None:
            return []
        values = value if isinstance(value, list) else [value]
        normalized: list[str] = []
        for item in values:
            text = str(item or "").strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    @field_validator("cognitive_subjects", mode="before")
    @classmethod
    def normalize_cognitive_subjects(cls, value: Any) -> list[str]:
        if value is None:
            return []
        values = value if isinstance(value, list) else [value]
        normalized: list[str] = []
        for item in values:
            text = str(item or "").strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    @field_validator("cognitive_chapter", "public_reveal_chapter", "reveal_chapter", mode="before")
    @classmethod
    def normalize_optional_chapter(cls, value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        if isinstance(value, bool):
            raise ValueError("chapter anchor must be a positive integer or null")
        try:
            chapter = int(value)
        except (TypeError, ValueError):
            raise ValueError("chapter anchor must be a positive integer or null") from None
        if chapter < 1:
            raise ValueError("chapter anchor must be a positive integer or null")
        return chapter

    @field_validator("character_knowledge_coverage", mode="before")
    @classmethod
    def normalize_character_knowledge_coverage(cls, value: Any) -> dict[str, AwarenessLevel]:
        return normalize_character_knowledge_coverage(value, none_as_empty=False)

    @field_validator("foreshadow_chapters", mode="before")
    @classmethod
    def normalize_foreshadow_chapters(cls, value: Any) -> list[int]:
        values = value if isinstance(value, list) else [value]
        normalized: list[int] = []
        for item in values:
            try:
                chapter = int(item)
            except (TypeError, ValueError):
                continue
            if chapter >= 1 and chapter not in normalized:
                normalized.append(chapter)
        normalized.sort()
        return normalized

    @model_validator(mode="after")
    def infer_range_from_chapters(self) -> CoherenceClaim:
        empty_range = (
            self.chapter_range is not None
            and not self.chapter_range.start
            and not self.chapter_range.end
        )
        if (self.chapter_range is None or empty_range) and self.chapter_numbers:
            self.chapter_range = ClaimChapterRange(
                start=min(self.chapter_numbers),
                end=max(self.chapter_numbers),
            )
        return self


class CoherenceClaimBatch(VersionedSchema):
    """LLM output envelope for one claim extraction chunk."""

    model_config = ConfigDict(extra="ignore")

    claims: list[CoherenceClaim] = Field(default_factory=list)
    coverage_status: ClaimCoverageStatus
    unprocessed_source_refs: list[str] = Field(default_factory=list)
    summary: str = ""

    @field_validator("unprocessed_source_refs", mode="before")
    @classmethod
    def normalize_unprocessed_source_refs(cls, value: Any) -> list[str]:
        values = value if isinstance(value, list) else [value]
        result: list[str] = []
        for item in values:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
        return result

    @model_validator(mode="after")
    def validate_coverage_evidence(self) -> CoherenceClaimBatch:
        if self.coverage_status == "complete" and self.unprocessed_source_refs:
            raise ValueError("complete coverage cannot include unprocessed source refs")
        if self.coverage_status != "complete" and not self.unprocessed_source_refs:
            raise ValueError("incomplete coverage must identify unprocessed source refs")
        return self


class ConflictCandidate(VersionedSchema):
    """A possibly conflicting claim group retrieved before LLM adjudication."""

    model_config = ConfigDict(extra="ignore")

    candidate_id: str
    candidate_type: str
    reason: str
    claim_ids: list[str] = Field(default_factory=list)
    claims: list[CoherenceClaim] = Field(default_factory=list)
    retrieval_sources: list[str] = Field(default_factory=list)
    severity_hint: str = "medium"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    chapter_span: ClaimChapterRange | None = None


class ConflictCandidateReport(VersionedSchema):
    """Persisted candidate retrieval report for one init stage."""

    model_config = ConfigDict(extra="ignore")

    stage: str
    candidates: list[ConflictCandidate] = Field(default_factory=list)
    claims_count: int = 0
    degraded_memory: bool = False
    summary: str = ""


class InitConflictAdjudicationReport(VersionedSchema):
    """Unified report shape consumed by readiness and repair gates."""

    model_config = ConfigDict(extra="allow")

    stage: str
    artifact: str = ""
    verdict: str = "accept"
    issues: list[dict[str, Any]] = Field(default_factory=list)
    source_refs: list[Any] = Field(default_factory=list)
    repair_scope: list[dict[str, Any]] = Field(default_factory=list)
    preserve: list[Any] = Field(default_factory=list)
    change_intent: str = ""
    blocked: bool = False
    summary: str = "未发现硬冲突。"
    claims_count: int = 0
    candidate_count: int = 0
    adjudicated_candidate_count: int = 0
    degraded_memory: bool = False
    batch_reports: list[dict[str, Any]] = Field(default_factory=list)


class InitCreativeRefinementSuggestion(VersionedSchema):
    """One conservative enhancement suggestion derived from existing evidence."""

    model_config = ConfigDict(extra="ignore")

    suggestion_id: str
    artifact: str = "blueprint"
    target_path: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    benefit_type: str = ""
    rationale: str = ""
    risk_level: InitCreativeRiskLevel = "medium"
    auto_apply: bool = False

    @field_validator("suggestion_id", mode="before")
    @classmethod
    def normalize_suggestion_id(cls, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("suggestion_id must not be empty")
        return text

    @field_validator(
        "artifact",
        "target_path",
        "benefit_type",
        "rationale",
        mode="before",
    )
    @classmethod
    def normalize_optional_text_fields(cls, value: Any) -> str:
        return stringify_text_value(value)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def normalize_evidence_refs(cls, value: Any) -> list[str]:
        if value is None:
            return []
        values = value if isinstance(value, list) else [value]
        result: list[str] = []
        for item in values:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
        return result


class InitCreativeRefinementReport(VersionedSchema):
    """Optional conservative creative refinement envelope before init gates."""

    model_config = ConfigDict(extra="ignore")

    suggestions: list[InitCreativeRefinementSuggestion] = Field(default_factory=list)
    repair_scope: list[dict[str, Any]] = Field(default_factory=list)
    patches: list[dict[str, Any]] = Field(default_factory=list)
    preserve: list[Any] = Field(default_factory=list)
    risks: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
