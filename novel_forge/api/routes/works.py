"""Routes for creating and listing works / projects."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from novel_forge.api.deps import get_project_inspector, get_runtime_services, get_storage
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.workspace.contracts import CreateWorkRequest, CreateWorkResponse
from novel_forge.workspace.execution import create_project_workspace
from novel_forge.workspace.projects import ProjectDetail, ProjectInspector, ProjectSummary
from novel_forge.workspace.runtime import RuntimeServices

router = APIRouter()
StorageDep = Annotated[FileSystemStorage, Depends(get_storage)]
RuntimeDep = Annotated[RuntimeServices, Depends(get_runtime_services)]
InspectorDep = Annotated[ProjectInspector, Depends(get_project_inspector)]


@router.post("/", response_model=CreateWorkResponse)
async def create_work(
    req: CreateWorkRequest,
    storage: StorageDep,
    runtime: RuntimeDep,
) -> CreateWorkResponse:
    """Create a new work (project directory)."""
    project_id = create_project_workspace(storage, runtime, req)
    return CreateWorkResponse(
        project_id=project_id,
        mode=req.mode,
        message=f"Project '{project_id}' created.",
    )


@router.get("/")
async def list_works(
    inspector: InspectorDep,
) -> list[str]:
    """List existing project IDs."""
    return inspector.list_project_ids()


@router.get("/summaries", response_model=list[ProjectSummary])
async def list_work_summaries(
    inspector: InspectorDep,
) -> list[ProjectSummary]:
    """List detailed project summaries for dashboards or admin views."""
    return inspector.list_projects()


@router.get("/{project_id}", response_model=ProjectDetail)
async def get_work_detail(
    project_id: str,
    inspector: InspectorDep,
) -> ProjectDetail:
    """Return detailed project info for one work."""
    try:
        return inspector.get_project_detail(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
