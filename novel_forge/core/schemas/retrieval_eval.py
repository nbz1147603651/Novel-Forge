"""Retrieval evaluation report payload schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RetrievalEvalGoldPayload(BaseModel):
    """Gold set derived from the final ChapterPlan and ChapterOutline."""

    model_config = ConfigDict(extra="forbid")

    characters: list[str] = Field(default_factory=list)
    event_tokens: list[str] = Field(default_factory=list)
    scene_count: int = Field(default=0, ge=0)
    source_fields: list[str] = Field(default_factory=list)


class RetrievalEvalRetrievedPayload(BaseModel):
    """Retrieved context observed in the actual Plan prompt inputs."""

    model_config = ConfigDict(extra="forbid")

    character_names: list[str] = Field(default_factory=list)
    noisy_entity_names: list[str] = Field(default_factory=list)
    event_tokens: list[str] = Field(default_factory=list)
    source_counts: dict[str, int] = Field(default_factory=dict)


class RetrievalEvalSceneMetricPayload(BaseModel):
    """Scene projection against the same chapter-level retrieved context."""

    model_config = ConfigDict(extra="forbid")

    scene_id: str
    gold_characters: list[str] = Field(default_factory=list)
    gold_event_tokens: list[str] = Field(default_factory=list)
    character_recall: float | None = None
    event_token_recall: float | None = None
    missed_characters: list[str] = Field(default_factory=list)
    missed_event_tokens: list[str] = Field(default_factory=list)


class RetrievalEvalReportPayload(BaseModel):
    """Persisted Phase 0a Point-A retrieval evaluation report."""

    model_config = ConfigDict(extra="forbid")

    report_type: Literal["retrieval_eval_point_a"] = "retrieval_eval_point_a"
    chapter: int = Field(ge=1)
    point: Literal["A_planning"] = "A_planning"
    evaluator_version: str = "phase0a.v0.1"
    retrieval_scope: Literal["plan_prompt_context"] = "plan_prompt_context"
    duration_ms: float = Field(default=0.0, ge=0.0)
    gold: RetrievalEvalGoldPayload
    retrieved: RetrievalEvalRetrievedPayload
    aggregate: dict[str, float | None] = Field(default_factory=dict)
    scene_metrics: list[RetrievalEvalSceneMetricPayload] = Field(default_factory=list)
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
