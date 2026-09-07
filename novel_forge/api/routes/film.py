"""映界 film-production API routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel, Field

from novel_forge.api.deps import get_runtime_services
from novel_forge.comic.pipeline import ComicPipeline
from novel_forge.comic.schemas import ComicFormat, ComicProjectState
from novel_forge.film.drama.pipeline import DramaPipeline
from novel_forge.film.drama.store import DramaProjectState
from novel_forge.film.node_catalog import film_node_catalog
from novel_forge.film.pipeline import FilmProductionPipeline
from novel_forge.film.prompting import optimize_node_prompt
from novel_forge.film.providers.base import FilmProviderError
from novel_forge.film.providers.catalog import film_provider_catalog
from novel_forge.film.reference_capabilities import render_hollywood_screenplay
from novel_forge.film.rendering import FilmRenderError
from novel_forge.film.schemas import (
    ComplianceReport,
    DeliveryManifest,
    FilmJob,
    FilmStage,
    FilmStudioState,
    ProductionMode,
)
from novel_forge.film.workflow_models import (
    FilmGraphDefinition,
    FilmGraphNode,
    FilmGraphRun,
    FilmGraphRunScope,
    FilmGraphValidationIssue,
    FilmGraphView,
    FilmNodeDefinition,
    FilmPromptOptimizationResult,
    FilmRunEstimate,
)
from novel_forge.film.workflow_repository import FilmWorkflowRevisionConflict
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.runtime import RuntimeServices

router = APIRouter()
RuntimeDep = Annotated[RuntimeServices, Depends(get_runtime_services)]


class FilmBootstrapRequest(BaseModel):
    mode: ProductionMode = ProductionMode.COLLABORATIVE
    refresh_sources: bool = False


class FilmAdvanceRequest(BaseModel):
    mode: ProductionMode | None = None
    use_ai: bool = True
    run_until: FilmStage | None = None


class FilmAssetGenerateRequest(BaseModel):
    provider_id: str = ""
    model_id: str = ""
    image_count: int = Field(default=4, ge=1, le=12)


class FilmCandidateSelectRequest(BaseModel):
    url: str = Field(min_length=1)
    lock: bool = True


class FilmShotGenerateRequest(BaseModel):
    provider_id: str = ""
    model_id: str = ""


class FilmShotPatchRequest(BaseModel):
    patch: dict[str, Any]


class FilmBatchGenerateRequest(BaseModel):
    shot_ids: list[str] = Field(default_factory=list)


class FilmGraphSaveRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    graph: FilmGraphDefinition


class FilmGraphValidateRequest(BaseModel):
    graph: FilmGraphDefinition


class FilmPromptOptimizeRequest(BaseModel):
    node: FilmGraphNode


class FilmRunEstimateRequest(BaseModel):
    scope: FilmGraphRunScope = FilmGraphRunScope.ALL
    target_node_ids: list[str] = Field(default_factory=list)


class FilmGraphRunRequest(FilmRunEstimateRequest):
    confirmed_cost: bool = False
    high_priority: bool = False


class FilmLineageDecisionRequest(BaseModel):
    subject_id: str = Field(min_length=1)
    action: str = Field(pattern="^(preserve_old_version|regenerate|rebind)$")


class MiniMaxFilmCallbackRequest(BaseModel):
    model_config = {"extra": "allow"}

    challenge: str = ""
    task_id: str = ""
    status: str = ""
    task: dict[str, Any] | None = None


class FilmRenderMasterRequest(BaseModel):
    burn_subtitles: bool = True
    width: int | None = Field(default=None, ge=320, le=7680)
    height: int | None = Field(default=None, ge=320, le=7680)
    frame_rate: float | None = Field(default=None, gt=0, le=120)


class DramaPlanRequest(BaseModel):
    title: str = Field(min_length=1)
    total_episodes: int = Field(ge=1, le=200)
    genre: str = ""
    logline: str = ""
    episode_duration_s: int = Field(default=120, ge=30, le=900)
    character_roster: list[str] = Field(default_factory=list)
    language: str = "zh"


class DramaOutlineRequest(BaseModel):
    start: int = Field(ge=1)
    end: int = Field(ge=1)


class DramaScreenplayRequest(BaseModel):
    episode_number: int = Field(ge=1)


def _pipeline(runtime: RuntimeServices, project_id: str) -> FilmProductionPipeline:
    try:
        project_root = runtime.storage.existing_project_dir(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc
    layout = ProjectLayout(project_root)
    layout.ensure_dirs()
    return FilmProductionPipeline(
        project_id=project_id,
        layout=layout,
        settings=runtime.settings,
        router=runtime.router,
    )


def _drama_pipeline(runtime: RuntimeServices, project_id: str) -> DramaPipeline:
    try:
        project_root = runtime.storage.existing_project_dir(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc
    layout = ProjectLayout(project_root)
    layout.ensure_dirs()
    return DramaPipeline(
        project_id=project_id,
        layout=layout,
        settings=runtime.settings,
        router=runtime.router,
    )


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=422 if isinstance(exc, FilmProviderError) else 400,
        detail={
            "message": str(exc),
            "retryable": bool(getattr(exc, "retryable", False)),
        },
    )


@router.get("/catalog", response_model=dict)
async def get_film_provider_catalog() -> dict[str, dict[str, Any]]:
    return film_provider_catalog()


@router.post("/providers/minimax/callback", response_model=dict)
async def receive_minimax_film_callback(
    request: MiniMaxFilmCallbackRequest,
    runtime: RuntimeDep,
    project_id: Annotated[str | None, Query(min_length=1)] = None,
) -> dict[str, object]:
    """Complete MiniMax's challenge handshake and accept H3 task updates."""

    if request.challenge:
        return {"challenge": request.challenge}
    if project_id is None:
        raise HTTPException(status_code=400, detail="project_id is required for task callbacks")
    payload = request.model_dump(mode="json", exclude_none=True)
    applied = _pipeline(runtime, project_id).apply_minimax_callback(payload)
    return {"received": True, "applied": applied}


@router.get("/projects/{project_id}", response_model=FilmStudioState)
async def get_film_studio(
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> FilmStudioState:
    try:
        return _pipeline(runtime, project_id).get_or_bootstrap()
    except (ValueError, OSError) as exc:
        raise _bad_request(exc) from exc


@router.post("/projects/{project_id}/bootstrap", response_model=FilmStudioState)
async def bootstrap_film_studio(
    project_id: Annotated[str, Path(min_length=1)],
    req: FilmBootstrapRequest,
    runtime: RuntimeDep,
) -> FilmStudioState:
    try:
        return _pipeline(runtime, project_id).get_or_bootstrap(
            mode=req.mode,
            refresh_sources=req.refresh_sources,
        )
    except (ValueError, OSError) as exc:
        raise _bad_request(exc) from exc


@router.post("/projects/{project_id}/advance", response_model=FilmStudioState)
async def advance_film_studio(
    project_id: Annotated[str, Path(min_length=1)],
    req: FilmAdvanceRequest,
    runtime: RuntimeDep,
) -> FilmStudioState:
    try:
        return await _pipeline(runtime, project_id).advance(
            mode=req.mode,
            use_ai=req.use_ai,
            run_until=req.run_until,
        )
    except (ValueError, RuntimeError) as exc:
        raise _bad_request(exc) from exc


@router.post(
    "/projects/{project_id}/assets/{asset_id}/generate",
    response_model=FilmStudioState,
)
async def generate_film_asset(
    project_id: Annotated[str, Path(min_length=1)],
    asset_id: Annotated[str, Path(min_length=1)],
    req: FilmAssetGenerateRequest,
    runtime: RuntimeDep,
) -> FilmStudioState:
    try:
        return await _pipeline(runtime, project_id).generate_visual_asset(
            asset_id,
            provider_id=req.provider_id,
            model_id=req.model_id,
            image_count=req.image_count,
        )
    except (ValueError, RuntimeError, FilmProviderError) as exc:
        raise _bad_request(exc) from exc


@router.post(
    "/projects/{project_id}/assets/{asset_id}/select",
    response_model=FilmStudioState,
)
async def select_film_asset(
    project_id: Annotated[str, Path(min_length=1)],
    asset_id: Annotated[str, Path(min_length=1)],
    req: FilmCandidateSelectRequest,
    runtime: RuntimeDep,
) -> FilmStudioState:
    try:
        return _pipeline(runtime, project_id).select_asset_candidate(
            asset_id,
            req.url,
            lock=req.lock,
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.patch("/projects/{project_id}/shots/{shot_id}", response_model=FilmStudioState)
async def patch_film_shot(
    project_id: Annotated[str, Path(min_length=1)],
    shot_id: Annotated[str, Path(min_length=1)],
    req: FilmShotPatchRequest,
    runtime: RuntimeDep,
) -> FilmStudioState:
    try:
        return _pipeline(runtime, project_id).update_shot(shot_id, req.patch)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.post(
    "/projects/{project_id}/shots/{shot_id}/generate",
    response_model=FilmStudioState,
)
async def generate_film_shot(
    project_id: Annotated[str, Path(min_length=1)],
    shot_id: Annotated[str, Path(min_length=1)],
    req: FilmShotGenerateRequest,
    runtime: RuntimeDep,
) -> FilmStudioState:
    try:
        return await _pipeline(runtime, project_id).generate_shot(
            shot_id,
            provider_id=req.provider_id,
            model_id=req.model_id,
        )
    except (ValueError, RuntimeError, FilmProviderError) as exc:
        raise _bad_request(exc) from exc


@router.post(
    "/projects/{project_id}/media/{target_id}/query",
    response_model=FilmStudioState,
)
async def query_film_media_task(
    project_id: Annotated[str, Path(min_length=1)],
    target_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> FilmStudioState:
    try:
        return await _pipeline(runtime, project_id).query_media_task(target_id)
    except (ValueError, RuntimeError, FilmProviderError) as exc:
        raise _bad_request(exc) from exc


@router.post(
    "/projects/{project_id}/media/{target_id}/materialize",
    response_model=FilmStudioState,
)
async def materialize_film_media(
    project_id: Annotated[str, Path(min_length=1)],
    target_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> FilmStudioState:
    try:
        return await _pipeline(runtime, project_id).materialize_media(target_id)
    except (ValueError, OSError) as exc:
        raise _bad_request(exc) from exc


@router.post(
    "/projects/{project_id}/shots/{shot_id}/qc",
    response_model=FilmStudioState,
)
async def qc_film_shot(
    project_id: Annotated[str, Path(min_length=1)],
    shot_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> FilmStudioState:
    try:
        return await _pipeline(runtime, project_id).run_shot_qc(shot_id)
    except (ValueError, OSError) as exc:
        raise _bad_request(exc) from exc


@router.post(
    "/projects/{project_id}/shots/batch-generate",
    response_model=FilmStudioState,
)
async def batch_generate_film_shots(
    project_id: Annotated[str, Path(min_length=1)],
    req: FilmBatchGenerateRequest,
    runtime: RuntimeDep,
) -> FilmStudioState:
    try:
        return await _pipeline(runtime, project_id).generate_shots_batch(req.shot_ids or None)
    except (ValueError, RuntimeError, FilmProviderError) as exc:
        raise _bad_request(exc) from exc


@router.post("/projects/{project_id}/render-master", response_model=FilmStudioState)
async def render_film_master(
    project_id: Annotated[str, Path(min_length=1)],
    req: FilmRenderMasterRequest,
    runtime: RuntimeDep,
) -> FilmStudioState:
    try:
        return await _pipeline(runtime, project_id).render_master(
            burn_subtitles=req.burn_subtitles,
            width=req.width,
            height=req.height,
            frame_rate=req.frame_rate,
        )
    except (ValueError, RuntimeError, FilmRenderError, OSError) as exc:
        raise _bad_request(exc) from exc


@router.get("/projects/{project_id}/delivery", response_model=DeliveryManifest)
async def get_film_delivery(
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> DeliveryManifest:
    state = _pipeline(runtime, project_id).get_or_bootstrap()
    if state.delivery is None:
        raise HTTPException(status_code=404, detail="Delivery manifest not generated yet")
    blockers = list(
        dict.fromkeys([*state.delivery_blocking_reasons, *state.delivery.blocking_reasons])
    )
    if blockers:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Film delivery is stale or has unresolved upstream decisions.",
                "blocking_reasons": blockers,
                "actions": ["preserve_old_version", "regenerate", "rebind"],
            },
        )
    return state.delivery


@router.post(
    "/projects/{project_id}/lineage/resolve",
    response_model=FilmStudioState,
)
async def resolve_film_lineage_decision(
    request: FilmLineageDecisionRequest,
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> FilmStudioState:
    try:
        return _pipeline(runtime, project_id).resolve_lineage_decision(
            request.subject_id,
            request.action,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/projects/{project_id}/compliance", response_model=ComplianceReport)
async def get_film_compliance(
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> ComplianceReport:
    state = _pipeline(runtime, project_id).get_or_bootstrap()
    if state.compliance_report is None:
        raise HTTPException(status_code=404, detail="Compliance audit not run yet")
    return state.compliance_report


@router.get("/projects/{project_id}/hollywood-screenplay")
async def get_hollywood_screenplay(
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> Response:
    """G7: Hollywood-format screenplay export (INT./EXT. + SHOT annotations)."""
    state = _pipeline(runtime, project_id).get_or_bootstrap()
    text = render_hollywood_screenplay(state.screenplay, state.shots)
    return Response(content=text, media_type="text/plain; charset=utf-8")


@router.get("/projects/{project_id}/jobs", response_model=list[FilmJob])
async def list_film_jobs(
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> list[FilmJob]:
    return _pipeline(runtime, project_id).list_jobs()


@router.post("/projects/{project_id}/jobs/{job_id}/cancel", response_model=FilmStudioState)
async def cancel_film_job(
    project_id: Annotated[str, Path(min_length=1)],
    job_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> FilmStudioState:
    try:
        return await _pipeline(runtime, project_id).cancel_job(job_id)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.post("/projects/{project_id}/export-otio", response_model=dict)
async def export_film_timeline(
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> dict[str, str]:
    pipeline = _pipeline(runtime, project_id)
    state = pipeline.get_or_bootstrap()
    path = pipeline.store.export_otio(state)
    return {"path": str(path), "format": "OpenTimelineIO"}


@router.post(
    "/projects/{project_id}/shots/{shot_id}/poll",
    response_model=FilmStudioState,
)
async def poll_film_shot_task(
    project_id: Annotated[str, Path(min_length=1)],
    shot_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> FilmStudioState:
    """Auto-poll one shot's provider task until it finishes.

    Queued/processing phases and elapsed time stream through the pipeline
    ``film_task_progress`` events; a cancelled job stops the loop early.
    """
    try:
        return await _pipeline(runtime, project_id).poll_shot_task(shot_id)
    except (ValueError, RuntimeError, FilmProviderError) as exc:
        raise _bad_request(exc) from exc


@router.get("/projects/{project_id}/workflow", response_model=dict)
async def get_film_workflow(
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> dict[str, object]:
    """Executable node-graph snapshot: topological order, ready nodes and
    the overall completion ratio (ComfyUI-style execution view)."""
    pipeline = _pipeline(runtime, project_id)
    state = pipeline.get_or_bootstrap()
    return pipeline.workflow_view(state)


@router.post(
    "/projects/{project_id}/workflow/nodes/{node_id}/rerun",
    response_model=FilmStudioState,
)
async def rerun_film_workflow_node(
    project_id: Annotated[str, Path(min_length=1)],
    node_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> FilmStudioState:
    """Revert one node and its transitive dependents to pending so only the
    affected subgraph re-executes (the frontend '只重跑失败节点' operation)."""
    try:
        return _pipeline(runtime, project_id).rerun_node(node_id)
    except ValueError as exc:
        raise _bad_request(exc) from exc


# ---------------------------------------------------------------- workflow v2


@router.get("/node-catalog", response_model=list[FilmNodeDefinition])
async def get_film_node_catalog() -> list[FilmNodeDefinition]:
    """Return the only node types allowed on the free-form film canvas."""

    return film_node_catalog()


@router.get("/projects/{project_id}/graph", response_model=FilmGraphView)
async def get_film_graph(
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> FilmGraphView:
    return _pipeline(runtime, project_id).graph_view()


@router.put("/projects/{project_id}/graph", response_model=FilmGraphView)
async def save_film_graph(
    request: FilmGraphSaveRequest,
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> FilmGraphView:
    try:
        return _pipeline(runtime, project_id).save_graph(
            request.graph,
            expected_revision=request.expected_revision,
        )
    except FilmWorkflowRevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "expected_revision": exc.expected,
                "actual_revision": exc.actual,
            },
        ) from exc


@router.post("/projects/{project_id}/graph/validate", response_model=list[FilmGraphValidationIssue])
async def validate_film_graph(
    request: FilmGraphValidateRequest,
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> list[FilmGraphValidationIssue]:
    view = _pipeline(runtime, project_id).validate_graph(request.graph)
    return view.validation_issues


@router.post(
    "/projects/{project_id}/graph/prompts/optimize",
    response_model=FilmPromptOptimizationResult,
)
async def optimize_film_graph_prompt(
    request: FilmPromptOptimizeRequest,
    project_id: Annotated[str, Path(min_length=1)],
) -> FilmPromptOptimizationResult:
    """Normalize a node prompt against the authoritative backend template.

    This operation never calls a paid model.  H3 Context IR remains the
    explicit provider-backed creative optimization checkpoint in the graph.
    """

    del project_id
    return optimize_node_prompt(request.node)


@router.post("/projects/{project_id}/runs/estimate", response_model=FilmRunEstimate)
async def estimate_film_graph_run(
    request: FilmRunEstimateRequest,
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> FilmRunEstimate:
    return _pipeline(runtime, project_id).estimate_graph_run(
        scope=request.scope,
        target_node_ids=request.target_node_ids,
    )


@router.post("/projects/{project_id}/runs", response_model=FilmGraphRun)
async def create_film_graph_run(
    request: FilmGraphRunRequest,
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> FilmGraphRun:
    return _pipeline(runtime, project_id).create_graph_run(
        scope=request.scope,
        target_node_ids=request.target_node_ids,
        confirmed_cost=request.confirmed_cost,
        high_priority=request.high_priority,
    )


@router.get("/projects/{project_id}/runs", response_model=list[FilmGraphRun])
async def list_film_graph_runs(
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[FilmGraphRun]:
    return _pipeline(runtime, project_id).workflow_repository.list_runs(limit=limit)


@router.get("/projects/{project_id}/runs/{run_id}", response_model=FilmGraphRun)
async def get_film_graph_run(
    project_id: Annotated[str, Path(min_length=1)],
    run_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> FilmGraphRun:
    run = _pipeline(runtime, project_id).workflow_repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return run


@router.post("/projects/{project_id}/runs/{run_id}/cancel", response_model=FilmGraphRun)
async def cancel_film_graph_run(
    project_id: Annotated[str, Path(min_length=1)],
    run_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> FilmGraphRun:
    try:
        return _pipeline(runtime, project_id).cancel_graph_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/projects/{project_id}/runs/{run_id}/events",
    response_model=list[dict[str, object]],
)
async def get_film_graph_events(
    project_id: Annotated[str, Path(min_length=1)],
    run_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
    after_sequence: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, object]]:
    events = _pipeline(runtime, project_id).workflow_repository.list_events(
        run_id,
        after_sequence=after_sequence,
    )
    return [event.model_dump(mode="json") for event in events]


# ---------------------------------------------------------------- short drama


@router.get("/projects/{project_id}/drama", response_model=DramaProjectState)
async def get_drama_studio(
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> DramaProjectState:
    return _drama_pipeline(runtime, project_id).get_or_bootstrap()


@router.post("/projects/{project_id}/drama/plan", response_model=DramaProjectState)
async def plan_drama_series(
    request: DramaPlanRequest,
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> DramaProjectState:
    try:
        return await _drama_pipeline(runtime, project_id).plan_series(
            title=request.title,
            total_episodes=request.total_episodes,
            genre=request.genre,
            logline=request.logline,
            episode_duration_s=request.episode_duration_s,
            character_roster=tuple(request.character_roster),
            language=request.language,
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.post("/projects/{project_id}/drama/outlines", response_model=DramaProjectState)
async def expand_drama_outlines(
    request: DramaOutlineRequest,
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> DramaProjectState:
    try:
        return await _drama_pipeline(runtime, project_id).expand_outlines(
            start=request.start, end=request.end
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.post("/projects/{project_id}/drama/screenplay", response_model=DramaProjectState)
async def write_drama_screenplay(
    request: DramaScreenplayRequest,
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> DramaProjectState:
    try:
        return await _drama_pipeline(runtime, project_id).write_episode_screenplay(
            episode_number=request.episode_number
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.get("/projects/{project_id}/drama/audit", response_model=dict)
async def audit_drama_package(
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> dict[str, object]:
    pipeline = _drama_pipeline(runtime, project_id)
    state = pipeline.get_or_bootstrap()
    issues = pipeline.audit_production_package(state)
    return {"gate_passed": not issues, "issues": issues}


@router.post("/projects/{project_id}/drama/export", response_model=dict)
async def export_drama_package(
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> dict[str, object]:
    pipeline = _drama_pipeline(runtime, project_id)
    state = pipeline.get_or_bootstrap()
    return pipeline.export_production_package(state)


# ---------------------------------------------------------------- comic (P3)


class ComicPlanRequest(BaseModel):
    format: ComicFormat = ComicFormat.PAGE


def _comic_pipeline(runtime: RuntimeServices, project_id: str) -> ComicPipeline:
    try:
        project_root = runtime.storage.existing_project_dir(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc
    layout = ProjectLayout(project_root)
    layout.ensure_dirs()
    return ComicPipeline(
        project_id=project_id,
        layout=layout,
        router=runtime.router,
    )


@router.get("/projects/{project_id}/comic", response_model=ComicProjectState)
async def get_comic_studio(
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> ComicProjectState:
    return _comic_pipeline(runtime, project_id).get_or_bootstrap()


@router.post("/projects/{project_id}/comic/plan", response_model=ComicProjectState)
async def plan_comic_pages(
    request: ComicPlanRequest,
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> ComicProjectState:
    try:
        return await _comic_pipeline(runtime, project_id).plan_pages(fmt=request.format)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/projects/{project_id}/comic/audit", response_model=dict)
async def audit_comic_package(
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> dict[str, object]:
    pipeline = _comic_pipeline(runtime, project_id)
    state = pipeline.get_or_bootstrap()
    issues = pipeline.audit(state)
    return {"gate_passed": not issues, "issues": issues}


@router.post("/projects/{project_id}/comic/export", response_model=dict)
async def export_comic_package(
    project_id: Annotated[str, Path(min_length=1)],
    runtime: RuntimeDep,
) -> dict[str, object]:
    pipeline = _comic_pipeline(runtime, project_id)
    state = pipeline.get_or_bootstrap()
    return pipeline.export_package(state)
