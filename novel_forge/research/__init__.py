"""Initialization-time web research helpers."""

from __future__ import annotations

from novel_forge.research.chapter_evidence import (
    ChapterResearchEvidence,
    prepare_chapter_research_evidence,
)
from novel_forge.research.contracts import (
    ModelPriorNotes,
    OutlineResearchChapterNote,
    OutlineResearchGrounding,
    ResearchBrief,
    ResearchDossier,
    ResearchQuery,
    ResearchReport,
    ResearchResult,
    ResearchSource,
)
from novel_forge.research.service import (
    ground_outline_research,
    plan_research_queries_with_llm,
    run_init_web_research,
    synthesize_init_research_dossier,
    synthesize_model_prior_notes,
)

__all__ = [
    "ChapterResearchEvidence",
    "ModelPriorNotes",
    "OutlineResearchChapterNote",
    "OutlineResearchGrounding",
    "ResearchBrief",
    "ResearchDossier",
    "ResearchQuery",
    "ResearchReport",
    "ResearchResult",
    "ResearchSource",
    "ground_outline_research",
    "plan_research_queries_with_llm",
    "prepare_chapter_research_evidence",
    "run_init_web_research",
    "synthesize_init_research_dossier",
    "synthesize_model_prior_notes",
]
