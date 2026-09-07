"""Routes for memory module operations."""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from novel_forge.api.deps import get_runtime_services
from novel_forge.core.authoring_context import (
    AuthoringAuthorityError,
    check_authoring_authority,
)
from novel_forge.core.constants import TaskType
from novel_forge.core.guards import assert_not_coroutine
from novel_forge.workspace.authoring_control import authoring_operation
from novel_forge.workspace.execution_memory import require_memory_maintenance_authority
from novel_forge.workspace.runtime import RuntimeServices

router = APIRouter()
RuntimeDep = Annotated[RuntimeServices, Depends(get_runtime_services)]


@contextmanager
def _memory_model_authority(
    runtime: RuntimeServices, project_id: str, chapter: int
) -> Iterator[None]:
    try:
        with authoring_operation(
            runtime, SimpleNamespace(project_id=project_id, chapter_number=chapter), "repair"
        ):
            yield
            check_authoring_authority()
    except AuthoringAuthorityError as exc:
        raise HTTPException(409, str(exc)) from exc


def _require_memory_rewrite(runtime: RuntimeServices, project_id: str, operation: str) -> None:
    try:
        require_memory_maintenance_authority(runtime, project_id, operation)
    except AuthoringAuthorityError as exc:
        raise HTTPException(409, str(exc)) from exc


class MotifSummaryResponse(BaseModel):
    """Response for motif summary query."""

    motifs: list[dict[str, Any]]
    active_motifs: list[dict[str, Any]]
    suggestions: list[dict[str, Any]]
    repetition_warnings: list[dict[str, Any]]


class SummaryResponse(BaseModel):
    """Response for summary query."""

    chapter_summaries: dict[str, str]
    volume_summaries: dict[str, str]
    arc_summaries: dict[str, str]
    current_context_summary: str


class EpisodicSearchRequest(BaseModel):
    """Request for episodic memory search."""

    query: str = Field(..., description="Search query text")
    chapter_range: tuple[int, int] | None = Field(None, description="Chapter range (start, end)")
    top_k: int = Field(default=5, ge=1, le=20)
    min_relevance: float = Field(default=0.6, ge=0.0, le=1.0)


class EpisodicSearchResponse(BaseModel):
    """Response for episodic memory search."""

    results: list[dict[str, Any]]
    retrieval_method: Literal["semantic", "temporal", "hybrid"]
    total_candidates: int
    retrieval_time_ms: float


class CompressionRequest(BaseModel):
    """Request for context compression."""

    original_text: str = Field(..., description="Text to compress")
    target_chars: int = Field(default=2000, ge=100, le=10000)
    context_facts: list[str] = Field(default_factory=list)
    use_semantic_enrichment: bool = Field(
        default=True, description="Enable semantic enrichment from episodic memory"
    )
    current_chapter: int = Field(
        default=0, ge=0, description="Current chapter number for semantic enrichment"
    )


class CompressionResponse(BaseModel):
    """Response for compression operation."""

    compressed_text: str
    quality_score: float
    original_length: int
    compressed_length: int
    compression_ratio: float
    retained_facts: list[str]
    warnings: list[str]


class CritiqueRequest(BaseModel):
    """Request body for chapter critique."""

    chapter_number: int = Field(..., ge=1, description="Chapter number being critiqued")
    chapter_text: str = Field(..., min_length=1, description="Full chapter text to critique")


class CritiqueResponse(BaseModel):
    """Response for chapter critique."""

    overall_score: float
    has_critical_issues: bool
    requires_revision: bool
    issues: list[dict[str, Any]]
    strengths: list[str]


class MemoryStatusResponse(BaseModel):
    """Response for memory module status."""

    episodic_memory_enabled: bool
    episodic_memory_indexed_chapters: int
    summary_service_enabled: bool
    compression_service_enabled: bool
    motif_tracking_enabled: bool
    motif_count: int
    critic_agent_enabled: bool
    cached_summaries: int
    cached_motifs: int
    last_indexed_chapter: int
    vector_store_backend: str = ""
    vector_store_path: str = ""
    vector_store_dimension: int = 0
    vector_store_index_type: str = ""
    vector_store_vector_count: int = 0
    vector_store_initialization_state: str = ""
    expression_memory_enabled: bool = False
    expression_observation_count: int = 0
    embedding_mode: str = ""
    embedding_stats: dict[str, Any] = Field(default_factory=dict)


class MemoryRebuildResponse(BaseModel):
    """Response for explicit vector collection rebuild."""

    project_id: str
    status: str
    rebuilt_vectors: int
    vector_store_backend: str
    vector_store_path: str = ""
    vector_store_dimension: int = 0
    vector_store_index_type: str = ""
    saved: bool = False
    embedding_stats: dict[str, Any] = Field(default_factory=dict)


class ExpressionMemoryRebuildResponse(BaseModel):
    """Response for expression-channel memory rebuild."""

    project_id: str
    status: str
    rebuilt_chapters: int
    observations: int
    vectors: int
    refreshed_profiles: bool = False
    skipped: str = ""
    expression_observation_count: int = 0


@router.get("/status/{project_id}", response_model=MemoryStatusResponse)
async def get_memory_status(
    project_id: str,
    runtime: RuntimeDep,
) -> MemoryStatusResponse:
    """Get memory module status for a project."""
    from novel_forge.core.config import get_settings

    settings = get_settings()
    memory_context = await runtime.get_memory_context(project_id)
    assert_not_coroutine(memory_context, "get_memory_context at api/routes/memory.py")

    if memory_context:
        status = memory_context.get_status_summary()
        motif_count = (
            len(memory_context.motif_tracker.motifs) if memory_context.motif_tracker else 0
        )
        indexed_chapters = status.get("episodic_indexed_chapters", 0)
        cached_summaries = status.get("cached_summaries", 0)
        cached_motifs = status.get("cached_motifs", 0)
        last_indexed = status.get("last_indexed_chapter", 0)
        vector_store_backend = str(status.get("vector_store_backend", "") or "")
        vector_store_path = str(status.get("vector_store_path", "") or "")
        vector_store_dimension = int(status.get("vector_store_dimension", 0) or 0)
        vector_store_index_type = str(status.get("vector_store_index_type", "") or "")
        vector_store_vector_count = int(status.get("vector_store_vector_count", 0) or 0)
        vector_store_initialization_state = str(
            status.get("vector_store_initialization_state", "") or ""
        )
        expression_memory_enabled = bool(status.get("expression_memory_enabled", False))
        expression_observation_count = int(status.get("expression_observation_count", 0) or 0)
        embedding_mode = str(status.get("embedding_mode", "") or "")
        embedding_stats = dict(status.get("embedding_stats", {}) or {})
    else:
        motif_count = 0
        indexed_chapters = 0
        cached_summaries = 0
        cached_motifs = 0
        last_indexed = 0
        vector_store_backend = ""
        vector_store_path = ""
        vector_store_dimension = 0
        vector_store_index_type = ""
        vector_store_vector_count = 0
        vector_store_initialization_state = ""
        expression_memory_enabled = False
        expression_observation_count = 0
        embedding_mode = ""
        embedding_stats = {}

    return MemoryStatusResponse(
        episodic_memory_enabled=settings.memory_episodic_enabled,
        episodic_memory_indexed_chapters=indexed_chapters,
        summary_service_enabled=settings.memory_multi_granularity_summary_enabled,
        compression_service_enabled=settings.memory_adaptive_compression_enabled,
        motif_tracking_enabled=settings.memory_motif_tracking_enabled,
        motif_count=motif_count,
        critic_agent_enabled=settings.memory_critic_agent_enabled,
        cached_summaries=cached_summaries,
        cached_motifs=cached_motifs,
        last_indexed_chapter=last_indexed,
        vector_store_backend=vector_store_backend,
        vector_store_path=vector_store_path,
        vector_store_dimension=vector_store_dimension,
        vector_store_index_type=vector_store_index_type,
        vector_store_vector_count=vector_store_vector_count,
        vector_store_initialization_state=vector_store_initialization_state,
        expression_memory_enabled=expression_memory_enabled,
        expression_observation_count=expression_observation_count,
        embedding_mode=embedding_mode,
        embedding_stats=embedding_stats,
    )


@router.get("/motifs/{project_id}", response_model=MotifSummaryResponse)
async def get_motif_summary(
    project_id: str,
    current_chapter: int,
    runtime: RuntimeDep,
) -> MotifSummaryResponse:
    """Get motif summary for current chapter."""
    memory_context = await runtime.get_memory_context(project_id)
    assert_not_coroutine(memory_context, "get_memory_context at api/routes/memory.py")

    if not memory_context or not memory_context.motif_tracker:
        raise HTTPException(status_code=404, detail="Motif tracker not available for this project")

    motif_tracker = memory_context.motif_tracker
    settings = getattr(memory_context, "settings", None) or getattr(
        memory_context, "_settings", None
    )
    raw_related_lookback = getattr(settings, "memory_motif_related_lookback_chapters", None)
    related_lookback = max(
        0,
        int(2 if raw_related_lookback is None else raw_related_lookback),
    )
    repetition_lookback = max(
        0,
        int(getattr(settings, "motif_repetition_lookback_chapters", 5) or 5),
    )
    repetition_gap = max(
        1,
        int(getattr(settings, "motif_repetition_recent_gap_chapters", 2) or 2),
    )

    motifs = [
        {
            "motif_id": m.motif_id,
            "category": m.category.value if hasattr(m.category, "value") else str(m.category),
            "description": m.description,
            "occurrence_count": m.occurrence_count,
            "last_chapter": m.last_appearance_chapter,
            "category_confidence": (m.metadata or {}).get("category_confidence"),
            "category_reason": (m.metadata or {}).get("category_reason", ""),
            "secondary_categories": (m.metadata or {}).get("secondary_categories", []),
            "classification_status": (m.metadata or {}).get("category_status", ""),
            "motif_role": (m.metadata or {}).get("motif_role", "unknown"),
            "importance_score": (m.metadata or {}).get("importance_score"),
            "retired_reason": (m.metadata or {}).get("retired_reason", ""),
        }
        for m in motif_tracker.motifs.values()
    ]

    active = [
        {
            "motif_id": m.motif_id,
            "category": m.category.value if hasattr(m.category, "value") else str(m.category),
            "description": m.description,
            "motif_role": (m.metadata or {}).get("motif_role", "unknown"),
            "importance_score": (m.metadata or {}).get("importance_score"),
        }
        for m in motif_tracker.motifs.values()
        if not m.retired and m.last_appearance_chapter >= max(1, current_chapter - related_lookback)
    ]

    suggestions = motif_tracker.get_suggestions_for_chapter(
        current_chapter=current_chapter,
        current_context="",
    )
    suggestions_data = [
        {
            "motif_id": s.motif_id,
            "motif_name": s.motif_name,
            "reason": s.reason,
            "suggestion": s.reason,
            "suggested_context": s.suggested_context,
            "thematic_fit": s.thematic_fit,
            "chapters_since_last_use": s.chapters_since_last_use,
            "priority": s.priority.value if hasattr(s.priority, "value") else str(s.priority),
        }
        for s in suggestions
    ]

    warnings = await motif_tracker.check_unintentional_repetition(
        chapter_number=current_chapter,
        chapter_text="",
        lookback_chapters=repetition_lookback,
        repetition_gap_chapters=repetition_gap,
    )
    warnings_data = [
        {
            "motif_name": w.motif_name,
            "chapter_number": w.chapter_number,
            "previous_chapters": w.previous_chapters,
            "similarity_score": w.similarity_score,
            "text_snippet": w.text_snippet,
            "suggestion": w.suggestion,
            "message": w.suggestion,
            "warning_type": "repetition",
            "severity": w.severity.value if hasattr(w.severity, "value") else str(w.severity),
        }
        for w in warnings
    ]

    return MotifSummaryResponse(
        motifs=motifs,
        active_motifs=active,
        suggestions=suggestions_data,
        repetition_warnings=warnings_data,
    )


@router.get("/summaries/{project_id}", response_model=SummaryResponse)
async def get_summaries(
    project_id: str,
    current_chapter: int,
    runtime: RuntimeDep,
    lookback_volumes: int = 1,
) -> SummaryResponse:
    """Get multi-granularity summaries for a project."""
    memory_context = await runtime.get_memory_context(project_id)
    assert_not_coroutine(memory_context, "get_memory_context at api/routes/memory.py")

    if not memory_context or not memory_context.summary_service:
        raise HTTPException(
            status_code=404, detail="Summary service not available for this project"
        )

    summary_service = memory_context.summary_service

    chapter_summaries = summary_service.get_chapter_summaries(
        start_chapter=max(1, current_chapter - 10),
        end_chapter=current_chapter - 1,
    )

    volume_summaries = summary_service.get_volume_summaries()
    arc_summaries = summary_service.get_arc_summaries()

    cached_summary = memory_context.get_cached_summary(current_chapter)
    if cached_summary:
        current_context = cached_summary
    else:
        current_context = summary_service.get_summary_for_context(
            current_chapter=current_chapter,
            granularity="chapter",
            lookback_volumes=lookback_volumes,
        )

    return SummaryResponse(
        chapter_summaries=chapter_summaries,
        volume_summaries=volume_summaries,
        arc_summaries=arc_summaries,
        current_context_summary=current_context,
    )


@router.post("/search/episodic/{project_id}", response_model=EpisodicSearchResponse)
async def search_episodic_memory(
    project_id: str,
    request: EpisodicSearchRequest,
    runtime: RuntimeDep,
) -> EpisodicSearchResponse:
    """Search episodic memory with semantic query."""
    memory_context = await runtime.get_memory_context(project_id)
    assert_not_coroutine(memory_context, "get_memory_context at api/routes/memory.py")

    if not memory_context or not memory_context.episodic_memory:
        raise HTTPException(
            status_code=404, detail="Episodic memory not available for this project"
        )

    import time

    start_time = time.time()

    results = await memory_context.episodic_memory.search_by_semantic(
        query=request.query,
        chapter_range=request.chapter_range,
        top_k=request.top_k,
        min_relevance=request.min_relevance,
    )

    retrieval_time_ms = (time.time() - start_time) * 1000

    return EpisodicSearchResponse(
        results=[
            {
                "chapter_number": r.chapter_number,
                "event_summary": r.event_summary,
                "scene_index": r.scene_index,
                "relevance_score": r.relevance_score,
                "text_snippet": r.text_snippet,
                "characters_involved": r.characters_involved,
            }
            for r in results
        ],
        retrieval_method="semantic",
        total_candidates=len(results),
        retrieval_time_ms=retrieval_time_ms,
    )


@router.post("/compress/{project_id}", response_model=CompressionResponse)
async def compress_context(
    project_id: str,
    request: CompressionRequest,
    runtime: RuntimeDep,
) -> CompressionResponse:
    """Compress context with quality verification."""
    # Semantic enrichment may be disabled without dropping the author's scope.
    with _memory_model_authority(runtime, project_id, request.current_chapter):
        memory_context = await runtime.get_memory_context(project_id)
        assert_not_coroutine(memory_context, "get_memory_context at api/routes/memory.py")

        if not memory_context or not memory_context.compression_service:
            raise HTTPException(
                status_code=404, detail="Compression service not available for this project"
            )

        if memory_context.episodic_memory is not None:
            memory_context.compression_service._episodic_memory = memory_context.episodic_memory

        current_chapter = request.current_chapter if request.use_semantic_enrichment else 0

        result = await memory_context.compression_service.compress_with_quality_check(
            original_text=request.original_text,
            target_chars=request.target_chars,
            task_type=TaskType.CONTEXT_COMPRESS,
            context_facts=request.context_facts,
            current_chapter=current_chapter,
        )

    return CompressionResponse(
        compressed_text=result.compressed_text,
        quality_score=result.quality_score,
        original_length=result.original_length,
        compressed_length=result.compressed_length,
        compression_ratio=result.compression_ratio,
        retained_facts=result.retained_facts,
        warnings=result.warnings,
    )


@router.post("/critique/{project_id}", response_model=CritiqueResponse)
async def critique_chapter(
    project_id: str,
    body: CritiqueRequest,
    runtime: RuntimeDep,
) -> CritiqueResponse:
    """Run critic agent on a chapter."""
    with _memory_model_authority(runtime, project_id, body.chapter_number):
        memory_context = await runtime.get_memory_context(project_id)
        assert_not_coroutine(memory_context, "get_memory_context at api/routes/memory.py")

        if not memory_context or not memory_context.critic_agent:
            raise HTTPException(
                status_code=404, detail="Critic agent not available for this project"
            )

        report = await memory_context.critic_agent.critique_chapter(
            chapter_number=body.chapter_number,
            chapter_text=body.chapter_text,
            canon_state=None,
            chapter_outline=None,
            previous_chapter_text="",
        )

    return CritiqueResponse(
        overall_score=report.overall_score,
        has_critical_issues=report.has_critical_issues,
        requires_revision=report.requires_revision,
        issues=[
            {
                "severity": issue.severity.value
                if hasattr(issue.severity, "value")
                else str(issue.severity),
                "category": issue.issue_type.value
                if hasattr(issue.issue_type, "value")
                else str(issue.issue_type),
                "summary": issue.summary,
                "evidence": issue.evidence,
                "suggested_fix": issue.suggested_fix,
            }
            for issue in report.issues
        ],
        strengths=report.strengths,
    )


@router.get("/context/{project_id}")
async def get_memory_context_for_prompt(
    project_id: str,
    current_chapter: int,
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Get memory context formatted for prompt injection."""
    memory_context = await runtime.get_memory_context(project_id)
    assert_not_coroutine(memory_context, "get_memory_context at api/routes/memory.py")

    if not memory_context:
        raise HTTPException(status_code=404, detail="Memory context not available for this project")

    context_getter = getattr(memory_context, "aget_memory_context_for_prompt", None)
    if not callable(context_getter):
        context_getter = getattr(memory_context, "get_memory_context_for_prompt", None)
    context = context_getter(
        current_chapter=current_chapter,
        include_motifs=True,
        include_summaries=True,
        summary_granularity="chapter",
    )
    if inspect.isawaitable(context):
        context = await context

    return {
        "project_id": project_id,
        "current_chapter": current_chapter,
        "memory_context": context,
        "status": memory_context.get_status_summary(),
    }


@router.post("/reset/{project_id}")
async def reset_memory_context(
    project_id: str,
    runtime: RuntimeDep,
) -> dict[str, str]:
    """Reset memory context for a project."""
    _require_memory_rewrite(runtime, project_id, "重置全书记忆")
    memory_context = await runtime.get_memory_context(project_id)
    assert_not_coroutine(memory_context, "get_memory_context at api/routes/memory.py")

    if not memory_context:
        raise HTTPException(status_code=404, detail="Memory context not available for this project")

    memory_context.reset()

    return {
        "project_id": project_id,
        "status": "reset",
    }


@router.post("/rebuild/{project_id}", response_model=MemoryRebuildResponse)
async def rebuild_memory_vectors(
    project_id: str,
    runtime: RuntimeDep,
) -> MemoryRebuildResponse:
    """Rebuild the project's zvec vector collection explicitly."""
    _require_memory_rewrite(runtime, project_id, "重建全书记忆向量")
    memory_context = await runtime.get_memory_context(project_id)
    assert_not_coroutine(memory_context, "get_memory_context at api/routes/memory.py")

    if not memory_context:
        raise HTTPException(status_code=404, detail="Memory context not available for this project")

    try:
        result = await memory_context.rebuild_vector_collection()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    status = result.get("status", {}) if isinstance(result.get("status"), dict) else {}
    return MemoryRebuildResponse(
        project_id=project_id,
        status="rebuilt",
        rebuilt_vectors=int(result.get("rebuilt_vectors", 0) or 0),
        vector_store_backend=str(
            result.get("backend") or status.get("vector_store_backend") or ""
        ),
        vector_store_path=str(result.get("path") or status.get("vector_store_path") or ""),
        vector_store_dimension=int(
            result.get("dimension") or status.get("vector_store_dimension") or 0
        ),
        vector_store_index_type=str(
            result.get("index_type") or status.get("vector_store_index_type") or ""
        ),
        saved=bool(result.get("saved", False)),
        embedding_stats=dict(result.get("embedding_stats", {}) or {}),
    )


@router.post("/expression/rebuild/{project_id}", response_model=ExpressionMemoryRebuildResponse)
async def rebuild_expression_memory(
    project_id: str,
    runtime: RuntimeDep,
    from_chapter: int | None = None,
    to_chapter: int | None = None,
) -> ExpressionMemoryRebuildResponse:
    """Refresh profiles when needed and rebuild expression-channel semantic memory."""
    _require_memory_rewrite(runtime, project_id, "重建表达记忆与画像")
    memory_context = await runtime.get_memory_context(project_id)
    assert_not_coroutine(memory_context, "get_memory_context at api/routes/memory.py")

    if not memory_context:
        raise HTTPException(status_code=404, detail="Memory context not available for this project")

    try:
        result = await memory_context.rebuild_expression_channel_memory(
            from_chapter=from_chapter,
            to_chapter=to_chapter,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    status = result.get("status", {}) if isinstance(result.get("status"), dict) else {}
    return ExpressionMemoryRebuildResponse(
        project_id=project_id,
        status="rebuilt" if not result.get("skipped") else "skipped",
        rebuilt_chapters=int(result.get("rebuilt_chapters", 0) or 0),
        observations=int(result.get("observations", 0) or 0),
        vectors=int(result.get("vectors", 0) or 0),
        refreshed_profiles=bool(result.get("refreshed_profiles", False)),
        skipped=str(result.get("skipped", "") or ""),
        expression_observation_count=int(status.get("expression_observation_count", 0) or 0),
    )


class SceneSearchRequest(BaseModel):
    """Request for scene type search."""

    scene_type: Literal["对话", "动作", "内心独白", "场景转换", "混合"] = Field(
        ..., description="Scene type to search for"
    )
    chapter_range: tuple[int, int] | None = Field(None, description="Chapter range (start, end)")
    top_k: int = Field(default=5, ge=1, le=20)


class SceneSearchResponse(BaseModel):
    """Response for scene type search."""

    scene_type: str
    similar_chapters: list[dict[str, Any]]
    total_found: int
    scene_patterns: list[dict[str, Any]]


class CharacterArcRequest(BaseModel):
    """Request for character arc tracking."""

    character_name: str = Field(..., description="Character name to track")
    lookback_chapters: int = Field(default=10, ge=1, le=50)


class CharacterArcResponse(BaseModel):
    """Response for character arc tracking."""

    character_name: str
    arc_milestones: list[dict[str, Any]]
    emotional_trajectory: list[dict[str, Any]]
    location_history: list[dict[str, Any]]
    relationship_changes: list[dict[str, Any]]


@router.post("/search/scenes/{project_id}", response_model=SceneSearchResponse)
async def search_scene_patterns(
    project_id: str,
    request: SceneSearchRequest,
    runtime: RuntimeDep,
) -> SceneSearchResponse:
    """Search for similar scenes by type across chapters.

    This endpoint helps writers find similar scene patterns (dialogue-heavy,
    action-focused, introspective, etc.) from previous chapters to maintain
    consistency and avoid repetition.
    """
    memory_context = await runtime.get_memory_context(project_id)
    assert_not_coroutine(memory_context, "get_memory_context at api/routes/memory.py")

    if not memory_context:
        raise HTTPException(status_code=404, detail="Memory context not available for this project")

    scene_type_queries = {
        "对话": "人物对话 角色交流 言语互动 对话场景",
        "动作": "动作场面 战斗场景 运动场景 身体活动",
        "内心独白": "内心独白 心理活动 思考回忆 情感描写",
        "场景转换": "场景切换 地点变化 过渡段落 章节过渡",
        "混合": "复合场景 多元素场景 复杂场景",
    }

    query = scene_type_queries.get(request.scene_type, request.scene_type)

    if request.chapter_range:
        start_ch, end_ch = request.chapter_range
    else:
        end_ch = memory_context._last_indexed_chapter
        start_ch = max(1, end_ch - 20) if end_ch > 0 else 1

    results = await memory_context.search_relevant_history(
        query=query,
        current_chapter=end_ch + 1,
        lookback=end_ch - start_ch,
        top_k=request.top_k,
        min_relevance=0.5,
    )

    return SceneSearchResponse(
        scene_type=request.scene_type,
        similar_chapters=results,
        total_found=len(results),
        scene_patterns=[
            {
                "pattern_type": request.scene_type,
                "frequency": len([r for r in results if r.get("relevance_score", 0) > 0.6]),
                "avg_relevance": sum(r.get("relevance_score", 0) for r in results)
                / max(len(results), 1),
            }
        ],
    )


@router.get("/character/{project_id}", response_model=CharacterArcResponse)
async def track_character_arc(
    project_id: str,
    character_name: str,
    runtime: RuntimeDep,
    lookback_chapters: int = 10,
) -> CharacterArcResponse:
    """Track character development arc across chapters.

    This endpoint provides a character's historical state, emotional trajectory,
    location changes, and relationship evolution over multiple chapters.
    """
    memory_context = await runtime.get_memory_context(project_id)
    assert_not_coroutine(memory_context, "get_memory_context at api/routes/memory.py")

    if not memory_context:
        raise HTTPException(status_code=404, detail="Memory context not available for this project")

    character_history = memory_context.get_character_history(
        character_name=character_name,
        current_chapter=memory_context._last_indexed_chapter + 1,
        lookback=lookback_chapters,
    )

    arc_milestones: list[dict[str, Any]] = []
    emotional_trajectory: list[dict[str, Any]] = []
    location_history: list[dict[str, Any]] = []
    relationship_changes: list[dict[str, Any]] = []

    for state in character_history:
        arc_milestones.append(
            {
                "chapter": state.get("chapter"),
                "location": state.get("location", ""),
                "emotional_state": state.get("emotional_state", ""),
            }
        )
        emotional_trajectory.append(
            {
                "chapter": state.get("chapter"),
                "emotion": state.get("emotional_state", ""),
            }
        )
        location_history.append(
            {
                "chapter": state.get("chapter"),
                "location": state.get("location", ""),
            }
        )

    return CharacterArcResponse(
        character_name=character_name,
        arc_milestones=arc_milestones,
        emotional_trajectory=emotional_trajectory,
        location_history=location_history,
        relationship_changes=relationship_changes,
    )
