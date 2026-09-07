"""Typed contracts for initialization-time research."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ResearchStatus = Literal["skipped", "succeeded", "empty", "failed"]


class ResearchQuery(BaseModel):
    """One deterministic web-research query derived from the story spec."""

    query: str
    rationale: str = ""
    intent: str = ""
    priority: Literal["must", "should", "nice"] = "should"
    locale: str = ""
    source_preferences: list[str] = Field(default_factory=list)
    recency_required: bool = False
    risk_if_missing: str = ""


class ResearchSource(BaseModel):
    """One normalized external source result."""

    title: str = ""
    url: str = ""
    snippet: str = ""
    score: float = 0.0


class ResearchResult(BaseModel):
    """Provider output before final briefing."""

    provider: str
    queries: list[ResearchQuery] = Field(default_factory=list)
    sources: list[ResearchSource] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ResearchBrief(BaseModel):
    """Compact prompt-facing research summary."""

    summary: str = ""
    source_notes: list[str] = Field(default_factory=list)


class ModelPriorNotes(BaseModel):
    """Model prior-knowledge supplement, independent from external sources."""

    enabled: bool = False
    status: ResearchStatus = "skipped"
    notes: list[str] = Field(default_factory=list)
    terminology: list[str] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def prompt_context(self) -> dict[str, object]:
        """Return prompt-facing prior notes while preserving the provenance boundary."""
        return {
            "enabled": self.enabled,
            "status": self.status,
            "source_type": "model_prior",
            "notes": list(self.notes),
            "terminology": list(self.terminology),
            "uncertainty_notes": list(self.uncertainty_notes),
            "warnings": list(self.warnings),
        }


class ResearchReport(BaseModel):
    """Persisted report for the init web-research stage."""

    schema_version: int = 1
    enabled: bool = False
    provider: str = "noop"
    status: ResearchStatus = "skipped"
    config: dict[str, object] = Field(default_factory=dict)
    config_fingerprint: str = ""
    queries: list[ResearchQuery] = Field(default_factory=list)
    sources: list[ResearchSource] = Field(default_factory=list)
    brief: ResearchBrief = Field(default_factory=ResearchBrief)
    warnings: list[str] = Field(default_factory=list)
    created_at: str = ""
    spec_fingerprint: str = ""
    llm_planned: bool = Field(default=False)
    knowledge_gaps: list[str] = Field(default_factory=list)
    query_plan_warnings: list[str] = Field(default_factory=list)
    fallback_used: str = Field(default="")
    model_prior: ModelPriorNotes = Field(default_factory=ModelPriorNotes)

    def prompt_context(self) -> dict[str, object]:
        """Return a small prompt-facing context with no raw provider payload."""
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "status": self.status,
            "summary": self.brief.summary,
            "source_notes": list(self.brief.source_notes),
            "warnings": list(self.warnings),
            "llm_planned": self.llm_planned,
            "knowledge_gaps": list(self.knowledge_gaps),
            "query_plan_warnings": list(self.query_plan_warnings),
            "fallback_used": self.fallback_used,
            "model_prior": self.model_prior.prompt_context(),
        }


class ResearchDossier(BaseModel):
    """LLM-compressed research package consumed by initialization prompts."""

    schema_version: int = 1
    enabled: bool = False
    provider: str = "noop"
    status: ResearchStatus = "skipped"
    summary: str = ""
    real_world_constraints: list[str] = Field(default_factory=list)
    terminology: list[str] = Field(default_factory=list)
    inspiration_notes: list[str] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: str = ""
    spec_fingerprint: str = ""
    research_report_fingerprint: str = ""
    config_fingerprint: str = ""
    model_prior_notes: list[str] = Field(default_factory=list)
    model_prior_terminology: list[str] = Field(default_factory=list)
    model_prior_uncertainty_notes: list[str] = Field(default_factory=list)

    def prompt_context(self) -> dict[str, object]:
        """Return prompt-facing research context with source-aware synthesis."""
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "status": self.status,
            "summary": self.summary,
            "real_world_constraints": list(self.real_world_constraints),
            "terminology": list(self.terminology),
            "inspiration_notes": list(self.inspiration_notes),
            "uncertainty_notes": list(self.uncertainty_notes),
            "source_refs": list(self.source_refs),
            "source_notes": list(self.source_refs),
            "warnings": list(self.warnings),
            "model_prior_notes": list(self.model_prior_notes),
            "model_prior_terminology": list(self.model_prior_terminology),
            "model_prior_uncertainty_notes": list(self.model_prior_uncertainty_notes),
        }


class OutlineResearchChapterNote(BaseModel):
    """Chapter-scoped research reminder derived after outline generation."""

    chapter_number: int
    reminders: list[str] = Field(default_factory=list)
    fact_risks: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


class OutlineResearchGrounding(BaseModel):
    """Research-to-outline alignment report used by chapter contracts."""

    schema_version: int = 1
    enabled: bool = False
    status: ResearchStatus = "skipped"
    summary: str = ""
    global_notes: list[str] = Field(default_factory=list)
    chapter_notes: list[OutlineResearchChapterNote] = Field(default_factory=list)
    fact_risks: list[str] = Field(default_factory=list)
    terminology: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: str = ""
    dossier_fingerprint: str = ""
    outline_fingerprint: str = ""
    config_fingerprint: str = ""

    def prompt_context(self, *, max_chapter_notes: int = 80) -> dict[str, object]:
        """Return bounded prompt-facing grounding context for chapter contracts."""
        notes = self.chapter_notes[: max(0, int(max_chapter_notes or 0))]
        return {
            "enabled": self.enabled,
            "status": self.status,
            "summary": self.summary,
            "global_notes": list(self.global_notes),
            "chapter_notes": [note.model_dump(mode="json") for note in notes],
            "fact_risks": list(self.fact_risks),
            "terminology": list(self.terminology),
            "source_refs": list(self.source_refs),
            "warnings": list(self.warnings),
        }
