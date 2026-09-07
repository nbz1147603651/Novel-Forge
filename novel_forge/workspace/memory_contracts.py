"""Shared request/response contracts for memory module operations."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class MemoryMotifData(BaseModel):
    """Motif tracking data for UI display."""

    motif_id: str
    category: str
    description: str
    occurrence_count: int = 0
    last_chapter: int = 0
    is_active: bool = False
    chapters: list[int] = Field(default_factory=list)


class MemoryMotifSuggestion(BaseModel):
    """Motif suggestion for current chapter."""

    motif_id: str
    suggestion: str
    priority: Literal["high", "medium", "low"] = "medium"
    category: str = ""


class MemoryRepetitionWarning(BaseModel):
    """Warning for unintentional motif repetition."""

    motif_id: str
    warning_type: str
    message: str
    severity: Literal["critical", "high", "medium", "low"] = "medium"


class MemorySummaryData(BaseModel):
    """Summary data for memory context."""

    granularity: Literal["scene", "chapter", "volume", "arc"] = "chapter"
    chapter_number: int = 0
    summary_text: str = ""
    key_events: list[str] = Field(default_factory=list)
    characters_involved: list[str] = Field(default_factory=list)
    motifs_used: list[str] = Field(default_factory=list)


class MemoryEpisodicEvent(BaseModel):
    """Episodic memory event for display."""

    chapter_number: int
    event_summary: str
    scene_index: int = 0
    relevance_score: float = 1.0
    text_snippet: str = ""
    characters_involved: list[str] = Field(default_factory=list)
    timestamp_in_story: str = ""


class MemorySkillTool(BaseModel):
    """Skill tool definition for UI."""

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class MemorySkillData(BaseModel):
    """Skill data for UI."""

    name: str
    description: str
    tools: list[MemorySkillTool] = Field(default_factory=list)


class MemoryCritiqueIssue(BaseModel):
    """Critique issue for display."""

    severity: Literal["critical", "high", "medium", "low"]
    category: str
    summary: str
    evidence: str = ""
    suggested_fix: str = ""


class MemoryCritiqueResult(BaseModel):
    """Critique result for display."""

    overall_score: float = 0.0
    has_critical_issues: bool = False
    requires_revision: bool = False
    issues: list[MemoryCritiqueIssue] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)


class MemoryPanelSnapshot(BaseModel):
    """Aggregated memory state for the chapter studio memory panel."""

    project_id: str
    current_chapter: int

    episodic_enabled: bool = False
    indexed_chapters: int = 0

    motifs: list[MemoryMotifData] = Field(default_factory=list)
    active_motifs: list[MemoryMotifData] = Field(default_factory=list)
    motif_suggestions: list[MemoryMotifSuggestion] = Field(default_factory=list)
    repetition_warnings: list[MemoryRepetitionWarning] = Field(default_factory=list)

    chapter_summaries: dict[str, str] = Field(default_factory=dict)
    volume_summaries: dict[str, str] = Field(default_factory=dict)
    current_context_summary: str = ""

    compression_stats: dict[str, Any] = Field(default_factory=dict)

    skills: dict[str, list[str]] = Field(default_factory=dict)

    recent_episodes: list[MemoryEpisodicEvent] = Field(default_factory=list)

    last_critique: MemoryCritiqueResult | None = None


class MemorySearchRequest(BaseModel):
    """Request for episodic memory search."""

    query: str
    chapter_range_start: int | None = None
    chapter_range_end: int | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    min_relevance: float = Field(default=0.6, ge=0.0, le=1.0)


class MemorySearchResponse(BaseModel):
    """Response for episodic memory search."""

    results: list[MemoryEpisodicEvent]
    retrieval_method: Literal["semantic", "temporal", "hybrid"] = "semantic"
    retrieval_time_ms: float = 0.0


class MemorySkillExecutionRequest(BaseModel):
    """Request for skill tool execution."""

    skill_name: str
    tool_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class MemorySkillExecutionResponse(BaseModel):
    """Response for skill tool execution."""

    success: bool
    data: Any = None
    error: str = ""


class MemoryCompressionRequest(BaseModel):
    """Request for context compression."""

    original_text: str
    target_chars: int = Field(default=2000, ge=100, le=10000)
    context_facts: list[str] = Field(default_factory=list)


class MemoryCompressionResponse(BaseModel):
    """Response for context compression."""

    compressed_text: str
    quality_score: float
    compression_ratio: float
    retained_facts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
